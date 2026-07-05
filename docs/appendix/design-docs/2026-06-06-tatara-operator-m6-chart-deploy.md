# tatara-operator M6 (chart hardening + deploy wiring) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. M6 is config/manifest work, not Go-TDD: each task shows the EXACT file contents (or precise diffs) and a verification command with expected output. The FINAL deploy step (`helmfile -e default diff`) is GATED: it requires explicit user confirmation before any `apply`. Do NOT run `helmfile apply` or `terraform apply` as part of this plan.

**Goal:** Harden and finish the `tatara-operator` Helm chart (from the M0 skeleton), wire up cluster-agnostic deploy, and stand up the Keycloak + infra-helmfile plumbing so the operator can run in the `tatara` namespace. Specifically: finalize the manager Deployment (ConfigMap + Secret `envFrom`), ServiceAccount + RBAC (CRDs + jobs/pods/services/secrets-read/configmaps/networkpolicies), Service (REST + webhook on `HTTP_ADDR`) + metrics, ServiceMonitor, Ingress (host/class supplied by infra, not the chart), the CRDs; a templated egress-allowlist NetworkPolicy applied to spawned agent + ingest pods; the M1 ingest-result ConfigMap RBAC follow-up (`tatara-ingest` SA + Role); the `tatara-operator` confidential Keycloak client + `tatara-operator` audience mapper; and the `tatara-operator` release in the infra helmfile `tatara` bucket.

**Architecture:** The chart is created via `helm create tatara-operator` then edited (rule 5) and is cluster-agnostic (rule 14): no baked regcred, node affinity, ingress host/class, storage class, or replicated-secret names live in `values.yaml`. All cluster-specific customization comes from `~/Documents/infra/helmfile` (per-bucket `values/common.yaml` + per-release `values/tatara-operator/{common,default}.yaml` + sops `default.secrets.yaml`). Config follows rule 6: each input is a camelCase scalar in `values.yaml` mapped through one `envConfig` helper to a kebab/SCREAMING_SNAKE ConfigMap key, consumed by the manager via `envFrom`; secrets follow the same path through a templated `Secret`. The four CRDs (Project/Repository/Task/Subtask) are installed by the chart under `charts/tatara-operator/crds/` (helm installs `crds/` before templates, no templating). The egress-allowlist NetworkPolicy is a chart-templated object whose `podSelector` matches the `tatara.dev/managed-by: tatara-operator` label that M1's ingest Job pod template and M4's agent Pod MUST carry (flagged below). RBAC is split: the manager's ClusterRole/Role (broad reconcile rights) and the narrow `tatara-ingest` Role (ConfigMap patch only) for the ingest Job's dedicated ServiceAccount.

**Tech Stack:** Helm 3/4, `helm lint`, `helm template`, `kubeconform` (or `helm template` object-count assertion where kubeconform is unavailable), Terraform + `mrparkers/keycloak` provider (`terraform fmt -check`, `terraform validate`), helmfile + sops (`helmfile -e default diff` as the gated final step). All chart objects mirror `~/Documents/tatara/tatara-memory/charts/tatara-memory/` exactly (configmap `envFrom`, templated secret, deployment, serviceaccount, servicemonitor, networkpolicy, ingress, `_helpers.tpl`).

**Spec:** `~/Documents/tatara/docs/superpowers/specs/2026-06-06-tatara-operator-design.md` (Security, observability, deploy section).
**Pin set (authoritative names/paths - obey exactly):** `~/Documents/tatara/docs/superpowers/plans/_tatara-operator-shared-contracts.md` (Deploy section).

**Reference sources on disk (read these, do not guess):**
- Chart structure to mirror EXACTLY: `~/Documents/tatara/tatara-memory/charts/tatara-memory/` (`templates/{_helpers.tpl,configmap.yaml,deployment.yaml,secret.yaml,service.yaml,serviceaccount.yaml,servicemonitor.yaml,networkpolicy.yaml,ingress.yaml}`, `values.yaml`, `Chart.yaml`).
- Helmfile release wiring: `~/Documents/infra/helmfile/helmfiles/tatara/helmfile.yaml.gotmpl`, `~/Documents/infra/helmfile/helmfiles/tatara/values/common.yaml`, `~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-memory/common.yaml`, `~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-chat/default.yaml`.
- Keycloak clients to mirror: `~/Documents/infra/terraform/keycloak/tatara_clients.tf` (read it; M6 adds a `tatara-operator` confidential service-account client + a `tatara-operator` audience mapper on the `tatara` scope).
- sops config: `~/Documents/infra/helmfile/.sops.yaml` (rule `path_regex: default.secrets.yaml` + `.*.secret.*.yaml`, PGP key `D39E46932A270AA3BA490B9DB9FE928D3E8BCED8`).

**Repo dirs / paths:**
- Operator chart: `~/Documents/tatara/tatara-operator/charts/tatara-operator/`.
- Keycloak terraform: `~/Documents/infra/terraform/keycloak/`.
- Infra helmfile tatara bucket: `~/Documents/infra/helmfile/helmfiles/tatara/`.

**Preconditions (built in M0-M5, assumed present):**
- M0 produced a `helm lint`-clean chart skeleton at `charts/tatara-operator/` with the four CRDs under `charts/tatara-operator/crds/` (generated by `make manifests` via controller-gen). M6 edits that skeleton; if a skeleton object is missing, M6 creates it to match tatara-memory.
- `internal/config.Config` env scalars (pin set): `HTTP_ADDR`, `METRICS_ADDR`, `INTERNAL_ADDR`, `OIDC_ISSUER`, `OIDC_AUDIENCE` (=`tatara-operator`), `MEMORY_BASE_URL`, `INGESTER_IMAGE`, `EXTERNAL_WEBHOOK_BASE`, `OPERATOR_OIDC_CLIENT_ID`, `OPERATOR_OIDC_CLIENT_SECRET`, `ANTHROPIC_SECRET_NAME`, `CLI_OIDC_SECRET_NAME`, `LOG_LEVEL`. Plus `Namespace` (default `tatara`).
- M1 ingest Job runs under ServiceAccount `tatara-ingest` and patches `<repo>-ingest-result` ConfigMaps; M1 MEMORY flagged the SA + Role as an M6 deliverable.
- M4 agent Pod + Service are spawned by the Task reconciler; M4 builds the pod via `internal/agent/pod.go`.

**Environment naming:** The infra helmfile `tatara` bucket uses env name `default` (existing files: `values/tatara-memory/default.yaml`, `default.secrets.yaml`). All M6 helmfile additions use `default` as the env. Substitute the real env name if the bucket is later run under a different `-e`.

---

## Conventions used throughout this plan

- All paths absolute. Chart work happens in a worktree off `tatara-operator` `main`, merged back to `main`; NEVER build/deploy from a worktree (rule 10). Chart-publish (OCI push) and the helmfile diff/apply run from `main`.
- Each chart task ends with `helm lint` + a targeted `helm template` assertion. Terraform tasks end with `terraform fmt -check` + `terraform validate`. The helmfile task ends with the gated `helmfile -e default diff`.
- Mirror tatara-memory chart style verbatim: same `_helpers.tpl` macro names (renamed to `tatara-operator.*`), same `envFrom` ConfigMap+Secret ordering, same label includes.
- Conventional commits on the M6 branch. Cross-repo: chart + RBAC + NetworkPolicy land in `tatara-operator`; the Keycloak client lands in `infra/terraform`; the release wiring lands in `infra/helmfile`. These are three separate repos with three separate commits/PRs.

---

## Task 1: Chart metadata, values.yaml, and _helpers.tpl

**Files:**
- Modify/Create: `~/Documents/tatara/tatara-operator/charts/tatara-operator/Chart.yaml`
- Modify/Create: `~/Documents/tatara/tatara-operator/charts/tatara-operator/values.yaml`
- Modify/Create: `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/_helpers.tpl`

