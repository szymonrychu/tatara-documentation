# Design: Implement false-refusal root-cause fix (helmfile#8)

Date: 2026-06-23
Status: approved, ready for planning
Repos touched: tatara-operator, tatara-cli, tatara-claude-code-wrapper
Deploy: GitOps via tatara-helmfile (cli -> wrapper -> operator)

## Problem

Issue `szymonrychu/helmfile#8` (platform-wide pre-commit hook freshness,
filed in the hub repo, fix spans helmfile + terraform + ansible) ended with
the bot posting:

> The implement agent produced no change after 2 attempts and did not explain
> why via decline_implementation. Leaving this for a human.

Investigation proved the agent did NOT refuse. It implemented; the result was
lost and a duplicate task produced the false comment. Four distinct defects
stacked.

### Evidence

- `scan-qe-cmx57` resultSummary: edited terraform + ansible, skipped helmfile
  ("already at baseline"). Those two repos have ZERO branches / ZERO MRs on
  `tatara/fix-8-...` -> the cross-repo work was lost.
- `scan-qe-ppqnx` opened MR !1197 with the helmfile bumps (correct slice).
- 17 `issueLifecycle` Task CRs exist for issue #8 (12 Parked, 3 Stopped, 2
  Conversation), all sharing pod `tatara-infrastructure-gl-helmfile-issue-8`
  and branch `tatara/fix-8-...`.
- `cmx57` ran after `ppqnx` had pushed helmfile changes to the shared branch,
  found nothing to commit, and parked `refused-no-explanation` at 12:16.

## Root causes

**A. Cross-repo writeback is discover-after-the-fact, never declared.**
Operator clones ALL project repos into the pod
(`task_controller.go:358` `ensurePodAndService` -> `projectRepos()` ->
`BuildPod` serializes `TATARA_REPOS`). Wrapper pushes any repo with a diff
(`CommitAndPushAll`, `bootstrap.go:62-74` -> `repo.go:47-59`). Writeback opens
an MR per project repo whose branch landed on the remote
(`writeback.go:146-213`); a 422 "no commits" is skipped silently
(`writeback.go:171`). The agent is never told which repos are in scope (turn
prompts in `turnloop.go` never mention multi-repo work) and nothing returns
which repos it touched. Net: sibling edits can silently never land, and the
operator cannot distinguish "agent skipped on purpose" from "agent meant to
but didn't."

**B. dedupKey frees on Park, so rescans respawn duplicates.**
`TaskTerminal` returns true for `LifecycleState=="Parked"`
(`task_types.go:176-182`). `dedupExists` only blocks on non-terminal
QueuedEvents/Tasks (`enqueue.go:56-84`), so once a task parks its dedupKey is
released. The operator's own park comment advances the issue `updatedAt`,
which flips the activity-vs-creation gate in `isDeduped`
(`projectscan.go:151-187`, gate at line 182) to false, so the next
`issueScan` enqueues a fresh Task with the identical dedupKey. All tasks for
an issue share one pod name (`BuildPodName`, `pod.go:195-209`) and one branch
(`TaskBranch`, `pod.go:327-342`).

**C. Empty finish is misread as refusal; no "already done" signal and decline
accepts whitespace.** When a respawned duplicate checks out the shared branch
the fix is already committed, so the agent produces no diff. `finishImplement`
(`lifecycle.go:1427-1468`) only takes the codified-refusal path when
`outcome.Action=="declined"` AND `TrimSpace(reason)!=""`; otherwise it falls
to the empty-retry cap (`lifecycle.go:1470-1515`) and parks with the false
`refused-no-explanation`. Two compounding gaps:
1. The only legal `Action` is `declined` (`task_types.go:63` enum) - there is
   no way to say "already present, nothing to do".
2. The CLI decline tool checks `argString(a,"reason")==""`
   (`tools.go:532-534`) not `TrimSpace`, so a whitespace reason passes the
   CLI, gets 400'd by the operator (`handlers.go:847-849`), and the agent's
   decline is silently dropped while the wrapper never propagates the failure.

## Fixes

### Defect B - duplication (operator only)

**B1 (adopt the Parked task).** In `createScanTask`
(`projectscan.go:329-368`), before `EnqueueEvent`, look up an existing
lifecycle Task for `(repo, number)` including Parked; if found, re-enter
Triage on that task (reuse pod/branch) rather than creating a second Task. Add
`hasLiveOrAdoptableTask()` returning `*Task`. One task per issue forever; the
shared branch/pod becomes a feature.

**B3 (bot comments do not advance the rescan activity gate).** Make
`isDeduped`'s activity gate (`projectscan.go:182`) ignore bot-authored
comments so the operator's own park/discuss comments never re-trigger a scan.
Consistent with the `brainstorm-no-repeat-comment` and `bot-has-last-word`
memories.

Ship both. B1 is the structural fix; B3 is a cheap correctness guard that
should land regardless.

### Defect C - false refusal: already-done signal + decline validation (operator + cli + wrapper)

- Enum `declined;already_done` (`task_types.go:63`); REST handler accepts both
  via a `map[string]bool` (`handlers.go:843-846`); both require a non-empty
  trimmed reason.
