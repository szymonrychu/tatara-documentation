# Incremental Re-ingest (Operator): Full-History Namespace Clone + Per-Repository Cron Schedule -- Implementation Plan

## Header

- **Date:** 2026-06-09
- **Repo:** `tatara-operator` (`/Users/szymonri/Documents/tatara/tatara-operator`)
- **Module:** `github.com/szymonrychu/tatara-operator`, Go `1.26.3`
- **Specs (obey exactly):**
  - `docs/superpowers/specs/2026-06-09-incremental-reingest-design.md` (Component 1 + Phase 0)
  - `docs/superpowers/specs/2026-06-09-phase0-contract-lock.md` (namespacePath rule, locked)
  - `docs/superpowers/specs/2026-06-09-namespace-clone-layout-notes.md` (clone layout)
- **Scope (operator only):**
  1. `internal/ingest/job.go`: drop `--depth 1` from `cloneCmd` (full history so `<since>` resolves); set clone destination to `/workspace/<namespacePath(url)>` and point ingester `--repo-root` at it; add a tiny `namespacePath` helper.
  2. `api/v1alpha1/repository_types.go`: add required `Spec.ReingestSchedule string` (kubebuilder Required + cron pattern) and `Status.LastScheduledReingest *metav1.Time`; regenerate CRD (`make manifests`) + deepcopy (`make generate`).
  3. `internal/controller/repository_controller.go`: after a repo is Ingested, parse `spec.reingestSchedule` with `github.com/robfig/cron/v3`; compute next fire from base (`lastScheduledReingest | lastIngestTime | creationTimestamp`); if due and `now.After(lastIngestTime)` stamp the existing `tatara.dev/reingest-requested` annotation + set `status.lastScheduledReingest`; else `RequeueAfter = next-now` (clamped). Bad cron: log ERROR, skip. Do not break the webhook path.
  4. `deploy-samples/tatara-project.yaml`: add `reingestSchedule: "0 6 * * *"` to every Repository.

### Repo conventions (verified by reading the code)

- **Tests:** stdlib `testing`, plain `t.Run`/top-level `Test*` funcs. The `ingest` package uses pure unit tests (`strings.Contains` assertions on the built Job). The `controller` package uses **envtest** (`internal/controller/suite_test.go` boots one control plane; helpers `mkProject`, `mkRepo`, `getRepo`, `reconcileRepo`, `listIngestJobs`, `waitRepoJob`, `setProjectMemoryReady`, `findCond`). `testify` is present in `go.mod` but the operator's controller/ingest tests do **not** use it -- match that, use stdlib `t.Errorf`/`t.Fatalf`.
- **Annotation constant:** `tataradevv1alpha1.ReingestRequestedAnnotation = "tatara.dev/reingest-requested"` (in `api/v1alpha1/annotations.go`), aliased in the controller as `ReingestAnnotation`. The webhook stamps it with `time.Now().UTC().Format(time.RFC3339)` (`internal/webhook/server.go:133`). Match that exact format.
- **Logging:** controller uses `log.FromContext(ctx)` (logr). For the bad-cron ERROR, use `l.Error(err, "...", "key", val)`.
- **Make targets:** `make generate` (deepcopy via controller-gen), `make manifests` (CRD into `charts/tatara-operator/crds`), `make test` (envtest: `KUBEBUILDER_ASSETS=... go test ./... -race -count=1`), `make build`.
- **CRD validation is live in envtest.** `mkRepo` (repository_controller_test.go) and `mkTaskRepository` (task_controller_test.go) `Create` Repositories against the real API server; once `reingestSchedule` is Required they MUST set it or every controller test fails at create. The `webhook` and `restapi` tests use a **fake client** (no CRD validation) and are unaffected.

### Sequencing / dependencies

- Task 1 (namespacePath helper) and Task 2 (clone command) are in `internal/ingest`, pure unit tests, no CRD dependency -- do them first.
- Task 3 (CRD fields + regen) must precede Task 4 and Task 5 because the controller tests run against envtest with the regenerated CRD, and the new Required field forces the test-helper updates in Task 4.
- Task 5 (scheduler) depends on the new spec/status fields (Task 3) and the `robfig/cron/v3` dependency (Task 5 step 0).
- Task 6 (samples) is independent; do it last.

Run each task's tests with the envtest harness. The exact command for `internal/controller` tests is:

```
KUBEBUILDER_ASSETS="$(go run sigs.k8s.io/controller-runtime/tools/setup-envtest@release-0.21 use 1.33.0 -p path)" go test ./internal/controller/... -race -count=1 -run <RegExp>
```

For `internal/ingest` (no envtest needed):

```
go test ./internal/ingest/... -race -count=1 -run <RegExp>
```

---

## Task 1: `namespacePath` helper in `internal/ingest`

Maps a git clone URL to its on-disk subpath `owner[/subgroups]/repo`, dropping scheme, host, userinfo, and a trailing `.git`. Per the contract lock (`2026-06-09-phase0-contract-lock.md`), implement it tiny and independently (no shared module).

### Files
- **Create** `/Users/szymonri/Documents/tatara/tatara-operator/internal/ingest/namespace.go`
- **Create** `/Users/szymonri/Documents/tatara/tatara-operator/internal/ingest/namespace_test.go`

### Steps

**Step 1.1 -- Write the failing test (full code).**

Create `/Users/szymonri/Documents/tatara/tatara-operator/internal/ingest/namespace_test.go`:

```go
package ingest

import "testing"

func TestNamespacePath(t *testing.T) {
	tests := []struct {
		name string
		url  string
		want string
	}{
		{"https github with .git", "https://github.com/szymonrychu/tatara-cli.git", "szymonrychu/tatara-cli"},
		{"https github no .git", "https://github.com/szymonrychu/tatara-cli", "szymonrychu/tatara-cli"},
		{"https gitlab subgroups", "https://gitlab.com/szymonrychu/infra/helmfile", "szymonrychu/infra/helmfile"},
		{"scp-like git@", "git@github.com:szymonrychu/tatara-cli.git", "szymonrychu/tatara-cli"},
		{"ssh url with port", "ssh://git@host:22/group/sub/repo.git", "group/sub/repo"},
		{"trailing slash", "https://github.com/acme/widgets/", "acme/widgets"},
		{"https with userinfo", "https://x-access-token@github.com/acme/widgets.git", "acme/widgets"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := namespacePath(tt.url); got != tt.want {
				t.Errorf("namespacePath(%q) = %q, want %q", tt.url, got, tt.want)
			}
		})
	}
}
```

