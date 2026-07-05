# Brainstorm throughput + queue-as-concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make hourly brainstorm actually deliver new issues by removing the redundant autonomous-enqueue cap (queue limits concurrency, not creation), raising `maxOpenProposals` to 10, focusing brainstorm on new ideas only, and giving it a cheap early-exit.

**Architecture:** Operator-side changes drop the shared `remaining` autonomous budget so cron events always enqueue and wait in `Queued` (bounded only by `QueueCapacity` concurrency, already built); brainstorm prompt drops the comment-on-existing path and gains an early-exit instruction; a new `skip_brainstorm` MCP tool (tatara-cli) + `/tasks/{t}/brainstorm-outcome` REST endpoint records a no-proposal exit. Deploy is GitOps: cli -> wrapper pin -> operator -> one tatara-helmfile MR.

**Tech Stack:** Go (k8s controller-runtime operator + cli MCP server), Helm, Helmfile, mise toolchain.

## Global Constraints

- Newest stable Go; build/test/lint via `mise exec -- go ...` / `mise run lint|test|build` (bare `go` may be wrong version). Run `mise install` once in a fresh clone.
- JSON logs only (`log/slog`); log every business action at INFO with structured fields; wrap errors `fmt.Errorf("context: %w", err)`.
- Table-driven Go tests with `t.Run`. TDD: failing test first.
- KISS, no tech-debt; document non-obvious decisions in the repo `MEMORY.md`.
- Branch flow: worktree off fresh `main` -> develop -> merge to `main` -> build/deploy from `main` only. Start every repo from `git checkout main && git pull` (external bots push to these repos).
- Deploy ONLY via tatara-helmfile GitOps. Never `kubectl set image`/`patch`/`helm upgrade` by hand. Bump BOTH chart version AND pinned `image.tag`.
- `requesting-code-review` before each commit; fix critical/high; then commit.
- Adding a CRD status field requires regenerating manifests (`make manifests generate`); the operator chart templates its CRDs (`crd-bases/` + `templates/crds.yaml`), so the helm upgrade applies the new field. A pre-existing CRD may need a one-time helm-ownership relabel.

---

## Dependency order

```
C1 (cli skip_brainstorm) ---------> W1 (wrapper cli-pin bump)
O1,O2,O3 (operator, same file: sequential) 
O4 (operator: api+restapi+writeback, parallel with O1-O3)
   all images built --------------> H1 (helmfile MR: tags + chart versions + maxOpenProposals=10)
```

Operator tasks O1 -> O2 -> O3 touch `internal/controller/projectscan.go` and MUST run sequentially. O4 touches different files and may run in parallel. C1 is independent. W1 needs C1 merged + cli image tag. H1 is last.

---

## Task C1: tatara-cli `skip_brainstorm` MCP tool

Repo: `tatara-cli`. Branch: `feat/skip-brainstorm-tool` off fresh `main`.

**Files:**
- Modify: `internal/mcp/tools.go` (add tool in `OperatorTools`, after `decline_implementation` ~line 540)
- Test: `internal/mcp/tools_test.go` (existing tools-list / request-shape tests)

**Interfaces:**
- Produces: MCP tool `skip_brainstorm{task?, reason}` issuing `POST /tasks/{task}/brainstorm-outcome` with body `{"action":"none","reason":<reason>}`. `task` defaults to `TATARA_TASK` env. Consumed by operator endpoint in O4.

- [ ] **Step 1: Write the failing test**

In `internal/mcp/tools_test.go`, follow the existing pattern used to assert a tool's HTTP mapping (mirror the `decline_implementation` test if present; otherwise the table that exercises `op` builders). Add:

