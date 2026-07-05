# Enroll tatara (dogfood) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Fix the ingester + wrapper image gaps and enroll the tatara platform as the first real operator `Project`.

**Architecture:** Three image builds (ingester runtime base swap; tatara-cli container; wrapper bundling cli v0.4.0) then enrollment objects (SCM secret + Project + Repository CRs + GitHub webhooks). Builds publish to Harbor; enrollment applies to the live `tatara` namespace.

**Tech Stack:** docker buildx, Harbor OCI, helmfile, kubectl, cnpg, GitHub.

Spec: `docs/superpowers/specs/2026-06-07-enroll-tatara-dogfood-design.md`. All builds are linux/amd64 (homelab nodes). Use `mise` for helm/helmfile; local docker daemon for builds (already logged in to harbor).

---

## Task E1: Ingester runtime image (git + go toolchain)

**Files:**
- Modify: `tatara-memory-repo-ingester/Dockerfile` (final stage only)

- [ ] **Step 1: Swap the final stage base.** In `Dockerfile`, change the final runtime stage from `FROM gcr.io/distroless/cc-debian12:nonroot` to `FROM golang:1.26-bookworm`. Keep the builder stage and the `COPY --from=builder` of the `tatara-ingest` binary and the existing `ENTRYPOINT`/`USER` as-is, EXCEPT: golang:bookworm has no `nonroot` user, so drop any `USER nonroot` line in the final stage (the ingest Job pod securityContext sets runAsUser). Ensure `git` and `go` are on PATH (they are in golang:bookworm). Set `ENV GOTOOLCHAIN=auto` in the final stage so it can build repos pinned to a newer Go.

- [ ] **Step 2: Build + smoke-test locally.**
```bash
cd ~/Documents/tatara/tatara-memory-repo-ingester
COMMIT=$(git rev-parse --short HEAD); DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
docker buildx build --platform=linux/amd64 --build-arg VERSION=0.2.0 \
  --build-arg COMMIT=$COMMIT --build-arg DATE=$DATE \
  -t harbor.szymonrichert.pl/containers/tatara-memory-repo-ingester:0.2.0 --load .
docker run --rm --entrypoint sh harbor.szymonrichert.pl/containers/tatara-memory-repo-ingester:0.2.0 \
  -c 'git --version && go version && tatara-ingest --help >/dev/null 2>&1; echo ingest-exit=$?'
```
Expected: prints a git version, a go version, and `ingest-exit=` (0 or a flag-usage code, not 127/command-not-found).

- [ ] **Step 3: Push.**
```bash
docker buildx build --platform=linux/amd64 --build-arg VERSION=0.2.0 \
  --build-arg COMMIT=$COMMIT --build-arg DATE=$DATE \
  -t harbor.szymonrichert.pl/containers/tatara-memory-repo-ingester:0.2.0 --push .
```
Expected: `naming to ...:0.2.0 done`.

- [ ] **Step 4: Commit the Dockerfile change** (local; no push of the repo unless asked).
```bash
git add Dockerfile && git commit -m "fix: ingester runtime image has git + go toolchain (clone + type-resolved Go)"
```

- [ ] **Step 5: Bump the operator's ingester image pin + re-apply.**
```bash
cd ~/Documents/infra/helmfile
sed -i '' 's#tatara-memory-repo-ingester:0.1.0#tatara-memory-repo-ingester:0.2.0#' helmfiles/tatara/values/tatara-operator/default.yaml
git add helmfiles/tatara/values/tatara-operator/default.yaml && git commit -m "chore(tatara): ingesterImage 0.2.0 (git+go runtime)"
mise exec -- helmfile -e default -l application=tatara-operator apply
kubectl -n tatara get cm tatara-operator -o jsonpath='{.data.INGESTER_IMAGE}{"\n"}'
```
Expected: ConfigMap `INGESTER_IMAGE` shows `...:0.2.0`.

---

## Task E2: tatara-cli + wrapper images

**Files:** none (builds only).

