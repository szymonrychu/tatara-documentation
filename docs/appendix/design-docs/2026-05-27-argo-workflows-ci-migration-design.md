# Argo Workflows CI Migration - Design

**Date:** 2026-05-27
**Phase:** 5 (`tatara-argo-workflows`), brought forward from planned to in-progress.
**Scope:** Big-bang migration of CI/CD lanes for `tatara-cli` and `tatara-memory` from GitHub Actions (and local Makefile targets) to argo workflows triggered via the existing argo-events lane in the homelab.

## Goal

One source of truth for CI/CD across the tatara platform. Push/PR/tag events on any tatara repo flow through `events.szymonrichert.pl/push` into the cluster, dispatch by `(provider, repo, event)` to a ClusterWorkflowTemplate, execute in the `tatara` namespace, and report per-step status back to github commit status.

## What is already in place (do not re-build)

- `argo-workflows` helm release (server + controller + cnpg postgres) in `argo-workflows` ns
- `argo-events` helm release (controller + EventBus default NATS) in `argo-workflows` ns
- Generic multi-tenant onboarding lane delivered by another agent:
  - `infra/.../argo-events/common.yaml` declares `events.github.repos.<name>` with `owner`, `name`, `namespace`, `workflows` map (event -> WorkflowTemplate)
  - Adding a namespace to `infra/.../argo-workflows/common.yaml` `controller.workflowNamespaces` auto-provisions `argo-workflow` SA + Role + RoleBinding + token Secret in that namespace
  - Generic Sensor in the argo-events release dispatches `(provider, repo, event)` -> the right (namespace, WorkflowTemplate)
- Cluster-role-based admin pattern for workflow SAs (deploy from CI works without extra RBAC in consumer charts)
- Webhook ingress at `https://events.szymonrichert.pl/push`, github HMAC secret in `github-access` Secret, `github-access` PAT in same secret
- Harbor robot creds (`HARBOR_ROBOT_GITLAB_USERNAME` / `_PASSWORD`) staged in `~/Documents/infra/.env`

## What this design adds

A new repo `github.com/szymonrychu/tatara-argo-workflows` whose helm chart ships the tatara application overlay:

1. Seven ClusterWorkflowTemplates (5 atomic + 1 composite + 1 helper)
2. Three sops-encrypted Secrets in `tatara` namespace

Plus three onboarding/migration PRs.

## Repo layout

```
tatara-argo-workflows/
├── CLAUDE.md (canonical copy from ~/Documents/tatara/CLAUDE.md)
├── README.md
├── ARCHITECTURE.md
├── MEMORY.md
├── ROADMAP.md
├── LICENSE
├── charts/
│   └── tatara-argo-workflows/         # `helm create`d, then edited
│       ├── Chart.yaml
│       ├── values.yaml
│       └── templates/
│           ├── _helpers.tpl
│           ├── cwt-go-ci.yaml
│           ├── cwt-go-release.yaml
│           ├── cwt-container-build.yaml
│           ├── cwt-helm-publish.yaml
│           ├── cwt-helmfile-deploy.yaml
│           ├── cwt-tatara-memory-tag.yaml
│           ├── cwt-github-status.yaml
│           ├── secret-harbor-robot.yaml
│           ├── secret-age-key.yaml
│           └── secret-github-status.yaml
├── values/
│   └── tatara-argo-workflows/
│       ├── common.yaml
│       ├── default.yaml
│       └── default.secrets.yaml       # sops-encrypted
├── helmfile.yaml.gotmpl
├── .sops.yaml
├── .gitignore
└── .pre-commit-config.yaml
```

No Go code. No Dockerfile. No subchart deps.

### Values shape

```yaml
namespace: tatara
harbor:
  registry: harbor.szymonrichert.pl
github:
  apiBase: https://api.github.com

# Scalar config only above; secret values go in default.secrets.yaml (sops).
```

`default.secrets.yaml` (sops, decrypted at helmfile-apply time):

