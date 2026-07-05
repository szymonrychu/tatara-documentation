# Brainstorm: no repeat-comment, prefer novelty — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the project-wide brainstorm/healthCheck agent from commenting on the same issue every cycle: flag issues the bot already commented on, steer the prompt to novel work, and hard-gate a duplicate bot comment at the egress endpoint.

**Architecture:** tatara-operator only. (1) `buildIssuesContext` scans each in-context open issue's comments via the existing token-bound `SCMReader.ListIssueComments` and appends a `[bot-engaged]` marker when the bot already commented. (2) `brainstormGoalProject` + `healthCheckGoalProject` gain a no-re-comment path keyed on that marker. (3) The `commentOnIssue` REST handler obtains a reader from the project token and returns 409 when the bot already commented (authoritative backstop, fails open on SCM read error). No cli/wrapper/agent.image change — `comment_on_issue` already shipped.

**Tech Stack:** Go (operator), controller-runtime, chi, prometheus, envtest.

## Global Constraints

- Newest stable Go pinned in `go.mod`; build/test via `mise exec -- go ...` or `mise run test`.
- KISS / no tech-debt / boy-scout adjacent fixes (CLAUDE.md hard rules 2-4).
- JSON logs via `log/slog`; log every business action at INFO with structured fields (hard rules 11-12).
- Metrics for things that can fail (hard rule 13) — reuse the existing `scmWritesTotal` vec (`SCMWrite(provider, op, result)`); no new metric.
- Detection is a **live SCM comment scan** (no persisted state). Re-comment cap is **1** (zero re-comments). `BotLogin` empty -> feature is a no-op (no scan, no gate).
- Bot identity comes from `proj.Spec.Scm.BotLogin` (e.g. `tatara-bot`). The marker string is exactly `[bot-engaged]` and MUST match between Task 1 (writer) and Task 2 (prompt reference).
- Deploy after merge: operator image via CI, then a tatara-helmfile MR bumping BOTH the operator chart version AND the pinned `image.tag`.

---

### Task 1: Flag bot-engaged issues in the brainstorm/healthCheck context

**Files:**
- Modify: `internal/controller/projectscan.go` — `buildIssuesContext` (currently `func (r *ProjectReconciler) buildIssuesContext(ctx, _ *Project, issuesBySlug, repos)` at ~line 1244) and its two callers `brainstorm()` (~line 1063) and `healthCheck()` (~line 1160).
- Test: `internal/controller/projectscan_brainstorm_dedup_test.go` (add a test + extend `goalCapturingReader`).

**Interfaces:**
- Consumes: `scm.SCMReader.ListIssueComments(ctx, owner, repo, number) ([]scm.IssueComment, error)` (exists); `scm.IssueComment.Author` (string login); `scm.OwnerRepo(url) (owner, name string, err error)`; `proj.Spec.Scm.BotLogin`.
- Produces: `buildIssuesContext(ctx context.Context, proj *tatarav1alpha1.Project, reader scm.SCMReader, issuesBySlug map[string][]scm.IssueRef, repos []tatarav1alpha1.Repository) string` — same output as today plus a trailing ` [bot-engaged]` on lines whose issue already has a comment authored by `proj.Spec.Scm.BotLogin`.

- [ ] **Step 1: Write the failing test**

Add to `internal/controller/projectscan_brainstorm_dedup_test.go`. First extend `goalCapturingReader` with a comments map and an override (place next to the existing `ListOpenIssues` override):

```go
// commentsBySlugNum is keyed "slug#number" -> comments returned for that issue.
// Add this field to the goalCapturingReader struct:
//   commentsBySlugNum map[string][]scm.IssueComment
func (g *goalCapturingReader) ListIssueComments(_ context.Context, owner, repo string, number int) ([]scm.IssueComment, error) {
	key := fmt.Sprintf("%s/%s#%d", owner, repo, number)
	return g.commentsBySlugNum[key], nil
}
```

