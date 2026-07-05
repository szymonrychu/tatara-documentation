# Phase 0 Contract + Re-ingest Server Surface (tatara-memory) Implementation Plan

## Header

- **Repo:** `/Users/szymonri/Documents/tatara/tatara-memory`
- **Module:** `github.com/szymonrychu/tatara-memory`
- **Go directive:** `go 1.25.0`
- **Scope (this plan only):** the tatara-memory server-side surface that every other re-ingest plan depends on -- the migration wave (code-graph Phase-0 columns/tables + `memory_sources`), the locked Phase-0 contract types (`Edge` confidence, `Entity` provenance, `Hyperedge` + `GraphPush.Hyperedges`, new entity-type/relation constants), the `pgstore.Reconcile` writes for those, the `memory_sources` population in the ingest worker, and `DeleteMemoriesBySource` + `/memories:bulk reconcile_files` purge-then-insert.
- **Out of scope (other plans):** the operator clone/cron, the ingester `walk.Changes`/`content_sha`, `DocsAnalyzer` doc entities, the analytics compute job. This plan ships only schema, contract, store writes, and the server reconcile path. Producers may emit the new fields empty.
- **Contract authority:** `/Users/szymonri/Documents/tatara/docs/superpowers/specs/2026-06-09-phase0-contract-lock.md`. The JSON tags below are copied byte-for-byte from that lock and MUST NOT be altered.

### Conventions in this repo (observed; follow exactly)

- Migrations: one `.sql` file per wave under `internal/<pkg>/migrations/`, `//go:embed`-ed in `migrate.go`, concatenated by `MigrationSQL()`, applied sequentially in `Migrate()`. All DDL is `CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` (idempotent). `MigrationSQL()` is unit-tested with `strings.Contains` assertions in `migrate_test.go` (package `<pkg>_test`).
- Integration (DB-backed) tests carry `//go:build integration`, open a DSN from `TATARA_TEST_PG_DSN` (skip when unset), use `testify/require`, and a `freshStore`/`freshStoreWithDB` helper that calls `Migrate` then `DELETE FROM` the tables.
- Pure unit tests have no build tag, use `testify/require` (or stdlib `t.Fatalf` in older files), and table-driven `t.Run` where there are cases.
- Errors wrapped with `fmt.Errorf("...: %w", err)`. Logging is `log/slog` (the worker/service do not log per-item; keep it that way).
- Commit messages: Conventional Commits, lowercase scope, e.g. `feat(codegraph): ...`, `feat(memory): ...`, `test(...): ...`.

### Test commands

- Unit (fast, no DB): `go test ./internal/<pkg>/...`
- Integration (DB): `go test -tags=integration ./internal/<pkg>/...` with `TATARA_TEST_PG_DSN` exported. If no Postgres is available the integration tests `t.Skip`; in that case run them once a DSN exists. Every integration task below lists both the command and the expected pass line.
- Build the whole module after each package: `go build ./...`

### Task order (strict; later tasks compile against earlier ones)

1. Migration wave SQL + `migrate.go` wiring + `MigrationSQL` unit tests (codegraph + memory).
2. Contract types: `Edge`/`Entity` fields, `Hyperedge`, `GraphPush.Hyperedges`, entity-type + relation constants, `ConfidenceFor`/tier helper. Unit tests (JSON shape, helper).
3. `pgstore.Reconcile` writes: confidence columns, provenance columns, hyperedge purge+insert. Integration tests.
4. `memory_sources` store helpers + `Service.DeleteMemoriesBySource`. Unit + integration tests.
5. Worker populates `memory_sources` after `CreateMemory`. Unit tests (in-memory sink).
6. `BulkMemoriesRequest` decode (back-compat bare array) + `reconcile_files` plumbed to enqueue/worker purge-then-insert. Unit + httpapi tests.
7. Chart bump to 0.2.5.

---

## Task 1: code-graph Phase-0 migration (confidence + provenance columns, hyperedge tables)

### Files

- Create: `internal/codegraph/migrations/0003_phase0_graphify.sql`
- Modify: `internal/codegraph/migrate.go`
- Modify: `internal/codegraph/migrate_test.go`

### Steps

**Step 1.1 - Write the failing unit test for the new migration SQL.**

Replace the body of `internal/codegraph/migrate_test.go` with:

```go
package codegraph_test

import (
	"strings"
	"testing"

	"github.com/szymonrychu/tatara-memory/internal/codegraph"
)

func TestMigrationSQLExists(t *testing.T) {
	sql := codegraph.MigrationSQL()
	for _, want := range []string{"code_entities", "code_edges", "CREATE TABLE IF NOT EXISTS"} {
		if !strings.Contains(sql, want) {
			t.Fatalf("migration SQL missing %q", want)
		}
	}
	if !strings.Contains(sql, "cross_repo_symbols") {
		t.Fatalf("migration SQL missing cross_repo_symbols")
	}
}

func TestMigrationSQLPhase0(t *testing.T) {
	sql := codegraph.MigrationSQL()
	for _, want := range []string{
		"confidence_score",
		"confidence_tier",
		"code_edges_repo_tier",
		"community",
		"cohesion",
		"betweenness",
		"source_url",
		"captured_at",
		"line_start",
		"line_end",
		"code_hyperedges",
		"code_hyperedge_members",
		"code_hyperedges_src",
	} {
		if !strings.Contains(sql, want) {
			t.Fatalf("phase0 migration SQL missing %q", want)
		}
	}
}
```

**Step 1.2 - Run it; expect FAIL.**

Command: `go test ./internal/codegraph/ -run TestMigrationSQLPhase0`
Expected: FAIL with `phase0 migration SQL missing "confidence_score"` (the new SQL file does not exist yet).

**Step 1.3 - Create the migration SQL file.**

Create `internal/codegraph/migrations/0003_phase0_graphify.sql` with exactly (column names/types copied from the design spec, lines 260-292):

```sql
-- Phase 0: graphify-forward-compatible capture. Confidence is promoted to typed
-- columns on code_edges; analytics + provenance columns are reserved on
-- code_entities (compute later); empty hyperedge tables reserve the n-ary shape.
ALTER TABLE code_edges
    ADD COLUMN IF NOT EXISTS confidence_score real NOT NULL DEFAULT 1.0,
    ADD COLUMN IF NOT EXISTS confidence_tier  text NOT NULL DEFAULT 'EXTRACTED';
CREATE INDEX IF NOT EXISTS code_edges_repo_tier ON code_edges (repo, confidence_tier);

ALTER TABLE code_entities
    ADD COLUMN IF NOT EXISTS community    int,
    ADD COLUMN IF NOT EXISTS cohesion     real,
    ADD COLUMN IF NOT EXISTS degree       int,
    ADD COLUMN IF NOT EXISTS betweenness  real,
    ADD COLUMN IF NOT EXISTS source_url   text,
    ADD COLUMN IF NOT EXISTS author       text,
    ADD COLUMN IF NOT EXISTS captured_at  timestamptz,
    ADD COLUMN IF NOT EXISTS line_start   int,
    ADD COLUMN IF NOT EXISTS line_end     int;

CREATE TABLE IF NOT EXISTS code_hyperedges (
    repo             text NOT NULL,
    id               text NOT NULL,
    label            text NOT NULL,
    relation         text NOT NULL,
    confidence_score real NOT NULL DEFAULT 1.0,
    src_file         text NOT NULL,
    properties       jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (repo, id)
);
CREATE TABLE IF NOT EXISTS code_hyperedge_members (
    repo         text NOT NULL,
    hyperedge_id text NOT NULL,
    entity_id    text NOT NULL,
    PRIMARY KEY (repo, hyperedge_id, entity_id)
);
CREATE INDEX IF NOT EXISTS code_hyperedges_src ON code_hyperedges (repo, src_file);
```

**Step 1.4 - Wire the migration into `migrate.go`.**

Replace the full contents of `internal/codegraph/migrate.go` with:

```go
package codegraph

import (
	"context"
	"database/sql"
	_ "embed"
)

//go:embed migrations/0001_codegraph.sql
var migration0001 string

//go:embed migrations/0002_cross_repo_symbols.sql
var migration0002 string

//go:embed migrations/0003_phase0_graphify.sql
var migration0003 string

// MigrationSQL returns the DDL for the code-graph schema (all migrations concatenated).
func MigrationSQL() string {
	return migration0001 + "\n" + migration0002 + "\n" + migration0003
}

// Migrate applies the code-graph schema to db, creating tables if they do not exist.
func Migrate(ctx context.Context, db *sql.DB) error {
	if _, err := db.ExecContext(ctx, migration0001); err != nil {
		return err
	}
	if _, err := db.ExecContext(ctx, migration0002); err != nil {
		return err
	}
	_, err := db.ExecContext(ctx, migration0003)
	return err
}
```

