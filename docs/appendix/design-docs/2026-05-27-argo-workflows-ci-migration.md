# Argo Workflows CI Migration - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `~/Documents/tatara/docs/superpowers/specs/2026-05-27-argo-workflows-ci-migration-design.md`

**Goal:** Migrate tatara-cli and tatara-memory CI/CD from github actions and local Makefile targets to argo workflows triggered via the existing argo-events lane, with per-step github commit status reporting.

**Architecture:** New repo `tatara-argo-workflows` ships a helm chart with 7 ClusterWorkflowTemplates (5 atomic + 1 composite + 1 helper) plus 3 sops-encrypted ns-scoped Secrets in `tatara` ns. Two infra-repo files declare tatara onboarding via the existing multi-tenant registry pattern. Per-step github status posted via curl from a helper CWT called as exit handler from every workflow.

**Tech Stack:** helm 3.x, argo-workflows 1.0.14 / v4.0.5, argo-events 2.4.21, sops + age, goreleaser v2, moby/buildkit:rootless, golangci-lint v2.11.4, go 1.25, helmfile v0.x.

---

## File structure

**New repo `github.com/szymonrychu/tatara-argo-workflows`:**

| Path | Responsibility |
|---|---|
| `CLAUDE.md` | Canonical project rules (copy of `~/Documents/tatara/CLAUDE.md`) |
| `README.md` | One-pager: what this repo is, link to ARCHITECTURE |
| `ARCHITECTURE.md` | How the CI flows; one diagram per flow |
| `MEMORY.md` | Append-only decisions/dead-ends log |
| `ROADMAP.md` | Component-local roadmap (v0.1.0 ship; v0.1.1 follow-ups) |
| `LICENSE` | MIT (match siblings) |
| `.gitignore` | Standard helm + sops ignores |
| `.sops.yaml` | sops creation rule, same pgp recipient as tatara-memory |
| `.pre-commit-config.yaml` | yamllint, helm-lint, gitleaks |
| `helmfile.yaml.gotmpl` | One release: `tatara-argo-workflows` referencing `./charts/tatara-argo-workflows` |
| `charts/tatara-argo-workflows/Chart.yaml` | Chart metadata, version 0.1.0 |
| `charts/tatara-argo-workflows/values.yaml` | Scalar defaults (namespace, harbor registry, github api base) |
| `charts/tatara-argo-workflows/templates/_helpers.tpl` | Standard helm helpers + tag-without-v helper |
| `charts/tatara-argo-workflows/templates/cwt-github-status.yaml` | Helper CWT, called from every other CWT |
| `charts/tatara-argo-workflows/templates/cwt-go-ci.yaml` | Atomic: lint + test |
| `charts/tatara-argo-workflows/templates/cwt-go-release.yaml` | Atomic: goreleaser |
| `charts/tatara-argo-workflows/templates/cwt-container-build.yaml` | Atomic: buildkit multi-tag push |
| `charts/tatara-argo-workflows/templates/cwt-helm-publish.yaml` | Atomic: helm package + OCI push |
| `charts/tatara-argo-workflows/templates/cwt-helmfile-deploy.yaml` | Atomic: helmfile sync |
| `charts/tatara-argo-workflows/templates/cwt-tatara-memory-tag.yaml` | Composite DAG: build+publish parallel, then deploy |
| `charts/tatara-argo-workflows/templates/secret-harbor-robot.yaml` | Namespaced Secret in `tatara` |
| `charts/tatara-argo-workflows/templates/secret-age-key.yaml` | Namespaced Secret in `tatara` |
| `charts/tatara-argo-workflows/templates/secret-github-status.yaml` | Namespaced Secret in `tatara` |
| `values/tatara-argo-workflows/common.yaml` | Empty stub (per-environment overrides go here) |
| `values/tatara-argo-workflows/default.yaml` | Empty stub |
| `values/tatara-argo-workflows/default.secrets.yaml` | sops-encrypted secrets |

**Modified in `~/Documents/infra/`:**

| Path | Change |
|---|---|
| `helmfile/helmfiles/coding/values/argo-workflows/common.yaml` | Add `tatara` to `controller.workflowNamespaces` |
| `helmfile/helmfiles/coding/values/argo-events/common.yaml` | Replace inline-extraObjects with `events.github.repos` registry |

**Modified in `~/Documents/tatara/tatara-cli/`:**

| Path | Change |
|---|---|
| `.goreleaser.yaml` | amd64-only (linux + darwin); drop `brews:` |
| `.github/workflows/` | DELETED |
| `MEMORY.md` | Wave-6 preconditions collapsed; CI now via argo |
| `ROADMAP.md` | v0.1.0 unblocked; tap repo line removed |

**Modified in `~/Documents/tatara/tatara-memory/`:**

| Path | Change |
|---|---|
| `Makefile` | Remove `push` and `chart-push` targets; add `ci` target |
| `MEMORY.md` | v1.1 GHA-CI line struck; argo-driven now |
| `ROADMAP.md` | Container/chart push moved to phase-5 |

**Modified in `~/Documents/tatara/`:**

| Path | Change |
|---|---|
| `MEMORY.md` | Cross-repo decision: phase 5 brought forward, agent's onboarding lane reused |
| `ROADMAP.md` | Phase 5 marked shipped |

---

## Wave 0 - Repo bootstrap

### Task 0.1: Create github repo + clone

**Files:** none locally yet

- [ ] **Step 1: Create github repo**

```bash
gh repo create szymonrychu/tatara-argo-workflows --public --description "Argo Workflows CI/CD substrate for the tatara platform" --license MIT
```

Expected: repo URL printed, e.g. `https://github.com/szymonrychu/tatara-argo-workflows`.

- [ ] **Step 2: Clone into siblings dir**

```bash
cd ~/Documents/tatara && git clone https://github.com/szymonrychu/tatara-argo-workflows.git
cd ~/Documents/tatara/tatara-argo-workflows
```

Expected: directory exists with `LICENSE` and `.git`.

- [ ] **Step 3: Verify gitignore from parent matches it**

```bash
grep "^tatara-argo-workflows$" ~/Documents/tatara/.gitignore
```

