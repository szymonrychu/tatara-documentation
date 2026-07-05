# BuildKit-on-Ceph Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development for the 6-repo fan-out (Phase 4). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace kaniko in-cluster image builds with a single rootless `buildkitd`
Deployment whose state dir (`--root`) lives on a Ceph RBD PVC, moving all build IO
off the control-plane etcd NVMe (`/dev/nvme0n1p2`) that kaniko's containerd-overlay
writes were starving (root cause of the recurring node-flap / apiserver-crashloop CI
outages).

**Architecture:** One `buildkitd` Deployment (replicas 1, `Recreate`) in `arc-runners`,
state on a 100Gi `rook-ceph` PVC, exposed via ClusterIP Service `buildkitd:1234` (gRPC),
ingress-locked by NetworkPolicy to ARC runner pods only. Each repo's CI `image` job runs
`buildctl --addr tcp://buildkitd.arc-runners:1234 build` with a **remote git context**
(buildkitd clones the private repo, like kaniko did) and pushes to harbor using
per-build creds forwarded from the runner's `DOCKER_CONFIG` (no standing cluster push
secret). Migration is staged and gated by a pilot.

**Tech Stack:** Kubernetes 1.33, rook-ceph (RBD), moby/buildkit v0.18.2-rootless,
buildctl client, GitHub Actions (ARC runners), GitLab helmfile (infra deploy).

---

## Verified facts (checked against live cluster + repos, not assumed)

- **Ceph OSDs are on dedicated SATA SSDs** (`ata-Patriot_P210_2048GB_*`), one per CP
  node, **physically separate** from `/dev/nvme0n1p2` (etcd + containerd). => build IO
  on a Ceph PVC genuinely lands off the etcd NVMe. This is the whole premise; confirmed.
- **CP kernel 6.1.0-34 (Debian 12)** >> 5.13 => rootless buildkit native
  overlayfs-in-userns snapshotter works; no `/dev/fuse` needed. nas worker is 6.12.
- **ARC runner pods carry `app.kubernetes.io/component=runner`** => NetworkPolicy
  ingress selector.
- **No default-deny egress** in `arc-runners` (no netpols at all) => ingress-only policy
  is correct; buildkitd egress to harbor/docker.io/gcr.io/DNS stays open.
- **arc-runners has no PSA enforce label** => privileged would be allowed (fallback), but
  we use rootless.
- **kaniko-build.sh** dispatches a Job that self-clones `git://github.com/szymonrychu/<repo>.git#<SHA>`,
  pushes both `:<SHORT_SHA>` and `:<VERSION>` (`git describe --tags --always --dirty`),
  build-args `VERSION`/`COMMIT`/`DATE`. FROM images are PUBLIC (`golang:*-alpine` docker.io,
  `gcr.io/distroless/static-debian12:nonroot`) pulled anonymously; the harbor docker-config
  was used for push (and works for FROM-proxy too). buildctl forwarding the same harbor
  creds covers push; public FROMs pull anonymously, same as kaniko.
- **ci.yml `image` job** per repo: `runs-on: <repo-name>` (its own scale set),
  `needs: [secscan, lint, test, build, smoke]`, `if: github.event_name == 'push'`, env
  `GITHUB_TOKEN`/`HARBOR_USERNAME`/`HARBOR_PASSWORD`, steps `checkout(fetch-depth:0)` +
  `azure/setup-kubectl` + `bash .github/ci/kaniko-build.sh`. ONLY this job changes.
- **Repos that build images (the "6"):** tatara-operator, tatara-cli, tatara-memory,
  tatara-memory-repo-ingester, tatara-claude-code-wrapper, tatara-chat.
- **Dispatcher RBAC** (`tatara-ci-dispatcher`): batch/jobs CRUD + pods/pods.log get +
  secrets create/delete. Reduced to `rules: []` only at the END (Phase 5), after all 6
  repos are off kaniko (the SA is pinned by `serviceAccountName` in arc-runner values, so
  it stays; only its Role empties).
