# tatara-helmfile extraction + autonomous deploy harness - design

Date: 2026-06-13
Status: approved (user pinned all decisions upfront, authorized run-to-completion)

## Goal

Stand up a dedicated private `tatara-helmfile` GitHub repo that owns the
tatara platform's helm releases, deploys them to the cluster via GitHub
Actions on self-hosted in-cluster runners, is enrolled as a `Repository`
in the existing `tatara` Project, and is the target of a new rigid
in-session deploy harness skill that lets tatara agents implement an
issue end-to-end and ship the resulting deploy through the pipeline.

End state: a tatara agent picks up an issue, implements + merges code in a
component repo, then opens/merges an MR in `tatara-helmfile` that bumps the
release, watches the apply pipeline, rolls back on failure, and closes the
issue as delivered - all autonomously, gated only by green pipelines and
the diff.

## Decisions (pinned)

1. **Deploy auth: in-cluster runner + dedicated SA.** A dedicated ARC runner
   scale set `arc-runner-tatara-helmfile` (arc-runners ns) runs as a dedicated
   ServiceAccount `tatara-helmfile-deployer` bound (ClusterRoleBinding) to a
   deploy ClusterRole. `helmfile apply` uses the pod's in-cluster SA token
   (no KUBECONFIG, no kubeconfig secret leaves the cluster). The operator
   chart installs CRDs + ClusterRoles + webhooks, so the ClusterRole is broad
   (effectively cluster-admin scope; we reuse the existing cluster-admin bind
   precedent rather than hand-curate verbs - see Security).
2. **Apply gate: auto-apply on merge.** PR shows the `helmfile diff` as a
   sticky comment; merge to `main` auto-applies. No human approval gate -
   the agent self-merges and deploys end-to-end. The gate is the diff +
   green component pipeline + green apply pipeline.
3. **Infra split: full extract, tatara-helmfile sole owner.** The
   `helmfiles/tatara/` bucket and its CI generation are removed from
   `~/Documents/infra/helmfile`. `tatara-helmfile` owns both releases AND the
   enrollment CRs (the `tatara.dev` Project + a `Repository` CR that
   self-enrolls `tatara-helmfile`). infra no longer references tatara. This
   intentionally reverses the 2026-06-05 consolidation
   ([[tatara-helmfile-into-infra]]); rationale: scoping bot deploy access to a
   single dedicated repo instead of the whole 60+ release homelab infra repo.
4. **Harness: in-session subagent-driven skill, full loop.** A skill baked
   into the tatara-claude-code-wrapper image. The agent runs all 9 steps in
   its long-lived PTY session; the implement sub-loop uses
   `superpowers:subagent-driven-development` in git worktrees. Pipeline
   watching is `gh` CLI. Issue research is tatara-mcp + web.

## On-disk + repo conventions

- New repo: `github.com/szymonrychu/tatara-helmfile` (private). Local clone:
  `~/Documents/tatara-helmfile` (sibling of `~/Documents/infra`, mirrors the
  infra-helmfile layout). Default choice; not a gitignored child of the
  tatara docs repo because it is platform infra, not a component.
- Bot identity `szymonrychu-bot` ([[tatara-bot-identity-szymonrychu-bot]])
  has write access; its PAT is the `tatara-scm` token already in use.

## Sub-system A: tatara-helmfile repo structure

Ported from the infra `helmfiles/tatara/` bucket, flattened to a standalone
single-bucket repo (no umbrella, only tatara releases). KISS: with two
releases we do NOT port the 521-line `.parse.py` dynamic-pipeline generator;
workflows are static YAML.

