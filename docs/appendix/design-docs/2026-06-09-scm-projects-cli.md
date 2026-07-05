# SCM Projects CLI Tools Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Implementation subagents run sonnet; the merge/integration agent runs opus. Develop in a git worktree off `main`, merge back to `main`, build only from `main`. Steps use checkbox (- [ ]) syntax.

**Goal:** Add 3 operator-mediated MCP intent tools to `tatara-cli` so agents can emit SCM intent (propose an issue, render a PR/MR review verdict, decide a self-authored PR/MR outcome) without ever calling SCM directly. Each tool is a thin CRD-write over the operator REST API; the operator's TaskReconciler is the sole SCM egress path. Operator tool count rises 9 -> 12.

**Architecture:** `tatara-cli` exposes tatara REST endpoints as MCP tools. `Tool` carries a `Target` (`TargetMemory`|`TargetOperator`) and a `Build func(map[string]any) (method, path string, body any, err error)`. `OperatorTools()` returns the operator-targeted set; each entry is constructed via the local `op(name, desc, schema, build)` helper and resolves `task`/`project` via `argOrEnv(a, key, envKey)` (arg wins, else env `TATARA_TASK`/`TATARA_PROJECT`). `Server.register` wraps each `Tool` via `buildTool` -> `mcplib.NewToolWithRawSchema(name, desc, schema)` (RawInputSchema only; setting both InputSchema and RawInputSchema breaks `tools/list` marshalling - see MEMORY 2026-06-09). The 3 new tools are pure `op(...)` entries; no new types, no new helpers.

**Tech Stack:** Go 1.25.5, stdlib `net/http`/`net/url`, `encoding/json`; tests with `testify` (`require`/`assert`) and `net/http/httptest`. gofmt + golangci-lint clean. Table-driven tests with `t.Run`.

---

## File Structure

| File | Create/Modify | Responsibility |
| --- | --- | --- |
| `tatara-cli/internal/mcp/tools.go` | Modify (`OperatorTools()` :283-390, doc comment :283, slice return) | Append 3 `op(...)` entries: `propose_issue`, `review_verdict`, `pr_outcome`. No other functions touched. |
| `tatara-cli/internal/mcp/tools_test.go` | Modify (build-path / require-args / env-fallback / count tests) | Assert the 3 new tools build the correct method+path+body, enforce required args, honor env fallback, and bump the count assertion 9 -> 12. |
| `tatara-cli/internal/mcp/server_test.go` | Modify (`TestBuildTool_AllToolsMarshal` :21-26 already iterates `OperatorTools()`; no edit needed beyond confirming it covers 12) | Confirm all 12 operator tools marshal via `NewToolWithRawSchema`; add an explicit schema-validity test for the operator set. |
| `tatara-cli/internal/version/version.go` | Modify (`Version` default) | Bump default version string to `0.5.0`. |
| `tatara-cli/MEMORY.md` | Modify (append dated entry) | Record the 3-tool addition + wire shape. |
| `tatara-cli/ROADMAP.md` | Modify (mark shipped) | Move the SCM-projects CLI item to shipped. |

---

### Task 1: Add the 3 operator intent tools (TDD)

**Files:**
- Modify: `tatara-cli/internal/mcp/tools.go` (`OperatorTools()` doc comment at `:283`, return slice ending at `:389`)
- Test: `tatara-cli/internal/mcp/tools_test.go`

Contract (lock section 4, byte-for-byte):

```
propose_issue   schema {repo|repositoryRef, title, body, kind(bug|improvement)} required: title,body,kind,repo
  -> POST /projects/{TATARA_PROJECT}/issues  body {repositoryRef,title,body,kind}
review_verdict  schema {task?, decision(approve|request_changes|comment), body, suggestions:[{path,line,body}]} required: decision
  -> POST /tasks/{task|TATARA_TASK}/review   body {decision,body,suggestions}
pr_outcome      schema {task?, action(merge|close), reason} required: action
  -> POST /tasks/{task|TATARA_TASK}/pr-outcome body {action,reason}
```

