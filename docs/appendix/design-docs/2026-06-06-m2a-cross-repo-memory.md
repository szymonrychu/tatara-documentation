# M2-A - cross-repo symbols in tatara-memory Plan

> REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Accept cross-repo provides/requires symbol rows on the bulk push, store them per-file-scoped, and serve a `/code/cross-repo` join endpoint.

**Architecture:** Additive `GraphPush.Symbols`; new `cross_repo_symbols` table reconciled in the existing transaction; new `CrossRepo` Store/Service/HTTP method joining requires<->provides on `(symbol,lang)` across repos.

**Spec:** `docs/superpowers/specs/2026-06-06-code-graph-full-scope-design.md` (M2 + M2-A).

**Integration DB:** `TATARA_TEST_PG_DSN=postgres://postgres:pg@localhost:55433/tatara_test?sslmode=disable` is running. Run integration tests with `-tags integration`.

All paths under `internal/codegraph/` unless noted. Follow the existing file patterns exactly (the package already has types.go, store.go, pgstore.go, service.go, migrate.go, migrations/, metrics.go and the httpapi handlers/router).

---

### Task 1: Contract types (write path)

**Files:** Modify `internal/codegraph/types.go`.

- [ ] Add, near the existing structs:

```go
// Symbol roles for cross-repo resolution.
const (
	RoleProvides = "provides"
	RoleRequires = "requires"
)

// SymbolRow is a cross-repo provides/requires fact owned by a file.
type SymbolRow struct {
	Symbol   string `json:"symbol"`
	Lang     string `json:"lang"`
	Kind     string `json:"kind"`
	Role     string `json:"role"`
	EntityID string `json:"entity_id"`
	SrcFile  string `json:"src_file"`
}

// CrossRef is one end of a cross-repo symbol link.
type CrossRef struct {
	Repo     string `json:"repo"`
	EntityID string `json:"entity_id"`
	Symbol   string `json:"symbol"`
	Lang     string `json:"lang"`
}

// CrossRepoLinks are the cross-repo consumers/providers of an entity.
type CrossRepoLinks struct {
	Consumers []CrossRef `json:"consumers"` // others requiring what this entity provides
	Providers []CrossRef `json:"providers"` // others providing what this entity requires
}
```

- [ ] Add `Symbols []SymbolRow `json:"symbols,omitempty"`` to `GraphPush`.
- [ ] Commit `feat(codegraph): cross-repo symbol contract types`.

### Task 2: Migration

**Files:** Create `internal/codegraph/migrations/0002_cross_repo_symbols.sql`; modify `migrate.go`.

- [ ] Write the SQL (verbatim):

```sql
CREATE TABLE IF NOT EXISTS cross_repo_symbols (
    repo      text NOT NULL,
    symbol    text NOT NULL,
    lang      text NOT NULL,
    kind      text NOT NULL DEFAULT '',
    role      text NOT NULL,
    entity_id text NOT NULL,
    src_file  text NOT NULL DEFAULT '',
    PRIMARY KEY (repo, symbol, role, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_crs_join ON cross_repo_symbols(symbol, lang, role);
CREATE INDEX IF NOT EXISTS idx_crs_src  ON cross_repo_symbols(repo, src_file);
```

