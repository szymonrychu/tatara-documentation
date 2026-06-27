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

    How human approval is required before any code is written or merged.

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
  | Maintainer allowlist check (approval comment from maintainerLogins?)
  v
Task transitions to Implement
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
  | Human maintainer review + merge (afterApproval policy)
  v
Merged to main
```

Every boundary is authenticated. Every gate requires a human decision before code reaches production.

## Security posture summary

| Control | Mechanism |
|---|---|
| Webhook authenticity | HMAC-SHA256 signature validation |
| Issue intake gate | `reporterLogins` allowlist |
| Implementation approval | `maintainerLogins` allowlist + natural-language triage |
| Code merge gate | `afterApproval` merge policy (human-only merge) |
| API authentication | OIDC bearer tokens, per-service audience |
| Agent tool surface | `TATARA_TOOL_PROFILE` per task kind |
| Agent headless mode | Interactive pickers hard-denied in `settings.json` |
| Bot exclusion from self-approval | `botLogin` excluded from approver set at controller level |
| Commit identity | Bot email only (`botEmail` on ScmSpec) |
| Secret storage | SOPS-encrypted values files; never plaintext in git |
