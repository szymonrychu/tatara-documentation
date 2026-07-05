# Tatara as an Agentic-Dev Platform: SOTA Comparison and Improvements

**Date:** 2026-06-06
**Author:** research synthesis (3 parallel WebSearch streams), Opus
**Status:** notes / improvement backlog input. Not a design spec. Actionable
items are mirrored into `ROADMAP.md`.

## Framing

Tatara is a supporting system for autonomous, unattended agentic development.
Three pillars, evaluated here against the 2024-2026 state of the art:

1. **Memory** (`tatara-memory` + `tatara-memory-repo-ingester`) - an AI/syntax-
   aware code search engine for massive, multi-repo, partly-legacy codebases.
2. **Chat** (`tatara-chat`) - the coordination bus for agents developing in
   parallel.
3. **Agents** (`tatara-claude-code-wrapper`) - Claude Code driven unattended in
   k8s pods, one session per pod.

Headline verdict: the three core architectural bets are all directionally
correct and match where the field converged. The gaps are not in the cores;
they are in the **control, precision, and isolation planes around** each core.
The most valuable cross-cutting theme is that **all three pillars are
deliberately under-specified on the seams between them** - cross-repo symbol
links (memory), conflict/claim coordination (chat), and credential/blast-radius
isolation (agents) - and those seams are exactly where fleet-scale agentic dev
breaks.

---

## Pillar 1 - Memory: code search over large/legacy multi-repo codebases

Reference points the field converged on: Sourcegraph **SCIP** (language-neutral,
human-readable symbol IDs, cross-repo monikers; replaced LSIF, ~8x smaller/~3x
faster); Meta **Glean** (open-sourced Dec 2024; per-language schemas + a derived
language-neutral view + stacked-immutable-DB incremental indexing); GitHub
**stack-graphs** (file-incremental name resolution without invoking the build);
**Aider** repo-map (tree-sitter tags -> symbol graph -> PageRank ranking);
**RepoGraph** (ICLR 2025; k-hop ego-graph retrieval, +32.8% avg SWE-bench-Lite
resolve-rate); **Cursor** (AST chunking + trained embeddings + Merkle-tree
incremental sync, +12.5% avg accuracy of hybrid over grep-only, largest on
1000+ file repos); **Greptile** (per-node docstring embeddings + agentic
reference tracing).

What tatara already gets right (defend these): a deterministic structural graph
keyed by **stable, line-free, language-neutral symbol IDs** is exactly the
SCIP/Glean model and a genuine strength; hybrid structural-graph + semantic-
chunk beats grep-only (measured consensus, do not rip it out for pure agentic
grep); per-language logic isolated in the ingester with a language-neutral
store/query is independently the Glean design; file-granularity incremental
replace matches Glean/stack-graphs. Do **not** adopt line-level node granularity
(RepoGraph-style) as the primary model - it explodes node count and breaks the
stable-ID strength; borrow its *ranking*, not its granularity. Do **not** adopt
Glean's storage engine - that solves Meta-monorepo scale, not 8-repo scale.

### High-impact gaps

- **Cross-repo symbol resolution (the biggest architectural gap).** The graph is
  keyed per-repo (`(repo, id)`) and the canonical ID has no package+version
  coordinate, so a symbol exported by repo A and called from repo B does not
  link - the call edge dangles or resolves only by name collision. "Who consumes
  this API across services" is a first-order agentic-dev query that is currently
  unanswerable precisely. Fix: add a global symbol layer (extend the ID with a
  package/version coordinate, or emit/consume SCIP import/export monikers and
  stitch). Same machinery enables eager cross-file edge re-resolution (below).
  *Medium effort / very high impact.*
- **No retrieval-quality eval harness.** There is no signal for whether a graph
  or ingester change helped or hurt retrieval. Everyone serious has one (Cursor
  Context Bench; SWE-bench-Lite/RepoBench/CodeRAG-Bench recall@k). Stand up a
  small fixed `(query -> expected entity/edge set)` eval over our own repos and
  gate changes on it. *Low-medium effort / high impact - de-risks everything
  else, do this first.*
