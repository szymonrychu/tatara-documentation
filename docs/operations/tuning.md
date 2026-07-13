---
title: Tuning
---

# Tuning agents and flows

Task-oriented guide to the levers that control how much a `Project` runs, what
it spends, and which scheduled flows are active. Every lever here is a field
on the [`Project`](../reference/project.md) CR. Full field-by-field reference
(types, defaults, kubebuilder validation) lives there; this page is "what do I
change to get effect X."

!!! danger "GitOps only - never `kubectl edit`/`patch`/`helm upgrade` by hand"
    Live Project spec values are owned by the standalone `tatara-helmfile` repo
    (`values/project-tatara/common.yaml`, `values/project-infrastructure/common.yaml`),
    which renders the `tatara-project` chart and self-deploys on merge to `main`
    via an in-cluster ARC runner. To change any value on this page: edit the
    relevant `common.yaml`, open a PR, review the sticky `helmfile diff` comment,
    merge. The pipeline applies it. A live `kubectl patch` is permitted only as
    incident response to unblock a down service, and any such patch must be
    immediately re-asserted through a `tatara-helmfile` MR so the repo matches
    live state. See [Deployment & GitOps](deployment.md).

---

## Pause a project entirely

Set `maxConcurrentAgents: 0`.

```yaml
# values/project-tatara/common.yaml
project:
  spec:
    maxConcurrentAgents: 0
```

The admission unit is now the **pod spawn**, not the Task. At `0`, `admit()`
short-circuits at the top and no `QueuedEvent` is ever admitted - so no pod
spawns and no Task is created. **Every** pod-spawning stage goes through the
same chokepoint, so a `0` freezes the whole project mid-flight, including
Tasks that are already running.

There is no `Minimum=1` on this field. `Project.QueueCapacity()` floors at 3
when the field is unset, so the pause is a **direct
`spec.maxConcurrentAgents == 0` check** at the top of `admit()`, deliberately
not routed through `QueueCapacity()` - which would silently un-pause you.

!!! warning "A paused project does not shred its backlog"
    The `approved` stage has a 24h `admission-starved` deadline. That check
    **skips Tasks whose project is paused** - it is the only stage-deadline
    exception in the platform, and it exists so that the kill switch is a
    pause and not a backlog shredder.

To reduce load without a full stop, lower `maxConcurrentAgents` to a smaller
positive number, or tune `queue.capacity` / `queue.alertCapacity` directly if
you need the normal-pool and alert-pool (incident) concurrency to diverge from
`maxConcurrentAgents`.

---

## Disable a specific flow

Every cron-driven activity (`mrScan`, `issueScan`) is off when its `schedule`
is empty. `brainstorm` and `documentation` additionally require
`enabled: true` - clearing `enabled` (or leaving it unset) disables them
regardless of `schedule`.

| To disable | Set |
|---|---|
| MR/PR review scan | `scm.cron.mrScan.schedule: ""` |
| Issue scan | `scm.cron.issueScan.schedule: ""` |
| Brainstorm (self-driven proposals) | `scm.cron.brainstorm.enabled: false` |
| Documentation (periodic docs upkeep) | `scm.cron.documentation.enabled: false` |

`scm.cron.refine` has no independent schedule - it fires as a mandatory
barrier before every due scan/brainstorm cycle and cannot be disabled short of
removing all of mrScan/issueScan/brainstorm/documentation schedules.

There is no push-CD deploy-supervision backstop cron any more (`cdScan` is
gone with the fields it swept). Documentation is now **one nightly batch Task
per project**, covering everything delivered in the last 24h - not a
per-delivery spawn and not a "did anything meaningful change?" judgment call.

Both live projects currently run `mrScan` every 2h and `issueScan` every 4h
(stretched from hourly for token conservation; push webhooks cover real-time
activity, so these are backstops, not the primary trigger path).

---

## Enable or disable the brainstorm staleness reaper

