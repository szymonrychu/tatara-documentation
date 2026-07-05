# Implementation Plan: Durable token/cost measurement (G2 + G12)

Spec: `docs/superpowers/specs/2026-07-04-durable-measurement-design.md`
Date: 2026-07-04

## Goal

Make the operator-side `operator_task_tokens_total` the durable, model- and
cache-aware source of truth for agent token/$ accounting, and add a bounded
per-outcome churn metric, so dashboards ($-cost, cache-hit-ratio, churn) and the
runaway alert read a metric that accumulates correctly for every kind (including
single-turn triageIssue/review) instead of the dead/ephemeral `ccw_*` push.

Two independent deliverables:

1. OPERATOR (Tasks 1-6, sequential, ONE branch): stamp the resolved model on the
   Task, accumulate all four token classes on Task status, add a `model` label +
   four `type` values to `operator_task_tokens_total`, unfold `recordUsage` to
   emit all four classes + model, add `operator_task_terminal_tokens_total{outcome}`
   with a `terminalOutcome` classifier emitted at the lifecycle-terminal
   chokepoint, then regenerate the CRD + deepcopy.
2. OBSERVABILITY (Tasks 7-8, independent, ONE branch in tatara-observability):
   replace the crude $ panel with a model+cache-accurate expr, make the
   cache-hit-ratio panel functional, add a churn panel, harden the guard script,
   bump the dashboard version, and re-baseline the runaway alert to the
   model-aware $ rate.

## Architecture

- **Model stamping**: `BuildPod` already sets the pod `MODEL` env from
  `modelForKind(project, kind)`. The controller stamps that same resolved model
  on `Task.Status.ResolvedModel` at pod creation, so `recordUsage` and the
  terminal emit read the model that actually ran (accurate even if `ModelByKind`
  changes after spawn) without re-resolving from the Project at callback time.
- **Metric enrichment**: the turn-complete callback `turnUsage` struct ALREADY
  carries all four token classes (`input_tokens`, `output_tokens`,
  `cache_read_input_tokens`, `cache_creation_input_tokens`). `recordUsage`
  currently folds `cache_read` into `input` and ignores `cache_creation`. The fix
  emits each class as its own `type` series under the new `model` label. NO
  wrapper change is needed.
- **Cumulative status**: `recordUsage` accumulates all four classes onto new
  `Task.Status.Cumulative{Input,Output,CacheRead,CacheCreation}` fields (existing
  `CumulativeTokens += output` semantics are LEFT UNCHANGED for back-compat and
  to keep existing tests green). At the lifecycle-terminal transition
  (`setLifecycleState`, the terminal chokepoint), those cumulatives are added to
  `operator_task_terminal_tokens_total{outcome}` under the task's classified
  outcome (`delivered` / `churned` / `abandoned`). Each of N recreated churn
  tasks contributes its own cumulative at its own terminal, so `{outcome=churned}`
  sums the whole churn with no per-issue-lineage tracking.
- **Dollars in PromQL (RESOLVED, no ruler)**: tatara-observability provisions
  Grafana only; there is no Prometheus/Mimir ruler in scope. The $ arithmetic
  stays in the dashboard/alert PromQL (as the G1-minimal fix did), now keyed on
  the real `model` label with separate `cache_read`/`cache_creation` `type`
  series. Price table (per token): opus-4-8 in 5e-6 / out 25e-6; sonnet-5 in 3e-6
  / out 15e-6; haiku-4-5 in 1e-6 / out 5e-6; `cache_read` = 0.1x the model input
  price, `cache_creation` = 1.25x the model input price. Model label values are
  the literal MODEL env strings `claude-opus-4-8` / `claude-sonnet-5` /
  `claude-haiku-4-5`.

## Tech Stack

- Go (operator): controller-runtime, kubebuilder CRD, prometheus/client_golang,
  envtest. Worktree: `/Users/szymonri/Documents/tc-worktrees/dm-operator`.
- Terraform + Grafana JSON + alert YAML (observability). Repo:
  `/Users/szymonri/Documents/tatara/tatara-observability` (work on a
  `durable-measurement` branch off `main`).

## Global Constraints

- Newest stable Go pinned in `go.mod`; `gofmt` + `golangci-lint` + `go vet`
  clean; wrap errors with `%w`; table-driven tests with `t.Run`; JSON slog logs;
  Prometheus `/metrics`.
- CRD fields are typed API fields. New `Status` fields are scalars
  (`string` / `int64`), all `+optional`.
