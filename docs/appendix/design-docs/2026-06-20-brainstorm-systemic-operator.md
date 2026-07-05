# Plan: Cross-repo-aware brainstorm + ultracode agents - tatara-operator

For agentic workers using `superpowers:subagent-driven-development`. Each Task
below is a self-contained TDD unit: dispatch one sonnet implementation subagent
per Task with this file's path + the Task number. Tasks are ordered so types,
CRD, and the scm primitive land before their consumers. The opus merge subagent
integrates parallel Tasks and runs `superpowers:requesting-code-review` before
each commit.

Repo: `/Users/szymonri/Documents/tatara/tatara-operator` (Go), branch `main`.
Develop in a worktree off `main` (`superpowers:using-git-worktrees`), merge back
to `main`, build/deploy from `main` only.

Spec: `docs/superpowers/specs/2026-06-20-brainstorm-systemic-and-agent-ultracode-design.md`

## Goal

Make `brainstorm()`/`healthCheck()` survey live cross-repo state (open issues +
open MRs/PRs with CI + `main` health), reason about SYSTEMIC opportunities, and
manage the backlog. Add an agent effort knob (`EFFORT` env), multi-issue systemic
proposals (shared `systemicId` + correlation label, counting as one against the
cap), egress rejection of weak titles with a derived no-agent fallback, and
issue-numbered work branches.

## Architecture

- **scm**: new `SCMReader.GetDefaultBranchHeadSHA(ctx, owner, repo)` primitive in
  `github.go`/`gitlab.go` + all 15 test fakes; paired with existing
  `GetCommitCIStatus` for per-repo `main` pipeline health.
- **controller/projectscan**: `buildRepoStateContext` replaces `buildIssuesContext`,
  emitting ISSUES / OPEN MRs / MAIN HEALTH blocks from already-fetched data plus
  bounded MR-state/main-health reads (non-fatal). Goals gain the systemic mandate +
  orchestration instructions. `proposalBacklogCount` groups by systemic label.
- **controller/writeback**: `weakTitle` gate, systemic label + footer on
  `createProposal`, PR-title derivation from `Task.Spec.Source.Title`.
- **restapi**: `propose_issue` gains `systemicId`; `propose_issue`/`change_summary`
  reject weak titles 4xx with guidance.
- **agent/pod**: `TaskBranch` issue-numbered; `EFFORT` env in `BuildPod`.
- **api/v1alpha1**: `AgentSpec.Effort`, `TaskSource.Title`,
  `ProposedIssueSpec.SystemicID`. CRD + deepcopy regenerated.
- **webhook**: populate `TaskSource.Title` from `ev.Title`. `TaskSource` flows
  through `internal/queue/enqueue.go:168` (`Source: p.Source`) to the Task spec
  unchanged - no queue change needed.

## Tech Stack

Go 1.26.3 (pinned in `go.mod` and `.mise.toml`), controller-runtime, chi router,
controller-gen v0.18.0, stdlib `log/slog`, Prometheus, envtest. Test fixtures use
`httptest.NewServer` with `&GitHub{apiBase: srv.URL}` / `&GitLab{apiBase: srv.URL}`
(the per-client `token` is empty in tests).

## Toolchain commands (mise)

- Tests: `mise exec -- make test`  (envtest-backed; or a focused package:
  `mise exec -- go test ./internal/scm/... -race -count=1`)
- Lint:  `mise run lint`            (== `mise exec -- golangci-lint run ./...`)
- Build: `mise run build`
- **CRD + deepcopy regen (mandatory after any `api/v1alpha1` type change):**
  `mise exec -- make generate manifests`
  - `make generate` -> `controller-gen object:...` rewrites `api/v1alpha1/zz_generated.deepcopy.go`.
  - `make manifests` -> `controller-gen crd ... output:crd:artifacts:config=charts/tatara-operator/crd-bases`
    rewrites the CRD YAMLs the helm `templates/crds.yaml` inject relies on.
  - Always `mise exec -- make fmt` (gofmt -s) before commit.

## Global Constraints (spec hard rules - verbatim contract)

1. **Newest stable Go** for any Go service. Pin the Go directive to the exact
   minor in `go.mod` (currently `go 1.26.3`).
2. **KISS, always.** Three similar lines beats a premature abstraction.
3. **Boy-scout rule** on adjacent issues; fix easy adjacent things, do not ask.
4. **NEVER introduce tech-debt.** Complex thing -> call it out in `MEMORY.md`
   with rationale. Never defer cleanup to "later".
5. **Charts via `helm create`** then edited; never hand-rolled.
6. **No plain ENVs / no lists in values.yaml.** camelCase scalar -> kebab-case
   ConfigMap/Secret key -> `envFrom`. List-shaped data into a templated ConfigMap.
7. **Sonnet for implementation, Opus for merges.** Plan + review in opus.
8. **EVERYTHING through superpowers** (brainstorming, writing-plans, TDD,
   systematic-debugging, requesting-code-review, verification-before-completion,
   subagent-driven-development, using-git-worktrees, finishing-a-development-branch).
9. **Subagent-driven, parallel** where independent; dispatch in one message.
10. **Branch flow:** worktree off `main` -> develop -> merge to `main` -> cleanup
    worktree -> build/deploy from `main` only.
11. **JSON logs only.** Stdlib `log/slog` (controller uses `log.FromContext`);
    same structured field shape everywhere.
12. **Log every business action at INFO** with structured fields (`action`,
    `resource_id`, `duration_ms` where relevant). WARN/ERROR appropriately.
13. **Metrics for everything that counts, times out, or can fail.** Counters,
    histograms, gauges. `/metrics` already exposed.
14. **Charts cluster-agnostic.** No baked cluster specifics in `values.yaml`;
    cluster config lives in `tatara-helmfile`.
15. **Deploy ONLY through `tatara-helmfile` (GitOps).** Never `kubectl set image`
    / `patch` / `helm upgrade` by hand to ship. Deploy = merge to operator `main`
    (CI builds image+chart) -> `tatara-helmfile` MR bumping BOTH the operator
    chart version AND the pinned `image.tag` (see
    `tatara-operator-deploy-chart-version-and-image-tag`).

## Conventions used in code below

- Errors wrapped: `fmt.Errorf("context: %w", err)`.
- Tests table-driven with `t.Run`.
- Conventional commits: `feat: ...`, `fix: ...`, `refactor: ...`, `test: ...`.

---

## Anchor corrections discovered (vs the brief)

- `ProposedIssueSpec` (not "ProposedIssue spec") is the struct at
  `api/v1alpha1/task_types.go:11`; its enum field `Kind` is
  `+kubebuilder:validation:Enum=bug;improvement`. `SystemicID` is added here.
- `TaskSource` is at `task_types.go:79` (fields: Provider, IssueRef, URL,
  AuthorLogin, IsPR, Number - all confirmed). `Title` is added here.
- `AgentSpec` is at `project_types.go:65` (Model, Image, PermissionMode,
  MaxTurnsPerTask, TurnTimeoutSeconds, ContextWindowTokens,
  HandoverThresholdPercent, MaxLifecycleIterations). `Effort` is added here.
- `buildIssuesContext` is a METHOD: `func (r *ProjectReconciler) buildIssuesContext(ctx, proj, reader, issuesBySlug map[string][]scm.IssueRef, repos []Repository) string`
  at `projectscan.go:1113`. `buildRepoStateContext` keeps the same receiver+signature.
- `firstLine` is at `writeback.go:396` (used at `writeback.go:119`, 72-char cap).
  `taskBranch` (lowercase, `writeback.go:392`) delegates to `agent.TaskBranch`.
- `TaskBranch` is at `agent/pod.go:295`; `BuildPod` env list at `pod.go:312`
  (MODEL at 313). `podNameSuffix` at `pod.go:173` already derives `issue-N`/`mr-N`.
- restapi `proposeIssueReq` at `handlers.go:367`; handler `proposeIssue` at 374
  (builds `ProposedIssue` at 420). `changeSummaryReq` at 966; `changeSummary` at 974.
