# Brainstorm Backpressure + Approval Backstop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. TDD throughout: failing test first, watch it fail, minimal code, watch it pass.

**Goal:** Make the operator's brainstorm activity per-repo and backpressured (hourly; skip a repo once it holds >= maxOpenProposals open unapproved agent proposals; else start one brainstorm), and add an approval backstop that recovers proposals whose approval webhook was missed.

**Architecture:** Extends `internal/controller/projectscan.go`'s hourly `runScans`. `brainstorm()` becomes per-repo with an in-flight guard + a live-SCM proposal-count cap. A new `approvalBackstop()` step flips stuck `AwaitingApproval` proposal Tasks whose issue lost the approval label. New CRD field `BrainstormActivity.MaxOpenProposals`; new metrics.

**Tech Stack:** Go, controller-runtime, kubebuilder CRDs, Prometheus client, envtest (`make test`), gofmt + golangci-lint.

**Spec:** `docs/superpowers/specs/2026-06-13-brainstorm-backpressure-approval-backstop-design.md`

**Key existing symbols (verified):**
- `BrainstormActivity{Enabled, Schedule, MaxPerCycle, Sources}` (`api/v1alpha1/project_types.go:81`).
- `CronActivity{MaxPerRepo, Schedule}`; `ScmSpec.ApprovalLabel` (`project_types.go`).
- `ProposedIssueSpec{RepositoryRef, Title, Body, Kind}`; `TaskSpec.ProposedIssue`; `TaskSpec.Source{Number, IssueRef, ...}`.
- `ConditionApprovalApproved = "ApprovalApproved"`; phase `"AwaitingApproval"`.
- `isTerminal(phase) = Succeeded||Failed`; `isActive(phase) = Planning||Running`.
- Helpers: `scanReader`, `projectReposForScan`, `existingScanTasks`, `activityDue`, `createBrainstormTask(ctx,proj,*repo,goal,sources)`, `hasLabel(labels,want)`, `scanTaskLabels(c,activity,kind)`, `r.Metrics.ScanItem(activity,outcome)`.
- `scm.SCMReader.ListOpenIssues(ctx,owner,repo) []IssueRef{Repo,Number,Title,Labels,UpdatedAt,IsPR}`; `GetIssue(ctx,owner,repo,number) IssueContent{Title,Body}` (added earlier today).
- `scm.OwnerRepo(repoURL) (owner,repo,err)`; repo URL via `repo.Spec.URL`.

---

### Task 1: CRD field `BrainstormActivity.MaxOpenProposals`

**Files:**
- Modify: `api/v1alpha1/project_types.go` (BrainstormActivity struct)
- Modify: `config/crd/bases/*project*.yaml` + `charts/tatara-operator/crds/*` (via `make manifests`)
- Test: `api/v1alpha1/project_types_test.go`

- [ ] **Step 1: Failing test** in `project_types_test.go`:
```go
func TestBrainstormActivity_MaxOpenProposalsField(t *testing.T) {
	b := BrainstormActivity{MaxOpenProposals: 3}
	if b.MaxOpenProposals != 3 {
		t.Fatalf("MaxOpenProposals = %d, want 3", b.MaxOpenProposals)
	}
}
```
- [ ] **Step 2:** `go test ./api/v1alpha1/ -run MaxOpenProposals` -> FAIL (field undefined).
- [ ] **Step 3:** add to `BrainstormActivity` (after `MaxPerCycle`):
```go
	// MaxOpenProposals caps open, unapproved agent proposals per repo; at or
	// above this the repo is skipped. Default 3.
	// +kubebuilder:default=3
	// +optional
	MaxOpenProposals int `json:"maxOpenProposals,omitempty"`
```
Leave `MaxPerCycle` in place (retired: ignored by the new brainstorm; keep the field for API compat).
- [ ] **Step 4:** `go test ./api/v1alpha1/ -run MaxOpenProposals` -> PASS. Run `make manifests` to regen CRDs.
- [ ] **Step 5:** commit `feat(crd): BrainstormActivity.maxOpenProposals (default 3)`.

---

### Task 2: Metrics - open-proposals gauge + backstop counter

