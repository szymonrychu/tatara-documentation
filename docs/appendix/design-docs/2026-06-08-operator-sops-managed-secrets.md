# Operator sops-managed secrets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the tatara-operator chart render its four cluster-managed secrets (`tatara-anthropic`, `tatara-cli-oidc`, `lightrag-openai`, `tatara-scm`) from sops-encrypted helmfile values, replacing manual `kubectl create secret`.

**Architecture:** A new chart template renders one conditionally-gated Secret per item, named from the existing `*SecretName` values, with data keys fixed by consumer code. Real values live only in the infra helmfile's sops overlay. No operator Go change: CRs/config already reference these secrets by path. Chart-only change, so `version` bumps but `appVersion` and the image stay `0.2.2`.

**Tech Stack:** Helm, helmfile, sops, kubectl.

Spec: `docs/superpowers/specs/2026-06-08-operator-sops-managed-secrets-design.md`.

Consumer-fixed data keys (do not change): `tatara-anthropic`=`oauth-token`; `tatara-cli-oidc`=`client-id`,`client-secret`; `lightrag-openai`=`LLM_BINDING_API_KEY`; `tatara-scm`=`token`,`webhookSecret`.

---

## Task 1: Chart renders the four managed secrets

**Files (in a worktree off `tatara-operator` main):**
- Create: `charts/tatara-operator/templates/managed-secrets.yaml`
- Modify: `charts/tatara-operator/values.yaml`
- Modify: `charts/tatara-operator/Chart.yaml`

- [ ] **Step 0: Create the worktree** (REQUIRED SUB-SKILL: superpowers:using-git-worktrees).

```bash
cd ~/Documents/tatara/tatara-operator
git worktree add -b feat/sops-managed-secrets /tmp/to-secrets main
cd /tmp/to-secrets
```
Expected: worktree at `/tmp/to-secrets` on branch `feat/sops-managed-secrets`.

- [ ] **Step 1: Write the failing render check.** Helm has no unittest here, so the test is a `helm template` assertion. Create `/tmp/secret-render-test.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /tmp/to-secrets
OUT=$(mise exec -- helm template t charts/tatara-operator \
  --set anthropicSecretName=tatara-anthropic --set anthropicOauthToken=OAT \
  --set cliOidcSecretName=tatara-cli-oidc --set cliOidcClientId=CID --set cliOidcClientSecret=CSEC \
  --set openaiSecretName=lightrag-openai --set openaiApiKey=OAI \
  --set scmSecretName=tatara-scm --set scmToken=TOK --set scmWebhookSecret=WHS)
echo "$OUT" | grep -q "name: tatara-anthropic" || { echo "FAIL: no tatara-anthropic"; exit 1; }
echo "$OUT" | grep -q "oauth-token:" || { echo "FAIL: no oauth-token key"; exit 1; }
echo "$OUT" | grep -q "name: tatara-cli-oidc" || { echo "FAIL: no tatara-cli-oidc"; exit 1; }
echo "$OUT" | grep -q "client-secret:" || { echo "FAIL: no client-secret key"; exit 1; }
echo "$OUT" | grep -q "name: lightrag-openai" || { echo "FAIL: no lightrag-openai"; exit 1; }
echo "$OUT" | grep -q "LLM_BINDING_API_KEY:" || { echo "FAIL: no LLM_BINDING_API_KEY key"; exit 1; }
echo "$OUT" | grep -q "name: tatara-scm" || { echo "FAIL: no tatara-scm"; exit 1; }
echo "$OUT" | grep -q "webhookSecret:" || { echo "FAIL: no webhookSecret key"; exit 1; }
# empty values render NONE of the four:
EMPTY=$(mise exec -- helm template t charts/tatara-operator)
echo "$EMPTY" | grep -q "name: tatara-anthropic" && { echo "FAIL: rendered with empty values"; exit 1; }
echo "PASS"
```

- [ ] **Step 2: Run it to verify it fails.**

```bash
chmod +x /tmp/secret-render-test.sh && /tmp/secret-render-test.sh
```
Expected: `FAIL: no tatara-anthropic` (template does not exist yet), exit 1.

- [ ] **Step 3: Add the value defaults.** In `charts/tatara-operator/values.yaml`, under the existing secret-scalars block (near `operatorOidcClientSecret: ""`), add:

```yaml
# Cluster-managed secrets rendered from SOPS values (empty = not rendered, use
# an externally-managed Secret of the same name instead). Data keys are fixed
# by consumer code; do not rename.
scmSecretName: ""
anthropicOauthToken: ""
cliOidcClientId: ""
cliOidcClientSecret: ""
openaiApiKey: ""
scmToken: ""
scmWebhookSecret: ""
```

