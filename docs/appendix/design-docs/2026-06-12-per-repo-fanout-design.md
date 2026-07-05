# Per-repo all-items fan-out - design (2026-06-12)

## Problem

The autonomous cron processes too little. `mrScan`/`issueScan` run hourly and
`selectCandidates(eligible, priorityLabel, maxPerCycle)` truncates to
`maxPerCycle` (=1) items **globally across all repos** per cycle. With a backlog
of ~20 open issues + ~12 open MRs across 6 repos and a drain rate of 1 issue +
1 MR per hour, the queue never clears and most items wait days.

Goal: every open issue/MR in every project repo gets triaged, parallelised by
repo, bounded by a total-concurrency ceiling so the cluster is not overloaded.

## Decision: per-repo lanes

Each repo gets a "lane" holding at most `maxPerRepo` (default 1) in-progress
Tasks. The scan tops up every repo's lane each run, picking that repo's best
remaining un-triaged item (priority-then-stale). As a Task finishes, the lane
refills with the next item. N repos with work -> up to N concurrent agents,
capped by the existing per-Project `MaxConcurrentTasks` ceiling.

Reuses the one-Task-per-item model and the existing agent unchanged. Rejected:
one long-lived "repoTriage" mega-session per repo (fights the one-goal-per-Task
+ one-turn-per-session wrapper model; long sessions risk context limits and
partial failure).

## Components (`tatara-operator`)

### 1. Per-repo top-up selection (replaces global pick-N)

In `internal/controller/projectscan.go`, `mrScan` and `issueScan` currently do:
`selected = selectCandidates(eligible, priorityLabel, act.MaxPerCycle)` then
create one Task per `selected`. Replace with per-repo top-up:

- Group the deduped `eligible` candidates by repo slug (`candidate.repo`).
- For each repo, compute `laneOccupancy(repo)` = count of this Project's Tasks
  with `Spec.RepositoryRef == <repo>` whose phase is non-terminal AND not
  `AwaitingApproval` (i.e. pending/gated, `Planning`, or `Running`). Terminal
  (`Succeeded`/`Failed`) and `AwaitingApproval` do NOT occupy the lane.
- Create up to `maxPerRepo - laneOccupancy(repo)` Tasks from that repo's
  candidates, ordered priority-then-stale (reuse the ordering inside
  `selectCandidates`, applied per-repo with `n = maxPerRepo - laneOccupancy`).

Dedup is unchanged (vs all non-terminal Tasks for the item), so a handled item -
including one sitting in `AwaitingApproval` - is never recreated; the lane simply
advances to the next not-yet-Tasked item.

`maxPerRepo - laneOccupancy <= 0` -> create nothing for that repo this run.

### 2. Approval gate does not block the lane

Because `laneOccupancy` excludes `AwaitingApproval`, a repo whose triaged items
pile up as un-approved proposals keeps draining the rest of its backlog. The
human approves the proposal pile at their own pace; it never stalls triage. (This
is exactly today's 20 `implement/AwaitingApproval` Tasks - handled, awaiting the
approval label.)

### 3. Global concurrency ceiling retained

The existing per-Project `Spec.MaxConcurrentTasks` (enforced in
`task_controller.go` `atConcurrencyCap`, counting `isActive` Tasks) is the hard
total-load bound. The scan may create more Tasks than the ceiling; the task
controller gates execution so only `MaxConcurrentTasks` run at once and the rest
wait. So concurrent agents = `min(repos-with-work x maxPerRepo,
MaxConcurrentTasks)`. Live `tatara` Project set to `MaxConcurrentTasks: 3`.

### 4. Backlog-aware cadence

`runScans` (Project reconciler) requeues to the next cron fire. Add: when a scan
runs and any repo still has open items with no Task (backlog beyond lane
capacity), return a short requeue (`backlogRequeue = 60s`) as the soonest instead
of the hourly next-fire. When every lane is at capacity / no un-Tasked items
remain, revert to the cron schedule. This refills freed lanes within ~a minute
without creating a Task flood, and idles back to hourly once drained.

`runScans` already returns a `soonest` duration; fold `backlogRequeue` into the
`consider(...)` reduction when a backlog is detected during `mrScan`/`issueScan`.

### 5. CRD field rename

`MRScan` and `IssueScan` are both the `CronActivity` type; `Brainstorm` is a
separate `BrainstormActivity` type (`api/v1alpha1/project_types.go`). So renaming
`CronActivity.MaxPerCycle` -> `MaxPerRepo` (json `maxPerCycle` -> `maxPerRepo`,
keep `+kubebuilder:default=1`) cleanly affects only the two scans;
`BrainstormActivity` keeps its own `MaxPerCycle` (genuinely per-cycle). Regenerate
the CRD (`make manifests`). Update the live `tatara` Project manifest
(`issueScan.maxPerRepo: 1`, `mrScan.maxPerRepo: 1`) - the default is 1 so a
dropped value is harmless, but update it for clarity. Only the `tatara` Project
exists, so no compatibility shim is needed.

## Scope

- `mrScan` + `issueScan` only. `brainstorm` unchanged (daily, genuinely 1/cycle).
- Approval gate, egress gate, author/actor gate, dedup: all unchanged.
- `selectCandidates` ordering logic (priority-then-stale) reused per-repo, not
  rewritten.

## Data flow

```
hourly (or 60s while backlog) scan:
  for each repo:
    occ = laneOccupancy(repo)            # active+pending, excl AwaitingApproval
    pick (maxPerRepo - occ) best items   # priority-then-stale, deduped
    create Task per picked item
task_controller: admits up to MaxConcurrentTasks active total (rest gated)
agent runs Task -> proposal (AwaitingApproval) | merge | close
lane frees -> next scan tops it up
backlog empty -> requeue reverts to cron schedule
```

## Error handling

- Scan read error for a repo: skip that repo (unchanged behaviour), other repos
  still top up.
- Over-creation guard: `laneOccupancy` counting pending+active prevents creating
  a second Task for a repo whose first is merely gated by the global ceiling.
- Mass approval (many `AwaitingApproval` re-activate at once) can briefly exceed
  `maxPerRepo` active for a repo; total load stays bounded by `MaxConcurrentTasks`.
  Acceptable; no extra gate.

## Testing

- `selectCandidates`/top-up: table test - given candidates across 3 repos and
  existing Tasks giving repoA `laneOccupancy=1`, repoB=0, repoC=0 with
  `maxPerRepo=1`, the scan creates 0 for A, 1 for B (its priority-then-stale
  best), 1 for C.
- `laneOccupancy`: counts Planning/Running/pending; excludes
  AwaitingApproval/Succeeded/Failed.
- Backlog cadence: when un-Tasked items remain after a scan, `runScans` returns
  `<= backlogRequeue`; when none remain, returns the cron next-fire.
- Global ceiling unchanged: with `MaxConcurrentTasks=3` and 6 created Tasks, the
  existing `atConcurrencyCap` test still gates to 3 active (regression check).
- envtest reconcile: seed 6 repos x 2 open items, run a scan, assert one Task per
  repo created (not maxPerCycle=1 total), capped by laneOccupancy.

## Out of scope

- Improvement #2 (CI-fail-on-main -> auto-issue) and #3 (local-dev <-> external
  agent) - separate specs.
- Changing the agent/wrapper session model.
- Priority-ordered admission among globally-gated Tasks (the task controller's
  FIFO-ish gating is unchanged; priority is honoured at per-repo selection).
