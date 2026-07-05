# M5 - SCIP ingestion (ingester, scip-go first) Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Ingest a pre-generated SCIP index (`index.scip`) into the code graph via a `--scip` cmd flag, bypassing the source walker.

**Spec:** `docs/superpowers/specs/2026-06-06-code-graph-full-scope-design.md` (M5).

Read `cmd/tatara-ingest/{main,run}.go`, `internal/push/push.go`, `internal/contract/contract.go` first.

---

### Task 1: SCIP parsing + mapping

**Files:**
- Create: `internal/scip/scip.go` (parse + map), `internal/scip/scip_test.go`
- Dep: `go get github.com/sourcegraph/scip/bindings/go/scip@latest && go mod tidy`

The SCIP protobuf model (from the bindings): `Index{Metadata, Documents []*Document}`; `Document{RelativePath, Language, Occurrences []*Occurrence, Symbols []*SymbolInformation}`; `Occurrence{Range []int32, Symbol string, SymbolRoles int32}` (role bit `scip.SymbolRole_Definition`); `SymbolInformation{Symbol, Kind, DisplayName}`.

- [ ] **Step 1: Failing test.** Build a minimal `*scip.Index` IN the test (no binary fixture): one `Document{RelativePath:"foo.go", Language:"go"}` with two defined symbols `A` and `B` (each a `SymbolInformation` with a definition `Occurrence` whose `Range` covers distinct line spans) and a reference `Occurrence` to `B` located inside `A`'s definition range. Marshal it with `proto.Marshal` to a temp `index.scip`. Call `scip.Parse(path, "myrepo")` and assert:
  - entities for `A` and `B` exist with IDs `scip:go:<symbol>`, `FilePath:"foo.go"`;
  - an edge from `A`'s entity to `B`'s entity (relation `calls` if B is a function/method kind, else `references`);
  - `Files` returned == `["foo.go"]`.
  Run -> RED.

- [ ] **Step 2: Implement `internal/scip/scip.go`:**
  - `func Parse(path, repo string) (contract.GraphPush, error)`: read file, `proto.Unmarshal` into `scip.Index`.
  - For each Document: collect definition occurrences (those with the `Definition` role bit) and their `SymbolInformation`. Emit one `contract.Entity` per defined symbol: `ID = "scip:" + doc.Language + ":" + sym.Symbol`, `Name = sym.DisplayName` (or the last `.`/`/`-delimited component), `Type = scipKind(sym.Kind)` (map a few SCIP kinds -> contract entity types; fallback `"scip_symbol"`), `FilePath = doc.RelativePath`, `Properties{"scip_kind": kind.String()}`.
  - For each NON-definition (reference) occurrence, find the enclosing definition occurrence in the same document (the def whose `Range` contains the reference's start line/col); emit an edge `From = scip:<lang>:<enclosingDefSymbol>`, `To = scip:<lang>:<referencedSymbol>`, `Relation = calls` if the referenced symbol's kind is function/method else `references`, `SrcFile = doc.RelativePath`, `Properties{"resolution":"type_resolved","confidence":"0.98"}` (SCIP indexers are type-resolved). Skip references with no enclosing def.
  - Build `GraphPush{Repo: repo, Files: <sorted unique doc relative paths>, Entities, Edges}` (no Symbols, no Commit). Return it.
  - Provide a small `scipRange` helper for containment; comma-ok everywhere; handle unmarshal errors with %w.
- [ ] **Step 3:** Run until green. `golangci-lint run ./internal/scip/...` clean.
- [ ] **Step 4:** Commit `feat(scip): parse SCIP index into a code-graph push`.

### Task 2: --scip cmd flag

**Files:** Modify `cmd/tatara-ingest/main.go` (flags), `cmd/tatara-ingest/run.go`.

- [ ] Add `scipPath` and `scipRepo` to `options`; register `--scip` and `--scip-repo` flags in `resolveOptions`. When `--scip` is set, `repo-root` is NOT required (the SCIP path replaces the walk); validate `--scip-repo` is set when `--scip` is.
- [ ] In `run`, branch at the top: if `o.scipPath != ""`, call `scip.Parse(o.scipPath, o.scipRepo)`, then `push.PushGraph(ctx, gp)` (graph only - SCIP carries no chunks in v1), log a completion line with entity/edge counts, and return. Otherwise the existing walk path.
- [ ] Test in `cmd/tatara-ingest/run_test.go` (or scip-specific): build a temp index.scip, run `run` with `options{scipPath, scipRepo, baseURL}` against an httptest stub asserting `/code-graph:bulk` received the SCIP-derived entities; assert chunks endpoint NOT called.
- [ ] `CGO_ENABLED=1 go test ./... -count=1` green; `golangci-lint run ./...` clean. Commit `feat(cmd): --scip flag for SCIP-index ingestion`.

### Task 3: Verify + docs

- [ ] Full build/test/lint green. Update README usage with `--scip <index.scip> --scip-repo <name>`. Note in MEMORY.md: M5 v1 is intra-repo graph from a SCIP index; SCIP import/export monikers -> cross-repo provides/requires is deferred (ROADMAP).
