---
title: The Big Picture
---

# The Big Picture

Read this page and you will know what tatara is made of, why the pieces are arranged the way they are, and which decisions the platform hands back to you. If you would rather see it happen first, [Watch One Run](watch-one-run.md) follows a single real piece of work end to end.

## The problem it is built for

Your backlog grows faster than you can ship it. Issues pile up, small improvements get deferred forever, and on-call time goes to repetitive triage. AI coding assistants are good at writing code from a clear spec, but on their own they are stateless: every session starts from nothing, forgets what was decided last time, and knows nothing about the rest of your system.

tatara supplies the three things that are missing around the model: durable memory of your codebase, task tracking that lives in Kubernetes rather than in a chat window, and a loop that runs against your GitHub or GitLab workflow instead of beside it.

## The forge metaphor

The name comes from the Japanese *tatara* iron-smelting forge: a collective, repeated process organized around a permanent substrate. That is the design intent, and it maps onto three real components.

- The **permanent substrate** is the knowledge graph in LightRAG and Neo4j. It lives in your cluster, grows as your codebase changes, and outlives every agent session. See [How Agents Remember Your Code](memory-graph.md).
- The **ephemeral sessions** are the Claude Code pods. Each starts, does one bounded piece of work, and terminates. The next one picks up where the last left off because the graph and the Task both survive it.
- The **repeated process** is the issue-to-pull-request loop: propose, approve, implement, review, merge.

## The platform in one diagram

```
Your GitHub/GitLab org
        |
        | You open an issue (no label required)
        |
        v
tatara-operator (Kubernetes)
        |
        | 1. Mints a Task CR
        | 2. Spawns a claude-code-wrapper Pod
        |
        v
Claude Code (inside the Pod)
        |
        | reads codebase memory
        | writes code
        | submits an outcome; the operator opens the PR
        |
        v
GitHub/GitLab PR
        |
        | a review pod returns a verdict via submit_outcome
        | the operator posts the review, then merges on green CI
        |
        v
Done
```

The operator is the glue, and it is the only thing that writes state. It watches your SCM for events, keeps the admission queue, spawns and reaps agent pods, and writes results back to the forge. No agent ever asks for a state; it submits an outcome and the operator decides what that outcome means.

## What the agent actually does

Inside each pod, Claude Code runs the way it would on your laptop: the full interactive mode, all its tools, the same decision loop. Three things differ.

- A Go supervisor submits turns programmatically instead of a human typing them.
- The agent works in a cloned repository under `/workspace` rather than your local files.
- Instead of reading your codebase from scratch, it queries a pre-built graph of it.

The agent reads files, writes code, runs tests, calls platform tools over MCP, and pushes commits. It cannot log into arbitrary services or reach secrets it was not given. [Inside an Agent Session](agent-sessions.md) has the mechanics.

## Not every unit of work starts with you

A Task's origin kind records why it exists, and reacting to an issue you filed is only one of the eight. The platform also proposes and grooms its own backlog, answers alerts, keeps dependencies current, and ships its own components.

- **brainstorm** proposes net-new work as fresh issues, capped per project. A bot-filed proposal faces exactly the same approval gate as a human-filed one, not a lighter one.
- **refine** grooms the existing backlog, folding duplicates and closing stale proposals. It opens nothing and implements nothing.
- **incident** fires from a Grafana alert webhook. An alert at three in the morning spawns an investigation pod that gathers evidence and writes up what it found, ready for whoever reads it over coffee.
- **upgrade** keeps dependencies moving, on a schedule you configure. It is off until you turn it on.
Shipping is not a kind of its own. **push-CD** is what happens after any of them merges: a change with a declared significance rides a semver release cascade to a `tatara-helmfile` apply, walked through in [From Issue to PR](issue-to-pr.md).

The full trigger table for all eight kinds is in [The Agentic Operating Model](../concepts/agentic-model.md#origin-kinds-and-where-they-trigger).

## What stays with you

tatara runs most of its own loop. Three decisions do not move.

1. **Which repositories are enrolled.** tatara engages every open issue in an enrolled repository; no label is required. A trigger label, where you configure one, changes only whether the operator picks the issue up immediately or on its next scan. It does not skip the conversation and it does not grant approval.
2. **Whether any given piece of work gets built.** The only approval action is a maintainer's comment on the issue. The `implement` agent judges whether a comment approves, cites it with a comment id and a verbatim quote, and pins the plan it wants approved. The operator then re-checks that citation against its own mirror before a line of code is written. Labels are a write-only projection of state and never a way to grant anything.
3. **The branch-protection posture around merge.** The merge itself is operator logic, not a forge-enforced gate. Because the platform has one bot identity, a rule requiring an approving review would deadlock every merge; a no-direct-push rule on `main` is the control you do have.

[The Agentic Operating Model](../concepts/agentic-model.md#human-in-the-loop-gates) spells out both gates in full, and [Approval Gates](../operations/security/approval-gates.md) covers the grammar the operator checks.

## Why this is not a CI/CD script

A CI/CD script runs fixed commands. tatara runs an agent that reasons about your specific codebase: it reads your code, follows your patterns, asks when the spec is ambiguous, and writes in your style.

The cost of that is behavior you cannot fully predict. The gates exist because of it. The approval conversation shapes direction, but code-writing waits on a maintainer comment the agent cites and the operator independently verifies, and a separate `review` pod reads the diff before the operator merges on green CI. The reviewing pod is never the pod that wrote the code, which is what makes the separation structural rather than a matter of instruction.

## What could go wrong, and what happens then

| Problem | What tatara does |
|---|---|
| The agent writes bad code | A separate `review` pod reads the diff and returns a verdict through `submit_outcome`. The operator merges only on an accepted approve, at the exact reviewed head SHA, with green CI |
| The agent misunderstands the issue | The approval conversation settles direction before any code is written, and the plan it pins is hashed and re-checked at code-writing time |
| A pod dies mid-task | Each pod's life is bounded by a TTL. Near expiry it writes a handoff note to `Task.status.notes`, or the operator writes one for it, and the next pod reads it at turn 0. The Task persists across pods; no session is ever resumed |
| The queue is overloaded | The `QueuedEvent` admission queue bounds concurrency on `Project.spec.maxConcurrentAgents`. Set it to `0` for a full-project pause |
| CI goes red | A red pipeline sends the Task back to `under-implementation` for another attempt, bounded at three laps before it parks at `ci-red` |
| An alert fires overnight | The webhook spawns an `incident` pod that investigates read-only and files what it found. A human decides in the morning |
| Something stalls that nobody can fix automatically | The Task parks with a named `parkReason` and stops spending agents on it until a person replies. It does not retry blindly |
