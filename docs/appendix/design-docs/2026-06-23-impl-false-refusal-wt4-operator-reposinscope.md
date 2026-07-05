# Cross-Repo Declarative Scope (ReposInScope) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make cross-repo implement scope declarative on the Task (optional `Spec.ReposInScope`), inject it into the implement prompt, and warn on the source issue when an in-scope repo produced no branch instead of silently skipping it.
**Architecture:** Add an optional `[]string` field to `TaskSpec` (absent = today's single-repo behavior, no regression), regenerate the CRD, surface the list in `implementPrompt` so the agent edits every in-scope repo, and in `writeBackOpenChange` post a WARNING comment for each in-scope repo whose branch yielded no commits. No atomicity: other MRs still open. This is WT-4 of the helmfile#8 false-refusal fix; it merges LAST in operator order (after WT-1 Defect B and WT-2 `already_done` enum), so rebase onto the merged WT-2 CRD before regenerating.
**Tech Stack:** Go (stdlib log/slog, controller-runtime for operator), table-driven tests with t.Run, golangci-lint, gofmt.

## Global Constraints
- Newest stable Go; KISS; no tech-debt; JSON logs (log/slog); business actions logged at INFO with structured fields; metrics for anything that counts/times-out/fails.
- TDD strictly: failing test first, run it red, minimal impl, run green, commit. Conventional commits (feat:/fix:/refactor:/test:). Frequent commits.
- Operator CRD changes: regenerate with controller-gen via `make generate` (deepcopy) and `make manifests` (CRD yaml into the chart `crd-bases/`); templated CRDs apply on helm upgrade.
- Run tests via mise (`mise exec -- go test ./...` or `mise run test`); lint via `mise exec -- golangci-lint run`.

---

## Repo facts grounded in the current code (read these before starting)

- Repo root: `/Users/szymonri/Documents/tatara/tatara-operator`.
- `TaskSpec` lives in `api/v1alpha1/task_types.go` (NOT `types.go`; the spec said `types.go` but no such file exists - trust the code). The struct is at `task_types.go:148-169`. The CRD `Kind` enum and existing `+optional`/`+kubebuilder` markers there are the pattern to mirror.
- `WT-2` adds the `already_done` enum to `ImplementOutcome.Action` (`task_types.go:63`). Both WT-2 and this slice touch the CRD; WT-2 merges before WT-4. After rebasing onto merged WT-2, run `make manifests` once so the regenerated CRD contains BOTH the `already_done` enum and the new `reposInScope` field.
- `implementPrompt(task)` is in `internal/controller/lifecycle.go:1355-1378`. It calls `planTurnText(task.Spec.Goal, taskBranch(task), task.Spec.ProjectRef, task.Name)` (defined in `internal/controller/turnloop.go:20-36`) then appends the decline instruction and lifecycle guidance. We inject the in-scope block here.
- `writeBackOpenChange` is in `internal/controller/writeback.go:74-286`. The ordered-repos loop is `writeback.go:146-213`. The no-commit skip is the `skipReason == "no-change"` branch at `writeback.go:171-174`. `ordered[0]` is the primary repo; the rest are the other Project repos. Repos are `tatarav1alpha1.Repository`; `repo.Name` is the CR name (e.g. `tatara-cli`), `repo.Spec.URL` the git URL.
- The SCM writer interface is `scm.SCMWriter` (alias `Writer` in `writeback.go:28`). `writer.Comment(ctx, token, issueRef, body)` posts an issue comment (signature confirmed in `writeback.go:228`, `lifecycle.go:1339`). `task.Spec.Source.IssueRef` is the comment target (e.g. `o/r#7`).
- `r.Metrics.WritebackOutcome(result string)` (`internal/obs/operator_metrics.go:545`) uses `WithLabelValues`, so a NEW label value needs NO metric code change. `WritebackOutcomeCounter(result)` (line 550) returns the counter for test assertions. Existing values: `no_change`, `skip_4xx`, `no_pr`, `opened`. We add `in_scope_no_branch`.
- Test harness for writeback: `internal/controller/task_writeback_test.go`. `fakeWriter` (lines 23-50) records `commentArgs []string` as `issueRef+"|"+body`. `newWriteBackReconciler(t, fw)` builds the reconciler; `seedWritebackPending(t, name, scmSecret, project, repo)` seeds a Succeeded+WritebackPending Task with one repo and `Source.IssueRef = "o/r#7"`; `reconcileWriteback(t, r, name)` drives one reconcile. Mirror these.
- `implementPrompt` has a focused unit test file `internal/controller/lifecycle_implement_*_test.go`; the existing `lifecycle_implement_refusal_test.go` tests the decline instruction string. Mirror its plain-function-call style (call `implementPrompt(task)` directly, assert on the returned string).
- CRD chart dir target var is `CHART_CRD_DIR` in the `Makefile`; `make manifests` writes there. `make generate` writes `zz_generated.deepcopy.go`. A `[]string` field needs deepcopy regen (slices are deep-copied) - run BOTH.

---

## Task 1: Add optional `Spec.ReposInScope []string` to the Task CRD

**Interfaces**
- Produces: `tatarav1alpha1.TaskSpec.ReposInScope []string` (JSON `reposInScope`, `+optional`). Absent/nil = single-repo behavior.
- Consumes: nothing new.

Steps:

- [ ] Write the failing test. Append to `api/v1alpha1/task_types_test.go` (it exists; open it to match the existing package + import style, then add this test):

```go
func TestTaskSpecReposInScope(t *testing.T) {
	// Absent field defaults to nil (single-repo behavior, no regression).
	var empty TaskSpec
	if empty.ReposInScope != nil {
		t.Fatalf("zero-value ReposInScope = %v, want nil", empty.ReposInScope)
	}

	// Populated field round-trips and deep-copies independently.
	spec := TaskSpec{
		ProjectRef:    "proj",
		RepositoryRef: "tatara-helmfile",
		Goal:          "fix #8",
		ReposInScope:  []string{"tatara-helmfile", "terraform", "ansible"},
	}
	task := &Task{Spec: spec}
	cp := task.DeepCopy()
	cp.Spec.ReposInScope[0] = "mutated"
	if task.Spec.ReposInScope[0] != "tatara-helmfile" {
		t.Fatalf("DeepCopy did not isolate ReposInScope: original mutated to %q", task.Spec.ReposInScope[0])
	}
	if len(cp.Spec.ReposInScope) != 3 {
		t.Fatalf("DeepCopy lost elements: got %d, want 3", len(cp.Spec.ReposInScope))
	}
}
```

- [ ] Run it red:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./api/v1alpha1/ -run TestTaskSpecReposInScope
```

Expected failure: compile error `spec.ReposInScope undefined (type TaskSpec has no field or method ReposInScope)`.

- [ ] Minimal impl. Edit `api/v1alpha1/task_types.go`. Add the field to `TaskSpec` (place it right after `ProposedIssue` at the end of the struct, before the closing `}` at line 169):

```go
	// +optional
	ProposedIssue *ProposedIssueSpec `json:"proposedIssue,omitempty"`
	// ReposInScope is the optional declarative list of Project Repository CR
	// names this Task is expected to change. When set, the implement prompt tells
	// the agent the issue spans these repos and writeback posts a WARNING comment
	// for any in-scope repo whose branch produced no commits, instead of skipping
	// it silently. Absent/empty = single-repo behavior (primary repo only), so
	// existing Tasks are unaffected.
	// +optional
	ReposInScope []string `json:"reposInScope,omitempty"`
