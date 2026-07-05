# Research-Grounded Improvements - Design

**Date:** 2026-06-06
**Status:** design approved; pending spec review
**Spans:** `tatara-memory` (M1-M5, with the ingester `tatara-memory-repo-ingester`
for M2/M3/M5) and `tatara-chat` (C1-C4)
**Source research:** `docs/2026-06-06-agentic-dev-sota-improvements.md` (SOTA
comparison) plus full reads of the primary papers cited per sub-task.

## Problem

The SOTA comparison surfaced a backlog of improvements. This spec captures the
subset with a **research-paper grounding** (plus closely-related interchange
standards), as a single decomposed stream. The paper-backed improvements land
entirely in two pillars: **memory** (code search) and **chat** (agent
coordination). The agent-wrapper items (lethal trifecta, sandboxing, durable
resume) are industry/security-grounded, not paper-grounded, and are out of scope
here - they remain in `ROADMAP.md` Phase 9.

This is a **program spec**: it defines each sub-task and its contract in enough
detail to become its own per-repo plan. It is not a single cross-repo
implementation plan. Memory and chat are independent repos and proceed as two
parallel tracks. Within each track, one sub-task is foundational and ships first
(M1 eval harness gates every memory change; C1 typed messages is the substrate
the other chat changes build on).

## Design principles drawn from the papers

The papers consistently say *do not over-build*. These constraints bind the
whole spec:

- **Shallow wins.** RepoGraph's 1-hop ego-graph (`+flatten`) beats 2-hop on
  SWE-bench-Lite (29.67% vs 26.00%) at a fraction of the tokens. Default
  traversal depth is 1; expand on demand.
- **Typing reduces ambiguity, not derailment.** MAST is explicit that
  communication-protocol fixes are "often insufficient for FC2 failures, which
  demand deeper social reasoning." Typed messages are sold as an
  ambiguity/duplicated-work fix, not a coordination cure-all.
- **Liveness is undecidable at design time.** TraceFix proves agent protocols
  "routinely contain nondeterministic loops that are valid business logic," so
  termination must be enforced by **runtime circuit breakers**, never by a
  static guarantee. Safety (mutual exclusion, no orphan locks) *is* statically
  provable and is enforced in the DB.
- **One mandatory field.** FIPA-ACL's single durable lesson across 25 years is
  that the **performative is mandatory and everything else is optional**.
- **Fixed verb set over generated queries.** CodexGraph's LLM-emits-Cypher loop
  collapsed CrossCodeEval EM by 19.6pp when the translation agent was removed and
  inflated tokens ~5x; a fixed retrieval verb set (which tatara-cli already
  exposes as `code_*` MCP tools) avoids that fragility. We do **not** add
  LLM-generated graph queries.

---

# Track 1 - Memory (`tatara-memory` + ingester)

## M1 - Retrieval-quality eval harness (foundational; ship first)

**Why:** there is currently no signal for whether an ingester/graph/ranking
change helped or hurt retrieval. Every other memory sub-task needs this as a
regression gate, and M3's confidence numbers are *calibrated* by it.

**Grounding:** RepoBench (acc@k for cross-file retrieval, XF-F/XF-R/IF
stratification), CodeRAG-Bench (NDCG@10; BM25 collapses to 5.2 on NL queries -
dense/graph must beat lexical), CodeSearchNet (MRR over 999 distractors + 1 gold;
docstring-first-paragraph -> function as zero-annotation query/gold; NBoW beats
BiRNN - always validate against a dumb baseline), SWE-bench-Lite (gold-symbol-in-
context recall@k; BM25 file recall ~56.7% @3 -> ~86.7% @30), TypeEvalPy
(small/curated/category-stratified philosophy).

**Two tracks:**

