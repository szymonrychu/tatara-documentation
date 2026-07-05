# Issue-lifecycle M2 - conversation (Triage discuss / Conversation idle) - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** Close the conversation loop: the agent posts questions (discuss, wired
in M0), the maintainer's comment re-spawns the agent fed the thread, the loop
runs until `triggerLabel` (-> Implement) or 1h continuous silence (-> Stopped),
and the re-spawned agent sees the full issue thread as context.

**Architecture:** Per spec, parts A+C. Idle is webhook-primary + scan backstop;
conversation context is read via a new `ListIssueComments` reader (prompt
construction only, never idle timing). **Repos:** `tatara-operator` + `tatara-cli`.

---

### Task 1: ListIssueComments SCM reader cap

**Files:** `internal/scm/scm.go` (`IssueComment{Author,Body,CreatedAt}` + interface method), `github.go`, `gitlab.go`, fakes; Tests.

- [ ] Failing tests (httptest): `ListIssueComments(ctx, owner, repo, number)` returns ordered `[]IssueComment` from the GitHub issue-comments API / GitLab notes API.
- [ ] Implement on both providers; add to `SCMReader`; satisfy fakes + `wire.go`.
- [ ] Test PASS. Commit `feat(scm): ListIssueComments reader cap`.

### Task 2: Conversation-prompt construction (thread as context)

**Files:** `internal/controller/lifecycle.go`, `turnloop.go`; Tests.

- [ ] Failing test: the Triage turn prompt (when re-entering from Conversation) includes the issue body + the rendered comment thread (author + body per comment) so a fresh pod has full context.
- [ ] Implement: before spawning the Triage agent run, fetch `ListIssueComments` for the source issue and render a thread block into the plan prompt. Cap length (most-recent-N or a char budget) to bound the prompt. On the first Triage entry (no prior comments) the block is just the issue body.
- [ ] Test PASS. Commit `feat(controller): conversation thread injected into triage prompt`.

### Task 3: Conversation idle state + Stopped

**Files:** `internal/controller/lifecycle.go`; Tests.

- [ ] Failing tests for the Conversation handler: `now < DeadlineAt` -> `RequeueAfter` until the deadline (no pod); `now >= DeadlineAt` -> `Stopped` (resumable), `giveup{reason="idle"}` counter NOT incremented (idle stop is normal, use a distinct `tatara_lifecycle_idle_stop_total`).
- [ ] Implement the Conversation handler. Ensure the pod is torn down on entry (the discuss transition in M0 already tears down; assert no pod recreated in Conversation).
- [ ] Test PASS. Commit `feat(controller): Conversation idle state stops after deadline`.

### Task 4: issue_comment webhook reaction

**Files:** `internal/webhook/server.go`, `internal/controller/lifecycle.go`; Tests `server_test.go`.

- [ ] Failing tests:
  - an `issue_comment` (`created`) on an issue with a live lifecycle Task in `Conversation` -> reset `LastActivityAt`+`DeadlineAt`, set state to `Triage`, nudge a reconcile (so the agent re-spawns fed the new comment).
  - the same on a `Stopped` task -> re-open: set `Triage`, reset timers.
  - `triggerLabel` applied (labeled) on a `Conversation` task -> `Implement`.
  - a comment by the bot itself -> ignored (no reset; avoid self-trigger loops).
- [ ] Implement: extend `handleWorkItem` to find the issue's live lifecycle Task and patch state/timers via a helper (RetryOnConflict). Author-gate: only human (non-bot) comments reset the timer. Keep the existing approval-unlabel reaction.
- [ ] Test PASS. Commit `feat(webhook): issue_comment continues the lifecycle conversation`.

### Task 5: issueScan conversation backstop

**Files:** `internal/controller/projectscan.go`; Tests.

- [ ] Failing test: `issueScan` finds an issue whose `updatedAt` is newer than the bound lifecycle Task's `LastActivityAt` while the Task is `Conversation`/`Stopped` -> it reactivates the Task (state -> Triage, reset timers) rather than creating a duplicate. (Covers a missed `issue_comment` webhook.)
- [ ] Implement in the issueScan dedup/bind path.
- [ ] Test PASS. Commit `feat(controller): issueScan re-activates stale conversation tasks`.

### Task 6: cli issue_outcome=discuss schema test

**Files:** `tatara-cli/internal/mcp/tools.go`; Tests. (The operator-side discuss handling is M0 Task 5; this confirms the cli surface.)

- [ ] Failing test: `issue_outcome` accepts `action=discuss` (enum `implement;close;discuss`) with the `comment` field; the POST body carries it.
- [ ] Implement the enum addition. Test PASS. Commit (cli repo) `feat(cli): issue_outcome discuss action`.

---

## Self-review

- [ ] Idle stop uses `LastActivityAt`/`DeadlineAt` only; no comment polling on the idle path.
- [ ] Only human comments reset the timer (no bot self-trigger).
- [ ] ListIssueComments used solely for prompt construction + the scan backstop, not idle.
- [ ] Full suite green; lint clean (both repos).
