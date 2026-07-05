# infra-helmfile: tatara-helmfile Deploy Runner + tatara Bucket Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** In the existing GitLab `~/Documents/infra/helmfile` repo, add a dedicated in-cluster ARC runner scale set (`arc-runner-tatara-helmfile`) that runs as a new cluster-admin-bound ServiceAccount for autonomous `helmfile apply` from the new `tatara-helmfile` GitHub repo, and fully remove the `helmfiles/tatara/` bucket (reversing the 2026-06-05 consolidation), via one GitLab MR off `main`.

**Architecture:** Two independent workstreams in a single worktree. (A) Add a new release in `helmfiles/coding/helmfile.yaml.gotmpl` mirroring the per-repo runner pattern but with its own values file (dedicated SA `tatara-helmfile-deployer`, not the shared `tatara-ci-dispatcher` kaniko SA) plus a raw pre-manifest creating that SA + a cluster-admin ClusterRoleBinding (modelled on the `cluster_cleanup` precedent). (B) Delete `helmfiles/tatara/`, drop its include from the root `helmfile.yaml.gotmpl`, confirm `.parse.py` regenerates valid CI with no dangling `needs`, and record the reversal in `MEMORY.md`. Tools on the runner come from the standard `ghcr.io/actions/actions-runner` image plus a `mise install` bootstrap in the `tatara-helmfile` workflows (decided below).

**Tech Stack:** Helmfile 1.5.3 (gotmpl), Helm 4.2.0, ARC `gha-runner-scale-set` 0.14.2, raw Kubernetes RBAC manifests applied via the repo `.hook.sh` presync, GitLab CI dynamic-pipeline generator (`.parse.py`, pydantic), sops PGP, pre-commit (yamllint/gitleaks/check-yaml).

---

## Deferred-question resolution: runner image + tool delivery

The spec (Sub-system B) defers: "use the same runner image as other tatara runners + mise bootstrap, or a dedicated image. Decide in plan."

**Decision: standard `ghcr.io/actions/actions-runner:2.335.1` image (identical to every other per-repo tatara runner) + `mise` bootstrap inside the `tatara-helmfile` workflows.** No dedicated runner image, no initContainer.

Rationale, grounded in the repo:
- The base `ghcr.io/actions/actions-runner` image ships `git`, `curl`, `jq`, `sudo` and `apt` but NOT `helm`/`helmfile`/`kubectl`/`sops`/`helm-secrets`/`helm-diff`. The existing component CI proves this: `tatara-operator/.github/workflows/ci.yml` pulls each tool in-job (`actions/setup-go@v5`, `azure/setup-kubectl@v4`, `azure/setup-helm@v4`, `curl ... gitleaks`). So the runner image is a thin host, tools come from the workflow.
- The infra GitLab CI's own helmfile pattern is `mise install` first (`.parse.py` `HELMFILE_BEFORE_SCRIPT[0] == "mise install"`), reading a pinned `.mise.toml`. `tatara-helmfile` will carry an identical `.mise.toml` (helm 4.2.0, helmfile 1.5.3, kubectl 1.36.1, sops 3.13.1, helm-secrets 4.7.4, helm-diff per the design spec). Bootstrapping with `mise` reuses that exact pinning and the helm-secrets/helm-diff plugin install hooks already encoded in `.mise.toml`'s `[hooks].postinstall`.
- Net: tool delivery is `tatara-helmfile`'s problem (PLAN 1's `diff.yaml`/`apply.yaml`), NOT this repo's. **This repo (PLAN 2) only declares the runner pod + its SA/RBAC.** The runner image stays the cluster-standard `ghcr.io/actions/actions-runner:2.335.1`. This task list therefore does NOT build any image and does NOT add tool-install steps; it hands the `mise install` requirement to PLAN 1 as a cross-plan note (see "Handoff to PLAN 1").

This keeps the runner declaration a verbatim mirror of the existing per-repo runners (KISS, no new image to maintain, no tech-debt).

---

## Orphaned-Repository-CR audit (CRITICAL handoff to PLAN 1)

The spec (Sub-system C) requires auditing `helmfiles/tatara/values/tatara-operator/raw/` for any `Repository` CRs that removal would orphan.

**Audit result (verified before writing this plan):**
- `find helmfiles/tatara -type f` returns exactly one raw manifest: `values/tatara-operator/raw/project-tatara.tatara-operator.pre.yaml`.
- `grep -rl "kind: Repository" helmfiles/` over the whole infra repo returns **NONE**.
- `grep -rn "tatara.dev" helmfiles/` returns only the single `project-tatara...pre.yaml` (a `kind: Project`, `apiVersion: tatara.dev/v1alpha1`).

