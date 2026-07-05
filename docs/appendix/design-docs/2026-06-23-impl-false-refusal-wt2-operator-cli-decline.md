# Defect C: false-refusal already_done + decline hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the operator falsely parking implement tasks as `refused-no-explanation` when the change is already present, by adding an `already_done` outcome action and forcing every decline/already_done to carry a non-whitespace reason at both the CLI and operator layers.
**Architecture:** The CLI exposes MCP outcome tools (`decline_implementation`, new `already_done`) that POST to the operator REST `/implement-outcome` endpoint, which writes `Task.Status.ImplementOutcome`. The operator `finishImplement` reconcile path reads that outcome and parks the task on a codified-terminal path (skipping the empty-retry loop) with a distinct giveup metric label per outcome. The two repos merge and deploy independently (cli first, then operator).
**Tech Stack:** Go (stdlib log/slog, controller-runtime for operator), table-driven tests with t.Run, golangci-lint, gofmt.

## Global Constraints
- Newest stable Go; KISS; no tech-debt; JSON logs (log/slog); business actions logged at INFO with structured fields; metrics for anything that counts/times-out/fails.
- TDD strictly: failing test first, run it red, minimal impl, run green, commit. Conventional commits (feat:/fix:/refactor:/test:). Frequent commits.
- Operator CRD changes: regenerate with controller-gen via the repo's make/mise target (find it); templated CRDs apply on helm upgrade.
- Run tests via mise (`mise exec -- go test ./...` or `mise run test`); lint via `mise exec -- golangci-lint run`.

---

## Repo facts grounded in current code (read before starting)

**tatara-cli** (`~/Documents/tatara/tatara-cli`):
- `internal/mcp/tools.go:289` `OperatorTools()` builds tools with the local `op(name, desc, schema, build)` helper (`tools.go:290-292`). Each tool's `Build` returns `(method, path string, body any, err error)`.
- `decline_implementation` lives at `tools.go:525-540`. Its reason check is `if argString(a, "reason") == ""` (`tools.go:532-534`) - NOT trimmed. `argString` is defined at `tools.go:264`.
- `skip_research` (`tools.go:541-556`) already uses the trimmed pattern `if strings.TrimSpace(argString(a, "reason")) == ""` (`tools.go:548`). Mirror it.
- `strings` is already imported (used at `tools.go:548`, `tools.go:769`).
- Tests: `internal/mcp/tools_test.go`. `operatorToolByName(t, name)` helper at `tools_test.go:271`. Decline tests at `tools_test.go:120-152`. `skip_research` blank-reason test pattern at `tools_test.go:1613` (`{"reason": "   "}` expects error). `TestOperatorTools_TargetIsOperator` (`tools_test.go:154`) iterates ALL `OperatorTools()` and asserts `Target == TargetOperator` - a new tool is auto-covered.