Add the field to the struct literal `goalCapturingReader` (the `commentsBySlugNum map[string][]scm.IssueComment` line) and add `"fmt"` to the test imports if missing.

Then the test:

```go
// TestBrainstorm_BotEngagedIssueFlagged verifies that an issue the bot already
// commented on is marked [bot-engaged] in the goal, while an untouched issue is not.
func TestBrainstorm_BotEngagedIssueFlagged(t *testing.T) {
	proj, repos := seedBrainstormProject(t, "bs-botengaged", []string{"o/eng1", "o/eng2"}, 5)

	reader := &goalCapturingReader{
		issuesByRepo: map[string][]scm.IssueRef{
			"o/eng1": {
				{Repo: "o/eng1", Number: 7, Title: "improve caching layer", UpdatedAt: time.Now()},
			},
			"o/eng2": {
				{Repo: "o/eng2", Number: 2, Title: "fix login redirect", UpdatedAt: time.Now()},
			},
		},
		commentsBySlugNum: map[string][]scm.IssueComment{
			// Bot already commented on o/eng1#7 (off-limits); o/eng2#2 untouched.
			"o/eng1#7": {{Author: "tatara-bot", Body: "looking into this"}},
		},
	}

	r := newScanReconciler(reader)
	r.Metrics = obs.NewOperatorMetrics(prometheus.NewRegistry())

	act := tatarav1alpha1.BrainstormActivity{Enabled: true, MaxOpenProposals: 5}
	budget := 99
	r.brainstorm(context.Background(), proj, reader, repos, nil, act, &budget)

	tasks := listBrainstormTasks(t, "bs-botengaged")
	if len(tasks) != 1 {
		t.Fatalf("want 1 brainstorm task, got %d", len(tasks))
	}
	goal := tasks[0].Spec.Goal

	if !strings.Contains(goal, "o/eng1#7 [] improve caching layer [bot-engaged]") {
		t.Fatalf("bot-engaged issue not flagged:\n%s", goal)
	}
	if strings.Contains(goal, "fix login redirect [bot-engaged]") {
		t.Fatalf("untouched issue wrongly flagged:\n%s", goal)
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `mise exec -- go test ./internal/controller/ -run TestBrainstorm_BotEngagedIssueFlagged -v`
Expected: FAIL — compile error (`buildIssuesContext` arity / `goalCapturingReader` field) or assertion (no `[bot-engaged]` in goal).

- [ ] **Step 3: Change `buildIssuesContext` to scan comments and flag**

In `internal/controller/projectscan.go`, replace the signature and the line-building loop:

```go
func (r *ProjectReconciler) buildIssuesContext(ctx context.Context, proj *tatarav1alpha1.Project, reader scm.SCMReader, issuesBySlug map[string][]scm.IssueRef, repos []tatarav1alpha1.Repository) string {
	l := log.FromContext(ctx)
	botLogin := ""
	if proj.Spec.Scm != nil {
		botLogin = proj.Spec.Scm.BotLogin
	}
	var lines []string
	total := 0
	for i := range repos {
		owner, name, err := scm.OwnerRepo(repos[i].Spec.URL)
		if err != nil {
			continue
		}
		slug := owner + "/" + name
		issues := issuesBySlug[slug]
		for _, iss := range issues {
			if iss.IsPR {
				continue
			}
			if len(lines) >= maxIssuesContext {
				total++
				continue
			}
			total++
			labels := strings.Join(iss.Labels, ",")
			title := iss.Title
			// Collapse newlines in title for a single-line entry.
			title = strings.ReplaceAll(title, "\n", " ")
			title = strings.ReplaceAll(title, "\r", "")
			line := fmt.Sprintf("%s#%d [%s] %s", slug, iss.Number, labels, title)
			if botCommentedOnIssue(ctx, reader, owner, name, iss.Number, botLogin) {
				line += " [bot-engaged]"
			}
			lines = append(lines, line)
		}
	}
	if len(lines) == 0 {
		return ""
	}
	omitted := total - len(lines)
	result := strings.Join(lines, "\n")
	if omitted > 0 {
		result += fmt.Sprintf("\n(+%d more omitted)", omitted)
		l.Info("brainstorm: buildIssuesContext: capped issues context",
			"shown", len(lines), "omitted", omitted)
	}
	return result
}

