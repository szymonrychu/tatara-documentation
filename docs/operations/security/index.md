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

    How a maintainer's **comment**, matched against `Project.spec.scm.approvalPhrases` and pinned as single-use evidence, is required before any code is written, and how the operator-owned merge gate works.

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
  | Approval grammar, run by the OPERATOR over the mirrored thread:
  |   the most recent maintainer comment, whose TEXT matches an entry in
  |   approvalPhrases, pinned as single-use ApprovalEvidence.
  |   The bot is excluded structurally. Labels are never read.
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
| Implementation approval | The [approval grammar](approval-gates.md#the-approval-grammar): the most recent maintainer comment on the thread, whose normalised text is an anchored whole-line match against `Project.spec.scm.approvalPhrases`, pinned as single-use `ApprovalEvidence`. `maintainerLogins` is closed by default (an empty list approves nothing) |
| Code merge gate | The **operator** merges, on an accepted review verdict, on green CI, at the exact reviewed head SHA. No MCP tool exposes merge; the forge's native merge-on-green is never armed. The gate is operator logic, not a forge control - see the accepted risk on the approval-gates page |
| API authentication | OIDC bearer tokens, per-service audience |
| Agent tool surface | `TATARA_TOOL_PROFILE` per task kind; 20 tools, fail-closed |
| Agent headless mode | Interactive pickers hard-denied in `settings.json` |
| Bot exclusion from self-approval | Every mirrored comment carries `isBot`, set from `botLogin`; the grammar refuses a bot-authored comment before it reads the text |
| Commit identity | Bot email only (`botEmail` on ScmSpec) |
| Secret storage | SOPS-encrypted values files; never plaintext in git |
