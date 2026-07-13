---
title: QueuedEvent CRD
---

# QueuedEvent

A `QueuedEvent` CR is an entry in the operator's admission queue. Producers (webhook handlers, cron reconcilers) create `QueuedEvent` objects; the dispatcher admits them to become `Task` CRs when a concurrency slot is available.

```
apiVersion: tatara.dev/v1alpha1
kind: QueuedEvent
```

!!! info "Internal resource"
    `QueuedEvent` is primarily an internal operator resource. You rarely create or modify them directly. They appear in `kubectl get queuedevents` and are useful for diagnosing why a task has not started yet.

## Spec

| Field | Type | Description |
|---|---|---|
| `seq` | int64 | Monotonically increasing sequence number. Drains in ascending `(priority, seq)`, not `seq` alone |
| `priority` | `*int` (0, 1, 2; default 2) | Lower drains first. `0` = incident (redundant with the reserved alert pool; kept for clarity), `1` = webhook-originated (a human is waiting on a thread), `2` = cron/sweep-originated (proactive work). A **pointer**, not a plain int: a bare `int` would always serialize `0` and every producer that forgot the field would land in the most urgent tier |
| `class` | enum | `normal` or `alert` - `alert` (incident-agent spawns) draws from the reserved alert pool, independent of `priority` |
| `kind` | string | Task kind this event will become |
| `autonomous` | bool | True for cron-generated (autonomous) events |
| `projectRef` | string | Parent Project CR name |
| `repositoryRef` | string | Repository CR name (empty for project-scoped kinds) |
| `dedupKey` | string | The **natural key** - `iss:<repo>#<number>`, `mr:<repo>!<number>`, or the incident alert-group hash. A second event with the same key is dropped if the first is still Queued or Admitted |
| `payload` | [QueuedEventPayload](#queuedeventpayload) | What to spawn on admission |

The dedup key lives in this **field**, never a label: Kubernetes label values cannot contain `:`
or `#`, so a label-based natural key would have to be hashed back into an opaque digest -
exactly what this field form avoids. The authoritative "is this issue already being worked?"
lookup is a field index on the owning `Issue`/`MergeRequest` CR (`issueKey`, `mrKey`), not a
label selector on the `QueuedEvent`.

### QueuedEventPayload

```go
type QueuedEventPayload struct {
    AgentKind string                `json:"agentKind"`           // required
    TaskRef   string                `json:"taskRef,omitempty"`   // existing Task (stage-driven spawn)
    NewTask   *QueuedTaskBlueprint  `json:"newTask,omitempty"`   // blueprint for a Task that does not exist yet
}
```

| Field | Type | Description |
|---|---|---|
| `agentKind` | string | **Required.** The pod to spawn: `brainstorm`, `incident`, `clarify`, `refine`, `review`, `documentation`, or `implement` |
| `taskRef` | string | Names an existing Task - a stage-driven spawn (e.g. `approved -> implementing`) |
| `newTask` | `QueuedTaskBlueprint` | The blueprint for a Task that does not exist yet - a mint |

Exactly one of `taskRef` / `newTask` is set. `QueuedTaskBlueprint` carries the deterministic
`name` (so admission is idempotent), the origin `kind`, `goal`, `projectRef`, `repositoryRef`,
owned `issueKeys`, `alertRules`, and any `labels`/`annotations` to apply to the created Task.

## Status

| Field | Type | Description |
|---|---|---|
| `state` | enum | `Queued` or `Admitted` |
| `taskRef` | string | Name of the Task created (or already existing, for a stage-driven spawn) on admission |
| `admittedAt` | timestamp | When this event was admitted |

## Queue classes and capacity

```
normal class: up to Project.spec.agent.maxConcurrentAgents slots (default 3)
alert class:  up to Project.spec.queue.alertCapacity slots (default 1)
```

Capacity is keyed on `maxConcurrentAgents` - the admission unit is one **pod-spawn**, not one
Task. `maxConcurrentAgents == 0` is the project-wide pause: the operator admits nothing at all,
independent of `alertCapacity`. Alert-class events (incidents) have dedicated reserved capacity
on top of the normal pool; a full normal queue does not block an incoming incident from being
admitted.

Within the normal pool, priority-2 (cron/sweep) work is guaranteed at least one slot whenever a
priority-2 event has been Queued for over an hour, so a busy project's priority 0/1 traffic can
never fully starve the nightly documentation batch or a refine groom pass.

## Dedup behavior

When a new `QueuedEvent` arrives with a `dedupKey` that matches an existing `Queued` or
`Admitted` event, the new event is dropped. This prevents duplicate Tasks when, for example, a
webhook fires twice or a cron overlaps with a still-running task for the same issue.

For `clarify` events the dedup key is the issue's natural key, `iss:<repo>#<number>`. The
operator uses a fixed `name` (not `generateName`) to make admission idempotent even if the
QueuedEvent is re-created.

## Inspecting the queue

```sh
kubectl -n tatara get queuedevents
# NAME                        SEQ   CLASS    KIND             STATE
# my-project-12345            17    normal   clarify          Queued
# my-project-alert-98765      18    alert    incident         Admitted

kubectl -n tatara describe queuedevent my-project-12345
```

A `Queued` event that stays `Queued` for a long time indicates the queue is at capacity. Check
currently running Tasks by stage instead - `Task` has no `status.phase` field: <!-- stale-ok: status.phase -->

```sh
kubectl -n tatara get tasks -l tatara.dev/project=my-project \
  -o custom-columns=NAME:.metadata.name,STAGE:.status.stage,REASON:.status.stageReason
```
