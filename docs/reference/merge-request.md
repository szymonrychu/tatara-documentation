---
title: MergeRequest
description: The MergeRequest CRD - tatara's mirror of one forge pull/merge request, the crash-safe review-post pipeline, and the operator-only merge path.
---

# MergeRequest

A `MergeRequest` CR is tatara's mirror of one pull request (GitHub) or merge
request (GitLab). It is namespaced, carries a status subresource, and its
short name is `mr`.

```
apiVersion: tatara.dev/v1alpha1
kind: MergeRequest
```

The CR name is `mr-<repositoryRef>-<number>`, where `<repositoryRef>` is the
[`Repository`](repository.md) CR name, never the raw `owner/repo` slug - the
same convention [`Issue`](issue.md) uses.

The operator indexes MergeRequests by `mrKey = <repositoryRef>!<number>` for
dedup lookups in the sweep and the `QueuedEvent` producer - a direct field
lookup, never a label selector.

---

## MergeRequestSpec

| Field | Type | Required | Description |
|---|---|---|---|
| `repositoryRef` | `string` | yes | Name of the owning `Repository` CR |
| `number` | `int` (min 1) | yes | The PR/MR number on the forge |
| `url` | `string` | yes | The PR/MR's URL on the forge |
| `projectRef` | `string` | yes | Name of the owning `Project` CR |

---

## MergeRequestStatus

