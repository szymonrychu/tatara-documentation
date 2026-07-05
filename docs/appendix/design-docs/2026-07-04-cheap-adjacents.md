# Cheap Adjacents (Token Conservation, Component 5) Implementation Plan

> For agentic workers: this plan is executed by parallel implementation
> subagents driven by the orchestrator.
> REQUIRED SUB-SKILL: superpowers:subagent-driven-development

Goal: land the four independent, low-effort, high-ROI token-conservation
adjacents from component 5 of the token-conservation design
(`docs/superpowers/specs/2026-07-04-token-conservation-design.md`):
(1) stretch `mrScan`/`issueScan`/`refine` scan cadence from hourly to every
2-4h; (2) activate the already-live stale-proposal reaper via
`staleProposalDays=14` AND gate bot-authored brainstorming proposals on human
activity so churn stops at source; (3) fix the `implementPrompt` double-append
of `platformProblemGuidance`+`toolingConsumeGuidance` (~219 tok/Implement
turn-0); (4) add a per-Task cumulative-token runaway backstop for the
turn-uncapped `implement`/`issueLifecycle` kinds.

Architecture: all code changes are in `tatara-operator` (Go, controller-runtime
operator). All configuration changes are in `tatara-helmfile` (Project CR values
rendered verbatim by the `tatara-project` chart, `charts/tatara-project/templates/project.yaml`
does `toYaml .Values.project.spec`). No `tatara-cli`/`wrapper`/`skills` changes.
Deploy is GitOps: merge operator `main` (CI builds+pushes image+chart), then one
`tatara-helmfile` MR bumping the `tatara-operator` chart version + pinned
`image.tag` AND applying the Project values edits.

Tech Stack: Go 1.26.3, controller-runtime, kubebuilder CRDs, testify + stdlib
`testing` table tests, Prometheus client, Helm/helmfile YAML.

## Global Constraints

- Newest stable Go; pin the exact minor in go.mod. gofmt + golangci-lint must pass. Wrap errors with %w. Table-driven tests with t.Run.
- KISS. No tech debt; if complex, note rationale in MEMORY.md.
- JSON logs via stdlib log/slog. Expose /metrics Prometheus on every service. Log business actions at INFO with structured fields.
- Charts via 'helm create' then edited, never hand-rolled. Charts cluster-agnostic.
- values.yaml rule: NO plain ENVs, NO lists in values.yaml. camelCase scalar in values.yaml -> kebab-case key in ConfigMap/Secret -> workload consumes via envFrom. List-shaped data goes into a templated ConfigMap read at runtime. (Note: CRD spec fields are exempt from this - they are typed API fields, not helm values; a map[string]string on a CRD spec is fine.)
- Model IDs are authoritative literals: claude-opus-4-8 (Opus), claude-sonnet-5 (Sonnet). Effort enum: low|medium|high|xhigh|max.
- Deploy ONLY via tatara-helmfile GitOps: merge component repo main (CI builds+pushes image/chart), then a tatara-helmfile MR bumping BOTH the chart version AND the pinned image.tag for the release. Never kubectl set-image/patch to ship. Project CR value changes are tatara-helmfile values edits.
- Branch flow: worktree off main -> develop -> merge to component main -> deploy from main only.

Repo roots (absolute):
- operator: `/Users/szymonri/Documents/tatara/tatara-operator`
- helmfile: `/Users/szymonri/Documents/tatara/tatara-helmfile`

Build/test invocation in the operator repo uses mise: `mise exec -- go test ./internal/controller/...` etc. `mise install` once in a fresh clone.

Tasks 1, 2, 3 are independent operator code changes (different files, no shared
symbols) and MAY be dispatched in parallel. Task 4 (helmfile values) is
independent of the operator code except that the `maxTaskTokens` value it sets
requires Task 2's CRD field to exist in the deployed CRD; sequencing is handled
in Task 6 (deploy). Task 5 regenerates operator CRD/deepcopy artifacts and MUST
run after Tasks 1-3 merge. Task 6 is the deploy, last.

---

### Task 1: Fix implementPrompt double-append of platformProblemGuidance + toolingConsumeGuidance

The Implement turn-0 prompt bills `platformProblemGuidance` twice
(`planTurnText` adds it at `turnloop.go:66`; `lifecyclePhaseGuidance("Implement")`
adds it again at `turnloop.go:88`) and `toolingConsumeGuidance` twice
(`planTurnText` adds it at `turnloop.go:66`; an explicit `base += toolingConsumeGuidance`
at `lifecycle.go:1518`). Root-cause fix: split the lifecycle-phase block from its
trailing `platformProblemGuidance` so `implementPrompt` can append the phase block
WITHOUT re-adding the guidance `planTurnText` already carried, and drop the
explicit `toolingConsumeGuidance` re-append. Other callers (`lifecycleTriageText`,
which does NOT use `planTurnText`) keep the guidance-carrying
`lifecyclePhaseGuidance`.

**Files:**
- Modify: `internal/controller/turnloop.go:77` (split `lifecyclePhaseGuidance` into `lifecyclePhaseBlock` + guidance wrapper)
- Modify: `internal/controller/lifecycle.go:1504` (use block, not guidance) and `internal/controller/lifecycle.go:1518` (delete explicit re-append)
- Test: `internal/controller/lifecycle_test.go` (new `TestImplementPrompt_GuidanceAppearsExactlyOnce`)

