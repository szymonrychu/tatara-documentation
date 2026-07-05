# CI Pipelines (Subsystem A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each of the five tatara Go service repos a GitHub Actions pipeline (secscan/lint/test/build/smoke on PR; same plus a kaniko image build+push to harbor on push-to-main), running on the org-scoped self-hosted ARC runner.

**Architecture:** Per-repo `.github/workflows/ci.yml` + one `.github/ci/kaniko-build.sh` that, on main only, dispatches a kaniko Kubernetes Job (kaniko self-clones the private repo via git context and pushes to `harbor.szymonrichert.pl/containers/<repo>:<sha>` and `:<git-describe-version>`). Infra prerequisites (org-scope the runner; a `tatara-ci` namespace + dispatcher SA/RBAC + a sops harbor docker-config secret) ship via the infra helmfile. Spec: `docs/superpowers/specs/2026-06-12-ci-pipelines-design.md`.

**Tech Stack:** GitHub Actions, ARC (actions-runner-controller), kaniko, kubectl, Go (setup-go/go.mod), golangci-lint v2.1.6, gitleaks v8.30.1, helmfile + sops.

---

> **As-built amendment (2026-06-12):** harbor push auth now comes from the repo
> `HARBOR_USERNAME`/`HARBOR_PASSWORD` GitHub secrets (already set on all 5 repos),
> materialized at dispatch as a transient docker-registry secret - so **U2 and the
> persistent `harbor-push-dockercfg` sops secret are eliminated**, and Task 8's MR
> dropped the secret (just ns + SA/RBAC + serviceAccountName, already merged as
> `helmfile 3472e24f`). The kaniko Job is a heredoc inside `kaniko-build.sh` (no
> separate `kaniko-job.yaml` / envsubst). The authoritative as-built description is
> the spec's "As-built status" + "Image job" sections. **U1 is the only remaining
> user step.** See `docs/superpowers/specs/2026-06-12-ci-pipelines-design.md`.

## One irreducible user step (blocker - flag, do not work around)

- **U1 (gates Task 9 / MR-B):** Grant the existing `szymonrychu-arc` GitHub App access to the org `szymonrychu` (or at least the 5 component repos) with self-hosted-runner administration. Until done, org-level runner registration fails. **The `githubConfigUrl` org change (Task 9) MUST NOT be merged before U1**, or the existing `tatara` runner deregisters and breaks.

PR pipelines (secscan/lint/test/build/smoke) and the image job both just need U1 (harbor creds are GitHub secrets, already provided).

## File structure

Per component repo (`tatara-operator`, `tatara-cli`, `tatara-memory`, `tatara-memory-repo-ingester`, `tatara-claude-code-wrapper`):
- Create `.github/workflows/ci.yml` - the pipeline (smoke job present only on cli/memory/wrapper).
- Create `.github/ci/kaniko-build.sh` - main-only kaniko Job dispatcher (identical across repos).

Infra repo (`~/Documents/infra/helmfile`), all under `helmfiles/coding/values/arc-runner-szymonrychu/`:
- Modify `common.yaml` - add `serviceAccountName` (MR-A); change `githubConfigUrl` to org (MR-B, gated on U1).
- Create `raw/tatara-ci-namespace.common.pre.yaml` - Namespace `tatara-ci`.
- Create `raw/tatara-ci-dispatcher.common.pre.yaml` - ServiceAccount `tatara-ci-dispatcher` (ns arc-runners) + Role + RoleBinding (ns tatara-ci).
- Create `raw/harbor-push-dockercfg.tatara-ci.pre.secrets.yaml` - sops Secret (dockerconfigjson) in tatara-ci.

## Phasing

- **Phase 1 (Tasks 1-3):** Build the operator pipeline + dispatcher, validate locally (actionlint/shellcheck/dry-run). Operator is the template-proving repo.
- **Phase 2 (Tasks 4-7):** Replicate to cli, memory, ingester, wrapper with per-repo deltas (parallelizable subagents).
- **Phase 3 (Tasks 8-9):** Infra MR-A (namespace/SA/RBAC/secret scaffold + serviceAccountName) and MR-B (org-scope, gated on U1).
- **Phase 4 (Task 10):** End-to-end verification on real infra after U1+U2.

