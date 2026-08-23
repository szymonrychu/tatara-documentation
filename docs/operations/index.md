---
title: Operations
---

# Operations

You run tatara on your cluster: these pages are what you need once it is live. Deploy and roll back through GitOps, read what it costs and how to tune it, work a runbook when something breaks, and know what the bot identity can and cannot touch.

<div class="grid cards" markdown>

-   :material-ship-wheel: **Deployment & GitOps**

    ---

    Size the deployment, scale it, and follow a change from helmfile PR to live cluster.

    [:octicons-arrow-right-24: Deployment](deployment.md)

-   :material-chart-line: **Observability**

    ---

    Read the metrics and logs tatara emits, use the Grafana dashboards, and manage alert rules as code.

    [:octicons-arrow-right-24: Observability](observability.md)

-   :material-tune: **Tuning & On/Off**

    ---

    Control how much a project runs, what it spends, and which scheduled flows are active.

    [:octicons-arrow-right-24: Tuning](tuning.md)

-   :material-book-open-outline: **Runbooks**

    ---

    Work a failure scenario step by step: wedged pods, memory stack issues, CI failures.

    [:octicons-arrow-right-24: Runbooks](runbooks.md)

-   :material-swap-horizontal: **Task-Centric Cutover**

    ---

    Run the one-time migration to the task-centric operator, PR by PR, with the point of no return marked.

    [:octicons-arrow-right-24: Cutover](cutover.md)

-   :material-shield-lock-outline: **Security**

    ---

    Know what the bot identity, approval gates, prompt-injection defenses, and webhook authentication protect you from.

    [:octicons-arrow-right-24: Security](security/index.md)

</div>
