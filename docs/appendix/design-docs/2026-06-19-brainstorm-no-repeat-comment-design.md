# Brainstorm: no repeat-comment, prefer novelty — Design

Date: 2026-06-19
Repo: tatara-operator (operator-only)
Status: approved design, pre-plan

## Problem

The hourly project-wide brainstorm agent surveys open issues and, per the
2026-06-15 brainstorm-dedup design, may call `comment_on_issue` when its idea
"connects to" an existing issue instead of opening a duplicate. In practice the
highest-leverage idea keeps mapping to the SAME open issue cycle after cycle, so
the agent re-comments the same issue repeatedly instead of generating new
improvements. Nothing tells the agent which issues the bot already engaged, and
nothing stops a repeat comment.

Desired behavior: never comment twice on the same issue. If the best idea
connects to an already-engaged issue, the agent moves on -- a different
high-leverage improvement (novel standalone proposal in the same repo, another
repo, or project-wide), a comment on a still-untouched issue, or a no-op.

## Approved decisions

- Enforcement: **prompt + operator hard-gate** (inject bot-comment awareness AND
  refuse a duplicate bot comment at the egress endpoint). Matches the
  bot-authorship-at-egress design.
- Re-comment cap: **1** (zero re-comments). The bot comments any issue at most
  once, ever; after that the issue is off-limits for further comments.
- Detection: **live SCM comment scan** (`ListIssueComments`, check
  `Author == BotLogin`). Authoritative, no new persisted state.

All scoped to tatara-operator: the `comment_on_issue` cli MCP tool and wrapper
pin already shipped (2026-06-15), so no cli / wrapper / `agent.image` change. The
prompt only narrows WHEN the agent calls the existing tool.

## Design

### Part A -- context awareness (internal/controller/projectscan.go `brainstorm()`)

`brainstorm()` already calls `ListOpenIssues` across all project repos to build
the dedup context block. Extend that pass: for each open issue included in the
context (subject to the existing cap, e.g. first 60), call
`ListIssueComments(ctx, owner, repo, number)` and check whether any
`IssueComment.Author == proj.Spec.Scm.BotLogin`.

- If yes, mark that issue's context entry `[bot-engaged]` (alongside its existing
  `repo#N [labels] Title` + snippet).
- The scan honors the same truncation cap as the issue context: issues beyond the
  cap are not scanned and carry no flag; `log()` when truncated. The Part B
  hard-gate still protects unscanned issues.
- `BotLogin` empty (no bot configured) -> skip the scan entirely, no flags. Same
  as today's behavior.
- Cost: at most one `ListIssueComments` call per in-context open issue per cycle
  (N <= cap, hourly). Trivial against GitHub's 5000/hr authenticated budget and
  GitLab equivalents.

### Part A -- prompt (`brainstormGoalProject`)

Revise the action menu so a `[bot-engaged]` issue is removed from the
comment-eligible set:

- Duplicate of an existing issue -> do NOT propose; one-line note naming the
  duplicate (unchanged).
- Connects to / is a sub-aspect of an issue that is NOT `[bot-engaged]` ->
  `comment_on_issue` on that issue (unchanged).
- Connects to a `[bot-engaged]` issue -> do NOT re-comment. Instead pick a
  different high-leverage improvement: a genuinely novel standalone idea
  (`propose_issue`, same repo / another repo / project-wide), OR a comment on a
  different still-untouched issue, OR finish with a no-op note if nothing else
  stands out.
- Genuinely novel standalone -> `propose_issue` (unchanged).
- Explicit framing: "Prefer NEW improvements over re-engaging existing
  discussion. Never comment twice on the same issue."

Exactly one of {propose_issue, comment_on_issue, no-op-with-note} per run, as
today. Writeback is unaffected (a no-proposal run is `BrainstormComplete`, not a
failure).

### Part B -- egress hard-gate (internal/restapi/handlers.go `commentOnIssue`)

The `commentOnIssue` handler already resolves the Project, maps `repo` to an
`owner/repo`, fetches the project SCM token, and posts via an SCM writer. Before
the write, add a gate:

1. Obtain an SCM **reader** bound to the same project token. The handler today
   gets only a writer via the server's SCM factory; the concrete `*GitHub` /
   `*GitLab` clients already satisfy `SCMReader` too. Extend the factory so the
   handler can obtain a reader (either widen the existing factory's return type
   to a combined read+write interface, or add a sibling `SCMReaderFor`),
   reusing the same provider + token resolution. Match existing conventions.
2. `ListIssueComments(ctx, owner, repo, number)`; if any
   `Author == proj.Spec.Scm.BotLogin` -> return **409 Conflict**, body
   `bot already commented on this issue; pick another action`. Do NOT post.
3. On block: `log.InfoContext(action=scm_issue_comment_blocked,
   reason=already_commented, project, repo, number)` and increment a counter
   `brainstorm_recomment_blocked_total` (hard rule 13).
4. `BotLogin` empty -> skip the gate, post as today.

This is the authoritative backstop: even if the agent ignores the prompt, the
operator refuses the second bot comment. It also makes `comment_on_issue`
idempotent against the bot's own prior comment. TOCTOU between the read and the
post is ignored -- hourly cadence + cap-1 make a concurrent double-post
practically impossible.

## Deploy order

Operator-only, single component:
1. tatara-operator: merge Part A + B -> operator image (CI builds + pushes).
2. tatara-helmfile: MR bumping the operator chart version AND the pinned
   `image.tag` (per the operator-deploy memory: both, never chart-only);
   diff -> merge -> pipeline applies.

No cli, wrapper, or Project `agent.image` change -- the prompt only constrains an
already-deployed tool.

## Testing (TDD)

- projectscan: with a bot comment present on an issue, the built context flags it
  `[bot-engaged]`; absent -> no flag; `BotLogin` empty -> no scan calls. Goal text
  from `brainstormGoalProject` contains the no-re-comment instruction (string
  asserts) and still names `propose_issue` / `comment_on_issue`.
- handler: `commentOnIssue` returns 409 when a bot comment already exists on the
  target issue (writer.Comment NOT called); 200 + writer.Comment called when none
  exists; reader built from the project token; `BotLogin` empty -> gate skipped.
- Full envtest controller suite green (KUBEBUILDER_ASSETS via setup-envtest
  1.33.0).

## Out of scope

- Semantic "same topic" dedup: we key strictly on the bot's literal prior comment
  on that issue, not on content similarity.
- Recently-closed issues (the scan covers open issues only, as today).
- Per-issue cap > 1 / configurable back-and-forth: fixed at 1.
- No persisted comment-history state (live scan only).