Branch flow per repo: worktree off freshly-pulled `main` (per the dev-from-fresh-main rule - the autonomous bots push to these repos), land via PR. Infra ships via GitLab MR (auto-merge on pipeline).

---

## Task 1: Operator kaniko dispatcher script

**Files:**
- Create: `tatara-operator/.github/ci/kaniko-build.sh`

- [ ] **Step 1: Start from fresh main**

```bash
cd ~/Documents/tatara/tatara-operator
git checkout main && git pull --ff-only
git checkout -b ci/pipelines
mkdir -p .github/ci .github/workflows
```

- [ ] **Step 2: Write the dispatcher script**

Create `.github/ci/kaniko-build.sh`:

```bash
#!/usr/bin/env bash
# Dispatch a one-shot kaniko Job that builds this repo's image and pushes it to
# harbor. Runs on the ARC runner (in-cluster); uses the runner's mounted
# ServiceAccount for kubectl. Streams kaniko logs and propagates the Job result.
set -euo pipefail

REPO="${1:?repo name required}"          # e.g. tatara-operator
NS="tatara-ci"
SHORT_SHA="${GITHUB_SHA:0:7}"
VERSION="$(git describe --tags --always --dirty)"
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
JOB="kaniko-${REPO}-${SHORT_SHA}"
CLONE_SECRET="clone-${REPO}-${SHORT_SHA}"

cleanup() {
  kubectl -n "$NS" delete secret "$CLONE_SECRET" --ignore-not-found >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Transient clone-token secret (kaniko git-context auth for the private repo).
# Token never printed; created via stdin apply.
kubectl -n "$NS" create secret generic "$CLONE_SECRET" \
  --from-literal=username=x-access-token \
  --from-literal=token="${GITHUB_TOKEN:?GITHUB_TOKEN required}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

# Render and apply the kaniko Job.
cat <<EOF | kubectl apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: ${JOB}
  namespace: ${NS}
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 600
  activeDeadlineSeconds: 1500
  template:
    spec:
      restartPolicy: Never
      imagePullSecrets:
        - name: regcred
      containers:
        - name: kaniko
          image: harbor.szymonrichert.pl/containers/kaniko-executor:v1.24.0-debug
          command: ["/kaniko/executor"]
          args:
            - --context=git://github.com/szymonrychu/${REPO}.git#${GITHUB_SHA}
            - --dockerfile=Dockerfile
            - --destination=harbor.szymonrichert.pl/containers/${REPO}:${SHORT_SHA}
            - --destination=harbor.szymonrichert.pl/containers/${REPO}:${VERSION}
            - --build-arg=VERSION=${VERSION}
            - --build-arg=COMMIT=${SHORT_SHA}
            - --build-arg=DATE=${BUILD_DATE}
            - --compressed-caching=false
            - --cache-copy-layers=true
          env:
            - name: GIT_USERNAME
              valueFrom:
                secretKeyRef: { name: ${CLONE_SECRET}, key: username }
            - name: GIT_PASSWORD
              valueFrom:
                secretKeyRef: { name: ${CLONE_SECRET}, key: token }
          volumeMounts:
            - name: docker-config
              mountPath: /kaniko/.docker
      volumes:
        - name: docker-config
          secret:
            secretName: harbor-push-dockercfg
            items:
              - { key: .dockerconfigjson, path: config.json }
EOF

# Wait for the pod, stream kaniko logs to completion, then read the Job result.
for _ in $(seq 1 60); do
  if kubectl -n "$NS" get pod -l job-name="$JOB" -o name 2>/dev/null | grep -q .; then break; fi
  sleep 2
done
kubectl -n "$NS" logs -f "job/${JOB}" || true

for _ in $(seq 1 30); do
  succeeded="$(kubectl -n "$NS" get job "$JOB" -o jsonpath='{.status.succeeded}' 2>/dev/null || true)"
  failed="$(kubectl -n "$NS" get job "$JOB" -o jsonpath='{.status.failed}' 2>/dev/null || true)"
  if [[ "$succeeded" == "1" ]]; then echo "kaniko: build pushed"; exit 0; fi
  if [[ -n "$failed" && "$failed" != "0" ]]; then echo "kaniko: build failed"; exit 1; fi
  sleep 2
done
echo "kaniko: timed out waiting for Job result"
exit 1
```

