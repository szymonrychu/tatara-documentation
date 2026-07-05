# Autonomous Cron: tatara-cli `issue_outcome` MCP Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `issue_outcome` MCP tool to tatara-cli's `OperatorTools()` (operator tool count 12 -> 13), wiring it to `POST /tasks/{TASK_ID}/issue-outcome`, and bump the cli version 0.5.0 -> 0.6.0.

**Architecture:** The tool mirrors the existing `pr_outcome` operator tool exactly. It declares an enum-constrained `action` (`implement` | `close`) and a free-form `comment`, resolves `TASK_ID` via `argOrEnv(a, "task", "TATARA_TASK")`, validates that `action` is present, and returns `(POST, "/tasks/{TASK_ID}/issue-outcome", body, nil)`. Validation that mirrors the operator's stricter rules (comment required on close) is enforced server-side by the operator; the cli tool itself asserts only the same surface as `pr_outcome` (action required), so the Build function stays a thin REST shim. Tests are table-driven `t.Run` subtests appended to the existing `internal/mcp/tools_test.go`, following the exact patterns used for `pr_outcome`.

**Tech Stack:** Go (stdlib `net/http`, `encoding/json`), `github.com/stretchr/testify` (`require`/`assert`), `go test -race`.

---

## Context for the implementer (read before starting)

This repo is `tatara-cli` at `/Users/szymonri/Documents/tatara/tatara-cli`. The single file you touch for the tool is `internal/mcp/tools.go`; the test file is `internal/mcp/tools_test.go`; the version constant is `internal/version/version.go`.

The `Tool` struct (tools.go:26-32):

```go
type Tool struct {
	Name        string
	Description string
	Schema      json.RawMessage
	Target      Target
	Build       func(args map[string]any) (method, path string, body any, err error)
}
```

`OperatorTools()` (tools.go:284-456) builds operator tools through the local `op` helper (tools.go:285-287):

```go
op := func(name, desc, schema string, build func(map[string]any) (string, string, any, error)) Tool {
	return Tool{Name: name, Description: desc, Schema: json.RawMessage(schema), Target: TargetOperator, Build: build}
}
```

The model to mirror is `pr_outcome` (tools.go:439-454). It resolves the task via `argOrEnv(a, "task", "TATARA_TASK")` (tools.go:276-281), requires `action`, copies an optional scalar into the body, and posts to `"/tasks/" + url.PathEscape(tk) + "/pr-outcome"`. `issue_outcome` is structurally identical: required `action`, optional `comment`, path suffix `/issue-outcome`.

Contract-lock constraints that MUST match byte-for-byte (from `2026-06-11-autonomous-cron-contract-lock.md` section 8):
- Tool name: `issue_outcome`
- Description: `Record the outcome of an issue-triage task: implement (open a PR) or close (with a comment).`
- Schema: `action` enum `[implement, close]`, `comment` string, `required: [action]`
- Method/path: `POST` `/tasks/{TASK_ID}/issue-outcome`, `TASK_ID` from `argOrEnv` env `TATARA_TASK`
- Operator tool count becomes 13.

Test/build commands:
- Single subtest: `go test ./internal/mcp/ -run 'TestName' -v`
- Full package race: `go test ./internal/mcp/ -race -count=1`
- Lint: `golangci-lint run ./... || [ $? -eq 5 ]`
- Format check: `gofmt -l internal/mcp/tools.go` (empty output = formatted)

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `internal/mcp/tools.go` | Append the `issue_outcome` `op(...)` entry to `OperatorTools()`; update the doc comment count 12 -> 13. | Modify |
| `internal/mcp/tools_test.go` | Update count assertion 12 -> 13; add build-path, require-args, env-fallback, and body subtests for `issue_outcome`. | Modify |
| `internal/version/version.go` | Bump `Version` 0.5.0 -> 0.6.0. | Modify |

---

## Task 1: Update the operator tool count assertion to 13 (Red)

This is the count guard that locks the new tool in. We flip it first so it fails until Task 2 adds the tool, giving a clean Red-Green for the registration itself.

**Files:**
- Test: `internal/mcp/tools_test.go:111-113`

- [ ] **Step 1: Update the failing count assertion**

In `internal/mcp/tools_test.go`, change the existing `TestAllOperatorTools_Count` (lines 111-113):

```go
func TestAllOperatorTools_Count(t *testing.T) {
	require.Len(t, OperatorTools(), 13)
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/mcp/ -run TestAllOperatorTools_Count -v`
Expected: FAIL with `Error: "[...]" should have 13 item(s), but has 12`

- [ ] **Step 3: No implementation yet**

Leave this failing on purpose; Task 2 makes it pass by adding the tool. Do not commit here - Task 2 commits the test + impl together.

---

## Task 2: Add the `issue_outcome` tool to `OperatorTools()` (Green)

