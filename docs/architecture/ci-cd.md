---
title: CI/CD & Deploy Model
---

# CI/CD & Deploy Model

Tatara enforces a strict GitOps-only deploy model. No component is ever deployed by running `helm upgrade`, `kubectl set image`, or `kubectl apply` by hand. Every deploy is a git-recorded semver release that a pipeline applies. The platform runs a semver push-CD cascade (cut over 2026-07-05): merging to a component repo cuts a version tag, publishes the image and charts under that version, and propagates the bump into `tatara-helmfile` as a bot-authored auto-merge PR.

## The deploy chain

```mermaid
flowchart TD
    A[Merge to component repo main<br/>merged PR carries a semver:major|minor|patch label] --> B[Component CI<br/>lint + test + build]
    B --> C[release.yml: cut next tag vX.Y.Z]
    C --> D[Publish image :vX.Y.Z to Harbor]
    C --> E[Publish operator + tatara-project charts X.Y.Z to Harbor OCI]
    D --> F[cd-release bot opens version-bump PR to tatara-helmfile<br/>coalesced into cd/deploy-train]
    E --> F
    F --> G[diff.yaml: sticky helmfile-diff PR comment]
    F --> H[Auto-merge on green required checks<br/>gh pr merge --auto --squash]
    H --> I[apply.yaml on ARC runner<br/>helmfile -e default apply]
    I --> J[Live cluster updated]
```

The cascade is not human-gated for the automated path: `diff.yaml` still posts a sticky `helmfile diff` comment for auditability, but the version-bump PR auto-merges on green required checks. A human gate exists only where you configure branch protection to require a review on the `tatara-helmfile` PR; by default the deploy-train merges itself.

## Semver release and the version label

The release is driven by a `semver:{major|minor|patch}` label on the merged component PR. `release.yml` reads that label, cuts the next tag off `main`, and refuses to tag if no `semver:` label is present. The tag drives both the published image tag and the chart version.

A single release publishes, for the operator:

- **Image** `harbor.szymonrichert.pl/containers/tatara-operator:vX.Y.Z`. The per-commit `:SHORT_SHA` traceability image is pushed separately by the shared CI job on every commit; the release job publishes only `:vX.Y.Z`. Harbor's `containers` project has tag immutability, so a tag is published exactly once.
- **Charts** `tatara-operator` and `tatara-project`, both versioned as the bare semver `X.Y.Z` (no `v`, no SHA). The version bump into `tatara-helmfile` is gated on BOTH charts publishing, so a partial publish (operator chart up, `tatara-project` chart missing) can never ship a half release into the helmfile.

## Version pins in tatara-helmfile

Every deploy is two pins in `tatara-helmfile`, both pointing at the same semver release:

1. **Chart version** in `helmfile.yaml.gotmpl` - bare semver, e.g. `version: 0.4.11`.
2. **Image tag** in `values/tatara-operator/common.yaml` - the `v`-prefixed tag, e.g. `image.tag: "v0.4.11"`.

Note the asymmetry: the chart version has no leading `v`, the image tag does. The chart's `deployment.yaml` defaults the running image tag to `.Chart.AppVersion`, so a chart-version-only bump that leaves `image.tag` stale keeps the old image running. An image-tag-only bump fails `helmfile apply` because the pinned chart version does not exist in Harbor. The `cd-release` bot rewrites both pins in the same version-bump PR.

!!! warning "Keep chart pins recent"
    Harbor retains a bounded number of chart tags. A pin pointing to a chart version that has since been garbage-collected fails `helmfile apply` with "chart not found". The cascade keeps pins current automatically; hand-authored pins should track a live released version.

## Component CI

Component repos build via per-repo CI triggered by GitHub webhook on push to `main` or PR. `tatara-argo-workflows` was decommissioned (2026-07-05); it was homelab GitLab CI, never part of the tatara platform runtime. The in-cluster `argo-workflows` Helm release remains as separate, unrelated homelab infrastructure.

**Image build:** rootless `buildkitd` on a Ceph PVC (durable). TCP probes (not exec probes) on the buildkitd Service. Single-arch builds for the homelab cluster.

**Chart packaging:** release charts are packaged with the bare semver version (`helm package --version X.Y.Z`) and `helm push`ed to Harbor OCI. Any SHA-suffixed pre-release chart version must use a `g` prefix (e.g. `0.0.0-g0707870`) because Helm's semver validation rejects a pre-release segment that starts with a digit; a bare `0.0.0-0707870` is invalid.

## ARC runner

The `tatara-helmfile` `apply.yaml` and `diff.yaml` workflows run on an in-cluster GitHub Actions Runner Controller (ARC, `actions-runner-controller`) scale set (`runs-on: arc-runner-tatara-helmfile`). ARC is the GitHub self-hosted runner autoscaler; it is unrelated to Argo CD. The runner ServiceAccount has a `cluster-admin` binding (bounded to the tatara namespace). It has no KUBECONFIG - it uses the in-cluster ServiceAccount token directly.

**Runner failure modes:**

- A stale `AutoscalingListener` (deleted ERS ref) crash-loops and queues jobs forever. Fix: delete the stale `AutoscalingListener`.
- Control-plane-pinned runners: a flapping control-plane node evicts in-flight jobs at a uniform ~18 min mark (appears as "operation was canceled" across all concurrent jobs).

Applies are serialized by a non-cancelling concurrency group (`tatara-helmfile-apply`), so coalesced deploy-train merges queue rather than racing; at most one `helmfile apply` is in flight.

## tatara-project chart for enrollment CRs

`Project` and `Repository` CRs are managed by the `tatara-project` Helm chart rather than raw presync manifests. This keeps enrollment CRs under Helm ownership:

- `helm diff` shows CR changes before apply
- `helm rollback` reverts enrollment changes
- Clean ownership metadata on the CRs

The `project-tatara` and `project-infrastructure` releases use `needs: [tatara-operator]` so CRDs exist before CR application. The `tatara-project` chart is versioned and pinned in lockstep with `tatara-operator` (same `X.Y.Z`), so the CRs a release renders always match the operator's CRDs.

## Why live patches are forbidden

`kubectl set image` or `kubectl patch` bypasses:

- The diff review step (no sticky diff is posted before the change lands)
- The rollback-on-failure safety net (`helmDefaults.rollbackOnFailure: true`)
- The git audit trail

**Exception:** incident response. A `kubectl patch` is permitted to unblock a down service, but it must be immediately re-asserted through a `tatara-helmfile` PR so live state matches the repo. Never use live patches as a deploy path.

## CRD upgrade

CRDs are bundled in `charts/tatara-operator/templates/crds.yaml` and applied by `helm upgrade`. Pre-existing CRDs need a one-time ownership annotation before the chart can adopt them:

```bash
kubectl annotate crd projects.tatara.dev \
  meta.helm.sh/release-name=tatara-operator \
  meta.helm.sh/release-namespace=tatara \
  --overwrite
kubectl label crd projects.tatara.dev \
  app.kubernetes.io/managed-by=Helm \
  --overwrite
```

## Rollback

`helmDefaults.rollbackOnFailure: true` in `helmfile.yaml.gotmpl` instructs Helm to roll back all releases on any apply failure. This is automatic; no manual intervention is needed on a bad deploy.

Manual rollback is a revert of the version-bump commit in `tatara-helmfile` (which the cascade re-applies), or, for a live emergency:

```bash
helm rollback tatara-operator -n tatara  # roll back to previous revision
helmfile -e default diff                 # verify live state; then re-assert via a tatara-helmfile PR
```
