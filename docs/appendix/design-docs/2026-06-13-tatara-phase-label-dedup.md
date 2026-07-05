# tatara phase-label dedup + orphan-recovery (Option A) - implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development
> (sonnet implementers, opus review) to implement task-by-task. Steps use
> checkbox (`- [ ]`) syntax. TDD throughout: failing test first, watch it fail,
> minimal code, watch it pass, commit.

**Goal:** Make the issue's phase label the lifecycle state-of-truth and kill
duplicate-assignment + orphaned-MR bugs, using label PRESENCE + Task STATE
(NOT label-added-time - see the spec AMENDMENT 2026-06-13).

**Architecture:** tatara-operator (Go, controller-runtime). Four operator-managed
phase labels (`tatara-brainstorming`/`-approved`/`-implementation`/`-declined`),
exactly one current per issue. Dedup keys on label presence + Task state; an
orphan (active phase label, no live Task) is recovered by a backstop pass that
starts the correct lifecycle entry. Legacy `tatara-idea`/`tatara-rejected` are
recognized as aliases and lazily migrated by the egress (no migration job).

**Tech Stack:** Go 1.26, controller-runtime/kubebuilder, envtest, testify-free
table tests (`t.Run`).

**Spec:** `docs/superpowers/specs/2026-06-13-tatara-phase-label-dedup-design.md`
(read its AMENDMENT first; the label-added-time mechanism + `IssueLabelAddedAt`
are WITHDRAWN).

---

## Pre-flight (the worktree is already created off fresh main; verify baseline)

```bash
export KUBEBUILDER_ASSETS="$(go run sigs.k8s.io/controller-runtime/tools/setup-envtest@release-0.21 use 1.33.0 -p path)"
go build ./... && gofmt -l . && go vet ./... && go test ./internal/controller/... ./internal/scm/... ./internal/webhook/...
```
Expected: green. EVERY task: set `KUBEBUILDER_ASSETS` (above) before `go test`.
EVERY task ends with `gofmt -l .` clean (lint does NOT gofmt-check here, per MEMORY),
`go vet ./...`, and a conventional commit.

Files touched overall:
- `api/v1alpha1/project_types.go` (T1: new ScmSpec label fields)
- `api/v1alpha1/annotations.go` (T5: shared turn-annotation consts)
- `internal/controller/labels.go` (T1: label helpers + setLifecycleLabel)
- `internal/controller/lifecycle.go` (T1 caller fix, T2 state-entry labels)
- `internal/controller/projectscan.go` (T3 dedup, T4 backstop)
- `internal/controller/task_controller.go` (T5: alias consts to shared)
- `internal/webhook/server.go` (T5: reactivate owning Parked task)
- `charts/tatara-operator/crds/*` (T1: `make manifests`)
- matching `*_test.go` per task.

Tasks are SEQUENTIAL (shared files projectscan.go / lifecycle.go). Order:
T1 -> T2 -> T3 -> T4 -> T5 -> T6.

---

### Task 1: Four phase-label helpers + ScmSpec fields + CRD

**Files:**
- Modify: `api/v1alpha1/project_types.go` (ScmSpec)
- Modify: `internal/controller/labels.go` (`lifecycleLabels`, new helpers, `setLifecycleLabel`)
- Modify callers: `internal/controller/lifecycle.go` (finishTriage), `internal/controller/projectscan.go` (brainstorm, proposalBacklog)
- Test: `internal/controller/labels_test.go`
- Regen: `charts/tatara-operator/crds/` via `make manifests`

- [ ] **Step 1: Failing tests** in `labels_test.go`:

```go
func TestLifecycleLabelsFourDefaults(t *testing.T) {
	b, a, i, d := lifecycleLabels(nil)
	if b != "tatara-brainstorming" || a != "tatara-approved" || i != "tatara-implementation" || d != "tatara-declined" {
		t.Fatalf("defaults: got %q %q %q %q", b, a, i, d)
	}
	s := &tatarav1alpha1.ScmSpec{BrainstormingLabel: "bs", ApprovedLabel: "ap", ImplementationLabel: "im", DeclinedLabel: "dc"}
	b, a, i, d = lifecycleLabels(s)
	if b != "bs" || a != "ap" || i != "im" || d != "dc" {
		t.Fatalf("overrides: got %q %q %q %q", b, a, i, d)
	}
}

func TestManagedAndActivePhaseLabelsIncludeLegacy(t *testing.T) {
	managed := managedPhaseLabels(nil)
	for _, want := range []string{"tatara-brainstorming", "tatara-approved", "tatara-implementation", "tatara-declined", "tatara-idea", "tatara-rejected"} {
		if !contains(managed, want) {
			t.Fatalf("managedPhaseLabels missing %q: %v", want, managed)
		}
	}
	active := activePhaseLabels(nil)
	for _, want := range []string{"tatara-brainstorming", "tatara-approved", "tatara-implementation", "tatara-idea"} {
		if !contains(active, want) {
			t.Fatalf("activePhaseLabels missing %q: %v", want, active)
		}
	}
	if contains(active, "tatara-declined") || contains(active, "tatara-rejected") {
		t.Fatalf("activePhaseLabels must NOT include terminal labels: %v", active)
	}
}
```
(`contains` helper already exists in the controller test package - repository_controller_test.go. If the linker complains it is unused elsewhere, reuse it; do not redeclare.)

