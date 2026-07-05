# Token-conservation fleet re-enable runbook (G3)

Date: 2026-07-04. Owner: platform. Gate: review gap G3.

The agent fleet was scaled to 0 to stop token burn. P0 tiering + adjacents,
the G1/G2/G12 durable measurement, and the G7/G15/G17 hardening are all
deployed. This runbook brings the fleet back **staged**, not all-at-once
(the review's G3 warning), establishing a $/day baseline we do not yet have
before widening.

## Levers

| Lever | Where | Effect |
|---|---|---|
| Master on/off | `kubectl scale deploy/tatara-operator -n tatara --replicas=N` | operator controller up/down; currently **0** (live-scaled, not in git) |
| Operator replicas (git) | `values/tatara-operator/default.yaml` `replicaCount` (the `{env}.yaml`, highest-precedence layer - NOT `common.yaml`) | a helmfile apply reconciles the operator Deployment to this (3). Immaterial to Claude burn (controllers do not call Claude; leaderElection runs 1 active); leave at 3 and pause via the live scale above |
| Per-project pause | `values/project-<p>/common.yaml` `project.spec.maxConcurrentTasks` (5) | agents run at once per project; **0 FULLY pauses the project** (operator 2822968+ gates QueuedEvent admission on 0). Before 2822968, 0 floored to 3 and lifecycle/scan/triage tasks leaked through the per-repo seq-lane - the G3-blocking pathology. Unpause (0->N) resumes on the next scan-cron enqueue, not instantly |
| Brainstorm | `...cron.brainstorm.enabled` (true) + `schedule` (`0 * * * *`) | the largest idle Opus spend (G10); off in Stage 1 |
| Scan cadence | `...cron.issueScan.schedule` (`0 */4 * * *`), `mrScan` (`0 */2 * * *`) | already stretched |

Config changes go through a **tatara-helmfile MR + GitOps apply**. The master
scale is a live `kubectl scale` (matches the current paused state).

## Instruments (now durable + accurate)

- $-cost by kind/repo: `operator_task_tokens_total{model,type}` x per-model
  price (Grafana task-delivery dashboard). Sonnet-priced kinds
  (triageIssue/review) should read cheaper than Opus kinds - the tiering A/B.
- Cache-hit-ratio by kind (functional now that cache_read/input are separate).
- Churn: `operator_task_terminal_tokens_total{outcome}` -> $/delivered vs
  $/churned (issueLifecycle-scoped; single-shot churn not yet captured - G4/G8).
- Runaway alert: re-baselined to the model-aware $ rate.

Caveat: series populate only once tasks run. Empty panels at Stage 0 are
expected; they fill during Stage 1.

## Prerequisites / caveats before re-enabling

- **G9 (safety)**: if semver push-CD auto-merge is active, agent review is not
  a required check and mrScan is stretched to 2h - a bot PR could merge+deploy
  before a review pod spawns, reviewed by the downgraded Sonnet model when it
  does. Before relying on Sonnet review in a push-CD window, make agent review
  a required check OR hold the review downgrade. If push-CD is NOT live, N/A.
- **G4 (blind spot)**: review find-rate / implement CI-pass-rate / proposal
  accept-rate are not yet instrumented, so the Sonnet-review downgrade quality
  is watched manually via PR CI outcomes during Stage 1, not a metric.
- **Pause lever (fixed in operator 2822968)**: the first Stage-1 attempt found
  `maxConcurrentTasks=0` did NOT hold a project - `QueueCapacity()` floored to 3
  at 0, so issueLifecycle/scan/triage tasks kept leaking through the per-repo
  QueuedEvent seq-lane and "hold infrastructure at 0" silently burned ~63% of the
  window. Fixed by gating `admit()` on `maxConcurrentTasks==0`. Stage 1 now
  genuinely observes tatara alone with infrastructure held.

## Stage 0 - verify paused baseline

```
kubectl get deploy tatara-operator -n tatara -o custom-columns=IMAGE:'.spec.template.spec.containers[0].image',DESIRED:.spec.replicas
# expect: tatara-operator:2822968  0  (pre-Stage-1 paused; 2822968 carries the pause fix + G8)
```
Confirm the Grafana token-conservation dashboards load (empty is fine).

## Stage 1 - conservative single-project observation

Goal: establish the $/day baseline + confirm tiering and the durable metrics
behave on real turns, at minimal blast radius.

Config (one tatara-helmfile MR, then apply):
- Pin operator + both `tatara-project` releases to the new operator SHA
  (`helmfile.yaml.gotmpl` x3 + `values/tatara-operator/common.yaml` image.tag).
- `values/project-tatara/common.yaml`: `project.spec.maxConcurrentTasks: 2`;
  `...cron.brainstorm.enabled: false`.
- `values/project-infrastructure/common.yaml`:
  `project.spec.maxConcurrentTasks: 0` (held silent + validates the full-pause
  fix live).

replicaCount stays 3 (operator pods are not the Claude spend; leaderElection runs
1 active regardless). The apply reconciles the operator up on the new image; if it
lands with the operator still live-scaled to 0, `kubectl scale
deploy/tatara-operator -n tatara --replicas=3`.

Observe for a window (>= a full issueScan/mrScan cycle, ideally ~1 day):
- **Tiering works**: a spawned review or triageIssue pod has
  `MODEL=claude-sonnet-5`; an implement/incident pod has `claude-opus-4-8`
  (`kubectl get pod <p> -o jsonpath` env), and the $ panel shows the
  Sonnet-priced kinds cheaper.
- **Metrics populate**: `operator_task_tokens_total` shows all four `type`
  values with a real `model` label; cache-hit-ratio is non-zero; a completed
  lifecycle task adds to `operator_task_terminal_tokens_total{outcome}`.
- **Health**: no BootCrashLoop, no refusal/error flood in operator logs, no
  runaway alert.
- **Record the baseline**: $/hour and $/day at maxConcurrentTasks=2, per kind.

Success -> proceed to Stage 2 and set thresholds from this baseline.

### Stage 1 - first run results (2026-07-04, operator 2822968)

Ran ~14:48-14:55 UTC (~7 min, early snapshot), then operator re-scaled to 0.

- **Pause fix PASS (live)**: `infrastructure` (maxCT=0) created zero new Tasks /
  pods / tokens; `tatara` (maxCT=2) admitted 6 tasks at the same startup
  reconcile. The hold is proven by outcome + the project asymmetry - the skip is
  NOT emitted as a metric/log at INFO (infra queue was empty so `admit()` was
  never reached). Follow-up: add
  `operator_task_admission_skipped_total{project,reason="paused"}` so the pause is
  provable in metrics, not inferred.
- **Baseline ~$0.70/hr -> ~$16-17/day FLOOR** at this config: near-all
  `issueLifecycle`/opus, 99.99% cache-hit. This is the floor (brainstorm off,
  infra held, no implement/review ran); steady state is higher.
- **Tiering**: opus side confirmed (issueLifecycle/incident -> claude-opus-4-8);
  the Sonnet side (review/triageIssue) did NOT organically run - coded + envtest-
  proven but unexercised live. Watch for it at Stage 2.
- **Durable metrics**: model + 4 cache types live; cache-hit-ratio works;
  `operator_task_terminal_tokens_total` had only `outcome="abandoned"` (the reaped
  stragglers) - `delivered`/`churned` populate once a task ships/churns a PR.
- **Operator health / runaway alert**: 3/3, 0 restarts; alert normal (~2500x under
  threshold); coverage-gap meta-alert healthy now the model/cache series exist.
- **maxTaskTokens IS active** (the measurement subagent's "inert" finding was a
  misread, corrected here): the `maxTaskTokens=3000000` per-Task cumulative-output
  backstop enforces in `task_controller.go` (:1016/:1125; a zero value disables
  it, 3M is set) and is NOT gated by any flag. The subagent conflated it with the
  SEPARATE, richer `tokenBudgetEnabled` per-window budget feature (proactive/
  emergency percent, reset windows, `claudeSubscription` mode) - that one is
  `TOKEN_BUDGET_ENABLED="false"`, off by design. So the runbook's maxTaskTokens
  assumption HOLDS. Turning ON the per-window tokenBudget is a G6 design decision
  (budget in $/input-aware, per issue-lineage), not a safety blocker.
- Minor: `tool surface probe unhealthy` (tatara-chat backend unreachable from the
  operator) every reconcile - agents still run; worth a look.

## Stage 2 - widen stepwise (data-driven)

Set abort thresholds from Stage 1 first, e.g.: $/hr > 3x the Stage-1 per-lane
rate scaled to the new concurrency; refusal-rate > a few %; any BootCrashLoop;
churned:$ share climbing. Each step is one helmfile MR + apply, observed before
the next:

- **2a**: `project-tatara maxConcurrentTasks 2 -> 5`; `replicaCount 1 -> 3`.
  Watch throughput + $/day vs threshold.
- **2b**: `project-tatara cron.brainstorm.enabled true` (the biggest idle Opus
  spend - watch idle-cycle cost; if a no-op cycle still pays the full cold
  prefix, that is G10, note it).
- **2c**: `project-infrastructure maxConcurrentTasks 0 -> 5`.
- **Final**: remove the temporary `replicaCount` override (back to the git
  default 3); steady state = both projects, brainstorm on, full concurrency.

## Abort (any stage)

Immediate: `kubectl scale deploy/tatara-operator -n tatara --replicas=0`
(stops new spawns; in-flight agent pods drain/fail their next callback). Then
revert the last helmfile values change (MR) so git matches. Diagnose from the
$-by-kind + churn panels + operator logs before retrying the step.

## Done criteria

Fleet steady at both projects / brainstorm on / full concurrency, $/day within
the accepted budget, tiering confirmed cheaper for Sonnet kinds vs the pre-P0
all-Opus-xhigh baseline, no runaway. Then the remaining refinement gaps
(G4 quality proxies, G5 self-tuning revert, G6 $-budget, G8 healthCheck tier,
G10 brainstorm idle skip, G11 prefix slimming, G13 handoff, G14 subagent spike,
G16 cd-race) are prioritized against the real telemetry this re-enable produces.

## Related

`docs/2026-07-04-token-conservation-p0-review-gaps.md` (G3 source),
`docs/superpowers/specs/2026-07-04-durable-measurement-design.md` (the
instrument), [[tatara-token-conservation-2026-07-04]].
