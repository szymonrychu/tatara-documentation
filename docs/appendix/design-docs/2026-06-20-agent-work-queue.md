# Agent-work Queue (in-operator) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fold a durable, ordered admission queue into tatara-operator so every agent-spawning path enqueues a `QueuedEvent` instead of creating a Task directly; a dispatcher admits them in strict global `seq` FIFO under capacity `N` with a reserved alert pool `M`.

**Architecture:** A new `QueuedEvent` CRD is the durable buffer in etcd. Producers (SCM + Grafana webhooks, cron scans) write a QueuedEvent carrying a Task *blueprint* in its `Payload`. A new dispatcher controller drains the alert pool to `M` then the normal pool to `N` in `seq` order, building the Task from the blueprint on admission and freeing the slot when that Task reaches terminal. The execution-time `atConcurrencyCap`, the `maxOpenTasks` creation budget, and the per-repo `laneOccupancy`/`selectPerRepo`/`selectCandidates` selection layer are removed.

**Tech Stack:** Go (controller-runtime / kubebuilder), envtest, Prometheus client_golang, chi (webhook router), mise toolchain.

## Global Constraints

- Newest stable Go; pin exact minor in `go.mod` (do not change the pin in this plan).
- KISS; no premature abstraction. NEVER introduce tech-debt; if forced, record in `MEMORY.md`.
- JSON logs via stdlib `log/slog`. Log every business action at INFO with structured fields.
- Metrics for everything that counts/fails: counters/gauges via the existing `internal/obs` pattern; exposed on the existing `/metrics`.
- Charts cluster-agnostic; all new tunables are camelCase scalars on the Project CRD (no lists/plain-envs in values).
- New CRD fields are NOT auto-upgraded by Helm: deploy runbook must `kubectl apply` the regenerated CRDs (operator-CRD-gap memory).
- Single leader-elected active operator is assumed (seq allocation relies on it).
- Build/test through mise: `mise exec -- go test ./...`, `mise run test`, `make manifests`.
- Conventional commits: `type: short imperative`. Branch off `main` in a worktree; never build/deploy from a worktree.
- TDD: failing test first, minimal impl, green, commit. Frequent commits.

## File Structure

- `api/v1alpha1/queuedevent_types.go` (create) - `QueuedEvent`/`QueuedEventList`, `QueuedEventSpec`, `QueuedEventPayload`, `QueuedEventStatus`, kind/class constants, `ValidateQueuedEventSpec`. Owns the queue data model.
- `api/v1alpha1/queuedevent_types_test.go` (create) - validation tests.
- `api/v1alpha1/zz_generated.deepcopy.go` (modify, via `make generate`) - deepcopy for the new types.
- `api/v1alpha1/groupversion_info.go` or `*_types.go` `init()` (modify) - register `QueuedEvent` in the scheme builder (kubebuilder `SchemeBuilder.Register`).
- `api/v1alpha1/project_types.go` (modify) - add `Queue *QueueSpec` to `ProjectSpec`; `QueueSpec` type; `QueueCapacity`/`AlertCapacity`/`QueuedAutonomousCap` default helpers.
- `api/v1alpha1/project_types_test.go` (modify/create) - default helper tests.
- `internal/queue/allocator.go` (create) - `SeqAllocator` + `Recover`/`Next`; `SeqRecoverer` manager Runnable.
- `internal/queue/allocator_test.go` (create) - monotonic + recovery tests.
- `internal/controller/queue_enqueue.go` (create) - `EnqueueEvent` helper + `buildTaskFromQueuedEvent` + dedup query. Shared by webhook + crons + dispatcher.
- `internal/controller/queue_enqueue_test.go` (create) - enqueue/build/dedup tests.
- `internal/controller/queue_controller.go` (create) - `DispatcherReconciler` (inflight, Done-marking, admission, migration, SetupWithManager).
- `internal/controller/queue_controller_test.go` (create) - dispatcher unit + envtest.
- `internal/controller/projectscan.go` (modify) - crons enqueue instead of create; delete `selectCandidates`/`selectPerRepo`/`laneOccupancy`/`maxOpenTasks`/`openTaskCount`/budget threading; `createScanTask`/`createBrainstormTask`/`createHealthCheckTask` become enqueue.
- `internal/controller/task_controller.go` (modify) - delete `atConcurrencyCap` + callsite.
- `internal/controller/lifecycle.go` (modify) - delete the `atConcurrencyCap` callsite.
- `internal/webhook/server.go` (modify) - `handleWorkItem`, `createLifecycleTaskAtTriage`, `createIncidentTask`, `reactivateTask` enqueue instead of create; `incidentDedup` moves into the enqueue dedup.
- `internal/webhook/server.go` `Config` (modify) - add `Seq *queue.SeqAllocator`.
- `internal/obs/operator_metrics.go` (modify) - queue depth/inflight/admission metrics.
- `cmd/manager/wire.go` (modify) - construct `SeqAllocator`, register `SeqRecoverer` + `DispatcherReconciler`, inject allocator into `ProjectReconciler` + webhook `Config`.
- `config/crd/...` + `charts/tatara-operator/crds/...` (modify, via `make manifests`) - regenerated CRD manifests.

---

### Task 1: QueuedEvent CRD types + validation

**Files:**
- Create: `api/v1alpha1/queuedevent_types.go`
- Create: `api/v1alpha1/queuedevent_types_test.go`
- Modify: `api/v1alpha1/zz_generated.deepcopy.go` (via `make generate`)

**Interfaces:**
- Produces: `QueuedEvent`, `QueuedEventList`, `QueuedEventSpec{Seq int64; Class string; Kind string; Autonomous bool; ProjectRef string; RepositoryRef string; DedupKey string; Payload QueuedEventPayload}`, `QueuedEventPayload{Goal string; Kind string; RepositoryRef string; Source *TaskSource; Labels map[string]string; Annotations map[string]string; GenerateName string; Name string; Provider string; PodRepo string}`, `QueuedEventStatus{State string; TaskRef string; AdmittedAt *metav1.Time}`, consts `QueueClassNormal="normal"`, `QueueClassAlert="alert"`, `QueueStateQueued="Queued"`, `QueueStateAdmitted="Admitted"`, `QueueStateDone="Done"`, `func ValidateQueuedEventSpec(spec QueuedEventSpec) error`.

- [ ] **Step 1: Write the failing test**

```go
// api/v1alpha1/queuedevent_types_test.go
package v1alpha1

import "testing"

func TestValidateQueuedEventSpec(t *testing.T) {
	tests := []struct {
		name    string
		spec    QueuedEventSpec
		wantErr bool
	}{
		{"valid normal repo-scoped", QueuedEventSpec{Seq: 1, Class: QueueClassNormal, Kind: "issueLifecycle", ProjectRef: "p", RepositoryRef: "r", Payload: QueuedEventPayload{Kind: "issueLifecycle"}}, false},
		{"valid alert project-scoped", QueuedEventSpec{Seq: 2, Class: QueueClassAlert, Kind: "incident", ProjectRef: "p", Payload: QueuedEventPayload{Kind: "incident"}}, false},
		{"bad class", QueuedEventSpec{Seq: 1, Class: "urgent", Kind: "incident", ProjectRef: "p"}, true},
		{"bad kind", QueuedEventSpec{Seq: 1, Class: QueueClassNormal, Kind: "nope", ProjectRef: "p"}, true},
		{"missing projectRef", QueuedEventSpec{Seq: 1, Class: QueueClassNormal, Kind: "review"}, true},
		{"project-scoped kind with repoRef", QueuedEventSpec{Seq: 1, Class: QueueClassAlert, Kind: "incident", ProjectRef: "p", RepositoryRef: "r"}, true},
		{"repo-scoped kind without repoRef", QueuedEventSpec{Seq: 1, Class: QueueClassNormal, Kind: "issueLifecycle", ProjectRef: "p"}, true},
		{"zero seq", QueuedEventSpec{Seq: 0, Class: QueueClassNormal, Kind: "review", ProjectRef: "p", RepositoryRef: "r"}, true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := ValidateQueuedEventSpec(tt.spec)
			if (err != nil) != tt.wantErr {
				t.Fatalf("err=%v wantErr=%v", err, tt.wantErr)
			}
		})
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./api/v1alpha1/ -run TestValidateQueuedEventSpec -v`
Expected: FAIL (undefined: QueuedEventSpec / ValidateQueuedEventSpec).

- [ ] **Step 3: Write minimal implementation**

