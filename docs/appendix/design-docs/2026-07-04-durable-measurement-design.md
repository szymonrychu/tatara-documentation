# Durable token/cost measurement (G2 + G12)

Date: 2026-07-04
Status: design (approved, pre-plan)
Scope: tatara-operator, tatara-observability (+ tatara-claude-code-wrapper only
if the turn-complete callback must forward cache_creation)
Origin: token-conservation P0 review gaps G2 + G12
(`docs/2026-07-04-token-conservation-p0-review-gaps.md`). G4 (quality proxies)
is a separate follow-on spec.

## Problem

P0 shipped "measure-first" on an instrument that does not durably measure:

- **Dead $ producer (G1)**: `ccw_turn_cost_usd_total` was fed only from a
  `/workspace/result.json` PTY-interactive claude never writes. The P0
  G1-minimal fix re-derived $ query-side from `operator_task_tokens_total`, but
  that metric folds `cache_read` into `input` and has no `model` label, so the
  $ is a crude approximation (cache priced as input; model inferred from a
  kind->model mapping coupled to the tier map).
- **Ephemeral push delivery (G2)**: the wrapper `ccw_*` family reaches
  Prometheus via the operator push-receiver with a ~5-min TTL and per-run
  `run_id`/`pod` labels. Instant-sum panels show "pods alive in the last ~5min",
  not cumulative; per-run counters are born non-zero so `rate()`/`increase()`
  lose the first jump; single-turn pods (triageIssue/review - exactly the
  tiering-A/B'd kinds) contribute nothing to the cache-hit-ratio panel (the P2
  gate instrument) or the runaway alert; `run_id` is unbounded cardinality.
- **No churn attribution (G12)**: the motivating pathology (24x Task recreation
  on one issue) is not computable - no cost dimension distinguishes
  $/issue-delivered from $/issue-churned.

The durable, proven path already exists: the operator receives real per-turn
usage at the turn-complete callback (`recordUsage`, turncallback.go) and runs
as a long-lived process, so its `operator_task_tokens_total` counters
accumulate correctly, zero-init at operator boot, carry no `run_id`, and never
evict on a TTL. This design makes that operator-side family the model- and
cache-aware source of truth and derives $ from it.

## Goals

- A durable token metric with per-model and separated cache dimensions, so $
  and cache-hit-ratio are computable and accurate for every kind (including
  single-turn triageIssue/review).
- $ derived from a per-model price table living in Prometheus (updatable
  without an operator deploy - the Sonnet-5 intro price $2/$10 expires
  2026-08-31 -> $3/$15).
- Bounded per-outcome churn accounting so $/delivered vs $/churned is a panel.
- The re-enable dashboards ($-cost, cache-hit-ratio, churn) and the runaway
  alert read from this durable source, not the dead/ephemeral `ccw_*` push.

## Non-goals

- G4 quality proxies (review find-rate, implement CI pass-rate, proposal
  accept-rate) - separate spec; different lifecycle sources.
- Removing the wrapper `ccw_*` push emission outright. It is deprecated FOR
  ACCOUNTING (dashboards/alerts stop reading it); the emission may stay for
  per-turn live debug or be removed in a later cleanup. Not in scope here.
- Per-issue-label durable cost (rejected for cardinality; churn is
  outcome-keyed, not issue-keyed).

## Design decisions (from brainstorm)

1. Operator emits durable TOKENS; Prometheus prices them. Prices live in a
   per-model price table on the Prometheus side, not in operator Go.
2. Cache dimensions are separated: `type in {input, output, cache_read,
   cache_creation}` - `input` is uncached input only (stop folding cache_read).
3. `model` label added, resolved at turn-complete from the tier resolution.
4. Churn is a bounded metric keyed by terminal `outcome`, not by `issue`.

## Design

### Component 1: Enriched durable token metric (operator)

`operator/internal/obs/operator_metrics.go`.

- `operator_task_tokens_total` (currently labels `{project, repo, kind, issue,
  type}`, def ~:261-264) gains a `model` label ->
  `{project, repo, kind, issue, model, type}`.
