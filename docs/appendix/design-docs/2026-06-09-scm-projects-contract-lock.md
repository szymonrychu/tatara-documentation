# SCM-projects contract lock (shared wire types)

Date: 2026-06-09
Purpose: the exact new types/endpoints/tool-schemas that tatara-operator
(producer of REST + CRD), tatara-cli (consumer via MCP), and the SCM clients
must match byte-for-byte. Locked before the per-repo plans so they cannot
diverge (see the semantic-misses wire-shape incident, MEMORY). All egress is
operator-mediated: tatara-cli MCP tools only mutate CRDs over REST; the
TaskReconciler is the SOLE caller of any SCM write.

## 1. SCM egress (internal/scm)

`Client` keeps `Provider`, `DetectAndVerify`, `OpenChange`, `Comment` (unchanged).
`WebhookEvent` gains fields. New value types + a widened writer interface that
`*GitHub` and `*GitLab` both satisfy:

```go
// WebhookEvent additions (scm.go)
type WebhookEvent struct {
    Kind     string // push|issue|mr|other  (unchanged)
    Repo     string
    Branch   string
    Labels   []string
    Title    string
    Body     string
    IssueRef string
    URL      string
    // NEW:
    AuthorLogin  string // login of the issue/PR/MR author (board identity)
    Action       string // opened|labeled|unlabeled|closed|synchronize|submitted|created|other
    Number       int    // issue/PR/MR number (github) or iid (gitlab)
    IsPR         bool   // true for mr/pull_request events
    HeadSHA      string // PR/MR head commit (for CI lookup)
    HeadBranch   string // PR/MR source branch (for selfImprove push target)
    ChangedLabel string // for labeled/unlabeled: the single label added/removed
}

type IssueReq struct {
    Title  string
    Body   string
    Labels []string
}
type IssueRef struct {
    Ref string // owner/repo#n (github) or group/proj#iid (gitlab)
    URL string // html/web url
}
type PRState struct {
    Author     string
    HeadSHA    string
    HeadBranch string
    Mergeable  bool
    CIStatus   string // "" none | pending | success | failure
}
type Suggestion struct {
    Path string
    Line int
    Body string
}

// SCMWriter is what controller.SCMFor returns; *GitHub and *GitLab satisfy it.
// Embeds the existing 2 write methods plus the new capability sets.
type SCMWriter interface {
    OpenChange(ctx, repoURL, token, sourceBranch, targetBranch, title, body string) (string, error)
    Comment(ctx, token, issueRef, body string) error
    // IssueAuthor:
    CreateIssue(ctx, repoURL, token string, req IssueReq) (IssueRef, error)
    AddLabel(ctx, token, issueRef, label string) error
    RemoveLabel(ctx, token, issueRef, label string) error
    // Reviewer:
    GetPRState(ctx, repoURL, token string, number int) (PRState, error)
    Approve(ctx, repoURL, token string, number int, body string) error
    RequestChanges(ctx, repoURL, token string, number int, body string) error
    Suggest(ctx, repoURL, token string, number int, sugg []Suggestion) error
    Merge(ctx, repoURL, token string, number int, method string) error
    ClosePR(ctx, repoURL, token string, number int, body string) error
    // BoardManager:
    AddBoardItem(ctx, token string, board BoardRef, itemURL string) error
    SetBoardColumn(ctx, token string, board BoardRef, itemURL, column string) error
}

type BoardRef struct {
    Provider            string
    Owner               string
    GitHubProjectNumber int
    GitLabBoardID       int
    StatusField         string // GH single-select field; default "Status"
}
```

