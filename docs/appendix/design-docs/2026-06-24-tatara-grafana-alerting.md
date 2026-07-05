# tatara Grafana alerting -> emergency-brainstorm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Define Grafana-managed alerts (metric + Loki) for every tatara component, labelled `system=tatara`, so they route through the existing operator incident webhook into an emergency-brainstorm that files a `brainstorming` GitHub issue.

**Architecture:** All-Grafana-managed alerting via terraform (`infra/terraform/grafana`): a small Loki module extension + per-component `alerts/tatara-*.yaml` rule groups + a fix to the prewired-but-broken `Tatara`/`Critical` contact points (URL `/grafana` suffix + Bearer). Delivery wiring in `tatara-helmfile`: enable `GrafanaSpec` on the tatara Project, a `tatara-grafana` sops secret (reusing the existing infra Viewer SA token), and a widened `pushMetricsAllowedPrefixes`. No tatara component-repo code.

**Tech Stack:** terraform (grafana provider 4.39.0, S3 state, GitLab-CI apply), helmfile + sops, kube-prometheus-stack (Prometheus + Loki datasources), the live operator `incident` feature (`baa7be0`).

## Global Constraints

- Module schema (`modules/grafana_alert`): rule = `queries:[{expression[, datasource_uid, query_type]}]` + `math_operator` + `threshold` + `for` + `decimal_points` + `annotations` + `labels`. Inline comparisons are split (value in `expression`, comparison in `math_operator`/`threshold`).
- Per-rule `labels` REPLACES the module `default_labels` (no merge). Every routed rule MUST carry `homelab:"true"` + `system:"tatara"` + `component:"<svc>"` + `severity:"warning"|"critical"`. `info` rules carry `homelab`+`component`+`severity` but OMIT `system` (severity gate).
- Datasource uids: prometheus = `prometheus`; loki = `efihqbqlmroqod`.
- `default_no_data_state: "OK"` on every file so dark/absent series never page.
- Writing rules: no em dashes, no smart quotes, straight quotes, plain hyphens.
- terraform `fmt` clean; the full generated+verified file contents live in the generation output `tasks/weaxqrzav.output` and are written verbatim (with `<`/`>`, not HTML-escaped).
- Deploy is GitOps: terraform MR (CI applies) + tatara-helmfile MR (presync hook + GitOps applies). Never `kubectl apply` rules by hand.

## Coverage status (live-verified, honest)

| Component | Rule class | Fires now? | Note |
|---|---|---|---|
| operator | generic + specific | YES | operator is scraped (3 replicas, leader emits business metrics; rules aggregate sum/max) |
| chat | generic + specific | YES (specific counters dark until first event) | scraped; `container="tatara-chat"` pins app pod (excludes CNPG) |
| memory | generic (kube-state-metrics) | YES | crashloop/OOM/not-ready/waiting on `mem-*` pods |
| memory | specific (lightrag/code_graph/ingest/5xx) | **NO - dark** | `mem-*` pods are NOT a Prometheus scrape target; needs a PodMonitor (see Gap 1) |
| wrapper | generic (kube-state-metrics) | YES | `container="wrapper"` pin (agent pods are ephemeral, no `up{}`) |
| wrapper | specific (`ccw_*`) | **NO - dark** until allowlist | fixed by Task 9 (allowlist widen); metrics-dark canary fires today |
| wrapper | Loki internal_issue_report | YES | Loki query verified live (parses, 0 rows now) |
| ingester | Job-state + operator-side | YES (operator_ingest_job_total live) | ingester push metrics best-effort/dark |
| argo | CI failure | YES (broad proxy) | `argo_workflows_count` not scraped; fallback = kube-state Failed-pod count (see Gap 2) |
| operator/memory/chat | Loki error-burst | YES | verified live (operator returned value=3) |

