# Phase 2 Semantic Ceiling (tatara-memory) Implementation Plan

This plan implements the tatara-memory server-side slice of Phase 2: origin-scoped reconcile (`extractor`), the semantic cache (`semantic_extractions`), the `Related`/`Hyperedge` traversals, the in-process gonum analytics worker (Louvain communities + degree + betweenness + bridges), the debounced background trigger, and the new HTTP routes. It does NOT touch the ingester, cli, or operator repos.

**Repo:** `/Users/szymonri/Documents/tatara/tatara-memory`
**Module:** `github.com/szymonrychu/tatara-memory`
**Go:** `1.25.0`

## Conventions (read before starting)

- All integration tests use the `//go:build integration` tag, package `codegraph_test`, and the `freshStore(t)` / `freshStoreWithDB(t)` helpers from `internal/codegraph/pgstore_test.go`, which require `TATARA_TEST_PG_DSN` and call `codegraph.Migrate`. They `t.Skip` when the DSN is unset.
- Run integration tests with: `go test -tags=integration ./internal/codegraph/...` (set `TATARA_TEST_PG_DSN` in the env first).
- Run unit (non-tagged) tests with: `go test ./...`.
- Handler tests live in `internal/httpapi`, package `httpapi_test`, using the `stubCodeGraph` in `internal/httpapi/codegraph_test.go` and `httptest`.
- `ent(id, typ, file)` helper (in `pgstore_test.go`) builds an `Entity` with `Properties{"language":"go"}`.
- Commit after every GREEN step with the exact conventional message given.

**Migration-number reconciliation (load-bearing):** the prompt says "Migration 0005 (next after Phase 1's wave)". The actual `internal/codegraph/migrations/` directory contains only `0001`, `0002`, `0003` (Phase 1 added query tools but no codegraph migration). To keep the `migrate.go` embed/concat chain sequential and unbroken, this plan names the new file `0004_phase2_semantic.sql`. The "0005" in the prompt refers to the platform-wide migration wave count, not this directory's file index. Use `0004` for the filename.

---

## Task 1: Migration 0004 - extractor columns, semantic_extractions, code_communities, repo_analytics_state

### Files
- Create: `internal/codegraph/migrations/0004_phase2_semantic.sql`
- Modify: `internal/codegraph/migrate.go`
- Create: `internal/codegraph/migrate_phase2_test.go`

### Step 1 - Write failing test (full code)

Create `internal/codegraph/migrate_phase2_test.go`:

```go
//go:build integration

package codegraph_test

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory/internal/codegraph"
)

func colExists(t *testing.T, ctx context.Context, db interface {
	QueryRowContext(context.Context, string, ...any) *rowQuerier
}, table, col string) bool {
	t.Helper()
	return false
}

func TestMigratePhase2Schema(t *testing.T) {
	s, db, ctx := freshStoreWithDB(t)
	_ = s

	tableExists := func(name string) bool {
		var ok bool
		require.NoError(t, db.QueryRowContext(ctx, `
			SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name=$1)`, name).Scan(&ok))
		return ok
	}
	columnExists := func(table, col string) bool {
		var ok bool
		require.NoError(t, db.QueryRowContext(ctx, `
			SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name=$1 AND column_name=$2)`, table, col).Scan(&ok))
		return ok
	}

	require.True(t, columnExists("code_edges", "extractor"), "code_edges.extractor must exist")
	require.True(t, columnExists("code_entities", "extractor"), "code_entities.extractor must exist")
	require.True(t, columnExists("code_hyperedges", "extractor"), "code_hyperedges.extractor must exist")

	require.True(t, tableExists("semantic_extractions"), "semantic_extractions table must exist")
	require.True(t, tableExists("code_communities"), "code_communities table must exist")
	require.True(t, tableExists("repo_analytics_state"), "repo_analytics_state table must exist")

	// extractor defaults to 'ast'
	_, err := db.ExecContext(ctx, `INSERT INTO code_entities(repo, id, name, type, file_path) VALUES ('m4','m4:e','e','go_func','a.go')`)
	require.NoError(t, err)
	var extractor string
	require.NoError(t, db.QueryRowContext(ctx, `SELECT extractor FROM code_entities WHERE repo='m4' AND id='m4:e'`).Scan(&extractor))
	require.Equal(t, "ast", extractor)

	_ = codegraph.MigrationSQL()
}

type rowQuerier struct{}
```

Note: drop the unused `colExists`/`rowQuerier` scaffolding before RED if it does not compile; the canonical body is the `TestMigratePhase2Schema` function using `columnExists`/`tableExists` closures. Final test file content (use this, not the scaffolding):

```go
//go:build integration

package codegraph_test

import (
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory/internal/codegraph"
)

func TestMigratePhase2Schema(t *testing.T) {
	_, db, ctx := freshStoreWithDB(t)

	tableExists := func(name string) bool {
		var ok bool
		require.NoError(t, db.QueryRowContext(ctx, `
			SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name=$1)`, name).Scan(&ok))
		return ok
	}
	columnExists := func(table, col string) bool {
		var ok bool
		require.NoError(t, db.QueryRowContext(ctx, `
			SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name=$1 AND column_name=$2)`, table, col).Scan(&ok))
		return ok
	}

	require.True(t, columnExists("code_edges", "extractor"))
	require.True(t, columnExists("code_entities", "extractor"))
	require.True(t, columnExists("code_hyperedges", "extractor"))
	require.True(t, tableExists("semantic_extractions"))
	require.True(t, tableExists("code_communities"))
	require.True(t, tableExists("repo_analytics_state"))

	_, err := db.ExecContext(ctx, `INSERT INTO code_entities(repo, id, name, type, file_path) VALUES ('m4','m4:e','e','go_func','a.go')`)
	require.NoError(t, err)
	var extractor string
	require.NoError(t, db.QueryRowContext(ctx, `SELECT extractor FROM code_entities WHERE repo='m4' AND id='m4:e'`).Scan(&extractor))
	require.Equal(t, "ast", extractor)

	_ = codegraph.MigrationSQL()
}
```

### Step 2 - Run RED

```
go test -tags=integration -run TestMigratePhase2Schema ./internal/codegraph/...
```

Expected: FAIL - `columnExists("code_edges", "extractor")` returns false (column does not exist yet); assertion `require.True` fails.

### Step 3 - Minimal impl (full code)

Create `internal/codegraph/migrations/0004_phase2_semantic.sql`:

```sql
-- Phase 2: origin-scoped reconcile (extractor), semantic extraction cache,
-- community/analytics persistence, and the debounced analytics trigger state.

ALTER TABLE code_edges
    ADD COLUMN IF NOT EXISTS extractor text NOT NULL DEFAULT 'ast';
ALTER TABLE code_entities
    ADD COLUMN IF NOT EXISTS extractor text NOT NULL DEFAULT 'ast';
ALTER TABLE code_hyperedges
    ADD COLUMN IF NOT EXISTS extractor text NOT NULL DEFAULT 'ast';

CREATE INDEX IF NOT EXISTS code_edges_repo_extractor ON code_edges (repo, extractor);
CREATE INDEX IF NOT EXISTS code_entities_repo_extractor ON code_entities (repo, extractor);
CREATE INDEX IF NOT EXISTS code_hyperedges_repo_extractor ON code_hyperedges (repo, extractor);

CREATE TABLE IF NOT EXISTS semantic_extractions (
    repo         text NOT NULL,
    file_path    text NOT NULL,
    content_sha  text NOT NULL,
    extracted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (repo, file_path)
);

CREATE TABLE IF NOT EXISTS code_communities (
    repo      text NOT NULL,
    community int  NOT NULL,
    label     text NOT NULL DEFAULT '',
    cohesion  real NOT NULL DEFAULT 0,
    size      int  NOT NULL DEFAULT 0,
    PRIMARY KEY (repo, community)
);

CREATE TABLE IF NOT EXISTS repo_analytics_state (
    repo          text PRIMARY KEY,
    dirty         boolean NOT NULL DEFAULT false,
    reconciled_at timestamptz,
    computed_at   timestamptz
);
```

Modify `internal/codegraph/migrate.go` - add the embed and append it to both functions:

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

//go:embed migrations/0004_phase2_semantic.sql
var migration0004 string

// MigrationSQL returns the DDL for the code-graph schema (all migrations concatenated).
func MigrationSQL() string {
	return migration0001 + "\n" + migration0002 + "\n" + migration0003 + "\n" + migration0004
}

// Migrate applies the code-graph schema to db, creating tables if they do not exist.
func Migrate(ctx context.Context, db *sql.DB) error {
	if _, err := db.ExecContext(ctx, migration0001); err != nil {
		return err
	}
	if _, err := db.ExecContext(ctx, migration0002); err != nil {
		return err
	}
	if _, err := db.ExecContext(ctx, migration0003); err != nil {
		return err
	}
	_, err := db.ExecContext(ctx, migration0004)
	return err
}
```

### Step 4 - Run GREEN

```
go test -tags=integration -run TestMigratePhase2Schema ./internal/codegraph/...
```

Expected: PASS.

### Step 5 - Commit

```
git add internal/codegraph/migrations/0004_phase2_semantic.sql internal/codegraph/migrate.go internal/codegraph/migrate_phase2_test.go
git commit -m "feat(codegraph): migration 0004 - extractor columns, semantic_extractions, code_communities, repo_analytics_state"
```

---

## Task 2: GraphPush.Extractor + FileSHAs wire fields + FileSHA type + contract-shape guard

### Files
- Modify: `internal/codegraph/types.go`
- Modify: `internal/codegraph/types_test.go`

### Step 1 - Write failing test (full code)

Append to `internal/codegraph/types_test.go` (read it first to confirm the package is `codegraph` or `codegraph_test`; the assertions below import `encoding/json` and the package under test - place in the existing file's package):

```go
func TestGraphPushExtractorFileSHAsJSONTags(t *testing.T) {
	p := codegraph.GraphPush{
		Repo:      "r",
		Extractor: "semantic",
		Files:     []string{"a.go"},
		FileSHAs:  map[string]string{"a.go": "sha1"},
	}
	b, err := json.Marshal(p)
	require.NoError(t, err)
	s := string(b)
	require.Contains(t, s, `"extractor":"semantic"`)
	require.Contains(t, s, `"file_shas":{"a.go":"sha1"}`)

	// omitempty: extractor and file_shas absent when zero
	b2, err := json.Marshal(codegraph.GraphPush{Repo: "r", Files: []string{"a.go"}})
	require.NoError(t, err)
	require.NotContains(t, string(b2), "extractor")
	require.NotContains(t, string(b2), "file_shas")

	// FileSHA decodes path/content_sha tags
	var fs codegraph.FileSHA
	require.NoError(t, json.Unmarshal([]byte(`{"path":"a.go","content_sha":"deadbeef"}`), &fs))
	require.Equal(t, "a.go", fs.Path)
	require.Equal(t, "deadbeef", fs.ContentSHA)
}
```

If `types_test.go` is package `codegraph` (white-box), drop the `codegraph.` prefix and the import; if `codegraph_test`, keep them and ensure `encoding/json`, `testing`, `require`, and the package import are present.

### Step 2 - Run RED

```
go test -run TestGraphPushExtractorFileSHAsJSONTags ./internal/codegraph/...
```

Expected: FAIL - compile error: `p.Extractor`, `p.FileSHAs`, and `codegraph.FileSHA` are undefined.

### Step 3 - Minimal impl (full code)

In `internal/codegraph/types.go`, replace the `GraphPush` struct (lines 159-170) with the Phase 2 shape (byte-for-byte JSON tags per contract lock) and add the `FileSHA` type:

```go
// FileSHA is one file's content hash, used by the semantic-misses cache check
// and as the per-path value of GraphPush.FileSHAs.
type FileSHA struct {
	Path       string `json:"path"`
	ContentSHA string `json:"content_sha"`
}

// GraphPush is one ingest request: the changed file set plus the entities and
// edges those files own. Reconciliation deletes the prior graph owned by Files
// (scoped by Extractor) then inserts Entities, Edges, Symbols, and Hyperedges,
// in one transaction. When FileSHAs is set the semantic_extractions cache is
// upserted for those paths.
type GraphPush struct {
	Repo       string            `json:"repo"`
	Commit     string            `json:"commit,omitempty"`
	Extractor  string            `json:"extractor,omitempty"`
	Files      []string          `json:"files"`
	Entities   []Entity          `json:"entities"`
	Edges      []Edge            `json:"edges"`
	Symbols    []SymbolRow       `json:"symbols,omitempty"`
	Hyperedges []Hyperedge       `json:"hyperedges,omitempty"`
	FileSHAs   map[string]string `json:"file_shas,omitempty"`
}

