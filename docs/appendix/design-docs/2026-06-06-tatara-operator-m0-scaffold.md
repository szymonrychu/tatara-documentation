# tatara-operator M0 (scaffold) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `tatara-operator` repo skeleton: kubebuilder/controller-runtime project, the four CRD API types with generated deepcopy, shared `internal/{obs,auth,config}` packages, a no-reconciler Manager binary, Dockerfile, Makefile, and a `helm lint`-clean chart skeleton with CRDs.

**Architecture:** One Go binary built on controller-runtime. M0 wires only the cross-cutting foundations that every later milestone (M1-M6) depends on: the `tatara.dev/v1alpha1` API group (Project/Repository/Task/Subtask), JSON `slog` + Prometheus obs, OIDC Verifier + client-credentials TokenSource, env-scalar config, a Manager that exposes `/healthz` and `/metrics` with zero reconcilers, and a cluster-agnostic chart carrying the CRDs. No reconciler, webhook, REST, or agent logic ships in M0.

**Tech Stack:** Go 1.25.x, `sigs.k8s.io/controller-runtime`, `sigs.k8s.io/controller-tools` (controller-gen), `setup-envtest`, `github.com/coreos/go-oidc/v3`, `golang.org/x/oauth2/clientcredentials`, `github.com/prometheus/client_golang`, `github.com/stretchr/testify`, Helm 3/4, distroless static base image.

---

## Conventions used throughout this plan

- All paths are relative to the repo root `/Users/szymonri/Documents/tatara/tatara-operator` unless absolute.
- Module path: `github.com/szymonrychu/tatara-operator`.
- Run Go commands from the repo root.
- Conventional commits. Branch flow per CLAUDE.md rule 10: this M0 work happens in a worktree off `main`, merged back to `main`; never build/deploy from a worktree. Each task below ends in a commit on the M0 branch.
- Mirror sibling style from `tatara-chat` (`internal/auth`, `internal/obs`) and `tatara-memory-repo-ingester` (`internal/push/auth.go` client-credentials).

---

### Task 1: Repo creation and canonical project files

**Files:**
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/CLAUDE.md` (copied from parent)
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/LICENSE` (AGPLv3, copied from sibling)
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/README.md`
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/MEMORY.md`
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/ROADMAP.md`
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/.gitignore`

This task is scaffolding (not test-first): create the directory, git-init, drop the canonical files, make the first commit, and wire the GitHub remote.

- [ ] **Step 1: Create the directory and init git**

Run:
```bash
mkdir -p /Users/szymonri/Documents/tatara/tatara-operator
git -C /Users/szymonri/Documents/tatara/tatara-operator init -b main
```
Expected: `Initialized empty Git repository in /Users/szymonri/Documents/tatara/tatara-operator/.git/`

- [ ] **Step 2: Copy the canonical CLAUDE.md and AGPLv3 LICENSE from the parent / a sibling**

Run:
```bash
cp /Users/szymonri/Documents/tatara/CLAUDE.md /Users/szymonri/Documents/tatara/tatara-operator/CLAUDE.md
cp /Users/szymonri/Documents/tatara/tatara-memory-repo-ingester/LICENSE /Users/szymonri/Documents/tatara/tatara-operator/LICENSE
```
Verify:
```bash
head -1 /Users/szymonri/Documents/tatara/tatara-operator/CLAUDE.md
head -2 /Users/szymonri/Documents/tatara/tatara-operator/LICENSE
```
Expected:
```
# CLAUDE.md - tatara
                    GNU AFFERO GENERAL PUBLIC LICENSE
                       Version 3, 19 November 2007
```

- [ ] **Step 3: Write `.gitignore`**

Create `/Users/szymonri/Documents/tatara/tatara-operator/.gitignore`:
```gitignore
/bin/
/dist/
/cover.out
/cover.html
.DS_Store
*.test
```

- [ ] **Step 4: Write `README.md`**

Create `/Users/szymonri/Documents/tatara/tatara-operator/README.md`:
```markdown
# tatara-operator

A Kubernetes operator that orchestrates the tatara platform's unattended
agentic-development loop. It owns four CRDs in the `tatara.dev/v1alpha1`
API group - `Project`, `Repository`, `Task`, `Subtask` - reconciled by a
controller-runtime manager. It ingests repositories into `tatara-memory`,
receives GitHub/GitLab webhooks to keep memory fresh and to start work
from issues, and spawns `tatara-claude-code-wrapper` pods (with
`tatara-cli` as their MCP server) to do the work, landing results back in
the SCM.

It subsumes the previously-scoped `tatara-tasks` (REST task store; the
CRDs are the store now), `tatara-gitlab-bridge` (webhook bridge; built
in), and the orchestration role of `tatara-argo-workflows` (replaced by
operator-native Pod/Job spawning).

## Status

Milestone M0 (scaffold). API types, shared `internal/{obs,auth,config}`
packages, a no-reconciler manager, Dockerfile, Makefile, and a chart
skeleton carrying the CRDs. Reconciler, webhook, REST, and agent logic
land in M1-M6.

## Layout

```
cmd/manager/main.go               # controller-runtime manager entrypoint
api/v1alpha1/                      # Project/Repository/Task/Subtask types
internal/controller/              # reconcilers (M1+)
internal/obs/                     # JSON slog + Prometheus registry
internal/auth/                    # OIDC verifier + client-credentials token source
internal/config/                  # env-scalar config
charts/tatara-operator/           # cluster-agnostic Helm chart + CRDs
```

## Development

```bash
make generate   # controller-gen deepcopy
make manifests  # controller-gen CRD manifests into the chart
make test       # unit + envtest
make lint       # golangci-lint
make build      # static binary into bin/
make image      # container image
```

## License

AGPLv3. See `LICENSE`.
```

- [ ] **Step 5: Write stub `MEMORY.md`**

Create `/Users/szymonri/Documents/tatara/tatara-operator/MEMORY.md`:
```markdown
# MEMORY - tatara-operator

Past decisions and their context. One line per entry, dated. Append-only
in spirit; prune only when a decision is reversed.

- 2026-06-06 Repo created at milestone M0 (scaffold). API group `tatara.dev`,
  version `v1alpha1`, kinds Project/Repository/Task/Subtask, all namespaced
  to `tatara`. Built on kubebuilder/controller-runtime (rejected plain
  client-go: boilerplate, no envtest, non-idiomatic; rejected Argo-backed
  reconcilers: argo retired for tatara).
- 2026-06-06 Shared contracts pinned in
  `~/Documents/tatara/docs/superpowers/plans/_tatara-operator-shared-contracts.md`.
  All milestones use those exact names/paths/signatures.
- 2026-06-06 obs/auth mirror tatara-chat `internal/{obs,auth}`;
  client-credentials TokenSource mirrors
  tatara-memory-repo-ingester `internal/push/auth.go` (Keycloak `audience`
  form param).
```

- [ ] **Step 6: Write stub `ROADMAP.md`**

Create `/Users/szymonri/Documents/tatara/tatara-operator/ROADMAP.md`:
```markdown
# ROADMAP - tatara-operator

Planned work not yet started. One line per item; link to plans for detail.

- [x] M0 scaffold - kubebuilder project, four CRD types + deepcopy, go.mod,
  internal/{obs,auth,config}, no-reconciler manager, Dockerfile, Makefile,
  chart skeleton with CRDs. Plan:
  `docs/superpowers/plans/2026-06-06-tatara-operator-m0-scaffold.md`.
- [ ] M1 Project + Repository + ingest - ProjectReconciler,
  RepositoryReconciler, ingest Job spawning, last-ingested-commit tracking.
- [ ] M2 webhook server (push) - HMAC verify, provider detection,
  push -> main-filtered incremental re-ingest.
- [ ] M3 REST API + tatara-cli MCP tools - OIDC-gated CRUD + tool group.
- [ ] M4 Task reconciler + turn loop - wrapper Pod+Service, turn callbacks,
  subtask iteration, concurrency gating.
- [ ] M5 SCM write-back + work-item -> Task - scm interface (github+gitlab),
  branch/PR/MR/comment, work-item webhook -> Task.
- [ ] M6 chart + deploy wiring - NetworkPolicy, metrics, Keycloak client,
  infra helmfile `tatara` release.
```

- [ ] **Step 7: First commit**

Run:
```bash
git -C /Users/szymonri/Documents/tatara/tatara-operator add -A
git -C /Users/szymonri/Documents/tatara/tatara-operator commit -m "chore: scaffold tatara-operator repo with canonical files"
```
Expected: a commit listing CLAUDE.md, LICENSE, README.md, MEMORY.md, ROADMAP.md, .gitignore.

- [ ] **Step 8: Create and wire the GitHub remote**

Tatara repos are public under `szymonrychu/`; match that norm. Run:
```bash
gh repo create szymonrychu/tatara-operator --public --source=/Users/szymonri/Documents/tatara/tatara-operator --remote=origin --description "Kubernetes operator orchestrating the tatara agentic-development loop"
git -C /Users/szymonri/Documents/tatara/tatara-operator push -u origin main
```
Verify:
```bash
git -C /Users/szymonri/Documents/tatara/tatara-operator remote -v
```
Expected: `origin  https://github.com/szymonrychu/tatara-operator.git (fetch)` and `(push)`.

Note: the parent `tatara` repo's `.gitignore` already keeps child repos out; confirm `tatara-operator/` is ignored there before any parent commit (`git -C /Users/szymonri/Documents/tatara check-ignore tatara-operator` should print `tatara-operator`). If it is not ignored, add `tatara-operator/` to the parent `.gitignore`.

---

### Task 2: go.mod with module path, Go 1.25.x, and controller-runtime deps

**Files:**
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/go.mod`
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/go.sum` (generated)
- Test: `/Users/szymonri/Documents/tatara/tatara-operator/internal/version/version_test.go`
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/internal/version/version.go`

This task initializes the module and proves the toolchain works with a tiny test-first `version` package (used by Dockerfile/Makefile ldflags, mirroring `tatara-chat`).

- [ ] **Step 1: Initialize the module**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go mod init github.com/szymonrychu/tatara-operator
```
Expected: `go: creating new go.mod: module github.com/szymonrychu/tatara-operator`

- [ ] **Step 2: Pin the Go directive to the exact minor (rule 1)**

Verify the local toolchain minor:
```bash
go version
```
Expected (or newer stable): `go version go1.25.0 darwin/arm64`

Edit `/Users/szymonri/Documents/tatara/tatara-operator/go.mod` so the `go` directive reads the exact minor, e.g.:
```
module github.com/szymonrychu/tatara-operator

go 1.25.0
```