**tatara-operator** (`~/Documents/tatara/tatara-operator`):
- `api/v1alpha1/task_types.go:62-66` `ImplementOutcome`: enum marker `// +kubebuilder:validation:Enum=declined` at line 63, `Action string`, `Reason string` (required).
- REST handler `internal/restapi/handlers.go:868-930` `implementOutcome`. `implementOutcomeReq` struct at `handlers.go:868-871`. Action validation `if req.Action != "declined"` at `handlers.go:883-886`. Reason validation `if strings.TrimSpace(req.Reason) == ""` at `handlers.go:887-890`. `strings` already imported (used at line 887).
- `finishImplement` `internal/controller/lifecycle.go:1447-1640`. No-PR branch starts at `lifecycle.go:1503`. Codified-refusal gate `if outcome != nil && outcome.Action == "declined" && strings.TrimSpace(outcome.Reason) != ""` at `lifecycle.go:1508`. The codified path posts `outcome.Reason` as a comment (`lifecycle.go:1518`), applies the `"declined"` phase label (`lifecycle.go:1525`), parks `"refused"` (`lifecycle.go:1531`), records giveup `"refused"` (`lifecycle.go:1535`), clears outcome + retries, returns. The empty-retry loop is `lifecycle.go:1546-1591`, parking `"refused-no-explanation"`.
- `emptyImplementReentryPrompt` const at `lifecycle.go:1893-1900`.
- Giveup metric: `internal/obs/lifecycle_metrics.go` - `giveupTotal` is a `*prometheus.CounterVec` with a single free-form `"reason"` label (`lifecycle_metrics.go:39-42`). `RecordGiveup(reason string)` (`lifecycle_metrics.go:103`) and `GiveupTotal(reason string)` (`lifecycle_metrics.go:130`). NO metrics-definition change needed; only new label VALUES at call sites.
- CRD regen: `Makefile:37-39` `manifests` target runs controller-gen into `charts/tatara-operator/crd-bases`. Run `mise exec -- make manifests` (and `mise exec -- make generate` if deepcopy changes, but enum-only change does not). `Makefile:110` `ci` runs `generate manifests lint test rbac-check`.
- Handler tests: `internal/restapi/handlers_test.go:474-516+` (`TestImplementOutcome_Writes`, `_MissingAction`, `_InvalidAction`, `_EmptyReason`, `_MissingReason`). Helper `taskWithKind("t1", "alpha", "issueLifecycle")` and `buildRouter(t, ...)`.
- Lifecycle tests: `internal/controller/lifecycle_audit_test.go`. `TestRecordGiveup_Refused` (`lifecycle_audit_test.go:127`) seeds `ImplementOutcome{Action:"declined", Reason:...}`, calls `reconcileLifecycle`, asserts `GiveupTotal("refused")==1` and `LifecycleState=="Parked"`. Helpers: `seedLifecycleTask`, `newAuditReconciler(t, fw)`, `fetchTask`, `noChangeRecordingSCMWriter`, `fw.RecordedComments` (verify how it records below). `TestRecordGiveup_RefusedNoExplanation` (`lifecycle_audit_test.go:166`) seeds no outcome + `ImplementEmptyRetries=2`.

**SPEC DIVERGENCE NOTE (trust the code):**
- The spec (line 106) says "split giveup metric labels ... so this class stops collapsing into one bucket" implying a metrics def change. The live metric `giveupTotal` is ALREADY a free-form `reason`-labelled CounterVec; no definition change is needed. The split is purely new label-VALUE strings at the lifecycle.go call sites. The plan reflects this.
- The spec (line 65) says `Reason string // required` and the handler already calls `strings.TrimSpace(req.Reason)`. The operator already trims; only the action allow-list and the lifecycle gate need the `already_done` addition.
- The spec header for this slice says metrics def at "lifecycle.go ~1459"; the actual def is in `internal/obs/lifecycle_metrics.go` and needs no change. Call sites are `lifecycle.go:1535` (refused) and `lifecycle.go:1589` (refused-no-explanation).

---

# TASK GROUP CLI (tatara-cli) - MERGE + DEPLOY FIRST

> Deploy order: cli merges to main first (CI builds image), then the wrapper `TATARA_CLI_VERSION` pin bump follows. CONSTRAINT (`wrapper-cli-pin-needs-tokenless-mcp`): any new cli MCP tool MUST be served by `tatara mcp` tools/list WITHOUT a token, else the wrapper image build-guard fails on main-push. The new `already_done` tool is a plain `OperatorTools()` entry registered identically to `decline_implementation`, which is already served tokenless - so no extra work is needed beyond adding it to the same slice. Do NOT gate it behind any auth.

## CLI Task 1: decline_implementation rejects whitespace reason client-side

**Interfaces**
- Consumes: `argString(a map[string]any, k string) string` (`tools.go:264`); `strings.TrimSpace` (stdlib, already imported).
- Produces: hardened `decline_implementation` tool builder (same name/path/body shape, stricter validation).

Steps:

- [ ] Write a failing test. Append to `tatara-cli/internal/mcp/tools_test.go` after `TestDeclineImplementation_Build` (ends `tools_test.go:152`):

```go
func TestDeclineImplementation_RejectsWhitespaceReason(t *testing.T) {
	t.Setenv("TATARA_TASK", "t1")
	_, _, _, err := operatorToolByName(t, "decline_implementation").Build(map[string]any{
		"reason": "   ",
	})
	require.Error(t, err)
}
```

- [ ] Run it red:

```
cd /Users/szymonri/Documents/tatara/tatara-cli && mise exec -- go test ./internal/mcp/ -run TestDeclineImplementation_RejectsWhitespaceReason
```

Expected failure: `require.Error` fails (no error returned) because the current check `argString(a, "reason") == ""` passes a whitespace string straight through and builds a body.

- [ ] Minimal impl. In `tatara-cli/internal/mcp/tools.go`, change the decline reason check at `tools.go:532-534`:

```go
				if strings.TrimSpace(argString(a, "reason")) == "" {
					return "", "", nil, fmt.Errorf("reason required (non-empty): explain why this issue is not being implemented")
				}
```

- [ ] Run it green:

```
cd /Users/szymonri/Documents/tatara/tatara-cli && mise exec -- go test ./internal/mcp/ -run 'TestDeclineImplementation'
```

Expected: all decline subtests pass, including the new whitespace one and the existing `require reason`/`require task` cases.

- [ ] Commit:

```
cd /Users/szymonri/Documents/tatara/tatara-cli && git add internal/mcp/tools.go internal/mcp/tools_test.go && git commit -m "fix: reject whitespace decline reason client-side"
```

## CLI Task 2: add already_done MCP tool (trimmed-reason, declined-mirror)

**Interfaces**
- Consumes: `op(name, desc, schema string, build func(map[string]any)(string,string,any,error)) Tool` (`tools.go:290`); `argOrEnv(a, "task", "TATARA_TASK")`, `argString`, `strings.TrimSpace`, `url.PathEscape`.
- Produces: a new `OperatorTools()` entry `already_done` that POSTs to `/tasks/{t}/implement-outcome` with body `{"action":"already_done","reason":<reason>}`. Target is `TargetOperator` (set by `op`).

Steps:

- [ ] Write a failing test. Append to `tatara-cli/internal/mcp/tools_test.go` after the test added in CLI Task 1:

```go
func TestAlreadyDone_Build(t *testing.T) {
	t.Run("explicit task + reason", func(t *testing.T) {
		t.Setenv("TATARA_TASK", "")
		m, p, body, err := operatorToolByName(t, "already_done").Build(map[string]any{
			"task":   "t1",
			"reason": "the change is already present on the shared branch",
		})
		require.NoError(t, err)
		require.Equal(t, http.MethodPost, m)
		require.Equal(t, "/tasks/t1/implement-outcome", p)
		bm := body.(map[string]any)
		require.Equal(t, "already_done", bm["action"])
		require.Equal(t, "the change is already present on the shared branch", bm["reason"])
	})
	t.Run("env fallback", func(t *testing.T) {
		t.Setenv("TATARA_TASK", "env-task")
		_, p, _, err := operatorToolByName(t, "already_done").Build(map[string]any{
			"reason": "already committed",
		})
		require.NoError(t, err)
		require.Equal(t, "/tasks/env-task/implement-outcome", p)
	})
	t.Run("require reason", func(t *testing.T) {
		t.Setenv("TATARA_TASK", "t1")
		_, _, _, err := operatorToolByName(t, "already_done").Build(map[string]any{})
		require.Error(t, err)
	})
	t.Run("reject whitespace reason", func(t *testing.T) {
		t.Setenv("TATARA_TASK", "t1")
		_, _, _, err := operatorToolByName(t, "already_done").Build(map[string]any{"reason": "  "})
		require.Error(t, err)
	})
	t.Run("require task", func(t *testing.T) {
		t.Setenv("TATARA_TASK", "")
		_, _, _, err := operatorToolByName(t, "already_done").Build(map[string]any{"reason": "x"})
		require.Error(t, err)
	})
}
```

- [ ] Run it red:

```
cd /Users/szymonri/Documents/tatara/tatara-cli && mise exec -- go test ./internal/mcp/ -run TestAlreadyDone_Build
```

Expected failure: `operatorToolByName` calls `t.Fatalf` (tool not found) because `already_done` does not exist yet.

- [ ] Minimal impl. In `tatara-cli/internal/mcp/tools.go`, insert the new tool immediately after the `decline_implementation` entry (after its closing `}),` at `tools.go:540`) and before `op("skip_research", ...`:

```go
		op("already_done", "Declare that the requested change is ALREADY PRESENT and no new code is needed (e.g. another task already committed the fix on the shared branch, so this run produced no diff). Call this when, after re-reading the issue and the repository, you confirm the fix already exists. Posts the reason as a comment on the issue and parks the task. This is NOT a refusal - use decline_implementation if you are refusing to implement. A silent finish with no PR and no already_done/decline_implementation call is NOT allowed.",
			`{"type":"object","properties":{"task":{"type":"string"},"reason":{"type":"string","description":"What already-present change satisfies the issue (where the fix already lives, e.g. the commit/branch/PR), so no new code was produced. Posted to the issue."}},"required":["reason"]}`,
			func(a map[string]any) (string, string, any, error) {
				tk := argOrEnv(a, "task", "TATARA_TASK")
				if tk == "" {
					return "", "", nil, fmt.Errorf("task required")
				}
				if strings.TrimSpace(argString(a, "reason")) == "" {
					return "", "", nil, fmt.Errorf("reason required (non-empty): explain what already-present change satisfies this issue")
				}
				body := map[string]any{
					"action": "already_done",
					"reason": a["reason"],
				}
				return http.MethodPost, "/tasks/" + url.PathEscape(tk) + "/implement-outcome", body, nil
			}),
```

- [ ] Also update the `decline_implementation` description so the agent knows the difference. Replace the description string at `tools.go:525` with:

```go
		op("decline_implementation", "Declare that you REFUSE to implement this issue (it should not be done: out of scope, wrong approach, blocked, harmful). Call this after investigation when no code change SHOULD be made. Posts the reason as a comment on the issue and parks the task. If instead the change is ALREADY PRESENT and nothing is needed, call already_done. A silent finish with no PR and no decline_implementation/already_done call is NOT allowed.",
```

- [ ] Run it green:

```
cd /Users/szymonri/Documents/tatara/tatara-cli && mise exec -- go test ./internal/mcp/
```

Expected: `TestAlreadyDone_Build` (all subtests), `TestDeclineImplementation_*`, and `TestOperatorTools_TargetIsOperator` (auto-covers the new tool) pass.

- [ ] Lint:

```
cd /Users/szymonri/Documents/tatara/tatara-cli && mise exec -- golangci-lint run ./internal/mcp/
```

- [ ] Commit:

```
cd /Users/szymonri/Documents/tatara/tatara-cli && git add internal/mcp/tools.go internal/mcp/tools_test.go && git commit -m "feat: add already_done MCP outcome tool for already-present changes"
```

## CLI Task 3: verify tokenless tools/list exposes already_done

**Interfaces**
- Consumes: the `tatara mcp` server's tools/list (built from `AllTools()` -> `OperatorTools()`).
- Produces: confidence the wrapper cli-pin build-guard will pass.

Steps:

- [ ] Confirm `already_done` is reachable in the tool list without auth. Inspect whether a tokenless tools/list test already exists:

```
cd /Users/szymonri/Documents/tatara/tatara-cli && grep -rn "tools/list\|ListTools\|tools_list\|AllTools" internal/ cmd/ --include="*.go" | grep -iv _test
```

- [ ] If a tools/list serving path test exists, add an assertion that `already_done` appears in the returned tool names (mirror however that test enumerates names). If NO such serving test exists, add a minimal guard test to `tatara-cli/internal/mcp/tools_test.go`:

```go
func TestAllToolsIncludeAlreadyDone(t *testing.T) {
	found := false
	for _, tl := range AllTools() {
		if tl.Name == "already_done" {
			found = true
			require.Equal(t, TargetOperator, tl.Target)
		}
	}
	require.True(t, found, "already_done must be in AllTools() so tatara mcp serves it tokenless")
}
```

(Confirm `AllTools()` at `tools.go:36` includes `OperatorTools()`; if it composes via a different aggregator, assert against that aggregator instead.)

- [ ] Run it:

```
cd /Users/szymonri/Documents/tatara/tatara-cli && mise exec -- go test ./internal/mcp/ -run TestAllToolsIncludeAlreadyDone
```

Expected: green (red first if `already_done` were missing - it is present from CLI Task 2, so this is a regression guard).

- [ ] Commit:

```
cd /Users/szymonri/Documents/tatara/tatara-cli && git add internal/mcp/tools_test.go && git commit -m "test: guard already_done is served by tools/list tokenless"
```

---

# TASK GROUP OPERATOR (tatara-operator) - MERGE + DEPLOY SECOND

> Merge after the cli image exists (and per the spec, after WT-1 Defect-B operator slice if executed; this slice rebases on top). CRD enum change applies on helm upgrade (`operator-crd-templating-helm-adoption`).

## OP Task 1: ImplementOutcome enum accepts already_done

**Interfaces**
- Consumes: kubebuilder enum marker on `ImplementOutcome.Action` (`task_types.go:63`).
- Produces: regenerated CRD at `charts/tatara-operator/crd-bases` allowing `action: already_done`.

