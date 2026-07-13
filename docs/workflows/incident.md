---
title: Incident Response
---

# Incident Response

When a Grafana alert fires, the tatara operator receives a webhook, deduplicates it against
any already-running investigation, and spawns a project-scoped `incident` Task at the
`investigating` stage. The agent queries Grafana live, diagnoses the problem, and submits an
outcome that either files exactly one evidence-backed issue - carrying an additive
`tatara-incident` label so it is immediately visible as incident-originated - or declares a
confirmed false positive. `investigating` is the stage the `incident` origin kind enters at
`triaging`, and it runs the `incident` agent kind - see the
[origin-kind table](index.md#origin-kinds-and-the-agent-kind-each-one-spawns).

---

## 1. Trigger

The operator exposes a Grafana-specific webhook endpoint per Project:

```
POST /operator/webhooks/{project}/grafana
```

Grafana (Alertmanager-compatible webhook contact point) calls this endpoint when one of the
registered alert groups transitions to `firing`. Any other status (`resolved`, `pending`) is
silently accepted with HTTP 202 and no Task is created.

### Prerequisites

The `spec.grafana` block on the Project CR must have `enabled: true`:

```yaml
spec:
  grafana:
    enabled: true
    url: https://grafana.example.com   # base URL for the grafana-mcp queries
    secretRef: tatara-grafana          # Secret with serviceAccountToken + webhookSecret
```

Without `enabled: true`, the endpoint returns 404.

### Authentication

The endpoint uses a **static bearer token** (not HMAC). Grafana must send:

```
Authorization: Bearer <webhookSecret>
```

The operator reads `webhookSecret` from the Secret named in `spec.grafana.secretRef` and
performs a constant-time comparison. A mismatch returns 401.

The same Secret also carries `serviceAccountToken`, which the operator mounts into the
grafana-mcp sidecar so the agent can query Grafana read-only.

??? note "Bearer vs. HMAC"
    The SCM webhook endpoint (`/operator/webhooks/{project}`) uses HMAC signature
    verification tied to each provider's signature header. The Grafana endpoint uses a
    simpler static bearer token because Grafana's unified alerting webhook does not support
    HMAC signing.

---

## 2. Dedup

Each Grafana webhook payload carries a `groupKey` string identifying the alert group. The
operator derives a dedup key as the first **16 hex characters of SHA-256(`groupKey`)** and
stamps it on the Task as label `tatara.dev/alert-group`.

```go
// alertGroupHash (grafana.go)
h := sha256.Sum256([]byte(a.GroupKey))
return hex.EncodeToString(h[:])[:16]
```

This hash is also used as the deterministic name for the `QueuedEvent`, making
concurrent re-fires within the same alert group idempotent at the queue layer: the second
`EnqueueEvent` call returns `AlreadyExists` instead of creating a second investigation.

!!! important
    An ongoing incident Task (any non-terminal phase) for the same alert group blocks a new
    one. Once that Task reaches a terminal phase (Succeeded or Failed), the next alert fire
    produces a fresh Task.

---

## 3. Context injection

Before spawning the agent, the operator renders a compact alert context block from the
webhook payload and stores it in two places:

- **Task goal** - passed as the turn-0 instruction (see below).
- **Annotation `tatara.dev/grafana-alert`** - stored on the Task for audit/debug.

The rendered block contains:

```
status=firing groupKey=<groupKey>
commonLabels: {alertname=..., component=..., severity=..., system=tatara}
commonAnnotations: {summary=..., description=...}
externalURL: https://grafana.example.com/...
alert[0]: status=firing labels={...} annotations={...} startsAt=<RFC3339> generatorURL=...
```

This gives the agent the exact alert rule URL (`generatorURL`) and the Grafana instance URL
(`externalURL`) so it can navigate directly to the firing rule and related dashboards without
external input.

---

## 4. Grafana MCP access

When `spec.grafana.enabled` is true, the operator provisions a read-only grafana-mcp for the
Project. The grafana-mcp is exposed as an in-cluster service (reported in
`status.grafana.endpoint`), and its URL is injected as `TATARA_GRAFANA_MCP_URL` into **every**
agent pod in a grafana-enabled project, regardless of kind (the injection gates only on
`project.Spec.Grafana.Enabled`, not on the Task kind). In practice only `incident` agents are
prompted to use it; other kinds simply do not act on the mounted server.

The agent uses it to:

- Follow the `generatorURL` to read the exact firing rule definition.
- Run PromQL queries against the relevant datasource.
- Run LogQL queries against Loki for correlated log evidence.
- Inspect related dashboards and annotations.

!!! warning "Read-only scope"
    The grafana-mcp is configured with a Grafana **Viewer** service account token. The agent
    cannot modify alert rules, silence alerts, or acknowledge incidents through this
    interface. All grafana-mcp queries are read-only by design.

If the grafana-mcp is unreachable or returns an error (e.g. 401), the agent is instructed to
call `report_internal_issue` (platform-failure self-report channel) rather than filing a
normal incident issue. The distinction is: tool failure should surface as a platform alert,
not get misrouted as an application incident.

For an investigation spanning multiple repos or datasources, the incident agent fans out
`Agent`-tool subagents per repo/per signal source rather than holding all evidence in one
context - same principle as brainstorm's fan-out, same retirement of the `Workflow` tool and
`ultracode` effort tier.

---

## 5. Agent output

The pod's only path forward is `submit_outcome`:

```json
{"action":"file_issue","alert_rules":["tatara_operator_reaper_stalled"],
 "issue":{"repo":"tatara-operator","title":"...","body":"..."},
 "reason":"..."}
```
or
```json
{"action":"false_positive","alert_rules":["tatara_operator_reaper_stalled"],
 "reason":"..."}
```

`alert_rules` (at least one) and `reason` are **required on both** actions - an incident always
cites which rule(s) fired and why it reached its verdict, confirmed or not.

- **`file_issue`:** the operator creates the issue in the named repository, mints the Issue CR
  **under this same Task** - the incident Task, not a fresh one - and moves the stage
  `investigating -> clarifying`. The issue is additively marked `tatara-incident`
  (configurable, below) for its entire lifetime, independent of whatever stage its owning Task
  is in - this is what lets it be filtered and dashboarded on incident-origin without a
  separate issue type.
- **`false_positive`:** the stage moves `investigating -> rejected`. No issue is created, and
  `status.documentedBy` stays permanently empty - a false positive owns no merged MR, so it is
  never eligible for the nightly [documentation](documentation.md) batch either.

### Custom incident label

The label defaults to `tatara-incident`. Override it in the Project SCM spec:

```yaml
spec:
  scm:
    incidentLabel: "P0-incident"
```

---

## 5a. Tier-revert incidents

A `tatara_tier_quality` alert (fired by the quality-feedback loop when a model/effort tier
downgrade regresses review or CI outcomes for a given `kind`) routes through the **identical**
webhook path as any other Grafana alert: same bearer check, same `groupHash` dedup, same
`incident` Task, same alert-class queue slot. There is no separate code path, dedup key, or
Task kind for tier-revert.

The only branch is in **which goal the agent gets**. The webhook checks
`CommonLabels["tatara_tier_quality"] == "true"`:

| `tatara_tier_quality` label | Goal | Behavior |
|---|---|---|
| absent / `false` | `GoalProject` | Standard read-only investigation ending in `submit_outcome(action=file_issue)`; the agent is explicitly told not to remediate |
| `true` | `GoalTierRevert(project, kind, model)` | Investigates the quality regression for the named `kind`/`model` and opens a **GitOps MR against `tatara-helmfile`** (`values/project-<project>/common.yaml`) bumping `agent.modelByKind[kind]` back to the higher tier (e.g. `claude-opus-4-8`) and raising `agent.effortByKind[kind]` |

!!! warning "Agent proposes, never merges"
    `GoalTierRevert` explicitly instructs the agent to open the `tatara-helmfile` MR and stop -
    "Do NOT merge." The tier revert is agent-**proposed** GitOps, not a live edit of the
    `Project` CR's `modelByKind`/`effortByKind` fields via an MCP tool, and not a dedicated
    operator reconcile loop. It goes through the same human/CI-gated `tatara-helmfile` merge
    path as any other deploy pin change, consistent with the platform's GitOps-only deploy
    rule.

---

## 6. Queue priority

Incident events are enqueued with `class: alert` and `priority: 0` - two independent guarantees,
not one. `class: alert` draws from its own reserved pool, on top of the normal pool:

| Field | Default | Description |
|-------|---------|-------------|
| `spec.queue.alertCapacity` | `1` | Slots reserved exclusively for `class: alert` events |
| `spec.agent.maxConcurrentAgents` | `3` | Slots for the normal pool (also the project-wide pause switch at `0`) |

A saturated normal pool does **not** block an incoming incident: the alert pool is drained
first and independently, so an incident is admitted even while every normal-pool slot is
running an implement/brainstorm/review pod. `priority: 0` on the `QueuedEvent` itself (see the
[QueuedEvent reference](../reference/queued-event.md)) is a second, redundant guarantee within
whichever pool the event does land in.

!!! danger "An incident stuck in `triaging` for 15 minutes is a CRITICAL alert"
    Between the reserved alert-pool capacity and priority-0 admission, an incident should never
    queue meaningfully. A `triaging`-stage incident Task older than 15 minutes indicates the
    admission path itself is broken, not ordinary backlog pressure - it pages, not just logs.

---

## End-to-end flow

```mermaid
sequenceDiagram
    participant Grafana
    participant Operator as Operator webhook<br/>/operator/webhooks/{project}/grafana
    participant Queue as QueuedEvent<br/>(class=alert)
    participant Task as incident Task
    participant Agent as Agent pod<br/>(grafana-mcp sidecar)
    participant SCM as SCM (GitHub/GitLab)

    Grafana->>Operator: POST firing alert<br/>Authorization: Bearer <token>
    Operator->>Operator: Verify bearer token<br/>Check grafana.enabled
    Operator->>Operator: Compute groupHash = sha256(groupKey)[:16]
    Operator->>Queue: EnqueueEvent(dedupKey=groupHash, class=alert, priority=0)
    Note over Queue: Duplicate fire -> AlreadyExists, drop
    Queue->>Task: Admit -> create incident Task at investigating<br/>label tatara.dev/alert-group=<hash><br/>annotation tatara.dev/grafana-alert=<ctx>
    Task->>Agent: Spawn pod with grafana-mcp
    Agent->>Agent: Query Grafana (PromQL/LogQL/<br/>dashboards/alert rule)
    Agent->>Agent: Form diagnosis
    Agent->>Task: submit_outcome(action=file_issue, alert_rules, reason)
    Task->>SCM: Operator creates the issue,<br/>mints the Issue CR under this Task
    SCM-->>Task: Issue URL
    Task->>Task: stage: investigating -> clarifying
```

---

## Routing boundary

The incident pipeline spans two repos with a clear ownership split:

| Concern | Owner |
|---------|-------|
| Alert rule definitions (`alerts/*.yaml`, PromQL/LogQL expressions, thresholds) | `tatara-observability` |
| `system=tatara` notification policy routing | `infra/terraform/grafana` |
| Operator incident webhook contact point | `infra/terraform/grafana` |
| Incident Task lifecycle, dedup, agent goal | `tatara-operator` |

### Alert label requirements

For an alert to route to the incident webhook, its labels must include:

```yaml
labels:
  homelab: "true"         # matches the homelab top-level routing policy
  system: "tatara"        # routes to the tatara operator contact point
  component: "operator"   # identifies the originating component (informational)
  severity: "warning"     # "warning" or "critical" -> incident; "info" -> email only
```

!!! warning "Missing `system=tatara` silently misroutes"
    An alert rule without `system: "tatara"` will not reach the operator webhook. It will
    be routed by the homelab catch-all policy (typically email). No Task is created and no
    error is surfaced. Verify the label is present when a firing alert produces no incident
    Task.

Rules in `tatara-observability` follow the file-per-component convention
(`alerts/tatara-operator.yaml`, `alerts/tatara-memory.yaml`, etc.). Agents edit these YAML
files directly and open a PR; `terraform apply` runs on merge to main.

---

## Reference: Project CR fields

### `spec.grafana`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool` | - | Must be `true` to activate incident handling and the alert webhook endpoint. |
| `url` | `string` | - | Grafana base URL; passed to grafana-mcp as the query target. |
| `secretRef` | `string` | - | Name of a Secret in the operator namespace. Must contain keys `serviceAccountToken` (Grafana Viewer SA token) and `webhookSecret` (static bearer for the alert webhook). |
| `cooldownSeconds` | `int` | `3600` | **Deprecated.** Previously imposed a re-fire cooldown; replaced by in-flight dedup. Has no effect. |

### `spec.queue`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `alertCapacity` | `int` | `1` | Reserved concurrent slots for `class: alert` (incident) events. Independent of `spec.agent.maxConcurrentAgents`. |

### `spec.scm.incidentLabel`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `incidentLabel` | `string` | `tatara-incident` | Additive label stamped on every incident-originated issue for its entire lifetime. It is a permanent origin marker, never a status projection - see [Labels are write-only](../operations/security/approval-gates.md#labels-are-write-only). |
