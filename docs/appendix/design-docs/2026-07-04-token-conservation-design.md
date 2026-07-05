# Token conservation redesign

Date: 2026-07-04
Status: design (approved scope, pre-plan)
Scope: platform-wide (tatara-operator, tatara-cli, tatara-claude-code-wrapper,
tatara-agent-skills, tatara-observability, tatara-helmfile)

## Problem

The autonomous-agent fleet spends too many Claude API tokens. A 10-dimension
audit (workflow `wlm28e3y8`) found the spend has two multiplicative axes and
almost no dollar visibility.

**Axis 1 - price.** Every task kind (`triageIssue`, `review`, `brainstorm`
[`healthCheck` shares this Kind], `refine`, `implement`, `incident`,
`issueLifecycle`, `selfImprove`) runs `claude-opus-4-8` at
effort `xhigh`, a project-wide `Model`+`Effort` pair with zero per-kind
branching (`tatara-operator/internal/agent/pod.go:417-418`), even though
`BuildPod` already branches per-kind for tool profile, skill profile, and turn
cap. Opus is ~5x Sonnet; `xhigh` is max thinking. This multiplier rides every
input, output, and thinking token of every task, including pure-classification
triage and tool-gated grooming that need neither.

**Axis 2 - input shape.** The fleet is cache-hostile by construction:
- One pod per Task = a fresh `claude` process with an empty conversation, so
  the full stable prefix (Anthropic tool schemas ~5.3k tok + skill descriptions
  ~20k + repo CLAUDE.md ~1.8k) is re-billed at full input price on every task.
