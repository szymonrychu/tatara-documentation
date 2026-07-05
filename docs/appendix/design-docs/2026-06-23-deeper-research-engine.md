# Tatara Deep-Research Capability: Architecture Recommendation

## Decisions (2026-06-23, Szymon)

1. Egress: broad `ipBlock: 0.0.0.0/0` (reuse existing deploy-sample policy). Accepted blast-radius: brainstorm-pod bot PAT + OIDC creds exfiltratable. Mitigation: keep search-API keys out until Phase 2; academic/Serena are the only intended destinations.
2. Pod pool: widen existing brainstorm pods (no separate research kind).
3. Search budget: academic-only first (arXiv + OpenAlex, free). Paid web search deferred.
4. Ceiling: yes, long-lived ADR/RFC artifact exempt from silence-over-noise, human-gated before implementation.

Phase 1 (this build, no egress / no secrets): new `tatara-deep-architectural-research` SKILL.md, ADR + tech-radar skill, import-graph fitness function in operator CI, `skip_research` MCP tool, env-gated Serena MCP wiring, fix dangling `skip_brainstorm`.

## 1. Bottom line

The lever is a combination, not a single thing, and it is decidedly not "just prompt engineering." Szymon's instinct is right: a superpowers-style decision-tree skill is the highest-ROI single move (Anthropic's own "Building Effective Agents" endorses a written workflow over heavier orchestration), but a skill alone cannot grow the platform because the current flow is structurally sealed off from external knowledge and structurally throttled to one shallow proposal per cycle. So the recommendation is three coordinated changes: (1) a new `tatara-deep-architectural-research` SKILL.md encoding a survey-the-field -> find-papers -> assess-fit -> propose-with-guardrails decision tree, layered on the existing ReAct loop; (2) operator-level enablement of outbound internet for a dedicated research task profile plus a research-artifact (an ADR/RFC) that is allowed to outlive a single cycle, lifting the one-issue-per-cycle and discovery-only ceilings for genuinely net-new architecture; (3) a small set of new MCP tools (web search, academic-paper, web-crawl) plus a narrow egress-allowlisted pod profile so research agents can reach the field while implement/triage agents stay locked down. The non-negotiable guardrail layer is evolutionary-architecture fitness functions in CI plus a mandatory ADR before any structural refactor, because the 2025 agentic-refactoring study [OK] proves agents drift to cosmetic renames and tangle refactors into feature commits unless hard-gated. Prompt text changes nothing about the fact that today no external knowledge can physically enter the loop; that is an infrastructure and tooling fix, gated on a security decision Szymon owns.

## 2. The gap today

The brainstorm engine is a real fan-out orchestration with a real decision tree already (`tatara-deep-research/SKILL.md` Workflow steps 1-6: Orient -> Map state -> Score leverage -> Dedup/decide -> Compose -> File), dispatched per-repo via subagents. So the "superpowers decision tree" the user pointed at largely exists. What it cannot do is the entire problem:

- **No external knowledge can enter.** Both research skills (`tatara-deep-research`, `tatara-research-followup`) are graph-plus-on-disk only - memory MCP `query`/`describe`/`code_*` and the one repo on disk. There is no web, literature, paper, or comparable-architecture step anywhere. The system can harden what exists; it cannot pull in SOTA techniques or a "generic SCM interface" pattern it has never seen described.
- **The seam exists but is wired to nothing.** `proj.Spec.Scm.Brainstorm.Sources []string` (`project_types.go:201`) plus the `tatara.io/egress: internet` label stamp (`pod.go:478`, `hasInternetSource` `pod.go:632`) is the only infrastructure hook toward outside data. The live project even sets `sources: [docs, memory, internet]`. But no skill consumes it, and the `tatara-egress-internet` NetworkPolicy granting `ipBlock: 0.0.0.0/0` is a kubectl-applied deploy-sample, not chart-templated - its live application is unverified.
- **Leverage ranking is explicitly maintenance-biased.** `tatara-deep-research` step 3 ranks reliability of the live loop, planned loop features, the Phase-9 backlog, and deploy debt above all else. "Grow the architecture" is nowhere in the priority order, and an eval-harness gate hard-blocks a whole class of quality research.
- **Structural throttles forbid ambition.** One action per run, one issue per cycle, project-wide `MaxOpenProposals` cap (`projectscan.go:890`), discovery-only with silence-over-noise. A net-new subsystem cannot be expressed as a multi-issue epic; the only escape (systemic-id, <=6 issues counting as one) is framed around cross-cutting maintenance, not net-new design. Output is always an issue, never an RFC/spike, and the body is forbidden from listing open questions - the opposite of what exploratory architecture needs.

