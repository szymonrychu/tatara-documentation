# Code Graph in tatara-memory (sub-project A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic code-graph store and query API to tatara-memory: a `POST /code-graph:bulk` ingest endpoint (file-granular replace) plus `GET /code/*` traversal endpoints, backed by two new Postgres tables, exposing the structural graph that sub-projects B (ingester) and C (MCP tools) build on.

**Architecture:** New self-contained `internal/codegraph` package (domain types, embedded-SQL migration, `*sql.DB`-backed `PGStore`, validating `Service`, domain metrics) mirroring the existing `internal/ingest` patterns exactly. New `internal/httpapi/codegraph.go` handlers wired into the existing chi router behind the same OIDC auth group. The graph push is **synchronous** (a single bulk transaction - sub-second), unlike text ingest which stays async via the existing `/memories:bulk`. Migrations are wired at startup in `run()` (fixing a pre-existing gap: `ingest.Migrate` was never called in prod).

**Tech Stack:** Go 1.25, `database/sql` + `github.com/jackc/pgx/v5/stdlib`, `github.com/go-chi/chi/v5`, `github.com/prometheus/client_golang`, embedded SQL via `//go:embed`. Integration tests gated on `TATARA_TEST_PG_DSN` behind `//go:build integration`.

**Key deviations from the design spec (both KISS improvements, already reflected here):**
1. The graph push is **synchronous returning 200 + counts**, not an async 202+job. A single DB transaction does not need the worker-pool/job machinery; the slow part (LightRAG embedding of semantic chunks) stays on the existing async `/memories:bulk` that the ingester calls separately. Update the spec's "async, reuses ingest_jobs" note to match.
2. Migrations are now **applied at startup** (`run()` calls `a.migrate(ctx)`), which also fixes the latent gap that `ingest_jobs` was never migrated in production (cnpg only creates the `vector` extension). Record this in `tatara-memory/MEMORY.md`.

---

## File Structure

**New package `internal/codegraph/`:**
- `types.go` - `Entity`, `Edge`, `GraphPush`, `PushResult`, `EntityDetail`, `PathNode`; relation constants + private relation-set vars; sentinel errors; `clampDepth`/`normalizeDir` helpers.
- `migrations/0001_codegraph.sql` - `code_entities` + `code_edges` DDL.
- `migrate.go` - embedded `MigrationSQL()` + `Migrate(ctx, db)`.
- `store.go` - `Store` interface.
- `pgstore.go` - `PGStore` (`*sql.DB`): `Reconcile`, `SearchEntities`, `GetEntity`, `Neighbors`, `FileImports`, `CountEntities`.
- `service.go` - `Service` (validation, depth caps, named relation-set methods, metrics).
- `metrics.go` - `Metrics` (entities/edges upserted counters).
- `*_test.go` - unit tests (service, helpers) + integration tests (pgstore, migrate).

**Modified in `internal/httpapi/`:**
- `service.go` - add `CodeGraphService` interface.
- `errmap.go` - map `codegraph.ErrEntityNotFound` -> 404, `codegraph.ErrInvalidScope` -> 400.
- `router.go` - add `CodeGraph CodeGraphService` to `Config`; mount `/code-graph:bulk` + `/code/*` when non-nil.
- `codegraph.go` (new) - all code-graph handlers.

**Modified in `cmd/tatara-memory/`:**
- `app.go` - construct `codegraph` store/metrics/service; add `(a *app) migrate(ctx)`; inject `CodeGraph` into router config; add `cgSvc` to the `app` struct so `migrate` reaches the DB.
- `main.go` - `run()` calls `a.migrate(ctx)` after `newApp`, before listening.

---

## Conventions to follow (verbatim from the existing codebase)

- Errcheck idiom for rows/tx: `defer func() { _ = rows.Close() }()` and `defer func() { _ = tx.Rollback() }()`.
- Error wrapping: `fmt.Errorf("context: %w", err)`.
- Handlers are `handleXxx(cfg Config) http.HandlerFunc` closures; decode with `json.NewDecoder(r.Body).Decode(&x)`; validate; call service; on error `mapServiceError(w, r, err)`; success `WriteJSON(w, status, body)` / `WriteError(w, status, msg, RequestIDFromContext(r.Context()))`.
- jsonb params are cast explicitly in SQL (`$7::jsonb`); jsonb columns scan into `[]byte` then `json.Unmarshal`.
- No docstrings/comments on unchanged code; exported identifiers get a doc comment (the `revive` `exported` rule is on).
- `goimports` local prefix is `github.com/szymonrychu/tatara-memory`.

Run unit tests with `go test ./... -race -count=1`. Run integration tests with `go test -tags integration ./internal/codegraph/... -run Integration` (or the specific test) with `TATARA_TEST_PG_DSN` set. Lint with `golangci-lint run ./... || [ $? -eq 5 ]`.

---

## Task 1: codegraph domain types, constants, and helpers

**Files:**
- Create: `internal/codegraph/types.go`
- Test: `internal/codegraph/types_test.go`

- [ ] **Step 1: Write the failing test**

```go
package codegraph

import "testing"

func TestClampDepth(t *testing.T) {
	cases := []struct {
		name string
		in   int
		want int
	}{
		{"zero defaults", 0, defaultDepth},
		{"negative defaults", -3, defaultDepth},
		{"within range kept", 5, 5},
		{"over max clamped", 99, maxDepth},
		{"max kept", maxDepth, maxDepth},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := clampDepth(c.in); got != c.want {
				t.Fatalf("clampDepth(%d) = %d, want %d", c.in, got, c.want)
			}
		})
	}
}

func TestNormalizeDir(t *testing.T) {
	cases := map[string]string{
		"out": "out",
		"in":  "in",
		"":    "out",
		"OUT": "out",
		"bad": "out",
	}
	for in, want := range cases {
		if got := normalizeDir(in); got != want {
			t.Fatalf("normalizeDir(%q) = %q, want %q", in, got, want)
		}
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd tatara-memory && go test ./internal/codegraph/ -run 'TestClampDepth|TestNormalizeDir'`
Expected: FAIL - `internal/codegraph` package does not exist / undefined `clampDepth`.

- [ ] **Step 3: Write the implementation**

Create `internal/codegraph/types.go`:

```go
// Package codegraph stores and serves a deterministic code-structure graph
// (entities and edges) emitted by the repo ingester, scoped per repository and
// reconciled at file granularity.
package codegraph

import (
	"errors"
	"strings"
)

// ErrEntityNotFound is returned when a requested entity does not exist.
var ErrEntityNotFound = errors.New("codegraph: entity not found")

// ErrInvalidScope is returned when a push is malformed or contains an entity or
// edge whose owning file is not in the push's declared file set.
var ErrInvalidScope = errors.New("codegraph: invalid push scope")

// Relation constants used by the named-traversal endpoints. The producer
// (sub-project B) emits the full relation vocabulary; this service only needs
// the relations it groups into traversal sets.
const (
	relCalls        = "calls"
	relImports      = "imports"
	relReferences   = "references"
	relDependsOn    = "depends_on"
	relValueRef     = "value_ref"
	relIncludes     = "includes"
	relSubchart     = "subchart"
	relModuleSource = "module_source"
)

var (
	callRelations       = []string{relCalls}
	dependencyRelations = []string{relImports, relReferences, relDependsOn}
	resourceRelations   = []string{relDependsOn, relReferences, relValueRef, relIncludes, relSubchart, relModuleSource}
)

const (
	defaultDepth = 3
	maxDepth     = 10
)

// Entity is a node in the code graph (a package, type, function, resource,
// template, value key, file, or repo root). The ID is a canonical
// "<lang>:<kind>:<fqn>" string and is treated as opaque by the store.
type Entity struct {
	ID          string            `json:"id"`
	Name        string            `json:"name"`
	Type        string            `json:"type"`
	Description string            `json:"description,omitempty"`
	FilePath    string            `json:"file_path"`
	Properties  map[string]string `json:"properties,omitempty"`
}

// Edge is a directed, typed relationship between two entities. SrcFile is the
// file that owns the edge (where the reference originates) and is the unit of
// file-granular replacement.
type Edge struct {
	From       string            `json:"from"`
	To         string            `json:"to"`
	Relation   string            `json:"relation"`
	SrcFile    string            `json:"src_file"`
	Properties map[string]string `json:"properties,omitempty"`
}

// GraphPush is one ingest request: the changed file set plus the entities and
// edges those files own. Reconciliation deletes the prior graph owned by Files
// then inserts Entities and Edges, in one transaction.
type GraphPush struct {
	Repo     string   `json:"repo"`
	Commit   string   `json:"commit,omitempty"`
	Files    []string `json:"files"`
	Entities []Entity `json:"entities"`
	Edges    []Edge   `json:"edges"`
}

// PushResult summarises a completed reconciliation.
type PushResult struct {
	Repo             string `json:"repo"`
	Files            int    `json:"files"`
	EntitiesUpserted int    `json:"entities_upserted"`
	EdgesUpserted    int    `json:"edges_upserted"`
}

// EntityDetail is an entity plus its immediate outgoing and incoming edges.
type EntityDetail struct {
	Entity
	OutEdges []Edge `json:"out_edges"`
	InEdges  []Edge `json:"in_edges"`
}

// PathNode is an entity reached during a traversal, with the depth (>=1) at
// which it was first found.
type PathNode struct {
	Entity
	Depth int `json:"depth"`
}

func clampDepth(d int) int {
	if d <= 0 {
		return defaultDepth
	}
	if d > maxDepth {
		return maxDepth
	}
	return d
}

func normalizeDir(dir string) string {
	if strings.ToLower(dir) == "in" {
		return "in"
	}
	return "out"
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `go test ./internal/codegraph/ -run 'TestClampDepth|TestNormalizeDir'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/codegraph/types.go internal/codegraph/types_test.go
git commit -m "feat(codegraph): domain types, relation sets, depth/dir helpers"
```

---

## Task 2: migration SQL and Migrate

**Files:**
- Create: `internal/codegraph/migrations/0001_codegraph.sql`
- Create: `internal/codegraph/migrate.go`
- Test: `internal/codegraph/migrate_test.go`

- [ ] **Step 1: Write the failing test**

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
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/codegraph/ -run TestMigrationSQLExists`
Expected: FAIL - undefined `codegraph.MigrationSQL`.