```
tatara-helmfile/
├── helmfile.yaml.gotmpl          # root: single 'default' env, helmDefaults
│                                 #   (wait, timeout 900, force, --rollback-on-failure),
│                                 #   templates block (values/secrets layering), releases
├── .hook.sh                      # ported subset: applies raw/*.pre.yaml +
│                                 #   sops-decrypts *.pre.secrets.yaml on presync;
│                                 #   path references fixed for flat layout
├── .sops.yaml                    # same PGP key D39E...CED8 as infra (secrets copy verbatim)
├── .mise.toml                    # helm 4.2.0, helmfile 1.5.3, kubectl 1.36.1, sops 3.13.1,
│                                 #   helm-secrets 4.7.4, helm-diff (pinned, mirror infra)
├── .gitignore, .pre-commit-config.yaml, .gitleaks.toml
├── values/
│   ├── common.yaml               # imagePullSecrets: regcred
│   ├── default.yaml              # (env-level, currently empty/minimal)
│   ├── tatara-operator/
│   │   ├── common.yaml           # image.tag pin (chart 0.0.0-<sha>)
│   │   ├── default.yaml          # ingress host, webhook URLs, OIDC issuer, memory images
│   │   ├── default.secrets.yaml  # sops: 7 fields (copied verbatim, same key)
│   │   └── raw/
│   │       ├── project-tatara....pre.yaml          # tatara.dev Project CR
│   │       └── repositories-tatara.....pre.yaml    # Repository CRs incl. self-enroll (new)
│   └── tatara-chat/
│       └── default.yaml          # ingress host, path
├── README.md, MEMORY.md, ROADMAP.md, CLAUDE.md   # repo-local; CLAUDE.md per tatara contract
└── .github/workflows/
    ├── diff.yaml                 # PR -> helmfile diff -> sticky comment
    └── apply.yaml                # push main -> helmfile apply (auto), concurrency-guarded
```

Releases (verbatim from current bucket):
- `tatara-chat` oci://harbor.szymonrichert.pl/charts/tatara-chat v0.1.0, ns tatara
- `tatara-operator` oci://harbor.szymonrichert.pl/charts/tatara-operator v0.0.0-7d45bd9, ns tatara

Values layering preserved: `values/common.yaml` -> `values/{env}.yaml` ->
`values/{release}/common.yaml` -> `values/{release}/{env}.yaml` -> secrets
`values/{release}/{env}.secrets.yaml`. Single `default` environment.

## Sub-system B: GitHub Actions workflows + in-cluster deploy runner

Adapted from the legacy `backup_devopsbay/sunmetric/k8s-apps/.github/workflows`
pattern (diffs.yaml / development.yaml) but with: self-hosted ARC runner
(`runs-on: arc-runner-tatara-helmfile`), in-cluster SA auth (no KUBE_CONFIG),
sops/GPG decryption, Harbor OCI login, mise-pinned tools.

**diff.yaml** (trigger: `pull_request` to main):
1. checkout
2. mise install (helm/helmfile/kubectl/sops + helm-secrets + helm-diff)
3. import GPG key from `secrets.GPG_PRIVATE_RSA_B64` (sops decrypt)
4. `helm registry login harbor.szymonrichert.pl` with `secrets.HARBOR_USERNAME/PASSWORD`
5. `helmfile -e default diff` (in-cluster SA token via mounted SA)
6. strip ANSI, post as sticky PR comment (marocchino/sticky-pull-request-comment)
7. non-blocking: diff exit code does not fail the PR (diff is informational)

**apply.yaml** (trigger: `push` to main):
1-4. same setup as diff
5. `helmfile -e default apply` (in-cluster SA, --rollback-on-failure via helmDefaults)
6. concurrency control: `concurrency: { group: tatara-helmfile-apply, cancel-in-progress: false }`
   (GH-native replacement for the legacy Turnstyle action; serializes applies)
7. on failure the job fails (red), the harness reacts (rollback)

GH Actions secrets on the repo: `GPG_PRIVATE_RSA_B64`, `HARBOR_USERNAME`,
`HARBOR_PASSWORD`. No `KUBE_CONFIG` (in-cluster SA). `GITHUB_TOKEN` (default)
suffices for the sticky comment.

**Deploy runner (infra-helmfile addition, NOT in tatara-helmfile):** the
runner scale set + SA + RBAC are cluster bootstrap and must live with the
other ARC runners in `infra/helmfile/helmfiles/coding`. New:
- `arc-runner-tatara-helmfile` RunnerScaleSet (mirror existing per-repo sets:
  control-plane nodeSelector, fsGroup 1001 [[arc-runners-die-on-job-pickup-2026-06-13]],
  regcred, maxRunners cap).
