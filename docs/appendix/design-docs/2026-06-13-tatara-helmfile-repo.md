# tatara-helmfile Repo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Stand up a standalone private `tatara-helmfile` repo that owns the two tatara helm releases, deploys them to the cluster via GitHub Actions on an in-cluster ARC runner with an in-cluster ServiceAccount, and self-enrolls as a `Repository` in the `tatara` Project.

**Architecture:** Single-bucket helmfile flattened out of `infra/helmfile/helmfiles/tatara/` into a flat repo root (`helmfile.yaml.gotmpl`, `.hook.sh`, `values/<release>/...`). One `default` environment, helmDefaults `wait`/`timeout 900`/`force`/`--rollback-on-failure`, values+secrets layering preserved. Two static GH Actions workflows: `diff.yaml` (PR -> sticky `helmfile diff`) and `apply.yaml` (push main -> `helmfile apply`), both on `runs-on: arc-runner-tatara-helmfile` using the pod's in-cluster SA token (no KUBECONFIG), mise-pinned tools, GPG import from secret, Harbor OCI login. Enrollment is two raw presync manifests applied by `.hook.sh`: the existing `tatara.dev` Project CR plus a new `Repository` self-enroll CR.

**Tech Stack:** helmfile 1.5.3, helm 4.2.0, kubectl 1.36.1, sops 3.13.1, helm-secrets 4.7.4, helm-diff; bash hook; sops PGP (key `D39E46932A270AA3BA490B9DB9FE928D3E8BCED8`); GitHub Actions on ARC self-hosted in-cluster runner; OCI charts on `harbor.szymonrichert.pl`.

---

## Scope boundary (read first)

This plan covers ONLY the contents of the new `tatara-helmfile` repo
(spec Sub-system A, Sub-system B repo-side workflows, Sub-system C
enrollment CR). It explicitly does NOT cover:

- The in-cluster ARC runner scale set, the `tatara-helmfile-deployer`
  ServiceAccount, or its `cluster-admin` ClusterRoleBinding. Those are
  cluster bootstrap and live in `infra/helmfile/helmfiles/coding`
  (spec Sub-system B "Deploy runner" note: "infra-helmfile addition,
  NOT in tatara-helmfile"). A separate infra plan owns them. This repo's
  workflows merely set `runs-on: arc-runner-tatara-helmfile` and assume
  that SA is already bound.
- The `tatara-deploy-harness` skill + wrapper wiring (spec Sub-system D)
  - separate plan.
- Removal of the `helmfiles/tatara/` bucket from infra (spec Phase 2
  "infra-removal diff") - separate infra plan / MR.

Hard dependency to flag in the runbook: the diff/apply workflows are
RED until the infra plan ships `arc-runner-tatara-helmfile` + the
deployer SA. That is a documented human-gated cross-repo dependency,
not in scope here.

## Audit finding (orphan check, spec Sub-system C note)

The infra bucket's `values/tatara-operator/raw/` contains exactly ONE
manifest: `project-tatara.tatara-operator.pre.yaml` (the `tatara` Project
CR). There are NO `Repository` CRs and NO `*.pre.secrets.yaml` in the
bucket today. Extracting the bucket therefore orphans nothing - component
`Repository` CRs are not bucket-managed. The canonical `Repository` shape
is in `tatara-operator/deploy-samples/tatara-project.yaml` (one Project +
six Repository docs). This plan adds ONE new self-enroll `Repository` CR
for `tatara-helmfile` as a new raw manifest. (Optional, called out in the
relevant task: also carry the six component `Repository` CRs into the same
raw manifest so the operator's `projectReposForScan` discovery is fully
declarative from this repo. Decision: carry them - they belong with the
Project CR and were only ever applied ad-hoc from deploy-samples.)

## File Structure

All paths relative to the new repo root `~/Documents/tatara-helmfile/`.

Created:
- `helmfile.yaml.gotmpl` - root: single `default` env, helmDefaults, templates (values+secrets layering), two releases (`tatara-chat` 0.1.0, `tatara-operator` 0.0.0-7d45bd9).
- `.hook.sh` - ported tatara subset of the infra hook; paths fixed for flat layout (hook at repo root, values at `values/<release>/raw/`).
- `.sops.yaml` - same PGP key as infra.
- `.mise.toml` - helm 4.2.0 / helmfile 1.5.3 / kubectl 1.36.1 / sops 3.13.1 + helm-secrets 4.7.4 + helm-diff postinstall hook.
- `.gitignore` - flat-layout subset of infra `.gitignore`.
- `.pre-commit-config.yaml` - mirror of infra (yamllint exclude path fixed for flat layout).
- `.gitleaks.toml` - mirror of infra allowlist.
- `values/common.yaml` - `imagePullSecrets: [{name: regcred}]` (verbatim).
- `values/default.yaml` - empty env-level scalar map (`{}` placeholder, missingFileHandler covers absence; created so the layering path always resolves).
- `values/tatara-operator/common.yaml` - `image.tag: "7d45bd9"` (verbatim).
- `values/tatara-operator/default.yaml` - ingress/webhook/OIDC/memory-image values (verbatim).
- `values/tatara-operator/default.secrets.yaml` - sops file copied byte-for-byte (NOT re-encrypted).
- `values/tatara-operator/raw/project-tatara.tatara-operator.pre.yaml` - Project CR (verbatim).
- `values/tatara-operator/raw/repositories-tatara.tatara-operator.pre.yaml` - NEW: self-enroll Repository CR for tatara-helmfile + the six component Repository CRs.
- `values/tatara-chat/default.yaml` - ingress host/path (verbatim).
- `.github/workflows/diff.yaml` - PR -> helmfile diff -> sticky comment, non-blocking.
- `.github/workflows/apply.yaml` - push main -> helmfile apply, concurrency-guarded.
- `README.md`, `MEMORY.md`, `ROADMAP.md`, `CLAUDE.md` - repo-local docs; CLAUDE.md per tatara contract.

---

## Task 1: Worktree-isolated working clone for the new repo

The repo does not exist on GitHub yet (created in human-gated Task 12).
Develop the contents in a fresh local directory; that directory becomes the
initial commit pushed in Task 12. There is no `main` to branch from yet, so
the "fresh main" rule applies at push time, not now.

Files:
- Create dir: `~/Documents/tatara-helmfile/`

Steps:
- [ ] 1.1 Create the working directory: `mkdir -p ~/Documents/tatara-helmfile && cd ~/Documents/tatara-helmfile && git init -b main`. Expected: `Initialized empty Git repository`.
- [ ] 1.2 Confirm the infra source bucket is readable for verbatim copies: `ls ~/Documents/infra/helmfile/helmfiles/tatara/`. Expected: `helmfile.yaml.gotmpl  values/`.
- [ ] 1.3 No commit yet (empty repo, no remote). Proceed to author files.

---

## Task 2: Root helmfile.yaml.gotmpl

The flat layout needs no `helmfiles:` index (single bucket). Merge the
infra ROOT helmDefaults (`force`, `wait`, `syncArgs: [--rollback-on-failure]`)
with the bucket helmDefaults (`wait`, `timeout: 900`) into one block, add the
single `environments.default` env, port the `templates.default` anchor and the
two releases verbatim. The hook command path changes from `../../.hook.sh`
(bucket two levels deep) to `./.hook.sh` (hook at repo root). The hook's first
arg `pwd` now resolves to the repo root, which the ported `.hook.sh` expects.

Files:
- Create: `~/Documents/tatara-helmfile/helmfile.yaml.gotmpl`

Steps:
- [ ] 2.1 Write `helmfile.yaml.gotmpl` with this exact content:

```yaml
environments:
  default: {}

helmDefaults:
  force: true
  wait: true
  # Generous wait: image pulls + ServiceMonitor/CRD settling can exceed helm's
  # default 5m. Helm v4 renamed --atomic to --rollback-on-failure.
  timeout: 900
  syncArgs:
    - --rollback-on-failure

templates:
  default: &default
    missingFileHandler: Warn
    hooks:
    - events:
      - prepare
      - presync
      - postsync
      showlogs: true
      command: ./.hook.sh
      args:
      - '{{`{{ exec "pwd" (list) }}`}}'
      - '{{`{{ .Event.Name }}`}}'
      - '{{`{{ .Release.Name }}`}}'
      - '{{`{{ .Release.Namespace }}`}}'
      - '{{`{{ .Release.Version }}`}}'
    values:
    - values/common.yaml
    - values/{{`{{ .Environment.Name }}`}}.yaml
    - values/{{`{{ .Release.Name }}`}}/common.yaml
    - values/{{`{{ .Release.Name }}`}}/{{`{{ .Environment.Name }}`}}.yaml
    secrets:
    - values/{{`{{ .Release.Name }}`}}/{{`{{ .Environment.Name }}`}}.secrets.yaml

releases:

- name: tatara-chat
  chart: oci://harbor.szymonrichert.pl/charts/tatara-chat
  namespace: tatara
  createNamespace: true
  version: 0.1.0
  labels:
    purpose: tatara
    application: tatara-chat
  <<: *default

- name: tatara-operator
  chart: oci://harbor.szymonrichert.pl/charts/tatara-operator
  namespace: tatara
  createNamespace: true
  version: 0.0.0-7d45bd9
  labels:
    purpose: tatara
    application: tatara-operator
  <<: *default
```

- [ ] 2.2 Sanity-grep the template-escaping survived: `grep -c '{{`' helmfile.yaml.gotmpl`. Expected: a non-zero count (the escaped gotmpl exec/Environment/Release refs).

---

## Task 3: Ported .hook.sh (flat-layout path fix)

Port the infra hook. Two path changes for the flat layout:
1. The release values dir was `${CURRENT_PWD}/helmfiles/${PHASE}/values/${RELEASE_NAME}`. In the flat repo the hook sits at the root and values are at `values/${RELEASE_NAME}`, so `RELEASE_DIR="${CURRENT_PWD}/values/${RELEASE_NAME}"` and the `PHASE` derivation (`basename "$HELMFILE_DIR"`) is dropped.
2. Everything else (health-check loop, raw apply globs, sops-decrypt globs, hooks dir, the global `raw/` block) is preserved verbatim. The repo has no root-level `raw/`, so the global block is a harmless no-op (`[[ -d ... ]]` guards it) - kept for parity, not removed (KISS: minimal diff from the proven hook).

Files:
- Create: `~/Documents/tatara-helmfile/.hook.sh`

Steps:
- [ ] 3.1 Write `.hook.sh` with this exact content:

```bash
#!/bin/bash
set -e
set -o nounset
set -o pipefail

readonly CURRENT_PWD="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

if [[ -z "${DEFAULT_TIMEOUT_S:-}" ]]; then
  readonly DEFAULT_TIMEOUT_S='600'
fi

fail() {
    local output="${1}"
    printf "%s\n" "${output}" >&2
    exit 1
}

readonly HELMFILE_DIR="${1:-}"
readonly EVENT_NAME="${2:-}"
readonly RELEASE_NAME="${3:-}"
readonly RELEASE_NAMESPACE="${4:-}"
readonly RELEASE_VERSION="${5:-}"
readonly TIMEOUT_S="${6:-$DEFAULT_TIMEOUT_S}"

[[ -z "${HELMFILE_DIR}" ]] && fail "Missing 1st parameter HELMFILE_DIR"
[[ -z "${EVENT_NAME}" ]] && fail "Missing 2nd parameter EVENT_NAME"

if [[ "${EVENT_NAME}" == "presync" ]]; then
    readonly KUBECTL="kubectl apply --wait=true"
    readonly SEARCH_SUFF="pre"
elif [[ "${EVENT_NAME}" == "prepare" ]]; then
    readonly KUBECTL="kubectl diff"
    readonly SEARCH_SUFF="pre"
elif [[ "${EVENT_NAME}" == "postsync" ]]; then
    readonly KUBECTL="kubectl apply --wait=true"
    readonly SEARCH_SUFF="post"
else
    fail "Not implemented event: '${EVENT_NAME}'"
fi
readonly TIMEOUT_TMSTP="$(($(date +%s) + TIMEOUT_S))"

if [[ ! -z "${RELEASE_NAME}" ]] && [[ ! -z "${RELEASE_NAMESPACE}" ]]; then

    if [[ "${EVENT_NAME}" == "presync" ]] && kubectl get namespace "${RELEASE_NAMESPACE}" > /dev/null 2>&1; then
        printf "Checking '%s' helm relase health in '%s' namespace\n" "${RELEASE_NAME}" "${RELEASE_NAMESPACE}"

        faulty_release_revision=""
        while true; do
            release_json="$(helm list --all-namespaces --output json | jq -r ".[] | select(.name==\"${RELEASE_NAME}\" and .namespace==\"${RELEASE_NAMESPACE}\")")"
            if [[ -z "${release_json}" ]]; then
                printf "Release ${RELEASE_NAMESPACE}/${RELEASE_NAME} is missing, it's a fresh start!\n"
                break
            fi
            release_state="$(echo "${release_json}" | jq -r ".status")"
            if [[ "${release_state}" == "deployed" ]]; then
                printf "Release ${RELEASE_NAMESPACE}/${RELEASE_NAME} ok!\n"
                break
            elif [[ "${release_state}" == "failed" ]]; then
                printf "Previous release ${RELEASE_NAMESPACE}/${RELEASE_NAME} was 'failed', deleting faulty release from helm history!\n"
                release_revision="$(echo "${release_json}" | jq -r ".revision")"
                kubectl delete secret -n "${RELEASE_NAMESPACE}" "sh.helm.release.v1.${RELEASE_NAME}.v${release_revision}"
                break
            elif [[ "$(date +%s)" -ge "${TIMEOUT_TMSTP}" ]]; then
                printf "Timeout waiting for release ${RELEASE_NAMESPACE}/${RELEASE_NAME} to stabilize reached, deleting faulty release from helm history!\n"
                release_revision="$(echo "${release_json}" | jq -r ".revision")"
                kubectl delete secret -n "${RELEASE_NAMESPACE}" "sh.helm.release.v1.${RELEASE_NAME}.v${release_revision}"
                break
            fi
            sleep 1
        done
    fi

    readonly RELEASE_DIR="${CURRENT_PWD}/values/${RELEASE_NAME}"
    if [[ -d "${RELEASE_DIR}/raw" ]]; then
        printf "Searching for files to apply for '%s' in '%s' namespace!\n" "${RELEASE_NAME}" "${RELEASE_NAMESPACE}"
        find "${RELEASE_DIR}/raw" -name "*.common.${SEARCH_SUFF}.yaml" -exec bash -xec "${KUBECTL} -n ${RELEASE_NAMESPACE} -f {}" \;
        find "${RELEASE_DIR}/raw" -name "*.common.${SEARCH_SUFF}.secrets.yaml" -exec bash -xec "sops -d {} | ${KUBECTL} -n ${RELEASE_NAMESPACE} -f -" \;
        find "${RELEASE_DIR}/raw" -name "*.${RELEASE_NAME}.${SEARCH_SUFF}.yaml" -exec bash -xec "${KUBECTL} -n ${RELEASE_NAMESPACE} -f {}" \;
        find "${RELEASE_DIR}/raw" -name "*.${RELEASE_NAME}.${SEARCH_SUFF}.secrets.yaml" -exec bash -xec "sops -d {} | ${KUBECTL} -n ${RELEASE_NAMESPACE} -f -" \;
    fi
    if [[ -d "${RELEASE_DIR}/hooks" ]]; then
        printf "Searching for hook scripts to run for '%s' in '%s'!\n" "${RELEASE_NAME}" "${RELEASE_NAMESPACE}"
        find "${RELEASE_DIR}/hooks" -name "*.common.${SEARCH_SUFF}.sh" -exec bash -xe "{}" "${RELEASE_NAME}" "${RELEASE_NAMESPACE}" "${RELEASE_VERSION}" \;
        find "${RELEASE_DIR}/hooks" -name "*.${RELEASE_NAME}.${SEARCH_SUFF}.sh" -exec bash -xe "{}" "${RELEASE_NAME}" "${RELEASE_NAMESPACE}" "${RELEASE_VERSION}" \;
    fi
fi
printf "Running hook globally during '%s'\n" "${EVENT_NAME}"
if [[ -d "${CURRENT_PWD}/raw" ]]; then
    printf "Searching for files to apply!\n"
    find "${CURRENT_PWD}/raw" -name "*.${SEARCH_SUFF}.yaml" -exec bash -xec "${KUBECTL} -n ${RELEASE_NAMESPACE} -f {}" \;
    find "${CURRENT_PWD}/raw" -name "*.${SEARCH_SUFF}.secrets.yaml" -exec bash -xec "sops -d {} | ${KUBECTL} -n ${RELEASE_NAMESPACE} -f -" \;
fi
```

- [ ] 3.2 Make it executable: `chmod +x ~/Documents/tatara-helmfile/.hook.sh`. Verify: `test -x ~/Documents/tatara-helmfile/.hook.sh && echo OK`. Expected: `OK`.
- [ ] 3.3 Shellcheck (boy-scout, the infra original had no `PHASE` users left after removal): `shellcheck ~/Documents/tatara-helmfile/.hook.sh || true`. Expected: no errors about an unused `PHASE` (it was removed). Warnings about `find -exec bash -c {}` matching infra original are acceptable; do not "fix" them - keep parity with the proven hook.

---

## Task 4: Tool + lint config files (.sops.yaml, .mise.toml, .pre-commit-config.yaml, .gitleaks.toml, .gitignore)

Mirror infra exactly, dropping infra-only tools the standalone repo never
uses (terraform, kustomize, kubectx, stern) and fixing the flat-layout
yamllint exclude path. The sops PGP key is identical so the copied
`default.secrets.yaml` decrypts without re-encryption.

Files:
- Create: `~/Documents/tatara-helmfile/.sops.yaml`
- Create: `~/Documents/tatara-helmfile/.mise.toml`
- Create: `~/Documents/tatara-helmfile/.pre-commit-config.yaml`
- Create: `~/Documents/tatara-helmfile/.gitleaks.toml`
- Create: `~/Documents/tatara-helmfile/.gitignore`

Steps:
- [ ] 4.1 Write `.sops.yaml` (verbatim from infra):

```yaml
creation_rules:
  - path_regex: default.secrets.yaml
    pgp: D39E46932A270AA3BA490B9DB9FE928D3E8BCED8
  - path_regex: kubeconfig
    pgp: D39E46932A270AA3BA490B9DB9FE928D3E8BCED8
  - path_regex: .*.secret.*.yaml
    pgp: D39E46932A270AA3BA490B9DB9FE928D3E8BCED8
```

- [ ] 4.2 Write `.mise.toml` (helm/helmfile/kubectl/sops pinned; helm-secrets 4.7.4 + helm-diff postinstall; pre-commit kept for local hook install; infra-only tools removed):

```toml
[tools]
helm = '4.2.0'
helmfile = '1.5.3'
kubectl = '1.36.1'
sops = '3.13.1'
pre-commit = '4.6.0'

[tool_alias]
kubectl = "aqua:kubernetes/kubernetes/kubectl"

[hooks]
postinstall = [
    "helm plugin install https://github.com/jkroepke/helm-secrets/releases/download/v4.7.4/secrets-4.7.4.tgz --verify=false 2> /dev/null || true",
    "helm plugin install https://github.com/jkroepke/helm-secrets/releases/download/v4.7.4/secrets-getter-4.7.4.tgz --verify=false 2> /dev/null || true",
    "helm plugin install https://github.com/jkroepke/helm-secrets/releases/download/v4.7.4/secrets-post-renderer-4.7.4.tgz --verify=false 2> /dev/null || true",
    "helm plugin install https://github.com/databus23/helm-diff --verify=false 2> /dev/null || true",
    "pre-commit install || true",
]
```

- [ ] 4.3 Write `.pre-commit-config.yaml`. Diff from infra: the yamllint `exclude` no longer needs `^helmfiles/` (flat repo has none) but DOES need to exempt the sops raw secrets path under the new layout and the gotmpl helmfile (helmfile templates are not valid plain YAML). Use:

```yaml
# See https://pre-commit.com for more information
# See https://pre-commit.com/hooks.html for more hooks
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v3.2.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
        args:
          - --allow-multiple-documents
        exclude: '\.gotmpl$|\.secrets\.yaml$'
      - id: check-added-large-files
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.18.1
    hooks:
      - id: gitleaks
  - repo: https://github.com/adrienverge/yamllint
    rev: v1.35.1
    hooks:
      - id: yamllint
        args: [-d, '{extends: default, rules: {line-length: {max: 320}, document-start: disable}}']
        exclude: '\.gotmpl$|\.secrets\.yaml$'
  - repo: https://github.com/compilerla/conventional-pre-commit
    rev: v3.0.0
    hooks:
      - id: conventional-pre-commit
        stages: [commit-msg]
        args: []
```

- [ ] 4.4 Write `.gitleaks.toml` (verbatim from infra - allowlist patterns are path-relative and already match `*.secrets.yaml` / `*.secret.*.yaml`):

```toml
# Allowlist for SOPS-encrypted or known-safe paths.
# See https://github.com/gitleaks/gitleaks#allowlist
title = "tatara-helmfile gitleaks config"

[allowlist]
  description = "SOPS-encrypted secret files"
  paths = [
    '''raw/.*\.secrets\.yaml''',
    '''.*\.secret\..*\.yaml''',
    '''default\.secrets\.yaml''',
    '''kubeconfig'''
  ]
```

- [ ] 4.5 Write `.gitignore` (flat-layout subset; drops infra-only terraform/ansible/gradle entries, keeps the generic + claude/superpowers/env ignores):

```gitignore
.tmp
__pycache__
*.idea
*.log
.DS_Store
*.gz
venv
.pytest*
.worktrees/
.claude/
.helmfile
docs/superpowers/
.env
```

- [ ] 4.6 Verify mise resolves the tools without installing: `cd ~/Documents/tatara-helmfile && mise ls --missing 2>&1 | head`. Expected: lists helm/helmfile/kubectl/sops/pre-commit as available or installable, no parse error. (Actual install happens in Task 11 verification.)

---

## Task 5: Release values tree (common + tatara-operator + tatara-chat)

Copy the values verbatim from the infra bucket. The sops file is copied
byte-for-byte (NOT decrypted, NOT re-encrypted - same PGP key, spec
Security). The env-level `values/default.yaml` does not exist in infra
(missingFileHandler is `Warn`); create an explicit empty one so the layering
path always resolves cleanly and `template`/`diff` emit no per-release warning.

Files:
- Create: `~/Documents/tatara-helmfile/values/common.yaml`
- Create: `~/Documents/tatara-helmfile/values/default.yaml`
- Create: `~/Documents/tatara-helmfile/values/tatara-operator/common.yaml`
- Create: `~/Documents/tatara-helmfile/values/tatara-operator/default.yaml`
- Copy:   `~/Documents/tatara-helmfile/values/tatara-operator/default.secrets.yaml`
- Create: `~/Documents/tatara-helmfile/values/tatara-chat/default.yaml`

Steps:
- [ ] 5.1 Write `values/common.yaml`:

```yaml
imagePullSecrets:
  - name: regcred
```

- [ ] 5.2 Write `values/default.yaml`:

```yaml
{}
```

- [ ] 5.3 Write `values/tatara-operator/common.yaml`:

```yaml
# Cluster-specific values the chart does not bake (rule 14). The operator chart
# has no subcharts, so the only passthrough is the image tag pin. The regcred
# pull secret reaches the manager Deployment from the bucket values/common.yaml
# (imagePullSecrets), same as the other tatara releases.
image:
  tag: "7d45bd9"
```

- [ ] 5.4 Write `values/tatara-operator/default.yaml` (verbatim from infra):

```yaml
ingress:
  enabled: true
  host: tatara.szymonrichert.pl
  # path "/" lets the operator own /operator/webhooks and the REST routes
  # internally (no rewrite double-strip). Confirm against
  # internal/webhook/server.go route prefix before apply; adjust path and
  # externalWebhookBase together if the operator registers routes under a
  # sub-prefix.
  path: /
  className: nginx

# External base used to render Project.status.webhookURL. Must match the public
# host + the operator's internal webhook route prefix.
externalWebhookBase: "https://tatara.szymonrichert.pl/operator/webhooks"

# Callback URL: the internal Service the wrapper POST-s turn results to.
# Must match the tatara-operator-internal Service DNS + INTERNAL_ADDR port.
callbackUrl: "http://tatara-operator-internal.tatara.svc:8082"

# Per-Project memory stack images (the operator stamps these into the native
# objects it provisions per Project). memoryBaseUrl removed in N4: each Project
# now exposes its own endpoint via status.memory.endpoint.
memoryImage: "harbor.szymonrichert.pl/containers/tatara-memory:0.3.0"
lightragImage: "harbor.szymonrichert.pl/proxy-ghcr/hkuds/lightrag@sha256:67ccf8d9f74eb29da872bf8b3e6513605f5ac601fb0509c5b0ca16d98d2d307d"
neo4jImage: "neo4j:2026.04.0"
# Shared OpenAI Secret (ns tatara, key LLM_BINDING_API_KEY) every per-Project
# lightrag reads. Created out-of-band (gated step in N4 runbook).
openaiSecretName: "lightrag-openai"

# In-cluster service endpoints.
oidcIssuer: "https://auth.szymonrichert.pl/realms/master"
oidcAudience: "tatara-operator"
operatorOidcClientId: "tatara-operator"

# Secret names the operator references (cluster-managed, not chart-rendered).
anthropicSecretName: "tatara-anthropic"
cliOidcSecretName: "tatara-cli-oidc"
scmSecretName: "tatara-scm"

# imagePullSecret: cluster-specific pull secret for operator-spawned workloads
# (neo4j, lightrag, tatara-memory, cnpg Cluster). Harbor's proxy-dockerhub
# requires authentication; regcred is the standard pull secret in this cluster.
imagePullSecret: "regcred"

# Ingester image the ingest Job runs (must be published to harbor first).
ingesterImage: "harbor.szymonrichert.pl/containers/tatara-memory-repo-ingester:0.2.9"
```

- [ ] 5.5 Copy the sops secrets file byte-for-byte (NOT via sops, NOT decrypted):
`mkdir -p ~/Documents/tatara-helmfile/values/tatara-operator && cp ~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/default.secrets.yaml ~/Documents/tatara-helmfile/values/tatara-operator/default.secrets.yaml`. Verify it is still a sops file and unchanged: `grep -q 'sops:' ~/Documents/tatara-helmfile/values/tatara-operator/default.secrets.yaml && diff -q ~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/default.secrets.yaml ~/Documents/tatara-helmfile/values/tatara-operator/default.secrets.yaml && echo COPIED-VERBATIM`. Expected: `COPIED-VERBATIM`. (The 7 sops fields and the `D39E...CED8` recipient are unchanged; do NOT print or decrypt the contents.)
- [ ] 5.6 Write `values/tatara-chat/default.yaml` (verbatim from infra):

```yaml
ingress:
  enabled: true
  host: tatara.szymonrichert.pl
  path: /api/v1/chat
```

- [ ] 5.7 Verify the sops copy decrypts with the local key (proves the verbatim copy is intact under the same recipient; redact output): `cd ~/Documents/tatara-helmfile && sops -d values/tatara-operator/default.secrets.yaml >/dev/null && echo DECRYPT-OK`. Expected: `DECRYPT-OK`. If this fails because the PGP private key is not present on this machine, note it and skip - CI will decrypt with `GPG_PRIVATE_RSA_B64`. Do NOT echo decrypted values.

---

## Task 6: Raw enrollment manifests (Project CR + NEW Repository self-enroll CR)

Carry the existing Project CR verbatim, and author a new raw manifest holding
the `tatara-helmfile` self-enroll `Repository` CR plus the six component
`Repository` CRs (so operator discovery is fully declarative from this repo -
see Audit finding). Filenames MUST end `.tatara-operator.pre.yaml` so the
ported hook's `*.${RELEASE_NAME}.${SEARCH_SUFF}.yaml` glob picks them up on
the tatara-operator release's presync.

The Repository CR fields come from `repository_types.go`: `projectRef`
(required), `url` (required), `defaultBranch` (default "main"),
`ingestEnabled` (default true), `semanticIngest` (default true),
`reingestSchedule` (required, 5-field cron). Spec Sub-system C pins the
self-enroll values: `reingestSchedule: "0 6 * * *"`, `ingestEnabled: true`.
The six component CRs mirror `deploy-samples/tatara-project.yaml` exactly.

Files:
- Create: `~/Documents/tatara-helmfile/values/tatara-operator/raw/project-tatara.tatara-operator.pre.yaml`
- Create: `~/Documents/tatara-helmfile/values/tatara-operator/raw/repositories-tatara.tatara-operator.pre.yaml`

Steps:
- [ ] 6.1 Copy the Project CR verbatim: `mkdir -p ~/Documents/tatara-helmfile/values/tatara-operator/raw && cp ~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/raw/project-tatara.tatara-operator.pre.yaml ~/Documents/tatara-helmfile/values/tatara-operator/raw/`. Verify: `diff -q ~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/raw/project-tatara.tatara-operator.pre.yaml ~/Documents/tatara-helmfile/values/tatara-operator/raw/project-tatara.tatara-operator.pre.yaml && echo PROJECT-COPIED`. Expected: `PROJECT-COPIED`.
- [ ] 6.2 Write `values/tatara-operator/raw/repositories-tatara.tatara-operator.pre.yaml` with the NEW self-enroll CR first, then the six component CRs (multi-doc YAML):

```yaml
# Repository CRs enrolled in the tatara Project. The operator's
# projectReposForScan discovers these on the next cron and begins
# issue/MR scans + scheduled re-ingest. Applied by .hook.sh presync of
# the tatara-operator release (filename matches *.tatara-operator.pre.yaml).
#
# tatara-helmfile self-enrolls here (spec Sub-system C). The remaining six
# are the platform component repos, carried verbatim from
# tatara-operator/deploy-samples/tatara-project.yaml so enrollment is fully
# declarative from this repo.
apiVersion: tatara.dev/v1alpha1
kind: Repository
metadata:
  name: tatara-helmfile
  namespace: tatara
spec:
  projectRef: tatara
  url: "https://github.com/szymonrychu/tatara-helmfile"
  defaultBranch: main
  ingestEnabled: true
  semanticIngest: true
  reingestSchedule: "0 6 * * *"
---
apiVersion: tatara.dev/v1alpha1
kind: Repository
metadata:
  name: tatara-memory
  namespace: tatara
spec:
  projectRef: tatara
  url: "https://github.com/szymonrychu/tatara-memory"
  defaultBranch: main
  semanticIngest: true
  reingestSchedule: "0 6 * * *"
---
apiVersion: tatara.dev/v1alpha1
kind: Repository
metadata:
  name: tatara-cli
  namespace: tatara
spec:
  projectRef: tatara
  url: "https://github.com/szymonrychu/tatara-cli"
  defaultBranch: main
  semanticIngest: true
  reingestSchedule: "0 6 * * *"
---
apiVersion: tatara.dev/v1alpha1
kind: Repository
metadata:
  name: tatara-operator
  namespace: tatara
spec:
  projectRef: tatara
  url: "https://github.com/szymonrychu/tatara-operator"
  defaultBranch: main
  semanticIngest: true
  reingestSchedule: "0 6 * * *"
---
apiVersion: tatara.dev/v1alpha1
kind: Repository
metadata:
  name: tatara-chat
  namespace: tatara
spec:
  projectRef: tatara
  url: "https://github.com/szymonrychu/tatara-chat"
  defaultBranch: main
  semanticIngest: true
  reingestSchedule: "0 6 * * *"
---
apiVersion: tatara.dev/v1alpha1
kind: Repository
metadata:
  name: tatara-memory-repo-ingester
  namespace: tatara
spec:
  projectRef: tatara
  url: "https://github.com/szymonrychu/tatara-memory-repo-ingester"
  defaultBranch: main
  semanticIngest: true
  reingestSchedule: "0 6 * * *"
---
apiVersion: tatara.dev/v1alpha1
kind: Repository
metadata:
  name: tatara-claude-code-wrapper
  namespace: tatara
spec:
  projectRef: tatara
  url: "https://github.com/szymonrychu/tatara-claude-code-wrapper"
  defaultBranch: main
  semanticIngest: true
  reingestSchedule: "0 6 * * *"
```

- [ ] 6.3 Validate both raw manifests are well-formed multi-doc YAML and carry the expected kinds: `cd ~/Documents/tatara-helmfile && for f in values/tatara-operator/raw/*.pre.yaml; do python3 -c "import sys,yaml; list(yaml.safe_load_all(open('$f')))" && echo "$f OK"; done`. Expected: both print `... OK`.
- [ ] 6.4 Confirm the self-enroll CR cron matches the CRD pattern (`^(\S+\s+){4}\S+$`, MinLength 9): `echo "0 6 * * *" | grep -Eq '^(\S+\s+){4}\S+$' && echo CRON-OK`. Expected: `CRON-OK`.

---

## Task 7: diff.yaml workflow (PR -> helmfile diff -> sticky comment, non-blocking)

Adapt the legacy `diffs.yaml` pattern to: self-hosted ARC runner, in-cluster
SA (no KUBECONFIG / no `mkdir ~/.kube`), mise-pinned tools, GPG import from
`secrets.GPG_PRIVATE_RSA_B64`, Harbor OCI login via the exact pattern used in
the component CI (`helm registry login harbor.szymonrichert.pl -u "$HARBOR_USERNAME" -p "$HARBOR_PASSWORD"`).
ANSI strip + sticky comment via `marocchino/sticky-pull-request-comment@v2`.
Diff is informational: the helmfile diff step uses `continue-on-error: true`
and `set +e` so a non-zero diff exit never fails the PR (spec B.7). The ARC
runner image is Ubuntu (same family as the existing `tatara-operator` runner:
`apt-get` available, `actions/checkout@v4`); install mise via the official
installer then `mise install`.

Files:
- Create: `~/Documents/tatara-helmfile/.github/workflows/diff.yaml`

Steps:
- [ ] 7.1 Write `.github/workflows/diff.yaml`:

```yaml
name: diff

on:
  pull_request:
    branches: [main]

jobs:
  helmfile-diff:
    runs-on: arc-runner-tatara-helmfile
    env:
      GPG_PRIVATE_RSA_B64: ${{ secrets.GPG_PRIVATE_RSA_B64 }}
      HARBOR_USERNAME: ${{ secrets.HARBOR_USERNAME }}
      HARBOR_PASSWORD: ${{ secrets.HARBOR_PASSWORD }}
    steps:
      - uses: actions/checkout@v4

      - name: install mise
        run: |
          curl -fsSL https://mise.run | sh
          echo "$HOME/.local/bin" >> "$GITHUB_PATH"

      - name: mise install (helm/helmfile/kubectl/sops + plugins)
        run: |
          eval "$($HOME/.local/bin/mise activate bash)"
          mise install
          mise reshim

      - name: import GPG key (sops decrypt)
        run: |
          echo "$GPG_PRIVATE_RSA_B64" | base64 -d | gpg --batch --import

      - name: harbor OCI login
        run: |
          eval "$($HOME/.local/bin/mise activate bash)"
          helm registry login harbor.szymonrichert.pl -u "$HARBOR_USERNAME" -p "$HARBOR_PASSWORD"

      - name: helmfile diff
        id: diff
        continue-on-error: true
        run: |
          eval "$($HOME/.local/bin/mise activate bash)"
          set +e
          helmfile -e default diff --detailed-exitcode 2>&1 | tee diff.log
          set -e
          {
            echo 'output<<DIFF_EOF'
            sed 's/\x1b\[[0-9;]*m//g' diff.log
            echo 'DIFF_EOF'
          } >> "$GITHUB_OUTPUT"

      - uses: marocchino/sticky-pull-request-comment@v2
        with:
          header: tatara-helmfile-diff
          recreate: true
          message: |
            ## helmfile diff (`-e default`)

            ```diff
            ${{ steps.diff.outputs.output }}
            ```
```

- [ ] 7.2 Validate the workflow YAML parses: `python3 -c "import yaml; yaml.safe_load(open('$HOME/Documents/tatara-helmfile/.github/workflows/diff.yaml'))" && echo DIFF-YAML-OK`. Expected: `DIFF-YAML-OK`.
- [ ] 7.3 Confirm no `KUBECONFIG`/`KUBE_CONFIG` leaked in from the legacy pattern: `grep -ic 'kube_config\|kubeconfig\|aws_' ~/Documents/tatara-helmfile/.github/workflows/diff.yaml`. Expected: `0` (in-cluster SA only).

---

## Task 8: apply.yaml workflow (push main -> helmfile apply, concurrency-guarded)

Same setup block as diff. `helmfile -e default apply` (in-cluster SA;
`--rollback-on-failure` comes from helmDefaults `syncArgs`, no extra flag).
GH-native `concurrency` replaces the legacy Turnstyle action, serializing
applies with `cancel-in-progress: false` (spec B.6). On apply failure the job
goes red and the harness reacts (rollback - spec S8); no Slack notify ported.

Files:
- Create: `~/Documents/tatara-helmfile/.github/workflows/apply.yaml`

Steps:
- [ ] 8.1 Write `.github/workflows/apply.yaml`:

```yaml
name: apply

on:
  push:
    branches: [main]

concurrency:
  group: tatara-helmfile-apply
  cancel-in-progress: false

jobs:
  helmfile-apply:
    runs-on: arc-runner-tatara-helmfile
    env:
      GPG_PRIVATE_RSA_B64: ${{ secrets.GPG_PRIVATE_RSA_B64 }}
      HARBOR_USERNAME: ${{ secrets.HARBOR_USERNAME }}
      HARBOR_PASSWORD: ${{ secrets.HARBOR_PASSWORD }}
    steps:
      - uses: actions/checkout@v4

      - name: install mise
        run: |
          curl -fsSL https://mise.run | sh
          echo "$HOME/.local/bin" >> "$GITHUB_PATH"

      - name: mise install (helm/helmfile/kubectl/sops + plugins)
        run: |
          eval "$($HOME/.local/bin/mise activate bash)"
          mise install
          mise reshim

      - name: import GPG key (sops decrypt)
        run: |
          echo "$GPG_PRIVATE_RSA_B64" | base64 -d | gpg --batch --import

      - name: harbor OCI login
        run: |
          eval "$($HOME/.local/bin/mise activate bash)"
          helm registry login harbor.szymonrichert.pl -u "$HARBOR_USERNAME" -p "$HARBOR_PASSWORD"

      - name: helmfile apply
        run: |
          eval "$($HOME/.local/bin/mise activate bash)"
          helmfile -e default apply
```

- [ ] 8.2 Validate the workflow YAML parses: `python3 -c "import yaml; yaml.safe_load(open('$HOME/Documents/tatara-helmfile/.github/workflows/apply.yaml'))" && echo APPLY-YAML-OK`. Expected: `APPLY-YAML-OK`.
- [ ] 8.3 Confirm concurrency + runner + no KUBECONFIG: `grep -c 'cancel-in-progress: false\|arc-runner-tatara-helmfile' ~/Documents/tatara-helmfile/.github/workflows/apply.yaml`. Expected: `2`. And `grep -ic 'kube_config\|kubeconfig\|--atomic' ~/Documents/tatara-helmfile/.github/workflows/apply.yaml`. Expected: `0` (no atomic flag - rollback is via helmDefaults syncArgs).

---

## Task 9: Repo docs (CLAUDE.md, README.md, MEMORY.md, ROADMAP.md)

CLAUDE.md follows the tatara per-repo contract: copy the hard-rules block
from `~/Documents/tatara/CLAUDE.md` and adjust the "What this repo is"
preamble to describe tatara-helmfile (deploy bucket, not a code component).
README documents layout + the deploy flow. MEMORY records the reversal of
[[tatara-helmfile-into-infra]], the verbatim-sops decision, and the
cluster-admin SA risk. ROADMAP lists the cross-repo follow-ups (infra runner,
infra bucket removal, harness skill).

Files:
- Create: `~/Documents/tatara-helmfile/CLAUDE.md`
- Create: `~/Documents/tatara-helmfile/README.md`
- Create: `~/Documents/tatara-helmfile/MEMORY.md`
- Create: `~/Documents/tatara-helmfile/ROADMAP.md`

Steps:
- [ ] 9.1 Write `CLAUDE.md`. Use the canonical hard-rules block (copied below verbatim from the tatara contract; the only adaptation is the "What this repo is" section). Write this exact content:

```markdown
# CLAUDE.md - tatara-helmfile

This repo deploys the tatara platform's helm releases to the cluster. It
is platform infra, not a tatara code component, but it follows the tatara
per-repo contract.

## What this repo is

`tatara-helmfile` is the standalone helmfile bucket that owns the tatara
platform's helm releases (`tatara-chat`, `tatara-operator`) plus the
enrollment CRs (the `tatara.dev` Project and the per-repo Repository CRs).
It deploys via GitHub Actions on an in-cluster ARC runner
(`arc-runner-tatara-helmfile`) using the pod's in-cluster ServiceAccount
token - no KUBECONFIG. A PR posts a sticky `helmfile diff`; merge to `main`
auto-applies. This intentionally reverses the 2026-06-05 consolidation into
the infra helmfile (see MEMORY.md) to scope bot deploy access to one repo.

## What this repo is NOT

- Not a code component. No Go, no Dockerfile, no chart sources. Charts are
  pulled from `oci://harbor.szymonrichert.pl/charts` by version.
- Not the cluster bootstrap. The ARC runner scale set, the
  `tatara-helmfile-deployer` ServiceAccount, and its cluster-admin
  ClusterRoleBinding live in `infra/helmfile/helmfiles/coding`, not here.

## Hard rules

1. **Newest stable Go** for any Go service. Pin the Go directive to the
   exact minor in `go.mod`. (N/A here - no Go.)
2. **KISS, always.** Prefer simplicity over cleverness. Three similar
   lines is better than a premature abstraction.
3. **Boy-scout rule on adjacent issues.** If you see something easy to
   fix alongside current work, fix it. Do not ask.
4. **NEVER introduce tech-debt.** If a thing is complex, call it out in
   `MEMORY.md` with the rationale. Never defer cleanup to "later".
5. **Charts created via `helm create <name>`** then edited. Never
   hand-rolled. (N/A here - charts are pulled by version.)
6. **No plain ENVs in values.yaml. No lists in values.yaml.** All inputs
   map: camelCase scalar in `values.yaml` -> kebab-case key in
   ConfigMap/Secret -> workload consumes via `envFrom`. Genuinely
   list-shaped data is rendered into a templated ConfigMap and read at
   runtime.
7. **Sonnet for implementation. Opus for merges.** Implementation
   subagents are sonnet; the merge subagent is opus. Plan and review run
   in opus.
8. **EVERYTHING through superpowers.** brainstorming, writing-plans,
   test-driven-development, systematic-debugging, requesting-code-review,
   verification-before-completion, subagent-driven-development,
   using-git-worktrees, finishing-a-development-branch are mandatory.
9. **Subagent-driven, parallel development** where tasks are independent.
   Dispatch in a single message for true parallelism.
10. **Branch flow:** worktree off `main` -> develop in worktree -> merge
    back to source repo `main` -> cleanup worktree -> deploy from `main`
    only. NEVER deploy from a worktree.
11. **JSON logs only.** (N/A - no service code.)
12. **Log every business action at INFO** with structured fields. (N/A.)
13. **Metrics for everything that counts, times out, or can fail.** (N/A.)
14. **Charts are cluster-agnostic.** A component's helm chart MUST assume
    nothing about the cluster. All cluster-specific customization comes
    from THIS repo's values tree (per-bucket `values/common.yaml` +
    per-release `values/<name>/{common,default}.yaml` + sops
    `default.secrets.yaml`).