- `type` values become `input` | `output` | `cache_read` | `cache_creation`
  (currently only `input` | `output`, with `input` = InputTokens +
  CacheReadInputTokens folded, per `recordUsage`).
- `AddTaskTokens` (currently `(project, repo, kind, issue string, input,
  output int64)`, ~:820) becomes
  `AddTaskTokens(project, repo, kind, issue, model string, input, output,
  cacheRead, cacheCreation int64)`, emitting each non-zero token class as its
  own `type` series.
- `DeleteTaskSeries` (~:853) extends to delete all four `type` series for the
  (project,repo,kind,issue,model) tuple on task completion - the `issue` label
  stays bounded to live issues.

`operator/internal/controller/turncallback.go` `recordUsage` (~:247-293):
- Stop folding: `input` = `u.InputTokens` (uncached); emit `u.CacheReadInputTokens`
  as `cache_read` and `u.CacheCreationInputTokens` as `cache_creation`
  separately.
- Resolve the task's `model`: stamp the resolved model on `Task.Status`
  (new `Status.ResolvedModel`) at pod spawn (where `BuildPod` already calls
  `modelForKind`), and have `recordUsage` read `Status.ResolvedModel`. This is
  the model that ACTUALLY ran the turn (accurate even if the tier map changed
  after spawn) and avoids re-resolving from the Project at callback time.
- Pass model + four token classes to `AddTaskTokens` (still gated on
  `recorded`, so no double-count).
- `taskTokenLabels` (~:435) extends to also return the resolved model.

Dependency to verify at plan-time: the turn-complete callback `usage` payload
must carry `cache_creation_input_tokens`. `recordUsage` already reads
`u.CacheReadInputTokens`, so the `turnUsage` struct + the wrapper->operator
callback JSON likely carry cache fields; confirm `cache_creation` is present.
If absent, add it to the wrapper's callback usage payload (a small wrapper
change) - the wrapper already parses `cache_creation_input_tokens`
(transcript/result.go).

### Component 2: Bounded churn metric (operator) - G12

`operator/internal/obs` + the task-terminal path.

- New `operator_task_terminal_tokens_total{project, repo, outcome, model,
  type}` - NO `issue` label. `outcome in {delivered, churned, abandoned}`.
- At each task-terminal transition (the `setLifecycleState`/task_controller
  terminal chokepoint), add that task's cumulative per-class tokens to this
  metric under the task's classified `outcome`.
- Outcome classification (operator already has the inputs): `delivered` =
  terminal success (PR merged / issue resolved by the work, from the WorkItems
  ledger + terminal phase); `churned` = recreated / gave-up-and-rerolled
  (Task.Status.ImplementGiveUps, dedup-recreation, dup-closed); `abandoned` =
  declined/parked-terminal with no delivery. A single mapping function
  `terminalOutcome(task) string` with a table-test over the terminal
  phases/reasons.
- Cumulative per-class tokens for the task: extend the operator's per-task
  cumulative tracking (currently `Task.Status.CumulativeTokens += output` only,
  in `recordUsage`) to accumulate all four classes on `Task.Status`
  (`CumulativeInput`/`CumulativeOutput`/`CumulativeCacheRead`/
  `CumulativeCacheCreation`, keeping `CumulativeTokens` as the existing
  input+output total for back-compat if consumed elsewhere). At terminal, add
  these Status values to `operator_task_terminal_tokens_total{outcome, model,
  type}`. Each of the 24x recreated tasks contributes its own cumulative at its
  own terminal, so the `{outcome=churned}` bucket sums the whole churn without
  per-issue-lineage tracking.

### Component 3: Dollars, dashboards, alerts (observability)

`tatara-observability`.

- A per-model price table (opus-4-8 $5/$25, sonnet-5 $3/$15 [note intro
  $2/$10 through 2026-08-31], haiku-4-5 $1/$5 per 1M in/out; cache_read = 0.1x
  input, cache_creation = 1.25x input) applied to `operator_task_tokens_total`
  by `model`+`type`, summed for the $ series. Because `model` is now a real
  label, the derivation is model-accurate (no kind->model coupling) and
  cache-accurate (cache_read/cache_creation priced correctly).