```yaml
harborRobot:
  username: <from infra/.env HARBOR_ROBOT_GITLAB_USERNAME>
  password: <from infra/.env HARBOR_ROBOT_GITLAB_PASSWORD>
ageKey: |
  # AGE-SECRET-KEY-...
githubStatusToken: <github PAT with repo:status + contents:write on szymonrychu/tatara-*>
```

No list-shaped values, so no ConfigMap rendering needed in this chart.

## ClusterWorkflowTemplates

### Atomic templates

#### `tatara-go-ci`

Used by `push_main` and `pull_request` on Go repos.

- Params: `repo`, `ref`, `sha`, `prNumber` (optional)
- Entrypoint steps (sequential):
  1. `report-pending` -> calls `tatara-github-status` (state=pending, context=`tatara/ci`)
  2. `clone` (alpine/git): `git clone --depth=1 https://github.com/{{repo}} src && cd src && git checkout {{sha}}`
  3. `lint` (golangci/golangci-lint:v2.11.4): `golangci-lint run ./...`
  4. `test` (golang:1.25): `go test -race -count=1 ./...`
- `onExit`: `report-status` (success|failure from `{{workflow.status}}`)
- Secrets: none beyond `github-status-token`

#### `tatara-go-release`

Used by `push_tag` on tatara-cli.

- Params: `repo`, `ref`, `sha`, `tag`
- Entrypoint steps:
  1. `report-pending` (context=`tatara/release`)
  2. `clone` (full, no `--depth`, goreleaser needs tags + history)
  3. `goreleaser` (goreleaser/goreleaser:latest): `goreleaser release --clean` with env from `harbor-robot` and `github-status-token` (mapped to GITHUB_TOKEN for github release upload)
- `onExit`: report-status
- Note: tatara-cli's `.goreleaser.yaml` will be simplified to amd64-only (linux + darwin), no Homebrew tap. goreleaser itself handles: amd64 binary tarball + checksums + github release upload + amd64 docker image push to harbor.

#### `tatara-container-build`

Used by `tatara-memory-tag` composite.

- Params: `repo`, `ref`, `sha`, `tag`, `imageName`
- Entrypoint steps:
  1. `report-pending` (context=`tatara/container`)
  2. `clone`
  3. `build-push` (moby/buildkit:rootless): `buildctl-daemonless.sh build --frontend dockerfile.v0 --local context=src --local dockerfile=src --opt platform=linux/amd64 --output type=image,name=harbor.szymonrichert.pl/{{imageName}}:{{tag}},push=true,name-canonical=true`. Also pushes `{{tag-without-v}}` alias in a second invocation to permanently kill the tag-mismatch class noted in tatara/MEMORY.md.
- `onExit`: report-status
- Secrets: `harbor-robot` (mapped to DOCKER_AUTH_CONFIG via env)

#### `tatara-helm-publish`

Used by `tatara-memory-tag` composite.

- Params: `repo`, `ref`, `sha`, `tag`, `chartDir`, `chartName`
- Entrypoint steps:
  1. `report-pending` (context=`tatara/chart`)
  2. `clone`
  3. `package-push` (alpine/helm:3.x):
     - Compute `ver = tag without leading v`
     - In-place rewrite `<chartDir>/Chart.yaml`: set `version: <ver>` and `appVersion: <ver>` (KISS - one chart per release tag)
     - `helm dependency update <chartDir>`
     - `helm package <chartDir>`
     - `helm registry login -u $USERNAME -p $PASSWORD harbor.szymonrichert.pl`
     - `helm push <chartName>-<ver>.tgz oci://harbor.szymonrichert.pl/charts`
- `onExit`: report-status
- Secrets: `harbor-robot`

#### `tatara-helmfile-deploy`

Used by `tatara-memory-tag` composite.