- [ ] **Step 3: Write the implementation**

Create `internal/codegraph/migrations/0001_codegraph.sql`:

```sql
CREATE TABLE IF NOT EXISTS code_entities (
    repo        text NOT NULL,
    id          text NOT NULL,
    name        text NOT NULL,
    type        text NOT NULL,
    description text  NOT NULL DEFAULT '',
    file_path   text  NOT NULL DEFAULT '',
    properties  jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (repo, id)
);

CREATE INDEX IF NOT EXISTS idx_code_entities_repo_file ON code_entities(repo, file_path);
CREATE INDEX IF NOT EXISTS idx_code_entities_type ON code_entities(repo, type);
CREATE INDEX IF NOT EXISTS idx_code_entities_name ON code_entities(repo, name);

CREATE TABLE IF NOT EXISTS code_edges (
    repo       text NOT NULL,
    from_id    text NOT NULL,
    to_id      text NOT NULL,
    relation   text NOT NULL,
    src_file   text  NOT NULL DEFAULT '',
    properties jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (repo, from_id, to_id, relation)
);

CREATE INDEX IF NOT EXISTS idx_code_edges_to ON code_edges(repo, to_id, relation);
CREATE INDEX IF NOT EXISTS idx_code_edges_from ON code_edges(repo, from_id, relation);
CREATE INDEX IF NOT EXISTS idx_code_edges_src_file ON code_edges(repo, src_file);
```

Create `internal/codegraph/migrate.go`:

```go
package codegraph

import (
	"context"
	"database/sql"
	_ "embed"
)

//go:embed migrations/0001_codegraph.sql
var migration0001 string

// MigrationSQL returns the DDL for the code-graph schema.
func MigrationSQL() string {
	return migration0001
}

// Migrate applies the code-graph schema to db, creating tables if they do not exist.
func Migrate(ctx context.Context, db *sql.DB) error {
	_, err := db.ExecContext(ctx, migration0001)
	return err
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `go test ./internal/codegraph/ -run TestMigrationSQLExists`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/codegraph/migrate.go internal/codegraph/migrations/0001_codegraph.sql internal/codegraph/migrate_test.go
git commit -m "feat(codegraph): embedded schema migration (code_entities, code_edges)"
```

---

## Task 3: Store interface and PGStore.Reconcile (file-granular replace)

**Files:**
- Create: `internal/codegraph/store.go`
- Create: `internal/codegraph/pgstore.go`
- Test: `internal/codegraph/pgstore_test.go` (integration, build tag)

This task uses a real Postgres. Mirror the existing `internal/ingest/pgstore_test.go` `openPG` helper.

- [ ] **Step 1: Write the failing test**

Create `internal/codegraph/pgstore_test.go`:

```go
//go:build integration

package codegraph_test

import (
	"context"
	"database/sql"
	"os"
	"testing"

	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory/internal/codegraph"
)

func openPG(t *testing.T) *sql.DB {
	t.Helper()
	dsn := os.Getenv("TATARA_TEST_PG_DSN")
	if dsn == "" {
		t.Skip("TATARA_TEST_PG_DSN not set; skipping integration test")
	}
	db, err := sql.Open("pgx", dsn)
	require.NoError(t, err)
	t.Cleanup(func() { _ = db.Close() })
	return db
}

func freshStore(t *testing.T) (*codegraph.PGStore, context.Context) {
	t.Helper()
	ctx := context.Background()
	db := openPG(t)
	require.NoError(t, codegraph.Migrate(ctx, db))
	_, err := db.ExecContext(ctx, `DELETE FROM code_edges; DELETE FROM code_entities;`)
	require.NoError(t, err)
	return codegraph.NewPGStore(db), ctx
}

func ent(id, typ, file string) codegraph.Entity {
	return codegraph.Entity{ID: id, Name: id, Type: typ, FilePath: file, Properties: map[string]string{"language": "go"}}
}

func TestReconcileInsertsAndReplacesPerFile(t *testing.T) {
	s, ctx := freshStore(t)

	// Initial push: two files, two entities, one edge a->b owned by a.go.
	res, err := s.Reconcile(ctx, codegraph.GraphPush{
		Repo:  "r",
		Files: []string{"a.go", "b.go"},
		Entities: []codegraph.Entity{
			ent("go:func:r/a.A", "go_func", "a.go"),
			ent("go:func:r/b.B", "go_func", "b.go"),
		},
		Edges: []codegraph.Edge{
			{From: "go:func:r/a.A", To: "go:func:r/b.B", Relation: "calls", SrcFile: "a.go"},
		},
	})
	require.NoError(t, err)
	require.Equal(t, 2, res.EntitiesUpserted)
	require.Equal(t, 1, res.EdgesUpserted)

	n, err := s.CountEntities(ctx, "r")
	require.NoError(t, err)
	require.Equal(t, 2, n)

	// Re-push only a.go: A now calls nothing. b.go and its entity untouched.
	_, err = s.Reconcile(ctx, codegraph.GraphPush{
		Repo:     "r",
		Files:    []string{"a.go"},
		Entities: []codegraph.Entity{ent("go:func:r/a.A", "go_func", "a.go")},
		Edges:    nil,
	})
	require.NoError(t, err)

	// b.go entity still present (not in the changed file set).
	n, err = s.CountEntities(ctx, "r")
	require.NoError(t, err)
	require.Equal(t, 2, n)

	// The a->b edge (owned by a.go) was removed.
	callees, err := s.Neighbors(ctx, "r", "go:func:r/a.A", []string{"calls"}, "out", 3)
	require.NoError(t, err)
	require.Empty(t, callees)
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test -tags integration ./internal/codegraph/ -run TestReconcileInsertsAndReplacesPerFile`
Expected: FAIL - undefined `codegraph.NewPGStore` / `PGStore`.

- [ ] **Step 3: Write the implementation**

Create `internal/codegraph/store.go`:

```go
package codegraph

import "context"

// Store is the persistence interface for the code graph.
type Store interface {
	Reconcile(ctx context.Context, p GraphPush) (PushResult, error)
	SearchEntities(ctx context.Context, repo, q, typ string, limit int) ([]Entity, error)
	GetEntity(ctx context.Context, repo, id string) (EntityDetail, error)
	Neighbors(ctx context.Context, repo, id string, relations []string, dir string, depth int) ([]PathNode, error)
	FileImports(ctx context.Context, repo, path string) ([]Edge, error)
	CountEntities(ctx context.Context, repo string) (int, error)
}
```

Create `internal/codegraph/pgstore.go` (Reconcile + CountEntities for this task; the read methods are added in Task 4):

