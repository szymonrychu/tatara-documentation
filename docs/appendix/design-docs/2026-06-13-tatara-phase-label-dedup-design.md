# tatara phase-label dedup + orphan-recovery - design

Date: 2026-06-13
Status: AMENDED 2026-06-13 (Option A) - ready for implementation
Component: tatara-operator (Go; controller-runtime/kubebuilder).

## AMENDMENT 2026-06-13 (supersedes the label-added-time mechanism below)

The original mechanism - dedup/orphan-recovery by **label-added-time (T_L) vs
Task-creation-time** plus a new `SCMReader.IssueLabelAddedAt` - is WITHDRAWN. It
cannot work: the operator sets a phase label DURING a Task's run (brainstorming
on Triage entry, implementation on Implement entry), so the handling Task is
always created BEFORE the labels it sets (`T_task < T_L`). The rule "actively
handled iff non-terminal Task with `CreationTimestamp >= T_L`" therefore never
identifies the handler; for an orphaned terminal Task (e.g. parked mid-implement
with an open MR) it evaluates false and re-spawns a fresh Triage Task - exactly
the duplicate-assignment bug this design exists to kill. Label changes are
always operator-driven (human re-engagement arrives via reopen/comment =
webhook), so `T_L` is always later than its handler's creation and the time
comparison is structurally useless.

ADOPTED (Option A): dedup and orphan-recovery key on phase-label PRESENCE +
Task STATE, not label time. Phase labels remain the issue's state-of-truth.

- non-terminal Task for (repo,number) -> actively handled -> dedup (the EXISTING
  fast path in `isDeduped`, unchanged).
- only-terminal Tasks AND an ACTIVE phase label
  (`tatara-brainstorming`/`tatara-approved`/`tatara-implementation`) present on
  the OPEN issue -> ORPHAN -> the backstop resumes the correct lifecycle state
  (implementation -> MRCI for the open MR else Implement; approved -> Implement;
  brainstorming -> Triage); the scan suppresses a fresh Triage (deduped).
- no managed phase label -> brand-new issue -> eligible for a fresh Triage Task.
- `tatara-declined` -> terminal -> no action (deduped; a human who wants to
  reopen the discussion re-engages via comment -> webhook).

The issue's current labels are already available on the scan candidate
(`candidate.labels`, from `ListOpenIssues`), so `isDeduped` stays a PURE
function - no per-candidate SCM call, no new SCMReader method. `IssueLabelAddedAt`
and its GitHub-timeline / GitLab-resource_label_events parsing are NOT
implemented. The webhook twin-gap fix, the four-label state machine, the
state-entry label application, and the migration are UNCHANGED from the sections
below; only the dedup/backstop SIGNAL changes (presence+state, not time). Where
the sections below reference `IssueLabelAddedAt` or `T_L`, read them through this
amendment.

---

(Original design preserved below for provenance; superseded where it conflicts
with the amendment.)

## Problem

tatara assigns the SAME issue to two concurrent agents -> two branches -> two
MRs -> one merges and closes the issue, the other orphans into a conflict. This
is the real cause of the "MRs not merged with conflicting changes, issues
closed" incidents (confirmed: issue #39 -> cli PR#41 + PR#42; issue #7 ->
wrapper PR#9 + PR#10).

Root cause in `internal/controller/projectscan.go` `isDeduped` (the issue arm):

```go
// issue: terminal Task suppresses unless the issue saw newer activity.
if !c.updatedAt.After(t.CreationTimestamp.Time) {
    return true
}
```

A terminal task suppresses a re-pick ONLY while the issue has had no new
activity since the task was created. Any new activity (a human OR bot comment,
a label change, an edit) advances `updatedAt` past the task's creation time,
frees the dedup key, and a SECOND implement task is spawned - even though the
first task already opened an MR. Twin gap in the webhook: `handleIssueComment`
(internal/webhook/server.go) creates a NEW lifecycle task when the owning task
has gone terminal, instead of re-engaging it.

Deeper consequence: when the owning task goes terminal with its MR still open,
the MR is orphaned - nothing babysits it toward merge - which is why these MRs
sit unmerged forever.

## Solution overview

Make the issue's **phase label** the source of truth for its lifecycle state,
and drive BOTH dedup and orphan-recovery off **label-added-time vs
task-creation-time**. One rule does both jobs:

- An issue is *actively handled in phase X* iff its phase-X label is present AND
  a matching non-terminal Task exists that was created at/after the label was
  added. -> suppress creating any new Task (dedup).
