# tatara-operator M4 (Task reconciler + agent turn loop) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Each task is a full TDD cycle: write the failing test (full code), run it and see it FAIL, write the minimal implementation (full code), run it and see it PASS, then commit. Do not batch tasks. Do not skip the RED step.

**Goal:** Build M4 of `tatara-operator`: the wrapper HTTP `Session` client (`internal/agent/session.go`), pure builders for the wrapper agent `*corev1.Pod` + `*corev1.Service` (`internal/agent/pod.go`), the `TaskReconciler` that spawns one wrapper session per Task and drives it turn-by-turn over its Subtasks with concurrency gating and bounded pod-loss retries (`internal/controller/task_controller.go`), and the in-cluster `POST /internal/turn-complete` callback server plus its periodic poll backstop (`internal/controller/turncallback.go`), all wired into `cmd/manager/main.go`.

**Architecture:** A Task runs as a single long-lived `tatara-claude-code-wrapper` Pod (addressable via a same-named ClusterIP Service). The `TaskReconciler` gates on `Project.spec.maxConcurrentTasks`, spawns Pod+Service (phase `Planning`), waits for wrapper readiness, then submits turn 0 (the plan turn) instructing the agent to decompose the goal into Subtasks via the subtask MCP tool. Thereafter the loop is callback-driven: the wrapper POSTs each turn result to the operator's in-cluster `/internal/turn-complete` listener on `INTERNAL_ADDR`, which records the result onto the executing Subtask and requeues the Task; the reconciler then marks that Subtask `Done`, picks the next `Pending` Subtask by `spec.order`, and submits its title+detail (phase `Running`). A periodic requeue backstops missed callbacks by polling `GET /v1/messages/{turnId}`. Termination (no Pending Subtasks, or `maxTurns`/timeout) sets phase `Succeeded`/`Failed`, records `turnsCompleted`, DELETEs the wrapper session, and deletes Pod+Service. The SCM write-back (PR/MR + issue comment) is M5; M4 sets phase and leaves an explicit hook marker the M5 path fills.

**Tech Stack:** Go 1.25.x, `sigs.k8s.io/controller-runtime` (reconciler + `pkg/envtest` for reconciler tests), `net/http` + `net/http/httptest` for the wrapper Session client and the callback server, `k8s.io/api/core/v1` for Pod/Service builders, `github.com/prometheus/client_golang` (M0 metrics bundle), `github.com/stretchr/testify`.

**Spec:** `docs/superpowers/specs/2026-06-06-tatara-operator-design.md` (TaskReconciler section is the exact loop).
**Pin set (authoritative names/paths - obey exactly):** `docs/superpowers/plans/_tatara-operator-shared-contracts.md`
**Wrapper API + envs:** `~/Documents/tatara/tatara-claude-code-wrapper/README.md`.

**Reference sources on disk (read these, do not guess):**
- httptest client style (MUST mirror): `~/Documents/tatara/tatara-memory/internal/lightrag/{client.go,http_test.go}` (interface + `httptest.NewServer` table tests + `HTTPError` carrying status).
- Reconciler + envtest suite conventions (MUST mirror): the M1 plan `docs/superpowers/plans/2026-06-06-tatara-operator-m1-project-repository-ingest.md` Task 3 (`suite_test.go`, `exit_test.go`, reconciler struct embedding `client.Client`, `Metrics *obs.OperatorMetrics`, condition handling via `meta.SetStatusCondition`).
- Pod/Service env+secret wiring style (MUST mirror): M1 ingest builder `internal/ingest/job.go` (`BuildJob` env/`SecretKeyRef`/owner-ref structure), reproduced in the M1 plan Task 2.
- TokenSource: `~/Documents/tatara/tatara-operator/internal/auth/tokensource.go` (built in M0; `Token(ctx) (string, error)`).
- Manager wiring: M1 plan Task 6 (`cmd/manager/wire.go` `addReconcilers`).

**Repo dir / module path:** `~/Documents/tatara/tatara-operator/`, module `github.com/szymonrychu/tatara-operator`.

**Preconditions (built in M0-M3, assumed present - verify before relying on a symbol):**
- `api/v1alpha1/{project,repository,task,subtask}_types.go` with the exact spec/status fields from the spec "CRD model" section, `zz_generated.deepcopy.go`, and `AddToScheme`. `GroupVersion` exported by `api/v1alpha1/groupversion_info.go` (kubebuilder default; if M0 named it `SchemeGroupVersion`, use that).
- `internal/obs.OperatorMetrics` with `NewOperatorMetrics(reg *prometheus.Registry)`, `ReconcileResult(kind, result string)`. M4 ADDS `ObserveTurnDuration(seconds float64)` and `SetTasksInflight(n float64)` to this bundle (Task 1).
- `internal/auth.TokenSource` with `NewTokenSource(TokenSourceConfig)` and `Token(ctx) (string, error)`.
- `internal/config.Config` with the pin-set env scalars including `INTERNAL_ADDR` -> `InternalAddr`, `OIDCIssuer`, `OperatorOIDCClientID`, `OperatorOIDCClientSecret`, `AnthropicSecretName`, `CLIOIDCSecretName`, `MemoryBaseURL`, `Namespace`. (If M0 named a field differently, adjust references to match M0 exactly; do not rename M0 fields.)
- `internal/controller/suite_test.go` (envtest control plane, `k8sClient`, `testEnv`, `testNS = "tatara"`, `timeout`, `interval`) and `internal/controller/exit_test.go` from M1. M4 REUSES these; do not recreate them.
- `cmd/manager/wire.go` `addReconcilers(mgr, cfg, metrics)` from M1. M4 EXTENDS it.

**Pin-set constants used verbatim across this plan:**
- Wrapper REST: `POST /v1/messages {text, callbackUrl} -> 202 {turnId}`; `GET /v1/messages/{turnId} -> {state, finalText, stopReason, error, ...}`; `DELETE /v1/session`. Audience for the bearer: `tatara-claude-code-wrapper`. Wrapper listens on `:8080`; operator reaches it at `http://<svc>.<ns>.svc:8080`.
- Callback path: `POST /internal/turn-complete` on `INTERNAL_ADDR`, in-cluster, no OIDC.
- `DEFAULT_CALLBACK_URL` = `<INTERNAL_ADDR>/internal/turn-complete`.
- Pod env (from `Project.spec.agent` + Repository): `REPO_URL`, `REPO_BRANCH`, `MODEL`, `PERMISSION_MODE`, `TURN_TIMEOUT_SECONDS`, `DEFAULT_CALLBACK_URL`.
- Pod secrets: `ANTHROPIC_API_KEY` (from `ANTHROPIC_SECRET_NAME`, key `api-key`); Project SCM token (from `Project.spec.scmSecretRef`, key `token`, env `GIT_TOKEN`); tatara-cli OIDC client creds (from `CLI_OIDC_SECRET_NAME`, keys `client-id`/`client-secret`, envs `CLI_OIDC_CLIENT_ID`/`CLI_OIDC_CLIENT_SECRET`).
- Metrics (M0 names, obey exactly): `operator_turn_duration_seconds` (histogram), `operator_tasks_inflight` (gauge).
- Task phases (spec): `Pending|Planning|Running|Succeeded|Failed`. Subtask phases: `Pending|Running|Done|Failed`.

**Turn-id correlation contract (authoritative; defined here, used by reconciler + callback):**
The reconciler records the in-flight turn's id on the Task it spawned. Since `Task.status` (per spec) has no `currentTurnId` field, M4 stores it as the annotation `tatara.dev/current-turn` on the Task and the executing Subtask name as annotation `tatara.dev/current-subtask`. Turn 0 (plan turn) sets `current-turn` but leaves `current-subtask` empty (no Subtask executes the plan turn). The callback server resolves `turnId -> Task` by listing Tasks and matching `tatara.dev/current-turn`; it then writes the result onto the Subtask named by `tatara.dev/current-subtask` (if any) and requeues the Task by bumping the Task annotation `tatara.dev/turn-complete` to the RFC3339 time of the callback (the reconciler watches Tasks; the annotation change triggers a reconcile). Using annotations (not new status fields) keeps M4 from re-opening the M0 CRD schema. This is recorded in `MEMORY.md` in Task 9.

**Pod-loss retry contract:** the reconciler tracks recreate attempts on annotation `tatara.dev/pod-recreations` (integer). On finding the spawned Pod absent mid-run (Task already `Planning`/`Running`, no terminal phase), it recreates Pod+Service and increments the counter, up to `maxPodRecreations = 3`; exceeding that sets phase `Failed` reason `PodLost`.

---

## Task 1: Turn-duration + tasks-inflight metrics in `internal/obs`

The pin set names `operator_turn_duration_seconds` (histogram) and `operator_tasks_inflight` (gauge). M0/M1 created the `OperatorMetrics` bundle with the reconcile + ingest metrics; M4 adds these two collectors and their accessors.

**Files:**
- Modify: `~/Documents/tatara/tatara-operator/internal/obs/metrics.go`
- Modify: `~/Documents/tatara/tatara-operator/internal/obs/metrics_test.go`

- [ ] **Step 1: Write the failing test** (append to `internal/obs/metrics_test.go`)

```go
func TestTurnDurationAndInflight(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := NewOperatorMetrics(reg)

	m.ObserveTurnDuration(1.5)
	m.ObserveTurnDuration(2.5)
	m.SetTasksInflight(3)

	// histogram: assert sample count == 2 via the registry gather.
	mfs, err := reg.Gather()
	require.NoError(t, err)
	var turnCount uint64
	var inflight float64
	for _, mf := range mfs {
		switch mf.GetName() {
		case "operator_turn_duration_seconds":
			for _, mm := range mf.GetMetric() {
				turnCount += mm.GetHistogram().GetSampleCount()
			}
		case "operator_tasks_inflight":
			for _, mm := range mf.GetMetric() {
				inflight = mm.GetGauge().GetValue()
			}
		}
	}
	require.Equal(t, uint64(2), turnCount)
	require.Equal(t, float64(3), inflight)
}
```

Ensure the test file imports `"github.com/prometheus/client_golang/prometheus"` and `"github.com/stretchr/testify/require"` (already present if M1 added the metrics tests; add if missing).

- [ ] **Step 2: Run the test, expect FAIL**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/obs/ -run TestTurnDurationAndInflight -v
```

Expected: build failure - `m.ObserveTurnDuration undefined`, `m.SetTasksInflight undefined`.

- [ ] **Step 3: Add the collectors** (in `internal/obs/metrics.go`)

Add two fields to the `OperatorMetrics` struct (alongside the existing `reconcileTotal`, `ingestJobDuration`):

```go
	turnDuration  prometheus.Histogram
	tasksInflight prometheus.Gauge
```

In `NewOperatorMetrics`, construct and register them (extend the existing `reg.MustRegister(...)` call to include both):

```go
	m.turnDuration = prometheus.NewHistogram(prometheus.HistogramOpts{
		Name:    "operator_turn_duration_seconds",
		Help:    "Wrapper agent turn durations in seconds.",
		Buckets: prometheus.ExponentialBuckets(1, 2, 12),
	})
	m.tasksInflight = prometheus.NewGauge(prometheus.GaugeOpts{
		Name: "operator_tasks_inflight",
		Help: "Number of Tasks with a running wrapper session.",
	})
```

Add the accessors:

```go
// ObserveTurnDuration records a completed wrapper turn duration in seconds.
func (m *OperatorMetrics) ObserveTurnDuration(seconds float64) {
	m.turnDuration.Observe(seconds)
}

// SetTasksInflight sets the gauge of Tasks with a running wrapper session.
func (m *OperatorMetrics) SetTasksInflight(n float64) {
	m.tasksInflight.Set(n)
}
```

- [ ] **Step 4: Run the test, expect PASS**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/obs/ -run TestTurnDurationAndInflight -v
```

Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-operator && git checkout -b feat/m4-task-turn-loop
gofmt -w internal/obs && golangci-lint run ./internal/obs/...
git add internal/obs && git commit -m "feat: add turn-duration histogram and tasks-inflight gauge"
```

---

## Task 2: Session interface + TurnResult (pin set verbatim)

Define the wrapper client surface exactly as the pin set fixes it, plus a typed HTTP error carrying the status code (mirroring `lightrag.HTTPError`). The concrete `httpSession` is implemented in Task 3.

**Files:**
- Create: `~/Documents/tatara/tatara-operator/internal/agent/session.go`
- Create: `~/Documents/tatara/tatara-operator/internal/agent/errors_test.go`

- [ ] **Step 1: Write the failing test** (`internal/agent/errors_test.go`)

```go
package agent_test

import (
	"errors"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-operator/internal/agent"
)

func TestHTTPError_CarriesStatus(t *testing.T) {
	err := &agent.HTTPError{Status: 409, Body: "turn in flight"}
	require.Contains(t, err.Error(), "409")

	var he *agent.HTTPError
	require.True(t, errors.As(error(err), &he))
	require.Equal(t, 409, he.Status)
}

// Compile-time assertion that *httpSession satisfies Session is in session.go;
// here we only assert the interface and TurnResult shape are present.
func TestTurnResult_Fields(t *testing.T) {
	tr := agent.TurnResult{State: "completed", FinalText: "done", StopReason: "end_turn", Err: ""}
	require.Equal(t, "completed", tr.State)
	require.Equal(t, "done", tr.FinalText)
	require.Equal(t, "end_turn", tr.StopReason)
}
```

- [ ] **Step 2: Run the test, expect FAIL**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/agent/...
```

Expected: `FAIL` - `package ... internal/agent: no Go files` / undefined `agent.HTTPError`, `agent.TurnResult`, `agent.Session`.

- [ ] **Step 3: Write the minimal implementation** (`internal/agent/session.go`)