- [ ] **Step 4: Add the template.** Create `charts/tatara-operator/templates/managed-secrets.yaml`:

```yaml
{{- if .Values.anthropicOauthToken }}
---
apiVersion: v1
kind: Secret
metadata:
  name: {{ .Values.anthropicSecretName }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
type: Opaque
data:
  oauth-token: {{ .Values.anthropicOauthToken | b64enc | quote }}
{{- end }}
{{- if .Values.cliOidcClientId }}
---
apiVersion: v1
kind: Secret
metadata:
  name: {{ .Values.cliOidcSecretName }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
type: Opaque
data:
  client-id: {{ .Values.cliOidcClientId | b64enc | quote }}
  client-secret: {{ .Values.cliOidcClientSecret | b64enc | quote }}
{{- end }}
{{- if .Values.openaiApiKey }}
---
apiVersion: v1
kind: Secret
metadata:
  name: {{ .Values.openaiSecretName }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
type: Opaque
data:
  LLM_BINDING_API_KEY: {{ .Values.openaiApiKey | b64enc | quote }}
{{- end }}
{{- if .Values.scmToken }}
---
apiVersion: v1
kind: Secret
metadata:
  name: {{ .Values.scmSecretName }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "tatara-operator.labels" . | nindent 4 }}
type: Opaque
data:
  token: {{ .Values.scmToken | b64enc | quote }}
  webhookSecret: {{ .Values.scmWebhookSecret | b64enc | quote }}
{{- end }}
```

- [ ] **Step 5: Run the render check + helm lint to verify pass.**

```bash
/tmp/secret-render-test.sh
mise exec -- helm lint charts/tatara-operator
```
Expected: `PASS`; helm lint `0 chart(s) failed`.

- [ ] **Step 6: Bump the chart version (chart-only change; appVersion stays).** In `charts/tatara-operator/Chart.yaml` change `version: 0.2.2` to `version: 0.2.3`. Leave `appVersion: "0.2.2"` unchanged (no Go/binary change).

- [ ] **Step 7: Code review** (REQUIRED SUB-SKILL: superpowers:requesting-code-review). Dispatch a reviewer on the worktree diff; confirm the four Secrets render with correct names/keys, gating is correct, and no operator Go change is needed. Apply critical/high fixes.

- [ ] **Step 8: Commit, merge to main, clean up the worktree.**

```bash
cd /tmp/to-secrets && git add -A && git commit -m "feat(chart): render anthropic/cli-oidc/openai/scm secrets from sops values"
cd ~/Documents/tatara/tatara-operator && git merge --ff-only feat/sops-managed-secrets
git worktree remove /tmp/to-secrets && git branch -d feat/sops-managed-secrets
```
Expected: fast-forward merge onto main; worktree + branch gone.

---

## Task 2: Publish chart 0.2.3 (from main, no image rebuild)

**Files:** none (publish only).

- [ ] **Step 1: Package + push the chart.** The image is unchanged (`0.2.2`), so only the chart is published.

```bash
cd ~/Documents/tatara/tatara-operator
mise exec -- helm package charts/tatara-operator --version 0.2.3 -d /tmp
mise exec -- helm push /tmp/tatara-operator-0.2.3.tgz oci://harbor.szymonrichert.pl/charts
```
Expected: `Pushed: harbor.szymonrichert.pl/charts/tatara-operator:0.2.3`.

---

## Task 3: Infra helmfile - sops values + pins

**Files (in `~/Documents/infra/helmfile`):**
- Modify: `helmfiles/tatara/values/tatara-operator/default.yaml` (add `scmSecretName`)
- Modify: `helmfiles/tatara/helmfile.yaml.gotmpl` (chart version pin)
- Modify: `helmfiles/tatara/values/tatara-operator/default.secrets.yaml` (6 sops values; USER-run)

- [ ] **Step 1: Add the SCM secret name.** In `helmfiles/tatara/values/tatara-operator/default.yaml`, alongside `anthropicSecretName`/`cliOidcSecretName`/`openaiSecretName`, add:

```yaml
scmSecretName: "tatara-scm"
```

- [ ] **Step 2: Bump the chart version pin.** In `helmfiles/tatara/helmfile.yaml.gotmpl`, in the `tatara-operator` release block, change `version: 0.2.2` to `version: 0.2.3`. Leave the image tag pin in `values/tatara-operator/common.yaml` at `0.2.2` (image unchanged).