```go
func TestSkipBrainstormTool(t *testing.T) {
	tool := findOperatorTool(t, "skip_brainstorm")
	method, path, body, err := tool.build(map[string]any{"task": "t-123", "reason": "nothing worth proposing"})
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	if method != http.MethodPost {
		t.Fatalf("method = %s, want POST", method)
	}
	if path != "/tasks/t-123/brainstorm-outcome" {
		t.Fatalf("path = %s", path)
	}
	m := body.(map[string]any)
	if m["action"] != "none" || m["reason"] != "nothing worth proposing" {
		t.Fatalf("body = %#v", m)
	}
}

func TestSkipBrainstormRequiresReason(t *testing.T) {
	tool := findOperatorTool(t, "skip_brainstorm")
	if _, _, _, err := tool.build(map[string]any{"task": "t-1"}); err == nil {
		t.Fatal("expected error when reason missing")
	}
}
```

If `findOperatorTool` / `tool.build` helpers do not already exist in the test file, reuse the access pattern the existing tool tests use (they call `OperatorTools(...)` and locate by name). Match the existing helper names rather than inventing new ones.

- [ ] **Step 2: Run test to verify it fails**

Run: `mise exec -- go test ./internal/mcp/ -run SkipBrainstorm -v`
Expected: FAIL (`skip_brainstorm` not found).

- [ ] **Step 3: Implement the tool**

In `internal/mcp/tools.go`, immediately after the `decline_implementation` `op(...)` block (ends ~line 540), add:

```go
		op("skip_brainstorm", "Declare that this brainstorm cycle has nothing worth proposing, and exit early WITHOUT running the full deep-research fan-out. Call this after a cheap initial survey when no genuinely novel, high-leverage idea exists. Records the reason and ends the turn so no tokens are wasted.",
			`{"type":"object","properties":{"task":{"type":"string"},"reason":{"type":"string","description":"Why there is nothing to propose this cycle (what you scanned, why no new idea clears the bar)."}},"required":["reason"]}`,
			func(a map[string]any) (string, string, any, error) {
				tk := argOrEnv(a, "task", "TATARA_TASK")
				if tk == "" {
					return "", "", nil, fmt.Errorf("task required")
				}
				if argString(a, "reason") == "" {
					return "", "", nil, fmt.Errorf("reason required")
				}
				body := map[string]any{
					"action": "none",
					"reason": a["reason"],
				}
				return http.MethodPost, "/tasks/" + url.PathEscape(tk) + "/brainstorm-outcome", body, nil
			}),
```

- [ ] **Step 4: Run tests to verify pass**

Run: `mise exec -- go test ./internal/mcp/ -run SkipBrainstorm -v`
Expected: PASS. Then `mise run lint` and `mise exec -- go test ./...`.

- [ ] **Step 5: Verify tokenless tools/list (wrapper build-guard contract)**

The wrapper build guard requires `tatara mcp` to serve tools/list WITHOUT a token. Confirm `skip_brainstorm` is in the operator tool group and needs no token to list:

Run: `mise exec -- go test ./... && printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | mise exec -- go run . mcp 2>/dev/null | grep -c skip_brainstorm`
Expected: `>=1` (tool listed without TATARA_OPERATOR_TOKEN set).

- [ ] **Step 6: Code review, then commit**

Use `superpowers:requesting-code-review`; fix critical/high. Then:

```bash
git add internal/mcp/tools.go internal/mcp/tools_test.go
git commit -m "feat: add skip_brainstorm MCP tool for brainstorm early-exit"
```

---

## Task O1: Operator - remove autonomous-enqueue budget (queue limits concurrency, not creation)

Repo: `tatara-operator`. Branch: `feat/brainstorm-throughput` off fresh `main`. (O1->O2->O3 share this branch + file, sequential.)

**Files:**
- Modify: `internal/controller/projectscan.go` (remove `remaining *int` from `mrScan`@587, `issueScan`@705, `brainstorm`@834, `healthCheck`@952, `recoverOrphans`@1464; remove budget computation in `runScans`@1580-1593 and re-list@1631-1638; remove all `*remaining` guards + `skipped_budget` ScanItems @630-634,799-804,852-855,970-973,1465,1515)
- Modify: `api/v1alpha1/project_types.go` (deprecate doc comments @255-258, 283-286; remove `QueuedAutonomousCap()` call sites)
- Verify: `createHealthCheckTask`@364 uses a constant per-project dedup key (no-overlap); fix if not.
- Test: `internal/controller/projectscan_*_test.go`

