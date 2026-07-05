# Phase 2 Semantic Ingest Wiring (tatara-operator) Implementation Plan

This plan wires the Phase 2 semantic ceiling into the operator's ingest path: a per-Repository `semanticIngest` opt-out (CRD field, default true), and three new env vars on the ingest Job's main container (`OPENAI_API_KEY` sourced from the OpenAI Secret, `SEMANTIC_MODEL` defaulting to `gpt-4o-mini`, `SEMANTIC_INGEST` from the Repository flag). It also threads the OpenAI Secret name and model into the `ingest.Config` builder and adds the sample manifest flag.

Scope is operator-only. The memory/ingester/cli changes from the spec live in their own repos and are out of scope here.

## Design decisions (locked before tasks)

- **`SemanticIngest bool`, not `*bool`.** The repo's `RepositorySpec` already uses a value `bool` with `+kubebuilder:default=true` for `IngestEnabled`. The operator's tests compare `ingest.Config` structs with `==` (`wire_test.go`), and `Repository` values are passed by struct. A `*bool` would complicate deepcopy and break the `getDefault`-free value-struct style. Match the existing `IngestEnabled bool` + `+kubebuilder:default=true` pattern exactly. The CRD default makes an omitted field decode to `true`; in-process zero-value Repositories (constructed in Go tests without the API server applying defaults) read `false`, so the Job builder treats the flag verbatim and the controller-level default is the CRD's job. `deepcopy` for a value `bool` is `*out = *in` (no regen change to `RepositorySpec.DeepCopyInto` body), so `make generate` is a no-op for this field but is still run to prove it.
- **`SEMANTIC_INGEST` env value is `"true"`/`"false"`** (string form of the bool), emitted on the main container unconditionally so the ingester can read it deterministically.
- **`OPENAI_API_KEY`** is sourced via `SecretKeyRef{Name: cfg.OpenAISecretName, Key: "LLM_BINDING_API_KEY"}`, matching exactly how `lightragEnv` wires the same secret/key pair (`internal/memory/lightrag.go:53`). When `cfg.OpenAISecretName` is empty the env var is omitted entirely (no dangling SecretKeyRef), so the ingester's "no key -> skip semantic stage" guard fires.
- **`SEMANTIC_MODEL`** is a literal env var, value `cfg.SemanticModel`, defaulting to `gpt-4o-mini` when unset. The default is applied in `config.Load` (env `SEMANTIC_MODEL`), mirroring `getDefault`.
- **New `ingest.Config` fields:** `OpenAISecretName string` and `SemanticModel string`, populated by `ingestConfigFromConfig` from the operator `config.Config` (which already carries `OpenAISecretName`; `SemanticModel` is added there).

## Verification commands

- Per-package test: `go test ./api/v1alpha1/... -count=1`, `go test ./internal/ingest/... -count=1`, `go test ./internal/config/... -count=1`, `go test ./cmd/manager/... -count=1`.
- Codegen: `make generate` (deepcopy), `make manifests` (CRD).
- All env tests use the existing `envValue` helper and a new `envSecretRef` helper added in the ingest test file.

---

## Task 1: Add `SemanticIngest` field to `RepositorySpec`

### Files
- Modify: `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/repository_types.go`
- Modify: `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/repository_types_test.go`

### Steps

1. Write the failing test. Append to `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/repository_types_test.go`:

```go
func TestRepositorySpec_SemanticIngestField(t *testing.T) {
	r := &Repository{}
	r.Spec.SemanticIngest = true
	if !r.Spec.SemanticIngest {
		t.Fatalf("SemanticIngest = %v, want true", r.Spec.SemanticIngest)
	}
}

func TestRepository_DeepCopyCopiesSemanticIngest(t *testing.T) {
	r := &Repository{}
	r.Spec.SemanticIngest = true
	cp := r.DeepCopy()
	if !cp.Spec.SemanticIngest {
		t.Errorf("deepcopy lost SemanticIngest: %v", cp.Spec.SemanticIngest)
	}
}
```