## Writing rules

- No em dashes. No smart quotes. No arrows. No decorative Unicode. Plain
  hyphens and straight quotes.
- No preamble. No recap unless asked. One line at most.
- Show diffs, not whole files, for anything > 30 lines that exists.
- No docstrings/comments on code not being changed.

## What I want from a Claude session here

- Read `MEMORY.md` and `ROADMAP.md` before non-trivial work.
- Update `MEMORY.md` on any non-obvious decision or dead-end. One dated
  line per entry.
- Update `ROADMAP.md` on phase completion / re-scope.
- Use `/handoff` near context limits; do not soldier on.
- NEVER `docker buildx`/`helm push` charts or images locally; component
  CI builds + pushes to harbor on merge to main.
- The deploy runner SA is cluster-admin scoped - the single highest-risk
  element. Any code in `arc-runner-tatara-helmfile` can do anything to the
  cluster. Keep this repo bot-only-write and private.
```

- [ ] 9.2 Write `README.md`:

```markdown
# tatara-helmfile

Standalone helmfile bucket for the tatara platform. Owns two helm releases
and the operator enrollment CRs, deploys via GitHub Actions on an in-cluster
ARC runner.

## Layout

```
helmfile.yaml.gotmpl          # single 'default' env, helmDefaults, 2 releases
.hook.sh                      # presync: applies values/<release>/raw/*.pre.yaml,
                              #   sops-decrypts *.pre.secrets.yaml
values/
  common.yaml                 # imagePullSecrets: regcred (bucket-wide)
  default.yaml                # env-level (empty)
  tatara-operator/
    common.yaml               # image.tag pin
    default.yaml              # ingress/webhook/OIDC/memory-image values
    default.secrets.yaml      # sops (PGP D39E...CED8)
    raw/
      project-tatara.tatara-operator.pre.yaml        # tatara.dev Project CR
      repositories-tatara.tatara-operator.pre.yaml   # Repository CRs (incl. self-enroll)
  tatara-chat/
    default.yaml              # ingress host/path
.github/workflows/
  diff.yaml                   # PR -> helmfile diff -> sticky comment (non-blocking)
  apply.yaml                  # push main -> helmfile apply (concurrency-guarded)
```