- [ ] In `migrate.go`: add `//go:embed migrations/0002_cross_repo_symbols.sql` (var `migration0002`) and run it after 0001 in `Migrate` (two `ExecContext` calls in order; both idempotent). Update `MigrationSQL()` if it returns the embedded SQL (concatenate or return both - match the existing helper's contract).
- [ ] Integration test in `migrate_test.go` (or pgstore_test.go) that after `Migrate` the `cross_repo_symbols` table exists. Run with `-tags integration`.
- [ ] Commit `feat(codegraph): cross_repo_symbols migration`.

### Task 3: Reconcile symbols + scope validation

**Files:** Modify `internal/codegraph/pgstore.go` (Reconcile), `internal/codegraph/service.go` (Push validation).

- [ ] In `Reconcile`, inside the existing transaction, AFTER the entity/edge upserts: for each `f` in `p.Files`, `DELETE FROM cross_repo_symbols WHERE repo=$1 AND src_file=$2`; then for each `s` in `p.Symbols`, upsert:

```go
INSERT INTO cross_repo_symbols(repo, symbol, lang, kind, role, entity_id, src_file)
VALUES ($1,$2,$3,$4,$5,$6,$7)
ON CONFLICT (repo, symbol, role, entity_id) DO UPDATE SET
    lang=EXCLUDED.lang, kind=EXCLUDED.kind, src_file=EXCLUDED.src_file
```

  (Add a `SymbolsUpserted int` to `PushResult`? NO - keep PushResult frozen; the existing fields suffice. Do not change PushResult.)
- [ ] In `service.go` `Push`, extend validation: each `s` in `p.Symbols` must have `s.SrcFile` in the `files` set (else `ErrInvalidScope`) and `s.Role` in {provides, requires} (else `ErrInvalidScope`).
- [ ] Integration test (`-tags integration`): push entities+symbols for repo A, re-push one file with changed symbols, assert per-file replacement (old symbols for that file gone, others intact). A push with a symbol whose src_file is not in files returns ErrInvalidScope.
- [ ] Commit `feat(codegraph): reconcile cross-repo symbols, scope-validated`.

### Task 4: CrossRepo query (read path)

**Files:** Modify `internal/codegraph/store.go` (interface), `pgstore.go` (impl), `service.go` (method).

- [ ] Add to the `Store` interface: `CrossRepo(ctx context.Context, repo, id string) (CrossRepoLinks, error)`.
- [ ] Implement in `pgstore.go` with two fixed parameterized queries (gosec-safe):

```go
// Consumers: others that REQUIRE a symbol this entity PROVIDES.
const crossConsumersQuery = `
SELECT r.repo, r.entity_id, r.symbol, r.lang
FROM cross_repo_symbols p
JOIN cross_repo_symbols r
  ON r.symbol = p.symbol AND r.lang = p.lang AND r.role = 'requires'
WHERE p.repo = $1 AND p.entity_id = $2 AND p.role = 'provides' AND r.repo <> $1
ORDER BY r.repo, r.entity_id`

// Providers: others that PROVIDE a symbol this entity REQUIRES.
const crossProvidersQuery = `
SELECT q.repo, q.entity_id, q.symbol, q.lang
FROM cross_repo_symbols rq
JOIN cross_repo_symbols q
  ON q.symbol = rq.symbol AND q.lang = rq.lang AND q.role = 'provides'
WHERE rq.repo = $1 AND rq.entity_id = $2 AND rq.role = 'requires' AND q.repo <> $1
ORDER BY q.repo, q.entity_id`
```

  Scan each into `[]CrossRef`. Return `CrossRepoLinks{Consumers, Providers}`.
- [ ] Add `CrossRepo` to `Service` (thin pass-through to `store.CrossRepo`).
- [ ] Integration test: two repos, repo A provides `sym`, repo B requires `sym`; assert `CrossRepo(A, entityA)` returns B in Consumers and `CrossRepo(B, entityB)` returns A in Providers; self-repo matches excluded.
- [ ] Commit `feat(codegraph): cross-repo join query`.

### Task 5: HTTP endpoint

**Files:** Modify `internal/httpapi/codegraph.go` (handler), `router.go` (route), `internal/httpapi/service.go` (`CodeGraphService` interface), and the stub in the httpapi tests.

- [ ] Add `CrossRepo(ctx, repo, id string) (codegraph.CrossRepoLinks, error)` to the `CodeGraphService` interface and the test stub (`stubService` in the httpapi tests - add the method returning a canned value).
- [ ] `handleCrossRepo(cfg)` using the existing `reqRepo`/`reqIDParam` helpers; on success `WriteJSON(200, links)`; map errors via the existing `mapServiceError`.
- [ ] Mount `r.Get("/code/cross-repo", handleCrossRepo(cfg))` in the `cfg.CodeGraph != nil` block in `router.go`.
- [ ] Handler test (the existing httpapi codegraph test style) asserting 200 + the JSON shape, and 400 when repo/id missing.
- [ ] Commit `feat(httpapi): GET /code/cross-repo`.

### Task 6: Verify full suite

- [ ] `go build ./...`; `go test ./... -count=1` (unit) green; `TATARA_TEST_PG_DSN=... go test -tags integration ./internal/codegraph/... -count=1` green; `golangci-lint run ./...` clean.
- [ ] Confirm a legacy push WITHOUT `symbols` still validates and reconciles (back-compat) - add/confirm a test.