**Interfaces:**
- Produces: `func lifecyclePhaseBlock(state string) string` - the "## Lifecycle phase" block WITHOUT the trailing `platformProblemGuidance`.
- Consumes/preserves: `func lifecyclePhaseGuidance(state string) string` still returns `lifecyclePhaseBlock(state) + platformProblemGuidance` (unchanged external behavior for `lifecycleTriageText` at `turnloop.go:162` and `directive_platform_problem_test.go:29`).
- `func implementPrompt(task *tatarav1alpha1.Task) string` output unchanged except the two duplicate blocks removed.

Steps:

- [ ] **Step 1: Write the failing test.** Add to `internal/controller/lifecycle_test.go`:
  ```go
  // TestImplementPrompt_GuidanceAppearsExactlyOnce guards the token-conservation
  // double-append fix: the Implement turn-0 prompt must carry platformProblemGuidance
  // and toolingConsumeGuidance exactly once each (planTurnText already appends both;
  // lifecyclePhaseGuidance and the old explicit re-append duplicated them).
  func TestImplementPrompt_GuidanceAppearsExactlyOnce(t *testing.T) {
  	task := &tatarav1alpha1.Task{
  		ObjectMeta: metav1.ObjectMeta{Name: "task-dedupe", Namespace: testNS},
  		Spec: tatarav1alpha1.TaskSpec{
  			ProjectRef: "proj", RepositoryRef: "repo",
  			Goal: "fix the bug", Kind: "issueLifecycle",
  		},
  	}
  	got := implementPrompt(task)
  	if n := strings.Count(got, "## Platform problems"); n != 1 {
  		t.Errorf("platformProblemGuidance appears %d times, want 1:\n%s", n, got)
  	}
  	if n := strings.Count(got, toolingConsumeSubstr); n != 1 {
  		t.Errorf("toolingConsumeGuidance (%q) appears %d times, want 1:\n%s", toolingConsumeSubstr, n, got)
  	}
  	// The phase block must still be present (fix must not delete it).
  	if !strings.Contains(got, "## Lifecycle phase: Implement") {
  		t.Errorf("implementPrompt missing lifecycle phase block:\n%s", got)
  	}
  }
  ```
  (`toolingConsumeSubstr = "## Tooling from the issue"` already exists in `turnloop_tooling_test.go:16`; `strings` and `metav1` are already imported in `lifecycle_test.go`.)

- [ ] **Step 2: Run it, expect FAIL.** `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run TestImplementPrompt_GuidanceAppearsExactlyOnce -count=1`
  Expect: `platformProblemGuidance appears 2 times, want 1` and `toolingConsumeGuidance ("## Tooling from the issue") appears 2 times, want 1`, `FAIL`.

- [ ] **Step 3: Split the phase block from its guidance in `turnloop.go`.** Replace the body of `lifecyclePhaseGuidance` (currently `turnloop.go:77-89`) with a block helper plus a thin guidance wrapper:
  ```go
  // lifecyclePhaseBlock returns the "## Lifecycle phase" block WITHOUT the trailing
  // platformProblemGuidance. Callers that already carry platformProblemGuidance
  // (implementPrompt, via planTurnText) use this to avoid double-billing it; callers
  // that do not (lifecycleTriageText) use lifecyclePhaseGuidance, which appends it.
  func lifecyclePhaseBlock(state string) string {
  	durable := "Only what you post to the issue/MR conversation (comments, the issue_outcome decision) survives to the next run. Any file edits you make in this workspace are discarded and will NOT be restored."
  	switch state {
  	case "Implement", "MRCI", "Merge", "MainCI":
  		durable = "Changes you commit and push to the task branch ARE restored on the next run (the workspace is re-cloned and the branch checked out). Uncommitted file edits are discarded."
  	}
  	return fmt.Sprintf(
  		"\n\n## Lifecycle phase: %s\n"+
  			"This issue is handled as a multi-phase conversation and you are currently in the %s phase. "+
  			"The workspace is transient: it is rebuilt by git clone+checkout on every run and nothing on disk carries over between runs by itself. "+
  			"%s",
  		state, state, durable)
  }

  // lifecyclePhaseGuidance is lifecyclePhaseBlock plus platformProblemGuidance, for
  // callers (lifecycleTriageText) that do not already carry the platform guidance.
  func lifecyclePhaseGuidance(state string) string {
  	return lifecyclePhaseBlock(state) + platformProblemGuidance
  }
  ```
  (Keep the existing doc comment above `lifecyclePhaseGuidance` at `turnloop.go:69-76` describing the phase semantics; move it above `lifecyclePhaseBlock`.)

- [ ] **Step 4: Use the block + drop the explicit re-append in `implementPrompt`.** In `internal/controller/lifecycle.go`:
  - Change line 1504 `base += lifecyclePhaseGuidance("Implement")` to `base += lifecyclePhaseBlock("Implement")`.
  - Delete line 1518 `base += toolingConsumeGuidance` (the return becomes the immediately-following `return base`). `planTurnText` at `lifecycle.go:1476` already appended `toolingConsumeGuidance`, so the prompt keeps it exactly once.

- [ ] **Step 5: Run the new test + the existing prompt tests, expect PASS.**
  `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run 'TestImplementPrompt_GuidanceAppearsExactlyOnce|TestLifecycleImplementPlanText|TestToolingConsumeGuidance|TestDirective' -count=1`
  Expect: `ok  github.com/szymonrychu/tatara-operator/internal/controller`. (`TestToolingConsumeGuidance_InImplementPrompt` and `directive_platform_problem_test.go` still pass: the guidance is present once, and `lifecyclePhaseGuidance("Implement")` still contains `platformProblemGuidance` in isolation.)

