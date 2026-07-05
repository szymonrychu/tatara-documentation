# tatara-operator M2 (webhook server: push -> main-filtered incremental re-ingest) Implementation Plan

> For agentic workers: execute tasks top to bottom. Each task is one TDD cycle: write the failing test (full code), run it and SEE it fail, write the minimal implementation (full code), run it and SEE it pass, then commit. Do not skip the "see it fail" step. Do not batch tasks. Implementation subagents run sonnet (rule 7); plan/review/merge run opus. All work happens in a worktree off `main` (rule 10); never build or deploy from the worktree. Use the superpowers skills: `test-driven-development` per task, `requesting-code-review` before the final merge, `verification-before-completion` before claiming done.

**Goal:** Stand up the operator's webhook server. A `POST /operator/webhooks/{project}` endpoint on `HTTP_ADDR` receives GitHub/GitLab events, auto-detects the provider from headers, HMAC-verifies the payload against the Project's `webhookSecret`, and: on a `push` to a Repository's `defaultBranch` sets the `tatara.dev/reingest-requested` RFC3339 annotation on that Repository (re-using the M1 re-ingest trigger contract); on an `issue`/`mr` carrying the Project's `triggerLabel`, logs + increments a metric + returns 202 as a deliberate stub that M5 completes (no Task creation in M2). Unknown project or bad signature returns 404/401 with no CRD mutation. Every event increments `operator_webhook_events_total{provider,kind,result}`.

**Architecture:** The `internal/scm` package owns provider-agnostic webhook parsing behind the pinned `scm.Client` interface; M2 implements only `DetectAndVerify` for both providers (the `OpenChange`/`Comment` methods exist but return `errors.New("not implemented")`, completed in M5). A `registry` selects the right `scm.Client` from request headers. The `internal/webhook` package wraps that with a chi router that loads the Project + secret via the controller-runtime client, dispatches by event kind, and mutates the matching Repository. The server runs as a separate goroutine in `cmd/manager/main.go`, sharing the manager's client and `HTTP_ADDR` listener (the REST API in M3 mounts on the same router under different path prefixes). HTTP plumbing mirrors `tatara-chat/internal/httpapi` (chi v5 router, `httptest` table tests, JSON error envelope).

**Tech Stack:** Go 1.25.x (rule 1; pinned exact minor in `go.mod`), kubebuilder / controller-runtime, `github.com/go-chi/chi/v5`, `sigs.k8s.io/controller-runtime/pkg/client` (+ `pkg/client/fake` for tests), `github.com/prometheus/client_golang`, stdlib `crypto/hmac`/`crypto/sha256`/`crypto/subtle`, `log/slog`, `net/http/httptest`, `github.com/stretchr/testify/require`. Metrics come from `internal/obs` (M0). Config from `internal/config` (M0).

Assumptions (stated picks, rule: act + note): M0 (`internal/obs` with the `operator_webhook_events_total{provider,kind,result}` CounterVec already registered, `internal/config` with `HTTP_ADDR`, the four CRD types + deepcopy under `api/v1alpha1`, `cmd/manager/main.go` with a controller-runtime manager) and M1 (Repository reconciler honoring the `tatara.dev/reingest-requested` annotation) are merged on `main` before M2 starts. The annotation constant is owned by M1; M2 imports it (Task 5 picks the exact symbol). Remote-URL matching normalizes by stripping a trailing `.git` and a trailing `/`, lowercasing the host, and comparing case-insensitively (a single helper, unit-tested) - this is the one non-obvious choice and it is pinned in Task 4.

PRECONDITIONS - read before editing (do not re-read once in context, grep for symbols instead, per context economy):
- `docs/superpowers/specs/2026-06-06-tatara-operator-design.md` (Webhook server section).
- `docs/superpowers/plans/_tatara-operator-shared-contracts.md` (PIN SET - `scm.Client`, `WebhookEvent`, webhook-server contract, re-ingest trigger contract, metric labels). Obey exactly.
- `api/v1alpha1/{project_types,repository_types}.go` (M0) - confirm field names `Project.Spec.ScmSecretRef`, `Project.Spec.TriggerLabel`, `Repository.Spec.URL`, `Repository.Spec.DefaultBranch`, `Repository.Spec.ProjectRef`.
- `internal/config/config.go` (M0) - confirm `HTTP_ADDR` accessor.
- `internal/obs/metrics.go` (M0) - confirm the `operator_webhook_events_total` CounterVec accessor and label order `{provider,kind,result}`.
- M1 re-ingest annotation constant (grep `reingest-requested` under `internal/` and `api/`).
- Mirror HTTP/test conventions from `~/Documents/tatara/tatara-chat/internal/httpapi/{router,router_test,errors,middleware}.go` (chi router, `httptest`, table tests, JSON error envelope).

WORKTREE: `superpowers:using-git-worktrees` -> branch `feat/m2-webhook-push` off `main`. All commits land there; merge to `tatara-operator` `main` at the end via `superpowers:finishing-a-development-branch`.

---

### Task 1: scm types + interface + not-implemented stubs

**Files:** `internal/scm/scm.go`, `internal/scm/scm_test.go`