*Track A - call-edge precision.* Gold set built from Go, where `go/types` gives
correct call edges. Run the name-based resolver over the same code (and a small
hand-labelled Python/JS slice), compare to the type-resolved gold. Per
`resolution` bucket (see M3), compute precision (correct/emitted) and recall
(gold found/gold total). Outputs: (a) validates the confidence ordering, (b)
writes back *measured* confidence values. Minimum ~200-300 gold edges spread
across buckets.

*Track B - retrieval quality.* Frozen query->gold fixture, ~100-200 pairs,
auto-generated CodeSearchNet-style: each documented symbol's docstring/leading
comment (first paragraph) is the query; gold = that symbol's entity ID. Metrics:
recall@{1,3,10} (primary, single/few-gold), MRR over a fixed 1000-candidate pool,
NDCG@10 where a query has multiple graded golds. Mandatory BM25/lexical baseline
column. Stratify the report by same-file / cross-module / dynamic-construct.

**Mechanism:** a standalone command (Go, in the ingester or a small eval tool)
that reads a committed `eval/fixtures/*.json`, queries a running tatara-memory,
emits a `eval/baseline.json` of metrics. CI gates: no metric regresses more than
a configured threshold (default 2%) vs the committed baseline; failing below the
BM25 column on any slice is a hard alarm. Smallest trustworthy version: ~100-200
Track-B pairs + ~200 Track-A gold edges + BM25 baseline.

## M2 - Cross-repo symbol resolution (highest impact)

**Why:** the graph is keyed per-repo (`(repo, id)`) with no package/version
coordinate, so a symbol exported by repo A and called from repo B does not link.
"Which services consume this API" is unanswerable precisely. This is the headline
agentic-dev query for a multi-service codebase.

**Grounding:** stack-graphs ("Stack Graphs: Name Resolution at Scale"). Their
mechanism is precomputed **partial paths** (per-file path fragments with a
symbol/scope-stack signature) resolved at query time by **stitching** (concatenate
fragments whose stacks match) through a shared root - file-incremental, no build
invocation, cross-repo is the same operation as cross-file. Because tatara's IDs
are already FQN-canonical and commit-stable, the common case (import/export/call
across repos) reduces from the full two-stack traversal to a **relational
equi-join on the canonical symbol**.

**Mechanism:** new table
`cross_repo_symbols(symbol text, lang text, kind text, role text, repo text,
entity_id text, src_file text)` with `role in ('provides','requires')`, indexed
on `(symbol, lang, role)`. Populated during ingest from data already computed
(FQNs): each exported definition emits a `provides` row; each unresolved
cross-repo reference emits a `requires` row. Cross-repo edges
(`references`/`imports`/`depends_on`/`calls`) are materialized by
`INSERT ... SELECT` joining `requires` to `provides` on `(symbol, lang)`. Go
emits provides/requires directly from `go/types`-resolved symbols; Python/JS use
name-based FQN matching (already their resolution model). File-incremental for
free: re-ingest replaces only that file's `cross_repo_symbols` rows, same as the
current file-granularity edge replace. Add per-file content-hash skip so
unchanged files don't re-emit rows.

**Explicitly not doing:** the full push/pop-symbol + scope-stack +
jump-to-scope state machine. It only buys dynamic member-access resolution that
the name-based ingester cannot achieve anyway; Go is already type-resolved.

## M3 - Call-edge confidence model

**Why:** Python/JS call edges are name-based and imprecise; today nothing tells
the query layer or an agent which edges to trust. M2's cross-repo join also needs
a way to mark its name-matched results.

**Grounding:** TypeEvalPy (18-category taxonomy of where Python type/call
resolution breaks; Jedi infers parameters at 0/88, Pyright 8/88; best static tool
~67% exact / ~44% sound - so a bare name-matcher is strictly below that, with
error concentrated in `dynamic`/`decorators`/`external`/`imports`/`mro`). Typify
(usage-driven inference; the signal "callee resolved through import+def graph with
bound args" is the high-precision path - steal the signal, not the symbolic-
execution engine).

