# code_* Phase 2 MCP Tools Implementation Plan

This plan adds six new memory-target `code_*` MCP tools to `internal/mcp/tools.go` mapping to Phase 2 `/code-graph/*` routes, and adds an optional `by` param to the existing `code_important` tool. All new tools follow the existing `codeGet` Build helper and the locked `NewToolWithRawSchema` registration in `server.go` (no changes to `server.go` needed - it iterates `AllTools()`).

New tools (memory target, all GET via `codeGet`):
- `code_related` (id req; relations, min_confidence, repo opt) -> `/code-graph/related`
- `code_hyperedges` (entity, repo opt) -> `/code-graph/hyperedges`
- `code_hyperedge` (id req; repo opt) -> `/code-graph/hyperedge`
- `code_communities` (repo opt) -> `/code-graph/communities`
- `code_community` (repo, community req) -> `/code-graph/community`
- `code_bridges` (repo, limit opt) -> `/code-graph/bridges`
- `code_important` gains optional `by` (enum degree|betweenness) param

Tool-count totals change from 28 -> 34 in `AllTools()`. Three assertions update: `tools_test.go:146`, `tools_test.go:347`, `server_test.go:43`. `OperatorTools()` stays at 9.

Conventions observed from the repo (match exactly):
- Tools registered as struct literals in `AllTools()` with inline `Schema: json.RawMessage(...)` and `Build: codeGet(path, required, optional)`.
- `codeGet(path, required, optional)` sets required keys (errors if empty) and only sets optional keys when non-empty, then `GET path + "?" + q.Encode()`.
- Tests use `httptest.NewServer` + `freshClient` + `Invoke`, asserting `r.URL.Path` and `r.URL.Query().Get(k)`. Require-arg tests call `.Build(...)` directly and assert `require.Error`.
- Test style: table-driven, `require` for build/invoke, `assert` for counts, `t.Run` subtests, `c := c` capture in indexed loops.

All work happens on a feature branch off `main` (currently on `main`).

---

## Task 0: Create feature branch

**Files:** none (git only)

1. Create and switch to a feature branch:

```
git checkout -b feat/phase2-code-graph-tools
```

Expected: `Switched to a new branch 'feat/phase2-code-graph-tools'`.

---

## Task 1: Add `code_related` tool

**Files:**
- Modify: `/Users/szymonri/Documents/tatara/tatara-cli/internal/mcp/tools_test.go`
- Modify: `/Users/szymonri/Documents/tatara/tatara-cli/internal/mcp/tools.go`

### Steps

1. Write failing test. Append this function to the end of `internal/mcp/tools_test.go`:

```go
func TestCodeRelated_BuildQuery(t *testing.T) {
	cases := []struct {
		name string
		args map[string]any
		q    map[string]string
	}{
		{
			"all params",
			map[string]any{"id": "go:func:m.F", "relations": "conceptually_related_to,cites", "min_confidence": float64(0.5), "repo": "r"},
			map[string]string{"id": "go:func:m.F", "relations": "conceptually_related_to,cites", "min_confidence": "0.5", "repo": "r"},
		},
		{
			"id only",
			map[string]any{"id": "go:func:m.F"},
			map[string]string{"id": "go:func:m.F"},
		},
	}
	for _, c := range cases {
		c := c
		t.Run(c.name, func(t *testing.T) {
			var gotPath string
			var gotQuery url.Values
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				gotPath = r.URL.Path
				gotQuery = r.URL.Query()
				_, _ = w.Write([]byte(`[]`))
			}))
			defer srv.Close()
			cli := freshClient(t, srv.URL)
			_, err := Invoke(context.Background(), cli, toolByName(t, "code_related"), c.args)
			require.NoError(t, err)
			require.Equal(t, "/code-graph/related", gotPath)
			for k, v := range c.q {
				require.Equal(t, v, gotQuery.Get(k), "param %s", k)
			}
		})
	}
}

func TestCodeRelated_RequireID(t *testing.T) {
	_, _, _, err := toolByName(t, "code_related").Build(map[string]any{"repo": "r"})
	require.Error(t, err) // id required
}
```

