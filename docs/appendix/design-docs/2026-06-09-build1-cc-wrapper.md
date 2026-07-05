# Namespace-Preserving Clone Layout (tatara-claude-code-wrapper) Implementation Plan

Date: 2026-06-09
Repo: tatara-claude-code-wrapper (module `github.com/szymonrychu/tatara-claude-code-wrapper`)
Status: ready to implement
Source specs:
- `docs/superpowers/specs/2026-06-09-namespace-clone-layout-notes.md`
- `docs/superpowers/specs/2026-06-09-phase0-contract-lock.md` (namespacePath rule, locked)
- `docs/superpowers/specs/2026-06-09-incremental-reingest-design.md` (Phase 0 context)

## Goal

Clone each Project repo onto disk mirroring its namespace path (owner[/subgroups]/repo,
excluding the SCM host), instead of the flat basename used today. This prevents
collisions when two repos share a basename and makes the agent's on-disk tree
mirror the SCM namespace.

Concretely:
1. Add a `namespacePath(cloneURL string) string` helper to `internal/bootstrap`,
   implementing the LOCKED rule from the Phase 0 contract:
   - `https://github.com/szymonrychu/tatara-cli.git` -> `szymonrychu/tatara-cli`
   - `https://gitlab.com/szymonrychu/infra/helmfile` -> `szymonrychu/infra/helmfile`
   - `git@github.com:szymonrychu/tatara-cli.git` -> `szymonrychu/tatara-cli`
   - `ssh://git@host:22/group/sub/repo.git` -> `group/sub/repo`
   - Drops scheme, host, userinfo, a trailing `.git`, and leading/trailing slashes.
2. In `bootstrap.go`, change the multi-repo clone dest from
   `filepath.Join(p.Workspace, r.Name)` to `filepath.Join(p.Workspace, namespacePath(r.URL))`,
   `MkdirAll` the parent dir before clone, keep the `TASK_BRANCH` checkout in that
   dest, and keep primary-vs-nonprimary error behavior.
3. In `repo.go`, `CommitAndPushAll` iterates using `namespacePath(r.URL)` instead
   of `r.Name`.
4. `.mcp.json` / `CLAUDE.md` / settings stay at workspace root; claude cwd stays
   workspace root (NO change). The agent sees the namespace tree under cwd.

## Scope notes / facts established by exploration

- `RepoSpec` (in `internal/bootstrap/bootstrap.go`) ALREADY carries `URL`:
  ```go
  type RepoSpec struct {
      Name   string `json:"name"`
      URL    string `json:"url"`
      Branch string `json:"branch"`
  }
  ```
  No struct change needed.
- The operator ALREADY passes `url` in `TATARA_REPOS` JSON. Confirmed in
  `cmd/wrapper/config.go` (unmarshals `TATARA_REPOS` into `[]bootstrap.RepoSpec`)
  and `cmd/wrapper/config_test.go` (`[{"name":"a","url":"https://h/a","branch":"main"},...]`).
  `URL` is wired through end-to-end already; no plumbing work.
- Single-repo path (`p.RepoURL != "" && len(p.Repos)==0`, via `cloneRepo`) clones
  directly into `p.Workspace` and is NOT in scope (only multi-repo `Repos` uses the
  per-repo subdir). Leave `cloneRepo` and the single-repo branch untouched.
- Test conventions: package `bootstrap_test`, `github.com/stretchr/testify/require`,
  plain `func TestX(t *testing.T)` (no testify suite, no envtest, no slog in these
  tests). `GitRunner` test double is an inline closure capturing `[][]string` of
  `dir + args`. Match this exactly. Go 1.25.0.
- The `namespacePath` helper is package-internal (lowercase). Its unit test must
  therefore live in package `bootstrap` (white-box), in a NEW file
  `internal/bootstrap/namespace_test.go`. Existing tests are black-box
  (`bootstrap_test`); do not move them.
- Commit style in this repo: Conventional Commits, scope `bootstrap`
  (e.g. `feat(bootstrap): ...`). Confirmed via `git log`.