**Step 1.2 -- Run it, expect FAIL.**

```
go test ./internal/ingest/... -race -count=1 -run TestNamespacePath
```

Expected FAIL: `./namespace_test.go:... undefined: namespacePath` (build failure -- the function does not exist yet).

**Step 1.3 -- Minimal implementation (full code).**

Create `/Users/szymonri/Documents/tatara/tatara-operator/internal/ingest/namespace.go`:

```go
package ingest

import "strings"

// namespacePath maps a git clone URL to the on-disk subpath:
// owner[/subgroups]/repo, dropping scheme, host, userinfo, and a trailing
// ".git". Keeps the owner.
//
//	https://github.com/szymonrychu/tatara-cli.git   -> szymonrychu/tatara-cli
//	https://gitlab.com/szymonrychu/infra/helmfile    -> szymonrychu/infra/helmfile
//	git@github.com:szymonrychu/tatara-cli.git        -> szymonrychu/tatara-cli
//	ssh://git@host:22/group/sub/repo.git             -> group/sub/repo
func namespacePath(cloneURL string) string {
	s := strings.TrimSpace(cloneURL)

	// Drop scheme (https://, ssh://, git://, ...).
	if i := strings.Index(s, "://"); i >= 0 {
		s = s[i+3:]
	}

	// scp-like "git@host:owner/repo" has no scheme; split host from path on
	// the first ":" when there is no "/" before it.
	if !strings.Contains(s[:firstSlash(s)], "/") {
		if i := strings.Index(s, ":"); i >= 0 {
			s = s[:i] + "/" + s[i+1:]
		}
	}

	// Drop userinfo before the host ("git@host", "x-access-token@host").
	if i := strings.Index(s, "@"); i >= 0 && i < firstSlash(s) {
		s = s[i+1:]
	}

	// Drop the host segment (everything up to and including the first slash).
	if i := strings.Index(s, "/"); i >= 0 {
		s = s[i+1:]
	}

	s = strings.Trim(s, "/")
	s = strings.TrimSuffix(s, ".git")
	return s
}

// firstSlash returns the index of the first "/" in s, or len(s) when absent.
func firstSlash(s string) int {
	if i := strings.Index(s, "/"); i >= 0 {
		return i
	}
	return len(s)
}
```

**Step 1.4 -- Run it, expect PASS.**

```
go test ./internal/ingest/... -race -count=1 -run TestNamespacePath
```

Expected PASS: `ok  github.com/szymonrychu/tatara-operator/internal/ingest`.

**Step 1.5 -- Commit.**

```
git add internal/ingest/namespace.go internal/ingest/namespace_test.go
git commit -m "feat(ingest): add namespacePath helper for namespace-mirrored clone dir"
```

---

## Task 2: Full-history clone into namespace dir (`internal/ingest/job.go`)

Drop `--depth 1`, clone into `/workspace/<namespacePath(url)>`, and point `--repo-root` (and the `git -C` HEAD resolution) at that same dir. Apply both edits to the one `cloneCmd` together (per the layout notes).

### Files
- **Modify** `/Users/szymonri/Documents/tatara/tatara-operator/internal/ingest/job.go`
- **Modify** `/Users/szymonri/Documents/tatara/tatara-operator/internal/ingest/job_test.go`

### Steps

**Step 2.1 -- Write the failing test (full code).**

Add these two test functions to `/Users/szymonri/Documents/tatara/tatara-operator/internal/ingest/job_test.go` (append after `TestBuildJob_IncrementalIngest`):

```go
func TestBuildJob_FullHistoryClone(t *testing.T) {
	job := BuildJob(testProject(), testRepository(), "", testBaseURL, testConfig())
	clone := job.Spec.Template.Spec.InitContainers[0]
	cloneCmd := strings.Join(clone.Command, " ") + " " + strings.Join(clone.Args, " ")
	if strings.Contains(cloneCmd, "--depth") {
		t.Errorf("clone must be full history (no --depth): %q", cloneCmd)
	}
	if !strings.Contains(cloneCmd, "--branch main") {
		t.Errorf("clone cmd missing branch: %q", cloneCmd)
	}
}

func TestBuildJob_NamespaceCloneDir(t *testing.T) {
	job := BuildJob(testProject(), testRepository(), "", testBaseURL, testConfig())

	// widgets repo URL is https://github.com/acme/widgets.git -> acme/widgets
	const wantDir = "/workspace/acme/widgets"

	clone := job.Spec.Template.Spec.InitContainers[0]
	cloneCmd := strings.Join(clone.Command, " ") + " " + strings.Join(clone.Args, " ")
	if !strings.Contains(cloneCmd, wantDir) {
		t.Errorf("clone must target namespace dir %q: %q", wantDir, cloneCmd)
	}

	main := job.Spec.Template.Spec.Containers[0]
	cmd := strings.Join(main.Command, " ") + " " + strings.Join(main.Args, " ")
	if !strings.Contains(cmd, "--repo-root "+wantDir) {
		t.Errorf("ingest cmd must use namespace repo-root %q: %q", wantDir, cmd)
	}
	if !strings.Contains(cmd, "git -C "+wantDir+" rev-parse HEAD") {
		t.Errorf("HEAD resolution must run in namespace dir %q: %q", wantDir, cmd)
	}
}
```

Also update the existing `TestBuildJob_FullIngest` assertion that hard-codes `/workspace/repo` so it does not regress. In `TestBuildJob_FullIngest`, change the line:

```go
	if !strings.Contains(cmd, "tatara-ingest --repo-root /workspace/repo --repo-name widgets --base-url http://mem-acme.tatara.svc:8080") {
```

