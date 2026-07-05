# tatara-operator Design

**Date:** 2026-06-06
**Status:** approved design, pre-plan.
**Repo:** `github.com/szymonrychu/tatara-operator` (new, phase 9-ish).

## Purpose

A Kubernetes operator that orchestrates the tatara platform's unattended
agentic-development loop. It manages four CRDs (`Project`, `Repository`,
`Task`, `Subtask`), ingests repositories into `tatara-memory`, receives
GitHub/GitLab webhooks to keep memory fresh and to start work from issues,
and spawns `tatara-claude-code-wrapper` pods (with `tatara-cli` as their MCP
server) to do the work, landing results back in the SCM.

It subsumes three previously-scoped phases that were never built as separate
repos: `tatara-tasks` (phase 6, REST task store - the CRDs are the store
now), `tatara-gitlab-bridge` (phase 7, webhook bridge - built in), and the
orchestration role of `tatara-argo-workflows` (retired for tatara - replaced
by operator-native Pod/Job spawning). The old monolith's
Smith/Forge/Bellows/Ingot/TaskBoard CRDs are the lineage; this is the clean
rewrite with simpler names and no Argo.

## Goals

- Adding a `Repository` to a `Project` ingests it into `tatara-memory`.
- Each `Project` exposes a webhook URL that GitHub/GitLab can call on push
  (filtered to the default/main branch) to refresh memory incrementally.
- The same webhook accepts work-item events (issue/MR) and, when they carry
  the Project's trigger label, creates a `Task` and runs an agent on it
  unattended.
- A `Task` runs as one long-lived agent session; `Subtask`s are the unit of
  work, fed to the session one turn at a time; the agent self-plans subtasks
  via MCP.
- Agent output lands as a branch push + PR/MR + a comment on the originating
  issue.
- Directly closes production-readiness gaps 1, 2, 4, 8 (see
  `docs/2026-06-06-code-graph-production-readiness.md`).

## Non-goals (v1)

- Embedding/LLM backend for semantic chunks (gap 3) - a `tatara-memory`
  config concern, not the operator's.
- Retrieval-quality eval harness (gap 6) and scale validation (gap 9).
- Agent checkpoint/resume across pod restarts (owned by the wrapper
  ROADMAP). v1 retries by recreating the pod, bounded.
- gVisor/seccomp/Kata pod hardening (wrapper ROADMAP). v1 ships a basic
  egress-allowlist NetworkPolicy only.
- Per-user RBAC. OIDC audience gating only, matching the rest of tatara.

## Architecture

One Go binary (newest stable Go, pinned in `go.mod` - rule 1), built with
kubebuilder/controller-runtime, running three concerns in one process:

1. **Manager** - controller-runtime reconcilers for the four CRDs.
2. **Webhook server** - HTTP endpoint receiving GitHub/GitLab events,
   HMAC-verified per Project.
3. **REST API** - OIDC-gated (audience `tatara-operator`) CRUD over
   Project/Repository/Task/Subtask, backed by the CRDs. This is the only
   interface agents touch; agent pods get an OIDC token, never kube
   credentials. Mirrors how `tatara-memory` and `tatara-chat` are reached.

CRDs are the single source of truth. The webhook server and REST API are
two write paths into the same CRDs the reconcilers act on. Everything is
namespaced to `tatara`.

Rejected alternatives: Argo-backed reconcilers (argo retired for tatara,
heavyweight for "a pod and a job"); plain client-go without kubebuilder
(boilerplate, no envtest, non-idiomatic).

### Component diagram

```
   GitHub/GitLab ──push/issue/MR──▶ [webhook server] ──┐
                                                        │ creates/updates CRDs
   agent pod (tatara-cli) ──OIDC──▶ [REST API] ─────────┤
                                                        ▼
                                              ┌──────────────────┐
                                              │  CRDs (k8s API)  │
                                              │ Project/Repo/    │
                                              │ Task/Subtask     │
                                              └────────┬─────────┘
                                                       │ watch
                                              ┌────────▼─────────┐
                                              │   reconcilers    │
                                              └───┬─────────┬────┘
                            ingest Job (ingester) │         │ Pod+Service (wrapper)
                                                  ▼         ▼
                                          tatara-memory   claude session
```

