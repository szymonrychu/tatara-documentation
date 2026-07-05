# N4 - Retire static tatara-memory + operator chart RBAC/values + image bump + deploy

**Date:** 2026-06-07
**Milestone:** N4 of per-project-memory-provisioning.
**Repos touched:** `tatara-operator` (chart + crds + ROADMAP/MEMORY), `infra`
(helmfile tatara bucket), parent `tatara` (ROADMAP/MEMORY).
**Status:** ready to execute. ALL live deploy/uninstall/apply steps are GATED
(human-run, in `## Gated deploy runbook`). Tasks 1-9 PREPARE and validate only.

Authoritative sources:
- Spec: `docs/superpowers/specs/2026-06-07-per-project-memory-provisioning-design.md`
- Pin set: `docs/superpowers/plans/_per-project-memory-shared-contracts.md`
  (sections "RBAC additions (N4)" and "Retire static tatara-memory (N4)")
- Repo contract: `/Users/szymonri/Documents/tatara/CLAUDE.md` (rule 5 helm
  create, rule 6 no plain ENVs/lists, rule 14 cluster-agnostic, sops).

## Context this plan depends on (verified)

- N1 added `github.com/cloudnative-pg/cloudnative-pg/api/v1` (alias `cnpgv1`)
  to `tatara-operator/go.mod`; the chart's cnpg `cluster` subchart is pinned at
  `version: 0.6.1`, `repository: https://cloudnative-pg.github.io/charts`
  (source: `tatara-memory/charts/tatara-memory/Chart.yaml`). The vendored CRD
  in Task 3 MUST come from the cnpg release whose `api/v1` module N1 picked.
  Reconcile the two before Task 3 (see Task 3 "Pin reconciliation").
- N2 envtest loads CRDs from a single dir:
  `CRDDirectoryPaths: []string{filepath.Join("..", "..", "charts",
  "tatara-operator", "crds")}` (source:
  `tatara-operator/internal/controller/suite_test.go:35`). The cnpg `Cluster`
  CRD must land in that exact dir.
- N3 removed `MEMORY_BASE_URL` from `internal/config` and repointed
  Repository/Task wiring to `project.Status.Memory.Endpoint`. This plan removes
  the same key from the chart + infra values so chart and binary agree.
- Image pins (sources):
  - lightrag: `ghcr.io/hkuds/lightrag:v1.4.16`
    (`tatara-memory/charts/tatara-memory/charts/lightrag/values.yaml`).
  - neo4j: community single node; pin `neo4j:5-community` (native StatefulSet,
    not the upstream neo4j chart - per spec "Open picks").
  - memory: `harbor.szymonrichert.pl/containers/tatara-memory:0.2.0` (built in
    the gated runbook; tatara-memory service image, same repo as the chart's
    `image.repository`).
- Current operator chart version + appVersion: `0.1.1`
  (`charts/tatara-operator/Chart.yaml`). This milestone bumps both to `0.2.0`.
- Shared OpenAI secret name pinned to `lightrag-openai` (key
  `LLM_BINDING_API_KEY`), ns `tatara`.

## Defaults chosen (stated, not asked)

- neo4j image: `neo4j:5-community` (matches spec example; community single
  node).
- Shared OpenAI secret name: `lightrag-openai` (offered alt in the prompt;
  picked the more descriptive of the two).