// botCommentedOnIssue reports whether botLogin already authored a comment on the
// issue. Empty botLogin or any SCM read error -> false (best-effort flag; the
// commentOnIssue egress gate is the authoritative backstop).
func botCommentedOnIssue(ctx context.Context, reader scm.SCMReader, owner, name string, number int, botLogin string) bool {
	if botLogin == "" {
		return false
	}
	comments, err := reader.ListIssueComments(ctx, owner, name, number)
	if err != nil {
		return false
	}
	for _, c := range comments {
		if c.Author == botLogin {
			return true
		}
	}
	return false
}
```

- [ ] **Step 4: Update both callers to pass the reader**

In `brainstorm()` (~line 1063) change:

```go
issuesCtx := r.buildIssuesContext(ctx, proj, reader, issuesBySlug, sortedRepos)
```

In `healthCheck()` (~line 1160) change identically:

```go
issuesCtx := r.buildIssuesContext(ctx, proj, reader, issuesBySlug, sortedRepos)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `mise exec -- go test ./internal/controller/ -run TestBrainstorm_BotEngagedIssueFlagged -v`
Expected: PASS.

- [ ] **Step 6: Run the existing dedup tests (no regression)**

Run: `mise exec -- go test ./internal/controller/ -run 'TestBrainstorm|TestBrainstormGoalProject' -v`
Expected: PASS (existing cap/context tests use a reader whose `ListIssueComments` returns nil -> no flags).

- [ ] **Step 7: Commit**

```bash
git add internal/controller/projectscan.go internal/controller/projectscan_brainstorm_dedup_test.go
git commit -m "feat: flag bot-engaged issues in brainstorm/healthCheck context"
```

---

### Task 2: Prompt — never re-comment a bot-engaged issue, prefer novelty

**Files:**
- Modify: `internal/controller/projectscan.go` — `brainstormGoalProject` (~line 1180) and `healthCheckGoalProject` (~line 1208).
- Test: `internal/controller/projectscan_brainstorm_dedup_test.go`.

**Interfaces:**
- Consumes: the `[bot-engaged]` marker emitted by Task 1.
- Produces: goal strings whose DEDUP RULE removes `[bot-engaged]` issues from the comment-eligible set and instructs the agent to prefer novel work. Existing keywords `duplicate`, `comment_on_issue`, `propose_issue` remain present.

- [ ] **Step 1: Write the failing test**

Add to `internal/controller/projectscan_brainstorm_dedup_test.go`:

```go
// TestGoalProjects_NoReCommentInstruction verifies both project goals tell the
// agent not to re-comment a [bot-engaged] issue and to prefer new improvements.
func TestGoalProjects_NoReCommentInstruction(t *testing.T) {
	slugs := []string{"o/alpha"}
	ctx := "o/alpha#1 [] Fix login bug [bot-engaged]"
	for _, g := range []string{
		brainstormGoalProject(slugs, ctx),
		healthCheckGoalProject(slugs, ctx),
	} {
		if !strings.Contains(g, "[bot-engaged]") {
			t.Fatalf("goal does not reference the bot-engaged marker:\n%s", g)
		}
		// Must instruct: do not comment again on a bot-engaged issue.
		if !strings.Contains(g, "do NOT comment again") {
			t.Fatalf("goal missing no-re-comment instruction:\n%s", g)
		}
		// Must still embed the context and keep the three action verbs.
		for _, kw := range []string{"comment_on_issue", "propose_issue", ctx} {
			if !strings.Contains(g, kw) {
				t.Fatalf("goal missing %q:\n%s", kw, g)
			}
		}
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `mise exec -- go test ./internal/controller/ -run TestGoalProjects_NoReCommentInstruction -v`
Expected: FAIL with missing "do NOT comment again".

- [ ] **Step 3: Edit `brainstormGoalProject`**

In `internal/controller/projectscan.go`, change path 2 of the DEDUP RULE block (the `comment_on_issue` clause) and add the bot-engaged guard. Replace the path-2 line:

```go
		"2. If the best idea is a sub-aspect or connecting improvement TO an existing issue: " +
		"call comment_on_issue(repo, number, body) on that issue. Do NOT call propose_issue.\n" +