- Model IDs: `claude-opus-4-8`, `claude-sonnet-5`, `claude-haiku-4-5`.
- Deploy is out of scope for this plan (tatara-helmfile GitOps handles it).
- Operator build/vet/test env prefix (used verbatim below):
  ```
  export GOFLAGS=-buildvcs=false
  export KUBEBUILDER_ASSETS="$(go run sigs.k8s.io/controller-runtime/tools/setup-envtest@release-0.21 use 1.33.0 -p path)"
  ```
- envtest lesson: a test's `turnId` must be unique across the whole controller
  package (e.g. `turn-<distinct>`), else `resolveTaskByTurn` resolves a sibling
  test's task. Same for `mkTask`/project/repo names.
- CRD regen: `make manifests` (CRD) + `make generate` (deepcopy). New scalar
  status fields need NO deepcopy code change (value-copied by `*out = *in`);
  `make generate` is expected to produce an empty diff on
  `zz_generated.deepcopy.go`.

---

## OPERATOR (Tasks 1-6, sequential, one branch)

Branch: work in the existing worktree `/Users/szymonri/Documents/tc-worktrees/dm-operator`
on a `durable-measurement` branch. All six operator tasks share this branch and
must be committed in order (each depends on the previous compiling).

### Task 1: `Task.Status.ResolvedModel` + stamp at pod-creation

Stamp the resolved model on the Task at pod creation so the callback path reads
the model that actually ran.

**Files:**
- `api/v1alpha1/task_types.go` (add `ResolvedModel` to `TaskStatus`, ~:319 block)
- `internal/agent/pod.go` (export `ModelForKind`, ~:897)
- `internal/agent/pod_model_effort_test.go` (add export-wrapper test)
- `internal/controller/task_controller.go` (`ensurePodAndService`, ~:414-443; add
  `stampResolvedModel`)
- `internal/controller/task_controller_test.go` or a new
  `internal/controller/resolvedmodel_test.go` (envtest)

**Interfaces:**
```go
// api/v1alpha1/task_types.go, in TaskStatus:
// ResolvedModel is the MODEL env resolved for this Task's agent pod at spawn
// (modelForKind: per-kind override else project-wide). Stamped once at
// pod-creation; read by the token/terminal metrics so $ is priced by the model
// that actually ran. +optional
ResolvedModel string `json:"resolvedModel,omitempty"`

// internal/agent/pod.go (exported thin wrapper over modelForKind):
func ModelForKind(project *tatarav1alpha1.Project, kind string) string

// internal/controller/task_controller.go:
func (r *TaskReconciler) stampResolvedModel(ctx context.Context, task *tatarav1alpha1.Task, model string) error
```

**Steps:**
- [ ] Add the `ResolvedModel` field to `TaskStatus` in `api/v1alpha1/task_types.go`.
- [ ] Add exported `func ModelForKind(project *tatarav1alpha1.Project, kind string) string { return modelForKind(project, kind) }` in `internal/agent/pod.go` (keep unexported `modelForKind` so the existing `pod_model_effort_test.go` and `BuildPod` callsite at pod.go:417 are untouched).
- [ ] Write failing test in `pod_model_effort_test.go`: `TestModelForKind_Exported` asserting `ModelForKind(proj, "review") == modelForKind(proj, "review")` for a project with a `ModelByKind` override + a nil map.
- [ ] Run `go test ./internal/agent/... -run TestModelForKind_Exported` -> expect FAIL (undefined `ModelForKind`) then, after adding the wrapper, PASS.
- [ ] Add `stampResolvedModel`: `RetryOnConflict` Get fresh Task, if `fresh.Status.ResolvedModel != model { fresh.Status.ResolvedModel = model; return r.Status().Update(ctx, fresh) }` else return nil. Wrap errors `fmt.Errorf("stampResolvedModel: %w", err)`.
- [ ] In `ensurePodAndService`, after `pod := agent.BuildPod(...)` compute `model := agent.ModelForKind(project, task.Spec.Kind)`, and in the `apierrors.IsNotFound(err)` create branch after a successful `r.Create(ctx, pod)` call `_ = r.stampResolvedModel(ctx, task, model)` (best-effort; a stamp failure must not fail pod creation - the metric label degrades to "" which is fail-open, matching the `issue==""` convention).
- [ ] Write failing envtest `TestEnsurePodAndService_StampsResolvedModel`: create a Project with `Agent.Model="claude-opus-4-8"`, a Repository, a Task (kind implement), call `ensurePodAndService`, then Get the Task and assert `Status.ResolvedModel == "claude-opus-4-8"`. Use distinct project/repo/task names.
- [ ] Run `go test ./internal/controller/... -run TestEnsurePodAndService_StampsResolvedModel` -> expect FAIL (empty ResolvedModel).
- [ ] Implement the stamp call, re-run -> expect PASS.
- [ ] `gofmt -w`, `go vet ./...`, commit: `feat: stamp resolved model on Task.Status at pod-creation`.

