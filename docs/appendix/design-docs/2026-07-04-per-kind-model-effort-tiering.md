# Per-kind Model + Effort tiering Implementation Plan

> For agentic workers: this plan is executed by subagents.
> REQUIRED SUB-SKILL: superpowers:subagent-driven-development

**Goal:** Attack axis 1 of the token-conservation spec (component 1). Add
`modelByKind`/`effortByKind` map fields to the Project CR `AgentSpec`, resolve
`MODEL`/`EFFORT` per Task kind in `BuildPod` (per-kind override else project-wide
fallback), and carry the approved tier map in tatara-helmfile for both Projects.
The approved tiers, keyed on the operator's real `Task.Spec.Kind` enum:

| Kind | Model | Effort |
|---|---|---|
| triageIssue | claude-sonnet-5 | low |
| review | claude-sonnet-5 | medium |
| brainstorm (healthCheck shares Kind=brainstorm, inherits) | claude-opus-4-8 | high |
| refine | claude-opus-4-8 | high |
| implement | claude-opus-4-8 | high |
| incident | claude-opus-4-8 | high |
| issueLifecycle | claude-opus-4-8 | high |
| selfImprove | claude-opus-4-8 | high |

This is `xhigh` -> `high` everywhere the kind stays Opus.

**Architecture decision (KISS, applied in Task 5):** rather than enumerate all
eight kinds in `effortByKind`, we lower the project-wide `agent.effort` fallback
from `xhigh` to `high` and only override the two Sonnet kinds. All Opus kinds
(brainstorm/refine/implement/incident/issueLifecycle/selfImprove) then inherit
`high` from the fallback; only `triageIssue`/`review` carry explicit overrides.
Same for `model`: fallback stays `claude-opus-4-8`, `modelByKind` carries only
the two Sonnet entries. Minimal map, no redundant `high`/`opus` repetition.

**A/B RISK (plan comment, NOT a task):** `triageIssue` and `review` are the only
kinds dropping to Sonnet. `review` is the riskiest downgrade (it carries the
adversarial-verify structure). Both are high-frequency, so the volume covered is
large. The map is a pure tatara-helmfile values change, trivially reversible per
kind: revert a single `modelByKind`/`effortByKind` entry to bump one kind back to
Opus. Validate parity via the component-6 per-kind dashboards after rollout. No
task here mitigates this; it is a post-deploy observation gated on component 6.

**Architecture:** `AgentSpec` gains two `map[string]string` CRD spec fields
(CRD spec fields are typed API fields, exempt from the no-lists-in-values rule).
`BuildPod` (`internal/agent/pod.go`) already branches per kind for tool profile
(`toolProfileForKind`, pod.go:832) and skill profile (`skillProfileForKind`,
pod.go:861); the new `modelForKind`/`effortForKind` helpers follow that exact
pattern and wire into the `MODEL`/`EFFORT` env at pod.go:417-418. The Project CR
spec is rendered by the `tatara-project` chart via `toYaml .Values.project.spec`
(charts/tatara-project/templates/project.yaml:16), so map fields under
`project.spec.agent` in helmfile values pass straight through to the CR.

**Tech Stack:** Go 1.26.3 (pinned in `tatara-operator/go.mod`), controller-gen
v0.18.0 (`CONTROLLER_GEN_VERSION` in Makefile), controller-runtime CRD chart,
helm/helmfile. Tests: `go test` table-driven with `t.Run`, `stretchr/testify`
`require`. Repo tooling via mise (`mise exec -- go test ./...`).

## Global Constraints

- Newest stable Go; pin the exact minor in go.mod. gofmt + golangci-lint must pass. Wrap errors with %w. Table-driven tests with t.Run.
- KISS. No tech debt; if complex, note rationale in MEMORY.md.
- JSON logs via stdlib log/slog. Expose /metrics Prometheus on every service. Log business actions at INFO with structured fields.
- Charts via 'helm create' then edited, never hand-rolled. Charts cluster-agnostic.
- values.yaml rule: NO plain ENVs, NO lists in values.yaml. camelCase scalar in values.yaml -> kebab-case key in ConfigMap/Secret -> workload consumes via envFrom. List-shaped data goes into a templated ConfigMap read at runtime. (Note: CRD spec fields are exempt from this - they are typed API fields, not helm values; a map[string]string on a CRD spec is fine.)
- Model IDs are authoritative literals: claude-opus-4-8 (Opus), claude-sonnet-5 (Sonnet). Effort enum: low|medium|high|xhigh|max.
- Deploy ONLY via tatara-helmfile GitOps: merge component repo main (CI builds+pushes image/chart), then a tatara-helmfile MR bumping BOTH the chart version AND the pinned image.tag for the release. Never kubectl set-image/patch to ship. Project CR value changes are tatara-helmfile values edits.
- Branch flow: worktree off main -> develop -> merge to component main -> deploy from main only.

