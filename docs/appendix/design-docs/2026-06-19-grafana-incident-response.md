# Grafana incident-response Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Optional per-project Grafana incident-response: receive a Grafana alert webhook, provision a read-only grafana-mcp, run an incident agent that investigates live and files an evidence issue the brainstorm/lifecycle loop picks up.

**Architecture:** tatara-operator gains a `GrafanaSpec` on the Project CRD, an operator-provisioned per-project read-only `grafana/mcp-grafana` Deployment+Service (mirrors the memory stack SSA pattern), a `TATARA_GRAFANA_MCP_URL` pod env, a `POST /operator/webhooks/{project}/grafana` receiver that creates a project-scoped `incident` Task, and an `incidentGoalProject` goal. tatara-claude-code-wrapper registers grafana-mcp as an HTTP MCP server from that env var.

**Tech Stack:** Go (operator + wrapper), controller-runtime, chi, envtest, kubebuilder, grafana/mcp-grafana (streamable-http).

## Global Constraints

- Newest stable Go pinned in `go.mod`; build/test via `mise exec -- go ...` or `mise run test`/`mise run lint`. Wrapper repo uses the same mise convention.
- KISS, no tech-debt, boy-scout adjacent fixes (CLAUDE.md hard rules 2-4). JSON slog logging; metrics for things that can fail (rules 11-13). New/changed code only.
- Charts/CRD cluster-agnostic (rule 14): the grafana-mcp image tag is operator-config (helmfile-set), nothing cluster-specific baked.
- grafana-mcp runs READ-ONLY: args include `--disable-write`. No write/remediation MCP, ever (out of scope).
- Webhook auth = constant-time compare of `Authorization: Bearer <token>` against the `webhookSecret` key in `GrafanaSpec.SecretRef` (version-independent). HMAC is out of scope.
- Trigger: `status=="firing"` only; resolved/other -> 202 ignored. Dedup per alert group (`groupKey` hash) with `CooldownSeconds` (default 3600).
- Incident Task is project-scoped (empty `RepositoryRef`, Kind `incident`), created by the webhook path (which never consults `MaxOpenTasks`, so it is naturally exempt).
- Numeric `runAsUser` on the grafana-mcp container (runAsNonRoot needs a numeric uid; heed the runAsNonRoot-needs-numeric-uid incident).
- Operator CRD upgrades are skipped by Helm: the deploy runbook must `kubectl apply` the regenerated CRD (operator-crd-gap memory).
- Feature is fully inert unless a Project sets `Grafana.Enabled` (no field -> no provisioning, no env, no route effect, no wrapper overlay).

---

### Task 1: `GrafanaSpec` + `GrafanaStatus` on the Project CRD

**Files:**
- Modify: `api/v1alpha1/project_types.go` (add structs + `ProjectSpec.Grafana`, `ProjectStatus.Grafana`)
- Test: `api/v1alpha1/grafana_types_test.go` (create)
- Regenerate: CRD manifest via `make manifests`

**Interfaces:**
- Produces: `GrafanaSpec{Enabled bool; URL string; SecretRef string; CooldownSeconds int}`, `GrafanaStatus{Phase string; Endpoint string}`, `ProjectSpec.Grafana *GrafanaSpec`, `ProjectStatus.Grafana *GrafanaStatus`.

- [ ] **Step 1: Write the failing test**

Create `api/v1alpha1/grafana_types_test.go`:

```go
package v1alpha1

import "testing"

func TestGrafanaSpec_Fields(t *testing.T) {
	g := GrafanaSpec{Enabled: true, URL: "http://grafana:3000", SecretRef: "proj-grafana", CooldownSeconds: 1800}
	if !g.Enabled || g.URL == "" || g.SecretRef == "" || g.CooldownSeconds != 1800 {
		t.Fatalf("GrafanaSpec fields not wired: %+v", g)
	}
	p := ProjectSpec{Grafana: &g}
	if p.Grafana == nil || !p.Grafana.Enabled {
		t.Fatalf("ProjectSpec.Grafana not wired")
	}
	st := ProjectStatus{Grafana: &GrafanaStatus{Phase: "Ready", Endpoint: "http://grafana-mcp-x.ns.svc:8000"}}
	if st.Grafana == nil || st.Grafana.Phase != "Ready" {
		t.Fatalf("ProjectStatus.Grafana not wired")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `mise exec -- go test ./api/v1alpha1/ -run TestGrafanaSpec_Fields -v`
Expected: FAIL (compile error: `GrafanaSpec`/`GrafanaStatus` undefined).

- [ ] **Step 3: Add the structs + Project fields**

In `api/v1alpha1/project_types.go`, add after the `MemoryStatus` struct:

```go
// GrafanaSpec configures the optional per-project Grafana incident-response
// feature: an operator-provisioned read-only grafana-mcp and an alert-webhook
// receiver. The feature is inert unless Enabled.
type GrafanaSpec struct {
	Enabled bool `json:"enabled"`
	// URL is the Grafana base URL grafana-mcp queries (non-secret).
	URL string `json:"url"`
	// SecretRef names a Secret holding the Grafana credentials. Keys:
	//   serviceAccountToken - Grafana Viewer SA token (mounted into grafana-mcp)
	//   webhookSecret       - static bearer the alert webhook must present
	SecretRef string `json:"secretRef"`
	// CooldownSeconds is the per-alert-group refire window (default 3600).
	// +kubebuilder:default=3600
	// +optional
	CooldownSeconds int `json:"cooldownSeconds,omitempty"`
}

// GrafanaStatus reports the observed state of the per-Project grafana-mcp.
type GrafanaStatus struct {
	// +optional
	Phase string `json:"phase,omitempty"`
	// +optional
	Endpoint string `json:"endpoint,omitempty"`
}
```

In `ProjectSpec` (after the `Scm *ScmSpec` field):

```go
	// +optional
	Grafana *GrafanaSpec `json:"grafana,omitempty"`
```

In `ProjectStatus` (after `Memory *MemoryStatus`):

```go
	// +optional
	Grafana *GrafanaStatus `json:"grafana,omitempty"`
