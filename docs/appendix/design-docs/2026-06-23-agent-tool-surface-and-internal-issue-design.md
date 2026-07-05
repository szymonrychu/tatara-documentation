# Spec: agent MCP tool surface (per-phase gating + consolidation + report_internal_issue)

Date: 2026-06-23
Status: design approved (decisions resolved 2026-06-23), ready for implementation
Repos touched: tatara-cli, tatara-operator, tatara-claude-code-wrapper, tatara-helmfile (deploy only)

## Overview

One coherent change to the MCP tool surface that tatara agent pods see, made of three parts:

- Part A: per-phase / per-kind tool gating. The operator sets a single env scalar
  (TATARA_TOOL_PROFILE) on the agent pod from the task kind; the cli `tatara mcp` server
  registers only the tools that profile allows, shrinking the tool schemas in the model's
  context (the real token cost). Fail-open on unknown/empty profile.
- Part B: selective consolidation. Merge the two code-graph list+get pairs
  (code_communities+code_community, code_hyperedges+code_hyperedge) into one tool each.
  Directional traversal tools stay separate (their names are the selection signal). Net
  code-graph 21 -> 19; total 64 -> 62 (pre-Part-C).
- Part C: new report_internal_issue MCP tool. Cross-cutting platform self-report. Executes in
  the cli process (slog ERROR + cli metric), and the wrapper transcript tailer recognizes it by
  name and emits a wrapper ERROR log + wrapper metric. Telemetry-first, no durable issue.

Verified counts (against code, 2026-06-23): AllTools 34 (13 memory + 21 code-graph),
OperatorTools 20 (includes already_done at tools.go:253), ChatTools 10 = 64 total. The brief's
"63" / "19 operator" undercounts. After Part B: 62. After Part C adds report_internal_issue: 63.

These three parts collide in tatara-cli/internal/mcp/{tools.go,server.go}. The cross-cutting
decisions section pins the order and the shared contract so they compose cleanly.

## Part A: per-phase MCP tool gating

### Mechanism (verified seam)

tatara-cli/internal/mcp/server.go:NewServer registers AllTools()+OperatorTools()+ChatTools()
unconditionally (server.go:39-47). It is the ONLY seam that shrinks tools/list and therefore the
only one that cuts schema tokens in the model context. The wrapper settings.json allowlist
(rejected alternative) leaves every schema advertised: zero token win, and forces a tool-name
list into chart/ConfigMap territory (hard rule: no lists in values.yaml). So gating lives in the
cli, driven by one env var the operator sets.

NewServer gains a `profile string` parameter. mcp.go reads TATARA_TOOL_PROFILE (env) with an
optional --tool-profile flag overriding it, mirroring the existing
`--metrics-addr os.Getenv(...)` pattern at mcp.go:171. resolveProfile("") and
resolveProfile("<unknown>") return nil (allow-all) and log a WARN. Known profiles return the
membership set, always unioned with an `alwaysOn` set.

```go
func NewServer(memory, operator, chat *client.Client, log *slog.Logger, profile string) *Server {
    allow := resolveProfile(profile) // nil => allow all (fail-open)
    s := &Server{ ... }
    for _, t := range append(append(append(AllTools(), OperatorTools()...), ChatTools()...), PlatformTools()...) {
        if allow == nil || allow[t.Name] {
            s.register(t)
        }
    }
    return s
}
```

PlatformTools() is Part C's group; it is in the registration loop here so Part A can gate it
(though alwaysOn keeps report_internal_issue in every profile).

### Profile registry (new file internal/mcp/profiles.go)

Profiles are sets of tool groups plus explicitly-named operator/terminal tools. Groups keep it
DRY as tools are added:

- groupMemory = all 13 AllTools memory tools (create_memory..delete_edge)
- groupCodeGraph = all 19 post-Part-B code_* tools
- groupChat = all 10 chat_* tools
- terminal/operator tools named individually

alwaysOn (unioned into EVERY profile, so a profile author cannot forget them):
report_internal_issue (Part C), project_get, repo_list, task_get.