## CRD model (`tatara.dev/v1alpha1`)

All namespaced. Field names are camelCase in spec/status per kubebuilder
convention; this is not the values.yaml rule-6 surface.

### Project

```
spec:
  scmSecretRef: string         # Secret with provider token + webhook HMAC secret
  triggerLabel: string         # default "tatara"; label/command that starts a Task
  maxConcurrentTasks: int      # default 3; cap on running Task pods in this Project
  agent:
    model: string              # passed to wrapper MODEL
    image: string              # wrapper image ref
    permissionMode: string     # default "bypassPermissions"
    maxTurnsPerTask: int       # default 50; hard turn cap per Task
    turnTimeoutSeconds: int    # default 1800
status:
  webhookURL: string           # the address to configure in GitHub/GitLab
  conditions: []Condition
```

`scmSecretRef` points at a Secret with keys: `token` (provider PAT, used for
clone + PR/MR + comments) and `webhookSecret` (HMAC verification). One Secret
per Project; the operator mounts/uses it for that Project's ingest and agent
pods.

### Repository

```
spec:
  projectRef: string           # owning Project (also set as ownerReference)
  url: string                  # git remote (https)
  defaultBranch: string        # default "main"; the only branch push re-ingests
  ingestEnabled: bool          # default true
status:
  phase: string                # Pending|Ingesting|Ingested|Failed
  lastIngestedCommit: string   # SHA; feeds --since on next ingest
  lastIngestTime: Time
  jobName: string              # current/last ingest Job
  conditions: []Condition
```

### Task

```
spec:
  projectRef: string
  repositoryRef: string        # which Repository the agent works in
  goal: string                 # the objective (issue body for webhook-born Tasks)
  source:                      # optional; set for webhook-born Tasks
    provider: string           # github|gitlab
    issueRef: string           # owner/repo#123 or project!iid
    url: string
  maxTurns: int                # optional override of Project.agent.maxTurnsPerTask
status:
  phase: string                # Pending|Planning|Running|Succeeded|Failed
  podName: string
  turnsCompleted: int
  prURL: string                # PR/MR opened on completion
  resultSummary: string
  conditions: []Condition
```

Execution model is fixed: one Task = one wrapper session pod, multi-turn,
shared context. There is no per-Task execution-mode switch in v1.

### Subtask

```
spec:
  taskRef: string              # owning Task (also ownerReference)
  title: string
  detail: string
  order: int
status:
  phase: string                # Pending|Running|Done|Failed
  turnId: string               # wrapper turn that executed this subtask
  result: string
```

Subtask has **no independent reconciler**. It is data created/updated by the
agent (via the REST API / MCP) and consumed by the Task reconciler's turn
loop. Owner-referenced to its Task for cascade delete.

## Reconcilers

### ProjectReconciler

- Validate `scmSecretRef` exists and has `token` + `webhookSecret`.
- Compute and publish `status.webhookURL` (the operator's external webhook
  base + the Project name).
- Surface `maxConcurrentTasks` enforcement state in conditions (the Task
  reconciler does the actual gating).

### RepositoryReconciler

Drives an **ingest Job** using the `tatara-memory-repo-ingester` image,
which already carries the Go toolchain needed for type-resolved edges
(closes gap 2). The Job:

1. Clones `spec.url` at `spec.defaultBranch` using the Project SCM token.
2. Runs `tatara-ingest --repo-root <clone> --base-url <tatara-memory>
   [--since <status.lastIngestedCommit>]` with an OIDC token minted from the
   operator's service-account client.
3. On success, the reconciler records `status.lastIngestedCommit` (the
   cloned HEAD SHA), `lastIngestTime`, `phase=Ingested`.