Expected: line present (already gitignored per `~/Documents/tatara/CLAUDE.md`'s on-disk layout convention). If not, add it:

```bash
echo "tatara-argo-workflows/" >> ~/Documents/tatara/.gitignore
```

- [ ] **Step 4: Set up worktree off main for the bootstrap branch**

```bash
cd ~/Documents/tatara/tatara-argo-workflows
git worktree add .worktrees/bootstrap -b feat/bootstrap-chart
cd .worktrees/bootstrap
echo ".worktrees/" >> .gitignore
git add .gitignore && git commit -m "chore: ignore worktrees"
```

Expected: working in `~/Documents/tatara/tatara-argo-workflows/.worktrees/bootstrap` on branch `feat/bootstrap-chart`.

### Task 0.2: Canonical project files

**Files:**
- Create: `CLAUDE.md`, `README.md`, `ARCHITECTURE.md`, `MEMORY.md`, `ROADMAP.md`, `.sops.yaml`, `.pre-commit-config.yaml`

- [ ] **Step 1: Copy CLAUDE.md from parent**

```bash
cp ~/Documents/tatara/CLAUDE.md ~/Documents/tatara/tatara-argo-workflows/.worktrees/bootstrap/CLAUDE.md
```

Expected: file exists, identical to parent (rule per project CLAUDE.md: copy not symlink).

- [ ] **Step 2: Write README.md**

```bash
cat > README.md <<'EOF'
# tatara-argo-workflows

Argo Workflows CI/CD substrate for the tatara platform.

Ships ClusterWorkflowTemplates and ns-scoped Secrets in `tatara` namespace.
Onboarding is registry-driven from the infra repo (`events.github.repos`).

See `ARCHITECTURE.md` for flow diagrams and the spec at
`~/Documents/tatara/docs/superpowers/specs/2026-05-27-argo-workflows-ci-migration-design.md`.

## Deploy

```bash
helmfile -e default apply -l name=tatara-argo-workflows
```

## Smoke

```bash
argo submit -n tatara \
  --from clusterworkflowtemplate/tatara-go-ci \
  --parameter repo=szymonrychu/tatara-cli \
  --parameter ref=refs/heads/main \
  --parameter sha=<a-commit-sha>
```
EOF
```

- [ ] **Step 3: Write ARCHITECTURE.md**

```bash
cat > ARCHITECTURE.md <<'EOF'
# Architecture

## CI flow (push or pull_request)

```
github push -> events.szymonrichert.pl/push -> argo-events EventSource (infra)
              -> EventBus default (NATS) -> generic Sensor (infra)
              -> Workflow submission in tatara ns from CWT tatara-go-ci
              -> report-pending -> clone -> golangci-lint -> go test -race
              -> onExit -> tatara-github-status -> github commit status
```

Single status check: `tatara/ci`.

## Release flow (tag push on tatara-cli)

Same trigger lane, dispatches to CWT `tatara-go-release`. Steps:
report-pending -> clone (full) -> goreleaser -> onExit.

goreleaser handles: amd64 binary tarball, github release upload, container
push to harbor.

Single status check: `tatara/release`.

## Tag flow on tatara-memory (composite)

Dispatches to CWT `tatara-memory-tag`. DAG:

```
container-build -+
                  +-> helmfile-deploy
helm-publish ----+
```

Four status checks: `tatara/container`, `tatara/chart`, `tatara/deploy`,
`tatara/tag-pipeline` (aggregate).

## Status reporting

Every atomic CWT runs `tatara-github-status` as a first step (state=pending)
and again via `onExit` (state=success|failure|error mapped from
`{{workflow.status}}`). The composite CWT also reports its aggregate state.

PAT `github-status-token` (scope `repo:status` + `contents:write`) lives in
ns Secret `github-status-token` keyed `GITHUB_TOKEN`.
EOF
```

- [ ] **Step 4: Write MEMORY.md stub**

```bash
cat > MEMORY.md <<'EOF'
# MEMORY.md

Component-local memory for tatara-argo-workflows. Cross-repo decisions live
in `~/Documents/tatara/MEMORY.md`.

Format: `YYYY-MM-DD - decision/finding`

---

## Decisions

2026-05-27 - repo bootstrapped; ships only ClusterWorkflowTemplates + ns Secrets. EventSource, Sensor, EventBus, github HMAC secret stay in `~/Documents/infra/` argo-events release.
2026-05-27 - per-step github status (not aggregate-only) because debugging a failed tag pipeline benefits from knowing which sub-step (container/chart/deploy) failed without opening argo UI.
2026-05-27 - composite CWT for tatara-memory-tag only; tatara-cli's `.goreleaser.yaml` handles release+container in one step so push_tag dispatches directly to tatara-go-release.
2026-05-27 - buildkit-daemonless (single container, no sidecar) over kaniko because kaniko needs separate invocations for multi-arch; we're amd64-only so single-invocation either works, but buildkit's path is simpler if multi-arch is added later.

## Dead-ends / things tried that did not work

*(nothing yet)*

## Open questions

*(nothing yet)*
EOF
```

- [ ] **Step 5: Write ROADMAP.md stub**

```bash
cat > ROADMAP.md <<'EOF'
# ROADMAP.md

Component-local roadmap for tatara-argo-workflows. Phase-level platform
roadmap lives in `~/Documents/tatara/ROADMAP.md`.

Statuses: `planned`, `in progress`, `shipped`.

---

## v0.1.0 - phase 5 ship

**Status:** in progress

Seven ClusterWorkflowTemplates + 3 ns-scoped Secrets in `tatara`. Onboarding
declared in infra (`events.github.repos`).

See `~/Documents/tatara/docs/superpowers/specs/2026-05-27-argo-workflows-ci-migration-design.md`.

## v0.1.1 - follow-ups

**Status:** planned

- go mod cache PVC (workflow durations: estimated 90-180s for go-ci can drop to 30s with cache)
- workflow retry-on-flake for known-flaky tests
- ARM64 builds if a target requires it
- Self-onboard tatara-argo-workflows (push_tag triggers its own helmfile-deploy via the new CWTs)
- Add tatara-tasks (phase 6) to the registry when that repo lands

EOF
```

- [ ] **Step 6: Write .sops.yaml**

```bash
cat > .sops.yaml <<'EOF'
creation_rules:
  - path_regex: .*\.secrets\.yaml$
    pgp: D39E46932A270AA3BA490B9DB9FE928D3E8BCED8
EOF
```

Recipient matches tatara-memory's existing .sops.yaml fingerprint. Verify:

```bash
diff .sops.yaml ~/Documents/tatara/tatara-memory/.sops.yaml
```

Expected: no diff.

- [ ] **Step 7: Write .gitignore**

```bash
cat > .gitignore <<'EOF'
# Helm
charts/*/charts/
charts/*/Chart.lock

# Secrets in plaintext are never committed; sops ensures encryption.
# Anything matching *.secrets.plaintext.yaml would be a mistake:
*.secrets.plaintext.yaml
*.plaintext.yaml

# Worktrees
.worktrees/

# OS
.DS_Store

# Editor
.idea/
.vscode/

# Local helmfile build cache
.helmfile/
EOF
```

- [ ] **Step 8: Write .pre-commit-config.yaml**

```bash
cat > .pre-commit-config.yaml <<'EOF'
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
        args: [--allow-multiple-documents]
        exclude: ^charts/.*/templates/.*\.yaml$
      - id: check-added-large-files

  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.21.2
    hooks:
      - id: gitleaks

  - repo: https://github.com/adrienverge/yamllint
    rev: v1.35.1
    hooks:
      - id: yamllint
        args: [-d, '{extends: relaxed, rules: {line-length: {max: 200}}}']
        exclude: ^charts/.*/templates/.*\.yaml$
EOF
```

- [ ] **Step 9: Install hooks and run them**

```bash
pre-commit install
pre-commit run --all-files
```

Expected: PASS (or PASS after the hook's own fixes are applied).

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "chore: bootstrap tatara-argo-workflows repo with canonical files"
```

---

## Wave 1 - Chart skeleton

### Task 1.1: helm create

**Files:**
- Create: `charts/tatara-argo-workflows/` (via `helm create`)

- [ ] **Step 1: helm create**

```bash
mkdir -p charts && cd charts
helm create tatara-argo-workflows
cd ..
```

Expected: `charts/tatara-argo-workflows/` exists with Chart.yaml, values.yaml, templates/.

- [ ] **Step 2: Remove helm-create boilerplate we will not use**

```bash
rm -rf charts/tatara-argo-workflows/templates/tests
rm charts/tatara-argo-workflows/templates/{deployment,service,hpa,serviceaccount,ingress}.yaml
rm charts/tatara-argo-workflows/templates/NOTES.txt
```

Expected: `templates/` contains only `_helpers.tpl`.

- [ ] **Step 3: Overwrite Chart.yaml**

```yaml
# charts/tatara-argo-workflows/Chart.yaml
apiVersion: v2
name: tatara-argo-workflows
description: Argo Workflows CI/CD substrate for the tatara platform.
type: application
version: 0.1.0
appVersion: "0.1.0"
maintainers:
  - name: Szymon Richert
    email: szymon.rychu@gmail.com
```

- [ ] **Step 4: Overwrite values.yaml with scalar config only**

```yaml
# charts/tatara-argo-workflows/values.yaml
namespace: tatara

harbor:
  registry: harbor.szymonrichert.pl

github:
  apiBase: https://api.github.com

# Image pins for workflow step containers. Bump deliberately.
images:
  git: alpine/git:v2.45.2
  golangciLint: golangci/golangci-lint:v2.11.4
  go: golang:1.25
  goreleaser: goreleaser/goreleaser:v2.4.4
  buildkit: moby/buildkit:v0.17.0-rootless
  helm: alpine/helm:3.16.2
  helmfile: ghcr.io/helmfile/helmfile:v0.169.1
  alpine: alpine:3.20
EOF
```

- [ ] **Step 5: Overwrite _helpers.tpl**

```yaml
# charts/tatara-argo-workflows/templates/_helpers.tpl
{{/*
Standard labels.
*/}}
{{- define "tatara-argo-workflows.labels" -}}
app.kubernetes.io/name: tatara-argo-workflows
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
{{- end -}}
```

- [ ] **Step 6: Lint**

```bash
helm lint charts/tatara-argo-workflows
```

Expected: `1 chart(s) linted, 0 chart(s) failed`.

- [ ] **Step 7: Template (empty render so far is fine)**

```bash
helm template foo charts/tatara-argo-workflows
```

Expected: no output (no templates yet), exit 0.

- [ ] **Step 8: Commit**

```bash
git add charts/tatara-argo-workflows
git commit -m "feat: scaffold chart with values.yaml and _helpers.tpl"
```

---

### Task 1.2: Secrets values + sops encryption

**Files:**
- Create: `values/tatara-argo-workflows/common.yaml`, `default.yaml`, `default.secrets.yaml` (sops)

- [ ] **Step 1: Stub common.yaml + default.yaml**

```bash
mkdir -p values/tatara-argo-workflows
cat > values/tatara-argo-workflows/common.yaml <<'EOF'
# Cross-environment values. Override per-env in <env>.yaml.
EOF
cat > values/tatara-argo-workflows/default.yaml <<'EOF'
# Default environment values. Production-equivalent; this is a homelab.
EOF
```

- [ ] **Step 2: Read harbor robot creds from infra/.env**

```bash
source ~/Documents/infra/.env
echo "$HARBOR_ROBOT_GITLAB_USERNAME" | head -c 10; echo
```

Expected: a non-empty robot username prefix prints.

- [ ] **Step 3: Create github status PAT externally**

Run in browser: https://github.com/settings/tokens/new

- Note: `tatara-argo-workflows status reporting`
- Scopes: `repo:status`, `public_repo` (sufficient since all tatara repos are public), or fine-grained PAT with `Commit statuses: Read and write` + `Contents: Read and write` on `szymonrychu/tatara-*`
- Copy generated token; you will paste it in the next step

- [ ] **Step 4: Locate age key**

```bash
ls -la ~/.config/sops/age/keys.txt
```

Expected: file exists with `AGE-SECRET-KEY-...` content. (The user's existing sops key; same one tatara-memory uses.)

- [ ] **Step 5: Write plaintext secrets (will encrypt next)**

```bash
cat > /tmp/tatara-argo-workflows.plaintext.yaml <<EOF
harborRobot:
  username: ${HARBOR_ROBOT_GITLAB_USERNAME}
  password: ${HARBOR_ROBOT_GITLAB_PASSWORD}

# Paste the PAT from step 3 below
githubStatusToken: REPLACE_WITH_PAT

# age key contents (multi-line)
ageKey: |
$(cat ~/.config/sops/age/keys.txt | sed 's/^/  /')
EOF
```

Edit `/tmp/tatara-argo-workflows.plaintext.yaml` and replace `REPLACE_WITH_PAT` with the actual token from step 3.

- [ ] **Step 6: Encrypt with sops to final location**

```bash
sops --encrypt --pgp D39E46932A270AA3BA490B9DB9FE928D3E8BCED8 \
  /tmp/tatara-argo-workflows.plaintext.yaml \
  > values/tatara-argo-workflows/default.secrets.yaml
shred -u /tmp/tatara-argo-workflows.plaintext.yaml
```

Expected: `values/tatara-argo-workflows/default.secrets.yaml` exists and starts with `harborRobot:` and ends with sops metadata block.

- [ ] **Step 7: Verify decryption round-trip**

```bash
sops --decrypt values/tatara-argo-workflows/default.secrets.yaml | head -20
```

Expected: plaintext yaml prints with the original keys present.

- [ ] **Step 8: Commit**

```bash
git add values/tatara-argo-workflows/
git commit -m "feat: encrypted values for harbor robot, age key, github status PAT"
```

---

### Task 1.3: Helmfile entrypoint

**Files:**
- Create: `helmfile.yaml.gotmpl`

- [ ] **Step 1: Write helmfile**

```yaml
# helmfile.yaml.gotmpl
environments:
  default: {}

---

helmDefaults:
  wait: true
  syncArgs:
    - --force-conflicts
    - --rollback-on-failure

templates:
  default: &default
    missingFileHandler: Warn
    values:
      - values/{{`{{ .Release.Name }}`}}/common.yaml
      - values/{{`{{ .Release.Name }}`}}/{{`{{ .Environment.Name }}`}}.yaml
    secrets:
      - values/{{`{{ .Release.Name }}`}}/{{`{{ .Environment.Name }}`}}.secrets.yaml

releases:
  - name: tatara-argo-workflows
    namespace: tatara
    chart: ./charts/tatara-argo-workflows
    createNamespace: true
    <<: *default
```

- [ ] **Step 2: helmfile build (dry parse)**

```bash
helmfile build > /dev/null
```

Expected: exit 0, no output. Validates the gotmpl + secrets resolution.

- [ ] **Step 3: helmfile template (render the chart)**

```bash
helmfile -e default template
```

Expected: empty render (no manifests in chart yet), exit 0. If it errors with "namespace: tatara not found", that's expected for actual apply later; template should still pass.

- [ ] **Step 4: Commit**

```bash
git add helmfile.yaml.gotmpl
git commit -m "feat: helmfile entrypoint for default environment"
```

---

## Wave 2 - ClusterWorkflowTemplates

### Task 2.1: cwt-github-status (helper)

**Files:**
- Create: `charts/tatara-argo-workflows/templates/cwt-github-status.yaml`

Build this CWT first since every other CWT references it.

- [ ] **Step 1: Write the helper CWT**

```yaml
# charts/tatara-argo-workflows/templates/cwt-github-status.yaml
apiVersion: argoproj.io/v1alpha1
kind: ClusterWorkflowTemplate
metadata:
  name: tatara-github-status
  labels:
    {{- include "tatara-argo-workflows.labels" . | nindent 4 }}
spec:
  entrypoint: report
  arguments:
    parameters:
      - { name: repo,        value: "" }
      - { name: sha,         value: "" }
      - { name: state,       value: "pending" }
      - { name: context,     value: "tatara/unknown" }
      - { name: description, value: "" }
      - { name: targetUrl,   value: "" }
  templates:
    - name: report
      inputs:
        parameters:
          - name: repo
          - name: sha
          - name: state
          - name: context
          - name: description
          - name: targetUrl
      container:
        image: {{ .Values.images.alpine | quote }}
        command: [sh, -c]
        env:
          - name: GITHUB_TOKEN
            valueFrom:
              secretKeyRef:
                name: github-status-token
                key: GITHUB_TOKEN
        args:
          - |
            set -eu
            apk add --no-cache curl jq >/dev/null
            # Map argo workflow status string to github API state.
            case "{{`{{inputs.parameters.state}}`}}" in
              Succeeded) STATE=success ;;
              Failed)    STATE=failure ;;
              Error)     STATE=error   ;;
              pending|Running) STATE=pending ;;
              *) STATE="{{`{{inputs.parameters.state}}`}}" ;;
            esac
            BODY=$(jq -n \
              --arg state "$STATE" \
              --arg context "{{`{{inputs.parameters.context}}`}}" \
              --arg description "{{`{{inputs.parameters.description}}`}}" \
              --arg target_url "{{`{{inputs.parameters.targetUrl}}`}}" \
              '{state:$state, context:$context, description:$description, target_url:$target_url}')
            HTTP_CODE=$(curl -sS -o /tmp/resp.json -w '%{http_code}' \
              -X POST \
              -H "Authorization: token ${GITHUB_TOKEN}" \
              -H "Accept: application/vnd.github+json" \
              -d "$BODY" \
              "{{ .Values.github.apiBase }}/repos/{{`{{inputs.parameters.repo}}`}}/statuses/{{`{{inputs.parameters.sha}}`}}")
            if [ "$HTTP_CODE" != "201" ]; then
              echo "github status POST failed: HTTP $HTTP_CODE" >&2
              cat /tmp/resp.json >&2
              exit 1
            fi
            echo "github status posted: $STATE for {{`{{inputs.parameters.context}}`}}"
