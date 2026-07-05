---
title: Enrolling Repositories
---

# Enrolling Repositories

A `Repository` CR tells the operator which git repositories to ingest into memory and monitor for issues and PRs.

## Repository CR

```yaml
apiVersion: tatara.dev/v1alpha1
kind: Repository
metadata:
  name: my-service
  namespace: tatara
spec:
  projectRef: my-project        # REQUIRED: must match the Project CR name
  url: https://github.com/my-org/my-service          # REQUIRED
  reingestSchedule: "0 6 * * *" # REQUIRED: 5-field cron; daily at 06:00 UTC
  defaultBranch: main           # optional (default: main)
  ingestEnabled: true           # optional (default: true)
  semanticIngest: true          # optional (default: true); extra OpenAI extraction pass

  # Optional: override project-level allowlists for this repo
  # reporterLogins: [alice, bob]
  # maintainerLogins: [alice]
```

!!! warning "`url` and `reingestSchedule` are required"
    Both are enforced at CRD admission (`reingestSchedule` is `Required`, `MinLength: 9`, and
    must match a 5-field cron pattern `^(\S+\s+){4}\S+$`). A Repository CR without either is
    rejected by the API server, and the `tatara-project` chart hard-fails render before it ever
    reaches the cluster. `reingestSchedule` drives the per-repo catch-up re-ingest cron; webhook
    push events cover the gaps between runs.

## Via tatara-helmfile (recommended)

Manage Repository CRs via the `tatara-project` chart values in tatara-helmfile. This keeps them under Helm control alongside the Project. The chart renders `project.spec` and each `repositories[].spec` **verbatim** into the CR, so every field lives under a `spec:` level (a flat shape fails render):

```yaml
# values/project-my-project/common.yaml
project:
  name: my-project
  spec:                          # rendered verbatim into Project.spec
    scmSecretRef: tatara-scm     # REQUIRED by the chart
    scm:
      provider: github
      owner: my-org
      botLogin: my-org-bot

repositories:
  - name: my-service
    spec:                        # rendered verbatim into Repository.spec
      url: https://github.com/my-org/my-service          # REQUIRED
      reingestSchedule: "0 6 * * *"                       # REQUIRED (5-field cron)
  - name: my-infra
    spec:
      url: https://github.com/my-org/my-infra
      reingestSchedule: "0 7 * * *"
      semanticIngest: false
```

!!! note "`spec:` nesting is mandatory in chart values"
    `project.spec.scmSecretRef`, `repositories[].spec.url`, and `repositories[].spec.reingestSchedule`
    are checked by the chart. A flat layout (fields directly under `project:` or `repositories[]:`)
    renders an empty Project spec or fails outright. `projectRef` is auto-bound to `project.name`,
    so omit it in `repositories[].spec`.

## What enrollment does

1. **First ingest job:** the operator creates a `batch/v1` ingest Job that clones the repository and runs the ingester (`tatara-memory-repo-ingester`) over the full tree.
2. **Webhook delivery:** push events from the repository (delivered to the manually-configured webhook) trigger incremental re-ingests scoped to the changed files.
3. **Scheduled catch-up:** the required `reingestSchedule` cron runs a periodic full re-ingest in-process in the operator, closing any gap left by missed webhooks.
4. **Issue monitoring:** labeled issues on this repository are picked up by the `issueScan` cron or by direct webhook delivery.

## How ingest works

The ingester is a per-language analyzer pipeline feeding a graph store. Understanding it matters if you are deciding whether the memory graph is good enough to build agent behavior on.

1. **Clone and diff.** Each run is a fresh clone (not a `--since` shallow fetch, which proved unreliable). The ingester computes the change set for the run: a full ingest treats every file as added; an incremental ingest diffs against the last ingested commit into added/modified/renamed/deleted sets.
2. **Language analysis.** A registry of analyzers (Go, JavaScript/TypeScript, Python, Terraform, Helm, plus a docs analyzer and a SCIP index path) matches files by path. Each analyzer walks the repo (pruning `.git`, `node_modules`, `vendor`, `.venv`, and similar) to build a symbol-resolution index, then emits entities, edges, chunks, and symbol rows for its slice of the change set. Parsing is tree-sitter / HCL / Helm-template based; per-file parse failures are logged and the file is quarantined (excluded from the reconcile set) rather than aborting the batch.
3. **Bulk insert into memory.** Entities and edges are pushed to the graph, then chunks. The store is LightRAG over Neo4j (graph) plus CNPG/pgvector (embeddings and document status); LightRAG embeds chunks with OpenAI `text-embedding-3-small`.
4. **Reconcile per changed file.** On an incremental run the ingester reconciles per changed path: modified files replace their prior rows, deleted files purge theirs. Files whose analyzer failed are excluded from the reconcile set so a transient parse error never purges last-good rows with no replacement. A first/full ingest is insert-only.
5. **Backoff on failure.** `status.ingestFailureCount` counts consecutive failures and drives exponential backoff before the next attempt.

## Ingest status

```sh
kubectl -n tatara get repository my-service -o jsonpath='{.status}'
```

| Field | Meaning |
|---|---|
| `phase` | `Ingesting` / `Ingested` / `Failed` |
| `lastIngestedCommit` | SHA of the most recently ingested commit |
| `lastIngestTime` | timestamp of last successful ingest |
| `ingestFailureCount` | consecutive failures; drives exponential backoff |

## Semantic ingest and its graph impact

`semanticIngest` toggles an **additional** extraction pass on top of the analyzer pipeline above. It does not switch the graph on or off, and it has nothing to do with Claude.

When `semanticIngest: true` (the default), each changed file's chunks are also sent to **OpenAI** (`gpt-4o-mini` by default, via `SEMANTIC_MODEL`; base `https://api.openai.com/v1`) for LLM-assisted entity and relationship extraction. This gated pass is a no-op when `OPENAI_API_KEY` is unset. It adds relationship-level semantic edges the static analyzers cannot infer (cross-file intent, informal module dependencies), at a per-file token cost.

With `semanticIngest: false` the graph is built from the deterministic analyzers only: you keep entities, edges from static resolution, chunks, and embeddings, but lose the LLM-inferred semantic edges. Set it `false` for large infrastructure repos where static analysis is sufficient and the extra token spend is not worth it.

!!! note "OpenAI is still required either way"
    Disabling `semanticIngest` removes the `gpt-4o-mini` extraction pass, not the memory stack's OpenAI dependency: LightRAG embeddings always call OpenAI. See [Prerequisites](prerequisites.md#openai-api-key).

## Per-repository allowlist overrides

You can tighten or widen the project-level `reporterLogins` and `maintainerLogins` for a specific repository:

```yaml
spec:
  reporterLogins: [alice, bob]  # non-nil: overrides project list
  maintainerLogins: [alice]
```

A `null` (omitted) field inherits the project list. An explicit empty list `[]` removes all restrictions for that repository. See [Prompt Injection Defenses](../operations/security/prompt-injection.md) for why these lists matter.
