# tatara-operator M1 (Project + Repository reconcilers + ingest Job) Implementation Plan

> For agentic workers: execute tasks top to bottom. Each task is a complete
> TDD cycle: write the failing test exactly as given, run it and confirm the
> expected FAIL, write the minimal implementation exactly as given, run it and
> confirm PASS, then commit. Do not skip the run steps. Do not "improve"
> beyond the given code. Copy code verbatim; paths and commands are exact.
> Run every command from the repo root `~/Documents/tatara-operator` unless
> stated otherwise. Work in a worktree off `main` per hard rule 10; never
> build or deploy from the worktree.

## Goal

Make `tatara-operator` ingest repositories into `tatara-memory`. Deliver two
reconcilers and a Job builder:

1. `ProjectReconciler` validates the Project's SCM secret has `token` +
   `webhookSecret`, renders `status.webhookURL`, and sets conditions.
2. `internal/ingest/job.go` builds the `*batchv1.Job` that clones a repo with
   the SCM token and runs `tatara-ingest`.
3. `RepositoryReconciler` spawns that Job per the re-ingest trigger contract
   (full when `lastIngestedCommit==""`; incremental via `--since` when the
   `tatara.dev/reingest-requested` annotation is newer than
   `status.lastIngestTime`), guards concurrency via `status.jobName`, and on
   Job success reads the resolved HEAD SHA from a result ConfigMap to set
   `status.lastIngestedCommit`.
4. Both reconcilers are wired into `cmd/manager/main.go`.
5. Reconciles and ingest Job durations are recorded in the M0 metrics.

This closes production-readiness gaps 1 and 2 (nothing populates the graph;
ingest needs clone + Go toolchain).

## Architecture

CRDs are the source of truth (M0 created the `tatara.dev/v1alpha1` types
`Project`, `Repository`, `Task`, `Subtask` with deepcopy). M1 adds behavior:

- `ProjectReconciler` is pure validation + status rendering. No child objects.
- `RepositoryReconciler` owns a single in-flight ingest `Job` per Repository.
  The Job has two phases in one Pod: an init container clones the repo into an
  `emptyDir` using the Project SCM token; the main container runs
  `tatara-ingest` against `MEMORY_BASE_URL` with an OIDC bearer minted from
  the operator's client-credentials, and as its final step resolves the
  cloned HEAD SHA and writes it into a per-Repository result ConfigMap
  (`<repo>-ingest-result`, key `sha`). On Job success the reconciler reads
  that ConfigMap, sets `status.lastIngestedCommit`, `status.lastIngestTime`,
  `status.phase=Ingested`, and clears `status.jobName`.