2. Run RED: `go test ./api/v1alpha1/... -run 'TestRepositorySpec_SemanticIngestField|TestRepository_DeepCopyCopiesSemanticIngest' -count=1`
   Expected fail: compile error `r.Spec.SemanticIngest undefined (type RepositorySpec has no field or method SemanticIngest)`.

3. Minimal impl. In `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/repository_types.go`, edit the `RepositorySpec` struct to add the field after `IngestEnabled` (before `ReingestSchedule`). The full updated struct:

```go
// RepositorySpec defines the desired state of a Repository.
type RepositorySpec struct {
	ProjectRef string `json:"projectRef"`
	URL        string `json:"url"`
	// +kubebuilder:default="main"
	// +optional
	DefaultBranch string `json:"defaultBranch,omitempty"`
	// +kubebuilder:default=true
	// +optional
	IngestEnabled bool `json:"ingestEnabled,omitempty"`
	// SemanticIngest enables Phase 2 LLM semantic extraction for this
	// repository's ingest Job. Defaults true; set false to run AST-only
	// ingest and avoid per-changed-file LLM cost.
	// +kubebuilder:default=true
	// +optional
	SemanticIngest bool `json:"semanticIngest,omitempty"`
	// ReingestSchedule is a standard 5-field cron expression (e.g. "0 6 * * *")
	// that triggers a periodic catch-up re-ingest in addition to push webhooks.
	// +kubebuilder:validation:Required
	// +kubebuilder:validation:MinLength=9
	// +kubebuilder:validation:Pattern=`^(\S+\s+){4}\S+$`
	ReingestSchedule string `json:"reingestSchedule"`
}
```

4. Run GREEN: `go test ./api/v1alpha1/... -run 'TestRepositorySpec_SemanticIngestField|TestRepository_DeepCopyCopiesSemanticIngest' -count=1`
   Expected pass: `ok  github.com/szymonrychu/tatara-operator/api/v1alpha1`.

5. Commit:

```
git add api/v1alpha1/repository_types.go api/v1alpha1/repository_types_test.go
git commit -m "feat(api): add Repository.spec.semanticIngest (default true)"
```

---

## Task 2: Regenerate deepcopy and CRD manifests

### Files
- Modify (generated): `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/zz_generated.deepcopy.go`
- Modify (generated): `/Users/szymonri/Documents/tatara/tatara-operator/charts/tatara-operator/crds/tatara.dev_repositories.yaml`

### Steps

1. Write the failing test. Append to `/Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/repository_types_test.go`:

```go
func TestRepository_DeepCopyIndependentSemanticIngest(t *testing.T) {
	r := &Repository{}
	r.Spec.SemanticIngest = true
	cp := r.DeepCopy()
	cp.Spec.SemanticIngest = false
	if !r.Spec.SemanticIngest {
		t.Fatal("mutating the copy's SemanticIngest changed the original; deepcopy must be a value copy")
	}
}
```

2. Run RED: `go test ./api/v1alpha1/... -run TestRepository_DeepCopyIndependentSemanticIngest -count=1`
   Expected pass already (a value `bool` deepcopies via `*out = *in`), so RED here is the codegen freshness check instead: run `make generate && git diff --exit-code api/v1alpha1/zz_generated.deepcopy.go`.
   Expected: exit code 0 (no diff) because a value `bool` adds no deepcopy lines. If controller-gen rewrites formatting it surfaces here; otherwise the generated file is already correct. The substantive generated artifact is the CRD.

3. Minimal impl. Regenerate both artifacts:

```
make generate
make manifests
```

`make generate` updates `zz_generated.deepcopy.go` if needed (no-op for a value bool). `make manifests` writes the `semanticIngest` property (`type: boolean`, `default: true`) into `charts/tatara-operator/crds/tatara.dev_repositories.yaml`.

