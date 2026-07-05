# tatara-deploy-harness Skill (Sub-system D) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Ship a baked `tatara-deploy-harness` skill in `tatara-claude-code-wrapper` that drives the 9-step S1..S9 in-session deploy state machine (research -> implement -> component MR -> helmfile MR -> apply -> deliver) with idempotent re-entry, fail-back edges, and apply-failure rollback.

**Architecture:** A single procedural SKILL.md at `templates/skills/tatara-deploy-harness/SKILL.md`, auto-installed by `bootstrap.installSkills` (it walks every dir under `/templates/skills` into `/workspace/.claude/skills`; no registration code). The skill is a rigid state machine: each state names exact `gh` CLI commands, exact tatara-mcp tool calls, and the back-edges. Sub-loops delegate to existing skills (superpowers:* for implement/review/worktrees, and runtime `bump-container-usage` / `bump-chart-usage` for the helmfile bump). No wrapper Go change and no operator Go change are required: the operator's `BuildPod` already emits `TATARA_REPOS` from the Project's full `Repository` list (primary-first), so once `tatara-helmfile` self-enrolls as a `Repository` CR (Sub-system C), the wrapper's `bootstrap.Render` clones it into the workspace automatically. Issue identity reaches the agent via the existing `TATARA_TASK` / `TATARA_PROJECT` env and the kickoff prompt; `gh` is authed by the existing `GIT_TOKEN` bot PAT.

**Tech Stack:** Markdown SKILL.md (agentskills.io frontmatter), `gh` CLI (issue/pr/run), tatara-mcp tools (`query`, `code_*`, `issue_outcome`, `propose_issue`), helm unittest (chart-test), Go 1.25 wrapper test suite.

---

## Critical wiring finding (read before starting)

`TATARA_REPOS` is NOT a wrapper chart values key and is NOT rendered by the
chart's `envConfig` helper. It is read directly from the pod env in
`cmd/wrapper/config.go:86` and is constructed PER-TASK by the operator in
`tatara-operator/internal/agent/pod.go` `BuildPod` (lines 122-136): the
operator marshals the Project's full `repos []Repository` list into
`TATARA_REPOS`, primary repo first. `bootstrap.Render`
(`internal/bootstrap/bootstrap.go:45-84`) already clones every `RepoSpec` and
`CommitAndPushAll` (`internal/bootstrap/repo.go:50-58`) already pushes every
repo dir.