- Params: `repo`, `ref`, `sha`, `tag`, `helmfileDir` (default `.`)
- Entrypoint steps:
  1. `report-pending` (context=`tatara/deploy`)
  2. `clone`
  3. `sync` (ghcr.io/helmfile/helmfile:v0.x):
     - Mount `age-key` Secret as `~/.config/sops/age/keys.txt`
     - `helm registry login` for harbor
     - `helmfile -e default sync --args "--force-conflicts"`
- `onExit`: report-status
- ServiceAccount: `argo-workflow` (auto-provisioned in `tatara` ns by infra; has cluster-admin-equivalent via the agent's pattern)
- Secrets: `harbor-robot`, `age-key`

### Composite template

#### `tatara-memory-tag`

DAG entry for `push_tag` on tatara-memory.

- Params: `repo`, `ref`, `sha`, `tag`
- DAG:
  ```
  container-build ─┐
                    ├─> helmfile-deploy
  helm-publish ─────┘
  ```
  - `container-build`: `workflowTemplateRef: tatara-container-build` with `imageName=containers/tatara-memory`
  - `helm-publish`: `workflowTemplateRef: tatara-helm-publish` with `chartDir=charts/tatara-memory`, `chartName=tatara-memory`
  - `helmfile-deploy`: depends on both predecessors; `workflowTemplateRef: tatara-helmfile-deploy` with `helmfileDir=.`
- `onExit`: aggregate status (success only if all three children succeeded) -> context=`tatara/tag-pipeline`

No `tatara-cli-tag` composite: goreleaser handles release + container in one step.

### Helper template

#### `tatara-github-status`

Called from every CWT as both a pre-step (state=pending) and via `onExit` (state=success|failure|error).

- Params: `repo`, `sha`, `state`, `context`, `description`
- Container: alpine:3.20 (curl pre-installed via apk)
- Body: a single sh script that maps argo status to github API status (`Succeeded->success`, `Failed->failure`, `Error->error`, `pending->pending`) and POSTs:
  ```
  POST https://api.github.com/repos/{{repo}}/statuses/{{sha}}
  Authorization: token $GITHUB_TOKEN
  Body: {"state": <mapped>, "context": "{{context}}", "description": "{{description}}", "target_url": "https://workflows.szymonrichert.pl/workflows/{{workflow.namespace}}/{{workflow.name}}"}
  ```
- Secrets: `github-status-token` mounted as env GITHUB_TOKEN

## Secrets

All three live in `tatara` namespace, rendered by tatara-argo-workflows chart from sops-encrypted values.

| Secret name | Keys | Used by |
|---|---|---|
| `harbor-robot` | `username`, `password` | go-release, container-build, helm-publish, helmfile-deploy |
| `age-key` | `keys.txt` | helmfile-deploy |
| `github-status-token` | `GITHUB_TOKEN` | github-status helper + go-release |

No `tap-pat` (Homebrew dropped). No cluster-scoped secrets.

## RBAC

Out of scope for this design. The agent's pattern (cluster-role-based admin for workflow SAs) is already in place. The `argo-workflow` SA in `tatara` ns will be auto-provisioned by the infra chart when `tatara` is added to `controller.workflowNamespaces`, and will inherit the cluster-admin-equivalent binding.

## Network policy

Out of scope. Cluster CNI is flannel; NetworkPolicy not enforced.

## Status reporting strategy

Per-step granular status on github commits. A tagged tatara-memory commit shows 4 status checks: `tatara/container`, `tatara/chart`, `tatara/deploy`, `tatara/tag-pipeline` (aggregate). PRs and main pushes show one check (`tatara/ci`). Each check links to its argo workflow run via `target_url`.

State mapping:
- pending: posted at workflow start (first step in entrypoint)
- success: `workflow.status == Succeeded`
- failure: `workflow.status == Failed`
- error: `workflow.status == Error`

## Cutover plan

Three (optionally four) PRs land in order. Each is independently apply-able.

### PR-1: infra repo - onboard tatara

Two files edited in `~/Documents/infra/helmfile/helmfiles/coding/values/`:

`argo-workflows/common.yaml`:
```yaml
controller:
  workflowNamespaces:
    - argo-workflows
    - tatara
```

`argo-events/common.yaml` (replacing the inline-extraObjects pattern with the registry pattern):
```yaml
events:
  github:
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

Apply: `helmfile -e default apply -l name=argo-workflows` then `... -l name=argo-events`.

Result after apply:
- `argo-workflow` SA + Role + RoleBinding + token Secret auto-created in `tatara` ns
- EventSource lists tatara-memory and tatara-cli; webhook auto-registered with github via the PAT in `github-access`
- Demo `github-push-echo` WorkflowTemplate gone (replaced by tatara CWTs once PR-2 lands)
- Generic Sensor routes events to `tatara` ns by registry config

Brief webhook gap (~30s) during apply. No live consumers yet, safe.

### PR-2: tatara-argo-workflows bootstrap + first apply

1. `gh repo create szymonrychu/tatara-argo-workflows --public`
2. Bootstrap repo locally with the layout above
3. Pull `HARBOR_ROBOT_GITLAB_USERNAME` / `_PASSWORD` from `~/Documents/infra/.env`. Create new PAT (`github-status-token`) with `repo:status` + `contents:write` scopes on `szymonrychu/tatara-*`. Encrypt into `default.secrets.yaml` with the user's sops/age key.
4. `helm lint charts/tatara-argo-workflows`
5. `helmfile -e default apply -l name=tatara-argo-workflows`

Result: 7 ClusterWorkflowTemplates visible cluster-wide; 3 Secrets in `tatara` ns.

Smoke test 1 (manual submit):
```
argo submit -n tatara \
  --from clusterworkflowtemplate/tatara-go-ci \
  --parameter repo=szymonrychu/tatara-cli \
  --parameter ref=refs/heads/main \
  --parameter sha=$(git -C ~/Documents/tatara/tatara-cli rev-parse HEAD)
```

Expected: workflow succeeds, github commit status `tatara/ci` posts both pending and success to the HEAD commit on tatara-cli.

Smoke test 2 (push trigger):
- Push a no-op commit to tatara-cli main. Verify github webhook fires, Sensor submits `tatara-go-ci` workflow in `tatara` ns, status posts.

### PR-3: tatara-cli - delete GHA + adjust goreleaser

In `~/Documents/tatara/tatara-cli`:
- `rm -rf .github/workflows/`
- Edit `.goreleaser.yaml`:
  - `builds[].goos: [linux, darwin]`, `builds[].goarch: [amd64]`
  - Drop `brews:` block entirely
  - `dockers[].goos: linux`, `dockers[].goarch: amd64` (single arch)
- Update `MEMORY.md`: note that Wave-6 preconditions collapsed; only one remaining (harbor robot in tatara ns), now satisfied by tatara-argo-workflows chart
- Update `ROADMAP.md`: strike v0.1.1 follow-up "tighten golangci-lint v2.12+ strict verify" since CI now runs from `tatara-go-ci` CWT (the new CWT can use whatever golangci-lint version it wants without v1-leftover config tolerance), or keep as nice-to-have

Smoke: `git tag v0.1.0 && git push origin v0.1.0`. Argo `tatara-go-release` fires, goreleaser builds darwin-amd64 + linux-amd64 binaries, publishes github release with archives, pushes amd64 container to harbor.

### PR-4 (optional follow-up): tatara-memory - drop Makefile push targets

In `~/Documents/tatara/tatara-memory`:
- `Makefile`: remove `push` and `chart-push` targets; keep `build` for local dev. Add `ci` target mirroring `tatara-go-ci` steps for local verification.
- `MEMORY.md`: strike v1.1 follow-up "GitHub Actions CI" (replaced by argo)
- `ROADMAP.md`: move container/chart push from v1.1 follow-up to phase-5 shipped

Smoke: `git tag v0.1.4 && git push origin v0.1.4`. Argo `tatara-memory-tag` composite fires:
- `tatara-container-build` and `tatara-helm-publish` run in parallel
- After both succeed, `tatara-helmfile-deploy` syncs the `tatara` ns helm release
- 4 status checks land on the v0.1.4 commit: `tatara/container`, `tatara/chart`, `tatara/deploy`, `tatara/tag-pipeline`

## Order of execution

```
PR-1 (infra)            -> apply
PR-2 (tatara-argo-workflows) -> apply + smoke
PR-3 (tatara-cli)
PR-4 (tatara-memory, optional follow-up)
```

PR-1 must apply before PR-2 (otherwise the Sensor dispatches to undefined CWTs). PR-3 and PR-4 require PR-2's CWTs to be cluster-visible. PR-3 and PR-4 are mutually independent.

## Risks and known limitations

- **Webhook auto-registration relies on infra's existing PAT having `admin:repo_hook` scope.** If it lacks the scope, the chart logs an error and webhooks must be added manually via github UI. Verify scope before PR-1 apply.
- **Apply window between PR-1 and PR-2.** Once PR-1 applies, the Sensor's registry references CWTs (`tatara-go-ci`, `tatara-memory-tag`, `tatara-go-release`) that do not exist until PR-2 applies. Any push/PR to tatara-* during this window fails the workflow submission with a CWT-not-found error. Mitigation: apply PR-1 immediately followed by PR-2 (minutes, not hours); do not push to tatara repos in between. github webhook events that arrive during the gap are dropped silently by argo-events (no replay).
- **Per-namespace ServiceAccount.** CWTs must NOT pin `spec.serviceAccountName: argo-admin`. Leave it unset so the agent-provisioned `argo-workflow` SA in the executing namespace is used. The demo `github-push-echo` WorkflowTemplate used `argo-admin` (cluster-scoped); copying that pattern into the new CWTs would break the per-namespace isolation.
- **First apply of tatara-argo-workflows is manual** (`helmfile apply` from a cloned worktree). A future v0.1.1 of tatara-argo-workflows can register itself in the events.github.repos registry, making subsequent updates argo-driven on tag. Chicken-and-egg solved by manual bootstrap once.
- **buildkit rootless** needs an unconfined seccomp profile (or RuntimeDefault depending on host kernel). If buildkit fails to start in `tatara` ns, the workaround is to mark the pod's seccomp policy `Unconfined` in the CWT step spec. This is a known argo-workflows gotcha.
- **Helmfile-deploy from CI deploys main on every tag.** A tagged commit is presumed-good. If a tag points at a broken commit, helmfile-deploy rolls out the breakage. Mitigation: tag from main only, never tag a feature branch. Documented in CLAUDE.md branch flow already.
- **No GitHub PR check requirement enforcement.** github branch protection cannot require argo-driven checks without those checks first running on at least one PR. Bootstrap path: land PR-1 + PR-2 + PR-3, observe the `tatara/ci` check appearing on a tatara-cli PR, then enable branch protection requiring `tatara/ci` to be green.
- **No retry-on-flake.** A flaky test fails the workflow; user must re-tag or re-run via `argo retry` manually. Acceptable for a one-person homelab.
- **No caching layer.** `go mod download` and `go build` run from scratch on every workflow. Workflow durations: estimated 90-180s for go-ci, 60-120s for container-build, 30-60s for helm-publish, 60-90s for helmfile-deploy. Caching is a v0.1.1 follow-up via PVC-backed `~/go/pkg/mod`.

## Out of scope

- Phase 7 (`tatara-gitlab-bridge`). Gitlab is a future provider; this design is github-only.
- Phase 6 (`tatara-tasks`) onboarding. It will follow the same registry pattern when it lands.
- Multi-environment deploys (dev/stage/prod). Helmfile-deploy currently targets `-e default` only.
- ARM64 builds. Drop for now; user can revisit if a target needs it.
- Workflow caching (go mod cache PVC). v0.1.1.
- Release-on-merge instead of release-on-tag. Tag-driven is the current model and stays.
