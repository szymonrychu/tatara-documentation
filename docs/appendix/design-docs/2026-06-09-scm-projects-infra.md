# SCM Projects Infra/Deploy RUNBOOK Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to drive this runbook, BUT this is an operations runbook (account creation + sops + CR re-apply + helm), NOT TDD code. There is no test-first cycle. Steps use checkbox (`- [ ]`) syntax; each step is a concrete operator action with the exact command. Steps marked **[USER-GATED]** MUST be performed by the human (prod mutation, outward-facing SCM writes, or interactive secret-setting from a tty); the agent prepares and verifies around them but does not run them.

**Goal:** Stand up the SCM-Projects feature in prod: a dedicated `tatara-bot` GitHub + GitLab identity with a per-Project fine-grained PAT, the board + approval label, the `scm` block patched onto each `Project` CR via the declarative manifest (never `kubectl patch` on a helm/declaratively-managed spec), bumped operator/cli/wrapper image+chart pins applied from `main`, and the expanded webhook event set registered on every enrolled repo.

**Architecture:** Identity + secrets land first (bot accounts, FG-PAT, sops `token`/`webhookSecret`). The board (GitHub Projects v2 `tatara` / GitLab issue board) + `tatara/awaiting-approval` label + `Proposed` column are created and their numeric ids captured. The `Project` CR carries a new `spec.scm` block (provider/owner/botLogin/board/mergePolicy/prReactionScope/approvalLabel) edited into the declarative sample manifest and re-applied (the Project is NOT chart-templated, so this is `kubectl apply -f` of the single source of truth, not an ad-hoc patch). Operator/cli/wrapper image+chart pins are bumped in the infra helmfile, diffed, applied from `main`, then the operator is rollout-restarted because it reads image-pin envs only at startup (`internal/config/config.go`). Webhooks are registered per-repo because a fine-grained PAT has no org-webhook automation.

**Tech Stack:** GitHub (REST v3 + Projects v2 GraphQL), GitLab (REST), `gh` / `glab` CLIs, `kubectl`, helmfile + mise, sops-secret-helper, Harbor OCI charts/images.

Spec: `docs/superpowers/specs/2026-06-09-scm-projects-pr-reactions-design.md`. Contract lock: `docs/superpowers/specs/2026-06-09-scm-projects-contract-lock.md` (sections 2 and 5 govern the CR `scm` shape and the controller flow this infra feeds). All cluster config lives in `~/Documents/infra/helmfile/helmfiles/tatara` (CLAUDE.md rule 14). Build/deploy from `main` only (rule 10).

---

## File Structure

| File | Create/Modify | Responsibility |
| --- | --- | --- |
| `~/Documents/tatara/tatara-operator/deploy-samples/tatara-project.yaml` | Modify | Single declarative source of the `tatara` Project CR; gains the `spec.scm` block. Re-applied with `kubectl apply -f` (Project is not chart-templated; no `kubectl patch`). |
| `~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/default.secrets.yaml` | Modify (sops, USER-run `set`) | Holds `scmToken` (bot FG-PAT) + `scmWebhookSecret`; chart renders them into the `tatara-scm` Secret keys `token` + `webhookSecret`. |
| `~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/default.yaml` | Modify | Bumps `ingesterImage` if needed (no scm-feature change); confirms `scmSecretName: "tatara-scm"` present. |
| `~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/common.yaml` | Modify | Bumps operator `image.tag` pin to the new operator appVersion. |
| `~/Documents/infra/helmfile/helmfiles/tatara/helmfile.yaml.gotmpl` | Modify | Bumps operator chart `version:` pin to the new chart version. |
| `/tmp/tatara-board-ids.env` | Create (ephemeral) | Scratch capture of the board number/id + project node id for the GraphQL board ops; never committed. |

