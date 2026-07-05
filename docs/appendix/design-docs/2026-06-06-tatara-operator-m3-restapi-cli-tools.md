# tatara-operator M3 (OIDC REST API + tatara-cli MCP tool group) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Each task is a full TDD cycle: write the failing test (full code), run it and see it FAIL, write the minimal implementation (full code), run it and see it PASS, then commit. Do not batch tasks. Do not skip the RED step.

**Goal:** Build M3 of `tatara-operator`: an OIDC-gated REST API (`internal/restapi`) exposing read/write CRUD over the four CRDs (Project, Repository, Task, Subtask), backed by the controller-runtime client, sharing the `HTTP_ADDR` listener with the M2 webhook server; and the matching `tatara-cli` MCP tool group (`project_*`, `repo_*`, `task_*`, `subtask_*`) that maps the REST endpoints 1:1, shipped as its own tatara-cli release.

**Architecture:** Part A lives in the `tatara-operator` repo. A chi router (`internal/restapi/server.go`) mounts the REST routes behind the shared `internal/auth.Verifier` middleware (audience `tatara-operator`), and is composed onto the SAME `*chi.Mux` the M2 webhook server already created on `HTTP_ADDR` (webhook paths under `/operator/webhooks/...`, REST paths under the bare paths in the pin set). Handlers (`internal/restapi/handlers.go`) are methods on a `Server` struct holding a `sigs.k8s.io/controller-runtime/pkg/client.Client`; each handler lists/gets/patches/creates the relevant CRD via that client and marshals CRD spec/status into stable JSON DTOs. Tests use `httptest` + the controller-runtime `fake` client seeded with CRD objects, with auth bypassed (the `Verify` middleware is injected as nil/passthrough in tests, exactly as tatara-chat's router tests do). Part B lives in the `tatara-cli` repo: a second `internal/mcp` tool group registered against a SECOND `client.Client` bound to the operator base URL + operator audience, mirroring the existing memory tool-registration style precisely.

**Tech Stack:** Go 1.25.x, `sigs.k8s.io/controller-runtime/pkg/client` + `.../client/fake`, `github.com/go-chi/chi/v5`, `github.com/coreos/go-oidc/v3` (via the existing `internal/auth`), `github.com/prometheus/client_golang`, `github.com/stretchr/testify`. tatara-cli: `github.com/mark3labs/mcp-go`, `github.com/stretchr/testify`.

**Spec:** `docs/superpowers/specs/2026-06-06-tatara-operator-design.md`
**Pin set (authoritative names/paths - obey exactly):** `docs/superpowers/plans/_tatara-operator-shared-contracts.md`

**Reference sources on disk (read these, do not guess):**
- REST/router conventions: `~/Documents/tatara/tatara-chat/internal/httpapi/` (`router.go`, `errors.go`, `router_test.go`).
- OIDC middleware: `~/Documents/tatara/tatara-chat/internal/auth/` (`middleware.go`, `verifier.go`, `auth.go`). The operator's `internal/auth` (built in M0) mirrors this; reuse its `Verifier` + `Middleware`.
- tatara-cli tool style (MUST mirror exactly): `~/Documents/tatara/tatara-cli/internal/mcp/tools.go`, `server.go`, `tools_test.go`; `~/Documents/tatara/tatara-cli/internal/client/{client,config}.go`; `~/Documents/tatara/tatara-cli/internal/cmd/mcp.go`.

**Repo dirs / module paths:**
- Operator: `~/Documents/tatara/tatara-operator/`, module `github.com/szymonrychu/tatara-operator`.
- CLI: `~/Documents/tatara/tatara-cli/`, module `github.com/szymonrychu/tatara-cli`.

**Preconditions (built in M0-M2, assumed present):**
- `api/v1alpha1/{project,repository,task,subtask}_types.go` with the exact spec/status fields from the spec "CRD model" section, plus `zz_generated.deepcopy.go` and a registered scheme (`AddToScheme`).
- `internal/auth.Verifier` (OIDC, audience `tatara-operator`) and `internal/auth.Middleware(v *Verifier) func(http.Handler) http.Handler`.
- `internal/config.Config` with `HTTPAddr` (from `HTTP_ADDR`), `OIDCIssuer`, `OIDCAudience`, plus a `Namespace` field (default `tatara`).
- `internal/webhook/server.go` exposing a constructor that mounts webhook routes onto a `*chi.Mux` under `/operator/webhooks/...`. M3 composes REST routes onto the same mux; if M2 built a standalone `*chi.Mux`, M3 Task 1 refactors the listener so both route groups share it (see Task 8).
- `internal/obs` providing a `*slog.Logger` and a `*prometheus.Registry`.

**Cross-repo note:** Part A and Part B are independent repos and can be built in parallel by separate subagents (rule 9). They are wired together only at deploy time (M6) via mcp-config. Part B ships as its own tatara-cli release (`vX.Y.0`); state this in the tatara-cli commit + ROADMAP.

**DTO mapping (authoritative - all handlers use these JSON shapes).** JSON keys are camelCase matching the CRD spec/status field names. Each DTO carries `name` (the CRD metadata.name) as its identifier. Defined once in Task 2, reused by every handler:

```
ProjectDTO     { name, scmSecretRef, triggerLabel, maxConcurrentTasks,
                 agent{model,image,permissionMode,maxTurnsPerTask,turnTimeoutSeconds},
                 status{webhookURL, conditions} }
RepositoryDTO  { name, projectRef, url, defaultBranch, ingestEnabled,
                 status{phase, lastIngestedCommit, lastIngestTime, jobName, conditions} }
TaskDTO        { name, projectRef, repositoryRef, goal,
                 source{provider,issueRef,url}, maxTurns,
                 status{phase, podName, turnsCompleted, prURL, resultSummary, conditions} }
SubtaskDTO     { name, taskRef, title, detail, order,
                 status{phase, turnId, result} }
```

`conditions` is `[]metav1.Condition` re-marshaled as-is (already JSON-tagged by apimachinery). `lastIngestTime` is the RFC3339 string from `metav1.Time` (zero value -> omitted via `omitempty`).

---

## Task 1: REST server scaffold (chi router group + Server struct)

**Files:**
- Create: `~/Documents/tatara/tatara-operator/internal/restapi/server.go`
- Create: `~/Documents/tatara/tatara-operator/internal/restapi/server_test.go`

- [ ] **Step 1: Write the failing test** (`internal/restapi/server_test.go`)

```go
package restapi_test

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/stretchr/testify/require"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/restapi"
)

// newTestServer returns a restapi.Server backed by a fake client seeded with objs,
// and a chi router that mounts the REST routes with auth DISABLED (Verify=nil).
func newTestServer(t *testing.T, objs ...client.Object) (*restapi.Server, *chi.Mux) {
	t.Helper()
	scheme := runtime.NewScheme()
	require.NoError(t, tatarav1alpha1.AddToScheme(scheme))
	fc := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(objs...).
		WithStatusSubresource(&tatarav1alpha1.Project{}, &tatarav1alpha1.Repository{},
			&tatarav1alpha1.Task{}, &tatarav1alpha1.Subtask{}).
		Build()
	s := restapi.NewServer(restapi.Config{Client: fc, Namespace: "tatara"})
	r := chi.NewRouter()
	s.Mount(r, nil) // nil Verify => no auth middleware (tests)
	return s, r
}

func TestServer_AuthGate(t *testing.T) {
	deny := func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusUnauthorized)
		})
	}
	scheme := runtime.NewScheme()
	require.NoError(t, tatarav1alpha1.AddToScheme(scheme))
	fc := fake.NewClientBuilder().WithScheme(scheme).Build()
	s := restapi.NewServer(restapi.Config{Client: fc, Namespace: "tatara"})
	r := chi.NewRouter()
	s.Mount(r, deny)

	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/projects", nil))
	require.Equal(t, http.StatusUnauthorized, w.Code)
	_ = context.Background()
}
```

Add the missing imports the test references (`runtime "k8s.io/apimachinery/pkg/runtime"`, `client "sigs.k8s.io/controller-runtime/pkg/client"`). Keep them in the import block above; they are listed separately here only for clarity.

- [ ] **Step 2: Run the test, expect FAIL (package does not compile)**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/restapi/...
```

Expected: `FAIL` - `package github.com/szymonrychu/tatara-operator/internal/restapi: no Go files` / undefined `restapi.NewServer`, `restapi.Config`, `restapi.Server`.

- [ ] **Step 3: Write the minimal implementation** (`internal/restapi/server.go`)

```go
package restapi

import (
	"net/http"

	"github.com/go-chi/chi/v5"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

// Config holds the REST server dependencies.
type Config struct {
	Client    client.Client
	Namespace string
}

// Server exposes OIDC-gated CRUD over the tatara CRDs, backed by the
// controller-runtime client. It shares the HTTP_ADDR listener with the
// webhook server; callers mount it onto a shared chi router.
type Server struct {
	c  client.Client
	ns string
}

// NewServer constructs a Server from cfg.
func NewServer(cfg Config) *Server {
	return &Server{c: cfg.Client, ns: cfg.Namespace}
}

// Mount registers the REST routes on r. verify is the OIDC middleware;
// when nil, routes are mounted without auth (tests only). The routes use
// the bare paths from the pin set so they do not collide with the
// webhook server's /operator/webhooks/... prefix on the same listener.
func (s *Server) Mount(r chi.Router, verify func(http.Handler) http.Handler) {
	r.Group(func(r chi.Router) {
		if verify != nil {
			r.Use(verify)
		}
		s.routes(r)
	})
}

// routes wires every REST endpoint. Handlers are filled in by later tasks.
func (s *Server) routes(r chi.Router) {
	r.Get("/projects", s.listProjects)
	r.Get("/projects/{p}", s.getProject)
	r.Get("/projects/{p}/repositories", s.listRepositories)
	r.Get("/projects/{p}/tasks", s.listTasks)
	r.Get("/tasks/{t}", s.getTask)
	r.Patch("/tasks/{t}", s.patchTask)
	r.Get("/tasks/{t}/subtasks", s.listSubtasks)
	r.Post("/tasks/{t}/subtasks", s.createSubtask)
	r.Patch("/subtasks/{s}", s.patchSubtask)
}
```

Also create a thin `handlers.go` stub so the package compiles. Each handler is implemented for real in its own task; here they are `501` stubs:

```go
package restapi

import "net/http"

func (s *Server) listProjects(w http.ResponseWriter, r *http.Request)     { notImplemented(w) }
func (s *Server) getProject(w http.ResponseWriter, r *http.Request)        { notImplemented(w) }
func (s *Server) listRepositories(w http.ResponseWriter, r *http.Request)  { notImplemented(w) }
func (s *Server) listTasks(w http.ResponseWriter, r *http.Request)         { notImplemented(w) }
func (s *Server) getTask(w http.ResponseWriter, r *http.Request)           { notImplemented(w) }
func (s *Server) patchTask(w http.ResponseWriter, r *http.Request)         { notImplemented(w) }
func (s *Server) listSubtasks(w http.ResponseWriter, r *http.Request)      { notImplemented(w) }
func (s *Server) createSubtask(w http.ResponseWriter, r *http.Request)     { notImplemented(w) }
func (s *Server) patchSubtask(w http.ResponseWriter, r *http.Request)      { notImplemented(w) }

func notImplemented(w http.ResponseWriter) { w.WriteHeader(http.StatusNotImplemented) }
```

If `go-chi/chi/v5` and the controller-runtime fake client are not yet in `go.mod`, add them: `go get github.com/go-chi/chi/v5@v5.3.0 sigs.k8s.io/controller-runtime@latest` then `go mod tidy`.

- [ ] **Step 4: Run the test, expect PASS**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/restapi/...
```

Expected: `ok` (the auth-gate test passes; `/projects` returns 401 under deny, and the seeded-server helper compiles).

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-operator && git checkout -b feat/m3-restapi
gofmt -w internal/restapi && golangci-lint run ./internal/restapi/...
git add internal/restapi go.mod go.sum && git commit -m "feat: restapi server scaffold with auth-gated chi route group"
```

---

## Task 2: JSON DTOs + CRD mapping helpers

**Files:**
- Create: `~/Documents/tatara/tatara-operator/internal/restapi/dto.go`
- Create: `~/Documents/tatara/tatara-operator/internal/restapi/dto_test.go`

- [ ] **Step 1: Write the failing test** (`internal/restapi/dto_test.go`)

```go
package restapi

import (
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
)

func TestToProjectDTO(t *testing.T) {
	p := tatarav1alpha1.Project{
		ObjectMeta: metav1.ObjectMeta{Name: "demo"},
		Spec: tatarav1alpha1.ProjectSpec{
			ScmSecretRef: "demo-scm", TriggerLabel: "tatara", MaxConcurrentTasks: 3,
			Agent: tatarav1alpha1.AgentSpec{Model: "claude", Image: "img:1",
				PermissionMode: "bypassPermissions", MaxTurnsPerTask: 50, TurnTimeoutSeconds: 1800},
		},
		Status: tatarav1alpha1.ProjectStatus{WebhookURL: "https://x/operator/webhooks/demo"},
	}
	d := toProjectDTO(p)
	require.Equal(t, "demo", d.Name)
	require.Equal(t, "tatara", d.TriggerLabel)
	require.Equal(t, 3, d.MaxConcurrentTasks)
	require.Equal(t, "claude", d.Agent.Model)
	require.Equal(t, "https://x/operator/webhooks/demo", d.Status.WebhookURL)
}

func TestToTaskDTO_Source(t *testing.T) {
	task := tatarav1alpha1.Task{
		ObjectMeta: metav1.ObjectMeta{Name: "t1"},
		Spec: tatarav1alpha1.TaskSpec{
			ProjectRef: "demo", RepositoryRef: "repo", Goal: "do the thing",
			Source: &tatarav1alpha1.TaskSource{Provider: "github", IssueRef: "o/r#1", URL: "https://gh/1"},
		},
		Status: tatarav1alpha1.TaskStatus{Phase: "Running", TurnsCompleted: 2},
	}
	d := toTaskDTO(task)
	require.Equal(t, "do the thing", d.Goal)
	require.NotNil(t, d.Source)
	require.Equal(t, "github", d.Source.Provider)
	require.Equal(t, "Running", d.Status.Phase)
	require.Equal(t, 2, d.Status.TurnsCompleted)
}

func TestToSubtaskDTO(t *testing.T) {
	st := tatarav1alpha1.Subtask{
		ObjectMeta: metav1.ObjectMeta{Name: "s1"},
		Spec:       tatarav1alpha1.SubtaskSpec{TaskRef: "t1", Title: "step", Detail: "d", Order: 1},
		Status:     tatarav1alpha1.SubtaskStatus{Phase: "Done", TurnID: "turn-9", Result: "ok"},
	}
	d := toSubtaskDTO(st)
	require.Equal(t, "s1", d.Name)
	require.Equal(t, 1, d.Order)
	require.Equal(t, "Done", d.Status.Phase)
	require.Equal(t, "turn-9", d.Status.TurnID)
	_ = time.Now()
}
```

- [ ] **Step 2: Run the test, expect FAIL**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/restapi/ -run 'ToProjectDTO|ToTaskDTO|ToSubtaskDTO'
```

Expected: `FAIL` - undefined `toProjectDTO`, `toTaskDTO`, `toSubtaskDTO`, and the DTO types. (Adjust the CRD field names in the test if M0 used a slightly different Go field name; the JSON-tag is what the pin set fixes. Verify against `api/v1alpha1/*_types.go` before running.)

- [ ] **Step 3: Write the minimal implementation** (`internal/restapi/dto.go`)

```go
package restapi

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
)

type agentDTO struct {
	Model              string `json:"model,omitempty"`
	Image              string `json:"image,omitempty"`
	PermissionMode     string `json:"permissionMode,omitempty"`
	MaxTurnsPerTask    int    `json:"maxTurnsPerTask,omitempty"`
	TurnTimeoutSeconds int    `json:"turnTimeoutSeconds,omitempty"`
}

type projectStatusDTO struct {
	WebhookURL string             `json:"webhookURL,omitempty"`
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

type ProjectDTO struct {
	Name               string           `json:"name"`
	ScmSecretRef       string           `json:"scmSecretRef,omitempty"`
	TriggerLabel       string           `json:"triggerLabel,omitempty"`
	MaxConcurrentTasks int              `json:"maxConcurrentTasks,omitempty"`
	Agent              agentDTO         `json:"agent"`
	Status             projectStatusDTO `json:"status"`
}

type repositoryStatusDTO struct {
	Phase              string             `json:"phase,omitempty"`
	LastIngestedCommit string             `json:"lastIngestedCommit,omitempty"`
	LastIngestTime     string             `json:"lastIngestTime,omitempty"`
	JobName            string             `json:"jobName,omitempty"`
	Conditions         []metav1.Condition `json:"conditions,omitempty"`
}

type RepositoryDTO struct {
	Name          string              `json:"name"`
	ProjectRef    string              `json:"projectRef,omitempty"`
	URL           string              `json:"url,omitempty"`
	DefaultBranch string              `json:"defaultBranch,omitempty"`
	IngestEnabled bool                `json:"ingestEnabled"`
	Status        repositoryStatusDTO `json:"status"`
}

type taskSourceDTO struct {
	Provider string `json:"provider,omitempty"`
	IssueRef string `json:"issueRef,omitempty"`
	URL      string `json:"url,omitempty"`
}

type taskStatusDTO struct {
	Phase          string             `json:"phase,omitempty"`
	PodName        string             `json:"podName,omitempty"`
	TurnsCompleted int                `json:"turnsCompleted,omitempty"`
	PRURL          string             `json:"prURL,omitempty"`
	ResultSummary  string             `json:"resultSummary,omitempty"`
	Conditions     []metav1.Condition `json:"conditions,omitempty"`
}

type TaskDTO struct {
	Name          string         `json:"name"`
	ProjectRef    string         `json:"projectRef,omitempty"`
	RepositoryRef string         `json:"repositoryRef,omitempty"`
	Goal          string         `json:"goal,omitempty"`
	Source        *taskSourceDTO `json:"source,omitempty"`
	MaxTurns      int            `json:"maxTurns,omitempty"`
	Status        taskStatusDTO  `json:"status"`
}

type subtaskStatusDTO struct {
	Phase  string `json:"phase,omitempty"`
	TurnID string `json:"turnId,omitempty"`
	Result string `json:"result,omitempty"`
}

type SubtaskDTO struct {
	Name    string           `json:"name"`
	TaskRef string           `json:"taskRef,omitempty"`
	Title   string           `json:"title,omitempty"`
	Detail  string           `json:"detail,omitempty"`
	Order   int              `json:"order"`
	Status  subtaskStatusDTO `json:"status"`
}

func toProjectDTO(p tatarav1alpha1.Project) ProjectDTO {
	return ProjectDTO{
		Name: p.Name, ScmSecretRef: p.Spec.ScmSecretRef, TriggerLabel: p.Spec.TriggerLabel,
		MaxConcurrentTasks: p.Spec.MaxConcurrentTasks,
		Agent: agentDTO{
			Model: p.Spec.Agent.Model, Image: p.Spec.Agent.Image,
			PermissionMode: p.Spec.Agent.PermissionMode, MaxTurnsPerTask: p.Spec.Agent.MaxTurnsPerTask,
			TurnTimeoutSeconds: p.Spec.Agent.TurnTimeoutSeconds,
		},
		Status: projectStatusDTO{WebhookURL: p.Status.WebhookURL, Conditions: p.Status.Conditions},
	}
}

func toRepositoryDTO(r tatarav1alpha1.Repository) RepositoryDTO {
	d := RepositoryDTO{
		Name: r.Name, ProjectRef: r.Spec.ProjectRef, URL: r.Spec.URL,
		DefaultBranch: r.Spec.DefaultBranch, IngestEnabled: r.Spec.IngestEnabled,
		Status: repositoryStatusDTO{
			Phase: r.Status.Phase, LastIngestedCommit: r.Status.LastIngestedCommit,
			JobName: r.Status.JobName, Conditions: r.Status.Conditions,
		},
	}
	if !r.Status.LastIngestTime.IsZero() {
		d.Status.LastIngestTime = r.Status.LastIngestTime.UTC().Format("2006-01-02T15:04:05Z07:00")
	}
	return d
}

func toTaskDTO(task tatarav1alpha1.Task) TaskDTO {
	d := TaskDTO{
		Name: task.Name, ProjectRef: task.Spec.ProjectRef, RepositoryRef: task.Spec.RepositoryRef,
		Goal: task.Spec.Goal, MaxTurns: task.Spec.MaxTurns,
		Status: taskStatusDTO{
			Phase: task.Status.Phase, PodName: task.Status.PodName,
			TurnsCompleted: task.Status.TurnsCompleted, PRURL: task.Status.PRURL,
			ResultSummary: task.Status.ResultSummary, Conditions: task.Status.Conditions,
		},
	}
	if task.Spec.Source != nil {
		d.Source = &taskSourceDTO{
			Provider: task.Spec.Source.Provider, IssueRef: task.Spec.Source.IssueRef,
			URL: task.Spec.Source.URL,
		}
	}
	return d
}

func toSubtaskDTO(st tatarav1alpha1.Subtask) SubtaskDTO {
	return SubtaskDTO{
		Name: st.Name, TaskRef: st.Spec.TaskRef, Title: st.Spec.Title,
		Detail: st.Spec.Detail, Order: st.Spec.Order,
		Status: subtaskStatusDTO{Phase: st.Status.Phase, TurnID: st.Status.TurnID, Result: st.Status.Result},
	}
}
```

NOTE: the Go field names (`p.Spec.ScmSecretRef`, `task.Spec.Source`, `st.Status.TurnID`, etc.) must match what M0 generated in `api/v1alpha1/*_types.go`. Read those files first and correct any field-name mismatch here; do not invent fields.

- [ ] **Step 4: Run the test, expect PASS**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/restapi/ -run 'ToProjectDTO|ToTaskDTO|ToSubtaskDTO'
```

Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-operator && gofmt -w internal/restapi && golangci-lint run ./internal/restapi/...
git add internal/restapi && git commit -m "feat: restapi JSON DTOs mapping CRD spec/status"
```

---

## Task 3: GET /projects and GET /projects/{p}

**Files:**
- Modify: `~/Documents/tatara/tatara-operator/internal/restapi/handlers.go`
- Create: `~/Documents/tatara/tatara-operator/internal/restapi/handlers_test.go`

- [ ] **Step 1: Write the failing test** (`internal/restapi/handlers_test.go`)

```go
package restapi_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/stretchr/testify/require"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/restapi"
)

func buildRouter(t *testing.T, objs ...client.Object) *chi.Mux {
	t.Helper()
	scheme := runtime.NewScheme()
	require.NoError(t, tatarav1alpha1.AddToScheme(scheme))
	fc := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(objs...).
		WithStatusSubresource(&tatarav1alpha1.Project{}, &tatarav1alpha1.Repository{},
			&tatarav1alpha1.Task{}, &tatarav1alpha1.Subtask{}).
		Build()
	s := restapi.NewServer(restapi.Config{Client: fc, Namespace: "tatara"})
	r := chi.NewRouter()
	s.Mount(r, nil)
	return r
}

func project(name string) *tatarav1alpha1.Project {
	return &tatarav1alpha1.Project{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "tatara"},
		Spec:       tatarav1alpha1.ProjectSpec{TriggerLabel: "tatara", MaxConcurrentTasks: 3},
	}
}

func TestListProjects(t *testing.T) {
	r := buildRouter(t, project("alpha"), project("beta"))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/projects", nil))
	require.Equal(t, http.StatusOK, w.Code)
	var out []restapi.ProjectDTO
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &out))
	require.Len(t, out, 2)
}

func TestGetProject(t *testing.T) {
	r := buildRouter(t, project("alpha"))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/projects/alpha", nil))
	require.Equal(t, http.StatusOK, w.Code)
	var out restapi.ProjectDTO
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &out))
	require.Equal(t, "alpha", out.Name)
}

func TestGetProject_NotFound(t *testing.T) {
	r := buildRouter(t)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/projects/missing", nil))
	require.Equal(t, http.StatusNotFound, w.Code)
}
```

- [ ] **Step 2: Run the test, expect FAIL**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/restapi/ -run 'ListProjects|GetProject'
```

Expected: `FAIL` - handlers return 501 (`Not Implemented`), bodies are empty.

- [ ] **Step 3: Implement the handlers and shared helpers.** Replace the `listProjects`/`getProject` stubs in `handlers.go` and add the shared JSON/error helpers (kept local to the package, mirroring tatara-chat's `WriteJSON`/`WriteError`):

```go
package restapi

import (
	"encoding/json"
	"errors"
	"net/http"

	"github.com/go-chi/chi/v5"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"sigs.k8s.io/controller-runtime/pkg/client"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
)

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

// writeClientErr maps a k8s client error to an HTTP status.
func writeClientErr(w http.ResponseWriter, err error) {
	if apierrors.IsNotFound(err) {
		writeError(w, http.StatusNotFound, "not found")
		return
	}
	writeError(w, http.StatusInternalServerError, err.Error())
}

func (s *Server) listProjects(w http.ResponseWriter, r *http.Request) {
	var list tatarav1alpha1.ProjectList
	if err := s.c.List(r.Context(), &list, client.InNamespace(s.ns)); err != nil {
		writeClientErr(w, err)
		return
	}
	out := make([]ProjectDTO, 0, len(list.Items))
	for i := range list.Items {
		out = append(out, toProjectDTO(list.Items[i]))
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) getProject(w http.ResponseWriter, r *http.Request) {
	var p tatarav1alpha1.Project
	key := client.ObjectKey{Namespace: s.ns, Name: chi.URLParam(r, "p")}
	if err := s.c.Get(r.Context(), key, &p); err != nil {
		writeClientErr(w, err)
		return
	}
	writeJSON(w, http.StatusOK, toProjectDTO(p))
}

var _ = errors.Is // retained if needed by later tasks; remove if unused
```

Remove the now-duplicated `listProjects`/`getProject` stubs and the now-unused `notImplemented` import if no stubs remain that reference it. (Keep `notImplemented` until Task 7 retires the last stub.) Drop the `var _ = errors.Is` line if `errors` is unused after writing the remaining handlers.

- [ ] **Step 4: Run the test, expect PASS**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/restapi/ -run 'ListProjects|GetProject'
```

Expected: `ok` (3 sub-tests pass; missing project -> 404 via `apierrors.IsNotFound`).

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-operator && gofmt -w internal/restapi && golangci-lint run ./internal/restapi/...
git add internal/restapi && git commit -m "feat: GET /projects and GET /projects/{p}"
```

---

## Task 4: GET /projects/{p}/repositories and GET /projects/{p}/tasks

**Files:**
- Modify: `~/Documents/tatara/tatara-operator/internal/restapi/handlers.go`
- Modify: `~/Documents/tatara/tatara-operator/internal/restapi/handlers_test.go`

- [ ] **Step 1: Append failing tests**

```go
func repository(name, projectRef string) *tatarav1alpha1.Repository {
	return &tatarav1alpha1.Repository{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "tatara"},
		Spec:       tatarav1alpha1.RepositorySpec{ProjectRef: projectRef, URL: "https://git/" + name, DefaultBranch: "main", IngestEnabled: true},
	}
}

func task(name, projectRef string) *tatarav1alpha1.Task {
	return &tatarav1alpha1.Task{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "tatara"},
		Spec:       tatarav1alpha1.TaskSpec{ProjectRef: projectRef, RepositoryRef: "repo", Goal: "g"},
	}
}

func TestListRepositories_FilteredByProject(t *testing.T) {
	r := buildRouter(t, repository("r1", "alpha"), repository("r2", "alpha"), repository("r3", "beta"))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/projects/alpha/repositories", nil))
	require.Equal(t, http.StatusOK, w.Code)
	var out []restapi.RepositoryDTO
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &out))
	require.Len(t, out, 2)
	for _, d := range out {
		require.Equal(t, "alpha", d.ProjectRef)
	}
}

