---
title: Brainstorm Workflow
---

# Brainstorm Workflow

The brainstorm workflow is a periodic, autonomous improvement proposal engine. It surveys your
codebase knowledge graph, identifies improvement opportunities, and proposes issues for human
review. No human initiates it - a cron schedule fires it automatically.

## Trigger

Brainstorm is a **maintained backlog**, not a scheduled batch. The operator keeps
`targetOpenProposals` proposals open and awaiting a maintainer decision, and
refills toward that level from three triggers:

- **Maintainer verdict (primary).** The moment a proposal is approved, declined,
  or closed, it leaves the pending set and the operator launches the next
  session as soon as the `minSessionIntervalMinutes` floor has elapsed - no
  refine barrier on this path.
- **Cron (backstop).** `spec.scm.cron.brainstorm.schedule` still fires, still
  behind the [refine barrier](refine.md), and repairs the backlog after a
  dropped event. It is a pure backstop now: there is no breaker for it to reset,
  and it obeys the same session cooldown the event path does.
- **Periodic resync.** The Project reconciler re-evaluates the backlog every 15
  minutes, so a lost watch event costs refill *latency*, never correctness.

Manual: create a `Task` with `kind: brainstorm` against the project.

## One task per project per cycle

Brainstorm runs at **project scope**, not per-repo. At most one non-terminal brainstorm `Task`
may exist for a project at any time - a Task in flight for *any* repo blocks a new one
project-wide. Brainstorm Tasks carry an empty `repositoryRef`; the agent decides which repos to
target internally.

## Refine barrier

Before a due brainstorm tick proceeds, the operator gates it on the [refine workflow](refine.md)
completing a pass first. This is cadence-derived (no separate `refine` cron schedule): a due
brainstorm tick creates a `refine` Task and holds until that Task reaches `done`/`rejected` or
parks. A `refine` Task that fold-verification fails still releases the gate - a broken refine
never wedges brainstorm.

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

Either way the Task reaches `done` directly from `refined` - a brainstorm Task never
passes through `under-implementation` or `awaiting-review` itself, and `status.documentedBy` stays permanently
empty: a brainstorm Task owns no merged MR, so it is never eligible for the nightly
[documentation](documentation.md) batch.

**Each accepted proposal becomes its own new `implement`-origin Task.** The operator opens the SCM issue
in the named repo, mints the Issue CR, and mints a fresh Task with `kind: implement` owning it
(`SweepIssueKind` - the role `clarify` used to play), entering the state machine at `new` like any other newly filed issue. The brainstorm Task
that proposed it and the `implement` Task that inherits it are two separate Task objects from that
point on.

## Backlog target, not a cap

`Project.spec.scm.cron.brainstorm.targetOpenProposals` (default 3) is the number
of proposals the operator keeps open and awaiting a decision across the whole
project. On every trigger it computes

```
deficit = max(0, target - pending - inflight)
```

and spawns one session with `deficit` as its quota when the deficit is positive.

- **pending** counts `Issue` CRs whose provenance is a brainstorm proposal, whose
  forge issue is open, and whose platform status is not `approved`, `rejected` or
  `done`. An **approved** proposal frees its slot immediately, even though its
  forge issue stays open through implementation: it is no longer awaiting a
  decision. Proposals sharing a systemic group count as one slot.
- **inflight** is 1 while a non-terminal brainstorm `Task` exists for the project.
  One brainstorm session runs at a time, project-wide; only the trigger and the
  quota changed.
- The deficit is **clamped at zero.** If pending exceeds the target - after
  lowering the target, or a long stretch with no verdicts - the operator simply
  stops refilling and lets the backlog drain. It never closes a proposal to
  reconcile downward.

The count reads the `Issue` CR mirror in etcd, never the forge, so webhook
delivery lag and search-index staleness cannot cause a double-create.

`maxOpenProposals` is **deprecated**. It is still honoured as a target when
`targetOpenProposals` is unset, so an unmigrated `Project` keeps working; set
`targetOpenProposals` instead.

### Session quota

The spawned session's goal carries the frozen string, verbatim:

```
PROPOSAL QUOTA: file AT MOST <K> proposal(s) in this session. The operator truncates anything beyond <K>.
```

where `<K>` is the deficit clamped to `[1, 5]` - the `submit_outcome` ceiling.
Enforcement is two-sided: the `tatara-brainstorm-guardrails` skill states the
quota to the agent, and the operator **truncates** the submitted array to `<K>`.
Operator-side truncation is the authority, so an agent that ignores the quota
cannot overshoot the target.

### Session cooldown

A skip files no Issue, so the deficit that woke the event path is still positive
when the session ends and the wake fires again immediately. The floor that stops
that busy-loop is `minSessionIntervalMinutes` (default 12): a durable per-project
minimum wall-clock gap between two brainstorm **sessions**, applied whichever
path dispatched the prior one. Refill inside that window is refused with the
distinct reason `cooling-down`, separate from `paused` and `at-target`. The first
session of a project is never gated - the floor delays the next one.

