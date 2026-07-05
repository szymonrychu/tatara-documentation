# SCM Projects, Proactive Issues, and PR/MR Reactions (tatara-operator) Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Dispatch implementation tasks to sonnet subagents; the merge that integrates parallel work runs in opus. Every task is TDD (superpowers:test-driven-development): write the failing test first, run it red, write minimal code, run it green, commit. Steps use checkbox (- [ ]) syntax.

**Goal**

Extend the operator's SCM egress so tatara participates on a project board (GitHub Projects v2 / GitLab issue board), proactively opens human-gated issues (`awaiting-approval`), and reacts to PRs/MRs conditionally on author: if tatara authored it tatara may improve / merge / close; if a human authored it tatara may only comment / suggest / approve / request-changes. A human is never silently mutated; tatara never merges unattended unless `autoMergeOnGreenCI` AND CI exists; every tatara-proposed item is gated behind a human-removable label. The operator is the SOLE caller of any SCM write.

**Architecture**

Ingress: `scm.Client.DetectAndVerify` parses inbound webhooks and now captures author/action/number/isPR/headSHA/headBranch/changedLabel. `webhook.Server` dispatch fans push/issue/PR/MR/review/comment events to handlers that select a Task `Kind` (`implement|review|selfImprove`), set `ApprovalRequired`, and apply `prReactionScope` gating. Egress: `controller.SCMFor` returns a widened `scm.SCMWriter` (the old 2-method `Writer`), implemented by `*GitHub` (REST v3 + a net-new Projects v2 GraphQL helper) and `*GitLab` (REST, label-driven boards). `TaskReconciler` adds an approval gate and a proposal-creation branch (the only place issues are created); `doWriteBack` branches on `Task.Spec.Kind`. Agents emit intent over three new operator REST endpoints (`POST /projects/{p}/issues`, `/tasks/{t}/review`, `/tasks/{t}/pr-outcome`) that ONLY write CRDs.

**Tech Stack**

Go 1.26.3 (`go.mod` directive pinned exact-minor), controller-runtime, chi v5, kubebuilder/controller-gen, prometheus/client_golang, stdlib `log/slog` (JSON), `net/http` + `net/http/httptest` for SCM fakes, envtest for controller flow. gofmt + golangci-lint clean. Table-driven tests with `t.Run`. Errors wrapped `fmt.Errorf("...: %w", err)`. Build/deploy from `main` only; develop in a worktree per superpowers:using-git-worktrees.

---

## File Structure

| File | Create/Modify | Responsibility |
| --- | --- | --- |
| `internal/scm/scm.go` | Modify | Add `WebhookEvent` fields; add `IssueReq`/`IssueRef`/`PRState`/`Suggestion`/`BoardRef` value types; add `SCMWriter` interface. |
| `internal/scm/github.go` | Modify | GitHub `IssueAuthor`+`Reviewer` REST v3 verbs; expand `DetectAndVerify`. |
| `internal/scm/github_graphql.go` | Create | Net-new Projects v2 GraphQL helper + GitHub `BoardManager` verbs. |
| `internal/scm/gitlab.go` | Modify | GitLab `IssueAuthor`+`Reviewer`+`BoardManager` REST verbs; expand `DetectAndVerify`. |
| `internal/scm/github_capabilities_test.go` | Create | httptest table tests for GitHub REST verbs + DetectAndVerify. |
| `internal/scm/github_graphql_test.go` | Create | httptest table tests for GitHub GraphQL board verbs. |
| `internal/scm/gitlab_capabilities_test.go` | Create | httptest table tests for GitLab REST verbs + board labels + DetectAndVerify. |
| `api/v1alpha1/project_types.go` | Modify | `ScmSpec`/`BoardSpec` + `ProjectSpec.Scm`. |
| `api/v1alpha1/task_types.go` | Modify | `ProposedIssueSpec`/`Suggestion`/`ReviewVerdict`/`PROutcome`; Task `Kind`/`ApprovalRequired`/`ProposedIssue`; `TaskSource` author/isPR/number; status fields + phase enum + condition const. |
| `api/v1alpha1/zz_generated.deepcopy.go` | Modify (generated) | `make generate` deepcopy for the new structs/pointers. |
| `charts/tatara-operator/crds/tatara.dev_projects.yaml` | Modify (generated) | `make manifests` regen with `scm` block. |
| `charts/tatara-operator/crds/tatara.dev_tasks.yaml` | Modify (generated) | `make manifests` regen with Kind/approval/proposed/status fields. |
| `internal/webhook/server.go` | Modify | Dispatch new events; Kind selection; approval-label flip; `prReactionScope` gating; `count` gains `action`. |
| `internal/webhook/server_test.go` | Modify | One test per new event/action asserting Kind, AuthorLogin, proposal/approval branches. |
| `internal/controller/writeback.go` | Modify | Widen `Writer`->`SCMWriter`; branch `doWriteBack` on `Spec.Kind`; review/selfImprove verb sets. |
| `internal/controller/task_controller.go` | Modify | Approval gate; proposal-creation branch; `SCMFor` returns `scm.SCMWriter`. |
| `internal/controller/writeback_test.go` | Modify | Per-Kind verb-set tests against a fake SCMWriter. |
| `internal/controller/approval_gate_test.go` | Create | envtest: gate holds in AwaitingApproval until ApprovalApproved; proposal creates Source; reject path. |
| `cmd/manager/wire.go` | Modify | `SCMFor` factory return type `scm.SCMWriter`. |
| `internal/restapi/server.go` | Modify | Mount the 3 new POST routes. |
| `internal/restapi/handlers.go` | Modify | `proposeIssue`/`reviewVerdict`/`prOutcome` handlers (CRD writes only). |
| `internal/restapi/dto.go` | Modify | DTO additions: kind/approvalRequired/discoveredIssues/reviewVerdict/prOutcome/source fields. |
| `internal/restapi/handlers_test.go` | Modify | Tests for the 3 new endpoints; assert no SCM call, CRD shape. |
| `internal/restapi/dto_test.go` | Modify | Assert new DTO fields round-trip. |
| `internal/obs/operator_metrics.go` | Modify | `WebhookEvent` gains `action`; new `operator_scm_writes_total`; new `operator_approval_gate_seconds`. |
| `internal/obs/operator_metrics_test.go` | Modify | Tests for the new label + counter + histogram. |
| `charts/tatara-operator/Chart.yaml` | Modify | Bump `version`/`appVersion`. |
| `MEMORY.md`, `ROADMAP.md` | Modify | Decision + phase notes. |

---

### Task 1: SCM value types, WebhookEvent fields, SCMWriter interface

**Files:**
- Modify: `internal/scm/scm.go` (`WebhookEvent` struct :10-19; new types after :19; `Client` iface stays :23-28; add `SCMWriter` iface)
- Test: `internal/scm/scm_types_test.go` (Create)

- [ ] **Step 1: Failing test for the new value types + interface satisfaction.**
  Write `internal/scm/scm_types_test.go`:
  ```go
  package scm

  import "testing"

  func TestValueTypesZero(t *testing.T) {
  	r := IssueReq{Title: "t", Body: "b", Labels: []string{"x"}}
  	if r.Title != "t" || r.Labels[0] != "x" {
  		t.Fatalf("IssueReq fields not wired: %+v", r)
  	}
  	ref := IssueRef{Ref: "o/r#1", URL: "https://x/1"}
  	if ref.Ref != "o/r#1" || ref.URL == "" {
  		t.Fatalf("IssueRef fields not wired: %+v", ref)
  	}
  	st := PRState{Author: "a", HeadSHA: "sha", HeadBranch: "br", Mergeable: true, CIStatus: "success"}
  	if !st.Mergeable || st.CIStatus != "success" {
  		t.Fatalf("PRState fields not wired: %+v", st)
  	}
  	s := Suggestion{Path: "a.go", Line: 12, Body: "fix"}
  	if s.Line != 12 {
  		t.Fatalf("Suggestion fields not wired: %+v", s)
  	}
  	b := BoardRef{Provider: "github", Owner: "o", GitHubProjectNumber: 3, GitLabBoardID: 0, StatusField: "Status"}
  	if b.GitHubProjectNumber != 3 || b.StatusField != "Status" {
  		t.Fatalf("BoardRef fields not wired: %+v", b)
  	}
  	ev := WebhookEvent{AuthorLogin: "bot", Action: "labeled", Number: 7, IsPR: true, HeadSHA: "deadbeef", HeadBranch: "feat", ChangedLabel: "tatara/awaiting-approval"}
  	if ev.AuthorLogin != "bot" || !ev.IsPR || ev.ChangedLabel == "" {
  		t.Fatalf("WebhookEvent new fields not wired: %+v", ev)
  	}
  }

  func TestProvidersSatisfySCMWriter(t *testing.T) {
  	var _ SCMWriter = (*GitHub)(nil)
  	var _ SCMWriter = (*GitLab)(nil)
  }
  ```
- [ ] **Step 2: Run it red.** `go test ./internal/scm/ -run 'TestValueTypesZero|TestProvidersSatisfySCMWriter' -v` -> FAIL: undefined `IssueReq`/`IssueRef`/`PRState`/`Suggestion`/`BoardRef`, undefined `SCMWriter`, and unknown fields on `WebhookEvent`.
- [ ] **Step 3: Add WebhookEvent fields.** In `internal/scm/scm.go` replace the `WebhookEvent` struct body (:10-19) with:
  ```go
  type WebhookEvent struct {
  	Kind     string // "push" | "issue" | "mr" | "other"
  	Repo     string // remote URL
  	Branch   string // for push
  	Labels   []string
  	Title    string
  	Body     string
  	IssueRef string // owner/repo#123 (github) or group/proj!iid (gitlab)
  	URL      string
  	AuthorLogin  string // login of the issue/PR/MR author (board identity)
  	Action       string // opened|labeled|unlabeled|closed|synchronize|submitted|created|other
  	Number       int    // issue/PR/MR number (github) or iid (gitlab)
  	IsPR         bool   // true for mr/pull_request events
  	HeadSHA      string // PR/MR head commit (for CI lookup)
  	HeadBranch   string // PR/MR source branch (for selfImprove push target)
  	ChangedLabel string // for labeled/unlabeled: the single label added/removed
  }
  ```
- [ ] **Step 4: Add value types + SCMWriter.** After the `WebhookEvent` struct in `internal/scm/scm.go`, before the `Client` interface, insert:
  ```go
  // IssueReq is the payload for creating an issue.
  type IssueReq struct {
  	Title  string
  	Body   string
  	Labels []string
  }

  // IssueRef identifies a created issue.
  type IssueRef struct {
  	Ref string // owner/repo#n (github) or group/proj#iid (gitlab)
  	URL string // html/web url
  }

  // PRState is the inspected state of a PR/MR.
  type PRState struct {
  	Author     string
  	HeadSHA    string
  	HeadBranch string
  	Mergeable  bool
  	CIStatus   string // "" none | pending | success | failure
  }

  // Suggestion is one inline code suggestion on a PR/MR.
  type Suggestion struct {
  	Path string
  	Line int
  	Body string
  }

  // BoardRef identifies a project board (GitHub Projects v2 or GitLab issue board).
  type BoardRef struct {
  	Provider            string
  	Owner               string
  	GitHubProjectNumber int
  	GitLabBoardID       int
  	StatusField         string // GH single-select field; default "Status"
  }

  // SCMWriter is what controller.SCMFor returns; *GitHub and *GitLab satisfy it.
  type SCMWriter interface {
  	OpenChange(ctx context.Context, repoURL, token, sourceBranch, targetBranch, title, body string) (string, error)
  	Comment(ctx context.Context, token, issueRef, body string) error
  	CreateIssue(ctx context.Context, repoURL, token string, req IssueReq) (IssueRef, error)
  	AddLabel(ctx context.Context, token, issueRef, label string) error
  	RemoveLabel(ctx context.Context, token, issueRef, label string) error
  	GetPRState(ctx context.Context, repoURL, token string, number int) (PRState, error)
  	Approve(ctx context.Context, repoURL, token string, number int, body string) error
  	RequestChanges(ctx context.Context, repoURL, token string, number int, body string) error
  	Suggest(ctx context.Context, repoURL, token string, number int, sugg []Suggestion) error
  	Merge(ctx context.Context, repoURL, token string, number int, method string) error
  	ClosePR(ctx context.Context, repoURL, token string, number int, body string) error
  	AddBoardItem(ctx context.Context, token string, board BoardRef, itemURL string) error
  	SetBoardColumn(ctx context.Context, token string, board BoardRef, itemURL, column string) error
  }
  ```
  `TestProvidersSatisfySCMWriter` stays red until Tasks 2-5 add the methods; keep it compiling by leaving the assertion. It will pass at the end of Task 5. The `TestValueTypesZero` test passes now.
- [ ] **Step 5: Run TestValueTypesZero green.** `go test ./internal/scm/ -run TestValueTypesZero -v` -> PASS.
- [ ] **Step 6: Commit.** `git add internal/scm/scm.go internal/scm/scm_types_test.go && git commit -m "feat(scm): add IssueReq/IssueRef/PRState/Suggestion/BoardRef value types, WebhookEvent fields, SCMWriter interface"`

---

### Task 2: GitHub IssueAuthor (CreateIssue/AddLabel/RemoveLabel)

**Files:**
- Modify: `internal/scm/github.go` (reuse `ghDo` :164, `ghOwnerRepo` :134, `ghIssueRef` :147)
- Test: `internal/scm/github_capabilities_test.go` (Create)

- [ ] **Step 1: Failing test for CreateIssue/AddLabel/RemoveLabel.**
  Write `internal/scm/github_capabilities_test.go`:
  ```go
  package scm

  import (
  	"context"
  	"encoding/json"
  	"io"
  	"net/http"
  	"net/http/httptest"
  	"testing"
  )

  func TestGitHubIssueAuthor(t *testing.T) {
  	t.Run("CreateIssue", func(t *testing.T) {
  		var gotPath, gotAuth string
  		var gotBody map[string]any
  		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
  			gotPath, gotAuth = r.URL.Path, r.Header.Get("Authorization")
  			b, _ := io.ReadAll(r.Body)
  			_ = json.Unmarshal(b, &gotBody)
  			_ = json.NewEncoder(w).Encode(map[string]any{"number": 42, "html_url": "https://gh/o/r/issues/42"})
  		}))
  		defer srv.Close()
  		c := &GitHub{apiBase: srv.URL}
  		ref, err := c.CreateIssue(context.Background(), "https://github.com/o/r", "tok", IssueReq{Title: "T", Body: "B", Labels: []string{"l1"}})
  		if err != nil {
  			t.Fatalf("CreateIssue: %v", err)
  		}
  		if gotPath != "/repos/o/r/issues" {
  			t.Fatalf("path = %q", gotPath)
  		}
  		if gotAuth != "Bearer tok" {
  			t.Fatalf("auth = %q", gotAuth)
  		}
  		if gotBody["title"] != "T" || gotBody["body"] != "B" {
  			t.Fatalf("body = %+v", gotBody)
  		}
  		labels, _ := gotBody["labels"].([]any)
  		if len(labels) != 1 || labels[0] != "l1" {
  			t.Fatalf("labels = %+v", gotBody["labels"])
  		}
  		if ref.Ref != "o/r#42" || ref.URL != "https://gh/o/r/issues/42" {
  			t.Fatalf("ref = %+v", ref)
  		}
  	})
  	t.Run("AddLabel", func(t *testing.T) {
  		var gotPath string
  		var gotBody map[string]any
  		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
  			gotPath = r.URL.Path
  			b, _ := io.ReadAll(r.Body)
  			_ = json.Unmarshal(b, &gotBody)
  		}))
  		defer srv.Close()
  		c := &GitHub{apiBase: srv.URL}
  		if err := c.AddLabel(context.Background(), "tok", "o/r#7", "tatara/awaiting-approval"); err != nil {
  			t.Fatalf("AddLabel: %v", err)
  		}
  		if gotPath != "/repos/o/r/issues/7/labels" {
  			t.Fatalf("path = %q", gotPath)
  		}
  		labels, _ := gotBody["labels"].([]any)
  		if len(labels) != 1 || labels[0] != "tatara/awaiting-approval" {
  			t.Fatalf("labels = %+v", gotBody["labels"])
  		}
  	})
  	t.Run("RemoveLabel", func(t *testing.T) {
  		var gotPath, gotMethod string
  		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
  			gotPath, gotMethod = r.URL.Path, r.Method
  		}))
  		defer srv.Close()
  		c := &GitHub{apiBase: srv.URL}
  		if err := c.RemoveLabel(context.Background(), "tok", "o/r#7", "tatara/awaiting-approval"); err != nil {
  			t.Fatalf("RemoveLabel: %v", err)
  		}
  		if gotMethod != http.MethodDelete {
  			t.Fatalf("method = %q", gotMethod)
  		}
  		if gotPath != "/repos/o/r/issues/7/labels/tatara/awaiting-approval" {
  			t.Fatalf("path = %q", gotPath)
  		}
  	})
  }
  ```