- [ ] **Step 3: Add the runtime dependencies**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && \
  go get sigs.k8s.io/controller-runtime@latest && \
  go get k8s.io/apimachinery@latest && \
  go get k8s.io/api@latest && \
  go get k8s.io/client-go@latest && \
  go get github.com/coreos/go-oidc/v3@latest && \
  go get golang.org/x/oauth2@latest && \
  go get github.com/prometheus/client_golang@latest && \
  go get github.com/stretchr/testify@latest
```
Expected: each `go get` resolves and appends a `require` line; `go.sum` is created.

- [ ] **Step 4: Write the failing test for the version package**

Create `/Users/szymonri/Documents/tatara/tatara-operator/internal/version/version_test.go`:
```go
package version_test

import (
	"testing"

	"github.com/szymonrychu/tatara-operator/internal/version"
)

func TestString(t *testing.T) {
	tests := []struct {
		name    string
		version string
		commit  string
		date    string
		want    string
	}{
		{name: "defaults", version: "dev", commit: "unknown", date: "unknown", want: "dev (unknown, unknown)"},
		{name: "release", version: "v1.2.3", commit: "abc123", date: "2026-06-06T00:00:00Z", want: "v1.2.3 (abc123, 2026-06-06T00:00:00Z)"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			version.Version = tt.version
			version.Commit = tt.commit
			version.Date = tt.date
			if got := version.String(); got != tt.want {
				t.Fatalf("String() = %q, want %q", got, tt.want)
			}
		})
	}
}
```

- [ ] **Step 5: Run test to verify it fails**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go test ./internal/version/... -run TestString -v
```
Expected: FAIL - build error `package github.com/szymonrychu/tatara-operator/internal/version is not in std` / `no Go files in .../internal/version`.

- [ ] **Step 6: Write minimal implementation**

Create `/Users/szymonri/Documents/tatara/tatara-operator/internal/version/version.go`:
```go
package version

import "fmt"

var (
	Version = "dev"
	Commit  = "unknown"
	Date    = "unknown"
)

func String() string {
	return fmt.Sprintf("%s (%s, %s)", Version, Commit, Date)
}
```

- [ ] **Step 7: Run test to verify it passes**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go mod tidy && go test ./internal/version/... -run TestString -v
```
Expected: PASS - `ok  github.com/szymonrychu/tatara-operator/internal/version`.

- [ ] **Step 8: Commit**

```bash
git -C /Users/szymonri/Documents/tatara/tatara-operator add go.mod go.sum internal/version
git -C /Users/szymonri/Documents/tatara/tatara-operator commit -m "chore: init go module and version package"
```

---

### Task 3: API group scaffolding (groupversion_info.go + doc.go)

**Files:**
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/groupversion_info.go`
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/doc.go`
- Test: `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/groupversion_info_test.go`

The group is `tatara.dev`, version `v1alpha1` (pin set). This task establishes the `SchemeBuilder` / `GroupVersion` that the type files and `main.go` register against.

- [ ] **Step 1: Write the failing test**

Create `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/groupversion_info_test.go`:
```go
package v1alpha1_test

import (
	"testing"

	"github.com/szymonrychu/tatara-operator/api/v1alpha1"
)

func TestGroupVersion(t *testing.T) {
	if v1alpha1.GroupVersion.Group != "tatara.dev" {
		t.Fatalf("Group = %q, want tatara.dev", v1alpha1.GroupVersion.Group)
	}
	if v1alpha1.GroupVersion.Version != "v1alpha1" {
		t.Fatalf("Version = %q, want v1alpha1", v1alpha1.GroupVersion.Version)
	}
}

