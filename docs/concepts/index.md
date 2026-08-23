---
title: Concepts
---

# Concepts

Read these four pages and you will know what tatara does, why it is shaped the way it is, what it does to its own codebase, and whether it can run on your stack. Start here, before the architecture or component docs.

<div class="grid cards" markdown>

-   :material-robot-outline: **The Agentic Operating Model**

    ---

    The loop itself: the eight states a Task moves through, who writes them, where you approve, and what happens on a merge request tatara did not open.

    [:octicons-arrow-right-24: Agentic Model](agentic-model.md)

-   :material-check-decagram: **Why tatara**

    ---

    What tatara does for your backlog, who it fits, and the trade-offs you take on when you adopt it.

    [:octicons-arrow-right-24: Why tatara](why-tatara.md)

-   :material-autorenew: **tatara Builds tatara**

    ---

    tatara is enrolled as its own first Project and works its own repos. Worked examples: a graph-discovered refactor, an alert that retunes its own rule, a Task that closes itself as already delivered, docs that refresh themselves, and dependencies that bump themselves.

    [:octicons-arrow-right-24: Self-Improvement](self-improvement.md)

-   :material-map-check-outline: **Portability & Requirements**

    ---

    What tatara genuinely requires (Kubernetes, an OIDC issuer, the memory stack), what is only the maintainer's stack, and where the SCM, agent, and GitOps seams are welded shut. Read this before you decide tatara can run on your cluster.

    [:octicons-arrow-right-24: Portability & Requirements](portability.md)

</div>

If you would rather see it than read about it first, [Watch One Run](../explainers/watch-one-run.md) walks a single real piece of work through tatara's own repository, thread quotes included.

## Core ideas

**A permanent substrate under ephemeral sessions.** The name comes from the traditional Japanese iron-smelting forge: a collective, iterative process around a permanent structure. Agent pods here are disposable. The knowledge graph of your codebase is not - it survives pod failures, restarts, and code changes, which is why each session starts smarter than a cold read of the repo.

**Everything is a Kubernetes resource.** `Project`, `Repository`, `Task`, `QueuedEvent`, `Issue`, and `MergeRequest` are CRDs owned by a controller-runtime operator. You inspect agent state with `kubectl get tasks`, audit what happened with `kubectl describe task`, and gate access with RBAC. tatara does not invent a control plane; it extends the one you already run.

**One hard human gate by default, and you can add more.** The agent proposes and a maintainer decides, in exactly one way: a comment on the issue. The `implement` agent judges whether a comment approves the work and cites it, giving a forge comment id plus a verbatim quote. The operator then verifies that citation itself - the comment exists, its author is a verified non-bot maintainer, and the quoted text really occurs in the body the operator holds. The bot is structurally excluded from satisfying its own gate. The agent's judgment picks *which* comment to cite; the operator's check is what releases the gate.

Be precise about the rest of the path. `review` approves the bot's own PR from a separate pod by calling `submit_outcome(approve)`, never by posting a native forge approval (GitHub 422s a self-authored PR's own approval either way). The operator merges once required checks are green and that verdict is recorded, with no human merge step. You cannot add a branch-protection rule that requires an approving review as a second gate: the platform has one bot identity, so such a rule would deadlock every merge. See [the accepted-risk note](../operations/security/index.md) for what defense in depth looks like instead.

The two allowlists fail in opposite directions. `reporterLogins` (intake) is open by default. `maintainerLogins` (approval) is **closed** by default - populate it or nothing ever advances to implementation. Configure both and tatara is a strongly gated assistant; leave the defaults and name one maintainer, and the merge is autonomous. [Gate 2](agentic-model.md#gate-2-review-approval-then-an-operator-driven-merge) spells out which gates ship on and which you opt into.

**GitOps for everything, tatara included.** tatara deploys through `tatara-helmfile`, a GitOps helmfile repository driven by an in-cluster ARC runner. Operator deploys happen as pull requests that render a diff. Chart versions and image tags are pinned in git. `kubectl set-image` is forbidden.
