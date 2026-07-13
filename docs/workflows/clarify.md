---
title: Clarify Workflow
---

# Clarify Workflow

`clarify` is the entry point for both a brand-new issue and any comment on an existing one - the
same kind handles both, distinguished by which case triggered it. Its job is conversation and
scope, never code: it is the only kind whose `submit_outcome` can move a Task into `approved`,
and the only agent whose judgment the operator explicitly does **not** trust unverified - see
[the approval gate](#4-the-approval-gate-clarify-proposes-the-operator-verifies) below.

## 1. Trigger

Either of:

1. **New issue** on an enrolled repository - the operator mints a project-scoped Task with
   `kind: clarify` and enters it at `clarifying`.
2. **Any comment** on an existing issue that is part of a live Task's umbrella.

On a new issue, clarify digests the human's issue body and asks clarifying questions via the
normal issue-comment channel - the technical conversation happens on the issue thread, the same
SCM-native mechanism the platform has always used. On a comment, clarify reads the existing Task
CR and either answers back and waits, or submits `decision=implement` and exits.

## 2. Full context, always

Every clarify pod starts with the complete umbrella context: every Issue and MergeRequest the
Task owns, their full comment threads, and every repo in scope at its newest default branch -
the same [context bundle](../reference/context-bundle.md) every agent kind gets. Clarify may
check out branches or MRs if the conversation needs to reference in-flight code.

## 3. Live polling pod

Clarify is a **live polling pod**: once it posts a question or a partial plan, the operator
keeps it alive for up to **1 hour wall-clock**, delivering new comments to the running session
rather than tearing the pod down and re-spawning on every reply. If the hour elapses with no
reply, the operator stops the pod - the issue is left exactly where the conversation stopped,
resumable by a future comment (which re-spawns a fresh clarify pod against the same Task). This
is bounded by the `clarifying` stage's own 24h budget: past that, the Task parks at
`parked(awaiting-human)` regardless of how many short polling windows it has been through.

## 4. The approval gate: clarify proposes, the operator verifies

The pod's only path forward is `submit_outcome`:

```json
{"decision":"implement","reason":"szymonrychu commented \"go ahead\" on tatara-operator#291"}
```
or `decision: close` or `decision: discuss`. `reason` is **required on every decision** - for
`decision=implement` it must cite **who** approved and **where**, because the operator does not
take the agent's word for it: it independently re-reads the live thread and re-runs the
[approval grammar](../operations/security/approval-gates.md#the-approval-grammar) - maintainer
identity, an anchored whole-line phrase, single-use evidence - against every live Issue the Task
owns. **The agent's report of approval is not approval.** Clarify never writes code, and it
never grants itself the approval it is waiting on.

| From | Outcome | To |
|---|---|---|
| `clarifying` | `decision=implement`, grammar passes on every live owned Issue | `approved` |
| `clarifying` | `decision=implement`, grammar fails | `parked(identity-unverified)` |
| `clarifying` | `decision=discuss` | `parked(awaiting-human)` |
| `clarifying` | `decision=close` | `rejected` - the operator closes the issue |
| `clarifying` | stage-work budget (24h) elapses | `parked(awaiting-human)` |

A Task parked at `identity-unverified` is not stuck forever: the **next** non-bot comment on the
thread makes the operator re-sync that Issue's comments from the forge and re-run the grammar
against the refreshed thread. It either passes - stamping approval and moving to `approved` - or
the Task stays parked, silently, until the next human comment. The operator's own park comment
can never satisfy this itself: bot-authored events are dropped before they ever reach the queue.

!!! warning "Approval is not sticky"
    An Issue the Task acquires *after* reaching `approved` - via `issue_write(create)`, or a
    `refine` fold adopting one - resets the Task back to `clarifying`. The scope clause of the
    approval grammar no longer holds, so the mandate is re-gated. An agent cannot widen its own
    mandate by adopting work after the gate closed behind it.

!!! warning "Clarify cannot answer its own comments"
    This guard lives in the permission layer, not skill prose: the MCP comment action refuses
    to post when the last comment on the thread is bot-authored, and the webhook actor-check
    plus mention-check refuse to (re)spawn clarify off a bot-authored comment. The sole
    exception is `refine`, which is allowed to comment under tatara's own previous comment for
    scope-change or already-delivered notices - see [Refine](refine.md).

See [Approval Gates](../operations/security/approval-gates.md) for the full grammar, why the
match is anchored, and why labels play no part in it at all - `issue_write` has no `labels`
parameter and no `status` parameter, so clarify has no path to self-approve by any tool call.
