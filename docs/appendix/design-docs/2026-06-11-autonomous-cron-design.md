# Autonomous Cron: MR/Issue Triage + Self-Driven Brainstorming - Design

**Date:** 2026-06-11
**Status:** Design (approved, pre-plan)
**Builds on:** `2026-06-09-scm-projects-pr-reactions-design.md` (deployed, operator 0.3.0)

## 1. Goal

Make tatara act autonomously on a schedule, not only in reaction to webhooks:

1. Periodically triage open MRs/PRs in a Project and act per author (tatara-authored: fix/merge/close; human-authored: comment/suggest/approve/requestChanges).
2. Periodically triage open issues (per-repo issues + project board items) and either implement them (open a PR, author-gated downstream) or close them with an explanatory comment.
3. Optionally, self-drive the project: research docs + the tatara-memory graph + the live internet, and propose new issues (carrying the existing awaiting-approval marker).

All three are cron-driven, with a configurable per-cycle pick limit.

## 2. Non-goals

- No new agent runtime. Work is performed by the existing claude-code-wrapper pod via the existing Task turn loop.
- No change to the existing human-vs-tatara write-back verb matrix. Cron is a Task factory; it routes to existing Kinds.
- No K8s CronJob and no new CRD. Scheduling reuses the in-repo `repository_controller` cron pattern.
- Internet research is scoped to brainstorming only. Extending it to implement Tasks (library-doc lookup) is explicitly out of scope here.
- No board/issue sync engine. The SCM remains the source of truth; tatara reads on a schedule and writes via the audited egress path.

## 3. Architecture

Three cron-driven scans per Project, each a **Task factory**. The operator (which holds the SCM token and is the sole SCM-write caller) lists open work via new SCM read capabilities, applies selection + dedup, and creates Tasks up to per-activity caps. Tasks flow through the unchanged wrapper-pod turn loop and the unchanged write-back matrix.

```
Project reconciler (cron fires)
  -> projectscan.go: for each due activity
       -> scm.Client.List*            (operator egress, one token)
       -> select (priority-then-stale), cap at maxPerCycle
       -> dedup vs existing Project-owned Tasks
       -> create Task{Kind, Source}
  -> RequeueAfter = soonest next-fire of the three crons (clamped)

TaskReconciler (unchanged loop)
  -> spawn wrapper pod -> agent turns -> MCP intent
  -> writeback.go (unchanged matrix, author/actor gate at egress)
```

Identity and gating are inherited verbatim from the SCM-projects feature: the webhook/payload author is a hint only; the authoritative bot-authorship gate is enforced at egress in the TaskReconciler via `GetPRState` (see memory `scm-author-vs-actor-egress-gate`). Cron-created Tasks pass through the same gate, so a cron-picked human MR can never be auto-merged/closed.

## 4. Scheduling

Reuse the existing `repository_controller.go` mechanism exactly:

- `robfig/cron/v3` `cron.ParseStandard` on 5-field cron strings.
- Per-activity `Status.Last*Scan *metav1.Time` records the last fire; next-fire computed from `base = lastScan | creationTimestamp`.
- `RequeueAfter` set to the soonest of the three next-fires, clamped to `maxScheduleRequeue` (6h, reused constant) so clock skew or long sleeps still converge.
- A malformed cron string logs an error and disables that activity (does not crash the reconciler), mirroring `scheduleNextReingest`.

Scan logic lives in a new file `internal/controller/projectscan.go`, called from `ProjectReconciler.Reconcile` (which already returns `RequeueAfter` at project_controller.go:93). The reconciler keeps Project lifecycle; the scan file owns list/select/dedup/create.

## 5. The three activities

### 5.1 MR-triage

- Scan: `ListOpenPRs(ctx, owner, repo)` across the Project's enrolled repos (GitHub) or the bound project (GitLab).
- Per item: call `GetPRState` (authoritative author). Create a Task:
  - author == `BotLogin` -> `Kind=selfImprove`
  - author != `BotLogin` -> `Kind=review`
- No new egress verbs. The Task drives the existing matrix:
  - `review` (human MR): comment / suggest / approve / requestChanges. Never auto-close.
  - `selfImprove` (tatara MR): improve code / fix conflicts / merge-per-policy / close.