to:

```go
	if !strings.Contains(cmd, "tatara-ingest --repo-root /workspace/acme/widgets --repo-name widgets --base-url http://mem-acme.tatara.svc:8080") {
```

**Step 2.2 -- Run it, expect FAIL.**

```
go test ./internal/ingest/... -race -count=1 -run 'TestBuildJob_FullHistoryClone|TestBuildJob_NamespaceCloneDir|TestBuildJob_FullIngest'
```

Expected FAIL: `clone must be full history (no --depth)` and `clone must target namespace dir "/workspace/acme/widgets"` and `ingest cmd must use namespace repo-root "/workspace/acme/widgets"` (the current code clones `--depth 1` into the const `/workspace/repo`).

**Step 2.3 -- Minimal implementation (full code).**

Edit `/Users/szymonri/Documents/tatara/tatara-operator/internal/ingest/job.go`.

(a) Remove the now-unused `repoDir` const and keep `workspaceVolume`/`workspaceMount`. Change the const block from:

```go
const (
	workspaceVolume = "workspace"
	workspaceMount  = "/workspace"
	repoDir         = "/workspace/repo"
)
```

to:

```go
const (
	workspaceVolume = "workspace"
	workspaceMount  = "/workspace"
)
```

(b) Inside `BuildJob`, derive the per-repo clone dir and drop `--depth 1`. Replace the `cloneCmd`/`ingestArgs`/`mainScript` block:

```go
	// Use git credential helper to inject SCM_TOKEN without embedding it in
	// the URL string. The full URL appears literally in the command so tests
	// can assert on it; the token is supplied via SecretKeyRef env var.
	cloneCmd := fmt.Sprintf(
		`set -e; git -c "credential.helper=!f() { echo username=x-access-token; echo password=${SCM_TOKEN}; }; f" `+
			`clone --depth 1 --branch %s %s %s`,
		repo.Spec.DefaultBranch, repo.Spec.URL, repoDir)

	ingestArgs := fmt.Sprintf(
		"tatara-ingest --repo-root %s --repo-name %s --base-url %s",
		repoDir, repo.Name, baseURL)
	if since != "" {
		ingestArgs += " --since " + since
	}
	// After a successful ingest, resolve HEAD and patch the result ConfigMap
	// via the in-cluster API (the Job ServiceAccount has patch on it).
	resultCM := ResultConfigMapName(repo)
	mainScript := fmt.Sprintf(
		"set -e; %s; "+
			"SHA=$(git -C %s rev-parse HEAD); "+
			"kubectl -n %s patch configmap %s --type merge "+
			"-p \"{\\\"data\\\":{\\\"sha\\\":\\\"${SHA}\\\"}}\"",
		ingestArgs, repoDir, cfg.Namespace, resultCM)
```

with:

```go
	// Clone into a directory that mirrors the repo namespace (owner/.../repo),
	// not a flat "/workspace/repo", so concurrent clones never collide.
	repoDir := workspaceMount + "/" + namespacePath(repo.Spec.URL)

	// Use git credential helper to inject SCM_TOKEN without embedding it in
	// the URL string. The full URL appears literally in the command so tests
	// can assert on it; the token is supplied via SecretKeyRef env var.
	// Full-history clone (no --depth): the incremental diff needs <since> in
	// history, and a shallow clone exits 128 when <since> is absent.
	cloneCmd := fmt.Sprintf(
		`set -e; git -c "credential.helper=!f() { echo username=x-access-token; echo password=${SCM_TOKEN}; }; f" `+
			`clone --branch %s %s %s`,
		repo.Spec.DefaultBranch, repo.Spec.URL, repoDir)

	ingestArgs := fmt.Sprintf(
		"tatara-ingest --repo-root %s --repo-name %s --base-url %s",
		repoDir, repo.Name, baseURL)
	if since != "" {
		ingestArgs += " --since " + since
	}
	// After a successful ingest, resolve HEAD and patch the result ConfigMap
	// via the in-cluster API (the Job ServiceAccount has patch on it).
	resultCM := ResultConfigMapName(repo)
	mainScript := fmt.Sprintf(
		"set -e; %s; "+
			"SHA=$(git -C %s rev-parse HEAD); "+
			"kubectl -n %s patch configmap %s --type merge "+
			"-p \"{\\\"data\\\":{\\\"sha\\\":\\\"${SHA}\\\"}}\"",
		ingestArgs, repoDir, cfg.Namespace, resultCM)
```

**Step 2.4 -- Run it, expect PASS.**

```
go test ./internal/ingest/... -race -count=1
```

Expected PASS: `ok  github.com/szymonrychu/tatara-operator/internal/ingest` (all `TestBuildJob_*` pass, including the updated `TestBuildJob_FullIngest`).

**Step 2.5 -- Commit.**

```
git add internal/ingest/job.go internal/ingest/job_test.go
git commit -m "feat(ingest): full-history clone into namespace-mirrored workspace dir"
```

---

## Task 3: CRD fields -- `Spec.ReingestSchedule` (required) + `Status.LastScheduledReingest`

Add the API fields, then regenerate deepcopy and CRD. The new spec field is Required with a cron-shaped pattern.

### Files
- **Modify** `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/repository_types.go`
- **Modify** (regenerated) `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/zz_generated.deepcopy.go`
- **Modify** (regenerated) `/Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator/crds/tatara.dev_repositories.yaml`
- **Create** `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/repository_types_test.go`

### Steps

**Step 3.1 -- Write the failing test (full code).**

Create `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/repository_types_test.go`. This is a pure-Go test asserting the new fields exist, deepcopy works, and the JSON tags are correct (no envtest needed):

