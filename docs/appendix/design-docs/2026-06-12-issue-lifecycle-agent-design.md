# Issue-lifecycle agent - design

Date: 2026-06-12. Status: approved (design gate passed; per-section approval
2026-06-12). Supersedes the agent-side Issue/MR management handoff
(`docs/superpowers/handoffs/2026-06-12-agent-issue-mr-management.md`) and the
deferred subsystem-B "operator waits for its PR + closes the issue" scope.

Target repos: `tatara-operator` (the state machine), `tatara-cli` (agent MCP
surface), `tatara-claude-code-wrapper` (per-turn usage signal). Specs/plans in
the parent `tatara` repo under `docs/superpowers/`.

## Goal

One agent, bound to one issue, drives the whole lifecycle: triage ->
conversation -> implement -> MR-CI -> merge -> main-CI -> close. Today this is
split across short-lived per-phase Tasks (`triageIssue`, issue-sourced
`implement`, `selfImprove`) that each run once and terminate, with the merge
step error-looping on conflict. This design introduces a persistent
issue-bound `issueLifecycle` Task carrying an S1..S7 state machine in its
status, reusing the existing agent-run + writeback primitives but sequencing
them across states with SCM polling, a conversation idle timer, a context-based
handover guard, and MR scope reporting.

## What this fixes (the user's four parts + the live bug)

- **A. Issue conversation.** The agent posts designs/questions as issue
  comments and converses with the maintainer under the issue (states S1/S2).
- **B. MR scope completeness.** The MR body states delivered scope; a partial
  delivery opens a follow-up issue for the remainder (state S4 + writeback).
- **C. Issue-activity state machine.** `triggerLabel` -> straight to implement;
  a comment -> the agent answers in a loop until the maintainer approves
  (applies `triggerLabel`) or 1h of continuous silence parks it (S2).
- **D. MR babysitting.** The operator polls the PR pipeline and reacts: success
  -> merge; failure -> fix; conflict -> resolve; deadline -> comment + leave
  open (states S5/S6 + S7). This kills the live `selfImprove ... merge -> 405
  merge conflicts` controller-runtime backoff loop (root cause below).

### Root cause of the live error-loop (verified 2026-06-12)

`writeBackSelfImprove` (`writeback.go:561`) runs once at Task-terminal. For a
bot PR with `pr_outcome=merge` under `mergePolicy: afterApproval`,
`mergeAllowed` returns true unconditionally (`writeback.go:738-739`), so it
calls `writer.Merge()`. When the PR has conflicts the GitHub API returns
`*HTTPError{Status: 405}` (`github.go:226`), which `writeBackSelfImprove`
wraps and returns as an error (`writeback.go:613`). `Reconcile` returns
`(ctrl.Result{}, err)` (`task_controller.go:89`), so controller-runtime retries
on exponential backoff forever - the same merge, the same 405. There is no
conflict detection, no deadline, no give-up. D replaces this with a poll-and-
react state that detects the 405 and spawns a resolve-conflict agent turn.

## Decisions (pinned 2026-06-12)

1. **Architecture: canonical lifecycle Task first.** One persistent
   `issueLifecycle` Task per issue carries the S1..S7 state machine. Chosen
   over an incremental "D-first hotfix on the old per-phase model".
2. **Context guard: operator-computed from usage.** The wrapper already
   captures per-turn Claude `usage`; the operator plumbs it through the
   turn-complete callback, accumulates on the Task, and triggers a handover when
   the latest turn's input-token count crosses a configured percent of the
   context window.
3. **Idle tracking: webhook-primary + scan backstop.** `LastActivityAt` is
   reset by each inbound `issue_comment` webhook; a hard stop fires after 1h of
   continuous silence. `issueScan` re-binds (via issue `updatedAt`) if a comment
   was missed during operator downtime. No comment-polling on the idle hot path.