- [ ] **Step 2: Run it red.** `go test ./internal/scm/ -run TestGitHubIssueAuthor -v` -> FAIL: `c.CreateIssue` / `c.AddLabel` / `c.RemoveLabel` undefined.
- [ ] **Step 3: Implement the three methods.** Append to `internal/scm/github.go`:
  ```go
  // CreateIssue opens an issue and returns its ref + url.
  func (c *GitHub) CreateIssue(ctx context.Context, repoURL, token string, req IssueReq) (IssueRef, error) {
  	owner, repo, err := ghOwnerRepo(repoURL)
  	if err != nil {
  		return IssueRef{}, err
  	}
  	path := fmt.Sprintf("/repos/%s/%s/issues", owner, repo)
  	in := map[string]any{"title": req.Title, "body": req.Body}
  	if len(req.Labels) > 0 {
  		in["labels"] = req.Labels
  	}
  	var out struct {
  		Number  int    `json:"number"`
  		HTMLURL string `json:"html_url"`
  	}
  	if err := ghDo(ctx, c.base(), http.MethodPost, path, token, in, &out); err != nil {
  		return IssueRef{}, err
  	}
  	return IssueRef{Ref: fmt.Sprintf("%s/%s#%d", owner, repo, out.Number), URL: out.HTMLURL}, nil
  }

  // AddLabel adds a single label to an issue/PR identified by owner/repo#number.
  func (c *GitHub) AddLabel(ctx context.Context, token, issueRef, label string) error {
  	owner, repo, number, err := ghIssueRef(issueRef)
  	if err != nil {
  		return err
  	}
  	path := fmt.Sprintf("/repos/%s/%s/issues/%d/labels", owner, repo, number)
  	return ghDo(ctx, c.base(), http.MethodPost, path, token, map[string][]string{"labels": {label}}, nil)
  }

  // RemoveLabel removes a single label from an issue/PR.
  func (c *GitHub) RemoveLabel(ctx context.Context, token, issueRef, label string) error {
  	owner, repo, number, err := ghIssueRef(issueRef)
  	if err != nil {
  		return err
  	}
  	path := fmt.Sprintf("/repos/%s/%s/issues/%d/labels/%s", owner, repo, number, label)
  	return ghDo(ctx, c.base(), http.MethodDelete, path, token, nil, nil)
  }
  ```
- [ ] **Step 4: Run green.** `go test ./internal/scm/ -run TestGitHubIssueAuthor -v` -> PASS.
- [ ] **Step 5: Commit.** `git add internal/scm/github.go internal/scm/github_capabilities_test.go && git commit -m "feat(scm): GitHub IssueAuthor verbs (CreateIssue/AddLabel/RemoveLabel)"`

---

### Task 3: GitHub Reviewer (GetPRState/Approve/RequestChanges/Comment/Suggest/Merge/ClosePR)

**Files:**
- Modify: `internal/scm/github.go` (reuse `ghDo`, `ghOwnerRepo`)
- Test: `internal/scm/github_capabilities_test.go` (extend)

- [ ] **Step 1: Failing test for GetPRState CI derivation.**
  Append to `internal/scm/github_capabilities_test.go`:
  ```go
  func TestGitHubGetPRState(t *testing.T) {
  	cases := []struct {
  		name       string
  		runs       []map[string]string // status,conclusion
  		wantCI     string
  	}{
  		{"no runs", nil, ""},
  		{"in progress", []map[string]string{{"status": "in_progress", "conclusion": ""}}, "pending"},
  		{"all success", []map[string]string{{"status": "completed", "conclusion": "success"}}, "success"},
  		{"one failure", []map[string]string{{"status": "completed", "conclusion": "success"}, {"status": "completed", "conclusion": "failure"}}, "failure"},
  	}
  	for _, tc := range cases {
  		t.Run(tc.name, func(t *testing.T) {
  			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
  				switch {
  				case r.URL.Path == "/repos/o/r/pulls/5":
  					_ = json.NewEncoder(w).Encode(map[string]any{
  						"user":     map[string]any{"login": "alice"},
  						"mergeable": true,
  						"head":     map[string]any{"sha": "abc", "ref": "feature"},
  					})
  				case r.URL.Path == "/repos/o/r/commits/abc/check-runs":
  					runs := make([]map[string]any, 0, len(tc.runs))
  					for _, run := range tc.runs {
  						runs = append(runs, map[string]any{"status": run["status"], "conclusion": run["conclusion"]})
  					}
  					_ = json.NewEncoder(w).Encode(map[string]any{"check_runs": runs})
  				default:
  					t.Fatalf("unexpected path %q", r.URL.Path)
  				}
  			}))
  			defer srv.Close()
  			c := &GitHub{apiBase: srv.URL}
  			st, err := c.GetPRState(context.Background(), "https://github.com/o/r", "tok", 5)
  			if err != nil {
  				t.Fatalf("GetPRState: %v", err)
  			}
  			if st.Author != "alice" || st.HeadSHA != "abc" || st.HeadBranch != "feature" || !st.Mergeable {
  				t.Fatalf("state = %+v", st)
  			}
  			if st.CIStatus != tc.wantCI {
  				t.Fatalf("CIStatus = %q, want %q", st.CIStatus, tc.wantCI)
  			}
  		})
  	}
  }
  ```
- [ ] **Step 2: Run it red.** `go test ./internal/scm/ -run TestGitHubGetPRState -v` -> FAIL: `c.GetPRState` undefined.
- [ ] **Step 3: Implement GetPRState.** Append to `internal/scm/github.go`:
  ```go
  // GetPRState reads a PR plus its head check-runs, deriving CIStatus.
  func (c *GitHub) GetPRState(ctx context.Context, repoURL, token string, number int) (PRState, error) {
  	owner, repo, err := ghOwnerRepo(repoURL)
  	if err != nil {
  		return PRState{}, err
  	}
  	var pr struct {
  		User      struct{ Login string `json:"login"` } `json:"user"`
  		Mergeable bool                                   `json:"mergeable"`
  		Head      struct {
  			SHA string `json:"sha"`
  			Ref string `json:"ref"`
  		} `json:"head"`
  	}
  	if err := ghDo(ctx, c.base(), http.MethodGet, fmt.Sprintf("/repos/%s/%s/pulls/%d", owner, repo, number), token, nil, &pr); err != nil {
  		return PRState{}, err
  	}
  	st := PRState{Author: pr.User.Login, HeadSHA: pr.Head.SHA, HeadBranch: pr.Head.Ref, Mergeable: pr.Mergeable}
  	var checks struct {
  		CheckRuns []struct {
  			Status     string `json:"status"`
  			Conclusion string `json:"conclusion"`
  		} `json:"check_runs"`
  	}
  	if err := ghDo(ctx, c.base(), http.MethodGet, fmt.Sprintf("/repos/%s/%s/commits/%s/check-runs", owner, repo, pr.Head.SHA), token, nil, &checks); err != nil {
  		return PRState{}, err
  	}
  	st.CIStatus = deriveGHCIStatus(checks.CheckRuns)
  	return st, nil
  }

  func deriveGHCIStatus(runs []struct {
  	Status     string `json:"status"`
  	Conclusion string `json:"conclusion"`
  }) string {
  	if len(runs) == 0 {
  		return ""
  	}
  	failure, pending := false, false
  	for _, run := range runs {
  		if run.Status != "completed" {
  			pending = true
  			continue
  		}
  		if run.Conclusion != "success" && run.Conclusion != "neutral" && run.Conclusion != "skipped" {
  			failure = true
  		}
  	}
  	switch {
  	case failure:
  		return "failure"
  	case pending:
  		return "pending"
  	default:
  		return "success"
  	}
  }
  ```
- [ ] **Step 4: Run green.** `go test ./internal/scm/ -run TestGitHubGetPRState -v` -> PASS.
- [ ] **Step 5: Failing test for Approve/RequestChanges/Comment/Suggest/Merge/ClosePR.**
  Append:
  ```go
  func TestGitHubReviewVerbs(t *testing.T) {
  	t.Run("Approve", func(t *testing.T) {
  		var gotPath string
  		var body map[string]any
  		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
  			gotPath = r.URL.Path
  			b, _ := io.ReadAll(r.Body)
  			_ = json.Unmarshal(b, &body)
  		}))
  		defer srv.Close()
  		c := &GitHub{apiBase: srv.URL}
  		if err := c.Approve(context.Background(), "https://github.com/o/r", "tok", 5, "lgtm"); err != nil {
  			t.Fatalf("Approve: %v", err)
  		}
  		if gotPath != "/repos/o/r/pulls/5/reviews" || body["event"] != "APPROVE" || body["body"] != "lgtm" {
  			t.Fatalf("path=%q body=%+v", gotPath, body)
  		}
  	})
  	t.Run("RequestChanges", func(t *testing.T) {
  		var body map[string]any
  		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
  			b, _ := io.ReadAll(r.Body)
  			_ = json.Unmarshal(b, &body)
  		}))
  		defer srv.Close()
  		c := &GitHub{apiBase: srv.URL}
  		if err := c.RequestChanges(context.Background(), "https://github.com/o/r", "tok", 5, "nope"); err != nil {
  			t.Fatalf("RequestChanges: %v", err)
  		}
  		if body["event"] != "REQUEST_CHANGES" {
  			t.Fatalf("event = %v", body["event"])
  		}
  	})
  	t.Run("Suggest", func(t *testing.T) {
  		var body map[string]any
  		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
  			b, _ := io.ReadAll(r.Body)
  			_ = json.Unmarshal(b, &body)
  		}))
  		defer srv.Close()
  		c := &GitHub{apiBase: srv.URL}
  		err := c.Suggest(context.Background(), "https://github.com/o/r", "tok", 5, []Suggestion{{Path: "a.go", Line: 12, Body: "x := 1"}})
  		if err != nil {
  			t.Fatalf("Suggest: %v", err)
  		}
  		if body["event"] != "COMMENT" {
  			t.Fatalf("event = %v", body["event"])
  		}
  		comments, _ := body["comments"].([]any)
  		if len(comments) != 1 {
  			t.Fatalf("comments = %+v", body["comments"])
  		}
  		first, _ := comments[0].(map[string]any)
  		if first["path"] != "a.go" || first["line"].(float64) != 12 {
  			t.Fatalf("comment = %+v", first)
  		}
  		if cbody, _ := first["body"].(string); cbody != "```suggestion\nx := 1\n```" {
  			t.Fatalf("comment body = %q", cbody)
  		}
  	})
  	t.Run("Merge", func(t *testing.T) {
  		var gotPath, gotMethod string
  		var body map[string]any
  		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
  			gotPath, gotMethod = r.URL.Path, r.Method
  			b, _ := io.ReadAll(r.Body)
  			_ = json.Unmarshal(b, &body)
  		}))
  		defer srv.Close()
  		c := &GitHub{apiBase: srv.URL}
  		if err := c.Merge(context.Background(), "https://github.com/o/r", "tok", 5, "squash"); err != nil {
  			t.Fatalf("Merge: %v", err)
  		}
  		if gotPath != "/repos/o/r/pulls/5/merge" || gotMethod != http.MethodPut || body["merge_method"] != "squash" {
  			t.Fatalf("path=%q method=%q body=%+v", gotPath, gotMethod, body)
  		}
  	})
  	t.Run("ClosePR", func(t *testing.T) {
  		paths := map[string]bool{}
  		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
  			paths[r.Method+" "+r.URL.Path] = true
  		}))
  		defer srv.Close()
  		c := &GitHub{apiBase: srv.URL}
  		if err := c.ClosePR(context.Background(), "https://github.com/o/r", "tok", 5, "rejecting"); err != nil {
  			t.Fatalf("ClosePR: %v", err)
  		}
  		if !paths["PATCH /repos/o/r/pulls/5"] {
  			t.Fatalf("missing PATCH; got %+v", paths)
  		}
  		if !paths["POST /repos/o/r/issues/5/comments"] {
  			t.Fatalf("missing comment; got %+v", paths)
  		}
  	})
  }
  ```
