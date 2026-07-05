# Semver push-CD for tatara

Date: 2026-06-28
Status: design (approved shape, pending spec review)
Scope: cross-repo. Per-component implementation specs are produced during
writing-plans. Originating issue: szymonrychu/tatara-helmfile#87.

## 1. Goal and non-goals

### Goal
Replace the hand-edited short-SHA deploy pins (a pull / freshness model) with a
push model:

1. An implementation agent's PR auto-merges to `main` once its build is green.
2. On merge to `main`, a semver git tag is cut from the change significance the
   agent declared.
3. The new tag auto-propagates into the parent repo's pin, which re-enters the
   same auto-merge -> tag -> propagate loop one level up.
4. The cascade terminates at `tatara-helmfile`, which auto-applies to the
   cluster on push-to-`main`.
5. On a successful cluster apply, the operator closes the originating issue.

This closes issue #87's merged-but-not-deployed gap by construction: a merged
fix always reaches the cluster, and pins are never hand-edited or left to rot.

### Non-goals (v1)
- Automated verification of the agent's declared significance (Go exported-symbol
  diff / CRD-schema diff). Logged as a follow-up; v1 trusts the agent's claim.
- A new CI substrate. The 6 code repos stay on GitHub Actions; we do NOT migrate
  them onto the argo-events `cwt-*` pipeline (which only `tatara-argo-workflows`
  uses).
- Re-architecting how `tatara-memory` is deployed. It stays image-only /
  operator-provisioned (no published chart); only its image tag becomes semver.

## 2. Decisions (locked)

| # | Decision | Choice |
|---|----------|--------|
| D1 | How far automation rides | Fully autonomous: auto-merge -> auto-tag -> auto-propagate -> auto-apply to cluster. No human gate. |
| D2 | Undeclared significance | Block: an agent cannot open a PR without declaring (required MCP field). |
| D3 | Pipeline scope | 6 code repos (operator, cli, wrapper, memory, ingester, chat) + pin `tatara-agent-skills` -> wrapper. |
| D4 | Trust vs verify the claim | Trust the agent's declared significance for v1. Verification deferred. |
| D5 | Auto-merge gate | `semver:*` label present AND PR author == bot (`szymonrychu-bot`). Human PRs never auto-merge. |
| D6 | Propagation topology | Decentralized: each repo knows only its parent + the pin pattern there. Labels propagate the cascade. |
| D7 | Reuse | One composite action `cd-release` + tested `semver-bump.py`, hosted once, called by every repo's `release.yml`. |
| D8 | Agent lifecycle | Implement Task stays alive through deploy (operator-driven, pod-less), budget 1.2x worst-case cascade time, fixes failures. |
| D9 | Issue closure | Operator closes the originating issue on successful `tatara-helmfile` apply. |
| D10 | Deploy dedup | Convergent helmfile bumps coalesce into one deploy-train PR; applies serialize; a per-Project deploy ledger dedups the watch so N Tasks share one watcher and resolve in one sweep. |

## 3. Architecture

### 3.1 The version lever
Image and chart versions today come from `git describe --tags --always` (per
`build.sh` / `ci-shared.yml`). This degrades to a bare short-SHA ONLY because
operator/wrapper/cli/ingester/chat carry no semver tags. Seed each repo with a
semver tag and the existing version mechanism emits semver with no rework. The
redesign computes the next version explicitly from the merged PR's label and
overrides the publish version, then records it as a git tag (so the next cycle's
`git describe` and the human-visible release history stay consistent).

This eliminates the three fragmented chart schemes (`0.0.0-g<sha>`,
`0.0.0-<sha>`, static-unpublished) and the leading-zero `g`-prefix hack
(`chart-version-semver-leading-zero-sha-2026-06-23`).

### 3.2 The dependency graph (consumer pins producer)
```
cli ------> wrapper ------> helmfile (terminal -> cluster)
skills ---> wrapper --------^
operator -----------------> helmfile
memory -------------------> helmfile   (image pin, operator-provisioned)
ingester -----------------> helmfile   (image pin, operator-provisioned)
chat ---------------------> helmfile   (chart pin)
```
Deepest chain: `cli -> wrapper -> helmfile` (2 tag-cut hops). The graph is
small and stable; each repo encodes only its own outgoing edge.

### 3.3 End-to-end flow (single change)
1. Agent implements, pushes a branch, calls `change_summary` WITH
   `change_significance`.
