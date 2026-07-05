# Phase 2: semantic ceiling (LLM extraction + graph analytics)

Date: 2026-06-09
Status: design
Repos: tatara-memory, tatara-memory-repo-ingester, tatara-cli, tatara-operator
(operator wires the OpenAI key + `semanticIngest` flag into the ingest Job +
the new CRD field)
Depends on: Phase 0 (hyperedge tables, confidence cols, reserved analytics cols)
+ Phase 1 (walk.Change.ContentSHA, query surface).
Decisions: OpenAI gpt-4o-mini via the existing `lightrag-openai` key; ONE combined
build (2a + 2b); analytics runs in-memory (gonum), no separate Job.

This is the build where tatara passes graphify: graphify's full semantic richness
(LLM cross-cutting edges, concept/rationale nodes, hyperedges, communities,
centrality) but server-side, multi-repo, persistent, commit-anchored.

## Guiding invariant (unchanged)

Memory stays 1:1 with the default branch. Semantic edges/nodes/hyperedges and the
analytics signals are derived from the same per-file reconcile; nothing stale.

## Key design point: origin-scoped reconcile (`extractor`)

AST reconcile deletes ALL of a file's edges/entities by `src_file`. If semantic
edges shared that scope, an AST re-ingest of a file would wipe its cached semantic
edges. So every graph row carries an origin tag and reconcile is scoped by it:

- New column `extractor text NOT NULL DEFAULT 'ast'` on `code_edges`,
  `code_entities`, `code_hyperedges` (migration 0004).
- `GraphPush` gains `Extractor string` (empty = 'ast'). Reconcile deletes
  `WHERE repo=$1 AND src_file=ANY(files) AND extractor=$push_extractor`, then
  inserts. AST push (extractor='ast') and semantic push (extractor='semantic')
  reconcile independently. A changed file always re-extracts (its content_sha
  changed -> cache miss), so the two stay consistent; an unchanged file in a full
  pass keeps its semantic rows because the semantic push skips it (cache hit) and
  the AST push only touches extractor='ast' rows.

## 2a. Semantic extraction

### Flow (per ingest, in the ingester)

1. `walk.Diff` -> changed files + `ContentSHA` (Phase 1, already present).
2. AST analyze (existing) -> `GraphPush{Extractor:"ast", ...}` -> POST
   `/code-graph:bulk` (reconcile ast-scoped per src_file). [existing path + the
   extractor field]
3. POST `/code-graph/semantic-misses {repo, files:[{path, content_sha}]}` ->
   memory returns the subset whose stored content_sha differs/absent (cache miss).
   On a normal incremental diff every analyzed file is a miss; on a full/cron pass
   most are hits and skip the LLM.
4. For misses: group files into chunks (token-bounded, ~8 files/chunk) and call
   OpenAI `gpt-4o-mini` (JSON mode) with graphify's `extraction-spec.md` prompt
   verbatim (substituting FILE_LIST/CHUNK_NUM/TOTAL_CHUNKS/CHUNK_PATH; DEEP_MODE
   off). Bounded concurrency (e.g. 4). Parse the JSON fragment.
5. Map the fragment to `contract` types keyed to canonical entity IDs:
   - semantic edges (`semantically_similar_to`, `conceptually_related_to`,
     `rationale_for`, `shares_data_with`, `cites`) -> `Edge{Extractor:"semantic",
     ConfidenceScore, ConfidenceTier}`. Endpoints reference existing AST entity IDs
     where the model names a known symbol; otherwise a concept node id.
   - concept/rationale nodes -> `Entity{Type: concept|rationale, Extractor:
     "semantic"}` with id `concept:<repo>:<slug>` (deterministic from label).
   - hyperedges -> `Hyperedge` (max 3/chunk per the spec).
6. POST `/code-graph:bulk {Extractor:"semantic", Files:<miss files>, Entities,
   Edges, Hyperedges, FileSHAs:{path->sha}}`. Memory reconciles semantic-scoped per
   src_file and upserts `semantic_extractions(repo, file_path, content_sha)`.

### Memory side

- Migration 0004: `extractor` columns (above) + `semantic_extractions(repo,
  file_path, content_sha, extracted_at, PRIMARY KEY(repo,file_path))`.
- `Reconcile`: scope deletes by `extractor`; when `GraphPush.FileSHAs` is set,
  upsert `semantic_extractions`.
- `POST /code-graph/semantic-misses` -> `[]string` (paths needing extraction:
  content_sha != stored or absent).
- `GraphPush.Extractor`/`FileSHAs` on the contract (both repos).
- New traversal: `Related(repo, id, relations, minConfidence)` over semantic
  edges -> `GET /code-graph/related`. `Hyperedges(repo, entityID?)` and
  `Hyperedge(repo, id)` -> `GET /code-graph/hyperedges`, `/code-graph/hyperedge`.

### Ingester side

- New `internal/llm` OpenAI client (chat/completions, JSON mode, model + key +
  base URL from env: `OPENAI_API_KEY` (from lightrag-openai), `SEMANTIC_MODEL`
  default `gpt-4o-mini`, optional `OPENAI_BASE_URL`).
- New `internal/semantic` stage: chunker + prompt builder (loads the verbatim
  extraction-spec template, baked into the binary) + JSON parser -> contract types.