- webhook source blocks at `server.go:415` (reactive) and `server.go:610`
  (issueLifecycle); `ev.Title` is populated by both providers' DetectAndVerify
  (`github.go:115`, `gitlab.go:117`).
- `QueuedEventPayload.Source` is `*TaskSource` (`queuedevent_types.go:28`); the
  dispatcher copies it wholesale onto `Task.Spec.Source` at
  `internal/queue/enqueue.go:168`. Adding `Title` to `TaskSource` threads through
  the queue with NO queue change - only the upstream populators change.
- scan-born tasks build `TaskSource` in `createScanTask` (`projectscan.go:281`);
  the `candidate` struct (`projectscan.go:192`) has no title field, so WS5 adds
  `title` to `candidate` and the callers (issueScan/mrScan candidate builders)
  set it.
- SCMReader is implemented by `*GitHub`/`*GitLab` (production) plus 15 test fakes
  (one `GetIssue` method each); every fake gains a `GetDefaultBranchHeadSHA` stub.

---

# Tasks

## Task 1 - CRD fields: AgentSpec.Effort, TaskSource.Title, ProposedIssueSpec.SystemicID

**Files**
- Modify `api/v1alpha1/project_types.go:65` (AgentSpec) - add `Effort`.
- Modify `api/v1alpha1/task_types.go:11` (ProposedIssueSpec) - add `SystemicID`.
- Modify `api/v1alpha1/task_types.go:79` (TaskSource) - add `Title`.
- Regenerate `api/v1alpha1/zz_generated.deepcopy.go` + `charts/tatara-operator/crd-bases/*.yaml`.
- Test: `api/v1alpha1/types_fields_test.go` (new).

**Interfaces**
- Produces: `ProjectSpec.Agent.Effort string`; `TaskSource.Title string`;
  `ProposedIssueSpec.SystemicID string`.

**Steps**

1. Write failing test `api/v1alpha1/types_fields_test.go`:

```go
package v1alpha1

import (
	"encoding/json"
	"testing"
)

func TestNewFields_JSONRoundTrip(t *testing.T) {
	tests := []struct {
		name    string
		marshal func() ([]byte, error)
		want    string
	}{
		{
			name: "AgentSpec.Effort json key effort",
			marshal: func() ([]byte, error) {
				return json.Marshal(AgentSpec{Effort: "max"})
			},
			want: `"effort":"max"`,
		},
		{
			name: "TaskSource.Title json key title",
			marshal: func() ([]byte, error) {
				return json.Marshal(TaskSource{Provider: "github", IssueRef: "o/r#1", Title: "fix the thing"})
			},
			want: `"title":"fix the thing"`,
		},
		{
			name: "ProposedIssueSpec.SystemicID json key systemicId",
			marshal: func() ([]byte, error) {
				return json.Marshal(ProposedIssueSpec{RepositoryRef: "r", Title: "t", Body: "b", Kind: "bug", SystemicID: "abc123"})
			},
			want: `"systemicId":"abc123"`,
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			b, err := tc.marshal()
			if err != nil {
				t.Fatalf("marshal: %v", err)
			}
			if !jsonContains(string(b), tc.want) {
				t.Fatalf("json %s does not contain %s", b, tc.want)
			}
		})
	}
}

func jsonContains(haystack, needle string) bool {
	return len(haystack) >= len(needle) && (func() bool {
		for i := 0; i+len(needle) <= len(haystack); i++ {
			if haystack[i:i+len(needle)] == needle {
				return true
			}
		}
		return false
	})()
}
```

2. Run-it-fails: `mise exec -- go test ./api/v1alpha1/... -run TestNewFields_JSONRoundTrip -count=1`
   Expect: compile error - `Effort`, `Title`, `SystemicID` undefined.

3. Minimal impl. In `project_types.go` AgentSpec, append:

```go
	// Effort is the reasoning-effort level passed to the wrapper agent as the
	// EFFORT env var (the "ultracode" lever). Highest by default.
	// +kubebuilder:validation:Enum=low;medium;high;xhigh;max
	// +kubebuilder:default="xhigh"
	// +optional
	Effort string `json:"effort,omitempty"`
```

In `task_types.go` ProposedIssueSpec, append after `Kind`:

```go
	// SystemicID correlates one of several issues opened for a single systemic
	// improvement. When set, createProposal stamps label tatara/systemic-<id>
	// and a sibling footer; the group counts as one against maxOpenProposals.
	// +optional
	SystemicID string `json:"systemicId,omitempty"`
```

In `task_types.go` TaskSource, append after `Number`:

```go
	// Title is the originating issue/PR/MR title, captured at enqueue. Feeds the
	// branch slug (TaskBranch) and the no-agent PR-title fallback.
	// +optional
	Title string `json:"title,omitempty"`
```

4. Regenerate: `mise exec -- make generate manifests && mise exec -- make fmt`.

5. Run-it-passes: `mise exec -- go test ./api/v1alpha1/... -count=1` (green).
   Sanity: `git status` shows `zz_generated.deepcopy.go` and three
   `crd-bases/*.yaml` modified.

6. Commit: `feat(api): add AgentSpec.Effort, TaskSource.Title, ProposedIssueSpec.SystemicID + regen CRDs`

---

## Task 2 - SCM primitive: GetDefaultBranchHeadSHA

**Files**
- Modify `internal/scm/scm.go:146` (SCMReader interface) - add method.
- Modify `internal/scm/github.go` - implement (uses `ghDo`, `c.base()`, `c.token`).
- Modify `internal/scm/gitlab.go` - implement (uses `glDo`, `url.PathEscape`).
- Modify all 15 fakes (see list below) - add stub.
- Test: `internal/scm/default_branch_test.go` (new), mirroring
  `commit_ci_status_test.go` httptest pattern.

Fakes to update (each gets one method): `internal/controller/labels_test.go`
`commentReader`; `lifecycle_discuss_silence_test.go` `discussSilenceReader`;
`lifecycle_fixes_test.go` `fakeReaderCapture`; `lifecycle_label_test.go`
`errGetIssueReader`; `lifecycle_m2_test.go` `fakeReaderComments` +
`fakeReaderWithIssue`; `lifecycle_r2_audit_test.go` `ciStatusReader`;
`lifecycle_test.go` `fakeReaderMainCI`; `projectscan_run_test.go` `fakeReader`;
`proposal_dedup_test.go` `fakeProposalReader`; `writeback_r2_test.go`
`fakePRReader` + `gitlabProjectPathReader`; `writeback_r3_audit_test.go`
`listMetricReader`; `internal/restapi/issue_comment_test.go` `fakeReader`.

**Interfaces**
- Produces (SCMReader): `GetDefaultBranchHeadSHA(ctx context.Context, owner, repo string) (string, error)`.
  GitHub: `GET /repos/{owner}/{repo}` -> `default_branch`, then
  `GET /repos/{owner}/{repo}/commits/{branch}` -> `.sha`.
  GitLab (`owner` carries full project path; `repo` unused, matching
  GetCommitCIStatus): `GET /projects/{path}` -> `default_branch`, then
  `GET /projects/{path}/repository/branches/{branch}` -> `.commit.id`.

**Steps**

1. Write failing test `internal/scm/default_branch_test.go`:

```go
package scm

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
)

func TestGitHubGetDefaultBranchHeadSHA(t *testing.T) {
	tests := []struct {
		name        string
		defBranch   string
		commitSHA   string
		wantSHA     string
		repoStatus  int
		wantErr     bool
	}{
		{name: "resolves main head", defBranch: "main", commitSHA: "abc123", wantSHA: "abc123"},
		{name: "non-default branch name", defBranch: "trunk", commitSHA: "def456", wantSHA: "def456"},
		{name: "repo 404 errors", defBranch: "main", repoStatus: http.StatusNotFound, wantErr: true},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch {
				case r.URL.Path == "/repos/o/r":
					if tc.repoStatus != 0 {
						w.WriteHeader(tc.repoStatus)
						return
					}
					_ = json.NewEncoder(w).Encode(map[string]any{"default_branch": tc.defBranch})
				case r.URL.Path == "/repos/o/r/commits/"+tc.defBranch:
					_ = json.NewEncoder(w).Encode(map[string]any{"sha": tc.commitSHA})
				default:
					t.Errorf("unexpected path %q", r.URL.Path)
					w.WriteHeader(http.StatusNotFound)
				}
			}))
			defer srv.Close()
			c := &GitHub{apiBase: srv.URL}
			got, err := c.GetDefaultBranchHeadSHA(context.Background(), "o", "r")
			if tc.wantErr {
				if err == nil {
					t.Fatalf("want error, got sha %q", got)
				}
				return
			}
			if err != nil {
				t.Fatalf("GetDefaultBranchHeadSHA: %v", err)
			}
			if got != tc.wantSHA {
				t.Fatalf("sha = %q, want %q", got, tc.wantSHA)
			}
		})
	}
}

func TestGitLabGetDefaultBranchHeadSHA(t *testing.T) {
	const proj = "grp/proj"
	tests := []struct {
		name      string
		defBranch string
		commitID  string
		wantSHA   string
	}{
		{name: "resolves default branch", defBranch: "main", commitID: "sha-main", wantSHA: "sha-main"},
		{name: "custom default branch", defBranch: "develop", commitID: "sha-dev", wantSHA: "sha-dev"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			esc := url.PathEscape(proj)
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch {
				case r.URL.Path == "/projects/"+esc:
					_ = json.NewEncoder(w).Encode(map[string]any{"default_branch": tc.defBranch})
				case strings.HasPrefix(r.URL.Path, "/projects/"+esc+"/repository/branches/"):
					_ = json.NewEncoder(w).Encode(map[string]any{"commit": map[string]any{"id": tc.commitID}})
				default:
					t.Errorf("unexpected path %q", r.URL.Path)
					w.WriteHeader(http.StatusNotFound)
				}
			}))
			defer srv.Close()
			c := &GitLab{apiBase: srv.URL}
			got, err := c.GetDefaultBranchHeadSHA(context.Background(), proj, "")
			if err != nil {
				t.Fatalf("GetDefaultBranchHeadSHA: %v", err)
			}
			if got != tc.wantSHA {
				t.Fatalf("sha = %q, want %q", got, tc.wantSHA)
			}
		})
	}
}
```

2. Run-it-fails: `mise exec -- go test ./internal/scm/... -run GetDefaultBranchHeadSHA -count=1`
   Expect: `c.GetDefaultBranchHeadSHA undefined`.

3. Minimal impl. Interface (`scm.go`, inside SCMReader after `GetIssue`):

```go
	// GetDefaultBranchHeadSHA resolves the default branch HEAD commit sha.
	// For GitLab owner carries the full project path; repo is unused. Paired
	// with GetCommitCIStatus to report per-repo main-branch CI health.
	GetDefaultBranchHeadSHA(ctx context.Context, owner, repo string) (string, error)
```

`github.go`:

```go
// GetDefaultBranchHeadSHA resolves the default branch HEAD commit sha for owner/repo.
func (c *GitHub) GetDefaultBranchHeadSHA(ctx context.Context, owner, repo string) (string, error) {
	var meta struct {
		DefaultBranch string `json:"default_branch"`
	}
	if err := ghDo(ctx, c.base(), http.MethodGet, fmt.Sprintf("/repos/%s/%s", owner, repo), c.token, nil, &meta); err != nil {
		return "", fmt.Errorf("github: get repo meta %s/%s: %w", owner, repo, err)
	}
	if meta.DefaultBranch == "" {
		return "", fmt.Errorf("github: empty default_branch for %s/%s", owner, repo)
	}
	var commit struct {
		SHA string `json:"sha"`
	}
	if err := ghDo(ctx, c.base(), http.MethodGet, fmt.Sprintf("/repos/%s/%s/commits/%s", owner, repo, meta.DefaultBranch), c.token, nil, &commit); err != nil {
		return "", fmt.Errorf("github: get default branch head %s/%s@%s: %w", owner, repo, meta.DefaultBranch, err)
	}
	return commit.SHA, nil
}
```

`gitlab.go` (add `"fmt"`, `"net/url"` already imported):

```go
// GetDefaultBranchHeadSHA resolves the default branch HEAD commit sha. owner
// carries the full project path; repo is unused (matches GetCommitCIStatus).
func (c *GitLab) GetDefaultBranchHeadSHA(ctx context.Context, owner, _ /*repo*/ string) (string, error) {
	esc := url.PathEscape(owner)
	var meta struct {
		DefaultBranch string `json:"default_branch"`
	}
	if err := glDo(ctx, c.base(), http.MethodGet, "/projects/"+esc, c.token, nil, &meta); err != nil {
		return "", fmt.Errorf("gitlab: get project meta %s: %w", owner, err)
	}
	if meta.DefaultBranch == "" {
		return "", fmt.Errorf("gitlab: empty default_branch for %s", owner)
	}
	var branch struct {
		Commit struct {
			ID string `json:"id"`
		} `json:"commit"`
	}
	path := "/projects/" + esc + "/repository/branches/" + url.PathEscape(meta.DefaultBranch)
	if err := glDo(ctx, c.base(), http.MethodGet, path, c.token, nil, &branch); err != nil {
		return "", fmt.Errorf("gitlab: get default branch head %s@%s: %w", owner, meta.DefaultBranch, err)
	}
	return branch.Commit.ID, nil
}
```