---

### Task 1: Add `modelByKind`/`effortByKind` map fields to `AgentSpec` + regenerate deepcopy + CRD manifest

**Files:**
- Modify: `tatara-operator/api/v1alpha1/project_types.go` (AgentSpec, after `Effort` at :139)
- Test (Create): `tatara-operator/api/v1alpha1/agentspec_bykind_test.go`
- Regenerated (do not hand-edit): `tatara-operator/api/v1alpha1/zz_generated.deepcopy.go` (AgentSpec.DeepCopyInto at :16), `tatara-operator/charts/tatara-operator/crd-bases/tatara.dev_projects.yaml` (agent block at :46)
- Unchanged (auto-picks-up new fields via Glob): `tatara-operator/charts/tatara-operator/templates/crds.yaml`

**Interfaces:**
- Produces: `AgentSpec.ModelByKind map[string]string` (json `modelByKind,omitempty`), `AgentSpec.EffortByKind map[string]string` (json `effortByKind,omitempty`).
- Consumed by: Task 2 helpers `modelForKind`/`effortForKind`.

Note on the CRD template: `charts/tatara-operator/templates/crds.yaml` globs
`crd-bases/tatara.dev_*.yaml` (`.Files.Glob "crd-bases/tatara.dev_*.yaml"`) and
injects `helm.sh/resource-policy: keep`. It does not enumerate fields, so
regenerating `crd-bases/tatara.dev_projects.yaml` via `make manifests` is the
only CRD-template action needed - no hand-edit of `crds.yaml`. Per the
[[operator-crd-templating-helm-adoption-2026-06-20]] memory, `helm upgrade`
applies the templated CRDs, so the new fields reach the live CRD on deploy.

- [ ] **Step 1: Write the failing deepcopy-independence test.**
  Create `tatara-operator/api/v1alpha1/agentspec_bykind_test.go`:
  ```go
  package v1alpha1

  import "testing"

  // TestAgentSpecDeepCopy_ByKindMapsIndependent asserts the generated deepcopy
  // makes the by-kind maps independent (mutating the source after a DeepCopy
  // must not mutate the copy). This fails to COMPILE until the fields exist and
  // fails at runtime until controller-gen regenerates the map-copy loops.
  func TestAgentSpecDeepCopy_ByKindMapsIndependent(t *testing.T) {
  	in := &AgentSpec{
  		Model:        "claude-opus-4-8",
  		Effort:       "high",
  		ModelByKind:  map[string]string{"review": "claude-sonnet-5"},
  		EffortByKind: map[string]string{"review": "medium"},
  	}
  	out := in.DeepCopy()
  	in.ModelByKind["review"] = "mutated"
  	in.EffortByKind["review"] = "mutated"
  	if out.ModelByKind["review"] != "claude-sonnet-5" {
  		t.Fatalf("ModelByKind not deep-copied: got %q", out.ModelByKind["review"])
  	}
  	if out.EffortByKind["review"] != "medium" {
  		t.Fatalf("EffortByKind not deep-copied: got %q", out.EffortByKind["review"])
  	}
  }
  ```

- [ ] **Step 2: Run the test, expect a COMPILE failure.**
  ```
  mise exec -- go test ./api/v1alpha1/ -run TestAgentSpecDeepCopy_ByKindMapsIndependent
  ```
  Expected output contains:
  ```
  ./agentspec_bykind_test.go:12:3: unknown field ModelByKind in struct literal of type AgentSpec
  ./agentspec_bykind_test.go:13:3: unknown field EffortByKind in struct literal of type AgentSpec
  FAIL	github.com/szymonrychu/tatara-operator/api/v1alpha1 [build failed]
  ```