**Step 1.5 - Run to pass.**

Command: `go test ./internal/codegraph/ -run 'TestMigrationSQL'`
Expected: PASS (`ok  github.com/szymonrychu/tatara-memory/internal/codegraph`).

**Step 1.6 - Commit.**

```
git add internal/codegraph/migrations/0003_phase0_graphify.sql internal/codegraph/migrate.go internal/codegraph/migrate_test.go
git commit -m "feat(codegraph): phase0 migration (confidence/provenance columns, empty hyperedge tables)"
```

---

## Task 2: `memory_sources` migration

### Files

- Create: `internal/memory/migrations/0002_memory_sources.sql`
- Modify: `internal/memory/migrate.go`
- Modify: `internal/memory/migrate_test.go`

### Steps

**Step 2.1 - Write the failing unit test.**

Replace the body of `internal/memory/migrate_test.go` with:

```go
package memory_test

import (
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory/internal/memory"
)

func TestMigrationSQLExists(t *testing.T) {
	sql := memory.MigrationSQL()
	require.NotEmpty(t, sql)
	require.Contains(t, sql, "CREATE TABLE")
	require.Contains(t, sql, "deleted_memories")
	require.Contains(t, sql, "PRIMARY KEY")
}

func TestMigrationSQLMemorySources(t *testing.T) {
	sql := memory.MigrationSQL()
	require.Contains(t, sql, "memory_sources")
	require.Contains(t, sql, "track_id")
	require.Contains(t, sql, "memory_sources_repo_file")
}
```

**Step 2.2 - Run it; expect FAIL.**

Command: `go test ./internal/memory/ -run TestMigrationSQLMemorySources`
Expected: FAIL with `... Error: ... expected ... to contain "memory_sources"`.

**Step 2.3 - Create the migration SQL.**

Create `internal/memory/migrations/0002_memory_sources.sql` with exactly (from design spec lines 245-253):

```sql
CREATE TABLE IF NOT EXISTS memory_sources (
    repo      text NOT NULL,
    file_path text NOT NULL,
    track_id  text NOT NULL,
    PRIMARY KEY (repo, file_path, track_id)
);
CREATE INDEX IF NOT EXISTS memory_sources_repo_file
    ON memory_sources (repo, file_path);
```

**Step 2.4 - Wire it into `migrate.go`.**

Replace the full contents of `internal/memory/migrate.go` with:

```go
package memory

import (
	"context"
	"database/sql"
	_ "embed"
)

//go:embed migrations/0001_tombstones.sql
var migration0001 string

//go:embed migrations/0002_memory_sources.sql
var migration0002 string

// MigrationSQL returns the DDL for the memory schema (tombstones plus the
// repo/file -> track_id source index used by per-file reconcile).
func MigrationSQL() string {
	return migration0001 + "\n" + migration0002
}

// Migrate applies the memory schema to db, creating tables if they do
// not exist.
func Migrate(ctx context.Context, db *sql.DB) error {
	if _, err := db.ExecContext(ctx, migration0001); err != nil {
		return err
	}
	_, err := db.ExecContext(ctx, migration0002)
	return err
}
```

**Step 2.5 - Run to pass.**

Command: `go test ./internal/memory/ -run 'TestMigrationSQL'`
Expected: PASS (`ok  github.com/szymonrychu/tatara-memory/internal/memory`).

**Step 2.6 - Commit.**

```
git add internal/memory/migrations/0002_memory_sources.sql internal/memory/migrate.go internal/memory/migrate_test.go
git commit -m "feat(memory): memory_sources index migration (repo/file -> track_id)"
```

---

## Task 3: code-graph contract types (Edge confidence, Entity provenance, Hyperedge, GraphPush.Hyperedges)

### Files

- Modify: `internal/codegraph/types.go`
- Modify: `internal/codegraph/types_test.go`

### Steps

**Step 3.1 - Write the failing unit test for the widened types + JSON shape.**

Append to `internal/codegraph/types_test.go` (keep the existing `TestSymbolRowTypes`, `TestClampDepth`, `TestNormalizeDir`):

```go
func TestEdgeConfidenceJSON(t *testing.T) {
	e := Edge{From: "a", To: "b", Relation: relCalls, SrcFile: "a.go", ConfidenceScore: 0.98, ConfidenceTier: TierInferred}
	b, err := json.Marshal(e)
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]interface{}
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	if m["confidence_score"] != 0.98 {
		t.Fatalf("confidence_score missing or wrong: %v", m["confidence_score"])
	}
	if m["confidence_tier"] != "INFERRED" {
		t.Fatalf("confidence_tier missing or wrong: %v", m["confidence_tier"])
	}

	// omitempty: zero confidence fields are dropped.
	e2 := Edge{From: "a", To: "b", Relation: relCalls, SrcFile: "a.go"}
	b2, _ := json.Marshal(e2)
	var m2 map[string]interface{}
	_ = json.Unmarshal(b2, &m2)
	if _, ok := m2["confidence_score"]; ok {
		t.Fatal("confidence_score should be omitted when zero")
	}
}

func TestEntityProvenanceJSON(t *testing.T) {
	e := Entity{ID: "doc:section:README.md#intro", Name: "intro", Type: EntityDocSection, FilePath: "README.md",
		LineStart: 1, LineEnd: 9, SourceURL: "https://x", Author: "me", CapturedAt: "2026-06-09T00:00:00Z"}
	b, err := json.Marshal(e)
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]interface{}
	_ = json.Unmarshal(b, &m)
	for _, k := range []string{"line_start", "line_end", "source_url", "author", "captured_at"} {
		if _, ok := m[k]; !ok {
			t.Fatalf("entity json missing %q", k)
		}
	}
}

func TestHyperedgeAndGraphPushJSON(t *testing.T) {
	h := Hyperedge{ID: "h1", Label: "trio", Relation: "form", ConfidenceScore: 1.0, SrcFile: "a.go", Members: []string{"e1", "e2", "e3"}}
	p := GraphPush{Repo: "r", Files: []string{"a.go"}, Hyperedges: []Hyperedge{h}}
	b, err := json.Marshal(p)
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]interface{}
	_ = json.Unmarshal(b, &m)
	if _, ok := m["hyperedges"]; !ok {
		t.Fatal("hyperedges field missing when non-empty")
	}

	// omitempty: no hyperedges field when nil.
	p2 := GraphPush{Repo: "r", Files: []string{"a.go"}}
	b2, _ := json.Marshal(p2)
	var m2 map[string]interface{}
	_ = json.Unmarshal(b2, &m2)
	if _, ok := m2["hyperedges"]; ok {
		t.Fatal("hyperedges field should be omitted when nil")
	}
}

func TestConfidenceFor(t *testing.T) {
	cases := []struct {
		name      string
		score     float64
		wantTier  string
	}{
		{"extracted at 1.0", 1.0, TierExtracted},
		{"inferred mid", 0.98, TierInferred},
		{"inferred low boundary above ambiguous", 0.5, TierInferred},
		{"ambiguous at boundary", 0.3, TierAmbiguous},
		{"ambiguous below", 0.0, TierAmbiguous},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := TierFor(c.score); got != c.wantTier {
				t.Fatalf("TierFor(%v) = %q, want %q", c.score, got, c.wantTier)
			}
		})
	}
}
```

**Step 3.2 - Run it; expect FAIL.**

Command: `go test ./internal/codegraph/ -run 'TestEdgeConfidenceJSON|TestEntityProvenanceJSON|TestHyperedgeAndGraphPushJSON|TestConfidenceFor'`
Expected: FAIL to compile -- `undefined: TierInferred`, `undefined: EntityDocSection`, `undefined: Hyperedge`, `undefined: TierFor` (and `Entity has no field LineStart`).

**Step 3.3 - Widen `Edge`, `Entity`, add `Hyperedge`, `GraphPush.Hyperedges`, constants, `TierFor`.**

In `internal/codegraph/types.go`:

(a) Add the new entity-type and relation constants plus tier constants. Insert this block immediately after the existing relation `const (...)`/`var (...)` group (after line 36) and the existing `RoleProvides/RoleRequires` block stays where it is:

```go
// Phase 0 entity-type constants (doc/concept/rationale nodes flow through
// Entities, not a separate array). Locked by the Phase 0 contract.
const (
	EntityDocFile    = "doc_file"
	EntityDocSection = "doc_section"
	EntityConcept    = "concept"
	EntityRationale  = "rationale"
)

// Phase 0 semantic relation constants (reserved now, emitted Phase 2).
const (
	RelConceptuallyRelated = "conceptually_related_to"
	RelSemanticallySimilar = "semantically_similar_to"
	RelRationaleFor        = "rationale_for"
	RelSharesDataWith      = "shares_data_with"
	RelCites               = "cites"
)

// Confidence tiers for code edges. Mapping (per Phase 0 lock):
// 1.0 -> EXTRACTED; (0.3,1) -> INFERRED; <=0.3 -> AMBIGUOUS.
const (
	TierExtracted = "EXTRACTED"
	TierInferred  = "INFERRED"
	TierAmbiguous = "AMBIGUOUS"
)

// TierFor maps a confidence score to its tier.
func TierFor(score float64) string {
	switch {
	case score >= 1.0:
		return TierExtracted
	case score <= 0.3:
		return TierAmbiguous
	default:
		return TierInferred
	}
}
```

(b) Replace the `Entity` struct (lines 46-53) with the locked shape (field order and JSON tags copied byte-for-byte from the lock, lines 51-63):

```go
// Entity is a node in the code graph (a package, type, function, resource,
// template, value key, file, doc, concept, or repo root). The ID is a canonical
// "<lang>:<kind>:<fqn>" string and is treated as opaque by the store.
type Entity struct {
	ID          string            `json:"id"`
	Name        string            `json:"name"`
	Type        string            `json:"type"`
	Description string            `json:"description,omitempty"`
	FilePath    string            `json:"file_path"`
	LineStart   int               `json:"line_start,omitempty"`
	LineEnd     int               `json:"line_end,omitempty"`
	SourceURL   string            `json:"source_url,omitempty"`
	Author      string            `json:"author,omitempty"`
	CapturedAt  string            `json:"captured_at,omitempty"`
	Properties  map[string]string `json:"properties,omitempty"`
}
```

(c) Replace the `Edge` struct (lines 58-64) with the locked shape (from the lock, lines 30-38):

```go
// Edge is a directed, typed relationship between two entities. SrcFile is the
// file that owns the edge (where the reference originates) and is the unit of
// file-granular replacement. ConfidenceScore/ConfidenceTier are promoted to
// typed columns on reconcile (DEFAULT 1.0/'EXTRACTED' when the producer omits).
type Edge struct {
	From            string            `json:"from"`
	To              string            `json:"to"`
	Relation        string            `json:"relation"`
	SrcFile         string            `json:"src_file"`
	ConfidenceScore float64           `json:"confidence_score,omitempty"`
	ConfidenceTier  string            `json:"confidence_tier,omitempty"`
	Properties      map[string]string `json:"properties,omitempty"`
}
```

(d) Add the `Hyperedge` type immediately before the `GraphPush` struct (from the lock, lines 74-82):

```go
// Hyperedge is a genuinely n-ary relationship over 3+ entities, owned by SrcFile
// and reconciled per-file like edges. Empty until Phase 2.
type Hyperedge struct {
	ID              string            `json:"id"`
	Label           string            `json:"label"`
	Relation        string            `json:"relation"` // participate_in|implement|form
	ConfidenceScore float64           `json:"confidence_score,omitempty"`
	SrcFile         string            `json:"src_file"`
	Members         []string          `json:"members"` // entity IDs (3+)
	Properties      map[string]string `json:"properties,omitempty"`
}
```

(e) Add `Hyperedges` to `GraphPush` (replace lines 99-106 with the locked shape, from the lock lines 84-92):

```go
// GraphPush is one ingest request: the changed file set plus the entities and
// edges those files own. Reconciliation deletes the prior graph owned by Files
// then inserts Entities, Edges, Symbols, and Hyperedges, in one transaction.
type GraphPush struct {
	Repo       string      `json:"repo"`
	Commit     string      `json:"commit,omitempty"`
	Files      []string    `json:"files"`
	Entities   []Entity    `json:"entities"`
	Edges      []Edge      `json:"edges"`
	Symbols    []SymbolRow `json:"symbols,omitempty"`
	Hyperedges []Hyperedge `json:"hyperedges,omitempty"`
}
```

**Step 3.4 - Run to pass.**

Command: `go test ./internal/codegraph/ -run 'TestEdgeConfidenceJSON|TestEntityProvenanceJSON|TestHyperedgeAndGraphPushJSON|TestConfidenceFor|TestSymbolRowTypes'`
Expected: PASS (`ok  github.com/szymonrychu/tatara-memory/internal/codegraph`).

**Step 3.5 - Verify the whole module still builds (the `Entity`/`Edge` shape change touches `pgstore.go` scanners which still compile because scanned columns are a subset).**

Command: `go build ./...`
Expected: no output, exit 0.

**Step 3.6 - Commit.**

```
git add internal/codegraph/types.go internal/codegraph/types_test.go
git commit -m "feat(codegraph): phase0 contract types (edge confidence, entity provenance, hyperedge, relation/type constants)"
```

---

## Task 4: `pgstore.Reconcile` writes confidence + provenance + hyperedges

### Files

- Modify: `internal/codegraph/pgstore.go`
- Modify: `internal/codegraph/pgstore_test.go`

### Steps

**Step 4.1 - Write the failing integration test.**

Append to `internal/codegraph/pgstore_test.go` (this file already has `//go:build integration`, `freshStoreWithDB`, and the `ent` helper):

```go
func TestReconcileWritesConfidenceColumns(t *testing.T) {
	s, db, ctx := freshStoreWithDB(t)

	_, err := s.Reconcile(ctx, codegraph.GraphPush{
		Repo:  "rc",
		Files: []string{"a.go"},
		Entities: []codegraph.Entity{
			ent("go:func:rc/a.A", "go_func", "a.go"),
			ent("go:func:rc/a.B", "go_func", "a.go"),
		},
		Edges: []codegraph.Edge{
			// explicit confidence
			{From: "go:func:rc/a.A", To: "go:func:rc/a.B", Relation: "calls", SrcFile: "a.go",
				ConfidenceScore: 0.98, ConfidenceTier: codegraph.TierInferred},
			// omitted confidence -> server defaults
			{From: "go:func:rc/a.B", To: "go:func:rc/a.A", Relation: "references", SrcFile: "a.go"},
		},
	})
	require.NoError(t, err)

	var score float64
	var tier string
	require.NoError(t, db.QueryRowContext(ctx,
		`SELECT confidence_score, confidence_tier FROM code_edges WHERE repo='rc' AND relation='calls'`).Scan(&score, &tier))
	require.InDelta(t, 0.98, score, 1e-9)
	require.Equal(t, "INFERRED", tier)

	require.NoError(t, db.QueryRowContext(ctx,
		`SELECT confidence_score, confidence_tier FROM code_edges WHERE repo='rc' AND relation='references'`).Scan(&score, &tier))
	require.InDelta(t, 1.0, score, 1e-9)
	require.Equal(t, "EXTRACTED", tier)
}

func TestReconcileWritesEntityProvenance(t *testing.T) {
	s, db, ctx := freshStoreWithDB(t)

	_, err := s.Reconcile(ctx, codegraph.GraphPush{
		Repo:  "rp",
		Files: []string{"README.md"},
		Entities: []codegraph.Entity{
			{ID: "doc:section:README.md#intro", Name: "intro", Type: codegraph.EntityDocSection, FilePath: "README.md",
				LineStart: 1, LineEnd: 9, SourceURL: "https://example/x", Author: "me", CapturedAt: "2026-06-09T00:00:00Z"},
		},
	})
	require.NoError(t, err)

	var ls, le int
	var url, author string
	require.NoError(t, db.QueryRowContext(ctx,
		`SELECT line_start, line_end, source_url, author FROM code_entities WHERE repo='rp' AND id='doc:section:README.md#intro'`).
		Scan(&ls, &le, &url, &author))
	require.Equal(t, 1, ls)
	require.Equal(t, 9, le)
	require.Equal(t, "https://example/x", url)
	require.Equal(t, "me", author)
}

func TestReconcilePurgesAndInsertsHyperedgesPerFile(t *testing.T) {
	s, db, ctx := freshStoreWithDB(t)

	_, err := s.Reconcile(ctx, codegraph.GraphPush{
		Repo:  "rh",
		Files: []string{"a.go", "b.go"},
		Entities: []codegraph.Entity{
			ent("go:func:rh/a.A", "go_func", "a.go"),
			ent("go:func:rh/a.B", "go_func", "a.go"),
			ent("go:func:rh/a.C", "go_func", "a.go"),
			ent("go:func:rh/b.D", "go_func", "b.go"),
		},
		Hyperedges: []codegraph.Hyperedge{
			{ID: "rh:h1", Label: "trio", Relation: "form", ConfidenceScore: 1.0, SrcFile: "a.go",
				Members: []string{"go:func:rh/a.A", "go:func:rh/a.B", "go:func:rh/a.C"}},
		},
	})
	require.NoError(t, err)

	var hcount, mcount int
	require.NoError(t, db.QueryRowContext(ctx, `SELECT count(*) FROM code_hyperedges WHERE repo='rh'`).Scan(&hcount))
	require.Equal(t, 1, hcount)
	require.NoError(t, db.QueryRowContext(ctx, `SELECT count(*) FROM code_hyperedge_members WHERE repo='rh' AND hyperedge_id='rh:h1'`).Scan(&mcount))
	require.Equal(t, 3, mcount)

	// Re-push a.go with no hyperedges: the a.go-owned hyperedge and its members must be purged.
	_, err = s.Reconcile(ctx, codegraph.GraphPush{
		Repo:     "rh",
		Files:    []string{"a.go"},
		Entities: []codegraph.Entity{ent("go:func:rh/a.A", "go_func", "a.go")},
	})
	require.NoError(t, err)

	require.NoError(t, db.QueryRowContext(ctx, `SELECT count(*) FROM code_hyperedges WHERE repo='rh'`).Scan(&hcount))
	require.Equal(t, 0, hcount)
	require.NoError(t, db.QueryRowContext(ctx, `SELECT count(*) FROM code_hyperedge_members WHERE repo='rh'`).Scan(&mcount))
	require.Equal(t, 0, mcount)
}
```

