---
title: From Issue to PR
---

# From Issue to PR

*Follow one GitHub issue through the whole tatara machine - from the moment you click "Submit" to the moment the change is merged and deployed.*

The single source of truth for where a piece of work stands is `Task.status.stage`, a Kubernetes-native field you can watch with `kubectl get tasks`. The operator also projects a small, per-project-configurable set of labels onto the SCM issue as a read-only mirror of `Issue.status.status` and `Task.status.stage` - so you do not need cluster access to follow along - but the labels are a **one-way projection**, never a control input. Nothing in the operator ever reads a label to decide what happens next; a Task's status changes only from the transition table, driven by `submit_outcome` calls and comment-grammar checks. One label you will see referenced by name throughout the platform's docs and runbooks is `tatara-parked`, applied whenever a Task is in any `parked` reason - see [Approval Gates](../operations/security/approval-gates.md#labels-are-write-only) for the full projection rule.

---

## Step 1 - You open an issue

You create a GitHub issue in any repository enrolled in your tatara Project. The title and body are your only inputs; tatara reads them verbatim.

**Task stage:** *(none yet)*

If `Project.spec.scm.reporterLogins` is populated, the issue author must be the bot, a maintainer, or an allowed reporter, or the event is dropped at intake. Left empty (the shipped default), any author's issue is accepted. Either way, opening an issue does not itself grant anything - it only gets a Task minted.

This page follows the full path - issue opened, `clarify` runs first.

---

## Step 2 - The operator mints a Task

The operator sees the new open issue in an enrolled repository, mirrors it as an `Issue` custom resource, and mints a `Task` custom resource with `spec.kind: clarify` that owns it. The Task is the durable, project-scoped unit that carries state across the whole implementation stream - every Issue and MergeRequest it owns, plus `status.notes`, an append-only journal of plans, handoffs, and free-text continuation state every pod reads at turn 0.

```yaml
apiVersion: tatara.dev/v1alpha1
kind: Task
metadata:
  name: myproject-clarify-2026-07-12-a3f9d  # <project>-<kind>-<date>-<uid5>
spec:
  projectRef: myproject
  kind: clarify
  goal: "Support dark mode in the dashboard"
status:
  stage: triaging
```

**Task stage:** `triaging` - no pod runs here; the operator classifies the origin and, by `spec.kind`, drives the transition to the matching agent stage.

---

## Step 3 - `clarify` reads the issue

`triaging` transitions to `clarifying`, and the operator schedules a pod named `myproject-clarify-2026-07-12-a3f9d-clarify` - a container running `tatara-claude-code-wrapper` with `tatara-cli` as its MCP server, `TATARA_KIND=clarify`. The pod:

1. Clones the repository.
2. Reads the operator-rendered context bundle: the Issue, its comments, and any prior `status.notes` from an earlier pod on this same Task - built fresh every turn, there is no resume mode.
3. Loads the code knowledge graph from tatara-memory.
4. Presents everything to the agent and waits for a decision.

`clarify` has read-only access to the repository and issue at this stage - it does not write code. It is also a **live polling pod**: the operator re-spawns it on every new comment on an owned issue, not just once. See [Clarify](../workflows/clarify.md) for the full workflow.

---

## Step 4 - `clarify` decides: should we do this?

The agent reads the issue and the codebase and calls `submit_outcome(decision=...)`.

### Path A - close

The issue is out of scope, already fixed elsewhere, or not actionable. The agent calls `submit_outcome(decision=close, reason=...)`. The operator posts the reason as a comment and closes the issue.

**Task stage:** `rejected` - then the issue closes.

### Path B - discuss

The issue needs clarification, a design choice, or human input. The agent calls `submit_outcome(decision=discuss, reason=...)`, posting its questions, and the pod tears down (no cost while waiting).

**Task stage:** `clarifying` (waiting; pod-less until the next comment)

The Task keeps re-spawning `clarify` on every new non-bot comment on the thread until a maintainer's comment satisfies the approval grammar (see Path C) or the 24-hour `clarifying` budget elapses with no approval, at which point the Task parks `awaiting-human`.

!!! danger "Comments never approve on the agent's own say-so - only the operator's independent grammar check does"
    Even when `clarify` itself concludes the issue is implement-ready and calls
    `submit_outcome(decision=implement)`, that is informational: the operator
    independently re-evaluates the approval grammar against the thread. No line in the
    most recent maintainer comment matching one of `Project.spec.scm.approvalPhrases`
    means no approval, regardless of what the agent decided. This applies uniformly - a
    bot-authored brainstorm proposal and a human-filed issue are gated identically; there
    is no fast path for either. See
    [Approval Gates](../operations/security/approval-gates.md#the-approval-grammar).

### Path C - implement

The agent decides this is worth building and calls `submit_outcome(decision=implement, reason=...)`, citing who approved and where. The operator independently re-reads the thread and checks, for **every** Issue this Task owns that is still open: does its most recent maintainer comment (author in `maintainerLogins`, never the bot) consist of a line matching an entry in `Project.spec.scm.approvalPhrases`, anchored and whole-line, not previously consumed?

- **Every owned Issue passes:** the operator stamps `Issue.status.approval` on each and moves `Task.status.stage` to `approved`.
- **Any owned Issue fails:** the Task parks `identity-unverified`. The grammar is re-evaluated on every subsequent non-bot comment on any owned thread until it passes or the park ages out.

**Task stage:** `approved` (only once the grammar passes for every owned Issue; otherwise `parked(identity-unverified)`)

---

## Step 5 - `implement` writes the code

Once admitted from the queue, `approved` transitions to `implementing` and a pod spawns (`...-implement`, `TATARA_KIND=implement`). It may work across every repo the Task owns MRs in - a run is not scoped to just the repo the issue was filed in.

**Task stage:** `implementing`

The agent:

1. Re-reads the issue and its conversation thread from the context bundle.
2. Queries the code knowledge graph for relevant context.
3. Plans the change - for large or cross-repo work it tiers out sub-agents (see [subagent tiering](../workflows/implement.md#subagent-tiering)).
4. Writes code, commits, and pushes to branch `task/myproject-clarify-2026-07-12-a3f9d`.
5. Calls `submit_outcome(action=submitted, title=..., body=..., change_significance=..., merge_order=[...])` - `change_significance` (`major`/`minor`/`patch`, driving the semver tag on push-CD repos) is required and, once set, can only be raised by a later reviewer, never lowered. `merge_order` is required whenever the Task's MRs span more than one repo.

The operator then opens the pull request, referencing the owned issue. If the agent instead calls `submit_outcome(action=declined, decline_reason=...)` - for example, the fix already shipped on a sibling branch - the Task parks `implement-declined` and no PR is opened.

A pod that never becomes Ready within 5 minutes of creation is respawned automatically, not failed, up to `maxPodRecreations` (default 3); a pod that runs past `agentPodTTLSeconds` is stopped with a guaranteed handoff note in `status.notes` and a fresh pod picks up the same Task. `implement` is the one agent kind exempt from `maxTurnsPerPod` - a long healthy coding run is not cut off mid-work, bounded instead by the Task-lifetime `maxTurnsPerTask` (default 300).

---

## Step 6 - `implementing` -> `reviewing`

`submit_outcome(action=submitted)` with at least one owned MR open moves the Task straight to `reviewing` - there is no separate CI-polling stage the Task sits in; CI status is read as part of the review and merge sequence itself.

**Task stage:** `reviewing`

---

## Step 7 - `review` approves, the operator merges

A `review` pod spawns against the opened PR (see [PR / MR Review](../workflows/review.md)). It reads the diff read-only and calls `submit_outcome(verdict=...)`:

- **`verdict=approve`** - the operator reads the **live** PR head SHA (never the mirror), posts a `COMMENT`-type review under the bot identity carrying the verdict (GitHub 422s a self-authored `APPROVE` or `REQUEST_CHANGES` either way - there is only one bot identity), stamps `MergeRequest.status.reviewedSHA`, and moves the Task to `merging`.
- **`verdict=request_changes`** - the Task returns to `implementing` with the review's findings as context, bounded by `maxReviewRounds` (default 3; beyond it the Task parks `review-loop-exhausted`).

**Task stage:** `reviewing` until a verdict lands, then `merging` or back to `implementing`.

At `merging` the operator walks `Task.spec.mergeOrder` sequentially: for each repo it re-reads the live head, merges only if it still matches `reviewedSHA` and CI is green, and sends the Task back to `reviewing` if the head moved underneath it (bounded by `headMoveReentries`, cap 3, failing at `head-moving`). See [Merge and Deploy](../workflows/merge-and-deploy.md#the-merge-sequence) for the full sequence - this is an **operator** action end to end; no MCP tool exposes merge, and auto-merge is never armed on a tatara-opened PR. <!-- stale-ok: auto-merge -->

---

## Step 8 - Deploy and delivery

Once every repo in `mergeOrder` is merged, `merging` moves to `deploying` - still pod-less. When every owned MR shows `merged` and the release has actually landed, the operator closes every owned issue with a citing comment and moves the Task to `delivered`.

**Task stage:** `deploying`, then `delivered`.

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
    Op->>Op: mint Task (kind=clarify), stage=triaging
    Op->>Op: stage=clarifying

    Note over Op,Pod: clarify pod running

    Op->>Pod: schedule clarify pod
    Pod->>GH: fetch issue + comments
    Pod-->>Op: submit_outcome(decision=implement)
    Op->>Op: independently re-check the approval grammar (not yet satisfied)

    M->>GH: Comment: "go ahead"
    GH-->>Op: webhook (comment event)
    Op->>Op: verify M in maintainerLogins, phrase matches, not previously consumed
    Op->>Op: Issue.status.approval stamped; stage=approved

    Op->>Op: admitted; stage=implementing

    Note over Op,Pod: implement pod running

    Op->>Pod: schedule implement pod
    Pod->>GH: clone repo, write code, commit, push
    Pod-->>Op: submit_outcome(action=submitted, change_significance=minor)
    Op->>GH: open PR
    Op->>Op: stage=reviewing

    Op->>Rev: schedule review pod

    Note over Op,Rev: review pod running

    Rev->>GH: read PR diff (read-only)
    Rev-->>Op: submit_outcome(verdict=approve, reviewed_shas=[...])

    Op->>GH: read live PR head
    Op->>GH: post COMMENT review (verdict in body)
    Op->>Op: stage=merging

    Op->>GH: Merge(expectedHeadSHA=reviewedSHA)
    Op->>Op: stage=deploying
    Op->>GH: close issue #42, citing the release
    Op->>Op: stage=delivered
```

---

## What to do when a Task is Parked

A Task enters `parked` (with a specific `stageReason`) when the operator cannot proceed without human input: the approval grammar failed, the review-round cap was hit, CI never went green within the stage deadline, or the agent explicitly declined. The operator posts a comment explaining what stopped it, unless the [comment turn-taking gate](../operations/security/bot-identity.md) withholds it - e.g. a Task that keeps parking on the same unanswered thread stops re-commenting after the first note.

Your options:

- **Comment on the issue.** For `awaiting-human` or `identity-unverified`, any non-bot comment re-evaluates the relevant grammar or re-enters `clarifying`, as appropriate to the stage reason.
- **Post an approval phrase** (a maintainer account, matching `Project.spec.scm.approvalPhrases`) to record approval directly.
- **Fix the underlying problem** (e.g., a failing test) and comment to resume; approval already recorded earlier in the same Task is not re-consumed.

Every `parked` reason except `backlog-sweep` ages out on its own retention window (7 days by default) and is reaped after a final comment - it does not wait indefinitely.