- [ ] **Step 2: Run, watch fail** (`lifecycleLabels` returns 3 values -> compile error / wrong count). `go test ./internal/controller/ -run 'TestLifecycleLabelsFourDefaults|TestManagedAndActivePhaseLabelsIncludeLegacy'`

- [ ] **Step 3: ScmSpec fields** in `project_types.go` - after the existing `ApprovedLabel`/`RejectedLabel` block, keeping `IdeaLabel`/`RejectedLabel` as deprecated legacy aliases and ADDING:

```go
	// BrainstormingLabel marks an issue tatara is triaging / discussing (pre-approval).
	// +kubebuilder:default="tatara-brainstorming"
	// +optional
	BrainstormingLabel string `json:"brainstormingLabel,omitempty"`
	// ImplementationLabel marks an issue whose implementation is in flight.
	// +kubebuilder:default="tatara-implementation"
	// +optional
	ImplementationLabel string `json:"implementationLabel,omitempty"`
	// DeclinedLabel marks an issue declined before implementation (triage reject).
	// +kubebuilder:default="tatara-declined"
	// +optional
	DeclinedLabel string `json:"declinedLabel,omitempty"`
```
Update the `IdeaLabel` doc comment to: `// IdeaLabel is DEPRECATED (legacy alias for BrainstormingLabel); kept for lazy migration.` and `RejectedLabel` to `// RejectedLabel is DEPRECATED (legacy alias for DeclinedLabel); kept for lazy migration.` Keep their `+kubebuilder:default` values (`tatara-idea`/`tatara-rejected`).

- [ ] **Step 4: Rewrite `lifecycleLabels` + add helpers** in `labels.go`:

```go
// lifecycleLabels returns the four managed phase labels (brainstorming/approved/
// implementation/declined), applying defaults when a field is empty.
func lifecycleLabels(s *tatarav1alpha1.ScmSpec) (brainstorming, approved, implementation, declined string) {
	brainstorming, approved, implementation, declined =
		"tatara-brainstorming", "tatara-approved", "tatara-implementation", "tatara-declined"
	if s == nil {
		return
	}
	if s.BrainstormingLabel != "" {
		brainstorming = s.BrainstormingLabel
	}
	if s.ApprovedLabel != "" {
		approved = s.ApprovedLabel
	}
	if s.ImplementationLabel != "" {
		implementation = s.ImplementationLabel
	}
	if s.DeclinedLabel != "" {
		declined = s.DeclinedLabel
	}
	return
}

// legacyLabels returns the deprecated idea/rejected labels (lazy migration).
func legacyLabels(s *tatarav1alpha1.ScmSpec) (idea, rejected string) {
	idea, rejected = "tatara-idea", "tatara-rejected"
	if s == nil {
		return
	}
	if s.IdeaLabel != "" {
		idea = s.IdeaLabel
	}
	if s.RejectedLabel != "" {
		rejected = s.RejectedLabel
	}
	return
}

// managedPhaseLabels returns every label the operator owns (new + legacy), so
// setLifecycleLabel removes all-but-desired and dedup recognizes legacy issues.
func managedPhaseLabels(s *tatarav1alpha1.ScmSpec) []string {
	b, a, i, d := lifecycleLabels(s)
	idea, rej := legacyLabels(s)
	return []string{b, a, i, d, idea, rej}
}

// activePhaseLabels returns the labels meaning "in flight" (brainstorming,
// approved, implementation, + legacy idea). An OPEN issue bearing any of these
// with only-terminal Tasks is an orphan the backstop resumes.
func activePhaseLabels(s *tatarav1alpha1.ScmSpec) []string {
	b, a, i, _ := lifecycleLabels(s)
	idea, _ := legacyLabels(s)
	return []string{b, a, i, idea}
}
```

- [ ] **Step 5: `setLifecycleLabel` uses `managedPhaseLabels`** - replace these two lines in `setLifecycleLabel`:
```go
	idea, approved, rejected := lifecycleLabels(proj.Spec.Scm)
	managed := []string{idea, approved, rejected}
```
with:
```go
	managed := managedPhaseLabels(proj.Spec.Scm)
```
(the rest of setLifecycleLabel is unchanged; it adds `desired`, removes the rest of `managed`.)