```go
package codegraph

import (
	"context"
	"database/sql"
	"encoding/json"
)

// PGStore is a PostgreSQL-backed implementation of Store.
type PGStore struct {
	db *sql.DB
}

// NewPGStore returns a PGStore backed by the given database connection.
func NewPGStore(db *sql.DB) *PGStore {
	return &PGStore{db: db}
}

func marshalProps(p map[string]string) string {
	if len(p) == 0 {
		return "{}"
	}
	b, err := json.Marshal(p)
	if err != nil {
		return "{}"
	}
	return string(b)
}

func scanProps(raw []byte) map[string]string {
	if len(raw) == 0 {
		return nil
	}
	var m map[string]string
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil
	}
	return m
}

// Reconcile deletes the prior graph owned by p.Files then inserts p.Entities and
// p.Edges, all in a single transaction.
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
	}

	for _, e := range p.Entities {
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO code_entities(repo, id, name, type, description, file_path, properties)
			VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)
			ON CONFLICT (repo, id) DO UPDATE SET
				name=EXCLUDED.name, type=EXCLUDED.type, description=EXCLUDED.description,
				file_path=EXCLUDED.file_path, properties=EXCLUDED.properties`,
			p.Repo, e.ID, e.Name, e.Type, e.Description, e.FilePath, marshalProps(e.Properties)); err != nil {
			return PushResult{}, err
		}
	}

	for _, e := range p.Edges {
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO code_edges(repo, from_id, to_id, relation, src_file, properties)
			VALUES ($1,$2,$3,$4,$5,$6::jsonb)
			ON CONFLICT (repo, from_id, to_id, relation) DO UPDATE SET
				src_file=EXCLUDED.src_file, properties=EXCLUDED.properties`,
			p.Repo, e.From, e.To, e.Relation, e.SrcFile, marshalProps(e.Properties)); err != nil {
			return PushResult{}, err
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

// CountEntities returns the number of entities stored for a repo.
func (s *PGStore) CountEntities(ctx context.Context, repo string) (int, error) {
	var n int
	err := s.db.QueryRowContext(ctx, `SELECT count(*) FROM code_entities WHERE repo=$1`, repo).Scan(&n)
	return n, err
}
```

Note: `Neighbors` is referenced by the test but implemented in Task 4. To keep this task's test compiling and the package building, add the remaining `Store` methods as stubs now and flesh them out in Task 4. Add to `pgstore.go`:

```go
// SearchEntities is implemented in Task 4.
func (s *PGStore) SearchEntities(ctx context.Context, repo, q, typ string, limit int) ([]Entity, error) {
	return nil, nil
}

// GetEntity is implemented in Task 4.
func (s *PGStore) GetEntity(ctx context.Context, repo, id string) (EntityDetail, error) {
	return EntityDetail{}, ErrEntityNotFound
}

// Neighbors is implemented in Task 4.
func (s *PGStore) Neighbors(ctx context.Context, repo, id string, relations []string, dir string, depth int) ([]PathNode, error) {
	return nil, nil
}

// FileImports is implemented in Task 4.
func (s *PGStore) FileImports(ctx context.Context, repo, path string) ([]Edge, error) {
	return nil, nil
}
```

(The `Neighbors` stub returns nil, so the "edge removed" assertion `require.Empty(callees)` passes for the wrong reason in this task; Task 4 replaces the stub and its own test asserts real traversal. This is acceptable staging - the Reconcile behaviour under test here is real.)

- [ ] **Step 4: Run test to verify it passes**

Run: `go test -tags integration ./internal/codegraph/ -run TestReconcileInsertsAndReplacesPerFile` (with `TATARA_TEST_PG_DSN` set)
Expected: PASS. Without the env var: SKIP.

Also run the non-integration build to ensure the package compiles: `go build ./internal/codegraph/`

- [ ] **Step 5: Commit**

```bash
git add internal/codegraph/store.go internal/codegraph/pgstore.go internal/codegraph/pgstore_test.go
git commit -m "feat(codegraph): PGStore.Reconcile with file-granular replace"
```

---

## Task 4: PGStore read methods (search, get, neighbors, file-imports)

**Files:**
- Modify: `internal/codegraph/pgstore.go` (replace the four stubs)
- Test: `internal/codegraph/pgstore_read_test.go` (integration)

- [ ] **Step 1: Write the failing test**

Create `internal/codegraph/pgstore_read_test.go`:

```go
//go:build integration

package codegraph_test

import (
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory/internal/codegraph"
)

func TestReadMethods(t *testing.T) {
	s, ctx := freshStore(t)

	// A -> B -> C call chain (each owned by its own file), plus a dangling
	// edge C -> D where D was never inserted (orphan, must be filtered).
	_, err := s.Reconcile(ctx, codegraph.GraphPush{
		Repo:  "r",
		Files: []string{"a.go", "b.go", "c.go"},
		Entities: []codegraph.Entity{
			ent("go:func:r/a.A", "go_func", "a.go"),
			ent("go:func:r/b.B", "go_func", "b.go"),
			ent("go:func:r/c.C", "go_func", "c.go"),
		},
		Edges: []codegraph.Edge{
			{From: "go:func:r/a.A", To: "go:func:r/b.B", Relation: "calls", SrcFile: "a.go"},
			{From: "go:func:r/b.B", To: "go:func:r/c.C", Relation: "calls", SrcFile: "b.go"},
			{From: "go:func:r/c.C", To: "go:func:r/d.D", Relation: "calls", SrcFile: "c.go"},
		},
	})
	require.NoError(t, err)

	// SearchEntities by name fragment + type filter.
	found, err := s.SearchEntities(ctx, "r", "B", "go_func", 10)
	require.NoError(t, err)
	require.Len(t, found, 1)
	require.Equal(t, "go:func:r/b.B", found[0].ID)

	// GetEntity returns the entity plus its in/out edges.
	det, err := s.GetEntity(ctx, "r", "go:func:r/b.B")
	require.NoError(t, err)
	require.Equal(t, "go:func:r/b.B", det.ID)
	require.Len(t, det.OutEdges, 1) // B -> C
	require.Len(t, det.InEdges, 1)  // A -> B

	// GetEntity for a missing id is ErrEntityNotFound.
	_, err = s.GetEntity(ctx, "r", "go:func:r/nope")
	require.ErrorIs(t, err, codegraph.ErrEntityNotFound)

	// Callees of A to depth 3 = {B, C}; the orphan D is filtered out.
	out, err := s.Neighbors(ctx, "r", "go:func:r/a.A", []string{"calls"}, "out", 3)
	require.NoError(t, err)
	ids := map[string]int{}
	for _, n := range out {
		ids[n.ID] = n.Depth
	}
	require.Equal(t, 1, ids["go:func:r/b.B"])
	require.Equal(t, 2, ids["go:func:r/c.C"])
	require.NotContains(t, ids, "go:func:r/d.D")

	// Callers of C (direction in) to depth 3 = {B, A}.
	in, err := s.Neighbors(ctx, "r", "go:func:r/c.C", []string{"calls"}, "in", 3)
	require.NoError(t, err)
	inIDs := map[string]bool{}
	for _, n := range in {
		inIDs[n.ID] = true
	}
	require.True(t, inIDs["go:func:r/b.B"])
	require.True(t, inIDs["go:func:r/a.A"])

	// Depth 1 from A = {B} only.
	d1, err := s.Neighbors(ctx, "r", "go:func:r/a.A", []string{"calls"}, "out", 1)
	require.NoError(t, err)
	require.Len(t, d1, 1)
	require.Equal(t, "go:func:r/b.B", d1[0].ID)
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test -tags integration ./internal/codegraph/ -run TestReadMethods`
Expected: FAIL - stubs return nil/empty, assertions fail.

- [ ] **Step 3: Write the implementation**

Replace the four stub methods in `internal/codegraph/pgstore.go` with:

```go
// SearchEntities returns entities in repo matching an optional name/description
// fragment and optional exact type, ordered by name.
func (s *PGStore) SearchEntities(ctx context.Context, repo, q, typ string, limit int) ([]Entity, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, name, type, description, file_path, properties
		FROM code_entities
		WHERE repo=$1
		  AND ($2='' OR name ILIKE '%'||$2||'%' OR description ILIKE '%'||$2||'%')
		  AND ($3='' OR type=$3)
		ORDER BY name
		LIMIT $4`, repo, q, typ, limit)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	var out []Entity
	for rows.Next() {
		e, err := scanEntity(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

// GetEntity returns one entity plus its immediate outgoing and incoming edges.
func (s *PGStore) GetEntity(ctx context.Context, repo, id string) (EntityDetail, error) {
	row := s.db.QueryRowContext(ctx, `
		SELECT id, name, type, description, file_path, properties
		FROM code_entities WHERE repo=$1 AND id=$2`, repo, id)
	e, err := scanEntity(row)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return EntityDetail{}, ErrEntityNotFound
		}
		return EntityDetail{}, err
	}
	out, err := s.queryEdges(ctx,
		`SELECT from_id, to_id, relation, src_file, properties FROM code_edges WHERE repo=$1 AND from_id=$2`,
		repo, id)
	if err != nil {
		return EntityDetail{}, err
	}
	in, err := s.queryEdges(ctx,
		`SELECT from_id, to_id, relation, src_file, properties FROM code_edges WHERE repo=$1 AND to_id=$2`,
		repo, id)
	if err != nil {
		return EntityDetail{}, err
	}
	return EntityDetail{Entity: e, OutEdges: out, InEdges: in}, nil
}