```

- [ ] **Step 4: Run test + regenerate manifests**

Run: `mise exec -- go test ./api/v1alpha1/ -run TestGrafanaSpec_Fields -v`
Expected: PASS.
Run: `mise exec -- make manifests && mise exec -- make generate`
Expected: `config/crd/bases/*project*.yaml` now has `grafana` under spec + status; `zz_generated.deepcopy.go` updated (DeepCopy for the new pointers).

- [ ] **Step 5: Commit**

```bash
git add api/v1alpha1/project_types.go api/v1alpha1/grafana_types_test.go api/v1alpha1/zz_generated.deepcopy.go config/crd
git commit -m "feat: GrafanaSpec/GrafanaStatus on Project CRD"
```

---

### Task 2: `incident` Task kind

**Files:**
- Modify: `api/v1alpha1/task_types.go` (`projectScopedKinds`, the `Kind` enum marker)
- Test: `api/v1alpha1/task_types_test.go` (add; or the existing validate test file)
- Regenerate: CRD manifest

**Interfaces:**
- Produces: Task `Kind == "incident"` accepted as project-scoped (empty `RepositoryRef`).

- [ ] **Step 1: Write the failing test**

Add to `api/v1alpha1/task_types_test.go` (create if absent, `package v1alpha1`):

```go
func TestValidateTaskSpec_Incident(t *testing.T) {
	if err := ValidateTaskSpec(TaskSpec{Kind: "incident"}); err != nil {
		t.Fatalf("incident with empty repositoryRef must be valid: %v", err)
	}
	if err := ValidateTaskSpec(TaskSpec{Kind: "incident", RepositoryRef: "r"}); err == nil {
		t.Fatalf("incident with a repositoryRef must be rejected (project-scoped)")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `mise exec -- go test ./api/v1alpha1/ -run TestValidateTaskSpec_Incident -v`
Expected: FAIL (incident not in `projectScopedKinds`, so the second assertion - no error - fails).

- [ ] **Step 3: Add `incident` to the kind sets + enum**

In `api/v1alpha1/task_types.go`, add to `projectScopedKinds`:

```go
var projectScopedKinds = map[string]bool{
	"brainstorm":  true,
	"healthCheck": true,
	"incident":    true,
}
```

And extend the `Kind` kubebuilder enum marker on `TaskSpec.Kind`:

```go
	// +kubebuilder:validation:Enum=implement;review;selfImprove;triageIssue;brainstorm;issueLifecycle;incident
```

- [ ] **Step 4: Run test + regenerate**

Run: `mise exec -- go test ./api/v1alpha1/ -run TestValidateTaskSpec_Incident -v`
Expected: PASS.
Run: `mise exec -- make manifests`
Expected: Task CRD enum now lists `incident`.

- [ ] **Step 5: Commit**

```bash
git add api/v1alpha1/task_types.go api/v1alpha1/task_types_test.go config/crd
git commit -m "feat: add incident Task kind (project-scoped)"
```

---

### Task 3: grafana-mcp builders (`internal/grafanamcp`)

**Files:**
- Create: `internal/grafanamcp/grafanamcp.go` (Config, Names, Endpoint, labels/ownerRef/objectMeta helpers)
- Create: `internal/grafanamcp/builders.go` (`Deployment`, `Service`)
- Test: `internal/grafanamcp/builders_test.go`

**Interfaces:**
- Produces: `grafanamcp.Config{Namespace, Image, ImagePullSecret string}`; `grafanamcp.Name(project) string` (`"grafana-mcp-"+project`); `grafanamcp.Endpoint(project, ns) string` (`http://grafana-mcp-<project>.<ns>.svc:8000`); `grafanamcp.MCPURL(project, ns) string` (`<Endpoint>/mcp`); `grafanamcp.Deployment(p *v1alpha1.Project, cfg Config) *appsv1.Deployment`; `grafanamcp.Service(p, cfg) *corev1.Service`.
- Consumes: `api/v1alpha1` `Project` (reads `Spec.Grafana.URL`, `Spec.Grafana.SecretRef`).

- [ ] **Step 1: Write the failing test**

Create `internal/grafanamcp/builders_test.go`:

```go
package grafanamcp

import (
	"strings"
	"testing"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func proj() *tatarav1alpha1.Project {
	p := &tatarav1alpha1.Project{ObjectMeta: metav1.ObjectMeta{Name: "acme", Namespace: "tatara"}}
	p.Spec.Grafana = &tatarav1alpha1.GrafanaSpec{Enabled: true, URL: "http://grafana:3000", SecretRef: "acme-grafana"}
	return p
}

func TestDeployment_ReadOnlyStreamableHTTP(t *testing.T) {
	d := Deployment(proj(), Config{Namespace: "tatara", Image: "grafana/mcp-grafana:v0.1.0"})
	if d.Name != "grafana-mcp-acme" {
		t.Fatalf("name: %s", d.Name)
	}
	c := d.Spec.Template.Spec.Containers[0]
	args := strings.Join(c.Args, " ")
	if !strings.Contains(args, "streamable-http") || !strings.Contains(args, "--disable-write") {
		t.Fatalf("args must be read-only streamable-http: %v", c.Args)
	}
	if c.Ports[0].ContainerPort != 8000 {
		t.Fatalf("port: %d", c.Ports[0].ContainerPort)
	}
	var url, tokenFile string
	for _, e := range c.Env {
		if e.Name == "GRAFANA_URL" {
			url = e.Value
		}
		if e.Name == "GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE" {
			tokenFile = e.Value
		}
	}
	if url != "http://grafana:3000" {
		t.Fatalf("GRAFANA_URL: %q", url)
	}
	if tokenFile != "/etc/grafana/token" {
		t.Fatalf("token file env: %q", tokenFile)
	}
	if c.SecurityContext == nil || c.SecurityContext.RunAsUser == nil {
		t.Fatalf("container needs a numeric runAsUser (runAsNonRoot incident)")
	}
	// token mounted from the project's grafana secret, key serviceAccountToken.
	vol := d.Spec.Template.Spec.Volumes[0]
	if vol.Secret == nil || vol.Secret.SecretName != "acme-grafana" {
		t.Fatalf("token volume must project secret acme-grafana: %+v", vol)
	}
	if vol.Secret.Items[0].Key != "serviceAccountToken" || vol.Secret.Items[0].Path != "token" {
		t.Fatalf("token volume item must be serviceAccountToken->token: %+v", vol.Secret.Items)
	}
}

func TestService_ClusterIP8000(t *testing.T) {
	s := Service(proj(), Config{Namespace: "tatara"})
	if s.Name != "grafana-mcp-acme" || s.Spec.Ports[0].Port != 8000 {
		t.Fatalf("service: %s :%d", s.Name, s.Spec.Ports[0].Port)
	}
}

func TestEndpointAndMCPURL(t *testing.T) {
	if Endpoint("acme", "tatara") != "http://grafana-mcp-acme.tatara.svc:8000" {
		t.Fatalf("endpoint: %s", Endpoint("acme", "tatara"))
	}
	if MCPURL("acme", "tatara") != "http://grafana-mcp-acme.tatara.svc:8000/mcp" {
		t.Fatalf("mcp url: %s", MCPURL("acme", "tatara"))
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `mise exec -- go test ./internal/grafanamcp/ -v`
Expected: FAIL (package/functions undefined).

- [ ] **Step 3: Implement the package**

Create `internal/grafanamcp/grafanamcp.go`:

```go
// Package grafanamcp holds pure builder functions producing the per-Project
// read-only grafana-mcp workload (grafana/mcp-grafana, streamable-http,
// --disable-write). No function performs a client call; the ProjectReconciler
// server-side-applies the returned objects (mirrors internal/memory).
package grafanamcp

import (
	"fmt"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// Config is the operator-level (non-per-Project) input the builders need.
type Config struct {
	Namespace       string
	Image           string
	ImagePullSecret string
}

// Name returns the Deployment/Service name for a project.
func Name(project string) string { return "grafana-mcp-" + project }

// Endpoint is the in-cluster base URL of a project's grafana-mcp.
func Endpoint(project, namespace string) string {
	return fmt.Sprintf("http://grafana-mcp-%s.%s.svc:8000", project, namespace)
}

// MCPURL is the streamable-http MCP endpoint the agent connects to.
func MCPURL(project, namespace string) string { return Endpoint(project, namespace) + "/mcp" }

func labels(project string) map[string]string {
	return map[string]string{
		"app.kubernetes.io/name":     "grafana-mcp",
		"app.kubernetes.io/instance": Name(project),
		"tatara.dev/project":         project,
	}
}

func ownerRef(p *tatarav1alpha1.Project) metav1.OwnerReference {
	return *metav1.NewControllerRef(p, tatarav1alpha1.GroupVersion.WithKind("Project"))
}

func objectMeta(p *tatarav1alpha1.Project, cfg Config, name string) metav1.ObjectMeta {
	return metav1.ObjectMeta{
		Name:            name,
		Namespace:       cfg.Namespace,
		Labels:          labels(p.Name),
		OwnerReferences: []metav1.OwnerReference{ownerRef(p)},
	}
}
```

Create `internal/grafanamcp/builders.go`:

```go
package grafanamcp

import (
	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/intstr"
)

const grafanaRunAsUser int64 = 65532 // distroless nonroot; image runs as nonroot

func imagePullSecrets(cfg Config) []corev1.LocalObjectReference {
	if cfg.ImagePullSecret == "" {
		return nil
	}
	return []corev1.LocalObjectReference{{Name: cfg.ImagePullSecret}}
}

// Deployment builds the per-Project read-only grafana-mcp Deployment.
// streamable-http on :8000, --disable-write, Grafana Viewer token mounted from
// the project's grafana secret (key serviceAccountToken) and read via
// GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE (re-read per request).
func Deployment(p *tatarav1alpha1.Project, cfg Config) *appsv1.Deployment {
	name := Name(p.Name)
	replicas := int32(1)
	runAsNonRoot := true
	noPrivEsc := false
	runAsUser := grafanaRunAsUser
	sel := map[string]string{"app.kubernetes.io/instance": name}
	podLabels := labels(p.Name)

	return &appsv1.Deployment{
		TypeMeta:   metav1.TypeMeta{APIVersion: "apps/v1", Kind: "Deployment"},
		ObjectMeta: objectMeta(p, cfg, name),
		Spec: appsv1.DeploymentSpec{
			Replicas: &replicas,
			Selector: &metav1.LabelSelector{MatchLabels: sel},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{Labels: podLabels},
				Spec: corev1.PodSpec{
					ImagePullSecrets: imagePullSecrets(cfg),
					Volumes: []corev1.Volume{{
						Name: "grafana-token",
						VolumeSource: corev1.VolumeSource{Secret: &corev1.SecretVolumeSource{
							SecretName: p.Spec.Grafana.SecretRef,
							Items:      []corev1.KeyToPath{{Key: "serviceAccountToken", Path: "token"}},
						}},
					}},
					Containers: []corev1.Container{{
						Name:  "grafana-mcp",
						Image: cfg.Image,
						Args:  []string{"-t", "streamable-http", "--disable-write"},
						Ports: []corev1.ContainerPort{{Name: "http", ContainerPort: 8000, Protocol: corev1.ProtocolTCP}},
						Env: []corev1.EnvVar{
							{Name: "GRAFANA_URL", Value: p.Spec.Grafana.URL},
							{Name: "GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE", Value: "/etc/grafana/token"},
						},
						VolumeMounts: []corev1.VolumeMount{{
							Name: "grafana-token", MountPath: "/etc/grafana", ReadOnly: true,
						}},
						SecurityContext: &corev1.SecurityContext{
							RunAsNonRoot:             &runAsNonRoot,
							RunAsUser:                &runAsUser,
							AllowPrivilegeEscalation: &noPrivEsc,
						},
					}},
				},
			},
		},
	}
}

// Service exposes grafana-mcp on 8000 (ClusterIP).
func Service(p *tatarav1alpha1.Project, cfg Config) *corev1.Service {
	name := Name(p.Name)
	return &corev1.Service{
		TypeMeta:   metav1.TypeMeta{APIVersion: "v1", Kind: "Service"},
		ObjectMeta: objectMeta(p, cfg, name),
		Spec: corev1.ServiceSpec{
			Type:     corev1.ServiceTypeClusterIP,
			Selector: map[string]string{"app.kubernetes.io/instance": name},
			Ports:    []corev1.ServicePort{{Name: "http", Port: 8000, TargetPort: intstr.FromString("http"), Protocol: corev1.ProtocolTCP}},
		},
	}
}
```

NOTE: confirm the grafana-mcp streamable-http endpoint path (`/mcp`) and the image's nonroot uid against the pinned image during Task 9 deploy prep; adjust `MCPURL`/`grafanaRunAsUser` if the pinned image differs.

- [ ] **Step 4: Run tests to verify they pass**

Run: `mise exec -- go test ./internal/grafanamcp/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/grafanamcp
git commit -m "feat: grafana-mcp builders (read-only streamable-http Deployment+Service)"
```

---

### Task 4: `reconcileGrafanaMCP` + reconcile wiring + config

**Files:**
- Create: `internal/controller/project_grafana.go`
- Modify: `internal/controller/project_controller.go` (call after `reconcileMemory` ~line 94; add `GrafanaConfig grafanamcp.Config` field ~line 43)
- Modify: `internal/config/*` (add `GrafanaMCPImage` from env `GRAFANA_MCP_IMAGE`) and `cmd/manager/wire.go` (build `grafanamcp.Config`, set on reconciler)
- Test: `internal/controller/project_grafana_test.go`

**Interfaces:**
- Consumes: `grafanamcp.Deployment/Service/Name/Endpoint/Config` (Task 3); `GrafanaSpec`/`GrafanaStatus` (Task 1).
- Produces: `(*ProjectReconciler).reconcileGrafanaMCP(ctx, *Project) (time.Duration, error)`; reconciler field `GrafanaConfig grafanamcp.Config`.

- [ ] **Step 1: Write the failing test**

Create `internal/controller/project_grafana_test.go` (envtest, mirrors existing controller tests; use the package's `k8sClient`/`testNS`):

```go
package controller

import (
	"context"
	"testing"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

func TestReconcileGrafanaMCP_AppliesWhenEnabled(t *testing.T) {
	ctx := context.Background()
	p := &tatarav1alpha1.Project{}
	p.Name = "gmcp-on"
	p.Namespace = testNS
	p.Spec.ScmSecretRef = "gmcp-on-scm"
	p.Spec.Grafana = &tatarav1alpha1.GrafanaSpec{Enabled: true, URL: "http://grafana:3000", SecretRef: "gmcp-on-grafana"}
	if err := k8sClient.Create(ctx, p); err != nil {
		t.Fatalf("create project: %v", err)
	}

	r := &ProjectReconciler{Client: k8sClient, Scheme: k8sClient.Scheme()}
	r.GrafanaConfig.Namespace = testNS
	r.GrafanaConfig.Image = "grafana/mcp-grafana:test"

	if _, err := r.reconcileGrafanaMCP(ctx, p); err != nil {
		t.Fatalf("reconcileGrafanaMCP: %v", err)
	}

	var d appsv1.Deployment
	if err := k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: "grafana-mcp-gmcp-on"}, &d); err != nil {
		t.Fatalf("expected grafana-mcp deployment: %v", err)
	}
	var svc corev1.Service
	if err := k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: "grafana-mcp-gmcp-on"}, &svc); err != nil {
		t.Fatalf("expected grafana-mcp service: %v", err)
	}
	if p.Status.Grafana == nil || p.Status.Grafana.Endpoint == "" {
		t.Fatalf("status.grafana not set: %+v", p.Status.Grafana)
	}
}

func TestReconcileGrafanaMCP_TeardownWhenDisabled(t *testing.T) {
	ctx := context.Background()
	p := &tatarav1alpha1.Project{}
	p.Name = "gmcp-off"
	p.Namespace = testNS
	p.Spec.ScmSecretRef = "gmcp-off-scm"
	// No Grafana spec -> feature off.
	if err := k8sClient.Create(ctx, p); err != nil {
		t.Fatalf("create project: %v", err)
	}
	r := &ProjectReconciler{Client: k8sClient, Scheme: k8sClient.Scheme()}
	r.GrafanaConfig.Namespace = testNS
	r.GrafanaConfig.Image = "grafana/mcp-grafana:test"

	if _, err := r.reconcileGrafanaMCP(ctx, p); err != nil {
		t.Fatalf("reconcile (disabled): %v", err)
	}
	var d appsv1.Deployment
	err := k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: "grafana-mcp-gmcp-off"}, &d)
	if err == nil || !apierrors.IsNotFound(err) {
		t.Fatalf("disabled project must have NO grafana-mcp deployment; got err=%v", err)
	}
	_ = metav1.Now()
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `mise run test -- ./internal/controller/ -run TestReconcileGrafanaMCP` (or `KUBEBUILDER_ASSETS=... mise exec -- go test ./internal/controller/ -run TestReconcileGrafanaMCP -v`)
Expected: FAIL (`reconcileGrafanaMCP`/`GrafanaConfig` undefined).

- [ ] **Step 3: Implement `reconcileGrafanaMCP`**

Create `internal/controller/project_grafana.go`:

```go
package controller

import (
	"context"
	"fmt"
	"time"

	tataradevv1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/grafanamcp"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

const grafanaFieldOwner = "tatara-operator"
const grafanaRequeue = 10 * time.Second

// reconcileGrafanaMCP provisions (or tears down) the per-Project read-only
// grafana-mcp workload, gated on Spec.Grafana.Enabled. It mirrors
// reconcileMemory: SSA-apply when enabled, status roll-up, teardown when off.
// Failure is isolated and does not block other reconciles.
func (r *ProjectReconciler) reconcileGrafanaMCP(ctx context.Context, p *tataradevv1alpha1.Project) (time.Duration, error) {
	l := log.FromContext(ctx)
	ns := r.GrafanaConfig.Namespace
	name := grafanamcp.Name(p.Name)

	enabled := p.Spec.Grafana != nil && p.Spec.Grafana.Enabled
	if !enabled {
		// Teardown: best-effort delete of a previously-applied workload.
		_ = r.Delete(ctx, &appsv1.Deployment{ObjectMeta: objMeta(ns, name)})
		_ = r.Delete(ctx, &corev1.Service{ObjectMeta: objMeta(ns, name)})
		p.Status.Grafana = nil
		return 0, nil
	}

	objs := []client.Object{
		grafanamcp.Deployment(p, r.GrafanaConfig),
		grafanamcp.Service(p, r.GrafanaConfig),
	}
	for _, obj := range objs {
		if err := r.Patch(ctx, obj, client.Apply, //nolint:staticcheck
			client.FieldOwner(grafanaFieldOwner), client.ForceOwnership); err != nil {
			p.Status.Grafana = &tataradevv1alpha1.GrafanaStatus{Phase: "Failed", Endpoint: grafanamcp.Endpoint(p.Name, ns)}
			l.Error(err, "grafana-mcp apply failed", "action", "grafana_apply", "resource_id", p.Name)
			return 0, fmt.Errorf("apply %T %s: %w", obj, obj.GetName(), err)
		}
	}

	phase := "Provisioning"
	var d appsv1.Deployment
	if err := r.Get(ctx, types.NamespacedName{Namespace: ns, Name: name}, &d); err == nil {
		if d.Status.AvailableReplicas >= 1 {
			phase = "Ready"
		}
	} else if !apierrors.IsNotFound(err) {
		return grafanaRequeue, nil // transient cache blip
	}
	p.Status.Grafana = &tataradevv1alpha1.GrafanaStatus{Phase: phase, Endpoint: grafanamcp.Endpoint(p.Name, ns)}
	if phase != "Ready" {
		return grafanaRequeue, nil
	}
	return 0, nil
}

func objMeta(ns, name string) metav1ObjectMeta { return metav1ObjectMeta{Name: name, Namespace: ns} }
```

Replace the `objMeta` helper + `metav1ObjectMeta` alias with the real import: add `metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"` to the import block and define:

```go
func objMeta(ns, name string) metav1.ObjectMeta { return metav1.ObjectMeta{Name: name, Namespace: ns} }
```

(remove the placeholder `metav1ObjectMeta` line). 

In `internal/controller/project_controller.go`: add the reconciler field next to `MemoryConfig`:

```go
	GrafanaConfig grafanamcp.Config
```

(import `"github.com/szymonrychu/tatara-operator/internal/grafanamcp"`), and call it in `Reconcile` right after the `reconcileMemory` block (~line 94). Roll its requeue into the existing requeue selection - take the smaller non-zero of the two:

```go
	grafanaRequeueAfter, grafErr := r.reconcileGrafanaMCP(ctx, &project)
	if grafErr != nil {
		l.Error(grafErr, "grafana-mcp reconcile failed (non-blocking)", "resource_id", project.Name)
	}
	if grafanaRequeueAfter > 0 && (requeueAfter == 0 || grafanaRequeueAfter < requeueAfter) {
		requeueAfter = grafanaRequeueAfter
	}
```

(adapt variable names to the actual `Reconcile` body around line 94; the memory call uses `requeueAfter, memErr`.)

- [ ] **Step 4: Wire config**

In `internal/config` (the Config struct + Load, mirror `MemoryImage`): add `GrafanaMCPImage string` read from env `GRAFANA_MCP_IMAGE`. In `cmd/manager/wire.go`, set the reconciler field where `MemoryConfig: memoryConfigFromConfig(cfg)` is set (~line 156):

```go
		GrafanaConfig: grafanamcp.Config{
			Namespace:       cfg.Namespace,
			Image:           cfg.GrafanaMCPImage,
			ImagePullSecret: cfg.ImagePullSecret, // reuse the same field memoryConfigFromConfig uses
		},
```

(import `grafanamcp`; use the exact ImagePullSecret source `memoryConfigFromConfig` uses - grep it.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `mise run test -- ./internal/controller/ -run TestReconcileGrafanaMCP` then `mise exec -- go build ./...`
Expected: PASS + build clean.

- [ ] **Step 6: Commit**

```bash
git add internal/controller/project_grafana.go internal/controller/project_controller.go internal/controller/project_grafana_test.go internal/config cmd/manager/wire.go
git commit -m "feat: reconcileGrafanaMCP provisioning + config wiring"
```

---

### Task 5: Inject `TATARA_GRAFANA_MCP_URL` into agent pods

**Files:**
- Modify: `internal/agent/pod.go` (`BuildPod` env block, after `TATARA_CHAT_URL`)
- Test: `internal/agent/pod_grafana_test.go`

**Interfaces:**
- Consumes: `grafanamcp.MCPURL(project, ns)` (Task 3); `project.Spec.Grafana` (Task 1).
- Produces: env `TATARA_GRAFANA_MCP_URL` present on agent pods iff `project.Spec.Grafana != nil && Enabled`.

- [ ] **Step 1: Write the failing test**

Create `internal/agent/pod_grafana_test.go`:

```go
package agent

import (
	"testing"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func grafanaEnv(pod interface{ getEnv() map[string]string }) {}

func podEnvMap(t *testing.T, project *tatarav1alpha1.Project) map[string]string {
	t.Helper()
	task := &tatarav1alpha1.Task{ObjectMeta: metav1.ObjectMeta{Name: "t1"}, Spec: tatarav1alpha1.TaskSpec{ProjectRef: project.Name, Kind: "incident"}}
	pod := BuildPod(project, nil, task, nil, "http://mem", PodConfig{Namespace: "tatara"})
	m := map[string]string{}
	for _, e := range pod.Spec.Containers[0].Env {
		m[e.Name] = e.Value
	}
	return m
}

func TestBuildPod_GrafanaMCPURL_WhenEnabled(t *testing.T) {
	p := &tatarav1alpha1.Project{ObjectMeta: metav1.ObjectMeta{Name: "acme"}}
	p.Spec.Grafana = &tatarav1alpha1.GrafanaSpec{Enabled: true, URL: "http://g", SecretRef: "s"}
	m := podEnvMap(t, p)
	if m["TATARA_GRAFANA_MCP_URL"] != "http://grafana-mcp-acme.tatara.svc:8000/mcp" {
		t.Fatalf("grafana mcp url env wrong/missing: %q", m["TATARA_GRAFANA_MCP_URL"])
	}
}

func TestBuildPod_NoGrafanaMCPURL_WhenDisabled(t *testing.T) {
	p := &tatarav1alpha1.Project{ObjectMeta: metav1.ObjectMeta{Name: "acme"}}
	m := podEnvMap(t, p)
	if _, ok := m["TATARA_GRAFANA_MCP_URL"]; ok {
		t.Fatalf("grafana mcp url env must be absent when feature off")
	}
}
```

(Remove the unused `grafanaEnv` stub if the compiler flags it; it is a leftover - delete that line.)

- [ ] **Step 2: Run test to verify it fails**

Run: `mise exec -- go test ./internal/agent/ -run TestBuildPod_.*Grafana -v`
Expected: FAIL (env absent).

- [ ] **Step 3: Inject the env**

In `internal/agent/pod.go` `BuildPod`, after the env append block (after the `secretEnv(...)` lines, before the HMAC/labels section), add:

```go
	if project.Spec.Grafana != nil && project.Spec.Grafana.Enabled {
		// Per-project read-only grafana-mcp endpoint. The wrapper registers this
		// as an HTTP MCP server so the agent can query Grafana for live debugging.
		env = append(env, corev1.EnvVar{
			Name:  "TATARA_GRAFANA_MCP_URL",
			Value: grafanamcp.MCPURL(project.Name, cfg.Namespace),
		})
	}
```

Add the import `"github.com/szymonrychu/tatara-operator/internal/grafanamcp"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `mise exec -- go test ./internal/agent/ -run TestBuildPod_.*Grafana -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/agent/pod.go internal/agent/pod_grafana_test.go
git commit -m "feat: inject TATARA_GRAFANA_MCP_URL into agent pods when grafana enabled"
```

---

### Task 6: `incident.GoalProject` goal builder (shared package)

**Files:**
- Create: `internal/incident/goal.go` (new shared package, imported by both the webhook handler in Task 8 and any controller reference - one definition, no import cycle)
- Test: `internal/incident/goal_test.go`

**Interfaces:**
- Produces: `incident.GoalProject(alertCtx string, slugs []string) string`.

- [ ] **Step 1: Write the failing test**

Create `internal/incident/goal_test.go`:

```go
package incident

import (
	"strings"
	"testing"
)

func TestGoalProject(t *testing.T) {
	g := GoalProject("groupKey=abc status=firing commonLabels={alertname=HighCPU}", []string{"o/api", "o/web"})
	for _, kw := range []string{"o/api", "o/web", "groupKey=abc", "grafana", "propose_issue", "read-only"} {
		if !strings.Contains(g, kw) {
			t.Fatalf("incident goal missing %q:\n%s", kw, g)
		}
	}
	// Must forbid remediation/write actions.
	if !strings.Contains(g, "Do NOT") || !strings.Contains(strings.ToLower(g), "remediat") {
		t.Fatalf("incident goal must forbid remediation:\n%s", g)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `mise exec -- go test ./internal/incident/ -run TestGoalProject -v`
Expected: FAIL (package/function undefined).

- [ ] **Step 3: Implement the goal builder**

Create `internal/incident/goal.go`:

```go
// Package incident builds the turn-0 goal for a Grafana-fired incident Task.
// It lives in its own package so both the webhook receiver and the controller
// can import it without a cycle.
package incident

import "strings"

// GoalProject returns the turn-0 goal for a project-scoped incident Task fired
// by a Grafana alert. The agent investigates live (read-only) via the Grafana
// MCP server, then files exactly one evidence issue via propose_issue, choosing
// the repo the evidence implicates. alertCtx is a pre-rendered compact block of
// the alert (group key, status, labels, annotations, generator/external URLs).
func GoalProject(alertCtx string, slugs []string) string {
	repoList := strings.Join(slugs, ", ")
	return "A Grafana alert is FIRING for this project. Investigate it and hand a well-evidenced issue " +
		"to the team. Repositories in this project: " + repoList + ".\n\n" +
		"ALERT:\n" + alertCtx + "\n\n" +
		"Investigate LIVE using the `grafana` MCP server (read-only): query the relevant Prometheus/Loki " +
		"datasources, read the firing alert rule (follow its generatorURL), and inspect related dashboards. " +
		"Form a diagnosis backed by the queries you ran and their results.\n\n" +
		"Then call propose_issue(repo, body) EXACTLY ONCE. Choose the `repo` (from the list above) that the " +
		"evidence implicates. The body MUST contain: the alert summary, the queries/tools you ran and their " +
		"results, your diagnosis, and the Grafana links (generatorURL/externalURL). The issue lands with the " +
		"brainstorming label and the normal triage/brainstorm flow takes over.\n\n" +
		"If the grafana MCP server is unreachable, still file the issue with the raw alert and note the MCP was " +
		"unavailable. If after investigation this is a confirmed false positive, finish with a one-line note and " +
		"do NOT open an issue.\n\n" +
		"This is a READ-ONLY investigation. Do NOT take any remediation, write, or corrective action on any " +
		"system. Your only output is the issue (or the false-positive note)."
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `mise exec -- go test ./internal/incident/ -run TestGoalProject -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/incident
git commit -m "feat: incident.GoalProject goal builder (read-only grafana investigation)"
```

---

### Task 7: Grafana alert parser + rendered context

**Files:**
- Create: `internal/webhook/grafana.go` (`GrafanaAlert` struct, `parseGrafanaAlert`, `renderAlertContext`, `alertGroupHash`)
- Test: `internal/webhook/grafana_test.go`

**Interfaces:**
- Produces: `type GrafanaAlert struct{...}`; `parseGrafanaAlert([]byte) (GrafanaAlert, error)`; `renderAlertContext(GrafanaAlert) string`; `alertGroupHash(GrafanaAlert) string` (16-hex of `groupKey`).

- [ ] **Step 1: Write the failing test**

Create `internal/webhook/grafana_test.go`:

```go
package webhook

import (
	"strings"
	"testing"
)

const grafanaFiring = `{"status":"firing","groupKey":"{}/{alertname=\"HighCPU\"}","commonLabels":{"alertname":"HighCPU","severity":"critical"},"commonAnnotations":{"summary":"CPU high"},"externalURL":"http://grafana:3000","alerts":[{"status":"firing","labels":{"alertname":"HighCPU","instance":"node1"},"annotations":{"summary":"CPU high on node1"},"startsAt":"2026-06-19T00:00:00Z","generatorURL":"http://grafana:3000/alerting/rule","fingerprint":"abc123"}]}`

const grafanaResolved = `{"status":"resolved","groupKey":"g","alerts":[]}`

func TestParseGrafanaAlert(t *testing.T) {
	a, err := parseGrafanaAlert([]byte(grafanaFiring))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if a.Status != "firing" || a.GroupKey == "" || len(a.Alerts) != 1 {
		t.Fatalf("parsed wrong: %+v", a)
	}
	if a.Alerts[0].GeneratorURL == "" || a.CommonLabels["severity"] != "critical" {
		t.Fatalf("fields missing: %+v", a)
	}
}

func TestRenderAlertContext(t *testing.T) {
	a, _ := parseGrafanaAlert([]byte(grafanaFiring))
	ctx := renderAlertContext(a)
	for _, kw := range []string{"firing", "HighCPU", "generatorURL", "http://grafana:3000"} {
		if !strings.Contains(ctx, kw) {
			t.Fatalf("rendered ctx missing %q:\n%s", kw, ctx)
		}
	}
}

func TestAlertGroupHash_StableAndDistinct(t *testing.T) {
	a, _ := parseGrafanaAlert([]byte(grafanaFiring))
	b, _ := parseGrafanaAlert([]byte(grafanaResolved))
	if alertGroupHash(a) == "" || len(alertGroupHash(a)) != 16 {
		t.Fatalf("hash must be 16 hex: %q", alertGroupHash(a))
	}
	if alertGroupHash(a) == alertGroupHash(b) {
		t.Fatalf("distinct groupKeys must hash differently")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `mise exec -- go test ./internal/webhook/ -run 'TestParseGrafanaAlert|TestRenderAlertContext|TestAlertGroupHash' -v`
Expected: FAIL (undefined).

- [ ] **Step 3: Implement the parser**

Create `internal/webhook/grafana.go`:

```go
package webhook

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

// GrafanaAlert is the subset of the Grafana unified-alerting webhook payload
// (Alertmanager-compatible) the receiver needs.
type GrafanaAlert struct {
	Status            string            `json:"status"`
	GroupKey          string            `json:"groupKey"`
	CommonLabels      map[string]string `json:"commonLabels"`
	CommonAnnotations map[string]string `json:"commonAnnotations"`
	ExternalURL       string            `json:"externalURL"`
	Alerts            []GrafanaAlertItem `json:"alerts"`
}

type GrafanaAlertItem struct {
	Status       string            `json:"status"`
	Labels       map[string]string `json:"labels"`
	Annotations  map[string]string `json:"annotations"`
	StartsAt     string            `json:"startsAt"`
	GeneratorURL string            `json:"generatorURL"`
	Fingerprint  string            `json:"fingerprint"`
}

func parseGrafanaAlert(body []byte) (GrafanaAlert, error) {
	var a GrafanaAlert
	if err := json.Unmarshal(body, &a); err != nil {
		return GrafanaAlert{}, fmt.Errorf("parse grafana alert: %w", err)
	}
	if a.Status == "" {
		return GrafanaAlert{}, fmt.Errorf("grafana alert missing status")
	}
	return a, nil
}

func sortedKV(m map[string]string) string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, k+"="+m[k])
	}
	return strings.Join(parts, ", ")
}

// renderAlertContext produces the compact alert block embedded in the incident
// goal (one line per fact; per-alert generatorURL/labels included).
func renderAlertContext(a GrafanaAlert) string {
	var b strings.Builder
	fmt.Fprintf(&b, "status=%s groupKey=%s\n", a.Status, a.GroupKey)
	fmt.Fprintf(&b, "commonLabels: {%s}\n", sortedKV(a.CommonLabels))
	fmt.Fprintf(&b, "commonAnnotations: {%s}\n", sortedKV(a.CommonAnnotations))
	fmt.Fprintf(&b, "externalURL: %s\n", a.ExternalURL)
	for i, it := range a.Alerts {
		fmt.Fprintf(&b, "alert[%d]: status=%s labels={%s} annotations={%s} startsAt=%s generatorURL=%s\n",
			i, it.Status, sortedKV(it.Labels), sortedKV(it.Annotations), it.StartsAt, it.GeneratorURL)
	}
	return strings.TrimRight(b.String(), "\n")
}

// alertGroupHash is the dedup key for an alert group (16 hex of groupKey).
func alertGroupHash(a GrafanaAlert) string {
	h := sha256.Sum256([]byte(a.GroupKey))
	return hex.EncodeToString(h[:])[:16]
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `mise exec -- go test ./internal/webhook/ -run 'TestParseGrafanaAlert|TestRenderAlertContext|TestAlertGroupHash' -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/webhook/grafana.go internal/webhook/grafana_test.go
git commit -m "feat: grafana alert parser + rendered context + group hash"
```

---

### Task 8: Webhook receiver `handleGrafanaAlert` + incident Task create + dedup

**Files:**
- Modify: `internal/webhook/server.go` (`Mount` add route; new `handleGrafanaAlert`, `createIncidentTask`, dedup helper)
- Modify: `api/v1alpha1/<labels/annotations file>` (add `AnnGrafanaAlert`, `LabelAlertGroup`; grep for `AnnBrainstormSources`/`LabelActivity` to find the file)
- Test: `internal/webhook/grafana_handler_test.go`

**Interfaces:**
- Consumes: `parseGrafanaAlert`, `renderAlertContext`, `alertGroupHash` (Task 7); `incident.GoalProject(alertCtx, slugs)` (Task 6, in the shared `internal/incident` package - imported here, no cycle).
- Produces: route `POST /operator/webhooks/{project}/grafana`; `LabelAlertGroup`, `AnnGrafanaAlert` constants; `createIncidentTask`, `incidentDedup`, `projectRepoSlugs` helpers.

- [ ] **Step 1: Write the failing test**

Create `internal/webhook/grafana_handler_test.go` (mirror the existing webhook handler tests' harness: a fake client with a Project + Secret, `NewServer`, `Mount`, `httptest`):

```go
package webhook

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/go-chi/chi/v5"
	tatarav1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/obs"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"github.com/prometheus/client_golang/prometheus"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func grafanaRouter(t *testing.T, objs ...client.Object) (*chi.Mux, client.Client) {
	t.Helper()
	sch := runtime.NewScheme()
	_ = tatarav1.AddToScheme(sch)
	_ = corev1.AddToScheme(sch)
	fc := fake.NewClientBuilder().WithScheme(sch).WithObjects(objs...).
		WithStatusSubresource(&tatarav1.Project{}, &tatarav1.Task{}).Build()
	s := NewServer(Config{Client: fc, Namespace: "tatara", Metrics: obs.NewOperatorMetrics(prometheus.NewRegistry())})
	r := chi.NewRouter()
	s.Mount(r)
	return r, fc
}

func grafanaProject(name string) *tatarav1.Project {
	p := &tatarav1.Project{ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "tatara"}}
	p.Spec.Grafana = &tatarav1.GrafanaSpec{Enabled: true, URL: "http://g", SecretRef: name + "-grafana", CooldownSeconds: 3600}
	return p
}

func grafanaSecret(name string) *corev1.Secret {
	return &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: name + "-grafana", Namespace: "tatara"},
		Data: map[string][]byte{"webhookSecret": []byte("tok"), "serviceAccountToken": []byte("sa")}}
}

func postGrafana(r *chi.Mux, project, bearer, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/operator/webhooks/"+project+"/grafana", strings.NewReader(body))
	if bearer != "" {
		req.Header.Set("Authorization", "Bearer "+bearer)
	}
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	return w
}

func listIncidentTasks(t *testing.T, fc client.Client) []tatarav1.Task {
	t.Helper()
	var tl tatarav1.TaskList
	_ = fc.List(t.Context(), &tl)
	var out []tatarav1.Task
	for _, x := range tl.Items {
		if x.Spec.Kind == "incident" {
			out = append(out, x)
		}
	}
	return out
}

func TestGrafana_FiringCreatesIncident(t *testing.T) {
	r, fc := grafanaRouter(t, grafanaProject("p1"), grafanaSecret("p1"))
	w := postGrafana(r, "p1", "tok", grafanaFiring)
	if w.Code != http.StatusAccepted {
		t.Fatalf("want 202, got %d: %s", w.Code, w.Body.String())
	}
	if n := len(listIncidentTasks(t, fc)); n != 1 {
		t.Fatalf("want 1 incident task, got %d", n)
	}
}

func TestGrafana_BadBearer401(t *testing.T) {
	r, _ := grafanaRouter(t, grafanaProject("p2"), grafanaSecret("p2"))
	if w := postGrafana(r, "p2", "wrong", grafanaFiring); w.Code != http.StatusUnauthorized {
		t.Fatalf("want 401, got %d", w.Code)
	}
}

func TestGrafana_ResolvedIgnored(t *testing.T) {
	r, fc := grafanaRouter(t, grafanaProject("p3"), grafanaSecret("p3"))
	w := postGrafana(r, "p3", "tok", grafanaResolved)
	if w.Code != http.StatusAccepted {
		t.Fatalf("want 202, got %d", w.Code)
	}
	if n := len(listIncidentTasks(t, fc)); n != 0 {
		t.Fatalf("resolved must create no task, got %d", n)
	}
}

func TestGrafana_DedupInFlight(t *testing.T) {
	r, fc := grafanaRouter(t, grafanaProject("p4"), grafanaSecret("p4"))
	_ = postGrafana(r, "p4", "tok", grafanaFiring)
	_ = postGrafana(r, "p4", "tok", grafanaFiring) // same groupKey, in-flight
	if n := len(listIncidentTasks(t, fc)); n != 1 {
		t.Fatalf("dedup failed: want 1 incident task, got %d", n)
	}
}

func TestGrafana_DisabledProject(t *testing.T) {
	p := &tatarav1.Project{ObjectMeta: metav1.ObjectMeta{Name: "p5", Namespace: "tatara"}} // no Grafana
	r, _ := grafanaRouter(t, p)
	if w := postGrafana(r, "p5", "tok", grafanaFiring); w.Code != http.StatusNotFound {
		t.Fatalf("disabled project must 404, got %d", w.Code)
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `mise exec -- go test ./internal/webhook/ -run TestGrafana_ -v`
Expected: FAIL (route 404 / handler undefined).

- [ ] **Step 3: Add constants**

Grep `grep -rn "AnnBrainstormSources\|LabelActivity =" api/v1alpha1/` to find the constants file; add there:

```go
	// AnnGrafanaAlert carries the rendered Grafana alert context on an incident Task.
	AnnGrafanaAlert = "tatara.dev/grafana-alert"
	// LabelAlertGroup is the per-alert-group dedup key on an incident Task.
	LabelAlertGroup = "tatara.dev/alert-group"
```

- [ ] **Step 4: Add the route + handler**

In `internal/webhook/server.go` `Mount`:

```go
func (s *Server) Mount(r chi.Router) {
	r.Post("/operator/webhooks/{project}", s.handle)
	r.Post("/operator/webhooks/{project}/grafana", s.handleGrafanaAlert)
}
```

Add the handler (constant-time bearer compare via `crypto/subtle`, firing-only, dedup, create):

```go
func (s *Server) handleGrafanaAlert(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	projectName := chi.URLParam(r, "project")
	body, err := readBody(r)
	if err != nil {
		http.Error(w, "read body", http.StatusBadRequest)
		return
	}
	var proj tatarav1.Project
	if err := s.cfg.Client.Get(ctx, objKey(s.cfg.Namespace, projectName), &proj); err != nil {
		http.Error(w, "unknown project", http.StatusNotFound)
		return
	}
	if proj.Spec.Grafana == nil || !proj.Spec.Grafana.Enabled {
		http.Error(w, "grafana not enabled", http.StatusNotFound)
		return
	}
	secret, err := s.webhookSecret(ctx, proj.Spec.Grafana.SecretRef)
	if err != nil {
		http.Error(w, "secret", http.StatusInternalServerError)
		return
	}
	bearer := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	if subtle.ConstantTimeCompare([]byte(bearer), []byte(secret)) != 1 {
		s.count("grafana", "alert", "other", "bad_signature")
		http.Error(w, "verification failed", http.StatusUnauthorized)
		return
	}
	alert, err := parseGrafanaAlert(body)
	if err != nil {
		http.Error(w, "parse alert", http.StatusBadRequest)
		return
	}
	if alert.Status != "firing" {
		s.count("grafana", "alert", alert.Status, "ignored")
		w.WriteHeader(http.StatusAccepted)
		return
	}
	groupHash := alertGroupHash(alert)
	skip, reason, err := s.incidentDedup(ctx, proj.Name, groupHash, proj.Spec.Grafana.CooldownSeconds)
	if err != nil {
		http.Error(w, "dedup", http.StatusInternalServerError)
		return
	}
	if skip {
		s.count("grafana", "alert", "firing", reason)
		w.WriteHeader(http.StatusAccepted)
		return
	}
	if err := s.createIncidentTask(ctx, &proj, alert, groupHash); err != nil {
		s.count("grafana", "alert", "firing", "error")
		http.Error(w, "create task", http.StatusInternalServerError)
		return
	}
	s.count("grafana", "alert", "firing", "created")
	w.WriteHeader(http.StatusAccepted)
}

// incidentDedup reports whether to skip creating an incident for this alert
// group: skip if a non-terminal incident Task exists (in-flight), or the newest
// incident Task for the group was created within cooldownSeconds.
func (s *Server) incidentDedup(ctx context.Context, project, groupHash string, cooldownSeconds int) (bool, string, error) {
	var tl tatarav1.TaskList
	if err := s.cfg.Client.List(ctx, &tl,
		client.InNamespace(s.cfg.Namespace),
		client.MatchingLabels{tatarav1.LabelActivity: "incident", tatarav1.LabelAlertGroup: groupHash}); err != nil {
		return false, "", err
	}
	if cooldownSeconds <= 0 {
		cooldownSeconds = 3600
	}
	var newest *tatarav1.Task
	for i := range tl.Items {
		tk := &tl.Items[i]
		if tk.Spec.ProjectRef != project {
			continue
		}
		if !tatarav1.TaskTerminal(tk) {
			return true, "duplicate", nil // in-flight
		}
		if newest == nil || tk.CreationTimestamp.After(newest.CreationTimestamp.Time) {
			newest = tk
		}
	}
	if newest != nil && time.Since(newest.CreationTimestamp.Time) < time.Duration(cooldownSeconds)*time.Second {
		return true, "cooldown", nil
	}
	return false, "", nil
}

func (s *Server) createIncidentTask(ctx context.Context, proj *tatarav1.Project, alert GrafanaAlert, groupHash string) error {
	slugs := projectRepoSlugs(ctx, s.cfg.Client, s.cfg.Namespace, proj.Name)
	goal := incident.GoalProject(renderAlertContext(alert), slugs)
	task := &tatarav1.Task{
		ObjectMeta: metav1.ObjectMeta{
			GenerateName:    "incident-",
			Namespace:       s.cfg.Namespace,
			Labels:          map[string]string{tatarav1.LabelActivity: "incident", tatarav1.LabelAlertGroup: groupHash},
			Annotations:     map[string]string{tatarav1.AnnGrafanaAlert: renderAlertContext(alert)},
			OwnerReferences: []metav1.OwnerReference{*metav1.NewControllerRef(proj, tatarav1.GroupVersion.WithKind("Project"))},
		},
		Spec: tatarav1.TaskSpec{
			ProjectRef: proj.Name,
			// RepositoryRef intentionally empty: incident is project-scoped.
			Goal: goal,
			Kind: "incident",
		},
	}
	agent.StampPodName(task, proj.Name, "", "")
	return s.cfg.Client.Create(ctx, task)
}

// projectRepoSlugs returns the owner/repo slugs of a project's Repositories,
// name-sorted, for the incident goal's repo list.
func projectRepoSlugs(ctx context.Context, c client.Client, ns, project string) []string {
	var rl tatarav1.RepositoryList
	if err := c.List(ctx, &rl, client.InNamespace(ns)); err != nil {
		return nil
	}
	var slugs []string
	for i := range rl.Items {
		if rl.Items[i].Spec.ProjectRef != project {
			continue
		}
		if o, n, err := scm.OwnerRepo(rl.Items[i].Spec.URL); err == nil {
			slugs = append(slugs, o+"/"+n)
		}
	}
	sort.Strings(slugs)
	return slugs
}
```

Add imports to `server.go`: `"crypto/subtle"`, `"sort"`, `"github.com/szymonrychu/tatara-operator/internal/incident"` (Task 6's new package), and confirm `time`, `agent`, `scm`, `client`, `metav1` are already imported (they are, used elsewhere in the file).

- [ ] **Step 5: Run tests to verify they pass**

Run: `mise exec -- go test ./internal/webhook/ -run TestGrafana_ -v`
Expected: PASS.

- [ ] **Step 6: Run the full webhook suite (no regression)**

Run: `mise exec -- go test ./internal/webhook/ -count=1`
Expected: PASS (existing SCM webhook tests unaffected; the new route is additive).

- [ ] **Step 7: Commit**

```bash
git add internal/webhook/server.go internal/webhook/grafana_handler_test.go api/v1alpha1 internal/incident
git commit -m "feat: grafana alert webhook receiver -> project-scoped incident Task (firing-only, dedup+cooldown)"
```

---

### Task 9: Wrapper - register grafana-mcp as an HTTP MCP server

**Files:** (repo: tatara-claude-code-wrapper)
- Modify: `internal/bootstrap/bootstrap.go` (add `GrafanaMCPURL string` to `Params`)
- Modify: `internal/bootstrap/mcp.go` (`mergeMCP` adds the grafana entry when set)
- Modify: `cmd/wrapper/app.go` (~line 197-201, set `GrafanaMCPURL` from env) + `cmd/wrapper/config.go` (read env `TATARA_GRAFANA_MCP_URL`)
- Test: `internal/bootstrap/mcp_grafana_test.go`

**Interfaces:**
- Consumes: env `TATARA_GRAFANA_MCP_URL` (set by operator Task 5).
- Produces: `/workspace/.mcp.json` contains `mcpServers.grafana = {"type":"http","url":"<url>"}` iff the env/Param is set; absent otherwise; the tatara entry is preserved.

- [ ] **Step 1: Write the failing test**

Create `internal/bootstrap/mcp_grafana_test.go`:

```go
package bootstrap

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func readMCP(t *testing.T, ws string) mcpDoc {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(ws, ".mcp.json"))
	if err != nil {
		t.Fatalf("read .mcp.json: %v", err)
	}
	var d mcpDoc
	if err := json.Unmarshal(b, &d); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	return d
}

func TestMergeMCP_AddsGrafanaWhenSet(t *testing.T) {
	ws := t.TempDir()
	err := mergeMCP(Params{
		Workspace:     ws,
		BaseMCP:       []byte(`{"mcpServers":{"tatara":{"command":"tatara","args":["mcp"]}}}`),
		GrafanaMCPURL: "http://grafana-mcp-acme.tatara.svc:8000/mcp",
	})
	if err != nil {
		t.Fatalf("mergeMCP: %v", err)
	}
	d := readMCP(t, ws)
	if _, ok := d.MCPServers["tatara"]; !ok {
		t.Fatalf("tatara entry must be preserved")
	}
	g, ok := d.MCPServers["grafana"]
	if !ok {
		t.Fatalf("grafana entry missing")
	}
	var entry map[string]string
	_ = json.Unmarshal(g, &entry)
	if entry["type"] != "http" || entry["url"] != "http://grafana-mcp-acme.tatara.svc:8000/mcp" {
		t.Fatalf("grafana entry wrong: %s", string(g))
	}
}

func TestMergeMCP_NoGrafanaWhenUnset(t *testing.T) {
	ws := t.TempDir()
	if err := mergeMCP(Params{Workspace: ws, BaseMCP: []byte(`{"mcpServers":{}}`)}); err != nil {
		t.Fatalf("mergeMCP: %v", err)
	}
	if _, ok := readMCP(t, ws).MCPServers["grafana"]; ok {
		t.Fatalf("grafana entry must be absent when URL unset")
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Documents/tatara/tatara-claude-code-wrapper && mise exec -- go test ./internal/bootstrap/ -run TestMergeMCP_.*Grafana -v`
Expected: FAIL (`Params.GrafanaMCPURL` undefined).

- [ ] **Step 3: Add the Param + merge entry**

In `internal/bootstrap/bootstrap.go`, add to `Params` (near `BaseMCP`/`MCPOverlayDir`):

```go
	GrafanaMCPURL string
```

In `internal/bootstrap/mcp.go` `mergeMCP`, after the overlay loop and before marshalling, add:

```go
	if p.GrafanaMCPURL != "" {
		entry, _ := json.Marshal(map[string]string{"type": "http", "url": p.GrafanaMCPURL})
		merged.MCPServers["grafana"] = entry
	}
```

- [ ] **Step 4: Wire the env**

In `cmd/wrapper/config.go`, add a field + env read (mirror `MCPOverlayDir`):

```go
	GrafanaMCPURL string
```
```go
		GrafanaMCPURL: os.Getenv("TATARA_GRAFANA_MCP_URL"),
```

In `cmd/wrapper/app.go`, set it on the bootstrap `Params` (~line 197-201, alongside `BaseMCP`/`MCPOverlayDir`):

```go
		GrafanaMCPURL: cfg.GrafanaMCPURL,
```

- [ ] **Step 5: Run tests + build**

Run: `mise exec -- go test ./internal/bootstrap/ -run TestMergeMCP -v && mise exec -- go build ./...`
Expected: PASS + build clean.

- [ ] **Step 6: Commit**

```bash
git add internal/bootstrap/bootstrap.go internal/bootstrap/mcp.go cmd/wrapper/config.go cmd/wrapper/app.go internal/bootstrap/mcp_grafana_test.go
git commit -m "feat: register grafana-mcp as an http MCP server from TATARA_GRAFANA_MCP_URL"
```

---

### Task 10: Full verification (both repos)

- [ ] **Step 1: Operator lint + full suite + manifests clean**

Run (in tatara-operator):
`mise run lint && mise run test && mise exec -- make manifests && git diff --exit-status config/crd`
Expected: lint clean; all envtest suites green; no uncommitted manifest drift (if drift, commit the regenerated CRD).

- [ ] **Step 2: Wrapper lint + suite + MCP build-guard**

Run (in tatara-claude-code-wrapper):
`mise run lint && mise run test`
Expected: green, including the existing `TestTataraMCP_AdvertisesScmProjectTools` build-guard (the grafana entry is additive and must not break the tatara entry).

- [ ] **Step 3: Cross-cutting contract checks**

- `grep -rn "incident" api/v1alpha1/task_types.go` shows it in both the enum and `projectScopedKinds`.
- `grep -rn "TATARA_GRAFANA_MCP_URL" internal/agent/pod.go` (operator sets) and `cmd/wrapper/config.go` (wrapper reads) - the env name matches exactly on both sides.
- `internal/grafanamcp.MCPURL` path (`/mcp`) matches the URL the wrapper writes - both derive from the same env, so they align by construction.

- [ ] **Step 4: requesting-code-review, pre-commit, deploy**

Per CLAUDE.md: run `superpowers:requesting-code-review` on each repo's branch, fix critical/high, `pre-commit run --all-files`, then deploy in dependency order:
1. tatara-claude-code-wrapper main -> wrapper image.
2. tatara-operator main -> operator image; `kubectl apply` the regenerated CRD (Helm skips CRD upgrades - operator-crd-gap memory).
3. tatara-helmfile: bump operator chart version + `image.tag`; bump Project `agent.image` to the new wrapper; add `GRAFANA_MCP_IMAGE` to the operator config/values; per-project `GrafanaSpec` + the new Grafana Secret (sops keys `serviceAccountToken`, `webhookSecret`).
4. Grafana: Viewer service account + token; a `webhook` contact point -> `/operator/webhooks/{project}/grafana` with `Authorization: Bearer <webhookSecret>`.

---

## Self-Review

**Spec coverage:**
- Unit A (GrafanaSpec/Status, validation, manifests) -> Task 1.
- `incident` Task kind -> Task 2.
- Unit B (grafana-mcp builders + reconcile + config) -> Tasks 3, 4.
- Unit C (pod env, gated) -> Task 5.
- Unit D (wrapper MCP registration) -> Task 9.
- Unit E (webhook receiver: route, bearer, firing-only, dedup/cooldown, exempt from MaxOpenTasks) -> Tasks 7, 8.
- Unit F (incident goal; project-scoped Task; agent picks repo via propose_issue; read-only/no-remediation) -> Tasks 6, 8.
- Data flow + error handling -> exercised across Tasks 4/5/8 tests (disabled->404, resolved->202, bad bearer->401, dedup).
- Deploy runbook (CRD apply, cross-repo order, Grafana SA + contact point) -> Task 10 Step 4.

**Resolved design detail (location of the goal builder):** the goal builder is `incident.GoalProject` in the shared package `internal/incident` (Task 6 creates it there; Task 8 imports it). One definition, no import cycle, imported by both the webhook handler and any controller reference.

**Placeholder scan:** none. Every step carries real code/commands. The two flagged verify-at-deploy items (grafana-mcp `/mcp` endpoint path; image nonroot uid) are concrete defaults with an explicit confirmation step, not placeholders.

**Type consistency:** `grafanamcp.Config{Namespace,Image,ImagePullSecret}`, `grafanamcp.Name`/`Endpoint`/`MCPURL`, `GrafanaSpec{Enabled,URL,SecretRef,CooldownSeconds}`, `GrafanaStatus{Phase,Endpoint}`, env name `TATARA_GRAFANA_MCP_URL`, labels `LabelActivity="incident"` + `LabelAlertGroup`, annotation `AnnGrafanaAlert`, `incident.GoalProject(alertCtx, slugs)`, parser `parseGrafanaAlert`/`renderAlertContext`/`alertGroupHash` - all names match across tasks. The grafana-mcp port (8000) and read-only args (`streamable-http`,`--disable-write`) are consistent in builder + endpoint + URL.