Decisions pinned for this task (state, don't ask):
- `propose_issue` resolves the project via `argOrEnv(a, "project", "TATARA_PROJECT")` and the repo via `argString(a, "repositoryRef")` falling back to `argString(a, "repo")` (lock lists `repo|repositoryRef`). Both `project` and the repo value are required at build time. Body key is always `repositoryRef` (the wire name).
- `review_verdict` / `pr_outcome` resolve the task via `argOrEnv(a, "task", "TATARA_TASK")` exactly like `task_get`.
- Optional body keys (`body`, `suggestions`, `reason`) are added only when present in args, matching the `task_update` / `subtask_update` pattern already in the file.
- Path is built with `url.PathEscape` on the project/task segment, matching every existing operator tool.

- [ ] **Step 1: Write the failing build-path test for all 3 tools.** Append to `tatara-cli/internal/mcp/tools_test.go`:

```go
func TestOperatorTools_SCMBuildPaths(t *testing.T) {
	cases := []struct {
		tool   string
		args   map[string]any
		method string
		path   string
	}{
		{"propose_issue", map[string]any{"project": "alpha", "repositoryRef": "szymonrychu/tatara", "title": "t", "body": "b", "kind": "bug"}, http.MethodPost, "/projects/alpha/issues"},
		{"propose_issue", map[string]any{"project": "alpha", "repo": "szymonrychu/tatara", "title": "t", "body": "b", "kind": "improvement"}, http.MethodPost, "/projects/alpha/issues"},
		{"review_verdict", map[string]any{"task": "t1", "decision": "approve"}, http.MethodPost, "/tasks/t1/review"},
		{"pr_outcome", map[string]any{"task": "t1", "action": "merge"}, http.MethodPost, "/tasks/t1/pr-outcome"},
	}
	for _, c := range cases {
		t.Run(c.tool, func(t *testing.T) {
			m, p, _, err := operatorToolByName(t, c.tool).Build(c.args)
			require.NoError(t, err)
			require.Equal(t, c.method, m)
			require.Equal(t, c.path, p)
		})
	}
}

func TestOperatorTools_SCMBodies(t *testing.T) {
	t.Run("propose_issue", func(t *testing.T) {
		_, _, body, err := operatorToolByName(t, "propose_issue").Build(map[string]any{
			"project": "alpha", "repo": "szymonrychu/tatara", "title": "t", "body": "b", "kind": "bug",
		})
		require.NoError(t, err)
		m := body.(map[string]any)
		require.Equal(t, "szymonrychu/tatara", m["repositoryRef"])
		require.Equal(t, "t", m["title"])
		require.Equal(t, "b", m["body"])
		require.Equal(t, "bug", m["kind"])
		_, hasRepo := m["repo"]
		require.False(t, hasRepo, "body must use repositoryRef, not repo")
	})
	t.Run("review_verdict", func(t *testing.T) {
		sugg := []any{map[string]any{"path": "a.go", "line": float64(12), "body": "fix"}}
		_, _, body, err := operatorToolByName(t, "review_verdict").Build(map[string]any{
			"task": "t1", "decision": "request_changes", "body": "no", "suggestions": sugg,
		})
		require.NoError(t, err)
		m := body.(map[string]any)
		require.Equal(t, "request_changes", m["decision"])
		require.Equal(t, "no", m["body"])
		require.Equal(t, sugg, m["suggestions"])
	})
	t.Run("review_verdict_decision_only", func(t *testing.T) {
		_, _, body, err := operatorToolByName(t, "review_verdict").Build(map[string]any{
			"task": "t1", "decision": "comment",
		})
		require.NoError(t, err)
		m := body.(map[string]any)
		require.Equal(t, "comment", m["decision"])
		_, hasBody := m["body"]
		require.False(t, hasBody)
		_, hasSugg := m["suggestions"]
		require.False(t, hasSugg)
	})
	t.Run("pr_outcome", func(t *testing.T) {
		_, _, body, err := operatorToolByName(t, "pr_outcome").Build(map[string]any{
			"task": "t1", "action": "close", "reason": "stale",
		})
		require.NoError(t, err)
		m := body.(map[string]any)
		require.Equal(t, "close", m["action"])
		require.Equal(t, "stale", m["reason"])
	})
	t.Run("pr_outcome_action_only", func(t *testing.T) {
		_, _, body, err := operatorToolByName(t, "pr_outcome").Build(map[string]any{
			"task": "t1", "action": "merge",
		})
		require.NoError(t, err)
		m := body.(map[string]any)
		require.Equal(t, "merge", m["action"])
		_, hasReason := m["reason"]
		require.False(t, hasReason)
	})
}
```

- [ ] **Step 2: Run it, expect FAIL.** `go test ./internal/mcp/ -run 'TestOperatorTools_SCM' -v`. Expected FAIL: `operatorToolByName` calls `t.Fatalf("operator tool %q not found")` because `propose_issue`/`review_verdict`/`pr_outcome` are not in `OperatorTools()` yet.

- [ ] **Step 3: Add the 3 tools.** In `tatara-cli/internal/mcp/tools.go`, insert the 3 `op(...)` entries immediately before the closing `}` of the returned slice in `OperatorTools()` (after the `subtask_update` entry that ends at line ~388, before the slice's closing `}` at `:389`):

```go
		op("propose_issue", "Propose a new SCM issue for a deferred bug or improvement; created behind the awaiting-approval label until a human approves.",
			`{"type":"object","properties":{"project":{"type":"string"},"repo":{"type":"string"},"repositoryRef":{"type":"string"},"title":{"type":"string"},"body":{"type":"string"},"kind":{"type":"string","enum":["bug","improvement"]}},"required":["title","body","kind","repo"]}`,
			func(a map[string]any) (string, string, any, error) {
				p := argOrEnv(a, "project", "TATARA_PROJECT")
				if p == "" {
					return "", "", nil, fmt.Errorf("project required")
				}
				repo := argString(a, "repositoryRef")
				if repo == "" {
					repo = argString(a, "repo")
				}
				if repo == "" {
					return "", "", nil, fmt.Errorf("repo required")
				}
				if argString(a, "title") == "" {
					return "", "", nil, fmt.Errorf("title required")
				}
				if argString(a, "body") == "" {
					return "", "", nil, fmt.Errorf("body required")
				}
				if argString(a, "kind") == "" {
					return "", "", nil, fmt.Errorf("kind required")
				}
				body := map[string]any{
					"repositoryRef": repo,
					"title":         a["title"],
					"body":          a["body"],
					"kind":          a["kind"],
				}
				return http.MethodPost, "/projects/" + url.PathEscape(p) + "/issues", body, nil
			}),
		op("review_verdict", "Record a review verdict on a human-authored PR/MR Task (decision approve|request_changes|comment, optional body and inline suggestions). The operator posts it to SCM.",
			`{"type":"object","properties":{"task":{"type":"string"},"decision":{"type":"string","enum":["approve","request_changes","comment"]},"body":{"type":"string"},"suggestions":{"type":"array","items":{"type":"object","properties":{"path":{"type":"string"},"line":{"type":"integer"},"body":{"type":"string"}},"required":["path","line","body"]}}},"required":["decision"]}`,
			func(a map[string]any) (string, string, any, error) {
				tk := argOrEnv(a, "task", "TATARA_TASK")
				if tk == "" {
					return "", "", nil, fmt.Errorf("task required")
				}
				if argString(a, "decision") == "" {
					return "", "", nil, fmt.Errorf("decision required")
				}
				body := map[string]any{"decision": a["decision"]}
				if v, ok := a["body"]; ok {
					body["body"] = v
				}
				if v, ok := a["suggestions"]; ok {
					body["suggestions"] = v
				}
				return http.MethodPost, "/tasks/" + url.PathEscape(tk) + "/review", body, nil
			}),
		op("pr_outcome", "Decide the outcome of a tatara-authored PR/MR Task (action merge|close, optional reason). selfImprove only; the operator enforces merge policy.",
			`{"type":"object","properties":{"task":{"type":"string"},"action":{"type":"string","enum":["merge","close"]},"reason":{"type":"string"}},"required":["action"]}`,
			func(a map[string]any) (string, string, any, error) {
				tk := argOrEnv(a, "task", "TATARA_TASK")
				if tk == "" {
					return "", "", nil, fmt.Errorf("task required")
				}
				if argString(a, "action") == "" {
					return "", "", nil, fmt.Errorf("action required")
				}
				body := map[string]any{"action": a["action"]}
				if v, ok := a["reason"]; ok {
					body["reason"] = v
				}
				return http.MethodPost, "/tasks/" + url.PathEscape(tk) + "/pr-outcome", body, nil
			}),
```

Also update the `OperatorTools()` doc comment at `:283` from `// OperatorTools returns the 9 tatara-operator REST tools (Target=TargetOperator).` to `// OperatorTools returns the 12 tatara-operator REST tools (Target=TargetOperator).`

- [ ] **Step 4: Run to PASS.** `go test ./internal/mcp/ -run 'TestOperatorTools_SCM' -v`. Expected: `PASS` for `TestOperatorTools_SCMBuildPaths` (all 4 subtests) and `TestOperatorTools_SCMBodies` (all 5 subtests).

- [ ] **Step 5: gofmt + golangci-lint.** `gofmt -l internal/mcp/tools.go` (expect no output) and `golangci-lint run ./internal/mcp/...` (expect clean). If gofmt prints the file, run `gofmt -w internal/mcp/tools.go`.

- [ ] **Step 6: Commit.** `git add internal/mcp/tools.go internal/mcp/tools_test.go && git commit -m "feat: add propose_issue, review_verdict, pr_outcome operator MCP tools"`

---

### Task 2: Required-args, env-fallback, marshal + schema coverage (TDD)

**Files:**
- Modify: `tatara-cli/internal/mcp/tools_test.go`
- Modify: `tatara-cli/internal/mcp/server_test.go`
- Test paths: `tatara-cli/internal/mcp/tools_test.go`, `tatara-cli/internal/mcp/server_test.go`

- [ ] **Step 1: Write the failing required-args test.** Append to `tatara-cli/internal/mcp/tools_test.go`:

```go
func TestOperatorTools_SCMRequireArgs(t *testing.T) {
	_, _, _, err := operatorToolByName(t, "propose_issue").Build(map[string]any{"repo": "r", "title": "t", "body": "b", "kind": "bug"})
	require.Error(t, err) // project required (no env set)
	_, _, _, err = operatorToolByName(t, "propose_issue").Build(map[string]any{"project": "p", "title": "t", "body": "b", "kind": "bug"})
	require.Error(t, err) // repo required
	_, _, _, err = operatorToolByName(t, "propose_issue").Build(map[string]any{"project": "p", "repo": "r", "body": "b", "kind": "bug"})
	require.Error(t, err) // title required
	_, _, _, err = operatorToolByName(t, "propose_issue").Build(map[string]any{"project": "p", "repo": "r", "title": "t", "kind": "bug"})
	require.Error(t, err) // body required
	_, _, _, err = operatorToolByName(t, "propose_issue").Build(map[string]any{"project": "p", "repo": "r", "title": "t", "body": "b"})
	require.Error(t, err) // kind required
	_, _, _, err = operatorToolByName(t, "review_verdict").Build(map[string]any{"decision": "approve"})
	require.Error(t, err) // task required (no env set)
	_, _, _, err = operatorToolByName(t, "review_verdict").Build(map[string]any{"task": "t1"})
	require.Error(t, err) // decision required
	_, _, _, err = operatorToolByName(t, "pr_outcome").Build(map[string]any{"action": "merge"})
	require.Error(t, err) // task required (no env set)
	_, _, _, err = operatorToolByName(t, "pr_outcome").Build(map[string]any{"task": "t1"})
	require.Error(t, err) // action required
}

func TestOperatorTools_SCMEnvFallback(t *testing.T) {
	t.Setenv("TATARA_TASK", "task-from-env")
	t.Setenv("TATARA_PROJECT", "proj-from-env")
	cases := []struct {
		tool string
		args map[string]any
		path string
	}{
		{"propose_issue", map[string]any{"repo": "r", "title": "t", "body": "b", "kind": "bug"}, "/projects/proj-from-env/issues"},
		{"review_verdict", map[string]any{"decision": "comment"}, "/tasks/task-from-env/review"},
		{"pr_outcome", map[string]any{"action": "merge"}, "/tasks/task-from-env/pr-outcome"},
	}
	for _, c := range cases {
		t.Run(c.tool, func(t *testing.T) {
			_, p, _, err := operatorToolByName(t, c.tool).Build(c.args)
			require.NoError(t, err)
			require.Equal(t, c.path, p)
		})
	}
}
```

- [ ] **Step 2: Run it, expect PASS already (tools exist from Task 1).** `go test ./internal/mcp/ -run 'TestOperatorTools_SCMRequireArgs|TestOperatorTools_SCMEnvFallback' -v`. Expected: PASS. If `TestOperatorTools_SCMRequireArgs` fails on the env-fallback edge (env leaking from another test), confirm no `TATARA_*` is set in the shell; these subtests deliberately do not set env so `argOrEnv` returns `""`.

- [ ] **Step 3: Bump the count assertion (failing change).** Edit `TestAllOperatorTools_Count` in `tatara-cli/internal/mcp/tools_test.go`:

```go
func TestAllOperatorTools_Count(t *testing.T) {
	require.Len(t, OperatorTools(), 12)
}
```

- [ ] **Step 4: Run, expect PASS.** `go test ./internal/mcp/ -run TestAllOperatorTools_Count -v`. Expected: PASS (Task 1 added exactly 3 tools, 9 -> 12). If it reports `len 9`, the Task 1 entries were not added; stop and fix Task 1.

- [ ] **Step 5: Add operator-schema-validity test + extend marshal coverage assertion.** The existing `TestBuildTool_AllToolsMarshal` in `server_test.go` already iterates `append(AllTools(), OperatorTools()...)`, so it now covers all 12 operator tools through `buildTool` -> `NewToolWithRawSchema` with no edit. Add an explicit operator-schema test so the 3 new raw schemas are asserted valid JSON. Append to `tatara-cli/internal/mcp/server_test.go`:

```go
func TestOperatorTools_SchemasAreValidJSON(t *testing.T) {
	tools := OperatorTools()
	require.Len(t, tools, 12)
	for _, tl := range tools {
		var v any
		require.NoErrorf(t, json.Unmarshal(tl.Schema, &v), "operator tool %q has invalid JSON schema", tl.Name)
		_, err := json.Marshal(buildTool(tl))
		require.NoErrorf(t, err, "operator tool %q must marshal for tools/list", tl.Name)
	}
}
```

- [ ] **Step 6: Run the full operator + marshal suite.** `go test ./internal/mcp/ -run 'TestOperatorTools|TestBuildTool_AllToolsMarshal|TestNewServer_RegistersMemoryAndOperatorTools' -v`. Expected: PASS. `TestBuildTool_AllToolsMarshal` marshals all 34 memory + 12 operator tools; `TestNewServer_RegistersMemoryAndOperatorTools` confirms `ToolCount() == 34 + 12 == 46`.

- [ ] **Step 7: Full package run.** `go test ./internal/mcp/...`. Expected: `ok` with all tests passing. Then `gofmt -l internal/mcp/*_test.go` (no output) and `golangci-lint run ./internal/mcp/...` (clean).

- [ ] **Step 8: Commit.** `git add internal/mcp/tools_test.go internal/mcp/server_test.go && git commit -m "test: cover SCM operator tool args, env fallback, schema marshal (9->12 count)"`

---

### Task 3: Version bump + MEMORY/ROADMAP notes

**Files:**
- Modify: `tatara-cli/internal/version/version.go`
- Modify: `tatara-cli/MEMORY.md`
- Modify: `tatara-cli/ROADMAP.md`

Decision: bump the in-repo default `Version` from `0.4.3` (current shipped, per MEMORY) to `0.5.0` (new minor: 3 new MCP tools, additive). Release tagging stays a separate `git tag v0.5.0` step driven by CI/ldflags; this change only updates the source default.

- [ ] **Step 1: Bump version default.** In `tatara-cli/internal/version/version.go` change:

```go
	Version = "dev"
```

to:

```go
	Version = "0.5.0"
```

- [ ] **Step 2: Build to confirm it compiles.** `go build ./...`. Expected: no output (success).

- [ ] **Step 3: Append MEMORY entry.** Add to `tatara-cli/MEMORY.md` after the last entry:

```
2026-06-09 - **SCM intent MCP tools (cli T2).** Added 3 operator-targeted tools (9->12): `propose_issue` -> `POST /projects/{TATARA_PROJECT}/issues` body `{repositoryRef,title,body,kind}` (kind bug|improvement); `review_verdict` -> `POST /tasks/{task|TATARA_TASK}/review` body `{decision,body,suggestions}` (decision approve|request_changes|comment); `pr_outcome` -> `POST /tasks/{task|TATARA_TASK}/pr-outcome` body `{action,reason}` (action merge|close). All are CRD-writes via the operator REST API; the operator TaskReconciler is the SOLE SCM egress path (agents emit intent only). project/task resolved via argOrEnv(TATARA_PROJECT/TATARA_TASK) exactly like existing operator tools; propose_issue accepts repo|repositoryRef arg, body key is always repositoryRef. Wire shapes match contract lock 2026-06-09 sections 3+4 byte-for-byte. Ships as tatara-cli v0.5.0.
```

- [ ] **Step 4: Mark ROADMAP shipped.** In `tatara-cli/ROADMAP.md`, add (or flip the matching planned item to) a shipped entry:

```
## v0.5.0 - SCM intent MCP tools

**Status:** shipped

3 operator intent tools (propose_issue, review_verdict, pr_outcome) per
the SCM-projects contract lock. See
`~/Documents/tatara/docs/superpowers/plans/2026-06-09-scm-projects-cli.md`
and spec `2026-06-09-scm-projects-pr-reactions-design.md`.
```

- [ ] **Step 5: Final verification (verification-before-completion).** Run the full package suite once more after the doc/version edits: `go build ./... && go test ./internal/mcp/...`. Expected: build success, `ok` for the mcp package. Confirm `go test ./...` for the whole module is green: `go test ./...`.

- [ ] **Step 6: requesting-code-review.** Run `superpowers:requesting-code-review` on the worktree diff. Apply fixes for critical/high findings, re-run `go test ./internal/mcp/...` and `golangci-lint run ./...`, then `pre-commit run --all-files`.

- [ ] **Step 7: Commit.** `git add internal/version/version.go MEMORY.md ROADMAP.md && git commit -m "chore: bump cli to v0.5.0, MEMORY/ROADMAP for SCM intent tools"`

---

## Integration

- [ ] Merge the worktree branch back to `tatara-cli` `main` (`superpowers:finishing-a-development-branch`), clean up the worktree. Build/tag (`git tag v0.5.0`) and image build happen from `main` only, never from the worktree. The wrapper image bump (sequence step 3 in the design) is a separate tatara-claude-code-wrapper change that consumes this cli release; out of scope for this plan.
