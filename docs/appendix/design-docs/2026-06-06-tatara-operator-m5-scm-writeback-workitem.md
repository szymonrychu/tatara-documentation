# tatara-operator M5 (SCM write-back + work-item -> Task) Implementation Plan

> For agentic workers: execute tasks top to bottom. Each task is one TDD cycle: write the failing test (full code), run it and SEE it fail, write the minimal implementation (full code), run it and SEE it pass, then commit. Do not skip the "see it fail" step. Do not batch tasks. Implementation subagents run sonnet (rule 7); plan/review/merge run opus. All work happens in a worktree off `main` (rule 10); never build or deploy from the worktree. Use the superpowers skills: `test-driven-development` per task, `requesting-code-review` before the final merge, `verification-before-completion` before claiming done.

**Goal:** Complete the three write-side stubs the earlier milestones deliberately left open. (1) Implement `scm.GitHub.OpenChange`/`Comment` against the GitHub REST API (`POST /repos/{owner}/{repo}/pulls`, `POST /repos/{owner}/{repo}/issues/{n}/comments`). (2) Implement `scm.GitLab.OpenChange`/`Comment` against the GitLab REST API (`POST /projects/{id}/merge_requests`, `POST /projects/{id}/issues/{iid}/notes`, with the project path URL-encoded). (3) Wire the TaskReconciler's terminal-phase hook to call `OpenChange` on Task success (recording `status.prURL`) and, when `Task.spec.source.issueRef` is set, `Comment` the result. (4) Complete the M2 work-item webhook stub: a labeled `issue`/`mr` event creates a `Task` CRD under the Project, pointing at the matching Repository, owner-ref'd appropriately. (5) Wire the real `scm` registry into `cmd/manager/main.go` for both the webhook server and the Task reconciler. After M5 the operator can run a work item end to end: webhook -> Task -> agent turns -> branch push -> PR/MR + issue comment.

**Architecture:** `internal/scm` keeps the pinned `Client` interface unchanged; M5 replaces the two `errNotImplemented` method bodies per provider with real REST calls built on a small shared `http.Client` (constant-time nothing here - these are authenticated writes, not HMAC). Each provider parses `repoURL`/`issueRef` into the path components its REST API needs: GitHub `owner/repo` + issue number from `owner/repo#123`; GitLab the URL-encoded project path + iid from `group/proj!iid`, and the project path for MR creation derived from the remote URL. A new `internal/scm.Registry` (or extension of the M2 `Select`) lets callers pick a `Client` by provider string (the reconciler knows the provider from `Repository`/`Project`, not from request headers). The TaskReconciler gains an injected `scm.Registry` (real in main, fake in test) and, in its terminal branch (the hook M4 left), calls `OpenChange` then conditionally `Comment`. The webhook server gains an injected `scm` factory only to keep symmetry; its work-item branch now creates a `Task` via the controller-runtime client instead of logging. HTTP-client plumbing mirrors `tatara-memory/internal/lightrag/http.go` (context-aware `http.NewRequestWithContext`, `%w`-wrapped build/do/decode errors, a typed `HTTPError{Status,Body,Path}` for >=400, `httptest` table tests faking the upstream). Reconciler tests use envtest with a fake `scm` implementation; the webhook work-item test uses `httptest` with a signed payload + fake controller-runtime client.

**Tech Stack:** Go 1.25.x (rule 1; pinned exact minor in `go.mod`), kubebuilder / controller-runtime (`sigs.k8s.io/controller-runtime/pkg/client`, `pkg/client/fake`, envtest via `sigs.k8s.io/controller-runtime/pkg/envtest`), `github.com/go-chi/chi/v5`, `github.com/prometheus/client_golang`, stdlib `net/http`, `net/url`, `encoding/json`, `log/slog`, `net/http/httptest`, `github.com/stretchr/testify/require`. No third-party GitHub/GitLab SDK (rule 2 KISS - two POST calls each do not justify a dependency). Errors wrapped with `%w` (mirror lightrag). Metrics from `internal/obs` (M0); config from `internal/config` (M0).