**Interfaces:**
- Produces: `mrScan`, `issueScan`, `brainstorm`, `healthCheck`, `recoverOrphans` no longer take a `remaining *int` parameter and never short-circuit on a queued-autonomous budget. Concurrency is bounded solely by the dispatcher's `QueueCapacity`.

- [ ] **Step 1: Write the failing test**

Add a test asserting that with the queued-autonomous count already at/over the old cap, a due brainstorm STILL enqueues (no `skipped_budget`/`skipped`-on-cap). In `internal/controller/projectscan_run_test.go` (or a new `projectscan_no_budget_test.go`), mirror the existing scan-test harness (fake client, Project with `maxOpenTasks: 1`, pre-seed >=1 Queued autonomous QueuedEvent, brainstorm enabled + due, open-proposal backlog below `maxOpenProposals`):

```go
func TestBrainstormEnqueuesDespiteQueuedAutonomousCount(t *testing.T) {
	// Project: maxOpenTasks=1 (old cap), brainstorm enabled + due, backlog under maxOpenProposals.
	// Pre-seed two Queued autonomous QueuedEvents (count=2 > old cap 1).
	// Run the scan reconcile.
	// Expect: a NEW brainstorm QueuedEvent was created (dedupKey "brainstorm-<proj>"),
	// i.e. the old budget no longer blocks creation.
	...
	if !brainstormQEExists(qes, proj.Name) {
		t.Fatal("brainstorm event was not enqueued; budget gate still blocks creation")
	}
}
```

Build it from the closest existing scan test's setup helpers (do not invent new fakes).

- [ ] **Step 2: Run test to verify it fails**

Run: `mise exec -- go test ./internal/controller/ -run BrainstormEnqueuesDespite -v`
Expected: FAIL (event not created; budget short-circuits).

- [ ] **Step 3: Remove the budget plumbing**

In `internal/controller/projectscan.go`:
- Delete the budget block in `runScans` (`@1580-1593`) and the re-list recompute (`@1631-1638` keep the QE/Task re-list for dedup freshness, drop only the `remaining = ...` recompute).
- Change each call site to drop `&remaining`: `r.mrScan(ctx, proj, reader, repos, existing, cronSpec.MRScan)`, same for `issueScan`, `brainstorm`, `healthCheck`; `recoverOrphans(ctx, proj, reader, repos, issueCache)`. Remove the `if remaining > 0` guard around `recoverOrphans` (`@1655`) so it always runs after issueScan.
- In each function signature remove the trailing `remaining *int` param.
- Delete every `if *remaining <= 0 { ... return/break }` block and its `skipped_budget` ScanItem (`@630-634`, `@799-804`, `@852-855`, `@970-973`, `@1465`, `@1515`) and every `*remaining--`.

In `api/v1alpha1/project_types.go`:
- Update the doc comment on `MaxOpenTasks` (`@255-258`) and `Queue.QueuedAutonomousCap` (`@283-286`) to: `Deprecated: no longer enforced. The queue bounds CONCURRENCY (QueueCapacity), not event creation; over-limit events wait in Queued. Retained for CRD backward-compatibility; ignored.`
- Remove the now-unused `QueuedAutonomousCap()` method and its references (or keep the method but delete its only call site in projectscan; prefer removing the method if golangci-lint flags it as unused).

- [ ] **Step 4: Verify healthCheck no-overlap dedup**

Read `createHealthCheckTask`@364. Confirm its `dedupKey` is constant per project (e.g. `"healthcheck-"+proj.Name`) like brainstorm's `"brainstorm-"+proj.Name`@343. If it is NOT constant, change it to a constant per-project key so two health-checks can never run concurrently. Note the finding in the commit body.

- [ ] **Step 5: Run tests to verify pass**

