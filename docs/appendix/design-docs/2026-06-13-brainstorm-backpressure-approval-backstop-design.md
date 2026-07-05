# Per-repo backpressured brainstorm + approval backstop

Date: 2026-06-13
Component: tatara-operator (`internal/controller/projectscan.go` scan loop)
Status: approved (design), pending implementation

## Problem

Two gaps in the autonomous scan loop:

1. **Brainstorm is not per-repo and has no backpressure.** Today `brainstorm()`
   creates `MaxPerCycle` generative Tasks against `repos[0]` only, on a daily
   cron (`0 6 * * *`). It ignores the other repos and keeps proposing
   regardless of how many un-triaged proposals already pile up, so a repo can
   accumulate proposals faster than a human approves them.

2. **A missed approval webhook strands an approved proposal.** Approval is
   driven by a human removing the `approvalLabel` from a bot proposal issue; the
   `unlabeled` webhook flips the Task's `ApprovalApproved` condition. If that
   webhook is dropped, the proposal is approved on GitHub/GitLab but its Task
   sits in `AwaitingApproval` forever with no implementation.

## Goal

On an hourly cycle, for every repo in the project:

- If the repo already holds `>= maxOpenProposals` (default 3) open, agent-proposed,
  not-yet-approved issues, skip it.
- Otherwise start exactly one brainstorm session aiming to propose a single,
  well-defined issue. Over successive hours the repo saturates to the cap, then
  pauses until a human approves or closes some.

And, as a backup reconcile: detect proposals that are approved (label removed)
but not progressing (webhook missed) with no implementation running, and start
the implementation flow manually.

## Identity signals (grounded in existing behavior)

- **Agent proposal awaiting approval** == an OPEN, non-PR issue whose labels
  include `spec.scm.approvalLabel` (default `tatara/awaiting-approval`). The
  operator applies this label when it opens a proposal (`createProposal` /
  `propose_issue` flow). A human approves by REMOVING it. Human-filed issues do
  not carry it. So a label filter on the existing `ListOpenIssues` listing is a
  sufficient proxy for "proposed by agents, not closed, not approved" - no
  author lookup needed. (Accepted edge: a human manually applying that label to
  their own issue would also count toward the cap; rare and acceptable.)
- **Approved** == the `approvalLabel` has been removed from the (still open)
  proposal issue. Reused verbatim from the existing webhook approval semantic
  (`internal/webhook/server.go`: `unlabeled` + `ChangedLabel == approvalLabel`).
- **issue <-> Task** mapping via `Task.Spec.Source.Number` and the proposal Task's
  `Spec.ProposedIssue` / `Status` issue ref.

## Component A: per-repo backpressured brainstorm

Replaces the body of `brainstorm()`. Driven on the brainstorm cron, set to
hourly (`0 * * * *`); the per-repo cap, not the cron, is the throttle.

For each repo `r` in `projectReposForScan(proj)`:

1. **In-flight guard.** If any brainstorm Task for `r` is active
   (phase in {Pending, Planning, Running}), skip `r`. Rationale: the live-SCM
   proposal count lags until the brainstorm's agent actually opens the issue;
   without this guard a subsequent hourly cycle would start a second brainstorm
   for the same repo before the first surfaces, defeating the cap. One brainstorm
   per repo at a time.
2. **Backlog count.** `backlog(r)` = number of open, non-PR issues for `r` whose
   labels contain `approvalLabel`, via `ListOpenIssues(owner, repo)` filtered by
   `IsPR == false` and `hasLabel(labels, approvalLabel)`. On a listing error for
   `r`: log non-fatal and skip `r` (do not block other repos).
3. **Cap.** If `backlog(r) >= maxOpenProposals` (new field, default 3), skip `r`
   and record a skipped(reason=cap) metric.
4. **Create.** Otherwise create exactly one brainstorm Task targeting `r`:
   `createBrainstormTask(proj, &r, goal, brainstorm.Sources)` with
   `goal = "Propose a single, well-defined issue for repo <slug>"`. The brainstorm
   agent proposes one issue via the existing `propose_issue` flow.

