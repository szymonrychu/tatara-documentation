# False-Refusal Wrapper Half (WT-3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In `tatara-claude-code-wrapper`, propagate a rejected critical-outcome MCP tool call (decline_implementation / already_done) back to the agent by re-prompting it instead of finishing the turn silently (Defect C wrapper-half), and make the per-turn commit/push report exactly which repos it actually pushed so the operator learns the touched-repo set (Defect A wrapper-half).

**Architecture:** The wrapper never proxies MCP tool calls; the agent runs `tatara mcp`, which talks to the operator directly. The wrapper's only window into a tool call is the JSONL transcript, which the cc-stop-hook hands back (as `transcriptPath`) when a turn finishes. So Defect C is detected by scanning the just-completed turn's transcript for a `tool_result` block with `is_error:true` whose matching `tool_use` block named `mcp__tatara__decline_implementation` or `mcp__tatara__already_done`; on a hit the app re-prompts the live session via `sess.Submit` (bounded) and suppresses this turn's operator callback so the operator does not see a misleading silent finish. Defect A makes `CommitAndPush` return whether it pushed and `CommitAndPushAll` return the names of the repos it pushed; the app records that list on the turn `Record`, which the existing webhook callback already marshals verbatim to the operator.

**Tech Stack:** Go (stdlib log/slog, controller-runtime for operator), table-driven tests with t.Run, golangci-lint, gofmt.

## Global Constraints
- Newest stable Go; KISS; no tech-debt; JSON logs (log/slog); business actions logged at INFO with structured fields; metrics for anything that counts/times-out/fails.
- TDD strictly: failing test first, run it red, minimal impl, run green, commit. Conventional commits (feat:/fix:/refactor:/test:). Frequent commits.
- Operator CRD changes: regenerate with controller-gen via the repo's make/mise target (find it); templated CRDs apply on helm upgrade. (No CRD work in this slice; it is wrapper-only.)
- Run tests via mise (`mise exec -- go test ./...` or `mise run test`); lint via `mise exec -- golangci-lint run`.

---

## Repo facts grounded in the current code (read before starting)

- The repo root is `~/Documents/tatara/tatara-claude-code-wrapper`. Go directive is `go 1.25.0` (`go.mod`). mise wraps `make`: `mise run test` -> `make test`, `mise run lint` -> `make lint`, `mise run build` -> `make build`. For a single package use `mise exec -- go test ./internal/transcript/...`.
- The wrapper does NOT register or proxy MCP tools in-process. `internal/bootstrap/mcp_register.go::RegisterTataraMCP` just runs `tatara mcp-config <workspace>`, which writes one `.mcp.json` entry `{"command":"tatara","args":["mcp"]}`. The agent calls `tatara mcp` directly; the operator answers. There is no Go code path in the wrapper that observes an MCP tool HTTP status. The transcript is the only signal. Do NOT invent an in-process proxy.
- The cc-stop-hook posts `session.HookResult{... TranscriptPath string}` (`internal/session/session.go:58-72`). `Manager.Complete` records `r.TranscriptPath` onto `mgr.transcriptPath` and, after finalising, calls `mgr.fireDone(rec)` which invokes `OnTurnDone(rec)` (`session.go:925-945, 1041-1045`).
- `Manager.TranscriptPath()` (`session.go:1072-1076`) returns the current transcript path under the lock. `Manager.Submit(text, callbackURL)` (`session.go:754-800`) submits a fresh turn and returns `(id, error)`; it returns `ErrBusy` if a turn is in flight and `"session dead"`/`"session not ready"` otherwise. After `OnTurnDone` fires, the prior turn is already cleared (state Ready), so `Submit` is the correct re-prompt primitive (not `Interject`, which needs an in-flight turn).
- The app's turn finaliser is `sess.OnTurnDone` set in `cmd/wrapper/app.go:153-229`. It runs in a goroutine tracked by `a.turnWG`. Order today: commit/push -> conversation upload -> `sender.Deliver(url, rec)` -> agentTurnFinished hook. The callback URL is `rec.CallbackURL` or `defaultCB` (`cfg.DefaultCallbackURL`).
- Commit/push entrypoints: cross-repo `bootstrap.CommitAndPushAll(workspace, repos, branch, message, git)` (`internal/bootstrap/repo.go:62-74`); single-repo `bootstrap.CommitAndPush(dir, branch, message, git)` (`repo.go:47-59`). `CommitAndPush` skips commit+push when `git diff --cached --quiet` returns nil (clean tree).
- `turn.Record` (`internal/turn/turn.go:18-37`) is marshalled verbatim by `webhook.Sender.Deliver` -> `json.Marshal(rec)` (`internal/webhook/webhook.go:67`). Adding a JSON field to `Record` surfaces it to the operator with no other wiring. Fields with `json:"-"` (Text, CallbackURL) are not sent.
- `metrics.Metrics` lives in `internal/metrics/metrics.go`; add a collector to the struct, construct it in `New`, and add it to the `reg.MustRegister(...)` list (mirror `TurnResumes`, lines 35, 98-99, 114).
- Transcript content-block shape is already modelled in `internal/transcript/tailer.go:215-228` (`contentBlock` with `Type`, `Name`, `ID`, `ToolUseID`, `IsError`). The synchronous one-shot readers live in `internal/transcript/result.go` (mirror `LastMessage`/`LastAssistant` style: open, `bufio.Scanner` with `maxPartialBytes` buffer, skip malformed lines). Tests for that package are in `result_test.go` (inline JSONL string literals).
- `bootstrap` tests are package `bootstrap_test` (external) using `github.com/stretchr/testify/require`; repo tests in `internal/bootstrap/repo_test.go` use helpers `callsContainingAll`, `argsContainAll`, `errFake` (defined in `bootstrap_test.go`).

