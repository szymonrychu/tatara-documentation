# Issue-lifecycle M1 - D babysit (MRCI / Merge / MainCI / give-up) - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the poll-and-react babysit states so a bot PR drives itself
to a merged, main-green close: MRCI polls CI and reacts, Merge merges and
detects the 405 conflict, MainCI polls the merge commit, give-up parks on
deadline/iterations. **This milestone kills the live `selfImprove ... merge ->
405 merge conflicts` controller-runtime backoff loop.**

**Architecture:** Per spec. Builds on M0's `reconcileLifecycle` skeleton +
states. The Implement state (M0) gains a re-entry context so fix/resolve/
re-implement turns are fed the failing checks / conflict. Context-guard handover
on MainCI failure is M3; M1 re-implements under the `maxLifecycleIterations`
backstop only.

**Tech stack:** Go, envtest, table-driven. **Repo:** `tatara-operator`. Same worktree as M0.

---

## File structure

- `internal/scm/scm.go` - `SCMReader.GetCommitCIStatus`; `IssueComment` type stub (full use M2).
- `internal/scm/github.go`, `internal/scm/gitlab.go` - implement `GetCommitCIStatus`.
- `internal/scm/fake_test.go` (or the controller test fakes) - extend the fake reader/writer.
- `internal/controller/lifecycle.go` - MRCI, Merge, MainCI handlers; give-up; Implement re-entry context.
- `internal/controller/lifecycle_test.go` - state tests + the 405 regression.
- `api/v1alpha1/task_types.go` - add `implementContext` status field (re-entry prompt detail).

---

### Task 1: GetCommitCIStatus SCM reader cap

**Files:** `internal/scm/scm.go`, `github.go`, `gitlab.go`; Test `internal/scm/*_test.go`.

- [ ] **Step 1: Failing tests** (httptest server) that `GetCommitCIStatus(ctx, owner, repo, sha)` returns `"success"|"failure"|"pending"|""` from: GitHub combined status + check-runs on the commit; GitLab pipelines for the sha. Mirror `deriveGHCIStatus`/`glCIStatus` logic (reuse those helpers against the commit SHA rather than the PR head).
- [ ] **Step 2: Run - FAIL.**
- [ ] **Step 3: Implement** `GetCommitCIStatus` on both providers (reuse the existing CI-derivation helpers, pointed at the commit SHA endpoint) and add it to the `SCMReader` interface. Update `wire.go` constructions and the test fakes to satisfy the interface.
- [ ] **Step 4: Test PASS.**
- [ ] **Step 5: Commit** `feat(scm): GetCommitCIStatus reader cap (github+gitlab)`.

### Task 2: Implement re-entry context field + prompt

**Files:** `api/v1alpha1/task_types.go`, `internal/controller/lifecycle.go`, `turnloop.go`; Test.

- [ ] **Step 1: Failing test** that the Implement turn prompt includes a re-entry context block when `Status.ImplementContext` is set (e.g. "CI failed: <detail>") and is the plain plan prompt when empty.
- [ ] **Step 2: Run - FAIL.**
- [ ] **Step 3: Add** `implementContext` string to `TaskStatus` (`make manifests generate`). In the Implement handler, when `ImplementContext != ""` build the plan turn text with that block appended (extend `planTurnText` or wrap it), and clear `ImplementContext` after the run starts so the next fresh entry is clean.
- [ ] **Step 4: Test PASS.**
- [ ] **Step 5: Commit** `feat(controller): Implement re-entry context for fix/resolve turns`.

### Task 3: MRCI poll state

**Files:** `internal/controller/lifecycle.go`; Test `lifecycle_test.go`.

- [ ] **Step 1: Failing tests** (table) for the MRCI handler reading `GetPRState(prNumber).CIStatus`:
  - first entry sets `DeadlineAt = now + babysitDeadlineMinutes` if unset.
  - `pending` -> `ctrl.Result{RequeueAfter: pollRequeue}`, state stays MRCI, observe `tatara_mrci_wait_seconds` only on exit.
  - `success` -> transition to `Merge`.
  - `failure` -> set `ImplementContext` to the failing-checks summary, transition to `Implement` (fix turn), increment `lifecycleIterations`.
  - `""` (no CI) -> transition to `Merge` (nothing to wait on; merge gate decides).
  - `now > DeadlineAt` -> give-up: `Comment` on the PR (deadline message), transition to `Parked`, increment `tatara_lifecycle_giveup_total{reason="deadline"}`.
- [ ] **Step 2: Run - FAIL.**
- [ ] **Step 3: Implement** the MRCI handler per the table. Authorship gate first (reuse `GetPRState.Author == botLogin`; on mismatch -> Parked with a comment, `giveup{reason="not-bot-authored"}`). Use `setLifecycleState` for transitions.
- [ ] **Step 4: Test PASS.**
- [ ] **Step 5: Commit** `feat(controller): lifecycle MRCI poll-and-react state`.

### Task 4: Merge state + 405-conflict regression

**Files:** `internal/controller/lifecycle.go`; Test `lifecycle_test.go`.

