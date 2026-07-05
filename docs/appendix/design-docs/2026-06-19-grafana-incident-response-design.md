# Grafana incident-response — Design

Date: 2026-06-19
Repos: tatara-operator (primary) + tatara-claude-code-wrapper (MCP registration)
Status: approved design, pre-plan
Scope: single milestone, end-to-end. Optional per-project feature.

## Problem

Operators want tatara to react to Grafana alerts: receive an alert webhook, spin up an
agent that investigates live via Grafana (dashboards, Prometheus/Loki queries, alert
state), and file a GitHub issue with evidence so the existing brainstorm/lifecycle loop
takes over. Today the operator only ingests SCM webhooks (github/gitlab) and agents only
reach tatara-cli built-in MCP tools (memory/operator/chat) over in-cluster HTTP. There is
no Grafana ingress, no Grafana MCP, and no incident Task kind.

Reactive/remediation actions (the agent changing the live system) are explicitly OUT OF
SCOPE for this deliverable; grafana-mcp runs read-only.

## Approved decisions

- Single spec, single milestone (whole end-to-end).
- Incident agent picks the target repo from its investigation via the existing
  `propose_issue(repo, body)` path (no new repo-mapping mechanism).
- Trigger: `status=firing` only; dedup per alert group (`groupKey`) with a cooldown;
  resolved/other statuses ignored.
- grafana-mcp: ONE per-project operator-provisioned Deployment, read-only
  (`--disable-write`), authenticated to Grafana with a Viewer service-account token that
  the operator manages. Webhook auth baseline = static shared bearer (version-independent);
  HMAC (Grafana >=12) is a later refinement.
- agent -> grafana-mcp hop is unauthenticated in-cluster (consistent with the memory
  services). grafana-mcp env is injected into ALL the project's agent pods when Grafana is
  enabled (read-only, harmless), not incident-only.

## External facts (load-bearing)

- `grafana/mcp-grafana` (official): Go server. Config via env: `GRAFANA_URL`,
  `GRAFANA_SERVICE_ACCOUNT_TOKEN` or `GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE` (file re-read per
  request - fits a mounted/rotated secret). Transports via `-t`: `stdio` | `sse` |
  `streamable-http` (port 8000). `--disable-write` disables all mutating tools (dashboard
  writes, incident/Sift creation, alert-rule mutation, annotations, snapshots) while
  keeping all reads. Default toolsets include search/datasource/prometheus/loki/alerting/
  dashboard/incident/sift. A Grafana **Viewer** service-account token includes
  `datasources:read` + `datasources:query` + `dashboards:read` in OSS, sufficient for
  dashboards, datasource enumeration, Prometheus/Loki queries, alert/incident reads.
  Grafana 9.0+ required for full datasource functionality. Source:
  github.com/grafana/mcp-grafana.
- Grafana unified-alerting webhook (contact point type `webhook`): Alertmanager-compatible
  JSON: top-level `receiver`, `status` (firing|resolved), `alerts[]` (each with `status`,
  `labels`, `annotations`, `startsAt`, `endsAt`, `generatorURL`, `fingerprint`, `values`),
  `groupLabels`, `commonLabels`, `commonAnnotations`, `externalURL`, `groupKey`, `title`,
  `message`, `truncatedAlerts`. Auth options: HTTP Basic, `Authorization` header (default
  scheme Bearer) with a static token, custom headers, TLS; HMAC-SHA256 added in Grafana
  12.0 (May 2025) only. Source: grafana.com webhook-notifier docs.

## Architecture (units + boundaries)

### Unit A - Project CRD: `GrafanaSpec` (api/v1alpha1/project_types.go)

New optional pointer on `ProjectSpec` (sibling of `Scm`, `Memory`):

