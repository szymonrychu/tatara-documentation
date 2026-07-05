# CI Pipelines for tatara Go Services - Design

Date: 2026-06-12
Status: design + as-built (subsystem A implemented)

> **RUNNER TOPOLOGY CORRECTION (2026-06-12) - supersedes every "org-scope" /
> "U1 App grant" / uniform `runs-on: szymonrychu-tatara` mention below.**
> `szymonrychu` is a personal GitHub **account, not an org**, so an org-scoped
> runner is impossible (the org registration-token endpoint 404s; the listener
> crash-loops). The org-scope attempt was reverted. **Final design:** one
> repo-scoped `gha-runner-scale-set` per component repo, added DRY via a `range`
> in `helmfiles/coding/helmfile.yaml.gotmpl` + `values/arc-runner-shared.yaml`;
> each scale set is named after its repo (`tatara-operator`, `tatara-cli`, ...),
> and each repo's workflow uses **`runs-on: <repo-name>`** (its own
> `.github/actionlint.yaml` declares that label). All share the
> `arc-runner-szymonrychu-github` App secret (installed on every repo) and the
> `tatara-ci-dispatcher` SA/RBAC. **No remaining user step** - harbor secrets and
> the App install are already in place. MRs: `!1077` org-scope (bad) -> revert ->
> `!1080` per-repo scale sets. See MEMORY 2026-06-12.

Scope: subsystem A of the "CI pipelines + agent-wait-for-merge" feature. Subsystem
B (operator waits for its PR to merge + main CI green, resolves conflicts, closes
the associated issue) is a separate spec that consumes A's green-check contract
(see "Contract to subsystem B"). Build A first.

## Goal

Give each of the five tatara Go service repos a GitHub Actions pipeline that runs
on PR and on push-to-main: static checks, tests, compile, a feasible smoke, and -
on main only - a kaniko image build pushed to
`harbor.szymonrichert.pl/containers/<repo>:<tag>`. The main-branch run is the
authoritative "merged and built" signal subsystem B waits on.

## Repos in scope

`tatara-operator`, `tatara-cli`, `tatara-memory`, `tatara-memory-repo-ingester`,
`tatara-claude-code-wrapper`. All GitHub, all private, all Go with a Makefile +
multi-stage Dockerfile. `tatara-tasks`/`tatara-gitlab-bridge` deferred (not
cloned); `tatara-argo-workflows` excluded (no Go).

## House-style references (infra repo, GitLab CI - patterns, not syntax)

Two proven Go-service CI pipelines in the infra monorepo whose kaniko/harbor
patterns we reuse (they are GitLab CI; tatara is GitHub Actions, so we port the
patterns, not the YAML):

- `~/Documents/infra/optimizarr/.gitlab-ci.yml` - kaniko build + harbor push +
  chart publish + a `wait_for_main_pipeline` poll-with-deadline job.
- `~/Documents/infra/containers/.gitlab-ci.yml` - gitleaks security stage, kaniko.

Concretely reused:
- Kaniko image: `harbor.szymonrichert.pl/containers/kaniko-executor:v1.24.0-debug`.
- Kaniko auth: `/kaniko/.docker/config.json` built from the repo's
  `HARBOR_USERNAME`/`HARBOR_PASSWORD` (push to `containers/`). `golang` (docker.io)
  and `gcr.io/distroless` bases pull anonymously - egress is open (optimizarr
  proves it), so no registry-mirror is needed.
- Tag convention: `git describe --tags --always --dirty` plus short SHA.
- gitleaks secret scan in the static stage.
- `wait_for_main_pipeline` poll-with-deadline shape -> informs subsystem B.

## Decisions locked (from brainstorming)

- Self-hosted ARC runner, org-scoped (re-point existing scale set).
- Image build via kaniko (rootless, no daemon, no privileged), dispatched as a
  one-shot Kubernetes Job from the workflow; kaniko self-clones the private repo
  via git context using the workflow token.
- Harbor push auth comes from the repo's `HARBOR_USERNAME`/`HARBOR_PASSWORD`
  GitHub secrets (already set on all five repos), materialized at dispatch as a
  short-lived in-namespace docker-registry secret - no persistent in-cluster
  secret, no sops.
- Smoke is binary-level (kaniko has no docker daemon to `docker run`): exercise
  the compiled binary where a standalone signal exists; build-only where the
  service needs a live cluster/backend.
- Agent drives all helmfile/manifest/MR work; the one irreducible user step is the
  GitHub-App grant (U1). True blockers flagged, not worked around.

## Single irreducible user step

