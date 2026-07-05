# tatara-managed 3-label issue lifecycle - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The operator manages exactly one of `tatara-idea` / `tatara-approved` / `tatara-rejected` on every issue in a managed repo, driven by the triage agent reading the conversation; the label-toggle approval subsystem is retired.

**Architecture:** The three labels are a projection of the existing `issue_outcome` -> lifecycle-state transition. A new egress helper sets the labels; `finishTriage` applies them per outcome and enforces a bot-authored self-approve guard; brainstorm opens ideas with `tatara-idea` and completes; the webhook `flipApproval`, `approvalBackstop`, and the `ApprovalRequired` gate are removed. Conversation re-evaluation already exists via `handleIssueComment` resetting a task to Triage.

**Tech Stack:** Go 1.x, controller-runtime / kubebuilder, envtest, testify, Prometheus client. SCM via `internal/scm` (GitHub REST v3 / GitLab REST).

**Spec:** `docs/superpowers/specs/2026-06-13-tatara-label-lifecycle-design.md`

**Worktree:** Develop in a worktree off `tatara-operator` `main` (per repo CLAUDE.md branch flow). Always `git checkout main && git pull` first - bots push to this repo.

**Verification per task:** `go build ./... && go vet ./...`, the task's `go test`, and at the end `make manifests && make test` (envtest) + `golangci-lint run` + `gofmt -l`.

---

## Reference: confirmed current signatures (do not re-derive)

```go
// internal/scm/scm.go
type IssueRef struct { Repo string; Number int; Title string; Labels []string; UpdatedAt time.Time; IsPR bool }
type IssueComment struct { Author string; Body string; CreatedAt time.Time }
type IssueContent struct { Title string; Body string }
// SCMWriter:
AddLabel(ctx context.Context, token, issueRef, label string) error
RemoveLabel(ctx context.Context, token, issueRef, label string) error
CloseIssue(ctx context.Context, token, repo string, number int, comment string) error
Comment(ctx context.Context, token, issueRef, body string) error
// SCMReader:
ListOpenIssues(ctx context.Context, owner, repo string) ([]IssueRef, error)
ListIssueComments(ctx context.Context, owner, repo string, number int) ([]IssueComment, error)
GetIssue(ctx context.Context, owner, repo string, number int) (IssueContent, error)
func OwnerRepo(repoURL string) (owner, repo string, err error)

// internal/controller (TaskReconciler methods)
func (r *TaskReconciler) scmContext(ctx, task) (proj Project, repo Repository, writer scm.SCMWriter, token, provider string, err error)
func (r *TaskReconciler) scmToken(ctx, namespace, secretRef string) (string, error)
func (r *TaskReconciler) recordSCM(provider, verb string, err error) // verb is free-form metric label
func (r *TaskReconciler) setLifecycleState(ctx, task, state, reason string) error
func (r *TaskReconciler) resetAgentRun(ctx, task) error
func (r *TaskReconciler) repoURLForTask(ctx, task) string
// TaskReconciler fields: Client, Scheme, Metrics *obs.OperatorMetrics, SCMFor func(string)(scm.SCMWriter,error), ReaderFor func(provider,token string)(scm.SCMReader,error)

// helpers in package controller
func hasLabel(labels []string, want string) bool   // KEEP
func repoSlug(repo *Repository) string

// api/v1alpha1
ScmSpec{ Provider, Owner, BotLogin string; ApprovalLabel string; PriorityLabel string; ConversationIdleMinutes int; ... }
TaskSource{ Provider, IssueRef, URL, AuthorLogin string; IsPR bool; Number int }
IssueOutcome{ Action string; Comment string }   // Action in {implement, close, discuss}
```

---

## Task 1: Add the three label fields to ScmSpec

**Files:**
- Modify: `api/v1alpha1/project_types.go:127` (ScmSpec)
- Modify (generated): `config/crd/bases/*.yaml`, `charts/<chart>/crds/*` or `charts/<chart>/templates/crds*` via `make manifests`
- Test: `api/v1alpha1/types_test.go`

- [ ] **Step 1: Write the failing test**

Append to `api/v1alpha1/types_test.go`:

```go
func TestScmSpecLabelFields(t *testing.T) {
	s := ScmSpec{IdeaLabel: "tatara-idea", ApprovedLabel: "tatara-approved", RejectedLabel: "tatara-rejected"}
	if s.IdeaLabel != "tatara-idea" || s.ApprovedLabel != "tatara-approved" || s.RejectedLabel != "tatara-rejected" {
		t.Fatalf("label fields not set: %+v", s)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./api/v1alpha1/ -run TestScmSpecLabelFields`
Expected: FAIL - `unknown field IdeaLabel in struct literal`.

- [ ] **Step 3: Add the fields**

In `api/v1alpha1/project_types.go`, change the `ApprovalLabel` block (line 125-127) and add the new fields immediately after it:

```go
	// ApprovalLabel is DEPRECATED and no longer used: approval is now driven by
	// the conversation (the triage agent reads the thread) and projected onto the
	// idea/approved/rejected labels below. Kept only for migration tooling.
	// +kubebuilder:default="tatara/awaiting-approval"
	// +optional
	ApprovalLabel string `json:"approvalLabel,omitempty"`
	// IdeaLabel marks an issue tatara originated or updated but that is not yet
	// ready for implementation.
	// +kubebuilder:default="tatara-idea"
	// +optional
	IdeaLabel string `json:"ideaLabel,omitempty"`
	// ApprovedLabel marks an issue approved for implementation.
	// +kubebuilder:default="tatara-approved"
	// +optional
	ApprovedLabel string `json:"approvedLabel,omitempty"`
	// RejectedLabel marks an issue tatara closed (redundant, duplicate, not actionable).
	// +kubebuilder:default="tatara-rejected"
	// +optional
	RejectedLabel string `json:"rejectedLabel,omitempty"`
```

- [ ] **Step 4: Run test + regenerate manifests**

Run: `go test ./api/v1alpha1/ -run TestScmSpecLabelFields` -> PASS
Run: `make manifests` (regenerates CRDs incl. the bundled chart CRD)
Run: `go build ./...` -> no errors

- [ ] **Step 5: Commit**

```bash
git add api/v1alpha1/project_types.go api/v1alpha1/types_test.go config/ charts/
git commit -m "feat(operator): add idea/approved/rejected label fields to ScmSpec; deprecate approvalLabel"
```

---

## Task 2: Label egress helper (lifecycleLabels + setLifecycleLabel)

**Files:**
- Create: `internal/controller/labels.go`
- Test: `internal/controller/labels_test.go`

- [ ] **Step 1: Write the failing test**

