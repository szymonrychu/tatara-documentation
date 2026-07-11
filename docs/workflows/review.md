---
title: PR Review Workflow
---

# PR Review Workflow

The `review` workflow triggers when a human-authored PR is opened (or updated) in an enrolled repository. The agent checks out the PR head read-only, reviews the changes, and posts a structured review verdict.

## Trigger

Two independent paths feed `review` Tasks:

1. **Webhook:** a pull request is opened or synchronized on an enrolled repository.
2. **`mrScan` cron:** a periodic scan (peer of `issueScan`) that lists open PRs/MRs on enrolled repos and picks up any candidate the webhook missed, so a dropped or delayed webhook is not a permanent gap.

Both paths apply the same scope check via `spec.scm.prReactionScope`:

| Value | Behavior |
|---|---|
| Empty / unset (**the default**) | Reacts to **every** open human PR/MR - the historical, permissive behavior |
| `labeledOrMentioned` | Only PRs with the `triggerLabel` OR that mention the bot |
| `all` | Every PR in every enrolled repository (equivalent to the unset default, explicit) |

!!! warning "Default is permissive, not `labeledOrMentioned`"
    The default `prReactionScope` is empty, which reviews every open PR/MR. This is intentional: a defaulted value would be indistinguishable from an explicit `labeledOrMentioned`, so the field is opt-in rather than kubebuilder-defaulted to the narrower scope. Set `prReactionScope: labeledOrMentioned` explicitly to restrict review to labeled/mentioned PRs.

The PR author must not be the bot itself (bot-authored PRs go through the `implement`/deploy-supervisor auto-merge path, not review).

## Workflow

```mermaid
flowchart TD
    A[PR opened / synchronized] --> B[Webhook received]
    A2[mrScan cron] --> B2[Candidate PR/MR found]
    B --> C{prReactionScope check\n+ author != bot?}
    B2 --> C
    C -->|no| D[Ignore]
    C -->|yes| C2{Re-review dedup:\nhead SHA changed?}
    C2 -->|no, same head, terminal task exists| D
    C2 -->|yes| E[Create Task: review]
    E --> F[Spawn agent Pod\nwith PR head checked out read-only]
    F --> G[Agent reads diff + codebase context]
    G --> H[Agent posts review:\n approve / request_changes / comment]
    H --> I{Decision}
    I -->|approve| J[GitHub review: approved]
    I -->|request_changes| K[GitHub review: changes requested\nwith inline suggestions]
    I -->|comment| L[GitHub review: comment only]
```

## Re-review dedup

`mrScan` (and the webhook path) suppress re-review of a PR/MR whose head commit has not changed since the last completed review Task. The dedup key is the PR's current `headSHA` compared against the head SHA recorded on the matching Task's `role:reviewed` ledger entry (`WorkItemRef.HeadSHA`); pre-ledger Tasks fall back to a legacy `tatara.io/head-sha` label. Same head + a terminal review Task already exists -> suppressed. A new commit pushed to the PR changes the head SHA -> re-review proceeds.

!!! note "Past incident"
    An earlier version of this dedup only consulted the `role:openedPR` ledger entry, which a
    human-PR review Task never carries, so the check silently no-opped and the same MR was
    re-reviewed on every `mrScan` cycle, burning tokens. The fix's shared helper `headSHAForTask`
    now reads `HeadSHA` from **either** a `role:openedPR` or a `role:reviewed` entry (then falls
    back to `MergedHeadSHA` and the legacy `tatara.io/head-sha` label). For a human-PR review Task
    the operative entry is `role:reviewed`, so the dedup keys on the reviewed head as intended.

## Read-only constraint

The review agent **never pushes and never merges, and never calls a merge API**. The PR head is
checked out in `/workspace` read-only. Review's only two writeback actions are (a) a native
SCM review (approve / request-changes / comment) and (b) re-adding `tatara-implementation` to
invoke `implement` again on an unmergeable MR.