4. **Merge give-up: comment + leave open.** On deadline or unresolved conflict
   the operator comments on the PR and leaves it open (no auto-close, no
   follow-up issue for the give-up case).
5. **Labels reuse, no new field.** The existing `spec.triggerLabel` is the
   "allow-implementation"/approve-to-code signal (S2 -> S4). The existing
   `scm.approvalLabel` stays the merge gate / proposal-approval signal.
6. **Shelve `feat/scm-auto-merge`.** D's poll-and-react supersedes SCM-native
   auto-merge for the bot path (handles conflict / CI-failure / deadline, which
   native cannot, and needs no per-repo branch-protection setup). Leave that
   branch unmerged; do not build on it.
7. **`review` (human-PR) path untouched.** The lifecycle is issue-driven and
   bot-PR-driven only. Human-PR review stays the standalone `review` Task.

## Architecture: lifecycle = orchestration over existing primitives

`issueLifecycle` is a new `Task.Spec.Kind`. Its reconcile is a state machine
dispatching on `Task.Status.LifecycleState`. Each state is either an
**agent-run state** (spawns the wrapper Pod and drives turns with the existing
`driveTurns` machinery, ending in a decision/result) or a **poll state** (no
Pod; a pure SCM API call + requeue). It reuses, never duplicates:

- triage decision = the existing `triageIssue` agent run + `issue_outcome`.
- implement = the existing plan->subtask agent run, pushing `taskBranch(t)`.
- open MR = the existing `writeBackOpenChange` egress (operator-mediated).
- merge gate + merge = the existing `mergeAllowed` + `writer.Merge`.
- CI read = the existing `GetPRState.CIStatus`.

The standalone `triageIssue` and `selfImprove` Task creation in the binders
(`projectscan.go`, `webhook/server.go`) is replaced by `issueLifecycle`
creation. The shared writeback helpers are refactored into lifecycle-state
handlers (same logic, called from the state machine). No dead code is left:
the old single-shot dispatch arms for `triageIssue`/`selfImprove` are removed,
their logic folded into states. `review` and `brainstorm` arms remain.

### States

| State | Kind | What happens |
|-------|------|--------------|
| **Triage** (S1) | agent-run | Agent reads issue + docs + code, calls `issue_outcome`: `close` -> Done; `discuss` -> post questions, -> Conversation; `implement` -> Implement. |
| **Conversation** (S2) | idle | Pod torn down. `DeadlineAt = now + idle`. On `issue_comment` webhook: reset `LastActivityAt`/`DeadlineAt`, -> Triage (re-spawn fed the new comment). On `triggerLabel`: -> Implement. On `DeadlineAt` passed: -> Stopped (resumable). |
| **Implement** (S4) | agent-run | Agent implements from issue + conversation thread + docs/code (subagents/Workflow), pushes `taskBranch`, reports scope via `change_summary`. On run complete: operator opens MR (scope body + `Closes #N`), opens a follow-up issue if `remaining_scope` set, -> MRCI. |
| **MRCI** (S5) | poll | Poll `GetPRState.CIStatus` every `pollRequeue`. pending -> requeue (until `DeadlineAt`). success -> Merge. failure -> Implement (re-spawn fix turn fed the failing checks). `DeadlineAt` passed -> Parked (comment + leave open). |
| **Merge** (S6) | api | `mergeAllowed` then `writer.Merge`. success -> record merge SHA, -> MainCI. `405` conflict -> Implement (re-spawn resolve-conflict turn: rebase default branch, push). other error -> requeue w/ backoff under `DeadlineAt`. |
| **MainCI** (S6b) | poll | Poll the default-branch CI on the merge SHA. success -> closing comment + `CloseIssue` (idempotent w/ `Closes #N`), -> Done. failure -> Implement (re-spawn re-implement turn), **subject to the context guard** (below). |
| **Done / Stopped / Parked** | terminal | Done: issue closed, flow complete. Stopped: idle-parked, resumable from a new comment. Parked: PR left open for a human after give-up. |

