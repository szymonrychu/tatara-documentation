# Code-Graph Full Scope - Design (C, M2, Go-fallback, M5)

**Date:** 2026-06-06
**Status:** design approved (forks pinned); continuous build, checkpoint per merge
**Spans:** `tatara-cli` (C, M2-C), `tatara-memory` (M2-A), `tatara-memory-repo-ingester` (M2-B, Go-fallback, M5)
**Builds on:** A (`tatara-memory` codegraph, shipped) and B (ingester MVP, shipped `6ca98f5`).
**References:** `docs/superpowers/specs/2026-06-05-tatara-code-ingestion-design.md`,
`docs/superpowers/specs/2026-06-06-research-grounded-improvements-design.md` (M2/M5),
`docs/superpowers/specs/2026-06-06-tatara-memory-repo-ingester-design.md`.

## Scope (approved forks)

1. **C** - tatara-cli code-graph MCP tools (9 tools wrapping A's `/code/*`).
2. **M2** - cross-repo symbol resolution for **Go + Python + JavaScript**, stored
   as a **symbols table + join endpoint** (additive `GraphPush.Symbols`,
   `cross_repo_symbols` table, `/code/cross-repo` endpoint). Spans A -> B -> C.
3. **Go tree-sitter fallback** - index non-buildable Go packages (B).
4. **M5** - SCIP ingestion, **scip-go first**, via a `--scip` cmd flag consuming a
   pre-generated index (B).

Build order (each its own writing-plans -> subagent-driven cycle, merged before
the next): **C -> M2-A -> M2-B -> M2-C -> Go-fallback -> M5**. M2-A must precede
M2-B (contract) which precedes M2-C (the cross-repo tool wraps the new endpoint).

---

## Component C - tatara-cli code-graph MCP tools

Append 9 `Tool` entries to `AllTools()` in `internal/mcp/tools.go`, following the
exact existing pattern (`Tool{Name, Description, Schema json.RawMessage, Build}`
where `Build(args) -> (method, path, body, err)`). All `/code/*` endpoints are
**GET with query params**, so each `Build` returns `http.MethodGet`, a path with a
URL-encoded query string (built via `net/url.Values`), and `nil` body. The
existing `Invoke` -> `client.Do` plumbing and OIDC auth are reused unchanged.

| Tool | Endpoint | Required args | Optional args |
| --- | --- | --- | --- |
| `code_search` | `GET /code/entities` | `repo` | `q`, `type`, `limit` |
| `code_entity` | `GET /code/entity` | `repo`, `id` | - |
| `code_neighbors` | `GET /code/neighbors` | `repo`, `id`, `relation` | `direction`, `depth` |
| `code_callers` | `GET /code/callers` | `repo`, `id` | `depth` |
| `code_callees` | `GET /code/callees` | `repo`, `id` | `depth` |
| `code_dependents` | `GET /code/dependents` | `repo`, `id` | `depth` |
| `code_dependencies` | `GET /code/dependencies` | `repo`, `id` | `depth` |
| `code_file_imports` | `GET /code/file-imports` | `repo`, `path` | - |
| `code_resource_graph` | `GET /code/resource-graph` | `repo`, `id` | `depth` |

Each `Build` validates required args (returning an error like the existing
`"id required"` pattern) and skips empty optional args when building the query.
Schemas are inline JSON (`type`, `properties`, `required`), matching the existing
style. Update the tool-count test (currently asserts 13) to 22, and the schema/
uniqueness tests cover the new tools automatically. Tests follow the existing
`httptest` + `freshClient` + `toolByName` pattern, asserting GET method, exact
path, and decoded query params.

M2-C adds a 10th tool, `code_cross_repo` (`GET /code/cross-repo`, required
`repo`+`id`), in the M2 cycle after A's endpoint exists (count -> 23).

---

## M2 - cross-repo symbol resolution

### Contract (A and B, mirrored)

Additive `SymbolRow` + `GraphPush.Symbols` (omitempty, so existing pushes and the
frozen contract are unaffected):

```go
type SymbolRow struct {
    Symbol   string `json:"symbol"`    // cross-repo join key (see per-language keying)
    Lang     string `json:"lang"`      // go | python | javascript
    Kind     string `json:"kind"`      // func | type | method | class | module ...
    Role     string `json:"role"`      // "provides" | "requires"
    EntityID string `json:"entity_id"` // the owning entity's canonical ID in this repo
    SrcFile  string `json:"src_file"`  // repo-relative owning file (unit of replace)
}
// GraphPush gains:  Symbols []SymbolRow `json:"symbols,omitempty"`
```

Role constants: `RoleProvides = "provides"`, `RoleRequires = "requires"`.

### Per-language symbol keying

The join is on `(symbol, lang)`. Keying per language:

- **Go (type-resolved, globally unique):** `symbol = "<pkgImportPath>.<name>"`
  (e.g. `github.com/szymonrychu/tatara-memory/internal/codegraph.Entity`).
  `provides`: every exported package-level decl (func, type, exported method).
  `requires`: every reference (`TypesInfo.Uses`) resolving to an object whose
  package import path is **outside the current module AND under a configured org
  prefix** (default `github.com/szymonrychu/`), so stdlib/third-party are
  excluded. High confidence.
- **Python (name-based):** `symbol = "<dotted.module>.<name>"`.
  `provides`: top-level (module-level) funcs/classes. `requires`: imported names
  that do not resolve within the repo (the existing "unresolved import" case).
  Cross-repo match is name-based; `lang=python` signals the lower precision.
- **JavaScript (name-based):** `symbol = "<module-rel-path>::<name>"`.
  `provides`: exported top-level funcs/classes. `requires`: imports that do not
  resolve to an in-repo module. name-based.

The org-prefix filter (Go) is a config scalar (`crossRepoPrefix`, default
`github.com/szymonrychu/`); Python/JS emit all unresolved-external as requires
(the join only matches when another repo actually provides the same key, so
non-tatara names simply never join - lean enough at platform scale).

### M2-A (tatara-memory)

- **types.go:** add `SymbolRow` + `GraphPush.Symbols` + role constants.
- **migrations/0002_cross_repo_symbols.sql:**

```sql
CREATE TABLE IF NOT EXISTS cross_repo_symbols (
    repo      text NOT NULL,
    symbol    text NOT NULL,
    lang      text NOT NULL,
    kind      text NOT NULL DEFAULT '',
    role      text NOT NULL,            -- provides | requires
    entity_id text NOT NULL,
    src_file  text NOT NULL DEFAULT '',
    PRIMARY KEY (repo, symbol, role, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_crs_join ON cross_repo_symbols(symbol, lang, role);
CREATE INDEX IF NOT EXISTS idx_crs_src  ON cross_repo_symbols(repo, src_file);
```

  `migrate.go` runs 0001 then 0002 (embed both; idempotent `IF NOT EXISTS`).
- **pgstore.go Reconcile:** in the SAME transaction, after the entity/edge
  delete+upsert, for each file `DELETE FROM cross_repo_symbols WHERE repo=$1 AND
  src_file=$2`, then upsert `p.Symbols` (`ON CONFLICT (repo,symbol,role,entity_id)
  DO UPDATE`). File-granular replace preserved.
- **service.go Push:** extend scope validation - each `SymbolRow.SrcFile` must be
  in the push `files` set (else `ErrInvalidScope`), and `Role` must be
  provides/requires.
- **New read path `CrossRepo(ctx, repo, id) (CrossRepoLinks, error)`** on the
  Store + Service + `CodeGraphService` interface. Returns:

```go
type CrossRef struct {
    Repo     string `json:"repo"`
    EntityID string `json:"entity_id"`
    Symbol   string `json:"symbol"`
    Lang     string `json:"lang"`
}
type CrossRepoLinks struct {
    // others that REQUIRE a symbol this entity PROVIDES (downstream consumers)
    Consumers []CrossRef `json:"consumers"`
    // others that PROVIDE a symbol this entity REQUIRES (upstream providers)
    Providers []CrossRef `json:"providers"`
}
```

  SQL: `Consumers` = join this entity's `provides` rows to other repos' `requires`
  rows on `(symbol, lang)` where `repo <> $1`. `Providers` = the reverse. Two
  fixed parameterized queries (gosec-safe), `repo <> $1` excludes self-matches.
- **httpapi:** `handleCrossRepo` -> `GET /code/cross-repo?repo=&id=` returning
  `CrossRepoLinks`; mount in `router.go` under the `cfg.CodeGraph != nil` block.
- **Tests:** integration (Reconcile of symbols, per-file replace, the join query
  across two repos); handler test with a stub returning `CrossRepoLinks`.

### M2-B (ingester)

- **contract.go:** mirror `SymbolRow` + `GraphPush.Symbols` + role constants;
  extend `contract_shape_test.go` for the new field.
- **analyze.Result:** add `Symbols []contract.SymbolRow`.
- **Go analyzer:** emit `provides` for exported package-level decls (symbol =
  import-path FQN); emit `requires` for `TypesInfo.Uses` objects whose package is
  outside the module and under `crossRepoPrefix`. `EntityID` = the existing
  go: entity ID; `SrcFile` = the repo-relative owning file (must be in scope).
- **Python/JS analyzers:** emit `provides` for top-level defs and `requires` for
  unresolved-external imports, keyed per the scheme above.
- **config:** add `crossRepoPrefix` (default `github.com/szymonrychu/`).
- **run.go:** thread `agg.Symbols` into `GraphPush.Symbols`. Symbols whose
  `SrcFile` is outside the pushed `files` set must not be emitted (scope rule).
- **Tests:** per-analyzer assertions that provides/requires rows are emitted with
  correct symbol keys and roles; a Go test that an external tatara-prefixed
  reference produces a `requires` row and a stdlib reference does not.

### M2-C (tatara-cli)

One `code_cross_repo` tool: `GET /code/cross-repo?repo=&id=`, required `repo`+`id`.
Count test -> 23.

---

## Go tree-sitter fallback (ingester)

Add the smacker `golang` grammar (`github.com/smacker/go-tree-sitter/golang`).
New `internal/analyze/golang_fallback.go`: given a package's in-scope `.go` files,
parse each with tree-sitter, extract `function_declaration`/`method_declaration`
-> `go_func`/`go_method` and `type_declaration` -> `go_type` entities, and
`call_expression` -> name-based `calls` edges using the existing M3 ladder, with
`degraded_by=no_typecheck` and confidence capped at 0.45.

Entity IDs match the type-resolved scheme: `pkgPath` is computed structurally as
`modulePath + "/" + relDirFromModuleRoot` (module path read from `go.mod`; no
type-checking needed), so fallback IDs (`go:func:<pkgPath>.<name>`) are consistent
with the type-resolved analyzer's IDs.

Hook: in `golang.go`, where a package with `len(pkg.Errors) > 0` is currently
WARN+skipped, instead invoke the fallback for that package's in-scope files.
Type-resolved packages are unchanged. A test fixture with a deliberately
non-compiling package asserts entities + degraded call edges are still emitted.

`provides`/`requires` (M2) from fallback packages: emit `provides` for exported
decls (names are visible); skip `requires` (no type resolution to attribute a ref
to an external package reliably) - documented limitation.

---

## M5 - SCIP ingestion (ingester, scip-go first)

Add `github.com/sourcegraph/scip/bindings/go/scip` (protobuf bindings). A
`--scip <index.scip> --scip-repo <name>` cmd flag bypasses the walker:

- Parse the SCIP `Index` (protobuf). For each `Document` (carrying a repo-relative
  `relative_path`): emit an `Entity` per `SymbolInformation`/definition occurrence
  (map SCIP `Kind` -> contract entity type where it maps, else a generic
  `scip_symbol`), `file_path = document.relative_path`. For reference occurrences,
  emit `references` edges (and `calls` where SCIP role/kind indicates a call) from
  the enclosing definition to the referenced symbol's entity ID. Entity ID =
  `scip:<lang>:<scip-symbol-string>`.
- `GraphPush.Files` = the set of `document.relative_path` in the index.
- Push via the existing `push.PushGraph` (graph only; SCIP carries no semantic
  chunks in v1).
- First cut proves the mapping on a scip-go index fixture. Other languages need
  only their own `index.scip` (generated out-of-band) - no analyzer changes.

SCIP cross-repo (import/export monikers -> provides/requires) is a later combine,
not in this cut (documented in ROADMAP).

---

## Cross-cutting

- Hard rules unchanged: OIDC-gated endpoints, rule 6 config mapping, rule 14
  cluster-agnostic charts, JSON slog, metrics (A's existing code-graph counters
  extend to cross_repo_symbols counts; B logs symbol counts in its completion
  line), worktree branch flow (rule 10), sonnet implement / opus review (rule 7).
- A's frozen wire contract stays back-compatible: `Symbols` is additive+omitempty;
  pre-M2 ingester pushes (no symbols) still validate and reconcile.
- New deps: tatara-cli none; tatara-memory none; ingester gains
  `smacker/go-tree-sitter/golang` (fallback) and `sourcegraph/scip` (M5).

## Open tradeoffs (accepted)

- Python/JS cross-repo symbols are name-based; collisions across repos can produce
  spurious joins. `lang` records the precision; consumers weight accordingly.
- Cross-repo query is one hop (no recursive cross-repo CTE) - bounded and
  deterministic; multi-hop is a later enhancement.
- Go fallback packages emit provides but not requires (no type resolution).
- M5 v1 is intra-repo graph from a SCIP index; SCIP monikers -> cross-repo is
  deferred.
