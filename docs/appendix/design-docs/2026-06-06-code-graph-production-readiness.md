# Code-Graph: Production-Readiness Gap Analysis

**Date:** 2026-06-06
**Status:** assessment. Services deployed; graph empty; pipeline never run on real code.
**Scope:** what is missing to use the tatara code-graph/memory system against a
real (large, multi-repo, legacy) codebase.

## Verified current state (2026-06-06)

- **tatara-memory v0.2.0 is live** in the `tatara` namespace (revision 3,
  `/readyz` 200). New routes `/code/entities` and `/code/cross-repo` return 401
  (mounted + auth-gated). Startup migrations ran.
- **The code graph is empty.** Direct query against `tatara_memory` on
  `tatara-memory-postgres-1`:

  ```
  code_entities=0  code_edges=0  cross_repo_symbols=0  deleted_memories=0
  ```

  The v0.2.0 tables exist (migrations applied) but nothing has been ingested.
- **The ingester is built and merged but not deployed and never run.** Its only
  coverage is unit tests + httptest stubs; it has never pushed a real repo's
  graph/chunks into the live service.
- **lightrag `LLM_BINDING_API_KEY` is empty** (inline). The embedding/LLM backend
  for the semantic-chunk path is likely unconfigured - needs verification.
- **tatara-cli is not released/installed** where agents run, and
  **tatara-claude-code-wrapper is not deployed** (only an unrelated `mtg/claude-job-runner`
  scaled to 0 exists). No agent can currently call the code-graph MCP tools.

## Gaps, prioritized

### Critical path - nothing is usable until these

1. **Nothing populates the graph.** The ingester must run against real repos.
   - Fastest first run: it is a batch CLI - run
     `tatara-ingest --repo-root <path> --base-url <memory>` locally with an OIDC
     token, no in-cluster Job required.
   - Durable version: in-cluster deploy = Keycloak service-account client + image
     in Harbor + a Job/CronJob release in the infra helmfile + sops client secret.
2. **The ingester runtime must type-check the target repos.** Unaddressed
   operational requirement: the Go analyzer uses `golang.org/x/tools/go/packages`,
   which needs a real checkout, the `go` toolchain, and module download
   (network/cache). A bare Job will not have these. Whatever runs the ingester
   must clone the repo (git creds) and be able to `go build` it. Python/JS/TF/Helm
   are self-contained (tree-sitter compiled in, hcl/yaml pure-Go); only Go needs
   this (and Go is the platform's primary language). The non-buildable-Go
   tree-sitter fallback covers degraded cases but type-resolved edges need a real
   build environment.
3. **The semantic half may be dead.** `LLM_BINDING_API_KEY` is empty; if there is
   no working embedding backend, `/memories:bulk` chunks will not embed and
   semantic search will not work (only the structural graph). Decide:
   structural-graph-only for now, or wire a real embedding/LLM provider (sops
   secret) first. Verify whether the key arrives via a secretRef before assuming
   it is broken.
4. **Agents cannot query it.** tatara-cli (with `code_search` / `code_entity` /
   `code_neighbors` / `code_callers` / ... / `code_cross_repo`) is not installed
   where agents run, and the wrapper is not deployed. A populated graph is useless
   until tatara-cli is installed and `mcp-config`'d into the agent environment
   (the wrapper image).

### Validation - does it actually work on real code

5. **End-to-end has never run against a real repo.** First real ingest (e.g. the
   tatara repos, or the `~/Documents/tatara-old` legacy monolith) will surface the
   real edge cases: FQN collisions, path scoping at scale, bulk-push sizes,
   embedding throughput, analyzer crashes on real files.
6. **No retrieval-quality signal (M1 eval harness).** The M3 confidence numbers
   are uncalibrated priors. Before trusting retrieval on a large/legacy codebase,
   build the eval (query -> expected entities/edges, recall@k, BM25 baseline) and
   calibrate. Phase-9, not built.
7. **M2 cross-repo and M5 SCIP unproven on real data.** The M2 join works on
   synthetic fixtures; Python/JS name-based symbol keys will be noisier on real
   repos. M5 reference-edge attribution is known-broken on real SCIP indexes
   (def occurrences range over the name token, not the body) - documented in the
   ingester ROADMAP.

### Keeping it useful - freshness and scale

8. **No freshness automation.** `--since` requires the caller to supply the base
   commit; nothing tracks last-ingested-commit per repo or re-ingests on push.
   For a continuously-fresh codebase you need a trigger (the GitLab bridge / CI /
   CronJob) - Phase 7, not built. B is deliberately stateless about this.
9. **No scale validation** for the stated goal ("massive, multi-repo, legacy"):
   recursive-CTE traversal depth, bulk-push sizes, and embedding throughput are
   untested at volume.

## Shortest route to "in a real codebase"

1. **Run the ingester locally** against the tatara repos, pointed at the live
   tatara-memory, with an OIDC token. Populates and validates the structural side
   immediately, no new infra. (Gaps 1 + 5, structural.)
2. **Confirm/fix the embedding backend** if semantic search is wanted (gap 3),
   otherwise accept structural-graph-only for the first pass.
3. **Install tatara-cli + mcp-config** for the agents (gap 4).

The Keycloak client + in-cluster Job + freshness automation + eval harness make
it durable and trustworthy, but are not required for a first real run.

## Already shipped (for reference)

- A (tatara-memory code-graph store + API), B (ingester: walker + 5 analyzers +
  docs + push client + M3 confidence), C (tatara-cli MCP tools), M2 (cross-repo
  symbols across A/B/C), Go tree-sitter fallback, M5 SCIP v1. tatara-memory v0.2.0
  deployed. See `ROADMAP.md` phases 3 + 9 and the specs under
  `docs/superpowers/specs/2026-06-0*`.
