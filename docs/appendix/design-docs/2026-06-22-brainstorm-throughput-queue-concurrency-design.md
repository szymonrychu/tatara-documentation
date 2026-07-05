# Brainstorm throughput + queue-as-concurrency

Date: 2026-06-22
Status: approved, ready for planning
Repos touched: tatara-operator, tatara-cli, tatara-claude-code-wrapper, tatara-helmfile

## Problem

Brainstorm runs hourly per project but delivers very few new issues. Root
cause is three compounding throttles, evidenced live (Prometheus + Loki,
2026-06-22):

1. **Prompt yield.** `brainstormGoalProject` (operator
   `internal/controller/projectscan.go:1052`) hands the agent a dedup-first
   mandate that emits at most ONE `propose_issue` per cycle (6 only for a rare
   systemic idea) and biases toward commenting on existing issues over
   proposing new ones. Most cycles produce 0 new issues.
2. **`maxOpenProposals` cap, low.** Live `tatara`=8, `infrastructure`=3. When
   the open-proposal backlog hits it, every hourly cycle logs
   `brainstorm: project backlog at cap; skipping cycle` and creates nothing
   (`projectscan.go:917`). `infrastructure` sits pinned at 3/3 almost every
   hour.
3. **Queue abandonment.** A second, redundant cap `QueuedAutonomousCap`
   (=`maxOpenTasks`, 6 for `tatara`) makes crons STOP creating events when the
   queued-autonomous count is reached, abandoning the cycle
   (`projectscan.go:1586`, `630-634`). Brainstorm runs 4th in this shared
   budget (after mrScan, issueScan, recoverOrphans), so it is frequently
   starved before it runs.

The concurrency limiter the platform actually wants already exists and works:
`QueueCapacity` (=`maxConcurrentTasks`) gates how many tasks run at once; the
dispatcher (`internal/controller/queue_controller.go:84-156`) admits up to that
limit and leaves the rest in `Queued`, draining as slots free. The
`QueuedAutonomousCap` is a separate creation cap layered on top, and it is the
behavior that abandons work.

Nursing of existing issues already exists separately: `issueScan` reactivates a
Conversation/Stopped lifecycle Task on a new human comment after
`LastActivityAt` and resets the idle deadline (`projectscan.go:757-785`); the
TTL is `conversationIdleMinutes` (default 60; `lifecycle.go:1131-1177`).
Brainstorm therefore does not need to comment on existing issues.

## Goals

- Brainstorm events are never abandoned; if the agent slot is busy they queue
  and run later, bounded only by concurrency.
- Per-project per-activity no-overlap is preserved (no two concurrent
  brainstorms for one project).
- Open-proposal ceiling raised to 10.
- Brainstorm focuses purely on NEW ideas; nursing stays with issueLifecycle.
- Brainstorm can exit early (before the expensive deep-research fan-out) when
  there is nothing worth proposing, to avoid burning tokens.

Non-goals: changing issueScan/mrScan nursing behavior; changing
`conversationIdleMinutes`/`babysitDeadlineMinutes`; adding a hard "give-up"
TTL.

## Design

### 1. Queue: concurrency-only (operator)

Remove the autonomous enqueue gating:

- Delete the `remaining *int` budget parameter and its decrement/short-circuit
  from `mrScan` (`projectscan.go:587`), `issueScan` (`705`), `brainstorm`
  (`834`), `healthCheck` (`952`), `recoverOrphans` (`1464`), and the budget
  computation + `scan: at queued-autonomous cap` log in `runScans`
  (`1580-1593`, re-list at `1631-1638`).
- Remove the `skipped_budget` ScanItem outcomes.
- Concurrency is bounded solely by `QueueCapacity` in the dispatcher (existing,
  unchanged). Over-limit events wait in `Queued`; the dispatcher requeues every
  30s to drain as slots free (`queue_controller.go:243-266`).
- **No-overlap preserved by existing dedup:**
  - brainstorm: constant per-project dedup key `"brainstorm-"+proj.Name`
    (`projectscan.go:343`) + `brainstormInFlightProject` guard (`843`).
  - healthCheck: mirrors brainstorm; verify it uses an equivalent constant
    dedup key + in-flight guard during planning, fix if missing.
  - mrScan/issueScan/backstop: per-work-item dedup (per PR / per linked issue
    number), which is the correct no-overlap granularity.
- **API fields:** keep `maxOpenTasks` and `Queue.QueuedAutonomousCap` in the
  CRD for backward compatibility, but stop enforcing them. Mark them deprecated
  / ignored in the Go doc comments (`api/v1alpha1/project_types.go:255-258`,
  `283-286`) and `QueuedAutonomousCap()` becomes unused (remove its call sites;
  keep or remove the method per lint). Do NOT delete the CRD fields (avoids a
  CRD migration + helmfile values break). Record the deprecation rationale in
  operator `MEMORY.md`.
- Keep the `operator_queue_depth` gauge for visibility into queued backlog.

This is distinct from `maxOpenProposals` (the proposal-backlog gate), which
stays.

### 2. `maxOpenProposals` -> 10

