# Token-conservation P0 review: verdict + gaps for brainstorm

Date: 2026-07-04. Review of the token-efficiency agent's P0 work (4 parallel
review agents: operator diff, wrapper+observability diff, helmfile deploy
branch, design-level gap analysis) plus live Prometheus checks.

## Context

Fleet is SCALED TO 0 because of token burn. P0 is the gate for turning it
back on. Merge state (ROADMAP was stale):

- tatara-operator PR #218 (`c2f4986`): tiering fields + BuildPod resolution +
  maxTaskTokens backstop + implementPrompt double-append fix + brainstorm
  human-activity gate + TATARA_KIND/TATARA_REPO env - MERGED to main.
- tatara-claude-code-wrapper PR #73 (`3557fb5`): kind/repo/project labels on
  token+cost metrics - MERGED to main.
- tatara-observability PR #26 (`448a467`): $-spend + cache-hit-ratio panels -
  MERGED to main.
- tatara-helmfile branch `b96f25c`: dual-pin + tier maps + cadence + reaper +
  skillsRef pin + maxTaskTokens=3000000 - NOT merged, deploy owed.

## Verdict on the agent's work

Code is plan-faithful and clean: nothing the P0 plans required is missing,
the double-append fix is a root fix, tests are real (incl. regression tests),
the helmfile branch's strip-then-revert history was CORRECT handling of a real
CRD-ordering hazard (values would have shipped fields the deployed operator
CRD prunes; agent stripped, waited for operator merge, reverted + dual-pinned
so CRD and CRs land atomically; tatara-project `needs: [tatara-operator]`
makes in-apply ordering safe). Tier map matches the spec table exactly;
skillsRef pin correctly substituted merged-main HEAD for the plan's
stale-branch SHA.

The failure is one level up: P0's measurement component is built on a dead
instrument, and nobody root-caused it. The spec itself said
`ccw_turn_tokens_total` had zero live samples in 30 days; the work relabeled
the emit sites without diagnosing why the family was dark. Live check: ALL
`ccw_*` series absent for 14+ days (fleet at 0 explains the recent window,
but only 2 turn series exist in the whole 30d lookback even while the fleet
ran). Worse, `ccw_turn_cost_usd_total` has NO producer at all: the wrapper
only folds cost from `/workspace/result.json` `total_cost_usd`, which no
agent, prompt, or skill ever writes, and PTY-interactive claude never emits
result JSON. Post-deploy, the $-dashboards will render empty and the tiering
A/B has no signal. "Measure-first" shipped without verifying the instrument.

## Gaps (each = one brainstorm-session input)

### Blockers for re-enable

**G1. Dollar-cost pipeline has no producer; dark ccw family never root-caused.**
`ccw_turn_cost_usd_total` is only fed from a `/workspace/result.json` file
nothing writes (wrapper `cmd/cc-stop-hook/hook.go:43`, `session.go:983`);
there is no price table anywhere. `ccw_turn_tokens_total` was dark for 30d
pre-scale-down and the diff only relabels the emit path.
Direction: derive $ from `ccw_turn_tokens_total{model,type}` via a per-model
price table (incl. cache_read/cache_creation rates) - wrapper-side emit or
Prometheus recording rules; and diagnose the dark token family end-to-end
(stop-hook firing? push forwarding? scrape?) with an explicit smoke check in
the P0 acceptance gate.