### Phase -> profile mapping (post-Part-B names, Part-C alwaysOn implicit)

Memory + code-graph are broadly available everywhere (agents always research). chat_* is granted
to the multi-agent-room kinds: lifecycle, brainstorm, and incident (RESOLVED Q2: brainstorm and
incident may use rooms; the one-line groupChat add was taken now rather than deferred). review,
implement, triage, selfImprove stay chat-free. Each row's operator tools are IN ADDITION to
alwaysOn.

| Profile (task kind) | memory | code-graph | chat | operator tools (beyond alwaysOn) |
|---|---|---|---|---|
| brainstorm (brainstorm; healthCheck reuses Kind=brainstorm) | yes | yes | yes | task_list, task_get, subtask_list, subtask_create, subtask_update, propose_issue, comment_on_issue, skip_research |
| implement (implement) | yes | yes | no | task_update, subtask_list, subtask_create, subtask_update, change_summary, decline_implementation, already_done, submit_handover |
| review (review) | yes | yes | no | task_update, subtask_list, review_verdict, submit_handover |
| triage (triageIssue) | yes | yes | no | task_list, task_update, subtask_list, subtask_create, subtask_update, issue_outcome, comment, comment_on_issue |
| lifecycle (issueLifecycle, UNION of all states) | yes | yes | yes | task_list, task_update, subtask_list, subtask_create, subtask_update, issue_outcome, comment, comment_on_issue, change_summary, decline_implementation, already_done, pr_outcome, review_verdict, submit_handover |
| incident (incident) | yes | yes | yes | task_list, task_update, subtask_list, subtask_create, subtask_update, propose_issue, comment_on_issue, change_summary, decline_implementation, submit_handover |
| selfImprove (selfImprove) | yes | yes | no | task_update, subtask_list, subtask_create, subtask_update, change_summary, pr_outcome, decline_implementation, already_done, submit_handover |

Notes:
- brainstorm KEEPS comment_on_issue, propose_issue, skip_research: the live brainstorm directive
  (projectscan.go:1208/1224/1264) drives all three (path-1 duplicate, path-2 comment_on_issue,
  path-3 propose_issue, or skip_research no-yield). The dedup test that asserts comment_on_issue
  ABSENCE targets the project-wide brainstormGoalProject variant only, not the per-repo
  brainstorm goal. brainstorm correctly LOSES change_summary/review_verdict/issue_outcome/
  pr_outcome and chat.
- triage LOSES the code-mutation terminal tools (change_summary/pr_outcome). KEEPS issue_outcome,
  comment, comment_on_issue.
- lifecycle is the UNION on purpose: one long-lived pod / one `tatara mcp` process spans Triage ->
  Implement -> MRCI -> Merge -> MainCI -> Conversation. Per-lifecycle-state gating is infeasible
  without restarting the MCP server (out of scope). The per-turn directive
  (turnloop.go/lifecycle.go) names the correct terminal tool each turn, so over-provisioning the
  surface is acceptable: the directive drives correctness, not the tool list.
- chat_* is in lifecycle, brainstorm, and incident (RESOLVED Q2). lifecycle uses the Conversation
  room mechanism; brainstorm and incident get rooms now (one-line groupChat add per profile) so
  multi-agent collaboration in those kinds is not blocked. The other kinds stay chat-free.

### Operator wiring (tatara-operator/internal/agent/pod.go)

BuildPod appends one env var derived from task.Spec.Kind. It MUST go BEFORE the
`project.Spec.Agent.ExtraEnvs` append at pod.go:588 (the existing comment there notes a later
duplicate cannot shadow an earlier one), so an operator-supplied extra can override the profile
if ever needed.

```go
env = append(env, corev1.EnvVar{Name: "TATARA_TOOL_PROFILE", Value: toolProfileForKind(task.Spec.Kind)})
// ... existing code ...
env = append(env, project.Spec.Agent.ExtraEnvs...)
```

