# Per-project guidance + tatara self-infra awareness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add an optional `spec.scm.guidance` string to the Project CRD, append it to the brainstorm + healthCheck goal context, and set the tatara project's guidance so operational-infrastructure improvements are in-scope.

**Architecture:** One operator change (CRD field + goal injection + tests + regen manifests), then one tatara-helmfile MR (operator chart+image bump per the dual-pin rule + guidance value for project-tatara).

**Tech Stack:** Go (controller-runtime operator), Helm/Helmfile, mise.

## Global Constraints

- Build/test/lint via mise (`mise exec -- go test ./...`, `mise run lint`, `mise exec -- make manifests generate`). Never bare `go`.
- JSON logs; table-driven tests with `t.Run`; KISS, no tech-debt; no comments on unchanged code.
- Branch off fresh `main` (`git checkout main && git pull`); worktree; merge to main; build/deploy from main only.
- Deploy ONLY via tatara-helmfile GitOps. Per the dual-chart-pin memory: bump the `tatara-operator` chart pin AND BOTH `tatara-project` pins (project-tatara + project-infrastructure) to the new `0.0.0-<opSHA>`, plus the operator image tag.
- requesting-code-review before commit; verification-before-completion before claiming done.

---

## Task G1: Operator - `spec.scm.guidance` field + goal injection

Repo: `tatara-operator`. Branch: `feat/project-guidance` off fresh `main`.

**Files:**
- Modify: `api/v1alpha1/project_types.go` (add `Guidance` to `ScmSpec`, after the `Cron` field ~line 305)
- Modify: `internal/controller/projectscan.go` (`brainstormGoalProject` ~1052, `healthCheckGoalProject` ~1093; callers `brainstorm` ~931 and `healthCheck` where it builds the goal)
- Generated: `mise exec -- make manifests generate`
- Test: `internal/controller/projectscan_brainstorm_goal_test.go` (+ a healthCheck goal test)

**Interfaces:**
- Produces: `ScmSpec.Guidance string`; `brainstormGoalProject(slugs []string, repoStateCtx string, guidance string) string` and `healthCheckGoalProject(slugs []string, repoStateCtx string, guidance string) string` (new trailing `guidance` param).

- [ ] **Step 1: Write the failing tests**

```go
func TestBrainstormGoalAppendsGuidance(t *testing.T) {
	base := brainstormGoalProject([]string{"o/a"}, "STATE", "")
	if strings.Contains(base, "PROJECT CHARTER") {
		t.Fatal("empty guidance must not add a charter block")
	}
	g := brainstormGoalProject([]string{"o/a"}, "STATE", "self-hosting infra")
	if !strings.Contains(g, "PROJECT CHARTER: self-hosting infra") {
		t.Fatalf("guidance not appended: %s", g)
	}
}

func TestHealthCheckGoalAppendsGuidance(t *testing.T) {
	base := healthCheckGoalProject([]string{"o/a"}, "STATE", "")
	if strings.Contains(base, "PROJECT CHARTER") {
		t.Fatal("empty guidance must not add a charter block")
	}
	g := healthCheckGoalProject([]string{"o/a"}, "STATE", "self-hosting infra")
	if !strings.Contains(g, "PROJECT CHARTER: self-hosting infra") {
		t.Fatalf("guidance not appended: %s", g)
	}
}
```

- [ ] **Step 2: Run to verify they fail**

Run: `mise exec -- go test ./internal/controller/ -run AppendsGuidance -v`
Expected: FAIL (compile error - third arg / current signature is 2-arg).

- [ ] **Step 3: Add the CRD field**

In `api/v1alpha1/project_types.go`, in `ScmSpec`, after the `Cron *ScmCron` field:

```go
	// Guidance is free-form project charter text appended verbatim to the
	// brainstorm and healthCheck goal context. Empty leaves the goal unchanged.
	// +optional
	Guidance string `json:"guidance,omitempty"`
```

- [ ] **Step 4: Inject into both goal builders**

In `internal/controller/projectscan.go`, change both signatures to take a trailing `guidance string`, and before the final `return`-assembled string append (only when non-empty). Add a small helper to avoid duplication:

```go
func appendGuidance(goal, guidance string) string {
	if strings.TrimSpace(guidance) == "" {
		return goal
	}
	return goal + "\n\nPROJECT CHARTER: " + guidance
}
```

