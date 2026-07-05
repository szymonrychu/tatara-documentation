# tatara Grafana alerting -> emergency-brainstorm - Design

Date: 2026-06-24
Repos: infra/terraform (grafana) [primary], tatara-helmfile [delivery], infra/helmfile [none]
Status: approved design, pre-plan
Scope: single milestone, end-to-end. Builds on the shipped 2026-06-19 Grafana
incident-response feature (`docs/superpowers/specs/2026-06-19-grafana-incident-response-design.md`).

## Problem

The tatara platform exposes Prometheus metrics and structured JSON logs, but nothing
turns a degraded tatara component into action. We want Grafana alerts (workload-generic
+ tatara-specific, metric + log-based) that, when they fire, are fed back into tatara via
the existing operator webhook so tatara reacts: an `incident` Task (emergency-brainstorm)
investigates and files a `brainstorming` GitHub issue that enters the normal lifecycle.

The reactive machinery already exists and is live (operator `baa7be0`): a webhook receiver
`POST /operator/webhooks/{project}/grafana` (bearer-gated, firing-only, dedup) spawns a
project-scoped `incident` Task whose read-only grafana-mcp agent calls `propose_issue`.
This milestone supplies the missing pieces: the **alert definitions**, the **routing**, and
the **per-project enablement** so that pipeline actually carries tatara alerts.

## Key discovery (load-bearing)

Grafana in this cluster is **terraform-managed** (`infra/terraform/grafana`, grafana
provider, S3 state, GitLab-CI apply), not helm/sidecar. Alert rules are simple module YAML
under `terraform/grafana/alerts/*.yaml`; contact points + the notification-policy tree live
in `terraform/grafana/grafana_config.tf`.

The tatara routing is **prewired but broken**:
- `grafana_contact_point.tatara` ("Tatara") points at `.../operator/webhooks/tatara` - the
  **SCM** handler (`server.go:66 r.Post("/operator/webhooks/{project}", s.handle)`), not the
  grafana handler (`server.go:67 .../{project}/grafana`). A Grafana payload hits `scm.Select`
  and 400s "unrecognized provider".
- The contact point sends **no `Authorization` header**, but `handleGrafanaAlert`
  (`server.go:827-839`) requires `Grafana.Enabled` (else 404) AND a constant-time
  `Authorization: Bearer <webhookSecret>` match (else 401).
- The tatara Project has **no `spec.grafana`** (grafana not enabled), so the route 404s
  regardless. Only `infrastructure` is grafana-enabled (and its `.critical` contact-point
  webhook is broken the same way - it has never fired an incident end-to-end).

Consequence: near-zero tatara component-repo code. The work is terraform (alerts + contact
points) + tatara-helmfile (enable + secret + metrics allowlist).

## Approved decisions

1. **All Grafana-managed** alerting (metric + log), one engine, routed via the existing
   `Tatara` Grafana contact point to the operator webhook. (Not per-chart PrometheusRules,
   not a kube-prom AlertmanagerConfig.)
2. **Definitions live centrally in terraform** (`alerts/tatara-*.yaml`), covering each
   tatara component - not per-component chart ConfigMaps (no Grafana alerting sidecar exists;
   terraform is the established mechanism).
3. **Loki/LogQL alerting** included: wrapper `action=internal_issue_report` (agent-reported
   platform problems) with the scrubbed `description` carried into the alert as a label so
   the "what's wrong" text flows into the issue; plus a generic error-burst rule per Go
   component.
4. **Severity gate:** `warning`+`critical` alerts route to the emergency-brainstorm; `info`
   alerts do not (they omit the `system=tatara` label and so only reach the homelab email
   route, not the operator webhook).
5. **Push-receiver allowlist fix:** widen `pushMetricsAllowedPrefixes` so wrapper/ingester
   agent-pod metrics reach Prometheus (else their metric rules are dark). The wrapper's
   primary signal (`internal_issue_report`) is a log, so it works regardless.
6. **Fix the broken contact points** for both `tatara` and `infrastructure` (URL +
   `/grafana` suffix + Bearer). The SA token for grafana-mcp is reused from the existing
   read-only `infrastructure-grafana` Viewer token (sharing a read-only identity is
   acceptable in a homelab); no new token minted.

## Architecture and data flow