```

- [ ] Regenerate deepcopy + CRD (a new slice field requires both):

```
cd /Users/szymonri/Documents/tatara/tatara-operator && make generate && make manifests
```

This updates `api/v1alpha1/zz_generated.deepcopy.go` (adds the `ReposInScope` copy in `func (in *TaskSpec) DeepCopyInto`) and the CRD yaml under `CHART_CRD_DIR`. Verify the generated CRD gained the property:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && grep -rn "reposInScope" $(grep -E '^CHART_CRD_DIR' Makefile | head -1 | sed 's/.*=//; s/[[:space:]]//g')
```

Expected: at least one hit showing `reposInScope:` with `type: array` / `items: type: string`.

- [ ] Run it green:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./api/v1alpha1/ -run TestTaskSpecReposInScope
```

Expected: PASS.

- [ ] Lint + commit:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- golangci-lint run ./api/... && git add api/v1alpha1/task_types.go api/v1alpha1/task_types_test.go api/v1alpha1/zz_generated.deepcopy.go && git add -A $(grep -E '^CHART_CRD_DIR' Makefile | head -1 | sed 's/.*=//; s/[[:space:]]//g') && git commit -m "feat: add optional Task.Spec.ReposInScope for declarative cross-repo scope

Claude-Session: https://claude.ai/code/session_01PCbgVuC4LyPv15Z5S4wz7Z"
```