## How to run tests

- Single file's package, one test: `go test ./internal/bootstrap/ -run TestName -v`
- Whole bootstrap package: `go test ./internal/bootstrap/ -v`
- Full module: `go test ./... `

---

## Task 1: Add `namespacePath` helper with locked URL-mapping rule

Implements the locked rule from `2026-06-09-phase0-contract-lock.md`. White-box
unit test (package `bootstrap`) because the function is unexported.

### Files
- Create: `internal/bootstrap/namespace.go`
- Create: `internal/bootstrap/namespace_test.go`

### Steps

1. **Write the failing test.** Create `internal/bootstrap/namespace_test.go` with
   the complete table-driven test below. It is white-box (package `bootstrap`) so
   it can call the unexported `namespacePath`.

   ```go
   package bootstrap

   import "testing"

   func TestNamespacePath(t *testing.T) {
       cases := []struct {
           name string
           url  string
           want string
       }{
           {"https github with .git", "https://github.com/szymonrychu/tatara-cli.git", "szymonrychu/tatara-cli"},
           {"https github no .git", "https://github.com/szymonrychu/tatara-cli", "szymonrychu/tatara-cli"},
           {"https gitlab subgroups", "https://gitlab.com/szymonrychu/infra/helmfile", "szymonrychu/infra/helmfile"},
           {"https gitlab subgroups with .git", "https://gitlab.com/szymonrychu/infra/helmfile.git", "szymonrychu/infra/helmfile"},
           {"scp-like ssh with .git", "git@github.com:szymonrychu/tatara-cli.git", "szymonrychu/tatara-cli"},
           {"scp-like ssh no .git", "git@github.com:szymonrychu/tatara-cli", "szymonrychu/tatara-cli"},
           {"ssh scheme with port and subgroup", "ssh://git@host:22/group/sub/repo.git", "group/sub/repo"},
           {"https with userinfo", "https://x-access-token:tok@github.com/szymonrychu/tatara-cli.git", "szymonrychu/tatara-cli"},
           {"trailing slash", "https://github.com/szymonrychu/tatara-cli/", "szymonrychu/tatara-cli"},
           {"http scheme", "http://example.com/owner/repo.git", "owner/repo"},
       }
       for _, tc := range cases {
           t.Run(tc.name, func(t *testing.T) {
               if got := namespacePath(tc.url); got != tc.want {
                   t.Fatalf("namespacePath(%q) = %q, want %q", tc.url, got, tc.want)
               }
           })
       }
   }
   ```

2. **Run it, expect a build failure (FAIL).**
   Command: `go test ./internal/bootstrap/ -run TestNamespacePath -v`
   Expected FAIL: compile error
   `undefined: namespacePath` (the package does not yet declare `namespacePath`).