4. Run GREEN: `go test ./api/v1alpha1/... -count=1` and confirm the CRD carries the field: `grep -n semanticIngest charts/tatara-operator/crds/tatara.dev_repositories.yaml`
   Expected pass: `ok  github.com/szymonrychu/tatara-operator/api/v1alpha1`; grep prints the `semanticIngest:` property line with a `default: true` nearby.

5. Commit:

```
git add api/v1alpha1/zz_generated.deepcopy.go charts/tatara-operator/crds/tatara.dev_repositories.yaml
git commit -m "chore(api): regen deepcopy + CRD for semanticIngest"
```

---

## Task 3: Add `SemanticModel` to operator `config.Config`

### Files
- Modify: `/Users/szymonri/Documents/tatara/tatara-operator/internal/config/config.go`
- Modify: `/Users/szymonri/Documents/tatara/tatara-operator/internal/config/config_test.go`

### Steps

1. Write the failing test. In `/Users/szymonri/Documents/tatara/tatara-operator/internal/config/config_test.go`, add `"SEMANTIC_MODEL": "gpt-4o-mini"` to the `env` map in `TestLoad` (insert after the `"OPENAI_SECRET_NAME"` line), add the corresponding table row, and add a defaults test. Add this row to the `tests` slice in `TestLoad` (after the `OpenAISecretName` row):

```go
		{"SemanticModel", cfg.SemanticModel, "gpt-4o-mini"},
```

Add the `SEMANTIC_MODEL` entry to the `env` map in `TestLoad` immediately after the `OPENAI_SECRET_NAME` entry:

```go
		"SEMANTIC_MODEL":              "gpt-4o-mini",
```

Append a new defaults test:

```go
func TestLoad_SemanticModelDefault(t *testing.T) {
	t.Setenv("OIDC_ISSUER", "https://kc/realms/tatara")
	t.Setenv("OIDC_AUDIENCE", "tatara-operator")
	t.Setenv("SEMANTIC_MODEL", "")

	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.SemanticModel != "gpt-4o-mini" {
		t.Fatalf("SemanticModel default = %q, want gpt-4o-mini", cfg.SemanticModel)
	}
}
```

2. Run RED: `go test ./internal/config/... -count=1`
   Expected fail: compile error `cfg.SemanticModel undefined (type config.Config has no field or method SemanticModel)`.

3. Minimal impl. In `/Users/szymonri/Documents/tatara/tatara-operator/internal/config/config.go`, add the field to the `Config` struct after `OpenAISecretName`:

```go
	OpenAISecretName         string
	SemanticModel            string
```

And populate it in `Load`, after the `OpenAISecretName` line:

```go
		OpenAISecretName:         os.Getenv("OPENAI_SECRET_NAME"),
		SemanticModel:            getDefault("SEMANTIC_MODEL", "gpt-4o-mini"),
```

4. Run GREEN: `go test ./internal/config/... -count=1`
   Expected pass: `ok  github.com/szymonrychu/tatara-operator/internal/config`.

5. Commit:

```
git add internal/config/config.go internal/config/config_test.go
git commit -m "feat(config): add SemanticModel (SEMANTIC_MODEL, default gpt-4o-mini)"
```

---

## Task 4: Add `OpenAISecretName` + `SemanticModel` to `ingest.Config` and thread via `ingestConfigFromConfig`

### Files
- Modify: `/Users/szymonri/Documents/tatara/tatara-operator/internal/ingest/job.go`
- Modify: `/Users/szymonri/Documents/tatara/tatara-operator/cmd/manager/wire.go`
- Modify: `/Users/szymonri/Documents/tatara/tatara-operator/cmd/manager/wire_test.go`

### Steps

1. Write the failing test. In `/Users/szymonri/Documents/tatara/tatara-operator/cmd/manager/wire_test.go`, replace the body of `TestIngestConfigFromConfig` with the version that sets and asserts the two new fields:

```go
func TestIngestConfigFromConfig(t *testing.T) {
	cfg := config.Config{
		IngesterImage:            "img:1",
		OIDCIssuer:               "https://kc/realms/t",
		OperatorOIDCClientID:     "tatara-operator",
		OperatorOIDCClientSecret: "secret",
		Namespace:                "tatara",
		OpenAISecretName:         "tatara-openai",
		SemanticModel:            "gpt-4o-mini",
	}
	got := ingestConfigFromConfig(cfg, "tatara-memory")
	want := ingest.Config{
		IngesterImage:    "img:1",
		OIDCIssuer:       "https://kc/realms/t",
		OIDCClientID:     "tatara-operator",
		OIDCClientSecret: "secret",
		OIDCAudience:     "tatara-memory",
		Namespace:        "tatara",
		OpenAISecretName: "tatara-openai",
		SemanticModel:    "gpt-4o-mini",
	}
	if got != want {
		t.Errorf("ingestConfigFromConfig = %+v, want %+v", got, want)
	}
}
```

2. Run RED: `go test ./cmd/manager/... -run TestIngestConfigFromConfig -count=1`
   Expected fail: compile error `unknown field OpenAISecretName in struct literal of type ingest.Config` (and `SemanticModel`).

3. Minimal impl. In `/Users/szymonri/Documents/tatara/tatara-operator/internal/ingest/job.go`, add the two fields to the `Config` struct:

```go
// Config is the subset of operator configuration the Job builder needs.
type Config struct {
	IngesterImage    string
	OIDCIssuer       string
	OIDCClientID     string
	OIDCClientSecret string
	OIDCAudience     string
	Namespace        string
	ImagePullSecret  string
	OpenAISecretName string
	SemanticModel    string
}
```

In `/Users/szymonri/Documents/tatara/tatara-operator/cmd/manager/wire.go`, update `ingestConfigFromConfig` to pass them through:

```go
func ingestConfigFromConfig(cfg config.Config, memoryAudience string) ingest.Config {
	return ingest.Config{
		IngesterImage:    cfg.IngesterImage,
		OIDCIssuer:       cfg.OIDCIssuer,
		OIDCClientID:     cfg.OperatorOIDCClientID,
		OIDCClientSecret: cfg.OperatorOIDCClientSecret,
		OIDCAudience:     memoryAudience,
		Namespace:        cfg.Namespace,
		ImagePullSecret:  cfg.ImagePullSecret,
		OpenAISecretName: cfg.OpenAISecretName,
		SemanticModel:    cfg.SemanticModel,
	}
}
```

4. Run GREEN: `go test ./cmd/manager/... ./internal/ingest/... -count=1`
   Expected pass: `ok  github.com/szymonrychu/tatara-operator/cmd/manager` and `ok  github.com/szymonrychu/tatara-operator/internal/ingest`.

5. Commit:

```
git add internal/ingest/job.go cmd/manager/wire.go cmd/manager/wire_test.go
git commit -m "feat(ingest): thread OpenAISecretName + SemanticModel into ingest.Config"
```

---

## Task 5: Emit `OPENAI_API_KEY` (from the OpenAI Secret) on the ingest Job

### Files
- Modify: `/Users/szymonri/Documents/tatara/tatara-operator/internal/ingest/job.go`
- Modify: `/Users/szymonri/Documents/tatara/tatara-operator/internal/ingest/job_test.go`

### Steps

1. Write the failing test. In `/Users/szymonri/Documents/tatara/tatara-operator/internal/ingest/job_test.go`, add `OpenAISecretName` to `testConfig` and add an `envSecretRef` helper plus the assertion test. First, update `testConfig` to set the new field (full updated function):

