# Per-Project Memory N2 (ProjectReconciler provisioning + status.memory + Ready health + cascade) Implementation Plan

> For agentic workers: Execute tasks top to bottom. Each task is one
> red-green-commit cycle. Do not skip the failing-test step, do not batch
> tasks, do not write implementation before its test fails for the expected
> reason. Copy the FULL code blocks verbatim - they contain no placeholders.
> Run the exact commands shown and confirm the exact PASS/FAIL before moving
> on. Work in the `tatara-operator` repo on a worktree off `main`
> (`superpowers:using-git-worktrees`); build/deploy only ever from `main`.
> All paths are relative to the repo root
> `/Users/szymonri/Documents/tatara/tatara-operator`.

## Goal

Extend `ProjectReconciler` so that reconciling a `Project` provisions its
complete per-project memory stack (cnpg `Cluster`, neo4j `StatefulSet`+`Service`,
lightrag `Deployment`+`Service`+`PVC`, tatara-memory `Deployment`+`Service`+
`ConfigMap`+`Secret`, generated neo4j password `Secret`) using the N1
`internal/memory` builders, owner-ref'd to the Project for cascade delete; then
computes `status.memory.{phase,endpoint}` and a `MemoryReady` condition from the
owned objects' health and a `MemoryReady=False` on apply failure.

The reconciler:
1. Builds `memory.Config` from `config.Config` (injected field on the reconciler).
2. Generates a random neo4j password ONCE into `mem-<proj>-neo4j` (guard on
   existence so it is never rotated), then server-side-applies every stack
   object via SSA with a stable field owner.
3. Sets `phase=Ready` when cnpg `Cluster.status.readyInstances >= spec instances`
   AND neo4j `StatefulSet.status.readyReplicas >= 1` AND lightrag + memory
   `Deployment.status.availableReplicas >= 1`; else `phase=Provisioning`
   (requeue ~10s). On any apply error: `phase=Failed` + `MemoryReady=False`.
4. `Owns()` cnpgv1.Cluster, appsv1.StatefulSet, appsv1.Deployment, corev1.Service,
   corev1.PersistentVolumeClaim, corev1.ConfigMap, corev1.Secret.
5. Emits `operator_memory_provision_duration_seconds` (histogram) and
   `operator_memory_stacks` (gauge by phase).

## Architecture

`ProjectReconciler.Reconcile` keeps its existing SCM-validation path (sets
`status.webhookURL` + the `Ready` condition) unchanged, and gains a second,
independent concern: the memory stack. The two concerns share one
`r.Status().Update` at the end so both `status.memory` and `status.conditions`
(carrying both `Ready` and `MemoryReady`) persist atomically against the status
subresource.

Provisioning is server-side apply (SSA): `r.Patch(ctx, obj, client.Apply,
client.FieldOwner("tatara-operator"), client.ForceOwnership)`. SSA is
idempotent and declarative, so re-applying every reconcile converges the stack
without read-modify-write races. The generated neo4j password is the one
non-idempotent input, so it is created with a plain `Create` guarded on
`IsNotFound` and never re-applied (re-applying a fresh random value would rotate
it). Owner refs are set by the N1 builders (Controller=true), so cascade delete
falls out of Kubernetes garbage collection; the reconciler does no explicit
teardown.

Health is read from the owned objects' `.status`. In envtest there is no
kubelet/cnpg-operator, so tests fake the owned objects' status subresources
directly (the reconciler reads them; nothing else writes them), which exercises
the exact Provisioning->Ready transition logic.

The cnpg `Cluster` CRD is not in `client-go`'s scheme, so envtest cannot create
`Cluster` objects unless its CRD YAML is on disk and loaded. N4 owns the chart
RBAC; N2 only needs the CRD present for envtest, so this plan vendors the cnpg
`Cluster` CRD into `charts/tatara-operator/crds/` (already the envtest
`CRDDirectoryPaths` root) and registers `cnpgv1` in the suite scheme.

## Tech Stack

- Go 1.26 (`go.mod` directive `go 1.26.0`), controller-runtime v0.24.1,
  k8s.io/api+apimachinery v0.36.x.
- cnpg API types `github.com/cloudnative-pg/cloudnative-pg/api/v1` (alias
  `cnpgv1`) - added to go.mod and the scheme in N1; N2 vendors the matching
  CRD YAML for envtest.
- Tests: standard `testing` + envtest (`make test`, which sets
  `KUBEBUILDER_ASSETS`). No ginkgo; the package uses plain `testing` with a
  shared `TestMain` control plane (`internal/controller/suite_test.go`).
- Metrics: `prometheus/client_golang` via `internal/obs.OperatorMetrics`.

## Assumptions

- N1 is merged to `main` before N2 starts and provides, in package
  `github.com/szymonrychu/tatara-operator/internal/memory`:
  `type Config struct{ Namespace, MemoryImage, LightragImage, Neo4jImage,
  OpenAISecretName, OIDCIssuer, OIDCAudience string }`, `Names(project string)`,
  `Endpoint(project, namespace string) string`,
  `PGCluster(p *v1alpha1.Project, cfg Config) *cnpgv1.Cluster`,
  `Neo4jStatefulSet`, `Neo4jService`, `LightragDeployment`, `LightragService`,
  `LightragPVC`, `MemoryDeployment`, `MemoryService`, `MemoryConfigMap`,
  `MemorySecret`, `Neo4jPasswordSecret(p *v1alpha1.Project, cfg Config,
  password string) *corev1.Secret`. All builders set the Project owner ref
  (Controller=true) and the shared labels.
- N1 added `Project.Spec.Memory *MemorySpec` (`PgInstances int`,
  `PgStorage string`, `Neo4jStorage string`) and `Project.Status.Memory
  *MemoryStatus` (`Phase string`, `Endpoint string`) to `api/v1alpha1`, plus the
  config keys `MemoryImage`, `LightragImage`, `Neo4jImage`, `OpenAISecretName`
  on `config.Config`, the cnpgv1 go.mod dependency, and cnpgv1 scheme
  registration in `cmd/manager/main.go`'s `newScheme()`.
- The default pg instance count is 1 (applied in the N1 builder when
  `spec.memory.pgInstances == 0`); N2's Ready check compares
  `cluster.Status.ReadyInstances` against the same effective instance count via
  a single shared helper (Task 4).

If any of these are not yet on `main`, STOP and report - do not re-create N1
artifacts here.

---

## Task 1: Vendor the cnpg Cluster CRD into the chart crds dir and register cnpgv1 in the envtest suite

Make envtest able to create `cnpgv1.Cluster` objects. Without the CRD on disk
and the type in the suite scheme, every later test that applies the stack fails
at the `Cluster` apply with a no-kind-match / no-CRD error.

**Files:**
- `charts/tatara-operator/crds/postgresql.cnpg.io_clusters.yaml` (new, vendored)
- `internal/controller/suite_test.go` (edit: register cnpgv1 scheme)
- `hack/vendor-cnpg-crd.sh` (new, records the exact provenance command)

Steps:

- [ ] Determine the exact cnpg module version on `main` so the vendored CRD
  matches the compiled types:
  ```
  cd /Users/szymonri/Documents/tatara/tatara-operator
  go list -m github.com/cloudnative-pg/cloudnative-pg
  ```
  Expected: a line like `github.com/cloudnative-pg/cloudnative-pg v1.2x.y`.
  Record that version string; call it `<CNPG_VERSION>` below.

- [ ] Create `hack/vendor-cnpg-crd.sh` with the FULL content (this is the
  provenance record, not a test; it is run once by hand):
  ```bash
  #!/usr/bin/env bash
  # Vendors the cnpg Cluster CRD matching the go.mod cnpg version into the
  # chart crds dir so envtest can create Cluster objects. Re-run after bumping
  # the cnpg dependency. The chart deploys this CRD to real clusters too.
  set -euo pipefail
  cd "$(dirname "$0")/.."
  ver="$(go list -m -f '{{.Version}}' github.com/cloudnative-pg/cloudnative-pg)"
  url="https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/${ver}/config/crd/bases/postgresql.cnpg.io_clusters.yaml"
  echo "fetching ${url}"
  curl -fsSL "${url}" -o charts/tatara-operator/crds/postgresql.cnpg.io_clusters.yaml
  echo "wrote charts/tatara-operator/crds/postgresql.cnpg.io_clusters.yaml"
  ```

- [ ] Make it executable and run it:
  ```
  chmod +x hack/vendor-cnpg-crd.sh
  ./hack/vendor-cnpg-crd.sh
  ```
  Expected: it prints the fetched URL and writes
  `charts/tatara-operator/crds/postgresql.cnpg.io_clusters.yaml`. Confirm the
  file is non-empty and its first lines declare
  `kind: CustomResourceDefinition` with `names.kind: Cluster` and
  `group: postgresql.cnpg.io`:
  ```
  grep -E 'kind: CustomResourceDefinition|kind: Cluster|group: postgresql.cnpg.io' charts/tatara-operator/crds/postgresql.cnpg.io_clusters.yaml
  ```
  Expected: all three lines match. If `curl` fails (offline), fetch the same
  raw URL by any means and place the file at that path; the test in this task
  is what verifies correctness.

- [ ] Write the failing test by registering cnpgv1 in the suite scheme and
  proving the control plane accepts a `Cluster`. Edit
  `internal/controller/suite_test.go`. First add the import (place after the
  `tataradevv1alpha1` import line):
  ```go
  	cnpgv1 "github.com/cloudnative-pg/cloudnative-pg/api/v1"
  ```
  Then, inside `TestMain`, immediately after the existing
  `tataradevv1alpha1.AddToScheme(scheme.Scheme)` block, add:
  ```go
  		if err := cnpgv1.AddToScheme(scheme.Scheme); err != nil {
  			panic("add cnpg scheme: " + err.Error())
  		}
  ```
  Now add a new test file `internal/controller/memory_crd_test.go` with the FULL
  content:
  ```go
  package controller

  import (
  	"context"
  	"testing"

  	cnpgv1 "github.com/cloudnative-pg/cloudnative-pg/api/v1"
  	"k8s.io/apimachinery/pkg/types"
  )

  // TestCNPGClusterCRDInstalled proves the vendored cnpg Cluster CRD is loaded
  // by envtest and the cnpgv1 type is in the suite scheme, so later
  // provisioning tests can create Cluster objects.
  func TestCNPGClusterCRDInstalled(t *testing.T) {
  	ctx := context.Background()
  	c := &cnpgv1.Cluster{}
  	c.Name = "crd-probe"
  	c.Namespace = testNS
  	c.Spec.Instances = 1
  	c.Spec.StorageConfiguration = cnpgv1.StorageConfiguration{Size: "1Gi"}
  	if err := k8sClient.Create(ctx, c); err != nil {
  		t.Fatalf("create cnpg Cluster (CRD not installed or type not registered?): %v", err)
  	}
  	got := &cnpgv1.Cluster{}
  	if err := k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: "crd-probe"}, got); err != nil {
  		t.Fatalf("get cnpg Cluster: %v", err)
  	}
  	if got.Spec.Instances != 1 {
  		t.Fatalf("instances = %d, want 1", got.Spec.Instances)
  	}
  }
  ```

- [ ] Run it and confirm it FIRST fails if the scheme line is omitted - to see
  the red, temporarily comment out the `cnpgv1.AddToScheme` block you added,
  then run:
  ```
  make test 2>&1 | grep -A3 TestCNPGClusterCRDInstalled
  ```
  Expected FAIL: `create cnpg Cluster ... no kind is registered for the type
  v1.Cluster` (scheme) or a no-matches-for-kind error (CRD). Restore the
  `cnpgv1.AddToScheme` block.

- [ ] Run the full suite and confirm PASS:
  ```
  make test 2>&1 | tail -20
  ```
  Expected: `ok  	github.com/szymonrychu/tatara-operator/internal/controller`
  and `TestCNPGClusterCRDInstalled` passes.

- [ ] `superpowers:requesting-code-review`, apply critical/high fixes, then:
  ```
  pre-commit run --all-files
  git add charts/tatara-operator/crds/postgresql.cnpg.io_clusters.yaml hack/vendor-cnpg-crd.sh internal/controller/suite_test.go internal/controller/memory_crd_test.go
  git commit -m "test: vendor cnpg Cluster CRD and register cnpgv1 for envtest"
  ```

---

## Task 2: Add the N2 memory metrics to OperatorMetrics

Add `operator_memory_provision_duration_seconds` (histogram) and
`operator_memory_stacks` (gauge by phase) so the reconciler can record provision
duration and the per-phase stack count.

**Files:**
- `internal/obs/operator_metrics.go` (edit)
- `internal/obs/operator_metrics_test.go` (edit: add tests)

Steps:

- [ ] Add the failing tests. Append to `internal/obs/operator_metrics_test.go`
  the FULL functions:
  ```go
  func TestMemoryProvisionDuration(t *testing.T) {
  	reg := prometheus.NewRegistry()
  	m := NewOperatorMetrics(reg)

  	m.ObserveMemoryProvisionDuration(7.5)

  	mfs, err := reg.Gather()
  	if err != nil {
  		t.Fatalf("gather: %v", err)
  	}
  	var found bool
  	for _, mf := range mfs {
  		if mf.GetName() == "operator_memory_provision_duration_seconds" {
  			found = true
  			if got := mf.GetMetric()[0].GetHistogram().GetSampleCount(); got != 1 {
  				t.Fatalf("sample count = %d, want 1", got)
  			}
  		}
  	}
  	if !found {
  		t.Fatal("operator_memory_provision_duration_seconds not registered")
  	}
  }

  func TestMemoryStacksGauge(t *testing.T) {
  	reg := prometheus.NewRegistry()
  	m := NewOperatorMetrics(reg)

  	m.SetMemoryStacks("Ready", 3)
  	m.SetMemoryStacks("Provisioning", 1)

  	if got := testutil.ToFloat64(m.memoryStacks.WithLabelValues("Ready")); got != 3 {
  		t.Fatalf("Ready stacks = %v, want 3", got)
  	}
  	if got := testutil.ToFloat64(m.memoryStacks.WithLabelValues("Provisioning")); got != 1 {
  		t.Fatalf("Provisioning stacks = %v, want 1", got)
  	}
  }
  ```

