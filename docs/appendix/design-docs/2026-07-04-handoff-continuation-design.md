# Handoff-based continuation (replacing S3 conversation restore)

Date: 2026-07-04
Status: design (approved-by-delegation; user granted full autonomy)
Scope: tatara-chat, tatara-cli, tatara-claude-code-wrapper, tatara-agent-skills,
tatara-operator
Origin: token-conservation component 3 (handoff-replaces-S3), reshaped by the
user's directive that stale-session S3 restore cost is nonlinear and handoffs
are first-class queryable tatara-chat objects.

## Problem

A fresh agent pod today restores its predecessor's FULL conversation transcript
from S3 (`convstore.Restore` + `claude --resume`), so continuation re-bills the
entire prior conversation as cold input - a nonlinear cost that dwarfs the value
of the carried context. The intent is stateless pods that carry only a COMPACT
handoff ("where I left off + next steps"), stored where the platform's own
agents can query and groom it.

## Goals

- Replace S3 conversation restore with compact, queryable **handoffs** stored in
  tatara-chat (a new first-class entity), keyed by the existing stable-per-issue
  `CONVERSATION_OBJECT_KEY`.
- Agents write a handoff at end-of-work and read it at start, via chat MCP tools
  driven by the `/handoff` skill.
- The `refine` agent grooms handoffs (delete stale/done, keep fresh).
- The `brainstorm` agent prioritizes continuing open handoffs before fresh ideas.
- `refine` and `brainstorm` run under ONE schedule (refine, then brainstorm).

## Non-goals

- Conversation-level fidelity. Handoffs are summaries, not replayable
  transcripts; a fresh pod does not reconstruct the prior turn history.
- A generic chat entity store. Handoffs are a purpose-built table matching the
  chat house style (typed columns, hand-written SQL, no JSONB).
- Per-user RBAC on handoffs (chat auth is per-token identity, no RBAC; inherited
  unchanged).
- Keeping S3 conversation plumbing "just in case" - it is removed (the cost is
  the whole point).

## Design decisions

1. Storage = tatara-chat (user decision). A new `chat_handoffs` table, not the
   rooms/messages tables.
2. Handoff key = the wrapper's existing `CONVERSATION_OBJECT_KEY` ("stable per
   issue") - no new task identifier minted; the operator keeps passing it.
3. One current handoff per key (UNIQUE + upsert). "Open" = exists; refine
   deletes when done/stale. No status column (YAGNI).
4. Continuation is agent-driven: the wrapper surfaces the key in a first-turn
   preamble; the `/handoff` skill + `get_handoff` tool do the read. The wrapper
   holds no chat client.
5. Cron-merge reuses the EXISTING refine pre-scan barrier, re-scoped to the
   brainstorm tick - no new completion-hook machinery.

## The loop

```
work Task boots FRESH (no S3 restore) -> wrapper prepends a handoff preamble
  (the key) to the first goal -> agent (via /handoff skill) calls get_handoff
  -> resumes from the compact handoff -> does work -> write_handoff at end.

brainstorm cron tick -> refine Task created + barrier holds -> refine grooms
  handoffs (list_handoffs; delete stale/done) + issues -> refine terminal
  -> barrier releases -> brainstorm Task -> list_handoffs, prioritize
  continuing open handoffs, THEN fresh ideas.
```

## Component 1: tatara-chat handoff store (foundation)

A new vertical slice (chat has no generic store):

- **Migration** `internal/chat/migrations/0005_handoffs.sql`, wired at
  `internal/chat/migrate.go:24`:
  ```sql
  CREATE TABLE chat_handoffs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    handoff_key  TEXT NOT NULL UNIQUE,
    project      TEXT NOT NULL,
    repo         TEXT,
    kind         TEXT,
    created_by   TEXT NOT NULL,
    body         TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );
  CREATE INDEX chat_handoffs_project_idx ON chat_handoffs (project, repo, updated_at DESC);
  ```
- **Model** `Handoff` struct in `internal/chat/chat.go`.
- **Store methods** in `internal/chat/store.go`: `UpsertHandoff` (INSERT ... ON
  CONFLICT (handoff_key) DO UPDATE, latest-wins, sets updated_at + created_by),
  `GetHandoff(key)`, `ListHandoffs(project, repo)` (keyset/ordered by
  updated_at DESC, matching the existing pagination idiom), `DeleteHandoff(key)`.