- A phase label present with NO fresh handler (no matching task created since
  the label, or none running) -> orphaned -> the backstop starts the right Task.

Because dedup keys on the phase label (not on `updatedAt` vs task-creation), new
issue activity no longer frees the key, so no duplicate task/MR. Because a phase
label without a live handler triggers a start, orphaned MRs get re-engaged.

## Phase labels (operator-managed, exactly one current)

| label                  | meaning                                              | set when (lifecycle)                         |
| ---------------------- | ---------------------------------------------------- | -------------------------------------------- |
| `tatara-brainstorming` | agent picked it up / triaging-brainstorming          | Triage (and Conversation) state entered      |
| `tatara-approved`      | approved for implementation (human- or self-)        | finishTriage outcome=implement, pre-Implement|
| `tatara-implementation`| implementation in flight                             | Implement / MRCI / Merge / MainCI entered    |
| `tatara-declined`      | declined before implementation (triage reject)       | finishTriage outcome=close (closes the issue)|

Merged + green main CI -> issue closed (existing close path / close-guard). A
closed issue's labels may be left as-is or cleared; do not block on this.

This REPLACES the labels shipped earlier in this work line
(`tatara-idea`/`tatara-rejected`) and ADDS `tatara-implementation`;
`tatara-approved` is unchanged. `tatara-idea` -> `tatara-brainstorming`,
`tatara-rejected` -> `tatara-declined`.

The operator is the sole label egress (extends the shipped `setLifecycleLabel`
in internal/controller/labels.go): set exactly the current phase label, remove
the other three managed labels; never touch `triggerLabel`/`priorityLabel`.
Both GitHub and GitLab AddLabel auto-create labels (no provisioning step).

## Label-added timestamp (new SCMReader method)

Add to `SCMReader` (internal/scm/scm.go) and implement for GitHub + GitLab:

```go
// IssueLabelAddedAt returns when `label` was most recently added to the issue,
// ok=false if the label is not currently applied.
IssueLabelAddedAt(ctx context.Context, owner, repo string, number int, label string) (time.Time, bool, error)
```

