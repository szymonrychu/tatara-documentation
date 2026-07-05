# Revive & harden the bot-PR merge/recovery pipeline

Date: 2026-06-15
Repos touched: `tatara-operator` (code), `tatara-helmfile` (deploy), live cluster (verify)
Status: approved design, pre-plan

## Problem

Five bot-authored PRs sat unmerged indefinitely: `operator#50/#41/#39` (CONFLICTING),
`wrapper#25` (MERGEABLE), `chat#31` (CONFLICTING). The autonomous system both let them
go AND never recovered them.

### Root cause (metrics-verified)

`internal/controller/projectscan.go` `laneOccupancy()` decides whether a Task still
occupies a repo's scan lane by switching on `Status.Phase`:

```go
switch t.Status.Phase {
case "Succeeded", "Failed", "AwaitingApproval": continue
}
n++ // everything else "occupies" the lane
```

`issueLifecycle`/`review` Tasks never set `Status.Phase` — they signal terminality via
`Status.LifecycleState` (Done/Parked/Stopped). So every terminal lifecycle Task counts
against its repo lane forever. With `cron.mrScan.maxPerRepo=1`, the first lifecycle Task
to go terminal saturates the lane permanently.

Live lane counts vs cap 1: operator=29, chat=10, wrapper=6, memory=5, cli=3.
Operator metrics: `mrScan scanned=25 skipped_dedup=0 skipped_cap=25 picked=0`.
`mrScan`/`issueScan` recovery is structurally dead system-wide. Bug is identical in
deployed `14d9ff5` AND `origin/main` -> needs a code fix, not just a deploy.

### Why they were let go (ingress was the only safety net, and it was off)

- Cross-repo orphaning: one agent run for operator#42 opened sibling PRs in
  operator+wrapper+chat; the driving Task tracks only its own repo PR (merged
  operator#52, went Done) -> wrapper#25/chat#31 orphaned at birth.
- `operator#50` = duplicate of already-merged #46 (supersedes #41); the duplicate-merged
  auto-close guard exists in `origin/main` but is UNDEPLOYED (live=14d9ff5).
- Then recovery (dead) never re-adopted any of them.

### Contributing
- `scan-n6w2l` hot-loops: a Conversation-state issueLifecycle Task re-sets the same
  `tatara-brainstorming` label every ~3s (164 GitHub label calls/h) and pins the wrapper lane.
- Deploy lag: live operator ~7 commits behind `origin/main`.

## Approach

**Recovery-as-safety-net** (chosen over per-PR tracking / hybrid). The `mrScan` scan IS
the designed backstop; the laneOccupancy bug silently disabled it. Fix the bug (don't add
a parallel mechanism, per KISS + hard rule 4). The lifecycle already drives every adopted
bot PR to a terminal outcome:
- green -> `handleMerge` merges (squash);
- conflict -> `handleMerge` 405 path -> `setLifecycleState("Implement","merge-conflict")`
  with a rebase instruction -> agent rebases -> re-merges;
- duplicate-of-merged -> guard closes + parks (origin/main).

## Design

### A. laneOccupancy correctness (core ingress fix)
`laneOccupancy` must free the lane for Tasks not really holding an agent slot. Add a
`LifecycleState` check alongside the phase check: treat Done/Stopped/Parked (terminal) and
Conversation (human-blocked, no pod) as NOT occupying the lane. Mirrors the real
"is an agent slot in use" semantics (cf. `taskOpen`/`isLifecycleTerminal`). Keep the
existing phase exclusions (Succeeded/Failed/AwaitingApproval).

Effect: operator/chat/wrapper lanes drop to ~0-2; `mrScan`/`issueScan` resume picking.

TDD: failing unit test — a Parked issueLifecycle Task (empty `Phase`) is currently counted;
assert it is NOT, post-fix. Add a Conversation case too.

### B. Egress retry-bound (anti-abandonment + anti-thrash)
Once recovery is live, a genuinely-stuck PR would be re-adopted and re-parked every cycle
(agent spawn/hour forever). Bound it: before `mrScan` creates an adoption Task for a bot
PR, read the PR's recovery-attempt count (persisted on the PR as a
`tatara/recovery-attempts` numeric label, robust to Task GC). If `>= maxRecoveryAttempts`
(default 3), close the PR with an escalation comment ("auto-recovery exhausted after N
attempts; needs human") instead of re-adopting. Otherwise increment on adoption.

3 is high enough that a normal rebase (1-2 attempts) still lands; only persistently-broken
PRs escalate. Does not fire on first-pass conflicts (chat#31 will rebase and land).

TDD: PR at attempt count N-1 -> adopts + increments; at N -> closes + comments, no Task.

### C. Hot-loop fix
Systematic-debug the `scan-n6w2l` Conversation re-reconcile (~3s cadence, repeated
`ensurePhaseLabel`). Likely a self-triggering status update or missing idempotency on the
label set. Fix to idempotent / no rapid self-requeue. TDD: reconcile a Conversation Task
twice; assert no redundant `AddLabel` call when the label is already present.

### D. Deploy (GitOps only — hard rule 15)
Merge fixes to `tatara-operator` main (CI builds+pushes image+chart). Then a
`tatara-helmfile` MR bumping BOTH the chart version AND the pinned `image.tag`
(per the operator-deploy memory). Reviewed via diff, applied by the pipeline. No
`kubectl set-image`/patch. Ships the laneOccupancy+hot-loop+bound fixes plus the
already-merged duplicate-merged guard (#50 auto-close) and #46 teardown.

### E. Verify auto-heal (live)
After the new image is live, watch the revived recovery:
- `wrapper#25` (green) -> merged;
- `chat#31` (conflict) -> rebased -> merged (lands the operator#42 metrics work, as agreed);
- `operator#50` (dup) -> auto-closed; `#41` (superseded) -> closed; `#39` -> rebased or closed.
Confirm via `mrScan picked>0`, lane counts down, PRs reaching terminal. Intervene only if
auto-heal stalls past a couple of cycles.

## Out of scope / noted
- `maxPerRepo` stays 1 (capacity tuning handled separately by human PR helmfile#10).
- Writeback-narration cosmetic bug (PR body said "no PR opens" with a real diff) — note only.

## Execution constraints
- Worktree off FRESH `origin/main` (operator local is 21 behind; external bots push here).
- TDD every code fix (failing test first). A/B/C are independent files -> parallel subagents
  (sonnet impl, opus merge per hard rule 7), then build/deploy from `main` only (hard rule 10).
- Another agent is active on this system; coordinate via fresh-main rebases, small focused PRs.

## Testing
- Unit: laneOccupancy (terminal-lifecycle + Conversation free the lane); recovery-bound
  (adopt-vs-close at threshold); hot-loop idempotency.
- Integration/live: post-deploy, the 5 stuck PRs reach terminal; `mrScan picked>0`.