```go
func toolProfileForKind(kind string) string {
    switch kind {
    case "implement":      return "implement"
    case "review":         return "review"
    case "triageIssue":    return "triage"
    case "brainstorm":     return "brainstorm" // healthCheck shares Kind=brainstorm
    case "issueLifecycle": return "lifecycle"
    case "incident":       return "incident"
    case "selfImprove":    return "selfImprove"
    default:               return "" // fail-open
    }
}
```

### Observability (hard rule 13)

- New gauge tatara_mcp_registered_tools{profile} in internal/obs/obs.go (first GaugeVec in obs;
  add via package var + init MustRegister, matching the existing CounterVec pattern), set once at
  startup to the registered count.
- profile label added to the startup INFO log line in mcp.go; log resolved profile + registered
  count.

## Part B: selective consolidation

Single repo (tatara-cli). Two merges inside AllTools() code-graph block. No operator, wrapper, or
helmfile code change. Directional traversal stays separate: all of code_neighbors, code_callers,
code_callees, code_dependents, code_dependencies, code_resource_graph are
Service.Neighbors(fixedRelationSet, fixedDir) under the hood; the tool NAME encodes the
relation-set that is the model's selection signal. Merging them into a code_traverse(relation,
direction) mega-tool would leak relation vocabulary and force the model to know the strings: the
anti-pattern the brief forbids. Memory CRUD, edges, chat_*, operator task_/subtask_/comment, and
terminal intent tools all stay (distinct REST verbs/endpoints).

### Before -> after tool table (code-graph block only)

| Before | After | Discriminator |
|---|---|---|
| code_communities (list) + code_community (get-one) | code_communities | optional `community` (int): omit = list all; set = list that community's members |
| code_hyperedges (list, optional entity) + code_hyperedge (get-one by id) | code_hyperedges | optional `id`: omit = list (entity still filters); set = fetch that hyperedge; `id` wins over `entity` |

All other code-graph tools unchanged: code_search, code_entity, code_neighbors, code_callers,
code_callees, code_dependents, code_dependencies, code_file_imports, code_resource_graph,
code_cross_repo, code_path, code_important, code_stats, code_ambiguous_edges, code_explain,
code_related, code_bridges (17 kept + 2 merged = 19).

### Implementation shape

The two merged tools can no longer use the plain codeGet(path, required, optional) helper (the
path is arg-conditional). Replace each with an explicit Build closure that picks the path,
mirroring existing conditional builders (search_entities at tools.go:122, chat_list_rooms at
~639). Both backend routes already exist in tatara-memory (router.go community/communities and
hyperedge/hyperedges); no memory-server change. KISS: two small closures, no new abstraction.

Discriminator validation: silent fallback to list on a malformed key (matches existing
optional-arg argString behaviour). See open question 1.

### Count summary

Before: AllTools 34 (13 + 21) + Operator 20 + Chat 10 = 64.
After Part B: AllTools 32 (13 + 19) + Operator 20 + Chat 10 = 62.
After Part C (+report_internal_issue in PlatformTools): 63.

Hard-cut, no deprecated aliases: the tool list is rebuilt every server start, nothing external
pins these four names (grep of operator/wrapper internal/ is clean outside their own tests), and
aliases would re-inflate the surface we are shrinking.

## Part C: report_internal_issue

One logical event observed twice on purpose, because the cli `tatara mcp` server and the wrapper
are SEPARATE processes in the agent pod (no shared memory; the wrapper sees tool calls only via
the transcript tailer). The cli path is authoritative and always fires; the wrapper tailer path
satisfies the literal "ERROR in WRAPPER logs + WRAPPER metric" requirement.

### End-to-end path

```
agent (claude) --tool_use--> tatara mcp (cli)
  cli: validate -> slog.Error("internal issue reported", category, severity, description,
                              offending_tool, resource_id) [+ project/task from env]
     -> obs.InternalIssueTotal.WithLabelValues(category, severity).Inc()
     -> return ack text
claude writes tool_use block to transcript JSONL
wrapper tailer.processLine, case "tool_use" (tailer.go:337): block.Name ==
  "mcp__tatara__report_internal_issue"
  wrapper: emitInternalIssue(turnID, scrubbed block.Input)
     -> slog.Error / Warn (level follows severity), action=internal_issue_report, fields
        category/severity/offending_tool/resource_id/description
     -> InternalIssueTotal.WithLabelValues(category, severity).Inc()
```