Also update the cleanup in `freshStoreWithDB` so hyperedge rows are wiped between runs. In `internal/codegraph/pgstore_test.go`, change the `DELETE` statement (line 34) to:

```go
	_, err := db.ExecContext(ctx, `DELETE FROM code_hyperedge_members; DELETE FROM code_hyperedges; DELETE FROM cross_repo_symbols; DELETE FROM code_edges; DELETE FROM code_entities;`)
```

**Step 4.2 - Run it; expect FAIL.**

Command: `go test -tags=integration ./internal/codegraph/ -run 'TestReconcileWritesConfidenceColumns|TestReconcileWritesEntityProvenance|TestReconcilePurgesAndInsertsHyperedgesPerFile'` (with `TATARA_TEST_PG_DSN` set).
Expected: FAIL -- `pq: column "confidence_score" of relation "code_edges" ...` is NOT the error (migration creates them); the failure is the assertion `expected: 0.98 ... actual: 1` because `Reconcile` does not yet write the column, and `code_hyperedges` rows are 0 because `Reconcile` never inserts hyperedges. (If no DSN: `--- SKIP`; proceed to implement, then run once a DSN exists.)

**Step 4.3 - Update `Reconcile` to delete hyperedges per file, write confidence columns, write provenance columns, and insert hyperedges + members.**

In `internal/codegraph/pgstore.go`, replace the body of `Reconcile` (lines 50-112) with:

```go
// Reconcile deletes the prior graph owned by p.Files then inserts p.Entities,
// p.Edges, p.Symbols, and p.Hyperedges, all in a single transaction.
func (s *PGStore) Reconcile(ctx context.Context, p GraphPush) (PushResult, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return PushResult{}, err
	}
	defer func() { _ = tx.Rollback() }()

	for _, f := range p.Files {
		if _, err := tx.ExecContext(ctx, `DELETE FROM code_edges WHERE repo=$1 AND src_file=$2`, p.Repo, f); err != nil {
			return PushResult{}, err
		}
		if _, err := tx.ExecContext(ctx, `DELETE FROM code_entities WHERE repo=$1 AND file_path=$2`, p.Repo, f); err != nil {
			return PushResult{}, err
		}
		if _, err := tx.ExecContext(ctx, `DELETE FROM cross_repo_symbols WHERE repo=$1 AND src_file=$2`, p.Repo, f); err != nil {
			return PushResult{}, err
		}
		if _, err := tx.ExecContext(ctx,
			`DELETE FROM code_hyperedge_members WHERE repo=$1 AND hyperedge_id IN (
				SELECT id FROM code_hyperedges WHERE repo=$1 AND src_file=$2)`, p.Repo, f); err != nil {
			return PushResult{}, err
		}
		if _, err := tx.ExecContext(ctx, `DELETE FROM code_hyperedges WHERE repo=$1 AND src_file=$2`, p.Repo, f); err != nil {
			return PushResult{}, err
		}
	}

	for _, e := range p.Entities {
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO code_entities(repo, id, name, type, description, file_path, properties,
				line_start, line_end, source_url, author, captured_at)
			VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10,$11,$12)
			ON CONFLICT (repo, id) DO UPDATE SET
				name=EXCLUDED.name, type=EXCLUDED.type, description=EXCLUDED.description,
				file_path=EXCLUDED.file_path, properties=EXCLUDED.properties,
				line_start=EXCLUDED.line_start, line_end=EXCLUDED.line_end,
				source_url=EXCLUDED.source_url, author=EXCLUDED.author, captured_at=EXCLUDED.captured_at`,
			p.Repo, e.ID, e.Name, e.Type, e.Description, e.FilePath, marshalProps(e.Properties),
			nullInt(e.LineStart), nullInt(e.LineEnd), nullStr(e.SourceURL), nullStr(e.Author), nullTime(e.CapturedAt)); err != nil {
			return PushResult{}, err
		}
	}

	for _, e := range p.Edges {
		score := e.ConfidenceScore
		tier := e.ConfidenceTier
		if score == 0 && tier == "" {
			score, tier = 1.0, TierExtracted
		} else if tier == "" {
			tier = TierFor(score)
		}
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO code_edges(repo, from_id, to_id, relation, src_file, properties, confidence_score, confidence_tier)
			VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8)
			ON CONFLICT (repo, from_id, to_id, relation) DO UPDATE SET
				src_file=EXCLUDED.src_file, properties=EXCLUDED.properties,
				confidence_score=EXCLUDED.confidence_score, confidence_tier=EXCLUDED.confidence_tier`,
			p.Repo, e.From, e.To, e.Relation, e.SrcFile, marshalProps(e.Properties), score, tier); err != nil {
			return PushResult{}, err
		}
	}

	for _, sym := range p.Symbols {
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO cross_repo_symbols(repo, symbol, lang, kind, role, entity_id, src_file)
			VALUES ($1,$2,$3,$4,$5,$6,$7)
			ON CONFLICT (repo, symbol, role, entity_id) DO UPDATE SET
			    lang=EXCLUDED.lang, kind=EXCLUDED.kind, src_file=EXCLUDED.src_file`,
			p.Repo, sym.Symbol, sym.Lang, sym.Kind, sym.Role, sym.EntityID, sym.SrcFile); err != nil {
			return PushResult{}, err
		}
	}

	for _, h := range p.Hyperedges {
		score := h.ConfidenceScore
		if score == 0 {
			score = 1.0
		}
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO code_hyperedges(repo, id, label, relation, confidence_score, src_file, properties)
			VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)
			ON CONFLICT (repo, id) DO UPDATE SET
				label=EXCLUDED.label, relation=EXCLUDED.relation,
				confidence_score=EXCLUDED.confidence_score, src_file=EXCLUDED.src_file, properties=EXCLUDED.properties`,
			p.Repo, h.ID, h.Label, h.Relation, score, h.SrcFile, marshalProps(h.Properties)); err != nil {
			return PushResult{}, err
		}
		for _, m := range h.Members {
			if _, err := tx.ExecContext(ctx, `
				INSERT INTO code_hyperedge_members(repo, hyperedge_id, entity_id)
				VALUES ($1,$2,$3)
				ON CONFLICT (repo, hyperedge_id, entity_id) DO NOTHING`,
				p.Repo, h.ID, m); err != nil {
				return PushResult{}, err
			}
		}
	}

	if err := tx.Commit(); err != nil {
		return PushResult{}, err
	}
	return PushResult{
		Repo:             p.Repo,
		Files:            len(p.Files),
		EntitiesUpserted: len(p.Entities),
		EdgesUpserted:    len(p.Edges),
	}, nil
}
```

