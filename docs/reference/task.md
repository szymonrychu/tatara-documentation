---
title: Task CRD
---

# Task

A `Task` CR represents one discrete unit of agent work. The operator creates Tasks when a
`QueuedEvent` is admitted from the webhook queue, or directly on a cron schedule (brainstorm,
healthCheck, incident). Each Task spawns exactly one agent Pod per turn and drives it to
completion.

```
apiVersion: tatara.dev/v1alpha1
kind: Task
```

!!! note "Operator-managed"
    Tasks are created and fully managed by the tatara operator. Direct `kubectl apply` of a
    Task is supported for debugging but is not the normal path. Prefer letting the operator
    create Tasks from QueuedEvents or cron triggers.

---

## Kind families

Task kinds are divided into two families based on scope. The `repositoryRef` field is
**required** for repo-scoped kinds and **must be empty** for project-scoped kinds. The
operator rejects Tasks that violate this contract at reconcile time.

| Kind | Family | Default | Description |
|---|---|:---:|---|
| `implement` | repo-scoped | yes | Write code and open a PR for a labeled issue |
| `review` | repo-scoped | | Review a human-authored PR with inline suggestions |
| `selfImprove` | repo-scoped | | Self-directed improvement run on a repository |
| `triageIssue` | repo-scoped | | Triage a single issue (standalone, outside lifecycle) |
| `issueLifecycle` | repo-scoped | | Full issue lifecycle: triage -> conversation -> implement -> merge |
| `brainstorm` | project-scoped | | Propose improvement issues across all enrolled repos |
| `refine` | project-scoped | | Refine pending proposals before a brainstorm cycle |
| `healthCheck` | project-scoped | | Platform health check with a generated report |
| `incident` | project-scoped | | Investigate a Grafana alert and open an incident issue |

!!! warning "Kind-conditional validation"
    The CRD schema cannot express conditional field requirements, so `repositoryRef`
    validation is enforced at reconcile time by the controller, not by the API server.
    A Task that fails validation is immediately terminated with a descriptive condition.

---

## Spec