func TestListTasks_FilteredByProject(t *testing.T) {
	r := buildRouter(t, task("t1", "alpha"), task("t2", "beta"))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/projects/alpha/tasks", nil))
	require.Equal(t, http.StatusOK, w.Code)
	var out []restapi.TaskDTO
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &out))
	require.Len(t, out, 1)
	require.Equal(t, "t1", out[0].Name)
}
```

- [ ] **Step 2: Run, expect FAIL**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/restapi/ -run 'ListRepositories|ListTasks'
```

Expected: `FAIL` - stubs return 501. (Filtering decision: the pin-set CRDs are not indexed in the fake client, so filter in-memory on `spec.projectRef` rather than a field selector. KISS, rule 2.)

- [ ] **Step 3: Implement** (replace the two stubs)

```go
func (s *Server) listRepositories(w http.ResponseWriter, r *http.Request) {
	proj := chi.URLParam(r, "p")
	var list tatarav1alpha1.RepositoryList
	if err := s.c.List(r.Context(), &list, client.InNamespace(s.ns)); err != nil {
		writeClientErr(w, err)
		return
	}
	out := make([]RepositoryDTO, 0)
	for i := range list.Items {
		if list.Items[i].Spec.ProjectRef == proj {
			out = append(out, toRepositoryDTO(list.Items[i]))
		}
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) listTasks(w http.ResponseWriter, r *http.Request) {
	proj := chi.URLParam(r, "p")
	var list tatarav1alpha1.TaskList
	if err := s.c.List(r.Context(), &list, client.InNamespace(s.ns)); err != nil {
		writeClientErr(w, err)
		return
	}
	out := make([]TaskDTO, 0)
	for i := range list.Items {
		if list.Items[i].Spec.ProjectRef == proj {
			out = append(out, toTaskDTO(list.Items[i]))
		}
	}
	writeJSON(w, http.StatusOK, out)
}
```

