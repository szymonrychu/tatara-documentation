# Revive & Harden Bot-PR Merge/Recovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the operator's autonomous bot-PR recovery (dead due to a `laneOccupancy` accounting bug), bound it against thrash, quiet a cosmetic reconcile log, deploy via GitOps, and let the revived system heal the 5 stuck PRs.

**Architecture:** Single Go fix in `tatara-operator` `internal/controller/projectscan.go` (`laneOccupancy` must free the lane for terminal/Conversation lifecycle states), a small thrash-bound in `mrScan`, a log no-op cleanup in `labels.go`, then a `tatara-helmfile` GitOps deploy. Recovery-as-safety-net: the existing `mrScan`/lifecycle already merges green PRs, rebases conflicts, and closes duplicates once the scan can pick again.

**Tech Stack:** Go 1.x (operator, `controller-runtime`), table-driven `go test`, mise toolchain, Helm/Helmfile, GitHub Actions ARC runners, harbor registry.

**Repos:** `tatara-operator` (code), `tatara-helmfile` (deploy). Operator local `main` is 21 commits behind `origin/main`; external bots push here — all work happens in a worktree off **fresh** `origin/main`.

---

## File Structure

- `internal/controller/projectscan.go` — `laneOccupancy` fix (A) + `priorTerminalAttempts` + `mrScan` thrash-bound (B).
- `internal/controller/projectscan_select_test.go` — new lane test (A).
- `internal/controller/projectscan_recovery_bound_test.go` — new thrash-bound tests (B).
- `internal/controller/labels.go` — `setLifecycleLabel` no-op log fix (C).
- `internal/controller/lifecycle_label_test.go` — no-op log test (C).
- `tatara-helmfile` operator release values — chart version + `image.tag` bump (D).

---

## Task 0: Worktree off fresh origin/main

- [ ] **Step 1: Create isolated worktree from fresh `origin/main`**

REQUIRED SUB-SKILL: `superpowers:using-git-worktrees`. The operator repo lives at `~/Documents/tatara/tatara-operator`. Fetch first; never branch off the stale local `main`.

```bash
cd ~/Documents/tatara/tatara-operator
git fetch origin main
git worktree add -b fix/revive-pr-recovery ../tatara-operator-wt origin/main
cd ../tatara-operator-wt
git log --oneline -1   # expect origin/main HEAD (0acd1aa or newer)
```

- [ ] **Step 2: Baseline build/test green**

Run: `go build ./... && go test ./internal/controller/ -count=1`
Expected: PASS (clean baseline before changes).

---

## Task A: laneOccupancy frees terminal & Conversation lifecycle (CORE FIX)

**Files:**
- Modify: `internal/controller/projectscan.go` (`laneOccupancy`)
- Test: `internal/controller/projectscan_select_test.go`

**Why:** `issueLifecycle`/`review` tasks signal terminality via `Status.LifecycleState` (Done/Parked/Stopped) and leave `Status.Phase` empty. `laneOccupancy` only checks `Phase`, so terminal lifecycle tasks count against the repo lane forever; with `maxPerRepo=1`, recovery is dead. Live lanes: operator=29, chat=10, wrapper=6 (cap 1). `isLifecycleTerminal` already exists in this file.

- [ ] **Step 1: Write the failing test**

Add to `internal/controller/projectscan_select_test.go`:

```go
func TestLaneOccupancy_TerminalAndConversationLifecycleFreeLane(t *testing.T) {
	mkLC := func(repo, kind, lc string) tatarav1alpha1.Task {
		return tatarav1alpha1.Task{
			ObjectMeta: metav1.ObjectMeta{Labels: map[string]string{labelSourceRepo: sanitizeRepoLabel(repo)}},
			Spec:       tatarav1alpha1.TaskSpec{Kind: kind},
			// Phase intentionally empty: real issueLifecycle tasks never set it.
			Status: tatarav1alpha1.TaskStatus{LifecycleState: lc},
		}
	}
	existing := []tatarav1alpha1.Task{
		mkLC("o/r", "issueLifecycle", "Done"),
		mkLC("o/r", "issueLifecycle", "Parked"),
		mkLC("o/r", "issueLifecycle", "Stopped"),
		mkLC("o/r", "issueLifecycle", "Conversation"),
		mkLC("o/r", "issueLifecycle", "Implement"), // active -> occupies the lane
	}
	// Only the active task holds the lane; terminal + Conversation free it.
	require.Equal(t, 1, laneOccupancy(existing, "o/r", "issueLifecycle", "review"))
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/controller/ -run TestLaneOccupancy_TerminalAndConversationLifecycleFreeLane -v`
Expected: FAIL — `laneOccupancy(...) = 5, want 1`.