- lightrag image is pinned by tag `v1.4.16` (the subchart's pin), not digest:
  the subchart itself pins by tag and the platform has no digest-pin convention
  elsewhere. If a digest is later required, it is a one-line value edit.
- Chart `appVersion` quoted (`"0.2.0"`) to match the existing `Chart.yaml`
  convention.

---

## Task 1 - Operator chart RBAC (rbac.yaml)

**File:** `tatara-operator/charts/tatara-operator/templates/rbac.yaml`

Add cnpg `clusters` (+`/status`) CRUD, `apps` `statefulsets` CRUD, core
`persistentvolumeclaims` CRUD; widen the existing core `secrets` rule from
read-only to add `create`/`update` (keep `get`/`list`/`watch`). Keep every
existing grant. All additions stay in the namespaced `Role` (ns `tatara`); the
`ClusterRole`/bindings are untouched.

### Edit A - widen the secrets rule

Replace:

```yaml
  # Project SCM Secrets (read only: token + webhookSecret).
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["get", "list", "watch"]
```

with:

```yaml
  # Project SCM Secrets (read: token + webhookSecret) + per-Project generated
  # memory Secrets (create/update: neo4j password, memory config Secret).
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
```

(`patch`/`delete` added alongside `create`/`update`: the reconciler SSA-applies
and owner-ref'd Secrets are cascade-deleted with the Project; without `delete`
the per-Project teardown would orphan them. This is the minimal verb set the N2
provisioner needs, not gold-plating.)

### Edit B - add the three new resource rules

Insert immediately after the `configmaps` rule (before the `secrets` rule), so
the per-Project memory grants are grouped:

```yaml
  # Per-Project memory provisioning (N4): cnpg Cluster, neo4j StatefulSet, PVCs.
  - apiGroups: ["postgresql.cnpg.io"]
    resources: ["clusters"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: ["postgresql.cnpg.io"]
    resources: ["clusters/status"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apps"]
    resources: ["statefulsets"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: [""]
    resources: ["persistentvolumeclaims"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
```

(`clusters/status` is read-only: the operator reads `readyInstances` to compute
`status.memory.phase`; cnpg's own controller writes the status. `deployments`
under `apps` are NOT added here because the chart already grants pods/services/
configmaps in core; verify in step Verify-1 that the existing chart does NOT
already grant `apps/deployments` and add it if N2's `Owns(&appsv1.Deployment{})`
needs it - see Verify-1 note.)

### Verify-1 - render and assert

```bash
helm template tatara-operator /Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator \
  --show-only templates/rbac.yaml | grep -nE "clusters|statefulsets|persistentvolumeclaims|deployments|secrets"
```

Expected (Role rules block): lines showing
`resources: ["clusters"]`, `resources: ["clusters/status"]`,
`resources: ["statefulsets"]`, `resources: ["persistentvolumeclaims"]`, and the
widened `resources: ["secrets"]` verbs including `create`/`update`.

```bash
helm template tatara-operator /Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator \
  --show-only templates/rbac.yaml | grep -c '"create", "update", "patch", "delete"'
```

Expected: count >= 4 (jobs already had it; pods/services, configmaps, secrets,
clusters, statefulsets, pvcs add more).

**Verify-1 note (deployments):** N2/N3 builders create per-Project lightrag +
memory `Deployment`s and the reconciler `Owns(&appsv1.Deployment{})`. Confirm the
operator already has `apps`/`deployments` CRUD. Run:

```bash
helm template tatara-operator /Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator \
  --show-only templates/rbac.yaml | grep -A1 '"deployments"' || echo "MISSING deployments grant"
```

If it prints `MISSING deployments grant`, add this rule alongside the
statefulsets rule (the manager creates Deployments for lightrag + memory):

```yaml
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
```

Re-run Verify-1. Do not skip this check: the M6 RBAC (per MEMORY.md) listed only
"pods/services/configmaps/secrets-read/networkpolicies" for core and nothing
under `apps`, so `apps/deployments` is almost certainly missing and MUST be
added here.

---

## Task 2 - Operator chart config: ConfigMap keys + Chart bump

**Files:**
- `tatara-operator/charts/tatara-operator/templates/_helpers.tpl`
  (`tatara-operator.envConfig`)
- `tatara-operator/charts/tatara-operator/values.yaml`
- `tatara-operator/charts/tatara-operator/Chart.yaml`

Per rule 6: each new input is a camelCase scalar in `values.yaml`, mapped to a
SCREAMING_SNAKE ConfigMap key in the `envConfig` helper, consumed via existing
`envFrom`. No lists, no plain ENVs. The ConfigMap and Secret templates are
unchanged (they already render via the helper / b64 the one secret).

### Edit A - _helpers.tpl envConfig

In `{{- define "tatara-operator.envConfig" -}}`, remove the `MEMORY_BASE_URL`
line and add the four new keys.

Replace:

```
MEMORY_BASE_URL: {{ .Values.memoryBaseUrl | quote }}
INGESTER_IMAGE: {{ .Values.ingesterImage | quote }}
```

with:

```
MEMORY_IMAGE: {{ .Values.memoryImage | quote }}
LIGHTRAG_IMAGE: {{ .Values.lightragImage | quote }}
NEO4J_IMAGE: {{ .Values.neo4jImage | quote }}
OPENAI_SECRET_NAME: {{ .Values.openaiSecretName | quote }}
INGESTER_IMAGE: {{ .Values.ingesterImage | quote }}
```

### Edit B - values.yaml

Replace:

```yaml
memoryBaseUrl: ""
ingesterImage: ""
```

with:

```yaml
# Per-Project memory stack images + shared OpenAI secret (rule 6: scalar ->
# SCREAMING_SNAKE ConfigMap key -> manager via envFrom). MEMORY_BASE_URL was
# removed in N3 (replaced by per-Project status.memory.endpoint).
memoryImage: ""
lightragImage: ""
neo4jImage: ""
openaiSecretName: ""
ingesterImage: ""
```

### Edit C - Chart.yaml bump to 0.2.0

Replace:

```yaml
version: 0.1.1
appVersion: "0.1.1"
```

with:

```yaml
version: 0.2.0
appVersion: "0.2.0"
```

### Verify-2 - lint + render the ConfigMap keys

```bash
helm lint /Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator
```

Expected: `1 chart(s) linted, 0 chart(s) failed`.

```bash
helm template tatara-operator /Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator \
  --show-only templates/configmap.yaml | grep -E "MEMORY_IMAGE|LIGHTRAG_IMAGE|NEO4J_IMAGE|OPENAI_SECRET_NAME|MEMORY_BASE_URL"
```

Expected: four lines `MEMORY_IMAGE: ""`, `LIGHTRAG_IMAGE: ""`,
`NEO4J_IMAGE: ""`, `OPENAI_SECRET_NAME: ""`; NO `MEMORY_BASE_URL` line.

```bash
grep -E "^version:|^appVersion:" /Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator/Chart.yaml
```

Expected: `version: 0.2.0` and `appVersion: "0.2.0"`.

---

## Task 3 - Vendor the cnpg Cluster CRD for envtest

**Goal:** N2's envtest must create `postgresql.cnpg.io/v1` `Cluster` objects.
envtest loads CRDs from `charts/tatara-operator/crds` (suite_test.go:35).

**NOTE — N2 already owns vendoring this CRD** (same path + `hack/vendor-cnpg-crd.sh`).
If N2 has landed, this task is VERIFY-ONLY: confirm
`charts/tatara-operator/crds/postgresql.cnpg.io_clusters.yaml` exists and that
`helm template` ships it; do NOT re-download or duplicate it. Only perform the
download below if N2 did not vendor it.

**File to create:**
`tatara-operator/charts/tatara-operator/crds/postgresql.cnpg.io_clusters.yaml`

### Pin reconciliation (do this first)

The vendored CRD must match the cnpg `api/v1` Go module N1 added. Determine the
module version and download the matching CRD:

```bash
grep cloudnative-pg /Users/szymonri/Documents/tatara/tatara-operator/go.mod
```

Take the version it prints (e.g. `v1.24.1`). Download the `Cluster` CRD from the
matching upstream release. The cnpg release ships a single manifest containing
all CRDs; extract just the `clusters` CRD:

```bash
CNPG_REF="v1.24.1"   # REPLACE with the version go.mod prints above
curl -fsSL "https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/${CNPG_REF}/config/crd/bases/postgresql.cnpg.io_clusters.yaml" \
  -o /Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator/crds/postgresql.cnpg.io_clusters.yaml
```

(If that path 404s for the chosen tag, fall back to the `cnpg-<ref>.yaml`
release manifest and split out the `Cluster` CRD document - the per-CRD file
under `config/crd/bases/` is the canonical source and exists for every modern
cnpg tag.)

### Add a provenance header

Prepend a comment recording the source so the vendored copy is traceable (this
is a test-only asset; document why it lives in `crds/`):

```yaml
# Vendored from cloudnative-pg/cloudnative-pg <CNPG_REF>
# config/crd/bases/postgresql.cnpg.io_clusters.yaml
# Reason: envtest (internal/controller/suite_test.go) creates cnpg Cluster
# objects to test per-Project memory provisioning. envtest installs every CRD in
# this dir; the cnpg operator itself is NOT deployed by this chart, so this CRD
# is loaded into the cluster only if cnpg is present (Helm skips re-applying an
# existing CRD). The <CNPG_REF> tag matches the cnpg api/v1 module in go.mod.
```

**Helm-install caveat (call out, do not work around):** Helm applies everything
in `crds/` on `helm install`. In the target cluster cnpg already owns this CRD,
and Helm does not upgrade/replace existing CRDs on `helm upgrade`, so the
vendored copy is inert in prod (envtest-only effect). This is the documented
behaviour, not tech-debt; recorded in MEMORY.md (Task 7). If a future cnpg-less
cluster is targeted, this CRD would bootstrap the type - acceptable.

### Verify-3 - CRD is valid and loadable

```bash
helm template tatara-operator /Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator >/dev/null && echo "template OK"
```

Expected: `template OK` (Helm does not render `crds/` but a parse error in the
dir surfaces; the real validation is the next two).

```bash
grep -E "^  name: clusters.postgresql.cnpg.io|kind: Cluster" \
  /Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator/crds/postgresql.cnpg.io_clusters.yaml
```

Expected: the CRD `metadata.name: clusters.postgresql.cnpg.io` and a
`kind: Cluster` line in `spec.names`.

```bash
cd /Users/szymonri/Documents/tatara/tatara-operator && make test 2>&1 | tail -20
```

Expected: envtest suite green, including any N2 test that creates a `Cluster`.
(If N2 tests are not yet merged when this task runs, this command just confirms
the existing suite still passes with the new CRD present - no regression.)

---

## Task 4 - Infra: remove static tatara-memory release + operator values

**Repo:** `/Users/szymonri/Documents/infra/helmfile`

### Edit A - helmfile.yaml.gotmpl: remove the tatara-memory release

**File:** `helmfiles/tatara/helmfile.yaml.gotmpl`

Remove the entire `tatara-memory` release block (keep `tatara-chat` and
`tatara-operator`). Also remove the now-stale `helmDefaults.timeout` comment that
references the tatara-memory cnpg bootstrap (it was the only reason for the 900s
wait); keep `timeout: 900` since other releases benefit, but fix the comment.

Replace:

```yaml
helmDefaults:
  wait: true
  # tatara-memory bootstraps a 3-instance cnpg postgres cluster on first
  # install (initdb + two replica joins), which exceeds helm's default 5m
  # wait. Give it room so the install converges instead of timing out.
  timeout: 900
```

with:

```yaml
helmDefaults:
  wait: true
  # Generous wait: image pulls + ServiceMonitor/CRD settling can exceed helm's
  # default 5m. (Static tatara-memory cnpg bootstrap retired in N4; per-Project
  # memory is now provisioned by tatara-operator, not helm.)
  timeout: 900
```

And remove this block entirely:

```yaml
- name: tatara-memory
  chart: oci://harbor.szymonrichert.pl/charts/tatara-memory
  namespace: tatara
  createNamespace: true
  version: 0.2.0
  labels:
    purpose: tatara
    application: tatara-memory
  <<: *default
```

(Leave the blank line layout so `tatara-chat` is the first release.)

### Edit B - bump the tatara-operator release pins to 0.2.0

In the same file, the `tatara-operator` release:

Replace:

```yaml
  version: 0.1.1
```

(within the `tatara-operator` release block - it is the one under
`application: tatara-operator`)

with:

```yaml
  version: 0.2.0
```

### Edit C - delete values/tatara-memory/

```bash
cd /Users/szymonri/Documents/infra/helmfile && git rm -r helmfiles/tatara/values/tatara-memory/
```

(removes `common.yaml`, `default.yaml`, `default.secrets.yaml`.)

### Edit D - operator infra values: drop memoryBaseUrl, add image pins

**File:** `helmfiles/tatara/values/tatara-operator/default.yaml`

Replace:

```yaml
# In-cluster service endpoints.
memoryBaseUrl: "http://tatara-memory.tatara.svc:8080"
oidcIssuer: "https://auth.szymonrichert.pl/realms/master"
```

with:

```yaml
# Per-Project memory stack images (the operator stamps these into the native
# objects it provisions per Project). memoryBaseUrl removed in N4: each Project
# now exposes its own endpoint via status.memory.endpoint.
memoryImage: "harbor.szymonrichert.pl/containers/tatara-memory:0.2.0"
lightragImage: "ghcr.io/hkuds/lightrag:v1.4.16"
neo4jImage: "neo4j:5-community"
# Shared OpenAI Secret (ns tatara, key LLM_BINDING_API_KEY) every per-Project
# lightrag reads. Created out-of-band (gated step in this plan).
openaiSecretName: "lightrag-openai"

# In-cluster service endpoints.
oidcIssuer: "https://auth.szymonrichert.pl/realms/master"
```

**File:** `helmfiles/tatara/values/tatara-operator/common.yaml`

Replace:

```yaml
image:
  tag: "0.1.1"
```

with:

```yaml
image:
  tag: "0.2.0"
```

(`common.yaml` carries no `memoryBaseUrl`, so nothing to drop there; the image
tag pin is the only change.)

### Verify-4 - helmfile diff (read-only) renders cleanly

> `helmfile diff` is read-only (no apply). It is safe in Task 4. The chart must
> be published to OCI first for a full diff; if `helm pull` of the 0.2.0 chart
> fails (not yet pushed), this verification is deferred to the gated runbook step
> that runs after the chart push. Run it now if the 0.2.0 chart is already in
> Harbor; otherwise note "deferred to runbook step 5".

```bash
cd /Users/szymonri/Documents/infra/helmfile && \
  helmfile -e default -f helmfiles/tatara/helmfile.yaml.gotmpl -l application=tatara-operator diff 2>&1 | tail -40
```

Expected (once 0.2.0 chart is published): ConfigMap diff showing
`MEMORY_BASE_URL` removed and `MEMORY_IMAGE`/`LIGHTRAG_IMAGE`/`NEO4J_IMAGE`/
`OPENAI_SECRET_NAME` added; Role diff showing the new cnpg/statefulset/pvc rules
and widened secrets verbs; image tag `0.2.0`.

Static check that the release is gone and no stale refs remain:

```bash
cd /Users/szymonri/Documents/infra/helmfile && \
  helmfile -e default -f helmfiles/tatara/helmfile.yaml.gotmpl list 2>&1 | grep -E "tatara-memory|tatara-chat|tatara-operator"
```

Expected: `tatara-chat` and `tatara-operator` listed; NO `tatara-memory`.

```bash
grep -rn "memoryBaseUrl\|tatara-memory" /Users/szymonri/Documents/infra/helmfile/helmfiles/tatara/ || echo "no stale refs"
```

Expected: `no stale refs` (the helmfile release block, values dir, and the
memoryBaseUrl key are all gone).

---

## Task 5 - Shared OpenAI Secret (PREPARE only; creation is gated)

The shared `lightrag-openai` Secret (ns `tatara`, key `LLM_BINDING_API_KEY`)
holds the OpenAI/LLM key every per-Project lightrag reads. It is NOT chart-
rendered (rule 14: no replicated-secret names baked) and NOT hand-edited into
sops in this task.

**This task only documents the gated creation.** Do not run it. The actual
creation is runbook step 2.

Two acceptable mechanisms (pick the one matching the existing platform pattern -
the other tatara secrets like `tatara-anthropic` use reflector/sops-encrypted
manifests per the M6 ROADMAP gated steps 8-10):

1. **sops-encrypted manifest** (preferred, matches `tatara-cli-oidc` /
   `tatara-anthropic`): create a sops-encrypted Secret manifest and apply it.
   Use the `sops-secret-helper` skill to author/edit the encrypted file - do NOT
   hand-edit sops. Key: `LLM_BINDING_API_KEY`. The plaintext key value comes
   from the operator's existing OpenAI credential source (same value the retired
   static `values/tatara-memory/default.secrets.yaml` carried for lightrag's
   openai key - recover it from that sops file BEFORE Task 4 Edit C deletes it,
   or from the live cluster Secret `tatara-memory-*` openai key).

   > Adjacent-cleanup note for the human running Task 4 Edit C: the retired
   > `values/tatara-memory/default.secrets.yaml` is the source of truth for the
   > current lightrag OpenAI key. Extract it (via `sops-secret-helper`) BEFORE
   > `git rm`, or pull it from the live cluster, so the new `lightrag-openai`
   > Secret reuses the same key.