```go
// api/v1alpha1/queuedevent_types.go
package v1alpha1

import (
	"fmt"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

const (
	QueueClassNormal = "normal"
	QueueClassAlert  = "alert"

	QueueStateQueued   = "Queued"
	QueueStateAdmitted = "Admitted"
	QueueStateDone     = "Done"
)

// QueuedEventPayload is the Task blueprint a producer stashes; the dispatcher
// rebuilds the Task from it verbatim on admission. Producers keep ownership of
// label/goal/source construction so the dispatcher stays generic.
type QueuedEventPayload struct {
	Goal          string            `json:"goal,omitempty"`
	Kind          string            `json:"kind"`
	RepositoryRef string            `json:"repositoryRef,omitempty"`
	Source        *TaskSource       `json:"source,omitempty"`
	Labels        map[string]string `json:"labels,omitempty"`
	Annotations   map[string]string `json:"annotations,omitempty"`
	// GenerateName is used when Name is empty; Name is a fixed deterministic
	// Task name (issueLifecycle) that makes admission idempotent.
	GenerateName string `json:"generateName,omitempty"`
	Name         string `json:"name,omitempty"`
	// Provider + PodRepo feed agent.StampPodName on the rebuilt Task.
	Provider string `json:"provider,omitempty"`
	PodRepo  string `json:"podRepo,omitempty"`
}

type QueuedEventSpec struct {
	Seq           int64              `json:"seq"`
	Class         string             `json:"class"`
	Kind          string             `json:"kind"`
	Autonomous    bool               `json:"autonomous,omitempty"`
	ProjectRef    string             `json:"projectRef"`
	RepositoryRef string             `json:"repositoryRef,omitempty"`
	DedupKey      string             `json:"dedupKey,omitempty"`
	Payload       QueuedEventPayload `json:"payload"`
}

type QueuedEventStatus struct {
	State      string       `json:"state,omitempty"`
	TaskRef    string       `json:"taskRef,omitempty"`
	AdmittedAt *metav1.Time `json:"admittedAt,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Seq",type=integer,JSONPath=`.spec.seq`
// +kubebuilder:printcolumn:name="Class",type=string,JSONPath=`.spec.class`
// +kubebuilder:printcolumn:name="Kind",type=string,JSONPath=`.spec.kind`
// +kubebuilder:printcolumn:name="State",type=string,JSONPath=`.status.state`
type QueuedEvent struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`
	Spec              QueuedEventSpec   `json:"spec,omitempty"`
	Status            QueuedEventStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true
type QueuedEventList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []QueuedEvent `json:"items"`
}

func init() {
	SchemeBuilder.Register(&QueuedEvent{}, &QueuedEventList{})
}

// ValidateQueuedEventSpec mirrors ValidateTaskSpec's kind/repo-scoping rules.
func ValidateQueuedEventSpec(spec QueuedEventSpec) error {
	if spec.Seq <= 0 {
		return fmt.Errorf("queuedevent: seq must be positive")
	}
	if spec.Class != QueueClassNormal && spec.Class != QueueClassAlert {
		return fmt.Errorf("queuedevent: invalid class %q", spec.Class)
	}
	if spec.ProjectRef == "" {
		return fmt.Errorf("queuedevent: projectRef required")
	}
	if !repoScopedKinds[spec.Kind] && !projectScopedKinds[spec.Kind] {
		return fmt.Errorf("queuedevent: invalid kind %q", spec.Kind)
	}
	if projectScopedKinds[spec.Kind] && spec.RepositoryRef != "" {
		return fmt.Errorf("queuedevent: kind %q is project-scoped, repositoryRef must be empty", spec.Kind)
	}
	if repoScopedKinds[spec.Kind] && spec.RepositoryRef == "" {
		return fmt.Errorf("queuedevent: kind %q requires repositoryRef", spec.Kind)
	}
	return nil
}
```

Note: `repoScopedKinds`/`projectScopedKinds` already exist in `task_types.go` (same package). `issueLifecycle`/`review`/`mrScan`/`issueScan` must be present in `repoScopedKinds`; if any cron kind is missing there, add it in this step (boy-scout) and assert via the test's "valid normal repo-scoped" case.

