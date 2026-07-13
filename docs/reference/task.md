---
title: Task CRD
description: The Task CR - one durable, per-project unit of work carrying an implementation stream from triage to delivery.
---

# Task

A `Task` is the durable, per-project object that carries **one implementation
stream** from the moment it is triaged to the moment it is delivered. It is not
one pod, not one PR, and not one issue. It **owns** the SCM artifacts it spans -
which are separate `Issue` and `MergeRequest` CRs, listed in `status.issueRefs`
and `status.mrRefs` - and it outlives every pod that ever runs for it.

```
apiVersion: tatara.dev/v1alpha1
kind: Task
```

Two enums on this CR are easy to confuse, and the whole model hangs on the
difference between them.

**`spec.kind` is the ORIGIN.** It is immutable, it is baked into the Task's name,
and it says where this stream came from. Six values.

**`status.agentKind` is the AGENT THAT IS RUNNING RIGHT NOW.** It changes as the
Task moves through its stages. Seven values: the six origins, plus `implement`.

`implement` is an **agent kind only**. There is no implement-kind Task. A Task
reaches the `implementing` stage by being approved, never by being minted that
way - which is exactly the point: the approval gate sits between the origin and
the implement pod, and nothing mints its way past it.

| `spec.kind` (origin) | Minted by | First stage |
|---|---|---|
| `brainstorm` | a project cron | `brainstorming` |
| `incident` | a Grafana alert webhook | `investigating` |
| `clarify` | an issue webhook, or the backlog sweep | `clarifying` |
| `refine` | a project cron | `refining` |
| `review` | a PR/MR webhook (always a **human's** PR) | `reviewing` |
| `documentation` | the nightly documentation batch cron | `documenting` |

The Task's name is `<project>-<kind>-<YYYY-MM-DD>-<uid5>`, capped at 49
characters, and its pod is `<task-name>-<agent-kind>`. See
[the stage machine](task-stages.md#pod-naming) for why 49.

!!! note "Operator-managed"
    Tasks are created and fully managed by the operator, from an admitted
    `QueuedEvent`. `status.stage` is written by the **operator only** - no agent
    writes it, and no agent asks for a stage. An agent submits an outcome; the
    operator decides what that outcome means. Direct `kubectl apply` of a Task is
    for debugging, not a normal path.

Progress lives in exactly one field, `status.stage`, and it has fifteen members.
The transition table, the three clocks that bound every stage, the cycle caps and
the closed set of stage reasons are all on their own page:
**[the Task stage machine](task-stages.md)**.

Continuation state lives in exactly one field, `status.notes`, and it is an
append-only journal: **[Task notes](task-notes.md)**.

---

## TaskSpec

```go
type TaskSpec struct {
	ProjectRef string `json:"projectRef"`
	// RepositoryRef is the PRIMARY repo, set ONLY on documentation Tasks.
	// +optional
	RepositoryRef string `json:"repositoryRef,omitempty"`
	// +kubebuilder:validation:MaxLength=16384
	Goal string `json:"goal"`
	// Kind is the ORIGIN. Immutable, baked into the name. NOT the running agent
	// kind (that is Status.AgentKind).
	// +kubebuilder:validation:Enum=brainstorm;incident;clarify;refine;review;documentation
	Kind string `json:"kind"`
	// +optional
	// +kubebuilder:validation:MaxItems=20
	MergeOrder []string `json:"mergeOrder,omitempty"`
	// +optional
	// +kubebuilder:validation:MaxItems=50
	AlertRules []string `json:"alertRules,omitempty"`
	// +optional
	DedupKey string `json:"dedupKey,omitempty"`
	// +optional
	// +kubebuilder:validation:MaxItems=100
	DocumentsTasks []string `json:"documentsTasks,omitempty"`
	// +optional
	MaxTurnsPerTask int `json:"maxTurnsPerTask,omitempty"`
}
```

| Field | Type | Required | Description |
|---|---|:---:|---|
| `projectRef` | string | yes | Parent `Project` CR name |
| `repositoryRef` | string | conditional | The primary repo. Set **only** on documentation Tasks (the docs repo). Every other kind is project-scoped and leaves it empty |
| `goal` | string | yes | The natural-language goal. **Non-evictable**: the byte guard can spill comments and notes, but it can never shrink the goal, so the goal carries a hard cap of its own (`MaxLength=16384`) or it eats the budget the guard is defending |
| `kind` | enum | yes | The origin: `brainstorm`, `incident`, `clarify`, `refine`, `review`, `documentation`. **Immutable** |
| `mergeOrder` | `[]string` | conditional | The sequential, dependency-ordered list of `Repository` CR names whose MRs merge in this order. **Required** - and validated to cover every owned MR's repo - whenever the Task owns MRs in more than one repo. `MaxItems=20` |
| `alertRules` | `[]string` | no | Grafana alert-rule names that triggered an incident Task. `MaxItems=50` |
| `dedupKey` | string | no | The incident **alert-group hash**. Empty on every non-incident Task |
| `documentsTasks` | `[]string` | no | The delivered Tasks this nightly documentation batch covers. `MaxItems=100` |
| `maxTurnsPerTask` | int | no | Per-Task override of the **lifetime** turn backstop across every pod of this Task. Zero means `Project.spec.agent.maxTurnsPerTask` (default 300) |

!!! danger "`mergeOrder` has no lexical default"
    A multi-repo Task without a `mergeOrder` is a validation failure, not a
    lexically-ordered merge. Lexical order across this platform's own repos is
    `agent-skills < cli < claude-code-wrapper < operator` - which merges `cli`
    **before** `operator`, and that is precisely the schema-skew fleet outage this
    redesign exists to prevent. A default that is wrong in the most important case
    is worse than no default.

The old single `spec.maxTurns` is gone, split in two. <!-- stale-ok: maxTurns -->
`maxTurnsPerPod` (default
40) caps **one pod run**, and `maxTurnsPerTask` (default 300) caps the
**lifetime** across every pod. **The `implement` agent kind is exempt from
`maxTurnsPerPod`** - a long, healthy coding run must not be cut off - and the
lifetime cap is what bounds that exemption.

---

## TaskStatus

| Field | Type | Description |
|---|---|---|
| `stage` | enum | The 15-member stage. **The only progress field.** Written by the operator only. See [the stage machine](task-stages.md) |
| `stageEnteredAt` | time | Stamped on **every** transition. The clock for the pod-less stages |
| `agentKind` | enum | The agent running now: `brainstorm`, `incident`, `clarify`, `refine`, `review`, `documentation`, `implement` |
| `podName` | string | The current agent pod, `<task-name>-<agent-kind>` |
| `podStartedAt` | time | Stamped when the pod is **created**, and re-stamped on every respawn. It arms the readiness clock, and it is the base of the pod TTL (`podStartedAt + agentPodTTLSeconds`). **Cleared on every stage transition** |
| `stageWorkStartedAt` | time | Stamped when the pod becomes **Ready**. It arms the work clock. **Cleared on every stage transition.** The stage deadline must measure work, not queue wait |
| `notes` | `[]Note` | The append-only journal. **It is the continuation state.** See [Task notes](task-notes.md) |
| `pendingEvents` | `[]TaskEvent` | Mid-flight SCM events awaiting the next turn boundary. See [below](#mid-flight-events) |
| `stats` | [TaskStats](#taskstats) | Tokens, turns, pods, artifacts |
| `deliveredAt` | time | When the Task reached `delivered`. The reaper's 48h clock runs from here |
| `documentedBy` | string | The nightly documentation batch Task that covered this delivered Task. Empty until a batch covers it, and **permanently empty** for a Task that shipped no code |
| `issueRefs` | `[]string` | The `Issue` CRs this Task owns. `MaxItems=50` |
| `mrRefs` | `[]string` | The `MergeRequest` CRs this Task owns. `MaxItems=50` |
| `stageReason` | string | The machine reason for the current stage. **Mandatory** on `parked`, `failed` and `rejected`. Closed set: see [stage reasons](task-stages.md#stage-reasons) |
| `parkedFromStage` | string | **Observability only.** The un-park target is **never** derived from it - it is re-derived from `Issue.status.status` and the owned-MR state |
| `mergeCursor` | int | How far the sequential merge got through `spec.mergeOrder`. Persisted, so a restarted operator resumes and never re-merges |
| `mergeReentries` | int | Bounds the `merging` re-entry cycle. Cap 3, then `failed(merge-blocked)` |
| `deployReentries` | int | Bounds the `deploying` re-entry cycle. Cap 3, then `failed(deploy-blocked)` |
| `headMoveReentries` | int | Bounds the `reviewing` / `merging` moved-head cycle. Cap 3, then `failed(head-moving)`. **This one spawns a review pod every lap** |
| `humanReviewRounds` | int | Bounds the un-park cycle of a `review`-kind Task. Cap 5 (`maxHumanReviewRounds`), then it stays parked. **Also spawns a pod every lap** |
| `foldInFlight` | `[]string` | The member Tasks a refine umbrella is mid-adoption of. The reaper **skips** anything named here |
| `resolvedModel` | string | The model resolved for this Task's pod at spawn (`modelByKind` on the **agent** kind, else the project model). Stamped once, so cost is priced by the model that actually ran |
| `shortDescription` | string | One-line description, for the print columns |
| `conditions` | `[]Condition` | Standard Kubernetes conditions |

The four cycle-cap counters above are the Task-side half of the story. The fifth,
`reviewRounds`, lives on the `MergeRequest` - see
[cycle caps](task-stages.md#cycle-caps) for all five in one table.

!!! warning "There is no `deployDeadline`, no `mergeWaitDeadline`, no `reviewResolveDeadline`" <!-- stale-ok: deployDeadline, mergeWaitDeadline, reviewResolveDeadline -->
    The per-edge deadline family is gone, generalised into `stageEnteredAt` plus
    the three-clock model. Three clocks - admission, readiness, work - are armed
    by **which timestamps are set**, and every stage carries a budget. There is no
    per-edge deadline field left to forget on a new edge. See
    [the deadline invariant](task-stages.md#the-deadline-invariant).

### Note

```go
type Note struct {
	At    metav1.Time `json:"at"`
	// Agent is the WRITER. The REST layer stamps it from Status.AgentKind; an
	// agent can NEVER produce "operator".
	// +kubebuilder:validation:Enum=brainstorm;incident;clarify;refine;review;documentation;implement;operator
	Agent string `json:"agent"`
	// +kubebuilder:validation:Enum=note;plan;handoff
	Kind string `json:"kind"`
	// +kubebuilder:validation:MaxLength=4096
	Body string `json:"body"`
}
```

Full semantics on [Task notes](task-notes.md#the-journal).

### TaskEvent

```go
type TaskEvent struct {
	At metav1.Time `json:"at"`
	// +kubebuilder:validation:Enum=issue_comment;mr_comment;mr_review;label;alert
	Kind   string `json:"kind"`
	Repo   string `json:"repo"`   // Repository CR name
	Number int    `json:"number"` // 0 for kind=alert
	Author string `json:"author"`
	// +kubebuilder:validation:MaxLength=4096
	Body string `json:"body"`
}
```

---

## TaskStats

```go
type TaskStats struct {
	TokensInput         int64    `json:"tokensInput,omitempty"`
	TokensOutput        int64    `json:"tokensOutput,omitempty"`
	TokensCacheRead     int64    `json:"tokensCacheRead,omitempty"`
	TokensCacheCreation int64    `json:"tokensCacheCreation,omitempty"`
	Turns               int      `json:"turns,omitempty"`
	PodRuns             int      `json:"podRuns,omitempty"`
	WallSeconds         int64    `json:"wallSeconds,omitempty"`
	AgentsRun           []string `json:"agentsRun,omitempty"`
	IssueCount          int      `json:"issueCount,omitempty"`
	MRCount             int      `json:"mrCount,omitempty"`
	PodRecreations      int      `json:"podRecreations,omitempty"`
	NotesSpilled        int      `json:"notesSpilled,omitempty"`
	NotesSpilledRefs    []string `json:"notesSpilledRefs,omitempty"`
}
```

| Field | Description |
|---|---|
| `tokensInput` / `tokensOutput` / `tokensCacheRead` / `tokensCacheCreation` | Token accounting across every pod of this Task |
| `turns` | **Lifetime** turns across every pod. Checked against `maxTurnsPerTask`; at the cap, `failed(turn-budget-exhausted)` |
| `podRuns` | Pods this Task has run |
| `wallSeconds` | Total agent wall time |
| `agentsRun` | The agent kinds that have run. `MaxItems=50` |
| `issueCount` / `mrCount` | Owned `Issue` / `MergeRequest` counts. Both are print columns |
| `podRecreations` | Pod respawns **within the current stage**. At `maxPodRecreations` (3) the stage goes to `failed(pod-recreation-exhausted)`. **Reset to 0 on every transition** |
| `notesSpilled` | Notes evicted to `tatara-memory` by the byte guard |
| `notesSpilledRefs` | One `track_id` per spill batch. It **accumulates** - a single scalar ref would orphan every earlier batch. Read back with `task_context(notes=all)` |

---

## Print columns

`kubectl get task` shows: **Stage**, **Kind**, **Agent**, **Issues**
(`.status.stats.issueCount`), **MRs** (`.status.stats.mrCount`), **Turns**
(`.status.stats.turns`), **Project** (priority 1), **Description**, **Age**.

`Stage` and `Kind` next to each other is the fastest way to see the thing people
get wrong: a Task named `...-clarify-...` sitting at `stage=implementing` with
`agentKind=implement` is not an anomaly. It is the normal path.

---

## The etcd object budget

A Task is a hot object with unbounded-ish lists on it, and an object that grows
past the API server's ceiling becomes **permanently unwritable**. That is the
worst failure mode in the design - every writer fails, and the Task's Issues stay
pinned open by ownership forever - so it is foreclosed before it can happen.

**Every write is sized before it is issued.** `fitForWrite` marshals the object
and evicts oldest comments and oldest notes until it is under **800,000 bytes** -
half the ~1.5 MiB etcd ceiling.

The headroom is not timidity. `metadata.managedFields` grows unboundedly under
repeated server-side-apply patches on a hot object and is counted against the same
limit, so half the ceiling is reserved for the part of the object the operator
does not control.

The eviction itself is ordered: the spill to `tatara-memory` happens **once**,
outside the retry closure, and a note or comment is dropped **only** on spill
success. Nothing is ever dropped into a hole. The in-cluster trim that follows is
pure and in-memory, so it is safe to re-run on a conflict.

!!! danger "A count cap is not a byte cap"
    "409 when there are 200 notes" is a count cap. 200 notes of 4 KB is 800 KB;
    200 notes of 40 KB is 8 MB. Only bytes are bytes. And a 413 is **not** retried
    by `RetryOnConflict`, so the failure is silent, total, and permanent. That is
    why the guard is byte-exact and runs before the write, not a `MaxItems` marker
    hoping for the best.

When the guard cannot win - the object exceeds the budget with nothing left to
evict - the Task fails **loudly** with `stageReason=object-too-large`, written
through a minimal status patch that carries none of the oversized lists and
therefore cannot itself 413. Where it lands depends on the stage: the seven
**pod-spawning** stages route to `parked(object-too-large)` (it is one of the
common `podStageEdges` exits every pod stage carries, alongside
`admission-starved` and `stage-deadline`), while the four **pod-less** stages
(`triaging`, `approved`, `merging`, `deploying`) route to
`failed(object-too-large)`. A note write is never rejected on a count cap (an
agent must always be able to write its handoff), and a Task that genuinely
cannot fit dies with a recorded reason instead of becoming a silently
unwritable zombie.

---

## Mid-flight events

`status.pendingEvents` is how new SCM activity reaches an agent that is already
running. Events are delivered at the **turn boundary** - never mid-turn - and
render ahead of the context bundle, so the agent reads the delta first and the
refreshed baseline second. If no pod is running, one spawns and they ride in
turn 0.

The list is capped at **20**, drop-oldest, **in Go, before the write**.
`MaxItems=25` is a backstop only: an API-server 422 is not retried by
`RetryOnConflict` and would hot-loop webhook redelivery.

It is cleared by **set-difference inside the retry closure**, keyed on
`(kind, repo, number, at)`, and never by nil-assign - a webhook arriving between
render and clear must not be silently dropped - and only after the wrapper has
accepted the submit.

!!! danger "A bot-authored event is never enqueued"
    The enqueue filter drops any event whose author is
    `Project.spec.scm.botLogin`. This is load-bearing, not hygiene: the operator
    posts a comment on the issue when it parks a Task. Without the filter, that
    comment lands in the Task's own `pendingEvents` and **un-parks the Task the
    operator just parked** - a fully autonomous loop, driven by nothing but the
    platform talking to itself.

---

## Removed fields

Everything below was on the pre-redesign `Task` and is **gone**. If you have
automation, dashboards or `jsonpath` queries reading any of it, this table is the
migration.

| Removed | Where it went |
|---|---|
| `phase`, `lifecycleState` | `status.stage` - one field, fifteen members, no dual-terminal helper <!-- stale-ok: lifecycleState --> |
| `parkReason` | `status.stageReason` (now a closed set, and mandatory on every terminal) <!-- stale-ok: parkReason --> |
| `pendingInterjections`, `pendingComments` | `status.pendingEvents` <!-- stale-ok: pendingInterjections, pendingComments --> |
| `workItems` (and `WorkItemRef`) | the `Issue` and `MergeRequest` CRs, referenced by `status.issueRefs` / `status.mrRefs`. It was an embedded slice and never a CRD <!-- stale-ok: workItems, workItem, WorkItemRef --> |
| `subtasks` (and `SubtaskRef`, and the `Subtask` CRD itself) | `status.notes`, as a `plan` note <!-- stale-ok: subtasks, subtask, SubtaskRef, Subtask --> |
| `sessionID`, `conversationObjectKey`, `handover` | `status.notes`, as a `handoff` note. There is no session resume and no continuation preamble <!-- stale-ok: sessionID, conversationObjectKey, handover --> |
| `prURL`, `prNumber`, `headBranch`, `mergeCommitSHA`, `mergedHeadSHA` | `MergeRequest.status` <!-- stale-ok: prNumber, headBranch, mergeCommitSHA, mergedHeadSHA --> |
| `deployedVersion`, `deployArtifact`, `cascadeStage` | `MergeRequest.status`. There is no cascade state machine any more; `merging` and `deploying` are ordinary stages <!-- stale-ok: deployedVersion, deployArtifact, cascadeStage --> |
| `changeSummary` | `MergeRequest.status.significance`, plus the `submit_outcome` payload <!-- stale-ok: changeSummary --> |
| `reviewVerdict`, `prOutcome`, `issueOutcome`, `implementOutcome`, `brainstormOutcome` | `submit_outcome` - one tool name, one schema per agent kind <!-- stale-ok: reviewVerdict, prOutcome, issueOutcome, implementOutcome, brainstormOutcome --> |
| `turnsCompleted`, `cumulativeTokens`, `lastTurnInputTokens`, `cumulativeInput`, `cumulativeOutput`, `cumulativeCacheRead`, `cumulativeCacheCreation` | `status.stats` <!-- stale-ok: turnsCompleted, cumulativeTokens --> |
| `approvedByMaintainer`, `autoApproved` | `Issue.status.approval` (single-use `ApprovalEvidence`). Approval is comment **text**, matched by the operator; labels are write-only <!-- stale-ok: approvedByMaintainer, autoApproved --> |
| `gateEnteredAt`, `lastActivityAt`, `deadlineAt`, `mergeWaitDeadline`, `reviewResolveDeadline`, `deployDeadline` | generalised into `stageEnteredAt` plus the three-clock family. The wedge class they killed stays killed; the guarantee is now total instead of per-edge <!-- stale-ok: gateEnteredAt, lastActivityAt, deadlineAt, mergeWaitDeadline, reviewResolveDeadline, deployDeadline --> |
| `implementContext`, `implementEmptyRetries`, `implementGiveUps`, `writebackSkip4xxAttempts`, `disarmFailures`, `lifecycleIterations` | deleted. The [cycle caps](task-stages.md#cycle-caps) replace the ad-hoc loop-breakers <!-- stale-ok: implementContext, implementGiveUps, writebackSkip4xxAttempts, disarmFailures, lifecycleIterations --> |
| `resultSummary`, `discoveredIssues`, `followupIssueURL`, `linksSyncedURLs`, `linksSyncFailures`, `issueLinks`, `prLinks` | deleted. The `Issue` / `MergeRequest` mirrors and `submit_outcome` cover all of it <!-- stale-ok: resultSummary, discoveredIssues, linksSyncedURLs, linksSyncFailures, issueLinks, prLinks --> |
| spec: `source` (and `TaskSource`), `maxTurns`, `approvalRequired`, `proposedIssue` (and `ProposedIssueSpec`), `reposInScope`, `systemicGroup` (and `SystemicGroup`), `alertRule` | `maxTurns` split into `maxTurnsPerPod` / `maxTurnsPerTask`; `alertRule` became `alertRules`; the rest fold into the `Issue` CR, ownership, and `mergeOrder` <!-- stale-ok: TaskSource, maxTurns, approvalRequired, proposedIssue, ProposedIssueSpec, reposInScope, systemicGroup, SystemicGroup --> |
| kind enum: `implement`, `selfImprove`, `triageIssue`, `healthCheck`, `issueLifecycle` | `implement` survives as an **agent** kind only. The other four are gone: `triageIssue` and the front half of `issueLifecycle` are `clarify`, `healthCheck` is `brainstorm`, and the back half of `issueLifecycle` is the operator's own `merging` / `deploying` stages <!-- stale-ok: selfImprove, triageIssue, healthCheck, issueLifecycle --> |

**`spec.dedupKey` is kept.** Five of the six old dedup mechanisms fold into the
`(repo, number)` natural key of the `Issue` and `MergeRequest` CRs and are
genuinely deleted. The sixth does not: `dedupKey` is the incident **alert-group
hash**, and a firing alert arrives from Grafana with no issue and no PR to key
on. There is no natural key for it to fold into.

The `internal/harness` REST endpoints are also deleted. `QueuedEvent` is **not**
deleted.

---

## Example

```yaml
apiVersion: tatara.dev/v1alpha1
kind: Task
metadata:
  name: tatara-clarify-2026-07-12-m4z8q
  namespace: tatara
spec:
  projectRef: tatara
  kind: clarify
  goal: |
    Resolve tatara-operator#291: the reaper deletes a Task whose pod is
    mid-turn. Clarify scope with the maintainer, then implement.
  mergeOrder:
    - tatara-operator
    - tatara-cli
```

`mergeOrder` is set here because this stream is expected to touch two repos and
the operator merges them in that order, one at a time, each on green CI.

---

## Inspecting a Task

```sh
# Every Task on a project, with Stage / Kind / Agent / Issues / MRs / Turns
kubectl -n tatara get task -l tatara.dev/project=tatara

# Why is it parked?
kubectl -n tatara get task tatara-clarify-2026-07-12-m4z8q \
  -o jsonpath='{.status.stage}{" "}{.status.stageReason}{"\n"}'

# The journal - this is the continuation state, and the first thing to read
kubectl -n tatara get task tatara-clarify-2026-07-12-m4z8q \
  -o jsonpath='{.status.notes}' | jq -r '.[] | "\(.at) \(.agent)/\(.kind): \(.body)"'

# What it owns
kubectl -n tatara get task tatara-clarify-2026-07-12-m4z8q \
  -o jsonpath='{.status.issueRefs}{"\n"}{.status.mrRefs}{"\n"}'

# Stream the current pod's logs
kubectl -n tatara logs \
  "$(kubectl -n tatara get task tatara-clarify-2026-07-12-m4z8q \
      -o jsonpath='{.status.podName}')" \
  -c tatara-claude-code-wrapper -f
```

---

## See also

- [The Task stage machine](task-stages.md) - the fifteen stages, the transition table, the three clocks
- [Task notes](task-notes.md) - the journal
- [Issue](issue.md) and [MergeRequest](merge-request.md) - the SCM mirrors a Task owns
- [Project](project.md) - the levers: `maxConcurrentAgents`, `maxOpenTasks`, `modelByKind`
- [QueuedEvent](queued-event.md) - the admission unit
- [MCP tools](mcp-tools.md) - `submit_outcome`, `task_note`, `task_context`
