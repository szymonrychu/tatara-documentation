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

-   :material-map-check-outline: **Portability & Requirements**

    ---

    The honest self-assessment for evaluators: what tatara genuinely requires (Kubernetes, an OIDC IdP, the memory stack), what is just the maintainer's stack, and where the SCM / agent / GitOps seams are welded shut. Read this before deciding whether tatara can run on your stack.

    [:octicons-arrow-right-24: Portability & Requirements](portability.md)

</div>

## Core ideas

**The permanent substrate.** The name tatara comes from the traditional Japanese iron-smelting forge: a collective, iterative process around a permanent structure. Tatara applies this to software: ephemeral agent sessions work iteratively against a permanent knowledge graph of your codebase. The graph persists across agent restarts, pod failures, and code changes. It is the memory that makes every session smarter than a cold read.

**Everything is a Kubernetes resource.** Work items (tasks, events, subtasks) are CRDs managed by a controller-runtime operator. You inspect agent state with `kubectl get tasks`. You audit what happened with `kubectl describe task`. You gate access with RBAC. Tatara does not invent a new control plane - it extends the one you already run.

**One hard human gate by default, more when you configure them.** The agent proposes; a human decides whether an issue gets worked - that triage gate is the load-bearing control in the shipped defaults. Be precise about the rest: under the default `afterApproval` merge policy the operator squash-merges the bot's PR **itself** once CI is green, with no human merge step. A human merge is required only if you add a review-gated branch-protection rule on the repo, or if you populate the `reporterLogins` / `maintainerLogins` allowlists (both empty and open by default). Configured that way, tatara is a strongly gated assistant; out of the box the merge is autonomous. See [The Agentic Operating Model](agentic-model.md#gate-3-pr-merge-policy-autonomous-by-default) for exactly which gates are on by default and which you opt into.

**GitOps for everything, including itself.** Tatara deploys via `tatara-helmfile`, a GitOps helmfile repository driven by an in-cluster ARC runner. Operator deployments happen through pull requests that render a diff. Helm chart versions and image tags are pinned in git. `kubectl set-image` is explicitly forbidden.
