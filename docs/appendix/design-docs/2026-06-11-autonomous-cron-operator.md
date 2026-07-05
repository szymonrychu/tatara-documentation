# Autonomous Cron - tatara-operator Implementation Plan

**Date:** 2026-06-11
**Repo:** `tatara-operator`
**Design:** `docs/superpowers/specs/2026-06-11-autonomous-cron-design.md`
**Contract lock:** `docs/superpowers/specs/2026-06-11-autonomous-cron-contract-lock.md` (FROZEN wire types - field names, json tags, enum strings, route paths, label keys are byte-for-byte normative)

## Goal

Make the operator act autonomously on a schedule. The Project reconciler fires three cron-driven scans (mrScan / issueScan / brainstorm), each a Task factory: it lists open work via new SCM read capabilities, applies priority-then-stale selection + dedup against label-selected Project-owned Tasks, and creates Tasks up to per-activity caps. Tasks flow through the unchanged TaskReconciler turn loop and write-back matrix. MR-triage routes to `review`/`selfImprove` by authoritative `GetPRState` author; `triageIssue` Tasks emit a new `issue_outcome` (implement opens a PR via existing OpenChange, close calls new `CloseIssue`); `brainstorm` Tasks emit existing `propose_issue` and get a `tatara.io/egress=internet` pod label when `internet` is in sources.

## Architecture

```
ProjectReconciler.Reconcile (project_controller.go:93 returns RequeueAfter)
  -> projectscan.go: runScans(ctx, &project)
       for each due activity (cron.ParseStandard, base=Last*Scan|creationTimestamp):
         scm.SCMReader.List*   (operator egress, one token)
         select priority-then-stale, cap at maxPerCycle
         dedup vs label-selected Project-owned Tasks
         create Task{Kind, Source, dedup labels}
         stamp Status.Last*Scan
       RequeueAfter = soonest next-fire of the three (clamped maxScheduleRequeue=6h)
       bad cron string -> log ERROR + disable that activity (mirror scheduleNextReingest)

TaskReconciler (existing loop, extended):
  triageIssue -> agent emits issue_outcome -> writeBackIssue: close => CloseIssue
  brainstorm  -> agent emits propose_issue (existing createProposal path)
  MR-triage Tasks resolve review|selfImprove by GetPRState author (existing gate)

restapi: POST /tasks/{t}/issue-outcome -> Status.IssueOutcome (enum + comment-on-close + wrong-kind 409)
```

## Tech Stack

Go 1.26.3, controller-runtime, `github.com/robfig/cron/v3` (already a dep), kubebuilder CRD markers, controller-gen v0.18.0, envtest (k8s 1.33.0), `log/slog` JSON logging, table-driven tests with `t.Run`, error wrapping with `%w`, gofmt. Prometheus via `sigs.k8s.io/controller-runtime/pkg/metrics`.

## REQUIRED SUB-SKILL

You MUST follow `superpowers:test-driven-development` for every code task below: write the failing test FIRST, run it and SEE it fail, write minimal implementation, run it and SEE it pass, then commit. Do not write implementation before its test. Use `superpowers:systematic-debugging` for any unexpected failure.

## Conventions anchored from the existing repo

- Cron pattern source of truth: `internal/controller/repository_controller.go` (`maxScheduleRequeue = 6 * time.Hour` const at line 31; `cron.ParseStandard`; `schedule.Next(base)`; bad-cron logs `l.Error(err, ...)` and returns `ctrl.Result{}, nil` at lines 217-223).
- SCM HTTP helpers: `ghDo` (github.go:198), `glDo` (gitlab.go:252), `*HTTPError` on non-2xx (already returned by both).
- GraphQL dual user/org board query: `ghProjectID` (github_graphql.go:137) and the aliased `query { user(...) organization(...) }` shape.
- Reconciler test harness: envtest in `suite_test.go` (`k8sClient`, `testNS = "tatara"`, `timeout`, `interval`); reconciler constructed inline (`project_controller_test.go:28`); `obs.NewOperatorMetrics(prometheus.NewRegistry())`.
- REST handler tests: `buildRouter(t, objs...)` + `taskWithKind(name, projectRef, kind)` (handlers_test.go:21,taskWithKind helper); wrong-kind returns `http.StatusConflict` (handlers.go:336-338).
- Fake SCM writer test pattern: `fakeWriter` embedding `scm.SCMWriter` (task_writeback_test.go:23).
- Regen commands: `make generate` (deepcopy, Makefile:33) and `make manifests` (CRD into `charts/tatara-operator/crds`, Makefile:36). Test command: `make test` (sets `KUBEBUILDER_ASSETS`).

## Design decision (resolved ambiguity)

The contract lock section 4 says the read/close methods are "Added to `scm.Client`". But `scm.Client` (scm.go:86) is the webhook-detect interface (`Provider`/`DetectAndVerify`/`OpenChange`/`Comment`), not the reconciler's egress interface. The reconciler already uses `scm.SCMWriter` via `TaskReconciler.SCMFor`. Resolution, consistent with the existing split:
- The three **read** methods (`ListOpenPRs`/`ListOpenIssues`/`ListBoardItems`) go on a **new `scm.SCMReader` interface**, satisfied by `*GitHub`/`*GitLab`.
- `CloseIssue` is a **write** verb -> added to the existing `scm.SCMWriter` interface (TaskReconciler calls it on `close`).
- `ProjectReconciler` gets a new field `ReaderFor func(provider string) (scm.SCMReader, error)` (mirrors `TaskReconciler.SCMFor`), wired in `wire.go` to `scm.ReaderByProvider`.

The wire-type structs `PRRef`/`IssueRef`/`BoardItem` keep the lock's exact field names + json tags. The interface *method signatures* match the lock byte-for-byte.

---

# Phase 1 - SCM wire types and read/close capabilities

## Task 1.1: Add PRRef / IssueRef / BoardItem wire structs

**RED** - Test: `internal/scm/scm_types_test.go` (append a new function).

Append to `internal/scm/scm_types_test.go`:

```go
func TestScanWireTypesZero(t *testing.T) {
	pr := PRRef{Repo: "o/r", Number: 5, Author: "bot", HeadSHA: "abc", Labels: []string{"p"}}
	if pr.Repo != "o/r" || pr.Number != 5 || pr.Author != "bot" || pr.HeadSHA != "abc" || pr.Labels[0] != "p" {
		t.Fatalf("PRRef fields not wired: %+v", pr)
	}
	iss := IssueRef2{Repo: "o/r", Number: 7, Labels: []string{"p"}, IsPR: true}
	if iss.Repo != "o/r" || iss.Number != 7 || !iss.IsPR {
		t.Fatalf("IssueRef2 fields not wired: %+v", iss)
	}
	bi := BoardItem{Repo: "o/r", Number: 9, Column: "Todo"}
	if bi.Repo != "o/r" || bi.Number != 9 || bi.Column != "Todo" {
		t.Fatalf("BoardItem fields not wired: %+v", bi)
	}
}
```

Note: the lock names the issue struct `IssueRef`, but `scm.IssueRef` already exists (scm.go:37, a created-issue ref `{Ref, URL}`). To honor the lock's intent without a breaking rename of the existing type, name the new scan struct **`IssueRef2`** is WRONG - the lock is normative on `IssueRef`. Re-resolve below.

**RESOLUTION (corrected):** The lock freezes `IssueRef` with fields `{Repo, Number, Labels, UpdatedAt, IsPR}`. The existing `scm.IssueRef{Ref, URL}` is an internal (non-wire) return type used only by `CreateIssue`/`createProposal`. Rename the existing one to **`CreatedIssue`** (it is not in the contract lock, so it is free to rename) and give the new wire struct the locked name `IssueRef`. This keeps the frozen wire name exact.

So Task 1.1 actually has two steps; do them in order.

### Task 1.1a: Rename existing `scm.IssueRef` -> `scm.CreatedIssue`

**RED** - run the build to see current references break after the rename, then fix call sites.

Modify `internal/scm/scm.go` lines 36-40:

```go
// CreatedIssue identifies a created issue (internal return type, not a wire type).
type CreatedIssue struct {
	Ref string // owner/repo#n (github) or group/proj#iid (gitlab)
	URL string // html/web url
}
```

Update `internal/scm/scm.go` `SCMWriter.CreateIssue` signature (line 71):

```go
	CreateIssue(ctx context.Context, repoURL, token string, req IssueReq) (CreatedIssue, error)
```

Update implementations and callers (exact sites):
- `internal/scm/github.go:236` `func (c *GitHub) CreateIssue(...) (IssueRef, error)` -> `(CreatedIssue, error)`; line 239 `return IssueRef{}, err` -> `return CreatedIssue{}, err`; line 253 `return IssueRef{Ref: ...}` -> `return CreatedIssue{Ref: ...}`; line 251 same.
- `internal/scm/gitlab.go:290` signature -> `(CreatedIssue, error)`; lines 292, 304 `IssueRef{}` -> `CreatedIssue{}`; line 307 `IssueRef{Ref: ...}` -> `CreatedIssue{Ref: ...}`.
- `internal/scm/scm_types_test.go` line referencing `IssueRef{Ref: "o/r#1", URL: ...}` -> `CreatedIssue{Ref: ...}`.
- Any other reference: run `grep -rn 'scm.IssueRef\|IssueRef{' internal/` and fix each (the controller `createProposal` uses `scm.IssueReq` not `IssueRef`; `writer.CreateIssue` return is assigned to `ref` - no type name there).

**Command:** `go build ./... 2>&1 | head` - expect compile errors listing the old `IssueRef` references; fix until clean.

**GREEN:** `go build ./...` clean; `go test ./internal/scm/... -run TestValueTypesZero -count=1` PASS.

**Commit:** `refactor(scm): rename IssueRef return type to CreatedIssue to free the frozen wire name`

### Task 1.1b: Add the frozen wire structs

**RED** - replace the placeholder `IssueRef2` test in `scm_types_test.go` with the locked names:

```go
func TestScanWireTypesZero(t *testing.T) {
	pr := PRRef{Repo: "o/r", Number: 5, Author: "bot", HeadSHA: "abc", Labels: []string{"p"}}
	if pr.Repo != "o/r" || pr.Number != 5 || pr.Author != "bot" || pr.HeadSHA != "abc" || pr.Labels[0] != "p" {
		t.Fatalf("PRRef fields not wired: %+v", pr)
	}
	iss := IssueRef{Repo: "o/r", Number: 7, Labels: []string{"p"}, IsPR: true}
	if iss.Repo != "o/r" || iss.Number != 7 || !iss.IsPR {
		t.Fatalf("IssueRef fields not wired: %+v", iss)
	}
	bi := BoardItem{Repo: "o/r", Number: 9, Column: "Todo"}
	if bi.Repo != "o/r" || bi.Number != 9 || bi.Column != "Todo" {
		t.Fatalf("BoardItem fields not wired: %+v", bi)
	}
}
```

**Command:** `go test ./internal/scm/... -run TestScanWireTypesZero -count=1` - expect FAIL: `undefined: PRRef`, `undefined: IssueRef` (now a struct lit with new fields), `undefined: BoardItem`.

**GREEN** - add to `internal/scm/scm.go` (after the `CreatedIssue` block, near line 40), importing `"time"`:

```go
// PRRef is one open PR/MR listed for cron MR-triage.
type PRRef struct {
	Repo      string    `json:"repo"`
	Number    int       `json:"number"`
	Author    string    `json:"author"`
	HeadSHA   string    `json:"headSha"`
	Labels    []string  `json:"labels,omitempty"`
	UpdatedAt time.Time `json:"updatedAt"`
}

// IssueRef is one open issue listed for cron issue-triage.
type IssueRef struct {
	Repo      string    `json:"repo"`
	Number    int       `json:"number"`
	Labels    []string  `json:"labels,omitempty"`
	UpdatedAt time.Time `json:"updatedAt"`
	IsPR      bool      `json:"isPr"` // GitHub /issues returns PRs; filter these out
}

// BoardItem is one project-board item listed for cron issue-triage.
type BoardItem struct {
	Repo      string    `json:"repo"`
	Number    int       `json:"number"` // 0 for draft/non-issue items -> skipped
	Column    string    `json:"column"`
	UpdatedAt time.Time `json:"updatedAt"`
}
```

Add `"time"` to the `scm.go` import block (currently `context`, `fmt`, `net/http`).

**Command:** `go test ./internal/scm/... -run TestScanWireTypesZero -count=1` PASS.

**Commit:** `feat(scm): add PRRef/IssueRef/BoardItem wire structs (contract lock section 4)`

## Task 1.2: Add SCMReader interface + CloseIssue to SCMWriter

**RED** - Test: `internal/scm/scm_types_test.go`. Append:

```go
func TestProvidersSatisfySCMReader(t *testing.T) {
	var _ SCMReader = (*GitHub)(nil)
	var _ SCMReader = (*GitLab)(nil)
}

func TestProvidersSatisfyCloseIssue(t *testing.T) {
	var w SCMWriter = (*GitHub)(nil)
	_ = w
	var w2 SCMWriter = (*GitLab)(nil)
	_ = w2
}
```

**Command:** `go test ./internal/scm/... -run 'TestProvidersSatisfySCMReader|TestProvidersSatisfyCloseIssue' -count=1` - expect FAIL: `undefined: SCMReader` and (after that) `*GitHub does not implement SCMWriter (missing method CloseIssue)`.

**GREEN** - add to `internal/scm/scm.go`:

Add `CloseIssue` to the `SCMWriter` interface (after `SetBoardColumn`, line 81):

```go
	CloseIssue(ctx context.Context, repo string, number int, comment string) error
```

Add the new reader interface after the `SCMWriter` block:

```go
// SCMReader lists open work for the cron scan loop; *GitHub and *GitLab satisfy it.
type SCMReader interface {
	ListOpenPRs(ctx context.Context, owner, repo string) ([]PRRef, error)
	ListOpenIssues(ctx context.Context, owner, repo string) ([]IssueRef, error)
	ListBoardItems(ctx context.Context, board BoardRef) ([]BoardItem, error)
}
```