- Live incident state at plan time: 5vv07x2 cordoned (=> `rook-ceph-osd-2` Pending,
  Ceph degraded 2/3), maxRunners=1 on all 7 scale sets. Both restored in Phase 5.

---

## Decisions locked

- **Raw manifests under the existing `arc-runner-szymonrychu` release**, NOT a new
  helmfile release (no chart for one Deployment; matches the `tatara-ci-dispatcher` /
  `limitrange` precedent; rides the existing presync, zero `.sub.gitlab-ci.yml` change).
- **Rootless buildkit** (not privileged): appropriate co-located with etcd; kernel
  supports it. Privileged non-rootless is the documented fallback if the pilot shows a
  rootlesskit netns/socket failure.
- **buildkitd on control-plane** (mirrors runner placement). The proven killer was IO
  (now on Ceph); CPU/mem of compile is a NEW watch-item measured during the pilot. If
  apiserver flaps during a build despite IO-on-Ceph, move buildkitd to the nas worker
  (Ceph PVC mounts from any node).
- **Remote git context** (buildkitd clones), not local-context streaming: minimal delta
  from kaniko, keeps the runner checkout only for `git describe`.
- **Per-build harbor push auth from the runner** (`DOCKER_CONFIG` forwarded by buildctl),
  not a daemon-mounted standing secret: strictly better hygiene than kaniko's in-cluster
  transient secret.
- **GC cap in the daemon config** from day one (no deferred cleanup): keepBytes 60 GiB on
  the 100Gi PVC.

---

## Phase 1: Author + review infra manifests (infra repo, GitLab)

Branch `feat/buildkitd-on-ceph` off fresh `main` in `~/Documents/infra/helmfile`.

### Files to ADD

**`helmfiles/coding/values/arc-runner-szymonrychu/raw/buildkitd-pvc.common.pre.yaml`**

```yaml
# State dir for rootless buildkitd: all build layers/snapshots/cache live here
# (--root), on Ceph RBD (Patriot SATA SSDs), OFF the control-plane etcd NVMe
# (/dev/nvme0n1p2) that kaniko's containerd-overlay writes were starving.
# RWO single-attach: buildkitd is replicas:1 + Recreate, so RWO is correct and
# avoids RBD multi-attach errors.
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: buildkitd-state
spec:
  accessModes: ["ReadWriteOnce"]
  storageClassName: rook-ceph
  resources:
    requests:
      storage: 100Gi
```

**`helmfiles/coding/values/arc-runner-szymonrychu/raw/buildkitd.common.pre.yaml`**

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: buildkitd
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: buildkitd-config
data:
  buildkitd.toml: |
    # Cap the local cache so the Ceph PVC (--root) never fills and wedges every
    # build. buildkit auto-prunes the oldest beyond keepBytes.
    [worker.oci]
      gc = true
    [[worker.oci.gcpolicy]]
      keepBytes = 64424509440
      keepDuration = 604800
---
# Rootless buildkit, no API access. The only standing cred it holds is regcred
# (for its OWN image pull); harbor push + private git clone are per-build creds
# passed by the runner over gRPC. No Role/RoleBinding needed.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: buildkitd
  labels:
    app: buildkitd
