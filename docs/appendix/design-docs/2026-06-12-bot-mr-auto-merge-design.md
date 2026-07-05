# Bot MR auto-merge on green pipeline - design (2026-06-12)

## Problem

When the tatara operator opens a PR/MR for a bot-authored change (issue ->
implement -> `OpenChange`), nothing merges it when its pipeline goes green. The
existing path only merges when the hourly `mrScan` cron re-picks the PR and spins
up a full `selfImprove` agent pod, and even then `writeBackSelfImprove` withholds
the merge (no requeue) if CI is still pending. Result: bot PRs sit open, merges
are slow and agent-expensive.

Goal: when the operator opens a bot MR and its pipeline succeeds, the forge
merges it to the default branch automatically, and the linked issue closes.

## Decision: SCM-native auto-merge

Use the forge's own "merge when checks pass" feature rather than an operator poll
loop:

- GitHub: `enablePullRequestAutoMerge` (GraphQL), `mergeMethod: SQUASH`.
- GitLab: `PUT /merge_requests/{iid}/merge` with `merge_when_pipeline_succeeds=true`.

The forge holds the PR open and merges once required checks pass. No operator
polling, no deadline logic in the operator, minimal code. Rejected: operator
CI-poll-then-merge (more code, redundant with a feature the forge already has).

## Trigger / knob

Reuse the existing `Project.spec.scm.mergePolicy` field. Its `autoMergeOnGreenCI`
value already means "merge when CI is green"; map it to "enable native
auto-merge at PR-open time". `afterApproval` (the current live value) is
unchanged - bot PRs keep flowing through the agent review/merge path. Opt-in per
project; one knob, no new field.

The live `tatara` Project flips `mergePolicy: afterApproval` ->
`autoMergeOnGreenCI`.

## Components

### Part A - operator code (`tatara-operator`)

1. `OpenChange` gains a trailing `autoMerge bool` parameter on both the `Client`
   and `SCMWriter` interfaces (`internal/scm/scm.go`) and both implementations.
   When `autoMerge` is true, after the create call succeeds, enable native
   auto-merge **best-effort**: log at WARN and record an SCM-error metric on
   failure, but never return an error from `OpenChange` for it (the PR/MR open
   is the critical result; the hourly `selfImprove` path remains a fallback).
   - GitHub (`internal/scm/github.go`): decode `node_id` from the create-PR
     response; call `enablePullRequestAutoMerge(input:{pullRequestId:<node_id>,
     mergeMethod: SQUASH})` via the existing `ghGraphQL` helper.
   - GitLab (`internal/scm/gitlab.go`): decode `iid` and `sha` from the create-MR
     response; `PUT /projects/{id}/merge_requests/{iid}/merge` with
     `merge_when_pipeline_succeeds=true` and `sha=<sha>`.

2. `internal/controller/writeback.go` `writeBackOpenChange`: pass
   `autoMerge = (proj.Spec.Scm != nil && proj.Spec.Scm.MergePolicy ==
   "autoMergeOnGreenCI")` into `OpenChange`.

3. `writeBackBody` (same file): when the task source is an issue (not a PR -
   `task.Spec.Source != nil && !task.Spec.Source.IsPR && task.Spec.Source.Number
   > 0`), append a `Closes #<number>` line so the merge (native or agent-driven)
   auto-closes the linked issue. The bot PR is opened in the same repo as the
   issue, so the closing keyword resolves. Never added for `selfImprove` (source
   is already a PR).

### Part B - Project CR (infra)

Flip the live `tatara` Project `spec.scm.mergePolicy` to `autoMergeOnGreenCI`.
This Project is reconciled from the infra helmfile; change it at the source of
truth (the `Project` manifest / values the operator chart renders), not by
`kubectl edit`.

### Part C - per-repo GitHub settings (runbook prerequisite)

Native auto-merge no-ops unless the repo allows it AND `main` has a branch
protection rule with at least one required status check (otherwise GitHub merges
immediately, defeating the gate, or the mutation errors). Part C MUST land before
Part A is deployed.

For each of the 6 CI repos (operator, cli, memory, memory-repo-ingester,
claude-code-wrapper, chat):

1. `allow_auto_merge=true` (repo setting).
2. Branch protection on `main` with `required_status_checks.contexts =
   [secscan, lint, test, build, smoke]`, `strict=false`.

Uniformity gap: `tatara-operator`'s `ci.yml` currently has no `smoke` job, so
requiring `smoke` would stall operator PRs forever. Fix: add a `smoke` job to
`tatara-operator/.github/workflows/ci.yml` (build the manager binary, run
`--help` or an equivalent fast boot check, `|| true` where the binary exits
non-zero on `--help`) so it emits the `smoke` check like the other repos. This
also closes a real coverage gap. Before applying Part C, confirm each repo's
`ci.yml` emits all five PR checks; add the missing job to any that do not.

## Data flow

```
issue -> triageIssue agent -> push branch -> writeBackOpenChange
  -> OpenChange(autoMerge = policy==autoMergeOnGreenCI)
       create PR (body has "Closes #N")
       if autoMerge: enable native auto-merge (best-effort)
  -> forge waits for required checks -> merge to main -> issue closes
```

## Error handling

- Enable-auto-merge failure: WARN log + SCM-error metric, PR stays open, hourly
  `selfImprove` path is the fallback. No reconcile error.
- Branch protection absent (Part C not applied): the GraphQL/GitLab call errors;
  handled as above. This is why Part C is a hard prerequisite.
- `afterApproval` projects: `autoMerge=false`, behaviour identical to today.

## Testing (Part A, TDD)

- GitHub `OpenChange(autoMerge=true)`: httptest server asserts the create POST
  then a GraphQL POST whose body contains `enablePullRequestAutoMerge` and the
  decoded node id; `autoMerge=false` asserts no GraphQL call.
- GitHub enable-auto-merge failure (GraphQL 4xx): `OpenChange` still returns the
  URL and a nil error (best-effort).
- GitLab `OpenChange(autoMerge=true)`: asserts the create POST then a
  `PUT .../merge` with `merge_when_pipeline_succeeds=true`; `autoMerge=false`
  asserts no PUT.
- `writeBackBody`: issue source -> body contains `Closes #<n>`; PR source ->
  body does not.
- `writeBackOpenChange`: table test that `autoMerge` is derived from
  `MergePolicy` (passes true only for `autoMergeOnGreenCI`).

## Out of scope

- Operator-side merge deadline / give-up timer (forge holds the PR open
  indefinitely; revisit if stale bot PRs become a problem).
- CI-fail-on-main -> auto-issue (ROADMAP improvement #2, separate spec).
- Reacting to merge conflicts on bot PRs (separate; the agent review path still
  covers conflicted PRs via the hourly scan).
- Per-repo all-issues fan-out (ROADMAP improvement #1).

## Deploy ordering

1. Part C (repo settings + operator `smoke` job) - merge + apply first.
2. Part A merges to operator `main` -> CI publishes image + chart (same pipeline
   as the in-flight ingress deploy).
3. Part B (Project CR flip) ships with / after the operator chart bump.