| Field | Type | Default | Required | Description |
|---|---|---|:---:|---|
| `projectRef` | string | - | yes | Parent `Project` CR name |
| `repositoryRef` | string | - | conditional | `Repository` CR name. Required for repo-scoped kinds; must be omitted for project-scoped kinds. |
| `goal` | string | - | yes | Natural-language task goal passed as the first agent turn |
| `kind` | enum | `implement` | no | Task kind (see table above) |
| `source` | [TaskSource](#tasksource) | - | no | SCM work-item that originated this task |
| `maxTurns` | int | from Project | no | Per-task override for `agent.maxTurnsPerTask` |
| `proposedIssue` | [ProposedIssueSpec](#proposedissuespec) | - | no | Issue blueprint for brainstorm-created proposals awaiting human approval |
| `reposInScope` | `[]string` | - | no | Repository CR names this task is expected to change. Empty = single-repo (primary only). When set, the writeback step warns for any in-scope repo that produced no commits. |
| `systemicGroup` | [SystemicGroup](#systemicgroup) | - | no | Marks this task as the systemic-improvement lead. The lead opens one combined PR that closes all `sameRepoSiblings`. |
| `alertRule` | string | - | no | Grafana alert rule name (`alertname` label) that triggered an incident task. Descriptive only; dedup key is the `tatara.dev/alert-group` label hash. |

### TaskSource

Records the SCM work-item that originated the task. Populated automatically from the
webhook payload when the operator creates the Task from a QueuedEvent.

| Field | Type | Description |
|---|---|---|
| `provider` | enum | `github` or `gitlab` |
| `issueRef` | string | Canonical reference: `owner/repo#N` for issues and GitHub PRs, `owner/repo!N` for GitLab MRs |
| `url` | string | Browser URL of the originating issue or PR |
| `authorLogin` | string | SCM login of the item's author |
| `isPR` | bool | `true` when the source item is a PR or MR |
| `number` | int | Issue or PR number |
| `title` | string | Title at enqueue time. Feeds the deterministic branch slug and the no-agent PR-title fallback. |
| `dedupNumber` | int | Linked issue number for bot-PR tasks. When a bot MR body contains `Closes #N`, this field holds `N` so dedup matches the task against the issue slot, not the PR number. Zero means the task targets the item identified by `number`. |

### ProposedIssueSpec

Carried by brainstorm tasks that propose a new issue awaiting human approval. The operator
creates a tracker issue from this spec when the task completes.

| Field | Type | Description |
|---|---|---|
| `repositoryRef` | string | Target `Repository` CR name |
| `title` | string | Proposed issue title |
| `body` | string | Proposed issue body (Markdown) |
| `kind` | enum | `bug` or `improvement` |
| `systemicId` | string | Groups related proposals into one systemic improvement. When set, `createProposal` stamps a `tatara/systemic-<id>` label and sibling footer; the whole group counts as one against `maxOpenProposals`. |
| `incident` | bool | `true` when filed by an incident-investigation agent; `createProposal` then adds the incident label. |

### SystemicGroup

Carried by the lead task of a systemic-improvement group. The lead opens a single PR that
closes all same-repo siblings and is aware of cross-repo siblings as reference context.

| Field | Type | Description |
|---|---|---|
| `systemicId` | string | Group identifier. Matches the `tatara/systemic-<id>` SCM label applied by `createProposal`. |
| `sameRepoSiblings` | `[]int` | Issue numbers in the same repository that the lead PR closes. |
| `crossRepo` | `[]string` | Cross-repo sibling references in `owner/repo#N - title` form. Informational only; not closed by this PR. |

---

## Status

### Core progress fields

| Field | Type | Description |
|---|---|---|
| `phase` | enum | `Planning`, `Running`, `Succeeded`, `Failed`. Used by non-lifecycle tasks. Empty for `issueLifecycle` tasks for their entire lifetime (see [Termination](#termination)). |
| `podName` | string | Name of the currently running agent Pod |
| `turnsCompleted` | int | Total turns completed across all runs |
| `prURL` | string | URL of the opened PR or MR |
| `resultSummary` | string | Short natural-language summary written by the agent at task end |
| `conditions` | `[]Condition` | Standard Kubernetes conditions (`type`, `status`, `reason`, `message`). Includes `WritebackFailed` when the writeback loop-breaker trips. |
| `discoveredIssues` | `[]string` | Issue URLs discovered as a side-effect of the task (e.g. bugs found during review) |
| `followupIssueURL` | string | URL of the follow-up issue opened when `changeSummary.remainingScope` is non-empty. Guards against opening a second follow-up on re-entry. |
| `gateEnteredAt` | time | Timestamp when this task entered the admission gate. Used to detect gate-stall. |

### Agent outcome structs

One outcome struct is populated by the agent via MCP tool at the end of each task kind.
All other outcome fields are absent.

| Field | Type | Populated by |
|---|---|---|
| `reviewVerdict` | [ReviewVerdict](#reviewverdict) | `review` tasks |
| `prOutcome` | [PROutcome](#proutcome) | Post-merge or close of a tatara-authored PR |
| `issueOutcome` | [IssueOutcome](#issueoutcome) | `triageIssue` tasks |
| `implementOutcome` | [ImplementOutcome](#implementoutcome) | `implement` tasks that open no PR |
| `brainstormOutcome` | [BrainstormOutcome](#brainstormoutcome) | `brainstorm` tasks that file no proposals |
| `changeSummary` | [ChangeSummary](#changesummary) | `implement` tasks via the `change_summary` MCP tool |

#### ReviewVerdict

| Field | Type | Description |
|---|---|---|
| `decision` | enum | `approve`, `request_changes`, or `comment` |
| `body` | string | Review comment body |
| `suggestions` | `[]Suggestion` | Inline code suggestions. Each entry: `path` (file path), `line` (1-based), `body` (suggestion text). |

#### PROutcome

| Field | Type | Description |
|---|---|---|
| `action` | enum | `merge` or `close` |
| `reason` | string | Optional explanation |

#### IssueOutcome

| Field | Type | Description |
|---|---|---|
| `action` | enum | `implement`, `close`, or `discuss` |
| `comment` | string | Comment body. Required when `action` is `close` or `discuss`. |
| `plan` | string | Short implementation plan. Posted as an implementation-start message when `action` is `implement`. |

#### ImplementOutcome

Populated when an implement agent deliberately opens no PR.

| Field | Type | Description |
|---|---|---|
| `action` | enum | `declined` or `already_done` |
| `reason` | string | Required explanation of why no implementation was produced |

#### BrainstormOutcome

Populated when a brainstorm agent exits without filing any proposals.

| Field | Type | Description |
|---|---|---|
| `action` | enum | Always `none` |
| `reason` | string | Required explanation of why no proposals were filed |

#### ChangeSummary

Submitted by the implement agent via the `change_summary` MCP tool at the end of a run.

| Field | Type | Description |
|---|---|---|
| `prTitle` | string | PR title |
| `prBody` | string | PR body |
| `deliveredScope` | string | What was implemented in this run |
| `remainingScope` | string | Scope not completed. When non-empty, the operator opens a follow-up issue and records its URL in `followupIssueURL`. |
| `mostProblematic` | string | Hardest part of the change, from the agent's self-assessment |

---

## issueLifecycle-only status fields

The `issueLifecycle` kind operates a multi-phase state machine rather than a single-shot
run. The fields below are only populated for `issueLifecycle` tasks; all other task kinds
leave them empty.

### State machine

```mermaid
stateDiagram-v2
    [*] --> Triage
    Triage --> Conversation : discuss action
    Triage --> Implement : implement action
    Triage --> Done : close action
    Conversation --> Implement : human approves / agent decides
    Conversation --> Parked : inactivity deadline
    Conversation --> Done : resolved in conversation
    Implement --> MRCI : PR opened
    Implement --> Parked : implement-empty cap hit
    MRCI --> Merge : CI passes
    MRCI --> Implement : CI fails (re-implement)
    Merge --> MainCI
    MainCI --> Done : main CI passes
    MainCI --> Implement : regression detected
    Done --> [*]
    Stopped --> [*]
    Parked --> Conversation : human re-engages
    Parked --> Implement : human re-engages after implement-empty
```

!!! info "Dual termination design"
    `issueLifecycle` tasks never set `status.phase`; they signal completion through
    `status.lifecycleState`. Any code that checks whether a Task is finished **must** call
    the `TaskTerminal` helper (or replicate its logic), not test `phase` alone. See
    [Termination](#termination).

### State and timing

| Field | Type | Description |
|---|---|---|
| `lifecycleState` | enum | Current state: `Triage`, `Conversation`, `Implement`, `MRCI`, `Merge`, `MainCI`, `Done`, `Stopped`, `Parked` |
| `lastActivityAt` | time | Timestamp of the last meaningful activity (comment, state transition, agent turn). Used to enforce inactivity deadlines. |
| `deadlineAt` | time | When the current deadline expires. Set on each state transition that has a timeout. |
| `lifecycleIterations` | int | Total state-machine loop count. The operator parks the task if this exceeds `agent.maxLifecycleIterations`. |

### Branch and PR tracking

| Field | Type | Description |
|---|---|---|
| `headBranch` | string | Deterministic agent branch name. Reused across Implement re-entries for the same task. |
| `prNumber` | int | SCM PR/MR number of the current open change |
| `mergeCommitSHA` | string | SHA of the most recent merge commit on the target branch |
| `mergedHeadSHA` | string | Source-branch head SHA at the time of the most recent merge. Retained across `clearMergedChangeState` so a re-opened PR that re-proposes already-merged commits is detected as a duplicate. |

### Conversation persistence

| Field | Type | Description |
|---|---|---|
| `conversationObjectKey` | string | S3 object key for the Claude conversation transcript. Stable across state transitions. Empty until the first turn that reports it. |
| `sessionID` | string | Claude session ID. Passed back to the next Pod as `CONVERSATION_SESSION_ID` so each turn resumes the conversation with `claude --resume` rather than starting cold. |
| `handover` | string | Compacted handover text injected when the context window exceeds `agent.handoverThresholdPercent`. |

### Token accounting

| Field | Type | Description |
|---|---|---|
| `cumulativeTokens` | int64 | Total tokens consumed across all turns on this task |
| `lastTurnInputTokens` | int64 | Input tokens on the most recent completed turn |

### Re-entry context

| Field | Type | Description |
|---|---|---|
| `implementContext` | string | Optional re-entry prompt injected at the start of the next Implement turn (e.g. CI failure details, conflict notice). Cleared after the turn is submitted. |
| `parkReason` | string | Reason string from the last `Parked` transition. Cleared when the task leaves `Parked`. Carried for observability; does not gate re-activation. |

### Loop-breaker counters

Two counters prevent infinite retry loops. Both reset to zero when a PR is successfully opened.

| Field | Type | Cap behavior |
|---|---|---|
| `implementEmptyRetries` | int | Counts consecutive Implement runs that completed with zero commits. After the cap, the task is commented and parked with reason `implement-empty` instead of silently re-entering. |
| `writebackSkip4xxAttempts` | int | Counts consecutive writeback sweeps where every repo returned a permanent 4xx from `OpenChange`. After the cap, the writeback gate stops re-sweeping and records a `WritebackFailed` condition. |

### Async communication queues

| Field | Type | Description |
|---|---|---|
| `pendingComments` | `[]string` | Free-form comment bodies queued by the agent via the `comment` MCP tool. Posted to the linked SCM issue on the next reconcile, then cleared. Does not change lifecycle state. |
| `pendingInterjections` | `[]string` | Comment bodies queued by the webhook when a new issue or MR comment arrives while an agent turn is in flight. The reconciler delivers each to the live wrapper session as mid-session user input, then clears the list. |

---

## WorkItems ledger

`status.workItems` is the single source of truth for every SCM artifact this task spans:
the originating issue, any PRs it opens, proposals it files, and issues it closes. The
operator seeds the ledger lazily from `spec.source` on the first reconcile and updates it
as the agent drives actions via MCP tools.

The ledger is used for dedup, stall recovery, prompt generation, and cross-repo scope
determination via `TaskReposInScope`.

### WorkItemRef

| Field | Type | Values | Description |
|---|---|---|---|
| `provider` | string | `github`, `gitlab` | SCM provider |
| `repo` | string | `owner/repo` | Repository slug |
| `number` | int | | Issue or PR/MR number |
| `kind` | string | `issue`, `pr` | Artifact type |
| `role` | string | `source`, `closes`, `openedPR`, `proposed`, `reviewed` | How this item relates to the task |
| `state` | string | `proposed`, `approved`, `declined`, `implemented`, `open`, `closed`, `merged` | Current item state |
| `title` | string | | Item title (for prompt context) |
| `headSHA` | string | | Head commit SHA of a PR/MR at last refresh |
| `lastRefreshedAt` | time | | When the operator last synced this item's state from the SCM API |

---

## Termination

A Task is considered terminal when **either** of the following is true:

- `status.phase` is `Succeeded` or `Failed`
- `status.lifecycleState` is `Done`, `Stopped`, or `Parked`

!!! warning "Do not test `phase` alone"
    `issueLifecycle` tasks leave `status.phase` empty for their entire lifetime and signal
    completion only through `status.lifecycleState`. Code that tests `phase == Succeeded`
    will treat an active or finished lifecycle task identically. Always use the
    `TaskTerminal` helper (or its equivalent logic) for termination checks.

| Terminal state | Meaning |
|---|---|
| `Succeeded` | Non-lifecycle task completed successfully |
| `Failed` | Non-lifecycle task failed (turn timeout, max-turns hit, agent error) |
| `Done` | Lifecycle task completed (issue merged and main CI passed, or closed/declined) |
| `Stopped` | Lifecycle task administratively stopped |
| `Parked` | Lifecycle task stalled awaiting human input. Re-activated when the human comments on the linked issue. |

---

## Example

```yaml
apiVersion: tatara.dev/v1alpha1
kind: Task
metadata:
  name: implement-issue-42
  namespace: tatara
spec:
  projectRef: my-project
  repositoryRef: my-service
  kind: implement
  goal: |
    Implement the changes described in GitHub issue #42.
    Follow the existing patterns in internal/handler/.
  source:
    provider: github
    issueRef: my-org/my-service#42
    number: 42
    title: "Add retry logic to HTTP client"
```

---

## Inspecting tasks

```sh
# List all tasks for a project
kubectl -n tatara get task -l tatara.dev/project=my-project

# Wide view (Phase, Lifecycle, Kind, Turns columns)
kubectl -n tatara get task -l tatara.dev/project=my-project -o wide

# Describe a specific task
kubectl -n tatara describe task implement-issue-42

# Stream agent logs
kubectl -n tatara logs \
  $(kubectl -n tatara get task implement-issue-42 \
      -o jsonpath='{.status.podName}') \
  -c tatara-claude-code-wrapper -f

# Check work-item ledger
kubectl -n tatara get task implement-issue-42 \
  -o jsonpath='{.status.workItems}' | jq .
```