```go
// Package agent talks to the tatara-claude-code-wrapper REST API and builds
// the wrapper Pod + Service that runs a single Claude session per Task.
package agent

import (
	"context"
	"fmt"
)

// TurnResult is the outcome of one wrapper turn, as reported by the wrapper's
// GET /v1/messages/{turnId} response and the turn-complete callback.
type TurnResult struct {
	State, FinalText, StopReason, Err string
}

// Session is the operator's view of one wrapper session. baseURL is the
// per-pod wrapper address (http://<svc>.<ns>.svc:8080).
type Session interface {
	SubmitTurn(ctx context.Context, baseURL, text, callbackURL string) (turnID string, err error)
	GetTurn(ctx context.Context, baseURL, turnID string) (TurnResult, error)
	DeleteSession(ctx context.Context, baseURL string) error
}

// HTTPError is returned when the wrapper responds with a non-2xx status.
type HTTPError struct {
	Status int
	Body   string
}

func (e *HTTPError) Error() string {
	return fmt.Sprintf("wrapper http %d: %s", e.Status, e.Body)
}
```

- [ ] **Step 4: Run the test, expect PASS**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/agent/...
```

Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-operator && gofmt -w internal/agent && golangci-lint run ./internal/agent/...
git add internal/agent && git commit -m "feat: agent Session interface, TurnResult, HTTPError"
```

---

## Task 3: httpSession concrete client (httptest against a fake wrapper)

The concrete client mints an OIDC bearer (audience `tatara-claude-code-wrapper`) via a token func and calls the three wrapper endpoints. Tests run a `httptest.NewServer` impersonating the wrapper and assert request method/path/body/auth header and response decoding, plus error paths.

**Files:**
- Create: `~/Documents/tatara/tatara-operator/internal/agent/http.go`
- Create: `~/Documents/tatara/tatara-operator/internal/agent/http_test.go`

- [ ] **Step 1: Write the failing test** (`internal/agent/http_test.go`)

```go
package agent_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-operator/internal/agent"
)

func staticToken(_ context.Context) (string, error) { return "test-bearer", nil }

func newSession(t *testing.T, h http.HandlerFunc) (agent.Session, *httptest.Server) {
	t.Helper()
	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)
	return agent.NewHTTPSession(staticToken), srv
}

func TestSubmitTurn_202(t *testing.T) {
	s, srv := newSession(t, func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, http.MethodPost, r.Method)
		require.Equal(t, "/v1/messages", r.URL.Path)
		require.Equal(t, "Bearer test-bearer", r.Header.Get("Authorization"))

		var in struct {
			Text        string `json:"text"`
			CallbackURL string `json:"callbackUrl"`
		}
		require.NoError(t, json.NewDecoder(r.Body).Decode(&in))
		require.Equal(t, "do the thing", in.Text)
		require.Equal(t, "http://op/internal/turn-complete", in.CallbackURL)

		w.WriteHeader(http.StatusAccepted)
		_ = json.NewEncoder(w).Encode(map[string]string{"turnId": "turn-1"})
	})

	id, err := s.SubmitTurn(context.Background(), srv.URL, "do the thing", "http://op/internal/turn-complete")
	require.NoError(t, err)
	require.Equal(t, "turn-1", id)
}

func TestSubmitTurn_409(t *testing.T) {
	s, srv := newSession(t, func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusConflict)
		_, _ = w.Write([]byte("turn in flight"))
	})
	_, err := s.SubmitTurn(context.Background(), srv.URL, "x", "y")
	require.Error(t, err)
	var he *agent.HTTPError
	require.ErrorAs(t, err, &he)
	require.Equal(t, 409, he.Status)
}

func TestGetTurn(t *testing.T) {
	s, srv := newSession(t, func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, http.MethodGet, r.Method)
		require.Equal(t, "/v1/messages/turn-9", r.URL.Path)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"state":      "completed",
			"finalText":  "all green",
			"stopReason": "end_turn",
		})
	})
	tr, err := s.GetTurn(context.Background(), srv.URL, "turn-9")
	require.NoError(t, err)
	require.Equal(t, "completed", tr.State)
	require.Equal(t, "all green", tr.FinalText)
	require.Equal(t, "end_turn", tr.StopReason)
	require.Empty(t, tr.Err)
}

func TestGetTurn_CarriesErrorField(t *testing.T) {
	s, srv := newSession(t, func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"state": "failed",
			"error": "boom",
		})
	})
	tr, err := s.GetTurn(context.Background(), srv.URL, "turn-x")
	require.NoError(t, err)
	require.Equal(t, "failed", tr.State)
	require.Equal(t, "boom", tr.Err)
}

func TestGetTurn_NotFound(t *testing.T) {
	s, srv := newSession(t, func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	})
	_, err := s.GetTurn(context.Background(), srv.URL, "missing")
	var he *agent.HTTPError
	require.ErrorAs(t, err, &he)
	require.Equal(t, 404, he.Status)
}

func TestDeleteSession(t *testing.T) {
	called := false
	s, srv := newSession(t, func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, http.MethodDelete, r.Method)
		require.Equal(t, "/v1/session", r.URL.Path)
		require.Equal(t, "Bearer test-bearer", r.Header.Get("Authorization"))
		called = true
		w.WriteHeader(http.StatusNoContent)
	})
	require.NoError(t, s.DeleteSession(context.Background(), srv.URL))
	require.True(t, called)
}

func TestDeleteSession_PropagatesError(t *testing.T) {
	s, srv := newSession(t, func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte("kaboom"))
	})
	err := s.DeleteSession(context.Background(), srv.URL)
	var he *agent.HTTPError
	require.ErrorAs(t, err, &he)
	require.Equal(t, 500, he.Status)
}
```

- [ ] **Step 2: Run the test, expect FAIL**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/agent/ -run 'SubmitTurn|GetTurn|DeleteSession' -v
```

Expected: `FAIL` - undefined `agent.NewHTTPSession`.

- [ ] **Step 3: Write the minimal implementation** (`internal/agent/http.go`)

```go
package agent

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// TokenFunc mints a bearer token for the wrapper audience.
type TokenFunc func(ctx context.Context) (string, error)

// httpSession is the production Session, talking to a wrapper pod's REST API.
type httpSession struct {
	token TokenFunc
	hc    *http.Client
}

// NewHTTPSession returns a Session that authenticates wrapper calls with a
// bearer minted by token (audience tatara-claude-code-wrapper).
func NewHTTPSession(token TokenFunc) Session {
	return &httpSession{token: token, hc: &http.Client{Timeout: 30 * time.Second}}
}

func (s *httpSession) do(ctx context.Context, method, url string, body any, out any) error {
	var rdr io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return fmt.Errorf("agent: marshal body: %w", err)
		}
		rdr = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, url, rdr)
	if err != nil {
		return fmt.Errorf("agent: new request: %w", err)
	}
	tok, err := s.token(ctx)
	if err != nil {
		return fmt.Errorf("agent: mint token: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+tok)
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := s.hc.Do(req)
	if err != nil {
		return fmt.Errorf("agent: do request: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		b, _ := io.ReadAll(resp.Body)
		return &HTTPError{Status: resp.StatusCode, Body: string(b)}
	}
	if out != nil && resp.StatusCode != http.StatusNoContent {
		if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
			return fmt.Errorf("agent: decode response: %w", err)
		}
	}
	return nil
}

func (s *httpSession) SubmitTurn(ctx context.Context, baseURL, text, callbackURL string) (string, error) {
	body := map[string]string{"text": text, "callbackUrl": callbackURL}
	var out struct {
		TurnID string `json:"turnId"`
	}
	if err := s.do(ctx, http.MethodPost, baseURL+"/v1/messages", body, &out); err != nil {
		return "", err
	}
	return out.TurnID, nil
}

func (s *httpSession) GetTurn(ctx context.Context, baseURL, turnID string) (TurnResult, error) {
	var out struct {
		State      string `json:"state"`
		FinalText  string `json:"finalText"`
		StopReason string `json:"stopReason"`
		Error      string `json:"error"`
	}
	if err := s.do(ctx, http.MethodGet, baseURL+"/v1/messages/"+turnID, nil, &out); err != nil {
		return TurnResult{}, err
	}
	return TurnResult{State: out.State, FinalText: out.FinalText, StopReason: out.StopReason, Err: out.Error}, nil
}

func (s *httpSession) DeleteSession(ctx context.Context, baseURL string) error {
	return s.do(ctx, http.MethodDelete, baseURL+"/v1/session", nil, nil)
}

var _ Session = (*httpSession)(nil)
```

- [ ] **Step 4: Run the test, expect PASS**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/agent/ -run 'SubmitTurn|GetTurn|DeleteSession' -v
```

Expected: `ok` - all seven sub-tests pass.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-operator && gofmt -w internal/agent && golangci-lint run ./internal/agent/...
git add internal/agent && git commit -m "feat: httpSession wrapper client with OIDC bearer"
```

---

## Task 4: Wrapper Pod + Service builders (`internal/agent/pod.go`)

Pure builders, no client calls. `BuildPod` returns the wrapper `*corev1.Pod` with env from `Project.spec.agent` + Repository and secret-backed env from `ANTHROPIC_SECRET_NAME` / Project SCM token / `CLI_OIDC_SECRET_NAME`; `DEFAULT_CALLBACK_URL` is built from the operator `INTERNAL_ADDR`. `BuildService` returns the same-named ClusterIP `*corev1.Service`. Both owner-referenced to the Task. Pod name and Service name are identical (`PodName(task)`), so the operator addresses the wrapper at `http://<podname>.<ns>.svc:8080`.

**Files:**
- Create: `~/Documents/tatara/tatara-operator/internal/agent/pod.go`
- Create: `~/Documents/tatara/tatara-operator/internal/agent/pod_test.go`

- [ ] **Step 1: Write the failing test** (`internal/agent/pod_test.go`)

```go
package agent_test

import (
	"testing"

	"github.com/stretchr/testify/require"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/agent"
)

func sampleInputs() (*tatarav1alpha1.Project, *tatarav1alpha1.Repository, *tatarav1alpha1.Task, agent.PodConfig) {
	proj := &tatarav1alpha1.Project{
		ObjectMeta: metav1.ObjectMeta{Name: "demo", Namespace: "tatara"},
		Spec: tatarav1alpha1.ProjectSpec{
			ScmSecretRef: "demo-scm",
			Agent: tatarav1alpha1.AgentSpec{
				Model: "claude-x", Image: "wrapper:1",
				PermissionMode: "bypassPermissions", TurnTimeoutSeconds: 1800,
			},
		},
	}
	repo := &tatarav1alpha1.Repository{
		ObjectMeta: metav1.ObjectMeta{Name: "repo1", Namespace: "tatara"},
		Spec:       tatarav1alpha1.RepositorySpec{URL: "https://git/acme/repo1", DefaultBranch: "main"},
	}
	task := &tatarav1alpha1.Task{
		ObjectMeta: metav1.ObjectMeta{Name: "task-7", Namespace: "tatara", UID: "uid-task-7"},
		Spec:       tatarav1alpha1.TaskSpec{ProjectRef: "demo", RepositoryRef: "repo1", Goal: "g"},
	}
	cfg := agent.PodConfig{
		Namespace:           "tatara",
		InternalAddr:        "http://tatara-operator-internal.tatara.svc:9090",
		AnthropicSecretName: "anthropic",
		CLIOIDCSecretName:   "tatara-cli-oidc",
	}
	return proj, repo, task, cfg
}

func envValue(c corev1.Container, name string) (string, bool) {
	for _, e := range c.Env {
		if e.Name == name {
			return e.Value, true
		}
	}
	return "", false
}

func envSecretRef(c corev1.Container, name string) (*corev1.SecretKeySelector, bool) {
	for _, e := range c.Env {
		if e.Name == name && e.ValueFrom != nil && e.ValueFrom.SecretKeyRef != nil {
			return e.ValueFrom.SecretKeyRef, true
		}
	}
	return nil, false
}

func TestBuildPod_NameAndImageAndOwner(t *testing.T) {
	proj, repo, task, cfg := sampleInputs()
	pod := agent.BuildPod(proj, repo, task, cfg)

	require.Equal(t, agent.PodName(task), pod.Name)
	require.Equal(t, "tatara", pod.Namespace)
	require.Len(t, pod.Spec.Containers, 1)
	require.Equal(t, "wrapper:1", pod.Spec.Containers[0].Image)

	require.Len(t, pod.OwnerReferences, 1)
	or := pod.OwnerReferences[0]
	require.Equal(t, "Task", or.Kind)
	require.Equal(t, "task-7", or.Name)
	require.Equal(t, "uid-task-7", string(or.UID))
	require.True(t, or.Controller != nil && *or.Controller)
}

func TestBuildPod_PlainEnv(t *testing.T) {
	proj, repo, task, cfg := sampleInputs()
	c := agent.BuildPod(proj, repo, task, cfg).Spec.Containers[0]

	checks := map[string]string{
		"REPO_URL":             "https://git/acme/repo1",
		"REPO_BRANCH":          "main",
		"MODEL":                "claude-x",
		"PERMISSION_MODE":      "bypassPermissions",
		"TURN_TIMEOUT_SECONDS": "1800",
		"DEFAULT_CALLBACK_URL": "http://tatara-operator-internal.tatara.svc:9090/internal/turn-complete",
	}
	for k, want := range checks {
		got, ok := envValue(c, k)
		require.True(t, ok, "env %s missing", k)
		require.Equal(t, want, got, "env %s", k)
	}
}

func TestBuildPod_SecretEnv(t *testing.T) {
	proj, repo, task, cfg := sampleInputs()
	c := agent.BuildPod(proj, repo, task, cfg).Spec.Containers[0]

	ant, ok := envSecretRef(c, "ANTHROPIC_API_KEY")
	require.True(t, ok)
	require.Equal(t, "anthropic", ant.Name)
	require.Equal(t, "api-key", ant.Key)

	git, ok := envSecretRef(c, "GIT_TOKEN")
	require.True(t, ok)
	require.Equal(t, "demo-scm", git.Name)
	require.Equal(t, "token", git.Key)

	cid, ok := envSecretRef(c, "CLI_OIDC_CLIENT_ID")
	require.True(t, ok)
	require.Equal(t, "tatara-cli-oidc", cid.Name)
	require.Equal(t, "client-id", cid.Key)

	csec, ok := envSecretRef(c, "CLI_OIDC_CLIENT_SECRET")
	require.True(t, ok)
	require.Equal(t, "tatara-cli-oidc", csec.Name)
	require.Equal(t, "client-secret", csec.Key)
}

func TestBuildPod_PortAndReadiness(t *testing.T) {
	proj, repo, task, cfg := sampleInputs()
	c := agent.BuildPod(proj, repo, task, cfg).Spec.Containers[0]
	require.Len(t, c.Ports, 1)
	require.Equal(t, int32(8080), c.Ports[0].ContainerPort)
	require.NotNil(t, c.ReadinessProbe)
	require.Equal(t, "/readyz", c.ReadinessProbe.HTTPGet.Path)
}

func TestBuildService_MatchesPod(t *testing.T) {
	proj, repo, task, cfg := sampleInputs()
	svc := agent.BuildService(proj, repo, task, cfg)
	pod := agent.BuildPod(proj, repo, task, cfg)

	require.Equal(t, pod.Name, svc.Name) // service name == pod name
	require.Equal(t, "tatara", svc.Namespace)
	require.Equal(t, pod.Labels, svc.Spec.Selector)
	require.Len(t, svc.Spec.Ports, 1)
	require.Equal(t, int32(8080), svc.Spec.Ports[0].Port)

	require.Len(t, svc.OwnerReferences, 1)
	require.Equal(t, "Task", svc.OwnerReferences[0].Kind)
}
```

NOTE: the Go field names (`proj.Spec.Agent.Model`, `repo.Spec.URL`, `task.UID`) must match what M0 generated in `api/v1alpha1/*_types.go`. Read those files first and correct any mismatch in the test and impl; do not invent fields.

- [ ] **Step 2: Run the test, expect FAIL**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/agent/ -run 'BuildPod|BuildService' -v
```

Expected: `FAIL` - undefined `agent.BuildPod`, `agent.BuildService`, `agent.PodName`, `agent.PodConfig`.

- [ ] **Step 3: Write the minimal implementation** (`internal/agent/pod.go`)

```go
package agent

import (
	"strconv"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/intstr"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
)

// wrapperPort is the wrapper's in-pod HTTP listener.
const wrapperPort = 8080

// PodConfig holds the operator-level inputs the Pod/Service builders need that
// do not come from the CRDs.
type PodConfig struct {
	Namespace           string
	InternalAddr        string // operator INTERNAL_ADDR, e.g. http://...:9090
	AnthropicSecretName string
	CLIOIDCSecretName   string
}

// PodName returns the deterministic wrapper Pod (and Service) name for a Task.
func PodName(task *tatarav1alpha1.Task) string {
	return "wrapper-" + task.Name
}

func podLabels(task *tatarav1alpha1.Task) map[string]string {
	return map[string]string{
		"app.kubernetes.io/name":      "tatara-operator",
		"app.kubernetes.io/component": "agent",
		"tatara.dev/managed-by":       "tatara-operator",
		"tatara.dev/task":             task.Name,
	}
}

func ownerRef(task *tatarav1alpha1.Task) metav1.OwnerReference {
	controller := true
	return metav1.OwnerReference{
		APIVersion: tatarav1alpha1.GroupVersion.String(),
		Kind:       "Task",
		Name:       task.Name,
		UID:        task.UID,
		Controller: &controller,
	}
}

func secretEnv(name, secretName, key string) corev1.EnvVar {
	return corev1.EnvVar{
		Name: name,
		ValueFrom: &corev1.EnvVarSource{
			SecretKeyRef: &corev1.SecretKeySelector{
				LocalObjectReference: corev1.LocalObjectReference{Name: secretName},
				Key:                  key,
			},
		},
	}
}

// BuildPod returns the wrapper Pod for a Task, owner-referenced to the Task.
func BuildPod(project *tatarav1alpha1.Project, repo *tatarav1alpha1.Repository, task *tatarav1alpha1.Task, cfg PodConfig) *corev1.Pod {
	env := []corev1.EnvVar{
		{Name: "REPO_URL", Value: repo.Spec.URL},
		{Name: "REPO_BRANCH", Value: repo.Spec.DefaultBranch},
		{Name: "MODEL", Value: project.Spec.Agent.Model},
		{Name: "PERMISSION_MODE", Value: project.Spec.Agent.PermissionMode},
		{Name: "TURN_TIMEOUT_SECONDS", Value: strconv.Itoa(project.Spec.Agent.TurnTimeoutSeconds)},
		{Name: "DEFAULT_CALLBACK_URL", Value: cfg.InternalAddr + "/internal/turn-complete"},
		secretEnv("ANTHROPIC_API_KEY", cfg.AnthropicSecretName, "api-key"),
		secretEnv("GIT_TOKEN", project.Spec.ScmSecretRef, "token"),
		secretEnv("CLI_OIDC_CLIENT_ID", cfg.CLIOIDCSecretName, "client-id"),
		secretEnv("CLI_OIDC_CLIENT_SECRET", cfg.CLIOIDCSecretName, "client-secret"),
	}

	return &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:            PodName(task),
			Namespace:       cfg.Namespace,
			Labels:          podLabels(task),
			OwnerReferences: []metav1.OwnerReference{ownerRef(task)},
		},
		Spec: corev1.PodSpec{
			RestartPolicy: corev1.RestartPolicyNever,
			Containers: []corev1.Container{{
				Name:  "wrapper",
				Image: project.Spec.Agent.Image,
				Env:   env,
				Ports: []corev1.ContainerPort{{ContainerPort: wrapperPort}},
				ReadinessProbe: &corev1.Probe{
					ProbeHandler: corev1.ProbeHandler{
						HTTPGet: &corev1.HTTPGetAction{
							Path: "/readyz",
							Port: intstr.FromInt(wrapperPort),
						},
					},
				},
			}},
		},
	}
}