```

with:

```go
		"2. If the best idea is a sub-aspect or connecting improvement TO an existing issue " +
		"that is NOT marked [bot-engaged]: call comment_on_issue(repo, number, body) on that issue. " +
		"Do NOT call propose_issue.\n" +
		"   An issue marked [bot-engaged] already has your comment - do NOT comment again on it. " +
		"Prefer a NEW improvement instead: a genuinely novel standalone idea (path 3, in ANY repo or " +
		"project-wide), or a comment on a DIFFERENT issue that is not [bot-engaged]. Never comment " +
		"twice on the same issue.\n" +
```

- [ ] **Step 4: Edit `healthCheckGoalProject`**

Apply the same replacement to the path-2 line in `healthCheckGoalProject` (it shares the identical DEDUP RULE wording; replace "idea" with "finding" to match that function's phrasing):

```go
		"2. If the best finding is a sub-aspect or connecting improvement TO an existing issue " +
		"that is NOT marked [bot-engaged]: call comment_on_issue(repo, number, body) on that issue. " +
		"Do NOT call propose_issue.\n" +
		"   An issue marked [bot-engaged] already has your comment - do NOT comment again on it. " +
		"Prefer a NEW finding instead: a genuinely novel standalone issue (path 3, in ANY repo or " +
		"project-wide), or a comment on a DIFFERENT issue that is not [bot-engaged]. Never comment " +
		"twice on the same issue.\n" +
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `mise exec -- go test ./internal/controller/ -run TestGoalProjects_NoReCommentInstruction -v`
Expected: PASS.

- [ ] **Step 6: Run the full dedup + goal test set (no regression)**

Run: `mise exec -- go test ./internal/controller/ -run 'TestBrainstorm|TestGoalProjects|TestBrainstormGoalProject' -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add internal/controller/projectscan.go internal/controller/projectscan_brainstorm_dedup_test.go
git commit -m "feat: brainstorm/healthCheck prompt - never re-comment bot-engaged issues"
```

---

### Task 3: Egress hard-gate — 409 on duplicate bot comment

**Files:**
- Modify: `internal/restapi/server.go` — add `ReaderFor` to `Config` and `Server`, set it in `NewServer`.
- Modify: `internal/restapi/handlers.go` — `commentOnIssue` (~line 469): add the duplicate-comment gate before `writer.Comment`.
- Modify: `cmd/manager/wire.go` (~line 99) — wire `ReaderFor` into the restapi `Config`.
- Test: `internal/restapi/issue_comment_test.go` — add a fake reader + 409 test and a not-blocked test.

**Interfaces:**
- Consumes: `scm.SCMReader.ListIssueComments(ctx, owner, repo, number)`; existing `scm.ReaderByProvider(provider, token) (scm.SCMReader, error)`; `proj.Spec.Scm.BotLogin`; existing `s.scmFor`, `s.metrics.SCMWrite(provider, op, result)`.
- Produces: `Config.ReaderFor func(provider, token string) (scm.SCMReader, error)` and `Server.readerFor` of the same type; `commentOnIssue` returns 409 (`bot already commented...`) when the bot already authored a comment on the target issue.

