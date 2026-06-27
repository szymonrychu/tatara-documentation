---
title: tatara-claude-code-wrapper
---

# tatara-claude-code-wrapper

A single-session Claude Code supervisor. One pod runs one persistent interactive `claude` process driven over a PTY, submits one user turn at a time, captures each turn's result via a custom Stop hook, and exposes the whole thing as an OIDC-gated HTTP API with webhook-or-poll delivery.

**Repository:** [`github.com/szymonrychu/tatara-claude-code-wrapper`](https://github.com/szymonrychu/tatara-claude-code-wrapper)

## Why interactive-over-PTY, not `claude -p`

`claude -p` (print/headless mode) is a divergent codepath from the interactive TUI: different system prompt assembly, different skill and hook behavior, different permission UX. The wrapper allocates a PTY, spawns interactive `claude` as if a terminal were attached, and types each message in via bracketed paste + submit. Terminal output is never parsed for results - it is ring-buffered only for boot-dialog detection and debug logging. Results come from the Stop hook and the on-disk transcript.

This gives agent sessions the full Claude Code harness: skills, slash commands, normal hook and permission behavior.

## Boot sequence

1. **Load config** from env (scalars) and mounted ConfigMap files.
2. **Bootstrap** renders all files claude reads at startup:
   - Clone `REPO_URL@REPO_BRANCH` into `/workspace` (optional)
   - `/workspace/.mcp.json` (tatara-cli + any overlays)
   - `~/.claude/settings.json` (Stop hook path, `bypassPermissions`, MCP auto-enable, denied interactive pickers)
   - `~/.claude.json` (seeds onboarding flags so no interactive dialogs appear)
   - `~/.claude/CLAUDE.md` and `/workspace/CLAUDE.md`
   - Skills: baked (`/templates/skills`) + custom (`/etc/wrapper/skills`)
3. **Spawn `claude`** under a PTY. Start the ring-buffer reader and process-wait goroutine.
4. **`bootWait`**: The "Bypass Permissions mode" warning is not seedable and appears on every boot. The wrapper detects it in the ring buffer (ANSI/whitespace-stripped matching) and accepts it (Down + Enter). Then waits for output quiescence (no new PTY bytes for >1.5s, floored at ~4s) before marking the session ready.
5. **Start HTTP servers.** `/readyz` is not served until `Start` returns.

## Turn lifecycle

```
POST /v1/messages -> wrapper types message into PTY -> 202 {turnId}
claude works (MCP calls, file edits) -> end of turn -> cc-stop-hook runs
  hook reads last_assistant_message + transcript -> POSTs to loopback
wrapper records result -> POSTs to callbackUrl (operator); pollable via GET
```

Turns are strictly sequential. A second `POST /v1/messages` while a turn is in flight returns `409`.

## API

All `/v1/*` require an OIDC bearer token (audience `tatara-claude-code-wrapper`).

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/messages` | Submit a turn `{text, callbackUrl?}` → `202 {turnId}` |
| `POST` | `/v1/interject` | Inject `{text}` into the turn in flight → `202` |
| `GET` | `/v1/messages` | Turn history `[{turnId, state, startedAt, completedAt}]` |
| `GET` | `/v1/messages/{turnId}` | Full turn result: `{state, finalText, usage, stopReason, error?}` |
| `GET` | `/v1/session` | `{state, turnsCompleted, model, repo}` |
| `GET` | `/v1/transcript` | Full JSONL session transcript (debug) |
| `DELETE` | `/v1/session` | Graceful shutdown, pod exits |
| `GET` | `/healthz` `/readyz` `/metrics` | Operator endpoints (not exposed via ingress) |

## Conversation persistence

When `CONVERSATION_OBJECT_KEY` and `CONVERSATION_SESSION_ID` are set (injected by the operator from `Task.status`), the wrapper resumes the prior session. Under the handover threshold (default 25% of context window), the full transcript is replayed. At or above the threshold, only the compacted `Handover` text is passed as context and the session starts fresh from a summarized state.

## Lifecycle hooks

Shell commands the operator delivers as `HOOK_*` env vars, executed via `sh -c` at fixed points:

| Hook | Fires |
|---|---|
| `preClone` | Before each repo clone |
| `postClone` | After clone + checkout |
| `conversationStart` | Once after session boots |
| `conversationRestart` | After crash-relaunch |
| `agentTurnFinished` | After turn committed + callback delivered |
| `conversationFinished` | During session teardown |

Non-zero hook exit is logged and counted but never aborts the agent run.

## Configuration

All scalars via env (from chart ConfigMap `envFrom`):

| Var | Default | Description |
|---|---|---|
| `HTTP_ADDR` | `:8080` | Public API listen address |
| `INTERNAL_ADDR` | `:9090` | Loopback for Stop hook callback |
| `OIDC_ISSUER` | - | Keycloak issuer URL |
| `OIDC_AUDIENCE` | `tatara-claude-code-wrapper` | Expected token audience |
| `MODEL` | `claude-sonnet-4-6` | Claude model ID |
| `PERMISSION_MODE` | `bypassPermissions` | Claude permission mode |
| `REPO_URL` / `REPO_BRANCH` | - | Repository to clone (optional) |
| `TURN_TIMEOUT_SECONDS` | `1800` | Per-turn inactivity timeout |
| `BOOT_TIMEOUT_SECONDS` | `60` | Max wait for boot quiescence |
| `ANTHROPIC_API_KEY` | - | Long-lived Claude API key (from Secret) |

File/list config is mounted under `/etc/wrapper` (chart values: `globalClaudeMd`, `projectClaudeMd`, `baseMcp`, `extraMcpServers`, `allowedTools`, custom skills).

## Metrics

| Metric | Description |
|---|---|
| `ccw_turns_total` | Turn completions by result |
| `ccw_token_usage_total` | Cumulative token usage by type (input/output/cache) |
| `ccw_session_state` | Current session state gauge |
| `ccw_boot_duration_seconds` | Boot sequence duration histogram |