```go
func testConfig() Config {
	return Config{
		IngesterImage:    "registry.example/ingester:1.2.3",
		OIDCIssuer:       "https://kc.example/realms/tatara",
		OIDCClientID:     "tatara-operator",
		OIDCClientSecret: "s3cr3t",
		OIDCAudience:     "tatara-memory",
		Namespace:        "tatara",
		ImagePullSecret:  "regcred",
		OpenAISecretName: "tatara-openai",
		SemanticModel:    "gpt-4o-mini",
	}
}
```

Add the helper and test (append to the file):

```go
func envSecretRef(c corev1.Container, key string) *corev1.SecretKeySelector {
	for _, e := range c.Env {
		if e.Name == key && e.ValueFrom != nil {
			return e.ValueFrom.SecretKeyRef
		}
	}
	return nil
}

func TestBuildJob_OpenAIKeyFromSecret(t *testing.T) {
	job := BuildJob(testProject(), testRepository(), "", testBaseURL, testConfig())
	main := job.Spec.Template.Spec.Containers[0]
	ref := envSecretRef(main, "OPENAI_API_KEY")
	if ref == nil {
		t.Fatal("ingest container must source OPENAI_API_KEY from a secret")
	}
	if ref.Name != "tatara-openai" || ref.Key != "LLM_BINDING_API_KEY" {
		t.Errorf("OPENAI_API_KEY secretKeyRef = %s/%s, want tatara-openai/LLM_BINDING_API_KEY",
			ref.Name, ref.Key)
	}
}

func TestBuildJob_OpenAIKeyOmittedWhenSecretUnset(t *testing.T) {
	cfg := testConfig()
	cfg.OpenAISecretName = ""
	job := BuildJob(testProject(), testRepository(), "", testBaseURL, cfg)
	main := job.Spec.Template.Spec.Containers[0]
	for _, e := range main.Env {
		if e.Name == "OPENAI_API_KEY" {
			t.Fatal("OPENAI_API_KEY must be omitted when OpenAISecretName is unset")
		}
	}
}
```

2. Run RED: `go test ./internal/ingest/... -run 'TestBuildJob_OpenAIKeyFromSecret|TestBuildJob_OpenAIKeyOmittedWhenSecretUnset' -count=1`
   Expected fail: `ingest container must source OPENAI_API_KEY from a secret` (the env var is not yet emitted).

3. Minimal impl. In `/Users/szymonri/Documents/tatara/tatara-operator/internal/ingest/job.go`, add a helper above `BuildJob` that appends the OpenAI key env only when the secret name is set:

```go
// semanticEnv returns the env vars that drive the ingester's Phase 2 semantic
// extraction stage: the OpenAI key (sourced from the shared OpenAI Secret, same
// secret/key pair lightrag uses) and the model. The key is omitted when no
// OpenAI Secret is configured so the ingester falls back to AST-only ingest.
func semanticEnv(cfg Config) []corev1.EnvVar {
	env := []corev1.EnvVar{}
	if cfg.OpenAISecretName != "" {
		env = append(env, corev1.EnvVar{
			Name: "OPENAI_API_KEY",
			ValueFrom: &corev1.EnvVarSource{
				SecretKeyRef: &corev1.SecretKeySelector{
					LocalObjectReference: corev1.LocalObjectReference{Name: cfg.OpenAISecretName},
					Key:                  "LLM_BINDING_API_KEY",
				},
			},
		})
	}
	return env
}
```

In `BuildJob`, change the main container's `Env` to append `semanticEnv(cfg)`. Replace the `Containers` block's `Env` field so the main container env becomes:

```go
						Env: append([]corev1.EnvVar{
							{Name: "BASE_URL", Value: baseURL},
							{Name: "OIDC_ISSUER", Value: cfg.OIDCIssuer},
							{Name: "OIDC_CLIENT_ID", Value: cfg.OIDCClientID},
							{Name: "OIDC_CLIENT_SECRET", Value: cfg.OIDCClientSecret},
							{Name: "OIDC_AUDIENCE", Value: cfg.OIDCAudience},
						}, semanticEnv(cfg)...),
```