**Single biggest blocker: no outbound internet for the agents that would do research.** Internet egress is gated to one pod label the operator stamps only on brainstorm tasks with `internet` in sources; implement/triage/review agents are confined to in-cluster 443 by the `managed-pods` policy (`managedSelector: {}` matches cluster namespaces, not the open internet). Everything else is downstream of this. A perfect research skill on a pod that cannot reach `arxiv.org` is inert.

## 3. What the field offers (ranked levers)

Ranked by ROI for tatara given its hard constraints (frozen model, headless, GitOps-only, KISS). Verification status carried through honestly; note the entire self-improving/open-ended strand is `[UNVERIFIED]` and must be treated as directional, while the reasoning-frameworks and evolutionary-architecture strands are `[OK high]`.

**Tier 1 - adopt now, proven, low effort:**

1. **Superpowers-style SKILL.md as SOP/decision tree** (Anthropic Agent Skills + superpowers, `[OK high]`; Building Effective Agents, `[OK high]`). A written workflow the model follows, progressively disclosed so it costs nothing until triggered. This is exactly the lever the user named and the one Anthropic's guidance endorses over heavier frameworks. Effort: low (one SKILL.md). Honest caveat: superpowers' impact is anecdotal, so pair with a verification gate.
2. **Evolutionary Architecture + automated fitness functions** (Ford/Parsons/Kua, `[OK high]`). CI-encoded architectural invariants that both DETECT coupling (a fitness function that fails when core code imports a vendor SDK outside an adapter) and GATE every agent refactor PR. This is the governing anti-churn frame. Effort: low-medium (CI rules, ast-grep/`go list` import checks).
3. **Strangler Fig + Ports-and-Adapters + Anti-Corruption Layer** (Fowler / Cockburn / Evans, all `[OK high]`). The execution shape and target structure for "abstract beyond github/gitlab": define an `SCMProvider` port in tatara's own domain types, wrap existing GitHub calls in a `GitHubAdapter` with zero behavior change, route through the seam, then add `GitLabAdapter` incrementally. Each step shippable and reversible. Effort: medium (the actual refactor), but the methodology is free.
4. **ADRs + Technology Radar as steering** (Fowler, `[OK high]`). Every agent-proposed structural refactor emits an ADR (markdown, same spirit as MEMORY.md entries) before code: context, options, decision, consequences. A checked-in radar whose "hold" ring forbids new direct vendor imports in core. tatara's existing bot/MR review flow IS the Architecture Advisory Forum. Effort: low.
5. **Reflexion-style self-critique on failure** (`[OK high]`). On a failing test/CI/review, write down why before retrying once. tatara is feedback-rich; this is the single highest-ROI reasoning add. Effort: trivial (one skill step).

**Tier 2 - adopt selectively, proven where a hard metric exists:**

6. **AlphaEvolve evaluator-gated evolution** (`[UNVERIFIED]` as a system, but the mechanism is sound and the production claims are specific). Only works where tatara can write a deterministic fitness function: chart/image size, CI duration, test coverage, p99/error-rate from Grafana MCP, the laneOccupancy heuristic already flagged broken in MEMORY. Mark an evolvable block, LLM proposes diffs, gate strictly on the measured metric against a canary. The breadth/depth two-model split maps cleanly to tatara's "Sonnet implements, Opus merges." Do NOT apply where there is no hard metric - that is unguided churn. Effort: high.
7. **Co-scientist tournament for brainstorm admission** (`[UNVERIFIED]`, but pure orchestration over the existing stack). Generation agents per repo -> Reflection agents adversarially review for feasibility/tech-debt -> Ranking tournament (Elo) so only top-ranked proposals become tasks. Gives a principled replacement for `MaxOpenProposals` arrival-order admission: admit by rank. Effort: medium.
8. **OMNI interestingness + learnability filter** (`[UNVERIFIED]`, but it is just a Claude prompt - zero new infra). Two-factor gate on proposal admission: is it genuinely novel vs a near-duplicate (semantic dedup, generalizing the existing presence-dedup), and is it shippable now. Effort: low.

