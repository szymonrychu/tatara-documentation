# Autonomous Cron Infra/Deploy RUNBOOK Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to drive this runbook, BUT this is an operations runbook (operator/CRD bump + NetworkPolicy apply + Project CR re-apply + helm), NOT TDD code. There is no test-first cycle. Steps use checkbox (`- [ ]`) syntax; each step is a concrete operator action with the exact command. Steps marked **[USER-GATED]** MUST be performed by the human (prod mutation, cluster-side policy apply, `helmfile apply`, rollout restart, or interactive secret-setting from a tty); the agent prepares and verifies around them but does not run them. No `sops ... set` or account creation is needed in this runbook - the bot account + PAT + webhook secret were already gated in the prior SCM-projects runbook (`2026-06-09-scm-projects-infra.md`); this runbook only bumps the operator, adds one cluster-side egress NetworkPolicy, and patches the live Project CR with the new `spec.scm.cron` + `spec.scm.priorityLabel` block.

**Goal:** Roll the autonomous-cron operator (new appVersion, carries `ScmCron`/`BrainstormActivity`/`priorityLabel`/`Last*Scan` CRD additions, the `issue_outcome` REST endpoint, the scan loop, and the `tatara.io/egress: internet` pod-label stamp) into prod from `main`; add the cluster-side egress NetworkPolicy that allows internet egress only from pods labelled `tatara.io/egress=internet` and default-denies egress otherwise; and patch the live `tatara` Project CR with `spec.scm.cron` (mrScan/issueScan/brainstorm) plus `spec.scm.priorityLabel`, with `brainstorm.enabled: false` by default.

**Architecture:** Order is operator-first (CRD + controller + egress-label code must serve the new `spec.scm.cron` schema before the Project CR carrying it is applied, else the field is pruned/rejected), NetworkPolicy second (cluster-side, label-gated, independent of the operator roll), Project CR last (declarative re-apply of `deploy-samples/tatara-project.yaml`, never `kubectl patch`). The operator chart `version:` + `image.tag` pins bump to the new operator appVersion in the infra helmfile; `helmfile diff` then `helmfile apply` from `main`; rollout-restart because the manager reads image-pin envs only at startup (`internal/config/config.go`). The egress NetworkPolicy is a standalone cluster manifest (NOT chart-templated and NOT helmfile-rendered - it is a sibling of the standalone Project CR manifest, applied with `kubectl apply -f`), keyed on `tatara.io/egress: internet`; a companion default-deny-egress policy in the `tatara` namespace forces every pod without that label onto the deny path while leaving DNS open. The Project CR gains `spec.scm.cron.{mrScan,issueScan,brainstorm}` + `spec.scm.priorityLabel`, byte-for-byte from contract lock section 5, edited into the single declarative manifest and `kubectl apply -f`-ed.

**Tech Stack:** `kubectl` (server-side CRD apply + NetworkPolicy + Project CR), helmfile + mise, Harbor OCI charts/images, sops-secret-helper (verify-only here; no `set`), `robfig/cron/v3` 5-field schedules (validated server-side by the bumped operator).

