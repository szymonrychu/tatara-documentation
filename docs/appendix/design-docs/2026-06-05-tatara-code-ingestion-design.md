# Tatara Code Ingestion - Design

**Date:** 2026-06-05
**Status:** design approved (forks pinned), pending spec review
**Spans:** `tatara-memory` (A), `tatara-memory-repo-ingester` (B, new phase-3 repo), `tatara-cli` (C)

## Problem

We want to ingest a whole code repository into tatara-memory so agents can ask
both *structural* questions ("who calls `Server.Run`?", "what does this
Terraform resource depend on?", "which templates read `image.repository`?") and
*semantic* questions ("where is auth handled?"). Naive ingestion - dumping files
as text and letting LightRAG's LLM guess a graph - does not give precise,
deterministic links. The ingester must understand Go, Python, JavaScript,
Terraform and Helm well enough to emit the real links between files, and
tatara-memory must store and expose that structure.

## Approved decisions (forks)

1. **Placement:** dedicated repo `tatara-memory-repo-ingester` (phase 3). Keeps
   heavy analysis deps out of the thin `tatara-cli`; runnable as an in-cluster
   Job or CI step.
2. **Contract:** hybrid - a deterministic entity+edge graph **plus** enriched
   semantic text chunks.
3. **Analysis backend:** best-per-language (precise), not a single tree-sitter
   pass.
4. **MCP surface:** dedicated code-graph tools.
5. **Storage:** the structural graph lives in **tatara-memory's own Postgres**
   (not LightRAG's graph). Semantic chunks go to LightRAG as today. The two are
   linked by canonical entity ID stored in each chunk's metadata.
6. **Re-ingest reconciliation:** replace at **file granularity**. Re-ingesting a
   repo pushes only changed files; tatara-memory deletes and reinserts exactly
   the graph owned by those files. No full wipe.
7. **Modularity is a hard constraint.** Adding a language must touch **only**
   sub-project B. The contract is language-neutral; A and C never change to gain
   a language.

## Why Postgres for the structural graph (not LightRAG)

- LightRAG keys entities by **name**; code symbols collide (`New`, `Run`,
  `Handler`). We must qualify names regardless.
- LightRAG's graph create API is one-entity / one-relation **per HTTP call** -
  thousands of calls per repo.
- LightRAG runs its own LLM extraction that would **merge/mangle** deterministic
  code nodes - the non-determinism we are rejecting.
- Per-file delete-and-replace is awkward in LightRAG, trivial with a
  `(repo, file_path)` predicate in SQL.
- Postgres gives deterministic bulk upsert and precise **recursive-CTE
  traversal** (callers/dependents to depth N).

LightRAG stays the vector/semantic store over enriched text. Best of both,
joined by ID.

---

## The contract (shared by A, B, C)

The contract is intentionally language-neutral. A language is just an ID prefix
and a set of type/relation string constants the analyzer emits.

### Canonical entity ID

`<lang>:<kind>:<fully-qualified-name>`

| Example | Meaning |
| --- | --- |
| `go:func:github.com/szymonrychu/tatara-cli/internal/mcp.NewServer` | Go function |
| `go:method:github.com/szymonrychu/tatara-cli/internal/mcp.(*Server).Run` | Go method |
| `go:type:github.com/szymonrychu/tatara-cli/internal/mcp.Server` | Go type |
| `go:package:github.com/szymonrychu/tatara-cli/internal/mcp` | Go package |
| `py:class:tatara_cli.ingest.Walker` | Python class |
| `py:func:tatara_cli.ingest.walk` | Python function |
| `py:module:tatara_cli.ingest` | Python module |
| `js:func:web/src/app.js::handleClick` | JavaScript function |
| `js:class:web/src/app.js::Widget` | JavaScript class |
| `js:module:web/src/app.js` | JavaScript module |
| `tf:resource:keycloak_openid_client.tatara_chat` | Terraform resource |
| `tf:module:tatara` | Terraform module call |
| `tf:variable:region` | Terraform variable |
| `tf:output:client_secret` | Terraform output |
| `helm:chart:tatara-chat` | Helm chart |
| `helm:template:tatara-chat/templates/deployment.yaml` | Helm template |
| `helm:value:tatara-chat.image.repository` | Helm value key |
| `file:tatara-cli/internal/mcp/server.go` | Source file |
| `repo:tatara-cli` | Repo root |

IDs are stable across commits for unchanged symbols (no line numbers in the ID).

### Entity

```
id          string   canonical ID (above) - primary key within (repo)
name        string   short display name (e.g. "Run", "tatara_chat")
type        string   entity type constant (below)
description string   short human summary (signature/kind line)
repo        string   owning repo (e.g. "tatara-cli")
file_path   string   repo-relative source path that OWNS this entity ("" for repo node)
properties  map[string]string  language, line_start, line_end, signature, exported, ...
```

Entity types: `repo, file, go_package, go_type, go_func, go_method,
py_module, py_class, py_func, js_module, js_class, js_func, tf_resource,
tf_data, tf_module, tf_variable, tf_output, helm_chart, helm_template,
helm_value`.

Each analyzer owns its own fully-qualified-name scheme (Go uses package import
paths, Python dotted modules, JavaScript repo-relative module paths). The store
treats IDs as opaque strings - this is why a new language never touches A.

### Edge

```
from        string   source entity ID
to          string   target entity ID
relation    string   relation constant (below)
repo        string   owning repo
src_file    string   the file that OWNS this edge (where the reference originates)
properties  map[string]string  count, line, kind, ...
```

Edge relations: `contains, defines, imports, calls, references, implements,
depends_on, module_source, var_ref, output_ref, value_ref, includes, subchart`.

**Edge ownership:** an edge is owned by the file where the reference originates
(`src_file`). When a file changes, exactly its outgoing edges are replaced.
Edges whose `to` target was removed/renamed by another file's change become
orphaned; queries filter dangling targets (`JOIN` drops them). This is the
accepted tradeoff for cheap file-granular replace.

### Semantic chunk

One per documentable unit (Go func/type/file, py class/func/module, tf resource,
helm template, markdown/doc section). Shape pushed to existing `/memories:bulk`:

```
idempotency_key = "<repo>:<entity_id>:<content_hash>"
text            = structural header + "\n---\n" + source body
metadata        = {repo, entity_id, type, file_path, language}
```

Structural header example:

```
[go_method] github.com/szymonrychu/tatara-cli/internal/mcp.(*Server).Run
file: internal/mcp/server.go:42-58
package: github.com/szymonrychu/tatara-cli/internal/mcp
calls: server.ServeStdio
signature: func (s *Server) Run(ctx context.Context) error
```

`entity_id` in metadata is the join key from a semantic hit back to the graph.

---

## Sub-project A - tatara-memory (the foundation)

Implemented first; B and C depend on its API. Adds a structural code-graph store
and query surface to the existing service. Existing memory/entity/edge/query
endpoints are untouched.

### Schema (new migration, `internal/codegraph/migrations/0001_codegraph.sql`)

```sql
CREATE TABLE code_entities (
    repo        text NOT NULL,
    id          text NOT NULL,            -- canonical entity ID
    name        text NOT NULL,
    type        text NOT NULL,
    description text NOT NULL DEFAULT '',
    file_path   text NOT NULL DEFAULT '',
    properties  jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (repo, id)
);
CREATE INDEX code_entities_repo_file ON code_entities (repo, file_path);
CREATE INDEX code_entities_type ON code_entities (repo, type);
CREATE INDEX code_entities_name ON code_entities (repo, name);

CREATE TABLE code_edges (
    repo       text NOT NULL,
    from_id    text NOT NULL,
    to_id      text NOT NULL,
    relation   text NOT NULL,
    src_file   text NOT NULL DEFAULT '',
    properties jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (repo, from_id, to_id, relation)
);
CREATE INDEX code_edges_to ON code_edges (repo, to_id, relation);
CREATE INDEX code_edges_src_file ON code_edges (repo, src_file);
```

Reuses the existing embedded-SQL `Migrate(ctx, db)` pattern (not goose).

### Ingest endpoint

`POST /code-graph:bulk` (OIDC-gated, audience `tatara-memory`). Synchronous: a
single bulk transaction (sub-second), since a graph push needs no job/poll
machinery. The only slow work (embedding) is on the separate `/memories:bulk`
the ingester calls for chunks. Request:

```json
{
  "repo": "tatara-cli",
  "commit": "abc123",
  "files": ["internal/mcp/server.go", "internal/mcp/tools.go"],
  "entities": [ { "id": "...", "name": "...", "type": "...", "description": "...",
                  "file_path": "...", "properties": {} } ],
  "edges": [ { "from": "...", "to": "...", "relation": "...", "src_file": "...",
               "properties": {} } ]
}
```

`files` is the set of changed files this push covers. Reconciliation, in one
transaction:

```
DELETE FROM code_edges    WHERE repo=$1 AND src_file = ANY($files);
DELETE FROM code_entities WHERE repo=$1 AND file_path = ANY($files);
-- bulk INSERT entities (ON CONFLICT (repo,id) DO UPDATE)
-- bulk INSERT edges    (ON CONFLICT (repo,from_id,to_id,relation) DO UPDATE)
```

Entities/edges in the push must have any *non-empty* `file_path`/`src_file`
within `files` (validated; 400 otherwise). Entities with an empty `file_path`
are repo/package-scoped (e.g. `go_package`, the `""`-for-repo-node case above)
and are exempt from the membership check; edges/symbols always carry a real
`src_file` and stay strict. Returns `200` + `{repo, files, entities_upserted,
edges_upserted}`. Semantic chunks are pushed separately by the ingester to the
existing `/memories:bulk` - A does not change that path.

A full re-ingest of a repo is just this call with `files` = every file. A
repo-delete is `files` = all files, empty entities/edges.

### Query endpoints (read, OIDC-gated)

| Method/path | Returns |
| --- | --- |
| `GET /code/entities?repo=&q=&type=&limit=` | entity search (name/desc ILIKE, optional type filter) |
| `GET /code/entities/{id}?repo=` | single entity + its immediate edges |
| `GET /code/neighbors?repo=&id=&relation=&direction=out|in&depth=N` | generic traversal (recursive CTE, depth-capped) |
| `GET /code/callers?repo=&id=&depth=N` | convenience: reverse `calls` to depth N |
| `GET /code/callees?repo=&id=&depth=N` | convenience: forward `calls` |
| `GET /code/dependents?repo=&id=&depth=N` | reverse `imports`/`references`/`depends_on` |
| `GET /code/dependencies?repo=&id=&depth=N` | forward of the above |
| `GET /code/files/{path}/imports?repo=` | `imports` edges out of a file's package |
| `GET /code/resources/{id}/graph?repo=&depth=N` | tf/helm dependency subgraph |

`depth` capped (default 3, max 10) to bound recursion. `neighbors` is the
primitive; the named routes are thin wrappers for ergonomics and so the MCP
tools map 1:1.

### Packages

- `internal/codegraph/` - `types.go` (Entity, Edge, contract constants),
  `store.go` (`*sql.DB`, bulk upsert + reconcile + traversal CTEs),
  `migrate.go`, `service.go` (validation, depth caps), `metrics.go`.
- `internal/httpapi/` - new `codegraph.go` handlers + routes wired into the
  existing router under the same auth middleware.

### Metrics (rule 13)

`code_entities_total{repo,op}`, `code_edges_total{repo,op}`,
`code_graph_ingest_jobs_total{status}`, `code_graph_queries_total{endpoint}`,
`code_graph_query_duration_seconds{endpoint}`, gauge
`code_entities_current{repo}`.

### Testing

Unit tests for store reconcile (file-granular delete/insert, orphan filtering)
and traversal CTEs against a real Postgres behind `//go:build integration` +
`TATARA_TEST_PG_DSN` (existing pattern). Handler tests with a stub store.
e2e: ingest a tiny fixture graph, query callers/dependents, re-push one file,
assert only that file's subgraph changed.

---

## Sub-project B - tatara-memory-repo-ingester (new repo, phase 3)

Go batch tool. Walks a repo, runs best-per-language analyzers, emits the graph +
chunks, pushes to A and to `/memories:bulk`, polls jobs to terminal.

### Modularity (hard requirement)

```go
// internal/analyze/analyzer.go
type Result struct {
    Entities []codegraph.Entity
    Edges    []codegraph.Edge
    Chunks   []Chunk
}

type Analyzer interface {
    Name() string                          // "go", "python", "javascript", "terraform", "helm"
    Match(path string) bool                // owns this file?
    Analyze(ctx context.Context, repoRoot string, files []string) (Result, error)
}
```

A registry holds analyzers; the walker groups files by the first analyzer whose
`Match` returns true and calls `Analyze` once per analyzer with its file set.
Adding a language = new file implementing `Analyzer` + one `Register(...)` call.
Nothing else in B, and nothing in A or C, changes.

### Analyzers (best-per-language)

- **Go:** `golang.org/x/tools/go/packages` with `NeedTypes|NeedSyntax|NeedDeps`
  + `go/types`. Emits packages/files/types/funcs/methods; edges
  `imports` (package), `calls` (type-resolved), `references`/`uses` (type),
  `implements` (interface satisfaction), `defines`/`contains`.
- **Python:** tree-sitter-python. Modules/classes/funcs; `imports` (resolved
  within repo where possible), `defines`, `calls` (name-based, best-effort
  intra-repo resolution; cross-module calls left as name refs).
- **JavaScript:** tree-sitter-javascript. Files/modules, functions (incl. arrow
  funcs assigned to names), classes; edges `imports` (ES `import` and
  CommonJS `require`, resolved to repo-relative module paths), `defines`,
  `calls` (name-based intra-module resolution, like Python). Module FQN is the
  repo-relative path; symbols are `<module>::<name>`. TypeScript can be added
  later as its own analyzer (tree-sitter-typescript) without disturbing this
  one.
- **Terraform:** `github.com/hashicorp/hcl/v2` + terraform-config-inspect.
  Resources/data/modules/variables/outputs; `depends_on` (explicit), plus
  `references`/`var_ref`/`output_ref` derived from interpolation traversal
  expressions; `module_source`.
- **Helm:** Chart.yaml subchart deps (`subchart`); `text/template` parse of
  templates for `.Values.*` (`value_ref`) and `include`/`template`
  (`includes`); values.yaml keys become `helm_value` entities.
- **Docs:** markdown/plaintext -> chunks only (no graph), linked to the owning
  dir/repo entity.

### Change detection

Default: diff the working tree against the last ingested commit
(`git diff --name-only <last>..<head>`), or full repo if no prior state. Per-file
content hash dedupes the semantic chunks (idempotency key embeds the hash).
`--full` forces a complete re-ingest.

### CLI

`tatara-ingest --repo-root <path> [--repo-name <n>] [--full] [--base-url ...]`.
Auth: OIDC client-credentials (service-account) for in-cluster/CI use; reuses the
`tatara` audience. JSON slog logs with final counts (entities, edges, chunks,
files, duration). Batch tool - no long-running server, so no `/metrics`
endpoint; counts are logged structured (rule 13 satisfied for a batch process;
rationale recorded in B's MEMORY.md per rule 4).

Detailed spec + plan in B's own cycle.

---

## Sub-project C - tatara-cli (code-graph MCP tools)

Adds dedicated MCP tools wrapping A's `/code/*` endpoints, following the
existing `Tool{Name, Description, Schema, Build}` registration pattern - each
tool is a thin `Build` that maps args to method+path. No analysis logic in C.

Tools: `code_search`, `code_entity`, `code_neighbors`, `code_callers`,
`code_callees`, `code_dependents`, `code_dependencies`, `code_file_imports`,
`code_resource_graph`. Adding a language adds **zero** tools (the contract is
language-neutral). Detailed spec + plan in C's own cycle.

---

## Cross-cutting

- **Auth:** all new endpoints OIDC-gated, audience `tatara-memory`, existing
  middleware. The ingester authenticates as a confidential service account.
- **Deploy:** A ships as a new tatara-memory minor (new tables via the embedded
  migration on startup). B is a new repo with its own chart/image (runnable as a
  Job). C ships as a tatara-cli minor.
- **Charts:** cluster-agnostic (rule 14); cluster config in the infra helmfile.
- **No new top-level secrets** beyond the ingester's service-account client.

## Build order

1. **A** - lock the contract (schema, `/code-graph:bulk`, `/code/*`). Ship.
2. **B** and **C** in parallel against A's frozen contract.

Each sub-project runs its own brainstorming-light -> writing-plans -> implement
cycle. This doc is the shared contract they reference.

## Open tradeoffs (accepted)

- Orphaned edges after a cross-file rename are filtered at query time, not
  eagerly cleaned. Cheap file-granular replace is worth it; a periodic
  prune-orphans sweep can be added later if needed.
- Python `calls` are name-based (no type resolution without execution).
  Acceptable; Go (the platform's primary language) is fully type-resolved.
- Two stores (PG structural + LightRAG semantic) linked by ID rather than one
  unified graph. Deliberate - see "Why Postgres".
