# Issue-lifecycle M0 - lifecycle model + skeleton - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce the `issueLifecycle` Task kind, its `Status.LifecycleState`
machine fields, the binders that create it, and a `reconcileLifecycle` skeleton
that wires the Triage and Implement states to the existing agent-run machinery -
deployable but inert beyond triage+implement.

**Architecture:** Per spec `docs/superpowers/specs/2026-06-12-issue-lifecycle-agent-design.md`.
The lifecycle reconcile dispatches on `Status.LifecycleState`, reusing
`ensurePodAndService`/`driveTurns` for agent-run states and transitioning state
on the agent's terminal decision. M0 lands the model + Triage + Implement -> MRCI
handoff (MRCI/Merge/MainCI are M1).

**Tech stack:** Go (controller-runtime, kubebuilder), envtest, table-driven tests.

**Repo:** `tatara-operator` only. Branch off fresh `main` in a worktree.

---

## File structure

- `api/v1alpha1/task_types.go` - new `TaskStatus` fields (lifecycle*).
- `api/v1alpha1/project_types.go` - new `AgentSpec`/`ScmSpec` fields.
- `api/v1alpha1/zz_generated.deepcopy.go` - regenerated.
- `config/crd/bases/*.yaml` + `charts/.../crds/*.yaml` - regenerated CRDs.
- `internal/controller/lifecycle.go` - NEW: `reconcileLifecycle` + state handlers + transition helper.
- `internal/controller/lifecycle_test.go` - NEW: table-driven state tests.
- `internal/controller/task_controller.go` - dispatch `issueLifecycle` Kind to `reconcileLifecycle`; per-kind inflight gauge includes `issueLifecycle`.
- `internal/controller/projectscan.go` - `issueScan` creates `issueLifecycle` (Triage) instead of `triageIssue`; `mrScan` bot-PR creates `issueLifecycle` (MRCI entry) instead of `selfImprove`. (MRCI handler is M1; M0 sets the entry state + dedup.)
- `internal/webhook/server.go` - labeled-issue path creates `issueLifecycle`; bot-PR path creates `issueLifecycle` (MRCI entry).
- `internal/obs/metrics.go` - lifecycle gauges/counters scaffolding.

Per spec, the standalone `triageIssue`/`selfImprove` Task-creation is replaced;
their writeback logic is reused by the lifecycle handlers (folded in M0/M1, not
duplicated). Keep `review`/`brainstorm` arms.

---

### Task 1: Task lifecycle status fields

**Files:** Modify `api/v1alpha1/task_types.go`; Test `api/v1alpha1/task_types_test.go` (or an envtest CRUD test in controller suite).

- [ ] **Step 1: Write the failing test.** A test that constructs a `Task`, sets each new status field (`LifecycleState="Triage"`, `LastActivityAt`, `DeadlineAt`, `HeadBranch`, `PRNumber`, `MergeCommitSHA`, `CumulativeTokens`, `LastTurnInputTokens`, `LifecycleIterations`, `Handover`), round-trips it through the fake client `Status().Update`/`Get`, and asserts the values persist. Also assert the `Kind` enum accepts `issueLifecycle`.
- [ ] **Step 2: Run it - expect FAIL** (fields/enum value absent).
- [ ] **Step 3: Add the fields** to `TaskStatus` with kubebuilder markers exactly as the spec's CRD section: `lifecycleState` (enum `Triage;Conversation;Implement;MRCI;Merge;MainCI;Done;Stopped;Parked`), `lastActivityAt`/`deadlineAt` (`*metav1.Time`), `headBranch`/`mergeCommitSHA`/`handover` (string), `prNumber`/`lifecycleIterations` (int), `cumulativeTokens`/`lastTurnInputTokens` (int64). Add `issueLifecycle` to the `Kind` enum marker on `TaskSpec.Kind`.
- [ ] **Step 4: `make manifests generate`** (regenerate CRDs + deepcopy). Run the test - expect PASS.
- [ ] **Step 5: Commit** `feat(api): Task lifecycle status fields + issueLifecycle kind`.

