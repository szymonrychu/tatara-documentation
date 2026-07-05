# Task Work-Item Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Task CR a Project-level first-class tracker carrying a work-item ledger (N issues + N MRs + proposals) that is the single source of truth for dedup, stall recovery, brainstorm triage, and agent prompt-building - removing the three `tatara.io` dedup labels.

**Architecture:** Add `Status.WorkItems []WorkItemRef` + `Status.ParkReason` + `Spec.Source.DedupNumber` to the Task CRD. Agents maintain the ledger via the MCP tools they already have (operator projects each action). A two-tier cron backstop silently syncs missed SCM drift and only spawns an agent when work is needed. Dedup and the webhook binder move from labels to spec/ledger identity at project scope.

**Tech Stack:** Go (controller-runtime / kubebuilder), tatara-operator. Tests: `mise run test` (envtest + table-driven unit tests).

## Global Constraints

- Newest stable Go; pin the exact minor in `go.mod` (do not change it).
- KISS. No tech-debt; if a thing is complex, note rationale in `MEMORY.md`.
- JSON logs only via stdlib `log/slog`; never `slog.Default()` for request-scoped logs - use the injected logger.
- Charts cluster-agnostic; no values.yaml lists/plain-env (N/A for this change except the CRD bump).
- Operator CRDs are templated (`crd-bases/` + `templates/crds.yaml`); regenerate with `make manifests generate` after any api change.
- Operator chart RBAC is hand-maintained in `templates/rbac.yaml`; this change adds no new watched kind, so no RBAC edit expected - verify.
- Conventional commits ending with the `Claude-Session` trailer. `pre-commit run --all-files` (gofmt + golangci-lint) green before every commit. `gofmt` and `golangci-lint` must pass; wrap errors `fmt.Errorf("ctx: %w", err)`; table-driven tests with `t.Run`.
- Deploy ONLY via tatara-helmfile (dual chart-pin `0.0.0-g<sha>` + image tag). Chart CI uses the `g`-prefixed version (leading-zero-SHA bug).

## File structure

New:
- `api/v1alpha1/workitem_types.go` - `WorkItemRef` type, role/kind/state constants, ledger helper methods on `*Task`.
- `internal/controller/ledger.go` - operator-side ledger projection helpers (upsert from agent actions, seed/migrate, backlog count, repos-in-scope, identity match).
- `internal/controller/backstop.go` - the two-tier cron reconciliation backstop (refresh + close-obsolete + reactivate), extracted so it is testable in isolation.

Modified:
- `api/v1alpha1/task_types.go` - add `Status.WorkItems`, `Status.ParkReason`, `Spec.Source.DedupNumber`; demote `Spec.RepositoryRef` doc to "optional primary-repo hint".
- `api/v1alpha1/annotations.go` - delete `LabelSourceRepo`, `LabelSourceNumber`, `LabelHeadSHA` after all references are migrated (keep `LabelSourceKind`, `LabelActivity`, `LabelIsPR`, `LabelAlertGroup`).
- `api/v1alpha1/zz_generated.deepcopy.go` - regenerated.
- `internal/controller/projectscan.go` - `isDeduped`, `priorTerminalAttempts`, `hasLiveLifecycleTaskForIssue`, `scanTaskLabels`, `proposalBacklogCount`, brainstorm projection; call the backstop from `runScans`.
- `internal/controller/lifecycle.go` - `setLifecycleState` persists/clears `ParkReason`; lifecycle-label and close paths project onto the ledger.
- `internal/controller/writeback.go` - opening a PR upserts a `role:openedPR` ledger entry.
- `internal/controller/labels.go` - `setLifecycleLabel` reflects the projected proposal state onto the ledger entry.
- `internal/webhook/server.go` - replace the `MatchingLabels{source-repo,source-number}` dedup with deterministic-name + in-memory project-scoped match; seed `Spec.Source.DedupNumber`; stop writing the three labels.
- `internal/agent/pod.go` - derive clone scope (ReposInScope) and prompt context from the ledger.
- `crd-bases/` + `templates/crds.yaml` (operator chart) - regenerated CRD.

---

## Phase 1 - Data model + lazy-seed migration + drop label writes