- [ ] **Step 1: Write the failing test**

Add to `internal/restapi/issue_comment_test.go`. First a fake reader (satisfies `scm.SCMReader`) and a builder that injects both factories:

```go
// fakeReader returns canned issue comments for the gate test.
type fakeReader struct {
	comments []scm.IssueComment
}

func (f *fakeReader) ListOpenPRs(_ context.Context, _, _ string) ([]scm.PRRef, error) {
	return nil, nil
}
func (f *fakeReader) ListOpenIssues(_ context.Context, _, _ string) ([]scm.IssueRef, error) {
	return nil, nil
}
func (f *fakeReader) ListBoardItems(_ context.Context, _ scm.BoardRef) ([]scm.BoardItem, error) {
	return nil, nil
}
func (f *fakeReader) GetCommitCIStatus(_ context.Context, _, _, _ string) (string, error) {
	return "", nil
}
func (f *fakeReader) ListIssueComments(_ context.Context, _, _ string, _ int) ([]scm.IssueComment, error) {
	return f.comments, nil
}
func (f *fakeReader) GetIssue(_ context.Context, _, _ string, _ int) (scm.IssueContent, error) {
	return scm.IssueContent{}, nil
}

// buildRouterWithReader injects both an SCMFor writer and a ReaderFor reader.
func buildRouterWithReader(t *testing.T, writer scm.SCMWriter, reader scm.SCMReader, objs ...client.Object) *chi.Mux {
	t.Helper()
	scheme := runtime.NewScheme()
	require.NoError(t, tatarav1alpha1.AddToScheme(scheme))
	require.NoError(t, corev1.AddToScheme(scheme))
	fc := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(objs...).
		WithStatusSubresource(&tatarav1alpha1.Project{}, &tatarav1alpha1.Repository{},
			&tatarav1alpha1.Task{}, &tatarav1alpha1.Subtask{}).
		Build()
	s := restapi.NewServer(restapi.Config{
		Client:    fc,
		Namespace: "tatara",
		SCMFor: func(_ string) (scm.SCMWriter, error) {
			return writer, nil
		},
		ReaderFor: func(_, _ string) (scm.SCMReader, error) {
			return reader, nil
		},
	})
	r := chi.NewRouter()
	s.Mount(r, nil)
	return r
}

// projectWithBot is projectWithSCM plus a BotLogin (gate is a no-op without it).
func projectWithBot(name, secretName, bot string) *tatarav1alpha1.Project {
	p := projectWithSCM(name, secretName)
	p.Spec.Scm.BotLogin = bot
	return p
}
```

Then the two tests:

```go
func TestCommentOnIssue_BlockedWhenBotAlreadyCommented(t *testing.T) {
	writer := &fakeWriter{}
	reader := &fakeReader{comments: []scm.IssueComment{
		{Author: "someone", Body: "first"},
		{Author: "tatara-bot", Body: "already weighed in"},
	}}
	proj := projectWithBot("projblk", "projblk-scm", "tatara-bot")
	secret := scmSecret("projblk-scm", "tok")
	repo := repoForProject("projblk-repo", "projblk", "https://github.com/o/r.git")

	r := buildRouterWithReader(t, writer, reader, proj, secret, repo)

	body := strings.NewReader(`{"repo":"o/r","number":9,"body":"again"}`)
	req := httptest.NewRequest(http.MethodPost, "/projects/projblk/issue-comment", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	require.Equal(t, http.StatusConflict, w.Code, "body: %s", w.Body.String())
	writer.mu.Lock()
	defer writer.mu.Unlock()
	require.Len(t, writer.comments, 0, "must not post when already commented")
}

func TestCommentOnIssue_PostsWhenBotNotYetCommented(t *testing.T) {
	writer := &fakeWriter{}
	reader := &fakeReader{comments: []scm.IssueComment{
		{Author: "someone", Body: "first"},
	}}
	proj := projectWithBot("projok", "projok-scm", "tatara-bot")
	secret := scmSecret("projok-scm", "tok")
	repo := repoForProject("projok-repo", "projok", "https://github.com/o/r.git")

	r := buildRouterWithReader(t, writer, reader, proj, secret, repo)

	body := strings.NewReader(`{"repo":"o/r","number":9,"body":"my take"}`)
	req := httptest.NewRequest(http.MethodPost, "/projects/projok/issue-comment", body)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	require.Equal(t, http.StatusOK, w.Code, "body: %s", w.Body.String())
	writer.mu.Lock()
	defer writer.mu.Unlock()
	require.Len(t, writer.comments, 1)
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `mise exec -- go test ./internal/restapi/ -run 'TestCommentOnIssue_BlockedWhenBotAlreadyCommented|TestCommentOnIssue_PostsWhenBotNotYetCommented' -v`
Expected: FAIL — `Config` has no `ReaderFor` field (compile error), and the block test returns 200 once it compiles.

- [ ] **Step 3: Add `ReaderFor` to `Config` and `Server`**

In `internal/restapi/server.go`, add to `Config` (after `SCMFor`):

```go
	// ReaderFor returns a token-bound SCMReader for the given provider name.
	// Used by the issue-comment gate to detect an existing bot comment.
	// When nil, the gate is skipped (post proceeds).
	ReaderFor func(provider, token string) (scm.SCMReader, error)
```

Add to `Server`:

```go
	readerFor func(provider, token string) (scm.SCMReader, error)
```

And set it in `NewServer`:

```go
	return &Server{c: cfg.Client, ns: cfg.Namespace, scmFor: cfg.SCMFor, readerFor: cfg.ReaderFor, log: l, metrics: cfg.Metrics}
```

- [ ] **Step 4: Add the gate to `commentOnIssue`**

In `internal/restapi/handlers.go`, insert the gate after the token is resolved (after the `token == ""` check at ~line 562) and before `start := time.Now()` (~line 564):

```go
	// Hard-gate (cap 1): refuse a second bot comment on the same issue. Best-effort -
	// empty BotLogin, no reader factory, or an SCM read error all fall open (post proceeds);
	// the brainstorm prompt is the first line of defence, this is the authoritative backstop.
	botLogin := ""
	if proj.Spec.Scm != nil {
		botLogin = proj.Spec.Scm.BotLogin
	}
	if botLogin != "" && s.readerFor != nil {
		if reader, rerr := s.readerFor(provider, token); rerr == nil {
			if owner, name, oerr := scm.OwnerRepo(matchedRepoURL); oerr == nil {
				if comments, cerr := reader.ListIssueComments(r.Context(), owner, name, req.Number); cerr == nil {
					for _, cm := range comments {
						if cm.Author == botLogin {
							if s.metrics != nil {
								s.metrics.SCMWrite(provider, "comment", "blocked")
							}
							s.log.InfoContext(r.Context(), "restapi: commentOnIssue blocked",
								append(reqLogFields(r),
									"action", "scm_issue_comment_blocked",
									"reason", "already_commented",
									"project", projName,
									"repo", req.Repo,
									"number", req.Number)...)
							writeError(w, http.StatusConflict, "bot already commented on this issue; pick another action")
							return
						}
					}
				}
			}
		}
	}
```

- [ ] **Step 5: Run the gate tests to verify they pass**

Run: `mise exec -- go test ./internal/restapi/ -run 'TestCommentOnIssue_BlockedWhenBotAlreadyCommented|TestCommentOnIssue_PostsWhenBotNotYetCommented' -v`
Expected: PASS.

- [ ] **Step 6: Run the full issue-comment suite (no regression)**

Run: `mise exec -- go test ./internal/restapi/ -run TestCommentOnIssue -v`
Expected: PASS — existing tests use `projectWithSCM` (no BotLogin) and `buildRouterWithSCM` (no ReaderFor), so the gate is skipped and behavior is unchanged.

- [ ] **Step 7: Wire `ReaderFor` into the manager**

In `cmd/manager/wire.go`, add to the `restapi.Config` literal (~line 99, after `SCMFor`):

```go
		ReaderFor: func(provider, token string) (scm.SCMReader, error) {
			return scm.ReaderByProvider(provider, token)
		},