Result-SHA design decision (resolving the spec's open question): the ingest
container, after clone, writes `git rev-parse HEAD` into the result ConfigMap
via the in-cluster Kubernetes API. This is the simplest correct, deterministic
mechanism: the reconciler never parses pod logs and never trusts a sentinel.
The Job runs under a dedicated ServiceAccount (`tatara-ingest`, created by the
M6 chart) whose Role grants `get`,`create`,`update`,`patch` on ConfigMaps in
the `tatara` namespace, scoped by RBAC. The reconciler pre-creates an empty
`<repo>-ingest-result` ConfigMap (owner-ref Repository) before launching the
Job so the Job only needs `patch`/`update`, and the reconciler always has a
known object to read. `MEMORY.md` records the RBAC requirement so M6 wires the
ServiceAccount + Role.

Trigger/concurrency contract (from the pin set, implemented verbatim here):

```
spawn ingest Job when:
  status.lastIngestedCommit == ""                         -> full (no --since)
  OR annotation tatara.dev/reingest-requested (RFC3339)
     is newer than status.lastIngestTime                  -> incremental (--since <sha>)
status.jobName != "" and that Job is still active         -> requeue, do not launch
Job is owner-referenced to the Repository.
on Job Complete: read <repo>-ingest-result.data["sha"],
  set status.lastIngestedCommit, status.lastIngestTime=now,
  status.phase=Ingested, status.jobName="".
on Job Failed: status.phase=Failed, status.jobName="", set Failed condition.
```

## Tech Stack

- Go `1.25.x` (pinned in `go.mod` by M0), kubebuilder / controller-runtime.
- `sigs.k8s.io/controller-runtime` reconcilers; `sigs.k8s.io/controller-runtime/pkg/envtest` for reconciler tests.
- `k8s.io/api/batch/v1`, `k8s.io/api/core/v1`, `k8s.io/apimachinery`.
- `github.com/prometheus/client_golang` (M0 `internal/obs`).
- `apimachinery/pkg/api/meta` + `metav1.Condition` for `status.conditions`.
- Tests: stdlib `testing`, table-driven with `t.Run`, errors wrapped `%w`,
  mirroring `tatara-memory/internal`.

Assumptions stated up front:
- M0 has created `api/v1alpha1` types with the spec field names exactly, the
  generated deepcopy, the scheme registration in `api/v1alpha1/groupversion_info.go`,
  `internal/config/config.go` exposing the pin-set config keys, and
  `internal/obs` providing `NewLogger` + `PromRegistry`. If any M1 task fails
  to compile because an M0 symbol is missing, stop and reconcile with the M0
  plan rather than inventing the symbol.
- Condition types used: `Ready` (Project), `Ingested` / `Failed` (Repository).
- M1 owns the M0-defined metrics `operator_reconcile_total{kind,result}` and
  `operator_ingest_job_duration_seconds`; if M0 has not yet registered them,
  Task 1 adds them to `internal/obs/metrics.go` (the first task that needs
  them) and the rest of M1 reuses them.

---

## Task 1: Operator metrics in `internal/obs`

Adds the two M1 metrics as package-level collectors registered on the obs
registry, with thin helpers the reconcilers call. If M0 already added
identical collectors, skip the duplicate registration and keep only the
helpers (the test still asserts the helpers exist and emit).

Files:
- Create: `internal/obs/operator_metrics.go`
- Test: `internal/obs/operator_metrics_test.go`

- [ ] Write the failing test (FULL code):

`internal/obs/operator_metrics_test.go`
```go
package obs

import (
	"strings"
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"
)

func TestReconcileTotal(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := NewOperatorMetrics(reg)

	m.ReconcileResult("Project", "success")
	m.ReconcileResult("Project", "success")
	m.ReconcileResult("Repository", "error")

	got := testutil.ToFloat64(m.reconcileTotal.WithLabelValues("Project", "success"))
	if got != 2 {
		t.Fatalf("Project/success = %v, want 2", got)
	}
	got = testutil.ToFloat64(m.reconcileTotal.WithLabelValues("Repository", "error"))
	if got != 1 {
		t.Fatalf("Repository/error = %v, want 1", got)
	}
}

func TestIngestJobDuration(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := NewOperatorMetrics(reg)

	m.ObserveIngestJobDuration(12.5)

	mfs, err := reg.Gather()
	if err != nil {
		t.Fatalf("gather: %v", err)
	}
	var found bool
	for _, mf := range mfs {
		if mf.GetName() == "operator_ingest_job_duration_seconds" {
			found = true
			if got := mf.GetMetric()[0].GetHistogram().GetSampleCount(); got != 1 {
				t.Fatalf("sample count = %d, want 1", got)
			}
		}
	}
	if !found {
		t.Fatal("operator_ingest_job_duration_seconds not registered")
	}
}

func TestOperatorMetricsNamesStable(t *testing.T) {
	reg := prometheus.NewRegistry()
	_ = NewOperatorMetrics(reg)
	mfs, _ := reg.Gather()
	want := map[string]bool{
		"operator_reconcile_total":             false,
		"operator_ingest_job_duration_seconds": false,
	}
	for _, mf := range mfs {
		if _, ok := want[mf.GetName()]; ok {
			want[mf.GetName()] = true
		}
	}
	for name, seen := range want {
		if !seen {
			t.Errorf("metric %q not registered", name)
		}
	}
	_ = strings.TrimSpace
}
```

- [ ] Run and expect FAIL:

```
go test ./internal/obs/ -run 'TestReconcileTotal|TestIngestJobDuration|TestOperatorMetricsNamesStable' -v
```

Expected: build failure `undefined: NewOperatorMetrics` (and `ObserveIngestJobDuration`, `ReconcileResult`, `reconcileTotal`).

- [ ] Write the minimal implementation (FULL code):

`internal/obs/operator_metrics.go`
```go
package obs

import "github.com/prometheus/client_golang/prometheus"

// OperatorMetrics holds the reconciler-facing Prometheus collectors for the
// tatara-operator. Construct one with NewOperatorMetrics and pass it to the
// reconcilers.
type OperatorMetrics struct {
	reconcileTotal    *prometheus.CounterVec
	ingestJobDuration prometheus.Histogram
}

// NewOperatorMetrics registers the operator collectors on reg and returns the
// bundle. Names and labels are pinned by the shared-contracts pin set.
func NewOperatorMetrics(reg prometheus.Registerer) *OperatorMetrics {
	m := &OperatorMetrics{
		reconcileTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "operator_reconcile_total",
			Help: "Total reconcile outcomes by kind and result.",
		}, []string{"kind", "result"}),
		ingestJobDuration: prometheus.NewHistogram(prometheus.HistogramOpts{
			Name:    "operator_ingest_job_duration_seconds",
			Help:    "Wall-clock duration of completed ingest Jobs.",
			Buckets: prometheus.ExponentialBuckets(5, 2, 8),
		}),
	}
	reg.MustRegister(m.reconcileTotal, m.ingestJobDuration)
	return m
}

// ReconcileResult increments operator_reconcile_total for the given kind and
// result ("success" or "error").
func (m *OperatorMetrics) ReconcileResult(kind, result string) {
	m.reconcileTotal.WithLabelValues(kind, result).Inc()
}

// ObserveIngestJobDuration records the wall-clock seconds a completed ingest
// Job took.
func (m *OperatorMetrics) ObserveIngestJobDuration(seconds float64) {
	m.ingestJobDuration.Observe(seconds)
}
```

- [ ] Run and expect PASS:

```
go test ./internal/obs/ -run 'TestReconcileTotal|TestIngestJobDuration|TestOperatorMetricsNamesStable' -v
```

Expected: `ok  github.com/szymonrychu/tatara-operator/internal/obs`.

- [ ] Commit:

```
git add internal/obs/operator_metrics.go internal/obs/operator_metrics_test.go
git commit -m "feat: add operator reconcile + ingest-job-duration metrics"
```

---

## Task 2: Ingest Job builder (`internal/ingest/job.go`)

Pure builder: given a Repository, its owning Project, an optional `--since`
SHA, and the operator config, return the `*batchv1.Job`. No cluster access in
this function. The Job is one Pod with an init container (`git clone` using
the SCM token into an `emptyDir`) and a main container from `INGESTER_IMAGE`
that runs `tatara-ingest` and then writes the resolved HEAD SHA into the
result ConfigMap via the in-cluster API. Env keys match the ingester's
UPPER_SNAKE-of-kebab convention (`BASE_URL`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`,
`OIDC_CLIENT_SECRET`, `OIDC_AUDIENCE`).

Files:
- Create: `internal/ingest/job.go`
- Test: `internal/ingest/job_test.go`

- [ ] Write the failing test (FULL code):

`internal/ingest/job_test.go`
```go
package ingest

import (
	"strings"
	"testing"

	tataradevv1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func testProject() *tataradevv1alpha1.Project {
	p := &tataradevv1alpha1.Project{}
	p.Name = "acme"
	p.Namespace = "tatara"
	p.Spec.ScmSecretRef = "acme-scm"
	return p
}

func testRepository() *tataradevv1alpha1.Repository {
	r := &tataradevv1alpha1.Repository{}
	r.Name = "widgets"
	r.Namespace = "tatara"
	r.UID = "repo-uid-123"
	r.Spec.ProjectRef = "acme"
	r.Spec.URL = "https://github.com/acme/widgets.git"
	r.Spec.DefaultBranch = "main"
	return r
}

func testConfig() Config {
	return Config{
		IngesterImage:    "registry.example/ingester:1.2.3",
		MemoryBaseURL:    "http://tatara-memory.tatara.svc:8080",
		OIDCIssuer:       "https://kc.example/realms/tatara",
		OIDCClientID:     "tatara-operator",
		OIDCClientSecret: "s3cr3t",
		OIDCAudience:     "tatara-memory",
		Namespace:        "tatara",
	}
}

func envValue(c corev1.Container, key string) string {
	for _, e := range c.Env {
		if e.Name == key {
			return e.Value
		}
	}
	return ""
}

func TestBuildJob_FullIngest(t *testing.T) {
	job := BuildJob(testProject(), testRepository(), "", testConfig())

	if job.Namespace != "tatara" {
		t.Errorf("namespace = %q, want tatara", job.Namespace)
	}
	if !strings.HasPrefix(job.Name, "widgets-ingest-") {
		t.Errorf("job name = %q, want prefix widgets-ingest-", job.Name)
	}
	if got := job.Spec.Template.Spec.RestartPolicy; got != corev1.RestartPolicyNever {
		t.Errorf("restartPolicy = %q, want Never", got)
	}
	if job.Spec.BackoffLimit == nil || *job.Spec.BackoffLimit != 2 {
		t.Errorf("backoffLimit = %v, want 2", job.Spec.BackoffLimit)
	}

	if len(job.OwnerReferences) != 1 {
		t.Fatalf("ownerReferences = %d, want 1", len(job.OwnerReferences))
	}
	or := job.OwnerReferences[0]
	if or.Kind != "Repository" || or.Name != "widgets" || string(or.UID) != "repo-uid-123" {
		t.Errorf("ownerRef = %+v, want Repository/widgets/repo-uid-123", or)
	}
	if or.Controller == nil || !*or.Controller {
		t.Error("ownerRef.Controller should be true")
	}

	initCs := job.Spec.Template.Spec.InitContainers
	if len(initCs) != 1 {
		t.Fatalf("init containers = %d, want 1", len(initCs))
	}
	clone := initCs[0]
	cloneCmd := strings.Join(clone.Command, " ") + " " + strings.Join(clone.Args, " ")
	if !strings.Contains(cloneCmd, "https://github.com/acme/widgets.git") {
		t.Errorf("clone cmd missing url: %q", cloneCmd)
	}
	if !strings.Contains(cloneCmd, "--branch main") {
		t.Errorf("clone cmd missing branch: %q", cloneCmd)
	}

	cs := job.Spec.Template.Spec.Containers
	if len(cs) != 1 {
		t.Fatalf("containers = %d, want 1", len(cs))
	}
	main := cs[0]
	if main.Image != "registry.example/ingester:1.2.3" {
		t.Errorf("image = %q, want registry.example/ingester:1.2.3", main.Image)
	}

	cmd := strings.Join(main.Command, " ") + " " + strings.Join(main.Args, " ")
	if !strings.Contains(cmd, "tatara-ingest --repo-root /workspace/repo --repo-name widgets --base-url http://tatara-memory.tatara.svc:8080") {
		t.Errorf("ingest cmd wrong: %q", cmd)
	}
	if strings.Contains(cmd, "--since") {
		t.Errorf("full ingest must not pass --since: %q", cmd)
	}
	if !strings.Contains(cmd, "widgets-ingest-result") {
		t.Errorf("ingest cmd must write result configmap: %q", cmd)
	}

	if v := envValue(main, "BASE_URL"); v != "http://tatara-memory.tatara.svc:8080" {
		t.Errorf("BASE_URL = %q", v)
	}
	if v := envValue(main, "OIDC_ISSUER"); v != "https://kc.example/realms/tatara" {
		t.Errorf("OIDC_ISSUER = %q", v)
	}
	if v := envValue(main, "OIDC_CLIENT_ID"); v != "tatara-operator" {
		t.Errorf("OIDC_CLIENT_ID = %q", v)
	}
	if v := envValue(main, "OIDC_CLIENT_SECRET"); v != "s3cr3t" {
		t.Errorf("OIDC_CLIENT_SECRET = %q", v)
	}
	if v := envValue(main, "OIDC_AUDIENCE"); v != "tatara-memory" {
		t.Errorf("OIDC_AUDIENCE = %q", v)
	}
}

func TestBuildJob_IncrementalIngest(t *testing.T) {
	job := BuildJob(testProject(), testRepository(), "abc1234", testConfig())
	main := job.Spec.Template.Spec.Containers[0]
	cmd := strings.Join(main.Command, " ") + " " + strings.Join(main.Args, " ")
	if !strings.Contains(cmd, "--since abc1234") {
		t.Errorf("incremental ingest must pass --since abc1234: %q", cmd)
	}
}

func TestBuildJob_SCMTokenFromSecret(t *testing.T) {
	job := BuildJob(testProject(), testRepository(), "", testConfig())
	clone := job.Spec.Template.Spec.InitContainers[0]
	var ref *corev1.EnvVarSource
	for _, e := range clone.Env {
		if e.Name == "SCM_TOKEN" {
			ref = e.ValueFrom
		}
	}
	if ref == nil || ref.SecretKeyRef == nil {
		t.Fatal("clone container must source SCM_TOKEN from a secret")
	}
	if ref.SecretKeyRef.Name != "acme-scm" || ref.SecretKeyRef.Key != "token" {
		t.Errorf("SCM_TOKEN secretKeyRef = %s/%s, want acme-scm/token",
			ref.SecretKeyRef.Name, ref.SecretKeyRef.Key)
	}
}

func TestBuildJob_SharedWorkspaceVolume(t *testing.T) {
	job := BuildJob(testProject(), testRepository(), "", testConfig())
	ps := job.Spec.Template.Spec
	var hasEmptyDir bool
	for _, v := range ps.Volumes {
		if v.Name == "workspace" && v.EmptyDir != nil {
			hasEmptyDir = true
		}
	}
	if !hasEmptyDir {
		t.Error("pod must have an emptyDir volume named workspace")
	}
	mounted := func(c corev1.Container) bool {
		for _, m := range c.VolumeMounts {
			if m.Name == "workspace" && m.MountPath == "/workspace" {
				return true
			}
		}
		return false
	}
	if !mounted(ps.InitContainers[0]) {
		t.Error("init container must mount workspace at /workspace")
	}
	if !mounted(ps.Containers[0]) {
		t.Error("main container must mount workspace at /workspace")
	}
	_ = metav1.Now
	_ = batchv1.Job{}
}
```

- [ ] Run and expect FAIL:

```
go test ./internal/ingest/ -v
```

Expected: build failure `undefined: BuildJob` and `undefined: Config`.

- [ ] Write the minimal implementation (FULL code):

`internal/ingest/job.go`
```go
// Package ingest builds the Kubernetes Job that clones a repository and runs
// tatara-ingest against tatara-memory.
package ingest

import (
	"fmt"

	tataradevv1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/rand"
)

// Config is the subset of operator configuration the Job builder needs.
type Config struct {
	IngesterImage    string
	MemoryBaseURL    string
	OIDCIssuer       string
	OIDCClientID     string
	OIDCClientSecret string
	OIDCAudience     string
	Namespace        string
}

// ResultConfigMapName returns the name of the ConfigMap an ingest Job patches
// with the resolved HEAD SHA for the given Repository.
func ResultConfigMapName(repo *tataradevv1alpha1.Repository) string {
	return repo.Name + "-ingest-result"
}

const (
	workspaceVolume = "workspace"
	workspaceMount  = "/workspace"
	repoDir         = "/workspace/repo"
)

// BuildJob returns the *batchv1.Job that ingests repo for project. When since
// is non-empty the ingest is incremental (--since since); otherwise it is a
// full ingest. The Job is owner-referenced to repo. It clones with the
// Project SCM token in an init container into an emptyDir, runs tatara-ingest
// in the main container, then writes the cloned HEAD SHA into the repo's
// result ConfigMap via the in-cluster API.
func BuildJob(project *tataradevv1alpha1.Project, repo *tataradevv1alpha1.Repository, since string, cfg Config) *batchv1.Job {
	backoff := int32(2)
	ttl := int32(3600)
	controller := true

	cloneCmd := fmt.Sprintf(
		"set -e; git clone --depth 1 --branch %s "+
			"https://x-access-token:${SCM_TOKEN}@${REPO_HOST_PATH} %s",
		repo.Spec.DefaultBranch, repoDir)

	ingestArgs := fmt.Sprintf(
		"tatara-ingest --repo-root %s --repo-name %s --base-url %s",
		repoDir, repo.Name, cfg.MemoryBaseURL)
	if since != "" {
		ingestArgs += " --since " + since
	}
	// After a successful ingest, resolve HEAD and patch the result ConfigMap
	// via the in-cluster API (the Job ServiceAccount has patch on it).
	resultCM := ResultConfigMapName(repo)
	mainScript := fmt.Sprintf(
		"set -e; %s; "+
			"SHA=$(git -C %s rev-parse HEAD); "+
			"kubectl -n %s patch configmap %s --type merge "+
			"-p \"{\\\"data\\\":{\\\"sha\\\":\\\"${SHA}\\\"}}\"",
		ingestArgs, repoDir, cfg.Namespace, resultCM)

	repoHostPath := repo.Spec.URL
	repoHostPath = trimScheme(repoHostPath)

	return &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{
			Name:      repo.Name + "-ingest-" + rand.String(5),
			Namespace: cfg.Namespace,
			Labels: map[string]string{
				"app.kubernetes.io/name":      "tatara-operator",
				"app.kubernetes.io/component": "ingest",
				"tatara.dev/managed-by":       "tatara-operator",
				"tatara.dev/repository":       repo.Name,
			},
			OwnerReferences: []metav1.OwnerReference{{
				APIVersion: tataradevv1alpha1.GroupVersion.String(),
				Kind:       "Repository",
				Name:       repo.Name,
				UID:        repo.UID,
				Controller: &controller,
			}},
		},
		Spec: batchv1.JobSpec{
			BackoffLimit:            &backoff,
			TTLSecondsAfterFinished: &ttl,
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{
					Labels: map[string]string{
						"app.kubernetes.io/name":      "tatara-operator",
						"app.kubernetes.io/component": "ingest",
						"tatara.dev/managed-by":       "tatara-operator",
						"tatara.dev/repository":       repo.Name,
					},
				},
				Spec: corev1.PodSpec{
					RestartPolicy:      corev1.RestartPolicyNever,
					ServiceAccountName: "tatara-ingest",
					Volumes: []corev1.Volume{{
						Name:         workspaceVolume,
						VolumeSource: corev1.VolumeSource{EmptyDir: &corev1.EmptyDirVolumeSource{}},
					}},
					InitContainers: []corev1.Container{{
						Name:    "clone",
						Image:   cfg.IngesterImage,
						Command: []string{"/bin/sh", "-c"},
						Args:    []string{cloneCmd},
						Env: []corev1.EnvVar{
							{
								Name: "SCM_TOKEN",
								ValueFrom: &corev1.EnvVarSource{
									SecretKeyRef: &corev1.SecretKeySelector{
										LocalObjectReference: corev1.LocalObjectReference{Name: project.Spec.ScmSecretRef},
										Key:                  "token",
									},
								},
							},
							{Name: "REPO_HOST_PATH", Value: repoHostPath},
						},
						VolumeMounts: []corev1.VolumeMount{{Name: workspaceVolume, MountPath: workspaceMount}},
					}},
					Containers: []corev1.Container{{
						Name:    "ingest",
						Image:   cfg.IngesterImage,
						Command: []string{"/bin/sh", "-c"},
						Args:    []string{mainScript},
						Env: []corev1.EnvVar{
							{Name: "BASE_URL", Value: cfg.MemoryBaseURL},
							{Name: "OIDC_ISSUER", Value: cfg.OIDCIssuer},
							{Name: "OIDC_CLIENT_ID", Value: cfg.OIDCClientID},
							{Name: "OIDC_CLIENT_SECRET", Value: cfg.OIDCClientSecret},
							{Name: "OIDC_AUDIENCE", Value: cfg.OIDCAudience},
						},
						VolumeMounts: []corev1.VolumeMount{{Name: workspaceVolume, MountPath: workspaceMount}},
					}},
				},
			},
		},
	}
}

// trimScheme removes a leading https:// or http:// from a URL so it can be
// reassembled with embedded credentials.
func trimScheme(u string) string {
	for _, p := range []string{"https://", "http://"} {
		if len(u) >= len(p) && u[:len(p)] == p {
			return u[len(p):]
		}
	}
	return u
}
```

Note on the SCM secret value: the builder injects the token via env
`SCM_TOKEN` (Secret key `token`); do not log it. The clone URL embeds
`x-access-token:${SCM_TOKEN}` which works for both GitHub and GitLab HTTPS.

- [ ] Run and expect PASS:

```
go test ./internal/ingest/ -v
```

Expected: `ok  github.com/szymonrychu/tatara-operator/internal/ingest` with all five tests passing.

- [ ] Commit:

```
git add internal/ingest/job.go internal/ingest/job_test.go
git commit -m "feat: build ingest Job (clone + tatara-ingest + result configmap)"
```

---

## Task 3: ProjectReconciler (envtest)

Validates `scmSecretRef` exists and has keys `token` + `webhookSecret`,
renders `status.webhookURL` from `EXTERNAL_WEBHOOK_BASE` + project name, sets
the `Ready` condition. This is the FIRST reconciler task, so it carries the
FULL envtest suite setup (`suite_test.go`). Later reconciler tasks reuse the
shared `k8sClient`/`testEnv` from this suite.

Files:
- Create: `internal/controller/suite_test.go`
- Create: `internal/controller/project_controller.go`
- Test: `internal/controller/project_controller_test.go`

- [ ] Write the failing test (FULL code).

First the suite (shared by all reconciler tests in the package):

`internal/controller/suite_test.go`
```go
package controller

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	tataradevv1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/client-go/kubernetes/scheme"
	"k8s.io/client-go/rest"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/envtest"
)

