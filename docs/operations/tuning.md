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

Set `maxConcurrentTasks: 0`. No new Task pods are admitted - every scan,
brainstorm, and webhook-triggered event still queues (`QueuedEvent`s
accumulate) but nothing spawns until the value is raised again. This is the
single project-wide kill switch; it does not touch cron schedules or delete
queued work.

```yaml
# values/project-tatara/common.yaml
project:
  spec:
    maxConcurrentTasks: 0
```

To reduce load without a full stop, lower `maxConcurrentTasks` to a smaller
positive number, or tune `queue.capacity` / `queue.alertCapacity` directly if
you need the normal-pool and alert-pool (incident) concurrency to diverge from
`maxConcurrentTasks`.

---

## Disable a specific flow

Every cron-driven activity (`mrScan`, `issueScan`, `cdScan`) is off when its
`schedule` is empty. `brainstorm` and `healthCheck` additionally require
`enabled: true` - clearing `enabled` (or leaving it unset) disables them
regardless of `schedule`.

| To disable | Set |
|---|---|
| MR/PR review scan | `scm.cron.mrScan.schedule: ""` |
| Issue scan | `scm.cron.issueScan.schedule: ""` |
| Push-CD deploy-supervision backstop | `scm.cron.cdScan.schedule: ""` |
| Brainstorm (self-driven proposals) | `scm.cron.brainstorm.enabled: false` |
| Health check (periodic tech-debt survey) | `scm.cron.healthCheck.enabled: false` |

`scm.cron.refine` has no independent schedule - it fires as a mandatory
barrier before every due scan/brainstorm cycle and cannot be disabled short of
removing all of mrScan/issueScan/brainstorm/healthCheck schedules.

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

Three independent levers, from broadest to narrowest:

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

### 2. Model/effort tiering per kind

Drop specific Task kinds to a cheaper model or lower reasoning effort while
keeping the project-wide fallback high for everything else:

```yaml
agent:
  model: claude-opus-4-8
  effort: high
  modelByKind:
    triageIssue: claude-sonnet-5
    review: claude-sonnet-5
  effortByKind:
    triageIssue: low
    review: medium
```

Keys are limited to the nine `Task.Spec.Kind` values (`implement`, `review`,
`triageIssue`, `brainstorm`, `issueLifecycle`, `incident`, `selfImprove`,
`refine`) plus the `healthCheck` pseudo-key (healthCheck Tasks carry
`Kind=brainstorm` but resolve against `healthCheck` first, falling back to the
`brainstorm` entry). A missing or empty entry falls back to the project-wide
`model`/`effort`. Both live projects currently tier only `triageIssue` and
`review` to Sonnet; every other kind stays on Opus at `high` effort.

### 3. Per-task runaway backstop (`maxTaskTokens`)

`agent.maxTaskTokens` is a cumulative output-token ceiling for the
otherwise turn-uncapped `implement`/`issueLifecycle` kinds. It is a safety
backstop against a looping agent, not a cost-tuning lever - `0` (default)
disables it. Both live projects run `maxTaskTokens: 3000000`. Tune it from
observed per-kind token telemetry once a healthy-run distribution is known,
not preemptively.

---

## Tune push-CD deploy budgets

`deployBudgetSeconds` (default `3300`) and `deploySingleHopBudgetSeconds`
(default `2100`) bound how long a `Deploying`-phase Task waits for its
push-CD cascade to reach a `tatara-helmfile` apply before the operator parks
it recoverable with reason `deploy-timeout`:

- `deployBudgetSeconds` covers the longest path (2 tag-cut hops, e.g.
  `cli -> wrapper -> helmfile`).
- `deploySingleHopBudgetSeconds` is the tighter deadline for artifacts one hop
  from `tatara-helmfile` (operator, memory, ingester, chat) with no
  intermediate parent rebuild.

```yaml
project:
  spec:
    deployBudgetSeconds: 3300
    deploySingleHopBudgetSeconds: 2100
```

`scm.cron.cdScan.schedule` controls the backstop sweep that catches cascades
stalled past 1.5x these budgets with no live watcher and rerolls them (parks
recoverable, or re-implements orphans). Widen the cron interval to reduce
sweep frequency; widen the budgets themselves if a component's build/test/
deploy pipeline legitimately takes longer than the default assumes.

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
