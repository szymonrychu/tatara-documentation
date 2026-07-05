# Per-Project Memory Provisioning Design

**Date:** 2026-06-07
**Status:** approved design, pre-plan.
**Repo:** `tatara-operator` (extends the deployed v0.1.1) + infra (retire static release).

## Purpose

Make `Project` creation provision a complete, isolated memory backend
(`tatara-memory` Go service + lightrag + neo4j + cnpg postgres) instead of
relying on one shared, statically helm-deployed `tatara-memory`. Each Project
gets its own code-graph + semantic store; the operator owns the stack's full
lifecycle. The static helmfile `tatara-memory` release is retired.

## Goals

- Creating a `Project` provisions its memory stack; deleting it tears the stack
  down (including data).
- Per-Project stacks are isolated (separate DBs, separate graph) but live in the
  shared `tatara` namespace, name-prefixed `mem-<project>-*`.
- Ingest Jobs and agent pods target the owning Project's memory endpoint, gated
  on that stack being Ready.
- No Helm: the operator creates native objects via controller-runtime,
  owner-ref'd to the Project.
- Per-Project memory is in-cluster only (no ingress); reached by service DNS.

## Non-goals (v1)

- Per-Project namespaces (shared `tatara` ns, name-prefixed).
- HA by default (cnpg defaults to 1 instance; neo4j single node).
- Multi-tenant single tatara-memory service (each Project gets its own service).
- Any external ingress for per-Project memory (in-cluster only; reached by svc
  DNS). External/human access can be added later if needed.
- Migrating data from the old shared release (it is empty;
  `docs/2026-06-06-code-graph-production-readiness.md` confirms
  `code_entities=0`).

## Decisions (from brainstorming)

1. **Topology:** full stack per Project, shared `tatara` ns, name-prefixed.
2. **Mechanism:** native objects via controller-runtime (no Helm), owner-ref'd
   to the Project for cascade delete.
3. **Secrets:** operator-generated random postgres + neo4j passwords per Project;
   lightrag OpenAI key from one shared Secret (`OPENAI_SECRET_NAME`); no secrets
   in the CRD.
4. **Delete:** cascade delete everything including PVCs/data.
5. **Footprint:** `spec.memory.pgInstances` (default 1); neo4j single node.
6. **No ingress:** per-Project memory is in-cluster only, reached by service DNS.

## CRD changes (`tatara.dev/v1alpha1`, Project)

### spec.memory (new, optional; all fields defaulted)

```
spec:
  memory:
    pgInstances:   int     # default 1
    pgStorage:     string  # default "10Gi"
    neo4jStorage:  string  # default "10Gi"
```

Images (memory/lightrag/neo4j) and the shared OpenAI secret name are operator
config (below), not per-Project.

### status.memory (new)

```
status:
  memory:
    phase:       string  # Provisioning | Ready | Failed
    endpoint:    string  # in-cluster: http://mem-<project>.tatara.svc:8080
  conditions: []Condition  # adds MemoryReady alongside the existing Ready
```

The existing `status.webhookURL` and the `Ready` (scm-validation) condition are
unchanged.

## Operator config additions (`internal/config`, env via ConfigMap/Secret)

- `MEMORY_IMAGE` (tatara-memory service image, e.g.
  `harbor.szymonrichert.pl/containers/tatara-memory:0.2.0`)
- `LIGHTRAG_IMAGE` (pinned lightrag image ref/digest)
- `NEO4J_IMAGE` (neo4j community image, e.g. `neo4j:5-community`)
- `OPENAI_SECRET_NAME` (shared Secret holding `LLM_BINDING_API_KEY`)

`MEMORY_BASE_URL` is **removed** (replaced by per-Project `status.memory.endpoint`).

## Provisioning (`internal/memory` builders + ProjectReconciler)

`internal/memory` holds pure builder functions (one file per component), each
returning the object(s) for a Project, owner-ref'd to it:

- `pg.go` -> cnpg `Cluster` CR `mem-<proj>-pg`: `instances` from spec, storage
  from spec, `bootstrap.initdb` db/owner `tatara_memory`, `postInitApplicationSQL`
  installing the `vector` extension. cnpg manages the app Secret
  `mem-<proj>-pg-app` (the `uri` key feeds `PG_DSN`).
- `neo4j.go` -> StatefulSet `mem-<proj>-neo4j` (community single node, `NEO4J_AUTH`
  from the generated neo4j Secret), headless+ClusterIP Service (bolt 7687, http
  7474), PVC `neo4jStorage`.
- `lightrag.go` -> Deployment `mem-<proj>-lightrag` + Service (9621) + PVC; env
  wired to the Project's pg (host `mem-<proj>-pg-rw`, db/user, password from the
  pg app Secret) + neo4j (`bolt://mem-<proj>-neo4j:7687`, password from the neo4j
  Secret) + `LLM_BINDING_API_KEY` from `OPENAI_SECRET_NAME`.