- [ ] **Step 3: Make it executable + shellcheck**

```bash
chmod +x .github/ci/kaniko-build.sh
shellcheck .github/ci/kaniko-build.sh
```

Expected: shellcheck exits 0 (no findings). If shellcheck is absent: `mise use -g shellcheck@latest` or `brew install shellcheck`.

- [ ] **Step 4: Commit**

```bash
git add .github/ci/kaniko-build.sh
git commit -m "ci: kaniko build-and-push dispatcher"
```

---

## Task 2: Operator ci.yml workflow

**Files:**
- Create: `tatara-operator/.github/workflows/ci.yml`

Operator has **no smoke job** (needs a live cluster) and needs committed generated code (present in-repo; `make lint/test/build` run standalone). `make test` downloads envtest assets via `go run setup-envtest` (network available on the runner).

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: ci

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  secscan:
    runs-on: szymonrychu-tatara
    steps:
      - uses: actions/checkout@v4
      - name: gitleaks
        run: |
          curl -sSfL "https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz" \
            | tar -xz gitleaks
          ./gitleaks detect --source . --no-git --redact --verbose

  lint:
    runs-on: szymonrychu-tatara
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version-file: go.mod
          cache: true
      - name: gofmt
        run: test -z "$(gofmt -s -l .)" || { gofmt -s -l .; echo 'gofmt -s required'; exit 1; }
      - name: golangci-lint
        run: |
          curl -sSfL https://raw.githubusercontent.com/golangci/golangci-lint/master/install.sh \
            | sh -s -- -b "$(go env GOPATH)/bin" v2.1.6
          export PATH="$(go env GOPATH)/bin:$PATH"
          make lint

  test:
    runs-on: szymonrychu-tatara
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version-file: go.mod
          cache: true
      - name: test
        run: make test

  build:
    runs-on: szymonrychu-tatara
    needs: [secscan, lint, test]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version-file: go.mod
          cache: true
      - name: build
        run: make build

  image:
    if: ${{ github.event_name == 'push' }}
    runs-on: szymonrychu-tatara
    needs: [secscan, lint, test, build]
    env:
      GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: azure/setup-kubectl@v4
      - name: build and push image
        run: bash .github/ci/kaniko-build.sh "${{ github.event.repository.name }}"
```

- [ ] **Step 2: actionlint the workflow**

```bash
cd ~/Documents/tatara/tatara-operator
curl -sSfL https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash | bash -s -- latest /tmp 2>/dev/null
/tmp/actionlint .github/workflows/ci.yml
```

Expected: actionlint exits 0. (Shellcheck of inline `run:` scripts is included by actionlint.) Fix any reported issue before committing.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add GitHub Actions pipeline (secscan/lint/test/build + main image)"
```

---

## Task 3: Operator PR + finish branch

**Files:** none (integration step)

- [ ] **Step 1: Push the branch and open a PR**

```bash
cd ~/Documents/tatara/tatara-operator
git push -u origin ci/pipelines
gh pr create --title "ci: GitHub Actions pipeline + kaniko image build" \
  --body "Subsystem A pipeline: secscan/lint/test/build on PR; kaniko build+push to harbor on main. Requires infra MR-A/B + App grant + harbor secret (see plan 2026-06-12-ci-pipelines)."
```

- [ ] **Step 2: Note - do not expect green until infra is live**

