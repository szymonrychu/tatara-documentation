# Per-Project External Exposure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Sonnet implementers, opus merge (CLAUDE.md rule 7). Start each repo from a fresh `main` + pull (bots push).

**Goal:** The tatara-operator provisions and exposes per-project memory (phase 1) and chat (phase 2) via a per-project Ingress on the shared host, so the tatara MCP tools reach the project's code graph externally with app-level OIDC auth.

**Architecture:** Phase 1 adds operator `Config` fields (ingress host/class/path prefixes), a pure `memory.Ingress(p, cfg)` builder appended to the existing `applyMemoryStack` (owner-ref'd, server-side-applied), `status.memory.externalEndpoint`, RBAC + `Owns(Ingress)`, chart values, infra values, and a tatara-cli `--project`/`-p` flag composing the per-project memory URL. Phase 2 adds an `internal/chat` provisioner (Deployment+Service+ConfigMap, like memory but stateless), the chat path on the Ingress, `status.chat`, and retires the standalone `tatara-chat` release.

**Tech Stack:** Go (controller-runtime, k8s networking/v1), Helm, cobra (cli), helmfile. Spec: `docs/superpowers/specs/2026-06-12-per-project-ingress-design.md`.

**Images ship via CI, not local buildx** (see MEMORY `tatara-images-via-ci-not-local-buildx`): merge operator/cli changes to main -> their pipelines push to harbor -> bump infra image pins -> deploy.

---

## File structure

**tatara-operator** (`~/Documents/tatara/tatara-operator`):
- Create: `internal/memory/ingress.go` - `Ingress(p, cfg)` builder + `ExternalMemoryURL()`.
- Create: `internal/memory/ingress_test.go` - builder unit tests.
- Modify: `internal/memory/memory.go` - add ingress fields to `memory.Config`.
- Modify: `internal/config/config.go` - add `IngressHost/IngressClassName/MemoryPathPrefix/ChatPathPrefix/ChatImage` + Load().
- Modify: `internal/controller/project_memory.go` - append the ingress in `applyMemoryStack`; set `status.memory.externalEndpoint`.
- Modify: the manager wiring that builds `memory.Config` from `config.Config` (find via grep `MemoryConfig`/`memory.Config{`) - map the new fields.
- Modify: `internal/controller/project_controller.go` - `Owns(&networkingv1.Ingress{})` in `SetupWithManager`.
- Modify: `api/v1alpha1/project_types.go` - `MemoryStatus.ExternalEndpoint` (phase 1); `ChatStatus` + `ProjectStatus.Chat` (phase 2). Then `make generate manifests`.
- Modify: `charts/tatara-operator/templates/configmap.yaml` - new env keys; `templates/rbac.yaml` - ingress rule; `values.yaml` - new values.
- Create (phase 2): `internal/chat/chat_builders.go` + `chat.go` + tests; wire into reconcile.

**tatara-cli** (`~/Documents/tatara/tatara-cli`):
- Modify: the root cobra command (grep `cobra.Command` in `cmd/tatara`) - persistent `--project`/`-p` flag + `TATARA_PROJECT` env.
- Modify: the memory client base-URL resolution (grep `MEMORY_URL`/`base-url`/`BaseURL`) - append `/<project>` when set.
- Create: a unit test for the URL composition.

**infra** (`~/Documents/infra/helmfile`):
- Modify: `helmfiles/tatara/values/tatara-operator/default.yaml` - ingress host/class/prefixes + chatImage.
- Modify (phase 2): `helmfiles/tatara/helmfile.yaml.gotmpl` - remove the standalone `tatara-chat` release.

---

## PHASE 1 - per-project memory ingress + cli --project

### Task 1: operator Config fields

**Files:** Modify `internal/config/config.go`

- [ ] **Step 1: Add fields to the `Config` struct** (after `LogLevel`):

```go
	LogLevel                 string
	IngressHost              string
	IngressClassName         string
	MemoryPathPrefix         string
	ChatPathPrefix           string
	ChatImage                string
```

- [ ] **Step 2: Populate them in `Load()`** (before the OIDC required checks):

```go
		LogLevel:                 getDefault("LOG_LEVEL", "info"),
		IngressHost:              os.Getenv("INGRESS_HOST"),
		IngressClassName:         getDefault("INGRESS_CLASS_NAME", "nginx"),
		MemoryPathPrefix:         getDefault("MEMORY_PATH_PREFIX", "/api/v1/memory"),
		ChatPathPrefix:           getDefault("CHAT_PATH_PREFIX", "/api/v1/chat"),
		ChatImage:                os.Getenv("CHAT_IMAGE"),
```

(`IngressHost`/`ChatImage` have no default - blank disables that feature.)

- [ ] **Step 3: Build + commit**

```bash
cd ~/Documents/tatara/tatara-operator && make build
git add internal/config/config.go && git commit -m "feat(config): ingress host/class/path-prefix + chat image"
```

### Task 2: memory.Config ingress fields

**Files:** Modify `internal/memory/memory.go`

- [ ] **Step 1: Add to the `memory.Config` struct** (after `ImagePullSecret`):

```go
	ImagePullSecret  string
	IngressHost      string
	IngressClassName string
	MemoryPathPrefix string
	ChatPathPrefix   string
	ChatImage        string
```

- [ ] **Step 2: Map them where the manager builds `memory.Config` from `config.Config`.** Grep `memory.Config{` in the repo (likely `cmd/manager/main.go` or controller setup). Add to that literal:

```go
		IngressHost:      cfg.IngressHost,
		IngressClassName: cfg.IngressClassName,
		MemoryPathPrefix: cfg.MemoryPathPrefix,
		ChatPathPrefix:   cfg.ChatPathPrefix,
		ChatImage:        cfg.ChatImage,
```

- [ ] **Step 3: Build + commit**

```bash
make build
git add internal/memory/memory.go <manager-file>
git commit -m "feat(memory): plumb ingress config into builder Config"
```

### Task 3: the Ingress builder (TDD)

**Files:** Create `internal/memory/ingress.go`, `internal/memory/ingress_test.go`

- [ ] **Step 1: Write the failing test** in `ingress_test.go`:

```go
package memory

import (
	"testing"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func testProject(name string) *tatarav1alpha1.Project {
	return &tatarav1alpha1.Project{ObjectMeta: metav1.ObjectMeta{Name: name, UID: "uid-1"}}
}

func TestIngress_NilWhenNoHost(t *testing.T) {
	if Ingress(testProject("alpha"), Config{Namespace: "tatara"}) != nil {
		t.Fatal("expected nil ingress when IngressHost is empty")
	}
}

func TestIngress_MemoryPathOnly(t *testing.T) {
	cfg := Config{Namespace: "tatara", IngressHost: "tatara.szymonrichert.pl", IngressClassName: "nginx", MemoryPathPrefix: "/api/v1/memory"}
	ing := Ingress(testProject("alpha"), cfg)
	if ing == nil {
		t.Fatal("expected non-nil ingress")
	}
	if ing.Name != "alpha" || ing.Namespace != "tatara" {
		t.Fatalf("meta: got %s/%s", ing.Namespace, ing.Name)
	}
	if ing.Annotations["nginx.ingress.kubernetes.io/rewrite-target"] != "/$2" {
		t.Fatalf("rewrite annotation missing: %v", ing.Annotations)
	}
	if *ing.Spec.IngressClassName != "nginx" {
		t.Fatalf("class: %v", ing.Spec.IngressClassName)
	}
	if len(ing.OwnerReferences) != 1 || ing.OwnerReferences[0].Name != "alpha" {
		t.Fatalf("owner ref: %v", ing.OwnerReferences)
	}
	paths := ing.Spec.Rules[0].HTTP.Paths
	if len(paths) != 1 {
		t.Fatalf("expected 1 path (memory only), got %d", len(paths))
	}
	if paths[0].Path != "/api/v1/memory/alpha(/|$)(.*)" {
		t.Fatalf("memory path: %s", paths[0].Path)
	}
	if paths[0].Backend.Service.Name != "mem-alpha" || paths[0].Backend.Service.Port.Number != 8080 {
		t.Fatalf("memory backend: %+v", paths[0].Backend.Service)
	}
	if ing.Spec.Rules[0].Host != "tatara.szymonrichert.pl" {
		t.Fatalf("host: %s", ing.Spec.Rules[0].Host)
	}
}

func TestIngress_AddsChatPath(t *testing.T) {
	cfg := Config{Namespace: "tatara", IngressHost: "h", MemoryPathPrefix: "/api/v1/memory", ChatPathPrefix: "/api/v1/chat"}
	ing := Ingress(testProject("alpha"), cfg)
	paths := ing.Spec.Rules[0].HTTP.Paths
	if len(paths) != 2 {
		t.Fatalf("expected memory+chat paths, got %d", len(paths))
	}
	if paths[1].Path != "/api/v1/chat/alpha(/|$)(.*)" || paths[1].Backend.Service.Name != "chat-alpha" {
		t.Fatalf("chat path/backend: %s %s", paths[1].Path, paths[1].Backend.Service.Name)
	}
}

func TestExternalMemoryURL(t *testing.T) {
	cfg := Config{IngressHost: "h", MemoryPathPrefix: "/api/v1/memory"}
	if got := ExternalMemoryURL("alpha", cfg); got != "https://h/api/v1/memory/alpha" {
		t.Fatalf("url: %s", got)
	}
	if ExternalMemoryURL("alpha", Config{}) != "" {
		t.Fatal("expected empty url when host unset")
	}
}
```

- [ ] **Step 2: Run - expect FAIL** (`Ingress`/`ExternalMemoryURL` undefined):

```bash
go test ./internal/memory/ -run 'TestIngress|TestExternalMemoryURL' -count=1
```

- [ ] **Step 3: Implement `internal/memory/ingress.go`:**

```go
package memory

import (
	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	networkingv1 "k8s.io/api/networking/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// Ingress builds the per-Project Ingress exposing this project's memory (and,
// when ChatPathPrefix is set, chat) on cfg.IngressHost under a project-scoped
// path with a rewrite that strips the prefix. Returns nil when cfg.IngressHost
// is empty (external exposure disabled). Owner-ref'd to the Project; no TLS
// block (the host cert is owned by the operator Ingress) and no nginx auth
// annotations (memory/chat enforce OIDC at the app).
func Ingress(p *tatarav1alpha1.Project, cfg Config) *networkingv1.Ingress {
	if cfg.IngressHost == "" {
		return nil
	}
	n := NamesFor(p.Name)
	pt := networkingv1.PathTypeImplementationSpecific
	paths := []networkingv1.HTTPIngressPath{{
		Path:     cfg.MemoryPathPrefix + "/" + p.Name + "(/|$)(.*)",
		PathType: &pt,
		Backend: networkingv1.IngressBackend{Service: &networkingv1.IngressServiceBackend{
			Name: n.Memory, Port: networkingv1.ServiceBackendPort{Number: 8080}}},
	}}
	if cfg.ChatPathPrefix != "" {
		paths = append(paths, networkingv1.HTTPIngressPath{
			Path:     cfg.ChatPathPrefix + "/" + p.Name + "(/|$)(.*)",
			PathType: &pt,
			Backend: networkingv1.IngressBackend{Service: &networkingv1.IngressServiceBackend{
				Name: "chat-" + p.Name, Port: networkingv1.ServiceBackendPort{Number: 8080}}},
		})
	}
	className := cfg.IngressClassName
	meta := objectMeta(p, cfg, p.Name)
	meta.Annotations = map[string]string{"nginx.ingress.kubernetes.io/rewrite-target": "/$2"}
	return &networkingv1.Ingress{
		TypeMeta:   metav1.TypeMeta{APIVersion: "networking.k8s.io/v1", Kind: "Ingress"},
		ObjectMeta: meta,
		Spec: networkingv1.IngressSpec{
			IngressClassName: &className,
			Rules: []networkingv1.IngressRule{{
				Host:             cfg.IngressHost,
				IngressRuleValue: networkingv1.IngressRuleValue{HTTP: &networkingv1.HTTPIngressRuleValue{Paths: paths}},
			}},
		},
	}
}

// ExternalMemoryURL is the external URL of a Project's memory, or "" if not exposed.
func ExternalMemoryURL(project string, cfg Config) string {
	if cfg.IngressHost == "" {
		return ""
	}
	return "https://" + cfg.IngressHost + cfg.MemoryPathPrefix + "/" + project
}
```

- [ ] **Step 4: Run - expect PASS.** Then `go vet ./internal/memory/`.

- [ ] **Step 5: Commit**

```bash
git add internal/memory/ingress.go internal/memory/ingress_test.go
git commit -m "feat(memory): per-Project Ingress builder + ExternalMemoryURL"
```

### Task 4: apply the ingress + set external endpoint

**Files:** Modify `internal/controller/project_memory.go`

- [ ] **Step 1: Append the ingress to the apply set** in `applyMemoryStack`, after the `objs := []client.Object{...}` literal:

```go
	if ing := memory.Ingress(p, cfg); ing != nil {
		objs = append(objs, ing)
	}
```

- [ ] **Step 2: Set `status.memory.externalEndpoint`** where the reconciler already sets `status.memory.endpoint` (grep `Endpoint:` / `MemoryStatus{` in this file). Add:

```go
		ExternalEndpoint: memory.ExternalMemoryURL(p.Name, r.MemoryConfig),
```

(Depends on Task 6 adding the `ExternalEndpoint` field.)

- [ ] **Step 3: Build + commit** (after Task 6 lands the field):

```bash
make build
git add internal/controller/project_memory.go
git commit -m "feat(controller): apply per-Project ingress + report external endpoint"
```

### Task 5: RBAC + Owns(Ingress)

**Files:** Modify `charts/tatara-operator/templates/rbac.yaml`, `internal/controller/project_controller.go`

- [ ] **Step 1: Add the ingress RBAC rule** to the manager Role in `rbac.yaml` (alongside the apps/deployments rule):

```yaml
  - apiGroups: ["networking.k8s.io"]
    resources: ["ingresses"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
```

- [ ] **Step 2: Add the kubebuilder RBAC marker** above the `ProjectReconciler.Reconcile` method (so generated RBAC stays in sync):

```go
// +kubebuilder:rbac:groups=networking.k8s.io,resources=ingresses,verbs=get;list;watch;create;update;patch;delete
```

- [ ] **Step 3: Add `Owns(&networkingv1.Ingress{})`** in `SetupWithManager` (add the `networkingv1 "k8s.io/api/networking/v1"` import):

```go
	return ctrl.NewControllerManagedBy(mgr).
		For(&tataradevv1alpha1.Project{}).
		// ...existing Owns(...)...
		Owns(&networkingv1.Ingress{}).
		Complete(r)
```

- [ ] **Step 4: Regenerate + build + commit**

```bash
make generate manifests build
git add charts/tatara-operator/templates/rbac.yaml internal/controller/project_controller.go config/
git commit -m "feat(rbac): operator manages per-Project ingresses + watches them"
```

### Task 6: ProjectStatus.MemoryStatus.ExternalEndpoint

**Files:** Modify `api/v1alpha1/project_types.go`

- [ ] **Step 1: Add the field to `MemoryStatus`:**

```go
type MemoryStatus struct {
	Phase            string `json:"phase,omitempty"`
	Endpoint         string `json:"endpoint,omitempty"`
	ExternalEndpoint string `json:"externalEndpoint,omitempty"`
}
```

- [ ] **Step 2: Regenerate CRDs + deepcopy, build:**

```bash
make generate manifests build
```

- [ ] **Step 3: Commit**

```bash
git add api/v1alpha1/project_types.go charts/tatara-operator/crds config/
git commit -m "feat(api): MemoryStatus.externalEndpoint"
```

### Task 7: chart values + env wiring

**Files:** Modify `charts/tatara-operator/values.yaml`, `charts/tatara-operator/templates/configmap.yaml`

- [ ] **Step 1: Add chart values** to `values.yaml`:

```yaml
ingressHost: ""
ingressClassName: "nginx"
memoryPathPrefix: "/api/v1/memory"
chatPathPrefix: "/api/v1/chat"
chatImage: ""
```

- [ ] **Step 2: Add the ConfigMap env keys** in `configmap.yaml` (matching the existing camelCase->SCREAMING_SNAKE pattern there):

```yaml
  INGRESS_HOST: {{ .Values.ingressHost | quote }}
  INGRESS_CLASS_NAME: {{ .Values.ingressClassName | quote }}
  MEMORY_PATH_PREFIX: {{ .Values.memoryPathPrefix | quote }}
  CHAT_PATH_PREFIX: {{ .Values.chatPathPrefix | quote }}
  CHAT_IMAGE: {{ .Values.chatImage | quote }}
```

- [ ] **Step 3: helm lint + commit**

```bash
make chart-lint
git add charts/tatara-operator/values.yaml charts/tatara-operator/templates/configmap.yaml
git commit -m "feat(chart): expose ingress host/class/path-prefix + chat image config"
```

### Task 8: tatara-cli --project / -p (TDD)

**Files:** Modify the tatara-cli root command + memory client base-URL resolution; create a test.

- [ ] **Step 1: Locate the URL resolution.** `cd ~/Documents/tatara/tatara-cli && grep -rn "MEMORY_URL\|base-url\|BaseURL\|api/v1/memory" internal cmd`.

- [ ] **Step 2: Write a failing test** for the composition helper (e.g. `internal/client/baseurl_test.go`):

```go
func TestMemoryURLForProject(t *testing.T) {
	if got := MemoryURLForProject("https://h/api/v1/memory", "alpha"); got != "https://h/api/v1/memory/alpha" {
		t.Fatalf("got %s", got)
	}
	if got := MemoryURLForProject("https://h/api/v1/memory/", "alpha"); got != "https://h/api/v1/memory/alpha" {
		t.Fatalf("trailing slash: %s", got)
	}
	if got := MemoryURLForProject("https://h/api/v1/memory", ""); got != "https://h/api/v1/memory" {
		t.Fatalf("no project: %s", got)
	}
}
```

- [ ] **Step 3: Run - expect FAIL.** `go test ./internal/client/ -run TestMemoryURLForProject -count=1`

- [ ] **Step 4: Implement `MemoryURLForProject`** (trim a trailing `/`, append `/<project>` when project non-empty):

```go
func MemoryURLForProject(base, project string) string {
	base = strings.TrimRight(base, "/")
	if project == "" {
		return base
	}
	return base + "/" + project
}
```

- [ ] **Step 5: Wire the flag.** On the root cobra command add a persistent flag and resolve the project (flag > `TATARA_PROJECT` env), then pass the project through `MemoryURLForProject` when constructing the memory client base URL:

```go
rootCmd.PersistentFlags().StringP("project", "p", os.Getenv("TATARA_PROJECT"), "tatara project (per-project memory path); env TATARA_PROJECT")
```

- [ ] **Step 6: Run - expect PASS.** `make test`

- [ ] **Step 7: Commit**

```bash
git add internal/client/ cmd/
git commit -m "feat(cli): --project/-p (+ TATARA_PROJECT) composes per-project memory URL"
```

### Task 9: infra values (gated deploy)

**Files:** Modify `~/Documents/infra/helmfile/helmfiles/tatara/values/tatara-operator/default.yaml`

- [ ] **Step 1: Add the ingress config** (fresh main + branch):

```yaml
ingressHost: tatara.szymonrichert.pl
ingressClassName: nginx
memoryPathPrefix: /api/v1/memory
chatPathPrefix: /api/v1/chat
chatImage: ""   # set in phase 2 once tatara-chat image is published
```

- [ ] **Step 2: `helmfile -e default diff -l name=tatara-operator`**, then commit + MR (auto-merge). Bump the operator `image.tag` pin to the new CI-built operator image (after the operator change is merged to main and its pipeline pushes). Deploy. Re-apply the Project CRD server-side (`kubectl apply --server-side` the regenerated CRD - Helm does not upgrade CRDs).

### Task 10: phase-1 end-to-end verification

- [ ] Merge operator + cli to main (their CI builds+pushes images). Bump infra pins + deploy. Then:

```bash
kubectl get ingress tatara -n tatara -o jsonpath='{.spec.rules[0].http.paths[*].path}'   # /api/v1/memory/tatara(/|$)(.*)
kubectl get project tatara -o jsonpath='{.status.memory.externalEndpoint}'                # https://tatara.szymonrichert.pl/api/v1/memory/tatara
~/go/bin/tatara -p tatara raw GET '/code/entities?repo=tatara-operator&q=Reconcile&limit=2'  # graph data via the ingress
```

Confirm the `tatara` MCP server in `.mcp.json` (`args: ["-p","tatara","mcp"]`) exposes working `code_*` tools.

---

## PHASE 2 - per-project chat provisioning + ingress path

### Task 11: chat builders (TDD)

**Files:** Create `internal/chat/chat.go` (helpers + Names), `internal/chat/chat_builders.go` (`ChatConfigMap`/`ChatDeployment`/`ChatService` for `chat-<project>`, port 8080, image `cfg.ChatImage`, env -> `mem-<project>` endpoint + operator URL + OIDC), `internal/chat/chat_builders_test.go`. Mirror `internal/memory/memory_builders.go` + `memory.go` (objectMeta/ownerRef/labels, owner-ref'd, ClusterIP Service on 8080). Verify the chat container's real listen port + required env by reading `~/Documents/tatara/tatara-chat/cmd` + its config; adjust the port/env to match (the design assumes 8080).

- [ ] Write failing tests asserting names (`chat-<project>`), owner-ref, image, port 8080, and the memory-endpoint env. Implement. `go test ./internal/chat/ -count=1`. Commit.

### Task 12: provision chat in reconcile + status

**Files:** Modify `internal/controller/project_memory.go` (or a new `project_chat.go`), `api/v1alpha1/project_types.go`.

- [ ] Append `chat.ChatConfigMap/ChatDeployment/ChatService(p, cfg)` to the apply set, gated on `cfg.ChatImage != ""`. Add `ChatStatus{Phase,Endpoint,ExternalEndpoint}` + `ProjectStatus.Chat`; set them (external = `https://<host><chatPathPrefix>/<project>`). The Ingress builder already adds the chat path when `ChatPathPrefix` is set (Task 3). `make generate manifests build`. Add chat RBAC reuse (deployments/services already granted). Commit.

### Task 13: retire standalone tatara-chat + deploy

**Files:** Modify `~/Documents/infra/helmfile/helmfiles/tatara/helmfile.yaml.gotmpl`, `helmfiles/tatara/values/tatara-operator/default.yaml`.

- [ ] Set `chatImage` in the operator values to the published `harbor.szymonrichert.pl/containers/tatara-chat:<tag>` (built by chat's CI). Remove the standalone `tatara-chat` release from the helmfile (the per-project chat supersedes it). MR + deploy. Verify `kubectl get ingress tatara` now has both `/api/v1/memory/tatara` and `/api/v1/chat/tatara` paths, `chat-tatara` Deployment+Service exist, and `status.chat.externalEndpoint` is set.

---

## Self-review notes

- Spec coverage: config fields=T1/T2/T7; Ingress builder (paths/rewrite/no-TLS/no-auth/owner-ref/gated)=T3; apply=T4; status.memory.externalEndpoint=T4/T6; RBAC+Owns=T5; cli --project/-p+env=T8; infra=T9; phase-1 verify=T10; chat provisioning=T11/T12; chat ingress path=T3 (ChatPathPrefix branch); retire single chat=T13. All spec sections covered.
- Type consistency: `memory.Config` fields (IngressHost/IngressClassName/MemoryPathPrefix/ChatPathPrefix/ChatImage) used identically in config map (T2), builder (T3), endpoint (T4). `MemoryStatus.ExternalEndpoint` defined T6, used T4. cli `MemoryURLForProject` defined+used T8.
- No local image builds: T9/T10/T13 ship via CI per the MEMORY rule.
- Wiring locations (manager `memory.Config{}` map, configmap.yaml key block, cli URL construction, status-set line) are referenced by grep in their tasks; the implementer reads the exact file - these are stable, well-located edits, not placeholders.