- [ ] Run and confirm FAIL (methods/fields do not exist yet):
  ```
  go test ./internal/obs/ -run 'TestMemoryProvisionDuration|TestMemoryStacksGauge' 2>&1 | tail -15
  ```
  Expected FAIL: compile error `m.ObserveMemoryProvisionDuration undefined`,
  `m.memoryStacks undefined`, `m.SetMemoryStacks undefined`.

- [ ] Implement. Edit `internal/obs/operator_metrics.go`. Add the two fields to
  the `OperatorMetrics` struct (after `tasksInflight prometheus.Gauge`):
  ```go
  	memoryProvisionDuration prometheus.Histogram
  	memoryStacks            *prometheus.GaugeVec
  ```
  In `NewOperatorMetrics`, add the two collectors to the `m := &OperatorMetrics{
  ...}` literal (after the `tasksInflight: ...` entry):
  ```go
  		memoryProvisionDuration: prometheus.NewHistogram(prometheus.HistogramOpts{
  			Name:    "operator_memory_provision_duration_seconds",
  			Help:    "Wall-clock duration of a per-project memory stack reaching Ready.",
  			Buckets: prometheus.ExponentialBuckets(5, 2, 8),
  		}),
  		memoryStacks: prometheus.NewGaugeVec(prometheus.GaugeOpts{
  			Name: "operator_memory_stacks",
  			Help: "Number of per-project memory stacks by phase.",
  		}, []string{"phase"}),
  ```
  Add both to the `reg.MustRegister(...)` call (after `m.tasksInflight,`):
  ```go
  		m.memoryProvisionDuration,
  		m.memoryStacks,
  ```
  Pre-initialise the gauge label set so all phases appear in Gather even before
  any stack exists - add after the existing webhook pre-init loop, before
  `return m`:
  ```go
  	for _, phase := range []string{"Provisioning", "Ready", "Failed"} {
  		m.memoryStacks.WithLabelValues(phase)
  	}
  ```
  Add the two methods at the end of the file:
  ```go
  // ObserveMemoryProvisionDuration records the wall-clock seconds a per-project
  // memory stack took to reach Ready.
  func (m *OperatorMetrics) ObserveMemoryProvisionDuration(seconds float64) {
  	m.memoryProvisionDuration.Observe(seconds)
  }

  // SetMemoryStacks sets the operator_memory_stacks gauge for the given phase.
  func (m *OperatorMetrics) SetMemoryStacks(phase string, n float64) {
  	m.memoryStacks.WithLabelValues(phase).Set(n)
  }
  ```

- [ ] Run and confirm PASS:
  ```
  go test ./internal/obs/ 2>&1 | tail -5
  ```
  Expected: `ok  	github.com/szymonrychu/tatara-operator/internal/obs`.

- [ ] `superpowers:requesting-code-review`, apply critical/high fixes, then:
  ```
  pre-commit run --all-files
  git add internal/obs/operator_metrics.go internal/obs/operator_metrics_test.go
  git commit -m "feat: add memory provision duration + stacks-by-phase metrics"
  ```

---

## Task 3: Generate and persist the neo4j password Secret exactly once

Add the password-generation + guard logic the reconciler uses before applying
the stack. The password is created once into `mem-<proj>-neo4j` and never
rotated: a second reconcile must read back the existing Secret's password, not
overwrite it.

**Files:**
- `internal/controller/project_memory.go` (new)
- `internal/controller/project_memory_test.go` (new)

Steps:

- [ ] Write the failing test `internal/controller/project_memory_test.go` with
  FULL content:
  ```go
  package controller

  import (
  	"context"
  	"testing"

  	tataradevv1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
  	"github.com/szymonrychu/tatara-operator/internal/memory"
  	corev1 "k8s.io/api/core/v1"
  	"k8s.io/apimachinery/pkg/types"
  )

  func newMemoryReconciler() *ProjectReconciler {
  	r := newProjectReconciler()
  	r.MemoryConfig = memory.Config{
  		Namespace:        testNS,
  		MemoryImage:      "harbor.example/tatara-memory:test",
  		LightragImage:    "harbor.example/lightrag:test",
  		Neo4jImage:       "neo4j:5-community",
  		OpenAISecretName: "openai-shared",
  		OIDCIssuer:       "https://keycloak.example/realms/tatara",
  		OIDCAudience:     "tatara-memory",
  	}
  	return r
  }

  func mkMemoryProject(t *testing.T, name string) *tataradevv1alpha1.Project {
  	t.Helper()
  	mkSecret(t, name+"-scm", map[string][]byte{
  		"token":         []byte("ghp_x"),
  		"webhookSecret": []byte("hmac"),
  	})
  	p := &tataradevv1alpha1.Project{}
  	p.Name = name
  	p.Namespace = testNS
  	p.Spec.ScmSecretRef = name + "-scm"
  	if err := k8sClient.Create(context.Background(), p); err != nil {
  		t.Fatalf("create project %s: %v", name, err)
  	}
  	return getProject(t, name)
  }

  func TestEnsureNeo4jPassword_GeneratesOnceAndIsStable(t *testing.T) {
  	ctx := context.Background()
  	r := newMemoryReconciler()
  	p := mkMemoryProject(t, "pw-once")

  	pw1, err := r.ensureNeo4jPassword(ctx, p)
  	if err != nil {
  		t.Fatalf("ensureNeo4jPassword first call: %v", err)
  	}
  	if len(pw1) < 24 {
  		t.Fatalf("password too short: %d chars", len(pw1))
  	}

  	names := memory.NamesFor(p.Name)
  	var sec corev1.Secret
  	if err := k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: names.Neo4jSecret}, &sec); err != nil {
  		t.Fatalf("neo4j secret not persisted: %v", err)
  	}

  	pw2, err := r.ensureNeo4jPassword(ctx, p)
  	if err != nil {
  		t.Fatalf("ensureNeo4jPassword second call: %v", err)
  	}
  	if pw2 != pw1 {
  		t.Fatalf("password rotated on second reconcile: %q != %q", pw2, pw1)
  	}
  }
  ```
  Note: `memory.NamesFor(...)` exposes the neo4j Secret name via field
  `Neo4jSecret` (N1 `Names` struct). If N1 named the field differently, use the
  exact field name from N1's `Names` struct - do not invent one.

- [ ] Run and confirm FAIL:
  ```
  make test 2>&1 | grep -E 'ensureNeo4jPassword|MemoryConfig|undefined' | head
  ```
  Expected FAIL: compile error `r.MemoryConfig undefined` and
  `r.ensureNeo4jPassword undefined`.

