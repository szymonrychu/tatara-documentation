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

The key within the Secret is exactly `token` (validated alongside `webhookSecret` at Project reconcile; a missing/empty key holds the Project not-ready with `SecretMissingKeys`). This Secret is managed as a SOPS-encrypted raw manifest, not via the Helm chart - the chart only templates the reference to the Secret name.

Two consumers read the `token` key:

- **Operator (server-side):** reads `token` via the Kubernetes API for its own SCM write-backs (comments, labels, approvals, PR opens) and read scans.
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

`spec.scm.maintainerLogins` must NOT include `botLogin`. The self-approve guard does not depend on that convention: the approval-comment scan skips any comment authored by `botLogin` before the approver-membership check runs (`if c.Author == "" || c.Author == botLogin { continue }` in the lifecycle controller), so a bot-authored comment can never release the self-approve hold or count as an approval - even if `botLogin` were mistakenly listed in `maintainerLogins`.

This is enforced in code, not through configuration. See [Approval Gates](approval-gates.md) for how the self-approve hold and the human-comment release fit into the full merge-gate flow.

## One OIDC identity for all agent pods

All agent pods authenticate to tatara-memory and the operator REST API using the **same** OIDC client credentials (the `tatara-claude-code-wrapper` Keycloak client). The `sub` claim is the service account UUID and is identical across all pods.

Per-task authorization relies on task context embedded in the pod env (`TATARA_TASK`, `TATARA_PROJECT`) and the operator REST API validating that the submitted task scope matches the request. It does not rely on OIDC identity differentiation.

See [Identity & OIDC](../../architecture/identity-and-oidc.md) for the full token flow.