```

Note: `{{`...`}}` is helm's escape for argo's `{{ }}` placeholder syntax so helm does not consume the braces.

- [ ] **Step 2: helm template + extract this resource**

```bash
helm template foo charts/tatara-argo-workflows | grep -A 50 "name: tatara-github-status"
```

Expected: renders cleanly with argo template parameters intact (`{{inputs.parameters.state}}` literal in the output).

- [ ] **Step 3: argo lint (dry validation)**

```bash
helm template foo charts/tatara-argo-workflows > /tmp/render.yaml
kubectl apply --dry-run=client -f /tmp/render.yaml 2>&1 | grep -i "tatara-github-status"
```

Expected: `clusterworkflowtemplate.argoproj.io/tatara-github-status created (dry run)` or similar.

- [ ] **Step 4: Commit**

```bash
git add charts/tatara-argo-workflows/templates/cwt-github-status.yaml
git commit -m "feat: tatara-github-status helper CWT for commit-status reporting"
```

---

### Task 2.2: cwt-go-ci

**Files:**
- Create: `charts/tatara-argo-workflows/templates/cwt-go-ci.yaml`

- [ ] **Step 1: Write the CWT**

```yaml
# charts/tatara-argo-workflows/templates/cwt-go-ci.yaml
apiVersion: argoproj.io/v1alpha1
kind: ClusterWorkflowTemplate
metadata:
  name: tatara-go-ci
  labels:
    {{- include "tatara-argo-workflows.labels" . | nindent 4 }}
spec:
  entrypoint: main
  onExit: report-exit
  arguments:
    parameters:
      - { name: repo,      value: "" }
      - { name: ref,       value: "" }
      - { name: sha,       value: "" }
      - { name: prNumber,  value: "" }
  volumeClaimTemplates:
    - metadata:
        name: workspace
      spec:
        accessModes: [ReadWriteOnce]
        resources:
          requests:
            storage: 2Gi
  templates:
    - name: main
      steps:
        - - name: report-pending
            templateRef:
              name: tatara-github-status
              template: report
              clusterScope: true
            arguments:
              parameters:
                - { name: repo,        value: "{{`{{workflow.parameters.repo}}`}}" }
                - { name: sha,         value: "{{`{{workflow.parameters.sha}}`}}" }
                - { name: state,       value: "pending" }
                - { name: context,     value: "tatara/ci" }
                - { name: description, value: "lint + test running" }
                - { name: targetUrl,   value: "https://workflows.szymonrichert.pl/workflows/{{`{{workflow.namespace}}`}}/{{`{{workflow.name}}`}}" }
        - - name: clone
            template: clone
        - - name: lint
            template: lint
        - - name: test
            template: test

    - name: clone
      container:
        image: {{ .Values.images.git | quote }}
        workingDir: /workspace
        command: [sh, -c]
        args:
          - |
            set -eux
            git clone --depth=1 "https://github.com/{{`{{workflow.parameters.repo}}`}}" src
            cd src
            git fetch --depth=1 origin "{{`{{workflow.parameters.sha}}`}}" || true
            git checkout "{{`{{workflow.parameters.sha}}`}}"
        volumeMounts:
          - { name: workspace, mountPath: /workspace }

    - name: lint
      container:
        image: {{ .Values.images.golangciLint | quote }}
        workingDir: /workspace/src
        command: [sh, -c]
        args:
          - golangci-lint run ./...
        volumeMounts:
          - { name: workspace, mountPath: /workspace }

    - name: test
      container:
        image: {{ .Values.images.go | quote }}
        workingDir: /workspace/src
        command: [sh, -c]
        args:
          - |
            set -eux
            go test -race -count=1 ./...
        volumeMounts:
          - { name: workspace, mountPath: /workspace }

    - name: report-exit
      steps:
        - - name: post
            templateRef:
              name: tatara-github-status
              template: report
              clusterScope: true
            arguments:
              parameters:
                - { name: repo,        value: "{{`{{workflow.parameters.repo}}`}}" }
                - { name: sha,         value: "{{`{{workflow.parameters.sha}}`}}" }
                - { name: state,       value: "{{`{{workflow.status}}`}}" }
                - { name: context,     value: "tatara/ci" }
                - { name: description, value: "{{`{{workflow.status}}`}}" }
                - { name: targetUrl,   value: "https://workflows.szymonrichert.pl/workflows/{{`{{workflow.namespace}}`}}/{{`{{workflow.name}}`}}" }
```

- [ ] **Step 2: Render and dry-apply**

```bash
helm template foo charts/tatara-argo-workflows > /tmp/render.yaml
kubectl apply --dry-run=client -f /tmp/render.yaml 2>&1 | grep -i "tatara-go-ci"
```

Expected: dry-run success.

- [ ] **Step 3: Commit**

```bash
git add charts/tatara-argo-workflows/templates/cwt-go-ci.yaml
git commit -m "feat: tatara-go-ci CWT (lint + test + status reporting)"
```

---

### Task 2.3: cwt-container-build

**Files:**
- Create: `charts/tatara-argo-workflows/templates/cwt-container-build.yaml`

- [ ] **Step 1: Write the CWT**

```yaml
# charts/tatara-argo-workflows/templates/cwt-container-build.yaml
apiVersion: argoproj.io/v1alpha1
kind: ClusterWorkflowTemplate
metadata:
  name: tatara-container-build
  labels:
    {{- include "tatara-argo-workflows.labels" . | nindent 4 }}