**G2. Ephemeral push-metric delivery unfit for cumulative accounting.**
Wrapper metrics reach Prometheus via the operator push-receiver, which stamps
per-run `run_id`/`pod` labels and evicts series 5 min after last push
(PUSH_METRICS_TTL). The new "Cost by Kind/Repo" instant-sum panel therefore
shows "pods alive in last ~5min", not cumulative spend; per-run counters are
born non-zero so `rate()`/`increase()` lose the first jump entirely -
single-turn pods (triage/review, exactly the A/B'd kinds) contribute nothing
to the cache-hit-ratio panel (the P2 gate instrument) or the existing
token-runaway alert. run_id labels are also unbounded cardinality over time.
Direction: durable roll-ups - recording rules over per-run `max_over_time`,
or operator-side durable counters aggregated at turn-complete (the
`operator_task_tokens_total` path already proves this works); zero-init label
children at wrapper boot.

**G3. Safe fleet re-enable runbook.**
Everything is off; the spec's "A/B via dashboards after rollout" assumes a
running fleet, and the rollout itself changes 6 knobs at once. Direction:
staged resume - deploy helmfile branch, G1 minimal fix first (else blind),
one project only, stretched cadence, maxTaskTokens active, brainstorm
disabled initially; watch $/day panels; widen stepwise. Define abort
thresholds up front.

### Tiering correctness / safety

**G4. Quality proxies named in acceptance are not instrumented.**
Spec acceptance cites proposal accept-rate, review find-rate, implement CI
pass-rate; no metric family exists for any of them (operator metrics have
only open-proposals gauge + issue-outcome + task-terminal). The
Sonnet-for-review downgrade - the spec's own "riskiest" call - is
unvalidatable. Direction: outcome metrics (review verdict + finding count,
implement-MR CI result, proposal label-transition counters) as component-6b.

**G5. No closed loop from regression to revert.**
Tiering is "trivially reversible per kind" but detection is human-eyeball.
Platform already has alert->incident->Task machinery. Direction: alert rules
on quality-proxy deltas per kind -> incident/selfImprove Task that proposes
the one-line tier-revert MR. Makes tiering self-tuning.

**G6. Token budget semantics: output-only, per-Task, model-blind.**
`maxTaskTokens` gates cumulative OUTPUT tokens only (turncallback.go:276),
while the diagnosed burn is INPUT re-billing (axis 2) - a replay/reprompt
loop with huge cold input and tiny output never trips it. Budget resets on
reroll (Parked implement-failed -> adopt/reroll < 3), so an issue can burn
~3x the cap; 3M Sonnet tokens != 3M Opus dollars. Direction: budget in $ (or
input+output+cache) accumulated per issue lineage, not per Task CR; wire
from the same usage callback.

**G7. Tier-map CRD fields accept garbage silently.**
`modelByKind`/`effortByKind` are bare `additionalProperties: {type: string}`.
Typo'd kind key (`triage-issue`) silently no-ops -> kind quietly stays
Opus/high, saving lost with no signal; typo'd model/effort VALUE admits fine
and reaches `claude --model <garbage>` -> BootCrashLoop -> every task of that
kind Failed. Direction: CRD CEL/XValidation on keys (kind enum) + values
(model-ID pattern, effort enum), plus BuildPod-time warn metric for unknown
keys.

**G8. healthCheck tier-inseparable from brainstorm - false constraint.**
Spec accepts healthCheck inheriting Opus+high because it shares
Kind=brainstorm, but pod.go already branches on the
`LabelActivity=healthCheck` label. healthCheck is recurring classification
work, prime Sonnet candidate. Direction: key model/effort resolution on
kind+activity (pseudo-key `healthCheck`).

**G9. Sonnet review x push-CD auto-merge stack risk in the same window.**
Semver push-CD auto-merges bot PRs on green CI trusting declared
significance; review is not a required check; mrScan (review backstop)
stretches to 2h; review simultaneously drops to Sonnet. A bot PR can merge +
deploy before any review pod spawns, reviewed by the downgraded model when
it does. Direction: sequence deliberately - make agent review a required
check for bot auto-merge, or hold the review downgrade until push-CD's
significance-verification follow-up exists.

### Missed spend levers

**G10. Brainstorm idle spend untouched - largest recurring Opus burn left.**
Brainstorm stays hourly Opus+high per project; a no-op cycle (proposal cap
already hit, no new corpus) still boots a pod and pays the full ~25k-token
cold prefix. Direction: stretch brainstorm cadence + operator-side pre-spawn
no-op check (maxOpenProposals reached / no delta since last cycle -> skip pod
creation entirely). Pod-less skip = 100% saving on idle cycles.

**G11. No prefix-size reduction lever; axis 2 rides entirely on gated P2.**
Nothing slims the ~25k-token prefix (tool schemas 5.3k + skill descriptions
~20k); P2's 4b superset skill install would INCREASE per-pod input for light
kinds exactly when the cache measurement fails. Also P0's cadence stretch
(2-4h) breaks P2 4c's keep-warm premise (max extended TTL 1h cannot bridge
cycles) - component 5 sabotages component 4c as designed. Direction: add
turn-0 input-composition measurement per kind to component 6; treat
skill-description/tool-schema slimming as an UN-gated axis-2 fallback;
re-scope 4c to intra-cycle burst coalescing only.

