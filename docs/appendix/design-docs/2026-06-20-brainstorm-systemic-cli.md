# Plan: brainstorm-systemic - tatara-cli (WS3, cli half)

Date: 2026-06-20
Spec: `docs/superpowers/specs/2026-06-20-brainstorm-systemic-and-agent-ultracode-design.md` (WS3)
Repo: `tatara-cli` (Go), branch `main`

## For agentic workers

Execute this with `superpowers:subagent-driven-development`. The work is a
single bite-sized TDD task on one file pair, so a single sonnet implementation
subagent is sufficient; an opus review pass (`superpowers:requesting-code-review`)
before the commit. Every step is strict red/green TDD
(`superpowers:test-driven-development`): write the failing test, run it, see it
fail for the right reason, then make it pass. Run
`superpowers:verification-before-completion` before claiming done.

## Goal

Give the `propose_issue` MCP operator tool an OPTIONAL `systemicId` string. When
the agent supplies it, the cli forwards it to the operator REST call under the
JSON key `systemicId`. When the agent omits it, the payload is byte-for-byte
unchanged from today, so every existing call and the operator's
`DisallowUnknownFields` decoder keep working. This is the cli half of WS3
(multiple cross-linked issues for one systemic improvement); the operator threads
`systemicId` onto `Task.Spec.ProposedIssue.SystemicID` and stamps a
`tatara/systemic-<id>` correlation label - that work lives in the operator plan.

## Architecture

`internal/mcp/tools.go` declares the operator tool registry in `OperatorTools()`.
Each tool is built by the local `op(name, desc, schema, build)` helper into a
`Tool{Name, Description, Schema (json.RawMessage), Target: TargetOperator, Build}`.
`propose_issue` is registered around line 396. Its `Build` closure validates the
required args, then assembles a `map[string]any` payload (`repositoryRef`,
`title`, `body`, `kind`) and returns `POST /projects/<project>/issues`. `Invoke`
(same package) JSON-marshals that map as the request body.

The change is local to that one closure plus its schema string:
1. Add `systemicId` to the `properties` of the schema JSON string. Do NOT add it
   to `"required"`.
2. In the `Build` closure, append `systemicId` to the payload map only when the
   agent provided it, using the package's existing conditional-optional idiom
   `if v, ok := a["systemicId"]; ok { body["systemicId"] = v }` (identical to how
   `change_summary` handles `remaining_scope`/`most_problematic`, `issue_outcome`
   handles `comment`/`plan`, and `pr_outcome` handles `reason`).

No new files, no new helpers, no signature changes. `Tool.Build` and `Invoke`
signatures are untouched.

## Tech Stack

- Go 1.25.5 (pinned in `.mise.toml`).
- Tests: `testing` + `github.com/stretchr/testify/{assert,require}` (already used
  throughout `internal/mcp/tools_test.go`).
- Tooling: mise. Build/test/lint run through `mise exec` / `mise run` (the repo
  pins Go and golangci-lint; a bare `go`/`golangci-lint` may be the wrong
  version).

## Global Constraints (spec hard rules)

- **Locked cross-repo contract (exact names).** The schema property is
  `systemicId` (type `string`), OPTIONAL (never in `"required"`). The forwarded
  REST JSON key is `systemicId`. These names are a contract shared with the
  operator REST handler; do not rename, re-case, or snake_case them.
- **KISS.** Three similar lines beat an abstraction. Follow the existing
  conditional-optional pattern verbatim; do not invent a generic optional-field
  merger.
- **Backward compatible.** When `systemicId` is absent the payload must be
  identical to today (no `systemicId` key at all), so existing agents and the
  operator's `DisallowUnknownFields` JSON decoder are unaffected.
- **No tech-debt, no deferral.** Ship the full change (schema + payload + tests)
  in one commit. If anything is non-obvious, note it in `MEMORY.md`.
- **Newest stable Go**, JSON-only logging conventions, KISS - per the repo
  CLAUDE.md hard rules. (No logging touched here; the tool is pure request
  construction.)
- **Conventional commits.** `feat: ...`, `pre-commit run --all-files` green
  before committing.

## Current code (quoted, to be edited)

### `propose_issue` registration, `internal/mcp/tools.go` ~396-423

The current input JSON schema string (line 397):

```
`{"type":"object","properties":{"project":{"type":"string"},"repo":{"type":"string"},"title":{"type":"string"},"body":{"type":"string"},"kind":{"type":"string","enum":["bug","improvement"]}},"required":["title","body","kind","repo"]}`
```