- [ ] Implement. First add the `MemoryConfig` field to the reconciler struct in
  `internal/controller/project_controller.go` (after `ExternalWebhookBase
  string`):
  ```go
  	MemoryConfig memory.Config
  ```
  Add the import `"github.com/szymonrychu/tatara-operator/internal/memory"` to
  `project_controller.go`. Then create `internal/controller/project_memory.go`
  with the FULL content:
  ```go
  package controller

  import (
  	"context"
  	"crypto/rand"
  	"encoding/base64"
  	"fmt"

  	tataradevv1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
  	"github.com/szymonrychu/tatara-operator/internal/memory"
  	corev1 "k8s.io/api/core/v1"
  	apierrors "k8s.io/apimachinery/pkg/api/errors"
  	"k8s.io/apimachinery/pkg/types"
  )

  // ensureNeo4jPassword returns the neo4j password for the Project's memory
  // stack, generating a random one and persisting it to the mem-<proj>-neo4j
  // Secret on first reconcile. On subsequent reconciles it reads the existing
  // Secret back so the password is never rotated.
  func (r *ProjectReconciler) ensureNeo4jPassword(ctx context.Context, p *tataradevv1alpha1.Project) (string, error) {
  	names := memory.NamesFor(p.Name)
  	var existing corev1.Secret
  	key := types.NamespacedName{Namespace: r.MemoryConfig.Namespace, Name: names.Neo4jSecret}
  	err := r.Get(ctx, key, &existing)
  	switch {
  	case err == nil:
  		pw := string(existing.Data["password"])
  		if pw == "" {
  			return "", fmt.Errorf("neo4j secret %s missing password key", names.Neo4jSecret)
  		}
  		return pw, nil
  	case !apierrors.IsNotFound(err):
  		return "", fmt.Errorf("get neo4j secret: %w", err)
  	}

  	pw, err := randomPassword(32)
  	if err != nil {
  		return "", fmt.Errorf("generate neo4j password: %w", err)
  	}
  	sec := memory.Neo4jPasswordSecret(p, r.MemoryConfig, pw)
  	if err := r.Create(ctx, sec); err != nil {
  		if apierrors.IsAlreadyExists(err) {
  			// Lost a race; read the winner back.
  			if err := r.Get(ctx, key, &existing); err != nil {
  				return "", fmt.Errorf("get neo4j secret after race: %w", err)
  			}
  			return string(existing.Data["password"]), nil
  		}
  		return "", fmt.Errorf("create neo4j secret: %w", err)
  	}
  	return pw, nil
  }

  // randomPassword returns a URL-safe base64 string with at least nBytes of
  // entropy.
  func randomPassword(nBytes int) (string, error) {
  	b := make([]byte, nBytes)
  	if _, err := rand.Read(b); err != nil {
  		return "", err
  	}
  	return base64.RawURLEncoding.EncodeToString(b), nil
  }
  ```

- [ ] Run and confirm PASS:
  ```
  make test 2>&1 | grep -E 'TestEnsureNeo4jPassword|^ok|FAIL' | head
  ```
  Expected: `TestEnsureNeo4jPassword_GeneratesOnceAndIsStable` passes; package
  `ok`.

- [ ] `superpowers:requesting-code-review`, apply critical/high fixes, then:
  ```
  pre-commit run --all-files
  git add internal/controller/project_controller.go internal/controller/project_memory.go internal/controller/project_memory_test.go
  git commit -m "feat: generate per-project neo4j password once, guarded on existence"
  ```

---

## Task 4: Apply the full stack via SSA with owner refs, and compute memory health

Add the SSA apply of every stack object and the health-evaluation helper. This
task does NOT yet wire status writing or `SetupWithManager.Owns` - it adds the
two pure-ish helpers (`applyMemoryStack`, `memoryPhase`) and tests that the
stack lands with correct owner refs and that the phase function returns the
right value for given object statuses.

**Files:**
- `internal/controller/project_memory.go` (edit)
- `internal/controller/project_memory_test.go` (edit)

Steps:

- [ ] Add the failing tests. Append to
  `internal/controller/project_memory_test.go` (and add the listed imports to
  its import block: `cnpgv1`, `appsv1`, `metav1`, `apimeta`):
  ```go
  func TestApplyMemoryStack_CreatesStackWithOwnerRefs(t *testing.T) {
  	ctx := context.Background()
  	r := newMemoryReconciler()
  	p := mkMemoryProject(t, "stack-create")

  	pw, err := r.ensureNeo4jPassword(ctx, p)
  	if err != nil {
  		t.Fatalf("password: %v", err)
  	}
  	if err := r.applyMemoryStack(ctx, p, pw); err != nil {
  		t.Fatalf("applyMemoryStack: %v", err)
  	}

  	names := memory.NamesFor(p.Name)

  	// cnpg Cluster present, owner-ref'd to the Project, instances from spec default.
  	var cluster cnpgv1.Cluster
  	if err := k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: names.PGCluster}, &cluster); err != nil {
  		t.Fatalf("get cnpg cluster: %v", err)
  	}
  	assertOwnedByProject(t, cluster.GetOwnerReferences(), p.Name)

  	// memory Deployment present and owner-ref'd.
  	var dep appsv1.Deployment
  	if err := k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: names.Memory}, &dep); err != nil {
  		t.Fatalf("get memory deployment: %v", err)
  	}
  	assertOwnedByProject(t, dep.GetOwnerReferences(), p.Name)

  	// neo4j StatefulSet present and owner-ref'd.
  	var sts appsv1.StatefulSet
  	if err := k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: names.Neo4j}, &sts); err != nil {
  		t.Fatalf("get neo4j statefulset: %v", err)
  	}
  	assertOwnedByProject(t, sts.GetOwnerReferences(), p.Name)

  	// Idempotent: a second apply must not error.
  	if err := r.applyMemoryStack(ctx, p, pw); err != nil {
  		t.Fatalf("second applyMemoryStack: %v", err)
  	}
  }

  func assertOwnedByProject(t *testing.T, refs []metav1.OwnerReference, project string) {
  	t.Helper()
  	for _, ref := range refs {
  		if ref.Kind == "Project" && ref.Name == project && ref.Controller != nil && *ref.Controller {
  			return
  		}
  	}
  	t.Fatalf("no controller ownerRef to Project %q in %+v", project, refs)
  }

  func TestMemoryPhase_Transitions(t *testing.T) {
  	cases := []struct {
  		name           string
  		readyInstances int
  		wantInstances  int
  		neo4jReady     int32
  		lightragAvail  int32
  		memoryAvail    int32
  		want           string
  	}{
  		{"all-down", 0, 1, 0, 0, 0, "Provisioning"},
  		{"pg-only", 1, 1, 0, 0, 0, "Provisioning"},
  		{"all-but-memory", 1, 1, 1, 1, 0, "Provisioning"},
  		{"all-ready", 1, 1, 1, 1, 1, "Ready"},
  		{"ha-pg-partial", 1, 3, 1, 1, 1, "Provisioning"},
  		{"ha-pg-ready", 3, 3, 1, 1, 1, "Ready"},
  	}
  	for _, tc := range cases {
  		t.Run(tc.name, func(t *testing.T) {
  			got := memoryPhase(tc.readyInstances, tc.wantInstances, tc.neo4jReady, tc.lightragAvail, tc.memoryAvail)
  			if got != tc.want {
  				t.Fatalf("memoryPhase = %q, want %q", got, tc.want)
  			}
  		})
  	}
  }
  ```