spec:
  replicas: 1
  # RWO PVC: never two pods at once. Recreate tears the old pod down before the
  # new one binds the volume (RollingUpdate would deadlock on RBD single-attach).
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: buildkitd
  template:
    metadata:
      labels:
        app: buildkitd
    spec:
      serviceAccountName: buildkitd
      # Fresh RBD ext4 PVC mounts root-owned; rootless buildkit runs as 1000.
      # fsGroup chowns the volume so --root is writable (same lesson as the
      # runner fsGroup:1001 fix). Omit and buildkitd crashloops on first write.
      securityContext:
        fsGroup: 1000
      imagePullSecrets:
        - name: regcred
      # Mirror the runners: build infra on control-plane. The disk is the PVC
      # (Ceph), not the node; node choice is only scheduling locality.
      nodeSelector:
        node-role.kubernetes.io/control-plane: ""
      tolerations:
        - key: node-role.kubernetes.io/control-plane
          operator: Exists
          effect: NoSchedule
      containers:
        - name: buildkitd
          # renovate: image=moby/buildkit
          image: harbor.szymonrichert.pl/proxy-dockerhub/moby/buildkit:v0.18.2-rootless
          args:
            - --addr
            - unix:///run/user/1000/buildkit/buildkitd.sock
            - --addr
            - tcp://0.0.0.0:1234
            - --root
            - /home/user/.local/share/buildkit
            - --config
            - /home/user/.config/buildkit/buildkitd.toml
            - --oci-worker-no-process-sandbox
          securityContext:
            runAsUser: 1000
            runAsGroup: 1000
            # Rootless needs the relaxed profiles for the clone()/mount() the
            # userns worker issues. k8s 1.33 uses the field, not the beta
            # annotation.
            seccompProfile:
              type: Unconfined
            appArmorProfile:
              type: Unconfined
          ports:
            - name: grpc
              containerPort: 1234
          readinessProbe:
            exec:
              command: ["buildctl", "--addr", "tcp://localhost:1234", "debug", "workers"]
            initialDelaySeconds: 5
            periodSeconds: 30
          livenessProbe:
            exec:
              command: ["buildctl", "--addr", "tcp://localhost:1234", "debug", "workers"]
            initialDelaySeconds: 15
            periodSeconds: 60
          resources:
            requests:
              cpu: 500m
              memory: 1Gi
              # Tiny: real build IO is on the PVC, not the pod overlay. This cap
              # is the etcd-NVMe protection, proven by builds staying under it.
              ephemeral-storage: 1Gi
            limits:
              cpu: "4"
              memory: 8Gi
              ephemeral-storage: 2Gi
          volumeMounts:
            - name: state
              mountPath: /home/user/.local/share/buildkit
            - name: config
              mountPath: /home/user/.config/buildkit
      volumes:
        - name: state
          persistentVolumeClaim:
            claimName: buildkitd-state
        - name: config
          configMap:
            name: buildkitd-config
---
apiVersion: v1
kind: Service
metadata:
  name: buildkitd
spec:
  selector:
    app: buildkitd
  ports:
    - name: grpc
      port: 1234
      targetPort: grpc
```

**`helmfiles/coding/values/arc-runner-szymonrychu/raw/buildkitd-netpol.common.pre.yaml`**

```yaml
# Lock buildkitd's gRPC port to in-namespace ARC runner pods only. Egress stays
# open (buildkitd must reach harbor for push + DNS, and docker.io/gcr.io for
# public FROM pulls); ingress IS the security boundary. No cluster-wide
# default-deny egress exists in arc-runners (verified), so no egress block is
# needed; if one is added later, allow 443+53 egress here.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: buildkitd
spec:
  podSelector:
    matchLabels:
      app: buildkitd
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app.kubernetes.io/component: runner
      ports:
        - protocol: TCP
          port: 1234
```

### Step gate: mandatory opus review BEFORE the MR

- [ ] Dispatch an opus reviewer (`superpowers:requesting-code-review`) on the 3 new
  manifests with the explicit charge from the user authorization: **flag every potential
  problem; confirm zero impact on other namespaces or cluster state.** Specifically:
  does adding an ingress NetworkPolicy selecting only `app: buildkitd` leave runner pods
  and all other arc-runners pods unaffected? Does the rootless pod spec schedule and run
  on a CP node without privilege? Is the PVC RWO + Recreate safe? Any way this disrupts
  the in-flight kaniko builds (it must not — Phase 1 is additive)?
- [ ] Fix all critical/high findings. Only proceed to the MR if the reviewer confirms no
  cross-namespace / cluster-state impact.

### Step: open the infra MR (additive, kaniko untouched)

- [ ] `git checkout -b feat/buildkitd-on-ceph`, add the 3 files, commit
  `feat: rootless buildkitd on ceph for tatara CI builds`, push, open MR via `glab`.
- [ ] Pipeline runs `helmfile diff` (ACTION=diff) for review. Confirm the diff adds ONLY
  the 4 objects in `arc-runners` (PVC, SA+ConfigMap+Deployment+Service, NetworkPolicy)
  and changes nothing else.
- [ ] Merge => pipeline `helmfile apply` on the nas gitlab-runner.

### Step: verify buildkitd is up

- [ ] `kubectl -n arc-runners get pvc buildkitd-state` => Bound, rook-ceph.
- [ ] `kubectl -n arc-runners rollout status deploy/buildkitd`.
- [ ] `kubectl -n arc-runners exec deploy/buildkitd -- buildctl --addr tcp://localhost:1234 debug workers`
  => lists an oci worker.
