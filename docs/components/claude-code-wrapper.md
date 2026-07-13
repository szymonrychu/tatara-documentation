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
| `POST` | `/v1/messages` | Submit a turn `{text, callbackUrl?, handoff?}` -> `202 {turnId}`; `409` turn in flight; **`410 Gone`** past the pod's TTL deadline |
| `GET` | `/v1/messages` | Turn history `[{turnId, state, startedAt, completedAt}]` |
| `GET` | `/v1/messages/{turnId}` | Full turn result: `{state, finalText, usage, stopReason, error?}` |
| `GET` | `/v1/session` | The six existing fields (`state`, `turnsCompleted`, `model`, `repo`, ...) **plus `contractVersion`** <!-- stale-ok: turnsCompleted --> |
| `GET` | `/v1/transcript` | Full JSONL session transcript (debug) |
| `DELETE` | `/v1/session` | Graceful shutdown, pod exits |
| `GET` | `/healthz` `/readyz` `/metrics` | Operator endpoints (not exposed via ingress) |

!!! danger "`POST /v1/interject` is deleted, not deprecated"
    The endpoint raced the Stop hook against the ring-buffer tailer: injecting text mid-turn had no ordering guarantee against the hook reading the transcript at turn-end, so a well-timed interject could be silently lost or attributed to the wrong turn. It was live in production until this release (the operator drove it from a `PendingInterjections` drain loop). It is removed in the same change that removes the operator's drain loop, operator first, so the wrapper never 404s a call the operator is still making. `Session.Interject` and `ErrNotBusy` are deleted with it. <!-- stale-ok: /v1/interject, PendingInterjections -->

## Contract-version handshake

The wrapper image and the operator image ship in different helm releases and can apply concurrently, so a window where a new operator pairs with an old agent image is reachable. To fail that fast instead of burning a full turn budget against a 404ing tool surface:

1. `contractVersion` is a compile-time constant in the wrapper binary (`const ContractVersion = 2`), bumped in the same release that ships a new tool surface. It is reported on every `GET /v1/session` response.
2. The operator injects `TATARA_CONTRACT_VERSION=2` into every agent pod's env (read by tatara-cli, not by the wrapper itself).
3. Before submitting a pod's turn-0, the operator reads `GET /v1/session` and compares the reported `contractVersion` against its own expectation. On a mismatch, or a response with no `contractVersion` field at all (an old wrapper), the operator fails the Task instantly with `stageReason=agent-contract-mismatch` and never submits a turn - zero tokens burned.

See [tatara-cli](cli.md#contract-version-handshake) for the third defense: the cli's MCP server refusing to start on a mismatched `TATARA_CONTRACT_VERSION`.

## Continuity across pods: no session resume

There is no `--resume`, no session replay, and no transcript persisted to S3. `CONVERSATION_SESSION_ID`, `CONVERSATION_OBJECT_KEY`, and `HANDOFF_KEY` are all deleted, and the wrapper makes no fork/replay decision at all. <!-- stale-ok: CONVERSATION_SESSION_ID, CONVERSATION_OBJECT_KEY, HANDOFF_KEY, --resume -->

Every pod's turn-0 context bundle is rendered fresh by the operator, identically every time. Continuity between one pod and the next pod of the same Task comes entirely from `Task.status.notes` - the append-only journal every pod reads at turn 0 - not from replaying a transcript. When a pod is stopped (TTL, crash, or graceful shutdown), the thing that must survive is a note in that journal, not a resumable session; see [Pod TTL: the stop sequence](#pod-ttl-the-stop-sequence) below for how the operator guarantees one is always written.

## Pod TTL: the stop sequence

`AGENT_POD_TTL_SECONDS` (from `Project.spec.agentPodTTLSeconds`, default 3600) bounds one pod's life, not the Task - the Task persists across as many pods as it needs. The wrapper computes `t0 = pod start + AGENT_POD_TTL_SECONDS`; the operator (the wrapper's only client) drives the rest of the sequence around that clock:

1. **The wrapper stops admitting normal turns past `t0`.** Any `POST /v1/messages` with `handoff` unset or `false` after `t0` gets `410 Gone`. It still accepts exactly one turn with `handoff: true` - without that carve-out, the handoff turn in step 3 would be refused by this same rule, and `Task.status.notes` would end up empty on every TTL stop.
2. **The operator waits for any in-flight turn's callback**, bounded by `TURN_TIMEOUT_SECONDS`. A pod is mid-turn at TTL expiry essentially always, and `POST /v1/messages` already `409`s while a turn is in flight, so the handoff turn cannot simply be submitted immediately.
3. **The operator submits exactly one `handoff: true` turn**, asking the agent to call `task_note(kind=handoff)` with everything the next pod needs, bounded by `TURN_TIMEOUT_SECONDS`.
4. **Hard cap at `t0 + 2*TURN_TIMEOUT_SECONDS + 60s`.** On that cap, or on any `410`/`409`/5xx from step 3, the operator writes a synthetic handoff note in-process from the last turn's final text plus which repos were pushed, then force-deletes the pod.

`Task.status.notes` is never empty after a TTL stop: either the agent wrote a handoff note, or the operator wrote a synthetic one.

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
| `TURN_TIMEOUT_SECONDS` | `1800` | Per-turn inactivity timeout; also bounds each step of the [pod TTL stop sequence](#pod-ttl-the-stop-sequence) |
| `BOOT_TIMEOUT_SECONDS` | `60` | Max wait for boot quiescence |
| `AGENT_POD_TTL_SECONDS` | `3600` (from `Project.spec.agentPodTTLSeconds`) | Bounds this pod's life; the wrapper computes `t0` from it and enforces the [stop sequence](#pod-ttl-the-stop-sequence) |
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