- **U1 (gates MR-B):** grant the existing `szymonrychu-arc` GitHub App access to
  the org `szymonrychu` (or the five component repos) with self-hosted-runner
  administration. Until done, org-level runner registration fails and the
  component-repo workflows have no runner. **The `githubConfigUrl` org change
  (MR-B) MUST NOT merge before U1**, or the existing `tatara` runner deregisters.

(Harbor creds, formerly a second user step, are already provided as the
`HARBOR_USERNAME`/`HARBOR_PASSWORD` repo secrets on all five repos.)

## Architecture

### Workflow topology

One self-contained `.github/workflows/ci.yml` per repo, named `ci` (so the
check-run name is stable for subsystem B). Per-repo copies, not a shared
`workflow_call` reusable workflow: the five repos have independent CI lifecycles
and private-repo reusable-workflow access adds cross-repo coupling for no gain.
The files are near-identical; deltas are the smoke step and whether smoke exists.
A `.github/actionlint.yaml` declares the self-hosted label `szymonrychu-tatara`.

Triggers:

```yaml
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]
```

Go version is never hardcoded - `actions/setup-go` with `go-version-file: go.mod`
reads each repo's pinned minor (operator 1.26.3, others 1.25.x). Runner:
`runs-on: szymonrychu-tatara` for every job (org-scoped after U1+MR-B).

### Jobs

| Job | Event | Needs | Action |
|-----|-------|-------|--------|
| secscan | both | - | gitleaks v8.30.1 `detect --source . --no-git --redact` |
| lint | both | - | `setup-go`; `gofmt -s -l` (fail on diff); install golangci-lint v2.1.6; `make lint` |
| test | both | - | `setup-go`; `make test` (operator envtest via its target; ingester installs gcc for CGO) |
| build | both | secscan, lint, test | `setup-go`; `make build` (ingester installs gcc) |
| smoke | both | build | binary-level check (cli/wrapper/memory only - see matrix) |
| image | push to main only | secscan, lint, test, build, smoke* | kaniko build + push to harbor (`if: github.event_name == 'push'`) |

`*` image `needs` includes `smoke` only where a smoke job exists
(cli/wrapper/memory); on operator/ingester it is `[secscan, lint, test, build]`.

### Smoke matrix (as-built)

| Repo | Binary | Smoke |
|------|--------|-------|
| tatara-cli | `bin/tatara` | `./bin/tatara --help` exit 0 |
| tatara-claude-code-wrapper | `bin/wrapper` | `./bin/wrapper --help` exit 0 |
| tatara-memory | `bin/tatara-memory` | `./bin/tatara-memory --help` (downgraded from health-probe: the server hard-requires Postgres+LightRAG and `waitForDB` blocks, so it cannot boot to serve `/healthz` in CI) |
| tatara-operator | manager | none (build-only; needs a live kube API) |
| tatara-memory-repo-ingester | ingest | none (build-only; needs a memory backend) |

### Image job - kaniko dispatch (W1)

`image` runs on the default runner (which has Node + the GitHub toolchain, so
`actions/checkout`/`setup-kubectl` work) and shells out to
`.github/ci/kaniko-build.sh` (identical across repos). That script:

1. Computes `SHORT_SHA=${GITHUB_SHA:0:7}` and `VERSION=$(git describe --tags
   --always --dirty)` (checkout uses `fetch-depth: 0` so tags resolve).