### Task 2: `Task.Status` cumulative all-four-classes

Accumulate every token class on the Task so the terminal emit can attribute the
task's whole spend. Existing `CumulativeTokens` semantics are LEFT UNCHANGED.

**Files:**
- `api/v1alpha1/task_types.go` (four new `Cumulative*` fields near :319)
- `internal/controller/turncallback.go` (`recordUsage`, :247-294)
- `internal/controller/turncallback_usage_test.go` (extend `TestTurnComplete_WithUsage`)

**Interfaces:**
```go
// api/v1alpha1/task_types.go, in TaskStatus (all +optional):
CumulativeInput         int64 `json:"cumulativeInput,omitempty"`
CumulativeOutput        int64 `json:"cumulativeOutput,omitempty"`
CumulativeCacheRead     int64 `json:"cumulativeCacheRead,omitempty"`
CumulativeCacheCreation int64 `json:"cumulativeCacheCreation,omitempty"`
```

**Steps:**
- [ ] Add the four `Cumulative*` fields to `TaskStatus`.
- [ ] Extend `TestTurnComplete_WithUsage` (turncallback_usage_test.go:21) with assertions on the new fields for the existing payload (`input_tokens:1000, output_tokens:200, cache_read_input_tokens:500, cache_creation_input_tokens:0`): assert `CumulativeInput==1000`, `CumulativeOutput==200`, `CumulativeCacheRead==500`, `CumulativeCacheCreation==0`. Leave the existing `LastTurnInputTokens==1500` and `CumulativeTokens==200` assertions intact (back-compat).
- [ ] Run `go test ./internal/controller/... -run TestTurnComplete_WithUsage$` -> expect FAIL (new fields zero).
- [ ] In `recordUsage`, inside the `RetryOnConflict` closure after the existing `fresh.Status.LastTurnInputTokens = inputTotal` / `fresh.Status.CumulativeTokens += u.OutputTokens` lines (turncallback.go:275-276), add:
  ```go
  fresh.Status.CumulativeInput += u.InputTokens
  fresh.Status.CumulativeOutput += u.OutputTokens
  fresh.Status.CumulativeCacheRead += u.CacheReadInputTokens
  fresh.Status.CumulativeCacheCreation += u.CacheCreationInputTokens
  ```
  Do NOT change `inputTotal` (still `u.InputTokens + u.CacheReadInputTokens`), `LastTurnInputTokens`, `CumulativeTokens`, or the returned `delta` - those keep the budget/back-compat contract.
- [ ] Run the test -> expect PASS. Also run `TestTurnComplete_WithUsage_Accumulates` and `TestTurnComplete_WithoutUsage_LeavesTokensUnchanged` -> expect PASS (unchanged).
- [ ] `gofmt -w`, `go vet ./...`, commit: `feat: accumulate all four token classes on Task.Status`.

### Task 3: `operator_task_tokens_total` gains `model` label + four `type` values

New `AddTaskTokens` / `DeleteTaskSeries` signatures and `taskTokenLabels` model
return. Pure unit-tested at the metric layer.

**Files:**
- `internal/obs/operator_metrics.go` (def ~:261-264; `AddTaskTokens` ~:820;
  `DeleteTaskSeries` ~:858)
- `internal/obs/operator_metrics_test.go` (`TestAddTaskTokens` :868;
  `TestDeleteTaskSeries_RemovesTokenAndTurn` :1028)
- `internal/controller/turncallback.go` (`taskTokenLabels` :435)
- `internal/controller/project_controller.go` (callsite :446)
- `internal/controller/reaper.go` (callsite :374-376)

**Interfaces:**
```go
// internal/obs/operator_metrics.go:
func (m *OperatorMetrics) AddTaskTokens(project, repo, kind, issue, model string, input, output, cacheRead, cacheCreation int64)
func (m *OperatorMetrics) DeleteTaskSeries(project, repo, kind, issue, model string)

// internal/controller/turncallback.go:
func taskTokenLabels(task *tatarav1alpha1.Task) (project, repo, kind, issue, model string)
```