The operator chart has NO subchart dependencies (unlike tatara-memory). It carries only its own manager workload + CRDs + RBAC + NetworkPolicy.

- [ ] **Step 1: Write `Chart.yaml`**

Create/overwrite `~/Documents/tatara/tatara-operator/charts/tatara-operator/Chart.yaml`:
```yaml
apiVersion: v2
name: tatara-operator
description: Kubernetes operator orchestrating the tatara agentic-development loop (Project/Repository/Task/Subtask CRDs)
type: application
version: 0.1.0
appVersion: "0.1.0"
```

- [ ] **Step 2: Write `values.yaml` (rule 6: scalars only, no plain ENVs, no lists except cluster-supplied passthrough)**

Create/overwrite `~/Documents/tatara/tatara-operator/charts/tatara-operator/values.yaml`:
```yaml
image:
  repository: harbor.szymonrichert.pl/containers/tatara-operator
  tag: ""
  pullPolicy: IfNotPresent

# imagePullSecrets are cluster-specific; the deploying helmfile supplies them.
imagePullSecrets: []

replicaCount: 1

# Listener/config scalars. Each maps 1:1 to a SCREAMING_SNAKE ConfigMap key
# via the envConfig helper and is consumed by the manager through envFrom.
httpAddr: ":8080"
metricsAddr: ":9090"
internalAddr: ":8081"
oidcIssuer: "https://auth.szymonrichert.pl/realms/master"
oidcAudience: "tatara-operator"
memoryBaseUrl: "http://tatara-memory.tatara.svc:8080"
ingesterImage: "harbor.szymonrichert.pl/containers/tatara-memory-repo-ingester:latest"
externalWebhookBase: ""
operatorOidcClientId: "tatara-operator"
anthropicSecretName: "tatara-anthropic"
cliOidcSecretName: "tatara-cli-oidc"
logLevel: "info"

# Secret-backed scalars. Provided by a SOPS-encrypted values overlay at deploy
# time; the chart Secret renders empty placeholders when unset. Set
# existingSecret to point envFrom at an externally-managed Secret instead.
existingSecret: ""
operatorOidcClientSecret: ""

service:
  type: ClusterIP
  httpPort: 8080
  metricsPort: 9090

ingress:
  enabled: false
  className: nginx
  host: ""
  path: "/"
  clusterIssuer: letsencrypt-prod
  tlsSecretName: ""

serviceMonitor:
  enabled: true
  interval: "30s"
  scrapeTimeout: "10s"

# managedPodNetworkPolicy is the egress-allowlist applied to spawned agent +
# ingest pods (selected by tatara.dev/managed-by=tatara-operator). Endpoints
# are cluster service DNS / labels; SCM egress is the broad 443 rule.
managedPodNetworkPolicy:
  enabled: true
  # tatara-chat service the agent's tatara-cli reaches over HTTP.
  chatServiceName: tatara-chat

rbac:
  # create the manager ClusterRole/RoleBinding and the tatara-ingest Role/SA.
  create: true

serviceAccount:
  create: true
  annotations: {}
  name: ""

ingestServiceAccount:
  # dedicated SA the M1 ingest Job runs under; Role grants ConfigMap patch only.
  create: true
  name: "tatara-ingest"

podSecurityContext:
  runAsNonRoot: true
  runAsUser: 65532
  runAsGroup: 65532
  fsGroup: 65532

containerSecurityContext:
  allowPrivilegeEscalation: false
  capabilities:
    drop: [ALL]
  readOnlyRootFilesystem: true

resources:
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    memory: 256Mi
```

Note (rule 6 compliance): `imagePullSecrets`, `capabilities.drop`, and `managedPodNetworkPolicy.*` scalars are the only structured values. `imagePullSecrets` is the documented cluster-supplied passthrough (matches tatara-memory). `capabilities.drop: [ALL]` mirrors tatara-memory verbatim. No env-shaped lists exist; all config is scalar through `envConfig`.

- [ ] **Step 3: Write `_helpers.tpl` (mirror tatara-memory, rename to tatara-operator, add envConfig with pin-set keys)**

Create/overwrite `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/_helpers.tpl`:
```yaml
{{/*
Expand the name of the chart.
*/}}
{{- define "tatara-operator.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "tatara-operator.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "tatara-operator.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "tatara-operator.labels" -}}
helm.sh/chart: {{ include "tatara-operator.chart" . }}
{{ include "tatara-operator.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "tatara-operator.selectorLabels" -}}
app.kubernetes.io/name: {{ include "tatara-operator.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "tatara-operator.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "tatara-operator.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Map camelCase values.* scalars to SCREAMING_SNAKE ConfigMap keys.
Strict: values.yaml carries only scalars; this macro is the single mapping point.
*/}}
{{- define "tatara-operator.envConfig" -}}
HTTP_ADDR: {{ .Values.httpAddr | quote }}
METRICS_ADDR: {{ .Values.metricsAddr | quote }}
INTERNAL_ADDR: {{ .Values.internalAddr | quote }}
OIDC_ISSUER: {{ .Values.oidcIssuer | quote }}
OIDC_AUDIENCE: {{ .Values.oidcAudience | quote }}
MEMORY_BASE_URL: {{ .Values.memoryBaseUrl | quote }}
INGESTER_IMAGE: {{ .Values.ingesterImage | quote }}
EXTERNAL_WEBHOOK_BASE: {{ .Values.externalWebhookBase | quote }}
OPERATOR_OIDC_CLIENT_ID: {{ .Values.operatorOidcClientId | quote }}
ANTHROPIC_SECRET_NAME: {{ .Values.anthropicSecretName | quote }}
CLI_OIDC_SECRET_NAME: {{ .Values.cliOidcSecretName | quote }}
LOG_LEVEL: {{ .Values.logLevel | quote }}
{{- end -}}
```

Note: `OPERATOR_OIDC_CLIENT_SECRET` is NOT in `envConfig`; it is a secret value rendered by `templates/secret.yaml` (Task 2) and consumed via the Secret `envFrom`.

- [ ] **Step 4: Verify lint after metadata/values/helpers exist (templates land in later tasks)**

The chart will not fully lint until the templates exist; this step just confirms the YAML parses. Run:
```bash
helm lint ~/Documents/tatara/tatara-operator/charts/tatara-operator
```
Expected at this point: lint may report `[ERROR]` for missing templates IF you run it before Tasks 2-9; that is fine. Re-run lint clean at Task 10. To validate just the values/helpers parse now:
```bash
helm template t ~/Documents/tatara/tatara-operator/charts/tatara-operator --show-only templates/_helpers.tpl 2>&1 | head -1
```
Expected: no YAML parse error (template helpers render nothing, command exits 0 with empty output or a "could not find template" note - both acceptable).

- [ ] **Step 5: Commit**
```bash
git -C ~/Documents/tatara/tatara-operator add charts/tatara-operator/Chart.yaml charts/tatara-operator/values.yaml charts/tatara-operator/templates/_helpers.tpl
git -C ~/Documents/tatara/tatara-operator commit -m "feat(chart): tatara-operator chart metadata, values, helpers"
```

---

## Task 2: ConfigMap, Secret, ServiceAccount

**Files:**
- Create: `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/configmap.yaml`
- Create: `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/secret.yaml`
- Create: `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/serviceaccount.yaml`

- [ ] **Step 1: ConfigMap (mirror tatara-memory)**

Create `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/configmap.yaml`:
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "tatara-operator.fullname" . }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
data:
  {{- include "tatara-operator.envConfig" . | nindent 2 }}