- [ ] **Step 4: Generate deepcopy + run tests**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- make generate && mise exec -- go test ./api/v1alpha1/ -run TestValidateQueuedEventSpec -v`
Expected: `make generate` updates `zz_generated.deepcopy.go`; test PASS.

- [ ] **Step 5: Commit**

```bash
git add api/v1alpha1/queuedevent_types.go api/v1alpha1/queuedevent_types_test.go api/v1alpha1/zz_generated.deepcopy.go
git commit -m "feat: QueuedEvent CRD types + validation"
```

---

### Task 2: QueueSpec on ProjectSpec + default helpers

**Files:**
- Modify: `api/v1alpha1/project_types.go` (add `Queue *QueueSpec` to `ProjectSpec` near `Scm`/`Memory`/`Grafana`; add `QueueSpec`)
- Modify: `api/v1alpha1/project_types_test.go`

**Interfaces:**
- Consumes: `ProjectSpec` (Task 0/existing), `QueuedEvent` consts (Task 1).
- Produces: `type QueueSpec struct{ Capacity int; AlertCapacity int; QueuedAutonomousCap int }`, methods `func (p *Project) QueueCapacity() int`, `func (p *Project) AlertCapacity() int`, `func (p *Project) QueuedAutonomousCap() int`.

- [ ] **Step 1: Write the failing test**

```go
// api/v1alpha1/project_types_test.go (add)
func TestQueueDefaults(t *testing.T) {
	// nil Queue: capacity falls back to MaxConcurrentTasks, cap to MaxOpenTasks, alert to 1.
	p := &Project{Spec: ProjectSpec{MaxConcurrentTasks: 5, MaxOpenTasks: 6}}
	if got := p.QueueCapacity(); got != 5 {
		t.Fatalf("QueueCapacity nil-queue = %d, want 5", got)
	}
	if got := p.QueuedAutonomousCap(); got != 6 {
		t.Fatalf("QueuedAutonomousCap nil-queue = %d, want 6", got)
	}
	if got := p.AlertCapacity(); got != 1 {
		t.Fatalf("AlertCapacity nil-queue = %d, want 1", got)
	}
	// explicit Queue overrides.
	p2 := &Project{Spec: ProjectSpec{MaxConcurrentTasks: 5, Queue: &QueueSpec{Capacity: 2, AlertCapacity: 3, QueuedAutonomousCap: 10}}}
	if p2.QueueCapacity() != 2 || p2.AlertCapacity() != 3 || p2.QueuedAutonomousCap() != 10 {
		t.Fatalf("explicit queue not honoured: %d/%d/%d", p2.QueueCapacity(), p2.AlertCapacity(), p2.QueuedAutonomousCap())
	}
	// hard floor when nothing set anywhere.
	p3 := &Project{}
	if p3.QueueCapacity() != 3 || p3.QueuedAutonomousCap() != 3 || p3.AlertCapacity() != 1 {
		t.Fatalf("hard floors wrong: %d/%d/%d", p3.QueueCapacity(), p3.QueuedAutonomousCap(), p3.AlertCapacity())
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./api/v1alpha1/ -run TestQueueDefaults -v`
Expected: FAIL (undefined: QueueSpec / QueueCapacity).

- [ ] **Step 3: Write minimal implementation**

```go
// api/v1alpha1/project_types.go

// in ProjectSpec, sibling of Grafana:
//   // +optional
//   Queue *QueueSpec `json:"queue,omitempty"`

// QueueSpec configures the in-operator agent-work admission queue.
type QueueSpec struct {
	// Capacity N: max concurrently-admitted normal-class events (defaults to
	// MaxConcurrentTasks, else 3).
	// +optional
	Capacity int `json:"capacity,omitempty"`
	// AlertCapacity M: reserved concurrent slots for alert-class events (default 1).
	// +optional
	AlertCapacity int `json:"alertCapacity,omitempty"`
	// QueuedAutonomousCap K: max Queued autonomous (cron) events; crons stop
	// enqueuing past it (defaults to MaxOpenTasks, else 3). Webhooks/alerts exempt.
	// +optional
	QueuedAutonomousCap int `json:"queuedAutonomousCap,omitempty"`
}

func (p *Project) QueueCapacity() int {
	if p.Spec.Queue != nil && p.Spec.Queue.Capacity > 0 {
		return p.Spec.Queue.Capacity
	}
	if p.Spec.MaxConcurrentTasks > 0 {
		return p.Spec.MaxConcurrentTasks
	}
	return 3
}

func (p *Project) AlertCapacity() int {
	if p.Spec.Queue != nil && p.Spec.Queue.AlertCapacity > 0 {
		return p.Spec.Queue.AlertCapacity
	}
	return 1
}

func (p *Project) QueuedAutonomousCap() int {
	if p.Spec.Queue != nil && p.Spec.Queue.QueuedAutonomousCap > 0 {
		return p.Spec.Queue.QueuedAutonomousCap
	}
	if p.Spec.MaxOpenTasks > 0 {
		return p.Spec.MaxOpenTasks
	}
	return 3
}
```

- [ ] **Step 4: Run tests + regenerate deepcopy**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- make generate && mise exec -- go test ./api/v1alpha1/ -run TestQueueDefaults -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/v1alpha1/project_types.go api/v1alpha1/project_types_test.go api/v1alpha1/zz_generated.deepcopy.go
git commit -m "feat: QueueSpec on ProjectSpec with capacity/alert/cap defaults"
```

---

### Task 3: SeqAllocator + boot recovery

**Files:**
- Create: `internal/queue/allocator.go`
- Create: `internal/queue/allocator_test.go`

**Interfaces:**
- Produces: `type SeqAllocator struct{...}`, `func NewSeqAllocator() *SeqAllocator`, `func (a *SeqAllocator) Recover(maxSeq int64)`, `func (a *SeqAllocator) Next() int64`, `type SeqRecoverer struct{ Client client.Client; Alloc *SeqAllocator; Namespace string }` implementing `Start(ctx context.Context) error` (manager.Runnable).
- Consumes: `QueuedEventList` (Task 1).

- [ ] **Step 1: Write the failing test**

```go
// internal/queue/allocator_test.go
package queue

import (
	"sync"
	"testing"
)

func TestSeqAllocator_MonotonicFromZero(t *testing.T) {
	a := NewSeqAllocator()
	if a.Next() != 1 || a.Next() != 2 || a.Next() != 3 {
		t.Fatal("expected 1,2,3 from a fresh allocator")
	}
}

func TestSeqAllocator_RecoverMaxPlusOne(t *testing.T) {
	a := NewSeqAllocator()
	a.Recover(41)
	if got := a.Next(); got != 42 {
		t.Fatalf("Next after Recover(41) = %d, want 42", got)
	}
}

func TestSeqAllocator_ConcurrentUnique(t *testing.T) {
	a := NewSeqAllocator()
	const n = 1000
	seen := make([]int64, n)
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(i int) { defer wg.Done(); seen[i] = a.Next() }(i)
	}
	wg.Wait()
	set := map[int64]bool{}
	for _, v := range seen {
		if set[v] {
			t.Fatalf("duplicate seq %d", v)
		}
		set[v] = true
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/queue/ -v`
Expected: FAIL (no package / undefined SeqAllocator).

- [ ] **Step 3: Write minimal implementation**

```go
// internal/queue/allocator.go
package queue

import (
	"context"
	"sync"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

// SeqAllocator hands out a strictly increasing int64 sequence. Correctness
// relies on a single leader-elected active operator (one allocator instance).
type SeqAllocator struct {
	mu   sync.Mutex
	next int64
}

func NewSeqAllocator() *SeqAllocator { return &SeqAllocator{next: 0} }

// Recover sets the counter so the next allocation is maxSeq+1. Call once at boot
// with the max Seq of existing QueuedEvents.
func (a *SeqAllocator) Recover(maxSeq int64) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if maxSeq > a.next {
		a.next = maxSeq
	}
}

func (a *SeqAllocator) Next() int64 {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.next++
	return a.next
}

// SeqRecoverer is a manager.Runnable that recovers the allocator high-water mark
// from existing QueuedEvents after the cache syncs.
type SeqRecoverer struct {
	Client    client.Client
	Alloc     *SeqAllocator
	Namespace string
}

func (s *SeqRecoverer) Start(ctx context.Context) error {
	var list tatarav1alpha1.QueuedEventList
	if err := s.Client.List(ctx, &list, client.InNamespace(s.Namespace)); err != nil {
		return err
	}
	var max int64
	for i := range list.Items {
		if list.Items[i].Spec.Seq > max {
			max = list.Items[i].Spec.Seq
		}
	}
	s.Alloc.Recover(max)
	log.FromContext(ctx).Info("queue: seq recovered", "action", "seq_recover", "max", max)
	return nil
}
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/queue/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/queue/
git commit -m "feat: SeqAllocator + boot recovery runnable for the work queue"
```

---

### Task 4: Enqueue helper + Task builder + dedup

**Files:**
- Create: `internal/controller/queue_enqueue.go`
- Create: `internal/controller/queue_enqueue_test.go`

**Interfaces:**
- Consumes: `SeqAllocator` (Task 3), `QueuedEvent*` (Task 1), existing `agent.StampPodName(task, project, provider, repo)`, existing `Task`/`TaskSource`.
- Produces:
  - `const LabelQueuedEvent = "tatara.dev/queued-event"`, `const LabelDedupKey = "tatara.dev/dedup-key"`
  - `func EnqueueEvent(ctx context.Context, c client.Client, alloc *queue.SeqAllocator, proj *tatarav1alpha1.Project, class string, autonomous bool, dedupKey string, payload tatarav1alpha1.QueuedEventPayload) (*tatarav1alpha1.QueuedEvent, bool, error)` - returns `(event, created, error)`; `created=false` on dedup skip.
  - `func buildTaskFromQueuedEvent(qe *tatarav1alpha1.QueuedEvent, proj *tatarav1alpha1.Project, scheme *runtime.Scheme) (*tatarav1alpha1.Task, error)`
  - `func dedupExists(ctx context.Context, c client.Client, ns, projectRef, dedupKey string) (bool, error)`

- [ ] **Step 1: Write the failing test**

```go
// internal/controller/queue_enqueue_test.go
package controller

import (
	"context"
	"testing"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/queue"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func testProject(name, ns string) *tatarav1alpha1.Project {
	return &tatarav1alpha1.Project{ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: ns}}
}

func TestEnqueueEvent_AssignsSeqAndFields(t *testing.T) {
	scheme := newTestScheme(t) // helper already present in suite_test.go / export_test.go
	c := fake.NewClientBuilder().WithScheme(scheme).Build()
	alloc := queue.NewSeqAllocator()
	proj := testProject("p", "tatara")
	pl := tatarav1alpha1.QueuedEventPayload{Kind: "incident", GenerateName: "incident-"}
	qe, created, err := EnqueueEvent(context.Background(), c, alloc, proj, tatarav1alpha1.QueueClassAlert, false, "grp1", pl)
	if err != nil || !created {
		t.Fatalf("created=%v err=%v", created, err)
	}
	if qe.Spec.Seq != 1 || qe.Spec.Class != tatarav1alpha1.QueueClassAlert || qe.Spec.Kind != "incident" {
		t.Fatalf("bad spec: %+v", qe.Spec)
	}
	if qe.Labels[LabelDedupKey] != "grp1" || qe.Status.State != tatarav1alpha1.QueueStateQueued {
		t.Fatalf("bad labels/state: %v %q", qe.Labels, qe.Status.State)
	}
}

func TestEnqueueEvent_DedupSkips(t *testing.T) {
	scheme := newTestScheme(t)
	c := fake.NewClientBuilder().WithScheme(scheme).Build()
	alloc := queue.NewSeqAllocator()
	proj := testProject("p", "tatara")
	pl := tatarav1alpha1.QueuedEventPayload{Kind: "incident", GenerateName: "incident-"}
	if _, created, _ := EnqueueEvent(context.Background(), c, alloc, proj, tatarav1alpha1.QueueClassAlert, false, "grp1", pl); !created {
		t.Fatal("first enqueue should create")
	}
	_, created, err := EnqueueEvent(context.Background(), c, alloc, proj, tatarav1alpha1.QueueClassAlert, false, "grp1", pl)
	if err != nil {
		t.Fatal(err)
	}
	if created {
		t.Fatal("duplicate dedupKey should skip")
	}
}

func TestBuildTaskFromQueuedEvent(t *testing.T) {
	scheme := newTestScheme(t)
	proj := testProject("p", "tatara")
	qe := &tatarav1alpha1.QueuedEvent{
		ObjectMeta: metav1.ObjectMeta{Name: "qe-1", Namespace: "tatara"},
		Spec: tatarav1alpha1.QueuedEventSpec{
			Seq: 1, Class: tatarav1alpha1.QueueClassNormal, Kind: "review", ProjectRef: "p", RepositoryRef: "r",
			Payload: tatarav1alpha1.QueuedEventPayload{
				Kind: "review", RepositoryRef: "r", Goal: "g", GenerateName: "scan-",
				Labels: map[string]string{"x": "y"}, Provider: "github", PodRepo: "r",
			},
		},
	}
	task, err := buildTaskFromQueuedEvent(qe, proj, scheme)
	if err != nil {
		t.Fatal(err)
	}
	if task.Spec.Kind != "review" || task.Spec.Goal != "g" || task.Spec.RepositoryRef != "r" {
		t.Fatalf("bad task spec: %+v", task.Spec)
	}
	if task.Labels[LabelQueuedEvent] != "qe-1" || task.Labels["x"] != "y" {
		t.Fatalf("missing labels: %v", task.Labels)
	}
	if task.GenerateName != "scan-" {
		t.Fatalf("bad generateName: %q", task.GenerateName)
	}
}
```

If `newTestScheme` does not exist yet, add a tiny helper in `export_test.go` that builds a scheme with `tatarav1alpha1.AddToScheme`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run 'TestEnqueueEvent|TestBuildTaskFromQueuedEvent' -v`
Expected: FAIL (undefined EnqueueEvent/buildTaskFromQueuedEvent/LabelQueuedEvent).

- [ ] **Step 3: Write minimal implementation**

```go
// internal/controller/queue_enqueue.go
package controller

import (
	"context"
	"fmt"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/agent"
	"github.com/szymonrychu/tatara-operator/internal/queue"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
)

const (
	LabelQueuedEvent = "tatara.dev/queued-event"
	LabelDedupKey    = "tatara.dev/dedup-key"
)

// dedupExists reports whether a non-Done QueuedEvent or a non-terminal Task with
// dedupKey already exists for the project.
func dedupExists(ctx context.Context, c client.Client, ns, projectRef, dedupKey string) (bool, error) {
	if dedupKey == "" {
		return false, nil
	}
	var qel tatarav1alpha1.QueuedEventList
	if err := c.List(ctx, &qel, client.InNamespace(ns), client.MatchingLabels{LabelDedupKey: dedupKey}); err != nil {
		return false, err
	}
	for i := range qel.Items {
		if qel.Items[i].Spec.ProjectRef == projectRef && qel.Items[i].Status.State != tatarav1alpha1.QueueStateDone {
			return true, nil
		}
	}
	var tl tatarav1alpha1.TaskList
	if err := c.List(ctx, &tl, client.InNamespace(ns), client.MatchingLabels{LabelDedupKey: dedupKey}); err != nil {
		return false, err
	}
	for i := range tl.Items {
		if tl.Items[i].Spec.ProjectRef == projectRef && !tatarav1alpha1.TaskTerminal(&tl.Items[i]) {
			return true, nil
		}
	}
	return false, nil
}

// EnqueueEvent writes a QueuedEvent (seq-assigned, owned by Project, state=Queued).
// Returns created=false when dedupKey already has live work.
func EnqueueEvent(ctx context.Context, c client.Client, alloc *queue.SeqAllocator, proj *tatarav1alpha1.Project,
	class string, autonomous bool, dedupKey string, payload tatarav1alpha1.QueuedEventPayload) (*tatarav1alpha1.QueuedEvent, bool, error) {

	dup, err := dedupExists(ctx, c, proj.Namespace, proj.Name, dedupKey)
	if err != nil {
		return nil, false, err
	}
	if dup {
		return nil, false, nil
	}
	labels := map[string]string{}
	if dedupKey != "" {
		labels[LabelDedupKey] = dedupKey
	}
	qe := &tatarav1alpha1.QueuedEvent{
		ObjectMeta: metav1.ObjectMeta{
			GenerateName: "qe-",
			Namespace:    proj.Namespace,
			Labels:       labels,
		},
		Spec: tatarav1alpha1.QueuedEventSpec{
			Seq:           alloc.Next(),
			Class:         class,
			Kind:          payload.Kind,
			Autonomous:    autonomous,
			ProjectRef:    proj.Name,
			RepositoryRef: payload.RepositoryRef,
			DedupKey:      dedupKey,
			Payload:       payload,
		},
	}
	if err := controllerutil.SetControllerReference(proj, qe, c.Scheme()); err != nil {
		return nil, false, fmt.Errorf("enqueue: set ownerref: %w", err)
	}
	if err := c.Create(ctx, qe); err != nil {
		return nil, false, fmt.Errorf("enqueue: create queuedevent: %w", err)
	}
	qe.Status.State = tatarav1alpha1.QueueStateQueued
	if err := c.Status().Update(ctx, qe); err != nil {
		return nil, false, fmt.Errorf("enqueue: set state: %w", err)
	}
	return qe, true, nil
}

// buildTaskFromQueuedEvent reconstructs the Task the producer described, labelled
// with the QueuedEvent name (dispatcher completion mapping) and dedup key.
func buildTaskFromQueuedEvent(qe *tatarav1alpha1.QueuedEvent, proj *tatarav1alpha1.Project, scheme *runtime.Scheme) (*tatarav1alpha1.Task, error) {
	p := qe.Spec.Payload
	labels := map[string]string{}
	for k, v := range p.Labels {
		labels[k] = v
	}
	labels[LabelQueuedEvent] = qe.Name
	if qe.Spec.DedupKey != "" {
		labels[LabelDedupKey] = qe.Spec.DedupKey
	}
	om := metav1.ObjectMeta{
		Namespace:   qe.Namespace,
		Labels:      labels,
		Annotations: p.Annotations,
	}
	if p.Name != "" {
		om.Name = p.Name
	} else {
		om.GenerateName = p.GenerateName
	}
	task := &tatarav1alpha1.Task{
		ObjectMeta: om,
		Spec: tatarav1alpha1.TaskSpec{
			ProjectRef:    proj.Name,
			RepositoryRef: p.RepositoryRef,
			Goal:          p.Goal,
			Kind:          p.Kind,
			Source:        p.Source,
		},
	}
	agent.StampPodName(task, proj.Name, p.Provider, p.PodRepo)
	if err := controllerutil.SetControllerReference(proj, task, scheme); err != nil {
		return nil, fmt.Errorf("build task: set ownerref: %w", err)
	}
	return task, nil
}
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run 'TestEnqueueEvent|TestBuildTaskFromQueuedEvent' -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/controller/queue_enqueue.go internal/controller/queue_enqueue_test.go internal/controller/export_test.go
git commit -m "feat: enqueue helper, Task blueprint builder, dedup query"
```

---

### Task 5: Dispatcher - inflight counts + Done marking

**Files:**
- Create: `internal/controller/queue_controller.go`
- Create: `internal/controller/queue_controller_test.go`

**Interfaces:**
- Consumes: `QueuedEvent*` (Task 1), `LabelQueuedEvent` (Task 4), `TaskTerminal` (existing).
- Produces:
  - `type DispatcherReconciler struct{ client.Client; Scheme *runtime.Scheme; Alloc *queue.SeqAllocator; Metrics *obs.OperatorMetrics }`
  - `func (r *DispatcherReconciler) poolInflight(qes []tatarav1alpha1.QueuedEvent, tasks []tatarav1alpha1.Task, class string) int`
  - `func (r *DispatcherReconciler) reconcileDone(ctx, qes, tasks) (changed bool, err error)` (marks Admitted whose Task is terminal/missing -> Done)
  - helper `taskByName(tasks []tatarav1alpha1.Task, name string) *tatarav1alpha1.Task`

- [ ] **Step 1: Write the failing test**

```go
// internal/controller/queue_controller_test.go
package controller

import (
	"testing"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func qe(name, class, state, taskRef string) tatarav1alpha1.QueuedEvent {
	return tatarav1alpha1.QueuedEvent{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "tatara"},
		Spec:       tatarav1alpha1.QueuedEventSpec{Class: class, ProjectRef: "p"},
		Status:     tatarav1alpha1.QueuedEventStatus{State: state, TaskRef: taskRef},
	}
}

func tk(name, phase, lifecycle, queuedEvent string) tatarav1alpha1.Task {
	return tatarav1alpha1.Task{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "tatara", Labels: map[string]string{LabelQueuedEvent: queuedEvent}},
		Spec:       tatarav1alpha1.TaskSpec{ProjectRef: "p"},
		Status:     tatarav1alpha1.TaskStatus{Phase: phase, LifecycleState: lifecycle},
	}
}

func TestPoolInflight_CountsAdmittedNonTerminal(t *testing.T) {
	r := &DispatcherReconciler{}
	qes := []tatarav1alpha1.QueuedEvent{
		qe("a", tatarav1alpha1.QueueClassNormal, tatarav1alpha1.QueueStateAdmitted, "t-a"), // running -> counts
		qe("b", tatarav1alpha1.QueueClassNormal, tatarav1alpha1.QueueStateAdmitted, "t-b"), // terminal -> not
		qe("c", tatarav1alpha1.QueueClassAlert, tatarav1alpha1.QueueStateAdmitted, "t-c"),  // alert running
		qe("d", tatarav1alpha1.QueueClassNormal, tatarav1alpha1.QueueStateQueued, ""),      // queued -> not
	}
	tasks := []tatarav1alpha1.Task{
		tk("t-a", "Running", "", "a"),
		tk("t-b", "Succeeded", "", "b"),
		tk("t-c", "Running", "", "c"),
	}
	if got := r.poolInflight(qes, tasks, tatarav1alpha1.QueueClassNormal); got != 1 {
		t.Fatalf("normal inflight = %d, want 1", got)
	}
	if got := r.poolInflight(qes, tasks, tatarav1alpha1.QueueClassAlert); got != 1 {
		t.Fatalf("alert inflight = %d, want 1", got)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run TestPoolInflight -v`
Expected: FAIL (undefined DispatcherReconciler).

- [ ] **Step 3: Write minimal implementation**

```go
// internal/controller/queue_controller.go
package controller

import (
	"context"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/obs"
	"github.com/szymonrychu/tatara-operator/internal/queue"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

type DispatcherReconciler struct {
	client.Client
	Scheme  *runtime.Scheme
	Alloc   *queue.SeqAllocator
	Metrics *obs.OperatorMetrics
}

func taskByName(tasks []tatarav1alpha1.Task, name string) *tatarav1alpha1.Task {
	for i := range tasks {
		if tasks[i].Name == name {
			return &tasks[i]
		}
	}
	return nil
}

// poolInflight counts Admitted QueuedEvents of class whose Task is still
// non-terminal. (Migration of unlabelled pre-queue Tasks is added in Task 7.)
func (r *DispatcherReconciler) poolInflight(qes []tatarav1alpha1.QueuedEvent, tasks []tatarav1alpha1.Task, class string) int {
	n := 0
	for i := range qes {
		q := &qes[i]
		if q.Spec.Class != class || q.Status.State != tatarav1alpha1.QueueStateAdmitted {
			continue
		}
		t := taskByName(tasks, q.Status.TaskRef)
		if t != nil && !tatarav1alpha1.TaskTerminal(t) {
			n++
		}
	}
	return n
}

// reconcileDone flips Admitted events whose Task is terminal or gone to Done.
func (r *DispatcherReconciler) reconcileDone(ctx context.Context, qes []tatarav1alpha1.QueuedEvent, tasks []tatarav1alpha1.Task) (bool, error) {
	changed := false
	for i := range qes {
		q := &qes[i]
		if q.Status.State != tatarav1alpha1.QueueStateAdmitted {
			continue
		}
		t := taskByName(tasks, q.Status.TaskRef)
		if t == nil || tatarav1alpha1.TaskTerminal(t) {
			q.Status.State = tatarav1alpha1.QueueStateDone
			if err := r.Status().Update(ctx, q); err != nil {
				return changed, err
			}
			changed = true
			log.FromContext(ctx).Info("queue: event done", "action", "queue_done", "resource_id", q.Name, "class", q.Spec.Class)
		}
	}
	return changed, nil
}
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run TestPoolInflight -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/controller/queue_controller.go internal/controller/queue_controller_test.go
git commit -m "feat: dispatcher inflight counting + Done marking"
```

---

### Task 6: Dispatcher - admission (alert pool then normal, FIFO, capacity)

**Files:**
- Modify: `internal/controller/queue_controller.go`
- Modify: `internal/controller/queue_controller_test.go`

**Interfaces:**
- Consumes: `poolInflight`/`reconcileDone` (Task 5), `buildTaskFromQueuedEvent` (Task 4), `QueueCapacity`/`AlertCapacity` (Task 2).
- Produces: `func (r *DispatcherReconciler) admit(ctx context.Context, proj *tatarav1alpha1.Project, qes []tatarav1alpha1.QueuedEvent, tasks []tatarav1alpha1.Task) error`.

- [ ] **Step 1: Write the failing test (envtest-backed)**

```go
// internal/controller/queue_controller_test.go (add; uses the envtest k8sClient from suite_test.go)
func TestAdmit_AlertBeforeNormal_AndCapacity(t *testing.T) {
	ctx := context.Background()
	ns := "tatara"
	proj := &tatarav1alpha1.Project{
		ObjectMeta: metav1.ObjectMeta{Name: "p-admit", Namespace: ns},
		Spec:       tatarav1alpha1.ProjectSpec{Queue: &tatarav1alpha1.QueueSpec{Capacity: 1, AlertCapacity: 1}},
	}
	mustCreate(t, ctx, proj) // helper: k8sClient.Create + cleanup

	mkQE := func(seq int64, class string) *tatarav1alpha1.QueuedEvent {
		q := &tatarav1alpha1.QueuedEvent{
			ObjectMeta: metav1.ObjectMeta{GenerateName: "qe-", Namespace: ns},
			Spec: tatarav1alpha1.QueuedEventSpec{
				Seq: seq, Class: class, Kind: "incident", ProjectRef: proj.Name,
				Payload: tatarav1alpha1.QueuedEventPayload{Kind: "incident", GenerateName: "x-"},
			},
		}
		mustCreate(t, ctx, q)
		q.Status.State = tatarav1alpha1.QueueStateQueued
		mustStatusUpdate(t, ctx, q)
		return q
	}
	normalQE := mkQE(1, tatarav1alpha1.QueueClassNormal) // older
	alertQE := mkQE(2, tatarav1alpha1.QueueClassAlert)   // newer but priority pool

	r := &DispatcherReconciler{Client: k8sClient, Scheme: k8sClient.Scheme()}
	qes, tasks := listQEsTasks(t, ctx, proj.Name) // helper
	if err := r.admit(ctx, proj, qes, tasks); err != nil {
		t.Fatal(err)
	}

	// Both pools have capacity 1: one alert + one normal admitted.
	got := refreshQE(t, ctx, alertQE)
	if got.Status.State != tatarav1alpha1.QueueStateAdmitted || got.Status.TaskRef == "" {
		t.Fatalf("alert not admitted: %+v", got.Status)
	}
	gotN := refreshQE(t, ctx, normalQE)
	if gotN.Status.State != tatarav1alpha1.QueueStateAdmitted {
		t.Fatalf("normal not admitted: %+v", gotN.Status)
	}
}
```

Add the small test helpers (`mustCreate`, `mustStatusUpdate`, `listQEsTasks`, `refreshQE`) to `export_test.go` if not present, following the existing envtest helper style in `suite_test.go`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run TestAdmit -v`
Expected: FAIL (undefined admit).

- [ ] **Step 3: Write minimal implementation**

```go
// internal/controller/queue_controller.go (add)
import "sort" // add to the import block

// admit drains the alert pool to AlertCapacity, then the normal pool to
// QueueCapacity, each in strict ascending seq order (pure global FIFO within a
// pool; head-of-line blocking accepted).
func (r *DispatcherReconciler) admit(ctx context.Context, proj *tatarav1alpha1.Project,
	qes []tatarav1alpha1.QueuedEvent, tasks []tatarav1alpha1.Task) error {

	admitPool := func(class string, cap int) error {
		inflight := r.poolInflight(qes, tasks, class)
		queued := make([]*tatarav1alpha1.QueuedEvent, 0)
		for i := range qes {
			if qes[i].Spec.Class == class && qes[i].Status.State == tatarav1alpha1.QueueStateQueued {
				queued = append(queued, &qes[i])
			}
		}
		sort.Slice(queued, func(i, j int) bool { return queued[i].Spec.Seq < queued[j].Spec.Seq })
		for _, q := range queued {
			if inflight >= cap {
				break
			}
			task, err := buildTaskFromQueuedEvent(q, proj, r.Scheme)
			if err != nil {
				return err
			}
			if err := r.Create(ctx, task); err != nil {
				// Leave Queued; requeue. Slot not consumed (inflight derives from Admitted).
				return err
			}
			q.Status.State = tatarav1alpha1.QueueStateAdmitted
			q.Status.TaskRef = task.Name
			now := metav1.Now()
			q.Status.AdmittedAt = &now
			if err := r.Status().Update(ctx, q); err != nil {
				return err
			}
			inflight++
			if r.Metrics != nil {
				r.Metrics.QueueAdmitted(class, q.Spec.Kind)
			}
			log.FromContext(ctx).Info("queue: admitted",
				"action", "queue_admit", "resource_id", q.Name, "task", task.Name,
				"class", class, "seq", q.Spec.Seq, "kind", q.Spec.Kind)
		}
		return nil
	}

	if err := admitPool(tatarav1alpha1.QueueClassAlert, proj.AlertCapacity()); err != nil {
		return err
	}
	return admitPool(tatarav1alpha1.QueueClassNormal, proj.QueueCapacity())
}
```

Add `metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"` to the imports. `r.Metrics.QueueAdmitted` is added in Task 12; guard with the `r.Metrics != nil` check shown so this task compiles and tests pass before metrics exist (the method is referenced only behind the nil-guard — define a no-op stub now in `obs` if the compiler requires the symbol; see note).

Compile note: Go needs the symbol to exist. In this task add a minimal `func (m *OperatorMetrics) QueueAdmitted(class, kind string) {}` stub to `internal/obs/operator_metrics.go` (real counter wired in Task 12).

- [ ] **Step 4: Run tests**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run TestAdmit -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/controller/queue_controller.go internal/controller/queue_controller_test.go internal/controller/export_test.go internal/obs/operator_metrics.go
git commit -m "feat: dispatcher admission (alert pool first, normal pool, seq FIFO, capacity)"
```

---

### Task 7: Dispatcher - migration count for pre-queue Tasks

**Files:**
- Modify: `internal/controller/queue_controller.go` (extend `poolInflight`)
- Modify: `internal/controller/queue_controller_test.go`

**Interfaces:**
- Consumes/Produces: `poolInflight` gains migration counting; signature unchanged.

- [ ] **Step 1: Write the failing test**

```go
// internal/controller/queue_controller_test.go (add)
func TestPoolInflight_CountsUnlabelledPreQueueTasks(t *testing.T) {
	r := &DispatcherReconciler{}
	var qes []tatarav1alpha1.QueuedEvent
	tasks := []tatarav1alpha1.Task{
		preQueueTask("old-normal", "Running", "review", ""),   // no queued-event label -> normal pool
		preQueueTask("old-incident", "Running", "incident", ""), // -> alert pool
		preQueueTask("old-done", "Succeeded", "review", ""),    // terminal -> not counted
	}
	if got := r.poolInflight(qes, tasks, tatarav1alpha1.QueueClassNormal); got != 1 {
		t.Fatalf("normal pre-queue inflight = %d, want 1", got)
	}
	if got := r.poolInflight(qes, tasks, tatarav1alpha1.QueueClassAlert); got != 1 {
		t.Fatalf("alert pre-queue inflight = %d, want 1", got)
	}
}

// preQueueTask: a Task with NO LabelQueuedEvent (created before the queue existed).
func preQueueTask(name, phase, kind, _ string) tatarav1alpha1.Task {
	return tatarav1alpha1.Task{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "tatara"}, // no labels
		Spec:       tatarav1alpha1.TaskSpec{ProjectRef: "p", Kind: kind},
		Status:     tatarav1alpha1.TaskStatus{Phase: phase},
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run TestPoolInflight_CountsUnlabelledPreQueueTasks -v`
Expected: FAIL (count 0, want 1 — migration not counted yet).

- [ ] **Step 3: Write minimal implementation**

```go
// internal/controller/queue_controller.go — replace poolInflight with:
func (r *DispatcherReconciler) poolInflight(qes []tatarav1alpha1.QueuedEvent, tasks []tatarav1alpha1.Task, class string) int {
	n := 0
	for i := range qes {
		q := &qes[i]
		if q.Spec.Class != class || q.Status.State != tatarav1alpha1.QueueStateAdmitted {
			continue
		}
		t := taskByName(tasks, q.Status.TaskRef)
		if t != nil && !tatarav1alpha1.TaskTerminal(t) {
			n++
		}
	}
	// Migration: non-terminal Tasks created before the queue (no queued-event
	// label) count toward their pool so capacity is not over-admitted at cutover.
	for i := range tasks {
		t := &tasks[i]
		if _, queued := t.Labels[LabelQueuedEvent]; queued {
			continue
		}
		if tatarav1alpha1.TaskTerminal(t) {
			continue
		}
		taskClass := tatarav1alpha1.QueueClassNormal
		if t.Spec.Kind == "incident" {
			taskClass = tatarav1alpha1.QueueClassAlert
		}
		if taskClass == class {
			n++
		}
	}
	return n
}
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run TestPoolInflight -v`
Expected: PASS (both pool tests).

- [ ] **Step 5: Commit**

```bash
git add internal/controller/queue_controller.go internal/controller/queue_controller_test.go
git commit -m "feat: dispatcher counts pre-queue Tasks toward capacity at cutover"
```

---

### Task 8: Dispatcher Reconcile + SetupWithManager (Task watch)

**Files:**
- Modify: `internal/controller/queue_controller.go` (add `Reconcile`, `SetupWithManager`)
- Modify: `internal/controller/queue_controller_test.go` (envtest end-to-end)

**Interfaces:**
- Consumes: `reconcileDone` (Task 5), `admit` (Task 6).
- Produces: `func (r *DispatcherReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error)`, `func (r *DispatcherReconciler) SetupWithManager(mgr ctrl.Manager) error`.

- [ ] **Step 1: Write the failing test**

```go
// internal/controller/queue_controller_test.go (add)
func TestDispatcherReconcile_AdmitsThenFreesOnTerminal(t *testing.T) {
	ctx := context.Background()
	ns := "tatara"
	proj := &tatarav1alpha1.Project{
		ObjectMeta: metav1.ObjectMeta{Name: "p-disp", Namespace: ns},
		Spec:       tatarav1alpha1.ProjectSpec{Queue: &tatarav1alpha1.QueueSpec{Capacity: 1, AlertCapacity: 1}},
	}
	mustCreate(t, ctx, proj)

	mk := func(seq int64) *tatarav1alpha1.QueuedEvent {
		q := &tatarav1alpha1.QueuedEvent{
			ObjectMeta: metav1.ObjectMeta{GenerateName: "qe-", Namespace: ns},
			Spec: tatarav1alpha1.QueuedEventSpec{Seq: seq, Class: tatarav1alpha1.QueueClassNormal, Kind: "incident", ProjectRef: proj.Name,
				Payload: tatarav1alpha1.QueuedEventPayload{Kind: "incident", GenerateName: "x-"}},
		}
		mustCreate(t, ctx, q)
		q.Status.State = tatarav1alpha1.QueueStateQueued
		mustStatusUpdate(t, ctx, q)
		return q
	}
	q1 := mk(1)
	q2 := mk(2)

	r := &DispatcherReconciler{Client: k8sClient, Scheme: k8sClient.Scheme()}
	if _, err := r.Reconcile(ctx, reqFor(q1)); err != nil { // helper: ctrl.Request for an object
		t.Fatal(err)
	}
	// capacity 1: q1 admitted, q2 still queued (head-of-line).
	if refreshQE(t, ctx, q1).Status.State != tatarav1alpha1.QueueStateAdmitted {
		t.Fatal("q1 should be admitted")
	}
	if refreshQE(t, ctx, q2).Status.State != tatarav1alpha1.QueueStateQueued {
		t.Fatal("q2 should still be queued (capacity 1)")
	}
	// Drive q1's task terminal, reconcile -> q1 Done, q2 admitted.
	task := taskForQE(t, ctx, refreshQE(t, ctx, q1)) // helper: get Task by LabelQueuedEvent
	task.Status.Phase = "Succeeded"
	mustStatusUpdate(t, ctx, task)
	if _, err := r.Reconcile(ctx, reqFor(q1)); err != nil {
		t.Fatal(err)
	}
	if refreshQE(t, ctx, q1).Status.State != tatarav1alpha1.QueueStateDone {
		t.Fatal("q1 should be Done")
	}
	if refreshQE(t, ctx, q2).Status.State != tatarav1alpha1.QueueStateAdmitted {
		t.Fatal("q2 should now be admitted")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run TestDispatcherReconcile -v`
Expected: FAIL (undefined Reconcile).

- [ ] **Step 3: Write minimal implementation**

```go
// internal/controller/queue_controller.go (add)
import (
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
	"k8s.io/apimachinery/pkg/types"
	"apimachinery/pkg/apierrors" // placeholder: use k8s.io/apimachinery/pkg/api/errors as apierrors
)

func (r *DispatcherReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	var qe tatarav1alpha1.QueuedEvent
	if err := r.Get(ctx, req.NamespacedName, &qe); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}
	var proj tatarav1alpha1.Project
	if err := r.Get(ctx, types.NamespacedName{Namespace: qe.Namespace, Name: qe.Spec.ProjectRef}, &proj); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}
	// Project-wide pass: list this project's QueuedEvents + Tasks.
	var qel tatarav1alpha1.QueuedEventList
	if err := r.List(ctx, &qel, client.InNamespace(qe.Namespace)); err != nil {
		return ctrl.Result{}, err
	}
	var tl tatarav1alpha1.TaskList
	if err := r.List(ctx, &tl, client.InNamespace(qe.Namespace)); err != nil {
		return ctrl.Result{}, err
	}
	qes := filterQEsByProject(qel.Items, proj.Name)
	tasks := filterTasksByProject(tl.Items, proj.Name)

	if _, err := r.reconcileDone(ctx, qes, tasks); err != nil {
		return ctrl.Result{}, err
	}
	// Re-list after Done mutations so admission sees fresh state.
	if err := r.List(ctx, &qel, client.InNamespace(qe.Namespace)); err != nil {
		return ctrl.Result{}, err
	}
	qes = filterQEsByProject(qel.Items, proj.Name)
	if err := r.admit(ctx, &proj, qes, tasks); err != nil {
		return ctrl.Result{}, err
	}
	return ctrl.Result{}, nil
}

func filterQEsByProject(in []tatarav1alpha1.QueuedEvent, project string) []tatarav1alpha1.QueuedEvent {
	out := in[:0:0]
	for i := range in {
		if in[i].Spec.ProjectRef == project {
			out = append(out, in[i])
		}
	}
	return out
}

func filterTasksByProject(in []tatarav1alpha1.Task, project string) []tatarav1alpha1.Task {
	out := in[:0:0]
	for i := range in {
		if in[i].Spec.ProjectRef == project {
			out = append(out, in[i])
		}
	}
	return out
}

func (r *DispatcherReconciler) SetupWithManager(mgr ctrl.Manager) error {
	mapTaskToQE := func(ctx context.Context, obj client.Object) []reconcile.Request {
		qeName := obj.GetLabels()[LabelQueuedEvent]
		if qeName == "" {
			return nil
		}
		return []reconcile.Request{{NamespacedName: types.NamespacedName{Namespace: obj.GetNamespace(), Name: qeName}}}
	}
	return ctrl.NewControllerManagedBy(mgr).
		For(&tatarav1alpha1.QueuedEvent{}).
		Watches(&tatarav1alpha1.Task{}, handler.EnqueueRequestsFromMapFunc(mapTaskToQE)).
		Complete(r)
}
```

Fix the import placeholder: use `apierrors "k8s.io/apimachinery/pkg/api/errors"` only if referenced; the code above uses `client.IgnoreNotFound`, so drop the `apierrors` import. Keep imports the compiler needs.

- [ ] **Step 4: Run tests**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run TestDispatcherReconcile -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/controller/queue_controller.go internal/controller/queue_controller_test.go internal/controller/export_test.go
git commit -m "feat: dispatcher Reconcile (project pass) + Task-watch wiring"
```

---

### Task 9: Webhook producers enqueue instead of create

**Files:**
- Modify: `internal/webhook/server.go` (`Config` add `Seq *queue.SeqAllocator`; `handleWorkItem`, `createLifecycleTaskAtTriage`, `createIncidentTask`, `reactivateTask` enqueue; delete `incidentDedup` (folded into enqueue dedup); `createIncidentTask` builds a Payload)
- Modify: `internal/webhook/server_test.go` (or the relevant existing webhook test files)

**Interfaces:**
- Consumes: `EnqueueEvent`, `LabelDedupKey` (Task 4); `controller` package (import the controller package for `EnqueueEvent`, or relocate `EnqueueEvent` to a neutral package both can import — see note).
- Produces: webhook handlers create QueuedEvents.

Note on package boundaries: `EnqueueEvent`/`buildTaskFromQueuedEvent` are in `internal/controller` but the webhook is `internal/webhook`. To avoid an import cycle, move `EnqueueEvent`, `buildTaskFromQueuedEvent`, `dedupExists`, and the `LabelQueuedEvent`/`LabelDedupKey` consts into a new neutral package `internal/queue` (alongside the allocator) in this task, updating Task 4/5/6/8 references from `EnqueueEvent` to `queue.EnqueueEvent`. Do this relocation as Step 1 here (mechanical move + import fixups), then run the full controller suite to confirm green before changing the webhook.

- [ ] **Step 1: Relocate enqueue helpers to internal/queue, fix imports, verify green**

Move the four symbols + two consts from `internal/controller/queue_enqueue.go` into `internal/queue/enqueue.go` (package `queue`). Update `internal/controller` callsites (dispatcher) to `queue.EnqueueEvent`, `queue.LabelQueuedEvent`, etc. `buildTaskFromQueuedEvent` takes `*runtime.Scheme` already, so it has no controller dependency.

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go build ./... && mise exec -- go test ./internal/controller/ ./internal/queue/ -v`
Expected: PASS (pure move, no behavior change).

- [ ] **Step 2: Write the failing webhook test**

```go
// internal/webhook/server_test.go (add) - assert enqueue, not Task create.
func TestHandleGrafanaAlert_EnqueuesAlertEvent(t *testing.T) {
	// Build a Server whose cfg.Client is a fake with a Grafana-enabled Project,
	// cfg.Seq = queue.NewSeqAllocator(). POST a firing alert.
	// Assert: exactly one QueuedEvent, Class=alert, Kind=incident, DedupKey=groupHash,
	// and NO Task created directly.
	// (Mirror the existing grafana handler test setup in grafana_handler_test.go.)
}
```

Fill the body following the existing `grafana_handler_test.go` setup (it already builds a Server + fake client + firing payload). Assert `QueuedEventList` length 1 with the fields above and `TaskList` length 0.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/webhook/ -run TestHandleGrafanaAlert_EnqueuesAlertEvent -v`
Expected: FAIL (still creates a Task / no QueuedEvent).

- [ ] **Step 4: Rewrite the webhook producers**

Add to `Config`: `Seq *queue.SeqAllocator`. Replace `createIncidentTask` body:

```go
func (s *Server) createIncidentTask(ctx context.Context, proj *tatarav1.Project, alert GrafanaAlert, groupHash string) error {
	slugs := projectRepoSlugs(ctx, s.cfg.Client, s.cfg.Namespace, proj.Name)
	alertCtx := renderAlertContext(alert)
	goal := incident.GoalProject(alertCtx, slugs)
	payload := tatarav1.QueuedEventPayload{
		Kind:         "incident",
		Goal:         goal,
		GenerateName: "incident-",
		Labels:       map[string]string{tatarav1.LabelActivity: "incident", tatarav1.LabelAlertGroup: groupHash},
		Annotations:  map[string]string{tatarav1.AnnGrafanaAlert: alertCtx},
		Provider:     "",
		PodRepo:      "",
	}
	_, _, err := queue.EnqueueEvent(ctx, s.cfg.Client, s.cfg.Seq, proj, tatarav1.QueueClassAlert, false, groupHash, payload)
	return err
}
```

Delete `incidentDedup` and its callsite in `handleGrafanaAlert` (the `EnqueueEvent` dedup on `dedupKey=groupHash` now covers the in-flight case). The cooldown window is intentionally dropped to keep one dedup mechanism; if a cooldown is still wanted, record the decision in MEMORY and add a `Done`-within-window check to `dedupExists` — but default for this plan: in-flight dedup only (a resolved+refiring group reopens once the prior incident is Done). Note this behavior change in the commit body.

For `handleWorkItem` (the issue/MR -> issueLifecycle path, lines ~404-429): replace the `Task{...}` + `r.Create` with a Payload carrying the same `Labels`, `Annotations`, `RepositoryRef`, `Goal`, `Kind`, `Source`, and a fixed `Name: issueLifecycleTaskName(...)` (deterministic), then `queue.EnqueueEvent(..., class=normal, autonomous=false, dedupKey=issueLifecycleTaskName(...), payload)`. The deterministic Name + dedupKey preserve today's idempotency. Set `Payload.Provider` from the resolved provider and `Payload.PodRepo` from the repo name (matching the old `agent.StampPodName` args at the create site).

For `createLifecycleTaskAtTriage` (lines ~559-622) and `reactivateTask`: route through `queue.EnqueueEvent` the same way (normal class, dedupKey = the deterministic lifecycle name). Reactivation that targets an existing Parked Task stays as-is if it mutates an existing Task rather than creating one — only the *create* arms move to enqueue.

- [ ] **Step 5: Run tests**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/webhook/ -v`
Expected: PASS (the new enqueue test + existing webhook tests adjusted to assert QueuedEvent where they previously asserted Task).

- [ ] **Step 6: Commit**

```bash
git add internal/webhook/ internal/queue/ internal/controller/
git commit -m "feat: webhook producers enqueue QueuedEvents (incident=alert, issueLifecycle=normal); drop incidentDedup cooldown"
```

---

### Task 10: Crons enqueue + delete the selection layer

**Files:**
- Modify: `internal/controller/projectscan.go` (`createScanTask`/`createBrainstormTask`/`createHealthCheckTask` enqueue; `runScans` drops the budget; `mrScan`/`issueScan`/`brainstorm`/`healthCheck` enqueue under the `K` cap; delete `selectCandidates`, `selectPerRepo`, `laneOccupancy`, `maxOpenTasks`, `openTaskCount`)
- Modify: the affected `projectscan_*_test.go` files
- Modify: `internal/controller/queue_controller.go` add `func queuedAutonomousCount(qes []tatarav1alpha1.QueuedEvent) int`

**Interfaces:**
- Consumes: `queue.EnqueueEvent` (Task 9), `QueuedAutonomousCap` (Task 2), `ProjectReconciler` gains `Alloc *queue.SeqAllocator`.
- Produces: crons emit QueuedEvents; selection layer removed.

- [ ] **Step 1: Write the failing test**

```go
// internal/controller/projectscan_run_test.go (add or adapt)
func TestRunScans_EnqueuesUnderAutonomousCap(t *testing.T) {
	// Seed a Project with Queue.QueuedAutonomousCap=2 and >2 eligible issues across repos.
	// Stub the SCM reader to return several open issues.
	// Call r.runScans(ctx, proj).
	// Assert: exactly 2 QueuedEvents created with Autonomous=true; NO Tasks created
	// directly by the scan (Tasks appear only after the dispatcher admits).
}
```

Fill using the existing `projectscan_run_test.go` reader-stub + seeding helpers. Assert `QueuedEventList` count == 2 and all `Spec.Autonomous == true`, `TaskList` count == 0.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run TestRunScans_EnqueuesUnderAutonomousCap -v`
Expected: FAIL (still creates Tasks / no cap on QueuedEvents).

- [ ] **Step 3: Implement**

Add `Alloc *queue.SeqAllocator` to `ProjectReconciler` (struct + wire in Task 12). Add:

```go
// internal/controller/queue_controller.go
func queuedAutonomousCount(qes []tatarav1alpha1.QueuedEvent) int {
	n := 0
	for i := range qes {
		if qes[i].Spec.Autonomous && qes[i].Status.State == tatarav1alpha1.QueueStateQueued {
			n++
		}
	}
	return n
}
```

Rewrite the three create-helpers to enqueue. Example for `createScanTask` (replace the `Task{...}`+ownerref+`r.Create`+`StampPodName` block, lines ~416-441) with a Payload build + enqueue. The dedupKey for a scan candidate is `kind + "\x00" + src.IssueRef`:

```go
payload := tatarav1alpha1.QueuedEventPayload{
	Kind:         kind,
	RepositoryRef: repo.Name,
	Goal:         goal,
	Source:       src,
	Labels:       scanTaskLabels(labelCand, activity, kind),
	GenerateName: "scan-",
	Provider:     provider,
	PodRepo:      repo.Name,
}
dedupKey := kind + "\x00" + src.IssueRef
_, created, err := queue.EnqueueEvent(ctx, r.Client, r.Alloc, proj, tatarav1alpha1.QueueClassNormal, true, dedupKey, payload)
if err != nil {
	return nil, fmt.Errorf("scan: enqueue: %w", err)
}
if created {
	r.Metrics.ScanTaskCreated(activity, kind) // keep the existing metric name; it now counts enqueues
}
return nil, nil // callers no longer use the returned *Task
```

Adjust `createScanTask`'s signature/return if callers consumed the `*Task` (they used it only for logging/budget — remove that usage). Apply the same transform to `createBrainstormTask` (class=normal, autonomous=true, dedupKey="brainstorm-"+proj.Name+cycle marker; Labels `{labelActivity:"brainstorm"}`, Annotations `{AnnBrainstormSources:...}`, GenerateName "brainstorm-", Kind "brainstorm") and `createHealthCheckTask` (same, activity "healthCheck", GenerateName "healthcheck-").

In `runScans` (lines ~1532-1636): delete `budget := maxOpenTasks(proj) - openTaskCount(existing)` and the `&budget` threading. Replace with a `K`-gate the scans honor:

```go
var qel tatarav1alpha1.QueuedEventList
if err := r.List(ctx, &qel, client.InNamespace(proj.Namespace)); err != nil {
	return 0, err
}
qes := filterQEsByProject(qel.Items, proj.Name)
remaining := proj.QueuedAutonomousCap() - queuedAutonomousCount(qes)
```

Thread `&remaining` where `&budget` was threaded; each scan enqueues while `remaining > 0`, decrementing on `created`. The webhook/alert paths never consult `remaining`.

In `mrScan`/`issueScan`: replace `selectPerRepo(eligible, priorityLabel, maxPerRepo, occ)` with a flat eligible list (all candidates), enqueue each via `createScanTask` while `*remaining > 0` and the candidate is not already deduped (the enqueue dedup handles the per-work-item skip; the old `laneOccupancy` per-repo cap is gone). Delete `selectCandidates`, `selectPerRepo`, `laneOccupancy`, `maxOpenTasks`, `openTaskCount`, `taskOpen`, the `candidate.updatedAt` sort usage, and `priorityLabel` plumbing. (Keep `candidate` struct + `candidatesFromPRs`/`candidatesFromIssues` — still used to shape eligible items.)

- [ ] **Step 4: Run tests**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -v`
Expected: PASS. Delete or rewrite the now-obsolete `projectscan_select_test.go`, `projectscan_backlog_test.go` (budget), and lane tests; assert the new enqueue behavior instead. Ensure no references to deleted functions remain (`mise exec -- go build ./...`).

- [ ] **Step 5: Commit**

```bash
git add internal/controller/
git commit -m "refactor: crons enqueue under autonomous cap; delete per-repo selection + maxOpenTasks budget"
```

---

### Task 11: Delete atConcurrencyCap (execution gate superseded by admission)

**Files:**
- Modify: `internal/controller/task_controller.go` (delete `atConcurrencyCap` func + callsite ~176; delete `taskActive` if it has no other callers, else keep)
- Modify: `internal/controller/lifecycle.go` (delete callsite ~533)
- Modify: affected tests (`task_controller_test.go`, any concurrency-cap test)

**Interfaces:**
- Removes: `atConcurrencyCap`. No replacement (admission is the only gate now).

- [ ] **Step 1: Write/adjust the failing test**

```go
// internal/controller/task_controller_test.go (add)
func TestReconcile_NoConcurrencyGate(t *testing.T) {
	// Seed a Project with MaxConcurrentTasks=1 and 2 active (Running) Tasks both
	// carrying the LabelQueuedEvent (admitted). Reconcile the second Task.
	// Assert it is NOT requeued/blocked by a concurrency cap (it proceeds to its
	// normal phase handling). Previously atConcurrencyCap would have gated it.
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/controller/ -run TestReconcile_NoConcurrencyGate -v`
Expected: FAIL (cap still gates).

- [ ] **Step 3: Implement (delete the gate)**

- Remove `func (r *TaskReconciler) atConcurrencyCap(...)` (task_controller.go:329).
- Remove its callsite in `Reconcile` (task_controller.go:176) and in `reconcileLifecycle` (lifecycle.go:533), including the now-dead `self`/cap branches and the `tasksInflight`-from-cap bookkeeping if it lived there.
- If `taskActive` (task_controller.go:101) and `isActive` lose all callers, delete them too (boy-scout). Keep any still referenced by the inflight gauge.
- Delete the old concurrency-cap test(s) (`TestTaskActive_ExcludesTerminalLifecycle`, the cap-deadlock regression test) or repurpose them to assert the gate is gone.

- [ ] **Step 4: Run tests + build**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go build ./... && mise exec -- go test ./internal/controller/ -v`
Expected: PASS; no references to `atConcurrencyCap`.

- [ ] **Step 5: Commit**

```bash
git add internal/controller/
git commit -m "refactor: remove atConcurrencyCap; admission queue is the only concurrency gate"
```

---

### Task 12: Wire manager + metrics + manifests + full suite

**Files:**
- Modify: `cmd/manager/wire.go` (construct `SeqAllocator`; register `SeqRecoverer` via `mgr.Add`; construct + `SetupWithManager` the `DispatcherReconciler`; inject `Alloc` into `ProjectReconciler`; set webhook `Config.Seq`)
- Modify: `internal/obs/operator_metrics.go` (real queue metrics)
- Modify: CRD manifests via `make manifests` (`config/crd/...` and `charts/tatara-operator/crds/...`)

**Interfaces:**
- Consumes: everything above.
- Produces: `func (m *OperatorMetrics) QueueAdmitted(class, kind string)`, `func (m *OperatorMetrics) SetQueueDepth(class string, n int)`, `func (m *OperatorMetrics) SetQueueInflight(class string, n int)`.

- [ ] **Step 1: Write the failing test**

```go
// internal/obs/operator_metrics_test.go (add)
func TestQueueMetrics_RegisterAndObserve(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := NewOperatorMetrics(reg)
	m.QueueAdmitted("alert", "incident")
	m.SetQueueDepth("normal", 3)
	m.SetQueueInflight("alert", 1)
	// gather + assert the three series exist with expected values
	mfs, err := reg.Gather()
	if err != nil {
		t.Fatal(err)
	}
	if !hasMetric(mfs, "operator_queue_admitted_total") ||
		!hasMetric(mfs, "operator_queue_depth") ||
		!hasMetric(mfs, "operator_queue_inflight") {
		t.Fatal("queue metrics not registered")
	}
}
```

`hasMetric` is a tiny helper over `[]*dto.MetricFamily` (add if absent).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/szymonri/Documents/tatara/tatara-operator && mise exec -- go test ./internal/obs/ -run TestQueueMetrics -v`
Expected: FAIL (methods undefined / not registered).

- [ ] **Step 3: Implement metrics + wiring**

In `internal/obs/operator_metrics.go` replace the Task-6 no-op `QueueAdmitted` stub with real instruments registered in `NewOperatorMetrics`:

```go
// fields on OperatorMetrics
queueAdmittedTotal *prometheus.CounterVec // labels: class, kind
queueDepth         *prometheus.GaugeVec   // label: class
queueInflight      *prometheus.GaugeVec   // label: class

// in NewOperatorMetrics, construct with prometheus.NewCounterVec/NewGaugeVec and reg.MustRegister(...)
func (m *OperatorMetrics) QueueAdmitted(class, kind string) { m.queueAdmittedTotal.WithLabelValues(class, kind).Inc() }
func (m *OperatorMetrics) SetQueueDepth(class string, n int) { m.queueDepth.WithLabelValues(class).Set(float64(n)) }
func (m *OperatorMetrics) SetQueueInflight(class string, n int) { m.queueInflight.WithLabelValues(class).Set(float64(n)) }
```

In the dispatcher `Reconcile`, after the admission pass, set the gauges from the current snapshot (depth = Queued per class, inflight = `poolInflight` per class) so they recompute from cluster state (no deltas), mirroring the lifecycle-gauge recompute precedent.

In `cmd/manager/wire.go`:

```go
seqAlloc := queue.NewSeqAllocator()
if err := mgr.Add(&queue.SeqRecoverer{Client: mgr.GetClient(), Alloc: seqAlloc, Namespace: cfg.Namespace}); err != nil {
	return err
}
// ProjectReconciler: add Alloc: seqAlloc to the struct literal (cmd/manager/wire.go:151-169)
// webhook Config: set Seq: seqAlloc (where the webhook.Config is built, ~cmd/manager/wire.go:88)
if err := (&controller.DispatcherReconciler{
	Client: mgr.GetClient(), Scheme: mgr.GetScheme(), Alloc: seqAlloc, Metrics: metrics,
}).SetupWithManager(mgr); err != nil {
	return err
}
```

- [ ] **Step 4: Regenerate manifests + full suite**

Run:
```bash
cd /Users/szymonri/Documents/tatara/tatara-operator
mise exec -- make manifests generate
mise exec -- go build ./...
mise exec -- golangci-lint run
mise exec -- go test ./...
```
Expected: CRDs for `QueuedEvent` + the `queue` field on Project appear under `config/crd/bases/` and `charts/tatara-operator/crds/`; build clean; lint clean; ALL tests PASS.

- [ ] **Step 5: Commit**

```bash
git add cmd/manager/wire.go internal/obs/ config/crd/ charts/tatara-operator/crds/ api/v1alpha1/
git commit -m "feat: wire seq allocator + dispatcher + queue metrics; regenerate CRDs"
```

---

## Self-Review

**Spec coverage:**
- Unit A (QueuedEvent CRD) -> Task 1. Unit B (seq) -> Task 3. Unit C (producers) -> Tasks 9, 10. Unit D (dispatcher) -> Tasks 5-8. Unit E (migration) -> Task 7. Unit F (QueueSpec config + supersession) -> Tasks 2, 10, 11.
- Strict global FIFO + alert-pool-first -> Task 6. Reserved alert capacity -> Tasks 2, 6. HOL blocking accepted -> demonstrated in Task 8 test. Queued-depth cap K -> Task 10. Dedup/cooldown move to enqueue -> Tasks 4, 9 (cooldown dropped to in-flight-dedup; flagged for MEMORY). Deletions (atConcurrencyCap/maxOpenTasks/lane selection) -> Tasks 10, 11. Metrics -> Task 12. CRD-apply runbook -> Task 12 + Deploy section.

**Placeholder scan:** One deliberate plan-time decision remains (incident cooldown vs in-flight-only dedup) - resolved to in-flight-only with a MEMORY note; not a placeholder. Test bodies for Tasks 9 and 10 reference "fill using existing helper setup" - acceptable because they reuse concrete existing test scaffolding (`grafana_handler_test.go`, `projectscan_run_test.go`); the assertions are fully specified.

**Type consistency:** `EnqueueEvent`/`buildTaskFromQueuedEvent`/`dedupExists` relocate to package `queue` in Task 9; all later references use `queue.` prefix. `LabelQueuedEvent`/`LabelDedupKey` move with them. `QueueClass*`/`QueueState*` consts consistent across tasks. `poolInflight` signature stable across Tasks 5/7. `r.Metrics.QueueAdmitted` stubbed in Task 6, made real in Task 12 (compiles throughout).

## Deploy (post-implementation)

1. tatara-operator: merge to `main` -> CI builds operator image. `kubectl apply` the regenerated CRDs (`QueuedEvent` + Project `queue` field) - Helm skips CRD upgrades.
2. tatara-helmfile: bump operator chart version + pinned `image.tag`; optionally set per-project `spec.queue {capacity, alertCapacity, queuedAutonomousCap}` (defaults match today's throughput). Diff -> apply.
3. Cutover: pre-queue in-flight Tasks drain under the Task 7 migration rule; new work flows through the queue. Enabling the (already-inert) Grafana incident webhook lights up the reserved alert lane.
