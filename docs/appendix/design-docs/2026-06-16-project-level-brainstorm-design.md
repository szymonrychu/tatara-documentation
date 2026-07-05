# Project-level brainstorm + healthCheck (drop repo pinning)

Date: 2026-06-16. Component: tatara-operator (operator-only).

## Problem

`brainstorm` and `healthCheck` are already PROJECT-scheduled (one Task per cycle,
project-wide in-flight guard + proposal-backlog cap) but each Task is pinned to a
deterministic `primaryRepo` (`TaskSpec.RepositoryRef = primaryRepo`, pod named
`...-<repo>-brainstorm`) while the pod checks out ALL repos via `TATARA_REPOS`. So
project-wide work is modeled as a repo-scoped Task. Make them genuinely
project-scoped.

## Decision

Approach A: empty `RepositoryRef` means project scope. Operator-only; the wrapper
already supports an empty `REPO_URL` and clones from `TATARA_REPOS`
(bootstrap.go:158 guards `if p.RepoURL != ""`; tests cover empty RepoURL driving
Repos[0]). No wrapper change, one PR, one deploy.

Rejected: B (new `TaskSpec.Scope` field - extra CRD surface + second source of
truth); C (sentinel ref - overloads the field, violates KISS).

## Changes (tatara-operator)

1. `api/v1alpha1/task_types.go`: `RepositoryRef` becomes `+optional` +
   `json:"repositoryRef,omitempty"`. Keep `ProposedIssueSpec.RepositoryRef`
   required (proposals always target a repo).
2. CRD validation: `RepositoryRef` required for repo-scoped kinds
   (implement/review/selfImprove/triageIssue/issueLifecycle), MUST be empty for
   `brainstorm`/`healthCheck`. Enforce in the existing validating path (webhook or
   reconcile guard, matching current conventions) + regenerate the CRD manifest
   (`make manifests`).
3. `internal/controller/projectscan.go`: `brainstorm()` and `healthCheck()` drop
   `primaryRepo` selection. They still iterate repos for the backlog-cap count,
   issues-context, and slug list, but create the Task with NO repo.
   `createBrainstormTask`/`createHealthCheckTask` lose the `*Repository` param; set
   `RepositoryRef=""`; call `StampPodName(task, proj.Name, provider, "")`. Update
   log fields (drop `primary_repo`). The `no valid repos` guard stays (still need at
   least one repo to build a goal/context).
4. `internal/controller/task_controller.go` (~line 202): when
   `task.Spec.RepositoryRef == ""` (project-scoped kind), skip the single-repo Get;
   load all project repos (existing all-repos query used for `TATARA_REPOS`) and
   call `BuildPod(project, nil, task, allRepos, ...)`.
5. `internal/agent/pod.go`: `BuildPod` with `repo == nil` omits `REPO_URL` /
   `REPO_BRANCH` and sets `TATARA_REPOS` to all repos (deterministic name sort, no
   primary-first). `BuildPodName`/`StampPodName` render `...-<project>-brainstorm`
   (or `-healthCheck`) when `repoRef == ""`.
6. `internal/controller/writeback.go`: `brainstorm` already returns at the
   propose_issue branch (no PR, no repo Get). Confirm `healthCheck`'s writeback case
   is equally repo-independent; if it Gets a primary repo, make it match brainstorm.
7. Accounting: brainstorm/healthCheck in-flight + budget are keyed by Kind/project,
   not repo, so empty `RepositoryRef` does not affect `laneOccupancy`/
   `atConcurrencyCap`. Verify the repo field-index path tolerates empty ref (those
   Tasks simply do not appear in the per-repo index, which is correct).

## Testing (TDD)

- `projectscan`: brainstorm + healthCheck create a Task with empty `RepositoryRef`
  and a project-scoped pod name; backlog cap / in-flight guard unchanged.
- `pod`: `BuildPod(nil repo)` emits no `REPO_URL`/`REPO_BRANCH`, `TATARA_REPOS` lists
  all repos; `BuildPodName("")` is project-scoped.
- CRD/validation: empty ref accepted for brainstorm/healthCheck, rejected for
  repo-scoped kinds; non-empty ref rejected for brainstorm/healthCheck.
- writeback: brainstorm + healthCheck complete with no PR and no repo dependency.
- Full envtest controller suite green (KUBEBUILDER_ASSETS via setup-envtest 1.33.0).

## Out of scope

issueScan/mrScan stay per-repo (a PR/issue lives in one repo). No wrapper change.
No deploy-value change (agentRunAsUser/agentScheduling persist).