Board identifiers to be filled in by the operator and recorded in `MEMORY.md` once known:
- `GITHUB_PROJECT_NUMBER` = `<TBD-after-Task 3>` (the Projects v2 board number)
- `GITLAB_BOARD_ID` = `<TBD-after-Task 3, only if a GitLab Project exists>`
- `GITHUB_PROJECT_NODE_ID` = `<TBD-after-Task 3>` (GraphQL node id; used by the operator's `addProjectV2ItemById` board ops, not stored in the CR)

---

## Task 1: Dedicated `tatara-bot` identity + per-Project fine-grained PAT

**Files:** none (account + token provisioning). Capture the bot logins; they become `spec.scm.botLogin` in Task 4.

- [ ] **Step 1: [USER-GATED] Create the `tatara-bot` GitHub user.** A bot identity cannot be created by the agent (account signup, email verification). The human signs up a dedicated GitHub account (suggested login `tatara-bot`), enables 2FA, and confirms the login string. Record it as `GH_BOT_LOGIN=tatara-bot` (this exact string is the authoritative "is this mine" signal `spec.scm.botLogin`; contract lock section 2). The agent does NOT create accounts.

- [ ] **Step 2: [USER-GATED] Invite `tatara-bot` to the `szymonrychu` org and grant repo + org-Projects scope.** Human action in the org settings: add `tatara-bot` as a member, and on each enrolled repo grant the bot **Write** on Contents, Pull requests, and Issues. Grant the bot **read+write** on org-level **Projects** (so it can move cards on the `tatara` Projects v2 board). The agent cannot administer org membership. Verify (agent-runnable, read-only) once the human confirms:
```bash
gh api orgs/szymonrychu/members/tatara-bot --silent && echo "bot is org member"
gh api repos/szymonrychu/tatara-operator/collaborators/tatara-bot/permission \
  --jq '.permission'   # expect: write or admin
```
Expected: `bot is org member`; permission `write` (or `admin`).

- [ ] **Step 3: [USER-GATED] Mint the per-Project fine-grained PAT (GitHub).** Logged in AS `tatara-bot`, the human creates a fine-grained PAT scoped to the resource owner `szymonrychu`, selecting only the enrolled repos, with **Repository permissions**: Contents=Read and write, Pull requests=Read and write, Issues=Read and write, Metadata=Read (mandatory), Webhooks=Read and write (so Task 6 can register hooks with this same token); and **Organization permissions**: Projects=Read and write. No `admin:org`. The PAT value is the `token` key for the Project Secret. The agent never sees the token. Record nothing in git; the human keeps it for Step 5 of Task 2.

- [ ] **Step 4: [USER-GATED] Create the `tatara-bot` GitLab user + `api` token (only if a GitLab Project is enrolled).** Human signs up a dedicated GitLab user `tatara-bot`, adds it to the group/project with **Developer** (or **Maintainer** if MR-merge is required) role, then mints a Project (or group) access token / PAT with the **`api`** scope (covers issues, MR notes/approvals, award emoji, board label edits per contract lock section 1). This token is the `token` key when `provider=gitlab`. Skip this task entirely if no GitLab Project is enrolled in this rollout.

---

## Task 2: Per-Project Secret keys `token` + `webhookSecret` (sops, USER-set)

**Files:**
- Modify: `~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/default.secrets.yaml` (sops; `set` is USER-run from a tty)
- Confirm: `~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/default.yaml` has `scmSecretName: "tatara-scm"`

Per the operator sops-managed-secrets design, the chart renders the `tatara-scm` Secret from sops values `scmToken` -> data key `token` and `scmWebhookSecret` -> data key `webhookSecret` (the keys `Project.Spec.ScmSecretRef` resolves; consumed at `internal/agent/pod.go`, `internal/ingest/job.go`, `internal/webhook/server.go`). Setting these is the **only** secret path; do NOT `kubectl create secret` by hand for an sops-managed cluster.

- [ ] **Step 1: Confirm the secrets file exists and list its current keys (agent-runnable, no values).**
```bash
cd ~/Documents/infra/helmfile
[[ -f .env ]] && echo "dotenv present" || echo "no dotenv (run op_env_dotfile first)"
./scripts/sops-secret-helper.sh keys helmfiles/tatara/values/tatara-operator/default.secrets.yaml
```
Expected: lists existing keys (e.g. `operatorOidcClientSecret`, `anthropicOauthToken`, ...). `keys` shows names only, never values; the agent may run it.

- [ ] **Step 2: [USER-GATED] Set the bot FG-PAT as `scmToken`.** The value is read interactively from `/dev/tty`; `set` fails without a tty and the agent must NOT run it. Tell the user to run, pasting the GitHub FG-PAT (or GitLab `api` token) from Task 1 when prompted:
```bash
cd ~/Documents/infra/helmfile
./scripts/sops-secret-helper.sh set helmfiles/tatara/values/tatara-operator/default.secrets.yaml scmToken
```

- [ ] **Step 3: [USER-GATED] Set the webhook HMAC secret as `scmWebhookSecret`.** Same tty-only `set`; agent must NOT run it. Generate a value first (the user runs both lines; the secret is piped into the prompt, never echoed into git):
```bash
cd ~/Documents/infra/helmfile
openssl rand -hex 24    # copy this output; paste it at the prompt below AND keep it for Task 6 webhook registration
./scripts/sops-secret-helper.sh set helmfiles/tatara/values/tatara-operator/default.secrets.yaml scmWebhookSecret
```
Note for the user: the same `scmWebhookSecret` value must be supplied as `config[secret]` to every webhook in Task 6, so the operator's HMAC verification (`internal/webhook/server.go`) matches. Keep it in your clipboard / a tmp file you delete after Task 6.

- [ ] **Step 4: Verify the keys landed (agent-runnable).** After the user confirms Steps 2-3:
```bash
cd ~/Documents/infra/helmfile
./scripts/sops-secret-helper.sh keys helmfiles/tatara/values/tatara-operator/default.secrets.yaml | grep -E 'scmToken|scmWebhookSecret'
```
Expected: both `scmToken` and `scmWebhookSecret` listed.

- [ ] **Step 5: Confirm `scmSecretName` is wired (agent-runnable, read-only).**
```bash
grep -n 'scmSecretName' ~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/default.yaml
```
Expected: `scmSecretName: "tatara-scm"` present (matches `Project.spec.scmSecretRef: tatara-scm`). If absent, add it; it is non-secret and lives in plaintext values.

---

## Task 3: Board + approval label + `Proposed` column

**Files:** ephemeral `/tmp/tatara-board-ids.env` (captured ids; never committed). The captured `GITHUB_PROJECT_NUMBER` becomes `spec.scm.board.githubProjectNumber` in Task 4; `GITLAB_BOARD_ID` becomes `spec.scm.board.gitlabBoardId`.

- [ ] **Step 1: [USER-GATED] Create the GitHub Projects v2 board named `tatara`.** Outward-facing prod create against the org. Run as a human (or the agent with explicit go-ahead, since it mutates the org). Get the org node id, create the project, capture its number + node id:
```bash
OWNER_ID=$(gh api graphql -f query='query($login:String!){organization(login:$login){id}}' -F login=szymonrychu --jq '.data.organization.id')
PROJ=$(gh api graphql -f query='
mutation($ownerId:ID!,$title:String!){createProjectV2(input:{ownerId:$ownerId,title:$title}){projectV2{id number}}}' \
  -F ownerId="$OWNER_ID" -F title='tatara')
echo "$PROJ"
echo "GITHUB_PROJECT_NUMBER=$(echo "$PROJ" | jq -r '.data.createProjectV2.projectV2.number')" | tee -a /tmp/tatara-board-ids.env
echo "GITHUB_PROJECT_NODE_ID=$(echo "$PROJ" | jq -r '.data.createProjectV2.projectV2.id')"  | tee -a /tmp/tatara-board-ids.env
```
Expected: a project number (small int) and a node id (`PVT_...`). **Record `GITHUB_PROJECT_NUMBER`** - it is required in Task 4 and in `MEMORY.md`.

- [ ] **Step 2: [USER-GATED] Ensure a `Status` single-select field with a `Proposed` option.** The operator's `SetBoardColumn` writes the `Status` single-select (contract lock section 1, `BoardRef.StatusField` default `"Status"`). New Projects v2 boards include a `Status` field with `Todo/In Progress/Done`; add the `Proposed` option used by the proposal flow:
```bash
PNODE=$(grep GITHUB_PROJECT_NODE_ID /tmp/tatara-board-ids.env | cut -d= -f2)
FIELD=$(gh api graphql -f query='
query($p:ID!){node(id:$p){... on ProjectV2{field(name:"Status"){... on ProjectV2SingleSelectField{id options{id name}}}}}}' -F p="$PNODE")
echo "$FIELD"   # confirm a Status field exists; note its id
# If "Proposed" is not among options, add it (preserve existing options - GraphQL replaces the full set):
# gh api graphql -f query='mutation($f:ID!,$opts:[ProjectV2SingleSelectFieldOptionInput!]!){updateProjectV2Field(input:{fieldId:$f,singleSelectOptions:$opts}){projectV2Field{... on ProjectV2SingleSelectField{options{name}}}}}' \
#   -F f=<FIELD_ID> -F 'opts[][name]=Proposed' -F 'opts[][color]=GRAY' -F 'opts[][description]=tatara-proposed, awaiting approval' (repeat for Todo/In Progress/Done so they are not dropped)
```
Expected: a `Status` field id and an options list including `Proposed` (after the optional update). If your tatara workflow uses board columns beyond `Proposed` (e.g. `In Progress`, `Done`), ensure they exist here too; the operator only writes the option name it is told to set.

- [ ] **Step 3: [USER-GATED] Create the approval label `tatara/awaiting-approval` on every enrolled repo.** Labels are per-repo on GitHub. This is the `spec.scm.approvalLabel` default and the approval state-machine source of truth (spec section D). Outward-facing prod write; run as human or with explicit go-ahead:
```bash
for r in tatara-memory tatara-cli tatara-operator tatara-chat tatara-memory-repo-ingester tatara-claude-code-wrapper tatara-argo-workflows; do
  gh api -X POST "repos/szymonrychu/$r/labels" \
    -f name='tatara/awaiting-approval' -f color='fbca04' \
    -f description='tatara-proposed work item; remove to approve' \
    && echo "label: $r" || echo "label exists or failed: $r"
done
```
Expected: a label per repo (or "already exists"). Also ensure the existing trigger label `tatara` exists on each repo (enrollment runbook already created it; re-run this loop with `-f name='tatara'` if any are missing).

- [ ] **Step 4: [USER-GATED] (GitLab only) Create the issue board + list labels + approval label.** Skip if no GitLab Project is enrolled. The board is label-driven (contract lock section 1: `SetBoardColumn` swaps a `board::<col>` scoped label). Create the scoped list labels and the approval label, then the board, and capture its id:
```bash
GL_PROJECT="<group/project>"   # the enrolled GitLab project path
for lbl in 'board::Proposed' 'board::In Progress' 'board::Done' 'tatara/awaiting-approval'; do
  glab api -X POST "projects/$(printf '%s' "$GL_PROJECT" | jq -sRr @uri)/labels" \
    -f "name=$lbl" -f color='#fbca04' && echo "gl label: $lbl"
done
BOARD=$(glab api -X POST "projects/$(printf '%s' "$GL_PROJECT" | jq -sRr @uri)/boards" -f name='tatara')
echo "GITLAB_BOARD_ID=$(echo "$BOARD" | jq -r '.id')" | tee -a /tmp/tatara-board-ids.env
```
Expected: the four labels created and a board id. **Record `GITLAB_BOARD_ID`** for Task 4.

---

## Task 4: Patch each Project CR with the `scm` block (declarative re-apply, NOT kubectl patch)

**Files:**
- Modify: `~/Documents/tatara/tatara-operator/deploy-samples/tatara-project.yaml`

The `Project` CR is NOT templated by the operator chart (the chart's `templates/` has no `project.yaml`); `deploy-samples/tatara-project.yaml` is its single declarative source. CLAUDE.md "never `kubectl patch` helm-managed spec fields" applies to declaratively-managed specs generally: edit the manifest (the source of truth) and `kubectl apply -f` it, so the live spec never drifts from git. Do NOT `kubectl patch project ...`. The `scm` field shapes are byte-for-byte from contract lock section 2 (camelCase json tags: `provider`, `owner`, `botLogin`, `board.{githubProjectNumber,gitlabBoardId,statusField}`, `mergePolicy`, `prReactionScope`, `approvalLabel`).

- [ ] **Step 1: Confirm the operator CRD already serves `spec.scm` (agent-runnable, read-only).** The CRD must carry the `scm` schema (shipped by the operator build bumped in Task 5). If the operator/CRD bump in Task 5 has not been applied yet, do Task 5 first - applying a Project with an unknown `spec.scm` field is rejected/pruned.
```bash
kubectl get crd projects.tatara.dev -o jsonpath='{.spec.versions[0].schema.openAPIV3Schema.properties.spec.properties.scm.properties}{"\n"}' | tr ',' '\n' | grep -E 'provider|owner|botLogin|board|mergePolicy|prReactionScope|approvalLabel' && echo "scm schema present"
```
Expected: the `scm` sub-properties listed and `scm schema present`. If empty, run Task 5 (chart/CRD bump) before continuing.

- [ ] **Step 2: Edit the Project manifest to add `spec.scm`.** Open `~/Documents/tatara/tatara-operator/deploy-samples/tatara-project.yaml` and add the `scm:` block under the `tatara` Project's `spec:`, filling `board.githubProjectNumber` from `GITHUB_PROJECT_NUMBER` captured in Task 3 (and `gitlabBoardId` only for a GitLab Project). For the GitHub dogfood Project the spec block is exactly:
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
```
For a GitLab Project, the block is instead `provider: gitlab`, `owner: <group>`, `botLogin: tatara-bot`, `board: {gitlabBoardId: <GITLAB_BOARD_ID>}`, same `mergePolicy`/`prReactionScope`/`approvalLabel`. Field names and enum values (`github|gitlab`, `afterApproval|autoMergeOnGreenCI`, `labeledOrMentioned|all`) must match contract lock section 2 byte-for-byte. `statusField` defaults to `Status` server-side (kubebuilder default) but is set explicitly here for clarity. Repeat the whole block per Project if more than one Project is enrolled.

- [ ] **Step 3: [USER-GATED] Apply the updated Project manifest (prod CR mutation).** Declarative apply of the source-of-truth manifest - never `kubectl patch`. Prod-mutating; run as human or with explicit go-ahead:
```bash
kubectl apply -f ~/Documents/tatara/tatara-operator/deploy-samples/tatara-project.yaml
```
Expected: `project.tatara.dev/tatara configured`.

- [ ] **Step 4: Verify the live spec carries the `scm` block (agent-runnable, read-only).**
```bash
kubectl -n tatara get project tatara -o jsonpath='{.spec.scm}{"\n"}'
```
Expected: the JSON object with `provider`, `owner`, `botLogin`, `board.githubProjectNumber`, `mergePolicy:afterApproval`, `prReactionScope:labeledOrMentioned`, `approvalLabel:tatara/awaiting-approval`. No field pruned (confirms the CRD schema served it).

- [ ] **Step 5: Commit the manifest change** (no secrets; agent-runnable).
```bash
cd ~/Documents/tatara/tatara-operator
git add deploy-samples/tatara-project.yaml
git commit -m "feat: tatara Project carries scm block (provider/owner/botLogin/board/mergePolicy)"
```

---

## Task 5: Bump operator/cli/wrapper image+chart pins; helmfile diff -> apply from main; rollout-restart operator

**Files:**
- Modify: `~/Documents/infra/helmfile/helmfiles/tatara/helmfile.yaml.gotmpl` (operator chart `version:` pin)
- Modify: `~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/common.yaml` (operator `image.tag` pin)
- Modify: `~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/default.yaml` (wrapper image is consumed via the Project CR `spec.agent.image`, bumped in Task 4's manifest; bump `ingesterImage` here only if that image was rebuilt - the scm feature does not rebuild it)

The operator reads image-pin envs ONLY at startup (`internal/config/config.go` `os.Getenv("INGESTER_IMAGE")`, `MEMORY_IMAGE`, etc., surfaced via the chart ConfigMap). A `helm apply` that changes ConfigMap values does not by itself restart the manager Pod, so a rollout restart is required after apply. Build from `main` only (rule 10): the operator/cli/wrapper images and the operator chart must already be published to Harbor from their respective `main` branches before this task (image/chart publishing is out of this runbook's scope; this task pins + applies the published artifacts).

- [ ] **Step 1: Confirm the published artifact versions (agent-runnable, read-only).** Determine the new operator chart `version`/`appVersion`, the operator `image.tag`, the wrapper image tag, and the cli image tag that carry the scm-projects feature (from each repo's `Chart.yaml`/release tag on `main`). Record them as `OP_VER`, `WRAP_TAG`, `CLI_TAG` for the edits below. (The cli image is bundled into the wrapper image at wrapper build time; the cluster pins only the wrapper image via the Project CR `spec.agent.image`, so `CLI_TAG` is informational here.)
```bash
grep -nE '^version:|^appVersion:' ~/Documents/tatara/tatara-operator/charts/tatara-operator/Chart.yaml
```
Expected: the operator chart version/appVersion that include the scm CRD + controller flow.

- [ ] **Step 2: Bump the operator chart `version:` pin in the helmfile.**
```bash
cd ~/Documents/infra/helmfile
# replace the current operator release version pin (e.g. 0.2.14) with $OP_VER:
sed -i '' 's/^\(  version: \)0\.2\.14$/\1<OP_VER>/' helmfiles/tatara/helmfile.yaml.gotmpl
grep -n 'version:' helmfiles/tatara/helmfile.yaml.gotmpl
```
Expected: the `tatara-operator` release `version:` now reads `<OP_VER>`. (Edit by hand if multiple releases share that version literal so only the operator release changes.)

- [ ] **Step 3: Bump the operator `image.tag` pin.**
```bash
cd ~/Documents/infra/helmfile
sed -i '' 's/^\(  tag: "\)0\.2\.14"$/\1<OP_VER>"/' helmfiles/tatara/values/tatara-operator/common.yaml
grep -n 'tag:' helmfiles/tatara/values/tatara-operator/common.yaml
```
Expected: `tag: "<OP_VER>"`. (`image.tag` defaults to `appVersion` when empty, but the bucket pins it explicitly; keep them in lockstep.)

- [ ] **Step 4: Bump the wrapper image pin.** The wrapper image is consumed by the operator-spawned agent Pod via the Project CR `spec.agent.image` (NOT an operator value). If the wrapper image tag changed, update it in `deploy-samples/tatara-project.yaml` and re-apply per Task 4 Step 3. Confirm the intended tag:
```bash
grep -n 'tatara-claude-code-wrapper' ~/Documents/tatara/tatara-operator/deploy-samples/tatara-project.yaml
```
Expected: `agent.image: harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper:<WRAP_TAG>`. If stale, edit it and fold it into Task 4's apply (one prod apply, not two).

- [ ] **Step 5: helmfile diff (agent-runnable, read-only).**
```bash
cd ~/Documents/infra/helmfile
[[ -f .env ]] && echo "dotenv present" || echo "no dotenv (run op_env_dotfile first)"
mise exec -- helmfile -e default -l application=tatara-operator diff
```
Expected: the diff shows the operator Deployment image bumped to `<OP_VER>`, the chart version change, the `tatara-scm` Secret rendered from the Task 2 sops values (`token`/`webhookSecret`), and no spurious churn in unrelated objects.

- [ ] **Step 6: [USER-GATED] helmfile apply from main (prod deploy).** Prod-mutating; run as human or with explicit go-ahead. Must be from `main` of the infra repo (rule 10):
```bash
cd ~/Documents/infra/helmfile
git rev-parse --abbrev-ref HEAD    # confirm: main
mise exec -- helmfile -e default -l application=tatara-operator apply
```
Expected: the release upgrades cleanly; `tatara-scm` Secret present with keys `token` + `webhookSecret`.

- [ ] **Step 7: [USER-GATED] Rollout-restart the operator (it reads image-pin envs at startup).** Even though the apply changed the Deployment image (triggering a rollout), force a restart to guarantee the manager re-reads every image-pin env from the bumped ConfigMap:
```bash
kubectl -n tatara rollout restart deploy/tatara-operator
kubectl -n tatara rollout status deploy/tatara-operator --timeout=300s
```
Expected: `deployment "tatara-operator" successfully rolled out`.

- [ ] **Step 8: Verify the operator is healthy and serving the scm CRD + image-pin envs (agent-runnable, read-only).**
```bash
kubectl -n tatara get pods -l app.kubernetes.io/name=tatara-operator
kubectl -n tatara get cm tatara-operator -o jsonpath='{.data.INGESTER_IMAGE}{"\n"}'
kubectl get crd projects.tatara.dev -o jsonpath='{.spec.versions[0].schema.openAPIV3Schema.properties.spec.properties.scm}{"\n"}' | head -c 200; echo
```
Expected: operator pod `Running`/ready; the ConfigMap image-pin env reads the expected tag; the CRD serves `spec.scm`. (This is the precondition for Task 4 if Task 4 was deferred until after this task.)

- [ ] **Step 9: Commit the infra pin bumps** (no secrets; agent-runnable).
```bash
cd ~/Documents/infra/helmfile
git add helmfiles/tatara/helmfile.yaml.gotmpl helmfiles/tatara/values/tatara-operator/common.yaml helmfiles/tatara/values/tatara-operator/default.yaml
git commit -m "chore(tatara): bump operator chart+image to <OP_VER> (scm-projects feature)"
```

---

## Task 6: Register the expanded webhook event set per enrolled repo

**Files:** none. Uses the bot FG-PAT (which carries Webhooks=Read and write from Task 1 Step 3) and the `scmWebhookSecret` value from Task 2 Step 3. A fine-grained PAT has NO org-webhook automation, so every repo is registered individually.

The operator's expanded dispatch (spec section C) handles `issues`, `issue_comment`, `pull_request`, `pull_request_review`, plus the existing `push`. GitLab equivalents: Issues, Note (comments), Merge request, and pipeline events. All POST to `https://tatara.szymonrichert.pl/operator/webhooks/tatara` with HMAC = `scmWebhookSecret`.

- [ ] **Step 1: Stage the webhook secret for the registration loop (USER provides it).** The agent never holds the secret. The human exports it for this shell (it was generated in Task 2 Step 3):
```bash
# [USER-GATED] human runs: export WHS='<the scmWebhookSecret value from Task 2 Step 3>'
[ -n "$WHS" ] && echo "WHS set" || echo "WHS missing - human must export it"
URL="https://tatara.szymonrichert.pl/operator/webhooks/tatara"
```
Expected: `WHS set`.

- [ ] **Step 2: [USER-GATED] Register/patch the GitHub webhook on every enrolled repo with the new event set.** Outward-facing prod write using the bot PAT (`gh` authenticated as `tatara-bot`, or `GH_TOKEN=<bot FG-PAT>`). Idempotent: create, and if a hook to that URL exists, patch its events instead:
```bash
EVENTS='-f events[]=push -f events[]=issues -f events[]=issue_comment -f events[]=pull_request -f events[]=pull_request_review'
for r in tatara-memory tatara-cli tatara-operator tatara-chat tatara-memory-repo-ingester tatara-claude-code-wrapper tatara-argo-workflows; do
  HID=$(gh api "repos/szymonrychu/$r/hooks" --jq ".[] | select(.config.url==\"$URL\") | .id" 2>/dev/null | head -1)
  if [ -n "$HID" ]; then
    gh api -X PATCH "repos/szymonrychu/$r/hooks/$HID" -F active=true $EVENTS \
      -f config[url]="$URL" -f config[content_type]=json -f config[secret]="$WHS" -f config[insecure_ssl]=0 \
      && echo "patched: $r" || echo "patch FAILED: $r"
  else
    gh api -X POST "repos/szymonrychu/$r/hooks" -f name=web -F active=true $EVENTS \
      -f config[url]="$URL" -f config[content_type]=json -f config[secret]="$WHS" -f config[insecure_ssl]=0 \
      && echo "created: $r" || echo "create FAILED: $r"
  fi
done
```
Expected: each repo reports `created` or `patched`. The hook now delivers `push,issues,issue_comment,pull_request,pull_request_review`.

- [ ] **Step 3: [USER-GATED] (GitLab only) Register the project webhook with the equivalent events.** Skip if no GitLab Project enrolled. Uses the bot `api` token (`glab` authenticated as `tatara-bot`):
```bash
GL_PROJECT="<group/project>"
PID=$(printf '%s' "$GL_PROJECT" | jq -sRr @uri)
glab api -X POST "projects/$PID/hooks" \
  -f url="$URL" -f token="$WHS" \
  -F push_events=true -F issues_events=true -F note_events=true \
  -F merge_requests_events=true -F pipeline_events=true -F enable_ssl_verification=true \
  && echo "gl hook created" || echo "gl hook FAILED"
```
Expected: `gl hook created`. (`note_events` covers MR/issue comments; `merge_requests_events` + `pipeline_events` cover the PR/MR-review + CI signals.)

- [ ] **Step 4: Verify deliveries land (agent-runnable, read-only once a test event fires).** Confirm the operator receives and HMAC-verifies the new events. Either ping a hook or watch the metric:
```bash
# GitHub: ping one hook and check last delivery
HID=$(gh api repos/szymonrychu/tatara-operator/hooks --jq ".[] | select(.config.url==\"$URL\") | .id" | head -1)
gh api -X POST "repos/szymonrychu/tatara-operator/hooks/$HID/pings" && echo "pinged"
gh api "repos/szymonrychu/tatara-operator/hooks/$HID/deliveries" --jq '.[0] | {event:.event, status:.status_code}'
# Operator side: the action-labelled webhook counter should increment
kubectl -n tatara exec deploy/tatara-operator -- wget -qO- localhost:9090/metrics 2>/dev/null | grep operator_webhook_events_total | head
```
Expected: a delivery with `status_code: 200` (or the operator's accepted code); `operator_webhook_events_total{...,action="..."}` present (the `action` label was added per spec section H / contract lock section 6).

- [ ] **Step 5: [USER-GATED] Clear the staged webhook secret from the shell.** Human runs:
```bash
unset WHS
rm -f /tmp/tatara-board-ids.env
```

---

## Task 7: Record board identifiers + close out

**Files:**
- Modify: `~/Documents/tatara/tatara-operator/MEMORY.md` (record the board number/id, bot login, and that the Project carries `spec.scm`)

- [ ] **Step 1: Record the identifiers in MEMORY.md (agent-runnable).** One dated line each: the GitHub Projects v2 board number (`GITHUB_PROJECT_NUMBER`), the GitLab board id if any (`GITLAB_BOARD_ID`), the bot login (`tatara-bot`), and that `spec.scm` now lives in `deploy-samples/tatara-project.yaml` (declarative, not chart-templated). Then commit:
```bash
cd ~/Documents/tatara/tatara-operator
git add MEMORY.md && git commit -m "docs: record scm-projects board ids + tatara-bot identity (dogfood enrollment)"
```

- [ ] **Step 2: End-to-end smoke (agent-runnable, read-only verification per superpowers:verification-before-completion).** Open a `tatara`-labelled issue on one repo and confirm a Task is created with the new fields; have the agent `propose_issue` and confirm the mirror Task holds `Phase=AwaitingApproval` until the `tatara/awaiting-approval` label is removed (the approval gate, contract lock section 5):
```bash
kubectl -n tatara get tasks -o custom-columns=NAME:.metadata.name,KIND:.spec.kind,PHASE:.status.phase,AUTHOR:.spec.source.authorLogin
```
Expected: a Task with `KIND` populated (`implement|review|selfImprove`) and, for a proposal, `PHASE=AwaitingApproval`. Removing the approval label flips `ApprovalApproved=True` and releases the gate.

---

## Notes

- **User-gated steps** (prod mutation / outward-facing SCM writes / tty secret-setting; agent must NOT run): Task 1 Steps 1-4 (account + PAT creation); Task 2 Steps 2-3 (`sops ... set`); Task 3 Steps 1-4 (board/label/column creation against prod org); Task 4 Step 3 (`kubectl apply` of the Project CR); Task 5 Steps 6-7 (`helmfile apply` + rollout restart); Task 6 Steps 1-3 and 5 (webhook registration + secret handling). Everything else (reads, `keys`, `remove`, `diff`, edits to git-tracked manifests, commits) is agent-runnable.
- **Never `kubectl patch` the Project spec** (CLAUDE.md): the Project is declaratively managed via `deploy-samples/tatara-project.yaml`; the `scm` block is edited there and `kubectl apply -f`-ed, so live state never drifts from git.
- **Order dependency:** Task 5 (operator/CRD bump applied) must precede Task 4's apply, because applying a Project with an unknown `spec.scm` field is pruned/rejected. If executing strictly top-to-bottom, do Task 5 before Task 4 Step 3 (Task 4 Step 1 guards this).
- **One prod apply for the Project CR:** if the wrapper image tag also changed (Task 5 Step 4), fold that edit into Task 4 Step 2 and apply once.
- Board ids are captured to `/tmp/tatara-board-ids.env` during Task 3 and recorded in MEMORY.md in Task 7; the tmp file is deleted in Task 6 Step 5.