### Task 1: `WorkItemRef` type + constants

**Files:**
- Create: `api/v1alpha1/workitem_types.go`
- Test: `api/v1alpha1/workitem_types_test.go`

**Interfaces:**
- Produces: `WorkItemRef` struct; role consts `RoleProposed="proposed"`, `RoleSource="source"`, `RoleCloses="closes"`, `RoleOpenedPR="openedPR"`, `RoleReviewed="reviewed"`; kind consts `WorkItemIssue="issue"`, `WorkItemPR="pr"`; state consts `WIProposed/Approved/Declined/Implemented/Open/Closed/Merged`.

- [ ] **Step 1: Write the failing test** (`workitem_types_test.go`): assert the constants have the exact string values above and that `WorkItemRef` zero value marshals with omitempty (no `number`/`state`/`headSHA` keys when empty).
- [ ] **Step 2: Run `go test ./api/v1alpha1/ -run WorkItem` - expect FAIL (undefined).**
- [ ] **Step 3: Implement** the struct (exact fields/JSON tags from the spec) + const blocks.
- [ ] **Step 4: Run the test - expect PASS.**
- [ ] **Step 5: Commit** `feat(api): add WorkItemRef type + ledger constants`.

### Task 2: Status/Spec field additions + deepcopy

**Files:**
- Modify: `api/v1alpha1/task_types.go` (add `WorkItems []WorkItemRef`, `ParkReason string` to `TaskStatus`; `DedupNumber int` to `TaskSource`)
- Modify: `api/v1alpha1/zz_generated.deepcopy.go` (regenerate)
- Test: `api/v1alpha1/types_fields_test.go` (extend existing field test)

**Interfaces:**
- Produces: `task.Status.WorkItems`, `task.Status.ParkReason`, `task.Spec.Source.DedupNumber`.

- [ ] **Step 1: Write failing test** asserting a round-trip Task with two `WorkItems`, a `ParkReason`, and `Source.DedupNumber` deep-copies equal (`in.DeepCopy()` then `reflect.DeepEqual`).
- [ ] **Step 2: Run - expect FAIL (field undefined).**
- [ ] **Step 3: Add the fields** with kubebuilder markers (`+optional`; no enum on WorkItems). Run `make generate` to regenerate deepcopy. Re-add the `Status.RepositoryRef`/`Spec.RepositoryRef` doc comment noting "optional primary-repo hint; clone scope derived from Status.WorkItems".
- [ ] **Step 4: Run `make generate manifests` then the test - expect PASS.**
- [ ] **Step 5: Commit** `feat(api): add Status.WorkItems/ParkReason + Source.DedupNumber`.

### Task 3: Ledger helper methods (upsert, match, repos, seed)

**Files:**
- Create: `internal/controller/ledger.go`
- Test: `internal/controller/ledger_test.go`

**Interfaces:**
- Produces:
  - `func UpsertWorkItem(t *tatarav1alpha1.Task, ref tatarav1alpha1.WorkItemRef)` - idempotent by (`Repo`,`Number`,`Kind`); updates `Role`/`State`/`HeadSHA`/`Title`/`LastRefreshedAt` in place, appends if absent. `Number==0` entries (unfiled proposals) match by (`Repo`,`Title`,`Role`).
  - `func taskMatchesItem(t *tatarav1alpha1.Task, repo string, number int) bool` - true when the Task's seed identity (`Spec.Source`: repo from `IssueRef`, number = `DedupNumber>0 ? DedupNumber : Number`) OR any ledger entry matches (repo, number).
  - `func reposInScope(t *tatarav1alpha1.Task) []string` - sorted distinct `Repo` across ledger + `Spec.Source`.
  - `func seedLedgerFromSpec(t *tatarav1alpha1.Task)` - if `Status.WorkItems` empty, seed from `Spec.Source` (role source/reviewed by IsPR), `SystemicGroup.SameRepoSiblings`/`CrossRepo` (role:closes), and `Status.PRNumber`/`PrURL` (role:openedPR). Idempotent (no-op when non-empty).