- [ ] Failing test - assert the `WebhookEvent` struct shape and that the github/gitlab stub structs satisfy `scm.Client` with `OpenChange`/`Comment` returning the not-implemented error. Write `internal/scm/scm_test.go` in full:
```go
package scm

import (
	"context"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestClientsSatisfyInterface(t *testing.T) {
	var _ Client = (*GitHub)(nil)
	var _ Client = (*GitLab)(nil)
}

func TestProviders(t *testing.T) {
	require.Equal(t, "github", (&GitHub{}).Provider())
	require.Equal(t, "gitlab", (&GitLab{}).Provider())
}

func TestM5MethodsNotImplemented(t *testing.T) {
	ctx := context.Background()
	for _, c := range []Client{&GitHub{}, &GitLab{}} {
		_, err := c.OpenChange(ctx, "https://x/r", "tok", "src", "dst", "t", "b")
		require.ErrorContains(t, err, "not implemented")
		require.ErrorContains(t, c.Comment(ctx, "tok", "o/r#1", "b"), "not implemented")
	}
}

func TestWebhookEventZeroValue(t *testing.T) {
	var e WebhookEvent
	require.Equal(t, "", e.Kind)
	require.Nil(t, e.Labels)
	_ = http.Header{}
}
```
- [ ] Run `go test ./internal/scm/...` -> EXPECT FAIL (compile error: undefined `Client`, `WebhookEvent`, `GitHub`, `GitLab`).
- [ ] Minimal impl - write `internal/scm/scm.go` in full (interface verbatim from the pin set; stubs for M5 methods):
```go
package scm

import (
	"context"
	"errors"
	"net/http"
)

// WebhookEvent is the provider-agnostic parse of an inbound SCM webhook.
type WebhookEvent struct {
	Kind     string // "push" | "issue" | "mr" | "other"
	Repo     string // remote URL
	Branch   string // for push
	Labels   []string
	Title    string
	Body     string
	IssueRef string // owner/repo#123 (github) or group/proj!iid (gitlab)
	URL      string
}

// Client is the per-provider SCM adapter. M2 implements DetectAndVerify;
// OpenChange and Comment are implemented in M5.
type Client interface {
	Provider() string
	DetectAndVerify(h http.Header, payload []byte, secret string) (WebhookEvent, error)
	OpenChange(ctx context.Context, repoURL, token, sourceBranch, targetBranch, title, body string) (url string, err error)
	Comment(ctx context.Context, token, issueRef, body string) error
}

var errNotImplemented = errors.New("not implemented: M5")

// GitHub implements Client for GitHub.
type GitHub struct{}

// GitLab implements Client for GitLab.
type GitLab struct{}

func (*GitHub) Provider() string { return "github" }
func (*GitLab) Provider() string { return "gitlab" }

// OpenChange is implemented in M5 (SCM write-back).
func (*GitHub) OpenChange(context.Context, string, string, string, string, string, string) (string, error) {
	return "", errNotImplemented
}

// Comment is implemented in M5 (SCM write-back).
func (*GitHub) Comment(context.Context, string, string, string) error { return errNotImplemented }

// OpenChange is implemented in M5 (SCM write-back).
func (*GitLab) OpenChange(context.Context, string, string, string, string, string, string) (string, error) {
	return "", errNotImplemented
}

// Comment is implemented in M5 (SCM write-back).
func (*GitLab) Comment(context.Context, string, string, string) error { return errNotImplemented }
```
- [ ] Run `go test ./internal/scm/...` -> EXPECT FAIL still (DetectAndVerify not yet declared on the structs, so `Client` is not satisfied - it is declared in Tasks 2 and 3). To make Task 1 self-contained and green, add temporary `DetectAndVerify` placeholders now and replace their bodies in Tasks 2/3. Add to `scm.go`:
```go
// DetectAndVerify is implemented per provider in github.go / gitlab.go.
func (*GitHub) DetectAndVerify(http.Header, []byte, string) (WebhookEvent, error) {
	return WebhookEvent{}, errNotImplemented
}

func (*GitLab) DetectAndVerify(http.Header, []byte, string) (WebhookEvent, error) {
	return WebhookEvent{}, errNotImplemented
}
```
Then re-run `go test ./internal/scm/...` -> EXPECT PASS.
- [ ] Note for Tasks 2/3: move each `DetectAndVerify` into `github.go`/`gitlab.go` and delete the placeholder from `scm.go` in the same task (one declaration only; a duplicate method is a compile error - this is the signal the move happened correctly).
- [ ] Commit: `feat(scm): Client interface, WebhookEvent, M5 not-implemented stubs`.

### Task 2: github DetectAndVerify (signature + push/issue/PR parsing)

**Files:** `internal/scm/github.go`, `internal/scm/github_test.go`