- ServiceAccount `tatara-helmfile-deployer` (arc-runners ns) as the runner pod SA.
- ClusterRoleBinding binding it to `cluster-admin` (raw pre-manifest, mirrors
  the cluster_cleanup + argo-workflow precedent). Tools in the runner image:
  the runner needs helm/helmfile/kubectl/sops/mise; use the same runner image
  as other tatara runners + mise bootstrap, or a dedicated image. Decide in plan.

## Sub-system C: enrollment in the tatara Project

Enrollment is two raw manifests applied by `.hook.sh` presync of the
`tatara-operator` release (they move with the bucket):
- the existing `tatara.dev` **Project** `tatara` CR (already in the bucket).
- a new **Repository** CR per enrolled repo. tatara-helmfile self-enrolls:
  `spec.projectRef: tatara`, `spec.url: https://github.com/szymonrychu/tatara-helmfile`,
  `spec.defaultBranch: main`, `spec.reingestSchedule: "0 6 * * *"`,
  `spec.ingestEnabled: true`. The operator's `projectReposForScan` discovers it
  on the next cron and begins issue/MR scans.

No GitHub App or per-repo webhook setup needed - the Project's single
`scmSecretRef` (token + webhookSecret keys, the bot PAT) covers it. The bot
(szymonrychu-bot) must have write/admin on the new repo.

Note: existing enrolled component repos currently have their `Repository` CRs
managed where? If they live only in the infra bucket today, extracting the
bucket moves ALL tatara `Repository` CRs into tatara-helmfile. Confirm during
implementation that no component `Repository` CR is orphaned by the infra
removal (audit `values/tatara-operator/raw/`).

## Sub-system D: static deployment harness skill (the 9-step loop)

A Claude Code skill `tatara-deploy-harness` shipped in the wrapper image at
`templates/skills/tatara-deploy-harness/SKILL.md` (baked, auto-installed by
`bootstrap.installSkills`). Procedural, rigid state machine. The agent invokes
it for the kickoff turn ("deliver issue #N in <repo>"). Runtime inputs:
issue number + component repo (from the kickoff prompt / env), `tatara-helmfile`
present as a second `RepoSpec` in `TATARA_REPOS` so it is cloned into the
workspace and editable.

State machine (labels in skill as S1..S9):

- **S1 Research.** `gh issue view`; tatara-mcp memory tools (`query`,
  `code_search`, `code_entity`, `code_neighbors`, `code_explain`) for codebase
  context; WebSearch/WebFetch for external research.
- **S2 Comment research.** `gh issue comment` with the research summary +
  proposed approach.
- **S3 Implement (subagent-driven).** `superpowers:brainstorming` (if design
  needed) -> `writing-plans` -> `using-git-worktrees` -> `subagent-driven-development`
  + `test-driven-development` -> `requesting-code-review` (fix critical/high) ->
  `pre-commit run --all-files`. Post progress comments under the issue. **This
  is the loop target: any later failure jumps back here.**
- **S4 Component MR + pipeline.** `gh pr create`; watch with `gh pr checks --watch`
  / `gh run watch`. Pipeline fail OR unmergeable -> S3.
- **S5 Self-merge.** `gh pr merge --merge --delete-branch`.
- **S6 Watch main pipeline.** `gh run watch` on the post-merge main run (this
  is where component images/charts build+push to harbor -
  [[tatara-images-via-ci-not-local-buildx]]). Fail -> S3.
- **S7 Helmfile MR.** In the `tatara-helmfile` clone, bump the release: image
  tag pin (`values/tatara-operator/common.yaml`) and/or chart version. Reuse
  existing skills `bump-container-usage` / `bump-chart-usage` where applicable.
  `gh pr create`; the diff workflow posts the sticky `helmfile diff`. Review
  the diff; pipeline fail OR diff wrong/unmergeable -> S3.
- **S8 Merge helmfile MR + watch apply.** `gh pr merge`; `gh run watch` on the
  apply.yaml run. Success -> S9. Failure -> **rollback**: `git revert` the merge
  commit on `tatara-helmfile` main, open+merge the revert PR (re-applies prior
  state via the same apply pipeline), then jump to S3.
- **S9 Deliver.** `gh issue comment` (delivered summary + links to both MRs +
  deployed versions); tatara-mcp `issue_outcome` to record success; `gh issue
  close`.

