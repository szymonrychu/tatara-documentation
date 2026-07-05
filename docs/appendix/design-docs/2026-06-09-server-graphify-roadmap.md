# Server-side graphify: parity roadmap (strictly better, never worse)

Date: 2026-06-09
Status: roadmap (organizing doc; per-phase specs follow)
Acceptance bar: tatara must be **strictly better than graphify and in no case
worse** -- every graphify capability and data-model element matched or exceeded,
no feature regressed. Parity is the floor; tatara's server-side, multi-repo,
persistent, commit-anchored, OIDC nature is what puts it ahead.

Source: graphify-parity-analysis workflow (2026-06-09), 5 analysts + synthesis.

## Where tatara already stands

tatara is ~40-50% to parity on the structural axis and near zero on the
discovery/exploration axis. The structural floor is solid and in several ways
**already exceeds graphify**:

- Deterministic, language-aware AST extraction (Go full type-check;
  Python/JS tree-sitter; Terraform HCL2; Helm; Docs) -- equals/exceeds
  graphify's per-language code extraction in precision.
- 20 typed entity types vs graphify's 6 flat file_types; typed directed edges
  (calls/imports/references/depends_on/value_ref/includes/subchart/module_source/...).
- File-granular transactional reconcile; idempotent chunk ingest.
- Directional depth-bounded traversal (recursive CTE), 11 code MCP tools
  (callers/callees/dependents/dependencies/neighbors/resource_graph/...).
- Cross-repo symbol linking (provides/requires) -- graphify lacks this entirely.
- LightRAG semantic RAG (6 query modes).

**tatara advantages over graphify (the "better" axis):** server-side +
persistent (no per-session rebuild, survives across sessions/machines);
multi-repo by construction with cross-repo resolution; canonical type-checked
entity IDs; incremental-by-commit with transactional convergence; OIDC + metrics
+ logging + job tracking; operator/task integration. These are kept and extended,
never traded away.

## What is missing (the ceiling)

The semantic half and the graph-science half of graphify:

1. **LLM semantic edges** (semantically_similar_to, conceptually_related_to,
   rationale_for, shares_data_with, cites) + concept/rationale nodes. tatara's
   DocsAnalyzer is content-only and emits no graph structure -- docs are
   invisible to every `code_*` tool.
2. **Docs as graph nodes** -- cannot walk spec -> implementing code.
3. **Confidence as a first-class, queryable, stratified edge property** (tatara
   computes call confidence but buries it in JSONB, no column/index/filter;
   non-call edges carry none).
4. **N-ary hyperedges** (auth flows, pipelines, patterns spanning repos).
5. **Graph-science signals**: Louvain communities + cohesion, god-nodes
   (degree/betweenness centrality), surprising cross-community bridges,
   knowledge-gap (isolated-node) detection.
6. **Shortest-path A->B**, **explain-node-in-context**, **corpus stats/report**.
7. **Richer provenance**: source_location line ranges, source_url/author/
   captured_at from frontmatter.
8. **Dual ast+semantic hashing** so unchanged files skip LLM re-extraction.

## Three-phase roadmap

### Phase 0 -- SHIPPED 2026-06-09 (build unit 1)

Deployed + validated: tatara-memory 0.2.5, repo-ingester 0.2.4, operator 0.2.13,
wrapper 0.1.9. All 6 repos re-ingest cleanly (full-history clone), per-repo cron
`0 6 * * *`, namespace clone layout live, and the forward-compatible schema is in
prod (confidence cols, hyperedge tables, memory_sources, reserved analytics/
provenance cols). Phase 1 and Phase 2 are the remaining work. Original Phase-0
scope below.

### Phase 0 -- fold into the incremental re-ingest spec NOW (must, before it ships)

Cheap, forward-compatible capture + schema so the ingest path is never rebuilt.
Detailed in `2026-06-09-incremental-reingest-design.md` (Phase 0 section). In
brief: widen GraphPush with SemanticEdges/ConceptNodes/Hyperedges (may be empty);
add `confidence_score`/`confidence_tier` columns to code_edges + reserve
community/cohesion/degree/betweenness/source_url/author/captured_at on
code_entities + create empty code_hyperedges + code_hyperedge_members, all in the
same migration wave; carry content_sha (+ blob) in walk.Changes; make
DocsAnalyzer emit doc entities so doc files reconcile into the code-graph too;
promote Go line_start/line_end to typed columns; extend the 1:1 invariant to
semantic edges, hyperedges, and doc nodes (same per-file purge).