var (
	testEnv   *envtest.Environment
	cfg       *rest.Config
	k8sClient client.Client
)

const (
	timeout  = 10 * time.Second
	interval = 250 * time.Millisecond
	testNS   = "tatara"
)

// TestMain boots a single envtest control plane for the whole controller
// package, registers the tatara.dev scheme and core types, creates the test
// namespace, and tears the control plane down at the end.
func TestMain(m *testing.M) {
	code := func() int {
		testEnv = &envtest.Environment{
			CRDDirectoryPaths:     []string{filepath.Join("..", "..", "config", "crd", "bases")},
			ErrorIfCRDPathMissing: true,
		}
		var err error
		cfg, err = testEnv.Start()
		if err != nil {
			panic("start envtest: " + err.Error())
		}
		defer func() { _ = testEnv.Stop() }()

		if err := tataradevv1alpha1.AddToScheme(scheme.Scheme); err != nil {
			panic("add scheme: " + err.Error())
		}

		k8sClient, err = client.New(cfg, client.Options{Scheme: scheme.Scheme})
		if err != nil {
			panic("new client: " + err.Error())
		}

		ns := &corev1.Namespace{}
		ns.Name = testNS
		if err := k8sClient.Create(context.Background(), ns); err != nil {
			panic("create namespace: " + err.Error())
		}

		return m.Run()
	}()
	osExit(code)
}
```

Add a tiny indirection so the suite is testable without importing os at the
top in a way that conflicts; create `internal/controller/exit_test.go`:

`internal/controller/exit_test.go`
```go
package controller

import "os"

// osExit is a thin wrapper so TestMain has a single exit point.
var osExit = os.Exit
```

Now the Project test:

`internal/controller/project_controller_test.go`
```go
package controller

import (
	"context"
	"testing"
	"time"

	tataradevv1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/obs"
	"github.com/prometheus/client_golang/prometheus"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
)

func newProjectReconciler() *ProjectReconciler {
	return &ProjectReconciler{
		Client:              k8sClient,
		Scheme:              k8sClient.Scheme(),
		Metrics:             obs.NewOperatorMetrics(prometheus.NewRegistry()),
		ExternalWebhookBase: "https://tatara.example/operator/webhooks",
	}
}