## Releases

| release          | chart                                              | version       | ns     |
|------------------|----------------------------------------------------|---------------|--------|
| tatara-chat      | oci://harbor.szymonrichert.pl/charts/tatara-chat   | 0.1.0         | tatara |
| tatara-operator  | oci://harbor.szymonrichert.pl/charts/tatara-operator | 0.0.0-7d45bd9 | tatara |

## Deploy flow

1. Open a PR bumping a release (image tag in `values/tatara-operator/common.yaml`
   and/or chart `version:` in `helmfile.yaml.gotmpl`).
2. `diff.yaml` posts the rendered `helmfile diff` as a sticky PR comment.
3. Merge to `main`. `apply.yaml` runs `helmfile -e default apply` on the
   in-cluster runner. Failures roll back via helmDefaults `--rollback-on-failure`.

## Local use

```bash
mise install                                  # helm/helmfile/kubectl/sops + plugins
helm registry login harbor.szymonrichert.pl   # OCI chart pull
helmfile -e default diff                       # against current kube-context
```

## Auth

- Cluster: in-cluster ServiceAccount `tatara-helmfile-deployer` (no KUBECONFIG).
- Harbor: `HARBOR_USERNAME` / `HARBOR_PASSWORD` GH Actions secrets.
- sops: `GPG_PRIVATE_RSA_B64` GH Actions secret (base64 PGP private key).

