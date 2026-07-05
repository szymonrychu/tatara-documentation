# Bot MR auto-merge on green pipeline - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the tatara operator opens a bot-authored PR/MR under
`mergePolicy: autoMergeOnGreenCI`, enable the forge's native auto-merge so it
merges to the default branch once required checks pass, and the linked issue
closes.

**Architecture:** New `EnableAutoMerge` method on the `SCMWriter` interface
(GitHub `enablePullRequestAutoMerge` GraphQL / GitLab
`merge_when_pipeline_succeeds`). `writeBackOpenChange` calls it best-effort after
opening each PR when the project policy is `autoMergeOnGreenCI`. `writeBackBody`
gains a `Closes #N` line for issue-sourced PRs. CI gate prerequisite: every CI
repo must emit a `smoke` check (operator and memory-repo-ingester currently do
not); branch protection then requires `secscan/lint/test/build/smoke`.

**Tech Stack:** Go 1.x, controller-runtime, GitHub REST+GraphQL, GitLab REST,
GitHub Actions, `gh` CLI.

**Pre-req before starting Part A:** the in-flight chart-publish PRs (operator #10
etc.) must be merged first; then create the worktree off **fresh** operator
`main` (`git checkout main && git pull`) per the dev-from-fresh-main rule (bots
push to these repos).

**Spec:** `docs/superpowers/specs/2026-06-12-bot-mr-auto-merge-design.md`

---

## Part A - operator code (`tatara-operator`)

All Part A tasks run in one worktree off `tatara-operator` `main`. Existing
patterns to mirror: `internal/scm/github.go` `Merge` (REST PUT),
`internal/scm/github_graphql.go` `ghGraphQL`/`ghResourceID`/`AddBoardItem`
(GraphQL mutation), `internal/scm/gitlab.go` `Merge` (REST PUT),
`internal/scm/github_writeback_test.go` + `gitlab_writeback_test.go` (httptest
pattern for OpenChange).

### Task A1: GitHub `EnableAutoMerge`

**Files:**
- Modify: `internal/scm/github.go` (add method + `ghMergeMethod` helper, after `Merge` ~line 386)
- Test: `internal/scm/github_writeback_test.go` (add tests)

- [ ] **Step 1: Write the failing tests**

Append to `internal/scm/github_writeback_test.go`:

```go
func TestGitHubEnableAutoMerge(t *testing.T) {
	var gotGraphQL string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		switch {
		case strings.Contains(string(body), "resource(url:"):
			// ghResourceID lookup -> return a PR node id
			_, _ = w.Write([]byte(`{"data":{"resource":{"id":"PR_node_123"}}}`))
		case strings.Contains(string(body), "enablePullRequestAutoMerge"):
			gotGraphQL = string(body)
			_, _ = w.Write([]byte(`{"data":{"enablePullRequestAutoMerge":{"clientMutationId":null}}}`))
		default:
			t.Fatalf("unexpected graphql body: %s", body)
		}
	}))
	defer srv.Close()

	c := &GitHub{graphQLBase: srv.URL}
	err := c.EnableAutoMerge(context.Background(), "https://github.com/o/r.git", "ghtok",
		"https://github.com/o/r/pull/7", "squash")
	require.NoError(t, err)
	require.Contains(t, gotGraphQL, "PR_node_123")
	require.Contains(t, gotGraphQL, "SQUASH")
}

func TestGitHubEnableAutoMergeError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if strings.Contains(string(body), "resource(url:") {
			_, _ = w.Write([]byte(`{"data":{"resource":{"id":"PR_node_123"}}}`))
			return
		}
		// auto-merge not allowed (no branch protection) -> graphql error
		_, _ = w.Write([]byte(`{"errors":[{"message":"Auto merge is not allowed"}]}`))
	}))
	defer srv.Close()
	c := &GitHub{graphQLBase: srv.URL}
	err := c.EnableAutoMerge(context.Background(), "https://github.com/o/r.git", "t",
		"https://github.com/o/r/pull/7", "squash")
	require.Error(t, err)
}
```