func reconcileProject(t *testing.T, name string) (ctrl.Result, error) {
	t.Helper()
	r := newProjectReconciler()
	return r.Reconcile(logf.IntoContext(context.Background(), logf.Log), ctrl.Request{
		NamespacedName: types.NamespacedName{Namespace: testNS, Name: name},
	})
}

func mkSecret(t *testing.T, name string, data map[string][]byte) {
	t.Helper()
	s := &corev1.Secret{}
	s.Name = name
	s.Namespace = testNS
	s.Data = data
	if err := k8sClient.Create(context.Background(), s); err != nil {
		t.Fatalf("create secret %s: %v", name, err)
	}
}

func getProject(t *testing.T, name string) *tataradevv1alpha1.Project {
	t.Helper()
	p := &tataradevv1alpha1.Project{}
	if err := k8sClient.Get(context.Background(), types.NamespacedName{Namespace: testNS, Name: name}, p); err != nil {
		t.Fatalf("get project %s: %v", name, err)
	}
	return p
}

func waitProjectReady(t *testing.T, name string, want metav1.ConditionStatus) *tataradevv1alpha1.Project {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		p := getProject(t, name)
		c := apierrors.FindStatusCondition(p.Status.Conditions, "Ready")
		if c != nil && c.Status == want {
			return p
		}
		time.Sleep(interval)
	}
	t.Fatalf("project %s Ready never reached %s", name, want)
	return nil
}

func TestProjectReconcile_ValidSecret(t *testing.T) {
	ctx := context.Background()
	mkSecret(t, "valid-scm", map[string][]byte{
		"token":         []byte("ghp_x"),
		"webhookSecret": []byte("hmac"),
	})
	p := &tataradevv1alpha1.Project{}
	p.Name = "proj-valid"
	p.Namespace = testNS
	p.Spec.ScmSecretRef = "valid-scm"
	if err := k8sClient.Create(ctx, p); err != nil {
		t.Fatalf("create project: %v", err)
	}

	if _, err := reconcileProject(t, "proj-valid"); err != nil {
		t.Fatalf("reconcile: %v", err)
	}

	got := waitProjectReady(t, "proj-valid", metav1.ConditionTrue)
	want := "https://tatara.example/operator/webhooks/proj-valid"
	if got.Status.WebhookURL != want {
		t.Errorf("webhookURL = %q, want %q", got.Status.WebhookURL, want)
	}
}

func TestProjectReconcile_MissingSecret(t *testing.T) {
	ctx := context.Background()
	p := &tataradevv1alpha1.Project{}
	p.Name = "proj-nosecret"
	p.Namespace = testNS
	p.Spec.ScmSecretRef = "does-not-exist"
	if err := k8sClient.Create(ctx, p); err != nil {
		t.Fatalf("create project: %v", err)
	}

	if _, err := reconcileProject(t, "proj-nosecret"); err != nil {
		t.Fatalf("reconcile returned error, want nil (status carries failure): %v", err)
	}
	got := waitProjectReady(t, "proj-nosecret", metav1.ConditionFalse)
	c := apierrors.FindStatusCondition(got.Status.Conditions, "Ready")
	if c.Reason != "SecretNotFound" {
		t.Errorf("reason = %q, want SecretNotFound", c.Reason)
	}
}

func TestProjectReconcile_MissingKeys(t *testing.T) {
	ctx := context.Background()
	mkSecret(t, "partial-scm", map[string][]byte{"token": []byte("ghp_x")})
	p := &tataradevv1alpha1.Project{}
	p.Name = "proj-partialkeys"
	p.Namespace = testNS
	p.Spec.ScmSecretRef = "partial-scm"
	if err := k8sClient.Create(ctx, p); err != nil {
		t.Fatalf("create project: %v", err)
	}

	if _, err := reconcileProject(t, "proj-partialkeys"); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	got := waitProjectReady(t, "proj-partialkeys", metav1.ConditionFalse)
	c := apierrors.FindStatusCondition(got.Status.Conditions, "Ready")
	if c.Reason != "SecretMissingKeys" {
		t.Errorf("reason = %q, want SecretMissingKeys", c.Reason)
	}
}

var _ = client.IgnoreNotFound
```

- [ ] Run and expect FAIL:

```
go test ./internal/controller/ -run 'TestProjectReconcile' -v
```

Expected: build failure `undefined: ProjectReconciler` (and its fields). If
envtest binaries are not installed the suite panics with a control-plane
start error; install them first with
`make envtest` or `setup-envtest use 1.31.x --bin-dir bin -p path` and export
`KUBEBUILDER_ASSETS` (M0 Makefile target). The build-failure FAIL is the
expected state for this step once envtest assets are present.

- [ ] Write the minimal implementation (FULL code):

`internal/controller/project_controller.go`
```go
// Package controller holds the tatara-operator reconcilers.
package controller

import (
	"context"
	"fmt"

	tataradevv1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/obs"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

// ProjectReconciler validates a Project's SCM secret and publishes its
// webhook URL.
type ProjectReconciler struct {
	client.Client
	Scheme              *runtime.Scheme
	Metrics             *obs.OperatorMetrics
	ExternalWebhookBase string
}

// +kubebuilder:rbac:groups=tatara.dev,resources=projects,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=tatara.dev,resources=projects/status,verbs=get;update;patch
// +kubebuilder:rbac:groups="",resources=secrets,verbs=get;list;watch

// Reconcile validates spec.scmSecretRef and sets status.webhookURL plus the
// Ready condition. A missing or malformed secret is reported via the Ready
// condition (status False), not returned as an error.
func (r *ProjectReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	l := log.FromContext(ctx)

	var project tataradevv1alpha1.Project
	if err := r.Get(ctx, req.NamespacedName, &project); err != nil {
		if apierrors.IsNotFound(err) {
			return ctrl.Result{}, nil
		}
		r.Metrics.ReconcileResult("Project", "error")
		return ctrl.Result{}, fmt.Errorf("get project: %w", err)
	}

	reason, message, ready := r.validateSecret(ctx, &project)

	project.Status.WebhookURL = fmt.Sprintf("%s/%s", r.ExternalWebhookBase, project.Name)
	status := metav1.ConditionTrue
	if !ready {
		status = metav1.ConditionFalse
	}
	meta.SetStatusCondition(&project.Status.Conditions, metav1.Condition{
		Type:               "Ready",
		Status:             status,
		Reason:             reason,
		Message:            message,
		ObservedGeneration: project.Generation,
	})

	if err := r.Status().Update(ctx, &project); err != nil {
		r.Metrics.ReconcileResult("Project", "error")
		return ctrl.Result{}, fmt.Errorf("update project status: %w", err)
	}

	l.Info("reconciled project",
		"action", "reconcile_project",
		"resource_id", project.Name,
		"ready", ready,
		"reason", reason)
	r.Metrics.ReconcileResult("Project", "success")
	return ctrl.Result{}, nil
}

// validateSecret returns the condition (reason, message, ready) for the
// Project's scmSecretRef. ready is true only when the secret exists and has
// both required keys.
func (r *ProjectReconciler) validateSecret(ctx context.Context, project *tataradevv1alpha1.Project) (reason, message string, ready bool) {
	if project.Spec.ScmSecretRef == "" {
		return "SecretRefEmpty", "spec.scmSecretRef is empty", false
	}
	var secret corev1.Secret
	key := types.NamespacedName{Namespace: project.Namespace, Name: project.Spec.ScmSecretRef}
	if err := r.Get(ctx, key, &secret); err != nil {
		if apierrors.IsNotFound(err) {
			return "SecretNotFound", fmt.Sprintf("secret %q not found", project.Spec.ScmSecretRef), false
		}
		return "SecretError", err.Error(), false
	}
	for _, k := range []string{"token", "webhookSecret"} {
		if len(secret.Data[k]) == 0 {
			return "SecretMissingKeys", fmt.Sprintf("secret %q missing key %q", project.Spec.ScmSecretRef, k), false
		}
	}
	return "Validated", "scm secret present with token and webhookSecret", true
}

// SetupWithManager registers the reconciler with the manager, watching
// Projects.
func (r *ProjectReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&tataradevv1alpha1.Project{}).
		Owns(&corev1.Secret{}).
		Complete(r)
}
```

Note: the test imports `apierrors "k8s.io/apimachinery/pkg/api/meta"` and
calls `apierrors.FindStatusCondition` (that symbol lives in
`k8s.io/apimachinery/pkg/api/meta`). The implementation imports both
`k8s.io/apimachinery/pkg/api/errors` (as `apierrors`, for `IsNotFound`) and
`k8s.io/apimachinery/pkg/api/meta` (as `meta`, for `SetStatusCondition`).
Keep the aliases distinct per file as written.

- [ ] Run and expect PASS:

```
go test ./internal/controller/ -run 'TestProjectReconcile' -v
```

Expected: `ok  github.com/szymonrychu/tatara-operator/internal/controller`
with the three Project tests passing.

- [ ] Commit:

```
git add internal/controller/suite_test.go internal/controller/exit_test.go internal/controller/project_controller.go internal/controller/project_controller_test.go
git commit -m "feat: ProjectReconciler validates scm secret + renders webhookURL"
```

---

## Task 4: RepositoryReconciler trigger + concurrency (envtest)

Implements the re-ingest trigger contract exactly. This task covers: full vs
incremental decision, the `status.jobName` concurrency guard, pre-creating the
result ConfigMap, and launching the Job. Job-success SHA application is the
NEXT task so this one stays bite-sized.

Reuses the suite from Task 3.

Files:
- Create: `internal/controller/repository_controller.go`
- Test: `internal/controller/repository_controller_test.go`

- [ ] Write the failing test (FULL code):

`internal/controller/repository_controller_test.go`
```go
package controller

