# Component C - code-graph MCP tools (tatara-cli) Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Add 9 code-graph MCP tools to tatara-cli wrapping tatara-memory's `/code/*` GET endpoints.

**Architecture:** Append `Tool` entries to `AllTools()` in `internal/mcp/tools.go`. Each `Build` returns `(GET, "/code/...?<encoded-query>", nil, err)`. Reuse existing `Invoke`/`client.Do`/auth unchanged.

**Spec:** `docs/superpowers/specs/2026-06-06-code-graph-full-scope-design.md` (Component C table).

---

### Task 1: Add the 9 code-graph tools

**Files:**
- Modify: `internal/mcp/tools.go` (append to `AllTools()`)
- Modify: `internal/mcp/tools_test.go` (count 13 -> 22 + new behavior tests)

- [ ] **Step 1: Update the count test + add behavior tests (RED).**

In `tools_test.go`, change the count assertion from 13 to 22. Add table-driven tests asserting each new tool builds the right GET method, path, and query. Pattern (reuse existing `freshClient`, `toolByName`, httptest):

```go
func TestCodeTools_BuildQueries(t *testing.T) {
	cases := []struct {
		tool string
		args map[string]any
		path string // expected URL.Path
		q    map[string]string // expected query params
	}{
		{"code_search", map[string]any{"repo": "r", "q": "x", "type": "go_func", "limit": float64(10)}, "/code/entities", map[string]string{"repo": "r", "q": "x", "type": "go_func", "limit": "10"}},
		{"code_entity", map[string]any{"repo": "r", "id": "go:func:m.F"}, "/code/entity", map[string]string{"repo": "r", "id": "go:func:m.F"}},
		{"code_neighbors", map[string]any{"repo": "r", "id": "x", "relation": "calls", "direction": "out", "depth": float64(2)}, "/code/neighbors", map[string]string{"repo": "r", "id": "x", "relation": "calls", "direction": "out", "depth": "2"}},
		{"code_callers", map[string]any{"repo": "r", "id": "x", "depth": float64(3)}, "/code/callers", map[string]string{"repo": "r", "id": "x", "depth": "3"}},
		{"code_callees", map[string]any{"repo": "r", "id": "x"}, "/code/callees", map[string]string{"repo": "r", "id": "x"}},
		{"code_dependents", map[string]any{"repo": "r", "id": "x"}, "/code/dependents", map[string]string{"repo": "r", "id": "x"}},
		{"code_dependencies", map[string]any{"repo": "r", "id": "x"}, "/code/dependencies", map[string]string{"repo": "r", "id": "x"}},
		{"code_file_imports", map[string]any{"repo": "r", "path": "a/b.go"}, "/code/file-imports", map[string]string{"repo": "r", "path": "a/b.go"}},
		{"code_resource_graph", map[string]any{"repo": "r", "id": "x", "depth": float64(1)}, "/code/resource-graph", map[string]string{"repo": "r", "id": "x", "depth": "1"}},
	}
	for _, c := range cases {
		t.Run(c.tool, func(t *testing.T) {
			var gotPath string
			var gotQuery url.Values
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				gotPath = r.URL.Path
				gotQuery = r.URL.Query()
				_, _ = w.Write([]byte(`[]`))
			}))
			defer srv.Close()
			cli := freshClient(t, srv.URL)
			_, err := Invoke(context.Background(), cli, toolByName(t, c.tool), c.args)
			require.NoError(t, err)
			require.Equal(t, c.path, gotPath)
			for k, v := range c.q {
				require.Equal(t, v, gotQuery.Get(k))
			}
		})
	}
}

func TestCodeTools_RequireArgs(t *testing.T) {
	_, _, _, err := toolByName(t, "code_entity").Build(map[string]any{"repo": "r"})
	require.Error(t, err) // id required
	_, _, _, err = toolByName(t, "code_search").Build(map[string]any{})
	require.Error(t, err) // repo required
	_, _, _, err = toolByName(t, "code_neighbors").Build(map[string]any{"repo": "r", "id": "x"})
	require.Error(t, err) // relation required
}
```

Add `"net/url"` to test imports. Run `go test ./internal/mcp/...` -> RED (22 != 13, undefined tools).

- [ ] **Step 2: Implement the tools (GREEN).**

Add a small query helper in `tools.go` and append 9 `Tool`s. Helper:

```go
// codeGet builds a GET tool path with an encoded query, requiring the given keys.
func codeGet(path string, required []string, optional []string) func(map[string]any) (string, string, any, error) {
	return func(a map[string]any) (string, string, any, error) {
		q := url.Values{}
		for _, k := range required {
			v := argString(a, k)
			if v == "" {
				return "", "", nil, fmt.Errorf("%s required", k)
			}
			q.Set(k, v)
		}
		for _, k := range optional {
			if v := argString(a, k); v != "" {
				q.Set(k, v)
			}
		}
		return http.MethodGet, path + "?" + q.Encode(), nil, nil
	}
}

// argString coerces string or JSON number args to a string.
func argString(a map[string]any, k string) string {
	switch v := a[k].(type) {
	case string:
		return v
	case float64:
		if v == float64(int64(v)) {
			return strconv.FormatInt(int64(v), 10)
		}
		return strconv.FormatFloat(v, 'f', -1, 64)
	case int:
		return strconv.Itoa(v)
	default:
		return ""
	}
}
```

Tools (append to the `AllTools()` slice):

```go
{Name: "code_search", Description: "Search code-graph entities by name/description, optional type filter.",
 Schema: json.RawMessage(`{"type":"object","properties":{"repo":{"type":"string"},"q":{"type":"string"},"type":{"type":"string"},"limit":{"type":"integer"}},"required":["repo"]}`),
 Build: codeGet("/code/entities", []string{"repo"}, []string{"q", "type", "limit"})},
{Name: "code_entity", Description: "Get a single code entity and its immediate edges.",
 Schema: json.RawMessage(`{"type":"object","properties":{"repo":{"type":"string"},"id":{"type":"string"}},"required":["repo","id"]}`),
 Build: codeGet("/code/entity", []string{"repo", "id"}, nil)},
{Name: "code_neighbors", Description: "Traverse the code graph from an entity along a relation.",
 Schema: json.RawMessage(`{"type":"object","properties":{"repo":{"type":"string"},"id":{"type":"string"},"relation":{"type":"string"},"direction":{"type":"string","enum":["out","in"]},"depth":{"type":"integer"}},"required":["repo","id","relation"]}`),
 Build: codeGet("/code/neighbors", []string{"repo", "id", "relation"}, []string{"direction", "depth"})},
{Name: "code_callers", Description: "Who calls this function/method (reverse calls), to depth N.",
 Schema: json.RawMessage(`{"type":"object","properties":{"repo":{"type":"string"},"id":{"type":"string"},"depth":{"type":"integer"}},"required":["repo","id"]}`),
 Build: codeGet("/code/callers", []string{"repo", "id"}, []string{"depth"})},
{Name: "code_callees", Description: "What this function/method calls (forward calls), to depth N.",
 Schema: json.RawMessage(`{"type":"object","properties":{"repo":{"type":"string"},"id":{"type":"string"},"depth":{"type":"integer"}},"required":["repo","id"]}`),
 Build: codeGet("/code/callees", []string{"repo", "id"}, []string{"depth"})},
{Name: "code_dependents", Description: "What depends on this entity (reverse imports/references/depends_on).",
 Schema: json.RawMessage(`{"type":"object","properties":{"repo":{"type":"string"},"id":{"type":"string"},"depth":{"type":"integer"}},"required":["repo","id"]}`),
 Build: codeGet("/code/dependents", []string{"repo", "id"}, []string{"depth"})},
{Name: "code_dependencies", Description: "What this entity depends on (forward imports/references/depends_on).",
 Schema: json.RawMessage(`{"type":"object","properties":{"repo":{"type":"string"},"id":{"type":"string"},"depth":{"type":"integer"}},"required":["repo","id"]}`),
 Build: codeGet("/code/dependencies", []string{"repo", "id"}, []string{"depth"})},
{Name: "code_file_imports", Description: "Imports out of a file's package.",
 Schema: json.RawMessage(`{"type":"object","properties":{"repo":{"type":"string"},"path":{"type":"string"}},"required":["repo","path"]}`),
 Build: codeGet("/code/file-imports", []string{"repo", "path"}, nil)},
{Name: "code_resource_graph", Description: "Terraform/Helm dependency subgraph for a resource.",
 Schema: json.RawMessage(`{"type":"object","properties":{"repo":{"type":"string"},"id":{"type":"string"},"depth":{"type":"integer"}},"required":["repo","id"]}`),
 Build: codeGet("/code/resource-graph", []string{"repo", "id"}, []string{"depth"})},
```

Add imports `"net/url"`, `"strconv"` to tools.go if not present. Run `go test ./internal/mcp/...` -> GREEN.

- [ ] **Step 3: Lint + full test + commit.**

`golangci-lint run ./...` clean; `go test ./... -count=1` green.
```bash
git add internal/mcp/tools.go internal/mcp/tools_test.go
git commit -m "feat(mcp): code-graph tools wrapping /code/* endpoints"
```
