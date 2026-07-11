---
title: Implement Workflow
---

# Implement Workflow

`implement` writes the code. It replaces the Implement/MRCI portion of the old `issueLifecycle`
state machine as its own discrete kind, spawned by a label swap or an MCP action from
`clarify` or `review` - never self-initiated.

## 1. Trigger

Either of:

1. A `clarify` pod hands off (`tatara-approved` -> `tatara-implementation` label swap).
2. A `review` pod finds an unmergeable MR (conflict, failed pipeline) under the Task and
   re-invokes implement.

## 2. Full Task-umbrella context

Implement picks up the **whole Task CR**: every linked issue and comment, every already-open PR
or MR and its conversation, across every repo in scope - not just the one issue that triggered
it. It works with all repos under the Task and opens PRs across every affected one, all under
the same project-scoped Task (see
[Task reference](../reference/task.md#task-umbrella-and-the-workitem-ledger)). Implement may
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
3. Calls `change_summary` with a PR title/body, delivered scope, remaining scope, and a
   **required** `change_significance` (`major`/`minor`/`patch`) - this is what makes the merged
   change push-CD-eligible; see [Deploy Supervisor](deploy-supervisor.md).
4. Opens (or updates) a PR per affected repo, referencing the issue(s) it closes.

Implement never merges its own PR and never approves its own diff - that is `review`'s job, in
a separate pod, on a separate turn.

## Reference: labels

| Label | Applied when |
|---|---|
| `tatara-implementation` | Implement is actively running |
| (handoff to `review`) | PR/MR-create webhook fires when implement opens a PR - no label change needed to trigger review |