- [ ] **Step 3: USER sets the six sops values.** Run the `sops-secret-helper` skill `set` for each key in `helmfiles/tatara/values/tatara-operator/default.secrets.yaml` (value entered at the tty, never argv). Current live values can be retrieved first if not at hand:

```bash
# retrieve current values to re-enter (run by the user; do not log):
kubectl -n tatara get secret tatara-anthropic  -o jsonpath='{.data.oauth-token}'         | base64 -d   # -> anthropicOauthToken
kubectl -n tatara get secret tatara-cli-oidc   -o jsonpath='{.data.client-id}'           | base64 -d   # -> cliOidcClientId
kubectl -n tatara get secret tatara-cli-oidc   -o jsonpath='{.data.client-secret}'       | base64 -d   # -> cliOidcClientSecret
kubectl -n tatara get secret lightrag-openai   -o jsonpath='{.data.LLM_BINDING_API_KEY}' | base64 -d   # -> openaiApiKey
# scmToken: the REAL GitHub PAT (scopes repo + admin:repo_hook) - NOT the current placeholder.
# scmWebhookSecret: openssl rand -hex 24
```
Keys to set: `anthropicOauthToken`, `cliOidcClientId`, `cliOidcClientSecret`, `openaiApiKey`, `scmToken`, `scmWebhookSecret`.
Expected: `mise exec -- ./scripts/sops-secret-helper.sh keys helmfiles/tatara/values/tatara-operator/default.secrets.yaml` lists all six plus `operatorOidcClientSecret`.

---

## Task 4: Migrate (delete + recreate) and verify

**Files:** none (cluster ops).

- [ ] **Step 1: Diff to confirm the four Secrets will be created.**

```bash
cd ~/Documents/infra/helmfile
mise exec -- helmfile cache cleanup
mise exec -- helmfile -e default -l application=tatara-operator diff 2>&1 | grep -E "Secret|tatara-anthropic|tatara-cli-oidc|lightrag-openai|tatara-scm"
```
Expected: shows the four Secrets as additions (helm will conflict on apply until Step 2 deletes the manual ones).

- [ ] **Step 2: Delete the manual secrets.**

```bash
kubectl -n tatara delete secret tatara-anthropic tatara-cli-oidc lightrag-openai tatara-scm
```
Expected: all four `deleted`.

- [ ] **Step 3: Apply.**

```bash
cd ~/Documents/infra/helmfile
mise exec -- helmfile -e default -l application=tatara-operator apply
```
Expected: `UPDATED RELEASES: tatara-operator ... 0.2.3`.

- [ ] **Step 4: Verify the secrets exist with the right keys (no values printed).**

```bash
for s in tatara-anthropic tatara-cli-oidc lightrag-openai tatara-scm; do
  echo -n "$s: "; kubectl -n tatara get secret "$s" -o go-template='{{range $k,$v := .data}}{{$k}} {{end}}{{"\n"}}'
done
# confirm tatara-scm.token is now a real PAT (prefix ghp_/github_pat_, not ca84):
T=$(kubectl -n tatara get secret tatara-scm -o jsonpath='{.data.token}' | base64 -d); printf 'scm token prefix=%s\n' "$(printf %s "$T" | cut -c1-4)"
kubectl -n tatara get pod -l app.kubernetes.io/name=tatara-operator
```
Expected: `tatara-anthropic: oauth-token`; `tatara-cli-oidc: client-id client-secret`; `lightrag-openai: LLM_BINDING_API_KEY`; `tatara-scm: token webhookSecret`; scm token prefix `ghp_`/`gith`; operator pod `Running`.

- [ ] **Step 5: Update MEMORY/ROADMAP.** In `tatara-operator/MEMORY.md` add a dated line: secrets now chart-rendered from sops (0.2.3); multi-project SCM deferred (rule 6: no lists in values.yaml). In `ROADMAP.md` mark items 8-10 (replicate anthropic/cli-oidc/scm) done via chart rendering. Commit on operator main.

```bash
cd ~/Documents/tatara/tatara-operator && git add MEMORY.md ROADMAP.md && git commit -m "docs: secrets chart-rendered from sops (0.2.3); multi-project scm deferred"
```

---

## Notes
- Tasks 1-2 are operator-repo work (worktree -> main -> publish). Tasks 3-4 are infra + cluster, gated on the user setting sops values (Task 3 Step 3) and supplying the real GitHub PAT.
- This change also fixes the placeholder `tatara-scm.token`: the user enters the real PAT into sops once, and the chart renders it. After Task 4, E3 enrollment is unblocked.
- The infra `common.yaml` image.tag (0.2.2) bump from the prior OAuth deploy is still uncommitted; fold it into the same infra MR.