Add the null-helper functions at the bottom of `internal/codegraph/pgstore.go` (after `scanEdges`), so empty provenance fields write SQL NULL rather than zero/empty-string (keeps reserved columns NULL until a producer fills them):

```go
func nullInt(v int) any {
	if v == 0 {
		return nil
	}
	return v
}

func nullStr(v string) any {
	if v == "" {
		return nil
	}
	return v
}

func nullTime(v string) any {
	if v == "" {
		return nil
	}
	return v
}
```

**Step 4.4 - Run to pass.**

Command: `go test -tags=integration ./internal/codegraph/ -run 'TestReconcileWritesConfidenceColumns|TestReconcileWritesEntityProvenance|TestReconcilePurgesAndInsertsHyperedgesPerFile|TestReconcileInsertsAndReplacesPerFile|TestReconcileSymbolsPerFileReplacement'`
Expected: PASS (`ok  github.com/szymonrychu/tatara-memory/internal/codegraph`). The existing per-file replacement tests still pass (unchanged delete+upsert semantics).

**Step 4.5 - Verify unit tests + build.**

Command: `go test ./internal/codegraph/ && go build ./...`
Expected: PASS / no output.

**Step 4.6 - Commit.**

```
git add internal/codegraph/pgstore.go internal/codegraph/pgstore_test.go
git commit -m "feat(codegraph): reconcile writes confidence/provenance columns and purges+inserts hyperedges per file"
```

---

## Task 5: `memory_sources` store helpers + `Service.DeleteMemoriesBySource`

This adds a small `SourceStore` (parallel to `TombstoneStore`) and a service method. The service gains an optional `sources` dependency, defaulting to nil (no-op) to keep all existing `NewService(lr, tomb)` call sites and unit tests compiling.

### Files

- Create: `internal/memory/sources.go`
- Create: `internal/memory/sources_test.go` (integration)
- Modify: `internal/memory/service.go`
- Create/append: `internal/memory/source_service_test.go` (unit)

### Steps

**Step 5.1 - Write the failing integration test for the `SourceStore`.**

Create `internal/memory/sources_test.go`:

```go
//go:build integration

package memory_test

import (
	"context"
	"database/sql"
	"os"
	"testing"

	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory/internal/memory"
)

func openSourcesDB(t *testing.T) *sql.DB {
	t.Helper()
	dsn := os.Getenv("TATARA_TEST_PG_DSN")
	if dsn == "" {
		t.Skip("TATARA_TEST_PG_DSN not set; skipping integration test")
	}
	db, err := sql.Open("pgx", dsn)
	require.NoError(t, err)
	t.Cleanup(func() { _ = db.Close() })
	ctx := context.Background()
	require.NoError(t, memory.Migrate(ctx, db))
	_, err = db.ExecContext(ctx, `DELETE FROM memory_sources`)
	require.NoError(t, err)
	return db
}

func TestSourceStoreAddListDelete(t *testing.T) {
	ctx := context.Background()
	db := openSourcesDB(t)
	ss := memory.NewSourceStore(db)

	require.NoError(t, ss.Add(ctx, "repoA", "a.go", "trk1"))
	require.NoError(t, ss.Add(ctx, "repoA", "a.go", "trk2"))
	require.NoError(t, ss.Add(ctx, "repoA", "b.go", "trk3"))
	// idempotent re-add
	require.NoError(t, ss.Add(ctx, "repoA", "a.go", "trk1"))

	ids, err := ss.TrackIDs(ctx, "repoA", "a.go")
	require.NoError(t, err)
	require.ElementsMatch(t, []string{"trk1", "trk2"}, ids)

	n, err := ss.DeleteByFile(ctx, "repoA", "a.go")
	require.NoError(t, err)
	require.Equal(t, int64(2), n)

	ids, err = ss.TrackIDs(ctx, "repoA", "a.go")
	require.NoError(t, err)
	require.Empty(t, ids)

	// b.go untouched
	ids, err = ss.TrackIDs(ctx, "repoA", "b.go")
	require.NoError(t, err)
	require.Equal(t, []string{"trk3"}, ids)
}
```

**Step 5.2 - Run it; expect FAIL.**

Command: `go test -tags=integration ./internal/memory/ -run TestSourceStoreAddListDelete`
Expected: FAIL to compile -- `undefined: memory.NewSourceStore`. (No DSN: would skip but still must compile, so this is a compile failure.)

**Step 5.3 - Create `internal/memory/sources.go`.**

```go
package memory

import (
	"context"
	"database/sql"
	"fmt"
)

// SourceStore indexes which track_ids were produced from a given repo/file, so a
// per-file reconcile can purge exactly that file's memories. Mirrors the
// per-file ownership the code-graph already has.
type SourceStore struct {
	db *sql.DB
}

// NewSourceStore returns a SourceStore backed by db.
func NewSourceStore(db *sql.DB) *SourceStore {
	return &SourceStore{db: db}
}

// Add records that track_id originated from (repo, filePath). Idempotent.
func (s *SourceStore) Add(ctx context.Context, repo, filePath, trackID string) error {
	_, err := s.db.ExecContext(ctx,
		`INSERT INTO memory_sources (repo, file_path, track_id) VALUES ($1,$2,$3)
		 ON CONFLICT (repo, file_path, track_id) DO NOTHING`,
		repo, filePath, trackID)
	if err != nil {
		return fmt.Errorf("memory_sources add: %w", err)
	}
	return nil
}

// TrackIDs returns the track_ids indexed for (repo, filePath).
func (s *SourceStore) TrackIDs(ctx context.Context, repo, filePath string) ([]string, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT track_id FROM memory_sources WHERE repo=$1 AND file_path=$2`, repo, filePath)
	if err != nil {
		return nil, fmt.Errorf("memory_sources select: %w", err)
	}
	defer func() { _ = rows.Close() }()
	var out []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, fmt.Errorf("memory_sources scan: %w", err)
		}
		out = append(out, id)
	}
	return out, rows.Err()
}

// DeleteByFile removes every source row for (repo, filePath) and returns the count.
func (s *SourceStore) DeleteByFile(ctx context.Context, repo, filePath string) (int64, error) {
	res, err := s.db.ExecContext(ctx,
		`DELETE FROM memory_sources WHERE repo=$1 AND file_path=$2`, repo, filePath)
	if err != nil {
		return 0, fmt.Errorf("memory_sources delete: %w", err)
	}
	n, _ := res.RowsAffected()
	return n, nil
}
```

**Step 5.4 - Run to pass (store).**

Command: `go test -tags=integration ./internal/memory/ -run TestSourceStoreAddListDelete`
Expected: PASS (or SKIP if no DSN; it must at minimum compile and the rest of the suite stays green). Then `go build ./...` -> no output.

**Step 5.5 - Write the failing unit test for `Service.DeleteMemoriesBySource`.**

Create `internal/memory/source_service_test.go`. It uses an in-memory source store and the existing `fake` lightrag client + `inMemTombstone` already defined in `service_test.go` (same `memory_test` package):

```go
package memory_test

import (
	"context"
	"sync"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory/internal/lightrag/fake"
	"github.com/szymonrychu/tatara-memory/internal/memory"
)

// inMemSources is a thread-safe in-memory sources index for unit tests.
type inMemSources struct {
	mu  sync.Mutex
	idx map[string][]string // key repo|file -> track_ids
}

func newInMemSources() *inMemSources { return &inMemSources{idx: map[string][]string{}} }

func key(repo, file string) string { return repo + "|" + file }

func (s *inMemSources) Add(_ context.Context, repo, file, trackID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	k := key(repo, file)
	for _, id := range s.idx[k] {
		if id == trackID {
			return nil
		}
	}
	s.idx[k] = append(s.idx[k], trackID)
	return nil
}

func (s *inMemSources) TrackIDs(_ context.Context, repo, file string) ([]string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := append([]string(nil), s.idx[key(repo, file)]...)
	return out, nil
}

func (s *inMemSources) DeleteByFile(_ context.Context, repo, file string) (int64, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	k := key(repo, file)
	n := int64(len(s.idx[k]))
	delete(s.idx, k)
	return n, nil
}