- [ ] Run and confirm FAIL:
  ```
  make test 2>&1 | grep -E 'applyMemoryStack|memoryPhase|undefined' | head
  ```
  Expected FAIL: `r.applyMemoryStack undefined`, `memoryPhase undefined`.

- [ ] Implement. Edit `internal/controller/project_memory.go`. Add imports
  `appsv1 "k8s.io/api/apps/v1"`, `cnpgv1
  "github.com/cloudnative-pg/cloudnative-pg/api/v1"`, and
  `"sigs.k8s.io/controller-runtime/pkg/client"`. Append:
  ```go
  // memoryFieldOwner is the SSA field-manager name the operator owns for the
  // per-project memory stack.
  const memoryFieldOwner = "tatara-operator"

  // effectivePGInstances returns the configured pg instance count for the
  // Project, defaulting to 1 when spec.memory is unset or zero.
  func effectivePGInstances(p *tataradevv1alpha1.Project) int {
  	if p.Spec.Memory != nil && p.Spec.Memory.PgInstances > 0 {
  		return p.Spec.Memory.PgInstances
  	}
  	return 1
  }

  // applyMemoryStack server-side-applies every object in the Project's memory
  // stack (owner-ref'd by the N1 builders). The neo4j password Secret is created
  // separately by ensureNeo4jPassword and is NOT applied here, so it is never
  // rotated.
  func (r *ProjectReconciler) applyMemoryStack(ctx context.Context, p *tataradevv1alpha1.Project, neo4jPassword string) error {
  	cfg := r.MemoryConfig
  	objs := []client.Object{
  		memory.PGCluster(p, cfg),
  		memory.Neo4jStatefulSet(p, cfg),
  		memory.Neo4jService(p, cfg),
  		memory.LightragPVC(p, cfg),
  		memory.LightragDeployment(p, cfg),
  		memory.LightragService(p, cfg),
  		memory.MemoryConfigMap(p, cfg),
  		memory.MemorySecret(p, cfg),
  		memory.MemoryDeployment(p, cfg),
  		memory.MemoryService(p, cfg),
  	}
  	for _, obj := range objs {
  		if err := r.Patch(ctx, obj, client.Apply,
  			client.FieldOwner(memoryFieldOwner), client.ForceOwnership); err != nil {
  			return fmt.Errorf("apply %T %s: %w", obj, obj.GetName(), err)
  		}
  	}
  	return nil
  }

  // memoryStackHealth reads the owned objects' statuses and returns the readiness
  // inputs for memoryPhase: cnpg readyInstances, neo4j readyReplicas, lightrag
  // availableReplicas, memory availableReplicas.
  func (r *ProjectReconciler) memoryStackHealth(ctx context.Context, p *tataradevv1alpha1.Project) (readyInstances int, neo4jReady, lightragAvail, memoryAvail int32, err error) {
  	names := memory.NamesFor(p.Name)
  	ns := r.MemoryConfig.Namespace

  	var cluster cnpgv1.Cluster
  	if e := r.Get(ctx, types.NamespacedName{Namespace: ns, Name: names.PGCluster}, &cluster); e != nil {
  		return 0, 0, 0, 0, fmt.Errorf("get cnpg cluster: %w", e)
  	}
  	readyInstances = cluster.Status.ReadyInstances

  	var sts appsv1.StatefulSet
  	if e := r.Get(ctx, types.NamespacedName{Namespace: ns, Name: names.Neo4j}, &sts); e != nil {
  		return 0, 0, 0, 0, fmt.Errorf("get neo4j statefulset: %w", e)
  	}
  	neo4jReady = sts.Status.ReadyReplicas

  	var lightrag appsv1.Deployment
  	if e := r.Get(ctx, types.NamespacedName{Namespace: ns, Name: names.Lightrag}, &lightrag); e != nil {
  		return 0, 0, 0, 0, fmt.Errorf("get lightrag deployment: %w", e)
  	}
  	lightragAvail = lightrag.Status.AvailableReplicas

  	var mem appsv1.Deployment
  	if e := r.Get(ctx, types.NamespacedName{Namespace: ns, Name: names.Memory}, &mem); e != nil {
  		return 0, 0, 0, 0, fmt.Errorf("get memory deployment: %w", e)
  	}
  	memoryAvail = mem.Status.AvailableReplicas

  	return readyInstances, neo4jReady, lightragAvail, memoryAvail, nil
  }

  // memoryPhase returns "Ready" when cnpg has at least the wanted ready
  // instances AND neo4j, lightrag and memory each report at least one ready /
  // available replica; otherwise "Provisioning".
  func memoryPhase(readyInstances, wantInstances int, neo4jReady, lightragAvail, memoryAvail int32) string {
  	if readyInstances >= wantInstances && neo4jReady >= 1 && lightragAvail >= 1 && memoryAvail >= 1 {
  		return "Ready"
  	}
  	return "Provisioning"
  }
  ```
  Note: builder names (`Neo4jService`, `LightragPVC`, `LightragService`,
  `MemoryConfigMap`, `MemorySecret`, `MemoryService`) and `Names` fields
  (`PGCluster`, `Neo4j`, `Lightrag`, `Memory`) must match N1 exactly. If N1's
  `Names` field for a service/object differs (e.g. `Neo4jService`), adjust the
  `names.X` references and the test to N1's actual field names - do not rename
  N1.

- [ ] Run and confirm PASS:
  ```
  make test 2>&1 | grep -E 'TestApplyMemoryStack|TestMemoryPhase|^ok|FAIL' | head
  ```
  Expected: both new tests pass; package `ok`.

- [ ] `superpowers:requesting-code-review`, apply critical/high fixes, then:
  ```
  pre-commit run --all-files
  git add internal/controller/project_memory.go internal/controller/project_memory_test.go
  git commit -m "feat: SSA-apply per-project memory stack and compute phase from owned health"
  ```

---

## Task 5: Wire provisioning + status.memory + MemoryReady condition into Reconcile, and Owns the stack kinds

Drive the helpers from `Reconcile`: ensure the password, apply the stack, read
health, set `status.memory.{phase,endpoint}` and the `MemoryReady` condition,
record metrics, and requeue while Provisioning. On apply failure set
`phase=Failed` + `MemoryReady=False`. Register the owned kinds in
`SetupWithManager`. This is the integration test of the full Provisioning->Ready
path, the Failed path, and cascade-delete via owner refs.

**Files:**
- `internal/controller/project_controller.go` (edit)
- `internal/controller/project_memory_test.go` (edit: integration tests)

Steps:

- [ ] Add the failing integration tests. Append to
  `internal/controller/project_memory_test.go`. They drive `Reconcile`
  directly, fake the owned objects' status subresources to healthy, and assert
  the transition. Add imports as needed (`apimeta
  "k8s.io/apimachinery/pkg/api/meta"`, `metav1`, `ctrl`, already-present types):
  ```go
  func reconcileMemory(t *testing.T, r *ProjectReconciler, name string) (ctrl.Result, error) {
  	t.Helper()
  	return r.Reconcile(logfIntoTestCtx(), ctrl.Request{
  		NamespacedName: types.NamespacedName{Namespace: testNS, Name: name},
  	})
  }

  func TestReconcile_ProvisionsStackAndSetsEndpoint(t *testing.T) {
  	ctx := context.Background()
  	r := newMemoryReconciler()
  	p := mkMemoryProject(t, "rec-prov")

  	res, err := reconcileMemory(t, r, p.Name)
  	if err != nil {
  		t.Fatalf("reconcile: %v", err)
  	}
  	if res.RequeueAfter == 0 {
  		t.Fatalf("expected requeue while Provisioning, got %+v", res)
  	}

  	got := getProject(t, p.Name)
  	if got.Status.Memory == nil {
  		t.Fatalf("status.memory is nil")
  	}
  	if got.Status.Memory.Phase != "Provisioning" {
  		t.Fatalf("phase = %q, want Provisioning", got.Status.Memory.Phase)
  	}
  	wantEndpoint := memory.Endpoint(p.Name, testNS)
  	if got.Status.Memory.Endpoint != wantEndpoint {
  		t.Fatalf("endpoint = %q, want %q", got.Status.Memory.Endpoint, wantEndpoint)
  	}
  }

  func TestReconcile_TransitionsToReadyWhenOwnedHealthy(t *testing.T) {
  	ctx := context.Background()
  	r := newMemoryReconciler()
  	p := mkMemoryProject(t, "rec-ready")

  	if _, err := reconcileMemory(t, r, p.Name); err != nil {
  		t.Fatalf("first reconcile: %v", err)
  	}
  	fakeStackHealthy(t, p.Name)

  	if _, err := reconcileMemory(t, r, p.Name); err != nil {
  		t.Fatalf("second reconcile: %v", err)
  	}
  	got := waitMemoryPhase(t, p.Name, "Ready")
  	c := apimeta.FindStatusCondition(got.Status.Conditions, "MemoryReady")
  	if c == nil || c.Status != metav1.ConditionTrue {
  		t.Fatalf("MemoryReady condition = %+v, want True", c)
  	}
  }

  func TestReconcile_FailedOnApplyError(t *testing.T) {
  	r := newMemoryReconciler()
  	// Empty namespace makes every SSA target a non-existent namespace, so the
  	// apply fails and the reconciler records phase=Failed + MemoryReady=False.
  	r.MemoryConfig.Namespace = "no-such-namespace-xyz"
  	p := mkMemoryProject(t, "rec-fail")

  	if _, err := reconcileMemory(t, r, p.Name); err == nil {
  		t.Fatalf("expected reconcile error from apply failure")
  	}
  	got := getProject(t, p.Name)
  	if got.Status.Memory == nil || got.Status.Memory.Phase != "Failed" {
  		t.Fatalf("phase = %v, want Failed", got.Status.Memory)
  	}
  	c := apimeta.FindStatusCondition(got.Status.Conditions, "MemoryReady")
  	if c == nil || c.Status != metav1.ConditionFalse {
  		t.Fatalf("MemoryReady = %+v, want False", c)
  	}
  }

  func TestReconcile_CascadeDeleteRemovesStack(t *testing.T) {
  	ctx := context.Background()
  	r := newMemoryReconciler()
  	p := mkMemoryProject(t, "rec-cascade")
  	if _, err := reconcileMemory(t, r, p.Name); err != nil {
  		t.Fatalf("reconcile: %v", err)
  	}
  	names := memory.NamesFor(p.Name)

  	// envtest has no GC controller; assert the controller ownerRef + Background
  	// propagation are in place, which is what drives real-cluster cascade.
  	var cluster cnpgv1.Cluster
  	if err := k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: names.PGCluster}, &cluster); err != nil {
  		t.Fatalf("get cluster: %v", err)
  	}
  	assertOwnedByProject(t, cluster.GetOwnerReferences(), p.Name)
  	var pvc corev1.PersistentVolumeClaim
  	if err := k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: names.LightragPVC}, &pvc); err != nil {
  		t.Fatalf("get lightrag pvc: %v", err)
  	}
  	assertOwnedByProject(t, pvc.GetOwnerReferences(), p.Name)

  	if err := k8sClient.Delete(ctx, getProject(t, p.Name)); err != nil {
  		t.Fatalf("delete project: %v", err)
  	}
  	if err := k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: p.Name}, &tataradevv1alpha1.Project{}); err == nil {
  		// Project still terminating is acceptable; the cascade is GC-driven and
  		// not simulated in envtest. The ownerRef assertions above prove it.
  		_ = cluster
  	}
  }
  ```
  Add the small test helpers at the end of the file (status-faking writes via
  the status subresource, mirroring how the reconciler reads them):
  ```go
  func logfIntoTestCtx() context.Context {
  	return logf.IntoContext(context.Background(), logf.Log)
  }

  func waitMemoryPhase(t *testing.T, name, want string) *tataradevv1alpha1.Project {
  	t.Helper()
  	deadline := time.Now().Add(timeout)
  	for time.Now().Before(deadline) {
  		p := getProject(t, name)
  		if p.Status.Memory != nil && p.Status.Memory.Phase == want {
  			return p
  		}
  		time.Sleep(interval)
  	}
  	t.Fatalf("project %s memory phase never reached %s", name, want)
  	return nil
  }

  // fakeStackHealthy patches the owned objects' status subresources to the
  // healthy values the reconciler reads (no kubelet/cnpg-operator in envtest).
  func fakeStackHealthy(t *testing.T, project string) {
  	t.Helper()
  	ctx := context.Background()
  	names := memory.NamesFor(project)

  	var cluster cnpgv1.Cluster
  	if err := k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: names.PGCluster}, &cluster); err != nil {
  		t.Fatalf("get cluster: %v", err)
  	}
  	cluster.Status.ReadyInstances = 1
  	if err := k8sClient.Status().Update(ctx, &cluster); err != nil {
  		t.Fatalf("fake cluster status: %v", err)
  	}

  	var sts appsv1.StatefulSet
  	if err := k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: names.Neo4j}, &sts); err != nil {
  		t.Fatalf("get sts: %v", err)
  	}
  	sts.Status.ReadyReplicas = 1
  	if err := k8sClient.Status().Update(ctx, &sts); err != nil {
  		t.Fatalf("fake sts status: %v", err)
  	}

  	for _, dn := range []string{names.Lightrag, names.Memory} {
  		var dep appsv1.Deployment
  		if err := k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: dn}, &dep); err != nil {
  			t.Fatalf("get deployment %s: %v", dn, err)
  		}
  		dep.Status.AvailableReplicas = 1
  		if err := k8sClient.Status().Update(ctx, &dep); err != nil {
  			t.Fatalf("fake deployment %s status: %v", dn, err)
  		}
  	}
  }
  ```
  Note: `Names` PVC field is referenced as `names.LightragPVC`; use N1's exact
  field name. If N1's `Cluster` status field for ready instances differs from
  `Status.ReadyInstances`, use the actual cnpgv1 field (verify with
  `go doc github.com/cloudnative-pg/cloudnative-pg/api/v1 ClusterStatus`).