```go
package v1alpha1

import (
	"testing"
	"time"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func TestRepositorySpec_ReingestScheduleField(t *testing.T) {
	r := &Repository{}
	r.Spec.ReingestSchedule = "0 6 * * *"
	if r.Spec.ReingestSchedule != "0 6 * * *" {
		t.Fatalf("ReingestSchedule = %q, want %q", r.Spec.ReingestSchedule, "0 6 * * *")
	}
}

func TestRepositoryStatus_LastScheduledReingestField(t *testing.T) {
	r := &Repository{}
	now := metav1.NewTime(time.Now())
	r.Status.LastScheduledReingest = &now
	if r.Status.LastScheduledReingest == nil {
		t.Fatal("LastScheduledReingest should round-trip a *metav1.Time")
	}
}

func TestRepository_DeepCopyCopiesLastScheduledReingest(t *testing.T) {
	now := metav1.NewTime(time.Now())
	r := &Repository{}
	r.Spec.ReingestSchedule = "0 6 * * *"
	r.Status.LastScheduledReingest = &now

	cp := r.DeepCopy()
	if cp.Spec.ReingestSchedule != "0 6 * * *" {
		t.Errorf("deepcopy lost ReingestSchedule: %q", cp.Spec.ReingestSchedule)
	}
	if cp.Status.LastScheduledReingest == nil {
		t.Fatal("deepcopy lost LastScheduledReingest")
	}
	// Must be a distinct pointer (deep, not shallow).
	if cp.Status.LastScheduledReingest == r.Status.LastScheduledReingest {
		t.Error("deepcopy must allocate a new LastScheduledReingest pointer")
	}
}
```

**Step 3.2 -- Run it, expect FAIL.**

```
go test ./api/v1alpha1/... -race -count=1
```

Expected FAIL: build error `r.Spec.ReingestSchedule undefined (type RepositorySpec has no field or method ReingestSchedule)` and `r.Status.LastScheduledReingest undefined`.

**Step 3.3 -- Minimal implementation: add the fields (full code).**

Edit `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/repository_types.go`.

Replace the `RepositorySpec` struct:

```go
// RepositorySpec defines the desired state of a Repository.
type RepositorySpec struct {
	ProjectRef string `json:"projectRef"`
	URL        string `json:"url"`
	// +kubebuilder:default="main"
	// +optional
	DefaultBranch string `json:"defaultBranch,omitempty"`
	// +kubebuilder:default=true
	// +optional
	IngestEnabled bool `json:"ingestEnabled,omitempty"`
}
```

with:

```go
// RepositorySpec defines the desired state of a Repository.
type RepositorySpec struct {
	ProjectRef string `json:"projectRef"`
	URL        string `json:"url"`
	// +kubebuilder:default="main"
	// +optional
	DefaultBranch string `json:"defaultBranch,omitempty"`
	// +kubebuilder:default=true
	// +optional
	IngestEnabled bool `json:"ingestEnabled,omitempty"`
	// ReingestSchedule is a standard 5-field cron expression (e.g. "0 6 * * *")
	// that triggers a periodic catch-up re-ingest in addition to push webhooks.
	// +kubebuilder:validation:Required
	// +kubebuilder:validation:MinLength=9
	// +kubebuilder:validation:Pattern=`^(\S+\s+){4}\S+$`
	ReingestSchedule string `json:"reingestSchedule"`
}
```

Replace the `RepositoryStatus` struct by adding the new status field after `LastIngestTime`:

```go
// RepositoryStatus defines the observed state of a Repository.
type RepositoryStatus struct {
	// +kubebuilder:validation:Enum=Pending;Ingesting;Ingested;Failed
	// +optional
	Phase string `json:"phase,omitempty"`
	// +optional
	LastIngestedCommit string `json:"lastIngestedCommit,omitempty"`
	// +optional
	LastIngestTime *metav1.Time `json:"lastIngestTime,omitempty"`
	// LastScheduledReingest is the last time the cron schedule stamped a
	// reingest-requested annotation; used as the base for the next fire.
	// +optional
	LastScheduledReingest *metav1.Time `json:"lastScheduledReingest,omitempty"`
	// +optional
	JobName string `json:"jobName,omitempty"`
	// +optional
	// +listType=map
	// +listMapKey=type
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}
```

**Step 3.4 -- Regenerate deepcopy + CRD.**

```
make generate
make manifests
```

Expected: `make generate` updates `api/v1alpha1/zz_generated.deepcopy.go` so `RepositoryStatus.DeepCopyInto` now also deep-copies `LastScheduledReingest` (mirroring the existing `LastIngestTime` block). `make manifests` adds `reingestSchedule` (with `minLength: 9`, the pattern, and a `required: [projectRef, reingestSchedule, url]` list) to `charts/tatara-operator/crds/tatara.dev_repositories.yaml`, plus `lastScheduledReingest` (`format: date-time`) under `status`.

Verify the regen actually happened (read-only check):

```
git diff --stat api/v1alpha1/zz_generated.deepcopy.go charts/tatara-operator/crds/tatara.dev_repositories.yaml
```

Expected: both files show as modified.

**Step 3.5 -- Run the API test, expect PASS.**

```
go test ./api/v1alpha1/... -race -count=1
```

Expected PASS: `ok  github.com/szymonrychu/tatara-operator/api/v1alpha1` (all three new tests pass; deepcopy now allocates a distinct `LastScheduledReingest` pointer).

**Step 3.6 -- Commit.**

```
git add api/v1alpha1/repository_types.go api/v1alpha1/repository_types_test.go api/v1alpha1/zz_generated.deepcopy.go charts/tatara-operator/crds/tatara.dev_repositories.yaml
git commit -m "feat(api): add required Repository.spec.reingestSchedule and status.lastScheduledReingest"
```

---

## Task 4: Make controller envtest helpers set the now-Required `reingestSchedule`

The Required field added in Task 3 makes envtest reject every `Repository` create that omits it. `mkRepo` (repository_controller_test.go) and `mkTaskRepository` (task_controller_test.go) both `Create` against the real API server. Update them so the existing controller test suite keeps creating valid objects. This is a pure test-infra change; its "test" is the existing suite passing again.

### Files
- **Modify** `/Users/szymonri/Documents/tatara/tatara-operator/internal/controller/repository_controller_test.go`
- **Modify** `/Users/szymonri/Documents/tatara/tatara-operator/internal/controller/task_controller_test.go`

