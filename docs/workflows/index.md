---
title: Workflows
---

# Workflows

A `Task` carries two separate kind fields. `Task.spec.kind` is the **origin** - immutable, baked
into the Task's name. `Task.status.agentKind` is the **running agent** - the pod currently doing
the work - one of **seven** values. `clarify` is gone as of the #521 lifecycle redesign: its
conversation-and-approval job folded into `implement`, which now runs both the approval gate
(`refined`) and the code (`under-implementation`) - see [Implement](implement.md).

Each agent kind is a single-purpose pod: no kind straddles triage, coding, and review in one
context. The operator - never an agent - drives every transition between them, per the
[state machine](../reference/task-stages.md).

<div class="grid cards" markdown>

-   :material-lightbulb-outline: **Brainstorm**

    ---

    Periodic improvement proposals generated autonomously from the codebase knowledge graph.
    Each accepted proposal becomes its own new `implement`-origin Task.

    [:octicons-arrow-right-24: Brainstorm](brainstorm.md)

-   :material-alert-circle-outline: **Incident Response**

    ---

    A Grafana alert fires an `incident` investigation; a confirmed finding files an issue and
    hands off to `implement`'s conversation phase.

    [:octicons-arrow-right-24: Incident](incident.md)

-   :material-hammer-wrench: **Implement**

    ---

    Runs the approval gate (live triage/human-conversation, citing a maintainer comment which the
    operator independently verifies) at `refined`, then writes the code across every repo under
    the Task at `under-implementation`. The `clarify` kind folded into this one at #521.

    [:octicons-arrow-right-24: Implement](implement.md)

-   :material-code-review: **PR / MR Review**

    ---

    Reviews the diff and submits a verdict. The operator, not the agent, posts the SCM review
    and performs the merge.

    [:octicons-arrow-right-24: PR / MR Review](review.md)

-   :material-file-document-edit-outline: **Documentation**

    ---

    One nightly batch Task per project, covering everything delivered in the last 24 hours -
    not one Task per delivery.

    [:octicons-arrow-right-24: Documentation](documentation.md)

-   :material-broom: **Refine**

    ---

    Groom-only backlog peer: folds duplicate Tasks, closes stale issues, links related work.
    Gates brainstorm as a cron barrier.

    [:octicons-arrow-right-24: Refine](refine.md)

-   :material-telescope: **Deep Architectural Research**

    ---

    Brainstorm goal variant that surveys the field and proposes ADR-style artifacts. Phase 1
    only - no live internet/search yet.

    [:octicons-arrow-right-24: Deep Architectural Research](research.md)

-   :material-arrow-up-bold-circle-outline: **Upgrade**

    ---

    Scheduled, opt-in, disabled by default. Takes one dependency-upgrade unit per Task, reads
    the release notes between the current pin and the target, and carries the code and config
    that must move with the bump - not just the pin - through review, merge and deploy.

    [:octicons-arrow-right-24: Upgrade](upgrade.md)

-   :material-rocket-launch-outline: **Merge and Deploy**

    ---

    Pod-less operator stages, not an agent kind: on an accepted `approve` verdict the operator
    merges each repo in `mergeOrder`, CI cuts the tag, `tatara-helmfile` applies, and the
    operator closes the owned issues.

    [:octicons-arrow-right-24: Merge and Deploy](merge-and-deploy.md)

</div>

## Origin kinds and the agent kind each one spawns

Every triaged Task enters `state=new`, which is pure operator work - it runs no agent,
classifies the origin, and picks the next state from `spec.kind`. That next state is what
spawns the first pod:

| Origin kind (`spec.kind`) | State after triage | Agent kind spawned |
|---|---|---|
| `brainstorm` | `refined` | `brainstorm` |
| `incident` | `refined` | `incident` |
| `implement` | `refined` | `implement` |
| `refine` | `refined` | `refine` |
| `review` | `awaiting-review` | `review` |
| `documentation` | `under-implementation` (minted straight in) | `documentation` |
| `takeover` | `refined` (minted straight in) | `implement` |
| `upgrade` | `under-implementation` (minted straight in) | `upgrade` |

`implement` is an origin in its own right, not just an agent kind: it is
`SweepIssueKind`, the value `MintIssueTask` stamps on any Task minted from a
new issue - webhook-delivered or backlog-sweep-discovered - which is exactly
the role `clarify` used to play as an origin.

Each pod's name is computed independently of the Task's own name - see
[Pod naming](../reference/task-stages.md#pod-naming).

`refined` is a single state serving five different origins (every row above except
`review`, `documentation` and `upgrade`) - what distinguishes them is `Task.status.agentKind`, which the
[state machine's `AgentKindFor` table](../reference/task-stages.md#which-agent-each-state-spawns)
derives from the origin, not the state. `implement` conducts the approval conversation there
before ever writing code - it is reached at triage for its own origin as well as
`brainstorm`/`incident`/`refine`/`takeover`, and again via `refined -> under-implementation`
once the [approval grammar](../operations/security/approval-gates.md#the-approval-grammar) has
passed for every live Issue the Task owns. See the [state machine](../reference/task-stages.md)
for the full transition table, and [MCP tools](../reference/mcp-tools.md#the-profile-gating-table)
for which tools each agent kind is gated to.

!!! note "Model and effort are configured per agent kind"
    `Project.spec.agent.modelByKind` / `effortByKind` key on the **agent** kind (`brainstorm`,
    `incident`, `implement`, `review`, `refine`, `documentation`, `upgrade`) - the same seven
    values as `Task.status.agentKind`.