```go
// GrafanaSpec configures the optional Grafana incident-response feature:
// an operator-provisioned read-only grafana-mcp and an alert-webhook receiver.
type GrafanaSpec struct {
    Enabled         bool   `json:"enabled"`
    // URL is the Grafana base URL the grafana-mcp queries (non-secret).
    URL             string `json:"url"`
    // SecretRef names a Secret holding the Grafana credentials, keys:
    //   serviceAccountToken - Grafana Viewer SA token (mounted into grafana-mcp)
    //   webhookSecret       - static bearer the alert webhook must present
    SecretRef       string `json:"secretRef"`
    // CooldownSeconds is the per-alert-group refire window (default 3600).
    CooldownSeconds int    `json:"cooldownSeconds,omitempty"`
}
```

- `ProjectSpec.Grafana *GrafanaSpec` (`+optional`).
- `ProjectStatus.Grafana *GrafanaStatus` (mcp workload health roll-up, mirrors
  `MemoryStatus`: phase + conditions + endpoint).
- CRD validation: when `Grafana != nil && Grafana.Enabled`, `URL` and `SecretRef` required.
- Regenerate CRD manifest (`make manifests`). Note the operator-CRD-gap memory: new Project
  fields are unsettable until the CRD is `kubectl apply`-ed (Helm skips CRD upgrades) - the
  deploy runbook must include the CRD apply.

### Unit B - grafana-mcp provisioning (operator, mirrors the memory stack)

New `reconcileGrafanaMCP` (new file `internal/controller/project_grafana.go`) + builders
(`internal/grafanamcp/` or a sibling of `internal/memory/`), gated on `Grafana.Enabled`.
Mirrors `project_memory.go` `applyMemoryStack` SSA pattern (FieldOwner `tatara-operator`):

- **Deployment** `grafana-mcp-<project>`: image from operator config (e.g.
  `grafana/mcp-grafana`, pinned tag), args `["-t","streamable-http","--disable-write"]`,
  container port 8000.
  - `GRAFANA_URL` from `Spec.Grafana.URL`.
  - `GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE=/etc/grafana/token` from a projected mount of
    `SecretRef` key `serviceAccountToken`.
  - Standard read-only securityContext (runAsNonRoot, numeric runAsUser - heed the
    runAsNonRoot-needs-numeric-uid incident).
- **Service** `grafana-mcp-<project>` ClusterIP :8000 (name == workload name).
- Health roll-up into `ProjectStatus.Grafana` (phase Pending/Ready/Failed, condition),
  requeue ~10s, mirroring `memoryStackHealth`/`memoryPhase`. Failure is isolated and does
  NOT block scm/memory features.
- Operator-level config: image + namespace via a `grafanamcp.Config` held on the
  reconciler (mirrors `MemoryConfig`), wired in `cmd/manager/wire.go`. The image tag is
  cluster-agnostic-configurable via the helmfile (chart rule 14: nothing cluster-specific
  baked).
- Teardown: when `Grafana == nil` or `!Enabled`, the Deployment/Service are not applied;
  owner-ref GC (Project-owned) removes any previously-applied workload.

### Unit C - agent pod env (internal/agent/pod.go)

In `BuildPod`, when `project.Spec.Grafana != nil && project.Spec.Grafana.Enabled`, add env
`TATARA_GRAFANA_MCP_URL=http://grafana-mcp-<project>.<ns>.svc:8000/mcp` (alongside
`TATARA_MEMORY_URL` etc.). Injected for ALL the project's agent pods (read-only).
Unauthenticated in-cluster hop, consistent with the existing tatara service URLs.

### Unit D - wrapper MCP registration (tatara-claude-code-wrapper)

The wrapper composes `/workspace/.mcp.json` by merging `MCP_BASE_PATH` + overlay dir
(`MergeMCP`, overlay wins) with `enableAllProjectMcpServers=true`. Add: during bootstrap,
when `TATARA_GRAFANA_MCP_URL` is set, contribute an overlay entry

```json
{ "grafana": { "type": "http", "url": "<TATARA_GRAFANA_MCP_URL>" } }
```

