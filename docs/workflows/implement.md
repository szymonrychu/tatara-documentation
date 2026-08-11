---
title: Implement Workflow
---

# Implement Workflow

`implement` writes the code, and - since the #521 lifecycle redesign folded the old
`clarify` agent kind into it - it also **conducts the approval conversation and judges
the approval grammar**, in whichever turn or pod the conversation actually happens in.
It is both an **agent kind** (`Task.status.agentKind`) and, since #521, an **origin
kind** (`Task.spec.kind: implement`): it is `SweepIssueKind`, the same value
`MintIssueTask` stamps on any Task minted from a new issue - webhook-delivered or
backlog-sweep-discovered - which is exactly the role `clarify` used to play as an
origin. It is also the agent every **other** triaged origin (`brainstorm`, `incident`,
`refine`, `takeover`) passes through on its way to code, driven entirely by the
operator, never self-initiated.

!!! info "Where `clarify` went"
    Before #521, a separate `clarify` agent kind - and origin - ran the conversation
    and approval gate, then handed off to a distinct `implement` agent once a Task
    reached `approved`. `clarify` is now gone entirely, on both fronts: its origin
    role is `spec.kind: implement`, and its three decisions - `implement`/`close`/
    `discuss` - became `action` values (`approved`/`rejected`/`discuss`) on the
    `implement` outcome. The same pod that judges approval is the one that goes on to
    write the code - see [Approval Gates](../operations/security/approval-gates.md)
    for the full grammar this section summarizes.

## 1. Trigger

Any of:

1. `(create) -> new -> refined`: a new issue arrives (webhook-delivered or
   backlog-sweep-discovered) and is minted directly with `spec.kind: implement` -
   `implement` is its own origin here, starting at the approval gate, not at code.
2. `new -> refined`: triage passed and routed a **different** origin kind
   (`brainstorm`, `incident`, `refine`, `takeover`) to the `implement` agent, same
   starting point.
3. **Any comment** on an existing issue that is part of a live Task's umbrella, while
   the Task sits at `refined` or `under-implementation`.