> **Spec divergence note for the orchestrator:** the design (`...specs/2026-06-23-implement-false-refusal-rootcause-design.md` lines 104-105, 122-123) says the wrapper detects the non-200 "on a non-200 from a critical outcome tool" and surfaces the pushed list "via the existing change-summary / MCP path". There is NO in-process MCP path and NO "change summary" struct in the wrapper. The faithful, KISS realisation is: detect via the transcript `is_error` tool_result (the operator's 400 manifests there as `is_error:true`), and surface the pushed-repo list as a new `turn.Record.PushedRepos` JSON field on the existing webhook callback. This plan implements that. Defect A "WARNING comment for in-scope-but-unmodified repo" and the `ReposInScope` prompt injection are operator-side (WT-4), not in this slice.

---

## Task 1: `transcript.FailedCriticalOutcome` detects a rejected critical-outcome MCP tool call

Add a synchronous transcript reader that returns the name and error text of the LAST critical-outcome MCP tool call in a turn that came back as an error. "Critical outcome tool" = `decline_implementation` or `already_done`, however the MCP namespace prefixes them (`mcp__tatara__decline_implementation`, etc.). Detection: build a map of `tool_use_id -> bare tool name` for critical-outcome `tool_use` blocks, then find the last `tool_result` block with `is_error:true` whose `tool_use_id` is in that map.

**Interfaces**
- Consumes: a transcript JSONL file path (string). Reuses `transcript.maxPartialBytes` (already defined in the package).
- Produces:
  ```go
  // in internal/transcript/result.go
  // FailedCriticalOutcome scans the LAST turn region of the JSONL transcript at
  // path for a critical-outcome MCP tool call (decline_implementation /
  // already_done, under any MCP namespace prefix) whose tool_result came back
  // is_error:true. Returns the bare tool name (e.g. "decline_implementation"),
  // the error text the operator returned, and found=true on a hit. found=false
  // with nil error means no failed critical-outcome call (the common case).
  func FailedCriticalOutcome(path string) (tool, errText string, found bool, err error)
  ```

**Steps**

- [ ] Read `internal/transcript/result.go` and `internal/transcript/result_test.go` to confirm the scanner idiom and `maxPartialBytes` usage.

- [ ] Write the failing test. Append to `internal/transcript/result_test.go`:

```go
// TestFailedCriticalOutcome covers the rejected-decline detection that the
// wrapper uses to re-prompt the agent instead of finishing a turn silently.
func TestFailedCriticalOutcome(t *testing.T) {
	tests := []struct {
		name      string
		lines     []string
		wantTool  string
		wantErrSub string
		wantFound bool
	}{
		{
			name: "rejected decline_implementation",
			lines: []string{
				`{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"mcp__tatara__decline_implementation","input":{"reason":"   "}}]}}`,
				`{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","is_error":true,"content":"400: reason must not be blank"}]}}`,
			},
			wantTool:   "decline_implementation",
			wantErrSub: "reason must not be blank",
			wantFound:  true,
		},
		{
			name: "rejected already_done",
			lines: []string{
				`{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"a9","name":"mcp__tatara__already_done","input":{}}]}}`,
				`{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"a9","is_error":true,"content":"400: reason required"}]}}`,
			},
			wantTool:   "already_done",
			wantErrSub: "reason required",
			wantFound:  true,
		},
		{
			name: "successful decline is not a hit",
			lines: []string{
				`{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"mcp__tatara__decline_implementation","input":{"reason":"blocked on creds"}}]}}`,
				`{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","is_error":false,"content":"ok"}]}}`,
			},
			wantFound: false,
		},
		{
			name: "unrelated failing tool is not a hit",
			lines: []string{
				`{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"b1","name":"Bash","input":{"command":"false"}}]}}`,
				`{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"b1","is_error":true,"content":"exit 1"}]}}`,
			},
			wantFound: false,
		},
		{
			name: "later successful retry supersedes earlier failure",
			lines: []string{
				`{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"mcp__tatara__decline_implementation","input":{"reason":""}}]}}`,
				`{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","is_error":true,"content":"400"}]}}`,
				`{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"t2","name":"mcp__tatara__decline_implementation","input":{"reason":"real"}}]}}`,
				`{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t2","is_error":false,"content":"ok"}]}}`,
			},
			wantFound: false,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			dir := t.TempDir()
			path := filepath.Join(dir, "t.jsonl")
			require.NoError(t, os.WriteFile(path, []byte(strings.Join(tt.lines, "\n")+"\n"), 0o644))

			tool, errText, found, err := transcript.FailedCriticalOutcome(path)
			require.NoError(t, err)
			require.Equal(t, tt.wantFound, found)
			if tt.wantFound {
				require.Equal(t, tt.wantTool, tool)
				require.Contains(t, errText, tt.wantErrSub)
			}
		})
	}
}

