# tatara-memory-repo-ingester (sub-project B) - Design

**Date:** 2026-06-06
**Status:** design approved; pending spec review
**Repo:** new `github.com/szymonrychu/tatara-memory-repo-ingester` (phase 3, B)
**Depends on:** tatara-memory sub-project A (frozen contract: `/code-graph:bulk`,
`/memories:bulk` + `/ingest-jobs/{id}`), already shipped on
`tatara-memory` main (`df1a137`).
**References:** the shared contract in
`docs/superpowers/specs/2026-06-05-tatara-code-ingestion-design.md` and the
research-grounded additions (M3 confidence) in
`docs/superpowers/specs/2026-06-06-research-grounded-improvements-design.md`.

## Problem

A (tatara-memory) can store and serve a deterministic code graph and semantic
chunks, but nothing produces them. B walks a repository, runs best-per-language
static analysis, and pushes the structural graph plus enriched semantic chunks to
A. It must understand Go, Python, JavaScript, Terraform and Helm well enough to
emit real inter-file links, and adding a language must touch only B (the hard
modularity contract).

## Scope decisions (approved)

- **All five languages in the first build** (Go, Python, JavaScript, Terraform,
  Helm) plus a docs/markdown chunk-only path.
- **Semantic chunks included** in the first build (graph + chunks, full hybrid).
- **M2 cross-repo linking deferred** to a fast-follow that touches both A and B
  (provides/requires emission + `cross_repo_symbols` table/join in A). Not in
  this build.
- **M3 call-edge confidence included** from the start (rides in existing edge
  `properties`; no A change).
- **M5 SCIP ingestion deferred** (a later analyzer).

## What this repo is NOT

- Not a long-running service: a stateless one-shot batch tool. No HTTP server, no
  `/metrics` endpoint (rule 13 satisfied via structured counts; see Observability).
- Not stateful about ingest history: the caller supplies the base commit for
  incremental runs. B stores nothing between runs.

---

## Repository layout

Mirrors tatara-memory. Created via `gh repo create` then scaffolded.

```
tatara-memory-repo-ingester/
├── CLAUDE.md                 # canonical contract, copied verbatim
├── Dockerfile                # multi-stage, CGO_ENABLED=1
├── Makefile
├── go.mod                    # go 1.25.x
├── MEMORY.md  ROADMAP.md  README.md  LICENSE
├── cmd/tatara-ingest/        # main: flags, slog, wiring
├── internal/
│   ├── contract/             # wire types mirroring A + Chunk + constants
│   ├── config/               # env-based config (rule 6), flags override
│   ├── analyze/              # Analyzer interface, Result, Registry, per-lang files
│   ├── walk/                 # walker + git change detection
│   └── push/                 # HTTP client (graph + chunk + job poll + OIDC)
└── charts/tatara-memory-repo-ingester/   # helm create -> edited to a Job
```

## Package responsibilities

### `internal/contract`

Wire types mirroring A byte-for-byte (B cannot import A's `internal/codegraph`
across module boundaries):

```go
type Entity struct {
    ID          string            `json:"id"`
    Name        string            `json:"name"`
    Type        string            `json:"type"`
    Description string            `json:"description,omitempty"`
    FilePath    string            `json:"file_path"`
    Properties  map[string]string `json:"properties,omitempty"`
}
type Edge struct {
    From       string            `json:"from"`
    To         string            `json:"to"`
    Relation   string            `json:"relation"`
    SrcFile    string            `json:"src_file"`
    Properties map[string]string `json:"properties,omitempty"`
}
type GraphPush struct {
    Repo     string   `json:"repo"`
    Commit   string   `json:"commit,omitempty"`
    Files    []string `json:"files"`
    Entities []Entity `json:"entities"`
    Edges    []Edge   `json:"edges"`
}
type PushResult struct {
    Repo             string `json:"repo"`
    Files            int    `json:"files"`
    EntitiesUpserted int    `json:"entities_upserted"`
    EdgesUpserted    int    `json:"edges_upserted"`
}
```

Plus the chunk intermediate (analyzer output) and the A chunk-item wire shape:

```go
// Chunk is what an analyzer emits per documentable unit.
type Chunk struct {
    EntityID string // join key back to the graph
    Type     string // entity type
    FilePath string
    Language string
    Header   string // structural header (see Semantic chunks)
    Body     string // source body
}
// IngestItem is the /memories:bulk item shape (A's contract).
type IngestItem struct {
    IdempotencyKey string            `json:"idempotency_key"`
    Text           string            `json:"text"`
    Metadata       map[string]string `json:"metadata,omitempty"`
}
```

