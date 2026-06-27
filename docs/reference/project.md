---
title: Project
---

# Project

The `Project` custom resource is the top-level grouping unit in tatara. One Project maps to a single SCM owner (a GitHub organization, a GitLab group, or a personal account), owns the per-project memory stack, and drives all scheduled activity: issue scans, MR reviews, brainstorm cycles, health checks, and incident handling.

Every `Repository` CR must reference a Project. Every `Task` is born inside a Project.

**API group / version:** `tatara.dev/v1alpha1`  
**Kind:** `Project`  
**Scope:** Namespaced

---

## Spec

### Top-level fields

| Field | Type | Default | Required | Description |
|---|---|---|---|---|
| `scmSecretRef` | `string` | - | **yes** | Name of the `Secret` in the same namespace holding the SCM token. Key `token` is the bot PAT or GitLab project access token. |
| `triggerLabel` | `string` | `tatara` | no | Issue label that causes the operator to react. An issue must carry this label (or be authored by the bot) to enter the tatara lifecycle. |
| `maxConcurrentTasks` | `int` | `3` | no | Maximum number of simultaneously running Task pods for this project. Also sets the default `queue.capacity` when `queue` is omitted. |
| `agent` | [`AgentSpec`](#agentspec) | see below | no | Configuration for the claude-code-wrapper agent session that every Task runs. |
| `memory` | [`MemorySpec`](#memoryspec) | see below | no | Size of the per-project memory stack (Postgres + Neo4j). |
| `scm` | [`ScmSpec`](#scmspec) | - | no | SCM provider binding, labels, cron schedules, and merge policy. |
| `grafana` | [`GrafanaSpec`](#grafanaspec) | disabled | no | Optional Grafana integration for incident-response tasks. |
| `queue` | [`QueueSpec`](#queuespec) | derived | no | Fine-grained admission queue tuning. |

!!! warning "Deprecated: `maxOpenTasks`"
    `maxOpenTasks` is no longer enforced. The queue bounds concurrency, not event creation; events above capacity wait in `Queued` phase. The field is retained for backward compatibility and silently ignored.

---

### AgentSpec

Controls every agent pod spawned by this project.

| Field | Type | Default | Description |
|---|---|---|---|
| `model` | `string` | operator default | Claude model ID (e.g. `claude-sonnet-4-6`). When empty the wrapper's own default applies. |
| `image` | `string` | operator default | Fully-qualified container image for the claude-code-wrapper pod. When empty the operator's compiled-in default is used. |
| `permissionMode` | `string` | `bypassPermissions` | Claude Code permission mode. `bypassPermissions` disables interactive approval prompts inside the agent. |
| `maxTurnsPerTask` | `int` | `50` | Hard ceiling on the number of agent turns per task. The task is failed when this limit is reached. |
| `turnTimeoutSeconds` | `int` | `1800` | Inactivity window per turn in seconds. A turn is killed only after this many seconds of **no streaming output** -- a turn actively producing output is never killed mid-work regardless of wall-clock age. |
| `contextWindowTokens` | `int` | `200000` | Context window budget passed to the wrapper. Controls when the agent initiates a handover. |
| `handoverThresholdPercent` | `int` | `25` | When the last-turn input token count exceeds this percentage of `contextWindowTokens`, the next pod receives a compacted handover text instead of the full conversation replay. Below the threshold the full transcript is replayed. |
| `maxLifecycleIterations` | `int` | `10` (min `3`) | Maximum times a lifecycle task can restart (crash-resume cycles) before the operator marks it terminal. |
| `effort` | `string` | `xhigh` | Reasoning-effort level forwarded to the wrapper as the `EFFORT` env var. Maps to Claude's extended thinking intensity. One of: `low`, `medium`, `high`, `xhigh`, `max`. |
| `hooks` | [`LifecycleHooks`](#lifecyclehooks) | - | Optional shell commands run at fixed points in the session. |
| `extraEnvs` | `[]EnvVar` | - | Additional environment variables appended to the wrapper container after the operator's required variables. A stray extra cannot shadow an operator-required variable. |
| `extraEnvsFrom` | `[]EnvFromSource` | - | ConfigMap or Secret refs whose keys are bulk-loaded into the wrapper container's environment. |
| `extraVolumeMounts` | `[]VolumeMount` | - | Additional volume mounts appended to the wrapper container. |
| `extraVolumes` | `[]Volume` | - | Additional volumes appended to the agent pod's volume list. |
| `extraSidecarContainers` | `[]Container` | - | Additional containers appended after the wrapper in the agent pod. Useful for MCP servers or local proxies. |
| `extraInitContainers` | `[]Container` | - | Init containers added to the agent pod. Run to completion before the wrapper starts. |

#### LifecycleHooks

Each field is a shell command string passed to `sh -c`. An empty field is skipped. Hook failures are logged and counted as metrics but never abort the agent session.

| Field | Trigger point | Arguments available |
|---|---|---|
| `preClone` | Before each repository clone | Repo URL as positional arg `$1` |
| `postClone` | After each successful clone and checkout | Clone destination directory as `$1` |
| `conversationStart` | Once, after the agent session boots | Task context from pod env (`TATARA_TASK`, `TATARA_PROJECT`) |
| `conversationRestart` | Each time the session is relaunched after a crash (`--continue` path) | Same as `conversationStart` |
| `agentTurnFinished` | After each agent turn (after work is committed and pushed) | Same as `conversationStart` |
| `conversationFinished` | Once, during session teardown | Same as `conversationStart` |

---

### MemorySpec

Governs the per-project memory stack: a CNPG-managed Postgres cluster (LightRAG backing store) and a Neo4j single-node instance (graph traversal).

| Field | Type | Default | Description |
|---|---|---|---|
| `pgInstances` | `int` | `1` | Number of Postgres instances in the CNPG cluster. Set to `3` for HA. |
| `pgStorage` | `string` | `10Gi` | Persistent volume size for each Postgres instance. |
| `neo4jStorage` | `string` | `10Gi` | Persistent volume size for Neo4j. |

!!! tip "Production sizing"
    Scale `pgInstances` to `3` to avoid single-node crash-recovery wedges. `pgStorage` is per-instance; total cluster storage is `pgInstances x pgStorage`.

---

### GrafanaSpec

Enables an operator-provisioned `grafana-mcp` sidecar and an alert-webhook receiver for incident-response tasks. The feature is entirely inert when `enabled: false`.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `false` | Master switch. Must be `true` for any other field to take effect. |
| `url` | `string` | - | Grafana base URL that `grafana-mcp` queries (non-sensitive). |
| `secretRef` | `string` | - | Name of the `Secret` holding Grafana credentials. Must contain two keys: `serviceAccountToken` (Grafana Viewer SA token mounted into `grafana-mcp`) and `webhookSecret` (static bearer token the alert webhook must present). |

!!! note "Deprecated: `cooldownSeconds`"
    `cooldownSeconds` (default `3600`) is retained for API compatibility but has no effect. The per-alert-group refire window was replaced by admission-time idempotency.

---

### QueueSpec

Fine-grained control over the in-operator admission queue. Omit this section entirely if the defaults derived from `maxConcurrentTasks` are sufficient.

| Field | Type | Default | Description |
|---|---|---|---|
| `capacity` | `int` | value of `maxConcurrentTasks`, else `3` | Maximum concurrently admitted normal-class events. Events above this limit wait in `Queued` phase until a slot frees. |
| `alertCapacity` | `int` | `1` | Reserved concurrent slots for alert-class events (incident webhooks from Grafana). Kept separate so a burst of normal tasks cannot starve incident response. |

!!! note "Queue vs concurrency"
    The queue bounds running concurrency, not the total number of events. Any number of events can be created; they accumulate in `Queued` state and are admitted FIFO as capacity frees.

---

### ScmSpec

Binds the project to an SCM provider and configures the full set of operational knobs: bot identity, approval gates, merge policy, labels, and cron schedules.

#### Identity

| Field | Type | Default | Required | Description |
|---|---|---|---|---|
| `provider` | `string` | - | **yes** | SCM provider. One of `github` or `gitlab`. |
| `owner` | `string` | - | **yes** | GitHub organization or user name, or GitLab group/user path. All enrolled repositories must live under this owner. |
| `botLogin` | `string` | - | **yes** | SCM username of the bot account. Used to distinguish bot comments from human comments and to gate autoapprove. |
| `botEmail` | `string` | - | no | Git commit author email for agent commits. When empty the wrapper's default identity applies. |

#### Approval gates

| Field | Type | Default | Description |
|---|---|---|---|
| `maintainerLogins` | `[]string` | `[]` | Trusted human maintainer accounts. Together with `botLogin` they form the "trusted insider" set for autoapprove. When non-empty, a thread comment is only treated as a human approval if its author appears in this list. When empty, any non-bot human reply releases the self-approve hold. Overridable per-repository via `RepositorySpec.maintainerLogins`. |
| `reporterLogins` | `[]string` | `[]` | Allowlist of accounts whose issues and issue comments the operator will act on. When non-empty, issues or comments from any account not in this list (and not a bot or maintainer) are silently dropped at intake. Prevents unknown third parties from driving the lifecycle via prompt injection. When empty, any author is accepted. Overridable per-repository via `RepositorySpec.reporterLogins`. |

!!! warning "Prompt-injection defense"
    Set `reporterLogins` in any project exposed to untrusted contributors. Without it, anyone who can open an issue can drive agent behavior.

#### Merge and review policy

| Field | Type | Default | Enum | Description |
|---|---|---|---|---|
| `mergePolicy` | `string` | `afterApproval` | `afterApproval`, `autoMergeOnGreenCI` | When `afterApproval`, the operator merges when the agent signals `pr_outcome=merge` (the agent infers human intent from the PR/issue thread; SCM review state is not independently verified). When `autoMergeOnGreenCI`, the bot merges as soon as CI is green. |
| `prReactionScope` | `string` | `labeledOrMentioned` | `labeledOrMentioned`, `all` | Controls which PRs/MRs trigger bot review. `labeledOrMentioned` restricts to PRs carrying the trigger label or explicitly mentioning the bot. `all` reacts to every open PR in enrolled repositories. |

#### Operational tuning

| Field | Type | Default | Description |
|---|---|---|---|
| `guidance` | `string` | - | Free-form project charter text appended verbatim to the brainstorm and healthCheck goal context. Use to steer agent proposals toward project-specific priorities. |
| `babysitDeadlineMinutes` | `int` | `60` | Minutes after task creation that the babysit controller starts checking for stuck tasks. |
| `conversationIdleMinutes` | `int` | `60` | Minutes of inactivity after which the operator considers a conversation stale and triggers recovery. |

#### Board integration

Configure `board` to enable project-board synchronization.

| Field | Type | Default | Description |
|---|---|---|---|
| `board.githubProjectNumber` | `int` | - | GitHub Projects (V2) project number. |
| `board.gitlabBoardId` | `int` | - | GitLab board ID. |
| `board.statusField` | `string` | `Status` | Name of the board field the operator writes task phase into. |

---

## Cron schedules

All cron fields use standard 5-field cron syntax (`minute hour dom month dow`). An empty `schedule` disables that activity.

### `scm.cron.mrScan` and `scm.cron.issueScan`

Both activities share the `CronActivity` shape.

| Field | Type | Default | Description |
|---|---|---|---|
| `schedule` | `string` | - | 5-field cron expression. Empty disables the activity. |
| `maxPerRepo` | `int` | `1` | Maximum in-progress tasks of this type per repository (per-repo lane throttle). A repository whose lane is full is skipped until the in-flight task completes. |

### `scm.cron.brainstorm`

Opt-in self-driven issue-proposal cycle. Disabled unless `enabled: true`.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `false` | Must be `true` to activate. |
| `schedule` | `string` | - | 5-field cron expression. |
| `maxOpenProposals` | `int` | `5` | If the total number of open, unapproved agent proposals across all repos in the project meets or exceeds this value, the brainstorm cycle is skipped entirely. Controls backlog pressure. |
| `sources` | `[]string` | - | Knowledge sources the brainstorm agent may consult. Allowed values: `docs`, `memory`, `internet`. An empty list uses only repository contents. |

!!! note "One brainstorm per project per cycle"
    `maxPerCycle` is deprecated and ignored. The controller hard-caps brainstorm at one task per project per cycle.

### `scm.cron.healthCheck`

Periodic project-health survey. Proposes one targeted discovery issue per cycle via the `tatara-health-check` skill. Disabled unless `enabled: true`.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `false` | Must be `true` to activate. |
| `schedule` | `string` | - | 5-field cron expression. |
| `maxOpenProposals` | `int` | `5` | Same semantics as brainstorm: cycle is skipped when open proposals meet this cap. |
| `sources` | `[]string` | - | Same allowed values as brainstorm: `docs`, `memory`, `internet`. |

### `scm.cron.refine`

Pre-step that fires automatically before each scan and brainstorm cycle. No independent schedule; it is a mandatory barrier, not a standalone cron.

| Field | Type | Default | Description |
|---|---|---|---|
| `closedLookbackDays` | `int` | `30` | How far back (in days) closed issues are loaded for already-implemented detection. Zero uses the default of 30. |

---

## Label set

The operator projects a set of SCM labels onto issues to communicate task phase. All labels are configurable; the table shows the defaults.

| Field (`scm.*`) | Default value | Semantics |
|---|---|---|
| `brainstormingLabel` | `tatara-brainstorming` | Issue is in triage or discussion (pre-approval). Applied by the triage agent. |
| `approvedLabel` | `tatara-approved` | Issue is approved for implementation. Set when a maintainer approves the proposal thread. |
| `implementationLabel` | `tatara-implementation` | Implementation is in flight. Applied when the implement task starts. |
| `declinedLabel` | `tatara-declined` | Issue was declined before implementation (triage reject). |
| `incidentLabel` | `tatara-incident` | Issue originated from an incident investigation. Applied additively alongside `brainstormingLabel`; never swept by the phase-label reconciler. |
| `priorityLabel` | *(empty)* | Optional priority tag. When set, the operator applies it to high-priority tasks. |

!!! warning "Deprecated label fields"
    `approvalLabel`, `ideaLabel`, and `rejectedLabel` are deprecated aliases retained for migration tooling only. Do not set them in new projects; they have no effect on operator behavior.

---

## Status

The operator writes observed state back to `.status`. All fields are read-only.

### Top-level status fields

| Field | Type | Description |
|---|---|---|
| `webhookURL` | `string` | The operator-provisioned inbound webhook URL for this project. Register this URL in your SCM provider to receive push events. Populated after the first reconciliation. |
| `conditions` | `[]Condition` | Standard Kubernetes conditions reflecting overall Project readiness. |
| `memory.phase` | `string` | Observed phase of the memory stack (`Pending`, `Running`, `Degraded`). |
| `memory.endpoint` | `string` | In-cluster HTTP URL of the tatara-memory service for this project. Used by agent pods and the operator. |
| `memory.externalEndpoint` | `string` | External URL of the memory service when exposed outside the cluster (optional). |
| `grafana.phase` | `string` | Observed phase of the grafana-mcp deployment. Empty when `grafana.enabled` is false. |
| `grafana.endpoint` | `string` | In-cluster URL of the grafana-mcp instance. |

### Last-run timestamps

All timestamps are RFC 3339 and reflect the last time the corresponding activity completed successfully.

| Field | Activity |
|---|---|
| `lastMRScan` | MR/PR review scan |
| `lastIssueScan` | Issue scan |
| `lastBrainstorm` | Brainstorm cycle |
| `lastHealthCheck` | Health check cycle |
| `lastRefine` | Refine pre-step |

---

## Annotated example

```yaml
apiVersion: tatara.dev/v1alpha1
kind: Project
metadata:
  name: my-platform
  namespace: tatara
spec:
  # (1)!
  scmSecretRef: my-platform-scm-token
  triggerLabel: tatara
  maxConcurrentTasks: 5

  agent:
    # (2)!
    model: claude-sonnet-4-6
    permissionMode: bypassPermissions
    maxTurnsPerTask: 60
    turnTimeoutSeconds: 1800
    contextWindowTokens: 200000
    handoverThresholdPercent: 25
    maxLifecycleIterations: 10
    effort: xhigh
    hooks:
      # (3)!
      postClone: "mise install --quiet"
      conversationFinished: |
        echo "Task ${TATARA_TASK} finished in project ${TATARA_PROJECT}"
    extraEnvs:
      - name: CUSTOM_REGISTRY
        value: harbor.example.com

  memory:
    # (4)!
    pgInstances: 3
    pgStorage: 20Gi
    neo4jStorage: 20Gi

  grafana:
    # (5)!
    enabled: true
    url: https://grafana.example.com
    secretRef: grafana-tatara-credentials

  queue:
    # (6)!
    capacity: 5
    alertCapacity: 2

  scm:
    provider: github
    owner: my-org
    # (7)!
    botLogin: my-org-bot
    botEmail: my-org-bot@users.noreply.github.com
    # (8)!
    maintainerLogins:
      - alice
      - bob
    reporterLogins:
      - alice
      - bob
      - charlie
    mergePolicy: afterApproval
    prReactionScope: labeledOrMentioned
    guidance: |
      This project runs the platform team's infrastructure repos.
      Prefer Kubernetes-native approaches. Avoid new external dependencies.

    board:
      githubProjectNumber: 42
      statusField: Status

    cron:
      mrScan:
        # (9)!
        schedule: "*/15 * * * *"
        maxPerRepo: 1
      issueScan:
        schedule: "0 * * * *"
        maxPerRepo: 1
      brainstorm:
        enabled: true
        schedule: "0 9 * * 1"
        maxOpenProposals: 8
        sources:
          - memory
          - docs
      healthCheck:
        enabled: true
        schedule: "0 10 * * 1"
        maxOpenProposals: 8
        sources:
          - memory
      refine:
        closedLookbackDays: 14
```

1. `scmSecretRef` is the only required field. The `Secret` must exist in the same namespace and contain a `token` key with the bot PAT.
2. Agent defaults are production-ready out of the box. Override `model` and `image` to pin specific versions.
3. Hooks run via `sh -c`. A non-zero exit is logged and counted but never aborts the session.
4. Set `pgInstances: 3` for HA. A single instance is acceptable for non-critical projects but is vulnerable to crash-recovery wedges on CephFS-backed storage.
5. Grafana integration provisions a `grafana-mcp` sidecar for incident-response tasks. The `secretRef` Secret must contain `serviceAccountToken` and `webhookSecret` keys.
6. `capacity` overrides the `maxConcurrentTasks` default for queue admission. `alertCapacity` reserves dedicated slots so incident tasks are never starved by a backlog of normal tasks.
7. `botLogin` must match the SCM account whose token is in `scmSecretRef`. Mismatches cause the operator to misidentify its own comments as human input.
8. `maintainerLogins` + `reporterLogins` form the security perimeter. At minimum set `maintainerLogins` to restrict who can approve proposals.
9. MR scan every 15 minutes, issue scan hourly, brainstorm and health check weekly on Monday morning.
