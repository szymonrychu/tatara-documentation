# tatara-managed 3-label issue lifecycle - design

Date: 2026-06-13
Status: approved (design), pending spec review
Component: tatara-operator (plus infra helmfile values for the new label config)

## Problem

Today the operator exposes three separate, inconsistent human-signal surfaces:

- Proposal approval: a human removes the `tatara/awaiting-approval` label; the
  webhook `flipApproval` (server.go:170) plus the `approvalBackstop`
  (projectscan.go:250) flip `ConditionApprovalApproved`, unblocking a gated
  implement Task parked at `AwaitingApproval`.
- Conversation -> code: a human adds the `triggerLabel` (`tatara`) to a
  Conversation-state lifecycle Task (server.go:182) to jump it to Implement.
- Comment: `issue_comment created` (server.go:359 `handleIssueComment`) resets a
  live lifecycle Task to `Triage` so the agent re-reads the thread.

The desired model is a single, legible set of three labels, managed by tatara
(operator egress) and driven by the conversation:

- `tatara-idea`: originated or updated by tatara, not ready yet.
- `tatara-approved`: approved for implementation.
- `tatara-rejected`: tatara closed it (redundant, duplicate, not actionable).

## Decisions (pinned with the user)

1. Approval signal = the lifecycle agent reads the conversation thread and
   interprets human intent (approve/decline/ongoing); the operator performs the
   SCM label egress. Humans signal in prose comments, not by toggling labels.
2. Scope = any tatara-managed issue. Every open issue in a managed repo is
   triaged (issueScan triages all open issues; comments enter via the webhook),
   so every such issue carries exactly one of the three labels.
3. Autonomy = tatara may self-approve clear-cut items. Two embedded rules:
   - (R1) Brainstorm output is always `tatara-idea` and is never self-approved.
     Tatara does not approve its own ideas; a human must approve them.
   - (R2) Self-approve (straight to `tatara-approved` + Implement) applies only
     to human-filed / external issues that triage judges clear-cut. A
     bot-authored issue (detected via `Source.AuthorLogin == BotLogin`) always
     waits for a human approval comment.

## Architecture

The three labels are a projection of lifecycle transitions. The triage agent
already emits an `issue_outcome` (`implement` / `discuss` / `close`) after
reading the thread; the operator maps each outcome to a label and a state:

| issue_outcome | label           | next state            |
| ------------- | --------------- | --------------------- |
| implement     | tatara-approved | Implement             |
| discuss       | tatara-idea     | Conversation          |
| close         | tatara-rejected | close issue -> Done   |

Conversation re-evaluation already exists: `handleIssueComment` resets a live
lifecycle Task to `Triage` on any non-bot comment, and creates one at `Triage`
if none exists. So "agent re-reads the thread on a human comment" needs no new
plumbing. The new work is the label egress at transition points, a
bot-authored self-approve guard, brainstorm relabeling, and retiring the
label-toggle approval subsystem.

## Components

### 1. CRD: new label fields (api/v1alpha1/project_types.go)

Add to `ScmSpec`:

```go
// IdeaLabel marks an issue tatara originated or updated but that is not yet
// ready for implementation.
// +kubebuilder:default="tatara-idea"
// +optional
IdeaLabel string `json:"ideaLabel,omitempty"`

// ApprovedLabel marks an issue approved for implementation.
// +kubebuilder:default="tatara-approved"
// +optional
ApprovedLabel string `json:"approvedLabel,omitempty"`

// RejectedLabel marks an issue tatara closed (redundant, duplicate, not
// actionable).
// +kubebuilder:default="tatara-rejected"
// +optional
RejectedLabel string `json:"rejectedLabel,omitempty"`
```

`ApprovalLabel` stays as a field (deprecated) only so migration code can read
the old value; it is no longer part of any approval mechanism.

Regenerate CRDs (`make manifests`) and the helm chart's bundled CRD.

### 2. Egress helper (internal/controller/labels.go, new)

