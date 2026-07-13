---
title: Brainstorm Workflow
---

# Brainstorm Workflow

The brainstorm workflow is a periodic, autonomous improvement proposal engine. It surveys your
codebase knowledge graph, identifies improvement opportunities, and proposes issues for human
review. No human initiates it - a cron schedule fires it automatically.

## Trigger

- **Cron:** `spec.scm.cron.brainstorm.schedule` on the `Project` CR.
- **Manual:** create a `Task` with `kind: brainstorm` against the project.

## One task per project per cycle

Brainstorm runs at **project scope**, not per-repo. At most one non-terminal brainstorm `Task`
may exist for a project at any time - a Task in flight for *any* repo blocks a new one
project-wide. Brainstorm Tasks carry an empty `repositoryRef`; the agent decides which repos to
target internally.

## Refine barrier

Before a due brainstorm tick proceeds, the operator gates it on the [refine workflow](refine.md)
completing a pass first. This is cadence-derived (no separate `refine` cron schedule): a due
brainstorm tick creates a `refine` Task and holds until that Task reaches a terminal stage. A
`refine` Task that ends in `failed` still releases the gate - a broken refine never wedges
brainstorm.

## Output

The pod's only path forward is `submit_outcome`:

```json
{"action":"propose","proposals":[
  {"repo":"tatara-operator","title":"...","body":"...","kind":"bug"}
]}
```
or
```json
{"action":"skip","reason":"..."}
```

`proposals` holds 1 to 5 entries, each naming its own target repo, a title, a body, and
`kind` (`bug` or `improvement`). `skip` requires a non-empty `reason` and is the agent's
cheap early-exit when a survey pass finds nothing novel or shippable - it costs one turn, not a
proposal fan-out for nothing.

Either way the Task reaches `delivered` directly from `brainstorming` - a brainstorm Task never
passes through `implementing` or `reviewing` itself, and `status.documentedBy` stays permanently
empty: a brainstorm Task owns no merged MR, so it is never eligible for the nightly
[documentation](documentation.md) batch.

**Each accepted proposal becomes its own new `clarify` Task.** The operator opens the SCM issue
in the named repo, mints the Issue CR, and mints a fresh Task with `kind: clarify` owning it,
entering the stage machine at `triaging` like any other newly filed issue. The brainstorm Task
that proposed it and the `clarify` Task that inherits it are two separate Task objects from that
point on.

## Proposal limits

`Project.spec.scm.cron.brainstorm.maxOpenProposals` is an **operator-side pre-admission gate**,
not an agent decision: on a due brainstorm tick the operator sums the open-proposal backlog
across the project's repos and, at or over the cap, skips the cycle entirely - no Task is
minted, no pod spawns, no tokens are spent. See [Project reference](../reference/project.md) for
the field's current default and scope.

This is a different gate from `action: skip`: the cap is the operator refusing to spawn a pod at
all; `skip` is a spawned pod choosing to yield nothing after it looked.

## Staleness reaper

`Project.spec.scm.cron.brainstorm.staleProposalDays`, when set to a positive value, opts in a
reaper that auto-closes bot-authored proposal issues with no human engagement for at least that
many days. The unset default disables it entirely - an explicit opt-in, not a kubebuilder
default.

## Conversation forking

When a brainstorm agent's proposals are accepted, each resulting `clarify` Task gets a **forked
copy** of the brainstorm conversation (S3 copy-object) as its starting context, without the
transcripts interfering with each other.

## Fan-out for wide surveys

For a survey deep enough to need per-repo or per-concern isolation, the brainstorm agent fans
out `Agent`-tool subagents split by context boundary rather than holding all of it in one
context - each subagent reports back a compact result. The agent never uses the retired
`Workflow` tool or `ultracode` effort tier for this fan-out; the same principle applies to
[incident](incident.md#4-grafana-mcp-access)'s fan-out.

## Configuring brainstorm sources

```yaml
spec:
  scm:
    cron:
      brainstorm:
        schedule: "0 9 * * 1"
        sources:
          - memory    # knowledge graph (always recommended)
          - docs      # docs/ directory content
          - internet  # outbound internet egress (requires NetworkPolicy)
        maxOpenProposals: 5
        staleProposalDays: 14
```

With `internet` in `sources`, the operator stamps `tatara.io/egress: internet` on the brainstorm
Pod, which a NetworkPolicy can use to grant `0.0.0.0/0` egress for that pod class only. As of
Phase 1 of the [deep architectural research](research.md) build, this remains the only egress
hook - no dedicated web-search/academic MCP servers are wired yet.

## Budget

`brainstorming` carries a 2h stage-work budget; on elapse the Task parks at
`parked(stage-deadline)`. See the [stage machine](../reference/task-stages.md) for the full
deadline and reason table.