- [ ] **Step 6: Commit.**
  `cd /Users/szymonri/Documents/tatara/tatara-operator && git add internal/controller/turnloop.go internal/controller/lifecycle.go internal/controller/lifecycle_test.go && git commit -m "fix: implementPrompt double-appended platform+tooling guidance"`

---

### Task 2: Per-Task cumulative-token runaway backstop for implement/issueLifecycle

`turnCap` (`task_controller.go:1089`) returns `(0,false)` for `implement`/`issueLifecycle`
so those kinds run turn-uncapped. Add an opt-in per-Task cumulative-token ceiling
that fails the Task when `Status.CumulativeTokens` (the operator's output-token
ledger, accumulated at `turncallback.go:276`) crosses `Project.Spec.Agent.MaxTaskTokens`.
Gate ONLY those two kinds; every other kind keeps its turn cap and is never token-gated.
The threshold is opt-in (0 = disabled, default), set from telemetry in the helmfile
values (Task 4); the terminate path already emits `operator_task_terminal_total{kind,phase,reason}`
so no new metric is needed.

**Files:**
- Modify: `api/v1alpha1/project_types.go:139` (add `MaxTaskTokens` to `AgentSpec`, after `Effort`)
- Modify: `internal/controller/task_controller.go:1100` (new `taskTokenBudgetExceeded` helper after `turnCap`) and `internal/controller/task_controller.go:1002` (wire the check at the turn-cap site)
- Test: `api/v1alpha1/project_types_test.go` (new `TestAgentSpec_MaxTaskTokensField`)
- Test: `internal/controller/task_controller_test.go` (new `TestTaskTokenBudgetExceeded` table test - create the file if absent using the package + imports below)

**Interfaces:**
- Produces field: `AgentSpec.MaxTaskTokens int64` (`json:"maxTaskTokens,omitempty"`, no kubebuilder default = 0 disabled).
- Produces: `func taskTokenBudgetExceeded(project *tatarav1alpha1.Project, task *tatarav1alpha1.Task) bool`.
- Consumes: `task.Status.CumulativeTokens int64` (`api/v1alpha1/task_types.go:374`), `project.Spec.Agent.MaxTaskTokens`, `task.Spec.Kind`.

Steps:

- [ ] **Step 1: Write the failing field test.** Add to `api/v1alpha1/project_types_test.go` (mirrors the existing `TestBrainstormActivity_StaleProposalDaysField` at line 230):
  ```go
  func TestAgentSpec_MaxTaskTokensField(t *testing.T) {
  	a := v1alpha1.AgentSpec{MaxTaskTokens: 3_000_000}
  	if a.MaxTaskTokens != 3_000_000 {
  		t.Fatalf("MaxTaskTokens = %d, want 3000000", a.MaxTaskTokens)
  	}
  	var z v1alpha1.AgentSpec
  	if z.MaxTaskTokens != 0 {
  		t.Fatalf("MaxTaskTokens zero value = %d, want 0 (backstop disabled)", z.MaxTaskTokens)
  	}
  }
  ```

- [ ] **Step 2: Run it, expect FAIL (compile error).** `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./api/v1alpha1/ -run TestAgentSpec_MaxTaskTokensField -count=1`
  Expect: `a.MaxTaskTokens undefined (type v1alpha1.AgentSpec has no field or method MaxTaskTokens)`, `FAIL [build failed]`.

- [ ] **Step 3: Add the field.** In `api/v1alpha1/project_types.go`, immediately after the `Effort string` field (ends at line 139), add:
  ```go
  	// MaxTaskTokens is a per-Task cumulative output-token ceiling for the
  	// otherwise turn-uncapped implementation kinds (implement, issueLifecycle): a
  	// runaway backstop, not a cost lever. 0 disables it (the default); opt in via
  	// the Project values. When Status.CumulativeTokens crosses it the Task is
  	// failed with reason TokenBudgetExceeded. TUNE from the component-6 per-kind
  	// token telemetry once a healthy-run distribution is known.
  	// +optional
  	MaxTaskTokens int64 `json:"maxTaskTokens,omitempty"`
  ```

- [ ] **Step 4: Run the field test, expect PASS.** `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./api/v1alpha1/ -run TestAgentSpec_MaxTaskTokensField -count=1`
  Expect: `ok  github.com/szymonrychu/tatara-operator/api/v1alpha1`.