2. Creates two short-lived secrets in `arc-runners` (the runner's own namespace)
   via `kubectl create` (not apply - the dispatcher SA has no `secrets:get`):
   a clone-token secret (`x-access-token` / `${{ secrets.GITHUB_TOKEN }}`) for
   kaniko's private git-context, and a docker-registry secret built from
   `HARBOR_USERNAME`/`HARBOR_PASSWORD` for the harbor push.
3. Creates a kaniko `Job` (heredoc, no envsubst template) that self-clones via
   `--context=git://github.com/szymonrychu/<repo>#<sha>`, builds the multi-stage
   Dockerfile, and pushes `:<short-sha>` + `:<version>`. The clone token is a
   `secretKeyRef` env; the docker-config secret mounts at `/kaniko/.docker`.
4. Waits for the pod, streams `kubectl logs -f job/...`, then reads
   `.status.succeeded`/`.status.failed` and propagates the result. A `trap`
   deletes both transient secrets on exit; the Job self-GCs via
   `ttlSecondsAfterFinished: 600` (+ `activeDeadlineSeconds: 1500`).

Why git-context rather than the house "local context": GitHub-Actions-on-ARC
default mode cannot run `container: kaniko` (kaniko's image has no Node, so
`actions/checkout` can't run in it) and cannot share a checked-out workspace into
a separate kaniko pod without an RWX PVC. Kaniko self-cloning the private repo via
git context is the minimal-blast-radius equivalent and needs no scale-set surgery.
(Considered alternative: a second `containerMode: kubernetes` build scale set +
RWX work volume - rejected for the extra scale set + RWX + containerMode change.)

### Infra changes (agent-driven; one user step U1)

All edits land in `~/Documents/infra/helmfile`, ship via GitLab MR (auto-merge on
pipeline), under `helmfiles/coding/values/arc-runner-szymonrychu/`. Constraint: the
infra `.hook.sh` applies raw manifests with `kubectl apply -n <release-namespace>`
(= `arc-runners`), so every raw object MUST live in `arc-runners`; cross-namespace
objects are silently rejected (this bit the first attempt - see MEMORY 2026-06-12).

**Merged (`helmfile` main):**
- `raw/tatara-ci-dispatcher.common.pre.yaml` - ServiceAccount `tatara-ci-dispatcher`
  + least-privilege Role + RoleBinding, all in `arc-runners`: manage `batch/jobs`,
  read `pods`/`pods/log`, and `create`+`delete` `secrets` only (no `get`/`list` -
  so a build cannot read the ARC GitHub App key `arc-runner-szymonrychu-github`
  which also lives in `arc-runners`). The kaniko Job + its transient secrets run in
  `arc-runners`.
- `common.yaml` - `template.spec.serviceAccountName: tatara-ci-dispatcher` (runner
  pod dispatches kaniko Jobs) + `githubConfigUrl: https://github.com/szymonrychu`
  (org-scope, so all five repos can schedule on `szymonrychu-tatara`).

MRs: `!1076` (SA + serviceAccountName), `!1078` (RBAC moved into arc-runners +
least-privilege secrets, dropped the `tatara-ci` namespace), `!1077` (org-scope).

No in-cluster harbor secret and no sops are involved (harbor creds flow from the
repo GitHub secrets through the transient docker-registry secret).

## Contract to subsystem B

After a bot-authored PR merges, B waits for "main CI green on the merge commit". A
guarantees:

- Every push to `main` triggers exactly one workflow named `ci`, producing a
  check-run named `ci` on the merge commit SHA.
- `ci` concludes `success` only if secscan+lint+test+build+smoke+image all pass
  (image pushed to harbor). So `ci == success` on a main SHA means "merged and
  built".
- B reads this via a new SCM reader method (`GetCommitCIStatus(sha)` / main-head
  status). The poll-with-deadline shape mirrors optimizarr's
  `wait_for_main_pipeline` (bounded attempts, fixed interval).

## Testing / rollout

No local emulation (`act` cannot model the self-hosted runner + in-cluster kaniko
dispatch). Local gates are `shellcheck` (kaniko-build.sh) + `actionlint`
(workflow). End-to-end validation runs on real infra, operator first:

1. U1 granted; MR-A + MR-B merged.
2. Operator PR pipeline (secscan/lint/test/build) green; merge; main pipeline runs
   `image` and produces `harbor.szymonrichert.pl/containers/tatara-operator:<sha>`.
3. Confirm the `ci` check on the main commit (subsystem-B contract).
4. Repeat for cli/memory/ingester/wrapper.

## As-built status (2026-06-12)

- All five repos: `ci/pipelines` branch pushed with `.github/workflows/ci.yml`,
  `.github/ci/kaniko-build.sh` (`NS=arc-runners`), `.github/actionlint.yaml`
  (shellcheck + actionlint clean).
- Infra merged: `!1076` + `!1078` (dispatcher SA/RBAC in `arc-runners`,
  least-privilege secrets) + `!1077` (org-scope). U1 done (App installed for all
  repos). Runner re-registered org-scoped (`githubConfigUrl=.../szymonrychu`, rev
  8); listener authenticated to the org.
- Remaining: open the 5 PRs, verify operator end-to-end (PR pipeline green -> merge
  -> main `image` -> `ci` check on main), then roll out cli/memory/ingester/wrapper.

## Out of scope

- Subsystem B (operator wait/conflict/close) - separate spec next.
- `tatara-tasks`, `tatara-gitlab-bridge` (not cloned), `tatara-argo-workflows`.
- Chart publishing pipelines (charts ship via the infra helmfile; unchanged).
- Multi-arch images (single-arch linux/amd64; kaniko keeps that).
- Renovate / auto-tag jobs (present in the infra references; not requested here).
