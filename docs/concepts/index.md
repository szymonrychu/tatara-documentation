---
title: Concepts
---

# Concepts

Tatara is built on a small set of ideas that, once understood, make the rest of the system fall into place. Start here before reading the architecture or component docs.

<div class="grid cards" markdown>

-   :material-robot-outline: **The Agentic Operating Model**

    ---

    How tatara turns a Kubernetes operator into an autonomous, issue-driven development loop - and what that means for your team.

    [:octicons-arrow-right-24: Agentic Model](agentic-model.md)

-   :material-check-decagram: **Why tatara**

    ---

    What problems tatara solves, who it is designed for, and the trade-offs to consider before adopting it.

    [:octicons-arrow-right-24: Why tatara](why-tatara.md)

</div>

## Core ideas

**The permanent substrate.** The name tatara comes from the traditional Japanese iron-smelting forge: a collective, iterative process around a permanent structure. Tatara applies this to software: ephemeral agent sessions work iteratively against a permanent knowledge graph of your codebase. The graph persists across agent restarts, pod failures, and code changes. It is the memory that makes every session smarter than a cold read.

**Everything is a Kubernetes resource.** Work items (tasks, events, subtasks) are CRDs managed by a controller-runtime operator. You inspect agent state with `kubectl get tasks`. You audit what happened with `kubectl describe task`. You gate access with RBAC. Tatara does not invent a new control plane - it extends the one you already run.

**Human approval at every gate.** The agent proposes; humans decide. No code is written without a human approving the plan. No code merges without a human merging the PR. The operator enforces these gates via allowlist-gated intake and `afterApproval` merge policy. The agent is a powerful assistant, not an autonomous actor with unchecked write access.

**GitOps for everything, including itself.** Tatara deploys via `tatara-helmfile`, a GitOps helmfile repository driven by an in-cluster ARC runner. Operator deployments happen through pull requests that render a diff. Helm chart versions and image tags are pinned in git. `kubectl set-image` is explicitly forbidden.
