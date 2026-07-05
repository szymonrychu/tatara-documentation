# Defect B - Duplicate issueLifecycle Tasks (operator) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the operator from spawning a second `issueLifecycle` Task for an issue that already has one (adopt + re-triage the existing Parked/terminal Task) and stop the operator's own bot comments from re-triggering a rescan.
**Architecture:** Two changes in `internal/controller/projectscan.go`. (B1) A new pure helper `hasLiveOrAdoptableTask` returns the single existing `issueLifecycle` Task for `(repo, number)`; `issueScan` adopts it via an inline status reset to `Triage` (reusing the deterministic pod/branch) instead of calling `createScanTask`. (B3) `isDeduped`'s activity-vs-creation gate gains an injectable human-activity predicate so bot-authored comments (which advance `updatedAt`) no longer free the dedup key; `mrScan` and `issueScan` pass a closure built from the SCM reader + `botLogin`, while all pure unit-test callers pass `nil` to keep today's behavior.
**Tech Stack:** Go (stdlib log/slog, controller-runtime for operator), table-driven tests with t.Run, golangci-lint, gofmt.

## Global Constraints
- Newest stable Go; KISS; no tech-debt; JSON logs (log/slog); business actions logged at INFO with structured fields; metrics for anything that counts/times-out/fails.
- TDD strictly: failing test first, run it red, minimal impl, run green, commit. Conventional commits (feat:/fix:/refactor:/test:). Frequent commits.
- Operator CRD changes: regenerate with controller-gen via the repo's make/mise target (find it); templated CRDs apply on helm upgrade. (NONE in this slice - no API type changes.)
- Run tests via mise (`mise exec -- go test ./...` or `mise run test`); lint via `mise exec -- golangci-lint run`.

---

## Code reality notes (read before starting; the plan trusts code over spec)

These were verified against the live tree and differ from or refine the spec:

1. **`resetAgentRun` / `setLifecycleState` are `TaskReconciler` methods**, not reachable from
   `ProjectReconciler.issueScan` (where adoption must happen). The spec named them as the adoption
   mechanism. The established `ProjectReconciler` pattern for re-entering a task from a scan is the
   **inline `RetryOnConflict` status update** already used by the reactivation pass
   (`projectscan.go:807-835`): set `Status.LifecycleState="Triage"`, clear `Status.Phase`, reset
   `Status.ImplementEmptyRetries=0`, set `LastActivityAt`+`DeadlineAt`. Adoption uses that exact
   pattern. The pod name (`agent.StampPodName` / `BuildPodName`) and branch (`TaskBranch`) are
   derived deterministically from the Task labels/source, so reuse is automatic once we keep the
   same Task object - no field copying needed. The next `TaskReconciler` reconcile of the adopted
   Task will `deleteWrapper` (idempotent) and re-spawn on the Triage state via the normal lifecycle
   path, so we do NOT need to call `resetAgentRun` here.