- Cache-hit-ratio panel: `cache_read / (cache_read + input)` by kind - now
  functional because the two are separate `type` series.
- Churn panel: $/delivered vs $/churned from
  `operator_task_terminal_tokens_total` x the same price table.
- Runaway alert re-baselined from the flat all-opus-xhigh threshold to the
  model-aware $ rate (folds in review G17's alert-re-baseline loose-end).
- Replace the G1-minimal crude $ panel expr with the model+cache-accurate one.

Open item for plan-time (same as G1-minimal hit): tatara-observability
provisions Grafana (dashboards + grafana-managed alerts), not a Prometheus
ruler. Determine whether the cluster has a Prometheus/Mimir ruler (likely in
the infra repo) where the price table + $ derivation should live as recording
rules; if not, the $/price arithmetic stays in the dashboard/alert PromQL
expressions (as G1-minimal did), now keyed on the real `model` label. The plan
resolves this by inspecting the infra Prometheus config; the metric design
above is unaffected either way.

### Component 4: Deprecate ccw_* for accounting

Dashboards/alerts stop reading the wrapper `ccw_turn_tokens_total`/
`ccw_turn_cost_usd_total` push family (already largely true post-G1). The
wrapper push emission stays for now (per-turn debug); a later cleanup may
remove it and the push-receiver `run_id` cardinality. No wrapper change here
unless cache_creation forwarding (Component 1) is needed.

## Cross-repo change list

| Repo | Change |
|---|---|
| tatara-operator | model label + 4 cache/token `type` values on operator_task_tokens_total; AddTaskTokens/DeleteTaskSeries signatures; recordUsage unfold + model resolve + cache split; new operator_task_terminal_tokens_total{outcome} + terminalOutcome() + all-class cumulative; unit + envtest |
| tatara-observability | model+cache-accurate $ (recording rules or PromQL) + working cache-hit-ratio + churn panels + alert re-baseline; drop ccw_* reads |
| tatara-claude-code-wrapper | ONLY if the callback usage lacks cache_creation_input_tokens: forward it in the turn-complete payload |

## Testing

- operator unit: AddTaskTokens emits four `type` series with the model label,
  skips zeros; terminalOutcome() table-test over terminal phases/reasons.
- operator envtest: a turn-complete callback with input/output/cache_read/
  cache_creation emits all four `operator_task_tokens_total{model,type}` series
  with the right values (extends the P0 TestTurnComplete_EmitsTaskTokens; reuse
  the shared-namespace unique-turn-id lesson from that test); a task reaching
  each terminal outcome adds to `operator_task_terminal_tokens_total{outcome}`.
- observability: dashboard JSON valid + panel-guard asserts the new exprs;
  terraform validate; recording-rule promtool check if a ruler is used.

## Acceptance criteria

- `operator_task_tokens_total` carries `model` and four separated `type` values
  live; cache-hit-ratio panel shows non-zero for single-turn kinds.
- The $-cost panel is model-accurate (a Sonnet review pod and an Opus implement
  pod price differently and correctly) and cache-accurate.
- `operator_task_terminal_tokens_total{outcome}` accumulates; a churned issue
  (recreated task) lands in `{outcome=churned}`.
- Dashboards/alerts read the operator-side durable source, not ccw_*.

## Deploy

Same chain as the gap batches (operator main -> image, observability terraform;
wrapper only if cache_creation forwarding). Fleet validated at 3 then scaled to
0 where a live check is cheap (e.g. confirm the four `type` series emit on a
single turn); dashboards confirmed against real series before permanent
re-enable.

## Related

[[tatara-token-conservation-2026-07-04]] (P0 + gaps),
`docs/2026-07-04-token-conservation-p0-review-gaps.md` (G2/G12/G4 source),
the P0 spec `docs/superpowers/specs/2026-07-04-token-conservation-design.md`
(component 6 this supersedes). G4 quality-proxy metrics: separate follow-on.
