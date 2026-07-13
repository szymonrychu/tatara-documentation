---
title: The Big Picture
---

# The Big Picture

## What tatara is trying to solve

Software engineering teams accumulate backlog faster than they can ship. Issues pile up, minor improvements are perpetually deferred, and on-call engineers spend time on repetitive triage. Meanwhile, AI coding assistants (GitHub Copilot, Claude Code, Cursor, and the like) are good at writing code given a clear spec - but on their own they are stateless. Each session starts from scratch, forgets what was decided before, and has no awareness of the broader system.

Tatara connects the dots: it gives AI agents **durable memory** of your codebase, **structured task tracking** in Kubernetes, and a **continuous feedback loop** with your GitHub/GitLab workflow.

## The forge metaphor

The name comes from the Japanese *tatara* iron-smelting forge: a collective, iterative process organized around a permanent substrate. That is the design intent:

- The **permanent substrate** is the knowledge graph (LightRAG + Neo4j). It lives in your cluster, grows as your codebase evolves, and survives across agent sessions.
- The **ephemeral sessions** are the Claude Code pods. They start, do a piece of work, commit it, and terminate. The next session picks up where the last one left off because the graph is still there.
- The **iterative process** is the issue-to-PR loop: propose, approve, implement, review, merge, repeat.

## The platform in one diagram

```
Your GitHub/GitLab org
        |
        | You open an issue (no label required)
        |
        v
tatara-operator (Kubernetes)
        |
        | 1. Creates a Task CR
        | 2. Spawns a claude-code-wrapper Pod
        |
        v
Claude Code (inside the Pod)
        |
        | reads codebase memory
        | writes code
        | opens PR
        |
        v
GitHub/GitLab PR
        |
        | review pod posts its verdict via submit_outcome
        | operator merges on an accepted verdict + green CI
        |
        v
Done
```

The operator is the glue. It watches your SCM for events, maintains the queue, spawns and monitors agent pods, and writes results back.

## What the agent actually does

Inside each pod, Claude Code runs as it would on your laptop - in its full interactive mode, with all its tools, skills, and the same decision-making loop. The difference is the environment:

- Instead of a human at a keyboard, a Go supervisor submits messages programmatically
- Instead of your local files, the agent works in a cloned repository mounted in `/workspace`
- Instead of reading docs from scratch, it queries a pre-built knowledge graph of your codebase

The agent can read files, write code, run tests, call APIs (via MCP tools), and push commits. It cannot log into arbitrary services or access secrets it was not explicitly given.

## The loop runs itself, mostly

Reacting to human-filed issues is only one of the agent kinds tatara runs. The platform also generates and maintains its own backlog, responds to alerts, and can deploy itself:

- **brainstorm** proposes net-new work as fresh issues (capped per project). A bot-filed proposal will not advance into implementation until a maintainer comments an approval phrase (`lgtm`, `approve`, `ship it`, and the like) on it - the same gate every other issue goes through, not a lighter one.
- **refine** grooms the existing backlog - tightening, de-duplicating, and closing stale proposals - without opening or implementing anything itself.
- **incident** fires from Grafana alert webhooks: an alert at 3am spawns an investigation agent that gathers context and reports back for a human in the morning.
- **push-CD** lets tatara ship its own components: a merged change with a declared significance rides a semver release cascade all the way to a `tatara-helmfile` apply (walked through in [From Issue to PR](issue-to-pr.md)).

So not every unit of work starts from a human-labeled issue. What stays with humans is the scope (which repos are enrolled), approval of proposed net-new work, and the branch-protection controls around merge. Within those bounds the loop is largely self-driving.

## What humans control

Tatara runs much of its own loop, but the load-bearing decisions stay with humans:

1. **Which repositories are enrolled.** Tatara clarifies *every* open issue in an enrolled repo - no label is required. The optional trigger label only affects intake (whether the operator engages immediately versus on the next scan); it does not skip the conversation and does not grant approval.
2. **Whether to approve any work the agent should implement - proposed or human-filed alike.** The only approval action is a maintainer's most recent comment on the issue matching one of `Project.spec.scm.approvalPhrases` (an anchored, whole-line match - not a substring, and not sticky once matched). Nothing else releases it: not a comment in `clarify`'s conversation, not the issue reporter matching the phrase, not the bot matching it. Labels are a write-only projection of that decision, never a way to grant it - see [Approval Gates](../operations/security/approval-gates.md).
3. **The merge gate is operator logic, not a forge-enforced one.** `review` (a separate bot pod that, under tatara's single bot identity, structurally cannot approve its own diff) posts its verdict on the PR, and the operator merges it on green CI with no human step. Because one identity opened and would review the PR, branch protection cannot require an approving review without deadlocking every merge; a no-direct-push rule on `main` is the control humans do have. See [Approval Gates](../operations/security/approval-gates.md).

The agent proposes, grooms, implements, and reviews its own work, and can even ship its own components; humans set scope and gate approval into implementation. This is the design intent, not a limitation.

## What makes tatara different from a CI/CD script

A CI/CD script runs fixed commands. Tatara runs an AI agent that reasons about your specific codebase. The agent reads your code, understands your patterns, asks clarifying questions if the spec is ambiguous, and writes code in your style.

The tradeoff is that agent behavior is probabilistic, not deterministic. The approval gates exist precisely because of this: `clarify`'s conversation shapes the direction, but implementation itself waits on a maintainer's approval-phrase comment before any code is written, and a separate `review` pod reviews the code before the operator merges it on green CI - with a no-direct-push branch-protection rule as the available defense-in-depth (a single bot identity means branch protection cannot require an approving review; see [Approval Gates](../operations/security/approval-gates.md)).

## What could go wrong (and how tatara handles it)

| Problem | How tatara handles it |
|---|---|
| Agent writes bad code | A separate `review` pod reviews the PR and reports its verdict via `submit_outcome`; the operator merges only on an accepted approve verdict at the exact reviewed head SHA, with green CI |
| Agent misunderstands the issue | `clarify` runs the conversation and agrees direction before any code is written |
| Pod crashes mid-task | Each pod's life is bounded by a TTL; near expiry it writes a handoff note to `Task.status.notes` (or the operator writes one for it) and the next pod reads it at turn 0 - the Task persists across pods, no session is "resumed". A local, in-pod crash relaunches the same pod via `--continue` |
| Queue overloaded | `QueuedEvent` admission queue bounds concurrency (`Project.spec.maxConcurrentAgents`, 0 = full pause) |
| CI breaks | `implement` investigates and retries; a failed pipeline routes back through `implement` |
| Incident fires at 3am | Alert webhook spawns an `incident` investigation pod; human reviews in the morning |
