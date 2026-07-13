---
title: Deep Architectural Research
---

# Deep Architectural Research

Deep architectural research is a **goal variant of the brainstorm workflow**, not a separate
Task kind. It walks a `tatara-deep-architectural-research` skill decision tree that adds a
"survey the field" step to the ordinary brainstorm loop and frames output as a long-lived
ADR-style artifact rather than a plain proposal issue. This is Phase 1 of a four-phase design
(`docs/2026-06-23-deeper-research-engine.md`); only Phase 1 is deployed today.

!!! note "No dedicated research Task kind"
    An earlier design considered a distinct `architectural-research` Task kind with its own
    egress-allowed pod profile. That was **not adopted** - the decision on record is to widen
    the existing brainstorm pods instead of introducing a separate kind. Everything below runs
    inside the `brainstorm` kind.

## What Phase 1 actually ships

| Piece | Status | Where |
|---|---|---|
| `tatara-deep-architectural-research` SKILL.md + ADR template | Live | `tatara-agent-skills/skills/brainstorming/tatara-deep-architectural-research/` |
| Cheap early-exit via `submit_outcome(action=skip)` | Live | shared with plain brainstorm - see [The skip path](#the-skip-path) |
| `archfitness` import-graph checker | Live, but currently synthetic-test-only | `tatara-operator/internal/archfitness` |
| ADR-framed brainstorm prompt | Live | brainstorm goal-prompt builder |
| Serena MCP env-gate plumbing | Plumbed end-to-end but **inert** | `TATARA_SERENA_URL` unset in helmfile; no Serena deployed |
| Dedicated research Task kind, web-search/arXiv/OpenAlex/Firecrawl MCP servers, egress policy, tournament-rank proposal admission, SCMProvider port refactor, skills-archive/self-improvement loop | **Not built** - design-doc only (Phases 2-4) | n/a |

## The skip path

A research-flavored brainstorm agent that surveys a problem and finds nothing novel or
shippable exits the same way any brainstorm pod does: `submit_outcome(action=skip, reason=...)`,
with a required non-empty `reason` (see [Brainstorm](brainstorm.md#output)). There is no
dedicated `skip_research` tool and no separate outcome shape - research is a goal variant of
brainstorm, so it shares brainstorm's one `submit_outcome` schema, cheap early-exit included.

## The ADR artifact

Where a normal brainstorm proposal is a single issue body forbidden from listing open questions,
a research-flavored proposal is framed as an ADR: problem statement with file:line evidence,
2-3 options with tradeoffs, a recommendation, and (unlike a normal proposal) open questions are
explicitly allowed. It is filed the same way as any other proposal - via
`submit_outcome(action=propose)`, landing as an issue body - it is **not** written to an in-repo
`docs/adr/` directory. Cross-cycle
championing (letting a research thread survive multiple brainstorm cycles, exempt from the
one-issue-per-cycle / silence-over-noise defaults) is called out in the design doc as still
deferred.

## archfitness

`archfitness` (`tatara-operator/internal/archfitness/archfitness.go`) is an import-graph checker:
it loads the module dependency graph and fails a check when core code imports a designated
"banned" vendor SDK (e.g. a raw GitHub/GitLab client) outside an adapter package. It is the
guardrail intended for a future strangler-pattern refactor (e.g. extracting an `SCMProvider`
port) - today it has zero live violations to flag, since that refactor has not started.

## Egress

Research still rides the existing `spec.scm.cron.brainstorm.sources` egress hook (see
[Brainstorm](brainstorm.md#configuring-brainstorm-sources)): setting `internet` in `sources`
stamps `tatara.io/egress: internet` on the brainstorm Pod. No dedicated web-search, academic
paper, or crawl MCP server is wired to consume that egress yet - that is Phase 2 scope.

## Subagent-only fan-out

The per-repo research fan-out that used to go through the `Workflow` tool (with an `ultracode`
effort tier) is now plain `Agent`-tool subagent dispatch - same topology (one subagent per repo
or per research angle, results reported back compactly), cheaper mechanism. `tatara-health-check`
is removed; its "survey and report" behavior is absorbed into brainstorm's own subagent fan-out
described on [Brainstorm](brainstorm.md).

## Current capability (Phase 1)

As deployed, deep architectural research is: the existing brainstorm loop, plus a skill that asks
the agent to frame findings as an ADR against the in-cluster knowledge graph and on-disk sources,
plus the shared `submit_outcome(action=skip)` early-exit and an as-yet-unenforced import-fitness
check (`archfitness`). It is **not** a live, internet-connected, multi-source research agent: the
internet-facing, multi-agent "survey the field" capability from the original design (Phases 2-4)
is unimplemented. Enrolling `internet` in `brainstorm.sources` only stamps an egress label; no
web-search, academic-paper, or crawl MCP server is wired to consume it yet.