The PR will register a `ci` check. Until U1 (App grant) + the infra MR-A merge, jobs queue with no runner. This is expected; verification happens in Task 10. Leave the PR open.

---

## Task 4: tatara-cli pipeline (has smoke)

**Files:**
- Create: `tatara-cli/.github/ci/kaniko-build.sh` (identical to Task 1 Step 2)
- Create: `tatara-cli/.github/workflows/ci.yml`

- [ ] **Step 1: Fresh main + branch + copy dispatcher**

```bash
cd ~/Documents/tatara/tatara-cli
git checkout main && git pull --ff-only
git checkout -b ci/pipelines
mkdir -p .github/ci .github/workflows
cp ~/Documents/tatara/tatara-operator/.github/ci/kaniko-build.sh .github/ci/kaniko-build.sh
chmod +x .github/ci/kaniko-build.sh
```

- [ ] **Step 2: Write ci.yml (with smoke)**

Identical to Task 2 Step 1, but insert a `smoke` job and add it to `build`/`image` `needs`. The full `smoke` job:

```yaml
  smoke:
    runs-on: szymonrychu-tatara
    needs: [build]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version-file: go.mod
          cache: true
      - name: smoke
        run: |
          make build
          ./bin/tatara --help
```

Change `build.needs` to `[secscan, lint, test]` (unchanged) and `image.needs` to `[secscan, lint, test, build, smoke]`. Insert the `smoke` job after `build`.

- [ ] **Step 3: Validate**

```bash
shellcheck .github/ci/kaniko-build.sh
/tmp/actionlint .github/workflows/ci.yml
```

Expected: both exit 0.

- [ ] **Step 4: Commit, push, PR**

```bash
git add .github/ci/kaniko-build.sh .github/workflows/ci.yml
git commit -m "ci: add GitHub Actions pipeline (secscan/lint/test/build/smoke + main image)"
git push -u origin ci/pipelines
gh pr create --title "ci: GitHub Actions pipeline + kaniko image build" \
  --body "Subsystem A pipeline. Requires infra prerequisites (see plan 2026-06-12-ci-pipelines)."
```

---

## Task 5: tatara-memory pipeline (has smoke, health-curl)

**Files:**
- Create: `tatara-memory/.github/ci/kaniko-build.sh` (identical to Task 1 Step 2)
- Create: `tatara-memory/.github/workflows/ci.yml`

- [ ] **Step 1: Fresh main + branch + copy dispatcher** (as Task 4 Step 1, in `~/Documents/tatara/tatara-memory`).

- [ ] **Step 2: Write ci.yml with the memory smoke job**

As Task 4 Step 2, but the `smoke` job step boots the server and probes `/healthz` (handler in `cmd/tatara-memory/health.go` always returns 200):

```yaml
      - name: smoke
        run: |
          make build
          ./bin/tatara-memory &
          PID=$!
          trap 'kill $PID 2>/dev/null || true' EXIT
          for i in $(seq 1 20); do
            if curl -fsS http://127.0.0.1:8080/healthz >/dev/null 2>&1; then echo healthy; exit 0; fi
            sleep 1
          done
          echo "memory did not become healthy"; exit 1
```

Implementer check: read `cmd/tatara-memory/main.go` to confirm (a) the default listen address/port (replace `8080` if different) and (b) that the server binds and serves `/healthz` without a configured backend. If main hard-requires a backend to start, downgrade the smoke to `./bin/tatara-memory --help` (exit 0) and note the downgrade in the PR body.

- [ ] **Step 3: Validate** (shellcheck + actionlint, both exit 0).

- [ ] **Step 4: Commit, push, PR** (as Task 4 Step 4).

---

## Task 6: tatara-memory-repo-ingester pipeline (no smoke, CGO)

**Files:**
- Create: `tatara-memory-repo-ingester/.github/ci/kaniko-build.sh` (identical to Task 1 Step 2)
- Create: `tatara-memory-repo-ingester/.github/workflows/ci.yml`