**Files:**
- Modify: `internal/obs/operator_metrics.go`
- Test: `internal/obs/operator_metrics_test.go`

- [ ] **Step 1: Failing tests** in `operator_metrics_test.go`:
```go
func TestOpenProposalsGauge(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := NewOperatorMetrics(reg)
	m.SetOpenProposals("o/r", 2)
	if got := testutil.ToFloat64(m.openProposals.WithLabelValues("o/r")); got != 2 {
		t.Fatalf("openProposals{o/r} = %v, want 2", got)
	}
}

func TestApprovalBackstopFlips(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := NewOperatorMetrics(reg)
	m.ApprovalBackstopFlip()
	m.ApprovalBackstopFlip()
	if got := testutil.ToFloat64(m.approvalBackstopFlips); got != 2 {
		t.Fatalf("approvalBackstopFlips = %v, want 2", got)
	}
}
```
- [ ] **Step 2:** `go test ./internal/obs/ -run 'OpenProposals|ApprovalBackstop'` -> FAIL (undefined).
- [ ] **Step 3:** add fields to `OperatorMetrics` struct:
```go
	openProposals         *prometheus.GaugeVec
	approvalBackstopFlips prometheus.Counter
```
construct in `NewOperatorMetrics`:
```go
		openProposals: prometheus.NewGaugeVec(prometheus.GaugeOpts{
			Name: "operator_open_proposals",
			Help: "Open, unapproved agent-proposed issues per repo.",
		}, []string{"repo"}),
		approvalBackstopFlips: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "operator_approval_backstop_flips_total",
			Help: "Approvals recovered by the backstop after a missed webhook.",
		}),
```
register both in the `reg.MustRegister(...)` list; add helpers:
```go
func (m *OperatorMetrics) SetOpenProposals(repo string, n float64) {
	m.openProposals.WithLabelValues(repo).Set(n)
}

func (m *OperatorMetrics) ApprovalBackstopFlip() { m.approvalBackstopFlips.Inc() }
```
- [ ] **Step 4:** `go test ./internal/obs/ -run 'OpenProposals|ApprovalBackstop'` -> PASS.
- [ ] **Step 5:** commit `feat(obs): open_proposals gauge + approval_backstop_flips counter`.

---

### Task 3: Per-repo backpressured brainstorm

**Files:**
- Modify: `internal/controller/projectscan.go` (`brainstorm` + its call in `runScans`)
- Test: `internal/controller/projectscan_run_test.go`

Change `brainstorm` signature to take the reader + existing tasks (like `mrScan`/`issueScan`):
`func (r *ProjectReconciler) brainstorm(ctx, proj, reader scm.SCMReader, repos []Repository, existing []Task, act BrainstormActivity)`.