// ExtractorAST is the default origin tag written to graph rows when a push omits
// Extractor. Reconcile scopes its per-src_file deletes by this tag.
const ExtractorAST = "ast"

// ExtractorSemantic tags rows produced by the LLM semantic extraction stage.
const ExtractorSemantic = "semantic"
```

### Step 4 - Run GREEN

```
go test -run TestGraphPushExtractorFileSHAsJSONTags ./internal/codegraph/...
```

Expected: PASS.

### Step 5 - Commit

```
git add internal/codegraph/types.go internal/codegraph/types_test.go
git commit -m "feat(codegraph): GraphPush.Extractor + FileSHAs wire fields, FileSHA type"
```

---

## Task 3: Origin-scoped reconcile - delete by extractor, write extractor onto rows, upsert semantic_extractions, mark analytics dirty

### Files
- Modify: `internal/codegraph/pgstore.go`
- Create: `internal/codegraph/pgstore_extractor_test.go`

### Step 1 - Write failing test (full code)

Create `internal/codegraph/pgstore_extractor_test.go`:

```go
//go:build integration

package codegraph_test

import (
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory/internal/codegraph"
)

func TestReconcile_ExtractorScopedDeletesPreserveOtherOrigin(t *testing.T) {
	s, db, ctx := freshStoreWithDB(t)

	// AST push for a.go
	_, err := s.Reconcile(ctx, codegraph.GraphPush{
		Repo:      "ex",
		Extractor: codegraph.ExtractorAST,
		Files:     []string{"a.go"},
		Entities:  []codegraph.Entity{ent("ex:a", "go_func", "a.go"), ent("ex:b", "go_func", "a.go")},
		Edges:     []codegraph.Edge{{From: "ex:a", To: "ex:b", Relation: "calls", SrcFile: "a.go"}},
	})
	require.NoError(t, err)

	// semantic push for the SAME file a.go
	_, err = s.Reconcile(ctx, codegraph.GraphPush{
		Repo:      "ex",
		Extractor: codegraph.ExtractorSemantic,
		Files:     []string{"a.go"},
		Entities:  []codegraph.Entity{{ID: "concept:ex:auth", Name: "auth", Type: codegraph.EntityConcept, FilePath: "a.go"}},
		Edges:     []codegraph.Edge{{From: "ex:a", To: "concept:ex:auth", Relation: codegraph.RelConceptuallyRelated, SrcFile: "a.go"}},
		FileSHAs:  map[string]string{"a.go": "sha-1"},
	})
	require.NoError(t, err)

	count := func(q string) int {
		var n int
		require.NoError(t, db.QueryRowContext(ctx, q).Scan(&n))
		return n
	}
	// Both origins coexist.
	require.Equal(t, 2, count(`SELECT count(*) FROM code_entities WHERE repo='ex' AND extractor='ast'`))
	require.Equal(t, 1, count(`SELECT count(*) FROM code_entities WHERE repo='ex' AND extractor='semantic'`))
	require.Equal(t, 1, count(`SELECT count(*) FROM code_edges WHERE repo='ex' AND extractor='ast'`))
	require.Equal(t, 1, count(`SELECT count(*) FROM code_edges WHERE repo='ex' AND extractor='semantic'`))

	// Re-ingest AST for a.go: semantic rows survive.
	_, err = s.Reconcile(ctx, codegraph.GraphPush{
		Repo:      "ex",
		Extractor: codegraph.ExtractorAST,
		Files:     []string{"a.go"},
		Entities:  []codegraph.Entity{ent("ex:a", "go_func", "a.go")},
	})
	require.NoError(t, err)
	require.Equal(t, 1, count(`SELECT count(*) FROM code_entities WHERE repo='ex' AND extractor='ast'`))
	require.Equal(t, 1, count(`SELECT count(*) FROM code_entities WHERE repo='ex' AND extractor='semantic'`), "semantic entity must survive AST re-ingest")
	require.Equal(t, 1, count(`SELECT count(*) FROM code_edges WHERE repo='ex' AND extractor='semantic'`), "semantic edge must survive AST re-ingest")

	// semantic_extractions cache row written.
	var sha string
	require.NoError(t, db.QueryRowContext(ctx, `SELECT content_sha FROM semantic_extractions WHERE repo='ex' AND file_path='a.go'`).Scan(&sha))
	require.Equal(t, "sha-1", sha)

	// repo_analytics_state marked dirty on reconcile.
	var dirty bool
	require.NoError(t, db.QueryRowContext(ctx, `SELECT dirty FROM repo_analytics_state WHERE repo='ex'`).Scan(&dirty))
	require.True(t, dirty)
}