4. `awaiting-review -> under-implementation`: a `review` pod submitted
   `verdict=request_changes` on a **non-`review`-kind** Task (i.e. the platform's own
   MR). No longer bounded by `maxReviewRounds` - that cap is deprecated with zero
   effect; the [24h residency cap](../reference/task-stages.md#residency-the-dead-man-switch)
   is the backstop instead.

## 2. The conversation phase: `refined`

On a new issue, `implement` digests the human's issue body and asks clarifying
questions via the normal issue-comment channel - the technical conversation happens on
the issue thread, the same SCM-native mechanism the platform has always used. On a
comment, it reads the existing Task CR and either answers back and waits, or submits
`action=approved` and, once the operator's independent verification passes, continues
straight into writing code.

**Live polling pod.** Once it posts a question or a partial plan, the operator keeps
the pod alive for up to **1 hour wall-clock**, delivering new comments to the running
session rather than tearing it down and re-spawning on every reply. If the hour
elapses with no reply, the operator stops the pod - the issue is left exactly where
the conversation stopped, resumable by a future comment (which re-spawns a fresh
`implement` pod against the same Task). This is bounded by the `refined` state's own
idle budget (`ConversationIdleDefault`, 60m by default): past that, the Task parks at
`parked(awaiting-human)` regardless of how many short polling windows it has been
through.

### The approval gate: implement proposes, the operator verifies

The pod's path forward at `refined` is `submit_outcome`:

```json
{"action":"approved","reason":"szymonrychu approved on tatara-operator#291",
 "plan_note_id":"n-05fa7cd2b344cec2",
 "approving_maintainer":"szymonrychu",
 "approval_citations":[{"id":"c-291-4","quote":"go ahead, ship it"}]}
```
or `action: rejected` or `action: discuss`. `reason` is **required on every action**
- for `action=approved` it must say in plain words **who** approved and **why** the
agent read their comment that way. `plan_note_id` is **always required** on
`action=approved`, human comment or not: the operator hashes that plan note's body at
grant and re-checks it before code is written, so a plan swapped after approval routes
back to `refined` (`plan-hash-mismatch`) instead of proceeding silently. `approving_maintainer`
and `approval_citations` travel **as a pair - both, or neither** - one citation entry
per **live** Issue the Task owns **that a maintainer has commented on at all**. An
Issue with no maintainer comment is not a citation gap: it either satisfies the
`autoApproveTataraProposals` carve-out or refuses outright, and there is nothing to
cite either way. The agent **judges meaning**; the operator does not take that
judgment on faith. It independently re-reads each cited comment against its own
mirror and checks [the facts, not the intent](../operations/security/approval-gates.md#the-approval-grammar):
the comment exists on that Issue, its author is a verified non-bot maintainer, the
quoted text truly occurs in the body the operator holds, and the comment has not
already been consumed as evidence - for **every** live Issue the Task owns that has
one to check. The operator does **not** require the citation to be the thread's most
recent maintainer comment - an earlier "go ahead" is still citable even if a later
maintainer comment merely says "thanks, ping me when the PR is up." Reading whether a
later comment actually *withdraws* an earlier approval is an intent question, and
intent is squarely the agent's job: a pod that sees a later maintainer comment
retracting or qualifying an earlier approval must submit `action=discuss`, not
`action=approved` citing the stale approval. **The agent's report of approval is not
approval, and it never grants itself the approval it is waiting on.**

| From | Outcome | To |
|---|---|---|
| `refined` | `action=approved`, the operator's citation and plan-hash checks pass on every live owned Issue | `under-implementation` |
| `refined` | `action=approved`, the citation check fails on any owned Issue | `parked(identity-unverified)` (HTTP 200, not an error) |
| `refined` | `action=discuss` | `parked(awaiting-human)` |
| `refined` | `action=rejected` | `rejected` - the operator closes the issue |
| `refined` | idle budget elapses | `parked(awaiting-human)` |

A Task parked at `identity-unverified` is not stuck forever: the **next** non-bot
comment on the thread makes the operator re-sync that Issue's comments from the forge
and un-park the Task, spawning a fresh `implement` pod against the refreshed thread.
The webhook path itself never grants approval - it only gets a live agent back in
front of the human who just commented. That pod reads the new comment, forms its own
judgment, and submits `action=approved` with a fresh citation through the same single
gate (`restapi.verifyApprovalScope`) every other pod goes through. **Nothing is posted
to the thread when the park happens** - the refusal is recorded only in
`Task.status.notes`, a log line, and a metric - so a genuinely new human comment is
what un-parks it, not a reaction to any prompt from the operator.

!!! warning "Approval is not sticky"
    An Issue the Task acquires *after* reaching `under-implementation` - via
    `issue_write(create)`, or a `refine` fold adopting one - resets the Task back to
    `refined`. The scope clause of the approval grammar no longer holds, so the
    mandate is re-gated. An agent cannot widen its own mandate by adopting work after
    the gate closed behind it.

!!! warning "Implement cannot answer its own comments at the conversation phase"
    This guard lives in the permission layer, not skill prose: the MCP comment action
    refuses to post when the last comment on the thread is bot-authored, and the
    webhook actor-check plus mention-check refuse to (re)spawn a pod off a
    bot-authored comment. The sole exception is `refine`, which is allowed to comment
    under tatara's own previous comment for scope-change or already-delivered notices
    - see [Refine](refine.md).

See [Approval Gates](../operations/security/approval-gates.md) for the full grammar,
why the operator re-derives who posted the cited comment and that the quote is really
there instead of trusting the agent's read, and why labels play no part in it at all -
`issue_write` has no `labels` parameter and no `status` parameter, so nothing gives
`implement` a path to self-approve by any tool call.

## 3. The code phase: `under-implementation`

Once `action=approved` grants, the pod - the same one, or its next turn on this Task -
picks up the **whole Task CR**: every linked Issue and MergeRequest and their
conversation, across every repo in scope, not just the one issue that triggered it. It
works with all repos under the Task and opens PRs across every affected one, all under
the same project-scoped Task (see the [context bundle](../reference/context-bundle.md)).
Implement may check out branches or existing MRs directly rather than always starting
from a fresh clone.

!!! warning "Implement may never reject for insufficient context"
    Because the operator renders the full umbrella bundle into the turn-0 prompt,
    implement is never in a position to say "I don't have enough information" the way
    old triage-adjacent kinds could. The rigid `implement` skill enforces this as a
    hard rule.

### Subagent tiering {: #subagent-tiering }

Implement's own agent surface (opus) is a **tiering point, not a single flat context**. The
rigid `implement` skill mandates dispatching work through typed `.claude/agents/*.md` files
shipped by `tatara-agent-skills`, each with baked `model:` frontmatter:

| Typed agent | Role | Model |
|---|---|---|
| `explorer` | Locate code, map structure, read-only research | haiku |
| `tester` | Write/run tests | haiku or sonnet |
| `builder` | Write code | sonnet |
| `architect` | Hard design/ambiguous-tradeoff calls | opus |

The dispatch table (task-shape -> named agent) is structural, resolved via frontmatter
(`param > frontmatter > parent`) rather than the `CLAUDE_CODE_SUBAGENT_MODEL` environment
override, which the design explicitly avoids as a silent-clobber footgun. This is the same
context-boundary fan-out principle as brainstorm/incident (split by repo, split by concern, keep
each subagent's context lean, report back a compact result) - implement is simply the kind that
tiers its own subagents by model as well as by boundary. No `Workflow` tool, no `ultracode`
effort tier, anywhere in this dispatch.

### Output

1. Clones (or checks out an existing branch/MR for) every repo in scope.
2. Writes code, commits, pushes to the deterministic task branch.
3. Opens (or updates) an MR per affected repo, referencing the issue(s) it closes.
4. Calls `submit_outcome`:

```json
{"action":"submitted","title":"...","body":"...",
 "change_significance":"major|minor|patch",
 "merge_order":["tatara-operator","tatara-cli"]}
```

`change_significance` is **required** on `submitted` and is **implement-owned**: a reviewer may
only raise it, never lower it - see [semver push-CD](merge-and-deploy.md#semver-push-cd).
`merge_order` is **required** whenever the Task's MRs span more than one repo, and there is **no
lexical default** - lexical order over this platform's own repos merges `cli` before `operator`,
which is precisely the fleet outage this field exists to prevent.

**The call itself can be refused.** Since [tatara-operator#594](https://github.com/szymonrychu/tatara-operator/pull/594),
if any MR the Task already owns is open with red CI, a real base conflict, or an unanswered
`request_changes`, `submit_outcome` returns a structured `409` and writes nothing - no claim, no
note, no transition. The agent's own prompt tells it to loop and wait for its own pipeline
(bounded roughly 20 minutes) before resubmitting; submitting while CI is still running is
explicitly correct. See [CI readiness gate](../reference/merge-request.md#ci-readiness-gate-on-submission).

On success, `under-implementation -> awaiting-review` (at least one owned MR must be open).
Implement never merges its own PR and never approves its own diff - that is `review`'s job, in a
separate pod, on a separate turn, and no MCP tool exposes a merge action to any agent kind.

```json
{"action":"declined","decline_reason":"..."}
```

`decline_reason` is required and non-empty. This parks the Task at
`parked(implement-declined)` - a terminal park with no re-entry; it ages out at `ParkRetention`
and is reaped.

## The `maxTurnsPerPod` exemption

**`implement` is the one agent kind exempt from `Project.spec.agent.maxTurnsPerPod`** (default
40, which caps every other kind's single pod run). A long healthy coding run must not be cut off
mid-implementation. `Task.spec.maxTurnsPerTask` / `Project.spec.agent.maxTurnsPerTask` used to be
the lifetime turn cap that bounded this exemption; it is now deprecated with zero effect (a turn
count measures how much an agent has done, not whether it is stuck). What actually bounds a
runaway `implement` Task today:

- The idle/work budget on `refined` and `under-implementation` - on elapse, `parked(awaiting-human)`
  or `parked(stage-deadline)` respectively.
- The probe/interrupt stall escalation on a turn that goes quiet mid-work - see
  [Stall detection](../architecture/agent-execution.md#stall-detection-probe-interrupt-stop).
- The [24h residency cap](../reference/task-stages.md#residency-the-dead-man-switch), a hardcoded
  dead-man switch for whatever the first two clocks cannot reach.
