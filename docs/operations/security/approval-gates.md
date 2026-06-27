---
title: Approval Gates
description: How humans stay in control of tatara agents - intake allowlisting, triage-driven label approval, and merge policy.
---

# Approval Gates

Tatara is designed to be useful without being autonomous. Two independent gates
prevent an agent from writing or merging code without explicit human intent: an
intake gate that controls which issues the operator acts on, and an approval gate
that controls whether a triage verdict leads to implementation. A third gate -
the merge policy - governs whether the operator merges the resulting PR
automatically or waits for a human to do it.

```mermaid
sequenceDiagram
    participant H as Human
    participant SCM as SCM (GitHub/GitLab)
    participant OP as Operator
    participant AG as Agent pod

    OP->>SCM: brainstorm: open proposal issue<br/>(tatara-brainstorming label)
    OP->>SCM: @mention maintainerLogins for review
    H->>SCM: reply in issue thread
    OP->>AG: spawn Triage agent (reads full thread)
    AG->>OP: issue_outcome(action=implement)
    OP->>OP: self-approve guard:<br/>human comment present?
    OP->>SCM: add tatara-approved label
    OP->>AG: spawn Implement agent
    AG->>SCM: open PR
    H->>SCM: review PR
    OP->>SCM: merge (per mergePolicy)
```

## Gate 1: Intake - who can drive the lifecycle

The intake gate controls which issues and issue comments the operator acts on.
By default the gate is **open**: the operator processes issues from any author.
When `spec.scm.reporterLogins` is non-empty the gate becomes **restricted**:
only these authors (plus the bot and any maintainer) may drive the lifecycle.
Everything else is dropped at intake - cron scan and webhook alike - so
unenrolled third parties cannot submit arbitrary work to agents.

The effective reporter set for a given repository is:

1. The configured `botLogin` - always trusted, unconditionally.
2. Every login in `spec.scm.maintainerLogins` - always trusted, unconditionally.
3. Every login explicitly listed in `spec.scm.reporterLogins` - trusted when the
   list is non-empty.

An empty `reporterLogins` disables the gate entirely (historical open behavior).

!!! warning "Default: open intake"
    With an empty `reporterLogins`, any SCM user who can file an issue on an
    enrolled repository can drive tatara. Enable the gate for any project where
    the repositories are publicly visible or where you do not want unsolicited
    automation.

```yaml
apiVersion: tatara.dev/v1alpha1
kind: Project
metadata:
  name: my-project
spec:
  scm:
    provider: github
    owner: my-org
    botLogin: my-bot
    reporterLogins:       # restrict intake to these accounts
      - alice
      - ci-system
    maintainerLogins:     # see Gate 2
      - alice
      - bob
```

## Gate 2: Triage approval - who can approve implementation

Before an agent writes any code the operator must see evidence of human approval.
Approval happens through the normal SCM comment thread on the issue, not through
a separate UI or label click. The triage agent reads the full thread (title, body,
all comments) and calls an `issue_outcome` MCP tool with one of three verdicts:

| Verdict | Meaning |
|---------|---------|
| `implement` | Triage agent judges the issue ready for implementation |
| `discuss` | More information or human input is needed |
| `close` | Issue should be rejected and closed |

### Self-approve guard

Tatara never approves its own brainstorm proposals without human engagement. When
the triage agent returns `implement` for a bot-authored issue:

1. The operator checks the comment thread for at least one comment whose author
   is **not** the bot.
2. If no such comment exists, the verdict is downgraded to `discuss` - the issue
   stays in brainstorming (`tatara-brainstorming` label) and the operator enters
   Conversation phase, waiting for human input. No Implement agent is spawned.
3. If a human comment is present, the verdict is honored: the `tatara-approved`
   label is applied and the Implement agent is dispatched.

This guard is fail-closed: if the authorship check fails for any reason (SCM
error, token issue), the operator treats the issue as bot-authored and withholds
approval.

### Third-party fast path

Issues filed by a known third-party contributor (an author who is neither the bot
nor a maintainer, but is in the `reporterLogins` allowlist) bypass the self-approve
guard and proceed directly to implementation when the triage verdict is `implement`.
The reasoning: a human already filed the work request; no additional approval signal
is needed.

### Who counts as a "human" for the approval check

When `spec.scm.maintainerLogins` is **non-empty**: only comments from accounts in
that list satisfy the human-engagement requirement for bot-authored proposals, and
only those accounts' comment intent is read by the triage agent as authoritative.

When `spec.scm.maintainerLogins` is **empty**: any comment from any non-bot account
satisfies the guard (historical behavior, preserved for migration compatibility).

!!! note "Approval is conversational, not keyword-driven"
    The triage agent reads natural language. "Looks good, ship it" and "approved -
    implement this week" are both sufficient. There is no magic keyword or command
    syntax required.

### Human label override (Conversation phase)

While the task is in Conversation (awaiting human input), the operator polls the
SCM issue for label changes on every reconcile. A human may bypass the triage
conversation entirely by directly applying a label:

- Add `tatara-approved` - the operator immediately transitions the task to
  Implement, skipping a fresh Triage agent run.
- Add `tatara-declined` - the operator parks the task with reason `human-declined`.