- [ ] Failing test - real signed fixtures, HMAC computed in the test. Write `internal/scm/github_test.go` in full:
```go
package scm

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func ghSig(secret string, body []byte) string {
	m := hmac.New(sha256.New, []byte(secret))
	m.Write(body)
	return "sha256=" + hex.EncodeToString(m.Sum(nil))
}

func ghHeader(event, secret string, body []byte) http.Header {
	h := http.Header{}
	h.Set("X-GitHub-Event", event)
	h.Set("X-Hub-Signature-256", ghSig(secret, body))
	return h
}

func TestGitHubDetectAndVerify(t *testing.T) {
	const secret = "s3cr3t"
	pushBody := []byte(`{"ref":"refs/heads/main","after":"abc123","repository":{"clone_url":"https://github.com/o/r.git"}}`)
	issueBody := []byte(`{"action":"opened","issue":{"number":7,"title":"Fix bug","body":"do it","labels":[{"name":"tatara"},{"name":"bug"}],"html_url":"https://github.com/o/r/issues/7"},"repository":{"clone_url":"https://github.com/o/r.git","full_name":"o/r"}}`)
	prBody := []byte(`{"action":"opened","pull_request":{"number":9,"title":"PR title","body":"pr body","labels":[{"name":"tatara"}],"html_url":"https://github.com/o/r/pull/9"},"repository":{"clone_url":"https://github.com/o/r.git","full_name":"o/r"}}`)

	tests := []struct {
		name  string
		event string
		body  []byte
		want  WebhookEvent
	}{
		{"push", "push", pushBody, WebhookEvent{Kind: "push", Repo: "https://github.com/o/r.git", Branch: "main"}},
		{"issue", "issues", issueBody, WebhookEvent{Kind: "issue", Repo: "https://github.com/o/r.git", Labels: []string{"tatara", "bug"}, Title: "Fix bug", Body: "do it", IssueRef: "o/r#7", URL: "https://github.com/o/r/issues/7"}},
		{"pr", "pull_request", prBody, WebhookEvent{Kind: "mr", Repo: "https://github.com/o/r.git", Labels: []string{"tatara"}, Title: "PR title", Body: "pr body", IssueRef: "o/r#9", URL: "https://github.com/o/r/pull/9"}},
		{"other", "ping", []byte(`{}`), WebhookEvent{Kind: "other"}},
	}
	c := &GitHub{}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := c.DetectAndVerify(ghHeader(tt.event, secret, tt.body), tt.body, secret)
			require.NoError(t, err)
			require.Equal(t, tt.want, got)
		})
	}
}

func TestGitHubBadSignature(t *testing.T) {
	const secret = "s3cr3t"
	body := []byte(`{"ref":"refs/heads/main","after":"x","repository":{"clone_url":"https://github.com/o/r.git"}}`)
	h := http.Header{}
	h.Set("X-GitHub-Event", "push")
	h.Set("X-Hub-Signature-256", ghSig("wrong", body))
	_, err := (&GitHub{}).DetectAndVerify(h, body, secret)
	require.Error(t, err)
}

func TestGitHubMissingSignature(t *testing.T) {
	body := []byte(`{}`)
	h := http.Header{}
	h.Set("X-GitHub-Event", "push")
	_, err := (&GitHub{}).DetectAndVerify(h, body, "s")
	require.Error(t, err)
}
```
- [ ] Run `go test ./internal/scm/... -run GitHub` -> EXPECT FAIL (DetectAndVerify is the placeholder returning not-implemented).
- [ ] Minimal impl - move `DetectAndVerify` into `internal/scm/github.go` and delete the placeholder from `scm.go`. Write `internal/scm/github.go` in full:
```go
package scm

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
)

type ghLabel struct {
	Name string `json:"name"`
}

type ghWorkItem struct {
	Number  int       `json:"number"`
	Title   string    `json:"title"`
	Body    string    `json:"body"`
	Labels  []ghLabel `json:"labels"`
	HTMLURL string    `json:"html_url"`
}

type ghPayload struct {
	Ref        string `json:"ref"`
	After      string `json:"after"`
	Repository struct {
		CloneURL string `json:"clone_url"`
		FullName string `json:"full_name"`
	} `json:"repository"`
	Issue       *ghWorkItem `json:"issue"`
	PullRequest *ghWorkItem `json:"pull_request"`
}

// DetectAndVerify verifies the X-Hub-Signature-256 HMAC and parses the payload.
func (*GitHub) DetectAndVerify(h http.Header, payload []byte, secret string) (WebhookEvent, error) {
	if err := verifyGitHubSig(h.Get("X-Hub-Signature-256"), payload, secret); err != nil {
		return WebhookEvent{}, err
	}
	event := h.Get("X-GitHub-Event")
	var p ghPayload
	if err := json.Unmarshal(payload, &p); err != nil {
		return WebhookEvent{}, fmt.Errorf("github: parse payload: %w", err)
	}
	switch event {
	case "push":
		return WebhookEvent{Kind: "push", Repo: p.Repository.CloneURL, Branch: strings.TrimPrefix(p.Ref, "refs/heads/")}, nil
	case "issues":
		return ghWorkItemEvent("issue", p.Repository.FullName, p.Repository.CloneURL, p.Issue), nil
	case "pull_request":
		return ghWorkItemEvent("mr", p.Repository.FullName, p.Repository.CloneURL, p.PullRequest), nil
	default:
		return WebhookEvent{Kind: "other"}, nil
	}
}

func ghWorkItemEvent(kind, fullName, cloneURL string, wi *ghWorkItem) WebhookEvent {
	if wi == nil {
		return WebhookEvent{Kind: "other"}
	}
	labels := make([]string, 0, len(wi.Labels))
	for _, l := range wi.Labels {
		labels = append(labels, l.Name)
	}
	return WebhookEvent{
		Kind:     kind,
		Repo:     cloneURL,
		Labels:   labels,
		Title:    wi.Title,
		Body:     wi.Body,
		IssueRef: fmt.Sprintf("%s#%d", fullName, wi.Number),
		URL:      wi.HTMLURL,
	}
}

func verifyGitHubSig(header string, payload []byte, secret string) error {
	if header == "" {
		return errors.New("github: missing X-Hub-Signature-256")
	}
	want, ok := strings.CutPrefix(header, "sha256=")
	if !ok {
		return errors.New("github: malformed signature")
	}
	m := hmac.New(sha256.New, []byte(secret))
	m.Write(payload)
	got := hex.EncodeToString(m.Sum(nil))
	if !hmac.Equal([]byte(got), []byte(want)) {
		return errors.New("github: signature mismatch")
	}
	return nil
}
```
Delete the `func (*GitHub) DetectAndVerify(...)` placeholder from `scm.go`.
- [ ] Run `go test ./internal/scm/... -run GitHub` -> EXPECT PASS. Run `go test ./internal/scm/...` -> EXPECT PASS (Task 1 tests still green).
- [ ] Commit: `feat(scm): github DetectAndVerify with HMAC-256 verify and push/issue/pr parsing`.

### Task 3: gitlab DetectAndVerify (token + push/issue/MR parsing)

**Files:** `internal/scm/gitlab.go`, `internal/scm/gitlab_test.go`

