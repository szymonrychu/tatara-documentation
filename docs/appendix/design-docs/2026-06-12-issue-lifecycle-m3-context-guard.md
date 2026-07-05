# Issue-lifecycle M3 - context-guard handover - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** When an S7 (MainCI-failure) re-implement would run with a near-full
context, the operator makes the agent write a handover, resets the session, and
resumes a fresh agent from the handover doc. Operator-computed from the
wrapper's per-turn usage (decision 2).

**Architecture:** Per spec. The wrapper already serializes per-turn `usage`; the
operator just needs to deserialize + accumulate it, then trip a threshold.
**Repos:** `tatara-operator` (most) + `tatara-cli` (`submit_handover`) +
`tatara-claude-code-wrapper` (confirm usage is populated; only change if absent).

---

### Task 1: Operator consumes per-turn usage

**Files:** `internal/controller/turncallback.go`, `internal/agent/session.go`/`http.go`, `task_controller.go`; Tests `turncallback_test.go`.

- [ ] Failing test: a turn-complete callback payload that includes a `usage` object (`{input_tokens, output_tokens, cache_read_input_tokens, ...}`) populates `Task.Status.LastTurnInputTokens` (= `input_tokens` + `cache_read_input_tokens`) and adds `output_tokens` to `Status.CumulativeTokens`.
- [ ] Implement: add `Usage json.RawMessage` to `turnCompletePayload` (the wrapper already sends it); parse the token fields; persist onto the Task in `recordResult`/`recordTurn`. Tolerate absent/empty usage (leave fields unchanged).
- [ ] Test PASS. Commit `feat(controller): accumulate per-turn token usage on the Task`.

### Task 2: Confirm wrapper emits usage (wrapper repo)

**Files:** `tatara-claude-code-wrapper/internal/turn/turn.go` (read-only confirm); add a test only if a gap is found.

- [ ] Read `turn.Record` + the Stop-hook plumbing; confirm `usage` is populated for one-turn sessions and posted to the callback. If populated (expected per the code map), NO wrapper change - note it in the milestone report. If a gap exists (usage dropped for one-turn sessions), add a failing test + minimal fix to forward `HookResult.usage` into `turn.Record.usage`.
- [ ] Commit only if changed.

### Task 3: submit_handover MCP tool (cli) + operator store

**Files:** `tatara-cli/internal/mcp/tools.go` (+ tests); `tatara-operator` REST handler + `internal/controller`; Tests.

- [ ] Failing test (cli): `submit_handover` tool accepts `{handover: string}` (task auto-resolved from `TATARA_TASK`), POSTs to `/tasks/{tk}/handover`.
- [ ] Failing test (operator): the `/tasks/{tk}/handover` endpoint writes `Status.Handover` (bounded length).
- [ ] Implement both. Tests PASS. Commit (cli) `feat(cli): submit_handover tool`; (operator) `feat(operator): handover endpoint stores Status.Handover`.

### Task 4: Context-guard trip on S7

**Files:** `internal/controller/lifecycle.go`; Tests.

- [ ] Failing tests for the MainCI-failure path (replacing M1's direct re-implement):
  - `lastTurnInputTokens * 100 / contextWindowTokens >= handoverThresholdPercent` (default 50) -> submit a "write a handover via submit_handover" turn to the current agent, wait for `Status.Handover` to be set, then teardown the session + pod, set a `pendingHandoverResume` marker, increment `tatara_lifecycle_handover_total`, transition to `Implement` (fresh pod next reconcile).
  - below threshold -> direct re-implement (M1 behavior).
- [ ] Implement: compute pct from `project.Spec.Agent.ContextWindowTokens` (default 200000); the handover turn prompt instructs the agent to call `submit_handover`; gate the teardown on `Status.Handover != ""` (with a turn-timeout fallback so a non-cooperating agent still resets).
- [ ] Test PASS. Commit `feat(controller): context-guard handover before S7 re-implement`.

### Task 5: Resume from handover

**Files:** `internal/controller/lifecycle.go`, `turnloop.go`; Tests.

- [ ] Failing test: an Implement run entered with `pendingHandoverResume` set injects `Status.Handover` into the first turn prompt ("Resume from this handover: <doc>") and clears `pendingHandoverResume` (the handover doc stays for audit) so the fresh agent continues, not restarts.
- [ ] Implement.
- [ ] Test PASS. Commit `feat(controller): resume implement from handover doc`.

---

## Self-review

- [ ] Usage parsing tolerates missing fields (no nil-panic, no zeroing on absent usage).
- [ ] The handover trip has a turn-timeout fallback (a stuck agent still resets - no deadlock).
- [ ] `pendingHandoverResume` cleared exactly once after injection.
- [ ] Full suite green across touched repos; lint clean.