// BuildService returns the ClusterIP Service fronting the wrapper Pod. Its name
// equals the Pod name so the operator can address the wrapper at
// http://<name>.<ns>.svc:8080.
func BuildService(project *tatarav1alpha1.Project, repo *tatarav1alpha1.Repository, task *tatarav1alpha1.Task, cfg PodConfig) *corev1.Service {
	return &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{
			Name:            PodName(task),
			Namespace:       cfg.Namespace,
			Labels:          podLabels(task),
			OwnerReferences: []metav1.OwnerReference{ownerRef(task)},
		},
		Spec: corev1.ServiceSpec{
			Selector: podLabels(task),
			Ports: []corev1.ServicePort{{
				Port:       wrapperPort,
				TargetPort: intstr.FromInt(wrapperPort),
			}},
		},
	}
}

// BaseURL returns the in-cluster wrapper address for a Task's Service.
func BaseURL(task *tatarav1alpha1.Task, namespace string) string {
	return "http://" + PodName(task) + "." + namespace + ".svc:" + strconv.Itoa(wrapperPort)
}
```

NOTE: `_ = repo` is consumed via `repo.Spec.URL`; `project`/`task` are consumed too, so no unused params. Verify `intstr` import path `k8s.io/apimachinery/pkg/util/intstr` resolves; if not present run `go mod tidy`.

- [ ] **Step 4: Run the test, expect PASS**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/agent/ -run 'BuildPod|BuildService' -v
```

Expected: `ok` - all five sub-tests pass.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-operator && gofmt -w internal/agent && golangci-lint run ./internal/agent/...
git add internal/agent && git commit -m "feat: wrapper Pod + Service builders with env/secret/owner wiring"
```

---

## Task 5: fakeSession test double + turn-loop helpers (pure, no envtest)

Before wiring the reconciler, build a `fakeSession` (in the controller test package) and the pure loop-decision helpers the reconciler delegates to, so the turn-selection logic is tested in isolation from Kubernetes. The helpers: `planTurnText(goal string)`, `nextPendingSubtask(subs []Subtask) (*Subtask, bool)`, `turnText(sub Subtask)`.

**Files:**
- Create: `~/Documents/tatara/tatara-operator/internal/controller/turnloop.go`
- Create: `~/Documents/tatara/tatara-operator/internal/controller/turnloop_test.go`

- [ ] **Step 1: Write the failing test** (`internal/controller/turnloop_test.go`)

```go
package controller

import (
	"strings"
	"testing"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
)

func sub(name string, order int, phase string) tatarav1alpha1.Subtask {
	s := tatarav1alpha1.Subtask{}
	s.Name = name
	s.Spec.Order = order
	s.Spec.Title = name + "-title"
	s.Spec.Detail = name + "-detail"
	s.Status.Phase = phase
	return s
}

func TestPlanTurnText_MentionsDecompose(t *testing.T) {
	txt := planTurnText("ship the feature")
	if !strings.Contains(txt, "ship the feature") {
		t.Errorf("plan turn missing goal: %q", txt)
	}
	if !strings.Contains(strings.ToLower(txt), "subtask") {
		t.Errorf("plan turn missing subtask MCP instruction: %q", txt)
	}
}

func TestNextPendingSubtask_PicksLowestOrder(t *testing.T) {
	subs := []tatarav1alpha1.Subtask{
		sub("c", 3, "Pending"),
		sub("a", 1, "Done"),
		sub("b", 2, "Pending"),
	}
	got, ok := nextPendingSubtask(subs)
	if !ok {
		t.Fatal("expected a pending subtask")
	}
	if got.Name != "b" {
		t.Errorf("next = %q, want b (lowest-order Pending)", got.Name)
	}
}

func TestNextPendingSubtask_NoneLeft(t *testing.T) {
	subs := []tatarav1alpha1.Subtask{sub("a", 1, "Done"), sub("b", 2, "Done")}
	if _, ok := nextPendingSubtask(subs); ok {
		t.Error("expected no pending subtask")
	}
}

func TestTurnText_TitleAndDetail(t *testing.T) {
	txt := turnText(sub("x", 1, "Pending"))
	if !strings.Contains(txt, "x-title") || !strings.Contains(txt, "x-detail") {
		t.Errorf("turn text missing title/detail: %q", txt)
	}
}
```

- [ ] **Step 2: Run the test, expect FAIL**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/controller/ -run 'PlanTurnText|NextPendingSubtask|TurnText' -v
```

Expected: `FAIL` - undefined `planTurnText`, `nextPendingSubtask`, `turnText`.

- [ ] **Step 3: Write the minimal implementation** (`internal/controller/turnloop.go`)

```go
package controller

import (
	"fmt"
	"sort"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
)

// planTurnText is the turn-0 prompt: the goal plus the instruction to
// decompose the work into Subtasks via the subtask MCP tool.
func planTurnText(goal string) string {
	return fmt.Sprintf(
		"%s\n\nDecompose this objective into ordered Subtasks via the subtask MCP tool "+
			"(subtask_create), one per concrete step. Do not start implementation in this turn.",
		goal)
}

// nextPendingSubtask returns the lowest-order Pending subtask, if any.
func nextPendingSubtask(subs []tatarav1alpha1.Subtask) (*tatarav1alpha1.Subtask, bool) {
	pending := make([]tatarav1alpha1.Subtask, 0, len(subs))
	for i := range subs {
		if subs[i].Status.Phase == "Pending" || subs[i].Status.Phase == "" {
			pending = append(pending, subs[i])
		}
	}
	if len(pending) == 0 {
		return nil, false
	}
	sort.Slice(pending, func(i, j int) bool { return pending[i].Spec.Order < pending[j].Spec.Order })
	out := pending[0]
	return &out, true
}

// turnText is the prompt for executing one Subtask.
func turnText(sub tatarav1alpha1.Subtask) string {
	return fmt.Sprintf("Subtask: %s\n\n%s", sub.Spec.Title, sub.Spec.Detail)
}
```

NOTE: a Subtask created with no `status.phase` is treated as Pending (the REST POST in M3 does not set a phase). If M0's CRD defaults `status.phase` to `Pending`, the `== ""` branch is harmless.

- [ ] **Step 4: Run the test, expect PASS**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/controller/ -run 'PlanTurnText|NextPendingSubtask|TurnText' -v
```

Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-operator && gofmt -w internal/controller && golangci-lint run ./internal/controller/...
git add internal/controller/turnloop.go internal/controller/turnloop_test.go && git commit -m "feat: pure turn-loop decision helpers"
```

---

## Task 6: TaskReconciler skeleton - concurrency gate + spawn (envtest)

The reconciler embeds `client.Client`, holds `Scheme`, `Metrics`, an `agent.Session`, and a `PodConfig`. This task implements: load Task; if terminal phase, return; gate on `Project.spec.maxConcurrentTasks` (count Tasks in the Project whose `status.phase` is `Planning` or `Running`; if at cap and this Task is not already counted, requeue after a delay); else spawn Pod+Service if absent and set phase `Planning`. Turn submission lands in Task 7. A `fakeSession` records calls. Reuses the M1 `suite_test.go` control plane.

**Files:**
- Create: `~/Documents/tatara/tatara-operator/internal/controller/task_controller.go`
- Create: `~/Documents/tatara/tatara-operator/internal/controller/task_controller_test.go`
- Create: `~/Documents/tatara/tatara-operator/internal/controller/fakesession_test.go`