`LifecycleIterations` counts S4->S7 loop entries; a hard `maxLifecycleIterations`
cap (default 10) is a defense-in-depth backstop to the context guard.

### Entry points (the binders set the entry state)

- **New/updated issue** (issueScan or `issues` webhook, no `triggerLabel`):
  create-or-rebind an `issueLifecycle` Task at **Triage**.
- **Issue already carrying `triggerLabel`** (label webhook / scan): enter at
  **Implement** (skip dialogue), matching today's "labeled issue -> implement".
- **New comment on an open issue** (`issue_comment` webhook): rebind the
  issue's Task; if Conversation, reset the timer and -> Triage; if absent,
  create at Triage.
- **Bot-authored PR with no live lifecycle Task** (mrScan / `pull_request`
  webhook; the stale-MR / operator-restart case): create-or-rebind an
  `issueLifecycle` Task entered at **MRCI** (babysit the existing PR). If the
  PR body links an issue (`Closes #N`), key the Task to that issue; else key it
  to the PR. This replaces the standalone `selfImprove` path.
- **Human-authored PR**: unchanged `review` Task.

Task identity (dedup) extends the existing label scheme
(`tatara.io/source-repo`, `tatara.io/source-number`, `tatara.io/source-kind`):
one `issueLifecycle` Task per `(repo, issue-number)` (or `(repo, pr-number)`
for the PR-entry case). A non-terminal lifecycle Task for the key suppresses
re-creation; `LifecycleState in {Done, Stopped, Parked}` frees the key.

## CRD changes

### `Task.Status` (new fields, all optional)

- `lifecycleState` string, kubebuilder enum
  `Triage;Conversation;Implement;MRCI;Merge;MainCI;Done;Stopped;Parked`. Empty
  on non-lifecycle Tasks.
- `lastActivityAt` *metav1.Time - last inbound issue activity (idle reference).
- `deadlineAt` *metav1.Time - the active timer (idle in Conversation; CI-poll
  deadline in MRCI/MainCI).
- `headBranch` string - the agent work branch (`tatara/task-<name>`; persisted
  for the resolve-conflict rebase turn).
- `prNumber` int - the opened PR/MR number (set at open; used by poll/merge).
- `mergeCommitSHA` string - the squash-merge commit, polled in MainCI.
- `cumulativeTokens` int64 - sum of per-turn output tokens (observability).
- `lastTurnInputTokens` int64 - latest turn's input tokens (context-guard input).
- `lifecycleIterations` int - count of S4->S7 loop entries.
- `handover` string - the handover artifact for a fresh agent to resume from
  (bounded; written by the agent via `submit_handover`, read into the next
  agent's first turn prompt).

`PrURL` (existing) is retained as the canonical PR URL; `prNumber` is the
parsed integer the poll/merge calls need.

### `Project.Spec` (new fields, all optional with defaults)

- `agent.contextWindowTokens` int, default 200000 - denominator for the context
  percent. Conservative; tune per the wrapper's effective claude window.
- `agent.handoverThresholdPercent` int, default 50 - handover trips when
  `lastTurnInputTokens * 100 / contextWindowTokens >= threshold` after an S7
  iteration.
- `agent.maxLifecycleIterations` int, default 10 - hard S4->S7 backstop.
- `scm.babysitDeadlineMinutes` int, default 60 - the MRCI/MainCI poll deadline.
- `scm.conversationIdleMinutes` int, default 60 - the S2 idle stop (the user's
  "1h continuous").

No new label field: `spec.triggerLabel` and `scm.approvalLabel` are reused.

## Cross-repo contract

### tatara-cli (agent MCP surface)

- `issue_outcome` gains a third action `discuss` (enum becomes
  `implement;close;discuss`); the existing `comment` field carries the
  questions/design to post. `discuss` -> the operator posts the comment and
  parks the lifecycle in Conversation.
- New `change_summary` tool (M4): `pr_title`, `pr_body`, `delivered_scope`,
  optional `remaining_scope`. Called at the end of an Implement run; the
  operator uses these for the MR body and the follow-up-issue decision.
- New `submit_handover` tool (M3): `handover` text. Called when the agent
  judges (or is told) it should hand over; stored on `Task.Status.handover`.
- Conversation context read (M2): the operator builds the Triage/Conversation
  turn prompt from the issue body plus the comment thread. This requires a
  `ListIssueComments` SCM **reader** cap (operator-side; see below) - used only
  for prompt construction, never for idle timing.

### tatara-claude-code-wrapper (per-turn usage)

The wrapper's `turn.Record` already serializes a `usage` field (Claude API
usage from the Stop-hook transcript) and posts it to the operator callback. The
only gap is operator-side: the operator's `turnCompletePayload` does not
deserialize it. **M3 is therefore operator-mostly**: add `usage` to
`turnCompletePayload`, parse `input_tokens` (+`cache_read_input_tokens`) into
`lastTurnInputTokens` and `output_tokens` into `cumulativeTokens`. The wrapper
change is at most a confirmation that `usage` is populated for one-turn
sessions; no payload restructure expected.

