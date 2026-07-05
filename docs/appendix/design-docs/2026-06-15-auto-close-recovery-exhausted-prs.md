# Auto-close recovery-exhausted bot PRs — Design + Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** When the revived recovery cannot land a bot PR after `maxRecoveryAttempts` adoption cycles, `mrScan` CLOSES the PR (branch preserved, reopen-to-retry comment) instead of leaving it parked open. Closes the egress gap surfaced 2026-06-15 (recovery lands fixable PRs but parks stale/unfixable ones open, requiring manual cleanup of 4 PRs).

**Design (chosen):** Close-on-exhaustion, not stale-detection. Server-side "is the PR stale" (GitHub compare `ahead_by`) cannot detect content-equivalent-stale PRs (e.g. operator#39 was 1 commit ahead yet rebased to an empty diff — only a git rebase reveals it). So instead of detecting staleness, treat "recovery failed N times" as the close trigger. Reuses the `maxRecoveryAttempts=3` bound already in `mrScan` (`priorTerminalAttempts`). Reversible: `ClosePR` does not delete the branch; the comment invites reopen. Behavioral note: this also closes genuinely-unfixable (not just stale) PRs the autonomous agent gave up on after 3 tries — acceptable and reversible (a human reopens to hand-fix).

**Tech:** Go, controller-runtime, table-driven `go test` (envtest), tatara-helmfile GitOps deploy.

**Repo:** `tatara-operator` (code) + `tatara-helmfile` (deploy). Worktree off fresh `origin/main` (currently dc7c631; external bots push here).

## Key facts
- `ProjectReconciler` (project_controller.go) has `ReaderFor func(provider, token string) (scm.SCMReader, error)` but NO writer.
- `scm.ByProvider(name) (scm.SCMWriter, error)` returns a writer; `ClosePR(ctx, repoURL, token, number, body) error`.
- `scanReader` fetches the token from `sec.Data["token"]` (secret `proj.Spec.ScmSecretRef`).
- mrScan exhaustion block (projectscan.go, in the `c.author == bot` branch) currently:
  ```go
  if priorTerminalAttempts(existing, c.repo, c.number) >= maxRecoveryAttempts {
      r.Metrics.ScanItem("mrScan", "recovery_exhausted")
      l.Info("mrScan: recovery exhausted; not re-adopting bot PR", ...)
      continue
  }
  ```

---

## Task 0: Worktree off fresh origin/main
- [ ] `cd ~/Documents/tatara/tatara-operator && git fetch origin main`
- [ ] `git worktree add -b fix/auto-close-exhausted-prs $HOME/.config/superpowers/worktrees/tatara-operator/auto-close origin/main`
- [ ] Baseline: `KB="$(go run sigs.k8s.io/controller-runtime/tools/setup-envtest@release-0.21 use 1.33.0 -p path)"; KUBEBUILDER_ASSETS="$KB" go test ./internal/controller/ -count=1` → PASS

## Task 1: Give ProjectReconciler a writer (SCMFor) + scanWriter helper

**Files:** `internal/controller/project_controller.go` (struct field), `cmd/manager/wire.go` (wiring), `internal/controller/projectscan.go` (scanWriter helper).

- [ ] **Step 1: Add the field.** In `project_controller.go` `type ProjectReconciler struct`, after `ReaderFor`:
  ```go
  	// SCMFor returns the SCMWriter for a provider name (token passed per call).
  	SCMFor func(provider string) (scm.SCMWriter, error)
  ```
- [ ] **Step 2: Wire it.** In `cmd/manager/wire.go`, in the `&controller.ProjectReconciler{...}` literal (next to `ReaderFor`):
  ```go
  		SCMFor: func(provider string) (scm.SCMWriter, error) {
  			return scm.ByProvider(provider)
  		},
  ```
- [ ] **Step 3: scanWriter helper.** In `projectscan.go`, next to `scanReader`:
  ```go
  // scanWriter resolves the SCMWriter + token for the Project's provider, mirroring
  // scanReader. Used by mrScan to close PRs that recovery has exhausted.
  func (r *ProjectReconciler) scanWriter(ctx context.Context, proj *tatarav1alpha1.Project) (scm.SCMWriter, string, error) {
  	if r.SCMFor == nil {
  		return nil, "", fmt.Errorf("scan: SCMFor not wired")
  	}
  	var sec corev1.Secret
  	key := types.NamespacedName{Namespace: proj.Namespace, Name: proj.Spec.ScmSecretRef}
  	if err := r.Get(ctx, key, &sec); err != nil {
  		return nil, "", fmt.Errorf("scan: get scm secret: %w", err)
  	}
  	token := string(sec.Data["token"])
  	w, err := r.SCMFor(proj.Spec.Scm.Provider)
  	if err != nil {
  		return nil, "", err
  	}
  	return w, token, nil
  }
  ```
- [ ] **Step 4:** `go build ./...` clean. Commit: `feat(operator): ProjectReconciler SCMFor writer + scanWriter helper`.

## Task 2: Close the PR on recovery exhaustion (TDD)

**Files:** `internal/controller/projectscan.go` (mrScan exhaustion block + closeExhaustedPR), `internal/controller/projectscan_recovery_bound_test.go` (test).

- [ ] **Step 1: Failing test.** Add to `projectscan_recovery_bound_test.go` a test that runs `mrScan` against an open bot PR with 3 prior terminal (Parked) tasks for that PR, with a fake `SCMFor` writer recording `ClosePR` calls and a fake reader listing the PR; assert the PR's number was closed and no adoption task was created. Reuse the existing mrScan test harness in `projectscan_run_test.go`/`projectscan_binder_test.go` (fake reader returning `ListOpenPRs`, fake k8s client). Mirror `TestMRScanBotPRCreatesIssueLifecycleMRCI` for the harness; add a fake writer capturing `ClosePR(number)`. Assert: `closedPRs == [<n>]`, `recovery_closed` path taken, 0 new issueLifecycle tasks.

  (Read those two test files first for the exact fake types and k8s seeding helpers; wire `SCMFor` on the test `ProjectReconciler` to the fake writer.)

- [ ] **Step 2: Run → FAIL** (PR not closed; current code only skips).

- [ ] **Step 3: Implement.** Add a helper to `projectscan.go`:
  ```go
  // closeExhaustedPR closes a bot PR that recovery could not land after
  // maxRecoveryAttempts. The branch is preserved (ClosePR does not delete it),
  // so reopening retries. Best-effort: a failure to resolve repo/writer/token is
  // logged and the PR is left open (the scan still skips re-adoption).
  func (r *ProjectReconciler) closeExhaustedPR(ctx context.Context, proj *tatarav1alpha1.Project, repos []tatarav1alpha1.Repository, c candidate) {
  	l := log.FromContext(ctx)
  	repo, ok := r.matchRepoForSlug(repos, c.repo)
  	if !ok {
  		return
  	}
  	w, token, err := r.scanWriter(ctx, proj)
  	if err != nil {
  		l.Error(err, "mrScan: scanWriter for exhausted close (leaving PR open)",
  			"resource_id", proj.Name, "repo", c.repo, "pr", c.number)
  		return
  	}
  	body := fmt.Sprintf("Autonomous recovery could not land this PR after %d attempts; "+
  		"closing as superseded. The branch is preserved - reopen to retry or hand-fix.", maxRecoveryAttempts)
  	if cerr := w.ClosePR(ctx, repo.Spec.URL, token, c.number, body); cerr != nil {
  		l.Error(cerr, "mrScan: close exhausted PR failed (leaving open)",
  			"resource_id", proj.Name, "repo", c.repo, "pr", c.number)
  		return
  	}
  	r.Metrics.ScanItem("mrScan", "recovery_closed")
  	l.Info("mrScan: closed recovery-exhausted bot PR",
  		"action", "scan_recovery_closed", "resource_id", proj.Name, "repo", c.repo, "pr", c.number)
  }
  ```
  Change the exhaustion block in mrScan to call it (keep the existing metric + `continue`):
  ```go
  		if priorTerminalAttempts(existing, c.repo, c.number) >= maxRecoveryAttempts {
  			r.Metrics.ScanItem("mrScan", "recovery_exhausted")
  			r.closeExhaustedPR(ctx, proj, repos, c)
  			continue
  		}
  ```
- [ ] **Step 4: Run → PASS.** Full controller suite + `go build` + `golangci-lint` clean.
- [ ] **Step 5: Commit:** `feat(operator): close recovery-exhausted bot PRs instead of leaving them parked open`.

## Task 3: Review + deploy
- [ ] `superpowers:requesting-code-review` on the diff; fix critical/high; `pre-commit run --all-files`.
- [ ] Merge to operator `main` (PR, CI green). Note new short SHA.
- [ ] tatara-helmfile: bump `version: 0.0.0-<sha>` (helmfile.yaml.gotmpl) + `image.tag: "<sha>"` (values/tatara-operator/common.yaml); PR → review diff → merge → apply.
- [ ] Verify rollout: operator image == new SHA, Ready.

## Task 4: Verify + memory
- [ ] Confirm `recovery_closed` metric exists; no regressions in mrScan picked/recovery behavior.
- [ ] Update operator MEMORY.md/ROADMAP.md + the auto-memory with the egress-closure outcome.