- [ ] **Step 1: Write the failing test.** First the fake session (`internal/controller/fakesession_test.go`):

```go
package controller

import (
	"context"
	"sync"

	"github.com/szymonrychu/tatara-operator/internal/agent"
)

type submittedTurn struct {
	BaseURL, Text, CallbackURL, TurnID string
}

// fakeSession records SubmitTurn/GetTurn/DeleteSession calls and returns
// scripted turn ids. It is safe for concurrent use by the reconciler.
type fakeSession struct {
	mu        sync.Mutex
	submits   []submittedTurn
	nextID    int
	getResult map[string]agent.TurnResult
	deleted   []string
	submitErr error
}

func newFakeSession() *fakeSession {
	return &fakeSession{getResult: map[string]agent.TurnResult{}}
}

func (f *fakeSession) SubmitTurn(_ context.Context, baseURL, text, callbackURL string) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.submitErr != nil {
		return "", f.submitErr
	}
	f.nextID++
	id := "turn-" + itoa(f.nextID)
	f.submits = append(f.submits, submittedTurn{BaseURL: baseURL, Text: text, CallbackURL: callbackURL, TurnID: id})
	return id, nil
}

func (f *fakeSession) GetTurn(_ context.Context, _ string, turnID string) (agent.TurnResult, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.getResult[turnID], nil
}

func (f *fakeSession) DeleteSession(_ context.Context, baseURL string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.deleted = append(f.deleted, baseURL)
	return nil
}

func (f *fakeSession) lastSubmit() (submittedTurn, bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.submits) == 0 {
		return submittedTurn{}, false
	}
	return f.submits[len(f.submits)-1], true
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	return string(b)
}
```

Then the reconciler test (`internal/controller/task_controller_test.go`):

```go
package controller

import (
	"context"
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/agent"
	"github.com/szymonrychu/tatara-operator/internal/obs"
)

func newTaskReconciler(fs agent.Session) *TaskReconciler {
	return &TaskReconciler{
		Client:  k8sClient,
		Scheme:  k8sClient.Scheme(),
		Metrics: obs.NewOperatorMetrics(prometheus.NewRegistry()),
		Session: fs,
		PodConfig: agent.PodConfig{
			Namespace:           testNS,
			InternalAddr:        "http://op-internal.tatara.svc:9090",
			AnthropicSecretName: "anthropic",
			CLIOIDCSecretName:   "tatara-cli-oidc",
		},
	}
}

func reconcileTask(t *testing.T, r *TaskReconciler, name string) (ctrl.Result, error) {
	t.Helper()
	return r.Reconcile(logf.IntoContext(context.Background(), logf.Log), ctrl.Request{
		NamespacedName: types.NamespacedName{Namespace: testNS, Name: name},
	})
}

func mkProject(t *testing.T, name string, maxConcurrent int) {
	t.Helper()
	p := &tatarav1alpha1.Project{}
	p.Name = name
	p.Namespace = testNS
	p.Spec.ScmSecretRef = name + "-scm"
	p.Spec.MaxConcurrentTasks = maxConcurrent
	p.Spec.Agent = tatarav1alpha1.AgentSpec{
		Model: "claude-x", Image: "wrapper:1", PermissionMode: "bypassPermissions",
		MaxTurnsPerTask: 50, TurnTimeoutSeconds: 1800,
	}
	if err := k8sClient.Create(context.Background(), p); err != nil {
		t.Fatalf("create project: %v", err)
	}
}

func mkRepository(t *testing.T, name, projectRef string) {
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

func mkTask(t *testing.T, name, projectRef, repoRef string) {
	t.Helper()
	tk := &tatarav1alpha1.Task{}
	tk.Name = name
	tk.Namespace = testNS
	tk.Spec.ProjectRef = projectRef
	tk.Spec.RepositoryRef = repoRef
	tk.Spec.Goal = "ship the feature"
	if err := k8sClient.Create(context.Background(), tk); err != nil {
		t.Fatalf("create task: %v", err)
	}
}

func getTask(t *testing.T, name string) *tatarav1alpha1.Task {
	t.Helper()
	tk := &tatarav1alpha1.Task{}
	if err := k8sClient.Get(context.Background(), types.NamespacedName{Namespace: testNS, Name: name}, tk); err != nil {
		t.Fatalf("get task %s: %v", name, err)
	}
	return tk
}

func setTaskPhase(t *testing.T, name, phase string) {
	t.Helper()
	tk := getTask(t, name)
	tk.Status.Phase = phase
	if err := k8sClient.Status().Update(context.Background(), tk); err != nil {
		t.Fatalf("set phase %s: %v", name, err)
	}
}

func TestTaskReconcile_SpawnsPodAndService(t *testing.T) {
	mkProject(t, "p-spawn", 3)
	mkRepository(t, "r-spawn", "p-spawn")
	mkTask(t, "t-spawn", "p-spawn", "r-spawn")

	fs := newFakeSession()
	r := newTaskReconciler(fs)
	if _, err := reconcileTask(t, r, "t-spawn"); err != nil {
		t.Fatalf("reconcile: %v", err)
	}

	tk := getTask(t, "t-spawn")
	if tk.Status.Phase != "Planning" {
		t.Errorf("phase = %q, want Planning", tk.Status.Phase)
	}

	pod := &corev1.Pod{}
	if err := k8sClient.Get(context.Background(),
		types.NamespacedName{Namespace: testNS, Name: agent.PodName(tk)}, pod); err != nil {
		t.Fatalf("expected pod %s: %v", agent.PodName(tk), err)
	}
	svc := &corev1.Service{}
	if err := k8sClient.Get(context.Background(),
		types.NamespacedName{Namespace: testNS, Name: agent.PodName(tk)}, svc); err != nil {
		t.Fatalf("expected service %s: %v", agent.PodName(tk), err)
	}
	if tk.Status.PodName != agent.PodName(tk) {
		t.Errorf("status.podName = %q, want %q", tk.Status.PodName, agent.PodName(tk))
	}
}

func TestTaskReconcile_GatesAtCap(t *testing.T) {
	mkProject(t, "p-cap", 1)
	mkRepository(t, "r-cap", "p-cap")
	mkTask(t, "t-running", "p-cap", "r-cap")
	mkTask(t, "t-queued", "p-cap", "r-cap")
	setTaskPhase(t, "t-running", "Running")

	fs := newFakeSession()
	r := newTaskReconciler(fs)
	res, err := reconcileTask(t, r, "t-queued")
	if err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	if res.RequeueAfter == 0 {
		t.Error("expected requeue at cap")
	}
	// no pod created for the queued task
	pod := &corev1.Pod{}
	err = k8sClient.Get(context.Background(),
		types.NamespacedName{Namespace: testNS, Name: "wrapper-t-queued"}, pod)
	if !apierrors.IsNotFound(err) {
		t.Errorf("queued task must not spawn a pod, got err=%v", err)
	}
	_ = metav1.Now
}

func TestTaskReconcile_TerminalNoop(t *testing.T) {
	mkProject(t, "p-term", 3)
	mkRepository(t, "r-term", "p-term")
	mkTask(t, "t-done", "p-term", "r-term")
	setTaskPhase(t, "t-done", "Succeeded")

	fs := newFakeSession()
	r := newTaskReconciler(fs)
	if _, err := reconcileTask(t, r, "t-done"); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	if _, ok := fs.lastSubmit(); ok {
		t.Error("terminal task must not submit a turn")
	}
}
```

- [ ] **Step 2: Run the test, expect FAIL**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/controller/ -run 'TaskReconcile_Spawn|TaskReconcile_Gates|TaskReconcile_Terminal' -v
```

Expected: `FAIL` - undefined `TaskReconciler`.

- [ ] **Step 3: Write the minimal implementation** (`internal/controller/task_controller.go`)

```go
package controller

import (
	"context"
	"fmt"
	"time"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/agent"
	"github.com/szymonrychu/tatara-operator/internal/obs"
)

const (
	capRequeue        = 15 * time.Second
	pollRequeue       = 30 * time.Second
	maxPodRecreations = 3

	annCurrentTurn    = "tatara.dev/current-turn"
	annCurrentSubtask = "tatara.dev/current-subtask"
	annTurnComplete   = "tatara.dev/turn-complete"
	annPodRecreations = "tatara.dev/pod-recreations"
)

// TaskReconciler spawns one wrapper session per Task and drives it turn by
// turn over the Task's Subtasks.
type TaskReconciler struct {
	client.Client
	Scheme    *runtime.Scheme
	Metrics   *obs.OperatorMetrics
	Session   agent.Session
	PodConfig agent.PodConfig
}

// +kubebuilder:rbac:groups=tatara.dev,resources=tasks,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=tatara.dev,resources=tasks/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=tatara.dev,resources=subtasks,verbs=get;list;watch;update;patch
// +kubebuilder:rbac:groups=tatara.dev,resources=subtasks/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=tatara.dev,resources=projects;repositories,verbs=get;list;watch
// +kubebuilder:rbac:groups="",resources=pods;services,verbs=get;list;watch;create;delete

func isTerminal(phase string) bool { return phase == "Succeeded" || phase == "Failed" }
func isActive(phase string) bool   { return phase == "Planning" || phase == "Running" }

// Reconcile drives a Task through spawn -> plan turn -> subtask turns ->
// terminate. Turn results arrive via the /internal/turn-complete callback,
// which annotates the Task to trigger the next reconcile.
func (r *TaskReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	l := log.FromContext(ctx)

	var task tatarav1alpha1.Task
	if err := r.Get(ctx, req.NamespacedName, &task); err != nil {
		if apierrors.IsNotFound(err) {
			return ctrl.Result{}, nil
		}
		r.Metrics.ReconcileResult("Task", "error")
		return ctrl.Result{}, fmt.Errorf("get task: %w", err)
	}

	if isTerminal(task.Status.Phase) {
		return ctrl.Result{}, nil
	}

	var project tatarav1alpha1.Project
	if err := r.Get(ctx, types.NamespacedName{Namespace: task.Namespace, Name: task.Spec.ProjectRef}, &project); err != nil {
		r.Metrics.ReconcileResult("Task", "error")
		return ctrl.Result{}, fmt.Errorf("get project: %w", err)
	}

	// Concurrency gate: only applies to Tasks not yet active.
	if !isActive(task.Status.Phase) {
		atCap, err := r.atConcurrencyCap(ctx, &project, task.Name)
		if err != nil {
			r.Metrics.ReconcileResult("Task", "error")
			return ctrl.Result{}, err
		}
		if atCap {
			l.Info("task gated at concurrency cap",
				"action", "task_gate", "resource_id", task.Name, "project", project.Name)
			return ctrl.Result{RequeueAfter: capRequeue}, nil
		}
	}

	var repo tatarav1alpha1.Repository
	if err := r.Get(ctx, types.NamespacedName{Namespace: task.Namespace, Name: task.Spec.RepositoryRef}, &repo); err != nil {
		r.Metrics.ReconcileResult("Task", "error")
		return ctrl.Result{}, fmt.Errorf("get repository: %w", err)
	}

	if err := r.ensurePodAndService(ctx, &project, &repo, &task); err != nil {
		r.Metrics.ReconcileResult("Task", "error")
		return ctrl.Result{}, err
	}

	if task.Status.Phase == "" {
		task.Status.Phase = "Planning"
		task.Status.PodName = agent.PodName(&task)
		if err := r.Status().Update(ctx, &task); err != nil {
			r.Metrics.ReconcileResult("Task", "error")
			return ctrl.Result{}, fmt.Errorf("set planning phase: %w", err)
		}
	}

	r.updateInflightGauge(ctx)
	r.Metrics.ReconcileResult("Task", "success")
	return ctrl.Result{}, nil
}

// atConcurrencyCap reports whether the Project already has maxConcurrentTasks
// active Tasks, excluding self.
func (r *TaskReconciler) atConcurrencyCap(ctx context.Context, project *tatarav1alpha1.Project, self string) (bool, error) {
	max := project.Spec.MaxConcurrentTasks
	if max <= 0 {
		max = 3
	}
	var list tatarav1alpha1.TaskList
	if err := r.List(ctx, &list, client.InNamespace(project.Namespace)); err != nil {
		return false, fmt.Errorf("list tasks: %w", err)
	}
	active := 0
	for i := range list.Items {
		it := list.Items[i]
		if it.Spec.ProjectRef == project.Name && it.Name != self && isActive(it.Status.Phase) {
			active++
		}
	}
	return active >= max, nil
}

// ensurePodAndService creates the wrapper Pod+Service if they are absent.
func (r *TaskReconciler) ensurePodAndService(ctx context.Context, project *tatarav1alpha1.Project, repo *tatarav1alpha1.Repository, task *tatarav1alpha1.Task) error {
	pod := agent.BuildPod(project, repo, task, r.PodConfig)
	existing := &corev1.Pod{}
	err := r.Get(ctx, types.NamespacedName{Namespace: pod.Namespace, Name: pod.Name}, existing)
	if apierrors.IsNotFound(err) {
		if err := r.Create(ctx, pod); err != nil {
			return fmt.Errorf("create wrapper pod: %w", err)
		}
	} else if err != nil {
		return fmt.Errorf("get wrapper pod: %w", err)
	}

	svc := agent.BuildService(project, repo, task, r.PodConfig)
	existingSvc := &corev1.Service{}
	err = r.Get(ctx, types.NamespacedName{Namespace: svc.Namespace, Name: svc.Name}, existingSvc)
	if apierrors.IsNotFound(err) {
		if err := r.Create(ctx, svc); err != nil {
			return fmt.Errorf("create wrapper service: %w", err)
		}
	} else if err != nil {
		return fmt.Errorf("get wrapper service: %w", err)
	}
	return nil
}