func TestFailedCriticalOutcome_MissingFileErrors(t *testing.T) {
	_, _, _, err := transcript.FailedCriticalOutcome(filepath.Join(t.TempDir(), "nope.jsonl"))
	require.Error(t, err)
}
```

  If `result_test.go` is package `transcript` (internal) rather than `transcript_test`, change the qualified `transcript.FailedCriticalOutcome` to bare `FailedCriticalOutcome` and drop the import. Verify the package clause at the top of `result_test.go` first; match it. Ensure imports include `os`, `path/filepath`, `strings`, `testing`, and `github.com/stretchr/testify/require` (add any missing).

- [ ] Run it red:
  ```
  mise exec -- go test ./internal/transcript/ -run TestFailedCriticalOutcome
  ```
  Expected failure: `undefined: transcript.FailedCriticalOutcome` (compile error). That is the red state.

- [ ] Minimal impl. Append to `internal/transcript/result.go`:

```go
// criticalOutcomeTools is the set of MCP outcome tools whose rejection by the
// operator must be propagated back to the agent (a silently dropped decline is
// what produced the false "refused-no-explanation" park). Matched on the bare
// tool name after stripping any MCP namespace prefix (mcp__<server>__<name>).
var criticalOutcomeTools = map[string]bool{
	"decline_implementation": true,
	"already_done":           true,
}

// bareToolName strips an MCP namespace prefix ("mcp__tatara__decline_implementation"
// -> "decline_implementation"). A non-prefixed name is returned unchanged.
func bareToolName(name string) string {
	if i := strings.LastIndex(name, "__"); i >= 0 {
		return name[i+2:]
	}
	return name
}

// outcomeLine is the minimal view of a transcript line carrying tool_use and
// tool_result blocks, used by FailedCriticalOutcome. content is decoded as an
// array of blocks; genuine string-content user prompts simply fail to unmarshal
// into the array and are skipped.
type outcomeLine struct {
	Type    string `json:"type"`
	Message *struct {
		Content []struct {
			Type      string `json:"type"`
			ID        string `json:"id"`
			Name      string `json:"name"`
			ToolUseID string `json:"tool_use_id"`
			IsError   bool   `json:"is_error"`
			Content   json.RawMessage `json:"content"`
		} `json:"content"`
	} `json:"message"`
}

// FailedCriticalOutcome scans the JSONL transcript at path for a critical-outcome
// MCP tool call (decline_implementation / already_done, under any MCP namespace
// prefix) whose tool_result came back is_error:true. It returns the bare tool
// name, the operator's error text, and found=true on a hit. found=false (nil
// error) is the common no-failure case. A later non-error result for the same
// tool_use_id supersedes an earlier failure (the agent already corrected).
func FailedCriticalOutcome(path string) (tool, errText string, found bool, err error) {
	f, oerr := os.Open(path)
	if oerr != nil {
		return "", "", false, fmt.Errorf("open transcript: %w", oerr)
	}
	defer func() { _ = f.Close() }()

	criticalUseID := map[string]string{} // tool_use_id -> bare critical tool name
	failed := map[string]string{}        // tool_use_id -> error text (only failures)

	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1024*1024), maxPartialBytes)
	for sc.Scan() {
		var ol outcomeLine
		if err := json.Unmarshal(sc.Bytes(), &ol); err != nil || ol.Message == nil {
			continue
		}
		for _, b := range ol.Message.Content {
			switch b.Type {
			case "tool_use":
				if name := bareToolName(b.Name); criticalOutcomeTools[name] && b.ID != "" {
					criticalUseID[b.ID] = name
				}
			case "tool_result":
				if b.ToolUseID == "" {
					continue
				}
				if b.IsError {
					failed[b.ToolUseID] = jsonContentString(b.Content)
				} else {
					// A later success for the same call clears an earlier failure.
					delete(failed, b.ToolUseID)
				}
			}
		}
	}
	if err := sc.Err(); err != nil {
		return "", "", false, fmt.Errorf("scan transcript: %w", err)
	}

	for id, et := range failed {
		if name, ok := criticalUseID[id]; ok {
			return name, et, true, nil
		}
	}
	return "", "", false, nil
}