**Steps:**
- [ ] Change the `taskTokensTotal` CounterVec label slice (operator_metrics.go:264) from `[]string{"project", "repo", "kind", "issue", "type"}` to `[]string{"project", "repo", "kind", "issue", "model", "type"}` and update the `Help` text to `"...by project, repo, Task kind, issue, model, and type (input|output|cache_read|cache_creation)."`.
- [ ] Rewrite `TestAddTaskTokens` (operator_metrics_test.go:868) for the new signature: call `m.AddTaskTokens("tatara","tatara-operator","issueLifecycle","szymonrychu/tatara-operator#68","claude-opus-4-8", 1200, 300, 400, 50)` twice + a project-scoped `m.AddTaskTokens("tatara","","brainstorm","","claude-sonnet-5", 500, 0, 0, 0)`; assert `input`/`output`/`cache_read`/`cache_creation` series carry the `model` label with the summed values (input=2400, output=600, cache_read=800, cache_creation=100 for the first tuple), and that zero classes create NO series (`CollectAndCount` matches the exact non-zero series count).
- [ ] Run `go test ./internal/obs/... -run TestAddTaskTokens` -> expect FAIL (compile: signature mismatch).
- [ ] Reimplement `AddTaskTokens` to take `model` + `cacheRead` + `cacheCreation`, emitting `WithLabelValues(project, repo, kind, issue, model, "input"|"output"|"cache_read"|"cache_creation")` each gated on `>0`. Update the doc comment.
- [ ] Reimplement `DeleteTaskSeries(project, repo, kind, issue, model string)` to `DeleteLabelValues(project, repo, kind, issue, model, "input")` for all four types plus the existing `taskTurnsTotal.DeleteLabelValues(project, repo, kind, issue)`. Update the doc comment.
- [ ] Update `TestDeleteTaskSeries_RemovesTokenAndTurn` (:1028) for the new `AddTaskTokens`/`DeleteTaskSeries` signatures (pass a `model`, add cache classes, delete with the same model) and assert all four `type` series vanish.
- [ ] Extend `taskTokenLabels` (turncallback.go:435) to also return `model = task.Status.ResolvedModel` (empty when unstamped - fail-open).
- [ ] Update callsite `project_controller.go:446` to `project, repo, kind, issue, _ := taskTokenLabels(t)` (model unused there).
- [ ] Update callsite `reaper.go:374-376` to `project, repo, kind, issue, model := taskTokenLabels(tk)` and `s.Metrics.DeleteTaskSeries(project, repo, kind, issue, model)`.
- [ ] Update the `recordUsage` metric emission (turncallback.go:289-290) signature to the 5-return `taskTokenLabels` (full change in Task 4, but make it compile here by threading `model` and passing the four classes - or leave a temporary compiling call and finish semantics in Task 4). To keep Task 3 a clean compile, update the emission to: `project, repo, kind, issue, model := taskTokenLabels(task); s.Metrics.AddTaskTokens(project, repo, kind, issue, model, inputTotal, u.OutputTokens, 0, 0)` as a placeholder-free interim (still folded), then Task 4 unfolds it. (Interim keeps existing behavior semantically: input folded, cache classes 0.)
- [ ] Run `go test ./internal/obs/... ./internal/controller/...` (obs unit + controller compile) -> expect PASS.
- [ ] `gofmt -w`, `go vet ./...`, commit: `feat: add model label + cache token classes to operator_task_tokens_total`.

### Task 4: `recordUsage` emits all four classes + model (envtest)

Unfold the metric emission so the durable metric carries uncached input,
cache_read, and cache_creation separately, under the model label.

**Files:**
- `internal/controller/turncallback.go` (`recordUsage` :288-292)
- `internal/controller/turncallback_usage_test.go` (extend `TestTurnComplete_WithUsage`)

**Interfaces:** (uses the Task 3 `AddTaskTokens` / `taskTokenLabels` signatures)