- [ ] **Step 6: Fix callers (build-green within this task).**
  - `lifecycle.go` `finishTriage`: change `idea, approved, rejected := lifecycleLabels(project.Spec.Scm)` to `brainstorming, approved, _, declined := lifecycleLabels(project.Spec.Scm)`. Replace every `idea` use with `brainstorming` and every `rejected` use with `declined` in that function (the close-withheld arm `setLifecycleLabel(..., idea)` -> `brainstorming`; discuss arm `idea` -> `brainstorming`; await-approval arm `idea` -> `brainstorming`; close arm `rejected` -> `declined`; implement arm `approved` unchanged).
  - `projectscan.go` `brainstorm`: change `ideaLabel, _, _ := lifecycleLabels(proj.Spec.Scm)` to `ideaLabel, _, _, _ := lifecycleLabels(proj.Spec.Scm)` (rename local to `brainstormingLabel` for clarity; it is the label `proposalBacklog` counts). Pass it through unchanged.
  - `proposalBacklog`: it counts open non-PR issues bearing `ideaLabel`. Keep counting the brainstorming label; ALSO count the legacy idea label so backpressure is correct during migration: change the predicate to `hasLabel(iss.Labels, ideaLabel) || hasLabel(iss.Labels, legacyIdea)` where `legacyIdea, _ := legacyLabels(...)`. (Pass legacyIdea in, or compute inside.)
  - Search the whole tree for other `lifecycleLabels(` call sites and fix arity: `grep -rn 'lifecycleLabels(' internal/`.

- [ ] **Step 7: `make manifests`** to regen the CRD with the three new fields. Confirm `charts/tatara-operator/crds/tatara.dev_projects.yaml` gains `brainstormingLabel`/`implementationLabel`/`declinedLabel`.

- [ ] **Step 8: Run tests + gates.** `go test ./internal/controller/...`, `gofmt -l .`, `go vet ./...`. All green.

- [ ] **Step 9: Commit.** `feat(labels): four managed phase labels + legacy-alias helpers`

---

### Task 2: Apply phase labels at lifecycle state entry

**Files:**
- Modify: `internal/controller/lifecycle.go`
- Test: `internal/controller/lifecycle_label_test.go` (exists) or add cases there