- [ ] **Step 3: Implement the fix**

In `internal/controller/projectscan.go` `laneOccupancy`, add the lifecycle check **before** the existing phase switch:

```go
func laneOccupancy(existing []tatarav1alpha1.Task, repoSlug string, kinds ...string) int {
	label := sanitizeRepoLabel(repoSlug)
	n := 0
	for i := range existing {
		t := &existing[i]
		if t.Labels[labelSourceRepo] != label || !slices.Contains(kinds, t.Spec.Kind) {
			continue
		}
		// Lifecycle tasks signal terminality via LifecycleState (Phase stays
		// empty); Conversation is human-blocked with no running pod. Such tasks
		// hold no agent slot, so they must not occupy the repo's scan lane -
		// otherwise terminal issueLifecycle tasks starve mrScan/issueScan
		// recovery forever (maxPerRepo=1).
		if isLifecycleTerminal(t.Status.LifecycleState) || t.Status.LifecycleState == "Conversation" {
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

- [ ] **Step 4: Run the new test + the existing lane tests**

Run: `go test ./internal/controller/ -run 'TestLaneOccupancy|TestMRScanLaneOccupancy|TestIssueScanLaneOccupancy' -v`
Expected: PASS all. (Existing tests use Phase-only fixtures with empty LifecycleState, so the new branch does not change their outcome.)

- [ ] **Step 5: Commit**

```bash
git add internal/controller/projectscan.go internal/controller/projectscan_select_test.go
git commit -m "fix(operator): laneOccupancy frees terminal/Conversation lifecycle tasks

Lifecycle tasks track terminality via LifecycleState, not Phase, so terminal
issueLifecycle tasks were counted against the repo lane forever; with
maxPerRepo=1 this killed mrScan/issueScan recovery (lanes 29/10/6 vs cap 1)."
```

---

## Task B: Thrash-bound — stop re-adopting an unfixable PR

**Files:**
- Modify: `internal/controller/projectscan.go` (add `priorTerminalAttempts`; gate in `mrScan` bot-PR branch)
- Test: `internal/controller/projectscan_recovery_bound_test.go` (new)

**Why:** Once recovery is alive, a genuinely-broken PR would be re-adopted (new agent run) every scan cycle forever. Bound it: after N terminal attempts on the same PR, stop re-adopting (the last park comment already explains; a human takes over). Read-only over `existing` — no writer needed in scan.

- [ ] **Step 1: Write the failing tests**

Create `internal/controller/projectscan_recovery_bound_test.go`:

```go
package controller

import (
	"testing"

	"github.com/stretchr/testify/require"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
)

func mkPRTask(repo string, pr int, lc string) tatarav1alpha1.Task {
	return tatarav1alpha1.Task{
		ObjectMeta: metav1.ObjectMeta{Labels: map[string]string{labelSourceRepo: sanitizeRepoLabel(repo)}},
		Spec: tatarav1alpha1.TaskSpec{
			Kind:   "issueLifecycle",
			Source: &tatarav1alpha1.TaskSource{Number: pr, IsPR: true},
		},
		Status: tatarav1alpha1.TaskStatus{LifecycleState: lc},
	}
}

func TestPriorTerminalAttempts_CountsTerminalPRTasks(t *testing.T) {
	existing := []tatarav1alpha1.Task{
		mkPRTask("o/r", 50, "Parked"),
		mkPRTask("o/r", 50, "Done"),
		mkPRTask("o/r", 50, "Implement"), // non-terminal: not counted
		mkPRTask("o/r", 51, "Parked"),    // different PR: not counted
		mkPRTask("o/x", 50, "Parked"),    // different repo: not counted
	}
	require.Equal(t, 2, priorTerminalAttempts(existing, "o/r", 50))
	require.Equal(t, 0, priorTerminalAttempts(existing, "o/r", 99))
}