// neighborsOutQuery walks from->to (forward). neighborsInQuery walks to->from
// (reverse). Two fixed queries avoid building SQL by string concatenation
// (gosec G202). Orphan targets are dropped by the join to code_entities.
const neighborsOutQuery = `
	WITH RECURSIVE walk(id, depth) AS (
		SELECT $2::text, 0
		UNION
		SELECT e.to_id, w.depth + 1
		FROM walk w
		JOIN code_edges e ON e.repo=$1 AND e.from_id=w.id
		 AND e.relation = ANY(string_to_array($3, ','))
		WHERE w.depth < $4
	)
	SELECT DISTINCT ON (en.id) en.id, en.name, en.type, en.description, en.file_path, en.properties, w.depth
	FROM walk w
	JOIN code_entities en ON en.repo=$1 AND en.id=w.id
	WHERE w.depth > 0
	ORDER BY en.id, w.depth`

const neighborsInQuery = `
	WITH RECURSIVE walk(id, depth) AS (
		SELECT $2::text, 0
		UNION
		SELECT e.from_id, w.depth + 1
		FROM walk w
		JOIN code_edges e ON e.repo=$1 AND e.to_id=w.id
		 AND e.relation = ANY(string_to_array($3, ','))
		WHERE w.depth < $4
	)
	SELECT DISTINCT ON (en.id) en.id, en.name, en.type, en.description, en.file_path, en.properties, w.depth
	FROM walk w
	JOIN code_entities en ON en.repo=$1 AND en.id=w.id
	WHERE w.depth > 0
	ORDER BY en.id, w.depth`

// Neighbors walks edges of the given relations from id, in the given direction
// ("out" follows from->to, "in" follows to->from), up to depth hops.
func (s *PGStore) Neighbors(ctx context.Context, repo, id string, relations []string, dir string, depth int) ([]PathNode, error) {
	query := neighborsOutQuery
	if dir == "in" {
		query = neighborsInQuery
	}
	rows, err := s.db.QueryContext(ctx, query, repo, id, strings.Join(relations, ","), depth)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	var out []PathNode
	for rows.Next() {
		var n PathNode
		var raw []byte
		if err := rows.Scan(&n.ID, &n.Name, &n.Type, &n.Description, &n.FilePath, &raw, &n.Depth); err != nil {
			return nil, err
		}
		n.Properties = scanProps(raw)
		out = append(out, n)
	}
	return out, rows.Err()
}

// FileImports returns the import edges that originate in the given file.
func (s *PGStore) FileImports(ctx context.Context, repo, path string) ([]Edge, error) {
	return s.queryEdges(ctx,
		`SELECT from_id, to_id, relation, src_file, properties FROM code_edges WHERE repo=$1 AND src_file=$2 AND relation='imports'`,
		repo, path)
}

func (s *PGStore) queryEdges(ctx context.Context, query string, args ...any) ([]Edge, error) {
	rows, err := s.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	return scanEdges(rows)
}

type rowScanner interface {
	Scan(dest ...any) error
}

func scanEntity(r rowScanner) (Entity, error) {
	var e Entity
	var raw []byte
	if err := r.Scan(&e.ID, &e.Name, &e.Type, &e.Description, &e.FilePath, &raw); err != nil {
		return Entity{}, err
	}
	e.Properties = scanProps(raw)
	return e, nil
}

func scanEdges(rows *sql.Rows) ([]Edge, error) {
	var out []Edge
	for rows.Next() {
		var e Edge
		var raw []byte
		if err := rows.Scan(&e.From, &e.To, &e.Relation, &e.SrcFile, &raw); err != nil {
			return nil, err
		}
		e.Properties = scanProps(raw)
		out = append(out, e)
	}
	return out, rows.Err()
}
```

Add `"errors"` and `"strings"` to the `pgstore.go` import block (alongside `context`, `database/sql`, `encoding/json`). `strings` is now used by `Neighbors`; `errors` by `GetEntity`.

Note on the `step`/`matchCol` pair: for direction "out" we match edges where `from_id = current` and step to `to_id`; for "in" we match `to_id = current` and step to `from_id`. The two ternaries above set both correctly.

- [ ] **Step 4: Run test to verify it passes**

Run: `go test -tags integration ./internal/codegraph/ -run 'TestReadMethods|TestReconcile'`
Expected: PASS (both). Re-run the Task 3 test too, since `Neighbors` is now real.

Also `go vet ./internal/codegraph/` and `gofmt -l internal/codegraph/` (expect no output).

- [ ] **Step 5: Commit**

```bash
git add internal/codegraph/pgstore.go internal/codegraph/pgstore_read_test.go
git commit -m "feat(codegraph): PGStore search/get/neighbors/file-imports with orphan filtering"
```

---

## Task 5: Metrics and validating Service

**Files:**
- Create: `internal/codegraph/metrics.go`
- Create: `internal/codegraph/service.go`
- Test: `internal/codegraph/service_test.go` (unit, with a fake store)

- [ ] **Step 1: Write the failing test**

Create `internal/codegraph/service_test.go`:

```go
package codegraph_test

import (
	"context"
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory/internal/codegraph"
)

type fakeStore struct {
	pushed   codegraph.GraphPush
	lastRel  []string
	lastDir  string
	lastDep  int
}

func (f *fakeStore) Reconcile(_ context.Context, p codegraph.GraphPush) (codegraph.PushResult, error) {
	f.pushed = p
	return codegraph.PushResult{Repo: p.Repo, Files: len(p.Files), EntitiesUpserted: len(p.Entities), EdgesUpserted: len(p.Edges)}, nil
}
func (f *fakeStore) SearchEntities(_ context.Context, _, _, _ string, _ int) ([]codegraph.Entity, error) {
	return nil, nil
}
func (f *fakeStore) GetEntity(_ context.Context, _, _ string) (codegraph.EntityDetail, error) {
	return codegraph.EntityDetail{}, nil
}
func (f *fakeStore) Neighbors(_ context.Context, _, _ string, relations []string, dir string, depth int) ([]codegraph.PathNode, error) {
	f.lastRel, f.lastDir, f.lastDep = relations, dir, depth
	return nil, nil
}
func (f *fakeStore) FileImports(_ context.Context, _, _ string) ([]codegraph.Edge, error) {
	return nil, nil
}
func (f *fakeStore) CountEntities(_ context.Context, _ string) (int, error) { return 0, nil }

func newSvc() (*codegraph.Service, *fakeStore) {
	fs := &fakeStore{}
	return codegraph.NewService(fs, codegraph.NewMetrics(prometheus.NewRegistry())), fs
}

func TestPushRejectsEntityOutsideFiles(t *testing.T) {
	svc, _ := newSvc()
	_, err := svc.Push(context.Background(), codegraph.GraphPush{
		Repo:     "r",
		Files:    []string{"a.go"},
		Entities: []codegraph.Entity{{ID: "x", FilePath: "b.go"}},
	})
	require.ErrorIs(t, err, codegraph.ErrInvalidScope)
}

func TestPushRejectsEdgeOutsideFiles(t *testing.T) {
	svc, _ := newSvc()
	_, err := svc.Push(context.Background(), codegraph.GraphPush{
		Repo:  "r",
		Files: []string{"a.go"},
		Edges: []codegraph.Edge{{From: "x", To: "y", Relation: "calls", SrcFile: "b.go"}},
	})
	require.ErrorIs(t, err, codegraph.ErrInvalidScope)
}

func TestPushRequiresRepoAndFiles(t *testing.T) {
	svc, _ := newSvc()
	_, err := svc.Push(context.Background(), codegraph.GraphPush{Repo: "", Files: []string{"a.go"}})
	require.ErrorIs(t, err, codegraph.ErrInvalidScope)
	_, err = svc.Push(context.Background(), codegraph.GraphPush{Repo: "r", Files: nil})
	require.ErrorIs(t, err, codegraph.ErrInvalidScope)
}

func TestPushOK(t *testing.T) {
	svc, fs := newSvc()
	res, err := svc.Push(context.Background(), codegraph.GraphPush{
		Repo:     "r",
		Files:    []string{"a.go"},
		Entities: []codegraph.Entity{{ID: "x", FilePath: "a.go"}},
		Edges:    []codegraph.Edge{{From: "x", To: "y", Relation: "calls", SrcFile: "a.go"}},
	})
	require.NoError(t, err)
	require.Equal(t, 1, res.EntitiesUpserted)
	require.Equal(t, "r", fs.pushed.Repo)
}