3. **Write the minimal implementation.** Create `internal/bootstrap/namespace.go`
   with the complete code below.

   ```go
   package bootstrap

   import "strings"

   // namespacePath maps a git clone URL to the on-disk subpath:
   // owner[/subgroups]/repo, dropping scheme, host, userinfo, and a trailing
   // ".git". It keeps the owner and any subgroups.
   //
   //	https://github.com/szymonrychu/tatara-cli.git -> szymonrychu/tatara-cli
   //	https://gitlab.com/szymonrychu/infra/helmfile  -> szymonrychu/infra/helmfile
   //	git@github.com:szymonrychu/tatara-cli.git      -> szymonrychu/tatara-cli
   //	ssh://git@host:22/group/sub/repo.git           -> group/sub/repo
   func namespacePath(cloneURL string) string {
       s := strings.TrimSpace(cloneURL)
       // Strip a URL scheme (https://, http://, ssh://, git://).
       if i := strings.Index(s, "://"); i >= 0 {
           s = s[i+3:]
       }
       // scp-like syntax: git@github.com:owner/repo(.git). Split on the first ':'
       // that precedes the path. After scheme stripping, an scp-like ref has the
       // form user@host:path with no leading slash.
       if !strings.HasPrefix(s, "/") {
           if i := strings.Index(s, ":"); i >= 0 {
               // Only treat as scp-like host separator when there is no '/'
               // before the ':' (so a ':' in a path or a port after a '/' is left alone).
               if slash := strings.Index(s, "/"); slash < 0 || i < slash {
                   s = s[i+1:]
               }
           }
       }
       // Drop userinfo (user@) if present at the front of host[:port]/path.
       if i := strings.Index(s, "@"); i >= 0 {
           if slash := strings.Index(s, "/"); slash < 0 || i < slash {
               s = s[i+1:]
           }
       }
       s = strings.Trim(s, "/")
       // Now s is host[:port]/owner[/subgroups]/repo OR owner[/subgroups]/repo.
       parts := strings.Split(s, "/")
       // If the first segment looks like a host (contains '.' or ':'), drop it.
       if len(parts) > 1 && (strings.Contains(parts[0], ".") || strings.Contains(parts[0], ":")) {
           parts = parts[1:]
       }
       out := strings.Join(parts, "/")
       out = strings.TrimSuffix(out, ".git")
       return out
   }
   ```

   Rationale for the host-detection heuristic: after scheme + scp-separator
   stripping, an `ssh://git@host:22/group/sub/repo.git` reduces to
   `host:22/group/sub/repo.git`; the first segment `host:22` contains `:` so it is
   dropped, leaving `group/sub/repo`. An scp-like `git@github.com:owner/repo`
   reduces (via the `:` split) to `owner/repo`, whose first segment `owner` has no
   `.`/`:` so it is kept. An `https://github.com/owner/repo` reduces to
   `github.com/owner/repo`; first segment `github.com` has a `.` so it is dropped.

4. **Run it, expect PASS.**
   Command: `go test ./internal/bootstrap/ -run TestNamespacePath -v`
   Expected PASS: each `t.Run` subtest reports `--- PASS:` and the suite ends with
   `ok  github.com/szymonrychu/tatara-claude-code-wrapper/internal/bootstrap`.

5. **Commit.**
   Command:
   `git add internal/bootstrap/namespace.go internal/bootstrap/namespace_test.go && git commit -m "feat(bootstrap): add namespacePath helper for namespace-preserving clone layout"`

---

## Task 2: Clone multi-repo into the namespace subpath (bootstrap.go)

Switch the multi-repo clone destination to `namespacePath(r.URL)`, create the
parent directory, keep the `TASK_BRANCH` checkout pointed at the new dest, and
keep primary-vs-nonprimary error handling. Black-box test (package
`bootstrap_test`).

### Files
- Modify: `internal/bootstrap/bootstrap.go`
- Modify: `internal/bootstrap/enforce_test.go` (replace the existing
  `TestRender_ClonesEachRepoIntoSubdirAndChecksOutBranch` to assert namespace paths)

### Steps