- [ ] **Step 1: Failing tests** in `projectscan_run_test.go` (mirror the existing scan-test harness: `newProjectReconciler`-style ctor with a fake `ReaderFor` whose `ListOpenIssues` returns scripted `IssueRef`s; helpers to create Project with `Scm.Cron.Brainstorm` + `Scm.ApprovalLabel="tatara/awaiting-approval"`, Repositories, and Tasks). Write:
```go
// under cap, no in-flight -> one brainstorm task per repo, targeting it
func TestBrainstorm_UnderCap_CreatesOnePerRepo(t *testing.T) { /* 2 repos, 0 proposals each -> 2 brainstorm tasks, one per repo */ }
// at/over cap -> skip that repo
func TestBrainstorm_AtCap_SkipsRepo(t *testing.T) { /* repo with 3 open issues bearing approvalLabel -> no brainstorm task */ }
// in-flight brainstorm -> skip even if under cap
func TestBrainstorm_InFlight_SkipsRepo(t *testing.T) { /* pre-create a brainstorm Task (phase Planning) for repo -> no new task */ }
// listing error for one repo does not block others
func TestBrainstorm_ListErrorIsolatesRepo(t *testing.T) { /* reader errors for repo A, ok for repo B -> B gets a task */ }
```
Assert by listing Tasks with label `tatara.dev/activity=brainstorm` (via `scanTaskLabels`) and checking `Spec.RepositoryRef`.
- [ ] **Step 2:** `go test ./internal/controller/ -run Brainstorm` (envtest) -> FAIL.
- [ ] **Step 3: Implement.** Replace `brainstorm` body:
```go
func (r *ProjectReconciler) brainstorm(ctx context.Context, proj *tatarav1alpha1.Project, reader scm.SCMReader, repos []tatarav1alpha1.Repository, existing []tatarav1alpha1.Task, act tatarav1alpha1.BrainstormActivity) {
	l := log.FromContext(ctx)
	start := time.Now()
	cap := act.MaxOpenProposals
	if cap < 1 {
		cap = 3
	}
	approvalLabel := ""
	if proj.Spec.Scm != nil {
		approvalLabel = proj.Spec.Scm.ApprovalLabel
	}
	created := 0
	for i := range repos {
		repo := repos[i]
		slug := repoSlug(&repo) // owner/repo from repo.Spec.URL via scm.OwnerRepo; see helper below
		if slug == "" {
			continue
		}
		if brainstormInFlight(existing, repo.Name) {
			r.Metrics.ScanItem("brainstorm", "skipped_inflight")
			continue
		}
		backlog, err := r.proposalBacklog(ctx, reader, &repo, approvalLabel, existing)
		if err != nil {
			l.Info("brainstorm: backlog count failed (non-fatal)", "resource_id", proj.Name, "repo", repo.Name, "err", err.Error())
			continue
		}
		r.Metrics.SetOpenProposals(slug, float64(backlog))
		if backlog >= cap {
			r.Metrics.ScanItem("brainstorm", "skipped_cap")
			continue
		}
		goal := "Propose a single, well-defined issue for repo " + slug
		if _, err := r.createBrainstormTask(ctx, proj, &repo, goal, act.Sources); err != nil {
			l.Error(err, "scan: create brainstorm task", "resource_id", proj.Name, "repo", repo.Name)
			continue
		}
		r.Metrics.ScanItem("brainstorm", "picked")
		created++
	}
	r.Metrics.ObserveScanDuration("brainstorm", time.Since(start).Seconds())
	l.Info("brainstorm complete", "action", "scan_brainstorm", "resource_id", proj.Name, "picked", created, "duration_ms", time.Since(start).Milliseconds())
}

// brainstormInFlight reports whether a non-terminal brainstorm Task targets repoName.
func brainstormInFlight(existing []tatarav1alpha1.Task, repoName string) bool {
	for i := range existing {
		t := existing[i]
		if t.Labels["tatara.dev/activity"] == "brainstorm" && t.Spec.RepositoryRef == repoName && !isTerminal(t.Status.Phase) {
			return true
		}
	}
	return false
}

// proposalBacklog counts open, unapproved agent proposals for repo. When
// approvalLabel is set it counts open non-PR issues bearing that label (live
// ListOpenIssues). When empty it falls back to counting AwaitingApproval
// proposal Tasks for the repo.
func (r *ProjectReconciler) proposalBacklog(ctx context.Context, reader scm.SCMReader, repo *tatarav1alpha1.Repository, approvalLabel string, existing []tatarav1alpha1.Task) (int, error) {
	if approvalLabel == "" {
		n := 0
		for i := range existing {
			t := existing[i]
			if t.Spec.RepositoryRef == repo.Name && t.Spec.ProposedIssue != nil && t.Status.Phase == "AwaitingApproval" {
				n++
			}
		}
		return n, nil
	}
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
		if !iss.IsPR && hasLabel(iss.Labels, approvalLabel) {
			n++
		}
	}
	return n, nil
}
```
Add the exact activity-label constant: `scanTaskLabels` uses a label key for activity; reuse the SAME key (find it in `scanTaskLabels`, e.g. `labelActivity`). Replace `"tatara.dev/activity"` literals with that constant. Add `repoSlug(&repo)` helper using `scm.OwnerRepo(repo.Spec.URL)` returning `owner+"/"+name` or `""` on error (or reuse an existing slug helper if present - grep `OwnerRepo(` in projectscan.go first).
- [ ] **Step 4:** update the `runScans` brainstorm call to `r.brainstorm(ctx, proj, reader, repos, existing, cronSpec.Brainstorm)`. `go test ./internal/controller/ -run Brainstorm` -> PASS; `go build ./...`.
- [ ] **Step 5:** commit `feat(scan): per-repo backpressured brainstorm (cap maxOpenProposals, in-flight guard)`.