```
component metric (/metrics scrape)  or  component log (Loki ingest)
        |                                       |
        v                                       v
   Prometheus (uid=prometheus)            Loki (uid=<loki>, "Loki (Working)")
        |                                       |
        +------------------+--------------------+
                           v
        Grafana-managed rule  (terraform alerts/tatara-*.yaml)
        labels: homelab=true, system=tatara, component=<svc>, severity=warning|critical
                           |
                           v
        notification policy (grafana_config.tf):
          root -> policy[homelab=true]            (mute weekends)
                    -> policy[system=tatara]  -> contact_point "Tatara"   (evaluated first)
                    -> policy[severity=critical] -> contact_point "Critical"
                           |
                           v  (Bearer <webhookSecret>)
        POST https://tatara.szymonrichert.pl/operator/webhooks/tatara/grafana
                           |
                           v
        [live operator] handleGrafanaAlert  (Enabled? bearer? firing? dedup sha256(groupKey))
                           v
        incident Task -> agent pod (TATARA_TOOL_PROFILE=incident, grafana-mcp read-only)
                           v
        propose_issue(repo, evidence) -> brainstorming issue -> triage/lifecycle loop
```

Note: the `system=tatara` route is a **child of the `homelab=true` policy**, so tatara
alerts must carry `homelab=true` AND `system=tatara` to reach the Tatara contact point.
`info` alerts carry `homelab=true` but not `system=tatara`, so they fall through to the
homelab email route only (the severity gate).

## Unit A - extend `modules/grafana_alert` for Loki

The module (`terraform/grafana/modules/grafana_alert/main.tf`) builds a Grafana rule from a
query expression + a reduce + a round + a threshold. `type_to_model` currently supports only
`prometheus` and `math`; a `loki` `query_type` would fail the `type_to_model[query_type]`
lookup. Add a Loki model and let `expr` populate for Loki:

- Add `default_loki_model` (mirrors `default_prometheus_model`: `editorMode=code`,
  `queryType="instant"`, `instant=true`, `intervalMs`, `maxDataPoints`, `range=false`).
- Add `"loki" = local.default_loki_model` to `type_to_model`.
- In the runtime model `lookup`, set `expr` for `query_type` in `["prometheus","loki"]`
  (currently `== "prometheus"`); datasource `type`/`uid` already derive from `query_type`
  and the per-query `datasource_uid`, so a Loki query just needs `datasource_uid=<loki uid>`,
  `query_type="loki"`, and a LogQL `expression`.

This is the only code-shaped change and stays generic (any future Loki alert benefits).

## Unit B - alert inventory (`terraform/grafana/alerts/tatara-*.yaml`)

One file per group; filename (sans `.yaml`) becomes the rule-group name. Each rule uses the
module schema: `name`, `queries:[{expression, [datasource_uid, query_type]}]`,
`math_operator`, `threshold`, `for`, `decimal_points`, `annotations`, `labels`. Inline
comparisons from the synthesis are split into `expression` (the value) + `math_operator` +
`threshold` (e.g. `X > 0.2` -> expression `X`, op `>`, threshold `0.2`; `up == 0` ->
expression `up{...}`, op `<`, threshold `1`).

Labels on every routed rule: `{homelab: "true", system: "tatara", component: "<svc>",
severity: "warning"|"critical"}`. `info` rules omit `system`.

### B.1 Workload-generic group `tatara-workloads.yaml` (per component: operator, memory, chat, wrapper, ingester)

Rendered once per component (component label + selector substituted). kube-state-metrics /
cAdvisor / `up` based - independent of app metrics.

| name | severity | for | expression / op / threshold |
|---|---|---|---|
| `Tatara<C> target down` | critical (operator,memory) / warning (chat,wrapper) | 5m | `up{job=~".*<svc>.*"}` `<` `1` |
| `Tatara<C> crash looping` | critical (operator,memory) / warning | 5m | `increase(kube_pod_container_status_restarts_total{namespace="tatara",pod=~".*<svc>.*"}[15m])` `>` `3` |
| `Tatara<C> OOMKilled` | critical (operator,memory) / warning | 1m | `max(kube_pod_container_status_last_terminated_reason{namespace="tatara",pod=~".*<svc>.*",reason="OOMKilled"})` `>` `0` |
| `Tatara<C> container waiting` | warning | 10m | `sum(kube_pod_container_status_waiting_reason{namespace="tatara",pod=~".*<svc>.*",reason!=""})` `>` `0` |
| `Tatara<C> pod not ready` | warning | 10m | `max(kube_pod_status_ready{namespace="tatara",pod=~".*<svc>.*",condition="true"})` `<` `1` |