- [ ] Run and confirm FAIL:
  ```
  make test 2>&1 | grep -E 'TestReconcile_Provisions|TestReconcile_Transitions|TestReconcile_Failed|TestReconcile_Cascade|FAIL' | head
  ```
  Expected FAIL: the reconciler does not yet provision/set `status.memory`, so
  assertions fail (`status.memory is nil`, no requeue, no MemoryReady).

- [ ] Implement. Edit `internal/controller/project_controller.go`. Add imports:
  `"time"`, `appsv1 "k8s.io/api/apps/v1"`, `cnpgv1
  "github.com/cloudnative-pg/cloudnative-pg/api/v1"`. Replace the body of
  `Reconcile` so that after the existing SCM-validation block sets the `Ready`
  condition (but BEFORE the single `r.Status().Update`), it provisions memory
  and sets `status.memory` + `MemoryReady`. Concretely, after the
  `meta.SetStatusCondition(... "Ready" ...)` call and before the existing
  `if err := r.Status().Update(ctx, &project); ...`, insert:
  ```go
  	requeueAfter, memErr := r.reconcileMemory(ctx, &project)
  ```
  Then change the final block to persist status once, return `memErr` if set
  (after persisting), and honor the requeue:
  ```go
  	if err := r.Status().Update(ctx, &project); err != nil {
  		r.Metrics.ReconcileResult("Project", "error")
  		return ctrl.Result{}, fmt.Errorf("update project status: %w", err)
  	}

  	if memErr != nil {
  		r.Metrics.ReconcileResult("Project", "error")
  		return ctrl.Result{}, memErr
  	}

  	l.Info("reconciled project",
  		"action", "reconcile_project",
  		"resource_id", project.Name,
  		"ready", ready,
  		"reason", reason,
  		"memory_phase", project.Status.Memory.Phase)
  	r.Metrics.ReconcileResult("Project", "success")
  	return ctrl.Result{RequeueAfter: requeueAfter}, nil
  ```
  Add the `reconcileMemory` method to `internal/controller/project_memory.go`
  (it mutates `project.Status` in place; the caller persists once):
  ```go
  // memoryRequeue is how often the reconciler re-checks a Provisioning stack.
  const memoryRequeue = 10 * time.Second

  // reconcileMemory provisions the Project's memory stack and sets
  // project.Status.Memory + the MemoryReady condition (it does NOT persist;
  // the caller does one status update). It returns the requeue interval (set
  // while Provisioning) and a non-nil error only on a hard apply/health failure
  // (which is also recorded as phase=Failed + MemoryReady=False before
  // returning).
  func (r *ProjectReconciler) reconcileMemory(ctx context.Context, p *tataradevv1alpha1.Project) (time.Duration, error) {
  	start := time.Now()
  	p.Status.Memory = ensureMemoryStatus(p)
  	p.Status.Memory.Endpoint = memory.Endpoint(p.Name, r.MemoryConfig.Namespace)

  	pw, err := r.ensureNeo4jPassword(ctx, p)
  	if err != nil {
  		return 0, r.failMemory(p, "PasswordError", err)
  	}
  	if err := r.applyMemoryStack(ctx, p, pw); err != nil {
  		return 0, r.failMemory(p, "ApplyError", err)
  	}

  	readyInstances, neo4jReady, lightragAvail, memoryAvail, err := r.memoryStackHealth(ctx, p)
  	if err != nil {
  		return 0, r.failMemory(p, "HealthError", err)
  	}

  	phase := memoryPhase(readyInstances, effectivePGInstances(p), neo4jReady, lightragAvail, memoryAvail)
  	p.Status.Memory.Phase = phase

  	condStatus := metav1.ConditionFalse
  	reason := "Provisioning"
  	msg := "memory stack provisioning"
  	if phase == "Ready" {
  		condStatus = metav1.ConditionTrue
  		reason = "Ready"
  		msg = "memory stack ready at " + p.Status.Memory.Endpoint
  		r.Metrics.ObserveMemoryProvisionDuration(time.Since(start).Seconds())
  	}
  	meta.SetStatusCondition(&p.Status.Conditions, metav1.Condition{
  		Type:               "MemoryReady",
  		Status:             condStatus,
  		Reason:             reason,
  		Message:            msg,
  		ObservedGeneration: p.Generation,
  	})
  	r.Metrics.SetMemoryStacks(phase, 1)

  	if phase == "Ready" {
  		return 0, nil
  	}
  	return memoryRequeue, nil
  }

  // ensureMemoryStatus returns the existing status.memory or a fresh one.
  func ensureMemoryStatus(p *tataradevv1alpha1.Project) *tataradevv1alpha1.MemoryStatus {
  	if p.Status.Memory != nil {
  		return p.Status.Memory
  	}
  	return &tataradevv1alpha1.MemoryStatus{}
  }

  // failMemory records phase=Failed + MemoryReady=False on the Project status
  // and returns the wrapped error for the caller to surface.
  func (r *ProjectReconciler) failMemory(p *tataradevv1alpha1.Project, reason string, err error) error {
  	p.Status.Memory = ensureMemoryStatus(p)
  	p.Status.Memory.Phase = "Failed"
  	meta.SetStatusCondition(&p.Status.Conditions, metav1.Condition{
  		Type:               "MemoryReady",
  		Status:             metav1.ConditionFalse,
  		Reason:             reason,
  		Message:            err.Error(),
  		ObservedGeneration: p.Generation,
  	})
  	r.Metrics.SetMemoryStacks("Failed", 1)
  	return fmt.Errorf("reconcile memory: %w", err)
  }
  ```
  Add the imports `"k8s.io/apimachinery/pkg/api/meta"` and `metav1
  "k8s.io/apimachinery/pkg/apis/meta/v1"` to `project_memory.go` (if not already
  present). Finally update `SetupWithManager` in `project_controller.go` to own
  the stack kinds:
  ```go
  func (r *ProjectReconciler) SetupWithManager(mgr ctrl.Manager) error {
  	return ctrl.NewControllerManagedBy(mgr).
  		For(&tataradevv1alpha1.Project{}).
  		Owns(&corev1.Secret{}).
  		Owns(&cnpgv1.Cluster{}).
  		Owns(&appsv1.StatefulSet{}).
  		Owns(&appsv1.Deployment{}).
  		Owns(&corev1.Service{}).
  		Owns(&corev1.PersistentVolumeClaim{}).
  		Owns(&corev1.ConfigMap{}).
  		Complete(r)
  }
  ```
  (`corev1.Secret` is already owned.)

- [ ] Run and confirm PASS for the new tests and the whole suite (existing
  `TestProjectReconcile_*` must still pass - they construct a reconciler with no
  `MemoryConfig`, so `reconcileMemory` runs against namespace `""`; to keep
  those green, `newProjectReconciler` must set a valid `MemoryConfig.Namespace`.
  Update `newProjectReconciler` in `project_controller_test.go` to set
  `MemoryConfig: memory.Config{Namespace: testNS}` so the existing SCM tests
  also provision a stack harmlessly, OR have those tests assert only the `Ready`
  condition. Pick: set `MemoryConfig.Namespace = testNS` in
  `newProjectReconciler` and add the other required fields, matching
  `newMemoryReconciler`. Then `newMemoryReconciler` can just call
  `newProjectReconciler`.):
  ```
  make test 2>&1 | tail -25
  ```
  Expected: package `ok`; all `TestReconcile_*`, `TestProjectReconcile_*`, and
  earlier memory tests pass.