It is a **rate limit, not a circuit breaker**: it never inspects how the prior
session ended, so a skip and a propose are throttled identically. Deliberately
stopping is a separate, explicit thing the agent asks for (`action: exhausted`).

!!! danger "The skip circuit breaker is retired"
    Consecutive `action: skip` sessions no longer increment anything and
    `maxConsecutiveSkips` is not a field. <!-- stale-ok: maxConsecutiveSkips --> The
    breaker suppressed the event path once the counter crossed a threshold and
    **only** a cron tick reset it, so the fast path could be wedged and the slow
    path was the sole un-wedge. It also charged correct behaviour - an agent
    reporting "nothing worth proposing" - toward a brake, so a healthy project
    switched its own fast path off. See `internal/controller/proposalcount.go`.

### Proposal history in the prompt

Each session's turn-0 bundle carries a `<proposal_history>` block: the last
`historyWindow` (default 20) brainstorm proposals, newest first, each with a
status of `open`, `approved` or `declined`, its body, and its comments. The
comments carry *why* a maintainer declined a proposal, which a bare status flag
loses.

This is what stops a killed idea coming back. A discarded proposal is closed, so
the agent's own scan of open issues cannot see it and would happily re-propose
it. The block renders under the `maxBundleBytes` budget, evicting bot comments
first and then whole proposals oldest-first, so the most recent verdicts always
survive.

!!! warning "Declined history is bounded by 14 days, not `historyWindow`"
    A declined proposal's `Issue` CR is retained rather than deleted on close,
    so its rejection comments stay queryable and can feed this history block.
    Its owner `Task` is held from reaping by a dedicated
    `DeclinedProposalRetention` of **14 days** - distinct from the generic
    `RejectedRetention` (24 hours) and from `ParkRetention` (7 days). Without
    this exception the owner `Task` would have been collected at 24 hours, not
    7 days. Every decline is normalised onto this retained shape, including a
    proposal closed while its owner `Task` is `parked(backlog-sweep)` - so it
    does not matter whether you comment before closing.

    After 14 days the mirror is collected and that decline drops out of
    `<proposal_history>` regardless of `historyWindow` - declined entries are
    bounded by the 14-day retention clock first, `historyWindow` second. If
    you decline more than `historyWindow` proposals inside 14 days you see
    only the newest ones; a decline older than 14 days is gone regardless.
    This is a deliberate tradeoff, not an oversight.

This is a different gate from `action: skip`: a skip is a spawned pod choosing to yield nothing
after it looked, not the operator refusing to spawn one. Both trigger paths now run the same
control law - refill when the deficit is positive, the project is not paused, and the session
cooldown has elapsed - so neither path can wedge the other.

## Staleness reaper

There isn't one. `Project.spec.scm.cron.brainstorm.staleProposalDays` is a real CRD field and
describes a reaper that auto-closes bot-authored proposals with no human engagement, but **no
code reads it** - the field is declared and unconsumed, and the operator's `MEMORY.md` records it
as deliberately not built. Nothing auto-closes a stale proposal at any age today.

## Each session starts fresh

There is no conversation carried from a brainstorm session into the `implement`-origin Tasks its
proposals mint, and no fork of it. S3 conversation persistence and restore were removed on
2026-07-04 and the wrapper carries a regression guard against their return. What an implement
pod gets is the ordinary turn-0 [context bundle](../reference/context-bundle.md), which includes
the proposal issue and its full thread.

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
        enabled: true
        schedule: "0 9 * * 1"      # the backstop, not the primary trigger
        targetOpenProposals: 3
        historyWindow: 20
        minSessionIntervalMinutes: 12
        sources:
          - memory    # knowledge graph (always recommended)
          - docs      # docs/ directory content
          - internet  # outbound internet egress (requires NetworkPolicy)
```

With `internet` in `sources`, the operator stamps `tatara.io/egress: internet` on the brainstorm
Pod, which a NetworkPolicy can use to grant `0.0.0.0/0` egress for that pod class only. As of
Phase 1 of the [deep architectural research](research.md) build, this remains the only egress
hook - no dedicated web-search/academic MCP servers are wired yet.

## Budget

A brainstorm Task runs at `state=refined`, which - like every live state - carries the idle
budget (`ConversationIdleDefault`, 60m by default) rather than a dedicated fixed work budget; on
elapse the Task parks at `parked(awaiting-human)`. See the [state machine](../reference/task-stages.md)
for the full deadline and reason table. (Pre-#521 this was a dedicated `brainstorming` stage with
its own 2h work budget, parking at `stage-deadline` - the redesign folded it into the same idle
clock every live state shares.)