**G12. Cost attribution stops at kind/repo - churn cost invisible.**
The spec's own motivating pathology (24x Task recreation on one issue) is
not computable post-P0: no task/issue dimension on cost, no
$/issue-delivered vs $/issue-churned. Direction: issue (or task lineage) on
the operator-side durable cost ledger (pairs with G2/G6); churn dashboard =
cost by issueRef x terminal outcome.

### P1/P2 pre-work (do before planning, not during)

**G13. Handoff-replaces-S3 assumes a crashing pod can write its handoff.**
OOMKill/node-loss writes nothing; successor boots blind once S3 replay is
removed. "WIP lives in git" only covers implement-like kinds -
brainstorm/refine/incident have no committed state. Direction: wrapper
write-behind - checkpoint the handoff after EVERY turn, not at death; keep a
truncated-transcript last resort for non-git kinds. Must resolve before P1
plan.

**G14. P1 savings sized on an unverified subagent-settings key.**
Whether claude's settings.json can force subagent model/effort is deferred
to implementation; if absent, the fallback (prompt directive) is
unenforceable and P1's expected cut is speculative. Direction: 1-hour spike
now; if no key exists, design wrapper-side enforcement before P1 planning.

**G15. Fail-closed `resolveProfile` hostage to gated P2.**
The fail-open full-tool-surface drift is a known hole; its fix is bundled
into 4a, gated on a cache measurement that may say no. One-line security fix,
independent of caching, and profile gating is the ONLY authz boundary (all
agents share one OIDC identity). Direction: un-gate, ship in P0/P1.

### CD/ops hygiene

**G16. Tier values + skillsRef pin vs semver push-CD automation.**
`cd-release bump` pattern-rewrites the same `values/project-*/common.yaml`
files that now carry tier maps (agent block) - a human tier-revert MR races
the deploy-train branch; skillsRef SHA pin creates a manual bump obligation
on every tatara-agent-skills merge (silent skills staleness). Direction:
anchor pin-rewrite patterns to exact image keys with a test fixture
containing tier maps; wire the skills repo into the push-CD cascade.

**G17. P0 loose ends (single cleanup issue, not a brainstorm).**
Alert re-baseline (spec comp-6 item for observability) unshipped - 80k tok/s
runaway threshold still assumes all-opus-xhigh; `check_token_panels.sh` is
tautological and not wired into CI; brainstorm gate ignores
reactions/issue-edits as human engagement (proposal starves till reaper);
gate doubles ListIssueComments calls per proposal per cycle (add per-cycle
memo); pod.go:436 comment names healthCheck as a Kind; wrapper metrics carry
`kind=""` for pre-P0-operator windows (dashboards can't distinguish "old
operator" from empty).

## Suggested sequencing

1. G1 minimal ($ from token counts via recording rules - no image rebuild
   needed if done query-side) + merge helmfile `b96f25c` + G3 staged
   re-enable.
2. G2/G4/G12 as component-6b (real measurement); G7 CRD validation; G15
   un-gate fail-closed.
3. G5/G6/G8/G9/G10 as individual brainstorms once telemetry flows.
4. G13/G14 before P1 planning; G11 re-scopes P2; G16 lands with push-CD.