If `httptest`, `io`, `strings`, `net/http` are not already imported in this test
file, add them.

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd tatara-operator && go test ./internal/scm/ -run TestGitHubEnableAutoMerge -count=1`
Expected: FAIL - `c.EnableAutoMerge undefined`.

- [ ] **Step 3: Implement**

In `internal/scm/github.go`, after the `Merge` method:

```go
// EnableAutoMerge turns on GitHub native auto-merge for the PR at prURL, so the
// forge merges it once the branch's required status checks pass. Requires the
// repo to allow auto-merge and main to have a branch-protection rule with at
// least one required check; otherwise GitHub returns an error (caller treats it
// as non-fatal).
func (c *GitHub) EnableAutoMerge(ctx context.Context, repoURL, token, prURL, method string) error {
	prID, err := c.ghResourceID(ctx, token, prURL)
	if err != nil {
		return fmt.Errorf("github: resolve pr node id: %w", err)
	}
	q := fmt.Sprintf(`mutation { enablePullRequestAutoMerge(input:{pullRequestId:%q, mergeMethod: %s}) { clientMutationId } }`,
		prID, ghMergeMethod(method))
	return c.ghGraphQL(ctx, token, q, nil, nil)
}

func ghMergeMethod(method string) string {
	switch method {
	case "merge":
		return "MERGE"
	case "rebase":
		return "REBASE"
	default:
		return "SQUASH"
	}
}
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `cd tatara-operator && go test ./internal/scm/ -run TestGitHubEnableAutoMerge -count=1`
Expected: PASS (both).

- [ ] **Step 5: Commit**

```bash
git add internal/scm/github.go internal/scm/github_writeback_test.go
git commit -m "feat(scm): GitHub EnableAutoMerge via enablePullRequestAutoMerge"
```

### Task A2: GitLab `EnableAutoMerge`

**Files:**
- Modify: `internal/scm/gitlab.go` (add method + `glIIDFromURL` helper, after `Merge` ~line 435)
- Test: `internal/scm/gitlab_writeback_test.go` (add tests)

- [ ] **Step 1: Write the failing test**

Append to `internal/scm/gitlab_writeback_test.go`:

```go
func TestGitLabEnableAutoMerge(t *testing.T) {
	var gotPath, gotBody string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		b, _ := io.ReadAll(r.Body)
		gotBody = string(b)
		_, _ = w.Write([]byte(`{}`))
	}))
	defer srv.Close()

	c := &GitLab{apiBase: srv.URL}
	err := c.EnableAutoMerge(context.Background(), "https://gitlab.com/g/p.git", "gltok",
		"https://gitlab.com/g/p/-/merge_requests/5", "squash")
	require.NoError(t, err)
	require.Equal(t, "/projects/g%2Fp/merge_requests/5/merge", gotPath)
	require.Contains(t, gotBody, "merge_when_pipeline_succeeds")
}
```

If the file's `GitLab` struct field for the API base is named differently than
`apiBase`, mirror what `TestGitLabOpenChange` uses to construct the client.

- [ ] **Step 2: Run test, verify it fails**

Run: `cd tatara-operator && go test ./internal/scm/ -run TestGitLabEnableAutoMerge -count=1`
Expected: FAIL - `c.EnableAutoMerge undefined`.

- [ ] **Step 3: Implement**

In `internal/scm/gitlab.go`, after the `Merge` method:

```go
// EnableAutoMerge sets merge-when-pipeline-succeeds on the MR at mrURL so GitLab
// merges it once its pipeline passes. Best-effort: the endpoint can 405 if no
// pipeline exists yet, which the caller treats as non-fatal.
func (c *GitLab) EnableAutoMerge(ctx context.Context, repoURL, token, mrURL, method string) error {
	proj, err := glProjectPath(repoURL)
	if err != nil {
		return err
	}
	iid, err := glIIDFromURL(mrURL)
	if err != nil {
		return err
	}
	in := map[string]bool{"merge_when_pipeline_succeeds": true, "squash": method == "squash"}
	path := "/projects/" + url.PathEscape(proj) + "/merge_requests/" + strconv.Itoa(iid) + "/merge"
	return glDo(ctx, c.base(), http.MethodPut, path, token, in, nil)
}

func glIIDFromURL(mrURL string) (int, error) {
	i := strings.LastIndex(mrURL, "/")
	if i < 0 || i+1 >= len(mrURL) {
		return 0, fmt.Errorf("gitlab: cannot parse iid from %q", mrURL)
	}
	return strconv.Atoi(mrURL[i+1:])
}
```

