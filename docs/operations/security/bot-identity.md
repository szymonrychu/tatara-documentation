---
title: Bot Identity
---

# Bot Identity

## Dedicated bot account

Tatara operates under a **dedicated bot GitHub/GitLab account**, separate from any human developer account. For the reference deployment this is `szymonrychu-bot`. For your deployment, create a separate machine account before enrolling any repositories.

A dedicated account gives autonomous work its own attributable identity, a PAT you can scope and revoke independently of humans, and a stable `botLogin` the operator keys its self-loop and self-approval guards on. The load-bearing enforcement is in the two subsections below (approver-set exclusion, projected token mount), not in the choice of account itself.

## Required PAT scopes

=== "GitHub"
    | Scope | Purpose |
    |---|---|
    | `repo` | Read/write to enrolled repositories (clone, branch push, PR open) |
    | `read:org` | Read organization membership |
    | `read:user` | Read user profile (bot identity verification) |

=== "GitLab"
    | Scope | Purpose |
    |---|---|
    | `api` | Full API access (MR create, comment, label, review, merge) |
    | `read_repository` + `write_repository` | Clone + push |

## Secret storage

The PAT is stored as a Kubernetes Secret referenced by `spec.scmSecretRef` on the Project CR:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: tatara-scm
  namespace: tatara
stringData:
  token: "<bot-pat>"
```

The key within the Secret is exactly `token` (validated alongside `webhookSecret` at Project reconcile; a missing/empty key holds the Project not-ready with `SecretMissingKeys`). This Secret is managed as a SOPS-encrypted raw manifest, not via the Helm chart - the chart only templates the reference to the Secret name.

Two consumers read the `token` key:

- **Operator (server-side):** reads `token` via the Kubernetes API for its own SCM write-backs (comments, labels, reviews, PR opens, merges) and read scans. Every SCM write on this platform is the operator's; agent pods hold the same token for `git` only.
- **Wrapper pods:** the operator injects it as the `GIT_TOKEN` env var via a `SecretKeyRef` from the same `token` key, so the agent's `git`/SCM operations run as the bot. The token is never baked into the pod spec or image. The wrapper also re-exports it as `GITHUB_TOKEN` and `MISE_GITHUB_TOKEN` process env so `mise install` (and its aqua backend) authenticate as the bot when fetching tool releases from the GitHub API, avoiding unauthenticated rate limits during bootstrap. Both keys carry a `_TOKEN` suffix and are auto-redacted from logs.

## Bot as commit author

Agent pods commit using the bot identity. Set `spec.scm.botEmail` to the bot's commit email:

```yaml
spec:
  scm:
    botLogin: my-org-bot
    botEmail: "my-org-bot@users.noreply.github.com"