- Change the in-code fallback in `brainstorm` and `healthCheck`
  (`projectscan.go:838-840`, `956-957`) from 5 to 10.
- Set `maxOpenProposals: 10` for existing projects via tatara-helmfile values:
  the `tatara` project (currently 8) and the `infrastructure` project
  (currently 3).

### 3. Brainstorm prompt: new ideas only (operator)

Rewrite the DEDUP RULE / ACTION RULE in `brainstormGoalProject`
(`projectscan.go:1060-1087`):

- Drop path 2 (comment on a related existing issue). Brainstorm no longer calls
  `comment_on_issue`.
- Duplicate of an existing open issue -> skip: finish with a one-line note
  naming the duplicate, no tool call.
- Genuinely novel + standalone -> exactly ONE `propose_issue`, whose body
  DECOMPOSES the problem into smaller sub-problems / decision points and, for
  each, offers 2-3 concrete implementation options (one-line tradeoff each) plus
  a recommended pick. The maintainer's decision is choosing an option per
  sub-problem, giving granular directional control. Concrete options + a
  recommendation always - never a flat list of open questions.
- Systemic improvement spanning >=2 repos -> up to 6 `propose_issue` calls
  sharing one generated `systemicId` (kept; this is the cross-repo leverage
  lever).
- Add the early-exit instruction (see 4).

Yield stays at one proposal per cycle for the common case; throughput comes from
the non-abandoning queue + the raised cap + frequency, not from batching.

### 4. Brainstorm early-exit (cli + operator)

- **tatara-cli:** add `skip_brainstorm` MCP tool to `OperatorTools`
  (`internal/mcp/tools.go`, alongside `decline_implementation` at `525`). Args:
  `task`, `reason` (non-empty). Issues `POST /tasks/{task}/brainstorm-outcome`
  with `{"action":"none","reason":"..."}`.
- **operator restapi:** add a `brainstormOutcome` handler (mirror
  `implementOutcome`, `internal/restapi/handlers.go:833-890`) that validates
  `action=="none"` + non-empty reason and writes
  `Task.Status.BrainstormOutcome` (new optional status field).
- **operator lifecycle/writeback:** on a brainstorm task with
  `BrainstormOutcome.Action=="none"`, terminate the turn, map writeback to
  `BrainstormComplete` with the reason, record a brainstorm-outcome metric
  (e.g. a `none` outcome on the scan/issue-outcome counter), and clear the
  field. Silent finish (no tool calls) continues to map to `BrainstormComplete`
  as today.
- **prompt:** instruct the agent to do a cheap initial scan first; if nothing is
  worth proposing, call `skip_brainstorm(reason)` and exit BEFORE dispatching
  the per-repo deep-research fan-out. The fan-out runs only once the agent has
  decided there is a real candidate.
- **wrapper:** bump `TATARA_CLI_VERSION` pin to the cli version that ships
  `skip_brainstorm`. The wrapper image build guard requires `tatara mcp`
  tools/list to serve the new tool WITHOUT a token (per the wrapper-cli-pin
  contract).

### Deploy (GitOps, per the deploy hard rule)

Order:
1. tatara-cli merge to main -> CI builds image (new `skip_brainstorm`).
2. tatara-claude-code-wrapper: bump cli pin -> merge -> CI builds wrapper image.
3. tatara-operator merge to main -> CI builds image.
4. One tatara-helmfile MR: bump operator + wrapper image tags AND chart
   versions, and set `maxOpenProposals: 10` for the `tatara` and
   `infrastructure` projects. Review via diff, apply via pipeline.

No `kubectl set image` / `patch` / `helm upgrade` by hand.

## Testing (TDD)

**operator** (`internal/controller`, `internal/restapi`):
- runScans/mrScan/issueScan/brainstorm enqueue beyond the old
  `QueuedAutonomousCap` now create Queued events instead of skipping
  (`skipped_budget` outcome gone).
- brainstorm/healthCheck still single-in-flight per project (no second event
  while one is Queued or Admitted).
- `brainstormGoalProject` output no longer instructs `comment_on_issue`; asserts
  the propose-or-skip + systemic + early-exit contract text.
- `brainstormOutcome` endpoint: `action=="none"` + reason records status +
  terminates; rejects empty reason; rejects non-brainstorm tasks.
- in-code `maxOpenProposals` fallback is 10.

**tatara-cli** (`internal/mcp`):
- `skip_brainstorm` appears in tokenless `tatara mcp` tools/list.
- call builds the correct `POST /tasks/{task}/brainstorm-outcome` body.

## Risks / notes

- Removing the autonomous cap lets the queue depth grow under heavy scan load.
  Bounded in practice: scans dedup per work-item, backstop dedups per orphan,
  brainstorm/healthCheck are single-in-flight, so total Queued is bounded by
  distinct real work items. `operator_queue_depth` gives visibility; concurrency
  still bounds running pods.
- Keeping deprecated CRD fields is mild debt; chosen over a CRD migration +
  helmfile values break. Documented in operator `MEMORY.md`.
- `maxOpenProposals` (proposal backlog) and `QueuedAutonomousCap` (queue
  creation) are different knobs; only the latter is removed.