The ARC runner scale set + SA + cluster-admin binding live in
`infra/helmfile/helmfiles/coding`, not here.
```

- [ ] 9.3 Write `MEMORY.md`:

```markdown
# MEMORY - tatara-helmfile

- 2026-06-13 Repo created by full-extract of `infra/helmfile/helmfiles/tatara/`.
  Intentionally REVERSES the 2026-06-05 consolidation (`tatara-helmfile-into-infra`).
  Rationale: scope bot deploy access to one dedicated repo, not the whole 60+
  release homelab infra repo.
- 2026-06-13 Flat layout: hook at repo root (`./.hook.sh`), values at
  `values/<release>/raw/`. The infra hook's `PHASE`/`helmfiles/<phase>/` path
  derivation was dropped; `RELEASE_DIR=${PWD}/values/<release>`.
- 2026-06-13 `default.secrets.yaml` copied byte-for-byte from infra (same PGP
  recipient D39E...CED8). NEVER re-encrypted or printed. Same key means no
  rotation; key rotation is out of scope (noted as risk).
- 2026-06-13 Deploy runner SA `tatara-helmfile-deployer` is cluster-admin
  scoped (the operator chart installs CRDs/ClusterRoles/webhooks). Single
  highest-risk element. Mitigations: dedicated SA, private bot-only-write repo,
  control-plane-pinned + maxRunners-capped runner that only runs this repo's
  workflows. The runner + SA + binding live in infra/helmfile/helmfiles/coding.
