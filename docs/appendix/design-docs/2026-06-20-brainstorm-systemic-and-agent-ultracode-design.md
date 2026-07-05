# Cross-repo-aware brainstorm + ultracode agents - design

Date: 2026-06-20
Status: approved (brainstorm), plan + implementation pending
Repos: `tatara-operator` (primary), `tatara-claude-code-wrapper`, `tatara-cli`

## Problem

The autonomous brainstorm/implementation loop is repo-myopic and produces low-grade
artifacts:

1. **Brainstorm is blind to live repo state.** `brainstorm()` /
   `healthCheck()` (`internal/controller/projectscan.go`) feed the agent ONLY
   open issues (`buildIssuesContext`, cap 60). The agent never sees open
   MRs/PRs, their pipeline status, or `main`-branch CI health, so it cannot
   reason about systemic, cross-repository problems (the same failure across N
   repos, a missing CI step platform-wide, recurring debt) and cannot manage or
   dedup the existing issue/MR backlog across repos.
2. **Agents run at default effort, single-threaded.** The wrapper launches
   `claude` with `--model` only (`internal/session/pty.go`); there is no
   reasoning-effort lever and no instruction to decompose work or fan out
   subagents. Deep cross-repo synthesis needs maximum effort + orchestration.
3. **One issue per improvement.** The brainstorm goal hard-caps "Exactly one
   action per run"; `propose_issue` is single-issue. A genuinely systemic
   improvement that needs one work item per affected repo cannot be expressed.