- [ ] **Step 3: Add the two map fields to `AgentSpec`.**
  In `api/v1alpha1/project_types.go`, immediately after the `Effort` field
  (the block ending at line 139), insert:
  ```go
  	// ModelByKind overrides the project-wide Model per Task Kind. Keys are the
  	// Task.Spec.Kind enum values (triageIssue, review, brainstorm, refine,
  	// implement, incident, issueLifecycle, selfImprove); healthCheck shares
  	// Kind=brainstorm so inherits the brainstorm entry. A missing or empty entry
  	// falls back to Model. Values are authoritative model IDs (claude-opus-4-8,
  	// claude-sonnet-5).
  	// +optional
  	ModelByKind map[string]string `json:"modelByKind,omitempty"`
  	// EffortByKind overrides the project-wide Effort per Task Kind. Same keying as
  	// ModelByKind; a missing or empty entry falls back to Effort. Values are the
  	// effort enum (low|medium|high|xhigh|max).
  	// +optional
  	EffortByKind map[string]string `json:"effortByKind,omitempty"`
  ```

- [ ] **Step 4: Regenerate deepcopy and CRD manifests.**
  ```
  mise exec -- make generate manifests
  ```
  This runs controller-gen (v0.18.0): `generate` rewrites
  `api/v1alpha1/zz_generated.deepcopy.go` (adds map-copy loops for the two new
  maps inside `AgentSpec.DeepCopyInto`), `manifests` rewrites
  `charts/tatara-operator/crd-bases/tatara.dev_projects.yaml` (adds
  `modelByKind`/`effortByKind` `additionalProperties: {type: string}` under the
  `agent` properties). Confirm the generated deepcopy now contains a block like:
  ```
  	if in.ModelByKind != nil {
  		in, out := &in.ModelByKind, &out.ModelByKind
  		*out = make(map[string]string, len(*in))
  		for key, val := range *in {
  			(*out)[key] = val
  		}
  	}
  ```
  and the CRD yaml agent block now has:
  ```
                  modelByKind:
                    additionalProperties:
                      type: string
                    type: object
                  effortByKind:
                    additionalProperties:
                      type: string
                    type: object
  ```

- [ ] **Step 5: Run the test, expect PASS.**
  ```
  mise exec -- go test ./api/v1alpha1/ -run TestAgentSpecDeepCopy_ByKindMapsIndependent
  ```
  Expected: `ok  github.com/szymonrychu/tatara-operator/api/v1alpha1`.

- [ ] **Step 6: Verify the CRD chart-lint guard still passes** (the crds.yaml
  inject depends on controller-gen output shape):
  ```
  mise exec -- make chart-lint
  ```
  Expected: no `chart-lint: expected 5 tatara.dev CRDs ...` error; exit 0.

- [ ] **Step 7: gofmt + commit.**
  ```
  mise exec -- gofmt -s -w api/v1alpha1/project_types.go api/v1alpha1/agentspec_bykind_test.go
  git add api/v1alpha1/project_types.go api/v1alpha1/agentspec_bykind_test.go api/v1alpha1/zz_generated.deepcopy.go charts/tatara-operator/crd-bases/tatara.dev_projects.yaml
  git commit -m "feat: add modelByKind/effortByKind to AgentSpec for per-kind tiering"
  ```

---

### Task 2: `modelForKind` / `effortForKind` resolution helpers with table-driven tests

**Files:**
- Modify: `tatara-operator/internal/agent/pod.go` (add helpers next to `skillProfileForKind`, after :882)
- Test (Create): `tatara-operator/internal/agent/pod_model_effort_test.go`

**Interfaces:**
- Consumes: `AgentSpec.ModelByKind`/`EffortByKind` (Task 1), `project.Spec.Agent.Model`/`.Effort` (existing, project_types.go:103,139).
- Produces: `modelForKind(project *tatarav1alpha1.Project, kind string) string`, `effortForKind(project *tatarav1alpha1.Project, kind string) string`. Consumed by Task 3 (BuildPod wiring).