Steps:

- [ ] Edit `tatara-operator/api/v1alpha1/task_types.go`. Change the enum marker at line 63 from:

```go
	// +kubebuilder:validation:Enum=declined
```

to:

```go
	// +kubebuilder:validation:Enum=declined;already_done
```

- [ ] Regenerate the CRD manifests:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- make manifests
```

- [ ] Verify the enum landed in the generated CRD:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && grep -rn "already_done" charts/tatara-operator/crd-bases/
```

Expected: at least one hit (the `implementOutcome.action` enum now lists `already_done`).

- [ ] Build to confirm the API package still compiles:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go build ./api/...
```

- [ ] Commit:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && git add api/v1alpha1/task_types.go charts/tatara-operator/crd-bases && git commit -m "feat: allow already_done ImplementOutcome action in CRD enum"
```

## OP Task 2: REST handler accepts declined|already_done via allow-map, both require trimmed reason

**Interfaces**
- Consumes: `implementOutcomeReq{Action, Reason}` (`handlers.go:868`); `strings.TrimSpace`; `writeError`.
- Produces: `implementOutcome` handler accepting both actions; persists `ImplementOutcome{Action, Reason}` to `Task.Status`.

Steps:

- [ ] Write failing tests. Append to `tatara-operator/internal/restapi/handlers_test.go` after `TestImplementOutcome_MissingReason` (find its end near `handlers_test.go:518+`; place the block after it):

```go
func TestImplementOutcome_AlreadyDoneWrites(t *testing.T) {
	r := buildRouter(t, taskWithKind("t1", "alpha", "issueLifecycle"))
	body := strings.NewReader(`{"action":"already_done","reason":"already committed on the shared branch"}`)
	req := httptest.NewRequest(http.MethodPost, "/tasks/t1/implement-outcome", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusOK, w.Code)
	var out restapi.TaskDTO
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &out))
	require.NotNil(t, out.Status.ImplementOutcome)
	require.Equal(t, "already_done", out.Status.ImplementOutcome.Action)
	require.Equal(t, "already committed on the shared branch", out.Status.ImplementOutcome.Reason)
}

func TestImplementOutcome_AlreadyDoneEmptyReason(t *testing.T) {
	r := buildRouter(t, taskWithKind("t1", "alpha", "issueLifecycle"))
	body := strings.NewReader(`{"action":"already_done","reason":"   "}`)
	req := httptest.NewRequest(http.MethodPost, "/tasks/t1/implement-outcome", body)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusBadRequest, w.Code)
}
```

- [ ] Run them red:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/restapi/ -run 'TestImplementOutcome_AlreadyDone'
```

Expected failure: `TestImplementOutcome_AlreadyDoneWrites` gets 400 (current handler rejects any action != "declined" at `handlers.go:883`). `TestImplementOutcome_AlreadyDoneEmptyReason` happens to pass (already 400) but stays as a regression guard.

- [ ] Minimal impl. In `tatara-operator/internal/restapi/handlers.go`, replace the action check at `handlers.go:883-886`:

```go
	if req.Action != "declined" {
		writeError(w, http.StatusBadRequest, "action must be declined")
		return
	}
```

with an allow-map and an updated message:

```go
	validImplementActions := map[string]bool{"declined": true, "already_done": true}
	if !validImplementActions[req.Action] {
		writeError(w, http.StatusBadRequest, "action must be one of declined, already_done")
		return
	}
```

The existing trimmed-reason check (`handlers.go:887-890`) already applies to both actions; update only its message to be action-agnostic:

```go
	if strings.TrimSpace(req.Reason) == "" {
		writeError(w, http.StatusBadRequest, "reason required (non-empty) for action "+req.Action)
		return
	}
```

- [ ] Run green:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/restapi/ -run 'TestImplementOutcome'
```

Expected: all `TestImplementOutcome_*` pass (declined writes, missing/invalid action, empty/missing reason, new already_done writes + empty-reason).