2. **kubectl literal** (only if the platform creates other tatara secrets
   imperatively): `kubectl -n tatara create secret generic lightrag-openai
   --from-literal=LLM_BINDING_API_KEY=<key>`. Listed for completeness; prefer
   mechanism 1.

### Verify-5 - documentation completeness (no live action)

This task produces no file change of its own; it is validated by the runbook
referencing it. Confirm the runbook step 2 names the exact Secret/key/ns. No
command.

---

## Task 6 - Gated deploy runbook (HUMAN-RUN, ORDERED; do NOT execute in a task)

Run only after Tasks 1-5 are merged to the respective repo `main` branches and a
human has reviewed the diffs. Each step is gated; stop and present output before
the next destructive step.

1. **Publish the operator image 0.2.0.** From `tatara-operator` `main` (not a
   worktree, rule 10):
   build + push `harbor.szymonrichert.pl/containers/tatara-operator:0.2.0`.
   (Use the repo's existing image build target / Dockerfile, same flow as the
   M6 gated step 2 that pushed 0.1.0.)

2. **Create the shared OpenAI Secret** `lightrag-openai` in ns `tatara` with key
   `LLM_BINDING_API_KEY` (Task 5; via `sops-secret-helper` + apply, reusing the
   key recovered from the retiring `values/tatara-memory/default.secrets.yaml`).
   Verify:
   `kubectl -n tatara get secret lightrag-openai -o jsonpath='{.data.LLM_BINDING_API_KEY}' | head -c 8`
   prints non-empty.