- `memory.go` -> tatara-memory Deployment `mem-<proj>` + Service (8080) +
  ConfigMap + Secret; env: `HTTP_ADDR`, `LIGHTRAG_BASE_URL=http://mem-<proj>-lightrag:9621`,
  `OIDC_ISSUER`/`OIDC_AUDIENCE=tatara-memory`, `PG_DSN` from the pg app Secret
  `uri`, `WORKER_POOL_SIZE`, `LOG_LEVEL`.
- `secrets.go` -> the generated neo4j password Secret (and any pg auth not handled
  by cnpg). Passwords generated once; not regenerated on subsequent reconciles
  (guard on existence).

`ProjectReconciler` (extended): on reconcile, ensure the generated Secret(s),
then server-side-apply all stack objects (owner-ref Project). Compute
`status.memory`:
- `phase=Provisioning` until: cnpg `Cluster` reports ready instances + neo4j pod
  Ready + lightrag Deployment available + memory Deployment available; then
  `phase=Ready`, set `endpoint`. On a component error, `phase=Failed` + a
  `MemoryReady=False` condition with the reason. Requeue while Provisioning.

The reconciler `Owns()` the created kinds (cnpg Cluster, StatefulSet,
Deployment, Service, PVC, ConfigMap, Secret) so health/spec changes re-trigger
reconcile.

## Per-Project wiring + Ready gating

The single biggest ripple from today's global `MEMORY_BASE_URL`:

- **RepositoryReconciler:** resolve the owning Project; if
  `Project.status.memory.phase != Ready`, requeue (do not launch ingest). When
  Ready, the ingest Job `--base-url` = `Project.status.memory.endpoint`.
- **TaskReconciler:** likewise gate Task runs on the Project's memory Ready; the
  spawned wrapper pod's memory base URL (agent tatara-cli mcp-config /
  `MEMORY_BASE_URL`-equivalent pod env) = `Project.status.memory.endpoint`.

Auth is unchanged: every per-Project memory service uses audience
`tatara-memory`, so the operator's client-credentials token and the agents'
tatara-cli tokens already validate (the `tatara` scope carries the audience).

## Retiring the static tatara-memory

- Remove the `tatara-memory` release from
  `infra/helmfile/helmfiles/tatara/helmfile.yaml.gotmpl` and delete
  `values/tatara-memory/`.
- `helm uninstall tatara-memory -n tatara` (and the cnpg/neo4j it owns). It is
  empty, so no data migration. `tatara-chat` is untouched.
- Drop `MEMORY_BASE_URL` from operator config + the infra operator values; add
  the new memory/lightrag/neo4j image refs + OPENAI_SECRET_NAME.
- The tatara-memory **chart** remains in its repo as the reference the native
  lightrag/memory builders are ported from; not deployed.

## RBAC additions (chart)

The operator Role must also manage: cnpg `clusters.postgresql.cnpg.io`
(create/get/list/watch/update/patch/delete), `statefulsets.apps`,
`persistentvolumeclaims`, and continue with deployments/services/configmaps/
secrets. Secrets verbs widen to create/update (generating per-Project passwords)
- still namespaced to `tatara`.

## Observability

`operator_memory_provision_duration_seconds` (histogram),
`operator_memory_stacks` (gauge by phase). Business actions
(stack created/ready/failed/deleted) logged at INFO with `project` +
`resource_id`. CRD `status.memory.conditions`.

## Testing

- `internal/memory` builders: pure unit tests asserting object shape (names,
  owner refs, env wiring, cnpg instances/storage from spec, password-secret
  generated once).
- ProjectReconciler: envtest - provisions the stack, sets `status.memory`,
  transitions Provisioning->Ready as fakes/owned objects report healthy, cascade
  on delete.
- Repository/Task reconcilers: envtest - gate on memory Ready; ingest/pod use the
  Project endpoint.
- cnpg `Cluster` CRD must be installed in envtest (vendor the CRD into the test
  assets).

## Build decomposition (milestones)

- **N1 `internal/memory` builders** (pg/neo4j/lightrag/memory/secrets) +
  config additions. Pure, unit-tested.
- **N2 ProjectReconciler provisioning** + `status.memory` + Ready health +
  cascade. envtest (with cnpg CRD vendored).
- **N3 per-Project endpoint wiring** + Ready-gating in Repository/Task
  reconcilers; remove `MEMORY_BASE_URL`.
- **N4 retire static tatara-memory** (infra helmfile + uninstall) + operator
  chart RBAC/values additions + image bump + redeploy.

## Open picks (stated, not blocking)

- **neo4j community single node** as a native StatefulSet (the upstream neo4j
  Helm chart's HA/LB features are not reproduced).
- **No ingress** for per-Project memory; in-cluster service DNS only.
- Per-Project passwords are operator-generated and **not rotated** by reconcile
  (generated once, guarded on existence).
