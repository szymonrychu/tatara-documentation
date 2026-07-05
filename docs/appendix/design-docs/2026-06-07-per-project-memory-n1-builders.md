# Per-Project Memory N1 (`internal/memory` builder package + config) Implementation Plan

> For agentic workers: execute tasks top to bottom. Each task is a closed
> TDD loop: write the failing test, run it and SEE it fail for the stated
> reason, write the minimal implementation, run it and SEE it pass, then
> commit. Do not batch tasks. Do not skip the "see it fail" step. All paths
> are absolute or relative to the repo root
> `/Users/szymonri/Documents/tatara/tatara-operator`. Obey the pin set
> (`docs/superpowers/plans/_per-project-memory-shared-contracts.md`) verbatim:
> names, signatures, config keys, labels, and the `Endpoint` format are
> contracts that N2-N4 depend on. Where this plan and the chart differ, the
> pin set wins; the chart is only the source for env/wiring values.

## Goal

Land the pure, unit-tested foundation of per-Project memory provisioning in
the deployed `tatara-operator` repo:

1. CRD: `Project.spec.memory` (`*MemorySpec`) and `Project.status.memory`
   (`*MemoryStatus`); deepcopy + CRD manifests regenerated.
2. cnpg API types dependency (`github.com/cloudnative-pg/cloudnative-pg/api/v1`,
   alias `cnpgv1`) added to `go.mod` and registered in the operator scheme.
3. `internal/config`: add `MemoryImage`, `LightragImage`, `Neo4jImage`,
   `OpenAISecretName`; remove `MemoryBaseURL`.
4. `internal/memory`: pure builder functions producing the full per-Project
   stack (`mem-<proj>-*` family), each owner-ref'd to the Project and carrying
   the pin-set labels, with env/wiring ported faithfully from the
   `tatara-memory` chart.

No reconciler logic, no client calls, no RBAC, no infra changes - those are
N2-N4. This milestone is pure functions + types + config + scheme + go.mod.

## Architecture

`internal/memory` is a leaf package of pure builders. Each function takes a
`*v1alpha1.Project` plus a `memory.Config` (operator-level images/OIDC/ns) and
returns one or more typed Kubernetes API objects with:

- name from `Names(project)` (the `mem-<proj>-*` family),
- `metav1.ObjectMeta.Labels` = the four pin-set labels,
- a single controller `OwnerReference` to the Project (Controller=true,
  BlockOwnerDeletion=true), so N2's reconciler gets cascade delete for free,
- env/volumes/ports ported from the chart templates, but rewired to
  per-Project resource names (no shared `tatara-memory-*` names).

The cnpg `Cluster` is the only third-party type; everything else is core
`appsv1`/`corev1`. cnpg's controller (already running in the cluster) reconciles
the `Cluster` into the `mem-<proj>-pg-rw` Service and `mem-<proj>-pg-app` Secret
(key `uri`), which lightrag and memory consume. The neo4j password is a Secret
the operator generates (N2 supplies the random value; N1's builder takes it as
an argument so it stays pure).

`memory.Config` is the only new config surface the builders read; N1 wires the
operator `config.Config` -> `memory.Config` mapping only in a small constructor
test, not in the manager (the manager wiring is N2/N3 scope, but the four new
config fields and the removal of `MemoryBaseURL` land here because N3 removes
its last consumer and the field must not dangle).

Defaults (pgInstances 1, pgStorage 10Gi, neo4jStorage 10Gi) are applied inside
the builders by reading `p.Spec.Memory` and falling back when it is nil or a
field is zero/empty - NOT via kubebuilder defaults - so an empty `spec.memory`
(or absent) still provisions a complete stack. This matches the pin set.

## Tech Stack

- Go 1.26 (`go.mod` directive already `go 1.26.0`; do not change).
- controller-runtime v0.24.1, k8s.io/api v0.36.x (already present).
- controller-gen v0.18.0 via `make generate` / `make manifests`.
- New dep: `github.com/cloudnative-pg/cloudnative-pg/api/v1` (alias `cnpgv1`).
- Tests: stdlib `testing` + `testify` (`require`), matching the repo's existing
  style (`internal/ingest/job_test.go`, `internal/agent/pod_test.go`).
- Build/test commands: `go test ./...`, `go build ./...`, `make generate`,
  `make manifests`, `make tidy`.

---

## Task 1: Add cnpg API module to go.mod and register in scheme

cnpg `Cluster` is needed by the `PGCluster` builder (Task 8) and must be a known
type in the operator scheme so N2 can SSA it. Add the dependency and scheme
registration first so later tasks compile.