- [ ] **Step 4: Run test, verify it passes**

Run: `cd tatara-operator && go test ./internal/scm/ -run TestGitLabEnableAutoMerge -count=1`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/scm/gitlab.go internal/scm/gitlab_writeback_test.go
git commit -m "feat(scm): GitLab EnableAutoMerge via merge_when_pipeline_succeeds"
```

### Task A3: add to `SCMWriter`, wire into `writeBackOpenChange`

**Files:**
- Modify: `internal/scm/scm.go` (add `EnableAutoMerge` to the `SCMWriter` interface, after the `Merge` line ~107)
- Modify: `internal/controller/writeback.go` (`writeBackOpenChange`, the open loop ~line 113-129)
- Modify: `internal/controller/task_writeback_test.go` (the fake writer + a policy test)

- [ ] **Step 1: Add the interface method**

In `internal/scm/scm.go`, in the `SCMWriter` interface, directly after:

```go
	Merge(ctx context.Context, repoURL, token string, number int, method string) error
```

add:

```go
	EnableAutoMerge(ctx context.Context, repoURL, token, prURL, method string) error
```

- [ ] **Step 2: Run build, verify the fake writer no longer satisfies the interface**

Run: `cd tatara-operator && go build ./... 2>&1 | head`
Expected: FAIL - the test fake (the type assigned to `r.SCMFor` in
`task_writeback_test.go`, look for `openCalls`) is missing `EnableAutoMerge`.

- [ ] **Step 3: Extend the fake writer + write the failing policy test**

In `internal/controller/task_writeback_test.go`, on the fake writer struct that
has `openCalls`, add a counter field `enableAutoMergeCalls int` and the method:

```go
func (f *fakeWriter) EnableAutoMerge(ctx context.Context, repoURL, token, prURL, method string) error {
	f.enableAutoMergeCalls++
	return nil
}
```

(Use the fake type's actual name; `fakeWriter` is a placeholder for whatever the
file defines.)

Then add a test that proves the policy gate. Model the setup on the existing
`TestWriteBackIssue_ImplementCallsOpenChange` (an implement/triageIssue task that
reaches `writeBackOpenChange`):

```go
func TestWriteBackOpenChange_AutoMergeOnGreenCIEnablesAutoMerge(t *testing.T) {
	// Project policy autoMergeOnGreenCI -> EnableAutoMerge called per opened PR.
	fw := &fakeWriter{}
	// build a Project whose Spec.Scm.MergePolicy == "autoMergeOnGreenCI",
	// a Repository with a branch, and an implement Task whose pushed branch
	// makes OpenChange succeed (mirror TestWriteBackIssue_ImplementCallsOpenChange).
	// ... run reconcile/writeback ...
	require.GreaterOrEqual(t, fw.enableAutoMergeCalls, 1)
}

func TestWriteBackOpenChange_AfterApprovalSkipsAutoMerge(t *testing.T) {
	fw := &fakeWriter{}
	// same as above but Spec.Scm.MergePolicy == "afterApproval" (or empty)
	require.Zero(t, fw.enableAutoMergeCalls)
}
```

Fill the Project/Repository/Task construction by copying the existing
`TestWriteBackIssue_ImplementCallsOpenChange` setup verbatim and only changing
`Spec.Scm.MergePolicy`.

- [ ] **Step 4: Run tests, verify the new policy test fails**

Run: `cd tatara-operator && go test ./internal/controller/ -run AutoMerge -count=1`
Expected: FAIL - `enableAutoMergeCalls` stays 0 (wiring not added yet).

- [ ] **Step 5: Wire `writeBackOpenChange`**

In `internal/controller/writeback.go`, before the `for _, repo := range ordered`
loop (just after `body := writeBackBody(task)`), add:

```go
	autoMerge := proj.Spec.Scm != nil && proj.Spec.Scm.MergePolicy == "autoMergeOnGreenCI"
```

Inside the loop, immediately after `prURLs = append(prURLs, prURL)`:

```go
		if autoMerge {
			if e := writer.EnableAutoMerge(ctx, repo.Spec.URL, token, prURL, "squash"); e != nil {
				l.Error(e, "writeback: enable auto-merge (non-fatal)", "repo", repo.Name, "pr_url", prURL)
				r.recordSCM(provider, "auto_merge", e)
			} else {
				r.recordSCM(provider, "auto_merge", nil)
			}
		}
