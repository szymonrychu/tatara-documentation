# Brainstorm dedup + comment-on-existing-issue — Design

Date: 2026-06-15
Repos: tatara-operator, tatara-cli, tatara-claude-code-wrapper (+ tatara-helmfile deploy)
Status: approved design (scope/mechanism/context settled), pre-plan

## Problem

The hourly brainstorm agent opens a new issue every cycle (`propose_issue` is a hard
requirement) with no real dedup: the goal delegates dedup to the `tatara-deep-research`
skill, which was never built, and the agent has no way to see existing issues. Result:
duplicate proposals and "connecting" proposals that should be a comment on an existing
issue, not a new issue.

## Approved decisions

- Scope: **full** — (A) dedup so no duplicate issue opens, AND (B) the agent comments on
  the related existing issue when the idea connects to it.
- B mechanism: **operator-mediated** — a new operator endpoint + a cli MCP tool, so the
  SCM comment is posted by the operator under the bot identity (matches the
  bot-authorship-at-egress design). Not direct `gh`.
- Context richness: **rich** — inject each open issue's repo, number, title, labels, and a
  short body snippet into the brainstorm prompt.

## Design

### Part A — dedup (tatara-operator, prompt + context)

`brainstorm()` (projectscan.go) already holds an `scm.SCMReader`. Before creating the
brainstorm Task it will:
1. `ListOpenIssues` across all project repos, filter out PRs, build a compact-but-rich
   context block: one entry per open issue = `repo#N [labels] Title` + a body snippet
   (first ~200 chars, newlines collapsed). Cap total (e.g. first 60 issues / N tokens) and
   `log()` if truncated.
2. Pass that block into a revised goal builder `brainstormGoalProject(slugs, issuesCtx)`.

The goal text changes from "you MUST call propose_issue" to dedup-first:
- Survey the existing open issues listed below FIRST.
- If your highest-leverage idea **duplicates** an existing issue -> do NOT propose; finish
  with a one-line note naming the duplicate (no SCM action).
- If it is a **sub-aspect / connecting** improvement to an existing issue -> call
  `comment_on_issue(repo, number, body)` on that issue instead of opening a new one.
- Only if it is **genuinely novel and standalone** -> call `propose_issue`.
- Exactly one of {propose_issue, comment_on_issue, no-op-with-note} per run; state which and why.

Writeback (writeback.go) already classifies BrainstormProposed (propose_issue child) vs
BrainstormComplete (none). Add a third recognized outcome is unnecessary: a
comment_on_issue run files no proposal child -> BrainstormComplete is correct (the comment
already landed via the operator). No writeback change required beyond confirming the
no-proposal path is not treated as failure.

### Part B — comment_on_issue capability

**tatara-operator** — new endpoint, synchronous (brainstorm Tasks have no PendingComments
drain, so queue-on-task does not apply):
- Route: `POST /projects/{p}/issue-comment`, body `{ "repo": "<slug-or-name>", "number": <int>, "body": "<text>" }`.
- Handler `commentOnIssue`: resolve the Project, map `repo` to its Repository CR -> URL ->
  `owner/repo`, build issueRef `owner/repo#number`, post via an SCM writer using the
  project SCM secret token. Returns 200 on success; 4xx on bad repo/number/body.
- This requires wiring an SCM writer + project-token fetch into the restapi `Server`
  (it currently has only the k8s client). Add `SCMFor func(provider) (scm.SCMWriter, error)`
  + a token helper reading `proj.Spec.ScmSecretRef`. Mirrors how scanWriter resolves them
  on the controller side.
- Egress safety: the operator posts under the bot token (the agent never gets the token);
  this IS the egress mediation. Log `action=scm_issue_comment` with project/repo/number.

**tatara-cli** — new MCP tool in `internal/mcp/tools.go` via the existing `op(...)` helper:
- `op("comment_on_issue", "<desc: comment on an EXISTING issue when your idea connects to / is a sub-aspect of it, instead of opening a duplicate>", schema{repo:string, number:int, body:string, all required}, fn -> POST "/projects/"+p+"/issue-comment" with {repo, number, body})`.
- `tools/list` must serve it tokenlessly (build-guard in the wrapper checks this; see the
  wrapper-cli-pin memory).

**tatara-claude-code-wrapper** — bump `TATARA_CLI_VERSION` to the cli commit that ships
`comment_on_issue` (single-line pin change; CI rebuilds the wrapper image embedding the cli).

## Deploy order (dependency-ordered)
1. tatara-cli: merge `comment_on_issue` -> cli image + `tatara mcp` serves it tokenless.
2. tatara-claude-code-wrapper: bump TATARA_CLI_VERSION -> wrapper image (build-guard passes).
3. tatara-operator: merge endpoint + prompt -> operator image.
4. tatara-helmfile: bump operator `image.tag`/chart AND Project `spec.agent.image` to the
   new wrapper image; PR -> diff -> merge -> apply.

The operator endpoint and the prompt change are inert until the wrapper (with the new cli
tool) is the running agent image, so order 1->2->3->4 avoids a window where the prompt asks
for a tool the agent lacks. (If 3 lands before 2/4, the brainstorm prompt would reference
comment_on_issue before the agent has it — so the Project agent.image bump must accompany
or precede the operator prompt activation. Safe sequence: ship cli+wrapper first, bump
agent.image, then ship+deploy the operator prompt/endpoint.)

## Testing
- operator: handler unit test (resolve repo -> issueRef, writer.Comment called; bad repo ->
  4xx); brainstorm goal includes the issues context + dedup instructions (string asserts);
  full controller suite green.
- cli: tools.go test asserts `comment_on_issue` is registered and tools/list serves it
  tokenless; the op builds the right method/path/body.
- wrapper: existing build-guard (tools/list tokenless) is the gate.

## Out of scope / notes
- Dedup considers OPEN issues only (incl. open proposals). Recently-closed dedup is a later
  refinement.
- Semantic dedup (beyond title+snippet) is not attempted; the agent judges from the injected
  context.