---

## Task 2: Inject the in-scope repo list into the implement prompt

**Interfaces**
- Consumes: `tatarav1alpha1.TaskSpec.ReposInScope []string` (Task 1).
- Produces: `implementPrompt(task *tatarav1alpha1.Task) string` now contains a "This issue spans repos: X, Y, Z" line when `ReposInScope` is non-empty; unchanged when empty (no regression).

Steps:

- [ ] Write the failing test. Create `internal/controller/lifecycle_implement_scope_test.go`:

```go
package controller

import (
	"strings"
	"testing"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
)

func TestImplementPromptReposInScope(t *testing.T) {
	t.Run("absent field omits the spans-repos block", func(t *testing.T) {
		task := &tatarav1alpha1.Task{
			Spec: tatarav1alpha1.TaskSpec{
				ProjectRef:    "proj",
				RepositoryRef: "tatara-helmfile",
				Goal:          "fix #8",
			},
		}
		task.Name = "scan-x"
		got := implementPrompt(task)
		if strings.Contains(got, "spans repos") {
			t.Fatalf("single-repo task must not mention spans repos; got:\n%s", got)
		}
	})

	t.Run("populated field injects every in-scope repo", func(t *testing.T) {
		task := &tatarav1alpha1.Task{
			Spec: tatarav1alpha1.TaskSpec{
				ProjectRef:    "proj",
				RepositoryRef: "tatara-helmfile",
				Goal:          "fix #8",
				ReposInScope:  []string{"tatara-helmfile", "terraform", "ansible"},
			},
		}
		task.Name = "scan-x"
		got := implementPrompt(task)
		if !strings.Contains(got, "This issue spans repos: tatara-helmfile, terraform, ansible") {
			t.Fatalf("missing spans-repos line; got:\n%s", got)
		}
		if !strings.Contains(got, "Edit and push every repo you change") {
			t.Fatalf("missing edit-and-push directive; got:\n%s", got)
		}
	})
}
```

- [ ] Run it red:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run TestImplementPromptReposInScope
```

Expected failure: subtest "populated field..." fails because the spans-repos line is absent (the "absent" subtest already passes).

- [ ] Minimal impl. Edit `internal/controller/lifecycle.go`. In `implementPrompt` (line 1355), insert the in-scope block right after the decline instruction and before `base += lifecyclePhaseGuidance("Implement")` (line 1363). Replace:

```go
	base += "\n\n**IMPORTANT:** If after investigation you will NOT implement this " +
		"issue, you MUST call `decline_implementation` with a clear reason (what you " +
		"considered and why it should not / need not be done). A silent finish with no " +
		"PR and no `decline_implementation` call is NOT allowed and will be re-prompted."
	base += lifecyclePhaseGuidance("Implement")
```

with:

```go
	base += "\n\n**IMPORTANT:** If after investigation you will NOT implement this " +
		"issue, you MUST call `decline_implementation` with a clear reason (what you " +
		"considered and why it should not / need not be done). A silent finish with no " +
		"PR and no `decline_implementation` call is NOT allowed and will be re-prompted."
	if len(task.Spec.ReposInScope) > 0 {
		base += "\n\n**This issue spans repos: " + strings.Join(task.Spec.ReposInScope, ", ") +
			".** Edit and push every repo you change; each repo with a change gets its own PR/MR. " +
			"If a listed repo genuinely needs no change, say so explicitly in your result summary."
	}
	base += lifecyclePhaseGuidance("Implement")