- [ ] **Step 1-2: Failing tests (table-driven):** upsert add vs update; match by DedupNumber-as-linked-issue; reposInScope dedup+sort; seed from Spec.Source+SystemicGroup+PRNumber and idempotency on re-call.
- [ ] **Step 3: Implement.** Pure functions over the Task; no client calls.
- [ ] **Step 4: Tests PASS.**
- [ ] **Step 5: Commit** `feat(controller): work-item ledger helpers (upsert/match/seed/repos)`.

### Task 4: Persist `ParkReason` in `setLifecycleState`

**Files:**
- Modify: `internal/controller/lifecycle.go` (`setLifecycleState` - the `RetryOnConflict` closure)
- Test: `internal/controller/lifecycle_parkreason_test.go`

- [ ] **Step 1: Failing test (envtest):** transition a Task to `Parked` with reason `triage-failed` -> `Status.ParkReason=="triage-failed"`; then transition to `Implement` -> `ParkReason==""`.
- [ ] **Step 2: Run - FAIL.**
- [ ] **Step 3: Implement:** in the status-update closure, `if to=="Parked" { fresh.Status.ParkReason = reason } else { fresh.Status.ParkReason = "" }`.
- [ ] **Step 4: PASS.**
- [ ] **Step 5: Commit** `feat(controller): persist Status.ParkReason on Parked transitions`.

### Task 5: Lazy-seed on reconcile + stop writing the 3 labels at creation

**Files:**
- Modify: `internal/controller/projectscan.go` (`scanTaskLabels` at :99-105 - remove the 3 label writes, keep `LabelSourceKind`/`LabelActivity`/`LabelIsPR`)
- Modify: `internal/webhook/server.go` (:622-623, :742-743 label maps - remove source-repo/number/head-sha writes; keep kind/activity/is-pr)
- Modify: `internal/controller/task_controller.go` (`Reconcile`, after fetch) - call `seedLedgerFromSpec(task)` + persist if it changed
- Test: extend `internal/controller/ledger_test.go` + a webhook/scan label test

**Interfaces:**
- Consumes: `seedLedgerFromSpec` (Task 3).

- [ ] **Step 1: Failing tests:** a freshly created scan/webhook Task has NO `tatara.io/source-repo|source-number|head-sha` labels; after one reconcile its `Status.WorkItems` is seeded from `Spec.Source`.
- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implement** the label-write removals + the seed call (guarded: only `Status().Update` when WorkItems was empty and got populated).
- [ ] **Step 4: PASS;** run full `mise run test` (nothing else should break yet - reads still tolerate missing labels because dedup not migrated until Phase 2).
- [ ] **Step 5: Commit** `refactor(controller): seed ledger on reconcile; stop writing source dedup labels`.

> NOTE for executor: Phase 1 leaves the old label *reads* intact (Phase 2 removes them). Between phases, dedup degrades gracefully because new Tasks seed the ledger and old Tasks still carry labels; do not deploy mid-phase.

---

## Phase 2 - Dedup + webhook migrated to project-scoped spec/ledger identity

### Task 6: `isDeduped` reads spec/ledger, not labels

**Files:**
- Modify: `internal/controller/projectscan.go` (`isDeduped` :195-216; signature gains nothing - it already gets `existing`; matching switches to `taskMatchesItem` + head SHA from the `role:openedPR` ledger entry / `Status.MergedHeadSHA`)
- Test: `internal/controller/projectscan_dedup_test.go`

**Interfaces:**
- Consumes: `taskMatchesItem`, ledger helpers.
- Produces: unchanged `isDeduped(c, existing, managed, humanActivity)` behavior, label-free.

