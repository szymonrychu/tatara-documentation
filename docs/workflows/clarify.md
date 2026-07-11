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

When the conversation reaches an implement-ready state, clarify removes `tatara-brainstorming`
and adds `tatara-implementation` on the issue - the label swap the operator watches to spawn an
`implement` pod. Clarify itself never writes code.

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
| `tatara-approved` | A human (or clarify itself, once satisfied) signals implement-ready |
| `tatara-implementation` | Clarify hands off; an `implement` pod is about to spawn |
| `tatara-declined` | Clarify determines the issue should not be implemented |

See [Approval Gates](../operations/security/approval-gates.md) for the self-approve guard
governing bot-authored (brainstorm-originated) issues.
