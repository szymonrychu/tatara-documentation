---
title: Workflows
---

# Workflows

Tatara orchestrates several distinct agent workflows triggered by different events.

<div class="grid cards" markdown>

-   :material-lightbulb-outline: **Brainstorm**

    ---

    Periodic improvement proposals generated autonomously from the codebase knowledge graph.

    [:octicons-arrow-right-24: Brainstorm](brainstorm.md)

-   :material-source-branch: **Issue Lifecycle**

    ---

    Full end-to-end: triage -> conversation -> implement -> PR -> merge.

    [:octicons-arrow-right-24: Issue Lifecycle](issue-lifecycle.md)

-   :material-alert-circle-outline: **Incident Response**

    ---

    Grafana alert fires an investigation agent that diagnoses and files an incident issue.

    [:octicons-arrow-right-24: Incident](incident.md)

-   :material-code-review: **PR Review**

    ---

    Automated review of human-authored PRs with inline code suggestions.

    [:octicons-arrow-right-24: PR Review](review.md)

-   :material-broom: **Refine**

    ---

    Groom-only backlog peer: closes duplicates, tightens proposals, recovers stalled implements. Gates brainstorm as a cron barrier.

    [:octicons-arrow-right-24: Refine](refine.md)

-   :material-telescope: **Deep Architectural Research**

    ---

    Brainstorm goal variant that surveys the field and proposes ADR-style artifacts. Phase 1 only - no live internet/search yet.

    [:octicons-arrow-right-24: Deep Architectural Research](research.md)

-   :material-rocket-launch-outline: **Semver Push-CD**

    ---

    Autonomous merge -> tag -> propagate -> deploy cascade, terminating at `tatara-helmfile` with automatic issue closure.

    [:octicons-arrow-right-24: Semver Push-CD](push-cd.md)

</div>

## Workflow summary

| Workflow | Trigger | Agent kind | Scope | Output |
|---|---|---|---|---|
| Brainstorm | Cron (gated by refine barrier) / manual Task | `brainstorm` | Project | Proposal issues on repos |
| Deep Architectural Research | Same as Brainstorm (goal variant, not a separate kind) | `brainstorm` | Project | ADR-style proposal issue |
| Issue Lifecycle | Issue label / webhook / `issueScan` cron | `issueLifecycle` | Repo | PR + merged code |
| Implement | Manual Task creation | `implement` | Repo | PR |
| Incident | Grafana alert webhook | `incident` | Project | Incident issue, or a tier-revert `tatara-helmfile` MR |
| PR Review | PR webhook / `mrScan` cron | `review` | Repo | Review verdict + suggestions |
| Health Check | Cron | `brainstorm` (activity=`healthCheck`) | Project | Health report issue |
| Refine | Cadence-derived barrier ahead of brainstorm | `refine` | Project | Groomed backlog, escalation comments |
| Semver Push-CD | PR merge with declared `change_significance` | n/a (operator + CI, no dedicated Task kind) | Cross-repo | Tagged release, cluster deploy, closed issue |

!!! note "`triageIssue` kind is legacy"
    `triageIssue` still appears in the `Task.Spec.Kind` enum but is no longer created by any
    production code path - `issueLifecycle` now starts its own state machine at a `Triage` phase.
    See [Issue Lifecycle](issue-lifecycle.md) for detail.

!!! note "`healthCheck` is a `brainstorm` Task, not its own kind"
    Health-check Tasks are enqueued with `Kind: "brainstorm"` and distinguished by an
    `activity=healthCheck` label - exactly the way Deep Architectural Research reuses the
    `brainstorm` kind. `healthCheck` is a valid-but-unused `Task.Spec.Kind` enum value and a
    `modelByKind`/`effortByKind` pseudo-key for tuning, not the kind actually stamped on the Task.