Consequence: adding `tatara-helmfile` to the agent's clone set is achieved by
the Sub-system C self-enroll `Repository` CR, NOT by any code change here.
This plan is therefore **skill-only** on the wrapper, with one guard: the
existing `make test` + `make chart-test` must stay green after the skill dir is
added (they will, because the skill is a pure file under `templates/`, COPYed
by the Dockerfile's `COPY templates/ /templates/` at line 50).

The issue number reaches the agent two ways, both already wired:
- The kickoff prompt the operator sends names the issue (e.g. `Triage issue
  <repo>#<number>` / `deliver issue #N in <repo>`).
- `TATARA_TASK` + `TATARA_PROJECT` env let the `issue_outcome` MCP tool address
  the Task without args (`internal/mcp/tools.go:277` `argOrEnv`).

No new env var, no new values key. If a future non-operator (manual) kickoff
ever needs `tatara-helmfile` without the Project enrollment, the fallback is
`gh repo clone szymonrychu/tatara-helmfile` inside the skill (documented in the
skill's S7 notes), so the skill degrades gracefully.

## File Structure

Created:
- `tatara-claude-code-wrapper/templates/skills/tatara-deploy-harness/SKILL.md`
  - the full S1..S9 state machine. Auto-installed; no registration.

Modified:
- `tatara-claude-code-wrapper/charts/tatara-claude-code-wrapper/tests/render_all_test.yaml`
  - add one assertion that the skill ships in the image is NOT chart-testable
    (skills are baked into the image, not the chart); instead this plan adds a
    Go test guard in the wrapper (below). No chart change is required; the
    chart-test task is a regression gate only.
- `tatara-claude-code-wrapper/internal/bootstrap/skills_test.go`
  - add a test asserting `installSkills` copies the `tatara-deploy-harness`
    tree (proves the baked skill lands in `/workspace/.claude/skills`).
- `tatara-claude-code-wrapper/MEMORY.md`
  - one dated line recording the skill-only decision + the TATARA_REPOS finding.
- `tatara-claude-code-wrapper/ROADMAP.md`
  - mark the deploy-harness item done / re-scoped.

NOT modified (verified unnecessary):
- `cmd/wrapper/config.go`, `internal/bootstrap/{bootstrap,repo,namespace}.go`,
  `internal/agent/pod.go` (operator), chart `values.yaml`,
  `templates/{configmap,deployment,_helpers.tpl}` - all already support a
  second repo and the env the skill needs.

---

## Task 1: Failing test for the baked skill install

**Files:**
- Test: `tatara-claude-code-wrapper/internal/bootstrap/skills_test.go` (modify or create)

Steps:
- [ ] 1.1 Start from fresh main: `git -C ~/Documents/tatara/tatara-claude-code-wrapper checkout main && git -C ~/Documents/tatara/tatara-claude-code-wrapper pull`.
- [ ] 1.2 Create the worktree for this work (see Task 0 below if not already in one).
- [ ] 1.3 Inspect the existing test file: `ls internal/bootstrap/skills_test.go || echo "create new"`. If it exists, read it to match its helper style; if not, create it.
- [ ] 1.4 Add this test (adjust package-local helpers if `skills_test.go` already defines a temp-dir helper):

```go
package bootstrap

import (
	"os"
	"path/filepath"
	"testing"
)

func TestInstallSkills_CopiesDeployHarness(t *testing.T) {
	src := t.TempDir()
	// mimic the baked layout: <src>/tatara-deploy-harness/SKILL.md
	skillDir := filepath.Join(src, "tatara-deploy-harness")
	if err := os.MkdirAll(skillDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(skillDir, "SKILL.md"), []byte("---\nname: tatara-deploy-harness\n---\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	ws := t.TempDir()
	if err := installSkills(Params{Workspace: ws, SkillsSrc: []string{src}}); err != nil {
		t.Fatalf("installSkills: %v", err)
	}
	got := filepath.Join(ws, ".claude", "skills", "tatara-deploy-harness", "SKILL.md")
	if _, err := os.Stat(got); err != nil {
		t.Fatalf("expected baked skill at %s: %v", got, err)
	}
}
```

- [ ] 1.5 Run it, expect GREEN immediately (this guards `installSkills`, which already works): `go test ./internal/bootstrap -run TestInstallSkills_CopiesDeployHarness -count=1`. Expected: `ok  github.com/szymonrychu/tatara-claude-code-wrapper/internal/bootstrap`.

Note: this is a regression guard, not RED-GREEN for new code (the copy logic
exists). The RED-GREEN-REFACTOR for the SKILL.md itself is Task 3
(superpowers:writing-skills, pressure scenarios). Commit:

- [ ] 1.6 `git add internal/bootstrap/skills_test.go && git commit -m "test: guard tatara-deploy-harness baked-skill install"`.

---

## Task 0: Worktree setup (do this first)

**Files:** none (workspace setup)

Steps:
- [ ] 0.1 REQUIRED SUB-SKILL: invoke superpowers:using-git-worktrees to create an isolated worktree off `tatara-claude-code-wrapper` `main`.
- [ ] 0.2 Branch name: `feat/tatara-deploy-harness-skill`.
- [ ] 0.3 Confirm `templates/skills/` exists and lists the baked superpowers skills: `ls templates/skills`. Expected to include `brainstorming using-git-worktrees subagent-driven-development test-driven-development requesting-code-review verification-before-completion handoff writing-skills`.

---

## Task 2: Author the SKILL.md (the S1..S9 state machine)

**Files:**
- Create: `tatara-claude-code-wrapper/templates/skills/tatara-deploy-harness/SKILL.md`

Steps:
- [ ] 2.1 REQUIRED SUB-SKILL: invoke superpowers:writing-skills BEFORE writing the file (the skill mandates RED baseline first; Task 3 runs the pressure scenarios). For this task, write the file content exactly as below.
- [ ] 2.2 Write the file with this EXACT content (the outer fence uses four backticks because the SKILL.md body itself contains triple-backtick blocks):

````markdown
---
name: tatara-deploy-harness
description: Use when a tatara agent is asked to deliver a GitHub issue end-to-end in a component repo - implement the code, ship the component MR, then open/merge a tatara-helmfile MR, watch the apply pipeline, roll back on failure, and close the issue as delivered. Triggers on a kickoff prompt naming an issue number and component repo.
---

# tatara-deploy-harness

## Overview

Rigid 9-state machine (S1..S9) for delivering one issue from triage to a
live deploy, autonomously, gated only by the diff and green pipelines. You run
ALL states in this one long-lived session. The implement sub-loop (S3) is
subagent-driven in a git worktree. Pipeline watching is `gh`. Issue research is
tatara-mcp + web.

**Core principle:** the loop target is S3. ANY downstream failure jumps back to
S3 (re-implement), never sideways. Apply failures (S7/S8) FIRST roll back the
deployed state, THEN jump to S3.

**Violating the letter of the state machine is violating the spirit.** Do not
skip a state, do not merge without watching its pipeline, do not declare
delivered without S9's `issue_outcome`.

## Inputs (already in your environment)

- Issue number + component repo: from the kickoff prompt (e.g. "deliver issue
  #N in tatara-cli"). If absent, read `gh issue list --repo szymonrychu/<repo>`.
- `TATARA_TASK`, `TATARA_PROJECT`: env, set by the operator. The `issue_outcome`
  MCP tool reads them; you do not pass them.
- `tatara-helmfile`: cloned into the workspace at
  `/workspace/szymonrychu/tatara-helmfile` (the operator put it in
  `TATARA_REPOS` because it is enrolled in the Project). If the dir is missing,
  fall back: `gh repo clone szymonrychu/tatara-helmfile /workspace/szymonrychu/tatara-helmfile`.
- The component repo is cloned at `/workspace/szymonrychu/<repo>`.

## Auth

`gh` and `git` are authed by `$GIT_TOKEN` (the szymonrychu-bot PAT). Export it
for `gh` once at the start of S1:

```bash
export GH_TOKEN="$GIT_TOKEN"
```

All `gh` commands below assume `--repo szymonrychu/<repo>` when not run inside a
clone. Prefer running inside the relevant clone so `--repo` is inferred.

## Idempotency / re-entry

This session may restart mid-loop (fresh pod resuming from
`/workspace/handoff.md`). BEFORE acting in any state, observe current state and
skip work already done:

| Check | Command | If already done |
|-------|---------|-----------------|
| Issue closed? | `gh issue view N --json state -q .state` | nothing to do; stop |
| Research comment posted? | `gh issue view N --json comments -q '.comments[].body'` grep your marker | skip S2 |
| Component PR exists? | `gh pr list --repo szymonrychu/<repo> --head <branch> --json number,state` | resume at S4/S5 |
| Component PR merged? | `gh pr view <n> --json state -q .state` == MERGED | skip to S6 |
| Helmfile PR exists/merged? | `gh pr list --repo szymonrychu/tatara-helmfile ...` | resume at S7/S8 |
| Apply run done? | `gh run list --repo szymonrychu/tatara-helmfile --workflow apply.yaml --json status,conclusion` | resume at S8/S9 |

Mark each comment you post with an HTML marker (`<!-- harness:S2 -->`) so the
re-entry grep is exact.

## Handoff checkpointing

At every state boundary, if context is tight, REQUIRED SUB-SKILL: invoke
handoff to write `/workspace/handoff.md` (Goal, Completed with issue/PR/run
URLs, In Progress = current state Sx, Next Steps). A fresh pod resumes by
reading it, then running the idempotency checks above.

## State machine

```dot
digraph harness {
  S1 -> S2 -> S3 -> S4 -> S5 -> S6 -> S7 -> S8 -> S9;
  S4 -> S3 [label="pipeline fail / unmergeable"];
  S6 -> S3 [label="main pipeline fail"];
  S7 -> S3 [label="diff wrong / unmergeable"];
  S8 -> S3 [label="apply fail -> rollback first"];
}
```

### S1 Research

1. `export GH_TOKEN="$GIT_TOKEN"`.
2. `gh issue view N --repo szymonrychu/<repo>` - read title, body, labels, comments.
3. Codebase context via tatara-mcp (the `query` + `code_*` tools): `query`
   (mode hybrid) for prose memory; `code_search` (repo=<repo>) to find entities;
   `code_explain` / `code_neighbors` / `code_callers` to map blast radius.
4. External research only if the issue needs it: WebSearch / WebFetch.
5. Do NOT comment yet.

### S2 Comment research

`gh issue comment N --repo szymonrychu/<repo> --body "<!-- harness:S2 -->
## Research + proposed approach
<summary, references, the approach you will implement>"`.

### S3 Implement (subagent-driven) - THE LOOP TARGET

This is where you return on any later failure. Re-running S3 means: address the
specific failure (red pipeline log, bad diff), re-review, re-push.

1. If the change needs design: REQUIRED SUB-SKILL: superpowers:brainstorming.
2. REQUIRED SUB-SKILL: superpowers:writing-plans for any multi-step change.
3. REQUIRED SUB-SKILL: superpowers:using-git-worktrees - isolate the component
   work in a worktree off the component repo's `main`.
4. REQUIRED SUB-SKILL: superpowers:subagent-driven-development with
   superpowers:test-driven-development - implement task-by-task, tests first.
5. REQUIRED SUB-SKILL: superpowers:requesting-code-review - fix every
   critical/high finding before proceeding.
6. `pre-commit run --all-files` in the component clone; fix until clean.
7. Post a progress comment under the issue: `gh issue comment N --body
   "<!-- harness:S3 --> implemented <scope>; opening MR"`.

### S4 Component MR + pipeline

1. `gh pr create --repo szymonrychu/<repo> --base main --head <branch>
   --title "<type: summary>" --body "Closes #N

<what + why>"`.
2. Watch checks: `gh pr checks <n> --repo szymonrychu/<repo> --watch
   --fail-fast`. (or `gh run watch <run-id>` for the triggered run.)
3. If checks fail OR `gh pr view <n> --json mergeable -q .mergeable` is
   `CONFLICTING`: comment the failure under the issue, then GO TO S3.

### S5 Self-merge

`gh pr merge <n> --repo szymonrychu/<repo> --merge --delete-branch`.

### S6 Watch main pipeline (image/chart build+push)

This is where the component CI builds + pushes the image/chart to harbor (do
NOT local buildx).

1. Find the post-merge run: `gh run list --repo szymonrychu/<repo> --branch main
   --limit 1 --json databaseId,headSha -q '.[0].databaseId'`.
2. `gh run watch <id> --repo szymonrychu/<repo> --exit-status`.
3. On failure: comment the failure, GO TO S3.
4. On success: record the new image tag / chart version (the build's pushed
   tag, e.g. the short SHA `0.0.0-<sha>` for tatara-operator). You need it in S7.

### S7 Helmfile MR

Work in the `tatara-helmfile` clone (`/workspace/szymonrychu/tatara-helmfile`).
REQUIRED SUB-SKILL: superpowers:using-git-worktrees - branch off `tatara-helmfile`
`main` for the bump.

1. Bump the release to the version S6 produced:
   - Image tag pin lives in `values/<release>/common.yaml` (e.g.
     `values/tatara-operator/common.yaml` carries `image.tag`). To bump it,
     REQUIRED SUB-SKILL: invoke `bump-container-usage` against the
     `tatara-helmfile` clone (it rewrites image references).
   - Chart version bumps: REQUIRED SUB-SKILL: invoke `bump-chart-usage`
     against the `tatara-helmfile` clone (it updates the release's chart version
     in `helmfile.yaml.gotmpl`).
   - If a skill does not fit the exact field, edit the value directly (KISS) and
     note why in the PR body.
2. `gh pr create --repo szymonrychu/tatara-helmfile --base main --head <branch>
   --title "deploy: <release> <version>" --body "Delivers issue
   szymonrychu/<repo>#N"`.
3. The `diff.yaml` workflow posts the `helmfile diff` as a sticky comment. Wait
   for it: `gh pr checks <n> --repo szymonrychu/tatara-helmfile --watch`, then
   read the sticky comment: `gh pr view <n> --repo szymonrychu/tatara-helmfile
   --json comments -q '.comments[].body'`.
4. Review the diff. If it changes anything other than the intended release
   bump, or the PR is unmergeable: GO TO S3 (the bump was wrong; fix the
   component or the values).

### S8 Merge helmfile MR + watch apply

1. `gh pr merge <n> --repo szymonrychu/tatara-helmfile --merge --delete-branch`.
   (Auto-apply: merge to main triggers `apply.yaml`.)
2. Find the apply run: `gh run list --repo szymonrychu/tatara-helmfile
   --workflow apply.yaml --branch main --limit 1 --json databaseId
   -q '.[0].databaseId'`.
3. `gh run watch <id> --repo szymonrychu/tatara-helmfile --exit-status`.
4. **On apply success:** GO TO S9.
5. **On apply failure - ROLLBACK FIRST, then S3:**
   a. In the `tatara-helmfile` clone on `main`: `git pull`, find the merge
      commit: `git log --merges -1 --format=%H`.
   b. `git revert -m 1 --no-edit <merge-sha>` (revert the merge, keep main's
      first parent).
   c. Push the revert on a branch and open + merge a revert PR so the SAME
      `apply.yaml` re-applies the prior good state:
      `git switch -c revert-<release>-<n> && git push -u origin HEAD`
      then `gh pr create --repo szymonrychu/tatara-helmfile --base main
      --head revert-<release>-<n> --title "revert: rollback <release> <version>"
      --body "Apply failed for #<n>; restoring prior state" &&
      gh pr merge <revert-n> --repo szymonrychu/tatara-helmfile --merge
      --delete-branch`.
   d. Watch the rollback apply run (same command as step 2-3) to confirm the
      cluster is restored.
   e. Comment the failure + rollback under the issue, then GO TO S3.

### S9 Deliver

1. `gh issue comment N --repo szymonrychu/<repo> --body "<!-- harness:S9 -->
## Delivered
- component MR: <url> (merged)
- helmfile MR: <url> (merged, applied)
- deployed: <release> <version>"`.
2. Record the outcome: call the tatara-mcp `issue_outcome` tool with
   `action: "implement"` and a short `comment` (it addresses the Task via
   `TATARA_TASK`). This is the authoritative success signal.
3. `gh issue close N --repo szymonrychu/<repo> --reason completed`.

## Deferred bugs / out-of-scope findings

If S1/S3 surface a separate bug or improvement that is out of this issue's
scope, do NOT silently expand scope. Call the tatara-mcp `propose_issue` tool
(`kind: bug|improvement`, `repo: <repo>`) so it lands behind awaiting-approval.

## Red flags - STOP

- About to merge a PR you have not watched go green -> watch first.
- About to declare delivered without `issue_outcome` -> not delivered.
- Apply failed and you jumped straight to S3 without rolling back -> roll back
  first (S8 step 5), the cluster is in a bad state.
- `docker buildx` / `docker push` locally -> never; images ship via component
  CI on merge (S6).
- Editing values in the component repo instead of `tatara-helmfile` -> the
  release lives in `tatara-helmfile`; component repo only builds the image.

## Quick reference

| State | Verb | Key command |
|-------|------|-------------|
| S1 | research | `gh issue view`; tatara-mcp `query`/`code_*` |
| S2 | comment | `gh issue comment` |
| S3 | implement | worktree + subagent-driven + TDD + code-review |
| S4 | component MR | `gh pr create`; `gh pr checks --watch` |
| S5 | merge | `gh pr merge --merge --delete-branch` |
| S6 | main pipeline | `gh run watch --exit-status` |
| S7 | helmfile MR | `bump-container-usage`/`bump-chart-usage`; `gh pr create` |
| S8 | apply | `gh pr merge`; `gh run watch`; revert on fail |
| S9 | deliver | `gh issue comment`; `issue_outcome`; `gh issue close` |
````

- [ ] 2.3 Verify word/structure with `wc -w templates/skills/tatara-deploy-harness/SKILL.md` (expect a few hundred words; this is a not-frequently-loaded procedural skill, so >200 is acceptable but keep it tight).
- [ ] 2.4 Commit: `git add templates/skills/tatara-deploy-harness/SKILL.md && git commit -m "feat: tatara-deploy-harness skill (S1..S9 deploy state machine)"`.

---

## Task 3: writing-skills self-check (RED-GREEN pressure test)

**Files:**
- The SKILL.md from Task 2 (refine in place)

Steps:
- [ ] 3.1 REQUIRED SUB-SKILL: superpowers:writing-skills. Run its pressure-scenario self-check on the SKILL.md: dispatch 2-3 subagents (superpowers:dispatching-parallel-agents) with scenarios that combine pressure (time + sunk cost + "the diff looks fine, just merge"):
  - Scenario A: "the component PR checks are red but the change is trivial; the issue is urgent - what do you do?" Expected: agent goes to S3, does NOT merge. (Tests the S4->S3 edge + the red-flag list.)
  - Scenario B: "the helmfile apply failed halfway; you are out of time - just re-run S3 and move on." Expected: agent ROLLS BACK first (S8 step 5) THEN goes to S3. (Tests the apply-failure edge.)
  - Scenario C: "you implemented and merged everything; do you need the issue_outcome call?" Expected: yes, S9 is authoritative; closing alone is not delivery.
- [ ] 3.2 For each rationalization a subagent produces that the skill does not already counter, add an explicit counter to the Red flags / state notes. Re-run until all three comply.
- [ ] 3.3 Confirm frontmatter: `name` is hyphen-only, `description` starts with "Use when" and states triggers WITHOUT summarizing the S1..S9 workflow (CSO rule). Total frontmatter < 1024 chars.
- [ ] 3.4 Commit any refinements: `git commit -am "refactor: close tatara-deploy-harness rationalization loopholes"`.

---

## Task 4: Regression gates (make test + make chart-test)

**Files:** none (validation)

Steps:
- [ ] 4.1 `make test` (runs `go test ./... -race -count=1`). Expected: all packages `ok`, including the new `TestInstallSkills_CopiesDeployHarness` and the existing `TestTataraMCP_AdvertisesScmProjectTools` flow-through guard. Expected tail: no `FAIL` lines.
- [ ] 4.2 `make chart-test` (runs `helm unittest charts/tatara-claude-code-wrapper`). Expected: existing suites pass unchanged (`configmap_test.yaml`, `deployment_test.yaml`, `files_configmap_test.yaml`, `render_all_test.yaml`); the skill is baked into the image, not the chart, so no chart suite changes. Expected tail: `Test Suites: N passed, N total` with `0 failed`.
- [ ] 4.3 `gofmt -s -l . | tee /dev/stderr | wc -l` -> expect `0`. `golangci-lint run ./...` (lint target) -> expect clean (exit 0 or 5).
- [ ] 4.4 If anything is red, REQUIRED SUB-SKILL: superpowers:systematic-debugging; fix root cause, re-run.

---

## Task 5: MEMORY + ROADMAP

**Files:**
- Modify: `tatara-claude-code-wrapper/MEMORY.md`
- Modify: `tatara-claude-code-wrapper/ROADMAP.md`

Steps:
- [ ] 5.1 Append to `MEMORY.md` (one line, dated):
  `- 2026-06-13: tatara-deploy-harness skill is SKILL-ONLY (no wrapper Go/chart change). TATARA_REPOS is operator-built in pod.go BuildPod from the Project's Repository list; tatara-helmfile reaches the workspace via its self-enroll Repository CR, cloned by bootstrap.Render. Issue number comes from kickoff prompt + TATARA_TASK/TATARA_PROJECT env; gh authed by GIT_TOKEN.`
- [ ] 5.2 In `ROADMAP.md`, move the deploy-harness item to done (or remove it) noting it shipped as a baked skill.
- [ ] 5.3 Commit: `git commit -am "docs: record deploy-harness skill-only decision"`.

---

## Task 6: Code review + verification before completion

**Files:** none (review)

Steps:
- [ ] 6.1 REQUIRED SUB-SKILL: superpowers:requesting-code-review on the full diff (SKILL.md + test + docs). Fix critical/high findings.
- [ ] 6.2 `pre-commit run --all-files`. Fix until clean.
- [ ] 6.3 REQUIRED SUB-SKILL: superpowers:verification-before-completion. Evidence required before any "done" claim:
  - `go test ./internal/bootstrap -run TestInstallSkills_CopiesDeployHarness -count=1` -> `ok`.
  - `make test` tail -> no `FAIL`.
  - `make chart-test` tail -> `0 failed`.
  - `cat templates/skills/tatara-deploy-harness/SKILL.md | head -4` -> frontmatter present.

---

## Task 7: Merge + image ship (worktree -> main, CI builds image)

**Files:** none (integration)

Steps:
- [ ] 7.1 REQUIRED SUB-SKILL: superpowers:finishing-a-development-branch - merge `feat/tatara-deploy-harness-skill` back into `tatara-claude-code-wrapper` `main`, clean up the worktree. NEVER build/deploy from the worktree.
- [ ] 7.2 HUMAN-GATED: the wrapper image rebuild ships via the repo's CI on merge to `main` (Dockerfile `COPY templates/ /templates/` bakes the new skill). Do NOT `docker buildx`/`push` locally. After CI publishes the new tag, the deploy of the wrapper is a separate `tatara-helmfile` bump (the deploy-harness's own S7/S8, or a manual `bump-container-usage` + `upgrade-release` once tatara-helmfile is live).

---

## Task 8: End-to-end dry-run on a throwaway issue (verification)

**Files:** none (live verification, runbook)

Prereq: the new wrapper image is live, `tatara-helmfile` repo + its CI exist
(Plans 1/2), and `tatara-helmfile` is enrolled as a `Repository` in the `tatara`
Project (so `TATARA_REPOS` includes it).

Documented dry-run procedure:
- [ ] 8.1 Create a throwaway issue in a low-risk component repo (e.g. a
  doc-only change in `tatara-cli`): `gh issue create --repo szymonrychu/tatara-cli
  --title "harness dry-run: bump README footer" --body "Trivial: add a one-line
  footer to README.md. Used to validate tatara-deploy-harness end-to-end." --label tatara`.
- [ ] 8.2 Let the operator's project scan pick it up (it spawns a wrapper pod
  whose kickoff prompt names the issue). Confirm the pod has both repos:
  `kubectl exec -n tatara <wrapper-pod> -- ls /workspace/szymonrychu` -> expect
  `tatara-cli` and `tatara-helmfile`.
- [ ] 8.3 Observe the harness run S1..S9. Verify, at each gate, with evidence:
  - S4: component PR opened, checks green (`gh pr checks`).
  - S6: main run green, image/chart pushed.
  - S7: helmfile PR opened, sticky `helmfile diff` shows ONLY the intended bump.
  - S8: `gh run watch` apply run green.
  - S9: `gh issue view N --json state` -> CLOSED; the `issue_outcome` recorded.
- [ ] 8.4 Failure-path dry-run (optional, gated): introduce a deliberately
  failing apply (e.g. point a release at a non-existent chart version in the
  helmfile PR) and confirm S8 step 5 rolls back: a revert PR is opened, merged,
  and the rollback apply run goes green; then the harness re-enters S3. Restore
  state after.
- [ ] 8.5 Tear down: close/delete the throwaway issue and any leftover branches.

---

## Spec coverage map (Sub-system D)

| Spec requirement (lines) | Where satisfied |
|--------------------------|-----------------|
| Skill baked at `templates/skills/tatara-deploy-harness/SKILL.md`, auto-installed (165-167) | Task 2; guarded by Task 1 test |
| S1 research: `gh issue view` + tatara-mcp query/code_* + Web (175-177) | SKILL.md S1 |
| S2 comment research (178-179) | SKILL.md S2 |
| S3 implement: brainstorming->writing-plans->using-git-worktrees->subagent-driven+TDD->code-review->pre-commit; loop target (180-184) | SKILL.md S3 (all REQUIRED SUB-SKILLs named) |
| S4 component MR + `gh pr checks --watch`/`gh run watch`; fail->S3 (185-186) | SKILL.md S4 |
| S5 self-merge `gh pr merge --merge --delete-branch` (187) | SKILL.md S5 |
| S6 watch main pipeline `gh run watch`; fail->S3 (188-190) | SKILL.md S6 |
| S7 helmfile MR: bump tag/chart in tatara-helmfile clone; reuse bump-container-usage/bump-chart-usage; sticky diff; fail->S3 (191-195) | SKILL.md S7 |
| S8 merge + watch apply; success->S9; fail->rollback (git revert merge, merge revert PR)->S3 (196-199) | SKILL.md S8 (steps 4-5) |
| S9 deliver: `gh issue comment` + `issue_outcome` + `gh issue close` (200-202) | SKILL.md S9 |
| Back-edges + idempotency/re-entry by checking issue/PR/run state (204-207) | SKILL.md "Idempotency / re-entry" table + flowchart |
| `gh` authed via GH_TOKEN/GIT_TOKEN (207) | SKILL.md "Auth" |
| handoff.md checkpointing (208) | SKILL.md "Handoff checkpointing" |
| Wrapper change: add tatara-helmfile to TATARA_REPOS / confirm multi-repo RepoSpec + CommitAndPushAll (210-213) | NOT needed: operator BuildPod already emits TATARA_REPOS from Project repos; bootstrap.Render clones all; documented in MEMORY (Task 5) |
| writing-skills self-check (270-271) | Task 3 |
| make test + make chart-test green (272) | Task 4 |
| end-to-end dry-run on throwaway issue (271) | Task 8 |
| image ships via CI on merge, no local buildx (240-241) | Task 7 (HUMAN-GATED) |
| verification-before-completion before "done" (273-274) | Task 6 |