so Claude Code connects to grafana-mcp over streamable-http. Implemented in the bootstrap
MCP-merge path (sibling to `RegisterTataraMCP`); when the env is unset, no entry is added
(feature fully off). The exact streamable-http endpoint path (`/mcp`) is confirmed against
the pinned grafana-mcp image during implementation.

### Unit E - webhook receiver (internal/webhook/server.go + a Grafana parser)

New route `POST /operator/webhooks/{project}/grafana` (second `r.Post` in `(*Server).Mount`;
distinct path sidesteps the SCM `scm.Select` header switch and the `proj.Spec.Scm.Provider`
mismatch guard, since Grafana is orthogonal to SCM).

`handleGrafanaAlert`:
1. Project lookup by `{project}` path segment (operator namespace), as today.
2. Require `Grafana != nil && Enabled`, else 404/forbidden.
3. Auth: resolve `webhookSecret` from `Grafana.SecretRef`; constant-time compare against the
   `Authorization: Bearer <token>` header (reuse the constant-time pattern from the GitLab
   token path). Mismatch -> 401.
4. Parse the Grafana alert JSON (new small parser, e.g. `internal/scm/grafana.go` or
   `internal/webhook/grafana.go`: a `GrafanaAlert` struct with `Status`, `GroupKey`,
   `CommonLabels`, `CommonAnnotations`, `ExternalURL`, `Alerts[]{Status,Labels,Annotations,
   StartsAt,GeneratorURL,Fingerprint}`).
5. `status != "firing"` -> 202 ignored (resolved and group `ok` do nothing this milestone).
6. Dedup + cooldown: deterministic Task name `incidentTaskName(project, hash(GroupKey))`
   (mirrors `issueLifecycleTaskName`). Skip-create (200) when:
   - a non-terminal Task with that name exists (in-flight), OR
   - a Task with that name reached terminal within `CooldownSeconds` (refire window).
7. Create a project-scoped `incident` Task; **exempt from `MaxOpenTasks`** (reactive,
   like the SCM webhook path).

### Unit F - incident Task + goal (api + controller)

- New Task Kind `incident` in `task_types.go` enum; add to `projectScopedKinds` (empty
  `RepositoryRef`, like brainstorm). `ValidateTaskSpec` updated.
- The Task carries alert context for the goal: an annotation (e.g.
  `AnnGrafanaAlert`) with a compact rendered block (groupKey, status, commonLabels,
  per-alert labels/annotations, `generatorURL`, `externalURL`). No `Source` SCM ref (it is
  not an SCM work item).
