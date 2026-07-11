---
title: Workflows
---

# Workflows

Tatara orchestrates seven discrete agent kinds, plus one operator-only, non-agent CD step,
triggered by different events. Each kind is a single-purpose pod handing work to the next kind
via a label swap or an MCP action - no kind straddles triage, coding, and review.

<div class="grid cards" markdown>

-   :material-lightbulb-outline: **Brainstorm**

    ---

    Periodic improvement proposals generated autonomously from the codebase knowledge graph and external research.

    [:octicons-arrow-right-24: Brainstorm](brainstorm.md)

-   :material-alert-circle-outline: **Incident Response**

    ---

    Grafana alert fires an investigation agent that diagnoses and files an incident issue.

    [:octicons-arrow-right-24: Incident](incident.md)

-   :material-forum-outline: **Clarify**

    ---

    Live triage/human-conversation pod on a new issue or any comment; hands off to Implement.

    [:octicons-arrow-right-24: Clarify](clarify.md)

-   :material-hammer-wrench: **Implement**

    ---

    Writes the code across every repo under the Task umbrella; tiers its own typed subagents.

    [:octicons-arrow-right-24: Implement](implement.md)

-   :material-code-review: **PR Review**

    ---

    Automated review of PRs/MRs under a Task with inline code suggestions; approve-label or re-invoke Implement.

    [:octicons-arrow-right-24: PR Review](review.md)

-   :material-file-document-edit-outline: **Documentation**

    ---

    Schedule-driven docs-repo updates when non-trivial changes have landed since the last run.

    [:octicons-arrow-right-24: Documentation](documentation.md)

-   :material-broom: **Refine**

    ---

    Groom-only backlog peer: closes duplicates, tightens proposals, recovers stalled implements. Gates brainstorm as a cron barrier.

    [:octicons-arrow-right-24: Refine](refine.md)

-   :material-telescope: **Deep Architectural Research**

    ---

    Brainstorm goal variant that surveys the field and proposes ADR-style artifacts. Phase 1 only - no live internet/search yet.

    [:octicons-arrow-right-24: Deep Architectural Research](research.md)

-   :material-rocket-launch-outline: **Deploy Supervisor**

    ---

    Operator-only, non-agent CD step: autonomous merge -> tag -> propagate -> deploy cascade, terminating at `tatara-helmfile` with automatic issue closure.

    [:octicons-arrow-right-24: Deploy Supervisor](deploy-supervisor.md)

</div>

## Kind summary

| Kind | Trigger | Model | Scope | Output |
|---|---|---|---|---|
| Brainstorm | Cron (gated by refine barrier) | opus | Project | Linked issue set across affected repos |
| Incident | Grafana alert webhook | opus | Project | Evidence-backed incident issue, or a tier-revert `tatara-helmfile` MR |
| Clarify | New issue, or any comment on an existing issue | opus | Project | Triage conversation; hands off to Implement via label swap |
| Implement | `clarify`/`review` MCP action, or label swap | opus surface (tiers own subagents) | Project | PR(s) across every affected repo under the Task |
| PR / MR Review | PR/MR-create webhook | opus | Project | Approve-label + native review, or feedback + re-invoke Implement |
| Documentation | Cron | sonnet | Repo (docs repo) | Docs-repo update when non-trivial changes landed |
| Refine | Cadence-derived barrier ahead of brainstorm | sonnet | Project | Groomed backlog, escalation comments |
| Deploy Supervisor (not an agent kind) | Review approval + green CI | n/a (operator-only, no pod) | Cross-repo | Tagged release, cluster deploy, closed issue |

!!! note "Retired kinds"
    `selfImprove`, `triageIssue`, `healthCheck`, and `issueLifecycle` are retired as agent
    kinds. Their enum strings remain valid on the `Task.Spec.Kind` CRD field only so
    pre-existing stored Tasks keep working; no code path creates a new Task of any of these
    kinds. See [the kind taxonomy](../reference/index.md#task-kinds-and-scoping) for where
    each one's responsibility moved.
