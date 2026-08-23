---
title: From Issue to PR
---

# From Issue to PR

Follow one issue through the whole machine and you will know what happens at every point between clicking "Submit" and seeing the change deployed, and where you can step in. This is the general walkthrough; [Watch One Run](watch-one-run.md) is the same journey as one real run in tatara's own repository, with the thread quoted.

## The eight states, up front

Where a piece of work stands is `Task.status.state`, a field you can watch with `kubectl get tasks`. It is a closed eight-value enum, and the whole page below is a tour of it:

| State | What is happening |
|---|---|
| `new` | Triage. No pod runs |
| `refined` | The approval gate. A pod is up, writing a plan and looking for a maintainer comment to cite |
| `under-implementation` | Code is being written. A pod is up |
| `awaiting-review` | A review pod is reading the diff |
| `merged` | The merge phase. The operator is walking the Task's merge order. No pod |
| `deployed` | Merged, waiting for the deploy to land. No pod |
| `done` | Terminal, success |
| `rejected` | Terminal, stopped |

Two things worth fixing in your head before you start. `parked` and `failed` are **not** states: parking is a separate flag, `status.parkReason`, that a Task carries alongside whatever state it is in. And `merged` and `deployed` name the phase, not the milestone - a Task at `merged` has an accepted approval and an operator working its merge order, not necessarily a merged pull request.

Twenty-five transitions connect those eight states, and six guards constrain them. [The Task state machine](../reference/task-stages.md) tabulates every edge, guard, and park reason; this page does not repeat them.