`scm.cron.brainstorm.staleProposalDays` is an opt-in sentinel, not a normal
default: `<= 0` (including unset) keeps the reaper off. A positive value
auto-closes bot-authored proposals with no human engagement (no human
comment, no live work) after that many days, freeing backlog space under
`maxOpenProposals`.

```yaml
scm:
  cron:
    brainstorm:
      enabled: true
      maxOpenProposals: 10
      staleProposalDays: 14   # 0 or omit to disable the reaper
```

Both live projects run `staleProposalDays: 14`.

---

## Cap spend

Two independent levers, from broadest to narrowest:

### 1. Token-budget admission gate (`tokenBudget`)

Off by default at every level - a Project inherits the operator-wide default
(`enabled: false`) unless it sets its own `tokenBudget` block. When enabled it
pauses the normal pool at `proactivePercent` of the measured window and the
alert/incident pool at `emergencyPercent`:

```yaml
project:
  spec:
    tokenBudget:
      enabled: true
      mode: customWindow          # or claudeSubscription
      proactivePercent: 50
      emergencyPercent: 80
      resetSchedule: "0 0 * * *"  # customWindow only
      windowDuration: "24h"       # customWindow only
      tokenLimit: 50000000        # customWindow only
```

`mode: claudeSubscription` gates on the wrapper-reported Claude 5h/weekly
usage percentages instead of an absolute token count, and needs no
`resetSchedule`/`windowDuration`/`tokenLimit`. It exists in the current CRD
but is **not deployed anywhere today** - neither `tatara` nor `infrastructure`
sets a `tokenBudget` block, so the gate is fully off fleet-wide. A more
advanced per-kind admission gate (fleet-wide Claude-usage poller plus a
per-kind spawn-ceiling ladder, meant to supersede this per-project snapshot
mechanism) is in development on an unmerged feature branch
(`feat/usage-window-gating` in `tatara-operator`) - it is not yet part of the
`main` API and not usable via `tatara-helmfile` values yet.

### 2. Model/effort tiering per agent kind

Drop specific agent kinds to a cheaper model or lower reasoning effort while
keeping the project-wide fallback high for everything else:

```yaml
agent:
  model: claude-opus-4-8
  effort: high
  modelByKind:
    documentation: claude-sonnet-5
    refine: claude-sonnet-5
  effortByKind:
    documentation: medium
    refine: medium
```

`modelByKind` / `effortByKind` key on **`Task.status.agentKind`** (the
running agent), not `Task.spec.kind` (the immutable origin). The seven valid
keys are `brainstorm`, `incident`, `clarify`, `implement`, `review`,
`refine`, `documentation`. A missing or empty entry falls back to the
project-wide `model`/`effort`. The locked default tiering is
`brainstorm`/`incident`/`clarify`/`implement`/`review` on Opus at `high`
effort, and `documentation`/`refine` on Sonnet - both live projects run this
default unmodified.

!!! danger "A key on a retired kind is silently ignored"
    `values/project-*/common.yaml` currently sets `modelByKind.triageIssue` and <!-- stale-ok: triageIssue -->
    `effortByKind.triageIssue`. <!-- stale-ok: triageIssue -->
    `triageIssue` is a retired kind. The key does not match, so those Tasks <!-- stale-ok: triageIssue -->
    fall back to the project-wide default - **Opus at `high` effort**. That
    is a cost regression, not merely dead YAML. Repoint it at the surviving
    agent kinds above in the same PR. <!-- stale-ok: triageIssue -->

There is no separate per-task token-count backstop any more
(`agent.maxTaskTokens` is gone). <!-- stale-ok: maxTaskTokens --> The turn-based
lifetime cap, `agent.maxTurnsPerTask` below, is what bounds a runaway
`implement` Task now.

---

## All the levers