**Steps:**
- [ ] Extend `TestTurnComplete_WithUsage` to assert the emitted series. Build a `CallbackServer` with a fresh registry (mirror `TestRecordUsage_EmitsTurn` at turncallback_usage_test.go:134-164), stamp `Status.ResolvedModel="claude-opus-4-8"` on the task (via a status update helper), POST usage `input_tokens:1000, output_tokens:200, cache_read_input_tokens:500, cache_creation_input_tokens:80`, and assert via `testutil.ToFloat64(cb.Metrics.taskTokensTotal.WithLabelValues(project,repo,kind,issue,"claude-opus-4-8", <type>))`: `input==1000` (UNCACHED, not 1500), `output==200`, `cache_read==500`, `cache_creation==80`. Give this its own project/repo/task/turnId (`turn-usage-emit`, distinct).
  - Note: `taskTokensTotal` is unexported; add a small test-only accessor on `OperatorMetrics` if needed, e.g. `func (m *OperatorMetrics) TaskTokensCounter(project, repo, kind, issue, model, typ string) prometheus.Counter { return m.taskTokensTotal.WithLabelValues(project, repo, kind, issue, model, typ) }` (mirrors the existing `TaskTurnsCounter` accessor at operator_metrics.go:830), and assert through it.
- [ ] Run the test -> expect FAIL (input reads 1500, cache series absent).
- [ ] Change the emission in `recordUsage` (turncallback.go:288-291) from the interim folded call to:
  ```go
  project, repo, kind, issue, model := taskTokenLabels(task)
  s.Metrics.AddTaskTokens(project, repo, kind, issue, model,
      u.InputTokens, u.OutputTokens, u.CacheReadInputTokens, u.CacheCreationInputTokens)
  s.Metrics.AddTaskTurn(project, repo, kind, issue)
  ```
  (`inputTotal` remains only for `LastTurnInputTokens` and the returned budget delta.)
- [ ] Run the test -> expect PASS. Re-run `TestRecordUsage_EmitsTurn`, `TestRecordUsage_StaleCallback_NoTurn` -> expect PASS (turn counter path unchanged).
- [ ] `gofmt -w`, `go vet ./...`, commit: `feat: recordUsage emits uncached input + cache classes + model`.

### Task 5: `operator_task_terminal_tokens_total{outcome}` + `terminalOutcome` classifier

Bounded per-outcome churn metric emitted once at the lifecycle-terminal
transition.

**Files:**
- `internal/obs/operator_metrics.go` (new CounterVec + registration + `AddTerminalTokens`)
- `internal/obs/operator_metrics_test.go` (`TestAddTerminalTokens`)
- `internal/controller/lifecycle.go` (`setLifecycleState` :226-343; new `terminalOutcome`)
- `internal/controller/lifecycle_test.go` or new
  `internal/controller/terminaloutcome_test.go` (table test + envtest)

**Interfaces:**
```go
// internal/obs/operator_metrics.go:
func (m *OperatorMetrics) AddTerminalTokens(project, repo, outcome, model string, input, output, cacheRead, cacheCreation int64)

// internal/controller/lifecycle.go (pure classifier, table-tested):
func terminalOutcome(to, reason string, implementGiveUps int) string
```

**Steps:**
- [ ] Add the CounterVec field `taskTerminalTokensTotal` to `OperatorMetrics`, define it in `NewOperatorMetrics` next to `taskTokensTotal` (:261):
  ```go
  taskTerminalTokensTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
      Name: "operator_task_terminal_tokens_total",
      Help: "Cumulative agent token usage of terminated Tasks by project, repo, terminal outcome (delivered|churned|abandoned), model, and type (input|output|cache_read|cache_creation). No issue label - churn is outcome-keyed, not issue-keyed.",
  }, []string{"project", "repo", "outcome", "model", "type"}),
  ```
  and add it to the `reg.MustRegister(...)` list.
- [ ] Add `AddTerminalTokens` (mirror `AddTaskTokens`): `WithLabelValues(project, repo, outcome, model, <type>).Add(...)` each gated on `>0`.
- [ ] Write `TestAddTerminalTokens` (unit): call with `("tatara","tatara-operator","churned","claude-opus-4-8", 2000, 500, 800, 100)`, assert the four series + model + outcome labels, zero classes create no series.
- [ ] Run `go test ./internal/obs/... -run TestAddTerminalTokens` -> FAIL (undefined) then PASS after impl.
- [ ] Add pure classifier `terminalOutcome(to, reason string, implementGiveUps int) string` to lifecycle.go:
  ```go
  switch to {
  case "Done":
      return "delivered"
  case "Parked":
      if implementGiveUps > 0 || tatarav1alpha1.IsRecoverableGiveup(reason) {
          return "churned"
      }
      return "abandoned"
  default: // "Stopped" and any other terminal
      return "abandoned"
  }
  ```