- [x] **Step 1: Build + push tatara-cli:0.4.0.**
```bash
cd ~/Documents/tatara/tatara-cli
git checkout v0.4.0 2>/dev/null || git checkout main
COMMIT=$(git rev-parse --short HEAD); DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
docker buildx build --platform=linux/amd64 --build-arg VERSION=0.4.0 \
  --build-arg COMMIT=$COMMIT --build-arg DATE=$DATE \
  -t harbor.szymonrichert.pl/containers/tatara-cli:0.4.0 --push .
git checkout main 2>/dev/null || true
```
Expected: `naming to ...tatara-cli:0.4.0 done`. If the build fails on `COPY go.mod` missing go.sum, add `go.sum` to that COPY line in the cli Dockerfile, commit it, and retry (note the fix).

- [x] **Step 2: Verify the cli image carries the operator tools.**
```bash
docker run --rm --entrypoint sh harbor.szymonrichert.pl/containers/tatara-cli:0.4.0 \
  -c 'tatara mcp --help 2>&1 | head -1; tatara version 2>/dev/null || true'
```
Expected: runs without command-not-found (binary at `/usr/local/bin/tatara`).

- [x] **Step 3: Build + push the wrapper bundling cli 0.4.0.**
```bash
cd ~/Documents/tatara/tatara-claude-code-wrapper
COMMIT=$(git rev-parse --short HEAD); DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
docker buildx build --platform=linux/amd64 \
  --build-arg VERSION=0.1.0 --build-arg COMMIT=$COMMIT --build-arg DATE=$DATE \
  --build-arg TATARA_CLI_VERSION=0.4.0 \
  -t harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper:0.1.0 --push .
```
Expected: `naming to ...tatara-claude-code-wrapper:0.1.0 done`. If the go-build stage fails because go.mod requires a newer Go than `GO_VERSION=1.25`, add `--build-arg GO_VERSION=1.26` and retry (note it).

- [x] **Step 4: Verify the wrapper bundles tatara + claude + wrapper.**
```bash
docker run --rm --entrypoint sh harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper:0.1.0 \
  -c 'command -v tatara; command -v claude; command -v wrapper; command -v git'
```
Expected: all four paths printed.

---

## Task E3: Enrollment (needs the GitHub PAT)

**Files:**
- Create: `~/Documents/tatara/tatara-operator/deploy-samples/tatara-project.yaml` (the Project + Repository manifests, for reproducibility; gitignored-safe, no secrets)

- [ ] **Step 1: STOP - request the GitHub PAT from the human.** The `token` value cannot be invented. Ask the human to provide a GitHub PAT (scopes: `repo`, `admin:repo_hook`) for `szymonrychu/*`, OR to run the `kubectl create secret` themselves. Do not proceed to Step 2 until the token is available (as an env var the agent never prints, or human-created).

- [ ] **Step 2: Create the SCM secret (agent-safe, value never printed).**
```bash
# token provided out-of-band in $GH_PAT (exported by the human / op_env); webhookSecret generated
WHS=$(openssl rand -hex 24)
kubectl -n tatara create secret generic tatara-scm \
  --from-literal=token="$GH_PAT" --from-literal=webhookSecret="$WHS" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n tatara get secret tatara-scm -o go-template='{{range $k,$v := .data}}{{$k}} {{end}}{{"\n"}}'
echo "$WHS" > /tmp/tatara-webhook-secret   # for the webhook registration step; not committed
```
Expected: secret has keys `token webhookSecret`. (Never echo the values.)

