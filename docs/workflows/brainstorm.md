---
title: Brainstorm Workflow
---

# Brainstorm Workflow

The brainstorm workflow is a periodic, autonomous improvement proposal engine. It surveys your codebase knowledge graph, identifies improvement opportunities, and opens GitHub/GitLab issues for human review. No human initiates it - a cron schedule fires it automatically.

## Trigger

- **Cron:** `spec.scm.cron.brainstorm` on the `Project` CR (e.g. `"0 9 * * 1"` for Mondays at 09:00)
- **Manual:** Create a `Task` with `kind: brainstorm` against the project

## Workflow steps

```mermaid
flowchart TD
    A[Brainstorm cron fires] --> B{Refine barrier due?}
    B -->|yes| RB[Create Task: refine\nhold until terminal]
    RB --> G
    B -->|no| G{Operator pre-gate:\nbacklog below maxOpenProposals?}
    G -->|no, at cap| SKIP[Skip cycle\nmetric skipped_cap\nNO Task, NO pod, NO tokens]
    G -->|yes| C[Create Task: brainstorm]
    C --> D[Spawn agent Pod]
    D --> E[Query memory graph per repo\nscore candidates]
    E --> F{Found something\nnovel + shippable?}
    F -->|no| NONE[Agent early-exit:\nskip_research / BrainstormOutcome none]
    F -->|yes| H[call propose_issue MCP tool]
    H --> J[Operator opens issue\non target repo]
    J --> K[Apply tatara-brainstorming label]
    K --> L[Enqueue clarify\nTask for the new issue]
    L --> M[Human receives proposal\nin issue tracker]
```

