---
title: The Big Picture
---

# The Big Picture

## What tatara is trying to solve

Software engineering teams accumulate backlog faster than they can ship. Issues pile up, minor improvements are perpetually deferred, and on-call engineers spend time on repetitive triage. Meanwhile, AI coding tools (GitHub Copilot, Claude, GPT-4) are good at writing code given a clear spec - but they are stateless. Each session starts from scratch, forgets what was decided before, and has no awareness of the broader system.

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
        | You open an issue, add "tatara" label
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
        | Human reviews, approves, merges
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

## What humans control

Tatara is not fully autonomous. Humans decide:

1. **Which issues to act on** (by adding the `tatara` label)
2. **Whether to approve the agent's plan** (by commenting in the issue thread)
3. **Whether to merge the PR** (by reviewing and approving in GitHub)

The agent proposes and implements; humans decide and merge. This is the design intent, not a limitation.

## What makes tatara different from a CI/CD script

A CI/CD script runs fixed commands. Tatara runs an AI agent that reasons about your specific codebase. The agent reads your code, understands your patterns, asks clarifying questions if the spec is ambiguous, and writes code in your style.

The tradeoff is that agent behavior is probabilistic, not deterministic. The human approval gates exist precisely because of this: you review the plan before implementation, and you review the code before merge.

## What could go wrong (and how tatara handles it)

| Problem | How tatara handles it |
|---|---|
| Agent writes bad code | Human reviews the PR before merge |
| Agent misunderstands the issue | Triage plan is posted for approval before any code is written |
| Pod crashes mid-task | Conversation is persisted to S3; next pod resumes where it left off |
| Queue overloaded | QueuedEvent admission queue bounds concurrency |
| CI breaks | Agent investigates and retries in the MRCI phase |
| Incident fires at 3am | Alert webhook spawns investigation agent; human reviews in the morning |