"Fix+merge or close" therefore applies only to tatara-authored MRs, exactly matching the human-vs-tatara contract from the prior feature.

### 5.2 Issue-work

- Scan: `ListOpenIssues(ctx, owner, repo)` (per-repo) and `ListBoardItems(ctx, board)` (project board). Board items that are issues are de-duplicated against the per-repo issue list by `(repo, number)`.
- Per item: create a Task `Kind=triageIssue`, `Source` = the issue.
- The agent researches project direction (docs + memory), then emits exactly one `issue_outcome`:
  - `implement`: the same pod continues into the normal implement flow and opens a PR. The PR is tatara-authored, so it re-enters the author-gated review/merge path (merge governed by the existing `mergePolicy`). The `issue_outcome{implement}` call is a recorded marker; the PR is the artifact.
  - `close`: operator calls `CloseIssue(number, comment)` with the agent's explanatory comment.

This is the gated "implement-or-close" analog of MR fix-or-close. New issues are never silently force-merged: an implement decision still produces a PR that obeys `mergePolicy` (afterApproval | autoMergeOnGreenCI).

### 5.3 Brainstorm (opt-in)

- Enabled only when `spec.scm.cron.brainstorm.enabled` is true.
- Scan: generative, no list. Create a Task `Kind=brainstorm` with `maxPerCycle` bounding issues proposed per cycle.
- The agent researches per `sources` (subset of `docs`, `memory`, `internet`), then emits one or more existing `propose_issue` calls. Proposed issues carry the existing awaiting-approval marker (`approvalLabel`), so a human must approve before any implementation.
- `internet` in `sources` requires the pod to have outbound network + WebSearch/WebFetch (see section 9).

## 6. CRD changes

`Project.spec.scm` (extends the existing `ScmSpec`):

```yaml
scm:
  # ... existing: provider, owner, botLogin, board, mergePolicy,
  #     prReactionScope, approvalLabel ...
  priorityLabel: "tatara/priority"     # selection: items with this label first
  cron:
    mrScan:
      schedule: "0 * * * *"            # 5-field cron; empty disables
      maxPerCycle: 1
    issueScan:
      schedule: "0 * * * *"
      maxPerCycle: 1
    brainstorm:
      enabled: false
      schedule: "0 6 * * *"
      maxPerCycle: 1
      sources: [docs, memory, internet]
```

Go types (in `api/v1alpha1/project_types.go`):

```go
type CronActivity struct {
    Schedule    string `json:"schedule,omitempty"`
    MaxPerCycle int    `json:"maxPerCycle,omitempty"` // default 1, clamped >= 1
}

type BrainstormActivity struct {
    Enabled     bool     `json:"enabled,omitempty"`
    Schedule    string   `json:"schedule,omitempty"`
    MaxPerCycle int      `json:"maxPerCycle,omitempty"`
    // +kubebuilder:validation:items:Enum=docs;memory;internet
    Sources     []string `json:"sources,omitempty"`
}

type ScmCron struct {
    MRScan     CronActivity       `json:"mrScan,omitempty"`
    IssueScan  CronActivity       `json:"issueScan,omitempty"`
    Brainstorm BrainstormActivity `json:"brainstorm,omitempty"`
}

// added to ScmSpec:
//   PriorityLabel string   `json:"priorityLabel,omitempty"`
//   Cron          *ScmCron `json:"cron,omitempty"`
```

`ProjectStatus` adds:

```go
LastMRScan     *metav1.Time `json:"lastMRScan,omitempty"`
LastIssueScan  *metav1.Time `json:"lastIssueScan,omitempty"`
LastBrainstorm *metav1.Time `json:"lastBrainstorm,omitempty"`
```

`Task.spec.kind` enum extends from `implement;review;selfImprove` to `implement;review;selfImprove;triageIssue;brainstorm`.

Empty `cron` (or empty schedule per activity) keeps the feature dormant - safe default for the existing live Project, which has no `spec.scm` at all.

## 7. Selection: priority-then-stale

For a list of candidates and a `maxPerCycle` N:

1. Partition into `withPriority` (carries `priorityLabel`) and `rest`.
2. Within each partition, sort least-recently-touched first (PRs/issues: by `updatedAt` ascending; board items: by board updatedAt).
3. Concatenate `withPriority ++ rest`, take first N (after dedup filtering, section 8).

If `priorityLabel` is empty, the partition is pure stale-first.

## 8. Dedup: no re-pick

Tasks are the dedup record - no growing status map. Cron-created Tasks carry labels:

- `tatara.io/source-repo`
- `tatara.io/source-number`
- `tatara.io/source-kind` (review | selfImprove | triageIssue)
- `tatara.io/head-sha` (PRs only)

Scan procedure per candidate:

- **PR:** skip if a non-terminal Task exists for `(repo, number)`; skip if a terminal Task exists at the current `HeadSHA` (already handled this revision). A new push (new `HeadSHA`) makes it eligible again.
- **Issue:** skip if a non-terminal Task exists for `(repo, number)`; for terminal Tasks, skip unless the issue's `updatedAt` is newer than the Task's creation (new activity warrants re-triage). Closed issues are never reopened (they do not appear in `ListOpenIssues`).
- **Brainstorm:** no per-item dedup; `maxPerCycle` bounds output. The agent is instructed to check existing open issues (via `ListOpenIssues` results passed in the prompt, or its own `propose_issue` idempotency) to avoid duplicate proposals.

Terminal = Task phase in {Succeeded, Failed}. Tasks have TTL-based GC (existing), so the label-selected set stays bounded.

## 9. Web egress (brainstorm internet)

`sources: [..., internet]` requires the brainstorm pod to reach the internet and have WebSearch/WebFetch enabled.

- **Chart bakes nothing** (rule 14). The operator stamps a pod label (e.g. `tatara.io/egress: internet`) on brainstorm pods when `internet` is in `sources`.
- The cluster-side NetworkPolicy (infra helmfile) keys on that label to allow egress. Pods without the label get the default-deny egress.
- No per-task permission-mode switch: the default `PermissionMode=bypassPermissions` already allows WebSearch/WebFetch (claude built-ins). The only new knob is the egress label; without it the NetworkPolicy blocks outbound regardless of tool availability.

## 10. New SCM capabilities

Added to `scm.Client` (and both implementations):

```go
type PRRef struct {
    Repo      string
    Number    int
    Author    string
    HeadSHA   string
    Labels    []string
    UpdatedAt time.Time
}

type IssueRef struct {
    Repo      string
    Number    int
    Labels    []string
    UpdatedAt time.Time
    IsPR      bool        // GitHub returns PRs in the issues list; filter these out
}

type BoardItem struct {
    Repo      string
    Number    int          // 0 for draft/non-issue items (skipped)
    Column    string
    UpdatedAt time.Time
}

ListOpenPRs(ctx, owner, repo string) ([]PRRef, error)
ListOpenIssues(ctx, owner, repo string) ([]IssueRef, error)
// board is the Project's existing BoardSpec (githubProjectNumber | gitlabBoardId).
ListBoardItems(ctx context.Context, board BoardSpec) ([]BoardItem, error)
CloseIssue(ctx context.Context, repo string, number int, comment string) error
```

- **GitHub:** `ListOpenPRs`/`ListOpenIssues` via REST v3 (`GET /repos/{o}/{r}/pulls?state=open`, `GET /repos/{o}/{r}/issues?state=open` with `IsPR` filtering on `pull_request` presence). `ListBoardItems` via GraphQL ProjectV2 (user-scoped per the prior dual user/org fix). `CloseIssue` = REST `PATCH state=closed` + a comment POST.
- **GitLab:** REST `GET /projects/:id/merge_requests?state=opened`, `GET /projects/:id/issues?state=opened`; board items via the label-board mapping; `CloseIssue` = `PUT state_event=close` + a note POST.

## 11. New MCP tool: `issue_outcome`

Mirrors the existing `pr_outcome` tool, added to tatara-cli and registered by the wrapper.

```
issue_outcome(action: "implement" | "close", comment: string)
```

- Validated enum on `action`; `comment` required for `close`.
- Emits intent to the operator REST endpoint `POST /tasks/{t}/issue-outcome`; the TaskReconciler performs the SCM write (`CloseIssue`) on `close`, or records the marker on `implement`.

