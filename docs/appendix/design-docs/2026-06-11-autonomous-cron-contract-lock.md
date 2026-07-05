# Autonomous Cron - Contract Lock

**Date:** 2026-06-11
**Status:** Frozen wire types. Change here = change in all four repos.
**Pairs with:** `2026-06-11-autonomous-cron-design.md`

This freezes every cross-repo wire type before parallel build, so the operator,
cli, wrapper, and infra plans cannot diverge. Byte-for-byte. Field names, json
tags, enum strings, route paths, label keys are normative.

## 1. Task Kinds (string, no Go const)

`Task.Spec.Kind` is a bare string compared inline (existing pattern:
`task.Spec.Kind == "implement"`). The kubebuilder enum on the field extends:

```
// +kubebuilder:validation:Enum=implement;review;selfImprove;triageIssue;brainstorm
Kind string `json:"kind,omitempty"`
```

New values: `triageIssue`, `brainstorm`. Existing values unchanged.

## 2. Task dedup labels (new, operator-stamped)

Cron-created Tasks carry these labels (all values are strings):

| Key | Value | On |
|---|---|---|
| `tatara.io/source-repo` | repo slug (`owner/name` or GitLab path) | all cron Tasks with a source |
| `tatara.io/source-number` | PR/issue number, base-10 | MR-triage, triageIssue |
| `tatara.io/source-kind` | `review` \| `selfImprove` \| `triageIssue` | MR-triage, triageIssue |
| `tatara.io/head-sha` | PR head commit SHA | MR-triage only |
| `tatara.io/activity` | `mrScan` \| `issueScan` \| `brainstorm` | all cron Tasks |

Label-value sanitization: numbers and SHAs are DNS-label-safe as-is; repo slugs
with `/` are replaced with `.` for the label value (the unsanitized slug lives in
`Task.Spec.Source.Repo`). Selection for dedup uses `tatara.io/source-number` +
`tatara.io/source-repo`.

## 3. Egress pod label (new, operator-stamped)

Brainstorm pods, when `internet` is in `sources`, get:

```
tatara.io/egress: internet
```

Exact key/value. The cluster NetworkPolicy (infra) allow-rule keys on it.

## 4. SCM read/close capabilities

Added to `scm.Client` and both implementations. `time` is `time.Time`.

```go
type PRRef struct {
    Repo      string    `json:"repo"`
    Number    int       `json:"number"`
    Author    string    `json:"author"`
    HeadSHA   string    `json:"headSha"`
    Labels    []string  `json:"labels,omitempty"`
    UpdatedAt time.Time `json:"updatedAt"`
}

type IssueRef struct {
    Repo      string    `json:"repo"`
    Number    int       `json:"number"`
    Labels    []string  `json:"labels,omitempty"`
    UpdatedAt time.Time `json:"updatedAt"`
    IsPR      bool      `json:"isPr"` // GitHub /issues returns PRs; filter these out
}

type BoardItem struct {
    Repo      string    `json:"repo"`
    Number    int       `json:"number"` // 0 for draft/non-issue items -> skipped
    Column    string    `json:"column"`
    UpdatedAt time.Time `json:"updatedAt"`
}

// board is the Project's existing BoardSpec.
ListOpenPRs(ctx context.Context, owner, repo string) ([]PRRef, error)
ListOpenIssues(ctx context.Context, owner, repo string) ([]IssueRef, error)
ListBoardItems(ctx context.Context, board BoardSpec) ([]BoardItem, error)
CloseIssue(ctx context.Context, repo string, number int, comment string) error
```

Provider mapping:
- GitHub: `GET /repos/{o}/{r}/pulls?state=open`; `GET /repos/{o}/{r}/issues?state=open` (set `IsPR` from `pull_request` presence, caller filters); board via GraphQL ProjectV2 user-or-org (reuse the dual-aliased query); `CloseIssue` = comment POST then `PATCH /repos/{o}/{r}/issues/{n}` `{"state":"closed"}`.
- GitLab: `GET /projects/:id/merge_requests?state=opened`; `GET /projects/:id/issues?state=opened`; board items from label-board mapping; `CloseIssue` = note POST then `PUT /projects/:id/issues/:iid` `{"state_event":"close"}`.

Errors: return the existing `*HTTPError` on non-2xx (same as current methods).

**Implementation refinements (operator-internal, do not affect cross-repo wire):**
- The four read methods live on a new `scm.SCMReader` interface; `CloseIssue` is added to the existing `scm.SCMWriter` (egress path), not the webhook-detect `scm.Client`.
- `ListBoardItems` takes `scm.BoardRef` (resolved via the existing `boardRefFromSpec`), not the CRD `BoardSpec` - the `scm` package cannot import `api/v1alpha1`. GitLab `ListBoardItems` is a documented no-op (label-boards are already covered by `ListOpenIssues`).
- The frozen `IssueRef` name collides with a pre-existing `scm.IssueRef{Ref,URL}` return type; the pre-existing (un-frozen) type is renamed to `scm.CreatedIssue` to free the frozen name.
- The reader constructor is `ReaderFor(provider, token)` (token is per-Project, resolved from the scm secret before the call).