4. **Weak titles.** PR titles fall back to `firstLine(task.Spec.Goal)` (the
   issue/PR body's first line) when the agent does not call `change_summary`;
   agent-supplied titles are unvalidated. This produced a bare `Go` MR title in
   the `infrastructure` GitLab project.
5. **Opaque branch names.** Work branches are `tatara/task-<task-name>`
   (`internal/agent/pod.go` `TaskBranch`), carrying no issue number; impossible
   to correlate a branch to its issue at a glance.

## Goals

- Brainstorm/healthCheck survey live state across ALL repos: open issues, open
  MRs/PRs with CI status, and `main`-branch pipeline health, then reason about
  cross-repo systemic improvements and manage/dedup the backlog.
- Brainstorm + implementation agents run at maximum reasoning effort
  ("ultracode") and are instructed to orchestrate via parallel subagents /
  Workflow.
- A single systemic improvement can open multiple, cross-linked issues (one per
  affected repo) without exhausting the proposal cap.
- Issue and PR/MR titles are always meaningful and descriptive.
- Work branches are named from the originating gl/gh issue number.

## Non-goals

- Auto-closing issues the bot does not own. Dedup *management* is limited to
  linking/labelling/commenting; closing others' issues is too sensitive and
  stays out of scope.
- Changing the deploy path (tatara-helmfile GitOps) or the queue/admission
  model.
- Reactive remediation from CI failures (separate roadmap item).

## Decisions (locked)

| # | Decision |
|---|----------|
| D1 | Multi-issue model = **cross-linked + correlation label**. Agent calls `propose_issue` N times with a shared `systemicId`; operator stamps label `tatara/systemic-<id>` and a sibling footer. A systemic group counts as **1** against `maxOpenProposals`. No epic CRD. |
| D2 | Agent "ultracode" = **effort knob + orchestration instructions** (both). |
| D3 | Branch format = `tatara/<kind>-<issueN>-<slug>`; fallback `tatara/task-<name>` when no issue number is on the Task. |
| D4 | Weak agent-supplied titles are **rejected at egress** so the agent retries that same turn; the no-agent PR-open fallback **derives** a quality title (cannot re-prompt). |
| D5 | Dedup management = link/label/comment only (see Non-goals). |

## Work-streams

### WS1 - Brainstorm reads live repo state + cross-repo systemic [operator]

**New SCM method.** `SCMReader.GetDefaultBranchHeadSHA(ctx, owner, repo) (string, error)`
(`internal/scm/scm.go`), implemented in `github.go` (GET default branch ref) and
`gitlab.go` (GET project default branch / commits), plus the test fakes. Paired
with the existing `GetCommitCIStatus(owner, repo, sha)` to yield per-repo
`main` pipeline health. (Existing methods cannot resolve a branch HEAD to a SHA;
this is the only missing primitive.)

**Context gathering.** In `brainstorm()` and `healthCheck()`, after the existing
per-repo `ListOpenIssues` pass (already cached in `issuesBySlug`), gather per
repo, bounded:
- Open MRs/PRs via `ListOpenPRs(owner, name)` (already exists), each annotated
  with its CI status via the reader-side `GetCommitCIStatus(owner, name,
  pr.HeadSHA)` (`ListOpenPRs` already populates `PRRef.HeadSHA`, so no
  SCMWriter/`GetPRState` dependency). Cap the per-PR CI lookups (e.g. first 20
  PRs per repo by recency; beyond the cap list the PR without CI status).
- `main`-branch CI via `GetDefaultBranchHeadSHA` + `GetCommitCIStatus`.

**New builder.** Replace `buildIssuesContext` with `buildRepoStateContext`,
emitting three blocks the goal references:
- ISSUES: existing format `repo#N [labels] title` (keep the 60 cap and
  `[bot-engaged]` marking).
- OPEN MRs: `repo!N [ci:<status>] title` (provider-correct `#`/`!`).
- MAIN HEALTH: one line per repo, `repo main CI: <status>`.
All three reuse already-fetched data where possible; the MR/CI/main reads are
the only added round-trips, all non-fatal (a failed read degrades that line, not
the cycle).

**Goal rewrite.** `brainstormGoalProject` / `healthCheckGoalProject` gain an
explicit cross-repo-systemic mandate: survey the three blocks, prefer the
single highest-leverage SYSTEMIC opportunity (a pattern spanning >=2 repos, a
platform-wide gap, or recurring debt) over a one-repo tweak; manage/dedup the
backlog (link/label/comment related existing issues per D5). The existing
dedup-first contract and `[bot-engaged]` rule are preserved. The "exactly one
action" clause is replaced per WS3.

### WS2 - Agent ultracode [wrapper + operator]

**Wrapper effort knob.** `internal/session/pty.go` `claudeArgs()` appends
`--effort <level>` when a new config field `cfg.Effort` is non-empty (CLI flag
is the most version-robust headless lever). `cmd/wrapper/config.go` reads
`EFFORT` env. `internal/bootstrap/settings.go` `writeSettings` also writes
`"effortLevel": <level>` into `~/.claude/settings.json` (belt-and-suspenders).
The plan MUST verify, against the pinned `claude` version, that:
- the `--effort` flag / `effortLevel` key are accepted (else fall back to the
  env var `CLAUDE_CODE_EFFORT_LEVEL`);
- the existing `PERMISSION_MODE` auto-approves `Agent`/`Workflow` dispatch so a
  headless PTY turn does not hang on an approval prompt;
- the settings.json tool allow-list (if restrictive) permits `Agent` and
  `Workflow`.

**Operator effort field.** `Project.Spec.Agent` gains `Effort string`
(`+kubebuilder:validation:Enum=low;medium;high;xhigh;max`, default `xhigh`).
`internal/agent/pod.go` `BuildPod` passes it as `EFFORT` env (mirrors `MODEL` /
`PERMISSION_MODE`).

**Orchestration instructions.** The brainstorm + implement (issueLifecycle
Implement) goals instruct the agent to run at max effort, decompose the
cross-repo survey, dispatch one parallel subagent per repo, and use Workflow to
fan out then synthesize. The orchestration guidance is also folded into the
baked `tatara-deep-research` and `tatara-health-check` skills (wrapper image) so
it survives prompt edits.

### WS3 - Multiple issues per systemic improvement [operator + cli]

**propose_issue gains `systemicId`** (optional string). cli `internal/mcp/tools.go`
adds it to the `propose_issue` schema and forwards it in the REST payload;
operator REST handler threads it onto `Task.Spec.ProposedIssue.SystemicID`.

**Operator stamps correlation.** `createProposal` (`internal/controller/writeback.go`):
when `SystemicID != ""`, add label `tatara/systemic-<id>` to the created issue
and append a footer to the body: `Part of systemic improvement <id> spanning:
<repo list>` (the repo list is known at propose time; siblings are discoverable
via the shared label - no second pass, no hard `#N` back-refs needed).

**Cap grouping.** `proposalBacklogCount` groups open proposals by their
`tatara/systemic-<id>` label: each distinct systemic group counts as 1; each
standalone proposal counts as 1. So a systemic improvement's N issues do not
exhaust `maxOpenProposals`.

**Goal change.** The brainstorm goal replaces "exactly one action per run" with:
a one-repo improvement still emits exactly one issue; a genuinely systemic
improvement MAY emit one `propose_issue` per affected repo (bounded, e.g. <=6),
all sharing a `systemicId` the agent generates. The dedup-first paths are
otherwise unchanged.

### WS4 - Meaningful titles [operator + cli]

**`weakTitle(s string) bool`** helper (operator): true when the title is empty,
< 12 chars, <= 2 words, or matches a denylist of bare tokens (`go`, `update`,
`fix`, `change`, `wip`, `misc`, `chore`, language/tool names, etc.). Returns a
guidance string for the rejection.

**Reject agent-supplied titles at egress.** The REST handlers for
`propose_issue` (`title`) and `change_summary` (`pr_title`) validate via
`weakTitle`; a weak title returns 4xx with guidance. cli surfaces the tool error
so the agent retries with a descriptive title in the same turn.

**Derive the no-agent fallback.** At PR-open (`writeBackOpenChange`,
`internal/controller/writeback.go`), when `ChangeSummary.PRTitle` is absent or
weak, derive a conventional title from the captured work-item title
(`Task.Spec.Source.Title`, WS5) + a kind prefix, e.g. `fix(<scope>): <issue
title>`, instead of `firstLine(task.Spec.Goal)`. A weak title is never emitted
on a PR.

### WS5 - Branch named from issue number [operator]

**Capture the work-item title.** `TaskSource` (`api/v1alpha1/task_types.go`)
gains `Title string`, populated at enqueue from the issue/PR title for
webhook-born tasks (`internal/webhook/server.go`) and from the candidate for
scan-born implement/selfImprove tasks (`projectscan.go`). Feeds both the branch
slug (WS5) and the title fallback (WS4).

**Branch derivation.** `TaskBranch(t)` (`internal/agent/pod.go`): when
`t.Spec.Source.Number > 0`, return `tatara/<kind>-<Number>-<slug>` where
`<kind>` maps from the issue's labels / `Task.Spec.Kind` to `fix|feat|chore`,
and `<slug>` is a slugified, truncated `Source.Title`. Else fall back to
`tatara/task-<t.Name>`. The function stays pure and deterministic (all inputs on
the Task); the wrapper consumes `TASK_BRANCH` env unchanged, and the operator's
`writeBackOpenChange` derives the same value, so the wrapper/operator branch
contract is preserved.

## CRD changes

- `Project.Spec.Agent.Effort` (enum, default `xhigh`).
- `TaskSource.Title` (string, optional).
- `Task.Spec.ProposedIssue.SystemicID` (string, optional).

All three ride the now-templated CRD apply path (helm upgrade applies CRDs from
`crd-bases/`, see `[[operator-crd-templating-helm-adoption-2026-06-20]]`); no
manual `kubectl apply` needed, but the deploy MUST bump BOTH the operator chart
version and the pinned `image.tag`
(`[[tatara-operator-deploy-chart-version-and-image-tag]]`).

## Data flow (brainstorm, after)

```
cron brainstorm tick
  -> per repo (bounded): ListOpenIssues (cached) + ListOpenPRs
       + GetPRState.CIStatus (<=20) + GetDefaultBranchHeadSHA + GetCommitCIStatus
  -> buildRepoStateContext: ISSUES / OPEN MRs / MAIN HEALTH blocks
  -> brainstormGoalProject(slugs, ctx): cross-repo systemic mandate + dedup-first
  -> enqueue brainstorm QueuedEvent (Goal, Sources)
  -> agent pod (EFFORT=xhigh): deep-research skill, fan out 1 subagent/repo,
       synthesize systemic finding
  -> propose_issue x N (shared systemicId)   [WS3]
  -> operator createProposal: weakTitle gate [WS4] + systemic label/footer [WS3]
```

## Testing strategy (TDD)

- **scm**: table tests for `GetDefaultBranchHeadSHA` (github + gitlab) against
  recorded fixtures; fake reader returns a SHA.
- **projectscan**: `buildRepoStateContext` block formatting (issues/MRs/main),
  caps, degraded reads; brainstorm goal contains the systemic mandate; cap
  grouping by systemic label.
- **writeback**: `weakTitle` table; systemic label + footer on createProposal;
  PR-title derivation from `Source.Title` when ChangeSummary weak/absent.
- **pod**: `TaskBranch` table (issue-numbered vs fallback; kind mapping; slug).
- **restapi**: propose_issue/change_summary reject weak titles (4xx + guidance);
  `systemicId` threaded to Task.
- **wrapper**: `claudeArgs` includes `--effort` when set; `writeSettings`
  includes `effortLevel`; config reads `EFFORT`.
- **cli**: propose_issue schema carries `systemicId`; payload forwards it.

## Plan split

`docs/superpowers/plans/2026-06-20-brainstorm-systemic-{operator,wrapper,cli}.md`.
Build order: cli (schema) + wrapper (effort) can proceed in parallel with the
operator core; operator depends on its own CRD + scm method first. TDD per WS,
subagent-driven (sonnet impl, opus merge/review). Deploy via tatara-helmfile
after all three mains are green (operator chart+image, wrapper agent.image pin,
cli pin into wrapper).

## Risks

1. Exact effort knob vs the pinned `claude` version (mitigation: `--effort`
   flag + `effortLevel` setting + `CLAUDE_CODE_EFFORT_LEVEL` env; verify in
   plan).
2. Subagent/Workflow dispatch must not hang on an approval prompt under the
   headless PTY (mitigation: confirm PERMISSION_MODE auto-approves; allow-list
   includes Agent/Workflow).
3. Added SCM reads raise per-cycle API cost (mitigation: caps + non-fatal
   degradation; brainstorm/healthCheck are daily, bounded fan-out).
4. CRD field additions require the templated-CRD apply + dual chart/image bump
   on deploy.