### Steps

**Step 4.1 -- Run the existing controller suite to observe the breakage (expect FAIL).**

```
KUBEBUILDER_ASSETS="$(go run sigs.k8s.io/controller-runtime/tools/setup-envtest@release-0.21 use 1.33.0 -p path)" go test ./internal/controller/... -race -count=1 -run 'TestRepoReconcile|TestTask'
```

Expected FAIL: creates now fail with an admission error like `create repo ...: Repository.tatara.dev "..." is invalid: spec.reingestSchedule: Required value` (from `mkRepo`/`mkTaskRepository`).

**Step 4.2 -- Fix `mkRepo` (full edited helper).**

In `/Users/szymonri/Documents/tatara/tatara-operator/internal/controller/repository_controller_test.go`, replace the `mkRepo` helper:

```go
func mkRepo(t *testing.T, name, projectRef string) *tataradevv1alpha1.Repository {
	t.Helper()
	r := &tataradevv1alpha1.Repository{}
	r.Name = name
	r.Namespace = testNS
	r.Spec.ProjectRef = projectRef
	r.Spec.URL = "https://github.com/acme/" + name + ".git"
	r.Spec.DefaultBranch = "main"
	r.Spec.IngestEnabled = true
	if err := k8sClient.Create(context.Background(), r); err != nil {
		t.Fatalf("create repo %s: %v", name, err)
	}
	return r
}
```

with (add the `ReingestSchedule`):

```go
func mkRepo(t *testing.T, name, projectRef string) *tataradevv1alpha1.Repository {
	t.Helper()
	r := &tataradevv1alpha1.Repository{}
	r.Name = name
	r.Namespace = testNS
	r.Spec.ProjectRef = projectRef
	r.Spec.URL = "https://github.com/acme/" + name + ".git"
	r.Spec.DefaultBranch = "main"
	r.Spec.IngestEnabled = true
	r.Spec.ReingestSchedule = "0 6 * * *"
	if err := k8sClient.Create(context.Background(), r); err != nil {
		t.Fatalf("create repo %s: %v", name, err)
	}
	return r
}
```

**Step 4.3 -- Fix `mkTaskRepository` (full edited helper).**

In `/Users/szymonri/Documents/tatara/tatara-operator/internal/controller/task_controller_test.go`, replace:

```go
func mkTaskRepository(t *testing.T, name, projectRef string) {
	t.Helper()
	r := &tatarav1alpha1.Repository{}
	r.Name = name
	r.Namespace = testNS
	r.Spec.ProjectRef = projectRef
	r.Spec.URL = "https://git/acme/" + name
	r.Spec.DefaultBranch = "main"
	if err := k8sClient.Create(context.Background(), r); err != nil {
		t.Fatalf("create repository: %v", err)
	}
}
```

with:

```go
func mkTaskRepository(t *testing.T, name, projectRef string) {
	t.Helper()
	r := &tatarav1alpha1.Repository{}
	r.Name = name
	r.Namespace = testNS
	r.Spec.ProjectRef = projectRef
	r.Spec.URL = "https://git/acme/" + name
	r.Spec.DefaultBranch = "main"
	r.Spec.ReingestSchedule = "0 6 * * *"
	if err := k8sClient.Create(context.Background(), r); err != nil {
		t.Fatalf("create repository: %v", err)
	}
}
```

**Step 4.4 -- Run the suite again, expect PASS.**

```
KUBEBUILDER_ASSETS="$(go run sigs.k8s.io/controller-runtime/tools/setup-envtest@release-0.21 use 1.33.0 -p path)" go test ./internal/controller/... -race -count=1 -run 'TestRepoReconcile|TestTask'
```

Expected PASS: `ok  github.com/szymonrychu/tatara-operator/internal/controller` (creates succeed again).

**Step 4.5 -- Commit.**

```
git add internal/controller/repository_controller_test.go internal/controller/task_controller_test.go
git commit -m "test(controller): set required reingestSchedule in envtest repo helpers"
```

---

## Task 5: Per-Repository cron scheduler in the reconciler

Add the `github.com/robfig/cron/v3` dependency, then a `scheduleNextReingest` step run at the end of `Reconcile` for already-Ingested repos. It parses `spec.reingestSchedule`, computes the next fire from `base = lastScheduledReingest | lastIngestTime | creationTimestamp`, and either stamps the existing annotation (when due and `now.After(lastIngestTime)`) or returns `RequeueAfter = next-now` clamped to 6h. Bad cron logs ERROR and skips (no requeue, no crash). The webhook path is untouched: the scheduler only stamps the same annotation `ingestDecision` already consumes.

### Files
- **Modify** `/Users/szymonri/Documents/tatara/tatara-operator/go.mod` (and `go.sum`, via `go get`)
- **Modify** `/Users/szymonri/Documents/tatara/tatara-operator/internal/controller/repository_controller.go`
- **Modify** `/Users/szymonri/Documents/tatara/tatara-operator/internal/controller/repository_controller_test.go`

### Steps

**Step 5.0 -- Add the dependency (this changes state via `go get`; it is required and explicitly in scope).**

```
go get github.com/robfig/cron/v3@v3.0.1
```

Expected: `go.mod` gains `github.com/robfig/cron/v3 v3.0.1` in the `require` block and `go.sum` gains the matching hashes. Verify:

```
grep robfig go.mod
```

Expected output: `	github.com/robfig/cron/v3 v3.0.1`.

**Step 5.1 -- Write the failing tests (full code).**

Append to `/Users/szymonri/Documents/tatara/tatara-operator/internal/controller/repository_controller_test.go`. These use the existing envtest harness and helpers. They cover: due -> stamps annotation + sets `lastScheduledReingest`; not-yet-due -> `RequeueAfter` set, no stamp; bad cron -> no stamp, no requeue, no error; `lastScheduledReingest` prevents double-fire within one interval.