**Tier 3 - design boundaries and explicit no-gos:**

9. **DGM / ADAS / Voyager versioned archive of skills+prompts with empirical fitness** (`[UNVERIFIED]`). The closest 1:1 template for tatara growing itself - git already gives the lineage. Concept (a `SKILLS_ARCHIVE` indexed by measured win-rate, keep non-best variants as stepping stones, compose rather than reinvent) is adoptable; the full self-modifying loop is a Phase-4 ambition, not a near-term bet. Effort: high, speculative.
10. **SEAL** (`[UNVERIFIED]`) - explicit NEGATIVE boundary: requires weight updates, out of scope for frozen Claude. Adopt only the reward-gated-self-edit concept in artifact-space, reject the mechanism.
11. **Godel Agent** (`[UNVERIFIED]`) - cautionary maximum-freedom end. Adopt the framing (the agent's own config is in scope for improvement) but route every self-edit through the existing gated MR flow; never live self-patch. Mirrors tatara's existing "deploy only via tatara-helmfile" rule.
12. **Tree search family (ToT/GoT/LATS), all `[OK high]` on their narrow benchmarks** - do NOT build. Gains are on search-shaped puzzles, not open-ended architecture; cost scales with branching factor, and LATS branches real-world side effects (CI, MRs) which is operationally dangerous for a repo-mutating headless agent. The affordable slice (ask for 2-3 candidates and self-rank, one extra turn) captures most of the value, and the decomposition output contract already in `projectscan.go` does exactly this.
13. **DSPy** (`[OK high]`) - later-stage. Requires a labeled eval set tatara does not have for research quality. Revisit once there is a rubric judge or MR-accept-rate metric.

**Most-actionable cluster:** a deep-research SKILL.md (lever 1) walking the Strangler/Ports/ADR methodology (3+4), gated by fitness functions (2) and an OMNI-style novelty filter (8), with Reflexion (5) on failures. Everything in Tier 1 plus levers 8 is shippable without the speculative self-improving machinery.

## 4. Recommended design

### (a) New skill: `tatara-deep-architectural-research`

A new SKILL.md (injected wholesale like all others via `skills.go:15-32`), named by a new brainstorm goal variant in `brainstormGoalProject()` (`projectscan.go:1107`). It walks this decision tree (an AlphaCodium-style fixed flow, `[OK high]`, shaped to the task):

```
1. SCOPE      pick ONE pain-point from repoStateCtx / MEMORY / ROADMAP / a failing
              fitness function. State it as a problem, not a solution.
2. MAP INWARD memory MCP query/describe/code_* across repos + on-disk read.
              Establish what tatara does today and where the coupling/debt lives.
3. SURVEY     <-- NEW. web-search + academic-paper MCP. Find existing systems,
   THE FIELD  patterns, papers addressing this class of problem. Collect citations.
4. ASSESS FIT score each candidate against tatara's hard constraints (frozen model,
              headless, GitOps, KISS, no tech-debt). Reject what needs training or
              live self-patch. 2-3 surviving options with tradeoffs.
5. NOVELTY    OMNI gate: is this genuinely novel vs past proposals (semantic dedup),
   + LEARN    and shippable now given repo state? If not -> skip_research(reason).
6. SYNTHESIZE produce an ADR/RFC artifact: problem, evidence (file:line), 2-3 options
              with citations, a recommended option, a strangler migration sketch,
              and the fitness function that would gate it. Open questions ALLOWED here
              (unlike the issue body).
7. PROPOSE    file the ADR-backed proposal via propose_issue (systemicId if multi-repo);
              operator parks in Conversation for human approval. Never self-implement.
```

Steps 3-4 are the net-new external-research stage. They attach exactly where INPUT A identified: between "Map current state" and "Score leverage." A research subagent fans out (Anthropic orchestrator-worker pattern, `[UNVERIFIED]` but the design tatara already uses): effort-scaled per the contract - 1 agent for a fact-check, 2-4 for a comparison, more for a genuine survey - with explicit fan-out caps in the prompt so it does not run away (tatara's laneOccupancy/effort machinery enforces the ceiling).

### (b) Operator-level changes

- **A distinct research task kind** (e.g. `Kind: "architectural-research"`) so `hasInternetSource`-style egress and a longer multi-turn budget attach to research without loosening implement/triage/review pods. This is the clean alternative to widening `hasInternetSource` across all kinds (which would grant internet to code-writing agents holding write secrets - see section 5 blast radius).
- **A research artifact that outlives one cycle.** Today output is always a single issue, capped to one approval gate, forbidden from listing open questions. Add an ADR/RFC artifact (a markdown file proposed into the repo, or a long-lived discovery issue exempt from the silence-over-noise rule for a bounded number of cycles) so an architectural thread can be developed and championed across cycles rather than proposed once and abandoned. This directly lifts ceilings 3, 4, and 6 from INPUT A.
- **Admission by tournament rank, not arrival order.** Where `MaxOpenProposals` (`projectscan.go:890`) currently skips the whole cycle at cap, replace arrival-order with a co-scientist-style Elo rank so the cap admits the best proposals, not the first.
- **Fix the stale `skip_brainstorm` reference.** INPUT A confirms `skip_brainstorm` is named in the prompt (`projectscan.go:1122`) but has no matching MCP tool in tatara-cli. Either wire the tool or route honest no-yield through the existing `decline_implementation` pattern. The new skill needs a real `skip_research(reason)` terminal action.

### (c) Generic SCM interface, proposed safely

The agent does not rip out github/gitlab. Following Strangler + Ports + ACL (`[OK high]`):

1. A fitness function (import-graph check via ast-grep / `go list`) FAILS when operator/wrapper core imports `go-github` or a gitlab client outside a designated adapter package. This failing function is the trigger that tells the agent a hardcoded coupling exists.
2. The agent files an **ADR** proposing an `SCMProvider` port in tatara's own domain vocabulary (`GetIssue`, `PostComment`, `GetPRState`, `SetLabel`, `ApproveMR`) returning tatara types, never `github.PullRequest` or `gitlab.MergeRequest`.
3. A pure, behavior-preserving strangler PR: introduce the port, wrap existing GitHub calls in `GitHubAdapter` (the facade/ACL), route the live path through the seam. Zero behavior change. The adapter is where provider quirks already in MEMORY live (PR `#` vs MR `!` addressing, gitlab owner-split 404s, sender!=author egress gate) - so they never re-pollute the core.
4. Later, separately-reviewed PRs add `GitLabAdapter` and migrate remaining direct call-sites one seam at a time. Mechanical call-site rewrites go to a deterministic codemod (ast-grep, which tatara already mandates) fanned to parallel sonnet subagents, merged by opus - exactly CLAUDE.md rule 7.

### (d) Guardrails so growth stays reviewable

The 2025 agentic-refactoring study (`[OK high]`) is the evidence basis: agents skew to low-level renames (30.7% of all agentic refactorings were variable/parameter renames), tangle 53.9% of refactors into non-refactor commits, and achieve negligible smell reduction. So:

- **Commit hygiene gate:** a refactor PR contains ONLY the refactor - no feature changes, no new endpoints, no touched behavior tests. CI-enforced.
- **Fitness functions as the smell-detection tool:** give the agent the import-graph function so it targets high-value port extraction instead of drifting into renames.
- **Behavior validation:** the `SCMProvider` extraction must prove behavior-preserving - existing suite green, ideally a golden-output diff of operator reconcile against recorded fixtures - before the ADR/PR merges.
- **Intent declaration:** the agent OPENS with the ADR stating refactoring intent (agents do far more and better-bounded refactoring when intent is explicit), creating the audit trail.
- **Human-gated structural change:** keep discovery-only. The ADR is a proposal; the existing operator Conversation-park-until-human-approval flow is the Architecture Advisory Forum. No self-implementation of an unapproved tatara-authored issue (the existing `<!-- tatara-authored -->` + no-trigger-label mechanism already enforces this).
- **Proposal caps stay, admission changes:** the OMNI novelty filter prevents semantic near-duplicates; the tournament admits by value not arrival.

## 5. Permissions + MCP tooling shopping list

The pivotal answer from INPUT A: under `bypassPermissions` defaultMode with no `allowedTools` allowlist set anywhere live, agents can already run any built-in Claude Code tool except the three denied pickers - **including WebSearch and WebFetch**. The wrapper has zero WebSearch/WebFetch/proxy code; egress is governed entirely by NetworkPolicy. So the gate is network, not tool-permission. The shopping list is therefore mostly egress and MCP servers, not settings.json changes.

| Item | What it unlocks | Egress / auth / cost / blast-radius |
|---|---|---|
| **Outbound internet for the research pod profile** | The single enabling change. Without it everything below is inert. | Stamp `tatara.io/egress: internet` on the new `architectural-research` kind (not all kinds). **Verify `tatara-egress-internet` NetworkPolicy is actually applied** (`kubectl get netpol tatara-egress-internet -n tatara`) - it is an un-templated kubectl deploy-sample. Prefer an egress PROXY with a domain allowlist over `ipBlock: 0.0.0.0/0`: research agents hold a live Anthropic OAuth token, a writeable bot SCM PAT, and shared OIDC backplane creds. Broad outbound 443 is the difference between "can read docs" and "can POST these three secrets anywhere." Blast radius of leak: platform-wide compromise (PAT pushes to every enrolled repo, OIDC drives operator + knowledge graph). |
| **Built-in WebFetch / WebSearch** | Already permitted by settings.json; just needs the egress above. Lowest rung for static/server-rendered pages. | No new secret. Egress to whatever the allowlist permits. Cannot render JS pages (use crawl MCP for that). |
| **Web-search MCP (Brave or Tavily)** | General field survey. Brave = flat per-request billing, own index; Tavily = LLM-ready snippets + extract/crawl in one server, the default in gpt-researcher. All `[UNVERIFIED]`. | Self-host the npx server in-cluster (NOT the hosted remote) so only that one pod egresses to a single vendor host (api.search.brave.com / api.tavily.com). API key in a sops Secret, mounted `envFrom` per tatara's no-plain-ENV rule. Cost ~$2.50-$8 / 1000 searches; dwarfed by the ~15x LLM token cost of multi-agent research. |
| **Academic-paper MCP (arXiv + OpenAlex)** | The "find scientific papers to adopt" capability the user explicitly wants. arXiv = papers; OpenAlex = citation graph arXiv lacks. Both `[UNVERIFIED]`, both FREE, key-optional. | Egress to export.arxiv.org + arxiv.org (PDFs) and api.openalex.org. No billing, only politeness rate limits (~1 req/3s arXiv; set a mailto for OpenAlex's faster pool). arXiv MCP needs a writable PVC (it caches PDFs/markdown). Add Semantic Scholar (TLDR summaries, influence-weighted citations) or PubMed only if needed; free keys raise throughput. |
| **Web-crawl MCP (self-hosted Firecrawl)** | Read JS-rendered pages and crawl sites that WebFetch/curl cannot. `[UNVERIFIED]`. | Self-host in-cluster: only the Firecrawl pod egresses to target sites; MCP and Firecrawl talk over ClusterIP. Note open issue: self-hosted may still demand `FIRECRAWL_API_KEY` set (use a dummy). Keeps the deep-research loop's egress narrow. |
| **Serena (code-intelligence MCP)** | IDE-grade symbol-level navigation/edit across the 7 repos - find references, go-to-def, replace one function body. `[UNVERIFIED]`. | **ZERO egress, no API key, no internet** - runs LSP servers locally against on-disk repos. The standout safe win. Complements graphify (persistent graph) with live symbol queries. Useful for the strangler call-site rewrites. Deployable to ALL agent profiles, including locked-down implement pods. |
| **Grafana MCP (already wired)** | The hard-metric source for any AlphaEvolve-style evaluator (p99, error-rate, resource-vs-actual). | Already merged when `TATARA_GRAFANA_MCP_URL` set (`bootstrap/mcp.go:49`, `pod.go:427`). In-cluster, no new egress. |

**Safe vs needs-isolation:** research agents go on a **separate egress-allowed pod profile** (the new `architectural-research` kind) with a domain allowlist and, ideally, a scoped-down token set for research-only turns. Implement/triage/review/MRCI agents - the ones holding write secrets and running unattended Bash - stay locked to in-cluster 443. Serena and Grafana MCP are safe everywhere. Do NOT widen `hasInternetSource` across task kinds; that hands internet to code-writing agents with the full secret set, which is the exact blast-radius INPUT A warns against.

## 6. Phased rollout

**Phase 1 - skill + fitness functions (lowest risk, no egress, no new secrets). Repos: wrapper, operator.**
- New `tatara-deep-architectural-research/SKILL.md` walking steps 1-2 + 4-7 (the inward-facing slice: survey-the-field stub uses memory graph only for now). wrapper `templates/skills/`.
- ADR template + a checked-in Technology Radar in each repo.
- Import-graph fitness function in CI (ast-grep / `go list`) that fails on direct vendor-SDK imports outside an adapter package. Commit-hygiene gate for refactor PRs.
- Deploy Serena MCP (zero egress) to all profiles via cli `.mcp.json`.
- Fix the stale `skip_brainstorm` reference; add `skip_research` terminal action.

**Phase 2 - external research enablement (the unlock). Repos: operator, cli, tatara-helmfile.**
- New `architectural-research` task kind in the operator; stamp egress label only on it; verify/template the `tatara-egress-internet` policy (or replace with an egress proxy + domain allowlist).
- Self-host web-search (Tavily/Brave) + arXiv + OpenAlex + Firecrawl MCP servers in-cluster; wire `code_*`-style proxy tools into cli MCP registry (`tools.go`). Secrets via sops.
- Activate steps 3 (SURVEY THE FIELD) and 4 (ASSESS FIT) in the skill with citations.
- Research artifact (ADR/long-lived discovery issue) that survives across cycles, exempt from one-issue-per-cycle for net-new architecture.

**Phase 3 - the SCM-abstraction refactor as the first proof. Repos: operator (+ wrapper as it shares SCM calls).**
- Agent runs the full skill against the github/gitlab coupling: fitness function trips -> ADR proposing `SCMProvider` port -> human approves -> strangler PR (port + `GitHubAdapter`, behavior-preserving, golden-diff gated) -> later `GitLabAdapter` via ast-grep codemod fanned to sonnet subagents, opus merge.
- This validates the whole pipeline end-to-end on a real, named target.

**Phase 4 - measured self-improvement (ambitious, gated on Phase 2-3 working). Repos: operator, cli.**
- Co-scientist tournament admission replacing arrival-order at `MaxOpenProposals`; OMNI novelty+learnability gate on the queue.
- AlphaEvolve-style evaluator loop ONLY where a deterministic metric exists (chart size, CI duration, the broken laneOccupancy heuristic), canary-gated on Grafana metrics.
- A versioned skills+prompts archive (DGM/ADAS concept) with measured win-rate, every accepted change an immutable git-lineage node, non-best variants kept as stepping stones. All self-edits route through the existing gated MR flow - never live self-patch.

## 7. Open questions for Szymon

1. **How much egress, and via what mechanism?** Domain-allowlisted egress proxy (safer, more setup) vs the existing `ipBlock: 0.0.0.0/0` deploy-sample (simpler, broad)? Given research pods hold the bot PAT + OIDC + Anthropic token, I recommend the proxy. Decision needed before Phase 2.
2. **Separate research pod pool, or widen existing brainstorm pods?** I recommend a distinct `architectural-research` kind with a scoped-down token set so code-writing agents never get internet. Confirm this is acceptable operational complexity vs reusing brainstorm pods.
3. **Paid search API budget.** Self-hosted Brave/Tavily are ~$2.50-$8 / 1000 searches, dwarfed by the ~15x LLM token cost of multi-agent research. Free academic MCPs (arXiv/OpenAlex) may suffice for the paper-finding goal alone. Do you want paid web search at all in Phase 2, or academic-only first?
4. **Lift the discovery-only / one-issue-per-cycle ceiling for net-new architecture?** Growing the platform structurally needs a research artifact that can be championed across cycles. Are you comfortable with a long-lived ADR/RFC thread exempt from silence-over-noise, still human-gated before any implementation?
5. **Is the eval-harness gate negotiable?** The hard stop ("do NOT propose memory ranking work before the eval harness exists") blocks a class of quality research. Should the new research skill be allowed to propose the eval harness itself as its first net-new target, or does that prerequisite stay human-owned?