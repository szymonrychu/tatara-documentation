# Quality-feedback loop (G4 + G5)

Date: 2026-07-04
Status: design (approved, pre-plan)
Scope: tatara-operator, tatara-observability
Origin: token-conservation P0 review gaps G4 + G5
(`docs/2026-07-04-token-conservation-p0-review-gaps.md`). G6 ($-budget) is a
separate follow-on spec.

## Problem

P0 downgraded `review` and `triageIssue` to `claude-sonnet-5` (from
`claude-opus-4-8`) to cut cost - the spec's own "riskiest" call. Nothing
measures whether that downgrade hurt quality. The acceptance criteria cite
review find-rate / implement CI-pass-rate / proposal accept-rate, but no metric
family exists for any of them (the operator has only token/turn counters, a
terminal-outcome counter, an open-proposals gauge, and `tatara_issue_state`).
The Stage-1 re-enable measurement confirmed the gap: a Sonnet review pod's
quality is unobservable, watched only by human eyeball. And even if a regression
were spotted, the response is manual - despite the platform already having an
alert -> incident -> Task machinery that could react.

This spec instruments the downgrade (G4) and closes the loop so a detected
regression auto-proposes a tier-revert MR for human approval (G5), making the
Sonnet tiering safe to widen.

## Goals

- Model-keyed quality-proxy metrics that make a Sonnet-review regression visible:
  review find-rate (rubber-stamping) and downstream implement CI-pass-rate.
- Operator-side signal capture only - no wrapper change; G4 is single-repo.
- A self-tuning loop: a quality-regression alert auto-proposes a tier-revert MR
  against tatara-helmfile, awaiting human approval (agents never self-merge).
- Dashboards + alert rules in tatara-observability read the new metrics.

## Non-goals

- G6 $-budget redesign (separate spec). The `tokenBudget` per-window feature and
  `maxTaskTokens` cap are out of scope here.
- Proposal accept-rate proxy: brainstorm is opus (not downgraded), low relevance
  to the Sonnet A/B until brainstorm is tiered. Deferred.
- Auto-widen-back (re-applying a downgrade after recovery): risks flapping MRs;
  the loop reverts only, re-widening stays a human decision.
- The `selfImprove` Kind: it has no production creation path (tests only). G5
  rides the existing `incident` path instead of building selfImprove plumbing.
- Downstream review->PR-outcome correlation (a Sonnet-approved PR later
  reverted): deferred as a v2 signal; v1 relies on find-rate + a broad CI-pass
  metric.

## Design decisions (from brainstorm)

1. Scope: G4 + G5 as ONE spec (the full quality-feedback loop). G6 separate.
2. Proxies: review find-rate + implement CI-pass-rate, both model-keyed. Skip
   proposal accept-rate.
3. Signal source: the operator records the verdict at its own write-back (where
   it already posts Approve/RequestChanges); no SCM read-back, no wrapper change
   - G4 stays operator-only.
4. Revert autonomy: a regression auto-proposes a tier-revert MR (human approves
   at merge). No flag-only, no auto-widen-back.

## The loop

```
review Task runs -> posts verdict to SCM
  -> operator reads verdict+findings at Task-terminal -> G4 metric
    -> tatara-observability alert rule watches the downgraded kinds
      -> regression fires -> Grafana webhook (existing project contact point)
        -> operator handleGrafanaAlert recognizes a tier-quality alert
          -> incident Task scoped to tatara-helmfile, goal = revert this
             kind's tier -> MR opened -> human approves -> tier reverted
```

## G4 - quality proxies (operator)

`internal/obs/operator_metrics.go` - three new families, model-keyed:

- `operator_review_outcome_total{project, repo, model, verdict}` -
  `verdict in {approved, changes_requested}`. Incremented at review-Task
  terminal. Find-rate = `changes_requested / (approved + changes_requested)` per
  model. A Sonnet find-rate collapsing toward 100%-approved is rubber-stamping.
- `operator_review_findings_total{project, repo, model}` - counter summing the
  bot's review-comment count per review. With the outcome count -> average
  findings per review by model.
- `operator_implement_ci_total{project, repo, model, result}` -
  `result in {pass, fail}`, incremented when an implement Task's PR reaches a
  terminal CI conclusion. Broad health baseline (implement is opus). Requires
  PR -> producing-Task -> model correlation; if that proves fiddly, degrade to
  per-repo model-blind (`{project, repo, result}`) and note it.

### Review-verdict capture (operator, at write-back)

The operator posts the review verdict itself - `internal/controller/writeback.go`
calls `writer.Approve(...)` (verdict = approved) or `writer.RequestChanges(...)`
(verdict = changes_requested) when it writes back a review Task's result
(:1074/:1078). Record the metric AT that branch - there is no need to read the
posted review back, and the SCM client has no review-READ methods anyway (only
`Approve`/`RequestChanges`/`Suggest` writes; `GetPRState` is CI-only). At the
Approve / RequestChanges branch, increment
`operator_review_outcome_total{verdict}` with the verdict implied by the branch,
and `operator_review_findings_total` by the review's finding count (the count of
review suggestions/comments in the write-back object). Tag both with
`taskTokenLabels(task)` (project/repo/model) - `model` is `Status.ResolvedModel`,
the same source the token metrics already use (confirmed
`taskTokenLabels:452`), so attribution is consistent and reflects the model that
actually ran.