```

- [ ] Confirm `strings` is already imported in `lifecycle.go` (it is used throughout, e.g. `strings.TrimSpace`). If `mise exec -- go build` reports it missing, add `"strings"` to the import block; do not add it blindly.

- [ ] Run it green:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run TestImplementPromptReposInScope
```

Expected: PASS (both subtests).

- [ ] Lint + commit:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- golangci-lint run ./internal/controller/ && git add internal/controller/lifecycle.go internal/controller/lifecycle_implement_scope_test.go && git commit -m "feat: inject ReposInScope list into the implement prompt

Claude-Session: https://claude.ai/code/session_01PCbgVuC4LyPv15Z5S4wz7Z"
```

---

## Task 3: Warn on the issue when an in-scope repo produced no branch

**Interfaces**
- Consumes: `tatarav1alpha1.TaskSpec.ReposInScope []string` (Task 1); `scm.SCMWriter.Comment(ctx, token, issueRef, body)`; `r.Metrics.WritebackOutcome(string)`.
- Produces: in `writeBackOpenChange`, for every repo in `ReposInScope` whose OpenChange returned a `no-change` (422 no commits) skip, a WARNING issue comment is posted (best-effort, non-fatal) and `WritebackOutcome("in_scope_no_branch")` incremented. Other repos' MRs still open (no atomicity). Out-of-scope repos with no branch keep today's silent `no_change` behavior.

Design note: the existing loop classifies a no-commit repo as `skipReason == "no-change"` at `writeback.go:171`. We collect the names of in-scope repos that hit `no-change` into a slice during the loop, then after the loop (regardless of whether any PR opened) post one warning comment per missed in-scope repo. We do NOT block the PRs that did open. A repo is "in scope" if `repo.Name` is in `task.Spec.ReposInScope` (CR-name match; the prompt and the Project repo list both use CR names).

Steps:

- [ ] Write the failing test. Append to `internal/controller/task_writeback_test.go`. This seeds a single-repo Task whose only repo is in scope but whose branch has no commits (fakeWriter returns 422 "No commits"), and asserts a warning comment was posted:

```go
func TestWriteback_InScopeRepoNoBranchWarns(t *testing.T) {
	// In-scope repo produced no commits (422 No commits) -> must warn on the issue,
	// not skip silently.
	fw := &fakeWriter{openErr: &scm.HTTPError{Status: 422, Body: "No commits between main and tatara/task-x", Path: "/pulls"}}
	r := newWriteBackReconciler(t, fw)
	task := seedWritebackPending(t, "wb-inscope", "wb-scm-inscope", "wb-proj-inscope", "wb-repo-inscope")

	// Mark the single repo in scope.
	task.Spec.ReposInScope = []string{"wb-repo-inscope"}
	require.NoError(t, k8sClient.Update(context.Background(), task))

	_, err := reconcileWriteback(t, r, task.Name)
	require.NoError(t, err)

	fw.mu.Lock()
	defer fw.mu.Unlock()
	var warned bool
	for _, c := range fw.commentArgs {
		if strings.Contains(c, "o/r#7|") && strings.Contains(c, "wb-repo-inscope") && strings.Contains(strings.ToLower(c), "warning") {
			warned = true
		}
	}
	require.True(t, warned, "in-scope repo with no branch must produce a WARNING comment; got %v", fw.commentArgs)
}

