---
title: Implement Workflow
---

# Implement Workflow

`implement` writes the code. It is an **agent kind only, never a Task origin kind** - no webhook,
cron, or human action ever mints a Task with `kind: implement`. It is the stage every approved
Task passes through, driven entirely by the operator, never self-initiated.

## 1. Trigger

Either of:

1. `approved -> implementing`: a `clarify` Task's `submit_outcome(decision=implement)` passed
   the [approval grammar](../operations/security/approval-gates.md#the-approval-grammar) and a
   `QueuedEvent` for the implement pod was admitted.
2. `reviewing -> implementing`: a `review` pod submitted `verdict=request_changes` on a
   **non-`review`-kind** Task (i.e. the platform's own MR), bounded by `maxReviewRounds` (3).

## 2. Full Task-umbrella context

Implement picks up the **whole Task CR**: every linked Issue and MergeRequest and their
conversation, across every repo in scope - not just the one issue that triggered it. It works
with all repos under the Task and opens PRs across every affected one, all under the same
project-scoped Task (see the [context bundle](../reference/context-bundle.md)). Implement may
check out branches or existing MRs directly rather than always starting from a fresh clone.

!!! warning "Implement may never reject for insufficient context"
    Because the operator renders the full umbrella bundle into the turn-0 prompt, implement is
    never in a position to say "I don't have enough information" the way old triage-adjacent
    kinds could. The rigid `implement` skill enforces this as a hard rule.

## 3. Subagent tiering {: #subagent-tiering }

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

## 4. Output

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

On success, `implementing -> reviewing` (at least one owned MR must be open). Implement never
merges its own PR and never approves its own diff - that is `review`'s job, in a separate pod, on
a separate turn, and no MCP tool exposes a merge action to any agent kind.

```json
{"action":"declined","decline_reason":"..."}
```

`decline_reason` is required and non-empty. This parks the Task at
`parked(implement-declined)` - a terminal park with no re-entry; it ages out at `parkRetention`
and is reaped.

## The `maxTurnsPerPod` exemption

**`implement` is the one agent kind exempt from `Project.spec.agent.maxTurnsPerPod`** (default
40, which caps every other kind's single pod run). A long healthy coding run must not be cut off
mid-implementation. It is bounded instead by two other things, both of which apply to every kind
including `implement`:

- `Task.spec.maxTurnsPerTask` / `Project.spec.agent.maxTurnsPerTask` (default 300) - the
  **lifetime** turn cap across every pod of the Task, regardless of kind.
- The `implementing` stage's own 6h work-budget - on elapse, `parked(stage-deadline)`.
