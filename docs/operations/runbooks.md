---
title: Runbooks
---

# Runbooks

Operational runbooks for common tatara failure scenarios. Each entry lists symptoms, diagnosis steps, and the fix.

Grafana alert rules link straight into this page. Every rule in
[tatara-observability](https://github.com/szymonrychu/tatara-observability) carries a
`runbook_url` annotation pointing at one of this page's `tatara-runbook-*` anchors, and
the incident agent follows that link as phase 2 of every incident turn. Those anchors are
a cross-repo API, not an implementation detail.

## The anchor contract

Read this before editing this page.

Each alert rule gets its own anchor, declared as one line immediately above the section
that serves it, with no blank line in between:

```html
<a id="tatara-runbook-memory-stack-stuck-not-ready"></a><!-- alert: "Memory stack stuck not ready" status: covered -->
```

- **The anchor id is derived from the alert rule name, not from the heading.** Lowercase
  the rule's `name`, collapse every run of characters outside `[a-z0-9]` to a single `-`,
  strip leading and trailing `-`, and prefix `tatara-runbook-`. Both repos compute it the
  same way and neither keeps a mapping table.
- **Headings are therefore free to change.** Reword a heading, merge two sections, retitle
  the lot: no alert link breaks, because no alert link points at a heading slug. The
  mkdocs auto-slugs stay untouched too, so the in-page links already on this page keep
  working.
- **An anchor may be added; it may never be silently removed or renamed.**
  `scripts/check_runbook_anchors.py` fails CI on a removal, on a duplicate id, and on an
  id that does not match the alert name in its own marker. `mkdocs build --strict` cannot
  catch any of these: it validates internal links, and a link inbound from another repo is
  invisible to it.
- **One section may serve several alerts.** Stack one anchor line per alert above the
  heading. This is why the anchors are explicit `<a id>` elements rather than `attr_list`
  `{ #id }` heading ids, which allow only one id per heading and would displace the
  auto-slug.
- **`status: covered` means a written runbook backs the anchor. `status: none` is an
  honest "no runbook yet" placeholder.** A placeholder still resolves, still tells the
  on-call which rule fired and where it is defined, and keeps the gap countable. Never
  mark an anchor `covered` to make a number look better: a wrong runbook costs more than
  a missing one.
- **Renaming an alert rule renames its anchor.** That is a breaking change across two
  repos: add the new anchor here in the same change, or tatara-observability's
  `scripts/check_runbook_urls.py` fails on a dangling link.

Check it locally with `mise run lint` (or `python3 scripts/check_runbook_anchors.py`),
which also prints the current covered/total count.

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

<a id="tatara-runbook-wrapper-agent-pods-not-becoming-ready"></a><!-- alert: "Wrapper agent pods not becoming ready" status: covered -->
<a id="tatara-runbook-operator-agent-boot-crash-budget-exhausted"></a><!-- alert: "Operator agent boot crash budget exhausted" status: covered -->
<a id="tatara-runbook-operator-agent-unreachable-terminations"></a><!-- alert: "Operator agent unreachable terminations" status: covered -->
## Agent pod stuck / no turns completing

**Alert rules:** `Wrapper agent pods not becoming ready` (`alerts/tatara-wrapper.yaml`), `Operator agent boot crash budget exhausted` and `Operator agent unreachable terminations` (`alerts/tatara-operator.yaml`). All three mean a wrapper pod was created but never became usable; the first is the standing-state view, the other two are the operator giving up on it.

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

<a id="tatara-runbook-operator-webhook-error-ratio-high"></a><!-- alert: "Operator webhook error ratio high" status: covered -->
## Tasks not being created from webhooks

**Alert rule:** `Operator webhook error ratio high` (`alerts/tatara-operator.yaml`, warning) fires when more than 20% of inbound SCM webhook deliveries are rejected over 15m. It covers cause 1 below; the other causes are silent and produce no alert at all.

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

<a id="tatara-runbook-tatara-approval-refusals-elevated"></a><!-- alert: "Tatara approval refusals elevated" status: covered -->
## `parked(identity-unverified)`

**Alert rule:** `Tatara approval refusals elevated` (`alerts/tatara-logs.yaml`, warning) fires on a burst of refused approval attempts in 15m, labelled with the refusal `reason`. A single refusal does not alert; the runbook below applies either way.

**Symptoms:** A `clarifying`-stage Task moved to `parked` with `stageReason=identity-unverified`
after the clarify agent reported `decision=implement`, but the Task never advanced to
`approved`.

**Explanation:** The clarify agent judged that a maintainer approved and cited a comment as
evidence - the forge comment's `external_id` plus a verbatim quote from its body - for every
Issue the Task owns. The operator does not take that judgment on faith: `restapi.verifyApprovalScope`
independently re-derives, for **every** owned Issue, whether the cited comment exists, who posted
it, and whether the quoted text is really there. One of those structural checks failed, so the
operator refused the citation and parked the Task rather than granting an unverified mandate.

**Diagnosis, in order:**

1. **Cited comment exists.** Confirm the `external_id` the agent cited is actually present in `Issue.status.comments` - a stale mirror (the on-demand sync failed, or the comment is genuinely new) means the operator cannot find it at all.
2. **Commenter identity.** Confirm the cited comment's author is in `Project.spec.scm.maintainerLogins` and is **not** the bot's own login (`botLogin`) - a bot self-comment can never satisfy the check, by design.
3. **Quote really occurs in the body.** The `quote` the agent cited must be a verbatim substring of the comment body the operator itself holds - a paraphrase or a quote from a different comment fails this check.
4. **Not already consumed.** A comment already recorded as `ApprovalEvidence` on an Issue cannot approve a second time.
5. **Every owned Issue, not just one.** If the Task owns more than one Issue, every one of them needs its own valid citation. A Task with three owned Issues and only one satisfied stays parked.

There is no most-recent-comment check on the operator's side - it verifies structure, not
sequence, so an older approving comment is still citable even when a newer maintainer comment
exists on the thread. Whether that newer comment withdraws the earlier approval is an intent
question the operator does not ask; it is the clarify agent's job to read the whole thread and
submit `decision=discuss` instead of citing a stale approval when a later maintainer comment
actually walks it back.

For the full grammar specification (what the agent judges versus what the operator verifies) see
[Security: approval gates](../operations/security/approval-gates.md#the-approval-grammar) - this
runbook only tells you what to check, not how the verification itself works.

**Re-entry:** the **next** non-bot comment on the thread un-parks the Task to `conversing`,
spawning a fresh clarify pod against the refreshed thread - it does not re-run the check
directly. That pod reads the new comment, forms its own judgment, and submits a fresh
`decision=implement` with a new citation through the same gate. Have the maintainer post an
unambiguous comment and the next comment event will bring an agent back to read it.

---

<a id="tatara-runbook-operator-agent-contract-version-mismatch"></a><!-- alert: "Operator agent contract version mismatch" status: covered -->
## `failed(agent-contract-mismatch)`

**Alert rule:** `Operator agent contract version mismatch` (`alerts/tatara-operator.yaml`, critical).

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

<a id="tatara-runbook-memory-api-server-pod-not-ready"></a><!-- alert: "Memory API server pod not ready" status: covered -->
<a id="tatara-runbook-memory-stack-stuck-not-ready"></a><!-- alert: "Memory stack stuck not ready" status: covered -->
<a id="tatara-runbook-operator-memory-stack-failed"></a><!-- alert: "Operator memory stack failed" status: covered -->
<a id="tatara-runbook-operator-agent-pods-running-without-memory-recall"></a><!-- alert: "Operator agent pods running without memory recall" status: covered -->
## Memory stack unavailable

**Alert rules:** `Memory API server pod not ready` and `Memory stack stuck not ready` (`alerts/tatara-memory.yaml`, both critical), `Operator memory stack failed` (`alerts/tatara-operator.yaml`, critical) and `Operator agent pods running without memory recall` (warning). The last one is the consequence to act on: agents are no longer blocked by an unready memory stack, so they keep running turns against an empty corpus and ship degraded-quality work silently.

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

<a id="tatara-runbook-memory-postgres-or-neo4j-container-stuck-waiting"></a><!-- alert: "Memory postgres or neo4j container stuck waiting" status: covered -->
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

<a id="tatara-runbook-memory-code-graph-query-errors"></a><!-- alert: "Memory code-graph query errors" status: covered -->
## Neo4j EIO errors (not data loss)

**Alert rule:** `Memory code-graph query errors` (`alerts/tatara-memory.yaml`, warning) fires when code-graph queries error for 15m. A poisoned Neo4j page cache is the known cause; if the fix below does not clear it, treat the errors as a memory-stack problem instead.

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

---

<a id="tatara-runbook-operator-scrape-target-down"></a><!-- alert: "Operator scrape target down" status: covered -->
<a id="tatara-runbook-operator-deployment-has-no-available-replicas"></a><!-- alert: "Operator deployment has no available replicas" status: covered -->
<a id="tatara-runbook-operator-pod-not-ready"></a><!-- alert: "Operator pod not ready" status: covered -->
<a id="tatara-runbook-operator-replica-missing"></a><!-- alert: "Operator replica missing" status: covered -->
## Operator control plane down or degraded

**Symptoms:** Four `deploy/tatara-operator` rules in `alerts/tatara-operator.yaml`. `Operator scrape target down` (critical, `sum(up)<1` for 5m) fires when every replica vanishes from Prometheus's view, including a scale-to-zero. `Operator deployment has no available replicas` (critical, for 5m) fires when kube-state-metrics still sees the Deployment but reports 0 available replicas against a spec calling for >=1. `Operator pod not ready` (critical, for 10m) fires when kube-state-metrics is confirmed up but no pod passes its readiness probe. `Operator replica missing` (warning, for 15m) fires only on partial loss, 1-2 of 3 replicas up; a full 0/3 outage or a scrape gap reports NoData -> OK here and is caught by the three critical rules instead.

**What it means:** The operator is the platform's only control loop: no reconciles, no webhook ingestion, no agent-turn dispatch while any of the three critical rules holds. The warning-level replica-missing rule means leader election and HA are degraded but the loop is still running on the surviving replica(s).

**Diagnosis:**
```bash
kubectl -n tatara get deploy tatara-operator
kubectl -n tatara get pods -l app.kubernetes.io/name=tatara-operator
kubectl -n tatara describe pod -l app.kubernetes.io/name=tatara-operator | tail -40
```
```promql
sum(up{namespace="tatara",job="tatara-operator"}) or vector(0)
count(up{namespace="tatara",job="tatara-operator"} == 1)
```

**Fix:** Check pod events for the failing cause (image pull, scheduling, readiness probe). This is a helm-managed Deployment: never `kubectl set image|edit|patch` it, bump the chart appVersion and let CI/`tatara-helmfile` apply. If pods are healthy but the scrape target itself is gone, check the `ServiceMonitor` and Prometheus's target list. If kube-state-metrics itself is down, see "Kube-state-metrics down" below first, since it gates "Operator pod not ready".

---

<a id="tatara-runbook-kube-state-metrics-down"></a><!-- alert: "Kube-state-metrics down" status: covered -->
## Kube-state-metrics down (kube_* alerts blind)

**Symptoms:** `Kube-state-metrics down` (warning, `alerts/tatara-operator.yaml`, component operator) fires when `absent(up{job="kube-state-metrics"} == 1)` holds for 10m.

**What it means:** kube-state-metrics has stopped reporting. Every alert derived from a `kube_*` metric is blind while this fires - restart counts, OOMKilled reasons, waiting reasons, replica-available gauges all go silent, and "Operator pod not ready" is explicitly gated on this exporter being up, so it cannot fire either while kube-state-metrics is down.

**Diagnosis:**
```bash
kubectl get pods -A -l app.kubernetes.io/name=kube-state-metrics
kubectl get pods -A -l app.kubernetes.io/name=kube-state-metrics -o wide
```
```promql
absent(up{job="kube-state-metrics"} == 1)
```

**Fix:** kube-state-metrics is cluster infrastructure, not a tatara component, so it is out of scope for this repo's own charts. Check whether its pod is crash-looping, unscheduled, or scaled down, and restart or reschedule it. While it is down, treat every `kube_*`-derived tatara alert as unreliable rather than as confirming health, and rely on the operator's own `operator_*` metrics (reconcile, turn-submit, SCM) for control-loop signal instead.

---

<a id="tatara-runbook-operator-crash-looping"></a><!-- alert: "Operator crash looping" status: covered -->
<a id="tatara-runbook-memory-api-server-crash-looping"></a><!-- alert: "Memory API server crash looping" status: covered -->
<a id="tatara-runbook-wrapper-agent-container-crash-looping"></a><!-- alert: "Wrapper agent container crash-looping" status: covered -->
## Workload crash looping

**Symptoms:** Restart-rate rules on the container itself. `Operator crash looping` (critical, `alerts/tatara-operator.yaml`) fires past 2 restarts in a 15m window on the `tatara-operator` container. `Memory API server crash looping` (warning, `alerts/tatara-memory.yaml`) fires past 2 restarts/15m on a `mem-*` pod, excluding the `neo4j`/`pg`/`lightrag` sub-workloads. `Wrapper agent container crash-looping` (warning, `alerts/tatara-wrapper.yaml`) fires past 3 restarts/15m on the `wrapper` container in any agent pod.

**What it means:** The named container is repeatedly starting, failing, and being restarted by the kubelet, not merely slow to become ready. For the operator this means the control loop repeatedly drops out; for the memory API it means one project's memory stack is intermittently unreachable; for a wrapper it means the agent pod owning a Task is not completing turns.

**Diagnosis:**
```bash
kubectl -n tatara get pods -l tatara.io/task=<task-name>          # wrapper
kubectl -n tatara logs <pod> -c wrapper --previous                # wrapper, last crash
kubectl -n tatara logs deploy/tatara-operator --previous
kubectl -n tatara logs deploy/mem-<project> --previous
```
```promql
max by (pod) (increase(kube_pod_container_status_restarts_total{namespace="tatara"}[15m]))
```

**Fix:** Read the previous-container logs for the crash cause before assuming infra: an unhandled panic, a bad config/secret value, or a missed dependency at startup all present as a crash loop. If the terminated reason is `OOMKilled`, see "Workload OOMKilled" below instead. These are all helm-managed workloads: fix the chart value or image and let CI/`tatara-helmfile` roll it out, never patch the running pod directly.

---

<a id="tatara-runbook-operator-oomkilled"></a><!-- alert: "Operator OOMKilled" status: covered -->
<a id="tatara-runbook-memory-api-server-oomkilled"></a><!-- alert: "Memory API server OOMKilled" status: covered -->
<a id="tatara-runbook-wrapper-agent-container-oomkilled"></a><!-- alert: "Wrapper agent container OOMKilled" status: covered -->
<a id="tatara-runbook-tatara-ingest-pod-oomkilled"></a><!-- alert: "Tatara ingest pod OOMKilled" status: covered -->
## Workload OOMKilled

**Symptoms:** Four warning-severity rules key on `kube_pod_container_status_last_terminated_reason{reason="OOMKilled"}`, each `for: 1m` except the wrapper and ingest rules at `for: 5m`. `Operator OOMKilled` (`alerts/tatara-operator.yaml`) is the `tatara-operator` container; `Memory API server OOMKilled` (`alerts/tatara-memory.yaml`) is a `mem-*` pod excluding its `neo4j`/`pg`/`lightrag` sub-workloads; `Wrapper agent container OOMKilled` (`alerts/tatara-wrapper.yaml`) is the `wrapper` container in any agent pod; `Tatara ingest pod OOMKilled` (`alerts/tatara-ingester.yaml`) is any `*-ingest-*` pod.

**What it means:** The kernel OOM killer terminated the container for exceeding its memory limit. Ingest is the most predictable case: a large repository's static-analysis pass can blow the heap on its own.

**Diagnosis:**
```bash
kubectl -n tatara describe pod <pod> | grep -A5 "Last State"
kubectl -n tatara get pod <pod> -o jsonpath='{.status.containerStatuses[*].lastState.terminated.reason}{"\n"}'
```
```promql
kube_pod_container_status_last_terminated_reason{namespace="tatara",reason="OOMKilled"}
```

**Fix:** Raise the container's memory limit; the operator and memory alerts' own summaries also say to check for a leak if raising the limit does not stop the recurrence, and for ingest, bump the ingest job's memory limit for the offending repo size. All four workloads are helm-managed: change the chart's resource values and let CI/`tatara-helmfile` apply, never `kubectl edit` the limit directly.

---

<a id="tatara-runbook-operator-container-stuck-waiting"></a><!-- alert: "Operator container stuck waiting" status: covered -->
<a id="tatara-runbook-memory-api-server-container-stuck-waiting"></a><!-- alert: "Memory API server container stuck waiting" status: covered -->
<a id="tatara-runbook-wrapper-agent-container-stuck-waiting"></a><!-- alert: "Wrapper agent container stuck waiting" status: covered -->
<a id="tatara-runbook-tatara-ingest-pod-stuck-waiting"></a><!-- alert: "Tatara ingest pod stuck waiting" status: covered -->
## Container stuck waiting (image pull / create-container)

**Symptoms:** Four rules on `kube_pod_container_status_waiting_reason{reason=~"CrashLoopBackOff|ImagePullBackOff|ErrImagePull|CreateContainerConfigError|CreateContainerError"}`. `Operator container stuck waiting` (critical, `alerts/tatara-operator.yaml`, for 10m) and `Memory API server container stuck waiting` (critical, `alerts/tatara-memory.yaml`, for 10m) cover their own workloads; `Wrapper agent container stuck waiting` (critical, `alerts/tatara-wrapper.yaml`, for 15m) covers any `wrapper` container; `Tatara ingest pod stuck waiting` (warning, `alerts/tatara-ingester.yaml`, for 10m) covers `*-ingest-*` pods.

**What it means:** The named container is not running at all, never started, as opposed to the crash-looping rules above which fire on a container that starts and dies repeatedly. The `reason` label on the fired alert tells you which of the waiting reasons is holding it.

**Diagnosis:**
```bash
kubectl -n tatara get pods -o wide            # anything stuck Pending or not Running
kubectl -n tatara describe pod <pod> | grep -A10 Events
```
```promql
kube_pod_container_status_waiting_reason{namespace="tatara",reason=~"CrashLoopBackOff|ImagePullBackOff|ErrImagePull|CreateContainerConfigError|CreateContainerError"}
```

**Fix:** `ImagePullBackOff`/`ErrImagePull` means the tag does not exist in Harbor or the pull secret is wrong; charts never bake `imagePullSecrets`, that is cluster-specific and lives in `tatara-helmfile`/infra, so check the pin there. `CreateContainerConfigError`/`CreateContainerError` means a referenced ConfigMap/Secret key is missing; check the chart's `envFrom` sources exist. `CrashLoopBackOff` here means the container is failing before or during create; see "Workload crash looping" above once it is actually starting.

---

<a id="tatara-runbook-operator-reconcile-loop-wedged"></a><!-- alert: "Operator reconcile loop wedged" status: covered -->
<a id="tatara-runbook-operator-reconcile-error-ratio-high"></a><!-- alert: "Operator reconcile error ratio high" status: covered -->
## Operator reconcile loop wedged or erroring

**Symptoms:** Both in `alerts/tatara-operator.yaml`. `Operator reconcile loop wedged` (critical, for 15m) fires when `sum(increase(operator_reconcile_total[15m]))` (with an `or vector(0)` fallback) drops below 1, zero reconciles completed across every replica. `Operator reconcile error ratio high` (warning, for 15m) fires when the error-result share of `operator_reconcile_total` exceeds 0.2 over the same window.

**What it means:** controller-runtime's reconcile loop is either wedged (nothing completing) or completing but failing en masse. Either way, CRD state (`Project`/`Repository`/`Task`/`Issue`/`MergeRequest`/`QueuedEvent`) is not being reconciled to reflect reality, and this includes the stage-deadline enforcement described in the [stage machine reference](../reference/task-stages.md) - a wedged loop means no stage deadline anywhere is being enforced either.

**Diagnosis:**
```bash
kubectl -n tatara logs deploy/tatara-operator --tail=100
kubectl -n tatara get pods -l app.kubernetes.io/name=tatara-operator
```
```promql
sum(increase(operator_reconcile_total{namespace="tatara",job="tatara-operator"}[15m])) or vector(0)
sum(increase(operator_reconcile_total{namespace="tatara",job="tatara-operator",result="error"}[15m])) / clamp_min(sum(increase(operator_reconcile_total{namespace="tatara",job="tatara-operator"}[15m])), 1)
```

**Fix:** Read the operator's own logs around the last successful reconcile for a panic, deadlock, or a specific CR repeatedly failing (only the leader replica emits reconcile activity under leader election, so check which pod is leader before assuming all three are affected). If a genuine wedge with no error logged, a rollout restart of the Deployment is the standard remediation, via `tatara-helmfile`/chart upgrade, not a raw pod delete loop. If the error ratio is high but reconciles are still completing, the logged error per CR name is the actual lead.

---

<a id="tatara-runbook-operator-turn-submit-failure-ratio-high"></a><!-- alert: "Operator turn submit failure ratio high" status: covered -->
<a id="tatara-runbook-operator-turn-submit-p95-latency-high"></a><!-- alert: "Operator turn submit p95 latency high" status: covered -->
<a id="tatara-runbook-operator-agent-http-failure-spike"></a><!-- alert: "Operator agent HTTP failure spike" status: covered -->
## Agent turns failing to dispatch

**Symptoms:** All three in `alerts/tatara-operator.yaml`, warning. `Operator turn submit failure ratio high` (>0.3, for 15m, gated on >=10 attempts) fires when SubmitTurn calls to wrappers are failing. `Operator turn submit p95 latency high` (>30s, for 15m) fires when SubmitTurn is slow. `Operator agent HTTP failure spike` (>0.2/s, for 10m) fires on unreachable/timeout/transport-error outcomes against wrapper pods specifically.

**What it means:** The operator cannot reliably hand a turn to an agent pod's wrapper HTTP API. Slow or failing dispatch stalls whichever Task is in a pod-spawning stage, without necessarily showing up as a crash on either side.

**Diagnosis:**
```bash
kubectl -n tatara get pods -l tatara.io/task=<task-name>
kubectl -n tatara logs <pod> -c wrapper --tail=50
kubectl -n tatara logs deploy/tatara-operator | grep -i turn_submit | tail -50
```
```promql
histogram_quantile(0.95, sum(rate(operator_turn_submit_duration_seconds_bucket{namespace="tatara",job="tatara-operator"}[15m])) by (le))
sum(rate(operator_agent_http_total{namespace="tatara",job="tatara-operator",outcome=~"unreachable|timeout|transport_error"}[10m]))
```

**Fix:** Check the specific wrapper pod(s) the operator is failing to reach: not-yet-ready, crash-looping (see "Workload crash looping" above), or OOMKilled (see "Workload OOMKilled" above) all present as dispatch failure from the operator's side. If pods look healthy, check in-cluster Service/DNS reachability between the operator and the wrapper Service, and wrapper resource saturation under concurrent turns.

---

<a id="tatara-runbook-operator-scm-write-failure-ratio-high"></a><!-- alert: "Operator SCM write failure ratio high" status: covered -->
<a id="tatara-runbook-operator-scm-rate-limited"></a><!-- alert: "Operator SCM rate limited" status: covered -->
<a id="tatara-runbook-operator-mirror-or-webhook-writes-dropped"></a><!-- alert: "Operator mirror or webhook writes dropped" status: covered -->
## SCM writes failing, dropped, or rate limited

**Symptoms:** All warning, `alerts/tatara-operator.yaml`. `Operator SCM write failure ratio high` (>0.3, for 15m) fires when comments/labels/approvals/PRs are failing to land on GitHub/GitLab. `Operator SCM rate limited` (>0, for 5m) fires on any rate-limit response, labelled `provider` and `limit_type`. `Operator mirror or webhook writes dropped` (>0, for 15m) fires on best-effort mirror/webhook writes that got a 200 but were still dropped, labelled `project` and `site`.

**What it means:** SCM write failure means real, visible operator actions (comments, reviews, merges) are not reaching the forge. Rate limiting on GitHub specifically means hitting its secondary limit (80 content-creating requests/min, 500/hour); the Issue/MergeRequest mirror falls behind and every context bundle rendered from it goes stale. The dropped-write rule is the dangerous one: at `site=incident_refire` it silently suppresses incident re-escalation (a re-firing production alert stops escalating with nothing saying so); at `site=comment_append` a human comment is lost from the mirror on a path that still un-parks the Task, so the agent wakes to a comment its own bundle doesn't contain; at `site=issue_body_title` the derived issue-edited event never reaches the owning Task at all.

**Diagnosis:**
```bash
kubectl -n tatara logs deploy/tatara-operator | grep -iE 'scm_write|ratelimit|mirror_write_dropped' | tail -50
```
```promql
sum by (provider, limit_type) (increase(operator_scm_ratelimited_total{namespace="tatara",job="tatara-operator"}[10m])) or vector(0)
sum by (project, site) (operator_mirror_write_dropped_total{namespace="tatara",job="tatara-operator"})
```

**Fix:** Check `tatara-memory` health first for the dropped-write rule: an outage there makes every object-budget-guarded write fail together. For write failures generally, check the SCM token's validity and scope, and the provider's own status. For rate limiting, the write volume against a single Project or the whole platform needs to come down, or wait out the window; there is no override for GitHub's own limit.

---

<a id="tatara-runbook-memory-http-5xx-error-ratio-high"></a><!-- alert: "Memory HTTP 5xx error ratio high" status: covered -->
<a id="tatara-runbook-wrapper-http-5xx-responses"></a><!-- alert: "Wrapper HTTP 5xx responses" status: covered -->
<a id="tatara-runbook-operator-rest-api-error-ratio-high"></a><!-- alert: "Operator REST API error ratio high" status: covered -->
## Service HTTP 5xx error ratio high

**Symptoms:** All warning. `Memory HTTP 5xx error ratio high` (`alerts/tatara-memory.yaml`, >5%, for 10m, excludes `/readyz`/`/healthz`/`/metrics`) covers a project's `mem-<project>` API. `Wrapper HTTP 5xx responses` (`alerts/tatara-wrapper.yaml`, >0/s, for 10m, same probe exclusion) covers the wrapper's own HTTP API. `Operator REST API error ratio high` (`alerts/tatara-operator.yaml`, >0.1, for 15m, gated on >=10 requests) covers the operator's inbound REST API, agent turn callbacks like `patch_task`, `propose_issue`, `*_outcome`, `post_comment`, counting 5xx and Task-gone 404 on the internal-failure path, but not 4xx validation rejections.

**What it means:** A real request path is serving errors, not just being probed. On the operator this specifically means agent-pod writebacks are failing to land, which stalls the owning Task's stage progress even if the pod itself looks healthy.

**Diagnosis:**
```bash
kubectl -n tatara logs deploy/mem-<project> --tail=100
kubectl -n tatara logs <pod> -c wrapper --tail=100
kubectl -n tatara logs deploy/tatara-operator | grep -i restapi | tail -100
```
```promql
sum(rate(http_requests_total{namespace="tatara",pod=~"mem-.+",pod!~"mem-.*-(neo4j|pg|lightrag).*",status=~"Internal Server Error|Bad Gateway|Service Unavailable|Gateway Timeout"}[10m]))
```

**Fix:** Identify the failing route from the logs. If the errors are unrecovered panics rather than handled 5xx, see "Service HTTP handler panic" below. For the memory API, check the project's Postgres/Neo4j/LightRAG dependencies are healthy, since the API's own 5xx often reflects a downstream failure rather than a bug in the API layer itself.

---

<a id="tatara-runbook-memory-api-server-http-handler-panics"></a><!-- alert: "Memory API server HTTP handler panics" status: covered -->
<a id="tatara-runbook-wrapper-http-handler-panics"></a><!-- alert: "Wrapper HTTP handler panics" status: covered -->
## Service HTTP handler panic

**Symptoms:** `Memory API server HTTP handler panics` (warning, `alerts/tatara-memory.yaml`, >0, for 1m) and `Wrapper HTTP handler panics` (critical, `alerts/tatara-wrapper.yaml`, >0, for 5m) fire on any recovered panic in an HTTP handler over a 15m window.

**What it means:** A request handler panicked and was recovered by the server's panic-recovery middleware rather than taking the process down. The request itself still failed, but the container did not crash or restart, so this will not show up in the crash-looping or restart-count rules at all.

**Diagnosis:**
```bash
kubectl -n tatara logs deploy/mem-<project> | grep -i panic
kubectl -n tatara logs <pod> -c wrapper | grep -i panic
```
```promql
sum(increase(http_panics_total{job="tatara-memory"}[15m]))
sum(increase(ccw_http_panics_total[15m]))
```

**Fix:** No confirmed operational fix beyond capturing the stack trace from the recovered-panic log line and filing a code bug against the owning repo (`tatara-memory` or `tatara-claude-code-wrapper`). Each panic's root cause is specific to the request path that triggered it, and there is no generic infra remediation for a code-level defect like this.

---

<a id="tatara-runbook-operator-triage-stage-wedged"></a><!-- alert: "Operator triage stage wedged" status: covered -->
<a id="tatara-runbook-operator-pod-stage-wedged"></a><!-- alert: "Operator pod stage wedged" status: covered -->
<a id="tatara-runbook-operator-human-wait-stage-wedged"></a><!-- alert: "Operator human-wait stage wedged" status: covered -->
<a id="tatara-runbook-operator-approved-stage-starved"></a><!-- alert: "Operator approved stage starved" status: covered -->
## Task stage wedged past its clock

**Symptoms:** All key on `operator_task_stage_age_seconds`, `alerts/tatara-operator.yaml`. `Operator triage stage wedged` (critical, >900s, for 5m) is `stage=triaging` past its 5m budget. `Operator pod stage wedged` (warning, >129600s/36h, for 15m) is any of `brainstorming|investigating|refining|documenting|implementing|reviewing` past 36h. `Operator human-wait stage wedged` (warning, >216000s/60h, for 30m) is `stage=clarifying` past 60h. `Operator approved stage starved` (warning, >129600s/36h, for 30m) is `stage=approved` past 36h.

**What it means:** every Task stage carries a deadline invariant (see the [stage machine reference](../reference/task-stages.md)): triage exits at 5m to `failed(triage-stalled)`; a pod stage's healthy worst case is about 30h (a 24h admission wait plus up to 3 readiness respawns plus a work budget of 6h or less); `clarifying`'s healthy worst case is about 48h (24h admission plus its own 24h work budget); `approved` is pod-less with a 24h work-only budget. Firing past these margins means the deadline machinery itself is not enforcing, not that the Task is merely slow. For `approved`, the one legitimate exception is a project paused at `maxConcurrentAgents=0`, which deliberately holds Tasks there and skips the starve-park.

**Diagnosis:**
```bash
kubectl -n tatara get task <task-name> -o jsonpath='{.status.stage}{" "}{.status.stageReason}{" "}{.status.stageEnteredAt}{"\n"}'
kubectl -n tatara get project <project> -o jsonpath='{.spec.maxConcurrentAgents}{"\n"}'
```
```promql
max by (task, stage) (operator_task_stage_age_seconds{namespace="tatara",job="tatara-operator"})
```

**Fix:** Rule out the `approved`-stage pause exception first. Otherwise this means the operator's own deadline-sweep logic is not running for that Task; check "Operator reconcile loop wedged or erroring" above, since stage-deadline enforcement happens inside the reconcile loop. If reconciles are healthy platform-wide but one Task is still stuck, escalate: this is the deadline-invariant guarantee failing for a single CR, which [the stage reference](../reference/task-stages.md) treats as a bug, not an expected state.

---

<a id="tatara-runbook-operator-task-failure-spike"></a><!-- alert: "Operator task failure spike" status: covered -->
<a id="tatara-runbook-operator-task-park-spike"></a><!-- alert: "Operator task park spike" status: covered -->
## Task failure or park spike

**Symptoms:** `Operator task failure spike` (`alerts/tatara-operator.yaml`, warning) fires when more than 3 Tasks reach `stage=failed` in 1h, sustained 15m, broken out by `stageReason`. `Operator task park spike` (same file, warning) fires when more than 3 Tasks park in 3h, sustained 30m, broken out by `stageReason` - excluding `backlog-sweep` (the zero-cost mint that owns a backlog issue and spawns no pod) and `awaiting-human` (a human not having replied yet is not a platform failure).

**What it means:** Both are cross-cutting spike detectors, not single-cause alerts. A failure spike means Tasks are reaching some terminal `failed(...)` reason at an elevated rate; failed Tasks are kept 7 days as debugging artifacts before the reaper deletes them, so the CRs are still inspectable. A park spike means Tasks are landing in `parked` for any reason other than the two expected, benign ones - per the stage machine's re-entry function, every other `stageReason` (`stage-deadline`, `review-loop-exhausted`, `merge-timeout`, `deploy-timeout`, `admission-starved`, `no-outcome`, `pod-recreation-exhausted`, `agent-contract-mismatch`, and the rest) either matches a narrow re-entry rule or ages out at `parkRetention` (7d) and gets reaped.

**Diagnosis:**
```bash
kubectl -n tatara get tasks -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.stage}{" "}{.status.stageReason}{"\n"}{end}' | sort -k2,3
```
```promql
sum by (stageReason) (increase(operator_task_terminal_total{namespace="tatara",job="tatara-operator",stage="failed"}[1h]))
sum by (stageReason) (increase(operator_task_parked_total{namespace="tatara",job="tatara-operator",stageReason!~"backlog-sweep|awaiting-human"}[3h]))
```

**Fix:** There is no single fix - both alerts exist to point you at the `stageReason` breakdown, then at that reason's own runbook: `pod-recreation-exhausted`, see the Agent pod lost mid-stage runbook below; `merge-timeout`/`deploy-timeout`/`merge-blocked`/`deploy-blocked`, see the Delivery parked or permanently exhausted runbook below; `head-moving`, see the Merge stage not advancing runbook below; `agent-contract-mismatch`, see the `failed(agent-contract-mismatch)` runbook above on this page. A dominant `stage-deadline` or `no-outcome` reason with no other pattern points at the per-stage work budgets themselves - see [Tuning](tuning.md).

---

<a id="tatara-runbook-operator-task-pod-recreation-budget-exhausted"></a><!-- alert: "Operator task pod-recreation budget exhausted" status: covered -->
<a id="tatara-runbook-operator-agent-pod-force-deleted-at-ttl"></a><!-- alert: "Operator agent pod force-deleted at TTL" status: covered -->
## Agent pod lost mid-stage

**Symptoms:** `Operator task pod-recreation budget exhausted` (`alerts/tatara-operator.yaml`, warning) fires on any Task failing in 1h with `stageReason=pod-recreation-exhausted`. `Operator agent pod force-deleted at TTL` (same file, warning) fires when more than 2 agent pods of one `agent_kind` had to be force-deleted at TTL in 1h.

**What it means:** The first is the readiness clock's terminal case: a pod exists but never becomes Ready within the 5-minute `podReadyTimeout`, the operator respawns it, and once `stats.podRecreations` exceeds `maxPodRecreations` (3) the Task fails outright - investigate pod evictions, node pressure, and OOM-kills on the wrapper workload.

The second is a **wrapper-health** signal and, on its own, **not work loss**. `operator_agent_pod_ttl_expired_total` reports two independent labels. `outcome` answers how the POD was stopped (`graceful` or `force_deleted`); `force_deleted` means the graceful G.7 stop failed against a live pod - a wedged turn, an unresponsive PTY - and the operator deleted it with a zero grace period. `handoff` answers how the CONTINUATION STATE was captured, and that is the label that decides whether anything was lost. A Task whose agent wrote a perfect handoff note and whose wrapper then failed to tear down cleanly is `outcome=force_deleted, handoff=agent`: nothing is missing.

This runbook and the alert both asserted the opposite until tatara-operator#527 - that `force_deleted` meant "neither an agent handoff nor a synthetic one" - and that state was never reachable in the code. Work loss now has its own alert; see [Agent pod TTL-stopped with no handoff captured](#tatara-runbook-operator-agent-pod-ttl-stopped-with-no-handoff-captured) below.

**Diagnosis:**
```bash
kubectl -n tatara get pods -l tatara.io/task=<task-name>
kubectl -n tatara describe pod <pod-name> | grep -A5 -i "evicted\|oom\|node-pressure"
kubectl -n tatara get task <task-name> -o jsonpath='{.status.stats.podRecreations}{"\n"}'
```
```promql
sum(increase(operator_task_terminal_total{namespace="tatara",job="tatara-operator",stage="failed",stageReason="pod-recreation-exhausted"}[1h]))
sum by (agent_kind, handoff) (increase(operator_agent_pod_ttl_expired_total{namespace="tatara",job="tatara-operator",outcome="force_deleted"}[1h]))
```

**Fix:** For the recreation-budget alert, address the node-level cause the summary names - evictions, node memory pressure, OOM-kills - on the nodes the `wrapper` workload lands on; there is no operator-side retry left once the budget is spent. For force-deleted-at-TTL, split by `handoff` first (second query above). If `handoff` is `agent` or `synthetic`, continuity is intact and this is purely a teardown problem: check wrapper logs for what the pod was doing right up to the force-delete. If `handoff` is `none`, follow the runbook below - that is the work-loss case, and the force-delete is incidental to it.

---

<a id="tatara-runbook-operator-agent-pod-ttl-stopped-with-no-handoff-captured"></a><!-- alert: "Operator agent pod TTL-stopped with no handoff captured" status: covered -->
## Agent pod TTL-stopped with no handoff captured

**Symptoms:** `Operator agent pod TTL-stopped with no handoff captured` (`alerts/tatara-operator.yaml`, warning) fires on any agent pod, by `agent_kind`, TTL-stopped in 1h with `handoff=none`.

**What it means:** Silent work loss, and the Task will look healthy. The G.7 stop sequence guarantees `Task.status.notes` is non-empty after every TTL stop, but `handoff=none` is the case where non-empty is not the same as useful. Both sources of a handoff failed at once:

- the agent did not answer the one handoff turn the wrapper still admits past t0 (it was mid-turn at the hard cap, the wrapper 410'd/409'd it, or the pod was already gone), **and**
- `Task.status.lastTurn` was absent, so the operator had no `finalText` and no `pushedRepos` to build a synthetic note from.

What lands instead is a placeholder note leading with `NO CONTINUATION STATE WAS CAPTURED`. The next pod for that Task starts from a bundle whose only note says that, re-runs turn-0, and charges the retry to `maxTurnsPerTask`. Nothing about the Task's stage or conditions records the loss.

`status.lastTurn` is written by the turn-complete callback and cleared on every stage transition, so it is legitimately absent in exactly one benign case: the pod was TTL-stopped **before its first turn ever completed** in this stage. A Task that was still rendering turn-0 when its TTL expired has genuinely produced nothing to hand off. Check `status.stats.turns` and the pod's age before treating it as a defect.

`status.lastTurn` deliberately SURVIVES a mid-stage pod respawn (the crash/eviction path writes no handoff note, so it is the vanished pod's only surviving trace). A pod that TTL-stops before completing a turn of its own can therefore still land `handoff=synthetic` from an earlier pod's payload - which is correct, and is why the synthetic note dates the turn it was built from. If that timestamp predates the current pod, the work done since it is unrecorded even though the stop was not counted as `handoff=none`.

**Diagnosis:**
```promql
sum by (agent_kind) (increase(operator_agent_pod_ttl_expired_total{namespace="tatara",job="tatara-operator",handoff="none"}[1h]))
sum by (agent_kind, outcome, handoff) (increase(operator_agent_pod_ttl_expired_total{namespace="tatara",job="tatara-operator"}[6h]))
```
```logql
{namespace="tatara", app="tatara-operator"} | json | action="agent_pod_ttl_stop" | handoff="none"
```
```bash
# the affected Task's continuation state, and whether it had produced a turn at all
kubectl -n tatara get task <task-name> -o jsonpath='{.status.lastTurn}{"\n"}{.status.stats.turns}{"\n"}'
kubectl -n tatara get task <task-name> -o jsonpath='{range .status.notes[*]}{.at} {.agent} {.kind}: {.body}{"\n"}{end}'
```

**Fix:** Nothing recovers the lost turns - there is nothing persisted to recover from. Triage in this order:

1. **Confirm it is not a pre-first-turn stop.** `status.stats.turns == 0` with a pod younger than one `agentPodTTLSeconds` is the benign case; no action.
2. **Check whether the turn-complete callback is landing at all.** `handoff=none` on Tasks that have completed turns means `status.lastTurn` is not being written - look for `record last-turn continuation payload (non-fatal)` errors in the operator log, and for callback rejections (`callback_authn_failed`, HMAC skew) that would drop the payload before it is persisted.
3. **Check whether the wrapper is being torn down before the stop reaches it.** `reap_orphan reason="idle no live turn"` on the same Task shortly before the stop means the idle backstop and the pod TTL are fighting. The reaper stands down for the window in which the G.7 stop owns the pod - from t0 until the stop's own hard cap, `t0 + 2*turnTimeoutSeconds + 60s` - so this should not recur. The stand-down is bounded on purpose: a reconcile that never reaches the TTL gate would otherwise exempt a wedged pod from the idle backstop forever. A pod reaped past that cap is the backstop working, not this bug.
4. **Treat the affected Tasks as discontinuous.** Any Task whose notes journal contains only the `NO CONTINUATION STATE WAS CAPTURED` placeholder has lost its prior turns; do not read it as clean continuity when reviewing what the next pod produced.

---

<a id="tatara-runbook-operator-agent-turn-timeout-spike"></a><!-- alert: "Operator agent turn timeout spike" status: covered -->
## Agent turn timeout spike

**Symptoms:** `Operator agent turn timeout spike` (`alerts/tatara-operator.yaml`, warning) fires when more than 5 agent turns are terminated for stalling in 1h, sustained 15m.

**What it means:** This is the operator-side turn-execution stall detector, watching for a wedged model session or a hung wrapper across the `reconcile`, `poll_backstop`, and `planning_watchdog` sources. It is distinct from the SubmitTurn RPC latency rule - this one fires on turns the operator itself terminated for inactivity, burning turn budget and blocking the owning Tasks from advancing.

**Diagnosis:**
```bash
kubectl -n tatara logs deploy/tatara-operator | grep -i "turn_timeout\|stall" | tail -50
kubectl -n tatara get pods -l tatara.io/task=<task-name>
kubectl -n tatara logs <pod-name> -c wrapper --tail=100
```
```promql
sum(increase(operator_turn_timeout_total{namespace="tatara",job="tatara-operator"}[1h]))
```

**Fix:** Identify which Task(s) and pods are hitting the timeout from the operator logs, then check the wrapper's own logs for the hang signature (a wedged Claude session, an unresponsive PTY, an MCP call that never returns). A single wedged pod respawns and costs one turn; a sustained spike across many Tasks points at something systemic - an MCP dependency (memory, operator API) hanging under load rather than one bad turn. Escalate to a human if the pattern repeats across unrelated Tasks.

---

<a id="tatara-runbook-operator-handoff-drain-stalled"></a><!-- alert: "Operator handoff drain stalled" status: covered -->
## Handoff drain stalled

**Symptoms:** `Operator handoff drain stalled` (`alerts/tatara-operator.yaml`, warning) fires when more than 0 Tasks park in 1h with `stage=parked, stageReason=handoff-stalled`, sustained 5m.

**What it means:** A review outcome was accepted and the PR review already landed, but the cross-reconciler drain (`MergeRequestReconciler` -> `DrainPendingReview` -> `advanceAfterReview`) did not advance the Task within its 5-minute handoff deadline, so it parked instead of progressing. This can be a dead drain - the owning `MergeRequest` CR was deleted, the workqueue item was dropped, or leader election changed over mid-drain - or a false positive: an SCM/forge degradation that slowed the drain past 5m without actually breaking it. Recovery today is a backlog-sweep re-mint; a human should confirm which case this is before waiting on that.

**Diagnosis:**
```bash
kubectl -n tatara get task <task-name> -o jsonpath='{.status.stage}{" "}{.status.stageReason}{"\n"}'
kubectl -n tatara get mergerequests -l tatara.io/task=<task-name>
kubectl -n tatara logs deploy/tatara-operator | grep -i "DrainPendingReview\|advanceAfterReview" | tail -50
kubectl -n tatara get leases | grep tatara-operator
```
```promql
sum(increase(operator_task_terminal_total{namespace="tatara",job="tatara-operator",stage="parked",stageReason="handoff-stalled"}[1h]))
```

**Fix:** Confirm the owning `MergeRequest` CR still exists and check operator logs around the park time for a leader-election change or a dropped workqueue item. If the MergeRequest is gone or the drain clearly never ran, this is a dead drain needing a code fix or a manual nudge. If the SCM/forge was visibly degraded for longer than 5m in the same window, treat it as a false positive - the work is fine, it just parked slower than the deadline allows. `handoff-stalled` has no automatic re-entry; the Task ages out at `parkRetention` and the next backlog sweep re-mints its still-open issue.

---

<a id="tatara-runbook-operator-illegal-stage-transition"></a><!-- alert: "Operator illegal stage transition" status: covered -->
## Illegal stage transition (code bug, not an outage)

**Symptoms:** `Operator illegal stage transition` (`alerts/tatara-operator.yaml`, warning) fires when more than 0 attempted transitions (`from`, `to` labels) are logged in 15m, sustained 5m.

**What it means:** Any non-zero value here is a code bug in the operator's stage-transition table, not an operational condition. The transition table (see [the stage reference](../reference/task-stages.md)) is the closed set of legal `from -> to` edges; a transition outside it is rejected by the reconciler, logged at ERROR, and counted here instead of being silently applied. Firing means some code path attempted an edge the table does not allow.

**Diagnosis:**
```bash
kubectl -n tatara logs deploy/tatara-operator | grep -i "illegal.*transition\|ERROR.*stage" | tail -50
```
```promql
sum by (from, to) (increase(operator_illegal_stage_transition_total{namespace="tatara",job="tatara-operator"}[15m]))
```

**Fix:** There is no operational remediation - the Task itself is not corrupted (the rejected transition never applied), but the code path that attempted it needs a fix in the operator's transition table. Capture the `from`/`to` labels and the Task name from the log line and file it as a bug against `tatara-operator`; this is not something a restart or a config change resolves.

---

<a id="tatara-runbook-operator-agent-pod-pool-saturated-with-queued-work"></a><!-- alert: "Operator agent pod pool saturated with queued work" status: covered -->
<a id="tatara-runbook-operator-queue-depth-backlog"></a><!-- alert: "Operator queue depth backlog" status: covered -->
<a id="tatara-runbook-operator-incident-starved-in-triage"></a><!-- alert: "Operator incident starved in triage" status: covered -->
## Agent pod pool saturated / queue backlog

**Symptoms:** `Operator agent pod pool saturated with queued work` (`alerts/tatara-operator.yaml`, warning) fires when running `wrapper` pods stay at the `2 x maxConcurrentAgents=3` ceiling with `QueuedEvents` still waiting, for 30m. `Operator queue depth backlog` (same file, warning) fires when queue depth for a `(project, class)` pair exceeds 10, for 30m. `Operator incident starved in triage` (same file, critical) fires when the oldest queued incident (`class=alert, priority=0`) has waited over 5m in `state=Queued` while the alert pool is not reporting itself full.

**What it means:** The first two describe the normal pool running hot: at the concurrency ceiling with events queueing, or a per-project/class queue simply growing faster than it drains. Incidents hold a reserved slot (`alertCapacity`, default 1), so this is not by itself an incident outage - it is every webhook and sweep event queueing behind a full normal pool. The third alert is different in kind: `alertCapacity` normally guarantees an incident admits ahead of a busy normal pool, and the rule explicitly suppresses the legitimate case of 3 incidents already in flight and a 4th waiting. If it still fires, priority admission itself is broken or the whole triage path is wedged - a firing production alert is not being worked.

**Diagnosis:**
```bash
kubectl -n tatara get pods -l tatara.io/task -o wide
kubectl -n tatara get queuedevents -o jsonpath='{range .items[*]}{.spec.projectRef}{" "}{.spec.class}{" "}{.spec.priority}{" "}{.status.state}{"\n"}{end}'
kubectl -n tatara get project <project> -o jsonpath='{.spec.maxConcurrentAgents}{" "}{.spec.queue}{"\n"}'
```
```promql
sum(kube_pod_container_status_running{namespace="tatara",container="wrapper"})
max by (project, class) (operator_queue_depth{namespace="tatara",job="tatara-operator"})
max(operator_queue_age_seconds{namespace="tatara",job="tatara-operator",class="alert",priority="0",state="Queued"})
```

**Fix:** For pool saturation or a queue-depth backlog, raise `maxConcurrentAgents` (or `queue.capacity`) on the affected Project if the backlog is real proactive/webhook load - see [Tuning](tuning.md) - or investigate why running pods are not completing turns (see the Agent turn timeout spike runbook above) if the ceiling is being held by stuck work rather than throughput. For the incident-starved alert, check `operator_admission_blocked_total{reason="pool_full"}` yourself to rule out the legitimate 4th-incident case, then check whether the triage path (webhook receipt through to pod admission) is actually processing anything for that project.

---

<a id="tatara-runbook-tatara-merge-stage-wedged"></a><!-- alert: "Tatara merge stage wedged" status: covered -->
<a id="tatara-runbook-tatara-merge-cursor-stalled"></a><!-- alert: "Tatara merge cursor stalled" status: covered -->
<a id="tatara-runbook-tatara-merge-head-kept-moving"></a><!-- alert: "Tatara merge head kept moving" status: covered -->
## Merge stage not advancing

**Symptoms:** `Tatara merge stage wedged` (`alerts/tatara-cd.yaml`, warning) fires when a Task has spent over 7200s (half the 4h merge budget) in `stage=merging`, sustained 10m. `Tatara merge cursor stalled` (same file, warning) fires when a Task's merge cursor has not advanced past one repo in `spec.mergeOrder` for over 3600s, sustained 15m. `Tatara merge head kept moving` (same file, warning) fires on any Task failing in 1h with `stageReason=head-moving`.

**What it means:** `merging` walks `Task.spec.mergeOrder` sequentially from `status.mergeCursor`, merging each repo's reviewed MR against its live head SHA. A wedged merge stage means that walk has stopped: a required CI check is red, the reviewed head moved and forced a 409, or the forge is refusing the merge - it parks at `parked(merge-timeout)` at the full 4h budget. A stalled cursor localizes the same failure to one specific repo in the order - and because the merge is sequential, every repo behind it in `mergeOrder` is blocked too. `head-moving` is the bounded-cycle exhaustion case: the reviewed PR's head kept moving across 3 `reviewing <-> merging` laps (a human pushing to the branch, or a flapping CI autocommit), spawning a fresh review pod each lap before the operator gave up.

**Diagnosis:**
```bash
kubectl -n tatara get task <task-name> -o jsonpath='{.status.stage}{" "}{.status.mergeCursor}{" "}{.status.headMoveReentries}{"\n"}'
kubectl -n tatara get task <task-name> -o jsonpath='{.spec.mergeOrder}{"\n"}'
kubectl -n tatara get mergerequests -l tatara.io/task=<task-name>
```
```promql
max by (task) (operator_task_stage_age_seconds{namespace="tatara",job="tatara-operator",stage="merging"})
max by (task, repo) (operator_merge_cursor_stalled_seconds{namespace="tatara",job="tatara-operator"})
sum(increase(operator_task_terminal_total{namespace="tatara",job="tatara-operator",stage="failed",stageReason="head-moving"}[1h]))
```

**Fix:** For a wedged stage or a stalled cursor, check the stalled repo's CI status and open MR state directly on the forge - a red required check or a merge conflict needs a human or a fresh implement pass to clear, and no lexical default exists for `mergeOrder`: if the order itself encodes the wrong dependency, the merge will never resolve regardless of CI. For `head-moving`, find who or what kept pushing to the branch - a human commit or a flapping CI autocommit - since the Task has already failed and needs a fresh review cycle once the branch is stable.

---

<a id="tatara-runbook-tatara-deploy-stage-wedged"></a><!-- alert: "Tatara deploy stage wedged" status: covered -->
## Deploy stage not reaching the cluster

**Symptoms:** `Tatara deploy stage wedged` (`alerts/tatara-cd.yaml`, critical) fires when a Task has spent over 3600s (half the 2h deploy budget) in `stage=deploying`, sustained 10m.

**What it means:** `deploying` is pod-less: it waits for every owned `MergeRequest` to reach `state=merged` and `deployedAt` to be stamped, which happens only once the component's release job has cut and published `vX.Y.Z`, `tatara-helmfile`'s pin-bump PR has landed, and the ARC-runner `helmfile apply` has completed against the cluster. This alert means the merged code is not reaching the cluster - the release job, the pin propagation into `tatara-helmfile`, or the apply itself has stalled. It parks at `parked(deploy-timeout)` at the full 2h budget.

**Diagnosis:**
```bash
kubectl -n tatara get task <task-name> -o jsonpath='{.status.stage}{" "}{.status.deployReentries}{"\n"}'
kubectl -n tatara get mergerequests -l tatara.io/task=<task-name> -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.state}{" "}{.status.deployedAt}{"\n"}{end}'
```
```promql
max by (task) (operator_task_stage_age_seconds{namespace="tatara",job="tatara-operator",stage="deploying"})
```

**Fix:** Trace the delivery chain for the merged repo(s) in order: confirm the release job cut and published `vX.Y.Z` to Harbor, confirm `tatara-helmfile` received the pin-bump PR (`cd-release` bot), and check whether `apply.yaml` ran and succeeded on `arc-runner-tatara-helmfile` - see [CI/CD and the deploy model](../architecture/ci-cd.md). A stuck ARC runner (a stale `AutoscalingListener`) or a chart pin pointing at a garbage-collected Harbor tag are both documented failure modes above on this page ("ARC runner jobs stuck in queue", "Helmfile apply fails 'chart not found'"). Never hand-fix the pin; if the chain is genuinely broken, open a `tatara-helmfile` PR to re-assert it once the root cause clears.

---

<a id="tatara-runbook-tatara-delivery-parked-on-a-merge-or-deploy-timeout"></a><!-- alert: "Tatara delivery parked on a merge or deploy timeout" status: covered -->
<a id="tatara-runbook-tatara-merge-or-deploy-cycle-exhausted"></a><!-- alert: "Tatara merge or deploy cycle exhausted" status: covered -->
## Delivery parked or permanently exhausted

**Symptoms:** `Tatara delivery parked on a merge or deploy timeout` (`alerts/tatara-cd.yaml`, critical) fires on any Task parking in 30m with `stageReason` matching `merge-timeout` or `deploy-timeout`. `Tatara merge or deploy cycle exhausted` (same file, critical) fires on any Task failing in 1h with `stageReason` matching `merge-blocked` or `deploy-blocked`.

**What it means:** A merge or deploy stage blew its budget (4h for `merging`, 2h for `deploying`) and the Task parked. Merged component code is NOT reaching the cluster. Re-entry from `merge-timeout`/`deploy-timeout` resumes the same stage, cursor-first - it never re-enters `implementing` - so the fix lives in the owned `MergeRequest` CRs and the `tatara-helmfile` apply run, not with the agent. The exhaustion alert is the bounded end of that same cycle: `mergeReentries`/`deployReentries` is capped at 3, and past that the Task fails permanently at `merge-blocked`/`deploy-blocked` - the delivery path to a cluster-admin-scoped runner has stopped outright, not just slowed.

**Diagnosis:**
```bash
kubectl -n tatara get task <task-name> -o jsonpath='{.status.stage}{" "}{.status.stageReason}{" "}{.status.mergeReentries}{" "}{.status.deployReentries}{"\n"}'
kubectl -n tatara get mergerequests -l tatara.io/task=<task-name>
```
```promql
sum by (stageReason) (increase(operator_task_parked_total{namespace="tatara",job="tatara-operator",stageReason=~"merge-timeout|deploy-timeout"}[30m]))
sum by (stageReason) (increase(operator_task_terminal_total{namespace="tatara",job="tatara-operator",stage="failed",stageReason=~"merge-blocked|deploy-blocked"}[1h]))
```

**Fix:** For a parked Task, work the underlying blockage directly - see the Merge stage not advancing runbook and the Deploy stage not reaching the cluster runbook above - the un-park is automatic (the reentry counter increments and the stage resumes) as long as the reentry budget is not yet spent. For an already-`failed(merge-blocked|deploy-blocked)` Task, the cycle is exhausted and there is no further automatic re-entry: fix the root blockage (forge, CI, or the ARC runner/`tatara-helmfile` chain) and re-approve the Task's work as a fresh delivery attempt.

---

<a id="tatara-runbook-operator-unexpected-merge-detected"></a><!-- alert: "Operator unexpected merge detected" status: covered -->
## Unexpected merge on a deploy-path repo

**Symptoms:** `Operator unexpected merge detected` (`alerts/tatara-operator.yaml`, critical) fires on any repo where an MR merged in 15m without the operator's `mergeCursor` advancing, sustained 5m.

**What it means:** Merging is an operator-only action: no agent merges anything, and auto-merge is never armed on a tatara-opened PR. <!-- stale-ok: auto-merge --> The platform has exactly one bot identity, so the forge cannot tell an operator-driven merge apart from a merge performed by anything else holding the same token. This alert is the detection control for that residual risk: something other than the operator's own sequential `mergeOrder` walk put code on a cluster-admin-scoped deploy path.

**Diagnosis:**
```bash
kubectl -n tatara get task -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.mergeOrder}{" "}{.status.mergeCursor}{"\n"}{end}' | grep <repo>
kubectl -n tatara logs deploy/tatara-operator | grep -i "unexpected_merge\|unexpected merge" | tail -50
```
```promql
sum by (repo) (increase(operator_unexpected_merge_total{namespace="tatara",job="tatara-operator"}[15m]))
```

**Fix:** Investigate who or what merged the MR immediately - check the forge's own merge event/actor for the repo named in the alert, and confirm what code shipped as a result. There is no automatic remediation here by design: this is a security detection control, not a self-healing condition. Treat any confirmed non-operator merge on a deploy-path repo as a credential or access-control incident given the cluster-admin-scoped runner downstream of it, and escalate accordingly.

---

<a id="tatara-runbook-operator-object-too-large-to-write"></a><!-- alert: "Operator object too large to write" status: covered -->
<a id="tatara-runbook-operator-status-writes-blocked-by-an-unspillable-eviction-batch"></a><!-- alert: "Operator status writes blocked by an unspillable eviction batch" status: covered -->
<a id="tatara-runbook-operator-objbudget-spiller-unconfigured"></a><!-- alert: "Operator objbudget spiller unconfigured" status: covered -->
<a id="tatara-runbook-operator-task-context-served-with-missing-notes"></a><!-- alert: "Operator task context served with missing notes" status: covered -->
## Object byte-budget guard tripped

**Symptoms:** Four related alerts in `alerts/tatara-operator.yaml`. `Operator object too large to write` (critical) fires when an object cannot fit under the 800,000-byte budget even after evicting everything evictable. `Operator status writes blocked by an unspillable eviction batch` (critical, `reason=spill_error`) fires when an over-budget eviction batch cannot be spilled to `tatara-memory` for 15m. `Operator objbudget spiller unconfigured` (critical, `reason=unconfigured`) fires when a status write is refused because no spiller is wired up at all. `Operator task context served with missing notes` (warning) fires when `task_context(notes=all)` cannot rehydrate a spilled note batch and serves a partial history with a 2xx.

**What it means:** `Task.status.notes` beyond its 50-item cap, and evicted `Issue`/`MergeRequest` comments past their marshaled-size budget, spill one-way into `tatara-memory` rather than being dropped - see [Memory Architecture](../architecture/memory-architecture.md). "Object too large" is the terminal case: that object is now permanently unwritable and any Issue/MergeRequest it owns is pinned open forever. "Blocked by an unspillable batch" is the guard refusing rather than dropping - nothing is lost, but every writer for that object fails until `tatara-memory` returns; this is the memory outage's real blast radius now that agents are no longer gated on it. "Spiller unconfigured" looks identical operationally but is a wiring bug, not an outage: it will never clear on its own and no memory recovery fixes it. "Missing notes" is the read-side symptom: an agent asking for full note history gets a silently truncated one, with no indication in the response.

**Diagnosis:**
```bash
kubectl -n tatara logs deploy/tatara-operator | grep -i "object_too_large\|objbudget_spill\|notes_rehydrate" | tail -50
kubectl -n tatara get pods -l app.kubernetes.io/instance=mem-<project>
kubectl -n tatara get deploy mem-<project> -o jsonpath='{.status.readyReplicas}{"\n"}'
```
```promql
sum by (kind, name) (increase(operator_object_too_large_total{namespace="tatara",job="tatara-operator"}[15m]))
sum by (kind) (increase(operator_objbudget_spill_blocked_total{namespace="tatara",job="tatara-operator",reason="spill_error"}[30m]))
sum by (kind) (increase(operator_objbudget_spill_blocked_total{namespace="tatara",job="tatara-operator",reason="unconfigured"}[30m]))
```

**Fix:** For "object too large", manual intervention is required - there is no automatic recovery once eviction has already removed everything evictable and the object still does not fit. For "unspillable eviction batch" and "missing notes", check the affected project's memory stack first - see the Memory stack unavailable runbook above - and expect both to clear once `tatara-memory` is healthy again. For "spiller unconfigured", do not wait: this is a deploy-time wiring gap in the operator's byte-budget guard, and it needs a config or code fix, not a memory-stack recovery.

---

<a id="tatara-runbook-operator-artifact-left-with-no-controller-owner"></a><!-- alert: "Operator artifact left with no controller owner" status: covered -->
<a id="tatara-runbook-operator-gc-blocked"></a><!-- alert: "Operator GC blocked" status: covered -->
## Ownership or GC invariant broken

**Symptoms:** `Operator artifact left with no controller owner` (critical, `alerts/tatara-operator.yaml`) fires when an Issue or MergeRequest is found with plain owners but no controller owner. `Operator GC blocked` (warning, same file) fires when the operator cannot garbage-collect one or more aged-out objects.

**What it means:** The operator's ownership-repair guard caught an Issue/MergeRequest whose controller-owner reference went missing - the fold or reap controller-owner transfer path has a bug - and repaired it by promoting the oldest surviving plain owner. The repair keeps the object usable, but an invariant that should never break, broke. GC blocked fires when the reaper cannot delete an object: `reason` is `no_controller_owner` (the same invariant break), `fold_in_flight` (a refine umbrella adoption never completed), or `doc_reference` (a delivered Task is still referenced by a pending doc batch). A `fold_in_flight` reason that never clears means a refine umbrella died mid-adoption.

**Diagnosis:**
```bash
kubectl -n tatara logs deploy/tatara-operator | grep -i orphan | tail -50
kubectl -n tatara get issues,mergerequests -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.metadata.ownerReferences}{"\n"}{end}'
```
```promql
sum by (reason) (increase(operator_gc_blocked_total{namespace="tatara",job="tatara-operator"}[1h])) or vector(0)
```

**Fix:** No confirmed fix beyond the guard's own repair - this rule exists to surface the underlying bug in the fold/reap controller-owner transfer path, which has not yet been root-caused. Capture the offending object's name and owner history from the logs above and escalate; do not assume the repaired object is otherwise healthy. For a persistent `fold_in_flight` GC block, check whether the associated refine Task ever reached `delivered` - see the [stage reference](../reference/task-stages.md) for the refine/fold lifecycle.

---

<a id="tatara-runbook-operator-sweep-heartbeat-stale"></a><!-- alert: "Operator sweep heartbeat stale" status: covered -->
<a id="tatara-runbook-operator-sweep-erroring"></a><!-- alert: "Operator sweep erroring" status: covered -->
## Sweep heartbeat stale or erroring

**Symptoms:** `Operator sweep heartbeat stale` (critical, `alerts/tatara-operator.yaml`) fires when a project/activity's sweep is more than 3h past its own computed next-expected run, or when the metric is absent entirely (NoData is deliberately configured to fire on this rule too). `Operator sweep erroring` (warning, same file) fires when a sweep pass errors repeatedly within 1h, even while the heartbeat itself stays green.

**What it means:** The operator computes each activity's next-expected run from its own cron schedule and its own last recorded success, so a heartbeat breach is a genuinely missed run, not a cadence mismatch against the alert. An absent series means the operator published no next-expected timestamp for any activity at all - consistent with the whole sweep loop, or the operator process itself, being down. The erroring rule catches a different failure: some passes succeed (keeping the heartbeat green) while others for the same project/activity throw, with `reason` naming why.

**Diagnosis:**
```bash
kubectl -n tatara get pods -l app.kubernetes.io/name=tatara-operator
kubectl -n tatara get lease -n tatara
kubectl -n tatara logs deploy/tatara-operator | grep -i sweep | tail -50
```
```promql
time() - operator_sweep_next_expected_timestamp_seconds{namespace="tatara",job="tatara-operator"}
```

**Fix:** The operator is leader-elected across 3 replicas; only the leader runs the sweep loop, so confirm leadership is actually held by a healthy pod - a flapping or crash-looping leader stalls every project's sweeps at once, matching the NoData case. If leadership is stable, check the named `project`/`activity` in the logs above for a stuck reconcile. For sweep erroring, the `reason` label narrows the failing pass; one project/activity erroring while others succeed points at that project's own Repository/Issue state rather than the operator process.

---

<a id="tatara-runbook-operator-sweep-creation-budget-chronically-capped"></a><!-- alert: "Operator sweep creation budget chronically capped" status: covered -->
<a id="tatara-runbook-operator-task-mint-burst"></a><!-- alert: "Operator task mint burst" status: covered -->
## Sweep budget capped or task mint burst

**Symptoms:** `Operator sweep creation budget chronically capped` (warning, `alerts/tatara-operator.yaml`) fires when a project's sweep hits its `maxOpenTasks`/`maxNewTasksPerSweep` cap more than 6 times in 3h. `Operator task mint burst` (warning, same file) fires when the operator mints more than 20 Tasks in 1h.

**What it means:** The budget-capped alert means orphan Issues are being discovered faster than the creation budget lets them be minted into active Tasks - expected for a few hours right after cutover (a 150-issue backlog takes about 30 sweep passes at the default `maxNewTasksPerSweep` of 5), but sustained past a day it means the cap is genuinely starving the sweep. The mint-burst alert is a higher-level symptom: since one pass is capped at `maxNewTasksPerSweep`, more than 20 mints in an hour is either a mint/reap loop (an Issue's ownership is not sticking, so it gets re-minted every pass) or a real backlog drain.

**Diagnosis:**
```bash
kubectl -n tatara get project <project> -o jsonpath='{.spec.maxOpenTasks}{" "}{.spec.maxNewTasksPerSweep}{"\n"}'
kubectl -n tatara get tasks -o jsonpath='{range .items[*]}{.status.stage}{"\n"}{end}' | sort | uniq -c
```
```promql
sum by (project, cap) (increase(operator_sweep_mint_cap_hit_total{namespace="tatara",job="tatara-operator"}[3h])) or vector(0)
```

**Fix:** For a chronically capped budget, raise `Project.spec.maxOpenTasks` (see [Tuning](tuning.md)) or wait for active Tasks to clear a stage. For a mint burst, check `operator_task_stage{stage="parked"}`: if the minted Tasks are landing in `parked(backlog-sweep)` they spawn zero pods and the burst is a healthy backlog drain; if they land in a pod-spawning stage and keep re-appearing, ownership is not sticking on reap - correlate with the Ownership or GC invariant broken runbook above.

---

<a id="tatara-runbook-operator-documentation-batch-abandoned"></a><!-- alert: "Operator documentation batch abandoned" status: covered -->
## Documentation batch abandoned

**Symptoms:** `Operator documentation batch abandoned` (warning, `alerts/tatara-operator.yaml`) fires when the nightly documentation batch cron did not run in the last 25h, for at least one abandoned Task (`reason="never_ran"`).

**What it means:** Documentation is a nightly batch, not a per-delivery spawn: each night the operator mints one `documenting` Task per project covering every Task that reached `delivered` in the last 24h with at least one merged MR ref. If the cron itself did not fire, every Task that became eligible for that missed night is now permanently undocumented - `documentedBy` (see [Task reference](../reference/task.md)) never retries a Task a missed batch skipped; the next batch only picks up newly eligible Tasks.

**Diagnosis:**
```bash
kubectl -n tatara logs deploy/tatara-operator | grep -i documentation | tail -50
kubectl -n tatara get tasks -o jsonpath='{range .items[?(@.status.stage=="delivered")]}{.metadata.name}{" "}{.status.documentedBy}{"\n"}{end}'
```
```promql
sum by (reason) (increase(operator_doc_task_abandoned_total{namespace="tatara",job="tatara-operator",reason="never_ran"}[25h])) or vector(0)
```

**Fix:** Nothing recovers the missed cohort - per the alert's own summary, `documentedBy` never retries Tasks a missed batch skipped, so treat that coverage gap as permanent. The actionable fix is forward-looking: confirm the operator process is healthy and the nightly cron trigger is scheduled to fire again (see the Sweep heartbeat stale or erroring runbook above for the same cron-liveness pattern), so no further nights are lost.

---

<a id="tatara-runbook-memory-pvc-volume-near-full"></a><!-- alert: "Memory PVC volume near full" status: covered -->
## Memory PVC near full

**Symptoms:** `Memory PVC volume near full` (warning, `alerts/tatara-memory.yaml`) fires when a `mem-*` PVC's free-space ratio drops below 15% for 15m.

**What it means:** A CNPG Postgres or Neo4j volume for a project's memory stack is running low. Postgres WAL/data fill leads to a CNPG crash loop once the volume is exhausted, taking that project's memory quorum down with it - this is a leading indicator, not yet an outage.

**Diagnosis:**
```bash
kubectl -n tatara get pvc -l app.kubernetes.io/name=tatara-memory
kubectl -n tatara describe pvc <persistentvolumeclaim-from-alert-label>
```
```promql
min(
  kubelet_volume_stats_available_bytes{namespace="tatara",persistentvolumeclaim=~"mem-.*"}
  /
  kubelet_volume_stats_capacity_bytes{namespace="tatara",persistentvolumeclaim=~"mem-.*"}
) by (persistentvolumeclaim)
```

**Fix:** Expand storage before it hits 0: identify the affected component from the alert's `persistentvolumeclaim` label, then raise that project's `spec.memory.pgStorage`, `pgWalStorage`, or `neo4jStorage` (see [Memory Architecture](../architecture/memory-architecture.md)). Storage is monotonic - CNPG's admission webhook rejects shrinking it back down - so size the increase generously rather than incrementally.

---

<a id="tatara-runbook-memory-lightrag-call-error-ratio-high"></a><!-- alert: "Memory LightRAG call error ratio high" status: covered -->
<a id="tatara-runbook-memory-lightrag-call-p95-latency-high"></a><!-- alert: "Memory LightRAG call p95 latency high" status: covered -->
## LightRAG backend failing or slow

**Symptoms:** `Memory LightRAG call error ratio high` (warning, `alerts/tatara-memory.yaml`) fires when more than 10% of `tatara-memory`'s calls into LightRAG error over 10m. `Memory LightRAG call p95 latency high` (warning, same file) fires when the p95 call latency exceeds 30s over 10m, evaluated only while calls are actually happening.

**What it means:** `tatara-memory` proxies every ingest and query operation through LightRAG (`deploy/mem-<project>-lightrag`), which itself depends on Neo4j for the graph and Postgres for KV/vectors. A high error ratio or p95 latency here means the LightRAG backend is failing or slow to respond - agent `query`/`code_*` lookups and ingest jobs pushing chunks both degrade together, since they share this one call path.

**Diagnosis:**
```bash
kubectl -n tatara get pods -l app.kubernetes.io/name=tatara-memory,app.kubernetes.io/component=lightrag
kubectl -n tatara logs deploy/mem-<project>-lightrag --tail=100
```
```promql
sum(rate(lightrag_calls_total{namespace="tatara",result="error"}[10m]))
/
clamp_min(sum(rate(lightrag_calls_total{namespace="tatara"}[10m])), 0.001)
```

**Fix:** Check the LightRAG pod first for an OOM or crash-loop - LightRAG is its own Deployment, not a sidecar container, so it can restart independently of the rest of the stack. If the pod itself is healthy, the slowness or errors are likely downstream: check that same project's Neo4j and Postgres, since LightRAG cannot serve without both. See the Memory postgres/neo4j replica stuck and Neo4j EIO errors runbooks elsewhere on this page if a backing store looks unhealthy.

---

<a id="tatara-runbook-memory-service-operation-error-ratio-high"></a><!-- alert: "Memory service operation error ratio high" status: covered -->
## Memory service operation errors

**Symptoms:** `Memory service operation error ratio high` (warning, `alerts/tatara-memory.yaml`) fires when more than 10% of `tatara-memory` service operations error over 10m (writes plus non-lookup reads; `get`/`get_entity` not-found results are excluded so a normal cache-miss lookup does not trip it).

**What it means:** Agents are failing to read from or write to memory through `tatara-memory`'s own REST surface - a broader signal than the LightRAG-specific rules above, since it covers the service's own request handling as well as everything it calls into.

**Diagnosis:**
```bash
kubectl -n tatara get pods -l app.kubernetes.io/name=tatara-memory,app.kubernetes.io/instance=mem-<project>
kubectl -n tatara logs deploy/mem-<project> --tail=100
```
```promql
sum(rate(tatara_memory_op_total{namespace="tatara",result="error",op!~"get|get_entity"}[10m]))
/
clamp_min(sum(rate(tatara_memory_op_total{namespace="tatara",op!~"get|get_entity"}[10m])), 0.001)
```

**Fix:** Check `deploy/mem-<project>` logs for the failing `op` and its error. Because `tatara-memory` fronts LightRAG, Neo4j, and Postgres (see [Memory Architecture](../architecture/memory-architecture.md)), an elevated ratio here with a healthy `mem-<project>` process usually traces back to one of those three - cross-check the LightRAG backend and CNPG/Neo4j runbooks on this page before assuming a bug in `tatara-memory` itself.

---

<a id="tatara-runbook-operator-memory-retrieval-surface-absent"></a><!-- alert: "Operator memory retrieval surface absent" status: covered -->
<a id="tatara-runbook-operator-tool-surface-probe-failing"></a><!-- alert: "Operator tool-surface probe failing" status: covered -->
## Memory retrieval or tool surface absent

**Symptoms:** `Operator memory retrieval surface absent` (warning, `alerts/tatara-operator.yaml`) fires when the operator's own probe finds a `tatara-memory` retrieval route returning 404 for 15m. `Operator tool-surface probe failing` (warning, same file) fires when the operator's probe finds a named tool backend absent, erroring, or unreachable for 15m.

**What it means:** The operator actively probes the tool and memory retrieval surfaces agents depend on. A 404 on a previously-present route means the deployed `tatara-memory` binary has drifted or regressed - route `{route}` is simply gone. The tool-surface rule is the same idea for whichever `{backend}` the probe checks: agents may have lost access to that tool entirely, not just seen it degrade.

**Diagnosis:**
```bash
kubectl -n tatara logs deploy/tatara-operator | grep -i memory_retrieval_probe | tail -50
kubectl -n tatara logs deploy/tatara-operator | grep -i tool_surface_probe | tail -50
```
```promql
sum by (route) (increase(operator_memory_retrieval_probe_total{namespace="tatara",job="tatara-operator",result="absent"}[30m]))
sum by (backend) (increase(operator_tool_surface_probe_total{namespace="tatara",job="tatara-operator",result=~"absent|error|unreachable"}[30m]))
```

**Fix:** Take the `route` or `backend` label from the logs and confirm whether the deployed image for that surface (`mem-<project>` for a memory route, or the named tool backend) still exposes it - a route that vanished after a rollout points at a version mismatch between what the operator expects and what the current image ships. Check the relevant chart pins in `tatara-helmfile` advanced together; a partial or out-of-order rollout is the most common way a surface both sides previously agreed on disappears.

---

<a id="tatara-runbook-tatara-ingest-job-failing"></a><!-- alert: "Tatara ingest job failing" status: covered -->
<a id="tatara-runbook-tatara-ingest-run-failure-ratio-high"></a><!-- alert: "Tatara ingest run failure ratio high" status: covered -->
<a id="tatara-runbook-repository-stuck-in-failing-ingest-state"></a><!-- alert: "Repository stuck in failing ingest state" status: covered -->
<a id="tatara-runbook-memory-ingest-job-failure-ratio-high"></a><!-- alert: "Memory ingest job failure ratio high" status: covered -->
## Repo ingest failing

**Symptoms:** `Tatara ingest job failing` (warning, `alerts/tatara-ingester.yaml`) fires on any full-ingest failure in 1h (benign self-healing incremental failures are excluded). `Tatara ingest run failure ratio high` (warning, same file) fires when over 50% of all ingest runs fail in 1h. `Repository stuck in failing ingest state` (warning, same file) fires when a Repository has stayed pinned in a failing state for over 1h. `Memory ingest job failure ratio high` (warning, `alerts/tatara-memory.yaml`) fires when over 20% of `tatara-memory`'s own ingest jobs fail over 30m.

**What it means:** The ingester is fail-closed on a hard analyzer error: if an analyzer fails for its whole file batch, the run aborts before pushing anything and exits non-zero, so the operator retries the same commit window. An incremental Job has `backoffLimit: 0` (fail fast, escalate to full); a full Job retries twice before giving up. The full-ingest-failing alert means even that escalation path is failing, so the recall corpus for the affected repo is going stale. `Repository stuck in failing ingest state` means the persistent failure survived the incremental-to-full self-heal and has not cleared on re-ingest - an analyzer, auth, or graph-push problem that keeps recurring. `Memory ingest job failure ratio high` is the same failure viewed from `tatara-memory`'s own job-tracking, once a run does reach the push stage.

**Diagnosis:**
```bash
kubectl -n tatara get jobs -l tatara.dev/repository=<repo>
kubectl -n tatara logs job/<ingest-job-name> --tail=100
kubectl -n tatara describe repository <repo> | grep -iE 'phase|ingest'
```
```promql
sum(increase(operator_ingest_job_total{namespace="tatara",result="failure",mode="full"}[1h]))
max by (project, repo) (operator_repository_ingest_failing{namespace="tatara"})
```

**Fix:** Read the ingest Job's own logs for the analyzer name, file path, and error the run aborted on. A recurring clone/auth failure points at the project's `scmSecretRef` token; a recurring graph-push failure points at `tatara-memory`/LightRAG availability (see the LightRAG and memory service runbooks above). Because the self-heal already escalated incremental to full and it still failed, this needs a human to fix the underlying cause before the Repository will clear on its own.

---

<a id="tatara-runbook-tatara-ingest-job-stuck-active"></a><!-- alert: "Tatara ingest job stuck active" status: covered -->
<a id="tatara-runbook-repository-ingest-stale"></a><!-- alert: "Repository ingest stale" status: covered -->
<a id="tatara-runbook-repository-re-ingest-gated-on-memory-readiness"></a><!-- alert: "Repository re-ingest gated on memory readiness" status: covered -->
## Repo ingest wedged or stale

**Symptoms:** `Tatara ingest job stuck active` (warning, `alerts/tatara-ingester.yaml`) fires when an ingest Job has been active for over 30m. `Repository ingest stale` (warning, same file) fires when a Repository's last successful ingest was over 24h ago. `Repository re-ingest gated on memory readiness` (warning, same file) fires when a Repository's re-ingest has been held by the project memory-readiness gate for 1h.

**What it means:** A Job active past 30m is wedged - analyzer hang, a LightRAG lock, or a slow clone - not merely slow. Repository staleness is a backstop: normal live staleness is roughly 2.5h (the `reingestSchedule` cron plus webhook-driven incrementals), so 24h means the per-repo cron missed or is silently wedged. The memory-readiness gate is different again: nothing failed and nothing retried, so `operator_repository_ingest_failing` reads 0 throughout - the gate normally clears within a 3m stabilization window once the stack is Ready, so an hour of gating means that project's memory stack is not converging. If both this alert and Repository ingest stale are firing for the same project, the gate is the cause and the staleness is the consequence - fix the memory stack, not the cron.

**Diagnosis:**
```bash
kubectl -n tatara get jobs -l tatara.dev/repository=<repo> -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.active}{" "}{.status.startTime}{"\n"}{end}'
kubectl -n tatara describe repository <repo> | grep -iE 'phase|last ingest'
```
```promql
max(kube_job_status_active{namespace="tatara",job_name=~".*-ingest-.*"} * on(job_name) group_left() (time() - kube_job_status_start_time{namespace="tatara",job_name=~".*-ingest-.*"}))
max by (project, repo) (operator_repository_ingest_gated{namespace="tatara"})
```

**Fix:** For a stuck Job, `kubectl delete job` the wedged run and let the operator recreate it on the next trigger - the analyzer hang or clone stall will not self-clear. For plain staleness with no gating firing, check the operator logs for the repo's cron trigger and confirm `reingestSchedule` is still set on the Repository CR. For the memory-readiness gate, check `operator_memory_stacks{project=...}` and the project's memory stack health directly (see the memory-stack runbooks on this page); agents keep running meanwhile, but against a corpus that is both unreachable and going stale.

---

<a id="tatara-runbook-ingester-llm-call-failure-ratio-high"></a><!-- alert: "Ingester LLM call failure ratio high" status: covered -->
<a id="tatara-runbook-tatara-ingester-quarantining-files-analyzer-hard-error"></a><!-- alert: "Tatara ingester quarantining files (analyzer hard-error)" status: covered -->
<a id="tatara-runbook-memory-ingest-item-error-rate-elevated"></a><!-- alert: "Memory ingest item error rate elevated" status: covered -->
<a id="tatara-runbook-memory-code-graph-analytics-stalled-with-dirty-repos"></a><!-- alert: "Memory code-graph analytics stalled with dirty repos" status: covered -->
## Code graph quality degrading (partial graph)

**Symptoms:** `Ingester LLM call failure ratio high` (warning, `alerts/tatara-ingester.yaml`) fires when the LLM call failure ratio exceeds 30% over 1h (gated to at least 20 calls). `Tatara ingester quarantining files (analyzer hard-error)` (warning, same file) fires on any quarantined file in 1h. `Memory ingest item error rate elevated` (info, `alerts/tatara-memory.yaml`) fires on any nonzero error/timeout rate on ingest items over 30m. `Memory code-graph analytics stalled with dirty repos` (warning, same file) fires when dirty repos exist with no analytics recompute in 30m.

**What it means:** These four all shrink or stale the code graph without failing the ingest run itself, so they are easy to miss. The optional Phase 2 LLM semantic-extraction stage enriches the graph with concept and rationale nodes beyond plain AST analysis; a high failure ratio there means that enrichment is degrading best-effort while the run still reports success. A quarantined file is held at its last-good graph state and dropped from further updates until its next diff edit - the affected repo/language has an incomplete graph until then; check the ingest job's WARN logs for the analyzer name, paths, and error. Elevated memory ingest item errors mean individual chunks/entities are being dropped inside `tatara-memory` even when the overall job succeeds. Stalled analytics means centrality/community data (used by graph-aware queries) is going stale because dirty repos are piling up with no recompute.

**Diagnosis:**
```bash
kubectl -n tatara logs job/<ingest-job-name> | grep -i quarantin
kubectl -n tatara logs deploy/mem-<project> --tail=100
```
```promql
(sum(increase(llm_calls_total{result="fail"}[1h])) / clamp_min(sum(increase(llm_calls_total[1h])), 1)) * (sum(increase(llm_calls_total[1h])) >= bool 20)
sum(increase(ingest_files_quarantined_total[1h]))
max(code_graph_analytics_dirty_repos{namespace="tatara"}) * on() group_left() (sum(rate(code_graph_analytics_runs_total{namespace="tatara"}[30m])) == bool 0)
```

**Fix:** For LLM failures, check the OpenAI Secret and `SEMANTIC_MODEL` config on the affected project - semantic extraction runs AST-only and does not fail the job if the Secret is absent, so a failure ratio here means credentials or the API itself, not a missing config. For quarantined files, fix the underlying parse error named in the WARN log; the file recovers automatically on its next diff edit. For elevated memory ingest item errors, check `deploy/mem-<project>` logs for the specific item type failing. For stalled analytics, no confirmed fix is named beyond confirming the analytics recompute loop in `tatara-memory` is still running; if dirty repos keep accumulating with zero runs, escalate as a stuck background job in that service.

---

<a id="tatara-runbook-repository-phase-desync-still-being-produced"></a><!-- alert: "Repository phase desync still being produced" status: covered -->
<a id="tatara-runbook-ingest-job-creation-race-still-being-hit"></a><!-- alert: "Ingest job creation race still being hit" status: covered -->
## Repository reconciler self-repair still firing

**Symptoms:** `Repository phase desync still being produced` (info, `alerts/tatara-ingester.yaml`) fires when the Repository reconciler repairs a desynchronised (`Phase=Failed`, `IngestFailureCount=0`) state on any repo in 1h. `Ingest job creation race still being hit` (info, same file) fires when an ingest Job creation collapses into adopting an existing Job of the same deterministic name in 1h. Both are info-only (no `system=tatara` label): they email but never raise an incident Task.

**What it means:** Both rules track a self-repair that is working, so nothing is actually stuck - but each is watching for a bug that was never fully root-caused. The phase-desync repair fixes a Repository showing `Failed` with zero recorded ingest failures; one repo sat in that state for 20.8h with no real ingest failures before the repair existed, and what originally wrote the desynchronised state was never proven. A nonzero rate means whatever writes it is still running. The Job-creation race is the read-then-create path for the deterministic ingest Job name: two Jobs were once created 16ms apart from the same pod, and the deterministic name is the only thing collapsing that race into a safe adoption instead of a duplicate run.

**Diagnosis:**
```bash
kubectl -n tatara logs deploy/tatara-operator | grep -i phase_repaired | tail -50
kubectl -n tatara logs deploy/tatara-operator | grep -i job_deduplicated | tail -50
```
```promql
sum by (project, repo) (
  clamp_min(
    operator_repository_phase_repaired_total{namespace="tatara",job="tatara-operator"}
    - (operator_repository_phase_repaired_total{namespace="tatara",job="tatara-operator"} offset 1h
       or 0 * operator_repository_phase_repaired_total{namespace="tatara",job="tatara-operator"}),
    0
  )
)
```

**Fix:** No confirmed fix - both rules exist to make a still-open bug visible, not to point at a resolution: the repair (or the deduplication) is working as designed, so there is nothing broken to page on. A sustained nonzero rate on either is a signal that the underlying reconcile path deserves a real guard rather than the current self-heal, not evidence that ingest itself is failing. Track the rate over time and escalate for a code fix if it does not trend to zero.

---

<a id="tatara-runbook-wrapper-turns-erroring"></a><!-- alert: "Wrapper turns erroring" status: covered -->
<a id="tatara-runbook-wrapper-commit-push-failure-ratio-high"></a><!-- alert: "Wrapper commit/push failure ratio high" status: covered -->
## Wrapper turns or commits failing

**Symptoms:** `Wrapper turns erroring` (`alerts/tatara-wrapper.yaml`, warning) fires when more than 30% of turns fail over 30m. `Wrapper commit/push failure ratio high` (`alerts/tatara-wrapper.yaml`, warning) fires when more than 20% of commit/push attempts fail over 30m. Both key on the `ccw_*` counters the wrapper pushes from inside the agent pod.

**What it means:** Claude sessions inside agent pods are erroring out on a large share of turns, or the agent's work is failing to land on the source repos it edited. Either can stall a Task's progress without failing it outright.

**Diagnosis:**
```bash
kubectl -n tatara get pods -l tatara.io/task=<task-name>
kubectl -n tatara logs <pod-name> -c wrapper --tail=100
```
```promql
sum(rate(ccw_turns_total{result="failed"}[30m])) / clamp_min(sum(rate(ccw_turns_total[30m])), 0.0001)
sum(rate(ccw_commit_push_total{result="fail"}[30m])) / clamp_min(sum(rate(ccw_commit_push_total[30m])), 0.0001)
```
If both ratios look suspiciously flat instead of clearly bad or clearly fine, check the Wrapper metric pipeline dark runbook below first - a rate() over an empty series can look identical to a healthy one.

**Fix:** For turn failures, read the wrapper log around the failed turn for the underlying error (auth, MCP timeout, malformed tool call); if the pattern is the claude process itself restarting rather than a turn erroring cleanly, see the Claude process crash-looping runbook below instead. For commit/push failures, check that the Task's target [`Repository`](../reference/repository.md) still exists and that the SCM credential backing it has not expired or lost write access. Escalate to tatara-claude-code-wrapper if neither explains it.

---

<a id="tatara-runbook-wrapper-claude-process-crash-looping"></a><!-- alert: "Wrapper Claude process crash-looping" status: covered -->
## Claude process crash-looping inside a wrapper pod

**Symptoms:** `Wrapper Claude process crash-looping` (`alerts/tatara-wrapper.yaml`, warning) fires when the claude subprocess inside a single wrapper pod restarts more than 3 times in 15m, keyed by `exported_pod`.

**What it means:** The interactive claude process inside that agent pod is being relaunched repeatedly instead of running one stable session. The wrapper's `conversationRestart` lifecycle hook fires on each relaunch, but repeated relaunches burn turn budget and stall the Task even though the pod itself stays Ready.

**Diagnosis:**
```bash
kubectl -n tatara get pod <pod-name> -o jsonpath='{.status.containerStatuses[?(@.name=="wrapper")].restartCount}{"\n"}'
kubectl -n tatara describe pod <pod-name>
kubectl -n tatara logs <pod-name> -c wrapper --previous
```
```promql
sum by (exported_pod) (increase(ccw_claude_restarts_total[15m]))
```

**Fix:** Check `describe pod` for `OOMKilled` first - a relaunch loop with no boot-time explanation in the wrapper log is consistent with the container being killed for memory rather than claude itself faulting. If the previous-container log instead shows a claude-side crash, escalate to tatara-claude-code-wrapper; no confirmed root-cause fix beyond resource sizing has landed against a resolved incident yet.

---

<a id="tatara-runbook-operator-push-receiver-rejecting-wrapper-metrics"></a><!-- alert: "Operator push receiver rejecting wrapper metrics" status: covered -->
<a id="tatara-runbook-wrapper-metrics-blind-while-agents-running"></a><!-- alert: "Wrapper metrics blind while agents running" status: covered -->
## Wrapper metric pipeline dark

**Symptoms:** `Operator push receiver rejecting wrapper metrics` (`alerts/tatara-operator.yaml`, warning) fires when the operator's push receiver rejects more than 0 wrapper metric pushes (`result=~"rejected|too_large"`) over 15m. `Wrapper metrics blind while agents running` (`alerts/tatara-operator.yaml`, warning) fires when at least one `wrapper` container is running but the operator fleet has pushed zero metric runs for 15m.

**What it means:** Wrapper pods carry no scrape target - every `ccw_*` series (turns, commit/push, restarts, cost) exists only because the wrapper pushes it to the operator. A rejection means pushes are arriving but being dropped; a zero-push reading while pods are running means the pipeline is dark end to end. Either condition silently blinds every other `ccw_*`-based alert - treat the absence of those alerts as meaningless while this one is firing.

**Diagnosis:**
```bash
kubectl -n tatara logs deploy/tatara-operator | grep -i push_receive | tail -50
kubectl -n tatara get pods -l tatara.io/task -o wide
```
```promql
sum(increase(operator_push_receive_total{namespace="tatara",job="tatara-operator",result=~"rejected|too_large"}[15m]))
sum(operator_pushed_runs{namespace="tatara",job="tatara-operator"})
```

**Fix:** For rejections, read the operator log for the specific rejection reason; a `too_large` result points at the push payload outgrowing whatever size limit the receiver enforces, not necessarily a wrapper bug. For a fully dark pipeline, confirm the 3 leader-elected operator replicas are healthy and that wrapper pods can reach the operator's push endpoint from inside the cluster. Escalate to tatara-operator if the receiver is rejecting pushes that look well-formed.

---

<a id="tatara-runbook-agent-token-spend-runaway-s"></a><!-- alert: "Agent token spend runaway ($/s)" status: covered -->
<a id="tatara-runbook-agent-token-spend-runaway-series-missing-coverage-gap"></a><!-- alert: "Agent token spend runaway series missing (coverage gap)" status: covered -->
<a id="tatara-runbook-project-token-budget-riding-emergency-ceiling"></a><!-- alert: "Project token budget riding emergency ceiling" status: covered -->
## Agent token spend runaway

**Symptoms:** `Agent token spend runaway ($/s)` (`alerts/tatara-operator.yaml`, warning) fires when fleet-wide, model-aware spend exceeds $0.50/s over 15m. `Agent token spend runaway series missing (coverage gap)` (`alerts/tatara-operator.yaml`, warning) fires when `operator_task_tokens_total` is being emitted but no series carry the `model`/`type` labels the $/s rule depends on, for 15m. `Project token budget riding emergency ceiling` (`alerts/tatara-operator.yaml`, warning) fires per-project when `operator_token_budget_used_ratio{scope="used"}` reaches that same project's own `scope="emergency"` threshold, for 15m.

**What it means:** The first is a real fleet-wide cost runaway - a turn or loop burning budget. The second is not a runaway signal at all: it means the operator's model/cache token labelling has regressed, so the $/s rule has no data source and cannot detect a runaway even if one exists; it only fires while token activity is present, so it stays silent on an idle fleet. The third is one project's own `tokenBudget` emergency ceiling - further spawns for that project are already being paused - and is distinct from both the fleet $/s rule above and the account-wide usage-gate alerts below.

**Diagnosis:**
```bash
kubectl -n tatara get tasks -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.stage}{"\n"}{end}'
kubectl -n tatara logs deploy/tatara-operator | grep -i token | tail -50
```
```promql
sum(rate(operator_task_tokens_total{type="output"}[15m])) by (model)
absent(operator_task_tokens_total{model=~".+", type=~"input|output|cache_read|cache_creation"})
max by (project) (operator_token_budget_used_ratio{namespace="tatara",job="tatara-operator"})
```

**Fix:** For the $/s runaway, check the task-delivery Cost panel to find the offending Task and park or cancel it; see [Tuning](tuning.md) for `maxConcurrentAgents` and per-kind model/effort tiering if the spend is systemic rather than one Task. For the coverage-gap rule, this is an operator bug, not a spend problem: check the `AddTaskTokens` call sites in tatara-operator for a missing `model`/`cacheRead`/`cacheCreation` argument and escalate there - do not use this rule as a proxy for actual spend. For the per-project ceiling, the pause is already in effect by design; raise the project's `tokenBudget.emergencyPercent` or underlying limit via [Tuning](tuning.md) if it is unwanted, or leave it in place if it is protecting against a genuine runaway.

---

<a id="tatara-runbook-claude-account-usage-poll-unhealthy"></a><!-- alert: "Claude account usage poll unhealthy" status: covered -->
<a id="tatara-runbook-claude-account-usage-window-near-emergency-ceiling"></a><!-- alert: "Claude account usage window near emergency ceiling" status: covered -->
<a id="tatara-runbook-claude-account-monthly-overage-climbing"></a><!-- alert: "Claude account monthly overage climbing" status: covered -->
<a id="tatara-runbook-claude-code-api-429-rate-reactive-backstop-pending-otel-deployment"></a><!-- alert: "Claude Code API 429 rate (reactive backstop) - PENDING OTel deployment" status: covered -->
## Claude account usage gate

**Symptoms:** `Claude account usage poll unhealthy` (`alerts/tatara-usage-gate.yaml`, warning) fires when `tatara_account_usage_poll_health` is below 1 for 15m while the poller is enabled. `Claude account usage window near emergency ceiling` (`alerts/tatara-usage-gate.yaml`, critical) fires when the highest account usage window exceeds 80% utilization for 15m. `Claude account monthly overage climbing` (`alerts/tatara-usage-gate.yaml`, warning) fires when monthly pay-as-you-go overage utilization exceeds 80% for 30m. `Claude Code API 429 rate (reactive backstop) - PENDING OTel deployment` (`alerts/tatara-usage-gate.yaml`, critical) fires on any nonzero HTTP 429 rate over 5m, but the metric it reads does not exist on the platform yet.

**What it means:** These four gate the shared Claude subscription from different angles. Poll-unhealthy means the `/api/oauth/usage` poller has exceeded its failure threshold and the snapshot the gate holds is stale; the gate fails open on the last-known windows until they expire rather than blocking spawns on stale data (a `claudeSubscription`-mode project has no other input to fall back to). Near-emergency-ceiling means the account is close to a usage-window cap, with only the incident kind's 98% ceiling still having headroom; the 80% threshold is provisional, reused from the operator's `DefaultEmergencyPercent` convention pending real usage data. Monthly-overage is read-only and informational - overage never gates spawning by design, it exists for a human to decide whether to raise the plan limit or curb discretionary agent kinds. The 429 rule is meant as the reactive backstop of last resort, but it needs Claude Code's native OTel wired on wrapper pods and an OTLP-to-Prometheus collector in tatara-helmfile, neither of which has landed; the `or vector(0)` keeps the series defined, so today this rule can only read 0 and should never actually fire.

**Diagnosis:**
```bash
kubectl -n tatara logs deploy/tatara-operator | grep -i usage_poll | tail -50
```
```promql
tatara_account_usage_poll_health and (tatara_account_usage_poller_enabled == 1)
max(tatara_account_usage_utilization)
tatara_account_overage_percent
```

**Fix:** For poll-unhealthy, check operator logs for the poll failure reason (auth, 429, schema drift on the `/api/oauth/usage` response) and fix the underlying cause; the gate already fails open by design, so this is not itself an outage, but it is running blind. For near-emergency-ceiling, treat as critical: reduce spawn rate for non-incident kinds via [Tuning](tuning.md) until the window resets, since only incident work still has headroom. For monthly-overage, nothing is blocked automatically - a human decides whether to raise the account limit or curb discretionary kinds. For the 429 backstop, if it fires at all today, treat that as a metric-plumbing bug (confirm whether OTel Phase B and the OTLP collector Phase D have shipped) rather than a real 429 burst; once both phases land, any nonzero reading is a live incident per the design's floor.

---

<a id="tatara-runbook-tatara-operator-error-log-burst"></a><!-- alert: "Tatara operator error log burst" status: covered -->
<a id="tatara-runbook-tatara-memory-error-log-burst"></a><!-- alert: "Tatara memory error log burst" status: covered -->
<a id="tatara-runbook-tatara-operator-error-recurring"></a><!-- alert: "Tatara operator error recurring" status: covered -->
## Error log burst

**Symptoms:** `Tatara operator error log burst` (`alerts/tatara-logs.yaml`, warning) fires when tatara-operator logs more than 20 ERROR lines in 5m. `Tatara memory error log burst` (`alerts/tatara-logs.yaml`, warning) fires on the same threshold for tatara-memory. `Tatara operator error recurring` (`alerts/tatara-logs.yaml`, warning) fires when the operator logs 2 or more ERROR lines sharing the same `msg` label within 1h - below the burst threshold but a chronic trickle rather than a one-off.

**What it means:** The burst rules catch a sudden spike of ERROR-level logging on one component. The recurring rule catches a steady low-rate error a 5m burst window would never trip, grouped by the log line's own `msg` field so it names the specific recurring failure instead of just flagging "operator is erroring".

**Diagnosis:**
```promql
sum(count_over_time({namespace="tatara", app="tatara-operator"} | pattern `<_> <_> <_> <body>` | line_format "{{.body}}" | json | level="ERROR" [5m])) or vector(0)
sum(count_over_time({namespace="tatara", app="tatara-memory"} | pattern `<_> <_> <_> <body>` | line_format "{{.body}}" | json | level="ERROR" [5m])) or vector(0)
sum by (msg) (count_over_time({namespace="tatara", app="tatara-operator"} | pattern `<_> <_> <_> <body>` | line_format "{{.body}}" | json | level="ERROR" [1h])) or on() vector(0)
```
Run the same query in Grafana Explore and add `| msg="<the firing msg>"` for the recurring rule to isolate just that one failure.

**Fix:** Read the surfaced `msg` and structured fields to identify the failing operation, then treat it as a normal error investigation in that component's own code - operator errors usually trace to a reconcile loop (Task/Issue/MergeRequest/QueuedEvent), memory errors to the per-project API, Neo4j, or Postgres path. There is no single fix here; the rule's job is only to surface that ERROR-rate is elevated so a human reads the message.

---

<a id="tatara-runbook-tatara-operator-callback-auth-failures"></a><!-- alert: "Tatara operator callback auth failures" status: covered -->
<a id="tatara-runbook-operator-auth-rejections-elevated"></a><!-- alert: "Operator auth rejections elevated" status: covered -->
## Callback or API auth rejections

**Symptoms:** `Tatara operator callback auth failures` (`alerts/tatara-logs.yaml`, warning) fires when the operator logs more than 5 `callback_authn_failed` events in 15m on its turn-complete callback receiver - logged at INFO, so invisible to the error-log-burst rules above. `Operator auth rejections elevated` (`alerts/tatara-operator.yaml`, info) fires when the operator's REST API rejects auth (missing/invalid/rejected tokens) at more than 1/s over 15m.

**What it means:** The first is wrapper-to-operator traffic specifically: a wrapper pod's turn-complete callback is failing bearer-token verification on the way in, which is how the operator learns a turn finished. The second is broader - any caller of the operator's REST API failing auth, which could be a misconfigured token, an expired credential, or probing. The second is info-only (no `system=tatara` label): it emails rather than raising an incident Task.

**Diagnosis:**
```bash
kubectl -n tatara logs deploy/tatara-operator | grep callback_authn_failed | tail -50
```
```promql
sum (count_over_time({namespace="tatara", app="tatara-operator"} | pattern `<_> <_> <_> <body>` | line_format "{{.body}}" | json | action="callback_authn_failed" [15m])) or vector(0)
sum(rate(operator_auth_total{namespace="tatara",job="tatara-operator",result=~"missing_token|invalid_scheme|invalid_token|rejected"}[10m]))
```

**Fix:** For callback failures, check that the OIDC token the affected wrapper pod uses carries the audience the operator's callback receiver expects, and that Keycloak (`auth.szymonrichert.pl`, realm `master`) is healthy and issuing valid tokens. For elevated API rejections generally, check whether a credential rotated (Keycloak client secret, a tatara-cli token) without every caller picking up the new one, or whether the source looks like external probing rather than an internal caller.

---

<a id="tatara-runbook-tatara-agent-reported-platform-problem"></a><!-- alert: "Tatara agent reported platform problem" status: covered -->
## Agent-reported platform problem

**Symptoms:** `Tatara agent reported platform problem` (`alerts/tatara-operator.yaml`, warning) fires when agents report more than 0 internal issues in a category over a 5m delta of `agent_internal_issue_total`.

**What it means:** An agent inside a turn called the tool that records an internal issue, with a free-text description and a `category` label. This is a first-person signal: the agent itself is telling you something is wrong with the tatara platform, not with the target repo it was asked to change.

**Diagnosis:**
```bash
kubectl -n tatara logs deploy/tatara-operator | grep agent_internal_issue | tail -50
```
```promql
sum by (category) (increase(agent_internal_issue_total{namespace="tatara",job="tatara-operator"}[5m]))
```
Read the free-text description in the matching Loki line (`action="agent_internal_issue"`) - the `category` label alone will not tell you the specific complaint.

**Fix:** Triage by `category` and the reported free text; there is no generic fix because the category names whatever platform surface the agent hit (an MCP tool failure, memory unavailability, a contract mismatch it had to work around, and so on). Match the category against the other runbooks on this page first, or escalate to the component the description points at if none fits.

---

<a id="tatara-runbook-operator-orphan-reap-delete-errors"></a><!-- alert: "Operator orphan reap delete errors" status: covered -->
## Orphan reap delete errors

**Symptoms:** `Operator orphan reap delete errors` (`alerts/tatara-operator.yaml`, warning) fires when the backstop reaper fails to delete an orphan wrapper resource more than 0 times in 1h.

**What it means:** The operator's backstop reaper identified a wrapper resource it believes is orphaned and tried to delete it, but the delete itself failed. Failed-to-reap orphans accumulate - control-plane node pressure and cost leak - and the success-side `operator_orphan_reaped_total` metric does not surface this failure mode at all, so this rule is the only signal for it.

**Diagnosis:**
```bash
kubectl -n tatara logs deploy/tatara-operator | grep -i orphan | tail -50
kubectl -n tatara get pods -l tatara.io/task --sort-by=.metadata.creationTimestamp
```
```promql
sum(increase(operator_reap_delete_error_total{namespace="tatara",job="tatara-operator"}[1h])) or vector(0)
```

**Fix:** Read the operator log line for the delete error itself (RBAC denial, resource already gone/conflict, stuck finalizer) and act on that specific cause - a stuck finalizer on the orphaned pod is the most common reason a delete would fail outright. If pods are visibly piling up with no owning Task, deleting them by hand is a reasonable interim measure: orphan wrapper pods are individual agent pods, not helm-release-managed workloads, so a manual `kubectl delete pod` here does not carry the field-manager risk of patching a Deployment.

---

<a id="tatara-runbook-operator-context-bundle-over-budget"></a><!-- alert: "Operator context bundle over budget" status: covered -->
## Context bundle over budget

**Symptoms:** `Operator context bundle over budget` (`alerts/tatara-operator.yaml`, warning) fires when more than 0 context bundles for a given `agent_kind` exceeded `maxBundleBytes` in 1h and had their oldest comments elided.

**What it means:** The operator trims a rendered turn-0 context bundle to fit `maxBundleBytes` (default 400000, see [Tuning](tuning.md)) by eliding the oldest comments, marking the bundle with an explicit elided-comments marker so the agent knows it is working from a partial thread. At that budget against an 8192-byte per-comment cap this should essentially never fire, so a firing instance means a Task's comment thread genuinely outgrew the budget.

**Diagnosis:**
```bash
kubectl -n tatara logs deploy/tatara-operator | grep -i bundle_elided | tail -50
```
```promql
sum by (agent_kind) (increase(operator_bundle_elided_total{namespace="tatara",job="tatara-operator"}[1h])) or vector(0)
```

**Fix:** Identify the Task and `agent_kind` from the log line and check how many comments its owned Issue(s) accumulated - an unusually long-running or heavily-discussed Task is the expected cause, not a bug. If it recurs for one `agent_kind`, raise that project's `maxBundleBytes` via [Tuning](tuning.md); there is no per-kind override for this budget today, only the single project-wide value.

---

<a id="tatara-runbook-tier-quality-rubber-stamp-model-claude-sonnet-5"></a><!-- alert: "Tier-quality rubber-stamp (model=claude-sonnet-5)" status: covered -->
## Review rubber-stamp (tier quality)

**Symptoms:** `Tier-quality rubber-stamp (model=claude-sonnet-5)` (`alerts/tatara-quality.yaml`, warning) fires when claude-sonnet-5's review find-rate (`changes_requested` verdicts over all reviewed verdicts) drops below 2% over 6h, gated on a minimum review volume so it cannot fire on a quiet fleet.

**What it means:** A review agent running on claude-sonnet-5 is approving almost everything it looks at. That pattern is consistent with rubber-stamping - approving without substantive scrutiny - rather than genuinely finding nothing wrong across a real volume of reviews.

**Diagnosis:**
```promql
(sum(rate(operator_review_outcome_total{model="claude-sonnet-5",verdict="changes_requested"}[6h])) or vector(0))
/
clamp_min(sum(rate(operator_review_outcome_total{model="claude-sonnet-5"}[6h])), 0.001)
```
```bash
kubectl -n tatara logs deploy/tatara-operator | grep review_outcome | tail -50
```
Spot-check a handful of the sonnet-5 review Tasks' actual PR/MR comments for substance, not just the verdict field.

**Fix:** If a manual read of recent sonnet-5 reviews confirms shallow approvals, revert the `review` agent kind's `modelByKind` tiering back to Opus for the affected project (see the model/effort tiering section of [Tuning](tuning.md)) - the alert's own summary points at this as the G5 incident goal. If the sample looks genuinely clean, this may be a legitimately quiet review pool crossing the volume gate for the first time; reassess after another window rather than reverting immediately.

---

<a id="tatara-runbook-node-pod-network-partitioned"></a><!-- alert: "Node pod network partitioned" status: covered -->
## Node pod network partitioned

**Symptoms:** `Node pod network partitioned` (`alerts/tatara-nodes.yaml`, critical) fires when more than 80% of the pod-network scrape targets on one node are down for 10m while the node itself still reports `Ready`. Gated on a floor of at least 3 targets on that node, so a node carrying one or two pods cannot trip it on a single dead endpoint.

**What it means:** The node's pod overlay (flannel VXLAN on this cluster) is partitioned. The kubelet is host-network, so it keeps being scraped and keeps reporting the node `Ready`; everything running on the pod network becomes unreachable. That asymmetry is the entire signal - from every other angle the node looks healthy, which is why tatara-helmfile#239 ran for roughly 9 hours with the only page being `Operator replica missing`.

The known cause on this cluster is a NIC link flap: when the link drops, flannel recreates the `flannel.1` VXLAN device without restoring the per-peer ARP/FDB/route entries, and pod-to-pod traffic across that node stops. The USB NICs that caused the original incident were replaced on every node except the NAS on 2026-07-27, so do not diagnose a new occurrence as that specific hardware fault - but the flannel recovery gap is unfixed and applies to any node whose link drops for any reason.

**Diagnosis:**
```promql
count by (node) ((up == 0) * on (namespace, pod) group_left(node) kube_pod_info{node!=""})
/
count by (node) (up * on (namespace, pod) group_left(node) kube_pod_info{node!=""})
```
Healthy baseline is 0.09 to 0.28 per node - there are always some dead endpoints. A partitioned node reads close to 1.0.

```bash
kubectl get nodes -o wide
kubectl -n tatara get pods -o wide --field-selector spec.nodeName=<node>
kubectl get events -A --field-selector involvedObject.kind=Node | tail -30
```
Confirm the split: the node is `Ready`, its kubelet metrics are current, and its pods are the ones failing. If a host-network pod on the node is also down, this is not a pod-overlay partition and you are looking at a node-level fault instead.

**Fix:** Cordon and drain the node, which moves the workload onto healthy nodes and is the remediation both tatara-helmfile#239 and #245 prescribe:
```bash
kubectl cordon <node>
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
```
tatara-operator has a PodDisruptionBudget (`maxUnavailable: 1`) so the drain cannot take its HA to zero, and CloudNativePG maintains its own budgets for the memory Postgres clusters. Once drained, restart the CNI on that node to force flannel to rebuild the VXLAN peer state, verify pod-to-pod traffic across it, then uncordon.

Node-problem-detector and an automated remediation path would catch this class without a human, and are the standing ask recorded in tatara-helmfile's `ROADMAP.md`; they belong to the infra helmfile, not to any tatara-* repo.

---

<a id="tatara-runbook-node-volume-plane-wedged"></a><!-- alert: "Node volume plane wedged" status: covered -->
## Node volume plane wedged

**Symptoms:** `Node volume plane wedged` (`alerts/tatara-nodes.yaml`, critical) fires when a `Ready` node's kubelet has wanted to mount at least one more volume than it has actually mounted, continuously for 15m, over and above any gap already explained by a ReadWriteOnce handoff (see below). Pods that need the affected volume(s) hang in `Pending` or `ContainerCreating` indefinitely - **not** `CreateContainerError`, despite what an earlier version of this rule's summary claimed. Only pods needing the specific stuck volume(s) are affected, not everything scheduled on the node.

**What it means, and why this is now two rules, not one:** The raw desired-minus-actual gap has two possible causes with **opposite** remediations, and tatara-observability#90 caught the rule prescribing the wrong one on its very first firing:

1. **The node's own CSI/mount plane is broken** - the tatara-helmfile#245 class, which surfaced as `failed to stat ... permission denied` against a stale CephFS mount. Cordon and drain is correct here.
2. **A ReadWriteOnce volume is mid-handoff between two pods on two different nodes** - a `RollingUpdate` Deployment created a surge pod before releasing the volume from the pod it is replacing. The node is healthy; draining it evicts innocent workloads and does not resolve anything. This case now fires its own rule with its own remediation: [PersistentVolumeClaim multi-attach deadlock](#tatara-runbook-persistentvolumeclaim-multi-attach-deadlock).

This rule's expression subtracts case 2's volumes (Pending pods on this node whose RWO PVC is also referenced from a different node) before comparing against the threshold, so a pure multi-attach handoff no longer trips it at all - but the subtraction is a best-effort discriminator, not a proof, so **always confirm which case you are in before acting.**

A single volume showing for one scrape is a mount in progress, not this condition; that is what the 15m hold is for. A real node-level wedge persists for hours (tatara-helmfile#245 ran for a working day).

`volume_manager_total_volumes` comes from the kubelet, which is host-network, so this rule keeps working straight through the pod-overlay partition that `Node pod network partitioned` detects.

**Diagnosis - confirm which case you are in before touching the node:**
```promql
sum by (node) (volume_manager_total_volumes{state="desired_state_of_world"})
- sum by (node) (volume_manager_total_volumes{state="actual_state_of_world"})
```
```bash
kubectl get pods -A --field-selector spec.nodeName=<node> | grep -vE 'Running|Completed'
kubectl describe pod -n <ns> <pod> | tail -30
kubectl get events -A --field-selector involvedObject.kind=Pod | grep -i -E 'mount|volume' | tail -30
```
The pod `describe` output is the discriminator:

- A kubelet/CSI-level error against **this** node - for example `failed to stat ... permission denied` on a stale mount (tatara-helmfile#245) - means the node itself is at fault. Proceed to **Fix** below.
- An `attachdetach-controller` event reading `Multi-Attach error for volume ... already used by pod(s) <other-pod>` means another pod legitimately holds a ReadWriteOnce volume elsewhere. The node is healthy. **Do not drain it** - go to [PersistentVolumeClaim multi-attach deadlock](#tatara-runbook-persistentvolumeclaim-multi-attach-deadlock) instead.

Also check whether the PV still exists (`kubectl get pv <name>`) - a `VolumeFailedDelete ... still attached` event for a PV that is already gone is an ordinary teardown race for an ephemeral RBD volume, not a wedge, and should not be treated as either case above.

**Fix - only once a genuine node-level CSI fault is confirmed:** Cordon and drain the node, exactly as for a pod-network partition:
```bash
kubectl cordon <node>
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data
```
Draining releases the stale mounts; if a mount does not release, the node needs a kubelet restart or a reboot before it is safe to uncordon. Verify with the PromQL above that the gap returns to 0 before uncordoning.

Do **not** run this against a Multi-Attach handoff - the node hosting the `Pending` pod is not at fault there, and draining it evicts every other healthy pod using that node's volumes without resolving the deadlock.

Secondary finding from tatara-helmfile#245, still open: the memory Postgres PVCs are RWO volumes sitting on the cluster-default CephFS RWX storage class (`rook-ceph-rwx`), while the unused RBD block class `rook-ceph` would be the correct one, and a stale CephFS mount is precisely what produced the original failure. Exposing a Postgres storage class is a tatara-operator change (`PGCluster()` sets none today) and the migration is backup-and-restore, not an in-place edit.

---

<a id="tatara-runbook-persistentvolumeclaim-multi-attach-deadlock"></a><!-- alert: "PersistentVolumeClaim multi-attach deadlock" status: covered -->
## PersistentVolumeClaim multi-attach deadlock

**Symptoms:** `PersistentVolumeClaim multi-attach deadlock` (`alerts/tatara-nodes.yaml`, warning) fires when a ReadWriteOnce PVC is referenced from more than one node at once, with at least one of its referencing pods `Pending`, sustained for 15m.

**What it means - the circular wait:** This is a workload-level deadlock, not a node fault. A `RollingUpdate` Deployment with `maxUnavailable: 0` (surging a replacement pod before removing the old one) backed by a single-replica ReadWriteOnce PVC creates the new pod before terminating the old one. If the scheduler places the new pod on a different node than the one already holding the volume, the wait becomes circular and permanent:

- the new pod cannot attach the ReadWriteOnce volume, because the old pod legitimately still holds it;
- the old pod is not terminated until the new pod becomes `Ready`;
- the new pod cannot become `Ready` without the volume.

Nothing about this resolves on its own - it holds indefinitely until a human intervenes.

**The node hosting the `Pending` pod is not at fault.** Do not cordon or drain it. See [Node volume plane wedged](#tatara-runbook-node-volume-plane-wedged) for how to tell this apart from a genuine node-level CSI fault, which needs the opposite response.

**Diagnosis:**
```bash
kubectl -n <ns> describe pod <pending> | grep -A2 Multi-Attach
kubectl get pvc <name> -o jsonpath='{.spec.accessModes}'
kubectl get deploy <name> -o jsonpath='{.spec.strategy}'
```
The `describe pod` output names the pod that already holds the volume; the access mode confirms `ReadWriteOnce`; the strategy confirms a `RollingUpdate` with `maxUnavailable: 0` is what let the surge pod get created before the old one was torn down.

**Immediate unblock:** Delete the pod still holding the volume. The `Pending` pod attaches as soon as the volume is released, and the Deployment continues its rollout normally from there.

**Permanent fix:** Change the Deployment's rollout strategy so a surge pod can never be created before the old one releases the volume - either `strategy: Recreate` for any single-replica ReadWriteOnce workload, or `maxUnavailable: 1` / `maxSurge: 0` if a `RollingUpdate` is still wanted.

Real example seen: `home-automation/piper`. A daily 03:30Z re-render of its pod template re-rolls the Deployment every day; most days the handoff completes within minutes, but whenever the scheduler happens to place the surge pod on a different node than the current holder, it deadlocks permanently until a human deletes the old pod and fixes the strategy. It will keep re-deadlocking daily until the Deployment's strategy is changed - this is not a one-off, it is latent every day the fix is not applied.

---

<a id="tatara-runbook-log-collector-node-coverage-incomplete"></a><!-- alert: "Log collector node coverage incomplete" status: covered -->
## Log collector node coverage incomplete

**Symptoms:** `Log collector node coverage incomplete` (`alerts/tatara-logs.yaml`, warning) fires when the count of `Ready` nodes exceeds the count of ready log-collector pods for 30m.

**What it means:** At least one `Ready` node runs no log collector, so every pod scheduled there ships no logs to Loki at all. This matters far beyond the missing lines: **every Loki-backed rule in `alerts/tatara-logs.yaml` is blind on those nodes and returns a clean zero regardless of what happened there.** An empty Loki result for a pod on an uncovered node proves nothing. The failure is self-concealing, which is exactly how the agent report in tatara-observability#79 was lost.

The rule reads Prometheus, not Loki, deliberately: a Loki query cannot detect its own blind spot, because a node that ships nothing has no stream to select. kube-state-metrics is the only surface that knows about a node the collector never reached. For the same reason the rule sets `no_data_state: NoData` and `exec_err_state: Error` on itself, opting out of the Loki-shaped `Alerting` defaults the rest of that file uses.

The expression is a single subtraction so that it catches both failure modes: the DaemonSet never being scheduled onto a node (desired below node count) and being scheduled but unhealthy (ready below desired).

**Diagnosis:**
```promql
count(kube_node_status_condition{condition="Ready",status="true"} == 1)
- (sum(kube_daemonset_status_number_ready{namespace="monitoring",daemonset=~"promtail|alloy|grafana-agent|vector|fluent-bit"}) or vector(0))
```
Identify which nodes are uncovered:
```promql
count by (node) (kube_node_info)
count by (node) (kube_pod_info{namespace="monitoring", created_by_name="promtail"})
```
```bash
kubectl -n monitoring get ds
kubectl -n monitoring get pods -o wide -l app.kubernetes.io/name=promtail
kubectl get nodes -o custom-columns=NAME:.metadata.name,TAINTS:.spec.taints
```
Cross-check in Grafana Explore that the collector's `node_name` label values cover every node; a node that has never appeared over a 7d window has never shipped a line.

**Fix:** This is an infra change, not a tatara one - the log collector is not deployed by any tatara-* repo, so `tatara-helmfile` cannot fix it. Compare the collector DaemonSet's `nodeSelector` and `tolerations` against the uncovered nodes' taints. On this cluster the working reference is the `prometheus-prometheus-node-exporter` DaemonSet, which reaches every node; giving the collector the same toleration set, and dropping any restricting `nodeSelector`, is the fix. Route it to whoever owns the monitoring stack.

Until it lands, treat every namespace-wide Loki query as covering only part of the fleet, and confirm which nodes a pod ran on before concluding anything from an empty log result. Escalate to the cluster maintainer if the collector owner is unclear - there is no tatara-side workaround, only the awareness this alert provides.
