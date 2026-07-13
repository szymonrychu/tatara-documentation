---
title: Issue
description: The Issue CRD - tatara's mirror of one forge issue, its comments, and the single-use approval evidence pinned on it.
---

# Issue

An `Issue` CR is tatara's mirror of one issue on the forge (GitHub or GitLab).
It is namespaced, carries a status subresource, and its short name is `iss`.

```
apiVersion: tatara.dev/v1alpha1
kind: Issue
```

The CR name is `iss-<repositoryRef>-<number>` (for example
`iss-tatara-operator-291`). `<repositoryRef>` is the
[`Repository`](repository.md) CR name, which is already RFC-1123-safe - never
the raw `owner/repo` slug.

The operator also indexes Issues by `issueKey = <repositoryRef>#<number>` for
dedup lookups in the sweep and the `QueuedEvent` producer. That index is a
direct field lookup, never a label selector: label values reject `:` and `#`,
so a "natural key" encoded as a label would end up sha256-hashed back into an
opaque digest.

---

## IssueSpec

| Field | Type | Required | Description |
|---|---|---|---|
| `repositoryRef` | `string` | yes | Name of the owning `Repository` CR |
| `number` | `int` (min 1) | yes | The issue number on the forge |
| `url` | `string` | yes | The issue's URL on the forge |
| `projectRef` | `string` | yes | Name of the owning `Project` CR |

---

## IssueStatus