3. **Publish the operator chart 0.2.0** to Harbor. From `tatara-operator` `main`:
   ```bash
   helm package /Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator -d /tmp && \
   helm push /tmp/tatara-operator-0.2.0.tgz oci://harbor.szymonrichert.pl/charts
   ```

4. **Confirm prerequisites for per-Project memory exist in-cluster:** cnpg
   operator is installed (the per-Project `Cluster` objects need cnpg's
   controller), and the `lightrag-openai` Secret from step 2 is present.
   ```bash
   kubectl get crd clusters.postgresql.cnpg.io && \
   kubectl -n tatara get secret lightrag-openai
   ```
   Both must succeed. If cnpg is absent, install/confirm it before continuing
   (the retired static tatara-memory pulled cnpg via its `cluster` subchart;
   per-Project provisioning assumes a cluster-wide cnpg operator is already
   present - verify with the human).

5. **Review the helmfile diff** (read-only gate):
   ```bash
   cd /Users/szymonri/Documents/infra/helmfile && \
   helmfile -e default -f helmfiles/tatara/helmfile.yaml.gotmpl -l application=tatara-operator diff
   ```
   Expect: image 0.2.0; ConfigMap loses `MEMORY_BASE_URL`, gains the four image/
   secret keys; Role gains cnpg/statefulset/pvc rules + widened secrets verbs.
   Present to the human. Do not proceed without approval.