| Lever | Default | What it bounds |
|---|---|---|
| `maxConcurrentAgents` | `3` | Concurrent agent pods. `0` is the pause |
| `agentPodTTLSeconds` | `3600` (min `300`) | **One pod's life. The Task persists.** See the stop sequence below |
| `maxNewTasksPerSweep` | `5` (min `1`) | Tasks one sweep pass may mint |
| `maxOpenTasks` | `6` (min `1`) | ACTIVE Tasks (stage not in `parked` / `delivered` / `rejected` / `failed`). `parked(backlog-sweep)` Tasks do not count - they hold ownership, not work |
| `maxBundleBytes` | `400000` (min `50000`) | Hard byte budget on a rendered context bundle |
| `agent.maxTurnsPerPod` | `40` | One pod run. **The `implement` agent kind is EXEMPT** - a long healthy coding run must not be cut off |
| `agent.maxTurnsPerTask` | `300` | LIFETIME turns across every pod of the Task. Applies to every kind, `implement` included. This is what bounds the exemption |
| `agent.maxReviewRounds` | `3` | Accepted `request_changes` verdicts before `parked(review-loop-exhausted)` |
| `agent.maxHumanReviewRounds` | `5` | Un-parks of a `review`-kind Task back to `reviewing` on a human comment. At the cap it stays parked. This is what stops a chatty PR thread spawning one review pod per comment |
| `agent.maxPodRecreations` | `3` | Pod respawns within the current stage before `failed(pod-recreation-exhausted)`. Reset to 0 on every transition. **A pod that never becomes Ready within `podReadyTimeout` (5m of `podStartedAt`) is a respawn, not a failure** - it burns one of these, and this counter is what eventually terminates it |
| `scm.approvalPhrases` | `lgtm`, `approve`, `approved`, `ship it`, `go ahead`, `go`, `implement it` | The closed wordlist an approving comment must match. **Empty means the defaults; it can never mean "any text approves"** |

`maxOpenTasks` is a Task-**creation** budget and `maxNewTasksPerSweep` bounds
one sweep pass's minting - both are different levers from
`maxConcurrentAgents`, which is a pod-**concurrency** budget. Raising one does
not raise the others.

**`agentPodTTLSeconds` bounds a pod, not a Task.** On expiry the operator
stops admitting new turns, waits for the in-flight turn's callback (bounded by
`turnTimeoutSeconds`), submits one final handoff turn ("your pod is being
stopped; call `task_note(kind=handoff)` with everything the next pod needs"),
and hard-caps at `t0 + 2 * turnTimeoutSeconds + 60s`. On the cap, or on any
409/5xx, the operator writes a **synthetic handoff note in-process** from the
turn's `finalText` and `pushedRepos`, then force-deletes the pod.

`Task.status.notes` is therefore **never empty after a TTL stop**. Either the
agent wrote a handoff, or the operator wrote one for it.

---

## Stage deadlines: one clock family, no per-edge field to forget

There is no separate `deployBudgetSeconds` / `deploySingleHopBudgetSeconds` <!-- stale-ok: deployBudgetSeconds, deploySingleHopBudgetSeconds -->
pair to tune any more - both fields are gone, along with the
`Deploying`-phase deploy-supervision backstop they bounded. Every stage now
carries the **same three-clock family** (admission, readiness, work), armed
by which timestamps are set, against a per-stage budget. There is no per-edge
deadline field left to forget. See `reference/task-stages.md` for the full
transition table, the per-stage budgets, and the three-clock mechanics.

---

## Where these values live

| Project | Values file |
|---|---|
| `tatara` (self-hosting) | `tatara-helmfile/values/project-tatara/common.yaml` |
| `infrastructure` (GitLab) | `tatara-helmfile/values/project-infrastructure/common.yaml` |
| Operator deployment itself (image tag, replica count) | `tatara-helmfile/values/tatara-operator/common.yaml` |

Flow for any change on this page:

1. Edit the relevant `common.yaml` in a `tatara-helmfile` branch.
2. Open a PR. CI posts a sticky `helmfile diff` comment showing the exact
   rendered change.
3. Review the diff, merge to `main`.
4. The in-cluster ARC runner applies automatically - no manual `helmfile
   apply`, no `kubectl` mutation.

This is the only sanctioned path. See [Deployment & GitOps](deployment.md) for
the full pipeline and rollback story.