2. Operator opens the PR (bot-authored), stamps `semver:<level>`, enables native
   GitHub auto-merge.
3. PR's required checks go green -> GitHub squash-merges to `main`.
4. `release.yml` (push-to-main): build+publish image (+chart) as the computed
   `vX.Y.Z`; `cd-release tag` reads the merged PR's `semver:*` label, computes
   the next version, pushes the git tag.
5. `cd-release bump <parent>` pattern-rewrites the parent's pin, opens a
   bot-authored PR labeled `semver:patch`, enables auto-merge. Parent re-enters
   step 3.
6. At `tatara-helmfile`, the bump PR auto-merges on a green `helmfile diff`;
   push-to-main runs `helmfile apply` to the cluster.
7. Operator deploy-supervision observes the apply succeed, marks the Task `Done`,
   and closes the originating issue with a deployed-version comment.

## 4. Component 1 - significance declaration (tatara-cli MCP, the only agent touch)

Extend the existing tatara-scoped `change_summary` tool
(`tatara-cli/internal/mcp/tools.go`, currently ~507-537) with a REQUIRED enum:

```
change_significance: "major" | "minor" | "patch"
```

- `major` = backward-incompatible change (API break, CRD-incompatible change,
  removed/renamed public surface).
- `minor` = backward-compatible new functionality.
- `patch` = fix or anything else.

Reconcile the prompt vocabulary (`breaking`) to `major`. Because the field is
REQUIRED, an agent cannot produce a change summary - and therefore cannot get a
PR opened - without declaring (this is D2, enforced at the lowest layer).

`change_significance` (snake) maps to `changeSignificance` (camel) per the
existing Build mapping convention, POSTs to the operator REST endpoint, lands in
`Task.Status.ChangeSummary.Significance` (new field,
`tatara-operator/api/v1alpha1/task_types.go`).

This is the ONLY change to the agent/MCP surface. All tag/merge/propagation
logic lives in CI + operator. Generic superpowers skills are untouched, per the
"do not pollute skills/mcp with tatara-specific CD logic" requirement. The
`change_significance` declaration is the single tatara-mcp tool update the user
asked for.

## 5. Component 2 - operator: label + bot-gated auto-merge

In writeback (`tatara-operator/internal/controller/writeback.go`):

1. After `OpenChange` succeeds, set the PR label `semver:<significance>` via the
   existing `EnsureLabel`/SCM machinery. Add `semver:major|minor|patch` to the
   managed-label palette (additive, NOT phase labels).
2. Land the experimental `.worktrees/scm-auto-merge` `EnableAutoMerge()`
   (`github.go` GraphQL `enablePullRequestAutoMerge`). Enable auto-merge ONLY
   when both hold (D5):
   - `Task.Status.ChangeSummary.Significance` is set (=> label present), AND
   - the PR author is the bot (`szymonrychu-bot`) - reuses the bot-authorship
     egress gate (`scm-author-vs-actor-egress-gate`).
3. If significance is absent, do NOT open the PR / do NOT enable auto-merge;
   re-prompt the agent to declare (reuse the existing implement re-prompt path,
   `implement-false-refusal-rootcause`).

Post-rollout, implement agents must NOT self-merge: the `pr_outcome=merge` path
is removed/guarded for implement kind (auto-merge owns merging). `pr_outcome` is
retained for `close`/decline.

Tag-cut and propagation are AUTHOR-AGNOSTIC (they key on the `semver:*` label,
not the author), so a human merging a labeled PR by hand still triggers the full
downstream automation; only the merge step itself is bot-gated.

## 6. Component 3 - reusable `cd-release` composite action (D7)

Hosted once at `tatara-helmfile/.github/actions/cd-release/` (the CD hub; the
only purely-CD repo). Referenced by every repo's `release.yml` as
`uses: szymonrychu/tatara-helmfile/.github/actions/cd-release@main`. Two modes:

### mode: tag
Inputs: `token`. Steps:
1. Find the merged PR for the pushed commit (`gh pr list --search <sha>
   --state merged`).
2. Read its `semver:*` label (fail loudly if absent on a non-propagation
   commit).
3. `next = semver-bump.py <latest_tag> <level>` (latest =
   `git tag --sort=-v:refname | head -1`, seeded - see 10.1).
4. `git tag v<next> && git push origin v<next>`.
5. Output `version=<next>` for the publish + bump steps.