- [ ] **Step 5: Write the failing helper table test.** Add `internal/controller/task_controller_budget_test.go`:
  ```go
  package controller

  import (
  	"testing"

  	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
  )

  func TestTaskTokenBudgetExceeded(t *testing.T) {
  	mk := func(kind string, limit, used int64) (*tatarav1alpha1.Project, *tatarav1alpha1.Task) {
  		p := &tatarav1alpha1.Project{}
  		p.Spec.Agent.MaxTaskTokens = limit
  		tk := &tatarav1alpha1.Task{}
  		tk.Spec.Kind = kind
  		tk.Status.CumulativeTokens = used
  		return p, tk
  	}
  	cases := []struct {
  		name  string
  		kind  string
  		limit int64
  		used  int64
  		want  bool
  	}{
  		{"implement under budget continues", "implement", 1000, 500, false},
  		{"implement at budget stops", "implement", 1000, 1000, true},
  		{"implement over budget stops", "implement", 1000, 1500, true},
  		{"issueLifecycle over budget stops", "issueLifecycle", 1000, 2000, true},
  		{"review over budget not gated", "review", 1000, 5000, false},
  		{"triageIssue over budget not gated", "triageIssue", 1000, 5000, false},
  		{"implement disabled when limit zero", "implement", 0, 9_999_999, false},
  	}
  	for _, tc := range cases {
  		t.Run(tc.name, func(t *testing.T) {
  			p, tk := mk(tc.kind, tc.limit, tc.used)
  			if got := taskTokenBudgetExceeded(p, tk); got != tc.want {
  				t.Errorf("taskTokenBudgetExceeded(kind=%s limit=%d used=%d) = %v, want %v",
  					tc.kind, tc.limit, tc.used, got, tc.want)
  			}
  		})
  	}
  }
  ```

- [ ] **Step 6: Run it, expect FAIL (undefined).** `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run TestTaskTokenBudgetExceeded -count=1`
  Expect: `undefined: taskTokenBudgetExceeded`, `FAIL [build failed]`.

- [ ] **Step 7: Add the helper.** In `internal/controller/task_controller.go`, immediately after the `turnCap` function (ends at line 1100), add:
  ```go
  // taskTokenBudgetExceeded reports whether an uncapped implementation Task has
  // burned past its per-Task output-token backstop. Only implement/issueLifecycle
  // are gated (they run turn-uncapped per turnCap); every other kind keeps the turn
  // cap and is never token-gated here. A zero MaxTaskTokens disables the backstop.
  func taskTokenBudgetExceeded(project *tatarav1alpha1.Project, task *tatarav1alpha1.Task) bool {
  	if task.Spec.Kind != "implement" && task.Spec.Kind != "issueLifecycle" {
  		return false
  	}
  	limit := project.Spec.Agent.MaxTaskTokens
  	if limit <= 0 {
  		return false
  	}
  	return task.Status.CumulativeTokens >= limit
  }
  ```

- [ ] **Step 8: Wire the check at the turn-cap site.** In `internal/controller/task_controller.go`, immediately after the `turnCap` block (the `if limit, capped := turnCap(...)` ... `}` ending at line 1002), add:
  ```go
  	// Per-Task token runaway backstop (token conservation, component 5): the
  	// uncapped implementation kinds fail cleanly when they cross the per-Task
  	// output-token ceiling, so a looping agent cannot burn unbounded tokens. The
  	// terminate path records operator_task_terminal_total{reason=TokenBudgetExceeded}.
  	if taskTokenBudgetExceeded(project, task) {
  		return r.terminate(ctx, task, "Failed", "TokenBudgetExceeded",
  			fmt.Sprintf("cumulative tokens %d reached the per-task budget %d",
  				task.Status.CumulativeTokens, project.Spec.Agent.MaxTaskTokens))
  	}
  ```
  (`fmt` is already imported in `task_controller.go`.)

- [ ] **Step 9: Run helper + package tests, expect PASS.** `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run 'TestTaskTokenBudgetExceeded' -count=1 && mise exec -- go build ./...`
  Expect: `ok  github.com/szymonrychu/tatara-operator/internal/controller` and a clean build.

- [ ] **Step 10: Commit.**
  `cd /Users/szymonri/Documents/tatara/tatara-operator && git add api/v1alpha1/project_types.go api/v1alpha1/project_types_test.go internal/controller/task_controller.go internal/controller/task_controller_budget_test.go && git commit -m "feat: per-task token runaway backstop for implement/issueLifecycle"`

---

### Task 3: Gate bot-authored brainstorming proposals on human activity in issueScan

A bot-authored brainstorming proposal (carries the brainstorming label, none of
approved/implementation/declined) with no comments passes the existing gates:
`lastTerminalNoLabelTask` (`projectscan.go:1358`) only fires for no-managed-label
issues, `botHadLastWord` (`projectscan.go:1376`) returns false when there are no
comments, and `reapEligible` (`projectscan.go:1383`) only fires once the proposal
is ALSO stale (> `StaleProposalDays`). So between fresh and stale a fresh triage
Task is spawned every scan cycle - the observed brainstorm churn. Add a
label-only predicate plus a human-activity gate that skips fresh Task creation for
such a proposal until any human comment exists. This stops the churn from the
first cycle; the reaper (Task 4 activates it) still closes it once stale.

**Files:**
- Modify: `internal/controller/projectscan.go:517` (new pure `isBotBrainstormProposal` predicate after `isStaleUnengagedProposal`)
- Modify: `internal/controller/projectscan.go:1273` (compute lifecycle labels) and `internal/controller/projectscan.go:1382` (new gate in the create loop, after `botHadLastWord`, before `reapEligible`)
- Test: `internal/controller/projectscan_brainstorm_gate_test.go` (new)

**Interfaces:**
- Produces: `func isBotBrainstormProposal(c candidate, brainstorming, approved, implementation, declined, botLogin string) bool`.
- Consumes: `candidate{repo,number,author,labels,isPR}` (`projectscan.go`), `hasLabel`/`hasAnyLabel` (`projectscan.go:455,467`), `lifecycleLabels` (`labels.go:18`), `humanCommentAfter` (`projectscan.go:1940`, zero `since` = "any human comment ever"), `r.Metrics.ScanItem` (free-form reason string).

