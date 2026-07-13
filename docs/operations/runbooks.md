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

**Symptoms:** Task sitting in a pod-spawning stage (e.g. `implementing`, `reviewing`), `stats.turns`
not incrementing, wrapper `/readyz` fails. See the
[stage reference](../reference/task-stages.md) for which stages spawn which agent kind.

**Diagnosis:**
```bash
kubectl -n tatara get task <task-name> -o jsonpath='{.status.stage}{" "}{.status.stageReason}{"\n"}'
kubectl -n tatara get pods -l tatara.io/task=<task-name>
kubectl -n tatara logs <pod-name> -c wrapper --tail=50
kubectl -n tatara logs <pod-name> -c wrapper --previous   # if restarted
```

Check which of the three per-stage clocks is armed before assuming the pod itself is at fault
(see the stage reference's clock table): a pod that never becomes ready is on the READINESS
clock and **respawns automatically** (bounded by `maxPodRecreations`, default 3) rather than
failing the Task outright - the terminal reason when that budget is spent is
`pod-recreation-exhausted`, not a stalled-forever state.

**Common causes:**
1. **Boot quiescence timeout** - claude process hung during boot dialog detection. Check logs for `bootWait` timeout. This is the READINESS clock; the operator respawns the pod automatically. If you see this recurring, watch `stats.podRecreations` climb toward `maxPodRecreations` and `stageReason` land on `pod-recreation-exhausted` once exhausted.
2. **Anthropic credential invalid** - the wrapper authenticates with `CLAUDE_CODE_OAUTH_TOKEN`, injected from the `oauth-token` key of the Anthropic Secret (`anthropicSecretName`). An expired or revoked token fails boot. Update the Secret key and let the operator respawn the pod.
3. **OIDC token fetch failure** - Keycloak unreachable. Check `OIDC_ISSUER` and Keycloak health.
4. **MCP server not starting** - `tatara mcp` fails at init. Check `TATARA_MEMORY_URL` and `TATARA_OPERATOR_URL` are reachable from the pod. If instead the MCP server starts but the Task fails instantly with `stageReason=agent-contract-mismatch`, this is not a boot problem - see
   [`failed(agent-contract-mismatch)`](#failedagent-contract-mismatch) below.
5. **Stage-deadline or admission-starved park** - if the Task is `parked` rather than stuck in a pod stage, check `stageReason`. `admission-starved` means it has been waiting on a `maxConcurrentAgents` slot past the 24h admission clock (skipped entirely while the project is paused at `maxConcurrentAgents=0`); `stage-deadline` means an agent was running but blew the per-stage work budget - see the budget table on the [stage reference](../reference/task-stages.md).

---

## Tasks not being created from webhooks

**Symptoms:** Issues labeled `tatara` on GitHub/GitLab produce no `QueuedEvent` or `Task`.

**Diagnosis:**
```bash
kubectl -n tatara logs deploy/tatara-operator | grep webhook | tail -50
kubectl -n tatara get queuedevents
kubectl -n tatara get tasks -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.stage}{" "}{.status.stageReason}{"\n"}{end}'
```

**Common causes:**
1. **HMAC signature mismatch** - the secret configured in the GitHub/GitLab webhook does not match the `webhookSecret` key in the Project's `scmSecretRef` Secret (there is no global `WEBHOOK_SECRET` env; the secret is per-Project). Resync the SOPS-encrypted value and the SCM-side webhook config.
2. **Reporter allowlist drop** - `spec.scm.reporterLogins` is set and the issue author is not in the list. Intentional if you set this; add the account or clear the list.
3. **WebhookURL not registered** - check `Project.status.webhookURL` and confirm it matches the GitHub/GitLab webhook URL. The URL is set automatically on Project reconcile.
4. **Bot-authored issue** - the operator ignores issues authored by `botLogin` to prevent self-loops. Expected behavior.
5. **`maxOpenTasks` cap reached** - the Project's active-Task creation budget (default 6, counts every Task whose stage is pod-eligible; `parked(backlog-sweep)` Tasks do not count against it) is exhausted. A sweep that would exceed it mints nothing this pass. Check `Project.spec.maxOpenTasks` against the current count of active Tasks; raise it or wait for one to clear a stage.
6. **`maxConcurrentAgents=0`** - the Project is paused. At 0, `admit()` short-circuits and no `QueuedEvent` is ever admitted, so no pod and no Task work happens, even though the Task/QueuedEvent CR itself may exist. This is the intended full-project kill switch, not a bug - check `Project.spec.maxConcurrentAgents` if work has stopped platform-wide for one Project.
7. **Landed on `parked(backlog-sweep)` instead** - a webhook-originated Task from a sweep-discovered backlog issue starts parked with no pod and no queue entry by design; it exists only to own the Issue CR until a non-bot comment promotes it to `triaging` (subject to the `maxOpenTasks` cap in cause 5). This is expected, not stuck.

---

## `parked(identity-unverified)`

**Symptoms:** A `clarifying`-stage Task moved to `parked` with `stageReason=identity-unverified`
after a maintainer commented `decision=implement` intent, but the Task never advanced to
`approved`.

**Explanation:** The Task's `clarifying -> approved` transition requires the C.6 approval
grammar to pass for **every** Issue the Task owns, not just one if the Task is multi-issue. The
grammar failed - the operator saw a comment but it did not satisfy the approval check.

**Diagnosis, in order:**

1. **Phrase match.** Confirm the maintainer's comment matches one of `Project.spec.scm.approvalPhrases` under an **anchored, whole-line** match - a phrase embedded mid-sentence or as part of a longer line does not count. Check the exact phrase list on the Project CR.
2. **Commenter identity.** Confirm the commenter's login is in `Project.spec.scm.maintainerLogins` and is **not** the bot's own login (`botLogin`) - a bot self-comment can never satisfy the grammar, by design.
3. **Every owned Issue, not just one.** If the Task owns more than one Issue, every one of them needs its own matching comment from a maintainer login. A Task with three owned Issues and only one approved comment stays parked.

For the full grammar specification (anchoring rules, phrase normalization, multi-issue
semantics) see
[Security: approval gates](../operations/security/approval-gates.md#the-approval-grammar) - this
runbook only tells you what to check, not how the grammar itself works.

**Re-entry:** `identity-unverified` is not one of the narrow `parked` re-entry cases - a fresh
maintainer comment does not automatically retry the grammar. Correct the phrase or login issue
and have the maintainer re-comment with a matching, anchored phrase.

---

## `failed(agent-contract-mismatch)`

**Symptoms:** A Task fails **instantly** on entering a pod stage, before turn-0 is ever
submitted, with `stageReason=agent-contract-mismatch`. No turn budget was spent.

**Explanation:** The operator and the agent image (wrapper/cli/skills) ship in different helm
releases applied concurrently by the release cascade, so a version-skewed moment is reachable:
an operator upgrade that bumped `TATARA_CONTRACT_VERSION` landed without a matching agent-image
pin bump in the same window (or vice versa). The wrapper's MCP server refuses to start on a
version mismatch, and the operator independently verifies the wrapper's reported contract
version before submitting turn-0 - this failure is the guard working as intended, not a random
crash.

**Diagnosis:**
```bash
kubectl -n tatara logs deploy/tatara-operator | grep agent_contract_mismatch | tail -20
curl -s http://localhost:9090/metrics | grep operator_agent_contract_mismatch_total
```
The `operator_agent_contract_mismatch_total{expected,got,image}` metric tells you which image
is stale: `expected` is the operator's `TATARA_CONTRACT_VERSION`, `got` is what the wrapper
reported, and `image` names the offending pin.

**Fix:** Re-check the helmfile pins for the operator release and the agent-image release
(wrapper/cli/skills) in `tatara-helmfile`. One of them did not advance in step with the other.
Bump the stale pin so both sides agree on the contract version, then let the operator re-admit
the Task (it does not auto-retry; treat it like any other `failed` Task requiring a human look,
per the [stage reference](../reference/task-stages.md)).

See [Deployment](deployment.md#upgrades) for why this window is reachable even when both
pipelines are green.

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