Triggered on Repository create (`lastIngestedCommit` empty -> full ingest)
and on push webhook (incremental via `--since`). Idempotent; one active Job
per Repository at a time (`status.jobName` guards re-entry). Job is
owner-referenced to the Repository.

### TaskReconciler

The core loop. Spawns a singleton agent session and drives it turn by turn.

1. **Gate**: if running Tasks in the Project >= `maxConcurrentTasks`, requeue.
2. **Spawn**: create a wrapper **Pod + Service** (owner-ref Task). The pod
   gets: `REPO_URL`/`REPO_BRANCH` from the Repository, the Project SCM token
   (for clone + push), an OIDC token, and `tatara-cli` mcp-config wiring
   memory + operator + chat MCP servers. `DEFAULT_CALLBACK_URL` points at the
   operator's internal turn-callback endpoint. `phase=Planning`.
3. **Plan turn (turn 0)**: `POST /v1/messages` with the goal plus an
   instruction to decompose into Subtasks via the subtask MCP tool. The agent
   creates Subtask CRDs through the REST API.
4. **Iterate**: on each turn callback, mark the current Subtask `Done` with
   its result, pick the next `Pending` Subtask (by `order`), and
   `POST /v1/messages` with that Subtask's title+detail. `phase=Running`.
   The agent may append new Subtasks mid-run.
5. **Terminate**: when no `Pending` Subtasks remain, or `maxTurns`/timeout is
   hit. Then: the agent has pushed its branch; the operator opens a PR/MR
   against the Repository default branch and comments the result on
   `source.issueRef` (if set). Record `prURL`, `resultSummary`,
   `phase=Succeeded` (or `Failed`).
6. **Cleanup**: `DELETE /v1/session` (wrapper pod exits); delete Pod+Service.
   Bounded retries (recreate pod) on mid-run pod loss; no resume in v1.

The turn loop is callback-driven: the wrapper POSTs each turn result to the
operator's internal endpoint, which records it and triggers the next
reconcile. A periodic requeue backstops missed callbacks (poll
`GET /v1/messages/{turnId}`).

## Webhook server

One ingress path: `<external-base>/operator/webhooks/{project}`. Provider
auto-detected from headers (`X-GitHub-Event` / `X-Gitlab-Event`). Payload
HMAC-verified against the Project's `webhookSecret`. Unknown project or bad
signature -> 401/404, no CRD mutation.

- **Push event**, ref == `refs/heads/<Repository.defaultBranch>` only (the
  main-branch filter): find the Repository in the Project by remote URL,
  trigger an incremental re-ingest (clears nothing; relies on `--since`).
  Closes gap 8.
- **Work-item event** (issue opened / MR opened / trigger-comment) carrying
  the Project's `triggerLabel`: create a Task with `goal` = issue/MR body and
  `source` populated. The TaskReconciler picks it up and runs it unattended.

Both GitHub and GitLab are first-class behind a small `scm` interface with
two implementations (`github`, `gitlab`) covering: clone URL auth, open
PR/MR, comment on issue/MR, and webhook payload parsing/verification.

## REST API (agent-facing)

OIDC-gated, audience `tatara-operator`. CRUD scoped by Project:

```
GET   /projects, /projects/{p}
GET   /projects/{p}/repositories
GET   /projects/{p}/tasks, GET /tasks/{t}
PATCH /tasks/{t}                       # status notes from the agent
GET   /tasks/{t}/subtasks
POST  /tasks/{t}/subtasks              # agent self-plans
PATCH /subtasks/{s}                    # mark Done / add result
```