func TestAddToScheme(t *testing.T) {
	if v1alpha1.SchemeBuilder.GroupVersion.String() != "tatara.dev/v1alpha1" {
		t.Fatalf("SchemeBuilder.GroupVersion = %q, want tatara.dev/v1alpha1", v1alpha1.SchemeBuilder.GroupVersion.String())
	}
	if v1alpha1.AddToScheme == nil {
		t.Fatal("AddToScheme is nil")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go test ./api/v1alpha1/... -run TestGroupVersion -v
```
Expected: FAIL - `no Go files in .../api/v1alpha1` (build error).

- [ ] **Step 3: Write minimal implementation**

Create `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/groupversion_info.go`:
```go
// Package v1alpha1 contains API Schema definitions for the tatara.dev v1alpha1 API group.
// +kubebuilder:object:generate=true
// +groupName=tatara.dev
package v1alpha1

import (
	"k8s.io/apimachinery/pkg/runtime/schema"
	"sigs.k8s.io/controller-runtime/pkg/scheme"
)

var (
	// GroupVersion is group version used to register these objects.
	GroupVersion = schema.GroupVersion{Group: "tatara.dev", Version: "v1alpha1"}

	// SchemeBuilder is used to add go types to the GroupVersionKind scheme.
	SchemeBuilder = &scheme.Builder{GroupVersion: GroupVersion}

	// AddToScheme adds the types in this group-version to the given scheme.
	AddToScheme = SchemeBuilder.AddToScheme
)
```

Create `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/doc.go`:
```go
// +kubebuilder:object:generate=true
// +groupName=tatara.dev

// Package v1alpha1 holds the Project, Repository, Task, and Subtask custom
// resources for the tatara-operator. All kinds are namespaced.
package v1alpha1
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go test ./api/v1alpha1/... -run TestGroupVersion -v
```
Expected: PASS - `ok  github.com/szymonrychu/tatara-operator/api/v1alpha1`.

- [ ] **Step 5: Commit**

```bash
git -C /Users/szymonri/Documents/tatara/tatara-operator add api/v1alpha1/groupversion_info.go api/v1alpha1/doc.go api/v1alpha1/groupversion_info_test.go
git -C /Users/szymonri/Documents/tatara/tatara-operator commit -m "feat: add tatara.dev/v1alpha1 group version info"
```

---

### Task 4: Project and Repository types

**Files:**
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/project_types.go`
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/repository_types.go`
- Test: `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/project_types_test.go`

Field names are EXACTLY from the spec CRD-model section. `conditions` is `[]metav1.Condition`. These types register into the scheme via `init()`.

- [ ] **Step 1: Write the failing test**

Create `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/project_types_test.go`:
```go
package v1alpha1_test

import (
	"testing"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"

	"github.com/szymonrychu/tatara-operator/api/v1alpha1"
)

func TestProjectFields(t *testing.T) {
	p := v1alpha1.Project{
		Spec: v1alpha1.ProjectSpec{
			ScmSecretRef:       "scm-secret",
			TriggerLabel:       "tatara",
			MaxConcurrentTasks: 3,
			Agent: v1alpha1.AgentSpec{
				Model:              "claude-sonnet-4-6",
				Image:              "wrapper:latest",
				PermissionMode:     "bypassPermissions",
				MaxTurnsPerTask:    50,
				TurnTimeoutSeconds: 1800,
			},
		},
		Status: v1alpha1.ProjectStatus{
			WebhookURL: "https://example/operator/webhooks/p",
			Conditions: []metav1.Condition{{Type: "Ready", Status: metav1.ConditionTrue}},
		},
	}
	if p.Spec.Agent.MaxTurnsPerTask != 50 {
		t.Fatalf("MaxTurnsPerTask = %d, want 50", p.Spec.Agent.MaxTurnsPerTask)
	}
	if p.Status.WebhookURL == "" {
		t.Fatal("WebhookURL empty")
	}
}

func TestRepositoryFields(t *testing.T) {
	r := v1alpha1.Repository{
		Spec: v1alpha1.RepositorySpec{
			ProjectRef:    "p",
			URL:           "https://example/repo.git",
			DefaultBranch: "main",
			IngestEnabled: true,
		},
		Status: v1alpha1.RepositoryStatus{
			Phase:              "Ingested",
			LastIngestedCommit: "abc123",
			JobName:            "ingest-1",
		},
	}
	if r.Spec.DefaultBranch != "main" {
		t.Fatalf("DefaultBranch = %q, want main", r.Spec.DefaultBranch)
	}
	if r.Status.Phase != "Ingested" {
		t.Fatalf("Phase = %q, want Ingested", r.Status.Phase)
	}
}

func TestProjectRegisteredInScheme(t *testing.T) {
	s := runtime.NewScheme()
	if err := v1alpha1.AddToScheme(s); err != nil {
		t.Fatalf("AddToScheme: %v", err)
	}
	if !s.Recognizes(v1alpha1.GroupVersion.WithKind("Project")) {
		t.Fatal("Project kind not recognized by scheme")
	}
	if !s.Recognizes(v1alpha1.GroupVersion.WithKind("Repository")) {
		t.Fatal("Repository kind not recognized by scheme")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go test ./api/v1alpha1/... -run 'TestProjectFields|TestRepositoryFields|TestProjectRegisteredInScheme' -v
```
Expected: FAIL - undefined: `v1alpha1.Project`, `v1alpha1.ProjectSpec`, `v1alpha1.Repository`, etc.

- [ ] **Step 3: Write minimal implementation**

Create `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/project_types.go`:
```go
package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// AgentSpec configures the wrapper agent session a Task runs.
type AgentSpec struct {
	// +optional
	Model string `json:"model,omitempty"`
	// +optional
	Image string `json:"image,omitempty"`
	// +kubebuilder:default="bypassPermissions"
	// +optional
	PermissionMode string `json:"permissionMode,omitempty"`
	// +kubebuilder:default=50
	// +optional
	MaxTurnsPerTask int `json:"maxTurnsPerTask,omitempty"`
	// +kubebuilder:default=1800
	// +optional
	TurnTimeoutSeconds int `json:"turnTimeoutSeconds,omitempty"`
}

// ProjectSpec defines the desired state of a Project.
type ProjectSpec struct {
	ScmSecretRef string `json:"scmSecretRef"`
	// +kubebuilder:default="tatara"
	// +optional
	TriggerLabel string `json:"triggerLabel,omitempty"`
	// +kubebuilder:default=3
	// +optional
	MaxConcurrentTasks int `json:"maxConcurrentTasks,omitempty"`
	// +optional
	Agent AgentSpec `json:"agent,omitempty"`
}

// ProjectStatus defines the observed state of a Project.
type ProjectStatus struct {
	// +optional
	WebhookURL string `json:"webhookURL,omitempty"`
	// +optional
	// +listType=map
	// +listMapKey=type
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:scope=Namespaced
// +kubebuilder:printcolumn:name="Webhook",type=string,JSONPath=`.status.webhookURL`

// Project is the top-level grouping for repositories and tasks.
type Project struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   ProjectSpec   `json:"spec,omitempty"`
	Status ProjectStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// ProjectList contains a list of Project.
type ProjectList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []Project `json:"items"`
}

func init() {
	SchemeBuilder.Register(&Project{}, &ProjectList{})
}
```

Create `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/repository_types.go`:
```go
package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

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

// RepositoryStatus defines the observed state of a Repository.
type RepositoryStatus struct {
	// +kubebuilder:validation:Enum=Pending;Ingesting;Ingested;Failed
	// +optional
	Phase string `json:"phase,omitempty"`
	// +optional
	LastIngestedCommit string `json:"lastIngestedCommit,omitempty"`
	// +optional
	LastIngestTime *metav1.Time `json:"lastIngestTime,omitempty"`
	// +optional
	JobName string `json:"jobName,omitempty"`
	// +optional
	// +listType=map
	// +listMapKey=type
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:scope=Namespaced
// +kubebuilder:printcolumn:name="Phase",type=string,JSONPath=`.status.phase`
// +kubebuilder:printcolumn:name="Commit",type=string,JSONPath=`.status.lastIngestedCommit`

// Repository is a git remote ingested into tatara-memory for a Project.
type Repository struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   RepositorySpec   `json:"spec,omitempty"`
	Status RepositoryStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// RepositoryList contains a list of Repository.
type RepositoryList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []Repository `json:"items"`
}

func init() {
	SchemeBuilder.Register(&Repository{}, &RepositoryList{})
}
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go test ./api/v1alpha1/... -run 'TestProjectFields|TestRepositoryFields|TestProjectRegisteredInScheme' -v
```
Expected: PASS - all three subtests green.

Note: the scheme-registration test passes here because `SchemeBuilder.Register` records the types; `runtime.Object` interface methods (`DeepCopyObject`) are provided by generated code in Task 6. If the build complains that `*Project` does not implement `runtime.Object` before Task 6, that is expected - the registration call type-asserts. To keep this task self-contained and green, add temporary no-op `DeepCopyObject` only if the compiler requires it; otherwise proceed - Task 6 generates the real implementations. If a temporary stub is needed, delete it in Task 6 Step 1 before generating.

- [ ] **Step 5: Commit**

```bash
git -C /Users/szymonri/Documents/tatara/tatara-operator add api/v1alpha1/project_types.go api/v1alpha1/repository_types.go api/v1alpha1/project_types_test.go
git -C /Users/szymonri/Documents/tatara/tatara-operator commit -m "feat: add Project and Repository API types"
```

---

### Task 5: Task and Subtask types

**Files:**
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/task_types.go`
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/subtask_types.go`
- Test: `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/task_types_test.go`

Field names EXACTLY from the spec CRD-model section. `source` is an optional nested struct (`provider`, `issueRef`, `url`).

- [ ] **Step 1: Write the failing test**

Create `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/task_types_test.go`:
```go
package v1alpha1_test

import (
	"testing"

	"k8s.io/apimachinery/pkg/runtime"

	"github.com/szymonrychu/tatara-operator/api/v1alpha1"
)

func TestTaskFields(t *testing.T) {
	task := v1alpha1.Task{
		Spec: v1alpha1.TaskSpec{
			ProjectRef:    "p",
			RepositoryRef: "r",
			Goal:          "do the thing",
			Source: &v1alpha1.TaskSource{
				Provider: "github",
				IssueRef: "owner/repo#123",
				URL:      "https://github.com/owner/repo/issues/123",
			},
			MaxTurns: 25,
		},
		Status: v1alpha1.TaskStatus{
			Phase:          "Running",
			PodName:        "task-p-1",
			TurnsCompleted: 4,
			PrURL:          "https://github.com/owner/repo/pull/5",
			ResultSummary:  "opened PR",
		},
	}
	if task.Spec.Source.Provider != "github" {
		t.Fatalf("Source.Provider = %q, want github", task.Spec.Source.Provider)
	}
	if task.Status.TurnsCompleted != 4 {
		t.Fatalf("TurnsCompleted = %d, want 4", task.Status.TurnsCompleted)
	}
}

func TestSubtaskFields(t *testing.T) {
	s := v1alpha1.Subtask{
		Spec: v1alpha1.SubtaskSpec{
			TaskRef: "task-p-1",
			Title:   "write test",
			Detail:  "add the failing test",
			Order:   1,
		},
		Status: v1alpha1.SubtaskStatus{
			Phase:  "Done",
			TurnID: "turn-abc",
			Result: "test added",
		},
	}
	if s.Spec.Order != 1 {
		t.Fatalf("Order = %d, want 1", s.Spec.Order)
	}
	if s.Status.TurnID != "turn-abc" {
		t.Fatalf("TurnID = %q, want turn-abc", s.Status.TurnID)
	}
}

func TestTaskAndSubtaskRegisteredInScheme(t *testing.T) {
	sch := runtime.NewScheme()
	if err := v1alpha1.AddToScheme(sch); err != nil {
		t.Fatalf("AddToScheme: %v", err)
	}
	for _, kind := range []string{"Task", "Subtask"} {
		if !sch.Recognizes(v1alpha1.GroupVersion.WithKind(kind)) {
			t.Fatalf("%s kind not recognized by scheme", kind)
		}
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go test ./api/v1alpha1/... -run 'TestTaskFields|TestSubtaskFields|TestTaskAndSubtaskRegisteredInScheme' -v
```
Expected: FAIL - undefined: `v1alpha1.Task`, `v1alpha1.TaskSpec`, `v1alpha1.TaskSource`, `v1alpha1.Subtask`, etc.

- [ ] **Step 3: Write minimal implementation**

Create `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/task_types.go`:
```go
package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// TaskSource records the SCM work-item that originated a webhook-born Task.
type TaskSource struct {
	// +kubebuilder:validation:Enum=github;gitlab
	Provider string `json:"provider"`
	IssueRef string `json:"issueRef"`
	// +optional
	URL string `json:"url,omitempty"`
}

// TaskSpec defines the desired state of a Task.
type TaskSpec struct {
	ProjectRef    string `json:"projectRef"`
	RepositoryRef string `json:"repositoryRef"`
	Goal          string `json:"goal"`
	// +optional
	Source *TaskSource `json:"source,omitempty"`
	// +optional
	MaxTurns int `json:"maxTurns,omitempty"`
}

// TaskStatus defines the observed state of a Task.
type TaskStatus struct {
	// +kubebuilder:validation:Enum=Pending;Planning;Running;Succeeded;Failed
	// +optional
	Phase string `json:"phase,omitempty"`
	// +optional
	PodName string `json:"podName,omitempty"`
	// +optional
	TurnsCompleted int `json:"turnsCompleted,omitempty"`
	// +optional
	PrURL string `json:"prURL,omitempty"`
	// +optional
	ResultSummary string `json:"resultSummary,omitempty"`
	// +optional
	// +listType=map
	// +listMapKey=type
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:scope=Namespaced
// +kubebuilder:printcolumn:name="Phase",type=string,JSONPath=`.status.phase`
// +kubebuilder:printcolumn:name="Turns",type=integer,JSONPath=`.status.turnsCompleted`

// Task is one agent session driving a Repository toward a goal.
type Task struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   TaskSpec   `json:"spec,omitempty"`
	Status TaskStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// TaskList contains a list of Task.
type TaskList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []Task `json:"items"`
}

func init() {
	SchemeBuilder.Register(&Task{}, &TaskList{})
}
```

Create `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/subtask_types.go`:
```go
package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// SubtaskSpec defines the desired state of a Subtask. Created/updated by the
// agent via the REST API and consumed by the Task reconciler's turn loop.
type SubtaskSpec struct {
	TaskRef string `json:"taskRef"`
	Title   string `json:"title"`
	// +optional
	Detail string `json:"detail,omitempty"`
	// +optional
	Order int `json:"order,omitempty"`
}

// SubtaskStatus defines the observed state of a Subtask.
type SubtaskStatus struct {
	// +kubebuilder:validation:Enum=Pending;Running;Done;Failed
	// +optional
	Phase string `json:"phase,omitempty"`
	// +optional
	TurnID string `json:"turnId,omitempty"`
	// +optional
	Result string `json:"result,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:resource:scope=Namespaced
// +kubebuilder:printcolumn:name="Order",type=integer,JSONPath=`.spec.order`
// +kubebuilder:printcolumn:name="Phase",type=string,JSONPath=`.status.phase`

// Subtask is a unit of work fed to a Task's agent session one turn at a time.
type Subtask struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   SubtaskSpec   `json:"spec,omitempty"`
	Status SubtaskStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// SubtaskList contains a list of Subtask.
type SubtaskList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []Subtask `json:"items"`
}

func init() {
	SchemeBuilder.Register(&Subtask{}, &SubtaskList{})
}
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go test ./api/v1alpha1/... -run 'TestTaskFields|TestSubtaskFields|TestTaskAndSubtaskRegisteredInScheme' -v
```
Expected: PASS - all three subtests green. (Same DeepCopyObject caveat as Task 4 Step 4 applies until Task 6.)

- [ ] **Step 5: Commit**

```bash
git -C /Users/szymonri/Documents/tatara/tatara-operator add api/v1alpha1/task_types.go api/v1alpha1/subtask_types.go api/v1alpha1/task_types_test.go
git -C /Users/szymonri/Documents/tatara/tatara-operator commit -m "feat: add Task and Subtask API types"
```

---

### Task 6: Generate zz_generated.deepcopy.go via controller-gen

**Files:**
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/zz_generated.deepcopy.go` (generated)
- Modify: `/Users/szymonri/Documents/tatara/tatara-operator/go.mod` (controller-tools tool dependency)
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/hack/tools.go` (optional tool pin; only if not using `go run` with version)

This is a generation task (not test-first). It produces the `DeepCopy`/`DeepCopyObject` methods that satisfy `runtime.Object` for every type. If temporary `DeepCopyObject` stubs were added in Tasks 4/5, delete them first.

- [ ] **Step 1: Remove any temporary DeepCopyObject stubs**

If Tasks 4/5 needed temporary stubs to compile, delete them now so generation owns those methods. Verify none remain:
```bash
grep -rn "func.*DeepCopyObject" /Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/*.go | grep -v zz_generated
```
Expected: no output (all DeepCopy methods will live in `zz_generated.deepcopy.go`).

- [ ] **Step 2: Run controller-gen to generate deepcopy**

Run (pin controller-tools to a current release; this is the same binary the Makefile target will invoke):
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && \
  go run sigs.k8s.io/controller-tools/cmd/controller-gen@v0.18.0 \
    object:headerFile="hack/boilerplate.go.txt" paths="./api/..."
```
If the header file does not exist, create it first:

Create `/Users/szymonri/Documents/tatara/tatara-operator/hack/boilerplate.go.txt`:
```
// Code generated by controller-gen. DO NOT EDIT.
```
Then re-run the command above.

Expected: `api/v1alpha1/zz_generated.deepcopy.go` is created with `DeepCopyInto`, `DeepCopy`, and `DeepCopyObject` for `Project`, `ProjectList`, `ProjectSpec`, `ProjectStatus`, `AgentSpec`, `Repository`, `RepositoryList`, `RepositorySpec`, `RepositoryStatus`, `Task`, `TaskList`, `TaskSpec`, `TaskStatus`, `TaskSource`, `Subtask`, `SubtaskList`, `SubtaskSpec`, `SubtaskStatus`.

- [ ] **Step 3: Tidy and verify the API package compiles and all tests pass**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go mod tidy && go test ./api/v1alpha1/... -v
```
Expected: PASS - every subtest from Tasks 3-5 green, including the scheme-registration tests (now that `runtime.Object` is fully satisfied).

- [ ] **Step 4: Verify generated content covers all kinds**

Run:
```bash
grep -c "func (in \*.*) DeepCopyObject() runtime.Object" /Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/zz_generated.deepcopy.go
```
Expected: `8` (Project, ProjectList, Repository, RepositoryList, Task, TaskList, Subtask, SubtaskList).

- [ ] **Step 5: Commit**

```bash
git -C /Users/szymonri/Documents/tatara/tatara-operator add api/v1alpha1/zz_generated.deepcopy.go hack/boilerplate.go.txt go.mod go.sum
git -C /Users/szymonri/Documents/tatara/tatara-operator commit -m "chore: generate deepcopy for v1alpha1 types"
```

---

### Task 7: internal/config

**Files:**
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/internal/config/config.go`
- Test: `/Users/szymonri/Documents/tatara/tatara-operator/internal/config/config_test.go`

Env scalars EXACTLY from the pin set. Loaded from the process environment (populated by the chart's ConfigMap/Secret via `envFrom`, rule 6).

- [ ] **Step 1: Write the failing test**

Create `/Users/szymonri/Documents/tatara/tatara-operator/internal/config/config_test.go`:
```go
package config_test

import (
	"testing"

	"github.com/szymonrychu/tatara-operator/internal/config"
)

func TestLoad(t *testing.T) {
	env := map[string]string{
		"HTTP_ADDR":                   ":8080",
		"METRICS_ADDR":                ":9090",
		"INTERNAL_ADDR":               ":8081",
		"OIDC_ISSUER":                 "https://kc/realms/tatara",
		"OIDC_AUDIENCE":               "tatara-operator",
		"MEMORY_BASE_URL":             "http://tatara-memory:8080",
		"INGESTER_IMAGE":              "harbor/ingester:1",
		"EXTERNAL_WEBHOOK_BASE":       "https://ops.example",
		"OPERATOR_OIDC_CLIENT_ID":     "tatara-operator",
		"OPERATOR_OIDC_CLIENT_SECRET": "shh",
		"ANTHROPIC_SECRET_NAME":       "anthropic",
		"CLI_OIDC_SECRET_NAME":        "cli-oidc",
		"LOG_LEVEL":                   "debug",
	}
	for k, v := range env {
		t.Setenv(k, v)
	}

	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("Load: %v", err)
	}

	tests := []struct {
		name string
		got  string
		want string
	}{
		{"HTTPAddr", cfg.HTTPAddr, ":8080"},
		{"MetricsAddr", cfg.MetricsAddr, ":9090"},
		{"InternalAddr", cfg.InternalAddr, ":8081"},
		{"OIDCIssuer", cfg.OIDCIssuer, "https://kc/realms/tatara"},
		{"OIDCAudience", cfg.OIDCAudience, "tatara-operator"},
		{"MemoryBaseURL", cfg.MemoryBaseURL, "http://tatara-memory:8080"},
		{"IngesterImage", cfg.IngesterImage, "harbor/ingester:1"},
		{"ExternalWebhookBase", cfg.ExternalWebhookBase, "https://ops.example"},
		{"OperatorOIDCClientID", cfg.OperatorOIDCClientID, "tatara-operator"},
		{"OperatorOIDCClientSecret", cfg.OperatorOIDCClientSecret, "shh"},
		{"AnthropicSecretName", cfg.AnthropicSecretName, "anthropic"},
		{"CLIOIDCSecretName", cfg.CLIOIDCSecretName, "cli-oidc"},
		{"LogLevel", cfg.LogLevel, "debug"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if tt.got != tt.want {
				t.Fatalf("%s = %q, want %q", tt.name, tt.got, tt.want)
			}
		})
	}
}

func TestLoad_MissingRequired(t *testing.T) {
	t.Setenv("OIDC_ISSUER", "")
	t.Setenv("OIDC_AUDIENCE", "")
	if _, err := config.Load(); err == nil {
		t.Fatal("expected error for missing required OIDC_ISSUER/OIDC_AUDIENCE")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go test ./internal/config/... -v
```
Expected: FAIL - `no Go files in .../internal/config` / undefined `config.Load`.

- [ ] **Step 3: Write minimal implementation**

Create `/Users/szymonri/Documents/tatara/tatara-operator/internal/config/config.go`:
```go
package config

import (
	"fmt"
	"os"
)

// Config holds the env-scalar configuration for the operator. Each field is
// populated from an env var injected via the chart ConfigMap/Secret (rule 6).
type Config struct {
	HTTPAddr                 string
	MetricsAddr              string
	InternalAddr             string
	OIDCIssuer               string
	OIDCAudience             string
	MemoryBaseURL            string
	IngesterImage            string
	ExternalWebhookBase      string
	OperatorOIDCClientID     string
	OperatorOIDCClientSecret string
	AnthropicSecretName      string
	CLIOIDCSecretName        string
	LogLevel                 string
}

func getDefault(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// Load reads the operator configuration from the environment, applying
// defaults for the listener addresses and log level. OIDC issuer and
// audience are required.
func Load() (Config, error) {
	cfg := Config{
		HTTPAddr:                 getDefault("HTTP_ADDR", ":8080"),
		MetricsAddr:              getDefault("METRICS_ADDR", ":9090"),
		InternalAddr:             getDefault("INTERNAL_ADDR", ":8081"),
		OIDCIssuer:               os.Getenv("OIDC_ISSUER"),
		OIDCAudience:             os.Getenv("OIDC_AUDIENCE"),
		MemoryBaseURL:            os.Getenv("MEMORY_BASE_URL"),
		IngesterImage:            os.Getenv("INGESTER_IMAGE"),
		ExternalWebhookBase:      os.Getenv("EXTERNAL_WEBHOOK_BASE"),
		OperatorOIDCClientID:     os.Getenv("OPERATOR_OIDC_CLIENT_ID"),
		OperatorOIDCClientSecret: os.Getenv("OPERATOR_OIDC_CLIENT_SECRET"),
		AnthropicSecretName:      os.Getenv("ANTHROPIC_SECRET_NAME"),
		CLIOIDCSecretName:        os.Getenv("CLI_OIDC_SECRET_NAME"),
		LogLevel:                 getDefault("LOG_LEVEL", "info"),
	}
	if cfg.OIDCIssuer == "" {
		return Config{}, fmt.Errorf("config: OIDC_ISSUER is required")
	}
	if cfg.OIDCAudience == "" {
		return Config{}, fmt.Errorf("config: OIDC_AUDIENCE is required")
	}
	return cfg, nil
}
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go test ./internal/config/... -v
```
Expected: PASS - `TestLoad` (all 13 subtests) and `TestLoad_MissingRequired` green.

- [ ] **Step 5: Commit**

```bash
git -C /Users/szymonri/Documents/tatara/tatara-operator add internal/config
git -C /Users/szymonri/Documents/tatara/tatara-operator commit -m "feat: add internal/config env loader"
```

---

### Task 8: internal/obs (JSON slog logger + Prometheus metrics)

**Files:**
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/internal/obs/logger.go`
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/internal/obs/metrics.go`
- Test: `/Users/szymonri/Documents/tatara/tatara-operator/internal/obs/logger_test.go`
- Test: `/Users/szymonri/Documents/tatara/tatara-operator/internal/obs/metrics_test.go`

Mirrors tatara-chat `internal/obs`. Metric names EXACTLY from the pin set:
`operator_reconcile_total{kind,result}`, `operator_ingest_job_duration_seconds`,
`operator_turn_duration_seconds`, `operator_webhook_events_total{provider,kind,result}`,
`operator_tasks_inflight` (gauge). M0 only declares and registers them; later milestones use them.

- [ ] **Step 1: Write the failing logger test**

Create `/Users/szymonri/Documents/tatara/tatara-operator/internal/obs/logger_test.go`:
```go
package obs_test

import (
	"bytes"
	"encoding/json"
	"log/slog"
	"testing"

	"github.com/szymonrychu/tatara-operator/internal/obs"
)

func TestNewLogger_EmitsJSON(t *testing.T) {
	var buf bytes.Buffer
	logger := obs.NewLogger(&buf, slog.LevelInfo)
	logger.Info("hello", slog.String("action", "test"))

	var entry map[string]any
	if err := json.Unmarshal(buf.Bytes(), &entry); err != nil {
		t.Fatalf("log line is not valid JSON: %v (%q)", err, buf.String())
	}
	if entry["msg"] != "hello" {
		t.Fatalf("msg = %v, want hello", entry["msg"])
	}
	if entry["action"] != "test" {
		t.Fatalf("action = %v, want test", entry["action"])
	}
}

func TestParseLevel(t *testing.T) {
	tests := []struct {
		in   string
		want slog.Level
	}{
		{"debug", slog.LevelDebug},
		{"info", slog.LevelInfo},
		{"warn", slog.LevelWarn},
		{"error", slog.LevelError},
		{"", slog.LevelInfo},
		{"bogus", slog.LevelInfo},
	}
	for _, tt := range tests {
		t.Run(tt.in, func(t *testing.T) {
			if got := obs.ParseLevel(tt.in); got != tt.want {
				t.Fatalf("ParseLevel(%q) = %v, want %v", tt.in, got, tt.want)
			}
		})
	}
}
```

- [ ] **Step 2: Run logger test to verify it fails**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go test ./internal/obs/... -run 'TestNewLogger_EmitsJSON|TestParseLevel' -v
```
Expected: FAIL - `no Go files in .../internal/obs` / undefined `obs.NewLogger`, `obs.ParseLevel`.

- [ ] **Step 3: Write logger implementation**

Create `/Users/szymonri/Documents/tatara/tatara-operator/internal/obs/logger.go`:
```go
package obs

import (
	"io"
	"log/slog"
	"strings"
)

// NewLogger returns a JSON-format slog.Logger writing to w at the given level.
func NewLogger(w io.Writer, level slog.Level) *slog.Logger {
	h := slog.NewJSONHandler(w, &slog.HandlerOptions{Level: level})
	return slog.New(h)
}

// ParseLevel maps a config string to an slog.Level, defaulting to Info.
func ParseLevel(s string) slog.Level {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "debug":
		return slog.LevelDebug
	case "warn", "warning":
		return slog.LevelWarn
	case "error":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}
```

- [ ] **Step 4: Write the failing metrics test**

Create `/Users/szymonri/Documents/tatara/tatara-operator/internal/obs/metrics_test.go`:
```go
package obs_test

import (
	"testing"

	"github.com/prometheus/client_golang/prometheus/testutil"

	"github.com/szymonrychu/tatara-operator/internal/obs"
)

func TestNewMetrics_RegistersAll(t *testing.T) {
	m := obs.NewMetrics()
	if m.Registry == nil {
		t.Fatal("Registry is nil")
	}

	m.ReconcileTotal.WithLabelValues("Project", "success").Inc()
	m.IngestJobDuration.Observe(1.5)
	m.TurnDuration.Observe(2.5)
	m.WebhookEvents.WithLabelValues("github", "push", "accepted").Inc()
	m.TasksInflight.Set(3)

	want := []string{
		"operator_reconcile_total",
		"operator_ingest_job_duration_seconds",
		"operator_turn_duration_seconds",
		"operator_webhook_events_total",
		"operator_tasks_inflight",
	}
	for _, name := range want {
		t.Run(name, func(t *testing.T) {
			if testutil.CollectAndCount(m.Registry, name) == 0 {
				t.Fatalf("metric %q not registered/collected", name)
			}
		})
	}
}
```

- [ ] **Step 5: Run metrics test to verify it fails**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go test ./internal/obs/... -run TestNewMetrics_RegistersAll -v
```
Expected: FAIL - undefined `obs.NewMetrics`, `obs.Metrics`.

- [ ] **Step 6: Write metrics implementation**

Create `/Users/szymonri/Documents/tatara/tatara-operator/internal/obs/metrics.go`:
```go
package obs

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
)

// Metrics holds the operator's Prometheus collectors and the registry they
// are registered against. One Metrics per process.
type Metrics struct {
	Registry          *prometheus.Registry
	ReconcileTotal    *prometheus.CounterVec
	IngestJobDuration prometheus.Histogram
	TurnDuration      prometheus.Histogram
	WebhookEvents     *prometheus.CounterVec
	TasksInflight     prometheus.Gauge
}

// NewMetrics constructs and registers all operator metrics on a fresh registry
// pre-populated with the Go and process collectors.
func NewMetrics() *Metrics {
	reg := prometheus.NewRegistry()
	reg.MustRegister(collectors.NewGoCollector())
	reg.MustRegister(collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}))

	m := &Metrics{
		Registry: reg,
		ReconcileTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "operator_reconcile_total",
			Help: "Total reconcile invocations by kind and result.",
		}, []string{"kind", "result"}),
		IngestJobDuration: prometheus.NewHistogram(prometheus.HistogramOpts{
			Name:    "operator_ingest_job_duration_seconds",
			Help:    "Duration of repository ingest Jobs in seconds.",
			Buckets: prometheus.DefBuckets,
		}),
		TurnDuration: prometheus.NewHistogram(prometheus.HistogramOpts{
			Name:    "operator_turn_duration_seconds",
			Help:    "Duration of agent turns in seconds.",
			Buckets: prometheus.DefBuckets,
		}),
		WebhookEvents: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "operator_webhook_events_total",
			Help: "Total webhook events by provider, kind and result.",
		}, []string{"provider", "kind", "result"}),
		TasksInflight: prometheus.NewGauge(prometheus.GaugeOpts{
			Name: "operator_tasks_inflight",
			Help: "Number of Tasks currently running.",
		}),
	}
	reg.MustRegister(m.ReconcileTotal, m.IngestJobDuration, m.TurnDuration, m.WebhookEvents, m.TasksInflight)
	return m
}
```

- [ ] **Step 7: Run both obs tests to verify they pass**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go mod tidy && go test ./internal/obs/... -v
```
Expected: PASS - `TestNewLogger_EmitsJSON`, `TestParseLevel` (6 subtests), `TestNewMetrics_RegistersAll` (5 subtests) green.

- [ ] **Step 8: Commit**

```bash
git -C /Users/szymonri/Documents/tatara/tatara-operator add internal/obs go.mod go.sum
git -C /Users/szymonri/Documents/tatara/tatara-operator commit -m "feat: add internal/obs JSON slog logger and Prometheus metrics"
```

---

### Task 9: internal/auth Verifier (OIDC)

**Files:**
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/internal/auth/auth.go`
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/internal/auth/verifier.go`
- Test: `/Users/szymonri/Documents/tatara/tatara-operator/internal/auth/verifier_test.go`
- Test: `/Users/szymonri/Documents/tatara/tatara-operator/internal/auth/testjwks/server.go` (copied from tatara-chat)

Mirrors tatara-chat `internal/auth`. `Verifier` does JWKS discovery and verifies `iss`/`exp`/`aud contains OIDC_AUDIENCE`. Reuse the sibling's `testjwks` test helper verbatim (adjust import path only).

- [ ] **Step 1: Copy the testjwks helper from the sibling and fix its import path**

Run:
```bash
mkdir -p /Users/szymonri/Documents/tatara/tatara-operator/internal/auth/testjwks
cp /Users/szymonri/Documents/tatara/tatara-chat/internal/auth/testjwks/*.go /Users/szymonri/Documents/tatara/tatara-operator/internal/auth/testjwks/
```
Then grep for any `tatara-chat` import inside the copied helper and rewrite it to `tatara-operator`:
```bash
grep -rl "szymonrychu/tatara-chat" /Users/szymonri/Documents/tatara/tatara-operator/internal/auth/testjwks/
```
If any file is listed, edit each to replace `github.com/szymonrychu/tatara-chat` with `github.com/szymonrychu/tatara-operator`. (The helper is self-contained signing/JWKS code; it likely has no such import, in which case the grep prints nothing and no edit is needed.)

- [ ] **Step 2: Write the failing test (table-driven over the JWKS server)**

Create `/Users/szymonri/Documents/tatara/tatara-operator/internal/auth/verifier_test.go`:
```go
package auth_test

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-operator/internal/auth"
	"github.com/szymonrychu/tatara-operator/internal/auth/testjwks"
)

func TestVerifier_ValidToken(t *testing.T) {
	srv := testjwks.NewServer(t)
	ctx := context.Background()

	v, err := auth.NewVerifier(ctx, auth.Config{Issuer: srv.Issuer(), Audience: "tatara-operator"})
	require.NoError(t, err)

	tok := srv.SignTypedToken(t, testjwks.Claims{
		Issuer:   srv.Issuer(),
		Audience: []string{"tatara-operator"},
		Subject:  "agent-1",
		Extra:    map[string]any{"preferred_username": "agent"},
	})

	claims, err := v.Verify(ctx, tok)
	require.NoError(t, err)
	require.Equal(t, "agent-1", claims.Subject)
	require.Equal(t, "agent", claims.PreferredUsername)
}

func TestVerifier_Rejections(t *testing.T) {
	srv := testjwks.NewServer(t)
	ctx := context.Background()
	v, err := auth.NewVerifier(ctx, auth.Config{Issuer: srv.Issuer(), Audience: "tatara-operator"})
	require.NoError(t, err)

	foreign, err := rsa.GenerateKey(rand.Reader, 2048)
	require.NoError(t, err)

	tests := []struct {
		name string
		sign func() string
	}{
		{
			name: "expired",
			sign: func() string {
				return srv.SignTypedToken(t, testjwks.Claims{
					Issuer:    srv.Issuer(),
					Audience:  []string{"tatara-operator"},
					Subject:   "agent-1",
					IssuedAt:  time.Now().Add(-2 * time.Hour),
					NotBefore: time.Now().Add(-2 * time.Hour),
					ExpiresAt: time.Now().Add(-time.Hour),
				})
			},
		},
		{
			name: "wrong-issuer",
			sign: func() string {
				return srv.SignTypedToken(t, testjwks.Claims{
					Issuer:   "https://evil.example/realms/master",
					Audience: []string{"tatara-operator"},
					Subject:  "agent-1",
				})
			},
		},
		{
			name: "wrong-audience",
			sign: func() string {
				return srv.SignTypedToken(t, testjwks.Claims{
					Issuer:   srv.Issuer(),
					Audience: []string{"some-other-app"},
					Subject:  "agent-1",
				})
			},
		},
		{
			name: "bad-signature",
			sign: func() string {
				return srv.SignTokenWithKey(t, foreign, testjwks.Claims{
					Issuer:   srv.Issuer(),
					Audience: []string{"tatara-operator"},
					Subject:  "agent-1",
				})
			},
		},
		{
			name: "missing-sub",
			sign: func() string {
				return srv.SignTypedToken(t, testjwks.Claims{
					Issuer:   srv.Issuer(),
					Audience: []string{"tatara-operator"},
				})
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			_, err := v.Verify(ctx, tt.sign())
			require.Error(t, err)
		})
	}
}

func TestConfig_Validate(t *testing.T) {
	require.Error(t, auth.Config{Audience: "x"}.Validate())
	require.Error(t, auth.Config{Issuer: "x"}.Validate())
	require.NoError(t, auth.Config{Issuer: "x", Audience: "y"}.Validate())
}
```

- [ ] **Step 3: Run test to verify it fails**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go test ./internal/auth/... -run 'TestVerifier_ValidToken|TestVerifier_Rejections|TestConfig_Validate' -v
```
Expected: FAIL - undefined `auth.NewVerifier`, `auth.Config`, `auth.Verifier`.

- [ ] **Step 4: Write minimal implementation**

Create `/Users/szymonri/Documents/tatara/tatara-operator/internal/auth/auth.go`:
```go
package auth

import "errors"

// Config holds OIDC verifier settings.
type Config struct {
	Issuer   string
	Audience string
}

// Validate returns an error if required fields are missing.
func (c Config) Validate() error {
	if c.Issuer == "" {
		return errors.New("auth: issuer is required")
	}
	if c.Audience == "" {
		return errors.New("auth: audience is required")
	}
	return nil
}
```

Create `/Users/szymonri/Documents/tatara/tatara-operator/internal/auth/verifier.go`:
```go
package auth

import (
	"context"
	"fmt"

	"github.com/coreos/go-oidc/v3/oidc"
)

// Verifier validates JWT bearer tokens against an OIDC provider.
type Verifier struct {
	cfg      Config
	provider *oidc.Provider
	verifier *oidc.IDTokenVerifier
}

// Claims holds the parsed and validated token claims.
type Claims struct {
	Subject           string `json:"sub"`
	PreferredUsername string `json:"preferred_username"`
	Issuer            string `json:"iss"`
}

// NewVerifier discovers the OIDC provider at cfg.Issuer and returns a ready Verifier.
func NewVerifier(ctx context.Context, cfg Config) (*Verifier, error) {
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	provider, err := oidc.NewProvider(ctx, cfg.Issuer)
	if err != nil {
		return nil, fmt.Errorf("auth: discover issuer: %w", err)
	}
	v := provider.Verifier(&oidc.Config{ClientID: cfg.Audience})
	return &Verifier{cfg: cfg, provider: provider, verifier: v}, nil
}

// Verify validates raw and returns parsed claims on success.
func (v *Verifier) Verify(ctx context.Context, raw string) (*Claims, error) {
	tok, err := v.verifier.Verify(ctx, raw)
	if err != nil {
		return nil, fmt.Errorf("auth: verify token: %w", err)
	}
	var c Claims
	if err := tok.Claims(&c); err != nil {
		return nil, fmt.Errorf("auth: decode claims: %w", err)
	}
	c.Issuer = tok.Issuer
	c.Subject = tok.Subject
	if c.Subject == "" {
		return nil, fmt.Errorf("auth: missing sub claim")
	}
	return &c, nil
}
```

- [ ] **Step 5: Run test to verify it passes**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go mod tidy && go test ./internal/auth/... -run 'TestVerifier_ValidToken|TestVerifier_Rejections|TestConfig_Validate' -v
```
Expected: PASS - `TestVerifier_ValidToken`, `TestVerifier_Rejections` (5 subtests), `TestConfig_Validate` green.

- [ ] **Step 6: Commit**

```bash
git -C /Users/szymonri/Documents/tatara/tatara-operator add internal/auth/auth.go internal/auth/verifier.go internal/auth/verifier_test.go internal/auth/testjwks go.mod go.sum
git -C /Users/szymonri/Documents/tatara/tatara-operator commit -m "feat: add internal/auth OIDC verifier"
```

---

### Task 10: internal/auth TokenSource (client-credentials)

**Files:**
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/internal/auth/tokensource.go`
- Test: `/Users/szymonri/Documents/tatara/tatara-operator/internal/auth/tokensource_test.go`

Client-credentials grant against the issuer token endpoint, mirroring `tatara-memory-repo-ingester/internal/push/auth.go` (Keycloak `audience` form param). Mints bearer tokens to call tatara-memory and the wrapper; caches until near expiry (handled by `clientcredentials.Config.Token`/`TokenSource`, which caches internally).

- [ ] **Step 1: Write the failing test (httptest token endpoint)**

Create `/Users/szymonri/Documents/tatara/tatara-operator/internal/auth/tokensource_test.go`:
```go
package auth_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-operator/internal/auth"
)

