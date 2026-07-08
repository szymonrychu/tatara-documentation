---
title: Runbooks
---

# Runbooks

Operational runbooks for common tatara failure scenarios. Each entry lists symptoms, diagnosis steps, and the fix.

!!! note "Memory-stack naming and selectors"
    The memory stack is **per-Project**, not a single flat `tatara-memory` workload.
    Every object is owned by its `Project` CR and named `mem-<project>-*`:

    | Workload | Kind | Name | Port | Component label |
    |---|---|---|---|---|
    | Memory API | Deployment + Service | `mem-<project>` | 8080 | (none) |
    | Neo4j | StatefulSet | `mem-<project>-neo4j` | 7687 bolt / 7474 http | `neo4j` |
    | LightRAG | Deployment | `mem-<project>-lightrag` | 9621 | `lightrag` |
    | Postgres | CNPG `Cluster` | `mem-<project>-pg` (rw svc `mem-<project>-pg-rw`) | 5432 | - |

    Every object carries `app.kubernetes.io/name=tatara-memory`,
    `app.kubernetes.io/instance=mem-<project>`, and `tatara.dev/project=<project>`;
    Neo4j and LightRAG additionally carry `app.kubernetes.io/component`. There is no
    `app=tatara-memory` label and no `-c lightrag` container - LightRAG is its own
    Deployment. Select one project's whole stack with
    `-l app.kubernetes.io/instance=mem-<project>`, or every project's memory API with
    `-l app.kubernetes.io/name=tatara-memory`.

---

## Agent pod stuck / no turns completing

**Symptoms:** Task in `Running` phase, `turnsCompleted` not incrementing, wrapper `/readyz` fails.

**Diagnosis:**
```bash
kubectl -n tatara get pods -l tatara.io/task=<task-name>
kubectl -n tatara logs <pod-name> -c wrapper --tail=50
kubectl -n tatara logs <pod-name> -c wrapper --previous   # if restarted
```

**Common causes:**
1. **Boot quiescence timeout** - claude process hung during boot dialog detection. Check logs for `bootWait` timeout. Fix: delete the pod; the operator respawns.
2. **Anthropic credential invalid** - the wrapper authenticates with `CLAUDE_CODE_OAUTH_TOKEN`, injected from the `oauth-token` key of the Anthropic Secret (`anthropicSecretName`). An expired or revoked token fails boot. Update the Secret key and let the operator respawn the pod.
3. **OIDC token fetch failure** - Keycloak unreachable. Check `OIDC_ISSUER` and Keycloak health.
4. **MCP server not starting** - `tatara mcp` fails at init. Check `TATARA_MEMORY_URL` and `TATARA_OPERATOR_URL` are reachable from the pod.

---

## Tasks not being created from webhooks

**Symptoms:** Issues labeled `tatara` on GitHub/GitLab produce no `QueuedEvent` or `Task`.

**Diagnosis:**
```bash
kubectl -n tatara logs deploy/tatara-operator | grep webhook | tail -50
kubectl -n tatara get queuedevents
```

**Common causes:**
1. **HMAC signature mismatch** - the secret configured in the GitHub/GitLab webhook does not match the `webhookSecret` key in the Project's `scmSecretRef` Secret (there is no global `WEBHOOK_SECRET` env; the secret is per-Project). Resync the SOPS-encrypted value and the SCM-side webhook config.
2. **Reporter allowlist drop** - `spec.scm.reporterLogins` is set and the issue author is not in the list. Intentional if you set this; add the account or clear the list.
3. **WebhookURL not registered** - check `Project.status.webhookURL` and confirm it matches the GitHub/GitLab webhook URL. The URL is set automatically on Project reconcile.
4. **Bot-authored issue** - the operator ignores issues authored by `botLogin` to prevent self-loops. Expected behavior.

---

## Memory stack unavailable

**Symptoms:** Agent logs show `connection refused` to the memory endpoint
(`http://mem-<project>.<ns>.svc:8080`), or `ECONNREFUSED` to the memory URL.

**Diagnosis:** (substitute the affected `<project>`)
```bash
kubectl -n tatara get pods -l app.kubernetes.io/instance=mem-<project>
kubectl -n tatara logs deploy/mem-<project>
kubectl -n tatara describe project <project> | grep -A5 -i memory
```

**Common causes:**
1. **CNPG cluster not ready** - check `kubectl -n tatara get cluster mem-<project>-pg`. On first create, allow 2-3 minutes.
2. **LightRAG crash** - OOM or startup error. LightRAG is its own Deployment: `kubectl -n tatara logs deploy/mem-<project>-lightrag`.
3. **Neo4j PVC not bound** - `kubectl -n tatara get pvc -l app.kubernetes.io/instance=mem-<project>,app.kubernetes.io/component=neo4j`. If `Pending`, the storage class may lack capacity.
4. **Cold-start transient** - a freshly created Project's memory stack takes ~60s to become ready. Ingest jobs and agent turns retry automatically.

---

## Memory postgres/neo4j replica stuck (HA degraded, API still serving)

