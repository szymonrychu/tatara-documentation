# Operator sops-managed secrets Design

**Date:** 2026-06-08
**Status:** approved design, pre-plan.
**Scope:** Move the operator's cluster-managed secrets from manual `kubectl`
creation to sops-encrypted helmfile values rendered by the tatara-operator chart.

## Purpose

Today four secrets the operator depends on are created by hand with
`kubectl create secret` (`tatara-anthropic`, `tatara-cli-oidc`,
`lightrag-openai`, `tatara-scm`). Only `operatorOidcClientSecret` is
sops-managed (rendered by the chart). The operator must be deployable and its
secrets rotatable purely through the infra helmfile's sops-encrypted values, with
no manual secret editing. The chart creates the secrets; CRs and operator config
reference them by path (`Project.spec.scmSecretRef`, `*SecretName` config), which
is the model that already exists.

## Secrets in scope

Data keys are fixed by consumer code (do not invent new ones):

| Rendered Secret | Name from value | Data keys | Consumer |
| --- | --- | --- | --- |
| `tatara-anthropic` | `anthropicSecretName` | `oauth-token` | spawned agent (`internal/agent/pod.go:82`, `CLAUDE_CODE_OAUTH_TOKEN`) |
| `tatara-cli-oidc` | `cliOidcSecretName` | `client-id`, `client-secret` | spawned agent (`pod.go:84-85`) |
| `lightrag-openai` | `openaiSecretName` | `LLM_BINDING_API_KEY` | per-Project lightrag (`internal/memory/lightrag.go:50`) |
| `tatara-scm` | `scmSecretName` (new value) | `token`, `webhookSecret` | agent (`pod.go:83`), ingest clone (`internal/ingest/job.go:120`), webhook HMAC (`internal/webhook/server.go:212`) |

Out of scope: `operatorOidcClientSecret` (already rendered); per-Project
generated neo4j / postgres passwords (operator-generated, not user-supplied).

## Changes

### A. Operator chart (`charts/tatara-operator`)

- `values.yaml`: add empty camelCase scalar defaults so the chart stays
  cluster-agnostic (hard rule 14, real values only in infra sops):
  `anthropicOauthToken: ""`, `cliOidcClientId: ""`, `cliOidcClientSecret: ""`,
  `openaiApiKey: ""`, `scmToken: ""`, `scmWebhookSecret: ""`, and the secret
  name `scmSecretName: ""`. (`anthropicSecretName` / `cliOidcSecretName` /
  `openaiSecretName` already exist.)
- `templates/secret.yaml`: render one Secret per item, each gated
  `{{- if .Values.<value> }}`, named from its `*SecretName` value, data keys
  exactly as the table above, values `b64enc`-d. Mirrors the existing
  `operatorOidcClientSecret` Secret block. A Secret is skipped entirely when its
  value is empty, so a cluster that supplies the secret out-of-band is unaffected.
- Bump `Chart.yaml` `version` `0.2.2 -> 0.2.3`. `appVersion` stays `0.2.2`:
  chart-only change, the Go binary/image is unchanged and is not rebuilt.

Data keys `oauth-token`, `LLM_BINDING_API_KEY`, `webhookSecret` are immutable
consumer contracts; they deviate from hard rule 6's kebab-case convention because
the consuming code pins them. This is intentional, not a new mapping.

### B. Infra helmfile (`~/Documents/infra/helmfile/helmfiles/tatara`)

- `values/tatara-operator/default.secrets.yaml`: add the six sops-encrypted
  values via the `sops-secret-helper` skill (`set`, user-run, value from tty).
- `values/tatara-operator/default.yaml`: add `scmSecretName: "tatara-scm"`
  (the name the `tatara` Project's `scmSecretRef` points at).
- Bump the chart version pin `0.2.2 -> 0.2.3` in `helmfile.yaml.gotmpl`. The
  image tag pin in `values/tatara-operator/common.yaml` stays `0.2.2`.

### C. Migration (delete + recreate)

The existing secrets were `kubectl apply`-created (not helm-owned), so a chart
that renders same-named Secrets would hit helm's adoption conflict. Per the
chosen migration: `kubectl -n tatara delete secret tatara-anthropic
tatara-cli-oidc lightrag-openai tatara-scm`, then `helmfile -e default
-l application=tatara-operator apply` renders them fresh from sops. Brief window
where the secrets are absent; acceptable (no Task/ingest in flight during the
cutover).

## Reference model (no Go change)

The operator already resolves secrets by path: `Project.spec.scmSecretRef` names
the SCM secret per Project; `anthropicSecretName` / `cliOidcSecretName` /
`openaiSecretName` config name the others. The chart now *creates* those secrets;
the existing references resolve them. No operator code change.

## Non-goals

- Multi-project SCM secrets. `tatara-scm` is per-Project; rendering N of them
  would require a projects map (a list in `values.yaml`), which hard rule 6
  forbids. One `tatara-scm` is rendered for the single dogfood Project; multi-
  project is deferred and noted in MEMORY/ROADMAP.
- external-secrets / reflector. The requirement is sops-in-helmfile.
- Rotating the actual secret values (separate operational task); this change only
  moves where they live.

## Validation

- `helm template` of the chart with all six values set renders four Secrets with
  the correct names and data keys; with values empty, renders none of them.
- `helmfile -e default -l application=tatara-operator diff` after the migration
  shows the four Secrets created and no spurious Deployment churn.
- Post-apply: `kubectl -n tatara get secret tatara-anthropic tatara-cli-oidc
  lightrag-openai tatara-scm` all present with the expected data keys; operator
  pod healthy; a subsequent Project/Task resolves them.