Run: `mise exec -- go test ./internal/controller/ -run BrainstormEnqueuesDespite -v` (PASS), then `mise exec -- go test ./... && mise run lint`. Fix any test that asserted the old `skipped_budget` behavior (those assertions are now invalid; update them to expect enqueue).

- [ ] **Step 6: Update MEMORY.md + commit**

Add one dated line to operator `MEMORY.md`: queued-autonomous budget removed; queue now bounds concurrency only; maxOpenTasks/QueuedAutonomousCap deprecated (kept for CRD compat). Then code review (`requesting-code-review`), fix critical/high:

```bash
git add internal/controller/projectscan.go api/v1alpha1/project_types.go internal/controller/*_test.go MEMORY.md
git commit -m "feat: remove autonomous-enqueue budget; queue bounds concurrency not creation"
```

---

## Task O2: Operator - `maxOpenProposals` fallback 5 -> 10

Repo: `tatara-operator`, same branch `feat/brainstorm-throughput` (after O1).

**Files:**
- Modify: `internal/controller/projectscan.go` (`brainstorm`@838-840, `healthCheck`@956-957)
- Test: `internal/controller/projectscan_brainstorm_project_test.go`

- [ ] **Step 1: Write the failing test**

```go
func TestBrainstormDefaultProposalCapIsTen(t *testing.T) {
	// Project with brainstorm enabled, MaxOpenProposals unset (0).
	// Seed 9 open proposal-labelled issues (below new default 10).
	// Run scan; expect brainstorm NOT cap-skipped (enqueues).
	// Then seed 10; expect cap-skip.
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `mise exec -- go test ./internal/controller/ -run DefaultProposalCapIsTen -v`
Expected: FAIL (default is 5, so 9 already over -> cap-skips).

- [ ] **Step 3: Change the fallbacks**

In `internal/controller/projectscan.go`, both `brainstorm` (`@838-840`) and `healthCheck` (`@956-957`):

```go
	maxProp := act.MaxOpenProposals
	if maxProp < 1 {
		maxProp = 10
	}