Steps:

- [ ] **Step 1: Write the failing test.** Add `internal/controller/projectscan_brainstorm_gate_test.go`:
  ```go
  package controller

  import (
  	"context"
  	"testing"
  	"time"

  	"github.com/prometheus/client_golang/prometheus"
  	"github.com/stretchr/testify/require"
  	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
  	"github.com/szymonrychu/tatara-operator/internal/obs"
  	"github.com/szymonrychu/tatara-operator/internal/scm"
  )

  // freshBrainstormIssue is a bot-authored, brainstorming-labelled proposal that is
  // NOT stale (recent UpdatedAt) so the reaper's stale gate does not fire; only the
  // human-activity gate should suppress a fresh triage Task.
  func freshBrainstormIssue() scm.IssueRef {
  	return scm.IssueRef{Repo: "o/r", Number: 7, Author: "tatara-bot",
  		Labels: []string{"tatara-brainstorming"}, UpdatedAt: time.Now().Add(-2 * time.Hour)}
  }

  // TestIsBotBrainstormProposal is the pure label predicate.
  func TestIsBotBrainstormProposal(t *testing.T) {
  	brs, app, impl, dec := "tatara-brainstorming", "tatara-approved", "tatara-implementation", "tatara-declined"
  	cases := []struct {
  		name string
  		c    candidate
  		want bool
  	}{
  		{"bot brainstorming proposal", candidate{repo: "o/r", number: 7, author: "tatara-bot", labels: []string{brs}}, true},
  		{"human authored", candidate{repo: "o/r", number: 7, author: "szymonrychu", labels: []string{brs}}, false},
  		{"empty author", candidate{repo: "o/r", number: 7, author: "", labels: []string{brs}}, false},
  		{"advanced to approved", candidate{repo: "o/r", number: 7, author: "tatara-bot", labels: []string{brs, app}}, false},
  		{"already declined", candidate{repo: "o/r", number: 7, author: "tatara-bot", labels: []string{brs, dec}}, false},
  		{"no brainstorming label", candidate{repo: "o/r", number: 7, author: "tatara-bot", labels: []string{"other"}}, false},
  		{"is a PR", candidate{repo: "o/r", number: 7, author: "tatara-bot", labels: []string{brs}, isPR: true}, false},
  	}
  	for _, tc := range cases {
  		t.Run(tc.name, func(t *testing.T) {
  			if got := isBotBrainstormProposal(tc.c, brs, app, impl, dec, "tatara-bot"); got != tc.want {
  				t.Errorf("isBotBrainstormProposal = %v, want %v", got, tc.want)
  			}
  		})
  	}
  }

  // TestIssueScan_SkipsBrainstormProposalWithoutHumanActivity: a fresh bot proposal
  // with no comments must NOT create a triage Task.
  func TestIssueScan_SkipsBrainstormProposalWithoutHumanActivity(t *testing.T) {
  	proj, repo := seedBackstopProject(t, "bsgate-nohuman")
  	reader := &perRepoFakeReader{issuesByRepo: map[string][]scm.IssueRef{"o/r": {freshBrainstormIssue()}}}
  	// fakeReader.ListIssueComments returns nil -> no human comment ever.
  	r := newScanReconciler(reader)
  	r.Metrics = obs.NewOperatorMetrics(prometheus.NewRegistry())

  	r.issueScan(context.Background(), proj, reader, []tatarav1alpha1.Repository{repo}, nil, proj.Spec.Scm.Cron.IssueScan)

  	qes := listScanQEs(t, "bsgate-nohuman")
  	require.Empty(t, qes, "issueScan must not triage a bot brainstorming proposal with no human activity")
  }

  // TestIssueScan_TriagesBrainstormProposalWithHumanActivity: the same proposal WITH
  // a human comment must be triaged (one QueuedEvent created).
  func TestIssueScan_TriagesBrainstormProposalWithHumanActivity(t *testing.T) {
  	proj, repo := seedBackstopProject(t, "bsgate-human")
  	reader := &perRepoFakeReader{
  		fakeReader:   fakeReader{comments: []scm.IssueComment{{Author: "szymonrychu", Body: "please build this", CreatedAt: time.Now()}}},
  		issuesByRepo: map[string][]scm.IssueRef{"o/r": {freshBrainstormIssue()}},
  	}
  	r := newScanReconciler(reader)
  	r.Metrics = obs.NewOperatorMetrics(prometheus.NewRegistry())

  	r.issueScan(context.Background(), proj, reader, []tatarav1alpha1.Repository{repo}, nil, proj.Spec.Scm.Cron.IssueScan)

  	qes := listScanQEs(t, "bsgate-human")
  	require.Len(t, qes, 1, "issueScan must triage a bot brainstorming proposal once a human has engaged")
  }
  ```
  (`seedBackstopProject`, `newScanReconciler`, `listScanQEs`, `perRepoFakeReader`, `fakeReader` all exist in the controller test package; `scm.IssueComment{Author,Body,CreatedAt}` per `botIsLastCommenter` usage. Confirm the exact `IssueComment` field names via `internal/scm` before writing - use `CreatedAt` as in `projectscan.go:1979`.)

- [ ] **Step 2: Run it, expect FAIL.** `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run 'TestIsBotBrainstormProposal|TestIssueScan_.*BrainstormProposal' -count=1`
  Expect: `undefined: isBotBrainstormProposal` (build failed) - the predicate test - and once the predicate exists but the gate is not wired, `TestIssueScan_SkipsBrainstormProposalWithoutHumanActivity` fails with `Should be empty, but was [<one QE>]`.