func TestNamedTraversalsUseCorrectRelationSets(t *testing.T) {
	svc, fs := newSvc()
	ctx := context.Background()

	_, _ = svc.Callers(ctx, "r", "id", 0)
	require.Equal(t, []string{"calls"}, fs.lastRel)
	require.Equal(t, "in", fs.lastDir)
	require.Equal(t, 3, fs.lastDep) // depth 0 -> default 3

	_, _ = svc.Callees(ctx, "r", "id", 50)
	require.Equal(t, "out", fs.lastDir)
	require.Equal(t, 10, fs.lastDep) // depth 50 -> clamped to max 10

	_, _ = svc.Dependents(ctx, "r", "id", 2)
	require.Equal(t, []string{"imports", "references", "depends_on"}, fs.lastRel)
	require.Equal(t, "in", fs.lastDir)

	_, _ = svc.ResourceGraph(ctx, "r", "id", 1)
	require.Equal(t, []string{"depends_on", "references", "value_ref", "includes", "subchart", "module_source"}, fs.lastRel)
	require.Equal(t, "out", fs.lastDir)
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/codegraph/ -run 'TestPush|TestNamedTraversals'`
Expected: FAIL - undefined `codegraph.NewService` / `NewMetrics`.

- [ ] **Step 3: Write the implementation**

Create `internal/codegraph/metrics.go`:

```go
package codegraph

import "github.com/prometheus/client_golang/prometheus"

// Metrics holds the code-graph domain counters.
type Metrics struct {
	entitiesUpserted *prometheus.CounterVec
	edgesUpserted    *prometheus.CounterVec
}

// NewMetrics creates and registers the code-graph metrics with reg.
func NewMetrics(reg prometheus.Registerer) *Metrics {
	m := &Metrics{
		entitiesUpserted: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "code_graph_entities_upserted_total",
			Help: "Code-graph entities upserted, by repo.",
		}, []string{"repo"}),
		edgesUpserted: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "code_graph_edges_upserted_total",
			Help: "Code-graph edges upserted, by repo.",
		}, []string{"repo"}),
	}
	reg.MustRegister(m.entitiesUpserted, m.edgesUpserted)
	return m
}

func (m *Metrics) observePush(repo string, entities, edges int) {
	if m == nil {
		return
	}
	m.entitiesUpserted.WithLabelValues(repo).Add(float64(entities))
	m.edgesUpserted.WithLabelValues(repo).Add(float64(edges))
}
```

Create `internal/codegraph/service.go`:

```go
package codegraph

import (
	"context"
	"fmt"
)

const (
	defaultSearchLimit = 50
	maxSearchLimit     = 500
)

// Service validates requests, applies traversal caps, and delegates to a Store.
type Service struct {
	store   Store
	metrics *Metrics
}

// NewService returns a Service over the given store and metrics.
func NewService(store Store, metrics *Metrics) *Service {
	return &Service{store: store, metrics: metrics}
}

// Push validates that every entity and edge in p is owned by a file in p.Files,
// then reconciles the graph for that file set.
func (s *Service) Push(ctx context.Context, p GraphPush) (PushResult, error) {
	if p.Repo == "" {
		return PushResult{}, fmt.Errorf("%w: repo required", ErrInvalidScope)
	}
	if len(p.Files) == 0 {
		return PushResult{}, fmt.Errorf("%w: files required", ErrInvalidScope)
	}
	files := make(map[string]struct{}, len(p.Files))
	for _, f := range p.Files {
		files[f] = struct{}{}
	}
	for _, e := range p.Entities {
		if _, ok := files[e.FilePath]; !ok {
			return PushResult{}, fmt.Errorf("%w: entity %s file_path %q not in files", ErrInvalidScope, e.ID, e.FilePath)
		}
	}
	for _, e := range p.Edges {
		if _, ok := files[e.SrcFile]; !ok {
			return PushResult{}, fmt.Errorf("%w: edge %s->%s src_file %q not in files", ErrInvalidScope, e.From, e.To, e.SrcFile)
		}
	}
	res, err := s.store.Reconcile(ctx, p)
	if err != nil {
		return PushResult{}, err
	}
	s.metrics.observePush(p.Repo, res.EntitiesUpserted, res.EdgesUpserted)
	return res, nil
}

// Search returns entities matching q and typ in repo, with a capped limit.
func (s *Service) Search(ctx context.Context, repo, q, typ string, limit int) ([]Entity, error) {
	if limit <= 0 {
		limit = defaultSearchLimit
	}
	if limit > maxSearchLimit {
		limit = maxSearchLimit
	}
	return s.store.SearchEntities(ctx, repo, q, typ, limit)
}

// Entity returns one entity with its immediate edges.
func (s *Service) Entity(ctx context.Context, repo, id string) (EntityDetail, error) {
	return s.store.GetEntity(ctx, repo, id)
}

// Neighbors walks the given relations from id with capped depth and normalized direction.
func (s *Service) Neighbors(ctx context.Context, repo, id string, relations []string, dir string, depth int) ([]PathNode, error) {
	return s.store.Neighbors(ctx, repo, id, relations, normalizeDir(dir), clampDepth(depth))
}

// Callers returns entities that call id (reverse "calls").
func (s *Service) Callers(ctx context.Context, repo, id string, depth int) ([]PathNode, error) {
	return s.Neighbors(ctx, repo, id, callRelations, "in", depth)
}

// Callees returns entities that id calls (forward "calls").
func (s *Service) Callees(ctx context.Context, repo, id string, depth int) ([]PathNode, error) {
	return s.Neighbors(ctx, repo, id, callRelations, "out", depth)
}

// Dependents returns entities that depend on id (reverse imports/references/depends_on).
func (s *Service) Dependents(ctx context.Context, repo, id string, depth int) ([]PathNode, error) {
	return s.Neighbors(ctx, repo, id, dependencyRelations, "in", depth)
}

// Dependencies returns entities that id depends on (forward imports/references/depends_on).
func (s *Service) Dependencies(ctx context.Context, repo, id string, depth int) ([]PathNode, error) {
	return s.Neighbors(ctx, repo, id, dependencyRelations, "out", depth)
}

// ResourceGraph returns the forward infra-dependency subgraph from id (tf/helm relations).
func (s *Service) ResourceGraph(ctx context.Context, repo, id string, depth int) ([]PathNode, error) {
	return s.Neighbors(ctx, repo, id, resourceRelations, "out", depth)
}