**Conclusion: ZERO `Repository` CRs exist in the infra helmfile** - but they ARE required by the operator and DO exist live. Corrected picture (verified against the live cluster, `kubectl get repositories.tatara.dev -n tatara`):
- The operator's per-repo fan-out scans + scheduled re-ingest are driven by `Repository` CRs (`projectReposForScan` lists them; `tatara-operator/internal/agent/pod.go` `BuildPod` builds `TATARA_REPOS` from the Project's `Repository` list). Discovery is NOT owner-only.
- The live cluster has **6 `Repository` CRs** (tatara-memory, tatara-cli, tatara-operator, tatara-chat, tatara-memory-repo-ingester, tatara-claude-code-wrapper), all `phase: Ingested`, `reingestSchedule: "0 6 * * *"`, `semanticIngest: true`. They were applied ad-hoc via `kubectl apply -f tatara-operator/deploy-samples/tatara-project.yaml`, never under GitOps - which is the ONLY reason infra has none to "orphan."
- Removing the infra bucket does NOT delete these live `Repository` CRs (they are raw kubectl objects, not helm-release resources; `helmfile apply` does not prune them). **Do NOT `kubectl delete` them.**

This is the critical handoff: **PLAN 1 carries the `Project` CR `tatara` (full body below) AND brings all 6 live `Repository` CRs + a new `tatara-helmfile` self-enroll CR under `tatara-helmfile` GitOps** (PLAN 1 Task 6, specs matched to the live cluster). After PLAN 1's first apply, `tatara-helmfile` is the sole declarative source for the Project + all 7 Repository CRs.

## Cross-plan sequencing (HUMAN-GATED merge order)

This infra-removal MR MUST be merged ONLY AFTER `tatara-helmfile` is live and its first `apply` is green (it adopts the two releases and re-applies the Project + Repository CRs). Removing the bucket stops infra from managing `tatara-chat`/`tatara-operator` and from re-applying the Project; `helmfile apply` will not prune the still-running releases, but adopt-first eliminates any management gap. **Merge order: (1) tatara-helmfile repo created + first apply green, then (2) merge this MR.** The deploy-runner workstream (A) has no such ordering constraint and can merge anytime (it only adds a runner + RBAC; nothing in tatara-helmfile works until that runner exists, so ideally merge A first).

`helmfiles/tatara/values/tatara-operator/raw/project-tatara.tatara-operator.pre.yaml` (verbatim, the sole orphan-candidate):

```yaml
apiVersion: tatara.dev/v1alpha1
kind: Project
metadata:
  name: tatara
  namespace: tatara
spec:
  agent:
    image: harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper:a7eab9b
    maxTurnsPerTask: 50
    model: claude-opus-4-8
    permissionMode: bypassPermissions
    turnTimeoutSeconds: 1800
  maxConcurrentTasks: 3
  memory:
    neo4jStorage: 10Gi
    pgInstances: 1
    pgStorage: 10Gi
  scm:
    approvalLabel: tatara/awaiting-approval
    botLogin: szymonrychu-bot
    cron:
      brainstorm:
        enabled: true
        maxOpenProposals: 3
        schedule: 0 * * * *
        sources:
        - docs
        - memory
        - internet
      issueScan:
        maxPerRepo: 1
        schedule: 0 * * * *
      mrScan:
        maxPerRepo: 1
        schedule: 0 * * * *
    mergePolicy: afterApproval
    owner: szymonrychu
    prReactionScope: labeledOrMentioned
    priorityLabel: tatara/priority
    provider: github
  scmSecretRef: tatara-scm
  triggerLabel: tatara
```

PLAN 1 also needs the operator release pin + cluster values from the bucket (so the full release reproduces in `tatara-helmfile`):
- `tatara-operator` chart `oci://harbor.szymonrichert.pl/charts/tatara-operator` version `0.0.0-7d45bd9`, ns `tatara`, `createNamespace: true`, `image.tag: "7d45bd9"`.
- `tatara-chat` chart `oci://harbor.szymonrichert.pl/charts/tatara-chat` version `0.1.0`, ns `tatara`, `createNamespace: true`.
- bucket `values/common.yaml`: `imagePullSecrets: [{name: regcred}]`.
- sops `default.secrets.yaml`: 5 keys (`operatorOidcClientSecret`, `anthropicOauthToken`, `openaiApiKey`, `scmToken`, `scmWebhookSecret`), same PGP key `D39E46932A270AA3BA490B9DB9FE928D3E8BCED8` (copy verbatim, do NOT re-encrypt).

---

## File Structure

### Created
| Path | Responsibility |
|------|----------------|
| `helmfiles/coding/values/arc-runner-tatara-helmfile/common.yaml` | Runner pod spec for the deploy runner: mirrors `arc-runner-shared.yaml` (control-plane nodeSelector, fsGroup 1001, regcred, maxRunners 1, ephemeral Ceph work volume, `ghcr.io/actions/actions-runner:2.335.1`) but overrides `serviceAccountName` to the dedicated `tatara-helmfile-deployer` and sets `githubConfigUrl`/`runnerScaleSetName` for the new repo. |
| `helmfiles/coding/values/arc-runner-tatara-helmfile/raw/tatara-helmfile-deployer.common.pre.yaml` | Raw pre-manifest: ServiceAccount `tatara-helmfile-deployer` (ns `arc-runners`) + ClusterRoleBinding `tatara-helmfile-deployer` to the built-in `cluster-admin` ClusterRole (modelled on `cluster_cleanup` + the argo-workflow admin binding). Applied by `.hook.sh` presync. |

