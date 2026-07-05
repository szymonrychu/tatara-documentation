# tatara-operator N3 (per-Project memory endpoint wiring + Ready-gating; remove MEMORY_BASE_URL) Implementation Plan

> For agentic workers: execute tasks top to bottom. Each task is a complete
> TDD cycle: write the failing test exactly as given, run it and confirm the
> expected FAIL, write the minimal implementation exactly as given, run it and
> confirm PASS, then commit. Do not skip the run steps. Do not "improve"
> beyond the given code. Copy code verbatim; paths and commands are exact.
> Run every command from the repo root
> `/Users/szymonri/Documents/tatara/tatara-operator` unless stated otherwise.
> Work in a worktree off `main` per hard rule 10; never build or deploy from
> the worktree.

## Goal

Make ingest Jobs and agent wrapper pods target the OWNING Project's
per-Project memory endpoint (`Project.status.memory.endpoint`) instead of the
removed global `MEMORY_BASE_URL`, and gate both on the Project's memory stack
being Ready.

Concretely:

1. `RepositoryReconciler.Reconcile`: after resolving the owning Project, if
   `project.Status.Memory == nil || project.Status.Memory.Phase != "Ready"`,
   set a Repository condition `MemoryNotReady=True` and requeue (~15s) WITHOUT
   launching the ingest Job. When Ready, pass
   `project.Status.Memory.Endpoint` to the Job builder as the base URL.
2. `internal/ingest/job.go`: `BuildJob` takes the base URL as an explicit
   parameter (sourced from the Project endpoint). The `Config.MemoryBaseURL`
   field is removed.
