# Quality-feedback loop (G4+G5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Instrument the Sonnet-review/triage downgrade with model-keyed quality-proxy metrics (G4) and close a self-tuning loop that auto-proposes a tier-revert MR on regression (G5).

**Architecture:** Operator-only signal capture - review verdict recorded at the write-back Approve/RequestChanges branch, implement CI recorded in `handleMRCI`, both tagged with the token metrics' existing model resolution. A `handleGrafanaAlert` branch turns a tier-quality alert into an incident Task scoped to tatara-helmfile with a tier-revert goal. tatara-observability adds dashboards + the alert rules.

**Tech Stack:** Go 1.x (tatara-operator), prometheus/client_golang, controller-runtime + envtest, Grafana-managed dashboards + alert rules (tatara-observability).

## Global Constraints

- Repos: tatara-operator + tatara-observability. Branch off FRESH `origin/main` in each (external bots push). Worktree off main; never build/deploy from a worktree.
- Newest stable Go; JSON logs via `slog`; log every business action at INFO with structured fields.
- Metric label sets EXACTLY: `operator_review_outcome_total{project,repo,model,verdict}` (verdict in {approved, changes_requested}); `operator_review_findings_total{project,repo,model}`; `operator_implement_ci_total{project,repo,model,result}` (result in {pass, fail}). Reuse `taskTokenLabels(task)` for project/repo/model; `model` = `task.Status.ResolvedModel`.
- Model IDs: `claude-opus-4-8`, `claude-sonnet-5` (never invent).
- Alert rules carry labels `homelab`, `system=tatara`, plus the marker `tatara_tier_quality="true"` and `kind` + `model` + `project` (the operator keys the tier-revert branch on the marker and reads the others).
- TDD: failing test first. envtest via `KUBEBUILDER_ASSETS="$(go run sigs.k8s.io/controller-runtime/tools/setup-envtest@release-0.21 use 1.33.0 -p path)"`, `GOFLAGS=-buildvcs=false`. `make manifests` + `make generate` if CRD/markers change (none expected here). Pre-push hook runs full `make test`.
- Commit messages + PR bodies end with `https://claude.ai/code/session_01GVnfZiAkBdLANE2n3uN9BK`.
- Deploy is a SEPARATE gated step (fleet burn) - do NOT deploy from this plan.

---

## File Structure

**tatara-operator**
- `internal/obs/operator_metrics.go` - 3 new CounterVec fields + register + public methods (Task 1).
- `internal/controller/writeback.go` (~:1074/:1078) - record review outcome + findings at the Approve/RequestChanges branch (Task 2).
- `internal/controller/lifecycle.go` (`handleMRCI` ~:1925) - record implement CI pass/fail (Task 3).
- `internal/incident/goal.go` - new `GoalTierRevert` (Task 4).
- `internal/webhook/server.go` (`handleGrafanaAlert` ~:832, `createIncidentTask` ~:885) - tier-quality branch (Task 4).

**tatara-observability**
- Dashboards dir - a quality-feedback dashboard (Task 5).
- Alert rules dir - tier-quality rules (Task 6).

---

### Task 1: New quality metric families (operator)

**Files:**
- Modify: `internal/obs/operator_metrics.go`
- Test: `internal/obs/operator_metrics_test.go`

**Interfaces:**
- Produces: `(*OperatorMetrics).RecordReviewOutcome(project, repo, model, verdict string)`, `(*OperatorMetrics).AddReviewFindings(project, repo, model string, n int)`, `(*OperatorMetrics).RecordImplementCI(project, repo, model, result string)`.
- Consumes: the existing `OperatorMetrics` struct + registration idiom (mirror `taskTerminalTotal` at :319 and its `TaskTerminal` method at :943).

- [ ] **Step 1: Write failing test** - assert a registered gathering exposes the three families after calling the methods:

```go
func TestQualityMetrics_Emit(t *testing.T) {
    m := obs.NewOperatorMetrics(prometheus.NewRegistry()) // use the ctor's registry param; if none, adapt to the existing NewOperatorMetrics signature
    m.RecordReviewOutcome("tatara", "op", "claude-sonnet-5", "approved")
    m.RecordReviewOutcome("tatara", "op", "claude-sonnet-5", "changes_requested")
    m.AddReviewFindings("tatara", "op", "claude-sonnet-5", 3)
    m.RecordImplementCI("tatara", "op", "claude-opus-4-8", "fail")
    // gather + assert the 4 series exist with the right label values and counts
}
```