Backed directly by the k8s API (the operator's manager client). A new
`tatara-cli` MCP tool group (`project_*`, `repo_*`, `task_*`, `subtask_*`)
maps these 1:1, the same way `tatara-cli` already wraps memory and (planned)
chat. This `tatara-cli` change ships alongside the operator.

## Security, observability, deploy

- **NetworkPolicy** on agent + ingest pods: egress allowlist to
  tatara-memory, the operator REST/webhook service, tatara-chat, the SCM
  host(s), and DNS. Ingress denied except operator -> wrapper. (gVisor/seccomp
  deferred to the wrapper ROADMAP.)
- **Observability**: JSON `slog` everywhere (rule 11); business actions at
  INFO with structured fields (rule 12); Prometheus `/metrics` (rule 13) -
  reconcile counts/errors per kind, ingest Job duration/result, turn
  duration, webhook events by provider/result, in-flight Tasks gauge. CRD
  `status.conditions` for human/k8s-native visibility.
- **OIDC**: new confidential Keycloak client `tatara-operator` with service
  accounts enabled (the operator mints tokens to call tatara-memory and the
  wrapper). Also a `tatara-operator` audience so agent `tatara-cli` tokens
  reach the REST API. Defined in
  `infra/terraform/keycloak/tatara_clients.tf`.
- **Chart**: created via `helm create` then edited (rule 5), cluster-agnostic
  (rule 14) - no baked regcred/affinity/ingress host/storage class. CRDs
  installed by the chart. values.yaml holds only camelCase scalars mapped
  through ConfigMap/Secret via `envFrom` (rule 6). Release added to the infra
  helmfile `tatara` bucket.
- **Tests**: envtest for reconcilers (controller-runtime), httptest for the
  webhook server and REST API, a fake `scm` implementation for write-back
  paths. Table-driven (`t.Run`), errors wrapped with `%w`.

## Production-readiness gaps addressed

From `docs/2026-06-06-code-graph-production-readiness.md`:

- **Gap 1** (nothing populates the graph): Repository -> ingest Job. Closed.
- **Gap 2** (ingest needs clone + Go toolchain): ingest Job uses the
  ingester image (Go toolchain) and clones with SCM creds. Closed.
- **Gap 4** (agents can't query): agent pods run `tatara-cli` mcp-config'd
  against memory/operator/chat. Closed.
- **Gap 8** (no freshness automation): push webhook -> main-filtered
  incremental re-ingest, last-ingested-commit tracked in Repository status.
  Closed.
- **Gap 5** (never run e2e): the operator is the mechanism that makes the
  first real unattended run possible. Enabled.
- Out of scope, noted: **gap 3** (embedding backend), **gap 6** (eval
  harness), **gap 9** (scale validation).

## Build decomposition (milestones for the plan)

Each milestone is independently testable; built in order.

- **M0 - scaffold**: kubebuilder project, four CRD types + deepcopy, `go.mod`
  (newest Go), `internal/obs` (slog + metrics), `internal/auth` (OIDC),
  chart skeleton.
- **M1 - Project + Repository + ingest**: ProjectReconciler,
  RepositoryReconciler, ingest Job spawning, last-ingested-commit tracking.
  (gaps 1, 2)
- **M2 - webhook server (push)**: HMAC verify, provider detection, push ->
  main-filtered incremental re-ingest. (gap 8)
- **M3 - REST API + tatara-cli MCP tools**: OIDC-gated CRUD + the
  `tatara-cli` tool group.
- **M4 - Task reconciler + turn loop**: wrapper Pod+Service spawning, turn
  callbacks, subtask iteration, concurrency gating.
- **M5 - SCM write-back + work-item->Task**: `scm` interface (github +
  gitlab), branch/PR/MR/comment, work-item webhook -> Task. (gap 4 e2e)
- **M6 - chart + deploy wiring**: NetworkPolicy, metrics, Keycloak client,
  infra helmfile `tatara` release.

## Open decisions resolved (picks)

- Agent session runs as a **bare Pod + Service**, not a Deployment/Job: it is
  an addressable singleton that exits on completion; node loss is handled by
  bounded recreate, not reschedule.
- The REST task store **lives in this operator** (CRD-backed), subsuming the
  separate `tatara-tasks` service.
- Subtask has **no controller**; it is data driven by the Task loop + REST.