### Modified
| Path | Responsibility |
|------|----------------|
| `helmfiles/coding/helmfile.yaml.gotmpl` | Add the `arc-runner-tatara-helmfile` release (needs `arc-system/arc-controller`, uses `values/arc-runner-tatara-helmfile/common.yaml` via the `*default` template layering). |
| `helmfile.yaml.gotmpl` (root) | Remove the `- path: helmfiles/tatara/helmfile.yaml.gotmpl` include line. |
| `MEMORY.md` | Add a dated entry reversing the 2026-06-05 consolidation + recording the dedicated deploy runner + cluster-admin SA. |

### Deleted
| Path | Responsibility |
|------|----------------|
| `helmfiles/tatara/` (entire directory) | The full tatara bucket: `helmfile.yaml.gotmpl`, `values/common.yaml`, `values/tatara-chat/default.yaml`, `values/tatara-operator/{common,default,default.secrets}.yaml`, `values/tatara-operator/raw/project-tatara.tatara-operator.pre.yaml`. All content reproduced in this plan / ported by PLAN 1. |

### Test / validation (no code, so validation = concrete commands)
| Command | Where |
|---------|-------|
| `mise exec -- helmfile -e default build` | repo root: confirms the root include list + coding bucket render after both changes |
| `mise exec -- ./.parse.py` (via the CI venv, or `LOG_LEVEL=DEBUG`) | repo root: regenerates `.sub.gitlab-ci.yml`, must exit 0 and contain no `tatara:` jobs and no dangling `needs` |
| `mise exec -- helmfile -e default --selector application=arc-runner lint` | repo root: lints the runner releases incl. the new one (Harbor OCI not required for the ARC OCI chart pull which is ghcr) |
| `kubectl apply --dry-run=client -f helmfiles/coding/values/arc-runner-tatara-helmfile/raw/tatara-helmfile-deployer.common.pre.yaml` | validates the raw RBAC manifest |
| `pre-commit run --all-files` | yamllint/gitleaks/check-yaml clean |

---

## Pre-flight (Task 0)

Files: none (setup only).

- [ ] 0.1 Confirm the infra repo is a clean fresh `main`:
  ```bash
  git -C ~/Documents/infra/helmfile fetch origin
  git -C ~/Documents/infra/helmfile checkout main
  git -C ~/Documents/infra/helmfile pull --ff-only origin main
  git -C ~/Documents/infra/helmfile status -sb
  ```
  Expected: `## main...origin/main` with no behind/ahead. (The untracked `helmfiles/coding/values/tatara-argo-workflows/` seen at plan-time is pre-existing and out of scope; leave it.)
- [ ] 0.2 Create the worktree off `main` (superpowers:using-git-worktrees). Native tool if available, else:
  ```bash
  git -C ~/Documents/infra/helmfile worktree add -b feat/tatara-deploy-runner ../helmfile-tatara-deploy-runner main
  ```
  All subsequent paths in this plan are relative to the worktree root `~/Documents/infra/helmfile-tatara-deploy-runner`.
- [ ] 0.3 Bootstrap tools in the worktree:
  ```bash
  cd ~/Documents/infra/helmfile-tatara-deploy-runner && mise install
  ```
  Expected: helm/helmfile/kubectl/sops resolve; helm-secrets + helm-diff plugins install via `[hooks].postinstall`.
- [ ] 0.4 Baseline: regenerate CI BEFORE any change and stash the output for diff later:
  ```bash
  cd ~/Documents/infra/helmfile-tatara-deploy-runner && mise exec -- ./.parse.py && cp .sub.gitlab-ci.yml /tmp/sub-ci-before.yml
  ```
  Expected: exit 0; `/tmp/sub-ci-before.yml` contains `tatara/tatara-operator` and `tatara/tatara-chat` jobs (the bucket still present). If `./.parse.py` cannot import `gitlab_cicd_python_wrapper` outside the CI venv, note it and rely on the `helmfile build`-based validation in Task 4 instead (the generator only consumes `helmfile build` output; a clean build is the load-bearing check).

---

## Workstream A: deploy runner

### Task A1: dedicated deployer SA + cluster-admin ClusterRoleBinding (raw manifest)

Files:
- Create: `helmfiles/coding/values/arc-runner-tatara-helmfile/raw/tatara-helmfile-deployer.common.pre.yaml`
- Validate: `kubectl apply --dry-run=client -f <that file>`

Steps:
- [ ] A1.1 Create the directory:
  ```bash
  mkdir -p helmfiles/coding/values/arc-runner-tatara-helmfile/raw
  ```