- [ ] **Step 2: Run - fail** (methods undefined).
- [ ] **Step 3: Implement** - add three CounterVec fields mirroring the `taskTerminalTotal` idiom:

```go
reviewOutcomeTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
    Name: "operator_review_outcome_total",
    Help: "Review tasks by verdict (approved|changes_requested), keyed by the model that ran the review.",
}, []string{"project", "repo", "model", "verdict"}),
reviewFindingsTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
    Name: "operator_review_findings_total",
    Help: "Sum of review findings (suggestions/comments) per review, by model.",
}, []string{"project", "repo", "model"}),
implementCITotal: prometheus.NewCounterVec(prometheus.CounterOpts{
    Name: "operator_implement_ci_total",
    Help: "Implement-task PR CI conclusions (pass|fail), by model.",
}, []string{"project", "repo", "model", "result"}),
```
Register them in the same place the other Vecs register, and add:
```go
func (m *OperatorMetrics) RecordReviewOutcome(project, repo, model, verdict string) {
    m.reviewOutcomeTotal.WithLabelValues(project, repo, model, verdict).Inc()
}
func (m *OperatorMetrics) AddReviewFindings(project, repo, model string, n int) {
    if n > 0 { m.reviewFindingsTotal.WithLabelValues(project, repo, model).Add(float64(n)) }
}
func (m *OperatorMetrics) RecordImplementCI(project, repo, model, result string) {
    m.implementCITotal.WithLabelValues(project, repo, model, result).Inc()
}
```

- [ ] **Step 4: Run - pass.**
- [ ] **Step 5: Commit** `feat: quality-proxy metric families (G4)`.

---

### Task 2: Record review outcome at write-back (operator)

**Files:**
- Modify: `internal/controller/writeback.go` (the Approve branch ~:1074, RequestChanges ~:1078)
- Test: `internal/controller/writeback_quality_test.go`

**Interfaces:**
- Consumes: Task 1 methods; `taskTokenLabels(task)` (turncallback.go:440 -> project, repo, kind, issue, model).
- Produces: metric side-effect only.

**Context:** the write-back loop iterates review verdict objects `v` and calls `writer.Approve(ctx, repo.Spec.URL, token, number, v.Body)` (approved) or `writer.RequestChanges(...)` (changes_requested). The enclosing function has the `task` in scope (it writes back that task's result). The finding count is the number of suggestions/comments on `v` - read the exact field when implementing (e.g. `len(v.Suggestions)` or the review's comment slice); if no count field exists, record findings only for `changes_requested` as `1` and note it.

- [ ] **Step 1: Write failing test** - a review task written back via RequestChanges records `operator_review_outcome_total{verdict="changes_requested", model=<ResolvedModel>}` +1 and findings by the count; an Approve records `verdict="approved"`. Use a fake `writer` + a registry-backed `OperatorMetrics`, assert the series.
- [ ] **Step 2: Run - fail.**
- [ ] **Step 3: Implement** - after a successful `Approve`/`RequestChanges` call, record:

```go
project, repo, _, _, model := taskTokenLabels(task)
if r.Metrics != nil {
    r.Metrics.RecordReviewOutcome(project, repo, model, "approved") // or "changes_requested" in that branch
    r.Metrics.AddReviewFindings(project, repo, model, findingCount)  // findingCount from v
}
```
Record only after the write succeeds (not on error). Do it in BOTH branches with the right verdict.

- [ ] **Step 4: Run - pass.**
- [ ] **Step 5: Commit** `feat: record review verdict + findings at write-back (G4)`.

---

### Task 3: Record implement CI in handleMRCI (operator)

**Files:**
- Modify: `internal/controller/lifecycle.go` (`handleMRCI` ~:1925, at the `GetPRState` CIStatus branch ~:1950)
- Test: `internal/controller/lifecycle_ci_metric_test.go`