- GitHub: `GET /repos/{owner}/{repo}/issues/{number}/timeline`
  (Accept: application/vnd.github+json), filter events where `event == "labeled"`
  and `label.name == label`; take the most recent `created_at`. (Paginate if
  needed; an issue's timeline is small.)
- GitLab: `GET /projects/{proj}/issues/{iid}/resource_label_events`, filter
  `action == "add"` and `label.name == label`; take the most recent
  `created_at`.

This is read from SCM (robust; survives in-cluster Task GC; matches the
"label creation date" the design relies on) - NOT an in-cluster annotation.

## Dedup rule (replaces the isDeduped issue arm)

For an issue candidate `c` (repo, number):
1. Determine the issue's current phase label `L` and its added-time `T_L`
   (via `IssueLabelAddedAt`; if no managed phase label, fall through to existing
   behavior / treat as un-handled).
2. Find Tasks for (repo, number) (via the existing source-repo/source-number
   dedup labels). The issue is ACTIVELY HANDLED iff some such Task is
   non-terminal AND `Task.CreationTimestamp >= T_L`.
   - A Task created BEFORE `T_L` handled a prior phase; it does not count as
     handling the current phase.
3. Actively handled -> deduped (suppress new task). Not handled -> not deduped
   (eligible; the scan/backstop may create the right task).

Keep the PR arm of `isDeduped` (head-sha) unchanged. Keep the non-terminal
"any live task for (repo,number)" suppression as a fast path. Remove the
`updatedAt.After(CreationTimestamp)` arm (the gap).

## Backstop (orphan recovery; extends the scan/reconcile loop)

In `runScans` (or a dedicated pass invoked from it), for each managed OPEN issue
with a phase label but no fresh handler (per the dedup rule), start the right
Task - bounded by the existing `MaxOpenTasks` budget and per-repo lane logic:

| phase label            | no fresh handler -> action                                  |
| ---------------------- | ----------------------------------------------------------- |
| `tatara-brainstorming` | start a triage/issueLifecycle Task (Triage entry)           |
| `tatara-approved`      | start an implementation Task (approval happened, coding never began) |
| `tatara-implementation`| resume/re-create the implementation Task to babysit/land the open MR (un-orphan) |
| `tatara-declined`      | terminal; no action                                         |

For `tatara-implementation` recovery: prefer re-entering the lifecycle at MRCI
for the issue's existing open MR (so it is babysat toward merge) rather than
re-implementing from scratch; fall back to Implement if no open MR is found.

This subsumes the earlier approval-backstop idea and the MR-nursing gap.

## Webhook twin-gap fix

`internal/webhook/server.go` `handleIssueComment`: when a comment arrives and
the issue has a phase label with an owning Task (even terminal), reactivate the
owning Task (reset to Triage) rather than creating a new lifecycle Task. Only
create a new Task when the issue has no managed phase label / no owning Task.

## Integration with already-shipped work (this work line, deployed)

- Keep the close-guard: `hasUnmergedChange(task)` withholds any agent-driven
  CloseIssue; only the merged+green `handleMainCI` path closes a code-bearing
  issue. `tatara-declined` close is a pre-implementation reject (no code), so it
  closes normally.
- Keep `MaxOpenTasks` (default 3); the backstop's starts consume the same
  budget.
- Re-point the shipped `setLifecycleLabel`/`lifecycleLabels` (idea/approved/
  rejected) to the new four-label set and to STATE-ENTRY application (not just
  finishTriage outcomes): brainstorming on Triage entry, approved on
  finishTriage implement, implementation on Implement entry, declined on triage
  close.
- New `ScmSpec` fields (api/v1alpha1/project_types.go), kubebuilder defaults:
  `brainstormingLabel` = `tatara-brainstorming`, `approvedLabel` =
  `tatara-approved`, `implementationLabel` = `tatara-implementation`,
  `declinedLabel` = `tatara-declined`. Deprecate the shipped `ideaLabel`/
  `rejectedLabel` (keep as read-only aliases for migration, or drop after
  migration).

## Migration

On reconcile (one-time, idempotent), relabel existing open managed issues:
`tatara-idea` -> `tatara-brainstorming`, `tatara-rejected` -> `tatara-declined`.
`tatara-approved` is unchanged. Issues mid-implementation (have an open tatara
MR) should get `tatara-implementation`. Use the operator's AddLabel/RemoveLabel
egress; safe to re-run.

## Error handling

- `IssueLabelAddedAt` failure: fail safe toward NOT creating a duplicate - treat
  the issue as actively-handled (skip) on a read error, and let the next
  reconcile retry. (Never spawn a duplicate on a transient read error.)
- Label egress failures: AddLabel required (requeue); RemoveLabel best-effort
  (as in the shipped setLifecycleLabel).
- All status writes via RetryOnConflict.

## Testing (TDD)

- `IssueLabelAddedAt`: GitHub timeline + GitLab resource_label_events parsing
  (httptest-faked), most-recent-add semantics, ok=false when absent.
- Dedup rule: task created BEFORE the phase label -> NOT deduped (eligible);
  non-terminal task created AFTER the label -> deduped; terminal task after the
  label -> not actively handled -> eligible (backstop will act).
- Backstop: each phase-label-without-fresh-handler starts the correct Task; a
  fresh handler suppresses; declined is a no-op; MaxOpenTasks budget respected.
- `tatara-implementation` recovery re-enters MRCI for an existing open MR.
- Webhook: comment on an issue with a terminal owning Task reactivates it, does
  not create a duplicate.
- Migration: idea->brainstorming, rejected->declined, approved unchanged;
  idempotent.
- Regression: close-guard + MaxOpenTasks + the shipped finishTriage flow stay
  green.

## Out of scope

- The cli 0.7.0 release (separate ops) and the helm/token SSA reconciliation
  (needs the live bot PAT synced into sops; deploy currently via set-image).
- Reworking the MRCI/Merge/MainCI internals beyond the `tatara-implementation`
  re-entry.

## Key code touchpoints (operator, current main)

- `internal/controller/projectscan.go`: `isDeduped` (replace issue arm),
  `runScans`/`issueScan`/`mrScan`/`brainstorm` (backstop), `existingScanTasks`,
  `scanTaskLabels`, `laneOccupancy`, `candidate`, `findConvTaskToReactivate`.
- `internal/controller/labels.go`: `lifecycleLabels`, `setLifecycleLabel`
  (extend to four phase labels).
- `internal/controller/lifecycle.go`: `reconcileLifecycle` state dispatch +
  state-entry handlers (set phase label on entry), `finishTriage`,
  `hasUnmergedChange` (keep).
- `internal/webhook/server.go`: `handleIssueComment` (reactivate owning task).
- `internal/scm/scm.go` + `internal/scm/github*.go` + `internal/scm/gitlab*.go`:
  add `IssueLabelAddedAt`.
- `api/v1alpha1/project_types.go`: new `ScmSpec` label fields + `make manifests`.