- [ ] Failing test - write `internal/scm/gitlab_test.go` in full:
```go
package scm

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func glHeader(event, token string) http.Header {
	h := http.Header{}
	h.Set("X-Gitlab-Event", event)
	h.Set("X-Gitlab-Token", token)
	return h
}

func TestGitLabDetectAndVerify(t *testing.T) {
	const secret = "glt0ken"
	pushBody := []byte(`{"ref":"refs/heads/main","project":{"git_http_url":"https://gitlab.com/g/p.git","path_with_namespace":"g/p"}}`)
	issueBody := []byte(`{"object_kind":"issue","project":{"git_http_url":"https://gitlab.com/g/p.git","path_with_namespace":"g/p"},"object_attributes":{"iid":12,"title":"An issue","description":"desc","url":"https://gitlab.com/g/p/-/issues/12"},"labels":[{"title":"tatara"},{"title":"ops"}]}`)
	mrBody := []byte(`{"object_kind":"merge_request","project":{"git_http_url":"https://gitlab.com/g/p.git","path_with_namespace":"g/p"},"object_attributes":{"iid":34,"title":"An MR","description":"mr desc","url":"https://gitlab.com/g/p/-/merge_requests/34"},"labels":[{"title":"tatara"}]}`)

	tests := []struct {
		name  string
		event string
		body  []byte
		want  WebhookEvent
	}{
		{"push", "Push Hook", pushBody, WebhookEvent{Kind: "push", Repo: "https://gitlab.com/g/p.git", Branch: "main"}},
		{"issue", "Issue Hook", issueBody, WebhookEvent{Kind: "issue", Repo: "https://gitlab.com/g/p.git", Labels: []string{"tatara", "ops"}, Title: "An issue", Body: "desc", IssueRef: "g/p!12", URL: "https://gitlab.com/g/p/-/issues/12"}},
		{"mr", "Merge Request Hook", mrBody, WebhookEvent{Kind: "mr", Repo: "https://gitlab.com/g/p.git", Labels: []string{"tatara"}, Title: "An MR", Body: "mr desc", IssueRef: "g/p!34", URL: "https://gitlab.com/g/p/-/merge_requests/34"}},
		{"other", "Pipeline Hook", []byte(`{}`), WebhookEvent{Kind: "other"}},
	}
	c := &GitLab{}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := c.DetectAndVerify(glHeader(tt.event, secret), tt.body, secret)
			require.NoError(t, err)
			require.Equal(t, tt.want, got)
		})
	}
}

func TestGitLabBadToken(t *testing.T) {
	body := []byte(`{"ref":"refs/heads/main","project":{"git_http_url":"https://gitlab.com/g/p.git"}}`)
	_, err := (&GitLab{}).DetectAndVerify(glHeader("Push Hook", "wrong"), body, "glt0ken")
	require.Error(t, err)
}

func TestGitLabMissingToken(t *testing.T) {
	h := http.Header{}
	h.Set("X-Gitlab-Event", "Push Hook")
	_, err := (&GitLab{}).DetectAndVerify(h, []byte(`{}`), "glt0ken")
	require.Error(t, err)
}
```
- [ ] Run `go test ./internal/scm/... -run GitLab` -> EXPECT FAIL (placeholder).
- [ ] Minimal impl - move `DetectAndVerify` into `internal/scm/gitlab.go` and delete the placeholder from `scm.go`. Write `internal/scm/gitlab.go` in full:
```go
package scm

import (
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
)

type glLabel struct {
	Title string `json:"title"`
}

type glPayload struct {
	ObjectKind string `json:"object_kind"`
	Ref        string `json:"ref"`
	Project    struct {
		GitHTTPURL        string `json:"git_http_url"`
		PathWithNamespace string `json:"path_with_namespace"`
	} `json:"project"`
	ObjectAttributes struct {
		IID         int    `json:"iid"`
		Title       string `json:"title"`
		Description string `json:"description"`
		URL         string `json:"url"`
	} `json:"object_attributes"`
	Labels []glLabel `json:"labels"`
}

// DetectAndVerify verifies the X-Gitlab-Token and parses the payload.
func (*GitLab) DetectAndVerify(h http.Header, payload []byte, secret string) (WebhookEvent, error) {
	token := h.Get("X-Gitlab-Token")
	if token == "" {
		return WebhookEvent{}, errors.New("gitlab: missing X-Gitlab-Token")
	}
	if subtle.ConstantTimeCompare([]byte(token), []byte(secret)) != 1 {
		return WebhookEvent{}, errors.New("gitlab: token mismatch")
	}
	var p glPayload
	if err := json.Unmarshal(payload, &p); err != nil {
		return WebhookEvent{}, fmt.Errorf("gitlab: parse payload: %w", err)
	}
	switch h.Get("X-Gitlab-Event") {
	case "Push Hook":
		return WebhookEvent{Kind: "push", Repo: p.Project.GitHTTPURL, Branch: trimGitLabRef(p.Ref)}, nil
	case "Issue Hook":
		return glWorkItemEvent("issue", p), nil
	case "Merge Request Hook":
		return glWorkItemEvent("mr", p), nil
	default:
		return WebhookEvent{Kind: "other"}, nil
	}
}

func trimGitLabRef(ref string) string {
	const prefix = "refs/heads/"
	if len(ref) > len(prefix) && ref[:len(prefix)] == prefix {
		return ref[len(prefix):]
	}
	return ref
}

func glWorkItemEvent(kind string, p glPayload) WebhookEvent {
	labels := make([]string, 0, len(p.Labels))
	for _, l := range p.Labels {
		labels = append(labels, l.Title)
	}
	return WebhookEvent{
		Kind:     kind,
		Repo:     p.Project.GitHTTPURL,
		Labels:   labels,
		Title:    p.ObjectAttributes.Title,
		Body:     p.ObjectAttributes.Description,
		IssueRef: fmt.Sprintf("%s!%d", p.Project.PathWithNamespace, p.ObjectAttributes.IID),
		URL:      p.ObjectAttributes.URL,
	}
}
```
Delete the `func (*GitLab) DetectAndVerify(...)` placeholder from `scm.go`.
- [ ] Run `go test ./internal/scm/... -run GitLab` -> EXPECT PASS. Run `go test ./internal/scm/...` -> EXPECT PASS.
- [ ] Commit: `feat(scm): gitlab DetectAndVerify with token verify and push/issue/mr parsing`.

### Task 4: registry (provider selection by header) + remote-URL match helper

**Files:** `internal/scm/registry.go`, `internal/scm/registry_test.go`