**Interfaces:**
- Consumes: Task 1 `RecordImplementCI`; `taskTokenLabels(task)`; existing `st.CIStatus` (`"success"|"failure"|"pending"`).
- Produces: metric side-effect.

**Context:** `handleMRCI` reads `st, _ := writer.GetPRState(...)`; `st.CIStatus == "failure"` re-enters Implement, `"success"` proceeds to Merge. Record the metric on those two terminal conclusions only (skip `"pending"`), once per conclusion (guard against re-recording on repeated reconciles of the same conclusion - only record on the state TRANSITION, e.g. when the branch is actually taken to advance the task, not on every poll).

- [ ] **Step 1: Write failing test** - `handleMRCI` with a fake writer returning `CIStatus:"failure"` records `operator_implement_ci_total{result="fail", model=<ResolvedModel>}`; `"success"` records `result="pass"`; `"pending"` records nothing.
- [ ] **Step 2: Run - fail.**
- [ ] **Step 3: Implement** - in the success/failure branches:

```go
project, repo, _, _, model := taskTokenLabels(task)
switch st.CIStatus {
case "failure":
    if r.Metrics != nil { r.Metrics.RecordImplementCI(project, repo, model, "fail") }
    // existing re-enter-Implement logic
case "success":
    if r.Metrics != nil { r.Metrics.RecordImplementCI(project, repo, model, "pass") }
    // existing proceed-to-Merge logic
}
```
Ensure the record sits where the branch fires once per conclusion (co-located with the existing phase transition, which is idempotent per conclusion).

- [ ] **Step 4: Run - pass.**
- [ ] **Step 5: Commit** `feat: record implement PR CI pass/fail (G4)`.

---

### Task 4: Tier-revert incident goal + alert branch (operator, G5)

**Files:**
- Modify: `internal/incident/goal.go` (add `GoalTierRevert`)
- Modify: `internal/webhook/server.go` (`createIncidentTask` ~:885, uses `alert.CommonLabels`)
- Test: `internal/incident/goal_tier_revert_test.go`, `internal/webhook/grafana_tier_revert_test.go`

**Interfaces:**
- Produces: `incident.GoalTierRevert(project, kind, model string) string`.
- Consumes: `GrafanaAlert.CommonLabels map[string]string` (grafana.go); the existing `createIncidentTask` -> `queue.EnqueueEvent(... QueueClassAlert ...)` path.

- [ ] **Step 1: Write failing test (goal)** - `GoalTierRevert("tatara", "review", "claude-sonnet-5")` returns a goal that names the kind, the revert-to model `claude-opus-4-8`, and the exact path `values/project-tatara/common.yaml` `agent.modelByKind`/`agent.effortByKind`, and instructs "open one MR, do not merge".
- [ ] **Step 2: Run - fail.**
- [ ] **Step 3: Implement `GoalTierRevert`:**

```go
func GoalTierRevert(project, kind, model string) string {
    return "A quality-proxy alert is FIRING: kind \"" + kind + "\" on model \"" + model +
        "\" has regressed in project \"" + project + "\". Propose reverting this kind's tier: in " +
        "tatara-helmfile values/project-" + project + "/common.yaml, set agent.modelByKind[" + kind +
        "] back to claude-opus-4-8 and raise agent.effortByKind[" + kind + "] (to high). Open ONE MR " +
        "against tatara-helmfile with only that change and a short rationale citing the alert. Do NOT merge."
}
```

- [ ] **Step 4: Run - pass.**
- [ ] **Step 5: Write failing test (branch)** - a firing alert with `CommonLabels{"tatara_tier_quality":"true","kind":"review","model":"claude-sonnet-5"}` produces an incident QueuedEvent whose `Goal` == `GoalTierRevert(project,"review","claude-sonnet-5")` and is scoped so tatara-helmfile is a target; an alert WITHOUT the marker uses the existing `GoalProject`.
- [ ] **Step 6: Run - fail.**
- [ ] **Step 7: Implement branch** in `createIncidentTask`:

```go
var goal string
if alert.CommonLabels["tatara_tier_quality"] == "true" {
    goal = incident.GoalTierRevert(proj.Name, alert.CommonLabels["kind"], alert.CommonLabels["model"])
} else {
    goal = incident.GoalProject(alertCtx, slugs)
}
```
Keep the rest of the payload (Kind=incident, dedup groupHash, alert annotations) unchanged.