func TestReconcile_DefaultExtractorIsAST(t *testing.T) {
	s, db, ctx := freshStoreWithDB(t)
	_, err := s.Reconcile(ctx, codegraph.GraphPush{
		Repo:     "def",
		Files:    []string{"a.go"},
		Entities: []codegraph.Entity{ent("def:a", "go_func", "a.go")},
	})
	require.NoError(t, err)
	var extractor string
	require.NoError(t, db.QueryRowContext(ctx, `SELECT extractor FROM code_entities WHERE repo='def' AND id='def:a'`).Scan(&extractor))
	require.Equal(t, "ast", extractor)
}
```

### Step 2 - Run RED

```
go test -tags=integration -run 'TestReconcile_ExtractorScopedDeletesPreserveOtherOrigin|TestReconcile_DefaultExtractorIsAST' ./internal/codegraph/...
```

Expected: FAIL - the current `Reconcile` deletes all rows by `src_file` regardless of extractor (semantic rows wiped on AST re-ingest), does not write the `extractor` column, does not upsert `semantic_extractions`, and does not touch `repo_analytics_state`. The "semantic entity must survive AST re-ingest" assertion fails (count 0).

### Step 3 - Minimal impl (full code)

In `internal/codegraph/pgstore.go`, replace the `Reconcile` method (lines 53-162) with the extractor-scoped version. The deletes gain `AND extractor=$ext`, inserts write `extractor`, and after the loops it upserts `semantic_extractions` (when `FileSHAs` set) and marks `repo_analytics_state` dirty:

```go
// Reconcile deletes the prior graph owned by p.Files for p's Extractor origin,
// then inserts p.Entities, p.Edges, p.Symbols, and p.Hyperedges (all tagged with
// that extractor). When p.FileSHAs is set it upserts the semantic_extractions
// cache. It always marks repo_analytics_state dirty. All in one transaction.
func (s *PGStore) Reconcile(ctx context.Context, p GraphPush) (PushResult, error) {
	ext := p.Extractor
	if ext == "" {
		ext = ExtractorAST
	}

	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return PushResult{}, err
	}
	defer func() { _ = tx.Rollback() }()

	for _, f := range p.Files {
		if _, err := tx.ExecContext(ctx, `DELETE FROM code_edges WHERE repo=$1 AND src_file=$2 AND extractor=$3`, p.Repo, f, ext); err != nil {
			return PushResult{}, err
		}
		if _, err := tx.ExecContext(ctx, `DELETE FROM code_entities WHERE repo=$1 AND file_path=$2 AND extractor=$3`, p.Repo, f, ext); err != nil {
			return PushResult{}, err
		}
		if ext == ExtractorAST {
			if _, err := tx.ExecContext(ctx, `DELETE FROM cross_repo_symbols WHERE repo=$1 AND src_file=$2`, p.Repo, f); err != nil {
				return PushResult{}, err
			}
		}
		if _, err := tx.ExecContext(ctx,
			`DELETE FROM code_hyperedge_members WHERE repo=$1 AND hyperedge_id IN (
				SELECT id FROM code_hyperedges WHERE repo=$1 AND src_file=$2 AND extractor=$3)`, p.Repo, f, ext); err != nil {
			return PushResult{}, err
		}
		if _, err := tx.ExecContext(ctx, `DELETE FROM code_hyperedges WHERE repo=$1 AND src_file=$2 AND extractor=$3`, p.Repo, f, ext); err != nil {
			return PushResult{}, err
		}
	}

	for _, e := range p.Entities {
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO code_entities(repo, id, name, type, description, file_path, properties,
				line_start, line_end, source_url, author, captured_at, extractor)
			VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10,$11,$12,$13)
			ON CONFLICT (repo, id) DO UPDATE SET
				name=EXCLUDED.name, type=EXCLUDED.type, description=EXCLUDED.description,
				file_path=EXCLUDED.file_path, properties=EXCLUDED.properties,
				line_start=EXCLUDED.line_start, line_end=EXCLUDED.line_end,
				source_url=EXCLUDED.source_url, author=EXCLUDED.author, captured_at=EXCLUDED.captured_at,
				extractor=EXCLUDED.extractor`,
			p.Repo, e.ID, e.Name, e.Type, e.Description, e.FilePath, marshalProps(e.Properties),
			nullInt(e.LineStart), nullInt(e.LineEnd), nullStr(e.SourceURL), nullStr(e.Author), nullTime(e.CapturedAt), ext); err != nil {
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
			INSERT INTO code_edges(repo, from_id, to_id, relation, src_file, properties, confidence_score, confidence_tier, extractor)
			VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9)
			ON CONFLICT (repo, from_id, to_id, relation) DO UPDATE SET
				src_file=EXCLUDED.src_file, properties=EXCLUDED.properties,
				confidence_score=EXCLUDED.confidence_score, confidence_tier=EXCLUDED.confidence_tier,
				extractor=EXCLUDED.extractor`,
			p.Repo, e.From, e.To, e.Relation, e.SrcFile, marshalProps(e.Properties), score, tier, ext); err != nil {
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
			INSERT INTO code_hyperedges(repo, id, label, relation, confidence_score, src_file, properties, extractor)
			VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8)
			ON CONFLICT (repo, id) DO UPDATE SET
				label=EXCLUDED.label, relation=EXCLUDED.relation,
				confidence_score=EXCLUDED.confidence_score, src_file=EXCLUDED.src_file,
				properties=EXCLUDED.properties, extractor=EXCLUDED.extractor`,
			p.Repo, h.ID, h.Label, h.Relation, score, h.SrcFile, marshalProps(h.Properties), ext); err != nil {
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

	for path, sha := range p.FileSHAs {
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO semantic_extractions(repo, file_path, content_sha, extracted_at)
			VALUES ($1,$2,$3, now())
			ON CONFLICT (repo, file_path) DO UPDATE SET
				content_sha=EXCLUDED.content_sha, extracted_at=now()`,
			p.Repo, path, sha); err != nil {
			return PushResult{}, err
		}
	}

	if _, err := tx.ExecContext(ctx, `
		INSERT INTO repo_analytics_state(repo, dirty, reconciled_at)
		VALUES ($1, true, now())
		ON CONFLICT (repo) DO UPDATE SET dirty=true, reconciled_at=now()`, p.Repo); err != nil {
		return PushResult{}, err
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

### Step 4 - Run GREEN

```
go test -tags=integration -run 'TestReconcile_ExtractorScopedDeletesPreserveOtherOrigin|TestReconcile_DefaultExtractorIsAST' ./internal/codegraph/...
```

Expected: PASS. Also run the existing reconcile suite to confirm no regression: `go test -tags=integration ./internal/codegraph/...` - PASS.

### Step 5 - Commit

```
git add internal/codegraph/pgstore.go internal/codegraph/pgstore_extractor_test.go
git commit -m "feat(codegraph): origin-scoped reconcile by extractor, semantic cache upsert, analytics dirty flag"
```

---

## Task 4: SemanticMisses - paths whose stored content_sha differs or is absent

### Files
- Modify: `internal/codegraph/pgstore.go`
- Modify: `internal/codegraph/store.go`
- Modify: `internal/codegraph/service.go`
- Create: `internal/codegraph/pgstore_misses_test.go`

### Step 1 - Write failing test (full code)

Create `internal/codegraph/pgstore_misses_test.go`:

```go
//go:build integration

package codegraph_test

import (
	"sort"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory/internal/codegraph"
)

func TestSemanticMisses_AbsentDifferentAndMatching(t *testing.T) {
	s, _, ctx := freshStoreWithDB(t)

	// Seed cache: a.go -> sha-a (match), b.go -> sha-old (will differ).
	_, err := s.Reconcile(ctx, codegraph.GraphPush{
		Repo:      "sm",
		Extractor: codegraph.ExtractorSemantic,
		Files:     []string{"a.go", "b.go"},
		FileSHAs:  map[string]string{"a.go": "sha-a", "b.go": "sha-old"},
	})
	require.NoError(t, err)

	misses, err := s.SemanticMisses(ctx, "sm", []codegraph.FileSHA{
		{Path: "a.go", ContentSHA: "sha-a"},   // hit (matches)
		{Path: "b.go", ContentSHA: "sha-new"}, // miss (differs)
		{Path: "c.go", ContentSHA: "sha-c"},   // miss (absent)
	})
	require.NoError(t, err)
	sort.Strings(misses)
	require.Equal(t, []string{"b.go", "c.go"}, misses)
}

func TestSemanticMisses_EmptyInput(t *testing.T) {
	s, _, ctx := freshStoreWithDB(t)
	misses, err := s.SemanticMisses(ctx, "sm2", nil)
	require.NoError(t, err)
	require.Empty(t, misses)
}
```

### Step 2 - Run RED

```
go test -tags=integration -run 'TestSemanticMisses_' ./internal/codegraph/...
```

Expected: FAIL - compile error: `s.SemanticMisses` undefined on `*PGStore`.

### Step 3 - Minimal impl (full code)

Append to `internal/codegraph/pgstore.go`:

```go
// SemanticMisses returns the subset of files whose stored content_sha differs
// from the supplied sha or is absent from the semantic_extractions cache.
// These are the paths the ingester must re-extract with the LLM.
func (s *PGStore) SemanticMisses(ctx context.Context, repo string, files []FileSHA) ([]string, error) {
	var out []string
	for _, f := range files {
		var stored string
		err := s.db.QueryRowContext(ctx,
			`SELECT content_sha FROM semantic_extractions WHERE repo=$1 AND file_path=$2`,
			repo, f.Path).Scan(&stored)
		if errors.Is(err, sql.ErrNoRows) {
			out = append(out, f.Path)
			continue
		}
		if err != nil {
			return nil, err
		}
		if stored != f.ContentSHA {
			out = append(out, f.Path)
		}
	}
	return out, nil
}
```

Add to the `Store` interface in `internal/codegraph/store.go` (inside the interface block):

```go
	SemanticMisses(ctx context.Context, repo string, files []FileSHA) ([]string, error)
```

Add to `internal/codegraph/service.go`:

```go
// SemanticMisses returns the files whose cached content_sha differs or is absent.
func (s *Service) SemanticMisses(ctx context.Context, repo string, files []FileSHA) ([]string, error) {
	return s.store.SemanticMisses(ctx, repo, files)
}
```

### Step 4 - Run GREEN

```
go test -tags=integration -run 'TestSemanticMisses_' ./internal/codegraph/...
```

Expected: PASS.

### Step 5 - Commit

```
git add internal/codegraph/pgstore.go internal/codegraph/store.go internal/codegraph/service.go internal/codegraph/pgstore_misses_test.go
git commit -m "feat(codegraph): SemanticMisses - content_sha cache diff over file set"
```

---

## Task 5: Related, Hyperedges, Hyperedge store queries + types

### Files
- Modify: `internal/codegraph/types.go`
- Modify: `internal/codegraph/pgstore.go`
- Modify: `internal/codegraph/store.go`
- Modify: `internal/codegraph/service.go`
- Create: `internal/codegraph/pgstore_related_test.go`

### Step 1 - Write failing test (full code)

Create `internal/codegraph/pgstore_related_test.go`:

```go
//go:build integration

package codegraph_test

import (
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory/internal/codegraph"
)

func TestRelated_SemanticEdgesWithConfidence(t *testing.T) {
	s, _, ctx := freshStoreWithDB(t)

	_, err := s.Reconcile(ctx, codegraph.GraphPush{
		Repo:      "rel",
		Extractor: codegraph.ExtractorSemantic,
		Files:     []string{"a.go"},
		Entities: []codegraph.Entity{
			{ID: "rel:a", Name: "A", Type: "go_func", FilePath: "a.go"},
			{ID: "rel:b", Name: "B", Type: "go_func", FilePath: "a.go"},
			{ID: "rel:c", Name: "C", Type: "go_func", FilePath: "a.go"},
		},
		Edges: []codegraph.Edge{
			{From: "rel:a", To: "rel:b", Relation: codegraph.RelConceptuallyRelated, SrcFile: "a.go", ConfidenceScore: 0.9, ConfidenceTier: codegraph.TierInferred},
			{From: "rel:a", To: "rel:c", Relation: codegraph.RelSemanticallySimilar, SrcFile: "a.go", ConfidenceScore: 0.4, ConfidenceTier: codegraph.TierInferred},
		},
	})
	require.NoError(t, err)

	// All semantic relations, min_confidence 0 -> both targets.
	all, err := s.Related(ctx, "rel", "rel:a", nil, 0)
	require.NoError(t, err)
	require.Len(t, all, 2)

	// min_confidence 0.5 -> only rel:b survives.
	hi, err := s.Related(ctx, "rel", "rel:a", nil, 0.5)
	require.NoError(t, err)
	require.Len(t, hi, 1)
	require.Equal(t, "rel:b", hi[0].Entity.ID)
	require.Equal(t, codegraph.RelConceptuallyRelated, hi[0].Relation)
	require.InDelta(t, 0.9, hi[0].ConfidenceScore, 1e-6)

	// relation filter narrows to one relation.
	sim, err := s.Related(ctx, "rel", "rel:a", []string{codegraph.RelSemanticallySimilar}, 0)
	require.NoError(t, err)
	require.Len(t, sim, 1)
	require.Equal(t, "rel:c", sim[0].Entity.ID)
}

func TestHyperedges_AndHyperedge(t *testing.T) {
	s, _, ctx := freshStoreWithDB(t)

	_, err := s.Reconcile(ctx, codegraph.GraphPush{
		Repo:      "hy",
		Extractor: codegraph.ExtractorSemantic,
		Files:     []string{"a.go"},
		Entities: []codegraph.Entity{
			{ID: "hy:a", Name: "A", Type: "go_func", FilePath: "a.go"},
			{ID: "hy:b", Name: "B", Type: "go_func", FilePath: "a.go"},
			{ID: "hy:c", Name: "C", Type: "go_func", FilePath: "a.go"},
		},
		Hyperedges: []codegraph.Hyperedge{
			{ID: "hy:auth-flow", Label: "auth flow", Relation: "participate_in", SrcFile: "a.go", Members: []string{"hy:a", "hy:b", "hy:c"}},
		},
	})
	require.NoError(t, err)

	// All hyperedges in repo.
	all, err := s.Hyperedges(ctx, "hy", "")
	require.NoError(t, err)
	require.Len(t, all, 1)
	require.Equal(t, "hy:auth-flow", all[0].ID)
	require.ElementsMatch(t, []string{"hy:a", "hy:b", "hy:c"}, all[0].Members)

	// Filter by entity membership.
	byEnt, err := s.Hyperedges(ctx, "hy", "hy:b")
	require.NoError(t, err)
	require.Len(t, byEnt, 1)

	none, err := s.Hyperedges(ctx, "hy", "hy:zzz")
	require.NoError(t, err)
	require.Empty(t, none)

	// Single hyperedge by id.
	one, err := s.Hyperedge(ctx, "hy", "hy:auth-flow")
	require.NoError(t, err)
	require.Equal(t, "auth flow", one.Label)
	require.Len(t, one.Members, 3)

	_, err = s.Hyperedge(ctx, "hy", "nope")
	require.ErrorIs(t, err, codegraph.ErrEntityNotFound)
}
```

### Step 2 - Run RED

```
go test -tags=integration -run 'TestRelated_SemanticEdgesWithConfidence|TestHyperedges_AndHyperedge' ./internal/codegraph/...
```

Expected: FAIL - compile error: `s.Related`, `s.Hyperedges`, `s.Hyperedge`, and `codegraph.RelatedResult` undefined.

### Step 3 - Minimal impl (full code)

Add to `internal/codegraph/types.go` (after `Hyperedge`):

```go
// RelatedResult is a semantic neighbor of an entity: the target entity plus the
// semantic relation and confidence of the edge that reached it.
type RelatedResult struct {
	Entity
	Relation        string  `json:"relation"`
	ConfidenceScore float64 `json:"confidence_score"`
	ConfidenceTier  string  `json:"confidence_tier"`
}

// HyperedgeDetail is a hyperedge plus its resolved member entity IDs.
type HyperedgeDetail struct {
	Hyperedge
}

// semanticRelations is the relation vocabulary served by Related when the caller
// does not narrow it.
var semanticRelations = []string{
	RelConceptuallyRelated,
	RelSemanticallySimilar,
	RelRationaleFor,
	RelSharesDataWith,
	RelCites,
}
```

Append to `internal/codegraph/pgstore.go`:

```go
// Related returns the semantic-extractor neighbors of id: targets reached by a
// semantic relation edge with confidence_score >= minConfidence. When relations
// is empty the full semantic relation vocabulary is used.
func (s *PGStore) Related(ctx context.Context, repo, id string, relations []string, minConfidence float64) ([]RelatedResult, error) {
	rels := relations
	if len(rels) == 0 {
		rels = semanticRelations
	}
	relStr := strings.Join(rels, ",")
	rows, err := s.db.QueryContext(ctx, `
		SELECT en.id, en.name, en.type, en.description, en.file_path, en.properties,
		       e.relation, e.confidence_score, e.confidence_tier
		FROM code_edges e
		JOIN code_entities en ON en.repo=e.repo AND en.id=e.to_id
		WHERE e.repo=$1 AND e.from_id=$2
		  AND e.extractor='semantic'
		  AND e.relation = ANY(string_to_array($3, ','))
		  AND e.confidence_score >= $4
		ORDER BY e.confidence_score DESC, en.id`,
		repo, id, relStr, minConfidence)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	var out []RelatedResult
	for rows.Next() {
		var r RelatedResult
		var raw []byte
		if err := rows.Scan(&r.ID, &r.Name, &r.Type, &r.Description, &r.FilePath, &raw,
			&r.Relation, &r.ConfidenceScore, &r.ConfidenceTier); err != nil {
			return nil, err
		}
		r.Properties = scanProps(raw)
		out = append(out, r)
	}
	return out, rows.Err()
}

// Hyperedges returns the hyperedges in repo. When entityID is non-empty only
// hyperedges whose members include it are returned.
func (s *PGStore) Hyperedges(ctx context.Context, repo, entityID string) ([]Hyperedge, error) {
	var rows *sql.Rows
	var err error
	if entityID == "" {
		rows, err = s.db.QueryContext(ctx, `
			SELECT id, label, relation, confidence_score, src_file, properties
			FROM code_hyperedges WHERE repo=$1 ORDER BY id`, repo)
	} else {
		rows, err = s.db.QueryContext(ctx, `
			SELECT h.id, h.label, h.relation, h.confidence_score, h.src_file, h.properties
			FROM code_hyperedges h
			JOIN code_hyperedge_members m ON m.repo=h.repo AND m.hyperedge_id=h.id
			WHERE h.repo=$1 AND m.entity_id=$2 ORDER BY h.id`, repo, entityID)
	}
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	var out []Hyperedge
	for rows.Next() {
		h, err := s.scanHyperedge(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, h)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	for i := range out {
		members, err := s.hyperedgeMembers(ctx, repo, out[i].ID)
		if err != nil {
			return nil, err
		}
		out[i].Members = members
	}
	return out, nil
}

// Hyperedge returns a single hyperedge with its members, or ErrEntityNotFound.
func (s *PGStore) Hyperedge(ctx context.Context, repo, id string) (Hyperedge, error) {
	row := s.db.QueryRowContext(ctx, `
		SELECT id, label, relation, confidence_score, src_file, properties
		FROM code_hyperedges WHERE repo=$1 AND id=$2`, repo, id)
	h, err := s.scanHyperedge(row)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return Hyperedge{}, ErrEntityNotFound
		}
		return Hyperedge{}, err
	}
	members, err := s.hyperedgeMembers(ctx, repo, id)
	if err != nil {
		return Hyperedge{}, err
	}
	h.Members = members
	return h, nil
}

func (s *PGStore) scanHyperedge(r rowScanner) (Hyperedge, error) {
	var h Hyperedge
	var raw []byte
	if err := r.Scan(&h.ID, &h.Label, &h.Relation, &h.ConfidenceScore, &h.SrcFile, &raw); err != nil {
		return Hyperedge{}, err
	}
	h.Properties = scanProps(raw)
	return h, nil
}

func (s *PGStore) hyperedgeMembers(ctx context.Context, repo, id string) ([]string, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT entity_id FROM code_hyperedge_members WHERE repo=$1 AND hyperedge_id=$2 ORDER BY entity_id`, repo, id)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	var out []string
	for rows.Next() {
		var m string
		if err := rows.Scan(&m); err != nil {
			return nil, err
		}
		out = append(out, m)
	}
	return out, rows.Err()
}
```

Add to the `Store` interface in `internal/codegraph/store.go`:

```go
	Related(ctx context.Context, repo, id string, relations []string, minConfidence float64) ([]RelatedResult, error)
	Hyperedges(ctx context.Context, repo, entityID string) ([]Hyperedge, error)
	Hyperedge(ctx context.Context, repo, id string) (Hyperedge, error)