- [ ] **Step 4: Run, expect PASS**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/restapi/ -run 'ListRepositories|ListTasks'
```

Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-operator && gofmt -w internal/restapi && golangci-lint run ./internal/restapi/...
git add internal/restapi && git commit -m "feat: GET project repositories and tasks (project-scoped)"
```

---

## Task 5: GET /tasks/{t} and PATCH /tasks/{t} (agent status notes)

PATCH semantics (from the pin set / spec "status notes from the agent"): the agent posts a status note that the operator records into `Task.status.resultSummary` and appends as a `metav1.Condition`. Request DTO: `{ "resultSummary": string, "note": string }`. Both optional; `resultSummary` overwrites `status.resultSummary`; `note` (if non-empty) appends a condition `type=AgentNote, status=True, reason=AgentReport, message=<note>` with `lastTransitionTime=now`. The status subresource is updated via `client.Status().Update`.

**Files:**
- Modify: `~/Documents/tatara/tatara-operator/internal/restapi/handlers.go`
- Modify: `~/Documents/tatara/tatara-operator/internal/restapi/handlers_test.go`

- [ ] **Step 1: Append failing tests**

```go
func TestGetTask(t *testing.T) {
	r := buildRouter(t, task("t1", "alpha"))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/tasks/t1", nil))
	require.Equal(t, http.StatusOK, w.Code)
	var out restapi.TaskDTO
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &out))
	require.Equal(t, "t1", out.Name)
}

func TestPatchTask_SetsResultSummaryAndCondition(t *testing.T) {
	r := buildRouter(t, task("t1", "alpha"))
	body := strings.NewReader(`{"resultSummary":"halfway","note":"cloned repo"}`)
	req := httptest.NewRequest(http.MethodPatch, "/tasks/t1", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusOK, w.Code)
	var out restapi.TaskDTO
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &out))
	require.Equal(t, "halfway", out.Status.ResultSummary)
	require.NotEmpty(t, out.Status.Conditions)
	require.Equal(t, "AgentNote", out.Status.Conditions[len(out.Status.Conditions)-1].Type)
	require.Equal(t, "cloned repo", out.Status.Conditions[len(out.Status.Conditions)-1].Message)
}

func TestPatchTask_NotFound(t *testing.T) {
	r := buildRouter(t)
	req := httptest.NewRequest(http.MethodPatch, "/tasks/nope", strings.NewReader(`{"resultSummary":"x"}`))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusNotFound, w.Code)
}
```