6. **Apply the operator release** (GATED - destructive):
   ```bash
   cd /Users/szymonri/Documents/infra/helmfile && \
   helmfile -e default -f helmfiles/tatara/helmfile.yaml.gotmpl -l application=tatara-operator apply
   ```
   Then confirm the manager rolled to 0.2.0:
   `kubectl -n tatara rollout status deploy/tatara-operator`.

7. **Retire the static stack** (GATED - destructive; only after step 6 is
   healthy). The static release is empty (`code_entities=0`, per
   `docs/2026-06-06-code-graph-production-readiness.md`); no data migration.
   ```bash
   helm uninstall tatara-memory -n tatara
   ```
   This removes the static tatara-memory app, its lightrag/neo4j subcharts, and
   the cnpg `Cluster` `tatara-memory-postgres` it owned. `tatara-chat` and the
   operator are untouched. Verify:
   `kubectl -n tatara get all,cluster.postgresql.cnpg.io | grep -i tatara-memory`
   returns nothing.

8. **Verify a Project provisions its own memory** (acceptance):
   Apply/select a test `Project` and watch the operator provision the per-Project
   stack:
   ```bash
   kubectl -n tatara get project <proj> -o jsonpath='{.status.memory.phase}'
   ```
   Expect `Provisioning` then `Ready`. Then:
   ```bash
   kubectl -n tatara get cluster.postgresql.cnpg.io,statefulset,deploy,svc,pvc -l tatara.dev/project=<proj>
   ```
   Expect the `mem-<proj>-*` family (cnpg Cluster `mem-<proj>-pg`, neo4j STS
   `mem-<proj>-neo4j`, lightrag + memory Deployments, Services, PVCs).
   ```bash
   kubectl -n tatara get project <proj> -o jsonpath='{.status.memory.endpoint}'
   ```
   Expect `http://mem-<proj>.tatara.svc:8080`. Deleting the Project cascade-
   deletes the whole `mem-<proj>-*` family (owner-refs).