func TestWriteback_OutOfScopeRepoNoBranchSilent(t *testing.T) {
	// Repo with no commits but NOT in scope -> keep today's silent no_change skip
	// (no warning comment beyond the existing report-only result comment).
	fw := &fakeWriter{openErr: &scm.HTTPError{Status: 422, Body: "No commits between main and tatara/task-x", Path: "/pulls"}}
	r := newWriteBackReconciler(t, fw)
	task := seedWritebackPending(t, "wb-outscope", "wb-scm-outscope", "wb-proj-outscope", "wb-repo-outscope")
	// ReposInScope left nil.

	_, err := reconcileWriteback(t, r, task.Name)
	require.NoError(t, err)

	fw.mu.Lock()
	defer fw.mu.Unlock()
	for _, c := range fw.commentArgs {
		if strings.Contains(strings.ToLower(c), "warning") {
			t.Fatalf("out-of-scope no-branch repo must not warn; got comment %q", c)
		}
	}
}
```

- [ ] Ensure `"strings"` is imported in `task_writeback_test.go`. Open the import block (top of the file, lines 3-21); it currently does NOT import `strings`. Add `"strings"` to the stdlib group (alphabetical, after `"sync"`):

```go
import (
	"context"
	"strings"
	"sync"
	"testing"
```

- [ ] Run it red:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run 'TestWriteback_InScopeRepoNoBranchWarns|TestWriteback_OutOfScopeRepoNoBranchSilent'
```

Expected failure: `TestWriteback_InScopeRepoNoBranchWarns` fails (no warning comment posted - the in-scope repo is silently skipped today). `TestWriteback_OutOfScopeRepoNoBranchSilent` should already pass.

- [ ] Minimal impl. Edit `internal/controller/writeBackOpenChange` in `internal/controller/writeback.go`.

First, add an in-scope lookup set and a missed-repo accumulator. Immediately after the `var prURLs []string` / `var lastSkipStatus int` declarations (line 144-145), insert:

```go
	var prURLs []string
	var lastSkipStatus int
	// inScope is the declarative cross-repo scope (CR names). When a repo in this
	// set produces no branch (422 no commits) we warn on the issue instead of
	// skipping silently (Defect A1).
	inScope := make(map[string]bool, len(task.Spec.ReposInScope))
	for _, name := range task.Spec.ReposInScope {
		inScope[name] = true
	}
	var inScopeNoBranch []string
```

Then, in the `skipReason == "no-change"` branch (line 171-174), record the missed in-scope repo. Replace:

```go
				if skipReason == "no-change" {
					l.Info("writeback: implement produced no changes (branch has no commits)",
						"action", "writeback_no_change", "repo", repo.Name, "task", task.Name, "branch", sourceBranch)
					r.Metrics.WritebackOutcome("no_change")
				} else if skipReason == "already-exists" {
```

with:

```go
				if skipReason == "no-change" {
					if inScope[repo.Name] {
						l.Info("writeback: in-scope repo produced no commits; will warn on issue",
							"action", "writeback_in_scope_no_branch", "repo", repo.Name, "task", task.Name, "branch", sourceBranch)
						inScopeNoBranch = append(inScopeNoBranch, repo.Name)
						r.Metrics.WritebackOutcome("in_scope_no_branch")
					} else {
						l.Info("writeback: implement produced no changes (branch has no commits)",
							"action", "writeback_no_change", "repo", repo.Name, "task", task.Name, "branch", sourceBranch)
						r.Metrics.WritebackOutcome("no_change")
					}
				} else if skipReason == "already-exists" {
```

Finally, post the warning comment(s) after the loop. The loop ends at line 213 (closing `}` of `for _, repo := range ordered`). Immediately after that closing brace (before the `if len(prURLs) == 0 {` block at line 215), insert:

```go
	// Warn on the source issue for any in-scope repo that yielded no branch.
	// Best-effort and non-fatal: other repos' MRs still open (no atomicity, KISS).
	if len(inScopeNoBranch) > 0 && task.Spec.Source != nil && task.Spec.Source.IssueRef != "" {
		warnBody := "WARNING: this issue was declared to span repos that produced no change. " +
			"The following in-scope repo(s) had no commits on branch `" + sourceBranch + "` and got no PR/MR: " +
			strings.Join(inScopeNoBranch, ", ") + ". " +
			"If those repos genuinely need no change this is expected; otherwise the cross-repo edit was lost - re-run or fix manually."
		werr := writer.Comment(ctx, token, task.Spec.Source.IssueRef, warnBody)
		r.recordSCM(provider, "comment", werr)
		if werr != nil {
			l.Error(werr, "writeback: in-scope no-branch warning comment (non-fatal)",
				"action", "writeback_in_scope_warn_failed", "issue_ref", task.Spec.Source.IssueRef, "repos", strings.Join(inScopeNoBranch, ","))
		}
	}
```

`strings` and `r.recordSCM`/`writer`/`token`/`provider`/`l` are all already in scope in this function (no new imports). `strings` is imported at `writeback.go:9`.

- [ ] Run it green:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run 'TestWriteback_InScopeRepoNoBranchWarns|TestWriteback_OutOfScopeRepoNoBranchSilent'
```

Expected: PASS (both).

- [ ] Regression check the rest of writeback (the warning path must not perturb existing flows):

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run 'TestTaskWriteBack|TestWriteback'
```

Expected: PASS.

- [ ] Lint + commit:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- golangci-lint run ./internal/controller/ && git add internal/controller/writeback.go internal/controller/task_writeback_test.go && git commit -m "feat: warn on issue when an in-scope repo produces no branch in writeback

Claude-Session: https://claude.ai/code/session_01PCbgVuC4LyPv15Z5S4wz7Z"
```

---

## Task 4: Code review + verification before completion

**Interfaces**
- Consumes: the three prior commits (CRD field, prompt injection, writeback warning).
- Produces: a verified, lint-clean, fully-tested WT-4 slice on the branch, ready for the opus merge into operator `main`.

Steps:

- [ ] Run the full operator test suite (not just the touched packages) to catch cross-package breakage from the CRD regen:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./...
```

Expected: all PASS. If the envtest-backed `internal/controller` suite needs the CRD installed, the regenerated CRD under `CHART_CRD_DIR` is what `suite_test.go` loads - confirm it includes `reposInScope` (already grepped in Task 1).

- [ ] Lint the whole module:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- golangci-lint run
```

Expected: no findings.

- [ ] gofmt check (must be clean):

```
cd /Users/szymonri/Documents/tatara/tatara-operator && gofmt -l api/v1alpha1/task_types.go internal/controller/lifecycle.go internal/controller/writeback.go internal/controller/lifecycle_implement_scope_test.go internal/controller/task_writeback_test.go
```

Expected: empty output (no unformatted files).

- [ ] Confirm the deepcopy regen is committed and consistent (no drift):

```
cd /Users/szymonri/Documents/tatara/tatara-operator && make generate && make manifests && git status --porcelain
```

Expected: empty (nothing changed since the commits; if anything appears, the generated artifacts were not committed - commit them with `chore: regenerate CRD + deepcopy`).

- [ ] Invoke `superpowers:requesting-code-review` on the full WT-4 diff (`git diff main...HEAD` or the worktree branch range). Fix every critical/high finding, re-run the touched tests, and re-commit.

- [ ] Invoke `superpowers:verification-before-completion`: paste the actual green output of `mise exec -- go test ./...` and `mise exec -- golangci-lint run` as evidence before declaring the slice done. Do not claim done without that output.

- [ ] Merge-order reminder (do NOT skip): this is WT-4, the LAST operator slice. Before the opus merge to operator `main`, rebase onto the merged WT-2 (`already_done`) commit, then re-run `make manifests` so the single regenerated CRD carries BOTH the `already_done` enum and `reposInScope`. Re-run `mise exec -- go test ./...` post-rebase. Do NOT build or deploy from a worktree (hard-rule 10); the operator image and the tatara-helmfile dual-pin bump happen from `main` after all three operator slices land.

---

## Notes for the orchestrator (spec-vs-code divergences)

- Spec says the field goes in `api/v1alpha1/types.go`; that file does not exist. `TaskSpec` is in `api/v1alpha1/task_types.go`. Plan targets the real file.
- Spec lists `task_types.go:176-182` for `TaskTerminal` and `:63` for the enum - both confirmed accurate.
- Population of `ReposInScope`: the spec's A1 only requires the field + plumbing + prompt + writeback-warn. No triage/scan path in the current code infers multi-repo scope (triage produces an `IssueOutcome`, not a repo list), so population is left to the agent/issue (a future triage tool or the issue body). Documented in the field comment; NOT wired in this slice (in scope per spec: "else leave population to the agent/issue and document").
- New metric label value `in_scope_no_branch` needs no metric code change (`WritebackOutcome` uses `WithLabelValues`).