spec:
  entrypoint: main
  onExit: report-exit
  arguments:
    parameters:
      - { name: repo,      value: "" }
      - { name: ref,       value: "" }
      - { name: sha,       value: "" }
      - { name: tag,       value: "" }
      - { name: imageName, value: "" }
  volumeClaimTemplates:
    - metadata:
        name: workspace
      spec:
        accessModes: [ReadWriteOnce]
        resources:
          requests:
            storage: 4Gi
  templates:
    - name: main
      steps:
        - - name: report-pending
            templateRef:
              name: tatara-github-status
              template: report
              clusterScope: true
            arguments:
              parameters:
                - { name: repo,        value: "{{`{{workflow.parameters.repo}}`}}" }
                - { name: sha,         value: "{{`{{workflow.parameters.sha}}`}}" }
                - { name: state,       value: "pending" }
                - { name: context,     value: "tatara/container" }
                - { name: description, value: "building amd64 image" }
                - { name: targetUrl,   value: "https://workflows.szymonrichert.pl/workflows/{{`{{workflow.namespace}}`}}/{{`{{workflow.name}}`}}" }
        - - name: clone
            template: clone
        - - name: build-push
            template: build-push

    - name: clone
      container:
        image: {{ .Values.images.git | quote }}
        workingDir: /workspace
        command: [sh, -c]
        args:
          - |
            set -eux
            git clone --depth=1 "https://github.com/{{`{{workflow.parameters.repo}}`}}" src
            cd src
            git fetch --depth=1 origin "{{`{{workflow.parameters.sha}}`}}" || true
            git checkout "{{`{{workflow.parameters.sha}}`}}"
        volumeMounts:
          - { name: workspace, mountPath: /workspace }

    - name: build-push
      container:
        image: {{ .Values.images.buildkit | quote }}
        workingDir: /workspace/src
        command: [sh, -c]
        env:
          - name: HARBOR_USERNAME
            valueFrom:
              secretKeyRef:
                name: harbor-robot
                key: username
          - name: HARBOR_PASSWORD
            valueFrom:
              secretKeyRef:
                name: harbor-robot
                key: password
        args:
          - |
            set -eux
            mkdir -p ~/.docker
            B64=$(printf '%s:%s' "$HARBOR_USERNAME" "$HARBOR_PASSWORD" | base64 -w0)
            cat > ~/.docker/config.json <<JSON
            {"auths":{"{{ .Values.harbor.registry }}":{"auth":"${B64}"}}}
            JSON
            TAG_V="{{`{{workflow.parameters.tag}}`}}"
            TAG_PLAIN="${TAG_V#v}"
            IMG="{{ .Values.harbor.registry }}/{{`{{workflow.parameters.imageName}}`}}"
            buildctl-daemonless.sh build \
              --frontend dockerfile.v0 \
              --local context=. \
              --local dockerfile=. \
              --opt platform=linux/amd64 \
              --output type=image,\"name=${IMG}:${TAG_V},${IMG}:${TAG_PLAIN}\",push=true
        securityContext:
          seccompProfile:
            type: Unconfined
          # Buildkit rootless still wants user namespaces; if the host kernel does
          # not allow unprivileged user_namespaces, switch type to RuntimeDefault
          # and set runAsUser/runAsGroup to 1000.
        volumeMounts:
          - { name: workspace, mountPath: /workspace }

    - name: report-exit
      steps:
        - - name: post
            templateRef:
              name: tatara-github-status
              template: report
              clusterScope: true
            arguments:
              parameters:
                - { name: repo,        value: "{{`{{workflow.parameters.repo}}`}}" }
                - { name: sha,         value: "{{`{{workflow.parameters.sha}}`}}" }
                - { name: state,       value: "{{`{{workflow.status}}`}}" }
                - { name: context,     value: "tatara/container" }
                - { name: description, value: "{{`{{workflow.status}}`}}" }
                - { name: targetUrl,   value: "https://workflows.szymonrichert.pl/workflows/{{`{{workflow.namespace}}`}}/{{`{{workflow.name}}`}}" }
```

- [ ] **Step 2: Render and dry-apply**

```bash
helm template foo charts/tatara-argo-workflows > /tmp/render.yaml
kubectl apply --dry-run=client -f /tmp/render.yaml 2>&1 | grep -i "tatara-container-build"
```

Expected: dry-run success.

- [ ] **Step 3: Commit**

```bash
git add charts/tatara-argo-workflows/templates/cwt-container-build.yaml
git commit -m "feat: tatara-container-build CWT (buildkit, dual-tag push)"
```

---

### Task 2.4: cwt-go-release

**Files:**
- Create: `charts/tatara-argo-workflows/templates/cwt-go-release.yaml`

- [ ] **Step 1: Write the CWT**

```yaml
# charts/tatara-argo-workflows/templates/cwt-go-release.yaml
apiVersion: argoproj.io/v1alpha1
kind: ClusterWorkflowTemplate
metadata:
  name: tatara-go-release
  labels:
    {{- include "tatara-argo-workflows.labels" . | nindent 4 }}
spec:
  entrypoint: main
  onExit: report-exit
  arguments:
    parameters:
      - { name: repo, value: "" }
      - { name: ref,  value: "" }
      - { name: sha,  value: "" }
      - { name: tag,  value: "" }
  volumeClaimTemplates:
    - metadata:
        name: workspace
      spec:
        accessModes: [ReadWriteOnce]
        resources:
          requests:
            storage: 4Gi
  templates:
    - name: main
      steps:
        - - name: report-pending
            templateRef:
              name: tatara-github-status
              template: report
              clusterScope: true
            arguments:
              parameters:
                - { name: repo,        value: "{{`{{workflow.parameters.repo}}`}}" }
                - { name: sha,         value: "{{`{{workflow.parameters.sha}}`}}" }
                - { name: state,       value: "pending" }
                - { name: context,     value: "tatara/release" }
                - { name: description, value: "running goreleaser" }
                - { name: targetUrl,   value: "https://workflows.szymonrichert.pl/workflows/{{`{{workflow.namespace}}`}}/{{`{{workflow.name}}`}}" }
        - - name: clone
            template: clone-full
        - - name: goreleaser
            template: goreleaser

    - name: clone-full
      container:
        image: {{ .Values.images.git | quote }}
        workingDir: /workspace
        command: [sh, -c]
        args:
          - |
            set -eux
            git clone "https://github.com/{{`{{workflow.parameters.repo}}`}}" src
            cd src
            git fetch --tags
            git checkout "{{`{{workflow.parameters.sha}}`}}"
        volumeMounts:
          - { name: workspace, mountPath: /workspace }

    - name: goreleaser
      container:
        image: {{ .Values.images.goreleaser | quote }}
        workingDir: /workspace/src
        command: [sh, -c]
        env:
          - name: GITHUB_TOKEN
            valueFrom:
              secretKeyRef:
                name: github-status-token
                key: GITHUB_TOKEN
          - name: HARBOR_USERNAME
            valueFrom:
              secretKeyRef:
                name: harbor-robot
                key: username
          - name: HARBOR_PASSWORD
            valueFrom:
              secretKeyRef:
                name: harbor-robot
                key: password
        args:
          - |
            set -eux
            mkdir -p ~/.docker
            B64=$(printf '%s:%s' "$HARBOR_USERNAME" "$HARBOR_PASSWORD" | base64 -w0)
            cat > ~/.docker/config.json <<JSON
            {"auths":{"{{ .Values.harbor.registry }}":{"auth":"${B64}"}}}
            JSON
            goreleaser release --clean
        volumeMounts:
          - { name: workspace, mountPath: /workspace }

    - name: report-exit
      steps:
        - - name: post
            templateRef:
              name: tatara-github-status
              template: report
              clusterScope: true
            arguments:
              parameters:
                - { name: repo,        value: "{{`{{workflow.parameters.repo}}`}}" }
                - { name: sha,         value: "{{`{{workflow.parameters.sha}}`}}" }
                - { name: state,       value: "{{`{{workflow.status}}`}}" }
                - { name: context,     value: "tatara/release" }
                - { name: description, value: "{{`{{workflow.status}}`}}" }
                - { name: targetUrl,   value: "https://workflows.szymonrichert.pl/workflows/{{`{{workflow.namespace}}`}}/{{`{{workflow.name}}`}}" }
```

- [ ] **Step 2: Render and dry-apply**

```bash
helm template foo charts/tatara-argo-workflows > /tmp/render.yaml
kubectl apply --dry-run=client -f /tmp/render.yaml 2>&1 | grep -i "tatara-go-release"
```

Expected: dry-run success.

- [ ] **Step 3: Commit**

```bash
git add charts/tatara-argo-workflows/templates/cwt-go-release.yaml
git commit -m "feat: tatara-go-release CWT (goreleaser, amd64 binary + container + github release)"
```

---

### Task 2.5: cwt-helm-publish

**Files:**
- Create: `charts/tatara-argo-workflows/templates/cwt-helm-publish.yaml`

- [ ] **Step 1: Write the CWT**

```yaml
# charts/tatara-argo-workflows/templates/cwt-helm-publish.yaml
apiVersion: argoproj.io/v1alpha1
kind: ClusterWorkflowTemplate
metadata:
  name: tatara-helm-publish
  labels:
    {{- include "tatara-argo-workflows.labels" . | nindent 4 }}