### tatara-operator SCM layer

- New `SCMReader.ListIssueComments(ctx, owner, repo string, number int)
  ([]IssueComment, error)` (GitHub + GitLab), where `IssueComment` carries
  `Author`, `Body`, `CreatedAt`. Used for conversation-prompt construction (M2)
  and as the issue-thread source for the `Closes #N` link detection on the
  bot-PR entry. NOT used for idle timing.
- `GetPRState` is extended/confirmed to expose the default-branch CI status for
  the merge commit (MainCI poll). If `GetPRState` cannot report post-merge main
  CI, add a `GetCommitCIStatus(ctx, owner, repo, sha string) (string, error)`
  reader cap returning the same `"" | pending | success | failure` vocabulary.
- The `405`/conflict detection uses the existing `*scm.HTTPError`: `Merge`
  returns it; the Merge state inspects `he.Status == 405` (and/or a body match
  for "conflict") to branch to the resolve-conflict turn.

## The context-guard handover (S7)

After a MainCI failure that routes back to Implement, before re-spawning:
compute `pct = lastTurnInputTokens * 100 / contextWindowTokens`. If
`pct >= handoverThresholdPercent` (default 50):

1. The operator submits a final "write a handover" turn to the current agent
   (the `handoff` skill is baked into the wrapper image - verified), which calls
   `submit_handover(handover=...)`. The operator stores it on
   `Status.handover`.
2. The operator terminates the wrapper session/Pod (resets context).
3. The next Implement turn for a fresh Pod injects `Status.handover` into its
   first turn prompt, so the fresh agent resumes from the handover, not cold.

`lifecycleIterations >= maxLifecycleIterations` forces a Parked give-up
regardless of context (backstop).

## Reconcile flow (operator)

`Reconcile` keeps its current preamble (terminal guard, project/memory gate,
concurrency gate, approval gate). For `Kind == issueLifecycle` it dispatches to
`reconcileLifecycle(ctx, project, task)` which switches on
`Status.LifecycleState`:

- agent-run states (Triage / Implement) reuse `ensurePodAndService` +
  `driveTurns`; on the run's terminal result the state handler reads the agent's
  decision (`issue_outcome` / `change_summary`) and transitions, tearing down
  the Pod via `terminate`-like cleanup (without marking the whole Task
  terminal - only the lifecycle sub-run ends).
- poll states (MRCI / MainCI) return `ctrl.Result{RequeueAfter: pollRequeue}`
  until terminal/deadline; no Pod.
- the Merge state is a single API call + transition.

Webhook reactions (`webhook/server.go`) gain: `issue_comment` on an issue with
a live lifecycle Task -> patch `LastActivityAt` + nudge a reconcile (and, if
Conversation, reset to Triage); `pull_request` (bot-authored) with no live Task
-> create the MRCI-entry Task. The existing approval-unlabel reaction is
unchanged.