- [ ] **Step 3: Add the pure predicate.** In `internal/controller/projectscan.go`, immediately after `isStaleUnengagedProposal` (ends at line 517), add:
  ```go
  // isBotBrainstormProposal reports whether the candidate is a bot-authored, open
  // (non-PR) brainstorming proposal still in the proposal phase: it carries the
  // brainstorming label and NONE of approved/implementation/declined. It is the
  // label-only half of the source-of-churn gate - such a proposal must not spawn a
  // fresh triage Task every scan cycle until a human engages it. Mirrors
  // isStaleUnengagedProposal's author+label gates without the time / task-liveness
  // checks (the reaper owns staleness; this owns "never engaged").
  func isBotBrainstormProposal(c candidate, brainstorming, approved, implementation, declined, botLogin string) bool {
  	if c.isPR {
  		return false
  	}
  	if c.author == "" || c.author != botLogin {
  		return false
  	}
  	return hasLabel(c.labels, brainstorming) && !hasAnyLabel(c.labels, []string{approved, implementation, declined})
  }
  ```

- [ ] **Step 4: Compute lifecycle labels once in issueScan.** In `internal/controller/projectscan.go`, right after `managed := managedPhaseLabels(proj.Spec.Scm)` (line 1273), add:
  ```go
  	brainstorming, approved, implementation, declined := lifecycleLabels(proj.Spec.Scm)
  ```

- [ ] **Step 5: Add the gate in the create loop.** In `internal/controller/projectscan.go`, in the `for _, c := range eligible` loop, immediately after the `botHadLastWord` block (ends at line 1382) and before the `reapEligible` block (line 1383), add:
  ```go
  		// Source-of-churn gate (token conservation, component 5): a bot-authored
  		// brainstorming proposal no human has engaged must not be re-triaged every
  		// scan cycle. The reaper (staleProposalDays) only closes it once it is ALSO
  		// stale; this stops the churn from the first cycle. Any human comment (zero
  		// `since` = ever) clears it. Fail-open when SCM/botLogin/owner-split is
  		// unavailable, matching botHadLastWord and the reactivation gate.
  		if isBotBrainstormProposal(c, brainstorming, approved, implementation, declined, botLogin) {
  			owner, name, cut := strings.Cut(c.repo, "/")
  			if cut && reader != nil && botLogin != "" &&
  				!humanCommentAfter(ctx, reader, owner, name, c.number, botLogin, time.Time{}) {
  				r.Metrics.ScanItem("issueScan", "skipped_brainstorm_no_human")
  				l.Info("issueScan: skipped fresh task creation, brainstorming proposal awaiting human engagement",
  					"action", "scan_issue", "resource_id", proj.Name,
  					"issue", fmt.Sprintf("%s#%d", c.repo, c.number))
  				continue
  			}
  		}
  ```
  (`strings`, `time`, `fmt` already imported in `projectscan.go`.)