Have `brainstormGoalProject` and `healthCheckGoalProject` return `appendGuidance(<existing goal string>, guidance)`.

Update the callers:
- in `brainstorm`: `goal := brainstormGoalProject(slugs, issuesCtx, scmGuidance(proj))`
- in `healthCheck`: the matching `healthCheckGoalProject(slugs, issuesCtx, scmGuidance(proj))`

Add a nil-safe accessor:

```go
func scmGuidance(proj *tatarav1alpha1.Project) string {
	if proj.Spec.Scm == nil {
		return ""
	}
	return proj.Spec.Scm.Guidance
}
```

- [ ] **Step 5: Regenerate manifests + run tests**

Run: `mise exec -- make manifests generate`
Run: `mise exec -- go test ./internal/controller/ -run AppendsGuidance -v` (PASS), then `mise exec -- go test ./... && mise run lint`. Fix any existing goal-text test that called the builders with two args (now three).

- [ ] **Step 6: Code review, then commit**

`requesting-code-review`; fix critical/high. Then:

```bash
git add api/ internal/controller/ charts/ config/ 2>/dev/null; git add -A
git commit -m "feat: add spec.scm.guidance, append to brainstorm + healthCheck goals"
```

---

## Task G2: tatara-helmfile - deploy operator + tatara guidance

Repo: `tatara-helmfile`. Branch: `feat/project-guidance-deploy` off fresh `main`. PRECONDITION: G1 merged to operator main, image + charts built at `0.0.0-<opSHA>` (call it `GSHA`).

**Files:**
- Modify: `helmfile.yaml.gotmpl` (the `tatara-operator` chart `version` AND both `tatara-project` `version` pins -> `0.0.0-<GSHA>`)
- Modify: `values/tatara-operator/common.yaml` (image `tag` -> `<GSHA>`)
- Modify: `values/project-tatara/common.yaml` (add `scm.guidance` under the project's `scm:` block)

- [ ] **Step 1: Set the guidance value**

In `values/project-tatara/common.yaml`, under the tatara project's `scm:` map, add:

```yaml
        guidance: >-
          This project is self-hosting: its repositories (the tatara operator,
          cli, wrapper, memory, ingester, and helmfile) ARE the autonomous-agent
          platform you and the other agents run on. Treat the operational
          infrastructure as in-scope, not only application features: the helm
          charts, the tatara-helmfile releases and deploy pipeline, the CI
          workflows and build tooling, the k8s/ARC-runner/Ceph/memory-backend
          configuration, and the incidents recorded in each repository's
          MEMORY.md. When you find a recurring operational failure or a gap in
          the platform's reliability or observability, propose improvements to
          that infrastructure.
```

(Match the existing indentation of the `scm:` block in that file - confirm where `cron:`/`brainstorm:` sit and place `guidance` as a sibling scalar under `scm`.)

- [ ] **Step 2: Bump all chart pins + image tag**

- `helmfile.yaml.gotmpl`: the `tatara-operator` release `version: 0.0.0-<GSHA>` AND both `tatara-project` releases (project-tatara, project-infrastructure) `version: 0.0.0-<GSHA>`.
- `values/tatara-operator/common.yaml`: `image.tag: "<GSHA>"`.

- [ ] **Step 3: Open PR, verify CI diff, merge**

Push, open PR. CI runs `helmfile diff` (validates the `0.0.0-<GSHA>` charts are published and renders the guidance into the Project CR). On green, merge -> apply pipeline. Do not apply by hand.

- [ ] **Step 4: Verify live (verification-before-completion)**

After apply: `kubectl get project tatara -n tatara -o jsonpath='{.spec.scm.guidance}'` returns the charter text; operator pod on `<GSHA>`. Optionally confirm a subsequent brainstorm goal includes the charter (operator INFO log / next brainstorm task goal).

---

## Self-Review

- **Spec coverage:** CRD field -> G1 Step 3; goal injection both activities -> G1 Step 4; tatara guidance value -> G2 Step 1; deploy w/ dual-pin -> G2 Steps 2-3. All mapped.
- **Placeholders:** none; `<GSHA>` is the post-merge operator short-SHA, resolved at G2 time (documented), not a deferred TODO.
- **Type consistency:** `appendGuidance`, `scmGuidance`, and the 3-arg `brainstormGoalProject`/`healthCheckGoalProject` signatures are consistent across steps and tests.
