# Per-repo all-items fan-out - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the autonomous cron triage every open issue/MR in every project repo, parallelised per repo (one lane per repo, `maxPerRepo=1` active Task), bounded by the existing `MaxConcurrentTasks` ceiling.

**Architecture:** Replace the global pick-N in `mrScan`/`issueScan` with a per-repo top-up: for each repo, create up to `maxPerRepo - laneOccupancy(repo)` Tasks (priority-then-stale). `laneOccupancy` excludes `AwaitingApproval` so proposals don't stall a lane. A backlog-aware requeue refills freed lanes within ~60s. Reuses the existing per-item Task + agent.

**Tech Stack:** Go 1.x, controller-runtime, kubebuilder CRD, envtest.

**Worktree:** off fresh `tatara-operator` `main` (`git checkout main && git pull`). Bots push to this repo - the auto-merge worktree `feat/scm-auto-merge` already exists; create a SEPARATE worktree for this work.

**Spec:** `docs/superpowers/specs/2026-06-12-per-repo-fanout-design.md`

**Key existing symbols (read once, do not re-derive):**
- `internal/controller/projectscan.go`: `candidate{repo,number,author,headSHA,labels,updatedAt,isPR}`, `selectCandidates(in, priorityLabel, n)` (priority-then-stale, caps at n), `mrScan`/`issueScan` (currently `selected := selectCandidates(eligible, prio, act.MaxPerCycle)`), `isDeduped`, `sanitizeRepoLabel`, `matchRepoForSlug`, `createScanTask`, `existingScanTasks`, `runScans` (returns `(time.Duration, error)`, has a `consider(d time.Duration)` closure reducing `soonest`), `maxScheduleRequeue` const.
- Labels: `labelSourceRepo` (= `sanitizeRepoLabel(slug)`), `labelSourceNumber`, `labelActivity`.
- `api/v1alpha1/project_types.go`: `CronActivity{Schedule, MaxPerCycle}` (used by `MRScan` + `IssueScan`); `BrainstormActivity` (separate type, untouched); `ProjectSpec.MaxConcurrentTasks`.
- Task phases seen: `Planning`, `Running`, `AwaitingApproval`, `Succeeded`, `Failed`, plus `""` (new). `isActive` = Planning|Running (in `task_controller.go`).

---

### Task 1: Rename CronActivity.MaxPerCycle -> MaxPerRepo

**Files:**
- Modify: `api/v1alpha1/project_types.go` (CronActivity field)
- Modify: `internal/controller/projectscan.go` (2 call sites `act.MaxPerCycle`)
- Regenerate: `config/crd/...` + `charts/tatara-operator/crds/...` via `make manifests`

- [ ] **Step 1: Rename the field**

In `api/v1alpha1/project_types.go`, `CronActivity`:

```go
type CronActivity struct {
	// Schedule is a 5-field cron (robfig ParseStandard). Empty disables this activity.
	// +optional
	Schedule string `json:"schedule,omitempty"`
	// MaxPerRepo caps the number of in-progress Tasks per repo (one lane per repo).
	// +kubebuilder:default=1
	// +optional
	MaxPerRepo int `json:"maxPerRepo,omitempty"`
}
```

`BrainstormActivity` keeps its own `MaxPerCycle` - do not touch it.

- [ ] **Step 2: Update the 2 scan call sites (temporary, keeps build green)**

In `internal/controller/projectscan.go`, in BOTH `mrScan` and `issueScan`, change `act.MaxPerCycle` -> `act.MaxPerRepo` (the per-repo top-up replaces this entirely in Tasks 4-5; this keeps the build compiling now).

Grep to be sure: `grep -rn "MaxPerCycle" internal/ api/` should show only `BrainstormActivity` and brainstorm code after this.

- [ ] **Step 3: Regenerate the CRD**

Run: `make manifests`
Expected: `config/crd/bases/*project*.yaml` and `charts/tatara-operator/crds/*project*.yaml` now show `maxPerRepo` (not `maxPerCycle`) under `mrScan`/`issueScan`; `brainstorm` still has `maxPerCycle`.

- [ ] **Step 4: Build + existing scan tests**