- [ ] **Step 8: Run - pass** (unit + the covering envtest if present).
- [ ] **Step 9: Commit** `feat: tier-quality alert -> tier-revert incident goal (G5)`.

---

### Task 5: Quality-feedback dashboard (tatara-observability)

**Files:**
- Create: a quality-feedback dashboard following the repo's existing dashboard structure (inspect the dashboards dir first; match the provisioning format the other tatara dashboards use).
- Test: the repo's dashboard-validation path (JSON valid + panel-guard, mirror an existing dashboard test).

**Panels (PromQL):**
- Review find-rate by model: `sum by (model) (rate(operator_review_outcome_total{verdict="changes_requested"}[1h])) / sum by (model) (rate(operator_review_outcome_total[1h]))`.
- Findings per review by model: `sum by (model) (rate(operator_review_findings_total[1h])) / sum by (model) (rate(operator_review_outcome_total[1h]))`.
- Implement CI-pass-rate by model: `sum by (model) (rate(operator_implement_ci_total{result="pass"}[1h])) / sum by (model) (rate(operator_implement_ci_total[1h]))`.

- [ ] **Step 1: Inspect** the existing dashboard + its test/guard; copy the pattern.
- [ ] **Step 2: Write the dashboard** with the three panels above.
- [ ] **Step 3: Validate** (repo's dashboard test / `terraform validate` / JSON lint as the repo uses).
- [ ] **Step 4: Commit** `feat: quality-feedback dashboard (G4)`.

---

### Task 6: Tier-quality alert rules (tatara-observability, G5)

**Files:**
- Create/Modify: the alert-rules definitions following the repo's existing Grafana-managed alert-rule format (inspect first; these went through the alerts-as-code migration - see MEMORY note grafana-alerting-terraform-broken-contactpoints).
- Test: the repo's rule-validation path.

**Rules (on the downgraded kinds; thresholds are INITIAL/rough - tune from the G4 baseline once data exists, per the build order):**
- Rubber-stamp: for `model="claude-sonnet-5"`, `sum(rate(operator_review_outcome_total{verdict="changes_requested"}[6h])) / sum(rate(operator_review_outcome_total{model="claude-sonnet-5"}[6h])) < 0.02` sustained AND a minimum review volume (avoid firing on 1-2 reviews) - i.e. find-rate near zero.
- (Optional, once CI attribution is trusted) implement CI-pass-rate for merged work dropping below a floor.

Each rule MUST set labels: `homelab`, `system=tatara`, `tatara_tier_quality="true"`, `kind="review"`, `model="claude-sonnet-5"`, `project=<p>`, and route to the project's existing Grafana webhook contact point.

- [ ] **Step 1: Inspect** the existing tatara alert rules + their validation; copy the label + routing pattern.
- [ ] **Step 2: Write the rule(s)** with a code comment that the thresholds are provisional pending the G4 baseline.
- [ ] **Step 3: Validate** (repo's rule check).
- [ ] **Step 4: Commit** `feat: tier-quality alert rules (G5)`.

---

## Self-Review

- **Spec coverage:** G4 metrics (Task 1), review capture (Task 2), implement-CI (Task 3), dashboards (Task 5) = G4. Tier-revert goal + alert branch (Task 4), alert rules (Task 6) = G5. All spec sections covered.
- **Build order:** G4 code (1-3,5) is independent of G5 (4,6). G5's alert THRESHOLDS (Task 6) are marked provisional pending baseline - the code lands, numbers tune later. Consistent with the spec.
- **Types:** `RecordReviewOutcome/AddReviewFindings/RecordImplementCI` and `GoalTierRevert` signatures are used identically in the consuming tasks. `taskTokenLabels` returns `(project, repo, kind, issue, model)` - tasks read positions correctly.
- **Open detail flagged for implementer:** the review finding-count field on the write-back verdict object `v` (Task 2) - read the exact field; fall back to `1` for changes_requested if none. The implement-CI once-per-conclusion idempotency (Task 3) - co-locate with the existing phase transition.