3. `TaskReconciler.Reconcile`: gate the Task run on the owning Project's memory
   Ready (requeue ~15s otherwise); the wrapper pod's memory base-URL env
   (`TATARA_MEMORY_URL`, consumed by the agent's tatara-cli memory MCP) =
   `project.Status.Memory.Endpoint`.
4. `internal/agent/pod.go`: `BuildPod` takes the memory endpoint and emits
   `TATARA_MEMORY_URL`.
5. `internal/config/config.go` + `cmd/manager/wire.go`: remove every remaining
   `MemoryBaseURL` read and wiring. Nothing references it after N1 removed the
   notion of a global memory URL.

## Architecture

N1 removed the operator-config `MemoryBaseURL` field conceptually and N2 added
`Project.status.memory.{phase,endpoint}` (type `MemoryStatus`, pointer
`*MemoryStatus` at `Project.Status.Memory`). This milestone is the wiring
ripple: every consumer of the old single global memory URL is repointed to the
per-Project `status.memory.endpoint`, gated on `phase == "Ready"`.

Two consumers exist today:

- The ingest Job (`internal/ingest/job.go`): builds the `tatara-ingest`
  command's `--base-url` and the container's `BASE_URL` env from
  `cfg.MemoryBaseURL`. After N3 these come from a `baseURL string` parameter
  the `RepositoryReconciler` supplies from `project.Status.Memory.Endpoint`.
- The wrapper pod (`internal/agent/pod.go`): the agent inside the wrapper runs
  `tatara mcp` (the tatara-cli memory MCP stdio server) which resolves its
  backend via `TATARA_MEMORY_URL` (see
  `tatara-cli/internal/cmd/mcp.go`:`client.ResolveBaseURL(..., os.Getenv("TATARA_MEMORY_URL"), ...)`).
  The wrapper forwards `os.Environ()` to the claude subprocess, which inherits
  it to the MCP server, so setting `TATARA_MEMORY_URL` on the pod is the
  injection point. Today the pod sets NO memory env at all (the agent had no
  memory backend wired); N3 ADDS `TATARA_MEMORY_URL`.

Ready-gating contract (implemented verbatim):

```
resolve owning Project (spec.projectRef / spec.repositoryRef -> project).
if project.Status.Memory == nil || project.Status.Memory.Phase != "Ready":
    Repository: set condition MemoryNotReady=True (reason MemoryProvisioning),
                requeue after 15s, DO NOT create the ingest Job.
    Task:       requeue after 15s, DO NOT spawn the wrapper pod/service.
else:
    ingest Job base URL = project.Status.Memory.Endpoint
    wrapper pod TATARA_MEMORY_URL = project.Status.Memory.Endpoint
```

Auth is unchanged: every per-Project memory service uses OIDC audience
`tatara-memory`, so the ingester's client-credentials token and the agent's
tatara-cli token still validate against any per-Project endpoint.

The gate ordering in `RepositoryReconciler.Reconcile`: the Project is already
fetched at the existing `ingestDecision`/Job-launch site (line ~91-95). The
gate is inserted immediately AFTER that Get and BEFORE
`ensureResultConfigMap`/`BuildJob`, so the concurrency guard (an already-active
Job) still short-circuits first and a Ready->NotReady flip mid-ingest does not
kill an in-flight Job.

The gate ordering in `TaskReconciler.Reconcile`: the Project is already fetched
(line ~93-97). The memory gate is inserted immediately AFTER that Get, BEFORE
the concurrency cap check, so a Task whose memory is not Ready never counts
against or spawns anything. Terminal tasks (handled earlier at line ~77) are
unaffected.

## Tech Stack

- Go `1.26.x` (pinned in `go.mod`), kubebuilder / controller-runtime.
- `sigs.k8s.io/controller-runtime` reconcilers; `pkg/envtest` for reconciler
  tests (single control plane booted by `internal/controller/suite_test.go`).
- `k8s.io/api/batch/v1`, `k8s.io/api/core/v1`, `k8s.io/apimachinery`.
- `apimachinery/pkg/api/meta` (`meta.SetStatusCondition`) +
  `metav1.Condition` for `status.conditions`.
- Tests: stdlib `testing`, table-driven with `t.Run` where useful, errors
  wrapped `%w`. The controller-package envtest helpers (`mkProject`,
  `mkRepo`, `mkTask*`, `getRepo`, `getTask`, `reconcileRepo`, `reconcileTask`,
  `findCond`, `listIngestJobs`, `contains`) already exist and are reused.

Assumptions stated up front (verify before Task 1; if false, STOP and reconcile
with the N1/N2 result):

- N1/N2 have added to `api/v1alpha1` (Project): `type MemoryStatus struct {
  Phase string; Endpoint string }` and `Status.Memory *MemoryStatus`, with
  regenerated deepcopy and the CRD `status.memory` schema present in
  `charts/tatara-operator/crds/` (so envtest can persist it).
- N1 removed the operator-config notion of a single global memory URL at the
  design level. In the deployed tree the field `Config.MemoryBaseURL` and its
  `MEMORY_BASE_URL` read STILL EXIST (confirmed present). N3 owns their
  deletion (Task 5) - this is intentional, not a duplicate of N1.
- `memory.Endpoint(project, namespace)` exists in `internal/memory` (N1) and
  returns `http://mem-<proj>.<ns>.svc:8080`. N3 does NOT call it directly; it
  reads the already-computed `project.Status.Memory.Endpoint`.

Run the verification grep first:

```
cd /Users/szymonri/Documents/tatara/tatara-operator && \
  grep -rn "MemoryStatus\|Status.Memory" api/v1alpha1/ && \
  grep -rn "MemoryBaseURL\|MEMORY_BASE_URL" internal/ cmd/
```

Expect: `MemoryStatus` + `Status.Memory` present in `api/v1alpha1`;
`MemoryBaseURL`/`MEMORY_BASE_URL` still present in `internal/config`,
`internal/ingest`, `internal/controller/repository_controller_test.go`,
`cmd/manager`. If `MemoryStatus` is absent, N2 is not merged - STOP.

---

## Task 1: ingest Job builder takes base URL as a parameter

### Files
- MODIFY `internal/ingest/job.go`
- MODIFY `internal/ingest/job_test.go`

### Steps

- [ ] Replace the `MemoryBaseURL` config field usage with a `baseURL` parameter.
  Edit `internal/ingest/job.go`:

  Remove the `MemoryBaseURL` field from `Config`:

  ```go
  // Config is the subset of operator configuration the Job builder needs.
  type Config struct {
  	IngesterImage    string
  	OIDCIssuer       string
  	OIDCClientID     string
  	OIDCClientSecret string
  	OIDCAudience     string
  	Namespace        string
  }
  ```

  Change the `BuildJob` signature to take `baseURL string` (insert it right
  after `since string`) and use it in both the `--base-url` arg and the
  `BASE_URL` env. The new signature:

  ```go
  func BuildJob(project *tataradevv1alpha1.Project, repo *tataradevv1alpha1.Repository, since, baseURL string, cfg Config) *batchv1.Job {
  ```

  In the `ingestArgs` fmt.Sprintf, replace `cfg.MemoryBaseURL` with `baseURL`:

  ```go
  	ingestArgs := fmt.Sprintf(
  		"tatara-ingest --repo-root %s --repo-name %s --base-url %s",
  		repoDir, repo.Name, baseURL)
  ```

  In the main container env, replace the `BASE_URL` value:

  ```go
  						{Name: "BASE_URL", Value: baseURL},
  ```

  Also update the package doc comment's first sentence if it names the global
  URL (it does not; leave the `// Package ingest ...` comment as-is).

- [ ] Update the unit test to pass the base URL explicitly. Edit
  `internal/ingest/job_test.go`:

  Remove the `MemoryBaseURL` line from `testConfig()`:

  ```go
  func testConfig() Config {
  	return Config{
  		IngesterImage:    "registry.example/ingester:1.2.3",
  		OIDCIssuer:       "https://kc.example/realms/tatara",
  		OIDCClientID:     "tatara-operator",
  		OIDCClientSecret: "s3cr3t",
  		OIDCAudience:     "tatara-memory",
  		Namespace:        "tatara",
  	}
  }
  ```

  Add a constant near the top of the test file (after the imports) and use it
  everywhere a base URL is needed:

  ```go
  const testBaseURL = "http://mem-acme.tatara.svc:8080"
  ```

  Update every `BuildJob(...)` call to pass `testBaseURL` as the new fourth
  argument:

  - In `TestBuildJob_FullIngest`:
    `job := BuildJob(testProject(), testRepository(), "", testBaseURL, testConfig())`
  - In `TestBuildJob_IncrementalIngest`:
    `job := BuildJob(testProject(), testRepository(), "abc1234", testBaseURL, testConfig())`
  - In `TestBuildJob_SCMTokenFromSecret`:
    `job := BuildJob(testProject(), testRepository(), "", testBaseURL, testConfig())`
  - In `TestBuildJob_SharedWorkspaceVolume`:
    `job := BuildJob(testProject(), testRepository(), "", testBaseURL, testConfig())`

  Update the two assertions in `TestBuildJob_FullIngest` that hard-code the old
  URL:

  ```go
  	if !strings.Contains(cmd, "tatara-ingest --repo-root /workspace/repo --repo-name widgets --base-url http://mem-acme.tatara.svc:8080") {
  		t.Errorf("ingest cmd wrong: %q", cmd)
  	}
  ```

  ```go
  	if v := envValue(main, "BASE_URL"); v != "http://mem-acme.tatara.svc:8080" {
  		t.Errorf("BASE_URL = %q", v)
  	}
  ```

  Add a dedicated test asserting the parameter is honored (append to the file):

  ```go
  func TestBuildJob_BaseURLFromParameter(t *testing.T) {
  	const ep = "http://mem-other.tatara.svc:8080"
  	job := BuildJob(testProject(), testRepository(), "", ep, testConfig())
  	main := job.Spec.Template.Spec.Containers[0]
  	cmd := strings.Join(main.Command, " ") + " " + strings.Join(main.Args, " ")
  	if !strings.Contains(cmd, "--base-url "+ep) {
  		t.Errorf("ingest cmd must carry parameter base-url %q: %q", ep, cmd)
  	}
  	if v := envValue(main, "BASE_URL"); v != ep {
  		t.Errorf("BASE_URL = %q, want %q", v, ep)
  	}
  }
  ```

- [ ] Run the test, expect FAIL first (write the test before the impl edit if
  you prefer strict TDD; either way confirm both states):

  ```
  go test ./internal/ingest/...
  ```

  Expected after impl: `ok  github.com/szymonrychu/tatara-operator/internal/ingest`.
  (Before the impl edit: compile error `too many arguments in call to BuildJob`
  / `unknown field MemoryBaseURL` - that is the expected FAIL.)

- [ ] Commit:

  ```
  git add internal/ingest/job.go internal/ingest/job_test.go
  git commit -m "refactor: ingest BuildJob takes base URL as a parameter"
  ```

---

## Task 2: RepositoryReconciler gates on Project memory Ready and passes the endpoint

### Files
- MODIFY `internal/controller/repository_controller.go`
- MODIFY `internal/controller/repository_controller_test.go`

### Steps

- [ ] Write the failing envtest first. Edit
  `internal/controller/repository_controller_test.go`.

  First, the existing helper `newRepoReconciler()` still constructs an
  `ingest.Config` with `MemoryBaseURL` - remove that field (it no longer
  exists after Task 1):

  ```go
  func newRepoReconciler() *RepositoryReconciler {
  	return &RepositoryReconciler{
  		Client:  k8sClient,
  		Scheme:  k8sClient.Scheme(),
  		Metrics: obs.NewOperatorMetrics(prometheus.NewRegistry()),
  		IngestConfig: ingest.Config{
  			IngesterImage: "registry.example/ingester:1.2.3",
  			OIDCIssuer:    "https://kc.example/realms/tatara",
  			OIDCClientID:  "tatara-operator",
  			OIDCAudience:  "tatara-memory",
  			Namespace:     testNS,
  		},
  	}
  }
  ```

  The existing `mkProject` helper creates a Project with no memory status. Add
  a helper that marks a Project's memory Ready with an endpoint, and a helper
  that asserts a Repository condition. Append to the test file:

  ```go
  func setProjectMemoryReady(t *testing.T, name, endpoint string) {
  	t.Helper()
  	p := &tataradevv1alpha1.Project{}
  	if err := k8sClient.Get(context.Background(),
  		types.NamespacedName{Namespace: testNS, Name: name}, p); err != nil {
  		t.Fatalf("get project %s: %v", name, err)
  	}
  	p.Status.Memory = &tataradevv1alpha1.MemoryStatus{Phase: "Ready", Endpoint: endpoint}
  	if err := k8sClient.Status().Update(context.Background(), p); err != nil {
  		t.Fatalf("set project %s memory ready: %v", name, err)
  	}
  }
  ```

  Now add the two gating tests. The not-ready test asserts NO Job and the
  `MemoryNotReady` condition; the ready test asserts the Job carries the
  Project endpoint as `--base-url`:

  ```go
  func TestRepoReconcile_GatesUntilMemoryReady(t *testing.T) {
  	mkProject(t, "rp-mem", "rp-mem-scm")
  	mkSecret(t, "rp-mem-scm", map[string][]byte{"token": []byte("x"), "webhookSecret": []byte("y")})
  	mkRepo(t, "memrepo", "rp-mem")

  	// Project memory is not Ready (no status.memory at all).
  	res, err := reconcileRepo(t, "memrepo")
  	if err != nil {
  		t.Fatalf("reconcile: %v", err)
  	}
  	if res.RequeueAfter == 0 {
  		t.Error("expected requeue while memory not ready")
  	}
  	if jobs := listIngestJobs(t, "memrepo"); len(jobs) != 0 {
  		t.Fatalf("memory not ready must not launch a job, got %d", len(jobs))
  	}
  	r := getRepo(t, "memrepo")
  	cond := findCond(r.Status.Conditions, "MemoryNotReady")
  	if cond == nil || cond.Status != metav1.ConditionTrue {
  		t.Fatalf("expected MemoryNotReady=True condition, got %+v", cond)
  	}
  }

  func TestRepoReconcile_UsesProjectEndpointWhenReady(t *testing.T) {
  	mkProject(t, "rp-ep", "rp-ep-scm")
  	mkSecret(t, "rp-ep-scm", map[string][]byte{"token": []byte("x"), "webhookSecret": []byte("y")})
  	mkRepo(t, "eprepo", "rp-ep")
  	setProjectMemoryReady(t, "rp-ep", "http://mem-rp-ep.tatara.svc:8080")

  	if _, err := reconcileRepo(t, "eprepo"); err != nil {
  		t.Fatalf("reconcile: %v", err)
  	}
  	waitRepoJob(t, "eprepo")
  	jobs := listIngestJobs(t, "eprepo")
  	if len(jobs) != 1 {
  		t.Fatalf("jobs = %d, want 1", len(jobs))
  	}
  	script := jobs[0].Spec.Template.Spec.Containers[0].Args[0]
  	if !contains(script, "--base-url http://mem-rp-ep.tatara.svc:8080") {
  		t.Errorf("ingest job must use the Project endpoint as base-url: %q", script)
  	}
  }
  ```

  The four pre-existing repo tests (`TestRepoReconcile_FullIngestLaunchesJob`,
  `_ConcurrencyGuard`, `_IncrementalUsesSince`, `_NoReingestWhenAnnotationStale`)
  create Projects via `mkProject` that have NO memory status, so the new gate
  would make them requeue without a Job and break. Add a
  `setProjectMemoryReady(...)` call right after each `mkRepo(...)` in those four
  tests so their existing assertions (Job launched / incremental / stale) still
  hold. Concretely, in each, after the `mkRepo(t, "<name>", "<proj>")` line
  insert:

  ```go
  	setProjectMemoryReady(t, "<proj>", "http://mem-<proj>.tatara.svc:8080")
  ```

  using each test's project name: `rp-full`, `rp-guard`, `rp-inc`, `rp-stale`.
  (`_NoReingestWhenAnnotationStale` asserts zero jobs because the annotation is
  stale, so marking memory Ready does not change its expectation; keep the
  call for consistency so the gate is not the reason it has zero jobs.)

- [ ] Run, expect FAIL (gate + endpoint not yet implemented; the two new tests
  fail and the four edited tests fail because the gate does not exist so they
  currently still launch but `BuildJob` call-site has not been updated to the
  new signature -> compile error):

  ```
  go test ./internal/controller/... -run TestRepoReconcile
  ```

  Expected FAIL: compile error `not enough arguments in call to ingest.BuildJob`
  (the reconciler still calls the old 4-arg form) once Task 1 changed the
  signature. That is the expected red.

- [ ] Implement the gate and the endpoint pass-through. Edit
  `internal/controller/repository_controller.go`.

  Add the `meta`-based imports already present (`apimachinery/pkg/api/meta`,
  `metav1`) - both are already imported. After the existing Project Get block
  (the `var project ...; if err := r.Get(... repo.Spec.ProjectRef ...)` block
  around lines 91-95), insert the gate BEFORE `ensureResultConfigMap`:

  ```go
  	if project.Status.Memory == nil || project.Status.Memory.Phase != "Ready" {
  		meta.SetStatusCondition(&repo.Status.Conditions, metav1.Condition{
  			Type:               "MemoryNotReady",
  			Status:             metav1.ConditionTrue,
  			Reason:             "MemoryProvisioning",
  			Message:            "waiting for project " + project.Name + " memory stack to become Ready",
  			ObservedGeneration: repo.Generation,
  		})
  		if err := r.Status().Update(ctx, &repo); err != nil {
  			r.Metrics.ReconcileResult("Repository", "error")
  			return ctrl.Result{}, fmt.Errorf("set MemoryNotReady condition: %w", err)
  		}
  		l.Info("ingest gated: project memory not ready",
  			"action", "ingest_gate", "resource_id", repo.Name, "project", project.Name)
  		r.Metrics.ReconcileResult("Repository", "success")
  		return ctrl.Result{RequeueAfter: 15 * time.Second}, nil
  	}
  ```

  Then update the `BuildJob` call to pass the endpoint as the new `baseURL`
  argument:

  ```go
  	job := ingest.BuildJob(&project, &repo, since, project.Status.Memory.Endpoint, r.IngestConfig)
  ```

- [ ] Run, expect PASS:

  ```
  go test ./internal/controller/... -run TestRepoReconcile
  ```

  Expected: `ok ... internal/controller` (all repo tests green, including the
  two new gating tests).

- [ ] Commit:

  ```
  git add internal/controller/repository_controller.go internal/controller/repository_controller_test.go
  git commit -m "feat: gate ingest on project memory Ready; use per-project endpoint"
  ```

---

## Task 3: agent pod builder takes the memory endpoint and emits TATARA_MEMORY_URL

### Files
- MODIFY `internal/agent/pod.go`
- MODIFY `internal/agent/pod_test.go`

### Steps

- [ ] Write the failing unit test first. Edit `internal/agent/pod_test.go`.

  Change `BuildPod`/`BuildService` to take a `memoryEndpoint string` argument
  (the simplest, most explicit threading; the endpoint is per-Task runtime
  data, not static `PodConfig`). Update `sampleInputs` callers and add an
  assertion.

  Add a constant near the top (after imports):

  ```go
  const testMemoryEndpoint = "http://mem-demo.tatara.svc:8080"
  ```

  Update every `agent.BuildPod(proj, repo, task, cfg)` call to
  `agent.BuildPod(proj, repo, task, testMemoryEndpoint, cfg)` and every
  `agent.BuildService(proj, repo, task, cfg)` to
  `agent.BuildService(proj, repo, task, testMemoryEndpoint, cfg)`. The affected
  tests: `TestBuildPod_NameAndImageAndOwner`, `TestBuildPod_PlainEnv`,
  `TestBuildPod_SecretEnv`, `TestBuildPod_CallbackURLFromConfig`,
  `TestBuildPod_PortAndReadiness`, `TestBuildService_MatchesPod`.

  In `TestBuildPod_PlainEnv`, add the new env to the `checks` map:

  ```go
  		"TATARA_MEMORY_URL":    "http://mem-demo.tatara.svc:8080",
  ```

  Append a dedicated test:

  ```go
  func TestBuildPod_MemoryEndpointEnv(t *testing.T) {
  	proj, repo, task, cfg := sampleInputs()
  	const ep = "http://mem-other.tatara.svc:8080"
  	c := agent.BuildPod(proj, repo, task, ep, cfg).Spec.Containers[0]
  	got, ok := envValue(c, "TATARA_MEMORY_URL")
  	require.True(t, ok, "TATARA_MEMORY_URL missing")
  	require.Equal(t, ep, got)
  }
  ```

- [ ] Run, expect FAIL (compile error: too many args / missing env):

  ```
  go test ./internal/agent/...
  ```

  Expected FAIL: `too many arguments in call to agent.BuildPod`.

- [ ] Implement. Edit `internal/agent/pod.go`.

  Change `BuildPod` to accept `memoryEndpoint string` (insert before `cfg`):

  ```go
  func BuildPod(project *tatarav1alpha1.Project, repo *tatarav1alpha1.Repository, task *tatarav1alpha1.Task, memoryEndpoint string, cfg PodConfig) *corev1.Pod {
  ```

  Add the env entry to the `env` slice (place it alongside the other plain envs,
  after `TATARA_PROJECT`):

  ```go
  		// Per-project memory endpoint: the agent's tatara-cli memory MCP reads
  		// TATARA_MEMORY_URL to reach this Project's tatara-memory service.
  		{Name: "TATARA_MEMORY_URL", Value: memoryEndpoint},
  ```

  Change `BuildService` to the matching signature so callers thread one value
  (the service does not use the endpoint, but keeping the signatures parallel
  avoids a divergent call shape):

  ```go
  func BuildService(project *tatarav1alpha1.Project, repo *tatarav1alpha1.Repository, task *tatarav1alpha1.Task, memoryEndpoint string, cfg PodConfig) *corev1.Service {
  ```

  Add `_ = memoryEndpoint` as the first line of `BuildService` to keep it
  unused-arg-clean (gofmt/govet pass; the arg exists only for call symmetry):

  ```go
  	_ = memoryEndpoint
  ```

- [ ] Run, expect PASS:

  ```
  go test ./internal/agent/...
  ```

  Expected: `ok github.com/szymonrychu/tatara-operator/internal/agent`.

- [ ] Commit:

  ```
  git add internal/agent/pod.go internal/agent/pod_test.go
  git commit -m "feat: wrapper pod carries per-project TATARA_MEMORY_URL env"
  ```

---

## Task 4: TaskReconciler gates on Project memory Ready and threads the endpoint

### Files
- MODIFY `internal/controller/task_controller.go`
- MODIFY `internal/controller/task_controller_test.go`

### Steps

- [ ] Write the failing envtest first. Edit
  `internal/controller/task_controller_test.go`.

  The existing `mkTaskProject` creates a Project with no memory status. Add a
  reuse of the repo test's `setProjectMemoryReady` (it lives in the same
  `controller` package and is already defined in
  `repository_controller_test.go`, so it is directly callable here - do NOT
  redefine it).

  Add the gating test:

  ```go
  func TestTaskReconcile_GatesUntilMemoryReady(t *testing.T) {
  	mkTaskProject(t, "p-memgate", 3)
  	mkTaskRepository(t, "r-memgate", "p-memgate")
  	mkTask(t, "t-memgate", "p-memgate", "r-memgate")
  	// Project memory not Ready -> requeue, no pod.

  	fs := newFakeSession()
  	r := newTaskReconciler(fs)
  	res, err := reconcileTask(t, r, "t-memgate")
  	if err != nil {
  		t.Fatalf("reconcile: %v", err)
  	}
  	if res.RequeueAfter == 0 {
  		t.Error("expected requeue while project memory not ready")
  	}
  	pod := &corev1.Pod{}
  	err = k8sClient.Get(context.Background(),
  		types.NamespacedName{Namespace: testNS, Name: "wrapper-t-memgate"}, pod)
  	if !apierrors.IsNotFound(err) {
  		t.Errorf("memory not ready must not spawn a pod, got err=%v", err)
  	}
  }

  func TestTaskReconcile_PodCarriesMemoryEndpoint(t *testing.T) {
  	mkTaskProject(t, "p-memep", 3)
  	mkTaskRepository(t, "r-memep", "p-memep")
  	mkTask(t, "t-memep", "p-memep", "r-memep")
  	setProjectMemoryReady(t, "p-memep", "http://mem-p-memep.tatara.svc:8080")

  	fs := newFakeSession()
  	r := newTaskReconciler(fs)
  	if _, err := reconcileTask(t, r, "t-memep"); err != nil {
  		t.Fatalf("reconcile: %v", err)
  	}
  	pod := &corev1.Pod{}
  	if err := k8sClient.Get(context.Background(),
  		types.NamespacedName{Namespace: testNS, Name: "wrapper-t-memep"}, pod); err != nil {
  		t.Fatalf("expected wrapper pod: %v", err)
  	}
  	var got string
  	for _, e := range pod.Spec.Containers[0].Env {
  		if e.Name == "TATARA_MEMORY_URL" {
  			got = e.Value
  		}
  	}
  	if got != "http://mem-p-memep.tatara.svc:8080" {
  		t.Errorf("TATARA_MEMORY_URL = %q, want the project endpoint", got)
  	}
  }
  ```

  Every existing Task test creates its Project via `mkTaskProject` (no memory
  status) and then expects spawn/turns. With the new gate those would all
  requeue early and break. Add `setProjectMemoryReady(t, "<project>",
  "http://mem-<project>.tatara.svc:8080")` immediately after the
  `mkTask(...)`/task-create line in each of these tests, using each test's
  project name: `p-spawn`, `p-cap`, `p-plan`, `p-adv`, `p-end`, `p-max`,
  `p-tt`, `p-lost`, `p-rssum`, `p-rscount`, `p-rsnoop`.

  Notes for the special cases:
  - `TestTaskReconcile_GatesAtCap` (`p-cap`): mark `p-cap` memory Ready so the
    cap gate (not the memory gate) is what blocks `t-queued`.
  - `TestTaskReconcile_TerminalNoop` (`p-term`): the task is terminal and
    returns before the memory gate, so no `setProjectMemoryReady` is required;
    leaving it out keeps the test asserting the terminal short-circuit. (Adding
    it is harmless; omit for clarity.)
  - `TestTaskReconcile_MaxTurnsCap` (`p-max`): the Task is created inline, not
    via `mkTask`; add the `setProjectMemoryReady(t, "p-max", ...)` right after
    the `k8sClient.Create` for the task.