This path is also the recovery mechanism for proposals whose triage discussion
has stalled: apply the label directly to unblock the queue.

## Gate 3: Merge policy

The `spec.scm.mergePolicy` field controls whether the operator merges the
agent's PR automatically or defers to human action.

| Value | Behavior |
|-------|---------|
| `afterApproval` (default) | Operator merges when the agent signals `pr_outcome=merge`. CI state is not checked. |
| `autoMergeOnGreenCI` | Operator merges only when all CI checks report `success`. If CI is absent, falls back to `afterApproval`. |

```yaml
spec:
  scm:
    mergePolicy: autoMergeOnGreenCI   # recommended for production services
```

!!! warning "afterApproval does not require a live PR review"
    Under `afterApproval`, the operator trusts `pr_outcome=merge` as the agent
    relaying an approving signal from outside (e.g., a human reviewed the PR in
    GitHub and left a comment for the agent to proceed). It does **not**
    independently verify the GitHub review state. If you need a hard merge gate
    that requires a human approving review, use `autoMergeOnGreenCI` combined
    with a branch protection rule requiring an approved review before CI can pass.
    The CI gate then acts as a proxy for both code correctness and human review.

### Recommended branch protection (GitHub)

For production repositories enrolled in tatara with `afterApproval`:

- Require at least 1 approving review from a code owner.
- Dismiss stale reviews on push.
- Require status checks to pass before merging.
- Restrict who can push to the protected branch to the bot account and maintainers.

With these rules in place, a human review is a prerequisite for CI to be mergeable,
giving `autoMergeOnGreenCI` an effective human gate.

## Per-repository overrides

Both allowlists can be overridden at the Repository CR level, independently of the
Project. This lets you tighten gates on sensitive repositories without changing the
project-wide defaults.

```yaml
apiVersion: tatara.dev/v1alpha1
kind: Repository
metadata:
  name: payments-service
spec:
  projectRef: my-project
  url: https://github.com/my-org/payments-service
  maintainerLogins:    # overrides project-level for this repo only
    - alice
    - security-lead
  reporterLogins:      # overrides project-level for this repo only
    - alice
    - security-lead
```

Override semantics:

| Field on Repository | Effect |
|--------------------|--------|
| Not set (`null`) | Inherits the Project's list |
| Set to an explicit list (including empty `[]`) | Replaces the Project's list for this repository only |

An explicit empty list `[]` **opens** intake for that repository to any SCM
author (clears the project-level allowlist entirely), regardless of the
project-level `reporterLogins`. To close intake to only the bot and
maintainers, set `reporterLogins` to a non-empty list containing only the
trusted accounts.

## Label set reference

The operator manages the following labels. Names are configurable via the
`spec.scm.*Label` fields on the Project; defaults are shown.

| Label | Default name | Color | Phase |
|-------|-------------|-------|-------|
| Brainstorming | `tatara-brainstorming` | `#1d76db` (blue) | Triage/Conversation - proposal under discussion |
| Approved | `tatara-approved` | `#0e8a16` (green) | Ready for implementation |
| Implementation | `tatara-implementation` | `#fbca04` (yellow) | Implement agent active |
| Declined | `tatara-declined` | `#9e9e9e` (gray) | Rejected - no implementation |
| Incident | `tatara-incident` | `#d73a4a` (red) | Additive; incident-originated proposal |

The operator enforces exactly one phase label per managed issue at any time.
It adds the desired label and removes all other managed labels atomically.
The `tatara-incident` label is additive and never swept by the phase reconciler -
an incident proposal can carry both `tatara-incident` and `tatara-brainstorming`
simultaneously.

!!! note "Legacy labels"
    `tatara-idea` and `tatara-rejected` are deprecated aliases kept for migration
    compatibility. The operator still recognizes them for dedup and backstop
    purposes but no longer applies them to new issues. Migrate existing issues to
    the current label names at your convenience.

## Complete approval flow

```mermaid
flowchart TD
    A([Issue filed or brainstorm proposal]) --> B{Intake gate\nreporterLogins}
    B -- author not allowed --> Z1([Dropped - no action])
    B -- author allowed --> C[Triage agent reads\nfull comment thread]
    C -- outcome: close --> D[tatara-declined label\nIssue closed]
    C -- outcome: discuss --> E[tatara-brainstorming label\nConversation phase]
    C -- outcome: implement --> F{Bot-authored\nproposal?}
    F -- no, third-party --> G[tatara-approved label\nImplement agent]
    F -- yes --> H{Human comment\nin thread?}
    H -- no --> E
    H -- yes --> G
    E --> I{Human applies\nlabel manually?}
    I -- tatara-approved --> G
    I -- tatara-declined --> Z2([Parked - human-declined])
    I -- no label change --> J{ConversationIdleMinutes\nelapsed?}
    J -- yes --> Z3([Stopped - resumable])
    J -- no --> I
    G --> K[Agent opens PR]
    K --> L{mergePolicy}
    L -- afterApproval --> M{pr_outcome=merge\nfrom agent}
    L -- autoMergeOnGreenCI --> N{CI green?}
    N -- yes --> O([Operator merges PR])
    N -- no --> P([Hold - awaiting CI])
    M -- yes --> O
```