spec:
  entrypoint: main
  onExit: report-exit
  arguments:
    parameters:
      - { name: repo,       value: "" }
      - { name: ref,        value: "" }
      - { name: sha,        value: "" }
      - { name: tag,        value: "" }
      - { name: chartDir,   value: "" }
      - { name: chartName,  value: "" }
  volumeClaimTemplates:
    - metadata:
        name: workspace
      spec:
        accessModes: [ReadWriteOnce]
        resources:
          requests:
            storage: 2Gi
  templates:
    - name: main
      steps:
        - - name: report-pending
            templateRef:
              name: tatara-github-status
              template: report
              clusterScope: true
            arguments:
              parameters:
                - { name: repo,        value: "{{`{{workflow.parameters.repo}}`}}" }
                - { name: sha,         value: "{{`{{workflow.parameters.sha}}`}}" }
                - { name: state,       value: "pending" }
                - { name: context,     value: "tatara/chart" }
                - { name: description, value: "packaging chart" }
                - { name: targetUrl,   value: "https://workflows.szymonrichert.pl/workflows/{{`{{workflow.namespace}}`}}/{{`{{workflow.name}}`}}" }
        - - name: clone
            template: clone
        - - name: package-push
            template: package-push

    - name: clone
      container:
        image: {{ .Values.images.git | quote }}
        workingDir: /workspace
        command: [sh, -c]
        args:
          - |
            set -eux
            git clone --depth=1 "https://github.com/{{`{{workflow.parameters.repo}}`}}" src
            cd src
            git fetch --depth=1 origin "{{`{{workflow.parameters.sha}}`}}" || true
            git checkout "{{`{{workflow.parameters.sha}}`}}"
        volumeMounts:
          - { name: workspace, mountPath: /workspace }

    - name: package-push
      container:
        image: {{ .Values.images.helm | quote }}
        workingDir: /workspace/src
        command: [sh, -c]
        env:
          - name: HARBOR_USERNAME
            valueFrom:
              secretKeyRef:
                name: harbor-robot
                key: username
          - name: HARBOR_PASSWORD
            valueFrom:
              secretKeyRef:
                name: harbor-robot
                key: password
        args:
          - |
            set -eux
            apk add --no-cache sed >/dev/null
            CHART_DIR="{{`{{workflow.parameters.chartDir}}`}}"
            CHART_NAME="{{`{{workflow.parameters.chartName}}`}}"
            TAG_V="{{`{{workflow.parameters.tag}}`}}"
            VER="${TAG_V#v}"
            sed -i "s/^version:.*/version: ${VER}/"     "${CHART_DIR}/Chart.yaml"
            sed -i "s/^appVersion:.*/appVersion: \"${VER}\"/" "${CHART_DIR}/Chart.yaml"
            helm dependency update "${CHART_DIR}"
            helm package "${CHART_DIR}"
            echo "${HARBOR_PASSWORD}" | helm registry login -u "${HARBOR_USERNAME}" --password-stdin {{ .Values.harbor.registry }}
            helm push "${CHART_NAME}-${VER}.tgz" "oci://{{ .Values.harbor.registry }}/charts"
        volumeMounts:
          - { name: workspace, mountPath: /workspace }

    - name: report-exit
      steps:
        - - name: post
            templateRef:
              name: tatara-github-status
              template: report
              clusterScope: true
            arguments:
              parameters:
                - { name: repo,        value: "{{`{{workflow.parameters.repo}}`}}" }
                - { name: sha,         value: "{{`{{workflow.parameters.sha}}`}}" }
                - { name: state,       value: "{{`{{workflow.status}}`}}" }
                - { name: context,     value: "tatara/chart" }
                - { name: description, value: "{{`{{workflow.status}}`}}" }
                - { name: targetUrl,   value: "https://workflows.szymonrichert.pl/workflows/{{`{{workflow.namespace}}`}}/{{`{{workflow.name}}`}}" }
```

- [ ] **Step 2: Render and dry-apply**

```bash
helm template foo charts/tatara-argo-workflows > /tmp/render.yaml
kubectl apply --dry-run=client -f /tmp/render.yaml 2>&1 | grep -i "tatara-helm-publish"
```

- [ ] **Step 3: Commit**

```bash
git add charts/tatara-argo-workflows/templates/cwt-helm-publish.yaml
git commit -m "feat: tatara-helm-publish CWT (rewrite Chart.yaml + OCI push)"
```

---

### Task 2.6: cwt-helmfile-deploy

**Files:**
- Create: `charts/tatara-argo-workflows/templates/cwt-helmfile-deploy.yaml`

- [ ] **Step 1: Write the CWT**

```yaml
# charts/tatara-argo-workflows/templates/cwt-helmfile-deploy.yaml
apiVersion: argoproj.io/v1alpha1
kind: ClusterWorkflowTemplate
metadata:
  name: tatara-helmfile-deploy
  labels:
    {{- include "tatara-argo-workflows.labels" . | nindent 4 }}
spec:
  entrypoint: main
  onExit: report-exit
  arguments:
    parameters:
      - { name: repo,         value: "" }
      - { name: ref,          value: "" }
      - { name: sha,          value: "" }
      - { name: tag,          value: "" }
      - { name: helmfileDir,  value: "." }
  volumeClaimTemplates:
    - metadata:
        name: workspace
      spec:
        accessModes: [ReadWriteOnce]
        resources:
          requests:
            storage: 2Gi
  templates:
    - name: main
      steps:
        - - name: report-pending
            templateRef:
              name: tatara-github-status
              template: report
              clusterScope: true
            arguments:
              parameters:
                - { name: repo,        value: "{{`{{workflow.parameters.repo}}`}}" }
                - { name: sha,         value: "{{`{{workflow.parameters.sha}}`}}" }
                - { name: state,       value: "pending" }
                - { name: context,     value: "tatara/deploy" }
                - { name: description, value: "running helmfile sync" }
                - { name: targetUrl,   value: "https://workflows.szymonrichert.pl/workflows/{{`{{workflow.namespace}}`}}/{{`{{workflow.name}}`}}" }
        - - name: clone
            template: clone
        - - name: sync
            template: sync

    - name: clone
      container:
        image: {{ .Values.images.git | quote }}
        workingDir: /workspace
        command: [sh, -c]
        args:
          - |
            set -eux
            git clone --depth=1 "https://github.com/{{`{{workflow.parameters.repo}}`}}" src
            cd src
            git fetch --depth=1 origin "{{`{{workflow.parameters.sha}}`}}" || true
            git checkout "{{`{{workflow.parameters.sha}}`}}"
        volumeMounts:
          - { name: workspace, mountPath: /workspace }

    - name: sync
      container:
        image: {{ .Values.images.helmfile | quote }}
        workingDir: "/workspace/src"
        command: [sh, -c]
        env:
          - name: HARBOR_USERNAME
            valueFrom:
              secretKeyRef:
                name: harbor-robot
                key: username
          - name: HARBOR_PASSWORD
            valueFrom:
              secretKeyRef:
                name: harbor-robot
                key: password
        args:
          - |
            set -eux
            mkdir -p ~/.config/sops/age
            cat > ~/.config/sops/age/keys.txt <<KEY
            $(cat /var/run/secrets/age/keys.txt)
            KEY
            chmod 600 ~/.config/sops/age/keys.txt
            export SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt
            echo "${HARBOR_PASSWORD}" | helm registry login -u "${HARBOR_USERNAME}" --password-stdin {{ .Values.harbor.registry }}
            cd "{{`{{workflow.parameters.helmfileDir}}`}}"
            helmfile -e default sync --args "--force-conflicts"
        volumeMounts:
          - { name: workspace, mountPath: /workspace }
          - { name: age-key, mountPath: /var/run/secrets/age, readOnly: true }
      volumes:
        - name: age-key
          secret:
            secretName: age-key
            defaultMode: 0400

    - name: report-exit
      steps:
        - - name: post
            templateRef:
              name: tatara-github-status
              template: report
              clusterScope: true
            arguments:
              parameters:
                - { name: repo,        value: "{{`{{workflow.parameters.repo}}`}}" }
                - { name: sha,         value: "{{`{{workflow.parameters.sha}}`}}" }
                - { name: state,       value: "{{`{{workflow.status}}`}}" }
                - { name: context,     value: "tatara/deploy" }
                - { name: description, value: "{{`{{workflow.status}}`}}" }
                - { name: targetUrl,   value: "https://workflows.szymonrichert.pl/workflows/{{`{{workflow.namespace}}`}}/{{`{{workflow.name}}`}}" }
```

Note: `helmfile sync` requires kubeconfig pointing at the cluster. Argo workflow pods get an in-cluster SA token at `/var/run/secrets/kubernetes.io/serviceaccount/`. helmfile's underlying `helm` picks it up automatically when KUBECONFIG is unset.

- [ ] **Step 2: Render and dry-apply**

```bash
helm template foo charts/tatara-argo-workflows > /tmp/render.yaml
kubectl apply --dry-run=client -f /tmp/render.yaml 2>&1 | grep -i "tatara-helmfile-deploy"
```

- [ ] **Step 3: Commit**

```bash
git add charts/tatara-argo-workflows/templates/cwt-helmfile-deploy.yaml
git commit -m "feat: tatara-helmfile-deploy CWT (sops + helmfile sync from cluster SA)"
```

---

### Task 2.7: cwt-tatara-memory-tag (composite)

**Files:**
- Create: `charts/tatara-argo-workflows/templates/cwt-tatara-memory-tag.yaml`

- [ ] **Step 1: Write the composite CWT**

```yaml
# charts/tatara-argo-workflows/templates/cwt-tatara-memory-tag.yaml
apiVersion: argoproj.io/v1alpha1
kind: ClusterWorkflowTemplate
metadata:
  name: tatara-memory-tag
  labels:
    {{- include "tatara-argo-workflows.labels" . | nindent 4 }}