```

- [ ] **Step 8: Build + commit**

Run: `mise exec -- go build ./...`
Expected: success.

```bash
git add internal/restapi/server.go internal/restapi/handlers.go internal/restapi/issue_comment_test.go cmd/manager/wire.go
git commit -m "feat: 409 the issue-comment endpoint when bot already commented"
```

---

### Task 4: Full verification

- [ ] **Step 1: Lint**

Run: `mise run lint` (or `mise exec -- golangci-lint run`)
Expected: clean.

- [ ] **Step 2: Full envtest controller + restapi suites**

Run: `mise run test` (sets `KUBEBUILDER_ASSETS` via setup-envtest 1.33.0), or:
`mise exec -- go test ./internal/controller/... ./internal/restapi/...`
Expected: all green.

- [ ] **Step 3: Self-check the marker contract**

Confirm the exact string `[bot-engaged]` appears in both the writer (`projectscan.go` `buildIssuesContext`) and the two goal builders. Grep:
Run: `grep -rn "\[bot-engaged\]" internal/controller/projectscan.go`
Expected: 3+ hits (1 writer, brainstorm goal, healthCheck goal).

- [ ] **Step 4: requesting-code-review, then pre-commit, then deploy**

Per CLAUDE.md: run `superpowers:requesting-code-review`, fix critical/high, `pre-commit run --all-files`, then the operator deploy is a tatara-helmfile MR bumping the operator chart version AND `image.tag` (operator-deploy memory). No cli/wrapper/agent.image change.

---

## Self-Review

**Spec coverage:**
- Part A context awareness (live scan + `[bot-engaged]` flag, cap honored, empty BotLogin no-op) -> Task 1. The 60-issue cap is unchanged (scan only runs for in-context issues; the existing `(+N omitted)` path is untouched).
- Part A prompt (no-re-comment, prefer novelty, applies to brainstorm AND the shared healthCheck path) -> Task 2.
- Part B egress hard-gate (reader from project token, 409, log `scm_issue_comment_blocked`, counter, BotLogin empty -> skip) -> Task 3.
- Deploy (operator-only, chart + image.tag) -> Task 4 Step 4.
- Testing matrix (projectscan flag present/absent + BotLogin empty implicit via existing nil-comment readers; handler 409 vs 200; full suite) -> Tasks 1/2/3/4.

**Deviations from spec (intentional, KISS/DRY):**
- Metric reuses `SCMWrite(provider,"comment","blocked")` rather than a new `brainstorm_recomment_blocked_total` — no obs change, consistent with the existing `ok`/`error` result label.
- The gate fails OPEN on reader-factory / OwnerRepo / ListIssueComments errors (logs nothing on the read-error path beyond proceeding) — a transient SCM blip must not block legitimate first comments; the prompt covers the common case and the systematic re-comment loop is still caught when the read succeeds. Spec called this TOCTOU-ignorable; this extends the same best-effort stance to read errors.
- healthCheck is included alongside brainstorm (boy-scout, hard rule 3): it shares `buildIssuesContext` and the identical `comment_on_issue` dedup path, so excluding it would leave the same re-comment loop live there.

**Placeholder scan:** none — every step carries full code/commands.

**Type consistency:** `buildIssuesContext(ctx, proj, reader, issuesBySlug, repos)` signature matches both call sites (Task 1 Step 4); `botCommentedOnIssue` helper name used once; `ReaderFor`/`readerFor` field names consistent across Config/Server/wire; marker string `[bot-engaged]` identical in writer and both prompts.
