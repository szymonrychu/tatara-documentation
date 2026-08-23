---
title: Enrolling Repositories
---

# Enrolling Repositories

Enroll a repository and two things start happening: the operator ingests it into
your Project's memory graph, and it starts watching that repo for labelled issues
and for pull requests worth reviewing. Until a `Repository` CR exists, a Project
has nothing to work on.

You need a `Project` the operator has already accepted. If you don't have one yet,
do [Your First Project](first-project.md) first.

## The Repository CR

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

  # Optional: override the project-level allowlists for this repo.
  # Read "Per-repository allowlist overrides" below before you set either.
  # reporterLogins: [alice, bob]
  # maintainerLogins: [alice]
```

!!! warning "`url` and `reingestSchedule` are required"
    Both are enforced at CRD admission. `reingestSchedule` is `Required`, carries
    `MinLength: 9`, and has to match a 5-field cron pattern
    (`^(\S+\s+){4}\S+$`). A Repository CR missing either one is rejected by the API
    server, and the `tatara-project` chart hard-fails render before it ever reaches
    the cluster. The schedule drives the per-repo catch-up re-ingest; webhook push
    events cover the gaps between runs.

## Through tatara-helmfile (recommended)

Manage Repository CRs through the `tatara-project` chart values, which keeps them
under Helm control alongside the Project they belong to. The chart renders
`project.spec` and each `repositories[].spec` **verbatim** into the CR, so every
field lives under a `spec:` level. A flat shape fails render.

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
      maintainerLogins: [alice, bob]   # without this, nothing is ever approved

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

Because it renders the spec verbatim, any current or future CRD field is settable
here without a chart change.

!!! note "`spec:` nesting is mandatory in chart values"
    The chart checks `project.spec.scmSecretRef`, `repositories[].spec.url`, and
    `repositories[].spec.reingestSchedule`. A flat layout, with fields directly
    under `project:` or `repositories[]:`, renders an empty Project spec or fails
    outright. `projectRef` is auto-bound to `project.name`, so leave it out of
    `repositories[].spec`.

Each Project is one helmfile release of this chart. Adding a Project means adding a
release block in `helmfile.yaml.gotmpl` and a `values/project-<name>/common.yaml`;
adding a repository to an existing Project means one more entry in
`repositories:`.

## What enrollment does

1. **First ingest.** The operator creates a `batch/v1` Job that clones the
   repository and runs `tatara-memory-repo-ingester` over the whole tree.
2. **Webhook delivery.** Push events from the repository, delivered to the webhook
   you registered manually, trigger incremental re-ingests scoped to the changed
   files.
3. **Scheduled catch-up.** The `reingestSchedule` cron runs a periodic full
   re-ingest in-process in the operator, closing any gap left by a missed webhook.
4. **Issue monitoring.** Labelled issues on this repository are picked up by
   webhook delivery, or by the `issueScan` cron if you gave the Project one.
   `issueScan` has no default schedule; without it, webhooks are the only path in.

## How ingest works

The ingester is a per-language analyzer pipeline feeding a graph store. Read this
if you're deciding whether the memory graph is good enough to build agent behavior
on.

1. **Clone and diff.** Each run is a fresh clone. A `--since` shallow fetch was
   tried and proved unreliable. A full ingest treats every file as added; an
   incremental ingest diffs against the last ingested commit into added, modified,
   renamed, and deleted sets.
2. **Language analysis.** A registry of analyzers - Go, JavaScript and TypeScript,
   Python, Terraform, Helm, plus a docs analyzer and a SCIP index path - matches
   files by path. Each analyzer walks the repo, pruning `.git`, `node_modules`,
   `vendor`, `.venv` and similar, builds a symbol-resolution index, then emits
   entities, edges, chunks, and symbol rows for its slice of the change set.
   Parsing is tree-sitter, HCL, or Helm-template based. A per-file parse failure is
   logged and that file is quarantined rather than aborting the batch.
3. **Bulk insert.** Entities and edges go to the graph first, then chunks. The
   store is LightRAG over Neo4j for the graph and CNPG with pgvector for embeddings
   and document status. LightRAG embeds chunks with OpenAI `text-embedding-3-small`.
4. **Reconcile per changed file.** On an incremental run, modified files replace
   their prior rows and deleted files purge theirs. Files whose analyzer failed are
   excluded from the reconcile set, so a transient parse error never purges
   last-good rows with nothing to replace them. A first or full ingest is
   insert-only.
5. **Back off on failure.** `status.ingestFailureCount` counts consecutive failures
   and drives exponential backoff before the next attempt.

## Ingest status

```sh
kubectl -n tatara get repository my-service -o jsonpath='{.status}'
```

| Field | Meaning |
|---|---|
| `phase` | `Ingesting`, `Ingested`, `Failed`, or `MemoryDisabled` |
| `lastIngestedCommit` | SHA of the most recently ingested commit |
| `lastIngestTime` | Timestamp of the last successful ingest |
| `ingestFailureCount` | Consecutive failures; drives exponential backoff |
| `lastIngestFailureTime` | Timestamp of the most recent failure, paired with the count to compute backoff |
| `lastScheduledReingest` | When the cron last fired; the base for the next one |
| `jobName` | The ingest Job currently or most recently running |
| `openIssuesCount` / `openIncidentsCount` | Non-terminal Tasks scoped to this repo, recomputed on reconcile |
| `lastIssueScan` | When a sweep last covered **this** repository |

The default `kubectl get repository` output already shows project, phase, and both
open counts, so it is usually enough on its own.

!!! info "`MemoryDisabled` is not a failure"
    `phase: MemoryDisabled` means the owning Project has `spec.memory.enabled:
    false`. There is no recall corpus to write to, so ingest does not apply and
    short-circuits cleanly instead of retrying. It is deliberately distinct from
    `Failed` (ingest broke) and from waiting on a stack that will become ready.
    Agents on that Project keep working; they are told in their first prompt that
    recall is unavailable and to work from the repository instead.

## Semantic ingest and what it costs

`semanticIngest` toggles an **additional** extraction pass on top of the analyzer
pipeline above. It does not switch the graph on or off, and it has nothing to do
with Claude.

With `semanticIngest: true` (the default), each changed file's chunks also go to
**OpenAI** (`gpt-4o-mini` by default, via `SEMANTIC_MODEL`, base
`https://api.openai.com/v1`) for LLM-assisted entity and relationship extraction.
The pass is a no-op when `OPENAI_API_KEY` is unset. It adds relationship-level
semantic edges the static analyzers cannot infer - cross-file intent, informal
module dependencies - at a per-file token cost.

