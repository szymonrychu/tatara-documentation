# M2-B - cross-repo symbol emission (ingester) Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Emit cross-repo `provides`/`requires` symbol rows from the Go, Python, and JavaScript analyzers and push them in `GraphPush.Symbols`.

**Spec:** `docs/superpowers/specs/2026-06-06-code-graph-full-scope-design.md` (M2 keying rules + M2-B).

CGo required (`CGO_ENABLED=1`). Read the existing `internal/analyze/{golang,python,javascript}.go`, `internal/contract/contract.go`, `internal/config/config.go`, `cmd/tatara-ingest/run.go` before editing.

---

### Task 1: Contract mirror

**Files:** `internal/contract/contract.go`, `internal/contract/contract_shape_test.go`.

- [ ] Add (mirroring tatara-memory exactly):
```go
const (
	RoleProvides = "provides"
	RoleRequires = "requires"
)
type SymbolRow struct {
	Symbol   string `json:"symbol"`
	Lang     string `json:"lang"`
	Kind     string `json:"kind"`
	Role     string `json:"role"`
	EntityID string `json:"entity_id"`
	SrcFile  string `json:"src_file"`
}
```
- [ ] Add `Symbols []SymbolRow `json:"symbols,omitempty"`` to `GraphPush`.
- [ ] Extend `contract_shape_test.go` to assert a `SymbolRow` marshals to keys `symbol,lang,kind,role,entity_id,src_file` and that `GraphPush` with no symbols omits the `symbols` key.
- [ ] Commit `feat(contract): cross-repo SymbolRow + GraphPush.Symbols`.

### Task 2: Result.Symbols + config

**Files:** `internal/analyze/analyzer.go`, `internal/config/config.go`, `internal/config/config_test.go`.

- [ ] Add `Symbols []contract.SymbolRow` to `analyze.Result`.
- [ ] Add `CrossRepoPrefix string` to `config.Config`, loaded from key `cross-repo-prefix`, default `github.com/szymonrychu/`. Add a config test.
- [ ] Commit `feat(analyze,config): Result.Symbols and crossRepoPrefix`.

### Task 3: Go analyzer provides/requires

**Files:** `internal/analyze/golang.go`, `internal/analyze/golang_test.go`.

The Go analyzer needs the module path (from `go.mod`) to decide intra- vs cross-module, and the org prefix to filter requires. Pass the prefix into the analyzer: change `NewGo()` to `NewGo(crossRepoPrefix string)` (update `registry.go` and any callers to pass it; `Default()` will need the prefix - see Task 6 wiring, or default it in `Default()` to `github.com/szymonrychu/`). Simplest: `NewGo(prefix string)`; `Default()` keeps a no-arg shape by reading a package default - BUT to avoid hidden globals, give `Default(cfg)` a prefix param. Decide and keep consistent across Task 6.

- [ ] Behavior (add to `golang_test.go`, expand the fixture module): for a fixture whose module is `example.com/sample`, an exported func `pkg.F` produces a `provides` SymbolRow `{Symbol:"example.com/sample/pkg.F", Lang:"go", Kind:"func", Role:"provides", EntityID:"go:func:example.com/sample/pkg.F", SrcFile:"pkg/pkg.go"}`. A reference to an external package under the configured prefix (e.g. add a fixture importing `github.com/szymonrychu/other/thing.Do` is not buildable in a fixture) - so test requires via a UNIT on the helper instead: provide a small fixture that imports another in-fixture module path under the prefix is impractical. Simpler testable rule: make the prefix configurable in the test to `example.com/` so that references to `example.com/sample/...` from a DIFFERENT module are "external". Since the fixture is one module, test `requires` by setting prefix to a value that matches the fixture's own deps if any, OR unit-test the classifier function directly: extract `func classifyRef(objPkgPath, modulePath, prefix string) (emit bool, symbol string)` and unit-test it: in-module -> no requires; external under prefix -> requires with symbol=objPkgPath+"."+name; external not under prefix (stdlib) -> no requires.
- [ ] Implement: after building entities, for each exported package-level decl emit a `provides` SymbolRow (Kind from the entity type: func/type/method). For requires, walk `TypesInfo.Uses`; for each used object whose `obj.Pkg() != nil`, pkg path is outside the current module, and starts with `crossRepoPrefix`, emit a `requires` SymbolRow `{Symbol: objPkgPath+"."+obj.Name(), Lang:"go", Kind: kindOf(obj), Role:"requires", EntityID: <the using entity's ID>, SrcFile: <the using file>}` (dedup by (symbol, entity_id)). SrcFile must be in scope.
- [ ] Run `CGO_ENABLED=1 go test ./internal/analyze/... -run TestGo` green; commit `feat(analyze): Go provides/requires cross-repo symbols`.

### Task 4: Python provides/requires

**Files:** `internal/analyze/python.go`, `internal/analyze/python_test.go`.

- [ ] provides: each top-level (module-level) func/class -> SymbolRow `{Symbol:"<dotted.module>.<name>", Lang:"python", Kind:"func"|"class", Role:"provides", EntityID: <py entity id>, SrcFile}`.
- [ ] requires: for each import that does NOT resolve to an in-repo module (the existing unresolved-import detection), emit a `requires` SymbolRow `{Symbol:"<imported dotted name>", Lang:"python", Kind:"module", Role:"requires", EntityID: <importing module entity id>, SrcFile}`.
- [ ] Test (extend python_test.go): assert a provides row for a top-level def and a requires row for an unresolved external import.
- [ ] Commit `feat(analyze): Python provides/requires cross-repo symbols`.

### Task 5: JavaScript provides/requires

**Files:** `internal/analyze/javascript.go`, `internal/analyze/javascript_test.go`.

- [ ] provides: exported top-level funcs/classes -> SymbolRow `{Symbol:"<module-rel-path>::<name>", Lang:"javascript", Kind:..., Role:"provides", EntityID, SrcFile}`.
- [ ] requires: imports that do not resolve to an in-repo module -> `requires` SymbolRow `{Symbol:"<import specifier>", Lang:"javascript", Kind:"module", Role:"requires", EntityID:<importing module id>, SrcFile}`.
- [ ] Test: provides for an exported func; requires for a bare external import (e.g. `import x from 'react'`).
- [ ] Commit `feat(analyze): JavaScript provides/requires cross-repo symbols`.

### Task 6: Wire symbols through run + registry

**Files:** `cmd/tatara-ingest/run.go`, `internal/analyze/registry.go`, `cmd/tatara-ingest/main.go` (prefix plumb).

- [ ] `Default(crossRepoPrefix string)` passes the prefix to `NewGo`. Update all callers (`run.go` builds the registry with `cfg.CrossRepoPrefix`; pass it through `options`). Keep Python/JS constructors unchanged (they don't need the prefix; their requires are "unresolved in-repo" which is prefix-independent).
- [ ] In `run.go`, aggregate `res.Symbols` into `agg.Symbols` and set `GraphPush.Symbols = agg.Symbols`. Add the symbol count to the completion slog line.
- [ ] Update `cmd/tatara-ingest/run_test.go` if the registry/Default signature changed.
- [ ] Run full `CGO_ENABLED=1 go test ./...`, `golangci-lint run ./...` clean. Commit `feat(cmd): push cross-repo symbols`.

### Task 7: Verify

- [ ] `CGO_ENABLED=1 go build ./...`; full `CGO_ENABLED=1 go test ./... -race -count=1` green; lint clean. All emitted SymbolRow.SrcFile values are repo-relative and within the analyzer's `files` arg (the scope rule still holds for symbols).