1. **Write the failing test.** In `internal/bootstrap/enforce_test.go`, REPLACE
   the existing function `TestRender_ClonesEachRepoIntoSubdirAndChecksOutBranch`
   (currently asserting `filepath.Join(ws, "a")` / `filepath.Join(ws, "b")`) with
   the full replacement below. The two repos now have realistic URLs with owners,
   and one has subgroups, so the test pins the namespace layout. Keep all other
   functions in the file unchanged.

   ```go
   func TestRender_ClonesEachRepoIntoNamespaceSubdirAndChecksOutBranch(t *testing.T) {
       ws := t.TempDir()
       var calls [][]string // dir + args
       p := bootstrap.Params{
           HomeDir: t.TempDir(), Workspace: ws, BaseMCP: []byte(`{"mcpServers":{}}`),
           TaskBranch: "tatara/task-x",
           Repos: []bootstrap.RepoSpec{
               {Name: "tatara-cli", URL: "https://github.com/szymonrychu/tatara-cli.git", Branch: "main"},
               {Name: "helmfile", URL: "https://gitlab.com/szymonrychu/infra/helmfile.git", Branch: "dev"},
           },
           RepoURL: "https://github.com/szymonrychu/tatara-cli.git", HookCommand: "/x", PermissionMode: "bypassPermissions",
       }
       require.NoError(t, bootstrap.Render(p, func(dir string, a ...string) error {
           calls = append(calls, append([]string{dir}, a...))
           return nil
       }))
       joined := func() string {
           var s []string
           for _, c := range calls {
               s = append(s, strings.Join(c, " "))
           }
           return strings.Join(s, "|")
       }()

       destA := filepath.Join(ws, "szymonrychu", "tatara-cli")
       destB := filepath.Join(ws, "szymonrychu", "infra", "helmfile")

       // cloned into the namespace-preserving destinations
       require.Contains(t, joined, "clone")
       require.Contains(t, joined, "https://github.com/szymonrychu/tatara-cli.git")
       require.Contains(t, joined, destA)
       require.Contains(t, joined, "https://gitlab.com/szymonrychu/infra/helmfile.git")
       require.Contains(t, joined, destB)
       // checkout the task branch inside each namespace dir
       require.Contains(t, joined, destA+" checkout -b tatara/task-x")
       require.Contains(t, joined, destB+" checkout -b tatara/task-x")
       // parent dirs of the namespace destinations were created
       require.DirExists(t, filepath.Join(ws, "szymonrychu"))
       require.DirExists(t, filepath.Join(ws, "szymonrychu", "infra"))
       // session config lives in the workspace root, not inside a repo
       b, _ := os.ReadFile(filepath.Join(ws, ".mcp.json"))
       require.NotEmpty(t, b)
   }
   ```

2. **Run it, expect FAIL.**
   Command:
   `go test ./internal/bootstrap/ -run TestRender_ClonesEachRepoIntoNamespaceSubdirAndChecksOutBranch -v`
   Expected FAIL: the assertion
   `require.Contains(t, joined, ".../szymonrychu/tatara-cli")` fails because the
   current code clones into `filepath.Join(ws, r.Name)` =
   `.../tatara-cli` (no `szymonrychu/` parent). Failure message of the form
   `Error: "...tatara-cli clone..." does not contain ".../szymonrychu/tatara-cli"`.
   The `require.DirExists(t, filepath.Join(ws, "szymonrychu"))` also fails.

3. **Write the minimal implementation.** In `internal/bootstrap/bootstrap.go`,
   replace the multi-repo loop body. The current block is:

   ```go
   		for _, r := range p.Repos {
   			dest := filepath.Join(p.Workspace, r.Name)
   			args := []string{"clone", "--depth", "1"}
   			if r.Branch != "" {
   				args = append(args, "--branch", r.Branch)
   			}
   			args = append(args, r.URL, dest)
   			if err := git(p.Workspace, args...); err != nil {
   				if r.URL == p.RepoURL {
   					return fmt.Errorf("clone primary repo %s: %w", r.Name, err)
   				}
   				continue // non-primary clone failure: skip
   			}
   			if p.TaskBranch != "" {
   				if err := git(dest, "checkout", "-b", p.TaskBranch); err != nil {
   					if r.URL == p.RepoURL {
   						return err
   					}
   				}
   			}
   		}
   ```

   Replace it with (only the `dest` derivation and the added `MkdirAll` change;
   error behavior and checkout logic are preserved exactly):

   ```go
   		for _, r := range p.Repos {
   			dest := filepath.Join(p.Workspace, namespacePath(r.URL))
   			if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
   				if r.URL == p.RepoURL {
   					return fmt.Errorf("mkdir parent for primary repo %s: %w", r.Name, err)
   				}
   				continue // non-primary parent-dir failure: skip
   			}
   			args := []string{"clone", "--depth", "1"}
   			if r.Branch != "" {
   				args = append(args, "--branch", r.Branch)
   			}
   			args = append(args, r.URL, dest)
   			if err := git(p.Workspace, args...); err != nil {
   				if r.URL == p.RepoURL {
   					return fmt.Errorf("clone primary repo %s: %w", r.Name, err)
   				}
   				continue // non-primary clone failure: skip
   			}
   			if p.TaskBranch != "" {
   				if err := git(dest, "checkout", "-b", p.TaskBranch); err != nil {
   					if r.URL == p.RepoURL {
   						return err
   					}
   				}
   			}
   		}
   ```

   `os` and `filepath` are already imported in `bootstrap.go`; no import change.