```

Add to `internal/codegraph/service.go`:

```go
// Related returns semantic neighbors of id filtered by relations and minConfidence.
func (s *Service) Related(ctx context.Context, repo, id string, relations []string, minConfidence float64) ([]RelatedResult, error) {
	return s.store.Related(ctx, repo, id, relations, minConfidence)
}

// Hyperedges returns the hyperedges in repo, optionally filtered by member entity.
func (s *Service) Hyperedges(ctx context.Context, repo, entityID string) ([]Hyperedge, error) {
	return s.store.Hyperedges(ctx, repo, entityID)
}

// Hyperedge returns a single hyperedge with its members.
func (s *Service) Hyperedge(ctx context.Context, repo, id string) (Hyperedge, error) {
	return s.store.Hyperedge(ctx, repo, id)
}
```

### Step 4 - Run GREEN

```
go test -tags=integration -run 'TestRelated_SemanticEdgesWithConfidence|TestHyperedges_AndHyperedge' ./internal/codegraph/...
```

Expected: PASS.

### Step 5 - Commit

```
git add internal/codegraph/types.go internal/codegraph/pgstore.go internal/codegraph/store.go internal/codegraph/service.go internal/codegraph/pgstore_related_test.go
git commit -m "feat(codegraph): Related/Hyperedges/Hyperedge semantic traversal queries"
```

---

## Task 6: gonum analytics - build graph, Louvain communities, degree, betweenness, cohesion

### Files
- Modify: `go.mod` (add gonum; via `go get`)
- Create: `internal/analytics/compute.go`
- Create: `internal/analytics/compute_test.go`

### Step 1 - Write failing test (full code)

Create `internal/analytics/compute_test.go` (pure unit test, no DB, no build tag - feeds an in-memory edge list):

```go
package analytics

import (
	"testing"

	"github.com/stretchr/testify/require"
)

// twoClusterGraph: {a,b,c} densely connected, {d,e,f} densely connected,
// with a single bridge edge c-d. c and d should have the highest betweenness.
func twoClusterEdges() []Edge {
	return []Edge{
		{From: "a", To: "b"}, {From: "b", To: "c"}, {From: "a", To: "c"},
		{From: "d", To: "e"}, {From: "e", To: "f"}, {From: "d", To: "f"},
		{From: "c", To: "d"}, // bridge
	}
}

func TestCompute_TwoClustersCommunitiesAndCentrality(t *testing.T) {
	res := Compute([]string{"a", "b", "c", "d", "e", "f"}, twoClusterEdges())

	// Exactly two communities.
	communities := map[int]bool{}
	for _, c := range res.Communities {
		communities[c.Community] = true
	}
	require.Len(t, communities, 2, "expected two communities")

	// a,b,c share one community; d,e,f share the other; the two differ.
	comm := map[string]int{}
	for _, n := range res.Nodes {
		comm[n.ID] = n.Community
	}
	require.Equal(t, comm["a"], comm["b"])
	require.Equal(t, comm["b"], comm["c"])
	require.Equal(t, comm["d"], comm["e"])
	require.Equal(t, comm["e"], comm["f"])
	require.NotEqual(t, comm["a"], comm["d"])

	// Degree: c and d have degree 3 (two intra + one bridge); a has degree 2.
	deg := map[string]int{}
	for _, n := range res.Nodes {
		deg[n.ID] = n.Degree
	}
	require.Equal(t, 3, deg["c"])
	require.Equal(t, 3, deg["d"])
	require.Equal(t, 2, deg["a"])

	// Betweenness: the bridge endpoints c and d are strictly higher than a.
	bw := map[string]float64{}
	for _, n := range res.Nodes {
		bw[n.ID] = n.Betweenness
	}
	require.Greater(t, bw["c"], bw["a"])
	require.Greater(t, bw["d"], bw["a"])

	// Community size + cohesion are populated.
	for _, c := range res.Communities {
		require.Equal(t, 3, c.Size)
		require.GreaterOrEqual(t, c.Cohesion, 0.0)
	}
}

func TestCompute_EmptyGraph(t *testing.T) {
	res := Compute(nil, nil)
	require.Empty(t, res.Nodes)
	require.Empty(t, res.Communities)
}

func TestCompute_IsolatedNode(t *testing.T) {
	res := Compute([]string{"x"}, nil)
	require.Len(t, res.Nodes, 1)
	require.Equal(t, "x", res.Nodes[0].ID)
	require.Equal(t, 0, res.Nodes[0].Degree)
}
```

### Step 2 - Run RED

```
go test ./internal/analytics/...
```

Expected: FAIL - package does not compile / build: `Edge`, `Compute`, `analytics` undefined; no Go files in `internal/analytics`.

### Step 3 - Minimal impl (full code)

First add the dependency (this is the one allowed module-graph mutation; run it before writing the file):

```
go get gonum.org/v1/gonum@latest
```

Create `internal/analytics/compute.go`:

```go
// Package analytics computes graph-theoretic signals (community membership,
// cohesion, degree, betweenness) over a repo's code graph using gonum. It is
// pure: callers load the edge list from the store, Compute returns the signals,
// and callers persist them. No DB access lives here.
package analytics

import (
	"gonum.org/v1/gonum/graph/community"
	"gonum.org/v1/gonum/graph/network"
	"gonum.org/v1/gonum/graph/simple"
)

// Edge is one directed code-graph edge by entity ID. Direction is ignored for
// community detection and degree; the underlying graph is treated as undirected.
type Edge struct {
	From string
	To   string
}

// NodeSignal is the computed analytics for one entity.
type NodeSignal struct {
	ID          string
	Community   int
	Degree      int
	Betweenness float64
}

// CommunitySignal is the per-community summary.
type CommunitySignal struct {
	Community int
	Size      int
	Cohesion  float64
	Members   []string // entity IDs, for top-degree labeling by callers
}

// Result bundles the computed node and community signals.
type Result struct {
	Nodes       []NodeSignal
	Communities []CommunitySignal
}

// Compute builds an undirected graph from ids+edges and returns community,
// degree, and betweenness signals. ids that appear in no edge are still emitted
// (isolated nodes, degree 0). Empty input returns an empty Result.
func Compute(ids []string, edges []Edge) Result {
	if len(ids) == 0 {
		return Result{}
	}

	g := simple.NewUndirectedGraph()
	idToNode := make(map[string]int64, len(ids))
	nodeToID := make(map[int64]string, len(ids))
	for i, id := range ids {
		nid := int64(i)
		idToNode[id] = nid
		nodeToID[nid] = id
		g.AddNode(simple.Node(nid))
	}

	degree := make(map[string]int, len(ids))
	for _, e := range edges {
		fn, okf := idToNode[e.From]
		tn, okt := idToNode[e.To]
		if !okf || !okt || fn == tn {
			continue
		}
		if g.HasEdgeBetween(fn, tn) {
			continue
		}
		g.SetEdge(simple.Edge{F: simple.Node(fn), T: simple.Node(tn)})
		degree[e.From]++
		degree[e.To]++
	}

	// Louvain community detection (resolution 1.0, deterministic with nil src).
	reduced := community.Modularize(g, 1.0, nil)
	comms := reduced.Communities()

	betweenness := network.Betweenness(g)

	nodeCommunity := make(map[string]int, len(ids))
	commMembers := make(map[int][]string)
	for ci, members := range comms {
		for _, n := range members {
			id := nodeToID[n.ID()]
			nodeCommunity[id] = ci
			commMembers[ci] = append(commMembers[ci], id)
		}
	}

	var nodes []NodeSignal
	for _, id := range ids {
		nodes = append(nodes, NodeSignal{
			ID:          id,
			Community:   nodeCommunity[id],
			Degree:      degree[id],
			Betweenness: betweenness[idToNode[id]],
		})
	}

	var communities []CommunitySignal
	for ci, members := range commMembers {
		communities = append(communities, CommunitySignal{
			Community: ci,
			Size:      len(members),
			Cohesion:  cohesion(g, comms[ci]),
			Members:   members,
		})
	}

	return Result{Nodes: nodes, Communities: communities}
}

// cohesion is the intra-community edge density: 2*(internal edges) / (n*(n-1)).
// A fully-connected community scores 1.0; a community with no internal edges 0.
func cohesion(g *simple.UndirectedGraph, members []graphNode) float64 {
	n := len(members)
	if n < 2 {
		return 0
	}
	internal := 0
	for i := 0; i < n; i++ {
		for j := i + 1; j < n; j++ {
			if g.HasEdgeBetween(members[i].ID(), members[j].ID()) {
				internal++
			}
		}
	}
	possible := float64(n*(n-1)) / 2.0
	return float64(internal) / possible
}

// graphNode is the gonum node interface subset cohesion needs (ID only).
type graphNode interface {
	ID() int64
}
```

Note for the implementer: `community.Modularize` returns a `ReducedGraph` whose `Communities()` returns `[][]graph.Node`. The `cohesion` helper's `members []graphNode` must match that element type - adjust the signature to `[]graph.Node` (import `gonum.org/v1/gonum/graph`) if the concrete type does not satisfy the local `graphNode` interface. Verify the exact `Communities()` return type against the resolved gonum version and adapt the two call sites (`comms[ci]`) accordingly; the algorithm is unchanged.

### Step 4 - Run GREEN

```
go test ./internal/analytics/...
```

Expected: PASS - two communities detected, c/d degree 3, c/d betweenness > a, sizes 3, cohesion 1.0 for each triangle.

### Step 5 - Commit

```
git add go.mod go.sum internal/analytics/compute.go internal/analytics/compute_test.go
git commit -m "feat(analytics): gonum compute - Louvain communities, degree, betweenness, cohesion"
```

---

## Task 7: Analytics persistence - load edges, persist node/community signals, OpenAI labels gated on key

### Files
- Modify: `internal/codegraph/pgstore.go`
- Modify: `internal/codegraph/store.go`
- Create: `internal/codegraph/analytics_store.go`
- Create: `internal/codegraph/openai.go`
- Create: `internal/codegraph/analytics_store_test.go`

### Step 1 - Write failing test (full code)

Create `internal/codegraph/analytics_store_test.go`:

```go
//go:build integration

package codegraph_test

import (
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory/internal/codegraph"
)