Version pick: the cluster Helm chart 0.6.1 does not pin an operator appVersion
(it only renders a `Cluster` CR), so the api module version is chosen for CRD
compatibility, not chart coupling. The `api/v1` package lives in the main
`github.com/cloudnative-pg/cloudnative-pg` module. Pin to **`v1.27.0`** as a
recent, widely-deployed stable whose `Cluster` schema matches the fields this
plan uses (`Instances`, `StorageConfiguration.Size`, `Bootstrap.InitDB`
{`Database`, `Owner`, `PostInitApplicationSQL`}). VERIFY before pinning: run
`kubectl -n tatara get deploy -l app.kubernetes.io/name=cloudnative-pg -o jsonpath='{.items[0].spec.template.spec.containers[0].image}'`
and, if it reports a different minor, pin the api module to that exact minor
instead and note the override in `MEMORY.md`. Record the final version in
`MEMORY.md` (one dated line: "cnpg api module pinned to vX.Y.Z, matches live
operator image").

Note (tech-debt watch, record in `MEMORY.md` if it materializes): the cnpg api
package is part of the main module and pulls a sizeable transitive dep set.
That is the accepted cost of using upstream types instead of hand-rolling the
`Cluster` struct (hand-rolling would violate hard rule 4). If `go mod tidy`
surfaces a version conflict with the existing k8s.io v0.36.x set, prefer the
newest cnpg api minor whose go.mod is compatible with k8s.io v0.36.x and note
the chosen version.

**Files**

- Modify: `go.mod`, `go.sum` (via `go get` / `make tidy`)
- Modify: `cmd/manager/main.go` (register `cnpgv1` in `newScheme`)
- Test: `cmd/manager/main_test.go` (assert cnpg `Cluster` is registered)

**Steps**

- [ ] Add the failing test. Append to `cmd/manager/main_test.go` (adjust the
  import block to include the cnpg alias and the schema package):

  ```go
  func TestNewScheme_RegistersCNPGCluster(t *testing.T) {
      s := newScheme()
      gvk := schema.GroupVersionKind{
          Group:   "postgresql.cnpg.io",
          Version: "v1",
          Kind:    "Cluster",
      }
      if !s.Recognizes(gvk) {
          t.Fatalf("scheme does not recognize cnpg Cluster %v", gvk)
      }
      obj, err := s.New(gvk)
      if err != nil {
          t.Fatalf("scheme.New(%v): %v", gvk, err)
      }
      if _, ok := obj.(*cnpgv1.Cluster); !ok {
          t.Fatalf("scheme returned %T, want *cnpgv1.Cluster", obj)
      }
  }
  ```

  Ensure these imports are present in `cmd/manager/main_test.go`:

  ```go
  cnpgv1 "github.com/cloudnative-pg/cloudnative-pg/api/v1"
  "k8s.io/apimachinery/pkg/runtime/schema"
  ```

- [ ] Pull the dependency and tidy:

  ```
  go get github.com/cloudnative-pg/cloudnative-pg/api/v1@v1.27.0
  make tidy
  ```

  (If a conflict appears, follow the version-pick note above, then re-run
  `make tidy`.)

- [ ] Run and expect FAIL:

  ```
  go test ./cmd/manager/ -run TestNewScheme_RegistersCNPGCluster
  ```

  Expected: FAIL - `scheme does not recognize cnpg Cluster` (the scheme has not
  registered cnpg types yet). The test must compile (the dep now resolves); it
  fails only on the assertion.

- [ ] Minimal implementation. In `cmd/manager/main.go`, add the cnpg import and
  register it in `newScheme`:

  Import block - add:

  ```go
  cnpgv1 "github.com/cloudnative-pg/cloudnative-pg/api/v1"
  ```

  `newScheme` becomes:

  ```go
  func newScheme() *runtime.Scheme {
      s := runtime.NewScheme()
      utilRuntimeMust(clientgoscheme.AddToScheme(s))
      utilRuntimeMust(apiv1alpha1.AddToScheme(s))
      utilRuntimeMust(cnpgv1.AddToScheme(s))
      return s
  }
  ```

- [ ] Run and expect PASS:

  ```
  go test ./cmd/manager/ -run TestNewScheme_RegistersCNPGCluster
  go build ./...
  ```

  Expected: both PASS / no errors.

- [ ] Commit:

  ```
  git add go.mod go.sum cmd/manager/main.go cmd/manager/main_test.go
  git commit -m "feat: add cnpg api dependency and register Cluster in operator scheme"
  ```

---

## Task 2: Add `MemorySpec` / `MemoryStatus` CRD types + deepcopy + manifests

Add the new types to the Project CRD exactly per the pin set, regenerate
deepcopy and CRD YAML. Defaults are applied in the builders (Task 3+), NOT as
kubebuilder defaults, so the fields stay plain optional.

**Files**

- Modify: `api/v1alpha1/project_types.go`
- Test: `api/v1alpha1/project_types_test.go`
- Generated (via make): `api/v1alpha1/zz_generated.deepcopy.go`,
  `charts/tatara-operator/crds/tatara.dev_projects.yaml`

**Steps**

- [ ] Add the failing test. Append to `api/v1alpha1/project_types_test.go`
  (ensure `metav1` and `reflect` are imported; the package is `v1alpha1`):

  ```go
  func TestProject_MemorySpecStatusDeepCopy(t *testing.T) {
      p := &Project{
          Spec: ProjectSpec{
              Memory: &MemorySpec{
                  PgInstances:  2,
                  PgStorage:    "20Gi",
                  Neo4jStorage: "5Gi",
              },
          },
          Status: ProjectStatus{
              Memory: &MemoryStatus{
                  Phase:    "Ready",
                  Endpoint: "http://mem-acme.tatara.svc:8080",
              },
          },
      }
      cp := p.DeepCopy()
      if cp.Spec.Memory == p.Spec.Memory {
          t.Fatal("spec.memory pointer not deep-copied")
      }
      if cp.Status.Memory == p.Status.Memory {
          t.Fatal("status.memory pointer not deep-copied")
      }
      if !reflect.DeepEqual(cp.Spec.Memory, p.Spec.Memory) {
          t.Fatalf("spec.memory mismatch: %+v vs %+v", cp.Spec.Memory, p.Spec.Memory)
      }
      if !reflect.DeepEqual(cp.Status.Memory, p.Status.Memory) {
          t.Fatalf("status.memory mismatch: %+v vs %+v", cp.Status.Memory, p.Status.Memory)
      }
      // Mutating the copy must not affect the original.
      cp.Spec.Memory.PgInstances = 9
      if p.Spec.Memory.PgInstances == 9 {
          t.Fatal("mutating copy mutated original (shallow copy)")
      }
  }

  func TestProject_MemoryNilSafe(t *testing.T) {
      p := &Project{}
      cp := p.DeepCopy()
      if cp.Spec.Memory != nil || cp.Status.Memory != nil {
          t.Fatal("nil memory must deep-copy to nil")
      }
  }
  ```

- [ ] Run and expect FAIL:

  ```
  go test ./api/v1alpha1/ -run 'TestProject_Memory'
  ```

  Expected: FAIL to COMPILE - `ProjectSpec` has no field `Memory`,
  `MemorySpec`/`MemoryStatus` undefined. (Compile failure is the expected
  "red".)

- [ ] Minimal implementation. Edit `api/v1alpha1/project_types.go`. Add the two
  types and the two fields:

  Add after `AgentSpec`:

  ```go
  // MemorySpec configures the per-Project memory stack footprint. All fields
  // are optional; defaults (pgInstances 1, pgStorage 10Gi, neo4jStorage 10Gi)
  // are applied by the internal/memory builders, not by kubebuilder, so an
  // empty (or absent) spec.memory still provisions a complete stack.
  type MemorySpec struct {
      // +optional
      PgInstances int `json:"pgInstances,omitempty"`
      // +optional
      PgStorage string `json:"pgStorage,omitempty"`
      // +optional
      Neo4jStorage string `json:"neo4jStorage,omitempty"`
  }

  // MemoryStatus reports the observed state of the per-Project memory stack.
  // Endpoint is the canonical in-cluster URL every other component reads.
  type MemoryStatus struct {
      // +optional
      Phase string `json:"phase,omitempty"`
      // +optional
      Endpoint string `json:"endpoint,omitempty"`
  }
  ```

  In `ProjectSpec`, add (keep existing fields):

  ```go
      // +optional
      Memory *MemorySpec `json:"memory,omitempty"`
  ```

  In `ProjectStatus`, add (keep existing fields):

  ```go
      // +optional
      Memory *MemoryStatus `json:"memory,omitempty"`
  ```

- [ ] Regenerate deepcopy and CRDs:

  ```
  make generate
  make manifests
  ```

  Expected: `zz_generated.deepcopy.go` gains `DeepCopy`/`DeepCopyInto` for
  `MemorySpec` and `MemoryStatus` plus updated `ProjectSpec`/`ProjectStatus`
  copying the pointers; `charts/tatara-operator/crds/tatara.dev_projects.yaml`
  gains `spec.memory.{pgInstances,pgStorage,neo4jStorage}` and
  `status.memory.{phase,endpoint}`.

- [ ] Run and expect PASS:

  ```
  go test ./api/v1alpha1/ -run 'TestProject_Memory'
  go build ./...
  ```

  Expected: PASS.

- [ ] Verify the CRD manifest actually changed (evidence before claiming done):

  ```
  git diff --stat charts/tatara-operator/crds/tatara.dev_projects.yaml
  grep -n 'pgInstances\|neo4jStorage\|endpoint' charts/tatara-operator/crds/tatara.dev_projects.yaml
  ```

  Expected: the grep returns the new keys.

- [ ] Commit:

  ```
  git add api/v1alpha1/project_types.go api/v1alpha1/project_types_test.go \
          api/v1alpha1/zz_generated.deepcopy.go \
          charts/tatara-operator/crds/tatara.dev_projects.yaml
  git commit -m "feat: add Project spec.memory and status.memory CRD fields"
  ```

---

## Task 3: Config - add memory image/secret fields, remove MEMORY_BASE_URL

Add the four new config fields and remove the now-orphaned `MemoryBaseURL`.
N3 removes its last runtime consumer; in N1 the only existing reader is
`cmd/manager/wire.go` (`ingestConfigFromConfig`), which is patched here to stop
referencing the removed field so the tree keeps compiling.

**Files**

- Modify: `internal/config/config.go`
- Test: `internal/config/config_test.go`
- Modify (compile fix): `cmd/manager/wire.go`, and `internal/ingest/job.go` /
  its `Config` only if needed to keep `go build ./...` green (see step).

**Steps**

- [ ] Update the test first. Edit `internal/config/config_test.go`:

  In the `env` map in `TestLoad`, REMOVE the line:

  ```go
      "MEMORY_BASE_URL":             "http://tatara-memory:8080",
  ```

  and ADD:

  ```go
      "MEMORY_IMAGE":                "harbor/tatara-memory:0.2.0",
      "LIGHTRAG_IMAGE":              "ghcr.io/hkuds/lightrag:v1.4.16",
      "NEO4J_IMAGE":                 "neo4j:5-community",
      "OPENAI_SECRET_NAME":          "tatara-openai",
  ```

  In the `tests` table in `TestLoad`, REMOVE:

  ```go
      {"MemoryBaseURL", cfg.MemoryBaseURL, "http://tatara-memory:8080"},
  ```

  and ADD:

  ```go
      {"MemoryImage", cfg.MemoryImage, "harbor/tatara-memory:0.2.0"},
      {"LightragImage", cfg.LightragImage, "ghcr.io/hkuds/lightrag:v1.4.16"},
      {"Neo4jImage", cfg.Neo4jImage, "neo4j:5-community"},
      {"OpenAISecretName", cfg.OpenAISecretName, "tatara-openai"},
  ```

- [ ] Run and expect FAIL:

  ```
  go test ./internal/config/
  ```

  Expected: FAIL to COMPILE - `cfg.MemoryImage` etc. undefined, and
  `cfg.MemoryBaseURL` still referenced (will compile but the removed env line
  means the assertion would change). The compile failure on the new fields is
  the red.

- [ ] Minimal implementation. Edit `internal/config/config.go`:

  In the `Config` struct, REMOVE `MemoryBaseURL string` and ADD:

  ```go
      MemoryImage      string
      LightragImage    string
      Neo4jImage       string
      OpenAISecretName string
  ```

  In `Load()`, REMOVE:

  ```go
      MemoryBaseURL:            os.Getenv("MEMORY_BASE_URL"),
  ```

  and ADD:

  ```go
      MemoryImage:              os.Getenv("MEMORY_IMAGE"),
      LightragImage:            os.Getenv("LIGHTRAG_IMAGE"),
      Neo4jImage:               os.Getenv("NEO4J_IMAGE"),
      OpenAISecretName:         os.Getenv("OPENAI_SECRET_NAME"),
  ```

- [ ] Fix the compile break in the manager wiring. `cmd/manager/wire.go`
  `ingestConfigFromConfig` sets `MemoryBaseURL: cfg.MemoryBaseURL` into
  `ingest.Config`. N1 must keep `go build ./...` green without doing N3's
  re-wiring. Minimal change: drop the `MemoryBaseURL: cfg.MemoryBaseURL` line
  from the `ingest.Config{...}` literal in `ingestConfigFromConfig` (the ingest
  `Config.MemoryBaseURL` field stays; it is repointed to the per-Project
  endpoint in N3, and an empty value here is harmless because no ingest Job is
  built in N1). Leave a one-line comment:

  ```go
      // MemoryBaseURL is set per-Project in N3 (project.Status.Memory.Endpoint);
      // the global value was removed from operator config.
  ```

  Do NOT delete `ingest.Config.MemoryBaseURL` itself (N3 owns that). Confirm no
  other reference to `cfg.MemoryBaseURL` remains:

  ```
  grep -rn 'MemoryBaseURL' --include='*.go' . | grep -v '_test.go'
  ```

  Expected: only `internal/ingest/job.go` (the `ingest.Config` field + its use
  in `BuildJob`) and possibly the dropped-comment line; NO `config.Config`
  reference.

- [ ] Run and expect PASS:

  ```
  go test ./internal/config/
  go build ./...
  ```

  Expected: PASS / clean build.

- [ ] Commit:

  ```
  git add internal/config/config.go internal/config/config_test.go cmd/manager/wire.go
  git commit -m "feat: add memory image/secret config fields, remove MEMORY_BASE_URL"
  ```

---

## Task 4: `internal/memory` package - `Names`, `Endpoint`, `Config`, labels/ownerref helpers

The shared primitives every builder depends on. These are the contracts N2-N4
consume, so spell them out exactly.

**Files**

- Create: `internal/memory/memory.go`
- Test: `internal/memory/memory_test.go`

**Steps**

- [ ] Write the failing test. Create `internal/memory/memory_test.go`:

  ```go
  package memory_test

  import (
      "testing"

      "github.com/stretchr/testify/require"
      tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
      "github.com/szymonrychu/tatara-operator/internal/memory"
      metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
  )

  func testProject(name string) *tatarav1alpha1.Project {
      return &tatarav1alpha1.Project{
          ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "tatara", UID: "uid-123"},
      }
  }

  func TestNames(t *testing.T) {
      n := memory.NamesFor("acme")
      require.Equal(t, "mem-acme-pg", n.PGCluster)
      require.Equal(t, "mem-acme-pg-rw", n.PGService)
      require.Equal(t, "mem-acme-pg-app", n.PGAppSecret)
      require.Equal(t, "mem-acme-neo4j", n.Neo4j)
      require.Equal(t, "mem-acme-neo4j", n.Neo4jSecret)
      require.Equal(t, "mem-acme-lightrag", n.Lightrag)
      require.Equal(t, "mem-acme-lightrag-data", n.LightragPVC)
      require.Equal(t, "mem-acme", n.Memory)
  }

  func TestEndpoint(t *testing.T) {
      require.Equal(t, "http://mem-acme.tatara.svc:8080", memory.Endpoint("acme", "tatara"))
      require.Equal(t, "http://mem-foo.other.svc:8080", memory.Endpoint("foo", "other"))
  }
  ```

- [ ] Run and expect FAIL:

  ```
  go test ./internal/memory/
  ```

  Expected: FAIL to COMPILE - package `internal/memory` does not exist.

- [ ] Minimal implementation. Create `internal/memory/memory.go`:

  ```go
  // Package memory holds pure builder functions that produce the per-Project
  // memory stack (cnpg postgres, neo4j, lightrag, tatara-memory) as native
  // Kubernetes objects. Every object is named from Names, carries the pin-set
  // labels, and is owner-referenced to the Project for cascade delete. No
  // function performs any client call; callers (the ProjectReconciler, N2)
  // server-side-apply the returned objects.
  package memory

  import (
      "fmt"

      tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
      metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
  )

  // Defaults for an empty or partial spec.memory. Applied in the builders, not
  // as kubebuilder defaults, so an absent spec.memory still provisions.
  const (
      defaultPgInstances  = 1
      defaultPgStorage    = "10Gi"
      defaultNeo4jStorage = "10Gi"
  )

  // Config is the operator-level (non-per-Project) input the builders need.
  // The manager maps config.Config into this in N2/N3.
  type Config struct {
      Namespace        string
      MemoryImage      string
      LightragImage    string
      Neo4jImage       string
      OpenAISecretName string
      OIDCIssuer       string
      OIDCAudience     string
  }

  // Names holds every object name in the mem-<proj>-* family for one Project.
  type Names struct {
      PGCluster   string // cnpg Cluster
      PGService   string // cnpg-managed read-write Service
      PGAppSecret string // cnpg-managed app Secret (key "uri")
      Neo4j       string // StatefulSet + Service
      Neo4jSecret string // generated password Secret
      Lightrag    string // Deployment + Service
      LightragPVC string // lightrag data PVC
      Memory      string // tatara-memory Deployment + Service + ConfigMap + Secret
  }

  // NamesFor returns the name family for a project.
  func NamesFor(project string) Names {
      p := "mem-" + project
      return Names{
          PGCluster:   p + "-pg",
          PGService:   p + "-pg-rw",
          PGAppSecret: p + "-pg-app",
          Neo4j:       p + "-neo4j",
          Neo4jSecret: p + "-neo4j",
          Lightrag:    p + "-lightrag",
          LightragPVC: p + "-lightrag-data",
          Memory:      p,
      }
  }

  // Endpoint is the canonical in-cluster URL of a Project's tatara-memory
  // service. This is the value the reconciler writes to status.memory.endpoint
  // and every other component reads.
  func Endpoint(project, namespace string) string {
      return fmt.Sprintf("http://mem-%s.%s.svc:8080", project, namespace)
  }

  // labels returns the four pin-set labels carried by every object.
  func labels(project string) map[string]string {
      return map[string]string{
          "app.kubernetes.io/name":     "tatara-memory",
          "app.kubernetes.io/instance": "mem-" + project,
          "tatara.dev/project":         project,
      }
  }

  // ownerRef returns the single controller OwnerReference to the Project.
  func ownerRef(p *tatarav1alpha1.Project) metav1.OwnerReference {
      t := true
      return metav1.OwnerReference{
          APIVersion:         tatarav1alpha1.GroupVersion.String(),
          Kind:               "Project",
          Name:               p.Name,
          UID:                p.UID,
          Controller:         &t,
          BlockOwnerDeletion: &t,
      }
  }

  // objectMeta builds the shared ObjectMeta for an object named name owned by p.
  func objectMeta(p *tatarav1alpha1.Project, cfg Config, name string) metav1.ObjectMeta {
      return metav1.ObjectMeta{
          Name:            name,
          Namespace:       cfg.Namespace,
          Labels:          labels(p.Name),
          OwnerReferences: []metav1.OwnerReference{ownerRef(p)},
      }
  }

  // pgInstances resolves the postgres instance count from spec, defaulting.
  func pgInstances(p *tatarav1alpha1.Project) int {
      if p.Spec.Memory != nil && p.Spec.Memory.PgInstances > 0 {
          return p.Spec.Memory.PgInstances
      }
      return defaultPgInstances
  }

  // pgStorage resolves the postgres storage size from spec, defaulting.
  func pgStorage(p *tatarav1alpha1.Project) string {
      if p.Spec.Memory != nil && p.Spec.Memory.PgStorage != "" {
          return p.Spec.Memory.PgStorage
      }
      return defaultPgStorage
  }

  // neo4jStorage resolves the neo4j storage size from spec, defaulting.
  func neo4jStorage(p *tatarav1alpha1.Project) string {
      if p.Spec.Memory != nil && p.Spec.Memory.Neo4jStorage != "" {
          return p.Spec.Memory.Neo4jStorage
      }
      return defaultNeo4jStorage
  }
  ```

  Note on `Names` vs `NamesFor`: the pin set writes the function as
  `Names(project string) Names`, but `Names` is also the struct type, which Go
  forbids (a func and a type cannot share a name in the same package). Resolve
  by naming the function `NamesFor` and keeping the struct `Names`; update the
  test above accordingly (it already calls `memory.NamesFor(...)` - change those
  calls to `memory.NamesFor(...)`). Record this one-line deviation from the pin
  set in `MEMORY.md`: "pin set's `Names(project)` -> `NamesFor(project)`
  because `Names` is the returned struct type; struct name unchanged."

  ACTION: in `internal/memory/memory_test.go`, replace `memory.NamesFor("acme")`
  with `memory.NamesFor("acme")`.

- [ ] Run and expect PASS:

  ```
  go test ./internal/memory/
  ```

  Expected: PASS.

- [ ] Commit:

  ```
  git add internal/memory/memory.go internal/memory/memory_test.go
  git commit -m "feat: internal/memory primitives (NamesFor, Endpoint, Config, meta helpers)"
  ```

---

## Task 5: `PGCluster` builder

cnpg `Cluster` `mem-<proj>-pg`: instances + storage from spec (defaults 1 /
10Gi), `bootstrap.initdb` db/owner `tatara_memory`, `postInitApplicationSQL`
installing the `vector` extension. Ported from `values.yaml` `postgres.cluster`
block.

**Files**

- Create: `internal/memory/pg.go`
- Test: `internal/memory/pg_test.go`

**Steps**

- [ ] Write the failing test. Create `internal/memory/pg_test.go`:

  ```go
  package memory_test

  import (
      "testing"

      "github.com/stretchr/testify/require"
      tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
      "github.com/szymonrychu/tatara-operator/internal/memory"
  )

  func testCfg() memory.Config {
      return memory.Config{
          Namespace:        "tatara",
          MemoryImage:      "harbor/tatara-memory:0.2.0",
          LightragImage:    "ghcr.io/hkuds/lightrag:v1.4.16",
          Neo4jImage:       "neo4j:5-community",
          OpenAISecretName: "tatara-openai",
          OIDCIssuer:       "https://auth.example/realms/master",
          OIDCAudience:     "tatara-memory",
      }
  }

  func TestPGCluster_DefaultsAndShape(t *testing.T) {
      p := testProject("acme")
      c := memory.PGCluster(p, testCfg())

      require.Equal(t, "mem-acme-pg", c.Name)
      require.Equal(t, "tatara", c.Namespace)
      require.Equal(t, "tatara-memory", c.Labels["app.kubernetes.io/name"])
      require.Equal(t, "acme", c.Labels["tatara.dev/project"])

      require.Len(t, c.OwnerReferences, 1)
      require.Equal(t, "Project", c.OwnerReferences[0].Kind)
      require.Equal(t, "acme", c.OwnerReferences[0].Name)
      require.NotNil(t, c.OwnerReferences[0].Controller)
      require.True(t, *c.OwnerReferences[0].Controller)

      require.Equal(t, 1, c.Spec.Instances)
      require.Equal(t, "10Gi", c.Spec.StorageConfiguration.Size)

      require.NotNil(t, c.Spec.Bootstrap)
      require.NotNil(t, c.Spec.Bootstrap.InitDB)
      require.Equal(t, "tatara_memory", c.Spec.Bootstrap.InitDB.Database)
      require.Equal(t, "tatara_memory", c.Spec.Bootstrap.InitDB.Owner)
      require.Contains(t, c.Spec.Bootstrap.InitDB.PostInitApplicationSQL,
          "CREATE EXTENSION IF NOT EXISTS vector")
  }

  func TestPGCluster_SpecOverrides(t *testing.T) {
      p := testProject("acme")
      p.Spec.Memory = &tatarav1alpha1.MemorySpec{PgInstances: 3, PgStorage: "50Gi"}
      c := memory.PGCluster(p, testCfg())
      require.Equal(t, 3, c.Spec.Instances)
      require.Equal(t, "50Gi", c.Spec.StorageConfiguration.Size)
  }
  ```

- [ ] Run and expect FAIL:

  ```
  go test ./internal/memory/ -run TestPGCluster
  ```

  Expected: FAIL to COMPILE - `memory.PGCluster` undefined.

- [ ] Minimal implementation. Create `internal/memory/pg.go`. VERIFY the cnpg
  field names against the pinned module before writing (the struct paths below
  are correct for cnpg api v1.27.x: `Cluster{TypeMeta, ObjectMeta, Spec}`,
  `ClusterSpec{Instances int, StorageConfiguration StorageConfiguration,
  Bootstrap *BootstrapConfiguration}`, `StorageConfiguration{Size string}`,
  `BootstrapConfiguration{InitDB *BootstrapInitDB}`,
  `BootstrapInitDB{Database, Owner string, PostInitApplicationSQL []string}`).
  If a field name differs in the pinned version, adapt and note it in
  `MEMORY.md`:

  ```go
  package memory

  import (
      cnpgv1 "github.com/cloudnative-pg/cloudnative-pg/api/v1"
      tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
      metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
  )

  // PGCluster builds the per-Project cnpg Cluster. cnpg's controller derives the
  // mem-<proj>-pg-rw Service and the mem-<proj>-pg-app Secret (key "uri") that
  // lightrag and tatara-memory consume. The vector extension is installed via
  // postInitApplicationSQL on the tatara_memory database for lightrag's
  // PGVectorStorage.
  func PGCluster(p *tatarav1alpha1.Project, cfg Config) *cnpgv1.Cluster {
      n := NamesFor(p.Name)
      return &cnpgv1.Cluster{
          TypeMeta: metav1.TypeMeta{
              APIVersion: cnpgv1.GroupVersion.String(),
              Kind:       "Cluster",
          },
          ObjectMeta: objectMeta(p, cfg, n.PGCluster),
          Spec: cnpgv1.ClusterSpec{
              Instances: pgInstances(p),
              StorageConfiguration: cnpgv1.StorageConfiguration{
                  Size: pgStorage(p),
              },
              Bootstrap: &cnpgv1.BootstrapConfiguration{
                  InitDB: &cnpgv1.BootstrapInitDB{
                      Database: "tatara_memory",
                      Owner:    "tatara_memory",
                      PostInitApplicationSQL: []string{
                          "CREATE EXTENSION IF NOT EXISTS vector",
                      },
                  },
              },
          },
      }
  }
  ```

- [ ] Run and expect PASS:

  ```
  go test ./internal/memory/ -run TestPGCluster
  ```

  Expected: PASS.

- [ ] Commit:

  ```
  git add internal/memory/pg.go internal/memory/pg_test.go
  git commit -m "feat: internal/memory PGCluster builder (cnpg, vector extension)"
  ```

---

## Task 6: `Neo4jPasswordSecret` builder

The generated neo4j password Secret `mem-<proj>-neo4j`. N2 generates the random
password and guards on existence; the N1 builder takes the password as an
argument so it stays pure. Carries both `password` and `NEO4J_AUTH` keys (the
StatefulSet reads `NEO4J_AUTH=neo4j/<password>`; lightrag reads the raw
`password` mapped to `NEO4J_PASSWORD`), per the pin set naming line 33.

**Files**

- Create: `internal/memory/secrets.go`
- Test: `internal/memory/secrets_test.go`

**Steps**

- [ ] Write the failing test. Create `internal/memory/secrets_test.go`:

  ```go
  package memory_test

  import (
      "testing"

      "github.com/stretchr/testify/require"
      "github.com/szymonrychu/tatara-operator/internal/memory"
  )

  func TestNeo4jPasswordSecret(t *testing.T) {
      p := testProject("acme")
      s := memory.Neo4jPasswordSecret(p, testCfg(), "s3cret")

      require.Equal(t, "mem-acme-neo4j", s.Name)
      require.Equal(t, "tatara", s.Namespace)
      require.Equal(t, "tatara-memory", s.Labels["app.kubernetes.io/name"])
      require.Len(t, s.OwnerReferences, 1)
      require.True(t, *s.OwnerReferences[0].Controller)

      require.Equal(t, "s3cret", s.StringData["password"])
      require.Equal(t, "neo4j/s3cret", s.StringData["NEO4J_AUTH"])
  }
  ```

- [ ] Run and expect FAIL:

  ```
  go test ./internal/memory/ -run TestNeo4jPasswordSecret
  ```

  Expected: FAIL to COMPILE - `memory.Neo4jPasswordSecret` undefined.

- [ ] Minimal implementation. Create `internal/memory/secrets.go`:

  ```go
  package memory

  import (
      tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
      corev1 "k8s.io/api/core/v1"
      metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
  )

  // Neo4jPasswordSecret builds the generated neo4j password Secret. The caller
  // (ProjectReconciler, N2) generates password once and guards on existence;
  // this builder is pure. Key "password" feeds lightrag's NEO4J_PASSWORD; key
  // "NEO4J_AUTH" (neo4j/<password>) feeds the neo4j StatefulSet.
  func Neo4jPasswordSecret(p *tatarav1alpha1.Project, cfg Config, password string) *corev1.Secret {
      n := NamesFor(p.Name)
      return &corev1.Secret{
          TypeMeta:   metav1.TypeMeta{APIVersion: "v1", Kind: "Secret"},
          ObjectMeta: objectMeta(p, cfg, n.Neo4jSecret),
          Type:       corev1.SecretTypeOpaque,
          StringData: map[string]string{
              "password":   password,
              "NEO4J_AUTH": "neo4j/" + password,
          },
      }
  }
  ```

- [ ] Run and expect PASS:

  ```
  go test ./internal/memory/ -run TestNeo4jPasswordSecret
  ```

  Expected: PASS.

- [ ] Commit:

  ```
  git add internal/memory/secrets.go internal/memory/secrets_test.go
  git commit -m "feat: internal/memory Neo4jPasswordSecret builder"
  ```

---

## Task 7: `Neo4jStatefulSet` + `Neo4jService` builders

Single-node community neo4j as a native StatefulSet (NOT the upstream chart).
`NEO4J_AUTH` from the generated Secret key `NEO4J_AUTH`, bolt 7687, http 7474,
data PVC `/data` sized from `neo4jStorage`. Service exposes both ports.

**Files**

- Create: `internal/memory/neo4j.go`
- Test: `internal/memory/neo4j_test.go`

**Steps**

- [ ] Write the failing test. Create `internal/memory/neo4j_test.go`:

  ```go
  package memory_test

  import (
      "testing"

      "github.com/stretchr/testify/require"
      tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
      "github.com/szymonrychu/tatara-operator/internal/memory"
      corev1 "k8s.io/api/core/v1"
  )

  func envByName(env []corev1.EnvVar, name string) (corev1.EnvVar, bool) {
      for _, e := range env {
          if e.Name == name {
              return e, true
          }
      }
      return corev1.EnvVar{}, false
  }

  func TestNeo4jStatefulSet(t *testing.T) {
      p := testProject("acme")
      ss := memory.Neo4jStatefulSet(p, testCfg())

      require.Equal(t, "mem-acme-neo4j", ss.Name)
      require.Equal(t, "tatara", ss.Namespace)
      require.Equal(t, "mem-acme-neo4j", ss.Spec.ServiceName)
      require.EqualValues(t, 1, *ss.Spec.Replicas)
      require.Len(t, ss.OwnerReferences, 1)
      require.True(t, *ss.OwnerReferences[0].Controller)

      c := ss.Spec.Template.Spec.Containers[0]
      require.Equal(t, "neo4j:5-community", c.Image)

      // NEO4J_AUTH from the generated secret.
      auth, ok := envByName(c.Env, "NEO4J_AUTH")
      require.True(t, ok)
      require.NotNil(t, auth.ValueFrom)
      require.NotNil(t, auth.ValueFrom.SecretKeyRef)
      require.Equal(t, "mem-acme-neo4j", auth.ValueFrom.SecretKeyRef.Name)
      require.Equal(t, "NEO4J_AUTH", auth.ValueFrom.SecretKeyRef.Key)

      // Ports.
      ports := map[string]int32{}
      for _, pt := range c.Ports {
          ports[pt.Name] = pt.ContainerPort
      }
      require.Equal(t, int32(7687), ports["bolt"])
      require.Equal(t, int32(7474), ports["http"])

      // Data volume claim sized from default.
      require.Len(t, ss.Spec.VolumeClaimTemplates, 1)
      vct := ss.Spec.VolumeClaimTemplates[0]
      require.Equal(t, "10Gi", vct.Spec.Resources.Requests.Storage().String())

      // /data mount present.
      var mounted bool
      for _, m := range c.VolumeMounts {
          if m.MountPath == "/data" {
              mounted = true
          }
      }
      require.True(t, mounted)
  }

  func TestNeo4jStatefulSet_StorageOverride(t *testing.T) {
      p := testProject("acme")
      p.Spec.Memory = &tatarav1alpha1.MemorySpec{Neo4jStorage: "25Gi"}
      ss := memory.Neo4jStatefulSet(p, testCfg())
      require.Equal(t, "25Gi",
          ss.Spec.VolumeClaimTemplates[0].Spec.Resources.Requests.Storage().String())
  }

  func TestNeo4jService(t *testing.T) {
      p := testProject("acme")
      svc := memory.Neo4jService(p, testCfg())
      require.Equal(t, "mem-acme-neo4j", svc.Name)
      require.Len(t, svc.OwnerReferences, 1)
      ports := map[string]int32{}
      for _, pt := range svc.Spec.Ports {
          ports[pt.Name] = pt.Port
      }
      require.Equal(t, int32(7687), ports["bolt"])
      require.Equal(t, int32(7474), ports["http"])
      require.Equal(t, "mem-acme", svc.Spec.Selector["app.kubernetes.io/instance"])
  }
  ```

- [ ] Run and expect FAIL:

  ```
  go test ./internal/memory/ -run 'TestNeo4j(StatefulSet|Service)'
  ```

  Expected: FAIL to COMPILE - `memory.Neo4jStatefulSet` / `memory.Neo4jService`
  undefined.

- [ ] Minimal implementation. Create `internal/memory/neo4j.go`:

  ```go
  package memory

  import (
      tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
      appsv1 "k8s.io/api/apps/v1"
      corev1 "k8s.io/api/core/v1"
      metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
      "k8s.io/apimachinery/pkg/api/resource"
      "k8s.io/apimachinery/pkg/util/intstr"
  )

  // selectorLabels are the subset used as pod/Service selectors (immutable on a
  // StatefulSet, so kept minimal and stable).
  func selectorLabels(project, component string) map[string]string {
      return map[string]string{
          "app.kubernetes.io/instance":  "mem-" + project,
          "app.kubernetes.io/component": component,
      }
  }

  // Neo4jStatefulSet builds the single-node community neo4j StatefulSet. It is a
  // native build, NOT the upstream neo4j Helm chart: one replica, NEO4J_AUTH
  // from the generated Secret, bolt 7687 / http 7474, data PVC at /data.
  func Neo4jStatefulSet(p *tatarav1alpha1.Project, cfg Config) *appsv1.StatefulSet {
      n := NamesFor(p.Name)
      replicas := int32(1)
      sel := selectorLabels(p.Name, "neo4j")
      podLabels := labels(p.Name)
      podLabels["app.kubernetes.io/component"] = "neo4j"

      return &appsv1.StatefulSet{
          TypeMeta:   metav1.TypeMeta{APIVersion: "apps/v1", Kind: "StatefulSet"},
          ObjectMeta: objectMeta(p, cfg, n.Neo4j),
          Spec: appsv1.StatefulSetSpec{
              ServiceName: n.Neo4j,
              Replicas:    &replicas,
              Selector:    &metav1.LabelSelector{MatchLabels: sel},
              Template: corev1.PodTemplateSpec{
                  ObjectMeta: metav1.ObjectMeta{Labels: podLabels},
                  Spec: corev1.PodSpec{
                      Containers: []corev1.Container{{
                          Name:  "neo4j",
                          Image: cfg.Neo4jImage,
                          Env: []corev1.EnvVar{
                              {
                                  Name: "NEO4J_AUTH",
                                  ValueFrom: &corev1.EnvVarSource{
                                      SecretKeyRef: &corev1.SecretKeySelector{
                                          LocalObjectReference: corev1.LocalObjectReference{Name: n.Neo4jSecret},
                                          Key:                  "NEO4J_AUTH",
                                      },
                                  },
                              },
                          },
                          Ports: []corev1.ContainerPort{
                              {Name: "bolt", ContainerPort: 7687, Protocol: corev1.ProtocolTCP},
                              {Name: "http", ContainerPort: 7474, Protocol: corev1.ProtocolTCP},
                          },
                          ReadinessProbe: &corev1.Probe{
                              ProbeHandler: corev1.ProbeHandler{
                                  TCPSocket: &corev1.TCPSocketAction{Port: intstr.FromString("bolt")},
                              },
                              PeriodSeconds: 10,
                          },
                          VolumeMounts: []corev1.VolumeMount{
                              {Name: "data", MountPath: "/data"},
                          },
                      }},
                  },
              },
              VolumeClaimTemplates: []corev1.PersistentVolumeClaim{{
                  ObjectMeta: metav1.ObjectMeta{Name: "data"},
                  Spec: corev1.PersistentVolumeClaimSpec{
                      AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
                      Resources: corev1.VolumeResourceRequirements{
                          Requests: corev1.ResourceList{
                              corev1.ResourceStorage: resource.MustParse(neo4jStorage(p)),
                          },
                      },
                  },
              }},
          },
      }
  }

  // Neo4jService exposes bolt and http for the neo4j StatefulSet (ClusterIP).
  // lightrag connects to bolt://mem-<proj>-neo4j:7687.
  func Neo4jService(p *tatarav1alpha1.Project, cfg Config) *corev1.Service {
      n := NamesFor(p.Name)
      return &corev1.Service{
          TypeMeta:   metav1.TypeMeta{APIVersion: "v1", Kind: "Service"},
          ObjectMeta: objectMeta(p, cfg, n.Neo4j),
          Spec: corev1.ServiceSpec{
              Type:     corev1.ServiceTypeClusterIP,
              Selector: selectorLabels(p.Name, "neo4j"),
              Ports: []corev1.ServicePort{
                  {Name: "bolt", Port: 7687, TargetPort: intstr.FromString("bolt"), Protocol: corev1.ProtocolTCP},
                  {Name: "http", Port: 7474, TargetPort: intstr.FromString("http"), Protocol: corev1.ProtocolTCP},
              },
          },
      }
  }
  ```

  Note: the `TestNeo4jService` selector assertion checks
  `app.kubernetes.io/instance == "mem-acme"`; `selectorLabels` sets exactly
  that key, so the assertion holds. (The `component` key is also present; the
  test only asserts the instance key.)

- [ ] Run and expect PASS:

  ```
  go test ./internal/memory/ -run 'TestNeo4j'
  ```

  Expected: PASS.

- [ ] Commit:

  ```
  git add internal/memory/neo4j.go internal/memory/neo4j_test.go
  git commit -m "feat: internal/memory neo4j single-node StatefulSet + Service"
  ```

---

## Task 8: `LightragDeployment` + `LightragService` + `LightragPVC` builders

Port from `charts/.../lightrag/templates/{deployment,configmap,service,pvc}.yaml`
and `lightrag/_helpers.tpl` `configKeys`. Rewire to per-Project resources:
postgres host `mem-<proj>-pg-rw`, db/user `tatara_memory`, postgres password
from the cnpg app Secret `mem-<proj>-pg-app` key `password`; neo4j
`bolt://mem-<proj>-neo4j:7687`, neo4j password from `mem-<proj>-neo4j` key
`password`; OpenAI key `LLM_BINDING_API_KEY` from `cfg.OpenAISecretName`.
Container port 9621. Non-secret env as literal env vars on the container
(the chart used a ConfigMap+envFrom; the native build sets them inline for a
self-contained object - this is the KISS choice and keeps the builder pure with
no separate ConfigMap object to track). Image from `cfg.LightragImage`.

**Files**

- Create: `internal/memory/lightrag.go`
- Test: `internal/memory/lightrag_test.go`

**Steps**

- [ ] Write the failing test. Create `internal/memory/lightrag_test.go`:

  ```go
  package memory_test

  import (
      "testing"

      "github.com/stretchr/testify/require"
      "github.com/szymonrychu/tatara-operator/internal/memory"
      corev1 "k8s.io/api/core/v1"
  )

  func TestLightragDeployment(t *testing.T) {
      p := testProject("acme")
      d := memory.LightragDeployment(p, testCfg())

      require.Equal(t, "mem-acme-lightrag", d.Name)
      require.Equal(t, "tatara", d.Namespace)
      require.Len(t, d.OwnerReferences, 1)
      require.True(t, *d.OwnerReferences[0].Controller)
      require.Equal(t, appsv1RecreateName(), string(d.Spec.Strategy.Type))

      c := d.Spec.Template.Spec.Containers[0]
      require.Equal(t, "ghcr.io/hkuds/lightrag:v1.4.16", c.Image)
      require.Equal(t, int32(9621), c.Ports[0].ContainerPort)

      env := map[string]corev1.EnvVar{}
      for _, e := range c.Env {
          env[e.Name] = e
      }

      // Non-secret wiring.
      require.Equal(t, "mem-acme-pg-rw", env["POSTGRES_HOST"].Value)
      require.Equal(t, "5432", env["POSTGRES_PORT"].Value)
      require.Equal(t, "tatara_memory", env["POSTGRES_DATABASE"].Value)
      require.Equal(t, "tatara_memory", env["POSTGRES_USER"].Value)
      require.Equal(t, "bolt://mem-acme-neo4j:7687", env["NEO4J_URI"].Value)
      require.Equal(t, "neo4j", env["NEO4J_USERNAME"].Value)
      require.Equal(t, "PGVectorStorage", env["LIGHTRAG_VECTOR_STORAGE"].Value)
      require.Equal(t, "Neo4JStorage", env["LIGHTRAG_GRAPH_STORAGE"].Value)

      // Secret wiring.
      require.Equal(t, "tatara-openai", env["LLM_BINDING_API_KEY"].ValueFrom.SecretKeyRef.Name)
      require.Equal(t, "LLM_BINDING_API_KEY", env["LLM_BINDING_API_KEY"].ValueFrom.SecretKeyRef.Key)
      require.Equal(t, "mem-acme-pg-app", env["POSTGRES_PASSWORD"].ValueFrom.SecretKeyRef.Name)
      require.Equal(t, "password", env["POSTGRES_PASSWORD"].ValueFrom.SecretKeyRef.Key)
      require.Equal(t, "mem-acme-neo4j", env["NEO4J_PASSWORD"].ValueFrom.SecretKeyRef.Name)
      require.Equal(t, "password", env["NEO4J_PASSWORD"].ValueFrom.SecretKeyRef.Key)
  }

  func TestLightragService(t *testing.T) {
      p := testProject("acme")
      svc := memory.LightragService(p, testCfg())
      require.Equal(t, "mem-acme-lightrag", svc.Name)
      require.Equal(t, int32(9621), svc.Spec.Ports[0].Port)
      require.Equal(t, "mem-acme", svc.Spec.Selector["app.kubernetes.io/instance"])
      require.Len(t, svc.OwnerReferences, 1)
  }

  func TestLightragPVC(t *testing.T) {
      p := testProject("acme")
      pvc := memory.LightragPVC(p, testCfg())
      require.Equal(t, "mem-acme-lightrag-data", pvc.Name)
      require.Equal(t, "10Gi", pvc.Spec.Resources.Requests.Storage().String())
      require.Len(t, pvc.OwnerReferences, 1)
  }
  ```

  Add a tiny helper at the bottom of `lightrag_test.go` to avoid importing
  appsv1 just for one constant:

  ```go
  func appsv1RecreateName() string { return "Recreate" }
  ```

- [ ] Run and expect FAIL:

  ```
  go test ./internal/memory/ -run 'TestLightrag'
  ```

  Expected: FAIL to COMPILE - `memory.LightragDeployment` /
  `memory.LightragService` / `memory.LightragPVC` undefined.

- [ ] Minimal implementation. Create `internal/memory/lightrag.go`. Non-secret
  env values are ported verbatim from `lightrag/values.yaml` +
  `configKeys` (the chart defaults), rewired only for host/uri/db/user. Note
  lightrag's PVC is RWO with replicas 1, so strategy is `Recreate` (ported from
  the chart deployment comment):

  ```go
  package memory

  import (
      tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
      appsv1 "k8s.io/api/apps/v1"
      corev1 "k8s.io/api/core/v1"
      "k8s.io/apimachinery/pkg/api/resource"
      metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
      "k8s.io/apimachinery/pkg/util/intstr"
  )

  func secretEnv(name, secretName, key string) corev1.EnvVar {
      return corev1.EnvVar{
          Name: name,
          ValueFrom: &corev1.EnvVarSource{
              SecretKeyRef: &corev1.SecretKeySelector{
                  LocalObjectReference: corev1.LocalObjectReference{Name: secretName},
                  Key:                  key,
              },
          },
      }
  }

  // lightragEnv is the lightrag container environment, ported from the chart's
  // configKeys (non-secret defaults) and secret refs, rewired to per-Project
  // postgres (mem-<proj>-pg-rw / app Secret), neo4j (mem-<proj>-neo4j), and the
  // shared OpenAI Secret.
  func lightragEnv(p *tatarav1alpha1.Project, cfg Config) []corev1.EnvVar {
      n := NamesFor(p.Name)
      lit := func(k, v string) corev1.EnvVar { return corev1.EnvVar{Name: k, Value: v} }
      return []corev1.EnvVar{
          lit("LLM_BINDING", "openai"),
          lit("LLM_MODEL", "gpt-4.1-mini"),
          lit("EMBEDDING_BINDING", "openai"),
          lit("EMBEDDING_MODEL", "text-embedding-3-small"),
          lit("EMBEDDING_DIM", "1536"),
          lit("LIGHTRAG_KV_STORAGE", "PGKVStorage"),
          lit("LIGHTRAG_VECTOR_STORAGE", "PGVectorStorage"),
          lit("LIGHTRAG_GRAPH_STORAGE", "Neo4JStorage"),
          lit("LIGHTRAG_DOC_STATUS_STORAGE", "PGDocStatusStorage"),
          lit("NEO4J_URI", "bolt://"+n.Neo4j+":7687"),
          lit("NEO4J_USERNAME", "neo4j"),
          lit("MAX_ASYNC", "8"),
          lit("MAX_PARALLEL_INSERT", "8"),
          lit("EMBEDDING_FUNC_MAX_ASYNC", "8"),
          lit("POSTGRES_HOST", n.PGService),
          lit("POSTGRES_PORT", "5432"),
          lit("POSTGRES_DATABASE", "tatara_memory"),
          lit("POSTGRES_USER", "tatara_memory"),
          secretEnv("LLM_BINDING_API_KEY", cfg.OpenAISecretName, "LLM_BINDING_API_KEY"),
          secretEnv("POSTGRES_PASSWORD", n.PGAppSecret, "password"),
          secretEnv("NEO4J_PASSWORD", n.Neo4jSecret, "password"),
      }
  }

  // LightragDeployment builds the per-Project lightrag Deployment (port 9621,
  // Recreate strategy because the data PVC is RWO with one replica).
  func LightragDeployment(p *tatarav1alpha1.Project, cfg Config) *appsv1.Deployment {
      n := NamesFor(p.Name)
      replicas := int32(1)
      sel := selectorLabels(p.Name, "lightrag")
      podLabels := labels(p.Name)
      podLabels["app.kubernetes.io/component"] = "lightrag"

      return &appsv1.Deployment{
          TypeMeta:   metav1.TypeMeta{APIVersion: "apps/v1", Kind: "Deployment"},
          ObjectMeta: objectMeta(p, cfg, n.Lightrag),
          Spec: appsv1.DeploymentSpec{
              Replicas: &replicas,
              Strategy: appsv1.DeploymentStrategy{Type: appsv1.RecreateDeploymentStrategyType},
              Selector: &metav1.LabelSelector{MatchLabels: sel},
              Template: corev1.PodTemplateSpec{
                  ObjectMeta: metav1.ObjectMeta{Labels: podLabels},
                  Spec: corev1.PodSpec{
                      Containers: []corev1.Container{{
                          Name:  "lightrag",
                          Image: cfg.LightragImage,
                          Ports: []corev1.ContainerPort{
                              {Name: "http", ContainerPort: 9621, Protocol: corev1.ProtocolTCP},
                          },
                          Env: lightragEnv(p, cfg),
                          ReadinessProbe: &corev1.Probe{
                              ProbeHandler:  corev1.ProbeHandler{TCPSocket: &corev1.TCPSocketAction{Port: intstr.FromString("http")}},
                              PeriodSeconds: 10,
                          },
                          VolumeMounts: []corev1.VolumeMount{{Name: "data", MountPath: "/app/data"}},
                      }},
                      Volumes: []corev1.Volume{{
                          Name: "data",
                          VolumeSource: corev1.VolumeSource{
                              PersistentVolumeClaim: &corev1.PersistentVolumeClaimVolumeSource{ClaimName: n.LightragPVC},
                          },
                      }},
                  },
              },
          },
      }
  }

  // LightragService exposes lightrag on 9621 (ClusterIP).
  func LightragService(p *tatarav1alpha1.Project, cfg Config) *corev1.Service {
      n := NamesFor(p.Name)
      return &corev1.Service{
          TypeMeta:   metav1.TypeMeta{APIVersion: "v1", Kind: "Service"},
          ObjectMeta: objectMeta(p, cfg, n.Lightrag),
          Spec: corev1.ServiceSpec{
              Type:     corev1.ServiceTypeClusterIP,
              Selector: selectorLabels(p.Name, "lightrag"),
              Ports: []corev1.ServicePort{
                  {Name: "http", Port: 9621, TargetPort: intstr.FromString("http"), Protocol: corev1.ProtocolTCP},
              },
          },
      }
  }

  // LightragPVC is the lightrag data volume (RWO, sized 10Gi by default; lightrag
  // storage is not separately configurable in spec.memory, so it uses the fixed
  // chart default).
  func LightragPVC(p *tatarav1alpha1.Project, cfg Config) *corev1.PersistentVolumeClaim {
      n := NamesFor(p.Name)
      return &corev1.PersistentVolumeClaim{
          TypeMeta:   metav1.TypeMeta{APIVersion: "v1", Kind: "PersistentVolumeClaim"},
          ObjectMeta: objectMeta(p, cfg, n.LightragPVC),
          Spec: corev1.PersistentVolumeClaimSpec{
              AccessModes: []corev1.PersistentVolumeAccessMode{corev1.ReadWriteOnce},
              Resources: corev1.VolumeResourceRequirements{
                  Requests: corev1.ResourceList{corev1.ResourceStorage: resource.MustParse("10Gi")},
              },
          },
      }
  }
  ```