Resolution rule (mirrors the existing per-kind branch helpers' simplicity): a
non-empty per-kind entry wins; otherwise the project-wide value. Indexing a nil
map yields "", so no nil guard is needed - the `!= ""` check handles both
nil-map and set-empty. An empty-string override is treated as unset (defensive:
a stray empty value in the CR should not blank out MODEL/EFFORT).

- [ ] **Step 1: Write the failing helper tests.**
  Create `tatara-operator/internal/agent/pod_model_effort_test.go`:
  ```go
  package agent

  import (
  	"testing"

  	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
  )

  func TestModelForKind(t *testing.T) {
  	proj := &tatarav1alpha1.Project{}
  	proj.Spec.Agent.Model = "claude-opus-4-8"
  	proj.Spec.Agent.ModelByKind = map[string]string{
  		"review":      "claude-sonnet-5",
  		"triageIssue": "claude-sonnet-5",
  	}
  	cases := []struct {
  		name, kind, want string
  	}{
  		{"override present review", "review", "claude-sonnet-5"},
  		{"override present triage", "triageIssue", "claude-sonnet-5"},
  		{"override absent falls back", "implement", "claude-opus-4-8"},
  		{"unknown kind falls back", "bogus", "claude-opus-4-8"},
  		{"empty kind falls back", "", "claude-opus-4-8"},
  	}
  	for _, tc := range cases {
  		t.Run(tc.name, func(t *testing.T) {
  			if got := modelForKind(proj, tc.kind); got != tc.want {
  				t.Fatalf("modelForKind(%q) = %q, want %q", tc.kind, got, tc.want)
  			}
  		})
  	}
  }

  func TestModelForKind_NilAndEmptyOverride(t *testing.T) {
  	proj := &tatarav1alpha1.Project{}
  	proj.Spec.Agent.Model = "claude-opus-4-8"
  	if got := modelForKind(proj, "review"); got != "claude-opus-4-8" {
  		t.Fatalf("nil map: modelForKind = %q, want claude-opus-4-8", got)
  	}
  	proj.Spec.Agent.ModelByKind = map[string]string{"review": ""}
  	if got := modelForKind(proj, "review"); got != "claude-opus-4-8" {
  		t.Fatalf("empty override treated as set: modelForKind = %q, want claude-opus-4-8", got)
  	}
  }

  func TestEffortForKind(t *testing.T) {
  	proj := &tatarav1alpha1.Project{}
  	proj.Spec.Agent.Effort = "high"
  	proj.Spec.Agent.EffortByKind = map[string]string{
  		"review":      "medium",
  		"triageIssue": "low",
  	}
  	cases := []struct {
  		name, kind, want string
  	}{
  		{"override present review", "review", "medium"},
  		{"override present triage", "triageIssue", "low"},
  		{"override absent falls back", "implement", "high"},
  		{"unknown kind falls back", "bogus", "high"},
  		{"empty kind falls back", "", "high"},
  	}
  	for _, tc := range cases {
  		t.Run(tc.name, func(t *testing.T) {
  			if got := effortForKind(proj, tc.kind); got != tc.want {
  				t.Fatalf("effortForKind(%q) = %q, want %q", tc.kind, got, tc.want)
  			}
  		})
  	}
  }

  func TestEffortForKind_NilAndEmptyOverride(t *testing.T) {
  	proj := &tatarav1alpha1.Project{}
  	proj.Spec.Agent.Effort = "high"
  	if got := effortForKind(proj, "review"); got != "high" {
  		t.Fatalf("nil map: effortForKind = %q, want high", got)
  	}
  	proj.Spec.Agent.EffortByKind = map[string]string{"review": ""}
  	if got := effortForKind(proj, "review"); got != "high" {
  		t.Fatalf("empty override treated as set: effortForKind = %q, want high", got)
  	}
  }
  ```

- [ ] **Step 2: Run the tests, expect a COMPILE failure.**
  ```
  mise exec -- go test ./internal/agent/ -run 'TestModelForKind|TestEffortForKind'
  ```
  Expected output contains:
  ```
  ./pod_model_effort_test.go:24:16: undefined: modelForKind
  ./pod_model_effort_test.go:...: undefined: effortForKind
  FAIL	github.com/szymonrychu/tatara-operator/internal/agent [build failed]
  ```

- [ ] **Step 3: Implement the two helpers.**
  In `internal/agent/pod.go`, after `skillProfileForKind` (the function ending
  at line 882), add:
  ```go
  // modelForKind resolves the MODEL env for a Task Kind: a non-empty per-kind
  // override in AgentSpec.ModelByKind wins, else the project-wide Agent.Model.
  // Follows the toolProfileForKind/skillProfileForKind per-kind branch pattern.
  // A nil map or empty override value falls through to the project-wide model.
  func modelForKind(project *tatarav1alpha1.Project, kind string) string {
  	if v := project.Spec.Agent.ModelByKind[kind]; v != "" {
  		return v
  	}
  	return project.Spec.Agent.Model
  }

  // effortForKind resolves the EFFORT env for a Task Kind: a non-empty per-kind
  // override in AgentSpec.EffortByKind wins, else the project-wide Agent.Effort.
  func effortForKind(project *tatarav1alpha1.Project, kind string) string {
  	if v := project.Spec.Agent.EffortByKind[kind]; v != "" {
  		return v
  	}
  	return project.Spec.Agent.Effort
  }
  ```

- [ ] **Step 4: Run the tests, expect PASS.**
  ```
  mise exec -- go test ./internal/agent/ -run 'TestModelForKind|TestEffortForKind'
  ```
  Expected: `ok  github.com/szymonrychu/tatara-operator/internal/agent`.

- [ ] **Step 5: gofmt + commit.**
  ```
  mise exec -- gofmt -s -w internal/agent/pod.go internal/agent/pod_model_effort_test.go
  git add internal/agent/pod.go internal/agent/pod_model_effort_test.go
  git commit -m "feat: add modelForKind/effortForKind per-kind resolution helpers"
  ```

---

### Task 3: Wire `modelForKind`/`effortForKind` into `BuildPod`'s MODEL/EFFORT env

**Files:**
- Modify: `tatara-operator/internal/agent/pod.go:417-418` (MODEL/EFFORT env in `BuildPod`)
- Test (Modify): `tatara-operator/internal/agent/pod_model_effort_test.go` (add `TestBuildPod_ModelEffortByKind`)

**Interfaces:**
- Consumes: `modelForKind`/`effortForKind` (Task 2), `BuildPod(project, repo, task, repos, memoryEndpoint, cfg)` (pod.go:396), `envValue` test helper (pod_effort_test.go:10).
- Produces: MODEL/EFFORT env resolved per `task.Spec.Kind`.

- [ ] **Step 1: Write the failing BuildPod wiring test.**
  Append to `tatara-operator/internal/agent/pod_model_effort_test.go` (add the
  `require`, `corev1`, `metav1` imports to the file's import block):
  ```go
  func TestBuildPod_ModelEffortByKind(t *testing.T) {
  	proj := &tatarav1alpha1.Project{
  		ObjectMeta: metav1.ObjectMeta{Name: "demo", Namespace: "tatara"},
  		Spec: tatarav1alpha1.ProjectSpec{
  			ScmSecretRef: "demo-scm",
  			Agent: tatarav1alpha1.AgentSpec{
  				Model:              "claude-opus-4-8",
  				Effort:             "high",
  				Image:              "wrapper:1",
  				PermissionMode:     "bypassPermissions",
  				TurnTimeoutSeconds: 1800,
  				ModelByKind: map[string]string{
  					"review":      "claude-sonnet-5",
  					"triageIssue": "claude-sonnet-5",
  				},
  				EffortByKind: map[string]string{
  					"review":      "medium",
  					"triageIssue": "low",
  				},
  			},
  		},
  	}
  	repo := &tatarav1alpha1.Repository{
  		ObjectMeta: metav1.ObjectMeta{Name: "repo1", Namespace: "tatara"},
  		Spec:       tatarav1alpha1.RepositorySpec{URL: "https://git/acme/repo1", DefaultBranch: "main"},
  	}
  	cfg := PodConfig{
  		Namespace:           "tatara",
  		CallbackURL:         "http://tatara-operator-internal.tatara.svc:8082",
  		OIDCIssuer:          "https://keycloak.tatara.svc/realms/master",
  		AnthropicSecretName: "anthropic",
  		CLIOIDCSecretName:   "tatara-cli-oidc",
  		OperatorURL:         "http://tatara-operator.tatara.svc:8080",
  	}
  	cases := []struct {
  		kind, wantModel, wantEffort string
  	}{
  		{"review", "claude-sonnet-5", "medium"},
  		{"triageIssue", "claude-sonnet-5", "low"},
  		{"implement", "claude-opus-4-8", "high"},
  		{"brainstorm", "claude-opus-4-8", "high"},
  	}
  	for _, tc := range cases {
  		t.Run(tc.kind, func(t *testing.T) {
  			task := &tatarav1alpha1.Task{
  				ObjectMeta: metav1.ObjectMeta{Name: "task-1", Namespace: "tatara", UID: "uid-1"},
  				Spec:       tatarav1alpha1.TaskSpec{ProjectRef: "demo", RepositoryRef: "repo1", Goal: "g", Kind: tc.kind},
  			}
  			env := BuildPod(proj, repo, task, nil, "http://mem.tatara.svc:8080", cfg).Spec.Containers[0].Env
  			model, ok := envValue(env, "MODEL")
  			require.True(t, ok, "MODEL env present for kind %q", tc.kind)
  			require.Equal(t, tc.wantModel, model, "MODEL for kind %q", tc.kind)
  			effort, ok := envValue(env, "EFFORT")
  			require.True(t, ok, "EFFORT env present for kind %q", tc.kind)
  			require.Equal(t, tc.wantEffort, effort, "EFFORT for kind %q", tc.kind)
  		})
  	}
  }
  ```
  The file's import block becomes:
  ```go
  import (
  	"testing"

  	"github.com/stretchr/testify/require"
  	corev1 "k8s.io/api/core/v1"
  	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

  	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
  )
  ```
  (`corev1` is used transitively by the shared test package; keep it only if the
  linter does not flag it unused - if `goimports`/`golangci-lint` reports
  `corev1` unused in this file, drop that one import line. `metav1` and `require`
  are used directly here.)

- [ ] **Step 2: Run the test, expect FAIL (wrong values, not compile).**
  ```
  mise exec -- go test ./internal/agent/ -run TestBuildPod_ModelEffortByKind
  ```
  Expected failure (BuildPod still reads the project-wide Model/Effort, so the
  Sonnet kinds get the Opus/high fallback):
  ```
  --- FAIL: TestBuildPod_ModelEffortByKind/review
      pod_model_effort_test.go:...: MODEL for kind "review":
      	expected: "claude-sonnet-5"
      	actual  : "claude-opus-4-8"
  FAIL
  ```

- [ ] **Step 3: Wire the helpers into BuildPod.**
  In `internal/agent/pod.go`, replace lines 417-418:
  ```go
  		{Name: "MODEL", Value: project.Spec.Agent.Model},
  		{Name: "EFFORT", Value: project.Spec.Agent.Effort},
  ```
  with:
  ```go
  		{Name: "MODEL", Value: modelForKind(project, task.Spec.Kind)},
  		{Name: "EFFORT", Value: effortForKind(project, task.Spec.Kind)},
  ```

- [ ] **Step 4: Run the new test + the existing effort test, expect PASS.**
  The pre-existing `TestBuildPod_SetsEffortEnv` (pod_effort_test.go) sets only
  `Agent.Effort` with an empty Kind, so `effortForKind` falls back to the
  project value and it must still pass.
  ```
  mise exec -- go test ./internal/agent/ -run 'TestBuildPod_ModelEffortByKind|TestBuildPod_SetsEffortEnv'
  ```
  Expected: `ok  github.com/szymonrychu/tatara-operator/internal/agent`.

- [ ] **Step 5: Full operator test + lint sweep (nothing else regresses).**
  ```
  mise exec -- go test ./... && mise exec -- golangci-lint run
  ```
  Expected: all packages `ok`, golangci-lint exit 0.

- [ ] **Step 6: gofmt + commit.**
  ```
  mise exec -- gofmt -s -w internal/agent/pod.go internal/agent/pod_model_effort_test.go
  git add internal/agent/pod.go internal/agent/pod_model_effort_test.go
  git commit -m "feat: resolve MODEL/EFFORT per Task kind in BuildPod"
  ```

- [ ] **Step 7: Request code review, fix critical/high findings, run pre-commit, then merge to operator main.**
  Per the working agreement: `superpowers:requesting-code-review`, fix
  critical/high, `pre-commit run --all-files`, then merge the worktree branch to
  `tatara-operator` `main`. CI on main builds+pushes the operator image
  (`harbor.szymonrichert.pl/containers/tatara-operator:<shortSHA>`) and packages
  the `tatara-operator` + `tatara-project` charts as `0.0.0-g<shortSHA>`. Record
  the resulting operator main short SHA - Task 5 needs it. Call it
  `<newOperatorSHA>` below.

---

### Task 4: Carry the approved tier map in tatara-helmfile Project values (both Projects)

**Files:**
- Modify: `tatara-helmfile/values/project-tatara/common.yaml` (project.spec.agent block, :17-23)
- Modify: `tatara-helmfile/values/project-infrastructure/common.yaml` (project.spec.agent block, :17-23)

**Interfaces:**
- Consumes: the `modelByKind`/`effortByKind` CRD fields (Task 1) via the
  `tatara-project` chart's `toYaml .Values.project.spec`
  (charts/tatara-project/templates/project.yaml:16).
- Produces: Project CR `spec.agent.modelByKind`/`effortByKind` maps + the
  lowered project-wide `effort: high` fallback.

This repo has no Go/unit tests; verification is `helmfile template` rendering
the CR and asserting the maps land. The value edit and the deploy pin-bump
(Task 5) go in ONE tatara-helmfile MR (the CRD with the new fields must be live
for the CR carrying the maps to validate; the pins move in lockstep).

- [ ] **Step 1: Edit `values/project-tatara/common.yaml` agent block.**
  Replace the current agent block (:17-23):
  ```yaml
      agent:
        effort: xhigh
        image: harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper:8f3d880
        maxTurnsPerTask: 100
        model: claude-opus-4-8
        permissionMode: bypassPermissions
        turnTimeoutSeconds: 2700
  ```
  with (fallback effort lowered xhigh -> high; only the two Sonnet kinds
  overridden - see the KISS decision at the top of this plan):
  ```yaml
      agent:
        # Per-kind tiering (token-conservation component 1): project-wide fallback
        # is Opus/high; only triageIssue+review drop to Sonnet with low/medium
        # effort. All other kinds (brainstorm, refine, implement, incident,
        # issueLifecycle, selfImprove; healthCheck shares Kind=brainstorm) inherit
        # the high fallback. xhigh->high everywhere it stays Opus.
        effort: high
        model: claude-opus-4-8
        modelByKind:
          triageIssue: claude-sonnet-5
          review: claude-sonnet-5
        effortByKind:
          triageIssue: low
          review: medium
        image: harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper:8f3d880
        maxTurnsPerTask: 100
        permissionMode: bypassPermissions
        turnTimeoutSeconds: 2700
  ```

- [ ] **Step 2: Edit `values/project-infrastructure/common.yaml` agent block** identically.
  Replace the current agent block (:17-23):
  ```yaml
      agent:
        effort: xhigh
        image: harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper:8f3d880
        maxTurnsPerTask: 100
        model: claude-opus-4-8
        permissionMode: bypassPermissions
        turnTimeoutSeconds: 2700
  ```
  with:
  ```yaml
      agent:
        # Per-kind tiering (token-conservation component 1): project-wide fallback
        # is Opus/high; only triageIssue+review drop to Sonnet with low/medium
        # effort. All other kinds inherit the high fallback. xhigh->high where it
        # stays Opus.
        effort: high
        model: claude-opus-4-8
        modelByKind:
          triageIssue: claude-sonnet-5
          review: claude-sonnet-5
        effortByKind:
          triageIssue: low
          review: medium
        image: harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper:8f3d880
        maxTurnsPerTask: 100
        permissionMode: bypassPermissions
        turnTimeoutSeconds: 2700
  ```
  (Preserve the surrounding grafana/memory/scm blocks unchanged; only the
  `agent:` sub-block is edited.)

- [ ] **Step 3: Commit the value change** (the pin-bump in Task 5 lands in the
  same MR; commit separately for a clean diff).
  ```
  git add values/project-tatara/common.yaml values/project-infrastructure/common.yaml
  git commit -m "feat: per-kind model/effort tier map on both Project CRs"
  ```

---

### Task 5: Deploy bump - dual-pin operator chart + image.tag, in the MR carrying Task 4

**Files:**
- Modify: `tatara-helmfile/helmfile.yaml.gotmpl` (three `version:` pins at :64, :83, :95)
- Modify: `tatara-helmfile/values/tatara-operator/common.yaml` (`image.tag` at :6)

**Interfaces:**
- Consumes: `<newOperatorSHA>` from Task 3 Step 7 (operator main merge), the
  Task 4 value edits (same MR).
- Produces: a tatara-helmfile MR that, on the sticky `helmfile diff`, shows the
  operator Deployment image + CRD update and the two Project CR agent-map
  additions; auto-applies on merge.

Per [[tatara-helmfile-dual-chart-pin-and-cr-adoption-2026-06-22]] and
[[tatara-operator-deploy-chart-version-and-image-tag]]: an operator deploy bumps
BOTH the chart version pin AND the operator container `image.tag`; a chart-only
bump leaves the old operator image running (so the new `modelForKind` code never
executes). Both `tatara-operator` and the two `tatara-project` releases pin the
same operator-published chart SHA and must move in lockstep (the CRD shipped by
`tatara-operator@<sha>` must match the `tatara-project` chart the CRs render
against - the helmfile comment at :54-59 stresses this).

- [ ] **Step 1: Bump the three chart pins to the new operator SHA.**
  In `helmfile.yaml.gotmpl`, change all three occurrences of
  `version: 0.0.0-gfb8531a` (lines 64, 83, 95 - the `tatara-operator`,
  `project-tatara`, `project-infrastructure` releases) to
  `version: 0.0.0-g<newOperatorSHA>`.

- [ ] **Step 2: Bump the operator container image tag.**
  In `values/tatara-operator/common.yaml`, change:
  ```yaml
  image:
    tag: "fb8531a"
  ```
  to:
  ```yaml
  image:
    tag: "<newOperatorSHA>"
  ```

- [ ] **Step 3: Verify the CRs render with the new maps.**
  ```
  helmfile -f helmfile.yaml.gotmpl template -l application=tatara-project
  ```
  Expected: both rendered `Project` manifests contain, under `spec.agent`:
  ```
        modelByKind:
          review: claude-sonnet-5
          triageIssue: claude-sonnet-5
        effortByKind:
          review: medium
          triageIssue: low
        effort: high
  ```
  (map key order in the render is not significant).

- [ ] **Step 4: Verify the diff (before applying).**
  ```
  helmfile -f helmfile.yaml.gotmpl diff -l application=tatara-operator -l application=tatara-project
  ```
  Expected: the `tatara-operator` Deployment image changes
  `...tatara-operator:fb8531a` -> `...tatara-operator:<newOperatorSHA>`, the
  `projects.tatara.dev` CRD gains `modelByKind`/`effortByKind` under
  `spec.properties.agent.properties`, and both `Project` CRs gain the
  `spec.agent.modelByKind`/`effortByKind` maps with `effort: high` (was
  `xhigh`). No unrelated release drift.

- [ ] **Step 5: Commit and open the MR** (auto-merge on pipeline success per the
  tatara-helmfile flow; the sticky diff is the review artifact).
  ```
  git add helmfile.yaml.gotmpl values/tatara-operator/common.yaml
  git commit -m "chore: deploy per-kind tiering - bump operator to <newOperatorSHA> + Project tier maps"
  git push -u origin <branch>
  ```
  Open the MR (or push to the flow that posts the sticky `helmfile diff`); merge
  to `main` auto-applies on the in-cluster ARC runner.

- [ ] **Step 6: Post-apply verification** (per
  `superpowers:verification-before-completion`):
  ```
  kubectl -n tatara get deploy tatara-operator -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
  kubectl -n tatara get project tatara -o jsonpath='{.spec.agent.modelByKind}{"\n"}{.spec.agent.effortByKind}{"\n"}{.spec.agent.effort}{"\n"}'
  kubectl -n tatara get project infrastructure -o jsonpath='{.spec.agent.modelByKind}{"\n"}{.spec.agent.effortByKind}{"\n"}{.spec.agent.effort}{"\n"}'
  ```
  Expected: operator image tag `<newOperatorSHA>`; both Projects report
  `{"review":"claude-sonnet-5","triageIssue":"claude-sonnet-5"}`,
  `{"review":"medium","triageIssue":"low"}`, and `effort: high`. Then confirm on
  the next scan cycle that a fresh `review` pod carries `MODEL=claude-sonnet-5`
  and `EFFORT=medium` while an `implement` pod carries `MODEL=claude-opus-4-8`
  and `EFFORT=high`:
  ```
  kubectl -n tatara get pods -l tatara.dev/kind=review -o jsonpath='{range .items[*]}{.spec.containers[0].env[?(@.name=="MODEL")].value} {.spec.containers[0].env[?(@.name=="EFFORT")].value}{"\n"}{end}'
  ```
  (verify the pod-label selector against the live wrapper pod labels; if the
  operator does not label pods by kind, inspect a review pod's env directly by
  name). Do NOT verify on transition-period/terminating pods per
  [[verify-fix-via-downstream-not-draining-pods]].

- [ ] **Step 7: Record the outcome in tatara-operator MEMORY.md** (one dated
  line) noting component 1 shipped, the A/B risk on triageIssue/review, and that
  reverting a single `modelByKind`/`effortByKind` entry in tatara-helmfile bumps
  a kind back to Opus. Sequence any CLAUDE.md/MEMORY.md policy edits at cutover
  per [[semver-push-cd-2026-06-28]], not mid-rollout.

---

## Verification summary (whole plan)

- Task 1: `go test ./api/v1alpha1/` green; `make chart-lint` green; CRD yaml
  carries `modelByKind`/`effortByKind`.
- Task 2/3: `go test ./...` + `golangci-lint run` green in tatara-operator;
  `TestBuildPod_SetsEffortEnv` still passes (fallback path intact).
- Task 4/5: `helmfile template` shows both Project CRs with the tier maps;
  `helmfile diff` shows operator image + CRD update; post-apply `kubectl`
  confirms live CR maps and a fresh review pod on Sonnet/medium, implement pod on
  Opus/high.