Ingester builds/tests with `CGO_ENABLED=1` (needs a C compiler) and has **no smoke** (needs a memory backend).

- [ ] **Step 1: Fresh main + branch + copy dispatcher** (as Task 4 Step 1, in `~/Documents/tatara/tatara-memory-repo-ingester`).

- [ ] **Step 2: Write ci.yml** = Task 2's operator workflow (no smoke), but add a gcc install step to the `test` and `build` jobs (CGO):

In both `test` and `build` jobs, before the make step:

```yaml
      - name: install gcc (CGO)
        run: sudo apt-get update && sudo apt-get install -y --no-install-recommends gcc
```

- [ ] **Step 3: Validate** (shellcheck + actionlint, both exit 0).

- [ ] **Step 4: Commit, push, PR** (as Task 4 Step 4).

---

## Task 7: tatara-claude-code-wrapper pipeline (has smoke)

**Files:**
- Create: `tatara-claude-code-wrapper/.github/ci/kaniko-build.sh` (identical to Task 1 Step 2)
- Create: `tatara-claude-code-wrapper/.github/workflows/ci.yml`

- [ ] **Step 1: Fresh main + branch + copy dispatcher** (as Task 4 Step 1, in `~/Documents/tatara/tatara-claude-code-wrapper`).

- [ ] **Step 2: Write ci.yml** = Task 4 (with smoke), smoke step:

```yaml
      - name: smoke
        run: |
          make build
          ./bin/wrapper --help
```

- [ ] **Step 3: Validate** (shellcheck + actionlint, both exit 0).

- [ ] **Step 4: Commit, push, PR** (as Task 4 Step 4).

---

## Task 8: Infra MR-A - tatara-ci namespace, dispatcher SA/RBAC, harbor secret, serviceAccountName

**Files (all under `~/Documents/infra/helmfile/helmfiles/coding/values/arc-runner-szymonrychu/`):**
- Modify: `common.yaml` (add `serviceAccountName` only - NOT the configUrl yet)
- Create: `raw/tatara-ci-namespace.common.pre.yaml`
- Create: `raw/tatara-ci-dispatcher.common.pre.yaml`
- Create: `raw/harbor-push-dockercfg.tatara-ci.pre.secrets.yaml` (sops)

- [ ] **Step 1: Fresh main + branch**

```bash
cd ~/Documents/infra/helmfile
git checkout main && git pull --ff-only
git checkout -b feat/tatara-ci-kaniko-dispatcher
```

- [ ] **Step 2: Namespace raw manifest**

Create `raw/tatara-ci-namespace.common.pre.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: tatara-ci
```

- [ ] **Step 3: Dispatcher SA + RBAC raw manifest**

Create `raw/tatara-ci-dispatcher.common.pre.yaml`:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: tatara-ci-dispatcher
  namespace: arc-runners
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: tatara-ci-dispatcher
  namespace: tatara-ci