- [ ] A1.2 Write `helmfiles/coding/values/arc-runner-tatara-helmfile/raw/tatara-helmfile-deployer.common.pre.yaml` with EXACTLY this content (modelled on `arc-runner-szymonrychu/raw/tatara-ci-dispatcher.common.pre.yaml` for the SA + `backbone/raw/cluster_cleanup.common.post.yaml` for the ClusterRole/ClusterRoleBinding shape; reuses the built-in `cluster-admin` ClusterRole exactly as the argo-workflow RoleBinding does):
  ```yaml
  # Deploy identity for the tatara-helmfile GitHub repo. The dedicated ARC runner
  # pod (arc-runner-tatara-helmfile) runs as this ServiceAccount and executes
  # `helmfile apply` against the cluster using the pod's in-cluster SA token (no
  # KUBECONFIG leaves the cluster). The tatara-operator chart installs CRDs,
  # ClusterRoles and validating/mutating webhooks, so the deployer needs broad
  # cluster scope; we reuse the built-in cluster-admin ClusterRole rather than
  # hand-curate verbs (same precedent as cluster_cleanup + the argo-workflow
  # admin binding). This SA is DELIBERATELY separate from the shared kaniko
  # tatara-ci-dispatcher SA: only tatara-helmfile workflows run on this runner,
  # so the cluster-admin blast radius is scoped to that one private bot-write repo.
  # The SA lives in arc-runners because .hook.sh applies raw manifests with
  # `-n <release-namespace>` (= arc-runners); the ClusterRoleBinding is
  # cluster-scoped so the -n flag is ignored for it.
  apiVersion: v1
  kind: ServiceAccount
  metadata:
    name: tatara-helmfile-deployer
    namespace: arc-runners
  ---
  apiVersion: rbac.authorization.k8s.io/v1
  kind: ClusterRoleBinding
  metadata:
    name: tatara-helmfile-deployer
  roleRef:
    apiGroup: rbac.authorization.k8s.io
    kind: ClusterRole
    name: cluster-admin
  subjects:
    - kind: ServiceAccount
      name: tatara-helmfile-deployer
      namespace: arc-runners
  ```
- [ ] A1.3 Validate the manifest renders and is schema-valid:
  ```bash
  mise exec -- kubectl apply --dry-run=client -f helmfiles/coding/values/arc-runner-tatara-helmfile/raw/tatara-helmfile-deployer.common.pre.yaml
  ```
  Expected: `serviceaccount/tatara-helmfile-deployer created (dry run)` and `clusterrolebinding.rbac.authorization.k8s.io/tatara-helmfile-deployer created (dry run)`. (If the cluster context is unavailable, fall back to `mise exec -- kubectl apply --dry-run=client --validate=false -o yaml -f ...` or `yamllint` the file; the live apply is the human-gated step.)
- [ ] A1.4 Commit:
  ```bash
  git add helmfiles/coding/values/arc-runner-tatara-helmfile/raw/tatara-helmfile-deployer.common.pre.yaml
  git commit -m "feat: tatara-helmfile-deployer SA + cluster-admin binding"
  ```

### Task A2: deploy runner values file

Files:
- Create: `helmfiles/coding/values/arc-runner-tatara-helmfile/common.yaml`

Steps:
- [ ] A2.1 Write `helmfiles/coding/values/arc-runner-tatara-helmfile/common.yaml` with EXACTLY this content. It is a copy of `arc-runner-szymonrychu/common.yaml` with three deliberate changes: `githubConfigUrl`/`runnerScaleSetName` point at `tatara-helmfile`; `serviceAccountName` is the dedicated `tatara-helmfile-deployer` (NOT the kaniko `tatara-ci-dispatcher`); `maxRunners: 1` (a single serialized deployer, reinforcing the workflow `concurrency` guard and matching the low-cap rationale). `githubConfigSecret` stays `arc-runner-szymonrychu-github` (the shared ARC GitHub App credential covers all repos under the account; this is the same secret every per-repo runner uses). No `priorityClassName` (the per-repo runners omit it; only the standalone `arc-runner-szymonrychu` sets `gitlab-runner-low`):
  ```yaml
  # Deploy runner for the tatara-helmfile GitHub repo. szymonrychu is a personal
  # account (no org), so this is a repo-scoped gha-runner-scale-set; the
  # tatara-helmfile workflows use `runs-on: arc-runner-tatara-helmfile`. Unlike
  # the per-repo CI runners (arc-runner-shared.yaml), this one runs as its OWN
  # ServiceAccount tatara-helmfile-deployer (cluster-admin bound, created by the
  # raw/ pre-manifest in this release dir) so `helmfile apply` can manage the
  # whole tatara platform via the pod's in-cluster SA token. It does NOT use the
  # shared kaniko tatara-ci-dispatcher SA.
  controllerServiceAccount:
    namespace: arc-system
    name: arc-controller-gha-rs-controller

  githubConfigUrl: https://github.com/szymonrychu/tatara-helmfile
  githubConfigSecret: arc-runner-szymonrychu-github

  runnerScaleSetName: arc-runner-tatara-helmfile

  minRunners: 0
  # Single deployer: applies are serialized by the workflow concurrency guard;
  # one runner is enough and keeps the cluster-admin blast radius minimal.
  maxRunners: 1

  template:
    spec:
      # The _work volume is a Ceph RBD PVC (ext4, root-owned at mount). The runner
      # image runs as UID/GID 1001; fsGroup=1001 chowns the volume so the runner
      # can create _work/_tool. Without it: EACCES, job dies in ~3s (no step logs).
      securityContext:
        fsGroup: 1001
      # Dedicated deploy identity (see header). Bound to cluster-admin so
      # `helmfile apply` can install the operator's CRDs/ClusterRoles/webhooks.
      serviceAccountName: tatara-helmfile-deployer
      # Cluster rewrites ghcr.io/* to harbor.szymonrichert.pl/proxy-ghcr/* which
      # requires auth via regcred. regcred is applied to every release namespace
      # by the global .hook.sh presync mechanism (raw/regcred.pre.secrets.yaml).
      imagePullSecrets:
      - name: regcred
      # Builds run ON control-plane, mirroring the other tatara runners: keeps
      # runner IO off the media-stack node's root disk (sda 53-90% busy,
      # 2026-06-12). CP root-disk/etcd protection: ephemeral caps + arc-runners
      # LimitRange + workdir on Ceph, not node disk.
      nodeSelector:
        node-role.kubernetes.io/control-plane: ""
      volumes:
      - name: work
        ephemeral:
          volumeClaimTemplate:
            spec:
              accessModes: ["ReadWriteOnce"]
              storageClassName: rook-ceph
              resources:
                requests:
                  storage: 40Gi
      containers:
      - name: runner
        # renovate: image=ghcr.io/actions/actions-runner
        image: ghcr.io/actions/actions-runner:2.335.1
        command: ["/home/runner/run.sh"]
        resources:
          requests:
            cpu: 200m
            memory: 512Mi
            ephemeral-storage: 4Gi
          limits:
            cpu: "2"
            memory: 4Gi
            ephemeral-storage: 40Gi
        volumeMounts:
        - name: work
          mountPath: /home/runner/_work
  ```
