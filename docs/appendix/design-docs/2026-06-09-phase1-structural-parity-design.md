# Phase 1: structural-parity query tools (no LLM)

Date: 2026-06-09
Status: design
Repos: tatara-memory (pgstore + service + httpapi), tatara-cli (MCP tools)
Depends on: Phase 0 (shipped) - confidence columns, line_start/end columns.

## Goal

Close the read-side graphify gaps that need NO LLM and NO analytics job - they
run on the AST data + Phase-0 columns already in prod. Gives agents shortest
paths, importance ranking, corpus stats, confidence-filtered traversal, ranked
search, and explain-in-context. Pure query surface; no schema change, no ingest
change.

## Design decisions (picked)

- **Ranked search without pg_trgm.** cnpg's `app` user cannot `CREATE EXTENSION`,
  and pg_trgm is not installed. Rank with a deterministic CASE expression
  (exact name = 0, name prefix = 1, name substring = 2, description substring =
  3), tie-broken by name. Zero infra dependency. (pg_trgm fuzzy ranking is a
  noted future enhancement, not now.)
- **Degree on-the-fly.** `code_important(by=degree)` computes degree as a SQL
  aggregate over `code_edges` (in+out). The reserved `code_entities.degree`
  column stays empty until the Phase-2 analytics job persists it (betweenness
  needs that job; degree ships now cheaply).
- **Confidence filter is opt-in.** New optional `min_confidence` (float) and
  `tier` (string) params default to no filtering, so existing callers are
  unchanged.

## Components

### tatara-memory

All additions are read-only `PGStore` methods + `Service` passthroughs + httpapi
routes. Follow existing patterns (recursive CTE traversal, `EntityDetail`/
`PathNode` result types).

1. **ShortestPath** - `PGStore.ShortestPath(ctx, repo, fromID, toID, relations
   []string, maxDepth int) ([]Entity, error)`. Recursive CTE from `fromID`
   following `code_edges` (optionally filtered to `relations`), carrying the path
   as an array, stopping when `toID` is reached; `ORDER BY array_length LIMIT 1`.
   Returns the ordered entity chain (empty slice if unreachable within maxDepth,
   not an error). Route `GET /code-graph/path?repo=&from=&to=&relations=&max_depth=`.

2. **ImportantEntities** - `PGStore.ImportantEntities(ctx, repo string, limit
   int) ([]EntityDegree, error)` where `EntityDegree { Entity; Degree int }`.
   `SELECT e.*, (in+out edge counts) AS degree FROM code_entities e ... GROUP BY
   ... ORDER BY degree DESC LIMIT`. Route
   `GET /code-graph/important?repo=&limit=`.

3. **Stats** - `PGStore.Stats(ctx, repo string) (GraphStats, error)` where
   `GraphStats { Entities int; Edges int; EntitiesByType map[string]int;
   EdgesByRelation map[string]int; EdgesByTier map[string]int; IsolatedEntities
   int; ImportCycles int }`. Counts via GROUP BY; isolated = entities with no
   edges (NOT IN src/dst); import cycles via a recursive CTE over `imports`
   edges detecting a back-edge to the start. Route `GET /code-graph/stats?repo=`.

4. **AmbiguousEdges** - `PGStore.AmbiguousEdges(ctx, repo string, limit int)
   ([]Edge, error)`. `WHERE confidence_tier='AMBIGUOUS' OR confidence_score <=
   0.3 ORDER BY confidence_score`. Route `GET /code-graph/ambiguous?repo=&limit=`.

5. **Confidence-filtered traversal.** Add optional `minConfidence float64` and
   `tier string` to the existing neighbor/caller/callee/dependency walk methods.
   When set, the recursive CTE's edge join adds `AND (e.confidence_score >=
   $minConf) AND ($tier='' OR e.confidence_tier=$tier)`. Default zero-value =
   no filter (existing behavior). Thread through service + httpapi as optional
   query params.

6. **Ranked SearchEntities.** Change the `ORDER BY name` in `SearchEntities`
   (pgstore.go:175) to the CASE relevance order above, still honoring the
   `type` filter and `limit`. No signature change.

7. **EntityExplain** - `PGStore.EntityExplain(ctx, repo, id string)
   (EntityExplain, error)` where `EntityExplain { EntityDetail; OutNeighbors
   []Entity; InNeighbors []Entity }` - the entity, its in/out edges (existing
   `EntityDetail`), PLUS the joined neighbor entities (id/name/type/file_path/
   line_start/line_end) so an agent gets labels + citations in one call. Route
   `GET /code-graph/explain?repo=&id=`. (Leaves `code_entity` unchanged;
   explain is the enriched variant.)

### tatara-cli (MCP tools)

Add to `internal/mcp/tools.go` `OperatorTools()`/code tool group (these are
memory-target `code_*` tools). Each maps to a memory route above. JSON schemas
follow the existing `code_*` style.

- `code_path` (from, to, relations?, max_depth?) -> /code-graph/path
- `code_important` (repo?, limit?) -> /code-graph/important
- `code_stats` (repo?) -> /code-graph/stats
- `code_ambiguous_edges` (repo?, limit?) -> /code-graph/ambiguous
- `code_explain` (id, repo?) -> /code-graph/explain
- Extend `code_neighbors`/`code_callers`/`code_callees`/`code_dependencies`/
  `code_dependents` schemas with optional `min_confidence` (number) and `tier`
  (string) params, forwarded as query params.
- `code_search` gains no new params (ranking is server-side); behavior improves.

These are additive; the `contract_shape_test` / tool-count tests update to the
new totals.

## Error handling

- Unknown entity id (path/explain): 404 from memory, surfaced as a clean tool
  error. Unreachable path: 200 with empty chain (not an error).
- Bad `tier` value: ignored (treated as no tier filter) - or validated to the
  3 tiers; pick validation with a 400 to avoid silent no-op. Validate.

## Testing (TDD)

- memory: integration tests (skip without DSN) for each query: a small seeded
  graph asserts ShortestPath returns the chain and `[]` when unreachable;
  ImportantEntities orders by degree; Stats counts + isolated + one import
  cycle; AmbiguousEdges filters by tier/score; confidence-filtered traversal
  drops low-confidence edges; ranked SearchEntities orders exact-before-substring;
  EntityExplain returns neighbor labels + line ranges. Plus unit tests for the
  SQL-builder helpers where pure.
- cli: tool-build + Invoke tests (httptest memory) for each new tool and the new
  confidence params, matching existing `tools_test.go` style; tool-count tests
  updated.

## Build / deploy

memory image + chart bump (no migration - query-only, so a patch bump, e.g.
0.2.6); cli image bump (e.g. 0.4.4) - rebuild wrapper to bundle it. Infra: bump
memoryImage pin; cli reaches agents via the wrapper image (rebuild + Project
agent.image bump). No CRD/schema change.

## Out of scope (Phase 2)

Betweenness/community/centrality persistence + the analytics job; LLM semantic
edges/hyperedges; pg_trgm fuzzy search.
