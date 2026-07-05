# Agent-work queue (in-operator) - Design

Date: 2026-06-20
Repo: tatara-operator (only; wrapper unaffected)
Status: approved design, pre-plan
Scope: single milestone, end-to-end. Per-project, opt-out via defaults that match today's behaviour.

## Problem

Every agent-spawning path in the operator creates a Task CR directly and immediately:
SCM webhooks (issueLifecycle/review), the Grafana alert webhook (incident, deployed inert),
and the hourly crons (mrScan/issueScan/brainstorm/healthCheck). Execution is then throttled
by `maxConcurrentTasks` (execution-time `atConcurrencyCap`), creation by `maxOpenTasks`, and
per-repo concurrency by `laneOccupancy`/`selectPerRepo`. Among Tasks waiting behind the
concurrency cap, dispatch order is controller-runtime's generic workqueue - not a business
order. There is no single ordered intake, no durable buffer that decouples burst arrival from
execution, and no priority fast-path for alerts at execution time.

We want a per-project queue that:
- buffers incoming events durably (survives operator restarts; bounded by an explicit cap),
- admits them to execution in a strict per-project order (FIFO by arrival), against
  per-project capacity - one Project's backlog never consumes another's slots,
- with a reserved priority lane so alerts never wait behind normal dev work.

## Approved decisions

- **Placement: folded into the operator** (not a standalone service). The user explicitly
  accepts violating "outside the operator". Residual durability gap named + accepted: while
  the operator pod is actually down, webhooks are not accepted at all; we rely on sender
  retries (Grafana retries; GitHub/GitLab redeliver) plus a fast leader-elected restart. A
  QueuedEvent is written immediately on receipt, before any processing, so the only loss
  window is true operator unavailability.
- **Substrate: a `QueuedEvent` CRD in etcd** (not a Postgres table). The operator has no DB
  of its own; the per-project memory Postgres is project-scoped and provisioned by the
  operator, so coupling the operator's cross-project queue state to any one project's DB is
  wrong. etcd is already the durable store for every Task. KISS: no new datastore dependency.
- **Ordering: strict per-project FIFO** by an explicit monotonic `seq`. `seq` is an
  operator-global monotonic source (one counter), but admission, ordering, in-flight
  accounting, and capacity are all per-project: the dispatcher filters `QueuedEvent`s and
  `Task`s by `ProjectRef` before sorting and admitting, so a global `seq` only fixes the
  relative order *within* each project (cross-project, projects are fully isolated). Per-repo
  lanes are removed. Head-of-line blocking is accepted within a project: a slow head task
  holds a normal slot until it is terminal; unrelated repos in the same project wait behind
  it. Reserved alert capacity is the only bypass.
- **Priority: a reserved alert lane.** Alerts (Grafana firing) run in a separate capacity
  pool `M` that is never consumed by normal work, so an alert never waits behind dev work.
- **Autonomous (cron) enqueue is bounded** by a queued-depth cap (ports today's
  `maxOpenTasks` intent): crons stop enqueuing once the count of `Queued` autonomous events
  reaches the cap. Webhooks and alerts are always enqueued (never dropped).
- **Scope = all agent-running workflows**: every path that spawns an agent admits through
  the queue. No path creates a Task directly anymore.

## Architecture (units + boundaries)

### Unit A - `QueuedEvent` CRD (api/v1alpha1/queuedevent_types.go)

Namespaced CRD, owner-referenced to its Project (GC with the Project).

```go
type QueuedEventSpec struct {
    // Seq is the strict total order. Assigned by the single-active operator
    // from a monotonic counter; never reused. Required.
    Seq int64 `json:"seq"`
    // Class selects the capacity pool: "normal" | "alert".
    Class string `json:"class"`
    // Kind is the Task kind this event will mint:
    // issueLifecycle|review|brainstorm|healthCheck|incident|mrScan|issueScan.
    Kind string `json:"kind"`
    // Autonomous marks cron-originated events (subject to the queued-depth cap).
    // Webhook/alert events are false (always enqueued).
    Autonomous bool `json:"autonomous"`
    ProjectRef    string `json:"projectRef"`
    RepositoryRef string `json:"repositoryRef,omitempty"` // empty for project-scoped kinds
    // DedupKey collapses duplicate intake (incident groupKey hash; issueLifecycle
    // SHA-256 of projectName+issueRef). Empty = no dedup.
    DedupKey string `json:"dedupKey,omitempty"`
    // Payload carries the per-kind context the Task creation needs today
    // (goal/source/alert annotation block), as the existing TaskSource plus a
    // free-form annotation map. Modelled to round-trip exactly what each current
    // create-path already passes.
    Payload QueuedEventPayload `json:"payload"`
}

type QueuedEventStatus struct {
    // State: Queued -> Admitted -> Done.
    State   string `json:"state,omitempty"`
    TaskRef string `json:"taskRef,omitempty"` // Task minted on admission
    AdmittedAt *metav1.Time `json:"admittedAt,omitempty"`
}
```