- [ ] Run and expect PASS:

  ```
  go test ./internal/memory/ -run 'TestLightrag'
  ```

  Expected: PASS.

- [ ] Commit:

  ```
  git add internal/memory/lightrag.go internal/memory/lightrag_test.go
  git commit -m "feat: internal/memory lightrag Deployment + Service + PVC (per-project wiring)"
  ```

---

## Task 9: `MemoryDeployment` + `MemoryService` + `MemoryConfigMap` + `MemorySecret` builders

Port from `charts/tatara-memory/templates/{deployment,configmap,service,secret}.yaml`
and `_helpers.tpl` `envConfig`. Rewire: `LIGHTRAG_BASE_URL=http://mem-<proj>-lightrag:9621`,
`OIDC_ISSUER`/`OIDC_AUDIENCE` from `cfg`, `PG_DSN` from the cnpg app Secret
`mem-<proj>-pg-app` key `uri`. Non-secret env in a ConfigMap consumed via
`envFrom` (the parent chart's pattern, rule 6), the per-Project Secret is a
thin placeholder (cnpg owns the real DSN; the Secret exists for symmetry and
any future non-cnpg secret env, ported from the chart `secret.yaml`). Image
from `cfg.MemoryImage`, port 8080, probes `/healthz` + `/readyz`.

**Files**

- Create: `internal/memory/memory_builders.go`
- Test: `internal/memory/memory_builders_test.go`

**Steps**

- [ ] Write the failing test. Create `internal/memory/memory_builders_test.go`:

  ```go
  package memory_test

  import (
      "testing"

      "github.com/stretchr/testify/require"
      "github.com/szymonrychu/tatara-operator/internal/memory"
      corev1 "k8s.io/api/core/v1"
  )

  func TestMemoryDeployment(t *testing.T) {
      p := testProject("acme")
      d := memory.MemoryDeployment(p, testCfg())

      require.Equal(t, "mem-acme", d.Name)
      require.Equal(t, "tatara", d.Namespace)
      require.Len(t, d.OwnerReferences, 1)
      require.True(t, *d.OwnerReferences[0].Controller)

      c := d.Spec.Template.Spec.Containers[0]
      require.Equal(t, "harbor/tatara-memory:0.2.0", c.Image)
      require.Equal(t, int32(8080), c.Ports[0].ContainerPort)

      // envFrom references the ConfigMap and Secret.
      var cmRef, secRef bool
      for _, ef := range c.EnvFrom {
          if ef.ConfigMapRef != nil && ef.ConfigMapRef.Name == "mem-acme" {
              cmRef = true
          }
          if ef.SecretRef != nil && ef.SecretRef.Name == "mem-acme" {
              secRef = true
          }
      }
      require.True(t, cmRef, "configMapRef mem-acme missing from envFrom")
      require.True(t, secRef, "secretRef mem-acme missing from envFrom")

      // PG_DSN from the cnpg app secret key uri.
      var dsn corev1.EnvVar
      var found bool
      for _, e := range c.Env {
          if e.Name == "PG_DSN" {
              dsn, found = e, true
          }
      }
      require.True(t, found)
      require.Equal(t, "mem-acme-pg-app", dsn.ValueFrom.SecretKeyRef.Name)
      require.Equal(t, "uri", dsn.ValueFrom.SecretKeyRef.Key)
  }

  func TestMemoryConfigMap(t *testing.T) {
      p := testProject("acme")
      cm := memory.MemoryConfigMap(p, testCfg())
      require.Equal(t, "mem-acme", cm.Name)
      require.Equal(t, ":8080", cm.Data["HTTP_ADDR"])
      require.Equal(t, "http://mem-acme-lightrag:9621", cm.Data["LIGHTRAG_BASE_URL"])
      require.Equal(t, "https://auth.example/realms/master", cm.Data["OIDC_ISSUER"])
      require.Equal(t, "tatara-memory", cm.Data["OIDC_AUDIENCE"])
      require.Equal(t, "info", cm.Data["LOG_LEVEL"])
      require.Contains(t, cm.Data, "WORKER_POOL_SIZE")
      require.Len(t, cm.OwnerReferences, 1)
  }

  func TestMemorySecret(t *testing.T) {
      p := testProject("acme")
      s := memory.MemorySecret(p, testCfg())
      require.Equal(t, "mem-acme", s.Name)
      require.Equal(t, corev1.SecretTypeOpaque, s.Type)
      require.Len(t, s.OwnerReferences, 1)
  }

  func TestMemoryService(t *testing.T) {
      p := testProject("acme")
      svc := memory.MemoryService(p, testCfg())
      require.Equal(t, "mem-acme", svc.Name)
      require.Equal(t, int32(8080), svc.Spec.Ports[0].Port)
      require.Equal(t, "mem-acme", svc.Spec.Selector["app.kubernetes.io/instance"])
      require.Len(t, svc.OwnerReferences, 1)
  }
  ```

- [ ] Run and expect FAIL:

  ```
  go test ./internal/memory/ -run 'TestMemory(Deployment|ConfigMap|Secret|Service)'
  ```

  Expected: FAIL to COMPILE - the four `memory.Memory*` builders are undefined.

- [ ] Minimal implementation. Create `internal/memory/memory_builders.go`.
  `WORKER_POOL_SIZE` and `LOG_LEVEL` use the chart defaults (4, info); they are
  not in `memory.Config`, so hard-code the chart defaults (rule 2 / KISS - they
  are not per-Project tunables in this milestone):

  ```go
  package memory

  import (
      tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
      appsv1 "k8s.io/api/apps/v1"
      corev1 "k8s.io/api/core/v1"
      metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
      "k8s.io/apimachinery/pkg/util/intstr"
  )

  // MemoryConfigMap holds the tatara-memory non-secret env (ported from the
  // chart envConfig), rewired to the per-Project lightrag URL and the operator
  // OIDC config.
  func MemoryConfigMap(p *tatarav1alpha1.Project, cfg Config) *corev1.ConfigMap {
      n := NamesFor(p.Name)
      return &corev1.ConfigMap{
          TypeMeta:   metav1.TypeMeta{APIVersion: "v1", Kind: "ConfigMap"},
          ObjectMeta: objectMeta(p, cfg, n.Memory),
          Data: map[string]string{
              "HTTP_ADDR":         ":8080",
              "LIGHTRAG_BASE_URL": "http://" + n.Lightrag + ":9621",
              "OIDC_ISSUER":       cfg.OIDCIssuer,
              "OIDC_AUDIENCE":     cfg.OIDCAudience,
              "WORKER_POOL_SIZE":  "4",
              "LOG_LEVEL":         "info",
          },
      }
  }

  // MemorySecret is the per-Project tatara-memory Secret. The real PG DSN comes
  // from the cnpg app Secret via an inline PG_DSN env (see MemoryDeployment);
  // this Secret exists for envFrom symmetry and future non-cnpg secret env.
  func MemorySecret(p *tatarav1alpha1.Project, cfg Config) *corev1.Secret {
      n := NamesFor(p.Name)
      return &corev1.Secret{
          TypeMeta:   metav1.TypeMeta{APIVersion: "v1", Kind: "Secret"},
          ObjectMeta: objectMeta(p, cfg, n.Memory),
          Type:       corev1.SecretTypeOpaque,
      }
  }

  // MemoryDeployment builds the per-Project tatara-memory Deployment (port 8080).
  // Non-secret env via the ConfigMap, the per-Project Secret via envFrom, and
  // PG_DSN inline from the cnpg app Secret key uri.
  func MemoryDeployment(p *tatarav1alpha1.Project, cfg Config) *appsv1.Deployment {
      n := NamesFor(p.Name)
      replicas := int32(1)
      sel := selectorLabels(p.Name, "memory")
      podLabels := labels(p.Name)
      podLabels["app.kubernetes.io/component"] = "memory"

      return &appsv1.Deployment{
          TypeMeta:   metav1.TypeMeta{APIVersion: "apps/v1", Kind: "Deployment"},
          ObjectMeta: objectMeta(p, cfg, n.Memory),
          Spec: appsv1.DeploymentSpec{
              Replicas: &replicas,
              Selector: &metav1.LabelSelector{MatchLabels: sel},
              Template: corev1.PodTemplateSpec{
                  ObjectMeta: metav1.ObjectMeta{Labels: podLabels},
                  Spec: corev1.PodSpec{
                      Containers: []corev1.Container{{
                          Name:  "tatara-memory",
                          Image: cfg.MemoryImage,
                          Ports: []corev1.ContainerPort{
                              {Name: "http", ContainerPort: 8080, Protocol: corev1.ProtocolTCP},
                          },
                          EnvFrom: []corev1.EnvFromSource{
                              {ConfigMapRef: &corev1.ConfigMapEnvSource{LocalObjectReference: corev1.LocalObjectReference{Name: n.Memory}}},
                              {SecretRef: &corev1.SecretEnvSource{LocalObjectReference: corev1.LocalObjectReference{Name: n.Memory}}},
                          },
                          Env: []corev1.EnvVar{
                              secretEnv("PG_DSN", n.PGAppSecret, "uri"),
                          },
                          LivenessProbe: &corev1.Probe{
                              ProbeHandler:        corev1.ProbeHandler{HTTPGet: &corev1.HTTPGetAction{Path: "/healthz", Port: intstr.FromString("http")}},
                              InitialDelaySeconds: 5,
                              PeriodSeconds:       10,
                          },
                          ReadinessProbe: &corev1.Probe{
                              ProbeHandler:        corev1.ProbeHandler{HTTPGet: &corev1.HTTPGetAction{Path: "/readyz", Port: intstr.FromString("http")}},
                              InitialDelaySeconds: 5,
                              PeriodSeconds:       10,
                          },
                      }},
                  },
              },
          },
      }
  }

  // MemoryService exposes tatara-memory on 8080 (ClusterIP).
  func MemoryService(p *tatarav1alpha1.Project, cfg Config) *corev1.Service {
      n := NamesFor(p.Name)
      return &corev1.Service{
          TypeMeta:   metav1.TypeMeta{APIVersion: "v1", Kind: "Service"},
          ObjectMeta: objectMeta(p, cfg, n.Memory),
          Spec: corev1.ServiceSpec{
              Type:     corev1.ServiceTypeClusterIP,
              Selector: selectorLabels(p.Name, "memory"),
              Ports: []corev1.ServicePort{
                  {Name: "http", Port: 8080, TargetPort: intstr.FromString("http"), Protocol: corev1.ProtocolTCP},
              },
          },
      }
  }
  ```

- [ ] Run and expect PASS:

  ```
  go test ./internal/memory/ -run 'TestMemory(Deployment|ConfigMap|Secret|Service)'
  ```

  Expected: PASS.

- [ ] Commit:

  ```
  git add internal/memory/memory_builders.go internal/memory/memory_builders_test.go
  git commit -m "feat: internal/memory tatara-memory Deployment + Service + ConfigMap + Secret"
  ```

---

## Task 10: Full verification + MEMORY.md / ROADMAP.md

Final gate per `superpowers:verification-before-completion`: the whole tree
builds, lints, all tests pass, manifests are in sync, and the cross-session
docs are updated.

**Files**

- Modify: `MEMORY.md`, `ROADMAP.md` (repo root)

**Steps**

- [ ] Run the full suite and SEE it pass:

  ```
  make generate
  make manifests
  git diff --exit-code charts/tatara-operator/crds api/v1alpha1/zz_generated.deepcopy.go
  go build ./...
  make lint
  go test ./...
  ```

  Expected: `git diff --exit-code` returns clean (generated artifacts already
  committed in Task 2; if it reports a diff, the generation in Task 2 was
  incomplete - regenerate, re-commit, do not hand-edit). All other commands
  pass.

- [ ] Confirm no dangling `MEMORY_BASE_URL` and the cnpg type is wired:

  ```
  grep -rn 'MEMORY_BASE_URL\|MemoryBaseURL' --include='*.go' . | grep -v 'internal/ingest'
  grep -rn 'cnpgv1' cmd/manager/main.go internal/memory/pg.go
  ```

  Expected: first grep returns nothing (the only remaining `MemoryBaseURL` is
  the `ingest.Config` field, owned by N3); second grep returns the import +
  usages.

- [ ] Update `MEMORY.md` (append dated one-liners):

  - `2026-06-07 N1: per-Project memory builders landed in internal/memory (pure, unit-tested).`
  - `2026-06-07 cnpg api module pinned to <vX.Y.Z> (matches live operator image); part of main cloudnative-pg module, heavy transitive deps accepted to use upstream Cluster type.`
  - `2026-06-07 pin set Names(project) -> NamesFor(project): func and returned struct cannot share the name Names.`
  - `2026-06-07 MEMORY_BASE_URL removed from operator config; per-Project endpoint (status.memory.endpoint) replaces it in N3. ingest.Config.MemoryBaseURL retained, repointed in N3.`

- [ ] Update `ROADMAP.md`: mark N1 done, leave N2/N3/N4 as upcoming. If N1 is
  not yet listed, add the four-milestone block and check off N1.

- [ ] Commit:

  ```
  git add MEMORY.md ROADMAP.md
  git commit -m "docs: record N1 per-project memory builders complete"
  ```

- [ ] Final report: state evidence (test count / `go test ./...` ok,
  `make lint` ok, manifests in sync) - do not claim done without the command
  output in hand.

---

## Notes / contracts spelled out for N2-N4

- **`memory.Config`** (Task 4) - the only operator-level input the builders
  read. N2/N3 map `config.Config` -> `memory.Config` in the manager wiring.
- **`NamesFor(project) Names`** (struct fields: PGCluster, PGService,
  PGAppSecret, Neo4j, Neo4jSecret, Lightrag, LightragPVC, Memory) - the
  authoritative name source. (Pin set spelled it `Names`; renamed to `NamesFor`
  for the func, struct stays `Names`.)
- **`Endpoint(project, namespace) string`** = `http://mem-<proj>.<ns>.svc:8080`
  - the value N2 writes to `status.memory.endpoint` and N3 reads.
- **`MemorySpec`** {PgInstances int, PgStorage string, Neo4jStorage string} and
  **`MemoryStatus`** {Phase string, Endpoint string} - pointers on
  `ProjectSpec.Memory` / `ProjectStatus.Memory`.
- **Builder signatures** (all return owner-ref'd, pin-set-labelled objects):
  - `PGCluster(p, cfg) *cnpgv1.Cluster`
  - `Neo4jPasswordSecret(p, cfg, password string) *corev1.Secret`
  - `Neo4jStatefulSet(p, cfg) *appsv1.StatefulSet`, `Neo4jService(p, cfg) *corev1.Service`
  - `LightragDeployment(p, cfg) *appsv1.Deployment`, `LightragService(p, cfg) *corev1.Service`, `LightragPVC(p, cfg) *corev1.PersistentVolumeClaim`
  - `MemoryDeployment(p, cfg) *appsv1.Deployment`, `MemoryService(p, cfg) *corev1.Service`, `MemoryConfigMap(p, cfg) *corev1.ConfigMap`, `MemorySecret(p, cfg) *corev1.Secret`
- **Defaults** applied inside builders (not kubebuilder): pgInstances 1,
  pgStorage 10Gi, neo4jStorage 10Gi. lightrag PVC fixed 10Gi (no spec field).
- **Owner refs**: every object has one controller OwnerReference to the Project
  (Controller=true, BlockOwnerDeletion=true) so N2's `Owns()` + cascade delete
  work. N2 must SSA the generated neo4j password Secret first (guarded on
  existence) then the rest, and register all kinds (cnpgv1.Cluster, StatefulSet,
  Deployment, Service, PVC, ConfigMap, Secret) with `Owns()`.