// FileImports returns the import edges originating in path.
func (s *Service) FileImports(ctx context.Context, repo, path string) ([]Edge, error) {
	return s.store.FileImports(ctx, repo, path)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `go test ./internal/codegraph/ -run 'TestPush|TestNamedTraversals'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/codegraph/metrics.go internal/codegraph/service.go internal/codegraph/service_test.go
git commit -m "feat(codegraph): validating Service with traversal caps and domain metrics"
```

---

## Task 6: httpapi CodeGraphService interface and error mapping

**Files:**
- Modify: `internal/httpapi/service.go` (add interface)
- Modify: `internal/httpapi/errmap.go` (add codegraph error cases)
- Test: `internal/httpapi/errmap_test.go` (add cases; create if absent)

- [ ] **Step 1: Write the failing test**

Append to `internal/httpapi/errmap_test.go` (create the file with this content if it does not exist):

```go
package httpapi

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory/internal/codegraph"
)

func TestMapServiceError_CodeGraph(t *testing.T) {
	cases := []struct {
		name string
		err  error
		want int
	}{
		{"entity not found", codegraph.ErrEntityNotFound, http.StatusNotFound},
		{"invalid scope", codegraph.ErrInvalidScope, http.StatusBadRequest},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			w := httptest.NewRecorder()
			r := httptest.NewRequest(http.MethodGet, "/", nil)
			mapServiceError(w, r, c.err)
			require.Equal(t, c.want, w.Code)
		})
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/httpapi/ -run TestMapServiceError_CodeGraph`
Expected: FAIL - codegraph errors fall through to 500.

- [ ] **Step 3: Write the implementation**

In `internal/httpapi/errmap.go`, add the import `"github.com/szymonrychu/tatara-memory/internal/codegraph"` and two cases at the top of the `switch` in `mapServiceError` (before the `memory.ErrNotFound` case):

```go
	case errors.Is(err, codegraph.ErrEntityNotFound):
		WriteError(w, http.StatusNotFound, "not found", reqID)
	case errors.Is(err, codegraph.ErrInvalidScope):
		WriteError(w, http.StatusBadRequest, err.Error(), reqID)
```

In `internal/httpapi/service.go`, add the import `"github.com/szymonrychu/tatara-memory/internal/codegraph"` and this interface:

```go
// CodeGraphService is the domain interface for the code-structure graph.
// Concrete implementation lives in internal/codegraph.
type CodeGraphService interface {
	Push(ctx context.Context, p codegraph.GraphPush) (codegraph.PushResult, error)
	Search(ctx context.Context, repo, q, typ string, limit int) ([]codegraph.Entity, error)
	Entity(ctx context.Context, repo, id string) (codegraph.EntityDetail, error)
	Neighbors(ctx context.Context, repo, id string, relations []string, dir string, depth int) ([]codegraph.PathNode, error)
	Callers(ctx context.Context, repo, id string, depth int) ([]codegraph.PathNode, error)
	Callees(ctx context.Context, repo, id string, depth int) ([]codegraph.PathNode, error)
	Dependents(ctx context.Context, repo, id string, depth int) ([]codegraph.PathNode, error)
	Dependencies(ctx context.Context, repo, id string, depth int) ([]codegraph.PathNode, error)
	ResourceGraph(ctx context.Context, repo, id string, depth int) ([]codegraph.PathNode, error)
	FileImports(ctx context.Context, repo, path string) ([]codegraph.Edge, error)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `go test ./internal/httpapi/ -run TestMapServiceError_CodeGraph`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/httpapi/service.go internal/httpapi/errmap.go internal/httpapi/errmap_test.go
git commit -m "feat(httpapi): CodeGraphService interface + codegraph error mapping"
```

---

## Task 7: httpapi code-graph handlers

**Files:**
- Create: `internal/httpapi/codegraph.go`
- Test: `internal/httpapi/codegraph_test.go`

All handlers require `repo` as a query parameter (the graph is repo-scoped). Entity IDs contain slashes and colons, so the entity-id routes take the id as a **query parameter** (`?id=...`), not a path segment, to avoid chi path-escaping issues.

- [ ] **Step 1: Write the failing test**

Create `internal/httpapi/codegraph_test.go`:

```go
package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory/internal/codegraph"
)

type stubCodeGraph struct {
	pushErr   error
	pushed    codegraph.GraphPush
	entity    codegraph.EntityDetail
	entityErr error
	nodes     []codegraph.PathNode
}

func (s *stubCodeGraph) Push(_ context.Context, p codegraph.GraphPush) (codegraph.PushResult, error) {
	s.pushed = p
	if s.pushErr != nil {
		return codegraph.PushResult{}, s.pushErr
	}
	return codegraph.PushResult{Repo: p.Repo, Files: len(p.Files), EntitiesUpserted: len(p.Entities), EdgesUpserted: len(p.Edges)}, nil
}
func (s *stubCodeGraph) Search(_ context.Context, _, _, _ string, _ int) ([]codegraph.Entity, error) {
	return []codegraph.Entity{{ID: "go:func:r/a.A", Name: "A", Type: "go_func"}}, nil
}
func (s *stubCodeGraph) Entity(_ context.Context, _, _ string) (codegraph.EntityDetail, error) {
	return s.entity, s.entityErr
}
func (s *stubCodeGraph) Neighbors(_ context.Context, _, _ string, _ []string, _ string, _ int) ([]codegraph.PathNode, error) {
	return s.nodes, nil
}
func (s *stubCodeGraph) Callers(_ context.Context, _, _ string, _ int) ([]codegraph.PathNode, error) {
	return s.nodes, nil
}
func (s *stubCodeGraph) Callees(_ context.Context, _, _ string, _ int) ([]codegraph.PathNode, error) {
	return s.nodes, nil
}
func (s *stubCodeGraph) Dependents(_ context.Context, _, _ string, _ int) ([]codegraph.PathNode, error) {
	return s.nodes, nil
}
func (s *stubCodeGraph) Dependencies(_ context.Context, _, _ string, _ int) ([]codegraph.PathNode, error) {
	return s.nodes, nil
}
func (s *stubCodeGraph) ResourceGraph(_ context.Context, _, _ string, _ int) ([]codegraph.PathNode, error) {
	return s.nodes, nil
}
func (s *stubCodeGraph) FileImports(_ context.Context, _, _ string) ([]codegraph.Edge, error) {
	return []codegraph.Edge{{From: "p", To: "q", Relation: "imports"}}, nil
}

func cgRouter(cg CodeGraphService) http.Handler {
	return NewRouter(Config{
		Service:   &stubService{}, // existing MemoryService stub, pointer receiver (stub_test.go)
		CodeGraph: cg,
		Registry:  prometheus.NewRegistry(),
	})
}

func TestPostCodeGraph_OK(t *testing.T) {
	cg := &stubCodeGraph{}
	body := `{"repo":"r","files":["a.go"],"entities":[{"id":"x","file_path":"a.go","type":"go_func","name":"x"}],"edges":[]}`
	req := httptest.NewRequest(http.MethodPost, "/code-graph:bulk", strings.NewReader(body))
	w := httptest.NewRecorder()
	cgRouter(cg).ServeHTTP(w, req)
	require.Equal(t, http.StatusOK, w.Code)
	var res codegraph.PushResult
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &res))
	require.Equal(t, 1, res.EntitiesUpserted)
	require.Equal(t, "r", cg.pushed.Repo)
}

func TestPostCodeGraph_InvalidJSON(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/code-graph:bulk", strings.NewReader("{"))
	w := httptest.NewRecorder()
	cgRouter(&stubCodeGraph{}).ServeHTTP(w, req)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestSearchEntities_OK(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/code/entities?repo=r&q=A", nil)
	w := httptest.NewRecorder()
	cgRouter(&stubCodeGraph{}).ServeHTTP(w, req)
	require.Equal(t, http.StatusOK, w.Code)
	require.Contains(t, w.Body.String(), "go:func:r/a.A")
}

func TestSearchEntities_MissingRepo(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/code/entities?q=A", nil)
	w := httptest.NewRecorder()
	cgRouter(&stubCodeGraph{}).ServeHTTP(w, req)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestGetEntity_NotFound(t *testing.T) {
	cg := &stubCodeGraph{entityErr: codegraph.ErrEntityNotFound}
	req := httptest.NewRequest(http.MethodGet, "/code/entity?repo=r&id=missing", nil)
	w := httptest.NewRecorder()
	cgRouter(cg).ServeHTTP(w, req)
	require.Equal(t, http.StatusNotFound, w.Code)
}

func TestCallers_OK(t *testing.T) {
	cg := &stubCodeGraph{nodes: []codegraph.PathNode{{Entity: codegraph.Entity{ID: "go:func:r/x.X"}, Depth: 1}}}
	req := httptest.NewRequest(http.MethodGet, "/code/callers?repo=r&id=go:func:r/y.Y&depth=2", nil)
	w := httptest.NewRecorder()
	cgRouter(cg).ServeHTTP(w, req)
	require.Equal(t, http.StatusOK, w.Code)
	require.Contains(t, w.Body.String(), "go:func:r/x.X")
}

func TestNeighbors_RequiresRelation(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/code/neighbors?repo=r&id=x", nil)
	w := httptest.NewRecorder()
	cgRouter(&stubCodeGraph{}).ServeHTTP(w, req)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestFileImports_OK(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/code/file-imports?repo=r&path=a.go", nil)
	w := httptest.NewRecorder()
	cgRouter(&stubCodeGraph{}).ServeHTTP(w, req)
	require.Equal(t, http.StatusOK, w.Code)
	require.Contains(t, w.Body.String(), "imports")
}
```

The existing MemoryService stub is `stubService` (pointer receiver) in `internal/httpapi/stub_test.go`; `cgRouter` reuses it as `&stubService{}` so `Config.Service` is non-nil.

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./internal/httpapi/ -run 'TestPostCodeGraph|TestSearchEntities|TestGetEntity|TestCallers|TestNeighbors|TestFileImports'`
Expected: FAIL - `Config` has no `CodeGraph` field; handlers/routes undefined.

- [ ] **Step 3: Write the implementation**

Create `internal/httpapi/codegraph.go`:

```go
package httpapi

import (
	"encoding/json"
	"net/http"
	"strconv"
	"strings"

	"github.com/szymonrychu/tatara-memory/internal/codegraph"
)

func handlePostCodeGraph(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var p codegraph.GraphPush
		if err := json.NewDecoder(r.Body).Decode(&p); err != nil {
			WriteError(w, http.StatusBadRequest, "invalid json", RequestIDFromContext(r.Context()))
			return
		}
		res, err := cfg.CodeGraph.Push(r.Context(), p)
		if err != nil {
			mapServiceError(w, r, err)
			return
		}
		WriteJSON(w, http.StatusOK, res)
	}
}

func reqRepo(w http.ResponseWriter, r *http.Request) (string, bool) {
	repo := r.URL.Query().Get("repo")
	if repo == "" {
		WriteError(w, http.StatusBadRequest, "repo query parameter required", RequestIDFromContext(r.Context()))
		return "", false
	}
	return repo, true
}