Add `"strings"` to the test imports.

- [ ] **Step 2: Run, expect FAIL**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/restapi/ -run 'GetTask|PatchTask'
```

Expected: `FAIL` - 501 stubs.

- [ ] **Step 3: Implement** (replace the `getTask`/`patchTask` stubs; add the request DTO + a `decodeJSON` helper and `meta.SetStatusCondition` usage)

```go
import (
	"time"

	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

type taskPatchReq struct {
	ResultSummary *string `json:"resultSummary,omitempty"`
	Note          string  `json:"note,omitempty"`
}

func decodeJSON(r *http.Request, dst any) error {
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	return dec.Decode(dst)
}

func (s *Server) getTask(w http.ResponseWriter, r *http.Request) {
	var t tatarav1alpha1.Task
	key := client.ObjectKey{Namespace: s.ns, Name: chi.URLParam(r, "t")}
	if err := s.c.Get(r.Context(), key, &t); err != nil {
		writeClientErr(w, err)
		return
	}
	writeJSON(w, http.StatusOK, toTaskDTO(t))
}

func (s *Server) patchTask(w http.ResponseWriter, r *http.Request) {
	var req taskPatchReq
	if err := decodeJSON(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid body: "+err.Error())
		return
	}
	var t tatarav1alpha1.Task
	key := client.ObjectKey{Namespace: s.ns, Name: chi.URLParam(r, "t")}
	if err := s.c.Get(r.Context(), key, &t); err != nil {
		writeClientErr(w, err)
		return
	}
	if req.ResultSummary != nil {
		t.Status.ResultSummary = *req.ResultSummary
	}
	if req.Note != "" {
		meta.SetStatusCondition(&t.Status.Conditions, metav1.Condition{
			Type: "AgentNote", Status: metav1.ConditionTrue, Reason: "AgentReport",
			Message: req.Note, LastTransitionTime: metav1.NewTime(time.Now()),
		})
	}
	if err := s.c.Status().Update(r.Context(), &t); err != nil {
		writeClientErr(w, err)
		return
	}
	writeJSON(w, http.StatusOK, toTaskDTO(t))
}
```

NOTE on `meta.SetStatusCondition`: it dedupes by `Type`, so repeated notes update the same `AgentNote` condition (message replaced). That matches "status notes" semantics and keeps the condition list bounded. The test asserts the LAST condition is the `AgentNote`, which holds.

- [ ] **Step 4: Run, expect PASS**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/restapi/ -run 'GetTask|PatchTask'
```

Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-operator && gofmt -w internal/restapi && golangci-lint run ./internal/restapi/...
git add internal/restapi && git commit -m "feat: GET /tasks/{t} and PATCH /tasks/{t} status notes"
```

---

## Task 6: GET /tasks/{t}/subtasks and POST /tasks/{t}/subtasks (agent self-plans)

POST creates a Subtask CRD owner-referenced to the Task (cascade delete). Request DTO: `{ "title": string (required), "detail": string, "order": int }`. The handler generates a name via `metadata.generateName` = `<task>-st-`; sets `spec.taskRef` = task name and `ownerReferences` to the Task. Returns 201 with the created SubtaskDTO. GET lists subtasks filtered by `spec.taskRef`, sorted by `spec.order`.

**Files:**
- Modify: `~/Documents/tatara/tatara-operator/internal/restapi/handlers.go`
- Modify: `~/Documents/tatara/tatara-operator/internal/restapi/handlers_test.go`

- [ ] **Step 1: Append failing tests**

```go
func subtask(name, taskRef string, order int) *tatarav1alpha1.Subtask {
	return &tatarav1alpha1.Subtask{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "tatara"},
		Spec:       tatarav1alpha1.SubtaskSpec{TaskRef: taskRef, Title: name, Order: order},
	}
}