`propose_issue`, `review_verdict`, `pr_outcome` are reused unchanged. Tool count on the cli MCP server goes 12 -> 13.

## 12. Egress verb matrix (delta only)

Unchanged from the prior feature except:

| Kind | New verbs used |
|---|---|
| triageIssue | `CloseIssue` (on close), existing OpenChange/Comment (on implement) |
| brainstorm | existing `CreateIssue` + `AddLabel` (awaiting-approval) via `propose_issue` |

MR-triage adds no verbs. The author/actor authoritative gate (`GetPRState().Author == BotLogin` before any selfImprove pod spawn and before any Merge/ClosePR) is unchanged and now also covers cron-created selfImprove Tasks.

## 13. Observability

Per-activity Prometheus metrics (operator):

- Counters: `tatara_scan_items_total{activity, outcome=scanned|picked|skipped_dedup|skipped_cap}`.
- Counter: `tatara_scan_tasks_created_total{activity, kind}`.
- Histogram: `tatara_scan_duration_seconds{activity}`.
- Gauge: `tatara_tasks_inflight{kind}` (extends existing in-flight gauge to the new Kinds).
- Counter: `tatara_issue_outcome_total{action}`.

Every scan logs at INFO with `project`, `activity`, `listed`, `picked`, `skipped`, `duration_ms`. Errors listing/closing log at ERROR with the SCM HTTP status.

## 14. Testing

- envtest: cron fire computes correct next-fire from `Last*Scan`; malformed cron disables activity without crashing; scan creates Tasks with correct Kind + labels; dedup skips in-flight and same-SHA-handled; selection orders priority-then-stale and caps at `maxPerCycle`.
- Fake SCM client: `ListOpenPRs`/`ListOpenIssues`/`ListBoardItems`/`CloseIssue` table-driven; GitHub issue list filters PRs via `IsPR`.
- author/actor: cron-created Task for a human-authored PR resolves to `Kind=review` and the egress gate refuses merge/close even if a stale label suggested tatara authorship.
- cli: `issue_outcome` marshal + enum-validation test; missing comment on `close` rejected.
- wrapper: build-stage guard asserts `issue_outcome` is registered in the baked cli binary (same pattern as the prior tool-registration guard).
- Contract-lock doc (`2026-06-11-autonomous-cron-contract-lock.md`) freezes the wire types (PRRef/IssueRef/BoardItem, `issue_outcome` schema, REST endpoint, CRD fields) before parallel build, per the lesson that cross-repo wire divergence is caught late.

## 15. Cross-repo split (build order)

Mirrors the prior feature. Operator contract first; cli/wrapper follow; infra last.

1. **tatara-operator** (largest): `projectscan.go` scan loop + cron wiring in Project reconciler; SCM list+close capabilities (interface + GitHub + GitLab); CRD additions (ScmCron, BrainstormActivity, priorityLabel, Last*Scan, new Task Kinds) + regen; `triageIssue`/`brainstorm` Task handling; `issue_outcome` REST endpoint + TaskReconciler write; egress pod label for internet brainstorm; metrics; tests. Chart appVersion bump.
2. **tatara-cli**: `issue_outcome` MCP tool + marshal/enum test. Version bump.
3. **tatara-claude-code-wrapper**: register `issue_outcome`; image bump to the new cli; build-stage guard test. Chart bump.
4. **infra** (runbook, user-gated): add the `spec.scm.cron` + `priorityLabel` block to the Project CR; add the egress NetworkPolicy keyed on `tatara.io/egress: internet`; operator image roll. sops/account steps unchanged from the prior runbook.

## 16. Open risks

- **Brainstorm duplicate issues:** mitigated by passing existing open issues into the agent prompt and `maxPerCycle`, but the agent could still propose near-duplicates. Acceptable: proposals are gated behind human approval, so a duplicate is a cheap reject.
- **Scan cost at scale:** listing every repo every cron tick is O(repos). Bounded by enrolled-repo count (small) and the 6h clamp; revisit if repo count grows large.
- **Internet egress blast radius:** confined to brainstorm pods via the label-gated NetworkPolicy; default-deny everywhere else.