// updateInflightGauge sets operator_tasks_inflight to the count of active Tasks.
func (r *TaskReconciler) updateInflightGauge(ctx context.Context) {
	var list tatarav1alpha1.TaskList
	if err := r.List(ctx, &list, client.InNamespace(r.PodConfig.Namespace)); err != nil {
		return
	}
	n := 0
	for i := range list.Items {
		if isActive(list.Items[i].Status.Phase) {
			n++
		}
	}
	r.Metrics.SetTasksInflight(float64(n))
}

// SetupWithManager registers the Task reconciler, watching Tasks and the
// Pods/Services it owns.
func (r *TaskReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&tatarav1alpha1.Task{}).
		Owns(&corev1.Pod{}).
		Owns(&corev1.Service{}).
		Complete(r)
}
```

NOTE: `pollRequeue`, `maxPodRecreations`, the `annCurrent*` constants, and the recreate/terminate logic are referenced by Tasks 7-8; they are declared here so the package compiles. If golangci-lint flags any as unused after this task, keep them - they are consumed by the next tasks in the same package; add `//nolint:unused` only if the linter blocks the commit, and remove it once Task 8 lands. Verify CRD Go field names (`task.Status.Phase`, `task.Status.PodName`, `project.Spec.MaxConcurrentTasks`) against M0 before running.

- [ ] **Step 4: Run the test, expect PASS**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/controller/ -run 'TaskReconcile_Spawn|TaskReconcile_Gates|TaskReconcile_Terminal' -v
```

Expected: `ok` - three sub-tests pass.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-operator && gofmt -w internal/controller && golangci-lint run ./internal/controller/...
git add internal/controller && git commit -m "feat: TaskReconciler concurrency gate + wrapper pod/service spawn"
```

---

## Task 7: Plan turn (turn 0) + subtask iteration (envtest)

Extend `Reconcile` so that after the Pod is `Ready`: if no turn is in flight (`annCurrentTurn` empty) and no Subtasks have been executed yet, submit the plan turn (turn 0) and record `annCurrentTurn`. When a turn-complete annotation (`annTurnComplete`) is newer than the recorded plan/subtask turn: mark the executing Subtask `Done` (writing `status.result` from the recorded `TurnResult.FinalText`), pick the next `Pending` Subtask by order, submit its title+detail, set phase `Running`, increment `turnsCompleted`, record the new `annCurrentTurn`/`annCurrentSubtask`. The turn result text is read from the executing Subtask's `status.result` placeholder written by the callback (Task 8); for this task's tests, the result is injected directly via the Subtask `status.result` + the `annTurnComplete` annotation to simulate a delivered callback.

**Files:**
- Modify: `~/Documents/tatara/tatara-operator/internal/controller/task_controller.go`
- Modify: `~/Documents/tatara/tatara-operator/internal/controller/task_controller_test.go`

- [ ] **Step 1: Append failing tests** (`task_controller_test.go`)

```go
func mkSubtask(t *testing.T, name, taskRef string, order int) {
	t.Helper()
	st := &tatarav1alpha1.Subtask{}
	st.Name = name
	st.Namespace = testNS
	st.Spec.TaskRef = taskRef
	st.Spec.Title = name + "-title"
	st.Spec.Detail = name + "-detail"
	st.Spec.Order = order
	if err := k8sClient.Create(context.Background(), st); err != nil {
		t.Fatalf("create subtask %s: %v", name, err)
	}
}

func getSubtask(t *testing.T, name string) *tatarav1alpha1.Subtask {
	t.Helper()
	st := &tatarav1alpha1.Subtask{}
	if err := k8sClient.Get(context.Background(), types.NamespacedName{Namespace: testNS, Name: name}, st); err != nil {
		t.Fatalf("get subtask %s: %v", name, err)
	}
	return st
}

func annotate(t *testing.T, name string, kv map[string]string) {
	t.Helper()
	tk := getTask(t, name)
	if tk.Annotations == nil {
		tk.Annotations = map[string]string{}
	}
	for k, v := range kv {
		tk.Annotations[k] = v
	}
	if err := k8sClient.Update(context.Background(), tk); err != nil {
		t.Fatalf("annotate %s: %v", name, err)
	}
}

func TestTaskReconcile_PlanTurnSubmitted(t *testing.T) {
	mkProject(t, "p-plan", 3)
	mkRepository(t, "r-plan", "p-plan")
	mkTask(t, "t-plan", "p-plan", "r-plan")

	fs := newFakeSession()
	r := newTaskReconciler(fs)
	// First reconcile: spawn + planning. Mark the pod Ready, then reconcile again.
	if _, err := reconcileTask(t, r, "t-plan"); err != nil {
		t.Fatalf("reconcile 1: %v", err)
	}
	markPodReady(t, "wrapper-t-plan")
	if _, err := reconcileTask(t, r, "t-plan"); err != nil {
		t.Fatalf("reconcile 2: %v", err)
	}

	sub, ok := fs.lastSubmit()
	if !ok {
		t.Fatal("expected a plan turn submission")
	}
	if !contains(sub.Text, "ship the feature") {
		t.Errorf("plan turn text = %q", sub.Text)
	}
	tk := getTask(t, "t-plan")
	if tk.Annotations[annCurrentTurn] != sub.TurnID {
		t.Errorf("current-turn = %q, want %q", tk.Annotations[annCurrentTurn], sub.TurnID)
	}
}

func TestTaskReconcile_AdvancesToNextSubtask(t *testing.T) {
	mkProject(t, "p-adv", 3)
	mkRepository(t, "r-adv", "p-adv")
	mkTask(t, "t-adv", "p-adv", "r-adv")
	mkSubtask(t, "t-adv-s1", "t-adv", 1)
	mkSubtask(t, "t-adv-s2", "t-adv", 2)

	fs := newFakeSession()
	r := newTaskReconciler(fs)
	if _, err := reconcileTask(t, r, "t-adv"); err != nil {
		t.Fatalf("reconcile spawn: %v", err)
	}
	markPodReady(t, "wrapper-t-adv")
	if _, err := reconcileTask(t, r, "t-adv"); err != nil { // plan turn
		t.Fatalf("reconcile plan: %v", err)
	}
	planTurn, _ := fs.lastSubmit()

	// Simulate the plan-turn callback: turn complete, no executing subtask.
	annotate(t, "t-adv", map[string]string{annTurnComplete: "2026-06-06T10:00:00Z"})
	if _, err := reconcileTask(t, r, "t-adv"); err != nil { // submit s1
		t.Fatalf("reconcile s1: %v", err)
	}
	s1Turn, _ := fs.lastSubmit()
	if s1Turn.TurnID == planTurn.TurnID {
		t.Fatal("expected a new turn for subtask 1")
	}
	if !contains(s1Turn.Text, "t-adv-s1-title") {
		t.Errorf("s1 turn text = %q", s1Turn.Text)
	}
	tk := getTask(t, "t-adv")
	if tk.Status.Phase != "Running" {
		t.Errorf("phase = %q, want Running", tk.Status.Phase)
	}
	if tk.Annotations[annCurrentSubtask] != "t-adv-s1" {
		t.Errorf("current-subtask = %q, want t-adv-s1", tk.Annotations[annCurrentSubtask])
	}

	// Simulate s1 callback delivering a result; reconcile should mark s1 Done
	// and submit s2.
	st1 := getSubtask(t, "t-adv-s1")
	st1.Status.Result = "s1 result"
	if err := k8sClient.Status().Update(context.Background(), st1); err != nil {
		t.Fatalf("set s1 result: %v", err)
	}
	annotate(t, "t-adv", map[string]string{annTurnComplete: "2026-06-06T10:05:00Z"})
	if _, err := reconcileTask(t, r, "t-adv"); err != nil {
		t.Fatalf("reconcile s2: %v", err)
	}
	if getSubtask(t, "t-adv-s1").Status.Phase != "Done" {
		t.Errorf("s1 phase = %q, want Done", getSubtask(t, "t-adv-s1").Status.Phase)
	}
	s2Turn, _ := fs.lastSubmit()
	if !contains(s2Turn.Text, "t-adv-s2-title") {
		t.Errorf("s2 turn text = %q", s2Turn.Text)
	}
}

func contains(s, sub string) bool { return len(s) >= len(sub) && indexOf(s, sub) >= 0 }
func indexOf(s, sub string) int {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return i
		}
	}
	return -1
}
```

Add a `markPodReady` helper at the top of the file (envtest does not run kubelet, so the reconciler treats Pod readiness as a status condition we set in the test):

```go
func markPodReady(t *testing.T, podName string) {
	t.Helper()
	pod := &corev1.Pod{}
	if err := k8sClient.Get(context.Background(), types.NamespacedName{Namespace: testNS, Name: podName}, pod); err != nil {
		t.Fatalf("get pod %s: %v", podName, err)
	}
	pod.Status.Phase = corev1.PodRunning
	pod.Status.Conditions = []corev1.PodCondition{{Type: corev1.PodReady, Status: corev1.ConditionTrue}}
	if err := k8sClient.Status().Update(context.Background(), pod); err != nil {
		t.Fatalf("mark pod ready %s: %v", podName, err)
	}
}
```

- [ ] **Step 2: Run, expect FAIL**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/controller/ -run 'TaskReconcile_PlanTurn|TaskReconcile_AdvancesTo' -v
```

Expected: `FAIL` - the reconciler does not yet submit turns or advance subtasks.

- [ ] **Step 3: Implement the turn logic.** Insert a readiness check + turn driver into `Reconcile`, replacing the block from the `task.Status.Phase == ""` planning-set down to the metrics line. New body of `Reconcile` after `ensurePodAndService`:

```go
	// Set Planning on first spawn.
	if task.Status.Phase == "" {
		task.Status.Phase = "Planning"
		task.Status.PodName = agent.PodName(&task)
		if err := r.Status().Update(ctx, &task); err != nil {
			r.Metrics.ReconcileResult("Task", "error")
			return ctrl.Result{}, fmt.Errorf("set planning phase: %w", err)
		}
		r.updateInflightGauge(ctx)
		r.Metrics.ReconcileResult("Task", "success")
		return ctrl.Result{RequeueAfter: pollRequeue}, nil
	}

	ready, err := r.podReady(ctx, &task)
	if err != nil {
		r.Metrics.ReconcileResult("Task", "error")
		return ctrl.Result{}, err
	}
	if !ready {
		return ctrl.Result{RequeueAfter: 2 * time.Second}, nil
	}

	res, err := r.driveTurns(ctx, &project, &task)
	if err != nil {
		r.Metrics.ReconcileResult("Task", "error")
		return ctrl.Result{}, err
	}
	r.updateInflightGauge(ctx)
	r.Metrics.ReconcileResult("Task", "success")
	return res, nil
}

// podReady reports whether the wrapper Pod has the Ready condition true.
func (r *TaskReconciler) podReady(ctx context.Context, task *tatarav1alpha1.Task) (bool, error) {
	pod := &corev1.Pod{}
	if err := r.Get(ctx, types.NamespacedName{Namespace: task.Namespace, Name: agent.PodName(task)}, pod); err != nil {
		if apierrors.IsNotFound(err) {
			return false, nil
		}
		return false, fmt.Errorf("get pod for readiness: %w", err)
	}
	for _, c := range pod.Status.Conditions {
		if c.Type == corev1.PodReady && c.Status == corev1.ConditionTrue {
			return true, nil
		}
	}
	return false, nil
}

// driveTurns runs the callback-driven turn loop: plan turn first, then one
// Subtask per delivered turn-complete callback.
func (r *TaskReconciler) driveTurns(ctx context.Context, project *tatarav1alpha1.Project, task *tatarav1alpha1.Task) (ctrl.Result, error) {
	baseURL := agent.BaseURL(task, task.Namespace)
	cbURL := r.PodConfig.InternalAddr + "/internal/turn-complete"

	current := task.Annotations[annCurrentTurn]

	// No turn yet -> submit the plan turn (turn 0).
	if current == "" {
		id, err := r.Session.SubmitTurn(ctx, baseURL, planTurnText(task.Spec.Goal), cbURL)
		if err != nil {
			return ctrl.Result{}, fmt.Errorf("submit plan turn: %w", err)
		}
		return r.recordTurn(ctx, task, id, "")
	}

	// Turn in flight, no callback yet -> wait (backstop poll handled in Task 8).
	if task.Annotations[annTurnComplete] == "" {
		return ctrl.Result{RequeueAfter: pollRequeue}, nil
	}

	// A callback arrived. Mark the executing Subtask Done (if any).
	if prev := task.Annotations[annCurrentSubtask]; prev != "" {
		if err := r.markSubtaskDone(ctx, prev, current); err != nil {
			return ctrl.Result{}, err
		}
	}

	// Pick the next Pending Subtask.
	var subs tatarav1alpha1.SubtaskList
	if err := r.List(ctx, &subs, client.InNamespace(task.Namespace)); err != nil {
		return ctrl.Result{}, fmt.Errorf("list subtasks: %w", err)
	}
	mine := make([]tatarav1alpha1.Subtask, 0, len(subs.Items))
	for i := range subs.Items {
		if subs.Items[i].Spec.TaskRef == task.Name {
			mine = append(mine, subs.Items[i])
		}
	}
	next, ok := nextPendingSubtask(mine)
	if !ok {
		return r.terminate(ctx, task, "Succeeded", "NoPendingSubtasks", "all subtasks complete")
	}

	id, err := r.Session.SubmitTurn(ctx, baseURL, turnText(*next), cbURL)
	if err != nil {
		return ctrl.Result{}, fmt.Errorf("submit subtask turn: %w", err)
	}
	if next.Status.Phase != "Running" {
		next.Status.Phase = "Running"
		if err := r.Status().Update(ctx, next); err != nil {
			return ctrl.Result{}, fmt.Errorf("set subtask running: %w", err)
		}
	}
	task.Status.Phase = "Running"
	if err := r.Status().Update(ctx, task); err != nil {
		return ctrl.Result{}, fmt.Errorf("set task running: %w", err)
	}
	return r.recordTurn(ctx, task, id, next.Name)
}