func reqIDParam(w http.ResponseWriter, r *http.Request) (string, bool) {
	id := r.URL.Query().Get("id")
	if id == "" {
		WriteError(w, http.StatusBadRequest, "id query parameter required", RequestIDFromContext(r.Context()))
		return "", false
	}
	return id, true
}

func depthParam(r *http.Request) int {
	n, _ := strconv.Atoi(r.URL.Query().Get("depth"))
	return n
}

// Named handleSearchCodeEntities (not handleSearchEntities) to avoid colliding
// with the existing memory entity handler in entities.go.
func handleSearchCodeEntities(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		repo, ok := reqRepo(w, r)
		if !ok {
			return
		}
		limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
		es, err := cfg.CodeGraph.Search(r.Context(), repo, r.URL.Query().Get("q"), r.URL.Query().Get("type"), limit)
		if err != nil {
			mapServiceError(w, r, err)
			return
		}
		WriteJSON(w, http.StatusOK, map[string]interface{}{"entities": es})
	}
}

func handleGetCodeEntity(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		repo, ok := reqRepo(w, r)
		if !ok {
			return
		}
		id, ok := reqIDParam(w, r)
		if !ok {
			return
		}
		det, err := cfg.CodeGraph.Entity(r.Context(), repo, id)
		if err != nil {
			mapServiceError(w, r, err)
			return
		}
		WriteJSON(w, http.StatusOK, det)
	}
}

func writeNodes(w http.ResponseWriter, r *http.Request, nodes []codegraph.PathNode, err error) {
	if err != nil {
		mapServiceError(w, r, err)
		return
	}
	WriteJSON(w, http.StatusOK, map[string]interface{}{"nodes": nodes})
}

func handleNeighbors(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		repo, ok := reqRepo(w, r)
		if !ok {
			return
		}
		id, ok := reqIDParam(w, r)
		if !ok {
			return
		}
		rel := r.URL.Query().Get("relation")
		if rel == "" {
			WriteError(w, http.StatusBadRequest, "relation query parameter required", RequestIDFromContext(r.Context()))
			return
		}
		nodes, err := cfg.CodeGraph.Neighbors(r.Context(), repo, id, strings.Split(rel, ","), r.URL.Query().Get("direction"), depthParam(r))
		writeNodes(w, r, nodes, err)
	}
}

func handleCallers(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		repo, ok := reqRepo(w, r)
		if !ok {
			return
		}
		id, ok := reqIDParam(w, r)
		if !ok {
			return
		}
		nodes, err := cfg.CodeGraph.Callers(r.Context(), repo, id, depthParam(r))
		writeNodes(w, r, nodes, err)
	}
}

func handleCallees(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		repo, ok := reqRepo(w, r)
		if !ok {
			return
		}
		id, ok := reqIDParam(w, r)
		if !ok {
			return
		}
		nodes, err := cfg.CodeGraph.Callees(r.Context(), repo, id, depthParam(r))
		writeNodes(w, r, nodes, err)
	}
}

func handleDependents(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		repo, ok := reqRepo(w, r)
		if !ok {
			return
		}
		id, ok := reqIDParam(w, r)
		if !ok {
			return
		}
		nodes, err := cfg.CodeGraph.Dependents(r.Context(), repo, id, depthParam(r))
		writeNodes(w, r, nodes, err)
	}
}

func handleDependencies(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		repo, ok := reqRepo(w, r)
		if !ok {
			return
		}
		id, ok := reqIDParam(w, r)
		if !ok {
			return
		}
		nodes, err := cfg.CodeGraph.Dependencies(r.Context(), repo, id, depthParam(r))
		writeNodes(w, r, nodes, err)
	}
}

func handleResourceGraph(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		repo, ok := reqRepo(w, r)
		if !ok {
			return
		}
		id, ok := reqIDParam(w, r)
		if !ok {
			return
		}
		nodes, err := cfg.CodeGraph.ResourceGraph(r.Context(), repo, id, depthParam(r))
		writeNodes(w, r, nodes, err)
	}
}

func handleFileImports(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		repo, ok := reqRepo(w, r)
		if !ok {
			return
		}
		path := r.URL.Query().Get("path")
		if path == "" {
			WriteError(w, http.StatusBadRequest, "path query parameter required", RequestIDFromContext(r.Context()))
			return
		}
		edges, err := cfg.CodeGraph.FileImports(r.Context(), repo, path)
		if err != nil {
			mapServiceError(w, r, err)
			return
		}
		WriteJSON(w, http.StatusOK, map[string]interface{}{"edges": edges})
	}
}
```

The five named-traversal handlers (`handleCallers`, `handleCallees`, `handleDependents`, `handleDependencies`, `handleResourceGraph`) are identical except for the `cfg.CodeGraph.<Method>` call. A shared helper cannot cleanly capture `r.Context()`, so five explicit handlers are the KISS choice here (rule 2) over a context-juggling abstraction.

- [ ] **Step 4: Run test to verify it passes**

Run: `go test ./internal/httpapi/ -run 'TestPostCodeGraph|TestSearchEntities|TestGetEntity|TestCallers|TestNeighbors|TestFileImports'`
Expected: PASS (after Task 8 wires the routes - see note). Since routes are mounted in Task 8, this test will not pass until Task 8 is done. **Do Task 8 before re-running.** Alternatively, fold the Task 8 router edit into this step before running. Recommended: implement Task 8's router changes now, then run this test.

- [ ] **Step 5: Commit** (combined with Task 8)

---

## Task 8: Router wiring for code-graph routes

**Files:**
- Modify: `internal/httpapi/router.go`
- Test: covered by Task 7's `codegraph_test.go` (routes must exist for those tests to pass)

- [ ] **Step 1: Confirm the failing test**

The Task 7 tests fail until routes exist. Use them as this task's test.

Run: `go test ./internal/httpapi/ -run TestPostCodeGraph_OK`
Expected: FAIL - 404 (route not mounted).

- [ ] **Step 2: Write the implementation**

In `internal/httpapi/router.go`, add the field to `Config`:

```go
	CodeGraph  CodeGraphService
```

At the end of `mountV1`, add:

```go
	if cfg.CodeGraph != nil {
		r.Post("/code-graph:bulk", handlePostCodeGraph(cfg))
		r.Get("/code/entities", handleSearchCodeEntities(cfg))
		r.Get("/code/entity", handleGetCodeEntity(cfg))
		r.Get("/code/neighbors", handleNeighbors(cfg))
		r.Get("/code/callers", handleCallers(cfg))
		r.Get("/code/callees", handleCallees(cfg))
		r.Get("/code/dependents", handleDependents(cfg))
		r.Get("/code/dependencies", handleDependencies(cfg))
		r.Get("/code/resource-graph", handleResourceGraph(cfg))
		r.Get("/code/file-imports", handleFileImports(cfg))
	}
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `go test ./internal/httpapi/ -run 'TestPostCodeGraph|TestSearchEntities|TestGetEntity|TestCallers|TestNeighbors|TestFileImports'`
Expected: PASS (all).

Run the full httpapi package: `go test ./internal/httpapi/ -race -count=1`
Expected: PASS.

- [ ] **Step 4: Commit (Tasks 7 + 8 together)**

```bash
git add internal/httpapi/codegraph.go internal/httpapi/codegraph_test.go internal/httpapi/router.go
git commit -m "feat(httpapi): code-graph handlers and routes (/code-graph:bulk, /code/*)"
```

---

## Task 9: Wire codegraph into the app and apply migrations at startup

**Files:**
- Modify: `cmd/tatara-memory/app.go`
- Modify: `cmd/tatara-memory/main.go`
- Test: `cmd/tatara-memory/app_test.go` (add migrate-failure test)

- [ ] **Step 1: Write the failing test**

Append to `cmd/tatara-memory/app_test.go`:

```go
func TestApp_Migrate_FailsOnBadDB(t *testing.T) {
	db, _ := fakeDeps{}.openDB("")
	a := &app{db: db}
	require.Error(t, a.migrate(context.Background()))
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `go test ./cmd/tatara-memory/ -run TestApp_Migrate_FailsOnBadDB`
Expected: FAIL - `a.migrate` undefined.

- [ ] **Step 3: Write the implementation**

In `cmd/tatara-memory/app.go`:

Add imports `"github.com/szymonrychu/tatara-memory/internal/codegraph"` (keep import grouping; goimports will order it).

Add a field to the `app` struct (after `pool *ingest.Pool`):

```go
	cgSvc   *codegraph.Service
```

In `newAppWithDeps`, after the existing `enqueuer := ingest.NewEnqueuer(store)` line, construct the code-graph layer:

```go
	cgStore := codegraph.NewPGStore(db)
	cgMetrics := codegraph.NewMetrics(reg)
	cgSvc := codegraph.NewService(cgStore, cgMetrics)