Constants: entity types (`repo, file, go_package, go_type, go_func, go_method,
py_module, py_class, py_func, js_module, js_class, js_func, tf_resource, tf_data,
tf_module, tf_variable, tf_output, helm_chart, helm_template, helm_value`);
relations (`contains, defines, imports, calls, references, implements,
depends_on, module_source, var_ref, output_ref, value_ref, includes,
subchart`); M3 resolution levels (`type_resolved, scoped_name_match,
imported_name_match, global_name_match, ambiguous_multi_def, unresolved`).

A `contract_shape_test.go` round-trips a sample `GraphPush`/`IngestItem` through
`json.Marshal`/`Unmarshal` and asserts the exact field names, so drift from A is
caught in CI.

### `internal/analyze`

The modularity core.

```go
type Result struct {
    Entities []contract.Entity
    Edges    []contract.Edge
    Chunks   []contract.Chunk
}
type Analyzer interface {
    Name() string                 // "go", "python", "javascript", "terraform", "helm", "docs"
    Match(path string) bool       // owns this file?
    Analyze(ctx context.Context, repoRoot string, files []string) (Result, error)
}
```

`Registry` is an ordered slice of analyzers. The walker assigns each changed file
to the **first** analyzer whose `Match` returns true, then calls each analyzer's
`Analyze` once with its assigned file set. Adding a language = a new
`internal/analyze/<lang>.go` implementing `Analyzer` + one `Register(...)` call;
nothing else in B (and nothing in A or C) changes.

One file per analyzer: `golang.go`, `python.go`, `javascript.go`,
`terraform.go`, `helm.go`, `docs.go`. `analyzer.go` holds the interface, `Result`,
and `Registry`.

### `internal/walk`

Walks `repoRoot`, applies change detection, returns the changed file set grouped
by analyzer. Change detection:

- `--since <commit>` -> `git diff --name-only <commit>..HEAD` (only changed
  files).
- no `--since` -> full tree walk (every file under `repoRoot`, respecting
  `.gitignore` via `git ls-files`).
- `--full` -> force full even if `--since` is given.

B is stateless about the last-ingested commit; the caller/CI supplies `--since`.
Rationale (rule 4) recorded in MEMORY.md: querying A for "last commit per repo"
would couple B to A's internal state and A does not expose it; passing the base
commit from the orchestration layer is simpler and matches CI's natural knowledge
of the diff range.

### `internal/push`

HTTP client against tatara-memory:

- `PushGraph(ctx, GraphPush) (PushResult, error)` -> `POST {base}/code-graph:bulk`,
  expects 200 + `PushResult`. Synchronous.
- `PushChunks(ctx, repo string, items []IngestItem) error` -> `POST
  {base}/memories:bulk`, parse returned `IngestJob{id,status}`, then poll
  `GET {base}/ingest-jobs/{id}` every interval until `status` is terminal
  (`succeeded`/`failed`/`partial`). `partial`/`failed` returns an error carrying
  the job's item errors.
