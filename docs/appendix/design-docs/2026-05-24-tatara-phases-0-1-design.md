# Tatara - phases 0 and 1 design

Date: 2026-05-24
Status: Draft for review
Author: Szymon (with Claude)

## Background

The previous monolithic `tatara` project (now at `~/Documents/tatara-old`) reached v0.3 as one repository containing CRDs, controllers, MCP servers, a bridge, and helm charts. It worked, but the blast radius of any single change was the whole platform. The new approach splits tatara into eight independent components, each its own GitHub repository, built and shipped on its own cadence. This spec covers phases 0 and 1 only:

- **Phase 0**: `tatara` - the documentation and architecture index repo.
- **Phase 1**: `tatara-memory` - the memory microservice (REST API over LightRAG, OIDC-secured).

Phases 2-7 get one-paragraph scope sketches at the bottom and will be brainstormed in their own sessions when their turn comes.

## Scope of this spec

In scope:
- Component repo split, naming, on-disk layout.
- Phase 0 contents (docs only).
- Phase 1 (`tatara-memory`) full design: API shape, internal layers, chart layout, keycloak terraform, testing strategy, error semantics, observability.
- The hard-rule workflow encoded into every component repo's `CLAUDE.md`.

Out of scope:
- Detailed design for phases 2-7 (sketches only).
- Migration from `tatara-old` (the new repos are written fresh, lifting concepts not code; tatara-old stays as a reference).
- Multi-tenancy, per-user RBAC, rate limiting (deferred - see "Things explicitly NOT in v1").

## Repo split and on-disk layout

Eight GitHub repos under `szymonrychu/`:

| Phase | Repo                              | Purpose                                                       |
| ----- | --------------------------------- | ------------------------------------------------------------- |
| 0     | `tatara`                          | docs + architecture index                                     |
| 1     | `tatara-memory`                   | REST memory service over LightRAG, OIDC-gated                 |
| 2     | `tatara-cli`                      | Homebrew-installable CLI with login, raw, mcp, mcp-config     |
| 3     | `tatara-memory-repo-ingester`     | Batch tool that walks a repo and bulk-ingests into memory     |
| 4     | `tatara-claude-code-wrapper`      | Container image with claude + tatara-cli pre-configured       |
| 5     | `tatara-argo-workflows`           | WorkflowTemplates and a small controller                      |
| 6     | `tatara-tasks`                    | Task tracking service (REST, replaces tatara-old TaskBoard)   |
| 7     | `tatara-gitlab-bridge`            | Webhook -> workflow bridge, GitLab only                        |

On disk, the docs repo holds child repos as gitignored subdirectories:

```
~/Documents/tatara/                    # repo: szymonrychu/tatara
├── .git/
├── README.md  ARCHITECTURE.md  LICENSE  MEMORY.md  ROADMAP.md  CLAUDE.md
├── .gitignore                          # ignores tatara-*/
├── docs/superpowers/specs/             # this spec
├── tatara-memory/                      # repo: szymonrychu/tatara-memory (its own .git)
├── tatara-cli/                         # repo: szymonrychu/tatara-cli
├── tatara-memory-repo-ingester/
├── tatara-claude-code-wrapper/
├── tatara-argo-workflows/
├── tatara-tasks/
└── tatara-gitlab-bridge/
```

Each child is an independent git repo with its own remote, CI, MEMORY.md, ROADMAP.md, CLAUDE.md, helm chart (where relevant), helmfile, and Dockerfile.

## Phase 0 - `tatara` (docs)

Contents:
- `README.md` - project overview, lineage from tatara-old, link to each child repo.
- `ARCHITECTURE.md` - high-level component diagram, data flow, the OIDC model, the chart-deployment model.
- `LICENSE` - AGPLv3 (matches tatara-old).
- `MEMORY.md` - cross-repo decisions and learnings. Each child repo has its own MEMORY.md for component-local memory; this one carries only platform-wide decisions.
- `ROADMAP.md` - phase-level roadmap with status per repo.
- `CLAUDE.md` - the hard-rule workflow (see "Workflow & rules" below).
- `.gitignore` - ignores `tatara-*/` so child repos don't get sucked into the docs repo.
- `docs/superpowers/specs/` - design specs (this file) and future ones.

Out of scope for phase 0: no umbrella helmfile, no shared mise tasks, no scaffolding scripts. Child repos are independent.

## Phase 1 - `tatara-memory` (service)

### Stack
- Go, newest stable (currently 1.25.x).
- Stdlib `net/http` + `chi` router. Stdlib `log/slog` for JSON logging. `prometheus/client_golang` for metrics. `coreos/go-oidc` + `golang-jwt/jwt` for token verification.
- LightRAG (upstream `ghcr.io/hkuds/lightrag:latest`) backed by CloudNativePG (PGKVStorage, PGVectorStorage, PGDocStatusStorage) and Neo4j (Neo4JStorage).
- Tests: stdlib `testing` + `testify/require`. No mocking frameworks. Table-driven.