**Gap 1 (memory scrape):** `mem-<project>` pods (operator-provisioned) expose `/metrics` but nothing scrapes them. The 7 memory app-rules are correct and dark-safe; they activate once a PodMonitor/ServiceMonitor scrapes the `mem-*` API pods. Fix = a small tatara-operator change to provision a PodMonitor with the memory stack. **Out of this milestone's scope (operator code); tracked as a follow-up.**
**Gap 2 (argo scrape):** wire the Argo workflow-controller (`:9090/metrics`, `argo_workflows_count`) to a ServiceMonitor, then swap the argo rule to `argo_workflows_count{namespace="tatara",status="Failed"} > 0`. Follow-up.

---

### Task 1: Extend `grafana_alert` module for Loki

**Files:**
- Modify: `~/Documents/infra/terraform/grafana/modules/grafana_alert/main.tf`

**Interfaces:**
- Produces: `query_type: "loki"` support so `alerts/tatara-logs.yaml` renders. `variables.tf` needs no change (`query_type` is already `optional(string,"prometheus")`).

- [ ] **Step 1:** Add `default_loki_model` immediately before `default_math_model = {`:
```hcl
  default_loki_model = {
    refId         = "" # needs to be filled in runtime
    datasource    = {} # needs to be filled in runtime
    expr          = "" # needs to be filled in runtime
    editorMode    = "code"
    instant       = true
    intervalMs    = 1000
    legendFormat  = "__auto"
    maxDataPoints = 43200
    range         = false
    queryType     = "instant"
  }
```
- [ ] **Step 2:** Register loki in `type_to_model`:
```hcl
  type_to_model = {
    "prometheus" = local.default_prometheus_model
    "loki"       = local.default_loki_model
    "math"       = local.default_math_model
  }
```
- [ ] **Step 3:** Populate `expr` for prometheus OR loki (the per-query `model = jsonencode({...})` lookup map):
```hcl
                expr       = contains(["prometheus", "loki"], data.value.query_type) ? data.value.expression : ""
```
- [ ] **Step 4:** `cd ~/Documents/infra/terraform/grafana && terraform fmt && terraform validate`. Expected: `Success! The configuration is valid.`
- [ ] **Step 5:** Commit (`feat: grafana_alert module supports loki query_type`).

Datasource `type`/`uid` derivation is untouched (for loki it yields `type="loki"`, `uid=<datasource_uid>` because `query_type != "math"`). prometheus/math paths are byte-for-byte unaffected.

### Task 2: Fix the broken contact points + add bearer vars

**Files:**
- Modify: `~/Documents/infra/terraform/grafana/grafana_config.tf`
- Modify: `~/Documents/infra/terraform/grafana/variables.tf`

**Interfaces:**
- Consumes: `var.tatara_webhook_secret`, `var.infrastructure_webhook_secret` (set via `TF_VAR_*` GitLab CI variables at apply).
- Produces: `Tatara`/`Critical` webhooks now POST to `.../{project}/grafana` with `Authorization: Bearer`.

- [ ] **Step 1:** In `grafana_config.tf`, fix the `critical` webhook (-> infrastructure) and the `tatara` webhook: append `/grafana` to each `url`, add `authorization_scheme = "Bearer"` + `authorization_credentials = var.<project>_webhook_secret`. (Exact old->new in `tasks/weaxqrzav.output`, contact-point result.)
- [ ] **Step 2:** In `variables.tf`, append two `sensitive` string variables `tatara_webhook_secret`, `infrastructure_webhook_secret` (no default).
- [ ] **Step 3:** `terraform fmt && terraform validate`. Expected: valid. (`plan` will prompt for the two vars until the CI vars are set - that is expected.)
- [ ] **Step 4:** Do NOT touch the notification policy (the `system=tatara` child route already exists, lines 118-126).
- [ ] **Step 5:** Commit (`fix: grafana tatara/critical contact points -> /grafana + bearer`).

### Tasks 3-9: Per-component alert rule groups

Each task = create one `~/Documents/infra/terraform/grafana/alerts/tatara-<svc>.yaml` with the verified content from `tasks/weaxqrzav.output` (written verbatim, `<`/`>` not escaped). After each: `terraform fmt && terraform validate` (Expected: valid), then `terraform plan` shows the new `grafana_rule_group.rules["tatara-<svc>"]` with its rules. Commit per file (`feat: grafana alerts - tatara <svc>`).