// jsonContentString renders a tool_result content field as a human-readable
// string. The field is either a JSON string ("400: ...") or an array of blocks;
// in the array case the raw JSON is returned so the message is never empty.
func jsonContentString(raw json.RawMessage) string {
	if isJSONString(raw) {
		var s string
		if json.Unmarshal(raw, &s) == nil {
			return s
		}
	}
	return string(raw)
}
```

  `isJSONString` and `maxPartialBytes` already exist in the package; do not redefine them. Confirm `fmt`, `os`, `bufio`, `encoding/json`, `strings` are imported in `result.go` (`strings` may be missing - add it).

- [ ] Run it green:
  ```
  mise exec -- go test ./internal/transcript/ -run TestFailedCriticalOutcome
  ```
  Expected: `ok  github.com/szymonrychu/tatara-claude-code-wrapper/internal/transcript`.

- [ ] Lint:
  ```
  mise exec -- golangci-lint run ./internal/transcript/...
  ```

- [ ] Commit:
  ```
  git add internal/transcript/result.go internal/transcript/result_test.go && git commit -m "feat: detect rejected critical-outcome MCP tool call in transcript"
  ```

---

## Task 2: `CommitAndPush` reports whether it pushed; `CommitAndPushAll` returns the pushed-repo names

Change the two commit/push helpers so callers learn exactly which repos had a diff and pushed. `CommitAndPush` returns `(pushed bool, err error)`; a clean tree yields `(false, nil)`. `CommitAndPushAll` returns `(pushed []string, err error)` listing the `RepoSpec.Name` of every repo it pushed.

**Interfaces**
- Consumes (callers in `cmd/wrapper/app.go` updated in Task 4): `bootstrap.GitRunner`, `[]bootstrap.RepoSpec`.
- Produces (signature change in `internal/bootstrap/repo.go`):
  ```go
  func CommitAndPush(dir, branch, message string, git GitRunner) (pushed bool, err error)
  func CommitAndPushAll(workspace string, repos []RepoSpec, branch, message string, git GitRunner) (pushed []string, err error)
  ```

**Steps**

- [ ] Read `internal/bootstrap/repo.go` (lines 44-74) and `internal/bootstrap/repo_test.go` (the three tests `TestCommitAndPush_UsesNoVerify`, `TestCommitAndPushAll_SkipsEmptyNamespace`, `TestCommitAndPush_CleanTree_NoPush`) to learn the helpers `callsContainingAll`, `argsContainAll`, `errFake`.

- [ ] Update the existing tests to the new signatures and add pushed-list assertions. In `internal/bootstrap/repo_test.go`:

  Replace the `TestCommitAndPush_UsesNoVerify` call+check:
  ```go
	pushed, err := bootstrap.CommitAndPush("/tmp/dir", "feat/x", "tatara agent: feat/x", fakeGit)
	require.NoError(t, err)
	require.True(t, pushed, "dirty tree must report pushed=true")
  ```

  Replace the `TestCommitAndPush_CleanTree_NoPush` call+check:
  ```go
	pushed, err := bootstrap.CommitAndPush("/tmp/dir", "feat/x", "msg", fakeGit)
	require.NoError(t, err)
	require.False(t, pushed, "clean tree must report pushed=false")
  ```

  Replace the `TestCommitAndPushAll_SkipsEmptyNamespace` call+check (the existing fakeGit marks the tree dirty for every repo, so only the one valid-namespace repo pushes):
  ```go
	pushedRepos, err := bootstrap.CommitAndPushAll("/tmp/ws", repos, "feat/x", "msg", fakeGit)
	require.NoError(t, err)
	require.Equal(t, []string{"good"}, pushedRepos, "only the valid-namespace repo must be reported pushed")
  ```

- [ ] Add a new test for the mixed clean/dirty case. Append to `internal/bootstrap/repo_test.go`:

```go
// TestCommitAndPushAll_ReturnsOnlyDirtyRepos asserts the returned list contains
// exactly the repos that had a diff and pushed; a clean repo is omitted.
func TestCommitAndPushAll_ReturnsOnlyDirtyRepos(t *testing.T) {
	// dirtyByDir[dir]=true makes "diff --cached --quiet" report dirty for that dir.
	dirtyByDir := map[string]bool{
		filepath.Join("/tmp/ws", "owner", "dirtyrepo"): true,
	}
	fakeGit := func(dir string, args ...string) error {
		if len(args) >= 2 && args[0] == "diff" && args[1] == "--cached" {
			if dirtyByDir[dir] {
				return errFake("dirty")
			}
			return nil // clean
		}
		return nil
	}

	repos := []bootstrap.RepoSpec{
		{Name: "dirty", URL: "https://github.com/owner/dirtyrepo"},
		{Name: "clean", URL: "https://github.com/owner/cleanrepo"},
	}
	pushed, err := bootstrap.CommitAndPushAll("/tmp/ws", repos, "feat/x", "msg", fakeGit)
	require.NoError(t, err)
	require.Equal(t, []string{"dirty"}, pushed)
}
```

- [ ] Run it red:
  ```
  mise exec -- go test ./internal/bootstrap/ -run 'TestCommitAndPush'
  ```
  Expected failure: compile error `assignment mismatch: 2 variables but bootstrap.CommitAndPush returns 1 value` (the helpers still return a single value).

- [ ] Minimal impl. In `internal/bootstrap/repo.go` replace `CommitAndPush` and `CommitAndPushAll`:

```go
// CommitAndPush stages all changes, and when something is staged commits and
// pushes the branch to origin. A clean tree is left untouched: nothing is
// committed or pushed, so no empty remote branch is created. Returns pushed=true
// only when a commit was made and the push succeeded.
func CommitAndPush(dir, branch, message string, git GitRunner) (pushed bool, err error) {
	if err := git(dir, "add", "-A"); err != nil {
		return false, err
	}
	// `diff --cached --quiet` exits zero (nil) when the tree is clean.
	if git(dir, "diff", "--cached", "--quiet") == nil {
		return false, nil
	}
	if err := git(dir, "commit", "--no-verify", "-m", message); err != nil {
		return false, err
	}
	if err := git(dir, "push", "--no-verify", "-u", "origin", branch); err != nil {
		return false, err
	}
	return true, nil
}