- [ ] Write table test `TestTerminalOutcome` with `t.Run` cases: `{Done, "", 0}->delivered`; `{Parked, "implement-failed", 0}->churned` (recoverable reason); `{Parked, "maxIterations", 0}->churned`; `{Parked, "refused-no-explanation", 0}->churned`; `{Parked, "deadline", 0}->churned`; `{Parked, "refused", 2}->churned` (giveups>0 even with non-recoverable reason); `{Parked, "refused", 0}->abandoned` (deliberate decline); `{Parked, "duplicate", 0}->abandoned`; `{Stopped, "", 0}->abandoned`.
- [ ] Run `go test ./internal/controller/... -run TestTerminalOutcome` -> FAIL then PASS.
- [ ] Emit at the terminal transition in `setLifecycleState`. Inside the `RetryOnConflict` closure (after the `ImplementGiveUps++` bump at lifecycle.go:241 and before `Status().Update`), capture into closure-scoped locals the fresh cumulative + model + giveups:
  ```go
  cumIn, cumOut, cumCR, cumCC = fresh.Status.CumulativeInput, fresh.Status.CumulativeOutput, fresh.Status.CumulativeCacheRead, fresh.Status.CumulativeCacheCreation
  stampedModel = fresh.Status.ResolvedModel
  giveUps = fresh.Status.ImplementGiveUps
  fromState = from
  ```
  Then after the closure succeeds, guard on a transition INTO a terminal lifecycle state and emit once:
  ```go
  if r.Metrics != nil && !isLifecycleTerminal(fromState) && isLifecycleTerminal(to) {
      r.Metrics.AddTerminalTokens(task.Spec.ProjectRef, task.Spec.RepositoryRef,
          terminalOutcome(to, reason, giveUps), stampedModel,
          cumIn, cumOut, cumCR, cumCC)
  }
  ```
  (Reading captured closure values, NOT `task.Status`, avoids a stale in-memory snapshot; `!isLifecycleTerminal(fromState)` prevents a double-emit when `setLifecycleState` is re-called with an already-terminal `from`.)
- [ ] Write failing envtest `TestSetLifecycleState_EmitsTerminalTokens`: create Project/Repository/Task, seed `Status.ResolvedModel="claude-sonnet-5"` and the four cumulatives (e.g. 1000/300/500/50) and `LifecycleState="Implement"`, build a `TaskReconciler` with a fresh-registry `OperatorMetrics`, call `r.setLifecycleState(ctx, task, "Parked", "implement-failed")`, assert `taskTerminalTokensTotal.WithLabelValues(project, repo, "churned", "claude-sonnet-5", "input")==1000` (and output/cache_read/cache_creation). Add a second case transitioning `Implement->Done` asserting `outcome="delivered"`. Distinct names/turnIds.
- [ ] Run `go test ./internal/controller/... -run TestSetLifecycleState_EmitsTerminalTokens` -> FAIL then PASS.
- [ ] `gofmt -w`, `go vet ./...`, commit: `feat: emit operator_task_terminal_tokens_total by outcome at lifecycle terminal`.

### Task 6: CRD + deepcopy regen + full build

**Files:**
- `config/crd/bases/*task*.yaml` (regenerated)
- `api/v1alpha1/zz_generated.deepcopy.go` (regenerated; expected no-op for scalars)

**Steps:**
- [ ] Run `make manifests` -> the Task CRD gains `resolvedModel`, `cumulativeInput`, `cumulativeOutput`, `cumulativeCacheRead`, `cumulativeCacheCreation` under `status`.
- [ ] Run `make generate` -> confirm `git diff --stat api/v1alpha1/zz_generated.deepcopy.go` is EMPTY (all new fields are scalars, value-copied). If non-empty, review and commit the generated change.
- [ ] Run the full env prefix, then `go build ./... && go vet ./... && go test ./internal/obs/... ./internal/controller/... ./internal/agent/...` -> expect PASS.
- [ ] Run `golangci-lint run ./...` (or `make lint`) -> expect clean.
- [ ] `gofmt -w`, commit: `chore: regenerate CRD manifests for durable measurement fields`.

---

## OBSERVABILITY (Tasks 7-8, independent, one branch in tatara-observability)

Branch: `durable-measurement` off `main` in
`/Users/szymonri/Documents/tatara/tatara-observability`. Independent of the
operator branch (they touch different repos). These consume the metric shape the
operator tasks ship. NOTE: the crude G1-minimal `$` panel + `check_token_panels.sh`
guard live on the `g1-cost-from-operator-metric` branch, not `main`; base this
branch on `main` and either merge/cherry-pick the G1 panel first or add the panel
fresh - Task 7 REPLACES its expr either way.