- [ ] Failing test - write `internal/scm/registry_test.go` in full:
```go
package scm

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSelectByHeader(t *testing.T) {
	gh := http.Header{}
	gh.Set("X-GitHub-Event", "push")
	c, err := Select(gh)
	require.NoError(t, err)
	require.Equal(t, "github", c.Provider())

	gl := http.Header{}
	gl.Set("X-Gitlab-Event", "Push Hook")
	c, err = Select(gl)
	require.NoError(t, err)
	require.Equal(t, "gitlab", c.Provider())

	_, err = Select(http.Header{})
	require.Error(t, err)
}

func TestSameRemote(t *testing.T) {
	tests := []struct {
		a, b string
		want bool
	}{
		{"https://github.com/o/r.git", "https://github.com/o/r", true},
		{"https://github.com/o/r/", "https://github.com/o/r.git", true},
		{"https://GitHub.com/o/r.git", "https://github.com/o/r", true},
		{"https://github.com/o/r.git", "https://github.com/o/other.git", false},
		{"https://gitlab.com/g/p.git", "https://gitlab.com/g/p", true},
	}
	for _, tt := range tests {
		require.Equal(t, tt.want, SameRemote(tt.a, tt.b), "%s vs %s", tt.a, tt.b)
	}
}
```
- [ ] Run `go test ./internal/scm/... -run "TestSelectByHeader|TestSameRemote"` -> EXPECT FAIL (undefined `Select`, `SameRemote`).
- [ ] Minimal impl - write `internal/scm/registry.go` in full:
```go
package scm

import (
	"errors"
	"net/http"
	"net/url"
	"strings"
)

// Select returns the Client for the provider indicated by request headers.
func Select(h http.Header) (Client, error) {
	switch {
	case h.Get("X-GitHub-Event") != "":
		return &GitHub{}, nil
	case h.Get("X-Gitlab-Event") != "":
		return &GitLab{}, nil
	default:
		return nil, errors.New("scm: unrecognized provider headers")
	}
}

// SameRemote reports whether two git remote URLs refer to the same repository,
// ignoring a trailing .git or /, and lowercasing the host.
func SameRemote(a, b string) bool {
	na, ok1 := normalizeRemote(a)
	nb, ok2 := normalizeRemote(b)
	if !ok1 || !ok2 {
		return false
	}
	return na == nb
}

func normalizeRemote(raw string) (string, bool) {
	u, err := url.Parse(raw)
	if err != nil {
		return "", false
	}
	path := strings.TrimSuffix(u.Path, "/")
	path = strings.TrimSuffix(path, ".git")
	return strings.ToLower(u.Host) + path, true
}
```
- [ ] Run `go test ./internal/scm/...` -> EXPECT PASS.
- [ ] Commit: `feat(scm): provider registry Select + SameRemote URL matcher`.

### Task 5: webhook server (handler: load Project, verify, dispatch)

**Files:** `internal/webhook/server.go`, `internal/webhook/server_test.go`

This is the integration core. The handler depends on a controller-runtime client (fake in tests), the obs webhook counter, and `internal/scm`. It reads the M1 re-ingest annotation constant - grep `reingest-requested` in the repo and import the existing constant; do NOT redefine it. If M1 did not export one, the boy-scout fix (rule 3) is to add `const ReingestRequestedAnnotation = "tatara.dev/reingest-requested"` to `api/v1alpha1` and use it from both M1 and here; note that in `MEMORY.md`.