---

## Task 7 - ROADMAP / MEMORY updates

### tatara-operator/ROADMAP.md

Mark N4 done under the per-project-memory milestones (add the line if the N-series
list does not yet exist; N1-N3 plans add their own):

```markdown
- [x] N4 retire static tatara-memory + chart RBAC/values + image bump + deploy -
  operator chart Role gains cnpg clusters(+/status)/statefulsets/pvcs CRUD and
  widened secrets verbs; ConfigMap drops MEMORY_BASE_URL, adds MEMORY_IMAGE/
  LIGHTRAG_IMAGE/NEO4J_IMAGE/OPENAI_SECRET_NAME; Chart+appVersion 0.2.0; cnpg
  Cluster CRD vendored into charts/.../crds for envtest; infra removes the
  static tatara-memory release + values dir, bumps operator pins to 0.2.0, adds
  the image/secret values. Deploy + static-stack uninstall are gated.
  Plan: docs/superpowers/plans/2026-06-07-per-project-memory-n4-retire-deploy.md.
```

Add a gated follow-on block mirroring the M6 pattern (the runbook in Task 6):

```markdown
## N4 deploy follow-ons (gated - require human action in this order)

1. [ ] Build + push harbor.szymonrichert.pl/containers/tatara-operator:0.2.0.
2. [ ] Create shared Secret lightrag-openai (ns tatara, key LLM_BINDING_API_KEY)
   via sops-secret-helper, reusing the key from the retiring tatara-memory sops.
3. [ ] helm package + push tatara-operator-0.2.0.tgz to oci://.../charts.
4. [ ] Confirm cnpg operator + lightrag-openai present in-cluster.
5. [ ] helmfile -e default -l application=tatara-operator diff (review).
6. [ ] helmfile -e default -l application=tatara-operator apply.
7. [ ] helm uninstall tatara-memory -n tatara (empty static stack).
8. [ ] Verify a Project provisions mem-<proj>-* and reaches status.memory Ready.
```