- `finishImplement` (`lifecycle.go:1432`) takes the codified-terminal path for
  `declined || already_done` and skips the empty-retry loop entirely.
- CLI: `strings.TrimSpace(argString(a,"reason"))==""` (`tools.go:532-534`) so
  whitespace is rejected client-side with a clear, correctable message. Apply
  the same trimmed-reason validation to the `already_done` action.
- Wrapper: on a non-200 from a critical outcome tool (decline / already_done),
  re-prompt the agent with the failure rather than letting it finish silently
  (`app.go` / `mcp_register.go`).
- Split giveup metric labels: `refused-declined` / `refused-already-done` /
  `refused-no-explanation` (`lifecycle.go:1459`) so this class stops
  collapsing into one bucket.

This is the literal "forbid declines without explanation": a reason is
mandatory and trimmed at every layer (cli, operator) and a rejected outcome is
propagated back to the agent (wrapper) instead of being silently dropped.

### Defect A - cross-repo writeback (operator + wrapper + cli)

**A1 (declarative scope).**
- Add optional `Task.Spec.ReposInScope []string` (`api/v1alpha1/types.go`).
  Absent field = today's single-repo behavior (no regression).
- Operator injects the in-scope repo list into the turn prompt
  ("this issue spans repos: X, Y, Z; edit and push every one you change") via
  `turnloop.go` / `implementPrompt`.
- Wrapper returns the list of repos it actually pushed (annotate via the
  existing change-summary / MCP path, `CommitAndPushAll` site in `app.go`).
- Writeback: for a repo that is in scope but has no branch, post a WARNING
  comment on the issue instead of silently skipping (`writeback.go:146-213`).
  No all-or-nothing atomicity (out of scope, KISS) - other MRs still open.

## Design decisions (resolved)

- Cross-repo scope: declarative optional `ReposInScope` field (A1). Absent =
  single-repo behavior.
- In-scope-but-unmodified repo: WARNING comment, do not block other MRs.
- Duplicate handling: adopt + re-triage the Parked task (B1); bot comments
  excluded from the rescan activity gate (B3).
- Action name: `already_done`.
- `already_done` requires a non-empty trimmed reason (auditability, matches
  `implement-refusal-codified`).
- On MCP outcome-tool 400: re-prompt the agent with the validation error, fall
  through to the existing cap if it keeps failing.
- Separate giveup metrics: yes.

## Implementation order

Four parallel worktrees; operator slices merge sequentially (opus per
hard-rule 7).

- **WT-1 (operator): Defect B** - adoption (B1) + bot-comment activity gate
  (B3). Self-contained. Highest priority: stops the duplicate storm at source.
- **WT-2 (operator + cli): Defect C** - `already_done` enum/handler/lifecycle
  + CLI TrimSpace + metric labels.
- **WT-3 (wrapper): Defect C wrapper half + Defect A wrapper half** - MCP
  critical-tool failure propagation/re-prompt, and `CommitAndPushAll`
  returning the pushed-repo list. Co-located (both touch `app.go` /
  `mcp_register.go`).
- **WT-4 (operator): Defect A operator half** - `ReposInScope` CRD field,
  prompt injection, writeback in-scope-warn.

Operator merge order (smallest blast radius first): B (WT-1) -> C-operator
(WT-2) -> A-operator (WT-4), rebasing each. CRD changes in WT-2 (`already_done`
enum) and WT-4 (`ReposInScope`) both touch the CRD; templated CRDs apply on
helm upgrade (`operator-crd-templating-helm-adoption`).

## Deploy (GitOps, hard-rule 15)

1. **cli first** (WT-2 cli half) - merge to cli main, CI builds image. The
   wrapper `TATARA_CLI_VERSION` pin bump must follow and pass the
   tokenless-MCP build-guard (`wrapper-cli-pin-needs-tokenless-mcp`).
2. **wrapper** (WT-3) - merge after the cli image exists; CI builds image.
3. **operator** (WT-1 + WT-2 + WT-4 merged) - single image once all three
   operator slices are on main.
4. **tatara-helmfile MR** bumping BOTH chart version AND pinned `image.tag`
   for operator (`tatara-operator-deploy-chart-version-and-image-tag`,
   `tatara-helmfile-dual-chart-pin-and-cr-adoption`) plus the wrapper image
   tag; reviewed via diff, applied by the pipeline.

## Verification (per verify-fix-via-downstream-not-draining-pods)

- One lifecycle Task per issue after a freshly-rescanned multi-comment issue
  (no new dupes).
- An in-scope sibling repo with no edits produces a warning comment, not
  silence.
- An agent calling `already_done` parks `refused-already-done`, not
  `refused-no-explanation`.
- A whitespace decline reason is rejected client-side and the agent corrects,
  rather than being silently dropped.

## Files

- operator: `internal/queue/enqueue.go`,
  `internal/controller/{projectscan.go,lifecycle.go,writeback.go,turnloop.go,task_controller.go}`,
  `internal/agent/pod.go`, `internal/restapi/handlers.go`,
  `api/v1alpha1/{task_types.go,types.go}`
- cli: `internal/mcp/tools.go`
- wrapper: `cmd/wrapper/app.go`, `internal/bootstrap/{bootstrap.go,mcp_register.go}`