4. Run GREEN: `go test ./internal/ingest/... -count=1`
   Expected pass: `ok  github.com/szymonrychu/tatara-operator/internal/ingest` (existing `TestBuildJob_FullIngest` env assertions still hold; the base five env vars are unchanged and ordered first).

5. Commit:

```
git add internal/ingest/job.go internal/ingest/job_test.go
git commit -m "feat(ingest): pass OPENAI_API_KEY from OpenAI secret to ingest Job"
```

---

## Task 6: Emit `SEMANTIC_MODEL` and `SEMANTIC_INGEST` on the ingest Job

### Files
- Modify: `/Users/szymonri/Documents/tatara/tatara-operator/internal/ingest/job.go`
- Modify: `/Users/szymonri/Documents/tatara/tatara-operator/internal/ingest/job_test.go`

### Steps

1. Write the failing test. Append to `/Users/szymonri/Documents/tatara/tatara-operator/internal/ingest/job_test.go`:

```go
func TestBuildJob_SemanticModelEnv(t *testing.T) {
	job := BuildJob(testProject(), testRepository(), "", testBaseURL, testConfig())
	main := job.Spec.Template.Spec.Containers[0]
	if v := envValue(main, "SEMANTIC_MODEL"); v != "gpt-4o-mini" {
		t.Errorf("SEMANTIC_MODEL = %q, want gpt-4o-mini", v)
	}
}

func TestBuildJob_SemanticIngestEnv_True(t *testing.T) {
	repo := testRepository()
	repo.Spec.SemanticIngest = true
	job := BuildJob(testProject(), repo, "", testBaseURL, testConfig())
	main := job.Spec.Template.Spec.Containers[0]
	if v := envValue(main, "SEMANTIC_INGEST"); v != "true" {
		t.Errorf("SEMANTIC_INGEST = %q, want true", v)
	}
}

func TestBuildJob_SemanticIngestEnv_False(t *testing.T) {
	repo := testRepository()
	repo.Spec.SemanticIngest = false
	job := BuildJob(testProject(), repo, "", testBaseURL, testConfig())
	main := job.Spec.Template.Spec.Containers[0]
	if v := envValue(main, "SEMANTIC_INGEST"); v != "false" {
		t.Errorf("SEMANTIC_INGEST = %q, want false", v)
	}
}
```

2. Run RED: `go test ./internal/ingest/... -run 'TestBuildJob_SemanticModelEnv|TestBuildJob_SemanticIngestEnv_True|TestBuildJob_SemanticIngestEnv_False' -count=1`
   Expected fail: `SEMANTIC_MODEL = "", want gpt-4o-mini` (env vars not yet emitted).

3. Minimal impl. In `/Users/szymonri/Documents/tatara/tatara-operator/internal/ingest/job.go`, extend `semanticEnv` to take the repo so it can emit the per-repo flag and the model literal. Replace `semanticEnv` with:

```go
// semanticEnv returns the env vars that drive the ingester's Phase 2 semantic
// extraction stage: the OpenAI key (sourced from the shared OpenAI Secret, same
// secret/key pair lightrag uses), the model, and the per-Repository opt-out.
// The key is omitted when no OpenAI Secret is configured so the ingester falls
// back to AST-only ingest. SEMANTIC_MODEL defaults to gpt-4o-mini.
func semanticEnv(repo *tataradevv1alpha1.Repository, cfg Config) []corev1.EnvVar {
	model := cfg.SemanticModel
	if model == "" {
		model = "gpt-4o-mini"
	}
	env := []corev1.EnvVar{
		{Name: "SEMANTIC_MODEL", Value: model},
		{Name: "SEMANTIC_INGEST", Value: strconv.FormatBool(repo.Spec.SemanticIngest)},
	}
	if cfg.OpenAISecretName != "" {
		env = append(env, corev1.EnvVar{
			Name: "OPENAI_API_KEY",
			ValueFrom: &corev1.EnvVarSource{
				SecretKeyRef: &corev1.SecretKeySelector{
					LocalObjectReference: corev1.LocalObjectReference{Name: cfg.OpenAISecretName},
					Key:                  "LLM_BINDING_API_KEY",
				},
			},
		})
	}
	return env
}
```

