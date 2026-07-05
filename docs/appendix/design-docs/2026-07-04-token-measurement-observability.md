# Token Measurement + Observability Implementation Plan

> For agentic workers: this plan is executed with the
> **superpowers:subagent-driven-development** sub-skill (REQUIRED SUB-SKILL:
> superpowers:subagent-driven-development). Each `### Task N` is a self-contained
> TDD unit with its own failing-test -> implement -> pass -> commit cycle. Tasks
> 1-3 (wrapper) and Task 4-5 (operator) and Task 6 (observability) are in three
> separate repos; the deploy tasks 7-9 are sequenced last. Run tasks in the given
> order (later tasks depend on earlier metric-label shapes). Do NOT batch a code
> task with its deploy task.

## Goal

Component 6 (Measurement) of the token-conservation redesign
(`tatara/docs/superpowers/specs/2026-07-04-token-conservation-design.md`). This
is the prerequisite instrument and the gate for component 4 (cache reclaim).
Deliver: (a) wrapper token+cost Prometheus metrics carrying `kind`/`repo`/`project`
labels; (b) the operator pod env that exports those label values; (c) a
tatara-observability `$`-spend-per-kind/repo dashboard panel and a
cache-hit-ratio panel; (d) confirmation that the operator-side
`operator_task_tokens_total` family emits live series. Acceptance gate:
`ccw_turn_tokens_total{type="cache_read"}` is a visible non-empty series for a
fresh pod's turn-0.

## Architecture

The wrapper pod is short-lived: it pushes its `ccw_*`/`tatara_wrapper_*` metric
families to the operator's push-receiver (`OPERATOR_PUSH_URL` ->
`/internal/metrics/push`), which unions each family's label set, stamps
`run_id`/`pod`/`job`, ages series out on a TTL, and re-exposes them on the
operator's own `/metrics` for normal Prometheus scrape
(`tatara-operator/internal/pushmetrics/receiver.go`). Therefore adding
`kind`/`repo`/`project` labels at the wrapper emit site flows through to
Prometheus without any receiver change (the receiver already computes the label
union per family and forwards the `ccw_` prefix).

- Token/cost metrics are emitted in the wrapper at
  `tatara-claude-code-wrapper/internal/session/session.go:957` (`meterTokens`).
  Today: `TurnTokensTotal` labels `type,model`; `TurnCostUSD` is an unlabelled
  `prometheus.Counter`. This plan adds `kind,repo,project` to both.
- The label VALUES are per-pod constants (one Task per pod). They arrive as pod
  env set by the operator: `TATARA_PROJECT` already exists
  (`tatara-operator/internal/agent/pod.go:434`); `TATARA_KIND` and `TATARA_REPO`
  are added next to it. The wrapper reads them in
  `cmd/wrapper/config.go`, threads them into `session.Config`, and `meterTokens`
  supplies them at emit.
- The operator-side family `operator_task_tokens_total`
  (`tatara-operator/internal/obs/operator_metrics.go:264`) is emitted at
  `internal/controller/turncallback.go:290` on every recorded turn-complete; it
  is confirmed live (Task 5), not re-wired.
- Dashboards are Terraform-provisioned:
  `tatara-observability/dashboards.tf` renders
  `dashboards/task-delivery.json` into the Grafana `Tatara` folder on merge to
  `main` (`.github/workflows/apply.yml` runs `terraform apply`). New panels are
  JSON edits to that file.

## Tech Stack

- Go 1.25 (wrapper), Go 1.26 (operator). `prometheus/client_golang`,
  `stretchr/testify`. Build/test via `mise exec -- go test ./...`.
- Terraform + Grafana provider (observability). Dashboards as JSON.
- Deploy via tatara-helmfile GitOps (chart version + `image.tag` bump per
  release); observability applies via its own GitHub Actions on merge to `main`.

## Global Constraints

- Newest stable Go; pin the exact minor in go.mod. gofmt + golangci-lint must
  pass. Wrap errors with `%w`. Table-driven tests with `t.Run`.
- KISS. No tech debt; if complex, note rationale in MEMORY.md.
- JSON logs via stdlib log/slog. Expose /metrics Prometheus on every service.
  Log business actions at INFO with structured fields.
- Charts via `helm create` then edited, never hand-rolled. Charts
  cluster-agnostic.
- values.yaml rule: NO plain ENVs, NO lists in values.yaml. camelCase scalar in
  values.yaml -> kebab-case key in ConfigMap/Secret -> workload consumes via
  envFrom. List-shaped data goes into a templated ConfigMap read at runtime.
  (CRD spec fields are exempt - typed API fields, not helm values; a
  map[string]string on a CRD spec is fine.)
- Model IDs are authoritative literals: claude-opus-4-8 (Opus), claude-sonnet-5
  (Sonnet). Effort enum: low|medium|high|xhigh|max.
- Deploy ONLY via tatara-helmfile GitOps: merge component repo main (CI
  builds+pushes image/chart), then a tatara-helmfile MR bumping BOTH the chart
  version AND the pinned image.tag for the release. Never kubectl
  set-image/patch to ship. Project CR value changes are tatara-helmfile values
  edits.
- Branch flow: worktree off main -> develop -> merge to component main -> deploy
  from main only.

---

### Task 1: Wrapper metrics carry kind/repo/project labels

**Files:**
- Modify: `tatara-claude-code-wrapper/internal/metrics/metrics.go:48` (label list
  of `TurnTokensTotal`), `:49` (`TurnCostUSD` type), `:123-126` (registrations),
  `:6-49` (struct field type for `TurnCostUSD`).
- Test: `tatara-claude-code-wrapper/internal/metrics/metrics_test.go` (append a
  table test).

**Interfaces:**
- Produces: `Metrics.TurnTokensTotal *prometheus.CounterVec` with labels
  `[]string{"type","model","kind","repo","project"}`.