- [ ] Run, expect FAIL (compile error from the changed `BuildPod`/`BuildService`
  signatures used by the reconciler, plus the two new tests):

  ```
  go test ./internal/controller/... -run TestTaskReconcile
  ```

  Expected FAIL: `not enough arguments in call to agent.BuildPod` (the
  reconciler still calls the old 4-arg form).

- [ ] Implement. Edit `internal/controller/task_controller.go`.

  Add the memory gate right after the Project Get (the block around lines
  93-97), BEFORE the concurrency cap check:

  ```go
  	if project.Status.Memory == nil || project.Status.Memory.Phase != "Ready" {
  		l.Info("task gated: project memory not ready",
  			"action", "task_memory_gate", "resource_id", task.Name, "project", project.Name)
  		return ctrl.Result{RequeueAfter: capRequeue}, nil
  	}
  ```

  (`capRequeue` is the existing `15 * time.Second` constant.)

  Thread the endpoint into `ensurePodAndService`. Change its signature to
  accept the endpoint and pass it to the builders:

  ```go
  func (r *TaskReconciler) ensurePodAndService(ctx context.Context, project *tatarav1alpha1.Project, repo *tatarav1alpha1.Repository, task *tatarav1alpha1.Task) (bool, error) {
  	pod := agent.BuildPod(project, repo, task, project.Status.Memory.Endpoint, r.PodConfig)
  ```

  and the service line:

  ```go
  	svc := agent.BuildService(project, repo, task, project.Status.Memory.Endpoint, r.PodConfig)
  ```

  No call-site change to `ensurePodAndService` is needed (it already receives
  `project`); the endpoint is read from `project.Status.Memory.Endpoint` inside
  it. The gate above guarantees `project.Status.Memory != nil` by the time
  `ensurePodAndService` runs.

