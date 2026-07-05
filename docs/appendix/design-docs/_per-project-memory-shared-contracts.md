# Per-project memory: shared contracts (pin set)

Authoritative cross-milestone agreements for the per-project memory plans
(N1-N4). The spec
(`docs/superpowers/specs/2026-06-07-per-project-memory-provisioning-design.md`)
is intent; this pins exact names/paths/signatures so the milestones compose.
Work happens in the deployed `tatara-operator` repo
(`/Users/szymonri/Documents/tatara/tatara-operator`, branch `main`, Go 1.26,
controller-runtime; envtest via `make test`).

## Source-of-truth files to PORT from (read these; the chart stays the reference)

- lightrag (native port target): `tatara-memory/charts/tatara-memory/charts/lightrag/templates/{deployment,configmap,secret,service,pvc}.yaml`
  - env: secrets `LLM_BINDING_API_KEY`, `POSTGRES_PASSWORD`, `NEO4J_PASSWORD`;
    configmap non-secret env (LLM/embedding binding, `POSTGRES_HOST/PORT/DATABASE/USER`,
    `NEO4J_URI`, working dir); container port 9621.
- tatara-memory service: `tatara-memory/charts/tatara-memory/templates/{deployment,configmap,secret,service}.yaml`
  - env: `HTTP_ADDR=:8080`, `LIGHTRAG_BASE_URL`, `OIDC_ISSUER`, `OIDC_AUDIENCE=tatara-memory`,
    `WORKER_POOL_SIZE`, `LOG_LEVEL`, `PG_DSN` (from cnpg app secret key `uri`).
- cnpg Cluster shape: `tatara-memory/charts/tatara-memory/values.yaml` (`postgres:` block;
  instances, storage, `bootstrap.initdb`, `postInitApplicationSQL` for `CREATE EXTENSION vector`).
  Live reference: `kubectl -n tatara get cluster tatara-memory-postgres -o yaml`.
- neo4j: `tatara-memory/charts/tatara-memory/values.yaml` (`neo4j:` block) - but the
  native build is a SINGLE-NODE community StatefulSet (do NOT reproduce the upstream
  neo4j helm chart). `NEO4J_AUTH=neo4j/<password>`, bolt 7687, http 7474, PVC `/data`.
- Operator wiring to change: `tatara-operator/internal/controller/repository_controller.go`
  (ingest `--base-url`), `internal/ingest/job.go` (base-url arg), `internal/agent/pod.go`
  (agent memory URL env), `cmd/manager/wire.go` + `internal/config/config.go`.

## Naming (all in ns `tatara`, prefix per Project; `<proj>` = Project.Name)

- cnpg Cluster: `mem-<proj>-pg`  (cnpg auto-makes Service `mem-<proj>-pg-rw` + app Secret `mem-<proj>-pg-app` with key `uri`)
- neo4j: StatefulSet+Service `mem-<proj>-neo4j` (bolt `bolt://mem-<proj>-neo4j:7687`), Secret `mem-<proj>-neo4j` (key `password`, also `NEO4J_AUTH`)
- lightrag: Deployment+Service `mem-<proj>-lightrag` (`http://mem-<proj>-lightrag:9621`), PVC `mem-<proj>-lightrag-data`
- tatara-memory: Deployment+Service `mem-<proj>` (`http://mem-<proj>.tatara.svc:8080`), ConfigMap+Secret `mem-<proj>`
- All objects carry labels `app.kubernetes.io/name: tatara-memory`,
  `app.kubernetes.io/instance: mem-<proj>`, `tatara.dev/project: <proj>`, and an
  ownerReference to the Project (Controller=true) for cascade delete.

## CRD additions (`api/v1alpha1`, Project)

```go
// spec.memory (optional pointer *MemorySpec)
type MemorySpec struct {
    PgInstances  int    `json:"pgInstances,omitempty"`  // default 1
    PgStorage    string `json:"pgStorage,omitempty"`    // default "10Gi"
    Neo4jStorage string `json:"neo4jStorage,omitempty"` // default "10Gi"
}
// status.memory
type MemoryStatus struct {
    Phase    string `json:"phase,omitempty"`    // Provisioning|Ready|Failed
    Endpoint string `json:"endpoint,omitempty"` // http://mem-<proj>.tatara.svc:8080
}
```
Defaults applied in the builders/reconciler (not kubebuilder defaults) so an
empty `spec.memory` still provisions. `status.memory.endpoint` is the canonical
per-Project memory URL every other component reads.