With `semanticIngest: false` the graph is built from the deterministic analyzers
only. You keep entities, statically resolved edges, chunks, and embeddings, and you
lose the LLM-inferred semantic edges. Turn it off for large infrastructure repos
where static analysis is enough and the extra token spend isn't worth it.

!!! note "OpenAI is still required either way"
    Turning `semanticIngest` off removes the `gpt-4o-mini` pass, not the memory
    stack's OpenAI dependency: LightRAG embeddings always call OpenAI. See
    [Prerequisites](prerequisites.md#openai-api-key). The only way to drop OpenAI
    entirely is `spec.memory.enabled: false` on the Project.

## Per-repository allowlist overrides

You can override the project-level `reporterLogins` and `maintainerLogins` for one
repository:

```yaml
spec:
  reporterLogins: [alice, bob]   # non-nil: replaces the project list for this repo
  maintainerLogins: [alice]
```

Omitting a field inherits the project list. Setting it replaces the project list
for this repository only. An explicit empty list `[]` is meaningful and the two
fields mean **opposite** things when you write one:

| Field | `[]` means |
|---|---|
| `reporterLogins` | Intake is **open** for this repo. The operator acts on issues and comments from any author. |
| `maintainerLogins` | The approver set is **empty** for this repo. Nobody can approve anything here, so no Task on this repo ever leaves `refined`. |

!!! danger "An empty `maintainerLogins` silently stops this repository"
    Nothing fails, nothing warns, and the Repository still ingests and reports
    `Ingested`. Tasks are still minted from labelled issues. They just sit at
    `refined` forever, because no comment from anyone counts as an approval. If one
    repository in a Project has stopped moving while the others work, check this
    field first.

See [Prompt Injection Defenses](../operations/security/prompt-injection.md) for why
both lists matter, and
[Approval Gates](../operations/security/approval-gates.md#the-approval-grammar) for
how an approving comment is actually verified.

## Where to go next

- [Project Configuration](../reference/project-configuration.md) for the cron
  schedules that decide how often anything scans.
- [Repository reference](../reference/repository.md) for every field and status
  value at the API level.
- [The Memory Graph](../explainers/memory-graph.md) for what the ingest you just
  triggered actually built.