- [ ] **Task 3 - `tatara-operator.yaml`** (component=operator, 13 rules): generic (target down `sum(up{job="tatara-operator"})<1` critical, replica-missing, crashloop, OOM, waiting, not-ready) + specific (reconcile wedged, scan stalled, reconcile/turn-submit error ratios, submit p95>30s, agent-http spike, scm-write errors, memory-stack failed, tasks-inflight>=8, task failures, agent boot crashloop, queue-depth>10). Aggregates sum/max across replicas (business metrics leader-only). Verified names: `operator_reconcile_total`, `tatara_scan_items_total`, `tatara_scan_tasks_created_total`, `operator_turn_submit_total`, `operator_turn_submit_duration_seconds_bucket`, `operator_agent_http_total`, `operator_scm_writes_total`, `operator_memory_stacks{phase}`, `operator_tasks_inflight`, `operator_task_terminal_total`, `operator_queue_depth`, `operator_agent_boot_crash_total`.
- [ ] **Task 4 - `tatara-memory.yaml`** (component=memory, generic + 7 specific): generic via kube-state-metrics on selector `pod=~"mem-.+",pod!~"mem-.*-(neo4j|pg|lightrag).*"` (no `up{}` - unscraped). Specific (DARK until Gap 1): `http_requests_total` 5xx (status is TEXT), `lightrag_calls_total{result}`/`lightrag_call_duration_seconds_bucket`, `ingest_jobs_total{status}`, `code_graph_query_total{result}`, `code_graph_analytics_dirty_repos`/`code_graph_analytics_runs_total`. One info rule (ingest item error rate) OMITS `system`.
- [ ] **Task 5 - `tatara-chat.yaml`** (component=chat): generic via `container="tatara-chat"` (NOT `pod=~tatara-chat-.*` which hits CNPG) + `up{job="tatara-chat"}`. Specific: `http_requests_total` 5xx (`or vector(0)` guard), `chat_store_duration_seconds_bucket` p95, `chat_store_errors_total`, `chat_sweeper_errors_total`, `panics_total`. One info rule (`tatara_chat_auth_total` rejection) OMITS `system`.
- [ ] **Task 6 - `tatara-wrapper.yaml`** (component=wrapper): generic via `container="wrapper"` (no `up{}`). Specific (`ccw_commit_push_total`, `ccw_turns_total`, `ccw_http_requests_total`, `ccw_http_panics_total`, `ccw_turn_tokens_total`) are DARK until Task 9. The metrics-dark canary `operator_push_series_dropped_total{reason="reserved_name"}>0` fires today.
- [ ] **Task 7 - `tatara-ingester.yaml`** (component=ingester): Job-shaped, selector `job_name=~".*-ingest-.*"` / `pod=~".*-ingest-.*"` (no `kube_job_labels` on this cluster). Primary signal `operator_ingest_job_total{result="failure"}` (operator-side, always scraped); `kube_job_status_active` stuck>1800s; `ingest_run_result_total` ratio best-effort. All warning, carry `system`.
- [ ] **Task 8 - `tatara-argo.yaml`** (component=argo, 1 rule): `argo_workflows_count` absent live -> fallback `count(kube_pod_status_phase{namespace="tatara",phase="Failed"})>0` 10m, warning. Broad proxy (see Gap 2).
- [ ] **Task 9 - `tatara-logs.yaml`** (Loki, datasource `efihqbqlmroqod`, `query_type:loki`): rule 1 wrapper `internal_issue_report` (``sum by (description,category,severity)(count_over_time({namespace="tatara",app="tatara-claude-code-wrapper"} | pattern `<_> <_> <_> <body>` | line_format `{{.body}}` | json | action="internal_issue_report" [5m]))>0``, `for:"1m"`, annotation surfaces `{{ index $labels "description" }}`); rules 2-4 error-burst for operator/memory/chat (`... | json | level="ERROR" [5m] > 20`). All warning, carry `system`. (Use `for:"1m"` not `"0m"` - module may reject 0m.)

