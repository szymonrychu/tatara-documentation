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
| `seq` | int64 | Monotonically increasing sequence number (admission order) |
| `class` | enum | `normal` or `alert` (alert events get reserved slots) |
| `kind` | string | Task kind this event will become |
| `autonomous` | bool | True for cron-generated (autonomous) events |
| `projectRef` | string | Parent Project CR name |
| `repositoryRef` | string | Repository CR name (empty for project-scoped kinds) |
| `dedupKey` | string | Dedup key; a second event with the same key is dropped if the first is still Queued or Admitted |
| `payload` | [QueuedEventPayload](#queuedeventpayload) | Task blueprint rebuilt verbatim on admission |

### QueuedEventPayload

| Field | Type | Description |
|---|---|---|
| `goal` | string | Task goal text |
| `kind` | string | Task kind |
| `repositoryRef` | string | Repository CR name |
| `source` | TaskSource | SCM work-item that originated the event |
| `labels` | `map[string]string` | Labels to apply to the created Task |
| `annotations` | `map[string]string` | Annotations to apply to the created Task |
| `name` | string | Fixed Task name (for idempotent admission, e.g. issueLifecycle) |
| `generateName` | string | Name prefix when `name` is empty |
| `provider` | string | SCM provider (for pod naming) |
| `podRepo` | string | Repo slug used in pod name |
| `systemicGroup` | SystemicGroup | Systemic improvement group |
| `alertRule` | string | Alert rule name (incident events) |

## Status

| Field | Type | Description |
|---|---|---|
| `state` | enum | `Queued` or `Admitted` |
| `taskRef` | string | Name of the Task created on admission |
| `admittedAt` | timestamp | When this event was admitted |

## Queue classes and capacity

```
normal class: up to Project.queue.capacity slots (default 3)
alert class:  up to Project.queue.alertCapacity slots (default 1)
```

Alert-class events (incidents) have dedicated reserved capacity. A full normal queue does not block an incoming incident from being admitted.

## Dedup behavior

When a new `QueuedEvent` arrives with a `dedupKey` that matches an existing `Queued` or `Admitted` event, the new event is dropped. This prevents duplicate Tasks when, for example, a webhook fires twice or a cron overlaps with a still-running task for the same issue.

For `issueLifecycle` tasks, the dedup key is the issue `owner/repo#N` reference. The operator uses a fixed `name` (not `generateName`) to make admission idempotent even if the QueuedEvent is re-created.

## Inspecting the queue

```sh
kubectl -n tatara get queuedevents
# NAME                        SEQ   CLASS    KIND             STATE
# my-project-12345            17    normal   issueLifecycle   Queued
# my-project-alert-98765      18    alert    incident         Admitted

kubectl -n tatara describe queuedevent my-project-12345
```

A `Queued` event that stays `Queued` for a long time indicates the queue is at capacity. Check currently running tasks:

```sh
kubectl -n tatara get tasks -l tatara.dev/project=my-project \
  --field-selector status.phase=Running
```