!!! note "Auto-merge is unrelated to review's writeback path"
    Native auto-merge (the [deploy supervisor](deploy-supervisor.md) `semver:*`-gated mechanism)
    is a completely separate code path - review's approval is what makes the deploy supervisor
    willing to merge, but review itself never touches the merge API.

## Review output

The agent posts a GitHub/GitLab review with:

- **Decision:** `approve`, `request_changes`, or `comment`
- **Summary comment:** overall assessment
- **Inline suggestions:** `Suggestion` objects at specific file + line locations, formatted as GitHub suggestion blocks

```go
type ReviewVerdict struct {
    Decision    string             // approve | request_changes | comment
    Body        string             // review summary
    Suggestions []Suggestion       // inline suggestions
    Semver      []SemverAssignment // approve only: per-MR semver:<level> assignments
}

type Suggestion struct {
    Path string
    Line int
    Body string  // suggested replacement code
}

type SemverAssignment struct {
    Repo   string // owner/repo slug, matches WorkItemRef.Repo
    Number int    // MR/PR number
    Level  string // major | minor | patch
}
```

## Conversation persistence for reviews

Each PR gets its own conversation, distinct from any related issue's conversation. If the PR is synchronized (new commits pushed), the next review turn resumes from the prior conversation, giving the agent context about what it already reviewed.

## Approve-label + native review is the whole merge signal

On `approve`, review applies `tatara-approved` to the PR/MR **and** posts a native PR approval -
that is the entire approval signal the deploy supervisor consults. There is no separate human
maintainer sign-off step in the shipped default flow; review's approval, from a pod that
structurally never wrote the diff it is reviewing, is the merge gate. If review instead finds
any MR under the Task unmergeable (conflict, failed pipeline), it withholds approval and
re-adds `tatara-implementation` to invoke `implement` again - see
[Deploy Supervisor](deploy-supervisor.md) for what happens once approval + green CI both hold.

## Semver labeling on approve

On the same `approve` action, review also assigns a per-MR `semver:<level>` label to **every**
MR in the stream - human/maintainer-authored MRs and tatara-created MRs alike. This closes a real
gap: `change_significance` (declared via `change_summary`) is an `implement`-only signal, so a
human-authored MR in the same stream previously got no semver label from anyone, and the push-CD
pipeline refused to cut a release tag for it - the change merged but never deployed.

Rules, applied best-effort in the approve writeback (a labeling failure never blocks the
`approve` verb itself):

- **Per-MR level.** Review judges each member MR's level independently from its own diff:
  breaking change -> `major`, backward-compatible new functionality -> `minor`, fix/docs/other
  -> `patch`. One stream can mix levels across its member MRs (e.g. a docs-only MR at `patch`
  alongside an API-changing MR at `minor`).
- **Respect an existing human label.** If a member MR already carries any `semver:*` label,
  review leaves it untouched - a deliberately human-set level is authoritative and is never
  overwritten. This also makes the pass idempotent for bot MRs the operator already labeled at
  PR-open time (see [Deploy Supervisor Component 2](deploy-supervisor.md#component-2-bot-gated-auto-merge)).
- **Fallback for unassigned bot MRs.** A tatara-authored MR with no existing label falls back to
  the `change_significance` its `implement` agent already declared via `change_summary`, then to
  `patch` if that is also absent.
- **Sole stamping opportunity for human MRs.** Human-authored MRs never call `change_summary`, so
  this is the only place a human MR gets a `semver:*` label at all - without it, `cd-release` has
  nothing to tag from and the merged change never ships.

The assignments travel on the `review_verdict` MCP call as `ReviewVerdict.Semver`
(`[]SemverAssignment{Repo, Number, Level}`, see the struct above). See
[Deploy Supervisor Component 1b](deploy-supervisor.md#component-1b-review-semver-stamping-human-mrs)
for how the operator applies this list and where it fits in the wider push-CD cascade.
