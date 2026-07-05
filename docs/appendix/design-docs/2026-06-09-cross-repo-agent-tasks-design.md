# Cross-repo agent tasks - design

**Date:** 2026-06-09
**Status:** approved
**Repos touched:** `tatara-operator`, `tatara-claude-code-wrapper` (memory/ingester unchanged)

## Problem

A Task targets one `RepositoryRef`; the wrapper clones only that repo, and the
operator opens one PR on it. The agent does not know the Project's other repos
exist, so a cross-repo issue (change spanning `tatara-memory` + `tatara-cli`,
say) cannot be handled. The cross-repo *understanding* already exists (M2
cross-repo code-graph, queryable via the agent's memory MCP tools); the gaps are
(a) the agent is not told the other repos exist, (b) only the primary repo is
cloned, and (c) write-back is single-repo.

## Decision

Clone every Project repo into the agent's workspace and deliver changes as one
branch + PR per changed repo. Both forks chosen by the user: clone-all-upfront,
per-repo branch + PR.

## A. Workspace layout

- The wrapper clones every Project repo into `/workspace/<repo>/` (each its own
  git, `tatara/task-<task>` checked out from its default branch).
- The agent's session config (`CLAUDE.md`, `.mcp.json`, `.claude/skills`) is
  written to `/workspace` (the parent, OUTSIDE every repo); claude runs from
  `/workspace`.
- Bonus: this retires the `.git/info/exclude` scaffolding-exclusion (0.1.4) -
  config no longer lives inside any repo, so it cannot be staged into a commit.
  The exclude code is removed.
- The Task's `RepositoryRef` is the "primary" repo (where the issue lives); it is
  named in the prompt and is the issue the operator comments on.

## B. Operator -> pod

- The operator already lists all `Repository` CRs for the Project (webhook path).
  It passes the full set to the pod as one JSON env var:
  `TATARA_REPOS=[{"name":...,"url":...,"branch":...},...]` (primary first).
  Cloning is driven entirely by `TATARA_REPOS`. `REPO_URL`/`REPO_BRANCH` remain as
  the primary repo's identity (the prompt's "primary repo" and the marker for
  which clone failure is fatal); `TASK_BRANCH` is unchanged (one branch name
  reused across all repos).
  - Rationale for JSON env over a mounted ConfigMap: the operator builds the pod
    spec directly (not via helm values), the list is small (a handful of repos),
    and the wrapper parses one env var. No ConfigMap lifecycle to manage.
- `planTurnText` gains: "Primary repo: `<X>` (the issue's repo). All project
  repos are cloned under `/workspace/<name>`. Make changes in whatever repos the
  issue requires; each repo you touch gets its own PR on branch
  `tatara/task-<task>`."

## C. Wrapper enforcement (per-repo)

- Bootstrap: for each repo in `TATARA_REPOS`, clone into `/workspace/<name>` and
  `checkout -b TASK_BRANCH`. Global git credential helper + identity set once
  (unchanged). Best-effort: a non-primary repo whose clone fails is logged and
  skipped; primary-repo clone failure fails bootstrap.
- `OnTurnDone`: iterate the repo dirs under `/workspace`; for each with staged or
  unstaged changes, commit + push `TASK_BRANCH`. Replaces the single-repo
  `CommitAndPush` with a loop over repos (same per-repo logic: `add -A`, commit
  if staged, push). Best-effort per repo; a push failure logs and does not drop
  the turn callback.

## D. Operator write-back (per-repo PRs)

- After the turn loop completes, for each Project repo the operator checks via
  the SCM API whether `tatara/task-<task>` exists and differs from the repo's
  default branch; for each such repo it opens a PR (same scmToken, same org).
- It collects all opened PR URLs, comments the primary repo's issue with the full
  list, and records the URLs on the Task status (extend the existing single-PR
  status to a list).
- If no repo changed, no PRs (current behavior). The primary repo gets a PR only
  if it actually changed (cross-repo issues may touch only other repos).

## E. Memory

Unchanged. The cross-repo code-graph (`cross_repo_symbols`, `/code/cross-repo`)
is already queryable through the agent's tatara-cli memory MCP tools; the agent
uses it to understand the cross-repo landscape.

## Error handling

- Clone-all is best-effort: non-primary clone failure is logged + skipped;
  primary clone failure fails the task (as today).
- Per-repo push failure in `OnTurnDone`: logged, does not drop the turn callback.
- Write-back per repo is independent: one repo's PR failure does not block the
  others; failures are recorded on the Task status.

## Testing

- **Wrapper:** multi-repo clone into `/workspace/<name>` and per-repo enforce-push
  via a fake `GitRunner` over several repo dirs (assert each changed repo is
  committed + pushed, unchanged repos are not). Config-outside-repos asserted (no
  `.mcp.json`/`.claude` inside any repo's git). `TATARA_REPOS` parsing.
- **Operator:** `TATARA_REPOS` present on the pod spec (all project repos, primary
  first); multi-repo write-back opens a PR per changed repo and comments the
  issue with all links, via a fake SCM that reports branch existence per repo.

## Out of scope

- Cross-repo decomposition into Subtasks (the agent does multi-file/multi-repo
  work directly in one turn today; the subtask path remains available but is not
  required).
- Linking the per-repo PRs to each other beyond the issue comment.