// CommitAndPushAll runs CommitAndPush in each repo dir under workspace and
// returns the Name of every repo that had a diff and pushed, so the caller can
// report the exact touched-repo set to the operator.
func CommitAndPushAll(workspace string, repos []RepoSpec, branch, message string, git GitRunner) (pushed []string, err error) {
	for _, r := range repos {
		ns := namespacePath(r.URL)
		if ns == "" || filepath.Clean(filepath.Join(workspace, ns)) == filepath.Clean(workspace) {
			continue // no valid namespace: skip to avoid operating on the workspace root
		}
		dir := filepath.Join(workspace, ns)
		ok, perr := CommitAndPush(dir, branch, message, git)
		if perr != nil {
			return pushed, fmt.Errorf("commit/push %s: %w", r.Name, perr)
		}
		if ok {
			pushed = append(pushed, r.Name)
		}
	}
	return pushed, nil
}
```

- [ ] Run it green:
  ```
  mise exec -- go test ./internal/bootstrap/ -run 'TestCommitAndPush'
  ```
  Expected: `ok`. (The package will not fully build yet because `cmd/wrapper/app.go` still calls the old single-return signatures - that is Task 4. Restrict to `./internal/bootstrap/` here; it compiles in isolation.)

- [ ] Lint the package:
  ```
  mise exec -- golangci-lint run ./internal/bootstrap/...
  ```

- [ ] Commit:
  ```
  git add internal/bootstrap/repo.go internal/bootstrap/repo_test.go && git commit -m "feat: CommitAndPush(All) report the repos actually pushed"
  ```

---

## Task 3: add `PushedRepos` to the turn Record and a re-prompt metric

Add the JSON field that carries the touched-repo list to the operator on the existing callback, and a counter for outcome-rejection re-prompts (rule 13).

**Interfaces**
- Produces:
  ```go
  // in internal/turn/turn.go, on type Record
  PushedRepos []string `json:"pushedRepos,omitempty"`
  ```
  ```go
  // in internal/metrics/metrics.go, on type Metrics
  OutcomeRepromptTotal *prometheus.CounterVec // labels: tool=decline_implementation|already_done, result=reprompted|budget_exhausted
  ```
- Consumes (Task 4): `rec.PushedRepos`, `m.OutcomeRepromptTotal`.

**Steps**

- [ ] Read `internal/turn/turn.go` and the `Metrics` struct/`New`/register list in `internal/metrics/metrics.go`.

- [ ] Add the Record field. In `internal/turn/turn.go`, inside `type Record struct`, after the `ConversationObjectKey` field (line 36):

```go
	// PushedRepos is the set of project repos this turn actually committed and
	// pushed (had a diff). Reported to the operator on the callback so it knows
	// which repos were touched in a multi-repo task (Defect A). Empty/absent for
	// a turn that pushed nothing or a single-repo task with no diff.
	PushedRepos []string `json:"pushedRepos,omitempty"`
```

- [ ] Add the metric field. In `internal/metrics/metrics.go`, in `type Metrics struct`, after `TurnResumes` (line 35):

```go
	// Outcome-rejection re-prompt counter (rule 13): a critical outcome tool
	// (decline_implementation/already_done) the operator rejected, surfaced back
	// to the agent for correction instead of finishing the turn silently.
	OutcomeRepromptTotal *prometheus.CounterVec // labels: tool, result=reprompted|budget_exhausted
```

  In `New`, after the `TurnResumes` constructor (line 99):

```go
		OutcomeRepromptTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "ccw_outcome_reprompt_total", Help: "Critical-outcome MCP tool rejections re-prompted to the agent, by tool and result."}, []string{"tool", "result"}),
```

  In the `reg.MustRegister(...)` call, add `m.OutcomeRepromptTotal` to the list (e.g. on the `m.AuthTotal, m.TurnResumes, m.BootstrapRenderTotal,` line):

```go
		m.AuthTotal, m.TurnResumes, m.OutcomeRepromptTotal, m.BootstrapRenderTotal,
```

- [ ] Verify it compiles (no behavioural test needed; these are plumbing fields exercised in Task 4):
  ```
  mise exec -- go build ./internal/turn/... ./internal/metrics/...
  ```
  Expected: clean build.

- [ ] Commit:
  ```
  git add internal/turn/turn.go internal/metrics/metrics.go && git commit -m "feat: add PushedRepos record field and outcome-reprompt metric"
  ```

---

## Task 4: app finaliser records pushed repos and re-prompts on a rejected outcome tool

Wire the two features into `cmd/wrapper/app.go`'s `sess.OnTurnDone` finaliser:
1. Capture the pushed-repo list from `CommitAndPushAll` (and, in single-repo mode, the `pushed` bool) and set `rec.PushedRepos` before the callback.
2. After push+upload, before delivering the callback, scan the just-completed turn's transcript via `transcript.FailedCriticalOutcome`. On a hit and while a bounded budget remains, re-prompt the live session with `sess.Submit` (a corrective message instructing the agent to retry the outcome tool with a non-blank reason) and skip this turn's callback (the re-prompted turn's own completion delivers it). On budget exhaustion, fall through to the normal callback (the operator's existing empty-retry cap then handles it).

The re-prompt budget lives on the `app` struct, guarded by a mutex, capped at 2 (KISS: two corrections is plenty; the operator cap is the backstop).

**Interfaces**
- Consumes: `transcript.FailedCriticalOutcome(path) (tool, errText string, found bool, err error)`, `bootstrap.CommitAndPush(dir, branch, msg, git) (bool, error)`, `bootstrap.CommitAndPushAll(ws, repos, branch, msg, git) ([]string, error)`, `sess.TranscriptPath() string`, `sess.Submit(text, cb string) (string, error)`, `m.OutcomeRepromptTotal`, `rec.PushedRepos`.
- Produces: corrected finaliser behaviour; new field `a.outcomeReprompts int` + `a.repromptMu sync.Mutex` on `type app`.

**Steps**

- [ ] Read `cmd/wrapper/app.go` lines 28-42 (`type app`) and 153-229 (the `OnTurnDone` closure). Note `transcript` is not yet imported there; the package import path is `github.com/szymonrychu/tatara-claude-code-wrapper/internal/transcript`.

- [ ] Add the re-prompt budget fields to `type app`. In `cmd/wrapper/app.go`, inside `type app struct` after `finishHook func(context.Context)` (line 41):

```go
	// repromptMu guards outcomeReprompts, the per-pod count of times a rejected
	// critical-outcome MCP tool call (decline_implementation/already_done) was
	// surfaced back to the agent via a re-prompt instead of finishing the turn
	// silently (Defect C). Capped at maxOutcomeReprompts so a perpetually-failing
	// agent still reaches the operator's empty-retry cap rather than looping here.
	repromptMu       sync.Mutex
	outcomeReprompts int