Each test fake gets (signature varies by fake's named/anon params - match its
neighbours' style, e.g. for `fakeReader`):

```go
func (f *fakeReader) GetDefaultBranchHeadSHA(context.Context, string, string) (string, error) {
	return "", nil
}
```

4. Run-it-passes: `mise exec -- go test ./internal/scm/... ./internal/controller/... ./internal/restapi/... -count=1` (green; fakes compile).

5. Commit: `feat(scm): add GetDefaultBranchHeadSHA to SCMReader (+ github, gitlab, fakes)`

---

## Task 3 - WS5: TaskSource.Title capture (webhook + scan)

**Files**
- Modify `internal/webhook/server.go:415` and `:610` - add `Title: ev.Title`.
- Modify `internal/controller/projectscan.go:192` (candidate) - add `title string`.
- Modify `internal/controller/projectscan.go:281` (createScanTask) - set `Title`.
- Modify the candidate builders that feed createScanTask (issueScan/mrScan) to
  set `title` from `IssueRef.Title` / `PRRef` body-first-line (search
  `candidate{` constructions in `projectscan.go`).
- Test: `internal/webhook/source_title_test.go` (new) +
  `internal/controller/scan_source_title_test.go` (new).

**Interfaces**
- Consumes: `scm.WebhookEvent.Title` (already populated), `scm.IssueRef.Title`.
- Produces: `Task.Spec.Source.Title` populated for webhook-born and scan-born tasks.

**Steps**

1. Write failing test (webhook). Mirror an existing `server_test.go` setup that
   asserts on the enqueued payload's `Source`; add a case asserting
   `payload.Source.Title == ev.Title`. For the scan path, a table test on the
   candidate-built `TaskSource`:

```go
func TestCreateScanTask_SetsSourceTitle(t *testing.T) {
	tests := []struct {
		name      string
		cand      candidate
		wantTitle string
	}{
		{name: "issue title threaded", cand: candidate{repo: "o/r", number: 7, title: "Fix flaky CI on push"}, wantTitle: "Fix flaky CI on push"},
		{name: "empty title stays empty", cand: candidate{repo: "o/r", number: 8}, wantTitle: ""},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			src := scanSourceFor(provider, tc.cand) // helper extracted in impl
			if src.Title != tc.wantTitle {
				t.Fatalf("Source.Title = %q, want %q", src.Title, tc.wantTitle)
			}
		})
	}
}
```

2. Run-it-fails: `mise exec -- go test ./internal/controller/... -run SourceTitle -count=1`
   Expect: `candidate has no field title` / `scanSourceFor undefined`.

3. Minimal impl:
   - `candidate` struct: add `title string`.
   - In `createScanTask`, set `src.Title = srcCand.title` after building `src`
     (keep KISS - no helper extraction needed unless the test wants one; if the
     test references `scanSourceFor`, extract the 7-line `src :=` block into
     `func scanSourceFor(provider string, c candidate) *tatarav1alpha1.TaskSource`).
   - In the issueScan candidate builder, set `title: iss.Title`. In the mrScan
     builder, set `title: firstLine(pr.Body)` (the PR has no title field on
     `PRRef`; use the body's first line, capped - reuse `firstLine`). Confirm by
     reading the candidate constructions.
   - In `webhook/server.go` both `Source: &tatarav1.TaskSource{...}` blocks, add
     `Title: ev.Title,`.

4. Run-it-passes: `mise exec -- go test ./internal/controller/... ./internal/webhook/... -count=1` (green).

5. Commit: `feat(source): capture work-item Title on TaskSource (webhook + scan)`

---

## Task 4 - WS5: TaskBranch issue-numbered derivation

**Files**
- Modify `internal/agent/pod.go:295` (TaskBranch) + add `branchKind`, `slugifyTitle`.
- Test: `internal/agent/branch_test.go` (new).

**Interfaces**
- Produces:
  - `TaskBranch(t *tatarav1alpha1.Task) string` -> `tatara/<kind>-<Number>-<slug>`
    when `t.Spec.Source.Number > 0`, else `tatara/task-<t.Name>`.
  - `branchKind(t *tatarav1alpha1.Task) string` -> `fix|feat|chore`.
  - `slugifyTitle(s string) string` -> lowercase alnum+hyphen, trimmed, ~40 cap.
- Consumes: `t.Spec.Source.Number`, `t.Spec.Source.Title`, `t.Spec.Source.Labels`
  (Labels not on TaskSource - derive kind from `t.Spec.Kind` + Source.IsPR; see impl).

**Steps**

1. Write failing test `internal/agent/branch_test.go`:

```go
package agent

import (
	"strings"
	"testing"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
)

func taskWith(name, kind string, num int, title string, isPR bool) *tatarav1alpha1.Task {
	t := &tatarav1alpha1.Task{}
	t.Name = name
	t.Spec.Kind = kind
	if num > 0 || title != "" {
		t.Spec.Source = &tatarav1alpha1.TaskSource{Number: num, Title: title, IsPR: isPR}
	}
	return t
}

func TestTaskBranch(t *testing.T) {
	tests := []struct {
		name string
		task *tatarav1alpha1.Task
		want string
	}{
		{"issue numbered fix", taskWith("scan-abc", "issueLifecycle", 42, "Fix flaky CI on push events", false), "tatara/fix-42-fix-flaky-ci-on-push-events"},
		{"pr review is chore", taskWith("scan-def", "review", 7, "Review: add metrics", true), "tatara/chore-7-review-add-metrics"},
		{"no number falls back", taskWith("brainstorm-xyz", "brainstorm", 0, "", false), "tatara/task-brainstorm-xyz"},
		{"empty title still numbered", taskWith("scan-ghi", "issueLifecycle", 9, "", false), "tatara/fix-9"},
		{"long title truncated", taskWith("scan-jkl", "issueLifecycle", 1, strings.Repeat("very long word ", 10), false), ""},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := TaskBranch(tc.task)
			if tc.want != "" && got != tc.want {
				t.Fatalf("TaskBranch = %q, want %q", got, tc.want)
			}
			if len(got) > 63 {
				t.Fatalf("branch %q exceeds 63 chars", got)
			}
			if !strings.HasPrefix(got, "tatara/") {
				t.Fatalf("branch %q missing tatara/ prefix", got)
			}
		})
	}
}

func TestSlugifyTitle(t *testing.T) {
	tests := []struct{ in, want string }{
		{"Fix flaky CI on push events", "fix-flaky-ci-on-push-events"},
		{"  Trim   Spaces  ", "trim-spaces"},
		{"Special!@#chars$%^here", "special-chars-here"},
		{"", ""},
		{strings.Repeat("a", 60), strings.Repeat("a", 40)},
	}
	for _, tc := range tests {
		t.Run(tc.in, func(t *testing.T) {
			if got := slugifyTitle(tc.in); got != tc.want {
				t.Fatalf("slugifyTitle(%q) = %q, want %q", tc.in, got, tc.want)
			}
		})
	}
}

func TestBranchKind(t *testing.T) {
	tests := []struct {
		name string
		task *tatarav1alpha1.Task
		want string
	}{
		{"issueLifecycle is fix", taskWith("a", "issueLifecycle", 1, "x", false), "fix"},
		{"implement is feat", taskWith("b", "implement", 1, "x", false), "feat"},
		{"review is chore", taskWith("c", "review", 1, "x", true), "chore"},
		{"brainstorm is chore", taskWith("d", "brainstorm", 0, "", false), "chore"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := branchKind(tc.task); got != tc.want {
				t.Fatalf("branchKind = %q, want %q", got, tc.want)
			}
		})
	}
}
```

2. Run-it-fails: `mise exec -- go test ./internal/agent/... -run "TaskBranch|Slugify|BranchKind" -count=1`
   Expect: `slugifyTitle undefined`, `branchKind undefined`, and the issue-numbered
   `TaskBranch` cases fail (still returns `tatara/task-<name>`).

3. Minimal impl (`pod.go`, replacing the current `TaskBranch`):

```go
// slugifyTitle lowercases s, collapses every run of non-[a-z0-9] into a single
// '-', trims leading/trailing '-', and caps at 40 chars (trimmed again so a cut
// never leaves a trailing '-').
func slugifyTitle(s string) string {
	s = strings.ToLower(s)
	var b strings.Builder
	prevDash := false
	for _, r := range s {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') {
			b.WriteRune(r)
			prevDash = false
		} else if !prevDash {
			b.WriteByte('-')
			prevDash = true
		}
	}
	out := strings.Trim(b.String(), "-")
	if len(out) > 40 {
		out = strings.Trim(out[:40], "-")
	}
	return out
}

// branchKind maps a Task to a conventional branch prefix.
func branchKind(t *tatarav1alpha1.Task) string {
	switch t.Spec.Kind {
	case "issueLifecycle", "incident":
		return "fix"
	case "implement":
		return "feat"
	default: // review, brainstorm, healthCheck, selfImprove, triageIssue
		return "chore"
	}
}

// TaskBranch is the deterministic work branch all of the operator write-back,
// the turn prompts, and the wrapper agree on. When the Task carries an issue/PR
// number it is tatara/<kind>-<number>-<slug>; otherwise tatara/task-<task-name>.
func TaskBranch(t *tatarav1alpha1.Task) string {
	if t.Spec.Source != nil && t.Spec.Source.Number > 0 {
		base := fmt.Sprintf("tatara/%s-%d", branchKind(t), t.Spec.Source.Number)
		if slug := slugifyTitle(t.Spec.Source.Title); slug != "" {
			base += "-" + slug
		}
		if len(base) > 63 {
			base = strings.Trim(base[:63], "-")
		}
		return base
	}
	return "tatara/task-" + t.Name
}
```

4. Run-it-passes: `mise exec -- go test ./internal/agent/... -count=1` (green).
   Note: writeback's `taskBranch` and `BuildPod`'s `TASK_BRANCH` already call
   `agent.TaskBranch`, so both producers stay in sync automatically. Run
   `mise exec -- go test ./internal/controller/... -count=1` to confirm no
   existing test pinned the old `tatara/task-<name>` value for a numbered task;
   fix any such fixture (boy-scout) to the new format.

5. Commit: `feat(branch): derive issue-numbered work branches with kind + slug`

---

## Task 5 - WS2: EFFORT env in BuildPod

**Files**
- Modify `internal/agent/pod.go:312` (BuildPod env list) - add EFFORT.
- Test: `internal/agent/pod_effort_test.go` (new).

**Interfaces**
- Consumes: `project.Spec.Agent.Effort`.
- Produces: env var `EFFORT` on the wrapper pod (alongside MODEL, PERMISSION_MODE).

**Steps**

1. Write failing test `internal/agent/pod_effort_test.go`:

```go
package agent

import (
	"testing"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	corev1 "k8s.io/api/core/v1"
)

func envValue(env []corev1.EnvVar, name string) (string, bool) {
	for _, e := range env {
		if e.Name == name {
			return e.Value, true
		}
	}
	return "", false
}

func TestBuildPod_SetsEffortEnv(t *testing.T) {
	tests := []struct {
		name   string
		effort string
		want   string
	}{
		{"xhigh default", "xhigh", "xhigh"},
		{"max", "max", "max"},
		{"empty still emitted", "", ""},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			proj := &tatarav1alpha1.Project{}
			proj.Name = "p"
			proj.Spec.Agent.Effort = tc.effort
			task := &tatarav1alpha1.Task{}
			task.Name = "t"
			pod := BuildPod(proj, nil, task, nil, "http://mem", PodConfig{})
			got, ok := envValue(pod.Spec.Containers[0].Env, "EFFORT")
			if !ok {
				t.Fatalf("EFFORT env not set")
			}
			if got != tc.want {
				t.Fatalf("EFFORT = %q, want %q", got, tc.want)
			}
		})
	}
}
```

2. Run-it-fails: `mise exec -- go test ./internal/agent/... -run Effort -count=1`
   Expect: `EFFORT env not set`.

3. Minimal impl. In `BuildPod`'s env list, immediately after the MODEL entry
   (`pod.go:313`):

```go
		{Name: "MODEL", Value: project.Spec.Agent.Model},
		{Name: "EFFORT", Value: project.Spec.Agent.Effort},
		{Name: "PERMISSION_MODE", Value: project.Spec.Agent.PermissionMode},
```

4. Run-it-passes: `mise exec -- go test ./internal/agent/... -count=1` (green).

5. Commit: `feat(pod): pass agent Effort as EFFORT env to the wrapper`

---

## Task 6 - WS4: weakTitle validator

**Files**
- Modify `internal/controller/writeback.go` - add `weakTitle`.
- Test: `internal/controller/weaktitle_test.go` (new).

**Interfaces**
- Produces: `weakTitle(s string) (bool, string)` -> (isWeak, guidanceMessage).
  Weak when: empty, `< 12` chars, `<= 2` words, or a denylisted bare token.

**Steps**

1. Write failing test `internal/controller/weaktitle_test.go`:

```go
package controller

import "testing"

func TestWeakTitle(t *testing.T) {
	tests := []struct {
		name     string
		in       string
		wantWeak bool
	}{
		{"empty", "", true},
		{"bare go", "Go", true},
		{"bare update", "update", true},
		{"too short", "fix bug", true},
		{"two words", "fix everything", true},
		{"denylist wip", "wip", true},
		{"good conventional", "fix(scan): dedup brainstorm proposals by systemic label", false},
		{"good plain", "Add main-branch CI health to the brainstorm survey", false},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			gotWeak, guidance := weakTitle(tc.in)
			if gotWeak != tc.wantWeak {
				t.Fatalf("weakTitle(%q) weak = %v, want %v", tc.in, gotWeak, tc.wantWeak)
			}
			if gotWeak && guidance == "" {
				t.Fatalf("weakTitle(%q) weak but empty guidance", tc.in)
			}
			if !gotWeak && guidance != "" {
				t.Fatalf("weakTitle(%q) strong but non-empty guidance %q", tc.in, guidance)
			}
		})
	}
}
```

2. Run-it-fails: `mise exec -- go test ./internal/controller/... -run WeakTitle -count=1`
   Expect: `weakTitle undefined`.

3. Minimal impl (`writeback.go`):

```go
// weakTitleDenylist are bare tokens that, alone, make a useless title.
var weakTitleDenylist = map[string]bool{
	"go": true, "update": true, "fix": true, "change": true, "wip": true,
	"misc": true, "chore": true, "stuff": true, "changes": true, "updates": true,
	"python": true, "golang": true, "helm": true, "docker": true, "ci": true,
}

// weakTitle reports whether a proposed issue/PR title is too weak to emit and,
// when weak, a one-line guidance message the agent can act on that same turn.
func weakTitle(s string) (bool, string) {
	t := strings.TrimSpace(s)
	if t == "" {
		return true, "title is empty: provide a descriptive, conventional title (e.g. 'fix(scope): concrete change')"
	}
	if len(t) < 12 {
		return true, fmt.Sprintf("title %q is too short (<12 chars): describe the concrete change", t)
	}
	if len(strings.Fields(t)) <= 2 {
		return true, fmt.Sprintf("title %q has too few words (<=2): name the component and the change", t)
	}
	if weakTitleDenylist[strings.ToLower(t)] {
		return true, fmt.Sprintf("title %q is a bare token: describe the concrete change instead", t)
	}
	return false, ""
}
```

4. Run-it-passes: `mise exec -- go test ./internal/controller/... -run WeakTitle -count=1` (green).

5. Commit: `feat(writeback): add weakTitle title-quality validator`

---

## Task 7 - WS4: reject weak titles in restapi handlers + propose_issue systemicId

**Files**
- Modify `internal/restapi/handlers.go:367` (proposeIssueReq) - add `SystemicID`.
- Modify `internal/restapi/handlers.go:374` (proposeIssue) - weakTitle gate +
  thread SystemicID onto `ProposedIssue`.
- Modify `internal/restapi/handlers.go:966` (changeSummaryReq is fine) + `:974`
  (changeSummary) - weakTitle gate on `PRTitle` when non-empty.
- `weakTitle` lives in package `controller`; restapi cannot import it
  (controller imports restapi-adjacent? verify no cycle). DECISION: move
  `weakTitle` + denylist to a small shared spot. Simplest no-cycle option:
  duplicate is forbidden (DRY). Put `weakTitle` in `api/v1alpha1` (no deps) OR a
  new `internal/titlecheck` package. Use `internal/titlecheck` (one file,
  importable by both controller and restapi). Adjust Task 6's home accordingly:
  implement `weakTitle` in `internal/titlecheck/titlecheck.go` exported as
  `titlecheck.Weak(s) (bool, string)`; controller + restapi both call it.
- Test: `internal/restapi/propose_systemic_weak_test.go` (new).

**Interfaces**
- Consumes: `titlecheck.Weak(string) (bool, string)`.
- Produces: `proposeIssueReq.SystemicID string json:"systemicId,omitempty"`
  threaded to `task.Spec.ProposedIssue.SystemicID`; 4xx + guidance on weak title.

> Sequencing note: do Task 6 as `internal/titlecheck` from the start
> (`Weak(s string) (bool, string)`), and have controller call
> `titlecheck.Weak`. This avoids a rename here. The locked external name
> `weakTitle` is satisfied by a thin controller-local
> `func weakTitle(s string) (bool, string) { return titlecheck.Weak(s) }` if any
> other repo's plan references `weakTitle` by name; otherwise call
> `titlecheck.Weak` directly.

**Steps**

1. Write failing test `internal/restapi/propose_systemic_weak_test.go` (table,
   driving the real handler via httptest against a fake controller-runtime
   client - mirror the existing `issue_comment_test.go` harness):

```go
func TestProposeIssue_WeakTitleRejected(t *testing.T) {
	tests := []struct {
		name       string
		title      string
		wantStatus int
	}{
		{"weak bare go rejected", "Go", http.StatusBadRequest},
		{"empty rejected", "", http.StatusBadRequest},
		{"good accepted", "Add systemic correlation labels to proposals", http.StatusCreated},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			// ... build server with fake client containing project p + repo r ...
			body := fmt.Sprintf(`{"repositoryRef":"r","title":%q,"body":"a real body here","kind":"bug"}`, tc.title)
			rec := doRequest(t, srv, http.MethodPost, "/projects/p/issues", body)
			if rec.Code != tc.wantStatus {
				t.Fatalf("status = %d, want %d (body %s)", rec.Code, tc.wantStatus, rec.Body.String())
			}
		})
	}
}

func TestProposeIssue_SystemicIDThreaded(t *testing.T) {
	// POST with "systemicId":"grp1"; assert the created Task's
	// Spec.ProposedIssue.SystemicID == "grp1" via the fake client Get.
}
```

2. Run-it-fails: `mise exec -- go test ./internal/restapi/... -run "WeakTitle|SystemicID" -count=1`
   Expect: weak titles return 201 (no gate yet); SystemicID unset.

3. Minimal impl:
   - `proposeIssueReq`: add `SystemicID string json:"systemicId,omitempty"`.
   - In `proposeIssue`, after the existing required-field + kind switch, before
     building the Task:

```go
	if weak, guidance := titlecheck.Weak(req.Title); weak {
		writeError(w, http.StatusBadRequest, "weak title: "+guidance)
		return
	}
```

   - Thread SystemicID into the ProposedIssue literal:

```go
			ProposedIssue: &tatarav1alpha1.ProposedIssueSpec{
				RepositoryRef: req.RepositoryRef, Title: req.Title, Body: req.Body,
				Kind: req.Kind, SystemicID: req.SystemicID,
			},
```

   - In `changeSummary`, gate `PRTitle` only when non-empty (an absent PRTitle is
     legal - the derive-fallback handles it; only a present-but-weak title is
     rejected so the agent retries):

```go
	if req.PRTitle != "" {
		if weak, guidance := titlecheck.Weak(req.PRTitle); weak {
			writeError(w, http.StatusBadRequest, "weak pr title: "+guidance)
			return
		}
	}
```

4. Run-it-passes: `mise exec -- go test ./internal/restapi/... -count=1` (green).

5. Commit: `feat(restapi): reject weak titles + thread systemicId on propose_issue`

---

## Task 8 - WS3: systemic label + footer on createProposal; cap grouping

**Files**
- Modify `internal/controller/writeback.go:472` (createProposal) - systemic label
  + body footer when `SystemicID != ""`.
- Modify `internal/controller/projectscan.go` (proposalBacklogCount) - group open
  proposals by `tatara/systemic-<id>` label (one group counts as 1).
- Test: `internal/controller/systemic_proposal_test.go` (new) +
  `internal/controller/proposal_backlog_systemic_test.go` (new).

**Interfaces**
- Produces:
  - Created issue carries label `tatara/systemic-<id>` + footer
    `Part of systemic improvement <id> spanning: <repo list>` when SystemicID set.
  - `proposalBacklogCount(...)` counts each distinct `tatara/systemic-<id>` group
    once and each standalone proposal once.
- Locked label string: `tatara/systemic-<id>`.

**Steps**

1. Write failing tests. For cap grouping (pure, table-driven against
   `proposalBacklogCount` - confirm its exact current signature first; it takes
   `(iss []scm.IssueRef, brainstormingLabel, legacyIdea string)`):

```go
func TestProposalBacklogCount_GroupsSystemic(t *testing.T) {
	const bs = "tatara-brainstorming"
	mk := func(n int, labels ...string) scm.IssueRef {
		return scm.IssueRef{Repo: "o/r", Number: n, Labels: append([]string{bs}, labels...)}
	}
	tests := []struct {
		name string
		iss  []scm.IssueRef
		want int
	}{
		{"three standalone count three", []scm.IssueRef{mk(1), mk(2), mk(3)}, 3},
		{"systemic group counts one", []scm.IssueRef{mk(1, "tatara/systemic-abc"), mk(2, "tatara/systemic-abc"), mk(3, "tatara/systemic-abc")}, 1},
		{"mixed: group + standalone", []scm.IssueRef{mk(1, "tatara/systemic-abc"), mk(2, "tatara/systemic-abc"), mk(3)}, 2},
		{"two distinct groups", []scm.IssueRef{mk(1, "tatara/systemic-abc"), mk(2, "tatara/systemic-def")}, 2},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := proposalBacklogCount(tc.iss, bs, "tatara-idea"); got != tc.want {
				t.Fatalf("proposalBacklogCount = %d, want %d", got, tc.want)
			}
		})
	}
}
```

For the label+footer, drive `createProposal` with a fake SCM writer capturing the
`IssueReq` and assert `Labels` contains `tatara/systemic-grp1` and `Body`
contains the footer (mirror `proposal_dedup_test.go`'s writer fake).

2. Run-it-fails: `mise exec -- go test ./internal/controller/... -run "Systemic" -count=1`
   Expect: standalone counts pass but grouped cases return 3 (no grouping);
   label/footer absent.

3. Minimal impl:
   - In `proposalBacklogCount`, after filtering to proposal-labelled issues,
     collapse by systemic label:

```go
	const systemicPrefix = "tatara/systemic-"
	groups := map[string]bool{}
	standalone := 0
	for _, iss := range proposals { // proposals = the already-filtered set
		sid := ""
		for _, l := range iss.Labels {
			if strings.HasPrefix(l, systemicPrefix) {
				sid = l
				break
			}
		}
		if sid != "" {
			groups[sid] = true
		} else {
			standalone++
		}
	}
	return standalone + len(groups)
```

   (Refactor `proposalBacklogCount` so the existing per-issue filter feeds this
   collapse; keep the function pure.)
   - In `createProposal`, before `CreateIssue`:

```go
	labels := []string{label}
	body := task.Spec.ProposedIssue.Body
	if sid := task.Spec.ProposedIssue.SystemicID; sid != "" {
		labels = append(labels, "tatara/systemic-"+sid)
		body += fmt.Sprintf("\n\nPart of systemic improvement %s spanning: %s", sid, systemicRepoList(proj))
	}
	body += "\n\n" + tataraAuthoredMarker
```

   Pass `Labels: labels`. `systemicRepoList(proj)` returns a comma-joined sorted
   repo-slug list (the project's repos, known at propose time - reuse
   `r.projectRepos` + `scm.OwnerRepo`; if listing fails, degrade to the single
   `repo` slug). Keep it a small helper in `writeback.go`.

4. Run-it-passes: `mise exec -- go test ./internal/controller/... -count=1` (green).

5. Commit: `feat(proposal): stamp systemic label+footer and group it 1-per-cap`

---

## Task 9 - WS4: derive no-agent PR-title fallback from Source.Title

**Files**
- Modify `internal/controller/writeback.go:119` (writeBackOpenChange title init).
- Test: `internal/controller/pr_title_derive_test.go` (new).

**Interfaces**
- Consumes: `task.Spec.Source.Title`, `task.Status.ChangeSummary.PRTitle`,
  `branchKind`-style mapping, `titlecheck.Weak`.
- Produces: when ChangeSummary PRTitle absent or weak, PR title is
  `<kind>(<scope>): <Source.Title>` (kind from Task kind, scope from primary repo
  short name), never `firstLine(task.Spec.Goal)` and never a weak title.

**Steps**

1. Write failing test `internal/controller/pr_title_derive_test.go` (unit on an
   extracted pure helper `derivePRTitle(task, primaryRepoShortName string) string`
   so it is testable without a live SCM):

```go
func TestDerivePRTitle(t *testing.T) {
	mk := func(kind, srcTitle, csTitle, goal string) *tatarav1alpha1.Task {
		ta := &tatarav1alpha1.Task{}
		ta.Spec.Kind = kind
		ta.Spec.Goal = goal
		if srcTitle != "" {
			ta.Spec.Source = &tatarav1alpha1.TaskSource{Title: srcTitle}
		}
		if csTitle != "" {
			ta.Status.ChangeSummary = &tatarav1alpha1.ChangeSummary{PRTitle: csTitle}
		}
		return ta
	}
	tests := []struct {
		name string
		task *tatarav1alpha1.Task
		want string
	}{
		{"strong changesummary wins", mk("issueLifecycle", "Fix flaky CI", "fix(ci): retry flaky push checks", "body line"), "fix(ci): retry flaky push checks"},
		{"weak changesummary derives", mk("issueLifecycle", "Add main-branch CI health survey", "Go", "body line"), "fix(repo): Add main-branch CI health survey"},
		{"absent changesummary derives", mk("implement", "Thread systemicId through propose_issue", "", "issue body first line"), "feat(repo): Thread systemicId through propose_issue"},
		{"no source title falls to goal-ish but not weak", mk("issueLifecycle", "", "", "Make the brainstorm survey live state"), "fix(repo): Make the brainstorm survey live state"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := derivePRTitle(tc.task, "repo"); got != tc.want {
				t.Fatalf("derivePRTitle = %q, want %q", got, tc.want)
			}
		})
	}
}
```

2. Run-it-fails: `mise exec -- go test ./internal/controller/... -run DerivePRTitle -count=1`
   Expect: `derivePRTitle undefined`.

3. Minimal impl. Add `derivePRTitle` to `writeback.go`:

```go
// derivePRTitle returns the PR/MR title for a write-back. A strong
// ChangeSummary.PRTitle wins; otherwise it derives a conventional title from the
// captured work-item title (Source.Title), falling back to the goal first line.
// It never returns a weak title.
func derivePRTitle(task *tatarav1alpha1.Task, scope string) string {
	if cs := task.Status.ChangeSummary; cs != nil && cs.PRTitle != "" {
		if weak, _ := titlecheck.Weak(cs.PRTitle); !weak {
			return cs.PRTitle
		}
	}
	subject := ""
	if task.Spec.Source != nil {
		subject = strings.TrimSpace(task.Spec.Source.Title)
	}
	if subject == "" {
		subject = firstLine(task.Spec.Goal)
	}
	kind := "feat"
	switch task.Spec.Kind {
	case "issueLifecycle", "incident":
		kind = "fix"
	}
	return fmt.Sprintf("%s(%s): %s", kind, scope, subject)
}
```

   Then in `writeBackOpenChange`, replace the `title := firstLine(task.Spec.Goal)`
   + ChangeSummary override block (`writeback.go:119-127`) with a single call:

```go
	title := derivePRTitle(task, primaryRepo.Name)
	baseBody := writeBackBody(task)
	if cs := task.Status.ChangeSummary; cs != nil {
		deliveredBody := cs.PRBody
		if cs.DeliveredScope != "" {
			deliveredBody += "\n\n## Delivered\n" + cs.DeliveredScope
		}
		deliveredBody += "\n\n" + tataraAuthoredMarker
		baseBody = deliveredBody
	}
```

   (Body handling unchanged; only title sourcing moves into `derivePRTitle`.)

4. Run-it-passes: `mise exec -- go test ./internal/controller/... -count=1` (green).
   Check existing writeback tests that asserted the old `firstLine(Goal)` title and
   update them to the derived format (boy-scout).

5. Commit: `feat(writeback): derive conventional PR title from Source.Title fallback`

---

## Task 10 - WS1: buildRepoStateContext (ISSUES / OPEN MRs / MAIN HEALTH)

**Files**
- Modify `internal/controller/projectscan.go:1113` - rename `buildIssuesContext`
  -> `buildRepoStateContext`, extend signature to take the per-repo PR map +
  reader for MR/main reads.
- Modify call sites `projectscan.go:916` (brainstorm) and `:1016` (healthCheck) -
  gather open PRs (`ListOpenPRs`), bounded `GetPRState.CIStatus` (first 20 by
  recency), and `GetDefaultBranchHeadSHA` + `GetCommitCIStatus` per repo (all
  non-fatal), then pass into the new builder.
- Test: `internal/controller/repo_state_context_test.go` (new).

**Interfaces**
- Produces:
  `func (r *ProjectReconciler) buildRepoStateContext(ctx, proj, reader scm.SCMReader, issuesBySlug map[string][]scm.IssueRef, prsBySlug map[string][]scm.PRRef, prCIBySlug map[string]map[int]string, mainCIBySlug map[string]string, repos []Repository) string`
  emitting three blocks:
  - `ISSUES:` lines `slug#N [labels] title` (60 cap, `[bot-engaged]` preserved).
  - `OPEN MRs:` lines `slug!N [ci:<status>] title` (gitlab `!`, github `#`).
  - `MAIN HEALTH:` one line per repo `slug main CI: <status>`.
- Consumes: `GetDefaultBranchHeadSHA`, `GetCommitCIStatus`, `GetPRState`,
  `ListOpenPRs`.

**Steps**

1. Write failing test `internal/controller/repo_state_context_test.go` (table,
   pure - call the builder with prepared maps; no live SCM):

```go
func TestBuildRepoStateContext_Blocks(t *testing.T) {
	r := &ProjectReconciler{ /* Metrics etc. as existing tests set up */ }
	proj := &tatarav1alpha1.Project{}
	proj.Spec.Scm = &tatarav1alpha1.ScmSpec{Provider: "github", BotLogin: "bot"}
	repos := []tatarav1alpha1.Repository{{Spec: tatarav1alpha1.RepositorySpec{URL: "https://github.com/o/r"}}}
	issues := map[string][]scm.IssueRef{"o/r": {{Repo: "o/r", Number: 1, Title: "an open issue", Labels: []string{"bug"}}}}
	prs := map[string][]scm.PRRef{"o/r": {{Repo: "o/r", Number: 9, Body: "Add metrics"}}}
	prCI := map[string]map[int]string{"o/r": {9: "failure"}}
	mainCI := map[string]string{"o/r": "success"}
	// reader is a fake whose botCommentedOnIssue path returns false.
	got := r.buildRepoStateContext(context.Background(), proj, fakeRdr{}, issues, prs, prCI, mainCI, repos)
	for _, want := range []string{
		"ISSUES:", "o/r#1 [bug] an open issue",
		"OPEN MRs:", "o/r#9 [ci:failure]",
		"MAIN HEALTH:", "o/r main CI: success",
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("context missing %q\n---\n%s", want, got)
		}
	}
}
```

Add cases: gitlab provider -> `o/r!9`; missing mainCI entry -> `main CI: unknown`;
issues cap at 60 -> `(+N more omitted)`.

2. Run-it-fails: `mise exec -- go test ./internal/controller/... -run RepoStateContext -count=1`
   Expect: `buildRepoStateContext undefined`.

3. Minimal impl:
   - Rename the method; keep the existing ISSUES rendering (cap 60, `[bot-engaged]`)
     under an `ISSUES:` header.
   - Append an `OPEN MRs:` block iterating `prsBySlug` (cap e.g. 40 lines total),
     provider-correct separator via `proj.Spec.Scm.Provider == "gitlab"` -> `!`,
     CI from `prCIBySlug[slug][number]` (default `unknown`), title from
     `firstLine(pr.Body)` (PRRef has no Title).
   - Append a `MAIN HEALTH:` block: one `slug main CI: <status>` per repo
     (`mainCIBySlug[slug]`, default `unknown`).
   - In brainstorm() and healthCheck() call sites, build the new maps before the
     builder call, bounded + non-fatal (each failed read logs INFO and leaves
     that line degraded):

```go
	prsBySlug := map[string][]scm.PRRef{}
	prCIBySlug := map[string]map[int]string{}
	mainCIBySlug := map[string]string{}
	for i := range sortedRepos {
		slug := repoSlug(&sortedRepos[i])
		if slug == "" {
			continue
		}
		owner, name, err := scm.OwnerRepo(sortedRepos[i].Spec.URL)
		if err != nil {
			continue
		}
		if prs, perr := reader.ListOpenPRs(ctx, owner, name); perr == nil {
			prsBySlug[slug] = prs
			ci := map[int]string{}
			limit := 20
			for j, pr := range prs {
				if j >= limit {
					break
				}
				if st, serr := reader.GetPRState(ctx, sortedRepos[i].Spec.URL, "", pr.Number); serr == nil {
					ci[pr.Number] = st.CIStatus
				}
			}
			prCIBySlug[slug] = ci
		} else {
			l.Info("brainstorm: list open PRs failed (non-fatal)", "resource_id", proj.Name, "repo", sortedRepos[i].Name, "err", perr.Error())
		}
		if sha, serr := reader.GetDefaultBranchHeadSHA(ctx, owner, name); serr == nil && sha != "" {
			if st, cerr := reader.GetCommitCIStatus(ctx, owner, name, sha); cerr == nil {
				mainCIBySlug[slug] = st
			}
		} else if serr != nil {
			l.Info("brainstorm: main head sha failed (non-fatal)", "resource_id", proj.Name, "repo", sortedRepos[i].Name, "err", serr.Error())
		}
	}
```

   NOTE: `reader` is `scm.SCMReader`, which does NOT include `GetPRState`
   (that is on SCMWriter). Two options: (a) add `GetPRState` to the bounded read
   via the writer the reconciler already has, or (b) since `ListOpenPRs` returns
   `PRRef.HeadSHA`, read PR CI via `GetCommitCIStatus(owner, name, pr.HeadSHA)` -
   already on SCMReader, no GetPRState needed. PREFER (b): it reuses the reader
   interface, avoids a writer dependency, and HeadSHA is already populated by
   ListOpenPRs. Replace the `GetPRState` loop with
   `reader.GetCommitCIStatus(ctx, owner, name, pr.HeadSHA)`.
   (gitlab: pass `owner` = project path per GetCommitCIStatus contract; for the
   gitlab reader `owner` should be the full `owner/name` slug - confirm via
   `glProjectPath`; the slug `owner+"/"+name` is the project path.)

4. Run-it-passes: `mise exec -- go test ./internal/controller/... -count=1` (green).

5. Commit: `feat(scan): buildRepoStateContext with ISSUES/OPEN MRs/MAIN HEALTH blocks`

---

## Task 11 - WS1+WS2+WS3: goal rewrite (systemic mandate + orchestration + multi-issue)

**Files**
- Modify `internal/controller/projectscan.go:1039` (brainstormGoalProject) and
  `:1072` (healthCheckGoalProject) - new signature taking the rich context string,
  systemic mandate, orchestration instructions, multi-issue clause.
- Modify call sites at `:918`/`:1018` to pass the new context.
- Test: `internal/controller/goal_systemic_test.go` (new).

**Interfaces**
- Produces: `brainstormGoalProject(slugs []string, repoStateCtx string) string` and
  `healthCheckGoalProject(slugs []string, repoStateCtx string) string` whose output
  contains: the three-block context, an explicit SYSTEMIC mandate (prefer a
  pattern spanning >=2 repos / platform-wide gap / recurring debt), the
  orchestration instruction (max effort, one parallel subagent per repo, Workflow
  fan-out+synthesize), the multi-issue rule (one-repo -> one issue; systemic ->
  one propose_issue per affected repo, bounded <=6, shared agent-generated
  systemicId), and the preserved dedup-first + `[bot-engaged]` contract. The
  "exactly one action per run" clause is REMOVED.

**Steps**

1. Write failing test `internal/controller/goal_systemic_test.go`:

```go
func TestBrainstormGoalProject_SystemicMandate(t *testing.T) {
	goal := brainstormGoalProject([]string{"o/a", "o/b"}, "ISSUES:\no/a#1 [bug] x\nOPEN MRs:\no/a#2 [ci:failure] y\nMAIN HEALTH:\no/a main CI: failure")
	for _, want := range []string{
		"systemic", "subagent", "Workflow", "systemicId", "MAIN HEALTH:", "OPEN MRs:", "[bot-engaged]",
	} {
		if !strings.Contains(goal, want) {
			t.Fatalf("goal missing %q", want)
		}
	}
	if strings.Contains(goal, "Exactly one action per run") {
		t.Fatalf("stale single-action clause still present")
	}
}

func TestHealthCheckGoalProject_SystemicMandate(t *testing.T) {
	goal := healthCheckGoalProject([]string{"o/a"}, "ISSUES:\nMAIN HEALTH:\no/a main CI: success")
	for _, want := range []string{"systemic", "subagent", "tatara-health-check", "systemicId"} {
		if !strings.Contains(goal, want) {
			t.Fatalf("goal missing %q", want)
		}
	}
}
```

2. Run-it-fails: `mise exec -- go test ./internal/controller/... -run GoalProject_SystemicMandate -count=1`
   Expect: missing `systemic`/`subagent`/`systemicId`; stale single-action clause present.

3. Minimal impl: rewrite both goal builders. Keep the `tatara-deep-research` /
   `tatara-health-check` skill invocation and the dedup three-path block, but:
   - Replace the closing "Exactly one action per run - no exceptions." with the
     multi-issue rule.
   - Insert a SYSTEMIC mandate paragraph and ORCHESTRATION paragraph. Sketch:

```go
	return "Invoke the `tatara-deep-research` skill ... across ALL repositories: " + repoList + ". " +
		"Run at MAXIMUM reasoning effort. Decompose the survey: dispatch one parallel subagent per " +
		"repository (use the Agent/Workflow tools to fan out, then synthesize their findings into one " +
		"systemic conclusion). " +
		"\n\n" + repoStateBlock + "\n\n" +
		"SYSTEMIC MANDATE: prefer the single highest-leverage SYSTEMIC opportunity - a pattern spanning " +
		">=2 repositories, a platform-wide gap (e.g. a missing CI step everywhere), or recurring debt - " +
		"over a one-repo tweak. Survey the ISSUES, OPEN MRs, and MAIN HEALTH blocks above; manage the " +
		"backlog by linking/labelling/commenting related existing issues (never close issues you do not own).\n\n" +
		dedupBlock + // the existing three-path block, [bot-engaged] preserved
		"\n\nACTION RULE: a one-repo improvement emits exactly ONE propose_issue. A genuinely SYSTEMIC " +
		"improvement MAY emit one propose_issue per affected repository (bounded: at most 6), all sharing " +
		"a single `systemicId` string you generate. State which path and scope you chose before executing."
```

   where `repoStateBlock` wraps the passed `repoStateCtx` (or a "no repo state"
   note when empty) and `dedupBlock` is the existing path-1/2/3 text minus the
   final single-action sentence. Mirror the same for healthCheck with its skill +
   evidence wording.
   - Update call sites to pass the `buildRepoStateContext` result.

4. Run-it-passes: `mise exec -- go test ./internal/controller/... -count=1` (green).

5. Commit: `feat(goal): systemic mandate + orchestration + multi-issue brainstorm/healthcheck goals`

---

## Final integration (opus merge subagent)

1. Merge all Task branches; resolve fixture drift (old `tatara/task-<name>`
   branch assertions, old `firstLine(Goal)` title assertions, `buildIssuesContext`
   references -> `buildRepoStateContext`).
2. `mise exec -- make generate manifests fmt` (idempotent re-run; confirm clean).
3. `superpowers:verification-before-completion`:
   - `mise exec -- make test` (full, envtest-backed) -> all green.
   - `mise run lint` -> clean.
   - `mise run build` -> binary builds.
   - `git status` -> `zz_generated.deepcopy.go` + 3 CRD YAMLs reflect the new fields.
4. `superpowers:requesting-code-review`; fix critical/high; `pre-commit run --all-files`.
5. Merge worktree -> `main`; cleanup worktree.
6. Update operator `MEMORY.md` (systemic-label cap-grouping decision; reader-only
   PR CI via `HeadSHA` not GetPRState; `internal/titlecheck` shared package) and
   `ROADMAP.md` (mark WS1-5 operator done; deploy owed).
7. Deploy is OUT of this plan's scope but owed: after CI builds the operator
   image+chart on `main`, open a `tatara-helmfile` MR bumping BOTH the operator
   chart version AND the pinned `image.tag`
   (`tatara-operator-deploy-chart-version-and-image-tag`); the templated CRD apply
   path (`operator-crd-templating-helm-adoption-2026-06-20`) ships the new fields.

---

## Build order summary

```
Task 1  (types + CRD/deepcopy regen)          <- everything depends on this
Task 2  (GetDefaultBranchHeadSHA + 15 fakes)   <- Task 10 depends on this
Task 3  (TaskSource.Title capture)             <- Tasks 4, 9 depend on this
Task 4  (TaskBranch issue-numbered)            }
Task 5  (EFFORT env)                           } parallel after 1 (+3 for Task 4)
Task 6  (titlecheck.Weak / weakTitle)          <- Tasks 7, 9 depend on this
Task 7  (restapi weak-title reject + systemicId) after 1, 6
Task 8  (systemic label/footer + cap grouping) after 1
Task 9  (PR-title derive)                       after 3, 6
Task 10 (buildRepoStateContext)                 after 2
Task 11 (goal rewrite)                          after 10
```