- [ ] `kubectl -n arc-runners exec deploy/buildkitd -- df -h /home/user/.local/share/buildkit`
  => the mount is the RBD PVC (~100Gi), NOT the pod overlay.

If rootless fails to start (rootlesskit netns/socket error in logs), apply the fallback:
non-rootless `moby/buildkit:v0.18.2` image, drop `-rootless` + `--oci-worker-no-process-sandbox`
+ seccomp/apparmor Unconfined, add `securityContext.privileged: true`, mount at
`/var/lib/buildkit` with `--root /var/lib/buildkit`. Re-review, re-apply.

---

## Phase 2: Pilot build script + ci.yml (tatara-cli only)

Branch off **fresh main** in `~/Documents/tatara/tatara-cli` (bots push here; always pull).

### File to ADD: `.github/ci/build.sh`

```bash
#!/usr/bin/env bash
# Build this repo's image via the shared rootless buildkitd daemon and push to
# harbor. Runs on the ARC runner (in-cluster, namespace arc-runners). Talks gRPC
# to the buildkitd Service; buildkitd writes all layers/cache to its Ceph PVC
# (--root), OFF the control-plane etcd NVMe. No in-cluster Job, no transient
# cluster secrets: harbor push auth is a per-build docker config on THIS runner,
# the private-repo clone token is a buildkit frontend secret. Replaces
# kaniko-build.sh.
set -euo pipefail

REPO="${1:?repo name required}"
BUILDKITD_ADDR="tcp://buildkitd.arc-runners:1234"
SHORT_SHA="${GITHUB_SHA:0:7}"
VERSION="$(git describe --tags --always --dirty)"
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
DEST="harbor.szymonrichert.pl/containers/${REPO}"

: "${GITHUB_TOKEN:?GITHUB_TOKEN required}"
: "${HARBOR_USERNAME:?HARBOR_USERNAME required}"
: "${HARBOR_PASSWORD:?HARBOR_PASSWORD required}"

# Per-build docker config on the runner only (never an in-cluster secret).
# buildctl reads $DOCKER_CONFIG and forwards harbor auth to buildkitd for push.
DOCKER_CONFIG="$(mktemp -d)"
export DOCKER_CONFIG
trap 'rm -rf "$DOCKER_CONFIG"' EXIT
auth="$(printf '%s:%s' "$HARBOR_USERNAME" "$HARBOR_PASSWORD" | base64 -w0)"
cat >"${DOCKER_CONFIG}/config.json" <<EOF
{"auths":{"harbor.szymonrichert.pl":{"auth":"${auth}"}}}
EOF

# Remote git context (buildkitd clones the private repo, like kaniko did).
# MUST be https:// (NOT git://): buildkit's GIT_AUTH_TOKEN basic-auth extraheader
# only engages over https, and github.com no longer serves the git:// protocol.
# GIT_AUTH_TOKEN is the buildkit git-source frontend secret for the private
# clone; it is NOT a build-arg, so it never lands in a layer.
buildctl --addr "$BUILDKITD_ADDR" build \
  --frontend dockerfile.v0 \
  --opt context="https://github.com/szymonrychu/${REPO}.git#${GITHUB_SHA}" \
  --opt filename=Dockerfile \
  --opt build-arg:VERSION="${VERSION}" \
  --opt build-arg:COMMIT="${SHORT_SHA}" \
  --opt build-arg:DATE="${BUILD_DATE}" \
  --secret id=GIT_AUTH_TOKEN,env=GITHUB_TOKEN \
  --import-cache type=registry,ref="${DEST}:buildcache" \
  --export-cache type=registry,ref="${DEST}:buildcache,mode=max" \
  --output "type=image,\"name=${DEST}:${SHORT_SHA},${DEST}:${VERSION}\",push=true"

echo "buildkit: pushed ${DEST}:${SHORT_SHA} and ${DEST}:${VERSION}"
```

