---
title: How It Works
---

# How It Works

Read these five pages and you will know what tatara does, why it is shaped this way, and whether it fits your organization. They are ordered to be read straight through, and the first one is different from the rest: it is a single run that actually happened, quoted from the thread it happened in, rather than a general explanation. Once you have the model, [Reference](../reference/index.md) has the field-level detail.

<div class="grid cards" markdown>

-   :material-telescope: **Watch One Run**

    ---

    Start here. One real piece of work in tatara's own repository, nine hours on 2026-08-17, with every quote copied out of the issue and the pull request. An alert files the issue, a maintainer types two words to release it, a review round catches a bug in the agent's own diff that would have recorded somebody else's merged work as rejected, the agent declines half of another finding in public with its reasons, and the change deploys. The other four pages tell you how the machine works; this one shows you what it looked like.

    [:octicons-arrow-right-24: Watch One Run](watch-one-run.md)

-   :material-map: **The Big Picture**

    ---

    What the pieces are, why they are arranged this way, and which decisions stay with you rather than with the agents.

    [:octicons-arrow-right-24: The Big Picture](big-picture.md)

-   :material-source-pull: **From Issue to PR**

    ---

    The same journey as a numbered walkthrough: every state a Task passes through, what runs in each one, and what you can do at each point.

    [:octicons-arrow-right-24: From Issue to PR](issue-to-pr.md)

-   :material-graph: **How Agents Remember Your Code**

    ---

    Why an agent that queries a pre-built graph of your repository beats one that reads 50 files to answer the same question, and how that graph gets built and kept fresh.

    [:octicons-arrow-right-24: The Memory Graph](memory-graph.md)

-   :material-robot-outline: **Inside an Agent Session**

    ---

    What actually runs in an agent pod: a real interactive Claude Code session driven over a pseudo-terminal, and what carries forward when the pod goes away.

    [:octicons-arrow-right-24: Agent Sessions](agent-sessions.md)

</div>

If you want the model rather than the narrative, [The Agentic Operating Model](../concepts/agentic-model.md) covers the eight Task states, the two human gates, and the security boundary in one page.
