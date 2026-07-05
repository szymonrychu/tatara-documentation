# Semver push-CD - cutover runbook

Date: 2026-06-28
Companion to `docs/superpowers/specs/2026-06-28-semver-push-cd-design.md`.
Covers the gated, one-time, mostly out-of-repo steps the implementation
workflow could NOT perform (tag pushes, repo settings, Harbor policy, secrets,
the enable-auto-merge cutover). The code lives on `feat/semver-push-cd` in every
component repo; this is what makes it live.

Do the steps IN ORDER. Steps 1-5 are safe to do anytime (they do not change
runtime behavior). Step 6 is the irreversible cutover.

## 0. Pre-flight: review + merge the branches

`feat/semver-push-cd` exists locally in: operator, helmfile, cli, wrapper,
memory, ingester, chat, agent-skills, observability. Review each diff, then
merge to each repo `main` (normal CI builds + publishes current-style artifacts;
nothing auto-deploys yet because auto-merge/tagging is not active until step 6).
NOTE: the very first merge that LANDS `release.yml` will not itself trigger a
release run (`workflow_run`/push release jobs only fire once the workflow is
already on `main`) - expected GitHub behavior.

## 1. Seed semver tags (before any release run)

`cd-release` mode=tag computes the next version from the latest tag matching
`^v?[0-9]+\.[0-9]+\.[0-9]+$` (stray non-semver tags such as memory's
`wave-N-merged` are ignored by design). Repos lacking a clean `vX.Y.Z` tag must
be seeded once, or the first release cuts from `v0.0.0 -> v0.0.1` and goes
backwards from the static chart/plugin version.

Recommended seeds (use `max(existing clean semver tag, static version)`):

| repo | seed | source of truth |
|------|------|-----------------|
| tatara-operator | `v0.4.10` | Chart.yaml |
| tatara-claude-code-wrapper | `v0.1.0` | (no version today) |
| tatara-memory-repo-ingester | `v0.2.9` | Chart.yaml |
| tatara-chat | `v0.1.0` | static Chart.yaml 0.1.0 |
| tatara-agent-skills | `v0.1.0` | plugin.json 0.1.0 |
| tatara-memory | `v0.4.0` | Chart.yaml (only `wave-N-merged` tags exist) |
| tatara-cli | already `v0.4.0` | leave as-is |

```
git -C <repo> tag vX.Y.Z <main-sha> && git -C <repo> push origin vX.Y.Z
```

## 2. TATARA_CD_TOKEN (bot PAT) as an Actions secret

Add `TATARA_CD_TOKEN` (the `szymonrychu-bot` PAT) as a repo (or org) Actions
secret on all 9 repos. Scope: push tags on the repo itself + clone / open PR /
enable-auto-merge on its PARENT repo (cli->wrapper, skills->wrapper,
wrapper->helmfile, operator/memory/ingester/chat->helmfile). The terminal
helmfile needs it only to be a bump target (no parent).

## 3. Branch protection + Allow auto-merge (all 7 deploy-flow repos)

For operator, cli, wrapper, memory, ingester, chat, helmfile:
- Enable "Allow auto-merge" in repo settings (the action calls
  `gh pr merge --auto`; it errors if this is off).
- Protect `main` with the repo's CI as a REQUIRED status check
  (`helmfile diff` is the required check on tatara-helmfile). Auto-merge only
  fires once required checks pass.

## 4. Harbor retention policy

Add a Harbor retention rule that PROTECTS `vX.Y.Z` (image) and `X.Y.Z` (chart)
tags from GC, separate from the churning `:SHORT_SHA` tags. Without this the
recurring `chart-not-found` class (`operator-ci-partial-chart-publish`,
helmfile MEMORY #45/#46) recurs with semver tags.

## 5. Observability alert (safe to land early)

`tatara-observability/alerts/tatara-cd.yaml` (`tatara_cd_cascade_failed` /
`_stalled`, gauge semantics) stays inert (value 0) until the operator emits the
gauges - which ships with the operator branch. Merge observability whenever; it
reconciles once operator is deployed.

## 6. CUTOVER (irreversible - do auto-merge enablement and the no-self-merge
##    docs together)

Order matters: enabling auto-merge BEFORE the docs forbid self-merge is fine;
landing the docs BEFORE auto-merge exists freezes all merges. So:

1. Deploy the operator branch (bot-gated `EnableAutoMerge` + significance gate +
   `Deploying` phase + deploy-supervision + deploy-ledger + cdScan + issue-close)
   via a tatara-helmfile bump (the LAST hand-edited pin bump - after this,
   bumps are automated). Set the Project CR `deployBudgetSeconds` (default 3300;
   single-hop override 2100) if tuning the 1.2x budget.
2. In the SAME cutover, land the agent-facing doc edits forbidding self-merge:
   add the CD section to each component `CLAUDE.md` (agents declare
   `change_significance`; NEVER self-merge; the pipeline merges/tags/deploys/
   closes) and the `MEMORY.md` note. (These edits were intentionally NOT made by
   the build - they belong here.)
3. From now on: every component merge auto-cuts a tag, auto-propagates, and
   auto-applies; the originating issue auto-closes on apply success.

## 7. Post-cutover cleanup

- Retire each repo's legacy `ci.yml` image/chart publish job once its helmfile
  pin has moved to `vX.Y.Z` (the workflow left them in place, so during cutover
  each merge double-builds: `:SHORT_SHA` via ci.yml then `:vX.Y.Z` via
  release.yml - buildkit cache makes the second cheap, but the duplicate is
  removable once pins are semver).
- Confirm the ingester `chart` job was dropped (vestigial; pinned nowhere).
- Reconcile the live `mem-infrastructure-pg` `io_method=sync` drift and any
  other open helmfile drift before the first automated apply.

## Known operational caveats (by design, not bugs)

- mode=tag is not idempotent on a re-run: re-running a release job after a tag
  was cut computes from the now-latest tag and cuts another bump. Do not blindly
  re-run a green release job.
- Propagation (bump) PRs are always labeled `semver:patch` - a dependency bump
  is a patch by definition. Component significance does NOT propagate past the
  first hop; that is intended (the parent's own version reflects "a dep moved").
- The cli/skills build-guard runs on push-to-main, not at PR time, so a
  guard-breaking bump auto-merges then fails post-merge CI - deploy-supervision /
  cdScan catch the stalled cascade and reroll. If a PR-time guard gate is wanted
  later, add it as a required check.
