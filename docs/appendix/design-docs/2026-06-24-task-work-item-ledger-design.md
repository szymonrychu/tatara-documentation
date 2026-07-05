# Task CR as first-class lifecycle tracker (work-item ledger)

Date: 2026-06-24
Repo: tatara-operator
Status: design - pending user review

## Concept

The **Task CR is the first-class tracker for the whole brainstorm -> implement
-> review lifecycle**. It carries a **ledger of every SCM work-item it spans**
(N tracker issues + N MRs + brainstorm proposals), which is the single source of
truth for dedup, stall-recovery, agent-context building, and brainstorm triage.
SCM artifacts (issues, labels, MRs) become a **projection** of ledger state for
human visibility, not the source of truth.

**A Task is a Project-level resource, not Repository-level.** It is owned by a
Project (`Spec.ProjectRef`) and its ledger may span multiple Repositories within
that Project - the repo is a property of each `WorkItemRef`, never of the Task as
a whole. `Spec.RepositoryRef` is demoted from the Task's identity to an optional
primary-repo hint; the set of repos an agent pod needs (clone scope /
ReposInScope) is derived from the ledger, so cross-repo systemic work is native.

This subsumes today's brittle model where a Task's SCM tie lives in three k8s
labels (`tatara.io/source-repo`, `source-number`, `head-sha`) plus a single
`Spec.Source`, where brainstorm proposals live only as label-tagged issues, and
where there is no live refresh of SCM state onto the Task.

## Problems this fixes

1. **Dangling bot MRs get stranded.** `mrScan.isDeduped` skips a PR whose
   terminal Task carries the same head SHA. A conflicting/stale bot MR never
   gets a new commit, so once its repair task parks (even on a transient
   boot-crash) the MR is deduped forever and never closed. Live instance:
   `tatara-claude-code-wrapper!50` (CONFLICTING, parked `BootCrashLoop` since
   2026-06-20, target `tatara-operator#74` already CLOSED).
2. **Linkage is brittle and single-valued.** Labels are overloaded
   (`source-number` is sometimes the *linked-issue* number), used as a webhook
   server-side selector, and cannot represent a Task spanning multiple
   issues/MRs (only the partial `SystemicGroup` does).
3. **Brainstorm backlog is opaque.** The proposal-backlog cap that stalled MRs
   is counted by scanning issue labels per cron, with no durable per-Task view.

## Ownership model

**Agents are the primary driver.** A Task and its ledger are created and
maintained by the agents working on them, through the MCP tools they already
have - `new issue`, `update issue`, `create MR`, `comment`, `change label`,
`decline`, etc. The operator is the single writer of the CR, but it writes in
**response to those existing agent MCP actions** (relayed via the
wrapper/REST callback path) - projecting each action onto the ledger and the
SCM. No new agent-facing ledger tool is introduced.

**The cron reconciliation loop is a backstop, not a co-equal mechanism.** The
real-time path is webhook -> agent -> MCP. The cron loop only complements it:
it picks up items that, for some reason (missed webhook, crashed pickup, a
stalled/parked Task), were not handled right away, and it refreshes SCM state
onto the ledger. It never duplicates work the live path already owns.

## Creation paths (all converge on one Task model)

Each path is event-driven (webhook/cron) and then carried by the agent via MCP;
each seeds the ledger:

| Path | Trigger | Seeds the ledger with |
|------|---------|-----------------------|
| 1. Project brainstorm | brainstorm cron, project scope | the N improvements the session noted, as `role:proposed` entries |
| 2. Webhook targeted brainstorm | reporter/maintainer opens an issue | that issue (`role:source`), routed through a targeted brainstorm |
| 3. Cron sweeper (missed/un-enrolled) | backstop sweep finds an un-enrolled issue | that issue (`role:source`), routed through a targeted brainstorm |
| 4. Review agent | webhook or cron reaction to a NON-tatara MR | that MR (`role:reviewed`) |

## Design

### 1. The work-item ledger

```go
type WorkItemRef struct {
    Provider string `json:"provider"`              // github|gitlab
    Repo     string `json:"repo"`                  // owner/repo
    Number   int    `json:"number,omitempty"`      // 0 until an issue/PR is filed (proposals)
    Kind     string `json:"kind"`                  // issue|pr
    Role     string `json:"role"`                  // proposed|source|closes|openedPR|reviewed
    State    string `json:"state,omitempty"`       // role-dependent (see below)
    Title    string `json:"title,omitempty"`       // proposal text / context
    HeadSHA  string `json:"headSHA,omitempty"`      // pr only
    LastRefreshedAt *metav1.Time `json:"lastRefreshedAt,omitempty"`
}
```

`State` vocabulary by role:
- `proposed`: `proposed | approved | declined | implemented`
- `source` / `closes` (issues): `open | closed`
- `openedPR` / `reviewed` (PRs): `open | closed | merged`