### tatara-operator/MEMORY.md (append, dated)

```markdown
- 2026-06-07 (N4) Static tatara-memory retired; per-Project memory is operator-
  provisioned. Chart 0.2.0: Role adds postgresql.cnpg.io clusters(+/status),
  apps/statefulsets, core/persistentvolumeclaims CRUD; secrets verbs widened to
  create/update/patch/delete (was read-only) for generated neo4j password +
  memory config Secrets; apps/deployments confirmed/added for lightrag+memory.
  ConfigMap drops MEMORY_BASE_URL (N3), adds MEMORY_IMAGE/LIGHTRAG_IMAGE/
  NEO4J_IMAGE/OPENAI_SECRET_NAME. cnpg Cluster CRD vendored into
  charts/tatara-operator/crds (envtest-only; Helm skips existing CRDs in prod
  where cnpg owns it). Image pins: tatara-memory:0.2.0, lightrag v1.4.16
  (subchart pin, tag not digest), neo4j:5-community (native single-node STS, not
  the upstream neo4j chart). Shared OpenAI secret name: lightrag-openai.
```

### parent tatara/MEMORY.md (append, dated)

```markdown
- 2026-06-07 (N4) Architecture change: tatara-memory is no longer a statically
  helm-deployed shared service. The tatara-operator provisions a full isolated
  memory stack (cnpg pg + neo4j + lightrag + tatara-memory) per Project, native
  objects owner-ref'd to the Project, in the shared tatara ns (mem-<proj>-*).
  The static tatara-memory release is removed from the infra helmfile tatara
  bucket. The tatara-memory *chart* remains in its repo as the reference the
  native builders are ported from; it is not deployed.
```