2. **Comment author + timestamp already exist.** `scm.IssueComment` (`internal/scm/scm.go:132-137`)
   carries `Author` and `CreatedAt`; both GitHub (`github_scan.go:207`) and GitLab
   (`gitlab_scan.go:155`) populate them. `humanCommentAfter(ctx, reader, owner, name, number,
   botLogin, since)` (`projectscan.go:1387-1398`) already returns whether a non-bot comment exists
   after `since`, fail-open on read error. **No SCM read change is needed** - the bot-author data
   is already available, so B3 is purely a wiring change (this contradicts the spec's "if the SCM
   read must change, include that as a task": it does not).

3. **The issue side of B3 is already partly solved.** `issueScan` already gates fresh creation on
   `lastTerminalNoLabelTask` + `humanCommentAfter` (`projectscan.go:854-872`). But the
   `isDeduped` line-182 gate (`!c.updatedAt.After(t.CreationTimestamp.Time)`) is what flips a
   terminal Task from deduped->eligible when the bot's own comment advances `updatedAt`, and
   **`mrScan` (`projectscan.go:656`) has NO secondary human-activity gate** - so for non-PR work
   reached via mrScan, and to make the gate correct at the source rather than relying on a second
   downstream check, B3 fixes the gate inside `isDeduped` itself. The fix is signature-additive (a
   trailing `humanActivity` closure; `nil` preserves the old `updatedAt.After(creation)` behavior),
   so the many pure `isDeduped(...)` unit-test callers stay valid by passing `nil`.

4. **`isDeduped` has exactly 2 production callers** (`projectscan.go:656` in `mrScan`, `:841` in
   `issueScan`) and **~12 test callers** across `projectscan_dedup_test.go`,
   `projectscan_binder_test.go`. The test callers all want the legacy behavior; they pass `nil`.

5. **`TaskTerminal`** (`api/v1alpha1/task_types.go:176-182`) returns true for `Phase==Succeeded|Failed`
   OR `LifecycleState==Done|Stopped|Parked`. `isLifecycleTerminal` (`projectscan.go:34-40`) returns
   true for `Done|Stopped|Parked`. For adoption we want the ONE existing lifecycle Task whether it
   is Parked (the duplicate-storm case), Conversation, Triage, Implement, etc. - i.e. any
   `issueLifecycle` Task for `(repo, number)` that is NOT `Done`/`Stopped` (those are deliberately
   closed; a genuinely new fresh scan with new human activity should still create). See Task 2 for
   the exact adoptable predicate.

---

## Task 1: `hasLiveOrAdoptableTask` helper (B1, pure)

**Interfaces**
- Consumes: `[]tatarav1alpha1.Task`, `slug string`, `number int`; label keys `labelSourceRepo`,
  `labelSourceNumber` (`projectscan.go:83-84`); `sanitizeRepoLabel` (`projectscan.go:91`);
  `strconv.Itoa`.
- Produces: `func hasLiveOrAdoptableTask(existing []tatarav1alpha1.Task, slug string, number int) *tatarav1alpha1.Task`
  - Returns the first matching `issueLifecycle` Task for `(slug, number)` whose `LifecycleState`
    is neither `"Done"` nor `"Stopped"` (i.e. live OR Parked OR Conversation OR a Triage/Implement
    in flight OR an unstarted lifecycle Task). Returns `nil` when no such Task exists.
  - Matches only Tasks stamped `source-kind=issueLifecycle` so a `review` Task on a PR sharing the
    number space is never adopted.

Why exclude `Done`/`Stopped` but include `Parked`: `Done` = deliberately closed (triage-close /
merged), `Stopped` = idle-stopped conversation that the reactivation pass already owns; re-adopting
either would resurrect intentionally-closed work. `Parked` is the false-refusal duplicate-storm
state we explicitly want to re-enter. Everything non-terminal is trivially adoptable.

### Step 1.1 - failing test

- [ ] Append to `internal/controller/projectscan_dedup_test.go`:

```go
func mkLifecycleKindTask(repo string, number int, lifecycleState string) tatarav1alpha1.Task {
	tk := tatarav1alpha1.Task{}
	tk.Labels = scanTaskLabels(candidate{repo: repo, number: number}, "issueScan", "issueLifecycle")
	tk.Status.LifecycleState = lifecycleState
	return tk
}

func TestHasLiveOrAdoptableTask(t *testing.T) {
	cases := []struct {
		name     string
		existing []tatarav1alpha1.Task
		wantName bool // true => a Task is returned (adoptable)
	}{
		{
			name:     "no tasks -> nil",
			existing: nil,
			wantName: false,
		},
		{
			name:     "Parked lifecycle task -> adopt",
			existing: []tatarav1alpha1.Task{mkLifecycleKindTask("o/r", 8, "Parked")},
			wantName: true,
		},
		{
			name:     "Triage (in-flight) -> adopt",
			existing: []tatarav1alpha1.Task{mkLifecycleKindTask("o/r", 8, "Triage")},
			wantName: true,
		},
		{
			name:     "Conversation -> adopt",
			existing: []tatarav1alpha1.Task{mkLifecycleKindTask("o/r", 8, "Conversation")},
			wantName: true,
		},
		{
			name:     "Done -> NOT adoptable",
			existing: []tatarav1alpha1.Task{mkLifecycleKindTask("o/r", 8, "Done")},
			wantName: false,
		},
		{
			name:     "Stopped -> NOT adoptable",
			existing: []tatarav1alpha1.Task{mkLifecycleKindTask("o/r", 8, "Stopped")},
			wantName: false,
		},
		{
			name: "wrong number -> nil",
			existing: []tatarav1alpha1.Task{
				mkLifecycleKindTask("o/r", 9, "Parked"),
			},
			wantName: false,
		},
		{
			name: "review-kind task for same number is ignored",
			existing: []tatarav1alpha1.Task{
				func() tatarav1alpha1.Task {
					tk := tatarav1alpha1.Task{}
					tk.Labels = scanTaskLabels(candidate{repo: "o/r", number: 8}, "mrScan", "review")
					tk.Status.LifecycleState = "Parked"
					return tk
				}(),
			},
			wantName: false,
		},
		{
			name: "Parked preferred over a Done sibling",
			existing: []tatarav1alpha1.Task{
				mkLifecycleKindTask("o/r", 8, "Done"),
				mkLifecycleKindTask("o/r", 8, "Parked"),
			},
			wantName: true,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := hasLiveOrAdoptableTask(tc.existing, "o/r", 8)
			if (got != nil) != tc.wantName {
				t.Fatalf("hasLiveOrAdoptableTask returned=%v, want adoptable=%v", got != nil, tc.wantName)
			}
		})
	}
}
```

- [ ] Run it red:
  `mise exec -- go test ./internal/controller/ -run TestHasLiveOrAdoptableTask -count=1`
  Expected failure: `undefined: hasLiveOrAdoptableTask` (compile error).

### Step 1.2 - minimal impl

- [ ] In `internal/controller/projectscan.go`, add immediately after `hasLiveLifecycleTaskForIssue`
  (ends at line ~1522):

```go
// hasLiveOrAdoptableTask returns the single issueLifecycle Task for (slug, number)
// that should be ADOPTED rather than duplicated: any matching issueLifecycle Task
// whose LifecycleState is neither "Done" nor "Stopped". This covers the in-flight
// states (Triage/Conversation/Implement/MRCI/Merge/MainCI), the unstarted state
// (empty LifecycleState), AND the Parked state that the false-refusal duplicate
// storm produces. Done (deliberately closed) and Stopped (idle, owned by the
// reactivation pass) are excluded so genuinely-finished issues are not resurrected.
// A Parked sibling is preferred over a Done/Stopped one. Returns nil when no
// adoptable Task exists. Pure (snapshot only); caller adopts via an inline status
// reset to Triage, reusing the deterministic pod/branch.
func hasLiveOrAdoptableTask(existing []tatarav1alpha1.Task, slug string, number int) *tatarav1alpha1.Task {
	repoLabel := sanitizeRepoLabel(slug)
	numLabel := strconv.Itoa(number)
	for i := range existing {
		t := &existing[i]
		if t.Labels[labelSourceKind] != "issueLifecycle" {
			continue
		}
		if t.Labels[labelSourceRepo] != repoLabel || t.Labels[labelSourceNumber] != numLabel {
			continue
		}
		switch t.Status.LifecycleState {
		case "Done", "Stopped":
			continue
		}
		return t
	}
	return nil
}
```

- [ ] Run it green:
  `mise exec -- go test ./internal/controller/ -run TestHasLiveOrAdoptableTask -count=1`
  Expected: PASS.

### Step 1.3 - commit

- [ ] `git -C /Users/szymonri/Documents/tatara/tatara-operator add internal/controller/projectscan.go internal/controller/projectscan_dedup_test.go`
- [ ] `git -C /Users/szymonri/Documents/tatara/tatara-operator commit -m "feat: add hasLiveOrAdoptableTask helper for issueLifecycle dedup"`

---

## Task 2: `issueScan` adopts the existing Task instead of creating a duplicate (B1)

This is the structural fix. In the `issueScan` eligible loop, before `createScanTask`, check for an
adoptable Task; if found, reset it to `Triage` inline and skip creation. One Task per issue forever;
the shared pod/branch becomes a feature.

**Interfaces**
- Consumes: `hasLiveOrAdoptableTask` (Task 1); `r.Client` / `r.Status()` (controller-runtime client
  on `ProjectReconciler`); `retry.RetryOnConflict`, `retry.DefaultBackoff` (`k8s.io/client-go/util/retry`,
  already imported); `metav1.Now`, `metav1.NewTime`; `proj.Spec.Scm.ConversationIdleMinutes`;
  `r.Metrics.ScanItem(activity, outcome)` (`internal/obs/operator_metrics.go:390`).
- Produces: a new private method on `*ProjectReconciler`:
  `func (r *ProjectReconciler) adoptLifecycleTask(ctx context.Context, proj *tatarav1alpha1.Project, task *tatarav1alpha1.Task) error`
  and an adoption branch inside `issueScan`'s eligible loop.

### Step 2.1 - failing integration test

- [ ] Append to `internal/controller/projectscan_binder_test.go` (envtest-backed; uses
  `seedScanProject`, `newScanReconciler`, `listScanQEs`, `k8sClient`, `testNS` from the existing
  harness):

```go
// TestIssueScanAdoptsParkedTaskInsteadOfDuplicating asserts the false-refusal fix:
// a Parked issueLifecycle Task for an issue with new human activity is RE-ENTERED to
// Triage (adopted) rather than producing a second Task / QueuedEvent. One Task per issue.
func TestIssueScanAdoptsParkedTaskInsteadOfDuplicating(t *testing.T) {
	ctx := context.Background()
	cron := &tatarav1alpha1.ScmCron{IssueScan: tatarav1alpha1.CronActivity{Schedule: "0 * * * *", MaxPerRepo: 2}}
	proj, repoA := seedScanProject(t, "adopt-parked", cron)

	created := metav1.NewTime(time.Now().Add(-2 * time.Hour))

	// Pre-create a Parked issueLifecycle Task for o/r#8 (the duplicate-storm state).
	pre := &tatarav1alpha1.Task{}
	pre.GenerateName = "scan-"
	pre.Namespace = testNS
	pre.Labels = scanTaskLabels(candidate{repo: "o/r", number: 8}, "issueScan", "issueLifecycle")
	pre.Spec = tatarav1alpha1.TaskSpec{
		ProjectRef: "adopt-parked", RepositoryRef: repoA.Name,
		Goal: "g", Kind: "issueLifecycle",
	}
	if err := k8sClient.Create(ctx, pre); err != nil {
		t.Fatalf("pre-create: %v", err)
	}
	pre.CreationTimestamp = created
	pre.Status.Phase = "Succeeded"
	pre.Status.LifecycleState = "Parked"
	pre.Status.ImplementEmptyRetries = 2
	if err := k8sClient.Status().Update(ctx, pre); err != nil {
		t.Fatalf("pre status: %v", err)
	}

	// Issue updated after the Parked task, with a NEW human comment after creation
	// (so the line-182 human-activity gate lets it through to the adoption branch).
	reader := &fakeReader{
		issues: []scm.IssueRef{
			{Repo: "o/r", Number: 8, UpdatedAt: time.Now()},
		},
		comments: []scm.IssueComment{
			{Author: "szymon", CreatedAt: time.Now()}, // human, after the Parked task creation
		},
	}
	r := newScanReconciler(reader)
	r.Metrics = obs.NewOperatorMetrics(prometheus.NewRegistry())

	r.issueScan(ctx, proj, reader, []tatarav1alpha1.Repository{*repoA},
		[]tatarav1alpha1.Task{*pre}, cron.IssueScan)

	// No new QueuedEvent: the Parked Task was adopted, not duplicated.
	qes := listScanQEs(t, "adopt-parked")
	if len(qes) != 0 {
		t.Fatalf("want 0 QEs (adopted, not duplicated), got %d", len(qes))
	}

	// The existing Task is re-entered to Triage with a clean run state.
	got := &tatarav1alpha1.Task{}
	if err := k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: pre.Name}, got); err != nil {
		t.Fatalf("get adopted task: %v", err)
	}
	if got.Status.LifecycleState != "Triage" {
		t.Fatalf("adopted task LifecycleState = %q, want Triage", got.Status.LifecycleState)
	}
	if got.Status.Phase != "" {
		t.Fatalf("adopted task Phase = %q, want cleared", got.Status.Phase)
	}
	if got.Status.ImplementEmptyRetries != 0 {
		t.Fatalf("adopted task ImplementEmptyRetries = %d, want 0", got.Status.ImplementEmptyRetries)
	}
	if got.Status.LastActivityAt == nil || got.Status.DeadlineAt == nil {
		t.Fatalf("adopted task must stamp LastActivityAt + DeadlineAt")
	}
}
```

Note: `types` is `k8s.io/apimachinery/pkg/apis/meta/v1`'s sibling `k8s.io/apimachinery/pkg/types`;
add it to the test file's imports if not present. The existing file imports `metav1`,
`tatarav1alpha1`, `scm`, `obs`, `prometheus`, `time`, `context`, `testing` - add
`"k8s.io/apimachinery/pkg/types"`.

- [ ] Run it red:
  `mise exec -- go test ./internal/controller/ -run TestIssueScanAdoptsParkedTaskInsteadOfDuplicating -count=1`
  Expected failure: a QE IS created (`want 0 QEs ... got 1`) and the Task stays Parked, because the
  adoption branch does not exist yet.

### Step 2.2 - minimal impl: `adoptLifecycleTask`

- [ ] In `internal/controller/projectscan.go`, add a method near the reactivation logic (after
  `findConvTaskToReactivate`, around line 150, or grouped with `issueScan`):

```go
// adoptLifecycleTask re-enters an existing issueLifecycle Task to Triage in place
// of creating a duplicate. It mirrors the reactivation pass: clear the terminal run
// state (Phase, ImplementEmptyRetries) and re-arm the lifecycle (LifecycleState=Triage,
// LastActivityAt=now, DeadlineAt=now+idle). The Task's pod name and branch are derived
// deterministically from its labels/source, so the next TaskReconciler reconcile reuses
// the same pod/branch. RetryOnConflict handles racing reconcile writes.
func (r *ProjectReconciler) adoptLifecycleTask(ctx context.Context, proj *tatarav1alpha1.Project, task *tatarav1alpha1.Task) error {
	now := metav1.Now()
	idleMinutes := 60
	if proj.Spec.Scm != nil && proj.Spec.Scm.ConversationIdleMinutes > 0 {
		idleMinutes = proj.Spec.Scm.ConversationIdleMinutes
	}
	deadline := metav1.NewTime(now.Add(time.Duration(idleMinutes) * time.Minute))
	return retry.RetryOnConflict(retry.DefaultBackoff, func() error {
		fresh := &tatarav1alpha1.Task{}
		if err := r.Get(ctx, types.NamespacedName{Namespace: task.Namespace, Name: task.Name}, fresh); err != nil {
			return err
		}
		fresh.Status.LifecycleState = "Triage"
		fresh.Status.Phase = ""
		fresh.Status.ImplementEmptyRetries = 0
		fresh.Status.LastActivityAt = &now
		fresh.Status.DeadlineAt = &deadline
		return r.Status().Update(ctx, fresh)
	})
}
```

### Step 2.3 - minimal impl: adoption branch in `issueScan`

- [ ] In `issueScan`'s eligible loop (`projectscan.go:848-884`), insert the adoption check
  immediately AFTER the `matchRepoForSlug` block and BEFORE the `lastTerminalNoLabelTask`
  human-activity gate. The current sequence is:

  ```
  for _, c := range eligible {
      repo, ok := r.matchRepoForSlug(repos, c.repo)
      if !ok { ...skipped_norepo...; continue }
      // <-- INSERT ADOPTION HERE
      if lt := lastTerminalNoLabelTask(c, existing, managed); lt != nil { ... }
      goal := ...
      ok2, err := r.createScanTask(...)
  ```

  Insert:

```go
		// Adoption (B1): if an issueLifecycle Task already exists for this issue
		// (Parked from a false refusal, or otherwise live), re-enter it to Triage
		// instead of creating a duplicate. One Task per issue forever; the shared
		// pod/branch is intentional. Done/Stopped Tasks are excluded by the helper
		// so deliberately-closed issues still create fresh on new activity.
		if adopt := hasLiveOrAdoptableTask(existing, c.repo, c.number); adopt != nil {
			if err := r.adoptLifecycleTask(ctx, proj, adopt); err != nil {
				l.Error(err, "issueScan: adopt existing lifecycle task",
					"action", "adopt_lifecycle", "resource_id", adopt.Name,
					"issue", fmt.Sprintf("%s#%d", c.repo, c.number))
				r.Metrics.ScanItem("issueScan", "adopt_error")
				continue
			}
			l.Info("issueScan: adopted existing lifecycle task (re-triage, no duplicate)",
				"action", "adopt_lifecycle", "resource_id", adopt.Name,
				"issue", fmt.Sprintf("%s#%d", c.repo, c.number))
			r.Metrics.ScanItem("issueScan", "adopted")
			continue
		}
```

  Rationale for placement: a non-terminal lifecycle Task is already caught by `isDeduped` (so it
  never reaches the eligible loop); the eligible loop only sees issues whose Tasks are all terminal
  (Done/Stopped/Parked) OR have no Task. The adoption helper deliberately ignores Done/Stopped, so
  it fires ONLY for Parked - exactly the duplicate-storm case - and otherwise falls through to the
  existing `lastTerminalNoLabelTask` gate + `createScanTask` (unchanged behavior for Done/Stopped/
  no-Task issues). This keeps the human-activity gate (Task 3) still governing whether a Parked
  Task is even eligible.

- [ ] Run it green:
  `mise exec -- go test ./internal/controller/ -run TestIssueScanAdoptsParkedTaskInsteadOfDuplicating -count=1`
  Expected: PASS.

- [ ] Regression check the binder + dedup suites:
  `mise exec -- go test ./internal/controller/ -run 'TestIssueScan|TestDedup|TestHasLiveOrAdoptableTask' -count=1`
  Expected: PASS.

### Step 2.4 - commit

- [ ] `git -C /Users/szymonri/Documents/tatara/tatara-operator add internal/controller/projectscan.go internal/controller/projectscan_binder_test.go`
- [ ] `git -C /Users/szymonri/Documents/tatara/tatara-operator commit -m "feat: adopt existing Parked lifecycle task instead of duplicating on issueScan"`

---

## Task 3: bot-authored comments do not free the dedup activity gate (B3)

`isDeduped`'s line-182 gate frees a terminal Task's dedup key when the issue's `updatedAt` advances
past the Task's creation. The operator's own park/discuss comment advances `updatedAt`, so the gate
opens on bot activity alone. Make the gate consult an injectable human-activity predicate; `nil`
preserves today's `updatedAt.After(creation)` behavior (for pure unit tests), production callers
pass a closure built from the SCM reader + `botLogin` so only HUMAN comments free the key.

**Interfaces**
- Consumes: `humanCommentAfter(ctx, reader, owner, name, number, botLogin, since)`
  (`projectscan.go:1387`); `proj.Spec.Scm.BotLogin`; `strings.Cut`.
- Produces: changed signature
  `func isDeduped(c candidate, existing []tatarav1alpha1.Task, managed []string, humanActivity func(c candidate, since time.Time) bool) bool`
  where `humanActivity == nil` means "use `c.updatedAt.After(since)`" (legacy). Two production
  callers pass a non-nil closure; all test callers pass `nil`.

### Step 3.1 - failing test

- [ ] Append to `internal/controller/projectscan_dedup_test.go`:

```go
func TestIsDeduped_BotCommentDoesNotFreeKey(t *testing.T) {
	created := metav1.Now()
	terminal := mkCronTask("o/r", 7, "issueLifecycle", "", "Succeeded")
	terminal.Status.LifecycleState = "Parked"
	terminal.CreationTimestamp = created
	existing := []tatarav1alpha1.Task{terminal}
	managed := managedPhaseLabels(nil)

	// Candidate updatedAt advanced past creation (as a bot comment would do).
	c := candidate{repo: "o/r", number: 7, updatedAt: created.Add(time.Hour)}

	// Legacy nil predicate: updatedAt advanced -> NOT deduped (eligible). Unchanged behavior.
	if isDeduped(c, existing, managed, nil) {
		t.Fatalf("nil predicate: updatedAt advanced should be eligible (legacy behavior)")
	}

	// Human-activity predicate that reports NO human comment since the Task creation
	// (i.e. only the bot commented): the key must stay HELD -> deduped.
	noHuman := func(_ candidate, _ time.Time) bool { return false }
	if !isDeduped(c, existing, managed, noHuman) {
		t.Fatalf("bot-only activity must keep the dedup key held (deduped)")
	}

	// Human comment present after creation -> key freed -> eligible.
	yesHuman := func(_ candidate, _ time.Time) bool { return true }
	if isDeduped(c, existing, managed, yesHuman) {
		t.Fatalf("human activity must free the dedup key (eligible)")
	}
}
```

- [ ] Update the EXISTING `isDeduped(...)` test callers to pass a trailing `nil` so the package
  compiles. They are in `projectscan_dedup_test.go` (lines ~49, 67, 70, 73, 86, 99, 112, 121, 134,
  147) and `projectscan_binder_test.go` (line ~162). Mechanically:
  `mise exec -- gofmt -l ./...` will not flag these; do a search-and-replace within those two test
  files turning `isDeduped(<args>)` into `isDeduped(<args>, nil)`. Concretely:
  - `isDeduped(tc.cand, existing, managed)` -> `isDeduped(tc.cand, existing, managed, nil)`
  - `isDeduped(candidate{repo: "o/r", number: 6}, existing, managed)` -> `..., nil)`
  - `isDeduped(older, existing, managed)` -> `isDeduped(older, existing, managed, nil)`
  - `isDeduped(newer, existing, managed)` -> `isDeduped(newer, existing, managed, nil)`
  - `isDeduped(c, existing, managed)` (each occurrence) -> `isDeduped(c, existing, managed, nil)`
  - in `projectscan_binder_test.go`: `isDeduped(tc.cand, tc.existing, managed)` -> `..., nil)`

- [ ] Run it red:
  `mise exec -- go test ./internal/controller/ -run 'TestIsDeduped_BotCommentDoesNotFreeKey|TestDedup' -count=1`
  Expected failure: compile error `not enough arguments in call to isDeduped` until the signature
  changes (Step 3.2) and existing callers get the `nil` arg (done above). After the test edits but
  before the impl, the new test fails to compile against the old 3-arg signature.

### Step 3.2 - minimal impl: signature + gate

- [ ] In `internal/controller/projectscan.go`, change `isDeduped` (line 161). Update the doc line
  and the gate at line ~182:

```go
// isDeduped reports whether a candidate already has a Task that should suppress
// a re-pick. Phase labels are the issue's state-of-truth (Option A):
//   - any non-terminal Task for (repo,number) -> skip (fast path)
//   - PR: a terminal Task at the same head-sha -> skip
//   - issue: a managed phase label present on the OPEN issue -> skip (active =>
//     handled by the live Task above; terminal+label => orphan the backstop
//     resumes; declined => no action). No managed label -> legacy/untracked, fall
//     back to the activity gate so a stale terminal Task is not re-triaged unless
//     the issue saw new HUMAN activity.
//
// humanActivity gates the no-managed-label terminal path: it reports whether the
// issue saw human activity strictly after `since` (the terminal Task's creation).
// nil means use the legacy candidate.updatedAt comparison (pure callers/tests).
// Production callers pass a closure built from the SCM reader + botLogin so the
// operator's OWN park/discuss comments (which advance updatedAt) never free the
// dedup key and respawn a duplicate (scm-author-vs-actor-egress-gate pattern).
func isDeduped(c candidate, existing []tatarav1alpha1.Task, managed []string, humanActivity func(c candidate, since time.Time) bool) bool {
	repoLabel := sanitizeRepoLabel(c.repo)
	numLabel := strconv.Itoa(c.number)
	for i := range existing {
		t := &existing[i]
		if t.Labels[labelSourceRepo] != repoLabel || t.Labels[labelSourceNumber] != numLabel {
			continue
		}
		if !tatarav1alpha1.TaskTerminal(t) {
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
		if humanActivity != nil {
			if !humanActivity(c, t.CreationTimestamp.Time) {
				return true
			}
		} else if !c.updatedAt.After(t.CreationTimestamp.Time) {
			return true
		}
	}
	return false
}
```

- [ ] Run it green:
  `mise exec -- go test ./internal/controller/ -run 'TestIsDeduped_BotCommentDoesNotFreeKey|TestDedup' -count=1`
  Expected: PASS.

### Step 3.3 - commit

- [ ] `git -C /Users/szymonri/Documents/tatara/tatara-operator add internal/controller/projectscan.go internal/controller/projectscan_dedup_test.go internal/controller/projectscan_binder_test.go`
- [ ] `git -C /Users/szymonri/Documents/tatara/tatara-operator commit -m "feat: bot-authored comments no longer free the issue dedup key"`

---

## Task 4: wire the human-activity closure into mrScan + issueScan (B3 production path)

The two production callers now build a closure that resolves the issue's owner/name and calls
`humanCommentAfter`. Fail-open (treat as human activity) when the repo cannot be split or the reader/
botLogin are unavailable, matching `humanCommentAfter`'s and the reactivation gate's existing
fail-open posture.

**Interfaces**
- Consumes: `isDeduped(..., humanActivity)` (Task 3); `humanCommentAfter`; `strings.Cut`;
  `reader scm.SCMReader`; `botLogin` from `proj.Spec.Scm.BotLogin`.
- Produces: a shared closure builder used by both scan loops:
  `func (r *ProjectReconciler) humanActivityGate(ctx context.Context, reader scm.SCMReader, botLogin string) func(c candidate, since time.Time) bool`

### Step 4.1 - failing integration test

- [ ] Append to `internal/controller/projectscan_binder_test.go`:

```go
// TestIssueScanBotCommentDoesNotRespawnTask asserts the end-to-end B3 guard: a
// terminal (Parked) issueLifecycle task whose issue updatedAt advanced ONLY because
// of a bot comment must NOT be adopted/respawned (no Triage flip, no QE). A second
// run after a HUMAN comment lands DOES re-enter it.
func TestIssueScanBotCommentDoesNotRespawnTask(t *testing.T) {
	ctx := context.Background()
	cron := &tatarav1alpha1.ScmCron{IssueScan: tatarav1alpha1.CronActivity{Schedule: "0 * * * *", MaxPerRepo: 2}}
	proj, repoA := seedScanProject(t, "b3-botcomment", cron)
	// seedScanProject sets BotLogin "tatara-bot".

	created := metav1.NewTime(time.Now().Add(-2 * time.Hour))
	pre := &tatarav1alpha1.Task{}
	pre.GenerateName = "scan-"
	pre.Namespace = testNS
	pre.Labels = scanTaskLabels(candidate{repo: "o/r", number: 8}, "issueScan", "issueLifecycle")
	pre.Spec = tatarav1alpha1.TaskSpec{
		ProjectRef: "b3-botcomment", RepositoryRef: repoA.Name, Goal: "g", Kind: "issueLifecycle",
	}
	if err := k8sClient.Create(ctx, pre); err != nil {
		t.Fatalf("pre-create: %v", err)
	}
	pre.CreationTimestamp = created
	pre.Status.Phase = "Succeeded"
	pre.Status.LifecycleState = "Parked"
	if err := k8sClient.Status().Update(ctx, pre); err != nil {
		t.Fatalf("pre status: %v", err)
	}

	// Bot-only comment after creation; issue updatedAt advanced.
	botOnly := &fakeReader{
		issues:   []scm.IssueRef{{Repo: "o/r", Number: 8, UpdatedAt: time.Now()}},
		comments: []scm.IssueComment{{Author: "tatara-bot", CreatedAt: time.Now()}},
	}
	r := newScanReconciler(botOnly)
	r.Metrics = obs.NewOperatorMetrics(prometheus.NewRegistry())
	r.issueScan(ctx, proj, botOnly, []tatarav1alpha1.Repository{*repoA},
		[]tatarav1alpha1.Task{*pre}, cron.IssueScan)

	if qes := listScanQEs(t, "b3-botcomment"); len(qes) != 0 {
		t.Fatalf("bot-only comment: want 0 QEs, got %d", len(qes))
	}
	got := &tatarav1alpha1.Task{}
	if err := k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: pre.Name}, got); err != nil {
		t.Fatalf("get task: %v", err)
	}
	if got.Status.LifecycleState != "Parked" {
		t.Fatalf("bot-only comment: task must stay Parked, got %q", got.Status.LifecycleState)
	}

	// Now a human comments: the task is adopted -> Triage.
	withHuman := &fakeReader{
		issues:   []scm.IssueRef{{Repo: "o/r", Number: 8, UpdatedAt: time.Now()}},
		comments: []scm.IssueComment{{Author: "szymon", CreatedAt: time.Now()}},
	}
	r2 := newScanReconciler(withHuman)
	r2.Metrics = obs.NewOperatorMetrics(prometheus.NewRegistry())
	// Re-list existing (the Parked task is still Parked).
	r2.issueScan(ctx, proj, withHuman, []tatarav1alpha1.Repository{*repoA},
		[]tatarav1alpha1.Task{*got}, cron.IssueScan)

	got2 := &tatarav1alpha1.Task{}
	if err := k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: pre.Name}, got2); err != nil {
		t.Fatalf("get task 2: %v", err)
	}
	if got2.Status.LifecycleState != "Triage" {
		t.Fatalf("human comment: task must be adopted to Triage, got %q", got2.Status.LifecycleState)
	}
}
```

- [ ] Run it red:
  `mise exec -- go test ./internal/controller/ -run TestIssueScanBotCommentDoesNotRespawnTask -count=1`
  Expected failure: the bot-only run flips the Task to Triage (because issueScan still passes `nil`
  to `isDeduped`, so the Parked Task is eligible and the adoption branch fires on bot activity).

### Step 4.2 - minimal impl: closure builder + wire both callers

- [ ] Add the builder in `internal/controller/projectscan.go` (near `humanCommentAfter`, line ~1387):

```go
// humanActivityGate returns the isDeduped human-activity predicate for a scan
// cycle: reports whether the candidate's issue saw a non-bot comment strictly
// after `since`. Fail-open (true) when the repo slug cannot be split or the
// reader/botLogin are unavailable, matching humanCommentAfter and the
// reactivation gate. PR candidates have no issue comment timeline, so the
// predicate returns the legacy updatedAt comparison for them (isDeduped never
// reaches the gate for PRs, but keep it correct if called).
func (r *ProjectReconciler) humanActivityGate(ctx context.Context, reader scm.SCMReader, botLogin string) func(c candidate, since time.Time) bool {
	return func(c candidate, since time.Time) bool {
		if c.isPR {
			return c.updatedAt.After(since)
		}
		owner, name, ok := strings.Cut(c.repo, "/")
		if !ok || reader == nil || botLogin == "" {
			return true
		}
		return humanCommentAfter(ctx, reader, owner, name, c.number, botLogin, since)
	}
}
```

- [ ] Wire `mrScan` (`projectscan.go:653-661`). Replace the dedup loop's `isDeduped` call:

  Current:
  ```go
	managed := managedPhaseLabels(proj.Spec.Scm)
	var eligible []candidate
	for _, c := range cands {
		if isDeduped(c, existing, managed) {
  ```
  New (build the gate from the already-resolved `bot` var at `mrScan` line 624-627):
  ```go
	managed := managedPhaseLabels(proj.Spec.Scm)
	gate := r.humanActivityGate(ctx, reader, bot)
	var eligible []candidate
	for _, c := range cands {
		if isDeduped(c, existing, managed, gate) {
  ```

- [ ] Wire `issueScan` (`projectscan.go:838-845`). `botLogin` is already resolved at
  `issueScan` line 803-806. Replace:

  Current:
  ```go
	managed := managedPhaseLabels(proj.Spec.Scm)
	var eligible []candidate
	for _, c := range cands {
		if isDeduped(c, existing, managed) {
  ```
  New:
  ```go
	managed := managedPhaseLabels(proj.Spec.Scm)
	gate := r.humanActivityGate(ctx, reader, botLogin)
	var eligible []candidate
	for _, c := range cands {
		if isDeduped(c, existing, managed, gate) {
  ```

- [ ] Run it green:
  `mise exec -- go test ./internal/controller/ -run TestIssueScanBotCommentDoesNotRespawnTask -count=1`
  Expected: PASS.

- [ ] Regression: the full scan suite:
  `mise exec -- go test ./internal/controller/ -run 'TestIssueScan|TestMRScan|TestDedup|TestHasLiveOrAdoptableTask|TestIsDeduped|TestRunScans' -count=1`
  Expected: PASS. (If `TestMRScan*` regress because the `fakeReader` returns no comments and a PR
  candidate is involved, note that `gate` is only consulted on the non-PR no-managed-label terminal
  path; PR candidates short-circuit at the `c.isPR` branch in `isDeduped` before the gate, so these
  should be unaffected. Investigate any failure per systematic-debugging; do not weaken the gate.)

### Step 4.3 - commit

- [ ] `git -C /Users/szymonri/Documents/tatara/tatara-operator add internal/controller/projectscan.go internal/controller/projectscan_binder_test.go`
- [ ] `git -C /Users/szymonri/Documents/tatara/tatara-operator commit -m "feat: gate scan dedup on human activity so bot comments don't respawn tasks"`

---

## Task 5: full verification + code review (verification-before-completion + requesting-code-review)

**Interfaces** Consumes: the whole `internal/controller` + `internal/queue` packages. Produces: a
green build/test/lint and a reviewed diff ready to merge to `tatara-operator` main.

### Step 5.1 - full suite + lint + build

- [ ] `mise exec -- gofmt -l internal/ api/` (expect: no output - all formatted).
- [ ] `mise exec -- go build ./...` (expect: clean).
- [ ] `mise exec -- go test ./... -count=1` (expect: PASS for the whole module; envtest binaries are
  installed in the agent container; if `internal/controller` envtest fails to find the kube-apiserver
  binary, run `mise run test` which sets `KUBEBUILDER_ASSETS`).
- [ ] `mise exec -- golangci-lint run` (expect: clean; fix any new findings in the touched files).

### Step 5.2 - self-review against the spec

- [ ] Confirm against `docs/superpowers/specs/2026-06-23-implement-false-refusal-rootcause-design.md`
  section "Defect B":
  - B1: one Task per issue - adoption fires for Parked (and any non-Done/Stopped live) Task,
    re-entering Triage, no second QE. Verified by `TestIssueScanAdoptsParkedTaskInsteadOfDuplicating`.
  - B3: bot park/discuss comments do not re-trigger a scan. Verified by
    `TestIsDeduped_BotCommentDoesNotFreeKey` (unit) + `TestIssueScanBotCommentDoesNotRespawnTask`
    (integration).
- [ ] Confirm NO API/CRD changes in this slice (so no controller-gen run needed): `git -C
  /Users/szymonri/Documents/tatara/tatara-operator diff --name-only main -- api/` returns nothing.

### Step 5.3 - code review

- [ ] Invoke `superpowers:requesting-code-review` on the branch diff. Fix all critical/high findings,
  re-run Step 5.1, then re-review the fix.
- [ ] Run `pre-commit run --all-files` if the repo has `.pre-commit-config.yaml`; fix hook findings.

### Step 5.4 - finish the branch

- [ ] Per `superpowers:finishing-a-development-branch`: this slice merges to `tatara-operator` main
  FIRST in the operator merge order (B -> C-operator -> A-operator). Do NOT build/deploy from the
  worktree. Leave the actual image build + `tatara-helmfile` bump to the orchestrator after all three
  operator slices land on main (per the spec Deploy section + hard-rule 15).

---

## Notes for the orchestrator (divergences from spec)

- Spec named `resetAgentRun` + `setLifecycleState` (TaskReconciler methods) for adoption; this plan
  uses the established `ProjectReconciler` inline `RetryOnConflict` status reset (the reactivation
  pattern) because those methods are not reachable from `issueScan`. Behaviorally equivalent: pod/
  branch are deterministic and the next TaskReconciler reconcile re-spawns on the Triage state.
- Spec asked whether the SCM read must change to expose comment authors: it does NOT. `scm.IssueComment`
  already carries `Author` + `CreatedAt` and `humanCommentAfter` already uses them. B3 is pure wiring.
- B3 is implemented inside `isDeduped` (signature-additive `humanActivity` closure, `nil`=legacy) so
  BOTH `mrScan` and `issueScan` get the bot-author exclusion at the source, not only the issueScan
  `lastTerminalNoLabelTask` downstream gate (which already existed for issueScan but not mrScan).
- The pre-existing issueScan `lastTerminalNoLabelTask` + `humanCommentAfter` gate (lines 854-872) is
  left in place; it now sits AFTER the adoption branch and is redundant for the adopt path but still
  governs the create-fresh path for Done/Stopped/no-Task issues. Harmless; KISS leaves it.