**Files:**
- Modify: `internal/mcp/tools.go:283` (doc comment count), `internal/mcp/tools.go:454` (append new `op(...)` entry after `pr_outcome`)
- Test: `internal/mcp/tools_test.go` (count assertion from Task 1)

- [ ] **Step 1: Add the `issue_outcome` op entry after `pr_outcome`**

In `internal/mcp/tools.go`, the `pr_outcome` entry currently ends at line 454:

```go
				return http.MethodPost, "/tasks/" + url.PathEscape(tk) + "/pr-outcome", body, nil
			}),
	}
}
```

Insert the new tool between the closing `}),` of `pr_outcome` and the `}` that closes the returned slice. Replace those three lines with:

```go
				return http.MethodPost, "/tasks/" + url.PathEscape(tk) + "/pr-outcome", body, nil
			}),
		op("issue_outcome", "Record the outcome of an issue-triage task: implement (open a PR) or close (with a comment).",
			`{"type":"object","properties":{"action":{"type":"string","enum":["implement","close"]},"comment":{"type":"string"}},"required":["action"]}`,
			func(a map[string]any) (string, string, any, error) {
				tk := argOrEnv(a, "task", "TATARA_TASK")
				if tk == "" {
					return "", "", nil, fmt.Errorf("task required")
				}
				if argString(a, "action") == "" {
					return "", "", nil, fmt.Errorf("action required")
				}
				body := map[string]any{"action": a["action"]}
				if v, ok := a["comment"]; ok {
					body["comment"] = v
				}
				return http.MethodPost, "/tasks/" + url.PathEscape(tk) + "/issue-outcome", body, nil
			}),
	}
}
```

- [ ] **Step 2: Update the `OperatorTools` doc-comment count**

In `internal/mcp/tools.go`, the doc comment on line 283 reads:

```go
// OperatorTools returns the 12 tatara-operator REST tools (Target=TargetOperator).
```

Change it to:

```go
// OperatorTools returns the 13 tatara-operator REST tools (Target=TargetOperator).
```

- [ ] **Step 3: Run the count test to verify it passes**

Run: `go test ./internal/mcp/ -run TestAllOperatorTools_Count -v`
Expected: PASS

- [ ] **Step 4: Verify formatting and lint**

Run: `gofmt -l internal/mcp/tools.go`
Expected: empty output (no files listed = already formatted)

Run: `golangci-lint run ./internal/mcp/... || [ $? -eq 5 ]`
Expected: exit 0 (no findings, or exit-5 "no go files matched" tolerated by the `||` clause)

- [ ] **Step 5: Commit**

```bash
git add internal/mcp/tools.go internal/mcp/tools_test.go
git commit -m "feat: add issue_outcome operator MCP tool"
```

---

## Task 3: Add `issue_outcome` to the SCM build-path table test (Red-Green)

This asserts the tool builds the exact contract-lock method and path. Extend the existing `TestOperatorTools_SCMBuildPaths` (tools_test.go:706-726) rather than adding a new test, matching how `pr_outcome` is covered there.

**Files:**
- Test: `internal/mcp/tools_test.go:713-717` (the `cases` table inside `TestOperatorTools_SCMBuildPaths`)

- [ ] **Step 1: Add the failing build-path case**

In `internal/mcp/tools_test.go`, the `cases` table of `TestOperatorTools_SCMBuildPaths` currently ends:

```go
		{"review_verdict", map[string]any{"task": "t1", "decision": "approve"}, http.MethodPost, "/tasks/t1/review"},
		{"pr_outcome", map[string]any{"task": "t1", "action": "merge"}, http.MethodPost, "/tasks/t1/pr-outcome"},
	}
```

Add the `issue_outcome` row after `pr_outcome`:

```go
		{"review_verdict", map[string]any{"task": "t1", "decision": "approve"}, http.MethodPost, "/tasks/t1/review"},
		{"pr_outcome", map[string]any{"task": "t1", "action": "merge"}, http.MethodPost, "/tasks/t1/pr-outcome"},
		{"issue_outcome", map[string]any{"task": "t1", "action": "implement"}, http.MethodPost, "/tasks/t1/issue-outcome"},
	}
```

- [ ] **Step 2: Run the build-path test to verify it passes**

Because Task 2 already added the tool, this case passes immediately. Run it to confirm:

Run: `go test ./internal/mcp/ -run TestOperatorTools_SCMBuildPaths -v`
Expected: PASS, including the `TestOperatorTools_SCMBuildPaths/issue_outcome` subtest

(If `issue_outcome` were not yet registered, `operatorToolByName` would `t.Fatalf("operator tool %q not found", name)` - the expected failure if Task 2 was skipped.)

- [ ] **Step 3: Commit**