Selectors (`job`/`pod` regex) are confirmed against live Prometheus during implementation
and pinned to the exact ServiceMonitor `job` label per component.

### B.2 `tatara-operator.yaml` (component=operator)

The existing operator-chart `tatara-loop` PrometheusRule stays as the Prometheus-evaluated
loop-health set (it routes nowhere today). To get operator alerts to the webhook under the
all-Grafana-managed decision, define the operator alerts as Grafana rules here too. To avoid
double-coverage confusion the synthesis-net-new operator signals are prioritized; the core
loop-health alerts are reproduced as Grafana rules (single routing source for incidents).

Key rules (net-new + reproduced loop-health), all `severity=warning` unless noted:
- `Tatara operator down` - `up{job=~".*tatara-operator.*"}` `<` `1` (critical, 5m).
- `Tatara loop wedged` - `sum(increase(operator_reconcile_total[15m]))` `<` `1` (15m).
- `Tatara loop stalled` - `increase(tatara_scan_tasks_created_total[3h]) + increase(tatara_scan_items_total[3h])` `<` `1` (30m).
- `Tatara reconcile error ratio high` - `sum(rate(operator_reconcile_total{result="error"}[10m]))/clamp_min(sum(rate(operator_reconcile_total[10m])),0.001)` `>` `0.2` (15m).
- `Tatara turn submit failure ratio high` - submit error ratio `>` `0.3` (10m).
- `Tatara agent http unreachable spike` - `operator_agent_http_total{outcome=~"unreachable|timeout"}` ratio `>` `0.25` (10m).
- `Tatara turn submit p95 high` - `histogram_quantile(0.95, sum(rate(operator_turn_submit_duration_seconds_bucket[10m])) by (le))` `>` `30` (15m).
- `Tatara scm write errors` - `sum(rate(operator_scm_writes_total{result="error"}[15m]))` `>` `0.1` (15m).
- `Tatara webhook error ratio high` - webhook error/bad_signature/provider_mismatch ratio `>` `0.2` (10m).
- `Tatara memory stack failed` - `operator_memory_stacks{phase="Failed"}` `>` `0` (15m).
- `Tatara tasks inflight pinned` - `operator_tasks_inflight` `>=`(`>` `7.999`) `8` (2h).
- `Tatara task failures` - `increase(operator_task_terminal_total{phase="Failed"}[30m])` `>` `0`.
- `Tatara agent boot crashloop` - `increase(operator_agent_boot_crash_total{outcome="failed"}[30m])` `>` `0`.
- `Tatara queue depth backlog` - `max(operator_queue_depth)` `>` `10` (15m).

### B.3 `tatara-memory.yaml` (component=memory)

- `Tatara memory 5xx ratio high` - `http_requests_total` 5xx ratio (status text) `>` `0.1` (10m).
- `Tatara memory lightrag error ratio high` - `lightrag_calls_total{result="error"}` ratio `>` `0.2` (10m).
- `Tatara memory lightrag p95 high` - `histogram_quantile(0.95, ... lightrag_call_duration_seconds_bucket ...)` `>` `10` (15m).
- `Tatara memory ingest failure ratio high` - `ingest_jobs_total{status="failed"}` ratio `>` `0.2` (15m).
- `Tatara memory code-graph query errors high` - `code_graph_query_total{result="error"}` ratio `>` `0.2` (10m).
- `Tatara memory analytics stalled` - `code_graph_analytics_dirty_repos > 0 and rate(code_graph_analytics_runs_total[30m]) == 0` (expression returns dirty count when stalled) `>` `0` (30m).

### B.4 `tatara-chat.yaml` (component=chat)

- `Tatara chat 5xx ratio high` - chat `http_requests_total` 5xx ratio `>` `0.1` (10m).
- `Tatara chat store errors high` - `increase(chat_store_op_errors_total[15m])` `>` `0` (15m).
- `Tatara chat store p95 high` - chat store-op duration p95 `>` threshold (15m).
- `Tatara chat sweeper stalled` - sweeper runs flat while pending `>` `0` (30m).

### B.5 `tatara-wrapper.yaml` (component=wrapper) - requires the allowlist fix to fire