### Task 2: Project agent/scm lifecycle config fields

**Files:** Modify `api/v1alpha1/project_types.go`; Test alongside Task 1's suite.

- [ ] **Step 1: Failing test** asserting a `Project` accepts `spec.agent.contextWindowTokens`, `spec.agent.handoverThresholdPercent`, `spec.agent.maxLifecycleIterations`, `spec.scm.babysitDeadlineMinutes`, `spec.scm.conversationIdleMinutes` and that unset values default per the kubebuilder markers (200000 / 50 / 10 / 60 / 60).
- [ ] **Step 2: Run - FAIL.**
- [ ] **Step 3: Add the fields** with `+kubebuilder:default=` markers exactly per spec. (`AgentSpec` already has `MaxTurnsPerTask`, `TurnTimeoutSeconds`; add alongside. `ScmSpec` already has `MergePolicy` etc.)
- [ ] **Step 4: `make manifests generate`; test PASS.**
- [ ] **Step 5: Commit** `feat(api): Project lifecycle config (context window, deadlines, idle)`.

### Task 3: Lifecycle transition helper + metrics

**Files:** Create `internal/controller/lifecycle.go`; Modify `internal/obs/metrics.go`; Test `internal/controller/lifecycle_test.go`, `internal/obs/metrics_test.go`.

- [ ] **Step 1: Failing test** for a `setLifecycleState(ctx, task, to, reason)` helper: it must `RetryOnConflict`-update `Status.LifecycleState`, emit an INFO log `action=lifecycle_transition` with `from`/`to`, and increment `tatara_lifecycle_transition_total{from,to}`. Assert the field changes and the counter increments (use a test registry).
- [ ] **Step 2: Run - FAIL.**
- [ ] **Step 3: Implement** `setLifecycleState` (RetryOnConflict re-Get + status update, like `clearWritebackPending`) and the metrics: gauges `tatara_lifecycle_state{state}`, counters `tatara_lifecycle_transition_total{from,to}`, `tatara_lifecycle_handover_total`, `tatara_lifecycle_giveup_total{reason}`, histograms `tatara_mrci_wait_seconds`, `tatara_lifecycle_seconds`. Add the obs methods.
- [ ] **Step 4: Test PASS.**
- [ ] **Step 5: Commit** `feat(controller): lifecycle transition helper + metrics`.

### Task 4: reconcileLifecycle skeleton dispatch

**Files:** Modify `internal/controller/task_controller.go`, `internal/controller/lifecycle.go`; Test `lifecycle_test.go`.

- [ ] **Step 1: Failing test** that a `Task` with `Kind=issueLifecycle` and empty `LifecycleState` is initialized to `Triage` by `reconcileLifecycle`, and that an unknown `LifecycleState` returns an error (defensive). Drive via the reconciler with envtest fakes.
- [ ] **Step 2: Run - FAIL.**
- [ ] **Step 3: Implement.** In `Reconcile`, after the existing gates (memory, concurrency, approval) and before the generic spawn, add: `if task.Spec.Kind == "issueLifecycle" { return r.reconcileLifecycle(ctx, &project, &task) }`. Implement `reconcileLifecycle` switching on `Status.LifecycleState`: empty -> set `Triage`; `Triage`/`Implement` -> the agent-run handlers (Task 5/6); `MRCI`/`Merge`/`MainCI`/`Conversation` -> a stub returning `RequeueAfter: pollRequeue` with a `// M1/M2` marker (NOT an error); `Done`/`Stopped`/`Parked` -> `ctrl.Result{}, nil`; default -> error. Update `updateInflightGauge` known-kinds list to include `issueLifecycle`.
- [ ] **Step 4: Test PASS.**
- [ ] **Step 5: Commit** `feat(controller): reconcileLifecycle skeleton dispatch`.