Notes:
- `--import-cache`/`--export-cache type=registry ...:buildcache,mode=max` gives
  cross-build layer reuse via harbor (kaniko had none). A separate `:buildcache` tag.
  First build has no cache to import (warning, not error) — expected.
- Multi-tag push via the quoted `"name=A,B"` form (kaniko used two `--destination`).
- No `--opt platform`: defaults to daemon-native amd64, matching kaniko. Multi-arch later.
- **Pilot contingency:** the git-source secret id is `GIT_AUTH_TOKEN`; if this buildkit
  version expects header form, the private clone fails — then also pass
  `--secret id=GIT_AUTH_HEADER` (`Authorization: Basic base64(x-access-token:TOKEN)`).
  Resolve on the pilot before fan-out and bake the working form into the shared script.

### File to DELETE: `.github/ci/kaniko-build.sh`

### File to EDIT: `.github/workflows/ci.yml` — `image` job only

Replace the `image` job's steps (keep `if`, `runs-on: tatara-cli`, `needs`, `env`):

```yaml
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: install buildctl
        run: |
          curl -sSfL "https://github.com/moby/buildkit/releases/download/v0.18.2/buildkit-v0.18.2.linux-amd64.tar.gz" \
            | sudo tar -xz -C /usr/local bin/buildctl
      - name: build and push image
        run: bash .github/ci/build.sh "${{ github.event.repository.name }}"
```

(`azure/setup-kubectl` removed; only the static buildctl client is needed.)

- [ ] Commit on a branch, open PR, then merge to main (image job runs on push).

---

## Phase 3: Verify the etcd-NVMe fix on the pilot (authoritative, 3 corroborating signals)

Per `verify-fix-via-downstream-not-draining-pods`: measure during a real compile, not the
transition window; require multiple signals.

- [ ] **No kaniko Job created:** `kubectl -n arc-runners get jobs -w` during the build =>
  none. (Proves the new path ran.)
- [ ] **CP etcd NVMe stays flat:** on the node running buildkitd,
  `iostat -x 2` (via `kubectl debug node/<cp> --image=nicolaka/netshoot`) — the
  `nvme0n1` `%util` must NOT spike across the compile window (the heaviest write phase).
- [ ] **Ceph took the IO instead:** buildkitd PVC grows
  (`exec deploy/buildkitd -- du -sh /home/user/.local/share/buildkit`) AND the rbd pool
  shows write throughput (`ceph osd pool stats` via the rook tools pod if present).
- [ ] **apiserver healthy through the build:** `kubectl get pods -n kube-system | grep apiserver`
  restart count does NOT increase during the build; no new `TaintManagerEviction`.
- [ ] **Image pushed, both tags:** `skopeo inspect docker://${DEST}:${SHORT_SHA}` and
  `:${VERSION}` resolve; the downstream chart job (pulls the image) passes.

Pass = all five. Fail on rootless/auth => fix per the Phase 1/2 contingencies, rebuild,
re-verify before fan-out.

---

## Phase 4: Fan out to the other 5 repos (parallel subagents)

`tatara-cli` proven, apply the **identical** change to tatara-operator, tatara-memory,
tatara-memory-repo-ingester, tatara-claude-code-wrapper, tatara-chat. The `build.sh` is
byte-identical (takes the repo name as `$1`); the ci.yml edit is identical except the job
keeps each repo's own `runs-on`/`needs`. Dispatch one subagent per repo in a single
message (sonnet, per tatara rule 7). Each: fresh `main`, add `build.sh`, delete
`kaniko-build.sh`, edit the `image` job, PR, merge.

**Stagger the merges** (do NOT merge all 5 at once): the memory lesson is mass-merging
spikes builds. Merge sequentially, confirm each repo's first post-migration build pushes
to harbor before the next. With builds now on Ceph this is belt-and-suspenders, but the
node is still recovering.

