# SCM Projects, proactive issues, and PR/MR reactions

Date: 2026-06-09
Status: design (approved in brainstorm 2026-06-09)
Repos: tatara-operator (core), tatara-cli (MCP tools), tatara-claude-code-wrapper
(agent reach). Infra: tatara-bot SCM account + per-Project fine-grained PAT.

## Goal

Extend the operator's SCM issue/conversation handling so tatara: (1) participates
on a project board (GitHub Projects v2 named `tatara`, spanning manually-enrolled
repos; or a single GitLab issue board), (2) proactively opens issues for deferred
bugs and noticed improvements, marked `awaiting-approval` until a human approves,
and (3) reacts to PRs/MRs conditionally on author -- if tatara authored it, tatara
may improve code / fix conflicts / reject / merge; if a human authored it, tatara
may comment / suggest / approve / request-changes ("dislike with a comment").

Acceptance: a human never has tatara silently mutate their branch; tatara never
merges unattended unless `autoMergeOnGreenCI` is set AND CI exists; every
tatara-proposed work item is visibly gated behind a human-removable label.

## Decisions locked in brainstorm

- **Auth: fine-grained PAT, no GitHub App.** Reuse the existing single-Secret
  model (`Project.Spec.ScmSecretRef`). No org webhook -> enrollment is manual.
- **Identity: dedicated `tatara-bot` account.** Author-login `== Project.Spec.scm.botLogin`
  is the authoritative "is this mine" signal. Body marker is human-visible only.
- **Board topology: one provider per Project**, bound at CR creation, patchable,
  no cross-provider sync.
- **Approval: SCM label is the signal, CRD mirrors it.**
- **Merge: per-Project policy** `afterApproval` (default) or `autoMergeOnGreenCI`
  (auto-merge only when CI is detected; else falls back to afterApproval).
- **Egress: operator-mediated.** Agents emit intent via MCP; the operator performs
  every SCM write through one audited path with the Project token.
- **Discussion scope: Issues + PR/MR comment threads only.** GitHub Discussions
  out of scope.
- **PR/MR reaction modeled as a Task** with a new `Kind` (Approach A): reuse the
  existing turn-loop and single egress path; only write-back branches on Kind.

## Architecture (end to end)

```
   webhook (issues/issue_comment/PR|MR/review, push)        agent MCP intent
              |  Client.DetectAndVerify (author captured)         |  REST
              v                                                    v
   webhook.Server dispatch ---------> Task CRD <------- restapi handlers
   (Kind: implement|review|selfImprove,                          |
    ApprovalRequired, Source.AuthorLogin)                        |
              |                                                   |
              v   TaskReconciler turn-loop (gate: memory + cap + ApprovalApproved)
              |        spawn wrapper Pod, drive turns, agent self-plans Subtasks
              v
   write-back (branches on Kind) --> IssueAuthor / Reviewer / BoardManager
                                     (GitHub REST v3 + Projects v2 GraphQL; GitLab REST)
```

## Components

### A. SCM capability interfaces (`internal/scm`)

Keep the ingress `Client` (`Provider`, `DetectAndVerify`) at `scm.go:23`. Split
egress into focused capability interfaces; each provider implements what it
supports, feature-detected by type assertion:

```go
type IssueAuthor interface {
    CreateIssue(ctx, repo, IssueReq) (IssueRef, error)   // title, body, labels
    Comment(ctx, target, body string) error              // issue or PR/MR thread
    AddLabel(ctx, target, label string) error
    RemoveLabel(ctx, target, label string) error
}

type Reviewer interface {
    GetPRState(ctx, repo, number) (PRState, error)       // author, headSHA, mergeable, ciStatus
    Approve(ctx, repo, number, body string) error
    RequestChanges(ctx, repo, number, body string) error // GL: unapprove + thumbsdown + note
    Suggest(ctx, repo, number, []Suggestion) error       // inline ```suggestion blocks
    Merge(ctx, repo, number, method string) error
    ClosePR(ctx, repo, number, body string) error
}