- Auth: OIDC client-credentials via `golang.org/x/oauth2/clientcredentials`
  (issuer's token endpoint discovered from config), token attached as
  `Authorization: Bearer`. Token cached and refreshed by the oauth2 transport.
- Retry: transient HTTP/5xx and network errors retried with capped exponential
  backoff (e.g. 3 attempts); non-retryable 4xx returns immediately.

### `internal/config`

Rule-6 env mapping (camelCase scalar in values.yaml -> kebab ConfigMap/Secret key
-> `envFrom`). Fields: `BaseURL`, `OIDCIssuer`, `OIDCClientID`,
`OIDCClientSecret`, `OIDCAudience`, `PollInterval`, `HTTPTimeout`. Loaded from env
(via `os.Getenv` with a small `Load()` mirroring tatara-memory's pattern); CLI
flags override env for local runs.

### `cmd/tatara-ingest`

Flags: `--repo-root <path>` (required), `--repo-name <n>` (default: base name of
repo-root or `git remote` name), `--since <commit>`, `--full`, `--base-url`,
plus OIDC overrides. Sets up JSON `slog`, builds the registry, runs
walk -> analyze -> push, logs final structured counts, exits non-zero on push
failure or `partial`/`failed` job.

---

## The analyzers

Each emits `contract.Entity`/`Edge`/`Chunk` with canonical
`<lang>:<kind>:<fqn>` IDs (no line numbers; line spans go in `properties`).

### Go (`golang.go`)

`golang.org/x/tools/go/packages` with `NeedName|NeedFiles|NeedSyntax|NeedTypes|
NeedTypesInfo|NeedDeps` + `go/types`. Emits `go_package`, `file`, `go_type`,
`go_func`, `go_method`; edges `contains`, `defines`, `imports` (package),
`calls` (type-resolved via `types.Info.Uses` on call exprs), `references`
(type use), `implements` (interface satisfaction via `types.Implements`). All
edges -> `resolution=type_resolved, confidence=0.98`. A package that fails to
type-check logs WARN and is skipped (its files contribute no graph; a
tree-sitter fallback is a noted future item, not this build).

### Python (`python.go`)

tree-sitter (`github.com/smacker/go-tree-sitter` + its bundled `python`
grammar; CGo). Emits `py_module`, `py_class`, `py_func`; edges `contains`,
`defines`, `imports` (resolved to in-repo modules where the import target maps to
a walked file), `calls` (name-based). FQN = dotted module path
(`pkg.sub.module.Class.func`). Confidence per M3:

- callee resolves to a unique def in the same module/class scope ->
  `scoped_name_match` (0.85);
- resolves through a tracked import to a unique in-repo def ->
  `imported_name_match` (0.7);
- unique def somewhere in the repo, no scope/import anchor -> `global_name_match`
  (0.45);
- name matches >1 def -> `ambiguous_multi_def` (0.2);
- no in-repo def (external/builtin/dynamic) -> `unresolved` (0.0), emitted as a
  `dangling_call` marker on the source entity rather than an edge.

`degraded_by` (comma-joined) is set when the call site sits inside a hard
construct (`decorator`, `dynamic`, `reflection`, `reexport`, `mro`, `lambda`,
`higher_order`); when non-empty, confidence is capped at 0.45.

### JavaScript (`javascript.go`)

tree-sitter (`javascript` grammar; CGo). Emits `js_module` (FQN = repo-relative
module path), `js_func` (incl. arrow funcs assigned to a name), `js_class`;
symbols are `<module>::<name>`. Edges `contains`, `defines`, `imports` (ES
`import` and CommonJS `require`, resolved to repo-relative module paths),
`calls` (name-based, same M3 ladder and `degraded_by` rules as Python).

### Terraform (`terraform.go`)

`github.com/hashicorp/hcl/v2` + `github.com/hashicorp/terraform-config-inspect`.
Emits `tf_resource`, `tf_data`, `tf_module`, `tf_variable`, `tf_output`; edges
`depends_on` (explicit), `references`/`var_ref`/`output_ref` (derived from
interpolation/traversal expressions), `module_source`. HCL is statically
resolved -> `resolution=type_resolved, confidence=0.98`.

### Helm (`helm.go`)

`sigs.k8s.io/yaml` for `Chart.yaml`/`values.yaml`, `text/template` to parse
templates (no dependency on full `helm.sh/helm/v3`). Emits `helm_chart`,
`helm_template`, `helm_value`; edges `subchart` (Chart.yaml dependencies),
`value_ref` (`.Values.*` in templates), `includes` (`include`/`template`
calls). Template/values are statically parsed -> `type_resolved`. A template that
fails to parse logs WARN and is skipped.

### Docs (`docs.go`)

Markdown/plaintext (`.md`, `.txt`, `.rst`) -> `Chunks` only, no graph. Each doc
section becomes a chunk linked (via metadata `entity_id`) to the owning directory
or repo entity. `Match` is last in registry order (lowest precedence).

---

## Semantic chunks

One chunk per documentable unit (Go func/type/file, py class/func/module, js
func/class/module, tf resource, helm template, doc section). The analyzer emits a
`contract.Chunk`; `push` assembles the `IngestItem`:

- `idempotency_key = "<repo>:<entity_id>:<content_hash>"` (content hash over the
  body; re-ingesting unchanged content is a no-op upsert).
- `text = Header + "\n---\n" + Body`.
- `metadata = {repo, entity_id, type, file_path, language}` (`entity_id` is the
  join key from a semantic hit back to the graph).

Structural header example:

```
[go_method] github.com/szymonrychu/tatara-cli/internal/mcp.(*Server).Run
file: internal/mcp/server.go:42-58
package: github.com/szymonrychu/tatara-cli/internal/mcp
calls: server.ServeStdio
signature: func (s *Server) Run(ctx context.Context) error
```

---

## Data flow

```
tatara-ingest --repo-root R [--since C]
  walk(R, since)                      -> changed files grouped by analyzer
  for each analyzer: Analyze(...)     -> aggregate Entities, Edges, Chunks
  PushGraph(GraphPush{repo, commit=HEAD, files=changed, entities, edges})
                                      -> POST /code-graph:bulk (sync) -> PushResult
  PushChunks(items from Chunks)       -> POST /memories:bulk -> job
                                         poll /ingest-jobs/{id} to terminal
  log counts (entities, edges, chunks, files, per-language, per-resolution, duration)
  exit non-zero on push error or partial/failed job
```

A full re-ingest = run without `--since` (or with `--full`): `files` = every
file, A reconciles each at file granularity. A repo-delete is out of B's scope
(operator calls `/code-graph:bulk` with empty entities/edges).

## Error handling

- Per-file analyzer error -> WARN, skip that file, continue. One bad file never
  fails the run.
- Go package / Helm template parse failure -> WARN, skip that unit.
- `PushGraph` non-2xx -> retry transient, then ERROR + non-zero exit (graph and
  chunks are pushed in that order; a graph failure aborts before chunks).
- Chunk job `partial`/`failed` -> log item errors, non-zero exit (graph already
  applied; the run is reported as partial).
- No error handling for impossible states (rule: no defensive code for cases that
  cannot happen).

## Observability (rule 13, batch variant)

No `/metrics` endpoint (no long-running process to scrape). Final structured slog
line carries counts: total/per-language entities, edges, chunks, files;
per-`resolution`-bucket edge counts; push durations; job outcome. Every business
action (walk start, per-analyzer result, graph push, chunk job poll result)
logged at INFO with structured fields (rule 12). Rationale for no-/metrics
recorded in MEMORY.md (rule 4). A Prometheus Pushgateway emitter is a noted
future option, not built now.

## Configuration & deploy

- Rule 6: scalars in `values.yaml` camelCase -> kebab ConfigMap/Secret keys ->
  `envFrom`. Secret (sops in the infra helmfile): OIDC client secret. ConfigMap:
  base URL, issuer, client id, audience, intervals.
- Chart `charts/tatara-memory-repo-ingester` created via `helm create` then
  edited to a **Job** (one-shot, `restartPolicy: Never`, `backoffLimit` set).
  Cluster-agnostic (rule 14): no baked imagePullSecrets, node affinity, storage
  class, or replicated-secret names; all cluster config comes from the infra
  helmfile `tatara` bucket.
- Dockerfile: multi-stage, `CGO_ENABLED=1` (tree-sitter), C toolchain (gcc) in
  the builder stage, debian-slim or `gcr.io/distroless/cc` runtime (CGo binary
  links libc, so not scratch/static).

## Testing

- Per-analyzer unit tests against tiny `internal/analyze/testdata/<lang>/` fixture
  trees; assert exact entities, edges (incl. `resolution`/`confidence`), and
  chunks.
- `walk` + change-detection tests against a fixture git repo (init, commit, edit,
  assert the diff set).
- `push` tests against an `httptest.Server` stubbing `/code-graph:bulk`,
  `/memories:bulk`, and `/ingest-jobs/{id}` (including a `partial` job
  path).
- `contract_shape_test.go` JSON round-trip guarding against drift from A.
- `//go:build integration` e2e gated on an env-provided base URL + token: ingest a
  fixture repo into a real tatara-memory, query `/code/callers` and
  `/code/entities`, assert the expected graph, re-ingest one file, assert only
  that file's subgraph changed. Mirrors A's integration gating.

## Build order (subagent-driven; all 5 languages this build)

1. Scaffold repo (gh + layout, CLAUDE.md, Makefile, go.mod) + `contract` package
   (+ shape test) + `config`. Foundation; locks the wire types.
2. `analyze` interface + `Registry` + `walk` + change detection (with a trivial
   no-op analyzer to test the pipeline).
3. `push` client (graph + chunk + job poll + OIDC) against the httptest stub.
4. The five analyzers + docs as **parallel independent units** (each its own file
   + testdata + tests), built against the frozen `contract` and `Analyzer`
   interface.
5. `cmd/tatara-ingest` wiring + integration e2e + Dockerfile + chart.

Opus merge subagent integrates the parallel analyzers onto `main`; build/deploy
from `main` only (rule 10).

## Open tradeoffs (accepted)

- **Name-based dynamic-language calls** (Python/JS) are imprecise; M3 confidence
  makes that explicit rather than hiding it. Go/Terraform/Helm are statically
  resolved.
- **Stateless change detection**: caller supplies `--since`. Deliberate; avoids
  coupling to A's internal state.
- **CGo** (tree-sitter) for Python/JS forces a libc runtime image and
  `CGO_ENABLED=1`; Go/Terraform/Helm/docs are pure Go. Accepted for analysis
  precision.
- **smacker/go-tree-sitter** chosen for bundled grammars over the official
  split-module binding; swappable behind the analyzer if maintenance becomes a
  concern.
- **No tree-sitter fallback for non-buildable Go** in this build (Go failures are
  skipped, not degraded). Noted as a future item.

## Deploy-time follow-ons (out of this build's code scope)

- Keycloak service-account client for the ingester (confidential, `tatara`
  audience) in `infra/terraform/keycloak`.
- Image + chart publish (Harbor) and an infra-helmfile `tatara`-bucket release
  (a Job/CronJob entry), following the rule-10 deploy-from-main path.