func TestTokenSource_MintsAndSendsAudience(t *testing.T) {
	var gotForm map[string][]string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.NoError(t, r.ParseForm())
		gotForm = r.Form
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"access_token": "minted-token",
			"token_type":   "Bearer",
			"expires_in":   300,
		})
	}))
	defer srv.Close()

	ts := auth.NewTokenSource(auth.TokenSourceConfig{
		TokenURL:     srv.URL,
		ClientID:     "tatara-operator",
		ClientSecret: "shh",
		Audience:     "tatara-memory",
	})

	tok, err := ts.Token(context.Background())
	require.NoError(t, err)
	require.Equal(t, "minted-token", tok)
	require.Equal(t, "client_credentials", gotForm.Get("grant_type"))
	require.Equal(t, "tatara-memory", gotForm.Get("audience"))
}

func TestTokenSource_PropagatesError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "nope", http.StatusUnauthorized)
	}))
	defer srv.Close()

	ts := auth.NewTokenSource(auth.TokenSourceConfig{
		TokenURL:     srv.URL,
		ClientID:     "tatara-operator",
		ClientSecret: "wrong",
		Audience:     "tatara-memory",
	})

	_, err := ts.Token(context.Background())
	require.Error(t, err)
}
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go test ./internal/auth/... -run 'TestTokenSource' -v
```
Expected: FAIL - undefined `auth.NewTokenSource`, `auth.TokenSourceConfig`.

- [ ] **Step 3: Write minimal implementation**

Create `/Users/szymonri/Documents/tatara/tatara-operator/internal/auth/tokensource.go`:
```go
package auth