```

GitHub and GitLab have dedicated noreply commit email formats for bot accounts. Using these keeps the human developer's commit identity off autonomous changes.

## Bot exclusion from approval gates

`spec.scm.maintainerLogins` must NOT include `botLogin` - but the guard does not depend on that
convention. Approval is granted by a maintainer comment whose text matches
`Project.spec.scm.approvalPhrases`. The bot is excluded **structurally**: every ingested comment
carries `isBot`, set from `Project.spec.scm.botLogin`, and the grammar refuses a bot-authored
comment before it reads the text.

Three independent checks keep the bot from ever satisfying the gate:

1. **Bot-authored comments never enter the queue.** The event filter drops any comment whose author
   is `botLogin` before it becomes a pending event, so the operator's own posts - including the
   comment it writes when it parks a Task for a missing approval - can never re-drive the Task they
   describe.
2. **The grammar refuses `isBot` before it reads the body.** A comment carrying `isBot: true` is
   rejected on identity, not on wording. An agent pod acting as the bot cannot approve its own work
   by typing an approval phrase into a thread.
3. **The maintainer check itself excludes the bot.** `IsMaintainer` returns `false` for `botLogin`
   even if it were mistakenly present in `maintainerLogins`, so the bot cannot satisfy the identity
   clause on its own merits either.

This is enforced in code, not through configuration. See
[Approval Gates](approval-gates.md#the-approval-grammar) for the full grammar, and for why approval
is defined over comment text rather than over an event on a thread.

## One identity, and what it costs

The platform has exactly **one** bot identity. The operator and every agent pod act as the same
account. That buys a single revocable credential and a clean audit trail, and it costs the platform
one control it would otherwise have.

A forge will not let an identity make a review **decision** on its own pull request. GitHub returns
`422` for both `APPROVE` and `REQUEST_CHANGES` from the PR author; only a `COMMENT` review is
permitted. So when a `review` pod submits an approving verdict, the operator posts it as a review
comment and then merges. **The approval of record is the merge**, not a green review state on the PR.

!!! danger "Accepted risk: the merge gate is operator logic, not a forge permission"
    Two consequences follow, and both are accepted explicitly:

    1. **Branch protection cannot require an approving review.** Nothing on this platform could
       ever satisfy such a rule - the only account that could approve is the account that opened
       the PR - so enabling it would deadlock every merge, permanently.
    2. **An agent pod holds the same `GIT_TOKEN` the operator merges with.** The forge cannot
       distinguish a merge call made by a pod from one made by the operator. A deliberately
       malicious or catastrophically confused agent has a possible path to merging its own PR.

    The residual risk is accepted, and answered with **detection rather than prevention**: a merge
    the operator did not initiate is caught by the sweep and raises
    `operator_unexpected_merge_total`, a critical alert.

    What remains, and is worth having: branch protection **forbidding direct pushes to `main`** on
    every enrolled repository (this needs no review requirement and is the highest-value control
    available under one identity); a scoped installation token in place of an org-wide PAT; and
    `gh`, `glab` and direct-to-forge-API `curl` on the agent pod's deny-list, so a compliant agent
    has no ergonomic path to the merge endpoint. **That deny-list is an in-cluster rule only** -
    workstation skills run by a human with their own credentials keep `gh` and keep human-driven
    merge.

## Comment turn-taking gate

Mechanical bot comments (park notes, terminal-diagnostics posts, alert-group re-fire recurrence notes) pass through a turn-taking gate before posting, so the operator does not pile up repeated comments on the same thread:

- **Rule 2 - never comment on the bot's own PR/MR.** If the target PR/MR was authored by `botLogin`, the comment is withheld outright. The author check trusts the webhook's `AuthorLogin` hint only for GitHub (where it is the true author); on GitLab, where that field is the webhook actor rather than the resource author, the operator reads the PR/MR's authoritative author instead.
- **Rule 1 - stay silent while the bot already has the last word.** On an issue or a human-authored PR/MR, the operator withholds a new comment if its own most recent comment on the thread is still unanswered - unless a comment from a *silence breaker* (an account listed in `reporterLogins` or `maintainerLogins`, unioned; if both lists are empty, any non-bot account qualifies) landed at or after it. This is what stops the runaway re-commenting an alert re-fire or a repeatedly-failing terminal task used to produce.

Both rules fail open: a missing bot login, an unreadable comment list, or any other read error lets the comment through rather than silently dropping it. A withheld comment is recorded as an `operator_scm_writes_total{result="suppressed_bot_mr"}` or `{result="suppressed_last_word"}` write instead - see [Observability](../observability.md#core-counters).

Not every mechanical comment is gated. One-shot, already-idempotent posts - the final "Done, opened PR: ..." outcome notice and PR/MR review verdicts (which key off head SHA and are deduped elsewhere) - post unconditionally, since gating them would risk suppressing the one notice a thread actually needs.

## One OIDC identity for all agent pods

All agent pods authenticate to tatara-memory and the operator REST API using the **same** OIDC client credentials (the `tatara-claude-code-wrapper` Keycloak client). The `sub` claim is the service account UUID and is identical across all pods.

Per-task authorization relies on task context embedded in the pod env (`TATARA_TASK`, `TATARA_PROJECT`) and the operator REST API validating that the submitted task scope matches the request. It does not rely on OIDC identity differentiation.

See [Identity & OIDC](../../architecture/identity-and-oidc.md) for the full token flow.