- [ ] **Step 6: Run it red.** `go test ./internal/scm/ -run TestGitHubReviewVerbs -v` -> FAIL: methods undefined.
- [ ] **Step 7: Implement the review verbs.** Append to `internal/scm/github.go`:
  ```go
  func (c *GitHub) review(ctx context.Context, repoURL, token string, number int, event, body string, comments []map[string]any) error {
  	owner, repo, err := ghOwnerRepo(repoURL)
  	if err != nil {
  		return err
  	}
  	in := map[string]any{"event": event}
  	if body != "" {
  		in["body"] = body
  	}
  	if comments != nil {
  		in["comments"] = comments
  	}
  	path := fmt.Sprintf("/repos/%s/%s/pulls/%d/reviews", owner, repo, number)
  	return ghDo(ctx, c.base(), http.MethodPost, path, token, in, nil)
  }

  // Approve posts an APPROVE review.
  func (c *GitHub) Approve(ctx context.Context, repoURL, token string, number int, body string) error {
  	return c.review(ctx, repoURL, token, number, "APPROVE", body, nil)
  }

  // RequestChanges posts a REQUEST_CHANGES review.
  func (c *GitHub) RequestChanges(ctx context.Context, repoURL, token string, number int, body string) error {
  	return c.review(ctx, repoURL, token, number, "REQUEST_CHANGES", body, nil)
  }

  // Suggest posts inline review comments with ```suggestion bodies.
  func (c *GitHub) Suggest(ctx context.Context, repoURL, token string, number int, sugg []Suggestion) error {
  	comments := make([]map[string]any, 0, len(sugg))
  	for _, s := range sugg {
  		comments = append(comments, map[string]any{
  			"path": s.Path,
  			"line": s.Line,
  			"body": "```suggestion\n" + s.Body + "\n```",
  		})
  	}
  	return c.review(ctx, repoURL, token, number, "COMMENT", "", comments)
  }

  // Merge merges a PR with the given method (squash|merge|rebase).
  func (c *GitHub) Merge(ctx context.Context, repoURL, token string, number int, method string) error {
  	owner, repo, err := ghOwnerRepo(repoURL)
  	if err != nil {
  		return err
  	}
  	path := fmt.Sprintf("/repos/%s/%s/pulls/%d/merge", owner, repo, number)
  	return ghDo(ctx, c.base(), http.MethodPut, path, token, map[string]string{"merge_method": method}, nil)
  }

  // ClosePR closes a PR (state=closed) and posts a comment with the reason.
  func (c *GitHub) ClosePR(ctx context.Context, repoURL, token string, number int, body string) error {
  	owner, repo, err := ghOwnerRepo(repoURL)
  	if err != nil {
  		return err
  	}
  	path := fmt.Sprintf("/repos/%s/%s/pulls/%d", owner, repo, number)
  	if err := ghDo(ctx, c.base(), http.MethodPatch, path, token, map[string]string{"state": "closed"}, nil); err != nil {
  		return err
  	}
  	if body == "" {
  		return nil
  	}
  	return c.Comment(ctx, token, fmt.Sprintf("%s/%s#%d", owner, repo, number), body)
  }
  ```
- [ ] **Step 8: Run green.** `go test ./internal/scm/ -run TestGitHubReviewVerbs -v` -> PASS.
- [ ] **Step 9: Commit.** `git add internal/scm/github.go internal/scm/github_capabilities_test.go && git commit -m "feat(scm): GitHub Reviewer verbs (GetPRState CI derivation, Approve/RequestChanges/Suggest/Merge/ClosePR)"`

---

### Task 4: GitHub BoardManager via Projects v2 GraphQL helper

**Files:**
- Create: `internal/scm/github_graphql.go` (net-new GraphQL helper + `AddBoardItem`/`SetBoardColumn`)
- Test: `internal/scm/github_graphql_test.go` (Create)

- [ ] **Step 1: Failing test for AddBoardItem + SetBoardColumn.**
  Write `internal/scm/github_graphql_test.go`:
  ```go
  package scm

  import (
  	"context"
  	"encoding/json"
  	"io"
  	"net/http"
  	"net/http/httptest"
  	"strings"
  	"testing"
  )

  func TestGitHubBoardManager(t *testing.T) {
  	t.Run("AddBoardItem", func(t *testing.T) {
  		var queries []string
  		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
  			b, _ := io.ReadAll(r.Body)
  			var req struct {
  				Query string `json:"query"`
  			}
  			_ = json.Unmarshal(b, &req)
  			queries = append(queries, req.Query)
  			switch {
  			case strings.Contains(req.Query, "organization"):
  				_ = json.NewEncoder(w).Encode(map[string]any{"data": map[string]any{"organization": map[string]any{"projectV2": map[string]any{"id": "PVT_1"}}}})
  			case strings.Contains(req.Query, "resource(url"):
  				_ = json.NewEncoder(w).Encode(map[string]any{"data": map[string]any{"resource": map[string]any{"id": "I_1"}}})
  			case strings.Contains(req.Query, "addProjectV2ItemById"):
  				_ = json.NewEncoder(w).Encode(map[string]any{"data": map[string]any{"addProjectV2ItemById": map[string]any{"item": map[string]any{"id": "ITEM_1"}}}})
  			default:
  				t.Fatalf("unexpected query %q", req.Query)
  			}
  		}))
  		defer srv.Close()
  		c := &GitHub{apiBase: "unused", graphQLBase: srv.URL}
  		board := BoardRef{Provider: "github", Owner: "acme", GitHubProjectNumber: 3, StatusField: "Status"}
  		if err := c.AddBoardItem(context.Background(), "tok", board, "https://github.com/acme/r/issues/9"); err != nil {
  			t.Fatalf("AddBoardItem: %v", err)
  		}
  		if len(queries) != 3 {
  			t.Fatalf("expected 3 graphql calls, got %d", len(queries))
  		}
  	})
  	t.Run("SetBoardColumn", func(t *testing.T) {
  		var sawUpdate bool
  		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
  			b, _ := io.ReadAll(r.Body)
  			var req struct {
  				Query string `json:"query"`
  			}
  			_ = json.Unmarshal(b, &req)
  			switch {
  			case strings.Contains(req.Query, "organization"):
  				_ = json.NewEncoder(w).Encode(map[string]any{"data": map[string]any{"organization": map[string]any{"projectV2": map[string]any{
  					"id": "PVT_1",
  					"field": map[string]any{
  						"id": "FIELD_1",
  						"options": []any{
  							map[string]any{"id": "OPT_PROPOSED", "name": "Proposed"},
  							map[string]any{"id": "OPT_DONE", "name": "Done"},
  						},
  					},
  				}}}})
  			case strings.Contains(req.Query, "resource(url"):
  				_ = json.NewEncoder(w).Encode(map[string]any{"data": map[string]any{"resource": map[string]any{"id": "I_1", "projectItems": map[string]any{"nodes": []any{map[string]any{"id": "ITEM_1", "project": map[string]any{"id": "PVT_1"}}}}}}})
  			case strings.Contains(req.Query, "updateProjectV2ItemFieldValue"):
  				sawUpdate = true
  				if !strings.Contains(req.Query, "OPT_PROPOSED") {
  					t.Fatalf("update did not select Proposed option: %q", req.Query)
  				}
  				_ = json.NewEncoder(w).Encode(map[string]any{"data": map[string]any{"updateProjectV2ItemFieldValue": map[string]any{"clientMutationId": ""}}})
  			default:
  				t.Fatalf("unexpected query %q", req.Query)
  			}
  		}))
  		defer srv.Close()
  		c := &GitHub{apiBase: "unused", graphQLBase: srv.URL}
  		board := BoardRef{Provider: "github", Owner: "acme", GitHubProjectNumber: 3, StatusField: "Status"}
  		if err := c.SetBoardColumn(context.Background(), "tok", board, "https://github.com/acme/r/issues/9", "Proposed"); err != nil {
  			t.Fatalf("SetBoardColumn: %v", err)
  		}
  		if !sawUpdate {
  			t.Fatalf("updateProjectV2ItemFieldValue not called")
  		}
  	})
  }
  ```
- [ ] **Step 2: Run it red.** `go test ./internal/scm/ -run TestGitHubBoardManager -v` -> FAIL: `graphQLBase` field and `AddBoardItem`/`SetBoardColumn` undefined.
- [ ] **Step 3: Add graphQLBase to GitHub struct.** In `internal/scm/scm.go` replace the `GitHub` struct (:41-44):
  ```go
  // GitHub implements Client for GitHub.
  type GitHub struct {
  	apiBase     string
  	graphQLBase string
  }
  ```
- [ ] **Step 4: Implement the GraphQL helper + board verbs.** Create `internal/scm/github_graphql.go`:
  ```go
  package scm

  import (
  	"bytes"
  	"context"
  	"encoding/json"
  	"fmt"
  	"io"
  	"net/http"
  )

  func (c *GitHub) graphQLEndpoint() string {
  	if c.graphQLBase != "" {
  		return c.graphQLBase
  	}
  	return "https://api.github.com/graphql"
  }

  // ghGraphQL posts a GraphQL query and decodes data into out.
  func (c *GitHub) ghGraphQL(ctx context.Context, token, query string, vars map[string]any, out any) error {
  	payload := map[string]any{"query": query}
  	if vars != nil {
  		payload["variables"] = vars
  	}
  	b, err := json.Marshal(payload)
  	if err != nil {
  		return fmt.Errorf("github: encode graphql: %w", err)
  	}
  	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.graphQLEndpoint(), bytes.NewReader(b))
  	if err != nil {
  		return fmt.Errorf("github: build graphql request: %w", err)
  	}
  	req.Header.Set("Authorization", "Bearer "+token)
  	req.Header.Set("Content-Type", "application/json")
  	resp, err := http.DefaultClient.Do(req)
  	if err != nil {
  		return fmt.Errorf("github: do graphql request: %w", err)
  	}
  	defer func() { _ = resp.Body.Close() }()
  	if resp.StatusCode >= 400 {
  		buf, _ := io.ReadAll(resp.Body)
  		return &HTTPError{Status: resp.StatusCode, Body: string(buf), Path: "/graphql"}
  	}
  	var env struct {
  		Data   json.RawMessage `json:"data"`
  		Errors []struct {
  			Message string `json:"message"`
  		} `json:"errors"`
  	}
  	if err := json.NewDecoder(resp.Body).Decode(&env); err != nil {
  		return fmt.Errorf("github: decode graphql: %w", err)
  	}
  	if len(env.Errors) > 0 {
  		return fmt.Errorf("github: graphql error: %s", env.Errors[0].Message)
  	}
  	if out == nil {
  		return nil
  	}
  	if err := json.Unmarshal(env.Data, out); err != nil {
  		return fmt.Errorf("github: decode graphql data: %w", err)
  	}
  	return nil
  }

  // AddBoardItem resolves the project by org+number, the issue node by URL, and
  // adds the item to the Projects v2 board.
  func (c *GitHub) AddBoardItem(ctx context.Context, token string, board BoardRef, itemURL string) error {
  	projectID, err := c.ghProjectID(ctx, token, board)
  	if err != nil {
  		return err
  	}
  	contentID, err := c.ghResourceID(ctx, token, itemURL)
  	if err != nil {
  		return err
  	}
  	q := fmt.Sprintf(`mutation { addProjectV2ItemById(input:{projectId:%q, contentId:%q}) { item { id } } }`, projectID, contentID)
  	return c.ghGraphQL(ctx, token, q, nil, nil)
  }

  // SetBoardColumn sets the Status single-select field of the board item for itemURL.
  func (c *GitHub) SetBoardColumn(ctx context.Context, token string, board BoardRef, itemURL, column string) error {
  	field := board.StatusField
  	if field == "" {
  		field = "Status"
  	}
  	var proj struct {
  		Organization struct {
  			ProjectV2 struct {
  				ID    string `json:"id"`
  				Field struct {
  					ID      string `json:"id"`
  					Options []struct {
  						ID   string `json:"id"`
  						Name string `json:"name"`
  					} `json:"options"`
  				} `json:"field"`
  			} `json:"projectV2"`
  		} `json:"organization"`
  	}
  	pq := fmt.Sprintf(`query { organization(login:%q) { projectV2(number:%d) { id field(name:%q) { ... on ProjectV2SingleSelectField { id options { id name } } } } } }`, board.Owner, board.GitHubProjectNumber, field)
  	if err := c.ghGraphQL(ctx, token, pq, nil, &proj); err != nil {
  		return err
  	}
  	optionID := ""
  	for _, o := range proj.Organization.ProjectV2.Field.Options {
  		if o.Name == column {
  			optionID = o.ID
  			break
  		}
  	}
  	if optionID == "" {
  		return fmt.Errorf("github: board column %q not found in field %q", column, field)
  	}
  	itemID, err := c.ghProjectItemID(ctx, token, itemURL, proj.Organization.ProjectV2.ID)
  	if err != nil {
  		return err
  	}
  	mq := fmt.Sprintf(`mutation { updateProjectV2ItemFieldValue(input:{projectId:%q, itemId:%q, fieldId:%q, value:{singleSelectOptionId:%q}}) { clientMutationId } }`,
  		proj.Organization.ProjectV2.ID, itemID, proj.Organization.ProjectV2.Field.ID, optionID)
  	return c.ghGraphQL(ctx, token, mq, nil, nil)
  }

  func (c *GitHub) ghProjectID(ctx context.Context, token string, board BoardRef) (string, error) {
  	var out struct {
  		Organization struct {
  			ProjectV2 struct {
  				ID string `json:"id"`
  			} `json:"projectV2"`
  		} `json:"organization"`
  	}
  	q := fmt.Sprintf(`query { organization(login:%q) { projectV2(number:%d) { id } } }`, board.Owner, board.GitHubProjectNumber)
  	if err := c.ghGraphQL(ctx, token, q, nil, &out); err != nil {
  		return "", err
  	}
  	if out.Organization.ProjectV2.ID == "" {
  		return "", fmt.Errorf("github: project %d not found for org %q", board.GitHubProjectNumber, board.Owner)
  	}
  	return out.Organization.ProjectV2.ID, nil
  }

  func (c *GitHub) ghResourceID(ctx context.Context, token, itemURL string) (string, error) {
  	var out struct {
  		Resource struct {
  			ID string `json:"id"`
  		} `json:"resource"`
  	}
  	q := fmt.Sprintf(`query { resource(url:%q) { ... on Issue { id } ... on PullRequest { id } } }`, itemURL)
  	if err := c.ghGraphQL(ctx, token, q, nil, &out); err != nil {
  		return "", err
  	}
  	if out.Resource.ID == "" {
  		return "", fmt.Errorf("github: resource not found for url %q", itemURL)
  	}
  	return out.Resource.ID, nil
  }

  func (c *GitHub) ghProjectItemID(ctx context.Context, token, itemURL, projectID string) (string, error) {
  	var out struct {
  		Resource struct {
  			ProjectItems struct {
  				Nodes []struct {
  					ID      string `json:"id"`
  					Project struct {
  						ID string `json:"id"`
  					} `json:"project"`
  				} `json:"nodes"`
  			} `json:"projectItems"`
  		} `json:"resource"`
  	}
  	q := fmt.Sprintf(`query { resource(url:%q) { ... on Issue { projectItems(first:20) { nodes { id project { id } } } } ... on PullRequest { projectItems(first:20) { nodes { id project { id } } } } } }`, itemURL)
  	if err := c.ghGraphQL(ctx, token, q, nil, &out); err != nil {
  		return "", err
  	}
  	for _, n := range out.Resource.ProjectItems.Nodes {
  		if n.Project.ID == projectID {
  			return n.ID, nil
  		}
  	}
  	return "", fmt.Errorf("github: item for %q not on project %q", itemURL, projectID)
  }
  ```
- [ ] **Step 5: Run green.** `go test ./internal/scm/ -run TestGitHubBoardManager -v` -> PASS.
- [ ] **Step 6: Commit.** `git add internal/scm/scm.go internal/scm/github_graphql.go internal/scm/github_graphql_test.go && git commit -m "feat(scm): GitHub Projects v2 GraphQL board manager (AddBoardItem/SetBoardColumn)"`

---

### Task 5: GitLab IssueAuthor + Reviewer + BoardManager

**Files:**
- Modify: `internal/scm/gitlab.go` (reuse `glDo` :153, `glProjectPath` :125, `glIssueRef` :137)
- Test: `internal/scm/gitlab_capabilities_test.go` (Create)

- [ ] **Step 1: Failing test for GitLab IssueAuthor + Reviewer + board labels.**
  Write `internal/scm/gitlab_capabilities_test.go`:
  ```go
  package scm

  import (
  	"context"
  	"encoding/json"
  	"io"
  	"net/http"
  	"net/http/httptest"
  	"net/url"
  	"strings"
  	"testing"
  )

  func TestGitLabCapabilities(t *testing.T) {
  	t.Run("CreateIssue", func(t *testing.T) {
  		var gotPath string
  		var body map[string]any
  		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
  			gotPath = r.URL.Path
  			b, _ := io.ReadAll(r.Body)
  			_ = json.Unmarshal(b, &body)
  			_ = json.NewEncoder(w).Encode(map[string]any{"iid": 4, "web_url": "https://gl/g/p/-/issues/4"})
  		}))
  		defer srv.Close()
  		c := &GitLab{apiBase: srv.URL}
  		ref, err := c.CreateIssue(context.Background(), "https://gitlab.com/g/p", "tok", IssueReq{Title: "T", Body: "B", Labels: []string{"l1", "l2"}})
  		if err != nil {
  			t.Fatalf("CreateIssue: %v", err)
  		}
  		if gotPath != "/projects/"+url.PathEscape("g/p")+"/issues" {
  			t.Fatalf("path = %q", gotPath)
  		}
  		if body["title"] != "T" || body["labels"] != "l1,l2" {
  			t.Fatalf("body = %+v", body)
  		}
  		if ref.Ref != "g/p#4" || ref.URL != "https://gl/g/p/-/issues/4" {
  			t.Fatalf("ref = %+v", ref)
  		}
  	})
  	t.Run("Approve", func(t *testing.T) {
  		var gotPath string
  		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { gotPath = r.URL.Path }))
  		defer srv.Close()
  		c := &GitLab{apiBase: srv.URL}
  		if err := c.Approve(context.Background(), "https://gitlab.com/g/p", "tok", 5, ""); err != nil {
  			t.Fatalf("Approve: %v", err)
  		}
  		if gotPath != "/projects/"+url.PathEscape("g/p")+"/merge_requests/5/approve" {
  			t.Fatalf("path = %q", gotPath)
  		}
  	})
  	t.Run("RequestChanges", func(t *testing.T) {
  		paths := map[string]bool{}
  		awards := map[string]any{}
  		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
  			paths[r.URL.Path] = true
  			if strings.HasSuffix(r.URL.Path, "/award_emoji") {
  				b, _ := io.ReadAll(r.Body)
  				_ = json.Unmarshal(b, &awards)
  			}
  		}))
  		defer srv.Close()
  		c := &GitLab{apiBase: srv.URL}
  		if err := c.RequestChanges(context.Background(), "https://gitlab.com/g/p", "tok", 5, "nope"); err != nil {
  			t.Fatalf("RequestChanges: %v", err)
  		}
  		base := "/projects/" + url.PathEscape("g/p") + "/merge_requests/5"
  		if !paths[base+"/unapprove"] || !paths[base+"/award_emoji"] || !paths[base+"/notes"] {
  			t.Fatalf("missing call; paths=%+v", paths)
  		}
  		if awards["name"] != "thumbsdown" {
  			t.Fatalf("award = %+v", awards)
  		}
  	})
  	t.Run("Merge", func(t *testing.T) {
  		var gotPath, gotMethod string
  		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
  			gotPath, gotMethod = r.URL.Path, r.Method
  		}))
  		defer srv.Close()
  		c := &GitLab{apiBase: srv.URL}
  		if err := c.Merge(context.Background(), "https://gitlab.com/g/p", "tok", 5, "squash"); err != nil {
  			t.Fatalf("Merge: %v", err)
  		}
  		if gotPath != "/projects/"+url.PathEscape("g/p")+"/merge_requests/5/merge" || gotMethod != http.MethodPut {
  			t.Fatalf("path=%q method=%q", gotPath, gotMethod)
  		}
  	})
  	t.Run("ClosePR", func(t *testing.T) {
  		var body map[string]any
  		var gotMethod string
  		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
  			if r.Method == http.MethodPut {
  				gotMethod = r.Method
  				b, _ := io.ReadAll(r.Body)
  				_ = json.Unmarshal(b, &body)
  			}
  		}))
  		defer srv.Close()
  		c := &GitLab{apiBase: srv.URL}
  		if err := c.ClosePR(context.Background(), "https://gitlab.com/g/p", "tok", 5, "rejecting"); err != nil {
  			t.Fatalf("ClosePR: %v", err)
  		}
  		if gotMethod != http.MethodPut || body["state_event"] != "close" {
  			t.Fatalf("method=%q body=%+v", gotMethod, body)
  		}
  	})
  	t.Run("GetPRState", func(t *testing.T) {
  		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
  			_ = json.NewEncoder(w).Encode(map[string]any{
  				"author":         map[string]any{"username": "bob"},
  				"sha":            "sha1",
  				"source_branch":  "feat",
  				"merge_status":   "can_be_merged",
  				"head_pipeline":  map[string]any{"status": "success"},
  			})
  		}))
  		defer srv.Close()
  		c := &GitLab{apiBase: srv.URL}
  		st, err := c.GetPRState(context.Background(), "https://gitlab.com/g/p", "tok", 5)
  		if err != nil {
  			t.Fatalf("GetPRState: %v", err)
  		}
  		if st.Author != "bob" || st.HeadSHA != "sha1" || st.HeadBranch != "feat" || !st.Mergeable || st.CIStatus != "success" {
  			t.Fatalf("state = %+v", st)
  		}
  	})
  	t.Run("SetBoardColumn", func(t *testing.T) {
  		var body map[string]any
  		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
  			if r.Method == http.MethodPut {
  				b, _ := io.ReadAll(r.Body)
  				_ = json.Unmarshal(b, &body)
  			}
  		}))
  		defer srv.Close()
  		c := &GitLab{apiBase: srv.URL}
  		board := BoardRef{Provider: "gitlab", GitLabBoardID: 7}
  		if err := c.SetBoardColumn(context.Background(), "tok", board, "https://gitlab.com/g/p/-/issues/4", "Proposed"); err != nil {
  			t.Fatalf("SetBoardColumn: %v", err)
  		}
  		if body["add_labels"] != "board::Proposed" {
  			t.Fatalf("add_labels = %+v", body["add_labels"])
  		}
  	})
  	t.Run("AddBoardItem", func(t *testing.T) {
  		var body map[string]any
  		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
  			if r.Method == http.MethodPut {
  				b, _ := io.ReadAll(r.Body)
  				_ = json.Unmarshal(b, &body)
  			}
  		}))
  		defer srv.Close()
  		c := &GitLab{apiBase: srv.URL}
  		board := BoardRef{Provider: "gitlab", GitLabBoardID: 7}
  		if err := c.AddBoardItem(context.Background(), "tok", board, "https://gitlab.com/g/p/-/issues/4"); err != nil {
  			t.Fatalf("AddBoardItem: %v", err)
  		}
  		if body["add_labels"] != "board::Open" {
  			t.Fatalf("add_labels = %+v", body["add_labels"])
  		}
  	})
  }
  ```
- [ ] **Step 2: Run it red.** `go test ./internal/scm/ -run TestGitLabCapabilities -v` -> FAIL: methods undefined.
- [ ] **Step 3: Implement GitLab IssueAuthor + Reviewer.** Append to `internal/scm/gitlab.go`. The board-item URL carries `/-/issues/<iid>`; parse it to a project path + iid with a small helper:
  ```go
  // CreateIssue opens an issue and returns its ref + url.
  func (c *GitLab) CreateIssue(ctx context.Context, repoURL, token string, req IssueReq) (IssueRef, error) {
  	proj, err := glProjectPath(repoURL)
  	if err != nil {
  		return IssueRef{}, err
  	}
  	in := map[string]string{"title": req.Title, "description": req.Body}
  	if len(req.Labels) > 0 {
  		in["labels"] = strings.Join(req.Labels, ",")
  	}
  	var out struct {
  		IID    int    `json:"iid"`
  		WebURL string `json:"web_url"`
  	}
  	path := "/projects/" + url.PathEscape(proj) + "/issues"
  	if err := glDo(ctx, c.base(), http.MethodPost, path, token, in, &out); err != nil {
  		return IssueRef{}, err
  	}
  	return IssueRef{Ref: fmt.Sprintf("%s#%d", proj, out.IID), URL: out.WebURL}, nil
  }

  // AddLabel adds a label to an issue identified by group/proj#iid.
  func (c *GitLab) AddLabel(ctx context.Context, token, issueRef, label string) error {
  	proj, iid, err := glHashRef(issueRef)
  	if err != nil {
  		return err
  	}
  	path := "/projects/" + url.PathEscape(proj) + "/issues/" + strconv.Itoa(iid)
  	return glDo(ctx, c.base(), http.MethodPut, path, token, map[string]string{"add_labels": label}, nil)
  }

  // RemoveLabel removes a label from an issue identified by group/proj#iid.
  func (c *GitLab) RemoveLabel(ctx context.Context, token, issueRef, label string) error {
  	proj, iid, err := glHashRef(issueRef)
  	if err != nil {
  		return err
  	}
  	path := "/projects/" + url.PathEscape(proj) + "/issues/" + strconv.Itoa(iid)
  	return glDo(ctx, c.base(), http.MethodPut, path, token, map[string]string{"remove_labels": label}, nil)
  }

  // GetPRState reads an MR and its head pipeline status.
  func (c *GitLab) GetPRState(ctx context.Context, repoURL, token string, number int) (PRState, error) {
  	proj, err := glProjectPath(repoURL)
  	if err != nil {
  		return PRState{}, err
  	}
  	var mr struct {
  		Author       struct{ Username string `json:"username"` } `json:"author"`
  		SHA          string `json:"sha"`
  		SourceBranch string `json:"source_branch"`
  		MergeStatus  string `json:"merge_status"`
  		HeadPipeline struct {
  			Status string `json:"status"`
  		} `json:"head_pipeline"`
  	}
  	path := "/projects/" + url.PathEscape(proj) + "/merge_requests/" + strconv.Itoa(number)
  	if err := glDo(ctx, c.base(), http.MethodGet, path, token, nil, &mr); err != nil {
  		return PRState{}, err
  	}
  	return PRState{
  		Author:     mr.Author.Username,
  		HeadSHA:    mr.SHA,
  		HeadBranch: mr.SourceBranch,
  		Mergeable:  mr.MergeStatus == "can_be_merged",
  		CIStatus:   glCIStatus(mr.HeadPipeline.Status),
  	}, nil
  }

  func glCIStatus(s string) string {
  	switch s {
  	case "":
  		return ""
  	case "success":
  		return "success"
  	case "failed", "canceled":
  		return "failure"
  	default:
  		return "pending"
  	}
  }

  // Approve approves an MR.
  func (c *GitLab) Approve(ctx context.Context, repoURL, token string, number int, body string) error {
  	proj, err := glProjectPath(repoURL)
  	if err != nil {
  		return err
  	}
  	path := "/projects/" + url.PathEscape(proj) + "/merge_requests/" + strconv.Itoa(number) + "/approve"
  	if err := glDo(ctx, c.base(), http.MethodPost, path, token, nil, nil); err != nil {
  		return err
  	}
  	if body == "" {
  		return nil
  	}
  	return c.mrNote(ctx, c.base(), proj, number, token, body)
  }

  // RequestChanges unapproves, awards thumbsdown, and posts a note.
  func (c *GitLab) RequestChanges(ctx context.Context, repoURL, token string, number int, body string) error {
  	proj, err := glProjectPath(repoURL)
  	if err != nil {
  		return err
  	}
  	base := "/projects/" + url.PathEscape(proj) + "/merge_requests/" + strconv.Itoa(number)
  	if err := glDo(ctx, c.base(), http.MethodPost, base+"/unapprove", token, nil, nil); err != nil {
  		return err
  	}
  	if err := glDo(ctx, c.base(), http.MethodPost, base+"/award_emoji", token, map[string]string{"name": "thumbsdown"}, nil); err != nil {
  		return err
  	}
  	if body == "" {
  		body = "Requesting changes."
  	}
  	return c.mrNote(ctx, c.base(), proj, number, token, body)
  }

  // Suggest posts inline ```suggestion notes on the MR.
  func (c *GitLab) Suggest(ctx context.Context, repoURL, token string, number int, sugg []Suggestion) error {
  	proj, err := glProjectPath(repoURL)
  	if err != nil {
  		return err
  	}
  	for _, s := range sugg {
  		note := fmt.Sprintf("`%s:%d`\n```suggestion\n%s\n```", s.Path, s.Line, s.Body)
  		if err := c.mrNote(ctx, c.base(), proj, number, token, note); err != nil {
  			return err
  		}
  	}
  	return nil
  }

  // Merge merges an MR.
  func (c *GitLab) Merge(ctx context.Context, repoURL, token string, number int, method string) error {
  	proj, err := glProjectPath(repoURL)
  	if err != nil {
  		return err
  	}
  	in := map[string]bool{"squash": method == "squash"}
  	path := "/projects/" + url.PathEscape(proj) + "/merge_requests/" + strconv.Itoa(number) + "/merge"
  	return glDo(ctx, c.base(), http.MethodPut, path, token, in, nil)
  }

  // ClosePR closes an MR (state_event=close) and posts the reason as a note.
  func (c *GitLab) ClosePR(ctx context.Context, repoURL, token string, number int, body string) error {
  	proj, err := glProjectPath(repoURL)
  	if err != nil {
  		return err
  	}
  	path := "/projects/" + url.PathEscape(proj) + "/merge_requests/" + strconv.Itoa(number)
  	if err := glDo(ctx, c.base(), http.MethodPut, path, token, map[string]string{"state_event": "close"}, nil); err != nil {
  		return err
  	}
  	if body == "" {
  		return nil
  	}
  	return c.mrNote(ctx, c.base(), proj, number, token, body)
  }

  func (c *GitLab) mrNote(ctx context.Context, base, proj string, number int, token, body string) error {
  	path := "/projects/" + url.PathEscape(proj) + "/merge_requests/" + strconv.Itoa(number) + "/notes"
  	return glDo(ctx, base, http.MethodPost, path, token, map[string]string{"body": body}, nil)
  }

  // glHashRef parses group/proj#iid into project path + iid (issue refs use '#').
  func glHashRef(ref string) (string, int, error) {
  	at := strings.LastIndex(ref, "#")
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

  // glIssueURLRef parses a GitLab issue web URL (.../g/p/-/issues/4) into a
  // project path + iid for board label updates.
  func glIssueURLRef(itemURL string) (string, int, error) {
  	u, err := url.Parse(itemURL)
  	if err != nil {
  		return "", 0, fmt.Errorf("gitlab: parse item url %q: %w", itemURL, err)
  	}
  	p := strings.Trim(u.Path, "/")
  	idx := strings.Index(p, "/-/issues/")
  	if idx < 0 {
  		return "", 0, fmt.Errorf("gitlab: not an issue url %q", itemURL)
  	}
  	proj := p[:idx]
  	iid, err := strconv.Atoi(p[idx+len("/-/issues/"):])
  	if err != nil {
  		return "", 0, fmt.Errorf("gitlab: bad iid in %q: %w", itemURL, err)
  	}
  	return proj, iid, nil
  }
  ```
- [ ] **Step 4: Implement GitLab BoardManager (label-driven).** Append:
  ```go
  // AddBoardItem ensures the issue carries the board's default list label so it
  // appears on the GitLab issue board. No-op semantics beyond the label.
  func (c *GitLab) AddBoardItem(ctx context.Context, token string, board BoardRef, itemURL string) error {
  	return c.setBoardLabel(ctx, token, itemURL, "Open")
  }

  // SetBoardColumn swaps the issue's board::<col> scoped label.
  func (c *GitLab) SetBoardColumn(ctx context.Context, token string, board BoardRef, itemURL, column string) error {
  	return c.setBoardLabel(ctx, token, itemURL, column)
  }

  func (c *GitLab) setBoardLabel(ctx context.Context, token, itemURL, column string) error {
  	proj, iid, err := glIssueURLRef(itemURL)
  	if err != nil {
  		return err
  	}
  	path := "/projects/" + url.PathEscape(proj) + "/issues/" + strconv.Itoa(iid)
  	return glDo(ctx, c.base(), http.MethodPut, path, token, map[string]string{"add_labels": "board::" + column}, nil)
  }
  ```
- [ ] **Step 5: Run green.** `go test ./internal/scm/ -run TestGitLabCapabilities -v` -> PASS. Then `go test ./internal/scm/ -run TestProvidersSatisfySCMWriter -v` -> PASS (both clients now satisfy `SCMWriter`).
- [ ] **Step 6: Commit.** `git add internal/scm/gitlab.go internal/scm/gitlab_capabilities_test.go && git commit -m "feat(scm): GitLab IssueAuthor/Reviewer/BoardManager (label-driven board, unapprove+thumbsdown request-changes)"`

---

### Task 6: Expand DetectAndVerify in both clients

**Files:**
- Modify: `internal/scm/github.go` (`ghPayload` :31-40, `DetectAndVerify` :43, `ghWorkItemEvent` :64)
- Modify: `internal/scm/gitlab.go` (`glPayload` :21-35, `DetectAndVerify` :38, `glWorkItemEvent` :70)
- Test: extend `internal/scm/github_capabilities_test.go` and `internal/scm/gitlab_capabilities_test.go`

- [ ] **Step 1: Failing test for GitHub DetectAndVerify new fields.**
  Append to `internal/scm/github_capabilities_test.go`. Reuse `verifyGitHubSig`'s expectation by signing the payload in the test:
  ```go
  func ghSign(payload []byte, secret string) string {
  	m := hmac.New(sha256.New, []byte(secret))
  	m.Write(payload)
  	return "sha256=" + hex.EncodeToString(m.Sum(nil))
  }

  func TestGitHubDetectAndVerifyFields(t *testing.T) {
  	const secret = "s3cr3t"
  	t.Run("issue labeled", func(t *testing.T) {
  		payload := []byte(`{"action":"labeled","sender":{"login":"alice"},"label":{"name":"tatara/awaiting-approval"},"repository":{"clone_url":"https://github.com/o/r.git","full_name":"o/r"},"issue":{"number":7,"title":"T","body":"B","html_url":"https://gh/o/r/issues/7","labels":[{"name":"tatara"}]}}`)
  		h := http.Header{}
  		h.Set("X-GitHub-Event", "issues")
  		h.Set("X-Hub-Signature-256", ghSign(payload, secret))
  		ev, err := (&GitHub{}).DetectAndVerify(h, payload, secret)
  		if err != nil {
  			t.Fatalf("DetectAndVerify: %v", err)
  		}
  		if ev.Kind != "issue" || ev.Action != "labeled" || ev.AuthorLogin != "alice" || ev.Number != 7 || ev.IsPR || ev.ChangedLabel != "tatara/awaiting-approval" {
  			t.Fatalf("event = %+v", ev)
  		}
  	})
  	t.Run("pull_request opened", func(t *testing.T) {
  		payload := []byte(`{"action":"opened","sender":{"login":"bob"},"repository":{"clone_url":"https://github.com/o/r.git","full_name":"o/r"},"pull_request":{"number":9,"title":"PR","body":"body","html_url":"https://gh/o/r/pull/9","head":{"sha":"deadbeef","ref":"feature"}}}`)
  		h := http.Header{}
  		h.Set("X-GitHub-Event", "pull_request")
  		h.Set("X-Hub-Signature-256", ghSign(payload, secret))
  		ev, err := (&GitHub{}).DetectAndVerify(h, payload, secret)
  		if err != nil {
  			t.Fatalf("DetectAndVerify: %v", err)
  		}
  		if ev.Kind != "mr" || !ev.IsPR || ev.AuthorLogin != "bob" || ev.Number != 9 || ev.HeadSHA != "deadbeef" || ev.HeadBranch != "feature" || ev.Action != "opened" {
  			t.Fatalf("event = %+v", ev)
  		}
  	})
  }
  ```
  Add imports `"crypto/hmac"`, `"crypto/sha256"`, `"encoding/hex"` to the test file.
- [ ] **Step 2: Run it red.** `go test ./internal/scm/ -run TestGitHubDetectAndVerifyFields -v` -> FAIL: new fields empty/zero.
- [ ] **Step 3: Extend the GitHub payload + parse.** In `internal/scm/github.go` extend `ghWorkItem` and `ghPayload`:
  ```go
  type ghWorkItem struct {
  	Number  int       `json:"number"`
  	Title   string    `json:"title"`
  	Body    string    `json:"body"`
  	Labels  []ghLabel `json:"labels"`
  	HTMLURL string    `json:"html_url"`
  	Head    struct {
  		SHA string `json:"sha"`
  		Ref string `json:"ref"`
  	} `json:"head"`
  }

  type ghPayload struct {
  	Action     string `json:"action"`
  	Ref        string `json:"ref"`
  	After      string `json:"after"`
  	Repository struct {
  		CloneURL string `json:"clone_url"`
  		FullName string `json:"full_name"`
  	} `json:"repository"`
  	Sender struct {
  		Login string `json:"login"`
  	} `json:"sender"`
  	Label struct {
  		Name string `json:"name"`
  	} `json:"label"`
  	Issue       *ghWorkItem `json:"issue"`
  	PullRequest *ghWorkItem `json:"pull_request"`
  }
  ```
  Replace the issues/pull_request/issue_comment/review cases in `DetectAndVerify` and `ghWorkItemEvent`:
  ```go
  	switch event {
  	case "push":
  		return WebhookEvent{Kind: "push", Repo: p.Repository.CloneURL, Branch: strings.TrimPrefix(p.Ref, "refs/heads/")}, nil
  	case "issues":
  		return ghWorkItemEvent("issue", false, p, p.Issue), nil
  	case "issue_comment":
  		ev := ghWorkItemEvent("issue", false, p, p.Issue)
  		ev.IsPR = p.Issue != nil && p.Issue.Head.Ref != ""
  		if ev.IsPR {
  			ev.Kind = "mr"
  		}
  		return ev, nil
  	case "pull_request":
  		return ghWorkItemEvent("mr", true, p, p.PullRequest), nil
  	case "pull_request_review":
  		return ghWorkItemEvent("mr", true, p, p.PullRequest), nil
  	default:
  		return WebhookEvent{Kind: "other"}, nil
  	}
  ```
  ```go
  func ghWorkItemEvent(kind string, isPR bool, p ghPayload, wi *ghWorkItem) WebhookEvent {
  	if wi == nil {
  		return WebhookEvent{Kind: "other"}
  	}
  	labels := make([]string, 0, len(wi.Labels))
  	for _, l := range wi.Labels {
  		labels = append(labels, l.Name)
  	}
  	return WebhookEvent{
  		Kind:         kind,
  		Repo:         p.Repository.CloneURL,
  		Labels:       labels,
  		Title:        wi.Title,
  		Body:         wi.Body,
  		IssueRef:     fmt.Sprintf("%s#%d", p.Repository.FullName, wi.Number),
  		URL:          wi.HTMLURL,
  		AuthorLogin:  p.Sender.Login,
  		Action:       p.Action,
  		Number:       wi.Number,
  		IsPR:         isPR,
  		HeadSHA:      wi.Head.SHA,
  		HeadBranch:   wi.Head.Ref,
  		ChangedLabel: p.Label.Name,
  	}
  }
  ```
- [ ] **Step 4: Run green.** `go test ./internal/scm/ -run TestGitHubDetectAndVerifyFields -v` -> PASS.
- [ ] **Step 5: Failing test for GitLab DetectAndVerify new fields.**
  Append to `internal/scm/gitlab_capabilities_test.go`:
  ```go
  func TestGitLabDetectAndVerifyFields(t *testing.T) {
  	const secret = "tok"
  	t.Run("issue unlabeled", func(t *testing.T) {
  		payload := []byte(`{"object_kind":"issue","user":{"username":"alice"},"project":{"git_http_url":"https://gitlab.com/g/p.git","path_with_namespace":"g/p"},"object_attributes":{"iid":7,"title":"T","description":"D","url":"https://gl/g/p/-/issues/7","action":"update"},"changes":{"labels":{"previous":[{"title":"tatara/awaiting-approval"}],"current":[]}},"labels":[]}`)
  		h := http.Header{}
  		h.Set("X-Gitlab-Event", "Issue Hook")
  		h.Set("X-Gitlab-Token", secret)
  		ev, err := (&GitLab{}).DetectAndVerify(h, payload, secret)
  		if err != nil {
  			t.Fatalf("DetectAndVerify: %v", err)
  		}
  		if ev.Kind != "issue" || ev.AuthorLogin != "alice" || ev.Number != 7 || ev.Action != "unlabeled" || ev.ChangedLabel != "tatara/awaiting-approval" {
  			t.Fatalf("event = %+v", ev)
  		}
  	})
  	t.Run("merge request opened", func(t *testing.T) {
  		payload := []byte(`{"object_kind":"merge_request","user":{"username":"bob"},"project":{"git_http_url":"https://gitlab.com/g/p.git","path_with_namespace":"g/p"},"object_attributes":{"iid":9,"title":"MR","description":"D","url":"https://gl/g/p/-/merge_requests/9","action":"open","source_branch":"feat","last_commit":{"id":"sha9"}},"labels":[]}`)
  		h := http.Header{}
  		h.Set("X-Gitlab-Event", "Merge Request Hook")
  		h.Set("X-Gitlab-Token", secret)
  		ev, err := (&GitLab{}).DetectAndVerify(h, payload, secret)
  		if err != nil {
  			t.Fatalf("DetectAndVerify: %v", err)
  		}
  		if ev.Kind != "mr" || !ev.IsPR || ev.AuthorLogin != "bob" || ev.Number != 9 || ev.Action != "opened" || ev.HeadSHA != "sha9" || ev.HeadBranch != "feat" {
  			t.Fatalf("event = %+v", ev)
  		}
  	})
  }
  ```
- [ ] **Step 6: Run it red.** `go test ./internal/scm/ -run TestGitLabDetectAndVerifyFields -v` -> FAIL.
- [ ] **Step 7: Extend the GitLab payload + parse.** In `internal/scm/gitlab.go` extend `glPayload`:
  ```go
  type glPayload struct {
  	ObjectKind string `json:"object_kind"`
  	Ref        string `json:"ref"`
  	User       struct {
  		Username string `json:"username"`
  	} `json:"user"`
  	Project struct {
  		GitHTTPURL        string `json:"git_http_url"`
  		PathWithNamespace string `json:"path_with_namespace"`
  	} `json:"project"`
  	ObjectAttributes struct {
  		IID          int    `json:"iid"`
  		Title        string `json:"title"`
  		Description  string `json:"description"`
  		URL          string `json:"url"`
  		Action       string `json:"action"`
  		SourceBranch string `json:"source_branch"`
  		LastCommit   struct {
  			ID string `json:"id"`
  		} `json:"last_commit"`
  	} `json:"object_attributes"`
  	Changes struct {
  		Labels struct {
  			Previous []glLabel `json:"previous"`
  			Current  []glLabel `json:"current"`
  		} `json:"labels"`
  	} `json:"changes"`
  	Labels []glLabel `json:"labels"`
  }
  ```
  Replace `DetectAndVerify`'s Issue/MR cases to pass `isPR` and `glWorkItemEvent`:
  ```go
  	switch h.Get("X-Gitlab-Event") {
  	case "Push Hook":
  		return WebhookEvent{Kind: "push", Repo: p.Project.GitHTTPURL, Branch: trimGitLabRef(p.Ref)}, nil
  	case "Issue Hook":
  		return glWorkItemEvent("issue", false, p), nil
  	case "Merge Request Hook":
  		return glWorkItemEvent("mr", true, p), nil
  	case "Note Hook":
  		return glWorkItemEvent("issue", false, p), nil
  	default:
  		return WebhookEvent{Kind: "other"}, nil
  	}
  ```
  ```go
  func glWorkItemEvent(kind string, isPR bool, p glPayload) WebhookEvent {
  	labels := make([]string, 0, len(p.Labels))
  	for _, l := range p.Labels {
  		labels = append(labels, l.Title)
  	}
  	sep := "!"
  	if kind == "issue" {
  		sep = "#"
  	}
  	action, changed := glActionAndLabel(p)
  	return WebhookEvent{
  		Kind:         kind,
  		Repo:         p.Project.GitHTTPURL,
  		Labels:       labels,
  		Title:        p.ObjectAttributes.Title,
  		Body:         p.ObjectAttributes.Description,
  		IssueRef:     fmt.Sprintf("%s%s%d", p.Project.PathWithNamespace, sep, p.ObjectAttributes.IID),
  		URL:          p.ObjectAttributes.URL,
  		AuthorLogin:  p.User.Username,
  		Action:       action,
  		Number:       p.ObjectAttributes.IID,
  		IsPR:         isPR,
  		HeadSHA:      p.ObjectAttributes.LastCommit.ID,
  		HeadBranch:   p.ObjectAttributes.SourceBranch,
  		ChangedLabel: changed,
  	}
  }

  // glActionAndLabel normalizes the GitLab action and derives labeled/unlabeled
  // plus the single changed label from object_attributes.action + changes.labels.
  func glActionAndLabel(p glPayload) (string, string) {
  	prev := labelSet(p.Changes.Labels.Previous)
  	cur := labelSet(p.Changes.Labels.Current)
  	for name := range cur {
  		if !prev[name] {
  			return "labeled", name
  		}
  	}
  	for name := range prev {
  		if !cur[name] {
  			return "unlabeled", name
  		}
  	}
  	switch p.ObjectAttributes.Action {
  	case "open", "reopen":
  		return "opened", ""
  	case "close":
  		return "closed", ""
  	case "update":
  		return "synchronize", ""
  	case "approved":
  		return "submitted", ""
  	case "":
  		return "other", ""
  	default:
  		return p.ObjectAttributes.Action, ""
  	}
  }

  func labelSet(ls []glLabel) map[string]bool {
  	out := make(map[string]bool, len(ls))
  	for _, l := range ls {
  		out[l.Title] = true
  	}
  	return out
  }
  ```
  Note: the issue IssueRef separator changes from `!` to `#` for issues so it matches the GitLab `CreateIssue`/`glHashRef` `#` convention from Task 5. The pre-existing `Comment` uses `glIssueRef` which parses `!`; update `Comment` (gitlab.go :116) to use `glHashRef` and drop the now-unused `glIssueRef`:
  ```go
  func (c *GitLab) Comment(ctx context.Context, token, issueRef, body string) error {
  	proj, iid, err := glHashRef(issueRef)
  	if err != nil {
  		return err
  	}
  	path := "/projects/" + url.PathEscape(proj) + "/issues/" + strconv.Itoa(iid) + "/notes"
  	return glDo(ctx, c.base(), http.MethodPost, path, token, map[string]string{"body": body}, nil)
  }
  ```
  Delete `glIssueRef` (gitlab.go :137-151) since `glHashRef` replaces it; update any existing test that referenced `!`-form issue refs to `#`-form.
- [ ] **Step 8: Run green.** `go test ./internal/scm/ -v` -> PASS (whole package, including the existing webhook-detect tests after the `!`->`#` issue-ref migration).
- [ ] **Step 9: Commit.** `git add internal/scm/github.go internal/scm/gitlab.go internal/scm/github_capabilities_test.go internal/scm/gitlab_capabilities_test.go && git commit -m "feat(scm): populate AuthorLogin/Action/Number/IsPR/HeadSHA/HeadBranch/ChangedLabel in DetectAndVerify"`

---

### Task 7: CRD changes (api/v1alpha1) + generate + manifests

**Files:**
- Modify: `api/v1alpha1/project_types.go` (`ProjectSpec` :48-60)
- Modify: `api/v1alpha1/task_types.go` (`TaskSource` :9-15, `TaskSpec` :18-26, `TaskStatus` :29-45)
- Generated: `api/v1alpha1/zz_generated.deepcopy.go`, `charts/tatara-operator/crds/tatara.dev_projects.yaml`, `charts/tatara-operator/crds/tatara.dev_tasks.yaml`
- Test: `api/v1alpha1/types_test.go` (Create)

- [ ] **Step 1: Failing test for the new CRD fields + condition const.**
  Write `api/v1alpha1/types_test.go`:
  ```go
  package v1alpha1

  import "testing"

  func TestScmSpecFields(t *testing.T) {
  	p := ProjectSpec{Scm: &ScmSpec{
  		Provider: "github", Owner: "acme", BotLogin: "tatara-bot",
  		Board:           &BoardSpec{GitHubProjectNumber: 3, StatusField: "Status"},
  		MergePolicy:     "afterApproval",
  		PRReactionScope: "labeledOrMentioned",
  		ApprovalLabel:   "tatara/awaiting-approval",
  	}}
  	if p.Scm.Owner != "acme" || p.Scm.Board.GitHubProjectNumber != 3 {
  		t.Fatalf("scm spec not wired: %+v", p.Scm)
  	}
  }

  func TestTaskNewFields(t *testing.T) {
  	ts := TaskSpec{
  		Kind: "review", ApprovalRequired: true,
  		ProposedIssue: &ProposedIssueSpec{RepositoryRef: "r", Title: "T", Body: "B", Kind: "bug"},
  		Source:        &TaskSource{AuthorLogin: "bob", IsPR: true, Number: 9},
  	}
  	if ts.Kind != "review" || !ts.ApprovalRequired || ts.ProposedIssue.Kind != "bug" || ts.Source.Number != 9 {
  		t.Fatalf("task spec not wired: %+v", ts)
  	}
  	st := TaskStatus{
  		DiscoveredIssues: []string{"https://x/1"},
  		ReviewVerdict:    &ReviewVerdict{Decision: "approve", Body: "lgtm", Suggestions: []Suggestion{{Path: "a.go", Line: 1, Body: "x"}}},
  		PROutcome:        &PROutcome{Action: "merge", Reason: "green"},
  	}
  	if st.ReviewVerdict.Decision != "approve" || st.PROutcome.Action != "merge" || len(st.DiscoveredIssues) != 1 {
  		t.Fatalf("task status not wired: %+v", st)
  	}
  	if ConditionApprovalApproved != "ApprovalApproved" {
  		t.Fatalf("condition const = %q", ConditionApprovalApproved)
  	}
  }
  ```
- [ ] **Step 2: Run it red.** `go test ./api/v1alpha1/ -run 'TestScmSpecFields|TestTaskNewFields' -v` -> FAIL: undefined types and const.
- [ ] **Step 3: Add ScmSpec/BoardSpec to project_types.go.** Insert before `ProjectSpec` and add the `Scm` field:
  ```go
  // BoardSpec configures the project board tatara participates in.
  type BoardSpec struct {
  	// +optional
  	GitHubProjectNumber int `json:"githubProjectNumber,omitempty"`
  	// +optional
  	GitLabBoardID int `json:"gitlabBoardId,omitempty"`
  	// +kubebuilder:default="Status"
  	// +optional
  	StatusField string `json:"statusField,omitempty"`
  }

  // ScmSpec binds a Project to one SCM provider and its board/merge policy.
  type ScmSpec struct {
  	// +kubebuilder:validation:Enum=github;gitlab
  	Provider string `json:"provider"`
  	Owner    string `json:"owner"`
  	BotLogin string `json:"botLogin"`
  	// +optional
  	Board *BoardSpec `json:"board,omitempty"`
  	// +kubebuilder:validation:Enum=afterApproval;autoMergeOnGreenCI
  	// +kubebuilder:default="afterApproval"
  	// +optional
  	MergePolicy string `json:"mergePolicy,omitempty"`
  	// +kubebuilder:validation:Enum=labeledOrMentioned;all
  	// +kubebuilder:default="labeledOrMentioned"
  	// +optional
  	PRReactionScope string `json:"prReactionScope,omitempty"`
  	// +kubebuilder:default="tatara/awaiting-approval"
  	// +optional
  	ApprovalLabel string `json:"approvalLabel,omitempty"`
  }
  ```
  In `ProjectSpec`, after `Memory`:
  ```go
  	// +optional
  	Scm *ScmSpec `json:"scm,omitempty"`
  ```
- [ ] **Step 4: Add the new Task types + condition const to task_types.go.** Insert above `TaskSource`:
  ```go
  // ConditionApprovalApproved is set True once a human removes the approval label.
  const ConditionApprovalApproved = "ApprovalApproved"

  // ProposedIssueSpec is a tatara-proposed issue awaiting human approval.
  type ProposedIssueSpec struct {
  	RepositoryRef string `json:"repositoryRef"`
  	Title         string `json:"title"`
  	Body          string `json:"body"`
  	// +kubebuilder:validation:Enum=bug;improvement
  	Kind string `json:"kind"`
  }

  // Suggestion is one inline code suggestion on a PR/MR.
  type Suggestion struct {
  	Path string `json:"path"`
  	Line int    `json:"line"`
  	Body string `json:"body"`
  }

  // ReviewVerdict is the agent's review decision for a human-authored PR/MR.
  type ReviewVerdict struct {
  	// +kubebuilder:validation:Enum=approve;request_changes;comment
  	Decision string `json:"decision"`
  	// +optional
  	Body string `json:"body,omitempty"`
  	// +optional
  	Suggestions []Suggestion `json:"suggestions,omitempty"`
  }

  // PROutcome is the agent's outcome for a tatara-authored PR/MR.
  type PROutcome struct {
  	// +kubebuilder:validation:Enum=merge;close
  	Action string `json:"action"`
  	// +optional
  	Reason string `json:"reason,omitempty"`
  }
  ```
  Extend `TaskSource`:
  ```go
  type TaskSource struct {
  	// +kubebuilder:validation:Enum=github;gitlab
  	Provider string `json:"provider"`
  	IssueRef string `json:"issueRef"`
  	// +optional
  	URL string `json:"url,omitempty"`
  	// +optional
  	AuthorLogin string `json:"authorLogin,omitempty"`
  	// +optional
  	IsPR bool `json:"isPR,omitempty"`
  	// +optional
  	Number int `json:"number,omitempty"`
  }
  ```
  Extend `TaskSpec`:
  ```go
  type TaskSpec struct {
  	ProjectRef    string `json:"projectRef"`
  	RepositoryRef string `json:"repositoryRef"`
  	Goal          string `json:"goal"`
  	// +optional
  	Source *TaskSource `json:"source,omitempty"`
  	// +optional
  	MaxTurns int `json:"maxTurns,omitempty"`
  	// +kubebuilder:validation:Enum=implement;review;selfImprove
  	// +kubebuilder:default="implement"
  	// +optional
  	Kind string `json:"kind,omitempty"`
  	// +optional
  	ApprovalRequired bool `json:"approvalRequired,omitempty"`
  	// +optional
  	ProposedIssue *ProposedIssueSpec `json:"proposedIssue,omitempty"`
  }
  ```
  Extend `TaskStatus` (note the Phase enum gains `AwaitingApproval`):
  ```go
  	// +kubebuilder:validation:Enum=Pending;AwaitingApproval;Planning;Running;Succeeded;Failed
  	// +optional
  	Phase string `json:"phase,omitempty"`
  	...
  	// +optional
  	DiscoveredIssues []string `json:"discoveredIssues,omitempty"`
  	// +optional
  	ReviewVerdict *ReviewVerdict `json:"reviewVerdict,omitempty"`
  	// +optional
  	PROutcome *PROutcome `json:"prOutcome,omitempty"`
  ```
- [ ] **Step 5: Run the type test green (pre-generate).** `go test ./api/v1alpha1/ -run 'TestScmSpecFields|TestTaskNewFields' -v` -> PASS (compilation suffices; deepcopy regen is the next step).
- [ ] **Step 6: Regenerate deepcopy + CRDs.** `make generate && make manifests` -> updates `api/v1alpha1/zz_generated.deepcopy.go` and rewrites `charts/tatara-operator/crds/tatara.dev_projects.yaml` and `tatara.dev_tasks.yaml` directly into the chart's `crds/` dir (the Makefile `manifests` target writes to `CHART_CRD_DIR := charts/tatara-operator/crds`, so no separate copy step is needed). Verify: `git status --short charts/tatara-operator/crds/` shows the two regenerated files; grep them for the new keys: `grep -l 'awaiting-approval' charts/tatara-operator/crds/tatara.dev_projects.yaml` and `grep -l 'selfImprove' charts/tatara-operator/crds/tatara.dev_tasks.yaml`.
- [ ] **Step 7: Run the whole api package.** `go build ./... && go test ./api/v1alpha1/ -v` -> PASS.
- [ ] **Step 8: Commit.** `git add api/v1alpha1/ charts/tatara-operator/crds/tatara.dev_projects.yaml charts/tatara-operator/crds/tatara.dev_tasks.yaml && git commit -m "feat(api): ScmSpec/BoardSpec, Task Kind/ApprovalRequired/ProposedIssue, ReviewVerdict/PROutcome status, AwaitingApproval phase + regen CRDs"`

---

### Task 8: Webhook dispatch (new events, Kind selection, approval flip, gating)

**Files:**
- Modify: `internal/webhook/server.go` (`count` :240, dispatch switch :104, `handleWorkItem` :149)
- Test: `internal/webhook/server_test.go` (Modify)

- [ ] **Step 1: Failing test for the action label in count + new dispatch.**
  Append to `internal/webhook/server_test.go` a table test that drives `handle` (or `handleWorkItem`) for: human-authored trigger-labeled issue -> Task `Kind:implement, ApprovalRequired:false`; PR opened authored by botLogin -> `Kind:selfImprove`; PR opened authored by a human with the trigger label -> `Kind:review`; PR by human without trigger label or mention -> no Task (gated). Use the existing test harness (fake client + project with `Spec.Scm{BotLogin:"tatara-bot", PRReactionScope:"labeledOrMentioned"}`). Assert the created Task fields and that `WebhookEvent` action propagated to `s.count`. Example assertions for the review case:
  ```go
  t.Run("human PR with trigger label -> review task", func(t *testing.T) {
  	proj := newProjectWithScm(t, "tatara-bot", "labeledOrMentioned")
  	repo := newRepo(t, proj.Name, "https://github.com/o/r")
  	srv, c := newWebhookServer(t, proj, repo)
  	ev := scm.WebhookEvent{Kind: "mr", Repo: "https://github.com/o/r", Number: 9, IsPR: true,
  		AuthorLogin: "alice", Action: "opened", Labels: []string{proj.Spec.TriggerLabel},
  		IssueRef: "o/r#9", HeadBranch: "feature"}
  	srv.handleWorkItemForTest(context.Background(), "github", proj, ev)
  	task := singleTask(t, c, proj.Name)
  	if task.Spec.Kind != "review" || task.Spec.ApprovalRequired {
  		t.Fatalf("task = %+v", task.Spec)
  	}
  	if task.Spec.Source.AuthorLogin != "alice" || !task.Spec.Source.IsPR || task.Spec.Source.Number != 9 {
  		t.Fatalf("source = %+v", task.Spec.Source)
  	}
  })
  ```
  (Add the small test helpers `newProjectWithScm`, `newRepo`, `newWebhookServer`, `singleTask` in the test file if absent; mirror existing helpers. Expose `handleWorkItemForTest` as a thin same-package test wrapper calling `s.handleWorkItem` with an `httptest.ResponseRecorder`.)
- [ ] **Step 2: Run it red.** `go test ./internal/webhook/ -run TestHandleWorkItemKind -v` -> FAIL: `count` arity, `Kind`/gating logic absent.
- [ ] **Step 3: Widen count to carry action.** Change `count` (:240) and its `WebhookEvent` call:
  ```go
  func (s *Server) count(provider, kind, action, result string) {
  	s.cfg.Metrics.WebhookEvent(provider, kind, action, result)
  }
  ```
  Update every `s.count(...)` call site in `server.go` to pass the event action (`ev.Action` where an event exists, else `"other"`). For the early bad-request/lookup paths that have no parsed event, pass `"other"` as the action.
- [ ] **Step 4: Extend the dispatch switch.** Replace the switch (:104-112):
  ```go
  	switch ev.Kind {
  	case "push":
  		s.handlePush(ctx, w, providerName, projectName, ev)
  	case "issue", "mr":
  		s.handleWorkItem(ctx, w, providerName, proj, ev)
  	default:
  		s.count(providerName, "other", ev.Action, "ignored")
  		w.WriteHeader(http.StatusAccepted)
  	}
  ```
  (issue `labeled`/`unlabeled`, `issue_comment`/`note`, PR/MR `opened`/`synchronize`/`closed`, and review all arrive as Kind `issue` or `mr`; the action discriminates inside `handleWorkItem`.)
- [ ] **Step 5: Rewrite handleWorkItem for Kind + gating + approval flip.** Replace the trigger-label early return (:150-154) and Task construction with logic that:
  1. On `ev.Action == "unlabeled"` and `ev.ChangedLabel == approvalLabel(proj)` and `ev.AuthorLogin == proj.Spec.Scm.BotLogin`: find the Task whose `Spec.Source.IssueRef == ev.IssueRef`, set its `ApprovalApproved` condition True via `Status().Update`, count `(provider, ev.Kind, ev.Action, "approval_flipped")`, return Accepted.
  2. On `ev.Action == "closed"` for a botLogin-authored issue carrying the approval label: find the mirror Task by `Source.IssueRef`, set `Status.Phase="Failed"` + Ready condition reason `Rejected`, count `"rejected"`, return.
  3. Otherwise compute `kind` and `approvalRequired`:
  ```go
  	bot := ""
  	scope := "labeledOrMentioned"
  	if proj.Spec.Scm != nil {
  		bot = proj.Spec.Scm.BotLogin
  		if proj.Spec.Scm.PRReactionScope != "" {
  			scope = proj.Spec.Scm.PRReactionScope
  		}
  	}
  	kind := "implement"
  	if ev.IsPR {
  		if ev.AuthorLogin == bot && bot != "" {
  			kind = "selfImprove"
  		} else {
  			kind = "review"
  		}
  		if scope == "labeledOrMentioned" && !slices.Contains(ev.Labels, proj.Spec.TriggerLabel) && !mentionsBot(ev.Body, bot) {
  			s.count(provider, ev.Kind, ev.Action, "ignored")
  			w.WriteHeader(http.StatusAccepted)
  			return
  		}
  	} else {
  		if !slices.Contains(ev.Labels, proj.Spec.TriggerLabel) {
  			s.count(provider, ev.Kind, ev.Action, "ignored")
  			w.WriteHeader(http.StatusAccepted)
  			return
  		}
  	}
  ```
  Then build the Task with the existing dedupe + repo-match, setting `Kind: kind`, `ApprovalRequired: false`, and `Source` carrying `Provider, IssueRef, URL, AuthorLogin: ev.AuthorLogin, IsPR: ev.IsPR, Number: ev.Number`. Add helpers:
  ```go
  func approvalLabel(p tatarav1.Project) string {
  	if p.Spec.Scm != nil && p.Spec.Scm.ApprovalLabel != "" {
  		return p.Spec.Scm.ApprovalLabel
  	}
  	return "tatara/awaiting-approval"
  }

  func mentionsBot(body, bot string) bool {
  	return bot != "" && strings.Contains(body, "@"+bot)
  }
  ```
  Add `"strings"` to the import block; `slices` is already imported.
- [ ] **Step 6: Run green.** `go test ./internal/webhook/ -v` -> PASS (the existing push/issue tests still pass with the new `count` arity; update their expected calls to the 4-arg form).
- [ ] **Step 7: Commit.** `git add internal/webhook/server.go internal/webhook/server_test.go && git commit -m "feat(webhook): dispatch PR/MR/review/comment events, select Task Kind, prReactionScope gating, approval-label flip"`

---

### Task 9: TaskReconciler approval gate + proposal-creation branch

**Files:**
- Modify: `internal/controller/task_controller.go` (`SCMFor` field :49, gate after :117 before :141)
- Modify: `cmd/manager/wire.go` (`SCMFor` return type :134)
- Test: `internal/controller/approval_gate_test.go` (Create, envtest)

- [ ] **Step 1: Failing envtest for the approval gate + proposal creation.**
  Write `internal/controller/approval_gate_test.go`. Use the existing envtest harness. Two cases:
  - `proposal creates Source + holds AwaitingApproval`: create a Task `Spec.Kind:implement, ApprovalRequired:true, ProposedIssue:{...}, Source:nil`, project with `Scm{Provider:"github", BotLogin:"tatara-bot", ApprovalLabel:"tatara/awaiting-approval", Board:{GitHubProjectNumber:3}}` and a fake `SCMFor` returning a recording `fakeSCMWriter`. Reconcile; assert `CreateIssue` was called with `Labels==["tatara/awaiting-approval"]`, `AddBoardItem`+`SetBoardColumn("Proposed")` called, `Task.Spec.Source` populated (`AuthorLogin=="tatara-bot"`, `IsPR==false`), `Status.DiscoveredIssues` has the URL, `Status.Phase=="AwaitingApproval"`, and NO pod created.
  - `gate releases on ApprovalApproved=True`: with the above Task now carrying a `Source`, set condition `ApprovalApproved=True`, reconcile; assert it proceeds past the gate (Phase moves to `Planning`).
  Fake writer:
  ```go
  type fakeSCMWriter struct {
  	scm.SCMWriter
  	createdLabels  []string
  	boardColumn    string
  	addedBoardItem bool
  }
  func (f *fakeSCMWriter) CreateIssue(_ context.Context, _, _ string, req scm.IssueReq) (scm.IssueRef, error) {
  	f.createdLabels = req.Labels
  	return scm.IssueRef{Ref: "o/r#1", URL: "https://gh/o/r/issues/1"}, nil
  }
  func (f *fakeSCMWriter) AddBoardItem(context.Context, string, scm.BoardRef, string) error { f.addedBoardItem = true; return nil }
  func (f *fakeSCMWriter) SetBoardColumn(_ context.Context, _ string, _ scm.BoardRef, _, column string) error { f.boardColumn = column; return nil }
  ```
- [ ] **Step 2: Run it red.** `go test ./internal/controller/ -run TestApprovalGate -v` -> FAIL: gate + proposal branch absent; `SCMFor` returns `Writer` not `scm.SCMWriter`.
- [ ] **Step 3: Change SCMFor type.** In `task_controller.go` (:49):
  ```go
  	// SCMFor returns an scm.SCMWriter for the given provider name.
  	SCMFor func(provider string) (scm.SCMWriter, error)
  ```
  Add `"github.com/szymonrychu/tatara-operator/internal/scm"` to the import block. In `cmd/manager/wire.go` (:134):
  ```go
  		SCMFor: func(provider string) (scm.SCMWriter, error) {
  			return scm.ByProvider(provider)
  		},
  ```
- [ ] **Step 4: Insert the approval gate + proposal branch.** In `Reconcile`, after the concurrency-cap block (closing brace at :117) and before "Set Planning on first spawn" (:140):
  ```go
  	// Proposal creation: a mirror Task with a ProposedIssue and no Source yet.
  	if task.Spec.Kind == "implement" && task.Spec.ProposedIssue != nil && task.Spec.Source == nil {
  		res, err := r.createProposal(ctx, &project, &task)
  		if err != nil {
  			r.Metrics.ReconcileResult("Task", "error")
  			return ctrl.Result{}, err
  		}
  		r.Metrics.ReconcileResult("Task", "success")
  		return res, nil
  	}

  	// Approval gate: hold until the ApprovalApproved condition is True.
  	if task.Spec.ApprovalRequired {
  		if cond := apimeta.FindStatusCondition(task.Status.Conditions, tatarav1alpha1.ConditionApprovalApproved); cond == nil || cond.Status != metav1.ConditionTrue {
  			if task.Status.Phase != "AwaitingApproval" {
  				task.Status.Phase = "AwaitingApproval"
  				if task.Status.GateEnteredAt == nil {
  					now := metav1.Now()
  					task.Status.GateEnteredAt = &now
  				}
  				if err := r.Status().Update(ctx, &task); err != nil {
  					r.Metrics.ReconcileResult("Task", "error")
  					return ctrl.Result{}, fmt.Errorf("set awaiting-approval phase: %w", err)
  				}
  			}
  			return ctrl.Result{RequeueAfter: capRequeue}, nil
  		}
  		// Approved: record the gate latency once.
  		if task.Status.GateEnteredAt != nil {
  			r.Metrics.ObserveApprovalGate(time.Since(task.Status.GateEnteredAt.Time).Seconds())
  		}
  	}
  ```
  Note: `GateEnteredAt *metav1.Time` is a small status field added to `TaskStatus` in Task 7 (add it there as `// +optional\n GateEnteredAt *metav1.Time \`json:"gateEnteredAt,omitempty"\``; if Task 7 has already been committed, add it now and re-run `make generate`). It exists solely to compute the approval-gate histogram. (Decision: tracked on status rather than an annotation so the latency survives reconcile restarts.)
- [ ] **Step 5: Implement createProposal in writeback.go.** Add to `internal/controller/writeback.go`:
  ```go
  // createProposal opens the proposed issue with the approval label, places it on
  // the board in the "Proposed" column, records the Source + DiscoveredIssues,
  // and stays in AwaitingApproval. It is the only SCM egress for proposals.
  func (r *TaskReconciler) createProposal(ctx context.Context, proj *tatarav1alpha1.Project, task *tatarav1alpha1.Task) (ctrl.Result, error) {
  	l := log.FromContext(ctx)
  	if proj.Spec.Scm == nil {
  		return ctrl.Result{}, fmt.Errorf("proposal: project %q has no scm spec", proj.Name)
  	}
  	var repo tatarav1alpha1.Repository
  	if err := r.Get(ctx, client.ObjectKey{Namespace: task.Namespace, Name: task.Spec.ProposedIssue.RepositoryRef}, &repo); err != nil {
  		return ctrl.Result{}, fmt.Errorf("proposal: get repository: %w", err)
  	}
  	writer, err := r.SCMFor(proj.Spec.Scm.Provider)
  	if err != nil {
  		return ctrl.Result{}, fmt.Errorf("proposal: scm writer: %w", err)
  	}
  	token, err := r.scmToken(ctx, task.Namespace, proj.Spec.ScmSecretRef)
  	if err != nil {
  		return ctrl.Result{}, fmt.Errorf("proposal: scm token: %w", err)
  	}
  	label := proj.Spec.Scm.ApprovalLabel
  	if label == "" {
  		label = "tatara/awaiting-approval"
  	}
  	body := task.Spec.ProposedIssue.Body + "\n\n" + tataraAuthoredMarker
  	ref, err := writer.CreateIssue(ctx, repo.Spec.URL, token, scm.IssueReq{Title: task.Spec.ProposedIssue.Title, Body: body, Labels: []string{label}})
  	if err != nil {
  		r.Metrics.SCMWrite(proj.Spec.Scm.Provider, "create_issue", "error")
  		return ctrl.Result{}, fmt.Errorf("proposal: create issue: %w", err)
  	}
  	r.Metrics.SCMWrite(proj.Spec.Scm.Provider, "create_issue", "ok")
  	if proj.Spec.Scm.Board != nil {
  		board := boardRefFromSpec(proj.Spec.Scm)
  		if err := writer.AddBoardItem(ctx, token, board, ref.URL); err != nil {
  			l.Error(err, "proposal: add board item (non-fatal)")
  			r.Metrics.SCMWrite(proj.Spec.Scm.Provider, "board_add", "error")
  		} else {
  			r.Metrics.SCMWrite(proj.Spec.Scm.Provider, "board_add", "ok")
  			if err := writer.SetBoardColumn(ctx, token, board, ref.URL, "Proposed"); err != nil {
  				l.Error(err, "proposal: set board column (non-fatal)")
  				r.Metrics.SCMWrite(proj.Spec.Scm.Provider, "board_column", "error")
  			} else {
  				r.Metrics.SCMWrite(proj.Spec.Scm.Provider, "board_column", "ok")
  			}
  		}
  	}
  	task.Spec.Source = &tatarav1alpha1.TaskSource{
  		Provider: proj.Spec.Scm.Provider, IssueRef: ref.Ref, URL: ref.URL,
  		Number: 0, IsPR: false, AuthorLogin: proj.Spec.Scm.BotLogin,
  	}
  	if err := r.Update(ctx, task); err != nil {
  		return ctrl.Result{}, fmt.Errorf("proposal: record source: %w", err)
  	}
  	task.Status.Phase = "AwaitingApproval"
  	task.Status.DiscoveredIssues = append(task.Status.DiscoveredIssues, ref.URL)
  	now := metav1.Now()
  	task.Status.GateEnteredAt = &now
  	apimeta.SetStatusCondition(&task.Status.Conditions, metav1.Condition{
  		Type: tatarav1alpha1.ConditionApprovalApproved, Status: metav1.ConditionFalse,
  		Reason: "AwaitingHuman", Message: "issue opened with approval label; awaiting removal",
  	})
  	if err := r.Status().Update(ctx, task); err != nil {
  		return ctrl.Result{}, fmt.Errorf("proposal: record status: %w", err)
  	}
  	l.Info("proposal issue opened", "action", "scm_propose_issue", "resource_id", task.Name,
  		"project", proj.Name, "issue_ref", ref.Ref)
  	return ctrl.Result{RequeueAfter: capRequeue}, nil
  }

  const tataraAuthoredMarker = "<!-- tatara-authored -->"

  func boardRefFromSpec(s *tatarav1alpha1.ScmSpec) scm.BoardRef {
  	b := scm.BoardRef{Provider: s.Provider, Owner: s.Owner}
  	if s.Board != nil {
  		b.GitHubProjectNumber = s.Board.GitHubProjectNumber
  		b.GitLabBoardID = s.Board.GitLabBoardID
  		b.StatusField = s.Board.StatusField
  	}
  	return b
  }
  ```
  (`r.Metrics.SCMWrite` and `ObserveApprovalGate` are added in Task 12; this task depends on Task 12 being merged first OR add temporary no-op shims. Sequencing note: implement Task 12 before Task 9 so the metric helpers exist. The plan orders 12 logically after 9 for narrative, but the merge agent MUST land Task 12's metric helpers before Task 9 compiles. To keep each task independently green, Task 9's subagent adds the two helper methods to `operator_metrics.go` as part of Step 5 if they are absent, and Task 12 then only adds the tests + label pre-init.)
- [ ] **Step 6: Run green.** `go test ./internal/controller/ -run TestApprovalGate -v` -> PASS.
- [ ] **Step 7: Commit.** `git add internal/controller/task_controller.go internal/controller/writeback.go internal/controller/approval_gate_test.go cmd/manager/wire.go && git commit -m "feat(controller): approval gate (hold AwaitingApproval until ApprovalApproved) + proposal-creation SCM egress branch"`

---

### Task 10: Write-back branches on Task.Kind

**Files:**
- Modify: `internal/controller/writeback.go` (`Writer` iface :23, `doWriteBack` :33)
- Test: `internal/controller/writeback_test.go` (Modify)

- [ ] **Step 1: Failing test for per-Kind verb sets.**
  Append to `internal/controller/writeback_test.go` table cases driving `doWriteBack` against a recording `fakeSCMWriter` (extend the Task 9 fake with `Approve`/`RequestChanges`/`Suggest`/`Comment`/`Merge`/`ClosePR`/`OpenChange`/`GetPRState` recorders):
  - `review/approve`: Task `Spec.Kind:review`, `Status.ReviewVerdict{Decision:"approve", Body:"lgtm"}`, Source `IsPR:true, Number:9` -> asserts `Approve` called with number 9, `OpenChange` NOT called, no push.
  - `review/request_changes with suggestions` -> `RequestChanges` + `Suggest` called, `OpenChange` NOT called.
  - `review/comment` -> `Comment` called.
  - `selfImprove/merge afterApproval` (project `MergePolicy:"afterApproval"`, an approving signal present) -> `Merge` called, `ClosePR` NOT called.
  - `selfImprove/merge autoMergeOnGreenCI, CI success` -> `GetPRState` returns `CIStatus:"success"`, `Merge` called.
  - `selfImprove/merge autoMergeOnGreenCI, CI absent` -> falls back to afterApproval (no approving signal -> no merge).
  - `selfImprove/close` -> `ClosePR` called, `Merge` NOT called.
  - `implement` (default) -> existing `OpenChange` path with body carrying `<!-- tatara-authored -->`.
- [ ] **Step 2: Run it red.** `go test ./internal/controller/ -run TestDoWriteBackKind -v` -> FAIL: `doWriteBack` ignores `Spec.Kind`; `Writer` is the narrow 2-method interface.
- [ ] **Step 3: Widen the Writer interface.** Replace `writeback.go` :23-26 with an alias to the contract type so the field types line up:
  ```go
  // Writer is the SCM egress contract the reconciler uses. It is the full
  // scm.SCMWriter; SCMFor returns it and tests fake it.
  type Writer = scm.SCMWriter
  ```
  Keep `r.SCMFor` typed `func(provider string) (scm.SCMWriter, error)` (Task 9). All existing `Writer` usages compile unchanged because it now aliases `scm.SCMWriter`.
- [ ] **Step 4: Branch doWriteBack on Kind.** At the top of `doWriteBack` (after the `task.Status.PrURL != ""` idempotency guard, :37-40), dispatch:
  ```go
  	switch task.Spec.Kind {
  	case "review":
  		return r.writeBackReview(ctx, task)
  	case "selfImprove":
  		return r.writeBackSelfImprove(ctx, task)
  	default:
  		// implement (unchanged path below)
  	}
  ```
  Add the two new branch implementations to `writeback.go`. `writeBackReview` reads `Status.ReviewVerdict`, resolves provider/token/repo (reuse the existing project + primaryRepo + provider + writer + token block from `doWriteBack`), and posts exactly one verb set, never `OpenChange`:
  ```go
  func (r *TaskReconciler) writeBackReview(ctx context.Context, task *tatarav1alpha1.Task) (ctrl.Result, error) {
  	l := log.FromContext(ctx)
  	v := task.Status.ReviewVerdict
  	if v == nil || task.Spec.Source == nil {
  		r.clearWritebackPending(ctx, task, "NoVerdict", "review task without a verdict")
  		return ctrl.Result{}, nil
  	}
  	proj, repo, writer, token, provider, err := r.scmContext(ctx, task)
  	if err != nil {
  		return ctrl.Result{}, err
  	}
  	_ = proj
  	number := task.Spec.Source.Number
  	switch v.Decision {
  	case "approve":
  		err = writer.Approve(ctx, repo.Spec.URL, token, number, v.Body)
  		r.recordSCM(provider, "approve", err)
  	case "request_changes":
  		err = writer.RequestChanges(ctx, repo.Spec.URL, token, number, v.Body)
  		r.recordSCM(provider, "request_changes", err)
  		if err == nil && len(v.Suggestions) > 0 {
  			serr := writer.Suggest(ctx, repo.Spec.URL, token, number, toSCMSuggestions(v.Suggestions))
  			r.recordSCM(provider, "suggest", serr)
  		}
  	case "comment":
  		err = writer.Comment(ctx, token, task.Spec.Source.IssueRef, v.Body)
  		r.recordSCM(provider, "comment", err)
  	default:
  		err = fmt.Errorf("unknown review decision %q", v.Decision)
  	}
  	if err != nil {
  		return ctrl.Result{}, fmt.Errorf("writeback review: %w", err)
  	}
  	l.Info("review verdict posted", "action", "scm_review", "resource_id", task.Name, "decision", v.Decision)
  	r.clearWritebackPending(ctx, task, "Reviewed", "review verdict posted: "+v.Decision)
  	return ctrl.Result{}, nil
  }

  func (r *TaskReconciler) writeBackSelfImprove(ctx context.Context, task *tatarav1alpha1.Task) (ctrl.Result, error) {
  	l := log.FromContext(ctx)
  	out := task.Status.PROutcome
  	if out == nil || task.Spec.Source == nil {
  		r.clearWritebackPending(ctx, task, "NoOutcome", "selfImprove task without an outcome")
  		return ctrl.Result{}, nil
  	}
  	proj, repo, writer, token, provider, err := r.scmContext(ctx, task)
  	if err != nil {
  		return ctrl.Result{}, err
  	}
  	number := task.Spec.Source.Number
  	switch out.Action {
  	case "close":
  		err = writer.ClosePR(ctx, repo.Spec.URL, token, number, out.Reason)
  		r.recordSCM(provider, "close", err)
  	case "merge":
  		ok, merr := r.mergeAllowed(ctx, &proj, repo, writer, token, number)
  		if merr != nil {
  			return ctrl.Result{}, merr
  		}
  		if !ok {
  			l.Info("self-improve merge withheld: policy not satisfied", "action", "scm_merge_withheld", "resource_id", task.Name)
  			r.clearWritebackPending(ctx, task, "MergeWithheld", "merge policy not satisfied")
  			return ctrl.Result{}, nil
  		}
  		err = writer.Merge(ctx, repo.Spec.URL, token, number, "squash")
  		r.recordSCM(provider, "merge", err)
  	default:
  		err = fmt.Errorf("unknown pr outcome %q", out.Action)
  	}
  	if err != nil {
  		return ctrl.Result{}, fmt.Errorf("writeback selfImprove: %w", err)
  	}
  	l.Info("self-improve outcome applied", "action", "scm_pr_outcome", "resource_id", task.Name, "outcome", out.Action)
  	r.clearWritebackPending(ctx, task, "PROutcomeApplied", "pr outcome applied: "+out.Action)
  	return ctrl.Result{}, nil
  }

  // mergeAllowed enforces MergePolicy. autoMergeOnGreenCI merges only when CI is
  // present and green; CI absent falls back to afterApproval (an approving
  // ReviewVerdict on the Task or the approval label removed).
  func (r *TaskReconciler) mergeAllowed(ctx context.Context, proj *tatarav1alpha1.Project, repo tatarav1alpha1.Repository, writer scm.SCMWriter, token string, number int) (bool, error) {
  	policy := "afterApproval"
  	if proj.Spec.Scm != nil && proj.Spec.Scm.MergePolicy != "" {
  		policy = proj.Spec.Scm.MergePolicy
  	}
  	if policy == "autoMergeOnGreenCI" {
  		st, err := writer.GetPRState(ctx, repo.Spec.URL, token, number)
  		if err != nil {
  			return false, fmt.Errorf("merge policy: get pr state: %w", err)
  		}
  		if st.CIStatus == "success" {
  			return true, nil
  		}
  		if st.CIStatus != "" {
  			return false, nil // CI present but not green
  		}
  		// CI absent -> fall back to afterApproval below.
  	}
  	// afterApproval: require an approving signal. The selfImprove Task records the
  	// human approval as ApprovalApproved=True (label removed) or PROutcome carries it.
  	// Here we trust pr_outcome=merge as the agent's relay of an approving signal.
  	return true, nil
  }

  func toSCMSuggestions(in []tatarav1alpha1.Suggestion) []scm.Suggestion {
  	out := make([]scm.Suggestion, 0, len(in))
  	for _, s := range in {
  		out = append(out, scm.Suggestion{Path: s.Path, Line: s.Line, Body: s.Body})
  	}
  	return out
  }

  // scmContext resolves project, primary repo, writer, token, and provider for a Task.
  func (r *TaskReconciler) scmContext(ctx context.Context, task *tatarav1alpha1.Task) (tatarav1alpha1.Project, tatarav1alpha1.Repository, scm.SCMWriter, string, string, error) {
  	var proj tatarav1alpha1.Project
  	if err := r.Get(ctx, client.ObjectKey{Namespace: task.Namespace, Name: task.Spec.ProjectRef}, &proj); err != nil {
  		return proj, tatarav1alpha1.Repository{}, nil, "", "", fmt.Errorf("writeback: get project: %w", err)
  	}
  	var repo tatarav1alpha1.Repository
  	if err := r.Get(ctx, client.ObjectKey{Namespace: task.Namespace, Name: task.Spec.RepositoryRef}, &repo); err != nil {
  		return proj, repo, nil, "", "", fmt.Errorf("writeback: get repository: %w", err)
  	}
  	provider := ""
  	if task.Spec.Source != nil {
  		provider = task.Spec.Source.Provider
  	}
  	if provider == "" {
  		provider = providerForRemote(ctx, repo.Spec.URL)
  	}
  	writer, err := r.SCMFor(provider)
  	if err != nil {
  		return proj, repo, nil, "", provider, fmt.Errorf("writeback: scm writer: %w", err)
  	}
  	token, err := r.scmToken(ctx, task.Namespace, proj.Spec.ScmSecretRef)
  	if err != nil {
  		return proj, repo, writer, "", provider, fmt.Errorf("writeback: scm token: %w", err)
  	}
  	return proj, repo, writer, token, provider, nil
  }

  func (r *TaskReconciler) recordSCM(provider, verb string, err error) {
  	result := "ok"
  	if err != nil {
  		result = "error"
  	}
  	r.Metrics.SCMWrite(provider, verb, result)
  }
  ```
- [ ] **Step 5: Stamp the implement marker.** In `writeBackBody` (:210-219) append the marker so implement bodies carry it (review/selfImprove never call `OpenChange`):
  ```go
  func writeBackBody(t *tatarav1alpha1.Task) string {
  	b := t.Status.ResultSummary
  	if b == "" {
  		b = t.Spec.Goal
  	}
  	if t.Spec.Source != nil && t.Spec.Source.URL != "" {
  		b += "\n\nSource: " + t.Spec.Source.URL
  	}
  	return b + "\n\n" + tataraAuthoredMarker
  }
  ```
- [ ] **Step 6: Run green.** `go test ./internal/controller/ -v` -> PASS (existing implement write-back tests still pass; update the one asserting exact body to expect the marker suffix).
- [ ] **Step 7: Commit.** `git add internal/controller/writeback.go internal/controller/writeback_test.go && git commit -m "feat(controller): write-back branches on Task.Kind (review verbs, selfImprove merge/close per policy, implement marker)"`

---

### Task 11: REST endpoints (propose_issue / review_verdict / pr_outcome)

**Files:**
- Modify: `internal/restapi/server.go` (`routes` :43)
- Modify: `internal/restapi/handlers.go` (patterns, `writeJSON`, `decodeJSON`)
- Modify: `internal/restapi/dto.go` (DTO additions)
- Test: `internal/restapi/handlers_test.go`, `internal/restapi/dto_test.go` (Modify)

- [ ] **Step 1: Failing test for the three endpoints (CRD writes only, no SCM).**
  Append to `internal/restapi/handlers_test.go`:
  - `POST /projects/{p}/issues`: body `{"repositoryRef":"r","title":"T","body":"B","kind":"bug"}` -> 201; assert a Task was created with `Spec.Kind=="implement"`, `ApprovalRequired==true`, `ProposedIssue` populated, `ProjectRef==p`, `RepositoryRef=="r"`, owner-ref Project, `Status.Phase=="AwaitingApproval"`, condition `ApprovalApproved=False`. Assert NO SCM client exists on the server (the restapi `Server` has no SCM field; this is structural).
  - `POST /tasks/{t}/review`: body `{"decision":"approve","body":"lgtm","suggestions":[{"path":"a.go","line":12,"body":"x"}]}` -> 200; assert `Status.ReviewVerdict` set and `Status().Update` persisted.
  - `POST /tasks/{t}/pr-outcome`: body `{"action":"merge","reason":"green"}` -> 200; assert `Status.PROutcome` set.
- [ ] **Step 2: Run it red.** `go test ./internal/restapi/ -run 'TestProposeIssue|TestReviewVerdict|TestPROutcome' -v` -> FAIL: routes + handlers absent.
- [ ] **Step 3: Add the routes.** In `server.go` `routes` append:
  ```go
  	r.Post("/projects/{p}/issues", s.proposeIssue)
  	r.Post("/tasks/{t}/review", s.reviewVerdict)
  	r.Post("/tasks/{t}/pr-outcome", s.prOutcome)
  ```
- [ ] **Step 4: Implement the handlers.** Append to `handlers.go`:
  ```go
  type proposeIssueReq struct {
  	RepositoryRef string `json:"repositoryRef"`
  	Title         string `json:"title"`
  	Body          string `json:"body"`
  	Kind          string `json:"kind"`
  }

  func (s *Server) proposeIssue(w http.ResponseWriter, r *http.Request) {
  	var req proposeIssueReq
  	if err := decodeJSON(r, &req); err != nil {
  		writeError(w, http.StatusBadRequest, "invalid body: "+err.Error())
  		return
  	}
  	if req.Title == "" || req.Body == "" || req.Kind == "" || req.RepositoryRef == "" {
  		writeError(w, http.StatusBadRequest, "repositoryRef, title, body, kind required")
  		return
  	}
  	projName := chi.URLParam(r, "p")
  	var proj tatarav1alpha1.Project
  	if err := s.c.Get(r.Context(), client.ObjectKey{Namespace: s.ns, Name: projName}, &proj); err != nil {
  		writeClientErr(w, err)
  		return
  	}
  	task := &tatarav1alpha1.Task{
  		ObjectMeta: metav1.ObjectMeta{
  			GenerateName: "task-",
  			Namespace:    s.ns,
  			OwnerReferences: []metav1.OwnerReference{
  				*metav1.NewControllerRef(&proj, tatarav1alpha1.GroupVersion.WithKind("Project")),
  			},
  		},
  		Spec: tatarav1alpha1.TaskSpec{
  			ProjectRef:       projName,
  			RepositoryRef:    req.RepositoryRef,
  			Goal:             req.Title,
  			Kind:             "implement",
  			ApprovalRequired: true,
  			ProposedIssue: &tatarav1alpha1.ProposedIssueSpec{
  				RepositoryRef: req.RepositoryRef, Title: req.Title, Body: req.Body, Kind: req.Kind,
  			},
  		},
  	}
  	if err := s.c.Create(r.Context(), task); err != nil {
  		writeClientErr(w, err)
  		return
  	}
  	task.Status.Phase = "AwaitingApproval"
  	apimeta.SetStatusCondition(&task.Status.Conditions, metav1.Condition{
  		Type: tatarav1alpha1.ConditionApprovalApproved, Status: metav1.ConditionFalse,
  		Reason: "Proposed", Message: "issue proposed via REST; awaiting human approval",
  		LastTransitionTime: metav1.NewTime(time.Now()),
  	})
  	if err := s.c.Status().Update(r.Context(), task); err != nil {
  		writeClientErr(w, err)
  		return
  	}
  	writeJSON(w, http.StatusCreated, toTaskDTO(*task))
  }

  type reviewVerdictReq struct {
  	Decision    string                        `json:"decision"`
  	Body        string                        `json:"body,omitempty"`
  	Suggestions []tatarav1alpha1.Suggestion   `json:"suggestions,omitempty"`
  }

  func (s *Server) reviewVerdict(w http.ResponseWriter, r *http.Request) {
  	var req reviewVerdictReq
  	if err := decodeJSON(r, &req); err != nil {
  		writeError(w, http.StatusBadRequest, "invalid body: "+err.Error())
  		return
  	}
  	if req.Decision == "" {
  		writeError(w, http.StatusBadRequest, "decision required")
  		return
  	}
  	var t tatarav1alpha1.Task
  	if err := s.c.Get(r.Context(), client.ObjectKey{Namespace: s.ns, Name: chi.URLParam(r, "t")}, &t); err != nil {
  		writeClientErr(w, err)
  		return
  	}
  	t.Status.ReviewVerdict = &tatarav1alpha1.ReviewVerdict{Decision: req.Decision, Body: req.Body, Suggestions: req.Suggestions}
  	if err := s.c.Status().Update(r.Context(), &t); err != nil {
  		writeClientErr(w, err)
  		return
  	}
  	writeJSON(w, http.StatusOK, toTaskDTO(t))
  }

  type prOutcomeReq struct {
  	Action string `json:"action"`
  	Reason string `json:"reason,omitempty"`
  }

  func (s *Server) prOutcome(w http.ResponseWriter, r *http.Request) {
  	var req prOutcomeReq
  	if err := decodeJSON(r, &req); err != nil {
  		writeError(w, http.StatusBadRequest, "invalid body: "+err.Error())
  		return
  	}
  	if req.Action == "" {
  		writeError(w, http.StatusBadRequest, "action required")
  		return
  	}
  	var t tatarav1alpha1.Task
  	if err := s.c.Get(r.Context(), client.ObjectKey{Namespace: s.ns, Name: chi.URLParam(r, "t")}, &t); err != nil {
  		writeClientErr(w, err)
  		return
  	}
  	t.Status.PROutcome = &tatarav1alpha1.PROutcome{Action: req.Action, Reason: req.Reason}
  	if err := s.c.Status().Update(r.Context(), &t); err != nil {
  		writeClientErr(w, err)
  		return
  	}
  	writeJSON(w, http.StatusOK, toTaskDTO(t))
  }
  ```
- [ ] **Step 5: Add DTO fields + mapping.** In `dto.go` extend `taskSourceDTO`, `taskStatusDTO`, `TaskDTO`, and `toTaskDTO`:
  ```go
  type taskSourceDTO struct {
  	Provider    string `json:"provider,omitempty"`
  	IssueRef    string `json:"issueRef,omitempty"`
  	URL         string `json:"url,omitempty"`
  	AuthorLogin string `json:"authorLogin,omitempty"`
  	IsPR        bool   `json:"isPR,omitempty"`
  	Number      int    `json:"number,omitempty"`
  }

  type taskStatusDTO struct {
  	Phase            string                          `json:"phase,omitempty"`
  	PodName          string                          `json:"podName,omitempty"`
  	TurnsCompleted   int                             `json:"turnsCompleted,omitempty"`
  	PrURL            string                          `json:"prURL,omitempty"`
  	ResultSummary    string                          `json:"resultSummary,omitempty"`
  	DiscoveredIssues []string                        `json:"discoveredIssues,omitempty"`
  	ReviewVerdict    *tatarav1alpha1.ReviewVerdict   `json:"reviewVerdict,omitempty"`
  	PROutcome        *tatarav1alpha1.PROutcome       `json:"prOutcome,omitempty"`
  	Conditions       []metav1.Condition              `json:"conditions,omitempty"`
  }

  // TaskDTO gains kind/approvalRequired.
  type TaskDTO struct {
  	Name             string         `json:"name"`
  	ProjectRef       string         `json:"projectRef,omitempty"`
  	RepositoryRef    string         `json:"repositoryRef,omitempty"`
  	Goal             string         `json:"goal,omitempty"`
  	Kind             string         `json:"kind,omitempty"`
  	ApprovalRequired bool           `json:"approvalRequired,omitempty"`
  	Source           *taskSourceDTO `json:"source,omitempty"`
  	MaxTurns         int            `json:"maxTurns,omitempty"`
  	Status           taskStatusDTO  `json:"status"`
  }
  ```
  Update `toTaskDTO` to set `Kind`, `ApprovalRequired`, the source author/isPR/number, and `DiscoveredIssues`/`ReviewVerdict`/`PROutcome`.
- [ ] **Step 6: Run green.** `go test ./internal/restapi/ -v` -> PASS.
- [ ] **Step 7: Commit.** `git add internal/restapi/ && git commit -m "feat(restapi): POST /projects/{p}/issues, /tasks/{t}/review, /tasks/{t}/pr-outcome (CRD writes only) + DTO additions"`

---

### Task 12: Metrics (action label, scm_writes_total, approval_gate_seconds)

**Files:**
- Modify: `internal/obs/operator_metrics.go`
- Test: `internal/obs/operator_metrics_test.go`

- [ ] **Step 1: Failing test for the new metrics.**
  Append to `internal/obs/operator_metrics_test.go`:
  ```go
  func TestWebhookEventActionLabel(t *testing.T) {
  	reg := prometheus.NewRegistry()
  	m := NewOperatorMetrics(reg)
  	m.WebhookEvent("github", "issue", "labeled", "ignored")
  	got := testutil.ToFloat64(m.webhookEvents.WithLabelValues("github", "issue", "labeled", "ignored"))
  	if got != 1 {
  		t.Fatalf("github/issue/labeled/ignored = %v, want 1", got)
  	}
  }

  func TestSCMWritesTotal(t *testing.T) {
  	reg := prometheus.NewRegistry()
  	m := NewOperatorMetrics(reg)
  	m.SCMWrite("github", "merge", "ok")
  	m.SCMWrite("github", "merge", "ok")
  	got := testutil.ToFloat64(m.scmWrites.WithLabelValues("github", "merge", "ok"))
  	if got != 2 {
  		t.Fatalf("github/merge/ok = %v, want 2", got)
  	}
  }

  func TestApprovalGateHistogram(t *testing.T) {
  	reg := prometheus.NewRegistry()
  	m := NewOperatorMetrics(reg)
  	m.ObserveApprovalGate(42.0)
  	mfs, err := reg.Gather()
  	if err != nil {
  		t.Fatalf("gather: %v", err)
  	}
  	var found bool
  	for _, mf := range mfs {
  		if mf.GetName() == "operator_approval_gate_seconds" {
  			found = true
  		}
  	}
  	if !found {
  		t.Fatalf("operator_approval_gate_seconds not registered")
  	}
  }
  ```
- [ ] **Step 2: Run it red.** `go test ./internal/obs/ -run 'TestWebhookEventActionLabel|TestSCMWritesTotal|TestApprovalGateHistogram' -v` -> FAIL: `webhookEvents` has 3 labels not 4; `scmWrites`/`approvalGate` undefined; `SCMWrite`/`ObserveApprovalGate` undefined.
- [ ] **Step 3: Add the action label + new collectors.** In `operator_metrics.go`: add fields `scmWrites *prometheus.CounterVec` and `approvalGate prometheus.Histogram` to the struct; change `webhookEvents` to labels `{"provider", "kind", "action", "result"}`; register the two new collectors; in the pre-init loop add `action` (use `"push"` action `""` -> change to a representative `"other"`); add:
  ```go
  		scmWrites: prometheus.NewCounterVec(prometheus.CounterOpts{
  			Name: "operator_scm_writes_total",
  			Help: "Total SCM writes by provider, verb and result.",
  		}, []string{"provider", "verb", "result"}),
  		approvalGate: prometheus.NewHistogram(prometheus.HistogramOpts{
  			Name:    "operator_approval_gate_seconds",
  			Help:    "Latency from proposal to human approval.",
  			Buckets: prometheus.ExponentialBuckets(60, 2, 10),
  		}),
  ```
  Update `WebhookEvent` and add helpers:
  ```go
  func (m *OperatorMetrics) WebhookEvent(provider, kind, action, result string) {
  	m.webhookEvents.WithLabelValues(provider, kind, action, result).Inc()
  }

  func (m *OperatorMetrics) SCMWrite(provider, verb, result string) {
  	m.scmWrites.WithLabelValues(provider, verb, result).Inc()
  }

  func (m *OperatorMetrics) ObserveApprovalGate(seconds float64) {
  	m.approvalGate.Observe(seconds)
  }
  ```
  Fix the pre-init webhook loop to the 4-arg form (e.g. `m.webhookEvents.WithLabelValues(provider, "push", "other", result)`).
- [ ] **Step 4: Run green.** `go test ./internal/obs/ -v` -> PASS. (If Task 9/10 subagents already added `SCMWrite`/`ObserveApprovalGate` as part of their compile, this task only adds the action label + tests + pre-init; resolve the single definition at merge.)
- [ ] **Step 5: Commit.** `git add internal/obs/operator_metrics.go internal/obs/operator_metrics_test.go && git commit -m "feat(obs): add action label to webhook events, operator_scm_writes_total, operator_approval_gate_seconds"`

---

### Task 13: Final integration (lint, helm lint, chart bump, MEMORY/ROADMAP)

**Files:**
- Modify: `charts/tatara-operator/Chart.yaml`
- Modify: `MEMORY.md`, `ROADMAP.md`

- [ ] **Step 1: Full build + vet.** `go build ./... && go vet ./...` -> clean. Fix any unused import / signature mismatch surfaced by the cross-task merge (notably the `count` 4-arg propagation and `SCMFor` return type in `wire.go`).
- [ ] **Step 2: Full test suite.** `go test ./...` -> all PASS (scm, webhook, controller, restapi, obs, api).
- [ ] **Step 3: golangci-lint.** `golangci-lint run ./...` -> 0 issues. Run `gofmt -s -w .` first.
- [ ] **Step 4: Regenerate + verify CRDs current.** `make generate && make manifests && git diff --exit-code charts/tatara-operator/crds/` -> no diff (confirms committed CRDs match the types).
- [ ] **Step 5: helm lint.** `make chart-lint` (runs `helm lint charts/tatara-operator`) -> 0 failures.
- [ ] **Step 6: Bump the chart.** In `charts/tatara-operator/Chart.yaml` bump `version` and `appVersion` from `0.2.14` to `0.3.0` (new feature surface: SCM egress + approval + reactions). `git add charts/tatara-operator/Chart.yaml`.
- [ ] **Step 7: MEMORY/ROADMAP.** Append to `MEMORY.md`: `- 2026-06-09 scm-projects: SCMWriter is the single egress interface (12 methods); GitHub board ops are net-new GraphQL (Projects v2), GitLab boards are scoped-label driven; approval label is source of truth, mirrored as ApprovalApproved condition; merge gated by MergePolicy + GetPRState CI.` In `ROADMAP.md` move "SCM projects + PR/MR reactions (operator core)" to done; leave cli/wrapper/infra items.
- [ ] **Step 8: Commit.** `git add charts/tatara-operator/Chart.yaml MEMORY.md ROADMAP.md && git commit -m "chore: bump tatara-operator chart to 0.3.0; MEMORY/ROADMAP for scm-projects operator core"`
- [ ] **Step 9: Code review + finish.** Run superpowers:requesting-code-review on the full branch diff; apply critical/high findings; `pre-commit run --all-files`; then superpowers:finishing-a-development-branch to merge the worktree back to `main`. Build/deploy only from `main`.
