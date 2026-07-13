---
title: Deployment
---

# Deployment

## Resource sizing

!!! note "Basis for these numbers"
    The figures below are **conservative starting points**, not measured p95 from a
    production fleet at scale. Tatara's reference deployment runs two Projects on a
    single homelab cluster, so treat these as floors to boot from and then right-size
    against the observability signals called out per workload. Each row names the
    dashboard/alert metric that tells you when to raise it - see
    [Observability](observability.md).

### tatara-operator

The operator is lightweight. Its memory footprint tracks the number of active Tasks
it is supervising - watch `operator_task_stage{stage,kind}` (a gauge, not a
counter): each pod-eligible stage holds reconcile state, a watch on its wrapper pod
(where one is running), and turn-submit bookkeeping. Because Tasks are now
long-lived (they persist across many pods over a stage's lifetime, see
[Runbooks](runbooks.md)), the memory-relevant signal is pod count, not Task count -
watch agent pod count against a Project's `maxConcurrentAgents` gate.

| Environment | Replicas | CPU request | Memory request |
|---|---|---|---|
| Development | 1 | 100m | 128Mi |
| Production | 2-3 | 250m | 256Mi |

The operator uses leader election for scheduling. Non-leader replicas handle webhook delivery and REST API requests; only the leader runs crons, reconciliation, and business-metric emission.

### tatara-memory (per project)

One full stack per Project (`mem-<project>-*`, see the [Runbooks](runbooks.md)
topology note). The two heavy consumers are LightRAG and Neo4j, and both scale with
corpus size: track `operator_lightrag_documents{project=...}` (the per-project
LightRAG corpus gauge). LightRAG peak memory correlates with document count and
concurrent ingest fan-out; Neo4j heap tracks the graph node/edge count derived from
the same corpus. Size these against the corpus of your largest Project, not the
smallest.

| Component | CPU (req/lim) | Memory (req/lim) | Storage |
|---|---|---|---|
| Memory API (`mem-<project>`) | 250m / 500m | 256Mi / 512Mi | - |
| LightRAG (`mem-<project>-lightrag`) | 500m / 2000m | 1Gi / 4Gi | `mem-<project>-lightrag-data` PVC (fixed 10Gi, not spec-configurable) |
| Neo4j (`mem-<project>-neo4j`) | 500m / 2000m | 2Gi / 8Gi | `neo4jStorage` PVC |
| CNPG Postgres (`mem-<project>-pg`, per instance) | 250m / 1000m | 512Mi / 2Gi | `pgStorage` PVC |

Scale `pgInstances` to 3 for production HA. Aggregate footprint is roughly linear
in the number of Projects, since there is no shared memory stack - budget node
capacity accordingly.

### Agent pods (per active task)

The wrapper binary itself is minimal; the `claude` process is the memory consumer,
and peak memory is driven by the working-tree size and turn transcript, not the
model. `Project.spec.agent.modelByKind` / `effortByKind` key on the **agent
kind** (`brainstorm|incident|clarify|refine|review|documentation|implement`), not
the Task's origin `kind` - see [Tuning](tuning.md#cap-spend) for per-kind
model/effort tiering.

The admission unit is the **agent pod**, not the Task: `Project.spec.maxConcurrentAgents`
(default 3) gates concurrently-running pods, and `0` is the full-project pause kill
switch (no `QueuedEvent` is ever admitted at 0, so no pod and no Task work
happens). `agentPodTTLSeconds` (default 3600, minimum 300) bounds one pod's life;
the Task it belongs to persists and gets a fresh pod on the next stage entry or
re-entry.

| Workload | Typical peak memory |
|---|---|
| Wrapper pod (any model) | 256-512Mi per active turn |

Set Pod memory limits conservatively. For per-kind cost/latency capacity planning,
read `operator_task_tokens_total` (`model`-labelled) rather than assuming a single
model across all kinds.

## Storage

### CNPG (Postgres)

