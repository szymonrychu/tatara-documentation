---
title: Semver Push-CD
---

# Deploy Supervisor & Semver Push-CD

Push-CD replaces hand-edited short-SHA deploy pins with a fully autonomous push model. It is
**not an agent kind** - it is an operator-only supervisor loop that takes over once `review` has
approved a PR and required checks are green. No agent pod runs during any of this: `implement`
declares a change's significance, `review` approves it, the deploy supervisor auto-merges once
required checks are green, CI cuts a semver tag from the declared significance, the tag cascades
up through parent-repo pins, and the cascade terminates at `tatara-helmfile`, which auto-applies
to the cluster. On a successful apply, the operator closes the originating issue. The only
per-Task state this touches is the umbrella Task's `lifecycleState` field (Go struct field
renamed to `DeployState`; the JSON/CRD wire key itself is unchanged - see
[Task reference](../reference/task.md#deploy-supervision-only-status-fields)),
not a new object.

Design: `docs/superpowers/specs/2026-06-28-semver-push-cd-design.md`. Cutover runbook:
`docs/superpowers/plans/2026-06-28-semver-push-cd-cutover-runbook.md`.

!!! note "Status: cascade proven end-to-end (as of 2026-07-05)"
    Push-CD is live and the full cascade has run in production. The operator
    self-deployed `v0.4.11` through its own semver trains (`#108`-`#112`), the
    coalescing `cd/deploy-train` branch exists on `tatara-helmfile`, and the live
    `tatara-helmfile` operator pin is a semver tag (`v0.4.11`), not a short-SHA.
    Automated tags are cut from significance declarations, not just the one-time
    seed tags. The only genuinely one-time prerequisites remain the cutover
    runbook's seeding steps - initial `vX.Y.Z` seed tag per repo, the
    `TATARA_CD_TOKEN` secret, branch-protection/auto-merge settings, and Harbor
    retention - which you run once per repo when enrolling it into the cascade,
    not on every deploy.

## End-to-end flow

```mermaid
sequenceDiagram
    participant Agent as Implement agent
    participant Op as Operator
    participant CI as Repo CI (release.yml)
    participant Parent as Parent repo
    participant HF as tatara-helmfile
    participant Cluster

    Agent->>Op: change_summary(change_significance=major|minor|patch)
    Op->>Op: Task.Status.ChangeSummary.Significance set
    Op->>CI: Open bot-authored PR, label semver:<level>,\nenable native auto-merge
    CI-->>Op: Required checks green -> GitHub squash-merges to main
    Op->>Op: Task enters Deploying (pod-less)
    CI->>CI: release.yml: build+publish vX.Y.Z,\ncd-release tag pushes git tag
    CI->>Parent: cd-release bump: rewrite pin(s),\nopen bot PR labeled semver:patch, auto-merge
    Parent->>Parent: re-enters same auto-merge -> tag -> bump loop
    Parent->>HF: cd-release bump into cd/deploy-train\n(coalesced, create-or-update)
    HF->>HF: helmfile diff green -> auto-merge -> apply.yaml\n(concurrency: tatara-helmfile-apply, serialized)
    HF-->>Cluster: helmfile apply
    Cluster-->>Op: Apply success observed (deploy ledger)
    Op->>Op: Resolve every matching Task in one sweep
    Op->>Agent: Task Done; originating issue closed with\n"Deployed <repo>@vX.Y.Z, applied via tatara-helmfile@<sha>"
```

## Component 1 - significance declaration (implement's agent/MCP touch)

`change_significance` is a **required** enum parameter on the existing `change_summary` MCP tool
(`tatara-cli`): `major | minor | patch`.

- `major` - backward-incompatible change (API break, CRD-incompatible change, removed/renamed
  public surface).
- `minor` - backward-compatible new functionality.
- `patch` - fix, or anything else.

Because the field is required, an agent cannot record a change summary without declaring it: a
missing or invalid value is rejected (HTTP 400) at the operator's REST handler. The value maps
`changeSignificance` -> `Task.Status.ChangeSummary.Significance`. Declaring significance is what
makes a merged change **push-CD-eligible** (`pushCDEligible`); it does **not** gate PR opening. An
`implement` PR whose agent never recorded a significance still opens - the operator
logs a WARN (`writeback_no_significance`) and routes the merged change down the legacy
close-and-Done path with no `semver:*` label and no auto-merge. This is `implement`'s only touch
on the agent-facing surface for significance - it only covers MRs `implement` opens itself.
`review` carries a second, complementary touch (Component 1b below) that stamps per-MR labels on
approve, including for MRs `implement` never opened. Past those two declarations, all
tag/merge/propagation logic lives in CI and the operator, not in any generic superpowers skill.

## Component 1b - review semver stamping (human MRs)

`change_significance` only covers MRs `implement` opens itself. A human- or maintainer-authored
MR in the same stream never calls `change_summary`, so before this it carried no `semver:*` label
from anyone, and `cd-release` refused to cut a tag for it - the change merged but never deployed.
`review`, on `approve`, closes this gap: it assigns a per-MR `semver:<level>` label to **every**
MR in the stream, human and tatara-created alike, via `ReviewVerdict.Semver`
(`[]SemverAssignment{Repo, Number, Level}` on the `review_verdict` MCP call).

- **Per-MR level**, judged from that MR's own diff: breaking change -> `major`, backward-compatible
  new functionality -> `minor`, fix/docs/other -> `patch`. One stream can mix levels across its
  member MRs.
- **Respects an existing human `semver:*` label** - never overwritten; a deliberately human-set
  level is authoritative. This also makes the pass idempotent for bot MRs the operator already
  labeled at PR-open time (Component 2 below).
- **Falls back**, for an unlabeled tatara-authored MR, to that MR's own `change_significance` from
  `change_summary`, then to `patch`.
- **Best-effort**: applied across every stream member in the approve writeback; a labeling failure
  on one member never blocks the `approve` verb itself.
- **Sole stamping path for human MRs** - without it, a human-authored MR never gets a `semver:*`
  label and the push-CD pipeline never cuts a tag for it.

See [Review workflow](review.md#semver-labeling-on-approve) for the full rubric from the agent
side.

## Component 2 - bot-gated auto-merge

After the PR opens, the operator (`writeback.go`, `applySemverAutoMerge`):

1. Stamps the PR label `semver:<significance>` (additive, not a phase label).
2. Enables native GitHub auto-merge (GraphQL `enablePullRequestAutoMerge`) **only when both**
   hold: `Task.Status.ChangeSummary.Significance` is set, **and** the PR author is the bot
   (`szymonrychu-bot`).
3. If significance is absent, `applySemverAutoMerge` returns early: the PR **still opens** (a WARN
   `writeback_no_significance` is logged), but it gets no `semver:*` label and no auto-merge, and
   the merged change takes the legacy close-and-Done path (`pushCDEligible=false`). There is no
   writeback-layer re-prompt.

Post-rollout, `implement` agents never self-merge. `pr_outcome` remains profiled as a
`selfImprove`-only MCP tool (a kind now retired), so it plays no part in the live merge path;
even where it is reachable, `pr_outcome=merge` no longer merges directly - it defers to native
auto-merge (the bot PR had auto-merge enabled at open time, so the forge squash-merges once
required checks pass). Auto-merge owns merging, gated additionally on `review`'s `tatara-approved`
label; `pr_outcome=close` is retained for declines.

Tag-cut and propagation are **author-agnostic** - they key on the `semver:*` label, not on who
merged. A human merging a labeled PR by hand still triggers the full downstream cascade; only the
initial merge step itself is bot-gated.

## Component 3 - the dependency graph (consumer pins producer)

```
cli ------> wrapper ------> helmfile (terminal -> cluster)
skills ---> wrapper --------^
operator -----------------> helmfile
memory -------------------> helmfile   (image pin only, operator-provisioned)
ingester -----------------> helmfile   (image pin only, operator-provisioned)
chat ---------------------> helmfile   (chart pin)
```

Deepest chain: `cli -> wrapper -> helmfile` (two tag-cut hops). Every repo encodes only its own
outgoing edge - propagation is decentralized, not driven by a central orchestrator. A dependency
bump PR is always labeled `semver:patch` (a dependency moving is a patch by definition);
component significance does not propagate past the first hop.

## Component 4 - `cd-release` composite action

One reusable composite action, hosted once at `tatara-helmfile/.github/actions/cd-release/`,
called by every repo's `release.yml`:

- **mode `tag`:** find the merged PR for the pushed commit, read its `semver:*` label, compute
  the next version from the latest clean `vX.Y.Z` tag, push the new tag.
- **mode `bump`:** clone the parent repo, pattern-rewrite each pin (regex/yaml-path, not line
  numbers), open (or update) a bot-authored PR labeled `semver:patch`, enable auto-merge.

## Component 5 - deploy-supervision and the `Deploying` phase

The Task's `lifecycleState` (not `phase`) tracks the cascade after PR merge; the Task does not go
terminal at merge. It enters a new **`Deploying`** phase, driven by the operator reconcile loop,
not by an agent pod:

- **Pod-less:** no agent pod runs while `Deploying`. Because there is no pod, `Deploying` must be
  excluded from per-repo lane occupancy (it would otherwise starve recovery); a lane is only
  re-acquired if a fix agent needs to spawn.
- **Tracks the cascade:** this repo tagged -> parent bump PR opened -> parent merged and tagged
  -> ... -> `tatara-helmfile` apply succeeded for the commit carrying this Task's artifact
  version.
- **Budget:** `deployBudgetSeconds` on the Project CR (default `3300`s multi-hop, `2100`s
  single-hop override) is the `Deploying`-phase deadline, sized as roughly 1.2x the worst-case
  cascade time. Exceeding it Parks the Task (`deploy-timeout`, recoverable).
- **Failure -> fix:** any cascade stage failing (red required check, failed build, a bump PR that
  won't merge, a build-guard rejection, a `helmfile apply` error) transitions the Task to
  recovery - adopt in place, or spawn an implement agent scoped to fix that specific stage. This
  reuses the same bounded-reroll machinery and `maxImplGiveUps` cap as [refine's Parked-implement
  recovery](refine.md#recovering-gave-up-implementations).
- **`cdScan` backstop:** an operator cron, peer of `issueScan`/`mrScan`, that finds cascades
  stalled past the budget with no live watcher and rerolls or reparks them for a human.
- **Issue closure:** on the cascade reaching a successful `tatara-helmfile` apply, the operator
  transitions the Task to `Done` and closes the originating issue with
  `Deployed <repo>@vX.Y.Z, applied via tatara-helmfile@<sha>`.

## Component 5b - deploy dedup and coalescing

Every cascade terminates at the single `tatara-helmfile` repo, so bursts of concurrent deploys
are coalesced rather than raced:

- **Deploy-train:** the terminal-hop `cd-release bump` is create-or-update against a single
  well-known branch `cd/deploy-train`, not a fresh branch per bump. Each component adds/updates
  only its own pin as a separate commit on that branch, so a bad pin can be reverted without
  dropping the others. A burst of N component tags collapses into N commits on one PR -> one
  merge -> one apply.
- **Serialized applies:** `tatara-helmfile`'s `apply.yaml` runs under a GitHub Actions
  `concurrency: group=tatara-helmfile-apply, cancel-in-progress=false` group, so at most one
  cluster apply is in flight at a time. (The group name diverges from the design spec's
  `cd-apply`; the existing name is kept - the property that matters is the serialized,
  non-cancelling queue.)
- **Per-Project deploy ledger:** a ConfigMap-CAS ledger records every `Deploying` Task as
  `{artifact, version, sourceTaskRef, issueRef, headSHA, state}`. One operator reconcile polls
  the apply outcome and matches it against ledger entries, resolving **every** matching Task in a
  single sweep (Done + issue-close) - N converging Tasks share one watcher, not N pollers.
- **Failure isolation:** if a coalesced apply fails, deploy-supervision identifies the offending
  pin(s), reverts only those commits from `cd/deploy-train`, rerolls only those components'
  Tasks, and lets the train re-apply with the remaining good pins.

## Humans in this flow

The pattern applies to local/human development too, and since Component 1b a human no longer has
to manually label their own PR for it to release: `review` stamps a `semver:<level>` label on
every MR in an approved stream, human-authored MRs included. A human can still set `semver:*` on
their own PR ahead of review - review respects and never overwrites an existing human label - but
it is no longer required for the release tag to get cut. Everything from tag-cut onward
(propagation, deploy-train, apply, issue-close) is identical either way. Auto-merge stays
bot-gated, so a human still merges their own labeled PR by hand; agents never self-merge - that is
a hard rule in every component repo's `CLAUDE.md`.

## Reference

| Field | Type | Default | Description |
|---|---|---|---|
| `change_significance` (MCP param on `change_summary`) | enum `major\|minor\|patch` | required, no default | Declared by the implementing agent; gates PR opening and auto-merge |
| `Task.Status.ChangeSummary.Significance` | string | - | Operator-side landing spot for the declared value |
| `ReviewVerdict.Semver` (MCP field on `review_verdict`) | `[]SemverAssignment{Repo, Number, Level}` | none, best-effort | Per-MR `semver:<level>` assigned by review on approve, for every MR in the stream; respects an existing human label, falls back to that MR's `change_significance` then `patch`; sole labeling path for human MRs (Component 1b) |
| `semver:<level>` PR label | string | - | Additive label stamped on the PR; author-agnostic trigger for tag-cut/propagation. Stamped by writeback at PR-open for bot MRs with declared significance, and by review on approve for every MR in the stream |
| `spec.deployBudgetSeconds` (Project CR) | int | `3300` (multi-hop), `2100` (single-hop override) | `Deploying`-phase deadline before Park/reroll |
| `cd/deploy-train` branch (tatara-helmfile only) | - | - | Shared coalescing branch for terminal-hop bumps |
| `cdScan` | cron activity | peer cadence to `issueScan`/`mrScan` | Backstop that catches stalled cascades with no live watcher |