---

### Task 4: Approval backstop

**Files:**
- Modify: `internal/controller/projectscan.go` (new `approvalBackstop` + call in `runScans`)
- Test: `internal/controller/projectscan_run_test.go`

- [ ] **Step 1: Failing tests:**
```go
// approved (label gone) + open + no impl running -> ApprovalApproved flipped True
func TestApprovalBackstop_FlipsStuckApproved(t *testing.T) { /* AwaitingApproval proposal Task; fake GetIssue/ListOpenIssues report the issue open WITHOUT approvalLabel -> condition flipped */ }
// label still present -> no flip
func TestApprovalBackstop_NotApproved_NoOp(t *testing.T) {}
// implementation already running for the issue -> no flip
func TestApprovalBackstop_ImplRunning_NoOp(t *testing.T) {}
```
- [ ] **Step 2:** `go test ./internal/controller/ -run ApprovalBackstop` -> FAIL.
- [ ] **Step 3: Implement:**
```go
// approvalBackstop recovers proposals approved on the SCM (approval label
// removed) whose ApprovalApproved condition was never flipped (missed webhook).
func (r *ProjectReconciler) approvalBackstop(ctx context.Context, proj *tatarav1alpha1.Project, reader scm.SCMReader, repos []tatarav1alpha1.Repository, existing []tatarav1alpha1.Task) {
	l := log.FromContext(ctx)
	if proj.Spec.Scm == nil || proj.Spec.Scm.ApprovalLabel == "" {
		return
	}
	approvalLabel := proj.Spec.Scm.ApprovalLabel
	for i := range existing {
		t := existing[i]
		if t.Spec.ProposedIssue == nil || t.Status.Phase != "AwaitingApproval" || t.Spec.Source == nil {
			continue
		}
		if apimeta.IsStatusConditionTrue(t.Status.Conditions, tatarav1alpha1.ConditionApprovalApproved) {
			continue
		}
		if implRunningForIssue(existing, t.Spec.Source.IssueRef, t.Name) {
			continue
		}
		repo := repoByName(repos, t.Spec.RepositoryRef)
		if repo == nil {
			continue
		}
		owner, name, err := scm.OwnerRepo(repo.Spec.URL)
		if err != nil {
			continue
		}
		issues, err := reader.ListOpenIssues(ctx, owner, name)
		if err != nil {
			l.Info("approvalBackstop: list issues failed (non-fatal)", "resource_id", proj.Name, "repo", repo.Name, "err", err.Error())
			continue
		}
		open, labelPresent := issueState(issues, t.Spec.Source.Number, approvalLabel)
		if !open || labelPresent {
			continue // closed (webhook path), or still awaiting approval
		}
		// Approved on SCM but condition not set: flip it (RetryOnConflict).
		if err := r.flipApprovalApproved(ctx, &t); err != nil {
			l.Error(err, "approvalBackstop: flip approval", "resource_id", t.Name)
			continue
		}
		r.Metrics.ApprovalBackstopFlip()
		l.Info("approvalBackstop: recovered missed approval", "action", "approval_backstop", "resource_id", t.Name, "issue", t.Spec.Source.IssueRef)
	}
}

// implRunningForIssue reports whether some active (non-terminal, non-AwaitingApproval)
// Task other than self references issueRef.
func implRunningForIssue(existing []tatarav1alpha1.Task, issueRef, self string) bool {
	for i := range existing {
		t := existing[i]
		if t.Name == self || t.Spec.Source == nil || t.Spec.Source.IssueRef != issueRef {
			continue
		}
		if !isTerminal(t.Status.Phase) && t.Status.Phase != "AwaitingApproval" {
			return true
		}
	}
	return false
}

// issueState returns (open, hasApprovalLabel) for number among the open issues
// (open=false if number is not in the open list).
func issueState(issues []scm.IssueRef, number int, approvalLabel string) (bool, bool) {
	for _, iss := range issues {
		if iss.Number == number {
			return true, hasLabel(iss.Labels, approvalLabel)
		}
	}
	return false, false
}

func repoByName(repos []tatarav1alpha1.Repository, name string) *tatarav1alpha1.Repository {
	for i := range repos {
		if repos[i].Name == name {
			return &repos[i]
		}
	}
	return nil
}

func (r *ProjectReconciler) flipApprovalApproved(ctx context.Context, task *tatarav1alpha1.Task) error {
	return retry.RetryOnConflict(retry.DefaultRetry, func() error {
		fresh := &tatarav1alpha1.Task{}
		if err := r.Get(ctx, client.ObjectKeyFromObject(task), fresh); err != nil {
			return err
		}
		apimeta.SetStatusCondition(&fresh.Status.Conditions, metav1.Condition{
			Type: tatarav1alpha1.ConditionApprovalApproved, Status: metav1.ConditionTrue,
			Reason: "ApprovalBackstop", Message: "approval label removed on SCM; recovered by backstop",
			ObservedGeneration: fresh.Generation,
		})
		return r.Status().Update(ctx, fresh)
	})
}
```
Note: `issueState` only sees OPEN issues (ListOpenIssues), so a closed issue returns open=false -> skipped (webhook handles close). That is intentional per spec.
- [ ] **Step 4:** call `r.approvalBackstop(ctx, proj, reader, repos, existing)` once per scan reconcile in `runScans` (after the issueScan block, unconditional - it is cheap and self-gating). `go test ./internal/controller/ -run ApprovalBackstop` -> PASS; `go build ./...`.
- [ ] **Step 5:** commit `feat(scan): approval backstop for missed approval webhooks`.