spec:
  entrypoint: main
  onExit: report-exit
  arguments:
    parameters:
      - { name: repo, value: "" }
      - { name: ref,  value: "" }
      - { name: sha,  value: "" }
      - { name: tag,  value: "" }
  templates:
    - name: main
      dag:
        tasks:
          - name: container-build
            templateRef:
              name: tatara-container-build
              template: main
              clusterScope: true
            arguments:
              parameters:
                - { name: repo,      value: "{{`{{workflow.parameters.repo}}`}}" }
                - { name: ref,       value: "{{`{{workflow.parameters.ref}}`}}" }
                - { name: sha,       value: "{{`{{workflow.parameters.sha}}`}}" }
                - { name: tag,       value: "{{`{{workflow.parameters.tag}}`}}" }
                - { name: imageName, value: "containers/tatara-memory" }
          - name: helm-publish
            templateRef:
              name: tatara-helm-publish
              template: main
              clusterScope: true
            arguments:
              parameters:
                - { name: repo,      value: "{{`{{workflow.parameters.repo}}`}}" }
                - { name: ref,       value: "{{`{{workflow.parameters.ref}}`}}" }
                - { name: sha,       value: "{{`{{workflow.parameters.sha}}`}}" }
                - { name: tag,       value: "{{`{{workflow.parameters.tag}}`}}" }
                - { name: chartDir,  value: "charts/tatara-memory" }
                - { name: chartName, value: "tatara-memory" }
          - name: helmfile-deploy
            depends: "container-build && helm-publish"
            templateRef:
              name: tatara-helmfile-deploy
              template: main
              clusterScope: true
            arguments:
              parameters:
                - { name: repo,        value: "{{`{{workflow.parameters.repo}}`}}" }
                - { name: ref,         value: "{{`{{workflow.parameters.ref}}`}}" }
                - { name: sha,         value: "{{`{{workflow.parameters.sha}}`}}" }
                - { name: tag,         value: "{{`{{workflow.parameters.tag}}`}}" }
                - { name: helmfileDir, value: "." }

    - name: report-exit
      steps:
        - - name: post
            templateRef:
              name: tatara-github-status
              template: report
              clusterScope: true
            arguments:
              parameters:
                - { name: repo,        value: "{{`{{workflow.parameters.repo}}`}}" }
                - { name: sha,         value: "{{`{{workflow.parameters.sha}}`}}" }
                - { name: state,       value: "{{`{{workflow.status}}`}}" }
                - { name: context,     value: "tatara/tag-pipeline" }
                - { name: description, value: "{{`{{workflow.status}}`}}" }
                - { name: targetUrl,   value: "https://workflows.szymonrichert.pl/workflows/{{`{{workflow.namespace}}`}}/{{`{{workflow.name}}`}}" }
```

- [ ] **Step 2: Render and dry-apply**

```bash
helm template foo charts/tatara-argo-workflows > /tmp/render.yaml
kubectl apply --dry-run=client -f /tmp/render.yaml 2>&1 | grep -i "tatara-memory-tag"
```

- [ ] **Step 3: Commit**

```bash
git add charts/tatara-argo-workflows/templates/cwt-tatara-memory-tag.yaml
git commit -m "feat: tatara-memory-tag composite CWT (DAG: container+chart || deploy)"
```

---

## Wave 3 - Secrets templates in chart

### Task 3.1: harbor-robot secret

**Files:**
- Create: `charts/tatara-argo-workflows/templates/secret-harbor-robot.yaml`

- [ ] **Step 1: Write template**

```yaml
# charts/tatara-argo-workflows/templates/secret-harbor-robot.yaml
{{- if .Values.harborRobot }}
apiVersion: v1
kind: Secret
metadata:
  name: harbor-robot
  namespace: {{ .Values.namespace }}
  labels:
    {{- include "tatara-argo-workflows.labels" . | nindent 4 }}
type: Opaque
stringData:
  username: {{ required "harborRobot.username required (from sops values)" .Values.harborRobot.username | quote }}
  password: {{ required "harborRobot.password required (from sops values)" .Values.harborRobot.password | quote }}
{{- end }}
```

- [ ] **Step 2: Render with sops secrets**

```bash
helmfile -e default template | grep -A 5 "name: harbor-robot"
```

Expected: Secret renders with `stringData.username` and `stringData.password` populated from decrypted sops values. Verify values are NOT literal placeholders.

- [ ] **Step 3: Commit**

```bash
git add charts/tatara-argo-workflows/templates/secret-harbor-robot.yaml
git commit -m "feat: harbor-robot Secret template (ns tatara)"
```

### Task 3.2: github-status-token secret

**Files:**
- Create: `charts/tatara-argo-workflows/templates/secret-github-status.yaml`

- [ ] **Step 1: Write template**

```yaml
# charts/tatara-argo-workflows/templates/secret-github-status.yaml
{{- if .Values.githubStatusToken }}
apiVersion: v1
kind: Secret
metadata:
  name: github-status-token
  namespace: {{ .Values.namespace }}
  labels:
    {{- include "tatara-argo-workflows.labels" . | nindent 4 }}
type: Opaque
stringData:
  GITHUB_TOKEN: {{ required "githubStatusToken required (from sops values)" .Values.githubStatusToken | quote }}
{{- end }}
```

- [ ] **Step 2: Render**

```bash
helmfile -e default template | grep -A 5 "name: github-status-token"
```

Expected: rendered Secret has `stringData.GITHUB_TOKEN` populated.

- [ ] **Step 3: Commit**

```bash
git add charts/tatara-argo-workflows/templates/secret-github-status.yaml
git commit -m "feat: github-status-token Secret template (ns tatara)"
```

### Task 3.3: age-key secret

**Files:**
- Create: `charts/tatara-argo-workflows/templates/secret-age-key.yaml`

- [ ] **Step 1: Write template**

```yaml
# charts/tatara-argo-workflows/templates/secret-age-key.yaml
{{- if .Values.ageKey }}
apiVersion: v1
kind: Secret
metadata:
  name: age-key
  namespace: {{ .Values.namespace }}
  labels:
    {{- include "tatara-argo-workflows.labels" . | nindent 4 }}
type: Opaque
stringData:
  keys.txt: {{ required "ageKey required (from sops values)" .Values.ageKey | quote }}
{{- end }}
```

- [ ] **Step 2: Render**

```bash
helmfile -e default template | grep -A 5 "name: age-key"
```

Expected: rendered Secret has `stringData["keys.txt"]` populated (multi-line content).

- [ ] **Step 3: Final lint of the whole chart**

```bash
helm lint charts/tatara-argo-workflows
helmfile -e default template > /tmp/render.yaml
kubectl apply --dry-run=client -f /tmp/render.yaml
```

Expected: lint passes; dry-run reports 7 ClusterWorkflowTemplates + 3 Secrets created.

- [ ] **Step 4: Commit**

```bash
git add charts/tatara-argo-workflows/templates/secret-age-key.yaml
git commit -m "feat: age-key Secret template (ns tatara, sops decryption key)"
```

---

## Wave 4 - Infra onboarding PR (PR-1)

### Task 4.0: Preflight - confirm infra chart supports the registry pattern

The user mentioned this pattern arrives in infra PR-975. Verify it is merged
before editing values.

- [ ] **Step 1: Check infra repo for the chart that consumes events.github.repos**

```bash
grep -r "events.github.repos\|workflows\s*$" ~/Documents/infra/helmfile/ ~/Documents/infra/charts/ 2>/dev/null | head -10
```

Expected: matches in the infra chart that templates Sensor/EventSource from
`.Values.events.github.repos`. If no matches found, PR-975 not yet merged;
stop and ask user.

- [ ] **Step 2: Check git log for the PR**

```bash
cd ~/Documents/infra && git log --oneline -20 | grep -iE "registry|github.repos|onboard|argo-events"
```

Expected: a recent commit landing the registry pattern.

If the registry pattern is not yet in main, do NOT proceed. Open a PR-975
sync or rebase task instead.

### Task 4.1: Onboard tatara ns in argo-workflows release

**Files:**
- Modify: `~/Documents/infra/helmfile/helmfiles/coding/values/argo-workflows/common.yaml`

- [ ] **Step 1: Read current file**

```bash
cat ~/Documents/infra/helmfile/helmfiles/coding/values/argo-workflows/common.yaml
```

Locate the `controller.workflowNamespaces` array. If absent, the chart defaults are in use; we will add an explicit override.

- [ ] **Step 2: Edit to add tatara**

Use the Edit tool to add or extend `controller.workflowNamespaces` to include `tatara`. Exact diff depends on current state; the final shape should be:

```yaml
controller:
  workflowNamespaces:
    - argo-workflows
    - tatara
```

- [ ] **Step 3: helmfile diff**

```bash
cd ~/Documents/infra/helmfile/helmfiles/coding
helmfile -e default diff -l name=argo-workflows
```

Expected: diff shows controller cm/deployment env change adding `tatara` to managed namespaces; or a new RoleBinding in `tatara` ns.

- [ ] **Step 4: helmfile apply**

```bash
helmfile -e default apply -l name=argo-workflows
```

Expected: SUCCESS. Verify SA appears in tatara ns:

```bash
kubectl -n tatara get sa argo-workflow rolebinding 2>&1
```

Expected: `argo-workflow` SA exists; RoleBinding exists.

- [ ] **Step 5: Commit in infra repo**

```bash
cd ~/Documents/infra
git add helmfile/helmfiles/coding/values/argo-workflows/common.yaml
git commit -m "feat: add tatara ns to argo-workflows controller workflowNamespaces"
```

### Task 4.2: Register tatara repos in argo-events

**Files:**
- Modify: `~/Documents/infra/helmfile/helmfiles/coding/values/argo-events/common.yaml`

- [ ] **Step 1: Read current file**

```bash
cat ~/Documents/infra/helmfile/helmfiles/coding/values/argo-events/common.yaml
```

The current file has `extraObjects` with inline EventSource/Sensor/WorkflowTemplate per the earlier survey. The new pattern uses `events.github.repos`.

- [ ] **Step 2: Replace inline extraObjects with registry pattern**

Use the Edit tool. The target state:

```yaml
global:
  imagePullSecrets:
    - name: regcred