```

- [ ] **Step 6: Run tests, verify they pass**

Run: `cd tatara-operator && go test ./internal/controller/ -run AutoMerge -count=1`
Expected: PASS (both).

- [ ] **Step 7: Commit**

```bash
git add internal/scm/scm.go internal/controller/writeback.go internal/controller/task_writeback_test.go
git commit -m "feat(controller): enable native auto-merge on bot PRs under autoMergeOnGreenCI"
```

### Task A4: `Closes #N` in PR body for issue-sourced PRs

**Files:**
- Modify: `internal/controller/writeback.go` (`writeBackBody` ~line 254-263)
- Test: `internal/controller/task_writeback_test.go` (add unit test)

- [ ] **Step 1: Write the failing test**

Add to `internal/controller/task_writeback_test.go`:

```go
func TestWriteBackBody_ClosesIssueWhenIssueSourced(t *testing.T) {
	issueTask := &tatarav1alpha1.Task{}
	issueTask.Spec.Goal = "Fix the thing"
	issueTask.Spec.Source = &tatarav1alpha1.TaskSource{IsPR: false, Number: 42}
	require.Contains(t, writeBackBody(issueTask), "Closes #42")

	prTask := &tatarav1alpha1.Task{}
	prTask.Spec.Goal = "Self improve"
	prTask.Spec.Source = &tatarav1alpha1.TaskSource{IsPR: true, Number: 7}
	require.NotContains(t, writeBackBody(prTask), "Closes #")
}
```

Use the actual type name of `task.Spec.Source` (find it via `grep -n "Source \*"
api/v1alpha1/task_types.go`); `TaskSource` is a placeholder.

- [ ] **Step 2: Run test, verify it fails**

Run: `cd tatara-operator && go test ./internal/controller/ -run TestWriteBackBody_Closes -count=1`
Expected: FAIL - body has no `Closes #42`.

- [ ] **Step 3: Implement**

In `writeBackBody`, before the final `return`, add:

```go
	if t.Spec.Source != nil && !t.Spec.Source.IsPR && t.Spec.Source.Number > 0 {
		b += fmt.Sprintf("\n\nCloses #%d", t.Spec.Source.Number)
	}
```

- [ ] **Step 4: Run test, verify it passes**

Run: `cd tatara-operator && go test ./internal/controller/ -run TestWriteBackBody_Closes -count=1`
Expected: PASS.

- [ ] **Step 5: Full suite + lint**

Run:
```bash
cd tatara-operator
KUBEBUILDER_ASSETS="$(go run sigs.k8s.io/controller-runtime/tools/setup-envtest@release-0.21 use 1.33.0 -p path)" go test ./... -count=1
gofmt -l . && golangci-lint run
```
Expected: all tests PASS; `gofmt -l` prints nothing; lint clean.

- [ ] **Step 6: Commit**

```bash
git add internal/controller/writeback.go internal/controller/task_writeback_test.go
git commit -m "feat(controller): add Closes #N to issue-sourced PR bodies"
```

### Task C1: operator `smoke` CI job (same repo, same worktree)

**Files:**
- Modify: `tatara-operator/.github/workflows/ci.yml` (add a `smoke` job between `build` and `image`)

- [ ] **Step 1: Add the job**

Insert after the `build:` job block, before `image:`:

```yaml
  smoke:
    runs-on: tatara-operator
    needs: [build]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version-file: go.mod
          cache: true
      - name: smoke
        run: |
          make build
          ./bin/tatara-operator --help || true
```

(Operator builds with `CGO_ENABLED=0`, so no `build-essential` step is needed.)
Match the surrounding indentation exactly (`actionlint` runs in CI).

- [ ] **Step 2: Lint the workflow locally if actionlint is available**