Skill notes section codifies: the back-edges (S4/S6 fail -> S3, S7/S8 fail ->
rollback -> S3), idempotency (re-entrant if the session restarts mid-loop -
check current issue/PR/run state before acting), and that `gh` is authed via
`GH_TOKEN`/`GIT_TOKEN` (the bot PAT). State is checkpointed to
`/workspace/handoff.md` (the `handoff` skill) so a fresh pod can resume.

Wrapper change required: add `tatara-helmfile` to the agent's `TATARA_REPOS`
(or document that the harness clones it on demand via `gh repo clone`).
Confirm multi-repo `RepoSpec` editing + the per-turn commit/push contract
handles a second repo (the map indicates `CommitAndPushAll` exists).

## Security

- The deploy runner SA is cluster-admin scoped. This is the single highest-risk
  element: any code that runs in `arc-runner-tatara-helmfile` can do anything to
  the cluster. Mitigations: dedicated SA (not shared with the kaniko
  `tatara-ci-dispatcher`), the runner only runs `tatara-helmfile` workflows,
  control-plane pinning + maxRunners cap already constrain blast radius, and
  the repo is private with bot-only write.
- sops PGP private key delivered as a GH Actions secret (`GPG_PRIVATE_RSA_B64`).
  Same key as infra; rotation is out of scope but noted in MEMORY.
- Secrets (`default.secrets.yaml`) are copied verbatim (same key) - never
  re-encrypted or printed ([[scm-author-vs-actor-egress-gate]] reminds us redaction
  slips leak; do not echo decrypted values in CI).

## Human-gated / out-of-band steps (cannot be fully autonomous)

These are prepared (manifests/PRs authored) but require the user or a live
action outside this session:
1. Creating the private GitHub repo + setting GH Actions secrets (needs real
   key material: GPG key, Harbor creds). Prepared: a `gh repo create` + `gh
   secret set` runbook.
2. Merging the infra-helmfile MR that removes the tatara bucket and the MR that
   adds the deploy runner (GitLab repo; reviewed/merged by user).
3. First live `helmfile apply` from the new repo + the `kubectl apply` of the
   new `Repository` CR / deploy-runner RBAC. Verified before declaring done.
4. Wrapper image rebuild ships via its CI on merge ([[tatara-images-via-ci-not-local-buildx]]),
   not local buildx.

## Build order / phases

Independent workstreams (parallel worktrees, subagent-driven):
- **Phase 1 (parallel):**
  - A: author `tatara-helmfile` repo contents (port bucket, hook, sops, mise,
    README/MEMORY/ROADMAP/CLAUDE.md).
  - B: author `.github/workflows/diff.yaml` + `apply.yaml`.
  - D: author the `tatara-deploy-harness` skill in the wrapper repo (+ wrapper
    `TATARA_REPOS` wiring, helm unittest).
  - infra-prep: author the infra-helmfile deploy-runner scale set + RBAC raw
    manifests (separate worktree off infra main).
- **Phase 2 (after A):** author the new `Repository` self-enroll CR + audit
  for orphaned component `Repository` CRs; author the infra-removal diff.
- **Phase 3 (merge/integrate, opus):** assemble repo, run `helmfile -e default
  template`/lint + `helm unittest` + `pre-commit`, open PRs/MRs, write the
  runbook for the human-gated steps.
- **Phase 4 (verify):** drive pipelines, live apply, enrollment, end-to-end
  harness dry-run on a throwaway issue.

## Testing & verification

- `tatara-helmfile`: `helmfile -e default lint` and `helmfile -e default
  template` must render both releases offline (Harbor login required for OCI
  pull; use `--skip-deps` / cached charts where possible). `pre-commit
  run --all-files`. `gitleaks` clean.
- workflows: validate YAML + `act`-style dry where possible; real validation is
  the first PR (diff posts) + first merge (apply runs).
- harness skill: `superpowers:writing-skills` self-check; a dry-run on a
  trivial throwaway issue end-to-end before trusting it on real work.
- wrapper: `make test` + `make chart-test` (helm unittest) green.
- verification-before-completion before any "done" claim; evidence (rendered
  diff, green run URLs, live `kubectl get project/repository`) required.