State-entry contract (issue sources only; skip when `task.Spec.Source == nil || task.Spec.Source.IsPR`):
- Triage entry -> `tatara-brainstorming`
- Implement entry -> `tatara-implementation`
- (approved on finishTriage implement, declined on close: ALREADY set by Task 1's finishTriage.)

- [ ] **Step 1: Failing tests** in `lifecycle_label_test.go` using the existing `labelWriter`/`reconcilerFor`/`seedLabelTask` fakes (see existing tests in that file for the exact harness):

```go
func TestTriageEntrySetsBrainstormingLabel(t *testing.T) {
	// seed an issueLifecycle Task with LifecycleState "" (fresh) and an issue Source.
	// reconcile once; assert the labelWriter recorded AddLabel("tatara-brainstorming")
	// and that no other managed label was added.
}

func TestImplementEntrySetsImplementationLabel(t *testing.T) {
	// seed a Task at LifecycleState "Implement", Phase "" (fresh spawn), issue Source,
	// memory Ready, under cap. reconcile; assert AddLabel("tatara-implementation").
}

func TestPRSourceTaskSkipsPhaseLabel(t *testing.T) {
	// seed an Implement-entry Task whose Source.IsPR == true.
	// reconcile; assert NO AddLabel call was made for a phase label.
}
```
Model them on the existing finishTriage label tests in the same file (copy the harness setup; do not invent new fakes).

- [ ] **Step 2: Run, watch fail.**

- [ ] **Step 3: Brainstorming on Triage entry.** In `reconcileLifecycle`, the `case ""` block sets the initial state then requeues. Add the label there (it already has `project` loaded). After `r.setLifecycleState(ctx, task, entry, "initial")` succeeds and when `entry == "Triage"`, call a new helper:
```go
	if entry == "Triage" {
		if err := r.ensurePhaseLabel(ctx, &project, task, "brainstorming"); err != nil {
			r.Metrics.ReconcileResult("Task", "error")
			return ctrl.Result{}, err
		}
	}
```
Also cover tasks that ENTER Triage via reactivation (LifecycleState already "Triage" on first handleTriage). Simplest robust placement: at the TOP of `handleTriage`, before driving the agent (i.e. when `!isTerminal(task.Status.Phase)`), call `ensurePhaseLabel(ctx, project, task, "brainstorming")` (idempotent). Put it just before `r.buildTriagePromptFor`.

- [ ] **Step 4: Implementation on Implement entry.** In `handleImplement`, inside the fresh-spawn branch (`task.Status.Phase == ""`), AFTER the iteration increment + re-read and BEFORE building the prompt, call `r.ensurePhaseLabel(ctx, project, task, "implementation")` (idempotent; only fires on Implement spawn, not every poll).

- [ ] **Step 5: Add `ensurePhaseLabel` helper** in `lifecycle.go`:
```go
// ensurePhaseLabel sets the desired managed phase label on the task's source
// ISSUE (no-op for PR sources or missing source). which is one of
// "brainstorming"|"approved"|"implementation"|"declined"; it resolves the
// configured label name and delegates to setLifecycleLabel (idempotent).
func (r *TaskReconciler) ensurePhaseLabel(ctx context.Context, project *tatarav1alpha1.Project, task *tatarav1alpha1.Task, phase string) error {
	if task.Spec.Source == nil || task.Spec.Source.IsPR || task.Spec.Source.IssueRef == "" {
		return nil
	}
	brainstorming, approved, implementation, declined := lifecycleLabels(project.Spec.Scm)
	var desired string
	switch phase {
	case "brainstorming":
		desired = brainstorming
	case "approved":
		desired = approved
	case "implementation":
		desired = implementation
	case "declined":
		desired = declined
	default:
		return nil
	}
	return r.setLifecycleLabel(ctx, project, task, desired)
}
```
(finishTriage may optionally be refactored to call `ensurePhaseLabel(..., "approved"/"declined"/"brainstorming")` instead of inline `setLifecycleLabel`, for consistency - OPTIONAL; only if it keeps tests green. Do not change finishTriage behavior.)

- [ ] **Step 6: Tests + gates green. Commit.** `feat(lifecycle): set phase label on Triage and Implement entry`

---

### Task 3: Dedup by phase-label presence + Task state

**Files:**
- Modify: `internal/controller/projectscan.go` (`isDeduped`, add `hasAnyLabel`, callers `mrScan`/`issueScan`)
- Test: `internal/controller/projectscan_dedup_test.go`

- [ ] **Step 1: Failing tests** in `projectscan_dedup_test.go` (match the existing test style there):

```go
func TestDedupTerminalTaskWithActiveLabelIsDeduped(t *testing.T) {
	// candidate: issue repo "o/r" #5, labels ["tatara-implementation"], updatedAt now.
	// existing: ONE terminal task for o/r#5 (Phase "Succeeded" or LifecycleState "Parked"),
	//           CreationTimestamp BEFORE updatedAt.
	// managed := managedPhaseLabels(nil)
	// isDeduped(c, existing, managed) == true  (orphan; backstop handles; no fresh triage)
}

func TestDedupTerminalTaskNoLabelNewActivityEligible(t *testing.T) {
	// candidate: issue o/r#5, labels [] (no managed label), updatedAt AFTER task creation.
	// existing: one terminal task for o/r#5 created BEFORE updatedAt.
	// isDeduped == false  (legacy/untracked + new activity -> eligible for fresh triage)
}

func TestDedupTerminalTaskNoLabelNoNewActivityDeduped(t *testing.T) {
	// same as above but updatedAt == task creation (not after) -> isDeduped == true
}

func TestDedupNonTerminalTaskAlwaysDeduped(t *testing.T) {
	// existing: a non-terminal task (Phase "Planning") for o/r#5, any labels.
	// isDeduped == true (fast path unchanged)
}

func TestDedupDeclinedLabelIsDeduped(t *testing.T) {
	// candidate labels ["tatara-declined"], one terminal task. managed includes declined.
	// isDeduped == true (declined is in managedPhaseLabels -> suppressed; no scan action)
}

func TestDedupPRHeadShaUnchanged(t *testing.T) {
	// PR candidate (isPR true) + terminal task at same headSHA -> deduped (arm unchanged).
}
```

- [ ] **Step 2: Run, watch fail** (signature mismatch: `isDeduped` takes 2 args today).

- [ ] **Step 3: Add `hasAnyLabel`** in `projectscan.go` near `hasLabel`:
```go
func hasAnyLabel(labels, want []string) bool {
	for _, w := range want {
		if hasLabel(labels, w) {
			return true
		}
	}
	return false
}
```

- [ ] **Step 4: Rewrite `isDeduped`** - add `managed []string` param; replace ONLY the issue arm. Final function:
```go
// isDeduped reports whether a candidate already has a Task that should suppress
// a re-pick. Phase labels are the issue's state-of-truth (Option A):
//   - any non-terminal Task for (repo,number) -> skip (fast path)
//   - PR: a terminal Task at the same head-sha -> skip
//   - issue: a managed phase label present on the OPEN issue -> skip (active =>
//     handled by the live Task above; terminal+label => orphan the backstop
//     resumes; declined => no action). No managed label -> legacy/untracked, fall
//     back to activity-vs-creation so a stale terminal Task is not re-triaged
//     unless the issue saw new activity.
func isDeduped(c candidate, existing []tatarav1alpha1.Task, managed []string) bool {
	repoLabel := sanitizeRepoLabel(c.repo)
	numLabel := strconv.Itoa(c.number)
	for i := range existing {
		t := &existing[i]
		if t.Labels[labelSourceRepo] != repoLabel || t.Labels[labelSourceNumber] != numLabel {
			continue
		}
		lifecycleTerminal := t.Status.LifecycleState != "" && isLifecycleTerminal(t.Status.LifecycleState)
		if !isTerminal(t.Status.Phase) && !lifecycleTerminal {
			return true
		}
		if c.isPR {
			if t.Labels[labelHeadSHA] == c.headSHA && c.headSHA != "" {
				return true
			}
			continue
		}
		// issue: phase label is state-of-truth.
		if hasAnyLabel(c.labels, managed) {
			return true
		}
		if !c.updatedAt.After(t.CreationTimestamp.Time) {
			return true
		}
	}
	return false
}
```

- [ ] **Step 5: Fix callers.** In `mrScan` and `issueScan`, compute `managed := managedPhaseLabels(proj.Spec.Scm)` once before the dedup loop and pass it: `isDeduped(c, existing, managed)`. (mrScan candidates are PRs so `managed` is unused there, but pass for signature consistency.) Also fix any other `isDeduped(` call site (`grep -rn 'isDeduped(' internal/`).

- [ ] **Step 6: Tests + gates green. Commit.** `fix(projectscan): dedup issues by phase-label presence, not updatedAt`

---

### Task 4: Backstop - recover orphaned issues

**Files:**
- Modify: `internal/controller/projectscan.go` (add `recoverOrphans`, `hasNonTerminalTaskForIssue`; wire into `runScans`)
- Test: `internal/controller/projectscan_run_test.go` (or a new `projectscan_backstop_test.go`)

Orphan = OPEN issue with an active phase label and NO non-terminal Task for it.
Recovery entry by label priority (implementation > approved > brainstorming/idea):
- `tatara-implementation` (or no live MR) -> entry `Implement` (resume coding; an
  open bot MR is instead recovered by mrScan's MRCI task, which makes the issue
  non-orphan in the fresh re-list, so this only fires when no live MR exists)
- `tatara-approved` -> entry `Implement`
- `tatara-brainstorming` / legacy `tatara-idea` -> entry `Triage`
- `tatara-declined` -> no action

- [ ] **Step 1: Failing tests** (use the ProjectReconciler test harness in projectscan_*_test.go; fake SCMReader returning issues with labels; fake client with existing tasks):

```go
func TestBackstopRecoversImplementationOrphanToImplement(t *testing.T) {
	// reader: open issue o/r#7 labels ["tatara-implementation"], no PRs.
	// existing tasks: NONE for o/r#7.
	// budget := 3
	// recoverOrphans(...); assert ONE issueLifecycle Task created for o/r#7 with
	// LifecycleEntryAnnotation == "Implement"; budget decremented to 2.
}
func TestBackstopRecoversBrainstormingOrphanToTriage(t *testing.T) {
	// issue labels ["tatara-brainstorming"], no tasks -> Task with entry "Triage".
}
func TestBackstopRecoversLegacyIdeaOrphanToTriage(t *testing.T) {
	// issue labels ["tatara-idea"], no tasks -> entry "Triage".
}
func TestBackstopSkipsIssueWithLiveTask(t *testing.T) {
	// issue labels ["tatara-implementation"], existing NON-terminal task for o/r#7
	// (e.g. an mrScan MRCI task) -> recoverOrphans creates NOTHING.
}
func TestBackstopSkipsDeclined(t *testing.T) {
	// issue labels ["tatara-declined"], no tasks -> creates NOTHING.
}
func TestBackstopRespectsBudgetZero(t *testing.T) {
	// budget := 0 -> creates NOTHING regardless of orphans.
}
```

- [ ] **Step 2: Run, watch fail.**

- [ ] **Step 3: Add helpers + `recoverOrphans`** in `projectscan.go`:
```go
// hasNonTerminalTaskForIssue reports whether any open (non-terminal) Task exists
// for (slug, number) in the snapshot.
func hasNonTerminalTaskForIssue(existing []tatarav1alpha1.Task, slug string, number int) bool {
	repoLabel := sanitizeRepoLabel(slug)
	numLabel := strconv.Itoa(number)
	for i := range existing {
		t := &existing[i]
		if t.Labels[labelSourceRepo] != repoLabel || t.Labels[labelSourceNumber] != numLabel {
			continue
		}
		if taskOpen(t) {
			return true
		}
	}
	return false
}

// recoverOrphans starts the correct lifecycle Task for each OPEN issue that
// carries an active phase label but has no live Task (a missed/never-started or
// stalled handler). It RE-LISTS existing Tasks so it sees Tasks mrScan/issueScan
// created earlier this cycle (an open bot MR becomes a live MRCI Task -> not an
// orphan). Bounded by the shared open-task budget.
func (r *ProjectReconciler) recoverOrphans(ctx context.Context, proj *tatarav1alpha1.Project, reader scm.SCMReader, repos []tatarav1alpha1.Repository, budget *int) {
	if *budget <= 0 {
		return
	}
	l := log.FromContext(ctx)
	existing, err := r.existingScanTasks(ctx, proj)
	if err != nil {
		l.Error(err, "backstop: list tasks", "action", "backstop_list_error", "resource_id", proj.Name)
		return
	}
	brainstorming, approved, implementation, _ := lifecycleLabels(proj.Spec.Scm)
	legacyIdea, _ := legacyLabels(proj.Spec.Scm)
	for i := range repos {
		owner, name, oerr := scm.OwnerRepo(repos[i].Spec.URL)
		if oerr != nil {
			continue
		}
		issues, lerr := reader.ListOpenIssues(ctx, owner, name)
		if lerr != nil {
			l.Error(lerr, "backstop: ListOpenIssues", "action", "backstop_list_error", "resource_id", proj.Name, "repo", repos[i].Name)
			continue
		}
		slug := owner + "/" + name
		for _, iss := range issues {
			if iss.IsPR {
				continue
			}
			var entry, goal string
			switch {
			case hasLabel(iss.Labels, implementation):
				entry = "Implement"
				goal = fmt.Sprintf("Resume implementation for %s#%d (phase label present, no live task)", slug, iss.Number)
			case hasLabel(iss.Labels, approved):
				entry = "Implement"
				goal = fmt.Sprintf("Implement approved issue %s#%d", slug, iss.Number)
			case hasLabel(iss.Labels, brainstorming) || hasLabel(iss.Labels, legacyIdea):
				entry = "Triage"
				goal = fmt.Sprintf("Triage issue %s#%d", slug, iss.Number)
			default:
				continue
			}
			if hasNonTerminalTaskForIssue(existing, slug, iss.Number) {
				continue
			}
			if *budget <= 0 {
				return
			}
			repo, ok := r.matchRepoForSlug(repos, slug)
			if !ok {
				continue
			}
			cand := candidate{repo: slug, number: iss.Number, labels: iss.Labels, updatedAt: iss.UpdatedAt}
			ann := map[string]string{tatarav1alpha1.LifecycleEntryAnnotation: entry}
			if _, cerr := r.createScanTask(ctx, proj, &repo, cand, cand, "backstop", "issueLifecycle", goal, ann); cerr != nil {
				l.Error(cerr, "backstop: create recovery task", "action", "backstop_create_error", "resource_id", proj.Name, "repo", repo.Name)
				continue
			}
			l.Info("backstop: recovered orphaned issue", "action", "backstop_recover",
				"resource_id", proj.Name, "issue", fmt.Sprintf("%s#%d", slug, iss.Number), "entry", entry)
			r.Metrics.ScanItem("backstop", "recovered")
			*budget--
		}
	}
}
```

- [ ] **Step 4: Wire into `runScans`.** Inside the `issueScan` due-block, AFTER `r.stampScan(ctx, proj, "issueScan")` and the `consider(...)` calls, add:
```go
				if budget > 0 {
					r.recoverOrphans(ctx, proj, reader, repos, &budget)
				}
```
(Piggybacks the hourly issue cadence; runs only when issueScan is due. `budget` is the same shared int already declared in runScans.)

- [ ] **Step 5: Tests + gates green. Commit.** `feat(projectscan): backstop recovers orphaned phase-labelled issues`

---

### Task 5: Webhook twin-gap - reactivate owning Parked task instead of duplicating

**Files:**
- Modify: `api/v1alpha1/annotations.go` (export turn-annotation consts)
- Modify: `internal/controller/task_controller.go` (alias local consts to the exported ones)
- Modify: `internal/webhook/server.go` (`handleIssueComment`, add `findReactivatableTask` + reactivation)
- Test: `internal/webhook/issue_comment_test.go` (or issue_comment_create_test.go)

A human comment on an issue whose only owning Task is **Parked** must RESUME that
Task (clean agent-run reset -> Triage), not spawn a duplicate. Done tasks and
no-owning-task keep the existing "create fresh" behavior.

- [ ] **Step 1: Export shared consts** in `api/v1alpha1/annotations.go`:
```go
// Turn-loop annotation keys, shared by the controller (agent-run state) and the
// webhook (reactivation must clear them so a fresh run starts clean).
const (
	AnnCurrentTurn    = "tatara.dev/current-turn"
	AnnCurrentSubtask = "tatara.dev/current-subtask"
	AnnTurnComplete   = "tatara.dev/turn-complete"
	AnnTurnStartedAt  = "tatara.dev/turn-started-at"
	AnnPodRecreations = "tatara.dev/pod-recreations"
)
```
In `task_controller.go`, change the local const block to alias them (keep the local lowercase names so the rest of the package is untouched):
```go
	annCurrentTurn           = tatarav1alpha1.AnnCurrentTurn
	annCurrentSubtask        = tatarav1alpha1.AnnCurrentSubtask
	annTurnComplete          = tatarav1alpha1.AnnTurnComplete
	annPodRecreations        = tatarav1alpha1.AnnPodRecreations
	annTurnStartedAt         = tatarav1alpha1.AnnTurnStartedAt
	annPendingHandoverResume = "tatara.dev/pending-handover-resume"
	annAgentUnreachableSince = "tatara.dev/agent-unreachable-since"
```
(`const` with a value from another package is legal in Go only for untyped string constants - these are. Confirm `go build` passes; if the linter objects to mixing, leave annPendingHandoverResume/annAgentUnreachableSince as-is, only the five turn consts need sharing.)

- [ ] **Step 2: Failing tests** in `issue_comment_test.go` (use the existing webhook test harness - fake client, signed payload helpers already present in the package):

```go
func TestIssueCommentReactivatesParkedOwningTask(t *testing.T) {
	// existing: an issueLifecycle Task for o/r#9, LifecycleState "Parked",
	//           Phase "Planning", with annCurrentTurn set and a wrapper pod present.
	// POST a human issue_comment (ActorLogin != bot) for o/r#9.
	// assert: NO new Task created (task count stays 1); the Parked Task now has
	//         LifecycleState "Triage", Phase "", annCurrentTurn cleared.
}
func TestIssueCommentDoneOwningTaskCreatesFresh(t *testing.T) {
	// existing: a Done issueLifecycle Task for o/r#9.
	// human comment -> a NEW Triage Task is created (existing behavior; merged work
	// stays Done, new comment = new episode).
}
func TestIssueCommentNoOwningTaskCreatesFresh(t *testing.T) {
	// no task -> createLifecycleTaskAtTriage (unchanged).
}
```

- [ ] **Step 3: Run, watch fail.**

- [ ] **Step 4: `findReactivatableTask`** in `server.go`:
```go
// findReactivatableTask returns an owning issueLifecycle Task for issueRef that
// went terminal but is resumable (LifecycleState == "Parked"). Done tasks are
// NOT reactivated (their work is complete). Returns (task, true) when found.
func (s *Server) findReactivatableTask(ctx context.Context, projectName, issueRef string) (*tatarav1.Task, bool) {
	var tasks tatarav1.TaskList
	if err := s.cfg.Client.List(ctx, &tasks, client.InNamespace(s.cfg.Namespace)); err != nil {
		return nil, false
	}
	for i := range tasks.Items {
		t := &tasks.Items[i]
		if t.Spec.Kind != "issueLifecycle" || t.Spec.ProjectRef != projectName {
			continue
		}
		if t.Spec.Source == nil || t.Spec.Source.IssueRef != issueRef {
			continue
		}
		if t.Status.LifecycleState == "Parked" {
			return t, true
		}
	}
	return nil, false
}
```

- [ ] **Step 5: Reactivation in `handleIssueComment`.** In the `if !found {` branch, for the `!ev.IsPR` case, BEFORE calling `createLifecycleTaskAtTriage`, try reactivation:
```go
	if !found {
		if !ev.IsPR {
			if parked, ok := s.findReactivatableTask(ctx, proj.Name, ev.IssueRef); ok {
				s.reactivateTask(ctx, w, provider, proj, ev, parked)
				return
			}
			s.createLifecycleTaskAtTriage(ctx, w, provider, proj, ev)
			return
		}
		...
	}
```
Add `reactivateTask`:
```go
// reactivateTask resumes a Parked owning Task: it clears the agent-run state
// (Phase, turn annotations) and the wrapper pod/service, sets LifecycleState back
// to Triage, and stamps LastActivityAt/DeadlineAt so the reconciler re-triages
// the issue with the new comment. Mirrors the controller's resetAgentRun so the
// next reconcile spawns a clean run (no stale turn/pod state).
func (s *Server) reactivateTask(ctx context.Context, w http.ResponseWriter, provider string, proj tatarav1.Project, ev scm.WebhookEvent, task *tatarav1.Task) {
	// Best-effort delete the wrapper pod + service.
	pod := &corev1.Pod{}
	pod.Name = agent.PodName(task)
	pod.Namespace = s.cfg.Namespace
	if err := s.cfg.Client.Delete(ctx, pod); err != nil && !apierrors.IsNotFound(err) {
		s.log.ErrorContext(ctx, "reactivate: delete pod (non-fatal)", "error", err, "task", task.Name)
	}
	svc := &corev1.Service{}
	svc.Name = agent.PodName(task)
	svc.Namespace = s.cfg.Namespace
	if err := s.cfg.Client.Delete(ctx, svc); err != nil && !apierrors.IsNotFound(err) {
		s.log.ErrorContext(ctx, "reactivate: delete service (non-fatal)", "error", err, "task", task.Name)
	}

	idleMinutes := 60
	if proj.Spec.Scm != nil && proj.Spec.Scm.ConversationIdleMinutes > 0 {
		idleMinutes = proj.Spec.Scm.ConversationIdleMinutes
	}
	if updateErr := retry.RetryOnConflict(retry.DefaultRetry, func() error {
		fresh := &tatarav1.Task{}
		if err := s.cfg.Client.Get(ctx, client.ObjectKeyFromObject(task), fresh); err != nil {
			return err
		}
		now := metav1.Now()
		deadline := metav1.NewTime(now.Add(time.Duration(idleMinutes) * time.Minute))
		fresh.Status.LifecycleState = "Triage"
		fresh.Status.Phase = ""
		fresh.Status.LastActivityAt = &now
		fresh.Status.DeadlineAt = &deadline
		if err := s.cfg.Client.Status().Update(ctx, fresh); err != nil {
			return err
		}
		// Clear turn annotations (metadata update, separate from status).
		fresh2 := &tatarav1.Task{}
		if err := s.cfg.Client.Get(ctx, client.ObjectKeyFromObject(task), fresh2); err != nil {
			return err
		}
		if fresh2.Annotations != nil {
			delete(fresh2.Annotations, tatarav1.AnnCurrentTurn)
			delete(fresh2.Annotations, tatarav1.AnnCurrentSubtask)
			delete(fresh2.Annotations, tatarav1.AnnTurnComplete)
			delete(fresh2.Annotations, tatarav1.AnnTurnStartedAt)
			delete(fresh2.Annotations, tatarav1.AnnPodRecreations)
		}
		return s.cfg.Client.Update(ctx, fresh2)
	}); updateErr != nil {
		s.log.ErrorContext(ctx, "reactivate: update task", "error", updateErr, "task", task.Name)
		s.count(provider, ev.Kind, ev.Action, "error")
		http.Error(w, "reactivate task", http.StatusInternalServerError)
		return
	}
	s.log.InfoContext(ctx, "issue_comment: reactivated parked lifecycle task",
		"project", proj.Name, "task", task.Name, "issue_ref", ev.IssueRef)
	s.count(provider, ev.Kind, ev.Action, "accepted")
	w.WriteHeader(http.StatusAccepted)
}
```
Add imports to `server.go`: `"github.com/szymonrychu/tatara-operator/internal/agent"` (for `PodName`). `corev1`, `apierrors`, `metav1`, `retry`, `time` are already imported.

- [ ] **Step 6: Tests + gates green** (`go test ./internal/webhook/... ./internal/controller/...`). Commit. `fix(webhook): reactivate parked lifecycle task on comment, no duplicate`

---

### Task 6: Full verification gate (not TDD)

**Files:** none (verification only).

- [ ] **Step 1: Regenerate + full suite.**
```bash
make manifests
export KUBEBUILDER_ASSETS="$(go run sigs.k8s.io/controller-runtime/tools/setup-envtest@release-0.21 use 1.33.0 -p path)"
go build ./... && gofmt -l . && go vet ./... && go test ./... && golangci-lint run
```
All green; `gofmt -l .` prints nothing; CRD diff shows only the three new label fields.

- [ ] **Step 2: Grep for stragglers.**
```bash
grep -rn 'lifecycleLabels(\|isDeduped(\|tatara-idea\|tatara-rejected' internal/ | grep -v _test
```
Confirm no 3-return `lifecycleLabels`, no 2-arg `isDeduped`, and that `tatara-idea`/`tatara-rejected` appear only in `legacyLabels`/`project_types.go` defaults.

- [ ] **Step 3: Final opus code review** of the whole branch diff vs `main` (superpowers:requesting-code-review). Fix Critical/High. Re-run gates.

---

## Self-review checklist (run after writing, before execution)
- Spec coverage: four labels (T1), state-entry application (T2), presence+state dedup (T3), backstop (T4), webhook twin-gap (T5), lazy legacy migration (T1 managed set + T3 dedup + T4 idea-alias). `IssueLabelAddedAt` correctly NOT implemented (amendment).
- Type consistency: `lifecycleLabels` 4-return used everywhere; `isDeduped(c, existing, managed)` everywhere; `ensurePhaseLabel` phase strings match the switch; shared `AnnCurrentTurn` etc. used in both packages.
- No placeholders.