crds:
  install: true
  keep: true

controller:
  rbac:
    enabled: true
    namespaced: true
    managedNamespace: argo-workflows
  replicas: 1
  resources:
    requests: { cpu: 50m, memory: 64Mi }
    limits:   { cpu: 200m, memory: 256Mi }
  podLabels:
    purpose: workflow-engine

events:
  github:
    webhookUrl: https://events.szymonrichert.pl
    webhookPath: /push
    repos:
      tatara-memory:
        owner: szymonrychu
        name: tatara-memory
        namespace: tatara
        workflows:
          push_main:    tatara-go-ci
          pull_request: tatara-go-ci
          push_tag:     tatara-memory-tag
      tatara-cli:
        owner: szymonrychu
        name: tatara-cli
        namespace: tatara
        workflows:
          push_main:    tatara-go-ci
          pull_request: tatara-go-ci
          push_tag:     tatara-go-release
```

Note: keep `extraObjects` ONLY if the agent's onboarding chart does not template EventBus/Ingress/Secret. Check first:

```bash
helm get values argo-events -n argo-workflows | head -30
```

If those still need to be in `extraObjects`, keep them. The minimum invariant: EventBus `default` exists, `github-access` Secret exists, ingress `argo-events-github-webhook` exists. If any are gone after this edit, restore them in extraObjects.

- [ ] **Step 3: helmfile diff**

```bash
cd ~/Documents/infra/helmfile/helmfiles/coding
helmfile -e default diff -l name=argo-events
```

Expected: Sensor and demo WorkflowTemplate removed; EventSource updated to include both repos; new resources generated from the registry pattern.

- [ ] **Step 4: helmfile apply**

```bash
helmfile -e default apply -l name=argo-events
```

Expected: SUCCESS.

- [ ] **Step 5: Verify resources**

```bash
kubectl -n argo-workflows get eventsource,sensor 2>&1
kubectl -n argo-workflows get eventsource github -o jsonpath='{.spec.github}' | jq .
```

Expected: one EventSource `github` with both repos; one or more Sensors dispatching to tatara ns; demo `github-push-echo` WorkflowTemplate gone.

- [ ] **Step 6: Confirm webhooks registered on github**

```bash
gh api repos/szymonrychu/tatara-memory/hooks --jq '.[].config.url'
gh api repos/szymonrychu/tatara-cli/hooks --jq '.[].config.url'
```

Expected: `https://events.szymonrichert.pl/push` appears for both. If not, the EventSource is not auto-registering; create manually:

```bash
gh api repos/szymonrychu/tatara-cli/hooks -X POST -f name=web \
  -F active=true \
  -F 'events[]=push' \
  -F 'events[]=pull_request' \
  -f config[url]=https://events.szymonrichert.pl/push \
  -f config[content_type]=json \
  -f config[secret]="$(kubectl -n argo-workflows get secret github-access -o jsonpath='{.data.secret}' | base64 -d)" \
  -f config[insecure_ssl]=0
```

(Repeat for tatara-memory if needed.)

- [ ] **Step 7: Commit in infra repo**

```bash
cd ~/Documents/infra
git add helmfile/helmfiles/coding/values/argo-events/common.yaml
git commit -m "feat: register tatara-memory and tatara-cli via events.github.repos registry"
```

---

## Wave 5 - Deploy tatara-argo-workflows (PR-2)

### Task 5.1: First apply

**Files:** none modified

- [ ] **Step 1: Final lint and template**

```bash
cd ~/Documents/tatara/tatara-argo-workflows/.worktrees/bootstrap
helm lint charts/tatara-argo-workflows
helmfile -e default template > /tmp/render.yaml
kubectl apply --dry-run=client -f /tmp/render.yaml
```

Expected: clean lint; dry-run lists 7 CWTs and 3 Secrets.

- [ ] **Step 2: Apply**

```bash
helmfile -e default apply -l name=tatara-argo-workflows
```

Expected: helm release `tatara-argo-workflows` deployed; 10 resources created.

- [ ] **Step 3: Verify cluster state**

```bash
kubectl get clusterworkflowtemplate | grep ^tatara-
kubectl -n tatara get secret harbor-robot age-key github-status-token
```

Expected: 7 CWTs listed (`tatara-github-status`, `tatara-go-ci`, `tatara-go-release`, `tatara-container-build`, `tatara-helm-publish`, `tatara-helmfile-deploy`, `tatara-memory-tag`); 3 Secrets in tatara ns.

### Task 5.2: Smoke test 1 - manual argo submit

- [ ] **Step 1: Get a real sha from tatara-cli main**

```bash
SHA=$(git -C ~/Documents/tatara/tatara-cli rev-parse main)
echo "$SHA"
```

- [ ] **Step 2: Submit go-ci workflow manually**

```bash
argo submit -n tatara \
  --from clusterworkflowtemplate/tatara-go-ci \
  --parameter repo=szymonrychu/tatara-cli \
  --parameter ref=refs/heads/main \
  --parameter sha="$SHA" \
  --serviceaccount argo-workflow \
  --wait
```

Expected: workflow `tatara-go-ci-XXXXX` runs through report-pending -> clone -> lint -> test -> report-exit. Final status `Succeeded`.

- [ ] **Step 3: Verify github commit status posted**

```bash
gh api repos/szymonrychu/tatara-cli/commits/$SHA/statuses --jq '.[] | {context, state, description}'
```

Expected: at least 2 statuses for context `tatara/ci`: one pending (older), one success.

### Task 5.3: Smoke test 2 - push trigger

- [ ] **Step 1: Make a no-op commit on tatara-cli**

```bash
cd ~/Documents/tatara/tatara-cli
git commit --allow-empty -m "chore: trigger argo CI smoke"
git push origin main
```

- [ ] **Step 2: Watch workflows**

```bash
kubectl -n tatara get wf --watch
```

Expected: within ~30s, a new workflow named `<sensor-name>-XXXXX` appears, runs through go-ci, exits Succeeded.

- [ ] **Step 3: Verify status on the empty commit**

```bash
SHA=$(git -C ~/Documents/tatara/tatara-cli rev-parse HEAD)
gh api repos/szymonrychu/tatara-cli/commits/$SHA/statuses --jq '.[] | {context, state}'
```

Expected: `tatara/ci` reaches state `success`.

### Task 5.4: Merge tatara-argo-workflows bootstrap branch

- [ ] **Step 1: Push branch and open PR**

```bash
cd ~/Documents/tatara/tatara-argo-workflows/.worktrees/bootstrap
git push -u origin feat/bootstrap-chart
gh pr create --title "feat: bootstrap chart with 7 CWTs and ns secrets" --body "Implements 2026-05-27-argo-workflows-ci-migration plan waves 0-3. Smoke verified live."
```

- [ ] **Step 2: Merge**

```bash
gh pr merge --squash --delete-branch
```

- [ ] **Step 3: Clean up worktree**

```bash
cd ~/Documents/tatara/tatara-argo-workflows
git worktree remove .worktrees/bootstrap
git fetch origin && git checkout main && git pull
```

---

## Wave 6 - Migrate tatara-cli (PR-3)

### Task 6.1: Trim .goreleaser.yaml

**Files:**
- Modify: `~/Documents/tatara/tatara-cli/.goreleaser.yaml`

- [ ] **Step 1: Read current file**

```bash
cat ~/Documents/tatara/tatara-cli/.goreleaser.yaml
```

- [ ] **Step 2: Edit**

Apply the following changes:
- In `builds:` (or top-level), set `goos: [linux, darwin]` and `goarch: [amd64]`. Drop arm64.
- In `dockers:` keep only `goos: linux`, `goarch: amd64`; drop manifest list / multi-arch sections if present.
- Delete the entire `brews:` block.

- [ ] **Step 3: Local validate**

```bash
cd ~/Documents/tatara/tatara-cli
goreleaser check
```

Expected: `loading config file: .goreleaser.yaml ... config is valid`.

- [ ] **Step 4: Commit**

```bash
git add .goreleaser.yaml
git commit -m "chore: amd64-only builds; drop Homebrew tap (deferred)"
```

### Task 6.2: Delete .github/workflows

**Files:**
- Delete: `~/Documents/tatara/tatara-cli/.github/workflows/`

- [ ] **Step 1: Remove**

```bash
cd ~/Documents/tatara/tatara-cli
git rm -r .github/workflows/
```

- [ ] **Step 2: If .github/ is now empty, remove it too**

```bash
[ -z "$(ls -A .github 2>/dev/null)" ] && rmdir .github && git add -u
```

- [ ] **Step 3: Commit**

```bash
git commit -m "chore: delete .github/workflows; CI now runs in argo"
```

### Task 6.3: Update docs

**Files:**
- Modify: `~/Documents/tatara/tatara-cli/MEMORY.md`, `~/Documents/tatara/tatara-cli/ROADMAP.md`

- [ ] **Step 1: Edit MEMORY.md**

Append:

```
2026-05-27 - CI/release migrated from .github/workflows to argo workflows. Tag push triggers tatara-go-release CWT in tatara ns (argo-events dispatch via infra registry pattern). goreleaser config trimmed to amd64-only (linux + darwin); Homebrew tap dropped (user manages binary install manually).
```

