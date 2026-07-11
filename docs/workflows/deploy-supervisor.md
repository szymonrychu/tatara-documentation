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

## Component 1 - significance declaration (the only agent/MCP touch)

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
close-and-Done path with no `semver:*` label and no auto-merge. This is the **only** change to the
agent-facing surface; all tag/merge/propagation logic lives in CI and the operator, not in any
generic superpowers skill.

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

The pattern applies to local/human development too: a human (or any agent) declares significance
via a `semver:*` label on their own PR and gets the same downstream automation - auto-merge is
bot-gated, so a human still merges their own PR by hand, but everything from tag-cut onward
(propagation, deploy-train, apply, issue-close) is identical either way. Agents never self-merge;
that is a hard rule in every component repo's `CLAUDE.md`.

## Reference

| Field | Type | Default | Description |
|---|---|---|---|
| `change_significance` (MCP param on `change_summary`) | enum `major\|minor\|patch` | required, no default | Declared by the implementing agent; gates PR opening and auto-merge |
| `Task.Status.ChangeSummary.Significance` | string | - | Operator-side landing spot for the declared value |
| `semver:<level>` PR label | string | - | Additive label stamped on the PR; author-agnostic trigger for tag-cut/propagation |
| `spec.deployBudgetSeconds` (Project CR) | int | `3300` (multi-hop), `2100` (single-hop override) | `Deploying`-phase deadline before Park/reroll |
| `cd/deploy-train` branch (tatara-helmfile only) | - | - | Shared coalescing branch for terminal-hop bumps |
| `cdScan` | cron activity | peer cadence to `issueScan`/`mrScan` | Backstop that catches stalled cascades with no live watcher |
