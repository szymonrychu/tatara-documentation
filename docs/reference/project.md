---
title: Project
---

# Project

The `Project` custom resource is the top-level grouping unit in tatara. One Project maps to a single SCM owner (a GitHub organization, a GitLab group, or a personal account), owns the per-project memory stack, and drives all scheduled activity: issue scans, MR reviews, brainstorm cycles, and incident handling.

Every `Repository` CR must reference a Project. Every `Task`, `QueuedEvent`, `Issue`, and `MergeRequest` is born inside a Project.

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
| `maxConcurrentAgents` | `int` | `3` | no | Maximum number of simultaneously admitted **agent pods** for this project. The admission unit is the pod-spawn, not the Task - a Task advancing from one pod-spawning stage to the next consumes a fresh slot. `0` is the full-project pause kill switch: `admit()` short-circuits and no `QueuedEvent` is ever admitted, so no pod (and, for a mint, no Task) is ever created. There is no `Minimum=1`. |
| `agentPodTTLSeconds` | `int` | `3600` | no | Bounds **one pod's** life; the Task persists. On expiry the operator stops admitting new turns, waits for the in-flight turn (bounded by `agent.turnTimeoutSeconds`), submits one final handoff turn, and force-deletes the pod. `Task.status.notes` is never empty after a TTL stop: either the agent wrote a handoff note, or the operator wrote one for it. Minimum `300`. |
| `maxNewTasksPerSweep` | `int` | `5` | no | Caps how many Tasks **one sweep pass** may mint. Minimum `1`. |
| `maxOpenTasks` | `int` | `6` | no | Caps **active** Tasks: every Task whose stage is pod-eligible (not `parked`/`delivered`/`rejected`/`failed`). This is a Task **creation** budget, not the same lever as `maxConcurrentAgents` (a concurrency budget) - a sweep that would exceed it mints nothing that pass. `parked(backlog-sweep)` Tasks do not count: they hold ownership, not work. Minimum `1`. |
| `maxBundleBytes` | `int` | `400000` | no | Hard byte budget for a rendered [context bundle](context-bundle.md#the-byte-budget) (~100k tokens). Oldest comments elide first, behind an explicit marker; no summarization, no model call. Minimum `50000`. |
| `agent` | [`AgentSpec`](#agentspec) | see below | no | Configuration for the claude-code-wrapper agent pods this project spawns. |
| `memory` | [`MemorySpec`](#memoryspec) | see below | no | Size of the per-project memory stack (Postgres + Neo4j). |
| `workspace` | [`WorkspaceSpec`](#workspacespec) | enabled | no | Persistent per-Task `/workspace` and per-project build-cache volumes. Gated ANDed with the chart-level `agentWorkspacePvcEnabled` value, which defaults **off**. |
| `scm` | [`ScmSpec`](#scmspec) | - | no | SCM provider binding, maintainer/reporter allowlists, labels, and cron schedules. |
| `grafana` | [`GrafanaSpec`](#grafanaspec) | disabled | no | Optional Grafana integration for incident-response tasks. |
| `documentation` | [`DocumentationSpec`](#documentationspec) | disabled | no | On-switch and docs-target repo for the nightly documentation agent. Requires `scm.cron.documentation.schedule` to also be set - see [`scm.cron.documentation`](#scmcrondocumentation). |
| `queue` | [`QueueSpec`](#queuespec) | derived | no | Fine-grained admission queue tuning. |
| `tokenBudget` | [`TokenBudgetSpec`](#tokenbudgetspec) | nil (inherits operator default, off) | no | Token-budget admission gate: pauses proactive and/or incident work once usage crosses a percentage threshold. |
| `upgradePolicy` | [`UpgradePolicySpec`](#upgradepolicyspec) | nil (`engine: none`) | no | The resolved policy handed to the dependency-upgrade agent's turn-0 assignment: discovery engine, how far a single Task may jump, and per-level minimum release age. A top-level `spec` sibling, not nested under `scm`. Requires `scm.cron.upgrade.schedule` to also be set - see [`scm.cron.upgrade`](#scmcronupgrade). |

!!! note "`maxConcurrentAgents: 0` fully pauses a project"
    The pause is **not** routed through `QueueCapacity()` (which floors at `3` and would silently un-pause). It is a direct `spec.maxConcurrentAgents == 0` check at the top of `admit()`: every scan, brainstorm, and webhook-triggered event queues but nothing is ever admitted - not even a Task already in flight that needs its next pod. See [Tuning](../operations/tuning.md).

The per-stage deadlines that used to live here as `deployBudgetSeconds` / `deploySingleHopBudgetSeconds` are gone: every stage's exit deadline is a fixed budget measured from `status.stageEnteredAt` (or `podStartedAt`, or `stageWorkStartedAt` for a live pod stage), not a Project-configurable field. See the one clock, one table model on the [Task stage machine](task-stages.md). <!-- stale-ok: deployBudgetSeconds, deploySingleHopBudgetSeconds -->

---

### AgentSpec

Controls every agent pod spawned by this project.

| Field | Type | Default | Description |
|---|---|---|---|
| `model` | `string` | operator default | Claude model ID (e.g. `claude-opus-4-8` project-wide, tiered down per agent kind to `claude-sonnet-5`). When empty the wrapper's own default applies. |
| `image` | `string` | operator default | Fully-qualified container image for the claude-code-wrapper pod. When empty the operator's compiled-in default is used. |
| `permissionMode` | `string` | `bypassPermissions` | Claude Code permission mode. `bypassPermissions` disables interactive approval prompts inside the agent. |
| `maxTurnsPerPod` | `int` | `40` | **Deprecated, zero effect.** Ceiling on agent turns within one pod run used to exist independently of `maxTurnsPerTask`; the field is kept only because helmfile still sets it (removing it is a breaking CRD change reserved for a later `semver:major`). |
| `maxTurnsPerTask` | `int` | `300` | **Deprecated, zero effect.** Used to be the lifetime turn ceiling across every pod of the Task; a turn count measures how much an agent has done, not whether it is stuck, so it no longer parks or fails anything. See [stall detection](../architecture/agent-execution.md#stall-detection-probe-interrupt-stop) and the [residency cap](task-stages.md#the-deadline-invariant) for what replaced it. |
| `maxReviewRounds` | `int` | `3` | **Deprecated, zero effect.** Used to park the Task at `review-loop-exhausted` after this many `request_changes` verdicts; a round count measures conversation length, not convergence, so the `reviewing <-> implementing` cycle is no longer capped by this field. |
| `maxHumanReviewRounds` | `int` | `5` | Un-parks of a `review`-kind Task back to `reviewing` on a human PR comment. At the cap it stays parked at `awaiting-human` - a human's PR is fixed by the human. Still active - unlike `maxReviewRounds` above, this counter was not retired. |
| `maxPodRecreations` | `int` | `3` | **Deprecated, zero effect.** Used to park the Task at `pod-recreation-exhausted` after this many respawns within the current state; repeated pod death is now treated as a crash to *alert* on (`operator_pod_recreations_total`, still counted and labeled by `reason` - see [Runbooks](../operations/runbooks.md#tatara-runbook-operator-agent-pod-recreation-loop)), not a Task to terminate. The [residency cap](task-stages.md#the-deadline-invariant) (24h, hardcoded, not a field) is the only remaining backstop against an endless respawn loop. |
| `turnTimeoutSeconds` | `int` | `1800` | Inactivity window per turn in seconds. **Meaning changed**: this no longer kills the turn. After this many seconds with no agent activity the operator sends a probe (`POST /v1/probe`) instead, waits `stallProbeGraceSeconds` for a reply, retries up to `stallProbeMaxAttempts` times, and only then interrupts the session and runs the ordinary stop-and-handoff sequence. A turn actively producing output is never probed. See [stall detection](../architecture/agent-execution.md#stall-detection-probe-interrupt-stop) in Agent Execution. |
| `stallProbeGraceSeconds` | `int` | `300` | How long the operator waits for a stall probe to be answered before counting the attempt unanswered. The probe is delivered at the agent's next tool-call boundary, so a healthy agent inside one long tool call answers late rather than never. Minimum `60`. |
| `stallProbeMaxAttempts` | `int` | `2` | Unanswered probes before the operator interrupts the session (`POST /v1/interrupt` on the wrapper) and runs the stop-and-handoff sequence. Range `1`-`5`. |
| `effort` | `string` | `xhigh` | Reasoning-effort level forwarded to the wrapper as the `EFFORT` env var. Maps to Claude's extended thinking intensity. One of: `low`, `medium`, `high`, `xhigh`, `max`. |
| `modelByKind` | `map[string]string` | `{}` | Per-**agent-kind** override of `model`, keyed on `Task.status.agentKind` (not the Task origin kind). Valid keys: `brainstorm`, `incident`, `implement`, `review`, `refine`, `documentation`, `upgrade` - seven, `clarify` folded into `implement` at #521. Locked defaults: `brainstorm`/`incident`/`implement`/`review` = `claude-opus-*`; `documentation`/`refine` = `claude-sonnet-*`; `upgrade` has no locked default and falls back to `model` unless set. Values must start with `claude-` (max 64 chars). A missing/empty entry falls back to `model`. |
| `effortByKind` | `map[string]string` | `{}` | Per-agent-kind override of `effort`. Same 7-key set as `modelByKind`. Values must be one of `low`, `medium`, `high`, `xhigh`, `max`. A missing/empty entry falls back to `effort`. |
| `skillsRef` | `string` | `main` | Git ref (branch, tag, or SHA) of the `tatara-agent-skills` repo the wrapper clones at boot. **Hand-managed, not CD-rewritten**: `tatara-agent-skills`' release pipeline only bumps the wrapper image's own baked-in `TATARA_SKILLS_REF` default, never this Project-CR field, so an empty or stale `skillsRef` here silently rides `main` (or an old tag) forever with nothing to flag it - see [tatara-operator#421](https://github.com/szymonrychu/tatara-operator/issues/421). Pin to a released tag (e.g. `v1.5.2`) and bump it by hand; `tatara-helmfile`'s `check_agent_pins()` CI/pre-commit guard fails the build if this or the wrapper image tag is missing or not a `vX.Y.Z` tag - see [tatara-helmfile](../components/helmfile.md#agent-pin-coverage-guard). |
| `hooks` | [`LifecycleHooks`](#lifecyclehooks) | - | Optional shell commands run at fixed points in the session. |
| `extraEnvs` | `[]EnvVar` | - | Additional environment variables appended to the wrapper container after the operator's required variables. A stray extra cannot shadow an operator-required variable. |
| `extraEnvsFrom` | `[]EnvFromSource` | - | ConfigMap or Secret refs whose keys are bulk-loaded into the wrapper container's environment. |
| `extraVolumeMounts` | `[]VolumeMount` | - | Additional volume mounts appended to the wrapper container. |
| `extraVolumes` | `[]Volume` | - | Additional volumes appended to the agent pod's volume list. |
| `extraSidecarContainers` | `[]Container` | - | Additional containers appended after the wrapper in the agent pod. Useful for a local proxy, or for an MCP server that isn't already reachable as a service - for an existing HTTP/SSE MCP endpoint, prefer [`mcpServers`](#mcpserverspec) below. |
| `extraInitContainers` | `[]Container` | - | Init containers added to the agent pod. Run to completion before the wrapper starts. |
| `mcpServers` | [`[]MCPServerSpec`](#mcpserverspec) | `[]` | Additional MCP servers to merge into this project's agent pods, on top of the platform-owned servers and any overlay-dir fragments baked into the image. Serialized to the wrapper as the `TATARA_EXTRA_MCP_SERVERS` env var (compact JSON; omitted entirely when empty). The operator validates shape only - reserved-name enforcement and the merge itself happen in the wrapper, see [tatara-claude-code-wrapper](../components/claude-code-wrapper.md#configuration). |

!!! danger "There is no resume mode"
    `contextWindowTokens` and the old compacted-handover threshold field are gone. <!-- stale-ok: handover --> Every pod's turn-0 gets the identical [context bundle](context-bundle.md) render, bounded by `Project.spec.maxBundleBytes` - there is no partial-resume calculation and nothing carries a Claude session id across a pod boundary. What carries forward between pods is [`Task.status.notes`](task-notes.md).

#### LifecycleHooks

Each field is a shell command string passed to `sh -c`. An empty field is skipped. Hook failures are logged and counted as metrics but never abort the agent session.

| Field | Trigger point | Arguments available |
|---|---|---|
| `preClone` | Before each repository clone | Repo URL as positional arg `$1` |
| `postClone` | After each successful clone and checkout | Clone destination directory as `$1` |
| `conversationStart` | Once, after the agent session boots | Task context from pod env (`TATARA_TASK`, `TATARA_PROJECT`) |
| `conversationRestart` | Each time the wrapper process is relaunched after a pod recreation | Same as `conversationStart` |
| `agentTurnFinished` | After each agent turn (after work is committed and pushed) | Same as `conversationStart` |
| `conversationFinished` | Once, during session teardown | Same as `conversationStart` |

---

#### MCPServerSpec

One entry per additional MCP server to make available to this project's agent pods, alongside the platform-owned servers (`tatara`, `grafana` when enabled, `serena`) and any overlay-dir fragments baked into the image.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `string` | - | **Required.** Server name as it appears in `.mcp.json` and in tool names (`mcp__<name>__*`). Must match the operator's name pattern. A reserved platform name is rejected - that check lives in the wrapper, not here, so a bad value fails open (skipped with a warning) rather than blocking pod boot. |
| `url` | `string` | - | **Required.** MCP server endpoint URL. |
| `type` | `string` | `http` | Transport. One of `http`, `sse`. |

!!! note "Fully generic - no product-specific code in tatara"
    The operator validates shape only; it has no knowledge of what any given MCP server does. The first consumer is the `mtg` Project's in-cluster `spellslinger` MCP service, wired up entirely through this field - see [tatara-helmfile](../components/helmfile.md).

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

### WorkspaceSpec

Persistent volumes for the agent pod, replacing the container's writable layer -
which is volatile (destroyed with the pod) and unbounded (no guarantee a node
has room for every repo a project clones and builds). Not a speed feature by
itself: a resumed repo costs more than a fresh shallow clone (~5s vs ~2s). The
win is the separate build-cache volume - a cold `go build` vs. a warm one is a
5-6x difference.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `*bool` | `true` | Tri-state: **`nil` and `true` both mean on** - an operational escape hatch for a bad rollout, not a tuning knob. Set explicitly to `false` to opt out; omitting the field is not an opt-out. |
| `storageClass` | `string` | cluster default | Must be **RWX-capable**. The operator force-deletes and immediately reschedules agent pods (`GracePeriodSeconds: 0`), so an RWO/block-mode class (e.g. Ceph RBD) stalls every respawn in Multi-Attach. `rook-ceph-rwx` is the deployed choice. |
| `size` | `string` | operator default | Size of the per-**Task** workspace PVC, mounted at `/workspace` and `~/.cache/pre-commit`. |
| `cacheEnabled` | `bool` | `true` | Whether to also provision the per-**project** build-cache PVC. |
| `cacheSize` | `string` | operator default | Size of the per-project cache PVC, mounted at `~/.cache/go-build`, `~/go/pkg/mod`, `~/.cache/pip`, `~/.npm`, and `~/.local/share/mise/downloads` (never the baked-in `mise` `installs`/`shims` dirs - mounting over those would shadow the image's own toolchain). Content-addressed (Go action-ID hash, module@version), so cross-Task sharing within a project is safe; size to the project's real build footprint, not a fixed default (a Go-heavy project's `GOCACHE`+`GOMODCACHE` alone can run into several GB, while a Python/Node project's pip/npm footprint is closer to tens of MB). |

Two PVCs, two lifecycles: the per-Task workspace PVC is owned by the Task and
deleted only on a **terminal** outcome, never on a park (a parked Task can
resume, and destroying its workspace would defeat the point). The per-project
cache PVC is owned by the Project and provisioned once, independent of any
single Task.

A fresh CephFS subvolume is `root:root 0755` and the agent runs as a non-root
uid - `AgentFsGroup` plus `AgentFsGroupChangePolicy: OnRootMismatch` on the
operator's own deployment config (not a per-Project field) fix ownership
without a full recursive chown on every mount, which would make the cache a
net loss on a large tree.

The agent pod is not created until every PVC it mounts is **Bound** - an
unprovisionable volume costs zero pods (bounded at a 10-minute timeout, then
the Task parks at `operator-error`) rather than respawning indefinitely.

A `review`-stage pod for an `implement` Task **inherits** the same workspace
(sequential stages of one Task, one pod name - never concurrent writers, so
this is not a sharing hazard by itself). It is a review hazard instead: any
edit a review agent makes there is committed and pushed at turn end into the
very MR it is judging, attributed as if the implementer wrote it. The review
agent is told this explicitly when the project has the workspace enabled.

!!! danger "Structural-schema pruning is silent"
    Setting `workspace` fields before the CRD carrying them has been applied
    to the cluster is dropped with **no error anywhere**. Verify the live CRD
    (`kubectl get crd projects.tatara.dev -o json`) carries the fields you are
    about to set before relying on them.

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

The real on/off switch for the nightly documentation agent, and its docs-target repo. `scm.cron.documentation.schedule` (below) is a separate, required gate - both must be set for the cron to actually fire.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `false` | Master switch. Has no `kubebuilder:default` - do not gate behavior on "unset == false" without checking this field explicitly. |
| `repo` | `string` | - | Git URL of the central documentation repo the agent maintains. Must also be enrolled as a `Repository` CR under this Project so the bot has push access and mkdocs CI runs. |

---

### UpgradePolicySpec

The policy the upgrade agent is handed at turn 0. The operator does not act on it - it renders
it verbatim into the assignment. Every decision it describes (which candidate is eligible, how
far a hop may jump) is the agent's, made against release notes the operator itself never reads.
Nil disables nothing by itself - the real on/off switch is `scm.cron.upgrade.schedule`, below -
but `engine` defaults to `none`, so an unset block behaves as if there were no dependency
manifests to scan.

| Field | Type | Default | Description |
|---|---|---|---|
| `engine` | `string` | `none` | `renovate` runs the Renovate CLI **read-only** inside the pod (`RENOVATE_PLATFORM=local`, `RENOVATE_DRY_RUN=full`) and reads its report as a candidate HINT, never a source of truth. `none` means the agent enumerates candidates itself by reading pins directly - the right choice for a repo with no dependency manifests, lockfiles or image tags at all. Enum `renovate`\|`none`. |
| `majorStrategy` | `string` | `nextHopOnly` | How far one Task may jump. `nextHopOnly` proposes the next eligible release only - the smallest increment above the current pin - and walks a multi-hop chain one deployed Task at a time; the repo's current pin **is** the cursor, no chain state is persisted anywhere. `latest` jumps straight to the newest eligible release. Enum `nextHopOnly`\|`latest`. |
| `minimumReleaseAge` | [`ReleaseAgeSpec`](#releaseagespec) | all zero | Per-level (`major`/`minor`/`patch`) minimum age, in days, a release must have before the agent will propose it. `0` is bleeding edge: take it the moment it publishes - a deliberate, accepted trade (a broken release can reach the cluster), not an oversight. |

#### ReleaseAgeSpec

| Field | Type | Default | Description |
|---|---|---|---|
| `major` | `int` | `0` | Minimum age in days for a major-version release to be eligible. |
| `minor` | `int` | `0` | Minimum age in days for a minor-version release to be eligible. |
| `patch` | `int` | `0` | Minimum age in days for a patch-version release to be eligible. |

See [Upgrade](../workflows/upgrade.md) for the full agent behavior this policy drives.

---

### QueueSpec

Fine-grained control over the in-operator admission queue. Omit this section entirely if the defaults derived from `maxConcurrentAgents` are sufficient.

| Field | Type | Default | Description |
|---|---|---|---|
| `capacity` | `int` | value of `maxConcurrentAgents`, else `3` | Maximum concurrently admitted normal-class pod-spawns. Events above this limit wait in `Queued` state until a slot frees. |
| `alertCapacity` | `int` | `1` | Reserved concurrent slots for alert-class events (incident-agent spawns). Kept separate so a burst of normal-priority work cannot starve incident response. |

!!! note "Queue vs concurrency"
    The queue bounds running concurrency, not the total number of events. Any number of events can be created; they accumulate in `Queued` state and are admitted in `(priority, seq)` order as capacity frees - see the [QueuedEvent lifecycle](index.md#queuedevent-lifecycle).

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
    `mode: claudeSubscription` and the corresponding `status.tokenBudget.fiveHourPercent` / `weeklyPercent` fields exist in the current CRD but are inert until a wrapper snapshot with a future reset time is reported (an unknown or past reset is ignored so the gate can never wedge on a stale snapshot). Neither live Project (`tatara`, `infrastructure`) currently sets a `tokenBudget` block, so the gate is fully disabled fleet-wide today.

---

### ScmSpec

Binds the project to an SCM provider and configures the full set of operational knobs: bot identity, the approval grammar, labels, and cron schedules.

#### Identity

| Field | Type | Default | Required | Description |
|---|---|---|---|---|
| `provider` | `string` | - | **yes** | SCM provider. One of `github` or `gitlab`. |
| `owner` | `string` | - | **yes** | GitHub organization or user name, or GitLab group/user path. All enrolled repositories must live under this owner. |
| `botLogin` | `string` | - | **yes** | SCM username of the bot account. Used to distinguish bot comments from human comments: a comment authored by `botLogin` can never satisfy the approval grammar's maintainer check, and it is dropped at intake before it can re-trigger the Task the bot just acted on (the [mid-flight events](context-bundle.md#mid-flight-events) enqueue filter). |
| `botEmail` | `string` | - | no | Git commit author email for agent commits. When empty the wrapper's default identity applies. |

#### Approval gates

| Field | Type | Default | Description |
|---|---|---|---|
| `maintainerLogins` | `[]string` | `[]` | Trusted human maintainer accounts. The operator's **only** approval path is a maintainer's comment: the `implement` agent judges whether it approves and cites it (a comment id plus a verbatim quote), and the operator independently verifies the citation - see [Approval gates](../operations/security/approval-gates.md#the-approval-grammar) for the full grammar. **Closed by default:** an empty list means no login is a maintainer, so nothing can ever be approved and no issue advances out of `refined`. Overridable per-repository via `RepositorySpec.maintainerLogins`. |
| `reporterLogins` | `[]string` | `[]` | Allowlist of accounts whose issues and issue comments the operator will act on. When non-empty, issues or comments from any account not in this list (and not a bot or maintainer) are silently dropped at intake. Prevents unknown third parties from driving the lifecycle via prompt injection. When empty, any author is accepted. Overridable per-repository via `RepositorySpec.reporterLogins`. |

!!! warning "Prompt-injection defense"
    Set `reporterLogins` in any project exposed to untrusted contributors. Without it, anyone who can open an issue can drive agent behavior.

#### Merge and review policy

| Field | Type | Default | Enum | Description |
|---|---|---|---|---|
| `prReactionScope` | `string` | *(empty)* | `labeledOrMentioned`, `all` | Controls which PRs/MRs trigger bot review. Empty (the default) is the historical open behavior: reviews every open human PR/MR. `labeledOrMentioned` restricts reviews to PRs carrying `triggerLabel` or @-mentioning the bot, so unlabeled/un-mentioned MRs are not re-reviewed every scan cycle. `all` is an explicit synonym for the empty/open default. The default is deliberately not `labeledOrMentioned`: a defaulted value would be indistinguishable from an explicit opt-in, silently gating every project. |

Merging itself has no policy field to set: it is always an operator action, triggered only by an accepted `submit_outcome(verdict=approve)` from a review pod, and no tatara-opened PR ever carries an auto-merge setting. <!-- stale-ok: auto-merge --> See [Merge and deploy](../workflows/merge-and-deploy.md#the-merge-sequence) for the full sequence.

!!! tip "Enable `labeledOrMentioned` to stop repeat re-reviews"
    Both live projects (`tatara`, `infrastructure`) set `prReactionScope: labeledOrMentioned` explicitly. Leaving it empty means every `mrScan` cycle re-reviews every open PR/MR regardless of prior review state.

#### Operational tuning

| Field | Type | Default | Description |
|---|---|---|---|
| `guidance` | `string` | - | Free-form project charter text appended verbatim to the brainstorm goal context. Use to steer agent proposals toward project-specific priorities. |

Per-stage stall detection is no longer a Project-configurable minute count. Every stage runs a fixed budget from `status.stageEnteredAt` / `status.podStartedAt` / `status.stageWorkStartedAt` - see the [Task stage machine](task-stages.md) for the full table.

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
| `targetOpenProposals` | `int` | `3` | The backlog TARGET: how many proposals the operator keeps open and awaiting a maintainer decision across all repos in the project. It refills toward this level and never closes a proposal to reconcile downward. `0` disables refill. |
| `maxOpenProposals` | `int` | `5` | **Deprecated.** The pre-target ceiling, retained as an alias: honoured as the target only when `targetOpenProposals` is unset, so an unmigrated `Project` keeps working. Set `targetOpenProposals` instead. |
| `historyWindow` | `int` | `20` | How many recent brainstorm proposals are rendered into the session's turn-0 prompt as the `<proposal_history>` block, with their outcome and maintainer comments. `0` omits the block. |
| `maxConsecutiveSkips` | `int` | `3` | Circuit-breaker threshold. After this many consecutive sessions ending in `action: skip`, the event-driven refill path is suppressed until a cron tick resets the counter. `0` disables the breaker. |
| `staleProposalDays` | `int` | `0` (reaper off) | Opts in the staleness reaper: a positive value auto-closes bot-authored proposals with no human engagement (no human comment, no live work) for at least that many days, clearing dead proposals out of the `targetOpenProposals` backlog. `<=0` (the unset default) disables the reaper entirely. This is an explicit opt-in sentinel, not a kubebuilder default - "unset" must never be indistinguishable from an active value. |
| `sources` | `[]string` | - | Knowledge sources the brainstorm agent may consult. Allowed values: `docs`, `memory`, `internet`. An empty list uses only repository contents. |

!!! note "One brainstorm per project per cycle"
    `maxPerCycle` is deprecated and ignored. The controller hard-caps brainstorm at one task per project per cycle.

!!! note "`scm.cron.healthCheck` retired" <!-- stale-ok: healthCheck -->
    The retired origin kind's own cron block (`scm.cron.healthCheck`) is dropped along with it; there is no independent health-check schedule any more. <!-- stale-ok: healthCheck --> `maxOpenProposals` and the `BrainstormActivity`/`HealthCheckActivity` shapes it is declared on both remain live in the API - only the cron trigger and the origin kind are gone. <!-- stale-ok: healthCheck -->

### `scm.cron.documentation`

Schedule-driven. **One nightly batch Task per project**, covering everything delivered in the last 24 hours - not a per-delivery spawn, and not a "did anything meaningful change?" judgment call. This `CronActivity` has no `enabled` field of its own; the real on-switch is the top-level [`spec.documentation`](#documentationspec) block (`enabled` + `repo`). Both `spec.documentation.enabled` and a non-empty `schedule` here are required for the cron to fire.

| Field | Type | Default | Description |
|---|---|---|---|
| `schedule` | `string` | - | 5-field cron expression. Empty disables the activity. |
| `maxPerRepo` | `int` | `1` | Maximum in-progress documentation tasks per repo (per-repo lane throttle). |

### `scm.cron.refine`

Pre-step that fires automatically before each scan and brainstorm cycle. No independent schedule; it is a mandatory barrier, not a standalone cron.

| Field | Type | Default | Description |
|---|---|---|---|
| `closedLookbackDays` | `int` | `30` | How far back (in days) closed issues are loaded for already-implemented detection. Zero uses the default of 30. |

### `scm.cron.upgrade`

The dependency-upgrade cron. **Default off** (empty `schedule`) for every project: enabling it
lets an agent open merge requests that change deployed versions, so it is opt-in per project in
the enrollment values, alongside [`upgradePolicy`](#upgradepolicyspec).

| Field | Type | Default | Description |
|---|---|---|---|
| `schedule` | `string` | - | 5-field cron expression. Empty disables the activity - matches `refine`'s own-schedule shape, not `documentation`'s enabled-flag shape. |
| `maxOpenUpgrades` | `int` | `1` | Caps the project's concurrent open upgrade lanes: live upgrade Tasks plus enqueued events not yet minted into one. **Set it explicitly in the enrollment values** - a kubebuilder default applies only on write and never retroactively, so raising it later never reaches a Project CR that already exists. Range `1`-`10`. |

Each due tick mints **at most one** upgrade Task, and only while open upgrade lanes sit below
`maxOpenUpgrades`. Throughput is therefore the cron **frequency**, not a fan-out:
`58 */4 * * *` yields up to six Tasks a day, each taking exactly one dependency-upgrade unit.
Minting several Tasks per fire was rejected - each would self-scan and race for the same top
candidate, and there is no agent-side task-minting tool left to partition the work with.

---

## Label set

The operator projects a set of SCM labels onto issues to communicate the platform's decision state. **Every label here is a write-only projection**: the operator writes it when the underlying `Issue.status.status` (or the parked/reaped state) changes, and no label is ever read to derive that status - the sole exception is the internal `tatara-parked` marker, which is read to decide re-mint *cost*, never *authority* (see [Ownership, GC, and admission](../architecture/ownership.md)).

| Field (`scm.*`) | Default value | Semantics |
|---|---|---|
| `brainstormingLabel` | `tatara-brainstorming` | Written while an issue is at `refined` (pre-approval triage/discussion). |
| `approvedLabel` | `tatara-approved` | Written when `Issue.status.status` becomes `approved` - i.e. after the [approval grammar](../operations/security/approval-gates.md#the-approval-grammar) accepts a maintainer comment. Never itself read to grant approval. <!-- stale-ok: approvedLabel, tatara-approved --> |
| `implementationLabel` | `tatara-implementation` | Written when the Task's running agent becomes `implement`. |
| `declinedLabel` | `tatara-declined` | Written when `Issue.status.status` becomes `rejected`. |
| `incidentLabel` | `tatara-incident` | Issue originated from an incident investigation. Applied additively alongside `brainstormingLabel`; never swept by the phase-label reconciler. |
| `priorityLabel` | *(empty)* | Optional priority tag. When set, the operator applies it to high-priority tasks. |

!!! note "Removed: `approvalLabel`, `ideaLabel`, `rejectedLabel`" <!-- stale-ok: approvalLabel, ideaLabel, rejectedLabel -->
    These three fields are removed from the CRD outright - they configured the old label-applies-approval trigger, and approval is comment-text-only now (see [Approval gates](#approval-gates)). <!-- stale-ok: approvalLabel, ideaLabel, rejectedLabel -->

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
| `lastRefine` | Refine pre-step |
| `lastCDScan` | RETIRED - there is no independent deploy-supervision backstop cron any more; every stage's stall detection is the fixed per-stage clock on the [Task stage machine](task-stages.md). Read-only, kept only for back-compat round-trip of stored Projects; no writer sets it any more. |
| `lastHealthCheck` | RETIRED - `healthCheck` no longer fires. <!-- stale-ok: healthCheck --> Read-only, kept only for back-compat round-trip of stored Projects; no writer sets it any more. |

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
  maxConcurrentAgents: 5
  # (10)!
  agentPodTTLSeconds: 3600
  maxNewTasksPerSweep: 5
  maxOpenTasks: 6
  maxBundleBytes: 400000

  agent:
    # (2)!
    model: claude-opus-4-8
    permissionMode: bypassPermissions
    turnTimeoutSeconds: 1800
    effort: high
    # (11)!
    maxTurnsPerPod: 40
    maxTurnsPerTask: 300
    maxReviewRounds: 3
    maxHumanReviewRounds: 5
    maxPodRecreations: 3
    modelByKind:
      documentation: claude-sonnet-5
      refine: claude-sonnet-5
    effortByKind:
      documentation: low
      refine: medium
    skillsRef: v1.5.2
    hooks:
      # (3)!
      postClone: "mise install --quiet"
      conversationFinished: |
        echo "Task ${TATARA_TASK} finished in project ${TATARA_PROJECT}"
    extraEnvs:
      - name: CUSTOM_REGISTRY
        value: harbor.example.com
    # (15)!
    mcpServers:
      - name: internal-search
        url: http://internal-search.svc.cluster.local:8080/mcp
        type: http

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

  # (14)!
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
        targetOpenProposals: 3
        historyWindow: 20
        # (13)!
        staleProposalDays: 14
        sources:
          - memory
          - docs
      documentation:
        schedule: "0 2 * * *"
        maxPerRepo: 1
      refine:
        closedLookbackDays: 14
```

1. `scmSecretRef` is the only required field. The `Secret` must exist in the same namespace and contain a `token` key with the bot PAT.
2. Agent defaults are production-ready out of the box. Override `model` and `image` to pin specific versions.
3. Hooks run via `sh -c`. A non-zero exit is logged and counted but never aborts the session.
4. Set `pgInstances: 3` for HA. A single instance is acceptable for non-critical projects but is vulnerable to crash-recovery wedges on CephFS-backed storage.
5. Grafana integration provisions a `grafana-mcp` sidecar for incident-response tasks. The `secretRef` Secret must contain `serviceAccountToken` and `webhookSecret` keys.
6. `capacity` overrides the `maxConcurrentAgents` default for queue admission. `alertCapacity` reserves dedicated slots so incident tasks are never starved by a backlog of normal-priority work.
7. `botLogin` must match the SCM account whose token is in `scmSecretRef`. Mismatches cause the operator to misidentify its own comments as human input.
8. `maintainerLogins` + `reporterLogins` form the security perimeter. `maintainerLogins` is not optional hardening here - it is **required** for anything to ever be approved: empty means no login can ever be cited as a maintainer approval, so no issue advances out of `refined`.
9. MR scan every 15 minutes, issue scan hourly, brainstorm weekly on Monday morning, documentation nightly.
10. `agentPodTTLSeconds` bounds one pod's life, not the Task. `maxNewTasksPerSweep` and `maxOpenTasks` are separate Task-minting budgets from the pod-concurrency budget (`maxConcurrentAgents`) above. `maxBundleBytes` is the hard byte cap on every rendered context bundle.
11. `maxTurnsPerPod`, `maxTurnsPerTask`, `maxReviewRounds`, and `maxPodRecreations` are **deprecated with zero effect** - kept only because helmfile still sets them. `maxHumanReviewRounds` is the one survivor of the old budget group and still bounds review re-entry on human PR comments. `modelByKind`/`effortByKind` tier specific **agent** kinds down (here `documentation`/`refine` drop to Sonnet at lower effort) while the project-wide `model`/`effort` fallback stays high-end for everything else. `skillsRef` pins the agent-skills clone to a released tag to avoid `main` drift; it is hand-bumped in every project including `tatara`/`infrastructure` - no pipeline rewrites it - and `tatara-helmfile`'s `check_agent_pins()` guard fails CI if it (or the wrapper image tag) is ever left unpinned.
12. `tokenBudget` is off unless this block is present with `enabled: true`. `customWindow` mode meters absolute tokens against `tokenLimit` inside the cron-anchored `resetSchedule`/`windowDuration` window; `claudeSubscription` mode gates on wrapper-reported Claude usage percentages instead (see [TokenBudgetSpec](#tokenbudgetspec)).
13. `staleProposalDays: 14` opts in the brainstorm staleness reaper: bot proposals with no human engagement for 14+ days are auto-closed, keeping the `targetOpenProposals` backlog from clogging with dead proposals. Omit or set `<=0` to keep the reaper off.
14. `documentation.enabled` + `documentation.repo` is the real on-switch and docs-target repo for the nightly documentation agent; `scm.cron.documentation.schedule` (above) is a separate, also-required gate - the cron `CronActivity` has no `enabled` field of its own.
15. `mcpServers` bring-your-own-MCPs into this project's agent pods, on top of the platform-owned servers. The operator only checks shape (`name` pattern, `type` enum); the wrapper does the actual merge and drops any entry that collides with a reserved platform name.