- [ ] Commit:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && git add internal/restapi/handlers.go internal/restapi/handlers_test.go && git commit -m "feat: implement-outcome accepts already_done with required trimmed reason"
```

## OP Task 3: finishImplement takes codified-terminal path for declined OR already_done

**Interfaces**
- Consumes: `fresh.Status.ImplementOutcome *tatarav1alpha1.ImplementOutcome` (Action, Reason); `r.scmContext`, `writer.Comment`, `r.ensurePhaseLabel`, `r.setLifecycleState`, `r.LifecycleMetrics.RecordGiveup(reason)`, `r.clearImplementOutcome`, `r.setImplementEmptyRetries`, `r.resetAgentRun`.
- Produces: codified-terminal parking for both `declined` (park reason `refused`, giveup `refused-declined`) and `already_done` (park reason `refused-already-done`, giveup `refused-already-done`), each posting the reason comment + `declined` phase label, skipping the empty-retry loop.

NOTE: park-reason strings: spec text (line 99 of the orchestrator brief) says for `already_done` park reason `refused-already-done`, for `declined` keep `refused`. Giveup metric label split: `refused-declined` / `refused-already-done` / `refused-no-explanation`. So the LifecycleState park-reason and the giveup metric label differ for the declined case (`refused` vs `refused-declined`) - keep them as specified.

Steps:

- [ ] Write a failing test. Append to `tatara-operator/internal/controller/lifecycle_audit_test.go` after `TestRecordGiveup_Refused` (ends `lifecycle_audit_test.go:162`):

```go
// TestRecordGiveup_AlreadyDone verifies an already_done outcome parks via the
// codified-terminal path with giveup reason "refused-already-done" and
// LifecycleState park reason "refused-already-done".
func TestRecordGiveup_AlreadyDone(t *testing.T) {
	ctx := logf.IntoContext(context.Background(), logf.Log)
	name := "audit-giveup-alreadydone"
	proj := "audit-gad-proj"
	repo := "audit-gad-repo"
	sec := "audit-gad-sec"
	src := &tatarav1alpha1.TaskSource{
		Provider: "github", IssueRef: "o/r#405",
		URL: "https://github.com/o/r/issues/405", Number: 405,
		IsPR: false,
	}
	task := seedLifecycleTask(t, name, proj, repo, sec, src)
	task.Status.LifecycleState = "Implement"
	task.Status.Phase = "Succeeded"
	task.Status.ImplementOutcome = &tatarav1alpha1.ImplementOutcome{
		Action: "already_done", Reason: "fix already committed on the shared branch in PR #101",
	}
	if err := k8sClient.Status().Update(context.Background(), task); err != nil {
		t.Fatalf("seed: %v", err)
	}

	fw := &noChangeRecordingSCMWriter{}
	r, lm, _ := newAuditReconciler(t, fw)

	_, err := r.reconcileLifecycle(ctx, fetchTask(t, name))
	if err != nil {
		t.Fatalf("reconcileLifecycle: %v", err)
	}

	if v := testutil.ToFloat64(lm.GiveupTotal("refused-already-done")); v != 1 {
		t.Errorf("giveup{refused-already-done} = %v, want 1", v)
	}
	if got := fetchTask(t, name); got.Status.LifecycleState != "Parked" {
		t.Errorf("LifecycleState = %q, want Parked", got.Status.LifecycleState)
	}
}
```

Also add a guard that `declined` now records `refused-declined`. Append:

```go
// TestRecordGiveup_RefusedDeclinedLabel verifies the declined codified path
// now records giveup label "refused-declined" (split from the old "refused").
func TestRecordGiveup_RefusedDeclinedLabel(t *testing.T) {
	ctx := logf.IntoContext(context.Background(), logf.Log)
	name := "audit-giveup-refdecl"
	proj := "audit-grd-proj"
	repo := "audit-grd-repo"
	sec := "audit-grd-sec"
	src := &tatarav1alpha1.TaskSource{
		Provider: "github", IssueRef: "o/r#406",
		URL: "https://github.com/o/r/issues/406", Number: 406,
		IsPR: false,
	}
	task := seedLifecycleTask(t, name, proj, repo, sec, src)
	task.Status.LifecycleState = "Implement"
	task.Status.Phase = "Succeeded"
	task.Status.ImplementOutcome = &tatarav1alpha1.ImplementOutcome{
		Action: "declined", Reason: "out of scope, tracked elsewhere",
	}
	if err := k8sClient.Status().Update(context.Background(), task); err != nil {
		t.Fatalf("seed: %v", err)
	}

	fw := &noChangeRecordingSCMWriter{}
	r, lm, _ := newAuditReconciler(t, fw)

	_, err := r.reconcileLifecycle(ctx, fetchTask(t, name))
	if err != nil {
		t.Fatalf("reconcileLifecycle: %v", err)
	}

	if v := testutil.ToFloat64(lm.GiveupTotal("refused-declined")); v != 1 {
		t.Errorf("giveup{refused-declined} = %v, want 1", v)
	}
}
```

Update the EXISTING `TestRecordGiveup_Refused` (`lifecycle_audit_test.go:156`) assertion - it asserts `GiveupTotal("refused")==1`, which will become `refused-declined`. Change line 156-157 from:

```go
	if v := testutil.ToFloat64(lm.GiveupTotal("refused")); v != 1 {
		t.Errorf("giveup{refused} = %v, want 1", v)
	}