- **Handler + routes** extend the `chatStore` interface (`handler.go:24-34`) +
  `Routes()` (`handler.go:141-151`): `POST /handoffs` (upsert),
  `GET /handoffs?project=&repo=` (list), `GET /handoffs/{key}`,
  `DELETE /handoffs/{key}`. Mounted in the existing auth+metrics group -
  authz inherited (per-token OIDC, `created_by` from claims).
- **Metrics** following `internal/chat/metrics.go`.

Tests: migration-count guard stays green; store CRUD + upsert-latest-wins;
handler routes; the auth group covers the new routes.

## Component 2: tatara-cli handoff MCP tools

Chat tools are data-driven REST defs (`Target: TargetChat`, a `Build` closure
returning method/path/body) dispatched via the shared generic client - no new
client needed.

- Add `HandoffTools()` after `tools.go:919` (all `Target: TargetChat`):
  - `write_handoff{handoff_key, project, repo, kind, body}` -> `POST /handoffs`
  - `list_handoffs{project, repo?}` -> `GET /handoffs?project=&repo=`
  - `get_handoff{handoff_key}` -> `GET /handoffs/{handoff_key}`
  - `delete_handoff{handoff_key}` -> `DELETE /handoffs/{handoff_key}`
- Register in the concat at `server.go:57` (all tools always registered for
  cache stability; call-time authz gates them - the Component-4a change).
- **Gating**: add `groupHandoff` near `profiles.go:71-83` + a `handoff bool` on
  `profileSpec` unioned in `resolveProfile` (`profiles.go:196-200`). Allocation:
  - `write_handoff`, `get_handoff`, `list_handoffs`: the work kinds that carry
    continuity - implement, issueLifecycle, incident, brainstorm, refine.
  - `delete_handoff`: refine only (the groomer).
- **Update the ~7 hardcoded tool-count assertions** (`tools_test.go:270-281`,
  `server_test.go:56,65,98,126,427`, `e2e_test.go:76`) for the +4 tools.