## Observability (hard rules 11-13)

- New metric labels/series: `tatara_lifecycle_state{state}` gauge (count of
  Tasks per lifecycle state); `tatara_lifecycle_transition_total{from,to}`
  counter; `tatara_lifecycle_handover_total` counter;
  `tatara_lifecycle_giveup_total{reason}` counter (deadline|maxIterations|
  conflict). Histograms: `tatara_mrci_wait_seconds`, `tatara_lifecycle_seconds`
  (issue open -> Done).
- INFO log per transition with `action=lifecycle_transition`,
  `resource_id=<task>`, `from`, `to`, `issue`, `pr`. WARN on give-up/handover.

## Testing strategy

- envtest table-driven per state handler: Triage decisions (close/discuss/
  implement), Conversation timer (comment resets, 1h stop, label jumps),
  Implement->MRCI open, MRCI poll (pending requeue / success / failure->fix /
  deadline->park), Merge (success / 405->resolve / error->requeue), MainCI
  (success->close / failure->reimplement), context guard (>=50% input tokens
  after S7 -> handover), iteration backstop.
- Fakes: the existing fake `SCMWriter`/`SCMReader` extended with
  `ListIssueComments`, `GetCommitCIStatus`, a programmable `Merge` returning a
  `405` `*HTTPError`, and a `GetPRState` with scriptable `CIStatus`.
- The live 405 loop gets a regression test: a `Merge` returning 405 in the
  Merge state must transition to Implement (resolve-conflict), never return the
  error to controller-runtime.
- cli MCP tool tests for `issue_outcome=discuss`, `change_summary`,
  `submit_handover` (schema + body shape, mirroring existing tool tests).
- operator turn-callback test: a payload with `usage` populates
  `lastTurnInputTokens`/`cumulativeTokens`.

## Decomposition (one spec; milestone plans; deploy incrementally)

- **M0 - lifecycle model + skeleton.** CRD status/spec fields + deepcopy +
  CRD manifests; `issueLifecycle` kind; binders switch to creating lifecycle
  Tasks (Triage / MRCI entry); `reconcileLifecycle` skeleton dispatch with
  Triage + Implement wired to the existing agent-run; transitions table; metrics
  scaffolding. Deployable but inert beyond triage+implement.
- **M1 - D babysit (MRCI / Merge / MainCI / give-up).** The poll-and-react
  states, the 405 resolve-conflict turn, the deadline park, the MainCI close.
  **First milestone that kills the live error-loop.** Ships with M0.
- **M2 - conversation (Triage discuss / Conversation idle).** `issue_outcome=
  discuss`, `issue_comment` webhook reaction, `LastActivityAt`/idle stop,
  `triggerLabel` jump, `ListIssueComments` + conversation-prompt construction.
- **M3 - context-guard handover.** Usage plumb (operator payload + accumulate),
  `submit_handover`, threshold trip after S7, session reset + resume-from-
  handover turn.
- **M4 - MR scope completeness.** `change_summary`, scope-describing MR body,
  follow-up issue on `remaining_scope`.

Each milestone: TDD, subagent-driven, opus review, merge to the component's
local `main` only (deploy is separately gated). Deploy after M0+M1 (error-loop
fix) bumping BOTH the helmfile chart version AND the pinned `image.tag` (memory
`tatara-operator-deploy-chart-version-and-image-tag`), then M2/M3/M4
incrementally. cli/wrapper changes ride their own image cuts where touched.

## Out of scope

- The `review` human-PR path (unchanged).
- SCM-native auto-merge (`feat/scm-auto-merge`, shelved).
- ROADMAP #2 (CI-failure -> auto-issue, the main-branch issue-creation side) and
  #3 (local-dev <-> external-agent) - adjacent, separate specs. M1's MainCI poll
  shares the CI-read primitive with #2; build back-to-back.