- **Graph-aware ranking missing.** We have vector chunks and recursive-CTE
  traversal but no ranking fusing them. Unranked depth-3 traversals overflow
  agent context with noise. Add PageRank / personalized-PageRank centrality
  ranking (seeded by query-matched entities) and a k-hop ego-graph route
  returning a ranked, token-budgeted subgraph. This is most of RepoGraph's
  +32.8%. *Medium effort / high impact.*
- **Partially reinventing SCIP - decide deliberately.** Don't replace the
  Postgres graph, but treat SCIP as an **ingestion interchange**: where a good
  indexer exists (scip-java for Java/Kotlin/Scala, scip-clang for C/C++,
  scip-typescript for TS/JS) consume it instead of writing an analyzer. Instant
  precise coverage for the exact "legacy/large" language targets, stays an
  ingester concern (respects the modularity rule). *Medium effort / high impact
  on language breadth.*

### Worth considering

- **Dynamic-language call precision.** Python/JS name-based call resolution is
  known-weak. Cheap first step: tag name-resolved edges with a
  confidence/resolution-quality property so the query layer and agents know
  which call edges to trust. Later: drive pyright/jedi or the TS LSP (folds into
  SCIP ingestion).
- **Eager cross-file edge re-resolution** (vs current query-time orphan
  filtering). Lazy pruning rots and makes centrality ranking wrong (PageRank
  over phantom edges). Same export-table machinery as the cross-repo layer.
- **Broken/non-buildable repos.** Go's `go/packages`+`go/types` needs a
  compiling repo; legacy/mid-refactor code often doesn't. Add a tree-sitter
  fallback tier (error-tolerant, lower-confidence edges) so we index *something*
  instead of nothing - "doesn't compile" is the normal state of legacy code.
- **Semantic-path reranker** (BM25+vector -> RRF -> cross-encoder rerank).
  Low-risk upside, gate on the eval harness.
- **Legacy languages (COBOL/Java) via the existing KG-first model.** The 2025
  COBOL->Java line of work is literally our architecture pointed at legacy; a
  tree-sitter COBOL ingester emitting the existing entity/edge schema is a
  natural extension *if legacy is a real target*.

---

## Pillar 2 - Chat: coordination bus for parallel agents

Closest analogs: **AutoGen GroupChat** (shared multi-party room) crossed with a
**blackboard** (append-only shared state). Crucially, the dominant protocols
**A2A** (Google) and **MCP** (Anthropic) are **1:1 delegation** models, not
multi-party rooms - so tatara-chat is **not** a worse A2A; it occupies a design
point A2A deliberately does not cover. Its multi-party broadcast/DM room is more
expressive than A2A for peer coordination. Defend the deliberate simplicity:
append-only Postgres is already an event-sourced, replayable substrate (do not
add Kafka/a broker); server-minted UUID handles are right (no agent-card
discovery needed); the flat peer room is a legitimate choreography choice -
keep it as transport and let a supervisor impose orchestration *on top* via
primitives, rather than baking a hierarchy into the bus.

Why this matters: the MAST taxonomy (1,600+ multi-agent traces, NeurIPS 2025)
attributes **36.9% of multi-agent failures to coordination breakdowns**, names
free-form messaging as a root cause, and flags "multiple agents believe they
control the same resource" - directly the parallel-coding conflict hazard.
Anthropic's own finding: coding is a *hard* multi-agent case precisely because
of write conflicts.

### High-impact gaps