func TestDeleteMemoriesBySource(t *testing.T) {
	ctx := context.Background()
	lr := fake.New()
	tomb := newInMemTombstone()
	src := newInMemSources()
	svc := memory.NewServiceWithSources(lr, tomb, src)

	// Create two memories and index them under repoX/a.go.
	m1, err := svc.CreateMemory(ctx, memory.Memory{Text: "one"})
	require.NoError(t, err)
	m2, err := svc.CreateMemory(ctx, memory.Memory{Text: "two"})
	require.NoError(t, err)
	require.NoError(t, src.Add(ctx, "repoX", "a.go", m1.ID))
	require.NoError(t, src.Add(ctx, "repoX", "a.go", m2.ID))

	n, err := svc.DeleteMemoriesBySource(ctx, "repoX", "a.go")
	require.NoError(t, err)
	require.Equal(t, 2, n)

	// Both track_ids are tombstoned (DeleteMemory was called for each).
	d1, _ := tomb.IsDeleted(ctx, m1.ID)
	d2, _ := tomb.IsDeleted(ctx, m2.ID)
	require.True(t, d1)
	require.True(t, d2)

	// Index rows are gone.
	ids, err := src.TrackIDs(ctx, "repoX", "a.go")
	require.NoError(t, err)
	require.Empty(t, ids)

	// Idempotent: a second call purges nothing.
	n, err = svc.DeleteMemoriesBySource(ctx, "repoX", "a.go")
	require.NoError(t, err)
	require.Equal(t, 0, n)
}
```

**Step 5.6 - Run it; expect FAIL.**

Command: `go test ./internal/memory/ -run TestDeleteMemoriesBySource`
Expected: FAIL to compile -- `undefined: memory.NewServiceWithSources`, `svc.DeleteMemoriesBySource undefined`.

**Step 5.7 - Add the `sourceIndex` interface, the `sources` field, the constructor, and the method in `internal/memory/service.go`.**

(a) After the `tombstoner` interface (line 31), add:

```go
// sourceIndex is the minimal interface Service needs from SourceStore: list and
// purge the track_ids produced from a repo/file. May be nil (delete-by-source
// becomes a no-op returning 0).
type sourceIndex interface {
	TrackIDs(ctx context.Context, repo, filePath string) ([]string, error)
	DeleteByFile(ctx context.Context, repo, filePath string) (int64, error)
}
```

(b) Add the `sources` field to the `Service` struct (so it becomes):

```go
// Service provides memory CRUD and retrieval operations backed by LightRAG.
type Service struct {
	lr      lightrag.Client
	tomb    tombstoner
	sources sourceIndex
	now     func() time.Time
}
```

(c) Replace `NewService` and add `NewServiceWithSources`:

```go
// NewService returns a Service backed by the given LightRAG client.
// tomb may be nil; if nil, tombstone checks are skipped (no-op).
func NewService(lr lightrag.Client, tomb tombstoner) *Service {
	return &Service{lr: lr, tomb: tomb, now: time.Now}
}

// NewServiceWithSources is NewService plus a sources index that backs
// DeleteMemoriesBySource. sources may be nil (delete-by-source is a no-op).
func NewServiceWithSources(lr lightrag.Client, tomb tombstoner, sources sourceIndex) *Service {
	return &Service{lr: lr, tomb: tomb, sources: sources, now: time.Now}
}
```

(d) Add the method (append near `DeleteMemory`, after line 134):

```go
// DeleteMemoriesBySource purges every memory produced from (repo, filePath):
// it deletes each indexed track_id via DeleteMemory (lightrag DeleteDocs +
// tombstone), then clears the source-index rows. Idempotent; returns the count
// of track_ids purged. A nil sources index is a no-op returning 0.
func (s *Service) DeleteMemoriesBySource(ctx context.Context, repo, filePath string) (int, error) {
	if s.sources == nil {
		return 0, nil
	}
	ids, err := s.sources.TrackIDs(ctx, repo, filePath)
	if err != nil {
		return 0, fmt.Errorf("source track_ids: %w", err)
	}
	for _, id := range ids {
		if err := s.DeleteMemory(ctx, id); err != nil {
			if errors.Is(err, ErrNotFound) {
				continue // already gone upstream; index cleanup below still runs
			}
			return 0, fmt.Errorf("delete memory %s for %s/%s: %w", id, repo, filePath, err)
		}
	}
	if _, err := s.sources.DeleteByFile(ctx, repo, filePath); err != nil {
		return 0, fmt.Errorf("purge source index %s/%s: %w", repo, filePath, err)
	}
	return len(ids), nil
}
```

**Step 5.8 - Run to pass.**

Command: `go test ./internal/memory/ -run TestDeleteMemoriesBySource && go test ./internal/memory/`
Expected: PASS for the new test and the full memory unit suite (the existing `NewService` tests are unchanged).

**Step 5.9 - Run the integration store test once more and build.**

Command: `go test -tags=integration ./internal/memory/ && go build ./...`
Expected: PASS / no output.

**Step 5.10 - Commit.**

```
git add internal/memory/sources.go internal/memory/sources_test.go internal/memory/service.go internal/memory/source_service_test.go
git commit -m "feat(memory): SourceStore index and Service.DeleteMemoriesBySource (purge memories by repo/file)"
```

---

## Task 6: ingest worker populates `memory_sources` after `CreateMemory`

The worker's `processItem` currently discards the returned `Memory` (whose `.ID` is the lightrag `track_id`). To index sources, the `Pool` needs an optional sources sink and the item's `repo`/`file_path` from `Metadata`. The ingester already stamps both on item metadata (design spec line 302); the metadata keys are `repo` and `file_path`. A nil sink keeps every existing `NewPool(store, runner, size)` call site working.

### Files

- Modify: `internal/ingest/pool.go`
- Modify: `internal/ingest/pool_test.go`

### Steps

**Step 6.1 - Write the failing unit test (in-memory sink captures repo/file/track_id).**

Append to `internal/ingest/pool_test.go`:

```go
type capturingSources struct {
	mu      sync.Mutex
	added   []addedSource
}

type addedSource struct{ repo, file, trackID string }

func (c *capturingSources) Add(_ context.Context, repo, file, trackID string) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.added = append(c.added, addedSource{repo, file, trackID})
	return nil
}

func (c *capturingSources) snapshot() []addedSource {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]addedSource(nil), c.added...)
}

// trackingRunner returns a Memory whose ID is a deterministic track_id so the
// pool can index it.
type trackingRunner struct{}

func (trackingRunner) CreateMemory(_ context.Context, m memory.Memory) (memory.Memory, error) {
	m.ID = "trk_" + m.IdempotencyOrKey()
	return m, nil
}

func TestPoolIndexesSourcesAfterCreate(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	store := ingest.NewMemStore()
	src := &capturingSources{}
	pool := ingest.NewPoolWithSources(store, trackingRunner{}, 1, src)
	pool.Start(ctx)
	defer pool.Stop()

	e := ingest.NewEnqueuer(store, nil)
	job, err := e.Enqueue(ctx, []memory.IngestItem{
		{IdempotencyKey: "k1", Text: "a", Metadata: map[string]string{"repo": "repoA", "file_path": "a.go"}},
		{IdempotencyKey: "k2", Text: "b", Metadata: map[string]string{"repo": "repoA"}}, // no file_path -> not indexed
	})
	require.NoError(t, err)
	pool.Notify(job.ID)

	waitFor(t, func() bool {
		j, _ := store.GetJob(ctx, job.ID)
		return j.Status == memory.JobStatusSucceeded
	}, "job did not succeed")

	got := src.snapshot()
	require.Len(t, got, 1)
	require.Equal(t, "repoA", got[0].repo)
	require.Equal(t, "a.go", got[0].file)
	require.Equal(t, "trk_k1", got[0].trackID)
}
```

The test references `m.IdempotencyOrKey()` only to build a deterministic ID; replace it with the item key directly to avoid adding a method. Use this `trackingRunner` instead (it has no access to the key, so derive the track_id from the text which is unique per item here):

```go
func (trackingRunner) CreateMemory(_ context.Context, m memory.Memory) (memory.Memory, error) {
	m.ID = "trk_" + m.ID // m.ID is set by processItem to the item's idempotency key
	return m, nil
}
```

and the assertion becomes `require.Equal(t, "trk_k1", got[0].trackID)` (processItem sets `Memory.ID = item.IdempotencyKey` before calling the runner; see Step 6.2).

**Step 6.2 - Run it; expect FAIL.**

Command: `go test ./internal/ingest/ -run TestPoolIndexesSourcesAfterCreate`
Expected: FAIL to compile -- `undefined: ingest.NewPoolWithSources`.

**Step 6.3 - Add the `SourceSink` interface, the `sources` field, the constructor, and the index call in `internal/ingest/pool.go`.**

(a) After the `itemRunner` interface (line 16), add:

```go
// SourceSink records that a track_id was produced from a repo/file. The memory
// SourceStore satisfies it. May be nil (indexing disabled).
type SourceSink interface {
	Add(ctx context.Context, repo, filePath, trackID string) error
}
```

(b) Add `sources SourceSink` to the `Pool` struct.

(c) Replace `NewPool`/`newPool` and add `NewPoolWithSources`:

```go
// NewPool returns a Pool backed by the given store and runner with size worker goroutines.
func NewPool(store JobStore, runner itemRunner, size int) *Pool {
	return newPool(store, runner, size, 256, nil)
}