```

to:

```go
	if v := testutil.ToFloat64(lm.GiveupTotal("refused-declined")); v != 1 {
		t.Errorf("giveup{refused-declined} = %v, want 1", v)
	}
```

(Either keep `TestRecordGiveup_Refused` updated or rely on the new `TestRecordGiveup_RefusedDeclinedLabel`; update the old one so it does not assert the now-dead `refused` label.)

- [ ] Run them red:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run 'TestRecordGiveup_AlreadyDone|TestRecordGiveup_RefusedDeclinedLabel|TestRecordGiveup_Refused$'
```

Expected failure: `TestRecordGiveup_AlreadyDone` fails - the current gate at `lifecycle.go:1508` only matches `Action == "declined"`, so `already_done` falls into the empty-retry loop and never records `refused-already-done` (and does not park terminally on the first reconcile). `TestRecordGiveup_RefusedDeclinedLabel` and the edited `TestRecordGiveup_Refused` fail because the call site still records `refused`.

- [ ] Minimal impl. In `tatara-operator/internal/controller/lifecycle.go`, change the codified-refusal gate at `lifecycle.go:1508` from:

```go
		outcome := fresh.Status.ImplementOutcome
		if outcome != nil && outcome.Action == "declined" && strings.TrimSpace(outcome.Reason) != "" {
```

to (accept both actions):

```go
		outcome := fresh.Status.ImplementOutcome
		codifiedTerminal := outcome != nil &&
			(outcome.Action == "declined" || outcome.Action == "already_done") &&
			strings.TrimSpace(outcome.Reason) != ""
		if codifiedTerminal {
			// Per-action park reason + giveup metric label.
			parkReason := "refused"
			giveupReason := "refused-declined"
			if outcome.Action == "already_done" {
				parkReason = "refused-already-done"
				giveupReason = "refused-already-done"
			}
```

Then update the log line at `lifecycle.go:1512-1513` to be action-aware:

```go
			l.Info("implement: agent declared codified terminal outcome; parking",
				"action", "lifecycle_implement_codified_terminal", "resource_id", task.Name,
				"impl_action", outcome.Action, "park_reason", parkReason)
```

Update the `setLifecycleState` call at `lifecycle.go:1531` from:

```go
			if err := r.setLifecycleState(ctx, fresh, "Parked", "refused"); err != nil {
```

to:

```go
			if err := r.setLifecycleState(ctx, fresh, "Parked", parkReason); err != nil {
```

Update the `RecordGiveup` call at `lifecycle.go:1535` from:

```go
			if r.LifecycleMetrics != nil {
				r.LifecycleMetrics.RecordGiveup("refused")
			}
```

to:

```go
			if r.LifecycleMetrics != nil {
				r.LifecycleMetrics.RecordGiveup(giveupReason)
			}
```

The comment posting (`outcome.Reason`, `lifecycle.go:1518`) and the `declined` phase label (`lifecycle.go:1525`) stay as-is - both `declined` and `already_done` post the reason comment and apply the `declined` label, matching the spec ("Post the reason comment + declined label as today"). Leave `clearImplementOutcome`, `setImplementEmptyRetries(0)`, and `resetAgentRun` unchanged.

- [ ] Run green:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run 'TestRecordGiveup'
```

Expected: `TestRecordGiveup_AlreadyDone`, `TestRecordGiveup_RefusedDeclinedLabel`, edited `TestRecordGiveup_Refused`, and the untouched `TestRecordGiveup_RefusedNoExplanation` / `_TriageFailed` / `_ImplementFailed` / `_NoPRNumber` all pass.

- [ ] Lint:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- golangci-lint run ./internal/controller/ ./internal/restapi/
```