- `+kubebuilder` printcolumns: Seq, Class, Kind, State.
- Validation: `Class in {normal,alert}`; `Kind` in the known set; project-scoped kinds must
  have empty `RepositoryRef`, repo-scoped kinds must set it (mirror `ValidateTaskSpec`).
- CRD-gap memory: new CRD must be `kubectl apply`-ed (Helm skips CRD upgrades) - in the
  deploy runbook.

### Unit B - seq assignment (single-active, recovered on boot)

The operator runs leader-elected single-active, so seq assignment needs no distributed
counter. A small `seqAllocator` holds an in-memory `int64`. On manager start (after cache
sync), it lists all QueuedEvents once and sets the counter to `max(Seq)+1` (0 -> 1 when
none). `Next()` is mutex-guarded `++`. Enqueue always goes through it. etcd
`creationTimestamp` is 1s-granular (collisions under burst), so seq is explicit, not derived.

### Unit C - producers: enqueue instead of create

All current "create a Task" sites become "enqueue a QueuedEvent":

- **Webhook handlers** (`internal/webhook/server.go`, `grafana.go`): build a QueuedEvent
  (`Class=alert` for Grafana firing, else `normal`; `Autonomous=false`) and create it.
  Dedup/cooldown moves to enqueue: skip-create when a non-terminal QueuedEvent OR a
  non-terminal Task with that `DedupKey` exists (preserves today's incident groupKey dedup
  + cooldown and the issueLifecycle deterministic-name dedup). The webhook path is never
  capped.
- **Crons** (`internal/controller/projectscan.go` runScans -> mrScan/issueScan/brainstorm/
  healthCheck): each selected work item becomes a QueuedEvent with `Autonomous=true`.
  Before enqueuing, check the queued-depth cap (Unit F).

### Unit D - dispatcher controller (internal/controller/queue_controller.go, new)

A controller-runtime reconciler watching QueuedEvent (and watching Task to free slots).

- Maintains two in-flight counts derived from cluster state each pass (never from deltas,
  mirroring the lifecycle-gauge recompute precedent): `normalInFlight` = QueuedEvents in
  `Admitted` with `Class=normal` whose Task is non-terminal; same for `alertInFlight`.
- Admission pass (per project): list `Queued` events, sort by `(class-priority, seq)` where
  alert sorts ahead only within its own pool accounting. Concretely: drain the alert pool
  first (admit `Queued` alert events while `alertInFlight < M`), then the normal pool (admit
  `Queued` normal events in seq order while `normalInFlight < N`). Pure per-project FIFO
  within each pool by seq; HOL blocking accepted. `N`/`M`/`normalInFlight`/`alertInFlight`
  are all scoped to the project being reconciled (events/Tasks filtered by `ProjectRef`).
- Admit = create the Task CR exactly as the current create-paths do (reusing the existing
  builders, fed from `Payload`), label it `tatara.dev/queued-event=<name>`, then set the
  QueuedEvent `State=Admitted, TaskRef, AdmittedAt`.
- Completion: a Task watch maps terminal Tasks (single-source `TaskTerminal` predicate) back
  to their QueuedEvent via the label/owner-ref; on terminal set QueuedEvent `State=Done` and
  requeue the dispatcher (frees the slot). `Done` events are GC'd after a short TTL.

### Unit E - migration of in-flight Tasks at cutover

At deploy, non-terminal Tasks exist with no QueuedEvent. The dispatcher counts any
non-terminal Task lacking the `tatara.dev/queued-event` label toward the matching pool's
in-flight (incident kind -> alert pool, else normal) so capacity is not over-admitted during
drain. No backfill of QueuedEvents for them; they finish under the existing reconciler and
free capacity normally.

### Unit F - capacity + cap config (Project CRD)

New `spec.queue` on ProjectSpec (scalars per rule 6, camelCase):

```go
type QueueSpec struct {
    Capacity            int `json:"capacity,omitempty"`            // N, default = maxConcurrentTasks
    AlertCapacity       int `json:"alertCapacity,omitempty"`       // M, default 1
    QueuedAutonomousCap int `json:"queuedAutonomousCap,omitempty"` // K, default = old maxOpenTasks (3/6)
}
```

