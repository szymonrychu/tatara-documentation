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
    A[Brainstorm cron fires] --> B[Create Task: brainstorm]
    B --> C[Spawn agent Pod]
    C --> D{Query memory graph\nper repo}
    D --> E[Score improvement\ncandidates]
    E --> F{Below maxOpenProposals?}
    F -->|yes| G[call createProposal MCP tool]
    F -->|no| H[BrainstormOutcome: none]
    G --> I[Operator opens issue\non target repo]
    I --> J[Apply tatara-brainstorming label]
    J --> K[Enqueue issueLifecycle\nTask for the new issue]
    K --> L[Human receives proposal\nin issue tracker]
```

## Proposal structure

Each proposal issue includes:
- A concrete, actionable title
- A description covering: problem statement, proposed approach, expected benefit
- The `tatara-brainstorming` label (configurable via `spec.scm.brainstormingLabel`; `tatara-idea` is a deprecated legacy alias)

The agent targets proposals at specific repositories based on graph analysis - it does not spray proposals across all repos indiscriminately.

## Proposal limits

`spec.scm.cron.brainstorm.maxOpenProposals` (default: 5 per project) limits how many open proposal issues can exist simultaneously. The brainstorm agent checks this count before filing; if the cap is met, it exits with `BrainstormOutcome{action: none, reason: "..."}`.

## Systemic improvements

When the brainstorm identifies a cross-cutting issue affecting multiple repositories, it can file related proposals as a **systemic group**:

- Each proposal gets a `tatara/systemic-<id>` label
- The group counts as one against `maxOpenProposals`
- The lead task (lowest issue number in the group for a given repo) opens a single combined PR that closes all same-repo siblings

```mermaid
flowchart LR
    S[Systemic group] --> R1[Repo A issue #10\nlead]
    S --> R2[Repo B issue #22\nlead]
    S --> R3[Repo A issue #11\nsibling of #10]
    R1 --> |combined PR closes| R3
```

## Conversation forking

When a brainstorm agent opens multiple proposal issues, each resulting `issueLifecycle` task gets a **forked copy** of the brainstorm conversation (S3 copy-object). This gives each implementation agent the brainstorm context as its starting point, without the transcripts interfering with each other.

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
```

With `internet` in sources, the operator stamps `tatara.io/egress: internet` on the brainstorm Pod, which a NetworkPolicy can use to grant `0.0.0.0/0` egress for that pod class only.

## Health check vs. brainstorm

The `healthCheck` workflow is a lighter-weight variant. Instead of proposing new work, it assesses the health of the current platform state (stalled tasks, drift, CI failures) and produces a report issue. It runs on its own `spec.scm.cron.healthCheck` schedule.