**Mechanism:** store on each call edge's `properties` jsonb a discrete
`resolution` and a numeric `confidence`, plus a `degraded_by` string array.

| `resolution` | Emitted when | Prior `confidence` |
| --- | --- | --- |
| `type_resolved` | Go `go/types`; or callee resolved via import+def graph, unique def | 0.98 |
| `scoped_name_match` | unique def in same module/class scope | 0.85 |
| `imported_name_match` | resolves through a tracked import to a unique def | 0.7 |
| `global_name_match` | unique def somewhere in graph, no scope/import anchor | 0.45 |
| `ambiguous_multi_def` | name matches >1 def | 0.2 |
| `unresolved` | no matching def (external/builtin/dynamic) | 0.0 (or a `dangling_call` marker, no edge) |

`degraded_by` flags the call site sitting inside a hard construct
(`decorator`, `dynamic`, `reflection`, `reexport`, `mro`, `lambda`,
`higher_order`); when non-empty, cap confidence at 0.45 even on a name match. The
numeric values are **priors**; M1 Track A overwrites them with measured precision
per bucket. Three buckets (type-resolved / single-name-match / ambiguous-or-
unresolved) is the defensible minimum; the five-way split of single-name-match by
anchor strength is justified by RepoBench's XF-F vs XF-R gap. Do not invent finer
granularity than the benchmarks support.

## M4 - Retrieval depth and serialization tuning

**Why:** unranked deep traversals overflow agent context with noise; the paper
result is that shallow + flattened is both cheaper and more accurate.

**Grounding:** RepoGraph k-ablation (1-hop+flatten 29.67% / 2,310 tok beats
2-hop+flatten 26.00% / 10,505 tok; 1-hop+summarized 28.33% / 717 tok is the
cheap-but-strong option).

