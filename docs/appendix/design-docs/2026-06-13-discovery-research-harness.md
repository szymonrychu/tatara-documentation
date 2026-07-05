# Discovery-phase research harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship two wrapper agent skills (`tatara-deep-research`, `tatara-research-followup`) plus the MCP/operator plumbing they need, so the autonomous loop's discovery phase does deep cross-platform research and keeps research-issue conversations alive until a human approves - all through the `tatara` MCP server, issues staying in discovery.

**Architecture:** Three repos, three layers. Wrapper ships the two baked `SKILL.md` workflows. tatara-cli adds a task-scoped `comment` MCP tool. tatara-operator adds the `POST /tasks/{t}/comment` REST handler + a `Task.Status.PendingComments` queue drained to SCM on reconcile, and nudges the brainstorm + triage turn prompts to invoke the skills by name.

**Tech Stack:** Go (newest stable per repo go.mod), controller-runtime + chi (operator), cobra + stdio MCP (cli), Claude Code SKILL.md trees (wrapper). Table-driven Go tests. Conventional commits.

---

## Spec

`docs/superpowers/specs/2026-06-13-discovery-research-harness-design.md`

## File structure (what changes)

tatara-claude-code-wrapper (worktree off main):
- Create: `templates/skills/tatara-deep-research/SKILL.md`
- Create: `templates/skills/tatara-research-followup/SKILL.md`
- Create/Modify: a Go test asserting both skills are present + valid frontmatter
- Modify (serial tail, after cli tagged): `Dockerfile` `TATARA_CLI_VERSION` pin + the build-time tool-guard list (add `comment`)

tatara-cli (worktree off main):
- Modify: `internal/mcp/tools.go` - add `comment` to `OperatorTools()`; fix stale `propose_issue` description
- Modify: `internal/mcp/tools_test.go` - test `comment` registration + build mapping

tatara-operator (worktree off main):
- Modify: `api/v1alpha1/task_types.go` - add `PendingComments []string` to `TaskStatus`
- Regenerate: `make manifests generate` (CRD yaml + deepcopy) -> `config/crd/...` and chart `crds/`
- Modify: `internal/restapi/server.go` - route `POST /tasks/{t}/comment`
- Modify: `internal/restapi/handlers.go` - `postComment` handler + `issueCommentReq`
- Modify: `internal/restapi/*_test.go` - handler test
- Modify: `internal/controller/lifecycle.go` - drain `PendingComments` in `reconcileLifecycle`
- Modify: `internal/controller/lifecycle_*_test.go` - drain test
- Modify: `internal/controller/turnloop.go:58` - nudge `lifecycleTriageText` to invoke `tatara-research-followup`
- Modify: `internal/controller/projectscan.go:772` - nudge brainstorm goal to invoke `tatara-deep-research`
- Modify: `internal/controller/*_test.go` - prompt-nudge assertions

## Cross-repo ordering

- Lanes A (wrapper skills A1-A3), B (cli), C (operator) are INDEPENDENT - implement in parallel worktrees.
- Serial tail A4: after cli `comment` merges + is tagged, bump the wrapper Dockerfile cli pin + guard, then merge wrapper.
- Skill names referenced by operator (C5/C6) must equal the wrapper skill dir names exactly: `tatara-deep-research`, `tatara-research-followup`.

---

## LANE A - tatara-claude-code-wrapper

### Task A1: `tatara-deep-research` skill

**Files:**
- Create: `templates/skills/tatara-deep-research/SKILL.md`

- [ ] **Step 1: Create the skill file** with exactly this content:

```markdown
---
name: tatara-deep-research
description: Use on an autonomous platform-research turn (the brainstorm task kind) to discover ONE high-leverage improvement for the tatara platform and open a discovery-phase issue via the propose_issue MCP tool. Researches deeply across the whole platform using the tatara-memory knowledge/code graph plus the on-disk repo, scores leverage against platform and per-repo goals, and files a single well-formed issue that stays in discovery (never self-implemented).
---

# tatara deep research

Discover and propose ONE high-leverage improvement issue per run. All
input and output go through the `tatara` MCP server. You never use git or
gh; you never open an issue yourself - `propose_issue` does that under the
bot identity.

## Hard constraints

- ONE issue per run. The brainstorm task completes after a single proposal.
- Stay in discovery. Do NOT request implementation. Embed the literal
  marker `<!-- tatara-authored -->` in the issue body and never set a
  trigger label - the operator holds tatara-authored ideas in
  conversation until a human approves.
- Every proposal must respect the platform's 14 hard rules (read the
  on-disk `CLAUDE.md`), or the loop that later implements it will reject
  it. KISS; no tech debt; charts cluster-agnostic; conventional commits;
  newest stable Go; JSON slog + INFO business logging + /metrics.
- Communication only via `tatara` MCP tools.

## Workflow

Create a TodoWrite item per numbered step.

1. **Orient on goals.** Read the on-disk `ROADMAP.md`, `MEMORY.md`, and
   `CLAUDE.md` of the task's repo (the platform goal, the repo charter,
   the hard rules). Then use the memory MCP tools for the wider picture:
   `query` (mode global or hybrid) for "tatara platform goal" and "open
   roadmap themes"; `describe` for an overview of the target repo.

2. **Map current state.** Use the code-graph tools to find where the
   system is fragile or under-optimized, repo-scoped where useful:
   `code_stats`, `code_important` (high-PageRank entities = load-bearing
   code), `code_communities` (subsystem clustering), `code_bridges`
   (coupling/risk), and `code_cross_repo` (cross-repo edges - the pod has
   only one repo on disk, so cross-repo understanding MUST come from the
   graph). Then READ the actual on-disk code for the strongest candidate
   area to confirm what the graph suggested.

3. **Score leverage.** Rank candidate improvements by impact in this
   order: (a) reliability/observability of the LIVE autonomous loop
   (it is dogfooding in production and surfaces real bugs); (b) un-built
   but planned loop features; (c) the Phase-9 SOTA backlog; (d) deploy
   debt. Respect gates: do NOT propose downstream memory ranking/reranker
   work before the memory retrieval-quality eval harness exists. Pick the
   single highest-leverage, well-scoped item.

4. **Dedup.** Call `task_list` and review the repo's open issues/tasks to
   avoid duplicating an existing proposal or the operator's own brainstorm
   output. If a similar idea is already open, pick the next-best candidate
   instead.

5. **Compose ONE proposal.** Write:
   - Title: imperative, specific (e.g. "Add per-item ingest timeout to the
     memory ingest worker").
   - Body: Problem (what hurts, why it matters to the platform/repo goal);
     Evidence (`file:line` references and concrete graph findings from
     steps 1-2); Proposed approach (KISS, respecting the hard rules);
     Scope boundary (what is in and explicitly out); Open questions for
     the maintainer. Append the literal line `<!-- tatara-authored -->`.

6. **File it.** Call `propose_issue` with `title`, `body`, `kind`
   (`improvement` or `bug`), and `repo` (the repo slug; `project` defaults
   from env). Do not set any trigger/approval label. Then stop - the
   brainstorm task is complete.

## Anti-patterns

- Proposing more than one issue in a run.
- Proposing vague "improve X" issues with no `file:line` evidence.
- Requesting implementation / setting a trigger label (breaks discovery).
- Proposing memory ranking work before the eval-harness gate.
- Reading only the on-disk repo and ignoring the cross-repo graph.
```

- [ ] **Step 2: Verify frontmatter parses** (covered by Task A3 test).

- [ ] **Step 3: Commit**

```bash
git add templates/skills/tatara-deep-research/SKILL.md
git commit -m "feat: add tatara-deep-research discovery skill"
```

### Task A2: `tatara-research-followup` skill

**Files:**
- Create: `templates/skills/tatara-research-followup/SKILL.md`

- [ ] **Step 1: Create the skill file** with exactly this content:

```markdown
---
name: tatara-research-followup
description: Use when continuing an existing discovery/research issue conversation on an issueLifecycle Triage or Conversation turn. Read the issue thread and task state, research the gaps with the tatara-memory graph and on-disk code, post substantive design comments via the comment MCP tool, refine the proposal into a concrete design, and push toward human approval - never self-approving. Idle quietly when there is nothing new to add.
---

# tatara research follow-up

Keep a discovery-phase issue conversation alive and move it toward an
approvable design. All input and output go through the `tatara` MCP
server. You never use git or gh.

## Hard constraints

- NEVER self-approve. If THIS issue is tatara-authored, only a human's
  approval comment may lead to implementation - you only discuss and
  refine. End the turn with `issue_outcome(discuss)`, never
  `issue_outcome(implement)` on an unapproved tatara-authored issue.
- Silence over noise. If there is no human input and nothing genuinely
  new to add, post nothing and let the conversation idle.
- One focused turn. Communication only via `tatara` MCP tools.

## Workflow

Create a TodoWrite item per numbered step.

1. **Load context.** Call `task_get` (task=env `TATARA_TASK`) for the task
   status and lifecycle state. Read the issue body and the full comment
   thread (the turn prompt includes the thread). Extract: open questions,
   maintainer asks, unresolved design decisions, and whether a human has
   engaged.

2. **Research the gaps.** Use the memory MCP tools (`query`, `describe`,
   and the `code_*` family incl. `code_cross_repo`) plus the on-disk code
   to answer the specific questions raised and to deepen any thin part of
   the proposal. The pod has one repo on disk; use the graph for
   cross-repo facts.

3. **Respond in-thread** with the `comment` MCP tool (task=env
   `TATARA_TASK`, body=...). Post focused comments, not one wall of text:
   - Answer each maintainer question with evidence (`file:line`, graph
     findings).
   - Refine the proposal into a concrete design: architecture,
     components, data flow, error handling, testing, plus an
     implementation outline.
   - Surface remaining decisions for the maintainer.

4. **Drive to approval.** When the design is converged AND a human has
   engaged in the thread, post a short summary of the agreed design and
   explicitly ask the maintainer for the approval signal (an approval
   comment / the approval label). Do not approve it yourself.

5. **Idle discipline.** If nothing new is warranted, do not comment.

6. **Close the turn.** Call `issue_outcome` with action `discuss` (supply
   a one-line status as `comment`) to hold the issue in Conversation. Use
   action `close` ONLY if the idea is clearly dead AND a human concurred
   in the thread. You MUST call `issue_outcome` before finishing.

## Anti-patterns

- Calling `issue_outcome(implement)` on a tatara-authored issue without a
  human approval comment.
- Posting one giant comment instead of focused, answerable ones.
- Commenting with no new research when the thread is waiting on the human.
- Making code changes or opening PRs in this turn.
```

- [ ] **Step 2: Commit**

```bash
git add templates/skills/tatara-research-followup/SKILL.md
git commit -m "feat: add tatara-research-followup discovery skill"
```

### Task A3: Skill-presence test

**Files:**
- Test: `internal/bootstrap/skills_test.go` (extend if it exists, else create)

- [ ] **Step 1: Write the failing test.** First check for an existing skills test:
  `grep -rn "templates/skills\|installSkills\|func Test" internal/bootstrap/skills_test.go`.
  If none exists, create `internal/bootstrap/skills_test.go`:

```go
package bootstrap

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDiscoverySkillsPresentAndValid(t *testing.T) {
	root := "../../templates/skills"
	for _, name := range []string{"tatara-deep-research", "tatara-research-followup"} {
		path := filepath.Join(root, name, "SKILL.md")
		b, err := os.ReadFile(path)
		if err != nil {
			t.Fatalf("read %s: %v", path, err)
		}
		s := string(b)
		if !strings.HasPrefix(s, "---\n") {
			t.Fatalf("%s: missing YAML frontmatter", name)
		}
		end := strings.Index(s[4:], "\n---")
		if end < 0 {
			t.Fatalf("%s: unterminated frontmatter", name)
		}
		fm := s[4 : 4+end]
		if !strings.Contains(fm, "name: "+name) {
			t.Fatalf("%s: frontmatter name does not match dir", name)
		}
		if !strings.Contains(fm, "description:") {
			t.Fatalf("%s: frontmatter missing description", name)
		}
		body := s[4+end+4:]
		if len(strings.TrimSpace(body)) == 0 {
			t.Fatalf("%s: empty body", name)
		}
	}
}
```

- [ ] **Step 2: Run it - expect PASS** (the SKILL.md files from A1/A2 exist).