**Symptoms:** `Memory postgres or neo4j container stuck waiting` fires (`alerts/tatara-memory.yaml`).
Unlike [Memory stack unavailable](#memory-stack-unavailable), the memory API keeps serving via the
surviving primary, so agent turns are not failing and `TataraMemoryStackFailed` / "Memory stack stuck
not ready" stay silent - only this rule catches the degraded HA member.

**Diagnosis:** (substitute the affected `<project>`; the alert's `pod` label tells you which family)
```bash
kubectl -n tatara cnpg status mem-<project>-pg                              # postgres member
kubectl -n tatara get pods -l cnpg.io/cluster=mem-<project>-pg              # postgres pods
kubectl -n tatara get pods -l app.kubernetes.io/instance=mem-<project>,app.kubernetes.io/component=neo4j  # neo4j pods
kubectl -n tatara describe pod <stuck-pg-or-neo4j-pod>
```

**Common causes:**
1. **WAL/data volume too small** - a cnpg replica crash-loops during basebackup/catchup if its
   volume fills. Check `kubectl -n tatara get pvc -l cnpg.io/cluster=mem-<project>-pg`. WAL lives
   on its own PVC (`spec.memory.pgWalStorage`, default `8Gi`), separate from PGDATA
   (`spec.memory.pgStorage`); a WAL burst during a standby resync can overrun it even when PGDATA
   has headroom. **Durable fix:** raise `pgWalStorage` (storage is monotonic - CNPG's admission
   webhook rejects shrinking it back down).
2. **CephFS `CreateContainerError`** - see [CephFS write-cap wedge](#cephfs-write-cap-wedge-cnpg-checkpoint-hang) below.
3. **Legitimate re-clone in progress, not a false positive** - the rule keys on the container waiting
   *reason* (`CrashLoopBackOff`/`ImagePullBackOff`/`CreateContainerError`/...), not pod-not-ready, so a
   replica genuinely `Running` through basebackup/catchup does not trip it even past 10m.

Act before the remaining primary also fails - a second member down is a full outage, not just degraded HA.

---

## CephFS write-cap wedge (CNPG checkpoint hang)

**Symptoms:** CNPG Postgres pod stuck in `end-of-recovery checkpoint`, pwrite64 hang in `D` state, all agent turns stalled.

**Diagnosis:**
```bash
kubectl -n tatara exec <cnpg-pod> -- ps aux | grep postgres
# Look for pwrite64 in D (uninterruptible sleep) state
ceph health detail | grep cap
```

**Fix:**
```bash
ceph mds fail <standby-replay-mds>  # fails the standby-replay MDS, dropping stale write caps
# CNPG unblocks within seconds
```

**Root cause:** Dead Ceph client sessions (from unclean probe-kill restarts) hold stale write caps on CephFS. The MDS does not release them until the session expires or the MDS fails over. Failing the standby-replay MDS drops caps immediately.

**Durable fix:** Scale CNPG to 3 replicas (`pgInstances: 3`). Consider RBD instead of CephFS for CNPG PVCs.

---

## GitLab approve 401 loop

**Symptoms:** Operator logs show repeated `POST /approve 401` errors; Tasks stuck in `WritebackPending`.

**Explanation:** GitLab returns 401 (not 404) when attempting to approve an MR that the bot has already approved. The operator must treat 401 from `/approve` as idempotent success (same as 404 from unapprove). Check if the operator version includes this fix (swallow 401 as success).

---

## Buildkitd dial timeout (CI image builds)

**Symptoms:** CI workflow fails with `dial tcp buildkitd:1234 i/o timeout`.

**Fix:**
```bash
kubectl -n tatara rollout restart deploy/buildkitd
```

**Root cause:** Stale kube-proxy routing rules after a buildkitd pod restart. The restart flushes the stale entries.

---

## ARC runner jobs stuck in queue

**Symptoms:** CI jobs queue but no runners pick them up. `AutoscalingListener` pod crash-loops.

**Fix:**
```bash
kubectl -n tatara get autoscalinglisteners
kubectl -n tatara delete autoscalinglistener <stale-name>
```

**Root cause:** A newly added ARC runner set can leave a stale `AutoscalingListener` referencing a deleted ERS. The listener crash-loops and permanently queues jobs.

---

## Neo4j EIO errors (not data loss)

**Symptoms:** Agent `code_graph_*` MCP calls fail; `mem-<project>` logs show `EIO` on Neo4j queries.

**Fix:** (restart the affected project's Neo4j pod)
```bash
kubectl -n tatara delete pod -l app.kubernetes.io/instance=mem-<project>,app.kubernetes.io/component=neo4j
```

**Root cause:** Poisoned page-cache after Ceph OSD crash/recovery. The EIO is a cache-read error, not data loss. Restarting Neo4j clears the page cache; data is intact in the underlying storage. Neo4j is a read-projection rebuildable from CNPG via a full re-ingest, so even a lost PVC is recoverable.

---

## Helmfile apply fails "chart not found"

**Symptoms:** `apply.yaml` GitHub Actions workflow fails with `Error: chart not found` or `manifest not found in registry`.

**Fix:** The chart pin in `helmfile.yaml.gotmpl` points to a GC'd Harbor tag. Find the latest SHA with both charts published:

```bash
# List available operator chart tags in Harbor
crane ls harbor.szymonrichert.pl/charts/tatara-operator | tail -10
```

Update the chart `version:` in `helmfile.yaml.gotmpl` and the `image.tag` in `values/tatara-operator/common.yaml` to the same recent SHA, then open a tatara-helmfile PR.

---

## Partial CI publish (operator chart published, project chart missing)

**Symptoms:** `tatara-operator` chart is available in Harbor but `tatara-project` chart is not. `helmfile apply` fails on the `project-tatara` release.

**Fix:** Find the latest `main` SHA where BOTH charts were published successfully:

```bash
# Check Harbor for tatara-project chart
crane ls harbor.szymonrichert.pl/charts/tatara-project | tail -10
```

Use the most recent SHA present in both `tatara-operator` AND `tatara-project` chart lists. Bump all three pins (operator chart, project chart, image tag) to that SHA.
