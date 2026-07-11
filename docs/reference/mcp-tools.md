---
title: MCP Tool Profiles
---

# MCP Tool Profiles

Authoritative reference for which MCP tools each agent kind can call, derived
from the real gating code in `tatara-cli` (`internal/mcp/profiles.go`,
`server.go`, `tools.go`) and the pod-injection code in `tatara-operator`
(`internal/agent/pod.go`). This supersedes any per-kind tool tables in other
docs if they disagree - re-derive from the source files listed under
[Where profiles live](#where-profiles-live) rather than trusting a second copy.

---

## How gating works

- The operator (`tatara-operator`, `internal/agent/pod.go`) sets
  `TATARA_TOOL_PROFILE` in the agent pod's env, derived from the Task's
  `spec.kind` via `toolProfileForKind`. This is the only place the env var is
  actually assigned in the running platform.
- `tatara mcp` (the CLI, `internal/cmd/mcp.go`) reads `TATARA_TOOL_PROFILE`
  (or `--tool-profile`) and passes it into `mcp.NewServer`.
- **`tools/list` is profile-invariant** (Component 4a / G15): `NewServer`
  registers every tool, for every profile, unconditionally. This keeps the
  `tools/list` response byte-identical across all agent kinds so every pod
  shares one Anthropic prompt-cache prefix - a per-kind filtered list would
  fragment the cache (tools render first in cache order).
- Gating is enforced at **call time** instead, in the `register()` dispatch
  closure: before invoking a tool's handler, the server checks the resolved
  per-profile allow-set. A denied call never reaches the backend; it returns
  `tool "<name>" is not permitted for profile "<profile>"` and increments
  `tool_calls_total{status="denied"}`.
- **Fail-open**: `TATARA_TOOL_PROFILE` unset/empty -> `allow == nil` -> every
  registered tool is callable. This is the local-dev / no-profile path and
  logs a WARN.
- **Fail-closed**: a non-empty but unrecognized profile string resolves to
  the `alwaysOn` set only (4 tools) and logs a WARN. Profile gating is the
  sole authz boundary here - a typo must never silently grant the full
  surface.
- **Identity note**: every agent pod authenticates as the same OIDC client
  (`tatara-agent`). Authorization cannot key on caller identity, so it keys
  on tool profile plus env-scoped task/project context (`TATARA_TASK`,
  `TATARA_PROJECT`) instead.

---

## Tool groups

72 tools are registered in total, from five Go functions in
`tatara-cli/internal/mcp/tools.go`:

| Group | Count | Function | Target backend |
|---|---:|---|---|
| Memory | 13 | `AllTools()` (memory half) | tatara-memory |
| Code-graph | 19 | `AllTools()` (code_* half) | tatara-memory |
| Operator | 25 | `OperatorTools()` | tatara-operator |
| Chat | 10 | `ChatTools()` | tatara-chat |
| Handoff | 4 | `HandoffTools()` | tatara-chat |
| Platform | 1 | `PlatformTools()` | local (no backend call) |

Memory (`create_memory`, `get_memory`, `delete_memory`,
`bulk_create_memories`, `get_ingest_job`, `query`, `describe`, `get_entity`,
`search_entities`, `patch_entity`, `list_edges`, `create_edge`,
`delete_edge`) and code-graph (`code_search`, `code_entity`,
`code_neighbors`, `code_callers`, `code_callees`, `code_dependents`,
`code_dependencies`, `code_file_imports`, `code_resource_graph`,
`code_cross_repo`, `code_path`, `code_important`, `code_stats`,
`code_ambiguous_edges`, `code_explain`, `code_related`, `code_hyperedges`,
`code_communities`, `code_bridges`) are unconditional: every named profile
gets all 32 of them, unfiltered.

### alwaysOn (all 7 named profiles, plus fail-closed unknown)

| Tool | Description |
|---|---|
| `report_internal_issue` | Report a platform-internal issue (tool error, directive contradiction, workspace/memory/graph inconsistency, auth). Structured log + metric only, no SCM issue. |
| `project_get` | Read the current project |
| `repo_list` | List repositories in a project |
| `task_get` | Read the current task |

### Chat (10) - `chat` flag profiles only

`chat_create_room`, `chat_list_rooms`, `chat_get_room`, `chat_close_room`,
`chat_add_participant`, `chat_list_participants`, `chat_remove_participant`,
`chat_send_message`, `chat_poll_messages`, `chat_get_log`.

### Handoff (4) - continuity-carrying profiles only

Added after the tool surface was last widely documented; not yet reflected
everywhere else in the docs tree.

| Tool | Gate |
|---|---|
| `write_handoff` | `handoff` flag profiles |
| `get_handoff` | `handoff` flag profiles |
| `list_handoffs` | `handoff` flag profiles |
| `delete_handoff` | `handoffDelete` flag - **`refine` only**. Refine is the handoff groomer; every other continuity-carrying profile can write/read but never delete another pod's handoff. |

---

## Per-kind profile table

`toolProfileForKind` maps a Task `kind` to a profile name. Under the 7-kind model the profile
name matches the kind name for all seven live kinds - `clarify` gets its own profile rather than
diverging from its kind name the way the retired `triageIssue -> triage` /
`issueLifecycle -> lifecycle` mapping did (confirm this against source at execution time per the
warning below).

| Profile | Task kind(s) | Chat | Handoff (r/w) | Handoff delete | Operator tools | Total allowed |
|---|---|:---:|:---:|:---:|---:|---:|
| `refine` | `refine` | no | yes | **yes** | 6 | 46 |
| `brainstorm` | `brainstorm` | yes | yes | no | 7 | 56 |
| `implement` | `implement` | no | yes | no | 8 | 47 |
| `review` | `review` | no | no | no | 4 | 40 |
| `clarify` | `clarify` | ? | ? | no | ? | ? |
| `incident` | `incident` | yes | yes | no | 10 | 59 |
| `documentation` | `documentation` | no | ? | no | ? | ? |
| *(empty)* | fail-open, local dev | - | - | - | all 22 (+`project_list`) | 72 |
| *(unrecognized string)* | fail-closed | no | no | no | 0 | 4 |

!!! warning "Do not invent tool counts"
    `clarify` and `documentation` are new profiles with no shipped tool-set yet as of this
    plan. The spec names a new `clarify` profile "from old lifecycle/triage" (implying it
    inherits most of the retired `triage`/`lifecycle` profiles' operator-tool surface) and
    leaves `documentation`'s tool set unspecified. **Re-derive both from
    `tatara-cli/internal/mcp/profiles.go` once that change merges** - do not publish a
    fabricated tool count. `brainstorm` also drops its `healthCheck` dual-kind row since
    `healthCheck` no longer exists as a kind sharing the profile, and the `selfImprove` profile
    is removed outright (the kind was already dead before this redesign).

Total = 32 (memory + code-graph, unconditional) + chat (0 or 10) + handoff
(0, 3, or 4) + profile-specific operator tools + 4 (alwaysOn).

`refine`'s 6-tool operator surface is deliberately deny-by-default: it omits
`create_issue` (issue creation is an escalation vector) and every SCM-mutation
or merge-escalation tool. Historical notes elsewhere describe refine as
"~42 tools" - that count predates the `handoff` group being added (32 + 6 +
4 = 42); with `handoff` + `delete_handoff` now included, refine is 46.

`project_list` and `create_issue` are registered (`OperatorTools()`) but
**not granted to any named profile** - they are reachable only in fail-open
mode (empty `TATARA_TOOL_PROFILE`, e.g. local dev without the operator
injecting a profile).

---

## Operator tool matrix

All 25 `OperatorTools()` (`project_get`, `repo_list`, `task_get` are the
alwaysOn 3 of these and are omitted below since every profile has them).

!!! warning "clarify and documentation columns are not yet shipped"
    The `clarify` and `documentation` columns below are placeholders (`?`), not confirmed grants
    - see the [Do not invent tool counts](#per-kind-profile-table) warning above. `clarify`
    absorbs the retired `triage`/`lifecycle` profiles' responsibilities per the spec; do not
    assume it gets their exact per-tool grants without checking `profiles.go` once merged. The
    `selfImprove` profile is dropped from this matrix entirely (retired kind, no longer wired).

| Tool | refine | brainstorm | implement | review | clarify | incident | documentation |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `project_list` | | | | | | | |
| `task_list` | x | x | | | ? | x | ? |
| `task_update` | | | x | x | ? | x | ? |
| `subtask_list` | | x | x | x | ? | x | ? |
| `subtask_create` | | x | x | | ? | x | ? |
| `subtask_update` | | x | x | | ? | x | ? |
| `propose_issue` | | x | | | ? | x | ? |
| `review_verdict` | | | | x | ? | | ? |
| `pr_outcome` | | | | | ? | | ? |
| `change_summary` | | | x | | ? | x | ? |
| `submit_handover` | | | x | x | ? | x | ? |
| `issue_outcome` | | | | | ? | | ? |
| `decline_implementation` | | | x | | ? | x | ? |
| `already_done` | | | x | | ? | | ? |
| `skip_research` | | x | | | ? | | ? |
| `comment` | | | | | ? | | ? |
| `list_issues` | x | | | | ? | | ? |
| `list_commits` | x | | | | ? | | ? |
| `close_issue` | x | | | | ? | | ? |
| `edit_issue` | x | | | | ? | | ? |
| `create_issue` | | | | | | | |
| `comment_on_issue` | x | x | | | ? | x | ? |

### By category

| Category | Tools |
|---|---|
| Task/subtask ledger | `task_list`, `task_get`\*, `task_update`, `subtask_list`, `subtask_create`, `subtask_update` |
| Project/repo | `project_list` (fail-open only), `project_get`\*, `repo_list`\* |
| Issues (SCM) | `propose_issue`, `comment_on_issue`, `comment`, `list_issues`, `close_issue`, `edit_issue`, `create_issue` (fail-open only), `issue_outcome` |
| PR/MR | `review_verdict`, `pr_outcome`, `change_summary` |
| Refusal/terminal outcome | `decline_implementation`, `already_done`, `skip_research`, `submit_handover` |
| Platform self-report | `report_internal_issue`\* |
| Memory/graph | see [Tool groups](#tool-groups), 32 tools, unconditional |
| Conversation (agent-to-agent) | `chat_*` (10), conditional on `chat` |
| Continuity | `write_handoff`, `get_handoff`, `list_handoffs`, `delete_handoff` (refine only) |

\* alwaysOn - present in every profile including fail-closed unknown.

### Notable fields

- `change_summary` requires `change_significance` (`major` \| `minor` \|
  `patch`) as of the semver push-CD cutover - the tool call is rejected
  server-side if omitted or not one of the three enum values. This is what
  drives the auto-merge -> semver tag -> `tatara-helmfile` cascade; humans
  set the equivalent via a `semver:<level>` PR label.
- `edit_issue` patches only `title` and `body`. Labels are intentionally not
  editable through this tool - the four managed labels drive kind handoffs
  and stay operator/maintainer-controlled, so no profile (including
  `refine`, the groomer) can set them.
- `decline_implementation` / `already_done` / `skip_research` all require a
  non-empty `reason`; a silent finish with no PR and no outcome call is
  rejected as an unexplained refusal.

---

## Where profiles live

| What | File | Notes |
|---|---|---|
| Profile -> tool-set definitions (`groupMemory`, `groupCodeGraph`, `groupChat`, `groupHandoff`, `groupHandoffDelete`, `alwaysOn`, `profiles` map, `resolveProfile`) | `tatara-cli/internal/mcp/profiles.go` (whole file, ~253 lines) | Source of truth for what each profile allows. |
| Tool registration + call-time authz | `tatara-cli/internal/mcp/server.go` (`NewServer` ~L36-73, `register()` dispatch closure ~L112-165) | Registers all tools unconditionally; gates at call time per Component 4a. |
| Tool definitions (name/description/schema/handler per group) | `tatara-cli/internal/mcp/tools.go`: `AllTools()` L45, `OperatorTools()` L334, `ChatTools()` L788, `HandoffTools()` L933, `PlatformTools()` L1016 | Edit here to add/rename a tool or change its schema. |
| CLI flag/env wiring | `tatara-cli/internal/cmd/mcp.go` (`tatara mcp` command, ~L145 reads `TATARA_TOOL_PROFILE`/`--tool-profile`) | |
| Kind -> profile mapping used by the CLI's own tests | `tatara-cli/internal/mcp/profiles.go` `toolProfileForKind` (top of file) | Test-locked mirror of the operator's mapping; not called by the running `tatara mcp` serve path itself. |
| **Kind -> profile mapping actually applied to pods** | `tatara-operator/internal/agent/pod.go`: env injection at L655, `toolProfileForKind` at L838 | This is the authoritative mapping - the operator, not the CLI, decides which profile a pod gets. |

### How to change a kind's tool surface

1. To change **which tools a profile grants**: edit the `profiles` map (or a
   `group*` slice) in `tatara-cli/internal/mcp/profiles.go`.
2. To **add a new tool**: add it to the relevant `*Tools()` function in
   `tatara-cli/internal/mcp/tools.go`, then add its name to the profile(s)
   that should get it in `profiles.go`.
3. To **remap which profile a Task kind gets**: edit `toolProfileForKind` in
   `tatara-operator/internal/agent/pod.go` (this is the one that matters at
   runtime; keep the `tatara-cli` copy in sync since its test asserts the
   same mapping contract).
4. Both repos deploy via the standard semver push-CD path: merge to `main`
   with the required `change_significance` on `change_summary` (or a
   `semver:<level>` PR label for a human change) -> CI builds and tags ->
   `tatara-helmfile` auto-applies the new image/chart pin to the cluster.
   Never hand-edit a deploy pin; a `tatara-cli` change also needs the
   `tatara-claude-code-wrapper` image's `TATARA_CLI_VERSION` bumped forward
   before agent pods pick it up.