| Field | Type | Description |
|---|---|---|
| `title` | `string` | Issue title, last synced |
| `author` | `string` | Issue author's forge login |
| `body` | `string` (max 65536 chars) | Issue body, last synced |
| `createdAt` | `*Time` | When the issue was opened on the forge |
| `updatedAt` | `*Time` | Last forge-side update timestamp |
| `state` | `open` \| `closed` | **SCM truth** - mirrors the forge's own open/closed state |
| `status` | `new` \| `approved` \| `rejected` \| `done` | **The platform's decision state.** Operator-owned; see the danger note below |
| `labels` | `[]string` (max 50) | The issue's current labels, as synced from the forge |
| `comments` | [`[]Comment`](#comment) (max 200) | The most recent comments held in the CR |
| `commentCount` | `int` | `len(comments) + spilledComments` - a scalar the `Comments` print column can render, since kubebuilder cannot count a list |
| `spilledComments` | `int` | Count of oldest comments evicted to `tatara-memory` by the byte-budget guard |
| `spilledCommentsRefs` | `[]string` (max 50) | One `tatara-memory` track ID per spill batch. Accumulates - a single scalar ref would silently orphan every earlier batch on the second spill |
| `approval` | [`*ApprovalEvidence`](#approvalevidence) | Single-use evidence backing `status: approved`. `nil` means no verified approval |
| `commentsRetainedFrom` | `*Time` | The eviction watermark: the `createdAt` of the oldest comment still held in `comments`. The mirror sync ingests only comments newer than this, so an evicted comment is never re-fetched and re-spilled on the next sweep |
| `pendingComments` | `[]PendingComment` (max 20) | Durable comment/reply intents. `issue_write(action=comment\|edit\|close)` writes here and returns; the Issue reconciler drains it to the forge. `issue_write(action=create)` is synchronous instead, because the agent needs the new issue number back <!-- stale-ok: pendingComments --> |
| `lastSyncedAt` | `*Time` | When the mirror last synced against the forge |
| `conditions` | `[]metav1.Condition` | Standard Kubernetes conditions |

`PendingComment` has `requestId` (the client-supplied idempotency key),
`action` (`comment` \| `reply`), `body` (max 16384 chars), and `inReplyTo`
(optional). The same shape backs `MergeRequestStatus.pendingComments`. <!-- stale-ok: pendingComments -->
See [the review post](../workflows/merge-and-deploy.md#gitlab-has-no-review-object)
for how the reconciler drains a pending intent without double-posting.

!!! danger "`status` is operator-owned, and only the operator writes it"
    `Issue.status.status` is **operator-owned and webhook-driven only.** No MCP
    tool writes it. No agent-reachable REST endpoint writes it. **No cron or
    sweep path writes it.** A label read never produces it. See
    [Labels are write-only](../operations/security/approval-gates.md#labels-are-write-only).

Ownership: an Issue is owned by 1..N `Task`s, and exactly one carries
`controller: true` - the Task responsible for it right now. That owner is
what the `Task` print column renders. See
[Who owns what](../architecture/ownership.md#who-owns-what) for the full rule,
including how ownership moves during a [refine fold](../architecture/ownership.md#the-refine-fold).

---

## Comment

| Field | Type | Description |
|---|---|---|
| `externalId` | `string` | The provider's comment ID, always a string - GitHub uses an int64, GitLab a note ID, and the two disagree on width |
| `author` | `string` | Comment author's forge login |
| `body` | `string` (max 8192 bytes) | Comment body, **truncated at ingest** - see below |
| `createdAt` | `Time` | When the comment was posted |
| `isBot` | `bool` | `true` when `author == Project.spec.scm.botLogin` |
| `truncated` | `bool` | `true` when ingest cut `body` at 8192 bytes |
| `path` | `string` | File an inline review comment is anchored to. Empty for a plain comment |
| `line` | `int` | Line an inline review comment is anchored to. Zero when unset |
| `inReplyTo` | `string` | `externalId` of the review comment this one replies to |
| `reviewRound` | `int` | The review round this comment was posted in, when the operator authored it. Zero for every comment the operator did not author |

`isBot` is the **structural** bot exclusion both the
[approval grammar](../operations/security/approval-gates.md#the-approval-grammar)
and the sweep's `pendingEvents` enqueue filter rely on: it is set from
`Project.spec.scm.botLogin` at ingest, not inferred from comment content.

The 8192-byte truncation is not conservative for its own sake. GitHub allows
65,536-character comment bodies: 25 max-size comments alone is 1.6 MB, over
the etcd object ceiling on their own. A 64 KB comment is not prompt-useful
anyway. When ingest truncates, the rendered bundle carries `truncated="true"`
on the `<comment>` element, so the agent knows the text is partial and can
pull the full body with `scm_read(kind=comments)`.

---

## ApprovalEvidence

| Field | Type | Description |
|---|---|---|
| `login` | `string` | A verified maintainer login - never the bot |
| `commentId` | `string` | The `Comment.externalId` whose **text** matched an approval phrase |
| `createdAt` | `Time` | When the matching comment was posted |
| `phrase` | `string` | The matched entry from `Project.spec.scm.approvalPhrases` |
| `auto` | `bool` | Set on the `autoApproveTataraProposals` path. When `true`, `login` is the sentinel `<tatara:auto>` and `commentId` is empty |

`ApprovalEvidence` is **single-use**: a later approval must cite a newer
comment, and a replayed `commentId` is refused. `Issue.status.approval` being
`nil` means no verified approval exists, and the operator **fails closed** -
see [the approval grammar](../operations/security/approval-gates.md#the-approval-grammar)
for the full clause-by-clause rule this struct is evidence for.

---

## Print columns

| Column | Source |
|---|---|
| `Task` | `.metadata.ownerReferences[?(@.controller==true)].name` |
| `Repo` | `.spec.repositoryRef` |
| `Num` | `.spec.number` |
| `State` | `.status.state` |
| `Status` | `.status.status` |
| `Comments` | `.status.commentCount` |
| `Age` | standard |

---

## The mirror is a working set, not an archive

`Issue` and `MergeRequest` CRs are tatara's **mirror** of the forge.
`scm_read(kind=issues|mr|comments)` is served **from this mirror and never
touches the forge** - so the steady-state agent read cost of the platform is
zero forge requests, except `scm_read(kind=ci)`.

The mirror is at most one sweep behind. Every `scm_read` response carries
`lastSyncedAt` so an agent can **see** the staleness rather than assume
freshness. There is deliberately **no `refresh=true` escape hatch**: adding
one would hand every agent a forge-fanout button. The one path where
staleness is dangerous - `refine` closing an issue - re-validates live
immediately before each close.

The mirror is garbage-collected on delivery. It is not a permanent copy of
your tracker.

---

## See also

- [MergeRequest](merge-request.md) - the mirror's other half, and the merge/deploy path
- [Task](task.md) - the CRD that owns Issues and drives them through the stage machine
- [Task stages](task-stages.md) - the stage machine `status.status` feeds into
- [Approval gates](../operations/security/approval-gates.md) - the full comment-text approval grammar and why labels are write-only
- [Ownership](../architecture/ownership.md) - who owns an Issue, and how ownership moves