- [ ] Failing test - signed payloads built in the test, fake client seeded with Project + Repository, assertions on annotation mutation and status codes. Write `internal/webhook/server_test.go` in full:
```go
package webhook_test

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/stretchr/testify/require"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	tatarav1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/webhook"
)

const ns = "tatara"

func ghSign(secret string, body []byte) string {
	m := hmac.New(sha256.New, []byte(secret))
	m.Write(body)
	return "sha256=" + hex.EncodeToString(m.Sum(nil))
}

func newScheme(t *testing.T) *runtime.Scheme {
	s := runtime.NewScheme()
	require.NoError(t, corev1.AddToScheme(s))
	require.NoError(t, tatarav1.AddToScheme(s))
	return s
}

func seedClient(t *testing.T, objs ...client.Object) client.Client {
	return fake.NewClientBuilder().WithScheme(newScheme(t)).WithObjects(objs...).Build()
}

func project(name, secretRef, trigger string) *tatarav1.Project {
	return &tatarav1.Project{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: ns},
		Spec:       tatarav1.ProjectSpec{ScmSecretRef: secretRef, TriggerLabel: trigger},
	}
}

func secret(name, webhookSecret string) *corev1.Secret {
	return &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: ns},
		Data:       map[string][]byte{"webhookSecret": []byte(webhookSecret), "token": []byte("pat")},
	}
}

func repository(name, projectRef, url, branch string) *tatarav1.Repository {
	return &tatarav1.Repository{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: ns},
		Spec:       tatarav1.RepositorySpec{ProjectRef: projectRef, URL: url, DefaultBranch: branch},
	}
}

func newServer(t *testing.T, c client.Client) (http.Handler, *prometheus.Registry) {
	reg := prometheus.NewRegistry()
	srv := webhook.NewServer(webhook.Config{
		Client:    c,
		Namespace: ns,
		Metrics:   obs.NewMetrics(reg),
	})
	return srv.Handler(), reg
}

func post(t *testing.T, h http.Handler, project string, hdr http.Header, body []byte) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/operator/webhooks/"+project, strings.NewReader(string(body)))
	for k, vs := range hdr {
		for _, v := range vs {
			req.Header.Add(k, v)
		}
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	return w
}

func TestPushSetsReingestAnnotation(t *testing.T) {
	const secretVal = "whsec"
	c := seedClient(t,
		project("proj1", "proj1-scm", "tatara"),
		secret("proj1-scm", secretVal),
		repository("repo1", "proj1", "https://github.com/o/r.git", "main"),
	)
	h, reg := newServer(t, c)

	body := []byte(`{"ref":"refs/heads/main","after":"sha1","repository":{"clone_url":"https://github.com/o/r.git"}}`)
	hdr := http.Header{}
	hdr.Set("X-GitHub-Event", "push")
	hdr.Set("X-Hub-Signature-256", ghSign(secretVal, body))

	w := post(t, h, "proj1", hdr, body)
	require.Equal(t, http.StatusAccepted, w.Code)

	var got tatarav1.Repository
	require.NoError(t, c.Get(context.Background(), client.ObjectKey{Namespace: ns, Name: "repo1"}, &got))
	ts := got.Annotations[tatarav1.ReingestRequestedAnnotation]
	require.NotEmpty(t, ts)
	_, err := time.Parse(time.RFC3339, ts)
	require.NoError(t, err)

	require.Equal(t, 1.0, counterValue(t, reg, "operator_webhook_events_total", map[string]string{"provider": "github", "kind": "push", "result": "accepted"}))
}

func TestPushNonDefaultBranchNoMutation(t *testing.T) {
	const secretVal = "whsec"
	c := seedClient(t,
		project("proj1", "proj1-scm", "tatara"),
		secret("proj1-scm", secretVal),
		repository("repo1", "proj1", "https://github.com/o/r.git", "main"),
	)
	h, _ := newServer(t, c)
	body := []byte(`{"ref":"refs/heads/feature","after":"sha2","repository":{"clone_url":"https://github.com/o/r.git"}}`)
	hdr := http.Header{}
	hdr.Set("X-GitHub-Event", "push")
	hdr.Set("X-Hub-Signature-256", ghSign(secretVal, body))

	w := post(t, h, "proj1", hdr, body)
	require.Equal(t, http.StatusAccepted, w.Code)
	var got tatarav1.Repository
	require.NoError(t, c.Get(context.Background(), client.ObjectKey{Namespace: ns, Name: "repo1"}, &got))
	require.Empty(t, got.Annotations[tatarav1.ReingestRequestedAnnotation])
}

func TestIssueWithTriggerLabelStubbed(t *testing.T) {
	const secretVal = "whsec"
	c := seedClient(t,
		project("proj1", "proj1-scm", "tatara"),
		secret("proj1-scm", secretVal),
		repository("repo1", "proj1", "https://github.com/o/r.git", "main"),
	)
	h, reg := newServer(t, c)
	body := []byte(`{"action":"opened","issue":{"number":7,"title":"t","body":"b","labels":[{"name":"tatara"}],"html_url":"https://github.com/o/r/issues/7"},"repository":{"clone_url":"https://github.com/o/r.git","full_name":"o/r"}}`)
	hdr := http.Header{}
	hdr.Set("X-GitHub-Event", "issues")
	hdr.Set("X-Hub-Signature-256", ghSign(secretVal, body))

	w := post(t, h, "proj1", hdr, body)
	require.Equal(t, http.StatusAccepted, w.Code)
	// M2 stub: no Task created. Verify no Task objects exist.
	var tasks tatarav1.TaskList
	require.NoError(t, c.List(context.Background(), &tasks, client.InNamespace(ns)))
	require.Empty(t, tasks.Items)
	require.Equal(t, 1.0, counterValue(t, reg, "operator_webhook_events_total", map[string]string{"provider": "github", "kind": "issue", "result": "accepted"}))
}

func TestUnknownProject404(t *testing.T) {
	c := seedClient(t)
	h, reg := newServer(t, c)
	body := []byte(`{"ref":"refs/heads/main","after":"x","repository":{"clone_url":"https://github.com/o/r.git"}}`)
	hdr := http.Header{}
	hdr.Set("X-GitHub-Event", "push")
	hdr.Set("X-Hub-Signature-256", ghSign("whatever", body))
	w := post(t, h, "ghost", hdr, body)
	require.Equal(t, http.StatusNotFound, w.Code)
	require.Equal(t, 1.0, counterValue(t, reg, "operator_webhook_events_total", map[string]string{"provider": "github", "kind": "other", "result": "unknown_project"}))
}

func TestBadSignature401NoMutation(t *testing.T) {
	const secretVal = "whsec"
	c := seedClient(t,
		project("proj1", "proj1-scm", "tatara"),
		secret("proj1-scm", secretVal),
		repository("repo1", "proj1", "https://github.com/o/r.git", "main"),
	)
	h, reg := newServer(t, c)
	body := []byte(`{"ref":"refs/heads/main","after":"x","repository":{"clone_url":"https://github.com/o/r.git"}}`)
	hdr := http.Header{}
	hdr.Set("X-GitHub-Event", "push")
	hdr.Set("X-Hub-Signature-256", ghSign("wrong", body))
	w := post(t, h, "proj1", hdr, body)
	require.Equal(t, http.StatusUnauthorized, w.Code)
	var got tatarav1.Repository
	require.NoError(t, c.Get(context.Background(), client.ObjectKey{Namespace: ns, Name: "repo1"}, &got))
	require.Empty(t, got.Annotations[tatarav1.ReingestRequestedAnnotation])
	require.Equal(t, 1.0, counterValue(t, reg, "operator_webhook_events_total", map[string]string{"provider": "github", "kind": "other", "result": "bad_signature"}))
}
```
- [ ] Add the missing test imports at the top of the file (the writer of this test added them while iterating; the final import block must include): `"context"`, `"k8s.io/apimachinery/pkg/runtime"`, `"github.com/szymonrychu/tatara-operator/internal/obs"`, plus a `counterValue` helper. Add the helper to `internal/webhook/server_test.go`:
```go
func counterValue(t *testing.T, reg *prometheus.Registry, name string, labels map[string]string) float64 {
	mfs, err := reg.Gather()
	require.NoError(t, err)
	for _, mf := range mfs {
		if mf.GetName() != name {
			continue
		}
		for _, m := range mf.GetMetric() {
			match := true
			for _, lp := range m.GetLabel() {
				if want, ok := labels[lp.GetName()]; ok && want != lp.GetValue() {
					match = false
				}
			}
			if match && len(m.GetLabel()) == len(labels) {
				return m.GetCounter().GetValue()
			}
		}
	}
	return 0
}
```
- [ ] Run `go test ./internal/webhook/...` -> EXPECT FAIL (undefined `webhook.NewServer`, `webhook.Config`, `obs.NewMetrics`/its webhook counter accessor - confirm the M0 obs API name and adjust the test's `Metrics:` field and `counterValue` metric name to match the registered CounterVec). Resolve any obs API mismatch in the test FIRST (the metric name `operator_webhook_events_total` and labels `{provider,kind,result}` are pinned and must not change).
- [ ] Minimal impl - write `internal/webhook/server.go` in full. The handler flow: parse `{project}` -> `Get` Project (404 if missing) -> `Get` the `scmSecretRef` Secret + read `webhookSecret` -> `scm.Select(headers)` (other/400 if unrecognized) -> `DetectAndVerify` (401 on error) -> dispatch on `ev.Kind`. For `push`: list Repositories in the namespace whose `Spec.ProjectRef == project`, find one where `SameRemote(repo.Spec.URL, ev.Repo)` AND `ev.Branch == repo.Spec.DefaultBranch`; if found, set the annotation to `time.Now().UTC().Format(time.RFC3339)` and `Update`. For `issue`/`mr`: if `ev.Labels` contains `project.Spec.TriggerLabel`, log + count (M5 will create the Task here - explicit stub); else count as ignored. Always increment the counter with the final result label. Return 202 for all accepted paths.
```go
package webhook

import (
	"errors"
	"log/slog"
	"net/http"
	"slices"
	"time"

	"github.com/go-chi/chi/v5"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	corev1 "k8s.io/api/core/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"

	tatarav1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/obs"
	"github.com/szymonrychu/tatara-operator/internal/scm"
)

// Config holds webhook server dependencies.
type Config struct {
	Client    client.Client
	Namespace string
	Metrics   *obs.Metrics
	Logger    *slog.Logger
}

// Server serves the SCM webhook endpoint.
type Server struct {
	cfg Config
	log *slog.Logger
}

// NewServer constructs a webhook Server.
func NewServer(cfg Config) *Server {
	if cfg.Logger == nil {
		cfg.Logger = slog.Default()
	}
	return &Server{cfg: cfg, log: cfg.Logger}
}

// Handler returns the chi router mounting the webhook endpoint.
func (s *Server) Handler() http.Handler {
	r := chi.NewRouter()
	r.Post("/operator/webhooks/{project}", s.handle)
	return r
}

func (s *Server) handle(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	projectName := chi.URLParam(r, "project")

	body, err := readBody(r)
	if err != nil {
		s.count("unknown", "other", "bad_request")
		http.Error(w, "read body", http.StatusBadRequest)
		return
	}

	client, err := scm.Select(r.Header)
	if err != nil {
		s.count("unknown", "other", "bad_request")
		http.Error(w, "unrecognized provider", http.StatusBadRequest)
		return
	}
	provider := client.Provider()

	var proj tatarav1.Project
	if err := s.cfg.Client.Get(ctx, key(s.cfg.Namespace, projectName), &proj); err != nil {
		if apierrors.IsNotFound(err) {
			s.count(provider, "other", "unknown_project")
			http.Error(w, "unknown project", http.StatusNotFound)
			return
		}
		s.count(provider, "other", "error")
		http.Error(w, "lookup project", http.StatusInternalServerError)
		return
	}

	secret, err := s.webhookSecret(ctx, proj.Spec.ScmSecretRef)
	if err != nil {
		s.count(provider, "other", "error")
		http.Error(w, "secret", http.StatusInternalServerError)
		return
	}

	ev, err := client.DetectAndVerify(r.Header, body, secret)
	if err != nil {
		s.count(provider, "other", "bad_signature")
		http.Error(w, "verification failed", http.StatusUnauthorized)
		return
	}

	switch ev.Kind {
	case "push":
		s.handlePush(ctx, w, provider, projectName, ev)
	case "issue", "mr":
		s.handleWorkItem(w, provider, proj, ev)
	default:
		s.count(provider, "other", "ignored")
		w.WriteHeader(http.StatusAccepted)
	}
}

func (s *Server) handlePush(ctx context.Context, w http.ResponseWriter, provider, projectName string, ev scm.WebhookEvent) {
	var repos tatarav1.RepositoryList
	if err := s.cfg.Client.List(ctx, &repos, client.InNamespace(s.cfg.Namespace)); err != nil {
		s.count(provider, "push", "error")
		http.Error(w, "list repositories", http.StatusInternalServerError)
		return
	}
	for i := range repos.Items {
		repo := &repos.Items[i]
		if repo.Spec.ProjectRef != projectName {
			continue
		}
		if !scm.SameRemote(repo.Spec.URL, ev.Repo) || ev.Branch != repo.Spec.DefaultBranch {
			continue
		}
		if repo.Annotations == nil {
			repo.Annotations = map[string]string{}
		}
		repo.Annotations[tatarav1.ReingestRequestedAnnotation] = time.Now().UTC().Format(time.RFC3339)
		if err := s.cfg.Client.Update(ctx, repo); err != nil {
			s.count(provider, "push", "error")
			http.Error(w, "annotate repository", http.StatusInternalServerError)
			return
		}
		s.log.InfoContext(ctx, "webhook push re-ingest requested", "provider", provider, "project", projectName, "repository", repo.Name, "branch", ev.Branch)
		s.count(provider, "push", "accepted")
		w.WriteHeader(http.StatusAccepted)
		return
	}
	s.count(provider, "push", "ignored")
	w.WriteHeader(http.StatusAccepted)
}

func (s *Server) handleWorkItem(w http.ResponseWriter, provider string, proj tatarav1.Project, ev scm.WebhookEvent) {
	if !slices.Contains(ev.Labels, proj.Spec.TriggerLabel) {
		s.count(provider, ev.Kind, "ignored")
		w.WriteHeader(http.StatusAccepted)
		return
	}
	// M2 stub: a labeled work item is accepted and logged, but Task creation
	// is deliberately NOT performed here. M5 (work-item -> Task) completes this
	// branch by creating a Task with goal=ev.Body and source from ev.
	s.log.Info("webhook work item with trigger label (M2 stub, Task creation in M5)",
		"provider", provider, "project", proj.Name, "kind", ev.Kind, "issue_ref", ev.IssueRef)
	s.count(provider, ev.Kind, "accepted")
	w.WriteHeader(http.StatusAccepted)
}

func (s *Server) webhookSecret(ctx context.Context, ref string) (string, error) {
	var sec corev1.Secret
	if err := s.cfg.Client.Get(ctx, key(s.cfg.Namespace, ref), &sec); err != nil {
		return "", err
	}
	v, ok := sec.Data["webhookSecret"]
	if !ok {
		return "", errors.New("secret missing webhookSecret key")
	}
	return string(v), nil
}

func (s *Server) count(provider, kind, result string) {
	s.cfg.Metrics.WebhookEvents.WithLabelValues(provider, kind, result).Inc()
}

func key(ns, name string) client.ObjectKey {
	return client.ObjectKey{Namespace: ns, Name: name}
}
```
- [ ] Add the remaining imports the impl needs (`"context"`, `"io"` for `readBody`) and a small `readBody` helper in `server.go`:
```go
func readBody(r *http.Request) ([]byte, error) {
	defer r.Body.Close()
	return io.ReadAll(io.LimitReader(r.Body, 5<<20))
}
```
- [ ] Reconcile the obs accessor: the impl uses `s.cfg.Metrics.WebhookEvents` (a `*prometheus.CounterVec`). Confirm M0's `obs.Metrics` exposes the webhook counter as a field; if the field name differs, use M0's actual name in both `server.go` and the test (do not rename the metric or its labels). If M0 exposes only a registry + private metrics, the boy-scout fix is to export the field; note it in `MEMORY.md`.
- [ ] Run `go test ./internal/webhook/...` -> EXPECT PASS (all six cases). Run `go test ./internal/scm/...` -> EXPECT PASS.
- [ ] Commit: `feat(webhook): server handler - verify, push annotation, work-item M5 stub, metrics`.

### Task 6: wire webhook server into cmd/manager/main.go

**Files:** `cmd/manager/main.go` (modify), `internal/webhook/server_run_test.go`

The manager already builds a controller-runtime `manager.Manager`. Add the webhook HTTP server as a goroutine bound to `HTTP_ADDR`, sharing `mgr.GetClient()`. controller-runtime exposes `mgr.Add(manager.Runnable)`; prefer registering the HTTP server as a `Runnable` so it shares the manager's lifecycle and graceful shutdown rather than a raw `go` (cleaner, no leaked goroutine on shutdown - this is the pick).

- [ ] Failing test - assert that `NewServer(...).Handler()` returns a non-nil handler and that a `Runnable` wrapper starts/stops on context cancel. Write `internal/webhook/server_run_test.go` in full:
```go
package webhook_test

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-operator/internal/obs"
	"github.com/szymonrychu/tatara-operator/internal/webhook"
)

func TestRunnableStartStop(t *testing.T) {
	c := seedClient(t)
	srv := webhook.NewServer(webhook.Config{Client: c, Namespace: ns, Metrics: obs.NewMetrics(prometheus.NewRegistry())})
	r := webhook.NewRunnable(srv, "127.0.0.1:0")

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- r.Start(ctx) }()
	time.Sleep(50 * time.Millisecond)
	cancel()
	select {
	case err := <-done:
		require.NoError(t, err)
	case <-time.After(2 * time.Second):
		t.Fatal("runnable did not stop on context cancel")
	}

	// sanity: handler still serves
	w := httptest.NewRecorder()
	srv.Handler().ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/operator/webhooks/x", nil))
	require.Equal(t, http.StatusMethodNotAllowed, w.Code)
}
```
- [ ] Run `go test ./internal/webhook/... -run TestRunnableStartStop` -> EXPECT FAIL (undefined `webhook.NewRunnable`).
- [ ] Minimal impl - add a `Runnable` to `internal/webhook/server.go` (or a new `internal/webhook/runnable.go`) implementing controller-runtime's `manager.Runnable` (`Start(ctx) error`). It starts an `http.Server` with `Handler()` on the given addr and shuts down on `ctx.Done()`:
```go
// Runnable adapts the webhook Server to controller-runtime's manager.Runnable.
type Runnable struct {
	srv  *Server
	addr string
}

// NewRunnable wraps a Server so it can be registered with mgr.Add.
func NewRunnable(srv *Server, addr string) *Runnable {
	return &Runnable{srv: srv, addr: addr}
}

// Start serves HTTP until ctx is cancelled, then gracefully shuts down.
func (r *Runnable) Start(ctx context.Context) error {
	httpSrv := &http.Server{Addr: r.addr, Handler: r.srv.Handler(), ReadHeaderTimeout: 10 * time.Second}
	errCh := make(chan error, 1)
	go func() {
		if err := httpSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errCh <- err
			return
		}
		errCh <- nil
	}()
	select {
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		return httpSrv.Shutdown(shutdownCtx)
	case err := <-errCh:
		return err
	}
}
```
- [ ] Run `go test ./internal/webhook/... -run TestRunnableStartStop` -> EXPECT PASS.
- [ ] Modify `cmd/manager/main.go`: after the manager is built and CRD schemes are registered, construct the metrics (reuse the obs metrics already created in M0 - share the same `*obs.Metrics` with the reconcilers), build `webhook.NewServer(webhook.Config{Client: mgr.GetClient(), Namespace: cfg.Namespace, Metrics: metrics, Logger: logger})`, and register `mgr.Add(webhook.NewRunnable(srv, cfg.HTTPAddr))`. Use the actual M0 config accessor for `HTTP_ADDR` and the namespace. Show only the added lines as a diff in the PR (the file already exists; rule: diffs not whole files). Do NOT duplicate the obs registry; the `/metrics` endpoint stays owned by the manager's metrics server (M0/M6).
- [ ] Run `go build ./...` -> EXPECT PASS. Run `go vet ./...` -> clean.
- [ ] Commit: `feat(cmd): run webhook server as a manager runnable on HTTP_ADDR`.

### Task 7: full verification + merge

**Files:** none new (verification + merge only)

- [ ] `superpowers:verification-before-completion`. Run the full suite and lint from the worktree:
  - `go build ./...` -> clean.
  - `go test ./... -race -count=1` -> all green (especially `./internal/scm/...` and `./internal/webhook/...`).
  - `golangci-lint run ./...` -> clean (errors wrapped with `%w` where created, no unused, JSON slog only per rule 11).
  - `gofmt -l .` -> empty.
- [ ] Confirm the pinned contract is honored: `scm.Client` signature matches the pin set byte-for-byte; metric is `operator_webhook_events_total{provider,kind,result}`; annotation key is `tatara.dev/reingest-requested`; path is `POST /operator/webhooks/{project}` on `HTTP_ADDR`; bad signature -> 401, unknown project -> 404, both with no CRD mutation; work-item path is a logged + metered 202 stub with NO Task created (M5 completes it).
- [ ] Update `MEMORY.md` (one line, dated 2026-06-06): record any boy-scout exports made (M1 annotation constant, obs webhook-counter field) and the SameRemote normalization rule. Update `ROADMAP.md`: mark M2 done, note M5 owns Task creation on the work-item path and the `scm` write half (`OpenChange`/`Comment`).
- [ ] `superpowers:requesting-code-review` (opus). Apply critical/high findings, re-run `go test ./... -race` and `golangci-lint run ./...`, then `pre-commit run --all-files`.
- [ ] `superpowers:finishing-a-development-branch`: merge `feat/m2-webhook-push` into `tatara-operator` `main`, delete the worktree. Do not build or deploy from the worktree (rule 10).