```

- [ ] Add the import. In the import block of `cmd/wrapper/app.go`, add (alphabetical, after the `storage` line or near the other internal imports):

```go
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/transcript"
```

- [ ] Add the cap constant and the re-prompt helper. Add near the top of `cmd/wrapper/app.go` (after the `import` block, before `type app`):

```go
// maxOutcomeReprompts bounds how many times this pod re-prompts the agent after
// the operator rejects a critical-outcome tool call. Beyond this the finaliser
// delivers the callback normally and the operator's empty-retry cap takes over.
const maxOutcomeReprompts = 2
```

- [ ] Update the commit/push block to capture the pushed list. In the `OnTurnDone` goroutine (lines 167-189), replace the `if cfg.TaskBranch != "" { ... }` block body so the multi-repo and single-repo paths set `rec.PushedRepos`:

  Replace this current code:
  ```go
			if cfg.TaskBranch != "" {
				pushStart := time.Now()
				var err error
				if len(cfg.Repos) > 0 {
					err = bootstrap.CommitAndPushAll(cfg.Workspace, cfg.Repos, cfg.TaskBranch, "tatara agent: "+cfg.TaskBranch, gitRunner())
				} else {
					// Single-repo clones into workspace/<owner>/<repo>, not the
					// workspace root, so commit/push must target that subdir.
					repoDir := bootstrap.RepoDir(cfg.Workspace, cfg.RepoURL)
					if repoDir == "" {
						err = fmt.Errorf("cannot derive repo dir from REPO_URL %q for commit/push", cfg.RepoURL)
					} else {
						err = bootstrap.CommitAndPush(repoDir, cfg.TaskBranch, "tatara agent: "+cfg.TaskBranch, gitRunner())
					}
				}
				if err != nil {
					m.CommitPushTotal.WithLabelValues("fail").Inc()
					log.Error("commit/push failed", "action", "commit_push", "branch", cfg.TaskBranch, "error", err, "duration_ms", time.Since(pushStart).Milliseconds())
				} else {
					m.CommitPushTotal.WithLabelValues("ok").Inc()
					log.Info("commit/push succeeded", "action", "commit_push", "branch", cfg.TaskBranch, "duration_ms", time.Since(pushStart).Milliseconds())
				}
			}
  ```

  with:
  ```go
			if cfg.TaskBranch != "" {
				pushStart := time.Now()
				var err error
				if len(cfg.Repos) > 0 {
					var pushedRepos []string
					pushedRepos, err = bootstrap.CommitAndPushAll(cfg.Workspace, cfg.Repos, cfg.TaskBranch, "tatara agent: "+cfg.TaskBranch, gitRunner())
					rec.PushedRepos = pushedRepos
				} else {
					// Single-repo clones into workspace/<owner>/<repo>, not the
					// workspace root, so commit/push must target that subdir.
					repoDir := bootstrap.RepoDir(cfg.Workspace, cfg.RepoURL)
					if repoDir == "" {
						err = fmt.Errorf("cannot derive repo dir from REPO_URL %q for commit/push", cfg.RepoURL)
					} else {
						var pushed bool
						pushed, err = bootstrap.CommitAndPush(repoDir, cfg.TaskBranch, "tatara agent: "+cfg.TaskBranch, gitRunner())
						if pushed {
							rec.PushedRepos = []string{primaryRepoName(cfg)}
						}
					}
				}
				if err != nil {
					m.CommitPushTotal.WithLabelValues("fail").Inc()
					log.Error("commit/push failed", "action", "commit_push", "branch", cfg.TaskBranch, "error", err, "duration_ms", time.Since(pushStart).Milliseconds())
				} else {
					m.CommitPushTotal.WithLabelValues("ok").Inc()
					log.Info("commit/push succeeded", "action", "commit_push", "branch", cfg.TaskBranch,
						"pushed_repos", rec.PushedRepos, "duration_ms", time.Since(pushStart).Milliseconds())
				}
			}
  ```

- [ ] Insert the re-prompt check immediately before the callback delivery. In the same goroutine, the current tail is:
  ```go
			url := rec.CallbackURL
			if url == "" {
				url = defaultCB
			}
			sender.Deliver(url, rec)
  ```
  Insert, BEFORE `url := rec.CallbackURL`:

  ```go
			// Defect C: a critical outcome tool (decline_implementation/already_done)
			// the operator rejected (e.g. blank reason -> 400) shows up in the turn
			// transcript as an is_error tool_result. Rather than let the turn finish
			// silently (which the operator misreads as "refused-no-explanation"),
			// re-prompt the agent to retry with a non-blank reason, and skip THIS
			// turn's callback - the re-prompted turn delivers its own. Bounded by
			// maxOutcomeReprompts so a stuck agent still reaches the operator's cap.
			if path := a.sess.TranscriptPath(); path != "" {
				toolName, errText, found, ferr := transcript.FailedCriticalOutcome(path)
				if ferr != nil {
					log.Warn("outcome-reprompt scan failed; delivering callback as-is",
						"action", "outcome_reprompt", "turn_id", rec.ID, "error", ferr)
				} else if found {
					if a.reprompt(toolName, errText, rec.CallbackURL) {
						m.OutcomeRepromptTotal.WithLabelValues(toolName, "reprompted").Inc()
						log.Info("re-prompted agent after rejected outcome tool; suppressing this turn's callback",
							"action", "outcome_reprompt", "turn_id", rec.ID, "tool", toolName, "error", errText)
						return
					}
					m.OutcomeRepromptTotal.WithLabelValues(toolName, "budget_exhausted").Inc()
					log.Warn("outcome re-prompt budget exhausted; delivering callback so the operator cap applies",
						"action", "outcome_reprompt", "turn_id", rec.ID, "tool", toolName)
				}
			}

  ```

- [ ] Add the `reprompt` method and `primaryRepoName` helper to `cmd/wrapper/app.go` (after the `app` methods, e.g. after `buildBootstrapParams`):

```go
// reprompt submits a corrective turn telling the agent its critical outcome
// tool call was rejected and must be retried with a non-blank reason. It returns
// true when a re-prompt was issued, false when the budget is exhausted or the
// session would not accept a new turn (in which case the caller delivers the
// callback so the operator's empty-retry cap applies). The corrective text reuses
// the same callback URL so the eventual completion still reaches the operator.
func (a *app) reprompt(tool, errText, callbackURL string) bool {
	a.repromptMu.Lock()
	if a.outcomeReprompts >= maxOutcomeReprompts {
		a.repromptMu.Unlock()
		return false
	}
	a.outcomeReprompts++
	a.repromptMu.Unlock()

	msg := fmt.Sprintf("Your %s call was rejected by the operator: %s. "+
		"This is mandatory: call %s again with a clear, non-blank `reason` explaining the decision. "+
		"Do not finish the turn until the call succeeds.", tool, strings.TrimSpace(errText), tool)
	if _, err := a.sess.Submit(msg, callbackURL); err != nil {
		// Roll back the budget consumption: no turn was actually submitted.
		a.repromptMu.Lock()
		a.outcomeReprompts--
		a.repromptMu.Unlock()
		a.log.Warn("outcome re-prompt submit failed; delivering callback instead",
			"action", "outcome_reprompt", "tool", tool, "error", err)
		return false
	}
	return true
}