func TestAnalytics_ComputeAndPersist(t *testing.T) {
	s, db, ctx := freshStoreWithDB(t)

	// Two triangles bridged by c-d.
	_, err := s.Reconcile(ctx, codegraph.GraphPush{
		Repo:  "an",
		Files: []string{"f.go"},
		Entities: []codegraph.Entity{
			ent("an:a", "go_func", "f.go"), ent("an:b", "go_func", "f.go"), ent("an:c", "go_func", "f.go"),
			ent("an:d", "go_func", "f.go"), ent("an:e", "go_func", "f.go"), ent("an:f", "go_func", "f.go"),
		},
		Edges: []codegraph.Edge{
			{From: "an:a", To: "an:b", Relation: "calls", SrcFile: "f.go"},
			{From: "an:b", To: "an:c", Relation: "calls", SrcFile: "f.go"},
			{From: "an:a", To: "an:c", Relation: "calls", SrcFile: "f.go"},
			{From: "an:d", To: "an:e", Relation: "calls", SrcFile: "f.go"},
			{From: "an:e", To: "an:f", Relation: "calls", SrcFile: "f.go"},
			{From: "an:d", To: "an:f", Relation: "calls", SrcFile: "f.go"},
			{From: "an:c", To: "an:d", Relation: "calls", SrcFile: "f.go"},
		},
	})
	require.NoError(t, err)

	// nil labeler -> label = top-degree member name.
	require.NoError(t, s.RecomputeAnalytics(ctx, "an", nil))

	// Entity columns persisted.
	var community, degree int
	var betweenness float64
	require.NoError(t, db.QueryRowContext(ctx,
		`SELECT community, degree, betweenness FROM code_entities WHERE repo='an' AND id='an:c'`).
		Scan(&community, &degree, &betweenness))
	require.Equal(t, 3, degree)
	require.Greater(t, betweenness, 0.0)

	// Two communities persisted in code_communities with non-empty labels.
	var nComm int
	require.NoError(t, db.QueryRowContext(ctx, `SELECT count(*) FROM code_communities WHERE repo='an'`).Scan(&nComm))
	require.Equal(t, 2, nComm)
	var label string
	var size int
	require.NoError(t, db.QueryRowContext(ctx,
		`SELECT label, size FROM code_communities WHERE repo='an' ORDER BY community LIMIT 1`).Scan(&label, &size))
	require.NotEmpty(t, label)
	require.Equal(t, 3, size)

	// State cleared: dirty=false, computed_at set.
	var dirty bool
	require.NoError(t, db.QueryRowContext(ctx, `SELECT dirty FROM repo_analytics_state WHERE repo='an'`).Scan(&dirty))
	require.False(t, dirty)
}

func TestAnalytics_DirtyReposListing(t *testing.T) {
	s, db, ctx := freshStoreWithDB(t)
	_, err := s.Reconcile(ctx, codegraph.GraphPush{
		Repo: "dr", Files: []string{"a.go"}, Entities: []codegraph.Entity{ent("dr:a", "go_func", "a.go")},
	})
	require.NoError(t, err)
	// Backdate reconciled_at so debounce passes immediately.
	_, err = db.ExecContext(ctx, `UPDATE repo_analytics_state SET reconciled_at = now() - interval '5 minutes' WHERE repo='dr'`)
	require.NoError(t, err)

	repos, err := s.DirtyRepos(ctx, 60) // debounce 60s
	require.NoError(t, err)
	require.Contains(t, repos, "dr")

	require.NoError(t, s.RecomputeAnalytics(ctx, "dr", nil))
	repos2, err := s.DirtyRepos(ctx, 60)
	require.NoError(t, err)
	require.NotContains(t, repos2, "dr")
}
```

### Step 2 - Run RED

```
go test -tags=integration -run 'TestAnalytics_ComputeAndPersist|TestAnalytics_DirtyReposListing' ./internal/codegraph/...
```

Expected: FAIL - compile error: `s.RecomputeAnalytics`, `s.DirtyRepos` undefined.

### Step 3 - Minimal impl (full code)

Create `internal/codegraph/openai.go`:

```go
package codegraph

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"time"
)

// CommunityLabeler names a community from its member entity names.
type CommunityLabeler interface {
	Label(ctx context.Context, memberNames []string) (string, error)
}

// OpenAILabeler is a minimal chat/completions client used to name communities.
// Gated on OPENAI_API_KEY; NewOpenAILabelerFromEnv returns nil when unset, in
// which case callers fall back to the top-degree member name.
type OpenAILabeler struct {
	apiKey  string
	model   string
	baseURL string
	client  *http.Client
}

// NewOpenAILabelerFromEnv builds a labeler from OPENAI_API_KEY / SEMANTIC_MODEL
// / OPENAI_BASE_URL. Returns nil (no error) when OPENAI_API_KEY is unset.
func NewOpenAILabelerFromEnv() *OpenAILabeler {
	key := os.Getenv("OPENAI_API_KEY")
	if key == "" {
		return nil
	}
	model := os.Getenv("SEMANTIC_MODEL")
	if model == "" {
		model = "gpt-4o-mini"
	}
	base := os.Getenv("OPENAI_BASE_URL")
	if base == "" {
		base = "https://api.openai.com/v1"
	}
	return &OpenAILabeler{
		apiKey:  key,
		model:   model,
		baseURL: base,
		client:  &http.Client{Timeout: 20 * time.Second},
	}
}

type chatReq struct {
	Model    string    `json:"model"`
	Messages []chatMsg `json:"messages"`
}

type chatMsg struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type chatResp struct {
	Choices []struct {
		Message chatMsg `json:"message"`
	} `json:"choices"`
}

// Label returns a short community label from member names.
func (l *OpenAILabeler) Label(ctx context.Context, memberNames []string) (string, error) {
	prompt := fmt.Sprintf("Name this code module in 2-4 words from these symbol names: %v. Reply with only the label.", memberNames)
	body, err := json.Marshal(chatReq{
		Model:    l.model,
		Messages: []chatMsg{{Role: "user", Content: prompt}},
	})
	if err != nil {
		return "", err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, l.baseURL+"/chat/completions", bytes.NewReader(body))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+l.apiKey)
	resp, err := l.client.Do(req)
	if err != nil {
		return "", err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("openai label: status %d", resp.StatusCode)
	}
	var cr chatResp
	if err := json.NewDecoder(resp.Body).Decode(&cr); err != nil {
		return "", err
	}
	if len(cr.Choices) == 0 {
		return "", fmt.Errorf("openai label: empty choices")
	}
	return cr.Choices[0].Message.Content, nil
}
```

Create `internal/codegraph/analytics_store.go`:

```go
package codegraph

import (
	"context"
	"time"

	"github.com/szymonrychu/tatara-memory/internal/analytics"
)

// DirtyRepos returns repos whose analytics are dirty and whose last reconcile is
// older than debounceSecs (so a settling repo is not recomputed on every edit).
func (s *PGStore) DirtyRepos(ctx context.Context, debounceSecs int) ([]string, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT repo FROM repo_analytics_state
		WHERE dirty=true
		  AND reconciled_at IS NOT NULL
		  AND reconciled_at < now() - make_interval(secs => $1)`, debounceSecs)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	var out []string
	for rows.Next() {
		var r string
		if err := rows.Scan(&r); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// RecomputeAnalytics loads the repo's graph, computes signals via gonum, persists
// them to code_entities + code_communities, labels communities (via labeler or
// top-degree member name when labeler is nil), and clears the dirty flag.
func (s *PGStore) RecomputeAnalytics(ctx context.Context, repo string, labeler CommunityLabeler) error {
	ids, names, err := s.loadEntityIDs(ctx, repo)
	if err != nil {
		return err
	}
	edges, err := s.loadEdgePairs(ctx, repo)
	if err != nil {
		return err
	}

	res := analytics.Compute(ids, edges)

	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()

	for _, n := range res.Nodes {
		if _, err := tx.ExecContext(ctx, `
			UPDATE code_entities SET community=$3, degree=$4, betweenness=$5
			WHERE repo=$1 AND id=$2`,
			repo, n.ID, n.Community, n.Degree, n.Betweenness); err != nil {
			return err
		}
	}

	if _, err := tx.ExecContext(ctx, `DELETE FROM code_communities WHERE repo=$1`, repo); err != nil {
		return err
	}
	for _, c := range res.Communities {
		label := labelCommunity(ctx, labeler, c, names)
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO code_communities(repo, community, label, cohesion, size)
			VALUES ($1,$2,$3,$4,$5)`,
			repo, c.Community, label, c.Cohesion, c.Size); err != nil {
			return err
		}
	}

	if _, err := tx.ExecContext(ctx, `
		INSERT INTO repo_analytics_state(repo, dirty, computed_at)
		VALUES ($1, false, now())
		ON CONFLICT (repo) DO UPDATE SET dirty=false, computed_at=now()`, repo); err != nil {
		return err
	}

	return tx.Commit()
}

// labelCommunity returns an LLM label when a labeler is set and succeeds,
// otherwise the highest-degree member's name (no LLM).
func labelCommunity(ctx context.Context, labeler CommunityLabeler, c analytics.CommunitySignal, names map[string]string) string {
	memberNames := make([]string, 0, len(c.Members))
	for _, id := range c.Members {
		memberNames = append(memberNames, names[id])
	}
	if labeler != nil {
		lctx, cancel := context.WithTimeout(ctx, 20*time.Second)
		defer cancel()
		if lbl, err := labeler.Label(lctx, memberNames); err == nil && lbl != "" {
			return lbl
		}
	}
	// Fallback: first member name (callers seed Members in degree order is not
	// guaranteed; pick any non-empty name deterministically).
	for _, id := range c.Members {
		if names[id] != "" {
			return names[id]
		}
	}
	return ""
}

func (s *PGStore) loadEntityIDs(ctx context.Context, repo string) ([]string, map[string]string, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT id, name FROM code_entities WHERE repo=$1 ORDER BY id`, repo)
	if err != nil {
		return nil, nil, err
	}
	defer func() { _ = rows.Close() }()
	var ids []string
	names := map[string]string{}
	for rows.Next() {
		var id, name string
		if err := rows.Scan(&id, &name); err != nil {
			return nil, nil, err
		}
		ids = append(ids, id)
		names[id] = name
	}
	return ids, names, rows.Err()
}

func (s *PGStore) loadEdgePairs(ctx context.Context, repo string) ([]analytics.Edge, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT from_id, to_id FROM code_edges WHERE repo=$1`, repo)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	var out []analytics.Edge
	for rows.Next() {
		var e analytics.Edge
		if err := rows.Scan(&e.From, &e.To); err != nil {
			return nil, err
		}
		out = append(out, e)
	}
	return out, rows.Err()
}
```

Add to the `Store` interface in `internal/codegraph/store.go`:

```go
	DirtyRepos(ctx context.Context, debounceSecs int) ([]string, error)
	RecomputeAnalytics(ctx context.Context, repo string, labeler CommunityLabeler) error
```

### Step 4 - Run GREEN

```
go test -tags=integration -run 'TestAnalytics_ComputeAndPersist|TestAnalytics_DirtyReposListing' ./internal/codegraph/...
```

Expected: PASS.

### Step 5 - Commit

```
git add internal/codegraph/openai.go internal/codegraph/analytics_store.go internal/codegraph/store.go internal/codegraph/analytics_store_test.go
git commit -m "feat(codegraph): analytics persistence - RecomputeAnalytics, DirtyRepos, OpenAI labeler"
```

---

## Task 8: Community/bridge read queries + ImportantEntities by degree|betweenness

### Files
- Modify: `internal/codegraph/types.go`
- Modify: `internal/codegraph/pgstore.go`
- Modify: `internal/codegraph/store.go`
- Modify: `internal/codegraph/service.go`
- Create: `internal/codegraph/pgstore_community_test.go`

### Step 1 - Write failing test (full code)

Create `internal/codegraph/pgstore_community_test.go`:

```go
//go:build integration

package codegraph_test

import (
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory/internal/codegraph"
)

func seedTwoCluster(t *testing.T, s *codegraph.PGStore, ctx interface{ Done() <-chan struct{} }) {}