func TestListSubtasks_SortedByOrder(t *testing.T) {
	r := buildRouter(t, subtask("b", "t1", 2), subtask("a", "t1", 1), subtask("z", "t2", 1))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/tasks/t1/subtasks", nil))
	require.Equal(t, http.StatusOK, w.Code)
	var out []restapi.SubtaskDTO
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &out))
	require.Len(t, out, 2)
	require.Equal(t, 1, out[0].Order)
	require.Equal(t, 2, out[1].Order)
}

func TestCreateSubtask_OwnerRefAndTaskRef(t *testing.T) {
	r := buildRouter(t, task("t1", "alpha"))
	body := strings.NewReader(`{"title":"write tests","detail":"unit","order":1}`)
	req := httptest.NewRequest(http.MethodPost, "/tasks/t1/subtasks", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusCreated, w.Code)
	var out restapi.SubtaskDTO
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &out))
	require.Equal(t, "t1", out.TaskRef)
	require.Equal(t, "write tests", out.Title)
	require.NotEmpty(t, out.Name) // server-generated
}

func TestCreateSubtask_TaskNotFound(t *testing.T) {
	r := buildRouter(t)
	req := httptest.NewRequest(http.MethodPost, "/tasks/nope/subtasks", strings.NewReader(`{"title":"x"}`))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusNotFound, w.Code)
}

func TestCreateSubtask_MissingTitle(t *testing.T) {
	r := buildRouter(t, task("t1", "alpha"))
	req := httptest.NewRequest(http.MethodPost, "/tasks/t1/subtasks", strings.NewReader(`{"detail":"d"}`))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusBadRequest, w.Code)
}
```

NOTE: the fake client does not honour `generateName`. The handler must set a deterministic `Name` itself (e.g. `<task>-st-<rfc3339nano-ish>` or a short random suffix) so the create succeeds under both the fake client and a real apiserver. Use `names.SimpleNameGenerator.GenerateName(task+"-st-")` from `k8s.io/apiserver/pkg/storage/names` (already an indirect dep via controller-runtime) OR a small local random suffix to avoid adding a dep - pick the local suffix (KISS, rule 2): `fmt.Sprintf("%s-st-%d", task, time.Now().UnixNano())`.

- [ ] **Step 2: Run, expect FAIL**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/restapi/ -run 'ListSubtasks|CreateSubtask'
```

Expected: `FAIL` - 501 stubs.

- [ ] **Step 3: Implement** (replace the `listSubtasks`/`createSubtask` stubs)

```go
import (
	"fmt"
	"sort"
)

type subtaskCreateReq struct {
	Title  string `json:"title"`
	Detail string `json:"detail,omitempty"`
	Order  int    `json:"order,omitempty"`
}

func (s *Server) listSubtasks(w http.ResponseWriter, r *http.Request) {
	taskName := chi.URLParam(r, "t")
	var list tatarav1alpha1.SubtaskList
	if err := s.c.List(r.Context(), &list, client.InNamespace(s.ns)); err != nil {
		writeClientErr(w, err)
		return
	}
	items := make([]tatarav1alpha1.Subtask, 0)
	for i := range list.Items {
		if list.Items[i].Spec.TaskRef == taskName {
			items = append(items, list.Items[i])
		}
	}
	sort.Slice(items, func(i, j int) bool { return items[i].Spec.Order < items[j].Spec.Order })
	out := make([]SubtaskDTO, 0, len(items))
	for i := range items {
		out = append(out, toSubtaskDTO(items[i]))
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) createSubtask(w http.ResponseWriter, r *http.Request) {
	var req subtaskCreateReq
	if err := decodeJSON(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid body: "+err.Error())
		return
	}
	if req.Title == "" {
		writeError(w, http.StatusBadRequest, "title required")
		return
	}
	taskName := chi.URLParam(r, "t")
	var parent tatarav1alpha1.Task
	if err := s.c.Get(r.Context(), client.ObjectKey{Namespace: s.ns, Name: taskName}, &parent); err != nil {
		writeClientErr(w, err)
		return
	}
	st := &tatarav1alpha1.Subtask{
		ObjectMeta: metav1.ObjectMeta{
			Name:      fmt.Sprintf("%s-st-%d", taskName, time.Now().UnixNano()),
			Namespace: s.ns,
			OwnerReferences: []metav1.OwnerReference{
				*metav1.NewControllerRef(&parent, tatarav1alpha1.GroupVersion.WithKind("Task")),
			},
		},
		Spec: tatarav1alpha1.SubtaskSpec{
			TaskRef: taskName, Title: req.Title, Detail: req.Detail, Order: req.Order,
		},
	}
	if err := s.c.Create(r.Context(), st); err != nil {
		writeClientErr(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, toSubtaskDTO(*st))
}
```

NOTE: `tatarav1alpha1.GroupVersion` is the `schema.GroupVersion` exported by `api/v1alpha1/groupversion_info.go` (kubebuilder default name). If M0 named it `SchemeGroupVersion`, use that. Verify before writing.

- [ ] **Step 4: Run, expect PASS**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/restapi/ -run 'ListSubtasks|CreateSubtask'
```

Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-operator && gofmt -w internal/restapi && golangci-lint run ./internal/restapi/...
git add internal/restapi && git commit -m "feat: GET/POST /tasks/{t}/subtasks with owner-ref"
```

---

## Task 7: PATCH /subtasks/{s} (mark Done / add result)

PATCH sets `status.phase` and `status.result`. Request DTO: `{ "phase": string, "result": string, "turnId": string }`, all optional. The common agent call sets `phase=Done` + `result=...`. Status subresource update. Retire the last `notImplemented` stub and remove the helper.

**Files:**
- Modify: `~/Documents/tatara/tatara-operator/internal/restapi/handlers.go`
- Modify: `~/Documents/tatara/tatara-operator/internal/restapi/handlers_test.go`

- [ ] **Step 1: Append failing tests**

```go
func TestPatchSubtask_MarksDone(t *testing.T) {
	r := buildRouter(t, subtask("s1", "t1", 1))
	body := strings.NewReader(`{"phase":"Done","result":"all green","turnId":"turn-7"}`)
	req := httptest.NewRequest(http.MethodPatch, "/subtasks/s1", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusOK, w.Code)
	var out restapi.SubtaskDTO
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &out))
	require.Equal(t, "Done", out.Status.Phase)
	require.Equal(t, "all green", out.Status.Result)
	require.Equal(t, "turn-7", out.Status.TurnID)
}

func TestPatchSubtask_NotFound(t *testing.T) {
	r := buildRouter(t)
	req := httptest.NewRequest(http.MethodPatch, "/subtasks/none", strings.NewReader(`{"phase":"Done"}`))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusNotFound, w.Code)
}
```

- [ ] **Step 2: Run, expect FAIL**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/restapi/ -run 'PatchSubtask'
```

Expected: `FAIL` - 501 stub.

- [ ] **Step 3: Implement** (replace the `patchSubtask` stub; remove `notImplemented` since no stubs remain)

```go
type subtaskPatchReq struct {
	Phase  *string `json:"phase,omitempty"`
	Result *string `json:"result,omitempty"`
	TurnID *string `json:"turnId,omitempty"`
}

func (s *Server) patchSubtask(w http.ResponseWriter, r *http.Request) {
	var req subtaskPatchReq
	if err := decodeJSON(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid body: "+err.Error())
		return
	}
	var st tatarav1alpha1.Subtask
	key := client.ObjectKey{Namespace: s.ns, Name: chi.URLParam(r, "s")}
	if err := s.c.Get(r.Context(), key, &st); err != nil {
		writeClientErr(w, err)
		return
	}
	if req.Phase != nil {
		st.Status.Phase = *req.Phase
	}
	if req.Result != nil {
		st.Status.Result = *req.Result
	}
	if req.TurnID != nil {
		st.Status.TurnID = *req.TurnID
	}
	if err := s.c.Status().Update(r.Context(), &st); err != nil {
		writeClientErr(w, err)
		return
	}
	writeJSON(w, http.StatusOK, toSubtaskDTO(st))
}
```

Delete the `func notImplemented(...)` definition and any remaining stub functions; confirm `handlers.go` no longer references it (`grep -n notImplemented internal/restapi`).

- [ ] **Step 4: Run, expect PASS (full package)**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/restapi/...
```

Expected: `ok` - all REST tests green.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-operator && gofmt -w internal/restapi && golangci-lint run ./internal/restapi/...
git add internal/restapi && git commit -m "feat: PATCH /subtasks/{s} mark done with result"
```

---

## Task 8: Wire the REST server into the manager (shared HTTP_ADDR listener)

The webhook server (M2) and REST API share one HTTP listener on `HTTP_ADDR`. Compose both onto a single `*chi.Mux`: webhook routes under `/operator/webhooks/...` (already mounted by M2), REST routes under the bare pin-set paths. `cmd/manager/main.go` builds the `auth.Verifier`, constructs the REST `Server`, and mounts it with `auth.Middleware(verifier)`.

**Files:**
- Modify: `~/Documents/tatara/tatara-operator/cmd/manager/main.go`
- Create (if M2 has no composed-listener test): `~/Documents/tatara/tatara-operator/internal/restapi/integration_test.go`

- [ ] **Step 1: Write the failing integration test** (both route groups on one mux)

```go
package restapi_test

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/stretchr/testify/require"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/restapi"
)