Run: `KUBEBUILDER_ASSETS="$(cat /tmp/kba.txt)" go test ./internal/controller/ ./api/... -count=1`
Expected: PASS (any test setting `MaxPerCycle` on a scan activity must be updated to `MaxPerRepo` - grep the test files and fix).

- [ ] **Step 5: Commit**

```bash
git add api/v1alpha1/project_types.go internal/controller/projectscan.go config/crd charts/tatara-operator/crds
git commit -m "feat(api): rename scan CronActivity.MaxPerCycle to MaxPerRepo"
```

---

### Task 2: laneOccupancy pure helper

**Files:**
- Modify: `internal/controller/projectscan.go` (add helper + `slices` import)
- Test: `internal/controller/projectscan_test.go` (or the existing scan test file - grep for `func TestSelectCandidates` to find it)

- [ ] **Step 1: Write the failing test**

```go
func TestLaneOccupancy(t *testing.T) {
	mk := func(repo, kind, phase string) tatarav1alpha1.Task {
		return tatarav1alpha1.Task{
			ObjectMeta: metav1.ObjectMeta{Labels: map[string]string{labelSourceRepo: sanitizeRepoLabel(repo)}},
			Spec:       tatarav1alpha1.TaskSpec{Kind: kind},
			Status:     tatarav1alpha1.TaskStatus{Phase: phase},
		}
	}
	existing := []tatarav1alpha1.Task{
		mk("o/a", "triageIssue", "Running"),       // occupies a/issue lane
		mk("o/a", "triageIssue", "AwaitingApproval"), // does NOT occupy (awaiting)
		mk("o/a", "triageIssue", "Succeeded"),     // terminal, no
		mk("o/b", "review", "Planning"),           // occupies b/mr lane
		mk("o/a", "selfImprove", "Running"),       // wrong kind for issue lane
	}
	require.Equal(t, 1, laneOccupancy(existing, "o/a", "triageIssue"))
	require.Equal(t, 1, laneOccupancy(existing, "o/b", "review", "selfImprove"))
	require.Equal(t, 0, laneOccupancy(existing, "o/c", "triageIssue"))
}
```

Add `metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"` to the test imports if missing.

- [ ] **Step 2: Run, verify fail**

Run: `KUBEBUILDER_ASSETS="$(cat /tmp/kba.txt)" go test ./internal/controller/ -run TestLaneOccupancy -count=1`
Expected: FAIL - `laneOccupancy` undefined.

- [ ] **Step 3: Implement**

In `internal/controller/projectscan.go` (add `"slices"` to imports):

```go
// laneOccupancy counts this Project's scan Tasks for repoSlug that still occupy
// the repo's lane: Kind in kinds, phase not terminal and not AwaitingApproval
// (an awaiting-approval proposal frees the lane for the next item).
func laneOccupancy(existing []tatarav1alpha1.Task, repoSlug string, kinds ...string) int {
	label := sanitizeRepoLabel(repoSlug)
	n := 0
	for i := range existing {
		t := &existing[i]
		if t.Labels[labelSourceRepo] != label || !slices.Contains(kinds, t.Spec.Kind) {
			continue
		}
		switch t.Status.Phase {
		case "Succeeded", "Failed", "AwaitingApproval":
			continue
		}
		n++
	}
	return n
}
```

- [ ] **Step 4: Run, verify pass**

Run: `KUBEBUILDER_ASSETS="$(cat /tmp/kba.txt)" go test ./internal/controller/ -run TestLaneOccupancy -count=1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/controller/projectscan.go internal/controller/projectscan_test.go
git commit -m "feat(scan): laneOccupancy helper (per-repo, excludes AwaitingApproval)"
```

---

### Task 3: selectPerRepo pure helper

**Files:**
- Modify: `internal/controller/projectscan.go`
- Test: same scan test file as Task 2

- [ ] **Step 1: Write the failing test**