### Task 9b (tatara-helmfile): enable Project + secret + allowlist

**Files:**
- Modify: `tatara-helmfile/values/project-tatara/common.yaml` (add `grafana:` block, sibling of `agent`/`memory`, mirroring infrastructure)
- Create: `tatara-helmfile/values/tatara-operator/raw/tatara-grafana.tatara-operator.pre.secrets.yaml` (sops-encrypted; keys `serviceAccountToken` = reused infra Viewer token, `webhookSecret` = `TF_VAR_tatara_webhook_secret`)
- Modify: `tatara-helmfile/values/tatara-operator/default.yaml` (append `pushMetricsAllowedPrefixes: "wrapper_,agent_,memory_,ingest_,ccw_,tatara_wrapper_,analyzer_,scip_,semantic_,llm_,push_"`)

- [ ] **Step 1:** Apply the three edits (exact content in `tasks/weaxqrzav.output`, MULTI result).
- [ ] **Step 2:** sops-encrypt the new secret via the `sops-secret-helper` skill (decrypt `infrastructure-grafana.*.secrets.yaml`, copy `serviceAccountToken` verbatim; set `webhookSecret`). Confirm the operator presync hook glob (`*.tatara-operator.pre.secrets.yaml`) covers it.
- [ ] **Step 3:** Confirm the `tatara-project` chart templates `spec.grafana` (infrastructure proves the CRD field; verify the chart passthrough for project-tatara).
- [ ] **Step 4:** `helmfile -e <env> -l name=tatara-project diff` and `-l name=tatara-operator diff`. Expected: the grafana spec added, the secret applied, the prefix env changed. Commit.

### Task 10: Deploy + verify (GitOps)

- [ ] **Step 1:** Set GitLab CI vars on the terraform repo: `TF_VAR_tatara_webhook_secret`, `TF_VAR_infrastructure_webhook_secret` == the `webhookSecret` in `tatara-grafana` / `infrastructure-grafana`.
- [ ] **Step 2:** Open the **tatara-helmfile MR** (Task 9b). Review diff, merge -> presync applies the secret + GrafanaSpec; confirm `grafana-mcp-tatara` Deployment becomes Ready and `kubectl -n tatara get project tatara -o jsonpath='{.spec.grafana}'` is populated.
- [ ] **Step 3:** Open the **terraform MR** (Tasks 1-9). Review CI `plan` (new `tatara-*` rule groups + the two contact-point updates), merge -> CI applies.
- [ ] **Step 4 (verify):** In Grafana, confirm `tatara-*` rule groups load (folder Default) with no `expr` reformat on the loki rule. Trip one threshold (or pause/lower a synthetic rule) and confirm a `tatara` `incident` Task + a `brainstorming` issue appears. Confirm the metrics-dark canary fired and, post-allowlist, `operator_push_series_dropped_total{reason="reserved_name"}` stops rising and `ccw_*` series appear in Prometheus. Confirm a synthetic `internal_issue_report` log produces an alert whose `description` reaches the issue body.
- [ ] **Step 5:** Update `MEMORY.md` (the broken-contact-point root cause, the leader-only-metrics aggregation, the memory/argo scrape gaps) and `ROADMAP.md` (mark shipped; add Gap 1/Gap 2 follow-ups).

## Self-review notes

- Spec coverage: every spec unit maps to a task (A->T1, D->T2, B->T3-9, E->T9b, deploy->T10). Severity gate (info omits `system`) enforced in T4/T5.
- Known deviations from spec, all live-verified: memory uses kube-state-metrics generic (unscraped, no `up{}`); wrapper omits `up{}` target-down; argo uses a fallback proxy; `for:"1m"` not `"0m"`. All captured in Coverage status + Gaps.
- Human-gated: the two `TF_VAR_*` CI vars + the sops secret (reused infra token). Everything else GitOps.
