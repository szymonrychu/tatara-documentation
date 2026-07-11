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
| `documentation` | [`DocumentationSpec`](#documentationspec) | disabled | no | On-switch and docs-target repo for the post-merge documentation agent. Requires `scm.cron.documentation.schedule` to also be set - see [`scm.cron.documentation`](#scmcrondocumentation). |
| `queue` | [`QueueSpec`](#queuespec) | derived | no | Fine-grained admission queue tuning. |
| `tokenBudget` | [`TokenBudgetSpec`](#tokenbudgetspec) | nil (inherits operator default, off) | no | Token-budget admission gate: pauses proactive and/or incident work once usage crosses a percentage threshold. |
| `deployBudgetSeconds` | `int` | `3300` | no | Deploying-phase deadline (seconds) for a push-CD cascade along the longest path to a `tatara-helmfile` apply (2 tag-cut hops, e.g. cli -> wrapper -> helmfile). Exceeding it parks the Task recoverable with reason `deploy-timeout`. |
| `deploySingleHopBudgetSeconds` | `int` | `2100` | no | Tighter Deploying-phase deadline for artifacts one hop from `tatara-helmfile` (operator, memory, ingester, chat) with no intermediate parent rebuild. Deploy-supervision picks this over `deployBudgetSeconds` for single-hop artifacts. |

!!! warning "Deprecated: `maxOpenTasks`"
    `maxOpenTasks` is no longer enforced. The queue bounds concurrency, not event creation; events above capacity wait in `Queued` phase. The field is retained for backward compatibility and silently ignored.

!!! note "maxConcurrentTasks: 0 fully pauses a project"
    Setting `maxConcurrentTasks` to `0` admits no new Tasks: every scan, brainstorm, and webhook-triggered event queues but nothing spawns. This is the pause lever for a project (see [Tuning](../operations/tuning.md)).

---

### AgentSpec

Controls every agent pod spawned by this project.

| Field | Type | Default | Description |
|---|---|---|---|
| `model` | `string` | operator default | Claude model ID (e.g. `claude-opus-4-8` project-wide, tiered down per kind to `claude-sonnet-5`). When empty the wrapper's own default applies. |
| `image` | `string` | operator default | Fully-qualified container image for the claude-code-wrapper pod. When empty the operator's compiled-in default is used. |
| `permissionMode` | `string` | `bypassPermissions` | Claude Code permission mode. `bypassPermissions` disables interactive approval prompts inside the agent. |
| `maxTurnsPerTask` | `int` | `50` | Hard ceiling on the number of agent turns per task. The task is failed when this limit is reached. |
| `turnTimeoutSeconds` | `int` | `1800` | Inactivity window per turn in seconds. A turn is killed only after this many seconds of **no streaming output** -- a turn actively producing output is never killed mid-work regardless of wall-clock age. |
| `contextWindowTokens` | `int` | `200000` | Context window budget passed to the wrapper. Controls when the agent initiates a handover. |
| `handoverThresholdPercent` | `int` | `25` | When the last-turn input token count exceeds this percentage of `contextWindowTokens`, the next pod receives a compacted handover text instead of the full conversation replay. Below the threshold the full transcript is replayed. |
| `maxLifecycleIterations` | `int` | `10` (min `3`) | Maximum times a lifecycle task can restart (crash-resume cycles) before the operator marks it terminal. |
| `effort` | `string` | `xhigh` | Reasoning-effort level forwarded to the wrapper as the `EFFORT` env var. Maps to Claude's extended thinking intensity. One of: `low`, `medium`, `high`, `xhigh`, `max`. |
| `maxTaskTokens` | `int64` | `0` (disabled) | Per-Task cumulative output-token ceiling for the otherwise turn-uncapped `implement` kind: a runaway backstop, not a cost lever. `0` disables it. When `status.cumulativeTokens` crosses it the Task fails with reason `TokenBudgetExceeded`. |
| `modelByKind` | `map[string]string` | `{}` | Per-`Task.Spec.Kind` override of `model`. The CRD schema allows up to 11 entries (`MaxProperties=11`), gated by an `XValidation` allow-list covering all 11 keys: `implement`, `review`, `clarify`, `triageIssue`, `brainstorm`, `issueLifecycle`, `incident`, `selfImprove`, `refine`, `healthCheck`, `documentation`. All 11 are schema-valid on new writes, not merely retained for old CRs; the retired kinds (`triageIssue`/`issueLifecycle`/`healthCheck`/`selfImprove`) are simply functionally inert since no new Task ever carries those kinds. Locked defaults: `brainstorm`/`incident`/`clarify`/`implement`/`review` = `claude-opus-*`; `documentation`/`refine` = `claude-sonnet-*`. Values must start with `claude-` (max 64 chars). A missing/empty entry falls back to `model`. |
| `effortByKind` | `map[string]string` | `{}` | Per-kind override of `effort`. Same 11-key allow-list and `MaxProperties=11` as `modelByKind`. Values must be one of `low`, `medium`, `high`, `xhigh`, `max`. A missing/empty entry falls back to `effort`. |
| `skillsRef` | `string` | `main` | Git ref (branch, tag, or SHA) of the `tatara-agent-skills` repo the wrapper clones at boot. Pin to a SHA to avoid drift from `main`. |
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
| `pgStorage` | `string` | `10Gi` | Persistent volume size for each Postgres instance (PGDATA). |
| `pgWalStorage` | `string` | `8Gi` | Persistent volume size for CNPG's dedicated WAL volume, separate from PGDATA. |
| `neo4jStorage` | `string` | `10Gi` | Persistent volume size for Neo4j. |

!!! tip "Production sizing"
    Scale `pgInstances` to `3` to avoid single-node crash-recovery wedges. `pgStorage` is per-instance; total cluster storage is `pgInstances x pgStorage`.

!!! tip "WAL volume sizing"
    WAL lives on its own PVC (`pgWalStorage`) so a WAL burst -- or WAL retained for a
    lagging/re-syncing standby -- cannot fill PGDATA and take writes down. CNPG's
    `max_slot_wal_keep_size` defaults to half the WAL volume, so leave enough headroom
    for a standby resync to complete without crash-looping. Storage sizes are monotonic:
    CNPG's admission webhook rejects any shrink, so only raise these values.

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

### DocumentationSpec

The real on/off switch for the post-merge documentation agent, and its docs-target repo. `scm.cron.documentation.schedule` (below) is a separate, required gate - both must be set for the cron to actually fire.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `false` | Master switch. Has no `kubebuilder:default` - do not gate behavior on "unset == false" without checking this field explicitly. |
| `repo` | `string` | - | Git URL of the central documentation repo the agent maintains. Must also be enrolled as a `Repository` CR under this Project so the bot has push access and mkdocs CI runs. |

---

### QueueSpec

Fine-grained control over the in-operator admission queue. Omit this section entirely if the defaults derived from `maxConcurrentTasks` are sufficient.

| Field | Type | Default | Description |
|---|---|---|---|
| `capacity` | `int` | value of `maxConcurrentTasks`, else `3` | Maximum concurrently admitted normal-class events. Events above this limit wait in `Queued` phase until a slot frees. |
| `alertCapacity` | `int` | `1` | Reserved concurrent slots for alert-class events (incident webhooks from Grafana). Kept separate so a burst of normal tasks cannot starve incident response. |
| `queuedAutonomousCap` | `int` | value of `maxOpenTasks`, else `3` | **Deprecated**, no longer enforced. Retained for CRD backward-compatibility; ignored. |

!!! note "Queue vs concurrency"
    The queue bounds running concurrency, not the total number of events. Any number of events can be created; they accumulate in `Queued` state and are admitted FIFO as capacity frees.

---

### TokenBudgetSpec

Configures the per-Project token-budget admission gate: pauses proactive work (brainstorm, implement, review, ...) at `proactivePercent` and incident work (the alert pool) at `emergencyPercent` of the measured usage window. Off by default at every level: the operator-wide default `Config` is the zero value (`enabled: false`), and a Project inherits that default verbatim unless it sets its own `tokenBudget` block - the presence of the block, not any single field, is what turns the gate on for that Project.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `false` | Turns the gate on for this project. Authoritative once the block is present - it is **not** inherited from the operator-wide default. |
| `mode` | `string` | `customWindow` | `customWindow` meters the operator's own per-turn token accounting against `tokenLimit` within a cron-anchored reset window. `claudeSubscription` gates on the wrapper-reported Claude 5h/weekly usage percentages instead. |
| `proactivePercent` | `int` (0-100) | `50` | Pauses the normal pool at this percentage of the window. |
| `emergencyPercent` | `int` (0-100) | `80` | Pauses the alert pool (incidents) at this percentage. Ordered `>=` `proactivePercent` at evaluation; a lower value is raised to match. |
| `resetSchedule` | `string` | - | 5-field cron marking each window reset boundary. `customWindow` mode only; empty disables the custom window. |
| `windowDuration` | `string` | - | Declared window length as a Go duration (e.g. `"5h"`, `"168h"`). Bounds the reset-boundary search; pair with `resetSchedule`. |
| `tokenLimit` | `int64` | - | Absolute total-token budget per window. `customWindow` mode only. |

!!! info "claudeSubscription mode: present in the API, not yet load-bearing"
    `mode: claudeSubscription` and the corresponding `status.tokenBudget.fiveHourPercent` / `weeklyPercent` fields exist in the current CRD but are inert until a wrapper snapshot with a future reset time is reported (an unknown or past reset is ignored so the gate can never wedge on a stale snapshot). Neither live Project (`tatara`, `infrastructure`) currently sets a `tokenBudget` block, so the gate is fully disabled fleet-wide today. A follow-on per-kind admission gate (fleet-wide account-usage poller + per-kind spawn-ceiling ladder, superseding this per-project snapshot mechanism) is in development on a feature branch and has not merged to `main`; it is not part of this reference until it lands.

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
| `mergePolicy` | `string` | `afterApproval` | `afterApproval`, `autoMergeOnGreenCI` | Merge is performed by the operator-only [deploy supervisor](../workflows/deploy-supervisor.md) - never by an agent - once `review` has applied `tatara-approved` (from a separate pod that cannot approve its own diff) and required checks are green. No agent signals `pr_outcome=merge` in the live kind set. **`mergePolicy` itself has zero effect on this discrete `implement`/`review`/deploy-supervisor flow**: the merge gate (`superviseApprovedPRs`) checks `tatara-approved` + green CI + mergeable state directly and never consults `MergePolicy`. The field is read only by the legacy `issueLifecycle` drain's `handleMerge` path (in-flight pre-redesign Tasks); a new-model Project can leave it at either value with no behavioral difference. |
| `prReactionScope` | `string` | *(empty)* | `labeledOrMentioned`, `all` | Controls which PRs/MRs trigger bot review. Empty (the default) is the historical open behavior: reviews every open human PR/MR. `labeledOrMentioned` restricts reviews to PRs carrying `triggerLabel` or @-mentioning the bot, so unlabeled/un-mentioned MRs are not re-reviewed every scan cycle. `all` is an explicit synonym for the empty/open default. The default is deliberately not `labeledOrMentioned`: a defaulted value would be indistinguishable from an explicit opt-in, silently gating every project. |

!!! tip "Enable `labeledOrMentioned` to stop repeat re-reviews"
    Both live projects (`tatara`, `infrastructure`) set `prReactionScope: labeledOrMentioned` explicitly. Leaving it empty means every `mrScan` cycle re-reviews every open PR/MR regardless of prior review state.

#### Operational tuning

| Field | Type | Default | Description |
|---|---|---|---|
| `guidance` | `string` | - | Free-form project charter text appended verbatim to the brainstorm goal context. Use to steer agent proposals toward project-specific priorities. |
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

### `scm.cron.mrScan`, `scm.cron.issueScan`, and `scm.cron.cdScan`

All three activities share the `CronActivity` shape.

| Field | Type | Default | Description |
|---|---|---|---|
| `schedule` | `string` | - | 5-field cron expression. Empty disables the activity. |
| `maxPerRepo` | `int` | `1` | Maximum in-progress tasks of this type per repository (per-repo lane throttle). A repository whose lane is full is skipped until the in-flight task completes. |

`cdScan` is the push-CD deploy-supervision backstop: it sweeps `Deploying` Tasks whose cascade has stalled past 1.5x the applicable deploy budget (`deployBudgetSeconds` / `deploySingleHopBudgetSeconds`) with no live watcher, and rerolls them - parks recoverable Tasks, re-implements orphans. It is a peer of `mrScan`/`issueScan`, project-scoped. Empty `schedule` disables it.

### `scm.cron.brainstorm`

Opt-in self-driven issue-proposal cycle. Disabled unless `enabled: true`.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `false` | Must be `true` to activate. |
| `schedule` | `string` | - | 5-field cron expression. |
| `maxOpenProposals` | `int` | `5` | If the total number of open, unapproved agent proposals across all repos in the project meets or exceeds this value, the brainstorm cycle is skipped entirely. Controls backlog pressure. |
| `staleProposalDays` | `int` | `0` (reaper off) | Opts in the staleness reaper: a positive value auto-closes bot-authored proposals with no human engagement (no human comment, no live work) for at least that many days, clearing dead proposals out of the `maxOpenProposals` backlog. `<=0` (the unset default) disables the reaper entirely. This is an explicit opt-in sentinel, not a kubebuilder default - "unset" must never be indistinguishable from an active value. |
| `sources` | `[]string` | - | Knowledge sources the brainstorm agent may consult. Allowed values: `docs`, `memory`, `internet`. An empty list uses only repository contents. |

!!! note "One brainstorm per project per cycle"
    `maxPerCycle` is deprecated and ignored. The controller hard-caps brainstorm at one task per project per cycle.

!!! note "`scm.cron.healthCheck` retired"
    `healthCheck` had its own cron block (`scm.cron.healthCheck`); it is retired along with the
    kind. The `HealthCheckActivity` cron is dropped. See the retirement note on
    [the kind taxonomy](index.md#task-kinds-and-scoping).

### `scm.cron.documentation`

Schedule-driven documentation-repo updates. Replaces the previous push-webhook trigger
(`maybeEnqueueDocumentation`, now removed) entirely - `documentation` only ever fires on this
cron, never on a push event. This `CronActivity` has no `enabled` field of its own; the real
on-switch is the top-level [`spec.documentation`](#documentationspec) block (`enabled` + `repo`).
Both `spec.documentation.enabled` and a non-empty `schedule` here are required for the cron to
fire.

| Field | Type | Default | Description |
|---|---|---|---|
| `schedule` | `string` | - | 5-field cron expression. Empty disables the activity. |
| `maxPerRepo` | `int` | `1` | Maximum in-progress documentation tasks per repo (per-repo lane throttle). |

On each tick the agent determines when docs were last updated and what changed since (a
diff-since-last-run across the enrolled repos), and updates the docs repo only if the delta is
non-trivial.

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
| `tokenBudget` | [`TokenBudgetStatus`](#tokenbudgetstatus) | Token-budget accumulator/snapshot (see [`TokenBudgetSpec`](#tokenbudgetspec)). |

### Last-run timestamps

All timestamps are RFC 3339 and reflect the last time the corresponding activity completed successfully.

| Field | Activity |
|---|---|
| `lastMRScan` | MR/PR review scan |
| `lastIssueScan` | Issue scan |
| `lastBrainstorm` | Brainstorm cycle |
| `lastDocumentation` | Documentation cron cycle |
| `lastCDScan` | Push-CD deploy-supervision backstop scan |
| `lastRefine` | Refine pre-step |
| `lastHealthCheck` | RETIRED - `healthCheck` no longer fires. Read-only, kept only for back-compat round-trip of stored Projects; no writer sets it any more. |

### TokenBudgetStatus

| Field | Type | Description |
|---|---|---|
| `windowStart` | `metav1.Time` | When the current custom-window opened (the most recent reset boundary). `customWindow` mode. |
| `windowTokens` | `int64` | Total tokens spent in the current custom window so far. |
| `fiveHourPercent` | `int` (0-100) | Wrapper-reported Claude usage percentage for the rolling 5h window. `claudeSubscription` mode. |
| `fiveHourReset` | `metav1.Time` | Reset time for the 5h window snapshot. A nil or past value means "not reported" and the gate ignores it. |
| `weeklyPercent` | `int` (0-100) | Wrapper-reported Claude usage percentage for the rolling weekly window. `claudeSubscription` mode. |
| `weeklyReset` | `metav1.Time` | Reset time for the weekly window snapshot. Same nil/past-ignored semantics as `fiveHourReset`. |

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
  # (10)!
  deployBudgetSeconds: 3300
  deploySingleHopBudgetSeconds: 2100

  agent:
    # (2)!
    model: claude-opus-4-8
    permissionMode: bypassPermissions
    maxTurnsPerTask: 60
    turnTimeoutSeconds: 1800
    contextWindowTokens: 200000
    handoverThresholdPercent: 25
    maxLifecycleIterations: 10
    effort: high
    # (11)!
    maxTaskTokens: 3000000
    modelByKind:
      documentation: claude-sonnet-5
      refine: claude-sonnet-5
    effortByKind:
      documentation: low
      refine: medium
    skillsRef: 395713a0ef849fde8df5e27121840e043276eccf
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

  # (15)!
  documentation:
    enabled: true
    repo: https://github.com/my-org/my-docs

  queue:
    # (6)!
    capacity: 5
    alertCapacity: 2

  # (12)!
  tokenBudget:
    enabled: true
    mode: customWindow
    proactivePercent: 50
    emergencyPercent: 80
    resetSchedule: "0 0 * * *"
    windowDuration: "24h"
    tokenLimit: 50000000

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
      # (13)!
      cdScan:
        schedule: "*/30 * * * *"
        maxPerRepo: 1
      brainstorm:
        enabled: true
        schedule: "0 9 * * 1"
        maxOpenProposals: 8
        # (14)!
        staleProposalDays: 14
        sources:
          - memory
          - docs
      documentation:
        schedule: "0 10 * * 1"
        maxPerRepo: 1
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
9. MR scan every 15 minutes, issue scan hourly, brainstorm and documentation cron weekly on Monday morning.
10. Deploy budgets bound how long a `Deploying`-phase Task can wait for its push-CD cascade to reach a `tatara-helmfile` apply before parking recoverable. `deployBudgetSeconds` covers the longest 2-hop path (e.g. cli -> wrapper -> helmfile); `deploySingleHopBudgetSeconds` is tighter for single-hop artifacts (operator, memory, ingester, chat).
11. `maxTaskTokens` is a runaway backstop for the turn-uncapped `implement` kind, not a cost lever. `modelByKind`/`effortByKind` tier specific kinds down (here `documentation`/`refine` drop to Sonnet at lower effort) while the project-wide `model`/`effort` fallback stays high-end for everything else. `skillsRef` pins the agent-skills clone to a SHA to avoid `main` drift.
12. `tokenBudget` is off unless this block is present with `enabled: true`. `customWindow` mode meters absolute tokens against `tokenLimit` inside the cron-anchored `resetSchedule`/`windowDuration` window; `claudeSubscription` mode gates on wrapper-reported Claude usage percentages instead (see [TokenBudgetSpec](#tokenbudgetspec)).
13. `cdScan` is the push-CD deploy-supervision backstop: it sweeps stalled `Deploying` Tasks past 1.5x the deploy budget and rerolls them. Empty `schedule` disables it.
14. `staleProposalDays: 14` opts in the brainstorm staleness reaper: bot proposals with no human engagement for 14+ days are auto-closed, keeping the `maxOpenProposals` backlog from clogging with dead proposals. Omit or set `<=0` to keep the reaper off.
15. `documentation.enabled` + `documentation.repo` is the real on-switch and docs-target repo for the post-merge documentation agent; `scm.cron.documentation.schedule` (below) is a separate, also-required gate - the cron `CronActivity` has no `enabled` field of its own.
