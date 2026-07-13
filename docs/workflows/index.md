---
title: Workflows
---

# Workflows

A `Task` carries two separate kind fields. `Task.spec.kind` is the **origin** - immutable, baked
into the Task's name, one of six values. `Task.status.agentKind` is the **running agent** - the
pod currently doing the work - one of seven values: the six origins plus `implement`. `implement`
is an agent kind only. It is never an origin: no webhook, cron, or human action ever mints a Task
with `kind: implement`. It is a **stage** every approved Task passes through on its way from
`approved` to `reviewing`, regardless of which of the six origins started it.

Each agent kind is a single-purpose pod: no kind straddles triage, coding, and review in one
context. The operator - never an agent - drives every transition between them, per the
[stage machine](../reference/task-stages.md).

<div class="grid cards" markdown>

-   :material-lightbulb-outline: **Brainstorm**

    ---

    Periodic improvement proposals generated autonomously from the codebase knowledge graph.
    Each accepted proposal becomes its own new `clarify` Task.

    [:octicons-arrow-right-24: Brainstorm](brainstorm.md)

-   :material-alert-circle-outline: **Incident Response**

    ---

    A Grafana alert fires an `incident` investigation; a confirmed finding files an issue and
    hands off to `clarify`.

    [:octicons-arrow-right-24: Incident](incident.md)

-   :material-forum-outline: **Clarify**

    ---

    Live triage/human-conversation pod on a new issue or any comment on a live Task's umbrella;
    runs the approval grammar before a Task can advance to `approved`.

    [:octicons-arrow-right-24: Clarify](clarify.md)

-   :material-hammer-wrench: **Implement**

    ---

    Writes the code across every repo under the Task once it is `approved`. A stage, not an
    origin - every approved Task passes through it exactly once per review round.

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

-   :material-rocket-launch-outline: **Merge and Deploy**

    ---

    Pod-less operator stages, not an agent kind: on an accepted `approve` verdict the operator
    merges each repo in `mergeOrder`, CI cuts the tag, `tatara-helmfile` applies, and the
    operator closes the owned issues.

    [:octicons-arrow-right-24: Merge and Deploy](merge-and-deploy.md)

</div>

## Origin kinds and the agent kind each one spawns

Every Task enters the stage machine at `triaging`, which is pure operator work - it runs no
agent, classifies the origin, and picks the next stage from `spec.kind`. That next stage is what
spawns the first pod:

| Origin kind (`spec.kind`) | Stage entered from `triaging` | Agent kind spawned | Pod name |
|---|---|---|---|
| `brainstorm` | `brainstorming` | `brainstorm` | `<task>-brainstorm` |
| `incident` | `investigating` | `incident` | `<task>-incident` |
| `clarify` | `clarifying` | `clarify` | `<task>-clarify` |
| `refine` | `refining` | `refine` | `<task>-refine` |
| `review` | `reviewing` | `review` | `<task>-review` |
| `documentation` | `documenting` | `documentation` | `<task>-documentation` |

`implement` has no row above because it is never what `triaging` selects. It is reached only via
`approved -> implementing`, once the [approval grammar](../operations/security/approval-gates.md#the-approval-grammar)
has passed for every live Issue the Task owns. See the [stage machine](../reference/task-stages.md)
for the full transition table, and [MCP tools](../reference/mcp-tools.md#the-profile-gating-table)
for which tools each agent kind is gated to.

!!! note "Model and effort are configured per agent kind"
    `Project.spec.agent.modelByKind` / `effortByKind` key on the **agent** kind (`brainstorm`,
    `incident`, `clarify`, `implement`, `review`, `refine`, `documentation`) - the same seven
    values as `Task.status.agentKind`, never the six-value origin enum.