```go
func setRepoIngested(t *testing.T, name, sha string, lastIngest time.Time) {
	t.Helper()
	r := getRepo(t, name)
	r.Status.LastIngestedCommit = sha
	lt := metav1.NewTime(lastIngest)
	r.Status.LastIngestTime = &lt
	r.Status.Phase = "Ingested"
	if err := k8sClient.Status().Update(context.Background(), r); err != nil {
		t.Fatalf("seed ingested status for %s: %v", name, err)
	}
}

func TestRepoReconcile_ScheduleStampsAnnotationWhenDue(t *testing.T) {
	mkProject(t, "rp-sch1", "rp-sch1-scm")
	mkSecret(t, "rp-sch1-scm", map[string][]byte{"token": []byte("x"), "webhookSecret": []byte("y")})
	mkRepo(t, "sch1", "rp-sch1")
	setProjectMemoryReady(t, "rp-sch1", "http://mem-rp-sch1.tatara.svc:8080")

	// Schedule fires every minute; last ingest was an hour ago, so it is due now.
	r := getRepo(t, "sch1")
	r.Spec.ReingestSchedule = "* * * * *"
	if err := k8sClient.Update(context.Background(), r); err != nil {
		t.Fatalf("set schedule: %v", err)
	}
	setRepoIngested(t, "sch1", "shaSch1", time.Now().Add(-1*time.Hour))

	if _, err := reconcileRepo(t, "sch1"); err != nil {
		t.Fatalf("reconcile: %v", err)
	}

	got := getRepo(t, "sch1")
	if got.Annotations[ReingestAnnotation] == "" {
		t.Fatal("due schedule must stamp the reingest-requested annotation")
	}
	if got.Status.LastScheduledReingest == nil {
		t.Fatal("due schedule must set status.lastScheduledReingest")
	}
	// No Job yet: the annotation re-triggers reconcile via the watch; this
	// reconcile pass only stamps.
	if jobs := listIngestJobs(t, "sch1"); len(jobs) != 0 {
		t.Fatalf("schedule stamp pass must not itself launch a job, got %d", len(jobs))
	}
}

func TestRepoReconcile_ScheduleRequeuesWhenNotDue(t *testing.T) {
	mkProject(t, "rp-sch2", "rp-sch2-scm")
	mkSecret(t, "rp-sch2-scm", map[string][]byte{"token": []byte("x"), "webhookSecret": []byte("y")})
	mkRepo(t, "sch2", "rp-sch2")
	setProjectMemoryReady(t, "rp-sch2", "http://mem-rp-sch2.tatara.svc:8080")

	// Far-future daily schedule + a fresh ingest => not due; expect a requeue.
	r := getRepo(t, "sch2")
	r.Spec.ReingestSchedule = "0 6 * * *"
	if err := k8sClient.Update(context.Background(), r); err != nil {
		t.Fatalf("set schedule: %v", err)
	}
	setRepoIngested(t, "sch2", "shaSch2", time.Now())

	res, err := reconcileRepo(t, "sch2")
	if err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	if res.RequeueAfter <= 0 {
		t.Errorf("not-due schedule must set RequeueAfter, got %v", res.RequeueAfter)
	}
	if res.RequeueAfter > 6*time.Hour {
		t.Errorf("RequeueAfter must be clamped to 6h, got %v", res.RequeueAfter)
	}
	if getRepo(t, "sch2").Annotations[ReingestAnnotation] != "" {
		t.Error("not-due schedule must not stamp the annotation")
	}
	if getRepo(t, "sch2").Status.LastScheduledReingest != nil {
		t.Error("not-due schedule must not set lastScheduledReingest")
	}
}

func TestRepoReconcile_ScheduleBadCronSkips(t *testing.T) {
	mkProject(t, "rp-sch3", "rp-sch3-scm")
	mkSecret(t, "rp-sch3-scm", map[string][]byte{"token": []byte("x"), "webhookSecret": []byte("y")})
	mkRepo(t, "sch3", "rp-sch3")
	setProjectMemoryReady(t, "rp-sch3", "http://mem-rp-sch3.tatara.svc:8080")

	// A syntactically-shaped but semantically invalid cron (bad minute field).
	r := getRepo(t, "sch3")
	r.Spec.ReingestSchedule = "99 6 * * *"
	if err := k8sClient.Update(context.Background(), r); err != nil {
		t.Fatalf("set schedule: %v", err)
	}
	setRepoIngested(t, "sch3", "shaSch3", time.Now().Add(-1*time.Hour))

	res, err := reconcileRepo(t, "sch3")
	if err != nil {
		t.Fatalf("bad cron must not error the reconcile: %v", err)
	}
	if res.RequeueAfter != 0 {
		t.Errorf("bad cron must skip scheduling (no requeue), got %v", res.RequeueAfter)
	}
	if getRepo(t, "sch3").Annotations[ReingestAnnotation] != "" {
		t.Error("bad cron must not stamp the annotation")
	}
}

func TestRepoReconcile_ScheduleNoDoubleFireWithinInterval(t *testing.T) {
	mkProject(t, "rp-sch4", "rp-sch4-scm")
	mkSecret(t, "rp-sch4-scm", map[string][]byte{"token": []byte("x"), "webhookSecret": []byte("y")})
	mkRepo(t, "sch4", "rp-sch4")
	setProjectMemoryReady(t, "rp-sch4", "http://mem-rp-sch4.tatara.svc:8080")

	r := getRepo(t, "sch4")
	r.Spec.ReingestSchedule = "0 6 * * *"
	if err := k8sClient.Update(context.Background(), r); err != nil {
		t.Fatalf("set schedule: %v", err)
	}
	setRepoIngested(t, "sch4", "shaSch4", time.Now().Add(-25*time.Hour))

	// lastScheduledReingest is recent => next fire is in the future => not due,
	// even though lastIngestTime is old. Guards against double-fire.
	r = getRepo(t, "sch4")
	just := metav1.NewTime(time.Now().Add(-1 * time.Minute))
	r.Status.LastScheduledReingest = &just
	if err := k8sClient.Status().Update(context.Background(), r); err != nil {
		t.Fatalf("seed lastScheduledReingest: %v", err)
	}

	res, err := reconcileRepo(t, "sch4")
	if err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	if getRepo(t, "sch4").Annotations[ReingestAnnotation] != "" {
		t.Error("recent lastScheduledReingest must prevent a second stamp this interval")
	}
	if res.RequeueAfter <= 0 {
		t.Errorf("expected a requeue to the next fire, got %v", res.RequeueAfter)
	}
}
```