### mode: bump
Inputs: `token`, `parent_repo`, `pins` (JSON list of `{file, pattern,
value_template}`), `new_value`, `pr_label` (default `semver:patch`). Steps:
1. Clone the parent repo (bot token).
2. For each pin: pattern-rewrite in place (regex or yaml-path; NOT line numbers,
   which drift). A repo with multiple pins rewrites them all in ONE commit
   (operator's 4 pins, wrapper's 2 helmfile pins, cli's 3 build-args) for
   atomicity.
3. Open a bot-authored PR, apply `pr_label` (`semver:patch` - a dependency bump
   is a patch by definition), enable auto-merge.

`semver-bump.py`: pure function `(latest_tag, level) -> next_tag`, table-driven
tests (major resets minor+patch, minor resets patch, patch increments). Lives
beside the action.

Each repo therefore encodes only its outgoing edge (D6); the action is the
single source of bump+label logic.

## 7. Component 4 - per-repo `release.yml` and the parent map

Each of the 6 code repos gains a `release` job on push-to-`main`, gated on
build+publish success. It runs `cd-release tag`, publishes artifacts at the
computed version, then `cd-release bump <parent>`.

| repo | parent | pin(s) in parent (rewrite by pattern) | artifact |
|------|--------|----------------------------------------|----------|
| cli | wrapper | `Dockerfile` `ARG TATARA_CLI_VERSION`, `Makefile`, `build.sh` (all 3, lockstep - fixes existing drift) | image/binary, no chart |
| skills | wrapper | wrapper `TATARA_SKILLS_REF` default (closes the floating `main` ref) | plugin, no chart |
| wrapper | helmfile | `values/project-tatara/common.yaml` + `values/project-infrastructure/common.yaml` wrapper image (both, lockstep) | image + chart |
| operator | helmfile | `helmfile.yaml.gotmpl` operator chart + project-tatara chart + project-infrastructure chart + `values/tatara-operator/common.yaml` image tag (all 4, atomic) | 2 charts + image |
| memory | helmfile | `values/tatara-operator/default.yaml` `memoryImage` | image only (operator-provisioned) |
| ingester | helmfile | `values/tatara-operator/default.yaml` `ingesterImage` | image only (operator-provisioned) |
| chat | helmfile | `helmfile.yaml.gotmpl` chat chart | chart |

Notes:
- Image tag scheme stays DUAL: publish `:vX.Y.Z` (what pins move to) AND keep
  `:SHORT_SHA` for rollback/debug traceability.
- Charts publish bare `X.Y.Z` (drop `0.0.0-*`). `tatara-memory` continues to
  publish NO chart.
- operator: propagation is gated on BOTH the tatara-operator AND tatara-project
  charts publishing, to prevent the partial-publish wedge
  (`operator-ci-partial-chart-publish-2026-06-25`).
- cli/skills -> wrapper bumps re-trigger a wrapper build (the wrapper build-guard
  that verifies the pinned cli advertises required MCP tools can hard-fail the
  cascade; that failure is handled by deploy-supervision, section 8).
- `tatara-helmfile` is terminal: no `release.yml`, no tag, no propagation. Its
  "build" gate for auto-merging incoming bump PRs is a green `helmfile diff`;
  push-to-main runs `helmfile apply`.

## 8. Component 5 - deploy-supervision and resiliency (D8, D9)

The implement Task does not go terminal at PR-merge; it stays alive through
deployment, driven by the operator (not the agent pod), so it survives the agent
pod dying.

### 8.1 Deploying phase
After the implement PR auto-merges, the Task enters a new `Deploying` phase:
- POD-LESS: no agent pod runs; the operator reconcile loop polls cascade state.
  Because no pod runs, `Deploying` RELEASES the per-repo execution lane (it must
  be excluded from `laneOccupancy`, or it re-creates the lane-starvation trap of
  `operator-laneoccupancy-starves-recovery-2026-06-15`). It re-acquires a lane
  only to spawn a fix agent.
- Tracks the cascade: this repo tagged -> parent bump PR opened -> parent merged
  + tagged -> ... -> `tatara-helmfile` apply succeeded for the commit that
  carries this Task's artifact version.
- Deploy-detected signal (plan to confirm): poll the `tatara-helmfile`
  `apply.yaml` run conclusion for the bump commit AND/OR confirm the live
  workload now runs the new version (belt-and-suspenders for operator-provisioned
  memory/ingester, which the operator applies itself).

### 8.2 Budget
The `Deploying` deadline = `1.2 x worst-case cascade time`, where worst-case =
sum of the per-stage p95 durations along the longest path from this repo to
`tatara-helmfile`-applied (build, publish, merge, per hop, plus the final
apply). Defaults specified in the plan; tunable on the Project CR. On exceeding
the deadline: Park (recoverable, reason `deploy-timeout`) -> recovery (8.3).

### 8.3 Failure -> fix
Any cascade stage failing - a red required check, a failed build, a bump PR that
will not merge, the wrapper build-guard rejecting a cli bump, a `helmfile apply`
error - transitions the Task to recovery: adopt-in-place or spawn an implement
agent scoped to FIX that stage, with the failing repo + job + logs in the
prompt. Reuses the bounded-reroll machinery
(`refine-recover-gaveup-implementations-2026-06-28`,
`boot-crash-budget-burn-stale-pod-2026-06-27`): bounded rerolls, then escalate
(incident / maintainer).

### 8.4 Backstops (agent died AND operator missed an event)
- `cdScan`: an operator cron (peer of `issueScan`/`mrScan`) that finds cascades
  stalled past a threshold with no live watcher and opens a fix Task.
- A `tatara_cd_cascade_failed` (and `..._stalled`) metric -> Grafana alert
  (tatara-observability) -> investigation Task, mirroring the existing
  internal-issue escalation path.

### 8.5 Issue closure (D9)
On the cascade reaching `tatara-helmfile` apply success, the operator:
1. Transitions the Task to `Done`.
2. Closes the originating issue (the Task already links it) with a comment:
   `Deployed <repo>@vX.Y.Z, applied via tatara-helmfile@<sha>.`
   Reuses existing issue-close egress (`issue_outcome=close` / lifecycle close).

The watch (8.1) and resolution here are deduplicated per section 8a - N Tasks
converging on the same apply share one watcher and resolve in one sweep.

## 8a. Component 5b - deploy dedup and coalescing (D10)

Every component cascade terminates at the SINGLE `tatara-helmfile` repo. Under
load, many Tasks (across repos) reach `Deploying` near-simultaneously and would
otherwise: open N conflicting bump PRs (memory and ingester both edit
`values/tatara-operator/default.yaml`; operator/wrapper touch overlapping
files), trigger N racing `helmfile apply` runs, and have N Tasks redundantly
polling the same runs ("sink together and wait for the same pipelines"). Three
mechanisms address this:

### 8a.1 Coalesce bump PRs into a deploy train (terminal hop only)
`cd-release bump` into `tatara-helmfile` is CREATE-OR-UPDATE against a single
well-known branch `cd/deploy-train`, not a fresh branch per bump. Each component
adds/updates ONLY its own pin as a separate commit on that branch (so a bad pin
can be reverted without dropping the others), labels the PR `semver:patch`, and
enables auto-merge. A burst of N component tags becomes N commits on ONE PR ->
one merge -> one apply. Non-terminal parents (wrapper, fan-in of only cli +
skills) keep independent PRs; conflicts there are rare and handled by
rebase-retry in `cd-release bump`.

### 8a.2 Serialize applies
`tatara-helmfile` `apply.yaml` runs under a GitHub Actions
`concurrency: group=cd-apply, cancel-in-progress=false` so applies queue rather
than race or supersede each other. At most one cluster apply in flight.

### 8a.3 Deduplicate the watch (per-Project deploy ledger)
A per-Project deploy ledger (ConfigMap CAS, modelled on the existing
`SeqSource` counter, `agent-work-queue-leader-only-webhook-2026-06-20`, and the
Task work-item ledger, `task-work-item-ledger-shipped-2026-06-24`) records each
`Deploying` Task as `{artifact, version, sourceTaskRef, issueRef}`. ONE operator
reconcile polls the helmfile apply outcome; on success it matches the applied
pin versions against ledger entries and resolves EVERY matching Task in a single
sweep (Done + close issue, 8.5). N Tasks => one watcher => one resolution pass.
No thundering-herd polling.

This composes with the existing implement-agent dedup
(`systemic-impl-dedup-lead-per-repo-2026-06-23`, lead-per-repo for one
brainstorm's issues): that dedups WORK; this dedups DEPLOY. Distinct layers.

### 8a.4 Failure isolation under coalescing
If a coalesced apply fails, deploy-supervision (8.3) identifies the offending
pin(s) (which component's diff/apply regressed), reverts only those commits from
`cd/deploy-train`, rerolls only those components' Tasks, and lets the train
re-apply with the good pins. The valid bumps are not held hostage by one bad
one.

## 9. Component 6 - local development and agent-behavior docs

This pattern applies to LOCAL development too: a human (or any agent) declares
significance via a `semver:*` label, pushes a branch, and lets CI cut the tag +
propagate. Humans merge by hand (auto-merge is bot-gated) but get the same
downstream automation. Nobody hand-edits a deploy pin.

Doc updates (SEQUENCED AT CUTOVER - see 10.3; landing them before auto-merge
exists would freeze all merges):
- Project `CLAUDE.md` (and the copy in every component repo): add a CD section -
  agents NEVER self-merge; declare `change_significance`; the pipeline merges,
  tags, propagates, deploys, and closes the issue.
- Project `MEMORY.md` + each component `MEMORY.md`: record the push-CD model and
  the no-self-merge rule.
- A local memory entry capturing the design intent (written at design time).

## 10. Rollout (ordered, gated)

### 10.1 Prerequisites (one-time, before any auto-merge)
- Seed tags: tag operator, wrapper, ingester, chat, skills at
  `max(existing tags, static Chart.yaml/plugin version)` so `cd-release tag` has
  a base. cli (`v0.4.x`) and memory (`v0.1.x`) already tagged - reconcile to not
  go backwards.
- Branch protection + "Allow auto-merge" on `main` for all 7 repos; required
  check = each repo's CI (`helmfile diff` for helmfile).
- Harbor retention policy protecting `vX.Y.Z` / `X.Y.Z` tags from GC (the
  recurring `chart-not-found` class); SHORT_SHA tags keep churning.
- `TATARA_CD_TOKEN` (bot PAT, exists) available to release jobs for cross-repo
  PR creation.

### 10.2 Build order
1. `cd-release` action + `semver-bump.py` + tests (tatara-helmfile).
2. tatara-cli: `change_significance` on `change_summary`.
3. tatara-operator: `Task.Status.ChangeSummary.Significance`, REST handler,
   writeback label + bot-gated `EnableAutoMerge`, `Deploying` phase +
   deploy-supervision + per-Project deploy ledger (watch dedup) + `cdScan` +
   issue-close + lane exclusion.
4. tatara-helmfile: `cd/deploy-train` coalescing in `cd-release bump`, apply
   `concurrency: cd-apply` serialization.
5. Per-repo `release.yml` (all 6) + parent configs.
6. tatara-observability: `tatara_cd_cascade_failed` / `..._stalled` alert.
7. Doc/cutover (10.3).

### 10.3 Cutover (the only ordering that matters)
Auto-merge and the no-self-merge doc rule must land TOGETHER: enable bot-gated
auto-merge in the operator deploy, THEN (same rollout) update CLAUDE.md/MEMORY.md
to forbid agent self-merge. Reverse order freezes merges; doing the docs first
without auto-merge means nothing merges at all.

## 11. Risks accepted (per D1 + D4) and follow-ups

- Fully autonomous + trust => a mislabeled agent PR can ship to prod with no
  human gate and no verification. Mitigated only by: required declaration,
  build-must-be-green, bot-author gate, and (operator `major`) the fact that
  CRDs are chart-templated so `helm upgrade` migrates them
  (`operator-crd-templating-helm-adoption-2026-06-20`). A CRD removal /
  incompatible change is still unverified.
- FOLLOW-UP (deferred): automated significance verification - Go exported-symbol
  diff and CRD-schema diff that confirm/override the agent's claim before
  tagging; `archfitness` import-graph already exists as a starting point.
- FOLLOW-UP: consider a `major`-only human gate if autonomous breaking deploys
  prove too risky in practice.

## 12. Open items for the plan
- Confirm writeback only opens a PR when `ChangeSummary.Significance` is present
  (exact gating).
- Confirm the `TATARA_SKILLS_REF` default location in the wrapper (build arg vs
  runtime env) for the skills->wrapper bump target.
- Confirm whether `tatara-ingester` publishes a chart consumed anywhere (the
  helmfile pins it as an image); if the chart is vestigial, stop publishing it.
- Pick the authoritative deploy-detected signal in 8.1 (helmfile Actions run
  conclusion vs live-workload version vs a helmfile apply webhook back to the
  operator - tatara-helmfile is enrolled).
- Compute the per-stage p95 defaults for the 1.2x budget.