- `Tatara wrapper commit/push failing` - `ccw_commit_push_total{result="fail"}` ratio `>` `0.3` (15m).
- `Tatara wrapper turns erroring` - `ccw_turns_total{result!="success"}` ratio `>` `0.5` (15m).
- `Tatara wrapper http 5xx` - `ccw_http_requests_total{status_code=~"5.."}` ratio `>` `0.1` (10m).
- `Tatara wrapper http panics` - `increase(ccw_http_panics_total[10m])` `>` `0`.
- `Tatara wrapper metrics dark` (canary, fires today) - `sum(rate(operator_push_series_dropped_total{reason="reserved_name"}[10m]))` `>` `0` (15m).

### B.6 `tatara-ingester.yaml` (component=ingester) + `tatara-argo.yaml` (component=argo)

- `Tatara ingest job failing` - kube_job_status_failed for `app.kubernetes.io/component=ingest` `>` `0` (15m).
- `Tatara ingest run failure ratio` - `ingest_run_result_total{result="failure"}` ratio `>` `0.25` (30m).
- `Tatara ingest job stuck active` - active ingest Job age `>` `1800`s.
- `Tatara CI workflow failing` (argo) - `argo_workflows_count{namespace="tatara",status="Failed"}` `>` `0` (gated on the Argo controller being scraped; else dropped).

### B.7 `tatara-logs.yaml` (Loki, query_type=loki, datasource=<loki uid>)

CRI prefix is not stripped at ingest, so each rule strips it with `| pattern` before `| json`.

- `Tatara agent reported platform problem` (component=wrapper, severity=warning, for 0m):
  ```
  expression: |
    sum by (description, category, severity) (
      count_over_time(
        {namespace="tatara", app="tatara-claude-code-wrapper"}
        | pattern `<_> <_> <_> <body>` | line_format `{{.body}}`
        | json | action="internal_issue_report" [5m]
      )
    )
  math_operator: ">"   threshold: 0   decimal_points: 0
  annotations:
    summary: 'Agent reported a platform problem ({{ index $labels "category" }}/{{ index $labels "severity" }}): {{ index $labels "description" }}'
  labels: { homelab: "true", system: "tatara", component: "wrapper", severity: "warning" }
  ```
  The scrubbed `description` rides as a label -> `commonLabels` -> operator `renderAlertContext`
  -> issue body, satisfying "include what's wrong". Cardinality is bounded (low-volume,
  PII-scrubbed in the wrapper tailer).
- `Tatara <C> error log burst` for component in {operator, memory, chat} (severity=warning,
  for 5m): `sum(count_over_time({namespace="tatara", app="tatara-<svc>"} | pattern ... |
  line_format ... | json | level="ERROR" [5m]))` `>` `20`. Annotation links Grafana Explore
  (`generatorURL`); the incident agent fetches the offending lines live via grafana-mcp.

## Unit C - labeling, routing, severity gate

- Every routed rule: `homelab=true` (matches the homelab parent policy), `system=tatara`
  (matches the child route to the Tatara contact point), `component=<svc>`, `severity`.
- The `system=tatara` policy route already exists in `grafana_config.tf` (no change needed):
  it is evaluated first under `homelab=true` so tatara alerts win over the critical/email route.
- Severity gate = label presence: `info` rules omit `system=tatara`, so they reach only the
  homelab email path, never the operator webhook.

## Unit D - contact-point fixes (`grafana_config.tf`)

- `grafana_contact_point.tatara`: webhook `url` -> `https://tatara.szymonrichert.pl/operator/webhooks/tatara/grafana`; add `authorization_scheme = "Bearer"` and `authorization_credentials = var.tatara_webhook_secret`.
- `grafana_contact_point.critical` webhook (-> infrastructure): `url` -> `.../operator/webhooks/infrastructure/grafana`; add `authorization_scheme = "Bearer"` + `authorization_credentials = var.infrastructure_webhook_secret`. (Scope add: makes infrastructure incidents fire; criticals already reaching this contact point will now also create incident issues.)
- New sensitive variables `tatara_webhook_secret`, `infrastructure_webhook_secret` in
  `variables.tf`, sourced from GitLab CI `TF_VAR_*` (never committed). Values must equal the
  `webhookSecret` keys in the respective `*-grafana` k8s Secrets the operator verifies against.

## Unit E - tatara-helmfile (delivery)

- `values/project-tatara/common.yaml`: add `project.spec.grafana` mirroring
  `project-infrastructure` - `enabled: true`, `url: http://prometheus-grafana.monitoring.svc.cluster.local`, `secretRef: tatara-grafana`.