// TestSharedMux_RESTAndWebhookCoexist asserts REST paths and a webhook-prefixed
// path can live on the same mux without collision.
func TestSharedMux_RESTAndWebhookCoexist(t *testing.T) {
	scheme := runtime.NewScheme()
	require.NoError(t, tatarav1alpha1.AddToScheme(scheme))
	fc := fake.NewClientBuilder().WithScheme(scheme).Build()

	r := chi.NewRouter()
	// stand-in for the M2 webhook mount
	r.Post("/operator/webhooks/{project}", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusAccepted)
	})
	restapi.NewServer(restapi.Config{Client: fc, Namespace: "tatara"}).Mount(r, nil)

	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/projects", nil))
	require.Equal(t, http.StatusOK, w.Code)

	w = httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/operator/webhooks/demo", nil))
	require.Equal(t, http.StatusAccepted, w.Code)
}
```

- [ ] **Step 2: Run, expect PASS for the coexistence test, then verify main wiring compiles**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/restapi/ -run 'SharedMux'
```

Expected: `ok` (this confirms the mount API composes; it is a guard test). The real wiring change is in `main.go`, verified by `go build` in Step 4.

- [ ] **Step 3: Edit `cmd/manager/main.go`** to build the shared mux and start one HTTP server on `cfg.HTTPAddr`. Show the diff only (file already exists from M0-M2). Insert after the manager is created and the `auth.Verifier` is available:

```go
// --- REST API + webhook on the shared HTTP_ADDR listener ---
verifier, err := auth.NewVerifier(ctx, auth.Config{Issuer: cfg.OIDCIssuer, Audience: cfg.OIDCAudience})
if err != nil {
	setupLog.Error(err, "build OIDC verifier")
	os.Exit(1)
}

httpMux := chi.NewRouter()
// M2 webhook routes (keep existing call; example shown):
webhook.NewServer(webhook.Config{Client: mgr.GetClient(), Namespace: cfg.Namespace}).Mount(httpMux)
// M3 REST routes, OIDC-gated:
restapi.NewServer(restapi.Config{Client: mgr.GetClient(), Namespace: cfg.Namespace}).
	Mount(httpMux, auth.Middleware(verifier))

httpSrv := &http.Server{Addr: cfg.HTTPAddr, Handler: httpMux, ReadHeaderTimeout: 10 * time.Second}
go func() {
	setupLog.Info("starting http server", "addr", cfg.HTTPAddr)
	if err := httpSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		setupLog.Error(err, "http server")
		os.Exit(1)
	}
}()
// graceful shutdown alongside the manager:
defer func() {
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = httpSrv.Shutdown(shutdownCtx)
}()
```

Adjust to M2's actual webhook constructor name/signature (read `internal/webhook/server.go` first). If M2 already created `httpMux` and the `http.Server`, do NOT duplicate it - just add the `restapi.NewServer(...).Mount(...)` line onto the existing mux and drop the webhook/server boilerplate shown above. The single invariant: exactly one `http.Server` on `cfg.HTTPAddr`, carrying both route groups.

Add imports as needed: `"errors"`, `"net/http"`, `"time"`, `"github.com/go-chi/chi/v5"`, the operator `internal/auth`, `internal/restapi`, `internal/webhook`.

- [ ] **Step 4: Verify build + full test suite**

```bash
cd ~/Documents/tatara/tatara-operator && go build ./... && go test ./...
```

Expected: build succeeds; all packages `ok` (restapi green; M0-M2 packages unaffected).

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-operator && gofmt -w . && golangci-lint run ./...
git add cmd/manager/main.go internal/restapi && git commit -m "feat: mount REST API and webhook on shared HTTP_ADDR listener"
```

---

## Task 9: Finish the operator branch

- [ ] **Step 1: Run the full verification** (superpowers:verification-before-completion)

```bash
cd ~/Documents/tatara/tatara-operator && gofmt -l . && golangci-lint run ./... && go test ./...
```

Expected: `gofmt -l` prints nothing; lint clean; all tests `ok`.

- [ ] **Step 2: Request code review** (superpowers:requesting-code-review) on the `feat/m3-restapi` diff. Apply critical/high findings, re-run Step 1.

- [ ] **Step 3: Update MEMORY.md and ROADMAP.md** (operator repo)

`MEMORY.md` append:
```markdown
- 2026-06-06 - **M3 REST API.** OIDC-gated CRUD over the four CRDs, backed by
  the controller-runtime client, sharing the HTTP_ADDR listener with the M2
  webhook server (one chi mux, REST on bare paths, webhooks under
  /operator/webhooks/). PATCH /tasks records resultSummary + an AgentNote
  condition (deduped by type). POST /tasks/{t}/subtasks sets a deterministic
  name (fake client ignores generateName) + owner-ref. Subtask filtering is
  in-memory on spec.*Ref (fake client has no field indexers; KISS). Tests:
  httptest + controller-runtime fake client with status subresource enabled.
```

`ROADMAP.md`: mark M3 (REST API half) done; leave M4-M6 pending.

- [ ] **Step 4: Merge to main** (superpowers:finishing-a-development-branch), per CLAUDE.md rule 10 (merge to source repo `main`; build/deploy only from `main`, handled in M6). Do not deploy here.

---

# PART B - tatara-cli operator MCP tool group (separate repo, separate release)

The existing `tatara-cli` MCP server binds ONE `client.Client` to the memory base URL and registers all tools against it (`internal/mcp/server.go`, `NewServer(c *client.Client, ...)`). The operator tools target a DIFFERENT base URL and a DIFFERENT OIDC audience. The clean approach (mirroring the existing style, not abstracting prematurely) is: give each `Tool` an explicit target client, and let the server hold two clients (memory + operator), dispatching each tool against its target. This is the minimum change that preserves the existing memory tools verbatim.

**Decision (stated, not asked):** add a `Target` field to `Tool` (`"memory"` or `"operator"`); `Invoke` already takes the client explicitly, so the only change to dispatch is selecting which client by `Target`. Existing memory tools default to `"memory"` (zero value mapped to memory). This keeps `tools.go`'s registration shape identical.

This change ships as its own tatara-cli release (next minor, e.g. `v0.4.0`); state that in the commit and tatara-cli ROADMAP. It does not depend on the operator being built - the tools are pure REST-path builders, tested with `httptest` exactly like the memory tools.

## Task 10: Operator base-URL + audience config

**Files:**
- Modify: `~/Documents/tatara/tatara-cli/internal/client/config.go`
- Modify: `~/Documents/tatara/tatara-cli/internal/client/config_test.go`

- [ ] **Step 1: Append a failing test** (`config_test.go`)

```go
func TestResolveOperatorBaseURL_Precedence(t *testing.T) {
	require.Equal(t, "https://flag", ResolveOperatorBaseURL("https://flag", "https://env", &FileConfig{OperatorBaseURL: "https://file"}))
	require.Equal(t, "https://env", ResolveOperatorBaseURL("", "https://env", &FileConfig{OperatorBaseURL: "https://file"}))
	require.Equal(t, "https://file", ResolveOperatorBaseURL("", "", &FileConfig{OperatorBaseURL: "https://file"}))
	require.Equal(t, DefaultOperatorBaseURL, ResolveOperatorBaseURL("", "", &FileConfig{}))
}
```

Add `"github.com/stretchr/testify/require"` to the test imports if not present (match the existing `config_test.go` import style first).

- [ ] **Step 2: Run, expect FAIL**

```bash
cd ~/Documents/tatara/tatara-cli && go test ./internal/client/ -run 'ResolveOperatorBaseURL'
```

Expected: `FAIL` - undefined `ResolveOperatorBaseURL`, `DefaultOperatorBaseURL`, `FileConfig.OperatorBaseURL`.

- [ ] **Step 3: Implement** (mirror `ResolveBaseURL`/`DefaultBaseURL` exactly)

```go
const DefaultOperatorBaseURL = "https://tatara.szymonrichert.pl/api/v1/operator"
```

Add to `FileConfig`:
```go
type FileConfig struct {
	BaseURL         string `yaml:"baseUrl"`
	OperatorBaseURL string `yaml:"operatorBaseUrl"`
	Issuer          string `yaml:"issuer"`
}
```

Add the resolver (identical shape to `ResolveBaseURL`):
```go
// ResolveOperatorBaseURL returns the first non-empty of: flag, env, file, default.
func ResolveOperatorBaseURL(flag, env string, file *FileConfig) string {
	if flag != "" {
		return flag
	}
	if env != "" {
		return env
	}
	if file != nil && file.OperatorBaseURL != "" {
		return file.OperatorBaseURL
	}
	return DefaultOperatorBaseURL
}
```

- [ ] **Step 4: Run, expect PASS**

```bash
cd ~/Documents/tatara/tatara-cli && go test ./internal/client/ -run 'ResolveOperatorBaseURL'
```

Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-cli && git checkout -b feat/operator-mcp-tools
gofmt -w internal/client && golangci-lint run ./internal/client/...
git add internal/client && git commit -m "feat: operator base-url + audience config resolution"
```