The current `Build` closure body construction and endpoint (lines 398-422):

```go
func(a map[string]any) (string, string, any, error) {
	p := argOrEnv(a, "project", "TATARA_PROJECT")
	if p == "" {
		return "", "", nil, fmt.Errorf("project required")
	}
	repo := argString(a, "repo")
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
```

### Confirmed conditional-optional pattern in this file

`change_summary` (~482-487), the exact idiom to follow:

```go
if v, ok := a["remaining_scope"]; ok {
	body["remainingScope"] = v
}
if v, ok := a["most_problematic"]; ok {
	body["mostProblematic"] = v
}
```

`issue_outcome` (~514-519):

```go
if v, ok := a["comment"]; ok {
	body["comment"] = v
}
if v, ok := a["plan"]; ok {
	body["plan"] = v
}
```

`pr_outcome` (~454-456):

```go
if v, ok := a["reason"]; ok {
	body["reason"] = v
}
```

And `project` defaulting to `TATARA_PROJECT` uses `argOrEnv(a, "project", "TATARA_PROJECT")`
(line 399). For `systemicId` there is no env fallback (no such env exists), so the
plain `if v, ok := a["systemicId"]; ok` form is correct - same as `comment`/`plan`.

NOTE: `systemicId` here is the same key in BOTH the schema property and the
payload map (unlike `change_summary`, which translates snake_case tool args to
camelCase REST keys). So pass `a["systemicId"]` straight through, do NOT translate.

### Test file to extend

`internal/mcp/tools_test.go`. The propose_issue body assertions live in
`TestOperatorTools_SCMBodies` -> subtest `"propose_issue"` (~942-954). It already
asserts `repositoryRef`/`title`/`body`/`kind` and that no `repo` key leaks. New
subtests follow that exact style (build the tool via `operatorToolByName(t, ...)`,
cast `body.(map[string]any)`, `require.Equal` / `require.False(t, ...ok)`).
Schema-presence assertions follow `TestIssueOutcome_SchemaContainsDiscuss`
(~1038-1041) which does `require.Contains(t, string(tl.Schema), ...)`.

---

## Task 1: propose_issue gains optional `systemicId`, forwarded in payload

**Files**
- `internal/mcp/tools.go` (edit `propose_issue` schema string + `Build` closure)
- `internal/mcp/tools_test.go` (extend; new subtests + a schema-shape test)

**Interfaces** (no signatures change; for reference)
- `func OperatorTools() []Tool` - registry, unchanged signature.
- `Tool.Build func(args map[string]any) (method, path string, body any, err error)`
  - `propose_issue` closure now conditionally adds `body["systemicId"]`.
- Schema (`Tool.Schema json.RawMessage`) gains property `systemicId` of
  `{"type":"string"}`, not in `required`.

### Step 1 (RED): test that the schema advertises `systemicId` and keeps it optional

Add to `internal/mcp/tools_test.go`:

```go
func TestProposeIssue_SchemaHasOptionalSystemicID(t *testing.T) {
	tl := operatorToolByName(t, "propose_issue")
	schema := string(tl.Schema)
	require.Contains(t, schema, `"systemicId"`,
		"propose_issue schema must advertise systemicId property")
	require.Contains(t, schema, `"systemicId":{"type":"string"}`,
		"systemicId must be a string property")

	// systemicId must NOT be required: decode the schema and inspect the
	// required list so a future reorder of properties cannot make this pass
	// by accident.
	var parsed struct {
		Required []string `json:"required"`
	}
	require.NoError(t, json.Unmarshal(tl.Schema, &parsed))
	require.NotContains(t, parsed.Required, "systemicId",
		"systemicId must stay optional (not in required)")
	// The existing required set is unchanged.
	require.ElementsMatch(t, []string{"title", "body", "kind", "repo"}, parsed.Required)
}
```

Run it:

```
mise exec -- go test ./internal/mcp/ -run TestProposeIssue_SchemaHasOptionalSystemicID -v
```

Expected (RED): FAIL - the schema string has no `systemicId`, so the first
`require.Contains` fails:
```
--- FAIL: TestProposeIssue_SchemaHasOptionalSystemicID
    tools_test.go:NN:
        Error: ... does not contain "systemicId"
        Test: TestProposeIssue_SchemaHasOptionalSystemicID
        Messages: propose_issue schema must advertise systemicId property
FAIL
```

### Step 2 (GREEN): add `systemicId` to the schema string