- [ ] **Step 1: Failing tests (table-driven)** mirroring the spec: same identity dedups; different does not; bot-PR `DedupNumber`(=linked issue) matches an issue-slot task; PR same-head-SHA (from ledger openedPR entry) dedups; non-terminal task dedups (in-flight). Build Tasks with `Spec.Source`+`Status.WorkItems`, NO labels.
- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implement:** replace `t.Labels[labelSource*]` comparisons with `taskMatchesItem(t, c.repo, c.number)`; replace `t.Labels[labelHeadSHA]` with `headSHAForTask(t)` (helper reading the `role:openedPR` entry's `HeadSHA`, fallback `Status.MergedHeadSHA`).
- [ ] **Step 4: PASS.**
- [ ] **Step 5: Commit** `refactor(controller): isDeduped matches on spec/ledger identity`.

### Task 7: `priorTerminalAttempts` + `hasLiveLifecycleTaskForIssue` label-free

**Files:**
- Modify: `internal/controller/projectscan.go` (:123, :200, :248, :1725, :1754 matchers)
- Test: extend `projectscan_dedup_test.go`

- [ ] **Step 1: Failing tests** for both functions matching on `taskMatchesItem` (no labels).
- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implement** by replacing each `t.Labels[labelSourceRepo]/[labelSourceNumber]` guard with `taskMatchesItem`. (`priorTerminalAttempts` already keys partly on `Spec.Source.Number`; unify on `taskMatchesItem`.)
- [ ] **Step 4: PASS.**
- [ ] **Step 5: Commit** `refactor(controller): lifecycle-task lookups match on spec/ledger`.

### Task 8: Webhook dedup via deterministic name + project-scoped in-memory match

**Files:**
- Modify: `internal/webhook/server.go` (:350-405 dedup block; :411 issue arm; seed `Spec.Source.DedupNumber` where `dedupNumber` is computed)
- Test: `internal/webhook/server_dedup_test.go`

**Interfaces:**
- Consumes: `taskMatchesItem`, `TaskTerminal`.

- [ ] **Step 1: Failing tests:** (a) two deliveries for the same work-item produce ONE Task (deterministic name collision is idempotent); (b) a PR-slot event is not blocked by an issue-slot task and vice versa (slot disambiguation preserved via `Spec.Source.IsPR`/`DedupNumber`, not `LabelIsPR`); (c) no `tatara.io/source-*` labels written.
- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implement:** replace the `client.MatchingLabels{source-repo,source-number}` list with `client.InNamespace(ns)` list filtered in-memory by `taskMatchesItem(t, dedupSlug, dedupNumber)` and slot (`t.Spec.Source.IsPR == dedupIsPR`). Set `task.Spec.Source.DedupNumber = dedupNumber` when it differs from the PR number. Keep the deterministic-name path for `issueLifecycle`. (Accept O(tasks) list per the Full-removal decision.)
- [ ] **Step 4: PASS;** full `mise run test`.
- [ ] **Step 5: Commit** `refactor(webhook): project-scoped dedup via spec identity, drop label selector`.

### Task 9: Delete the three label constants

**Files:**
- Modify: `api/v1alpha1/annotations.go` (remove `LabelSourceRepo`, `LabelSourceNumber`, `LabelHeadSHA`)
- Modify: any lingering references (compile-driven)
- Test: `go build ./...` + full suite

- [ ] **Step 1:** `grep -rn 'LabelSourceRepo\|LabelSourceNumber\|LabelHeadSHA' --include=*.go` -> expect only the const definitions remain.
- [ ] **Step 2:** Remove the three consts; `go build ./...` -> fix any straggler.
- [ ] **Step 3:** `mise run test` green.
- [ ] **Step 4: Commit** `refactor(api): remove the 3 tatara.io source dedup labels`.

---

## Phase 3 - Cron backstop (two-tier)

### Task 10: SCM refresh of ledger entries (Tier 1, no agent)

**Files:**
- Create: `internal/controller/backstop.go` (`refreshLedger(ctx, reader, t) (changed bool)`)
- Test: `internal/controller/backstop_test.go` (fake `SCMReader`)

**Interfaces:**
- Consumes: `scm.SCMReader` (`ListOpenIssues`/`ListOpenPRs`/issue+PR state getters).
- Produces: `refreshLedger` updates each `WorkItemRef.State`/`HeadSHA`/`LastRefreshedAt` from live SCM; returns whether anything changed.

- [ ] **Step 1: Failing test:** ledger with an issue marked `open` whose SCM state is now `closed`, and a PR whose head SHA advanced -> `refreshLedger` updates both, returns `true`; no-op returns `false`.
- [ ] **Step 2: FAIL.** 
- [ ] **Step 3: Implement** using the existing reader; per provider map states to `open|closed|merged`.
- [ ] **Step 4: PASS.**
- [ ] **Step 5: Commit** `feat(controller): backstop refreshLedger (Tier 1 SCM sync)`.

### Task 11: Tier-2 decision (close-obsolete / reactivate / none)

**Files:**
- Modify: `internal/controller/backstop.go` (`backstopAction(t) action` returning `actionNone|actionCloseObsolete|actionReactivate`)
- Test: extend `backstop_test.go`

**Interfaces:**
- Produces: `backstopAction(*Task) backstopDecision`.

- [ ] **Step 1: Failing tests:** all source/closes issues closed + open MR -> `CloseObsolete`; open MR + open source issue + no live pod -> `Reactivate`; pure state refresh, nothing actionable -> `None`; live pod present -> `None`.
- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implement** the ordered decision (close-obsolete first). "No live pod" = `Status.PodName=="" || pod gone` (reuse existing pod-liveness helper).
- [ ] **Step 4: PASS.**
- [ ] **Step 5: Commit** `feat(controller): backstop Tier-2 action decision`.

### Task 12: Wire backstop into the project scan + bound reactivation

**Files:**
- Modify: `internal/controller/projectscan.go` (`runScans` :1848 - invoke a `backstopSweep(ctx, proj, reader)` after the existing scans; reuse `priorTerminalAttempts`/`closeExhaustedPR`)
- Modify: `internal/controller/backstop.go` (`backstopSweep` - list project Tasks, `refreshLedger` each, persist, then apply `backstopAction`: CloseObsolete -> close MR; Reactivate -> create MRCI task if `priorTerminalAttempts < maxRecoveryAttempts` else `closeExhaustedPR`)
- Test: `backstop_sweep_test.go` (envtest)

- [ ] **Step 1: Failing test:** a project with a stranded open-MR Task (no pod, prior 1 terminal attempt) -> sweep creates exactly one reactivation MRCI task; with 3 prior terminal attempts -> closes the PR, no new task; with all source issues closed -> closes MR, no task; Tier-1-only drift (label change) -> ledger updated, NO task created.
- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implement** `backstopSweep`; call from `runScans`.
- [ ] **Step 4: PASS;** full suite.
- [ ] **Step 5: Commit** `feat(controller): two-tier backstop sweep wired into project scan`.

---

## Phase 4 - Hybrid label projection for brainstorm proposals

### Task 13: `proposalBacklogCount` from the ledger

**Files:**
- Modify: `internal/controller/projectscan.go` (`proposalBacklogCount` :1668 + callers :1130/:1227)
- Test: `projectscan_backlog_test.go`

**Interfaces:**
- Produces: `proposalBacklogFromTasks(tasks []Task) int` - count of `role:proposed` ledger entries in non-terminal `State` (`proposed`), systemic-grouped (one per `SystemicID`).

- [ ] **Step 1: Failing test:** tasks carrying N `role:proposed` open entries (some sharing a systemic id) -> count matches the spec's standalone+groups rule.
- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implement** the ledger-based count; switch the brainstorm/healthCheck cap check to it (keep the SCM-issue count as a fallback only if `Status.WorkItems` empty, for migration).
- [ ] **Step 4: PASS.**
- [ ] **Step 5: Commit** `feat(controller): maxOpenProposals counted from ledger`.

### Task 14: Project proposal state -> issue + label; read label changes back

**Files:**
- Modify: `internal/controller/labels.go` (`setLifecycleLabel` - after setting the SCM label, upsert the matching `role:proposed` ledger entry `State`)
- Modify: `internal/controller/lifecycle.go` (the triage/approval read path - when a human `approved`/`declined` label is observed, set the entry `State` and drive implement/decline)
- Test: `projection_test.go`

- [ ] **Step 1: Failing tests:** projecting `approved` sets entry `State=approved`; observing a human `tatara-declined` sets `State=declined`; an `approved` proposal seeds implementation (a `role:source`/`closes` entry + implement task).
- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implement** the two-way projection (operator-owned writes only).
- [ ] **Step 4: PASS.**
- [ ] **Step 5: Commit** `feat(controller): hybrid label projection for brainstorm proposals`.

---

## Phase 5 - Maintenance projection + Task-sourced prompts + clone scope + systemic

### Task 15: Project agent MCP actions onto the ledger

**Files:**
- Modify: `internal/controller/writeback.go` (:285/:330 PR-open path - `UpsertWorkItem(role:openedPR)`)
- Modify: `internal/controller/lifecycle.go` (:1653 PRNumber set; close/merge paths - reflect `State`)
- Test: `writeback_ledger_test.go`

- [ ] **Step 1: Failing tests:** agent opens a PR -> a `role:openedPR{state:open,headSHA}` entry appears; a recorded merge -> entry `State=merged`; a closed source issue -> entry `State=closed`.
- [ ] **Step 2: FAIL.** 
- [ ] **Step 3: Implement** the upserts at the existing record sites (no new MCP tool).
- [ ] **Step 4: PASS.**
- [ ] **Step 5: Commit** `feat(controller): project agent MCP actions onto the ledger`.

### Task 16: Prompt-building + clone scope from the ledger

**Files:**
- Modify: `internal/agent/pod.go` (clone scope / ReposInScope from `reposInScope(t)`; prompt context block from `Status.WorkItems` - statuses, adjacent issues, conversation pointers)
- Test: `internal/agent/pod_ledger_test.go`

- [ ] **Step 1: Failing tests:** a multi-repo ledger yields a deduped sorted clone scope; the rendered prompt includes each spanned open issue/MR ref+state and the systemic siblings.
- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implement** reading the ledger in `BuildPod`/prompt assembly; fold `SystemicGroup` into ledger `role:closes` at creation (projectscan create path).
- [ ] **Step 4: PASS;** full suite.
- [ ] **Step 5: Commit** `feat(agent): build clone scope + prompt context from the ledger`.

---

## Phase 6 - One-off incident + deploy

### Task 17: Unstick wrapper!50 (incident, manual - NOT code)

- [ ] **Step 1:** Confirm `tatara-operator#74` is CLOSED (it is, 2026-06-20).
- [ ] **Step 2:** Close `tatara-claude-code-wrapper!50` with a comment: "Superseded - target issue tatara-operator#74 resolved; closing stale conflicting MR."
- [ ] **Step 3:** `kubectl -n tatara delete task.tatara.dev scan-qe-jt6zx` (the stale Parked task).
- [ ] **Step 4:** Note in `MEMORY.md` that the iterator close-obsolete rule now handles this class.

### Task 18: CRD regen + deploy via tatara-helmfile

- [ ] **Step 1:** `make manifests generate` in operator; confirm `crd-bases`/`templates/crds.yaml` gained `status.workItems`, `status.parkReason`, `spec.source.dedupNumber`; `helm lint` the chart.
- [ ] **Step 2:** Merge operator to `main`; CI builds image + chart (`0.0.0-g<sha>`).
- [ ] **Step 3:** tatara-helmfile MR: bump `tatara-operator` + `tatara-project` chart pins to `0.0.0-g<sha>` and `values/tatara-operator/common.yaml` image `tag` to `<sha>`; review the `helmfile diff`; apply.
- [ ] **Step 4: verification-before-completion:** operator 3/3 Running on the new SHA; CRD shows the new fields; a fresh scan/webhook Task seeds `Status.WorkItems` and carries NO `tatara.io/source-*` labels; the backstop reactivates a stranded open-MR Task (or confirm none remain).

---

## Self-review notes

- Spec coverage: ledger model (T1-3), ParkReason (T4), lazy-seed+drop-writes (T5), dedup migration (T6-9), two-tier backstop (T10-12), hybrid projection + ledger backlog (T13-14), MCP-action projection + prompt/clone scope + systemic (T15-16), wrapper!50 (T17), Project-level scope (reposInScope in T3/T16; RepositoryRef demotion T2), deploy (T18). All spec sections mapped.
- Migration safety: Phase 1 stops label writes + seeds ledger but keeps label reads; Phase 2 flips reads then deletes consts. Never deploy between Phase 1 and 2 (noted inline). Single deploy at T18 after all phases land on `main`.
- Type consistency: `WorkItemRef`, role/kind/state consts, `UpsertWorkItem`, `taskMatchesItem`, `reposInScope`, `seedLedgerFromSpec`, `refreshLedger`, `backstopAction`, `backstopSweep`, `proposalBacklogFromTasks` - names used consistently across tasks.