Assumptions (stated picks, rule: act + note):
- M0-M4 are merged on `main` before M5 starts. Specifically: `internal/scm/{scm,github,gitlab,registry}.go` exist with the pinned `Client` interface and real `DetectAndVerify` (M2); `internal/webhook/server.go` has the work-item handler stub from M2 Task 5 (`handleWorkItem` logging + metering, no Task created); `internal/controller/task_controller.go` (M4) has a TaskReconciler that drives the turn loop and reaches a terminal branch where the spec says "the operator opens a PR/MR ... and comments" - M4 left that as a hook (sets `status.phase=Succeeded`/`Failed`, leaves `status.prURL` empty and no comment). M5 reads the actual M4 symbol names before editing (Task 3 PRECONDITION).
- `api/v1alpha1` field names per the spec/pin set: `Task.Spec.ProjectRef`, `Task.Spec.RepositoryRef`, `Task.Spec.Goal`, `Task.Spec.Source` (a struct `TaskSource{Provider, IssueRef, URL string}`), `Task.Spec.MaxTurns`, `Task.Status.PRURL`, `Task.Status.ResultSummary`, `Task.Status.Phase`; `Repository.Spec.URL`, `Repository.Spec.DefaultBranch`, `Repository.Spec.ProjectRef`; `Project.Spec.ScmSecretRef`, `Project.Spec.TriggerLabel`. If `TaskSource` or `Task.Status.PRURL`/`ResultSummary` are absent (M0 underspecified them), the boy-scout fix (rule 3) is to add them to the types + regenerate deepcopy in Task 3, noting it in `MEMORY.md`. Confirm the exact Go field name for `prURL` (kubebuilder json tag `prURL`; Go field likely `PRURL`) by reading `api/v1alpha1/task_types.go` first.
- The Project SCM token lives in the `scmSecretRef` Secret under key `token` (pin set "SCM secret shape"). The reconciler reads it; the webhook does not need the token for Task creation.
- PR/MR title = `Task.spec.goal` (first line, truncated to a sane length), body = `Task.status.resultSummary` plus a footer line linking `Task.spec.source.url` when set. `sourceBranch` = the branch the agent pushed; the convention is `tatara/task-<task-name>` (pinned in Task 3, matching what the wrapper pod is told to push - the wrapper-side branch naming is the wrapper's contract; M5 reads it from a Task status/annotation if M4 records it, else uses the deterministic `tatara/task-<name>`). `targetBranch` = `Repository.spec.defaultBranch`.
- Provider for write-back is resolved from `Repository`/event: webhook-born Tasks carry `source.provider`; for others derive from the Repository remote URL host (github.com -> github, gitlab.* -> gitlab) via a helper, pinned in Task 5.

PRECONDITIONS - read before editing (do not re-read once in context; grep for symbols instead, per context economy):
- `docs/superpowers/specs/2026-06-06-tatara-operator-design.md` (TaskReconciler "Terminate" step 5; Webhook server work-item bullet; scm interface).
- `docs/superpowers/plans/_tatara-operator-shared-contracts.md` (PIN SET - `scm.Client` signatures byte-for-byte; `WebhookEvent`; SCM secret shape; webhook-server contract stating M5 completes the work-item -> Task path). Obey exactly.
- `docs/superpowers/plans/2026-06-06-tatara-operator-m2-webhook-push.md` (Task 1 scm stubs this plan replaces; Task 5 `handleWorkItem` stub this plan completes; `scm.Select`/`SameRemote` helpers in `registry.go`).
- `internal/scm/{scm,github,gitlab,registry}.go` (M2) - confirm `errNotImplemented`, the `GitHub`/`GitLab` receiver types, and the `Select` signature.
- `internal/controller/task_controller.go` (M4) - confirm the TaskReconciler struct name, its fields, the terminal branch, and how it reads the Project SCM token + Repository. This is the single most important read for Task 3; do not edit blind (CLAUDE.md).
- `internal/webhook/server.go` (M2) - confirm `Server`/`Config`, `handleWorkItem`, `key`, the metrics accessor, and whether the router uses `Handler()` or `Mount()`.
- `api/v1alpha1/{task_types,repository_types,project_types}.go` - confirm the field names listed in Assumptions.
- `internal/obs/metrics.go` - confirm the metric accessor names used by reconciler/webhook.
- Mirror Go HTTP-client/test conventions from `~/Documents/tatara/tatara-memory/internal/lightrag/{http.go,http_test.go}` (context-aware request build, `%w` wrapping, typed `HTTPError`, `httptest` table tests faking the upstream, `t.Run`).

WORKTREE: `superpowers:using-git-worktrees` -> branch `feat/m5-scm-writeback-workitem` off `main`. All commits land there; merge to `tatara-operator` `main` at the end via `superpowers:finishing-a-development-branch`. Never build or deploy from the worktree (rule 10).

---

### Task 1: GitHub OpenChange + Comment (REST, httptest-faked)

**Files:** `internal/scm/github.go` (modify - replace the two M5 stub bodies + add a small client), `internal/scm/github_writeback_test.go` (new)

Replace the `errNotImplemented` bodies of `(*GitHub).OpenChange` and `(*GitHub).Comment`. Parse `repoURL` -> `owner/repo` (strip scheme/host, trailing `.git`); parse `issueRef` (`owner/repo#123`) -> number. Build authenticated requests against a configurable base (default `https://api.github.com`) so the test can point at an `httptest.Server`. Mirror lightrag's `roundTrip`/`HTTPError` style.

- [ ] Failing test - write `internal/scm/github_writeback_test.go` in full:
```go
package scm

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/require"
)

func newGitHub(t *testing.T, h http.HandlerFunc) *GitHub {
	t.Helper()
	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)
	return &GitHub{apiBase: srv.URL}
}

func TestGitHubOpenChange(t *testing.T) {
	c := newGitHub(t, func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, http.MethodPost, r.Method)
		require.Equal(t, "/repos/o/r/pulls", r.URL.Path)
		require.Equal(t, "Bearer ghtok", r.Header.Get("Authorization"))
		require.Equal(t, "application/vnd.github+json", r.Header.Get("Accept"))
		var in map[string]string
		require.NoError(t, json.NewDecoder(r.Body).Decode(&in))
		require.Equal(t, "feature-x", in["head"])
		require.Equal(t, "main", in["base"])
		require.Equal(t, "Fix the bug", in["title"])
		require.Equal(t, "body text", in["body"])
		w.WriteHeader(http.StatusCreated)
		_ = json.NewEncoder(w).Encode(map[string]any{"html_url": "https://github.com/o/r/pull/42"})
	})

	url, err := c.OpenChange(context.Background(), "https://github.com/o/r.git", "ghtok", "feature-x", "main", "Fix the bug", "body text")
	require.NoError(t, err)
	require.Equal(t, "https://github.com/o/r/pull/42", url)
}

func TestGitHubComment(t *testing.T) {
	c := newGitHub(t, func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, http.MethodPost, r.Method)
		require.Equal(t, "/repos/o/r/issues/7/comments", r.URL.Path)
		require.Equal(t, "Bearer ghtok", r.Header.Get("Authorization"))
		var in map[string]string
		require.NoError(t, json.NewDecoder(r.Body).Decode(&in))
		require.Equal(t, "done", in["body"])
		w.WriteHeader(http.StatusCreated)
		_ = json.NewEncoder(w).Encode(map[string]any{"id": 1})
	})

	require.NoError(t, c.Comment(context.Background(), "ghtok", "o/r#7", "done"))
}

func TestGitHubOpenChangeErrorStatus(t *testing.T) {
	c := newGitHub(t, func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusUnprocessableEntity)
		_, _ = w.Write([]byte(`{"message":"A pull request already exists"}`))
	})
	_, err := c.OpenChange(context.Background(), "https://github.com/o/r.git", "t", "h", "b", "title", "body")
	require.Error(t, err)
	var he *HTTPError
	require.ErrorAs(t, err, &he)
	require.Equal(t, 422, he.Status)
}

func TestGitHubParse(t *testing.T) {
	tests := []struct {
		name        string
		repoURL     string
		wantOwner   string
		wantRepo    string
		wantErr     bool
	}{
		{"https-git", "https://github.com/o/r.git", "o", "r", false},
		{"https-no-git", "https://github.com/o/r", "o", "r", false},
		{"trailing-slash", "https://github.com/o/r/", "o", "r", false},
		{"bad", "not a url with no path", "", "", true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			o, rp, err := ghOwnerRepo(tt.repoURL)
			if tt.wantErr {
				require.Error(t, err)
				return
			}
			require.NoError(t, err)
			require.Equal(t, tt.wantOwner, o)
			require.Equal(t, tt.wantRepo, rp)
		})
	}
}

func TestGitHubIssueNumber(t *testing.T) {
	o, rp, n, err := ghIssueRef("o/r#123")
	require.NoError(t, err)
	require.Equal(t, "o", o)
	require.Equal(t, "r", rp)
	require.Equal(t, 123, n)

	_, _, _, err = ghIssueRef("garbage")
	require.Error(t, err)
}
```
- [ ] Run `go test ./internal/scm/... -run GitHub` -> EXPECT FAIL (compile: `GitHub` has no `apiBase` field; `ghOwnerRepo`, `ghIssueRef`, `HTTPError` undefined; `OpenChange`/`Comment` still return the not-implemented error).
- [ ] Minimal impl - in `internal/scm/github.go`: add the `apiBase` field to `GitHub` (default applied lazily), a shared `HTTPError` type (or a `scm`-package one if not already present - if `scm.go` lacks it, add it there and reuse for gitlab in Task 2), the `ghOwnerRepo`/`ghIssueRef` parsers, and the real method bodies. Replace the existing M5 stub methods:
```go
// add near the GitHub type
func (c *GitHub) base() string {
	if c.apiBase != "" {
		return c.apiBase
	}
	return "https://api.github.com"
}

// OpenChange creates a pull request and returns its html_url.
func (c *GitHub) OpenChange(ctx context.Context, repoURL, token, sourceBranch, targetBranch, title, body string) (string, error) {
	owner, repo, err := ghOwnerRepo(repoURL)
	if err != nil {
		return "", err
	}
	path := fmt.Sprintf("/repos/%s/%s/pulls", owner, repo)
	reqBody := map[string]string{"title": title, "head": sourceBranch, "base": targetBranch, "body": body}
	var out struct {
		HTMLURL string `json:"html_url"`
	}
	if err := ghDo(ctx, c.base(), http.MethodPost, path, token, reqBody, &out); err != nil {
		return "", err
	}
	return out.HTMLURL, nil
}

// Comment posts a comment on an issue or PR identified by owner/repo#number.
func (c *GitHub) Comment(ctx context.Context, token, issueRef, body string) error {
	owner, repo, number, err := ghIssueRef(issueRef)
	if err != nil {
		return err
	}
	path := fmt.Sprintf("/repos/%s/%s/issues/%d/comments", owner, repo, number)
	return ghDo(ctx, c.base(), http.MethodPost, path, token, map[string]string{"body": body}, nil)
}

func ghOwnerRepo(repoURL string) (string, string, error) {
	u, err := url.Parse(repoURL)
	if err != nil {
		return "", "", fmt.Errorf("github: parse repo url %q: %w", repoURL, err)
	}
	p := strings.TrimSuffix(strings.Trim(u.Path, "/"), ".git")
	parts := strings.Split(p, "/")
	if len(parts) < 2 || parts[len(parts)-1] == "" || parts[len(parts)-2] == "" {
		return "", "", fmt.Errorf("github: cannot derive owner/repo from %q", repoURL)
	}
	return parts[len(parts)-2], parts[len(parts)-1], nil
}

func ghIssueRef(ref string) (string, string, int, error) {
	at := strings.LastIndex(ref, "#")
	if at < 0 {
		return "", "", 0, fmt.Errorf("github: malformed issue ref %q", ref)
	}
	or, numStr := ref[:at], ref[at+1:]
	oParts := strings.Split(or, "/")
	if len(oParts) != 2 || oParts[0] == "" || oParts[1] == "" {
		return "", "", 0, fmt.Errorf("github: malformed issue ref %q", ref)
	}
	n, err := strconv.Atoi(numStr)
	if err != nil {
		return "", "", 0, fmt.Errorf("github: malformed issue number in %q: %w", ref, err)
	}
	return oParts[0], oParts[1], n, nil
}

func ghDo(ctx context.Context, base, method, path, token string, in, out any) error {
	var rdr io.Reader
	if in != nil {
		b, err := json.Marshal(in)
		if err != nil {
			return fmt.Errorf("github: encode body: %w", err)
		}
		rdr = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, base+path, rdr)
	if err != nil {
		return fmt.Errorf("github: build request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Accept", "application/vnd.github+json")
	if rdr != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return fmt.Errorf("github: do request: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode >= 400 {
		buf, _ := io.ReadAll(resp.Body)
		return &HTTPError{Status: resp.StatusCode, Body: string(buf), Path: path}
	}
	if out == nil {
		_, _ = io.Copy(io.Discard, resp.Body)
		return nil
	}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return fmt.Errorf("github: decode response: %w", err)
	}
	return nil
}
```
Add the `apiBase` field to the `GitHub` struct (`type GitHub struct{ apiBase string }`) and the needed imports (`bytes`, `encoding/json`, `fmt`, `io`, `net/url`, `strconv`, `strings`). If `HTTPError` is not yet declared in the `scm` package, add it to `scm.go`:
```go
// HTTPError is returned when an SCM REST call responds 4xx/5xx.
type HTTPError struct {
	Status int
	Body   string
	Path   string
}

func (e *HTTPError) Error() string {
	return fmt.Sprintf("scm: %s -> %d: %s", e.Path, e.Status, e.Body)
}
```
- [ ] Run `go test ./internal/scm/... -run GitHub` -> EXPECT PASS. Run `go test ./internal/scm/...` -> EXPECT PASS (M2 DetectAndVerify tests still green; the M2 `TestM5MethodsNotImplemented` test now contradicts the implementation - delete or update that M2 test case for GitHub in this same task, noting it; it asserted the stub behavior M5 removes).
- [ ] Commit: `feat(scm): github OpenChange (PR) + Comment via REST`.

### Task 2: GitLab OpenChange + Comment (REST, project path URL-encoded, httptest-faked)

**Files:** `internal/scm/gitlab.go` (modify - replace the two M5 stub bodies + add a small client), `internal/scm/gitlab_writeback_test.go` (new)

Replace `errNotImplemented` for GitLab. GitLab addresses projects by URL-encoded `path_with_namespace` (e.g. `group%2Fproj`). Derive the project path from `repoURL` for `OpenChange`; for `Comment`, parse `issueRef` (`group/proj!iid`) into project path + iid. Auth header is `PRIVATE-TOKEN`. Base default `https://gitlab.com/api/v4`.

- [ ] Failing test - write `internal/scm/gitlab_writeback_test.go` in full:
```go
package scm

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/require"
)

func newGitLab(t *testing.T, h http.HandlerFunc) *GitLab {
	t.Helper()
	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)
	return &GitLab{apiBase: srv.URL}
}

func TestGitLabOpenChange(t *testing.T) {
	c := newGitLab(t, func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, http.MethodPost, r.Method)
		require.Equal(t, "/projects/g%2Fp/merge_requests", r.URL.EscapedPath())
		require.Equal(t, "gltok", r.Header.Get("PRIVATE-TOKEN"))
		var in map[string]string
		require.NoError(t, json.NewDecoder(r.Body).Decode(&in))
		require.Equal(t, "feature-x", in["source_branch"])
		require.Equal(t, "main", in["target_branch"])
		require.Equal(t, "Fix the bug", in["title"])
		require.Equal(t, "body text", in["description"])
		w.WriteHeader(http.StatusCreated)
		_ = json.NewEncoder(w).Encode(map[string]any{"web_url": "https://gitlab.com/g/p/-/merge_requests/9"})
	})

	url, err := c.OpenChange(context.Background(), "https://gitlab.com/g/p.git", "gltok", "feature-x", "main", "Fix the bug", "body text")
	require.NoError(t, err)
	require.Equal(t, "https://gitlab.com/g/p/-/merge_requests/9", url)
}

func TestGitLabComment(t *testing.T) {
	c := newGitLab(t, func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, http.MethodPost, r.Method)
		require.Equal(t, "/projects/g%2Fp/issues/12/notes", r.URL.EscapedPath())
		require.Equal(t, "gltok", r.Header.Get("PRIVATE-TOKEN"))
		var in map[string]string
		require.NoError(t, json.NewDecoder(r.Body).Decode(&in))
		require.Equal(t, "done", in["body"])
		w.WriteHeader(http.StatusCreated)
		_ = json.NewEncoder(w).Encode(map[string]any{"id": 1})
	})

	require.NoError(t, c.Comment(context.Background(), "gltok", "g/p!12", "done"))
}

func TestGitLabOpenChangeErrorStatus(t *testing.T) {
	c := newGitLab(t, func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusConflict)
		_, _ = w.Write([]byte(`{"message":["already exists"]}`))
	})
	_, err := c.OpenChange(context.Background(), "https://gitlab.com/g/p.git", "t", "h", "b", "title", "body")
	require.Error(t, err)
	var he *HTTPError
	require.ErrorAs(t, err, &he)
	require.Equal(t, 409, he.Status)
}

func TestGitLabProjectPath(t *testing.T) {
	tests := []struct {
		name    string
		repoURL string
		want    string
		wantErr bool
	}{
		{"git", "https://gitlab.com/g/p.git", "g/p", false},
		{"no-git", "https://gitlab.com/g/p", "g/p", false},
		{"subgroup", "https://gitlab.com/g/sub/p.git", "g/sub/p", false},
		{"bad", "::::", "", true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := glProjectPath(tt.repoURL)
			if tt.wantErr {
				require.Error(t, err)
				return
			}
			require.NoError(t, err)
			require.Equal(t, tt.want, got)
		})
	}
}

func TestGitLabIssueRef(t *testing.T) {
	path, iid, err := glIssueRef("g/sub/p!12")
	require.NoError(t, err)
	require.Equal(t, "g/sub/p", path)
	require.Equal(t, 12, iid)

	_, _, err = glIssueRef("garbage")
	require.Error(t, err)
}
```
- [ ] Run `go test ./internal/scm/... -run GitLab` -> EXPECT FAIL (compile: `GitLab` has no `apiBase`; `glProjectPath`, `glIssueRef` undefined; methods return not-implemented).
- [ ] Minimal impl - in `internal/scm/gitlab.go`: add `apiBase` to `GitLab`, the parsers, and the real bodies. Replace the M5 stub methods:
```go
func (c *GitLab) base() string {
	if c.apiBase != "" {
		return c.apiBase
	}
	return "https://gitlab.com/api/v4"
}

// OpenChange creates a merge request and returns its web_url.
func (c *GitLab) OpenChange(ctx context.Context, repoURL, token, sourceBranch, targetBranch, title, body string) (string, error) {
	proj, err := glProjectPath(repoURL)
	if err != nil {
		return "", err
	}
	path := "/projects/" + url.PathEscape(proj) + "/merge_requests"
	reqBody := map[string]string{
		"source_branch": sourceBranch,
		"target_branch": targetBranch,
		"title":         title,
		"description":   body,
	}
	var out struct {
		WebURL string `json:"web_url"`
	}
	if err := glDo(ctx, c.base(), http.MethodPost, path, token, reqBody, &out); err != nil {
		return "", err
	}
	return out.WebURL, nil
}

// Comment posts a note on an issue identified by group/proj!iid.
func (c *GitLab) Comment(ctx context.Context, token, issueRef, body string) error {
	proj, iid, err := glIssueRef(issueRef)
	if err != nil {
		return err
	}
	path := "/projects/" + url.PathEscape(proj) + "/issues/" + strconv.Itoa(iid) + "/notes"
	return glDo(ctx, c.base(), http.MethodPost, path, token, map[string]string{"body": body}, nil)
}

func glProjectPath(repoURL string) (string, error) {
	u, err := url.Parse(repoURL)
	if err != nil {
		return "", fmt.Errorf("gitlab: parse repo url %q: %w", repoURL, err)
	}
	p := strings.TrimSuffix(strings.Trim(u.Path, "/"), ".git")
	if p == "" {
		return "", fmt.Errorf("gitlab: cannot derive project path from %q", repoURL)
	}
	return p, nil
}

func glIssueRef(ref string) (string, int, error) {
	at := strings.LastIndex(ref, "!")
	if at < 0 {
		return "", 0, fmt.Errorf("gitlab: malformed issue ref %q", ref)
	}
	proj, iidStr := ref[:at], ref[at+1:]
	if proj == "" {
		return "", 0, fmt.Errorf("gitlab: malformed issue ref %q", ref)
	}
	iid, err := strconv.Atoi(iidStr)
	if err != nil {
		return "", 0, fmt.Errorf("gitlab: malformed iid in %q: %w", ref, err)
	}
	return proj, iid, nil
}

func glDo(ctx context.Context, base, method, path, token string, in, out any) error {
	var rdr io.Reader
	if in != nil {
		b, err := json.Marshal(in)
		if err != nil {
			return fmt.Errorf("gitlab: encode body: %w", err)
		}
		rdr = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, base+path, rdr)
	if err != nil {
		return fmt.Errorf("gitlab: build request: %w", err)
	}
	req.Header.Set("PRIVATE-TOKEN", token)
	req.Header.Set("Accept", "application/json")
	if rdr != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return fmt.Errorf("gitlab: do request: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode >= 400 {
		buf, _ := io.ReadAll(resp.Body)
		return &HTTPError{Status: resp.StatusCode, Body: string(buf), Path: path}
	}
	if out == nil {
		_, _ = io.Copy(io.Discard, resp.Body)
		return nil
	}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return fmt.Errorf("gitlab: decode response: %w", err)
	}
	return nil
}
```
Add `apiBase` to `GitLab` (`type GitLab struct{ apiBase string }`) and imports (`bytes`, `encoding/json`, `fmt`, `io`, `net/url`, `strconv`, `strings`). Reuse the package `HTTPError` added in Task 1. Update/delete the GitLab arm of the M2 `TestM5MethodsNotImplemented` test in `scm_test.go` (now implemented), noting it.
- [ ] Run `go test ./internal/scm/... -run GitLab` -> EXPECT PASS. Run `go test ./internal/scm/...` -> EXPECT PASS.
- [ ] Commit: `feat(scm): gitlab OpenChange (MR) + Comment via REST, url-encoded project path`.

### Task 3: TaskReconciler write-back (OpenChange on success, conditional Comment) - envtest

**Files:** `internal/controller/task_controller.go` (modify), `internal/controller/task_writeback_test.go` (new)

Read `internal/controller/task_controller.go` FIRST (CLAUDE.md: never edit blind). Locate (a) the TaskReconciler struct, (b) its terminal branch where M4 sets `status.phase=Succeeded`/`Failed`, (c) how it already loads the Repository and the Project SCM token. M5 adds an injected SCM dependency and, in the success branch, opens a change + records `status.prURL`, then comments if `Task.spec.source.issueRef` is set.

Pin the injected SCM seam as a narrow interface so envtest can fake it without hitting the network:
```go
// Writer opens changes and comments on the originating work item. Implemented
// by the per-provider scm clients; faked in tests.
type Writer interface {
	OpenChange(ctx context.Context, repoURL, token, sourceBranch, targetBranch, title, body string) (string, error)
	Comment(ctx context.Context, token, issueRef, body string) error
}
```
The reconciler resolves the concrete `Writer` per Task from the provider (a `func(provider string) (Writer, error)` factory field, e.g. `SCMFor`). Default factory in main returns `*scm.GitHub`/`*scm.GitLab`; tests inject a fake. Provider source: `Task.spec.source.provider` if set, else derived from the Repository remote host (helper `providerForRemote`, pinned here).

- [ ] Failing test - envtest with a fake Writer; seed Project+Secret+Repository+Task; drive the success branch and assert `status.prURL` + Comment. Write `internal/controller/task_writeback_test.go` in full (adjust the harness setup lines to match M4's existing `suite_test.go`/envtest bootstrap - reuse it, do not start a second envtest):
```go
package controller

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"

	tatarav1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
)

type fakeWriter struct {
	mu          sync.Mutex
	openCalls   int
	commentArgs []string // issueRef|body
	prURL       string
	openErr     error
}

func (f *fakeWriter) OpenChange(_ context.Context, _, _, src, dst, title, _ string) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.openCalls++
	if f.openErr != nil {
		return "", f.openErr
	}
	if f.prURL == "" {
		f.prURL = "https://example/pr/1"
	}
	return f.prURL, nil
}

func (f *fakeWriter) Comment(_ context.Context, _, issueRef, body string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.commentArgs = append(f.commentArgs, issueRef+"|"+body)
	return nil
}

// reconcileToSuccess applies the agent's terminal state for a Task and reconciles
// once. M4's loop normally reaches this via turn callbacks; the test seeds the
// preconditions M4 uses to enter its terminal success branch (no Pending
// Subtasks). Set resultSummary + a pushed-branch marker the way M4 records it.
func TestTaskWriteBackOpensPRAndComments(t *testing.T) {
	ctx := context.Background()
	fw := &fakeWriter{prURL: "https://github.com/o/r/pull/5"}

	r := &TaskReconciler{
		Client: k8sClient,
		Scheme: k8sClient.Scheme(),
		SCMFor: func(string) (Writer, error) { return fw, nil },
	}

	mustCreate(t, ctx, &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{Name: "proj-scm", Namespace: testNS},
		Data:       map[string][]byte{"token": []byte("pat"), "webhookSecret": []byte("w")},
	})
	mustCreate(t, ctx, &tatarav1.Project{
		ObjectMeta: metav1.ObjectMeta{Name: "proj", Namespace: testNS},
		Spec:       tatarav1.ProjectSpec{ScmSecretRef: "proj-scm", TriggerLabel: "tatara"},
	})
	mustCreate(t, ctx, &tatarav1.Repository{
		ObjectMeta: metav1.ObjectMeta{Name: "repo", Namespace: testNS},
		Spec:       tatarav1.RepositorySpec{ProjectRef: "proj", URL: "https://github.com/o/r.git", DefaultBranch: "main"},
	})
	task := &tatarav1.Task{
		ObjectMeta: metav1.ObjectMeta{Name: "task1", Namespace: testNS},
		Spec: tatarav1.TaskSpec{
			ProjectRef:    "proj",
			RepositoryRef: "repo",
			Goal:          "Fix the bug",
			Source:        tatarav1.TaskSource{Provider: "github", IssueRef: "o/r#7", URL: "https://github.com/o/r/issues/7"},
		},
	}
	mustCreate(t, ctx, task)

	// Drive the Task into the state M4 enters its terminal success branch from.
	// (Mirror exactly what M4 sets before write-back: phase + resultSummary +
	// any pushed-branch field. Read M4's terminal branch and replicate here.)
	seedTerminalSuccess(t, ctx, r, task)

	require.Eventually(t, func() bool {
		var got tatarav1.Task
		if err := k8sClient.Get(ctx, client.ObjectKeyFromObject(task), &got); err != nil {
			return false
		}
		return got.Status.PRURL != "" && got.Status.Phase == "Succeeded"
	}, 5*time.Second, 100*time.Millisecond)

	var got tatarav1.Task
	require.NoError(t, k8sClient.Get(ctx, client.ObjectKeyFromObject(task), &got))
	require.Equal(t, "https://github.com/o/r/pull/5", got.Status.PRURL)

	fw.mu.Lock()
	defer fw.mu.Unlock()
	require.Equal(t, 1, fw.openCalls)
	require.Len(t, fw.commentArgs, 1)
	require.Contains(t, fw.commentArgs[0], "o/r#7|")
}

func TestTaskWriteBackNoCommentWhenNoSource(t *testing.T) {
	ctx := context.Background()
	fw := &fakeWriter{prURL: "https://github.com/o/r/pull/6"}
	r := &TaskReconciler{Client: k8sClient, Scheme: k8sClient.Scheme(), SCMFor: func(string) (Writer, error) { return fw, nil }}

	mustCreate(t, ctx, &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: "proj2-scm", Namespace: testNS}, Data: map[string][]byte{"token": []byte("pat"), "webhookSecret": []byte("w")}})
	mustCreate(t, ctx, &tatarav1.Project{ObjectMeta: metav1.ObjectMeta{Name: "proj2", Namespace: testNS}, Spec: tatarav1.ProjectSpec{ScmSecretRef: "proj2-scm"}})
	mustCreate(t, ctx, &tatarav1.Repository{ObjectMeta: metav1.ObjectMeta{Name: "repo2", Namespace: testNS}, Spec: tatarav1.RepositorySpec{ProjectRef: "proj2", URL: "https://github.com/o/r2.git", DefaultBranch: "main"}})
	task := &tatarav1.Task{ObjectMeta: metav1.ObjectMeta{Name: "task2", Namespace: testNS}, Spec: tatarav1.TaskSpec{ProjectRef: "proj2", RepositoryRef: "repo2", Goal: "no-source task"}}
	mustCreate(t, ctx, task)
	seedTerminalSuccess(t, ctx, r, task)

	require.Eventually(t, func() bool {
		var got tatarav1.Task
		_ = k8sClient.Get(ctx, client.ObjectKeyFromObject(task), &got)
		return got.Status.PRURL != ""
	}, 5*time.Second, 100*time.Millisecond)

	fw.mu.Lock()
	defer fw.mu.Unlock()
	require.Equal(t, 1, fw.openCalls)
	require.Empty(t, fw.commentArgs)
}
```
Add the two small test helpers `mustCreate` and `seedTerminalSuccess` at the bottom of the file (or reuse M4 equivalents if present - grep first):
```go
func mustCreate(t *testing.T, ctx context.Context, obj client.Object) {
	t.Helper()
	require.NoError(t, k8sClient.Create(ctx, obj))
}

// seedTerminalSuccess replicates the exact preconditions M4 requires to enter
// its terminal success branch, then triggers a Reconcile. Read M4's terminal
// branch (no Pending subtasks + resultSummary set) and mirror it here. This is
// the one place this plan defers to M4's actual shape; align it after reading
// task_controller.go.
func seedTerminalSuccess(t *testing.T, ctx context.Context, r *TaskReconciler, task *tatarav1.Task) {
	t.Helper()
	var cur tatarav1.Task
	require.NoError(t, k8sClient.Get(ctx, client.ObjectKeyFromObject(task), &cur))
	cur.Status.Phase = "Running"
	cur.Status.ResultSummary = "did the thing"
	require.NoError(t, k8sClient.Status().Update(ctx, &cur))
	_, err := r.Reconcile(ctx, reconcileReq(task))
	require.NoError(t, err)
}
```
(If M4's `suite_test.go` already exposes `k8sClient`, `testNS`, and a `reconcileReq`/request helper, reuse them and drop redundant declarations. The `reconcileReq` helper builds a `ctrl.Request` for the Task's NamespacedName; add it only if M4 lacks one.)
- [ ] Run `go test ./internal/controller/... -run TaskWriteBack` -> EXPECT FAIL (compile: `TaskReconciler` has no `SCMFor` field; `Writer` undefined; `Task.Status.PRURL`/`Task.Spec.Source` may be undefined). Resolve undefined CRD fields FIRST per Assumptions (add `TaskSource`, `Status.PRURL`, `Status.ResultSummary` to `api/v1alpha1/task_types.go` + `make generate manifests` if absent), noting it in `MEMORY.md`.
- [ ] Minimal impl - in `internal/controller/task_controller.go`: add the `Writer` interface and a `SCMFor func(provider string) (Writer, error)` field on `TaskReconciler`; in the terminal success branch (the M4 hook), after the agent's branch is pushed, do the write-back. Insert in the success branch (adapt variable names to M4's actual code):
```go
// --- write-back (M5): open PR/MR and comment on the originating work item ---
provider := task.Spec.Source.Provider
if provider == "" {
	provider = providerForRemote(repo.Spec.URL)
}
writer, err := r.SCMFor(provider)
if err != nil {
	log.Error(err, "select scm writer", "provider", provider)
	return r.failTask(ctx, &task, fmt.Sprintf("scm writer: %v", err))
}
token, err := r.scmToken(ctx, task.Namespace, proj.Spec.ScmSecretRef)
if err != nil {
	return r.failTask(ctx, &task, fmt.Sprintf("scm token: %v", err))
}
sourceBranch := taskBranch(&task)
title := firstLine(task.Spec.Goal)
body := writeBackBody(&task)
prURL, err := writer.OpenChange(ctx, repo.Spec.URL, token, sourceBranch, repo.Spec.DefaultBranch, title, body)
if err != nil {
	log.Error(err, "open change", "task", task.Name)
	return r.failTask(ctx, &task, fmt.Sprintf("open change: %v", err))
}
task.Status.PRURL = prURL
if task.Spec.Source.IssueRef != "" {
	if err := writer.Comment(ctx, token, task.Spec.Source.IssueRef, writeBackBody(&task)+"\n\n"+prURL); err != nil {
		log.Error(err, "comment on work item", "issue_ref", task.Spec.Source.IssueRef)
		// non-fatal: PR exists; record but do not fail the Task.
	}
}
task.Status.Phase = "Succeeded"
if err := r.Status().Update(ctx, &task); err != nil {
	return ctrl.Result{}, fmt.Errorf("update task status: %w", err)
}
log.Info("task write-back complete", "task", task.Name, "pr_url", prURL, "commented", task.Spec.Source.IssueRef != "")
return ctrl.Result{}, nil
```
Add the helpers (in the same file, or a new `internal/controller/writeback.go`):
```go
func providerForRemote(remote string) string {
	if strings.Contains(strings.ToLower(remote), "gitlab") {
		return "gitlab"
	}
	return "github"
}

func taskBranch(t *tatarav1.Task) string {
	return "tatara/task-" + t.Name
}

func firstLine(s string) string {
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		s = s[:i]
	}
	if len(s) > 72 {
		s = s[:72]
	}
	if s == "" {
		return "tatara automated change"
	}
	return s
}

func writeBackBody(t *tatarav1.Task) string {
	b := t.Status.ResultSummary
	if b == "" {
		b = t.Spec.Goal
	}
	if t.Spec.Source.URL != "" {
		b += "\n\nSource: " + t.Spec.Source.URL
	}
	return b
}

func (r *TaskReconciler) scmToken(ctx context.Context, ns, ref string) (string, error) {
	var sec corev1.Secret
	if err := r.Get(ctx, client.ObjectKey{Namespace: ns, Name: ref}, &sec); err != nil {
		return "", fmt.Errorf("get scm secret: %w", err)
	}
	v, ok := sec.Data["token"]
	if !ok {
		return "", fmt.Errorf("scm secret %q missing token key", ref)
	}
	return string(v), nil
}
```
If M4 already has a `failTask` helper, reuse it; otherwise add a minimal one that sets `status.phase=Failed`, appends a condition/`resultSummary`, and returns `(ctrl.Result{}, nil)` (a terminal failure is not a reconcile error - do not requeue forever on a permanent SCM 422). Add imports as needed (`strings`, `fmt`, `corev1`, `client`).
- [ ] Run `go test ./internal/controller/... -run TaskWriteBack` -> EXPECT PASS. Run `go test ./internal/controller/...` -> EXPECT PASS (M4 tests still green; if `seedTerminalSuccess` drove a path M4 also exercises, ensure no double-open - the success branch must be idempotent: skip OpenChange when `status.prURL != ""`). Add that guard if missing:
```go
if task.Status.PRURL != "" {
	return ctrl.Result{}, nil // write-back already done
}
```
- [ ] Commit: `feat(controller): task write-back - OpenChange on success, comment on source issue`.

### Task 4: webhook work-item -> Task creation (httptest, signed payload, fake client)

**Files:** `internal/webhook/server.go` (modify `handleWorkItem`), `internal/webhook/workitem_test.go` (new)

Read `internal/webhook/server.go` FIRST: confirm `handleWorkItem`'s current signature and how it has access to the controller-runtime client + namespace. M5 replaces the M2 log-only stub with Task creation. The handler must: find the Repository in the Project matching `ev.Repo` (via `scm.SameRemote`), then create a `Task` with `goal=ev.Body`, `source={provider, issueRef, url}`, `projectRef=project`, `repositoryRef=<matched repo>`, owner-ref'd to the Project (cascade delete with the Project; the Task in turn owns Subtasks). Provider comes from the selected `scm.Client.Provider()`. No Task when the trigger label is absent (already handled) and no Task when no Repository matches (count `ignored`/`no_repo`, still 202).

- [ ] Failing test - write `internal/webhook/workitem_test.go` in full (reuses helpers from `server_test.go`: `seedClient`, `project`, `secret`, `repository`, `newServer`, `post`, `ghSign`, `counterValue`, `ns`; do not redeclare them - this file is `package webhook_test` like M2's):
```go
package webhook_test

import (
	"context"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
	"sigs.k8s.io/controller-runtime/pkg/client"

	tatarav1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
)

func TestIssueWithTriggerLabelCreatesTask(t *testing.T) {
	const secretVal = "whsec"
	c := seedClient(t,
		project("proj1", "proj1-scm", "tatara"),
		secret("proj1-scm", secretVal),
		repository("repo1", "proj1", "https://github.com/o/r.git", "main"),
	)
	h, reg := newServer(t, c)

	body := []byte(`{"action":"opened","issue":{"number":7,"title":"Fix the bug","body":"please fix","labels":[{"name":"tatara"}],"html_url":"https://github.com/o/r/issues/7"},"repository":{"clone_url":"https://github.com/o/r.git","full_name":"o/r"}}`)
	hdr := http.Header{}
	hdr.Set("X-GitHub-Event", "issues")
	hdr.Set("X-Hub-Signature-256", ghSign(secretVal, body))

	w := post(t, h, "proj1", hdr, body)
	require.Equal(t, http.StatusAccepted, w.Code)

	var tasks tatarav1.TaskList
	require.NoError(t, c.List(context.Background(), &tasks, client.InNamespace(ns)))
	require.Len(t, tasks.Items, 1)
	tk := tasks.Items[0]
	require.Equal(t, "proj1", tk.Spec.ProjectRef)
	require.Equal(t, "repo1", tk.Spec.RepositoryRef)
	require.Equal(t, "please fix", tk.Spec.Goal)
	require.Equal(t, "github", tk.Spec.Source.Provider)
	require.Equal(t, "o/r#7", tk.Spec.Source.IssueRef)
	require.Equal(t, "https://github.com/o/r/issues/7", tk.Spec.Source.URL)
	// owner-ref'd to the Project
	require.Len(t, tk.OwnerReferences, 1)
	require.Equal(t, "Project", tk.OwnerReferences[0].Kind)
	require.Equal(t, "proj1", tk.OwnerReferences[0].Name)

	require.Equal(t, 1.0, counterValue(t, reg, "operator_webhook_events_total", map[string]string{"provider": "github", "kind": "issue", "result": "task_created"}))
}

func TestWorkItemNoLabelNoTask(t *testing.T) {
	const secretVal = "whsec"
	c := seedClient(t,
		project("proj1", "proj1-scm", "tatara"),
		secret("proj1-scm", secretVal),
		repository("repo1", "proj1", "https://github.com/o/r.git", "main"),
	)
	h, _ := newServer(t, c)
	body := []byte(`{"action":"opened","issue":{"number":8,"title":"x","body":"y","labels":[{"name":"bug"}],"html_url":"https://github.com/o/r/issues/8"},"repository":{"clone_url":"https://github.com/o/r.git","full_name":"o/r"}}`)
	hdr := http.Header{}
	hdr.Set("X-GitHub-Event", "issues")
	hdr.Set("X-Hub-Signature-256", ghSign(secretVal, body))

	w := post(t, h, "proj1", hdr, body)
	require.Equal(t, http.StatusAccepted, w.Code)
	var tasks tatarav1.TaskList
	require.NoError(t, c.List(context.Background(), &tasks, client.InNamespace(ns)))
	require.Empty(t, tasks.Items)
}

func TestWorkItemLabeledButNoRepoMatch(t *testing.T) {
	const secretVal = "whsec"
	c := seedClient(t,
		project("proj1", "proj1-scm", "tatara"),
		secret("proj1-scm", secretVal),
		repository("repo1", "proj1", "https://github.com/o/OTHER.git", "main"),
	)
	h, reg := newServer(t, c)
	body := []byte(`{"action":"opened","issue":{"number":9,"title":"x","body":"y","labels":[{"name":"tatara"}],"html_url":"https://github.com/o/r/issues/9"},"repository":{"clone_url":"https://github.com/o/r.git","full_name":"o/r"}}`)
	hdr := http.Header{}
	hdr.Set("X-GitHub-Event", "issues")
	hdr.Set("X-Hub-Signature-256", ghSign(secretVal, body))

	w := post(t, h, "proj1", hdr, body)
	require.Equal(t, http.StatusAccepted, w.Code)
	var tasks tatarav1.TaskList
	require.NoError(t, c.List(context.Background(), &tasks, client.InNamespace(ns)))
	require.Empty(t, tasks.Items)
	require.Equal(t, 1.0, counterValue(t, reg, "operator_webhook_events_total", map[string]string{"provider": "github", "kind": "issue", "result": "no_repo"}))
}
```
Note: this supersedes M2's `TestIssueWithTriggerLabelStubbed` (which asserted NO task). Delete that M2 test case in this task (it tested the stub M5 removes), and keep its no-label assertion via the new `TestWorkItemNoLabelNoTask`.
- [ ] Run `go test ./internal/webhook/... -run "WorkItem|IssueWithTriggerLabelCreates"` -> EXPECT FAIL (handler still logs only; no Task; result label `task_created`/`no_repo` not emitted; also `TestIssueWithTriggerLabelStubbed` now fails - remove it).
- [ ] Minimal impl - rewrite `handleWorkItem` in `internal/webhook/server.go`. It needs the context, the matched provider, the Project, and the event; it lists Repositories in the namespace, finds the one with `ProjectRef == proj.Name` and `SameRemote(repo.Spec.URL, ev.Repo)`, then creates the Task with an owner reference to the Project. Replace the M2 stub body:
```go
func (s *Server) handleWorkItem(ctx context.Context, w http.ResponseWriter, provider string, proj tatarav1.Project, ev scm.WebhookEvent) {
	if !slices.Contains(ev.Labels, proj.Spec.TriggerLabel) {
		s.count(provider, ev.Kind, "ignored")
		w.WriteHeader(http.StatusAccepted)
		return
	}

	var repos tatarav1.RepositoryList
	if err := s.cfg.Client.List(ctx, &repos, client.InNamespace(s.cfg.Namespace)); err != nil {
		s.count(provider, ev.Kind, "error")
		http.Error(w, "list repositories", http.StatusInternalServerError)
		return
	}
	var repo *tatarav1.Repository
	for i := range repos.Items {
		r := &repos.Items[i]
		if r.Spec.ProjectRef == proj.Name && scm.SameRemote(r.Spec.URL, ev.Repo) {
			repo = r
			break
		}
	}
	if repo == nil {
		s.log.InfoContext(ctx, "work item labeled but no matching repository", "project", proj.Name, "remote", ev.Repo, "issue_ref", ev.IssueRef)
		s.count(provider, ev.Kind, "no_repo")
		w.WriteHeader(http.StatusAccepted)
		return
	}

	task := &tatarav1.Task{
		ObjectMeta: metav1.ObjectMeta{
			GenerateName: "task-",
			Namespace:    s.cfg.Namespace,
			OwnerReferences: []metav1.OwnerReference{
				*metav1.NewControllerRef(&proj, tatarav1.GroupVersion.WithKind("Project")),
			},
		},
		Spec: tatarav1.TaskSpec{
			ProjectRef:    proj.Name,
			RepositoryRef: repo.Name,
			Goal:          ev.Body,
			Source: tatarav1.TaskSource{
				Provider: provider,
				IssueRef: ev.IssueRef,
				URL:      ev.URL,
			},
		},
	}
	if err := s.cfg.Client.Create(ctx, task); err != nil {
		s.count(provider, ev.Kind, "error")
		http.Error(w, "create task", http.StatusInternalServerError)
		return
	}
	s.log.InfoContext(ctx, "work item created task", "project", proj.Name, "repository", repo.Name, "task", task.Name, "issue_ref", ev.IssueRef)
	s.count(provider, ev.Kind, "task_created")
	w.WriteHeader(http.StatusAccepted)
}
```
Update the caller in `handle` to pass `ctx`: `s.handleWorkItem(ctx, w, provider, proj, ev)`. Add imports `metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"` and confirm `tatarav1.GroupVersion` is the M0 group-version var name (grep `GroupVersion` in `api/v1alpha1`; if it is `SchemeGroupVersion`, use that). Verify `metav1.NewControllerRef` requires the Project to be a registered scheme type (the fake client builder already has it; in main the manager scheme has it).
- [ ] Run `go test ./internal/webhook/...` -> EXPECT PASS (new work-item cases + the M2 push/signature/unknown-project cases still green; the removed stub test is gone).
- [ ] Commit: `feat(webhook): work-item with trigger label creates a Task owner-ref'd to Project`.

### Task 5: wire real scm registry into cmd/manager/main.go

**Files:** `cmd/manager/main.go` (modify), `internal/scm/registry.go` (modify - add a provider->Client lookup if M2's `Select` only does headers)

The TaskReconciler now needs a `SCMFor func(string) (Writer, error)` and the webhook server already gets its `scm.Client` via header `Select` (no change needed there for provider selection). Provide a single registry function `scm.ByProvider(name string) (Client, error)` returning the real `*GitHub`/`*GitLab` (with default API bases), and adapt it into the reconciler's `Writer` factory (a `Client` satisfies `Writer` because `OpenChange`/`Comment` match).

- [ ] Failing test - write `internal/scm/registry_byprovider_test.go` in full:
```go
package scm

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestByProvider(t *testing.T) {
	gh, err := ByProvider("github")
	require.NoError(t, err)
	require.Equal(t, "github", gh.Provider())

	gl, err := ByProvider("gitlab")
	require.NoError(t, err)
	require.Equal(t, "gitlab", gl.Provider())

	_, err = ByProvider("bitbucket")
	require.Error(t, err)
}
```
- [ ] Run `go test ./internal/scm/... -run TestByProvider` -> EXPECT FAIL (undefined `ByProvider`).
- [ ] Minimal impl - add to `internal/scm/registry.go`:
```go
// ByProvider returns the real Client for a provider name ("github"|"gitlab").
func ByProvider(name string) (Client, error) {
	switch name {
	case "github":
		return &GitHub{}, nil
	case "gitlab":
		return &GitLab{}, nil
	default:
		return nil, fmt.Errorf("scm: unknown provider %q", name)
	}
}
```
Add the `fmt` import if absent.
- [ ] Run `go test ./internal/scm/...` -> EXPECT PASS.
- [ ] Modify `cmd/manager/main.go` (read it first; show only the diff per the writing rules). Where the TaskReconciler is constructed (M4), inject the SCM factory; the webhook server construction is unchanged (it uses `scm.Select` internally). Add:
```go
if err = (&controller.TaskReconciler{
	Client: mgr.GetClient(),
	Scheme: mgr.GetScheme(),
	// ... existing M4 fields (Session client, Metrics, Config, etc.) ...
	SCMFor: func(provider string) (controller.Writer, error) {
		return scm.ByProvider(provider)
	},
}).SetupWithManager(mgr); err != nil {
	setupLog.Error(err, "unable to create controller", "controller", "Task")
	os.Exit(1)
}
```
Match M4's actual reconciler field set and setup call exactly (do not drop fields). Add the `scm` import. The returned `scm.Client` satisfies `controller.Writer` (its `OpenChange`/`Comment` match); if Go complains about the interface mismatch, the factory wraps it (`return scm.ByProvider(provider)` typed to `controller.Writer` works because `*scm.GitHub` implements both method sets identically) - confirm by compile.
- [ ] Run `go build ./...` -> EXPECT PASS. Run `go vet ./...` -> clean.
- [ ] Commit: `feat(cmd): inject real scm registry into the Task reconciler`.

### Task 6: full verification + merge

**Files:** none new (verification + merge only)

- [ ] `superpowers:verification-before-completion`. From the worktree:
  - `go build ./...` -> clean.
  - `go test ./... -race -count=1` -> all green (especially `./internal/scm/...`, `./internal/controller/...`, `./internal/webhook/...`). envtest binaries must be present (`make envtest` / `setup-envtest` as M4 wired it).
  - `golangci-lint run ./...` -> clean (all created errors wrapped with `%w`; no unused; JSON slog only, rule 11).
  - `gofmt -l .` -> empty.
- [ ] Confirm the pinned contract is honored byte-for-byte: `scm.Client` signatures unchanged from the pin set; `OpenChange` returns the PR/MR URL (github `html_url`, gitlab `web_url`); `Comment` targets issue comments (github `issues/{n}/comments`, gitlab `issues/{iid}/notes`); GitHub auth `Authorization: Bearer`, GitLab auth `PRIVATE-TOKEN`; GitLab project path URL-encoded; reconciler records `status.prURL` and only comments when `Task.spec.source.issueRef` is set; webhook creates a Task with `goal=Body`, `source{provider,issueRef,url}`, `projectRef`/`repositoryRef`, owner-ref Project, only when the trigger label is present AND a Repository matches by remote URL.
- [ ] Confirm no double-write: the reconciler success branch is idempotent (`status.prURL != ""` short-circuits); a permanent SCM error fails the Task without infinite requeue.
- [ ] Update `MEMORY.md` (one line, dated 2026-06-06): record CRD additions made if any (`TaskSource`, `Status.PRURL`/`ResultSummary`), the `scm.HTTPError` type, the `tatara/task-<name>` branch-naming convention, the deleted M2 work-item stub test, and that GitLab issue notes (not MR notes) are used for `Comment` even on MR-born Tasks (pick: the originating work item's `IssueRef` carries the kind via its `!iid` form; both issues and MRs resolve through `/issues/{iid}/notes` for v1 - if MR-born Tasks need MR notes, that is a follow-up; note it). Update `ROADMAP.md`: mark M5 done; the only remaining milestone is M6 (chart + deploy wiring).
- [ ] `superpowers:requesting-code-review` (opus). Apply critical/high findings, re-run `go test ./... -race` and `golangci-lint run ./...`, then `pre-commit run --all-files`.
- [ ] `superpowers:finishing-a-development-branch`: merge `feat/m5-scm-writeback-workitem` into `tatara-operator` `main`, delete the worktree. Do not build or deploy from the worktree (rule 10).