import (
	"context"
	"testing"
	"time"

	tataradevv1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/ingest"
	"github.com/szymonrychu/tatara-operator/internal/obs"
	"github.com/prometheus/client_golang/prometheus"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
)

func newRepoReconciler() *RepositoryReconciler {
	return &RepositoryReconciler{
		Client:  k8sClient,
		Scheme:  k8sClient.Scheme(),
		Metrics: obs.NewOperatorMetrics(prometheus.NewRegistry()),
		IngestConfig: ingest.Config{
			IngesterImage: "registry.example/ingester:1.2.3",
			MemoryBaseURL: "http://tatara-memory.tatara.svc:8080",
			OIDCIssuer:    "https://kc.example/realms/tatara",
			OIDCClientID:  "tatara-operator",
			OIDCAudience:  "tatara-memory",
			Namespace:     testNS,
		},
	}
}

func reconcileRepo(t *testing.T, name string) (ctrl.Result, error) {
	t.Helper()
	r := newRepoReconciler()
	return r.Reconcile(logf.IntoContext(context.Background(), logf.Log), ctrl.Request{
		NamespacedName: types.NamespacedName{Namespace: testNS, Name: name},
	})
}

func mkProject(t *testing.T, name, secretRef string) {
	t.Helper()
	p := &tataradevv1alpha1.Project{}
	p.Name = name
	p.Namespace = testNS
	p.Spec.ScmSecretRef = secretRef
	if err := k8sClient.Create(context.Background(), p); err != nil {
		t.Fatalf("create project %s: %v", name, err)
	}
}

func mkRepo(t *testing.T, name, projectRef string) *tataradevv1alpha1.Repository {
	t.Helper()
	r := &tataradevv1alpha1.Repository{}
	r.Name = name
	r.Namespace = testNS
	r.Spec.ProjectRef = projectRef
	r.Spec.URL = "https://github.com/acme/" + name + ".git"
	r.Spec.DefaultBranch = "main"
	r.Spec.IngestEnabled = true
	if err := k8sClient.Create(context.Background(), r); err != nil {
		t.Fatalf("create repo %s: %v", name, err)
	}
	return r
}

func getRepo(t *testing.T, name string) *tataradevv1alpha1.Repository {
	t.Helper()
	r := &tataradevv1alpha1.Repository{}
	if err := k8sClient.Get(context.Background(), types.NamespacedName{Namespace: testNS, Name: name}, r); err != nil {
		t.Fatalf("get repo %s: %v", name, err)
	}
	return r
}

func listIngestJobs(t *testing.T, repoName string) []batchv1.Job {
	t.Helper()
	var jl batchv1.JobList
	if err := k8sClient.List(context.Background(), &jl,
		client.InNamespace(testNS),
		client.MatchingLabels{"tatara.dev/repository": repoName}); err != nil {
		t.Fatalf("list jobs: %v", err)
	}
	return jl.Items
}

func waitRepoJob(t *testing.T, repoName string) string {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		r := getRepo(t, repoName)
		if r.Status.JobName != "" {
			return r.Status.JobName
		}
		time.Sleep(interval)
	}
	t.Fatalf("repo %s never set status.jobName", repoName)
	return ""
}

func TestRepoReconcile_FullIngestLaunchesJob(t *testing.T) {
	mkProject(t, "rp-full", "rp-full-scm")
	mkSecret(t, "rp-full-scm", map[string][]byte{"token": []byte("x"), "webhookSecret": []byte("y")})
	mkRepo(t, "full", "rp-full")

	if _, err := reconcileRepo(t, "full"); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	jobName := waitRepoJob(t, "full")

	jobs := listIngestJobs(t, "full")
	if len(jobs) != 1 {
		t.Fatalf("jobs = %d, want 1", len(jobs))
	}
	if jobs[0].Name != jobName {
		t.Errorf("status.jobName = %q, job = %q", jobName, jobs[0].Name)
	}
	// full ingest: no --since in the main container script
	script := jobs[0].Spec.Template.Spec.Containers[0].Args[0]
	if contains(script, "--since") {
		t.Errorf("full ingest job must not pass --since: %q", script)
	}
	// result ConfigMap pre-created
	cm := &corev1.ConfigMap{}
	if err := k8sClient.Get(context.Background(),
		types.NamespacedName{Namespace: testNS, Name: "full-ingest-result"}, cm); err != nil {
		t.Fatalf("result configmap not pre-created: %v", err)
	}
	if getRepo(t, "full").Status.Phase != "Ingesting" {
		t.Errorf("phase = %q, want Ingesting", getRepo(t, "full").Status.Phase)
	}
}

func TestRepoReconcile_ConcurrencyGuard(t *testing.T) {
	mkProject(t, "rp-guard", "rp-guard-scm")
	mkSecret(t, "rp-guard-scm", map[string][]byte{"token": []byte("x"), "webhookSecret": []byte("y")})
	mkRepo(t, "guard", "rp-guard")

	if _, err := reconcileRepo(t, "guard"); err != nil {
		t.Fatalf("first reconcile: %v", err)
	}
	first := waitRepoJob(t, "guard")

	// second reconcile while the Job is still active must not launch another
	if _, err := reconcileRepo(t, "guard"); err != nil {
		t.Fatalf("second reconcile: %v", err)
	}
	jobs := listIngestJobs(t, "guard")
	if len(jobs) != 1 {
		t.Fatalf("jobs after second reconcile = %d, want 1 (guard held)", len(jobs))
	}
	if getRepo(t, "guard").Status.JobName != first {
		t.Errorf("jobName changed under guard: %q -> %q", first, getRepo(t, "guard").Status.JobName)
	}
}

func TestRepoReconcile_IncrementalUsesSince(t *testing.T) {
	mkProject(t, "rp-inc", "rp-inc-scm")
	mkSecret(t, "rp-inc-scm", map[string][]byte{"token": []byte("x"), "webhookSecret": []byte("y")})
	r := mkRepo(t, "inc", "rp-inc")

	// simulate a prior successful ingest
	r = getRepo(t, "inc")
	r.Status.LastIngestedCommit = "oldsha99"
	r.Status.LastIngestTime = metav1.NewTime(time.Now().Add(-1 * time.Hour))
	r.Status.Phase = "Ingested"
	if err := k8sClient.Status().Update(context.Background(), r); err != nil {
		t.Fatalf("seed status: %v", err)
	}
	// request a re-ingest via the annotation, newer than lastIngestTime
	r = getRepo(t, "inc")
	if r.Annotations == nil {
		r.Annotations = map[string]string{}
	}
	r.Annotations["tatara.dev/reingest-requested"] = time.Now().Format(time.RFC3339)
	if err := k8sClient.Update(context.Background(), r); err != nil {
		t.Fatalf("set annotation: %v", err)
	}

	if _, err := reconcileRepo(t, "inc"); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	waitRepoJob(t, "inc")
	jobs := listIngestJobs(t, "inc")
	if len(jobs) != 1 {
		t.Fatalf("jobs = %d, want 1", len(jobs))
	}
	script := jobs[0].Spec.Template.Spec.Containers[0].Args[0]
	if !contains(script, "--since oldsha99") {
		t.Errorf("incremental job must pass --since oldsha99: %q", script)
	}
}

func TestRepoReconcile_NoReingestWhenAnnotationStale(t *testing.T) {
	mkProject(t, "rp-stale", "rp-stale-scm")
	mkSecret(t, "rp-stale-scm", map[string][]byte{"token": []byte("x"), "webhookSecret": []byte("y")})
	r := mkRepo(t, "stale", "rp-stale")

	r = getRepo(t, "stale")
	r.Status.LastIngestedCommit = "shaA"
	r.Status.LastIngestTime = metav1.NewTime(time.Now())
	r.Status.Phase = "Ingested"
	if err := k8sClient.Status().Update(context.Background(), r); err != nil {
		t.Fatalf("seed status: %v", err)
	}
	r = getRepo(t, "stale")
	if r.Annotations == nil {
		r.Annotations = map[string]string{}
	}
	// annotation OLDER than lastIngestTime -> no new ingest
	r.Annotations["tatara.dev/reingest-requested"] = time.Now().Add(-2 * time.Hour).Format(time.RFC3339)
	if err := k8sClient.Update(context.Background(), r); err != nil {
		t.Fatalf("set annotation: %v", err)
	}

	if _, err := reconcileRepo(t, "stale"); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	jobs := listIngestJobs(t, "stale")
	if len(jobs) != 0 {
		t.Fatalf("stale annotation must not launch a job, got %d", len(jobs))
	}
}

func contains(s, sub string) bool {
	return len(sub) == 0 || (len(s) >= len(sub) && indexOf(s, sub) >= 0)
}