```bash
git add internal/mcp/tools_test.go
git commit -m "test: cover issue_outcome build path"
```

---

## Task 4: Add `issue_outcome` require-args and env-fallback tests (Red-Green)

Mirror the `pr_outcome` coverage in `TestOperatorTools_SCMRequireArgs` (tools_test.go:728-747) and `TestOperatorTools_SCMEnvFallback` (tools_test.go:749-768).

**Files:**
- Test: `internal/mcp/tools_test.go:743-746` (require-args block), `internal/mcp/tools_test.go:757-759` (env-fallback table)

- [ ] **Step 1: Add the failing require-args assertions**

In `internal/mcp/tools_test.go`, `TestOperatorTools_SCMRequireArgs` currently ends:

```go
	_, _, _, err = operatorToolByName(t, "pr_outcome").Build(map[string]any{"action": "merge"})
	require.Error(t, err) // task required (no env set)
	_, _, _, err = operatorToolByName(t, "pr_outcome").Build(map[string]any{"task": "t1"})
	require.Error(t, err) // action required
}
```

Add the two `issue_outcome` checks before the closing brace:

```go
	_, _, _, err = operatorToolByName(t, "pr_outcome").Build(map[string]any{"action": "merge"})
	require.Error(t, err) // task required (no env set)
	_, _, _, err = operatorToolByName(t, "pr_outcome").Build(map[string]any{"task": "t1"})
	require.Error(t, err) // action required
	_, _, _, err = operatorToolByName(t, "issue_outcome").Build(map[string]any{"action": "implement"})
	require.Error(t, err) // task required (no env set)
	_, _, _, err = operatorToolByName(t, "issue_outcome").Build(map[string]any{"task": "t1"})
	require.Error(t, err) // action required
}
```

- [ ] **Step 2: Add the failing env-fallback case**

`TestOperatorTools_SCMEnvFallback` currently has this `cases` table:

```go
		{"propose_issue", map[string]any{"repo": "r", "title": "t", "body": "b", "kind": "bug"}, "/projects/proj-from-env/issues"},
		{"review_verdict", map[string]any{"decision": "comment"}, "/tasks/task-from-env/review"},
		{"pr_outcome", map[string]any{"action": "merge"}, "/tasks/task-from-env/pr-outcome"},
	}
```

Add the `issue_outcome` row:

```go
		{"propose_issue", map[string]any{"repo": "r", "title": "t", "body": "b", "kind": "bug"}, "/projects/proj-from-env/issues"},
		{"review_verdict", map[string]any{"decision": "comment"}, "/tasks/task-from-env/review"},
		{"pr_outcome", map[string]any{"action": "merge"}, "/tasks/task-from-env/pr-outcome"},
		{"issue_outcome", map[string]any{"action": "close"}, "/tasks/task-from-env/issue-outcome"},
	}
```

- [ ] **Step 3: Run both tests to verify they pass**

Run: `go test ./internal/mcp/ -run 'TestOperatorTools_SCMRequireArgs|TestOperatorTools_SCMEnvFallback' -v`
Expected: PASS, including `TestOperatorTools_SCMEnvFallback/issue_outcome`

- [ ] **Step 4: Commit**

```bash
git add internal/mcp/tools_test.go
git commit -m "test: cover issue_outcome required-args and env fallback"
```

---

## Task 5: Add the `issue_outcome` body marshal subtest (Red-Green)

Mirror the `pr_outcome` body subtests inside `TestOperatorTools_SCMBodies` (tools_test.go:807-825): one with `comment` present, one action-only confirming `comment` is omitted from the body when not supplied. This is the marshal half of the spec's "cli `issue_outcome` marshal + enum-validation test" (section 14) - the enum-validation half (rejecting a bad `action`, and rejecting a missing `comment` on `close`) is enforced operator-side per the contract lock; the cli asserts the correct method/path/body surface.

**Files:**
- Test: `internal/mcp/tools_test.go:825` (append two `t.Run` subtests before the closing brace of `TestOperatorTools_SCMBodies`)

- [ ] **Step 1: Add the failing body subtests**

In `internal/mcp/tools_test.go`, `TestOperatorTools_SCMBodies` currently ends:

```go
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

Add the two `issue_outcome` subtests before the function's closing brace:

```go
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
	t.Run("issue_outcome_close", func(t *testing.T) {
		_, _, body, err := operatorToolByName(t, "issue_outcome").Build(map[string]any{
			"task": "t1", "action": "close", "comment": "out of scope",
		})
		require.NoError(t, err)
		m := body.(map[string]any)
		require.Equal(t, "close", m["action"])
		require.Equal(t, "out of scope", m["comment"])
	})
	t.Run("issue_outcome_action_only", func(t *testing.T) {
		_, _, body, err := operatorToolByName(t, "issue_outcome").Build(map[string]any{
			"task": "t1", "action": "implement",
		})
		require.NoError(t, err)
		m := body.(map[string]any)
		require.Equal(t, "implement", m["action"])
		_, hasComment := m["comment"]
		require.False(t, hasComment)
	})
}
```

- [ ] **Step 2: Run the body test to verify it passes**

Run: `go test ./internal/mcp/ -run TestOperatorTools_SCMBodies -v`
Expected: PASS, including `TestOperatorTools_SCMBodies/issue_outcome_close` and `.../issue_outcome_action_only`

- [ ] **Step 3: Verify the schema enum is valid JSON and the tool surface is consistent**

The `issue_outcome` schema is already exercised by the existing `TestAllTools_SchemasAreValidJSON`-style guard only for `AllTools()`. Operator tool schemas are not JSON-checked elsewhere, so confirm the whole operator package passes with race:

Run: `go test ./internal/mcp/ -race -count=1`
Expected: PASS (all tests, no data races)

- [ ] **Step 4: Commit**

```bash
git add internal/mcp/tools_test.go
git commit -m "test: cover issue_outcome request body marshal"
```

---

## Task 6: Bump the cli version 0.5.0 -> 0.6.0

The design (section 15) and lock (section 10) call for a cli version bump alongside the new tool. Minor bump since this is an additive, backward-compatible feature.

**Files:**
- Modify: `internal/version/version.go:5`

- [ ] **Step 1: Bump the version constant**

In `internal/version/version.go`, line 5 reads:

```go
	Version = "0.5.0"
```

Change it to:

```go
	Version = "0.6.0"
```

- [ ] **Step 2: Verify the package still builds**

Run: `go build ./...`
Expected: exit 0, no output

- [ ] **Step 3: Run the full test suite once more**

Run: `go test ./... -race -count=1`
Expected: PASS for every package (ok lines, no FAIL)

- [ ] **Step 4: Commit**

```bash
git add internal/version/version.go
git commit -m "chore: bump cli version to 0.6.0"
```

---

## Final verification (verification-before-completion)

Before claiming done, run the full battery and confirm each PASSes with your own eyes:

- [ ] `go test ./... -race -count=1` -> all packages `ok`
- [ ] `go vet ./...` -> exit 0
- [ ] `gofmt -l .` -> empty output
- [ ] `golangci-lint run ./... || [ $? -eq 5 ]` -> exit 0
- [ ] `git log --oneline -6` shows the six commits above

---

## Self-Review

**1. Spec coverage.** The cli scope is design section 11 ("New MCP tool: issue_outcome") and lock section 8 ("MCP tool: issue_outcome (tatara-cli)"), plus the design section 14 cli testing line and the section 15 step-2 version bump.

| Spec requirement | Implemented by |
|---|---|
| Tool name `issue_outcome` | Task 2 step 1 |
| Description `Record the outcome of an issue-triage task: implement (open a PR) or close (with a comment).` | Task 2 step 1 (byte-for-byte from lock section 8) |
| Schema: `action` enum `[implement, close]`, `comment` string, `required: [action]` | Task 2 step 1 |
| `POST /tasks/{TASK_ID}/issue-outcome` | Task 2 step 1; asserted Task 3 |
| `TASK_ID` via `argOrEnv` env `TATARA_TASK` | Task 2 step 1; asserted Task 4 (env-fallback) |
| Build signature `func(map[string]any) (string, string, any, error)` returning `(POST, path, body, err)` | Task 2 step 1 |
| Operator tool count 12 -> 13 | Task 1 + Task 2; doc comment updated Task 2 step 2 |
| cli marshal test | Task 5 (body subtests) |
| cli enum-validation note (comment-on-close enforced operator-side) | Documented in Task 5 preamble; cli asserts action-required (Task 4) and body surface (Task 5) |
| Version bump | Task 6 |

No gaps. Note on the section-14 phrase "missing comment on close rejected": per the contract lock section 7, that rejection is an operator-handler 400, not a cli Build error - the cli's `issue_outcome` requires only `action`, exactly like `pr_outcome` requires only `action`. This is the resolved ambiguity (see returned summary). The cli test therefore asserts the correct method/path/body it produces, not server-side validation it does not own.

**2. Placeholder scan.** No "TBD", "TODO", "add appropriate X", "similar to Task N", or bare prose-only code steps. Every code step shows complete Go. Searched: clean.

**3. Type consistency.** Tool name `issue_outcome`, arg keys `action`/`comment`/`task`, helper `argOrEnv(a, "task", "TATARA_TASK")`, helper `argString`, path suffix `/issue-outcome`, and body keys `action`/`comment` are spelled identically in Tasks 2-5. The `op(...)` helper and `operatorToolByName` test helper names match the existing file. Version string `"0.6.0"` is consistent in Task 6. No drift.