- [ ] Run, expect PASS:

  ```
  go test ./internal/controller/... -run TestTaskReconcile
  ```

  Expected: `ok ... internal/controller` (all Task tests green incl. the two
  new ones).

- [ ] Commit:

  ```
  git add internal/controller/task_controller.go internal/controller/task_controller_test.go
  git commit -m "feat: gate Task on project memory Ready; thread endpoint to wrapper pod"
  ```

---

## Task 5: remove MemoryBaseURL from config + wire.go

### Files
- MODIFY `internal/config/config.go`
- MODIFY `internal/config/config_test.go`
- MODIFY `cmd/manager/wire.go`
- MODIFY `cmd/manager/wire_test.go`

### Steps

- [ ] Write the failing test first. Edit `internal/config/config_test.go`:

  Remove the `MEMORY_BASE_URL` env from the `env` map in `TestLoad`:

  ```go
  		"OIDC_AUDIENCE":               "tatara-operator",
  		"INGESTER_IMAGE":              "harbor/ingester:1",
  ```

  (delete the `"MEMORY_BASE_URL": "http://tatara-memory:8080",` line)

  Remove the `MemoryBaseURL` assertion row from the `tests` table:

  ```go
  		{"OIDCAudience", cfg.OIDCAudience, "tatara-operator"},
  		{"IngesterImage", cfg.IngesterImage, "harbor/ingester:1"},
  ```

  (delete `{"MemoryBaseURL", cfg.MemoryBaseURL, "http://tatara-memory:8080"},`)

  Edit `cmd/manager/wire_test.go` `TestIngestConfigFromConfig`:

  ```go
  func TestIngestConfigFromConfig(t *testing.T) {
  	cfg := config.Config{
  		IngesterImage:            "img:1",
  		OIDCIssuer:               "https://kc/realms/t",
  		OperatorOIDCClientID:     "tatara-operator",
  		OperatorOIDCClientSecret: "secret",
  		Namespace:                "tatara",
  	}
  	got := ingestConfigFromConfig(cfg, "tatara-memory")
  	want := ingest.Config{
  		IngesterImage:    "img:1",
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

- [ ] Run, expect FAIL (the structs still have/lack the field inconsistently):

  ```
  go test ./internal/config/... ./cmd/manager/...
  ```

  Expected FAIL: `cfg.MemoryBaseURL` referenced in `wire.go` no longer matches
  the test's omission, OR (after the config edit below is not yet done)
  `unknown field` mismatches. Confirm a red before proceeding.

- [ ] Implement. Edit `internal/config/config.go`:

  Remove the field from the `Config` struct:

  ```go
  	OIDCIssuer               string
  	OIDCAudience             string
  	IngesterImage            string
  ```

  (delete the `MemoryBaseURL            string` line)

  Remove the env read in `Load`:

  ```go
  		OIDCAudience:             os.Getenv("OIDC_AUDIENCE"),
  		IngesterImage:            os.Getenv("INGESTER_IMAGE"),
  ```

  (delete `MemoryBaseURL:            os.Getenv("MEMORY_BASE_URL"),`)

  Edit `cmd/manager/wire.go`, `ingestConfigFromConfig`:

  ```go
  func ingestConfigFromConfig(cfg config.Config, memoryAudience string) ingest.Config {
  	return ingest.Config{
  		IngesterImage:    cfg.IngesterImage,
  		OIDCIssuer:       cfg.OIDCIssuer,
  		OIDCClientID:     cfg.OperatorOIDCClientID,
  		OIDCClientSecret: cfg.OperatorOIDCClientSecret,
  		OIDCAudience:     memoryAudience,
  		Namespace:        cfg.Namespace,
  	}
  }
  ```

- [ ] Run, expect PASS:

  ```
  go test ./internal/config/... ./cmd/manager/...
  ```

  Expected: both packages `ok`.

- [ ] Verify nothing references the removed field anywhere:

  ```
  grep -rn "MemoryBaseURL\|MEMORY_BASE_URL" internal/ cmd/ api/
  ```

  Expected: NO output (empty). If anything remains, fix it before committing.

- [ ] Commit:

  ```
  git add internal/config/config.go internal/config/config_test.go cmd/manager/wire.go cmd/manager/wire_test.go
  git commit -m "refactor: remove MemoryBaseURL config and wiring"
  ```

---

## Task 6: full build, vet, lint, and whole-suite verification

### Files
- none (verification only)

### Steps

- [ ] Run the full module build and the whole test suite (envtest included):

  ```
  go build ./... && go vet ./... && make test
  ```

  Expected: build clean, `go vet` silent, `make test` reports all packages
  `ok` (envtest control plane boots once for `internal/controller`). No
  package may be `FAIL` or `[build failed]`.

- [ ] Run the linter exactly as CI does:

  ```
  golangci-lint run ./...
  ```

  Expected: `0 issues`.

- [ ] Confirm the gate behaviors one more time in isolation (cheap sanity,
  re-run only these since they changed):

  ```
  go test ./internal/controller/... -run 'Memory'
  go test ./internal/ingest/... ./internal/agent/...
  ```

  Expected: all `ok`.

- [ ] Update `MEMORY.md` (append, dated `2026-06-07`):

  ```
  - 2026-06-07 N3: ingest Job + wrapper pod target per-project
    `Project.status.memory.endpoint`; both reconcilers requeue (15s) until
    `status.memory.phase == "Ready"` (Repository sets `MemoryNotReady`
    condition). `ingest.BuildJob` takes base URL as a param; `agent.BuildPod`
    emits `TATARA_MEMORY_URL` (agent tatara-cli memory MCP reads it). Operator
    `Config.MemoryBaseURL` / `MEMORY_BASE_URL` removed.
  ```

- [ ] Update `ROADMAP.md`: mark N3 done, leave N4 (retire static tatara-memory
  + chart RBAC/values + image bump + redeploy) as the next milestone.

- [ ] Commit:

  ```
  git add MEMORY.md ROADMAP.md
  git commit -m "docs: N3 per-project memory wiring complete; N4 next"
  ```

---

## Done criteria

- `go build ./...`, `go vet ./...`, `make test`, `golangci-lint run ./...` all
  clean.
- `grep -rn "MemoryBaseURL\|MEMORY_BASE_URL" internal/ cmd/ api/` is empty.
- A Repository whose Project memory is not Ready: no ingest Job, condition
  `MemoryNotReady=True`, requeue.
- A Repository whose Project memory is Ready: ingest Job `--base-url` and
  `BASE_URL` env equal `project.Status.Memory.Endpoint`.
- A Task whose Project memory is not Ready: no wrapper pod, requeue.
- A Task whose Project memory is Ready: wrapper pod env `TATARA_MEMORY_URL`
  equals `project.Status.Memory.Endpoint`.
- `ingest.BuildJob` and `agent.BuildPod`/`BuildService` take the endpoint as a
  parameter; `ingest.Config` has no `MemoryBaseURL`.

## Out of scope (do NOT do here)

- N4: retiring the static `tatara-memory` helmfile release, operator chart RBAC
  additions, infra operator values (`memoryImage`/`lightragImage`/`neo4jImage`/
  `openaiSecretName`, dropping `memoryBaseUrl`), image bump, redeploy,
  `helm uninstall`.
- Any `internal/memory` builder changes (N1) or `ProjectReconciler`
  provisioning/status (N2).
- Changing the OIDC audience (`tatara-memory`) - unchanged; the per-Project
  endpoints all share it.
