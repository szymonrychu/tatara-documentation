---
title: MCP Tools by Agent Kind
description: The 20-tool, per-profile-gated MCP surface every agent pod calls.
---

# MCP tools by agent kind

**Twenty tools.** The pre-redesign surface was 74, and a clarify pod could see
all of them and legally call four.

Tools **not in the pod's profile are not registered at all**. `tools/list` is
per-profile, so an agent cannot see a tool it may not call. (This
deliberately breaks the byte-identical-`tools/list` prompt-cache
optimisation. That is an accepted cost.)

`resolveProfile` **fails closed.** An empty or unknown `TATARA_TOOL_PROFILE`
serves only the always-on set and logs WARN - and it does not register
`submit_outcome`, which means the pod has no terminal tool and the failure is
loud rather than silent.

`TATARA_TOOL_PROFILE` is the **agent** kind (`Task.status.agentKind`), one of
seven: `brainstorm`, `incident`, `clarify`, `implement`, `review`, `refine`,
`documentation`.

## The five tool groups

| Group | Count | Tools |
|---|---|---|
| Platform | 7 | `task_get`, `task_list`, `task_context`, `task_note`, `project_get`, `repo_list`, `report_internal_issue` |
| SCM | 3 | `scm_read`, `issue_write`, `mr_write` |
| Code graph | 4 | `code_search`, `code_context`, `code_graph`, `code_explain` |
| Memory | 5 | `memory_query`, `memory_describe`, `memory_write`, `memory_entity`, `memory_edges` |
| Outcome | 1 | `submit_outcome` (one name, seven schemas) |

## The profile gating table

Always-on for every profile, including the fail-closed empty one: `task_get`,
`task_context`, `task_note`, `project_get`, `repo_list`,
`report_internal_issue`.

| tool | brainstorm | incident | clarify | implement | review | refine | documentation |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| `submit_outcome` | yes | yes | yes | yes | yes | yes | yes |
| `task_get` / `task_context` / `task_note` | yes | yes | yes | yes | yes | yes | yes |
| `project_get` / `repo_list` / `report_internal_issue` | yes | yes | yes | yes | yes | yes | yes |
| `task_list` | yes | yes | - | - | - | yes | - |
| `scm_read` | yes | yes | yes | yes | yes | yes | yes |
| `issue_write` | - | - | yes | - | - | yes | - |
| `mr_write` | - | - | - | yes | yes | comment-only* | yes |
| `code_search` | yes | yes | yes | yes | yes | - | yes |
| `code_context` | yes | yes | yes | yes | yes | - | yes |
| `code_graph` | yes | yes | - | yes | yes | - | yes |
| `code_explain` | yes | yes | yes | yes | yes | - | yes |
| `memory_query` / `memory_describe` | yes | yes | yes | yes | yes | yes | yes |
| `memory_write` | yes | yes | - | yes | - | - | yes |
| `memory_entity` | yes | yes | - | - | - | - | yes |
| `memory_edges` | - | yes | - | - | - | - | yes |

\* `refine`'s `mr_write` is restricted to `action=comment` by a cli-side and
operator-side check, not by the schema. It is the only non-uniform cell in
the table.

Counts are derived from the table above, not quoted from a summary line: each
profile is 20 minus the cells the table marks `-` for it (the always-on row
and `submit_outcome` are never denied, so they never subtract).

| profile | denied cells | count |
|---|---|:--:|
| brainstorm | `issue_write`, `mr_write`, `memory_edges` | 17 |
| incident | `issue_write`, `mr_write` | 18 |
| clarify | `task_list`, `mr_write`, `code_graph`, `memory_write`, `memory_entity`, `memory_edges` | 14 |
| implement | `task_list`, `issue_write`, `memory_entity`, `memory_edges` | 16 |
| review | `task_list`, `issue_write`, `memory_write`, `memory_entity`, `memory_edges` | 15 |
| refine | `code_search`, `code_context`, `code_graph`, `code_explain`, `memory_write`, `memory_entity`, `memory_edges` | 13 |
| documentation | `task_list`, `issue_write` | 18 |

Counts: brainstorm 17, incident 18, clarify 14, implement 16, review 15,
refine 13, documentation 18.