The `maxOpenProposals` check and the `BrainstormOutcome{none}` early-exit are two **different**
gates at two different altitudes: the cap is an operator pre-admission gate (left branch, no pod
ever spawns), while `none` is an agent decision reached only after a pod is already running (right
branch). See [Proposal limits](#proposal-limits).

## One task per project per cycle

Brainstorm runs at **project scope**, not per-repo. At most one non-terminal brainstorm `Task` may exist for a project at any time - a Task in flight for *any* repo blocks a new one project-wide. Brainstorm Tasks carry an empty `RepositoryRef`; the agent decides which repos to target internally.

`spec.scm.cron.brainstorm.maxPerCycle` is a **deprecated, inert field** (kubebuilder default `1`). Setting it has no effect - the operator hard-caps at one brainstorm cycle regardless of its value.

## Refine barrier

Before a due brainstorm tick proceeds, the operator gates it on the [refine workflow](refine.md) completing a pass first. This is cadence-derived (no separate `refine` cron schedule): a due brainstorm tick creates a `refine` Task and holds (polling roughly every 30s) until that Task reaches a terminal state (`Succeeded` or `Failed` - a failed refine still releases the gate, so a broken refine never wedges brainstorm). Only brainstorm waits on this barrier; `mrScan`, `issueScan`, and `documentation` do not.

## Proposal structure

Each proposal issue includes:
- A concrete, actionable title
- A description covering: problem statement, proposed approach, expected benefit
- The `tatara-brainstorming` label (configurable via `spec.scm.brainstormingLabel`; `tatara-idea` is a deprecated legacy alias)

The agent targets proposals at specific repositories based on graph analysis - it does not spray proposals across all repos indiscriminately. Live handoffs (see [Refine](refine.md)) are considered for continuation proposals before a fresh-ideas research pass runs, under the same cap below.

## Proposal limits

`spec.scm.cron.brainstorm.maxOpenProposals` (CRD default: **5** per project) limits how many open
proposal issues can exist simultaneously, counted **across all repos in the project, summed** (a
systemic group counts as **one** regardless of how many sibling issues it spans).

This is an **operator-side pre-admission gate**, not an agent decision. On a due brainstorm tick
the operator (`brainstorm()` in `projectscan.go`) sums the open-proposal backlog across repos and,
if it is at or over the cap, records the `skipped_cap` scan metric and returns **before creating
any brainstorm Task or spawning a pod**. No agent runs, so no tokens are spent. This gate is where
project-wide proposal-flood / token backpressure lives.

Do **not** confuse the cap with `BrainstormOutcome{action: none, reason: "..."}`. That is the
distinct **agent-driven** no-yield path: when a pod *did* spawn (backlog was under the cap) but the
agent surveyed the codebase and found nothing novel or shippable, it exits cheaply via
[`skip_research`](research.md#skip_research) or an equivalent `none` outcome without filing
anything. One is the operator refusing to spawn (at cap); the other is a spawned agent choosing to
yield nothing.

## Systemic improvements

When the brainstorm identifies a cross-cutting issue affecting multiple repositories, it can file related proposals as a **systemic group** (bounded to at most 6 repos per group):

- Each proposal gets a `tatara/systemic-<id>` label
- The group counts as one against `maxOpenProposals`
- Per repo, the lead is elected as the **lowest issue number** in that repo for the group; the lead task opens a single combined PR that closes all same-repo siblings from the PR body (`Closes #N, Closes #M`)
- Cross-repo siblings are reference-only in the lead's prompt - the lead never edits another repo's code

```mermaid
flowchart LR
    S[Systemic group] --> R1[Repo A issue #10\nlead]
    S --> R2[Repo B issue #22\nlead]
    S --> R3[Repo A issue #11\nsibling of #10]
    R1 --> |combined PR closes| R3
```

For a systemic group spanning up to 6 repos, or any single-repo survey deep enough to need
per-concern isolation, the brainstorm agent fans out `Agent`-tool subagents split by context
boundary (per-repo, per-concern) rather than holding all of it in one context - each subagent
reports back a compact result; the agent never uses the retired `Workflow` tool or `ultracode`
effort tier for this fan-out.

## Staleness reaper (auto-decline)

`spec.scm.cron.brainstorm.staleProposalDays` opts in a staleness reaper: a positive value auto-closes bot-authored proposals that have had no human engagement (no human comment, no live work) for at least that many days. The unset default (`0` or negative) **disables the reaper entirely** - this is an explicit opt-in sentinel, not a kubebuilder default. When enabled, unengaged proposals older than the window are closed automatically, keeping `maxOpenProposals` from silently filling up with ideas nobody responded to.

## Issue engagement tracking

Every issue in an agent's context carries an `[bot-engaged]` marker when the bot has already commented on it. This is a **passive dedup marker**, not an instruction the brainstorm agent acts on directly - brainstorm itself never comments on existing issues (it only ever creates new proposals via `createProposal`/`propose_issue`). The marker exists so the agent can see, at a glance, which issues in its context it has already touched.

The actual "don't repeat yourself" behaviors that consume this marker live elsewhere:

- **issueScan** independently skips triage re-entry for a bot-authored, brainstorming-labeled issue that has had no human activity since the bot's own comment (bot-only `updatedAt` bumps do not count as engagement).

!!! note "healthCheck absorbed into brainstorm"
    The retired `healthCheck` kind used to consult `[bot-engaged]` to decide whether to comment
    on an existing issue, propose a new one, or skip. brainstorm now also absorbs what
    `healthCheck` used to cover (platform-health survey framed as a proposal) - there is no
    separate report-issue output type; health-survey findings are filed the same way as any
    other brainstorm proposal. See [the kind taxonomy](../reference/index.md#task-kinds-and-scoping).

## Conversation forking

When a brainstorm agent opens multiple proposal issues, each resulting `clarify` task gets a **forked copy** of the brainstorm conversation (S3 copy-object). This gives each `clarify` agent the brainstorm context as its starting point, without the transcripts interfering with each other.

## Configuring brainstorm sources

```yaml
spec:
  scm:
    cron:
      brainstorm:
        sources:
          - memory    # knowledge graph (always recommended)
          - docs      # docs/ directory content
          - internet  # outbound internet egress (requires NetworkPolicy)
        maxOpenProposals: 5
        staleProposalDays: 14
```

With `internet` in sources, the operator stamps `tatara.io/egress: internet` on the brainstorm Pod, which a NetworkPolicy can use to grant `0.0.0.0/0` egress for that pod class only. As of Phase 1 of the [deep architectural research](research.md) build, this remains the only egress hook - no dedicated web-search/academic MCP servers are wired yet.

!!! note "healthCheck retired"
    The `healthCheck` kind (a lighter-weight variant that assessed platform health - stalled
    tasks, drift, CI failures - and produced a report issue on its own
    `spec.scm.cron.healthCheck` schedule) is retired. Its behavior is absorbed into brainstorm's
    own survey pass, filed as an ordinary brainstorm proposal rather than a separate report-issue
    output. See [the kind taxonomy](../reference/index.md#task-kinds-and-scoping).