```

Add `CodeGraph: cgSvc,` to the `httpapi.Config{...}` literal passed to `httpapi.NewRouter`.

Add `cgSvc: cgSvc,` to the returned `&app{...}` literal.

Add the migrate method (anywhere in `app.go`, e.g. after `shutdown`):

```go
// migrate applies all embedded schema migrations. It is idempotent
// (CREATE TABLE IF NOT EXISTS) and runs at startup before serving.
func (a *app) migrate(ctx context.Context) error {
	if err := ingest.Migrate(ctx, a.db); err != nil {
		return fmt.Errorf("migrate ingest schema: %w", err)
	}
	if err := codegraph.Migrate(ctx, a.db); err != nil {
		return fmt.Errorf("migrate codegraph schema: %w", err)
	}
	return nil
}
```

Add `"fmt"` to the `app.go` import block.

In `cmd/tatara-memory/main.go`, in `run()`, after `a, err := newApp(ctx, cfg)` (and its error check) and before the `a.log.Info("starting", ...)` line, add:

```go
	if err := a.migrate(ctx); err != nil {
		return err
	}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `go test ./cmd/tatara-memory/ -run 'TestApp_Migrate_FailsOnBadDB|TestNewApp_WithFakes|TestApp_NewAndShutdown' -race`
Expected: PASS (all - `TestNewApp_WithFakes` still passes because `newAppWithDeps` does not migrate; only `run()` does).

Run the whole binary package: `go test ./cmd/tatara-memory/ -race -count=1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cmd/tatara-memory/app.go cmd/tatara-memory/main.go cmd/tatara-memory/app_test.go
git commit -m "feat: wire code-graph service and apply migrations at startup"
```

---

## Task 10: Full verification, lint, and end-to-end integration test

**Files:**
- Create: `internal/codegraph/e2e_test.go` (integration: HTTP round-trip through the real router + real PG)

- [ ] **Step 1: Write the failing test**

Create `internal/codegraph/e2e_test.go`:

```go
//go:build integration

package codegraph_test

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory/internal/codegraph"
	"github.com/szymonrychu/tatara-memory/internal/httpapi"
)

func TestCodeGraphE2E(t *testing.T) {
	s, _ := freshStore(t)
	svc := codegraph.NewService(s, codegraph.NewMetrics(prometheus.NewRegistry()))

	router := httpapi.NewRouter(httpapi.Config{
		Service:   nil, // memory endpoints unused here
		CodeGraph: svc,
		Registry:  prometheus.NewRegistry(),
	})

	// Push a small graph.
	body := `{"repo":"e2e","files":["a.go","b.go"],
		"entities":[
			{"id":"go:func:e2e/a.A","name":"A","type":"go_func","file_path":"a.go"},
			{"id":"go:func:e2e/b.B","name":"B","type":"go_func","file_path":"b.go"}],
		"edges":[{"from":"go:func:e2e/a.A","to":"go:func:e2e/b.B","relation":"calls","src_file":"a.go"}]}`
	w := httptest.NewRecorder()
	router.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/code-graph:bulk", strings.NewReader(body)))
	require.Equal(t, http.StatusOK, w.Code)

	// Callees of A includes B.
	w = httptest.NewRecorder()
	router.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/code/callees?repo=e2e&id=go:func:e2e/a.A", nil))
	require.Equal(t, http.StatusOK, w.Code)
	require.Contains(t, w.Body.String(), "go:func:e2e/b.B")

	// Callers of B includes A.
	w = httptest.NewRecorder()
	router.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/code/callers?repo=e2e&id=go:func:e2e/b.B", nil))
	require.Equal(t, http.StatusOK, w.Code)
	require.Contains(t, w.Body.String(), "go:func:e2e/a.A")
}
```

Note: `NewRouter` mounts code-graph routes under the auth group only when `Verify` is set; with `Verify` nil, the group runs without auth (the existing pattern for tests). Confirm `mountV1` runs regardless of `Verify`; it does (see router.go - the group calls `mountV1` whether or not `Verify` is set).

- [ ] **Step 2: Run test to verify it fails (then passes)**

Run: `go test -tags integration ./internal/codegraph/ -run TestCodeGraphE2E` (with `TATARA_TEST_PG_DSN`)
Expected: PASS if Tasks 1-9 are correct. If it fails, fix the offending layer.

- [ ] **Step 3: Full suite**

Run unit tests: `go test ./... -race -count=1`
Expected: PASS.

Run integration tests: `go test -tags integration ./internal/codegraph/... ./internal/ingest/...` (with `TATARA_TEST_PG_DSN`)
Expected: PASS.

- [ ] **Step 4: Lint and format**

Run: `gofmt -l . | grep -v '^$'` (expect no output)
Run: `golangci-lint run ./... || [ $? -eq 5 ]`
Expected: 0 issues. Fix any `errcheck`/`revive`/`unused` findings (common: missing doc comment on an exported identifier; an unused helper; an unchecked error).

- [ ] **Step 5: Commit**

```bash
git add internal/codegraph/e2e_test.go
git commit -m "test(codegraph): HTTP-to-Postgres e2e round-trip"
```

---

## Task 11: Update component docs (MEMORY, ROADMAP) and the design spec note

**Files:**
- Modify: `tatara-memory/MEMORY.md`
- Modify: `tatara-memory/ROADMAP.md`
- Modify (parent repo): `docs/superpowers/specs/2026-06-05-tatara-code-ingestion-design.md`

- [ ] **Step 1: Update tatara-memory/MEMORY.md**

Prepend a dated entry:

```
2026-06-06 - Code-graph store added (internal/codegraph). Structural graph
(code_entities/code_edges) lives in this service's Postgres; semantic chunks
stay in LightRAG. POST /code-graph:bulk is SYNCHRONOUS (single bulk tx) returning
200 + counts - deliberately not the async job path, since the only slow work
(embedding) is the existing /memories:bulk the ingester calls separately.
Migrations are now applied at startup in run() via app.migrate (ingest +
codegraph); previously ingest.Migrate was never called in prod (cnpg only
creates the vector extension), so ingest_jobs was an un-migrated latent gap now
fixed. Re-ingest replaces at file granularity (DELETE WHERE repo+file). Orphan
edges (target removed by another file) are filtered at query time via the
entity join. Part of phase-3 sub-project A; contract in parent-repo spec
2026-06-05-tatara-code-ingestion-design.md.
```

- [ ] **Step 2: Update tatara-memory/ROADMAP.md**

Add under a new heading after the existing v1.x sections:

```
## v0.2 - Code graph (phase 3 sub-project A)

**Status:** in progress 2026-06-06.

code_entities/code_edges schema, POST /code-graph:bulk (synchronous,
file-granular replace), GET /code/* traversal (search, entity, neighbors,
callers, callees, dependents, dependencies, resource-graph, file-imports).
Migrations wired at startup. Consumed by tatara-memory-repo-ingester (B) and
tatara-cli code-graph MCP tools (C).
```

- [ ] **Step 3: Update the design spec's async note**

In the parent repo spec `docs/superpowers/specs/2026-06-05-tatara-code-ingestion-design.md`, the "Ingest endpoint" section under sub-project A says the push is async reusing `ingest_jobs`. Replace that with the synchronous decision:

Change "Async, reuses the `ingest_jobs` machinery." to "Synchronous: a single bulk transaction (sub-second), returning 200 with `{repo, files, entities_upserted, edges_upserted}`. No job/poll - the only slow work (embedding) is on the separate `/memories:bulk` the ingester calls for chunks." Adjust the request/response prose accordingly (200 not 202; no IngestJob).

- [ ] **Step 4: Commit**

The tatara-memory docs commit happens in the tatara-memory repo:

```bash
cd tatara-memory
git add MEMORY.md ROADMAP.md
git commit -m "docs: code-graph store (sub-project A) memory + roadmap"
```

The spec note commit happens in the parent repo:

```bash
cd ..
git add docs/superpowers/specs/2026-06-05-tatara-code-ingestion-design.md
git commit -m "docs: code-graph push is synchronous (spec correction)"
```

---

## Final verification checklist (run after all tasks)

- [ ] `cd tatara-memory && go test ./... -race -count=1` passes.
- [ ] `go test -tags integration ./internal/codegraph/... ./internal/ingest/...` passes with `TATARA_TEST_PG_DSN` set.
- [ ] `golangci-lint run ./... || [ $? -eq 5 ]` reports 0 issues.
- [ ] `gofmt -l .` prints nothing.
- [ ] `go build ./...` succeeds.
- [ ] New endpoints are gated by auth (mounted inside the `cfg.Verify` group) and excluded from `/healthz`, `/readyz`, `/metrics`.
- [ ] `code_graph_entities_upserted_total` / `code_graph_edges_upserted_total` appear on `/metrics` after a push.
```