- [ ] Per repo: first push build green + image in harbor.

---

## Phase 5: Retire kaniko + restore normal posture

- [ ] **Second infra MR:** edit `tatara-ci-dispatcher.common.pre.yaml` Role to `rules: []`
  (keep SA + RoleBinding; values pin `serviceAccountName: tatara-ci-dispatcher`). Update
  the file comment. This shrinks attack surface (a build can no longer create Jobs/secrets
  next to the ARC GitHub App key). ONLY after all 6 repos confirmed off kaniko.
- [ ] Delete the unused `kaniko-executor` image reference if nothing else uses it (note,
  don't block).
- [ ] **Restore parallelism:** `helmfile`-side maxRunners back to 2 (the live patch is 1);
  uncordon 5vv07x2 (`kubectl uncordon kubernetes-5vv07x2`) => `rook-ceph-osd-2` schedules,
  Ceph returns to 3/3. Do this only after the build-IO fix is proven, so restored
  parallelism no longer re-triggers the flap.
- [ ] **Abandon** the 5 stale `fix/kaniko-dir-ceph` branches (operator/wrapper/memory/
  chat/ingester) — dead-end superseded by this migration.
- [ ] **MEMORY:** record buildkit-on-ceph done, the GC cap, the RWO single-replica
  throughput ceiling + SPOF blast radius, the Phase-5 RBAC-reduction ordering constraint,
  and that Ceph OSDs are on separate SATA SSDs (the fact that makes this work).

---

## Risks (from design, carried forward)

- **RWO single buildkitd is a throughput ceiling + SPOF.** Up to ~6-12 runners funnel
  through one daemon/one RBD volume; buildkit serializes + shares cache so correctness is
  fine, latency is the risk. A rollout/node-drain fails all in-flight builds for the
  restart window (CI is retriable; cache survives on the PVC). Start single, measure, only
  scale (horizontal, one RWO PVC each behind the Service) if queue latency hurts. Document
  the ceiling.
- **Cache growth** — mitigated by the GC cap (in from day one).
- **Rootless kernel/netns** — mitigated by kernel 6.1 + the privileged fallback; pilot is
  the gate.
- **CPU/mem of compile on a CP node** — new watch-item (Phase 3 apiserver check); move
  buildkitd to nas if it bites. Mitigated up front: cpu limit capped at 2.

---

## Adversarial review outcome (2026-06-14, 4 lenses, 19 findings, 10 confirmed)

Cross-namespace / cluster-state gate (user authorization): **PASS** — all 4 lenses confirm
the change is additive and arc-runners-scoped; no cluster-scoped object, no RBAC, no
networking/CNI/etcd change, no name collision, the ingress-only netpol selects only
`app: buildkitd` so other arc-runners pods are unaffected. Sole residual cluster-wide
vector is CP compute contention (now capped).

Applied before MR:
- **CRITICAL** git context `git://` -> `https://` (GIT_AUTH_TOKEN only authenticates over
  https; github.com dropped git://). Build would never clone the private repo otherwise.
- **HIGH** buildctl install: dropped `--strip-components=1` (wrote the wrong path).
- **MEDIUM** buildkitd cpu limit 4 -> 2 (a single compile cannot starve etcd next to it).
- **MEDIUM/sec** `automountServiceAccountToken: false` on the buildkitd pod (zero API
  access needed; neutralizes apiserver-reachability).

Documented, NOT blocking v1:
- buildkitd gRPC is unauthenticated; the netpol (ingress from `component=runner` only) is
  the authz boundary. CONSTRAINT: never apply the `app.kubernetes.io/component=runner`
  label to a non-runner workload in arc-runners. Future hardening: mutual-TLS on the
  daemon (`--tlscacert/--tlscert/--tlskey` + buildctl client certs).

Rejected (reviewer error): "deny the .25 VIP in buildkitd egress" — harbor.szymonrichert.pl
IS the .25 keepalived VIP, so buildkitd MUST egress to .25:443 to push/pull. Denying it
would break every build.
