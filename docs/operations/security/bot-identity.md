---
title: Bot Identity
---

# Bot Identity

## Dedicated bot account

Tatara operates under a **dedicated bot GitHub/GitLab account**, separate from any human developer account. For the reference deployment this is `szymonrychu-bot`. For your deployment, create a separate machine account before enrolling any repositories.

**Why a dedicated account:**

- **Audit trail:** all autonomous commits and PR comments are clearly attributed to the bot, not to a human developer's personal account.
- **Permission scoping:** the bot only needs `repo` + `read:org` on the enrolled repositories, not full org admin.
- **Revocability:** revoking the bot's PAT immediately stops all tatara activity without affecting any human accounts.
- **Dedup loop prevention:** the operator gates on `botLogin` to avoid reacting to its own PR events, preventing infinite loops.

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
    | `api` | Full API access (MR create, comment, label, approve) |
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

This Secret is managed as a SOPS-encrypted raw manifest (not via Helm chart - the chart only templates the reference to the Secret name). The operator reads it via projected volume.

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

`spec.scm.maintainerLogins` must NOT include `botLogin`. The operator unconditionally excludes `botLogin` from the approver set at the controller level - a bot-authored comment can never self-approve an implementation, regardless of `maintainerLogins` configuration.

This is enforced in code, not through configuration. Setting `botLogin` in `maintainerLogins` would be silently ignored.

## One OIDC identity for all agent pods

All agent pods authenticate to tatara-memory and the operator REST API using the **same** OIDC client credentials (the `tatara-claude-code-wrapper` Keycloak client). The `sub` claim is the service account UUID and is identical across all pods.

Per-task authorization relies on task context embedded in the pod env (`TATARA_TASK`, `TATARA_PROJECT`) and the operator REST API validating that the submitted task scope matches the request. It does not rely on OIDC identity differentiation.

See [Identity & OIDC](../../architecture/identity-and-oidc.md) for the full token flow.
