---
title: Runbooks
---

# Runbooks

Operational runbooks for common tatara failure scenarios. Each entry lists symptoms, diagnosis steps, and the fix.

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
2. **Anthropic API key invalid** - `ANTHROPIC_API_KEY` expired or revoked. Update the Secret and restart.
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
1. **HMAC signature mismatch** - webhook secret in GitHub/GitLab settings doesn't match `WEBHOOK_SECRET` env. Resync the secret.
2. **Reporter allowlist drop** - `spec.scm.reporterLogins` is set and the issue author is not in the list. Intentional if you set this; add the account or clear the list.
3. **WebhookURL not registered** - check `Project.status.webhookURL` and confirm it matches the GitHub/GitLab webhook URL. The URL is set automatically on Project reconcile.
4. **Bot-authored issue** - the operator ignores issues authored by `botLogin` to prevent self-loops. Expected behavior.

---

## Memory stack unavailable

**Symptoms:** Agent logs show `connection refused` to tatara-memory, or `ECONNREFUSED` to the memory URL.

**Diagnosis:**
```bash
kubectl -n tatara get pods -l app=tatara-memory
kubectl -n tatara logs svc/tatara-memory
kubectl -n tatara describe project <project-name> | grep -A5 Memory
```

**Common causes:**
1. **CNPG cluster not ready** - check `kubectl -n tatara get clusters` (cnpg). On first create, allow 2-3 minutes.
2. **LightRAG container crash** - OOM or startup error. Check `kubectl -n tatara logs deploy/tatara-memory -c lightrag`.
3. **Neo4j PVC not bound** - `kubectl -n tatara get pvc | grep neo4j`. If `Pending`, storage class may not have capacity.
4. **Cold-start transient** - a freshly created Project's memory stack takes ~60s to become ready. Ingest jobs and agent turns will retry automatically.

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

**Symptoms:** Agent `code_graph_*` MCP calls fail; tatara-memory logs show `EIO` on Neo4j queries.

**Fix:**
```bash
kubectl -n tatara delete pod -l app=neo4j
```

**Root cause:** Poisoned page-cache after Ceph OSD crash/recovery. The EIO is a cache-read error, not data loss. Restarting Neo4j clears the page cache; data is intact in the underlying CephFS.

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
