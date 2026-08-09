---
title: Security
---

# Security

Tatara runs autonomous AI agents with write access to your repositories. The security model is explicit, layered, and designed so that every boundary is authenticated and every gate requires a human-in-the-loop step before code reaches production.

<div class="grid cards" markdown>

-   :material-robot: **Bot Identity**

    ---

    The dedicated bot account, its permissions, and why shared credentials are unsafe.

    [:octicons-arrow-right-24: Bot Identity](bot-identity.md)

-   :material-shield-check: **Approval Gates**

    ---

    How a maintainer's **comment**, judged by the agent and independently re-verified by the operator and pinned as single-use evidence, is required before any code is written, and how the operator-owned merge gate works.

    [:octicons-arrow-right-24: Approval Gates](approval-gates.md)

-   :material-needle: **Prompt-Injection Defenses**

    ---

    How tatara prevents untrusted issue content from hijacking the agent.

    [:octicons-arrow-right-24: Prompt Injection](prompt-injection.md)

-   :material-webhook: **Webhook & Egress Security**

    ---

    HMAC webhook validation, egress controls, and the reporter allowlist.

    [:octicons-arrow-right-24: Webhooks & Egress](webhooks.md)

</div>

## Trust model summary

```
Internet
  |
  | HMAC-verified webhook (GitHub/GitLab secret)
  v
tatara-operator (webhook server)
  |
  | Reporter allowlist check (issue author in reporterLogins?)
  v
QueuedEvent created
  |
  | The implement agent judges the thread and CITES a comment (external_id +
  |   verbatim quote). The OPERATOR independently verifies structure, not
  |   intent - the citation exists, its author is a verified non-bot
  |   maintainer, the quote truly occurs in the body the operator holds,
  |   and it has not already been consumed - pinned as single-use
  |   ApprovalEvidence. The bot is excluded structurally. Labels are never
  |   read. There is no most-recent-comment check: reading whether a later
  |   comment withdraws an earlier approval is the agent's job, not the
  |   operator's.
  v
Task reaches the approved stage
  |
  | OIDC-authenticated (bearer token, audience scoped per service)
  v
tatara-memory / wrapper APIs
  |
  | Headless Claude Code (no interactive prompts)
  | Interactive pickers hard-denied in settings.json
  v
Code commit + PR open (bot identity only)
  |
  | review pod submits a verdict; the OPERATOR posts the review
  | the OPERATOR merges, on green CI, at the exact reviewed head SHA
  v
Merged to main
```

Every boundary is authenticated. Merge is an operator action: no agent can call
it, and no pull request is ever left armed to merge itself once CI goes green.
The one place the platform's guarantees stop short of a forge-enforced control is
the merge itself - see
[the accepted risk of a single bot identity](approval-gates.md#gate-3-merge-an-operator-action).

## Security posture summary

| Control | Mechanism |
|---|---|
| Webhook authenticity | HMAC-SHA256 signature validation |
| Issue intake gate | `reporterLogins` allowlist |
| Implementation approval | The [approval grammar](approval-gates.md#the-approval-grammar): the `implement` agent judges whether a comment approves and cites it, plus pins the plan it wants approved; the operator verifies the citation exists, its author is a verified non-bot maintainer, its quoted text truly occurs in the body the operator holds, and it has not already been consumed - pinned as single-use `ApprovalEvidence`. There is no most-recent-comment check; that judgment is the agent's. `maintainerLogins` is closed by default (an empty list approves nothing) |
| Code merge gate | The **operator** merges, on an accepted review verdict, on green CI, at the exact reviewed head SHA. No MCP tool exposes merge; the forge's native merge-on-green is never armed. The gate is operator logic, not a forge control - see the accepted risk on the approval-gates page |
| API authentication | OIDC bearer tokens, per-service audience |
| Agent tool surface | `TATARA_TOOL_PROFILE` per task kind; 20 tools, fail-closed |
| Agent headless mode | Interactive pickers hard-denied in `settings.json` |
| Bot exclusion from self-approval | Every mirrored comment carries `isBot`, set from `botLogin`; the operator refuses a bot-authored citation before it checks the quoted text |
| Commit identity | Bot email only (`botEmail` on ScmSpec) |
| Secret storage | SOPS-encrypted values files; never plaintext in git |