### API - domain-shaped resources

The service hides LightRAG. Consumers (CLI, ingester, MCP wrapper) talk a stable contract that survives LightRAG version bumps.

```
POST   /v1/memories                       # ingest one document/chunk -> {id}
GET    /v1/memories/{id}                  # fetch
DELETE /v1/memories/{id}                  # remove

POST   /v1/memories:bulk                  # bulk ingest -> {job_id}
GET    /v1/ingest-jobs/{job_id}           # job status

POST   /v1/queries                        # mode: hybrid|local|global|naive
                                          # -> {matches:[{id,score,text,...}]}
POST   /v1/queries:describe               # -> {response, sources:[...]} (LightRAG /lightrag/query)

GET    /v1/entities/{id}
GET    /v1/entities?q=...                   # search
PATCH  /v1/entities/{id}                  # name, type, description, properties

GET    /v1/edges
POST   /v1/edges                          # {from_entity, to_entity, relation, properties}
DELETE /v1/edges/{id}

GET    /healthz
GET    /readyz
GET    /metrics                           # prometheus
```

All `/v1/*` endpoints require a valid bearer JWT (see Auth).

### Internal layers

```
cmd/tatara-memory/main.go         entrypoint, flag parsing, wires layers
internal/httpapi/                 chi router, middleware (req-id, slog access log, auth, metrics, panic recovery)
internal/auth/                    JWKS verifier, audience+issuer check
internal/memory/                  domain service: Memory, Entity, Edge, Query, IngestJob
internal/lightrag/                typed LightRAG HTTP client (only this package knows the upstream wire format)
internal/lightrag/fake/           in-memory fake for tests
internal/ingest/                  async ingest worker pool, job state in postgres
internal/obs/                     slog JSON logger init, prom registry, otel tracer init
```

Only `internal/memory` orchestrates lightrag calls. `internal/httpapi` translates HTTP to/from domain types. This keeps the LightRAG dependency contained.

### Auth

- Bearer JWT in `Authorization: Bearer <token>`.
- Issuer: `https://auth.szymonrichert.pl/realms/master`.
- JWKS auto-discovered via `/.well-known/openid-configuration` and cached with rotation handling (go-oidc handles this).
- Verification: signature, `iss`, `exp`, `aud` must contain `tatara-memory`.
- Claims `sub` and `preferred_username` propagate into log context and an `X-User` response header (debug aid).
- No per-user RBAC in v1. Every valid token gets full access. Add when there's a real ask.

### Ingest jobs

- Bulk ingest is async. Client POSTs `{items:[...]}`, gets `{job_id, status:"queued"}`.
- Job state lives in postgres (the same cnpg cluster lightrag uses, separate database `tatara_memory`).
- In-process worker pool (configurable size, default 4) drains the queue.
- Each chunk has a client-supplied or server-generated `idempotency_key`. Retries on the same key are no-ops.
- On startup, the worker resumes any `running` jobs from the table (crash-safe).
- Job status: `queued | running | succeeded | failed | partial`. Includes counts (`total`, `done`, `failed`) and a bounded list of per-item errors (last 50).

We do **not** introduce redis/rabbitmq for this. Cnpg is already a dependency and the volume is low. The job runner interface stays small enough that swapping the backend later is cheap if ever needed.

### Observability

**Logging** (slog JSON, every line):
- `request_id`, `user`, `route`, `method`, `status`, `duration_ms` on every request.
- INFO for every business action: ingest started, ingest item written, query executed, entity updated, edge created, edge deleted.
- WARN for recoverable problems (lightrag transient errors, retry exhaustion on a single item).
- ERROR for genuine failures (panic recovered, lightrag down, postgres unreachable).

**Metrics** (prometheus):
- Counters: `http_requests_total{route,method,status}`, `ingest_jobs_total{terminal_state}`, `ingest_items_total{result}`, `lightrag_calls_total{op,result}`, `auth_failures_total{reason}`.
- Histograms: `http_request_duration_seconds{route}`, `lightrag_call_duration_seconds{op}`, `ingest_item_duration_seconds`, `query_duration_seconds{mode}`.
- Gauges: `http_in_flight`, `ingest_jobs_running`, `ingest_worker_idle`.

**Tracing**: OTLP exporter via env, otel-instrumented chi + outbound HTTP client. Stubbed in v1 (enabled when an OTEL endpoint is configured).

### Error semantics