- [ ] A2.2 Validate it is well-formed YAML:
  ```bash
  mise exec -- yamllint -d '{extends: default, rules: {line-length: {max: 320}, document-start: disable}}' helmfiles/coding/values/arc-runner-tatara-helmfile/common.yaml
  ```
  Expected: no errors (yamllint config mirrors `.pre-commit-config.yaml`; note `^helmfiles/` is excluded in pre-commit, so this is a manual sanity check only).
- [ ] A2.3 Commit:
  ```bash
  git add helmfiles/coding/values/arc-runner-tatara-helmfile/common.yaml
  git commit -m "feat: arc-runner-tatara-helmfile deploy runner values"
  ```

### Task A3: register the deploy runner release in the coding bucket

Files:
- Modify: `helmfiles/coding/helmfile.yaml.gotmpl`

Steps:
- [ ] A3.1 Read `helmfiles/coding/helmfile.yaml.gotmpl` (already known). The new release goes immediately AFTER the per-repo `{{- range ... }}{{- end }}` block (ends at the line `{{- end }}` following the range that lists `tatara-operator ... tatara-chat`), BEFORE the `- name: joplin-database` release. It deliberately does NOT use `values/arc-runner-shared.yaml` (that file hardcodes the kaniko SA); instead the `*default` template's values layering auto-loads `values/arc-runner-tatara-helmfile/common.yaml` via `values/{{ .Release.Name }}/common.yaml`. So the release body is minimal and carries no inline `values:` block.
- [ ] A3.2 Apply this edit. Insert after the `{{- end }}` that closes the per-repo runner range (the block starting `{{- range $repo := list "tatara-operator" ...`) and before `- name: joplin-database`:
  ```yaml
  # Dedicated deploy runner for the tatara-helmfile GitHub repo. Unlike the
  # per-repo CI runners above, it runs as its OWN cluster-admin-bound SA
  # (tatara-helmfile-deployer, created by values/arc-runner-tatara-helmfile/raw/)
  # so `helmfile apply` can manage the whole tatara platform from in-cluster.
  # Values come from values/arc-runner-tatara-helmfile/common.yaml via the
  # *default template layering (NOT arc-runner-shared.yaml, which pins the
  # shared kaniko dispatcher SA).
  - name: arc-runner-tatara-helmfile
    version: 0.14.2
    chart: oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set
    namespace: arc-runners
    needs:
    - arc-system/arc-controller
    labels:
      purpose: ci
      application: arc-runner
    <<: *default
  ```
  The exact `old_string` to match for the Edit is the boundary between the range end and the joplin release:
  ```
  {{- end }}

  - name: joplin-database
  ```
  Replace with the new release block inserted between them (keep `{{- end }}` and `- name: joplin-database`).
- [ ] A3.3 Validate the bucket renders with the new release present:
  ```bash
  mise exec -- helmfile -e default build 2>&1 | grep -A2 "arc-runner-tatara-helmfile"
  ```
  Expected: the release appears with `namespace: arc-runners`, `serviceAccountName: tatara-helmfile-deployer` resolved from the values file, and `chart: oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set version 0.14.2`. A clean overall `helmfile build` (exit 0) is the load-bearing check.
- [ ] A3.4 Commit:
  ```bash
  git add helmfiles/coding/helmfile.yaml.gotmpl
  git commit -m "feat: register arc-runner-tatara-helmfile release"
  ```

---

## Workstream B: tatara bucket removal

### Task B1: drop the tatara include from the root helmfile

Files:
- Modify: `helmfile.yaml.gotmpl` (root)