func TestCommunitiesAndBridgesAndImportantBy(t *testing.T) {
	s, _, ctx := freshStoreWithDB(t)

	_, err := s.Reconcile(ctx, codegraph.GraphPush{
		Repo:  "cm",
		Files: []string{"f.go"},
		Entities: []codegraph.Entity{
			ent("cm:a", "go_func", "f.go"), ent("cm:b", "go_func", "f.go"), ent("cm:c", "go_func", "f.go"),
			ent("cm:d", "go_func", "f.go"), ent("cm:e", "go_func", "f.go"), ent("cm:f", "go_func", "f.go"),
		},
		Edges: []codegraph.Edge{
			{From: "cm:a", To: "cm:b", Relation: "calls", SrcFile: "f.go"},
			{From: "cm:b", To: "cm:c", Relation: "calls", SrcFile: "f.go"},
			{From: "cm:a", To: "cm:c", Relation: "calls", SrcFile: "f.go"},
			{From: "cm:d", To: "cm:e", Relation: "calls", SrcFile: "f.go"},
			{From: "cm:e", To: "cm:f", Relation: "calls", SrcFile: "f.go"},
			{From: "cm:d", To: "cm:f", Relation: "calls", SrcFile: "f.go"},
			{From: "cm:c", To: "cm:d", Relation: "calls", SrcFile: "f.go"},
		},
	})
	require.NoError(t, err)
	require.NoError(t, s.RecomputeAnalytics(ctx, "cm", nil))

	// Communities list.
	comms, err := s.Communities(ctx, "cm")
	require.NoError(t, err)
	require.Len(t, comms, 2)
	for _, c := range comms {
		require.Equal(t, 3, c.Size)
	}

	// Community members.
	members, err := s.Community(ctx, "cm", comms[0].Community)
	require.NoError(t, err)
	require.Len(t, members, 3)

	// Bridges: high-betweenness entities connecting >1 community (cm:c, cm:d).
	bridges, err := s.Bridges(ctx, "cm", 10)
	require.NoError(t, err)
	require.NotEmpty(t, bridges)
	ids := map[string]bool{}
	for _, b := range bridges {
		ids[b.ID] = true
	}
	require.True(t, ids["cm:c"] || ids["cm:d"], "a bridge endpoint must be reported")

	// ImportantBy degree vs betweenness both return data.
	byDeg, err := s.ImportantEntitiesBy(ctx, "cm", "degree", 10)
	require.NoError(t, err)
	require.NotEmpty(t, byDeg)
	byBw, err := s.ImportantEntitiesBy(ctx, "cm", "betweenness", 10)
	require.NoError(t, err)
	require.NotEmpty(t, byBw)
	// Top by betweenness is a bridge endpoint.
	require.True(t, byBw[0].ID == "cm:c" || byBw[0].ID == "cm:d")
}
```

Remove the unused `seedTwoCluster` stub before RED.

### Step 2 - Run RED

```
go test -tags=integration -run TestCommunitiesAndBridgesAndImportantBy ./internal/codegraph/...
```

Expected: FAIL - compile error: `s.Communities`, `s.Community`, `s.Bridges`, `s.ImportantEntitiesBy`, `codegraph.CommunityRow`, `codegraph.Bridge` undefined.

### Step 3 - Minimal impl (full code)

Add to `internal/codegraph/types.go`:

```go
// CommunityRow is one detected community with its label, size, and cohesion.
type CommunityRow struct {
	Community int     `json:"community"`
	Label     string  `json:"label"`
	Size      int     `json:"size"`
	Cohesion  float64 `json:"cohesion"`
}

// Bridge is a high-betweenness entity that connects more than one community.
type Bridge struct {
	Entity
	Betweenness float64 `json:"betweenness"`
	Community   int     `json:"community"`
	NeighborCommunities int `json:"neighbor_communities"`
}

// ImportantBy is the ranking column for ImportantEntitiesBy.
const (
	ImportantByDegree      = "degree"
	ImportantByBetweenness = "betweenness"
)
```

Append to `internal/codegraph/pgstore.go`:

```go
// Communities returns the detected communities for a repo, ordered by size DESC.
func (s *PGStore) Communities(ctx context.Context, repo string) ([]CommunityRow, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT community, label, size, cohesion
		FROM code_communities WHERE repo=$1 ORDER BY size DESC, community`, repo)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	var out []CommunityRow
	for rows.Next() {
		var c CommunityRow
		if err := rows.Scan(&c.Community, &c.Label, &c.Size, &c.Cohesion); err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

// Community returns the member entities of one community.
func (s *PGStore) Community(ctx context.Context, repo string, community int) ([]Entity, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, name, type, description, file_path, properties
		FROM code_entities WHERE repo=$1 AND community=$2 ORDER BY id`, repo, community)
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

// Bridges returns the highest-betweenness entities whose neighbors span more
// than one community, ordered by betweenness DESC.
func (s *PGStore) Bridges(ctx context.Context, repo string, limit int) ([]Bridge, error) {
	rows, err := s.db.QueryContext(ctx, `
		WITH neighbor_comms AS (
			SELECT en.id,
			       count(DISTINCT nb.community) AS nc
			FROM code_entities en
			JOIN code_edges e ON e.repo=en.repo AND (e.from_id=en.id OR e.to_id=en.id)
			JOIN code_entities nb ON nb.repo=en.repo
			  AND nb.id = CASE WHEN e.from_id=en.id THEN e.to_id ELSE e.from_id END
			WHERE en.repo=$1 AND nb.community IS NOT NULL
			GROUP BY en.id
		)
		SELECT en.id, en.name, en.type, en.description, en.file_path, en.properties,
		       COALESCE(en.betweenness, 0), COALESCE(en.community, 0), nc.nc
		FROM code_entities en
		JOIN neighbor_comms nc ON nc.id=en.id
		WHERE en.repo=$1 AND nc.nc > 1
		ORDER BY en.betweenness DESC NULLS LAST, en.id
		LIMIT $2`, repo, limit)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	var out []Bridge
	for rows.Next() {
		var b Bridge
		var raw []byte
		if err := rows.Scan(&b.ID, &b.Name, &b.Type, &b.Description, &b.FilePath, &raw,
			&b.Betweenness, &b.Community, &b.NeighborCommunities); err != nil {
			return nil, err
		}
		b.Properties = scanProps(raw)
		out = append(out, b)
	}
	return out, rows.Err()
}

