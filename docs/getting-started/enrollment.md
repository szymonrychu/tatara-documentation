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
  projectRef: my-project        # must match the Project CR name
  url: https://github.com/my-org/my-service
  defaultBranch: main
  ingestEnabled: true
  semanticIngest: true          # LLM semantic extraction (set false to save cost)
  reingestSchedule: "0 6 * * *" # daily at 06:00 UTC

  # Optional: override project-level allowlists for this repo
  # reporterLogins: [alice, bob]
  # maintainerLogins: [alice]
```

## Via tatara-helmfile (recommended)

Manage Repository CRs via the `tatara-project` chart values in tatara-helmfile. This keeps them under Helm control alongside the Project:

```yaml
# values/project-my-project/common.yaml
project:
  name: my-project
  scmSecretRef: tatara-scm
  scm:
    provider: github
    owner: my-org
    botLogin: my-org-bot

repositories:
  - name: my-service
    url: https://github.com/my-org/my-service
    reingestSchedule: "0 6 * * *"
  - name: my-infra
    url: https://github.com/my-org/my-infra
    reingestSchedule: "0 7 * * *"
    semanticIngest: false
```

## What enrollment does

1. **First ingest job:** the operator creates an ingest `Job` that clones the repository, chunks files by a language-aware splitter, and bulk-inserts into the project's `tatara-memory` stack (LightRAG + Neo4j).
2. **Webhook delivery:** push events from the repository flow through the project webhook, triggering incremental re-ingests on changed files.
3. **Issue monitoring:** labeled issues on this repository are picked up by the `issueScan` cron or by direct webhook delivery.

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

## Semantic ingest

When `semanticIngest: true` (the default), each changed file is sent to Claude for LLM-powered entity and relationship extraction. This produces richer graph nodes (function signatures, class hierarchies, module dependencies) but incurs per-file token cost.

Set `semanticIngest: false` for large infrastructure repos where AST-only analysis is sufficient.

## Per-repository allowlist overrides

You can tighten or widen the project-level `reporterLogins` and `maintainerLogins` for a specific repository:

```yaml
spec:
  reporterLogins: [alice, bob]  # non-nil: overrides project list
  maintainerLogins: [alice]
```

A `null` (omitted) field inherits the project list. An explicit empty list `[]` removes all restrictions for that repository. See [Prompt Injection Defenses](../operations/security/prompt-injection.md) for why these lists matter.