```go
func TestSelectPerRepo(t *testing.T) {
	c := func(repo string, n int, age time.Duration) candidate {
		return candidate{repo: repo, number: n, updatedAt: time.Now().Add(-age)}
	}
	eligible := []candidate{
		c("o/a", 1, 3*time.Hour), c("o/a", 2, 1*time.Hour), // a: two items
		c("o/b", 3, 2*time.Hour),                            // b: one item
	}
	// a lane already has 1 active -> a gets 0; b has 0 -> b gets its 1.
	occ := func(slug string) int {
		if slug == "o/a" {
			return 1
		}
		return 0
	}
	got := selectPerRepo(eligible, "", 1, occ)
	require.Len(t, got, 1)
	require.Equal(t, "o/b", got[0].repo)

	// empty lanes, maxPerRepo 1 -> one per repo, stale-first within repo.
	got2 := selectPerRepo(eligible, "", 1, func(string) int { return 0 })
	require.Len(t, got2, 2)
	repos := []string{got2[0].repo, got2[1].repo}
	require.ElementsMatch(t, []string{"o/a", "o/b"}, repos)
	for _, g := range got2 {
		if g.repo == "o/a" {
			require.Equal(t, 1, g.number) // a#1 is older -> stale-first
		}
	}
}
```

- [ ] **Step 2: Run, verify fail**

Run: `KUBEBUILDER_ASSETS="$(cat /tmp/kba.txt)" go test ./internal/controller/ -run TestSelectPerRepo -count=1`
Expected: FAIL - `selectPerRepo` undefined.

- [ ] **Step 3: Implement**

```go
// selectPerRepo groups eligible candidates by repo and picks, per repo, the best
// (priority-then-stale) items up to maxPerRepo minus that repo's lane occupancy.
func selectPerRepo(eligible []candidate, priorityLabel string, maxPerRepo int, occ func(repoSlug string) int) []candidate {
	if maxPerRepo < 1 {
		maxPerRepo = 1
	}
	byRepo := map[string][]candidate{}
	var order []string
	for _, c := range eligible {
		if _, ok := byRepo[c.repo]; !ok {
			order = append(order, c.repo)
		}
		byRepo[c.repo] = append(byRepo[c.repo], c)
	}
	var out []candidate
	for _, slug := range order {
		n := maxPerRepo - occ(slug)
		if n < 1 {
			continue
		}
		out = append(out, selectCandidates(byRepo[slug], priorityLabel, n)...)
	}
	return out
}
```

- [ ] **Step 4: Run, verify pass**

Run: `KUBEBUILDER_ASSETS="$(cat /tmp/kba.txt)" go test ./internal/controller/ -run TestSelectPerRepo -count=1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/controller/projectscan.go internal/controller/projectscan_test.go
git commit -m "feat(scan): selectPerRepo per-repo top-up selection"
```

---

### Task 4: Wire issueScan to per-repo top-up + backlog cadence

**Files:**
- Modify: `internal/controller/projectscan.go` (`issueScan` signature + body; `runScans` call site; add `backlogRequeue` const)
- Test: scan test file (envtest)

- [ ] **Step 1: Add the constant + change issueScan**

Add near `maxScheduleRequeue`:

```go
const backlogRequeue = 60 * time.Second
```

In `issueScan`, change the signature to return a backlog bool:

```go
func (r *ProjectReconciler) issueScan(ctx context.Context, proj *tatarav1alpha1.Project, reader scm.SCMReader, repos []tatarav1alpha1.Repository, existing []tatarav1alpha1.Task, act tatarav1alpha1.CronActivity) bool {
```

Replace the selection line:

```go
	selected := selectCandidates(eligible, proj.Spec.Scm.PriorityLabel, act.MaxPerRepo)
```

with:

```go
	selected := selectPerRepo(eligible, proj.Spec.Scm.PriorityLabel, act.MaxPerRepo,
		func(slug string) int { return laneOccupancy(existing, slug, "triageIssue") })
```

At the end of `issueScan` (after the `l.Info("issueScan complete", ...)` line), return whether eligible items remain un-created:

```go
	return len(selected) < len(eligible)
```

- [ ] **Step 2: Consume backlog in runScans**

In `runScans`, the issueScan due-branch currently:

```go
		if due {
			r.issueScan(ctx, proj, reader, repos, existing, cronSpec.IssueScan)
			r.stampScan(ctx, proj, "issueScan")
			if next2, ok2 := activityNextFire(cronSpec.IssueScan.Schedule, now); ok2 {
				consider(next2)
			}
		} else {
```

change to:

```go
		if due {
			backlog := r.issueScan(ctx, proj, reader, repos, existing, cronSpec.IssueScan)
			r.stampScan(ctx, proj, "issueScan")
			if next2, ok2 := activityNextFire(cronSpec.IssueScan.Schedule, now); ok2 {
				consider(next2)
			}
			if backlog {
				consider(backlogRequeue)
			}
		} else {
```

- [ ] **Step 3: Write the failing envtest**

Add to the scan test file (mirror the existing issueScan envtest - grep `func TestIssueScan` or `issueScan(` in the test file for the seed pattern). The test seeds a Project with 2 repos each having 2 open issues, a `MaxPerRepo: 1` issueScan activity, runs the scan, and asserts ONE triageIssue Task per repo (not 1 total) and that backlog is reported:

```go
func TestIssueScan_PerRepoTopUp(t *testing.T) {
	// seed project + repos o/a, o/b; reader returns 2 open issues per repo.
	// (reuse the existing fake SCMReader + seed helpers in this file)
	// run: backlog := r.issueScan(ctx, proj, reader, repos, existing, act{MaxPerRepo:1})
	// assert: exactly 1 triageIssue Task whose source repo == o/a, 1 == o/b (2 total)
	// assert: backlog == true (2 more issues remain, lanes now full)
}
```

Fill the seed using this file's existing scan-test helpers (fake reader returning `[]scm.IssueRef`, `seed*` project/repo creators). Assert via `kubectl`-equivalent `k8sClient.List` of Tasks filtered by `labelActivity == "issueScan"`.

- [ ] **Step 4: Run, verify fail then pass**

Run: `KUBEBUILDER_ASSETS="$(cat /tmp/kba.txt)" go test ./internal/controller/ -run TestIssueScan_PerRepoTopUp -count=1`
Expected after Steps 1-2: PASS (1 Task/repo, backlog true).

- [ ] **Step 5: Full controller suite (regression: global ceiling still gates)**

Run: `KUBEBUILDER_ASSETS="$(cat /tmp/kba.txt)" go test ./internal/controller/ -count=1`
Expected: PASS (existing `atConcurrencyCap` / scan tests still green).

- [ ] **Step 6: Commit**

```bash
git add internal/controller/projectscan.go internal/controller/projectscan_test.go
git commit -m "feat(scan): issueScan per-repo top-up + backlog requeue"
```

---

### Task 5: Wire mrScan to per-repo top-up + backlog cadence

**Files:**
- Modify: `internal/controller/projectscan.go` (`mrScan` signature + body; `runScans` call site)
- Test: scan test file (envtest)

- [ ] **Step 1: Change mrScan**

Change `mrScan` signature to return `bool`:

```go
func (r *ProjectReconciler) mrScan(ctx context.Context, proj *tatarav1alpha1.Project, reader scm.SCMReader, repos []tatarav1alpha1.Repository, existing []tatarav1alpha1.Task, act tatarav1alpha1.CronActivity) bool {
```

Replace the selection line:

```go
	selected := selectCandidates(eligible, proj.Spec.Scm.PriorityLabel, act.MaxPerRepo)
```

with:

```go
	selected := selectPerRepo(eligible, proj.Spec.Scm.PriorityLabel, act.MaxPerRepo,
		func(slug string) int { return laneOccupancy(existing, slug, "review", "selfImprove") })
```

At the end of `mrScan`, return:

```go
	return len(selected) < len(eligible)
```

- [ ] **Step 2: Consume backlog in runScans (mrScan branch)**

In `runScans`, mirror Task 4 Step 2 for the mrScan due-branch:

```go
		if due {
			backlog := r.mrScan(ctx, proj, reader, repos, existing, cronSpec.MRScan)
			r.stampScan(ctx, proj, "mrScan")
			if next2, ok2 := activityNextFire(cronSpec.MRScan.Schedule, now); ok2 {
				consider(next2)
			}
			if backlog {
				consider(backlogRequeue)
			}
		} else {
```