The operator also projects a small, per-project-configurable set of labels onto the SCM issue as a read-only mirror of `Issue.status.status` and `Task.status.state`, so you can follow along without cluster access. The labels are a **one-way projection**, never a control input: nothing in the operator ever reads a label to decide what happens next. One you will see named throughout the runbooks is `tatara-parked`, applied whenever a Task carries any `parkReason` - see [Approval Gates](../operations/security/approval-gates.md#labels-are-write-only) for the full projection rule.

!!! info "Since the #521 lifecycle redesign: `clarify` is `implement`"
    This page describes the platform after the #521 lifecycle redesign folded the `clarify` agent
    kind into `implement` (both as the running agent and as the Task origin) and replaced the old
    16-member `status.stage` with an 8-member `status.state` plus the orthogonal
    `status.parkReason` flag.

---

## Step 1 - You open an issue

You open a GitHub issue in any repository enrolled in your tatara Project. The title and body are your only inputs, and tatara reads them verbatim.

**Task state:** *(none yet)*

If `Project.spec.scm.reporterLogins` is populated, the issue author must be the bot, a maintainer, or an allowed reporter, or the event is dropped at intake. Left empty (the shipped default), any author's issue is accepted. Either way, opening an issue does not itself grant anything - it only gets a Task minted.

This page follows the full path - issue opened, `implement`'s approval-gate conversation runs first.

---

## Step 2 - The operator mints a Task

The operator sees the new open issue in an enrolled repository, mirrors it as an `Issue` custom resource, and mints a `Task` custom resource with `spec.kind: implement` that owns it (`SweepIssueKind` - the origin role `clarify` used to play). The Task is the durable, project-scoped unit that carries state across the whole implementation stream - every Issue and MergeRequest it owns, plus `status.notes`, an append-only journal of plans, handoffs, and free-text continuation state every pod reads at turn 0.

```yaml
apiVersion: tatara.dev/v1alpha1
kind: Task
metadata:
  name: myproject-implement-2026-07-12-a3f9d  # <project>-<kind>-<date>-<uid5>
spec:
  projectRef: myproject
  kind: implement
  goal: "Support dark mode in the dashboard"
status:
  state: new
```

**Task state:** `new` - no pod runs here; the operator classifies the origin and, by `spec.kind`, drives the transition to the matching agent state.

---

## Step 3 - `implement` reads the issue

`new` transitions to `refined`, and the operator schedules a pod named `imp-myproject-<repo>-i<issue>` - a container running `tatara-claude-code-wrapper` with `tatara-cli` as its MCP server, `TATARA_KIND=implement`. The pod:

1. Clones the repository.
2. Reads the operator-rendered context bundle: the Issue, its comments, and any prior `status.notes` from an earlier pod on this same Task - built fresh every turn, there is no resume mode.
3. Loads the code knowledge graph from tatara-memory.
4. Presents everything to the agent and waits for a decision.

At this point `implement` has read-only access to the repository and issue - it does not write code yet. It is also a **live polling pod**: the operator re-spawns it on every new comment on an owned issue, not just once. See [Implement](../workflows/implement.md) for the full workflow.

---

## Step 4 - `implement` decides: should we do this?

The agent reads the issue and the codebase and calls `submit_outcome(action=...)`.

### Path A - rejected

The issue is out of scope, already fixed elsewhere, or not actionable. The agent calls `submit_outcome(action=rejected, reason=...)`. The operator posts the reason as a comment and closes the issue.

**Task state:** `rejected` - then the issue closes.

### Path B - discuss

The issue needs clarification, a design choice, or human input. The agent calls `submit_outcome(action=discuss, reason=...)`, posting its questions, and the pod tears down (no cost while waiting).

**Task state:** `refined` (waiting; pod-less until the next comment)

The Task keeps re-spawning `implement` on every new non-bot comment on the thread until a maintainer's comment satisfies the approval grammar (see Path C) or the idle budget (`ConversationIdleDefault`, 60m by default, once a pod is up) elapses with no approval, at which point the Task parks `awaiting-human`.

!!! danger "The agent judges meaning; only the operator's independent structural check grants anything"
    Even when `implement` itself concludes the issue is ready and calls
    `submit_outcome(action=approved, approval_citations=...)`, the agent's judgment is
    informational: the operator independently re-verifies each cited comment against the
    thread, plus the pinned plan note's hash. A citation whose comment does not exist, whose author is not a verified
    non-bot maintainer, or whose quote does not genuinely occur in the body the operator
    holds means no approval, regardless of what the agent decided. This applies
    uniformly - a bot-authored brainstorm proposal and a human-filed issue are gated
    identically; there is no fast path for either. See
    [Approval Gates](../operations/security/approval-gates.md#the-approval-grammar).

### Path C - approved

The agent decides this is worth building and calls `submit_outcome(action=approved, reason=..., plan_note_id=..., approval_citations=[{id, quote}, ...])`, citing who approved and why for every **live** Issue it owns that a maintainer has actually commented on - an Issue with no maintainer comment has nothing to cite and is not required to carry one; it either satisfies the `autoApproveTataraProposals` carve-out or refuses on its own. `plan_note_id` names the plan the operator will hash and re-check before any code is written. The operator independently re-reads its own mirror and checks, for every live Issue this Task owns that has a citation to check: does the cited comment exist, is its author in `maintainerLogins` and never the bot, does the quoted text genuinely occur in the comment body, and has it not already been consumed? There is no requirement that the cited comment be the thread's most recent maintainer comment - the agent, not the operator, is responsible for reading whether a later comment withdraws an earlier approval.

- **Every live owned Issue passes:** the operator stamps `Issue.status.approval` on each and moves `Task.status.state` to `under-implementation`.
- **Any live owned Issue fails:** the Task parks `identity-unverified` (HTTP 200, not an error, and nothing is posted to the issue thread - the refusal lives only in the operator's logs, notes and metrics). The next non-bot comment on any owned thread un-parks the Task, spawning a fresh `implement` pod that submits its own fresh citation.

**Task state:** `under-implementation` (only once the citation and plan-hash checks pass for every live owned Issue; otherwise `parked(identity-unverified)`)

---

## Step 5 - `implement` writes the code

Once the approval gate grants, `refined` transitions to `under-implementation` and the same pod - or its next turn on this Task - picks up the coding work (still `TATARA_KIND=implement`). It may work across every repo the Task owns MRs in - a run is not scoped to just the repo the issue was filed in.

**Task state:** `under-implementation`

The agent:

1. Re-reads the issue and its conversation thread from the context bundle.
2. Queries the code knowledge graph for relevant context.
3. Plans the change - for large or cross-repo work it tiers out sub-agents (see [subagent tiering](../workflows/implement.md#subagent-tiering)).
4. Writes code, commits, and pushes to branch `task/myproject-implement-2026-07-12-a3f9d`.
5. Calls `submit_outcome(action=submitted, title=..., body=..., change_significance=..., merge_order=[...])` - `change_significance` (`major`/`minor`/`patch`, driving the semver tag on push-CD repos) is required and, once set, can only be raised by a later reviewer, never lowered. `merge_order` is required whenever the Task's MRs span more than one repo.

The operator then opens the pull request, referencing the owned issue. If the agent instead calls `submit_outcome(action=declined, decline_reason=...)` - for example, the fix already shipped on a sibling branch - the Task parks `implement-declined` and no PR is opened.

Since [tatara-operator#594](https://github.com/szymonrychu/tatara-operator/pull/594), step 5's `submit_outcome` is itself gated: if the Task already owns an **open** MR with red CI, a real base conflict, or an unanswered `request_changes`, the call is refused outright with a structured `409` before anything is written, and the agent's prompt tells it to wait for its own pipeline (bounded ~20 minutes) and resubmit. See [CI readiness gate](../reference/merge-request.md#ci-readiness-gate-on-submission).

A pod that never becomes Ready within 5 minutes of creation is respawned automatically, uncapped (`maxPodRecreations` is deprecated with zero effect - see [Runbooks](../operations/runbooks.md#tatara-runbook-operator-agent-pod-recreation-loop)); a pod that runs past `agentPodTTLSeconds` is stopped with a guaranteed handoff note in `status.notes` and a fresh pod picks up the same Task. `maxTurnsPerPod` (`implement`'s per-pod turn cap, with `implement` itself exempt) and the Task-lifetime `maxTurnsPerTask` that used to bound that exemption are both deprecated with zero effect; what bounds a runaway Task now is the [24h residency cap](../reference/task-stages.md#residency-the-dead-man-switch).

---

## Step 6 - `under-implementation` -> `awaiting-review`

`submit_outcome(action=submitted)` with at least one owned MR open moves the Task straight to `awaiting-review` - there is no separate CI-polling state the Task sits in; CI status is read as part of the review and merge sequence itself.

**Task state:** `awaiting-review`

---

## Step 7 - `review` approves, the operator merges

There is no separate review Task. The same Task moves to `awaiting-review`, the implement pod is torn down, and a `review` pod comes up against the opened PR (see [PR / MR Review](../workflows/review.md)). It reads the diff read-only and calls `submit_outcome(verdict=...)`:

- **`verdict=approve`** - the outcome call writes nothing to the forge. It records a pending review on each owned `MergeRequest` CR, carrying the SHAs the agent reports having reviewed. The operator posts the review afterwards, on the next reconcile: a `COMMENT`-type review under the bot identity with the verdict in its body (GitHub 422s a self-authored `APPROVE` or `REQUEST_CHANGES` either way, since there is only one bot identity). Once every owned MR's pending review has landed, the Task moves to `merged`.
- **`verdict=request_changes`** - the Task returns to `under-implementation` with the review's findings as context. It is the **same Task**, with a fresh implement pod: the review pod is torn down, not run alongside. `maxReviewRounds` is deprecated with zero effect, so the `awaiting-review` to `under-implementation` cycle carries no round count at all (see the [residency cap](../reference/task-stages.md#residency-the-dead-man-switch) for the backstop that replaced it). The same review-`approve` outcome is also subject to the CI readiness gate above - a red or unresolved MR refuses the approval outright.

**Task state:** `awaiting-review` until a verdict lands, then `merged` or back to `under-implementation`.

At `merged` the operator walks `Task.spec.mergeOrder` sequentially: for each repo it re-reads the live head, merges only if it still matches `reviewedSHA` and CI is green, and sends the Task back to `awaiting-review` if the head moved underneath it (bounded by `headMoveReentries`, cap 3, parking at `head-moving`). See [Merge and Deploy](../workflows/merge-and-deploy.md#the-merge-sequence) for the full sequence - this is an **operator** action end to end; no MCP tool exposes merge, and auto-merge is never armed on a tatara-opened PR. <!-- stale-ok: auto-merge -->

---

## Step 8 - Deploy and delivery

Once every repo in `mergeOrder` is merged, `merged` moves to `deployed` - still pod-less. When every owned MR shows `merged` and the release has actually landed, the operator closes every owned issue with a citing comment and moves the Task to `done`.

**Task state:** `deployed`, then `done`.

`MergeRequest.status.significance` (set from the implement Task's `change_significance`, only ever raised by a reviewer) drives the semver tag the release job cuts. See [semver push-CD](../workflows/merge-and-deploy.md#semver-push-cd) for the tag-cut-to-cluster-apply chain.

Delivery is not documented per-change. A Task delivered in the last 24 hours becomes eligible for the **next nightly `documentation` batch Task** for its project, which covers everything delivered since the last run in one PR - not one documentation pipeline per merged change.

---

## Full sequence diagram

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant M as Maintainer
    participant GH as GitHub
    participant Op as tatara-operator
    participant Pod as Agent Pod
    participant Rev as review Pod

    Dev->>GH: Open issue #42
    GH-->>Op: webhook
    Op->>Op: mint Task (kind=implement), state=new
    Op->>Op: state=refined

    Note over Op,Pod: implement pod running (approval-gate turn)

    Op->>Pod: schedule implement pod
    Pod->>GH: fetch issue + comments
    Pod-->>Op: submit_outcome(action=approved, no citation yet)
    Op->>Op: verifyApprovalScope: no comment to cite; park(identity-unverified)

    M->>GH: Comment: "go ahead, I approve!"
    GH-->>Op: webhook (comment event)
    Op->>Op: sync comment mirror; un-park (no grant here)

    Note over Op,Pod: fresh implement pod running

    Op->>Pod: schedule implement pod
    Pod->>GH: fetch refreshed comments
    Pod-->>Op: submit_outcome(action=approved, plan_note_id=..., approval_citations=[{id, quote}])
    Op->>Op: verify M in maintainerLogins, quote occurs verbatim, not previously consumed, plan hash matches
    Op->>Op: Issue.status.approval stamped; state=under-implementation

    Note over Op,Pod: same pod (or its next turn) writes code

    Pod->>GH: clone repo, write code, commit, push
    Pod-->>Op: submit_outcome(action=submitted, change_significance=minor)
    Op->>GH: open PR
    Op->>Op: state=awaiting-review

    Op->>Rev: schedule review pod

    Note over Op,Rev: review pod running

    Rev->>GH: read PR diff (read-only)
    Rev-->>Op: submit_outcome(verdict=approve, reviewed_shas=[...])

    Op->>GH: read live PR head
    Op->>GH: post COMMENT review (verdict in body)
    Op->>Op: state=merged

    Op->>GH: Merge(expectedHeadSHA=reviewedSHA)
    Op->>Op: state=deployed
    Op->>GH: close issue #42, citing the release
    Op->>Op: state=done
```

---

## What to do when a Task is Parked

A Task parks (with a specific `parkReason`) when the operator cannot proceed without human input: the citation check found nothing to grant, the merge could not complete, CI never went green within the state deadline, or the agent explicitly declined. A park is not a failure and not a state - the Task stays exactly where it was and un-parks from there.

Not every park reason posts a comment explaining itself. `identity-unverified` posts **nothing** to the issue thread: the refusal is visible only in the operator's own logs, `Task.status.notes`, and the `operator_approval_refused_total` metric, never on the thread a maintainer is watching. Where the operator does post an explanatory comment for other park reasons, the [comment turn-taking gate](../operations/security/bot-identity.md) can still withhold a repeat one - for example, a Task that keeps parking on the same unanswered thread stops re-commenting after the first note - but for `identity-unverified` there was never a first note to begin with.

Your options:

- **Comment on the issue.** For `awaiting-human` or `identity-unverified`, any non-bot comment un-parks the Task, spawning a fresh agent pod to read it, as appropriate to the park reason.
- **Comment as a maintainer** to give the next `implement` pod something unambiguous to cite as approval - there is no configured phrase to match, only the agent's judgment and the operator's independent verification of that citation.
- **Fix the underlying problem** (a failing test, for example) and comment to resume; approval already recorded earlier in the same Task is not re-consumed.

You are not the only way out. Every park except `backlog-sweep` ages out on a 7-day retention window and is then reaped, and a Task parked under a reason nothing un-parks can be collected and its issue re-minted automatically at most three times before it becomes a real dead end sitting in the backlog with the `tatara-parked` label on it. Nothing waits indefinitely, and nothing retries forever.

---

## Where to go next

- [Watch One Run](watch-one-run.md) - this journey as one real nine-hour run, including the review round that sent the fix back and the rework it forced.
- [The Task state machine](../reference/task-stages.md) - every edge, guard, and park reason, in full.
- [The Agentic Operating Model](../concepts/agentic-model.md) - the model behind the walkthrough, including the two human gates and the security boundary.