```go
// lifecycleLabels returns the three managed labels for the project, with
// defaults applied.
func lifecycleLabels(scm *tatarav1alpha1.ScmSpec) (idea, approved, rejected string)

// setLifecycleLabel ensures exactly `desired` of the three managed labels is
// present on the task's source issue: adds `desired` if absent, removes the
// other two if present. Idempotent: a no-op when already in the target state.
// Only ever touches the three managed labels; never triggerLabel/priorityLabel.
func (r *TaskReconciler) setLifecycleLabel(ctx context.Context, proj *tatarav1alpha1.Project, task *tatarav1alpha1.Task, desired string) error
```

Implementation notes:
- Resolve writer + token via the existing `scmContext(ctx, task)` path used by
  `triageCloseIssue` / `triagePostComment`.
- Read the issue's current labels from `reader.GetIssue` is title/body only;
  use `reader.ListOpenIssues` (already used by the backlog/backstop) and match
  on `IssueRef`/Number to get current `Labels`, OR call `AddLabel` then
  `RemoveLabel` unconditionally (both auto-create / are idempotent on the SCM
  side). Prefer the read-then-diff form to avoid noisy label events; fall back
  to unconditional add/remove if the issue is not in the open-issues list.
- `AddLabel(desired)` is required (retry, return error to requeue on hard
  failure). `RemoveLabel(other)` is best-effort (log + metric on failure,
  continue) - a lingering extra label is cosmetic and self-heals on the next
  transition.
- Log at INFO with `action=scm_set_label`, `resource_id`, `issue_ref`,
  `label`. Metric: reuse the SCM write counter (`AddLabel`/`RemoveLabel`).

### 3. finishTriage (internal/controller/lifecycle.go:410)

Insert the label egress and the bot-authored guard:

- `close`: `setLifecycleLabel(rejected)` -> `triageCloseIssue` -> Done.
- `discuss`: `setLifecycleLabel(idea)` -> `triagePostComment` -> Conversation.
- `implement` (and the nil-outcome default): apply the bot-authored guard:
  - If `task.Spec.Source != nil && task.Spec.Source.AuthorLogin == proj.Spec.Scm.BotLogin`:
    fetch issue comments (`reader.ListIssueComments`); if there is no comment
    authored by a non-bot login, downgrade this outcome to `discuss`
    (`setLifecycleLabel(idea)` -> Conversation, post no comment or a neutral
    "awaiting your go-ahead" once). A `ListIssueComments` error fails closed
    (treated as no human comment -> park).
  - Otherwise (human-filed issue, or bot-authored with a human comment present):
    `setLifecycleLabel(approved)` -> Implement.

The guard is the enforcement point for R1 and R2. The default-implement path
(nil outcome) routes through the same guard, so a bot-authored issue can never
be silently self-approved.

### 4. Triage prompt (internal/controller/turnloop.go lifecycleTriageText)

Extend the turn-0 triage instructions:
- Read the full issue thread (title, body, all comments) before deciding.
- Map human intent: an approval/go-ahead -> `implement`; a decline / "close" /
  "duplicate" -> `close`; anything still under discussion or needing the human
  -> `discuss`.
- For a tatara-authored idea (the issue body carries the tatara-authored
  marker, or you opened it), emit `implement` ONLY if a human has posted an
  approval comment; otherwise emit `discuss` and wait.

### 5. Brainstorm proposal (internal/controller/writeback.go createProposal)

- Label the new issue with `ideaLabel` (resolved via `lifecycleLabels`) instead
  of `approvalLabel`.
- After the issue is opened (and optional board placement), the brainstorm Task
  completes: set `Status.Phase = "Succeeded"` and clear `WritebackPending` with
  a `BrainstormProposed` reason. Remove the `advanceToAwaitingApproval` call and
  the `AwaitingApproval` parking for proposals.
- Keep the title-level idempotency and Source-set idempotency guards.
- Keep the board "Proposed" column placement.

### 6. proposalBacklog (internal/controller/projectscan.go:755)