```

- [ ] **Step 2: Secret (templated, mirrors tatara-memory secret.yaml; key kebab-cased)**

Create `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/secret.yaml`:
```yaml
{{- if not .Values.existingSecret }}
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "tatara-operator.fullname" . }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
type: Opaque
data:
  # operator-oidc-client-secret is a placeholder; the real value lands from a
  # SOPS-encrypted values overlay at deploy time. The manager reads it via the
  # Secret envFrom as OPERATOR_OIDC_CLIENT_SECRET (key normalized below).
  OPERATOR_OIDC_CLIENT_SECRET: {{ .Values.operatorOidcClientSecret | b64enc | quote }}
{{- end }}
```
Note: the Secret data key is the literal env var name `OPERATOR_OIDC_CLIENT_SECRET` so the Deployment `envFrom: secretRef` injects it directly (no rename needed). This matches the pin-set config name.

- [ ] **Step 3: ServiceAccount (manager SA; mirror tatara-memory)**

Create `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/serviceaccount.yaml`:
```yaml
{{- if .Values.serviceAccount.create -}}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ include "tatara-operator.serviceAccountName" . }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
  {{- with .Values.serviceAccount.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
{{- end }}
```

- [ ] **Step 4: Verify render**
```bash
helm template t ~/Documents/tatara/tatara-operator/charts/tatara-operator \
  --show-only templates/configmap.yaml --show-only templates/secret.yaml --show-only templates/serviceaccount.yaml
```
Expected: three objects render. ConfigMap `data` contains all 12 `envConfig` keys (`HTTP_ADDR`, `METRICS_ADDR`, `INTERNAL_ADDR`, `OIDC_ISSUER`, `OIDC_AUDIENCE`, `MEMORY_BASE_URL`, `INGESTER_IMAGE`, `EXTERNAL_WEBHOOK_BASE`, `OPERATOR_OIDC_CLIENT_ID`, `ANTHROPIC_SECRET_NAME`, `CLI_OIDC_SECRET_NAME`, `LOG_LEVEL`). Secret `data` has `OPERATOR_OIDC_CLIENT_SECRET`. Assert the count:
```bash
helm template t ~/Documents/tatara/tatara-operator/charts/tatara-operator --show-only templates/configmap.yaml | grep -cE '^\s+(HTTP_ADDR|METRICS_ADDR|INTERNAL_ADDR|OIDC_ISSUER|OIDC_AUDIENCE|MEMORY_BASE_URL|INGESTER_IMAGE|EXTERNAL_WEBHOOK_BASE|OPERATOR_OIDC_CLIENT_ID|ANTHROPIC_SECRET_NAME|CLI_OIDC_SECRET_NAME|LOG_LEVEL):'
```
Expected: `12`.

- [ ] **Step 5: Commit**
```bash
git -C ~/Documents/tatara/tatara-operator add charts/tatara-operator/templates/configmap.yaml charts/tatara-operator/templates/secret.yaml charts/tatara-operator/templates/serviceaccount.yaml
git -C ~/Documents/tatara/tatara-operator commit -m "feat(chart): configmap, secret, serviceaccount"
```

---

## Task 3: Manager Deployment (ConfigMap + Secret envFrom)

**Files:**
- Create: `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/deployment.yaml`

- [ ] **Step 1: Write the Deployment (mirror tatara-memory; two ports http+metrics; no inline PG env)**

Create `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/deployment.yaml`:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "tatara-operator.fullname" . }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.replicaCount }}
  selector:
    matchLabels:
      {{- include "tatara-operator.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "tatara-operator.selectorLabels" . | nindent 8 }}
      annotations:
        checksum/config: {{ include "tatara-operator.envConfig" . | sha256sum }}
    spec:
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      serviceAccountName: {{ include "tatara-operator.serviceAccountName" . }}
      {{- with .Values.podSecurityContext }}
      securityContext:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      containers:
        - name: tatara-operator
          {{- with .Values.containerSecurityContext }}
          securityContext:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          ports:
            - name: http
              containerPort: 8080
              protocol: TCP
            - name: metrics
              containerPort: 9090
              protocol: TCP
          envFrom:
            - configMapRef:
                name: {{ include "tatara-operator.fullname" . }}
            - secretRef:
                name: {{ default (include "tatara-operator.fullname" .) .Values.existingSecret }}
          livenessProbe:
            httpGet:
              path: /healthz
              port: metrics
            initialDelaySeconds: 5
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /readyz
              port: metrics
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
```
Note: `/healthz` and `/readyz` live on the metrics listener (`METRICS_ADDR` :9090), matching the M0 manager which exposes health + `/metrics` together. REST + webhook share `HTTP_ADDR` :8080 (`http` port). If M0 instead put health on `HTTP_ADDR`, change the probe `port` to `http`; verify against `cmd/manager/main.go` before committing (boy-scout, rule 3).

- [ ] **Step 2: Verify render**
```bash
helm template t ~/Documents/tatara/tatara-operator/charts/tatara-operator --show-only templates/deployment.yaml
```
Expected: one Deployment. `envFrom` has both a `configMapRef` and a `secretRef` named `t-tatara-operator`. Two container ports `http`(8080) and `metrics`(9090). Assert envFrom wiring:
```bash
helm template t ~/Documents/tatara/tatara-operator/charts/tatara-operator --show-only templates/deployment.yaml | grep -cE 'configMapRef|secretRef'
```
Expected: `2`.

- [ ] **Step 3: Commit**
```bash
git -C ~/Documents/tatara/tatara-operator add charts/tatara-operator/templates/deployment.yaml
git -C ~/Documents/tatara/tatara-operator commit -m "feat(chart): manager Deployment with configmap+secret envFrom"
```

---

## Task 4: Service + metrics, ServiceMonitor

**Files:**
- Create: `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/service.yaml`
- Create: `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/servicemonitor.yaml`

- [ ] **Step 1: Service (two ports: http for REST+webhook, metrics for /metrics)**

Create `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/service.yaml`:
```yaml
apiVersion: v1
kind: Service
metadata:
  name: {{ include "tatara-operator.fullname" . }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
spec:
  type: {{ .Values.service.type }}
  ports:
    - name: http
      port: {{ .Values.service.httpPort }}
      targetPort: http
      protocol: TCP
    - name: metrics
      port: {{ .Values.service.metricsPort }}
      targetPort: metrics
      protocol: TCP
  selector:
    {{- include "tatara-operator.selectorLabels" . | nindent 4 }}
```

- [ ] **Step 2: ServiceMonitor (scrape the metrics port)**

Create `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/servicemonitor.yaml`:
```yaml
{{- if .Values.serviceMonitor.enabled }}
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: {{ include "tatara-operator.fullname" . }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
spec:
  namespaceSelector:
    matchNames:
      - {{ .Release.Namespace }}
  selector:
    matchLabels:
      {{- include "tatara-operator.selectorLabels" . | nindent 6 }}
  endpoints:
    - port: metrics
      path: /metrics
      interval: {{ .Values.serviceMonitor.interval }}
      scrapeTimeout: {{ .Values.serviceMonitor.scrapeTimeout }}
{{- end }}
```

- [ ] **Step 3: Verify render**
```bash
helm template t ~/Documents/tatara/tatara-operator/charts/tatara-operator --show-only templates/service.yaml --show-only templates/servicemonitor.yaml
```
Expected: a Service with `http`(8080) + `metrics`(9090) ports, and a ServiceMonitor scraping `port: metrics path: /metrics`.

- [ ] **Step 4: Commit**
```bash
git -C ~/Documents/tatara/tatara-operator add charts/tatara-operator/templates/service.yaml charts/tatara-operator/templates/servicemonitor.yaml
git -C ~/Documents/tatara/tatara-operator commit -m "feat(chart): Service (http+metrics) and ServiceMonitor"
```

---

## Task 5: Manager RBAC (ClusterRole + RoleBinding/ClusterRoleBinding)

**Files:**
- Create: `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/rbac.yaml`

The manager reconciles the four CRDs (cluster-scoped CRD read for the types; namespaced CR read/write) and spawns/reads jobs, pods, services, secrets (read), configmaps, networkpolicies in the `tatara` namespace. Because all CRs are namespaced and the operator runs single-namespace, use a Role + RoleBinding for the namespaced verbs and a small ClusterRole + ClusterRoleBinding only for the `customresourcedefinitions` read (CRDs are cluster-scoped). M0 `make manifests` may already emit `// +kubebuilder:rbac` markers; this hand-written chart RBAC is the source of truth for deploy (rule 5 chart edited). Keep it in sync with the kubebuilder markers (boy-scout: if they drift, reconcile to the broader set and note in MEMORY).

- [ ] **Step 1: Write the RBAC manifest**

Create `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/rbac.yaml`:
```yaml
{{- if .Values.rbac.create }}
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: {{ include "tatara-operator.fullname" . }}-manager
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
rules:
  # CRDs reconciled by the manager.
  - apiGroups: ["tatara.dev"]
    resources: ["projects", "repositories", "tasks", "subtasks"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: ["tatara.dev"]
    resources: ["projects/status", "repositories/status", "tasks/status", "subtasks/status"]
    verbs: ["get", "update", "patch"]
  # ingest Jobs.
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  # agent Pods + Services (M4) and ingest-result ConfigMaps (M1).
  - apiGroups: [""]
    resources: ["pods", "services", "configmaps"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  # Project SCM Secrets (read only: token + webhookSecret).
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["get", "list", "watch"]
  # the managed-pod egress NetworkPolicy may be created per-Project at runtime.
  - apiGroups: ["networking.k8s.io"]
    resources: ["networkpolicies"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: [""]
    resources: ["events"]
    verbs: ["create", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: {{ include "tatara-operator.fullname" . }}-manager
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: {{ include "tatara-operator.fullname" . }}-manager
subjects:
  - kind: ServiceAccount
    name: {{ include "tatara-operator.serviceAccountName" . }}
    namespace: {{ .Release.Namespace }}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: {{ include "tatara-operator.fullname" . }}-crd-reader
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
rules:
  - apiGroups: ["apiextensions.k8s.io"]
    resources: ["customresourcedefinitions"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: {{ include "tatara-operator.fullname" . }}-crd-reader
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: {{ include "tatara-operator.fullname" . }}-crd-reader
subjects:
  - kind: ServiceAccount
    name: {{ include "tatara-operator.serviceAccountName" . }}
    namespace: {{ .Release.Namespace }}
{{- end }}
```
Note: ClusterRole/ClusterRoleBinding names are NOT namespaced; if two operator releases ran in one cluster they would collide. Single-cluster single-release is the deploy reality (one `tatara` ns); the name includes the release-derived fullname so a differently-named release stays distinct. Recorded in MEMORY (Task 11).

- [ ] **Step 2: Verify render**
```bash
helm template t ~/Documents/tatara/tatara-operator/charts/tatara-operator --show-only templates/rbac.yaml | grep -cE '^kind: (Role|RoleBinding|ClusterRole|ClusterRoleBinding)$'
```
Expected: `4` (Role, RoleBinding, ClusterRole, ClusterRoleBinding).

- [ ] **Step 3: Commit**
```bash
git -C ~/Documents/tatara/tatara-operator add charts/tatara-operator/templates/rbac.yaml
git -C ~/Documents/tatara/tatara-operator commit -m "feat(chart): manager RBAC (CRDs, jobs, pods, services, secrets-read, configmaps, networkpolicies)"
```

---

## Task 6: ingest ServiceAccount + Role (M1 follow-up)

**Files:**
- Create: `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/ingest-rbac.yaml`

This resolves the M1-flagged follow-up: the M1 ingest Job runs under ServiceAccount `tatara-ingest` and patches the `<repo>-ingest-result` ConfigMap. M6 creates that SA + a narrow Role granting `get,create,update,patch` on ConfigMaps in the `tatara` namespace, and a RoleBinding. The M1 Job already sets `ServiceAccountName: "tatara-ingest"` (see M1 plan `internal/ingest/job.go`); no operator-code change is needed, only this chart RBAC.

- [ ] **Step 1: Write the ingest RBAC manifest**

Create `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/ingest-rbac.yaml`:
```yaml
{{- if .Values.ingestServiceAccount.create }}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ .Values.ingestServiceAccount.name }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
    app.kubernetes.io/component: ingest
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: {{ .Values.ingestServiceAccount.name }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
    app.kubernetes.io/component: ingest
rules:
  # the ingest Job patches its <repo>-ingest-result ConfigMap with the HEAD SHA.
  - apiGroups: [""]
    resources: ["configmaps"]
    verbs: ["get", "create", "update", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: {{ .Values.ingestServiceAccount.name }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
    app.kubernetes.io/component: ingest
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: {{ .Values.ingestServiceAccount.name }}
subjects:
  - kind: ServiceAccount
    name: {{ .Values.ingestServiceAccount.name }}
    namespace: {{ .Release.Namespace }}
{{- end }}
```
Note: SA name is the fixed string `tatara-ingest` (not release-prefixed) because the M1 Job hardcodes `ServiceAccountName: "tatara-ingest"`. Keep it fixed; do NOT switch to a release-derived name without also changing `internal/ingest/job.go` (flagged here, not changed in M6).

- [ ] **Step 2: Verify render**
```bash
helm template t ~/Documents/tatara/tatara-operator/charts/tatara-operator --show-only templates/ingest-rbac.yaml
```
Expected: ServiceAccount `tatara-ingest`, Role `tatara-ingest` (configmaps get/create/update/patch), RoleBinding `tatara-ingest` binding the two. Assert verbs:
```bash
helm template t ~/Documents/tatara/tatara-operator/charts/tatara-operator --show-only templates/ingest-rbac.yaml | grep -A3 'resources: \["configmaps"\]' | grep verbs
```
Expected: `verbs: ["get", "create", "update", "patch"]`.

- [ ] **Step 3: Commit**
```bash
git -C ~/Documents/tatara/tatara-operator add charts/tatara-operator/templates/ingest-rbac.yaml
git -C ~/Documents/tatara/tatara-operator commit -m "feat(chart): tatara-ingest ServiceAccount + ConfigMap-patch Role (M1 follow-up)"
```

---

## Task 7: Managed-pod NetworkPolicy (agent + ingest egress allowlist)

**Files:**
- Create: `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/managed-pod-networkpolicy.yaml`

This is the spec's "egress-allowlist NetworkPolicy on agent + ingest pods". It selects pods by the label `tatara.dev/managed-by: tatara-operator`. Egress is allowed to: tatara-memory (svc), the operator Service (REST/webhook + internal callback), tatara-chat (svc), SCM hosts (broad 443 - GitHub/GitLab are external, host set is open-ended so 443 to any external IP is the pragmatic allowlist, matching tatara-memory's `namespaceSelector: {}` 443 egress), and DNS. Ingress is denied except from the operator (operator -> wrapper on the wrapper's HTTP port).

**CROSS-MILESTONE LABEL REQUIREMENT (FLAG for M1 + M4):** This policy only works if spawned pods carry `tatara.dev/managed-by: tatara-operator`. The M1 ingest Job pod template (`internal/ingest/job.go`, the `batchv1.Job.Spec.Template.ObjectMeta.Labels` map) currently sets `app.kubernetes.io/name`, `app.kubernetes.io/component: ingest`, `tatara.dev/repository`; it MUST also set `tatara.dev/managed-by: tatara-operator`. The M4 agent Pod (`internal/agent/pod.go`) MUST set the same label on the Pod's `ObjectMeta.Labels`. These are code changes in M1/M4, NOT chart changes; M6 records the requirement in MEMORY and the M1/M4 plans should be amended if not already done. If those plans are already merged, add a tiny follow-up commit in each repo. Verify after deploy that both pod kinds carry the label (Task 12 manual check).

- [ ] **Step 1: Write the NetworkPolicy template**

Create `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/managed-pod-networkpolicy.yaml`:
```yaml
{{- if .Values.managedPodNetworkPolicy.enabled }}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ include "tatara-operator.fullname" . }}-managed-pods
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      tatara.dev/managed-by: tatara-operator
  policyTypes:
    - Ingress
    - Egress
  ingress:
    # operator -> wrapper only: the manager pod reaches the agent wrapper on 8080.
    - from:
        - podSelector:
            matchLabels:
              {{- include "tatara-operator.selectorLabels" . | nindent 14 }}
      ports:
        - port: 8080
          protocol: TCP
  egress:
    # DNS.
    - to:
        - namespaceSelector: {}
          podSelector:
            matchLabels:
              k8s-app: kube-dns
      ports:
        - port: 53
          protocol: UDP
        - port: 53
          protocol: TCP
    # tatara-memory service.
    - to:
        - podSelector:
            matchLabels:
              app.kubernetes.io/name: tatara-memory
      ports:
        - port: 8080
          protocol: TCP
    # tatara-chat service.
    - to:
        - podSelector:
            matchLabels:
              app.kubernetes.io/name: {{ .Values.managedPodNetworkPolicy.chatServiceName }}
      ports:
        - port: 8080
          protocol: TCP
    # the operator itself (REST API, internal turn-complete callback).
    - to:
        - podSelector:
            matchLabels:
              {{- include "tatara-operator.selectorLabels" . | nindent 14 }}
      ports:
        - port: 8080
          protocol: TCP
        - port: 8081
          protocol: TCP
    # SCM hosts (GitHub/GitLab) + Keycloak token endpoint: external HTTPS.
    - to:
        - namespaceSelector: {}
      ports:
        - port: 443
          protocol: TCP
{{- end }}
```
Note on the 443 egress: GitHub/GitLab/Keycloak are external; an IP-CIDR allowlist for github.com/gitlab.com is brittle (rotating IP ranges). The pragmatic allowlist is "HTTPS to anywhere", matching tatara-memory's existing `namespaceSelector: {}` 443 egress rule. Tightening to specific CIDRs is deferred to the wrapper hardening ROADMAP (gVisor/seccomp era); recorded in MEMORY. The `8081` egress to the operator is the internal turn-complete callback (`INTERNAL_ADDR`).

- [ ] **Step 2: Verify render**
```bash
helm template t ~/Documents/tatara/tatara-operator/charts/tatara-operator --show-only templates/managed-pod-networkpolicy.yaml
```
Expected: one NetworkPolicy `t-tatara-operator-managed-pods` with `podSelector.matchLabels: tatara.dev/managed-by: tatara-operator`, one ingress rule (operator->8080), and egress rules for DNS, tatara-memory:8080, tatara-chat:8080, operator:8080+8081, and 443-to-any. Assert the selector:
```bash
helm template t ~/Documents/tatara/tatara-operator/charts/tatara-operator --show-only templates/managed-pod-networkpolicy.yaml | grep -A2 'podSelector:' | grep 'tatara.dev/managed-by: tatara-operator'
```
Expected: one match line `tatara.dev/managed-by: tatara-operator`.

- [ ] **Step 3: Commit**
```bash
git -C ~/Documents/tatara/tatara-operator add charts/tatara-operator/templates/managed-pod-networkpolicy.yaml
git -C ~/Documents/tatara/tatara-operator commit -m "feat(chart): egress-allowlist NetworkPolicy for managed agent+ingest pods"
```

---

## Task 8: Ingress (webhook + REST paths; host/class from infra, rule 14)

**Files:**
- Create: `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/ingress.yaml`

The Ingress exposes the webhook path (`/operator/webhooks/...`) and the REST paths on the `http` Service port. Host, className, TLS secret, and path are supplied by the infra helmfile (`enabled: false` in the chart default per rule 14). Mirror tatara-memory's ingress (single host, rewrite-target when path != "/"). The operator serves both webhook and REST under a common prefix; the infra value sets `path` to the operator's external base prefix (e.g. `/operator`), and the operator's internal routes (`/operator/webhooks/...`, REST paths) sit under it. Use a single path rule with prefix capture, matching tatara-memory.

- [ ] **Step 1: Write the Ingress template**

Create `~/Documents/tatara/tatara-operator/charts/tatara-operator/templates/ingress.yaml`:
```yaml
{{- if .Values.ingress.enabled }}
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ include "tatara-operator.fullname" . }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
  annotations:
    cert-manager.io/cluster-issuer: {{ .Values.ingress.clusterIssuer | quote }}
    {{- if ne .Values.ingress.path "/" }}
    nginx.ingress.kubernetes.io/rewrite-target: /$2
    {{- end }}
spec:
  ingressClassName: {{ .Values.ingress.className }}
  rules:
    - host: {{ .Values.ingress.host | quote }}
      http:
        paths:
          - path: {{ if ne .Values.ingress.path "/" }}{{ .Values.ingress.path }}(/|$)(.*){{ else }}/{{ end }}
            pathType: {{ if ne .Values.ingress.path "/" }}ImplementationSpecific{{ else }}Prefix{{ end }}
            backend:
              service:
                name: {{ include "tatara-operator.fullname" . }}
                port:
                  name: http
  tls:
    - hosts:
        - {{ .Values.ingress.host | quote }}
      secretName: {{ default (printf "%s-tls" (include "tatara-operator.fullname" .)) .Values.ingress.tlsSecretName }}
{{- end }}
```
Note (rule 14): chart default `ingress.enabled: false`, `host: ""`, `className: nginx`. The real host (`tatara.szymonrichert.pl`), path (`/operator`), and class come from `infra/helmfile/.../values/tatara-operator/default.yaml` (Task 10). The rewrite-target strips the path prefix so the operator sees `/operator/webhooks/{project}` if `EXTERNAL_WEBHOOK_BASE` already encodes the host+prefix; align `EXTERNAL_WEBHOOK_BASE` with the ingress path. The operator's `status.webhookURL` rendering uses `EXTERNAL_WEBHOOK_BASE`; if the rewrite-target strips `/operator`, set `path: /` instead and route entirely inside the app. Decide based on M2's actual route prefix; default here keeps `/operator` external and lets the app own `/operator/webhooks` (so use `path: /` in infra values to avoid double-stripping). Confirm against `internal/webhook/server.go` route registration before committing infra values (Task 10).

- [ ] **Step 2: Verify render with infra-style overrides**
```bash
helm template t ~/Documents/tatara/tatara-operator/charts/tatara-operator \
  --set ingress.enabled=true --set ingress.host=tatara.szymonrichert.pl --set ingress.path=/ \
  --show-only templates/ingress.yaml
```
Expected: one Ingress, host `tatara.szymonrichert.pl`, `ingressClassName: nginx`, backend service `t-tatara-operator` port `http`, TLS host `tatara.szymonrichert.pl`. With chart defaults (`enabled:false`) the template renders nothing:
```bash
helm template t ~/Documents/tatara/tatara-operator/charts/tatara-operator --show-only templates/ingress.yaml
```
Expected: `Error: could not find template templates/ingress.yaml in chart` OR empty output (the `if` guards it). Both confirm rule-14 compliance (no host baked).

- [ ] **Step 3: Commit**
```bash
git -C ~/Documents/tatara/tatara-operator add charts/tatara-operator/templates/ingress.yaml
git -C ~/Documents/tatara/tatara-operator commit -m "feat(chart): Ingress for webhook + REST paths (host/class from infra, rule 14)"
```

---

## Task 9: CRDs in the chart

**Files:**
- Verify/Create: `~/Documents/tatara/tatara-operator/charts/tatara-operator/crds/tatara.dev_projects.yaml`
- Verify/Create: `~/Documents/tatara/tatara-operator/charts/tatara-operator/crds/tatara.dev_repositories.yaml`
- Verify/Create: `~/Documents/tatara/tatara-operator/charts/tatara-operator/crds/tatara.dev_tasks.yaml`
- Verify/Create: `~/Documents/tatara/tatara-operator/charts/tatara-operator/crds/tatara.dev_subtasks.yaml`

M0 generated these via `make manifests` (controller-gen). M6 confirms they live under the chart's `crds/` directory (helm installs `crds/` before templates and does not template them). If M0 emitted them to `config/crd/bases/` instead, copy them into `charts/tatara-operator/crds/` and wire the Makefile `manifests` target to output there (boy-scout, rule 3).

- [ ] **Step 1: Ensure the four CRDs exist under the chart crds/ dir**
```bash
ls ~/Documents/tatara/tatara-operator/charts/tatara-operator/crds/
```
Expected: `tatara.dev_projects.yaml  tatara.dev_repositories.yaml  tatara.dev_subtasks.yaml  tatara.dev_tasks.yaml`. If absent, regenerate and copy:
```bash
cd ~/Documents/tatara/tatara-operator && make manifests
cp config/crd/bases/tatara.dev_*.yaml charts/tatara-operator/crds/
```
(Only if `make manifests` writes to `config/crd/bases`; if the Makefile already targets the chart `crds/`, skip the copy.)

- [ ] **Step 2: Verify each CRD declares the right group/kind/scope**
```bash
grep -l 'group: tatara.dev' ~/Documents/tatara/tatara-operator/charts/tatara-operator/crds/*.yaml | wc -l
grep -h 'scope:' ~/Documents/tatara/tatara-operator/charts/tatara-operator/crds/*.yaml | sort -u
```
Expected: `4`, and `    scope: Namespaced` (all four namespaced).

- [ ] **Step 3: Commit (if any CRD was moved/regenerated)**
```bash
git -C ~/Documents/tatara/tatara-operator add charts/tatara-operator/crds/ Makefile
git -C ~/Documents/tatara/tatara-operator commit -m "chore(chart): ensure CRDs ship under chart crds/"
```

---

## Task 10: Full chart lint + render verification (gate before publish)

**Files:** none (verification only).

- [ ] **Step 1: helm lint clean**
```bash
helm lint ~/Documents/tatara/tatara-operator/charts/tatara-operator
```
Expected: `1 chart(s) linted, 0 chart(s) failed` (`[INFO]` lines OK; no `[ERROR]`).

- [ ] **Step 2: Render the full chart and count expected objects**
```bash
helm template t ~/Documents/tatara/tatara-operator/charts/tatara-operator | grep -cE '^kind:'
```
Expected (with defaults: ingress off, all else on): `13` -
ConfigMap, Secret, ServiceAccount(manager), Deployment, Service, ServiceMonitor, Role(manager), RoleBinding(manager), ClusterRole, ClusterRoleBinding, ServiceAccount(ingest), Role(ingest), RoleBinding(ingest), NetworkPolicy. That is 14; CRDs are not counted by `helm template` (they live in `crds/`, not `templates/`). Recount: ConfigMap(1) Secret(2) SA-mgr(3) Deployment(4) Service(5) ServiceMonitor(6) Role-mgr(7) RoleBinding-mgr(8) ClusterRole(9) ClusterRoleBinding(10) SA-ingest(11) Role-ingest(12) RoleBinding-ingest(13) NetworkPolicy(14). Expected: `14`.

- [ ] **Step 3: kubeconform validation (schema-check rendered objects)**

If `kubeconform` is installed:
```bash
helm template t ~/Documents/tatara/tatara-operator/charts/tatara-operator \
  | kubeconform -strict -ignore-missing-schemas -summary -
```
Expected: `Summary: N resources found ... 0 errors, 0 skipped` (ServiceMonitor + NetworkPolicy schemas may be skipped via `-ignore-missing-schemas`; that is acceptable). If `kubeconform` is NOT installed, fall back to a dry-run server validate against the live cluster ONLY if a cluster is reachable and you have read access (non-mutating):
```bash
helm template t ~/Documents/tatara/tatara-operator/charts/tatara-operator | kubectl apply --dry-run=server -f - 2>&1 | tail
```
Expected: each object reports `(server dry run)` with no error. Skip this fallback if no cluster is available; the `helm lint` + render-count gate is sufficient for the plan.

- [ ] **Step 4: Bump chart version if edited post-M0 and publish from main**

After merging the M6 branch to `tatara-operator` `main` (rule 10: build/publish from main only), publish the OCI chart:
```bash
git -C ~/Documents/tatara/tatara-operator checkout main && git -C ~/Documents/tatara/tatara-operator pull
helm package ~/Documents/tatara/tatara-operator/charts/tatara-operator -d /tmp
helm push /tmp/tatara-operator-0.1.0.tgz oci://harbor.szymonrichert.pl/charts
```
Expected: `Pushed: harbor.szymonrichert.pl/charts/tatara-operator:0.1.0  Digest: sha256:...`. (Chart version `0.1.0` must match the helmfile pin in Task 12. The operator container image `harbor.szymonrichert.pl/containers/tatara-operator:0.1.0` must be built+pushed first; flagged in ROADMAP, Task 11.)

---

## Task 11: ROADMAP + MEMORY updates (operator repo)

**Files:**
- Modify: `~/Documents/tatara/tatara-operator/MEMORY.md`
- Modify: `~/Documents/tatara/tatara-operator/ROADMAP.md`

- [ ] **Step 1: Append MEMORY entries**

Append to `~/Documents/tatara/tatara-operator/MEMORY.md`:
```markdown
- 2026-06-06 M6 chart finalized. Cluster-agnostic (rule 14): no baked
  regcred/affinity/ingress-host/storage-class; all of that comes from the infra
  helmfile tatara bucket. Config is scalar-via-envConfig -> ConfigMap envFrom
  (rule 6); OPERATOR_OIDC_CLIENT_SECRET is the only secret env, via templated
  Secret. CRDs ship under chart crds/ (helm installs them pre-templates).
- 2026-06-06 Managed-pod NetworkPolicy selects tatara.dev/managed-by=tatara-operator.
  M1 ingest Job pod template and M4 agent Pod MUST set this label (code change,
  not chart). Egress allowlist: DNS, tatara-memory:8080, tatara-chat:8080,
  operator:8080+8081, and HTTPS-to-any (443) for SCM+Keycloak. CIDR-tightening of
  the 443 rule deferred to wrapper hardening ROADMAP (brittle IP ranges).
- 2026-06-06 tatara-ingest SA + ConfigMap-patch Role added (M1 follow-up). SA
  name is the fixed string "tatara-ingest" (M1 Job hardcodes it); do not
  release-prefix without changing internal/ingest/job.go.
- 2026-06-06 Manager RBAC: namespaced Role (CRDs+status, jobs, pods/services/
  configmaps, secrets-read, networkpolicies, events) + cluster-scoped
  CRD-reader ClusterRole. Single-cluster single-release assumed; ClusterRole
  name carries release fullname to avoid cross-release collision.
- 2026-06-06 Keycloak: confidential tatara-operator client (service accounts,
  no browser flows) mints tokens to memory/wrapper; tatara-operator audience
  mapper on the tatara scope means one tatara-cli token carries aud=tatara-memory
  AND aud=tatara-operator AND aud=tatara-chat (M3 multi-audience need resolved).
```

- [ ] **Step 2: Update ROADMAP - mark M6 done, add deploy follow-ons**

Edit `~/Documents/tatara/tatara-operator/ROADMAP.md`: change `- [ ] M6 ...` to `- [x] M6 chart + deploy wiring - chart hardened, NetworkPolicy, RBAC, Keycloak client, infra helmfile release. Plan: docs/superpowers/plans/2026-06-06-tatara-operator-m6-chart-deploy.md.` and append a "Deploy follow-ons (out-of-band)" section:
```markdown
## Deploy follow-ons (out-of-band, before first real run)

- [ ] Replicate `ANTHROPIC_API_KEY` into the `tatara` namespace as Secret
  `tatara-anthropic` (key the wrapper reads as ANTHROPIC_API_KEY). The chart
  value `anthropicSecretName` points the operator at it; the Secret itself is
  cluster-managed (reflector/replicated-secret or sops), NOT chart-rendered.
- [ ] Build + push the `tatara-claude-code-wrapper` image and the
  `tatara-memory-repo-ingester` image to harbor; the operator references them
  via Project.spec.agent.image and INGESTER_IMAGE. Operator cannot run agents
  or ingest until both images are published.
- [ ] Build + push `harbor.szymonrichert.pl/containers/tatara-operator:0.1.0`
  before the helmfile pins chart 0.1.0 / image tag 0.1.0.
- [ ] Create per-Project SCM Secrets out-of-band (keys: token, webhookSecret),
  referenced by Project.spec.scmSecretRef. One Secret per Project; not chart-
  rendered. SOPS guidance in infra helmfile values/tatara-operator/default.secrets.yaml.
- [ ] Apply the tatara-cli OIDC client-creds Secret `tatara-cli-oidc` in the
  tatara namespace (the wrapper's tatara-cli mints operator/memory/chat tokens).
```

- [ ] **Step 3: Commit**
```bash
git -C ~/Documents/tatara/tatara-operator add MEMORY.md ROADMAP.md
git -C ~/Documents/tatara/tatara-operator commit -m "docs: record M6 deploy decisions + out-of-band follow-ons"
```

---

## Task 12: Keycloak - tatara-operator confidential client + audience mapper

**Files:**
- Modify: `~/Documents/infra/terraform/keycloak/tatara_clients.tf`

Add a confidential `tatara-operator` client (service accounts enabled, no browser flows; mirrors `tatara_memory`/`tatara_chat`) and a `tatara-operator` audience mapper on the `tatara` scope (mirrors `tatara_chat_aud`). The audience mapper puts `aud=tatara-operator` on every token issued with the `tatara` scope - including tatara-cli device-flow tokens - so a single tatara-cli token carries `aud` for memory, chat, AND operator. This resolves the M3-flagged need for one tatara-cli token to reach both `tatara-memory` and `tatara-operator`: the audience mappers are additive on the shared scope, so no separate token exchange is needed.

- [ ] **Step 1: Add the audience mapper (after the existing tatara_chat_aud mapper)**

Insert into `~/Documents/infra/terraform/keycloak/tatara_clients.tf`, after the `tatara_chat_aud` resource (around line 23):
```hcl
# tatara-operator (phase 9). This mapper puts aud=tatara-operator on every
# tatara-scope token (tatara-cli device flow and confidential service accounts
# alike) so those tokens reach the operator REST API. Combined with the
# tatara-memory and tatara-chat mappers, one tatara-cli token carries all three
# audiences (resolves the M3 multi-audience need).
resource "keycloak_openid_audience_protocol_mapper" "tatara_operator_aud" {
  realm_id                 = data.keycloak_realm.master.id
  client_scope_id          = keycloak_openid_client_scope.tatara.id
  name                     = "tatara-operator-audience"
  included_custom_audience = "tatara-operator"
}
```

- [ ] **Step 2: Add the confidential client + its default scopes (after tatara_chat block, before outputs)**

Insert before the `output "tatara_memory_client_secret"` block:
```hcl
resource "keycloak_openid_client" "tatara_operator" {
  realm_id    = data.keycloak_realm.master.id
  client_id   = "tatara-operator"
  name        = "tatara-operator"
  description = "Tatara operator (CRD reconciler + OIDC-gated REST/webhook). Confidential service-account client: mints tokens to call tatara-memory and the wrapper. Also the audience for agent tatara-cli tokens reaching the operator REST API."

  access_type                  = "CONFIDENTIAL"
  standard_flow_enabled        = false
  implicit_flow_enabled        = false
  direct_access_grants_enabled = false
  service_accounts_enabled     = true

  valid_redirect_uris = []

  access_token_lifespan = "3600"
}

resource "keycloak_openid_client_default_scopes" "tatara_operator_scopes" {
  realm_id  = data.keycloak_realm.master.id
  client_id = keycloak_openid_client.tatara_operator.id
  default_scopes = [
    "profile",
    "email",
    keycloak_openid_client_scope.tatara.name,
  ]
}
```

- [ ] **Step 3: Add the client-secret output (next to the existing outputs)**

Append after `output "tatara_chat_client_secret"`:
```hcl
output "tatara_operator_client_secret" {
  value     = keycloak_openid_client.tatara_operator.client_secret
  sensitive = true
}
```

- [ ] **Step 4: fmt + validate**
```bash
terraform -chdir=/Users/szymonri/Documents/infra/terraform/keycloak fmt -check
terraform -chdir=/Users/szymonri/Documents/infra/terraform/keycloak validate
```
Expected: `fmt -check` prints nothing and exits 0 (run `terraform fmt` without `-check` first if it reports the file). `validate` prints `Success! The configuration is valid.`
Note: `terraform validate` may require `terraform init` first; if the working dir is uninitialized, run `terraform -chdir=/Users/szymonri/Documents/infra/terraform/keycloak init -backend=false` (no remote state needed for validate).

- [ ] **Step 5: Plan (review only - do NOT apply)**
```bash
terraform -chdir=/Users/szymonri/Documents/infra/terraform/keycloak plan
```
Expected: plan shows `3 to add` (client, default-scopes, audience-mapper) `0 to change, 0 to destroy`. Review and STOP. Applying the Keycloak change is a separate user-gated action; the operator OIDC client secret produced by `terraform output -raw tatara_operator_client_secret` feeds the sops secrets file in Task 13.

- [ ] **Step 6: Commit (infra/terraform repo)**
```bash
git -C ~/Documents/infra add terraform/keycloak/tatara_clients.tf
git -C ~/Documents/infra commit -m "feat(keycloak): tatara-operator confidential client + audience mapper"
```

---

## Task 13: Infra helmfile - tatara-operator release + values + sops secrets

**Files:**
- Modify: `~/Documents/infra/helmfile/helmfiles/tatara/helmfile.yaml.gotmpl`
- Create: `~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/common.yaml`
- Create: `~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/default.yaml`
- Create (sops-encrypted): `~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/default.secrets.yaml`

- [ ] **Step 1: Add the release to the helmfile (append after the tatara-chat release)**

Append to `~/Documents/infra/helmfile/helmfiles/tatara/helmfile.yaml.gotmpl`:
```yaml
- name: tatara-operator
  chart: oci://harbor.szymonrichert.pl/charts/tatara-operator
  namespace: tatara
  createNamespace: true
  version: 0.1.0
  labels:
    purpose: tatara
    application: tatara-operator
  <<: *default
```
Note: the shared `*default` anchor already wires `values/common.yaml`, `values/default.yaml`, `values/tatara-operator/common.yaml`, `values/tatara-operator/default.yaml`, and the sops `values/tatara-operator/default.secrets.yaml` (env name `default`). No per-release block changes needed beyond this.

- [ ] **Step 2: Write `values/tatara-operator/common.yaml` (image tag pin; regcred passthrough)**

Create `~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/common.yaml`:
```yaml
# Cluster-specific values the chart does not bake (rule 14). The operator chart
# has no subcharts, so the only passthrough is the image tag pin. The regcred
# pull secret reaches the manager Deployment from the bucket values/common.yaml
# (imagePullSecrets), same as the other tatara releases.
image:
  tag: "0.1.0"
```

- [ ] **Step 3: Write `values/tatara-operator/default.yaml` (cluster-specific ingress + endpoints)**

Create `~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/default.yaml`:
```yaml
ingress:
  enabled: true
  host: tatara.szymonrichert.pl
  # path "/" lets the operator own /operator/webhooks and the REST routes
  # internally (no rewrite double-strip). See chart ingress.yaml note.
  path: /
  className: nginx

# External base used to render Project.status.webhookURL. Must match the public
# host + the operator's internal webhook route prefix.
externalWebhookBase: "https://tatara.szymonrichert.pl/operator/webhooks"

# In-cluster service endpoints.
memoryBaseUrl: "http://tatara-memory.tatara.svc:8080"
oidcIssuer: "https://auth.szymonrichert.pl/realms/master"
oidcAudience: "tatara-operator"
operatorOidcClientId: "tatara-operator"

# Secret names the operator references (cluster-managed, not chart-rendered).
anthropicSecretName: "tatara-anthropic"
cliOidcSecretName: "tatara-cli-oidc"

# Ingester image the ingest Job runs (must be published to harbor first).
ingesterImage: "harbor.szymonrichert.pl/containers/tatara-memory-repo-ingester:latest"
```
Note: if the operator's webhook routes are registered at `/operator/webhooks/{project}` inside the app AND the ingress path is `/`, then `EXTERNAL_WEBHOOK_BASE` correctly resolves to `https://tatara.szymonrichert.pl/operator/webhooks`. Confirm the route prefix against `internal/webhook/server.go` before deploy; adjust path/base together if it differs.

- [ ] **Step 4: Create the sops-encrypted secrets file**

The sops rule `path_regex: default.secrets.yaml` (PGP `D39E46932A270AA3BA490B9DB9FE928D3E8BCED8`) covers this filename. Use the `sops-secret-helper` skill to create/populate it (never hand-edit a sops file). The plaintext shape before encryption:
```yaml
# operatorOidcClientSecret: the confidential tatara-operator client secret from
#   `terraform -chdir=.../keycloak output -raw tatara_operator_client_secret`.
operatorOidcClientSecret: "REPLACE_WITH_KEYCLOAK_OUTPUT"
```
Then encrypt in place:
```bash
sops -e -i ~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/default.secrets.yaml
```
Expected: the file's `operatorOidcClientSecret` value becomes `ENC[AES256_GCM,...]` and a `sops:` metadata block appends (matching `values/tatara-memory/default.secrets.yaml` shape).

Per-Project SCM secret guidance (documented, NOT in this sops file): each Project's `scmSecretRef` Secret (keys `token`, `webhookSecret`) is created out-of-band in the `tatara` namespace, one per Project. They are not part of the operator release values because they are Project-scoped runtime data, not chart config. Record this in the file as a top comment and in ROADMAP (Task 11 already lists it). Likewise `tatara-anthropic` and `tatara-cli-oidc` Secrets are cluster-managed (replicated/sops), not rendered here.

- [ ] **Step 5: Verify the values render through helmfile template (no apply)**
```bash
helmfile -e default -f ~/Documents/infra/helmfile/helmfiles/tatara/helmfile.yaml.gotmpl -l application=tatara-operator template 2>&1 | grep -cE '^kind:'
```
Expected: the operator's object count (`14` from Task 10 step 2, plus possibly the helmfile hook output noise filtered out by the grep). If `template` fails on a missing published chart (chart 0.1.0 not yet pushed), this step requires Task 10 Step 4 (publish) to have run; otherwise expect `Error: chart "oci://.../tatara-operator" version "0.1.0" not found` - publish first, then re-run.

- [ ] **Step 6: GATED final step - helmfile diff (REQUIRE user confirmation before any apply)**

Run the diff to preview what the release would change against the live cluster:
```bash
helmfile -e default -f ~/Documents/infra/helmfile/helmfiles/tatara/helmfile.yaml.gotmpl -l application=tatara-operator diff
```
Expected: a diff showing the operator's 14 objects + 4 CRDs as net-new (`+` additions), no `-` deletions on existing tatara releases.

**STOP HERE. Do NOT run `helmfile apply`.** Present the diff to the user. Applying is a destructive/prod action (CLAUDE.md "Stop and ask on: prod configs") and requires explicit user confirmation. Preconditions the user must satisfy before apply: (a) the Keycloak change (Task 12) applied so the client+secret exist; (b) the operator container image `:0.1.0` and the wrapper + ingester images published; (c) the `tatara-anthropic`, `tatara-cli-oidc`, and per-Project SCM Secrets present in the `tatara` namespace. Only after the user confirms and those preconditions hold should `helmfile -e default ... apply` run.

- [ ] **Step 7: Commit (infra/helmfile repo)**
```bash
git -C ~/Documents/infra add helmfile/helmfiles/tatara/helmfile.yaml.gotmpl helmfile/helmfiles/tatara/values/tatara-operator/
git -C ~/Documents/infra commit -m "feat(helmfile): add tatara-operator release to tatara bucket"
```

---

## Verification summary (superpowers:verification-before-completion)

Before claiming M6 done, confirm:

1. `helm lint ~/Documents/tatara/tatara-operator/charts/tatara-operator` -> `0 chart(s) failed`.
2. `helm template t .../charts/tatara-operator | grep -cE '^kind:'` -> `14` (CRDs excluded; they live in `crds/`).
3. `ls .../charts/tatara-operator/crds/` -> 4 CRD files, all `scope: Namespaced`.
4. ConfigMap carries all 12 pin-set env keys; Secret carries `OPERATOR_OIDC_CLIENT_SECRET`; Deployment `envFrom` references both.
5. NetworkPolicy `podSelector` is `tatara.dev/managed-by: tatara-operator`; egress allowlist covers DNS/memory/chat/operator/443.
6. `tatara-ingest` SA + Role (configmaps get/create/update/patch) + RoleBinding render.
7. `terraform -chdir=.../keycloak fmt -check` clean; `validate` -> `Success!`; `plan` -> `3 to add`.
8. Helmfile release `tatara-operator` (chart 0.1.0) present; `values/tatara-operator/{common,default}.yaml` + sops `default.secrets.yaml` (encrypted) present.
9. `helmfile -e default ... diff` ran and was presented to the user; NO apply executed.
10. MEMORY + ROADMAP updated; the cross-milestone `tatara.dev/managed-by` label requirement flagged for M1 + M4; deploy follow-ons (ANTHROPIC_API_KEY replication, wrapper+ingester image publish, per-Project SCM secrets) listed.

## Flagged cross-milestone / out-of-band items (do not silently drop)

- **M1/M4 code change (label):** spawned ingest Job pods and agent Pods MUST carry `tatara.dev/managed-by: tatara-operator` or the NetworkPolicy will not select them. Amend `internal/ingest/job.go` and `internal/agent/pod.go`; this is the only behavioral dependency the chart imposes on earlier milestones.
- **Image publishing:** operator, wrapper, and ingester images must exist in harbor before apply.
- **Out-of-band Secrets:** `tatara-anthropic` (ANTHROPIC_API_KEY), `tatara-cli-oidc` (client creds), and per-Project `scmSecretRef` Secrets (token + webhookSecret) are cluster-managed, not chart-rendered.
- **Keycloak apply gating:** the `tatara-operator` client must be applied and its secret captured into the sops file before the helmfile apply succeeds (the manager fails readiness without a valid client-credentials secret).
