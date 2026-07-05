# Discovery-phase research harness - design

Date: 2026-06-13
Status: approved (brainstorm) - ready for plan
Repos touched: tatara-claude-code-wrapper, tatara-cli, tatara-operator
Spec owner repo: tatara (docs index)

## Problem

The tatara autonomous loop is live and dogfooding. Its discovery phase
(turn an idea into a well-formed, human-approvable issue) is thin: the
`brainstorm` task kind opens an idea-labelled issue from a single prompt
with no deep cross-platform research procedure, and the
`issueLifecycle` Conversation state has no procedure for an agent to
actually deepen a research issue into a converged design before a human
approves. We want two agent skills that encode those procedures, driven
entirely through the `tatara` MCP server, that:

1. From scratch, do deep cross-platform research and open ONE
   high-leverage discovery issue (kind `brainstorm`).
2. Follow up on an existing discovery issue: answer maintainer
   questions, refine into a concrete design in-thread, push toward
   approval, idle when there is nothing to add (kind `issueLifecycle`,
   Triage/Conversation).

Hard requirement: research issues stay in discovery - never
self-implemented - until a human approves.

## Constraints (from platform CLAUDE.md hard rules + research)

- Agent comms only through the `tatara` MCP server. No gh/git SCM
  tooling exists in the agent surface and none is added.
- Skills are baked `SKILL.md` trees, copied verbatim into
  `/workspace/.claude/skills` at boot (`installSkills`,
  `internal/bootstrap/skills.go`). Format: dir under
  `templates/skills/<name>/` with `SKILL.md` carrying YAML frontmatter
  (`name`, `description`) + markdown body, helper files by relative
  path. No Go templating of skill content.
- The wrapper is mode-agnostic. "Modes" (`brainstorm`,
  `issueLifecycle`, `review`) are operator `TaskSpec.Kind` values whose
  prompt text the operator POSTs as the turn `text`. A baked skill is
  only reliably used if the turn prompt names it (we do this) and/or its
  description matches strongly.
- The agent pod has ONE repo checked out on disk. Cross-repo context
  comes only from the tatara-memory graph (`code_cross_repo`, `query`,
  `describe`, the `code_*` family).
- Issue creation egress is `propose_issue` only -> `POST
  /projects/{p}/issues` -> Task CRD -> controller `CreateIssue` under
  `szymonrychu-bot`. It opens idea-labelled with `ApprovalRequired=false`
  and completes the Task. No `triggerLabel` => not force-implemented.
- Discovery hold: the self-approve guard (`finishTriage` implement arm)
  downgrades a `<!-- tatara-authored -->` issue with no human comment to
  discuss/idea + Conversation. R1: brainstorm output is never
  self-approved.
- Generic free-form commenting does NOT exist today: commenting is only
  a side-effect of `issue_outcome(discuss|close)` / `review_verdict`,
  both bound to a Task kind. The follow-up skill needs to post
  substantive design comments separately from the terminal
  state-transition, so we add a thin task-scoped `comment` tool.
- KISS; no tech debt; conventional commits; JSON slog; table-driven Go
  tests; newest stable Go pinned; charts cluster-agnostic (no chart
  change needed here).

## Architecture

Three layers, one per repo. Skills carry the *workflow*; MCP tools carry
the *capability*; operator prompt carries the *invocation*.

```
brainstorm turn ----> wrapper skill tatara-deep-research
   (operator prompt names it)        |
                                     | researches via tatara-memory MCP (graph)
                                     | + on-disk repo files
                                     v
                              propose_issue (MCP) --> operator --> bot opens
                                                      idea-labelled discovery issue

issueLifecycle Triage/Conversation turn ----> wrapper skill tatara-research-followup
   (operator prompt names it)                       |
                                                    | task_get + read thread
                                                    | research the gap (graph + on-disk)
                                                    v
                                       comment (NEW MCP) --> operator --> bot posts
                                                    |        design comments to the issue
                                                    v
                                       issue_outcome(discuss)  (terminal: hold Conversation,
                                                                never self-approve)
```

## Component A - tatara-claude-code-wrapper (skills)

Two new dirs under `templates/skills/`. Baked into the image by the
existing `COPY templates/ /templates/`; installed by `installSkills`.
No Go, chart, or Dockerfile change for the skills themselves (the
Dockerfile cli-pin bump in Component B is the only wrapper code change).

