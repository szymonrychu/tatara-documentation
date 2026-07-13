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

-   :material-autorenew: **tatara Builds tatara**

    ---

    Tatara is enrolled as its own first Project and manages its own codebase - proposing, implementing, and reviewing its own improvements. Concrete examples of brainstorm-driven refactors, alert-driven code and alerting fixes, refinement that closes already-delivered work, and documentation that refreshes itself.

    [:octicons-arrow-right-24: Self-Improvement](self-improvement.md)

-   :material-map-check-outline: **Portability & Requirements**

    ---

    The honest self-assessment for evaluators: what tatara genuinely requires (Kubernetes, an OIDC IdP, the memory stack), what is just the maintainer's stack, and where the SCM / agent / GitOps seams are welded shut. Read this before deciding whether tatara can run on your stack.

    [:octicons-arrow-right-24: Portability & Requirements](portability.md)

</div>

## Core ideas

**The permanent substrate.** The name tatara comes from the traditional Japanese iron-smelting forge: a collective, iterative process around a permanent structure. Tatara applies this to software: ephemeral agent sessions work iteratively against a permanent knowledge graph of your codebase. The graph persists across agent restarts, pod failures, and code changes. It is the memory that makes every session smarter than a cold read.

**Everything is a Kubernetes resource.** `Project`, `Repository`, `Task`, `QueuedEvent`, `Issue`, and `MergeRequest` are all CRDs managed by a controller-runtime operator. You inspect agent state with `kubectl get tasks`. You audit what happened with `kubectl describe task`. You gate access with RBAC. Tatara does not invent a new control plane - it extends the one you already run.

**One hard human gate by default, more when you configure them.** The agent proposes; a maintainer decides whether an issue gets worked - and decides it in exactly one way: a maintainer's most recent comment on the issue matching one of `Project.spec.scm.approvalPhrases`, anchored and whole-line, with the bot structurally excluded from ever satisfying its own gate. `clarify`'s conversation shapes the plan but does not itself release the gate - the agent's own read of the conversation is informational, not authoritative. Be precise about the rest: `review` approves the bot's own PR from a separate pod by calling `submit_outcome(approve)`, never by posting a native forge approval (GitHub 422s a self-authored PR's own approval either way), and the operator itself merges once required checks are green and that verdict is recorded, with no human merge step. There is no branch-protection rule that adds a human merge step on top of this by default - the platform's single bot identity means a rule requiring an approving review would deadlock every merge, so that option does not exist; see [the accepted-risk note](../operations/security/index.md) for what defense-in-depth looks like instead. `reporterLogins` (intake) is open by default; `maintainerLogins` (approval) is **closed** by default - populate it or nothing ever advances to implement. Configured that way, tatara is a strongly gated assistant; out of the box, once you have named at least one maintainer, the merge is autonomous. See [The Agentic Operating Model](agentic-model.md#gate-2-review-approval-then-an-operator-driven-merge) for exactly which gates are on by default and which you opt into.

**GitOps for everything, including itself.** Tatara deploys via `tatara-helmfile`, a GitOps helmfile repository driven by an in-cluster ARC runner. Operator deployments happen through pull requests that render a diff. Helm chart versions and image tags are pinned in git. `kubectl set-image` is explicitly forbidden.