NOTE on audience: the CLI's bearer token must carry the `tatara-operator` audience for operator calls. The existing token is minted via device/refresh flow against `DefaultIssuer`. Confirm with the M3 spec whether the same token already includes the `tatara-operator` audience (Keycloak audience mapper, added in M6 `tatara_clients.tf`). If yes, no token change is needed here - the operator client reuses the loaded token, same as memory. If a distinct audience-scoped token is required, that is a token-flow change owned by a follow-on; this plan assumes the single token carries both audiences (state this assumption in MEMORY.md). The operator base URL is the only new wiring needed in Task 12.

---

## Task 11: Operator MCP tool group (registration + REST-path builders)

**Files:**
- Modify: `~/Documents/tatara/tatara-cli/internal/mcp/tools.go`
- Modify: `~/Documents/tatara/tatara-cli/internal/mcp/tools_test.go`

- [ ] **Step 1: Append failing tests** (`tools_test.go`), mirroring `TestCodeTools_BuildQueries` style

```go
func TestOperatorTools_BuildPaths(t *testing.T) {
	cases := []struct {
		tool   string
		args   map[string]any
		method string
		path   string
	}{
		{"project_list", map[string]any{}, http.MethodGet, "/projects"},
		{"project_get", map[string]any{"project": "alpha"}, http.MethodGet, "/projects/alpha"},
		{"repo_list", map[string]any{"project": "alpha"}, http.MethodGet, "/projects/alpha/repositories"},
		{"task_list", map[string]any{"project": "alpha"}, http.MethodGet, "/projects/alpha/tasks"},
		{"task_get", map[string]any{"task": "t1"}, http.MethodGet, "/tasks/t1"},
		{"task_update", map[string]any{"task": "t1", "resultSummary": "x"}, http.MethodPatch, "/tasks/t1"},
		{"subtask_list", map[string]any{"task": "t1"}, http.MethodGet, "/tasks/t1/subtasks"},
		{"subtask_create", map[string]any{"task": "t1", "title": "step"}, http.MethodPost, "/tasks/t1/subtasks"},
		{"subtask_update", map[string]any{"subtask": "s1", "phase": "Done"}, http.MethodPatch, "/subtasks/s1"},
	}
	for _, c := range cases {
		t.Run(c.tool, func(t *testing.T) {
			m, p, _, err := operatorToolByName(t, c.tool).Build(c.args)
			require.NoError(t, err)
			require.Equal(t, c.method, m)
			require.Equal(t, c.path, p)
		})
	}
}

func TestOperatorTools_RequireArgs(t *testing.T) {
	_, _, _, err := operatorToolByName(t, "project_get").Build(map[string]any{})
	require.Error(t, err) // project required
	_, _, _, err = operatorToolByName(t, "task_get").Build(map[string]any{})
	require.Error(t, err) // task required
	_, _, _, err = operatorToolByName(t, "subtask_create").Build(map[string]any{"task": "t1"})
	require.Error(t, err) // title required
	_, _, _, err = operatorToolByName(t, "subtask_update").Build(map[string]any{})
	require.Error(t, err) // subtask required
}

func TestOperatorTools_Invoke(t *testing.T) {
	var gotMethod, gotPath string
	var gotBody map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod, gotPath = r.Method, r.URL.Path
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"name":"s1","taskRef":"t1"}`))
	}))
	defer srv.Close()
	c := freshClient(t, srv.URL)
	body, err := Invoke(context.Background(), c, operatorToolByName(t, "subtask_create"),
		map[string]any{"task": "t1", "title": "step", "detail": "d", "order": float64(1)})
	require.NoError(t, err)
	require.Equal(t, http.MethodPost, gotMethod)
	require.Equal(t, "/tasks/t1/subtasks", gotPath)
	require.Equal(t, "step", gotBody["title"])
	require.Contains(t, string(body), "taskRef")
}

func TestAllOperatorTools_Count(t *testing.T) {
	require.Len(t, OperatorTools(), 9)
}

func TestOperatorTools_TargetIsOperator(t *testing.T) {
	for _, tl := range OperatorTools() {
		require.Equal(t, TargetOperator, tl.Target)
	}
}

func operatorToolByName(t *testing.T, name string) Tool {
	t.Helper()
	for _, tl := range OperatorTools() {
		if tl.Name == name {
			return tl
		}
	}
	t.Fatalf("operator tool %q not found", name)
	return Tool{}
}
```

- [ ] **Step 2: Run, expect FAIL**

```bash
cd ~/Documents/tatara/tatara-cli && go test ./internal/mcp/ -run 'OperatorTools|AllOperatorTools'
```

Expected: `FAIL` - undefined `OperatorTools`, `TargetOperator`, `Tool.Target`.

- [ ] **Step 3: Implement.** Add a `Target` field to `Tool` and a `Target` type at the top of `tools.go`, then add `OperatorTools()` mirroring `AllTools()`/`codeGet` style:

In the `Tool` struct (add one field, keep everything else):
```go
type Target int

const (
	TargetMemory Target = iota // default: existing tools hit tatara-memory
	TargetOperator
)

type Tool struct {
	Name        string
	Description string
	Schema      json.RawMessage
	Target      Target
	Build       func(args map[string]any) (method, path string, body any, err error)
}
```

Add the operator group (separate constructor, same builder idioms - `url.PathEscape`, `argString`, the `id required` error style):
```go
// OperatorTools returns the 9 tatara-operator REST tools (Target=TargetOperator).
func OperatorTools() []Tool {
	op := func(name, desc, schema string, build func(map[string]any) (string, string, any, error)) Tool {
		return Tool{Name: name, Description: desc, Schema: json.RawMessage(schema), Target: TargetOperator, Build: build}
	}
	return []Tool{
		op("project_list", "List all Projects.",
			`{"type":"object","properties":{}}`,
			func(a map[string]any) (string, string, any, error) {
				return http.MethodGet, "/projects", nil, nil
			}),
		op("project_get", "Get a Project by name.",
			`{"type":"object","properties":{"project":{"type":"string"}},"required":["project"]}`,
			func(a map[string]any) (string, string, any, error) {
				p := argString(a, "project")
				if p == "" {
					return "", "", nil, fmt.Errorf("project required")
				}
				return http.MethodGet, "/projects/" + url.PathEscape(p), nil, nil
			}),
		op("repo_list", "List Repositories in a Project.",
			`{"type":"object","properties":{"project":{"type":"string"}},"required":["project"]}`,
			func(a map[string]any) (string, string, any, error) {
				p := argString(a, "project")
				if p == "" {
					return "", "", nil, fmt.Errorf("project required")
				}
				return http.MethodGet, "/projects/" + url.PathEscape(p) + "/repositories", nil, nil
			}),
		op("task_list", "List Tasks in a Project.",
			`{"type":"object","properties":{"project":{"type":"string"}},"required":["project"]}`,
			func(a map[string]any) (string, string, any, error) {
				p := argString(a, "project")
				if p == "" {
					return "", "", nil, fmt.Errorf("project required")
				}
				return http.MethodGet, "/projects/" + url.PathEscape(p) + "/tasks", nil, nil
			}),
		op("task_get", "Get a Task by name.",
			`{"type":"object","properties":{"task":{"type":"string"}},"required":["task"]}`,
			func(a map[string]any) (string, string, any, error) {
				tk := argString(a, "task")
				if tk == "" {
					return "", "", nil, fmt.Errorf("task required")
				}
				return http.MethodGet, "/tasks/" + url.PathEscape(tk), nil, nil
			}),
		op("task_update", "Record agent status notes on a Task (resultSummary, note).",
			`{"type":"object","properties":{"task":{"type":"string"},"resultSummary":{"type":"string"},"note":{"type":"string"}},"required":["task"]}`,
			func(a map[string]any) (string, string, any, error) {
				tk := argString(a, "task")
				if tk == "" {
					return "", "", nil, fmt.Errorf("task required")
				}
				body := map[string]any{}
				if v, ok := a["resultSummary"]; ok {
					body["resultSummary"] = v
				}
				if v, ok := a["note"]; ok {
					body["note"] = v
				}
				return http.MethodPatch, "/tasks/" + url.PathEscape(tk), body, nil
			}),
		op("subtask_list", "List Subtasks of a Task (sorted by order).",
			`{"type":"object","properties":{"task":{"type":"string"}},"required":["task"]}`,
			func(a map[string]any) (string, string, any, error) {
				tk := argString(a, "task")
				if tk == "" {
					return "", "", nil, fmt.Errorf("task required")
				}
				return http.MethodGet, "/tasks/" + url.PathEscape(tk) + "/subtasks", nil, nil
			}),
		op("subtask_create", "Create a Subtask under a Task (agent self-planning).",
			`{"type":"object","properties":{"task":{"type":"string"},"title":{"type":"string"},"detail":{"type":"string"},"order":{"type":"integer"}},"required":["task","title"]}`,
			func(a map[string]any) (string, string, any, error) {
				tk := argString(a, "task")
				if tk == "" {
					return "", "", nil, fmt.Errorf("task required")
				}
				if argString(a, "title") == "" {
					return "", "", nil, fmt.Errorf("title required")
				}
				body := map[string]any{"title": a["title"]}
				if v, ok := a["detail"]; ok {
					body["detail"] = v
				}
				if v, ok := a["order"]; ok {
					body["order"] = v
				}
				return http.MethodPost, "/tasks/" + url.PathEscape(tk) + "/subtasks", body, nil
			}),
		op("subtask_update", "Update a Subtask status (phase, result, turnId).",
			`{"type":"object","properties":{"subtask":{"type":"string"},"phase":{"type":"string"},"result":{"type":"string"},"turnId":{"type":"string"}},"required":["subtask"]}`,
			func(a map[string]any) (string, string, any, error) {
				st := argString(a, "subtask")
				if st == "" {
					return "", "", nil, fmt.Errorf("subtask required")
				}
				body := map[string]any{}
				for _, k := range []string{"phase", "result", "turnId"} {
					if v, ok := a[k]; ok {
						body[k] = v
					}
				}
				return http.MethodPatch, "/subtasks/" + url.PathEscape(st), body, nil
			}),
	}
}
```

`Invoke` is unchanged (it already takes the client explicitly). `argString`, `fmt`, `url`, `http` are already imported in `tools.go`.

- [ ] **Step 4: Run, expect PASS** (operator group + existing memory tests both green)

```bash
cd ~/Documents/tatara/tatara-cli && go test ./internal/mcp/...
```

Expected: `ok` - existing 23-tool tests still pass (Target zero value = TargetMemory, no behaviour change), new operator tests pass.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-cli && gofmt -w internal/mcp && golangci-lint run ./internal/mcp/...
git add internal/mcp && git commit -m "feat: operator MCP tool group (project/repo/task/subtask)"
```