type BoardManager interface {
    AddItem(ctx, board BoardRef, itemURL string) error
    SetColumn(ctx, board BoardRef, itemURL, column string) error
}
```

- **GitHub:** REST v3 for issues/PRs/reviews(`POST .../pulls/{n}/reviews`)/merge
  (`PUT .../pulls/{n}/merge`)/labels/CI(`GET .../commits/{sha}/check-runs`). A thin
  GraphQL v4 helper ONLY for Projects v2 (`addProjectV2ItemById`,
  `updateProjectV2ItemFieldValue`, project-by-number query). Existing client is
  REST-only (`github.go`), so the GraphQL helper is net-new but scoped to board ops.
- **GitLab:** REST. Issues `POST /projects/:id/issues`; MR notes; MR approve
  `POST .../merge_requests/:iid/approve`; merge `PUT .../merge_requests/:iid/merge`;
  CI via MR `head_pipeline`. Boards are label-driven: `SetColumn` swaps a
  `board::<col>` scoped label; `RequestChanges` = unapprove + thumbsdown award + note.

The old 2-method `Writer` (`writeback.go:23`) is replaced by these interfaces;
`doWriteBack` (`writeback.go:92`) branches on `Task.Spec.Kind`.

### B. CRD changes (`api/v1alpha1`)

`Project.Spec` (`project_types.go`) gains:
```go
type ScmSpec struct {
    Provider        string     // github|gitlab (enum) -- explicit binding
    Owner           string     // org/group
    BotLogin        string     // authoritative author-identity for "is this mine"
    Board           *BoardSpec // optional
    MergePolicy     string     // afterApproval (default) | autoMergeOnGreenCI
    PRReactionScope string     // labeledOrMentioned (default) | all
    ApprovalLabel   string     // default "tatara/awaiting-approval"
}
type BoardSpec struct {
    GitHubProjectNumber int    // provider=github
    GitLabBoardID       int    // provider=gitlab
    StatusField         string // GH single-select field used as columns (default "Status")
}
```
`Project.Spec.Scm *ScmSpec`, kubebuilder defaults on enums. `ScmSecretRef`,
`TriggerLabel`, `MaxConcurrentTasks` unchanged.

`Task` (`task_types.go`):
```go
Spec.Kind             string  // implement (default) | review | selfImprove
Spec.ApprovalRequired bool
Spec.Source.AuthorLogin string
Spec.Source.IsPR        bool
Spec.Source.Number      int
Status.DiscoveredIssues []string   // URLs of issues this Task proposed
// new Status condition type: ApprovalApproved
// new Phase value: AwaitingApproval
```

### C. Ingress: expanded webhook events (`internal/webhook/server.go`)

`WebhookEvent` (`scm.go`) gains `AuthorLogin`, `Action`, `Number`, `IsPR`,
`HeadSHA`, `HeadBranch`. The dispatch switch (`server.go:105`) grows from
{push, issue|mr} to also handle:
- issue `labeled`/`unlabeled` -> approval-signal handling (see D)
- `issue_comment`/`note` -> mention/command detection (@botLogin)
- `pull_request`/`merge_request` `opened`/`synchronize`/`closed`
- `pull_request_review`/review

`handleWorkItem` (`server.go:149`) populates `AuthorLogin` and decides Task `Kind`:
- issue (human, trigger label) -> `implement`, `ApprovalRequired:false`
- issue carrying `awaiting-approval` -> proposal mirror (see D), not actioned
- PR/MR, author `== botLogin` -> `selfImprove`
- PR/MR, author `!= botLogin` -> `review`
PR/MR reactions gated by `prReactionScope` (label or @mention by default).

### D. Approval state machine

`tatara/awaiting-approval` (Project.Spec.scm.ApprovalLabel) is the source of truth.
- **Propose:** agent `propose_issue` intent -> operator `CreateIssue` with the
  approval label + board column "Proposed" + body marker, AND creates a mirror
  `Task{Kind:implement, ApprovalRequired:true, Phase:AwaitingApproval,
  condition ApprovalApproved=False}`. Implementation blocked.
- **Approve:** human removes the approval label (or moves the card out of
  "Proposed"). Webhook `unlabeled` on a `botLogin`-authored issue -> patch
  `ApprovalApproved=True`. New gate in `task_controller.go` (after the
  `atConcurrencyCap` check at :107, before Planning at :142) holds the Task in
  `AwaitingApproval` until the condition is True.
- **Reject:** human closes the issue -> Task -> `Failed` (reason Rejected); mirror
  cleaned via owner-ref.
- **Human-authored, trigger-labeled issues:** existing path, `ApprovalRequired:false`.

### E. Egress: verb matrix by Task.Kind (`writeback.go` / `task_controller.go`)

- **review** (human PR/MR): agent reads diff + memory, emits a verdict; operator
  posts `comment` | `suggest` | `approve` | `requestChanges`. Never pushes to the
  human's branch.
- **selfImprove** (tatara PR/MR): agent works on the PR branch in a worktree
  (improve, fix conflicts via rebase), pushes; operator then `merge` (per
  mergePolicy) or `closePR` (reject) with a comment.
- **implement**: unchanged -- branch + `OpenChange` (PR/MR) + comment links on the
  origin issue (`writeback.go:93,117`). Bodies now carry the tatara-authored marker.

### F. Merge policy

`afterApproval` (default): merge a tatara-authored PR only when the human approval
signal is present (an approving review, or the `awaiting-approval` label removed).
`autoMergeOnGreenCI`: `GetPRState` detects CI (GH check-runs/status; GL head
pipeline). CI present + green -> merge. CI absent -> fall back to afterApproval.
Merge method squash (configurable later).

### G. MCP tools (operator-mediated; `tatara-cli` + operator REST)

New REST endpoints on the operator (`internal/restapi/handlers.go`,
mounted in `restapi/server.go`) and matching MCP tools in tatara-cli
`OperatorTools()` (`tools.go:284-390`). Agents emit intent only:
- `propose_issue(repo, title, body, kind=bug|improvement)`
- `review_verdict(task, decision=approve|request_changes|comment, body, suggestions[])`
- `pr_outcome(task, action=merge|close, reason)` (selfImprove only)
Wrapper exposes them via the existing `RegisterTataraMCP` wiring.

### H. Observability

- Extend `operator_webhook_events_total` with an `action` label.
- New `operator_scm_writes_total{provider,verb,result}`.
- New `operator_approval_gate_seconds` histogram (proposal -> approval latency).
- INFO log per business action (request_id, project, repo, task, verb, author).

## Testing strategy (TDD)

- Per-provider capability table tests against an httptest fake (GitHub REST +
  GraphQL, GitLab REST). One test per verb; assert request shape + auth header.
- Webhook unit tests: one per new event/action, asserting Kind selection,
  AuthorLogin capture, and the proposal/approval branches.
- envtest: Task gate holds in `AwaitingApproval` until `ApprovalApproved` flips;
  proposal creates the mirror Task; reject cleans it.
- write-back tests: each Kind maps to the correct verb set; `selfImprove` never
  fires review verbs and vice versa; merge gated by policy + CI detection.
- cli: `TestOperatorTools_AllToolsMarshal` extended for the 3 new tools.

## Sequencing (multi-repo, build from main)

1. **operator core** -- capability interfaces + GitHub GraphQL helper + GitLab
   board labels; CRD changes + regen; expanded webhook; Task Kind + approval gate;
   write-back verb matrix; merge policy; REST endpoints; metrics. Largest unit.
2. **tatara-cli** -- 3 MCP intent tools against the new REST endpoints.
3. **tatara-claude-code-wrapper** -- ensure the new tools register; bump image.
4. **infra** -- tatara-bot account + per-Project FG-PAT in sops; Project CRs
   patched with the `scm` block (provider/owner/botLogin/board/mergePolicy).

operator first (it owns the contract); cli/wrapper follow; infra last.

## Out of scope (YAGNI)

GitHub Discussions; auto-enroll polling loop (manual enrollment chosen);
bidirectional board sync; GitHub App; per-user RBAC; non-squash merge strategies.

## Extension-point file:line index (from the map workflow)

- webhook dispatch: `internal/webhook/server.go:105`; work-item: `:149`, label
  check `:150`, Task create `:216`.
- SCM interface: `internal/scm/scm.go:23`; provider select `registry.go:12`;
  GitHub REST `github.go:113`; GitLab `gitlab.go:94`.
- write-back: `internal/controller/writeback.go:23,92,117`; gate
  `task_controller.go:79,107,142`; WritebackPending set `:481`; terminate `:446`.
- CRDs: `api/v1alpha1/{project_types.go:49,52,55, task_types.go:9-14,38}`.
- REST: `internal/restapi/handlers.go`, `restapi/server.go:43`.
- cli tools: `tatara-cli/internal/mcp/tools.go:284-390`; wrapper
  `internal/bootstrap/mcp_register.go`.