- 2026-06-13 Repository CRs (self-enroll + 6 components) carried here in
  `raw/repositories-tatara.tatara-operator.pre.yaml`. Infra bucket had ONLY the
  Project CR, no Repository CRs (audit confirmed) - extracting orphaned nothing.
  Component CRs were previously applied ad-hoc from operator deploy-samples; now
  declarative from this repo.
- 2026-06-13 mise dropped infra-only tools (terraform/kustomize/kubectx/stern);
  kept helm/helmfile/kubectl/sops + helm-secrets 4.7.4 + helm-diff.
```

- [ ] 9.4 Write `ROADMAP.md`:

```markdown
# ROADMAP - tatara-helmfile

## Cross-repo follow-ups (not in this repo)

- [ ] infra: add `arc-runner-tatara-helmfile` RunnerScaleSet + ServiceAccount
      `tatara-helmfile-deployer` + cluster-admin ClusterRoleBinding in
      `infra/helmfile/helmfiles/coding`. Workflows here are RED until this ships.
- [ ] infra: remove the `helmfiles/tatara/` bucket from `infra/helmfile`
      (this repo is now sole owner). Drop the bucket from the root
      `helmfile.yaml.gotmpl` helmfiles index.
- [ ] wrapper: ship the `tatara-deploy-harness` skill + add `tatara-helmfile`
      to the agent's TATARA_REPOS (spec Sub-system D).