- [ ] **Step 3: Write the failing envtest**

```go
func TestMRScan_PerRepoTopUp(t *testing.T) {
	// seed project + repos o/a, o/b; fake reader returns 2 open PRs per repo,
	// one bot-authored (-> selfImprove) one human (-> review).
	// run: backlog := r.mrScan(ctx, proj, reader, repos, existing, act{MaxPerRepo:1})
	// assert: exactly 1 Task per repo (kind review|selfImprove), 2 total; backlog true.
}
```

Reuse the existing mrScan envtest seed pattern in this file (fake reader `ListOpenPRs`, bot/human authors via `candidate.author == BotLogin`).

- [ ] **Step 4: Run, verify pass**

Run: `KUBEBUILDER_ASSETS="$(cat /tmp/kba.txt)" go test ./internal/controller/ -run TestMRScan_PerRepoTopUp -count=1`
Expected: PASS.

- [ ] **Step 5: Full suite + gofmt + lint**

Run:
```bash
KUBEBUILDER_ASSETS="$(cat /tmp/kba.txt)" go test ./... -count=1
gofmt -l . | grep -v '^$' || echo gofmt-clean
golangci-lint run ./internal/controller/... ./api/...
```
Expected: tests PASS; gofmt clean; lint 0 issues.

- [ ] **Step 6: Commit**

```bash
git add internal/controller/projectscan.go internal/controller/projectscan_test.go
git commit -m "feat(scan): mrScan per-repo top-up + backlog requeue"
```

---

## Runbook - live deploy (after merge, GATED)

Not TDD; config/ops, run after the operator change deploys.

### R1: live Project manifest

Update the `tatara` Project manifest (the standalone manifest applied to the
cluster, NOT `kubectl edit`):
- `spec.scm.cron.issueScan.maxPerRepo: 1`, `spec.scm.cron.mrScan.maxPerRepo: 1`
  (rename from `maxPerCycle`).
- `spec.maxConcurrentTasks: 3` (the total-agent ceiling chosen for the cluster).

`kubectl apply --server-side` the regenerated Project CRD (Helm does not upgrade
CRDs), then apply the updated Project manifest.

### R2: deploy

Operator change merges to `main` -> CI publishes image + chart
`oci://harbor.szymonrichert.pl/charts/tatara-operator:0.0.0-<sha>`. Bump the
operator release version in the infra helmfile, `helmfile diff`, MR, deploy.

### R3: verify

`kubectl get project tatara -o jsonpath='{.spec.scm.cron.issueScan.maxPerRepo}'`
== 1; watch `kubectl get tasks -n tatara` create one triageIssue + one
review/selfImprove per repo (<= 3 active total), and the backlog of 20 issues +
12 MRs drain into proposals over the next scans.

---

## Self-Review

**Spec coverage:**
- Per-repo top-up selection -> Tasks 3, 4, 5.
- laneOccupancy excludes AwaitingApproval -> Task 2.
- Global MaxConcurrentTasks ceiling retained -> unchanged (Task 4/5 regression check; R1 sets it to 3).
- Backlog-aware 60s cadence -> Tasks 4, 5 (runScans consume).
- CRD rename MaxPerCycle->MaxPerRepo (scans only) -> Task 1.
- Live manifest maxPerRepo + maxConcurrentTasks:3 -> R1.
- mrScan + issueScan only; brainstorm untouched -> Tasks 4/5 touch only those; Task 1 leaves BrainstormActivity.

**Placeholder scan:** envtest seed bodies in Tasks 4/5 Step 3 are described as "reuse the existing scan-test seed pattern in this file" with the exact assertions spelled out - the seed helpers exist in the repo; the implementer copies them. Not a silent TODO.

**Type consistency:** `laneOccupancy(existing, slug, kinds...)` and
`selectPerRepo(eligible, prio, maxPerRepo, occ)` signatures identical across
definition (Tasks 2,3) and call sites (Tasks 4,5). `act.MaxPerRepo` consistent
after Task 1. `issueScan`/`mrScan` both return `bool` consumed by `runScans`.