import (
	"context"
	"fmt"

	"golang.org/x/oauth2"
	"golang.org/x/oauth2/clientcredentials"
)

// TokenSourceConfig configures a client-credentials TokenSource.
type TokenSourceConfig struct {
	TokenURL     string
	ClientID     string
	ClientSecret string
	Audience     string
}

// TokenSource mints bearer tokens via the OIDC client-credentials grant,
// passing the target audience as a Keycloak "audience" form value. Tokens are
// cached internally by the underlying oauth2 source until near expiry.
type TokenSource struct {
	src oauth2.TokenSource
}

// NewTokenSource returns a caching client-credentials TokenSource.
func NewTokenSource(cfg TokenSourceConfig) *TokenSource {
	c := clientcredentials.Config{
		ClientID:     cfg.ClientID,
		ClientSecret: cfg.ClientSecret,
		TokenURL:     cfg.TokenURL,
		EndpointParams: map[string][]string{
			"audience": {cfg.Audience},
		},
	}
	return &TokenSource{src: c.TokenSource(context.Background())}
}

// Token returns a valid bearer access token, refreshing if the cached one is
// near expiry.
func (t *TokenSource) Token(ctx context.Context) (string, error) {
	tok, err := t.src.Token()
	if err != nil {
		return "", fmt.Errorf("auth: mint client-credentials token: %w", err)
	}
	return tok.AccessToken, nil
}
```

Note: the `ctx` parameter on `Token` keeps the signature future-proof for per-call cancellation; the oauth2 source caches across calls. This matches the pin set's "caches until near expiry".

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go mod tidy && go test ./internal/auth/... -run 'TestTokenSource' -v
```
Expected: PASS - `TestTokenSource_MintsAndSendsAudience`, `TestTokenSource_PropagatesError` green.