`maxOpenProposals` is read from `spec.scm.cron.brainstorm.maxOpenProposals`
(default 3 when unset or < 1). `MaxPerCycle` is retired: the rate is fixed at
one per repo per cycle.

## Component B: approval backstop

A new step in the hourly scan reconcile (runs alongside mr/issue scan). For the
project:

1. List the project's implement Tasks in phase `AwaitingApproval` that carry a
   proposal (`Spec.ProposedIssue` set / a Source issue ref).
2. For each, read its proposal issue (label + open/closed state) via the reader.
   - **Approved + open** (label absent, issue open) and `ApprovalApproved`
     condition not already True, AND no implementation already running for that
     issue: flip `ApprovalApproved = True` (RetryOnConflict), so the existing
     gate releases the Task to implement. Record `approval_backstop_flips_total`.
   - Closed issue: webhook normally terminates; backstop may terminate the Task
     (Stopped/Rejected). Optional, lower priority.
3. **Orphan approved issue** (an approved proposal with no Task at all, e.g. the
   Task was GC'd): already recovered by the existing `issueScan` un-tasked-issue
   path - an open issue with no associated Task gets a fresh `issueLifecycle`
   Triage Task on the next issue scan, which re-triages and routes it. No special
   backstop logic needed (detecting "was an approved proposal" without a label
   would require a body-marker scan; not worth it). The backstop's job is only
   the stuck-`AwaitingApproval`-Task case above.

**Hard gate (both paths):** never act if an implementation is already running
for the issue - i.e. an active (non-terminal, non-`AwaitingApproval`) Task in
the project references the same issue number. Prevents double-starting.

## Configuration changes (Project CR)

`spec.scm.cron.brainstorm`:
- `schedule`: `0 6 * * *` -> `0 * * * *` (hourly).
- `maxOpenProposals`: new int, default 3.
- `maxPerCycle`: retired (ignored).

Applied via the infra helmfile raw Project values
(`helmfiles/tatara/values/tatara-operator/raw/project-tatara.*.yaml`).

## Observability

- Gauge `operator_open_proposals{repo}` - current open-proposal backlog per repo
  (set each cycle from the count).
- Counter `operator_scan_items_total{activity=brainstorm,outcome=...}` extended
  with outcomes `skipped_cap`, `skipped_inflight` (reuse existing ScanItem).
- Counter `operator_approval_backstop_flips_total` - approvals recovered by the
  backstop.
- INFO log per brainstorm decision (repo, backlog, action) and per backstop flip
  (task, issue, action).

## Error handling

- `ListOpenIssues` error for a repo -> log non-fatal, skip that repo only.
- Reader/token unavailable -> skip brainstorm + backstop for the cycle (same as
  existing scan behavior).
- `approvalLabel` empty on the project -> the label-based proposal count is
  undefined; treat backlog as 0 is unsafe (would never cap). Require
  `approvalLabel` set for the cap; when empty, fall back to counting the
  project's `AwaitingApproval` proposal Tasks for the repo as the backlog.
- Status flips use `RetryOnConflict` (cache-lag safe, per the documented
  lifecycle conflict-spin lesson).

## Testing (envtest + fake reader)

- backlog < cap and no in-flight brainstorm -> exactly 1 brainstorm Task created
  per under-cap repo, targeting that repo.
- backlog >= cap -> no brainstorm Task for that repo (skipped_cap).
- in-flight brainstorm Task for a repo -> skipped (skipped_inflight), even if
  backlog < cap.
- multi-repo: under-cap repos each get one, capped repos get none, in one cycle.
- backstop: an `AwaitingApproval` proposal Task whose issue lost the label and
  has no running implementation -> `ApprovalApproved` flipped True; a no-op when
  an implementation is already running; a no-op when the label is still present.
- fake reader returns labels via `ListOpenIssues` and label/state via `GetIssue`.
- `ListOpenIssues` error for one repo does not block the others.

## Out of scope

- Reusing the wrapper pod across lifecycle states (separate optimization).
- Changing the proposal/approval webhook itself (the backstop only covers misses).
- mr/issue scan cadence (already hourly).