Run: `cd tatara-claude-code-wrapper && go test ./internal/bootstrap/ -run TestDiscoverySkills -v`
Expected: PASS. (If it fails on the relative path, adjust `root` to match the test's package dir.)

- [ ] **Step 3: Commit**

```bash
git add internal/bootstrap/skills_test.go
git commit -m "test: assert discovery skills present with valid frontmatter"
```

### Task A4 (SERIAL TAIL - after Lane B cli is merged + tagged): bump cli pin + guard

**Files:**
- Modify: `Dockerfile`

- [ ] **Step 1:** Find the cli pin + the build-time tool guard:
  `grep -n "TATARA_CLI_VERSION\|propose_issue\|review_verdict\|pr_outcome\|issue_outcome" Dockerfile`

- [ ] **Step 2:** Set `TATARA_CLI_VERSION` to the new cli tag from Lane B. In the guard line/stage that asserts the operator tools exist, add `comment` to the checked list (same form as the existing `propose_issue`/`issue_outcome` entries).

- [ ] **Step 3: Build the guard stage locally to verify the pinned cli exposes `comment`.**
  Run: `docker build --target <guard-stage-name> .` (use the stage name found in step 1; the wrapper map calls it the cli test-guard stage). Expected: build succeeds (guard passes).

- [ ] **Step 4: Commit**

```bash
git add Dockerfile
git commit -m "chore: bump tatara-cli pin to <tag> and guard the comment tool"
```

---

## LANE B - tatara-cli

### Task B1: `comment` MCP tool (TDD)

**Files:**
- Modify: `internal/mcp/tools.go` (inside `OperatorTools()`, after the `issue_outcome` entry ~line 510)
- Test: `internal/mcp/tools_test.go`

- [ ] **Step 1: Write the failing test.** Mirror the existing operator-tool tests in `tools_test.go` (find one: `grep -n "issue_outcome\|propose_issue\|func Test" internal/mcp/tools_test.go`). Add:

```go
func TestCommentToolBuildsTaskScopedPost(t *testing.T) {
	t.Setenv("TATARA_TASK", "task-xyz")
	var tool Tool
	for _, x := range OperatorTools() {
		if x.Name == "comment" {
			tool = x
		}
	}
	if tool.Name != "comment" {
		t.Fatal("comment tool not registered in OperatorTools")
	}
	if tool.Target != TargetOperator {
		t.Fatalf("comment tool target = %v, want TargetOperator", tool.Target)
	}
	method, path, body, err := tool.Build(map[string]any{"body": "design note"})
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	if method != http.MethodPost {
		t.Fatalf("method = %s, want POST", method)
	}
	if path != "/tasks/task-xyz/comment" {
		t.Fatalf("path = %s, want /tasks/task-xyz/comment", path)
	}
	m, ok := body.(map[string]any)
	if !ok || m["body"] != "design note" {
		t.Fatalf("body = %#v, want {body: design note}", body)
	}
}

func TestCommentToolRequiresBody(t *testing.T) {
	t.Setenv("TATARA_TASK", "task-xyz")
	var tool Tool
	for _, x := range OperatorTools() {
		if x.Name == "comment" {
			tool = x
		}
	}
	if _, _, _, err := tool.Build(map[string]any{}); err == nil {
		t.Fatal("expected error when body missing")
	}
}
```
Ensure `net/http` is imported in the test file (it is used by sibling tests).

- [ ] **Step 2: Run - expect FAIL** (`comment tool not registered`).
  Run: `cd tatara-cli && go test ./internal/mcp/ -run TestCommentTool -v`
  Expected: FAIL.

- [ ] **Step 3: Implement.** In `internal/mcp/tools.go`, add this entry to the `OperatorTools()` return slice, immediately after the `issue_outcome` entry (before the closing `}`):

```go
		op("comment", "Post a free-form comment on the current task's linked issue (answer maintainer questions, post design notes). The operator posts it under the bot identity on the next reconcile and does NOT change the issue's lifecycle state. Use this to keep a discovery conversation alive; use issue_outcome to set the outcome.",
			`{"type":"object","properties":{"task":{"type":"string"},"body":{"type":"string"}},"required":["body"]}`,
			func(a map[string]any) (string, string, any, error) {
				tk := argOrEnv(a, "task", "TATARA_TASK")
				if tk == "" {
					return "", "", nil, fmt.Errorf("task required")
				}
				if argString(a, "body") == "" {
					return "", "", nil, fmt.Errorf("body required")
				}
				body := map[string]any{"body": a["body"]}
				return http.MethodPost, "/tasks/" + url.PathEscape(tk) + "/comment", body, nil
			}),
```
Update the `OperatorTools` doc comment count from "13" to "14" (line ~284).

- [ ] **Step 4: Run - expect PASS.**
  Run: `cd tatara-cli && go test ./internal/mcp/ -run TestCommentTool -v`
  Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/mcp/tools.go internal/mcp/tools_test.go
git commit -m "feat: add task-scoped comment MCP tool"
```

### Task B2: Fix stale propose_issue description (boy-scout)

**Files:**
- Modify: `internal/mcp/tools.go:390`

- [ ] **Step 1:** Replace the `propose_issue` description string. Change:
  `"Propose a new SCM issue for a deferred bug or improvement; created behind the awaiting-approval label until a human approves."`
  to:
  `"Propose a new SCM issue (bug or improvement). The operator opens it under the bot identity as an idea-labelled discovery issue; it stays in discussion until a human approves. Embed <!-- tatara-authored --> in the body to keep it in discovery."`

- [ ] **Step 2: Run the package tests - expect PASS** (no behavior change).
  Run: `cd tatara-cli && go test ./internal/mcp/ -v`
  Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add internal/mcp/tools.go
git commit -m "docs: correct stale propose_issue tool description"
```

### Task B3: Release a new cli tag (after B1/B2 reviewed + merged to main)

- [ ] After merge to main + green pipeline, tag the next version (check latest: `git tag --sort=-v:refname | head -1`), e.g. `git tag vX.Y.Z && git push origin vX.Y.Z`. Record the tag for Lane A Task A4.

---

## LANE C - tatara-operator

### Task C1: Add `PendingComments` to TaskStatus + regenerate

**Files:**
- Modify: `api/v1alpha1/task_types.go` (`TaskStatus`, ~line 101)

- [ ] **Step 1:** Add the field to the `TaskStatus` struct (near `IssueOutcome`):

```go
	// PendingComments are free-form comments queued by the agent via the
	// comment MCP tool, posted to the task's linked issue on the next
	// reconcile and then cleared. Does not change the lifecycle state.
	PendingComments []string `json:"pendingComments,omitempty"`
```

- [ ] **Step 2: Regenerate CRDs + deepcopy.**
  Run: `cd tatara-operator && make manifests generate`
  Expected: `config/crd/...` Task CRD gains `pendingComments`; `zz_generated.deepcopy.go` updates. If the chart vendors CRDs (`charts/*/crds/`), copy the regenerated CRD there (check `grep -rn "pendingComments\|issueOutcome" charts/*/crds/ config/crd/`).

- [ ] **Step 3: Build - expect PASS.**
  Run: `cd tatara-operator && go build ./...`
  Expected: success.

- [ ] **Step 4: Commit**

```bash
git add api/v1alpha1/task_types.go config/crd charts zz_generated.deepcopy.go
git commit -m "feat: add Task.Status.PendingComments queue"
```

### Task C2: `POST /tasks/{t}/comment` REST handler (TDD)

**Files:**
- Modify: `internal/restapi/handlers.go` (after `issueOutcome`, ~line 380)
- Modify: `internal/restapi/server.go` (route, after line 56)
- Test: the restapi handler test file (find: `grep -rln "issueOutcome\|func Test" internal/restapi/*_test.go`)

- [ ] **Step 1: Write the failing test.** Mirror the existing `issueOutcome` handler test. Add a test that POSTs `{"body":"hello"}` to `/tasks/{t}/comment` for a Task with `Spec.Source.IssueRef` set, asserts 200, and asserts the Task's `Status.PendingComments` contains `"hello"`; plus a 400 case for empty body and a 409 for a Task with no Source. Use the same test harness (fake client + chi router) the `issueOutcome` test uses. Example assertion core:

```go
func TestPostComment_QueuesComment(t *testing.T) {
	task := newTestTask("t1") // helper used by sibling tests; Spec.Kind issueLifecycle
	task.Spec.Source = &tatarav1alpha1.TaskSource{IssueRef: "owner/repo#5"}
	srv, c := newTestServer(t, task) // mirror the issueOutcome test setup
	rr := doJSON(t, srv, http.MethodPost, "/tasks/t1/comment", map[string]any{"body": "hello"})
	if rr.Code != http.StatusOK {
		t.Fatalf("code = %d, want 200; body=%s", rr.Code, rr.Body.String())
	}
	var got tatarav1alpha1.Task
	_ = c.Get(context.Background(), client.ObjectKey{Namespace: testNS, Name: "t1"}, &got)
	if len(got.Status.PendingComments) != 1 || got.Status.PendingComments[0] != "hello" {
		t.Fatalf("PendingComments = %#v, want [hello]", got.Status.PendingComments)
	}
}
```
Adapt helper names (`newTestTask`/`newTestServer`/`doJSON`/`testNS`) to whatever the sibling `issueOutcome` test actually uses.

- [ ] **Step 2: Run - expect FAIL** (no route/handler).
  Run: `cd tatara-operator && go test ./internal/restapi/ -run TestPostComment -v`
  Expected: FAIL (404 / undefined).

- [ ] **Step 3: Implement the handler** in `handlers.go`:

```go
type issueCommentReq struct {
	Body string `json:"body"`
}

func (s *Server) postComment(w http.ResponseWriter, r *http.Request) {
	var req issueCommentReq
	if err := decodeJSON(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid body: "+err.Error())
		return
	}
	if req.Body == "" {
		writeError(w, http.StatusBadRequest, "body required")
		return
	}
	var t tatarav1alpha1.Task
	if err := s.c.Get(r.Context(), client.ObjectKey{Namespace: s.ns, Name: chi.URLParam(r, "t")}, &t); err != nil {
		writeClientErr(w, err)
		return
	}
	if t.Spec.Source == nil || t.Spec.Source.IssueRef == "" {
		writeError(w, http.StatusConflict, "comment requires a task linked to an issue")
		return
	}
	t.Status.PendingComments = append(t.Status.PendingComments, req.Body)
	if err := s.c.Status().Update(r.Context(), &t); err != nil {
		writeClientErr(w, err)
		return
	}
	writeJSON(w, http.StatusOK, toTaskDTO(t))
}
```

- [ ] **Step 4: Register the route** in `server.go`, after line 56 (`/tasks/{t}/issue-outcome`):

```go
	r.Post("/tasks/{t}/comment", s.postComment)
```

- [ ] **Step 5: Run - expect PASS.**
  Run: `cd tatara-operator && go test ./internal/restapi/ -run TestPostComment -v`
  Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add internal/restapi/handlers.go internal/restapi/server.go internal/restapi/*_test.go
git commit -m "feat: POST /tasks/{t}/comment queues an agent comment"
```

### Task C3: Drain `PendingComments` in reconcileLifecycle (TDD)

**Files:**
- Modify: `internal/controller/lifecycle.go` (`reconcileLifecycle`, insert after the project fetch at line 243, before the spawn-gate block at line 245)
- Test: `internal/controller/lifecycle_*_test.go` (use the fake `SCMWriter` with a `Comment` method already present in `lifecycle_test.go:47`)

- [ ] **Step 1: Write the failing test.** Add a test that builds a lifecycle Task with `Status.PendingComments = ["a","b"]` and `Spec.Source.IssueRef` set, runs `reconcileLifecycle` with a fake writer that records `Comment` calls, and asserts: both comments were posted to the issue ref in order, and the Task's `Status.PendingComments` is cleared afterward. Mirror the existing lifecycle reconcile tests (they wire `scmContext` via the reconciler's fake reader/writer). Core assertion:

```go
func TestReconcileLifecycle_DrainsPendingComments(t *testing.T) {
	task := newLifecycleTask(t, "Triage") // helper used by sibling lifecycle tests
	task.Spec.Source = &tatarav1alpha1.TaskSource{IssueRef: "owner/repo#7"}
	task.Status.PendingComments = []string{"first", "second"}
	r, writer := newLifecycleReconciler(t, task) // mirror sibling setup; writer records Comment calls
	if _, err := r.reconcileLifecycle(context.Background(), task); err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	if got := writer.comments("owner/repo#7"); len(got) != 2 || got[0] != "first" || got[1] != "second" {
		t.Fatalf("posted comments = %#v, want [first second]", got)
	}
	// reload + assert cleared (the reconcile persists via Status().Update)
	var reloaded tatarav1alpha1.Task
	_ = r.Get(context.Background(), client.ObjectKey{Namespace: task.Namespace, Name: task.Name}, &reloaded)
	if len(reloaded.Status.PendingComments) != 0 {
		t.Fatalf("PendingComments not cleared: %#v", reloaded.Status.PendingComments)
	}
}
```
Adapt helper names to the sibling lifecycle tests. The fake writer in `lifecycle_test.go:47` already has `Comment(ctx, _, issueRef, body)`; extend it to record bodies per issueRef if it does not already.

- [ ] **Step 2: Run - expect FAIL** (comments not drained).
  Run: `cd tatara-operator && go test ./internal/controller/ -run TestReconcileLifecycle_DrainsPendingComments -v`
  Expected: FAIL.

- [ ] **Step 3: Implement the drain.** In `reconcileLifecycle`, insert immediately after the project fetch (after line 243), before the `// Memory + concurrency gates` block:

```go
	// Drain agent-queued free-form comments (from the comment MCP tool) to the
	// linked issue before anything else, then clear and requeue.
	if len(task.Status.PendingComments) > 0 && task.Spec.Source != nil && task.Spec.Source.IssueRef != "" {
		_, _, writer, token, _, err := r.scmContext(ctx, task)
		if err != nil {
			r.Metrics.ReconcileResult("Task", "error")
			return ctrl.Result{}, fmt.Errorf("lifecycle drain comments: %w", err)
		}
		for _, c := range task.Status.PendingComments {
			if cerr := writer.Comment(ctx, token, task.Spec.Source.IssueRef, c); cerr != nil {
				r.Metrics.ReconcileResult("Task", "error")
				return ctrl.Result{}, fmt.Errorf("lifecycle drain comment: %w", cerr)
			}
			l.Info("lifecycle: agent comment posted",
				"action", "scm_agent_comment", "resource_id", task.Name)
		}
		task.Status.PendingComments = nil
		if err := r.Status().Update(ctx, task); err != nil {
			r.Metrics.ReconcileResult("Task", "error")
			return ctrl.Result{}, fmt.Errorf("lifecycle clear comments: %w", err)
		}
		return ctrl.Result{Requeue: true}, nil
	}

```
(`scmContext` destructuring matches lifecycle.go:666; `writer.Comment` matches the backstop call at lifecycle.go:711.)

- [ ] **Step 4: Run - expect PASS.**
  Run: `cd tatara-operator && go test ./internal/controller/ -run TestReconcileLifecycle_DrainsPendingComments -v`
  Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/controller/lifecycle.go internal/controller/lifecycle_test.go
git commit -m "feat: drain queued agent comments to the linked issue on reconcile"
```

### Task C4: Nudge triage prompt to invoke the follow-up skill (TDD)

**Files:**
- Modify: `internal/controller/turnloop.go:58` (`lifecycleTriageText`)
- Test: `internal/controller/lifecycle_m2_test.go` (has `TestBuildTriagePrompt_*`)

- [ ] **Step 1: Write the failing test.** Add to `lifecycle_m2_test.go`:

```go
func TestLifecycleTriageText_NamesFollowupSkill(t *testing.T) {
	task := &tatarav1alpha1.Task{}
	task.Spec.Source = &tatarav1alpha1.TaskSource{IssueRef: "o/r#1", URL: "http://x"}
	got := lifecycleTriageText(task, "T", "B")
	if !strings.Contains(got, "tatara-research-followup") {
		t.Fatalf("triage prompt does not invoke tatara-research-followup skill:\n%s", got)
	}
}
```

- [ ] **Step 2: Run - expect FAIL.**
  Run: `cd tatara-operator && go test ./internal/controller/ -run TestLifecycleTriageText_NamesFollowupSkill -v`
  Expected: FAIL.

- [ ] **Step 3: Implement.** In `lifecycleTriageText`, add a line to the prompt (e.g. before "Your job:") instructing skill use:

```go
			"Invoke the `tatara-research-followup` skill, which defines how to research the gap, post design comments via the comment tool, and decide the outcome.\n\n"+
```
Insert it into the `fmt.Sprintf` format string at an appropriate point (after the issue body block, before "Your job:"). Keep the existing args order intact.

- [ ] **Step 4: Run - expect PASS** (and confirm sibling `TestBuildTriagePrompt_*` still pass).
  Run: `cd tatara-operator && go test ./internal/controller/ -run "TestLifecycleTriageText_NamesFollowupSkill|TestBuildTriagePrompt" -v`
  Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add internal/controller/turnloop.go internal/controller/lifecycle_m2_test.go
git commit -m "feat: triage prompt invokes the tatara-research-followup skill"
```

### Task C5: Nudge brainstorm goal to invoke the deep-research skill (TDD)

**Files:**
- Modify: `internal/controller/projectscan.go:772` (the brainstorm goal string)
- Test: `internal/controller/projectscan_*_test.go` (find brainstorm tests: `grep -rln "brainstorm\|Propose a single" internal/controller/*_test.go`)

- [ ] **Step 1: Inspect the goal construction** at `projectscan.go:772`:
  `goal := "Propose a single, well-defined issue for repo " + slug`.

- [ ] **Step 2: Write the failing test.** Add a test asserting the brainstorm goal text contains `tatara-deep-research`. If the goal is built inline, refactor it to a small helper `brainstormGoal(slug string) string` so it is unit-testable, and test the helper:

```go
func TestBrainstormGoal_NamesDeepResearchSkill(t *testing.T) {
	g := brainstormGoal("tatara-cli")
	if !strings.Contains(g, "tatara-deep-research") {
		t.Fatalf("brainstorm goal does not invoke tatara-deep-research skill: %s", g)
	}
	if !strings.Contains(g, "tatara-cli") {
		t.Fatalf("brainstorm goal lost the repo slug: %s", g)
	}
}
```

- [ ] **Step 3: Run - expect FAIL.**
  Run: `cd tatara-operator && go test ./internal/controller/ -run TestBrainstormGoal -v`
  Expected: FAIL.

- [ ] **Step 4: Implement.** Replace the inline goal with a helper and call it at line 772:

```go
func brainstormGoal(slug string) string {
	return "Invoke the `tatara-deep-research` skill to research the platform deeply and propose a single, " +
		"well-defined discovery issue for repo " + slug + ". The skill defines how to research via the " +
		"tatara-memory graph and on-disk code, score leverage, dedup, and file exactly one issue via propose_issue."
}
```
At line 772: `goal := brainstormGoal(slug)`.

- [ ] **Step 5: Run - expect PASS.**
  Run: `cd tatara-operator && go test ./internal/controller/ -run TestBrainstormGoal -v`
  Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add internal/controller/projectscan.go internal/controller/projectscan_run_test.go
git commit -m "feat: brainstorm goal invokes the tatara-deep-research skill"
```

### Task C6: Operator full-suite gate

- [ ] **Step 1: Run the full operator test suite.**
  Run: `cd tatara-operator && go test ./... && go vet ./...`
  Expected: PASS. Fix any breakage from C1-C5 (e.g. fake writers needing the recording helper).

---

## Integration & landing (per repo)

For EACH repo (wrapper, cli, operator), in its worktree:

- [ ] Run `superpowers:requesting-code-review` on the diff; fix all critical/high findings.
- [ ] Run the repo's linters + tests green: Go repos `golangci-lint run && go test ./...`; wrapper also `helm lint charts/* ` and helm-unittest if present.
- [ ] Merge the worktree branch back to that repo's `main` (hard rule 10), push.
- [ ] Nurse the main CI pipeline until green. Re-run on transient eviction (a uniform ~18min "operation was canceled" = control-plane node flap, NOT a test failure - see global memory). Watch for ARC stale-listener stuck queues. Keep fixing real failures until green.
- [ ] Clean up the worktree.

Ordering: land operator (C) and cli (B) first; tag cli (B3); then do wrapper A4 (cli pin bump) and land wrapper (A). Deploy stays GATED (infra helmfile) - out of scope.

## Self-review notes (author)

- Spec coverage: skill 1 -> A1; skill 2 -> A2; comment capability -> B1 + C2 + C3; discovery-stay -> A1 marker + existing self-approve guard (no new code); cross-platform research -> A1 step 2; full design conversation -> A2; invocation wiring -> C4 + C5; cli pin/guard -> A4; stale desc boy-scout -> B2. All covered.
- Type consistency: `PendingComments []string` defined C1, written C2, drained C3. `comment` tool name consistent across B1/A4/C2 path. Skill dir names consistent A1/A2 vs C4/C5.
- No placeholders: helper names in operator tests (`newTestTask` etc.) are explicitly flagged "adapt to sibling test harness" because the exact harness names vary by file; the implementer has the sibling test open.