## This repo

- [ ] First live `helmfile apply` from main (human-gated, after runner exists).
- [ ] Confirm `kubectl get project tatara` + `kubectl get repository -n tatara`
      shows the self-enroll + 6 component CRs after first apply.
- [ ] Consider sops PGP key rotation (currently shared with infra).
```

- [ ] 9.5 Verify all four docs exist and CLAUDE.md carries the 14 hard rules: `cd ~/Documents/tatara-helmfile && ls CLAUDE.md README.md MEMORY.md ROADMAP.md && grep -c '^[0-9]\+\. \*\*' CLAUDE.md`. Expected: the four filenames and `14`.

---

## Task 10: Initial commit (local; no remote yet)

Stage everything and make the first conventional commit on the local `main`.
The push to GitHub happens in human-gated Task 12.

Files:
- Modify: git index of `~/Documents/tatara-helmfile/`

Steps:
- [ ] 10.1 Stage and commit: `cd ~/Documents/tatara-helmfile && git add -A && git commit -m "feat: standalone tatara-helmfile deploy bucket + diff/apply workflows + self-enroll Repository CR"`. Expected: a commit listing all created files.
- [ ] 10.2 Confirm the sops file is committed as ciphertext (gitleaks allowlisted): `git show HEAD:values/tatara-operator/default.secrets.yaml | grep -q 'sops:' && echo SOPS-COMMITTED-ENCRYPTED`. Expected: `SOPS-COMMITTED-ENCRYPTED`.

---

## Task 11: Verification (lint, template, pre-commit, gitleaks)

Render both releases offline and run the lint suite. Harbor OCI login is
required to pull the charts for `template`/`lint`; if not authed locally,
note it and rely on CI for the render check (spec Testing).

Files:
- Test (commands only, no files created)

Steps:
- [ ] 11.1 Install tools: `cd ~/Documents/tatara-helmfile && mise install`. Expected: helm/helmfile/kubectl/sops installed + helm-secrets/helm-diff plugins installed via postinstall hook.
- [ ] 11.2 Harbor login (needed for OCI chart pull): `helm registry login harbor.szymonrichert.pl -u "$HARBOR_USERNAME" -p "$HARBOR_PASSWORD"`. If creds are not in the local env, set them or skip 11.3-11.4 and note "deferred to CI". Do NOT echo the password.
- [ ] 11.3 Lint: `cd ~/Documents/tatara-helmfile && helmfile -e default lint`. Expected: both `tatara-chat` and `tatara-operator` lint clean (or only chart-internal warnings, no helmfile-level error). If Harbor pull fails, record the error verbatim and defer to CI.
- [ ] 11.4 Template (offline render): `cd ~/Documents/tatara-helmfile && helmfile -e default template`. Expected: rendered manifests for both releases, no template error, the operator image tag `7d45bd9` and ingress host `tatara.szymonrichert.pl` present in the output. Verify: `helmfile -e default template 2>/dev/null | grep -c 'tatara.szymonrichert.pl'`. Expected: non-zero.
- [ ] 11.5 pre-commit: `cd ~/Documents/tatara-helmfile && pre-commit run --all-files`. Expected: all hooks pass (trailing-whitespace, end-of-file-fixer, check-yaml with the gotmpl/secrets excludes, gitleaks, yamllint, conventional-pre-commit). Fix any reported issue, then re-run until clean.
- [ ] 11.6 gitleaks standalone (matches the component CI scan): `cd ~/Documents/tatara-helmfile && gitleaks detect --source . --no-git --redact --verbose; echo "exit: $?"`. Expected: `no leaks found`, `exit: 0` (the sops file is allowlisted via `.gitleaks.toml`).
- [ ] 11.7 Workflow lint (if `actionlint` available, boy-scout): `actionlint ~/Documents/tatara-helmfile/.github/workflows/*.yaml 2>&1 || echo "actionlint unavailable - YAML parse already verified in Tasks 7-8"`. Expected: no errors, or the fallback note.
- [ ] 11.8 superpowers:verification-before-completion: collect evidence (lint output, the `template` grep count, pre-commit summary, gitleaks `exit: 0`) before claiming this repo is done. Do NOT claim done if 11.3/11.4 were deferred without the CI render having run.

---

## Task 12: HUMAN-GATED repo bootstrap + secrets (needs real key material)

> HUMAN-GATED. Requires the user (or a live action outside an autonomous
> session) because it needs real secret material: the GPG private key,
> Harbor creds, and GitHub admin rights to create a private repo + set
> secrets. Prepare the runbook; the agent does NOT run these without the
> user, and `gh secret set` values must come from the user, never from
> decrypting the sops file or echoing the PGP key in logs.

Files:
- Runbook (commands the user runs; no repo files)

Steps:
- [ ] 12.1 Create the private repo: `gh repo create szymonrychu/tatara-helmfile --private --description "Standalone helmfile deploy bucket for the tatara platform"`. Expected: repo created under szymonrychu.
- [ ] 12.2 Grant the bot write access (szymonrychu-bot, [[tatara-bot-identity-szymonrychu-bot]]): `gh api -X PUT repos/szymonrychu/tatara-helmfile/collaborators/szymonrychu-bot -f permission=push`. Expected: invitation/added. (Bot already holds the `tatara-scm` PAT; no new token needed.)
- [ ] 12.3 Push the local main: `cd ~/Documents/tatara-helmfile && git remote add origin https://github.com/szymonrychu/tatara-helmfile.git && git push -u origin main`. Expected: main pushed; this triggers no workflow (push, not PR; apply.yaml will run and stay RED until the infra runner exists - acceptable, documented).
- [ ] 12.4 Set the Harbor secrets (values from the user's Harbor robot account; the same creds the component CIs use): `gh secret set HARBOR_USERNAME --repo szymonrychu/tatara-helmfile` and `gh secret set HARBOR_PASSWORD --repo szymonrychu/tatara-helmfile`. Expected: both secrets set. Provide values via stdin/prompt, never on the command line.
- [ ] 12.5 Set the GPG secret (base64 of the SAME PGP private key that owns recipient `D39E46932A270AA3BA490B9DB9FE928D3E8BCED8`): `gpg --export-secret-keys D39E46932A270AA3BA490B9DB9FE928D3E8BCED8 | base64 | gh secret set GPG_PRIVATE_RSA_B64 --repo szymonrychu/tatara-helmfile`. Expected: secret set. Run this only on the user's machine that holds the private key; do NOT pipe the key through any logged channel.
- [ ] 12.6 Confirm secrets exist (names only, never values): `gh secret list --repo szymonrychu/tatara-helmfile`. Expected: `GPG_PRIVATE_RSA_B64`, `HARBOR_USERNAME`, `HARBOR_PASSWORD`.
- [ ] 12.7 Confirm NO `KUBE_CONFIG` secret is set (in-cluster SA only): the list from 12.6 must not contain any kube/aws secret.

---

## Cross-repo dependency reminder (runbook tail)

The diff/apply workflows are RED until a SEPARATE infra plan ships
`arc-runner-tatara-helmfile` + the `tatara-helmfile-deployer` cluster-admin
SA into `infra/helmfile/helmfiles/coding`, and a SEPARATE infra MR removes the
`helmfiles/tatara/` bucket. First live `helmfile apply` from this repo's main
is verified only after those land (spec Phase 4). Do not declare the deploy
harness end-to-end working until `kubectl get project tatara` and
`kubectl get repository -n tatara` confirm enrollment post-apply.