2. Run RED:

```
go test ./internal/mcp/ -run 'TestCodeRelated' -count=1
```

Expected: FAIL - `tool "code_related" not found in registry` (from `toolByName`'s `t.Fatalf`).

3. Minimal impl. In `internal/mcp/tools.go`, inside `AllTools()`, insert this entry immediately after the `code_explain` entry (after line 214, before the closing `}` of the returned slice):

```go
			{Name: "code_related", Description: "Semantic neighbors of an entity over semantic edges (conceptually_related_to, semantically_similar_to, rationale_for, shares_data_with, cites), optionally filtered by relation and min confidence.",
				Schema: json.RawMessage(`{"type":"object","properties":{"id":{"type":"string"},"relations":{"type":"string"},"min_confidence":{"type":"number"},"repo":{"type":"string"}},"required":["id"]}`),
				Build:  codeGet("/code-graph/related", []string{"id"}, []string{"relations", "min_confidence", "repo"})},
```

4. Run GREEN:

```
go test ./internal/mcp/ -run 'TestCodeRelated' -count=1
```

Expected: PASS (`ok github.com/szymonrychu/tatara-cli/internal/mcp`).

5. Commit:

```
git add internal/mcp/tools.go internal/mcp/tools_test.go
git commit -m "feat(mcp): add code_related tool mapping /code-graph/related"
```

---

## Task 2: Add `code_hyperedges` and `code_hyperedge` tools

**Files:**
- Modify: `/Users/szymonri/Documents/tatara/tatara-cli/internal/mcp/tools_test.go`
- Modify: `/Users/szymonri/Documents/tatara/tatara-cli/internal/mcp/tools.go`

### Steps

1. Write failing test. Append to the end of `internal/mcp/tools_test.go`:

```go
func TestCodeHyperedges_BuildQuery(t *testing.T) {
	cases := []struct {
		name string
		args map[string]any
		q    map[string]string
	}{
		{"entity and repo", map[string]any{"entity": "go:func:m.F", "repo": "r"}, map[string]string{"entity": "go:func:m.F", "repo": "r"}},
		{"no args", map[string]any{}, map[string]string{}},
	}
	for _, c := range cases {
		c := c
		t.Run(c.name, func(t *testing.T) {
			var gotPath string
			var gotQuery url.Values
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				gotPath = r.URL.Path
				gotQuery = r.URL.Query()
				_, _ = w.Write([]byte(`[]`))
			}))
			defer srv.Close()
			cli := freshClient(t, srv.URL)
			_, err := Invoke(context.Background(), cli, toolByName(t, "code_hyperedges"), c.args)
			require.NoError(t, err)
			require.Equal(t, "/code-graph/hyperedges", gotPath)
			for k, v := range c.q {
				require.Equal(t, v, gotQuery.Get(k), "param %s", k)
			}
		})
	}
}

func TestCodeHyperedge_BuildQuery(t *testing.T) {
	var gotPath string
	var gotQuery url.Values
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		gotQuery = r.URL.Query()
		_, _ = w.Write([]byte(`{}`))
	}))
	defer srv.Close()
	cli := freshClient(t, srv.URL)
	_, err := Invoke(context.Background(), cli, toolByName(t, "code_hyperedge"), map[string]any{"id": "he:r:file:label", "repo": "r"})
	require.NoError(t, err)
	require.Equal(t, "/code-graph/hyperedge", gotPath)
	require.Equal(t, "he:r:file:label", gotQuery.Get("id"))
	require.Equal(t, "r", gotQuery.Get("repo"))
}

func TestCodeHyperedge_RequireID(t *testing.T) {
	_, _, _, err := toolByName(t, "code_hyperedge").Build(map[string]any{"repo": "r"})
	require.Error(t, err) // id required
}
```

2. Run RED:

```
go test ./internal/mcp/ -run 'TestCodeHyperedge' -count=1
```

Expected: FAIL - `tool "code_hyperedges" not found in registry`.

3. Minimal impl. In `internal/mcp/tools.go`, inside `AllTools()`, insert these two entries immediately after the `code_related` entry added in Task 1:

```go
			{Name: "code_hyperedges", Description: "List n-ary hyperedges (group relations) in the code graph, optionally scoped to a member entity and/or repo.",
				Schema: json.RawMessage(`{"type":"object","properties":{"entity":{"type":"string"},"repo":{"type":"string"}}}`),
				Build:  codeGet("/code-graph/hyperedges", nil, []string{"entity", "repo"})},
			{Name: "code_hyperedge", Description: "Get a single hyperedge by id with its members.",
				Schema: json.RawMessage(`{"type":"object","properties":{"id":{"type":"string"},"repo":{"type":"string"}},"required":["id"]}`),
				Build:  codeGet("/code-graph/hyperedge", []string{"id"}, []string{"repo"})},
```

4. Run GREEN:

```
go test ./internal/mcp/ -run 'TestCodeHyperedge' -count=1
```

Expected: PASS.

5. Commit:

```
git add internal/mcp/tools.go internal/mcp/tools_test.go
git commit -m "feat(mcp): add code_hyperedges and code_hyperedge tools"
```

---

## Task 3: Add `code_communities` and `code_community` tools

**Files:**
- Modify: `/Users/szymonri/Documents/tatara/tatara-cli/internal/mcp/tools_test.go`
- Modify: `/Users/szymonri/Documents/tatara/tatara-cli/internal/mcp/tools.go`

### Steps

1. Write failing test. Append to the end of `internal/mcp/tools_test.go`:

```go
func TestCodeCommunities_BuildQuery(t *testing.T) {
	cases := []struct {
		name string
		args map[string]any
		q    map[string]string
	}{
		{"repo", map[string]any{"repo": "r"}, map[string]string{"repo": "r"}},
		{"no args", map[string]any{}, map[string]string{}},
	}
	for _, c := range cases {
		c := c
		t.Run(c.name, func(t *testing.T) {
			var gotPath string
			var gotQuery url.Values
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				gotPath = r.URL.Path
				gotQuery = r.URL.Query()
				_, _ = w.Write([]byte(`[]`))
			}))
			defer srv.Close()
			cli := freshClient(t, srv.URL)
			_, err := Invoke(context.Background(), cli, toolByName(t, "code_communities"), c.args)
			require.NoError(t, err)
			require.Equal(t, "/code-graph/communities", gotPath)
			for k, v := range c.q {
				require.Equal(t, v, gotQuery.Get(k), "param %s", k)
			}
		})
	}
}

func TestCodeCommunity_BuildQuery(t *testing.T) {
	var gotPath string
	var gotQuery url.Values
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		gotQuery = r.URL.Query()
		_, _ = w.Write([]byte(`{}`))
	}))
	defer srv.Close()
	cli := freshClient(t, srv.URL)
	_, err := Invoke(context.Background(), cli, toolByName(t, "code_community"), map[string]any{"repo": "r", "community": float64(3)})
	require.NoError(t, err)
	require.Equal(t, "/code-graph/community", gotPath)
	require.Equal(t, "r", gotQuery.Get("repo"))
	require.Equal(t, "3", gotQuery.Get("community"))
}

func TestCodeCommunity_RequireArgs(t *testing.T) {
	_, _, _, err := toolByName(t, "code_community").Build(map[string]any{"community": float64(1)})
	require.Error(t, err) // repo required
	_, _, _, err = toolByName(t, "code_community").Build(map[string]any{"repo": "r"})
	require.Error(t, err) // community required
}
```

2. Run RED:

```
go test ./internal/mcp/ -run 'TestCodeComm' -count=1
```

Expected: FAIL - `tool "code_communities" not found in registry`.

3. Minimal impl. In `internal/mcp/tools.go`, inside `AllTools()`, insert these two entries immediately after the `code_hyperedge` entry from Task 2:

```go
			{Name: "code_communities", Description: "List detected communities in the code graph (community, label, size, cohesion), optionally filtered by repo.",
				Schema: json.RawMessage(`{"type":"object","properties":{"repo":{"type":"string"}}}`),
				Build:  codeGet("/code-graph/communities", nil, []string{"repo"})},
			{Name: "code_community", Description: "List the member entities of a specific community.",
				Schema: json.RawMessage(`{"type":"object","properties":{"repo":{"type":"string"},"community":{"type":"integer"}},"required":["repo","community"]}`),
				Build:  codeGet("/code-graph/community", []string{"repo", "community"}, nil)},
```

4. Run GREEN:

```
go test ./internal/mcp/ -run 'TestCodeComm' -count=1
```

Expected: PASS.

5. Commit:

```
git add internal/mcp/tools.go internal/mcp/tools_test.go
git commit -m "feat(mcp): add code_communities and code_community tools"
```

---

## Task 4: Add `code_bridges` tool

**Files:**
- Modify: `/Users/szymonri/Documents/tatara/tatara-cli/internal/mcp/tools_test.go`
- Modify: `/Users/szymonri/Documents/tatara/tatara-cli/internal/mcp/tools.go`

### Steps

1. Write failing test. Append to the end of `internal/mcp/tools_test.go`:

```go
func TestCodeBridges_BuildQuery(t *testing.T) {
	cases := []struct {
		name string
		args map[string]any
		q    map[string]string
	}{
		{"repo and limit", map[string]any{"repo": "r", "limit": float64(5)}, map[string]string{"repo": "r", "limit": "5"}},
		{"no args", map[string]any{}, map[string]string{}},
	}
	for _, c := range cases {
		c := c
		t.Run(c.name, func(t *testing.T) {
			var gotPath string
			var gotQuery url.Values
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				gotPath = r.URL.Path
				gotQuery = r.URL.Query()
				_, _ = w.Write([]byte(`[]`))
			}))
			defer srv.Close()
			cli := freshClient(t, srv.URL)
			_, err := Invoke(context.Background(), cli, toolByName(t, "code_bridges"), c.args)
			require.NoError(t, err)
			require.Equal(t, "/code-graph/bridges", gotPath)
			for k, v := range c.q {
				require.Equal(t, v, gotQuery.Get(k), "param %s", k)
			}
		})
	}
}
```

2. Run RED:

```
go test ./internal/mcp/ -run 'TestCodeBridges' -count=1
```

Expected: FAIL - `tool "code_bridges" not found in registry`.

3. Minimal impl. In `internal/mcp/tools.go`, inside `AllTools()`, insert this entry immediately after the `code_community` entry from Task 3:

```go
			{Name: "code_bridges", Description: "High-betweenness entities that connect more than one community (graph bridges), ranked, optionally filtered by repo and limited.",
				Schema: json.RawMessage(`{"type":"object","properties":{"repo":{"type":"string"},"limit":{"type":"integer"}}}`),
				Build:  codeGet("/code-graph/bridges", nil, []string{"repo", "limit"})},
```

4. Run GREEN:

```
go test ./internal/mcp/ -run 'TestCodeBridges' -count=1
```

Expected: PASS.

5. Commit:

```
git add internal/mcp/tools.go internal/mcp/tools_test.go
git commit -m "feat(mcp): add code_bridges tool mapping /code-graph/bridges"
```

---

## Task 5: Add `by` param to `code_important`

**Files:**
- Modify: `/Users/szymonri/Documents/tatara/tatara-cli/internal/mcp/tools_test.go`
- Modify: `/Users/szymonri/Documents/tatara/tatara-cli/internal/mcp/tools.go`

### Steps

1. Write failing test. Append to the end of `internal/mcp/tools_test.go`:

```go
func TestCodeImportant_ByParam(t *testing.T) {
	cases := []struct {
		name string
		args map[string]any
		q    map[string]string
	}{
		{"by betweenness", map[string]any{"repo": "r", "by": "betweenness", "limit": float64(10)}, map[string]string{"repo": "r", "by": "betweenness", "limit": "10"}},
		{"by degree", map[string]any{"by": "degree"}, map[string]string{"by": "degree"}},
		{"no by", map[string]any{"repo": "r"}, map[string]string{"repo": "r"}},
	}
	for _, c := range cases {
		c := c
		t.Run(c.name, func(t *testing.T) {
			var gotPath string
			var gotQuery url.Values
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				gotPath = r.URL.Path
				gotQuery = r.URL.Query()
				_, _ = w.Write([]byte(`[]`))
			}))
			defer srv.Close()
			cli := freshClient(t, srv.URL)
			_, err := Invoke(context.Background(), cli, toolByName(t, "code_important"), c.args)
			require.NoError(t, err)
			require.Equal(t, "/code-graph/important", gotPath)
			for k, v := range c.q {
				require.Equal(t, v, gotQuery.Get(k), "param %s", k)
			}
		})
	}
}

func TestCodeImportant_NoByParam_NotForwarded(t *testing.T) {
	var gotQuery url.Values
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.Query()
		_, _ = w.Write([]byte(`[]`))
	}))
	defer srv.Close()
	cli := freshClient(t, srv.URL)
	_, err := Invoke(context.Background(), cli, toolByName(t, "code_important"), map[string]any{"repo": "r"})
	require.NoError(t, err)
	require.False(t, gotQuery.Has("by"), "by must not be forwarded when absent")
}
```

2. Run RED:

```
go test ./internal/mcp/ -run 'TestCodeImportant' -count=1
```

Expected: FAIL - `TestCodeImportant_ByParam/by_betweenness` fails on `require.Equal "betweenness"` got `""` (the current `code_important` does not forward `by`).

3. Minimal impl. In `internal/mcp/tools.go`, replace the existing `code_important` entry (current lines 203-205):

```go
			{Name: "code_important", Description: "Most important (highest-degree) entities in the code graph, optionally filtered by repo.",
				Schema: json.RawMessage(`{"type":"object","properties":{"repo":{"type":"string"},"limit":{"type":"integer"}}}`),
				Build:  codeGet("/code-graph/important", nil, []string{"repo", "limit"})},
```

with:

```go
			{Name: "code_important", Description: "Most important entities in the code graph, ranked by 'by' (degree default, or betweenness from persisted analytics), optionally filtered by repo.",
				Schema: json.RawMessage(`{"type":"object","properties":{"repo":{"type":"string"},"limit":{"type":"integer"},"by":{"type":"string","enum":["degree","betweenness"]}}}`),
				Build:  codeGet("/code-graph/important", nil, []string{"repo", "limit", "by"})},
```

4. Run GREEN:

```
go test ./internal/mcp/ -run 'TestCodeImportant' -count=1
```

Expected: PASS.

5. Commit:

```
git add internal/mcp/tools.go internal/mcp/tools_test.go
git commit -m "feat(mcp): add 'by' (degree|betweenness) param to code_important"
```

---

## Task 6: Update tool-count assertions to 34

**Files:**
- Modify: `/Users/szymonri/Documents/tatara/tatara-cli/internal/mcp/tools_test.go`
- Modify: `/Users/szymonri/Documents/tatara/tatara-cli/internal/mcp/server_test.go`

### Steps

1. Write failing test. This is a count-correction step: the six new tools make `len(AllTools()) == 34`, so the three existing `28` assertions now fail. Run the existing count tests to confirm RED before editing:

```
go test ./internal/mcp/ -run 'TestAllTools_TwentyEightEntries|TestAllTools_Count|TestNewServer_RegistersAllTools' -count=1
```

Expected: FAIL - `TestAllTools_TwentyEightEntries` and `TestAllTools_Count` fail with `expected 28, got 34` (and the server test if it shares the assertion).

2. Update the assertions. In `internal/mcp/tools_test.go`:

- At line 145-147, rename and update `TestAllTools_TwentyEightEntries`:

```go
func TestAllTools_ThirtyFourEntries(t *testing.T) {
	assert.Len(t, AllTools(), 34)
}
```

- At line 345-348, update the `28` in `TestAllTools_Count` (keep the function name; update the comment and value):

```go
// TestAllTools_Count verifies the tool registry grows to 34 after Phase 2 additions.
func TestAllTools_Count(t *testing.T) {
	assert.Len(t, AllTools(), 34)
}
```

In `internal/mcp/server_test.go` at line 43, update the cross-check:

```go
	// Cross-check: tool count matches registry.
	assert.Len(t, AllTools(), 34)
```

3. Run GREEN:

```
go test ./internal/mcp/ -run 'TestAllTools_ThirtyFourEntries|TestAllTools_Count|TestNewServer' -count=1
```

Expected: PASS.

4. Commit:

```
git add internal/mcp/tools_test.go internal/mcp/server_test.go
git commit -m "test(mcp): bump tool-count assertions to 34 for Phase 2 tools"
```

---

## Task 7: Full-package verification

**Files:** none (verification only)

### Steps

1. Run the entire `mcp` package test suite plus a vet:

```
go test ./internal/mcp/ -count=1 && go vet ./internal/mcp/
```

Expected: `ok github.com/szymonrychu/tatara-cli/internal/mcp` and no vet output. This confirms the new tools have valid JSON schemas (`TestAllTools_SchemasAreValidJSON` exercises every entry), unique names (`TestAllTools_NamesAreUnique`), correct memory target (default `TargetMemory`, since none set `Target`), and the server registers all 34 tools without panic.

2. Run the full module build and test as a final guard:

```
go build ./... && go test ./... -count=1
```

Expected: clean build, all packages PASS.

3. No commit (verification only). If any assertion fails here, return to the offending task and follow systematic-debugging before re-committing.

---

## Notes and rationale

- No changes to `server.go`: `NewServer` already loops `AllTools()` and registers each via `buildTool` -> `mcplib.NewToolWithRawSchema(t.Name, t.Description, t.Schema)`. Adding entries to `AllTools()` is sufficient; the locked registration path is preserved (no `NewTool`/`WithRawInputSchema`).
- `community` is declared `integer` in schema but coerced to a query string by `argString` (handles `float64` from JSON), matching how `limit`/`depth` are forwarded elsewhere. `code_community` requires both `repo` and `community` per the spec route signature `/code-graph/community`.
- `min_confidence` is forwarded as a string via `argString`'s `float64` branch (e.g. `0.5`), identical to the existing `TestConfidenceParams_ForwardedAsQueryParams` pattern - no new forwarding code needed.
- The `codeGet` optional-key skip-when-empty behavior gives the spec's "optional" semantics for `by`, `relations`, `entity`, `limit`, `repo` for free; the `TestCodeImportant_NoByParam_NotForwarded` test pins that `by` is absent from the query when omitted.
- Conventional-commit scope `mcp` matches the repo's prior history (`feat(mcp):`, `fix(mcp):`).
- All new tools default to `TargetMemory` (zero value) since they hit tatara-memory routes; no `Target` field is set, consistent with every other `code_*` entry.

### Critical Files for Implementation
- /Users/szymonri/Documents/tatara/tatara-cli/internal/mcp/tools.go
- /Users/szymonri/Documents/tatara/tatara-cli/internal/mcp/tools_test.go
- /Users/szymonri/Documents/tatara/tatara-cli/internal/mcp/server_test.go
- /Users/szymonri/Documents/tatara/tatara-cli/internal/mcp/server.go