Create `internal/controller/labels_test.go`. Use a programmable fake writer that records Add/Remove calls and a fake reader that returns current labels. Reuse the envtest `k8sClient`/`testNS` harness (as `proposal_dedup_test.go` does).

```go
package controller

import (
	"context"
	"sync"
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/stretchr/testify/require"
	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/obs"
	"github.com/szymonrychu/tatara-operator/internal/scm"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

type labelWriter struct {
	scm.SCMWriter
	mu      sync.Mutex
	added   []string
	removed []string
}

func (w *labelWriter) AddLabel(_ context.Context, _, _, label string) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.added = append(w.added, label)
	return nil
}
func (w *labelWriter) RemoveLabel(_ context.Context, _, _, label string) error {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.removed = append(w.removed, label)
	return nil
}

type labelReader struct {
	fakeProposalReader
	current []string
}

func (r *labelReader) ListOpenIssues(_ context.Context, _, _ string) ([]scm.IssueRef, error) {
	return []scm.IssueRef{{Repo: "o/r", Number: 7, Labels: r.current}}, nil
}

// seedLabelTask creates a project (with scm spec), repo, secret, and a lifecycle
// Task whose Source points at o/r#7.
func seedLabelTask(t *testing.T, suffix string, currentLabels []string) (*TaskReconciler, *tatarav1alpha1.Task, *labelWriter) {
	t.Helper()
	ctx := context.Background()
	sec := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: "lbl-scm-" + suffix, Namespace: testNS}, Data: map[string][]byte{"token": []byte("tok")}}
	require.NoError(t, k8sClient.Create(ctx, sec))
	proj := &tatarav1alpha1.Project{
		ObjectMeta: metav1.ObjectMeta{Name: "lbl-proj-" + suffix, Namespace: testNS},
		Spec: tatarav1alpha1.ProjectSpec{ScmSecretRef: "lbl-scm-" + suffix, Scm: &tatarav1alpha1.ScmSpec{Provider: "github", Owner: "o", BotLogin: "tatara-bot"}},
	}
	require.NoError(t, k8sClient.Create(ctx, proj))
	repo := &tatarav1alpha1.Repository{
		ObjectMeta: metav1.ObjectMeta{Name: "lbl-repo-" + suffix, Namespace: testNS},
		Spec:       tatarav1alpha1.RepositorySpec{ProjectRef: proj.Name, URL: "https://github.com/o/r.git", DefaultBranch: "main", ReingestSchedule: "0 6 * * *"},
	}
	require.NoError(t, k8sClient.Create(ctx, repo))
	task := &tatarav1alpha1.Task{
		ObjectMeta: metav1.ObjectMeta{Name: "lbl-task-" + suffix, Namespace: testNS},
		Spec: tatarav1alpha1.TaskSpec{ProjectRef: proj.Name, RepositoryRef: repo.Name, Kind: "issueLifecycle",
			Source: &tatarav1alpha1.TaskSource{Provider: "github", IssueRef: "o/r#7", Number: 7, AuthorLogin: "human"}},
	}
	require.NoError(t, k8sClient.Create(ctx, task))
	var fresh tatarav1alpha1.Task
	require.NoError(t, k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: task.Name}, &fresh))
	w := &labelWriter{}
	rdr := &labelReader{current: currentLabels}
	r := &TaskReconciler{Client: k8sClient, Scheme: k8sClient.Scheme(), Metrics: obs.NewOperatorMetrics(prometheus.NewRegistry()),
		SCMFor:    func(string) (scm.SCMWriter, error) { return w, nil },
		ReaderFor: func(_, _ string) (scm.SCMReader, error) { return rdr, nil }}
	return r, &fresh, w
}

func TestSetLifecycleLabel_AddsDesiredRemovesOthers(t *testing.T) {
	r, task, w := seedLabelTask(t, "addrm", []string{"tatara-idea", "unrelated"})
	var proj tatarav1alpha1.Project
	require.NoError(t, k8sClient.Get(context.Background(), types.NamespacedName{Namespace: testNS, Name: task.Spec.ProjectRef}, &proj))
	require.NoError(t, r.setLifecycleLabel(context.Background(), &proj, task, "tatara-approved"))
	require.Equal(t, []string{"tatara-approved"}, w.added)
	require.Equal(t, []string{"tatara-idea"}, w.removed) // only the managed label removed, not "unrelated"
}

func TestSetLifecycleLabel_NoopWhenAlreadySet(t *testing.T) {
	r, task, w := seedLabelTask(t, "noop", []string{"tatara-approved"})
	var proj tatarav1alpha1.Project
	require.NoError(t, k8sClient.Get(context.Background(), types.NamespacedName{Namespace: testNS, Name: task.Spec.ProjectRef}, &proj))
	require.NoError(t, r.setLifecycleLabel(context.Background(), &proj, task, "tatara-approved"))
	require.Empty(t, w.added)
	require.Empty(t, w.removed)
}

func TestSetLifecycleLabel_NeverTouchesTriggerOrPriority(t *testing.T) {
	r, task, w := seedLabelTask(t, "scope", []string{"tatara", "priority/high", "tatara-idea"})
	var proj tatarav1alpha1.Project
	require.NoError(t, k8sClient.Get(context.Background(), types.NamespacedName{Namespace: testNS, Name: task.Spec.ProjectRef}, &proj))
	require.NoError(t, r.setLifecycleLabel(context.Background(), &proj, task, "tatara-rejected"))
	require.Equal(t, []string{"tatara-rejected"}, w.added)
	require.Equal(t, []string{"tatara-idea"}, w.removed) // tatara + priority/high untouched
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/controller/ -run TestSetLifecycleLabel`
Expected: FAIL - `r.setLifecycleLabel undefined`.

- [ ] **Step 3: Implement labels.go**

Create `internal/controller/labels.go`:

```go
package controller

import (
	"context"
	"fmt"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/scm"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

// lifecycleLabels returns the three managed labels for the project, applying the
// tatara-idea/tatara-approved/tatara-rejected defaults when a field is empty.
func lifecycleLabels(s *tatarav1alpha1.ScmSpec) (idea, approved, rejected string) {
	idea, approved, rejected = "tatara-idea", "tatara-approved", "tatara-rejected"
	if s == nil {
		return
	}
	if s.IdeaLabel != "" {
		idea = s.IdeaLabel
	}
	if s.ApprovedLabel != "" {
		approved = s.ApprovedLabel
	}
	if s.RejectedLabel != "" {
		rejected = s.RejectedLabel
	}
	return
}

// setLifecycleLabel ensures exactly `desired` of the three managed labels is
// present on the task's source issue: it adds `desired` if absent and removes
// the other two managed labels if present. It never touches any non-managed
// label (triggerLabel, priorityLabel, etc.). Idempotent: a no-op when already in
// the target state. AddLabel failures are returned (caller requeues); RemoveLabel
// failures are logged and tolerated (a lingering extra label self-heals next pass).
func (r *TaskReconciler) setLifecycleLabel(ctx context.Context, proj *tatarav1alpha1.Project, task *tatarav1alpha1.Task, desired string) error {
	if task.Spec.Source == nil || task.Spec.Source.IssueRef == "" {
		return nil
	}
	l := log.FromContext(ctx)
	idea, approved, rejected := lifecycleLabels(proj.Spec.Scm)
	managed := []string{idea, approved, rejected}
	_, repo, writer, token, provider, err := r.scmContext(ctx, task)
	if err != nil {
		return fmt.Errorf("set label: %w", err)
	}
	issueRef := task.Spec.Source.IssueRef

	// Read current labels (best-effort) so we add/remove only what is needed and
	// avoid 404 churn from removing absent labels.
	current := map[string]bool{}
	if r.ReaderFor != nil {
		if reader, rerr := r.ReaderFor(provider, token); rerr == nil {
			if owner, name, oerr := scm.OwnerRepo(repo.Spec.URL); oerr == nil {
				if issues, lerr := reader.ListOpenIssues(ctx, owner, name); lerr == nil {
					for _, iss := range issues {
						if fmt.Sprintf("%s#%d", iss.Repo, iss.Number) == issueRef {
							for _, lb := range iss.Labels {
								current[lb] = true
							}
							break
						}
					}
				}
			}
		}
	}

	if !current[desired] {
		if aerr := writer.AddLabel(ctx, token, issueRef, desired); aerr != nil {
			r.recordSCM(provider, "add_label", aerr)
			return fmt.Errorf("set label add %q: %w", desired, aerr)
		}
		r.recordSCM(provider, "add_label", nil)
	}
	for _, lb := range managed {
		if lb == desired || !current[lb] {
			continue
		}
		if rerr := writer.RemoveLabel(ctx, token, issueRef, lb); rerr != nil {
			r.recordSCM(provider, "remove_label", rerr)
			l.Info("set label: remove other label failed (non-fatal)",
				"action", "scm_set_label", "resource_id", task.Name, "issue_ref", issueRef, "label", lb, "err", rerr.Error())
			continue
		}
		r.recordSCM(provider, "remove_label", nil)
	}
	l.Info("lifecycle label set", "action", "scm_set_label",
		"resource_id", task.Name, "issue_ref", issueRef, "label", desired)
	return nil
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `go test ./internal/controller/ -run TestSetLifecycleLabel`
Expected: PASS (3 subtests).

- [ ] **Step 5: Commit**

```bash
git add internal/controller/labels.go internal/controller/labels_test.go
git commit -m "feat(operator): add setLifecycleLabel egress helper for managed issue labels"
```

---

## Task 3: hasHumanComment helper (bot-authored guard input)

**Files:**
- Modify: `internal/controller/labels.go`
- Test: `internal/controller/labels_test.go`

- [ ] **Step 1: Write the failing test**

Append to `internal/controller/labels_test.go`. Extend `labelReader` to return configurable comments:

```go
type commentReader struct {
	fakeProposalReader
	comments []scm.IssueComment
}

func (r *commentReader) ListIssueComments(_ context.Context, _, _ string, _ int) ([]scm.IssueComment, error) {
	return r.comments, nil
}

func newReconcilerWithReader(rdr scm.SCMReader) *TaskReconciler {
	return &TaskReconciler{Client: k8sClient, Scheme: k8sClient.Scheme(),
		Metrics:   obs.NewOperatorMetrics(prometheus.NewRegistry()),
		SCMFor:    func(string) (scm.SCMWriter, error) { return &labelWriter{}, nil },
		ReaderFor: func(_, _ string) (scm.SCMReader, error) { return rdr, nil }}
}