---

## Task 12: Register the operator client + tools in the MCP server

The server must hold two clients (memory + operator) and dispatch each tool against its `Target`. The `mcp` cmd builds both clients (operator client uses `ResolveOperatorBaseURL` and the same loaded token).

**Files:**
- Modify: `~/Documents/tatara/tatara-cli/internal/mcp/server.go`
- Modify: `~/Documents/tatara/tatara-cli/internal/mcp/server_test.go`
- Modify: `~/Documents/tatara/tatara-cli/internal/cmd/mcp.go`
- Modify: `~/Documents/tatara/tatara-cli/internal/cmd/mcp_test.go` (if it exercises NewServer)

- [ ] **Step 1: Read `server_test.go`** to match its assertion style, then append a failing test asserting the server registers both groups (32 tools) and routes by target. Example (adapt to existing helpers):

```go
func TestNewServer_RegistersMemoryAndOperatorTools(t *testing.T) {
	mem := freshClient(t, "http://memory.invalid")
	op := freshClient(t, "http://operator.invalid")
	s := NewServer(mem, op, slog.New(slog.NewTextHandler(io.Discard, nil)))
	require.Equal(t, len(AllTools())+len(OperatorTools()), s.ToolCount())
}
```

If the existing `server_test.go` has no `ToolCount` accessor, the failing test instead asserts `NewServer` now requires two clients (a compile-level contract change). Keep the test minimal and consistent with what's there - read first.

- [ ] **Step 2: Run, expect FAIL**

```bash
cd ~/Documents/tatara/tatara-cli && go test ./internal/mcp/ -run 'NewServer_Registers'
```

Expected: `FAIL` - `NewServer` signature is `(c *client.Client, log *slog.Logger)`; the two-client form and `ToolCount` do not exist.

- [ ] **Step 3: Implement.** Change `NewServer` to take both clients; register both tool groups, dispatching each against its target client:

```go
type Server struct {
	srv      *server.MCPServer
	memory   *client.Client
	operator *client.Client
	log      *slog.Logger
}

// NewServer registers the tatara-memory tools (against memory) and the
// tatara-operator tools (against operator).
func NewServer(memory, operator *client.Client, log *slog.Logger) *Server {
	s := &Server{
		srv:      server.NewMCPServer("tatara", version.Version, server.WithToolCapabilities(true)),
		memory:   memory,
		operator: operator,
		log:      log,
	}
	for _, t := range AllTools() {
		s.register(t)
	}
	for _, t := range OperatorTools() {
		s.register(t)
	}
	return s
}

// ToolCount returns the number of registered tools (test/observability helper).
func (s *Server) ToolCount() int { return len(AllTools()) + len(OperatorTools()) }

func (s *Server) clientFor(t Tool) *client.Client {
	if t.Target == TargetOperator {
		return s.operator
	}
	return s.memory
}
```

In `register`, dispatch via `s.clientFor(t)` instead of the single `s.client`:
```go
body, err := Invoke(ctx, s.clientFor(t), t, args)
```

- [ ] **Step 4: Update `internal/cmd/mcp.go`** to build the operator client and pass both into `NewServer`. Show the diff: after the existing memory client is constructed, add:

```go
opBaseFlag, _ := cmd.Flags().GetString("operator-base-url")
opBase := client.ResolveOperatorBaseURL(opBaseFlag, os.Getenv("TATARA_OPERATOR_URL"), fileCfg)
opCli, err := client.New(client.Config{
	BaseURL:   opBase,
	Token:     token,
	TokenPath: tokenPath,
	Reload:    func() (*auth.Token, error) { return auth.LoadToken(tokenPath) },
	Refresh:   refresh,
	Save:      func(t *auth.Token) error { return auth.SaveToken(tokenPath, t) },
})
if err != nil {
	return err
}
srv := mcp.NewServer(cli, opCli, logger)
```

And register the flag next to the existing `base-url` flag (find where `base-url` is registered on the mcp cmd - likely in `newMCPCmd` or `root.go`; mirror it):
```go
cmd.Flags().String("operator-base-url", "", "tatara-operator REST base URL (overrides env/file/default)")
```

The operator client reuses the same loaded `token` (single-token assumption from Task 10; the `tatara-operator` audience is added to the token via the Keycloak mapper in M6).

- [ ] **Step 5: Run + build, expect PASS**

```bash
cd ~/Documents/tatara/tatara-cli && go build ./... && go test ./...
```

Expected: build succeeds; all packages `ok`. If `internal/cmd/mcp_test.go` constructed the old single-client `NewServer`, update those call sites to the two-client form (boy-scout, rule 3).

- [ ] **Step 6: Commit**

```bash
cd ~/Documents/tatara/tatara-cli && gofmt -w internal && golangci-lint run ./...
git add internal && git commit -m "feat: register operator MCP tools against operator client"
```

---

## Task 13: Finish the tatara-cli branch + release

- [ ] **Step 1: Full verification** (superpowers:verification-before-completion)

```bash
cd ~/Documents/tatara/tatara-cli && gofmt -l . && golangci-lint run ./... && go test ./...
```

Expected: clean; all `ok`.

- [ ] **Step 2: Request code review** (superpowers:requesting-code-review) on `feat/operator-mcp-tools`. Apply critical/high findings, re-run Step 1.

- [ ] **Step 3: Bump version + update docs.** Bump `internal/version/version.Version` (or the build-time ldflag default) to the next minor (e.g. `v0.4.0`). Update `tatara-cli/MEMORY.md`:
```markdown
- 2026-06-06 - **Operator MCP tools.** Added a 9-tool group
  (project/repo/task/subtask) targeting the tatara-operator REST API.
  Tool now carries Target (memory|operator); server holds two clients and
  dispatches by target. Operator base URL resolves flag>env(TATARA_OPERATOR_URL)
  >file(operatorBaseUrl)>default. Single OIDC token assumed to carry both the
  tatara-memory and tatara-operator audiences (Keycloak mapper, added in
  operator M6); revisit if a separate audience-scoped token is needed.
  Ships as tatara-cli v0.4.0.
```
`ROADMAP.md`: mark the operator MCP tool group done.

- [ ] **Step 4: Merge to main** (superpowers:finishing-a-development-branch), rule 10. Tag/release `v0.4.0` from `main` per the repo's release process (build/deploy only from `main`).

---

## Out of scope for THIS plan (follow-on / other milestones)

- Operator chart, NetworkPolicy, metrics for REST handlers, Keycloak `tatara-operator` client + audience mapper, infra helmfile `tatara` release: M6.
- Task reconciler / turn loop / wrapper Pod that consumes these subtasks: M4.
- SCM write-back (PR/MR/comment) and work-item -> Task: M5.
- If the single OIDC token cannot carry both audiences, a separate operator-audience token flow in tatara-cli is a follow-on (noted in Task 10 + MEMORY.md).

## Per-task verification rule (applies to every task above)

After each task's Step "PASS", before committing: `gofmt -l` on the changed dir prints nothing, `golangci-lint run` on the changed package is clean, and the named test runs green. Never commit on red. This satisfies superpowers:verification-before-completion at task granularity; Task 9 / Task 13 do the whole-repo final pass.