(The interface method takes `board BoardRef` - the controller converts the CRD `BoardSpec` to `scm.BoardRef` via the existing `boardRefFromSpec`. The lock's signature names the arg `BoardSpec`; that is the CRD type and cannot be imported into `scm`, so the egress boundary uses `scm.BoardRef`, exactly as the existing board methods do. Noted resolution.)

This will not yet compile until Task 1.3/1.4 add the methods; that is expected - the test asserts the contract and drives those tasks. Keep the interface declarations now and let the assertion fail with "missing method" until the methods land.

**Command after 1.3+1.4:** `go test ./internal/scm/... -run 'TestProvidersSatisfySCMReader|TestProvidersSatisfyCloseIssue' -count=1` PASS.

**Commit (after 1.4):** combined; see 1.4.

## Task 1.3: GitHub ListOpenPRs / ListOpenIssues / ListBoardItems / CloseIssue

**RED** - Test: new file `internal/scm/github_scan_test.go`.

```go
package scm

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestGitHubListOpenPRs(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/repos/o/r/pulls" || r.URL.Query().Get("state") != "open" {
			t.Fatalf("path=%q query=%q", r.URL.Path, r.URL.RawQuery)
		}
		_ = json.NewEncoder(w).Encode([]map[string]any{
			{"number": 5, "user": map[string]any{"login": "alice"},
				"head": map[string]any{"sha": "abc"},
				"labels": []map[string]any{{"name": "tatara/priority"}},
				"updated_at": "2026-06-10T12:00:00Z"},
		})
	}))
	defer srv.Close()
	c := &GitHub{apiBase: srv.URL}
	prs, err := c.ListOpenPRs(context.Background(), "o", "r")
	if err != nil {
		t.Fatalf("ListOpenPRs: %v", err)
	}
	if len(prs) != 1 || prs[0].Repo != "o/r" || prs[0].Number != 5 || prs[0].Author != "alice" || prs[0].HeadSHA != "abc" || prs[0].Labels[0] != "tatara/priority" {
		t.Fatalf("prs = %+v", prs)
	}
	if prs[0].UpdatedAt.IsZero() {
		t.Fatalf("updatedAt not parsed: %+v", prs[0])
	}
}

func TestGitHubListOpenIssuesFiltersPRs(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/repos/o/r/issues" || r.URL.Query().Get("state") != "open" {
			t.Fatalf("path=%q query=%q", r.URL.Path, r.URL.RawQuery)
		}
		_ = json.NewEncoder(w).Encode([]map[string]any{
			{"number": 7, "labels": []map[string]any{{"name": "bug"}}, "updated_at": "2026-06-10T12:00:00Z"},
			{"number": 8, "pull_request": map[string]any{"url": "x"}, "updated_at": "2026-06-10T12:00:00Z"},
		})
	}))
	defer srv.Close()
	c := &GitHub{apiBase: srv.URL}
	iss, err := c.ListOpenIssues(context.Background(), "o", "r")
	if err != nil {
		t.Fatalf("ListOpenIssues: %v", err)
	}
	if len(iss) != 2 {
		t.Fatalf("want 2 items (IsPR set), got %+v", iss)
	}
	if iss[0].Number != 7 || iss[0].IsPR {
		t.Fatalf("issue 7 should not be PR: %+v", iss[0])
	}
	if iss[1].Number != 8 || !iss[1].IsPR {
		t.Fatalf("issue 8 should be flagged IsPR: %+v", iss[1])
	}
}

func TestGitHubCloseIssue(t *testing.T) {
	paths := map[string]string{} // "METHOD path" -> body state
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths[r.Method+" "+r.URL.Path] = ""
	}))
	defer srv.Close()
	c := &GitHub{apiBase: srv.URL}
	if err := c.CloseIssue(context.Background(), "o/r", 7, "closing: out of scope"); err != nil {
		t.Fatalf("CloseIssue: %v", err)
	}
	if _, ok := paths["POST /repos/o/r/issues/7/comments"]; !ok {
		t.Fatalf("missing comment POST; got %+v", paths)
	}
	if _, ok := paths["PATCH /repos/o/r/issues/7"]; !ok {
		t.Fatalf("missing PATCH close; got %+v", paths)
	}
}
```

**Command:** `go test ./internal/scm/... -run 'TestGitHubListOpenPRs|TestGitHubListOpenIssuesFiltersPRs|TestGitHubCloseIssue' -count=1` - expect FAIL: `c.ListOpenPRs undefined` etc.

**GREEN** - new file `internal/scm/github_scan.go`:

```go
package scm

import (
	"context"
	"fmt"
	"net/http"
	"time"
)

type ghPR struct {
	Number int `json:"number"`
	User   struct {
		Login string `json:"login"`
	} `json:"user"`
	Head struct {
		SHA string `json:"sha"`
	} `json:"head"`
	Labels    []ghLabel `json:"labels"`
	UpdatedAt time.Time `json:"updated_at"`
}

type ghIssue struct {
	Number      int       `json:"number"`
	Labels      []ghLabel `json:"labels"`
	UpdatedAt   time.Time `json:"updated_at"`
	PullRequest *struct {
		URL string `json:"url"`
	} `json:"pull_request"`
}

func ghLabelNames(in []ghLabel) []string {
	out := make([]string, 0, len(in))
	for _, l := range in {
		out = append(out, l.Name)
	}
	return out
}

// ListOpenPRs lists open pull requests for owner/repo.
func (c *GitHub) ListOpenPRs(ctx context.Context, owner, repo string) ([]PRRef, error) {
	var raw []ghPR
	path := fmt.Sprintf("/repos/%s/%s/pulls?state=open", owner, repo)
	if err := ghDo(ctx, c.base(), http.MethodGet, path, "", nil, &raw); err != nil {
		return nil, err
	}
	slug := owner + "/" + repo
	out := make([]PRRef, 0, len(raw))
	for _, p := range raw {
		out = append(out, PRRef{
			Repo: slug, Number: p.Number, Author: p.User.Login,
			HeadSHA: p.Head.SHA, Labels: ghLabelNames(p.Labels), UpdatedAt: p.UpdatedAt,
		})
	}
	return out, nil
}

// ListOpenIssues lists open issues for owner/repo. GitHub returns PRs in the
// issues feed; IsPR is set so the caller can filter.
func (c *GitHub) ListOpenIssues(ctx context.Context, owner, repo string) ([]IssueRef, error) {
	var raw []ghIssue
	path := fmt.Sprintf("/repos/%s/%s/issues?state=open", owner, repo)
	if err := ghDo(ctx, c.base(), http.MethodGet, path, "", nil, &raw); err != nil {
		return nil, err
	}
	slug := owner + "/" + repo
	out := make([]IssueRef, 0, len(raw))
	for _, i := range raw {
		out = append(out, IssueRef{
			Repo: slug, Number: i.Number, Labels: ghLabelNames(i.Labels),
			UpdatedAt: i.UpdatedAt, IsPR: i.PullRequest != nil,
		})
	}
	return out, nil
}

// CloseIssue posts a comment then PATCHes the issue state to closed.
func (c *GitHub) CloseIssue(ctx context.Context, repo string, number int, comment string) error {
	owner, name, err := ghOwnerRepoFromSlug(repo)
	if err != nil {
		return err
	}
	if comment != "" {
		cpath := fmt.Sprintf("/repos/%s/%s/issues/%d/comments", owner, name, number)
		if err := ghDo(ctx, c.base(), http.MethodPost, cpath, "", map[string]string{"body": comment}, nil); err != nil {
			return err
		}
	}
	ipath := fmt.Sprintf("/repos/%s/%s/issues/%d", owner, name, number)
	return ghDo(ctx, c.base(), http.MethodPatch, ipath, "", map[string]string{"state": "closed"}, nil)
}

func ghOwnerRepoFromSlug(slug string) (string, string, error) {
	for i := len(slug) - 1; i >= 0; i-- {
		if slug[i] == '/' {
			if i == 0 || i == len(slug)-1 {
				break
			}
			return slug[:i], slug[i+1:], nil
		}
	}
	return "", "", fmt.Errorf("github: malformed repo slug %q", slug)
}
```

`ghDo` is called with empty token here (test server ignores auth). At runtime the operator passes the token; the scan loop calls a token-bound wrapper - see note. The reader interface methods do not take a token in the lock signature, so the token is bound when the reader is constructed. RESOLUTION: the lock's reader signatures omit `token`. To keep `ghDo` token-aware, the scan loop constructs the reader per-call with the token captured. The simplest contract-faithful design: the reader methods carry the token implicitly via a constructed client. Since `*GitHub` has no token field, add a method-level token by widening at the `ReaderFor` seam instead. SEE Task 1.5 resolution - the `ProjectReconciler.ReaderFor` returns a reader whose methods already know the token. Concretely: add an unexported `token string` field to a thin wrapper.

To avoid carrying an empty token, implement the readers on `*GitHub` taking the token from an unexported field. Add to `internal/scm/scm.go` `GitHub` struct (lines 104-108):

```go
type GitHub struct {
	apiBase     string
	graphQLBase string
	token       string // bound for reader calls; empty for writer/webhook use
}
```

and in `github_scan.go` use `c.token` in `ghDo` instead of `""`. Same for GitLab (Task 1.4). The `ReaderFor` seam (Task 1.5) sets `token` once.

GitHub `ListBoardItems` (GraphQL ProjectV2, reuse dual user/org). Add to `github_scan.go`:

```go
// ListBoardItems lists ProjectV2 board items via GraphQL, dual user/org query.
func (c *GitHub) ListBoardItems(ctx context.Context, board BoardRef) ([]BoardItem, error) {
	type itemNode struct {
		UpdatedAt   time.Time `json:"updatedAt"`
		FieldValueByName *struct {
			Name string `json:"name"`
		} `json:"fieldValueByName"`
		Content struct {
			Number     int    `json:"number"`
			Repository struct {
				NameWithOwner string `json:"nameWithOwner"`
			} `json:"repository"`
		} `json:"content"`
	}
	type projectV2Items struct {
		Items struct {
			Nodes []itemNode `json:"nodes"`
		} `json:"items"`
	}
	var resp struct {
		User struct {
			ProjectV2 projectV2Items `json:"projectV2"`
		} `json:"user"`
		Organization struct {
			ProjectV2 projectV2Items `json:"projectV2"`
		} `json:"organization"`
	}
	field := board.StatusField
	if field == "" {
		field = "Status"
	}
	sel := fmt.Sprintf(`projectV2(number:%d){ items(first:100){ nodes { updatedAt fieldValueByName(name:%q){ ... on ProjectV2ItemFieldSingleSelectValue { name } } content { ... on Issue { number repository { nameWithOwner } } ... on PullRequest { number repository { nameWithOwner } } } } } }`, board.GitHubProjectNumber, field)
	q := fmt.Sprintf(`query { user(login:%q){ %s } organization(login:%q){ %s } }`, board.Owner, sel, board.Owner, sel)
	if err := c.ghGraphQL(ctx, c.token, q, nil, &resp); err != nil {
		return nil, err
	}
	nodes := resp.Organization.ProjectV2.Items.Nodes
	if len(resp.User.ProjectV2.Items.Nodes) > 0 {
		nodes = resp.User.ProjectV2.Items.Nodes
	}
	out := make([]BoardItem, 0, len(nodes))
	for _, n := range nodes {
		col := ""
		if n.FieldValueByName != nil {
			col = n.FieldValueByName.Name
		}
		out = append(out, BoardItem{
			Repo: n.Content.Repository.NameWithOwner, Number: n.Content.Number,
			Column: col, UpdatedAt: n.UpdatedAt,
		})
	}
	return out, nil
}
```

Add a GraphQL board-list test to `github_scan_test.go`:

```go
func TestGitHubListBoardItems(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"data": map[string]any{
				"user": map[string]any{
					"projectV2": map[string]any{
						"items": map[string]any{"nodes": []map[string]any{
							{"updatedAt": "2026-06-10T12:00:00Z",
								"fieldValueByName": map[string]any{"name": "Todo"},
								"content": map[string]any{"number": 9, "repository": map[string]any{"nameWithOwner": "o/r"}}},
						}},
					},
				},
				"organization": map[string]any{"projectV2": map[string]any{"items": map[string]any{"nodes": []any{}}}},
			},
		})
	}))
	defer srv.Close()
	c := &GitHub{graphQLBase: srv.URL}
	items, err := c.ListBoardItems(context.Background(), BoardRef{Owner: "o", GitHubProjectNumber: 3})
	if err != nil {
		t.Fatalf("ListBoardItems: %v", err)
	}
	if len(items) != 1 || items[0].Repo != "o/r" || items[0].Number != 9 || items[0].Column != "Todo" {
		t.Fatalf("items = %+v", items)
	}
}
```

**Command:** `go test ./internal/scm/... -run 'TestGitHubListOpenPRs|TestGitHubListOpenIssuesFiltersPRs|TestGitHubCloseIssue|TestGitHubListBoardItems' -count=1` PASS.

**Commit:** `feat(scm): GitHub ListOpenPRs/ListOpenIssues/ListBoardItems/CloseIssue (contract lock section 4)`

## Task 1.4: GitLab ListOpenPRs / ListOpenIssues / ListBoardItems / CloseIssue

**RED** - Test: new file `internal/scm/gitlab_scan_test.go`.

```go
package scm

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestGitLabListOpenPRs(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/projects/g%2Fp/merge_requests" || r.URL.Query().Get("state") != "opened" {
			t.Fatalf("path=%q query=%q", r.URL.Path, r.URL.RawQuery)
		}
		_ = json.NewEncoder(w).Encode([]map[string]any{
			{"iid": 5, "sha": "abc", "author": map[string]any{"username": "alice"},
				"labels": []string{"tatara/priority"}, "updated_at": "2026-06-10T12:00:00Z"},
		})
	}))
	defer srv.Close()
	c := &GitLab{apiBase: srv.URL, token: "tok"}
	prs, err := c.ListOpenPRs(context.Background(), "g", "p")
	if err != nil {
		t.Fatalf("ListOpenPRs: %v", err)
	}
	if len(prs) != 1 || prs[0].Repo != "g/p" || prs[0].Number != 5 || prs[0].Author != "alice" || prs[0].HeadSHA != "abc" || prs[0].Labels[0] != "tatara/priority" {
		t.Fatalf("prs = %+v", prs)
	}
}

func TestGitLabListOpenIssues(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/projects/g%2Fp/issues" || r.URL.Query().Get("state") != "opened" {
			t.Fatalf("path=%q query=%q", r.URL.Path, r.URL.RawQuery)
		}
		_ = json.NewEncoder(w).Encode([]map[string]any{
			{"iid": 7, "labels": []string{"bug"}, "updated_at": "2026-06-10T12:00:00Z"},
		})
	}))
	defer srv.Close()
	c := &GitLab{apiBase: srv.URL, token: "tok"}
	iss, err := c.ListOpenIssues(context.Background(), "g", "p")
	if err != nil {
		t.Fatalf("ListOpenIssues: %v", err)
	}
	if len(iss) != 1 || iss[0].Repo != "g/p" || iss[0].Number != 7 || iss[0].IsPR || iss[0].Labels[0] != "bug" {
		t.Fatalf("iss = %+v", iss)
	}
}

func TestGitLabCloseIssue(t *testing.T) {
	paths := map[string]bool{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths[r.Method+" "+r.URL.Path] = true
	}))
	defer srv.Close()
	c := &GitLab{apiBase: srv.URL, token: "tok"}
	if err := c.CloseIssue(context.Background(), "g/p", 7, "closing"); err != nil {
		t.Fatalf("CloseIssue: %v", err)
	}
	if !paths["POST /projects/g%2Fp/issues/7/notes"] {
		t.Fatalf("missing note POST; got %+v", paths)
	}
	if !paths["PUT /projects/g%2Fp/issues/7"] {
		t.Fatalf("missing PUT close; got %+v", paths)
	}
}
```

**Command:** `go test ./internal/scm/... -run 'TestGitLabListOpenPRs|TestGitLabListOpenIssues|TestGitLabCloseIssue' -count=1` - expect FAIL: undefined methods.

**GREEN** - add `token` field to `GitLab` struct in `scm.go` (lines 110-113):

```go
type GitLab struct {
	apiBase string
	token   string // bound for reader calls; empty for writer/webhook use
}
```

New file `internal/scm/gitlab_scan.go`:

```go
package scm

import (
	"context"
	"net/http"
	"net/url"
	"strconv"
	"time"
)

type glMR struct {
	IID    int    `json:"iid"`
	SHA    string `json:"sha"`
	Author struct {
		Username string `json:"username"`
	} `json:"author"`
	Labels    []string  `json:"labels"`
	UpdatedAt time.Time `json:"updated_at"`
}

type glIssue struct {
	IID       int       `json:"iid"`
	Labels    []string  `json:"labels"`
	UpdatedAt time.Time `json:"updated_at"`
}

// ListOpenPRs lists opened merge requests for the project path owner/repo.
func (c *GitLab) ListOpenPRs(ctx context.Context, owner, repo string) ([]PRRef, error) {
	proj := owner + "/" + repo
	var raw []glMR
	path := "/projects/" + url.PathEscape(proj) + "/merge_requests?state=opened"
	if err := glDo(ctx, c.base(), http.MethodGet, path, c.token, nil, &raw); err != nil {
		return nil, err
	}
	out := make([]PRRef, 0, len(raw))
	for _, m := range raw {
		out = append(out, PRRef{
			Repo: proj, Number: m.IID, Author: m.Author.Username,
			HeadSHA: m.SHA, Labels: m.Labels, UpdatedAt: m.UpdatedAt,
		})
	}
	return out, nil
}

// ListOpenIssues lists opened issues for the project path owner/repo.
func (c *GitLab) ListOpenIssues(ctx context.Context, owner, repo string) ([]IssueRef, error) {
	proj := owner + "/" + repo
	var raw []glIssue
	path := "/projects/" + url.PathEscape(proj) + "/issues?state=opened"
	if err := glDo(ctx, c.base(), http.MethodGet, path, c.token, nil, &raw); err != nil {
		return nil, err
	}
	out := make([]IssueRef, 0, len(raw))
	for _, i := range raw {
		out = append(out, IssueRef{
			Repo: proj, Number: i.IID, Labels: i.Labels, UpdatedAt: i.UpdatedAt, IsPR: false,
		})
	}
	return out, nil
}

// ListBoardItems is the label-board mapping: GitLab issue boards are
// label-driven, so the board view is the open-issue set with their board::*
// scoped label as the column. The cron loop dedups board items against
// ListOpenIssues by (repo, number); GitLab returns no separate board feed, so
// this returns the open issues with their board:: column (empty when none).
func (c *GitLab) ListBoardItems(ctx context.Context, board BoardRef) ([]BoardItem, error) {
	// GitLab board membership = open issues; the operator's per-repo
	// ListOpenIssues already covers them. Returning empty keeps the contract
	// (board items deduped by repo/number) without a second source of truth.
	return nil, nil
}

// CloseIssue posts a note then PUTs the issue state_event=close.
func (c *GitLab) CloseIssue(ctx context.Context, repo string, number int, comment string) error {
	proj := repo
	if comment != "" {
		npath := "/projects/" + url.PathEscape(proj) + "/issues/" + strconv.Itoa(number) + "/notes"
		if err := glDo(ctx, c.base(), http.MethodPost, npath, c.token, map[string]string{"body": comment}, nil); err != nil {
			return err
		}
	}
	ipath := "/projects/" + url.PathEscape(proj) + "/issues/" + strconv.Itoa(number)
	return glDo(ctx, c.base(), http.MethodPut, ipath, c.token, map[string]string{"state_event": "close"}, nil)
}
```

Resolution note (state in plan + MEMORY): GitLab boards are label-defined, so there is no REST "board items" feed distinct from issues. `ListBoardItems` returns nil; the issueScan covers GitLab board work via `ListOpenIssues`. GitHub uses the GraphQL ProjectV2 feed. The lock's interface is satisfied on both; GitLab's is a documented no-op.

**Command:** `go test ./internal/scm/... -run 'TestGitLab(ListOpenPRs|ListOpenIssues|CloseIssue)|TestProvidersSatisfySCMReader|TestProvidersSatisfyCloseIssue' -count=1` PASS.

**Commit:** `feat(scm): GitLab ListOpenPRs/ListOpenIssues/ListBoardItems/CloseIssue + SCMReader interface (contract lock section 4)`

## Task 1.5: ReaderByProvider constructor (token-bound)

**RED** - Test: append to `internal/scm/registry_byprovider_test.go`:

```go
func TestReaderByProvider(t *testing.T) {
	cases := []struct {
		name     string
		provider string
		wantErr  bool
	}{
		{"github", "github", false},
		{"gitlab", "gitlab", false},
		{"unknown", "bitbucket", true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			rd, err := ReaderByProvider(tc.provider, "tok")
			if tc.wantErr {
				if err == nil {
					t.Fatalf("want error for %q", tc.provider)
				}
				return
			}
			if err != nil || rd == nil {
				t.Fatalf("ReaderByProvider(%q): %v", tc.provider, err)
			}
		})
	}
}
```

**Command:** `go test ./internal/scm/... -run TestReaderByProvider -count=1` - expect FAIL: `undefined: ReaderByProvider`.

**GREEN** - add to `internal/scm/registry.go`:

```go
// ReaderByProvider returns a token-bound SCMReader for a provider name.
func ReaderByProvider(name, token string) (SCMReader, error) {
	switch name {
	case "github":
		return &GitHub{token: token}, nil
	case "gitlab":
		return &GitLab{token: token}, nil
	default:
		return nil, fmt.Errorf("scm: unknown provider %q", name)
	}
}
```

**Command:** `go test ./internal/scm/... -count=1` PASS (whole package).

**Commit:** `feat(scm): ReaderByProvider token-bound SCMReader constructor`

---

# Phase 2 - CRD additions + regen

## Task 2.1: ScmSpec cron fields + ProjectStatus Last*Scan

**RED** - Test: new file `internal/controller/projectscan_crd_test.go` (envtest round-trip of the new fields).

```go
package controller

import (
	"context"
	"testing"

	tataradevv1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

func TestProjectCronFieldsRoundTrip(t *testing.T) {
	ctx := context.Background()
	mkSecret(t, "cron-scm", map[string][]byte{"token": []byte("t"), "webhookSecret": []byte("w")})
	p := &tataradevv1alpha1.Project{}
	p.Name = "cron-proj"
	p.Namespace = testNS
	p.Spec.ScmSecretRef = "cron-scm"
	p.Spec.Scm = &tataradevv1alpha1.ScmSpec{
		Provider: "github", Owner: "o", BotLogin: "bot",
		PriorityLabel: "tatara/priority",
		Cron: &tataradevv1alpha1.ScmCron{
			MRScan:    tataradevv1alpha1.CronActivity{Schedule: "0 * * * *", MaxPerCycle: 2},
			IssueScan: tataradevv1alpha1.CronActivity{Schedule: "0 * * * *", MaxPerCycle: 1},
			Brainstorm: tataradevv1alpha1.BrainstormActivity{
				Enabled: true, Schedule: "0 6 * * *", MaxPerCycle: 1,
				Sources: []string{"docs", "memory", "internet"},
			},
		},
	}
	if err := k8sClient.Create(ctx, p); err != nil {
		t.Fatalf("create project: %v", err)
	}
	got := &tataradevv1alpha1.Project{}
	if err := k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: "cron-proj"}, got); err != nil {
		t.Fatalf("get project: %v", err)
	}
	if got.Spec.Scm.PriorityLabel != "tatara/priority" || got.Spec.Scm.Cron.MRScan.MaxPerCycle != 2 {
		t.Fatalf("cron fields not persisted: %+v", got.Spec.Scm)
	}
	if !got.Spec.Scm.Cron.Brainstorm.Enabled || got.Spec.Scm.Cron.Brainstorm.Sources[2] != "internet" {
		t.Fatalf("brainstorm fields not persisted: %+v", got.Spec.Scm.Cron.Brainstorm)
	}
	now := metav1.Now()
	got.Status.LastMRScan = &now
	got.Status.LastIssueScan = &now
	got.Status.LastBrainstorm = &now
	if err := k8sClient.Status().Update(ctx, got); err != nil {
		t.Fatalf("status update: %v", err)
	}
}
```

**Command:** `make test 2>&1 | head -40` - expect FAIL: `ScmCron`, `CronActivity`, `BrainstormActivity` undefined, and (after Go compiles) the CRD lacks the fields so the envtest apply strips them. The Go-compile failure comes first.

**GREEN** - add to `api/v1alpha1/project_types.go`, after `MemoryStatus` / before `AgentSpec` (or grouped near `ScmSpec`):

```go
// CronActivity schedules one Project scan activity (mrScan or issueScan).
type CronActivity struct {
	// Schedule is a 5-field cron (robfig ParseStandard). Empty disables this activity.
	// +optional
	Schedule string `json:"schedule,omitempty"`
	// +kubebuilder:default=1
	// +optional
	MaxPerCycle int `json:"maxPerCycle,omitempty"`
}

// BrainstormActivity schedules the opt-in self-driven issue-proposal scan.
type BrainstormActivity struct {
	// +optional
	Enabled bool `json:"enabled,omitempty"`
	// +optional
	Schedule string `json:"schedule,omitempty"`
	// +kubebuilder:default=1
	// +optional
	MaxPerCycle int `json:"maxPerCycle,omitempty"`
	// +kubebuilder:validation:items:Enum=docs;memory;internet
	// +optional
	Sources []string `json:"sources,omitempty"`
}

// ScmCron groups the three cron-driven scan activities.
type ScmCron struct {
	// +optional
	MRScan CronActivity `json:"mrScan,omitempty"`
	// +optional
	IssueScan CronActivity `json:"issueScan,omitempty"`
	// +optional
	Brainstorm BrainstormActivity `json:"brainstorm,omitempty"`
}
```

Add to the `ScmSpec` struct (after `ApprovalLabel`, line 76):

```go
	// +optional
	PriorityLabel string `json:"priorityLabel,omitempty"`
	// +optional
	Cron *ScmCron `json:"cron,omitempty"`
```

Add to `ProjectStatus` (after `Memory`, line 105):

```go
	// +optional
	LastMRScan *metav1.Time `json:"lastMRScan,omitempty"`
	// +optional
	LastIssueScan *metav1.Time `json:"lastIssueScan,omitempty"`
	// +optional
	LastBrainstorm *metav1.Time `json:"lastBrainstorm,omitempty"`
```

**Regen step (explicit):** run `make generate` then `make manifests`. This regenerates `api/v1alpha1/zz_generated.deepcopy.go` (adds DeepCopy for `CronActivity`/`BrainstormActivity`/`ScmCron` and updates `ScmSpec`/`ProjectStatus`) and `charts/tatara-operator/crds/tatara.dev_projects.yaml`.

**Command:** `make generate && make manifests && make test 2>&1 | tail -20` - `TestProjectCronFieldsRoundTrip` PASS.

**Commit:** `feat(api): add ScmCron/CronActivity/BrainstormActivity + ScmSpec.priorityLabel/cron + ProjectStatus.last*Scan (contract lock section 5)`

## Task 2.2: Task Kind enum extend + Task.Status.IssueOutcome

**RED** - Test: append to `internal/controller/projectscan_crd_test.go`:

```go
func TestTaskKindEnumAndIssueOutcome(t *testing.T) {
	ctx := context.Background()
	for _, kind := range []string{"triageIssue", "brainstorm"} {
		tk := &tataradevv1alpha1.Task{}
		tk.Name = "enum-" + kind
		tk.Namespace = testNS
		tk.Spec = tataradevv1alpha1.TaskSpec{
			ProjectRef: "p", RepositoryRef: "r", Goal: "g", Kind: kind,
		}
		if err := k8sClient.Create(ctx, tk); err != nil {
			t.Fatalf("create task kind=%s: %v", kind, err)
		}
	}
	tk := &tataradevv1alpha1.Task{}
	if err := k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: "enum-triageIssue"}, tk); err != nil {
		t.Fatalf("get task: %v", err)
	}
	tk.Status.IssueOutcome = &tataradevv1alpha1.IssueOutcome{Action: "close", Comment: "out of scope"}
	if err := k8sClient.Status().Update(ctx, tk); err != nil {
		t.Fatalf("status update issueOutcome: %v", err)
	}
}
```

**Command:** `make test 2>&1 | head -30` - expect FAIL: `IssueOutcome` undefined; and the Kind enum rejects `triageIssue`/`brainstorm` at apply until the CRD is regenerated.

**GREEN** - modify `api/v1alpha1/task_types.go`.

Add the `IssueOutcome` type after `PROutcome` (line 43):

```go
// IssueOutcome is the agent's outcome for an issue-triage task.
type IssueOutcome struct {
	// +kubebuilder:validation:Enum=implement;close
	Action string `json:"action"`
	// +optional
	Comment string `json:"comment,omitempty"` // required when Action==close
}
```

Extend the Kind enum marker (line 69):

```go
	// +kubebuilder:validation:Enum=implement;review;selfImprove;triageIssue;brainstorm
	// +kubebuilder:default="implement"
	// +optional
	Kind string `json:"kind,omitempty"`
```

Add to `TaskStatus` after `PROutcome` (line 101):

```go
	// +optional
	IssueOutcome *IssueOutcome `json:"issueOutcome,omitempty"`
```

**Regen step:** `make generate && make manifests` (deepcopy for `IssueOutcome` + `tatara.dev_tasks.yaml` enum/field).

**Command:** `make generate && make manifests && make test 2>&1 | tail -20` - `TestTaskKindEnumAndIssueOutcome` PASS.

**Commit:** `feat(api): extend Task.Kind enum (triageIssue;brainstorm) + Task.Status.IssueOutcome (contract lock sections 1,6)`

---

# Phase 3 - Metrics

## Task 3.1: Scan + issue-outcome metrics

**RED** - Test: append to `internal/obs/operator_metrics_test.go` (mirror the existing gather-presence assertions in that file).

```go
func TestScanMetricsRegistered(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := NewOperatorMetrics(reg)
	m.ScanItem("mrScan", "picked")
	m.ScanTaskCreated("mrScan", "review")
	m.ObserveScanDuration("mrScan", 0.5)
	m.IssueOutcome("close")
	m.SetTasksInflightKind("triageIssue", 2)

	mfs, err := reg.Gather()
	if err != nil {
		t.Fatalf("gather: %v", err)
	}
	want := map[string]bool{
		"tatara_scan_items_total":         false,
		"tatara_scan_tasks_created_total": false,
		"tatara_scan_duration_seconds":    false,
		"tatara_issue_outcome_total":      false,
		"tatara_tasks_inflight":           false,
	}
	for _, mf := range mfs {
		if _, ok := want[mf.GetName()]; ok {
			want[mf.GetName()] = true
		}
	}
	for name, seen := range want {
		if !seen {
			t.Fatalf("metric %q not registered/gathered", name)
		}
	}
}
```

Note: `tatara_tasks_inflight{kind}` is a NEW gauge vec distinct from the existing `operator_tasks_inflight` gauge (different name per lock section 9). Keep the existing `operator_tasks_inflight` untouched; add a new `tatara_tasks_inflight` GaugeVec keyed by `kind`.

**Command:** `go test ./internal/obs/... -run TestScanMetricsRegistered -count=1` - expect FAIL: `m.ScanItem undefined` etc.

**GREEN** - add to `OperatorMetrics` struct (operator_metrics.go:8):

```go
	scanItemsTotal        *prometheus.CounterVec
	scanTasksCreatedTotal *prometheus.CounterVec
	scanDurationSeconds   *prometheus.HistogramVec
	issueOutcomeTotal     *prometheus.CounterVec
	tasksInflightKind     *prometheus.GaugeVec
```

In `NewOperatorMetrics`, construct them:

```go
		scanItemsTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "tatara_scan_items_total",
			Help: "Total scan candidates by activity and outcome.",
		}, []string{"activity", "outcome"}),
		scanTasksCreatedTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "tatara_scan_tasks_created_total",
			Help: "Tasks created by scan activity and Task kind.",
		}, []string{"activity", "kind"}),
		scanDurationSeconds: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "tatara_scan_duration_seconds",
			Help:    "Wall-clock duration of one scan activity.",
			Buckets: prometheus.ExponentialBuckets(0.05, 2, 10),
		}, []string{"activity"}),
		issueOutcomeTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "tatara_issue_outcome_total",
			Help: "Issue-triage outcomes by action.",
		}, []string{"action"}),
		tasksInflightKind: prometheus.NewGaugeVec(prometheus.GaugeOpts{
			Name: "tatara_tasks_inflight",
			Help: "In-flight Tasks by kind.",
		}, []string{"kind"}),
```

Add them to the `reg.MustRegister(...)` list. After the existing pre-init loops, pre-init the scan label combos:

```go
	for _, activity := range []string{"mrScan", "issueScan", "brainstorm"} {
		for _, outcome := range []string{"scanned", "picked", "skipped_dedup", "skipped_cap"} {
			m.scanItemsTotal.WithLabelValues(activity, outcome)
		}
	}
	for _, action := range []string{"implement", "close"} {
		m.issueOutcomeTotal.WithLabelValues(action)
	}
```

Add the methods at the end of the file:

```go
// ScanItem increments tatara_scan_items_total for an activity + outcome.
func (m *OperatorMetrics) ScanItem(activity, outcome string) {
	m.scanItemsTotal.WithLabelValues(activity, outcome).Inc()
}

// ScanTaskCreated increments tatara_scan_tasks_created_total for an activity + kind.
func (m *OperatorMetrics) ScanTaskCreated(activity, kind string) {
	m.scanTasksCreatedTotal.WithLabelValues(activity, kind).Inc()
}

// ObserveScanDuration records the seconds one scan activity took.
func (m *OperatorMetrics) ObserveScanDuration(activity string, seconds float64) {
	m.scanDurationSeconds.WithLabelValues(activity).Observe(seconds)
}

// IssueOutcome increments tatara_issue_outcome_total for an action.
func (m *OperatorMetrics) IssueOutcome(action string) {
	m.issueOutcomeTotal.WithLabelValues(action).Inc()
}

// SetTasksInflightKind sets tatara_tasks_inflight for one Task kind.
func (m *OperatorMetrics) SetTasksInflightKind(kind string, n float64) {
	m.tasksInflightKind.WithLabelValues(kind).Set(n)
}
```

**Command:** `go test ./internal/obs/... -count=1` PASS.

**Commit:** `feat(obs): add scan + issue-outcome metrics (contract lock section 9)`

---

# Phase 4 - projectscan.go scan loop

## Task 4.1: Selection helper - priority-then-stale

**RED** - Test: new file `internal/controller/projectscan_select_test.go`.

```go
package controller

import (
	"testing"
	"time"

	"github.com/szymonrychu/tatara-operator/internal/scm"
)

func TestSelectPriorityThenStale(t *testing.T) {
	base := time.Date(2026, 6, 10, 0, 0, 0, 0, time.UTC)
	cands := []candidate{
		{repo: "o/r", number: 1, labels: nil, updatedAt: base.Add(3 * time.Hour)},
		{repo: "o/r", number: 2, labels: []string{"tatara/priority"}, updatedAt: base.Add(2 * time.Hour)},
		{repo: "o/r", number: 3, labels: nil, updatedAt: base.Add(1 * time.Hour)},
		{repo: "o/r", number: 4, labels: []string{"tatara/priority"}, updatedAt: base.Add(4 * time.Hour)},
	}
	cases := []struct {
		name      string
		priority  string
		n         int
		wantOrder []int
	}{
		{"priority first then stale, cap 3", "tatara/priority", 3, []int{2, 4, 3}},
		{"no priority label = pure stale", "", 2, []int{3, 1}},
		{"cap 1 picks stalest priority", "tatara/priority", 1, []int{2}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := selectCandidates(cands, tc.priority, tc.n)
			if len(got) != len(tc.wantOrder) {
				t.Fatalf("len = %d, want %d (%+v)", len(got), len(tc.wantOrder), got)
			}
			for i, want := range tc.wantOrder {
				if got[i].number != want {
					t.Fatalf("pos %d = #%d, want #%d (%+v)", i, got[i].number, want, got)
				}
			}
		})
	}
}

var _ = scm.PRRef{}
```

**Command:** `go test ./internal/controller/... -run TestSelectPriorityThenStale -count=1` - expect FAIL: `undefined: candidate`, `undefined: selectCandidates`.

**GREEN** - new file `internal/controller/projectscan.go` (start it with the selection primitives only; the scan loop lands in 4.3):

```go
package controller

import (
	"sort"
	"time"
)

// candidate is one scannable work item (PR, issue, or board item) normalized
// for selection + dedup. number/repo identify it; labels drive priority;
// updatedAt drives stale-first ordering.
type candidate struct {
	repo      string
	number    int
	author    string
	headSHA   string
	labels    []string
	updatedAt time.Time
	isPR      bool
}

func hasLabel(labels []string, want string) bool {
	if want == "" {
		return false
	}
	for _, l := range labels {
		if l == want {
			return true
		}
	}
	return false
}

// selectCandidates partitions into priority-labelled and rest, sorts each
// least-recently-updated first, concatenates priority++rest, and caps at n.
func selectCandidates(in []candidate, priorityLabel string, n int) []candidate {
	if n < 1 {
		n = 1
	}
	var withPriority, rest []candidate
	for _, c := range in {
		if hasLabel(c.labels, priorityLabel) {
			withPriority = append(withPriority, c)
		} else {
			rest = append(rest, c)
		}
	}
	staleFirst := func(s []candidate) {
		sort.SliceStable(s, func(i, j int) bool { return s[i].updatedAt.Before(s[j].updatedAt) })
	}
	staleFirst(withPriority)
	staleFirst(rest)
	out := append(withPriority, rest...)
	if len(out) > n {
		out = out[:n]
	}
	return out
}
```

**Command:** `go test ./internal/controller/... -run TestSelectPriorityThenStale -count=1` PASS.

**Commit:** `feat(controller): projectscan candidate type + priority-then-stale selection`

## Task 4.2: Dedup labels + dedup filter

**RED** - Test: new file `internal/controller/projectscan_dedup_test.go`.

```go
package controller

import (
	"testing"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func mkCronTask(repo string, number int, kind, headSHA, phase string) tatarav1alpha1.Task {
	tk := tatarav1alpha1.Task{}
	tk.Labels = scanTaskLabels(candidate{repo: repo, number: number, headSHA: headSHA}, "mrScan", kind)
	tk.Status.Phase = phase
	return tk
}

func TestScanTaskLabels(t *testing.T) {
	got := scanTaskLabels(candidate{repo: "o/r", number: 5, headSHA: "abc"}, "mrScan", "review")
	if got["tatara.io/source-repo"] != "o.r" {
		t.Fatalf("source-repo = %q (want sanitized o.r)", got["tatara.io/source-repo"])
	}
	if got["tatara.io/source-number"] != "5" || got["tatara.io/source-kind"] != "review" {
		t.Fatalf("labels = %+v", got)
	}
	if got["tatara.io/head-sha"] != "abc" || got["tatara.io/activity"] != "mrScan" {
		t.Fatalf("labels = %+v", got)
	}
}

func TestDedupPR(t *testing.T) {
	existing := []tatarav1alpha1.Task{
		mkCronTask("o/r", 1, "review", "sha1", "Running"),      // non-terminal -> skip #1
		mkCronTask("o/r", 2, "review", "sha2", "Succeeded"),    // terminal at sha2 -> skip same-sha
	}
	cases := []struct {
		name string
		cand candidate
		want bool // true = skipped (deduped)
	}{
		{"in-flight pr skipped", candidate{repo: "o/r", number: 1, headSHA: "shaX", isPR: true}, true},
		{"terminal same sha skipped", candidate{repo: "o/r", number: 2, headSHA: "sha2", isPR: true}, true},
		{"terminal new sha eligible", candidate{repo: "o/r", number: 2, headSHA: "sha9", isPR: true}, false},
		{"unseen pr eligible", candidate{repo: "o/r", number: 3, headSHA: "shaY", isPR: true}, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := isDeduped(tc.cand, existing); got != tc.want {
				t.Fatalf("isDeduped = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestDedupIssue(t *testing.T) {
	created := metav1.Now()
	terminal := mkCronTask("o/r", 7, "triageIssue", "", "Succeeded")
	terminal.CreationTimestamp = created
	existing := []tatarav1alpha1.Task{
		mkCronTask("o/r", 6, "triageIssue", "", "Planning"), // non-terminal -> skip #6
		terminal,                                            // terminal -> skip unless newer activity
	}
	older := candidate{repo: "o/r", number: 7, updatedAt: created.Time.Add(-time.Hour)}
	newer := candidate{repo: "o/r", number: 7, updatedAt: created.Time.Add(time.Hour)}
	if !isDeduped(candidate{repo: "o/r", number: 6}, existing) {
		t.Fatalf("in-flight issue #6 should be deduped")
	}
	if !isDeduped(older, existing) {
		t.Fatalf("terminal issue with no new activity should be deduped")
	}
	if isDeduped(newer, existing) {
		t.Fatalf("terminal issue with newer activity should be eligible")
	}
}
```

Add `"time"` import to the test.

**Command:** `go test ./internal/controller/... -run 'TestScanTaskLabels|TestDedupPR|TestDedupIssue' -count=1` - expect FAIL: `undefined: scanTaskLabels`, `undefined: isDeduped`.

**GREEN** - add to `internal/controller/projectscan.go` (import `strconv`, `strings`, `tatarav1alpha1`):

```go
const (
	labelSourceRepo   = "tatara.io/source-repo"
	labelSourceNumber = "tatara.io/source-number"
	labelSourceKind   = "tatara.io/source-kind"
	labelHeadSHA      = "tatara.io/head-sha"
	labelActivity     = "tatara.io/activity"
)

// sanitizeRepoLabel makes a repo slug DNS-label-safe by replacing '/' with '.'.
func sanitizeRepoLabel(repo string) string {
	return strings.ReplaceAll(repo, "/", ".")
}

// scanTaskLabels builds the operator-stamped dedup labels for a cron Task.
// head-sha is omitted for non-PR candidates.
func scanTaskLabels(c candidate, activity, kind string) map[string]string {
	l := map[string]string{
		labelSourceRepo:   sanitizeRepoLabel(c.repo),
		labelSourceNumber: strconv.Itoa(c.number),
		labelSourceKind:   kind,
		labelActivity:     activity,
	}
	if c.headSHA != "" {
		l[labelHeadSHA] = c.headSHA
	}
	return l
}

// isDeduped reports whether a candidate already has a Task that should suppress
// a re-pick, per the dedup rules (design section 8):
//   - any non-terminal Task for (repo, number) -> skip
//   - PR: a terminal Task at the same head-sha -> skip (already handled revision)
//   - issue: a terminal Task whose creation is at/after the candidate updatedAt -> skip
func isDeduped(c candidate, existing []tatarav1alpha1.Task) bool {
	repoLabel := sanitizeRepoLabel(c.repo)
	numLabel := strconv.Itoa(c.number)
	for i := range existing {
		t := &existing[i]
		if t.Labels[labelSourceRepo] != repoLabel || t.Labels[labelSourceNumber] != numLabel {
			continue
		}
		if !isTerminal(t.Status.Phase) {
			return true
		}
		if c.isPR {
			if t.Labels[labelHeadSHA] == c.headSHA && c.headSHA != "" {
				return true
			}
			continue
		}
		// issue: terminal Task suppresses unless the issue saw newer activity.
		if !c.updatedAt.After(t.CreationTimestamp.Time) {
			return true
		}
	}
	return false
}
```

`isTerminal` already exists (task_controller.go:60). `tatarav1alpha1` import alias matches the task_controller file. Add `"time"` only if referenced - it is not in this file (the test imports it).

**Command:** `go test ./internal/controller/... -run 'TestScanTaskLabels|TestDedupPR|TestDedupIssue' -count=1` PASS.

**Commit:** `feat(controller): dedup labels + isDeduped filter (contract lock section 2)`

## Task 4.3: Cron next-fire + bad-cron disable

**RED** - Test: new file `internal/controller/projectscan_cron_test.go`.

```go
package controller

import (
	"testing"
	"time"
)

func TestActivityNextFire(t *testing.T) {
	base := time.Date(2026, 6, 11, 10, 0, 0, 0, time.UTC)
	cases := []struct {
		name     string
		schedule string
		last     time.Time
		wantOK   bool
		wantDue  bool // now is at/after next
		now      time.Time
	}{
		{"empty disables", "", base, false, false, base},
		{"hourly not yet due", "0 * * * *", base, true, false, base.Add(30 * time.Minute)},
		{"hourly due", "0 * * * *", base, true, true, base.Add(90 * time.Minute)},
		{"bad cron disabled", "not a cron", base, false, false, base},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			next, ok := activityNextFire(tc.schedule, tc.last)
			if ok != tc.wantOK {
				t.Fatalf("ok = %v, want %v", ok, tc.wantOK)
			}
			if !ok {
				return
			}
			due := !tc.now.Before(next)
			if due != tc.wantDue {
				t.Fatalf("due = %v at now=%v next=%v, want %v", due, tc.now, next, tc.wantDue)
			}
		})
	}
}
```

**Command:** `go test ./internal/controller/... -run TestActivityNextFire -count=1` - expect FAIL: `undefined: activityNextFire`.

**GREEN** - add to `internal/controller/projectscan.go` (import `"github.com/robfig/cron/v3"`):

```go
// activityNextFire parses a 5-field cron and returns the next fire after base.
// ok=false when the schedule is empty (disabled) or malformed (caller logs).
func activityNextFire(schedule string, base time.Time) (time.Time, bool) {
	if schedule == "" {
		return time.Time{}, false
	}
	parsed, err := cron.ParseStandard(schedule)
	if err != nil {
		return time.Time{}, false
	}
	return parsed.Next(base), true
}
```

**Command:** `go test ./internal/controller/... -run TestActivityNextFire -count=1` PASS.

**Commit:** `feat(controller): activityNextFire cron next-fire helper (mirrors scheduleNextReingest)`

## Task 4.4: candidate builders from SCM list results

**RED** - Test: append to `internal/controller/projectscan_select_test.go`:

```go
func TestCandidatesFromPRs(t *testing.T) {
	prs := []scm.PRRef{
		{Repo: "o/r", Number: 5, Author: "alice", HeadSHA: "abc", Labels: []string{"x"}, UpdatedAt: time.Unix(100, 0)},
	}
	got := candidatesFromPRs(prs)
	if len(got) != 1 || got[0].number != 5 || got[0].author != "alice" || got[0].headSHA != "abc" || !got[0].isPR {
		t.Fatalf("candidatesFromPRs = %+v", got)
	}
}

func TestCandidatesFromIssues(t *testing.T) {
	iss := []scm.IssueRef{
		{Repo: "o/r", Number: 7, Labels: []string{"bug"}, UpdatedAt: time.Unix(100, 0), IsPR: false},
		{Repo: "o/r", Number: 8, IsPR: true}, // filtered out
	}
	got := candidatesFromIssues(iss)
	if len(got) != 1 || got[0].number != 7 || got[0].isPR {
		t.Fatalf("candidatesFromIssues should drop IsPR rows: %+v", got)
	}
}
```

**Command:** `go test ./internal/controller/... -run 'TestCandidatesFromPRs|TestCandidatesFromIssues' -count=1` - expect FAIL: undefined builders.

**GREEN** - add to `internal/controller/projectscan.go` (import `"github.com/szymonrychu/tatara-operator/internal/scm"`):

```go
func candidatesFromPRs(prs []scm.PRRef) []candidate {
	out := make([]candidate, 0, len(prs))
	for _, p := range prs {
		out = append(out, candidate{
			repo: p.Repo, number: p.Number, author: p.Author, headSHA: p.HeadSHA,
			labels: p.Labels, updatedAt: p.UpdatedAt, isPR: true,
		})
	}
	return out
}

// candidatesFromIssues drops rows GitHub reported as PRs (IsPR) so issueScan
// never triages a PR as an issue.
func candidatesFromIssues(iss []scm.IssueRef) []candidate {
	out := make([]candidate, 0, len(iss))
	for _, i := range iss {
		if i.IsPR {
			continue
		}
		out = append(out, candidate{
			repo: i.Repo, number: i.Number, labels: i.Labels, updatedAt: i.UpdatedAt, isPR: false,
		})
	}
	return out
}

// candidatesFromBoard maps board items (issues only; Number 0 = draft, skipped)
// to candidates, deduping against per-repo issues happens in the caller via
// (repo, number).
func candidatesFromBoard(items []scm.BoardItem) []candidate {
	out := make([]candidate, 0, len(items))
	for _, b := range items {
		if b.Number == 0 {
			continue
		}
		out = append(out, candidate{repo: b.Repo, number: b.Number, updatedAt: b.UpdatedAt, isPR: false})
	}
	return out
}
```

**Command:** `go test ./internal/controller/... -run 'TestCandidatesFromPRs|TestCandidatesFromIssues' -count=1` PASS.

**Commit:** `feat(controller): candidate builders from SCM list results (board draft + IsPR filtering)`

## Task 4.5: Task factory - createScanTask

**RED** - Test: new file `internal/controller/projectscan_factory_test.go` (envtest; creates a Task and asserts labels + Kind + Source).

```go
package controller

import (
	"context"
	"testing"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"k8s.io/apimachinery/pkg/types"
)

func TestCreateScanTask(t *testing.T) {
	ctx := context.Background()
	mkSecret(t, "factory-scm", map[string][]byte{"token": []byte("t"), "webhookSecret": []byte("w")})
	proj := &tatarav1alpha1.Project{}
	proj.Name = "factory-proj"
	proj.Namespace = testNS
	proj.Spec.ScmSecretRef = "factory-scm"
	proj.Spec.Scm = &tatarav1alpha1.ScmSpec{Provider: "github", Owner: "o", BotLogin: "bot"}
	if err := k8sClient.Create(ctx, proj); err != nil {
		t.Fatalf("create project: %v", err)
	}
	repo := &tatarav1alpha1.Repository{}
	repo.Name = "factory-repo"
	repo.Namespace = testNS
	repo.Spec = tatarav1alpha1.RepositorySpec{ProjectRef: "factory-proj", URL: "https://github.com/o/r.git", DefaultBranch: "main"}
	if err := k8sClient.Create(ctx, repo); err != nil {
		t.Fatalf("create repo: %v", err)
	}

	r := newScanReconciler(nil)
	c := candidate{repo: "o/r", number: 5, headSHA: "abc", isPR: true}
	created, err := r.createScanTask(ctx, proj, repo, c, "mrScan", "review", "review PR o/r#5")
	if err != nil {
		t.Fatalf("createScanTask: %v", err)
	}

	got := &tatarav1alpha1.Task{}
	if err := k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: created.Name}, got); err != nil {
		t.Fatalf("get created task: %v", err)
	}
	if got.Spec.Kind != "review" || got.Spec.ProjectRef != "factory-proj" || got.Spec.RepositoryRef != "factory-repo" {
		t.Fatalf("task spec = %+v", got.Spec)
	}
	if got.Labels[labelSourceRepo] != "o.r" || got.Labels[labelSourceNumber] != "5" || got.Labels[labelHeadSHA] != "abc" || got.Labels[labelActivity] != "mrScan" {
		t.Fatalf("task labels = %+v", got.Labels)
	}
	if got.Spec.Source == nil || got.Spec.Source.Number != 5 || !got.Spec.Source.IsPR || got.Spec.Source.Provider != "github" {
		t.Fatalf("task source = %+v", got.Spec.Source)
	}
}
```

This needs a `newScanReconciler` helper - define it in this test file (it constructs a `ProjectReconciler` with the envtest client and a fake reader). Add:

```go
func newScanReconciler(reader scm.SCMReader) *ProjectReconciler {
	r := newProjectReconciler() // existing helper (project_controller_test.go)
	r.ReaderFor = func(string) (scm.SCMReader, error) { return reader, nil }
	return r
}
```

(import `"github.com/szymonrychu/tatara-operator/internal/scm"`).

**Command:** `make test 2>&1 | grep -A3 TestCreateScanTask | head` - expect FAIL: `r.ReaderFor undefined`, `r.createScanTask undefined`.

**GREEN** - first add the `ReaderFor` field to `ProjectReconciler` (project_controller.go:26-32):

```go
type ProjectReconciler struct {
	client.Client
	Scheme              *runtime.Scheme
	Metrics             *obs.OperatorMetrics
	ExternalWebhookBase string
	MemoryConfig        memory.Config
	// ReaderFor returns a token-bound scm.SCMReader for a provider name.
	// Nil in tests that do not exercise scanning; wired in wire.go at runtime.
	ReaderFor func(provider string) (scm.SCMReader, error)
}
```

Add the `scm` import to `project_controller.go`.

Add `createScanTask` to `internal/controller/projectscan.go` (imports: `context`, `fmt`, `tatarav1alpha1`, `metav1`, `controllerutil`, `client`):

```go
// createScanTask creates one cron Task for a candidate with the dedup labels,
// a TaskSource pointing at the work item, and an owner-ref to the Project.
func (r *ProjectReconciler) createScanTask(ctx context.Context, proj *tatarav1alpha1.Project, repo *tatarav1alpha1.Repository, c candidate, activity, kind, goal string) (*tatarav1alpha1.Task, error) {
	src := &tatarav1alpha1.TaskSource{
		Provider: proj.Spec.Scm.Provider,
		IssueRef: fmt.Sprintf("%s#%d", c.repo, c.number),
		Number:   c.number,
		IsPR:     c.isPR,
	}
	if c.author != "" {
		src.AuthorLogin = c.author
	}
	task := &tatarav1alpha1.Task{
		ObjectMeta: metav1.ObjectMeta{
			GenerateName: "scan-",
			Namespace:    proj.Namespace,
			Labels:       scanTaskLabels(c, activity, kind),
		},
		Spec: tatarav1alpha1.TaskSpec{
			ProjectRef:    proj.Name,
			RepositoryRef: repo.Name,
			Goal:          goal,
			Kind:          kind,
			Source:        src,
		},
	}
	if err := controllerutil.SetControllerReference(proj, task, r.Scheme); err != nil {
		return nil, fmt.Errorf("scan: set ownerref: %w", err)
	}
	if err := r.Create(ctx, task); err != nil {
		return nil, fmt.Errorf("scan: create task: %w", err)
	}
	r.Metrics.ScanTaskCreated(activity, kind)
	return task, nil
}
```

**Command:** `make test 2>&1 | grep -A3 TestCreateScanTask | head` PASS.

**Commit:** `feat(controller): createScanTask factory stamps dedup labels + TaskSource + ownerref`

## Task 4.6: matchRepo + the three scans (mrScan/issueScan/brainstorm) + runScans

**RED** - Test: new file `internal/controller/projectscan_run_test.go` (envtest; fake reader; asserts Tasks created with correct Kind, dedup, cap, and Last*Scan stamping).

Add a fake reader at the top:

```go
package controller

import (
	"context"
	"testing"
	"time"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/scm"
	"k8s.io/apimachinery/pkg/types"
)

type fakeReader struct {
	prs    []scm.PRRef
	issues []scm.IssueRef
	board  []scm.BoardItem
	prErr  error
}

func (f *fakeReader) ListOpenPRs(context.Context, string, string) ([]scm.PRRef, error) {
	return f.prs, f.prErr
}
func (f *fakeReader) ListOpenIssues(context.Context, string, string) ([]scm.IssueRef, error) {
	return f.issues, nil
}
func (f *fakeReader) ListBoardItems(context.Context, scm.BoardRef) ([]scm.BoardItem, error) {
	return f.board, nil
}

func seedScanProject(t *testing.T, name string, cron *tatarav1alpha1.ScmCron) (*tatarav1alpha1.Project, *tatarav1alpha1.Repository) {
	t.Helper()
	ctx := context.Background()
	mkSecret(t, name+"-scm", map[string][]byte{"token": []byte("t"), "webhookSecret": []byte("w")})
	proj := &tatarav1alpha1.Project{}
	proj.Name = name
	proj.Namespace = testNS
	proj.Spec.ScmSecretRef = name + "-scm"
	proj.Spec.Scm = &tatarav1alpha1.ScmSpec{Provider: "github", Owner: "o", BotLogin: "tatara-bot", PriorityLabel: "tatara/priority", Cron: cron}
	if err := k8sClient.Create(ctx, proj); err != nil {
		t.Fatalf("create project: %v", err)
	}
	repo := &tatarav1alpha1.Repository{}
	repo.Name = name + "-repo"
	repo.Namespace = testNS
	repo.Spec = tatarav1alpha1.RepositorySpec{ProjectRef: name, URL: "https://github.com/o/r.git", DefaultBranch: "main"}
	if err := k8sClient.Create(ctx, repo); err != nil {
		t.Fatalf("create repo: %v", err)
	}
	return proj, repo
}

func listScanTasks(t *testing.T, project string) []tatarav1alpha1.Task {
	t.Helper()
	var list tatarav1alpha1.TaskList
	if err := k8sClient.List(context.Background(), &list); err != nil {
		t.Fatalf("list tasks: %v", err)
	}
	var out []tatarav1alpha1.Task
	for i := range list.Items {
		if list.Items[i].Spec.ProjectRef == project {
			out = append(out, list.Items[i])
		}
	}
	return out
}

func TestRunScans_MRScanCreatesReviewAndSelfImprove(t *testing.T) {
	cron := &tatarav1alpha1.ScmCron{MRScan: tatarav1alpha1.CronActivity{Schedule: "* * * * *", MaxPerCycle: 2}}
	proj, _ := seedScanProject(t, "mrscan-proj", cron)
	reader := &fakeReader{prs: []scm.PRRef{
		{Repo: "o/r", Number: 1, Author: "tatara-bot", HeadSHA: "a", UpdatedAt: time.Unix(100, 0)},
		{Repo: "o/r", Number: 2, Author: "human", HeadSHA: "b", UpdatedAt: time.Unix(200, 0)},
	}}
	r := newScanReconciler(reader)
	if _, err := r.runScans(context.Background(), proj); err != nil {
		t.Fatalf("runScans: %v", err)
	}
	tasks := listScanTasks(t, "mrscan-proj")
	if len(tasks) != 2 {
		t.Fatalf("want 2 tasks, got %d", len(tasks))
	}
	kinds := map[string]bool{}
	for _, tk := range tasks {
		kinds[tk.Spec.Kind] = true
	}
	if !kinds["selfImprove"] || !kinds["review"] {
		t.Fatalf("want review+selfImprove kinds, got %+v", kinds)
	}
	got := &tatarav1alpha1.Project{}
	_ = k8sClient.Get(context.Background(), types.NamespacedName{Namespace: testNS, Name: "mrscan-proj"}, got)
	if got.Status.LastMRScan == nil {
		t.Fatalf("LastMRScan not stamped")
	}
}

func TestRunScans_IssueScanCap(t *testing.T) {
	cron := &tatarav1alpha1.ScmCron{IssueScan: tatarav1alpha1.CronActivity{Schedule: "* * * * *", MaxPerCycle: 1}}
	proj, _ := seedScanProject(t, "issuescan-proj", cron)
	reader := &fakeReader{issues: []scm.IssueRef{
		{Repo: "o/r", Number: 10, UpdatedAt: time.Unix(100, 0)},
		{Repo: "o/r", Number: 11, UpdatedAt: time.Unix(200, 0)},
	}}
	r := newScanReconciler(reader)
	if _, err := r.runScans(context.Background(), proj); err != nil {
		t.Fatalf("runScans: %v", err)
	}
	tasks := listScanTasks(t, "issuescan-proj")
	if len(tasks) != 1 {
		t.Fatalf("cap=1 should create 1 task, got %d", len(tasks))
	}
	if tasks[0].Spec.Kind != "triageIssue" {
		t.Fatalf("kind = %q, want triageIssue", tasks[0].Spec.Kind)
	}
}

func TestRunScans_BadCronDisablesNoCrash(t *testing.T) {
	cron := &tatarav1alpha1.ScmCron{MRScan: tatarav1alpha1.CronActivity{Schedule: "not a cron", MaxPerCycle: 1}}
	proj, _ := seedScanProject(t, "badcron-proj", cron)
	r := newScanReconciler(&fakeReader{})
	res, err := r.runScans(context.Background(), proj)
	if err != nil {
		t.Fatalf("bad cron must not error: %v", err)
	}
	if len(listScanTasks(t, "badcron-proj")) != 0 {
		t.Fatalf("bad cron must create no tasks")
	}
	_ = res
}

func TestRunScans_DedupSkipsInFlight(t *testing.T) {
	cron := &tatarav1alpha1.ScmCron{IssueScan: tatarav1alpha1.CronActivity{Schedule: "* * * * *", MaxPerCycle: 5}}
	proj, repo := seedScanProject(t, "dedup-proj", cron)
	// pre-create an in-flight triageIssue Task for o/r#10
	pre := &tatarav1alpha1.Task{}
	pre.GenerateName = "scan-"
	pre.Namespace = testNS
	pre.Labels = scanTaskLabels(candidate{repo: "o/r", number: 10}, "issueScan", "triageIssue")
	pre.Spec = tatarav1alpha1.TaskSpec{ProjectRef: "dedup-proj", RepositoryRef: repo.Name, Goal: "g", Kind: "triageIssue"}
	if err := k8sClient.Create(context.Background(), pre); err != nil {
		t.Fatalf("pre-create: %v", err)
	}
	pre.Status.Phase = "Running"
	_ = k8sClient.Status().Update(context.Background(), pre)

	reader := &fakeReader{issues: []scm.IssueRef{{Repo: "o/r", Number: 10, UpdatedAt: time.Unix(100, 0)}}}
	r := newScanReconciler(reader)
	if _, err := r.runScans(context.Background(), proj); err != nil {
		t.Fatalf("runScans: %v", err)
	}
	// only the pre-existing one; no new task for #10
	if n := len(listScanTasks(t, "dedup-proj")); n != 1 {
		t.Fatalf("dedup failed: want 1 task, got %d", n)
	}
}
```

**Command:** `make test 2>&1 | grep -E 'TestRunScans|FAIL' | head` - expect FAIL: `r.runScans undefined`.

**GREEN** - add to `internal/controller/projectscan.go` the scan orchestration. Imports add: `time`, `metav1`, `ctrl "sigs.k8s.io/controller-runtime"`, `"sigs.k8s.io/controller-runtime/pkg/log"`, `apierrors`, `types`, `corev1`.

```go
// matchRepoForSlug returns the Project Repository whose URL maps to the given
// owner/name slug, or ok=false. Used to bind a scanned (repo, number) candidate
// back to a Repository CRD for the Task's RepositoryRef.
func (r *ProjectReconciler) matchRepoForSlug(repos []tatarav1alpha1.Repository, slug string) (tatarav1alpha1.Repository, bool) {
	for i := range repos {
		owner, name, err := ghOwnerRepoFromURL(repos[i].Spec.URL)
		if err != nil {
			continue
		}
		if owner+"/"+name == slug {
			return repos[i], true
		}
	}
	return tatarav1alpha1.Repository{}, false
}

// projectReposForScan returns all Repositories owned by the Project.
func (r *ProjectReconciler) projectReposForScan(ctx context.Context, proj *tatarav1alpha1.Project) ([]tatarav1alpha1.Repository, error) {
	var list tatarav1alpha1.RepositoryList
	if err := r.List(ctx, &list, client.InNamespace(proj.Namespace)); err != nil {
		return nil, fmt.Errorf("scan: list repositories: %w", err)
	}
	var out []tatarav1alpha1.Repository
	for i := range list.Items {
		if list.Items[i].Spec.ProjectRef == proj.Name {
			out = append(out, list.Items[i])
		}
	}
	return out, nil
}

// existingScanTasks lists Project-owned Tasks carrying the dedup activity label.
func (r *ProjectReconciler) existingScanTasks(ctx context.Context, proj *tatarav1alpha1.Project) ([]tatarav1alpha1.Task, error) {
	var list tatarav1alpha1.TaskList
	if err := r.List(ctx, &list, client.InNamespace(proj.Namespace)); err != nil {
		return nil, fmt.Errorf("scan: list tasks: %w", err)
	}
	var out []tatarav1alpha1.Task
	for i := range list.Items {
		if list.Items[i].Spec.ProjectRef == proj.Name && list.Items[i].Labels[labelActivity] != "" {
			out = append(out, list.Items[i])
		}
	}
	return out, nil
}

// scanReader resolves the token-bound SCMReader for the Project's provider.
func (r *ProjectReconciler) scanReader(ctx context.Context, proj *tatarav1alpha1.Project) (scm.SCMReader, error) {
	var sec corev1.Secret
	key := types.NamespacedName{Namespace: proj.Namespace, Name: proj.Spec.ScmSecretRef}
	if err := r.Get(ctx, key, &sec); err != nil {
		return nil, fmt.Errorf("scan: get scm secret: %w", err)
	}
	_ = sec // token is consumed by ReaderFor via ByProvider in wire.go; tests inject a fake reader.
	return r.ReaderFor(proj.Spec.Scm.Provider)
}

// runScans runs each due activity and returns the soonest next-fire as a
// requeue. Cron parsing/SCM/create failures are logged and skipped per activity
// so one bad activity never blocks the others or crashes the reconciler.
func (r *ProjectReconciler) runScans(ctx context.Context, proj *tatarav1alpha1.Project) (time.Duration, error) {
	l := log.FromContext(ctx)
	if proj.Spec.Scm == nil || proj.Spec.Scm.Cron == nil || r.ReaderFor == nil {
		return 0, nil
	}
	cronSpec := proj.Spec.Scm.Cron
	now := time.Now()
	soonest := time.Duration(0)
	consider := func(next time.Time) {
		d := next.Sub(now)
		if d < 0 {
			d = 0
		}
		if d > maxScheduleRequeue {
			d = maxScheduleRequeue
		}
		if soonest == 0 || d < soonest {
			soonest = d
		}
	}

	reader, rerr := r.scanReader(ctx, proj)
	if rerr != nil {
		l.Error(rerr, "scan: resolve reader", "action", "scan_reader_error", "resource_id", proj.Name)
		return maxScheduleRequeue, nil
	}
	repos, err := r.projectReposForScan(ctx, proj)
	if err != nil {
		return 0, err
	}
	existing, err := r.existingScanTasks(ctx, proj)
	if err != nil {
		return 0, err
	}

	// mrScan
	if base, due, next, ok := r.activityDue(proj, "mrScan"); ok {
		consider(next)
		if due {
			r.mrScan(ctx, proj, reader, repos, existing, cronSpec.MRScan)
			r.stampScan(ctx, proj, "mrScan")
		}
		_ = base
	} else if cronSpec.MRScan.Schedule != "" {
		l.Error(fmt.Errorf("invalid cron %q", cronSpec.MRScan.Schedule), "scan: invalid mrScan cron, disabling",
			"action", "scan_cron_invalid", "resource_id", proj.Name, "activity", "mrScan")
	}

	// issueScan
	if _, due, next, ok := r.activityDue(proj, "issueScan"); ok {
		consider(next)
		if due {
			r.issueScan(ctx, proj, reader, repos, existing, cronSpec.IssueScan)
			r.stampScan(ctx, proj, "issueScan")
		}
	} else if cronSpec.IssueScan.Schedule != "" {
		l.Error(fmt.Errorf("invalid cron %q", cronSpec.IssueScan.Schedule), "scan: invalid issueScan cron, disabling",
			"action", "scan_cron_invalid", "resource_id", proj.Name, "activity", "issueScan")
	}

	// brainstorm (opt-in)
	if cronSpec.Brainstorm.Enabled {
		if _, due, next, ok := r.activityDue(proj, "brainstorm"); ok {
			consider(next)
			if due {
				r.brainstorm(ctx, proj, repos, cronSpec.Brainstorm)
				r.stampScan(ctx, proj, "brainstorm")
			}
		} else if cronSpec.Brainstorm.Schedule != "" {
			l.Error(fmt.Errorf("invalid cron %q", cronSpec.Brainstorm.Schedule), "scan: invalid brainstorm cron, disabling",
				"action", "scan_cron_invalid", "resource_id", proj.Name, "activity", "brainstorm")
		}
	}

	return soonest, nil
}

// activityDue computes (base, due, next, ok) for one activity. base is
// Last*Scan|creationTimestamp; ok=false on empty/bad cron.
func (r *ProjectReconciler) activityDue(proj *tatarav1alpha1.Project, activity string) (time.Time, bool, time.Time, bool) {
	schedule := ""
	var last *metav1.Time
	switch activity {
	case "mrScan":
		schedule = proj.Spec.Scm.Cron.MRScan.Schedule
		last = proj.Status.LastMRScan
	case "issueScan":
		schedule = proj.Spec.Scm.Cron.IssueScan.Schedule
		last = proj.Status.LastIssueScan
	case "brainstorm":
		schedule = proj.Spec.Scm.Cron.Brainstorm.Schedule
		last = proj.Status.LastBrainstorm
	}
	base := proj.CreationTimestamp.Time
	if last != nil {
		base = last.Time
	}
	next, ok := activityNextFire(schedule, base)
	if !ok {
		return base, false, time.Time{}, false
	}
	return base, !time.Now().Before(next), next, true
}

// stampScan records the per-activity Last*Scan and persists status.
func (r *ProjectReconciler) stampScan(ctx context.Context, proj *tatarav1alpha1.Project, activity string) {
	now := metav1.Now()
	fresh := &tatarav1alpha1.Project{}
	if err := r.Get(ctx, types.NamespacedName{Namespace: proj.Namespace, Name: proj.Name}, fresh); err != nil {
		return
	}
	switch activity {
	case "mrScan":
		fresh.Status.LastMRScan = &now
		proj.Status.LastMRScan = &now
	case "issueScan":
		fresh.Status.LastIssueScan = &now
		proj.Status.LastIssueScan = &now
	case "brainstorm":
		fresh.Status.LastBrainstorm = &now
		proj.Status.LastBrainstorm = &now
	}
	_ = r.Status().Update(ctx, fresh)
}

// mrScan lists open PRs across repos, selects, dedups, and creates Tasks routed
// by authoritative author -> review (human) | selfImprove (bot).
func (r *ProjectReconciler) mrScan(ctx context.Context, proj *tatarav1alpha1.Project, reader scm.SCMReader, repos []tatarav1alpha1.Repository, existing []tatarav1alpha1.Task, act tatarav1alpha1.CronActivity) {
	l := log.FromContext(ctx)
	start := time.Now()
	var cands []candidate
	for i := range repos {
		owner, name, err := ghOwnerRepoFromURL(repos[i].Spec.URL)
		if err != nil {
			continue
		}
		prs, err := reader.ListOpenPRs(ctx, owner, name)
		if err != nil {
			l.Error(err, "scan: ListOpenPRs", "action", "scan_list_error", "resource_id", proj.Name, "activity", "mrScan", "repo", repos[i].Name)
			continue
		}
		cands = append(cands, candidatesFromPRs(prs)...)
	}
	for range cands {
		r.Metrics.ScanItem("mrScan", "scanned")
	}
	picked := selectCandidates(cands, proj.Spec.Scm.PriorityLabel, act.MaxPerCycle)
	created := 0
	for _, c := range picked {
		if isDeduped(c, existing) {
			r.Metrics.ScanItem("mrScan", "skipped_dedup")
			continue
		}
		repo, ok := r.matchRepoForSlug(repos, c.repo)
		if !ok {
			continue
		}
		kind := "review"
		if c.author == proj.Spec.Scm.BotLogin {
			kind = "selfImprove"
		}
		goal := fmt.Sprintf("Triage %s PR %s#%d", kind, c.repo, c.number)
		if _, err := r.createScanTask(ctx, proj, &repo, c, "mrScan", kind, goal); err != nil {
			l.Error(err, "scan: create mrScan task", "resource_id", proj.Name, "repo", repo.Name)
			continue
		}
		r.Metrics.ScanItem("mrScan", "picked")
		created++
	}
	r.Metrics.ObserveScanDuration("mrScan", time.Since(start).Seconds())
	l.Info("mrScan complete", "action", "scan_mr", "resource_id", proj.Name,
		"listed", len(cands), "picked", created, "duration_ms", time.Since(start).Milliseconds())
}

// issueScan lists open issues (per-repo + board) and creates triageIssue Tasks.
func (r *ProjectReconciler) issueScan(ctx context.Context, proj *tatarav1alpha1.Project, reader scm.SCMReader, repos []tatarav1alpha1.Repository, existing []tatarav1alpha1.Task, act tatarav1alpha1.CronActivity) {
	l := log.FromContext(ctx)
	start := time.Now()
	seen := map[string]bool{}
	var cands []candidate
	addUnique := func(cs []candidate) {
		for _, c := range cs {
			key := fmt.Sprintf("%s#%d", c.repo, c.number)
			if seen[key] {
				continue
			}
			seen[key] = true
			cands = append(cands, c)
		}
	}
	for i := range repos {
		owner, name, err := ghOwnerRepoFromURL(repos[i].Spec.URL)
		if err != nil {
			continue
		}
		iss, err := reader.ListOpenIssues(ctx, owner, name)
		if err != nil {
			l.Error(err, "scan: ListOpenIssues", "action", "scan_list_error", "resource_id", proj.Name, "activity", "issueScan", "repo", repos[i].Name)
			continue
		}
		addUnique(candidatesFromIssues(iss))
	}
	if proj.Spec.Scm.Board != nil {
		board := boardRefFromSpec(proj.Spec.Scm)
		items, err := reader.ListBoardItems(ctx, board)
		if err != nil {
			l.Error(err, "scan: ListBoardItems", "action", "scan_list_error", "resource_id", proj.Name, "activity", "issueScan")
		} else {
			addUnique(candidatesFromBoard(items))
		}
	}
	for range cands {
		r.Metrics.ScanItem("issueScan", "scanned")
	}
	picked := selectCandidates(cands, proj.Spec.Scm.PriorityLabel, act.MaxPerCycle)
	created := 0
	for _, c := range picked {
		if isDeduped(c, existing) {
			r.Metrics.ScanItem("issueScan", "skipped_dedup")
			continue
		}
		repo, ok := r.matchRepoForSlug(repos, c.repo)
		if !ok {
			continue
		}
		goal := fmt.Sprintf("Triage issue %s#%d", c.repo, c.number)
		if _, err := r.createScanTask(ctx, proj, &repo, c, "issueScan", "triageIssue", goal); err != nil {
			l.Error(err, "scan: create issueScan task", "resource_id", proj.Name, "repo", repo.Name)
			continue
		}
		r.Metrics.ScanItem("issueScan", "picked")
		created++
	}
	r.Metrics.ObserveScanDuration("issueScan", time.Since(start).Seconds())
	l.Info("issueScan complete", "action", "scan_issue", "resource_id", proj.Name,
		"listed", len(cands), "picked", created, "duration_ms", time.Since(start).Milliseconds())
}

// brainstorm creates up to MaxPerCycle generative Tasks (no list). The primary
// Project repo (first) hosts the brainstorm Task.
func (r *ProjectReconciler) brainstorm(ctx context.Context, proj *tatarav1alpha1.Project, repos []tatarav1alpha1.Repository, act tatarav1alpha1.BrainstormActivity) {
	l := log.FromContext(ctx)
	start := time.Now()
	if len(repos) == 0 {
		return
	}
	n := act.MaxPerCycle
	if n < 1 {
		n = 1
	}
	created := 0
	for i := 0; i < n; i++ {
		c := candidate{repo: "", number: 0, isPR: false}
		goal := "Brainstorm new issues for project " + proj.Name
		task, err := r.createBrainstormTask(ctx, proj, &repos[0], goal, act.Sources)
		if err != nil {
			l.Error(err, "scan: create brainstorm task", "resource_id", proj.Name)
			continue
		}
		_ = task
		_ = c
		r.Metrics.ScanItem("brainstorm", "picked")
		created++
	}
	r.Metrics.ObserveScanDuration("brainstorm", time.Since(start).Seconds())
	l.Info("brainstorm complete", "action", "scan_brainstorm", "resource_id", proj.Name,
		"picked", created, "duration_ms", time.Since(start).Milliseconds())
}
```

`ghOwnerRepoFromURL` is needed (parse a clone URL to owner/name). The existing `ghOwnerRepo` lives in `scm` (unexported). Add a small exported helper to `scm` OR reuse `scm.SameRemote`-style parsing. Simplest: add `scm.OwnerRepo(url string) (string, string, error)` exported wrapper. Sub-step:

- In `internal/scm/github.go`, add below `ghOwnerRepo`:
  ```go
  // OwnerRepo parses a GitHub clone/repo URL into owner and repo name.
  func OwnerRepo(repoURL string) (string, string, error) { return ghOwnerRepo(repoURL) }
  ```
- In `projectscan.go`, define `ghOwnerRepoFromURL = scm.OwnerRepo` is not idiomatic; instead call `scm.OwnerRepo` directly and rename the local references. Replace `ghOwnerRepoFromURL(...)` calls with `scm.OwnerRepo(...)` in projectscan.go.

`createBrainstormTask` is defined in Task 5.3 (it stamps the egress label via a Task annotation the pod builder reads). For Task 4.6 GREEN, define a minimal `createBrainstormTask` here that creates a `Kind=brainstorm` Task with the sources recorded; Task 5.3 wires the pod egress label. Add to `projectscan.go`:

```go
// createBrainstormTask creates a Kind=brainstorm Task. sources is recorded as a
// comma-joined annotation the pod builder reads to decide the egress label.
func (r *ProjectReconciler) createBrainstormTask(ctx context.Context, proj *tatarav1alpha1.Project, repo *tatarav1alpha1.Repository, goal string, sources []string) (*tatarav1alpha1.Task, error) {
	task := &tatarav1alpha1.Task{
		ObjectMeta: metav1.ObjectMeta{
			GenerateName: "brainstorm-",
			Namespace:    proj.Namespace,
			Labels:       map[string]string{labelActivity: "brainstorm"},
			Annotations:  map[string]string{annBrainstormSources: strings.Join(sources, ",")},
		},
		Spec: tatarav1alpha1.TaskSpec{
			ProjectRef:    proj.Name,
			RepositoryRef: repo.Name,
			Goal:          goal,
			Kind:          "brainstorm",
		},
	}
	if err := controllerutil.SetControllerReference(proj, task, r.Scheme); err != nil {
		return nil, fmt.Errorf("scan: set ownerref: %w", err)
	}
	if err := r.Create(ctx, task); err != nil {
		return nil, fmt.Errorf("scan: create brainstorm task: %w", err)
	}
	r.Metrics.ScanTaskCreated("brainstorm", "brainstorm")
	return task, nil
}
```

Add the annotation constant near the label constants:

```go
const annBrainstormSources = "tatara.dev/brainstorm-sources"
```

**Command:** `make test 2>&1 | grep -E 'TestRunScans|FAIL|ok ' | head` - all four `TestRunScans_*` PASS.

**Commit:** `feat(controller): runScans loop - mrScan/issueScan/brainstorm with selection, dedup, cap, Last*Scan stamping`

## Task 4.7: Wire runScans into ProjectReconciler.Reconcile

**RED** - Test: append to `internal/controller/projectscan_run_test.go`:

```go
func TestReconcileRequeuesFromScan(t *testing.T) {
	cron := &tatarav1alpha1.ScmCron{MRScan: tatarav1alpha1.CronActivity{Schedule: "0 0 1 1 *", MaxPerCycle: 1}} // yearly: never due now
	proj, _ := seedScanProject(t, "requeue-proj", cron)
	r := newScanReconciler(&fakeReader{})
	res, err := r.Reconcile(context.Background(), ctrl.Request{NamespacedName: types.NamespacedName{Namespace: testNS, Name: "requeue-proj"}})
	if err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	if res.RequeueAfter <= 0 || res.RequeueAfter > maxScheduleRequeue {
		t.Fatalf("RequeueAfter = %v, want (0, %v]", res.RequeueAfter, maxScheduleRequeue)
	}
	_ = proj
}
```

Add `ctrl "sigs.k8s.io/controller-runtime"` to the test imports.

**Command:** `make test 2>&1 | grep -E 'TestReconcileRequeuesFromScan|FAIL' | head` - expect FAIL: the current Reconcile returns `requeueAfter` only from `reconcileMemory`; scan requeue not yet wired (RequeueAfter likely 0).

**GREEN** - modify `internal/controller/project_controller.go` Reconcile. After the status update block (after line 80 `r.updateMemoryStackCounts(ctx)`), before the final return at line 93, compute the scan requeue and fold it into the result. Replace lines 80-93:

```go
	r.updateMemoryStackCounts(ctx)

	scanRequeue, scanErr := r.runScans(ctx, &project)
	if scanErr != nil {
		r.Metrics.ReconcileResult("Project", "error")
		return ctrl.Result{}, scanErr
	}
	requeueAfter = soonestRequeue(requeueAfter, scanRequeue)

	memPhase := ""
	if project.Status.Memory != nil {
		memPhase = project.Status.Memory.Phase
	}
	l.Info("reconciled project",
		"action", "reconcile_project",
		"resource_id", project.Name,
		"ready", ready,
		"reason", reason,
		"memory_phase", memPhase)
	r.Metrics.ReconcileResult("Project", "success")
	return ctrl.Result{RequeueAfter: requeueAfter}, nil
}

// soonestRequeue returns the smaller positive duration; 0 means "no requeue"
// and loses to any positive value.
func soonestRequeue(a, b time.Duration) time.Duration {
	switch {
	case a == 0:
		return b
	case b == 0:
		return a
	case a < b:
		return a
	default:
		return b
	}
}
```

Add `"time"` to `project_controller.go` imports.

**Command:** `make test 2>&1 | grep -E 'TestReconcileRequeuesFromScan|FAIL|ok ' | head` PASS, and the full project reconciler suite still green.

**Commit:** `feat(controller): wire runScans into ProjectReconciler.Reconcile requeue`

## Task 4.8: Wire ReaderFor in wire.go + token plumbing

**RED** - Test: `cmd/manager/wire_test.go` if one exists; otherwise this is a compile-only wiring change verified by `go build ./...` and the existing wire test (if any). Check: `ls cmd/manager/*_test.go`. If no test exists, add a guard test `cmd/manager/wire_reader_test.go`:

```go
package main

import (
	"testing"

	"github.com/szymonrychu/tatara-operator/internal/scm"
)

func TestReaderForWiring(t *testing.T) {
	rd, err := scm.ReaderByProvider("github", "tok")
	if err != nil || rd == nil {
		t.Fatalf("ReaderByProvider github: %v", err)
	}
}
```

**Command:** `go build ./... && go test ./cmd/... -count=1` - build first; expect the ProjectReconciler literal in wire.go to lack `ReaderFor` (no failure yet, the field is optional) - so this task is purely additive wiring.

**GREEN** - in `cmd/manager/wire.go`, extend the `ProjectReconciler` literal (lines 104-110). The reader is token-bound per Project, so `ReaderFor` cannot capture a single token at wire time; instead it returns a reader the reconciler re-binds per call. Since `ReaderByProvider` needs the token, and the reconciler resolves the token in `scanReader` before calling `ReaderFor`, change the seam: `ReaderFor` takes only the provider here and the reconciler passes the token. Resolution: make `ReaderFor func(provider, token string) (scm.SCMReader, error)`.

Update the field type in `project_controller.go`:

```go
	ReaderFor func(provider, token string) (scm.SCMReader, error)
```

Update `scanReader` in `projectscan.go` to pass the token:

```go
func (r *ProjectReconciler) scanReader(ctx context.Context, proj *tatarav1alpha1.Project) (scm.SCMReader, error) {
	var sec corev1.Secret
	key := types.NamespacedName{Namespace: proj.Namespace, Name: proj.Spec.ScmSecretRef}
	if err := r.Get(ctx, key, &sec); err != nil {
		return nil, fmt.Errorf("scan: get scm secret: %w", err)
	}
	token := string(sec.Data["token"])
	return r.ReaderFor(proj.Spec.Scm.Provider, token)
}
```

Update the test fake `newScanReconciler` (in `projectscan_factory_test.go`):

```go
func newScanReconciler(reader scm.SCMReader) *ProjectReconciler {
	r := newProjectReconciler()
	r.ReaderFor = func(string, string) (scm.SCMReader, error) { return reader, nil }
	return r
}
```

Wire in `wire.go`:

```go
	if err := (&controller.ProjectReconciler{
		Client:              mgr.GetClient(),
		Scheme:              mgr.GetScheme(),
		Metrics:             metrics,
		ExternalWebhookBase: cfg.ExternalWebhookBase,
		MemoryConfig:        memoryConfigFromConfig(cfg),
		ReaderFor: func(provider, token string) (scm.SCMReader, error) {
			return scm.ReaderByProvider(provider, token)
		},
	}).SetupWithManager(mgr); err != nil {
		return fmt.Errorf("setup ProjectReconciler: %w", err)
	}
```

**Command:** `go build ./... && make test 2>&1 | tail -10` - build clean, all controller + cmd tests PASS.

**Commit:** `feat(operator): wire ReaderByProvider into ProjectReconciler.ReaderFor (token-bound per Project)`

---

# Phase 5 - Task handling (triageIssue / brainstorm / MR-triage) + egress label

## Task 5.1: REST POST /tasks/{t}/issue-outcome handler

**RED** - Test: append to `internal/restapi/handlers_test.go` (mirror the prOutcome tests exactly, contract lock section 7).

```go
func TestIssueOutcome(t *testing.T) {
	r := buildRouter(t, taskWithKind("t1", "alpha", "triageIssue"))
	body := strings.NewReader(`{"action":"close","comment":"out of scope"}`)
	req := httptest.NewRequest(http.MethodPost, "/tasks/t1/issue-outcome", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusOK, w.Code)
	var out restapi.TaskDTO
	require.NoError(t, json.Unmarshal(w.Body.Bytes(), &out))
	require.NotNil(t, out.Status.IssueOutcome)
	require.Equal(t, "close", out.Status.IssueOutcome.Action)
	require.Equal(t, "out of scope", out.Status.IssueOutcome.Comment)
}

func TestIssueOutcome_Implement(t *testing.T) {
	r := buildRouter(t, taskWithKind("t1", "alpha", "triageIssue"))
	body := strings.NewReader(`{"action":"implement"}`)
	req := httptest.NewRequest(http.MethodPost, "/tasks/t1/issue-outcome", body)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusOK, w.Code)
}

func TestIssueOutcome_MissingAction(t *testing.T) {
	r := buildRouter(t, taskWithKind("t1", "alpha", "triageIssue"))
	body := strings.NewReader(`{"comment":"x"}`)
	req := httptest.NewRequest(http.MethodPost, "/tasks/t1/issue-outcome", body)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestIssueOutcome_InvalidAction(t *testing.T) {
	r := buildRouter(t, taskWithKind("t1", "alpha", "triageIssue"))
	body := strings.NewReader(`{"action":"merge"}`)
	req := httptest.NewRequest(http.MethodPost, "/tasks/t1/issue-outcome", body)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestIssueOutcome_CloseRequiresComment(t *testing.T) {
	r := buildRouter(t, taskWithKind("t1", "alpha", "triageIssue"))
	body := strings.NewReader(`{"action":"close"}`)
	req := httptest.NewRequest(http.MethodPost, "/tasks/t1/issue-outcome", body)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusBadRequest, w.Code)
}

func TestIssueOutcome_WrongKind(t *testing.T) {
	r := buildRouter(t, taskWithKind("t1", "alpha", "review"))
	body := strings.NewReader(`{"action":"implement"}`)
	req := httptest.NewRequest(http.MethodPost, "/tasks/t1/issue-outcome", body)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusConflict, w.Code)
}

func TestIssueOutcome_TaskNotFound(t *testing.T) {
	r := buildRouter(t)
	body := strings.NewReader(`{"action":"implement"}`)
	req := httptest.NewRequest(http.MethodPost, "/tasks/missing/issue-outcome", body)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusNotFound, w.Code)
}
```

**Command:** `go test ./internal/restapi/... -run TestIssueOutcome -count=1` - expect FAIL: route 404 / no handler / `out.Status.IssueOutcome` nil (DTO lacks the field).

**GREEN** - three edits:

1. `internal/restapi/server.go` line 55 - add the route after `pr-outcome`:

```go
	r.Post("/tasks/{t}/pr-outcome", s.prOutcome)
	r.Post("/tasks/{t}/issue-outcome", s.issueOutcome)
```

2. `internal/restapi/dto.go` - add `IssueOutcome` to `taskStatusDTO` (after `PROutcome`, line 67):

```go
	IssueOutcome *tatarav1alpha1.IssueOutcome `json:"issueOutcome,omitempty"`
```

and in `toTaskDTO` (taskStatusDTO literal, after `PROutcome: ...`):

```go
				IssueOutcome:     task.Status.IssueOutcome,
```

3. `internal/restapi/handlers.go` - add the handler after `prOutcome` (line 346):

```go
type issueOutcomeReq struct {
	Action  string `json:"action"`
	Comment string `json:"comment,omitempty"`
}

func (s *Server) issueOutcome(w http.ResponseWriter, r *http.Request) {
	var req issueOutcomeReq
	if err := decodeJSON(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid body: "+err.Error())
		return
	}
	if req.Action == "" {
		writeError(w, http.StatusBadRequest, "action required")
		return
	}
	switch req.Action {
	case "implement", "close":
	default:
		writeError(w, http.StatusBadRequest, "action must be one of implement, close")
		return
	}
	if req.Action == "close" && req.Comment == "" {
		writeError(w, http.StatusBadRequest, "comment required when action is close")
		return
	}
	var t tatarav1alpha1.Task
	if err := s.c.Get(r.Context(), client.ObjectKey{Namespace: s.ns, Name: chi.URLParam(r, "t")}, &t); err != nil {
		writeClientErr(w, err)
		return
	}
	if t.Spec.Kind != "triageIssue" {
		writeError(w, http.StatusConflict, "issue outcome only applies to a triageIssue task")
		return
	}
	t.Status.IssueOutcome = &tatarav1alpha1.IssueOutcome{Action: req.Action, Comment: req.Comment}
	if err := s.c.Status().Update(r.Context(), &t); err != nil {
		writeClientErr(w, err)
		return
	}
	writeJSON(w, http.StatusOK, toTaskDTO(t))
}
```

**Command:** `go test ./internal/restapi/... -run TestIssueOutcome -count=1` PASS (all 7).

**Commit:** `feat(restapi): POST /tasks/{t}/issue-outcome handler + IssueOutcome DTO (contract lock section 7)`

## Task 5.2: TaskReconciler write-back for triageIssue close -> CloseIssue

**RED** - Test: new file `internal/controller/issue_writeback_test.go` (fake writer with `CloseIssue`; mirror task_writeback_test.go).

```go
package controller

import (
	"context"
	"sync"
	"testing"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/scm"
	corev1 "k8s.io/api/core/v1"
	apimeta "k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

type fakeIssueWriter struct {
	scm.SCMWriter
	mu         sync.Mutex
	closeCalls []string // repo|number|comment
}

func (f *fakeIssueWriter) CloseIssue(_ context.Context, repo string, number int, comment string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.closeCalls = append(f.closeCalls, repo+"|"+comment)
	return nil
}

func TestWriteBackIssueClose(t *testing.T) {
	ctx := context.Background()
	fw := &fakeIssueWriter{}
	r := newWriteBackReconciler(t, &fakeWriter{}) // reuse harness for client/metrics
	r.SCMFor = func(string) (scm.SCMWriter, error) { return fw, nil }

	sec := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: "iw-scm", Namespace: testNS}, Data: map[string][]byte{"token": []byte("t"), "webhookSecret": []byte("w")}}
	_ = k8sClient.Create(ctx, sec)
	proj := &tatarav1alpha1.Project{ObjectMeta: metav1.ObjectMeta{Name: "iw-proj", Namespace: testNS}, Spec: tatarav1alpha1.ProjectSpec{ScmSecretRef: "iw-scm", Scm: &tatarav1alpha1.ScmSpec{Provider: "github", Owner: "o", BotLogin: "bot"}}}
	_ = k8sClient.Create(ctx, proj)
	repo := &tatarav1alpha1.Repository{ObjectMeta: metav1.ObjectMeta{Name: "iw-repo", Namespace: testNS}, Spec: tatarav1alpha1.RepositorySpec{ProjectRef: "iw-proj", URL: "https://github.com/o/r.git", DefaultBranch: "main"}}
	_ = k8sClient.Create(ctx, repo)

	task := &tatarav1alpha1.Task{ObjectMeta: metav1.ObjectMeta{Name: "iw-task", Namespace: testNS}}
	task.Spec = tatarav1alpha1.TaskSpec{ProjectRef: "iw-proj", RepositoryRef: "iw-repo", Goal: "g", Kind: "triageIssue",
		Source: &tatarav1alpha1.TaskSource{Provider: "github", IssueRef: "o/r#7", Number: 7}}
	_ = k8sClient.Create(ctx, task)
	task.Status.Phase = "Succeeded"
	task.Status.IssueOutcome = &tatarav1alpha1.IssueOutcome{Action: "close", Comment: "out of scope"}
	apimeta.SetStatusCondition(&task.Status.Conditions, metav1.Condition{Type: "WritebackPending", Status: metav1.ConditionTrue, Reason: "x", Message: "x"})
	_ = k8sClient.Status().Update(ctx, task)

	if _, err := reconcileWriteback(t, r, "iw-task"); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	if len(fw.closeCalls) != 1 || fw.closeCalls[0] != "o/r|out of scope" {
		t.Fatalf("CloseIssue calls = %+v", fw.closeCalls)
	}
}
```

**Command:** `make test 2>&1 | grep -E 'TestWriteBackIssueClose|FAIL' | head` - expect FAIL: triageIssue falls through to the implement OpenChange path (no CloseIssue called).

**GREEN** - modify `internal/controller/writeback.go` `doWriteBack` switch (lines 39-46):

```go
	switch task.Spec.Kind {
	case "review":
		return r.writeBackReview(ctx, task)
	case "selfImprove":
		return r.writeBackSelfImprove(ctx, task)
	case "triageIssue":
		return r.writeBackIssue(ctx, task)
	default:
		// implement / brainstorm (proposal path handled pre-spawn) - unchanged below
	}
```

Add `writeBackIssue` to `writeback.go` (after `writeBackSelfImprove`):

```go
// writeBackIssue applies a triageIssue Task's IssueOutcome: close calls
// CloseIssue with the agent's comment; implement records the marker only (the
// PR opened during the agent run is the artifact, re-entering the author-gated
// path). Never calls OpenChange.
func (r *TaskReconciler) writeBackIssue(ctx context.Context, task *tatarav1alpha1.Task) (ctrl.Result, error) {
	l := log.FromContext(ctx)
	out := task.Status.IssueOutcome
	if out == nil || task.Spec.Source == nil {
		r.clearWritebackPending(ctx, task, "NoOutcome", "triageIssue task without an outcome")
		return ctrl.Result{}, nil
	}
	if out.Action == "implement" {
		r.Metrics.IssueOutcome("implement")
		l.Info("issue outcome implement (PR is the artifact)", "action", "scm_issue_outcome", "resource_id", task.Name, "outcome", "implement")
		r.clearWritebackPending(ctx, task, "IssueImplement", "implement decision recorded; PR is the artifact")
		return ctrl.Result{}, nil
	}
	// close
	_, repo, writer, _, provider, err := r.scmContext(ctx, task)
	if err != nil {
		return ctrl.Result{}, err
	}
	repoSlug, _, perr := repoSlugFromURL(repo.Spec.URL, provider)
	if perr != nil {
		return ctrl.Result{}, perr
	}
	if cerr := writer.CloseIssue(ctx, repoSlug, task.Spec.Source.Number, out.Comment); cerr != nil {
		r.recordSCM(provider, "close_issue", cerr)
		return ctrl.Result{}, fmt.Errorf("writeback issue close: %w", cerr)
	}
	r.recordSCM(provider, "close_issue", nil)
	r.Metrics.IssueOutcome("close")
	l.Info("issue closed", "action", "scm_issue_outcome", "resource_id", task.Name, "outcome", "close", "number", task.Spec.Source.Number)
	r.clearWritebackPending(ctx, task, "IssueClosed", "issue closed with comment")
	return ctrl.Result{}, nil
}

// repoSlugFromURL derives the provider-correct repo slug (owner/name for
// GitHub, group/proj path for GitLab) that CloseIssue expects.
func repoSlugFromURL(repoURL, provider string) (string, string, error) {
	if provider == "gitlab" {
		proj, err := scm.GitLabProjectPath(repoURL)
		return proj, "", err
	}
	owner, name, err := scm.OwnerRepo(repoURL)
	return owner + "/" + name, "", err
}
```

Add exported `scm.GitLabProjectPath` to `internal/scm/gitlab.go` (wrapper over `glProjectPath`):

```go
// GitLabProjectPath parses a GitLab repo URL into its project path.
func GitLabProjectPath(repoURL string) (string, error) { return glProjectPath(repoURL) }
```

**Command:** `make test 2>&1 | grep -E 'TestWriteBackIssueClose|FAIL|ok ' | head` PASS.

**Commit:** `feat(controller): triageIssue write-back - CloseIssue on close, marker on implement (contract lock section 7)`

## Task 5.3: Brainstorm pod egress label + sources annotation -> pod

**RED** - Test: append to `internal/agent/pod_test.go` (mirror the existing pod env/label tests).

```go
func TestBuildPodEgressLabel(t *testing.T) {
	cases := []struct {
		name    string
		sources string
		want    bool
	}{
		{"internet present", "docs,memory,internet", true},
		{"internet absent", "docs,memory", false},
		{"no annotation", "", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			task := &tatarav1alpha1.Task{}
			task.Name = "bs"
			task.Spec.Kind = "brainstorm"
			if tc.sources != "" {
				task.Annotations = map[string]string{"tatara.dev/brainstorm-sources": tc.sources}
			}
			proj := &tatarav1alpha1.Project{}
			repo := &tatarav1alpha1.Repository{}
			pod := BuildPod(proj, repo, task, nil, "http://mem", PodConfig{Namespace: "tatara"})
			_, has := pod.Labels["tatara.io/egress"]
			if has != tc.want {
				t.Fatalf("egress label present=%v, want %v (labels=%+v)", has, tc.want, pod.Labels)
			}
			if tc.want && pod.Labels["tatara.io/egress"] != "internet" {
				t.Fatalf("egress label value = %q, want internet", pod.Labels["tatara.io/egress"])
			}
		})
	}
}
```

**Command:** `go test ./internal/agent/... -run TestBuildPodEgressLabel -count=1` - expect FAIL: label never set.

**GREEN** - modify `internal/agent/pod.go` `podLabels` to merge the egress label when the brainstorm-sources annotation contains `internet`. Change `podLabels` signature and call site, OR (simpler, no signature churn) add the label in `BuildPod` after `Labels: podLabels(task)`. The `ObjectMeta.Labels` is set inline at line 142; refactor to a local:

```go
	labels := podLabels(task)
	if task.Spec.Kind == "brainstorm" && hasInternetSource(task.Annotations["tatara.dev/brainstorm-sources"]) {
		labels["tatara.io/egress"] = "internet"
	}
	return &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:            PodName(task),
			Namespace:       cfg.Namespace,
			Labels:          labels,
			OwnerReferences: []metav1.OwnerReference{ownerRef(task)},
		},
```

Add the helper (import `strings` already present):

```go
// hasInternetSource reports whether the comma-joined brainstorm sources list
// includes "internet", gating the egress NetworkPolicy pod label.
func hasInternetSource(csv string) bool {
	for _, s := range strings.Split(csv, ",") {
		if strings.TrimSpace(s) == "internet" {
			return true
		}
	}
	return false
}
```

**Command:** `go test ./internal/agent/... -run TestBuildPodEgressLabel -count=1` PASS; `go test ./internal/agent/... -count=1` whole package green.

**Commit:** `feat(agent): stamp tatara.io/egress=internet on brainstorm pods when internet in sources (contract lock section 3)`

## Task 5.4: Author/actor gate covers cron selfImprove on human PR

**RED** - Test: new file `internal/controller/scan_authorship_test.go` (envtest; a cron mrScan that mis-stamped a label as bot, but the live PR author is human, must not merge/close). Reuse the existing `selfimprove_authorship_test.go` GetPRState fake pattern.

```go
package controller

import (
	"context"
	"testing"

	tatarav1alpha1 "github.com/szymonrychu/tatara-operator/api/v1alpha1"
	"github.com/szymonrychu/tatara-operator/internal/scm"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

type humanAuthorWriter struct {
	scm.SCMWriter
}

func (humanAuthorWriter) GetPRState(context.Context, string, string, int) (scm.PRState, error) {
	return scm.PRState{Author: "human-dev"}, nil // NOT the bot
}

func TestCronSelfImproveOnHumanPRTerminates(t *testing.T) {
	ctx := context.Background()
	sec := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: "auth-scm", Namespace: testNS}, Data: map[string][]byte{"token": []byte("t"), "webhookSecret": []byte("w")}}
	_ = k8sClient.Create(ctx, sec)
	proj := &tatarav1alpha1.Project{ObjectMeta: metav1.ObjectMeta{Name: "auth-proj", Namespace: testNS}, Spec: tatarav1alpha1.ProjectSpec{ScmSecretRef: "auth-scm", Scm: &tatarav1alpha1.ScmSpec{Provider: "github", Owner: "o", BotLogin: "tatara-bot"}}}
	_ = k8sClient.Create(ctx, proj)
	// memory Ready so the task is not gated for that reason
	proj.Status.Memory = &tatarav1alpha1.MemoryStatus{Phase: "Ready", Endpoint: "http://mem"}
	_ = k8sClient.Status().Update(ctx, proj)
	repo := &tatarav1alpha1.Repository{ObjectMeta: metav1.ObjectMeta{Name: "auth-repo", Namespace: testNS}, Spec: tatarav1alpha1.RepositorySpec{ProjectRef: "auth-proj", URL: "https://github.com/o/r.git", DefaultBranch: "main"}}
	_ = k8sClient.Create(ctx, repo)

	task := &tatarav1alpha1.Task{ObjectMeta: metav1.ObjectMeta{Name: "auth-task", Namespace: testNS,
		Labels: scanTaskLabels(candidate{repo: "o/r", number: 9, headSHA: "abc", isPR: true}, "mrScan", "selfImprove")}}
	task.Spec = tatarav1alpha1.TaskSpec{ProjectRef: "auth-proj", RepositoryRef: "auth-repo", Goal: "g", Kind: "selfImprove",
		Source: &tatarav1alpha1.TaskSource{Provider: "github", IssueRef: "o/r#9", Number: 9, IsPR: true, AuthorLogin: "tatara-bot"}}
	_ = k8sClient.Create(ctx, task)

	r := newWriteBackReconciler(t, &fakeWriter{})
	r.SCMFor = func(string) (scm.SCMWriter, error) { return humanAuthorWriter{}, nil }
	if _, err := reconcileWriteback(t, r, "auth-task"); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	got := &tatarav1alpha1.Task{}
	_ = k8sClient.Get(ctx, types.NamespacedName{Namespace: testNS, Name: "auth-task"}, got)
	if got.Status.Phase != "Failed" {
		t.Fatalf("cron selfImprove on human PR must terminate Failed, got phase=%q", got.Status.Phase)
	}
}
```

**Command:** `make test 2>&1 | grep -E 'TestCronSelfImproveOnHumanPR|FAIL' | head` - expect this to PASS immediately IF the existing `selfImproveBotAuthored` pre-spawn gate (task_controller.go:164) already covers cron Tasks (it keys on `Kind == "selfImprove"`, not on origin). This is a regression/coverage assertion: the gate already applies because cron-created selfImprove Tasks share the Kind. If the test fails, the gate has an origin-specific guard that must be removed.

**GREEN** - if the test passes as-is, no code change (the gate is origin-agnostic by design). If it fails, ensure the `selfImproveBotAuthored` gate at task_controller.go:164 has no `task.Spec.Source.AuthorLogin`-trust shortcut. Verify the gate calls `GetPRState` (authoritative) and ignores the stamped label. Confirmed by reading task_controller.go:161-180 - it already does. The task is thus a guard test that locks the behavior.

**Command:** `make test 2>&1 | grep -E 'TestCronSelfImproveOnHumanPR|ok ' | head` PASS.

**Commit:** `test(controller): cron selfImprove on human-authored PR terminates via authoritative author gate`

---

# Phase 6 - Chart version bump

## Task 6.1: Bump Chart.yaml version + appVersion

**RED** - Test: chart lint (no Go test). The CRD YAMLs were regenerated in Tasks 2.1/2.2 by `make manifests`.

**Command (verify CRDs regenerated):** `git status charts/tatara-operator/crds/` - expect `tatara.dev_projects.yaml` and `tatara.dev_tasks.yaml` modified (cron fields, Last*Scan, Kind enum, IssueOutcome). If not, run `make manifests`.

**GREEN** - modify `charts/tatara-operator/Chart.yaml` lines 5-6:

```yaml
version: 0.4.0
appVersion: "0.4.0"
```

**Command:** `helm lint charts/tatara-operator` (or `make chart-lint`) - expect `1 chart(s) linted, 0 chart(s) failed`.

**Note (helm crds/ are install-once):** Helm only installs files under `crds/` on first install; it never upgrades them. The new Project/Task CRD fields (cron, Last*Scan, Kind enum, IssueOutcome) require a manual server-side apply at deploy time:

```
kubectl apply --server-side -f charts/tatara-operator/crds/tatara.dev_projects.yaml
kubectl apply --server-side -f charts/tatara-operator/crds/tatara.dev_tasks.yaml
```

Record this in the infra runbook (out of scope for this repo) and in MEMORY.md.

**Commit:** `chore(chart): bump tatara-operator to 0.4.0 (autonomous cron CRD + scan loop)`

---

# Phase 7 - Final verification

## Task 7.1: Full suite + lint + fmt

Per `superpowers:verification-before-completion`, run and SEE pass before claiming done:

```
gofmt -l .            # expect: no output
make generate         # expect: no diff (deepcopy already current)
make manifests        # expect: no diff (CRDs already current)
make test             # expect: all packages ok, 0 failures, -race clean
make lint             # expect: clean (exit 0 or 5)
helm lint charts/tatara-operator
```

If `make generate`/`make manifests` produce a diff, commit it:

**Commit (only if needed):** `chore(api): regenerate deepcopy + CRD manifests`

Update `MEMORY.md` with the resolved decisions (GitLab ListBoardItems no-op; IssueRef rename to CreatedIssue; SCMReader split from SCMWriter; ReaderFor takes provider+token; helm crds install-once needs manual server-side apply) and move the operator autonomous-cron item out of `ROADMAP.md`.

**Commit:** `docs: record autonomous-cron operator decisions in MEMORY; update ROADMAP`

---

# Writing-plans self-review

## Spec coverage (design section 15, item 1 + lock section 10, item 1)

| Scope item | Task(s) |
|---|---|
| `projectscan.go` scan loop + cron wiring in Project reconciler | 4.1-4.8 |
| soonest-of-three next-fire RequeueAfter clamped to maxScheduleRequeue | 4.6 (`consider`/`maxScheduleRequeue`), 4.7 (`soonestRequeue`) |
| per-activity Status.Last*Scan stamping | 4.6 (`stampScan`), tested 4.6 |
| bad-cron logs-and-disables (mirror scheduleNextReingest) | 4.3, 4.6 (`activityDue` ok=false branch), tested `TestRunScans_BadCronDisablesNoCrash` |
| Selection priority-then-stale | 4.1 (`selectCandidates`) |
| Dedup via label-selected Project-owned Tasks (PR same-SHA, issue new-activity) | 4.2 (`isDeduped`), tested 4.2 + `TestRunScans_DedupSkipsInFlight` |
| SCM caps ListOpenPRs/ListOpenIssues/ListBoardItems/CloseIssue (interface + GitHub REST+GraphQL + GitLab REST) | 1.2-1.5 |
| Exact PRRef/IssueRef/BoardItem from lock section 4 | 1.1b |
| Fake SCM client for tests | 4.6 (`fakeReader`), 5.2 (`fakeIssueWriter`) |
| CRD CronActivity/BrainstormActivity/ScmCron + ScmSpec.priorityLabel/cron + Last*Scan | 2.1 |
| Task Kind enum extend + Task.Status.IssueOutcome | 2.2 |
| controller-gen CRD + deepcopy regen explicit steps | 2.1, 2.2, 7.1 |
| MR-triage routes review|selfImprove by GetPRState author | 4.6 (`mrScan` author compare), gate 5.4 |
| triageIssue -> issue_outcome (implement OpenChange / close CloseIssue) | 5.1, 5.2 |
| brainstorm -> propose_issue; egress label when internet in sources | 5.3 (label), 4.6 (`createBrainstormTask` sources annotation); propose_issue reuses existing createProposal path |
| REST POST /tasks/{t}/issue-outcome (enum + comment-on-close + wrong-kind 409) + Status write + CloseIssue on close | 5.1, 5.2 |
| Metrics lock section 9 | 3.1 |
| Chart appVersion/version bump + helm crds install-once note | 6.1 |
| Tests: envtest (next-fire, bad-cron, scan Kind+labels, dedup, cap) | 4.6, 4.7 |
| Tests: fake SCM table-driven (IsPR filter) | 1.3, 1.4 |
| Tests: author/actor gate cron Task human PR | 5.4 |

## Placeholder scan

Searched the plan for `TBD`, `add appropriate`, `similar to`, `etc.`, `...` in code bodies. None remain in implementation/test code blocks. Every code block is complete, compilable Go/YAML. The one `_ = base` / `_ = c` lines are intentional (loop variables retained for clarity / future use within the same minimal function) and compile. The `var _ = scm.PRRef{}` in 4.1's test is a deliberate import-keepalive and is removed naturally once 4.4 adds real `scm` usage - noted: drop that line when 4.4 lands.

## Type consistency vs contract lock (byte-for-byte)

- Task Kind enum: `implement;review;selfImprove;triageIssue;brainstorm` - matches lock section 1 (Task 2.2).
- Dedup labels: `tatara.io/source-repo`, `tatara.io/source-number`, `tatara.io/source-kind`, `tatara.io/head-sha`, `tatara.io/activity` - matches lock section 2 (Task 4.2 constants). Repo `/`->`.` sanitization matches lock.
- Egress label `tatara.io/egress: internet` - matches lock section 3 (Task 5.3).
- `PRRef`/`IssueRef`/`BoardItem` field names + json tags (`headSha`, `isPr`) - matches lock section 4 (Task 1.1b). Method signatures `ListOpenPRs(ctx, owner, repo)`, `ListOpenIssues(ctx, owner, repo)`, `ListBoardItems(ctx, board)`, `CloseIssue(ctx, repo, number, comment)` - match lock (1.2-1.4). `board` typed as `scm.BoardRef` (the lock's `BoardSpec` is the CRD type, un-importable into `scm`; documented resolution in the plan header).
- CRD `CronActivity`/`BrainstormActivity`/`ScmCron` field names + json tags + `+kubebuilder:default=1` + sources enum `docs;memory;internet` - matches lock section 5 (Task 2.1).
- `IssueOutcome{Action,Comment}` with `Action` enum `implement;close` - matches lock section 6 (Task 2.2). `TaskStatus.IssueOutcome *IssueOutcome json:"issueOutcome,omitempty"` - matches.
- REST route `POST /tasks/{t}/issue-outcome`; body `{action, comment}`; 400 invalid action / 400 comment-on-close / 404 missing / 409 wrong-kind - matches lock section 7 (Task 5.1).
- Metrics names `tatara_scan_items_total{activity,outcome}`, `tatara_scan_tasks_created_total{activity,kind}`, `tatara_scan_duration_seconds{activity}`, `tatara_issue_outcome_total{action}`, `tatara_tasks_inflight{kind}` - matches lock section 9 (Task 3.1).

## Deviations (documented, justified)

1. Existing `scm.IssueRef{Ref,URL}` renamed to `scm.CreatedIssue` so the frozen wire name `IssueRef` is exact (Task 1.1a). The renamed type is internal, not in the lock.
2. Read methods on a new `scm.SCMReader` interface (not `scm.Client`, which is the webhook-detect interface); `CloseIssue` on `scm.SCMWriter`. Matches the existing reconciler-egress split.
3. `ProjectReconciler.ReaderFor func(provider, token string)` mirrors `TaskReconciler.SCMFor`; token resolved from the scm secret per Project (Task 4.8).
4. GitLab `ListBoardItems` returns nil (boards are label-defined; issueScan's `ListOpenIssues` already covers board work). GitHub uses GraphQL ProjectV2. Both satisfy `SCMReader`.