| Field | Type | Description |
|---|---|---|
| `title` | `string` | PR/MR title, last synced |
| `author` | `string` | PR/MR author's forge login |
| `body` | `string` (max 65536 chars) | PR/MR body, last synced |
| `createdAt` | `*Time` | When the PR/MR was opened |
| `updatedAt` | `*Time` | Last forge-side update timestamp |
| `state` | `open` \| `merged` \| `closed` | SCM truth |
| `status` | `new` \| `approved` \| `needs-changes` \| `rejected` | **The platform's review state.** Operator-owned, written only from an accepted review `submit_outcome` |
| `headBranch` | `string` | The PR/MR's source branch <!-- stale-ok: headBranch --> |
| `headSHA` | `string` | The mirror's last-synced head commit. **Never trusted for a merge or an approval decision** - see below |
| `ciStatus` | `none` \| `pending` \| `running` \| `green` \| `red` | Last-synced CI status at `headSHA` |
| `mergeable` | `bool` | Whether the forge reports the PR/MR as mergeable |
| `comments` | [`[]Comment`](issue.md#comment) (max 200) | Same `Comment` shape as `Issue`, including the inline-review fields (`path`, `line`, `inReplyTo`, `reviewRound`) |
| `commentCount` | `int` | `len(comments) + spilledComments` |
| `spilledComments` | `int` | Count of oldest comments evicted to `tatara-memory`. A PR with five review rounds of thirty inline findings each is this platform's own normal output, so MergeRequest needs the same spill guard Issue has |
| `spilledCommentsRefs` | `[]string` (max 50) | One `tatara-memory` track ID per spill batch, accumulating exactly like `Issue.status.spilledCommentsRefs` |
| `commentsRetainedFrom` | `*Time` | The eviction watermark, same contract as `Issue.status.commentsRetainedFrom` |
| `mergedAt` | `*Time` | When the operator merged this PR/MR |
| `deployedAt` | `*Time` | When the merged change finished deploying |
| `deployedVersion` | `string` | The tag the release pipeline cut and deployed <!-- stale-ok: deployedVersion --> |
| `significance` | `major` \| `minor` \| `patch` | **Implement-owned.** See below |
| `reviewedSHA` | `string` | The **live** head SHA read at the moment a review outcome was accepted |
| `reviewRounds` | `int` | Count of accepted `request_changes` verdicts on this PR/MR. See below |
| `pendingReview` | [`*PendingReview`](#pendingreview) | The durable intent to post a review. See below |
| `pendingComments` | `[]PendingComment` (max 20) | Durable comment/reply intents from `mr_write(action=comment\|reply)`. Same shape as [`Issue.status.pendingComments`](issue.md#issuestatus) <!-- stale-ok: pendingComments --> |
| `lastSyncedAt` | `*Time` | When the mirror last synced against the forge |
| `ownership` | `tatara` \| `external` | Whether the platform has push/merge agency over this MR. See [MR Ownership](../architecture/ownership.md#mr-ownership) |
| `ownershipReason` | `string` | The reason for the current ownership state: `initial` (never delegated), `takeover-requested-by:<user>`, or `external-push:<sha>` |
| `ownershipChangedAt` | `*Time` | When ownership last flipped |
| `lastBotHeadSHA` | `string` | The last head commit SHA this platform pushed; compared to the current head to detect human pushes and ownership changes |
| `lastMirroredCommentID` | `string` | Cursor for sweep comment syncing; the external ID of the last bot comment mirrored |
| `conditions` | `[]metav1.Condition` | Standard Kubernetes conditions |

**Kubernetes ownership** (who owns/deletes this CR): a MergeRequest is owned by 1..N `Task`s,
and exactly one carries `controller: true` - the Task responsible for it right now. That owner
is what the `Task` print column renders. See [Who owns what](../architecture/ownership.md#who-owns-what)
for the full rule, including how ownership moves during a [refine fold](../architecture/ownership.md#the-refine-fold).

**MR ownership** (push/merge agency): distinct from Kubernetes ownership above. Whether the
platform has the authority to push commits and merge. Tracked in `status.ownership` and
`status.ownershipReason`. See [MR Ownership](../architecture/ownership.md#mr-ownership).

### `headSHA` is never trusted

`headSHA` is the mirror's last-synced head, and the mirror is up to one
hourly sweep stale. **Both the merge and the approval re-fetch the head
live.** `reviewedSHA` - the live head read at the moment the review outcome
was accepted - is what the merge step passes as the expected head, and a 409
from the forge means the head moved underneath the review.

### `significance` is implement-owned

It is written once, from the implement Task's `submit_outcome`. A review
outcome may only **escalate** it, over the ordering `patch < minor < major`.
An attempt to lower it is ignored and logged at WARN. The in-cluster reviewer
is documented-flaky and must never be able to downgrade a major release to a
patch.

### `reviewRounds`

Counts **accepted** `request_changes` verdicts. At
`Project.spec.agent.maxReviewRounds` (default 3) the Task parks with
`stageReason=review-loop-exhausted`.

### `pendingReview` is the crash-safety intent record

`/outcome` persists it - body, findings, the verified SHA, and
`round = reviewRounds + 1` as the idempotency key - and returns before any
forge call. A separate reconciler does the actual review post, first
checking the forge itself for an existing post carrying that `(round, sha)`
marker before posting. On a marker hit it still **reconciles the mirror**
(it fetches the already-posted comment IDs and set-unions them in) - it skips
only the forge write, never the mirror append. `pendingReview` is cleared
last. The stage machine is gated on `pendingReview == nil`, so a pod can
never be spawned to fix findings that have not been recorded yet.

The marker itself lives on the forge and is written last, so it can only
ever mean "everything for this round landed" - see
[the GitLab ordering rule](../workflows/merge-and-deploy.md#gitlab-has-no-review-object).

---

## PendingReview

| Field | Type | Description |
|---|---|---|
| `body` | `string` (max 16384 chars) | The review body. The event is always `COMMENT` - there is no `event` field on the wire, since `APPROVE` and `REQUEST_CHANGES` are both blocked by the forge on a self-authored PR |
| `findings` | [`[]ReviewFinding`](#reviewfinding) (max 30) | Inline findings for this round |
| `sha` | `string` | The head this review was made against - equals `MergeRequest.status.reviewedSHA` once accepted |
| `round` | `int` | The idempotency key. It appears both in the forge marker (`<!-- tatara-review round=N sha=... -->`) and on every `Comment.reviewRound` this round produces, so a crash between the forge post and the mirror append cannot double-post |

## ReviewFinding

| Field | Type | Description |
|---|---|---|
| `path` | `string` | File the finding is anchored to |
| `line` | `int` (min 1) | Line the finding is anchored to |
| `body` | `string` (max 8192 chars) | Finding text |
| `severity` | `critical` \| `high` \| `medium` \| `low` | Finding severity |

The 30-item cap on `findings` is load-bearing: thirty findings times an
unbounded body is a byte-budget input the write guard cannot evict, since it
is spec-adjacent intent rather than an evictable comment.

---

## See also

- [Issue](issue.md) - the mirror's other half, and the shared `Comment` and `PendingComment` shapes
- [Issue: the mirror is a working set, not an archive](issue.md#the-mirror-is-a-working-set-not-an-archive) - staleness and the missing `refresh=true` escape hatch apply here too
- [Merge and deploy](../workflows/merge-and-deploy.md) - the full merge sequence, merge order, and the GitLab review-post ordering
- [Task stages](task-stages.md) - `reviewing`, `merging`, and `deploying` in the stage machine
- [Ownership](../architecture/ownership.md) - who owns a MergeRequest, and how ownership moves