GitHub: REST v3 for issues/labels/reviews(`POST .../pulls/{n}/reviews`,
event=APPROVE|REQUEST_CHANGES|COMMENT, suggestions as inline review comments with
` ```suggestion ` bodies)/merge(`PUT .../pulls/{n}/merge`,`merge_method`)/CI
(`GET .../commits/{sha}/check-runs`, derive success|failure|pending). Board ops
use a NEW GraphQL v4 helper (`POST https://api.github.com/graphql`): resolve
project id by org+number, `addProjectV2ItemById`, set the Status single-select via
`updateProjectV2ItemFieldValue`. GitLab: REST; `CreateIssue`
`POST /projects/:id/issues`; `Approve` `POST .../merge_requests/:iid/approve`;
`RequestChanges` = `POST .../merge_requests/:iid/unapprove` + thumbsdown award
(`POST .../award_emoji name=thumbsdown`) + note; `Merge`
`PUT .../merge_requests/:iid/merge`; `GetPRState` reads MR + `head_pipeline.status`;
board column = swap `board::<col>` scoped label via issue update; `AddBoardItem`
is a no-op for GitLab label-boards (issues are on the board once they carry a list
label) -> implemented as ensuring the board's default list label.

## 2. CRD fields (api/v1alpha1) -- exact json tags

```go
// project_types.go
type BoardSpec struct {
    GitHubProjectNumber int    `json:"githubProjectNumber,omitempty"`
    GitLabBoardID       int    `json:"gitlabBoardId,omitempty"`
    // +kubebuilder:default="Status"
    StatusField         string `json:"statusField,omitempty"`
}
type ScmSpec struct {
    // +kubebuilder:validation:Enum=github;gitlab
    Provider string `json:"provider"`
    Owner    string `json:"owner"`
    BotLogin string `json:"botLogin"`
    // +optional
    Board *BoardSpec `json:"board,omitempty"`
    // +kubebuilder:validation:Enum=afterApproval;autoMergeOnGreenCI
    // +kubebuilder:default="afterApproval"
    MergePolicy string `json:"mergePolicy,omitempty"`
    // +kubebuilder:validation:Enum=labeledOrMentioned;all
    // +kubebuilder:default="labeledOrMentioned"
    PRReactionScope string `json:"prReactionScope,omitempty"`
    // +kubebuilder:default="tatara/awaiting-approval"
    ApprovalLabel string `json:"approvalLabel,omitempty"`
}
// ProjectSpec gains:  Scm *ScmSpec `json:"scm,omitempty"`   (+optional)

// task_types.go
type ProposedIssueSpec struct {
    RepositoryRef string `json:"repositoryRef"`
    Title         string `json:"title"`
    Body          string `json:"body"`
    // +kubebuilder:validation:Enum=bug;improvement
    Kind string `json:"kind"`
}
type Suggestion struct {
    Path string `json:"path"`
    Line int    `json:"line"`
    Body string `json:"body"`
}
type ReviewVerdict struct {
    // +kubebuilder:validation:Enum=approve;request_changes;comment
    Decision    string       `json:"decision"`
    Body        string       `json:"body,omitempty"`
    Suggestions []Suggestion `json:"suggestions,omitempty"`
}
type PROutcome struct {
    // +kubebuilder:validation:Enum=merge;close
    Action string `json:"action"`
    Reason string `json:"reason,omitempty"`
}
// TaskSource gains:
//   AuthorLogin string `json:"authorLogin,omitempty"`
//   IsPR        bool   `json:"isPR,omitempty"`
//   Number      int    `json:"number,omitempty"`
// TaskSpec gains:
//   // +kubebuilder:validation:Enum=implement;review;selfImprove
//   // +kubebuilder:default="implement"
//   Kind             string             `json:"kind,omitempty"`
//   ApprovalRequired bool               `json:"approvalRequired,omitempty"`
//   ProposedIssue    *ProposedIssueSpec `json:"proposedIssue,omitempty"`
// TaskStatus.Phase enum becomes: Pending;AwaitingApproval;Planning;Running;Succeeded;Failed
// TaskStatus gains:
//   DiscoveredIssues []string       `json:"discoveredIssues,omitempty"`
//   ReviewVerdict    *ReviewVerdict `json:"reviewVerdict,omitempty"`
//   PROutcome        *PROutcome     `json:"prOutcome,omitempty"`
// New condition type constant: "ApprovalApproved"
```

## 3. REST endpoints (operator restapi) -- CRD writes only, no SCM calls

- `POST /projects/{p}/issues` (propose_issue)
  req: `{"repositoryRef":"<repo>","title":"...","body":"...","kind":"bug|improvement"}`
  action: create `Task{Spec.Kind:implement, ApprovalRequired:true,
  ProposedIssue:{...}, ProjectRef:p, RepositoryRef:repo}`, owner-ref Project,
  `Status.Phase:AwaitingApproval`, condition `ApprovalApproved=False`.
  resp 201: the TaskDTO.
- `POST /tasks/{t}/review` (review_verdict)
  req: `{"decision":"approve|request_changes|comment","body":"...",
  "suggestions":[{"path":"a.go","line":12,"body":"..."}]}`
  action: set `Status.ReviewVerdict`; `Status().Update`. resp 200 TaskDTO.
- `POST /tasks/{t}/pr-outcome` (pr_outcome)
  req: `{"action":"merge|close","reason":"..."}`
  action: set `Status.PROutcome`; `Status().Update`. resp 200 TaskDTO.

`TaskDTO` (restapi/dto.go) gains `kind`, `approvalRequired`, `phase` already
present; add `discoveredIssues`, `reviewVerdict`, `prOutcome`, and
`source.authorLogin/isPR/number` to the existing DTO mapping.

## 4. tatara-cli MCP tools (3 new, Target=TargetOperator)

```
propose_issue   schema {repo|repositoryRef, title, body, kind(bug|improvement)} required: title,body,kind,repo
  -> POST /projects/{TATARA_PROJECT}/issues  body {repositoryRef,title,body,kind}
review_verdict  schema {task?, decision(approve|request_changes|comment), body, suggestions:[{path,line,body}]} required: decision
  -> POST /tasks/{task|TATARA_TASK}/review   body {decision,body,suggestions}
pr_outcome      schema {task?, action(merge|close), reason} required: action
  -> POST /tasks/{task|TATARA_TASK}/pr-outcome body {action,reason}
```
Task/project resolved via `argOrEnv(a,"task","TATARA_TASK")` /
`argOrEnv(a,"project","TATARA_PROJECT")` exactly like existing operator tools.
Count rises 9 -> 12 operator tools; `TestOperatorTools_*` marshal test extended.

## 5. Controller flow (TaskReconciler) -- the only SCM egress

- **Approval gate:** after the `atConcurrencyCap` check (task_controller.go:106-117)
  and before "Set Planning" (:141), if `task.Spec.ApprovalRequired` and the
  `ApprovalApproved` condition is not True, set `Phase=AwaitingApproval` and
  `RequeueAfter: capRequeue` (no pod spawn).
- **Proposal creation:** if `Phase==AwaitingApproval` && `Spec.ProposedIssue!=nil`
  && `Spec.Source==nil`: `CreateIssue` (labels = [approvalLabel]) -> set
  `Spec.Source{Provider,IssueRef,URL,Number,IsPR:false,AuthorLogin:botLogin}`,
  append `Status.DiscoveredIssues`, board `AddBoardItem`+`SetBoardColumn(Proposed)`;
  stay AwaitingApproval. (SCM egress lives here, not in restapi.)
- **Approval signal:** webhook `issue` `unlabeled` where `ChangedLabel==approvalLabel`
  and author/owner is `botLogin` -> find Task by `Source.IssueRef`, set condition
  `ApprovalApproved=True`. Gate releases on next reconcile.
- **Write-back branches on `Spec.Kind`** (writeback.go):
  - `implement` (default): unchanged; bodies stamped with the marker line
    `<!-- tatara-authored -->`.
  - `review`: read `Status.ReviewVerdict`; post via `Comment`/`Suggest`/`Approve`/
    `RequestChanges`. Never `OpenChange`, never push.
  - `selfImprove`: agent has pushed to `HeadBranch`; read `Status.PROutcome`; on
    `merge` consult `Project.Spec.Scm.MergePolicy` (afterApproval needs an approving
    signal; autoMergeOnGreenCI calls `GetPRState`, merges only if `CIStatus==success`,
    else falls back to afterApproval) then `Merge`; on `close` -> `ClosePR`.
- **Kind selection (webhook handleWorkItem):** issue+triggerLabel ->
  implement/ApprovalRequired=false; PR/MR author==botLogin -> selfImprove;
  PR/MR author!=botLogin -> review; gated by `prReactionScope`
  (labeledOrMentioned: triggerLabel present OR body/comment @mentions botLogin; all).

## 6. Metrics

`operator_webhook_events_total{provider,kind,action,result}` (add `action`).
New `operator_scm_writes_total{provider,verb,result}` (verb: create_issue|comment|
add_label|remove_label|approve|request_changes|suggest|merge|close|open_change|
board_add|board_column). New `operator_approval_gate_seconds` histogram.