func TestHasHumanComment(t *testing.T) {
	_, task, _ := seedLabelTask(t, "humancmt", nil)
	var proj tatarav1alpha1.Project
	require.NoError(t, k8sClient.Get(context.Background(), types.NamespacedName{Namespace: testNS, Name: task.Spec.ProjectRef}, &proj))

	// only a bot comment -> false
	r1 := newReconcilerWithReader(&commentReader{comments: []scm.IssueComment{{Author: "tatara-bot", Body: "proposal"}}})
	got, err := r1.hasHumanComment(context.Background(), &proj, task)
	require.NoError(t, err)
	require.False(t, got)

	// a human comment present -> true
	r2 := newReconcilerWithReader(&commentReader{comments: []scm.IssueComment{{Author: "tatara-bot", Body: "x"}, {Author: "szymon", Body: "looks good, go"}}})
	got, err = r2.hasHumanComment(context.Background(), &proj, task)
	require.NoError(t, err)
	require.True(t, got)
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/controller/ -run TestHasHumanComment`
Expected: FAIL - `r.hasHumanComment undefined`.

- [ ] **Step 3: Implement hasHumanComment**

Append to `internal/controller/labels.go`:

```go
// hasHumanComment reports whether the task's source issue has at least one
// comment authored by a non-bot login. Used to gate self-approval of
// bot-authored issues: tatara never self-approves its own idea before a human
// has engaged. Returns the underlying error so the caller can fail closed.
func (r *TaskReconciler) hasHumanComment(ctx context.Context, proj *tatarav1alpha1.Project, task *tatarav1alpha1.Task) (bool, error) {
	if r.ReaderFor == nil || task.Spec.Source == nil {
		return false, nil
	}
	botLogin := ""
	if proj.Spec.Scm != nil {
		botLogin = proj.Spec.Scm.BotLogin
	}
	provider := task.Spec.Source.Provider
	if provider == "" && proj.Spec.Scm != nil {
		provider = proj.Spec.Scm.Provider
	}
	token, err := r.scmToken(ctx, task.Namespace, proj.Spec.ScmSecretRef)
	if err != nil {
		return false, err
	}
	reader, err := r.ReaderFor(provider, token)
	if err != nil {
		return false, err
	}
	owner, name, err := scm.OwnerRepo(r.repoURLForTask(ctx, task))
	if err != nil {
		return false, err
	}
	comments, err := reader.ListIssueComments(ctx, owner, name, task.Spec.Source.Number)
	if err != nil {
		return false, err
	}
	for _, c := range comments {
		if c.Author != "" && c.Author != botLogin {
			return true, nil
		}
	}
	return false, nil
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `go test ./internal/controller/ -run TestHasHumanComment` -> PASS

- [ ] **Step 5: Commit**

```bash
git add internal/controller/labels.go internal/controller/labels_test.go
git commit -m "feat(operator): add hasHumanComment helper for bot-authored self-approve guard"
```

---

## Task 4: Wire labels + bot-authored guard into finishTriage

**Files:**
- Modify: `internal/controller/lifecycle.go:445-486` (finishTriage switch) and add `enterConversation` helper
- Test: `internal/controller/lifecycle_label_test.go` (new)

- [ ] **Step 1: Write the failing test**

Create `internal/controller/lifecycle_label_test.go`. This drives `finishTriage` directly with a Succeeded triage task carrying an `IssueOutcome`, asserting the label egress and resulting lifecycle state. Reuse `seedLabelTask`/`labelWriter`/`commentReader`.

```go
package controller

import (
	"context"
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/stretchr/testify/require"
	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/obs"
	"github.com/szymonrychu/tatara-operator/internal/scm"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

// reconcilerFor builds a TaskReconciler with the given writer + reader for the
// finishTriage label path. resetAgentRun and setLifecycleState use the same
// k8sClient, so the seeded task must already exist.
func reconcilerFor(w scm.SCMWriter, rdr scm.SCMReader) *TaskReconciler {
	return &TaskReconciler{Client: k8sClient, Scheme: k8sClient.Scheme(),
		Metrics:   obs.NewOperatorMetrics(prometheus.NewRegistry()),
		SCMFor:    func(string) (scm.SCMWriter, error) { return w, nil },
		ReaderFor: func(_, _ string) (scm.SCMReader, error) { return rdr, nil }}
}

func markSucceededWithOutcome(t *testing.T, name, action string) {
	t.Helper()
	ctx := context.Background()
	var fresh tatarav1alpha1.Task
	require.NoError(t, k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: name}, &fresh))
	fresh.Status.Phase = "Succeeded"
	fresh.Status.IssueOutcome = &tatarav1alpha1.IssueOutcome{Action: action, Comment: "c"}
	require.NoError(t, k8sClient.Status().Update(ctx, &fresh))
}

func getTask(t *testing.T, name string) *tatarav1alpha1.Task {
	t.Helper()
	var fresh tatarav1alpha1.Task
	require.NoError(t, k8sClient.Get(context.Background(), types.NamespacedName{Namespace: testNS, Name: name}, &fresh))
	return &fresh
}

func TestFinishTriage_HumanFiledImplement_Approved(t *testing.T) {
	r, task, w := seedLabelTask(t, "hf-impl", []string{"tatara-idea"})
	w2 := w // labelWriter
	r = reconcilerFor(w2, &commentReader{})
	var proj tatarav1alpha1.Project
	require.NoError(t, k8sClient.Get(context.Background(), types.NamespacedName{Namespace: testNS, Name: task.Spec.ProjectRef}, &proj))
	markSucceededWithOutcome(t, task.Name, "implement")
	got := getTask(t, task.Name)
	_, err := r.finishTriage(context.Background(), &proj, got)
	require.NoError(t, err)
	require.Equal(t, []string{"tatara-approved"}, w2.added)
	require.Equal(t, "Implement", getTask(t, task.Name).Status.LifecycleState)
}

func TestFinishTriage_Close_Rejected(t *testing.T) {
	r, task, w := seedLabelTask(t, "close-rej", []string{"tatara-idea"})
	r = reconcilerFor(w, &commentReader{})
	var proj tatarav1alpha1.Project
	require.NoError(t, k8sClient.Get(context.Background(), types.NamespacedName{Namespace: testNS, Name: task.Spec.ProjectRef}, &proj))
	markSucceededWithOutcome(t, task.Name, "close")
	_, err := r.finishTriage(context.Background(), &proj, getTask(t, task.Name))
	require.NoError(t, err)
	require.Equal(t, []string{"tatara-rejected"}, w.added)
	require.Equal(t, "Done", getTask(t, task.Name).Status.LifecycleState)
}

func TestFinishTriage_Discuss_Idea(t *testing.T) {
	r, task, w := seedLabelTask(t, "disc-idea", nil)
	r = reconcilerFor(w, &commentReader{})
	var proj tatarav1alpha1.Project
	require.NoError(t, k8sClient.Get(context.Background(), types.NamespacedName{Namespace: testNS, Name: task.Spec.ProjectRef}, &proj))
	markSucceededWithOutcome(t, task.Name, "discuss")
	_, err := r.finishTriage(context.Background(), &proj, getTask(t, task.Name))
	require.NoError(t, err)
	require.Equal(t, []string{"tatara-idea"}, w.added)
	require.Equal(t, "Conversation", getTask(t, task.Name).Status.LifecycleState)
}

func TestFinishTriage_BotAuthoredImplement_NoHumanComment_ParksIdea(t *testing.T) {
	r, task, w := seedLabelTask(t, "bot-noh", nil)
	// make the task bot-authored
	got := getTask(t, task.Name)
	got.Spec.Source.AuthorLogin = "tatara-bot"
	require.NoError(t, k8sClient.Update(context.Background(), got))
	r = reconcilerFor(w, &commentReader{comments: []scm.IssueComment{{Author: "tatara-bot", Body: "my idea"}}})
	var proj tatarav1alpha1.Project
	require.NoError(t, k8sClient.Get(context.Background(), types.NamespacedName{Namespace: testNS, Name: task.Spec.ProjectRef}, &proj))
	markSucceededWithOutcome(t, task.Name, "implement")
	_, err := r.finishTriage(context.Background(), &proj, getTask(t, task.Name))
	require.NoError(t, err)
	require.Equal(t, []string{"tatara-idea"}, w.added) // downgraded
	require.Equal(t, "Conversation", getTask(t, task.Name).Status.LifecycleState)
}

func TestFinishTriage_BotAuthoredImplement_WithHumanComment_Approved(t *testing.T) {
	r, task, w := seedLabelTask(t, "bot-h", nil)
	got := getTask(t, task.Name)
	got.Spec.Source.AuthorLogin = "tatara-bot"
	require.NoError(t, k8sClient.Update(context.Background(), got))
	r = reconcilerFor(w, &commentReader{comments: []scm.IssueComment{{Author: "szymon", Body: "approved, go"}}})
	var proj tatarav1alpha1.Project
	require.NoError(t, k8sClient.Get(context.Background(), types.NamespacedName{Namespace: testNS, Name: task.Spec.ProjectRef}, &proj))
	markSucceededWithOutcome(t, task.Name, "implement")
	_, err := r.finishTriage(context.Background(), &proj, getTask(t, task.Name))
	require.NoError(t, err)
	require.Equal(t, []string{"tatara-approved"}, w.added)
	require.Equal(t, "Implement", getTask(t, task.Name).Status.LifecycleState)
}
```

Note: `seedLabelTask` returns a reconciler we discard here (reassigned via `reconcilerFor`) so the writer/reader are the ones we assert on. `_ = r` if the linter flags the first assignment; prefer `_, task, w := seedLabelTask(...)`.

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/controller/ -run TestFinishTriage`
Expected: FAIL - existing `finishTriage` adds no labels (assertions on `w.added` fail) and bot-authored implement goes to Implement, not Conversation.

- [ ] **Step 3: Add the enterConversation helper**

In `internal/controller/lifecycle.go`, add near `handleConversation`:

```go
// enterConversation sets the conversation idle deadline + LastActivityAt and
// transitions the task to Conversation with the given reason. Shared by the
// discuss and bot-await-approval triage outcomes.
func (r *TaskReconciler) enterConversation(ctx context.Context, project *tatarav1alpha1.Project, task *tatarav1alpha1.Task, reason string) error {
	idleMinutes := 60
	if project.Spec.Scm != nil && project.Spec.Scm.ConversationIdleMinutes > 0 {
		idleMinutes = project.Spec.Scm.ConversationIdleMinutes
	}
	if err := retry.RetryOnConflict(retry.DefaultRetry, func() error {
		fresh := &tatarav1alpha1.Task{}
		if err := r.Get(ctx, client.ObjectKeyFromObject(task), fresh); err != nil {
			return err
		}
		now := metav1.Now()
		deadline := metav1.NewTime(now.Add(time.Duration(idleMinutes) * time.Minute))
		fresh.Status.DeadlineAt = &deadline
		fresh.Status.LastActivityAt = &now
		return r.Status().Update(ctx, fresh)
	}); err != nil {
		return fmt.Errorf("enter conversation: set deadline: %w", err)
	}
	return r.setLifecycleState(ctx, task, "Conversation", reason)
}
```

- [ ] **Step 4: Rewrite the finishTriage switch**

Replace the `switch action { ... }` block in `finishTriage` (`internal/controller/lifecycle.go:445-483`) with:

```go
	idea, approved, rejected := lifecycleLabels(project.Spec.Scm)

	switch action {
	case "close":
		if err := r.setLifecycleLabel(ctx, project, task, rejected); err != nil {
			return ctrl.Result{}, err
		}
		if err := r.triageCloseIssue(ctx, project, task, comment); err != nil {
			return ctrl.Result{}, err
		}
		if err := r.setLifecycleState(ctx, task, "Done", "triage-close"); err != nil {
			return ctrl.Result{}, err
		}

	case "discuss":
		if err := r.setLifecycleLabel(ctx, project, task, idea); err != nil {
			return ctrl.Result{}, err
		}
		if err := r.triagePostComment(ctx, project, task, comment); err != nil {
			return ctrl.Result{}, err
		}
		if err := r.enterConversation(ctx, project, task, "triage-discuss"); err != nil {
			return ctrl.Result{}, err
		}

	default: // "implement" and anything else
		// Bot-authored self-approve guard (rules R1/R2): tatara never approves its
		// own idea before a human has engaged. A human-filed issue may self-approve.
		if task.Spec.Source != nil && project.Spec.Scm != nil &&
			project.Spec.Scm.BotLogin != "" && task.Spec.Source.AuthorLogin == project.Spec.Scm.BotLogin {
			human, herr := r.hasHumanComment(ctx, project, task)
			if herr != nil {
				l.Info("triage: hasHumanComment failed; parking as idea (fail closed)",
					"action", "lifecycle_triage_guard", "resource_id", task.Name, "err", herr.Error())
				human = false
			}
			if !human {
				if err := r.setLifecycleLabel(ctx, project, task, idea); err != nil {
					return ctrl.Result{}, err
				}
				if err := r.enterConversation(ctx, project, task, "triage-await-approval"); err != nil {
					return ctrl.Result{}, err
				}
				return ctrl.Result{}, r.resetAgentRun(ctx, task)
			}
		}
		if err := r.setLifecycleLabel(ctx, project, task, approved); err != nil {
			return ctrl.Result{}, err
		}
		if err := r.setLifecycleState(ctx, task, "Implement", "triage-implement"); err != nil {
			return ctrl.Result{}, err
		}
	}

	return ctrl.Result{}, r.resetAgentRun(ctx, task)
```

This deletes the inline deadline block in the old `discuss` case (now in `enterConversation`).

- [ ] **Step 5: Run test to verify it passes**

Run: `go test ./internal/controller/ -run TestFinishTriage` -> PASS (5 subtests)
Run: `go test ./internal/controller/ -run 'TestSetLifecycleLabel|TestHasHumanComment'` -> still PASS

- [ ] **Step 6: Commit**

```bash
git add internal/controller/lifecycle.go internal/controller/lifecycle_label_test.go
git commit -m "feat(operator): apply managed labels in finishTriage with bot-authored self-approve guard"
```

---

## Task 5: Update the triage prompt for conversation-driven approval

**Files:**
- Modify: `internal/controller/turnloop.go:71-84` (lifecycleTriageText body)
- Test: `internal/controller/turnloop_test.go` (add a focused assertion; if the file does not exist, create it)

- [ ] **Step 1: Write the failing test**

Add to `internal/controller/turnloop_test.go`:

```go
func TestLifecycleTriageText_ApprovalInstructions(t *testing.T) {
	task := &tatarav1alpha1.Task{Spec: tatarav1alpha1.TaskSpec{Source: &tatarav1alpha1.TaskSource{IssueRef: "o/r#1", URL: "u"}}}
	got := lifecycleTriageText(task, "T", "B")
	if !strings.Contains(got, "human") || !strings.Contains(got, "approval comment") {
		t.Fatalf("triage prompt missing conversation-approval guidance:\n%s", got)
	}
}
```

(Ensure the file imports `strings` and `tatarav1alpha1`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/controller/ -run TestLifecycleTriageText_ApprovalInstructions`
Expected: FAIL - phrase absent.

- [ ] **Step 3: Extend the prompt**

In `internal/controller/turnloop.go`, replace the format string body of `lifecycleTriageText` (the `return fmt.Sprintf(...)` block, lines 71-84) with:

```go
	return fmt.Sprintf(
		"You are the tatara lifecycle agent performing Triage for issue %s (%s).\n\n"+
			"Issue title: %s\n"+
			"Issue body:\n%s\n\n"+
			"Your job:\n"+
			"1. Read the issue AND the full conversation thread carefully.\n"+
			"2. Use tatara MCP tools (memory, code search, docs) to understand the codebase.\n"+
			"3. Decide the outcome by interpreting the human's intent in the thread:\n"+
			"   - A human approval / go-ahead -> action=implement.\n"+
			"   - A human decline, or duplicate / out-of-scope / not-actionable -> action=close (supply the reason as `comment`).\n"+
			"   - Still under discussion or needing the human -> action=discuss (supply your questions as `comment`).\n"+
			"4. IMPORTANT: if THIS issue was opened by tatara itself (a tatara idea), emit action=implement "+
			"ONLY if a human has posted an approval comment in the thread; otherwise emit action=discuss and wait.\n"+
			"5. Call the `issue_outcome` MCP tool with your chosen action.\n\n"+
			"You MUST call issue_outcome before finishing. Do not open PRs or make code changes in this turn.",
		issueRef, issueURL, title, body)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `go test ./internal/controller/ -run TestLifecycleTriageText` -> PASS
Run: `go test ./internal/controller/ -run TestBuildTriagePrompt` (if present) -> still PASS

- [ ] **Step 5: Commit**

```bash
git add internal/controller/turnloop.go internal/controller/turnloop_test.go
git commit -m "feat(operator): triage prompt interprets conversation for approval; bot ideas wait for human"
```

---

## Task 6: Brainstorm proposals open with idea label and complete (no AwaitingApproval)

**Files:**
- Modify: `internal/controller/writeback.go` - `createProposal` (label), add `completeProposal`, replace 3 `advanceToAwaitingApproval` callsites (lines 310, 401, 478), delete `advanceToAwaitingApproval` (407-447)
- Test: `internal/controller/proposal_dedup_test.go` (rewrite AwaitingApproval assertions to Succeeded)

- [ ] **Step 1: Update the failing assertions first (red)**

In `internal/controller/proposal_dedup_test.go`, every assertion of the form `require.Equal(t, "AwaitingApproval", <task>.Status.Phase)` becomes `require.Equal(t, "Succeeded", <task>.Status.Phase)`. Also rename helper comments referencing "advances the Task to AwaitingApproval" to "completes the Task (Succeeded)". Search the file: `rg -n "AwaitingApproval" internal/controller/proposal_dedup_test.go`.

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/controller/ -run TestCreateProposal`
Expected: FAIL - current code still sets `AwaitingApproval`.

- [ ] **Step 3: Implement completeProposal + idea label**

In `internal/controller/writeback.go`:

(a) In `createProposal`, replace the approval-label block (lines 344-347):

```go
	idea, _, _ := lifecycleLabels(proj.Spec.Scm)
	label := idea
```

(b) Replace the three `return r.advanceToAwaitingApproval(ctx, task, <url>)` calls (lines 310, 401, and the one at 478 inside `recordExistingProposal`) with `return r.completeProposal(ctx, task, <url>)`.

(c) Delete `advanceToAwaitingApproval` (lines 404-447) and add:

```go
// completeProposal marks the brainstorm proposal Task Succeeded after the idea
// issue has been opened. The issue (now carrying the idea label) flows through
// the normal issue lifecycle from here; there is no AwaitingApproval parking.
func (r *TaskReconciler) completeProposal(ctx context.Context, task *tatarav1alpha1.Task, issueURL string) (ctrl.Result, error) {
	if err := retry.RetryOnConflict(retry.DefaultRetry, func() error {
		fresh := &tatarav1alpha1.Task{}
		if gerr := r.Get(ctx, client.ObjectKeyFromObject(task), fresh); gerr != nil {
			return gerr
		}
		fresh.Status.Phase = "Succeeded"
		present := false
		for _, u := range fresh.Status.DiscoveredIssues {
			if u == issueURL {
				present = true
				break
			}
		}
		if !present {
			fresh.Status.DiscoveredIssues = append(fresh.Status.DiscoveredIssues, issueURL)
		}
		apimeta.SetStatusCondition(&fresh.Status.Conditions, metav1.Condition{
			Type:               "WritebackPending",
			Status:             metav1.ConditionFalse,
			Reason:             "BrainstormProposed",
			Message:            "proposal issue opened with idea label",
			ObservedGeneration: fresh.Generation,
		})
		return r.Status().Update(ctx, fresh)
	}); err != nil {
		return ctrl.Result{}, fmt.Errorf("proposal: complete: %w", err)
	}
	return ctrl.Result{}, nil
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `go test ./internal/controller/ -run TestCreateProposal` -> PASS
Run: `go build ./...` -> no `advanceToAwaitingApproval` references remain (if build fails, grep for stragglers: `rg -n advanceToAwaitingApproval internal/`).

- [ ] **Step 5: Commit**

```bash
git add internal/controller/writeback.go internal/controller/proposal_dedup_test.go
git commit -m "feat(operator): brainstorm opens idea-labelled issue and completes; drop AwaitingApproval parking"
```

---

## Task 7: proposalBacklog counts idea-labelled issues; brainstorm passes idea label

**Files:**
- Modify: `internal/controller/projectscan.go` - `brainstorm` (line 693-696), `proposalBacklog` (751-781)
- Test: `internal/controller/projectscan_backlog_test.go` (new) or extend existing backlog test

- [ ] **Step 1: Write the failing test**

Note: the count is now over ALL open non-PR issues bearing the idea label (this subsumes bot-authored ideas and is conservative backpressure - documented deviation from the spec wording "bot-authored", which `IssueRef` cannot express without an Author field). Create `internal/controller/projectscan_backlog_test.go`:

```go
package controller

import (
	"context"
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/stretchr/testify/require"
	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/obs"
	"github.com/szymonrychu/tatara-operator/internal/scm"
)

type backlogReader struct {
	fakeProposalReader
	issues []scm.IssueRef
}

func (r *backlogReader) ListOpenIssues(_ context.Context, _, _ string) ([]scm.IssueRef, error) {
	return r.issues, nil
}

func TestProposalBacklog_CountsIdeaLabel(t *testing.T) {
	repo := &tatarav1alpha1.Repository{Spec: tatarav1alpha1.RepositorySpec{URL: "https://github.com/o/r.git"}}
	rdr := &backlogReader{issues: []scm.IssueRef{
		{Repo: "o/r", Number: 1, Labels: []string{"tatara-idea"}},
		{Repo: "o/r", Number: 2, Labels: []string{"tatara-approved"}}, // not counted
		{Repo: "o/r", Number: 3, Labels: []string{"tatara-idea"}, IsPR: true}, // PR, not counted
		{Repo: "o/r", Number: 4, Labels: []string{"tatara-idea"}},
	}}
	r := &ProjectReconciler{Metrics: obs.NewOperatorMetrics(prometheus.NewRegistry())}
	n, err := r.proposalBacklog(context.Background(), rdr, repo, "tatara-idea", nil)
	require.NoError(t, err)
	require.Equal(t, 2, n)
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/controller/ -run TestProposalBacklog_CountsIdeaLabel`
Expected: FAIL or compile error - current `proposalBacklog` has the `approvalLabel == ""` fallback branch and is keyed to approval semantics; the new test asserts pure idea-label counting. (It may pass coincidentally because counting logic is the same; if so, proceed to harden in Step 3 and keep the test as a regression guard.)

- [ ] **Step 3: Simplify proposalBacklog and switch brainstorm to the idea label**

In `internal/controller/projectscan.go`:

(a) Replace `proposalBacklog` (lines 751-781) with the idea-label-only form (drop the `approvalLabel == ""` AwaitingApproval fallback, now dead since proposals no longer park):

```go
// proposalBacklog counts open, undecided ideas for repo: open non-PR issues
// bearing the idea label (live ListOpenIssues). This subsumes tatara-originated
// proposals and any human-filed issue parked as an idea, providing conservative
// brainstorm backpressure.
func (r *ProjectReconciler) proposalBacklog(ctx context.Context, reader scm.SCMReader, repo *tatarav1alpha1.Repository, ideaLabel string, _ []tatarav1alpha1.Task) (int, error) {
	owner, name, err := scm.OwnerRepo(repo.Spec.URL)
	if err != nil {
		return 0, err
	}
	issues, err := reader.ListOpenIssues(ctx, owner, name)
	if err != nil {
		return 0, err
	}
	n := 0
	for _, iss := range issues {
		if !iss.IsPR && hasLabel(iss.Labels, ideaLabel) {
			n++
		}
	}
	return n, nil
}
```

(b) In `brainstorm` (lines 693-696), replace the approval-label lookup with the idea label:

```go
	ideaLabel, _, _ := lifecycleLabels(proj.Spec.Scm)
```

and update the call `r.proposalBacklog(ctx, reader, &repo, approvalLabel, existing)` to `r.proposalBacklog(ctx, reader, &repo, ideaLabel, existing)`. Remove the now-unused `approvalLabel := ""` block (lines 693-696).

- [ ] **Step 4: Run test to verify it passes**

Run: `go test ./internal/controller/ -run TestProposalBacklog` -> PASS
Run: `go build ./...` -> no errors

- [ ] **Step 5: Commit**

```bash
git add internal/controller/projectscan.go internal/controller/projectscan_backlog_test.go
git commit -m "feat(operator): brainstorm backlog counts open idea-labelled issues"
```

---

## Task 8: Retire the webhook label-toggle approval path

**Files:**
- Modify: `internal/webhook/server.go` - remove the `unlabeled` branch (lines 163-175), `flipApproval` (528-568), `approvalLabel` (570-575)
- Delete: `internal/webhook/approval_flip_test.go`
- Test: `internal/webhook/` existing routing tests must still pass

- [ ] **Step 1: Delete the obsolete test (red via build)**

```bash
git rm internal/webhook/approval_flip_test.go
```

- [ ] **Step 2: Remove the unlabeled branch**

In `internal/webhook/server.go`, delete the entire approval-flip comment + `if ev.Action == "unlabeled" && ...` block (lines 163-175, the block ending at the closing `}` before the triggerLabel comment at 177).

- [ ] **Step 3: Remove flipApproval and approvalLabel**

Delete `func (s *Server) flipApproval(...)` (528-568) and `func approvalLabel(p tatarav1.Project) string` (570-575).

- [ ] **Step 4: Verify build + routing tests**

Run: `go build ./...`
Expected: PASS. If a compile error references `flipApproval`/`approvalLabel`, grep and remove the straggler: `rg -n "flipApproval|approvalLabel" internal/webhook/`.
Run: `go test ./internal/webhook/...`
Expected: PASS - push, issue_comment, triggerLabel-jump, and kind-switch tests are unaffected.

- [ ] **Step 5: Commit**

```bash
git add internal/webhook/server.go
git commit -m "refactor(operator): retire webhook label-removal approval (flipApproval); approval is conversation-driven"
```

---

## Task 9: Retire the approvalBackstop

**Files:**
- Modify: `internal/controller/projectscan.go` - remove `approvalBackstop` (783-832), `implRunningForIssue` (834-847), `issueState` (849-861), `repoByName` (863-871), `flipApprovalApproved` (873-890), and the `runScans` call site (line 971)
- Delete obsolete backstop tests in `internal/controller/projectscan_run_test.go` (the `approvalBackstop` test functions referencing lines ~640-790)

- [ ] **Step 1: Delete the obsolete tests (red via build)**

In `internal/controller/projectscan_run_test.go`, delete the test functions that call `r.approvalBackstop(...)` (the three call sites at lines 691, 723, 772 and their enclosing `func Test...` bodies, plus any helper only they use). Identify exact bounds: `rg -n "func Test|approvalBackstop" internal/controller/projectscan_run_test.go`.

- [ ] **Step 2: Run to verify it fails to build**

Run: `go vet ./internal/controller/` after Step 3 removals; before removals the suite still references removed symbols. (This task is a removal; the "test" is a green suite with the dead approval machinery gone.)

- [ ] **Step 3: Remove the functions + call site**

In `internal/controller/projectscan.go`:
- Delete the `runScans` line `r.approvalBackstop(ctx, proj, reader, repos, existing)` (line 971).
- Delete `approvalBackstop`, `implRunningForIssue`, `issueState`, `repoByName`, `flipApprovalApproved` (lines 783-890).

- [ ] **Step 4: Verify build + tests**

Run: `go build ./...`
Expected: PASS. Stragglers: `rg -n "approvalBackstop|implRunningForIssue|issueState|repoByName|flipApprovalApproved" internal/`.
Run: `go test ./internal/controller/...`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/controller/projectscan.go internal/controller/projectscan_run_test.go
git commit -m "refactor(operator): retire approvalBackstop and its dead helpers"
```

---

## Task 10: Retire the ApprovalRequired gate; propose_issue no longer requires approval

**Files:**
- Modify: `internal/controller/task_controller.go` - remove the approval gate block (lines 146-174)
- Modify: `internal/restapi/handlers.go:248` - `ApprovalRequired: true` -> `ApprovalRequired: false` (or drop the field from the literal)
- Modify: `internal/restapi/handlers_test.go:236` - assertion `ApprovalRequired` true -> false
- Delete: `internal/controller/approval_gate_test.go`

- [ ] **Step 1: Delete obsolete gate test + flip the handler assertion (red)**

```bash
git rm internal/controller/approval_gate_test.go
```
In `internal/restapi/handlers_test.go:236`, change `require.True(t, out.ApprovalRequired)` to `require.False(t, out.ApprovalRequired)`.

- [ ] **Step 2: Run to verify it fails**

Run: `go test ./internal/restapi/ -run TestProposeIssue` (or the test covering handlers.go:236)
Expected: FAIL - handler still sets `ApprovalRequired: true`.

- [ ] **Step 3: Remove the gate + stop requiring approval**

(a) In `internal/restapi/handlers.go`, line 248, set `ApprovalRequired: false,` (keep `Kind: "implement"` and `ProposedIssue`).

(b) In `internal/controller/task_controller.go`, delete the entire approval gate block (lines 146-174, the `// Approval gate:` comment through the closing brace of `if task.Spec.ApprovalRequired { ... }`).

- [ ] **Step 4: Verify build + tests**

Run: `go build ./...`
Expected: PASS. The proposal task now flows: dispatch (Kind implement + ProposedIssue + Source nil, task_controller.go:136) -> `createProposal` -> `completeProposal` (Succeeded). It never reaches the removed gate.
Run: `go test ./internal/restapi/... ./internal/controller/...`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/controller/task_controller.go internal/restapi/handlers.go internal/restapi/handlers_test.go
git commit -m "refactor(operator): retire ApprovalRequired gate; propose_issue does not require approval"
```

---

## Task 11: Remove the now-unused approval metrics

**Files:**
- Modify: `internal/obs/operator_metrics.go` - remove `ObserveApprovalGate` (~235) and `ApprovalBackstopFlip` (~245) plus their registered collector fields
- Modify: `internal/obs/operator_metrics_test.go` - remove the `m.ObserveApprovalGate(42.0)` (line ~199) and `m.ApprovalBackstopFlip()` (lines ~259-260) assertions

- [ ] **Step 1: Remove the metric test calls (red via build)**

In `internal/obs/operator_metrics_test.go`, delete the lines calling `ObserveApprovalGate` and `ApprovalBackstopFlip` (and any assertion reading those collectors).

- [ ] **Step 2: Verify failing build**

Run: `go build ./internal/obs/`
Expected: still builds (defs present). The removal is validated by the suite passing after Step 3.

- [ ] **Step 3: Remove the metric definitions**

In `internal/obs/operator_metrics.go`, remove the `ObserveApprovalGate` method, the `ApprovalBackstopFlip` method, and the struct fields + `prometheus.NewHistogram`/`NewCounter` registrations they use (`operator_approval_*`). Grep first: `rg -n "ApprovalGate|ApprovalBackstop|approval_gate|approval_backstop" internal/obs/`.

- [ ] **Step 4: Verify build + tests**

Run: `go build ./...`
Expected: PASS. Stragglers: `rg -n "ObserveApprovalGate|ApprovalBackstopFlip" internal/`.
Run: `go test ./internal/obs/...`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/obs/operator_metrics.go internal/obs/operator_metrics_test.go
git commit -m "refactor(operator): drop unused approval-gate and approval-backstop metrics"
```

---

## Task 12: Full verification + migration runbook + MEMORY note

**Files:**
- Modify: `MEMORY.md` (tatara-operator), `ROADMAP.md` (tatara-operator)
- Create/append: deploy runbook note for the label migration (in this plan / MEMORY)

- [ ] **Step 1: Full build + lint + tests**

Run:
```bash
make manifests
gofmt -l internal/ api/        # expect: no output
go vet ./...
golangci-lint run
make test                      # envtest suite
```
Expected: all green.

- [ ] **Step 2: Confirm no orphaned references**

Run: `rg -n "advanceToAwaitingApproval|flipApproval|approvalBackstop|approvalLabel\b|ObserveApprovalGate|ApprovalBackstopFlip" internal/ | grep -v _test`
Expected: no production references (the deprecated `ApprovalLabel` *field* in `api/v1alpha1` and `AwaitingApproval` enum value may remain, intentionally).

- [ ] **Step 3: Write the MEMORY entry**

Append to `tatara-operator/MEMORY.md` (one line, dated):

```
- 2026-06-13: issue approval moved from label-toggle (awaiting-approval removal + flipApproval/approvalBackstop/ApprovalRequired gate, all removed) to conversation-driven 3-label model (tatara-idea/approved/rejected) set by operator egress in finishTriage. Brainstorm opens idea-labelled issues and completes (no AwaitingApproval). Bot-authored ideas never self-approve (require a human comment); human-filed clear-cut issues self-approve. Deprecated-but-kept no-ops: ScmSpec.ApprovalLabel field, ConditionApprovalApproved const, AwaitingApproval phase enum, Task.GateEnteredAt (avoid CRD/DTO churn).
```

Move/close the matching `ROADMAP.md` item if present.

- [ ] **Step 4: Document the deploy-time migration (runbook)**

Add to the deploy notes (and reference from MEMORY): existing open issues carrying `tatara/awaiting-approval` must be relabeled once to `tatara-idea` at deploy. Manual, low-volume, idempotent:

GitHub (per repo, bot token in env as `$GH_TOKEN`):
```bash
for repo in tatara-operator tatara-cli tatara-memory tatara-memory-repo-ingester tatara-claude-code-wrapper tatara-chat; do
  gh issue list -R szymonrychu/$repo --label "tatara/awaiting-approval" --state open --json number -q '.[].number' \
  | while read n; do
      gh issue edit -R szymonrychu/$repo "$n" --add-label "tatara-idea" --remove-label "tatara/awaiting-approval"
    done
done
```
In-flight `AwaitingApproval` proposal Tasks at deploy: they are superseded once the issue is relabeled and picked up by the lifecycle path; delete any that linger (`kubectl get task -o json | jq '... AwaitingApproval ...'`) after confirming the issue carries `tatara-idea`.

- [ ] **Step 5: Commit**

```bash
git add MEMORY.md ROADMAP.md
git commit -m "docs(operator): MEMORY + migration runbook for 3-label issue lifecycle"
```

---

## Self-review notes (addressed inline)

- **Spec coverage:** CRD fields (T1), egress helper (T2), bot-guard input (T3), finishTriage label+guard (T4), triage prompt (T5), brainstorm idea-label + complete (T6), backlog (T7), retire webhook (T8) / backstop (T9) / gate (T10), metrics cleanup (T11), migration + MEMORY (T12). All spec sections mapped.
- **Deviation (documented):** the spec said the backlog counts "bot-authored" idea issues; `IssueRef` carries no author, so T7 counts all open idea-labelled non-PR issues (conservative; subsumes bot ideas). Adding an Author field to `IssueRef` was rejected as scope creep.
- **Deviation (documented):** `ApprovalRequired` field, `ConditionApprovalApproved` const, `AwaitingApproval` phase enum, and `GateEnteredAt` are kept as deprecated no-ops rather than removed, to avoid CRD/DTO/enum churn. Recorded in MEMORY (T12).
- **Type consistency:** `lifecycleLabels(*ScmSpec) (idea, approved, rejected string)`, `setLifecycleLabel(ctx, *Project, *Task, desired string) error`, `hasHumanComment(ctx, *Project, *Task) (bool, error)`, `enterConversation(ctx, *Project, *Task, reason string) error`, `completeProposal(ctx, *Task, issueURL string) (ctrl.Result, error)` - signatures consistent across all tasks.