### Task 5: Triage state (reuse the triage agent-run + issue_outcome)

**Files:** Modify `internal/controller/lifecycle.go`; Test `lifecycle_test.go`.

- [ ] **Step 1: Failing tests** (table) for the Triage handler driving an agent run via the existing pod/turn machinery to a terminal result carrying an `IssueOutcome`:
  - `IssueOutcome.Action=close` -> `CloseIssue` called with the comment, transition to `Done`.
  - `IssueOutcome.Action=discuss` -> `Comment` posts the questions, transition to `Conversation`, set `DeadlineAt = now + conversationIdleMinutes`.
  - `IssueOutcome.Action=implement` -> transition to `Implement` (no SCM write).
  Use the fake `SCMWriter` to assert the right egress; assert the state field after.
- [ ] **Step 2: Run - FAIL.**
- [ ] **Step 3: Implement** the Triage handler: ensure pod + drive turns (reuse `ensurePodAndService`/`driveTurns`); on the run's terminal (the agent called `issue_outcome`, recorded in `Status.IssueOutcome`), tear down the wrapper session/pod (reuse the cleanup half of `terminate` without setting a terminal Task phase - extract a `teardownAgentRun` helper), then branch on `IssueOutcome.Action` exactly as the tests require, using `setLifecycleState`. The close path reuses `writeBackIssue`'s CloseIssue logic; the discuss path reuses `writer.Comment`.
- [ ] **Step 4: Test PASS.**
- [ ] **Step 5: Commit** `feat(controller): lifecycle Triage state`.

### Task 6: Implement state -> open MR -> MRCI handoff

**Files:** Modify `internal/controller/lifecycle.go`; Test `lifecycle_test.go`.

- [ ] **Step 1: Failing test:** the Implement handler drives an agent run, then on terminal opens the MR via the existing `writeBackOpenChange` egress, records `PrURL`/`prNumber`/`headBranch`, and transitions to `MRCI`. Assert `OpenChange` called with `taskBranch(task)` and the default branch, `prNumber` parsed, state == `MRCI`.
- [ ] **Step 2: Run - FAIL.**
- [ ] **Step 3: Implement** the Implement handler: ensure pod + drive turns (the existing plan->subtask flow, agent pushes `taskBranch`); on terminal, `teardownAgentRun`, call the shared open-change logic (refactor `writeBackOpenChange` so the lifecycle can call it and read back the opened PR URL/number), set `Status.PrURL`/`prNumber`/`headBranch`, increment `lifecycleIterations`, `setLifecycleState(MRCI)`. (M0: the MR body is the existing `writeBackBody`; M4 enriches it with scope. The `Closes #N` link is added in M1/M4 - M0 keeps the existing body.)
- [ ] **Step 4: Test PASS.**
- [ ] **Step 5: Commit** `feat(controller): lifecycle Implement state opens MR, enters MRCI`.

### Task 7: Binders create issueLifecycle (issueScan + labeled-issue webhook)

**Files:** Modify `internal/controller/projectscan.go`, `internal/webhook/server.go`; Test `projectscan_test.go`, `server_test.go`.