- [ ] Commit:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && git add internal/controller/lifecycle.go internal/controller/lifecycle_audit_test.go && git commit -m "fix: finishImplement codified-terminal path for declined and already_done"
```

## OP Task 4: update the empty-retry re-entry prompt to mention already_done

**Interfaces**
- Consumes: `emptyImplementReentryPrompt` const (`lifecycle.go:1893-1900`), used at `lifecycle.go:1551`.
- Produces: the re-entry nudge that now offers the `already_done` tool as the correct escape for an already-present change, so a respawned duplicate stops mislabelling itself.

NOTE: This is the agent-facing prompt; no behavioral test asserts its exact text. Keep it a const edit and verify the package still builds/tests. (Boy-scout: keeping the prompt aligned with the new tool prevents the false-refusal recurring.)

Steps:

- [ ] Edit the const at `lifecycle.go:1893-1900`. Replace the whole const with:

```go
const emptyImplementReentryPrompt = "Your previous attempt finished without " +
	"committing any change, so no PR could be opened and the issue is still open. " +
	"Re-read the issue and the repository, then do EXACTLY ONE of: " +
	"(1) implement the fix and commit it; " +
	"(2) if the change is ALREADY PRESENT (e.g. another run already committed it on the shared branch), " +
	"call `already_done` with a reason naming where the fix already lives; " +
	"(3) if this issue genuinely should NOT be implemented (out of scope, wrong approach, blocked), " +
	"call `decline_implementation` with a clear reason. " +
	"A silent finish with no PR and no `already_done`/`decline_implementation` call is NOT allowed " +
	"and will be escalated to a human."
```

- [ ] Build + run the controller tests to confirm nothing references the old wording:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go build ./... && mise exec -- go test ./internal/controller/ -run 'TestRecordGiveup|Empty'
```

Expected: green (no test pins the prompt string; if one does, update it to match).

- [ ] Commit:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && git add internal/controller/lifecycle.go && git commit -m "fix: re-entry prompt offers already_done for already-present changes"
```

## OP Task 5: full operator verification gate

**Interfaces**
- Consumes: all prior operator tasks.
- Produces: a fully green `make ci` proving CRD/handler/lifecycle/metrics changes integrate.

Steps:

- [ ] Run the full operator test + generate + manifests + rbac gate:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- make ci
```

Expected: green. If `manifests` produces a diff, the CRD was not committed in OP Task 1 - re-run and commit. If `rbac-check` fails, no new CRD/controller was added here (enum-only), so investigate any unrelated drift before proceeding.

- [ ] If `make ci` is unavailable or partial, fall back to:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./... && mise exec -- golangci-lint run && mise exec -- gofmt -l api internal
```

Expected: tests pass, lint clean, `gofmt -l` prints nothing.

---

# FINAL TASK (both repos): code review + verification before completion

**Interfaces**
- Consumes: all commits in tatara-cli and tatara-operator on the worktree branch.
- Produces: a reviewed, verified, lint-clean, test-green slice ready to merge (cli first, then operator).

Steps:

- [ ] Invoke `superpowers:requesting-code-review` on the combined diff of both repos. Fix every critical/high finding (re-running the relevant `mise exec -- go test` after each fix). Do not skip findings.

- [ ] Invoke `superpowers:verification-before-completion`. Run and paste real output for ALL of:

```
cd /Users/szymonri/Documents/tatara/tatara-cli && mise exec -- go test ./... && mise exec -- golangci-lint run && mise exec -- gofmt -l internal cmd
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- make ci
cd /Users/szymonri/Documents/tatara/tatara-operator && grep -rn "already_done" charts/tatara-operator/crd-bases/
```

Expected evidence: both test suites green; both lints clean; `gofmt -l` empty; CRD contains `already_done`.

- [ ] Confirm the deploy-order constraints are satisfied before claiming done: (a) cli commits are independently buildable so the cli image ships first; (b) `already_done` is in `OperatorTools()`/`AllTools()` so `tatara mcp` serves it tokenless (CLI Task 3) - required for the downstream wrapper `TATARA_CLI_VERSION` pin bump to pass its build-guard; (c) operator commits include the regenerated CRD so the helm upgrade enables `already_done`.

- [ ] Do NOT merge or push unless the user explicitly asks. Report: commits per repo, test/lint evidence, and that cli must merge + build before the wrapper pin bump, operator merges after.
