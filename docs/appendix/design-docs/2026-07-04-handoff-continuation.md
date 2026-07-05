# Handoff-based continuation - Implementation Plan

> **For agentic workers:** implement per-repo via workflows (subagent-driven TDD). Each repo's component is fully specified in `docs/superpowers/specs/2026-07-04-handoff-continuation-design.md` - read the matching component section as your requirements. This plan pins the shared cross-repo CONTRACT (which every repo must match exactly) + the build/deploy order.

**Goal:** Replace S3 conversation restore with compact handoffs stored in tatara-chat, queryable + groomable by the platform's own agents.

## Global Constraints

- 5 repos, each branch off FRESH origin/main (external bots push). Worktree off main; never build/deploy from a worktree.
- Newest stable Go; JSON logs via slog; follow each repo's existing patterns exactly (chat: hand-written SQL + typed columns + no JSONB; cli: data-driven REST tool defs; operator: barrier/cron idioms).
- TDD (failing test first). gofmt/vet clean. Commit messages + PR bodies end with `https://claude.ai/code/session_01GVnfZiAkBdLANE2n3uN9BK`.
- Deploy is gated/coordinated separately - do NOT deploy from this plan.

## THE SHARED CONTRACT (every repo matches this verbatim)

**Handoff key:** the wrapper's existing `CONVERSATION_OBJECT_KEY` (stable per issue). No new identifier.

**tatara-chat REST API** (under the existing `/api/v1/chat` auth group):
- `POST /handoffs` body `{handoff_key, project, repo, kind, body}` -> upsert (ON CONFLICT(handoff_key) DO UPDATE), returns the handoff.
- `GET /handoffs?project=<p>&repo=<r>` -> list (repo optional), ordered updated_at DESC.
- `GET /handoffs/{handoff_key}` -> one, 404 if absent.
- `DELETE /handoffs/{handoff_key}` -> delete, idempotent.

**tatara-chat table** `chat_handoffs(id UUID pk, handoff_key TEXT UNIQUE NOT NULL, project TEXT NOT NULL, repo TEXT, kind TEXT, created_by TEXT NOT NULL, body TEXT NOT NULL, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ)`.

**cli MCP tools** (Target: TargetChat), names + args EXACTLY:
- `write_handoff{handoff_key, project, repo, kind, body}` -> POST /handoffs
- `list_handoffs{project, repo?}` -> GET /handoffs?project=&repo=
- `get_handoff{handoff_key}` -> GET /handoffs/{handoff_key}
- `delete_handoff{handoff_key}` -> DELETE /handoffs/{handoff_key}
- gating group `groupHandoff`: write/get/list on profiles {implement, issueLifecycle, incident, brainstorm, refine}; delete on {refine} only.

**wrapper preamble** (first goal submission, when CONVERSATION_OBJECT_KEY set): prepend
`Continuation key: <key>. If you have prior context, call get_handoff with this key before starting, and write_handoff an updated summary before you finish.`

## Tasks (by repo = one workflow each; see spec component for detail)

- **T1 tatara-chat** (spec Component 1): migration 0005_handoffs.sql wired at migrate.go:24; Handoff model; Store Upsert/Get/List/Delete; chatStore iface + handler + routes; metrics; tests (migration-count guard, store CRUD + upsert-latest-wins, handler routes).
- **T2 tatara-cli** (spec Component 2): HandoffTools() (4 tools, Target TargetChat) registered at server.go:57; groupHandoff + handoff bool on profileSpec + resolveProfile union; UPDATE the ~7 hardcoded tool-count assertions (tools_test.go:270-281, server_test.go:56/65/98/126/427, e2e_test.go:76); tests for build/gating.
- **T3 tatara-claude-code-wrapper** (spec Component 3): remove convStore restore/fork (app.go:89-140) + OnTurnDone upload (:220-245) + Conversation-restore/S3 fields + convstore/storage packages + metric + tests; KEEP CONVERSATION_OBJECT_KEY as the handoff key; add the first-submission handoff preamble in postMessage (messages.go:69); grep-guard test that no S3 conv code remains. (Bake bump to the new cli happens at deploy, not here.)
- **T4 tatara-agent-skills** (spec Component 4): rewrite /handoff SKILL.md to the chat-backed flow (get_handoff at start, write_handoff compact summary at end); `profiles:` = {implement, issueLifecycle, incident, brainstorm, refine}; keep it short.
- **T5 tatara-operator** (spec Component 5): refine.GoalProject += handoff-groom instruction; brainstormGoalProject += handoff-prioritize instruction; re-scope the refine pre-scan barrier (projectscan.go:2745-2799) to fire on the brainstorm cron tick (create refine -> requeueRefineBarrier -> on refine terminal release into r.brainstorm), drop refine gating of mrScan/issueScan/healthCheck; envtest for the barrier + goal contents.

## Build + deploy order

Implement T1+T2 (foundation) first, then T3/T4/T5 (independent consumers) - all reference the contract above, not each other's code. Deploy: chat -> chat image (+helmfile chat pin); cli -> cli image -> wrapper bakes TATARA_CLI_VERSION -> wrapper image -> helmfile agent.image; operator -> operator image+charts -> helmfile dual-pin; skills -> skillsRef pin. One helmfile MR carries all pins once images publish.

## Self-review

Contract is the single source of cross-repo truth; each task matches it verbatim. T1/T2 have no inbound deps; T3/T4/T5 depend only on the contract (compile-independent of chat/cli). Build order respects deploy deps (chat+cli images before wrapper bake). No placeholder tasks - each points to a fully-specified spec component.