Update the call site in `BuildJob` to pass `repo`:

```go
						}, semanticEnv(repo, cfg)...),
```

Add `"strconv"` to the import block:

```go
import (
	"fmt"
	"strconv"

	tataradevv1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/rand"
)
```

4. Run GREEN: `go test ./internal/ingest/... -count=1`
   Expected pass: `ok  github.com/szymonrychu/tatara-operator/internal/ingest` (the Task 5 `TestBuildJob_OpenAIKeyFromSecret` / `..._OmittedWhenSecretUnset` tests still pass because the OpenAI key block is unchanged).

5. Commit:

```
git add internal/ingest/job.go internal/ingest/job_test.go
git commit -m "feat(ingest): emit SEMANTIC_MODEL + SEMANTIC_INGEST on ingest Job"
```

---

## Task 7: Add `semanticIngest: true` to the deploy sample

### Files
- Modify: `/Users/szymonri/Documents/tatara/tatara-operator/deploy-samples/tatara-project.yaml`

### Steps

1. Write the failing test. There is no Go test harness for the sample YAML; the verification is a grep gate. Define the expected post-state: every `Repository` spec line carries `semanticIngest: true`. Run the RED check first.

2. Run RED: `grep -c 'semanticIngest: true' deploy-samples/tatara-project.yaml`
   Expected fail: prints `0` (no occurrences yet).

3. Minimal impl. In `/Users/szymonri/Documents/tatara/tatara-operator/deploy-samples/tatara-project.yaml`, add `semanticIngest: true` to each `Repository` spec inline map. Each of the six Repository `spec:` lines changes from:

```yaml
spec: {projectRef: tatara, url: "https://github.com/szymonrychu/tatara-memory", defaultBranch: main, reingestSchedule: "0 6 * * *"}
```

to:

```yaml
spec: {projectRef: tatara, url: "https://github.com/szymonrychu/tatara-memory", defaultBranch: main, semanticIngest: true, reingestSchedule: "0 6 * * *"}
```

Apply the identical insertion (`semanticIngest: true, ` before `reingestSchedule`) to all six Repository entries: `tatara-memory`, `tatara-cli`, `tatara-operator`, `tatara-chat`, `tatara-memory-repo-ingester`, `tatara-claude-code-wrapper`.

4. Run GREEN: `grep -c 'semanticIngest: true' deploy-samples/tatara-project.yaml`
   Expected pass: prints `6`.

5. Commit:

```
git add deploy-samples/tatara-project.yaml
git commit -m "docs(samples): set semanticIngest: true on sample repositories"
```

---

## Final full-suite verification (after all tasks)

Run the whole operator suite to confirm no regression across packages that construct `ingest.Config` or `Repository`:

```
make generate
make manifests
git diff --exit-code api/ charts/
go test ./... -count=1
```

Expected: `git diff --exit-code` exits 0 (generated artifacts already committed and current); `go test ./...` prints `ok` for every package, including `internal/controller` (which calls `ingest.BuildJob` via the reconciler and constructs `ingest.Config` in `repository_controller_test.go`) and `cmd/manager`.

### Critical Files for Implementation
- /Users/szymonri/Documents/tatara/tatara-operator/internal/ingest/job.go
- /Users/szymonri/Documents/tatara/tatara-operator/api/v1alpha1/repository_types.go
- /Users/szymonri/Documents/tatara/tatara-operator/internal/config/config.go
- /Users/szymonri/Documents/tatara/tatara-operator/cmd/manager/wire.go
- /Users/szymonri/Documents/tatara/tatara-operator/internal/memory/lightrag.go