4. **Run it, expect PASS.**
   Command:
   `go test ./internal/bootstrap/ -run TestRender_ClonesEachRepoIntoNamespaceSubdirAndChecksOutBranch -v`
   Expected PASS: `--- PASS: TestRender_ClonesEachRepoIntoNamespaceSubdirAndChecksOutBranch`
   and `ok  github.com/szymonrychu/tatara-claude-code-wrapper/internal/bootstrap`.

5. **Run the whole package to confirm nothing else broke.**
   Command: `go test ./internal/bootstrap/ -v`
   Expected PASS: all tests in the package pass, including the unchanged
   `TestRender_WritesClaudeMdSettingsSkillsAndMergesMCP`,
   `TestRender_ClonesRepoWhenURLSet`,
   `TestRender_ChecksOutTaskBranchAfterClone` (single-repo path, unaffected), and
   the `TestCommitAndPush*` tests. Final line `ok ...`.

6. **Commit.**
   Command:
   `git add internal/bootstrap/bootstrap.go internal/bootstrap/enforce_test.go && git commit -m "feat(bootstrap): clone multi-repo into namespace-preserving subpath under workspace"`

---

## Task 3: Iterate `CommitAndPushAll` over the namespace subpath (repo.go)

Make the per-turn commit/push walk the same namespace-derived directories that
Task 2 clones into, so `add -A` / `commit` / `push` run in the correct repo dir.
Black-box test.

### Files
- Modify: `internal/bootstrap/repo.go`
- Modify: `internal/bootstrap/enforce_test.go` (replace
  `TestCommitAndPushAll_PushesEachRepoOnItsDir`)

### Steps

1. **Write the failing test.** In `internal/bootstrap/enforce_test.go`, REPLACE
   the existing `TestCommitAndPushAll_PushesEachRepoOnItsDir` (which uses
   `RepoSpec{{Name: "a"}, {Name: "b"}}` and asserts `/ws/a` / `/ws/b`) with the
   full replacement below. It now supplies `URL`s and asserts the push runs in the
   namespace dir. Keep all other functions in the file unchanged.

   ```go
   func TestCommitAndPushAll_PushesEachRepoOnItsNamespaceDir(t *testing.T) {
       var calls [][]string
       git := func(dir string, a ...string) error {
           calls = append(calls, append([]string{dir}, a...))
           if len(a) >= 3 && a[0] == "diff" && a[1] == "--cached" && a[2] == "--quiet" {
               return errors.New("changes")
           }
           return nil
       }
       repos := []bootstrap.RepoSpec{
           {Name: "tatara-cli", URL: "https://github.com/szymonrychu/tatara-cli.git"},
           {Name: "helmfile", URL: "https://gitlab.com/szymonrychu/infra/helmfile.git"},
       }
       require.NoError(t, bootstrap.CommitAndPushAll("/ws", repos, "tatara/task-x", "msg", git))
       var s []string
       for _, c := range calls {
           s = append(s, strings.Join(c, " "))
       }
       all := strings.Join(s, "|")
       require.Contains(t, all, "/ws/szymonrychu/tatara-cli push -u origin tatara/task-x")
       require.Contains(t, all, "/ws/szymonrychu/infra/helmfile push -u origin tatara/task-x")
   }
   ```

