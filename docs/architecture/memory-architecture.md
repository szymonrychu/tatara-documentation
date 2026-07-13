---
title: Memory Architecture
---

# Memory Architecture

The tatara memory system is a persistent knowledge graph of your codebase. It replaces the cold-read pattern (agent reads files from scratch on every turn) with a queryable graph that survives session boundaries, pod restarts, and code changes.

The name alludes to the platform metaphor: the tatara forge's permanent substrate that every ephemeral agent session works against.

## Stack

```
tatara-memory (REST service, Go)
        |
        v
    LightRAG (Python, upstream library)
        |              |
        v              v
     Neo4j          CNPG Postgres
  (authoritative   (LightRAG KV + vectors:
   graph store,     PGKVStorage +
   Neo4JStorage)    PGVectorStorage;
                    doc + ingest status)
```

LightRAG is configured with `LIGHTRAG_GRAPH_STORAGE=Neo4JStorage`, `PGKVStorage`,
and `PGVectorStorage`. Neo4j holds the entity/relationship graph; Postgres holds the
key-value store, vector embeddings, and document/ingest status. `tatara-memory` stores
no live conversation state - there is no session-resume mechanism at all (see
[Agent Execution](agent-execution.md#task-continuity-the-notes-journal)). What it
does store is the **overflow** from `Task`, `Issue`, and `MergeRequest` objects in
etcd, once those hit their own byte budgets: see
[Task continuity spill](#task-continuity-spill) below.

One stack is provisioned per `Project` CR by the operator. Sizes are tunable via `spec.memory`:

```yaml
spec:
  memory:
    pgInstances: 3       # CNPG replicas (1=dev, 3=HA)
    pgStorage: 20Gi
    pgWalStorage: 10Gi   # dedicated WAL volume, separate from PGDATA (default 8Gi)
    neo4jStorage: 10Gi
```

## How memory is populated

```mermaid
flowchart TD
    A[Repository CR created] --> B[Operator creates ingest Job]
    B --> C[tatara-memory-repo-ingester\nclones + walks repo]
    C --> D{Language analyzer}
    D --> E[AST entities + edges]
    D --> F[Semantic text chunks]
    E --> G[tatara-memory REST API]
    F --> G
    G --> H[LightRAG]
    H --> I[(Neo4j\ngraph store)]
    H --> J[(Postgres\nKV + vectors)]

    K[Push webhook] -->|annotates Repository CR| L[Incremental ingest Job\n--since lastIngestedCommit]
    L --> C
    M[Cron reingestSchedule] --> L
```

1. **Initial ingest:** Repository CR created -> operator creates an ingest `Job` -> `tatara-memory-repo-ingester` clones + bulk-posts chunks and graph entities.
2. **Incremental ingest:** push webhook annotates the Repository CR; the operator creates a new ingest Job running `--since <lastIngestedCommit>` (delta only).
3. **Scheduled re-ingest:** `spec.reingestSchedule` (cron expression on the Repository CR) triggers a periodic **incremental** catch-up (`--since lastIngestedCommit`), guarding against missed webhooks. It is not a full rebuild; a full ingest happens only on first ingest or after repeated incremental failures (e.g. a force-pushed branch whose since-SHA is gone).
4. **Semantic extraction:** when `spec.semanticIngest: true` (default) and an OpenAI
   Secret is configured, each changed file is also processed by **OpenAI** for
   LLM-powered entity and relationship extraction (`SEMANTIC_MODEL`, default
   `gpt-4o-mini`; the key comes from the OpenAI Secret, key `LLM_BINDING_API_KEY`). This
   enriches the Neo4j graph beyond what AST analysis alone produces. It is not Claude:
   with no OpenAI Secret provided the ingester runs AST-only and does not fail.

## How agents query memory

Inside agent pods, `tatara-cli mcp` exposes memory query tools over MCP:

| MCP tool | What it does |
|---|---|
| `query` | Semantic search over the LightRAG knowledge graph |
| `code_search` | Search code-graph entities by name/description (optional type filter) |
| `code_stats` | Graph statistics: entity/edge counts, types, isolated entities, import cycles |
| `code_entity` / `get_entity` | Get a single entity and its immediate edges |
| `code_explain` | Full context for an entity: detail plus in/out neighbors with file locations |
| `code_neighbors` / `code_callers` / `code_callees` | Traverse the graph from an entity along a relation |
| `code_path` | Shortest path between two code entities |
| `code_dependents` / `code_dependencies` / `code_cross_repo` | Dependency and cross-repo edges |

These are the real tool names (there is no `memory_query`, `code_graph_list`,
`code_graph_get`, or `code_graph_explain`). The full code-graph surface is ~20 `code_*`
tools. The LightRAG query mode (naive, local, global, hybrid) is chosen by the tool
implementation based on query type; hybrid mode (vector + graph traversal) is the default.

## Task continuity spill

There is no session-resume mode: `--resume`, a stored session ID, and a stored S3 transcript object key are all gone (`Task.status.sessionID` and `Task.status.conversationObjectKey` no longer exist). <!-- stale-ok: sessionID, conversationObjectKey -->
Every pod's turn-0 gets an
identical, freshly rendered context bundle from current CR state; the state that
actually carries forward between pods for the same `Task` is
`Task.status.notes`, an append-only journal (see
[Agent Execution](agent-execution.md#task-continuity-the-notes-journal)). Two
things overflow etcd's per-object byte budget and spill into `tatara-memory`
instead of being dropped:

- **Notes beyond the 50-item Go-side cap** on `Task.status.notes`. The oldest
  notes spill first; `Task.status.stats.notesSpilled` counts them and
  `Task.status.stats.notesSpilledRefs` accumulates one `track_id` per spill
  batch. They are read back via the `task_context(notes=all)` MCP tool - a
  spilled note that could not be read back would be continuity silently lost,
  so the read path always includes spilled notes.
- **Evicted `Issue`/`MergeRequest` comments**, once the object's marshaled size
  would exceed its byte budget (a pre-write guard, not a count cap: 200
  comments of 40 KB each blows well past a count-only ceiling). The oldest
  comments are spilled first, `status.commentsRetainedFrom` advances past
  what was evicted, and the spill write happens once, outside any retry loop,
  before the trimmed object is ever written - so a spill failure aborts the
  write instead of silently losing comments.

Both spills are one-way: nothing pulls old notes or comments back out of
`tatara-memory` into the CR. They exist so the agent-facing tools can still
retrieve the full history on demand without the CR itself growing past its
etcd object ceiling.

## Durability considerations

| Concern | Mitigation |
|---|---|
| CNPG pod restart | CNPG manages HA; use `pgInstances: 3` for production. Postgres holds LightRAG KV + vectors, so a lost Postgres loses embeddings and requires re-ingest. |
| Neo4j pod restart | Neo4j is the **authoritative** LightRAG graph store, not a projection. Its PVC holds durable graph data; there is no rebuild-from-Postgres path. Recovery from a lost Neo4j volume is re-ingest of every repo, not an automatic rebuild - do not delete the Neo4j PVC expecting it to repopulate. |
| CephFS write-cap leak | Known fragile under unclean probe-kill restarts (CNPG `io_method=sync`); consider RBD for CNPG PVCs in Ceph environments |
| LightRAG `duplicated` response | Treated as success; re-ingesting the same chunk is idempotent |
| LightRAG `busy` response | Treated as transient; controller retries with exponential backoff |
| Stale page cache (Neo4j EIO) | Restart the Neo4j pod; the error is poisoned page-cache from Ceph OSD crashes, not data loss |