Steps:
- [ ] B1.1 Edit the root `helmfile.yaml.gotmpl`. Remove the single line:
  ```
  - path: helmfiles/tatara/helmfile.yaml.gotmpl
  ```
  The Edit `old_string` is the last two include lines (anchor on the preceding `blog` line so the match is unique):
  ```
  - path: helmfiles/blog/helmfile.yaml.gotmpl
  - path: helmfiles/tatara/helmfile.yaml.gotmpl
  ```
  Replace with:
  ```
  - path: helmfiles/blog/helmfile.yaml.gotmpl
  ```
- [ ] B1.2 Validate the root still builds with the include gone (tatara dir still present at this point, so it must simply be ignored):
  ```bash
  mise exec -- helmfile -e default build > /dev/null && echo "BUILD OK"
  ```
  Expected: `BUILD OK`, and NO `tatara/tatara-operator`/`tatara/tatara-chat` releases in the build output:
  ```bash
  mise exec -- helmfile -e default build 2>/dev/null | grep -c "namespace: tatara" || true
  ```
  Expected: `0`.
- [ ] B1.3 Commit:
  ```bash
  git add helmfile.yaml.gotmpl
  git commit -m "refactor: drop tatara bucket include from root helmfile"
  ```

### Task B2: delete the tatara bucket directory

Files:
- Delete: `helmfiles/tatara/` (entire tree)

Steps:
- [ ] B2.1 Remove the directory (tracked files, so `git rm -r`):
  ```bash
  git rm -r helmfiles/tatara
  ```
  Expected: removes `helmfiles/tatara/helmfile.yaml.gotmpl`, `values/common.yaml`, `values/tatara-chat/default.yaml`, `values/tatara-operator/{common,default,default.secrets}.yaml`, and `values/tatara-operator/raw/project-tatara.tatara-operator.pre.yaml`.
- [ ] B2.2 Confirm nothing else in the repo references the bucket:
  ```bash
  grep -rn "helmfiles/tatara" . --include='*.yaml' --include='*.gotmpl' --include='*.py' --include='*.yml' 2>/dev/null || echo "NO DANGLING REFS"
  ```
  Expected: `NO DANGLING REFS`.
- [ ] B2.3 Commit:
  ```bash
  git commit -m "refactor: remove tatara bucket (extracted to tatara-helmfile repo)"
  ```

### Task B3: confirm `.parse.py` regenerates valid CI with no dangling needs

Files:
- Validate: `.parse.py` output (generated `.sub.gitlab-ci.yml`)

Steps:
- [ ] B3.1 Re-run the generator AFTER both removals:
  ```bash
  mise exec -- ./.parse.py && cp .sub.gitlab-ci.yml /tmp/sub-ci-after.yml
  ```
  Expected: exit 0. (If the local env lacks `gitlab_cicd_python_wrapper`/`pydantic_settings`, run inside the CI builder image or a venv: `python3 -m venv /tmp/pv && /tmp/pv/bin/pip install pydantic pydantic-settings pyyaml gitlab_cicd_python_wrapper && /tmp/pv/bin/python ./.parse.py`. If the wrapper package is unavailable locally, the authoritative check is B3.3 below, since `.parse.py` derives every job purely from `helmfile build` output and the per-bucket value-path globs.)
- [ ] B3.2 Confirm NO tatara jobs and NO dangling `needs` survive:
  ```bash
  grep -n "tatara" /tmp/sub-ci-after.yml || echo "NO TATARA JOBS"
  ```
  Expected: `NO TATARA JOBS`. The generator names jobs `{namespace}:{name}` and resolves `needs` only within each bucket's own releases; the tatara bucket's two releases (`tatara:tatara-operator`, `tatara:tatara-chat`) had no cross-bucket `needs` and nothing in other buckets `needs` them (verified: no other release references `tatara/...`), so removal leaves no dangling reference.
- [ ] B3.3 Diff before/after to confirm the ONLY delta is the two removed tatara jobs + the dropped `coding-N`/`tatara-N` stage names (the new `arc-runner-tatara-helmfile` release adds one `arc-runners:arc-runner-tatara-helmfile` job in the `coding-*` stages):
  ```bash
  diff /tmp/sub-ci-before.yml /tmp/sub-ci-after.yml || true
  ```
  Expected delta: REMOVED `tatara:tatara-operator` + `tatara:tatara-chat` jobs and any `tatara-1`/`tatara-2` stage entries; ADDED `arc-runners:arc-runner-tatara-helmfile`. No other job's `needs`/`stage` changes. (If `/tmp/sub-ci-before.yml` was not capturable per Task 0.4, assert directly: `grep -c "tatara:" /tmp/sub-ci-after.yml` returns `0` and `grep -c "arc-runner-tatara-helmfile" /tmp/sub-ci-after.yml` returns `>= 1`.)
- [ ] B3.4 Clean up the generated artifact (it is a CI build artifact, not committed):
  ```bash
  git status --short .sub.gitlab-ci.yml
  ```
  Expected: `.sub.gitlab-ci.yml` is untracked/ignored. If it is tracked here, leave it as-is (do not commit the regenerated copy); the CI `process:template` job regenerates it. Confirm it is in `.gitignore` or otherwise not staged; do NOT `git add` it.