- 4xx for client problems. Body: `{"error":"...","request_id":"..."}`. No internal detail leakage.
- 5xx only for genuine internal failures. LightRAG upstream errors map to 502; transient ones (timeouts, 503 upstream) to 503 with `Retry-After`.
- Ingest job per-item errors are reported in the job status, not as 5xx on the bulk POST (the POST succeeds as long as the job is enqueued).
- One global panic-recovery middleware. No per-handler try/catch noise.

### Chart layout

Created via `helm create tatara-memory`, then edited.

```
charts/tatara-memory/
├── Chart.yaml                 # subchart deps: cnpg-cluster, neo4j, lightrag
├── values.yaml                # camelCase scalars only
├── templates/
│   ├── _helpers.tpl
│   ├── deployment.yaml        # tatara-memory app
│   ├── service.yaml
│   ├── ingress.yaml
│   ├── servicemonitor.yaml    # prometheus-operator
│   ├── configmap.yaml         # non-secret config, kebab-case keys
│   ├── secret.yaml            # secret config, kebab-case keys, ExternalSecret-friendly
│   ├── networkpolicy.yaml
│   └── serviceaccount.yaml
├── tests/                     # helm-unittest
└── charts/                    # vendored subcharts (after helm dep update)
    └── lightrag/              # local subchart, also helm-created
```