- [ ] **Step 1: Failing tests:**
  - `issueScan` over an eligible open issue creates a Task with `Kind=issueLifecycle` (entry state empty -> Triage), labels `source-repo`/`source-number`/`source-kind=issueLifecycle`; lane occupancy counts `issueLifecycle` (replacing `triageIssue` in the kinds arg).
  - A labeled-issue webhook (`triggerLabel` applied) creates `issueLifecycle` with `Status.LifecycleState=Implement` (the spec's "issue carrying triggerLabel -> enter at Implement").
  - Dedup: a non-terminal `issueLifecycle` Task for `(repo, number)` suppresses re-creation; `LifecycleState in {Done,Stopped,Parked}` frees it.
- [ ] **Step 2: Run - FAIL.**
- [ ] **Step 3: Implement.** In `projectscan.go`, change the issueScan Task kind to `issueLifecycle`, update `laneOccupancy(..., "issueLifecycle")`, and the dedup terminal check to include lifecycle terminals. In `server.go`, the labeled-issue branch creates `issueLifecycle` and sets the initial `LifecycleState=Implement` when the trigger label is present at creation (else Triage). Keep the existing per-`(repo,number)` dedup labels.
- [ ] **Step 4: Test PASS.**
- [ ] **Step 5: Commit** `feat(controller): binders create issueLifecycle tasks`.

### Task 8: Binder creates issueLifecycle MRCI-entry for bot PRs (replaces selfImprove)

**Files:** Modify `internal/controller/projectscan.go`, `internal/webhook/server.go`; Test as above.

- [ ] **Step 1: Failing tests:** `mrScan` (and the `pull_request` bot-authored webhook) for a bot-authored open PR creates a Task `Kind=issueLifecycle`, `Status.LifecycleState=MRCI`, `prNumber`/`PrURL` set, keyed to the linked issue number if the PR body has `Closes #N` else to the PR number. Human-authored PRs still create `review`. Lane occupancy for mrScan counts `issueLifecycle` (MRCI) + `review`.
- [ ] **Step 2: Run - FAIL.**
- [ ] **Step 3: Implement.** Replace the bot-PR `selfImprove` creation with `issueLifecycle` MRCI-entry creation (set `LifecycleState=MRCI`, `prNumber`, `PrURL`). Reuse the existing authorship determination (`GetPRState.Author == botLogin`). Parse `Closes #N` from the PR body for the dedup key (best-effort; fall back to PR number). Keep `review` for human PRs. Update `laneOccupancy(..., "issueLifecycle", "review")`.
- [ ] **Step 4: Test PASS.**
- [ ] **Step 5: Commit** `feat(controller): bot-PR binder creates issueLifecycle MRCI entry`.

### Task 9: Remove dead single-shot triageIssue/selfImprove creation; keep shared logic

**Files:** `internal/controller/writeback.go`, `task_controller.go`, tests.

- [ ] **Step 1: Failing/again-green test sweep.** Confirm no binder creates `triageIssue` or `selfImprove` anymore (grep + a test asserting issueScan/mrScan kinds). The authorship pre-spawn gate in `task_controller.go` (`Kind=="selfImprove"`) is repurposed: the MRCI-entry lifecycle Task's authorship is verified at MRCI handling (M1) instead - for M0, move the existing `selfImproveBotAuthored` gate to also accept `issueLifecycle` MRCI entries, or defer to M1. Keep `writeBackSelfImprove`/`writeBackIssue` as reusable helpers (called by lifecycle handlers), not as kind-dispatch arms that are now unreachable - delete the unreachable `doWriteBack` arms for `triageIssue`/`selfImprove` only if nothing else calls them, else leave a clear comment. NO dead code (hard rule 4): either reused or removed.
- [ ] **Step 2: Run the full suite - PASS** (`make test`).
- [ ] **Step 3: `gofmt`, `golangci-lint run`, `helm lint charts/*` - clean.**
- [ ] **Step 4: Commit** `refactor(controller): retire standalone triageIssue/selfImprove creation`.

---

## Self-review checklist (run before requesting review)

- [ ] Every new status/spec field has a kubebuilder marker matching the spec.
- [ ] CRDs + deepcopy regenerated (`make manifests generate`); chart `crds/` updated.
- [ ] `reconcileLifecycle` handles every enum value (no fallthrough panic); M1/M2 stubs requeue, do not error.
- [ ] No binder creates `triageIssue`/`selfImprove`; no unreachable dead code.
- [ ] `updateInflightGauge` known-kinds includes `issueLifecycle`.
- [ ] Full suite green with `-race`; gofmt/golangci/helm-lint clean.