### `tatara-deep-research/SKILL.md`

Frontmatter:
- name: `tatara-deep-research`
- description: Use on an autonomous platform-research (brainstorm) turn
  to discover one high-leverage improvement and open a discovery-phase
  issue via propose_issue, after deep cross-platform research over the
  tatara-memory graph and the on-disk repo.

Body workflow (numbered, with a checklist the agent turns into todos):
1. Orient on goals. Read on-disk `ROADMAP.md`, `MEMORY.md`, `CLAUDE.md`
   (platform goal, repo charter, the 14 hard rules). Use memory `query`
   (global/hybrid) for platform goal and open roadmap themes; `describe`
   for the repo.
2. Map current state. `code_stats`, `code_important` (high-PageRank
   entities), `code_communities` (subsystems), `code_bridges` (coupling
   risk), `code_cross_repo` (cross-repo edges). Then READ the on-disk
   code for the strongest candidate area (graph points, files confirm).
3. Score leverage. Priority order: live-loop reliability/observability >
   un-built planned loop features > Phase-9 SOTA backlog > deploy debt.
   Respect gates (e.g. the memory retrieval-quality eval harness gates
   any memory ranking/reranker proposal - do not propose downstream
   memory-ranking work before that gate exists).
4. Dedup. `task_list` + the repo's open issue list (via task/project
   reads); skip if a similar idea is already open (avoid colliding with
   the operator's own brainstorm proposals and the MaxOpenProposals cap).
5. Compose ONE proposal: imperative title; body = problem, evidence
   (`file:line` + concrete graph findings), KISS approach that respects
   the hard rules, explicit scope boundary, open questions for the
   maintainer. Embed the literal `<!-- tatara-authored -->` marker so
   the self-approve guard holds it in discovery.
6. Emit via `propose_issue` (kind `improvement` or `bug`; `repo` /
   `repositoryRef`; `project` from env). Never set `triggerLabel`.
   Exactly one issue per run; then stop (the brainstorm Task completes).

Rules block: comms only via MCP; never open issues via gh/git; one issue
per run; stay in discovery; respect all 14 hard rules in the proposal so
the loop that implements it will accept it.

### `tatara-research-followup/SKILL.md`

Frontmatter:
- name: `tatara-research-followup`
- description: Use when continuing an existing discovery/research issue
  conversation (issueLifecycle Triage/Conversation turn) - read the
  thread, answer maintainer questions, deepen the proposal into a
  concrete design, push toward approval, idle when nothing to add. Never
  self-approve.

Body workflow:
1. Load context. `task_get` (issue thread, lifecycle state, status);
   read issue body + all comments. Extract open questions, maintainer
   asks, unresolved design points. Identify whether a human has engaged.
2. Research the gap. Use the memory graph + on-disk code (cross-repo via
   `code_cross_repo`) to answer the specific questions or to deepen a
   thin proposal.
3. Respond in-thread with the `comment` tool: answer each question with
   evidence; refine the proposal into a concrete design (architecture,
   components, data flow, error handling, testing) plus an
   implementation outline; surface remaining decisions. Multiple
   comments allowed; keep each focused.
4. Drive to approval. When the design is converged AND a human has
   engaged, post a summary of the agreed design and explicitly request
   the human approval signal (a maintainer comment / the approval
   label). NEVER self-approve (R1 + self-approve guard).
5. Idle discipline. If there is no human input and nothing genuinely new
   to add, post nothing and let Conversation idle (~1h). Do not spam.
6. End the turn with `issue_outcome(discuss)` to hold/return to
   Conversation; use `issue_outcome(close)` only if the idea is clearly
   dead and a human concurred. Never `issue_outcome(implement)` on a
   tatara-authored research issue lacking human approval.

Rules block: comms only via MCP; never self-approve; one focused turn;
silence over noise.

### Tests (wrapper)
- A Go test (or extend the existing skill-copy test) asserting both new
  skill dirs exist under `templates/skills/` and each `SKILL.md` parses
  with non-empty `name` + `description` frontmatter and a body.
- Existing helm-unittest in `charts/.../tests/` is unaffected (no chart
  change).

## Component B - tatara-cli (comment MCP tool)

New MCP tool in `internal/mcp/tools.go`:
- name: `comment`
- target: operator
- args: `task` (string, defaults from `TATARA_TASK` via `argOrEnv`),
  `body` (string, required), `target` (enum `issue`|`mr`, default
  `issue`).
- maps to `POST /tasks/{task}/comment` with `{body, target}`.

Boy-scout (adjacent, in scope): fix the stale `propose_issue`
description at `tools.go:390` ("created behind the awaiting-approval
label") to match reality (opens an idea-labelled issue, no parking).

Tests: table-driven test for tool registration + arg mapping + the REST
path/payload (mirroring the existing `issue_outcome`/`propose_issue`
tool tests).

Release: tag a new cli version after merge. The wrapper Dockerfile pins
`TATARA_CLI_VERSION` and a build-time guard test asserts the operator
tools exist - add `comment` to that guard list and bump the pin to the
new tag (this is the only wrapper Go-side change).

## Component C - tatara-operator (handler + prompt nudges)

1. REST handler `POST /tasks/{task}/comment` in `internal/restapi`
   (alongside `issue-outcome`/`review`/`pr-outcome` handlers): validate
   the Task exists and is non-terminal; resolve the linked issue/MR
   (issue number from the Task's source metadata, or the linked MR for
   `target=mr`); post `body` via the existing `scm.Writer.PostComment`
   primitive used by `triagePostComment`, under the bot token. Return
   200 with the posted comment ref. Reuse existing auth/identity; do not
   add a new SCM code path.
2. Controller wiring as needed so the handler reaches the writer (follow
   the `triagePostComment` call path; no new lifecycle state).
3. Prompt nudges:
   - brainstorm prompt builder (the text behind the `brainstorm` Kind,
     `createProposal` path): add an instruction to invoke the
     `tatara-deep-research` skill.
   - `lifecycleTriageText` (`internal/controller/turnloop.go:58`): add an
     instruction to invoke the `tatara-research-followup` skill on
     Triage/Conversation turns.

Tests: REST handler test (`POST /tasks/{t}/comment` -> writer
`PostComment` invoked with the right issue/MR + body; non-terminal/terminal
Task guard); a unit assertion that the two prompt builders contain the
skill-invocation text. envtest only where the existing handler tests use
it.

## Cross-repo contract & ordering

- Operator prompt strings reference the exact skill names
  (`tatara-deep-research`, `tatara-research-followup`) shipped by the
  wrapper. Single change-set keeps them consistent.
- The cli `comment` tool calls `POST /tasks/{task}/comment`, which the
  operator must implement. cli tool is unit-testable independently
  (mock REST); runtime needs the operator handler.
- Build-time dependency: the wrapper Dockerfile cli-pin bump + guard
  update must wait until the cli `comment` tool is merged and tagged.

Implementation order (parallel where possible):
- Parallel lane 1: tatara-operator (handler + controller + prompt
  nudges + tests).
- Parallel lane 2: tatara-cli (comment tool + stale-desc fix + tests).
- Parallel lane 3: tatara-claude-code-wrapper skills (the two SKILL.md
  trees + skill-presence test).
- Serial tail: after cli merges + is tagged, bump the wrapper Dockerfile
  `TATARA_CLI_VERSION` pin + guard list, then merge wrapper.

## Done definition

Per repo: TDD -> `requesting-code-review` -> fix critical/high ->
`pre-commit run --all-files` -> merge to that repo's `main` (triggers CI
image build) -> nurse the main pipeline until green (re-run, do not
misread a uniform ~18min "operation was canceled" control-plane-node
eviction as a test failure; watch for ARC stale-listener). Deploy stays
gated (infra helmfile) and is OUT OF SCOPE here.

## Decisions (locked)

- Comment tool is task-scoped (`/tasks/{task}/comment`), not
  arbitrary-issue: reuses existing identity/linkage; skill 2 always runs
  inside a Task. KISS.
- Skill names: `tatara-deep-research`, `tatara-research-followup`.
- One issue per from-scratch run (matches the brainstorm Kind, which
  completes after one proposal; backpressure-friendly vs MaxOpenProposals).
- Research breadth: cross-platform (graph + on-disk).
- Follow-up: full design conversation to approval, with idle discipline.

## Out of scope

- Deploying any of the three components (infra helmfile, gated).
- The phase-label-dedup migration (separate worktree/agent).
- A new operator Task Kind (skills slot onto existing `brainstorm` /
  `issueLifecycle` kinds).
- Arbitrary-issue commenting outside a Task.