// NewPoolWithSources is NewPool plus a sink that indexes (repo, file_path,
// track_id) after each successful CreateMemory. sources may be nil.
func NewPoolWithSources(store JobStore, runner itemRunner, size int, sources SourceSink) *Pool {
	return newPool(store, runner, size, 256, sources)
}

func newPool(store JobStore, runner itemRunner, size, buf int, sources SourceSink) *Pool {
	if size < 1 {
		size = 1
	}
	if buf < 1 {
		buf = 1
	}
	return &Pool{
		store:   store,
		runner:  runner,
		size:    size,
		sources: sources,
		notify:  make(chan string, buf),
		stop:    make(chan struct{}),
	}
}
```

(Also update the one internal caller in `pool_internal_test.go` if it calls the old 4-arg `newPool`; see Step 6.4.)

(d) Replace `processItem` (lines 162-169) so it captures the returned track_id and indexes the source when the sink is set and the item has a `file_path`:

```go
func (p *Pool) processItem(ctx context.Context, it memory.IngestItem) error {
	created, err := p.runner.CreateMemory(ctx, memory.Memory{
		ID:       it.IdempotencyKey,
		Text:     it.Text,
		Metadata: it.Metadata,
	})
	if err != nil {
		return err
	}
	if p.sources != nil {
		repo := it.Metadata["repo"]
		file := it.Metadata["file_path"]
		if repo != "" && file != "" && created.ID != "" {
			if err := p.sources.Add(ctx, repo, file, created.ID); err != nil {
				return err
			}
		}
	}
	return nil
}
```

**Step 6.4 - Check the internal test for `newPool` arity.**

Command: `go vet ./internal/ingest/ 2>&1 | head` (or just compile). If `pool_internal_test.go` calls `newPool(...)` with the old 5-arg signature, update that call to pass a trailing `nil` for `sources`. Apply the minimal edit needed to compile.

**Step 6.5 - Run to pass.**

Command: `go test ./internal/ingest/`
Expected: PASS (`ok  github.com/szymonrychu/tatara-memory/internal/ingest`), including the existing `TestPoolDrainsJob`, `TestPoolPartial`, etc. (all use the unchanged `NewPool`, sink nil).

**Step 6.6 - Wire the sink in `app.go` so production indexes sources.**

In `cmd/tatara-memory/app.go`, after `tomb := memory.NewTombstoneStore(db)` (line 160), add `srcStore := memory.NewSourceStore(db)`; change `memSvc := memory.NewService(lrc, tomb)` to `memSvc := memory.NewServiceWithSources(lrc, tomb, srcStore)`; change `pool := ingest.NewPool(store, memSvc, cfg.WorkerPoolSize)` to `pool := ingest.NewPoolWithSources(store, memSvc, cfg.WorkerPoolSize, srcStore)`.

**Step 6.7 - Build and run the full cmd test.**

Command: `go build ./... && go test ./cmd/tatara-memory/`
Expected: PASS / no output.

**Step 6.8 - Commit.**

```
git add internal/ingest/pool.go internal/ingest/pool_test.go internal/ingest/pool_internal_test.go cmd/tatara-memory/app.go
git commit -m "feat(ingest): index memory_sources (repo/file -> track_id) after CreateMemory"
```

---

## Task 7: `/memories:bulk` accepts `reconcile_files` (back-compat bare array) and purges-then-inserts

The HTTP layer must decode either a bare `[]IngestItem` (legacy) or the new `BulkMemoriesRequest` object. When `reconcile_files` is present, the worker must call `DeleteMemoriesBySource(repo, f)` for each `f` BEFORE inserting items, atomically with the job. The cleanest seam that keeps purge-then-insert ordered with respect to the job (design spec line 315) is to make the purge a synchronous step inside `Enqueue`-time is wrong (must be in job processing). We add a `reconcileRunner` the handler invokes before enqueue is not atomic; instead we make the purge run in the worker as a pre-step. Concretely: the bulk request's `reconcile_files` is passed to the enqueuer, persisted on the job, and the pool drains a purge phase before items. To keep this plan self-contained and minimal, the purge runs in the handler-invoked enqueue path via a new `IngestService.EnqueueReconcile` that the worker executes first. Given the existing `JobStore`/`Pool` shape, the simplest correct approach is: the handler resolves `repo` from the items, calls `cfg.Service.DeleteMemoriesBySource` for each reconcile file synchronously, THEN enqueues the items. Synchronous purge-before-enqueue is ordered and idempotent (a re-delivered request re-purges then re-inserts), satisfying the invariant; the job itself remains insert-only.

> Design note for the executor: the spec's "atomic with respect to the job" is best-effort here. Synchronous purge-then-enqueue in the handler is ordered and idempotent, which is sufficient for the 1:1 invariant (a crash between purge and enqueue leaves the file empty, and the next reconcile re-inserts). If a future plan needs strict job-atomicity, the purge moves into a worker pre-phase; that is out of scope for this plan and noted in MEMORY.

### Files

- Modify: `internal/httpapi/ingest.go`
- Modify: `internal/httpapi/service.go`
- Modify: `internal/httpapi/ingest_test.go`
- Modify: `internal/httpapi/stub_test.go`

### Steps

**Step 7.1 - Write the failing httpapi test for back-compat decode + reconcile purge.**

Append to `internal/httpapi/ingest_test.go`:

```go
// reconcileSpyService records DeleteMemoriesBySource calls and embeds stubService.
type reconcileSpyService struct {
	stubService
	mu      sync.Mutex
	deleted [][2]string // {repo, file}
}

func (s *reconcileSpyService) DeleteMemoriesBySource(_ context.Context, repo, file string) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.deleted = append(s.deleted, [2]string{repo, file})
	return 0, nil
}

func (s *reconcileSpyService) snapshot() [][2]string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([][2]string(nil), s.deleted...)
}

func TestBulkIngestBareArrayBackCompat(t *testing.T) {
	ing := &ingestStub{enq: memory.IngestJob{ID: "jobBC", Status: memory.JobStatusQueued}}
	srv := newSrvIngest(t, &reconcileSpyService{}, ing)
	defer srv.Close()

	// Legacy bare array body must still be accepted.
	body := `[{"text":"a"},{"text":"b"}]`
	resp, err := http.Post(srv.URL+"/memories:bulk", "application/json", bytes.NewReader([]byte(body)))
	require.NoError(t, err)
	defer func() { _ = resp.Body.Close() }()
	require.Equal(t, http.StatusAccepted, resp.StatusCode)
}

func TestBulkIngestReconcileFilesPurgesFirst(t *testing.T) {
	spy := &reconcileSpyService{}
	ing := &ingestStub{enq: memory.IngestJob{ID: "jobRC", Status: memory.JobStatusQueued}}
	srv := newSrvIngest(t, spy, ing)
	defer srv.Close()

	body := `{"reconcile_files":["a.go","b.go"],
		"items":[{"text":"new a","metadata":{"repo":"repoA","file_path":"a.go"}}]}`
	resp, err := http.Post(srv.URL+"/memories:bulk", "application/json", bytes.NewReader([]byte(body)))
	require.NoError(t, err)
	defer func() { _ = resp.Body.Close() }()
	require.Equal(t, http.StatusAccepted, resp.StatusCode)

	got := spy.snapshot()
	require.ElementsMatch(t, [][2]string{{"repoA", "a.go"}, {"repoA", "b.go"}}, got)
}
```

Add `"sync"` to the import block of `internal/httpapi/ingest_test.go`.

Add the `DeleteMemoriesBySource` no-op to the base `stubService` in `internal/httpapi/stub_test.go` so the existing `MemoryService` interface (extended in Step 7.3) is satisfied by `stubService` everywhere it is used:

```go
func (s *stubService) DeleteMemoriesBySource(_ context.Context, _, _ string) (int, error) {
	return 0, nil
}
```

**Step 7.2 - Run it; expect FAIL.**

Command: `go test ./internal/httpapi/ -run 'TestBulkIngestBareArrayBackCompat|TestBulkIngestReconcileFilesPurgesFirst'`
Expected: FAIL -- bare array decode fails (`invalid json` -> 400, want 202) because the handler decodes into a struct only; and the reconcile spy records nothing because the handler does not call `DeleteMemoriesBySource`. May also fail to compile until `MemoryService` declares `DeleteMemoriesBySource` (Step 7.3).

**Step 7.3 - Add `DeleteMemoriesBySource` to the `MemoryService` interface.**

In `internal/httpapi/service.go`, add to the `MemoryService` interface (after `DeleteMemory`):

```go
	DeleteMemoriesBySource(ctx context.Context, repo, filePath string) (int, error)