## Operator config additions (`internal/config`, env via ConfigMap/Secret)

Add: `MEMORY_IMAGE`, `LIGHTRAG_IMAGE`, `NEO4J_IMAGE`, `OPENAI_SECRET_NAME`
(shared Secret holding key `LLM_BINDING_API_KEY`). REMOVE `MEMORY_BASE_URL`
(replaced by per-Project endpoint). Thread these into the builders via a
`memory.Config` struct.

## `internal/memory` builder package (N1)

Pure functions, no client calls, each returns the object(s) owner-ref'd to the
Project. Signatures (pin exactly):
```go
type Config struct {
    Namespace, MemoryImage, LightragImage, Neo4jImage, OpenAISecretName,
    OIDCIssuer, OIDCAudience string
}
func NamesFor(project string) Names              // func NamesFor, returns struct Names; mem-<proj> family
func PGCluster(p *v1alpha1.Project, cfg Config) *cnpgv1.Cluster
func Neo4jStatefulSet(p, cfg) *appsv1.StatefulSet ; func Neo4jService(...) *corev1.Service
func LightragDeployment(p, cfg) *appsv1.Deployment ; func LightragService/PVC(...)
func MemoryDeployment(p, cfg) *appsv1.Deployment ; func MemoryService/ConfigMap/Secret(...)
func Neo4jPasswordSecret(p, cfg, password string) *corev1.Secret  // generated once
func Endpoint(project, namespace string) string  // http://mem-<proj>.<ns>.svc:8080
```
cnpg types: import `github.com/cloudnative-pg/cloudnative-pg/api/v1` (alias
`cnpgv1`) - add to go.mod and register in the scheme. `PgInstances`/storage read
from `p.Spec.Memory` with defaults (1 / 10Gi / 10Gi).

## Provisioning + status (N2, in ProjectReconciler)

On reconcile: ensure the neo4j password Secret (generate random once, guard on
existence; use `crypto/rand`), then SSA all stack objects (owner-ref Project).
Compute `status.memory.phase`: `Ready` when cnpg Cluster `.status` has
`readyInstances >= instances` AND neo4j StatefulSet `readyReplicas>=1` AND
lightrag + memory Deployments `availableReplicas>=1`; else `Provisioning`
(requeue); component apply error -> `Failed` + `MemoryReady=False` condition.
Set `status.memory.endpoint = memory.Endpoint(p.Name, ns)` once names exist.
`Owns()` cnpgv1.Cluster, StatefulSet, Deployment, Service, PVC, ConfigMap,
Secret.

## Ready-gating wiring (N3)

- `RepositoryReconciler`: resolve owning Project; if
  `project.Status.Memory == nil || project.Status.Memory.Phase != "Ready"` ->
  requeue (no ingest Job). Ingest Job `--base-url` = `project.Status.Memory.Endpoint`
  (replaces the removed global `MEMORY_BASE_URL`).
- `TaskReconciler`: gate Task runs on the Project memory Ready; the wrapper pod's
  memory base-URL env (the value the agent's tatara-cli uses) =
  `project.Status.Memory.Endpoint`. (Find the current pod env that carried the
  memory URL; repoint it from config to the Project endpoint.)

## RBAC additions (N4, operator chart Role, ns `tatara`)

Add: `postgresql.cnpg.io` `clusters` (+`/status`) CRUD; `apps` `statefulsets`
CRUD; core `persistentvolumeclaims` CRUD; secrets verbs widen to
`create;update` (still get/list/watch). Keep existing deployments/services/
configmaps/jobs/pods grants.

## Retire static tatara-memory (N4)

- `infra/helmfile/helmfiles/tatara/helmfile.yaml.gotmpl`: remove the
  `tatara-memory` release; delete `values/tatara-memory/`. Keep `tatara-chat` +
  `tatara-operator`.
- Operator infra values (`values/tatara-operator/{common,default}.yaml`): drop
  `memoryBaseUrl`; add `memoryImage`, `lightragImage`, `neo4jImage`,
  `openaiSecretName`. The shared OpenAI Secret in ns `tatara` is created
  out-of-band (note as a gated step; key `LLM_BINDING_API_KEY`).
- Live: `helm uninstall tatara-memory -n tatara` is a GATED prod step (it is
  empty) - the plan PREPARES it; the human/operator-controller runs the
  uninstall. Do NOT helm-uninstall from a plan task automatically.
- envtest: vendor the cnpg `Cluster` CRD into the test CRD dir so envtest can
  create Cluster objects.