- [ ] **Step 5: Run the full auth + obs + config + api suite**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go test ./api/... ./internal/... -count=1
```
Expected: PASS - `ok` for `api/v1alpha1`, `internal/auth`, `internal/config`, `internal/obs`, `internal/version`.

- [ ] **Step 6: Commit**

```bash
git -C /Users/szymonri/Documents/tatara/tatara-operator add internal/auth/tokensource.go internal/auth/tokensource_test.go go.mod go.sum
git -C /Users/szymonri/Documents/tatara/tatara-operator commit -m "feat: add internal/auth client-credentials token source"
```

---

### Task 11: cmd/manager/main.go (no-reconciler manager with healthz + metrics)

**Files:**
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/cmd/manager/main.go`
- Test: `/Users/szymonri/Documents/tatara/tatara-operator/cmd/manager/main_test.go`

Wire a controller-runtime Manager, register the `v1alpha1` scheme, expose `/healthz` + `/readyz` and `/metrics`. NO reconcilers registered. The testable seam is a `buildManager` function that constructs the manager from a `config.Config` and a `*runtime.Scheme`; `main` calls it and blocks on `Start`.

- [ ] **Step 1: Write the failing test**

Create `/Users/szymonri/Documents/tatara/tatara-operator/cmd/manager/main_test.go`:
```go
package main

import (
	"testing"

	"k8s.io/apimachinery/pkg/runtime"

	apiv1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
)

func TestNewScheme_RegistersAllKinds(t *testing.T) {
	s := newScheme()
	for _, kind := range []string{"Project", "Repository", "Task", "Subtask"} {
		if !s.Recognizes(apiv1alpha1.GroupVersion.WithKind(kind)) {
			t.Fatalf("scheme does not recognize %s", kind)
		}
	}
}

func TestNewScheme_HasCoreTypes(t *testing.T) {
	s := newScheme()
	gvk := runtime.NewScheme()
	_ = gvk
	// core/v1 Pod must be registered so the manager client can read Secrets/Pods later.
	if !s.Recognizes(corePodGVK()) {
		t.Fatal("scheme does not recognize core/v1 Pod")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go test ./cmd/manager/... -v
```
Expected: FAIL - `no Go files` / undefined `newScheme`, `corePodGVK`.

- [ ] **Step 3: Write minimal implementation**

Create `/Users/szymonri/Documents/tatara/tatara-operator/cmd/manager/main.go`:
```go
package main

import (
	"context"
	"log/slog"
	"os"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/healthz"
	"sigs.k8s.io/controller-runtime/pkg/manager"
	metricsserver "sigs.k8s.io/controller-runtime/pkg/metrics/server"

	apiv1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/config"
	"github.com/szymonrychu/tatara-operator/internal/obs"
	"github.com/szymonrychu/tatara-operator/internal/version"
)

func newScheme() *runtime.Scheme {
	s := runtime.NewScheme()
	utilRuntimeMust(clientgoscheme.AddToScheme(s))
	utilRuntimeMust(apiv1alpha1.AddToScheme(s))
	return s
}

func corePodGVK() schema.GroupVersionKind {
	return corev1.SchemeGroupVersion.WithKind("Pod")
}

func utilRuntimeMust(err error) {
	if err != nil {
		panic(err)
	}
}

func buildManager(cfg config.Config, scheme *runtime.Scheme) (manager.Manager, error) {
	return ctrl.NewManager(ctrl.GetConfigOrDie(), manager.Options{
		Scheme: scheme,
		Metrics: metricsserver.Options{
			BindAddress: cfg.MetricsAddr,
		},
		HealthProbeBindAddress: cfg.InternalAddr,
	})
}

func run(ctx context.Context, logger *slog.Logger) error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	mgr, err := buildManager(cfg, newScheme())
	if err != nil {
		return err
	}
	if err := mgr.AddHealthzCheck("healthz", healthz.Ping); err != nil {
		return err
	}
	if err := mgr.AddReadyzCheck("readyz", healthz.Ping); err != nil {
		return err
	}
	// No reconcilers registered in M0; controllers land in M1-M4.
	logger.Info("starting manager",
		slog.String("action", "manager_start"),
		slog.String("version", version.String()),
		slog.String("metrics_addr", cfg.MetricsAddr),
	)
	return mgr.Start(ctx)
}

func main() {
	logger := obs.NewLogger(os.Stdout, slog.LevelInfo)
	ctrl.SetLogger(slogToLogr(logger))
	if err := run(ctrl.SetupSignalHandler(), logger); err != nil {
		logger.Error("manager exited with error", slog.String("error", err.Error()))
		os.Exit(1)
	}
}
```

The `slogToLogr` adapter bridges the controller-runtime `logr.Logger` requirement to our slog handler. Add it in the same package:

Create `/Users/szymonri/Documents/tatara/tatara-operator/cmd/manager/logr.go`:
```go
package main

import (
	"log/slog"

	"github.com/go-logr/logr"
)

func slogToLogr(l *slog.Logger) logr.Logger {
	return logr.FromSlogHandler(l.Handler())
}
```