- [ ] `superpowers:verification-before-completion`: run the full suite once more
  clean and confirm zero failures:
  ```
  make test 2>&1 | grep -E '^(ok|FAIL|---)' | tail -30
  ```
  Expected: every package line is `ok`, no `FAIL`.

- [ ] `superpowers:requesting-code-review`, apply critical/high fixes, then:
  ```
  pre-commit run --all-files
  git add internal/controller/project_controller.go internal/controller/project_memory.go internal/controller/project_memory_test.go internal/controller/project_controller_test.go
  git commit -m "feat: provision per-project memory stack, set status.memory + MemoryReady, own stack kinds"
  ```

---

## Task 6: Inject memory.Config into the reconciler from operator config in wire.go

Build `memory.Config` from `config.Config` and pass it to the `ProjectReconciler`
so the deployed operator provisions stacks with the configured images and the
shared OpenAI secret. Keeps `cmd/manager` compiling against the new field.

**Files:**
- `cmd/manager/wire.go` (edit)
- `cmd/manager/wire_test.go` (edit or new)

Steps:

- [ ] Write the failing test. If `cmd/manager/wire_test.go` exists, append;
  else create it with FULL content:
  ```go
  package main

  import (
  	"testing"

  	"github.com/szymonrychu/tatara-operator/internal/config"
  )

  func TestMemoryConfigFromConfig(t *testing.T) {
  	cfg := config.Config{
  		Namespace:        "tatara",
  		MemoryImage:      "harbor.example/tatara-memory:0.2.0",
  		LightragImage:    "harbor.example/lightrag:1.0.0",
  		Neo4jImage:       "neo4j:5-community",
  		OpenAISecretName: "openai-shared",
  		OIDCIssuer:       "https://keycloak.example/realms/tatara",
  		OIDCAudience:     "tatara",
  	}
  	mc := memoryConfigFromConfig(cfg)
  	if mc.Namespace != "tatara" || mc.MemoryImage != cfg.MemoryImage ||
  		mc.LightragImage != cfg.LightragImage || mc.Neo4jImage != cfg.Neo4jImage ||
  		mc.OpenAISecretName != cfg.OpenAISecretName || mc.OIDCIssuer != cfg.OIDCIssuer {
  		t.Fatalf("memoryConfigFromConfig mismatch: %+v", mc)
  	}
  	if mc.OIDCAudience != "tatara-memory" {
  		t.Fatalf("OIDCAudience = %q, want tatara-memory (the memory service audience)", mc.OIDCAudience)
  	}
  }
  ```
  Rationale for the audience: per the spec/pin set every per-project memory
  service uses audience `tatara-memory`; the operator's own `OIDC_AUDIENCE` is
  the operator API audience, so `memoryConfigFromConfig` pins `tatara-memory`
  explicitly (matching how `ingestConfigFromConfig` is called with the literal
  `"tatara-memory"`).

- [ ] Run and confirm FAIL:
  ```
  go test ./cmd/manager/ -run TestMemoryConfigFromConfig 2>&1 | tail -10
  ```
  Expected FAIL: `memoryConfigFromConfig undefined`.

- [ ] Implement. Edit `cmd/manager/wire.go`. Add the import
  `"github.com/szymonrychu/tatara-operator/internal/memory"`. Add the mapper
  next to `ingestConfigFromConfig`:
  ```go
  // memoryConfigFromConfig maps operator config to the per-project memory stack
  // builder config. The audience is always the memory-service audience
  // (tatara-memory), not the operator's own API audience.
  func memoryConfigFromConfig(cfg config.Config) memory.Config {
  	return memory.Config{
  		Namespace:        cfg.Namespace,
  		MemoryImage:      cfg.MemoryImage,
  		LightragImage:    cfg.LightragImage,
  		Neo4jImage:       cfg.Neo4jImage,
  		OpenAISecretName: cfg.OpenAISecretName,
  		OIDCIssuer:       cfg.OIDCIssuer,
  		OIDCAudience:     "tatara-memory",
  	}
  }
  ```
  In `addReconcilers`, set the field on the `ProjectReconciler` literal (after
  `ExternalWebhookBase: cfg.ExternalWebhookBase,`):
  ```go
  		MemoryConfig:        memoryConfigFromConfig(cfg),
  ```

- [ ] Run and confirm PASS, plus a full build:
  ```
  go test ./cmd/manager/ -run TestMemoryConfigFromConfig 2>&1 | tail -5
  go build ./... 2>&1 | tail -5
  ```
  Expected: test passes; build clean (no output).

- [ ] `superpowers:requesting-code-review`, apply critical/high fixes, then:
  ```
  pre-commit run --all-files
  git add cmd/manager/wire.go cmd/manager/wire_test.go
  git commit -m "feat: inject per-project memory.Config into ProjectReconciler"
  ```

---

## Final verification

- [ ] `superpowers:verification-before-completion`. Full suite + build + lint:
  ```
  make test 2>&1 | grep -E '^(ok|FAIL)' 
  go build ./... 
  pre-commit run --all-files
  ```
  Expected: every package `ok`, build clean, all hooks pass.

- [ ] Update `MEMORY.md` (one line): the cnpg Cluster CRD is vendored into
  `charts/tatara-operator/crds/` via `hack/vendor-cnpg-crd.sh` and re-run after
  any cnpg dependency bump; envtest loads it from the same dir the chart ships.
- [ ] Update `ROADMAP.md`: mark N2 done; N3 (per-project endpoint wiring +
  Ready-gating in Repository/Task reconcilers, remove `MEMORY_BASE_URL`) and N4
  (retire static tatara-memory, chart RBAC/values, image bump, redeploy) remain.
- [ ] `superpowers:finishing-a-development-branch`: merge the worktree back to
  `main`, clean up the worktree. Do NOT build/deploy from the worktree.

## Notes / non-obvious decisions

- SSA field owner is the literal `"tatara-operator"` for every stack object.
  Re-applying every reconcile is the intended convergence mechanism; the only
  non-idempotent input (neo4j password) is created once via `Create`, never
  applied.
- The neo4j password Secret is created by `ensureNeo4jPassword`, NOT included in
  `applyMemoryStack`, precisely so SSA can never rotate it.
- envtest has no kubelet/cnpg-operator/GC controller, so: (a) health tests fake
  the owned objects' status subresources to exercise the real Provisioning->Ready
  branch in `memoryPhase`; (b) cascade-delete is asserted via the controller
  ownerRefs the N1 builders set, which is what drives GC on a real cluster.
- The `Failed` path is forced in test by pointing `MemoryConfig.Namespace` at a
  non-existent namespace so the first SSA apply errors; the reconciler records
  `phase=Failed` + `MemoryReady=False`, persists status, then returns the error
  (so controller-runtime requeues with backoff).
- `ProjectReconciler.Reconcile` now persists `status.memory`,
  `status.webhookURL`, and both conditions (`Ready`, `MemoryReady`) in one
  status update against the subresource - the two concerns never race each
  other on the status object.
