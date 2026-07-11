---
title: Clarify Workflow
---

# Clarify Workflow

`clarify` replaces the triage/human-conversation front half of the old `issueLifecycle` state
machine with a discrete, single-purpose kind. It is the entry point for both a brand-new issue
and any comment on an existing one - the same kind handles both, distinguished by which case
triggered it.

## 1. Trigger

Either of:

1. **New issue** on an enrolled repository.
2. **Any comment** on an existing issue that is part of a live Task's umbrella.

On a new issue, clarify creates the project-scoped Task CR, runs a targeted mini-brainstorm pass
over the issue's own scope, digests the human's issue body, and asks clarifying questions via
the normal issue-comment channel - the technical conversation happens on the issue thread, the
same SCM-native mechanism the platform has always used.

On a comment, clarify reads the existing Task CR (or retro-creates it from full SCM history if
none exists yet - e.g. a comment on an issue tatara never triaged) and either answers back and
waits, or launches `implement` and exits.

## 2. Full context, always

Every clarify pod starts with the complete umbrella context: all linked issues and their full
comment threads, all repos in scope at their newest default branches. This is the same turn-0
context bundle every kind gets under the Task umbrella (see
[Task reference](../reference/task.md#task-umbrella-and-the-workitem-ledger)) - clarify may
check out branches or MRs if the conversation needs to reference in-flight code.

## 3. Live polling pod

Clarify is a **live polling pod**: once it posts a question or a partial plan, the operator
keeps it alive for up to **1 hour wall-clock**, delivering new comments to the running session
via the existing `PendingInterjections` mechanism rather than tearing the pod down and
re-spawning on every reply. If the hour elapses with no reply, the operator kills the pod - the
issue is left exactly where the conversation stopped, resumable by a future comment (which
re-spawns a fresh clarify pod against the same Task).

## 4. Handoff to implement

Clarify's own judgment that the conversation has reached an implement-ready state is
**not** sufficient to hand off. When clarify calls `issue_outcome(action=implement)`, the
operator checks whether a verified maintainer approval has already been recorded on the
Task (`Status.ApprovedByMaintainer`). That fact is set exclusively by a maintainer
applying the `tatara-approved` label directly to the issue - never by a comment, never by
clarify's own verdict, and never by a non-maintainer or bot applying the label. See
[Approval Gates](../operations/security/approval-gates.md#gate-2-maintainer-approval-label-who-can-approve-implementation)
for the full mechanism.

- **Approval already recorded:** clarify removes `tatara-brainstorming` and adds
  `tatara-implementation` on the issue - the label swap the operator watches to spawn an
  `implement` pod.
- **No recorded approval:** the `implement` verdict is downgraded. The issue stays on
  `tatara-brainstorming` and clarify keeps polling (or the pod is killed on the 1-hour
  idle timeout, resumable by a future comment or by the maintainer applying the label
  directly). Clarify never writes code, and it never grants itself the approval it is
  waiting on.

A maintainer can apply `tatara-approved` at any point - even before clarify reaches an
implement-ready verdict - and the operator records the approval as soon as the webhook
event arrives, independent of clarify's own conversational state.

!!! warning "Clarify cannot answer its own comments"
    This guard lives in the permission layer, not skill prose: the MCP comment action refuses
    to post when the last comment on the thread is bot-authored, and the webhook actor-check
    plus mention-check refuse to (re)spawn clarify off a bot-authored comment. The sole
    exception is `refine`, which is allowed to comment under tatara's own previous comment for
    scope-change or already-delivered notices - see [Refine](refine.md).

## Reference: labels

| Label | Applied when |
|---|---|
| `tatara-brainstorming` | Clarify is actively conversing (question posted, awaiting reply) |
| `tatara-approved` | Applied **only** by a maintainer, directly on the issue - the sole action the operator records as approval; clarify never applies this label itself |
| `tatara-implementation` | Clarify hands off; an `implement` pod is about to spawn |
| `tatara-declined` | Clarify determines the issue should not be implemented |

See [Approval Gates](../operations/security/approval-gates.md) for the full maintainer-approval
gate governing every issue, bot-authored or human-filed alike.