- [ ] **Step 4: Add the go-logr dependency if not already present**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go get github.com/go-logr/logr@latest && go mod tidy
```
Expected: `github.com/go-logr/logr` resolves (it is already a transitive dep of controller-runtime; this promotes it to direct).

- [ ] **Step 5: Run test to verify it passes**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go test ./cmd/manager/... -v
```
Expected: PASS - `TestNewScheme_RegistersAllKinds`, `TestNewScheme_HasCoreTypes` green. (These test pure functions; they do not start the manager, so no kubeconfig is required.)

- [ ] **Step 6: Verify the binary compiles**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && go build -o /dev/null ./cmd/manager
```
Expected: no output, exit 0.

- [ ] **Step 7: Commit**

```bash
git -C /Users/szymonri/Documents/tatara/tatara-operator add cmd/manager go.mod go.sum
git -C /Users/szymonri/Documents/tatara/tatara-operator commit -m "feat: add no-reconciler manager entrypoint with healthz and metrics"
```

---

### Task 12: Makefile

**Files:**
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/Makefile`

Targets: `generate`, `manifests`, `test`, `lint`, `build`, `image`, plus helpers (`fmt`, `tidy`, `envtest-bin`). `manifests` writes CRDs into the chart's `crds/` dir. `test` runs unit tests plus envtest via `setup-envtest`. This is a scaffold task (not test-first); verification is running the targets.

- [ ] **Step 1: Write the Makefile**

Create `/Users/szymonri/Documents/tatara/tatara-operator/Makefile`:
```makefile
SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c

REGISTRY ?= harbor.szymonrichert.pl
IMAGE_NAME ?= containers/tatara-operator
VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
COMMIT ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
DATE ?= $(shell date -u +%Y-%m-%dT%H:%M:%SZ)

IMAGE_REF := $(REGISTRY)/$(IMAGE_NAME):$(VERSION)

CONTROLLER_GEN_VERSION ?= v0.18.0
CONTROLLER_GEN ?= go run sigs.k8s.io/controller-tools/cmd/controller-gen@$(CONTROLLER_GEN_VERSION)

ENVTEST_VERSION ?= release-0.21
ENVTEST ?= go run sigs.k8s.io/controller-runtime/tools/setup-envtest@$(ENVTEST_VERSION)
ENVTEST_K8S_VERSION ?= 1.33.0

CHART_CRD_DIR := charts/tatara-operator/crds

# Resolve helm binary via mise to avoid homebrew helm 4.x shadow.
HELM_BIN := $(shell mise exec -- bash -c 'echo $$PATH' | tr ':' '\n' | grep -m1 'mise/installs/helm')
ifdef HELM_BIN
HELM_BIN := $(HELM_BIN)/helm
else
HELM_BIN := helm
endif

.PHONY: all generate manifests test lint build image fmt tidy chart-lint clean ci

all: generate manifests lint test build

generate:
	$(CONTROLLER_GEN) object:headerFile="hack/boilerplate.go.txt" paths="./api/..."

manifests:
	mkdir -p $(CHART_CRD_DIR)
	$(CONTROLLER_GEN) crd paths="./api/..." output:crd:artifacts:config=$(CHART_CRD_DIR)

fmt:
	gofmt -s -w .

tidy:
	go mod tidy

lint:
	golangci-lint run ./... || [ $$? -eq 5 ]

test:
	KUBEBUILDER_ASSETS="$$($(ENVTEST) use $(ENVTEST_K8S_VERSION) -p path)" \
		go test ./... -race -count=1

build:
	CGO_ENABLED=0 go build \
		-trimpath \
		-ldflags "-s -w \
		  -X github.com/szymonrychu/tatara-operator/internal/version.Version=$(VERSION) \
		  -X github.com/szymonrychu/tatara-operator/internal/version.Commit=$(COMMIT) \
		  -X github.com/szymonrychu/tatara-operator/internal/version.Date=$(DATE)" \
		-o bin/tatara-operator \
		./cmd/manager

image:
	docker buildx build \
		--platform=linux/amd64 \
		--build-arg VERSION=$(VERSION) \
		--build-arg COMMIT=$(COMMIT) \
		--build-arg DATE=$(DATE) \
		-t $(IMAGE_REF) \
		--load \
		.

chart-lint:
	$(HELM_BIN) lint charts/tatara-operator

ci: generate manifests lint test

clean:
	rm -rf bin dist
```

- [ ] **Step 2: Verify `make generate` is a no-op against committed deepcopy**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && make generate && git status --porcelain api/v1alpha1/zz_generated.deepcopy.go
```
Expected: no output from `git status` (the generated file matches what Task 6 committed).

- [ ] **Step 3: Verify `make manifests` writes CRDs into the chart**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && make manifests && ls charts/tatara-operator/crds/
```
Expected: four files - `tatara.dev_projects.yaml`, `tatara.dev_repositories.yaml`, `tatara.dev_tasks.yaml`, `tatara.dev_subtasks.yaml`.

- [ ] **Step 4: Verify `make test` runs (envtest provisions a control plane)**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && make test
```
Expected: `setup-envtest` downloads the 1.33.0 assets on first run, then all packages report `ok`. (No envtest-backed suites exist in M0; the `KUBEBUILDER_ASSETS` wiring is proven by the command succeeding and being available for M1.)

- [ ] **Step 5: Commit**

```bash
git -C /Users/szymonri/Documents/tatara/tatara-operator add Makefile charts/tatara-operator/crds
git -C /Users/szymonri/Documents/tatara/tatara-operator commit -m "build: add Makefile with generate/manifests/test/lint/build/image targets"
```

---

### Task 13: Dockerfile (multi-stage, distroless static, non-root)

**Files:**
- Create: `/Users/szymonri/Documents/tatara/tatara-operator/Dockerfile`

Mirrors the tatara-chat Dockerfile style: alpine builder, distroless static-debian12 nonroot runtime, ldflags from version vars. Scaffold task; verification is a build.

- [ ] **Step 1: Write the Dockerfile**

Create `/Users/szymonri/Documents/tatara/tatara-operator/Dockerfile`:
```dockerfile
# syntax=docker/dockerfile:1.7

ARG GO_VERSION=1.25
FROM golang:${GO_VERSION}-alpine AS builder

WORKDIR /src
RUN apk add --no-cache git ca-certificates

COPY go.mod go.sum ./
RUN go mod download

COPY . .

RUN mkdir -p /out

ARG VERSION=dev
ARG COMMIT=unknown
ARG DATE=unknown

RUN CGO_ENABLED=0 GOOS=linux go build \
    -trimpath \
    -ldflags "-s -w \
      -X github.com/szymonrychu/tatara-operator/internal/version.Version=${VERSION} \
      -X github.com/szymonrychu/tatara-operator/internal/version.Commit=${COMMIT} \
      -X github.com/szymonrychu/tatara-operator/internal/version.Date=${DATE}" \
    -o /out/tatara-operator \
    ./cmd/manager

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=builder /out/tatara-operator /tatara-operator
USER nonroot:nonroot
EXPOSE 8080 8081 9090
ENTRYPOINT ["/tatara-operator"]
```

- [ ] **Step 2: Verify the image builds**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && docker buildx build --build-arg VERSION=dev -t tatara-operator:dev --load .
```
Expected: build succeeds; final stage is distroless static nonroot. (If a local Docker daemon is unavailable, instead verify the build stage compiles with `go build -o /dev/null ./cmd/manager` and note the image build as deferred to CI.)

- [ ] **Step 3: Commit**

```bash
git -C /Users/szymonri/Documents/tatara/tatara-operator add Dockerfile
git -C /Users/szymonri/Documents/tatara/tatara-operator commit -m "build: add multi-stage distroless Dockerfile"
```

---

### Task 14: Helm chart skeleton (helm create, stripped, cluster-agnostic)

**Files:**
- Create (via `helm create`): `/Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator/...`
- Final kept files:
  - `charts/tatara-operator/Chart.yaml`
  - `charts/tatara-operator/values.yaml`
  - `charts/tatara-operator/templates/_helpers.tpl`
  - `charts/tatara-operator/templates/deployment.yaml`
  - `charts/tatara-operator/templates/serviceaccount.yaml`
  - `charts/tatara-operator/templates/rbac.yaml`
  - `charts/tatara-operator/templates/configmap.yaml`
  - `charts/tatara-operator/templates/secret.yaml`
  - `charts/tatara-operator/templates/service.yaml`
  - `charts/tatara-operator/crds/*.yaml` (from Task 12 `make manifests`)

Chart created via `helm create` (rule 5), then stripped. Cluster-agnostic (rule 14): no baked imagePullSecrets, affinity, ingress host/class, storage class, or replicated-secret names in `values.yaml`. `values.yaml` holds only camelCase scalars (rule 6) mapped to kebab-case ConfigMap/Secret keys consumed via `envFrom`. Full hardening (NetworkPolicy for spawned pods, ServiceMonitor, ingress) is M6; M0 only needs `helm lint` clean.

- [ ] **Step 1: Scaffold the chart with helm create**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && make manifests >/dev/null 2>&1 || true
helm create charts/tatara-operator
```
This generates the default chart. Note the CRDs from Task 12 already live in `charts/tatara-operator/crds/` and `helm create` does not touch that dir.

- [ ] **Step 2: Remove default template files not used by M0**

Run:
```bash
rm -rf /Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator/templates/tests
rm -f /Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator/templates/hpa.yaml
rm -f /Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator/templates/ingress.yaml
rm -f /Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator/templates/NOTES.txt
rm -f /Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator/templates/serviceaccount.yaml
```
(The default `serviceaccount.yaml` is replaced below with a simpler one; `deployment.yaml`, `service.yaml`, `_helpers.tpl` are overwritten in later steps.)

- [ ] **Step 3: Overwrite Chart.yaml**

Create `/Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator/Chart.yaml`:
```yaml
apiVersion: v2
name: tatara-operator
description: Kubernetes operator orchestrating the tatara agentic-development loop
type: application
version: 0.1.0
appVersion: "0.1.0"
```

- [ ] **Step 4: Overwrite values.yaml (camelCase scalars only, rule 6 + rule 14)**

Create `/Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator/values.yaml`:
```yaml
# Image. tag defaults to chart appVersion when empty.
image:
  repository: harbor.szymonrichert.pl/containers/tatara-operator
  tag: ""
  pullPolicy: IfNotPresent

replicaCount: 1

# Listener addresses (camelCase scalar -> kebab-case ConfigMap key -> envFrom).
httpAddr: ":8080"
metricsAddr: ":9090"
internalAddr: ":8081"