`task_list` goes to the broad-context trio only, because a clarify / implement
/ review pod that can list every Task can wander into another Task's work.
`issue_write` is clarify plus refine; brainstorm and incident file issues
through `submit_outcome`, so the proposal cap and dedup still apply. `code_*`
is denied to refine - a backlog groomer reads issues, not code.
Graph-mutating memory tools are denied to conversational and reviewing pods.
And the one non-uniform cell, honestly: refine's `mr_write` is restricted to
`action=comment` by a cli-side and operator-side check, not by the schema.

## The three tools whose absences are the point

### `mr_write` has three actions: `open`, `comment`, `reply`

**No `merge`. No `approve`. No `request_changes`.**

The platform has **one** bot identity, so an implement pod holding
`mr_write(approve)` can approve its own MR. And two writers of a review - the
agent via `mr_write`, the operator via `submit_outcome` - forced a head-SHA
reconciliation gate that was itself a wedge. So: the operator posts every SCM
review, from the accepted review `submit_outcome`. **One writer per medium.**
The agent writes conversation; the operator writes reviews, merges, labels
and status. A hallucinated merge call has nowhere to land.

`action=open` is **idempotent**: an existing open MR on the same
`task/<task-name>` head branch returns
`{"status":"ok","existing":true,...}` without calling the forge. It is
**refused with 409** when the Task already owns a *merged* MR for that repo -
the structural stop on the duplicate-PR path after a partial merge.

### `issue_write` has no `status` and no `labels` parameter

Approval and every lifecycle label are operator-owned. A `labels` key would
let an agent stamp the trigger label and self-escalate.

### `scm_read(kind=ci)` is the only live forge read

`kind=issues`, `kind=mr` and `kind=comments` are served from the CR mirror
and never touch the forge. `repo` is required on every kind.

`kind=ci` is the `gh run watch` / `gh pr checks` replacement, and it is a
**polled point read, not a blocking watch** - because `turnTimeoutSeconds` is
an *inactivity* window, so a single blocking call longer than it terminally
fails the turn. The operator paces it server-side to one forge fetch per 20
seconds per `(repo, number)`; a call inside the window is served from the
last result with `"cached": true`. `logTail` (last 4000 bytes) is served only
for a check whose conclusion is `failure`, `timed_out` or `cancelled` - a
green run's logs are never fetched.

## `submit_outcome`

One name, seven schemas. REST: `POST /tasks/{task}/outcome`, body
`{"kind":"<profile>","payload":{...}}`.

`kind` must equal `Task.status.agentKind` (409 on mismatch - the pod's claim
is not trusted). The call is idempotent for the same
`(task, agentKind, stage)`, and it 409s when the Task is in a terminal
stage.

=== "implement / documentation"

    Identical schema for both profiles.

    ```json
    {"type":"object","properties":{
      "task":{"type":"string"},
      "action":{"type":"string","enum":["submitted","declined"]},
      "title":{"type":"string","description":"MR title. Required when action=submitted."},
      "body":{"type":"string","description":"MR body. Required when action=submitted."},
      "change_significance":{"type":"string","enum":["major","minor","patch"],
        "description":"Required when action=submitted. major=backward-incompatible; minor=backward-compatible feature; patch=fix. YOU own this level - a reviewer may raise it but can never lower it."},
      "merge_order":{"type":"array","items":{"type":"string"},
        "description":"REQUIRED when this task's MRs span more than one repo: the Repository CR names in dependency order, first-merged first. There is NO default. Get it wrong and a downstream repo ships against an API that has not merged yet."},
      "decline_reason":{"type":"string","description":"Required when action=declined."}},
     "required":["action"],"additionalProperties":false}
    ```

=== "review"

    ```json
    {"type":"object","properties":{
      "task":{"type":"string"},
      "verdict":{"type":"string","enum":["approve","request_changes"]},
      "change_significance":{"type":"string","enum":["major","minor","patch"],
        "description":"Optional. It may only RAISE the level the implementer declared, never lower it."},
      "reviewed_shas":{"type":"array","minItems":1,"items":{"type":"object","properties":{
          "repo":{"type":"string"},"number":{"type":"integer"},"sha":{"type":"string"}},
        "required":["repo","number","sha"]},
        "description":"REQUIRED. The head SHA you ACTUALLY CHECKED OUT AND READ, per MR. The operator re-reads the live head and REFUSES your verdict if it moved while you were reviewing - anything pushed after your checkout would otherwise merge unreviewed under your approval."},
      "findings":{"type":"array","items":{"type":"object","properties":{
          "repo":{"type":"string"},"number":{"type":"integer"},
          "path":{"type":"string"},"line":{"type":"integer"},"body":{"type":"string"},
          "severity":{"type":"string","enum":["critical","high","medium","low"]}},
        "required":["repo","number","body","severity"]},
        "description":"Required (at least 1) when verdict=request_changes. The OPERATOR posts these as the SCM review and its inline comments - you do not post them yourself."}},
     "required":["verdict","reviewed_shas"],"additionalProperties":false}
    ```

    There is no `comment` verdict: a review either approves (merge proceeds)
    or requests changes (loop back). A non-decision has no stage to go to.

    `verdict=approve` does not post an approving review. The platform has one
    bot identity, so the forge rejects a self-approve; the operator posts a
    COMMENT review carrying the verdict and then merges. The merge is the
    approval of record.