**Step 5.2 -- Run them, expect FAIL.**

```
KUBEBUILDER_ASSETS="$(go run sigs.k8s.io/controller-runtime/tools/setup-envtest@release-0.21 use 1.33.0 -p path)" go test ./internal/controller/... -race -count=1 -run 'TestRepoReconcile_Schedule'
```

Expected FAIL: `TestRepoReconcile_ScheduleStampsAnnotationWhenDue` fails -- the annotation is empty and `LastScheduledReingest` is nil because the reconciler has no scheduling step yet. (`TestRepoReconcile_ScheduleRequeuesWhenNotDue` may currently pass trivially since `RequeueAfter` is 0; that is acceptable -- the suite as a whole is red.)

**Step 5.3 -- Minimal implementation (full code).**

Edit `/Users/szymonri/Documents/tatara/tatara-operator/internal/controller/repository_controller.go`.

(a) Add the import. Change the import block to include `cron`:

```go
import (
	"context"
	"fmt"
	"time"

	"github.com/robfig/cron/v3"
	tataradevv1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/ingest"
	"github.com/szymonrychu/tatara-operator/internal/obs"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/log"
)
```

(b) Add a clamp const near the top (after `ReingestAnnotation`):

```go
// maxScheduleRequeue bounds the cron requeue so clock skew or long sleeps still
// re-evaluate the schedule reasonably soon.
const maxScheduleRequeue = 6 * time.Hour
```

(c) Replace the post-`ingestDecision` "no ingest wanted" early return so it runs the scheduler instead of returning bare. Currently:

```go
	since, want := r.ingestDecision(&repo)
	if !want {
		r.Metrics.ReconcileResult("Repository", "success")
		return ctrl.Result{}, nil
	}
```

becomes:

```go
	since, want := r.ingestDecision(&repo)
	if !want {
		res, err := r.scheduleNextReingest(ctx, &repo)
		if err != nil {
			r.Metrics.ReconcileResult("Repository", "error")
			return ctrl.Result{}, err
		}
		r.Metrics.ReconcileResult("Repository", "success")
		return res, nil
	}
```

(d) Add the scheduler method. Append after `ingestDecision`:

```go
// scheduleNextReingest applies the per-Repository cron schedule for an
// already-ingested repo. It parses spec.reingestSchedule and computes the next
// fire from base = lastScheduledReingest | lastIngestTime | creationTimestamp.
// When the fire is due (and strictly after lastIngestTime, so an in-flight
// ingest from another trigger is not double-stamped), it stamps the existing
// reingest-requested annotation and records lastScheduledReingest; the
// annotation change re-triggers reconcile, which launches the Job via the
// existing path. Otherwise it requeues at the next fire (clamped). A bad cron
// expression is logged at ERROR and skipped (no requeue, no error).
func (r *RepositoryReconciler) scheduleNextReingest(ctx context.Context, repo *tataradevv1alpha1.Repository) (ctrl.Result, error) {
	l := log.FromContext(ctx)

	// Only schedule once a repo has been ingested at least once; a first full
	// ingest is driven by ingestDecision, not the cron.
	if repo.Status.LastIngestedCommit == "" || repo.Spec.ReingestSchedule == "" {
		return ctrl.Result{}, nil
	}

	schedule, err := cron.ParseStandard(repo.Spec.ReingestSchedule)
	if err != nil {
		l.Error(err, "invalid reingestSchedule, skipping cron",
			"action", "ingest_schedule_invalid", "resource_id", repo.Name,
			"schedule", repo.Spec.ReingestSchedule)
		return ctrl.Result{}, nil
	}

	var lastIngestTime time.Time
	if repo.Status.LastIngestTime != nil {
		lastIngestTime = repo.Status.LastIngestTime.Time
	}

	base := repo.CreationTimestamp.Time
	if repo.Status.LastIngestTime != nil {
		base = repo.Status.LastIngestTime.Time
	}
	if repo.Status.LastScheduledReingest != nil {
		base = repo.Status.LastScheduledReingest.Time
	}

	now := time.Now()
	next := schedule.Next(base)

	if now.Before(next) {
		requeue := next.Sub(now)
		if requeue > maxScheduleRequeue {
			requeue = maxScheduleRequeue
		}
		return ctrl.Result{RequeueAfter: requeue}, nil
	}

	// Due. Guard against firing while an ingest from another trigger is still
	// in flight or just finished: only stamp when now is strictly after the
	// last successful ingest.
	if !now.After(lastIngestTime) {
		return ctrl.Result{RequeueAfter: maxScheduleRequeue}, nil
	}

	if repo.Annotations == nil {
		repo.Annotations = map[string]string{}
	}
	repo.Annotations[ReingestAnnotation] = now.UTC().Format(time.RFC3339)
	if err := r.Update(ctx, repo); err != nil {
		return ctrl.Result{}, fmt.Errorf("stamp scheduled reingest annotation: %w", err)
	}

	scheduled := metav1.NewTime(now)
	repo.Status.LastScheduledReingest = &scheduled
	if err := r.Status().Update(ctx, repo); err != nil {
		return ctrl.Result{}, fmt.Errorf("update lastScheduledReingest: %w", err)
	}

	l.Info("scheduled re-ingest requested",
		"action", "ingest_schedule_fire", "resource_id", repo.Name,
		"schedule", repo.Spec.ReingestSchedule)
	return ctrl.Result{}, nil
}
```