// recordTurn writes the in-flight turn id + executing subtask onto the Task,
// clears the turn-complete marker, and bumps turnsCompleted when a turn closed.
func (r *TaskReconciler) recordTurn(ctx context.Context, task *tatarav1alpha1.Task, turnID, subtaskName string) (ctrl.Result, error) {
	fresh := &tatarav1alpha1.Task{}
	if err := r.Get(ctx, types.NamespacedName{Namespace: task.Namespace, Name: task.Name}, fresh); err != nil {
		return ctrl.Result{}, fmt.Errorf("reload task: %w", err)
	}
	if fresh.Annotations == nil {
		fresh.Annotations = map[string]string{}
	}
	if fresh.Annotations[annTurnComplete] != "" {
		fresh.Status.TurnsCompleted++
	}
	fresh.Annotations[annCurrentTurn] = turnID
	fresh.Annotations[annCurrentSubtask] = subtaskName
	delete(fresh.Annotations, annTurnComplete)
	if err := r.Update(ctx, fresh); err != nil {
		return ctrl.Result{}, fmt.Errorf("record turn annotations: %w", err)
	}
	if fresh.Status.TurnsCompleted != task.Status.TurnsCompleted {
		if err := r.Status().Update(ctx, fresh); err != nil {
			return ctrl.Result{}, fmt.Errorf("record turns completed: %w", err)
		}
	}
	return ctrl.Result{RequeueAfter: pollRequeue}, nil
}

// markSubtaskDone sets a Subtask Done, recording the turn id (its result is
// written by the callback before this reconcile runs).
func (r *TaskReconciler) markSubtaskDone(ctx context.Context, name, turnID string) error {
	st := &tatarav1alpha1.Subtask{}
	if err := r.Get(ctx, types.NamespacedName{Namespace: r.PodConfig.Namespace, Name: name}, st); err != nil {
		if apierrors.IsNotFound(err) {
			return nil
		}
		return fmt.Errorf("get subtask %s: %w", name, err)
	}
	st.Status.Phase = "Done"
	st.Status.TurnID = turnID
	if err := r.Status().Update(ctx, st); err != nil {
		return fmt.Errorf("mark subtask done: %w", err)
	}
	return nil
}
```

The `terminate` method is added in Task 8; for this task only, add a temporary stub so the package compiles (Task 8 replaces it):

```go
func (r *TaskReconciler) terminate(ctx context.Context, task *tatarav1alpha1.Task, phase, reason, msg string) (ctrl.Result, error) {
	task.Status.Phase = phase
	return ctrl.Result{}, r.Status().Update(ctx, task)
}
```

Remove the now-superseded tail of the original `Reconcile` (the standalone `task.Status.Phase == ""` block and the trailing metrics/return that this task replaced). Verify the final `Reconcile` ends at the new `return res, nil`.

- [ ] **Step 4: Run, expect PASS**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/controller/ -run 'TaskReconcile_PlanTurn|TaskReconcile_AdvancesTo|TaskReconcile_Spawn|TaskReconcile_Gates|TaskReconcile_Terminal' -v
```

Expected: `ok` - plan turn submitted, subtasks advance, prior tests still green.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-operator && gofmt -w internal/controller && golangci-lint run ./internal/controller/...
git add internal/controller && git commit -m "feat: plan turn + callback-driven subtask iteration"
```

---

## Task 8: Termination, cleanup, maxTurns/timeout cap, bounded pod-loss retry (envtest)

Replace the `terminate` stub with the real one: on termination set phase `Succeeded`/`Failed`, record `turnsCompleted`, `DELETE /v1/session`, delete Pod+Service, and write the M5 write-back hook marker. Add the `maxTurns` cap (from `task.spec.maxTurns` else `project.spec.agent.maxTurnsPerTask`) and the bounded pod-loss recreate. The M5 hook: set condition `type=WritebackPending, status=True, reason=AwaitingM5` so the M5 SCM path has a clear, queryable signal; record this contract in `MEMORY.md` (Task 9).

**Files:**
- Modify: `~/Documents/tatara/tatara-operator/internal/controller/task_controller.go`
- Modify: `~/Documents/tatara/tatara-operator/internal/controller/task_controller_test.go`

- [ ] **Step 1: Append failing tests**

```go
func TestTaskReconcile_TerminatesWhenNoPending(t *testing.T) {
	mkProject(t, "p-end", 3)
	mkRepository(t, "r-end", "p-end")
	mkTask(t, "t-end", "p-end", "r-end")

	fs := newFakeSession()
	r := newTaskReconciler(fs)
	if _, err := reconcileTask(t, r, "t-end"); err != nil { // spawn
		t.Fatalf("spawn: %v", err)
	}
	markPodReady(t, "wrapper-t-end")
	if _, err := reconcileTask(t, r, "t-end"); err != nil { // plan turn
		t.Fatalf("plan: %v", err)
	}
	// Plan turn callback, but the agent created no subtasks -> terminate Succeeded.
	annotate(t, "t-end", map[string]string{annTurnComplete: "2026-06-06T11:00:00Z"})
	if _, err := reconcileTask(t, r, "t-end"); err != nil {
		t.Fatalf("terminate: %v", err)
	}

	tk := getTask(t, "t-end")
	if tk.Status.Phase != "Succeeded" {
		t.Errorf("phase = %q, want Succeeded", tk.Status.Phase)
	}
	if len(fs.deleted) == 0 {
		t.Error("expected DELETE /v1/session")
	}
	// pod + service deleted
	pod := &corev1.Pod{}
	if err := k8sClient.Get(context.Background(),
		types.NamespacedName{Namespace: testNS, Name: "wrapper-t-end"}, pod); err == nil && pod.DeletionTimestamp == nil {
		t.Error("expected wrapper pod deleted")
	}
	// M5 hook marker
	if findCond(tk.Status.Conditions, "WritebackPending") == nil {
		t.Error("expected WritebackPending condition for the M5 write-back hook")
	}
}

func TestTaskReconcile_MaxTurnsCap(t *testing.T) {
	mkProject(t, "p-max", 3)
	mkRepository(t, "r-max", "p-max")
	tk := &tatarav1alpha1.Task{}
	tk.Name = "t-max"
	tk.Namespace = testNS
	tk.Spec.ProjectRef = "p-max"
	tk.Spec.RepositoryRef = "r-max"
	tk.Spec.Goal = "g"
	tk.Spec.MaxTurns = 1
	if err := k8sClient.Create(context.Background(), tk); err != nil {
		t.Fatalf("create task: %v", err)
	}
	mkSubtask(t, "t-max-s1", "t-max", 1)
	mkSubtask(t, "t-max-s2", "t-max", 2)

	fs := newFakeSession()
	r := newTaskReconciler(fs)
	if _, err := reconcileTask(t, r, "t-max"); err != nil {
		t.Fatalf("spawn: %v", err)
	}
	markPodReady(t, "wrapper-t-max")
	if _, err := reconcileTask(t, r, "t-max"); err != nil { // plan turn (turnsCompleted stays 0)
		t.Fatalf("plan: %v", err)
	}
	annotate(t, "t-max", map[string]string{annTurnComplete: "2026-06-06T11:10:00Z"})
	if _, err := reconcileTask(t, r, "t-max"); err != nil { // s1 turn -> turnsCompleted=1
		t.Fatalf("s1: %v", err)
	}
	annotate(t, "t-max", map[string]string{annTurnComplete: "2026-06-06T11:15:00Z"})
	if _, err := reconcileTask(t, r, "t-max"); err != nil { // hits cap -> terminate
		t.Fatalf("cap: %v", err)
	}
	tk2 := getTask(t, "t-max")
	if tk2.Status.Phase != "Succeeded" && tk2.Status.Phase != "Failed" {
		t.Errorf("phase = %q, want terminal after maxTurns", tk2.Status.Phase)
	}
}

func TestTaskReconcile_PodLostRecreatesThenFails(t *testing.T) {
	mkProject(t, "p-lost", 3)
	mkRepository(t, "r-lost", "p-lost")
	mkTask(t, "t-lost", "p-lost", "r-lost")
	setTaskPhase(t, "t-lost", "Running")
	annotate(t, "t-lost", map[string]string{
		annPodRecreations: "3",
		annCurrentTurn:    "turn-1",
	})

	fs := newFakeSession()
	r := newTaskReconciler(fs)
	// Pod absent (never created) + recreations exhausted -> Failed.
	if _, err := reconcileTask(t, r, "t-lost"); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	tk := getTask(t, "t-lost")
	if tk.Status.Phase != "Failed" {
		t.Errorf("phase = %q, want Failed (pod lost, retries exhausted)", tk.Status.Phase)
	}
}

func findCond(conds []metav1.Condition, typ string) *metav1.Condition {
	for i := range conds {
		if conds[i].Type == typ {
			return &conds[i]
		}
	}
	return nil
}
```

Add `metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"` to the test imports if not already present.

- [ ] **Step 2: Run, expect FAIL**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/controller/ -run 'TaskReconcile_Terminates|TaskReconcile_MaxTurns|TaskReconcile_PodLost' -v
```

Expected: `FAIL` - no real terminate (stub does not delete or set conditions), no maxTurns cap, no pod-loss handling.

- [ ] **Step 3: Implement.** Replace the `terminate` stub with the real method, add a `recreateOrFail` path, and a `turnCap` check.

Replace the stub:

```go
// terminate ends the Task: set phase, record turns, delete the wrapper
// session + Pod + Service, and leave the M5 write-back hook marker.
func (r *TaskReconciler) terminate(ctx context.Context, task *tatarav1alpha1.Task, phase, reason, msg string) (ctrl.Result, error) {
	baseURL := agent.BaseURL(task, task.Namespace)
	if err := r.Session.DeleteSession(ctx, baseURL); err != nil {
		// Best-effort: the pod is about to be deleted anyway; log via condition.
		meta.SetStatusCondition(&task.Status.Conditions, metav1.Condition{
			Type: "SessionDeleteFailed", Status: metav1.ConditionTrue,
			Reason: "DeleteError", Message: err.Error(),
		})
	}

	pod := &corev1.Pod{}
	pod.Name = agent.PodName(task)
	pod.Namespace = task.Namespace
	if err := r.Delete(ctx, pod); err != nil && !apierrors.IsNotFound(err) {
		return ctrl.Result{}, fmt.Errorf("delete wrapper pod: %w", err)
	}
	svc := &corev1.Service{}
	svc.Name = agent.PodName(task)
	svc.Namespace = task.Namespace
	if err := r.Delete(ctx, svc); err != nil && !apierrors.IsNotFound(err) {
		return ctrl.Result{}, fmt.Errorf("delete wrapper service: %w", err)
	}

	task.Status.Phase = phase
	meta.SetStatusCondition(&task.Status.Conditions, metav1.Condition{
		Type: "Ready", Status: metav1.ConditionTrue, Reason: reason, Message: msg,
		ObservedGeneration: task.Generation,
	})
	// M5 write-back hook: the SCM PR/MR + issue comment path keys off this
	// condition. M4 only sets it; M5 clears it once the change is landed.
	if phase == "Succeeded" {
		meta.SetStatusCondition(&task.Status.Conditions, metav1.Condition{
			Type: "WritebackPending", Status: metav1.ConditionTrue,
			Reason: "AwaitingM5", Message: "agent run complete; SCM write-back handled in M5",
			ObservedGeneration: task.Generation,
		})
	}
	if err := r.Status().Update(ctx, task); err != nil {
		return ctrl.Result{}, fmt.Errorf("set terminal status: %w", err)
	}
	r.updateInflightGauge(ctx)
	return ctrl.Result{}, nil
}
```

Add `"k8s.io/apimachinery/pkg/api/meta"` and `metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"` to the imports of `task_controller.go`.

Add the turn-cap helper and wire it into `driveTurns` right after the `annTurnComplete` check resolves a delivered callback, before picking the next Subtask:

```go
// turnCap returns the maximum turns allowed for this Task.
func turnCap(project *tatarav1alpha1.Project, task *tatarav1alpha1.Task) int {
	if task.Spec.MaxTurns > 0 {
		return task.Spec.MaxTurns
	}
	if project.Spec.Agent.MaxTurnsPerTask > 0 {
		return project.Spec.Agent.MaxTurnsPerTask
	}
	return 50
}
```

In `driveTurns`, after marking the executing Subtask Done and BEFORE `nextPendingSubtask`, insert:

```go
	if task.Status.TurnsCompleted >= turnCap(project, task) {
		return r.terminate(ctx, task, "Succeeded", "MaxTurnsReached",
			fmt.Sprintf("reached turn cap %d", turnCap(project, task)))
	}
```

(Note: `recordTurn` bumps `turnsCompleted` when it sees a delivered callback; the count reflects turns whose callback was processed. The plan turn closing increments it on the next `recordTurn`, so the s1 submission is turnsCompleted=1, matching the `MaxTurnsCap` test with `maxTurns=1` terminating on the following callback.)

Add the pod-loss handling. Replace `ensurePodAndService` so that, for an already-active Task whose Pod is absent, it recreates up to `maxPodRecreations`, else returns a sentinel that `Reconcile` turns into a terminate. Change `ensurePodAndService` to return `(recreatedExhausted bool, err error)` and update its single caller:

```go
// ensurePodAndService creates the wrapper Pod+Service if absent. For an
// already-active Task it counts recreations; when the budget is exhausted it
// returns exhausted=true so the caller fails the Task.
func (r *TaskReconciler) ensurePodAndService(ctx context.Context, project *tatarav1alpha1.Project, repo *tatarav1alpha1.Repository, task *tatarav1alpha1.Task) (bool, error) {
	pod := agent.BuildPod(project, repo, task, r.PodConfig)
	existing := &corev1.Pod{}
	err := r.Get(ctx, types.NamespacedName{Namespace: pod.Namespace, Name: pod.Name}, existing)
	switch {
	case apierrors.IsNotFound(err):
		if isActive(task.Status.Phase) {
			if r.podRecreations(task) >= maxPodRecreations {
				return true, nil
			}
			if err := r.bumpRecreations(ctx, task); err != nil {
				return false, err
			}
		}
		if err := r.Create(ctx, pod); err != nil {
			return false, fmt.Errorf("create wrapper pod: %w", err)
		}
	case err != nil:
		return false, fmt.Errorf("get wrapper pod: %w", err)
	}

	svc := agent.BuildService(project, repo, task, r.PodConfig)
	existingSvc := &corev1.Service{}
	err = r.Get(ctx, types.NamespacedName{Namespace: svc.Namespace, Name: svc.Name}, existingSvc)
	if apierrors.IsNotFound(err) {
		if err := r.Create(ctx, svc); err != nil {
			return false, fmt.Errorf("create wrapper service: %w", err)
		}
	} else if err != nil {
		return false, fmt.Errorf("get wrapper service: %w", err)
	}
	return false, nil
}

func (r *TaskReconciler) podRecreations(task *tatarav1alpha1.Task) int {
	n, _ := atoiSafe(task.Annotations[annPodRecreations])
	return n
}

func (r *TaskReconciler) bumpRecreations(ctx context.Context, task *tatarav1alpha1.Task) error {
	fresh := &tatarav1alpha1.Task{}
	if err := r.Get(ctx, types.NamespacedName{Namespace: task.Namespace, Name: task.Name}, fresh); err != nil {
		return fmt.Errorf("reload task for recreation bump: %w", err)
	}
	if fresh.Annotations == nil {
		fresh.Annotations = map[string]string{}
	}
	n, _ := atoiSafe(fresh.Annotations[annPodRecreations])
	fresh.Annotations[annPodRecreations] = fmt.Sprintf("%d", n+1)
	return r.Update(ctx, fresh)
}

func atoiSafe(s string) (int, bool) {
	n := 0
	if s == "" {
		return 0, false
	}
	for _, c := range s {
		if c < '0' || c > '9' {
			return 0, false
		}
		n = n*10 + int(c-'0')
	}
	return n, true
}
```

Update the caller in `Reconcile`:

```go
	exhausted, err := r.ensurePodAndService(ctx, &project, &repo, &task)
	if err != nil {
		r.Metrics.ReconcileResult("Task", "error")
		return ctrl.Result{}, err
	}
	if exhausted {
		res, err := r.terminate(ctx, &task, "Failed", "PodLost", "wrapper pod lost; recreation budget exhausted")
		if err != nil {
			r.Metrics.ReconcileResult("Task", "error")
			return ctrl.Result{}, err
		}
		r.Metrics.ReconcileResult("Task", "success")
		return res, nil
	}
```

- [ ] **Step 4: Run, expect PASS (full package)**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/controller/...
```

Expected: `ok` - termination, maxTurns cap, pod-loss fail, and all earlier controller tests green.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-operator && gofmt -w internal/controller && golangci-lint run ./internal/controller/...
git add internal/controller && git commit -m "feat: task termination, cleanup, maxTurns cap, bounded pod-loss retry"
```

---

## Task 9: Turn-complete callback server + poll backstop (httptest + envtest)

The in-cluster callback listener handles `POST /internal/turn-complete`. The wrapper posts `{turnId, state, finalText, stopReason, error}`. The handler: parses the body, observes `operator_turn_duration_seconds` (using the request's reported duration if present, else 0), resolves `turnId -> Task` via the `annCurrentTurn` annotation, writes `finalText` onto the executing Subtask's `status.result` (named by `annCurrentSubtask`), and bumps the Task's `annTurnComplete` annotation to now (which triggers a reconcile). The poll backstop is a `manager.Runnable` that, on a ticker, lists active Tasks with an in-flight turn and no recent callback and calls `GetTurn`; if the turn is terminal it injects the same result path. Tests: httptest for the handler against the envtest client; a unit test for the backstop's "should poll" decision.

**Files:**
- Create: `~/Documents/tatara/tatara-operator/internal/controller/turncallback.go`
- Create: `~/Documents/tatara/tatara-operator/internal/controller/turncallback_test.go`
- Modify: `~/Documents/tatara/tatara-operator/MEMORY.md`

- [ ] **Step 1: Write the failing test** (`internal/controller/turncallback_test.go`)

```go
package controller

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"k8s.io/apimachinery/pkg/types"

	"github.com/szymonrychu/tatara-operator/internal/obs"
)

func newCallbackServer() *CallbackServer {
	return &CallbackServer{
		Client:    k8sClient,
		Metrics:   obs.NewOperatorMetrics(prometheus.NewRegistry()),
		Namespace: testNS,
	}
}

func TestTurnComplete_RecordsResultAndRequeues(t *testing.T) {
	mkProject(t, "p-cb", 3)
	mkRepository(t, "r-cb", "p-cb")
	mkTask(t, "t-cb", "p-cb", "r-cb")
	mkSubtask(t, "t-cb-s1", "t-cb", 1)
	annotate(t, "t-cb", map[string]string{
		annCurrentTurn:    "turn-42",
		annCurrentSubtask: "t-cb-s1",
	})

	cb := newCallbackServer()
	body, _ := json.Marshal(map[string]any{
		"turnId": "turn-42", "state": "completed",
		"finalText": "subtask done well", "stopReason": "end_turn",
	})
	req := httptest.NewRequest(http.MethodPost, "/internal/turn-complete", bytes.NewReader(body))
	w := httptest.NewRecorder()
	cb.Handler().ServeHTTP(w, req)

	if w.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want 204; body=%s", w.Code, w.Body.String())
	}
	st := getSubtask(t, "t-cb-s1")
	if st.Status.Result != "subtask done well" {
		t.Errorf("subtask result = %q, want recorded", st.Status.Result)
	}
	tk := getTask(t, "t-cb")
	if tk.Annotations[annTurnComplete] == "" {
		t.Error("expected turn-complete annotation set to requeue the task")
	}
}

func TestTurnComplete_UnknownTurn404(t *testing.T) {
	cb := newCallbackServer()
	body, _ := json.Marshal(map[string]any{"turnId": "nope", "state": "completed"})
	req := httptest.NewRequest(http.MethodPost, "/internal/turn-complete", bytes.NewReader(body))
	w := httptest.NewRecorder()
	cb.Handler().ServeHTTP(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("status = %d, want 404", w.Code)
	}
}

func TestTurnComplete_PlanTurnNoSubtask(t *testing.T) {
	mkProject(t, "p-cb2", 3)
	mkRepository(t, "r-cb2", "p-cb2")
	mkTask(t, "t-cb2", "p-cb2", "r-cb2")
	annotate(t, "t-cb2", map[string]string{annCurrentTurn: "turn-plan"})

	cb := newCallbackServer()
	body, _ := json.Marshal(map[string]any{"turnId": "turn-plan", "state": "completed", "finalText": "planned"})
	req := httptest.NewRequest(http.MethodPost, "/internal/turn-complete", bytes.NewReader(body))
	w := httptest.NewRecorder()
	cb.Handler().ServeHTTP(w, req)
	if w.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want 204; body=%s", w.Code, w.Body.String())
	}
	tk := getTask(t, "t-cb2")
	if tk.Annotations[annTurnComplete] == "" {
		t.Error("plan-turn callback must still requeue the task")
	}
}

func TestResolveTaskByTurn(t *testing.T) {
	mkProject(t, "p-res", 3)
	mkRepository(t, "r-res", "p-res")
	mkTask(t, "t-res", "p-res", "r-res")
	annotate(t, "t-res", map[string]string{annCurrentTurn: "turn-find-me"})

	cb := newCallbackServer()
	tk, err := cb.resolveTaskByTurn(context.Background(), "turn-find-me")
	if err != nil {
		t.Fatalf("resolve: %v", err)
	}
	if tk.Name != "t-res" {
		t.Errorf("resolved = %q, want t-res", tk.Name)
	}
	if _, err := cb.resolveTaskByTurn(context.Background(), "missing"); err == nil {
		t.Error("expected error for unknown turn")
	}
	_ = types.NamespacedName{}
	_ = time.Now
}
```

- [ ] **Step 2: Run, expect FAIL**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/controller/ -run 'TurnComplete|ResolveTaskByTurn' -v
```

Expected: `FAIL` - undefined `CallbackServer`.

- [ ] **Step 3: Write the implementation** (`internal/controller/turncallback.go`)

```go
package controller

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"time"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/agent"
	"github.com/szymonrychu/tatara-operator/internal/obs"
)

// CallbackServer handles the in-cluster /internal/turn-complete endpoint the
// wrapper POSTs to on each turn, and runs the poll backstop for missed
// callbacks. It has no OIDC: INTERNAL_ADDR is not exposed via ingress.
type CallbackServer struct {
	Client    client.Client
	Metrics   *obs.OperatorMetrics
	Session   agent.Session
	Namespace string
}

type turnCompletePayload struct {
	TurnID          string  `json:"turnId"`
	State           string  `json:"state"`
	FinalText       string  `json:"finalText"`
	StopReason      string  `json:"stopReason"`
	Error           string  `json:"error"`
	DurationSeconds float64 `json:"durationSeconds"`
}

// Handler returns the http.Handler for POST /internal/turn-complete.
func (s *CallbackServer) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/internal/turn-complete", s.handleTurnComplete)
	return mux
}

func (s *CallbackServer) handleTurnComplete(w http.ResponseWriter, r *http.Request) {
	l := log.FromContext(r.Context())
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var p turnCompletePayload
	if err := json.NewDecoder(r.Body).Decode(&p); err != nil {
		http.Error(w, "bad body", http.StatusBadRequest)
		return
	}
	s.Metrics.ObserveTurnDuration(p.DurationSeconds)

	if err := s.recordResult(r.Context(), agent.TurnResult{
		State: p.State, FinalText: p.FinalText, StopReason: p.StopReason, Err: p.Error,
	}, p.TurnID); err != nil {
		if errors.Is(err, errTurnNotFound) {
			http.Error(w, "unknown turn", http.StatusNotFound)
			return
		}
		l.Error(err, "record turn result", "turn_id", p.TurnID)
		http.Error(w, "record failed", http.StatusInternalServerError)
		return
	}
	l.Info("recorded turn result", "action", "turn_complete", "turn_id", p.TurnID, "state", p.State)
	w.WriteHeader(http.StatusNoContent)
}

var errTurnNotFound = errors.New("no task with that current turn")

// recordResult writes finalText onto the executing Subtask (if any) and bumps
// the Task's turn-complete annotation to requeue its reconcile.
func (s *CallbackServer) recordResult(ctx context.Context, tr agent.TurnResult, turnID string) error {
	task, err := s.resolveTaskByTurn(ctx, turnID)
	if err != nil {
		return err
	}
	if sub := task.Annotations[annCurrentSubtask]; sub != "" {
		st := &tatarav1alpha1.Subtask{}
		if err := s.Client.Get(ctx, types.NamespacedName{Namespace: s.Namespace, Name: sub}, st); err == nil {
			st.Status.Result = tr.FinalText
			if err := s.Client.Status().Update(ctx, st); err != nil {
				return fmt.Errorf("write subtask result: %w", err)
			}
		} else if !apierrors.IsNotFound(err) {
			return fmt.Errorf("get executing subtask: %w", err)
		}
	}
	if task.Annotations == nil {
		task.Annotations = map[string]string{}
	}
	task.Annotations[annTurnComplete] = time.Now().UTC().Format(time.RFC3339)
	if err := s.Client.Update(ctx, task); err != nil {
		return fmt.Errorf("requeue task: %w", err)
	}
	return nil
}

// resolveTaskByTurn finds the Task whose current-turn annotation matches turnID.
func (s *CallbackServer) resolveTaskByTurn(ctx context.Context, turnID string) (*tatarav1alpha1.Task, error) {
	var list tatarav1alpha1.TaskList
	if err := s.Client.List(ctx, &list, client.InNamespace(s.Namespace)); err != nil {
		return nil, fmt.Errorf("list tasks: %w", err)
	}
	for i := range list.Items {
		if list.Items[i].Annotations[annCurrentTurn] == turnID {
			return &list.Items[i], nil
		}
	}
	return nil, errTurnNotFound
}

// PollOnce polls in-flight turns for delivered results that missed a callback.
// It is the backstop body; the ticker loop calls it.
func (s *CallbackServer) PollOnce(ctx context.Context) {
	var list tatarav1alpha1.TaskList
	if err := s.Client.List(ctx, &list, client.InNamespace(s.Namespace)); err != nil {
		return
	}
	for i := range list.Items {
		task := &list.Items[i]
		turn := task.Annotations[annCurrentTurn]
		if turn == "" || isTerminal(task.Status.Phase) || task.Annotations[annTurnComplete] != "" {
			continue
		}
		tr, err := s.Session.GetTurn(ctx, agent.BaseURL(task, s.Namespace), turn)
		if err != nil {
			continue
		}
		if tr.State == "completed" || tr.State == "failed" {
			_ = s.recordResult(ctx, tr, turn)
		}
	}
}