**Subchart dependencies**:
- `cloudnative-pg/cluster` for postgres (lightrag's storage + tatara-memory's ingest_jobs).
- `neo4j/neo4j` (official chart).
- `lightrag` - no upstream chart exists; we vendor a local one (ported from tatara-old's `tatara-lightrag` chart, cleaned up).

**Values discipline (strict)**:
- `values.yaml` contains only camelCase scalars.
- No `env:` arrays, no `extraEnv`, no raw lists.
- Every `values.fooBar` maps to a ConfigMap or Secret key `foo-bar` (kebab-case); workload consumes via `envFrom`.
- Genuinely list-shaped data is rendered into a templated ConfigMap and read from there at runtime (never passed as a `values.someList: [...]`).

**Secrets**: never plaintext. SOPS-encrypted values files for environment overrides. Keycloak client secret, lightrag->openai/anthropic key, cnpg/neo4j passwords are all `existingSecret + key` references.

### Helmfile

Each repo gets its own `helmfile.yaml.gotmpl` at the root, single `default` environment, mirroring the pattern in `tatara-old/helmfile.yaml.gotmpl`. The docs repo does NOT have an umbrella helmfile in v0.

### Build / deploy discipline

- Development happens in git worktrees off the repo's `main` (`superpowers:using-git-worktrees`).
- Changes merge back to `main` on the source repo (`superpowers:finishing-a-development-branch`).
- Container images push to `harbor.szymonrichert.pl` and `helmfile apply` run **only** from `main` on the source repo. Never from a worktree.

## Keycloak terraform (lives in `~/Documents/infra/terraform/keycloak`)

A new file `tatara_clients.tf` in the master realm. Direct resources (not the oauth2-proxy module - we're doing bearer-token, not browser-flow oauth2-proxy).

```hcl
resource "keycloak_openid_client" "tatara_memory" {
  realm_id  = data.keycloak_realm.master.id
  client_id = "tatara-memory"
  name      = "tatara-memory"

  access_type                  = "CONFIDENTIAL"
  standard_flow_enabled        = false
  implicit_flow_enabled        = false
  direct_access_grants_enabled = false
  service_accounts_enabled     = true

  valid_redirect_uris = []
}

resource "keycloak_openid_client_scope" "tatara" {
  realm_id               = data.keycloak_realm.master.id
  name                   = "tatara"
  description            = "Access to tatara-memory and related tatara services"
  include_in_token_scope = true
}

resource "keycloak_openid_audience_protocol_mapper" "tatara_aud" {
  realm_id                = data.keycloak_realm.master.id
  client_scope_id         = keycloak_openid_client_scope.tatara.id
  name                    = "tatara-memory-audience"
  included_custom_audience = "tatara-memory"
}

resource "keycloak_openid_client" "tatara_cli" {
  realm_id  = data.keycloak_realm.master.id
  client_id = "tatara-cli"
  name      = "tatara-cli"

  access_type                                = "PUBLIC"
  standard_flow_enabled                      = true
  implicit_flow_enabled                      = false
  direct_access_grants_enabled               = false
  oauth2_device_authorization_grant_enabled  = true

  valid_redirect_uris = ["http://localhost:*/callback"]
}

resource "keycloak_openid_client_default_scopes" "tatara_cli_scopes" {
  realm_id       = data.keycloak_realm.master.id
  client_id      = keycloak_openid_client.tatara_cli.id
  default_scopes = ["openid", "profile", "email", keycloak_openid_client_scope.tatara.name]
}
```

The terraform change ships through the **infra** repo, not the tatara repo. The tatara-memory spec just names the two client IDs (`tatara-memory`, `tatara-cli`) and the scope (`tatara`) as a contract.

## Testing strategy

**Unit (`go test ./...`)**:
- `internal/auth`: in-process Keycloak-shaped JWKS (generated RSA key, signed JWT). Valid case + 5 invalid (expired, wrong issuer, wrong audience, bad signature, missing claim).
- `internal/memory`, `internal/ingest`: against a `lightrag.Client` interface, fake implementation in `internal/lightrag/fake`.
- `internal/httpapi`: `httptest.Server` end-to-end with real router + fake lightrag + auth verifier in test mode.

**Integration (`-tags=integration`)**:
- `test/integration/` has a `docker-compose.yml` bringing up lightrag + postgres + neo4j. Run on tag push in CI.

**Chart**:
- `helm lint` on every push.
- `helm template` snapshot diff in CI.
- Per-template unit tests under `charts/tatara-memory/tests/` using `helm-unittest`.

## Things explicitly NOT in v1

- Per-user RBAC, multi-tenancy, soft-delete, audit log table.
- Separate worker process for ingest (in-process pool suffices).
- Streaming responses (SSE/WebSocket).
- Rate limiting (handled by ingress if abuse appears).
- Migration from tatara-old data.

Each of these will get a brainstorming session if and when there's a real ask.

## Workflow & rules (CLAUDE.md, copied to every component repo)

- Newest stable Go for any Go service.
- KISS, always - prefer simplicity over cleverness.
- Boy-scout rule on adjacent issues - just fix them, no asking.
- NEVER introduce tech-debt. If a thing is complex, call it out, but NEVER defer.
- Charts always created via `helm create <name>` then edited; never hand-rolled.
- No plain ENVs in `values.yaml`. No lists in `values.yaml`. All values map: camelCase scalar in values -> kebab-case key in ConfigMap/Secret -> workload via envFrom.
- Implementation by sonnet subagents; merges by an opus subagent. Never run the implementation work in opus.
- EVERYTHING through superpowers - brainstorming, planning, TDD, systematic-debugging, requesting-code-review, verification-before-completion are mandatory.
- ALWAYS subagent-driven, parallel development where tasks are independent.
- ALWAYS flow: worktree -> develop in worktree -> merge back to source repo `main` -> cleanup worktree -> build/deploy from `main` only. NEVER build/deploy from a worktree.
- Cleanup worktrees regularly.
- All logs JSON via stdlib `log/slog` (Go) - same logger structure everywhere.
- Every business-logic action logs at INFO with structured fields.
- WARN and ERROR used appropriately.
- Every counted / timed / failure-prone operation has a prometheus metric. Expose the insides of the apps.

## Phases 2-7 - one-paragraph sketches (will be brainstormed later)

- **tatara-cli** - Go CLI, homebrew tap `szymonrychu/tap`. Cobra + `golang.org/x/oauth2` device-flow against the `tatara-cli` keycloak client. Subcommands: `login`, `logout`, `raw <verb> <path>`, `mcp` (stdio MCP server translating MCP tool calls into REST calls to tatara-memory and future services), `mcp-config <dir>` (writes `.mcp.json` for the current dir). Mirrors `~/Documents/spellslinger/spellslinger-cli` structure.
- **tatara-memory-repo-ingester** - Go batch tool. Walks a git repo, chunks files using a language-aware splitter, POSTs to `/v1/memories:bulk`, polls job status. Runs as a one-shot CronJob in cluster or invoked from `tatara-cli`.
- **tatara-claude-code-wrapper** - Container image bundling Claude Code + tatara-cli pre-installed and pre-configured with the user's token, ready to drop into argo workflows or local docker.
- **tatara-argo-workflows** - WorkflowTemplates and a small controller, lifted and cleaned from tatara-old's `cmd/controller` + `workflows/`. Depends on tatara-memory being live and tatara-cli being inside the agent image.
- **tatara-tasks** - Task tracking service exposed as REST (not CRD this time, given the microservice direction) + MCP wrapper served by tatara-cli. Replaces the TaskBoard CRD from tatara-old.
- **tatara-gitlab-bridge** - Webhook -> workflow bridge, GitLab only. GitHub support deferred (no current need). Smaller than tatara-old's `cmd/bridge`.

## Open questions

- Which `lightrag` upstream image tag/digest to pin? `latest` works for tatara-old but is unsafe. Decide during implementation; pin the digest.
- Which CloudNativePG and Neo4j chart versions to pin? Decide during implementation; pin in `Chart.yaml`.
- Do we keep tatara-old in a `_old/` subdir of the docs repo or as a totally separate archive? Default: keep it where it is, link from README.