Edit the `propose_issue` schema (line 397). Add `systemicId` to `properties`,
after `kind`, with a short description; leave `required` exactly as-is:

```go
`{"type":"object","properties":{"project":{"type":"string"},"repo":{"type":"string"},"title":{"type":"string"},"body":{"type":"string"},"kind":{"type":"string","enum":["bug","improvement"]},"systemicId":{"type":"string","description":"Optional shared id grouping several issues that are one systemic, cross-repo improvement. Call propose_issue once per affected repo with the same systemicId; the operator stamps a tatara/systemic-<id> label and the group counts as one against the proposal cap."}},"required":["title","body","kind","repo"]}`
```

Re-run:

```
mise exec -- go test ./internal/mcp/ -run TestProposeIssue_SchemaHasOptionalSystemicID -v
```

Expected (GREEN):
```
--- PASS: TestProposeIssue_SchemaHasOptionalSystemicID
PASS
ok  	github.com/szymonrychu/tatara-cli/internal/mcp
```

Also confirm `TestAllOperatorTools_Count` (expects 18) and
`TestChatTools_SchemasAreValidJSON`/`TestAllTools_SchemasAreValidJSON` (valid-JSON
guard) still pass - the schema string must remain valid JSON:

```
mise exec -- go test ./internal/mcp/ -run 'Count|SchemasAreValidJSON' -v
```

Expected: all PASS. (No tool was added, so the count of 18 is unchanged; the
schema is still one valid JSON object.)

### Step 3 (RED): test that a supplied `systemicId` is forwarded in the payload

Add to `internal/mcp/tools_test.go`:

```go
func TestProposeIssue_SystemicIDForwarded(t *testing.T) {
	t.Run("supplied systemicId is forwarded under the systemicId key", func(t *testing.T) {
		_, _, body, err := operatorToolByName(t, "propose_issue").Build(map[string]any{
			"project":    "alpha",
			"repo":       "szymonrychu/tatara",
			"title":      "feat: add platform-wide /metrics endpoint",
			"body":       "b",
			"kind":       "improvement",
			"systemicId": "sys-7f3a",
		})
		require.NoError(t, err)
		m := body.(map[string]any)
		require.Equal(t, "sys-7f3a", m["systemicId"])
		// existing keys are untouched
		require.Equal(t, "szymonrychu/tatara", m["repositoryRef"])
		require.Equal(t, "improvement", m["kind"])
	})

	t.Run("omitted systemicId leaves the payload unchanged", func(t *testing.T) {
		_, _, body, err := operatorToolByName(t, "propose_issue").Build(map[string]any{
			"project": "alpha",
			"repo":    "szymonrychu/tatara",
			"title":   "t",
			"body":    "b",
			"kind":    "bug",
		})
		require.NoError(t, err)
		m := body.(map[string]any)
		_, has := m["systemicId"]
		require.False(t, has, "systemicId must be absent when the agent did not supply it")
	})
}
```

Run:

```
mise exec -- go test ./internal/mcp/ -run TestProposeIssue_SystemicIDForwarded -v
```

Expected (RED): the `supplied` subtest FAILs because the payload map has no
`systemicId` yet (`m["systemicId"]` is nil):
```
--- FAIL: TestProposeIssue_SystemicIDForwarded/supplied_systemicId_is_forwarded_under_the_systemicId_key
    tools_test.go:NN:
        Error: Not equal:
        expected: string("sys-7f3a")
        actual  : <nil>
--- PASS: TestProposeIssue_SystemicIDForwarded/omitted_systemicId_leaves_the_payload_unchanged
FAIL
```
(The `omitted` subtest already passes - that is the backward-compat guard, and it
must keep passing through Step 4.)

### Step 4 (GREEN): conditionally add `systemicId` to the payload map

Edit the `propose_issue` `Build` closure. After the `body := map[string]any{...}`
literal (line 416-421) and before the `return http.MethodPost, ...` (line 422),
insert the conditional-optional append using the file's existing idiom:

```go
	body := map[string]any{
		"repositoryRef": repo,
		"title":         a["title"],
		"body":          a["body"],
		"kind":          a["kind"],
	}
	if v, ok := a["systemicId"]; ok {
		body["systemicId"] = v
	}
	return http.MethodPost, "/projects/" + url.PathEscape(p) + "/issues", body, nil
```

Re-run:

```
mise exec -- go test ./internal/mcp/ -run TestProposeIssue_SystemicIDForwarded -v
```