### Task 7: Dashboard - model+cache-accurate $, functional cache-hit-ratio, churn panel, guard, version bump

**Files:**
- `dashboards/task-delivery.json` (uid `tatara-task-delivery`)
- `scripts/check_token_panels.sh`

**Steps:**
- [ ] Ensure the dashboard has the "Cost (USD) by Kind and Repo" table panel and the "Cache Hit Ratio by Kind" timeseries panel present (from G1; if basing on `main`, add them). Add a new "Churn Cost (USD) by Outcome" table panel.
- [ ] REPLACE the "Cost (USD) by Kind and Repo" panel `expr` (the G1 crude expr:
  `sum by (kind, repo) ( (operator_task_tokens_total{type="input", kind=~"triageIssue|review", ...} * 0.000003) or ... )`)
  with the model+cache-accurate expr (12 disjoint model x type terms; `or` unions them, `sum by` collapses):
  ```
  sum by (kind, repo) (
      operator_task_tokens_total{type="input",          model="claude-opus-4-8",  project=~"$project"} * 5e-6
   or operator_task_tokens_total{type="output",         model="claude-opus-4-8",  project=~"$project"} * 25e-6
   or operator_task_tokens_total{type="cache_read",     model="claude-opus-4-8",  project=~"$project"} * 0.5e-6
   or operator_task_tokens_total{type="cache_creation", model="claude-opus-4-8",  project=~"$project"} * 6.25e-6
   or operator_task_tokens_total{type="input",          model="claude-sonnet-5",  project=~"$project"} * 3e-6
   or operator_task_tokens_total{type="output",         model="claude-sonnet-5",  project=~"$project"} * 15e-6
   or operator_task_tokens_total{type="cache_read",     model="claude-sonnet-5",  project=~"$project"} * 0.3e-6
   or operator_task_tokens_total{type="cache_creation", model="claude-sonnet-5",  project=~"$project"} * 3.75e-6
   or operator_task_tokens_total{type="input",          model="claude-haiku-4-5", project=~"$project"} * 1e-6
   or operator_task_tokens_total{type="output",         model="claude-haiku-4-5", project=~"$project"} * 5e-6
   or operator_task_tokens_total{type="cache_read",     model="claude-haiku-4-5", project=~"$project"} * 0.1e-6
   or operator_task_tokens_total{type="cache_creation", model="claude-haiku-4-5", project=~"$project"} * 1.25e-6
  )
  ```
- [ ] REPLACE the "Cache Hit Ratio by Kind" panel `expr` (G1 read the dead `ccw_turn_tokens_total`) with the operator-sourced expr (functional for single-turn kinds now that `input` is uncached and `cache_read` is separate):
  ```
  sum by (kind) (rate(operator_task_tokens_total{type="cache_read", project=~"$project"}[$__rate_interval]))
  / clamp_min(sum by (kind) (rate(operator_task_tokens_total{type=~"cache_read|input", project=~"$project"}[$__rate_interval])), 0.0001)
  ```