- [ ] **Step 6: Run the brainstorm-gate tests + the reap-gate regression, expect PASS.** `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run 'TestIsBotBrainstormProposal|TestIssueScan_.*BrainstormProposal|TestIssueScan_.*Reap|TestIssueScan_Triages' -count=1`
  Expect: `ok  github.com/szymonrychu/tatara-operator/internal/controller`. (`TestIssueScan_TriagesWhenReaperDisabled` uses a stale issue with reaper off; the new gate only fires for bot brainstorming proposals with no human comment - confirm that test's fixture still yields a Task; the stale issue is bot+brainstorming with no comments, so the new gate WOULD now suppress it too. If that regression test flips to empty, it is CORRECT new behavior: the churn source is closed independent of the reaper. Update `TestIssueScan_TriagesWhenReaperDisabled` to seed a human comment via `fakeReader{comments: [...]}` so it still asserts a Task is created, preserving its original intent - "reaper disabled -> normal triage" - under the stricter gate.)

- [ ] **Step 7: Run the full package test to catch collateral, expect PASS.** `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -count=1`
  Expect: `ok`. Fix any other scan test that relied on a commentless bot brainstorming proposal being triaged by seeding a human comment (same one-line `fakeReader{comments: ...}` pattern).

- [ ] **Step 8: Commit.**
  `cd /Users/szymonri/Documents/tatara/tatara-operator && git add internal/controller/projectscan.go internal/controller/projectscan_brainstorm_gate_test.go internal/controller/projectscan_reap_gate_test.go && git commit -m "feat: gate bot brainstorming proposals on human activity in issueScan"`

---

### Task 4: tatara-helmfile Project values - cadence, staleProposalDays, skillsRef pin, maxTaskTokens

Apply the four value changes to BOTH Project CRs. The `tatara-project` chart
renders `.Values.project.spec` verbatim, so every field lands on the Project CR
with no chart change. Cadence: stretch `issueScan` and `mrScan` from hourly;
`refine` has no own schedule (it fires before each due scan cycle) so it rides the
stretched scan cadence automatically - no separate field. `brainstorm` is out of
scope (not listed in the spec) and stays hourly. Chosen cadence (stated per
Decision authority; squarely inside the spec's 2-4h band): `mrScan` every 2h
(bot-MR CI/merge progression is slightly more time-sensitive, still webhook-backed),
`issueScan` every 4h.

**Files:**
- Modify: `/Users/szymonri/Documents/tatara/tatara-helmfile/values/project-tatara/common.yaml` (agent block ~17-23; cron block ~70-92)
- Modify: `/Users/szymonri/Documents/tatara/tatara-helmfile/values/project-infrastructure/common.yaml` (agent block ~17-23; cron block ~56-78)

**Interfaces:** none (YAML values consumed by the operator: `agent.skillsRef` -> `AgentSpec.SkillsRef` -> `pod.go:648`; `agent.maxTaskTokens` -> `AgentSpec.MaxTaskTokens` (Task 2); `scm.cron.brainstorm.staleProposalDays` -> `BrainstormActivity.StaleProposalDays` (`projectscan.go:2219`); `scm.cron.{issueScan,mrScan}.schedule`).

The pinned skills SHA is the current `tatara-agent-skills` HEAD:
`ae4250fd567b6dd0af439aba364460b1ce72d737`
(`ae4250f feat: add push-CD release workflow tagging semver and bumping wrapper skills ref`).
Re-verify HEAD at execution time: `cd /Users/szymonri/Documents/tatara/tatara-agent-skills && git rev-parse HEAD`; use whatever HEAD prints (the pin must be a real commit).

Steps:

- [ ] **Step 1: Edit `values/project-tatara/common.yaml`.**
  In the `agent:` block, add `skillsRef` and `maxTaskTokens` (alpha-ordered among the existing scalar keys):
  ```yaml
      agent:
        effort: xhigh
        image: harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper:8f3d880
        # Per-Task cumulative output-token runaway backstop for the turn-uncapped
        # implement/issueLifecycle kinds. TUNE from the component-6 per-kind token
        # telemetry; this is a safety ceiling, not a cost lever.
        maxTaskTokens: 3000000
        maxTurnsPerTask: 100
        model: claude-opus-4-8
        permissionMode: bypassPermissions
        # Pin the agent-skills install to a SHA (kills the `main` drift; component 5
        # / component 4b). Bump deliberately alongside a skills change.
        skillsRef: ae4250fd567b6dd0af439aba364460b1ce72d737
        turnTimeoutSeconds: 2700
  ```
  In the `cron:` block, change the two scan schedules and add `staleProposalDays` under `brainstorm`:
  ```yaml
        cron:
          brainstorm:
            enabled: true
            maxOpenProposals: 10
            schedule: 0 * * * *
            # Activate the stale-proposal reaper (operator #199): auto-close bot
            # proposals with no human engagement after 14 days.
            staleProposalDays: 14
            sources:
              - docs
              - memory
              - internet
          issueScan:
            maxPerRepo: 1
            # Stretched hourly -> every 4h (token conservation); webhooks cover real
            # issue activity, so the backstop scan can be less frequent.
            schedule: 0 */4 * * *
          mrScan:
            maxPerRepo: 1
            # Stretched hourly -> every 2h (token conservation); webhooks cover MR
            # events, this is the CI/merge-progression backstop.
            schedule: 0 */2 * * *
          refine:
            closedLookbackDays: 30
  ```

- [ ] **Step 2: Edit `values/project-infrastructure/common.yaml`.** Apply the identical `agent` additions (`maxTaskTokens: 3000000`, `skillsRef: <same SHA>`) and the identical cron edits (`issueScan.schedule: 0 */4 * * *`, `mrScan.schedule: 0 */2 * * *`, `brainstorm.staleProposalDays: 14`). Keep this project's own `botEmail`/`provider: gitlab`/repo list untouched.

- [ ] **Step 3: Verify templating renders the new fields.** From the helmfile repo, render the tatara-project releases and grep for each new key:
  ```
  cd /Users/szymonri/Documents/tatara/tatara-helmfile && helmfile template 2>/dev/null | grep -E "staleProposalDays|maxTaskTokens|skillsRef|schedule: 0 \*/[24]"
  ```
  Expect lines showing `staleProposalDays: 14`, `maxTaskTokens: 3000000`, `skillsRef: ae4250f...`, `schedule: 0 */4 * * *`, `schedule: 0 */2 * * *` for BOTH `project-tatara` and `project-infrastructure`. If `helmfile template` needs selectors/auth locally, fall back to `helm template` on the `tatara-project` chart with each `common.yaml` as `-f` values, asserting the same keys appear under `spec:`.
  (Do NOT run `helmfile diff`/`apply` locally against the cluster - that is the deploy pipeline's job in Task 6.)

- [ ] **Step 4: Commit (do NOT push yet; the push+MR is bundled with the chart/image bump in Task 6).**
  `cd /Users/szymonri/Documents/tatara/tatara-helmfile && git add values/project-tatara/common.yaml values/project-infrastructure/common.yaml && git commit -m "feat: stretch scan cadence, activate reaper, pin skillsRef, token backstop"`

---

### Task 5: Regenerate operator CRD + deepcopy artifacts for the new AgentSpec field

Task 2 added `AgentSpec.MaxTaskTokens`; the generated CRD manifests
(`charts/tatara-operator/crd-bases/tatara.dev_projects.yaml` and the mirror under
`config/crd/`) and deepcopy must be regenerated so the deployed CRD accepts the
new field (the operator rejects unknown Project spec fields - see the
DisallowUnknownFields memory). Run AFTER Tasks 1-3 are merged/rebased onto the
feature branch.

**Files:**
- Modify (generated): `charts/tatara-operator/crd-bases/tatara.dev_projects.yaml`, any `config/crd/**` mirror, `api/v1alpha1/zz_generated.deepcopy.go` (int64 scalar = no functional deepcopy delta, but run `make generate` for consistency).

Steps:

- [ ] **Step 1: Regenerate.** `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- make manifests generate`
  (Makefile targets `manifests:` at line 37 and `generate:` at line 34.)

- [ ] **Step 2: Confirm the field is in the CRD.** `cd /Users/szymonri/Documents/tatara/tatara-operator && grep -n "maxTaskTokens" charts/tatara-operator/crd-bases/tatara.dev_projects.yaml`
  Expect a `maxTaskTokens:` property under `spec.properties.agent.properties` with `type: integer` / `format: int64`.

- [ ] **Step 3: Build + full test to confirm nothing else drifted.** `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go build ./... && mise exec -- go test ./... -count=1`
  Expect: clean build and `ok` for all packages.

- [ ] **Step 4: Lint.** `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- gofmt -l internal/ api/ && mise exec -- golangci-lint run`
  Expect: no files listed by `gofmt -l`, `golangci-lint` clean.

- [ ] **Step 5: Commit the generated artifacts.**
  `cd /Users/szymonri/Documents/tatara/tatara-operator && git add charts/ config/ api/v1alpha1/zz_generated.deepcopy.go && git commit -m "chore: regenerate CRD + deepcopy for maxTaskTokens"`

---

### Task 6: Deploy - merge operator main, then one tatara-helmfile MR (chart+image bump + values)

Per the hard deploy rules. Requesting-code-review + verification-before-completion
gate the merge. This is procedural (no unit test); verification is via the deploy
diff and post-apply metrics.

Steps:

- [ ] **Step 1: Code review + pre-commit before merge.** Invoke `superpowers:requesting-code-review` on the operator branch diff (Tasks 1-3, 5). Fix any critical/high findings. Then, from the operator repo, run the repo's pre-commit/lint gate (`mise exec -- golangci-lint run`, `mise exec -- go test ./... -count=1`).

- [ ] **Step 2: Merge the operator feature branch to `main`** (worktree -> `main` per the branch-flow rule; never deploy from the worktree). Pushing `main` triggers CI to build+push the operator image and chart to harbor. Record the merged operator short SHA as `OP_SHA` (e.g. `git -C /Users/szymonri/Documents/tatara/tatara-operator rev-parse --short main`).

- [ ] **Step 3: Confirm CI published the image+chart.** Wait for the operator repo's CI run on `main` to go green (`gh run list --repo szymonrychu/tatara-operator --branch main --limit 1`). The chart version is `0.0.0-g<OP_SHA>` and the image tag `<OP_SHA>` (per the leading-zero-SHA + dual-pin deploy memories).

- [ ] **Step 4: Build the tatara-helmfile MR.** On a branch in `/Users/szymonri/Documents/tatara/tatara-helmfile`, on top of the Task 4 commit:
  - Bump the `tatara-operator` release chart version to `0.0.0-g<OP_SHA>` and the pinned operator `image.tag` to `<OP_SHA>` in `values/tatara-operator/common.yaml` (BOTH, per the dual-pin memory - chart-only leaves the old image running). Use the `bump-chart-usage` + `bump-container-usage` skills to locate the exact keys.
  - The Task 4 Project values edits ride the same branch/MR.

- [ ] **Step 5: Open the MR; review the sticky helmfile diff.** Push and open the MR (bot auto-posts a `helmfile diff`). Verify the diff shows ONLY: the operator Deployment image `<OP_SHA>` + chart version, and the Project CR field changes (`schedule`, `staleProposalDays: 14`, `skillsRef`, `maxTaskTokens: 3000000`) for both Projects. No unexpected resource churn.

- [ ] **Step 6: Merge the MR; the pipeline auto-applies.** Confirm the apply job is green.

- [ ] **Step 7: Verify live.** After apply:
  - Operator image tag matches `<OP_SHA>`: `kubectl -n tatara get deploy tatara-operator -o jsonpath='{.spec.template.spec.containers[0].image}'`.
  - Project CRs carry the new fields: `kubectl -n tatara get project tatara -o jsonpath='{.spec.agent.maxTaskTokens} {.spec.agent.skillsRef} {.spec.scm.cron.brainstorm.staleProposalDays} {.spec.scm.cron.issueScan.schedule} {.spec.scm.cron.mrScan.schedule}'` (repeat for `project infrastructure`).
  - Reaper active: over the next day watch `operator_task_terminal_total` / `IssueOutcome("stale-close")` and the new `ScanItem("issueScan","skipped_brainstorm_no_human")` counter appear; scan cadence reflects 2h/4h (fewer scan-cycle log lines).
  - Backstop wired (not yet expected to fire on healthy runs): watch for `operator_task_terminal_total{reason="TokenBudgetExceeded"}` staying at 0 on healthy implement/issueLifecycle runs; a non-zero value on a genuinely long healthy run means `maxTaskTokens=3000000` is too low - re-tune upward via a values-only MR.

- [ ] **Step 8: Record the outcome** in `tatara-operator/MEMORY.md` and `tatara-helmfile/MEMORY.md` (one dated line each): the four adjacents shipped, the pinned skills SHA, and that `maxTaskTokens=3000000` is a TUNE-from-telemetry placeholder. Move the component-5 item out of `ROADMAP.md`.
