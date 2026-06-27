---
title: tatara-helmfile
---

# tatara-helmfile

The GitOps helmfile repository for the tatara platform. Owns all platform Helm releases and the operator enrollment CRs (`Project` + `Repository`, codified via the `tatara-project` chart). Deploys on merge to `main` via GitHub Actions running an in-cluster ARC runner.

**Repository:** [`github.com/szymonrychu/tatara-helmfile`](https://github.com/szymonrychu/tatara-helmfile)

## Layout

```
helmfile.yaml.gotmpl          # single 'default' env, helmDefaults, releases
.hook.sh                      # presync: apply raw/ manifests, sops-decrypt secrets
values/
  common.yaml                 # imagePullSecrets: regcred (bucket-wide)
  tatara-operator/
    common.yaml               # image.tag pin
    default.yaml              # ingress, webhook, OIDC, memory-image values
    default.secrets.yaml      # sops-encrypted (PGP D39E...CED8)
    raw/                      # presync raw manifests (e.g. GitLab PAT Secret)
  project-tatara/
    common.yaml               # tatara Project + Repository CRs (tatara-project chart)
  project-infrastructure/
    common.yaml               # GitLab infrastructure Project + Repository CRs
  tatara-chat/
    default.yaml              # ingress host/path
.github/workflows/
  diff.yaml                   # PR -> helmfile diff -> sticky comment (non-blocking)
  apply.yaml                  # push main -> helmfile apply (concurrency-guarded)
```

## Releases

| Release | Chart | Namespace |
|---|---|---|
| `tatara-operator` | `oci://harbor.szymonrichert.pl/charts/tatara-operator` | `tatara` |
| `project-tatara` | `oci://harbor.szymonrichert.pl/charts/tatara-project` | `tatara` |
| `project-infrastructure` | `oci://harbor.szymonrichert.pl/charts/tatara-project` | `tatara` |
| `tatara-chat` | `oci://harbor.szymonrichert.pl/charts/tatara-chat` | `tatara` |

`project-tatara` and `project-infrastructure` both use `needs: [tatara-operator]` so CRDs exist before the Project/Repository CRs are applied.

## Deploy flow

1. Open a PR bumping a release (image tag in `values/tatara-operator/common.yaml` and/or chart `version:` in `helmfile.yaml.gotmpl`).
2. `diff.yaml` posts the rendered `helmfile diff` as a sticky PR comment for review.
3. Merge to `main`. `apply.yaml` runs `helmfile -e default apply` on the in-cluster runner. Failures roll back via `helmDefaults.rollbackOnFailure`.

## Chart version pinning

Chart versions follow the `0.0.0-g<sha>` pattern (operator CI packages charts on every push to main). **Pins must be kept recent** - Harbor GCs old chart tags, so a stale pin eventually fails `apply` with chart-not-found.

When bumping a release, always update BOTH:
- `values/tatara-operator/common.yaml` (`image.tag`)
- `helmfile.yaml.gotmpl` (`version:` for the chart)

A chart-only bump leaves the old image running.

## Enrollment CRs

Project and Repository CRs are declared as helmfile values under `values/project-*/common.yaml` and rendered by the `tatara-project` chart. This replaces the prior approach of raw YAML presync manifests - enrollment is now declarative and diff-visible.

## Local use

```bash
mise install                                  # helm/helmfile/kubectl/sops + plugins
helm registry login harbor.szymonrichert.pl   # OCI chart pull
helmfile -e default diff                       # dry-run against current kube-context
```

## Auth

- **Cluster:** in-cluster ServiceAccount `tatara-helmfile-deployer` (no KUBECONFIG needed)
- **Harbor:** `HARBOR_USERNAME` / `HARBOR_PASSWORD` GitHub Actions secrets
- **SOPS:** `GPG_PRIVATE_RSA_B64` GitHub Actions secret (base64-encoded PGP private key)

## GitOps rule

No component is ever deployed by running `helm upgrade`, `kubectl set image`, `kubectl apply`, or `kubectl patch` by hand for a production deploy. `kubectl patch` is allowed only for incident response (unblocking a down service); any such patch must be immediately re-asserted through tatara-helmfile.