- `Capacity` (N) replaces `maxConcurrentTasks` as the normal-pool execution gate.
- `AlertCapacity` (M) is the reserved alert pool.
- `QueuedAutonomousCap` (K) bounds `Queued` autonomous events: crons stop enqueuing once
  `count(Queued, Autonomous=true) >= K`. Webhooks/alerts exempt.
- Defaults chosen so a Project that sets nothing behaves like today's throughput.

### What this supersedes (deletions, not just additions)

- `maxConcurrentTasks` / `atConcurrencyCap` (task_controller.go) -> the dispatcher's `N`.
  The execution-time cap is removed; admission is the only gate.
- `maxOpenTasks` / `openTaskCount` / budget threading (projectscan.go) -> the queued-depth
  cap `K` on autonomous enqueue.
- `laneOccupancy` / `selectPerRepo` / `selectCandidates` / per-repo fan-out / `priorityLabel`
  sort (projectscan.go) -> removed. Global FIFO by seq replaces per-repo lanes; the alert
  class replaces the priority label for execution ordering. (`priorityLabel` may remain a
  no-op config field for API compat, or be dropped - decided in the plan.)

This is a real simplification of the projectscan selection layer: crons shrink to "list work
items, enqueue up to the cap", and all ordering/concurrency logic lives in one dispatcher.

## Data flow

```
event (SCM webhook | Grafana firing | cron scan item)
  -> enqueue QueuedEvent (seq=Next(); class=alert|normal; autonomous?; dedupKey)
       - webhook/alert: always; dedup-skip on existing non-terminal dedupKey
       - cron: skip if count(Queued, autonomous) >= K
  -> dispatcher: drain alert pool to M, then normal pool to N, FIFO by seq
  -> admit head: create Task (existing builder from Payload), label queued-event, State=Admitted
  -> existing TaskReconciler runs the agent (Pod path unchanged)
  -> Task terminal -> QueuedEvent State=Done -> slot freed -> dispatcher requeue
```

## Error handling

- Enqueue create conflict (dedup race) -> treat AlreadyExists as dedup hit (200), as today.
- Admission Task-create failure -> leave QueuedEvent `Queued`, requeue with backoff; the
  slot is not consumed (count derives from Admitted only).
- Operator restart -> seqAllocator recovers `max+1`; `Admitted` events whose Task is gone
  (deleted) are re-driven: if no Task and not Done, dispatcher recreates from Payload
  (idempotent on the same QueuedEvent).
- A QueuedEvent stuck `Admitted` with a terminal Task that the watch missed is reconciled to
  `Done` on the periodic resync (no delta reliance).

## Testing (TDD)

- api: `QueuedEvent` validation (class/kind/repo-scoping); `QueueSpec` defaults.
- seqAllocator: recover `max+1` on boot; monotonic under concurrent `Next()`.
- producers: each webhook + cron path enqueues the right class/kind/autonomous/dedupKey;
  dedup-skip on existing non-terminal dedupKey; cron respects `K`; webhook ignores `K`.
- dispatcher: alert pool drained before normal; never exceeds N/M; pure seq FIFO within a
  pool; HOL blocking demonstrated; terminal Task -> Done -> next admitted; migration counts
  unlabelled non-terminal Tasks toward the right pool.
- supersession: removed selection functions have no callers; crons no longer create Tasks
  directly; `atConcurrencyCap` gone.
- Full envtest controller + webhook suites green.

## Deploy

1. tatara-operator: merge CRD (`QueuedEvent` + `QueueSpec`) + dispatcher + producer rewrites
   + deletions -> operator image. `make manifests`; `kubectl apply` the regenerated CRDs
   (Helm skips CRD upgrades - operator-CRD-gap memory).
2. tatara-helmfile: bump operator chart version + pinned `image.tag`; set per-project
   `spec.queue` (or rely on defaults that match today). Diff -> apply.
3. Cutover note: at first reconcile after deploy, in-flight pre-queue Tasks drain under the
   migration rule; new work flows through the queue. The Grafana incident webhook, deployed
   inert, now enqueues alert-class events - enabling it (per its own design) lights up the
   reserved alert lane.

## Out of scope

- Standalone external intake service / Postgres-backed queue (rejected: user folded into
  operator; etcd substrate chosen).
- Per-repo fan-out / per-repo lanes (deliberately removed for per-project FIFO).
- Preemption of running tasks for alerts (reserved capacity instead).
- Cross-operator / multi-active seq assignment (single leader-elected active is assumed).
```