- [ ] ADD "Churn Cost (USD) by Outcome" table panel over `operator_task_terminal_tokens_total`, `sum by (outcome)`, same 12 model x type price terms but with the metric name `operator_task_terminal_tokens_total{...outcome=~".+", project=~"$project"}` (no `$project` if the terminal metric carries no project label filter mismatch - it does carry `project`). This gives `$/delivered` vs `$/churned` vs `$/abandoned` as rows.
- [ ] Bump the dashboard top-level `"version"` field (e.g. `main` is 1 -> set to the next integer; if built on G1's 3, use 4).
- [ ] Rewrite `scripts/check_token_panels.sh` jq assertions:
  - "Cost (USD) by Kind and Repo": expr matches `operator_task_tokens_total` AND `model=` AND `cache_creation` (proves model+cache-accurate, not the crude kind-coupled expr).
  - "Cache Hit Ratio by Kind": expr matches `operator_task_tokens_total` (NOT `ccw_`) AND `type=\"cache_read\"` AND `cache_read\\|input`.
  - "Churn Cost (USD) by Outcome": panel present AND expr matches `operator_task_terminal_tokens_total` AND `by ?\\(outcome\\)`.
  - Keep the `python3 -c json.load` valid-JSON check.
- [ ] Run `sh scripts/check_token_panels.sh` -> expect `token panels OK` (FAIL first if run before the edits land, PASS after).
- [ ] Run `terraform fmt -check` and `terraform validate` (dashboards are provisioned via `dashboards.tf`) -> expect clean.
- [ ] Commit: `feat: model+cache-accurate $ panel, functional cache-hit-ratio, churn panel`.

### Task 8: Re-baseline the token-runaway alert to the model-aware $ rate

The "Wrapper token spend runaway" rule (`alerts/tatara-wrapper.yaml:140-154`)
reads the dead/ephemeral `ccw_turn_tokens_total` and thresholds on a flat
token/s number. Re-point it to the durable operator metric priced by model, in
$/s. Move it to the operator rule group (the metric is `operator_*`).

**Files:**
- `alerts/tatara-wrapper.yaml` (remove the "Wrapper token spend runaway" rule)
- `alerts/tatara-operator.yaml` (add the re-baselined rule)
- `scripts/lint_alert_rules.py` / `scripts/test_lint_alert_rules.py` (only if the
  schema check needs a touch - expected none)

**Steps:**
- [ ] Remove the `- name: "Wrapper token spend runaway"` block from `alerts/tatara-wrapper.yaml` (lines 140-154), leaving the surrounding canary comment intact.
- [ ] Add to `alerts/tatara-operator.yaml` a rule:
  ```yaml
  - name: "Agent token spend runaway ($/s)"
    queries:
      - expression: |
          sum(
              rate(operator_task_tokens_total{type="input",          model="claude-opus-4-8"}[15m]) * 5e-6
           or rate(operator_task_tokens_total{type="output",         model="claude-opus-4-8"}[15m]) * 25e-6
           or rate(operator_task_tokens_total{type="cache_read",     model="claude-opus-4-8"}[15m]) * 0.5e-6
           or rate(operator_task_tokens_total{type="cache_creation", model="claude-opus-4-8"}[15m]) * 6.25e-6
           or rate(operator_task_tokens_total{type="input",          model="claude-sonnet-5"}[15m]) * 3e-6
           or rate(operator_task_tokens_total{type="output",         model="claude-sonnet-5"}[15m]) * 15e-6
           or rate(operator_task_tokens_total{type="cache_read",     model="claude-sonnet-5"}[15m]) * 0.3e-6
           or rate(operator_task_tokens_total{type="cache_creation", model="claude-sonnet-5"}[15m]) * 3.75e-6
           or rate(operator_task_tokens_total{type="input",          model="claude-haiku-4-5"}[15m]) * 1e-6
           or rate(operator_task_tokens_total{type="output",         model="claude-haiku-4-5"}[15m]) * 5e-6
           or rate(operator_task_tokens_total{type="cache_read",     model="claude-haiku-4-5"}[15m]) * 0.1e-6
           or rate(operator_task_tokens_total{type="cache_creation", model="claude-haiku-4-5"}[15m]) * 1.25e-6
          )
    math_operator: ">"
    threshold: 0.5
    for: 15m
    decimal_points: 3
    annotations:
      summary: "Agents spending {{ index $values \"C\" }} USD/s over 15m (>$0.50/s, model-aware). A turn or loop is burning budget; check the task-delivery Cost panel."
    labels:
      homelab: "true"
      system: "tatara"
      component: "operator"
      severity: "warning"
  ```
  (Threshold $0.50/s is the model-aware analogue of the old 80k tok/s ~= $0.40/s opus-input; tune live. It folds review gap G17's alert-re-baseline loose-end.)
- [ ] Run `python3 scripts/lint_alert_rules.py` and `python3 scripts/test_lint_alert_rules.py` -> expect PASS (schema unchanged).
- [ ] Run `terraform fmt -check` + `terraform validate` -> expect clean.
- [ ] Commit: `feat: re-baseline token-runaway alert to model-aware $ rate over durable metric`.

---

## Verification (before claiming done)

- Operator: full env prefix, then `go build ./... && go vet ./... && golangci-lint run ./... && go test ./...` all green; `git diff --stat` shows the CRD bases updated and `zz_generated.deepcopy.go` unchanged.
- Observability: `sh scripts/check_token_panels.sh` prints `token panels OK`; `python3 scripts/lint_alert_rules.py` + its test pass; `terraform fmt -check` + `terraform validate` clean.
- Acceptance (post-deploy, out of plan scope): `operator_task_tokens_total` carries `model` + four `type` values live; cache-hit-ratio panel non-zero for single-turn kinds; a churned issue lands in `operator_task_terminal_tokens_total{outcome="churned"}`.