## 5. CRD: Project additions

In `api/v1alpha1/project_types.go`:

```go
type CronActivity struct {
    // 5-field cron (robfig ParseStandard). Empty disables this activity.
    Schedule    string `json:"schedule,omitempty"`
    // +kubebuilder:default=1
    MaxPerCycle int    `json:"maxPerCycle,omitempty"` // clamped to >= 1 at use
}

type BrainstormActivity struct {
    Enabled     bool   `json:"enabled,omitempty"`
    Schedule    string `json:"schedule,omitempty"`
    // +kubebuilder:default=1
    MaxPerCycle int    `json:"maxPerCycle,omitempty"`
    // +kubebuilder:validation:items:Enum=docs;memory;internet
    Sources     []string `json:"sources,omitempty"`
}

type ScmCron struct {
    MRScan     CronActivity       `json:"mrScan,omitempty"`
    IssueScan  CronActivity       `json:"issueScan,omitempty"`
    Brainstorm BrainstormActivity `json:"brainstorm,omitempty"`
}
```

Added to existing `ScmSpec`:

```go
PriorityLabel string   `json:"priorityLabel,omitempty"`
Cron          *ScmCron `json:"cron,omitempty"`
```

Added to existing `ProjectStatus`:

```go
LastMRScan     *metav1.Time `json:"lastMRScan,omitempty"`
LastIssueScan  *metav1.Time `json:"lastIssueScan,omitempty"`
LastBrainstorm *metav1.Time `json:"lastBrainstorm,omitempty"`
```

`sources` enum values: `docs`, `memory`, `internet` (exact strings).

## 6. Task status: issue outcome

Added to `Task.Status` (mirrors existing `PROutcome` / `ReviewVerdict`):

```go
type IssueOutcome struct {
    // +kubebuilder:validation:Enum=implement;close
    Action  string `json:"action"`
    Comment string `json:"comment,omitempty"` // required when Action==close
}

// in TaskStatus:
IssueOutcome *IssueOutcome `json:"issueOutcome,omitempty"`
```

## 7. REST endpoint

Route (mirrors `/tasks/{t}/pr-outcome` at server.go:55):

```
POST /tasks/{t}/issue-outcome
```

Request body:

```json
{ "action": "implement" | "close", "comment": "string" }
```

Validation (in handler):
- `action` must be `implement` or `close` (else 400).
- `comment` required (non-empty) when `action == "close"` (else 400).
- Task must exist (else 404) and have `Kind == "triageIssue"` (else 409, mirroring the pr-outcome wrong-kind 409 at handlers.go:336).

On success: write `Task.Status.IssueOutcome`, return 200 with the updated Task DTO. The TaskReconciler performs the `CloseIssue` SCM write on `close`; `implement` records the marker only (the PR is the artifact).

## 8. MCP tool: issue_outcome (tatara-cli)

Added to `OperatorTools()`. Brings the operator tool count 12 -> 13.

```
name: issue_outcome
description: Record the outcome of an issue-triage task: implement (open a PR) or close (with a comment).
inputSchema:
  type: object
  properties:
    action:  { type: string, enum: [implement, close] }
    comment: { type: string }
  required: [action]
```

Build function signature matches the existing tools:
`func(map[string]any) (string, string, any, error)` returning
(method=`POST`, path=`/tasks/{TASK_ID}/issue-outcome`, body, err). `TASK_ID`
comes from `argOrEnv` (env `TATARA_TASK_ID`), same as `pr_outcome`.

## 9. Metrics (operator, Prometheus)

```
tatara_scan_items_total{activity, outcome}     counter  // outcome: scanned|picked|skipped_dedup|skipped_cap
tatara_scan_tasks_created_total{activity, kind} counter
tatara_scan_duration_seconds{activity}         histogram
tatara_issue_outcome_total{action}             counter   // action: implement|close
tatara_tasks_inflight{kind}                    gauge     // extend existing in-flight gauge to new kinds
```

`activity` label values: `mrScan`, `issueScan`, `brainstorm`. Registered via the
operator's existing metrics registry (controller-runtime
`sigs.k8s.io/controller-runtime/pkg/metrics`).

## 10. Build order (dependency)

1. tatara-operator (defines CRD, SCM caps, REST endpoint, scan loop).
2. tatara-cli (issue_outcome tool -> needs the REST route path frozen above).
3. tatara-claude-code-wrapper (registers the tool, bumps to the new cli image).
4. infra (Project CR cron block + egress NetworkPolicy + operator image roll).