- Per-kind `TATARA_TOOL_PROFILE`/`TATARA_SKILL_PROFILE` fragment that prefix
  from byte zero (tools render before system in Anthropic's cache order), so no
  two kinds ever share cache even in the same repo.
- Long/crashed tasks replay the full conversation from S3 on resume, re-billing
  a large cold input block every time.

Task count (hourly `mrScan`+`issueScan`+`brainstorm`+`refine` on both Projects,
~2 idle pods/hour/project) multiplies both axes. Brainstorm proposal churn
(24x Task recreation on one issue observed; reaper live but inert because
`staleProposalDays` is unset) adds full opus re-runs.

**Measurement gap.** `ccw_turn_cost_usd_total` is emitted by the wrapper but
appears in zero dashboards; wrapper token metrics carry only `type`+`model`
labels (no kind/repo), and `ccw_turn_tokens_total` returned zero live samples
over 30 days. We cannot attribute spend to a kind or repo today.

## Goals

- Cut fleet-wide token spend on both axes without degrading brainstorm/refine
  ideation quality or implement/incident correctness.
- Make spend observable in dollars, per kind and per repo, before and after.
- Keep the per-phase tool-gating security boundary intact (all agents share one
  OIDC identity, so authz cannot key on identity - see
  [[agents-share-oidc-identity-authz]]).
- Sequence measure-first: nothing built blind; the deepest lever is gated on a
  measurement.

## Non-goals

- Warm-pool of long-lived `claude` processes. Rejected: it breaks the
  one-pod-per-task isolation model and needs mid-process task handoff the
  wrapper lacks. Cross-pod cache reuse is achieved by a byte-stable prefix + a
  warm cache instead, which needs neither.
- Changing the Anthropic auth model. The single shared
  `CLAUDE_CODE_OAUTH_TOKEN` is the enabler, not a target.
- Reworking the lifecycle state machine. Delegation is a prompt+settings lever,
  not an operator-orchestration rewrite (decided: "directive + cheap
  subagents").

## Key facts established (investigator `a87d1f495b5b5c435`)

1. **One shared Anthropic identity.** Every pod, every kind, every project gets
   the same `CLAUDE_CODE_OAUTH_TOKEN` from one managed secret
   (`pod.go:452`, unconditional; secret `pod.go:135-137`+`config.go:410`,
   chart `managed-secrets.yaml`). The whole fleet is one content-keyed cache
   namespace. Caveat: whether OAuth-token auth exhibits identical server-side
   cache behavior to a raw API key must be measured, not assumed.
2. **Call-time authz is feasible.** The profile string lives on the cli MCP
   server struct (`internal/mcp/server.go:26,40`); the per-tool dispatch
   closure captures both the tool and the receiver (`server.go:93-135`), so
   gating can move from registration-time (hide from `tools/list`) to
   execution-time (reject the call) while serving a byte-identical superset
   list. The mutation point is `t.Handler`/`Invoke` (`server.go:101,115`);
   guarding before it preserves the boundary.
3. **Prefix is already cache-shaped.** The per-task goal is a trailing USER
   message (`SubmitTurn` -> wrapper `POST /v1/messages` -> PTY paste), not
   interpolated into system/CLAUDE.md. `headlessDirective` is a compile-time
   const (`bootstrap.go:313-330`); model/effort/CLAUDE.md/skills are
   project-static; per-task IDs are env vars only (`pod.go:430-438`).
4. **No cache kill-switch; resume preserves context.** `claude` is launched
   with `[--model, --effort, (--continue|--resume)]` only (`session/pty.go:51-69`);
   model is fixed per pod. Cache token counts are already parsed and exported:
   `ccw_turn_tokens_total{type=cache_read|cache_creation, model}`
   (`transcript/result.go`, `metrics.go:48,124`, `session.go:963-966`).
5. **Skills install deterministically** per profile + skills-repo SHA
   (`bootstrap/skills.go`), lexical walk, content-verbatim copy. One drift
   vector: `SkillsRef` defaults to `main` (`config.go:158`, `pod.go:648-655`) -
   pin to a SHA.

**The one open risk.** The `claude` CLI injects its own environment context
(cwd, per-task git branch = issue-numbered branch, `git status`, today's date).
Whether that block sits inside or outside claude's `cache_control` breakpoint -
and therefore how much of system+skills it invalidates - is a claude-CLI
internal not answerable from the repos. The tool-schema block renders first and
caches regardless. This is directly measurable via
`ccw_turn_tokens_total{type=cache_read}` on turn-0 of a fresh pod, so component
4 (cache reclaim) is gated on that measurement.

## Design

Six components. Impact ranked; sequencing is measure-first (see Phases).

### 1. Per-kind Model + Effort tiering

Attacks axis 1. Add `modelByKind` and `effortByKind` maps to the Project CR
`AgentSpec` (kebab-case scalar keys per the no-lists-in-values rule -> rendered
into a templated ConfigMap the operator reads, or CRD map fields). `BuildPod`
branches on kind at `pod.go:417-418`, resolving `MODEL`/`EFFORT` from the map
with the existing project-level `Agent.Model`/`Agent.Effort` as fallback.

Default map (keyed on the operator's actual `Task.Spec.Kind` enum from
`pod.go:832-853`):

| Kind | Model | Effort | Rationale |
|---|---|---|---|
| triageIssue | Sonnet | low | classification |
| review | Sonnet | medium | has adversarial-verify structure; riskiest downgrade |
| brainstorm | Opus | high | ideation + proposal quality (kept); `healthCheck` shares this Kind so inherits it |
| refine | Opus | high | cross-issue grooming judgment (kept) |
| implement | Opus | high | correctness; drop `xhigh` |
| incident | Opus | high | correctness; drop `xhigh` |
| issueLifecycle | Opus | high | correctness; drop `xhigh` |
| selfImprove | Opus | high | self-improvement judgment |

`xhigh`->`high` everywhere it remains Opus: `xhigh` is max-thinking and rarely
worth the thinking-token cost outside the hardest coding runs.

Only `triageIssue` and `review` drop to Sonnet - a small kind-set but both are
high-frequency (every issue triaged, every PR reviewed), so the volume covered
is large. Risk: Sonnet parity on those two, `review` most of all. Mitigation:
A/B via the component-6 per-kind dashboards after rollout; the map is a values
change, trivially reversible per kind. `healthCheck` cannot be tiered apart
from `brainstorm` (shared Kind); accepted (both stay Opus+high).

Model IDs are authoritative from the claude-api reference: `claude-opus-4-8`,
`claude-sonnet-5`. Effort enum: `low|medium|high|xhigh|max`.

### 2. Workflow delegation + cheap subagents

Amplifies axis 1 by moving expensive output/thinking volume onto Sonnet.

- **Directive**: work-doing prompts (implement, incident) instruct the agent to
  delegate independent/bulk work to subagents via the superpowers
  subagent-driven-development / dispatching-parallel-agents skills, reserving
  the Opus turn for planning and the merge. Authored in tatara-agent-skills as
  a TASK-type directive block; injected via the existing operator
  `skillsDirective` / guidance seam.
- **Cheap subagents**: wrapper `internal/bootstrap/settings.go` sets the
  subagent default model to Sonnet and low effort in the generated
  `settings.json`, so delegated work is ~5x cheaper regardless of the agent's
  choice. (Verify the settings key claude honors for subagent model/effort at
  implementation time; if none exists, fall back to the directive naming the
  model explicitly.)

Opus agent plans + merges; Sonnet subagents do the volume.

### 3. Handoff continuation, drop S3 conversation restore

Attacks axis 2 (removes the largest cold-cache input re-bill) and makes pods
stateless.

- On pause/crash/pod-death, the outgoing pod writes a compact handoff doc (the
  `handoff` skill's structured format) to durable storage keyed by Task.
- The next pod boots fresh and receives ONLY the handoff as its opening context
  (a small variable suffix), not a replayed conversation.
- Remove the S3 conversation replay + `--resume <ResumeSessionID>` restore path
  (wrapper `session/pty.go:59-68`, `session/session.go` resume handling) and
  the operator `ResumeSessionID` / conversation-S3 wiring. `--continue`
  within a live pod is unaffected (same process, cache intact); only
  cross-pod S3 restore is removed.

Composes with component 4: the handoff is the post-prefix variable suffix that
sits after the byte-stable cached prefix.

Caveat: handoff is lossy vs a full replay. Mitigation: the handoff skill's
structure is the quality bar; a mid-implement crash re-derives from the
handoff + the branch's committed state (the work-in-progress lives in git, not
only in conversation).

### 4. Cross-pod cache reclaim (measure-gated)

Attacks axis 2 burns #2/#3/#5. Feasible per the shared-token fact. Gated on the
component-6 measurement of the claude env-context ordering.

- **a. Unify tool prefix.** Serve a byte-identical superset `tools/list` to all
  kinds. `cli/internal/mcp/server.go`: register every tool (drop the
  `if allow[t.Name]` filter at registration), precompute `s.allow =
  resolveProfile(...)` once, and guard the dispatch closure:
  `if s.allow != nil && !s.allow[t.Name] { return NewToolResultError("tool not
  permitted for this profile") }` before `t.Handler`/`Invoke`. The kind signal
  is already on the server; the security boundary moves from hide-to-reject with
  no loss (defense-in-depth already exists operator-side). `resolveProfile` is
  made fail-closed for unknown/empty profiles as part of this change (kills the
  fail-open full-surface drift).
- **b. Unify skill prefix + pin SkillsRef.** Coarsen skill profiles so the
  installed set is byte-stable across kinds (or install the superset), and pin
  `SkillsRef` to a SHA in tatara-helmfile (kill the `main` drift). Wrapper
  `config.go:158` default stays but is overridden per deploy.
- **c. Keep-warm.** Hourly cadence + 5-min default TTL = cold between cycles.
  Coalesce the scan burst so pods 2..N of a cycle hit pod-1's cache write
  (free; the operator already spawns a cycle's pods near-simultaneously).
  Optionally use 1h extended TTL (2x write cost, bridges the hourly gap) if the
  measurement shows the prefix is large and stable enough to pay off.

Acceptance for 4: `ccw_turn_tokens_total{type=cache_read}` is non-zero on the
turn-0 of a fresh pod that follows another same-(repo,model) pod within TTL.

### 5. Cheap adjacents

Low-effort, high-ROI, independent of the above:
- Stretch `mrScan`/`issueScan`/`refine` cadence hourly -> every 2-4h in the
  tatara-helmfile Project values (webhook path covers real activity, so
  responsiveness loss is bounded).
- Set `staleProposalDays=14` on both Project CRs to activate the already-live
  reaper and stop brainstorm proposal churn at the reaper backstop; also gate
  brainstorming-labelled issues on the human-activity check so churn stops at
  source.
- Fix the `implementPrompt` double-append bug (`controller/lifecycle.go`
  re-appends `platformProblemGuidance` + `toolingConsumeGuidance` already added
  by `planTurnText`) - ~219 tok/Implement-turn-0, one-line fix.
- Add a per-task token/cost ceiling for the turn-uncapped implement/issueLifecycle
  (`task_controller.go` `turnCap` returns `(0,false)` for these) as a runaway
  backstop, threshold set from the component-6 telemetry.

### 6. Measurement (prerequisite)

- Surface `ccw_turn_cost_usd_total` in a $-per-kind/per-repo panel in
  tatara-observability (exists in code, zero panels today).
- Add `kind`/`repo`/`project` labels to the wrapper token+cost metrics
  (`internal/metrics/metrics.go` - today only `type`+`model`), sourced from the
  pod env the operator already sets.
- Add a cache-hit-ratio panel: `cache_read / (cache_read + input)` per kind.
- Confirm the operator-side per-kind token family
  (`operator_task_tokens_total`) is actually emitting (dashboard note says
  "populate after A1/A2 deploy", never confirmed) or wire it.

This is the acceptance instrument for 1-5 and the gate for 4.

## Phases / sequencing

- **P0 (now, no dependencies)**: component 6 (measurement) + component 1
  (tiering) + component 5 (adjacents). Fast, safe, independently deployable;
  begins the empirical baseline.
- **P1**: component 2 (delegation) + component 3 (handoff/drop-S3). Depend on
  the P0 telemetry to size effort and validate the delegation cut.
- **P2**: component 4 (cache reclaim), gated on the P0 env-context measurement.
  4a (superset tools + fail-closed) can ship even if the env-context poisons
  system+skills, because the tool block caches regardless; 4b/4c only if the
  measurement shows system+skills headroom.

## Cross-repo change list

| Repo | Change |
|---|---|
| tatara-operator | `modelByKind`/`effortByKind` on AgentSpec + `pod.go` branch; drop S3 conversation restore + `ResumeSessionID` wiring; write/read handoff at pod boundaries; cadence-driving fields; `staleProposalDays` consumption; `implementPrompt` double-append fix; per-task budget cap; kind/repo labels on the pod env for metrics |
| tatara-cli | superset `tools/list` + call-time authz guard + fail-closed `resolveProfile` (`internal/mcp/server.go`, `profiles.go`) |
| tatara-claude-code-wrapper | subagent Sonnet/low-effort in `settings.json` (`bootstrap/settings.go`); handoff-based continuation (`session/pty.go`, `session/session.go`); `kind`/`repo` metric labels (`internal/metrics`) |
| tatara-agent-skills | delegation directive (TASK-type) + handoff-continuation directive; coarsen skill `profiles:` for byte-stable prefix |
| tatara-observability | $-cost per-kind/repo dashboard + cache-hit-ratio panel + alert re-baseline |
| tatara-helmfile | Project CR `modelByKind`/`effortByKind` maps; cadence values (2-4h); `staleProposalDays=14`; pin `SkillsRef` to a SHA |

## Deploy path

Per the hard rules: each component repo merges to `main` (CI builds+pushes
image/chart), then a tatara-helmfile MR bumps BOTH the chart version and the
pinned `image.tag` per changed release, reviewed via diff and applied by the
pipeline. Project CR value changes (tier maps, cadence, staleProposalDays,
SkillsRef) are tatara-helmfile values edits. No `kubectl set image`/`patch` to
ship. The CLAUDE.md/MEMORY.md edits that codify the new tiering + handoff-only
policy are sequenced at cutover, not self-merged mid-rollout.

## Acceptance criteria

- Component 6 dashboards show $-spend and cache-hit-ratio per kind+repo, with
  live (non-empty) series.
- Post-P0: measurable drop in $/day driven by triage/review/conversation moving
  off Opus and `xhigh`->`high`, with brainstorm/refine/implement quality
  unchanged (proposal accept-rate, review find-rate, implement CI pass-rate as
  proxies).
- Component 3: no S3 conversation-restore code path remains; a crashed
  implement task resumes from handoff + branch state and completes.
- Component 4 (if P2 lands): `cache_read` non-zero on fresh-pod turn-0 within
  TTL of a prior same-(repo,model) pod; the superset tool prefix is
  byte-identical across kinds (a review pod's `tools/list` == an implement
  pod's) while a disallowed tool call is rejected at execution.

## Open questions / deferred

- Exact settings.json key `claude` honors for subagent model/effort (verify at
  implementation; directive fallback if absent).
- Whether 1h extended TTL pays off - decide from the P0 cache measurement.
- Operator-enforced decomposition for implement (phase-2 workflow depth) -
  deferred; revisit if P1 telemetry shows large residual Opus spend.
- OAuth-token vs raw-API-key server-side cache parity - the first thing the P0
  cache measurement confirms.

## Related memory

[[semver-push-cd-2026-06-28]] (cutover-sequencing pattern for CLAUDE.md/MEMORY.md
edits), [[agent-tool-surface-gating-report]] (the tool-profile gating this
unifies), [[agents-share-oidc-identity-authz]] (why gating can't key on
identity), [[brainstorm-proposal-autoclose-reaper-2026-06-28]] (the inert
reaper component 5 activates), [[tatara-observability-migration]] (where the
dashboards land).