### cli tool (tatara-cli)

New group PlatformTools() in tools.go, parallel to OperatorTools()/ChatTools(). Cross-cutting
(not memory/operator/chat-scoped), in alwaysOn so it appears in every Part-A profile. It does NOT
do an HTTP round-trip, so the Tool struct needs a minimal local-handler path: add an optional
`Handler func(args map[string]any) (string, error)` field; register() calls Handler when set and
skips clientFor/Invoke. One field + one if-branch (see open question 4 for the wiring choice).

Schema:
- category (enum, required): tool_error, directive_contradiction, workspace_broken,
  memory_inconsistent, graph_inconsistent, auth, other
- severity (enum, optional, default error): warn, error
- description (string, required, non-empty)
- offending_tool (string, optional)
- resource_id (string, optional)

No project/repo in the schema: those come from env (TATARA_PROJECT/TATARA_TASK) and are added as
log/label context by the server, matching how other tools default them. Validation rejects empty
description and unknown enum values (mirrors decline_implementation's non-empty guard).

New cli metric in internal/obs/obs.go: tatara_mcp_internal_issue_total{category,severity}
(package var + init MustRegister).

### wrapper recognition (tatara-claude-code-wrapper)

internal/transcript/tailer.go, processLine case "tool_use" (after the existing
agent-stream INFO log + incCounter("tool_use") at ~tailer.go:337-350):

```go
if block.Name == internalIssueToolName { // const "mcp__tatara__report_internal_issue"
    t.emitInternalIssue(turnID, block.Input) // block.Input already redactor-scrubbed upstream
}
```

emitInternalIssue unmarshals the scrubbed input into {Category, Severity, Description,
OffendingTool, ResourceID}, clamps Category/Severity to the known enum sets (default
category=other, severity=error on unknown/missing, bounding label cardinality exactly like the
existing clampNonMessageType), then logs at the severity-mapped level (warn->Warn,
error/default->Error) with action=internal_issue_report and increments the counter. On unmarshal
failure: log ERROR with category=other, severity=error, parse_error field, still increment (never
drop the signal, never panic; the tailer is the liveness heartbeat path).

The Tailer gains a second optional counter. Add an InternalIssueCounter interface (2-label
WithLabelValues(category, severity) Counter, same shape as the existing single-label
StreamCounter at tailer.go:24) and WithInternalIssueCounter(c) (nil-safe like WithCounter at
tailer.go:46). Wire it in session.go next to the existing
tailer.WithCounter(mgr.m.StreamEventsTotal) (~session.go:174).

New wrapper metric in internal/metrics/metrics.go:
tatara_wrapper_internal_issue_total{category,severity} (add field, construct in New, add to
MustRegister via the same reg / obs.PromRegistry()), exposed on the wrapper's existing /metrics.

### Name coupling

Claude namespaces MCP tools as mcp__<server>__<tool>. The cli server is registered as "tatara"
(server.go:32 NewMCPServer("tatara", ...)), so block.Name is mcp__tatara__report_internal_issue.
The wrapper matches that exact string as a package const, with a test asserting the constant and a
MEMORY.md note: if the server name or tool name changes, the wrapper silently stops emitting.

### Why not durable now

User asked for log + metric only. A Grafana panel on tatara_wrapper_internal_issue_total + a Loki
query on action=internal_issue_report gives full human visibility/alertability with no GitHub
issue spam. Durable issue creation is a clean follow-up (a Target=TargetOperator variant + new
operator endpoint, values-gated) once report volume is known. See open question 5.

## Cross-cutting decisions

1. Part B runs FIRST or lands together with Part A. Part A's profile membership table references
   tool NAMES; Part B removes code_community and code_hyperedge. Those two names are code-graph
   tools and are NOT referenced individually by any profile (profiles reference groupCodeGraph as
   a whole), so Part B does not actually break a profile string. But the groupCodeGraph membership
   list in profiles.go enumerates the 19 post-Part-B names; build Part A's groupCodeGraph from the
   post-merge AllTools() so a profiles_test that asserts "every name in groupCodeGraph exists in
   AllTools()" passes. Recommendation: land B and A in the same cli change (or B first), so
   groupCodeGraph is authored against final names.

2. Part C's report_internal_issue is in alwaysOn, so it appears in EVERY Part-A profile. A
   profiles_test asserts its presence in all profiles. PlatformTools() is in the NewServer
   registration loop and gated by Part A, but alwaysOn guarantees it survives every profile.

3. Part A's gating and Part C's registration agree on the group model: PlatformTools() is a first-
   class group (like OperatorTools/ChatTools), included in the NewServer loop and in ToolCount.
   ToolCount becomes profile-aware (counts only registered tools); the empty-profile case equals
   len(AllTools)+len(OperatorTools)+len(ChatTools)+len(PlatformTools) = 63.

4. All three touch tools.go/server.go. Sequence inside the cli repo: Part B (merge), then Part C
   (PlatformTools + Handler field + register branch), then Part A (NewServer profile param +
   profiles.go). One cli release carries all three; tests for each layer guard the others (the
   name-existence test catches a stale profile string after B's merge or C's rename).

5. The fail-open default means cli and operator can ship in either order (old cli ignores the env;
   new cli with no env serves the full set). Recommended order below ships cli first (no behavior
   change until the operator sets the env).

## Rollout order (3 repos + tatara-helmfile)

All via tatara-helmfile GitOps (hard rule 15): each release bumps the chart version AND the pinned
image.tag. Branch flow per repo: worktree off main -> TDD -> merge to component main (CI builds +
pushes image/chart) -> tatara-helmfile MR -> diff-review -> pipeline applies.

1. tatara-cli: land Parts B + C + A in one branch (B then C then A). Merge to main, CI builds
   image. tatara-helmfile MR bumping cli chart version + image tag. At this point the cli serves
   the full (now 63-tool) set in every pod because no TATARA_TOOL_PROFILE env is set yet, plus the
   new report_internal_issue tool works (cli-side telemetry). Safe, no behavior regression.
   Per the wrapper-cli-pin memory: confirm `tatara mcp` still serves tools/list tokenless before
   any wrapper image rebuild pins a new TATARA_CLI_VERSION.

2. tatara-claude-code-wrapper: tailer branch + new wrapper metric. Merge to main, CI builds image.
   tatara-helmfile MR bumping wrapper chart version + image tag. Until this lands, a
   report_internal_issue call still works (cli logs/metric) but produces no wrapper log/metric.
   Order vs cli is loose; ship after or alongside cli (the match const must agree with the
   registered server+tool name, which cli already provides).

3. tatara-operator: BuildPod TATARA_TOOL_PROFILE env. Merge to main, CI builds image.
   tatara-helmfile MR bumping BOTH the tatara-operator AND tatara-project chart pins + the pinned
   operator image.tag (per the dual-chart-pin deploy memory). Once applied, new agent pods get the
   profile env and gating goes live. Shipping operator last means gating only activates after the
   filtering cli is already deployed.

Post-deploy verification: a brainstorm pod's `tatara mcp` startup log shows profile=brainstorm and
a reduced tatara_mcp_registered_tools gauge vs an implement pod; trigger a report_internal_issue
and confirm both the cli ERROR log + tatara_mcp_internal_issue_total AND the wrapper ERROR log
(action=internal_issue_report) + tatara_wrapper_internal_issue_total.

## Test strategy

TDD, unit-level, all three repos. `mise exec -- go test ./...` + golangci-lint clean in each;
verification-before-completion before any "done".

tatara-cli:
- profiles_test.go (NEW): resolveProfile returns nil for "" and unknown (fail-open) + logs WARN;
  each known profile is non-empty and unions alwaysOn; every tool name referenced by every profile
  (including groupCodeGraph's 19 post-Part-B names) exists in
  AllTools()+OperatorTools()+ChatTools()+PlatformTools() (guards Part-B merges and Part-C renames);
  report_internal_issue present in EVERY profile; brainstorm excludes chat_*, change_summary,
  review_verdict, issue_outcome, pr_outcome but INCLUDES comment_on_issue + propose_issue +
  skip_research; triage excludes change_summary, pr_outcome; lifecycle includes chat_* and the
  union terminal set. Table-driven test mapping each of the 7 CRD kinds -> expected profile.
- server_test.go: NewServer with each profile registers exactly the expected count; empty profile
  registers the full 63.
- tools_test.go: merged code_communities (no community -> GET /code-graph/communities?repo=r;
  community=3 -> GET /code-graph/community?repo=r&community=3); merged code_hyperedges (no id ->
  GET /code-graph/hyperedges?repo=r, entity filter forwarded; id=h -> GET
  /code-graph/hyperedge?repo=r&id=h; id wins over entity); required-key guard (missing repo ->
  error); drop the old code_community/code_hyperedge name-keyed cases. PlatformTools() includes
  report_internal_issue with valid schema + enum constraints; local Handler validates empty
  description and unknown category/severity (table-driven); invoking it increments
  tatara_mcp_internal_issue_total and emits an ERROR slog record (slog test handler).
- obs_test.go: assert tatara_mcp_internal_issue_total and tatara_mcp_registered_tools registered
  (mirror existing TestToolCallsTotal_Registered).

tatara-operator:
- pod_test.go: BuildPod sets TATARA_TOOL_PROFILE correctly per kind (table-driven, t.Run per
  kind); healthCheck (Kind=brainstorm) -> brainstorm; the env is placed before ExtraEnvs; unknown
  kind -> "". A test enumerating the CRD kind enum asserting each maps to a non-empty profile.

tatara-claude-code-wrapper:
- tailer_test.go: synthetic transcript tool_use block named mcp__tatara__report_internal_issue,
  varying inputs (each category/severity valid; missing severity -> default error; unknown
  category -> clamped other; malformed JSON -> ERROR with parse_error, still counted); assert (a)
  the generic agent_stream tool_use INFO line still emits, (b) a distinct ERROR record with
  action=internal_issue_report + correct fields, (c) InternalIssueCounter incremented with clamped
  labels (fake counter implementing the interface). A name-constant test asserting the match
  string equals mcp__tatara__report_internal_issue.
- metrics_test.go: assert tatara_wrapper_internal_issue_total registered.

## Resolved decisions (2026-06-23)

1. Merged code-graph discriminator validation: SILENT FALLBACK to list on a malformed key (matches
   existing optional-arg argString behaviour). No explicit type-reject.
2. chat_* profiles: lifecycle + brainstorm + incident (granted now, not deferred). Other kinds
   chat-free. Reflected in the profile table above.
3. cli profile source: ENV + FLAG. Read TATARA_TOOL_PROFILE env with an optional --tool-profile
   flag overriding it (mirrors --metrics-addr os.Getenv default at mcp.go:171).
4. cli local-handler wiring: add an optional `Handler func(args) (string, error)` field to the Tool
   struct; register() calls it instead of clientFor/Invoke when set. KISS, reusable for future
   pure-local tools.
5. report_internal_issue scope: TELEMETRY-FIRST. cli+wrapper log+metric only. Durable GitHub-issue
   creation is an explicit follow-up (Target=TargetOperator variant + new operator endpoint,
   values-gated), NOT in this change.
6. report severity -> log level: severity enum {warn,error}, DEFAULT error; log level follows
   severity (warn->Warn, error/default->Error). Metric increments regardless of level.
7. metric label dimensionality: {category,severity} ONLY on both cli and wrapper counters.
   project/repo/kind carried as structured log fields, not labels (cardinality).
8. gating depth: FILTER-ONLY for the first cut (no wrapper deny-list). Operator REST authorizes
   terminal actions server-side per kind/state already.
