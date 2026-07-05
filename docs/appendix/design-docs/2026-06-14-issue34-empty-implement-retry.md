# Issue #34: Empty Implement Run -> Retry-then-Escalate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development per task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** An issueLifecycle Implement turn that produces zero commits must no longer silently park as benign `no-change` (fix never lands). Instead: retry up to 2x with a re-entry prompt, then comment on the issue and park with a distinct `implement-empty` reason. Plus the wrapper stops pushing empty task branches.

**Architecture:** Defense-in-depth across two independent repos.
- **tatara-claude-code-wrapper** (root, cleanliness): `CommitAndPush` skips `git push` when the working tree is clean, so no empty `tatara/task-*` branches accumulate on the remote.
- **tatara-operator** (core): `finishImplement`'s no-PR branch becomes retry-then-escalate for implement-lifecycle tasks, gated on a new `Status.ImplementEmptyRetries` counter (cap 2). `finishImplement` is already the implement-only terminal path (report/question/verify tasks reach writeback via `doWriteBack`, not here), so the guard is correctly scoped with no kind check needed.

**Tech Stack:** Go 1.24, controller-runtime, envtest (operator); Go, fake `GitRunner` (wrapper).

**Decisions pinned (user, 2026-06-14):**
- Empty-implement policy: **retry then park+comment, cap 2** (counter on `Task.Status`).
- Empty branch push: **skip push when no commit**.

