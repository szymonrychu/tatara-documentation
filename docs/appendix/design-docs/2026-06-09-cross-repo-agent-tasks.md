# Cross-repo agent tasks - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clone every Project repo into the agent workspace and deliver each repo's changes as its own branch + PR, so the agent can handle cross-repo issues.

**Architecture:** Operator passes the full Project repo set to the wrapper pod as one JSON env (`TATARA_REPOS`, primary first). The wrapper clones each repo into `/workspace/<name>`, checks out `tatara/task-<task>` in each, and on each turn commits+pushes every changed repo. The operator's write-back attempts a PR for `tatara/task-<task>` on every Project repo; repos without the branch 422 and are skipped; all opened PR links are posted on the issue. Session config moves to `/workspace` (parent, outside the repos), retiring the `.git/info/exclude` hack.

**Tech Stack:** Go 1.26 (operator), Go (wrapper); controller-runtime; testify; envtest (operator controller tests, `KUBEBUILDER_ASSETS` from setup-envtest).

**Spec:** `docs/superpowers/specs/2026-06-09-cross-repo-agent-tasks-design.md`

**Shared contract (both repos depend on this exact shape):**
`TATARA_REPOS` is a JSON array, primary repo first:
```json
[{"name":"tatara-cli","url":"https://github.com/szymonrychu/tatara-cli","branch":"main"},
 {"name":"tatara-memory","url":"https://github.com/szymonrychu/tatara-memory","branch":"main"}]
```

**Branch flow (both repos):** worktree off `main` -> develop -> merge to `main` -> build/deploy from `main`. Bump chart appVersion + image; record in MEMORY.

---

## File Structure

**tatara-claude-code-wrapper:**
- `cmd/wrapper/config.go` - parse `TATARA_REPOS` into `[]bootstrap.RepoSpec`.
- `internal/bootstrap/bootstrap.go` - `RepoSpec` type; `Params.Repos`; `GitRunner` becomes dir-parameterized; `Render` clones each repo into `/workspace/<name>` + checkout per repo; drop the exclude call.
- `internal/bootstrap/repo.go` - `cloneRepo`/`configureGit`/`CommitAndPush` take a dir; add `CommitAndPushAll(repos, branch, msg, git)`.
- `internal/bootstrap/exclude.go` - DELETE (config no longer inside a repo).
- `internal/bootstrap/exclude_test.go` cases in `enforce_test.go` - remove the exclude test; add multi-repo tests.
- `cmd/wrapper/app.go` - `gitRunner` becomes `func(dir string, args ...string) error`; `OnTurnDone` calls `CommitAndPushAll`.

**tatara-operator:**
- `internal/agent/pod.go` - `BuildPod` takes the repo list; sets `TATARA_REPOS` env (primary first).
- `internal/controller/task_controller.go` - pass all Project repos to `BuildPod`.
- `internal/controller/turnloop.go` - `planTurnText` gains the cross-repo + clone-layout instruction.
- `internal/controller/writeback.go` - write-back loops over all Project repos, opening a PR per repo (existing 422 handling skips repos without the branch); collect all PR URLs for the issue comment.

---

## Wrapper Tasks

### Task W1: TATARA_REPOS contract + parsing

**Files:**
- Modify: `internal/bootstrap/bootstrap.go` (add `RepoSpec`, `Params.Repos`)
- Modify: `cmd/wrapper/config.go` (parse env)
- Test: `cmd/wrapper/config_test.go`

- [ ] **Step 1: Write the failing test** (config_test.go)

```go
func TestLoadConfig_ParsesTataraRepos(t *testing.T) {
	t.Setenv("TATARA_REPOS", `[{"name":"a","url":"https://h/a","branch":"main"},{"name":"b","url":"https://h/b","branch":"dev"}]`)
	cfg, err := loadConfig(nil)
	require.NoError(t, err)
	require.Len(t, cfg.Repos, 2)
	require.Equal(t, "a", cfg.Repos[0].Name)
	require.Equal(t, "https://h/b", cfg.Repos[1].URL)
	require.Equal(t, "dev", cfg.Repos[1].Branch)
}
```

- [ ] **Step 2: Run, verify fail**

Run: `go test ./cmd/wrapper/ -run TestLoadConfig_ParsesTataraRepos`
Expected: FAIL (cfg.Repos undefined).

- [ ] **Step 3: Implement** - add to `internal/bootstrap/bootstrap.go`:

```go
// RepoSpec is one Project repo to clone into the workspace.
type RepoSpec struct {
	Name   string `json:"name"`
	URL    string `json:"url"`
	Branch string `json:"branch"`
}
```
Add `Repos []RepoSpec` to `Params`. In `cmd/wrapper/config.go` add field `Repos []bootstrap.RepoSpec` and in `loadConfig`:
```go
if raw := os.Getenv("TATARA_REPOS"); raw != "" {
	if err := json.Unmarshal([]byte(raw), &cfg.Repos); err != nil {
		return config{}, fmt.Errorf("parse TATARA_REPOS: %w", err)
	}
}
```
(add `encoding/json` import)

- [ ] **Step 4: Run, verify pass.** Run: `go test ./cmd/wrapper/ -run TestLoadConfig_ParsesTataraRepos`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cmd/wrapper/config.go cmd/wrapper/config_test.go internal/bootstrap/bootstrap.go
git commit -m "feat(config): parse TATARA_REPOS into RepoSpec list"
```

### Task W2: dir-parameterized GitRunner

**Files:**
- Modify: `internal/bootstrap/bootstrap.go` (`GitRunner` type, `configureGit`, checkout call)
- Modify: `internal/bootstrap/repo.go` (`cloneRepo`, `CommitAndPush` take dir)
- Modify: `cmd/wrapper/app.go` (`gitRunner` signature)
- Test: existing `internal/bootstrap/*_test.go` updated

- [ ] **Step 1: Update the GitRunner type and all signatures.** Change in bootstrap.go:
```go
// GitRunner runs a git subcommand in dir; injected for testability.
type GitRunner func(dir string, args ...string) error
```
`configureGit(p, git)` -> calls `git("", "config", "--global", ...)` (global is dir-independent; pass "" or workspace). `cloneRepo` -> `git(p.Workspace, append([]string{"clone","--depth","1",...}, url, dest)...)`. `CommitAndPush(dir, branch, msg, git)` -> all calls `git(dir, ...)`.

- [ ] **Step 2: Update existing tests** to the new signature: every `func(a ...string) error { ... }` fake becomes `func(dir string, a ...string) error { ... }`, recording `dir` where asserted. Update `app.go gitRunner`:
```go
func gitRunner() bootstrap.GitRunner {
	return func(dir string, args ...string) error {
		cmd := exec.Command("git", args...) //nolint:gosec
		cmd.Dir = dir
		out, err := cmd.CombinedOutput()
		if err != nil { return fmt.Errorf("git -C %s %v: %v: %w", dir, args, string(out), err) }
		return nil
	}
}
```
(callers pass the dir; `bootstrap.Render(params, gitRunner())`.)

- [ ] **Step 3: Run the bootstrap+cmd tests, fix until green.**

Run: `go test ./internal/bootstrap/ ./cmd/wrapper/`
Expected: PASS (after updating fakes).

- [ ] **Step 4: Commit**

```bash
git add internal/bootstrap cmd/wrapper
git commit -m "refactor(bootstrap): GitRunner takes a dir (prep for multi-repo)"
```

### Task W3: multi-repo clone + drop exclude; config in /workspace parent

**Files:**
- Modify: `internal/bootstrap/bootstrap.go` (`Render` clone loop)
- Delete: `internal/bootstrap/exclude.go`
- Modify: `internal/bootstrap/enforce_test.go` (replace exclude test with multi-repo clone test)
- Test: `internal/bootstrap/enforce_test.go`

- [ ] **Step 1: Write the failing test** (enforce_test.go) - replace `TestRender_ExcludesWrapperConfigFromGit` with:

```go
func TestRender_ClonesEachRepoIntoSubdirAndChecksOutBranch(t *testing.T) {
	ws := t.TempDir()
	var calls [][]string // dir + args
	p := bootstrap.Params{
		HomeDir: t.TempDir(), Workspace: ws, BaseMCP: []byte(`{"mcpServers":{}}`),
		TaskBranch: "tatara/task-x",
		Repos: []bootstrap.RepoSpec{
			{Name: "a", URL: "https://h/a", Branch: "main"},
			{Name: "b", URL: "https://h/b", Branch: "dev"},
		},
		RepoURL: "https://h/a", HookCommand: "/x", PermissionMode: "bypassPermissions",
	}
	require.NoError(t, bootstrap.Render(p, func(dir string, a ...string) error {
		calls = append(calls, append([]string{dir}, a...)); return nil
	}))
	joined := func() string { var s []string; for _, c := range calls { s = append(s, strings.Join(c, " ")) }; return strings.Join(s, "|") }()
	require.Contains(t, joined, filepath.Join(ws, "a")+" clone")
	require.Contains(t, joined, "https://h/a")
	require.Contains(t, joined, filepath.Join(ws, "b")+" clone")
	require.Contains(t, joined, "https://h/b")
	// checkout the task branch inside each repo dir
	require.Contains(t, joined, filepath.Join(ws, "a")+" checkout -b tatara/task-x")
	require.Contains(t, joined, filepath.Join(ws, "b")+" checkout -b tatara/task-x")
	// session config lives in the workspace PARENT, not inside a repo
	b, _ := os.ReadFile(filepath.Join(ws, ".mcp.json"))
	require.NotEmpty(t, b)
}
```

- [ ] **Step 2: Run, verify fail.** Run: `go test ./internal/bootstrap/ -run TestRender_ClonesEachRepoIntoSubdir`. Expected: FAIL.

- [ ] **Step 3: Implement** - in `Render`, replace the single-repo `if p.RepoURL != ""` block with:

```go
if len(p.Repos) > 0 {
	if err := configureGit(p, git); err != nil { return err } // global creds/identity, once
	for _, r := range p.Repos {
		dest := filepath.Join(p.Workspace, r.Name)
		args := []string{"clone", "--depth", "1"}
		if r.Branch != "" { args = append(args, "--branch", r.Branch) }
		args = append(args, r.URL, dest)
		if err := git(p.Workspace, args...); err != nil {
			if r.URL == p.RepoURL { return fmt.Errorf("clone primary repo %s: %w", r.Name, err) }
			continue // non-primary clone failure: skip, agent works with the rest
		}
		if p.TaskBranch != "" {
			if err := git(dest, "checkout", "-b", p.TaskBranch); err != nil {
				if r.URL == p.RepoURL { return err }
			}
		}
	}
}
```
Delete `internal/bootstrap/exclude.go` and remove the `excludeWorkspaceConfig` call. (Session config `writeIfSet(/workspace/CLAUDE.md)`, `mergeMCP` -> `/workspace/.mcp.json`, `installSkills` -> `/workspace/.claude/skills` are unchanged; they now sit in the parent, outside every repo.)

- [ ] **Step 4: Run, verify pass.** Run: `go test ./internal/bootstrap/`. Expected: PASS (remove the deleted exclude test).

- [ ] **Step 5: Commit**

```bash
git rm internal/bootstrap/exclude.go
git add internal/bootstrap
git commit -m "feat(bootstrap): clone each repo into /workspace/<name>; config in parent; drop exclude"
```

### Task W4: per-repo commit+push on each turn

**Files:**
- Modify: `internal/bootstrap/repo.go` (add `CommitAndPushAll`)
- Modify: `cmd/wrapper/app.go` (`OnTurnDone` uses it)
- Test: `internal/bootstrap/enforce_test.go`

- [ ] **Step 1: Write the failing test**

```go
func TestCommitAndPushAll_PushesEachRepoOnItsDir(t *testing.T) {
	var calls [][]string
	git := func(dir string, a ...string) error {
		calls = append(calls, append([]string{dir}, a...))
		if len(a) >= 3 && a[0] == "diff" && a[1] == "--cached" && a[2] == "--quiet" { return errors.New("changes") }
		return nil
	}
	repos := []bootstrap.RepoSpec{{Name: "a"}, {Name: "b"}}
	require.NoError(t, bootstrap.CommitAndPushAll("/ws", repos, "tatara/task-x", "msg", git))
	var s []string; for _, c := range calls { s = append(s, strings.Join(c, " ")) }
	all := strings.Join(s, "|")
	require.Contains(t, all, "/ws/a push -u origin tatara/task-x")
	require.Contains(t, all, "/ws/b push -u origin tatara/task-x")
}
```

- [ ] **Step 2: Run, verify fail.** Run: `go test ./internal/bootstrap/ -run TestCommitAndPushAll`. Expected: FAIL (undefined).

- [ ] **Step 3: Implement** in repo.go:

```go
// CommitAndPushAll runs CommitAndPush in each repo dir under workspace.
func CommitAndPushAll(workspace string, repos []RepoSpec, branch, message string, git GitRunner) error {
	for _, r := range repos {
		if err := CommitAndPush(filepath.Join(workspace, r.Name), branch, message, git); err != nil {
			return fmt.Errorf("commit/push %s: %w", r.Name, err)
		}
	}
	return nil
}
```
`CommitAndPush(dir, branch, msg, git)` is the existing logic with `git(dir, ...)`. (add `path/filepath`, `fmt` imports if needed.) In `app.go OnTurnDone`:
```go
if len(cfg.Repos) > 0 {
	if err := bootstrap.CommitAndPushAll(cfg.Workspace, repos(cfg), cfg.TaskBranch, "tatara agent: "+cfg.TaskBranch, gitRunner()); err != nil {
		log.Error("commit/push failed", "error", err)
	}
}
```
where `repos(cfg)` returns `cfg.Repos` as `[]bootstrap.RepoSpec` (already that type).

- [ ] **Step 4: Run, verify pass + full module.** Run: `go test ./...`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/bootstrap cmd/wrapper
git commit -m "feat(bootstrap): commit+push every changed repo each turn"
```

### Task W5: build + ship wrapper

- [ ] Bump `charts/tatara-claude-code-wrapper/Chart.yaml` appVersion -> `0.1.5`, version -> `0.1.6`; MEMORY entry.
- [ ] `pre-commit`/lint/gofmt/`go test ./...` green; requesting-code-review; merge to main.
- [ ] Build+push `tatara-claude-code-wrapper:0.1.5` (`--build-arg TATARA_CLI_VERSION=0.4.0`).

---

## Operator Tasks

### Task O1: TATARA_REPOS on the agent pod

**Files:**
- Modify: `internal/agent/pod.go` (`BuildPod` takes repos; sets env)
- Modify: `internal/controller/task_controller.go` (pass project repos)
- Test: `internal/agent/pod_test.go`

- [ ] **Step 1: Write the failing test** (pod_test.go) - assert `TATARA_REPOS` env is the JSON of the passed repos, primary first:

```go
func TestBuildPod_SetsTataraRepos(t *testing.T) {
	proj, repo, task, cfg := sampleInputs()
	repos := []tatarav1alpha1.Repository{
		{ObjectMeta: metav1.ObjectMeta{Name: "repo1"}, Spec: tatarav1alpha1.RepositorySpec{URL: "https://git/acme/repo1", DefaultBranch: "main"}},
		{ObjectMeta: metav1.ObjectMeta{Name: "repo2"}, Spec: tatarav1alpha1.RepositorySpec{URL: "https://git/acme/repo2", DefaultBranch: "dev"}},
	}
	c := agent.BuildPod(proj, repo, task, repos, testMemoryEndpoint, cfg).Spec.Containers[0]
	v, ok := envValue(c, "TATARA_REPOS")
	require.True(t, ok)
	var got []map[string]string
	require.NoError(t, json.Unmarshal([]byte(v), &got))
	require.Equal(t, "repo1", got[0]["name"]) // primary (the task's repo) first
	require.Equal(t, "https://git/acme/repo2", got[1]["url"])
}
```

- [ ] **Step 2: Run, verify fail.** Run: `KUBEBUILDER_ASSETS=$(setup-envtest use 1.33.0 -p path) go test ./internal/agent/ -run TestBuildPod_SetsTataraRepos`. Expected: FAIL (signature/env).

- [ ] **Step 3: Implement** - change `BuildPod` to accept `repos []tatarav1alpha1.Repository`; build JSON with the task's repo first:

```go
type repoEntry struct{ Name, URL, Branch string }
entries := []repoEntry{{repo.Name, repo.Spec.URL, repo.Spec.DefaultBranch}}
for i := range repos {
	if repos[i].Name == repo.Name { continue }
	entries = append(entries, repoEntry{repos[i].Name, repos[i].Spec.URL, repos[i].Spec.DefaultBranch})
}
buf, _ := json.Marshal(structSliceToJSONTags(entries)) // json tags name/url/branch
// env: {Name: "TATARA_REPOS", Value: string(buf)}
```
(Use a struct with `json:"name|url|branch"` tags. Update the `task_controller.go` call: list repos `client.MatchingFields`/filter by `ProjectRef == project.Name`, pass to `BuildPod`.)

- [ ] **Step 4: Run, verify pass.** Expected: PASS. Update other `BuildPod(...)` test call sites to the new signature (pass `nil` or a one-repo slice).

- [ ] **Step 5: Commit**

```bash
git add internal/agent internal/controller
git commit -m "feat(agent): pass all Project repos to the pod as TATARA_REPOS"
```

### Task O2: cross-repo prompt

**Files:**
- Modify: `internal/controller/turnloop.go`
- Test: `internal/controller/turnloop_test.go`

- [ ] **Step 1: Failing test**

```go
func TestPlanTurnText_MentionsAllReposCloned(t *testing.T) {
	txt := planTurnText("do x", "tatara/task-abc", "proj1", "task-abc")
	low := strings.ToLower(txt)
	require.Contains(t, low, "/workspace/")
	require.Contains(t, low, "each repo you")
}
```

- [ ] **Step 2: Run, verify fail.** `KUBEBUILDER_ASSETS=... go test ./internal/controller/ -run TestPlanTurnText_MentionsAllReposCloned`. FAIL.

- [ ] **Step 3: Implement** - extend `planTurnText`'s direct-implementation paragraph with: "All Project repos are cloned under `/workspace/<name>` (primary: this task's repo). Make changes in whatever repos the issue requires; each repo you change is committed and pushed to `tatara/task-<task>` and gets its own PR."

- [ ] **Step 4: Run, verify pass.** Keep the existing planTurnText tests green.

- [ ] **Step 5: Commit** `git commit -am "feat(turnloop): tell the agent all repos are cloned and each gets a PR"`

### Task O3: multi-repo write-back

**Files:**
- Modify: `internal/controller/writeback.go` (loop repos, open PR per repo)
- Modify: `internal/controller/task_controller.go` (pass project repos to write-back)
- Test: `internal/controller/writeback_test.go`

- [ ] **Step 1: Failing test** - with a fake SCM that reports a branch on two repos, write-back opens 2 PRs and the issue comment contains both URLs:

```go
func TestWriteback_OpensPRPerRepoWithBranch(t *testing.T) {
	// fake writer: OpenChange returns a PR URL for repos "o/r1","o/r2", 422 for "o/r3"
	// run writeback for task branch over repos [r1,r2,r3]
	// assert: 2 PRs collected; comment body contains both URLs
}
```
(Model it on the existing writeback_test; use the project repo list + a fake `scm.Writer` keyed by repo.)

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement** - in the write-back path, replace the single `OpenChange` for the primary with a loop over all Project repos: for each, attempt `writer.OpenChange(ctx, token, <repo remote>, taskBranch, title, body)`; a 422 (branch missing / no diff) is skipped (already handled); collect opened URLs. Comment the issue (primary `Source.IssueRef`) with all URLs joined. Extend Task status to hold `[]string` PR URLs (or a newline list in the existing field).

- [ ] **Step 4: Run, verify pass + full suite.** `KUBEBUILDER_ASSETS=... go test ./...`. PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(writeback): open a PR per changed Project repo; comment all links"`

### Task O4: build + ship operator

- [ ] Bump operator chart `0.2.8` -> `0.2.9`; MEMORY entry.
- [ ] Full suite (`KUBEBUILDER_ASSETS`) + lint/gofmt green; requesting-code-review; merge to main.
- [ ] Build+push `tatara-operator:0.2.9` + push chart `0.2.9`.

---

## Integration / deploy / validate

- [ ] Infra MR: bump operator chart pin + image to `0.2.9`; apply.
- [ ] Patch Project `spec.agent.image` -> wrapper `0.1.5` (+ deploy-samples manifest).
- [ ] Create a cross-repo issue on `tatara-cli` (e.g., "add a NOTE to both tatara-cli and tatara-memory READMEs"); confirm: one Task; pod clones all repos under `/workspace/<name>`; PRs opened on BOTH changed repos on `tatara/task-<task>`; issue commented with both PR links.
- [ ] Confirm a single-repo issue still works (only one PR opened).

## Self-review notes

- Spec coverage: A (W3 layout + parent config + drop exclude), B (O1 env + O2 prompt), C (W2 dir-runner, W3 clone-loop, W4 per-repo push), D (O3 write-back loop), E (no change). Error handling: W3 primary-vs-non-primary clone failure; W4 best-effort per repo; O3 per-repo independent (422 skip). All covered.
- Type consistency: `RepoSpec{Name,URL,Branch}` (wrapper) and the `TATARA_REPOS` JSON tags `name/url/branch` match O1's marshalled struct. `GitRunner func(dir string, args ...string) error` used consistently W2-W4.