Strike the obsolete line about Wave-6 preconditions (mark as superseded).

- [ ] **Step 2: Edit ROADMAP.md**

In `v0.1.0 - phase 2 ship` section, remove the "Pushing v0.1.0 is blocked on three preconditions ..." paragraph and add:

```
**Status:** code-complete on main 2026-05-27; release pipeline migrated to argo workflows.

Pushing `v0.1.0` now triggers tatara-go-release in the tatara ns. Only
remaining precondition: a github PAT for the `github-status-token` Secret
(already provisioned by tatara-argo-workflows chart). Harbor robot also
already in place.
```

In `v0.1.1 - follow-ups`, strike the "Tighten `.golangci.yml` schema" item if you wish (now governed by the CWT's pinned `golangci-lint v2.11.4` image, not local config strictness). Keep the e2e MCP smoke item.

- [ ] **Step 3: Commit**

```bash
git add MEMORY.md ROADMAP.md
git commit -m "docs: CI/release moved to argo; update tatara-cli memory and roadmap"
```

### Task 6.4: Tag v0.1.0 - first real release through argo

- [ ] **Step 1: Push the doc commits**

```bash
cd ~/Documents/tatara/tatara-cli
git push origin main
```

Expected: webhook fires, `tatara-go-ci` runs against the doc commit, posts green status.

- [ ] **Step 2: Tag and push**

```bash
git tag -a v0.1.0 -m "v0.1.0 - first release via argo workflows"
git push origin v0.1.0
```

- [ ] **Step 3: Watch the release workflow**

```bash
kubectl -n tatara get wf --watch
```

Expected: a workflow named matching `tatara-go-release-XXXXX` (or whatever name the sensor generates) appears and proceeds.

- [ ] **Step 4: Verify release artifacts**

```bash
gh release view v0.1.0 --repo szymonrychu/tatara-cli
# Expected: release exists with linux-amd64 and darwin-amd64 tarballs + checksums.

curl -sI https://harbor.szymonrichert.pl/v2/containers/tatara-cli/manifests/v0.1.0 | grep Docker-Content-Digest
# Expected: header present (image is in harbor).
```

- [ ] **Step 5: Verify github commit status**

```bash
SHA=$(git rev-list -n 1 v0.1.0)
gh api repos/szymonrychu/tatara-cli/commits/$SHA/statuses --jq '.[] | {context, state}'
```

Expected: `tatara/release` reached `success`.

---

## Wave 7 - Migrate tatara-memory (PR-4)

### Task 7.1: Trim Makefile

**Files:**
- Modify: `~/Documents/tatara/tatara-memory/Makefile`

- [ ] **Step 1: Read Makefile**

```bash
cat ~/Documents/tatara/tatara-memory/Makefile
```

- [ ] **Step 2: Remove `push` and `chart-push` targets; add `ci` target**

Apply this diff conceptually (exact diff depends on existing content):

```makefile
.PHONY: ci
ci:
	golangci-lint run ./...
	go test -race -count=1 ./...
```

Remove:
- `.PHONY: push` and its rule
- `.PHONY: chart-push` and its rule

Keep `build`, `test`, `lint` (refactor `ci` to depend on `lint` + `test` if those exist).

- [ ] **Step 3: Verify**

```bash
cd ~/Documents/tatara/tatara-memory
make ci
```

Expected: golangci-lint and `go test -race` run; both pass.

- [ ] **Step 4: Commit**

```bash
git add Makefile
git commit -m "chore: drop push/chart-push targets; CI moved to argo workflows"
```

### Task 7.2: Update docs

**Files:**
- Modify: `~/Documents/tatara/tatara-memory/MEMORY.md`, `~/Documents/tatara/tatara-memory/ROADMAP.md`

- [ ] **Step 1: Edit MEMORY.md**

Append:

```
2026-05-27 - container build, chart publish, and homelab deploy moved to argo (tatara-memory-tag composite CWT). `make push` and `make chart-push` removed from Makefile; the only local-dev target is `make ci` mirroring the CWT lint+test steps. Tag push to v0.1.4+ triggers parallel container-build + helm-publish, then helmfile-deploy. Four github commit statuses land on the tag commit: tatara/container, tatara/chart, tatara/deploy, tatara/tag-pipeline.
```

- [ ] **Step 2: Edit ROADMAP.md**

In the v1.1 follow-ups list, strike "GitHub Actions CI" (replaced by argo) and "docker-compose integration tests" if no longer relevant; otherwise keep.

- [ ] **Step 3: Commit**

```bash
git add MEMORY.md ROADMAP.md
git commit -m "docs: container/chart/deploy now argo-driven on tag push"
```

### Task 7.3: Tag v0.1.4 to smoke

- [ ] **Step 1: Push docs**

```bash
git push origin main
```

Expected: `tatara-go-ci` runs against the docs commit, green.

- [ ] **Step 2: Tag**

```bash
git tag -a v0.1.4 -m "v0.1.4 - phase 5 wiring complete; first tag through argo"
git push origin v0.1.4
```

- [ ] **Step 3: Watch composite workflow**

```bash
kubectl -n tatara get wf --watch
```

Expected: a workflow matching `tatara-memory-tag-XXXXX` appears, container-build + helm-publish run in parallel, then helmfile-deploy.

- [ ] **Step 4: Verify artifacts and deploy**

```bash
# Image present
curl -sI https://harbor.szymonrichert.pl/v2/containers/tatara-memory/manifests/v0.1.4 | grep Docker-Content-Digest
curl -sI https://harbor.szymonrichert.pl/v2/containers/tatara-memory/manifests/0.1.4 | grep Docker-Content-Digest
# Chart present in harbor charts OCI registry
helm pull oci://harbor.szymonrichert.pl/charts/tatara-memory --version 0.1.4
# Workload running new tag
kubectl -n tatara get deploy tatara-memory -o jsonpath='{.spec.template.spec.containers[0].image}'
# Expected: contains v0.1.4 or 0.1.4
```

- [ ] **Step 5: Verify 4 status checks**

```bash
SHA=$(git rev-list -n 1 v0.1.4)
gh api repos/szymonrychu/tatara-memory/commits/$SHA/statuses --jq '.[] | {context, state}'
```

Expected: four contexts with `state=success`: `tatara/container`, `tatara/chart`, `tatara/deploy`, `tatara/tag-pipeline`.

---

## Wave 8 - Cross-repo docs

### Task 8.1: Update ~/Documents/tatara/MEMORY.md and ROADMAP.md

**Files:**
- Modify: `~/Documents/tatara/MEMORY.md`, `~/Documents/tatara/ROADMAP.md`

- [ ] **Step 1: Append to MEMORY.md**

```
- 2026-05-27 - **Phase 5 (`tatara-argo-workflows`) brought forward and shipped.** Reuses the multi-tenant `events.github.repos` registry pattern delivered by infra agent. Tatara-argo-workflows ships ClusterWorkflowTemplates and ns Secrets only - the EventSource, Sensor, EventBus stay in infra's argo-events release. Two infra files edited to onboard tatara ns + register repos.
- 2026-05-27 - **CI/release through argo workflows, not github actions.** tatara-cli .github/workflows deleted; tatara-memory Makefile push/chart-push targets removed. Tag push triggers per-step status reporting (5 contexts max per release on tatara-memory). Homebrew tap deferred indefinitely; binary release via github only.
```

- [ ] **Step 2: Update ROADMAP.md**

Change Phase 5 to `**Status:** shipped 2026-05-27`. Add summary:

```
v0.1.0 ships 7 ClusterWorkflowTemplates + 3 ns Secrets in tatara ns.
tatara-cli and tatara-memory migrated; tag push drives the full
build+publish+deploy pipeline with per-step github status checks.
```

- [ ] **Step 3: Commit**

```bash
cd ~/Documents/tatara
git add MEMORY.md ROADMAP.md
git commit -m "docs: phase 5 (argo workflows CI) shipped"
```

### Task 8.2: Verify all green and final smoke

- [ ] **Step 1: Push another no-op to each consumer**

```bash
cd ~/Documents/tatara/tatara-cli && git commit --allow-empty -m "chore: final smoke" && git push
cd ~/Documents/tatara/tatara-memory && git commit --allow-empty -m "chore: final smoke" && git push
```

- [ ] **Step 2: Verify both got green ci status within 3 minutes**

```bash
sleep 180
for repo in tatara-cli tatara-memory; do
  SHA=$(git -C ~/Documents/tatara/$repo rev-parse HEAD)
  echo "$repo @$SHA:"
  gh api repos/szymonrychu/$repo/commits/$SHA/statuses --jq '.[] | select(.context=="tatara/ci") | {state, target_url}'
done
```

Expected: both report `state: success`.

- [ ] **Step 3: Plan complete - all four PRs merged, all smokes green.**

No commit needed.

---

## Self-review checklist (controller)

Before declaring done, controller verifies:

- [ ] All 7 CWTs visible: `kubectl get cwt | grep ^tatara- | wc -l` returns 7
- [ ] All 3 Secrets in tatara ns: `kubectl -n tatara get secret harbor-robot age-key github-status-token` returns no error
- [ ] At least one `tatara-go-ci` workflow reached `Succeeded` against a real tatara-cli sha
- [ ] At least one `tatara-go-release` workflow reached `Succeeded` and produced github release + harbor image for tatara-cli v0.1.0
- [ ] At least one `tatara-memory-tag` workflow reached `Succeeded`, all 4 status checks green on the v0.1.4 commit
- [ ] tatara-cli `.github/workflows/` directory gone from main
- [ ] tatara-memory Makefile has no `push:` or `chart-push:` targets
- [ ] `~/Documents/tatara/ROADMAP.md` shows phase 5 shipped

If any check fails, return to the relevant wave and fix before moving on.