// Start runs the callback HTTP server and the poll backstop until ctx is done.
// It implements sigs.k8s.io/controller-runtime/pkg/manager.Runnable.
func (s *CallbackServer) Start(ctx context.Context, addr string) error {
	srv := &http.Server{Addr: addr, Handler: s.Handler(), ReadHeaderTimeout: 5 * time.Second}
	go func() {
		t := time.NewTicker(pollRequeue)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				s.PollOnce(ctx)
			}
		}
	}()
	go func() {
		<-ctx.Done()
		_ = srv.Shutdown(context.Background())
	}()
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return fmt.Errorf("callback server: %w", err)
	}
	return nil
}
```

- [ ] **Step 2 (repeat run): Run, expect PASS**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./internal/controller/ -run 'TurnComplete|ResolveTaskByTurn' -v
```

Expected: `ok`.

- [ ] **Step 4: Record the design decision in `MEMORY.md`** (append one dated line):

```
- 2026-06-06 [M4] Turn<->Task correlation via annotations (tatara.dev/current-turn,
  current-subtask, turn-complete, pod-recreations), not new CRD status fields, to
  avoid re-opening the M0 schema. M5 write-back keys off the WritebackPending
  condition set on Succeeded.
```

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-operator && gofmt -w internal/controller && golangci-lint run ./internal/controller/...
git add internal/controller MEMORY.md && git commit -m "feat: turn-complete callback server + poll backstop"
```

---

## Task 10: Wire TaskReconciler + callback server into `cmd/manager/main.go`

Extend the M1 `addReconcilers` to also register `TaskReconciler`, and add the callback server as a manager `Runnable` listening on `INTERNAL_ADDR`. A unit test pins the config-to-PodConfig mapping; the manager bootstrap stays untested per controller-runtime convention.

**Files:**
- Modify: `~/Documents/tatara/tatara-operator/cmd/manager/wire.go`
- Modify: `~/Documents/tatara/tatara-operator/cmd/manager/wire_test.go`
- Modify: `~/Documents/tatara/tatara-operator/cmd/manager/main.go`

- [ ] **Step 1: Append the failing test** (`cmd/manager/wire_test.go`)

```go
func TestPodConfigFromConfig(t *testing.T) {
	cfg := config.Config{
		Namespace:           "tatara",
		InternalAddr:        "http://op-internal.tatara.svc:9090",
		AnthropicSecretName: "anthropic",
		CLIOIDCSecretName:   "tatara-cli-oidc",
	}
	got := podConfigFromConfig(cfg)
	want := agent.PodConfig{
		Namespace:           "tatara",
		InternalAddr:        "http://op-internal.tatara.svc:9090",
		AnthropicSecretName: "anthropic",
		CLIOIDCSecretName:   "tatara-cli-oidc",
	}
	if got != want {
		t.Errorf("podConfigFromConfig = %+v, want %+v", got, want)
	}
}
```

Add `"github.com/szymonrychu/tatara-operator/internal/agent"` to the test imports. If M0 named the config field for `INTERNAL_ADDR` something other than `InternalAddr`, use the M0 name in both the test and `wire.go`.

- [ ] **Step 2: Run, expect FAIL**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./cmd/manager/ -run TestPodConfigFromConfig -v
```

Expected: build failure - `undefined: podConfigFromConfig`.

- [ ] **Step 3: Implement.** Add to `cmd/manager/wire.go`:

```go
// podConfigFromConfig maps operator config to the wrapper Pod/Service builder
// config.
func podConfigFromConfig(cfg config.Config) agent.PodConfig {
	return agent.PodConfig{
		Namespace:           cfg.Namespace,
		InternalAddr:        cfg.InternalAddr,
		AnthropicSecretName: cfg.AnthropicSecretName,
		CLIOIDCSecretName:   cfg.CLIOIDCSecretName,
	}
}
```

Import `"github.com/szymonrychu/tatara-operator/internal/agent"` and `"github.com/szymonrychu/tatara-operator/internal/auth"` in `wire.go`. Extend `addReconcilers` to register the Task reconciler with a real `httpSession` whose bearer comes from a wrapper-audience TokenSource:

```go
	wrapperTokens := auth.NewTokenSource(auth.TokenSourceConfig{
		TokenURL:     cfg.OIDCIssuer + "/protocol/openid-connect/token",
		ClientID:     cfg.OperatorOIDCClientID,
		ClientSecret: cfg.OperatorOIDCClientSecret,
		Audience:     "tatara-claude-code-wrapper",
	})
	if err := (&controller.TaskReconciler{
		Client:    mgr.GetClient(),
		Scheme:    mgr.GetScheme(),
		Metrics:   metrics,
		Session:   agent.NewHTTPSession(wrapperTokens.Token),
		PodConfig: podConfigFromConfig(cfg),
	}).SetupWithManager(mgr); err != nil {
		return fmt.Errorf("setup TaskReconciler: %w", err)
	}
```

NOTE on the token-URL: use the exact Keycloak token endpoint the M0 TokenSource expects. If M0/config exposes a discovered token endpoint or a dedicated `OIDCTokenURL` field, use that instead of string-concatenating `/protocol/openid-connect/token`; match M0 exactly.

Add a callback-server `Runnable` to `addReconcilers` (or a sibling `addCallbackServer`; keep it in `addReconcilers` for one call site). The callback server needs its own wrapper Session for the poll backstop:

```go
	cbServer := &controller.CallbackServer{
		Client:    mgr.GetClient(),
		Metrics:   metrics,
		Session:   agent.NewHTTPSession(wrapperTokens.Token),
		Namespace: cfg.Namespace,
	}
	if err := mgr.Add(callbackRunnable{srv: cbServer, addr: cfg.InternalAddr}); err != nil {
		return fmt.Errorf("add callback server: %w", err)
	}
```

Add the runnable adapter (the manager passes a context to `Start`; the callback server's `Start` takes `(ctx, addr)`):

```go
type callbackRunnable struct {
	srv  *controller.CallbackServer
	addr string
}

func (c callbackRunnable) Start(ctx context.Context) error {
	return c.srv.Start(ctx, normalizeAddr(c.addr))
}

// normalizeAddr strips a URL scheme/host from INTERNAL_ADDR, yielding a
// listen address (":9090"). INTERNAL_ADDR is a URL for the wrapper's
// DEFAULT_CALLBACK_URL; the server listens on its port.
func normalizeAddr(internalAddr string) string {
	if i := indexByte(internalAddr, ':'); i >= 0 {
		// take the last colon-group as :port
		last := internalAddr
		for {
			j := indexByte(last, ':')
			if j < 0 {
				break
			}
			last = last[j+1:]
		}
		return ":" + last
	}
	return internalAddr
}

func indexByte(s string, b byte) int {
	for i := 0; i < len(s); i++ {
		if s[i] == b {
			return i
		}
	}
	return -1
}
```

Import `"context"` in `wire.go`.

NOTE: if M0's `config.Config` already carries a separate listen-address scalar for the internal server distinct from the callback URL, use it directly and delete `normalizeAddr`. KISS (rule 2): a single `INTERNAL_ADDR` URL drives both `DEFAULT_CALLBACK_URL` (full URL into the pod env) and the listen port (parsed here). State the chosen interpretation in the commit body.

`cmd/manager/main.go` already calls `addReconcilers(mgr, cfg, operatorMetrics)` from M1; no further `main.go` change is needed beyond confirming it compiles. If M1 wired only reconcilers and you added the callback server inside `addReconcilers`, `main.go` is unchanged. Verify with a build.

- [ ] **Step 4: Run, expect PASS + full build**

```bash
cd ~/Documents/tatara/tatara-operator && go test ./cmd/manager/ -run 'TestPodConfigFromConfig|TestIngestConfigFromConfig' -v
go build ./...
```

Expected: both wire tests pass and the whole module builds with the Task reconciler + callback server wired in.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-operator && gofmt -w cmd/manager && golangci-lint run ./cmd/manager/...
git add cmd/manager && git commit -m "feat: wire TaskReconciler + turn-complete callback server into manager"
```

---

## Task 11: Full-package verification + ROADMAP update

**Files:**
- Modify: `~/Documents/tatara/tatara-operator/ROADMAP.md`

- [ ] **Step 1: Run the full test suite with race + vet**

```bash
cd ~/Documents/tatara/tatara-operator && go vet ./... && go test -race ./...
```

Expected: `ok` for `internal/agent`, `internal/controller`, `internal/obs`, `cmd/manager`, and all M0-M3 packages. No race warnings. If envtest binaries are missing, run `make envtest` / `setup-envtest use` per the M1 suite's expectation before re-running.

- [ ] **Step 2: Confirm metric names are exactly the pin-set strings**

```bash
cd ~/Documents/tatara/tatara-operator && grep -rn "operator_turn_duration_seconds\|operator_tasks_inflight" internal/obs/metrics.go
```

Expected: both names present verbatim.

- [ ] **Step 3: Confirm callback path + wrapper paths are exact**

```bash
cd ~/Documents/tatara/tatara-operator && grep -rn "/internal/turn-complete\|/v1/messages\|/v1/session" internal/agent internal/controller
```

Expected: `/internal/turn-complete` in `turncallback.go` (handler) and `task_controller.go`/`pod.go` (callback URL); `/v1/messages` + `/v1/session` only in `internal/agent/http.go`.

- [ ] **Step 4: Update `ROADMAP.md`** - move M4 from planned to done, leaving M5/M6:

Replace the M4 line under the planned milestones with a done marker, e.g.:

```
- [x] M4 - Task reconciler + turn loop (wrapper Pod/Service, plan turn,
      subtask iteration, concurrency gate, callback server + poll backstop,
      bounded pod-loss retry). SCM write-back deferred to M5 via the
      WritebackPending condition hook.
```

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-operator && git add ROADMAP.md && git commit -m "docs: M4 task reconciler + turn loop complete"
```

---

## Self-Review

**Spec coverage (TaskReconciler section, the exact loop):**
1. Gate on `maxConcurrentTasks` + `operator_tasks_inflight` gauge - Task 6 (`atConcurrencyCap`, `capRequeue`), gauge in Task 1 + `updateInflightGauge`.
2. Spawn Pod+Service (phase=Planning), wait for readiness - Task 6 (`ensurePodAndService`), Task 7 (`podReady`, `RequeueAfter` until ready).
3. Turn 0 plan turn (goal + decompose-into-Subtasks-via-MCP) - Task 5 (`planTurnText`), Task 7 (`driveTurns` plan branch).
4. Iterate: mark current Subtask Done (result from `TurnResult.FinalText`), pick next Pending by `order`, submit title+detail, phase=Running; agent may append Subtasks mid-run (re-listed each reconcile) - Task 7 (`driveTurns`, `markSubtaskDone`, `nextPendingSubtask`), result written by the callback (Task 9 `recordResult`).
5. Terminate on no Pending OR maxTurns/timeout: phase Succeeded/Failed, record `turnsCompleted`, DELETE /v1/session, delete Pod+Service; PR/MR + comment is M5 (hook = `WritebackPending` condition) - Task 8 (`terminate`, `turnCap`).
6. Bounded pod-loss retry then Fail - Task 8 (`ensurePodAndService` recreate budget, `PodLost` terminate).
7. Observe `operator_turn_duration_seconds` - Task 1 + Task 9 (`ObserveTurnDuration` in the callback handler).
8. Callback `POST /internal/turn-complete` (no OIDC, in-cluster) + poll backstop - Task 9 (`CallbackServer`, `PollOnce`, `Start`).
9. Session interface + httpSession (audience `tatara-claude-code-wrapper`, OIDC bearer from TokenSource) - Tasks 2-3.
10. Pod/Service builders with exact env/secret/owner-ref/ports - Task 4.
11. Wire into `cmd/manager/main.go` - Task 10.

**Type consistency:** `Session` (SubmitTurn/GetTurn/DeleteSession) is identical across Tasks 2/3/6(fake)/10. `TurnResult{State,FinalText,StopReason,Err}` matches the pin set. `agent.PodConfig` fields are identical in Task 4, 6, 10. `agent.PodName`/`agent.BaseURL` used consistently. Annotation constants (`annCurrentTurn`, `annCurrentSubtask`, `annTurnComplete`, `annPodRecreations`) declared once in `task_controller.go` (Task 6) and reused in Tasks 7-9. Metric accessors `ObserveTurnDuration`/`SetTasksInflight` defined in Task 1, used in Tasks 6/8/9.

**Placeholder scan:** No TBD/TODO. The only deliberate deferral is the M5 SCM write-back, made concrete as the `WritebackPending` condition hook (stated in Tasks 8/9 and the spec). The `terminate` stub in Task 7 is explicitly replaced in Task 8. The `recreateOrFail`/cap/maxTurns constants declared in Task 6 are explicitly consumed in Tasks 7-8 (noted inline).

**Known assumptions flagged for the implementer:** CRD Go field names must be verified against M0's `api/v1alpha1/*_types.go` (flagged in Tasks 4, 6); the `config.Config` field for `INTERNAL_ADDR` and the OIDC token URL must match M0 (flagged in Task 10); the M1 `suite_test.go` is reused, not recreated (precondition). The annotation-based turn correlation is recorded in `MEMORY.md` (Task 9) as the non-obvious decision per rule.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-06-tatara-operator-m4-task-turn-loop.md`. Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh sonnet subagent per task, review between tasks. Tasks 1-5 are largely independent (metrics, agent client, agent builders, pure helpers) and can be parallelized; Tasks 6-8 are sequential (same reconciler file); Tasks 9-11 follow.
2. **Inline Execution** - execute tasks in this session via executing-plans with checkpoints.

Which approach?