Notes for the implementer: stamp the annotation with `r.Update` (spec/metadata write) BEFORE the status write, matching the webhook's `Update` ordering and the existing `ingestDecision` annotation contract. The metadata `Update` bumps the object so the subsequent `Status().Update` operates on the returned object's resourceVersion in-place (controller-runtime mutates `repo` on `Update`). The annotation re-triggers reconcile via the existing `For(&Repository{})` watch; that next pass goes through `ingestDecision`, sees `requested.After(lastIngestTime)`, and launches the incremental Job -- the webhook path is wholly reused and untouched.

**Step 5.4 -- Run them, expect PASS.**

```
KUBEBUILDER_ASSETS="$(go run sigs.k8s.io/controller-runtime/tools/setup-envtest@release-0.21 use 1.33.0 -p path)" go test ./internal/controller/... -race -count=1 -run 'TestRepoReconcile'
```

Expected PASS: `ok  github.com/szymonrychu/tatara-operator/internal/controller` (all four new `TestRepoReconcile_Schedule*` pass and the existing `TestRepoReconcile_*` still pass -- the not-due/stale-annotation cases now also exercise the requeue branch without launching jobs).

**Step 5.5 -- Commit.**

```
git add go.mod go.sum internal/controller/repository_controller.go internal/controller/repository_controller_test.go
git commit -m "feat(controller): per-Repository cron schedule stamps reingest annotation"
```

---

## Task 6: Add `reingestSchedule` to every sample Repository

The new field is Required, so the samples must carry it. Add `reingestSchedule: "0 6 * * *"` to all six `Repository` entries.

### Files
- **Modify** `/Users/szymonri/Documents/tatara/tatara-operator/deploy-samples/tatara-project.yaml`

### Steps

**Step 6.1 -- Edit the samples (full intended content of each Repository block).**

In `/Users/szymonri/Documents/tatara/tatara-operator/deploy-samples/tatara-project.yaml`, add `reingestSchedule: "0 6 * * *"` to each Repository's inline `spec` map. The six `spec` lines become:

```yaml
spec: {projectRef: tatara, url: "https://github.com/szymonrychu/tatara-memory", defaultBranch: main, reingestSchedule: "0 6 * * *"}
```
```yaml
spec: {projectRef: tatara, url: "https://github.com/szymonrychu/tatara-cli", defaultBranch: main, reingestSchedule: "0 6 * * *"}
```
```yaml
spec: {projectRef: tatara, url: "https://github.com/szymonrychu/tatara-operator", defaultBranch: main, reingestSchedule: "0 6 * * *"}
```
```yaml
spec: {projectRef: tatara, url: "https://github.com/szymonrychu/tatara-chat", defaultBranch: main, reingestSchedule: "0 6 * * *"}
```
```yaml
spec: {projectRef: tatara, url: "https://github.com/szymonrychu/tatara-memory-repo-ingester", defaultBranch: main, reingestSchedule: "0 6 * * *"}
```
```yaml
spec: {projectRef: tatara, url: "https://github.com/szymonrychu/tatara-claude-code-wrapper", defaultBranch: main, reingestSchedule: "0 6 * * *"}
```

(The `Project` block at the top is unchanged.)

**Step 6.2 -- Validate the samples against the regenerated CRD (read-only dry-run; no cluster mutation).**

If a cluster/kubeconfig is available:

```
kubectl apply --dry-run=client -f deploy-samples/tatara-project.yaml
```

Expected: `repository.tatara.dev/tatara-memory configured (dry run)` etc., with no `spec.reingestSchedule: Required value` error. If no cluster is reachable, instead grep-verify all six entries carry the field:

```
grep -c 'reingestSchedule: "0 6 \* \* \*"' deploy-samples/tatara-project.yaml
```

Expected: `6`.

**Step 6.3 -- Commit.**

```
git add deploy-samples/tatara-project.yaml
git commit -m "chore(samples): set reingestSchedule on every sample Repository"
```

---

## Final verification (whole-repo gate)

After all tasks, run the full quality gate the repo's `make ci` uses, to confirm nothing regressed:

```
make generate
make manifests
git diff --exit-code api/v1alpha1/zz_generated.deepcopy.go charts/tatara-operator/crds/tatara.dev_repositories.yaml
make fmt
make lint
make test
make build
```

Expected:
- `git diff --exit-code` on the generated files is clean (regen is idempotent; if it is not, the committed CRD/deepcopy was stale -- re-commit).
- `make lint` passes (or exits 5 = "no issues", which the Makefile treats as success).
- `make test` -> `ok` for `./api/v1alpha1/...`, `./internal/ingest/...`, `./internal/controller/...`, and all other packages (webhook/restapi unaffected -- they use the fake client and never set the Required field).
- `make build` produces `bin/tatara-operator`.

If `make test` reaches the `internal/webhook` or `internal/restapi` packages and they fail on the new Required field, that means a fake-client test there started enforcing CRD schema (it does not today) -- recheck, but no change is expected.

---

## Notes carried from the specs (do not lose)

- The memory `repo` LABEL stays the logical identifier (Repository CR name); only the clone DIRECTORY mirrors the namespace. No re-keying. (`2026-06-09-phase0-contract-lock.md`, `2026-06-09-namespace-clone-layout-notes.md`.)
- The clone keeps the existing inline credential helper that injects `SCM_TOKEN`; no other Job-spec change. (`2026-06-09-incremental-reingest-design.md` 1a.)
- The scheduler adds no CronJob resource, no new RBAC, no new image -- it only reuses the existing webhook re-ingest mechanism with a time-based stamper. (`2026-06-09-incremental-reingest-design.md` 1b.)
- Out of operator scope here (other repos / later MR): the live-CR patch of `reingestSchedule` on the 6 running Repositories, the ingester `walk`/`push` changes, the memory migrations, and chart/image version bumps. This plan covers only the four operator code/CRD/sample edits.

### Critical Files for Implementation
- /Users/szymonri/Documents/tatara/tatara-operator/internal/ingest/job.go
- /Users/szymonri/Documents/tatara/tatara-operator/internal/controller/repository_controller.go
- /Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/repository_types.go
- /Users/szymonri/Documents/tatara/tatara-operator/internal/controller/repository_controller_test.go
- /Users/szymonri/Documents/tatara/tatara-operator/Makefile