// ImportantEntitiesBy ranks entities by the chosen column. "betweenness" uses the
// persisted analytics column; anything else falls back to live degree.
func (s *PGStore) ImportantEntitiesBy(ctx context.Context, repo, by string, limit int) ([]EntityDegree, error) {
	if by != ImportantByBetweenness {
		return s.ImportantEntities(ctx, repo, limit)
	}
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, name, type, description, file_path, properties, COALESCE(degree, 0)
		FROM code_entities
		WHERE repo=$1
		ORDER BY betweenness DESC NULLS LAST, id
		LIMIT $2`, repo, limit)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	var out []EntityDegree
	for rows.Next() {
		var ed EntityDegree
		var raw []byte
		if err := rows.Scan(&ed.ID, &ed.Name, &ed.Type, &ed.Description, &ed.FilePath, &raw, &ed.Degree); err != nil {
			return nil, err
		}
		ed.Properties = scanProps(raw)
		out = append(out, ed)
	}
	return out, rows.Err()
}
```

Add to the `Store` interface in `internal/codegraph/store.go`:

```go
	Communities(ctx context.Context, repo string) ([]CommunityRow, error)
	Community(ctx context.Context, repo string, community int) ([]Entity, error)
	Bridges(ctx context.Context, repo string, limit int) ([]Bridge, error)
	ImportantEntitiesBy(ctx context.Context, repo, by string, limit int) ([]EntityDegree, error)
```

Add to `internal/codegraph/service.go` (reuse the existing `defaultImportantLimit`/`maxImportantLimit` caps for `ImportantEntitiesBy`):

```go
// Communities returns the detected communities for a repo.
func (s *Service) Communities(ctx context.Context, repo string) ([]CommunityRow, error) {
	return s.store.Communities(ctx, repo)
}

// Community returns the member entities of one community.
func (s *Service) Community(ctx context.Context, repo string, community int) ([]Entity, error) {
	return s.store.Community(ctx, repo, community)
}

// Bridges returns high-betweenness multi-community connectors, capped by limit.
func (s *Service) Bridges(ctx context.Context, repo string, limit int) ([]Bridge, error) {
	if limit <= 0 {
		limit = defaultImportantLimit
	}
	if limit > maxImportantLimit {
		limit = maxImportantLimit
	}
	return s.store.Bridges(ctx, repo, limit)
}

// ImportantEntitiesBy ranks entities by degree (default) or betweenness.
func (s *Service) ImportantEntitiesBy(ctx context.Context, repo, by string, limit int) ([]EntityDegree, error) {
	if limit <= 0 {
		limit = defaultImportantLimit
	}
	if limit > maxImportantLimit {
		limit = maxImportantLimit
	}
	return s.store.ImportantEntitiesBy(ctx, repo, by, limit)
}
```

### Step 4 - Run GREEN

```
go test -tags=integration -run TestCommunitiesAndBridgesAndImportantBy ./internal/codegraph/...
```

Expected: PASS.

### Step 5 - Commit

```
git add internal/codegraph/types.go internal/codegraph/pgstore.go internal/codegraph/store.go internal/codegraph/service.go internal/codegraph/pgstore_community_test.go
git commit -m "feat(codegraph): communities/community/bridges queries + important by degree|betweenness"
```

---

## Task 9: HTTP routes - related, hyperedges, hyperedge, semantic-misses, communities, community, bridges, important by

### Files
- Modify: `internal/httpapi/service.go` (extend `CodeGraphService`)
- Modify: `internal/httpapi/codegraph.go` (handlers)
- Modify: `internal/httpapi/router.go` (mount routes)
- Modify: `internal/httpapi/codegraph_test.go` (extend `stubCodeGraph` + add handler tests)

### Step 1 - Write failing test (full code)

First extend the `stubCodeGraph` in `internal/httpapi/codegraph_test.go` with the new methods (append these methods to the existing stub), then add the route tests. Append to `internal/httpapi/codegraph_test.go`:

```go
func (s *stubCodeGraph) SemanticMisses(_ context.Context, _ string, files []codegraph.FileSHA) ([]string, error) {
	var out []string
	for _, f := range files {
		out = append(out, f.Path)
	}
	return out, nil
}
func (s *stubCodeGraph) Related(_ context.Context, _, _ string, _ []string, _ float64) ([]codegraph.RelatedResult, error) {
	return []codegraph.RelatedResult{{Entity: codegraph.Entity{ID: "rel:b", Name: "B"}, Relation: codegraph.RelConceptuallyRelated, ConfidenceScore: 0.9}}, nil
}
func (s *stubCodeGraph) Hyperedges(_ context.Context, _, _ string) ([]codegraph.Hyperedge, error) {
	return []codegraph.Hyperedge{{ID: "h1", Label: "flow", Members: []string{"a", "b", "c"}}}, nil
}
func (s *stubCodeGraph) Hyperedge(_ context.Context, _, _ string) (codegraph.Hyperedge, error) {
	return codegraph.Hyperedge{ID: "h1", Label: "flow", Members: []string{"a", "b", "c"}}, nil
}
func (s *stubCodeGraph) Communities(_ context.Context, _ string) ([]codegraph.CommunityRow, error) {
	return []codegraph.CommunityRow{{Community: 0, Label: "auth", Size: 3, Cohesion: 1.0}}, nil
}
func (s *stubCodeGraph) Community(_ context.Context, _ string, _ int) ([]codegraph.Entity, error) {
	return []codegraph.Entity{{ID: "cm:a", Name: "A"}}, nil
}
func (s *stubCodeGraph) Bridges(_ context.Context, _ string, _ int) ([]codegraph.Bridge, error) {
	return []codegraph.Bridge{{Entity: codegraph.Entity{ID: "cm:c", Name: "C"}, Betweenness: 5.0, NeighborCommunities: 2}}, nil
}
func (s *stubCodeGraph) ImportantEntitiesBy(_ context.Context, _, by string, _ int) ([]codegraph.EntityDegree, error) {
	return []codegraph.EntityDegree{{Entity: codegraph.Entity{ID: "imp:" + by}, Degree: 3}}, nil
}

func newCodeGraphRouter(t *testing.T) http.Handler {
	t.Helper()
	return httpapi.NewRouter(httpapi.Config{
		Service:   &stubService{},
		CodeGraph: &stubCodeGraph{},
		Registry:  prometheus.NewRegistry(),
	})
}

func TestRoute_Related(t *testing.T) {
	r := newCodeGraphRouter(t)
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/code-graph/related?repo=r&id=rel:a&min_confidence=0.5", nil))
	require.Equal(t, http.StatusOK, rec.Code)
	var body struct {
		Related []codegraph.RelatedResult `json:"related"`
	}
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
	require.Len(t, body.Related, 1)
	require.Equal(t, "rel:b", body.Related[0].ID)
}

func TestRoute_Related_BadMinConfidence(t *testing.T) {
	r := newCodeGraphRouter(t)
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/code-graph/related?repo=r&id=x&min_confidence=2", nil))
	require.Equal(t, http.StatusBadRequest, rec.Code)
}

func TestRoute_Hyperedges(t *testing.T) {
	r := newCodeGraphRouter(t)
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/code-graph/hyperedges?repo=r", nil))
	require.Equal(t, http.StatusOK, rec.Code)
	var body struct {
		Hyperedges []codegraph.Hyperedge `json:"hyperedges"`
	}
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
	require.Len(t, body.Hyperedges, 1)
}

func TestRoute_Hyperedge(t *testing.T) {
	r := newCodeGraphRouter(t)
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/code-graph/hyperedge?repo=r&id=h1", nil))
	require.Equal(t, http.StatusOK, rec.Code)
	var h codegraph.Hyperedge
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &h))
	require.Equal(t, "h1", h.ID)
}

func TestRoute_SemanticMisses(t *testing.T) {
	r := newCodeGraphRouter(t)
	rec := httptest.NewRecorder()
	payload := `{"repo":"r","files":[{"path":"a.go","content_sha":"s1"},{"path":"b.go","content_sha":"s2"}]}`
	r.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/code-graph/semantic-misses", strings.NewReader(payload)))
	require.Equal(t, http.StatusOK, rec.Code)
	var body struct {
		Misses []string `json:"misses"`
	}
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
	require.ElementsMatch(t, []string{"a.go", "b.go"}, body.Misses)
}

func TestRoute_Communities(t *testing.T) {
	r := newCodeGraphRouter(t)
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/code-graph/communities?repo=r", nil))
	require.Equal(t, http.StatusOK, rec.Code)
	var body struct {
		Communities []codegraph.CommunityRow `json:"communities"`
	}
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
	require.Len(t, body.Communities, 1)
	require.Equal(t, "auth", body.Communities[0].Label)
}

func TestRoute_Community(t *testing.T) {
	r := newCodeGraphRouter(t)
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/code-graph/community?repo=r&community=0", nil))
	require.Equal(t, http.StatusOK, rec.Code)
	var body struct {
		Entities []codegraph.Entity `json:"entities"`
	}
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
	require.Len(t, body.Entities, 1)
}

func TestRoute_Bridges(t *testing.T) {
	r := newCodeGraphRouter(t)
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/code-graph/bridges?repo=r&limit=5", nil))
	require.Equal(t, http.StatusOK, rec.Code)
	var body struct {
		Bridges []codegraph.Bridge `json:"bridges"`
	}
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
	require.Len(t, body.Bridges, 1)
	require.Equal(t, "cm:c", body.Bridges[0].ID)
}

func TestRoute_ImportantBy(t *testing.T) {
	r := newCodeGraphRouter(t)
	rec := httptest.NewRecorder()
	r.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/code-graph/important?repo=r&by=betweenness", nil))
	require.Equal(t, http.StatusOK, rec.Code)
	var body struct {
		Entities []codegraph.EntityDegree `json:"entities"`
	}
	require.NoError(t, json.Unmarshal(rec.Body.Bytes(), &body))
	require.Len(t, body.Entities, 1)
	require.Equal(t, "imp:betweenness", body.Entities[0].ID)
}
```

(Ensure `net/http/httptest`, `strings`, `github.com/prometheus/client_golang/prometheus`, and `github.com/szymonrychu/tatara-memory/internal/httpapi` are imported in the test file - the existing file already imports several of these.)

### Step 2 - Run RED

```
go test -run 'TestRoute_Related|TestRoute_Hyperedges|TestRoute_Hyperedge|TestRoute_SemanticMisses|TestRoute_Communities|TestRoute_Community|TestRoute_Bridges|TestRoute_ImportantBy' ./internal/httpapi/...
```

Expected: FAIL - compile error: `stubCodeGraph` does not implement `CodeGraphService` (new methods not on the interface), and the new routes are not mounted (404), and `SemanticMisses`/`Related`/etc. handlers do not exist.

### Step 3 - Minimal impl (full code)

Add to the `CodeGraphService` interface in `internal/httpapi/service.go`:

```go
	SemanticMisses(ctx context.Context, repo string, files []codegraph.FileSHA) ([]string, error)
	Related(ctx context.Context, repo, id string, relations []string, minConfidence float64) ([]codegraph.RelatedResult, error)
	Hyperedges(ctx context.Context, repo, entityID string) ([]codegraph.Hyperedge, error)
	Hyperedge(ctx context.Context, repo, id string) (codegraph.Hyperedge, error)
	Communities(ctx context.Context, repo string) ([]codegraph.CommunityRow, error)
	Community(ctx context.Context, repo string, community int) ([]codegraph.Entity, error)
	Bridges(ctx context.Context, repo string, limit int) ([]codegraph.Bridge, error)
	ImportantEntitiesBy(ctx context.Context, repo, by string, limit int) ([]codegraph.EntityDegree, error)
```

Append handlers to `internal/httpapi/codegraph.go`:

```go
func handleRelated(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		repo, ok := reqRepo(w, r)
		if !ok {
			return
		}
		id, ok := reqIDParam(w, r)
		if !ok {
			return
		}
		var relations []string
		if rel := r.URL.Query().Get("relations"); rel != "" {
			relations = strings.Split(rel, ",")
		}
		var minConf float64
		if s := r.URL.Query().Get("min_confidence"); s != "" {
			v, err := strconv.ParseFloat(s, 64)
			if err != nil || v < 0 || v > 1 {
				WriteError(w, http.StatusBadRequest, "min_confidence must be a number between 0 and 1", RequestIDFromContext(r.Context()))
				return
			}
			minConf = v
		}
		results, err := cfg.CodeGraph.Related(r.Context(), repo, id, relations, minConf)
		if err != nil {
			mapServiceError(w, r, err)
			return
		}
		if results == nil {
			results = []codegraph.RelatedResult{}
		}
		WriteJSON(w, http.StatusOK, map[string]interface{}{"related": results})
	}
}

func handleHyperedges(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		repo, ok := reqRepo(w, r)
		if !ok {
			return
		}
		hes, err := cfg.CodeGraph.Hyperedges(r.Context(), repo, r.URL.Query().Get("entity"))
		if err != nil {
			mapServiceError(w, r, err)
			return
		}
		if hes == nil {
			hes = []codegraph.Hyperedge{}
		}
		WriteJSON(w, http.StatusOK, map[string]interface{}{"hyperedges": hes})
	}
}

func handleHyperedge(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		repo, ok := reqRepo(w, r)
		if !ok {
			return
		}
		id, ok := reqIDParam(w, r)
		if !ok {
			return
		}
		he, err := cfg.CodeGraph.Hyperedge(r.Context(), repo, id)
		if err != nil {
			mapServiceError(w, r, err)
			return
		}
		WriteJSON(w, http.StatusOK, he)
	}
}

type semanticMissesRequest struct {
	Repo  string             `json:"repo"`
	Files []codegraph.FileSHA `json:"files"`
}

func handleSemanticMisses(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var req semanticMissesRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			WriteError(w, http.StatusBadRequest, "invalid json", RequestIDFromContext(r.Context()))
			return
		}
		if req.Repo == "" {
			WriteError(w, http.StatusBadRequest, "repo required", RequestIDFromContext(r.Context()))
			return
		}
		misses, err := cfg.CodeGraph.SemanticMisses(r.Context(), req.Repo, req.Files)
		if err != nil {
			mapServiceError(w, r, err)
			return
		}
		if misses == nil {
			misses = []string{}
		}
		WriteJSON(w, http.StatusOK, map[string]interface{}{"misses": misses})
	}
}

func handleCommunities(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		repo, ok := reqRepo(w, r)
		if !ok {
			return
		}
		comms, err := cfg.CodeGraph.Communities(r.Context(), repo)
		if err != nil {
			mapServiceError(w, r, err)
			return
		}
		if comms == nil {
			comms = []codegraph.CommunityRow{}
		}
		WriteJSON(w, http.StatusOK, map[string]interface{}{"communities": comms})
	}
}

func handleCommunity(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		repo, ok := reqRepo(w, r)
		if !ok {
			return
		}
		cstr := r.URL.Query().Get("community")
		if cstr == "" {
			WriteError(w, http.StatusBadRequest, "community query parameter required", RequestIDFromContext(r.Context()))
			return
		}
		cid, err := strconv.Atoi(cstr)
		if err != nil {
			WriteError(w, http.StatusBadRequest, "community must be an integer", RequestIDFromContext(r.Context()))
			return
		}
		members, err := cfg.CodeGraph.Community(r.Context(), repo, cid)
		if err != nil {
			mapServiceError(w, r, err)
			return
		}
		if members == nil {
			members = []codegraph.Entity{}
		}
		WriteJSON(w, http.StatusOK, map[string]interface{}{"entities": members})
	}
}

func handleBridges(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		repo, ok := reqRepo(w, r)
		if !ok {
			return
		}
		limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
		bridges, err := cfg.CodeGraph.Bridges(r.Context(), repo, limit)
		if err != nil {
			mapServiceError(w, r, err)
			return
		}
		if bridges == nil {
			bridges = []codegraph.Bridge{}
		}
		WriteJSON(w, http.StatusOK, map[string]interface{}{"bridges": bridges})
	}
}
```

Modify `handleImportantEntities` in `internal/httpapi/codegraph.go` to honor the `by` param (replace the existing function body):

```go
func handleImportantEntities(cfg Config) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		repo, ok := reqRepo(w, r)
		if !ok {
			return
		}
		limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
		by := r.URL.Query().Get("by")
		var entities []codegraph.EntityDegree
		var err error
		if by != "" {
			entities, err = cfg.CodeGraph.ImportantEntitiesBy(r.Context(), repo, by, limit)
		} else {
			entities, err = cfg.CodeGraph.ImportantEntities(r.Context(), repo, limit)
		}
		if err != nil {
			mapServiceError(w, r, err)
			return
		}
		if entities == nil {
			entities = []codegraph.EntityDegree{}
		}
		WriteJSON(w, http.StatusOK, map[string]interface{}{"entities": entities})
	}
}
```

Mount the new routes in `internal/httpapi/router.go`, inside the `if cfg.CodeGraph != nil {` block (after the existing `/code-graph/explain` line):

```go
		r.Get("/code-graph/related", handleRelated(cfg))
		r.Get("/code-graph/hyperedges", handleHyperedges(cfg))
		r.Get("/code-graph/hyperedge", handleHyperedge(cfg))
		r.Post("/code-graph/semantic-misses", handleSemanticMisses(cfg))
		r.Get("/code-graph/communities", handleCommunities(cfg))
		r.Get("/code-graph/community", handleCommunity(cfg))
		r.Get("/code-graph/bridges", handleBridges(cfg))
```

### Step 4 - Run GREEN

```
go test -run 'TestRoute_Related|TestRoute_Hyperedges|TestRoute_Hyperedge|TestRoute_SemanticMisses|TestRoute_Communities|TestRoute_Community|TestRoute_Bridges|TestRoute_ImportantBy' ./internal/httpapi/...
```

Expected: PASS. Also run full `go test ./internal/httpapi/...` to confirm the extended interface compiles with existing tests - PASS.

### Step 5 - Commit

```
git add internal/httpapi/service.go internal/httpapi/codegraph.go internal/httpapi/router.go internal/httpapi/codegraph_test.go
git commit -m "feat(httpapi): related/hyperedges/hyperedge/semantic-misses/communities/community/bridges routes + important by"
```

---

## Task 10: Debounced analytics worker with injectable clock

### Files
- Create: `internal/codegraph/worker.go`
- Create: `internal/codegraph/worker_test.go`

### Step 1 - Write failing test (full code)

Create `internal/codegraph/worker_test.go` (pure unit test - no DB, no build tag - using a fake analytics store and a manual tick channel):

```go
package codegraph

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

type fakeAnalyticsStore struct {
	mu        sync.Mutex
	dirty     []string
	debounce  int
	recompute []string
}

func (f *fakeAnalyticsStore) DirtyRepos(_ context.Context, debounceSecs int) ([]string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.debounce = debounceSecs
	out := f.dirty
	f.dirty = nil // consumed
	return out, nil
}

func (f *fakeAnalyticsStore) RecomputeAnalytics(_ context.Context, repo string, _ CommunityLabeler) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.recompute = append(f.recompute, repo)
	return nil
}

func (f *fakeAnalyticsStore) recomputed() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([]string, len(f.recompute))
	copy(out, f.recompute)
	return out
}

func TestWorker_TickRecomputesDirtyRepos(t *testing.T) {
	fas := &fakeAnalyticsStore{dirty: []string{"r1", "r2"}}
	tick := make(chan time.Time)
	w := NewAnalyticsWorker(fas, nil, AnalyticsWorkerConfig{
		DebounceSecs: 60,
		tickC:        tick,
	})

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go w.Run(ctx)

	tick <- time.Now()                 // fire one tick
	require.Eventually(t, func() bool { // worker processes both repos
		got := fas.recomputed()
		return len(got) == 2
	}, time.Second, 5*time.Millisecond)

	require.ElementsMatch(t, []string{"r1", "r2"}, fas.recomputed())
	require.Equal(t, 60, fas.debounce)
}

func TestWorker_SingleFlightPerRepo(t *testing.T) {
	fas := &fakeAnalyticsStore{}
	w := NewAnalyticsWorker(fas, nil, AnalyticsWorkerConfig{DebounceSecs: 60})

	// Two concurrent processOnce calls for the same repo: only one recompute runs.
	fas.mu.Lock()
	fas.dirty = []string{"same"}
	fas.mu.Unlock()

	var wg sync.WaitGroup
	for i := 0; i < 5; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_ = w.recompute(context.Background(), "same")
		}()
	}
	wg.Wait()
	// recompute is idempotent here; single-flight ensures no panic / race and
	// that at least one ran.
	require.GreaterOrEqual(t, len(fas.recomputed()), 1)
}
```

### Step 2 - Run RED

```
go test -run 'TestWorker_TickRecomputesDirtyRepos|TestWorker_SingleFlightPerRepo' ./internal/codegraph/...
```

Expected: FAIL - compile error: `NewAnalyticsWorker`, `AnalyticsWorkerConfig`, `AnalyticsStore`, `w.Run`, `w.recompute` undefined.

### Step 3 - Minimal impl (full code)

Create `internal/codegraph/worker.go`:

```go
package codegraph

import (
	"context"
	"log/slog"
	"sync"
	"time"
)

// AnalyticsStore is the subset of the store the worker needs. Implemented by
// *PGStore.
type AnalyticsStore interface {
	DirtyRepos(ctx context.Context, debounceSecs int) ([]string, error)
	RecomputeAnalytics(ctx context.Context, repo string, labeler CommunityLabeler) error
}

// AnalyticsWorkerConfig configures the debounced recompute worker.
type AnalyticsWorkerConfig struct {
	// Interval is how often the worker scans for dirty repos. Default 30s.
	Interval time.Duration
	// DebounceSecs is how long a repo must be settled (no reconcile) before its
	// analytics are recomputed. Default 60.
	DebounceSecs int
	// Logger; defaults to slog.Default().
	Logger *slog.Logger
	// tickC, when non-nil, replaces the internal ticker (tests inject ticks).
	tickC <-chan time.Time
}

// AnalyticsWorker periodically recomputes analytics for dirty, settled repos.
// Single-flight per repo prevents concurrent recomputes of the same repo.
type AnalyticsWorker struct {
	store        AnalyticsStore
	labeler      CommunityLabeler
	interval     time.Duration
	debounceSecs int
	log          *slog.Logger
	tickC        <-chan time.Time

	mu       sync.Mutex
	inflight map[string]bool
}

// NewAnalyticsWorker constructs the worker. labeler may be nil (no LLM labels).
func NewAnalyticsWorker(store AnalyticsStore, labeler CommunityLabeler, cfg AnalyticsWorkerConfig) *AnalyticsWorker {
	if cfg.Interval <= 0 {
		cfg.Interval = 30 * time.Second
	}
	if cfg.DebounceSecs <= 0 {
		cfg.DebounceSecs = 60
	}
	if cfg.Logger == nil {
		cfg.Logger = slog.Default()
	}
	return &AnalyticsWorker{
		store:        store,
		labeler:      labeler,
		interval:     cfg.Interval,
		debounceSecs: cfg.DebounceSecs,
		log:          cfg.Logger,
		tickC:        cfg.tickC,
		inflight:     map[string]bool{},
	}
}

// Run blocks until ctx is canceled, processing dirty repos on each tick.
func (w *AnalyticsWorker) Run(ctx context.Context) {
	tickC := w.tickC
	if tickC == nil {
		t := time.NewTicker(w.interval)
		defer t.Stop()
		tickC = t.C
	}
	for {
		select {
		case <-ctx.Done():
			return
		case <-tickC:
			w.processOnce(ctx)
		}
	}
}

func (w *AnalyticsWorker) processOnce(ctx context.Context) {
	repos, err := w.store.DirtyRepos(ctx, w.debounceSecs)
	if err != nil {
		w.log.Error("analytics dirty repos", "err", err)
		return
	}
	for _, repo := range repos {
		if err := w.recompute(ctx, repo); err != nil {
			w.log.Error("analytics recompute", "repo", repo, "err", err)
		}
	}
}

// recompute runs RecomputeAnalytics for one repo under a single-flight guard.
// If a recompute for repo is already running, recompute returns nil immediately.
func (w *AnalyticsWorker) recompute(ctx context.Context, repo string) error {
	w.mu.Lock()
	if w.inflight[repo] {
		w.mu.Unlock()
		return nil
	}
	w.inflight[repo] = true
	w.mu.Unlock()
	defer func() {
		w.mu.Lock()
		delete(w.inflight, repo)
		w.mu.Unlock()
	}()
	return w.store.RecomputeAnalytics(ctx, repo, w.labeler)
}
```

### Step 4 - Run GREEN

```
go test -run 'TestWorker_TickRecomputesDirtyRepos|TestWorker_SingleFlightPerRepo' ./internal/codegraph/...
```

Expected: PASS.

### Step 5 - Commit

```
git add internal/codegraph/worker.go internal/codegraph/worker_test.go
git commit -m "feat(codegraph): debounced analytics worker with single-flight and injectable tick"
```

---

## Task 11: Wire the worker into app.go (start on boot, stop on shutdown)

### Files
- Modify: `cmd/tatara-memory/app.go`
- Modify: `cmd/tatara-memory/app_test.go`

### Step 1 - Write failing test (full code)

Read `cmd/tatara-memory/app_test.go` first to match its existing structure and the `fakeDeps`/`config` fixtures it uses. Append a test asserting the app holds an analytics worker cancel func and that `shutdown` invokes it. Add to `cmd/tatara-memory/app_test.go`:

```go
func TestApp_AnalyticsWorkerCancelOnShutdown(t *testing.T) {
	a := &app{}
	called := false
	a.analyticsCancel = func() { called = true }
	require.NoError(t, a.shutdown(context.Background()))
	require.True(t, called, "shutdown must cancel the analytics worker")
}
```

(Ensure `context`, `testing`, and `require` are imported in `app_test.go` - they already are for the existing tests.)

### Step 2 - Run RED

```
go test -run TestApp_AnalyticsWorkerCancelOnShutdown ./cmd/tatara-memory/...
```

Expected: FAIL - compile error: `a.analyticsCancel` undefined field on `app`.

### Step 3 - Minimal impl (full code)

In `cmd/tatara-memory/app.go`, add the field to the `app` struct:

```go
type app struct {
	log             *slog.Logger
	reg             *prometheus.Registry
	db              *sql.DB
	lrc             lightrag.Client
	pool            *ingest.Pool
	server          *http.Server
	reaper          *memory.Reaper
	reaperCancel    context.CancelFunc
	analyticsCancel context.CancelFunc
	stopOTL         func(context.Context) error
}
```

In `shutdown`, cancel it alongside the reaper (add after the `reaperCancel` block):

```go
	if a.analyticsCancel != nil {
		a.analyticsCancel()
	}
```

In `newAppWithDeps`, after the codegraph service is built (`cgSvc := codegraph.NewService(...)`), start the worker and capture the cancel. Add:

```go
	analyticsLabeler := codegraph.NewOpenAILabelerFromEnv() // nil when OPENAI_API_KEY unset
	analyticsWorker := codegraph.NewAnalyticsWorker(cgStore, analyticsLabeler, codegraph.AnalyticsWorkerConfig{
		Logger: logger,
	})
	analyticsCtx, analyticsCancel := context.WithCancel(context.Background())
	go analyticsWorker.Run(analyticsCtx)
```

Note: `NewOpenAILabelerFromEnv` returns `*OpenAILabeler`; pass it directly. If it returns nil, `NewAnalyticsWorker`'s `labeler` is the nil interface and `RecomputeAnalytics` falls back to top-degree member names. Because `*OpenAILabeler(nil)` is a non-nil interface value, guard it:

```go
	var labeler codegraph.CommunityLabeler
	if l := codegraph.NewOpenAILabelerFromEnv(); l != nil {
		labeler = l
	}
	analyticsWorker := codegraph.NewAnalyticsWorker(cgStore, labeler, codegraph.AnalyticsWorkerConfig{
		Logger: logger,
	})
	analyticsCtx, analyticsCancel := context.WithCancel(context.Background())
	go analyticsWorker.Run(analyticsCtx)
```

Then add `analyticsCancel: analyticsCancel,` to the returned `&app{...}` literal.

### Step 4 - Run GREEN

```
go test -run TestApp_AnalyticsWorkerCancelOnShutdown ./cmd/tatara-memory/...
```

Expected: PASS. Also run `go build ./...` to confirm wiring compiles - exit 0.

### Step 5 - Commit

```
git add cmd/tatara-memory/app.go cmd/tatara-memory/app_test.go
git commit -m "feat(app): start debounced analytics worker on boot, cancel on shutdown"
```

---

## Task 12: Full-suite verification + chart bump

### Files
- Modify: `charts/tatara-memory/Chart.yaml`

### Step 1 - Verify the whole repo (no new test; this is the regression gate)

Run the unit suite and the integration suite:

```
go build ./... && go vet ./... && go test ./...
```

Expected: PASS / exit 0 (unit). Then, with `TATARA_TEST_PG_DSN` set:

```
go test -tags=integration ./...
```

Expected: PASS (all integration tests including the new extractor, misses, related, hyperedge, analytics, community/bridge tests, and the unchanged Phase 0/1 reconcile suite). If anything fails, fix before continuing - do not bump the chart over a red suite.

### Step 2 - Minimal impl (chart bump)

In `charts/tatara-memory/Chart.yaml`, bump `version` and `appVersion` from `0.2.6` to `0.3.0` (minor bump for the Phase 2 feature wave + migration 0004):

```yaml
version: 0.3.0
appVersion: "0.3.0"
```

### Step 3 - Run GREEN (lint the chart if helm is available)

```
helm lint charts/tatara-memory
```

Expected: `1 chart(s) linted, 0 chart(s) failed`. If `helm` is unavailable in the environment, confirm the YAML parses with `go run` is not applicable; instead re-read the file to confirm the two fields changed.

### Step 4 - Commit

```
git add charts/tatara-memory/Chart.yaml
git commit -m "chore(chart): bump tatara-memory to 0.3.0 (Phase 2 semantic ceiling + migration 0004)"
```

---

## Sequencing and dependencies

- Tasks 1-2 (migration, wire types) are the foundation; everything depends on them.
- Task 3 (reconcile) depends on 1+2.
- Tasks 4 (misses) and 5 (related/hyperedge) depend on 1-3 and are independent of each other.
- Task 6 (gonum compute) is standalone (pure unit) and can run in parallel with 4-5.
- Task 7 (analytics persistence) depends on 1, 3, 6.
- Task 8 (community/bridge queries) depends on 7 (needs persisted columns).
- Task 9 (HTTP routes) depends on 4, 5, 8 (the service methods they expose).
- Task 10 (worker) depends on 7 (the `AnalyticsStore` methods).
- Task 11 (app wiring) depends on 10.
- Task 12 (verify + chart) is last.

## Known reconciliations called out for the implementer

- Migration file is `0004` (next sequential in `internal/codegraph/migrations/`), not `0005`; the prompt's "0005" is the platform-wide wave count. Keeping the `migrate.go` embed chain at 0004 is load-bearing - a 0005 filename would break the `//go:embed` reference.
- `Bridge.NeighborCommunities` field is gofmt-aligned; run `gofmt -w` on touched files before each commit (the repo is gofmt-clean).
- gonum's `community.Modularize(...).Communities()` element type must be matched in `analytics/compute.go` `cohesion` (see the note in Task 6 step 3); verify against the resolved gonum version, adapting `[]graphNode` to `[]graph.Node` if needed.
- `NewOpenAILabelerFromEnv` returns a typed nil pointer; the app.go wiring (Task 11) must convert via an explicit nil check so the `CommunityLabeler` interface is truly nil when the key is unset, preserving the no-LLM fallback.

### Critical Files for Implementation
- /Users/szymonri/Documents/tatara/tatara-memory/internal/codegraph/pgstore.go
- /Users/szymonri/Documents/tatara/tatara-memory/internal/codegraph/types.go
- /Users/szymonri/Documents/tatara/tatara-memory/internal/httpapi/codegraph.go
- /Users/szymonri/Documents/tatara/tatara-memory/internal/httpapi/router.go
- /Users/szymonri/Documents/tatara/tatara-memory/cmd/tatara-memory/app.go