Carried as `Status.WorkItems []WorkItemRef` (mutable, operator-owned). It
generalizes and subsumes `Spec.Source`, `SystemicGroup.SameRepoSiblings`/
`CrossRepo`, and `Status.PRNumber`/`PrURL`/`HeadBranch`.

`Spec.Source` stays as the **immutable creation-time seed identity** (the
webhook/scan need an identity before Status is populated). Add
`Spec.Source.DedupNumber int` (omitempty) to carry the linked-issue number for
bot-PR tasks explicitly, replacing the overloaded `source-number` label.

`Status.ParkReason string` is added (set on every `Parked` transition, cleared
otherwise) - carried for context/observability only; it does NOT gate
re-activation.

### 2. Hybrid label projection (brainstorm triage)

The ledger is authoritative; SCM issues + lifecycle labels are a two-way
projection:

- **Project (write):** for each `role:proposed` entry, the operator files/maintains
  a tracker issue and sets the lifecycle label to mirror `State`
  (`proposed` -> `tatara-brainstorming`, `approved` -> `tatara-approved`,
  `declined` -> `tatara-declined`, `implemented` -> closed). Filing the issue
  populates the entry's `Number`.
- **Read back (signal):** a human relabel (`tatara-approved` / `tatara-declined`)
  is read as an approve/decline signal and written onto the entry's `State`.
  An `approved` proposal drives implementation (a `role:source`/`closes` item +
  an implement Task, per the systemic flow).
- **Backpressure cap** (`maxOpenProposals`) is computed from ledger
  `role:proposed` entries in non-terminal `State` (equivalent to today's
  open-proposal count, now ledger-derived). Triage semantics for the human are
  unchanged.

### 3. Remove the three `tatara.io` dedup labels

Drop `LabelSourceRepo`, `LabelSourceNumber`, `LabelHeadSHA` and every
read/write/select:

- `projectscan.go` (`isDeduped`, `priorTerminalAttempts`,
  `hasLiveLifecycleTaskForIssue`, `scanTaskLabels`, lines 58/99/123/200/207/248/
  1725/1754): match on identity derived from `Spec.Source`
  (repo from `IssueRef`, number from `DedupNumber || Number`) and head SHA from
  the `role:openedPR` ledger entry.
- `webhook/server.go` (352-365, 411-412, 622-623, 742-743): the at-creation
  dedup `MatchingLabels` selector is replaced by deterministic Task naming
  (issueLifecycle names already derive from the dedup key -> duplicate create
  collides on name = idempotent) plus an in-memory match over a listed set for
  the non-deterministic arms. Heavier hot path (O(tasks)) accepted per the
  Full-removal decision.

Single identity helper reused everywhere: `taskMatchesItem(t, repo, number)`.

### 4. Cron reconciliation backstop (stalled issue/MR sweep)

A periodic reconcile that COMPLEMENTS the webhook/MCP path - it only acts on
items the live path missed or that stalled, never re-driving healthy in-flight
work. It walks in-cluster Tasks (cadence aligned to the hourly scan; refined in
the plan). It works in **two tiers** - cheap data sync first, agent only if
needed:

**Tier 1 - silent sync (no agent).** For each `WorkItemRef`, fetch current SCM
state (issue open/closed, PR open/closed/merged, head SHA, CI, missed comments)
and update the Task: `State`, `HeadSHA`, `LastRefreshedAt`, appended
comments/history. A missed label/state/comment change is reconciled directly
onto the Task. NO pod is started for pure state catch-up.

**Tier 2 - escalate to an agent only when the synced change needs work.** After
Tier 1, classify the drift:
- `role:proposed` flipped to `approved` -> seed implementation.
- new human comment awaiting reply -> Conversation.
- open MR stalled (no live pod): **close-obsolete first** - if every
  `source`/`closes` issue is `closed`/`merged`, close the MR with a superseded
  note and do NOT start an agent; **otherwise re-activate** (regardless of prior
  park reason), bounded by `maxRecoveryAttempts` -> `closeExhaustedPR`.
- pure state refresh with nothing actionable -> done, no agent.

The agent it does start is prompted from the now-current Task (section 6), so it
never works from stale state. This replaces same-SHA-label dedup: recovery is
driven by live SCM state on the ledger, not a label a conflicting MR can never
change.

### 5. Maintenance via existing agent MCP tools (operator projects)

The ledger is maintained as a side-effect of the MCP tools agents ALREADY use;
the operator is the single CR writer and projects each action onto the ledger.
No new ledger-write tool. Mapping of existing tool -> ledger effect:

| Existing agent MCP action | Ledger effect |
|---------------------------|---------------|
| `new issue` | upsert `WorkItemRef{kind:issue, role:source\|closes, state:open, number}` |
| `update issue` / `comment` | refresh the matching entry (state, title) |
| `change label` (approve/decline projection) | set the `role:proposed` entry `State` |
| `create MR` (today -> `writeback.go` sets `PrURL`/`PRNumber`) | upsert `WorkItemRef{kind:pr, role:openedPR, state:open, headSHA}` |
| `close issue`/`merge MR` | set the matching entry `State` to `closed`/`merged` |

These callbacks already flow operator-ward via the wrapper/REST path; the change
is to project them onto `Status.WorkItems` in addition to today's single-valued
`Status` fields. The cron backstop (section 4) only fills gaps the live path
missed.

### 6. The Task is the canonical history and primary prompt source

The Task is essentially the durable discussion/implementation/review history -
the work-item ledger plus the conversation transcript
(`Status.ConversationObjectKey`/`SessionID`) and decision summaries
(`Handover`, `ResultSummary`, outcomes). It is the **primary source for
prompt-building**: when the operator starts any agent (implement / conversation
/ review), the prompt is assembled FROM the Task so the agent knows upfront,
without re-deriving from SCM:

- the status of each spanned issue (`role:source`/`closes`/`proposed` + `State`),
- the adjacent/sibling issues and MRs in the ledger,
- what the conversation has been about (prior comments, decisions, park reasons),
- the current MR/CI state.

SCM is the projection/backstop, not the per-prompt fetch source. Systemic wiring
now: `SystemicGroup.SameRepoSiblings` + `CrossRepo` fold into the ledger as
`role:closes` entries at creation, and the lead impl agent's combined-PR context
is built from the ledger.

### 7. One-off: unstick wrapper!50

Its task predates the ledger and its target `tatara-operator#74` is CLOSED.
Close MR !50 with a superseded note and delete the stale `scan-qe-jt6zx` Task.
The iterator's close-obsolete rule handles this class going forward.

## Migration / back-compat

In-flight Tasks created before this change have the old labels but no ledger. On
first reconcile, lazily seed `Status.WorkItems` from `Spec.Source` (+ any
`SystemicGroup` + `Status.PRNumber`) and stop reading labels. Label writes are
removed immediately; the lazy seed preserves dedup across the deploy.

## Testing (TDD)

- Ledger: seed from each of the 4 creation paths; upsert idempotent by
  (repo, number, kind); proposal entries with `Number==0`.
- Hybrid projection: `proposed` entry -> issue filed + `tatara-brainstorming`
  label; human `tatara-approved` -> entry `State=approved` + implement seeded;
  `maxOpenProposals` counted from ledger.
- `taskMatchesItem` / dedup: same identity dedups, different does not; bot-PR
  `DedupNumber` matches an issue-slot scan task; webhook duplicate collides on
  deterministic name.
- Backstop Tier 1 (no agent): a missed label/state/comment change updates the
  Task and starts NO pod. Tier 2 escalates only when actionable
  (approved->implement, new human comment->Conversation, stalled open MR).
- Backstop reactivation: refresh updates `State`/`HeadSHA`; all source issues
  closed -> MR closed (no agent); open MR + no live pod + issues still open ->
  reactivate; bounded by `maxRecoveryAttempts` -> `closeExhaustedPR`.
- Prompt-building: an agent started by the backstop is prompted from the
  post-sync Task (current state, adjacent issues, conversation), not stale SCM.
- Write-back: opening an MR upserts a `role:openedPR` entry; label change / issue
  close reflected.
- Context-building: ledger -> prompt includes spanned open issues + MRs;
  systemic siblings present.
- `setLifecycleState` persists/clears `ParkReason`.

## Suggested phasing (for the plan)

1. Data model (`WorkItemRef`, `Status.WorkItems`, `Spec.Source.DedupNumber`,
   `Status.ParkReason`) + demote `Spec.RepositoryRef` to optional hint +
   lazy-seed migration + drop label writes.
2. Dedup + webhook migrated to project-scoped identity-from-spec/ledger; remove
   label reads.
3. Cron backstop (Tier 1 silent sync + Tier 2 close-obsolete/reactivate).
4. Hybrid label projection for brainstorm proposals + `maxOpenProposals` from
   ledger.
5. Maintenance projection from existing MCP tools + Task-sourced prompt-building
   + pod clone-scope (ReposInScope) derived from the ledger + systemic wiring.
6. One-off wrapper!50 unstick.

## Out of scope

- GC of the ~965 accumulated terminal issueLifecycle Task CRs (separate).
- Re-running the human triage backlog itself (that is the human's to drain).

## Deploy

Operator change. CRD gains `status.workItems`, `status.parkReason`,
`spec.source.dedupNumber`. Standard operator deploy via tatara-helmfile: dual
chart-pin (tatara-operator + tatara-project `0.0.0-g<sha>`) +
`values/tatara-operator/common.yaml` image `tag` bump; templated CRDs apply on
`helm upgrade`.