func TestRecoveryBoundThreshold(t *testing.T) {
	require.Equal(t, 3, maxRecoveryAttempts)
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `go test ./internal/controller/ -run 'TestPriorTerminalAttempts|TestRecoveryBoundThreshold' -v`
Expected: FAIL — `undefined: priorTerminalAttempts` / `undefined: maxRecoveryAttempts`.

- [ ] **Step 3: Implement helper + constant**

In `internal/controller/projectscan.go` (near the other scan helpers):

```go
// maxRecoveryAttempts bounds how many times mrScan re-adopts the same bot PR
// before giving up. A PR that has been driven to a terminal lifecycle this many
// times is not fixable by another autonomous pass; stop re-spawning agents and
// leave it for a human (the last park comment already explains why).
const maxRecoveryAttempts = 3

// priorTerminalAttempts counts terminal (Done/Stopped/Parked) tasks that already
// targeted this exact PR, so mrScan can stop re-adopting an unfixable PR.
func priorTerminalAttempts(existing []tatarav1alpha1.Task, repoSlug string, prNumber int) int {
	want := sanitizeRepoLabel(repoSlug)
	n := 0
	for i := range existing {
		t := &existing[i]
		if t.Spec.Source == nil || !t.Spec.Source.IsPR || t.Spec.Source.Number != prNumber {
			continue
		}
		if t.Labels[labelSourceRepo] != want {
			continue
		}
		if isLifecycleTerminal(t.Status.LifecycleState) {
			n++
		}
	}
	return n
}
```

- [ ] **Step 4: Gate adoption in `mrScan`**

In `internal/controller/projectscan.go` `mrScan`, inside the `for _, c := range selected` loop, in the `if c.author == bot && bot != ""` branch, **before** building `labelCand`/`srcCand` and calling `createScanTask`:

```go
			if priorTerminalAttempts(existing, c.repo, c.number) >= maxRecoveryAttempts {
				r.Metrics.ScanItem("mrScan", "recovery_exhausted")
				l.Info("mrScan: recovery exhausted; not re-adopting bot PR",
					"action", "scan_recovery_exhausted", "resource_id", proj.Name,
					"repo", c.repo, "pr", c.number, "attempts", priorTerminalAttempts(existing, c.repo, c.number))
				continue
			}
```

- [ ] **Step 5: Run tests**

Run: `go test ./internal/controller/ -run 'TestPriorTerminalAttempts|TestRecoveryBoundThreshold|TestMRScanBotPR' -v`
Expected: PASS (helper tests pass; existing mrScan bot-PR tests unaffected — their fixtures have 0 prior terminal PR tasks).

- [ ] **Step 6: Commit**

```bash
git add internal/controller/projectscan.go internal/controller/projectscan_recovery_bound_test.go
git commit -m "feat(operator): bound bot-PR recovery re-adoption (maxRecoveryAttempts=3)

After N terminal recovery passes on the same PR, mrScan stops re-adopting it
so an unfixable PR no longer re-spawns an agent every scan cycle forever."
```

---

## Task C: Quiet the no-op lifecycle-label log (cosmetic)

**Files:**
- Modify: `internal/controller/labels.go` (`setLifecycleLabel`)
- Test: `internal/controller/lifecycle_label_test.go`

**Why:** `setLifecycleLabel` already gates the `AddLabel` API call (only 7 real calls in 4h), but logs `"lifecycle label set"` on **every** reconcile even when nothing changed — 164 misleading entries/h. Log only when an add/remove actually happened. (NOTE: low priority — purely cosmetic; the lane harm is fixed by Task A. Skippable if time-constrained.)

- [ ] **Step 1: Inspect the existing fake writer/reader test pattern**

Read `internal/controller/lifecycle_label_test.go` and `internal/controller/labels_test.go` to reuse their fake `SCMWriter`/`ReaderFor` (counts `AddLabel`/`RemoveLabel` calls) and `TaskReconciler` wiring. Mirror that harness for the test below (the exact fake type names live in those files).

- [ ] **Step 2: Write the failing test**

Add a test that calls `setLifecycleLabel` twice with a reader reporting the issue already carries exactly `desired`, asserting `AddLabel`/`RemoveLabel` are never called and the "changed" log path is not taken. Use the fake harness from Step 1; assert on the fake writer's call counters:

```go
func TestSetLifecycleLabel_NoOpWhenAlreadyDesired(t *testing.T) {
	// Arrange: reader returns the source issue already labelled exactly `desired`.
	// (Reuse the fake reader/writer from labels_test.go / lifecycle_label_test.go.)
	// Act: call setLifecycleLabel twice with the same desired label.
	// Assert: fakeWriter.addCalls == 0 && fakeWriter.removeCalls == 0.
}
```

- [ ] **Step 3: Run to verify it fails**

Run: `go test ./internal/controller/ -run TestSetLifecycleLabel_NoOpWhenAlreadyDesired -v`
Expected: FAIL — current code still enters the remove loop / logs unconditionally (assert on call counters, not the log).

- [ ] **Step 4: Implement — track whether anything changed**

In `internal/controller/labels.go` `setLifecycleLabel`, introduce a `changed` flag and only log when set:

```go
	changed := false
	if !known || !current[desired] {
		if aerr := writer.AddLabel(ctx, token, issueRef, desired); aerr != nil {
			r.recordSCM(provider, "add_label", aerr)
			return fmt.Errorf("set label add %q: %w", desired, aerr)
		}
		r.recordSCM(provider, "add_label", nil)
		changed = true
	}
	for _, lb := range managed {
		if lb == desired || (known && !current[lb]) {
			continue
		}
		if rerr := writer.RemoveLabel(ctx, token, issueRef, lb); rerr != nil {
			r.recordSCM(provider, "remove_label", rerr)
			l.Info("set label: remove other label failed (non-fatal)",
				"action", "scm_set_label", "resource_id", task.Name, "issue_ref", issueRef, "label", lb, "err", rerr.Error())
			continue
		}
		r.recordSCM(provider, "remove_label", nil)
		changed = true
	}
	if changed {
		l.Info("lifecycle label set", "action", "scm_set_label",
			"resource_id", task.Name, "issue_ref", issueRef, "label", desired)
	}
	return nil
```

- [ ] **Step 5: Run tests**

Run: `go test ./internal/controller/ -run 'TestSetLifecycleLabel|TestLifecycleLabel' -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add internal/controller/labels.go internal/controller/lifecycle_label_test.go
git commit -m "fix(operator): only log lifecycle label set when a label actually changed

Cuts ~160 misleading no-op 'lifecycle label set' logs/hour; no behavior change."
```

---

## Task D: Integrate & deploy via tatara-helmfile (GitOps only)

**Hard rule 15:** deploy ONLY through `tatara-helmfile`. No `kubectl set image`/patch/edit.

- [ ] **Step 1: Final verify in worktree**

Run: `go build ./... && go test ./internal/controller/ -count=1 && golangci-lint run ./... 2>/dev/null || true`
Expected: build + tests PASS. (pre-commit lint gate per repo config runs at commit.)

- [ ] **Step 2: Code review before merge**

REQUIRED SUB-SKILL: `superpowers:requesting-code-review` on the worktree diff. Fix any critical/high findings, then `pre-commit run --all-files`.

- [ ] **Step 3: Merge worktree -> operator `main` (opus merge per hard rule 7)**

Push the branch and open a PR to `tatara-operator` `main`, or fast-forward merge locally on `main`. Branch flow per hard rule 10: develop in worktree, merge to source repo `main`, then build/deploy from `main` only.

```bash
cd ~/Documents/tatara/tatara-operator
git fetch origin main
git checkout main && git reset --hard origin/main   # local main was stale (21 behind)
git merge --no-ff fix/revive-pr-recovery
git push origin main
```

(If a PR is required by branch protection, push `fix/revive-pr-recovery` and open the PR; CI must be green before merge.)

- [ ] **Step 4: Confirm CI built & pushed the image + chart**

After merge, the operator repo CI builds and pushes `harbor.szymonrichert.pl/containers/tatara-operator:<new-main-short-sha>` and the chart (images build via CI, never local buildx).

```bash
gh run list --repo szymonrychu/tatara-operator --branch main --limit 3
# wait for the ci run (build job) to succeed; note the new image tag (short SHA of main HEAD)
git rev-parse --short origin/main
```

- [ ] **Step 5: Bump the operator release in `tatara-helmfile`**

In the `tatara-helmfile` repo, bump BOTH the chart version AND the pinned `image.tag` for the operator release (per the operator-deploy memory — chart-only leaves the old image running). Use the helmfile bump skills if available (`/bump-chart-usage`, `/bump-container-usage`) or edit the operator release `values/<env>` + Chart usage directly. Open an MR; review the `helmfile diff`.

```bash
# in tatara-helmfile working copy
# set operator image.tag = <new-main-short-sha>; bump the operator chart version usage
helmfile -e <env> diff -l name=tatara-operator   # review: image tag + chart version change only
```

- [ ] **Step 6: Apply via the pipeline**

Merge the `tatara-helmfile` MR; the in-cluster ARC runner applies it. Confirm rollout:

```bash
kubectl -n tatara rollout status deploy/tatara-operator --timeout=180s
kubectl -n tatara get deploy tatara-operator -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
# expect the new tag (NOT 14d9ff5)
```

---

## Task E: Verify auto-heal (live)

REQUIRED SUB-SKILL: `superpowers:verification-before-completion`. Evidence before claims.

- [ ] **Step 1: Confirm recovery is picking again**

```bash
( kubectl port-forward -n tatara deploy/tatara-operator 19090:9090 >/dev/null 2>&1 & echo $! >/tmp/pf.pid ); sleep 3
curl -s localhost:19090/metrics | grep 'tatara_scan_items_total{activity="mrScan"'
kill "$(cat /tmp/pf.pid)"
# expect picked > 0 over the next cron cycle(s); skipped_cap drops
```

- [ ] **Step 2: Confirm lane counts dropped**

```bash
kubectl get tasks.tatara.dev -n tatara -o json | jq -r '.items[]
  | select(.spec.kind=="issueLifecycle" or .spec.kind=="review")
  | select((.status.phase//"") as $p | ($p!="Succeeded" and $p!="Failed" and $p!="AwaitingApproval"))
  | select((.status.lifecycleState//"") as $l | ($l!="Done" and $l!="Stopped" and $l!="Parked" and $l!="Conversation"))
  | (.metadata.labels["tatara.io/source-repo"]//"?")' | sort | uniq -c
# expect counts near 0 (only genuinely active tasks remain)
```

- [ ] **Step 3: Watch the 5 stuck PRs reach terminal**

```bash
for r in tatara-operator tatara-claude-code-wrapper tatara-chat; do
  gh pr list --repo szymonrychu/$r --state open --json number,mergeable,title -q '.[] | "'$r'#\(.number) \(.mergeable)"'
done
```
Expected over the next few cron cycles:
- `wrapper#25` (green) -> merged (lands operator#42 metrics work).
- `chat#31` (conflict) -> rebased by agent -> merged (lands the metrics work).
- `operator#50` (dup of merged #46) -> auto-closed by the now-deployed duplicate-merged guard.
- `operator#41` (superseded by #50) / `#39` -> rebased-and-merged or closed.

- [ ] **Step 4: If auto-heal stalls past ~2 cron cycles**

Intervene per the brainstorm decision (only if stalled): inspect the adoption task's lifecycle state + park reason, fix the specific blocker. Do NOT hand-merge as a deploy path; the lifecycle drives the merge.

- [ ] **Step 5: Record outcome**

Update operator `MEMORY.md`/`ROADMAP.md` and the auto-memory `operator-laneoccupancy-starves-recovery-2026-06-15` with the deployed tag + heal results. Clean up the worktree (`git worktree remove`).

---

## Self-Review

- **Spec coverage:** A (laneOccupancy) = Task A; B (egress bound) = Task B; C (hot-loop, re-scoped to cosmetic log) = Task C; D (deploy) = Task D; E (verify auto-heal) = Task E. All spec sections covered. The duplicate-merged guard (#50) ships via the deploy (already in origin/main) — verified in Task E step 3.
- **Placeholder scan:** Task C step 2 references the existing fake-writer harness by responsibility (counts AddLabel/RemoveLabel) rather than inlining unknown fake type names — Step 1 reads them first; the assertion (call counters == 0) is concrete.
- **Type consistency:** `laneOccupancy`, `isLifecycleTerminal`, `sanitizeRepoLabel`, `labelSourceRepo`, `priorTerminalAttempts`, `maxRecoveryAttempts`, `r.Metrics.ScanItem` match across tasks and existing code.
- **Scope:** single operator code change set + one helmfile deploy — one plan.