// primaryRepoName is the human-facing name of the single repo a non-cross-repo
// pod is bound to, derived from the namespace path of REPO_URL ("owner/repo").
// Used to populate PushedRepos in single-repo mode where there is no RepoSpec.
func primaryRepoName(cfg config) string {
	if dir := bootstrap.RepoDir(cfg.Workspace, cfg.RepoURL); dir != "" {
		return filepath.Base(dir)
	}
	return cfg.RepoURL
}
```

  This adds a `path/filepath` use in `app.go`; confirm `path/filepath` is imported (it is not in the current import list - add it). `strings` and `fmt` are already imported.

- [ ] Build the whole module to confirm the call-site signatures all line up:
  ```
  mise exec -- go build ./...
  ```
  Expected: clean build (this is where the Task 2 signature change finally meets its callers).

- [ ] Write a unit test for the `reprompt` budget logic with a fake session. The session is concrete (`*session.Manager`), so test `reprompt` against a small interface is not possible without refactor; instead test the budget arithmetic via a focused test that drives `a.reprompt` with a stub Submit. KISS approach: extract the Submit call behind a tiny field so it is injectable in the test. Add to `type app`:

```go
	// submitFn is the turn-submit primitive reprompt() uses; production wires it
	// to a.sess.Submit. Injectable so the re-prompt budget logic is unit-testable
	// without a live PTY session.
	submitFn func(text, callbackURL string) (string, error)