Spec: `docs/superpowers/specs/2026-06-11-autonomous-cron-design.md` (section 15 item 4 is this runbook's scope). Contract lock: `docs/superpowers/specs/2026-06-11-autonomous-cron-contract-lock.md` (sections 3 and 5 govern the egress label and the `spec.scm.cron` shape this infra feeds). Prior runbook this mirrors: `docs/superpowers/plans/2026-06-09-scm-projects-infra.md`. All cluster config lives in `~/Documents/infra/helmfile/helmfiles/tatara` (CLAUDE.md rule 14). Build/deploy from `main` only (rule 10).

**Assumptions stated up front:** new operator chart/appVersion = `0.4.0` (minor bump: new CRD fields + controller activity, backward-compatible defaults). Wrapper image tag (`<WRAP_TAG>`) is whatever the wrapper runbook publishes carrying `issue_outcome` registration; the cluster pins it via the Project CR `spec.agent.image`, so this runbook folds the wrapper tag bump into the single Project-CR apply. If those exact versions differ at execution time, substitute the real published tags - the steps are otherwise unchanged.

---

## File Structure

| File | Create/Modify | Responsibility |
| --- | --- | --- |
| `~/Documents/infra/helmfile/helmfiles/tatara/helmfile.yaml.gotmpl` | Modify (line 47: `version: 0.3.0` -> `0.4.0`) | Bumps the `tatara-operator` release chart `version:` pin to the autonomous-cron chart version. |
| `~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/common.yaml` | Modify (line 6: `tag: "0.3.0"` -> `"0.4.0"`) | Bumps the operator `image.tag` pin in lockstep with the chart version. |
| `~/Documents/tatara/tatara-operator/deploy-samples/tatara-egress-networkpolicy.yaml` | Create | Standalone cluster-side egress NetworkPolicy: allow internet egress only from `tatara.io/egress=internet`; default-deny egress (DNS-open) for every other pod in ns `tatara`. NOT chart-templated, NOT helmfile-rendered; applied via `kubectl apply -f`. |
| `~/Documents/tatara/tatara-operator/deploy-samples/tatara-project.yaml` | Modify (Project `spec.scm`, after line 12; `spec.agent.image` line 9 if wrapper bumped) | Single declarative source of the `tatara` Project CR; gains `spec.scm.priorityLabel` + `spec.scm.cron.{mrScan,issueScan,brainstorm}`. Re-applied with `kubectl apply -f` (Project is not chart-templated; no `kubectl patch`). |
| `~/Documents/tatara/tatara-operator/MEMORY.md` | Modify | Records the dated operator 0.4.0 roll, the egress NetworkPolicy name, and that the Project carries `spec.scm.cron` with `brainstorm.enabled: false`. |

No sops/secret edits in this runbook: `scmToken` + `scmWebhookSecret` already landed in the prior runbook (`2026-06-09-scm-projects-infra.md` Task 2), and the bot account + PAT (with the scopes the cron read/close paths reuse) were gated there. The cron feature adds no new SCM scope beyond Contents/Pull-requests/Issues/Projects already granted to `tatara-bot`.

---

## Task 1: Bump operator chart + image pins to the autonomous-cron appVersion

**Files:**
- Modify: `~/Documents/infra/helmfile/helmfiles/tatara/helmfile.yaml.gotmpl` (line 47)
- Modify: `~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/common.yaml` (line 6)

The autonomous-cron operator image + chart must already be published to Harbor from `tatara-operator` `main` (image/chart publishing is out of this runbook's scope; this task pins the published artifacts). Confirm the published version, then bump both pins in lockstep (the chart's `image.tag` defaults to `appVersion` when empty, but the bucket pins it explicitly).

- [ ] **Step 1: Confirm the published operator chart/app version (agent-runnable, read-only).** Determine the new operator chart `version`/`appVersion` carrying the cron CRD additions, the scan loop, and the `issue_outcome` endpoint (from the operator repo's `Chart.yaml` on `main`). Record it as `OP_VER` for the edits below.
```bash
grep -nE '^version:|^appVersion:' ~/Documents/tatara/tatara-operator/charts/tatara-operator/Chart.yaml
```
Expected: the operator chart version/appVersion including the autonomous-cron feature (`0.4.0` per the assumption above). If it still reads `0.3.0`, the operator build has not been published yet - block on the operator plan before continuing.

- [ ] **Step 2: Bump the operator chart `version:` pin in the helmfile (agent-runnable).** Only the `tatara-operator` release version changes; `tatara-chat` stays at `0.1.0`.
```bash
cd ~/Documents/infra/helmfile
sed -i '' 's/^\(  version: \)0\.3\.0$/\10.4.0/' helmfiles/tatara/helmfile.yaml.gotmpl
grep -n 'version:' helmfiles/tatara/helmfile.yaml.gotmpl
```
Expected: the `tatara-operator` release `version:` now reads `0.4.0`; `tatara-chat` still `0.1.0`. (Edit by hand if both releases ever share the same literal so only the operator release changes.)

- [ ] **Step 3: Bump the operator `image.tag` pin in lockstep (agent-runnable).**
```bash
cd ~/Documents/infra/helmfile
sed -i '' 's/^\(  tag: "\)0\.3\.0"$/\10.4.0"/' helmfiles/tatara/values/tatara-operator/common.yaml
grep -n 'tag:' helmfiles/tatara/values/tatara-operator/common.yaml
```
Expected: `tag: "0.4.0"`.

---

## Task 2: Server-side apply the bumped CRD (manual, precedes the helm roll)

**Files:** none (applies the CRD shipped in the operator chart's `crds/` dir from `main`).

The operator chart ships CRDs under `charts/tatara-operator/crds/` (`tatara.dev_projects.yaml`, `tatara.dev_tasks.yaml`, ...). Helm installs CRDs from `crds/` only on first install and never upgrades them on `helm upgrade` (standard helm CRD behavior). The autonomous-cron feature adds fields to BOTH the Project CRD (`spec.scm.priorityLabel`, `spec.scm.cron.*`, `status.lastMRScan|lastIssueScan|lastBrainstorm`) and the Task CRD (`spec.kind` enum gains `triageIssue;brainstorm`; `status.issueOutcome`). Those additions must be applied to the live CRDs manually with server-side apply BEFORE the helm roll, or the new `spec.scm.cron` field on the Project CR (Task 4) is pruned/rejected and the operator cannot persist `status.issueOutcome`.

- [ ] **Step 1: Diff the published CRDs against the live cluster CRDs (agent-runnable, read-only).** Confirm the new fields are present in the chart CRDs from `main` and absent live (so the apply is the delta).
```bash
# Project CRD: new spec.scm.cron + priorityLabel + status.last*Scan must appear in the chart copy.
grep -nE 'cron|priorityLabel|lastMRScan|lastIssueScan|lastBrainstorm' \
  ~/Documents/tatara/tatara-operator/charts/tatara-operator/crds/tatara.dev_projects.yaml
# Task CRD: enum must include triageIssue + brainstorm; status.issueOutcome must exist.
grep -nE 'triageIssue|brainstorm|issueOutcome' \
  ~/Documents/tatara/tatara-operator/charts/tatara-operator/crds/tatara.dev_tasks.yaml
# Live cluster (expect these to be MISSING until Step 2 applies them):
kubectl get crd projects.tatara.dev -o jsonpath='{.spec.versions[0].schema.openAPIV3Schema.properties.spec.properties.scm.properties.cron}{"\n"}'
kubectl get crd tasks.tatara.dev -o jsonpath='{.spec.versions[0].schema.openAPIV3Schema.properties.spec.properties.kind.enum}{"\n"}'
```
Expected: chart grep hits all new field names; live `projects` `scm.cron` prints empty and live `tasks` `kind.enum` prints `["implement","review","selfImprove"]` (no `triageIssue`/`brainstorm`) - confirming the delta to apply.

- [ ] **Step 2: [USER-GATED] Server-side apply the bumped Project + Task CRDs (prod CRD mutation).** Prod-mutating cluster schema change; run as human or with explicit go-ahead. Server-side apply preserves fields managed by other controllers and is the correct verb for CRD field additions:
```bash
kubectl apply --server-side --force-conflicts \
  -f ~/Documents/tatara/tatara-operator/charts/tatara-operator/crds/tatara.dev_projects.yaml \
  -f ~/Documents/tatara/tatara-operator/charts/tatara-operator/crds/tatara.dev_tasks.yaml
```
Expected: `customresourcedefinition.apiextensions.k8s.io/projects.tatara.dev serverside-applied` and the same for `tasks.tatara.dev`. (`--force-conflicts` resolves field-manager conflicts from the prior helm-install of the CRD; the schema-only change is non-destructive to existing CRs.)

- [ ] **Step 3: Verify the live CRDs now serve the new fields (agent-runnable, read-only).**
```bash
kubectl get crd projects.tatara.dev -o jsonpath='{.spec.versions[0].schema.openAPIV3Schema.properties.spec.properties.scm.properties.cron.properties}{"\n"}' \
  | tr ',' '\n' | grep -E 'mrScan|issueScan|brainstorm' && echo "project cron schema present"
kubectl get crd projects.tatara.dev -o jsonpath='{.spec.versions[0].schema.openAPIV3Schema.properties.spec.properties.scm.properties.priorityLabel}{"\n"}' \
  && echo "priorityLabel schema present"
kubectl get crd tasks.tatara.dev -o jsonpath='{.spec.versions[0].schema.openAPIV3Schema.properties.spec.properties.kind.enum}{"\n"}'
```
Expected: `mrScan`, `issueScan`, `brainstorm` listed and `project cron schema present`; a `priorityLabel` string schema and `priorityLabel schema present`; the Task `kind.enum` now reads `["implement","review","selfImprove","triageIssue","brainstorm"]`. If any is missing, Step 2 did not apply - re-run before Task 4.

---

## Task 3: Add the cluster-side egress NetworkPolicy (rule 14, label-gated internet egress)

**Files:**
- Create: `~/Documents/tatara/tatara-operator/deploy-samples/tatara-egress-networkpolicy.yaml`

Contract lock section 3 freezes the pod label `tatara.io/egress: internet` (exact key/value), stamped by the operator on brainstorm pods only when `internet` is in `spec.scm.cron.brainstorm.sources`. The chart bakes nothing cluster-specific (rule 14), so the allow-rule + the default-deny live in a cluster-side standalone manifest, a sibling of the declarative Project CR manifest (NOT chart-templated, NOT helmfile-rendered; applied with `kubectl apply -f`). Two policies in ns `tatara`:

1. `tatara-egress-internet` - allows TCP 443 (HTTPS) + UDP/TCP 53 (DNS) egress from pods carrying `tatara.io/egress: internet`. This is additive: it grants internet reach to the brainstorm pod.
2. `tatara-default-deny-egress` - selects every pod in the namespace, declares `policyTypes: [Egress]` with only a DNS allow rule. In Kubernetes a pod selected by any Egress policy is default-deny for all egress not explicitly allowed; this policy therefore denies all egress except DNS for pods that are NOT also selected by an allow policy. Pods labelled `tatara.io/egress: internet` are additionally selected by policy 1, so their 443 egress is restored; everything else is DNS-only.

The existing chart policy `tatara-operator-managed-pods` (selects `tatara.dev/managed-by: tatara-operator`, already allows broad 443) governs the spawned agent/ingest pods' SCM + in-cluster reach. The brainstorm pod carries BOTH `tatara.dev/managed-by: tatara-operator` (it is operator-spawned) AND, when `internet` sources, `tatara.io/egress: internet`. NetworkPolicies are additive (union of allows), so the brainstorm pod's egress is the union of the managed-pods policy and `tatara-egress-internet` - which is the intended internet reach. The new default-deny does not narrow the managed-pods policy (both are Egress policies; their allow rules union). This runbook's `tatara-default-deny-egress` exists to close egress for any pod in the namespace that is NOT covered by an allow policy (defense in depth, matching the spec's "default-deny otherwise").

- [ ] **Step 1: Create the standalone egress NetworkPolicy manifest (agent-runnable).** Write `~/Documents/tatara/tatara-operator/deploy-samples/tatara-egress-networkpolicy.yaml` with exactly:
```yaml
# Cluster-side egress policy for the autonomous-cron brainstorm feature.
# NOT chart-templated and NOT helmfile-rendered (rule 14: the chart bakes nothing
# cluster-specific). Applied with `kubectl apply -f`, a sibling of the standalone
# Project CR manifest. Keys on the operator-stamped pod label
# `tatara.io/egress: internet` (contract lock section 3), which the operator adds
# to brainstorm pods only when `internet` is in spec.scm.cron.brainstorm.sources.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: tatara-egress-internet
  namespace: tatara
  labels:
    app.kubernetes.io/part-of: tatara
    app.kubernetes.io/managed-by: kubectl
spec:
  # Only pods the operator stamped with the egress label get internet reach.
  podSelector:
    matchLabels:
      tatara.io/egress: internet
  policyTypes:
    - Egress
  egress:
    # DNS resolution.
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
    # Outbound HTTPS to the internet (WebSearch/WebFetch). Broad 443, matching the
    # managed-pods policy's existing SCM egress rule; CIDR tightening deferred.
    - to:
        - namespaceSelector: {}
      ports:
        - port: 443
          protocol: TCP
---
# Default-deny egress for every pod in ns tatara that is NOT selected by an allow
# policy. Selected pods are default-deny for all egress except the DNS rule below;
# pods carrying `tatara.io/egress: internet` are additionally selected by
# tatara-egress-internet (above) and so regain 443 egress (policies are additive).
# The operator manager + spawned agent/ingest pods keep their reach via the chart's
# tatara-operator-managed-pods policy (also additive).
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: tatara-default-deny-egress
  namespace: tatara
  labels:
    app.kubernetes.io/part-of: tatara
    app.kubernetes.io/managed-by: kubectl
spec:
  podSelector: {}
  policyTypes:
    - Egress
  egress:
    # DNS is always allowed so name resolution never breaks; all other egress for
    # an otherwise-unmatched pod is denied by virtue of being selected here.
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
```
Expected: file created. Validate it parses (dry-run, agent-runnable, read-only - does not mutate the cluster):
```bash
kubectl apply --dry-run=client -f ~/Documents/tatara/tatara-operator/deploy-samples/tatara-egress-networkpolicy.yaml
```
Expected: `networkpolicy.networking.k8s.io/tatara-egress-internet created (dry run)` and `... /tatara-default-deny-egress created (dry run)`, no schema error.

- [ ] **Step 2: Confirm the existing managed-pods egress allow still covers the operator + spawned pods (agent-runnable, read-only).** Before adding a namespace-wide default-deny, verify the chart policy that keeps the manager and agent/ingest pods reachable is live, so the new default-deny does not strand them.
```bash
kubectl -n tatara get networkpolicy tatara-operator-managed-pods -o jsonpath='{.spec.podSelector.matchLabels}{"\n"}'
kubectl -n tatara get networkpolicy -o name
```
Expected: the managed-pods policy selects `tatara.dev/managed-by: tatara-operator` and is present. (If it is absent, the operator chart is below the version that ships it - resolve before applying the default-deny, or operator-spawned pods lose egress.)

- [ ] **Step 3: [USER-GATED] Apply the egress NetworkPolicy to the cluster (prod policy mutation).** Cluster-side network mutation; run as human or with explicit go-ahead. This is independent of the helm roll and can be applied before or after Task 1's apply, but MUST be live before the first brainstorm cron fires.
```bash
kubectl apply -f ~/Documents/tatara/tatara-operator/deploy-samples/tatara-egress-networkpolicy.yaml
```
Expected: `networkpolicy.networking.k8s.io/tatara-egress-internet created` and `... /tatara-default-deny-egress created`.

- [ ] **Step 4: Verify the policies are live and the label selector is correct (agent-runnable, read-only).**
```bash
kubectl -n tatara get networkpolicy tatara-egress-internet \
  -o jsonpath='{.spec.podSelector.matchLabels}{"\n"}'
kubectl -n tatara get networkpolicy tatara-default-deny-egress \
  -o jsonpath='{.spec.podSelector}{" types="}{.spec.policyTypes}{"\n"}'
```
Expected: `tatara-egress-internet` selects `{"tatara.io/egress":"internet"}`; `tatara-default-deny-egress` has an empty podSelector `{}` and `policyTypes ["Egress"]`. Existing in-cluster traffic (operator -> agent, agent -> memory/chat) is unaffected because those flows are governed by the additive `tatara-operator-managed-pods` policy.

- [ ] **Step 5: Commit the manifest to the operator repo (no secrets; agent-runnable).**
```bash
cd ~/Documents/tatara/tatara-operator
git add deploy-samples/tatara-egress-networkpolicy.yaml
git commit -m "feat: cluster-side egress NetworkPolicy for brainstorm internet pods (tatara.io/egress=internet)"
```

---

## Task 4: Patch the Project CR with spec.scm.cron + priorityLabel (declarative re-apply, NOT kubectl patch)

**Files:**
- Modify: `~/Documents/tatara/tatara-operator/deploy-samples/tatara-project.yaml` (Project `spec.scm` block; `spec.agent.image` only if the wrapper tag bumped)

The `Project` CR is NOT templated by the operator chart; `deploy-samples/tatara-project.yaml` is its single declarative source. Edit the manifest (the source of truth) and `kubectl apply -f` it, so the live spec never drifts from git. Do NOT `kubectl patch project ...`. The `spec.scm.cron` + `spec.scm.priorityLabel` field shapes are byte-for-byte from contract lock section 5: `priorityLabel` (string), `cron.mrScan.{schedule,maxPerCycle}`, `cron.issueScan.{schedule,maxPerCycle}`, `cron.brainstorm.{enabled,schedule,maxPerCycle,sources}`. `sources` enum values are exactly `docs`, `memory`, `internet`.

The prior SCM-projects runbook already added a `spec.scm` block with `provider/owner/botLogin/board/mergePolicy/prReactionScope/approvalLabel`. This runbook ASSUMES that block is live on `deploy-samples/tatara-project.yaml`. The current committed manifest (lines 4-12) carries only `scmSecretRef/triggerLabel/maxConcurrentTasks/agent/memory` and no `spec.scm` at all - so before adding `cron`, confirm whether the SCM-projects block landed; if it did not, add the full `spec.scm` block (from the prior runbook) AND the `cron`/`priorityLabel` additions together in one edit.

- [ ] **Step 1: Confirm the operator CRD serves spec.scm.cron + priorityLabel (agent-runnable, read-only).** Guard: applying a Project with an unknown `spec.scm.cron` field is pruned/rejected. Task 2 must have applied the bumped CRD first.
```bash
kubectl get crd projects.tatara.dev \
  -o jsonpath='{.spec.versions[0].schema.openAPIV3Schema.properties.spec.properties.scm.properties.cron.properties.brainstorm.properties}{"\n"}' \
  | tr ',' '\n' | grep -E 'enabled|schedule|maxPerCycle|sources' && echo "brainstorm cron schema present"
```
Expected: `enabled`, `schedule`, `maxPerCycle`, `sources` listed and `brainstorm cron schema present`. If empty, run Task 2 (CRD apply) before continuing.

- [ ] **Step 2: Inspect the live Project spec.scm to determine the base block state (agent-runnable, read-only).** Decides whether Step 3 adds only `cron`+`priorityLabel` or the full `scm` block.
```bash
kubectl -n tatara get project tatara -o jsonpath='{.spec.scm}{"\n"}'
grep -n 'scm:' ~/Documents/tatara/tatara-operator/deploy-samples/tatara-project.yaml || echo "no scm block in manifest yet"
```
Expected: either a JSON object with `provider/owner/botLogin/...` (SCM-projects block already live - Step 3 appends only `cron`+`priorityLabel`), OR empty/`no scm block` (the SCM-projects block never landed - Step 3 must add the full `scm` block plus the cron additions).

- [ ] **Step 3: Edit the Project manifest to add spec.scm.cron + spec.scm.priorityLabel (agent-runnable).** Open `~/Documents/tatara/tatara-operator/deploy-samples/tatara-project.yaml`. Under the `tatara` Project's `spec.scm:` block (insert the whole `scm:` block after the `agent:`/`memory:` lines if it is absent per Step 2), the `priorityLabel` + `cron` additions are EXACTLY (byte-for-byte from contract lock section 5; `brainstorm.enabled: false` is the safe default, the three schedules are the design's defaults - mrScan/issueScan hourly, brainstorm daily 06:00):
```yaml
    priorityLabel: tatara/priority
    cron:
      mrScan:
        schedule: "0 * * * *"
        maxPerCycle: 1
      issueScan:
        schedule: "0 * * * *"
        maxPerCycle: 1
      brainstorm:
        enabled: false
        schedule: "0 6 * * *"
        maxPerCycle: 1
        sources:
          - docs
          - memory
          - internet
```
If Step 2 found NO `scm` block in the manifest, prepend the SCM-projects base (from `2026-06-09-scm-projects-infra.md` Task 4 Step 2: `provider: github`, `owner: szymonrychu`, `botLogin: tatara-bot`, `board.{githubProjectNumber,statusField}`, `mergePolicy: afterApproval`, `prReactionScope: labeledOrMentioned`, `approvalLabel: tatara/awaiting-approval`) above these `priorityLabel`/`cron` keys, all under the same `scm:` map. The complete `scm:` block then reads:
```yaml
  scm:
    provider: github
    owner: szymonrychu
    botLogin: tatara-bot
    board:
      githubProjectNumber: <GITHUB_PROJECT_NUMBER>
      statusField: Status
    mergePolicy: afterApproval
    prReactionScope: labeledOrMentioned
    approvalLabel: tatara/awaiting-approval
    priorityLabel: tatara/priority
    cron:
      mrScan:
        schedule: "0 * * * *"
        maxPerCycle: 1
      issueScan:
        schedule: "0 * * * *"
        maxPerCycle: 1
      brainstorm:
        enabled: false
        schedule: "0 6 * * *"
        maxPerCycle: 1
        sources:
          - docs
          - memory
          - internet
```
Substitute `<GITHUB_PROJECT_NUMBER>` from the value recorded in the operator `MEMORY.md` by the prior runbook. `brainstorm.enabled: false` keeps the internet-egress path dormant until a human opts in; `mrScan`/`issueScan` are active hourly with `maxPerCycle: 1` so the first live cycle picks at most one MR and one issue.

- [ ] **Step 4: Bump spec.agent.image to the new wrapper tag if the wrapper was rebuilt (agent-runnable).** The wrapper image (carrying `issue_outcome` registration) is consumed via the Project CR `spec.agent.image`, not an operator value. If the wrapper runbook published a new tag, edit line 9 of the manifest and fold the change into this same apply (one prod apply, not two).
```bash
grep -n 'tatara-claude-code-wrapper' ~/Documents/tatara/tatara-operator/deploy-samples/tatara-project.yaml
```
Expected: `agent.image: harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper:<WRAP_TAG>`. The current manifest pins `0.1.8`; if the wrapper runbook published a newer tag carrying `issue_outcome`, replace `0.1.8` with that tag here. If the wrapper image is unchanged, leave it.

- [ ] **Step 5: Validate the edited manifest parses and the cron block is well-formed (agent-runnable, read-only).** Server dry-run validates against the live CRD schema without mutating the CR.
```bash
kubectl apply --dry-run=server -f ~/Documents/tatara/tatara-operator/deploy-samples/tatara-project.yaml
```
Expected: `project.tatara.dev/tatara configured (server dry run)` and the Repository objects unchanged, with NO pruning warning on `spec.scm.cron` (server dry-run against the Task-2 CRD proves the field is accepted). A `cron` validation error here means Task 2 was skipped or the YAML indentation is off - fix before the real apply.

- [ ] **Step 6: [USER-GATED] Apply the updated Project manifest (prod CR mutation).** Declarative apply of the source-of-truth manifest - never `kubectl patch`. Prod-mutating; run as human or with explicit go-ahead:
```bash
kubectl apply -f ~/Documents/tatara/tatara-operator/deploy-samples/tatara-project.yaml
```
Expected: `project.tatara.dev/tatara configured` (and the Repository objects `unchanged`).

- [ ] **Step 7: Verify the live spec carries the cron block (agent-runnable, read-only).**
```bash
kubectl -n tatara get project tatara -o jsonpath='{.spec.scm.cron}{"\n"}'
kubectl -n tatara get project tatara -o jsonpath='{.spec.scm.priorityLabel}{"\n"}'
```
Expected: the `cron` JSON object with `mrScan.schedule:"0 * * * *"`, `issueScan.schedule:"0 * * * *"`, `brainstorm.{enabled:false,schedule:"0 6 * * *",maxPerCycle:1,sources:["docs","memory","internet"]}`; `priorityLabel` reads `tatara/priority`. No field pruned (confirms the CRD schema served it).

- [ ] **Step 8: Commit the manifest change (no secrets; agent-runnable).**
```bash
cd ~/Documents/tatara/tatara-operator
git add deploy-samples/tatara-project.yaml
git commit -m "feat: tatara Project carries scm.cron (mrScan/issueScan/brainstorm) + priorityLabel"
```

---

## Task 5: helmfile diff -> apply from main; rollout-restart the operator

**Files:** none (applies the Task 1 pin bumps).

The operator reads image-pin envs ONLY at startup (`internal/config/config.go`). A `helm apply` that changes the Deployment image triggers a rollout, but a rollout-restart is still forced to guarantee the manager re-reads every image-pin env from the bumped ConfigMap. Build from `main` only (rule 10).

- [ ] **Step 1: helmfile diff (agent-runnable, read-only).**
```bash
cd ~/Documents/infra/helmfile
[[ -f .env ]] && echo "dotenv present" || echo "no dotenv (run op_env_dotfile first)"
mise exec -- helmfile -e default -l application=tatara-operator diff
```
Expected: the diff shows the operator Deployment image bumped to `0.4.0`, the chart `version` change `0.3.0 -> 0.4.0`, and no spurious churn in unrelated objects. The CRD diff is empty because Task 2 already server-side-applied the CRDs (helm does not upgrade `crds/`); confirm the diff does NOT try to revert the CRD schema.

- [ ] **Step 2: [USER-GATED] helmfile apply from main (prod deploy).** Prod-mutating; run as human or with explicit go-ahead. Must be from `main` of the infra repo (rule 10):
```bash
cd ~/Documents/infra/helmfile
git rev-parse --abbrev-ref HEAD    # confirm: main
mise exec -- helmfile -e default -l application=tatara-operator apply
```
Expected: the release upgrades cleanly to chart `0.4.0`.

- [ ] **Step 3: [USER-GATED] Rollout-restart the operator (it reads image-pin envs at startup).** Force a restart to guarantee the manager re-reads every image-pin env from the bumped ConfigMap:
```bash
kubectl -n tatara rollout restart deploy/tatara-operator
kubectl -n tatara rollout status deploy/tatara-operator --timeout=300s
```
Expected: `deployment "tatara-operator" successfully rolled out`.

- [ ] **Step 4: Verify the operator is healthy and serving the cron CRD + new endpoint (agent-runnable, read-only).**
```bash
kubectl -n tatara get pods -l app.kubernetes.io/name=tatara-operator
kubectl -n tatara get deploy tatara-operator -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
kubectl get crd projects.tatara.dev \
  -o jsonpath='{.spec.versions[0].schema.openAPIV3Schema.properties.spec.properties.scm.properties.cron}{"\n"}' | head -c 120; echo
```
Expected: operator pod `Running`/ready; the Deployment image reads `...tatara-operator:0.4.0`; the CRD serves `spec.scm.cron`. (The `issue_outcome` REST route `POST /tasks/{t}/issue-outcome` is served by the bumped operator binary; it is exercised end-to-end in Task 6.)

- [ ] **Step 5: Commit the infra pin bumps (no secrets; agent-runnable).**
```bash
cd ~/Documents/infra/helmfile
git add helmfiles/tatara/helmfile.yaml.gotmpl helmfiles/tatara/values/tatara-operator/common.yaml
git commit -m "chore(tatara): bump operator chart+image to 0.4.0 (autonomous-cron feature)"
```

---

## Task 6: Verify the first cron cycle + record close-out

**Files:**
- Modify: `~/Documents/tatara/tatara-operator/MEMORY.md`

- [ ] **Step 1: Confirm the operator records a scan timestamp on the first cron fire (agent-runnable, read-only).** With `mrScan`/`issueScan` scheduled hourly, the reconciler writes `status.lastMRScan`/`status.lastIssueScan` on the first fire. Wait for the top of the hour (or the next reconcile after apply) and check:
```bash
kubectl -n tatara get project tatara -o jsonpath='LastMRScan={.status.lastMRScan} LastIssueScan={.status.lastIssueScan} LastBrainstorm={.status.lastBrainstorm}{"\n"}'
```
Expected: `LastMRScan` and `LastIssueScan` populated with RFC3339 timestamps after the first hourly fire; `LastBrainstorm` empty (brainstorm disabled). If both MR/Issue timestamps stay empty past the first scheduled tick, check the operator logs for a malformed-cron error (a bad schedule disables that activity without crashing, per design section 4).

- [ ] **Step 2: Confirm cron-created Tasks carry the dedup labels (agent-runnable, read-only).** If there are open MRs/issues on enrolled repos, the scan creates Tasks (capped at `maxPerCycle: 1` each) labelled per contract lock section 2.
```bash
kubectl -n tatara get tasks \
  -l tatara.io/activity \
  -o custom-columns='NAME:.metadata.name,KIND:.spec.kind,ACTIVITY:.metadata.labels.tatara\.io/activity,SRCREPO:.metadata.labels.tatara\.io/source-repo,SRCNUM:.metadata.labels.tatara\.io/source-number,SRCKIND:.metadata.labels.tatara\.io/source-kind'
```
Expected: zero or more Tasks; any present carry `tatara.io/activity` in `{mrScan,issueScan}`, `KIND` in `{review,selfImprove,triageIssue}`, and `tatara.io/source-repo`/`tatara.io/source-number` set. (No open MR/issue -> no Task, which is also a valid pass: the timestamp in Step 1 is the proof the scan ran.)

- [ ] **Step 3: Confirm the scan metrics are exposed (agent-runnable, read-only).** The bumped operator registers the autonomous-cron counters/histogram (contract lock section 9).
```bash
kubectl -n tatara exec deploy/tatara-operator -- wget -qO- localhost:9090/metrics 2>/dev/null \
  | grep -E 'tatara_scan_items_total|tatara_scan_tasks_created_total|tatara_scan_duration_seconds|tatara_issue_outcome_total' | head
```
Expected: `tatara_scan_items_total{activity=...,outcome=...}`, `tatara_scan_duration_seconds`, and (once any scan ran) `tatara_scan_tasks_created_total` present. `tatara_issue_outcome_total` appears after the first `issue_outcome` write. (Confirm the metrics port matches the operator's `/metrics` listener; adjust `9090` if the chart exposes a different port.)

- [ ] **Step 4: Record close-out in MEMORY.md (agent-runnable).** One dated line each: operator rolled to `0.4.0` carrying autonomous-cron; the egress NetworkPolicy `tatara-egress-internet` + `tatara-default-deny-egress` are live in ns `tatara` keyed on `tatara.io/egress: internet`; the Project carries `spec.scm.cron` with `brainstorm.enabled: false` (internet egress dormant until a human flips it). Then commit:
```bash
cd ~/Documents/tatara/tatara-operator
git add MEMORY.md
git commit -m "docs: record autonomous-cron operator roll + egress NetworkPolicy + cron block (dormant brainstorm)"
```

---

## Notes

- **User-gated steps** (prod/cluster mutation; agent must NOT run): Task 2 Step 2 (server-side CRD apply); Task 3 Step 3 (`kubectl apply` of the egress NetworkPolicy); Task 4 Step 6 (`kubectl apply` of the Project CR); Task 5 Steps 2-3 (`helmfile apply` + rollout restart). Everything else (reads, `--dry-run`, edits to git-tracked manifests, `helmfile diff`, commits) is agent-runnable.
- **No sops / no account changes:** unlike the prior SCM-projects runbook, this feature adds NO new secret and NO new SCM scope. `scmToken`/`scmWebhookSecret` and the `tatara-bot` account + PAT (Contents/Pull-requests/Issues/Projects scopes) were gated in `2026-06-09-scm-projects-infra.md`; the cron read/close paths (`ListOpenPRs`/`ListOpenIssues`/`ListBoardItems`/`CloseIssue`) reuse those exact scopes.
- **Order dependency:** Task 2 (CRD server-side apply) must precede Task 4's apply (Task 4 Step 1 guards this) and precede Task 5's helm roll is NOT required (helm never upgrades `crds/`, so the CRD must be applied manually regardless). Strict top-to-bottom order is: Task 1 (edit pins) -> Task 2 (CRD apply) -> Task 3 (NetworkPolicy) -> Task 4 (Project CR) -> Task 5 (helm roll) -> Task 6 (verify).
- **Never `kubectl patch` the Project spec** (CLAUDE.md): the Project is declaratively managed via `deploy-samples/tatara-project.yaml`; the `scm.cron` block is edited there and `kubectl apply -f`-ed, so live state never drifts from git.
- **NetworkPolicy is cluster-side, not chart-baked** (rule 14): the operator chart bakes nothing cluster-specific; the egress allow + default-deny live in the standalone `deploy-samples/tatara-egress-networkpolicy.yaml`, applied by hand, keyed on the frozen `tatara.io/egress: internet` label (contract lock section 3).
- **Brainstorm dormant by default:** `brainstorm.enabled: false` means the operator never stamps `tatara.io/egress: internet` on any pod yet, so the internet-egress allow rule is inert until a human opts in - the blast radius is zero on first roll.

---

## Self-Review (per superpowers:writing-plans)

**Spec coverage (design section 15 item 4 + contract lock sections 3, 5):**
- Operator chart `version:` + `image.tag` bump to the new appVersion: Task 1 (both pins, `0.3.0 -> 0.4.0`). ✓
- `helm crds/` manual server-side apply note: Task 2 (`kubectl apply --server-side --force-conflicts` on both Project + Task CRDs, with the explicit rationale that helm never upgrades `crds/`). ✓
- `helmfile diff -> apply` gated, build/deploy from `main` only: Task 5 Steps 1-2 (diff agent-runnable, apply USER-GATED, `git rev-parse` confirms `main`). ✓
- Egress NetworkPolicy (rule 14) allowing egress from `tatara.io/egress=internet`, default-deny otherwise, with the YAML shown: Task 3 Step 1 (full YAML for `tatara-egress-internet` allow + `tatara-default-deny-egress`). ✓
- Patch Project CR with `spec.scm.cron` (mrScan/issueScan/brainstorm) + `spec.scm.priorityLabel`, exact YAML fragment from lock section 5, `brainstorm.enabled: false`, three cron schedules: Task 4 Step 3 (byte-for-byte fragment). ✓
- Note that no sops/account changes are needed beyond the prior runbook: Notes block + File Structure preamble. ✓

**Placeholder scan:** no `TBD` / `add appropriate` / `similar to Task N` in any code/YAML block. The only `<...>` tokens are deliberate operator-supplied values - `<GITHUB_PROJECT_NUMBER>` (recorded in operator MEMORY.md by the prior runbook) and `<WRAP_TAG>` (the wrapper runbook's published tag) - each with an explicit source. The assumed `0.4.0` operator version is stated up front and used as a concrete literal throughout (Task 1 `sed` targets `0.3.0`).

**Type / name consistency vs contract lock (byte-for-byte):**
- Egress pod label `tatara.io/egress: internet` (lock section 3): used verbatim in the NetworkPolicy `podSelector.matchLabels` (Task 3 Step 1) and the verification jsonpath (Task 3 Step 4). ✓
- Dedup labels `tatara.io/activity`, `tatara.io/source-repo`, `tatara.io/source-number`, `tatara.io/source-kind` (lock section 2): used verbatim in the Task 6 Step 2 label selector + custom-columns. ✓
- CRD fields `spec.scm.priorityLabel`, `spec.scm.cron.{mrScan,issueScan,brainstorm}`, `cron.*.{schedule,maxPerCycle}`, `brainstorm.{enabled,schedule,maxPerCycle,sources}`, `sources` enum `docs;memory;internet` (lock section 5): the Task 4 Step 3 YAML uses these exact keys and enum strings; `status.{lastMRScan,lastIssueScan,lastBrainstorm}` (lock section 5) used verbatim in Task 6 Step 1. ✓
- Task `kind` enum adds `triageIssue;brainstorm` (lock section 1): verified in Task 2 Steps 1/3. ✓
- REST route `POST /tasks/{t}/issue-outcome` (lock section 7) and metrics `tatara_scan_items_total`/`tatara_scan_tasks_created_total`/`tatara_scan_duration_seconds`/`tatara_issue_outcome_total` (lock section 9): referenced verbatim in Task 5 Step 4 + Task 6 Step 3 (verification scope only - infra does not define them, the operator does). ✓