# OIDC.
oidcIssuer: ""
oidcAudience: "tatara-operator"

# Upstreams and identities.
memoryBaseURL: ""
ingesterImage: ""
externalWebhookBase: ""
operatorOidcClientId: "tatara-operator"

# Secret-shaped scalars (rendered into the Secret).
operatorOidcClientSecret: ""

# Names of pre-existing secrets the operator references for spawned pods.
anthropicSecretName: ""
cliOidcSecretName: ""

logLevel: "info"

resources:
  requests:
    cpu: 50m
    memory: 128Mi
  limits:
    cpu: 500m
    memory: 256Mi

serviceAccount:
  create: true
  name: ""
```

- [ ] **Step 5: Overwrite _helpers.tpl**

Create `/Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator/templates/_helpers.tpl`:
```yaml
{{- define "tatara-operator.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "tatara-operator.fullname" -}}
{{- printf "%s" (include "tatara-operator.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "tatara-operator.labels" -}}
app.kubernetes.io/name: {{ include "tatara-operator.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "tatara-operator.selectorLabels" -}}
app.kubernetes.io/name: {{ include "tatara-operator.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "tatara-operator.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "tatara-operator.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}
```

- [ ] **Step 6: Write the ConfigMap (kebab-case keys from camelCase scalars)**

Create `/Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator/templates/configmap.yaml`:
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "tatara-operator.fullname" . }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
data:
  HTTP_ADDR: {{ .Values.httpAddr | quote }}
  METRICS_ADDR: {{ .Values.metricsAddr | quote }}
  INTERNAL_ADDR: {{ .Values.internalAddr | quote }}
  OIDC_ISSUER: {{ .Values.oidcIssuer | quote }}
  OIDC_AUDIENCE: {{ .Values.oidcAudience | quote }}
  MEMORY_BASE_URL: {{ .Values.memoryBaseURL | quote }}
  INGESTER_IMAGE: {{ .Values.ingesterImage | quote }}
  EXTERNAL_WEBHOOK_BASE: {{ .Values.externalWebhookBase | quote }}
  OPERATOR_OIDC_CLIENT_ID: {{ .Values.operatorOidcClientId | quote }}
  ANTHROPIC_SECRET_NAME: {{ .Values.anthropicSecretName | quote }}
  CLI_OIDC_SECRET_NAME: {{ .Values.cliOidcSecretName | quote }}
  LOG_LEVEL: {{ .Values.logLevel | quote }}
```

- [ ] **Step 7: Write the Secret**

Create `/Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator/templates/secret.yaml`:
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "tatara-operator.fullname" . }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
type: Opaque
stringData:
  OPERATOR_OIDC_CLIENT_SECRET: {{ .Values.operatorOidcClientSecret | quote }}
```

- [ ] **Step 8: Write the ServiceAccount**

Create `/Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator/templates/serviceaccount.yaml`:
```yaml
{{- if .Values.serviceAccount.create -}}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ include "tatara-operator.serviceAccountName" . }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
{{- end -}}
```

- [ ] **Step 9: Write the RBAC (ClusterRole + ClusterRoleBinding)**

Create `/Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator/templates/rbac.yaml`:
```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: {{ include "tatara-operator.fullname" . }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
rules:
  - apiGroups: ["tatara.dev"]
    resources: ["projects", "repositories", "tasks", "subtasks"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: ["tatara.dev"]
    resources: ["projects/status", "repositories/status", "tasks/status", "subtasks/status"]
    verbs: ["get", "update", "patch"]
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: [""]
    resources: ["pods", "services"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["networking.k8s.io"]
    resources: ["networkpolicies"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: [""]
    resources: ["events"]
    verbs: ["create", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: {{ include "tatara-operator.fullname" . }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: {{ include "tatara-operator.fullname" . }}
subjects:
  - kind: ServiceAccount
    name: {{ include "tatara-operator.serviceAccountName" . }}
    namespace: {{ .Release.Namespace }}
```

- [ ] **Step 10: Write the Service (REST/webhook + metrics ports)**

Create `/Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator/templates/service.yaml`:
```yaml
apiVersion: v1
kind: Service
metadata:
  name: {{ include "tatara-operator.fullname" . }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
spec:
  type: ClusterIP
  selector:
    {{- include "tatara-operator.selectorLabels" . | nindent 4 }}
  ports:
    - name: http
      port: 8080
      targetPort: http
    - name: metrics
      port: 9090
      targetPort: metrics
```

- [ ] **Step 11: Write the Deployment (envFrom ConfigMap + Secret, rule 6)**

Create `/Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator/templates/deployment.yaml`:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "tatara-operator.fullname" . }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.replicaCount }}
  selector:
    matchLabels:
      {{- include "tatara-operator.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "tatara-operator.selectorLabels" . | nindent 8 }}
    spec:
      serviceAccountName: {{ include "tatara-operator.serviceAccountName" . }}
      securityContext:
        runAsNonRoot: true
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: manager
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          envFrom:
            - configMapRef:
                name: {{ include "tatara-operator.fullname" . }}
            - secretRef:
                name: {{ include "tatara-operator.fullname" . }}
          ports:
            - name: http
              containerPort: 8080
            - name: internal
              containerPort: 8081
            - name: metrics
              containerPort: 9090
          livenessProbe:
            httpGet:
              path: /healthz
              port: internal
            initialDelaySeconds: 10
            periodSeconds: 20
          readinessProbe:
            httpGet:
              path: /readyz
              port: internal
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
```

- [ ] **Step 12: Lint the chart**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && make chart-lint
```
Expected: `1 chart(s) linted, 0 chart(s) failed`. (If `helm` is unavailable via mise, run `helm lint charts/tatara-operator` directly.)

- [ ] **Step 13: Verify the chart templates render with required values set**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && helm template t charts/tatara-operator \
  --set oidcIssuer=https://kc/realms/tatara \
  --set memoryBaseURL=http://tatara-memory:8080 | grep -E "kind: (Deployment|ClusterRole|ConfigMap|Secret|Service|ServiceAccount|CustomResourceDefinition)"
```
Expected: lines for `CustomResourceDefinition` (x4 from `crds/`), `ServiceAccount`, `ClusterRole`, `ClusterRoleBinding`, `ConfigMap`, `Secret`, `Service`, `Deployment`.

- [ ] **Step 14: Commit**

```bash
git -C /Users/szymonri/Documents/tatara/tatara-operator add charts/tatara-operator
git -C /Users/szymonri/Documents/tatara/tatara-operator commit -m "feat: add cluster-agnostic helm chart skeleton with CRDs and RBAC"
```

---

### Task 15: Baseline verification (full pipeline green)

**Files:** none created. This task runs the full M0 acceptance gate.

- [ ] **Step 1: Format and tidy**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && make fmt && make tidy && git diff --stat
```
Expected: no unexpected diffs (or only formatting already applied). Commit any `go.mod`/`go.sum` tidy changes if present.

- [ ] **Step 2: Generate + manifests are clean (no drift)**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && make generate && make manifests && git status --porcelain
```
Expected: empty output - committed generated code and CRDs match a fresh generation.

- [ ] **Step 3: Lint passes**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && make lint
```
Expected: golangci-lint reports no issues (exit 0, or exit 5 "no Go files" tolerated by the target wrapper which only triggers on empty package sets - here it should be a clean 0).

- [ ] **Step 4: Full test suite green**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && make test
```
Expected: `ok` for `api/v1alpha1`, `cmd/manager`, `internal/auth`, `internal/config`, `internal/obs`, `internal/version`.

- [ ] **Step 5: Build the binary**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && make build && ls -l bin/tatara-operator
```
Expected: `bin/tatara-operator` exists and is executable.

- [ ] **Step 6: Chart lint clean**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && make chart-lint
```
Expected: `1 chart(s) linted, 0 chart(s) failed`.

- [ ] **Step 7: Update MEMORY.md / ROADMAP.md and push**

Append to `/Users/szymonri/Documents/tatara/tatara-operator/MEMORY.md`:
```markdown
- 2026-06-06 M0 scaffold complete: api/v1alpha1 (4 kinds + deepcopy),
  internal/{config,obs,auth}, no-reconciler manager, Dockerfile, Makefile
  (generate/manifests/test/lint/build/image), chart skeleton lint-clean with
  CRDs + RBAC. `make generate && make manifests && make test` green;
  `helm lint charts/tatara-operator` clean.
```
ROADMAP.md M0 line is already marked `[x]` in Task 1; leave as is.

Run:
```bash
git -C /Users/szymonri/Documents/tatara/tatara-operator add MEMORY.md
git -C /Users/szymonri/Documents/tatara/tatara-operator commit -m "docs: record M0 scaffold completion in MEMORY"
git -C /Users/szymonri/Documents/tatara/tatara-operator push -u origin HEAD
```
Expected: push succeeds. Per CLAUDE.md rule 10, merge this branch back to `main` before any build/deploy.

---

## Self-review notes

- **Spec coverage:** Repo creation + canonical files (Task 1) covers spec scope item 1; go.mod (Task 2) item 2; api/v1alpha1 four types + deepcopy (Tasks 3-6) item 3 with EXACT spec field names; Makefile (Task 12) item 4; obs/auth/config (Tasks 7-10) item 5 with EXACT pin-set config keys, metric names, and `Verifier`/`TokenSource` signatures; manager (Task 11) item 6 with no reconcilers; Dockerfile (Task 13) item 7; chart skeleton (Task 14) item 8 cluster-agnostic with the required RBAC verbs (jobs, pods/services, secrets-read, networkpolicies, CRDs); baseline verification (Task 15) item 9.
- **Pin-set fidelity:** module `github.com/szymonrychu/tatara-operator`; group `tatara.dev/v1alpha1`; config keys `HTTP_ADDR/METRICS_ADDR/INTERNAL_ADDR/OIDC_ISSUER/OIDC_AUDIENCE/MEMORY_BASE_URL/INGESTER_IMAGE/EXTERNAL_WEBHOOK_BASE/OPERATOR_OIDC_CLIENT_ID/OPERATOR_OIDC_CLIENT_SECRET/ANTHROPIC_SECRET_NAME/CLI_OIDC_SECRET_NAME/LOG_LEVEL`; metrics `operator_reconcile_total{kind,result}`, `operator_ingest_job_duration_seconds`, `operator_turn_duration_seconds`, `operator_webhook_events_total{provider,kind,result}`, `operator_tasks_inflight`; file layout matches the pin-set canonical paths.
- **Deferred to later milestones (correctly out of M0):** reconcilers (`internal/controller`), `internal/ingest`, `internal/agent`, `internal/webhook`, `internal/restapi`, `internal/scm`, NetworkPolicy templates, ServiceMonitor, ingress, Keycloak client, infra helmfile release.