- `run.go`: after the AST push, call semantic-misses, run the semantic stage on
  misses, push semantic GraphPush. Guard: if `SEMANTIC_API_KEY` unset OR the
  Repository opts out, skip the whole stage (AST-only, unchanged behavior).
- Per-Repository opt-out: `Repository.spec.semanticIngest bool` (default true);
  operator passes it as an env to the ingest Job. (Controls LLM cost per repo.)

### cli

- `code_related(id, relations?, min_confidence?, repo?)` -> /code-graph/related
- `code_hyperedges(entity?, repo?)` -> /code-graph/hyperedges
- `code_hyperedge(id, repo?)` -> /code-graph/hyperedge

## 2b. Analytics (in-memory worker)

### Compute

- New `internal/analytics` in tatara-memory using `gonum.org/v1/gonum/graph`:
  build a simple graph from `code_edges` for a repo; run Louvain community
  detection (`graph/community`), compute modularity-based cohesion per community,
  degree (in+out) and betweenness (`graph/network`) per entity.
- Persist: `code_entities.community/cohesion/degree/betweenness` (reserved Phase-0
  cols); `code_communities(repo, community, label, cohesion, size, PRIMARY
  KEY(repo, community))`.
- Labels: one `gpt-4o-mini` call per recompute naming each community from its top
  member names (cheap; reuse the OpenAI client - but analytics is server-side, so
  memory gets its own small OpenAI client gated on the same key; if no key, label
  = top-degree member name, no LLM).

### Trigger (debounced)

- A `repo_analytics_state(repo, dirty bool, reconciled_at, computed_at)` table.
  Every `Reconcile` sets `dirty=true, reconciled_at=now`.
- A background goroutine in the memory service (started in app.go) ticks every
  ~30s: for each dirty repo where `now - reconciled_at > debounce (e.g. 60s)`,
  recompute analytics, set `dirty=false, computed_at=now`. Single-flight per repo.
  Graphs are small (hundreds of nodes = ms), so this is cheap and never blocks
  request serving.

### cli + query

- `code_communities(repo?)` -> /code-graph/communities (list: community, label,
  size, cohesion).
- `code_community(repo, community)` -> /code-graph/community (members).
- `code_bridges(repo?, limit?)` -> /code-graph/bridges (high-betweenness entities
  that connect >1 community).
- `code_important` gains `by` param: `degree` (Phase 1, on-the-fly) | `betweenness`
  (Phase 2, from the persisted column).

## Error handling

- LLM call failure / timeout / malformed JSON: log WARN, skip that chunk's
  semantic edges (AST graph already pushed; semantic is best-effort, never fails
  the ingest). Retries: 1 retry on transient (5xx/429) with backoff.
- semantic-misses with no key / opt-out: stage skipped entirely.
- Analytics compute failure: log ERROR, leave prior columns, retry next tick.
- Determinism: concept node ids are deterministic slugs so re-extraction upserts
  rather than duplicates; hyperedge ids deterministic per (repo, src_file, label).

## Testing (TDD)

- memory: integration - extractor-scoped reconcile keeps semantic rows when AST
  re-ingests the same file and vice versa; semantic_extractions cache hit/miss;
  Related/Hyperedge queries; analytics worker computes community/degree/betweenness
  + code_communities on a seeded graph (deterministic small graph with two clear
  clusters); code_bridges finds the connector. Unit - gonum mapping, debounce
  logic (injected clock), JSON fragment -> contract mapping.
- ingester: unit - chunker groups files within token budget; prompt builder
  substitutes the spec template; JSON parser maps fragment -> contract (semantic
  edges/concept nodes/hyperedges, confidence tiers); semantic stage skipped when
  key unset; OpenAI client with an httptest stub (no real API in tests). The
  extraction-spec prompt is asserted to be embedded verbatim.
- cli: tool-build + Invoke (httptest) for code_related/hyperedges/hyperedge/
  communities/community/bridges + the code_important `by` param; tool-count tests.

## Build / deploy

memory image + chart bump (migration 0004); ingester image (OpenAI client + key
env from lightrag-openai); cli image + wrapper rebuild; operator passes
`semanticIngest` + the OpenAI key env to the ingest Job (operator image + CRD
field bump). Infra: bump memoryImage + ingesterImage pins, wire the
lightrag-openai key into the ingest Job env (operator already has openaiSecretName
= lightrag-openai). Validate live: semantic edges + concept/doc nodes appear,
hyperedges populate, communities/centrality compute, new tools return data.

## Cost / scale notes (record in MEMORY)

- Semantic extraction is LLM-per-changed-file (chunked). First/full ingest of a
  repo is a one-time burst; incremental is only changed files; cron catch-up uses
  the content_sha cache. Per-Repository `semanticIngest` gates cost. gpt-4o-mini
  is ~$0.15/1M input tokens; a repo of a few hundred files is cents.
- Analytics is in-process gonum on small graphs - negligible. If a repo ever
  grows to 10k+ entities, revisit betweenness cost (it is O(V*E)).

## Out of scope

- Image/video/paper extraction (graphify multi-modal). pg_trgm fuzzy search.
- Cross-repo semantic edges (semantic stays within a repo's files for now;
  cross-repo stays the AST provides/requires layer).