rules:
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["create", "get", "list", "watch", "delete"]
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get"]
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["create", "get", "delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: tatara-ci-dispatcher
  namespace: tatara-ci
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: tatara-ci-dispatcher
subjects:
  - kind: ServiceAccount
    name: tatara-ci-dispatcher
    namespace: arc-runners
```

- [ ] **Step 4: Harbor docker-config sops secret scaffold**

Create the plaintext skeleton, then encrypt. Create `raw/harbor-push-dockercfg.tatara-ci.pre.secrets.yaml`:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: harbor-push-dockercfg
  namespace: tatara-ci
type: kubernetes.io/dockerconfigjson
stringData:
  .dockerconfigjson: |
    {"auths":{"harbor.szymonrichert.pl":{"username":"REPLACE","password":"REPLACE"},"https://index.docker.io/v1/":{"username":"REPLACE","password":"REPLACE"}}}
```

Then encrypt in place:

```bash
cd ~/Documents/infra/helmfile
sops -e -i helmfiles/coding/values/arc-runner-szymonrychu/raw/harbor-push-dockercfg.tatara-ci.pre.secrets.yaml
```

**U2 (user step):** the user sets the real HARBOR + DOCKERHUB creds. Tell the user to run (interactive):

```bash
cd ~/Documents/infra/helmfile
sops helmfiles/coding/values/arc-runner-szymonrychu/raw/harbor-push-dockercfg.tatara-ci.pre.secrets.yaml
```

and replace the four `REPLACE` values with the push-capable HARBOR robot creds and a DOCKERHUB account (same creds optimizarr uses). Verify the file still matches the `.sops.yaml` rule (`.*.secret.*.yaml`).

- [ ] **Step 5: Add serviceAccountName to the runner template**

In `common.yaml`, under `template.spec`, add (do NOT change `githubConfigUrl` in this MR):

```yaml
template:
  spec:
    serviceAccountName: tatara-ci-dispatcher
```

- [ ] **Step 6: Validate the helmfile renders**

```bash
cd ~/Documents/infra/helmfile
helmfile -e coding -l application=arc-controller deps >/dev/null 2>&1 || true
helmfile -e coding diff -l name=arc-runner-szymonrychu 2>&1 | head -60
```

Expected: diff shows the added `serviceAccountName`; the `raw/` manifests are applied by the presync `.hook.sh` (not in the helm diff). Confirm no template errors. Verify the `tatara-ci-dispatcher` SA lives in `arc-runners` (runner pods' namespace) so the runner template can reference it.

- [ ] **Step 7: Commit + MR (auto-merge on pipeline)**

```bash
cd ~/Documents/infra/helmfile
git add helmfiles/coding/values/arc-runner-szymonrychu/
pre-commit run --all-files || git add -A
git commit -m "feat(arc): tatara-ci namespace + kaniko dispatcher SA/RBAC + harbor push secret"
git push -u origin feat/tatara-ci-kaniko-dispatcher \
  -o merge_request.create \
  -o merge_request.title="feat(arc): tatara-ci kaniko dispatcher" \
  -o merge_request.merge_when_pipeline_succeeds \
  -o merge_request.remove_source_branch
```

Then monitor the pipeline (`glab ci status`) until merged, and `git checkout main && git pull`.

---

## Task 9: Infra MR-B - org-scope the runner (GATED on U1)

**Files:**
- Modify: `~/Documents/infra/helmfile/helmfiles/coding/values/arc-runner-szymonrychu/common.yaml`

**DO NOT START until U1 is confirmed** (the `szymonrychu-arc` App is granted org / the 5 repos). Merging this before U1 deregisters the existing runner and breaks `tatara` CI.

- [ ] **Step 1: Confirm U1 with the user.** Ask the user to confirm the App now has access to the org (or the 5 component repos). Do not proceed otherwise.

- [ ] **Step 2: Fresh main + branch**

```bash
cd ~/Documents/infra/helmfile
git checkout main && git pull --ff-only
git checkout -b feat/arc-org-scope
```

- [ ] **Step 3: Change githubConfigUrl to org**

In `common.yaml`:

```yaml
githubConfigUrl: https://github.com/szymonrychu        # was: https://github.com/szymonrychu/tatara
```

- [ ] **Step 4: Validate render**

```bash
helmfile -e coding diff -l name=arc-runner-szymonrychu 2>&1 | head -40
```

Expected: diff shows the configUrl change only.

- [ ] **Step 5: Commit + MR (auto-merge on pipeline)**

```bash
git add helmfiles/coding/values/arc-runner-szymonrychu/common.yaml
pre-commit run --all-files || git add -A
git commit -m "feat(arc): org-scope szymonrychu-tatara runner for all tatara repos"
git push -u origin feat/arc-org-scope \
  -o merge_request.create \
  -o merge_request.title="feat(arc): org-scope runner" \
  -o merge_request.merge_when_pipeline_succeeds \
  -o merge_request.remove_source_branch
```

Monitor until merged; `git checkout main && git pull`.

- [ ] **Step 6: Confirm runner registration**

```bash
kubectl -n arc-runners get autoscalingrunnerset,ephemeralrunnerset 2>/dev/null
kubectl -n arc-runners get pods
```

Expected: the runner scale set reports healthy against the org; no registration errors in `kubectl -n arc-runners logs deploy/arc-controller-gha-rs-controller` (or the controller pod).

---

## Task 10: End-to-end verification (after U1 + U2 + MR-A + MR-B)

**Files:** none (verification)

- [ ] **Step 1: Trigger the operator PR pipeline**

Push an empty commit (or re-run) on the operator `ci/pipelines` PR branch so the workflow schedules on the now-available runner:

```bash
cd ~/Documents/tatara/tatara-operator
git commit --allow-empty -m "ci: trigger pipeline"
git push
gh run watch "$(gh run list -R szymonrychu/tatara-operator --branch ci/pipelines -L1 --json databaseId -q '.[0].databaseId')" --exit-status
```

Expected: `secscan`, `lint`, `test`, `build` all succeed; `image` is skipped (PR event). If a job fails, debug via `superpowers:systematic-debugging` (read the run logs; do not guess).

- [ ] **Step 2: Merge operator PR and verify the main pipeline + harbor image**

```bash
gh pr merge -R szymonrychu/tatara-operator --squash --delete-branch <pr-number>
# watch the main run
gh run watch "$(gh run list -R szymonrychu/tatara-operator --branch main -L1 --json databaseId -q '.[0].databaseId')" --exit-status
```

Expected: main run runs `image`; kaniko Job logs stream; the run concludes `success`. Confirm the harbor tags exist:

```bash
SHA=$(git -C ~/Documents/tatara/tatara-operator rev-parse --short=7 main)
curl -fsS -u "$HARBOR_USERNAME:$HARBOR_PASSWORD" \
  "https://harbor.szymonrichert.pl/v2/containers/tatara-operator/tags/list" | grep -o "$SHA"
```

Expected: the short-SHA tag is listed (plus the git-describe version tag).

- [ ] **Step 3: Confirm the `ci` check on the main commit (subsystem B contract)**

```bash
gh api "repos/szymonrychu/tatara-operator/commits/main/check-runs" \
  -q '.check_runs[] | select(.name=="ci") | {name, conclusion}'
```

Expected: a check-run named `ci` with `conclusion: success` on the main HEAD. This is the green signal subsystem B consumes.

- [ ] **Step 4: Roll out the other 4 PRs**

Merge each of the cli/memory/ingester/wrapper PRs once their PR pipelines are green (repeat Steps 1-3 per repo). Verify each produces its harbor image and a green `ci` check on main.

- [ ] **Step 5: Update MEMORY.md / ROADMAP.md**

In `~/Documents/tatara`: note in `MEMORY.md` that the 5 Go repos now have GitHub Actions CI (kaniko-on-ARC, harbor push) and that the `ci` check on main is the subsystem-B merge-green contract. Move the CI-pipelines item out of `ROADMAP.md`. Note subsystem B (operator wait/conflict/close) is the next phase.

---

## Self-review notes

- Spec coverage: secscan(gitleaks)=Task2/secscan; static lint+gofmt=lint job; tests=test job; build gate=build job; smoke matrix=Tasks 4/5/7 (cli/memory/wrapper) + none for operator/ingester (Tasks 2/6); kaniko image+harbor push on main=Task1 script + image job; org-scope runner=Task9; tatara-ci/SA/RBAC/secret=Task8; subsystem-B contract (`ci` check on main)=Task10 Step3. All spec sections covered.
- Per-repo deltas captured: operator (no smoke, envtest in `make test`); ingester (no smoke, CGO gcc install); cli/wrapper (`--help` smoke); memory (health-curl smoke with `--help` fallback).
- Ordering hazard encoded: MR-A (Task8) safe anytime; MR-B (Task9) hard-gated on U1; image job green needs U2.
- No unit tests (CI config); verification gates are shellcheck + actionlint + helmfile diff + end-to-end pipeline runs, stated per task.