---

### Task 5: Full verification + merge

- [ ] **Step 1:** `gofmt -l internal/ api/` -> empty.
- [ ] **Step 2:** `golangci-lint run ./...` -> 0 issues.
- [ ] **Step 3:** `make test` -> all packages `ok`.
- [ ] **Step 4:** request code review (opus); fix critical/high.
- [ ] **Step 5:** merge worktree branch to operator `main` (--no-ff), cleanup worktree.

---

### Task 6: Deploy + config (gated; build from main)

- [ ] **Step 1:** push operator `main`; build image locally (CI broken) `harbor.szymonrichert.pl/containers/tatara-operator:<sha>` (amd64, VERSION/COMMIT=<sha>, DATE=now); `--push`.
- [ ] **Step 2:** Project CR config (infra helmfile worktree off origin/main): in `helmfiles/tatara/values/tatara-operator/raw/project-tatara.*.yaml` set `spec.scm.cron.brainstorm.schedule: "0 * * * *"` and `spec.scm.cron.brainstorm.maxOpenProposals: 3`; in `values/tatara-operator/common.yaml` bump `image.tag` to `<sha>`. CRD changed (new field) -> `kubectl apply` the regenerated CRD (Helm does not upgrade crds/).
- [ ] **Step 3:** infra MR (auto-merge on pipeline); the child pipeline `helmfile apply` rolls the operator + re-applies the Project CR.
- [ ] **Step 4:** verify live: a repo under cap gets one brainstorm/hour; a repo at 3 open `awaiting-approval` issues is skipped; remove an approval label without a webhook (or simulate) and confirm the backstop flips the Task within the hour.

---

## Self-review notes
- Spec coverage: A (Task 3), B stuck-task (Task 4), B orphan (existing issueScan, no task), config + cadence (Task 6), metrics (Task 2), CRD field (Task 1), approvalLabel-empty fallback (Task 3 proposalBacklog). Covered.
- Confirm the activity-label key: grep `scanTaskLabels` for the exact constant (e.g. `labelActivity`) and use it in `brainstormInFlight` instead of the literal.
- Confirm there is no existing `repoSlug`/slug helper before adding one (grep `OwnerRepo(` in projectscan.go).
- `apimeta`, `metav1`, `client`, `retry`, `scm` already imported in the controller package.