- [ ] **Step 1: Failing tests:**
  - `mergeAllowed` true + `Merge` ok -> record `MergeCommitSHA` (from the merge response or a follow-up `GetPRState`), transition to `MainCI`.
  - `mergeAllowed` false -> stay/park per policy (afterApproval always true, so this is the autoMergeOnGreenCI-not-green case: requeue under deadline, NOT error).
  - **REGRESSION:** `Merge` returns `*scm.HTTPError{Status:405}` -> set `ImplementContext` to a resolve-conflict instruction ("Merge conflict on `<headBranch>`; rebase `<defaultBranch>`, resolve, push"), transition to `Implement`, increment `lifecycleIterations`. **Must NOT return the error to controller-runtime** (assert `err == nil` and state == `Implement`).
  - other `Merge` error -> `ctrl.Result{RequeueAfter: pollRequeue}` under deadline (transient), no infinite loop (deadline -> Parked).
- [ ] **Step 2: Run - FAIL.**
- [ ] **Step 3: Implement** the Merge handler: `mergeAllowed` (reuse), then `writer.Merge`. Inspect the error with `errors.As(&scm.HTTPError)`: `Status==405` (or body contains "conflict") -> resolve-conflict re-entry; else transient requeue under `DeadlineAt`; on deadline -> Parked (comment + leave open). On success capture the merge SHA (extend `Merge` to return the SHA, or call `GetPRState`/a commits lookup) into `Status.MergeCommitSHA`.
- [ ] **Step 4: Test PASS** (especially the 405 regression).
- [ ] **Step 5: Commit** `fix(controller): Merge 405-conflict spawns resolve turn instead of error-looping`.

### Task 5: MainCI poll state + close

**Files:** `internal/controller/lifecycle.go`; Test.

- [ ] **Step 1: Failing tests** for the MainCI handler reading `GetCommitCIStatus(MergeCommitSHA)`:
  - first entry sets a fresh `DeadlineAt = now + babysitDeadlineMinutes`.
  - `pending` -> requeue.
  - `success` -> post a closing comment on the issue (reuse `writer.Comment`), `CloseIssue` (idempotent if `Closes #N` already closed it - swallow a not-found/already-closed), transition to `Done`, observe `tatara_lifecycle_seconds`.
  - `failure` -> set `ImplementContext` ("main pipeline failed after merge: <detail>; re-implement on `<headBranch>`"), transition to `Implement`, increment `lifecycleIterations`. (Context-guard handover is M3.)
  - `now > DeadlineAt` -> Parked (comment), `giveup{reason="deadline"}`.
- [ ] **Step 2: Run - FAIL.**
- [ ] **Step 3: Implement** the MainCI handler per the table.
- [ ] **Step 4: Test PASS.**
- [ ] **Step 5: Commit** `feat(controller): lifecycle MainCI poll + close on green`.

### Task 6: maxLifecycleIterations backstop

**Files:** `internal/controller/lifecycle.go`; Test.

- [ ] **Step 1: Failing test** that when entering Implement with `lifecycleIterations >= maxLifecycleIterations` (default 10) the handler parks instead (comment + `giveup{reason="maxIterations"}` -> Parked), never spawning another agent run.
- [ ] **Step 2: Run - FAIL.**
- [ ] **Step 3: Implement** the backstop check at the top of the Implement handler (before spawning the pod). Use `project.Spec.Agent.MaxLifecycleIterations` (default 10).
- [ ] **Step 4: Test PASS.**
- [ ] **Step 5: Commit** `feat(controller): lifecycle iteration backstop parks runaway loops`.

### Task 7: Closes #N on the lifecycle MR body

**Files:** `internal/controller/lifecycle.go` (or `writeback.go` body helper); Test.

- [ ] **Step 1: Failing test** that an issue-keyed lifecycle Task's opened MR body contains `Closes #<issueNumber>` (the issue's own repo only - reuse the primary-repo scoping rule), and a PR-entry lifecycle Task (no source issue) does not.
- [ ] **Step 2: Run - FAIL.**
- [ ] **Step 3: Implement** a lifecycle-aware MR body: extend the open-change body builder used by the Implement state to append `Closes #N` when `Source.Number > 0 && !Source.IsPR` and the repo is the issue's own repo. (This is the `Closes` logic that does NOT exist on main today - build it here, scoped, not from `feat/scm-auto-merge`.)
- [ ] **Step 4: Test PASS.**
- [ ] **Step 5: Commit** `feat(controller): lifecycle MR body Closes the linked issue`.

### Task 8: Full suite + lint + the live-loop assertion

**Files:** all M1 tests.

- [ ] **Step 1:** `make test` (with `-race`) green; the 405 regression (Task 4) is the explicit live-loop guard.
- [ ] **Step 2:** `gofmt -l` empty, `golangci-lint run` clean, `helm lint charts/*` clean.
- [ ] **Step 3:** Re-run `make manifests generate`; confirm no CRD drift uncommitted.
- [ ] **Step 4: Commit** any fmt/manifest deltas `chore: regen + lint after M1`.

---

## Self-review checklist

- [ ] No reconcile path returns an error to controller-runtime for a merge
  conflict (the 405 regression test proves it). The old `writeBackSelfImprove`
  error-return path is no longer reachable for lifecycle tasks.
- [ ] Every poll state has a `DeadlineAt` and a give-up -> Parked transition.
- [ ] `lifecycleIterations` is incremented on every Implement re-entry; the
  backstop parks at the cap.
- [ ] `ImplementContext` is cleared after consumption (no stale fix prompt on a
  later fresh entry).
- [ ] MainCI close is idempotent vs `Closes #N`.
- [ ] Full suite green `-race`; gofmt/golangci/helm-lint clean; no CRD drift.