**Mechanism:** default the ego-graph/neighbor traversal depth to 1 (expand on
demand via the existing depth param, still capped at 10). Add a serialization
mode at the query API boundary: `flatten` (full subgraph dump, default) vs
`summarized` (token-budgeted), with an explicit token budget. No storage change;
this is query-layer behavior. Personalized-PageRank centrality ranking is a noted
follow-on (Aider's repo-map, blog-grounded) and is out of this paper-scoped
stream.

## M5 - SCIP interchange ingestion (larger; lower priority)

**Why:** instant precise coverage for languages with no tatara analyzer
(Java/Kotlin/Scala, C/C++, TS) - the exact "legacy/large" targets - without
writing analyzers, staying an ingester concern (respects the modularity rule).

**Grounding:** SCIP (Sourcegraph's language-neutral Protobuf symbol-index format;
human-readable symbol IDs; off-the-shelf indexers scip-java, scip-clang,
scip-typescript, scip-python). A closely-related interchange standard, not a
paper.

**Mechanism:** a new analyzer in the ingester that runs/consumes a SCIP index for
a matched language and maps SCIP symbols/occurrences/relationships onto tatara's
`code_entities`/`code_edges` and the M2 `cross_repo_symbols` rows (SCIP's
import/export monikers map directly to provides/requires). No change to A's store
or C's MCP tools - language-neutral by construction. Larger effort; can be cut or
deferred without affecting M1-M4.

---

# Track 2 - Chat (`tatara-chat`)

## C1 - Typed messages / performatives (foundational; ship first)

**Why:** freetext messages force guesswork about sender intent; MAST attributes
36.9% of multi-agent failures to inter-agent misalignment and finds role/spec
strengthening yields +9.4% success. Every other chat sub-task (claim, termination,
escalate) is expressed as a performative, so this lands first.

**Grounding:** MAST FC2 fixes (explicit message typing), FIPA-ACL (performative
mandatory, everything else optional; take request->agree/refuse and
cfp->propose->accept patterns + conversation-id/reply threading, drop the
mentalistic semantics and the unused long tail), A2A (Message vs Artifact split;
Part kinds text/data/file; contextId grouping).

**Mechanism:** add a mandatory `type` field to messages. Nine-act set:

| Performative | Meaning | Attacks (MAST) |
| --- | --- | --- |
| `propose` | "I intend to do X" (announce plan before acting) | FM-1.3 repetition, FM-2.3 derailment |
| `claim` | "I take a lease on resource R" | FM-1.3 duplicated work, two-agents-one-file |
| `release` | "lease on R freed" | orphan-lock prevention, FM-1.5 |
| `inform` | "here is a fact / status / result" | FM-2.4 information withholding |
| `request` | "please do X / give me Y" | FM-2.5 ignored input |
| `ack` | "received and accepted" (carries accept/reject + reason) | FM-2.5, FM-2.2 |
| `block` | "I am stuck, I need X" (= A2A input-required) | FM-3.1, FM-2.2 |
| `done` | "my unit of work is complete (+ artifact ref)" | FM-1.5, FM-3.2 |
| `escalate` | "cannot resolve among peers, raising to human" | FM-1.5 / FM-3.1 escape hatch |

Optional fields alongside the existing freetext body: `replyTo` (message id, =
FIPA in-reply-to), `streamId` (= A2A contextId, groups related messages/claims),
and a typed `data` JSON part (= A2A DataPart) for machine-readable payloads (e.g.
a claim record). `reject` is folded into `ack` with a status field to keep the
set small (splitting into `ack`/`nack` is the one acceptable variation).
Reaching MAST multi-level verification: a `done` is "verified" only once a peer
`ack`s it (optionally with a high-level objective check - ChatDev +15.6%).

## C2 - Claim/lease primitive

**Why:** two agents editing the same file is the central hazard of parallel
coding agents; chat alone doesn't prevent it.

**Grounding:** MAST FM-1.3 (duplicated work / "multiple agents believe they
control the same resource"); TraceFix (mutual exclusion and orphan-lock absence
are the statically-provable safety properties).

**Mechanism:** a `claims` table keyed by a normalized `resource_key` (e.g.
`repo:tatara-cli/path:cmd/root.go`, or a prefix `.../internal/auth/**` for
directory claims), with a partial unique index
`UNIQUE(resource_key) WHERE released_at IS NULL` - DB-enforced mutual exclusion,
no broker. Every claim has `expires_at = now() + lease_ttl` (default 10-15 min);
the holder `renew`s while working. Expiry is lazy (checked at claim time:
`released_at IS NULL AND expires_at > now()`), preventing orphan locks when an
agent crashes; an optional sweeper appends `release` events on expiry to keep the
room view consistent. A `claim` performative appends to the room *and* upserts the
table in one transaction (no new transport - peers see claims in their normal
cursor poll). A colliding claim returns 409 with the current holder's handle +
expiry, and the attempt is logged as an `inform` so contention is visible.

**Known limitation (record in MEMORY.md):** the DB guarantees no two *successful*
claims overlap, but cannot force an agent to honor a claim before editing. Honor
is enforced by prompt contract + a post-hoc `git diff` vs held claims at
`done`/`ack` time. (Mirrors TraceFix: "the monitor enforces the interface, not
the verified state machine.")

## C3 - Termination / circuit-breakers (runtime; room-enforced)

**Why:** unattended agents in a shared room will ping-pong and burn tokens; the
24h TTL is a garbage collector, not a termination condition.

**Grounding:** TraceFix (liveness undecidable at design time -> runtime breakers
mandatory; verified protocols cut deadlock/livelock 31.1% -> 14.1%, +topology
monitor -> 8.8%); MAST FM-1.5 (unaware of termination), FM-3.1 (premature
termination); AutoGen termination primitives (MaxMessageTermination,
TextMentionTermination - room-level analogs since there is no orchestrator).

**Mechanism:** the room owns these runtime breakers:

1. Max-messages-per-stream cap -> on breach, auto-`escalate` + flip room
   read-only.
2. Per-agent repetition/re-claim throttle (N near-identical messages, or
   re-`claim` of a just-released resource, in a window) -> throttle + `block`.
3. Idle/stall timeout (no stream-advancing message for T while claims held) ->
   expire leases + `escalate`.
4. Explicit completion: a stream is done only when a `done` is `ack`ed by the
   required quorum of active peers. Reaching the 24h TTL *without* a
   quorum-acked `done` is logged as a **failure metric**, not a clean close
   (guards FM-3.1 masquerading as a timeout).
5. Quorum liveness check: periodically compare resources `done`+`ack`ed vs
   `claim`ed-but-open; stagnation across two checks -> `escalate`.

All breakers are server-side because the room is the enforcer (no orchestrator
agent). Each emits the relevant performative so the action is visible in the log.

## C4 - Block/escalate push + durable cursor (small; high-value)

**Why:** cursor-poll latency wastes an LLM turn per empty poll; pods restart and
should resume without replaying acted-on messages.

**Grounding:** A2A input-required (a significant state change that triggers a push
notification) and resubscribe (`SubscribeToTask` restores a stream after a broken
connection).

**Mechanism:** a `block`/`escalate` performative flips the targeted/blocked
participant from passive polling to an active webhook push (reusing the existing
webhook-or-poll delivery). Make the per-participant cursor server-durable
(last-acked cursor per handle) so a restarted pod resumes O(new messages) without
re-reading the room or replaying acted-on messages. The append-only log already
provides event-sourcing; only durable cursor ownership is added.

---

# Cross-cutting

- **Per-component cycles.** Each sub-task is scoped to become its own per-repo
  plan. Memory sub-tasks live in `tatara-memory` (+ ingester); chat sub-tasks in
  `tatara-chat`. This spec is the shared reference they cite.
- **Hard rules.** All new endpoints OIDC-gated (existing middleware). New
  config follows rule 6 (camelCase scalar -> kebab ConfigMap/Secret key ->
  `envFrom`; list-shaped data via templated ConfigMap). Charts stay
  cluster-agnostic (rule 14). JSON slog + INFO business-action logs (rules
  11-12). Metrics for everything that counts/fails (rule 13): eval metrics,
  cross-repo-edge counts, per-resolution-bucket edge counts, claim
  contention/expiry counters, circuit-breaker trips, performative counts.
- **Migrations.** M2 (`cross_repo_symbols`) and C2 (`claims`) add tables via the
  existing embedded-SQL `Migrate(ctx, db)` startup pattern. M3 is a jsonb
  property change (no migration). C1 adds a `type` column + optional columns to
  the messages table.

# Build order

Two parallel tracks (independent repos):

- **Memory:** M1 (eval harness, gates the rest) -> M3 (confidence, calibrated by
  M1) -> M2 (cross-repo) -> M4 (retrieval tuning) -> M5 (SCIP, optional).
- **Chat:** C1 (typed messages, substrate) -> C2 (claim/lease) -> C3
  (termination) -> C4 (push + durable cursor).

Each sub-task runs its own writing-plans -> implement cycle. At the plan step,
produce per-repo program plans (one memory, one chat) rather than one cross-repo
plan.

# Open tradeoffs (accepted)

- Confidence numbers ship as priors and are calibrated post-hoc by M1 Track A;
  acceptable because the ordering is paper-justified and the harness corrects the
  magnitudes.
- M2 uses name-based FQN matching for Python/JS cross-repo edges (tagged via M3
  as lower-confidence); full type-resolved cross-repo precision only for Go.
  Deliberate - matches the per-language resolution reality.
- Claim honor is enforced by contract + post-hoc check, not by the DB. The DB
  guarantees claim *exclusivity*, not claim *obedience*.
- Termination liveness is best-effort runtime breakers, not a guarantee (proven
  impossible at design time by TraceFix).