```

  Wire it in `newApp` where `a := &app{...}` is built (lines 141-151): after the struct literal, add:
  ```go
	a.submitFn = a.sess.Submit
  ```

  And change `reprompt` to call `a.submitFn` instead of `a.sess.Submit`:
  ```go
	if _, err := a.submitFn(msg, callbackURL); err != nil {
  ```

- [ ] Add the test file `cmd/wrapper/reprompt_test.go`:

```go
package main

import (
	"errors"
	"sync"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestReprompt_BudgetAndRollback(t *testing.T) {
	t.Run("re-prompts up to the cap then refuses", func(t *testing.T) {
		var calls int
		a := &app{log: testLogger(), submitFn: func(text, cb string) (string, error) {
			calls++
			require.Contains(t, text, "rejected by the operator")
			require.Contains(t, text, "non-blank")
			require.Equal(t, "https://cb/x", cb)
			return "turn-1", nil
		}}
		require.True(t, a.reprompt("decline_implementation", "400: blank reason", "https://cb/x"))
		require.True(t, a.reprompt("decline_implementation", "400", "https://cb/x"))
		require.False(t, a.reprompt("decline_implementation", "400", "https://cb/x"),
			"third re-prompt must be refused once the budget is exhausted")
		require.Equal(t, maxOutcomeReprompts, calls)
	})

	t.Run("submit failure rolls back the budget", func(t *testing.T) {
		fail := true
		var calls int
		a := &app{log: testLogger(), submitFn: func(text, cb string) (string, error) {
			calls++
			if fail {
				return "", errors.New("session busy")
			}
			return "turn-2", nil
		}}
		require.False(t, a.reprompt("already_done", "400", ""), "a failed submit must not count against the budget")
		fail = false
		require.True(t, a.reprompt("already_done", "400", ""), "budget must be intact after a failed submit")
		require.Equal(t, 2, calls)
	})

	t.Run("concurrent re-prompts never exceed the cap", func(t *testing.T) {
		var mu sync.Mutex
		var calls int
		a := &app{log: testLogger(), submitFn: func(text, cb string) (string, error) {
			mu.Lock()
			calls++
			mu.Unlock()
			return "t", nil
		}}
		var wg sync.WaitGroup
		for i := 0; i < 10; i++ {
			wg.Add(1)
			go func() { defer wg.Done(); a.reprompt("decline_implementation", "e", "") }()
		}
		wg.Wait()
		require.LessOrEqual(t, calls, maxOutcomeReprompts)
	})
}
```

  Check `cmd/wrapper/app_audit_test.go` / `config_test.go` for an existing `testLogger()` helper; if none exists, add one to `reprompt_test.go`:
  ```go
  func testLogger() *slog.Logger { return slog.New(slog.NewTextHandler(io.Discard, nil)) }
  ```
  with imports `io`, `log/slog`. If a `testLogger` already exists in package `main`, reuse it and do not redefine.

- [ ] Run it red first (before adding `submitFn` wiring you would get a compile error; after wiring, run):
  ```
  mise exec -- go test ./cmd/wrapper/ -run TestReprompt_BudgetAndRollback
  ```
  Expected after impl: `ok`. If you run before adding the `reprompt` method/`submitFn` field, expected red is `undefined: app.reprompt` / `unknown field submitFn`.

- [ ] Run the full wrapper suite to confirm no regression in the finaliser-adjacent tests:
  ```
  mise exec -- go test ./...
  ```
  Expected: all packages `ok`. (Integration tests are behind `//go:build integration` and excluded from the default run.)

- [ ] Lint:
  ```
  mise exec -- golangci-lint run
  ```

- [ ] Commit:
  ```
  git add cmd/wrapper/app.go cmd/wrapper/reprompt_test.go && git commit -m "feat: re-prompt agent on rejected outcome tool and report pushed repos"
  ```

---

## Task 5: code review + verification before completion

**Interfaces**
- Consumes: the full WT-3 diff on this branch.
- Produces: a clean review, green full test+lint+build run, no uncommitted changes.

**Steps**

- [ ] Invoke superpowers:requesting-code-review on the branch diff (`git diff main...HEAD`). Focus areas: (1) the transcript parser correctly attributes a failed `tool_result` to a critical `tool_use` across the whole turn region and lets a later success supersede an earlier failure; (2) the re-prompt budget is race-free and rolls back on submit failure; (3) `rec.PushedRepos` is set on both the multi-repo and single-repo success paths and never on the failure path; (4) the suppressed-callback `return` does not skip the conversation upload or any required cleanup (note: upload already ran before the re-prompt check - confirm ordering in the final code).

- [ ] Fix every critical/high finding. Re-run the affected package tests after each fix.

- [ ] Invoke superpowers:verification-before-completion. Run and paste real output for:
  ```
  mise exec -- go build ./...
  mise exec -- go test ./...
  mise exec -- golangci-lint run
  git status --porcelain
  ```
  Required: build clean, all tests `ok`, lint clean, `git status --porcelain` empty (everything committed).

- [ ] Confirm against the design's verification bullets that apply to this slice:
  - A whitespace/blank decline reason rejected by the operator now causes the wrapper to re-prompt the agent (covered by `TestFailedCriticalOutcome` + `TestReprompt_BudgetAndRollback`), instead of the turn finishing silently.
  - A multi-repo task reports its touched repos to the operator via `rec.PushedRepos` (covered by `TestCommitAndPushAll_ReturnsOnlyDirtyRepos` + the app finaliser wiring).

- [ ] If `MEMORY.md` exists at the repo root, append one dated line: the wrapper has no in-process MCP proxy, so outcome-rejection is detected from the transcript `is_error` tool_result and the touched-repo set is surfaced as `turn.Record.PushedRepos` on the existing webhook callback. Commit with `docs: note transcript-based outcome-reprompt + PushedRepos decision`.

- [ ] Do NOT merge, push, build an image, or open a tatara-helmfile MR. Deploy is orchestrated separately (cli image first, then this wrapper; see the design's Deploy section). Report the branch as ready for the WT-3 merge step.