Run: `cd tatara-operator && actionlint .github/workflows/ci.yml || true`
Expected: no errors (or actionlint not installed - the PR's own lint will catch).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add smoke job (build + --help) so PRs emit a smoke check"
```

This completes the `tatara-operator` worktree. Run
`superpowers:finishing-a-development-branch` to merge to `main` (no push of the
PR is needed if merging locally; CI publishes the image+chart on the `main`
push).

---

## Part C2: ingester `smoke` CI job (`tatara-memory-repo-ingester`, separate PR)

Separate repo -> separate small branch + PR (use `git-commit-mr`-style flow or a
short-lived branch; CI must go green before merge).

**Files:**
- Modify: `tatara-memory-repo-ingester/.github/workflows/ci.yml` (add `smoke` between `build` and `image`)

- [ ] **Step 1: Add the job**

Insert after the `build:` job block, before `image:`:

```yaml
  smoke:
    runs-on: tatara-memory-repo-ingester
    needs: [build]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version-file: go.mod
          cache: true
      - name: build tools
        run: sudo apt-get update && sudo apt-get install -y --no-install-recommends build-essential
      - name: smoke
        run: |
          make build
          ./bin/tatara-ingest --help || true
```

(Ingester builds with `CGO_ENABLED=1`, so `build-essential` is required for the
build.)

- [ ] **Step 2: Commit + open PR; wait for green**

```bash
cd tatara-memory-repo-ingester
git checkout main && git pull
git checkout -b ci/smoke-job
git add .github/workflows/ci.yml
git commit -m "ci: add smoke job (build + --help) so PRs emit a smoke check"
git push -u origin ci/smoke-job
gh pr create -R szymonrychu/tatara-memory-repo-ingester --fill
```
Expected: PR checks (incl. the new `smoke`) pass; then merge `--squash --delete-branch`.

---

## Runbook - operator-executed, GATED (run after Part A + C merge)

These are config/ops steps, not TDD tasks. Each is outward-facing; confirm before
applying.

### R1: per-repo GitHub settings (all 6 CI repos)

For each of `tatara-operator tatara-cli tatara-memory
tatara-memory-repo-ingester tatara-claude-code-wrapper tatara-chat`:

```bash
# allow auto-merge
gh api -X PATCH repos/szymonrychu/<repo> -F allow_auto_merge=true

# branch protection on main: require the 5 PR checks
gh api -X PUT repos/szymonrychu/<repo>/branches/main/protection \
  -H "Accept: application/vnd.github+json" \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": false,
    "contexts": ["secscan", "lint", "test", "build", "smoke"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null
}
JSON
```

Verify: `gh api repos/szymonrychu/<repo>/branches/main/protection/required_status_checks --jq '.contexts'`.
Do NOT apply the `smoke` context to operator/ingester until their smoke job has
merged to `main` and reported once (else their PRs block on a never-reported
check).

### R2: flip the live `tatara` Project policy

Change `mergePolicy: afterApproval` -> `autoMergeOnGreenCI` at the source of
truth (the Project manifest / values the operator chart renders in the infra
helmfile - NOT `kubectl edit`). Ship it with the operator chart bump that carries
Part A. Verify post-deploy:
`kubectl get project tatara -o jsonpath='{.spec.scm.mergePolicy}'` ->
`autoMergeOnGreenCI`.

### R3: deploy Part A

Part A merged to operator `main` triggers CI to publish
`oci://harbor.szymonrichert.pl/charts/tatara-operator:0.0.0-<sha>` + image. Bump
the operator release version in the infra helmfile to that chart version,
`helmfile diff`, MR, deploy (the same path as the in-flight ingress phase-1
deploy - fold both bumps into one operator chart bump if they land together).

---

## Self-Review

**Spec coverage:**
- SCM-native auto-merge (GitHub) -> Task A1. (GitLab) -> Task A2.
- Wired at OpenChange, gated by `autoMergeOnGreenCI` -> Task A3.
- `Closes #N` issue close -> Task A4.
- Reuse `mergePolicy` knob -> Task A3 (gate) + R2 (flip live).
- Part C smoke uniformity (operator + ingester) -> Task C1, C2.
- Per-repo allow-auto-merge + branch protection (5 checks, 6 repos) -> R1.
- Deploy ordering (settings first, then operator deploy) -> R1/R3 notes + pre-req.

**Placeholder scan:** test fake type name (`fakeWriter`) and `task.Spec.Source`
type name (`TaskSource`) are flagged in-step as names to confirm by grep, not
left as silent TODOs. No other placeholders.

**Type consistency:** `EnableAutoMerge(ctx, repoURL, token, prURL, method
string) error` identical across scm.go interface, github.go, gitlab.go, the fake,
and the controller call site. `ghMergeMethod`/`glIIDFromURL` are defined in the
same task that uses them.