### Phase 1 -- SHIPPED 2026-06-09 (memory 0.2.6, cli 0.4.4, wrapper 0.1.10)

Live-validated on real graph data: `code_path`, `code_important` (degree),
`code_stats`, `code_ambiguous_edges`, `code_explain`, confidence filters on
traversal tools, ranked `code_search`. All read-side on Phase-0 columns; no
schema/ingest change. Phase 2 (semantic ceiling) is the remaining work.

### Phase 1 -- structural parity polish (should; ships on existing AST data, no LLM)

Read-side, needs only Phase-0 columns:
- `code_path(from, to, relations?, max_depth?)` -- shortest path via recursive CTE.
- `code_important(repo, by=degree, limit)` -- degree centrality (pure SQL).
- `code_stats(repo)` -- counts by type/relation/confidence_tier, isolated nodes,
  import-cycle detection.
- confidence filters (`min_confidence`, `tier`) on traversal tools +
  `code_ambiguous_edges(repo)`.
- ranked `code_search` (pg_trgm/tsvector ts_rank).
- enriched `code_entity`/`code_explain` (neighbor labels + line citation).

### Phase 2 -- SHIPPED 2026-06-09 (full graphify parity + beyond)

Deployed + live-validated: OpenAI gpt-4o-mini semantic extraction (graphify
extraction-spec verbatim, content_sha cache, extractor-scoped reconcile so AST +
semantic coexist), concept/rationale nodes, n-ary hyperedges; in-memory gonum
analytics (Louvain communities + cohesion + degree + betweenness, LLM labels).
Tools: code_related, code_hyperedges, code_hyperedge, code_communities,
code_community, code_bridges, code_important by=betweenness. On tatara-cli: 18
semantic edges (cites/semantically_similar_to/shares_data_with), 3 hyperedges, 11
labeled communities, betweenness centrality. Versions: memory 0.3.1, ingester
0.2.6, operator 0.2.14, cli 0.4.5, wrapper 0.1.11.

**Acceptance bar met: tatara is strictly better than graphify** -- every graphify
capability (semantic edges, concept/rationale nodes, hyperedges, communities,
centrality, confidence, provenance) is matched, AND tatara adds server-side,
multi-repo, persistent, commit-anchored, OIDC, incremental-by-commit operation
that graphify (a local per-session skill) has no analogue for.

3 deploy-discovered bugs fixed (best-effort kept AST ingest unbroken throughout):
empty-changeset 400 (Phase-1 carry); semantic-misses wire shape (memory returned
{misses:[]}, ingester+lock wanted bare []string); LLM call reused the memory
client's OIDC transport, poisoning the OpenAI request (invalid_issuer) -- fixed to
a plain HTTP client. Lesson: per-repo tests each mocked their own wire assumption,
so cross-repo shape mismatches only surfaced at deploy; pin exact JSON in the lock
and add a cross-repo contract test.

### Phase 2 -- the semantic ceiling (must for true parity; the heavy lift, where tatara overtakes)

- **Semantic-extraction stage** in the ingester reusing graphify's
  `extraction-spec.md` prompt verbatim, per changed-file chunk, cached by
  `(repo, file_path, content_sha)`, emitting semantic edges + concept/rationale/
  doc edges + hyperedges keyed to canonical entity IDs, reconciled per src_file.
  New tools `code_related`, `code_hyperedges`, `code_hyperedge`.
- **Post-reingest analytics job** (debounced after a successful incremental
  ingest): Louvain communities + cohesion + degree/betweenness + LLM cluster
  labels, populating the reserved columns. New tools `code_communities`,
  `code_community`, `code_bridges`, betweenness in `code_important`. Global, not
  per-file -- a downstream job owns the expensive passes; the re-ingest path
  stays cheap and file-granular.

End state: graphify's full semantic richness, but server-side, multi-repo,
persistent, commit-anchored, authenticated -- strictly better.

## Sequencing

Phase 0 ships inside the incremental re-ingest work (a few hundred lines, mostly
schema + contract, folded into edits already happening). Phase 1 right after
(high agent value, low cost, no LLM). Phase 2 is the parity investment (LLM cost
+ analytics infra). Namespace-preserving clone layout
(`2026-06-09-namespace-clone-layout-notes.md`) is orthogonal and can land
alongside Phase 0/1.

## Not doing (YAGNI / deferred)

- Query-result persistence / save-result feedback loop (low value until semantic
  search over saved answers exists; defer).
- Image/video/paper extraction (graphify multi-format) -- out of scope for a
  code+docs knowledge graph; revisit only if a real need appears.
