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

The wrapper is a consumer here, not the decision maker. The operator owns the fork/replay choice: `Project.spec.agent.handoverThresholdPercent` (default 25) as a share of `Project.spec.agent.contextWindowTokens`, measured against the last turn's input tokens. Based on that, the operator injects env into the pod and the wrapper reacts:

- Under threshold: the operator emits both `CONVERSATION_OBJECT_KEY` and `CONVERSATION_SESSION_ID`, and the wrapper does a full transcript replay (`claude --resume`) of the prior session.
- At or above threshold: the operator emits `CONVERSATION_OBJECT_KEY` but **omits** `CONVERSATION_SESSION_ID`, and instead injects a compacted `## Resume from handover` block into the turn-0 prompt. The wrapper starts a fresh session seeded from that summary rather than replaying.

The threshold, the context-window size, and the handover text are all operator concerns. The wrapper only reads the injected env and either resumes or starts fresh. Conversation persistence is gated on the operator having an S3 bucket configured (`S3_BUCKET`); with no bucket the operator injects no conversation env and every pod starts empty.

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
| `INTERNAL_ADDR` | `127.0.0.1:8090` | Loopback for Stop hook callback |
| `OIDC_ISSUER` | `https://auth.szymonrichert.pl/realms/master` | Keycloak issuer URL |
| `OIDC_AUDIENCE` | `tatara-claude-code-wrapper` | Expected token audience |
| `MODEL` | `""` (empty) | Claude model ID. The wrapper bakes **no** default; the operator sets the model per Task from `Project.spec.agent.model` / `modelByKind`. |
| `PERMISSION_MODE` | `bypassPermissions` | Claude permission mode |
| `REPO_URL` / `REPO_BRANCH` | - | Repository to clone (optional) |
| `TURN_TIMEOUT_SECONDS` | `1800` | Per-turn inactivity timeout |
| `BOOT_TIMEOUT_SECONDS` | `60` | Max wait for boot quiescence |
| `CLAUDE_CODE_OAUTH_TOKEN` | - | Claude subscription OAuth token. This is what the operator actually injects (from the `anthropicSecretName` Secret, key `oauth-token`); Claude Code reads it directly. |
| `ANTHROPIC_API_KEY` | - | Alternative metered Anthropic API key. Supported (used to pre-seed the trust dialog) but **not** what the deployed platform injects. |

File/list config is mounted under `/etc/wrapper` (chart values: `globalClaudeMd`, `projectClaudeMd`, `baseMcp`, `extraMcpServers`, `allowedTools`, custom skills).

## Metrics

| Metric | Type | Description |
|---|---|---|
| `ccw_turns_total` | counter | Turn completions by result |
| `ccw_turn_tokens_total` | counter | Claude tokens per turn by type/model/kind/repo/project |
| `ccw_turn_cost_usd_total` | counter | Cumulative turn cost in USD by kind/repo/project |
| `ccw_turn_in_flight` | gauge | Turns currently in flight (0 or 1) |
| `ccw_bootstrap_duration_seconds` | histogram | Full bootstrap (`Render()`) duration |
| `ccw_tool_calls_total` | counter | Agent tool calls observed in the transcript by tool and outcome |