Tatara uses [CloudNativePG](https://cloudnative-pg.io/) for managed Postgres. The operator creates a `Cluster` CR per project. Requirements:

- A `StorageClass` with `accessModes: [ReadWriteOnce]`
- For Ceph environments: use RBD, not CephFS (CephFS has known fragility under unclean pod restarts that can wedge the Postgres process)

### Neo4j

PVC with `ReadWriteOnce`. Neo4j is a read-projection of the CNPG data; losing the Neo4j PVC is recoverable by triggering a full re-ingest.

### S3 (conversation persistence)

Any S3-compatible backend. The operator uses `AWS SDK v2` with configurable endpoint. Tested with:
- AWS S3
- Ceph RGW (`s3Endpoint: http://rook-ceph-rgw-ceph-objectstore.rook-ceph`)
- MinIO

Use the Ceph OBC (Object Bucket Claim) endpoint, not a hand-configured service name, to avoid DNS NXDOMAIN failures.

## High availability

### Operator

Deploy 2-3 replicas. The operator uses `controller-runtime` leader election (Lease CR). Only the leader runs crons and reconciliation; non-leaders handle webhook and REST traffic.

```yaml
# values/tatara-operator/default.yaml
replicaCount: 2
```

### Memory stack

Set `pgInstances: 3` on the Project CR for HA Postgres. Neo4j does not have a HA mode in the default chart; the graph is rebuilable from CNPG.

### Queue persistence

The `QueuedEvent` admission queue is persisted as Kubernetes CRs, not in-memory. A restart of the operator does not lose queued events.

### Webhook delivery

GitHub/GitLab retry webhook deliveries on 5xx. If the operator is briefly unavailable, events are re-delivered. The periodic sweep cron backstops any webhooks missed during downtime.

## Network policies

Tatara components should run under tight NetworkPolicies:

- **Operator ingress:** 443 (webhook/REST) from ingress controller
- **Agent pods (clarify/implement/review):** in-cluster only (port 443 to operator REST, MCP, memory)
- **Agent pods (brainstorm with internet source):** `ipBlock: 0.0.0.0/0` egress, scoped by pod label `tatara.io/egress: internet`
- **tatara-memory:** ingress from operator and agent pods only
- **Neo4j:** ingress from tatara-memory only

`tatara-chat` is archived and fully decommissioned: its helm release, Ingress <!-- stale-ok: tatara-chat -->
path, and egress NetworkPolicy rule are removed. There is no chat Service to
route to in this or any later release train.

## Releases and the deploy chain

The semver push-CD cascade that gets a merged change from a component repo's
`main` onto the cluster - tag cut, chart publish, pin propagation, helmfile
apply - is documented in [Architecture: CI/CD](../architecture/ci-cd.md). This
page covers the operational side: how a human drives a deploy, and what runs
where.

Applies go through the ARC (Actions Runner Controller) runner in
`tatara-helmfile`, whose ServiceAccount is **cluster-admin scoped** - the
single highest-risk surface in the platform. Anything running in
`arc-runner-tatara-helmfile` can do anything to the cluster. Treat changes to
that runner, its RBAC, or its trigger conditions as the most sensitive class of
change in the whole system; see the highest-risk-surface note in the platform
`CLAUDE.md` for the standing rule.

## Upgrades

The operator CRDs are updated in-place by `helm upgrade` (they are included in `templates/crds.yaml`). No separate `kubectl apply -f crds.yaml` is needed for routine upgrades.

For breaking CRD changes, apply the CRD manifest directly before the Helm upgrade:
```sh
kubectl apply -f charts/tatara-operator/templates/crds.yaml
```

!!! danger "Contract-version skew across releases"
    The operator and the agent (wrapper/cli/skills) image ride in **different**
    helm releases applied concurrently by the same push-CD cascade, so a
    version-skewed moment is reachable during a rollout even though both land
    from green pipelines. The operator injects `TATARA_CONTRACT_VERSION` into
    every agent pod; the wrapper's MCP server refuses to start on a mismatch,
    and the operator independently asserts the wrapper's reported contract
    version before turn-0 and fails the Task instantly
    (`stage=failed`, `stageReason=agent-contract-mismatch`) rather than
    burning a turn budget against a 404ing tool surface. Watch
    `operator_agent_contract_mismatch_total` immediately after any operator or
    agent-image upgrade - see [Observability](observability.md) and the
    [runbook](runbooks.md#failedagent-contract-mismatch) if it fires.

## Rollback

Helm rollback reverts the operator deployment but not the CRDs. CRD rollback requires manual intervention if the schema changed. This is rare; the operator maintains backward-compatible CRD evolution.

```sh
# Check rollback history
helm -n tatara history tatara-operator

# Rollback to previous revision
helm -n tatara rollback tatara-operator
```