Count open non-PR issues that are bot-authored and bear `ideaLabel`. With the
proposal-Task parking removed, the `approvalLabel == ""` branch that counted
`AwaitingApproval` Tasks is dropped; the backlog is always computed from open
SCM issues bearing `ideaLabel`. The `maxOpenProposals` cap (default 3) is
unchanged.

### 7. Retire the label-toggle approval subsystem

- `internal/webhook/server.go`: remove the `unlabeled` + `approvalLabel`
  branch (server.go:170-175) and the `flipApproval` function (server.go:529-575)
  and the `approvalLabel(proj)` helper. Keep `handleIssueComment` and the
  `triggerLabel`->Implement jump (manual force-approve override).
- `internal/controller/projectscan.go`: remove `approvalBackstop`
  (250-289) and `flipApprovalApproved` (290-305) and their call site in
  `runScans`.
- Proposal `ApprovalRequired` gate: remove the proposal path that sets
  `ApprovalRequired` and the `AwaitingApproval` gate handling specific to
  proposals in `task_controller.go:146-174`. Audit for any other
  `ApprovalRequired` user before deleting the field; if none remains, drop the
  field and `ConditionApprovalApproved`.
- `advanceToAwaitingApproval` (writeback.go:407): removed (only the proposal
  path called it).

### 8. Migration

- One-time relabel at deploy: open issues carrying `tatara/awaiting-approval`
  are relabeled to `tatara-idea`. Implemented as a small idempotent reconcile
  pass keyed off the deprecated `approvalLabel` value (run once on startup; safe
  to re-run), or as a documented manual `gh`/`glab` relabel step in the deploy
  runbook. Decision: prefer the manual runbook step (KISS; one-shot, low
  volume), documented in the plan.
- In-flight `AwaitingApproval` proposal Tasks at deploy time: allow them to
  finish on the old arms where possible; otherwise they are superseded once the
  proposal issue is relabeled and picked up by the lifecycle path. Documented.

## Error handling

- `setLifecycleLabel`: `AddLabel` required (requeue on failure); `RemoveLabel`
  best-effort. Idempotent, safe to retry.
- Bot-authored guard `ListIssueComments` failure: fail closed (park as idea).
- finishTriage already clears `Status.IssueOutcome` before acting (idempotency
  against re-reconcile).
- Label egress never blocks a `close` from closing the issue: relabel to
  `rejected` first, then close; if relabel fails, the close still proceeds on
  retry (the rejected label is reapplied idempotently).

## Testing (TDD)

Unit (envtest where reconcile state is involved, fakes for SCM reader/writer):

- `setLifecycleLabel`: adds desired when absent; removes the other two when
  present; no-op when already in target; only ever touches the three managed
  labels (asserts triggerLabel/priorityLabel untouched); `AddLabel` error
  requeues; `RemoveLabel` error is non-fatal.
- `finishTriage`:
  - `close` -> label rejected + issue closed + state Done.
  - `discuss` -> label idea + comment posted + state Conversation.
  - human-filed `implement` -> label approved + state Implement.
  - bot-authored `implement`, no non-bot comment -> downgraded: label idea +
    state Conversation (no approval).
  - bot-authored `implement`, with a non-bot comment -> label approved + state
    Implement.
  - `ListIssueComments` error on a bot-authored implement -> fail closed (idea).
- `createProposal`: opens the issue with `ideaLabel`; brainstorm Task ends
  Succeeded (not AwaitingApproval); board placement intact; idempotency guards
  intact.
- `proposalBacklog`: counts open bot-authored `ideaLabel` issues; respects
  `maxOpenProposals`; ignores PRs and non-idea issues.
- Retirement: webhook routing still handles push / issue_comment / triggerLabel
  jump after the `unlabeled`/`flipApproval` removal; `runScans` still runs
  mrScan/issueScan/brainstorm after `approvalBackstop` removal.

## Out of scope

- Label-driven board column automation beyond the existing "Proposed"
  placement.
- Changing the triage / conversation idle deadlines.
- Any change to the MRCI / Merge / MainCI arms.