---

## Task C: reverse-consolidation MEMORY.md entry

Files:
- Modify: `MEMORY.md`

Steps:
- [ ] C1.1 Read `MEMORY.md` (already known). Add a new dated entry at the TOP of the bullet list (most-recent-first, matching the existing 2026-06-05 -> 2026-06-03 ordering), directly under the `One line per decision.` line:
  ```markdown
  - 2026-06-13: **Reversed the 2026-06-05 tatara consolidation.** The `helmfiles/tatara/` bucket is removed from this repo and its include dropped from the root helmfile; the tatara platform's releases (`tatara-operator` 0.0.0-7d45bd9, `tatara-chat` 0.1.0) + the `Project` CR `tatara` + sops secrets now live in the standalone private `github.com/szymonrychu/tatara-helmfile` repo, which deploys itself via GitHub Actions on an in-cluster ARC runner. Rationale: scope the autonomous tatara deploy bot's cluster-admin access to a single dedicated repo instead of this whole 60+ release homelab infra repo. Audit confirmed the bucket carried NO `Repository` CRs (operator discovers repos at runtime via the Project scmSecretRef), so nothing was orphaned. NEW here: `arc-runner-tatara-helmfile` RunnerScaleSet (coding bucket) runs as a dedicated cluster-admin-bound SA `tatara-helmfile-deployer` (NOT the shared kaniko `tatara-ci-dispatcher`); `helmfile apply` uses the pod's in-cluster SA token (no KUBECONFIG). The sops PGP key (D39E...CED8) is shared with tatara-helmfile; key rotation noted out of scope.
  ```
- [ ] C1.2 Commit:
  ```bash
  git add MEMORY.md
  git commit -m "docs: reverse tatara consolidation, add deploy runner note"
  ```

---

## Task 4: full verification (superpowers:verification-before-completion)

Files: none (validation only). Run every command; capture output as evidence before any "done" claim.

- [ ] 4.1 Root + all remaining buckets build clean (tatara gone, deploy runner present):
  ```bash
  mise exec -- helmfile -e default build > /tmp/build-final.txt 2>&1 && echo "BUILD OK"
  grep -c "namespace: tatara$" /tmp/build-final.txt   # expect 0
  grep -c "arc-runner-tatara-helmfile" /tmp/build-final.txt  # expect >= 1
  ```
  Expected: `BUILD OK`; first grep `0`; second grep `>= 1`.
- [ ] 4.2 Lint the runner releases (remaining buckets unaffected):
  ```bash
  mise exec -- helmfile -e default --selector application=arc-runner lint
  ```
  Expected: lint passes for `arc-runner-szymonrychu`, the six per-repo runners, and `arc-runner-tatara-helmfile`. (OCI chart pull is from ghcr via the proxy; if offline, `helmfile build` in 4.1 is the load-bearing render check.)
- [ ] 4.3 `.parse.py` clean + no tatara, no dangling needs (re-assert Task B3):
  ```bash
  mise exec -- ./.parse.py && grep -c "tatara:" .sub.gitlab-ci.yml   # expect 0
  grep -c "arc-runner-tatara-helmfile" .sub.gitlab-ci.yml            # expect >= 1
  ```
  Expected: generator exit 0; first grep `0`; second `>= 1`. Then ensure `.sub.gitlab-ci.yml` is not staged (Task B3.4).
- [ ] 4.4 RBAC manifest still dry-run valid:
  ```bash
  mise exec -- kubectl apply --dry-run=client -f helmfiles/coding/values/arc-runner-tatara-helmfile/raw/tatara-helmfile-deployer.common.pre.yaml
  ```
  Expected: SA + ClusterRoleBinding both `(dry run)`.
- [ ] 4.5 pre-commit clean across the worktree:
  ```bash
  mise exec -- pre-commit run --all-files
  ```
  Expected: all hooks pass (trailing-whitespace, end-of-file-fixer, check-yaml --allow-multiple-documents, gitleaks, yamllint [helmfiles excluded], conventional-pre-commit on commit-msg). Fix any reported issue and amend the relevant commit.
- [ ] 4.6 Review the full diff against `main`:
  ```bash
  git -C ~/Documents/infra/helmfile-tatara-deploy-runner diff main --stat
  ```
  Expected stat: 2 files added (`arc-runner-tatara-helmfile/common.yaml`, `.../raw/tatara-helmfile-deployer.common.pre.yaml`), `helmfiles/coding/helmfile.yaml.gotmpl` + root `helmfile.yaml.gotmpl` + `MEMORY.md` modified, and 6 files under `helmfiles/tatara/` deleted.
- [ ] 4.7 superpowers:requesting-code-review on the worktree diff. Fix any critical/high findings, re-run 4.5, then proceed.

---

## Task 5: push branch + open GitLab MR (HUMAN-GATED merge)

Files: none.

- [ ] 5.1 Push the branch:
  ```bash
  git -C ~/Documents/infra/helmfile-tatara-deploy-runner push -u origin feat/tatara-deploy-runner
  ```
