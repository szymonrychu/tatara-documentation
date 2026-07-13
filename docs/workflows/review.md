---
title: PR / MR Review Workflow
---

# PR / MR Review Workflow

`review` reads a diff and submits a verdict. It is the only writer of the review that is ever
posted to the forge, and the **only** thing it is ever posted as is a `COMMENT` - GitHub 422s
the platform's own bot identity on both `APPROVE` and `REQUEST_CHANGES` for a self-authored PR
(only `COMMENT` is permitted), so the decision that actually moves the Task lives in
`submit_outcome`, not in the forge's review-decision field. See
[Merge and Deploy](merge-and-deploy.md#the-merge-sequence) for what the operator does with an
accepted verdict.

## Trigger

Two independent paths feed `review` Tasks against a human's PR:

1. **Webhook:** a pull request is opened or synchronized on an enrolled repository.
2. **`mrScan` cron:** a periodic scan that lists open PRs/MRs on enrolled repos and picks up any
   candidate the webhook missed, so a dropped or delayed webhook is not a permanent gap.

Both paths apply the same scope check via `spec.scm.prReactionScope`:

| Value | Behavior |
|---|---|
| Empty / unset (**the default**) | Reacts to **every** open human PR/MR - the historical, permissive behavior |
| `labeledOrMentioned` | Only PRs with the `triggerLabel` OR that mention the bot |
| `all` | Every PR in every enrolled repository (equivalent to the unset default, explicit) |

!!! warning "Default is permissive, not `labeledOrMentioned`"
    The default `prReactionScope` is empty, which reviews every open PR/MR. Set
    `prReactionScope: labeledOrMentioned` explicitly to restrict review to labeled/mentioned PRs.

The PR author must not be the bot itself: a `review`-kind Task (`spec.kind: review`) is always
opened against **someone else's** PR, by construction. The platform's own MRs are reviewed at
the `reviewing` stage of the Task that opened them - a different stage of a `clarify`- or
`refine`-originated Task, never a `review`-kind Task.

## Re-review dedup

`mrScan` (and the webhook path) suppress re-review of a PR/MR whose head commit has not changed
since the last completed review. Same head + a terminal review Task already exists on that PR ->
suppressed. A new commit pushed to the PR changes the head SHA -> re-review proceeds.

## Read-only constraint

The review agent **never pushes and never merges, and never calls a merge API** - no MCP tool
exposes one to any agent kind. The PR head is checked out in `/workspace` read-only. Review's
only writeback actions are `mr_write(comment)` / `mr_write(reply)` - replying to a human's inline
thread - plus `submit_outcome` itself, which the operator turns into the posted review and,
where applicable, the merge.

## `submit_outcome`

```json
{"verdict":"approve","reviewed_shas":[{"repo":"tatara-operator","number":295,"sha":"a3f912c..."}]}
```
or
```json
{"verdict":"request_changes",
 "reviewed_shas":[{"repo":"tatara-operator","number":295,"sha":"a3f912c..."}],
 "change_significance":"minor",
 "findings":[{"repo":"tatara-operator","number":295,"path":"internal/x.go","line":42,
              "body":"...","severity":"high"}]}
```

- **`reviewed_shas` is required on both verdicts** - the exact head SHA the agent actually
  checked out and read, per MR. The operator re-reads the **live** head at acceptance and
  refuses the verdict if it moved while the agent was reviewing, so anything pushed after
  checkout never merges unreviewed under this approval.
- **`findings` is required (at least one) when `verdict=request_changes`.** The **operator**
  posts these as the SCM review and its inline comments - the agent does not post them itself.
  `mr_write` has no `approve` and no `request_changes` action; review keeps `mr_write` for
  `comment`/`reply` only, so it can still reply on a human's inline thread.
- **`change_significance` is optional and may only escalate** the level `implement` already
  declared - `max(implement, review)` over `patch < minor < major`. A lower value is ignored and
  logged WARN.
- **There is no `comment` verdict.** A review either approves (merge proceeds, on the platform's
  own MRs) or requests changes (loop back to `implementing`). A non-decision has no stage to go
  to.

## Transitions

| From | Outcome | To |
|---|---|---|
| `reviewing` | `verdict=approve`, `spec.kind != review` | `merging` - operator posts a `COMMENT` review from the verdict, then merges (gated on `pendingReview == nil`) |
| `reviewing` | `verdict=request_changes`, `spec.kind != review`, `reviewRounds < maxReviewRounds` (3) | `implementing` |
| `reviewing` | `verdict=request_changes` at `maxReviewRounds` | `parked(review-loop-exhausted)` |
| `reviewing` | either verdict, `spec.kind == review` | `parked(awaiting-human)` - see the carve-out below |

## The `review`-kind carve-out

On a **`review`-kind Task** - one minted for a *human's* PR, never the platform's own - **neither
verdict advances the Task.** `approve` parks it at `awaiting-human`, because merging a human's PR
is a human action. `request_changes` **also** parks it at `awaiting-human`, because a human's PR
is fixed by the human, not by an implement pod spawned against code the platform did not write.
The review is posted either way - the operator still runs `PostReview(COMMENT)` from the verdict.

**A `review`-kind Task never spawns an implement pod and never reaches `merging` - there is no
edge, by any path.** This holds regardless of which verdict comes back: `request_changes` is the
review agent's *normal* verdict on a bad human PR, so it is the primary path that has to be
closed, not an edge case. The next human comment on the thread un-parks the Task back to
`reviewing`, bounded by `status.humanReviewRounds` (cap 5, a separate counter from
`reviewRounds`, which only advances on `request_changes`) - so a chatty thread cannot spawn an
unbounded run of review pods.

## Conversation persistence for reviews

Each PR gets its own conversation, distinct from any related issue's conversation. If the PR is
synchronized (new commits pushed), the next review turn resumes from the prior conversation,
giving the agent context about what it already reviewed.

## Budget and cycle caps

`reviewing` carries a 4h stage-work budget; on elapse the Task parks at
`parked(stage-deadline)`. The `reviewing <-> implementing` loop is capped at `maxReviewRounds`
(3), tracked on the MergeRequest as `reviewRounds` - it increments only on `request_changes`, so
the approve path never advances it. See [Merge and Deploy](merge-and-deploy.md#budgets-and-the-bounded-cycles)
for the merge-side cycle caps that pick up once a verdict reaches `merging`.