- Produces: `Metrics.TurnCostUSD *prometheus.CounterVec` (was
  `prometheus.Counter`) with labels `[]string{"kind","repo","project"}`.
- Consumed by: `session.Manager.meterTokens` (Task 3).

- [ ] **Step 1: Write the failing table test.** Append to
  `internal/metrics/metrics_test.go`:

```go
// TestMetrics_TokenCostLabels asserts the token+cost metrics carry the
// kind/repo/project labels sourced from the pod env (component 6). Table-driven
// over both families.
func TestMetrics_TokenCostLabels(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := metrics.New(reg)

	m.TurnTokensTotal.WithLabelValues("cache_read", "claude-sonnet-5", "review", "tatara-operator", "tatara").Add(42)
	m.TurnCostUSD.WithLabelValues("review", "tatara-operator", "tatara").Add(0.5)

	mfs, err := reg.Gather()
	require.NoError(t, err)
	byName := map[string]*dto.MetricFamily{}
	for _, mf := range mfs {
		byName[mf.GetName()] = mf
	}

	cases := []struct {
		metric     string
		wantLabels map[string]string
		wantValue  float64
	}{
		{
			metric:     "ccw_turn_tokens_total",
			wantLabels: map[string]string{"type": "cache_read", "model": "claude-sonnet-5", "kind": "review", "repo": "tatara-operator", "project": "tatara"},
			wantValue:  42,
		},
		{
			metric:     "ccw_turn_cost_usd_total",
			wantLabels: map[string]string{"kind": "review", "repo": "tatara-operator", "project": "tatara"},
			wantValue:  0.5,
		},
	}
	for _, tc := range cases {
		t.Run(tc.metric, func(t *testing.T) {
			mf := byName[tc.metric]
			require.NotNil(t, mf, "family %s not registered", tc.metric)
			require.Len(t, mf.GetMetric(), 1)
			got := map[string]string{}
			for _, lp := range mf.GetMetric()[0].GetLabel() {
				got[lp.GetName()] = lp.GetValue()
			}
			require.Equal(t, tc.wantLabels, got)
			require.Equal(t, tc.wantValue, mf.GetMetric()[0].GetCounter().GetValue())
		})
	}
}
```

  Add the dto import to the test file's import block:

```go
	dto "github.com/prometheus/client_model/go"
```

- [ ] **Step 2: Run it, expect FAIL (compile error).** `TurnCostUSD` is still a
  `prometheus.Counter`, so `WithLabelValues` does not exist and the label arity
  on `TurnTokensTotal` is wrong.

```
mise exec -- go test ./internal/metrics/
```

  Expected: `./metrics_test.go:NN:XX: m.TurnCostUSD.WithLabelValues undefined (type prometheus.Counter has no field or method WithLabelValues)` and/or a
  `inconsistent label cardinality: expected 2 label values but got 5` panic.

- [ ] **Step 3: Change the struct field type.** In `internal/metrics/metrics.go`
  edit the `TurnCostUSD` field (line 49):

```go
	TurnTokensTotal *prometheus.CounterVec // labels: type=input|output|cache_read|cache_creation, model, kind, repo, project
	TurnCostUSD     *prometheus.CounterVec // labels: kind, repo, project (from result.json total_cost_usd)
```

- [ ] **Step 4: Extend the registrations.** In `New` change the two collector
  constructors (lines 123-126):

```go
		TurnTokensTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "ccw_turn_tokens_total", Help: "Claude tokens consumed per turn, summed across the turn, by token type, model, Task kind, repo, and project."}, []string{"type", "model", "kind", "repo", "project"}),
		TurnCostUSD: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "ccw_turn_cost_usd_total", Help: "Cumulative Claude turn cost in USD (from result.json total_cost_usd when present), by Task kind, repo, and project."}, []string{"kind", "repo", "project"}),
```

  `m.TurnCostUSD` is already in the `reg.MustRegister(...)` list (line 144); no
  change needed there (a `*CounterVec` is a `Collector` just like `Counter`).

- [ ] **Step 5: Run it, expect PASS.**