2. **Run it, expect FAIL.**
   Command:
   `go test ./internal/bootstrap/ -run TestCommitAndPushAll_PushesEachRepoOnItsNamespaceDir -v`
   Expected FAIL: the current `CommitAndPushAll` joins `workspace/r.Name`
   (`/ws/tatara-cli`, `/ws/helmfile`), so
   `require.Contains(t, all, "/ws/szymonrychu/tatara-cli push -u origin tatara/task-x")`
   fails with a message like
   `Error: "/ws/tatara-cli add -A|.../ws/tatara-cli push -u origin tatara/task-x|..."
   does not contain "/ws/szymonrychu/tatara-cli push -u origin tatara/task-x"`.

3. **Write the minimal implementation.** In `internal/bootstrap/repo.go`, change
   `CommitAndPushAll` to derive the dir via `namespacePath(r.URL)`. The current
   function is:

   ```go
   func CommitAndPushAll(workspace string, repos []RepoSpec, branch, message string, git GitRunner) error {
       for _, r := range repos {
           if err := CommitAndPush(filepath.Join(workspace, r.Name), branch, message, git); err != nil {
               return fmt.Errorf("commit/push %s: %w", r.Name, err)
           }
       }
       return nil
   }
   ```

   Replace it with:

   ```go
   func CommitAndPushAll(workspace string, repos []RepoSpec, branch, message string, git GitRunner) error {
       for _, r := range repos {
           dir := filepath.Join(workspace, namespacePath(r.URL))
           if err := CommitAndPush(dir, branch, message, git); err != nil {
               return fmt.Errorf("commit/push %s: %w", r.Name, err)
           }
       }
       return nil
   }
   ```

   `filepath` and `fmt` are already imported in `repo.go`; no import change.

4. **Run it, expect PASS.**
   Command:
   `go test ./internal/bootstrap/ -run TestCommitAndPushAll_PushesEachRepoOnItsNamespaceDir -v`
   Expected PASS: `--- PASS: TestCommitAndPushAll_PushesEachRepoOnItsNamespaceDir`
   and `ok ...`.

5. **Run the whole package.**
   Command: `go test ./internal/bootstrap/ -v`
   Expected PASS: all bootstrap tests pass (including
   `TestCommitAndPush_CommitsWhenDirtyThenPushes` and
   `TestCommitAndPush_SkipsCommitWhenClean`, which are single-dir and unaffected).
   Final line `ok ...`.

6. **Commit.**
   Command:
   `git add internal/bootstrap/repo.go internal/bootstrap/enforce_test.go && git commit -m "feat(bootstrap): commit/push each repo in its namespace-preserving dir"`

---

## Task 4: Full-module verification (config root stays at workspace, no regressions)

Confirm the whole module builds and tests green, and that the workspace-root
invariant (`.mcp.json` / `CLAUDE.md` / settings at root, claude cwd unchanged)
still holds. No production-code change in this task; it asserts the unchanged
invariant explicitly so a future edit cannot silently move config into a repo
subdir.

### Files
- Modify: `internal/bootstrap/enforce_test.go` (add one assertion-only test)

### Steps

1. **Write the test.** Append the function below to
   `internal/bootstrap/enforce_test.go`. It renders a multi-repo config and asserts
   `.mcp.json` and `CLAUDE.md` are at the workspace ROOT and NOT inside any repo
   namespace subdir.

   ```go
   func TestRender_SessionConfigStaysAtWorkspaceRootWithNamespaceClones(t *testing.T) {
       ws := t.TempDir()
       p := bootstrap.Params{
           HomeDir: t.TempDir(), Workspace: ws, BaseMCP: []byte(`{"mcpServers":{}}`),
           ProjectClaudeMd: "PROJECT RULES",
           TaskBranch:      "tatara/task-x",
           Repos: []bootstrap.RepoSpec{
               {Name: "tatara-cli", URL: "https://github.com/szymonrychu/tatara-cli.git", Branch: "main"},
           },
           RepoURL: "https://github.com/szymonrychu/tatara-cli.git", HookCommand: "/x", PermissionMode: "bypassPermissions",
       }
       require.NoError(t, bootstrap.Render(p, func(dir string, a ...string) error { return nil }))

       // config at workspace root
       require.FileExists(t, filepath.Join(ws, ".mcp.json"))
       require.FileExists(t, filepath.Join(ws, "CLAUDE.md"))
       b, _ := os.ReadFile(filepath.Join(ws, "CLAUDE.md"))
       require.Equal(t, "PROJECT RULES", string(b))

       // config is NOT duplicated inside the repo namespace subdir
       require.NoFileExists(t, filepath.Join(ws, "szymonrychu", "tatara-cli", ".mcp.json"))
       require.NoFileExists(t, filepath.Join(ws, "szymonrychu", "tatara-cli", "CLAUDE.md"))
   }
   ```