- `values/tatara-operator/raw/tatara-grafana.tatara-operator.pre.secrets.yaml` (sops, mirror
  the `infrastructure-grafana` raw secret): `serviceAccountToken` (reuse the existing
  infrastructure Viewer token value) + `webhookSecret` (== `var.tatara_webhook_secret`).
- Widen the operator metrics allowlist (cluster-specific override of the chart default
  `wrapper_,agent_,memory_,ingest_`): `pushMetricsAllowedPrefixes:
  "wrapper_,agent_,memory_,ingest_,ccw_,tatara_wrapper_,analyzer_,scip_,semantic_,llm_,push_"`
  on the tatara-operator release values.

No `infra/helmfile` change (Grafana already deployed; unified alerting + prometheus/loki
datasources present). No tatara component-repo code change.

## Secrets and deploy prerequisites

- `tatara-grafana` Secret keys: `serviceAccountToken` (reused infrastructure Viewer SA token,
  read-only), `webhookSecret` (new shared value).
- GitLab CI variables on the terraform repo: `TF_VAR_tatara_webhook_secret`,
  `TF_VAR_infrastructure_webhook_secret` set to match the corresponding Secret `webhookSecret`
  values. (`infrastructure-grafana.webhookSecret` already exists in-cluster; reuse it.)
- These are the only human-gated items; everything else is GitOps (terraform MR + helmfile MR).

## Testing (TDD)

- terraform: `terraform fmt` + `validate` + `plan` (CI). Module loki extension covered by a
  rendered-rule assertion (a Loki rule emits `datasource.type=loki`, `expr` populated,
  `queryType=instant`). A representative `alerts/tatara-*.yaml` asserts label set
  (`system=tatara` present on routed rules, absent on `info`).
- Post-apply verification (verification-before-completion): rule groups `tatara-*` present in
  Grafana (folder Default); temporarily trip one threshold (or `is_paused` toggle on a
  synthetic rule) and confirm a `tatara` `incident` Task + a `brainstorming` issue appears;
  confirm a synthetic `internal_issue_report` produces an alert whose `description` label
  reaches the issue body. Confirm wrapper `ccw_*` series now present in Prometheus after the
  allowlist change (`operator_push_series_dropped_total{reason="reserved_name"}` stops rising).

## Deploy order

1. **tatara-helmfile MR**: enable GrafanaSpec on the tatara Project + `tatara-grafana` sops
   secret + allowlist widen. Apply -> `grafana-mcp-tatara` provisioned, route accepts.
2. **terraform MR**: module Loki extension + `alerts/tatara-*.yaml` + contact-point fixes
   (tatara + infrastructure) + sensitive vars (CI vars set first). CI plan reviewed -> apply
   -> rules live, routing works.
3. **Verify** end-to-end per Testing.

Either MR is safe to land first: until the Project is grafana-enabled the webhook 404s
(alerts fire to email only); until the rules exist nothing routes. No breakage from ordering.

## Out of scope

- Remediation/write actions (grafana-mcp stays `--disable-write`; incident agent files an
  issue only) - per the 2026-06-19 design.
- Per-component chart ConfigMaps / a Grafana alerting sidecar (rejected: terraform is the
  established mechanism).
- Reworking the homelab notification tree beyond adding the (already-present) tatara route
  and fixing the two contact points.
- HMAC webhook auth (bearer is the baseline).

## Risks / notes

- Selector accuracy: `job`/`pod`/`app` label values must be pinned against live Prometheus/Loki
  during implementation (operator/memory/chat/wrapper confirmed as `app=tatara-<svc>` in Loki).
- Fixing the `infrastructure` contact point changes live behavior: homelab criticals routed to
  that contact point will now create incident issues. Accepted by the user.
- Loki `description`-as-label cardinality is bounded by low `internal_issue_report` volume and
  wrapper-side PII scrubbing; revisit if volume grows.
- Known redundancy (rule 4, documented not deferred-silently): once operator loop-health is
  reproduced as Grafana rules, the operator-chart `tatara-loop` PrometheusRule is dead weight -
  it still evaluates in Prometheus but routes to the kube-prom Alertmanager `null` receiver, so
  it cannot double-fire an incident (only Grafana reaches the webhook). It is retained this
  milestone to honor "no tatara component-repo change"; the clean-up (remove the PrometheusRule
  template from the operator chart) is a small follow-up operator-chart change, tracked in the
  operator ROADMAP. Until then the duplication is inert.