### parent tatara/ROADMAP.md

Remove/close any "shared static tatara-memory" item; if the per-project-memory
N-series is tracked here, mark N4 complete. (One-line edit; verify against the
file's current contents at execution time.)

### Verify-7

```bash
grep -n "N4" /Users/szymonri/Documents/tatara/tatara-operator/ROADMAP.md \
  /Users/szymonri/Documents/tatara/tatara-operator/MEMORY.md \
  /Users/szymonri/Documents/tatara/MEMORY.md
```

Expected: N4 entries present in all three.

---

## Commit plan (per repo; conventional commits; gated deploy excluded)

- `tatara-operator` (Tasks 1, 2, 3, 7-operator):
  `feat(chart): per-Project memory RBAC + image config; bump 0.2.0; vendor cnpg Cluster CRD`
  Pre-commit: `helm lint` (Verify-2), `make test` (Verify-3),
  `pre-commit run --all-files`.
- `infra` (Tasks 4, 7 has no infra file): two logical commits or one:
  `chore(helmfile): retire static tatara-memory; bump tatara-operator to 0.2.0; add per-Project memory image values`
  Pre-commit: `pre-commit run --all-files` (yaml/sops hooks); helmfile diff is
  gated (runbook step 5).
- `tatara` parent (Task 7 docs):
  `docs: N4 architecture change - per-Project memory provisioning replaces static tatara-memory`

Each repo: branch off `main` (`feat/per-project-memory-n4` /
`chore/retire-static-tatara-memory` / `docs/per-project-memory-n4`), code-review
before commit (superpowers:requesting-code-review), apply critical/high fixes,
`pre-commit run --all-files`, then commit. Do NOT push/apply the deploy runbook.

## Done criteria (verification-before-completion)

- Verify-1: Role renders cnpg clusters(+/status), statefulsets, pvcs,
  deployments, widened secrets. PASS.
- Verify-2: `helm lint` 0 failed; ConfigMap has 4 new keys, no MEMORY_BASE_URL;
  Chart 0.2.0. PASS.
- Verify-3: cnpg Cluster CRD present + named correctly; `make test` green. PASS.
- Verify-4: helmfile lists no tatara-memory; no stale memoryBaseUrl/tatara-memory
  refs; operator pins 0.2.0; diff (when chart published) shows expected deltas.
  PASS.
- Verify-7: N4 entries in both repos' ROADMAP/MEMORY + parent MEMORY. PASS.
- Gated runbook (Task 6): NOT executed by any task; recorded for human run.
```