func indexOf(s, sub string) int {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return i
		}
	}
	return -1
}
```

Add the missing `client` import used by `listIngestJobs` to this test file
header (it is referenced via `client.InNamespace`): the import block above
includes only what the visible code names; add `"sigs.k8s.io/controller-runtime/pkg/client"`
to the import list of this file when you paste it (kept out of the literal
block to avoid an unused-import error if your editor auto-removes it - it IS
used, keep it).

- [ ] Run and expect FAIL:

```
go test ./internal/controller/ -run 'TestRepoReconcile' -v
```

Expected: build failure `undefined: RepositoryReconciler` (and its
`IngestConfig` field).

- [ ] Write the minimal implementation (FULL code):

`internal/controller/repository_controller.go`
```go
package controller

import (
	"context"
	"fmt"
	"time"

	tataradevv1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/ingest"
	"github.com/szymonrychu/tatara-operator/internal/obs"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

// ReingestAnnotation is the RFC3339 timestamp annotation the M2 webhook sets
// to request an incremental re-ingest.
const ReingestAnnotation = "tatara.dev/reingest-requested"

// RepositoryReconciler drives ingest Jobs for Repositories.
type RepositoryReconciler struct {
	client.Client
	Scheme       *runtime.Scheme
	Metrics      *obs.OperatorMetrics
	IngestConfig ingest.Config
}

// +kubebuilder:rbac:groups=tatara.dev,resources=repositories,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=tatara.dev,resources=repositories/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=tatara.dev,resources=projects,verbs=get;list;watch
// +kubebuilder:rbac:groups=batch,resources=jobs,verbs=get;list;watch;create;delete
// +kubebuilder:rbac:groups="",resources=configmaps,verbs=get;list;watch;create;update;patch

// Reconcile launches and tracks the ingest Job for a Repository per the
// re-ingest trigger contract.
func (r *RepositoryReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	l := log.FromContext(ctx)

	var repo tataradevv1alpha1.Repository
	if err := r.Get(ctx, req.NamespacedName, &repo); err != nil {
		if apierrors.IsNotFound(err) {
			return ctrl.Result{}, nil
		}
		r.Metrics.ReconcileResult("Repository", "error")
		return ctrl.Result{}, fmt.Errorf("get repository: %w", err)
	}

	if !repo.Spec.IngestEnabled {
		return ctrl.Result{}, nil
	}

	// Concurrency guard: a named Job that still exists blocks new launches.
	if repo.Status.JobName != "" {
		var job batchv1.Job
		err := r.Get(ctx, types.NamespacedName{Namespace: repo.Namespace, Name: repo.Status.JobName}, &job)
		switch {
		case err == nil && jobActive(&job):
			l.Info("ingest job still active, requeueing",
				"action", "ingest_guard", "resource_id", repo.Name, "job", repo.Status.JobName)
			return ctrl.Result{RequeueAfter: 15 * time.Second}, nil
		case err == nil:
			// terminal job handled by Task 5 result-apply path
			return r.handleFinishedJob(ctx, &repo, &job)
		case apierrors.IsNotFound(err):
			// Job vanished (TTL/manual delete); clear and re-evaluate.
			repo.Status.JobName = ""
			if err := r.Status().Update(ctx, &repo); err != nil {
				r.Metrics.ReconcileResult("Repository", "error")
				return ctrl.Result{}, fmt.Errorf("clear stale jobName: %w", err)
			}
		default:
			r.Metrics.ReconcileResult("Repository", "error")
			return ctrl.Result{}, fmt.Errorf("get ingest job: %w", err)
		}
	}

	since, want := r.ingestDecision(&repo)
	if !want {
		r.Metrics.ReconcileResult("Repository", "success")
		return ctrl.Result{}, nil
	}

	var project tataradevv1alpha1.Project
	if err := r.Get(ctx, types.NamespacedName{Namespace: repo.Namespace, Name: repo.Spec.ProjectRef}, &project); err != nil {
		r.Metrics.ReconcileResult("Repository", "error")
		return ctrl.Result{}, fmt.Errorf("get owning project %q: %w", repo.Spec.ProjectRef, err)
	}

	if err := r.ensureResultConfigMap(ctx, &repo); err != nil {
		r.Metrics.ReconcileResult("Repository", "error")
		return ctrl.Result{}, fmt.Errorf("ensure result configmap: %w", err)
	}

	job := ingest.BuildJob(&project, &repo, since, r.IngestConfig)
	if err := r.Create(ctx, job); err != nil {
		r.Metrics.ReconcileResult("Repository", "error")
		return ctrl.Result{}, fmt.Errorf("create ingest job: %w", err)
	}

	repo.Status.JobName = job.Name
	repo.Status.Phase = "Ingesting"
	meta.SetStatusCondition(&repo.Status.Conditions, metav1.Condition{
		Type:               "Ingested",
		Status:             metav1.ConditionFalse,
		Reason:             "IngestStarted",
		Message:            "ingest job " + job.Name + " launched",
		ObservedGeneration: repo.Generation,
	})
	if err := r.Status().Update(ctx, &repo); err != nil {
		r.Metrics.ReconcileResult("Repository", "error")
		return ctrl.Result{}, fmt.Errorf("update repository status: %w", err)
	}

	l.Info("launched ingest job",
		"action", "ingest_start", "resource_id", repo.Name, "job", job.Name,
		"incremental", since != "")
	r.Metrics.ReconcileResult("Repository", "success")
	return ctrl.Result{}, nil
}

// ingestDecision returns (sinceSHA, wantIngest). Full ingest (empty since)
// when lastIngestedCommit is empty. Incremental (since=lastIngestedCommit)
// when the reingest-requested annotation is newer than lastIngestTime.
func (r *RepositoryReconciler) ingestDecision(repo *tataradevv1alpha1.Repository) (string, bool) {
	if repo.Status.LastIngestedCommit == "" {
		return "", true
	}
	raw := repo.Annotations[ReingestAnnotation]
	if raw == "" {
		return "", false
	}
	requested, err := time.Parse(time.RFC3339, raw)
	if err != nil {
		return "", false
	}
	if requested.After(repo.Status.LastIngestTime.Time) {
		return repo.Status.LastIngestedCommit, true
	}
	return "", false
}

// ensureResultConfigMap creates the empty <repo>-ingest-result ConfigMap
// (owner-ref Repository) if absent so the Job can patch it and the reconciler
// can read it back.
func (r *RepositoryReconciler) ensureResultConfigMap(ctx context.Context, repo *tataradevv1alpha1.Repository) error {
	cm := &corev1.ConfigMap{}
	cm.Name = ingest.ResultConfigMapName(repo)
	cm.Namespace = repo.Namespace
	if err := r.Get(ctx, types.NamespacedName{Namespace: cm.Namespace, Name: cm.Name}, cm); err == nil {
		return nil
	} else if !apierrors.IsNotFound(err) {
		return fmt.Errorf("get result configmap: %w", err)
	}
	cm = &corev1.ConfigMap{}
	cm.Name = ingest.ResultConfigMapName(repo)
	cm.Namespace = repo.Namespace
	cm.Data = map[string]string{"sha": ""}
	if err := controllerutil.SetControllerReference(repo, cm, r.Scheme); err != nil {
		return fmt.Errorf("set ownerref on result configmap: %w", err)
	}
	if err := r.Create(ctx, cm); err != nil && !apierrors.IsAlreadyExists(err) {
		return fmt.Errorf("create result configmap: %w", err)
	}
	return nil
}

// handleFinishedJob is implemented in Task 5; for Task 4 it requeues so the
// guard does not loop. Replaced wholesale by the Task 5 version.
func (r *RepositoryReconciler) handleFinishedJob(ctx context.Context, repo *tataradevv1alpha1.Repository, job *batchv1.Job) (ctrl.Result, error) {
	return ctrl.Result{RequeueAfter: 5 * time.Second}, nil
}

// jobActive reports whether a Job has neither completed nor failed.
func jobActive(job *batchv1.Job) bool {
	for _, c := range job.Status.Conditions {
		if (c.Type == batchv1.JobComplete || c.Type == batchv1.JobFailed) && c.Status == corev1.ConditionTrue {
			return false
		}
	}
	return true
}

// SetupWithManager registers the reconciler, watching Repositories and the
// Jobs they own.
func (r *RepositoryReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&tataradevv1alpha1.Repository{}).
		Owns(&batchv1.Job{}).
		Owns(&corev1.ConfigMap{}).
		Complete(r)
}
```

- [ ] Run and expect PASS:

```
go test ./internal/controller/ -run 'TestRepoReconcile' -v
```

Expected: `ok` with the four `TestRepoReconcile_*` tests passing (full launch,
concurrency guard, incremental `--since`, stale annotation no-op).

- [ ] Commit:

```
git add internal/controller/repository_controller.go internal/controller/repository_controller_test.go
git commit -m "feat: RepositoryReconciler ingest trigger + concurrency guard"
```

---

## Task 5: RepositoryReconciler applies Job result on success/failure

Replaces the stub `handleFinishedJob` so that on Job completion the reconciler
reads `<repo>-ingest-result.data["sha"]`, sets
`status.lastIngestedCommit`/`lastIngestTime`/`phase=Ingested`, clears
`status.jobName`, sets the `Ingested` condition True, and observes
`operator_ingest_job_duration_seconds`. On Job failure it sets `phase=Failed`,
clears `jobName`, and sets the condition False.

Reuses the suite from Task 3.

Files:
- Modify: `internal/controller/repository_controller.go`
- Test: `internal/controller/repository_result_test.go`

- [ ] Write the failing test (FULL code):

`internal/controller/repository_result_test.go`
```go
package controller