```
mise exec -- go test ./internal/metrics/
```

  Expected: `ok  	github.com/szymonrychu/tatara-claude-code-wrapper/internal/metrics`.
  (The wrapper will not yet compile as a whole - `session.go` still calls the old
  `TurnCostUSD.Add` / 2-arg `TurnTokensTotal`; that is fixed in Task 3. Scope
  this task's verification to the `internal/metrics` package.)

- [ ] **Step 6: Commit.**

```
git add internal/metrics/metrics.go internal/metrics/metrics_test.go
git commit -m "feat: add kind/repo/project labels to wrapper token+cost metrics"
```

---

### Task 2: Wrapper config reads TATARA_KIND / TATARA_REPO / TATARA_PROJECT

**Files:**
- Modify: `tatara-claude-code-wrapper/cmd/wrapper/config.go:14-94` (add three
  fields), `:121-181` (populate them).
- Test: `tatara-claude-code-wrapper/cmd/wrapper/config_test.go` (append).

**Interfaces:**
- Produces: `config.Kind`, `config.RepoName`, `config.Project string` sourced
  from env `TATARA_KIND`, `TATARA_REPO`, `TATARA_PROJECT` (default `""`).
- Consumed by: `app.go` session wiring (Task 3).

- [ ] **Step 1: Write the failing test.** Append to `cmd/wrapper/config_test.go`:

```go
func TestLoadConfig_MetricLabelEnv(t *testing.T) {
	t.Setenv("TATARA_KIND", "review")
	t.Setenv("TATARA_REPO", "tatara-operator")
	t.Setenv("TATARA_PROJECT", "tatara")

	cfg, err := loadConfig(nil)
	require.NoError(t, err)
	require.Equal(t, "review", cfg.Kind)
	require.Equal(t, "tatara-operator", cfg.RepoName)
	require.Equal(t, "tatara", cfg.Project)
}

func TestLoadConfig_MetricLabelEnv_DefaultsEmpty(t *testing.T) {
	t.Setenv("TATARA_KIND", "")
	t.Setenv("TATARA_REPO", "")
	t.Setenv("TATARA_PROJECT", "")

	cfg, err := loadConfig(nil)
	require.NoError(t, err)
	require.Equal(t, "", cfg.Kind)
	require.Equal(t, "", cfg.RepoName)
	require.Equal(t, "", cfg.Project)
}
```

  If `config_test.go` does not already import testify require, add
  `"github.com/stretchr/testify/require"` to its import block. Confirm the
  package clause matches the existing file (`package main`).

- [ ] **Step 2: Run it, expect FAIL (compile error).**

```
mise exec -- go test ./cmd/wrapper/ -run TestLoadConfig_MetricLabelEnv
```

  Expected: `cfg.Kind undefined (type config has no field or method Kind)`.

- [ ] **Step 3: Add the fields.** In `cmd/wrapper/config.go`, inside the `config`
  struct, after the `PodName string` field (line 36 area), add:

```go
	// Metric identity labels (component 6): the operator sets these on the pod
	// env so the wrapper's per-turn token/cost metrics attribute spend to a
	// Task kind, repo, and project. Empty for values the operator does not set
	// (e.g. RepoName is empty for project-scoped kinds).
	Kind     string
	RepoName string
	Project  string
```

- [ ] **Step 4: Populate them.** In `loadConfig`, inside the `cfg := config{...}`
  literal (after `PodName: envOr("POD_NAME", "")`, line 140), add:

```go
		Kind:     envOr("TATARA_KIND", ""),
		RepoName: envOr("TATARA_REPO", ""),
		Project:  envOr("TATARA_PROJECT", ""),
```

- [ ] **Step 5: Run it, expect PASS.**

```
mise exec -- go test ./cmd/wrapper/ -run TestLoadConfig_MetricLabelEnv
```

  Expected: `ok  	github.com/szymonrychu/tatara-claude-code-wrapper/cmd/wrapper`.

- [ ] **Step 6: Commit.**

```
git add cmd/wrapper/config.go cmd/wrapper/config_test.go
git commit -m "feat: wrapper config reads TATARA_KIND/TATARA_REPO/TATARA_PROJECT metric labels"
```

---

### Task 3: meterTokens emits the labels; wire config -> session.Config

**Files:**
- Modify: `tatara-claude-code-wrapper/internal/session/session.go` (session.Config
  struct add fields; `meterTokens` at :957 supply labels).
- Modify: `tatara-claude-code-wrapper/cmd/wrapper/app.go:143-154` (pass the three
  fields into `session.Config`).
- Test: `tatara-claude-code-wrapper/internal/session/session_test.go` (append a
  `meterTokens` test).

**Interfaces:**
- Consumes: `session.Config.Kind`, `.RepoName`, `.Project string`.
- Consumes: `Metrics.TurnTokensTotal.WithLabelValues(type, model, kind, repo, project)`,
  `Metrics.TurnCostUSD.WithLabelValues(kind, repo, project)` (Task 1).
- Produces: labelled series on turn-complete.

- [ ] **Step 1: Write the failing test.** Append to
  `internal/session/session_test.go`:

```go
func TestMeterTokens_EmitsIdentityLabels(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := metrics.New(reg)
	mgr := session.New(
		session.Config{Kind: "implement", RepoName: "tatara-operator", Project: "tatara"},
		turn.NewStore(), m, slog.New(slog.NewTextHandler(io.Discard, nil)),
		time.Now, func() string { return "t1" },
	)

	mgr.MeterTokensForTest(session.HookResult{
		TurnTokens: []session.TurnTokens{
			{Model: "claude-opus-4-8", Input: 100, Output: 20, CacheRead: 500, CacheCreation: 30},
		},
		ResultJSON: json.RawMessage(`{"total_cost_usd": 0.25}`),
	})

	mfs, err := reg.Gather()
	require.NoError(t, err)
	byName := map[string]*dto.MetricFamily{}
	for _, mf := range mfs {
		byName[mf.GetName()] = mf
	}

	// cache_read series carries the identity labels + value 500.
	tok := byName["ccw_turn_tokens_total"]
	require.NotNil(t, tok)
	var cacheRead *dto.Metric
	for _, mc := range tok.GetMetric() {
		lbl := map[string]string{}
		for _, lp := range mc.GetLabel() {
			lbl[lp.GetName()] = lp.GetValue()
		}
		if lbl["type"] == "cache_read" {
			cacheRead = mc
			require.Equal(t, "implement", lbl["kind"])
			require.Equal(t, "tatara-operator", lbl["repo"])
			require.Equal(t, "tatara", lbl["project"])
			require.Equal(t, "claude-opus-4-8", lbl["model"])
		}
	}
	require.NotNil(t, cacheRead, "cache_read series missing")
	require.Equal(t, float64(500), cacheRead.GetCounter().GetValue())

	// cost carries the identity labels + value 0.25.
	cost := byName["ccw_turn_cost_usd_total"]
	require.NotNil(t, cost)
	require.Len(t, cost.GetMetric(), 1)
	lbl := map[string]string{}
	for _, lp := range cost.GetMetric()[0].GetLabel() {
		lbl[lp.GetName()] = lp.GetValue()
	}
	require.Equal(t, map[string]string{"kind": "implement", "repo": "tatara-operator", "project": "tatara"}, lbl)
	require.Equal(t, 0.25, cost.GetMetric()[0].GetCounter().GetValue())
}
```

  Ensure the test file imports: `"encoding/json"`, `"io"`, `"log/slog"`,
  `"time"`, `dto "github.com/prometheus/client_model/go"`,
  `"github.com/prometheus/client_golang/prometheus"`,
  `"github.com/stretchr/testify/require"`, the `metrics`, `session`, and `turn`
  packages (match the existing module import paths already used in the test
  files). This test lives in the external `session_test` package (it references
  `session.New`); `meterTokens` is unexported, so Step 3 adds a tiny exported
  test shim.

- [ ] **Step 2: Run it, expect FAIL (compile error).**

```
mise exec -- go test ./internal/session/ -run TestMeterTokens_EmitsIdentityLabels
```

  Expected: `mgr.MeterTokensForTest undefined` and `unknown field Kind in struct
  literal of type session.Config`.

- [ ] **Step 3: Add the session.Config fields + the emit change + test shim.** In
  `internal/session/session.go`, add to the `Config` struct (after `Repo string`,
  line ~/type Config):

```go
	// Kind, RepoName, Project are the pod's metric-identity labels (component 6),
	// set once by the operator env and stamped onto every per-turn token/cost
	// series so spend attributes to a Task kind, repo, and project.
	Kind     string
	RepoName string
	Project  string
```

  Change `meterTokens` (lines 957-978) to supply the labels:

```go
func (mgr *Manager) meterTokens(r HookResult) {
	kind, repo, project := mgr.cfg.Kind, mgr.cfg.RepoName, mgr.cfg.Project
	for _, t := range r.TurnTokens {
		model := t.Model
		if model == "" {
			model = "unknown"
		}
		mgr.m.TurnTokensTotal.WithLabelValues("input", model, kind, repo, project).Add(float64(t.Input))
		mgr.m.TurnTokensTotal.WithLabelValues("output", model, kind, repo, project).Add(float64(t.Output))
		mgr.m.TurnTokensTotal.WithLabelValues("cache_read", model, kind, repo, project).Add(float64(t.CacheRead))
		mgr.m.TurnTokensTotal.WithLabelValues("cache_creation", model, kind, repo, project).Add(float64(t.CacheCreation))
	}
	if len(r.ResultJSON) > 0 {
		var rj struct {
			TotalCostUSD *float64 `json:"total_cost_usd"`
		}
		if err := json.Unmarshal(r.ResultJSON, &rj); err != nil {
			mgr.log.Warn("turn cost: malformed result.json, skipping", "err", err)
		} else if rj.TotalCostUSD != nil {
			mgr.m.TurnCostUSD.WithLabelValues(kind, repo, project).Add(*rj.TotalCostUSD)
		}
	}
}

// MeterTokensForTest exposes meterTokens to the external session_test package so
// the metric-label wiring can be asserted without driving a full turn.
func (mgr *Manager) MeterTokensForTest(r HookResult) { mgr.meterTokens(r) }
```

- [ ] **Step 4: Run it, expect PASS.**

```
mise exec -- go test ./internal/session/ -run TestMeterTokens_EmitsIdentityLabels
```

  Expected: `ok  	github.com/szymonrychu/tatara-claude-code-wrapper/internal/session`.

- [ ] **Step 5: Wire the config into session.Config in app.go.** In
  `cmd/wrapper/app.go`, extend the `session.New(session.Config{...})` literal
  (lines 143-154) to pass the three fields (add after `Repo: repo,`):

```go
		Kind:            cfg.Kind,
		RepoName:        cfg.RepoName,
		Project:         cfg.Project,
```

- [ ] **Step 6: Build + full test the wrapper (whole module now compiles).**

```
mise exec -- go build ./...
mise exec -- go test ./...
mise exec -- golangci-lint run
```

  Expected: build succeeds, all packages `ok`, linter clean.

- [ ] **Step 7: Commit.**

```
git add internal/session/session.go cmd/wrapper/app.go internal/session/session_test.go
git commit -m "feat: stamp kind/repo/project on wrapper per-turn token+cost metrics"
```

---

### Task 4: Operator pod env exports TATARA_KIND + TATARA_REPO

**Files:**
- Modify: `tatara-operator/internal/agent/pod.go:432-434` (add two env vars next
  to `TATARA_TASK`/`TATARA_PROJECT`).
- Test: `tatara-operator/internal/agent/pod_test.go` (append a test).

**Interfaces:**
- Produces: pod env `TATARA_KIND = task.Spec.Kind`, `TATARA_REPO =
  task.Spec.RepositoryRef` (both may be `""` for project-scoped kinds). These
  match the label values the operator uses for `operator_task_tokens_total`
  (`taskTokenLabels` at `internal/controller/turncallback.go:435`), so wrapper
  and operator token series align on `kind`/`repo`/`project`.
- Consumed by: wrapper `cmd/wrapper/config.go` (Task 2).

- [ ] **Step 1: Write the failing test.** Append to
  `internal/agent/pod_test.go`:

```go
func TestBuildPod_MetricIdentityEnv(t *testing.T) {
	proj, repo, task, cfg := sampleInputs()
	task.Spec.Kind = "review"
	task.Spec.RepositoryRef = "repo1"

	c := agent.BuildPod(proj, repo, task, nil, testMemoryEndpoint, cfg).Spec.Containers[0]

	kind, ok := envValue(c, "TATARA_KIND")
	require.True(t, ok, "TATARA_KIND must be set")
	require.Equal(t, "review", kind)

	r, ok := envValue(c, "TATARA_REPO")
	require.True(t, ok, "TATARA_REPO must be set")
	require.Equal(t, "repo1", r)
}

func TestBuildPod_MetricIdentityEnv_ProjectScopedEmptyRepo(t *testing.T) {
	proj, _, task, cfg := sampleInputs()
	task.Spec.Kind = "brainstorm"
	task.Spec.RepositoryRef = ""

	// Project-scoped kind: repo == nil, RepositoryRef empty.
	c := agent.BuildPod(proj, nil, task, nil, testMemoryEndpoint, cfg).Spec.Containers[0]

	kind, ok := envValue(c, "TATARA_KIND")
	require.True(t, ok)
	require.Equal(t, "brainstorm", kind)

	r, ok := envValue(c, "TATARA_REPO")
	require.True(t, ok, "TATARA_REPO must be present even when empty")
	require.Equal(t, "", r)
}
```

- [ ] **Step 2: Run it, expect FAIL.**

```
mise exec -- go test ./internal/agent/ -run TestBuildPod_MetricIdentityEnv
```

  Expected: `TATARA_KIND must be set` (envValue returns `ok=false`).

- [ ] **Step 3: Add the env vars.** In `internal/agent/pod.go`, inside the
  `env = append(env, []corev1.EnvVar{...}` block, immediately after the
  `{Name: "TATARA_PROJECT", Value: project.Name},` line (line 434), add:

```go
			// Metric identity (component 6): the wrapper stamps these onto its
			// per-turn token/cost series so fleet spend attributes to a Task kind
			// and repo. Same values the operator uses for operator_task_tokens_total
			// (taskTokenLabels), so the two token families align. Empty repo for
			// project-scoped kinds (brainstorm/refine/incident/healthCheck).
			{Name: "TATARA_KIND", Value: task.Spec.Kind},
			{Name: "TATARA_REPO", Value: task.Spec.RepositoryRef},
```

- [ ] **Step 4: Run it, expect PASS.**

```
mise exec -- go test ./internal/agent/ -run TestBuildPod_MetricIdentityEnv
```

  Expected: `ok  	github.com/szymonrychu/tatara-operator/internal/agent`.

- [ ] **Step 5: Full package test + build + lint.**

```
mise exec -- go build ./...
mise exec -- go test ./internal/agent/ ./internal/controller/
mise exec -- golangci-lint run
```

  Expected: build succeeds, both packages `ok`, linter clean.

- [ ] **Step 6: Commit.**

```
git add internal/agent/pod.go internal/agent/pod_test.go
git commit -m "feat: export TATARA_KIND/TATARA_REPO on agent pod env for wrapper metrics"
```

---

### Task 5: Confirm operator_task_tokens_total emits live series

**Files:**
- Modify: `tatara-operator/internal/controller/turncallback_test.go` (append a
  call-site test asserting the metric is bumped on a recorded turn-complete).
- No production code change: the emit already exists at
  `internal/controller/turncallback.go:288-291` (`if recorded && s.Metrics !=
  nil { s.Metrics.AddTaskTokens(...) }`). This task is the "confirm it emits"
  branch of the spec; the alternative "wire it" branch is not needed.

**Interfaces:**
- Consumes: `taskTokenLabels(task)` and `OperatorMetrics.AddTaskTokens(project,
  repo, kind, issue, input, output)`.
- Produces: a regression test proving the call site fires, plus a live-Prometheus
  acceptance check (Step 4).

- [ ] **Step 1: Read the call site to confirm the guard.** Confirm
  `internal/controller/turncallback.go` lines 285-292 read (no change, just
  verify against the current tree):

```go
	if recorded && s.Metrics != nil {
		project, repo, kind, issue := taskTokenLabels(task)
		s.Metrics.AddTaskTokens(project, repo, kind, issue, inputTotal, u.OutputTokens)
		s.Metrics.AddTaskTurn(project, repo, kind, issue)
	}
```

  If this block differs, STOP and reconcile: the emit path is the spec's
  precondition. (As of this plan it is present and correct.)

- [ ] **Step 2: Write a call-site regression test.** The test file is
  `package controller` and drives the real envtest harness over the HTTP handler
  (see `TestTurnComplete_RecordsResultAndRequeues` at
  `turncallback_test.go:27` as the template): `newCallbackServer()` builds a
  `*CallbackServer` with `Client: k8sClient`, `Namespace: testNS`, and a fresh
  `obs.NewOperatorMetrics(prometheus.NewRegistry())`; `mkTaskProject`/
  `mkTaskRepository`/`mkTask`/`mkSubtask`/`annotate` seed the CRs; a POST to
  `/internal/turn-complete` carrying a `usage` object drives `recordUsage` ->
  `AddTaskTokens`. Because `newCallbackServer()` hides its registry, build the
  server inline so this test owns the registry it asserts on. Append:

```go
func TestTurnComplete_EmitsTaskTokens(t *testing.T) {
	mkTaskProject(t, "p-tok", 3)
	mkTaskRepository(t, "r-tok", "p-tok")
	mkTask(t, "t-tok", "p-tok", "r-tok")
	mkSubtask(t, "t-tok-s1", "t-tok", 1)
	// Set a Kind + issue source so the emitted series carries real labels.
	tk := getTask(t, "t-tok")
	tk.Spec.Kind = "implement"
	tk.Spec.Source = &tatarav1alpha1.TaskSource{IssueRef: "szymonrychu/tatara-operator#7"}
	if err := k8sClient.Update(context.Background(), tk); err != nil {
		t.Fatalf("set kind/source: %v", err)
	}
	annotate(t, "t-tok", map[string]string{
		annCurrentTurn:    "turn-1",
		annCurrentSubtask: "t-tok-s1",
	})

	reg := prometheus.NewRegistry()
	cb := &CallbackServer{Client: k8sClient, Metrics: obs.NewOperatorMetrics(reg), Namespace: testNS}
	body, _ := json.Marshal(map[string]any{
		"turnId": "turn-1", "state": "completed",
		"finalText": "done", "stopReason": "end_turn",
		"usage": map[string]any{"input_tokens": 1200, "output_tokens": 300},
	})
	req := httptest.NewRequest(http.MethodPost, "/internal/turn-complete", bytes.NewReader(body))
	w := httptest.NewRecorder()
	cb.Handler().ServeHTTP(w, req)
	if w.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want 204; body=%s", w.Code, w.Body.String())
	}

	mfs, err := reg.Gather()
	if err != nil {
		t.Fatalf("gather: %v", err)
	}
	var input float64
	found := false
	for _, mf := range mfs {
		if mf.GetName() != "operator_task_tokens_total" {
			continue
		}
		for _, mc := range mf.GetMetric() {
			lbl := map[string]string{}
			for _, lp := range mc.GetLabel() {
				lbl[lp.GetName()] = lp.GetValue()
			}
			if lbl["kind"] == "implement" && lbl["type"] == "input" && lbl["repo"] == "r-tok" {
				input = mc.GetCounter().GetValue()
				found = true
			}
		}
	}
	if !found {
		t.Fatal("operator_task_tokens_total{kind=implement,type=input,repo=r-tok} not emitted")
	}
	if input != 1200 {
		t.Errorf("input tokens = %v, want 1200", input)
	}
}
```

  Notes for the executor: confirm the exact `mkTaskProject`/`mkTaskRepository`/
  `mkTask`/`mkSubtask`/`getTask`/`annotate` signatures against the current test
  file (they are used verbatim by the sibling tests, e.g. lines 28-35, 49, 53).
  The `usage` payload keys (`input_tokens`/`output_tokens`) match `turnUsage`
  (`turncallback.go:130-135`). `repo` label = `RepositoryRef` = `r-tok`.

- [ ] **Step 3: No production code change.** The emit already exists at
  `turncallback.go:288-291`. Do not add a metric accessor - the test asserts via
  `reg.Gather()` over the registry it owns. This keeps the confirmation
  zero-footprint on production code (spec's "confirm it emits" branch).

- [ ] **Step 4: Run it, expect PASS.**

```
mise exec -- go test ./internal/controller/ -run TestTurnComplete_EmitsTaskTokens
```

  Expected: `ok  	github.com/szymonrychu/tatara-operator/internal/controller`.
  This proves the HTTP turn-complete path bumps `operator_task_tokens_total` with
  the aligned `project/repo/kind/issue/type` labels. (Requires the envtest
  control plane the package's `TestMain`/`k8sClient` already sets up; run with
  the package's normal test invocation.)

- [ ] **Step 5: Live acceptance query (verification-before-completion).** After
  the operator that carries Task 4 is deployed (Task 8), confirm the family has
  non-empty live series in Prometheus. Use the grafana MCP
  `query_prometheus` tool (instant query) or the equivalent HTTP API:

```
operator_task_tokens_total{project="tatara"}
```

  Expected: at least one series with a non-zero value once any agent Task has
  completed a turn post-deploy. Record the result in the operator MEMORY.md
  (confirm the dashboard note "populate after A1/A2 deploy" is resolved). If the
  query is empty after a full scan cycle with known turn-completes, escalate:
  the emit guard (`recorded`) or the push/scrape wiring is the fault, not this
  metric's registration.

- [ ] **Step 6: Commit.**

```
git add internal/controller/turncallback_test.go
git commit -m "test: assert turn-complete emits operator_task_tokens_total"
```

---

### Task 6: Observability dashboard - $-spend and cache-hit-ratio panels

**Files:**
- Modify: `tatara-observability/dashboards/task-delivery.json` (add two panels to
  the `panels` array; bump `version`).
- Test: a JSON+PromQL validation script run locally (jq assertions + terraform
  validate). No new file is strictly required; the checks below are run inline.

**Interfaces:**
- Consumes (PromQL, from the wrapper push families now carrying labels):
  `ccw_turn_cost_usd_total{kind,repo,project}` and
  `ccw_turn_tokens_total{type,kind,project}`.
- Produces: two new Grafana panels in the `Tatara - Task Delivery` dashboard.

Panel layout note: existing panels occupy `y=0..26` (`gridPos` rows 0,10,18).
The two new panels go on a new row at `y=26`. Use `id` 6 and 7 (existing max id
is 5).

- [ ] **Step 1: Write the failing validation checks.** Create
  `tatara-observability/scripts/check_token_panels.sh` (a deterministic guard
  the deploy step and CI-adjacent review can run):

```sh
#!/usr/bin/env sh
# Verifies the token-spend and cache-hit-ratio panels exist and query the
# wrapper cost/token families. Fails non-zero if a panel is missing.
set -eu
DB="dashboards/task-delivery.json"

python3 -c "import json,sys; json.load(open('$DB'))"  # valid JSON

# $-spend panel: title present and queries ccw_turn_cost_usd_total by kind,repo.
jq -e '.panels[] | select(.title=="Cost (USD) by Kind and Repo")
       | .targets[].expr | select(test("ccw_turn_cost_usd_total"))
       | select(test("by ?\\(kind, ?repo\\)"))' "$DB" >/dev/null

# cache-hit-ratio panel: title present and computes cache_read/(cache_read+input).
jq -e '.panels[] | select(.title=="Cache Hit Ratio by Kind")
       | .targets[].expr
       | select(test("type=\"cache_read\""))
       | select(test("cache_read\\|input"))' "$DB" >/dev/null

echo "token panels OK"
```

  Make it executable: `chmod +x scripts/check_token_panels.sh`.

- [ ] **Step 2: Run it, expect FAIL.**

```
sh scripts/check_token_panels.sh
```

  Expected: non-zero exit at the first `jq -e` (the panels do not exist yet):
  `jq: error ... ` / empty output, exit code 1/4.

- [ ] **Step 3: Add the two panels.** In
  `dashboards/task-delivery.json`, insert these two objects into the `panels`
  array, after the last existing panel (the `Turn Rate by Kind` panel, id 5,
  which closes at line ~518). Add a comma after that panel's closing `}` and
  paste:

```json
    {
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "description": "Cumulative Claude spend in USD per Task kind and repo, from the wrapper's ccw_turn_cost_usd_total (result.json total_cost_usd). Populates once wrappers carrying the kind/repo/project labels are deployed. Use to attribute $ spend and A/B the per-kind model tiering.",
      "fieldConfig": {
        "defaults": {
          "color": {"mode": "thresholds"},
          "custom": {
            "align": "auto",
            "cellOptions": {"type": "auto"},
            "filterable": true,
            "inspect": false
          },
          "mappings": [],
          "unit": "currencyUSD",
          "thresholds": {
            "mode": "absolute",
            "steps": [{"color": "green", "value": null}]
          }
        },
        "overrides": [
          {
            "matcher": {"id": "byName", "options": "USD"},
            "properties": [
              {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "gradient"}},
              {"id": "custom.width", "value": 120},
              {
                "id": "thresholds",
                "value": {
                  "mode": "absolute",
                  "steps": [
                    {"color": "green", "value": null},
                    {"color": "yellow", "value": 1},
                    {"color": "red", "value": 5}
                  ]
                }
              }
            ]
          }
        ]
      },
      "gridPos": {"h": 8, "w": 12, "x": 0, "y": 26},
      "id": 6,
      "options": {
        "cellHeight": "sm",
        "footer": {"countRows": false, "fields": ["USD"], "reducer": ["sum"], "show": true},
        "showHeader": true,
        "sortBy": [{"desc": true, "displayName": "USD"}]
      },
      "pluginVersion": "11.0.0",
      "targets": [
        {
          "datasource": {"type": "prometheus", "uid": "prometheus"},
          "editorMode": "code",
          "expr": "sum by (kind, repo) (ccw_turn_cost_usd_total{project=~\"$project\"})",
          "format": "table",
          "instant": true,
          "legendFormat": "__auto",
          "refId": "A"
        }
      ],
      "title": "Cost (USD) by Kind and Repo",
      "transformations": [
        {
          "id": "organize",
          "options": {
            "excludeByName": {"Time": true, "__name__": true, "job": true, "pod": true, "run_id": true, "project": true},
            "includeByName": {},
            "indexByName": {"kind": 0, "repo": 1},
            "renameByName": {"Value": "USD"}
          }
        }
      ],
      "type": "table"
    },
    {
      "datasource": {"type": "prometheus", "uid": "prometheus"},
      "description": "Prompt-cache hit ratio per Task kind: cache_read / (cache_read + input) over the wrapper ccw_turn_tokens_total families. A ratio near 0 means the stable prefix is being re-billed at full input price every pod (cache-hostile); component 4 (cross-pod cache reclaim) is gated on this rising above 0 for a fresh pod's turn-0.",
      "fieldConfig": {
        "defaults": {
          "color": {"mode": "palette-classic"},
          "custom": {
            "axisBorderShow": false,
            "axisCenteredZero": false,
            "axisColorMode": "text",
            "axisLabel": "",
            "axisPlacement": "auto",
            "barAlignment": 0,
            "drawStyle": "line",
            "fillOpacity": 10,
            "gradientMode": "none",
            "hideFrom": {"legend": false, "tooltip": false, "viz": false},
            "insertNulls": false,
            "lineInterpolation": "linear",
            "lineWidth": 2,
            "pointSize": 5,
            "scaleDistribution": {"type": "linear"},
            "showPoints": "auto",
            "spanNulls": false,
            "stacking": {"group": "A", "mode": "none"},
            "thresholdsStyle": {"mode": "off"}
          },
          "mappings": [],
          "min": 0,
          "max": 1,
          "unit": "percentunit",
          "thresholds": {
            "mode": "absolute",
            "steps": [{"color": "green", "value": null}]
          }
        },
        "overrides": []
      },
      "gridPos": {"h": 8, "w": 12, "x": 12, "y": 26},
      "id": 7,
      "options": {
        "legend": {"calcs": ["mean", "lastNotNull"], "displayMode": "list", "placement": "bottom", "showLegend": true},
        "tooltip": {"mode": "multi", "sort": "desc"}
      },
      "pluginVersion": "11.0.0",
      "targets": [
        {
          "datasource": {"type": "prometheus", "uid": "prometheus"},
          "editorMode": "code",
          "expr": "sum by (kind) (rate(ccw_turn_tokens_total{type=\"cache_read\", project=~\"$project\"}[$__rate_interval])) / clamp_min(sum by (kind) (rate(ccw_turn_tokens_total{type=~\"cache_read|input\", project=~\"$project\"}[$__rate_interval])), 0.0001)",
          "legendFormat": "{{kind}}",
          "refId": "A"
        }
      ],
      "title": "Cache Hit Ratio by Kind",
      "type": "timeseries"
    }
```

  Then bump the dashboard `version` field (bottom of the file, currently
  `"version": 1`) to `2`.

- [ ] **Step 4: Run the checks, expect PASS.**

```
sh scripts/check_token_panels.sh
```

  Expected: `token panels OK`.

- [ ] **Step 5: Terraform validate (no backend/creds needed).**

```
mise exec -- terraform init -backend=false
mise exec -- terraform fmt -check
mise exec -- terraform validate
```

  Expected: `Success! The configuration is valid.` The dashboard is loaded via
  `file(...)` so an invalid JSON would surface at `plan`, but `validate` plus the
  Step 1 `json.load` catch a malformed edit. (`terraform fmt -check` covers only
  `.tf`; the JSON edit does not affect it.)

- [ ] **Step 6: Commit.**

```
git add dashboards/task-delivery.json scripts/check_token_panels.sh
git commit -m "feat: add \$-spend-by-kind/repo and cache-hit-ratio panels to task-delivery dashboard"
```

---

### Task 7: Deploy the wrapper (tatara-claude-code-wrapper -> main -> tatara-helmfile)

**Files:**
- `tatara-claude-code-wrapper` (merge the Task 1-3 branch to `main`; CI builds +
  pushes the image and chart).
- `tatara-helmfile` (MR bumping the wrapper release chart version AND pinned
  `image.tag`).

**Interfaces:**
- Produces: a wrapper image whose agent pods emit `ccw_turn_tokens_total` and
  `ccw_turn_cost_usd_total` with `kind/repo/project` labels.

- [ ] **Step 1: Request code review, address critical/high, run pre-commit.**
  Per the working agreement: `superpowers:requesting-code-review` on the Task 1-3
  diff; fix critical/high findings; then `pre-commit run --all-files`.

- [ ] **Step 2: Finish the branch -> merge to wrapper `main`.** Use
  `superpowers:finishing-a-development-branch`. On merge, the wrapper CI builds +
  pushes the image to harbor and packages the chart. Note the merge short SHA.

- [ ] **Step 3: Bump the wrapper release in tatara-helmfile.** Open a
  tatara-helmfile MR that bumps BOTH the wrapper chart version AND the pinned
  wrapper `image.tag` (per
  [[tatara-operator-deploy-chart-version-and-image-tag]] and the deploy hard
  rule - chart-only leaves the old image running). The wrapper image is consumed
  as the agent pod image via `project.Spec.Agent.Image`; confirm which release /
  values key carries it in tatara-helmfile and bump the tag there. Review via the
  MR diff; the pipeline applies on merge.

- [ ] **Step 4: Verify deploy.** After apply, confirm a freshly spawned agent pod
  runs the new image tag (`kubectl get pod <agent-pod> -o
  jsonpath='{.spec.containers[0].image}'` - READ only, not a deploy path). Do NOT
  `kubectl set image`/`patch` to ship.

---

### Task 8: Deploy the operator (tatara-operator -> main -> tatara-helmfile)

**Files:**
- `tatara-operator` (merge the Task 4-5 branch to `main`; CI builds + pushes the
  operator image + `tatara-operator`/`tatara-project` charts).
- `tatara-helmfile` (MR bumping the operator release chart version AND pinned
  `image.tag`).

**Interfaces:**
- Produces: an operator that sets `TATARA_KIND`/`TATARA_REPO` on every agent pod
  env (the label source the wrapper reads).

- [ ] **Step 1: Code review + pre-commit** on the Task 4-5 diff
  (`superpowers:requesting-code-review`), fix critical/high, `pre-commit run
  --all-files`.

- [ ] **Step 2: Merge to operator `main`.** CI builds + pushes the operator image
  and charts. Note the merge short SHA.

- [ ] **Step 3: Bump the operator release in tatara-helmfile.** MR bumping BOTH
  the operator chart pin AND the pinned `image.tag` in the operator release
  values (per [[tatara-helmfile-dual-chart-pin-and-cr-adoption-2026-06-22]] the
  operator deploy bumps TWO chart pins - `tatara-operator` and `tatara-project`;
  match that pattern). Review via diff; pipeline applies.

- [ ] **Step 4: Verify deploy + env.** Confirm the running operator is the new
  tag, then confirm a freshly spawned agent pod carries `TATARA_KIND` and
  `TATARA_REPO` (`kubectl get pod <agent-pod> -o jsonpath` over
  `.spec.containers[0].env` - READ only). Then run the Task 5 Step 5 live
  Prometheus query to confirm `operator_task_tokens_total` non-empty.

---

### Task 9: Apply the dashboard + acceptance gate verification

**Files:**
- `tatara-observability` (merge the Task 6 branch to `main`; the `apply` GitHub
  Actions workflow runs `terraform apply` and provisions the updated dashboard).

**Interfaces:**
- Produces: the live `Tatara - Task Delivery` dashboard with the two new panels.
- Verifies: the component-4 gate metric.

- [ ] **Step 1: Open the PR; confirm the sticky terraform plan is clean.** The
  `apply` workflow posts a `terraform plan` on the PR
  (`.github/workflows/apply.yml`, triggers on `dashboards/**` + `**.tf`). The plan
  must show an in-place update of `grafana_dashboard.task_delivery` only, no
  destroy.

- [ ] **Step 2: Merge to `main`.** The workflow runs `terraform apply`; the
  dashboard updates in the Grafana `Tatara` folder.

- [ ] **Step 3: Verify the panels render with live series.** Open the
  `Tatara - Task Delivery` dashboard (uid `tatara-task-delivery`). Confirm:
  - `Cost (USD) by Kind and Repo` shows non-empty rows once agent pods running
    the new wrapper (Task 7) have completed turns.
  - `Cache Hit Ratio by Kind` shows a per-kind line.

- [ ] **Step 4: ACCEPTANCE GATE (component 4 precondition).** Run the gate query
  in Prometheus (grafana MCP `query_prometheus`, instant):

```
ccw_turn_tokens_total{type="cache_read"}
```

  Expected: at least one non-empty series, carrying `kind`, `repo`, `project`,
  `model` labels, for a fresh pod's turn-0. A visible `cache_read` series (even
  value 0) proves the metric is being emitted per-pod with the identity labels;
  a NON-ZERO value on a fresh pod that follows another same-(repo,model) pod
  within cache TTL is the stronger signal component 4 needs. Record the observed
  turn-0 `cache_read` vs `input` ratio in the spec's component-4 section (this is
  the empirical input that decides whether 4b/4c pay off).

- [ ] **Step 5: Record in MEMORY.** Add a dated one-line entry to each touched
  repo's MEMORY.md (wrapper, operator, observability) noting: token+cost metrics
  now carry kind/repo/project; dashboard has $-spend + cache-hit-ratio panels;
  gate metric `ccw_turn_tokens_total{type=cache_read}` confirmed live at
  turn-0 with ratio <observed>. Update the spec's phase note that P0 component 6
  is shipped.
</content>
</invoke>
