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

</div>

## Workflow summary

| Workflow | Trigger | Agent kind | Scope | Output |
|---|---|---|---|---|
| Brainstorm | Cron / `tatara` label on special issue | `brainstorm` | Project | Proposal issues on repos |
| Issue Lifecycle | Issue label / webhook | `issueLifecycle` | Repo | PR + merged code |
| Implement | Manual Task creation | `implement` | Repo | PR |
| Triage | Issue creation / label | `triageIssue` | Repo | Comment + label update |
| Incident | Grafana alert webhook | `incident` | Project | Incident issue |
| PR Review | PR webhook | `review` | Repo | Review verdict + suggestions |
| Health Check | Cron | `healthCheck` | Project | Health report issue |
| Refine | Cron (pre-brainstorm) | `refine` | Project | Updated proposal issues |