import (
	"context"
	"testing"
	"time"

	tataradevv1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	apimeta "k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

func markJob(t *testing.T, name string, cond batchv1.JobConditionType) {
	t.Helper()
	job := &batchv1.Job{}
	if err := k8sClient.Get(context.Background(),
		types.NamespacedName{Namespace: testNS, Name: name}, job); err != nil {
		t.Fatalf("get job %s: %v", name, err)
	}
	job.Status.Conditions = []batchv1.JobCondition{{
		Type:               cond,
		Status:             corev1.ConditionTrue,
		LastTransitionTime: metav1.Now(),
	}}
	now := metav1.Now()
	job.Status.StartTime = &metav1.Time{Time: now.Add(-30 * time.Second)}
	if cond == batchv1.JobComplete {
		job.Status.CompletionTime = &now
	}
	if err := k8sClient.Status().Update(context.Background(), job); err != nil {
		t.Fatalf("update job status %s: %v", name, err)
	}
}

func setResultSHA(t *testing.T, repoName, sha string) {
	t.Helper()
	cm := &corev1.ConfigMap{}
	if err := k8sClient.Get(context.Background(),
		types.NamespacedName{Namespace: testNS, Name: repoName + "-ingest-result"}, cm); err != nil {
		t.Fatalf("get result cm: %v", err)
	}
	cm.Data = map[string]string{"sha": sha}
	if err := k8sClient.Update(context.Background(), cm); err != nil {
		t.Fatalf("update result cm: %v", err)
	}
}

func TestRepoReconcile_JobSuccessAppliesSHA(t *testing.T) {
	mkProject(t, "rp-ok", "rp-ok-scm")
	mkSecret(t, "rp-ok-scm", map[string][]byte{"token": []byte("x"), "webhookSecret": []byte("y")})
	mkRepo(t, "okrepo", "rp-ok")

	if _, err := reconcileRepo(t, "okrepo"); err != nil {
		t.Fatalf("launch reconcile: %v", err)
	}
	jobName := waitRepoJob(t, "okrepo")

	setResultSHA(t, "okrepo", "deadbeef")
	markJob(t, jobName, batchv1.JobComplete)

	if _, err := reconcileRepo(t, "okrepo"); err != nil {
		t.Fatalf("post-completion reconcile: %v", err)
	}

	got := getRepo(t, "okrepo")
	if got.Status.LastIngestedCommit != "deadbeef" {
		t.Errorf("lastIngestedCommit = %q, want deadbeef", got.Status.LastIngestedCommit)
	}
	if got.Status.Phase != "Ingested" {
		t.Errorf("phase = %q, want Ingested", got.Status.Phase)
	}
	if got.Status.JobName != "" {
		t.Errorf("jobName = %q, want cleared", got.Status.JobName)
	}
	if got.Status.LastIngestTime.IsZero() {
		t.Error("lastIngestTime not set")
	}
	c := apimeta.FindStatusCondition(got.Status.Conditions, "Ingested")
	if c == nil || c.Status != metav1.ConditionTrue {
		t.Errorf("Ingested condition = %+v, want True", c)
	}
}

func TestRepoReconcile_JobFailureSetsFailed(t *testing.T) {
	mkProject(t, "rp-bad", "rp-bad-scm")
	mkSecret(t, "rp-bad-scm", map[string][]byte{"token": []byte("x"), "webhookSecret": []byte("y")})
	mkRepo(t, "badrepo", "rp-bad")

	if _, err := reconcileRepo(t, "badrepo"); err != nil {
		t.Fatalf("launch reconcile: %v", err)
	}
	jobName := waitRepoJob(t, "badrepo")
	markJob(t, jobName, batchv1.JobFailed)

	if _, err := reconcileRepo(t, "badrepo"); err != nil {
		t.Fatalf("post-failure reconcile: %v", err)
	}

	got := getRepo(t, "badrepo")
	if got.Status.Phase != "Failed" {
		t.Errorf("phase = %q, want Failed", got.Status.Phase)
	}
	if got.Status.JobName != "" {
		t.Errorf("jobName = %q, want cleared", got.Status.JobName)
	}
	c := apimeta.FindStatusCondition(got.Status.Conditions, "Ingested")
	if c == nil || c.Status != metav1.ConditionFalse || c.Reason != "IngestFailed" {
		t.Errorf("Ingested condition = %+v, want False/IngestFailed", c)
	}
}
```

- [ ] Run and expect FAIL:

```
go test ./internal/controller/ -run 'TestRepoReconcile_Job(Success|Failure)' -v
```

Expected: FAIL - `lastIngestedCommit = "" want deadbeef` and
`phase = "Ingesting" want Ingested` (the stub `handleFinishedJob` only
requeues; it does not apply the result).

- [ ] Write the minimal implementation (FULL code) - replace the stub
`handleFinishedJob` with this full version:

In `internal/controller/repository_controller.go`, replace the entire stub:

```go
// handleFinishedJob is implemented in Task 5; for Task 4 it requeues so the
// guard does not loop. Replaced wholesale by the Task 5 version.
func (r *RepositoryReconciler) handleFinishedJob(ctx context.Context, repo *tataradevv1alpha1.Repository, job *batchv1.Job) (ctrl.Result, error) {
	return ctrl.Result{RequeueAfter: 5 * time.Second}, nil
}
```

with:

```go
// handleFinishedJob applies a terminal ingest Job's outcome to the Repository
// status: on success it reads the resolved HEAD SHA from the result ConfigMap
// and records lastIngestedCommit/lastIngestTime/phase=Ingested; on failure it
// records phase=Failed. It always clears status.jobName and observes the Job
// duration.
func (r *RepositoryReconciler) handleFinishedJob(ctx context.Context, repo *tataradevv1alpha1.Repository, job *batchv1.Job) (ctrl.Result, error) {
	l := log.FromContext(ctx)

	if job.Status.StartTime != nil && job.Status.CompletionTime != nil {
		r.Metrics.ObserveIngestJobDuration(job.Status.CompletionTime.Sub(job.Status.StartTime.Time).Seconds())
	}

	if jobSucceeded(job) {
		sha, err := r.readResultSHA(ctx, repo)
		if err != nil {
			r.Metrics.ReconcileResult("Repository", "error")
			return ctrl.Result{}, fmt.Errorf("read ingest result sha: %w", err)
		}
		repo.Status.LastIngestedCommit = sha
		repo.Status.LastIngestTime = metav1.Now()
		repo.Status.Phase = "Ingested"
		repo.Status.JobName = ""
		meta.SetStatusCondition(&repo.Status.Conditions, metav1.Condition{
			Type:               "Ingested",
			Status:             metav1.ConditionTrue,
			Reason:             "IngestSucceeded",
			Message:            "ingested at " + sha,
			ObservedGeneration: repo.Generation,
		})
		if err := r.Status().Update(ctx, repo); err != nil {
			r.Metrics.ReconcileResult("Repository", "error")
			return ctrl.Result{}, fmt.Errorf("update repository status: %w", err)
		}
		l.Info("ingest succeeded",
			"action", "ingest_succeeded", "resource_id", repo.Name, "sha", sha, "job", job.Name)
		r.Metrics.ReconcileResult("Repository", "success")
		return ctrl.Result{}, nil
	}

	repo.Status.Phase = "Failed"
	repo.Status.JobName = ""
	meta.SetStatusCondition(&repo.Status.Conditions, metav1.Condition{
		Type:               "Ingested",
		Status:             metav1.ConditionFalse,
		Reason:             "IngestFailed",
		Message:            "ingest job " + job.Name + " failed",
		ObservedGeneration: repo.Generation,
	})
	if err := r.Status().Update(ctx, repo); err != nil {
		r.Metrics.ReconcileResult("Repository", "error")
		return ctrl.Result{}, fmt.Errorf("update repository status: %w", err)
	}
	l.Info("ingest failed",
		"action", "ingest_failed", "resource_id", repo.Name, "job", job.Name)
	r.Metrics.ReconcileResult("Repository", "error")
	return ctrl.Result{}, nil
}

// jobSucceeded reports whether the Job has a Complete=True condition.
func jobSucceeded(job *batchv1.Job) bool {
	for _, c := range job.Status.Conditions {
		if c.Type == batchv1.JobComplete && c.Status == corev1.ConditionTrue {
			return true
		}
	}
	return false
}

// readResultSHA reads data["sha"] from the repo's result ConfigMap.
func (r *RepositoryReconciler) readResultSHA(ctx context.Context, repo *tataradevv1alpha1.Repository) (string, error) {
	var cm corev1.ConfigMap
	key := types.NamespacedName{Namespace: repo.Namespace, Name: ingest.ResultConfigMapName(repo)}
	if err := r.Get(ctx, key, &cm); err != nil {
		return "", fmt.Errorf("get result configmap: %w", err)
	}
	sha := cm.Data["sha"]
	if sha == "" {
		return "", fmt.Errorf("result configmap %s has empty sha", cm.Name)
	}
	return sha, nil
}
```

- [ ] Run and expect PASS:

```
go test ./internal/controller/ -run 'TestRepoReconcile_Job(Success|Failure)' -v
```

Expected: `ok` - both tests pass. Then run the whole controller package to
confirm no regression:

```
go test ./internal/controller/ -v
```

Expected: all Project and Repository tests pass.

- [ ] Commit:

```
git add internal/controller/repository_controller.go internal/controller/repository_result_test.go
git commit -m "feat: apply ingest Job result (sha from result configmap) to Repository status"
```

---

## Task 6: Wire both reconcilers into `cmd/manager/main.go`

Register `ProjectReconciler` and `RepositoryReconciler` with the manager,
constructing them from `internal/config` and `internal/obs`. A small unit test
validates the wiring helper (`addReconcilers`) so we exercise registration
without a full manager run; the manager bootstrap itself stays untested per
the usual controller-runtime convention (it is config glue).

Files:
- Modify: `cmd/manager/main.go`
- Create: `cmd/manager/wire.go`
- Test: `cmd/manager/wire_test.go`

- [ ] Write the failing test (FULL code):

`cmd/manager/wire_test.go`
```go
package main

import (
	"testing"

	"github.com/szymonrychu/tatara-operator/internal/config"
	"github.com/szymonrychu/tatara-operator/internal/ingest"
)

func TestIngestConfigFromConfig(t *testing.T) {
	cfg := config.Config{
		MemoryBaseURL:            "http://mem:8080",
		IngesterImage:            "img:1",
		OIDCIssuer:               "https://kc/realms/t",
		OperatorOIDCClientID:     "tatara-operator",
		OperatorOIDCClientSecret: "secret",
		Namespace:                "tatara",
	}
	got := ingestConfigFromConfig(cfg, "tatara-memory")
	want := ingest.Config{
		IngesterImage:    "img:1",
		MemoryBaseURL:    "http://mem:8080",
		OIDCIssuer:       "https://kc/realms/t",
		OIDCClientID:     "tatara-operator",
		OIDCClientSecret: "secret",
		OIDCAudience:     "tatara-memory",
		Namespace:        "tatara",
	}
	if got != want {
		t.Errorf("ingestConfigFromConfig = %+v, want %+v", got, want)
	}
}
```

This test pins the field mapping from `internal/config.Config` to
`ingest.Config`. It assumes M0's `config.Config` exposes the named fields
(`MemoryBaseURL`, `IngesterImage`, `OIDCIssuer`, `OperatorOIDCClientID`,
`OperatorOIDCClientSecret`, `Namespace`, `ExternalWebhookBase`). If M0 named
them differently, adjust the field references in `wire.go` and this test to
match M0 exactly - do not rename M0's fields.

- [ ] Run and expect FAIL:

```
go test ./cmd/manager/ -run TestIngestConfigFromConfig -v
```

Expected: build failure `undefined: ingestConfigFromConfig`.

- [ ] Write the minimal implementation (FULL code):

`cmd/manager/wire.go`
```go
package main

import (
	"fmt"

	"github.com/szymonrychu/tatara-operator/internal/config"
	"github.com/szymonrychu/tatara-operator/internal/controller"
	"github.com/szymonrychu/tatara-operator/internal/ingest"
	"github.com/szymonrychu/tatara-operator/internal/obs"
	ctrl "sigs.k8s.io/controller-runtime"
)

// ingestConfigFromConfig maps the operator config to the ingest Job builder
// config. memoryAudience is the OIDC audience the ingester presents to
// tatara-memory.
func ingestConfigFromConfig(cfg config.Config, memoryAudience string) ingest.Config {
	return ingest.Config{
		IngesterImage:    cfg.IngesterImage,
		MemoryBaseURL:    cfg.MemoryBaseURL,
		OIDCIssuer:       cfg.OIDCIssuer,
		OIDCClientID:     cfg.OperatorOIDCClientID,
		OIDCClientSecret: cfg.OperatorOIDCClientSecret,
		OIDCAudience:     memoryAudience,
		Namespace:        cfg.Namespace,
	}
}

// addReconcilers constructs and registers the M1 reconcilers with mgr.
func addReconcilers(mgr ctrl.Manager, cfg config.Config, metrics *obs.OperatorMetrics) error {
	if err := (&controller.ProjectReconciler{
		Client:              mgr.GetClient(),
		Scheme:              mgr.GetScheme(),
		Metrics:             metrics,
		ExternalWebhookBase: cfg.ExternalWebhookBase,
	}).SetupWithManager(mgr); err != nil {
		return fmt.Errorf("setup ProjectReconciler: %w", err)
	}
	if err := (&controller.RepositoryReconciler{
		Client:       mgr.GetClient(),
		Scheme:       mgr.GetScheme(),
		Metrics:      metrics,
		IngestConfig: ingestConfigFromConfig(cfg, "tatara-memory"),
	}).SetupWithManager(mgr); err != nil {
		return fmt.Errorf("setup RepositoryReconciler: %w", err)
	}
	return nil
}
```

Then modify `cmd/manager/main.go` (created by M0) to call `addReconcilers`
after the manager is built. Locate the section where M0 constructs the
`ctrl.Manager` (variable `mgr`), the loaded `config.Config` (variable `cfg`),
and the metrics bundle. Immediately after the manager is created and before
`mgr.Start(...)`, insert:

```go
	if err := addReconcilers(mgr, cfg, operatorMetrics); err != nil {
		setupLog.Error(err, "unable to set up reconcilers")
		os.Exit(1)
	}
```

where `operatorMetrics` is an `*obs.OperatorMetrics`. If M0 did not already
construct one, add (near where M0 builds the obs/registry, using the registry
M0 already exposes on the metrics server):

```go
	operatorMetrics := obs.NewOperatorMetrics(metricsRegistry)
```

using M0's registry variable name. State the exact variable names you used in
the commit body. Do not invent a new metrics endpoint; reuse M0's.

- [ ] Run and expect PASS:

```
go test ./cmd/manager/ -run TestIngestConfigFromConfig -v
go build ./...
```

Expected: the test passes and the whole module builds (confirming `main.go`
compiles with the new wiring).

- [ ] Commit:

```
git add cmd/manager/wire.go cmd/manager/wire_test.go cmd/manager/main.go
git commit -m "feat: wire Project + Repository reconcilers into manager"
```

---

## Task 7: Full-package verification + docs

No new behavior. Run the full suite, vet, and lint; update `MEMORY.md` and
`ROADMAP.md`.

Files:
- Modify: `MEMORY.md`
- Modify: `ROADMAP.md`

- [ ] Run the full verification (expect PASS):

```
go build ./...
go vet ./...
go test ./... -count=1
golangci-lint run
```

Expected: build clean, vet clean, all tests pass, lint clean. If
`golangci-lint` flags the `_ = strings.TrimSpace` / `_ = metav1.Now` / `_ =
batchv1.Job{}` / `_ = client.IgnoreNotFound` keep-alive lines added in tests,
remove them and re-add the genuinely-used imports they were guarding, then
re-run. They exist only to prevent unused-import churn while pasting; delete
any that the final code does not need.

- [ ] Update `MEMORY.md` (append, dated 2026-06-06):

```
- 2026-06-06 (M1) Ingest result SHA flows via a per-Repository ConfigMap
  `<repo>-ingest-result` (key `sha`). The ingest Job patches it via the
  in-cluster API after `git rev-parse HEAD`; the reconciler pre-creates it
  (owner-ref Repository) and reads it on Job success. Chosen over pod-log
  parsing (brittle) and Job annotations (Job cannot patch itself cleanly).
  REQUIRES M6 chart to create ServiceAccount `tatara-ingest` + a Role granting
  get/create/update/patch on ConfigMaps in ns `tatara`. Ingest container also
  needs `kubectl` on PATH (the ingester image carries the Go toolchain; verify
  kubectl presence in M6, else switch the patch step to a tiny `curl` against
  the API or a dedicated sidecar).
- 2026-06-06 (M1) Re-ingest trigger: full when status.lastIngestedCommit=="",
  incremental (--since lastIngestedCommit) when annotation
  tatara.dev/reingest-requested (RFC3339) is newer than status.lastIngestTime.
  status.jobName is the single-flight guard. Conditions: Project `Ready`,
  Repository `Ingested`.
- 2026-06-06 (M1) Clone uses x-access-token:${SCM_TOKEN}@<host/path> in the
  init container; works for both GitHub and GitLab HTTPS with the Secret key
  `token`.
```

- [ ] Update `ROADMAP.md`: move the M1 line to done / strike it, leave M2
(webhook server) as the next item. Add a one-line M6 follow-up:
"M6: chart must create `tatara-ingest` ServiceAccount + ConfigMap-patch Role
(see MEMORY.md)."

- [ ] Commit:

```
git add MEMORY.md ROADMAP.md
git commit -m "docs: record M1 ingest-result + trigger decisions"
```

---

## Done criteria for M1

- `go test ./...` green, including envtest reconciler tests and the Job-builder
  unit tests.
- `ProjectReconciler` sets `status.webhookURL` and the `Ready` condition;
  rejects missing secret / missing keys via condition (not error).
- `RepositoryReconciler` launches exactly one ingest Job per Repository,
  guards re-entry via `status.jobName`, passes `--since` only on the
  incremental path, and on Job success records `lastIngestedCommit` from the
  result ConfigMap with `phase=Ingested`; on failure `phase=Failed`.
- Metrics `operator_reconcile_total{kind,result}` and
  `operator_ingest_job_duration_seconds` are incremented/observed.
- Both reconcilers registered in `cmd/manager/main.go`; module builds.
- `MEMORY.md`/`ROADMAP.md` updated; the M6 RBAC follow-up is recorded.

Merge the worktree back to `main` per hard rule 10 (worktree -> source repo
`main` -> cleanup worktree). Do not build/deploy from the worktree.