- **Typed/structured messages instead of freetext (highest ROI).** Add a small
  mandatory performative field - ~8 acts mapped to coding coordination
  (`propose`, `claim`, `release`, `inform`, `request`, `ack`, `block`, `done`,
  `escalate`) - alongside a freetext body. A2A messages are typed (text/data/
  file parts); FIPA-ACL makes the performative the one mandatory field (but
  don't import all 22 acts). *Low effort / highest impact.*
- **`claim`/lease primitive for same-file conflicts.** A typed claim with a TTL
  lease (or a fold over claim/release events) so agents see ownership before
  starting work. This is the value chat can add that generic frameworks don't;
  MAST's prescribed fix is "each file/endpoint/table belongs to exactly one
  agent." *Medium effort / high impact.*
- **Loop / chatter / termination control.** Today the only stop is the 24h TTL,
  which is a garbage collector, not a termination condition. Add per-participant
  rate limiting, a max-messages-without-new-artifact circuit breaker, and an
  explicit room `concluded` state. Unattended agents in a shared room *will*
  ping-pong and burn tokens (Anthropic and MAST both document this; TraceFix cut
  deadlock/livelock 31%->14% but proved liveness needs *runtime* breakers).
  *Medium effort / high impact.*
- **Server-durable per-participant cursor.** Pods restart; make the last-acked
  cursor server-owned per handle so resume is O(new messages), not O(room), and
  acted-on messages aren't replayed. The append-only log already gives
  event-sourcing for free - only durable cursor ownership is missing. *Low
  effort / high impact.*

### Worth considering

- **Push (SSE) + webhook, keep polling as fallback.** Cursor-poll latency wastes
  an LLM turn per empty poll. SSE over the append log is natural (the cursor *is*
  the stream position); webhooks may fit the pod-restart model even better.
- **`escalate`/input-required as a first-class human channel** (pairs with typed
  messages) - the safety valve when agents can't resolve a conflict.
- **Expose tatara-chat as an MCP tool surface** (`chat_send`, `chat_poll`,
  `room_create`, `roster`...). From one agent's POV "post to the bus" *is* a tool
  call, so MCP is the right invocation surface while the room/typed-message/claim
  protocol stays our own. (The `spellslinger` MCP server in this environment
  already exposes exactly this shape - precedent to follow.)
- **OTel GenAI semantic-convention spans** for conversation tracing (complements
  the mandated Prometheus `/metrics`; makes "why did these agents thrash"
  tractable).
- **Presence/roster liveness** (so an agent doesn't DM a dead pod and livelock).
  Skip typing indicators - human-UX theater, zero agent value.

Do **not**: adopt A2A wholesale, add a message broker, build agent-card
discovery, or bake a fixed orchestrator hierarchy into the bus.

---

## Pillar 3 - Agents: Claude Code unattended in k8s

Verdict on the core: **PTY-driven interactive `claude`, one session per pod,
OIDC API, Stop-hook capture is sound and well-aligned** with how Devin/Copilot/
OpenHands actually run. One session per pod is the consensus isolation unit. The
PTY-over-`-p` choice is *defensible and arguably smart*, not cargo-culting,
because: (1) slash-invoked skills (`/code-review`) and built-in commands exist
**only** in interactive mode - the entire superpowers workflow depends on them;
(2) `--bare` (the recommended/`-p`-default path) strips auto-discovery of hooks,
skills, MCP, auto-memory, and CLAUDE.md - the opposite of what we want; (3) from
2026-06-15 `-p`/SDK draw from a separate Agent SDK credit pool, so PTY may be
materially cheaper at fleet scale. The gaps are all in the *control and
isolation plane around* the core, not the core.

### High-impact gaps and risks

- **Lethal trifecta: bypass-permissions + plain pods + long-lived creds.** The
  agents read untrusted content, hold real credentials, and can exfiltrate
  (Bash/WebFetch/git push) - the exact combination Simon Willison and
  Microsoft's "Agents Rule of Two" warn never to co-locate. The official Claude
  Code GitHub Action was exploited this way in June 2026 (HTML-comment injection
  -> read `/proc/self/environ` -> exfil `ANTHROPIC_API_KEY`; the Read tool runs
  in-process and bypassed the Bash sandbox). Plus two network-sandbox bypass
  CVEs. **Mitigations (assume injection will eventually succeed; bound blast
  radius):**
  - **Egress allowlist at the pod boundary** (NetworkPolicy/Cilium/egress
    proxy), not inside claude - the CVE history says don't trust the in-tool
    sandbox for egress. *Medium effort / very high impact.*
  - **Short-lived, per-repo, OIDC-minted git/API creds** via the pod
    ServiceAccount token exchanged per task (Vault JWT auth or per-run GitHub App
    installation token), fetched by a credential helper, never baked into env.
    Cuts blast radius from a durable org PAT to one repo for minutes. *Medium /
    high.*
- **Plain pods are weak isolation for arbitrary agent code.** A container escape
  reaches the node and every other agent's secrets. k8s-native fix: a **gVisor
  (`runsc`) RuntimeClass or Kata Containers** for agent pods + seccomp +
  read-only root FS + dropped capabilities. Highest security-per-effort after
  egress control. *Medium / high.*
- **Full permission bypass with no per-tool-call audit or risky-action gate.**
  Add a `PreToolUse`/`PostToolUse` hook (works on the PTY path too) that emits a
  structured audit event per tool call to the existing JSON-log pipeline and
  denies a deny-list (force-push, history rewrite, writes outside the worktree,
  non-allowlisted egress). A hook script, not a rearchitecture. *Low-medium /
  high.*
- **No durable checkpoint/resume.** A node drain/OOM/spot-reclaim at hour 3 loses
  the trajectory. Claude Code gives `--resume <session_id>` with on-disk JSONL;
  persist the session JSONL + git worktree to a PVC/object store at each turn
  boundary and re-attach on restart. Temporal (each turn an idempotent Activity)
  is the bulletproof option. *High effort / medium-high impact.*

### Worth considering

- **Structured result + cost capture.** The Stop-hook `last_assistant_message`
  grab works but is screen-scraping; augment the hook to also emit `session_id`,
  turn count, and token/cost so the API has the full structured record. (Full
  `--output-format json` presumes `-p`; stay on PTY but stop capturing prose
  alone.)
- **Per-session budget caps + kill switch** (`--max-turns` + token/cost
  tracking) for runaway control. Note the 2026-06-15 Agent SDK credit-pool change
  is itself an argument for the PTY path on subscription plans.
- **Auto-compaction is a real long-run failure mode** (documented mid-task
  firing / "conversation too long"). Use the superpowers phase boundaries
  (brainstorm/plan/TDD/subagent) to force a checkpoint + fresh context between
  phases instead of riding one session into auto-compact.
- **Fleet orchestration off Argo:** ARC ephemeral single-use runners are the
  lowest-friction path and align with Copilot's ephemeral+firewalled model;
  kagent (agents-as-CRDs for GitOps/RBAC/OTel) is the agent-native option.
- **Multi-agent merge strategy:** git-worktree-per-agent is already the right
  isolation primitive; the gap is a coordinator that partitions work to minimize
  overlapping edits before dispatch, plus a merge-queue/auto-rebase step (Devin/
  Factory both decompose by role rather than running N generalists). Pairs with
  the chat `claim` primitive.

The honest caveat on PTY-vs-SDK: what the SDK gives that the PTY doesn't is
exactly the control plane in the gaps above (native hook callbacks, structured
result/cost/session objects, programmatic resume/fork, permission-approval
callbacks). But hooks, `--resume`, and structured capture are all reachable from
the interactive/CLI path too - so keep the PTY and bolt those on; only
re-evaluate the SDK if the superpowers slash-command workflow is ever dropped.

---

## Cross-cutting themes

1. **The seams are the weakness, not the cores.** Cross-repo symbol links,
   inter-agent claim/conflict coordination, and per-agent credential isolation
   are all "between the boxes" - and all three are where fleet-scale agentic dev
   actually breaks.
2. **Assume failure, bound blast radius.** Prompt injection "may never be fully
   solved" (OpenAI, Dec 2025); the answer is egress allowlists, short-lived
   per-task creds, stronger sandboxes, and tool-call audit - not better filters.
3. **Make everything resumable.** Pods restart. Memory already replaces
   incrementally; chat already has an append log; agents need session-JSONL
   checkpointing. Durable per-participant cursors and durable session state are
   the same idea in two pillars.
4. **Measure retrieval and coordination quality.** Memory needs a recall@k eval
   harness; chat needs OTel conversation traces. Today neither has a regression
   signal.
5. **Borrow ideas from A2A/SCIP/AutoGen; adopt none wholesale.** Each is built
   for a different point (1:1 vendor delegation; Meta-scale indexing; in-process
   group chat) than tatara's homogeneous in-cluster fleet. The tatara bets are
   right; steal the primitives, not the engines.

Full per-pillar source citations are in the research-stream outputs captured in
this session's transcript.