- [ ] **Step 3: Apply the Project + Repository CRs.** Write `deploy-samples/tatara-project.yaml`:
```yaml
apiVersion: tatara.dev/v1alpha1
kind: Project
metadata: {name: tatara, namespace: tatara}
spec:
  scmSecretRef: tatara-scm
  triggerLabel: tatara
  maxConcurrentTasks: 2
  agent:
    image: harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper:0.1.0
    model: claude-opus-4-8
    permissionMode: bypassPermissions
  memory: {pgInstances: 1, pgStorage: 10Gi, neo4jStorage: 10Gi}
---
apiVersion: tatara.dev/v1alpha1
kind: Repository
metadata: {name: tatara-memory, namespace: tatara}
spec: {projectRef: tatara, url: "https://github.com/szymonrychu/tatara-memory", defaultBranch: main}
---
apiVersion: tatara.dev/v1alpha1
kind: Repository
metadata: {name: tatara-cli, namespace: tatara}
spec: {projectRef: tatara, url: "https://github.com/szymonrychu/tatara-cli", defaultBranch: main}
---
apiVersion: tatara.dev/v1alpha1
kind: Repository
metadata: {name: tatara-operator, namespace: tatara}
spec: {projectRef: tatara, url: "https://github.com/szymonrychu/tatara-operator", defaultBranch: main}
---
apiVersion: tatara.dev/v1alpha1
kind: Repository
metadata: {name: tatara-chat, namespace: tatara}
spec: {projectRef: tatara, url: "https://github.com/szymonrychu/tatara-chat", defaultBranch: main}
---
apiVersion: tatara.dev/v1alpha1
kind: Repository
metadata: {name: tatara-memory-repo-ingester, namespace: tatara}
spec: {projectRef: tatara, url: "https://github.com/szymonrychu/tatara-memory-repo-ingester", defaultBranch: main}
---
apiVersion: tatara.dev/v1alpha1
kind: Repository
metadata: {name: tatara-claude-code-wrapper, namespace: tatara}
spec: {projectRef: tatara, url: "https://github.com/szymonrychu/tatara-claude-code-wrapper", defaultBranch: main}
```
```bash
kubectl apply -f ~/Documents/tatara/tatara-operator/deploy-samples/tatara-project.yaml
```
Expected: project + 6 repositories created.

- [ ] **Step 4: Wait for the Project memory stack to be Ready** (the operator provisions cnpg+neo4j+lightrag+memory; Repositories gate on it).
```bash
for i in $(seq 1 15); do sleep 20; ph=$(kubectl -n tatara get project tatara -o jsonpath='{.status.memory.phase}'); echo "phase=$ph"; [ "$ph" = Ready ] && break; done
```
Expected: `phase=Ready`.

- [ ] **Step 5: Verify ingest reaches the graph.** Each Repository should launch an ingest Job and reach `phase=Ingested`.
```bash
kubectl -n tatara get repository -o custom-columns=NAME:.metadata.name,PHASE:.status.phase,SHA:.status.lastIngestedCommit
kubectl -n tatara get jobs -l app.kubernetes.io/component=ingest
# code-graph non-empty in the project's postgres:
POD=$(kubectl -n tatara get pods -l cnpg.io/cluster=mem-tatara-pg -o name | head -1)
kubectl -n tatara exec "$POD" -c postgres -- psql -U tatara_memory -d tatara_memory -tAc \
  'select count(*) from code_entities;' 2>/dev/null
```
Expected: repositories progress to `Ingested` with a SHA; `code_entities` count > 0. (First real ingest may surface edge cases - debug per systematic-debugging; the ingest Job logs are the source of truth.)

- [ ] **Step 6: Register GitHub webhooks per repo** (push + issues -> the operator).
```bash
WHS=$(cat /tmp/tatara-webhook-secret); URL="https://tatara.szymonrichert.pl/operator/webhooks/tatara"
for r in tatara-memory tatara-cli tatara-operator tatara-chat tatara-memory-repo-ingester tatara-claude-code-wrapper; do
  gh api -X POST "repos/szymonrychu/$r/hooks" -f name=web -F active=true \
    -f events[]=push -f events[]=issues \
    -f config[url]="$URL" -f config[content_type]=json -f config[secret]="$WHS" -f config[insecure_ssl]=0 \
    && echo "hook: $r" || echo "hook FAILED (may already exist): $r"
done
rm -f /tmp/tatara-webhook-secret
```
Expected: a hook per repo (or "already exists"). A push to `main` then triggers an incremental re-ingest; a `tatara`-labelled issue creates a Task.

- [ ] **Step 7: Commit the sample manifest** (no secrets in it).
```bash
cd ~/Documents/tatara/tatara-operator && git add deploy-samples/tatara-project.yaml && git commit -m "docs: tatara dogfood Project + Repository sample manifests"
```

---

## Notes
- E1 and E2 are independent and run in parallel (different repos). E3 is sequential after both and needs the human's PAT.
- Image pushes are outward (Harbor) but within the authorized deploy scope.
- The CRD `spec.memory`/`spec.agent` already exist on the live cluster (applied in the per-project-memory deploy).