This is strictly operator-side, needs no new SCM method, and is more reliable
than a read-back (no timing/parse dependency). The write-back path is the single
point every review verdict passes through.

### Implement CI capture (operator)

When the operator observes an implement Task's PR reach a terminal CI conclusion
(via the existing mrScan / PR-check webhook path), record
`operator_implement_ci_total{result}`. Correlate the PR to its producing
implement Task (via the issue ref the PR closes -> the Task for that issue) to
attach `model` + `kind`. If the correlation is unavailable at observation time,
record model-blind by repo and leave the model attribution to a v2.

## G5 - self-tuning revert

### Alert rules (tatara-observability)

New Grafana-managed alert rules on the G4 metrics, scoped to the downgraded
kinds, e.g.:
- `review` approved-rate for `model="claude-sonnet-5"` == 100% over the last N
  reviews (rubber-stamping), or find-rate below a threshold.
- (optional, once CI attribution lands) implement CI-pass-rate drop.

Each rule carries labels identifying the regressed `kind` + `model` +
`project`, and routes to the project's existing Grafana webhook contact point
(the same one incident response uses). Thresholds are set from the G4 baseline
(see build order) - initial values are rough and tuned once data exists.

Rules carry the standard `homelab` + `system=tatara` labels the tatara alert
pipeline requires, plus a `tatara_tier_quality="true"` marker label the operator
keys on.

### Operator - alert to tier-revert incident

`internal/webhook/server.go` `handleGrafanaAlert`: when an incoming alert
carries `tatara_tier_quality="true"`, instead of (or in addition to) the generic
incident goal, template the incident goal to a tier-revert:

> "Quality regression detected for kind `<kind>` on model `<model>` in project
> `<project>`. Propose reverting `agent.modelByKind[<kind>]` to
> `claude-opus-4-8` and raising `agent.effortByKind[<kind>]` in the
> tatara-helmfile tier maps (`values/project-<project>/common.yaml`). Open one
> MR; do not merge."

The `kind`/`model`/`project` come from the alert labels. The incident Task is
scoped to `tatara-helmfile` (enrolled in the project's Repository CRs), so it
can open the MR. Dedup uses the existing `alertGroupHash` so a persistently
firing rule does not spawn duplicate revert MRs.

## Cross-repo change list

| Repo | Change |
|---|---|
| tatara-operator | 3 new metric families; review-verdict record at the write-back Approve/RequestChanges branch; implement-CI record in handleMRCI on PR-CI conclusion; handleGrafanaAlert tier-quality branch templating the tier-revert incident goal; unit + envtest |
| tatara-observability | G4 dashboards (find-rate / findings-per-review / CI-pass by model); G5 alert rules on the G4 metrics with the tier-quality marker label; dashboard + rule validation |

The incident Task targets tatara-helmfile at runtime (agent opens the MR) - no
code change in tatara-helmfile.

## Build order (within the one plan)

1. **G4 metrics first.** Ship the review-outcome / findings / CI metrics + the
   SCM read + dashboards. Let the fleet run (a Stage-2 widen or normal steady
   state) to gather a real Sonnet-review baseline.
2. **G5 thresholds from the baseline.** Only then set the alert-rule thresholds
   and wire the operator tier-revert branch. This avoids arbitrary thresholds on
   a metric with almost no data today.

The plan sequences these; they are one spec but G5's alert thresholds depend on
G4's observed baseline.

## Testing

- operator unit: `operator_review_outcome_total` / `_findings_total` increment
  with the right `verdict`/`model` at the write-back Approve/RequestChanges
  branch; `operator_implement_ci_total` increments on a pass/fail conclusion in
  `handleMRCI`; the tier-revert goal string is table-tested from alert labels
  (kind/model/project -> expected goal, including the correct values path).
- operator envtest: a review Task written back via Approve/RequestChanges emits
  the outcome metric with `Status.ResolvedModel`; a tier-quality alert webhook
  creates an incident Task scoped to tatara-helmfile with the templated goal.
- observability: dashboard JSON valid + panel-guard asserts the new exprs;
  alert-rule validate (promtool/grafana rule check); the rules carry
  `homelab` + `system=tatara` + `tatara_tier_quality` labels.

## Acceptance criteria

- `operator_review_outcome_total` and `operator_review_findings_total` populate
  with a real `model` label as review Tasks terminate; a Sonnet review's
  find-rate is a readable panel.
- `operator_implement_ci_total` populates on implement PR CI conclusions.
- Dashboards render find-rate / findings / CI-pass by model.
- A tier-quality alert (fired or simulated) produces an incident Task scoped to
  tatara-helmfile whose goal proposes reverting the named kind's tier in the
  correct values file; the MR awaits human approval and is not self-merged.
- Alert thresholds are set from the G4 baseline, not guessed.

## Related

`docs/2026-07-04-token-conservation-p0-review-gaps.md` (G4/G5 source),
`docs/superpowers/specs/2026-07-04-durable-measurement-design.md` (the model
label G4 reuses), `docs/2026-07-04-token-conservation-reenable-runbook.md`
(the widen this de-risks), [[tatara-token-conservation-2026-07-04]],
[[grafana-alerting-terraform-broken-contactpoints-2026-06-24]] (alerts-as-code
in tatara-observability). G6 $-budget: separate follow-on.
