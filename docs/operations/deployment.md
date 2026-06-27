---
title: Deployment
---

# Deployment

## Resource sizing

### tatara-operator

The operator is lightweight; its compute footprint scales with the number of concurrent tasks (each active task triggers reconciliation loops).

| Environment | Replicas | CPU request | Memory request |
|---|---|---|---|
| Development | 1 | 100m | 128Mi |
| Production | 2-3 | 250m | 256Mi |

The operator uses leader election for scheduling. Non-leader replicas handle webhook delivery and REST API requests.

### tatara-memory (per project)

| Component | CPU | Memory | Storage |
|---|---|---|---|
| tatara-memory service | 250m / 500m | 256Mi / 512Mi | - |
| LightRAG (Python) | 500m / 2000m | 1Gi / 4Gi | - |
| Neo4j | 500m / 2000m | 2Gi / 8Gi | `neo4jStorage` PVC |
| CNPG Postgres (per instance) | 250m / 1000m | 512Mi / 2Gi | `pgStorage` PVC |

Scale `pgInstances` to 3 for production HA.

### Agent pods (per active task)

| Model | Typical peak memory |
|---|---|
| claude-sonnet-4-6 | 256-512Mi per active turn |

Set Pod memory limits conservatively; the wrapper binary itself is minimal - the `claude` process is the memory consumer.

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

GitHub/GitLab retry webhook deliveries on 5xx. If the operator is briefly unavailable, events are re-delivered. The periodic `issueScan` cron backstops any webhooks missed during downtime.

## Network policies

Tatara components should run under tight NetworkPolicies:

- **Operator ingress:** 443 (webhook/REST) from ingress controller
- **Agent pods (implement/review/triage):** in-cluster only (port 443 to operator REST, MCP, memory)
- **Agent pods (brainstorm with internet source):** `ipBlock: 0.0.0.0/0` egress, scoped by pod label `tatara.io/egress: internet`
- **tatara-memory:** ingress from operator and agent pods only
- **Neo4j:** ingress from tatara-memory only

## Upgrades

The operator CRDs are updated in-place by `helm upgrade` (they are included in `templates/crds.yaml`). No separate `kubectl apply -f crds.yaml` is needed for routine upgrades.

For breaking CRD changes, apply the CRD manifest directly before the Helm upgrade:
```sh
kubectl apply -f charts/tatara-operator/templates/crds.yaml
```

## Rollback

Helm rollback reverts the operator deployment but not the CRDs. CRD rollback requires manual intervention if the schema changed. This is rare; the operator maintains backward-compatible CRD evolution.

```sh
# Check rollback history
helm -n tatara history tatara-operator

# Rollback to previous revision
helm -n tatara rollback tatara-operator
```