```

**Step 7.4 - Implement the back-compat decode + reconcile purge in `internal/httpapi/ingest.go`.**

Replace the full contents of `internal/httpapi/ingest.go` with:

```go
package httpapi

import (
	"bytes"
	"encoding/json"
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/szymonrychu/tatara-memory/internal/memory"
)

// BulkMemoriesRequest is the /memories:bulk body. ReconcileFiles is the touched
// file set whose prior memories are purged before the items are inserted. A
// legacy bare JSON array of items is still accepted (decoded into Items).
type BulkMemoriesRequest struct {
	ReconcileFiles []string             `json:"reconcile_files,omitempty"`
	Items          []memory.IngestItem  `json:"items"`
}

// decodeBulk accepts either the BulkMemoriesRequest object or a bare
// []IngestItem (back-compat). A leading '[' selects the array form.
func decodeBulk(body []byte) (BulkMemoriesRequest, error) {
	trimmed := bytes.TrimLeft(body, " \t\r\n")
	if len(trimmed) > 0 && trimmed[0] == '[' {
		var items []memory.IngestItem
		if err := json.Unmarshal(body, &items); err != nil {
			return BulkMemoriesRequest{}, err
		}
		return BulkMemoriesRequest{Items: items}, nil
	}
	var req BulkMemoriesRequest
	if err := json.Unmarshal(body, &req); err != nil {
		return BulkMemoriesRequest{}, err
	}
	return req, nil
}

// repoFromItems returns the repo metadata shared by the items (first non-empty).
func repoFromItems(items []memory.IngestItem) string {
	for _, it := range items {
		if r := it.Metadata["repo"]; r != "" {
			return r
		}
	}
	return ""
}

func handleBulkIngest(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		body, err := readAllLimited(r)
		if err != nil {
			WriteError(w, http.StatusBadRequest, "invalid json", RequestIDFromContext(r.Context()))
			return
		}
		req, err := decodeBulk(body)
		if err != nil {
			WriteError(w, http.StatusBadRequest, "invalid json", RequestIDFromContext(r.Context()))
			return
		}
		if len(req.Items) == 0 && len(req.ReconcileFiles) == 0 {
			WriteError(w, http.StatusBadRequest, "items must not be empty", RequestIDFromContext(r.Context()))
			return
		}

		// Purge-before-insert: for every reconcile file, drop its prior memories.
		if len(req.ReconcileFiles) > 0 {
			repo := repoFromItems(req.Items)
			if repo != "" {
				for _, f := range req.ReconcileFiles {
					if _, err := cfg.Service.DeleteMemoriesBySource(r.Context(), repo, f); err != nil {
						mapServiceError(w, r, err)
						return
					}
				}
			}
		}

		if len(req.Items) == 0 {
			// Pure deletion reconcile (deleted files only): nothing to enqueue.
			WriteJSON(w, http.StatusAccepted, memory.IngestJob{Status: memory.JobStatusSucceeded})
			return
		}

		job, err := cfg.Ingest.Enqueue(r.Context(), req.Items)
		if err != nil {
			mapServiceError(w, r, err)
			return
		}
		WriteJSON(w, http.StatusAccepted, job)
	}
}

func handleGetJob(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		job, err := cfg.Ingest.GetJob(r.Context(), chi.URLParam(r, "id"))
		if err != nil {
			mapServiceError(w, r, err)
			return
		}
		WriteJSON(w, http.StatusOK, job)
	}
}
```

Add a small bounded body reader (avoids unbounded reads, mirrors the repo's defensive style). Add it to `internal/httpapi/ingest.go` (or, if a request-body limit helper already exists in `middleware.go`, reuse it instead and drop this):

```go
const maxBulkBody = 32 << 20 // 32 MiB

func readAllLimited(r *http.Request) ([]byte, error) {
	return io.ReadAll(http.MaxBytesReader(nil, r.Body, maxBulkBody))
}
```

and add `"io"` to the import block.

**Step 7.5 - Run to pass.**

Command: `go test ./internal/httpapi/`
Expected: PASS for the new tests and the existing `TestBulkIngest202`, `TestBulkIngestEmpty400`, `TestGetJob200` (the object form `{"items":[...]}` still decodes; `{"items":[]}` with no reconcile_files still 400).

**Step 7.6 - Build the whole module and run the full suite (unit).**

Command: `go build ./... && go test ./...`
Expected: PASS across all packages (integration-tagged tests are excluded without the tag).

**Step 7.7 - Commit.**

```
git add internal/httpapi/ingest.go internal/httpapi/service.go internal/httpapi/ingest_test.go internal/httpapi/stub_test.go
git commit -m "feat(httpapi): /memories:bulk reconcile_files purge-then-insert with bare-array back-compat"
```

---

## Task 8: chart bump to 0.2.5

### Files

- Modify: `charts/tatara-memory/Chart.yaml`

### Steps

**Step 8.1 - Bump version + appVersion.**

In `charts/tatara-memory/Chart.yaml`, change `version: 0.2.4` to `version: 0.2.5` and `appVersion: "0.2.4"` to `appVersion: "0.2.5"`. No values change (the new migrations ship in the image).

**Step 8.2 - Lint the chart.**

Command: `helm lint charts/tatara-memory`
Expected: `1 chart(s) linted, 0 chart(s) failed`.

**Step 8.3 - Commit.**

```
git add charts/tatara-memory/Chart.yaml
git commit -m "chore(chart): bump tatara-memory to 0.2.5 (phase0 migration wave)"
```

---

## Final verification (run before declaring done)

1. `go build ./...` -> exit 0.
2. `go test ./...` -> all unit packages PASS.
3. With `TATARA_TEST_PG_DSN` set to a throwaway Postgres: `go test -tags=integration ./internal/codegraph/ ./internal/memory/` -> PASS (migrations apply idempotently; confidence/provenance/hyperedge writes verified; `memory_sources` add/list/delete verified).
4. Contract sanity: `grep -n 'confidence_score\|confidence_tier\|line_start\|source_url\|captured_at\|hyperedges\|reconcile_files' internal/codegraph/types.go internal/httpapi/ingest.go` shows every locked JSON tag byte-for-byte as in the Phase 0 lock.

## Notes for the executor

- The JSON tags in Task 3 and Task 7 are LOCKED by `2026-06-09-phase0-contract-lock.md`. Do not reorder fields or rename tags; the ingester's `internal/contract/contract.go` mirror is guarded by a `contract_shape_test.go` against these exact shapes.
- `NewService` / `NewPool` are kept as-is and new `*WithSources` constructors added so no existing test or call site breaks; only `cmd/tatara-memory/app.go` switches to the `*WithSources` variants for production wiring.
- Provenance columns are written as SQL NULL when empty (Task 4 null-helpers) so reserved columns stay NULL until a producer fills them; the analytics columns (`community`/`cohesion`/`degree`/`betweenness`) are never written on the wire (server-computed later) and are not touched by `Reconcile`.
- The `/memories:bulk` purge is synchronous-before-enqueue (ordered, idempotent); record in `MEMORY.md` that strict job-atomicity of purge+insert is deferred (a worker pre-phase) and why, per the repo's no-tech-debt rule.

### Critical Files for Implementation

- /Users/szymonri/Documents/tatara/tatara-memory/internal/codegraph/types.go
- /Users/szymonri/Documents/tatara/tatara-memory/internal/codegraph/pgstore.go
- /Users/szymonri/Documents/tatara/tatara-memory/internal/memory/service.go
- /Users/szymonri/Documents/tatara/tatara-memory/internal/ingest/pool.go
- /Users/szymonri/Documents/tatara/tatara-memory/internal/httpapi/ingest.go