Expected (GREEN): both subtests PASS:
```
--- PASS: TestProposeIssue_SystemicIDForwarded/supplied_systemicId_is_forwarded_under_the_systemicId_key
--- PASS: TestProposeIssue_SystemicIDForwarded/omitted_systemicId_leaves_the_payload_unchanged
PASS
ok  	github.com/szymonrychu/tatara-cli/internal/mcp
```

### Step 5 (REGRESSION): the existing propose_issue body test still passes

The pre-existing `TestOperatorTools_SCMBodies/propose_issue` subtest builds
without `systemicId` and asserts `repositoryRef`/`title`/`body`/`kind` plus the
"no `repo` key leaks" guard. The new conditional must not perturb it. Run the
whole SCM body + schema + count surface:

```
mise exec -- go test ./internal/mcp/ -run 'SCMBodies|SCMBuildPaths|SCMRequireArgs|SCMEnvFallback|Count|SchemasAreValidJSON|ProposeIssue' -v
```

Expected: all PASS, including `TestOperatorTools_SCMBodies/propose_issue` and
`TestAllOperatorTools_Count` (18).

### Step 6 (VERIFY): full package + module green, then lint

```
mise exec -- go test ./internal/mcp/
mise exec -- go test ./...
mise run lint
```

Expected:
```
ok  	github.com/szymonrychu/tatara-cli/internal/mcp	<time>
...
ok  	github.com/szymonrychu/tatara-cli/...           (all packages)
```
and `mise run lint` (`make lint` -> golangci-lint) reports zero issues. Per
`superpowers:verification-before-completion`, paste this real output before
claiming done - do not assert green without running it.

### Step 7: review + commit

Run `superpowers:requesting-code-review` (opus) on the diff. Confirm: schema is
valid JSON, `systemicId` not in `required`, payload key matches the locked
contract (`systemicId`, not snake_case, not translated), omitted-case payload
byte-identical to before. Then:

```
pre-commit run --all-files
git add internal/mcp/tools.go internal/mcp/tools_test.go
git commit
```

Commit message (conventional):

```
feat: propose_issue forwards optional systemicId to operator

Adds an optional `systemicId` string to the propose_issue MCP tool schema
and forwards it under the `systemicId` REST key when the agent supplies it.
Lets one systemic, cross-repo improvement open one cross-linked issue per
affected repo (operator stamps the tatara/systemic-<id> correlation label).
Payload is unchanged when systemicId is omitted; not added to required.

Part of WS3, spec 2026-06-20-brainstorm-systemic-and-agent-ultracode-design.
```

(Append the session footer line per the repo git convention.)

---

## Integration / Deploy notes (read before shipping)

This cli change is one half of the locked cross-repo `systemicId` contract; the
operator half (REST handler threading `systemicId` onto
`Task.Spec.ProposedIssue.SystemicID`, the `tatara/systemic-<id>` label, the cap
grouping) ships from the operator plan. The cli is backward compatible standalone:
sending `systemicId` to an operator that does not yet understand it would be
rejected by the operator's `DisallowUnknownFields` decoder, so DO NOT instruct
agents to use `systemicId` until the operator side is deployed. The schema/payload
plumbing landing first on cli `main` is safe because the agent prompt does not
mention `systemicId` until the operator goal change ships.

**Wrapper pin bump (required to actually ship to agents).** The
tatara-claude-code-wrapper image bakes a pinned `TATARA_CLI_VERSION`. The new
`systemicId` tool surface only reaches running agents after:
1. this cli change merges to `main` and CI builds + pushes a new cli version, then
2. the wrapper bumps its `TATARA_CLI_VERSION` pin to that version and its image is
   rebuilt, then
3. tatara-helmfile bumps the wrapper `agent.image` tag (GitOps deploy path; never
   `kubectl set image`).

**Tokenless `tatara mcp` build-guard.** The wrapper image build runs `tatara mcp`
(serving `tools/list`) WITHOUT a token as a build-time guard; the build fails if
that command cannot list tools tokenless. This change only adds a property to an
existing tool's static schema and a conditional payload line - no new auth path,
no token requirement on `tools/list` - so the guard stays satisfied. Just confirm
`tatara mcp` still lists tools tokenless after the bump (it is exercised on
main-push, skipped on PR), per
`[[wrapper-cli-pin-needs-tokenless-mcp]]`.

Do NOT make any wrapper or helmfile change in this repo; those are separate plans.
This plan ends at a green cli `main`.