Tests: the 4 tools build the right method/path/body; groupHandoff gating
(refine can delete, others cannot; non-handoff profiles can't call any);
tool-count assertions updated.

## Component 3: tatara-claude-code-wrapper - drop S3, handoff preamble

- **Remove** the S3 path: the `convStore` restore/fork block
  (`cmd/wrapper/app.go:89-140`), the `OnTurnDone` upload (`app.go:220-245`),
  `resumeSID`/`ResumeSessionID`, the 3 `Conversation*` config fields
  (`config.go:91-100,188-190`), `S3Config()`/S3 fields, the `internal/convstore`
  + `internal/storage` packages, `ConversationOpsTotal`, and their tests.
  KEEP `CONVERSATION_OBJECT_KEY` as the handoff key (rename its doc, not the env,
  to avoid an operator-side change).
- **Continuation preamble**: in `postMessage` (`internal/httpapi/messages.go:69`),
  when this is the pod's FIRST goal submission and `CONVERSATION_OBJECT_KEY` is
  set, prepend a short preamble to the goal text before `Submit`:
  > "Continuation key: `<key>`. If you have prior context, call `get_handoff`
  > with this key before starting, and `write_handoff` an updated summary before
  > you finish."
  Track "first submission" with a simple bool on the session/app. The wrapper
  itself does NOT call chat - the agent does, via the MCP tool.
- The boot-crash fix already means fresh pods boot cleanly with no `--continue`;
  removing S3 restore means pods are always fresh + carry only the handoff.

Tests: the preamble is prepended on the first submission only, with the key;
absent when `CONVERSATION_OBJECT_KEY` is empty; no S3/convstore references remain
(build + a grep-guard test).

## Component 4: tatara-agent-skills - /handoff skill

Rework the `/handoff` skill to the chat-backed flow:
- **At start** (resuming): call `get_handoff{handoff_key}` (key from the
  wrapper preamble); if present, load it as the working context; if absent,
  start fresh.
- **At end** (checkpoint): call `write_handoff{handoff_key, project, repo, kind,
  body}` with a COMPACT markdown summary: current state, what's done, what's
  next, open questions, key file/PR refs. Keep it short (a summary, not a log).
- Tag with the `profiles:` that carry continuity (implement, issueLifecycle,
  incident, brainstorm, refine) so `TATARA_SKILL_PROFILE` installs it there.

## Component 5: tatara-operator - groom, prioritize, merge

- **refine grooms handoffs**: extend `refine.GoalProject` (`internal/refine/goal.go:34`)
  to instruct: list_handoffs for the project; delete handoffs that are stale/done
  (issue closed/resolved, clearly superseded, or aged with no matching open
  work), keep the rest. refine's profile already gets list+delete (Component 2).
- **brainstorm prioritizes handoffs**: extend `brainstormGoalProject`
  (`projectscan.go:1716`) to instruct: first list_handoffs + propose continuing
  the open ones that still matter, THEN generate fresh ideas (subject to the
  existing maxOpenProposals cap).
- **cron-merge**: re-scope the existing refine pre-scan barrier
  (`projectscan.go:2745-2799`) so it fires on the **brainstorm** cron tick rather
  than the `ClosedLookbackDays>0` opt-in. On a due brainstorm tick: create the
  refine Task, `requeueRefineBarrier` (30s poll); when refine reaches
  `TaskTerminal`, release into `r.brainstorm(...)`. Reuses
  `latestTerminalRefineTask`/`stampRefine`/`refineNeededThisCycle`. A Failed
  refine still releases the gate. Drop refine from gating mrScan/issueScan/
  healthCheck (those no longer wait on it). `ClosedLookbackDays` stays as the
  refine lookback input; its role as the enable-gate moves to "brainstorm
  enabled".

Tests (envtest): the barrier fires refine on a brainstorm tick and releases to
brainstorm on refine-terminal; the refine goal contains the handoff-groom
instruction; the brainstorm goal contains the handoff-prioritize instruction.

## Cross-repo change list + build order

| Order | Repo | Change |
|---|---|---|
| 1 | tatara-chat | chat_handoffs slice (migration+model+store+handler+routes+metrics+tests) |
| 2 | tatara-cli | HandoffTools() + groupHandoff gating + tool-count assertions |
| 3a | tatara-claude-code-wrapper | remove S3 conv restore/upload; first-turn handoff preamble; bake the new cli |
| 3b | tatara-agent-skills | /handoff skill chat-backed rewrite |
| 3c | tatara-operator | refine-groom + brainstorm-prioritize goals; barrier re-scope to brainstorm tick |

1 and 2 are the foundation; 3a/3b/3c depend on the MCP tools existing but are
independent of each other.

## Deploy coordination

chat merges -> chat image (+ helmfile chat pin if chat is a helmfile release).
cli merges -> cli image -> wrapper bakes `TATARA_CLI_VERSION` -> wrapper image ->
helmfile `agent.image`. operator merges -> operator image + charts -> helmfile
dual-pin. skills -> `skillsRef` pin bump. Sequence chat+cli first, then the
three consumers, then a helmfile MR carrying all the pins. Gated on burn as
usual; the fleet is currently running steady-state.

## Acceptance criteria

- A fresh pod boots with NO S3 restore; the first goal carries the handoff
  preamble; an agent resumes via `get_handoff` and checkpoints via
  `write_handoff`.
- Handoffs are queryable in chat (`GET /handoffs?project=`) and survive across
  pods.
- refine deletes a stale handoff (e.g. a closed issue's) and keeps a live one.
- brainstorm proposes continuing an open handoff before fresh ideas.
- One brainstorm cron tick runs refine then brainstorm (barrier), no separate
  refine schedule.
- No S3 conversation code remains in the wrapper.

## Related

`docs/superpowers/specs/2026-07-04-token-conservation-design.md` (component 3),
`docs/2026-06-28-refine-rework-design.md` (the barrier this reuses),
[[tatara-token-conservation-2026-07-04]],
[[refine-agent-backlog-groomer-2026-06-28]], [[project-level-brainstorm]],
[[envboolor-empty-bootcrash-2026-06-28]] (env-contract care when removing the
S3/Conversation* vars).