```

- [ ] **Step 4: Run to verify pass**

Run: `mise exec -- go test ./internal/controller/ -run DefaultProposalCapIsTen -v` (PASS), then `mise exec -- go test ./...`.

- [ ] **Step 5: Commit**

```bash
git add internal/controller/projectscan.go internal/controller/projectscan_brainstorm_project_test.go
git commit -m "feat: default maxOpenProposals 5 -> 10"
```

---

## Task O3: Operator - brainstorm prompt: new ideas only + early-exit

Repo: `tatara-operator`, same branch (after O2).

**Files:**
- Modify: `internal/controller/projectscan.go` (`brainstormGoalProject`@1052-1088)
- Test: `internal/controller/` (a goal-text test; create `projectscan_brainstorm_goal_test.go` if none asserts goal text)

**Interfaces:**
- Consumes: agent has MCP tool `skip_brainstorm{reason}` (C1). Goal text must instruct calling it for early-exit.

- [ ] **Step 1: Write the failing test**

```go
func TestBrainstormGoalDropsCommentPathAddsEarlyExit(t *testing.T) {
	g := brainstormGoalProject([]string{"o/a", "o/b"}, "STATE")
	if strings.Contains(g, "comment_on_issue") {
		t.Fatal("brainstorm goal must NOT instruct comment_on_issue (path-2 dropped)")
	}
	if !strings.Contains(g, "skip_brainstorm") {
		t.Fatal("brainstorm goal must instruct skip_brainstorm early-exit")
	}
	if !strings.Contains(g, "propose_issue") {
		t.Fatal("brainstorm goal must keep propose_issue path")
	}
	// Proposal must decompose into sub-problems and offer options per piece.
	for _, want := range []string{"sub-problem", "OPTIONS", "recommended"} {
		if !strings.Contains(g, want) {
			t.Fatalf("brainstorm goal must require decomposition+options; missing %q", want)
		}
	}
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `mise exec -- go test ./internal/controller/ -run BrainstormGoalDrops -v`
Expected: FAIL (current text contains `comment_on_issue`, lacks `skip_brainstorm`).

- [ ] **Step 3: Rewrite the goal**

Replace the DEDUP RULE / ACTION RULE block in `brainstormGoalProject` (`@1060-1087`) with a propose-or-skip contract. New return value:

```go
	return "Invoke the `tatara-deep-research` skill to survey the ENTIRE project and identify the highest-leverage " +
		"discovery or improvement opportunity across ALL repositories: " + repoList + ". " +
		"The skill defines how to research via the tatara-memory graph and on-disk code, score leverage, and dedup. " +
		"Run at MAXIMUM reasoning effort. " +
		"\n\n" + stateBlock + "\n\n" +
		"EARLY EXIT (do this FIRST, cheaply): before dispatching the per-repo deep-research fan-out, do a quick scan of " +
		"the ISSUES / OPEN MRs / MAIN HEALTH state above. If nothing clears the bar for a genuinely novel, high-leverage " +
		"NEW proposal this cycle, call `skip_brainstorm(reason)` and STOP. Do NOT run the expensive fan-out just to conclude " +
		"there is nothing to propose.\n\n" +
		"OTHERWISE decompose the survey: dispatch one parallel subagent per repository (use the Agent/Workflow tools to fan " +
		"out, then synthesize their findings into one systemic conclusion).\n\n" +
		"SYSTEMIC MANDATE: prefer the single highest-leverage systemic opportunity - a pattern spanning >=2 repositories, " +
		"a platform-wide gap (e.g. a missing CI step everywhere), or recurring debt - over a one-repo tweak.\n\n" +
		"NEW-IDEAS-ONLY CONTRACT - this is a discovery cycle for NEW proposals; nursing existing issues is handled " +
		"elsewhere. Follow exactly ONE path:\n" +
		"1. If the best idea DUPLICATES or is merely a sub-aspect of an existing open issue listed above: do NOT propose. " +
		"Finish with a one-line note naming the duplicate (e.g. 'Duplicate of o/repo#N'). Do NOT comment on it.\n" +
		"2. If the idea is genuinely novel AND standalone: call `propose_issue`. Set `repo` to the owning repository. " +
		"The proposal must be self-contained AND give the maintainer granular directional control. Required body shape: " +
		"(a) a one-paragraph problem statement; (b) a DECOMPOSITION of the problem into its smaller constituent " +
		"sub-problems / decision points; (c) for EACH sub-problem, 2-3 concrete implementation OPTIONS, each with a " +
		"one-line tradeoff, and YOUR recommended pick; (d) the maintainer's decision framed as choosing one option per " +
		"sub-problem (approve the recommended set, pick alternatives, or comment to refine). Every choice MUST come with " +
		"concrete options and a recommendation - do NOT produce a flat list of open questions.\n\n" +
		"ACTION RULE: a one-repo improvement emits exactly ONE propose_issue. A genuinely systemic improvement MAY emit one " +
		"propose_issue per affected repository (bounded: at most 6), all sharing a single `systemicId` string you generate. " +
		"State which path and scope you chose before executing."
```

- [ ] **Step 4: Run to verify pass**

Run: `mise exec -- go test ./internal/controller/ -run BrainstormGoalDrops -v` (PASS), then `mise exec -- go test ./...`. Update any existing goal-text test that asserted the old path-2 wording.

- [ ] **Step 5: Code review, then commit**

```bash
git add internal/controller/projectscan.go internal/controller/projectscan_brainstorm_goal_test.go
git commit -m "feat: brainstorm prompt - new ideas only (drop comment path) + early-exit"
```

---

## Task O4: Operator - `brainstorm-outcome` endpoint + status + writeback

Repo: `tatara-operator`. May run in parallel with O1-O3 (different files). If using one branch, rebase/order after O1-O3 to avoid churn; if a separate branch `feat/brainstorm-outcome`, no file overlap with O1-O3 except none (api/task_types.go, restapi, writeback.go are untouched by O1-O3).

**Files:**
- Modify: `api/v1alpha1/task_types.go` (add `BrainstormOutcome` type ~after `ImplementOutcome`@66; add `Status.BrainstormOutcome` field ~after @206)
- Modify: `internal/restapi/handlers.go` (add `brainstormOutcomeReq` + `brainstormOutcome` handler after implementOutcome@890)
- Modify: `internal/restapi/server.go` (route after @79)
- Modify: `internal/controller/writeback.go` (brainstorm case @48-56)
- Generated: CRD manifests (`make manifests generate`)
- Test: `internal/restapi/` handler test; `internal/controller/` writeback test

**Interfaces:**
- Consumes: cli `skip_brainstorm` POSTs `/tasks/{t}/brainstorm-outcome` `{"action":"none","reason":...}` (C1).
- Produces: `Task.Status.BrainstormOutcome *BrainstormOutcome{Action string; Reason string}`; writeback maps `Action=="none"` to `BrainstormComplete` with the reason.

- [ ] **Step 1: Write the failing tests**

Restapi handler test (mirror the implement-outcome handler test) in `internal/restapi/`:

```go
func TestBrainstormOutcomeRecordsNone(t *testing.T) {
	// brainstorm-kind Task; POST {"action":"none","reason":"nothing novel"}.
	// Expect 200 and Task.Status.BrainstormOutcome == {none, "nothing novel"}.
}
func TestBrainstormOutcomeRejectsEmptyReason(t *testing.T) {
	// POST {"action":"none"} -> 400.
}
func TestBrainstormOutcomeRejectsNonBrainstormTask(t *testing.T) {
	// issueLifecycle Task -> 409.
}
```

Writeback test in `internal/controller/`:

```go
func TestWriteBackBrainstormNoneIsComplete(t *testing.T) {
	// brainstorm Task, no proposal child, Status.BrainstormOutcome={none, "x"}.
	// writeBack -> WritebackPending cleared with reason "BrainstormComplete".
}
```

- [ ] **Step 2: Run to verify they fail**

Run: `mise exec -- go test ./internal/restapi/ ./internal/controller/ -run BrainstormOutcome -v`
Expected: FAIL (type/handler/route absent).

- [ ] **Step 3: Add the API type + status field**

In `api/v1alpha1/task_types.go`, after `ImplementOutcome` (`@66`):

```go
// BrainstormOutcome is the agent's declared outcome for a brainstorm task when
// it files no proposal (a deliberate early-exit). Mirrors ImplementOutcome.
type BrainstormOutcome struct {
	// +kubebuilder:validation:Enum=none
	Action string `json:"action"`
	Reason string `json:"reason"` // required; why nothing was proposed
}
```

In the Status struct, after `ImplementOutcome` (`@206`):

```go
	// +optional
	BrainstormOutcome *BrainstormOutcome `json:"brainstormOutcome,omitempty"`
```

- [ ] **Step 4: Add the REST handler + route**

In `internal/restapi/handlers.go`, after `implementOutcome` (`@890`):

```go
// --- POST /tasks/{t}/brainstorm-outcome ---

type brainstormOutcomeReq struct {
	Action string `json:"action"`
	Reason string `json:"reason"`
}

func (s *Server) brainstormOutcome(w http.ResponseWriter, r *http.Request) {
	var req brainstormOutcomeReq
	if err := decodeJSON(r, w, &req); err != nil {
		writeDecodeError(w, r, err)
		return
	}
	if req.Action != "none" {
		writeError(w, http.StatusBadRequest, "action must be none")
		return
	}
	if strings.TrimSpace(req.Reason) == "" {
		writeError(w, http.StatusBadRequest, "reason required")
		return
	}
	key := client.ObjectKey{Namespace: s.ns, Name: chi.URLParam(r, "t")}
	var t tatarav1alpha1.Task
	if err := s.c.Get(r.Context(), key, &t); err != nil {
		writeClientErr(w, err)
		return
	}
	if !authorizeForTask(w, r, &t) {
		return
	}
	if t.Spec.Kind != "brainstorm" {
		writeError(w, http.StatusConflict, "brainstorm outcome only applies to a brainstorm task")
		return
	}
	start := time.Now()
	if err := retry.RetryOnConflict(retry.DefaultRetry, func() error {
		if gerr := s.c.Get(r.Context(), key, &t); gerr != nil {
			return gerr
		}
		t.Status.BrainstormOutcome = &tatarav1alpha1.BrainstormOutcome{Action: req.Action, Reason: req.Reason}
		return s.c.Status().Update(r.Context(), &t)
	}); err != nil {
		if s.metrics != nil {
			s.metrics.RecordRESTRequest("brainstorm_outcome", "error", time.Since(start).Seconds())
		}
		writeClientErr(w, err)
		return
	}
	elapsed := time.Since(start)
	if s.metrics != nil {
		s.metrics.RecordRESTRequest("brainstorm_outcome", "ok", elapsed.Seconds())
	}
	s.log.InfoContext(r.Context(), "restapi: brainstormOutcome",
		append(reqLogFields(r),
			"action", "brainstorm_outcome",
			"resource_id", key.Name,
			"duration_ms", elapsed.Milliseconds())...)
	writeJSON(w, http.StatusOK, toTaskDTO(t))
}
```

In `internal/restapi/server.go`, after line 79:

```go
	r.Post("/tasks/{t}/brainstorm-outcome", s.brainstormOutcome)
```

- [ ] **Step 5: Wire writeback to surface the reason**

In `internal/controller/writeback.go`, brainstorm case (`@48-56`), make the no-proposal reason explicit:

```go
	case "brainstorm":
		// Brainstorm proposals are created via propose_issue which spawns child
		// Tasks. The brainstorm Task itself never opens a PR.
		if r.brainstormHasProposal(ctx, task) {
			return ctrl.Result{}, r.clearWritebackPending(ctx, task, "BrainstormProposed", "brainstorm proposals created via propose_issue; no PR to open")
		}
		reason := "brainstorm finished with no proposal filed via propose_issue"
		if o := task.Status.BrainstormOutcome; o != nil && o.Action == "none" && strings.TrimSpace(o.Reason) != "" {
			reason = "early-exit: " + o.Reason
		}
		return ctrl.Result{}, r.clearWritebackPending(ctx, task, "BrainstormComplete", reason)
```

(Add `strings` import if not present in writeback.go.)

- [ ] **Step 6: Regenerate manifests + run tests**

Run: `mise exec -- make manifests generate` (regenerates deepcopy + CRD yaml under `config/crd` and the chart `crd-bases`/`templates`). Then:
Run: `mise exec -- go test ./internal/restapi/ ./internal/controller/ -run BrainstormOutcome -v` (PASS), then `mise exec -- go test ./... && mise run lint`.

- [ ] **Step 7: Code review, then commit**

```bash
git add api/v1alpha1/task_types.go internal/restapi/handlers.go internal/restapi/server.go internal/controller/writeback.go config/ charts/ internal/restapi/*_test.go internal/controller/*_test.go
git commit -m "feat: brainstorm-outcome endpoint + status field for skip_brainstorm early-exit"
```

---

## Task W1: tatara-claude-code-wrapper - bump cli pin

Repo: `tatara-claude-code-wrapper`. Branch: `chore/bump-cli-skip-brainstorm` off fresh `main`. PRECONDITION: C1 merged to tatara-cli `main` and its CI image/tag built.

**Files:**
- Modify: the `TATARA_CLI_VERSION` pin (Dockerfile ARG or `.mise.toml` / build var - locate it).

- [ ] **Step 1: Find the current pin**

Run: `grep -rn "TATARA_CLI_VERSION\|tatara-cli" Dockerfile .mise.toml 2>/dev/null`
Identify the pinned cli version/tag.

- [ ] **Step 2: Bump to the cli version shipping `skip_brainstorm`**

Set the pin to the tatara-cli `main` commit/tag built after C1 merged (cut forward of the deployed tag, per the wrapper-cli-pin contract).

- [ ] **Step 3: Verify the build guard passes**

The wrapper image build runs a guard that `tatara mcp` tools/list (tokenless) succeeds. Locally or via the repo task, confirm the new cli builds and lists tools without a token (the guard only runs on main-push, not PR, so verify before merge):

Run: `mise run build` (or the repo's documented build) and confirm no guard failure; spot-check `skip_brainstorm` is served.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile .mise.toml
git commit -m "chore: bump TATARA_CLI_VERSION for skip_brainstorm tool"
```

---

## Task H1: tatara-helmfile - deploy operator + wrapper, set maxOpenProposals=10

Repo: `tatara-helmfile`. Branch: `feat/brainstorm-throughput-deploy` off fresh `main`. PRECONDITION: operator `main` image built (O1-O4 merged), wrapper `main` image built (W1 merged).

**Files:**
- Modify: operator release chart version + pinned `image.tag` (per the operator-deploy memory: BOTH).
- Modify: wrapper image tag used by the agent (Project `spec.agent.image`, currently `...:26d4450`).
- Modify: per-project values setting `cron.brainstorm.maxOpenProposals: 10` for the `tatara` and `infrastructure` Projects (locate the repositories/project values files, e.g. `values/.../*-pre.yaml`).

- [ ] **Step 1: Locate the values**

Run: `grep -rn "maxOpenProposals\|image:\|chart\|version:" values/ helmfile* 2>/dev/null | grep -iE "operator|maxOpenProposals|agent" | head -40`
Identify operator chart version + image.tag keys, the agent image pin, and where `maxOpenProposals` is set per project.

- [ ] **Step 2: Apply the edits**

- Bump operator chart `version` and operator `image.tag` to the new `main` build.
- Bump the agent image tag (wrapper) to the new wrapper `main` build.
- Set `maxOpenProposals: 10` for both `tatara` and `infrastructure` projects (raising tatara 8->10, infrastructure 3->10).

- [ ] **Step 3: Diff before apply**

Run: `helmfile diff` (or the repo task). Expected diff: operator Deployment image + chart, agent image on both Projects, and `maxOpenProposals` 8->10 / 3->10. Confirm the operator CRD diff includes the new `brainstormOutcome` status field (templated CRD). If apply fails on CRD ownership, do the one-time helm-ownership relabel per the operator-crd-templating memory.

- [ ] **Step 4: Commit + open MR (auto-merge on green pipeline)**

```bash
git add values/ helmfile*
git commit -m "feat: brainstorm throughput - operator+wrapper bump, maxOpenProposals=10"
```
Open the MR; the in-cluster ARC pipeline applies on merge. Do not apply by hand.

- [ ] **Step 5: Verify live (verification-before-completion)**

After the pipeline applies, confirm: operator pod on the new image; `kubectl get project tatara -o jsonpath='{.spec.scm.cron.brainstorm.maxOpenProposals}'` == 10; over the next 1-2 hours, brainstorm `tatara_scan_tasks_created_total{activity="brainstorm"}` increases and no `skipped_budget`; Loki shows `skip_brainstorm` early-exits and/or new proposals. Use the Grafana MCP (Prometheus uid `prometheus`, Loki uid `efihqbqlmroqod`).

---

## Self-Review (completed)

- **Spec coverage:** queue cap removal -> O1; maxOpenProposals 10 (code) -> O2, (existing projects) -> H1; brainstorm new-ideas-only -> O3; early-exit tool -> C1, endpoint/status/writeback -> O4, prompt -> O3; wrapper pin -> W1; deploy -> H1. All spec sections mapped.
- **Placeholders:** none; code shown for every code step. The two `grep`/locate steps (W1.1, H1.1) are discovery steps for repo-specific paths, not deferred work.
- **Type consistency:** `BrainstormOutcome{Action,Reason}` defined in O4 used by writeback (O4) and posted by `skip_brainstorm` (C1) with `action:"none"`; route `/tasks/{t}/brainstorm-outcome` matches between C1 and O4; `maxProp=10` default matches O2 + H1 value.