- [ ] 5.2 Open the MR via `glab` (or print the MR-create URL if `glab` is unavailable). Title and description below. **Do NOT enable auto-merge. Do NOT merge. The user merges this MR.**
  ```bash
  glab mr create \
    --source-branch feat/tatara-deploy-runner \
    --target-branch main \
    --title "feat: tatara-helmfile deploy runner + remove tatara bucket" \
    --description "$(cat <<'EOF'
  Reverses the 2026-06-05 tatara consolidation. Two workstreams:

  A. Adds `arc-runner-tatara-helmfile` RunnerScaleSet (coding bucket) running as a
     dedicated cluster-admin-bound SA `tatara-helmfile-deployer` (NOT the shared
     kaniko `tatara-ci-dispatcher`) for autonomous `helmfile apply` from the new
     `github.com/szymonrychu/tatara-helmfile` repo via in-cluster SA token.
  B. Removes the `helmfiles/tatara/` bucket and its root include. Audit confirmed
     the bucket carried NO `Repository` CRs (only the `Project` CR, ported to
     tatara-helmfile by PLAN 1); nothing orphaned.

  Validation done locally: `helmfile -e default build` clean, `.parse.py`
  regenerates with no tatara jobs and no dangling needs, `kubectl --dry-run`
  on the RBAC manifest, `pre-commit run --all-files` green.

  HUMAN-GATED: reviewer merges. After merge the CI applies the new runner; the
  first live `kubectl apply` of the deployer SA/ClusterRoleBinding happens via the
  `.hook.sh` presync on the next `arc-runner-tatara-helmfile` sync. The tatara
  releases are NOT in this repo anymore - they deploy from tatara-helmfile.
  EOF
  )"
  ```
  If `glab` is not configured, output the branch name + a one-line instruction for the user to open the MR `main <- feat/tatara-deploy-runner` and DO NOT proceed further.
- [ ] 5.3 STOP. Report the MR URL/branch. Merge is the user's action (Decision authority: destructive/prod, human-gated per the design spec's "Human-gated" section item 2).

---

## Handoff to PLAN 1 (cross-plan, CRITICAL)

1. **No `Repository` CRs to carry from infra.** The tatara bucket had ZERO. PLAN 1 authors the new self-enroll `Repository` CR for `tatara-helmfile` from scratch (design spec Sub-system C). Audit command for PLAN 1 to re-confirm post-extract: `grep -rl "kind: Repository" ~/Documents/infra/helmfile` returns nothing.
2. **Port the `Project` CR `tatara` verbatim** (full body in the "Orphaned-Repository-CR audit" section above) into `tatara-helmfile`'s `values/tatara-operator/raw/project-tatara.tatara-operator.pre.yaml`.
3. **Port release pins + values verbatim**: `tatara-operator` 0.0.0-7d45bd9 (`image.tag: "7d45bd9"`), `tatara-chat` 0.1.0, both ns `tatara` `createNamespace: true`; bucket `values/common.yaml` `imagePullSecrets: [{name: regcred}]`; sops `default.secrets.yaml` 5 keys, same PGP key D39E...CED8 (copy file verbatim, never re-encrypt).
4. **Runner tool delivery is PLAN 1's job.** This runner uses the stock `ghcr.io/actions/actions-runner:2.335.1` image with NO baked helm/helmfile/kubectl/sops. PLAN 1's `diff.yaml`/`apply.yaml` MUST install tools first (recommend `jdx/mise-action` reading `tatara-helmfile`'s `.mise.toml`, matching the infra `mise install`-first pattern). `runs-on: arc-runner-tatara-helmfile`.
5. **In-cluster SA auth.** The runner pod IS the `tatara-helmfile-deployer` SA (cluster-admin). PLAN 1's workflows must NOT set `KUBECONFIG`/`KUBE_CONFIG` - `helmfile apply` uses the pod's mounted SA token directly.

---

## Notes / constraints honored

- KISS: deploy runner is a verbatim mirror of the existing per-repo runner (same image, same volume, same nodeSelector/fsGroup), differing only in SA + repo URL + maxRunners. No new image, no initContainer, no abstraction.
- No tech-debt: cluster-admin scope is the deliberate, documented choice (operator installs CRDs/ClusterRoles/webhooks); rationale recorded in the manifest comment and `MEMORY.md`. sops key rotation flagged out of scope in `MEMORY.md`.
- Charts cluster-agnostic: unaffected (this is the infra side that owns cluster config).
- No plain ENVs/lists in any values.yaml: the runner values file is structured ARC chart config (pod spec), not app ENVs; no ConfigMap mapping needed.
- Branch flow: worktree off fresh `main`, develop in worktree, MR back to `main`, human merges; build/deploy only from `main` (the CI does it post-merge). Worktree cleanup after merge is the user's follow-up.
- Independently executable: this plan operates solely in `~/Documents/infra/helmfile` (its own worktree); the only cross-plan coupling is the one-directional handoff above (PLAN 1 consumes the ported CRs/values; nothing in PLAN 1 blocks PLAN 2 from completing/merging).
```