**Explicitly NOT doing (KISS / YAGNI - record in MEMORY):**
- No `SCMWriter.CompareCommits` interface method. The existing no-PR detection (every repo's `OpenChange` returns a 4xx when the head branch is empty *or* absent -> `len(prURLs)==0` -> `PrURL==""`) already detects an empty run authoritatively, for both old-wrapper (empty branch pushed) and new-wrapper (branch absent after skip-push). Adding an interface method + GitHub + GitLab impls + fakes buys only the avoidance of one already-handled failing API call. Not worth the surface.
- No wrapper `noChange` webhook field. The operator detects no-PR independently; plumbing a wrapper signal through the turn record + callback payload would be redundant.

---

## Repo A: tatara-claude-code-wrapper

### Task A1: CommitAndPush skips push on a clean tree

**Files:**
- Modify: `internal/bootstrap/repo.go:32-47`
- Test: `internal/bootstrap/repo_test.go` (new file)

- [ ] **Step 1: Write the failing test** (`internal/bootstrap/repo_test.go`)

```go
package bootstrap

import (
	"testing"

	"github.com/stretchr/testify/require"
)

// recordingGit records every git invocation and lets the test script the
// exit of `diff --cached --quiet` (clean tree = exit 0 = nil error).
type recordingGit struct {
	calls     [][]string
	treeDirty bool // when true, `diff --cached --quiet` returns an error (staged changes)
}

func (g *recordingGit) run(dir string, args ...string) error {
	g.calls = append(g.calls, args)
	if len(args) >= 2 && args[0] == "diff" && args[1] == "--cached" {
		if g.treeDirty {
			return errExit
		}
		return nil
	}
	return nil
}

var errExit = &gitExitError{}

type gitExitError struct{}

func (*gitExitError) Error() string { return "exit status 1" }

func didCall(calls [][]string, verb string) bool {
	for _, c := range calls {
		if len(c) > 0 && c[0] == verb {
			return true
		}
	}
	return false
}

func TestCommitAndPush_CleanTree_SkipsCommitAndPush(t *testing.T) {
	g := &recordingGit{treeDirty: false}
	err := CommitAndPush("/repo", "tatara/task-x", "msg", g.run)
	require.NoError(t, err)
	require.True(t, didCall(g.calls, "add"), "must always stage")
	require.False(t, didCall(g.calls, "commit"), "clean tree must not commit")
	require.False(t, didCall(g.calls, "push"), "clean tree must not push (no empty branch)")
}

func TestCommitAndPush_DirtyTree_CommitsAndPushes(t *testing.T) {
	g := &recordingGit{treeDirty: true}
	err := CommitAndPush("/repo", "tatara/task-x", "msg", g.run)
	require.NoError(t, err)
	require.True(t, didCall(g.calls, "commit"), "dirty tree must commit")
	require.True(t, didCall(g.calls, "push"), "dirty tree must push")
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Documents/tatara/tatara-claude-code-wrapper && go test ./internal/bootstrap/ -run TestCommitAndPush -v`
Expected: `TestCommitAndPush_CleanTree_SkipsCommitAndPush` FAILS (current code pushes unconditionally).

- [ ] **Step 3: Edit `CommitAndPush`** (`internal/bootstrap/repo.go`)

Replace the body (keep the doc comment but update the last sentence):

```go
// CommitAndPush stages all changes, and when something is staged commits and
// pushes the branch to origin. A clean tree is left untouched: nothing is
// committed and nothing is pushed, so no empty remote branch is created.
func CommitAndPush(dir, branch, message string, git GitRunner) error {
	if err := git(dir, "add", "-A"); err != nil {
		return err
	}
	// `diff --cached --quiet` exits zero (nil) when the tree is clean.
	if git(dir, "diff", "--cached", "--quiet") == nil {
		return nil
	}
	if err := git(dir, "commit", "-m", message); err != nil {
		return err
	}
	return git(dir, "push", "-u", "origin", branch)
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `go test ./internal/bootstrap/ -run TestCommitAndPush -v` then `go test ./...`
Expected: PASS, all green.

- [ ] **Step 5: gofmt + go vet**

Run: `gofmt -l internal/bootstrap/repo.go internal/bootstrap/repo_test.go && go vet ./internal/bootstrap/`

- [ ] **Step 6: Commit**

```bash
git add internal/bootstrap/repo.go internal/bootstrap/repo_test.go
git commit -m "fix: skip empty task-branch push when tree is clean (#34)"
```

---

## Repo B: tatara-operator

### Task B1: Add `ImplementEmptyRetries` status field

**Files:**
- Modify: `api/v1alpha1/task_types.go:162` (after `ImplementContext`)
- Regenerate: CRD manifests + deepcopy

- [ ] **Step 1: Add the field** after `ImplementContext` (line 162), before `PendingComments`:

```go
	// ImplementEmptyRetries counts consecutive Implement runs that finished
	// with zero commits (no PR opened). Bounded retry guard: after the cap the
	// task is commented + parked with reason "implement-empty" instead of
	// silently parked as a benign no-change. Reset to 0 when a run opens a PR.
	// +optional
	ImplementEmptyRetries int `json:"implementEmptyRetries,omitempty"`
```

- [ ] **Step 2: Regenerate**

Run: `cd ~/Documents/tatara/tatara-operator && make generate manifests`
Expected: `zz_generated.deepcopy.go` unchanged (int is a value field), CRD yaml gains `implementEmptyRetries`. No error.

- [ ] **Step 3: Build**

Run: `go build ./...`
Expected: success.

### Task B2: `finishImplement` retry-then-escalate on empty run

**Files:**
- Modify: `internal/controller/lifecycle.go:898-905` (the `PrURL == ""` block) and `:888` success path (reset counter)
- Test: `internal/controller/lifecycle_implement_empty_test.go` (new) or append to existing implement lifecycle test file

**Design of the new no-PR block** (replaces lines 898-905):

```go
	if fresh.Status.PrURL == "" {
		// Implement run produced no commit -> no PR. This is a failure to
		// deliver (report/question tasks never reach finishImplement), not a
		// benign no-change. Retry with a re-entry nudge up to the cap, then
		// comment on the issue and park with a distinct reason.
		const emptyRetryCap = 2
		if fresh.Status.ImplementEmptyRetries < emptyRetryCap {
			if err := r.bumpImplementEmptyRetries(ctx, fresh); err != nil {
				return ctrl.Result{}, err
			}
			if err := r.setImplementContext(ctx, fresh, emptyImplementReentryPrompt); err != nil {
				return ctrl.Result{}, err
			}
			l.Info("implement: no commit produced; retrying with re-entry nudge",
				"action", "lifecycle_implement_empty_retry", "resource_id", task.Name,
				"attempt", fresh.Status.ImplementEmptyRetries, "cap", emptyRetryCap)
			// resetAgentRun clears phase to "" and leaves LifecycleState=Implement,
			// so the next reconcile re-spawns the Implement run with ImplementContext.
			return ctrl.Result{}, r.resetAgentRun(ctx, fresh)
		}
		l.Info("implement: no commit after retry cap; commenting + parking",
			"action", "lifecycle_implement_empty_parked", "resource_id", task.Name)
		if _, _, writer, token, _, scmErr := r.scmContext(ctx, fresh); scmErr == nil &&
			fresh.Spec.Source != nil && fresh.Spec.Source.IssueRef != "" {
			msg := "The implement agent produced no change after " +
				strconv.Itoa(emptyRetryCap) + " attempts. Leaving this for a human - " +
				"the fix may be unclear, blocked, or already present."
			_ = writer.Comment(ctx, token, fresh.Spec.Source.IssueRef, msg)
		}
		if err := r.setLifecycleState(ctx, fresh, "Parked", "implement-empty"); err != nil {
			return ctrl.Result{}, err
		}
		return ctrl.Result{}, r.resetAgentRun(ctx, fresh)
	}
	// PR opened: clear any prior empty-retry count so a later re-entry into
	// Implement starts fresh.
	if fresh.Status.ImplementEmptyRetries > 0 {
		if err := r.bumpImplementEmptyRetries(ctx, fresh, 0); err != nil {
			l.Error(err, "implement: reset empty-retry counter (non-fatal)", "resource_id", task.Name)
		}
	}
```

(Adjust `bumpImplementEmptyRetries` signature to your taste - see helper below. The plan uses an increment helper and a separate reset; collapse into one variadic-set helper if cleaner. Keep `strconv` import - already present in writeback.go but check lifecycle.go imports.)

**Helper + constant** (add near `setImplementContext`, ~line 1145):

```go
// emptyImplementReentryPrompt nudges a re-spawned Implement agent that produced
// no diff on the prior turn to either deliver the change or stop and explain.
const emptyImplementReentryPrompt = "Your previous attempt finished without " +
	"committing any change, so no PR could be opened and the issue is still " +
	"open. Re-read the issue and the repository, then EITHER implement the fix " +
	"and commit it, OR if no code change is genuinely needed, state clearly why " +
	"in your final summary so a human can close the issue."

// setImplementEmptyRetries persists Status.ImplementEmptyRetries via RetryOnConflict.
func (r *TaskReconciler) setImplementEmptyRetries(ctx context.Context, task *tatarav1alpha1.Task, n int) error {
	if err := retry.RetryOnConflict(retry.DefaultRetry, func() error {
		fresh := &tatarav1alpha1.Task{}
		if err := r.Get(ctx, client.ObjectKeyFromObject(task), fresh); err != nil {
			return err
		}
		fresh.Status.ImplementEmptyRetries = n
		if err := r.Status().Update(ctx, fresh); err != nil {
			return err
		}
		task.Status.ImplementEmptyRetries = n
		return nil
	}); err != nil {
		return fmt.Errorf("setImplementEmptyRetries: %w", err)
	}
	return nil
}
```

In the block above, replace `bumpImplementEmptyRetries(ctx, fresh)` with `setImplementEmptyRetries(ctx, fresh, fresh.Status.ImplementEmptyRetries+1)` and the reset with `setImplementEmptyRetries(ctx, fresh, 0)`. Single helper, no overloads.

- [ ] **Step 1: Write the failing tests** (`internal/controller/lifecycle_implement_empty_test.go`)

Cover three behaviors with the existing envtest + fake SCM harness (model on the existing lifecycle implement tests - find the helper that seeds a lifecycle task in `LifecycleState=Implement`, `Phase=Succeeded`, no task branch, fake writer returning a 422 from `OpenChange`):

1. `TestFinishImplement_EmptyRun_FirstRetry`: empty run (OpenChange 422, PrURL stays ""), `ImplementEmptyRetries` starts 0 -> after `finishImplement`: counter==1, `ImplementContext` set to the re-entry prompt, `LifecycleState` still "Implement" (NOT Parked), `Phase==""` (reset). No issue comment posted yet.
2. `TestFinishImplement_EmptyRun_ParksAtCap`: counter pre-set to 2 -> after `finishImplement`: `LifecycleState=="Parked"`, a comment WAS posted to the issue containing "no change", counter unchanged or irrelevant.
3. `TestFinishImplement_PROpened_ResetsCounter`: counter pre-set to 1, fake writer returns a real PR URL -> after `finishImplement`: PrURL set, transitions toward MRCI, `ImplementEmptyRetries==0`.

Write the assertions first using the real `finishImplement` entry point (reconcile the lifecycle task, or call `finishImplement` directly if the test harness allows - check how existing lifecycle tests drive it).

- [ ] **Step 2: Run tests to verify they fail**

Run: `make test` (needs `KUBEBUILDER_ASSETS`; bare `go test` fails on envtest).
Expected: the three new tests FAIL (current code parks `no-change` on first empty run, never retries, never comments).

- [ ] **Step 3: Implement** the new no-PR block + helper + constant above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `make test`
Expected: all green, including the existing implement-lifecycle and writeback tests (regression: the goalecho no-goal-echo tests must still pass).

- [ ] **Step 5: gofmt + go vet**

Run: `gofmt -l internal/controller/ api/v1alpha1/ && go vet ./internal/controller/`

- [ ] **Step 6: Commit**

```bash
git add api/v1alpha1/ internal/controller/ config/crd/
git commit -m "fix: retry-then-escalate empty implement runs instead of silent no-change park (#34)"
```

---

## Integration / Verification

- [ ] Operator: `make test` fully green; `go build ./...` clean.
- [ ] Wrapper: `go test ./...` fully green.
- [ ] requesting-code-review on both diffs; fix critical/high findings.
- [ ] Merge each repo's branch to its own `main` (separate PRs; no cross-repo merge).
- [ ] Deploy via tatara-helmfile (hard rule 15): operator image+chart bump after CI; wrapper image bump in the Project CR `agent.image` after CI. Verify bot token unchanged.
- [ ] Close issue #34 referencing both PRs.
- [ ] MEMORY: record the empty-implement retry design + the explicit decision to NOT add CompareCommits.
