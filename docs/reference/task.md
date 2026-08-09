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

**`spec.kind` is the ORIGIN.** It is immutable and baked into the Task's
name.

**`status.agentKind` is the AGENT THAT IS RUNNING RIGHT NOW.** It changes as
the Task moves through its states. Six values.

`clarify` is **gone**, platform-wide, as of the #521 lifecycle redesign - both
as an agent kind *and* as an origin: `spec.kind: implement` is its direct
replacement in `MintIssueTask` (`SweepIssueKind = "implement"`), the same
function that mints a Task from any new issue, whether webhook-delivered or
sweep-discovered. Its three decisions folded into `action` values on the
`implement` outcome (see [Approval Gates](../operations/security/approval-gates.md)),
so the pod that judges approval is the same pod that goes on to write the
code, and the origin kind that starts that conversation is `implement`
itself.

| `spec.kind` (origin) | Minted by | Entry state | State after triage |
|---|---|---|---|
| `brainstorm` | a project cron | `new` | `refined` |
| `incident` | a Grafana alert webhook | `new` | `refined` |
| `implement` | an issue webhook, or the backlog sweep | `new` | `refined` |
| `refine` | a project cron | `new` | `refined` |
| `review` | a PR/MR webhook (always a **human's** PR) | `new` | `awaiting-review` |
| `documentation` | the nightly documentation batch cron | `under-implementation` | - (no triage) |
| `takeover` | a maintainer's hand-over comment on a foreign MR | `refined` | - (no triage) |

Seven `spec.kind` values now, not six. `takeover` is the pre-existing
[maintainer-gated MR hand-over](../workflows/mr-takeover.md) path, unrelated
to the #521 rename.

The Task's name is `<project>-<kind>-<YYYY-MM-DD>-<uid5>`, capped at 49
characters. Its pod's name is computed independently - see
[Pod naming](task-stages.md#pod-naming) for both formats, and for why 49.

!!! note "Operator-managed"
    Tasks are created and fully managed by the operator, from an admitted
    `QueuedEvent`. `status.state` is written by the **operator only** - no agent
    writes it, and no agent asks for a state. An agent submits an outcome; the
    operator decides what that outcome means. Direct `kubectl apply` of a Task is
    for debugging, not a normal path.

Progress lives in exactly one field, `status.state`, and it has eight
members - down from the pre-redesign machine's fifteen `status.stage`
members, with parking and pod-liveness split out into their own orthogonal
properties. The transition table, the three clocks that bound every state,
the cycle caps and the closed set of reasons are all on their own page:
**[the Task state machine](task-stages.md)**.

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
	// +kubebuilder:validation:Enum=brainstorm;incident;implement;refine;review;documentation;takeover
	Kind string `json:"kind,omitempty"`
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
| `kind` | enum | yes | The origin: `brainstorm`, `incident`, `implement`, `refine`, `review`, `documentation`, `takeover`. **Immutable**. `clarify` is gone; `implement` takes over its origin role (see above). `takeover` predates the #521 rename |
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
| `state` | enum | The 8-member state (`new`, `refined`, `under-implementation`, `awaiting-review`, `merged`, `deployed`, `done`, `rejected`). **The only progress field.** Written by the operator only. See [the state machine](task-stages.md) |
| `stateEnteredAt` | time | Stamped on **every** transition. The clock for the operator-driven states |
| `stateReason` | string | The reason on `done` / `rejected` only - mandatory on `rejected`. Closed, disjoint from `parkReason`. See [stage reasons](task-stages.md#stage-reasons) |
| `parkReason` | enum | **Whether the Task is stalled**, orthogonal to `state`: a Task parks *where it is*, not into a fourth state. Empty, or one of 28 closed reasons. See [the park flag](task-stages.md#the-park-flag) |
| `parkedAt` | time | When `parkReason` was set. The base of the park-retention clock (7d, except `backlog-sweep`, which never ages out) |
| `parkedFromState` | string | **Observability only** for most reasons - the un-park target is re-derived from `Issue.status.status` and the owned-MR state, never read back from here - except the `no-outcome` un-park gate, which does require it to be `under-implementation` or `awaiting-review` |
| `agentKind` | enum | The agent running now: `brainstorm`, `incident`, `refine`, `review`, `documentation`, `implement`. Six values - `clarify` is gone |
| `podName` | string | The current agent pod's name. See [Pod naming](task-stages.md#pod-naming) |
| `podStartedAt` | time | Stamped when the pod is **created**, and re-stamped on every respawn. It arms the readiness clock, and it is the base of the pod TTL (`podStartedAt + agentPodTTLSeconds`). **Cleared on every transition** |
| `stateWorkStartedAt` | time | Stamped when the pod becomes **Ready**. It arms the idle/work clock. **Cleared on every transition** |
| `conversationLastEventAt` | time | The idle clock's own anchor for the three **live** states - the latest of the last human comment, pod-ready, or last turn-complete. Re-stamped only by a **human** webhook comment, never by the agent's own activity |
| `notes` | `[]Note` | The append-only journal. **It is the continuation state.** See [Task notes](task-notes.md) |
| `pendingEvents` | `[]TaskEvent` | Mid-flight SCM events awaiting the next turn boundary. See [below](#mid-flight-events) |
| `stats` | [TaskStats](#taskstats) | Tokens, turns, pods, artifacts |
| `deliveredAt` | time | When the Task reached `done`. The reaper's 48h clock runs from here |
| `documentedBy` | string | The nightly documentation batch Task that covered this delivered Task. Empty until a batch covers it, and **permanently empty** for a Task that shipped no code |
| `issueRefs` | `[]string` | The `Issue` CRs this Task owns. `MaxItems=50` |
| `mrRefs` | `[]string` | The `MergeRequest` CRs this Task owns. `MaxItems=50` |
| `mergeCursor` | int | How far the sequential merge got through `spec.mergeOrder`. Persisted, so a restarted operator resumes and never re-merges |
| `mergeReentries` | int | Bounds the `merged` re-entry cycle. Cap 3, then `parked(merge-blocked)` |
| `deployReentries` | int | Bounds the `deployed` re-entry cycle. Cap 3, then `parked(deploy-blocked)` |
| `headMoveReentries` | int | Bounds the `awaiting-review` / `merged` moved-head cycle. Cap 3, then `parked(head-moving)`. **This one spawns a review pod every lap** |
| `ciRedReentries` | int | Bounds the re-implement cycle when live CI on the reviewed head goes red after approval. Cap 3, then `parked(ci-blocked)` |
| `humanReviewRounds` | int | Bounds the un-park cycle of a `review`-kind Task. Cap 5 (`maxHumanReviewRounds`), then it stays parked. **Also spawns a pod every lap** |
| `pinnedPlanNoteId` | string | The plan note's id, pinned at `action=approved` grant. The operator hashes that note's body at grant and re-checks it before code is written - a plan swapped after approval routes back to `refined` (`plan-hash-mismatch`) instead of proceeding |
| `foldInFlight` | `[]string` | The member Tasks a refine umbrella is mid-adoption of. The reaper **skips** anything named here |
| `resolvedModel` | string | The model resolved for this Task's pod at spawn (`modelByKind` on the **agent** kind, else the project model). Stamped once, so cost is priced by the model that actually ran |
| `shortDescription` | string | One-line description, for the print columns |
| `conditions` | `[]Condition` | Standard Kubernetes conditions |

The five cycle-cap counters above are the Task-side half of the story. The
sixth, `reviewRounds`, lives on the `MergeRequest` - see
[cycle caps](task-stages.md#cycle-caps) for all six in one table.

!!! warning "There is no `deployDeadline`, no `mergeWaitDeadline`, no `reviewResolveDeadline`" <!-- stale-ok: deployDeadline, mergeWaitDeadline, reviewResolveDeadline -->
    The per-edge deadline family is gone, generalised into `stateEnteredAt` plus
    the three-clock model. Three clocks - admission, readiness, work/idle - are armed
    by **which timestamps are set**, and every state carries a budget. There is no
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
| `turns` | **Lifetime** turns across every pod. Checked against `maxTurnsPerTask`; at the cap, `parked(turn-budget-exhausted)` |
| `podRuns` | Pods this Task has run |
| `wallSeconds` | Total agent wall time |
| `agentsRun` | The agent kinds that have run. `MaxItems=50` |
| `issueCount` / `mrCount` | Owned `Issue` / `MergeRequest` counts. Both are print columns |
| `podRecreations` | Pod respawns **within the current state**. At `maxPodRecreations` (3) the Task parks at `pod-recreation-exhausted`. **Reset to 0 on every transition** |
| `notesSpilled` | Notes evicted to `tatara-memory` by the byte guard |
| `notesSpilledRefs` | One `track_id` per spill batch. It **accumulates** - a single scalar ref would orphan every earlier batch. Read back with `task_context(notes=all)` |

---

## Print columns

`kubectl get task` shows: **State** (`.status.state`), **Park**
(`.status.parkReason`), **Agent** (`.status.agentKind`), **Kind**
(`.spec.kind`), **Project** (priority 1), **Turns** (`.status.stats.turns`),
**Description**, plus kubectl's own default **Age**.

`State` and `Kind` next to each other is the fastest way to see the thing
people get wrong: a Task named `...-refine-...` sitting at
`state=under-implementation` with `agentKind=implement` is not an anomaly.
It is the normal path - `state` is kind-agnostic, and `Park` being non-empty
is what actually means the Task is stalled, not any particular `state`
value.

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
evict - the Task parks **loudly** at `parkReason=object-too-large`, written
through a minimal status patch (`MinimalFailPatch`) that touches only
`status.parkReason` / `status.parkedAt`, carries none of the oversized lists,
and therefore cannot itself 413. This is uniform across every state now -
there is no separate `failed` terminal for pod-less states to route to
instead. `object-too-large` is `UnparkNever`: it has no re-entry and ages out
at `ParkRetention` like any other terminal park. A note write is never
rejected on a count cap (an agent must always be able to write its handoff),
and a Task that genuinely cannot fit dies with a recorded reason instead of
becoming a silently unwritable zombie.

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
    `Project.spec.scm.botLogin`. This is load-bearing, not hygiene: several
    paths do post as the bot - an agent's own `issue_write(action=comment)`,
    the operator's deploy-timeout notice, the terminal reap comment - and
    without the filter any of those would land in the Task's own
    `pendingEvents` and **un-park the Task the platform just parked itself
    into**, a fully autonomous loop driven by nothing but the platform talking
    to itself. Not every park reason posts a comment at all -
    `identity-unverified` posts nothing to the thread - but the filter has to
    hold uniformly regardless of which reasons do.

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
| kind enum: `implement`, `selfImprove`, `triageIssue`, `healthCheck`, `issueLifecycle` | `triageIssue` and the front half of `issueLifecycle` landed on `clarify` at the time, `healthCheck` on `brainstorm`, and the back half of `issueLifecycle` on the operator's own `merging` / `deploying` stages. **`clarify` itself is gone too now** - see the #521 rename below <!-- stale-ok: selfImprove, triageIssue, healthCheck, issueLifecycle --> |

### The #521 rename: `stage` (15 members) became `state` (8) plus `parkReason`

A second redesign, independent of the migration above, replaced
`status.stage` and its fifteen members with three orthogonal properties.
This is not a removal in the same sense as the table above - it is a field
rename plus a decomposition, and it is recent enough that dashboards and
`jsonpath` built against the first redesign's `stage` field need this table
too:

| Old (`status.stage`, 15 members) | New |
|---|---|
| `status.stage` | `status.state` - 8 members: `new`, `refined`, `under-implementation`, `awaiting-review`, `merged`, `deployed`, `done`, `rejected` |
| `stage=parked` (a stage, a terminal, *and* a pod-less marker, conflated) | `status.parkReason` - an orthogonal flag on top of whichever `state` the Task is already in. See [the park flag](task-stages.md#the-park-flag) |
| `stage=failed` (and every `failed(...)` reason) | folded into `status.parkReason` (most reasons) or `status.state=rejected` (a genuine stop, e.g. `false-positive`) - there is no `failed` state any more |
| `stage=delivered` | `status.state=done` |
| `stage=triaging`, `clarifying`, `investigating`, `refining`, `approved` | `status.state=new` or `refined`, disambiguated by `Task.status.agentKind`, not by the state itself |
| `stage=implementing` | `status.state=under-implementation` |
| `stage=reviewing` | `status.state=awaiting-review` |
| `stage=merging`, `deploying` | `status.state=merged`, `deployed` |
| `stage=documenting` | `status.state=under-implementation` with `agentKind=documentation` |
| `status.stageEnteredAt` / `stageWorkStartedAt` / `stageReason` | `status.stateEnteredAt` / `stateWorkStartedAt` / `stateReason` (the last now mandatory only on `rejected`, not on every terminal) |
| `status.parkedFromStage` | `status.parkedFromState` (same observability-only semantics, plus one load-bearing use: the `no-outcome` un-park gate) |
| `Task.spec.kind: clarify` | `Task.spec.kind: implement` (`SweepIssueKind`) - the same mint path, an issue webhook or the backlog sweep. Its three decisions became `action` values on the `implement` outcome - see [Approval Gates](../operations/security/approval-gates.md) |

`Note.Agent`'s enum still lists `clarify` as a valid **historical** value
(notes already in etcd say `clarify`) even though nothing can write it any
more - removing an already-used enum value breaks CRD ratcheting on every
Task that still carries one. See the note on `AgentKindFor` having no
`clarify` case above.

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
  name: tatara-incident-2026-07-12-m4z8q
  namespace: tatara
spec:
  projectRef: tatara
  kind: incident
  goal: |
    Resolve tatara-operator#291: the reaper deletes a Task whose pod is
    mid-turn. Confirm scope with the maintainer, then implement.
  mergeOrder:
    - tatara-operator
    - tatara-cli
```

`mergeOrder` is set here because this stream is expected to touch two repos and
the operator merges them in that order, one at a time, each on green CI.

---

## Inspecting a Task

```sh
# Every Task on a project, with State / Park / Agent / Kind / Turns
kubectl -n tatara get task -l tatara.dev/project=tatara

# Why is it parked?
kubectl -n tatara get task tatara-incident-2026-07-12-m4z8q \
  -o jsonpath='{.status.state}{" "}{.status.parkReason}{"\n"}'

# The journal - this is the continuation state, and the first thing to read
kubectl -n tatara get task tatara-incident-2026-07-12-m4z8q \
  -o jsonpath='{.status.notes}' | jq -r '.[] | "\(.at) \(.agent)/\(.kind): \(.body)"'

# What it owns
kubectl -n tatara get task tatara-incident-2026-07-12-m4z8q \
  -o jsonpath='{.status.issueRefs}{"\n"}{.status.mrRefs}{"\n"}'

# Stream the current pod's logs
kubectl -n tatara logs \
  "$(kubectl -n tatara get task tatara-incident-2026-07-12-m4z8q \
      -o jsonpath='{.status.podName}')" \
  -c tatara-claude-code-wrapper -f
```

---

## See also

- [The Task state machine](task-stages.md) - the eight states, `parkReason`, liveness, the transition table, the three clocks
- [Task notes](task-notes.md) - the journal
- [Issue](issue.md) and [MergeRequest](merge-request.md) - the SCM mirrors a Task owns
- [Project](project.md) - the levers: `maxConcurrentAgents`, `maxOpenTasks`, `modelByKind`
- [QueuedEvent](queued-event.md) - the admission unit
- [MCP tools](mcp-tools.md) - `submit_outcome`, `task_note`, `task_context`