- New goal builder `incidentGoalProject(alertCtx, slugs)` (`projectscan.go`, sibling to
  `brainstormGoalProject`). It instructs the agent to:
  1. Investigate the alert live using the Grafana MCP tools (query the relevant
     Prometheus/Loki datasources, read the firing alert rule via `generatorURL`, inspect
     related dashboards) - read-only.
  2. File exactly one issue via `propose_issue(repo, body)`, choosing the repo (from the
     project slugs listed) that owns the problem based on the evidence. Body = structured
     evidence: the alert, the queries/tools run and their results, the diagnosis, and the
     `generatorURL`/`externalURL` links. The issue lands with the brainstorming label and
     flows into the normal lifecycle.
  3. If grafana-mcp is unreachable, still file an issue with the raw alert (degraded mode),
     noting the MCP was unavailable. A no-op-with-note is allowed if the alert is a
     confirmed false positive (mirrors brainstorm's no-op path).
  4. Do NOT take remediation/write actions (out of scope; grafana-mcp is read-only anyway).
- Task -> Pod path is unchanged (the pod gets `TATARA_GRAFANA_MCP_URL` via Unit C).
- Writeback: the `propose_issue` child reuses the existing `createProposal` path unchanged.
  An incident run that files no proposal completes like a no-op brainstorm (not a failure).

## Data flow

```
Grafana alert (firing)
  -> POST /operator/webhooks/{project}/grafana   (Authorization: Bearer <webhookSecret>)
  -> verify bearer (constant-time) ; status==firing ; dedup(groupKey)+cooldown
  -> create project-scoped `incident` Task (alert ctx in annotation), exempt MaxOpenTasks
  -> agent Pod (env TATARA_GRAFANA_MCP_URL ; wrapper registers grafana http MCP)
  -> agent investigates read-only via grafana-mcp (PromQL/LogQL, dashboards, alert state)
  -> propose_issue(repo, evidence)  [agent-chosen repo]
  -> operator createProposal -> writer.CreateIssue (bot token) -> labeled issue
  -> brainstorm / issue-lifecycle takes over
```

## Error handling

- Bad/missing bearer -> 401. Grafana disabled for project -> 404/forbidden.
- Non-firing status -> 202 ignored. Dedup/cooldown hit -> 200 (no Task).
- grafana-mcp Deployment unhealthy -> `ProjectStatus.Grafana` Failed condition; does not
  block other reconciles; the incident agent runs in degraded mode (files raw-alert issue).
- propose_issue title-dedup (existing `findOpenIssueByTitle`) prevents duplicate incident
  issues across refires within the same wave.

## Testing (TDD)

- api: `GrafanaSpec` validation (URL+SecretRef required when enabled); `incident` in
  projectScopedKinds; CRD manifest regenerated.
- grafana-mcp builders + `reconcileGrafanaMCP`: Deployment args include
  `streamable-http`+`--disable-write`, token file env + secret mount, Service :8000;
  health roll-up; disabled -> not applied.
- `BuildPod`: `TATARA_GRAFANA_MCP_URL` present iff `Grafana.Enabled`; absent otherwise.
- webhook: `handleGrafanaAlert` bearer verify (401 on mismatch), firing-only (202 on
  resolved), dedup (in-flight + cooldown), incident Task created project-scoped + exempt
  from MaxOpenTasks; Grafana-disabled project rejected.
- goal: `incidentGoalProject` embeds the alert context + instructs grafana-mcp use +
  propose_issue + read-only/no-remediation (string asserts).
- wrapper: bootstrap adds the grafana http overlay entry iff `TATARA_GRAFANA_MCP_URL` set;
  merged `.mcp.json` contains the entry; absent when env unset; tatara entry preserved.
- Full envtest controller + webhook + agent suites green; wrapper bootstrap suite green.

## Deploy

Cross-repo, dependency-ordered:
1. tatara-claude-code-wrapper: merge overlay registration -> wrapper image.
2. tatara-operator: merge CRD + provisioning + webhook + incident Task/goal + pod env ->
   operator image. `kubectl apply` the regenerated CRD (Helm skips CRD upgrades).
3. tatara-helmfile: bump operator chart version + `image.tag`, bump Project `agent.image`
   to the new wrapper, add grafana-mcp image config, and per-project `GrafanaSpec` + the new
   Grafana Secret (sops: `serviceAccountToken`, `webhookSecret`). Diff -> apply.
4. In Grafana: create a Viewer service account + token, and a `webhook` contact point
   pointing at `/operator/webhooks/{project}/grafana` with `Authorization: Bearer
   <webhookSecret>`.

The feature is fully inert until a Project sets `Grafana.Enabled` (no CRD field set -> no
provisioning, no env, no route effect, no wrapper overlay).

## Out of scope

- Reactive/remediation write actions of any kind (grafana-mcp runs `--disable-write`; no
  write MCP; agent goal forbids remediation). Planned as a later, separately-designed
  capability.
- HMAC-SHA256 webhook auth (Grafana >=12). Bearer is the baseline; HMAC is a later
  refinement behind the same `webhookSecret`.
- Resolved-alert issue closing/commenting. Resolved alerts are ignored this milestone.
- Per-incident-only MCP scoping and an authenticated agent->grafana-mcp hop (decided
  against for simplicity; revisit if least-privilege requires it).