2. **Run it, expect PASS** (the behavior already holds; this test documents and
   guards it).
   Command:
   `go test ./internal/bootstrap/ -run TestRender_SessionConfigStaysAtWorkspaceRootWithNamespaceClones -v`
   Expected PASS: `--- PASS: TestRender_SessionConfigStaysAtWorkspaceRootWithNamespaceClones`.
   If this FAILS, it means a prior task wrongly moved config-writing into the repo
   loop; stop and fix that task before continuing.

3. **Run the full module.**
   Command: `go test ./...`
   Expected PASS: every package reports `ok` (or `no test files`). In particular
   `cmd/wrapper` tests, including `TestLoadConfig_ParsesTataraRepos`, still pass
   because `RepoSpec.URL` was already present and unmarshalled.

4. **Vet for safety.**
   Command: `go vet ./...`
   Expected: no output (clean).

5. **Commit.**
   Command:
   `git add internal/bootstrap/enforce_test.go && git commit -m "test(bootstrap): guard session config stays at workspace root under namespace clones"`

---

## Sequencing and dependencies

- Task 1 first (the helper everything else calls).
- Task 2 depends on Task 1 (`namespacePath` in `bootstrap.go`).
- Task 3 depends on Task 1 (`namespacePath` in `repo.go`); independent of Task 2.
- Task 4 last (whole-module gate; depends on 2 and 3 being in).

## Anticipated challenges / decisions already resolved

- **scp-like vs `ssh://` with port.** Both reduce to the right path via the
  scheme-strip then scp-`:`-split then host-detection heuristic. The table test in
  Task 1 covers `git@github.com:owner/repo.git` and `ssh://git@host:22/group/sub/repo.git`
  explicitly; if the heuristic is wrong, Task 1 fails before any production
  wiring exists.
- **No `RepoSpec` change.** `URL` already exists and is already populated by the
  operator via `TATARA_REPOS`; nothing to wire through. (Per the prompt's
  conditional: this note is the resolution -- the operator already passes URL.)
- **Single-repo path untouched.** `cloneRepo` / the `p.RepoURL != ""` branch clone
  into `p.Workspace` directly and are out of scope; their tests
  (`TestRender_ClonesRepoWhenURLSet`, `TestRender_ChecksOutTaskBranchAfterClone`)
  must stay green, which Task 2 step 5 and Task 4 step 3 verify.
- **memory `repo` LABEL unchanged.** Per the namespace-clone notes and contract
  lock, only the on-disk clone DIRECTORY mirrors the namespace; the logical `repo`
  label / entity IDs are not part of this repo's code and are not touched here.

### Critical Files for Implementation
- /Users/szymonri/Documents/tatara/tatara-claude-code-wrapper/internal/bootstrap/namespace.go (new helper)
- /Users/szymonri/Documents/tatara/tatara-claude-code-wrapper/internal/bootstrap/bootstrap.go (multi-repo clone dest + MkdirAll)
- /Users/szymonri/Documents/tatara/tatara-claude-code-wrapper/internal/bootstrap/repo.go (CommitAndPushAll dir derivation)
- /Users/szymonri/Documents/tatara/tatara-claude-code-wrapper/internal/bootstrap/enforce_test.go (multi-repo clone + push + config-root tests)
- /Users/szymonri/Documents/tatara/tatara-claude-code-wrapper/internal/bootstrap/namespace_test.go (new white-box helper test)
```