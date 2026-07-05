# Go tree-sitter fallback (ingester) Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Index non-buildable Go packages via tree-sitter when `go/packages` reports errors, instead of skipping them.

**Spec:** `docs/superpowers/specs/2026-06-06-code-graph-full-scope-design.md` (Go tree-sitter fallback).

CGo required. Read `internal/analyze/golang.go` (esp. the `len(pkg.Errors) > 0` skip), `internal/analyze/python.go` (tree-sitter walk pattern with `github.com/smacker/go-tree-sitter` + a grammar subpackage), and `internal/contract` constants first.

---

### Task 1: Fallback analyzer for broken Go packages

**Files:**
- Create: `internal/analyze/golang_fallback.go`
- Modify: `internal/analyze/golang.go` (call fallback at the package-error point)
- Test: `internal/analyze/golang_fallback_test.go`, fixture `internal/analyze/testdata/go_broken/`

- [ ] **Step 1: Broken-package fixture.** Create `internal/analyze/testdata/go_broken/pkg/broken.go` that is syntactically parseable but does NOT type-check (e.g. references an undefined symbol):

```go
package pkg

func H() int { return G() }

func G() int { return undefinedThing() } // undefinedThing is not declared -> type error
```

and `internal/analyze/testdata/go_broken/go.mod`:
```
module example.com/broken

go 1.25
```

- [ ] **Step 2: Failing test.** In `golang_fallback_test.go`, assert that running the Go analyzer over the broken package still emits a `go_func` entity for `H` and `G` (repo-relative `FilePath` `pkg/broken.go`), and a `calls` edge `H->G` with `properties[degraded_by]` containing `no_typecheck` and `confidence` <= 0.45. Run -> RED (currently the package is skipped, so zero entities).

- [ ] **Step 3: Implement `golang_fallback.go`.** A function `fallbackAnalyzeGoPackage(modulePath, absRepoRoot string, files []string, scope map[string]bool) analyze.Result` (or method on the go analyzer):
  - Compute `pkgPath` structurally: `modulePath` + "/" + (dir of the file relative to module root), or `modulePath` if the file is at module root. (Module path read from go.mod via the existing analyzer; pass it in.)
  - Parse each in-scope `.go` file with `github.com/smacker/go-tree-sitter` + the `golang` grammar (`golang.GetLanguage()`), mirroring python.go's `sitter.ParseCtx` usage.
  - Walk for `function_declaration` and `method_declaration` -> `go_func`/`go_method` entities (`go:func:<pkgPath>.<name>`, `go:method:<pkgPath>.(<recv>).<name>`); `type_declaration`/`type_spec` -> `go_type`. Emit `defines`/`contains` as the type-resolved analyzer does where cheap.
  - Walk `call_expression` -> name-based `calls` edges using the same M3 ladder as python/js (scoped/global/ambiguous/unresolved over the package's own def set), but ALWAYS add `no_typecheck` to `degraded_by` and cap confidence at 0.45 (`contract.ConfidenceFor` then min with 0.45, i.e. set "0.45" when the raw value exceeds it).
  - Emit a Chunk per func/type.
  - Respect the scope set (only in-`files` emissions).

- [ ] **Step 4: Hook into golang.go.** Where the analyzer currently does `if len(pkg.Errors) > 0 { log.Warn(...); continue }`, instead: log WARN (keep the signal) and call the fallback for that package's in-scope files, appending its Result. Type-resolved packages are unchanged. The module path is available (read it once from go.mod at analyze start, or derive from the first ok package's path - prefer reading go.mod).
- [ ] **Step 5:** Run until the test passes. Confirm the existing `TestGoAnalyzer` (type-resolved path) is unaffected.
- [ ] **Step 6:** `golangci-lint run ./internal/analyze/...` clean. Commit `feat(analyze): tree-sitter fallback for non-buildable Go packages`.

### Task 2: Verify

- [ ] Full `CGO_ENABLED=1 go test ./... -count=1` green; lint clean. Note in MEMORY.md: fallback packages emit provides (names visible) but NOT requires (no type resolution to attribute external refs) - per spec.