=== "clarify"

    ```json
    {"type":"object","properties":{
      "task":{"type":"string"},
      "decision":{"type":"string","enum":["implement","close","discuss"]},
      "reason":{"type":"string","description":"Required. For decision=implement, cite WHO approved and WHERE - the operator independently re-reads the thread and verifies both the identity AND the wording."}},
     "required":["decision","reason"],"additionalProperties":false}
    ```

=== "brainstorm"

    ```json
    {"type":"object","properties":{
      "task":{"type":"string"},
      "action":{"type":"string","enum":["propose","skip"]},
      "proposals":{"type":"array","minItems":1,"maxItems":5,"items":{"type":"object","properties":{
          "repo":{"type":"string"},"title":{"type":"string"},"body":{"type":"string"},
          "kind":{"type":"string","enum":["bug","improvement"]}},
        "required":["repo","title","body","kind"]}},
      "reason":{"type":"string","description":"Required when action=skip."}},
     "required":["action"],"additionalProperties":false}
    ```

=== "incident"

    ```json
    {"type":"object","properties":{
      "task":{"type":"string"},
      "action":{"type":"string","enum":["file_issue","false_positive"]},
      "alert_rules":{"type":"array","minItems":1,"items":{"type":"string"}},
      "issue":{"type":"object","properties":{
          "repo":{"type":"string"},"title":{"type":"string"},"body":{"type":"string"}},
        "required":["repo","title","body"],
        "description":"Required when action=file_issue."},
      "reason":{"type":"string"}},
     "required":["action","alert_rules","reason"],"additionalProperties":false}
    ```

=== "refine"

    ```json
    {"type":"object","properties":{
      "task":{"type":"string"},
      "folds":{"type":"array","items":{"type":"object","properties":{"task":{"type":"string"}},
        "required":["task"]},
        "description":"Member Tasks to fold in: their Issues/MRs are adopted, then the member Task is deleted. A member with a running pod is REFUSED."},
      "closes":{"type":"array","items":{"type":"object","properties":{
          "repo":{"type":"string"},"number":{"type":"integer"},"reason":{"type":"string"}},
        "required":["repo","number","reason"]}},
      "links":{"type":"array","items":{"type":"object","properties":{
          "repo":{"type":"string"},"number":{"type":"integer"},"isPR":{"type":"boolean"}},
        "required":["repo","number"]}}},
     "required":[],"additionalProperties":false}
    ```

## The other tools

### SCM (3)

**`scm_read`** - `repo` is required on every kind:

```json
{"type":"object","properties":{
  "kind":{"type":"string","enum":["issues","mr","comments","commits","ci"],
    "description":"issues|mr|comments are served from tatara's own mirror of the forge (fast, free, at most one hour stale). ci is a live read of the forge and is paced server-side to one fetch per 20s per PR."},
  "project":{"type":"string"},
  "repo":{"type":"string","description":"Repository CR name, e.g. tatara-operator. REQUIRED."},
  "number":{"type":"integer","description":"Required for kind=ci and kind=comments."},
  "is_pr":{"type":"boolean","description":"kind=comments only: read the MR thread instead of the issue thread."},
  "state":{"type":"string","enum":["open","closed","merged","all"],
    "description":"kind=issues (open|closed|all) and kind=mr (open|merged|closed|all)."},
  "since":{"type":"string","description":"kind=issues|mr only. RFC3339."},
  "labels":{"type":"string","description":"kind=issues only. Comma-separated."},
  "since_days":{"type":"integer","description":"kind=commits only. Default 30."},
  "limit":{"type":"integer"}},
 "required":["kind","repo"],"additionalProperties":false}
```

**`issue_write`** - no `status`, no `labels`:

```json
{"type":"object","properties":{
  "action":{"type":"string","enum":["create","edit","close","comment"]},
  "project":{"type":"string"},"repo":{"type":"string"},
  "number":{"type":"integer"},"title":{"type":"string"},"body":{"type":"string"},
  "comment":{"type":"string","description":"Required for action=close: every close cites its reason."}},
 "required":["action","repo"],"additionalProperties":false}
```

**`mr_write`** - no `merge`, no `approve`, no `request_changes`:

```json
{"type":"object","properties":{
  "action":{"type":"string","enum":["open","comment","reply"]},
  "project":{"type":"string"},"repo":{"type":"string"},
  "number":{"type":"integer"},"title":{"type":"string"},"body":{"type":"string"},
  "in_reply_to":{"type":"string","description":"Required for action=reply: the externalId from scm_read(kind=comments)."}},
 "required":["action","repo"],"additionalProperties":false}
```

### Code graph (4)

- `code_search` - `GET /code/entities?repo=&q=&type=&limit=`; required `repo`, `q`.
- `code_context` - `rel` enum
  `entity|neighbors|callers|callees|dependents|dependencies|file_imports|related|cross_repo`;
  required `repo`, `rel`; `id` is the entity id from `code_search`; `depth`
  default 1, max 4; extra args `relation` and `direction` (`out|in`) for
  `rel=neighbors`; `limit`.
- `code_graph` - `op` enum
  `path|important|stats|ambiguous|communities|hyperedges|bridges|resource_graph`;
  required `repo`, `op`; `from`/`to` for `op=path` only; `community` for
  `op=communities` (reads one community instead of the list); `id` for
  `op=hyperedges` (reads one hyperedge instead of the list); `limit`.
- `code_explain` - `GET /code-graph/explain?repo=&id=`; required `repo`, `id`.

### Memory (5)

- `memory_query` (`POST /queries`) / `memory_describe` (`POST /queries:describe`)
  - required `mode` (enum `local|global|hybrid|naive`), `text`; optional `top_k`.
- `memory_write` (`POST /memories`) - required `text`; optional `metadata`
  (string-valued object). Returns the `track_id`.
- `memory_entity(op=get|search|patch)` - `op` required.
  `op=get`/`op=patch` require `id`; `op=patch` also requires `patch` (the
  fields to change); `op=search` requires `q`; `limit` applies to `op=search`.
- `memory_edges(op=list|create|delete)` - `op` required. `op=list` takes
  optional `from`, `to`, `relation`, `limit`; `op=create` takes `from`, `to`,
  `relation`; `op=delete` requires `id`.

### Platform (7)

`task_get`, `task_list`, `task_context(task?, index?, notes?)`,
`task_note(kind, body)` (no `agent` argument - the operator stamps the
writer), `project_get`, `repo_list`. `task_note` replaces the entire chat
and handoff surface.

`report_internal_issue(category, description, severity?, offending_tool?,
resource_id?)` - required `category` (enum `tool_error`,
`directive_contradiction`, `workspace_broken`, `memory_inconsistent`,
`graph_inconsistent`, `auth`, `other`) and `description` (non-empty);
optional `severity` (`warn|error`, defaults `error`), `offending_tool`, and
`resource_id`. Emits a structured ERROR log and increments a metric; it does
NOT create a durable SCM issue.

`task_context`'s `notes` argument:

```json
{"type":"object","properties":{
  "task":{"type":"string","description":"Any Task in this project. Defaults to your own."},
  "index":{"type":"boolean","description":"Return the compact project-wide Task index instead of one Task's full bundle."},
  "notes":{"type":"string","enum":["recent","all"],
    "description":"recent (default) renders the notes in the bundle; all rehydrates the full note history, including notes spilled to memory. Use it when the <notes> marker says notes were elided."}},
 "additionalProperties":false}
```

## The `gh` / `glab` ban, with the workstation carve-out

!!! danger "In-cluster agent pods may not use `gh` or `glab`, and may not merge"
    This is enforced structurally: the pod has no forge token of its own for
    these paths, and the MCP profile exposes no merge action.
    `validate_skills.py` greps for `gh ` and `glab ` in any skill body.

!!! note "Workstation skills keep `gh` and keep human-driven merge"
    `start-development` and everything it drives is run by a human at a
    terminal with their own `gh` auth. It does not go through MCP profile
    gating, and the ban's enforcement mechanism does not reach it - so the
    ban does not apply to it. The rule is about what an autonomous pod may
    do with the platform's bot identity, not about what a human may do with
    their own.
