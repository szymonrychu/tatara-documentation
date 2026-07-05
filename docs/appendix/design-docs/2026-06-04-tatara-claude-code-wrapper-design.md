# tatara-claude-code-wrapper - Design

**Date:** 2026-06-04
**Phase:** 4
**Repo:** new `github.com/szymonrychu/tatara-claude-code-wrapper`
**Successor specs:** none yet

## Goal

A single-session Claude Code supervisor packaged as a container. One pod
runs one persistent interactive `claude` process driven over a PTY; each turn's
result is captured by a custom Stop hook and returned to the caller by
webhook or poll. A thin OIDC-gated HTTP API fronts the session. The whole
point is to avoid one-shot `claude -p`: the session stays alive across many
turns, keeping context.

This replaces nothing directly. The tatara-old `images/agent-claude` is the
spiritual ancestor (headless claude + Stop hook posting results to a bridge),
but that was wired to the dropped smith/bellows/spawn orchestrator. This
component keeps only the headless-claude + Stop-hook pattern and drops the
CRD machinery.

## Resolved design decisions

1. **Persistent multi-turn session**, not one-shot `claude -p`.
2. **One session per pod.** No multiplexing, no session registry. K8s /
   argo owns "which pod, keep it alive, route the next turn." Out of scope
   here.
3. **Input by driving the real interactive REPL over a PTY**, output via
   **custom Stop hook**. `claude` runs in its full interactive harness
   (the same codepath a human gets: skills, slash commands, normal hook
   and permission UX). **`-p`/`--print` is strictly forbidden** - headless
   print mode is a divergent codepath and is exactly what this component
   exists to avoid. The wrapper allocates a PTY, spawns `claude` attached
   to it as a terminal, and "types" each user message in (bracketed paste +
   submit key). The Stop hook fires at end-of-turn and posts the result
   back to the wrapper. Terminal output is drained/logged, never parsed for
   results - the hook plus the transcript file are the only result source.
   No `--input-format`/`--output-format`/stream-json anywhere.
4. **Async submit + webhook callback, with poll fallback.** `callbackUrl`
   is optional. Every turn result is persisted and retrievable by GET.
5. **Workspace: repo optional.** Scratch `/workspace` by default; clone a
   repo first if `repoUrl` is set. One code path.
6. **API auth: OIDC bearer** (Keycloak master realm), same model as
   tatara-memory. New audience `tatara-claude-code-wrapper`.
7. **Session config baked at pod start, eager boot.** No create endpoint;
   immutable per-pod session config from env + mounted ConfigMaps.

## Language and platform rules

- **Go** for the wrapper service and the `cc-stop-hook` binary (newest
  stable Go, pinned exact minor in `go.mod`).
- **Node** runs the `claude` CLI (npm `@anthropic-ai/claude-code`).
- slog JSON logging everywhere. Every business action at INFO with
  `turn_id`, `session_id`, `duration_ms`.
- `/metrics` Prometheus endpoint. Chart via `helm create` then trimmed.
- Hard rule 6 honored: camelCase scalar in `values.yaml` -> kebab-case key
  in ConfigMap/Secret -> consumed via `envFrom`. List- and file-shaped data
  (system files, MCP overlays, skills, CLAUDE.md bodies) rendered into
  templated ConfigMaps and read at runtime. No plain ENVs or lists in
  `values.yaml`.

## Process model inside the pod

The Go wrapper is PID 1. On start it:

1. Reads config: env scalars + mounted ConfigMap files.
2. Renders boot-time files:
   - `~/.claude/CLAUDE.md` from `globalClaudeMd` (if set).
   - `/workspace/CLAUDE.md` from `projectClaudeMd` (if set).
   - `/workspace/.mcp.json` = base tatara-cli memory server merged with any
     fragments in `/etc/wrapper/mcp.d/`.
   - `~/.claude/settings.json` wiring the **Stop hook** to
     `/usr/local/bin/cc-stop-hook`, `permissions.defaultMode:
     bypassPermissions`, and `enableAllProjectMcpServers: true`.
   - `~/.claude.json` seeded for a no-dialog unattended boot (onboarding,
     api-key-suffix approval, `/workspace` trust - see the spike recipe
     above).
   - Copies bundled skills + any custom skills from `/etc/wrapper/skills/`
     into `/workspace/.claude/skills/`.
3. If `repoUrl` is set, clones it (`repoBranch`) into `/workspace`;
   otherwise `/workspace` stays empty scratch.
4. Allocates a PTY (`github.com/creack/pty`), sets `TERM` + a window size,
   and spawns interactive `claude` attached to it (no `-p`). Waits for the
   prompt to be ready, then accepts turns. Eager boot.

Because the pod is unattended, claude must never deadlock on an interactive
boot dialog. The Task-1 spike (see the child repo `docs/spike-findings.md`)
established the exact no-dialog recipe against the real binary: pre-seed
`~/.claude.json` with `hasCompletedOnboarding: true`,
`customApiKeyResponses.approved: ["<last 20 chars of ANTHROPIC_API_KEY>"]`,
and `projects["/workspace"].hasTrustDialogAccepted: true`; set
`permissions.defaultMode: bypassPermissions` and
`enableAllProjectMcpServers: true` in `~/.claude/settings.json`; and launch
`claude` with **no** permission flag. Seeding suppresses the onboarding,
folder-trust, and custom-API-key dialogs. The one dialog that is NOT seedable
is the "Bypass Permissions mode" warning, which claude shows on every boot
whenever bypass is active: the wrapper's boot routine detects it in the
PTY ring buffer and accepts it (Down+Enter) before accepting turns. Turn
submission is two PTY writes (bracketed-paste text, ~400ms pause, then CR);
a single concatenated write does not submit. The harness stays fully
interactive and auto-proceeds. This is the concession to unattended
operation; everything else is the standard interactive experience. (Validated
end-to-end against real claude v2.1.162 during implementation.)

`claude` authenticates to Anthropic via `ANTHROPIC_API_KEY` (sops secret).
Bedrock/Vertex is a future env-passthrough toggle, out of scope for v0.1.0.
The bundled tatara-cli MCP authenticates to tatara-memory via
client-credentials (sops: client id + secret), so claude has memory tools
out of the box.

### Driving the interactive session (PTY)

- **Submit a turn**: write the message to the PTY master wrapped in
  bracketed-paste markers (`ESC[200~` ... `ESC[201~`) so embedded newlines
  do not submit early, then send the submit key (`\r`). Exact sequence is
  pinned by the Task-1 spike against the real binary.
- **Readiness**: the wrapper marks the session READY once the TUI prompt has
  rendered (heuristic captured in the spike) or after a bounded boot
  timeout; `/readyz` reflects this.
- **Interrupt**: the interrupt/`DELETE` path sends `ESC` (and `Ctrl-C` for
  shutdown) to the PTY.
- **No terminal parsing**: PTY output is copied to a ring buffer for
  `/healthz` liveness + debug logging only. Turn results come from the Stop
  hook and the transcript JSONL.

## Turn lifecycle

```
caller --POST /v1/messages {text, callbackUrl?}--> wrapper
   wrapper: turnId=new; type msg into PTY (bracketed-paste + submit); mark BUSY;
            persist turn record (state=running)
   wrapper --202 {turnId}--> caller
   ...claude works (MCP calls, edits in /workspace)...
   claude end-of-turn --runs--> cc-stop-hook
       hook reads transcript_path from its stdin hook payload,
       extracts final assistant message (+ /workspace/result.json if present),
       --POST 127.0.0.1:<internalPort>/internal/turn-complete--> wrapper
   wrapper: persist turn result; mark IDLE
   wrapper --POST callbackUrl {turnId, finalText, resultJson?, usage, stopReason}--> caller
            (only if callbackUrl was provided)
```

Turns are strictly sequential - one `claude` process handles one user
message at a time. `POST /v1/messages` while BUSY returns **409**; the caller
waits for the result (webhook or poll) before sending the next turn.
Queueing is deferred. The internal `/internal/turn-complete` listener binds
to `127.0.0.1` only and is not OIDC-gated.

## API surface

OIDC bearer on all `/v1/*` (verify JWT signature via JWKS, `iss`, `exp`,
`aud` contains `tatara-claude-code-wrapper`). Operator endpoints are
unauthenticated and not ingress-exposed.

- `POST /v1/messages` `{text, callbackUrl?}` -> `202 {turnId}` or `409` if
  busy. `callbackUrl` optional (push if present, poll otherwise).
- `GET /v1/messages` -> `[{turnId, state, startedAt, completedAt}]` turn
  history.
- `GET /v1/messages/{turnId}` -> full result `{turnId, state, finalText,
  resultJson?, usage, stopReason, error?}`. Poll / missed-callback path.
- `GET /v1/session` -> `{state, turnsCompleted, model, repo, uptime}`.
- `GET /v1/transcript` -> full JSONL session transcript (debugging).
- `DELETE /v1/session` -> graceful claude shutdown, then pod exits.
- `/healthz`, `/readyz` (readyz fails if claude died), `/metrics`.

ClusterIP only for v0.1.0. In-cluster callers (argo / a future controller).
Ingress deferred - one-session-per-pod makes external routing pointless
until a router/controller exists.

## Turn persistence

In-memory map `turnId -> result` plus the on-disk JSONL transcript as
the durable record. `GET /v1/messages*` reads the in-memory store;
`GET /v1/transcript` reads the file. For history that survives a pod
restart, `/workspace` (including the transcript) can be backed by a PVC -
chart toggle, default `emptyDir`.

## Configuration (hard rule 6)

**Scalars** (`values.yaml` camelCase -> kebab ConfigMap key -> `envFrom`):
`model`, `repoUrl`, `repoBranch`, `defaultCallbackUrl`,
`turnTimeoutSeconds` (default 1800), `webhookRetries` (default 3),
`internalPort`,
`httpPort`, OIDC issuer, OIDC audience.

**File / list / multiline-shaped** (templated ConfigMaps, mounted, read at
runtime - never inline in values):
- `globalClaudeMd` -> `~/.claude/CLAUDE.md`
- `projectClaudeMd` -> `/workspace/CLAUDE.md`
- `extraMcpServers` -> ConfigMap mounted at `/etc/wrapper/mcp.d/`, merged
  into the effective `.mcp.json` at boot.
- `skills` (custom SKILL.md sets) -> ConfigMap mounted at
  `/etc/wrapper/skills/` (small sets) or a PVC / init-container git-clone
  (larger sets) - chart-selectable. Copied into `/workspace/.claude/skills/`
  alongside the image-baked default skills.
- `allowedTools` (list) -> templated ConfigMap consumed by the rendered
  `settings.json`.

**Secrets** (sops): `ANTHROPIC_API_KEY`, tatara-memory client id + secret,
git credentials (only if a private repo is cloned).

## Extensibility

- **Additional MCP servers**: `extraMcpServers` fragments merged with the
  baked-in tatara-cli memory server. Lets a deployment add e.g. tatara-tasks
  MCP later without rebuilding the image.
- **Skills, including custom**: bundled set (`/handoff` etc.) plus
  caller-supplied skills via the mounted overlay. Both end up in the same
  `/workspace/.claude/skills/` path.
- **CLAUDE.md**: global and project scopes both configurable.

## Container modularity

The Dockerfile isolates each moving part behind a build ARG so bumping
claude is a one-line change + rebuild that touches nothing else:

```dockerfile
ARG CLAUDE_CODE_VERSION      # pinned, e.g. 1.x.y
ARG TATARA_CLI_VERSION       # tatara-cli binary pulled at this version
ARG NODE_VERSION=22
```

- Stage 1 (`golang:<newest>`): build `wrapper` + `cc-stop-hook`.
- Stage 2: fetch the tatara-cli binary at `TATARA_CLI_VERSION` (from harbor
  release or a tatara-cli image).
- Stage 3 (`node:${NODE_VERSION}-bookworm-slim`):
  `npm i -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}` in its own
  layer; copy the Go binaries, tatara-cli, git, baked skills, and template
  overlays as separate layers.

claude lives in its own layer; bumping it does not rebuild the Go binaries
(layer cache) and never touches the overlay config. A
`make bump-claude VERSION=...` target flips the ARG. The pinned-ARG build is
the reproducible default; a future deploy-time version override is possible
but not required.

## Observability

- Metrics: `ccw_turns_total`, `ccw_turn_duration_seconds` (histogram),
  `ccw_turn_in_flight` (gauge), `ccw_claude_restarts_total`,
  `ccw_webhook_delivery_total{result}`, `ccw_hook_received_total`.
- INFO log per business action with structured fields. WARN/ERROR for
  timeouts, webhook drops, claude exits.

## Failure modes

- **claude dies**: `/readyz` goes unhealthy, wrapper exits, K8s restarts the
  pod. In-RAM context is lost in v0.1.0. Because the transcript is persisted
  and `/workspace` is PVC-able, `claude --resume`-from-transcript is a clean
  v0.2 follow-up, not a redesign. Known limitation.
- **turn timeout**: no Stop-hook callback within `turnTimeoutSeconds` ->
  wrapper marks the turn failed and (if `callbackUrl` set) posts the
  failure; pollers see `state=failed`.
- **webhook delivery fails**: retry with exponential backoff
  (`webhookRetries` tries, default 3), then drop and increment
  `ccw_webhook_delivery_total{result="dropped"}`. The result is still
  retrievable by poll.

## Dependencies and preconditions

- New Keycloak client/audience `tatara-claude-code-wrapper` (terraform in
  `~/Documents/infra/terraform/keycloak`, mirrors the tatara-memory client).
  External precondition, not built in this repo.
- tatara-cli binary available at a pinned release (already shipped, phase 2).
- CI via existing tatara-argo-workflows: `go-ci` + `container-build` +
  `helm-publish`. Deploy via the tatara-helmfile bump loop once that lands
  (consolidation phase P14); for v0.1.0 the helm release lives in
  `~/Documents/infra/helmfile` like the others.

## Versioning

Starts at **v0.1.0**.

## Out of scope (v0.1.0)

- Multi-session per pod / session registry.
- Ingress / external routing.
- `claude --resume`-from-transcript after crash.
- Turn queueing (409-on-busy instead).
- Bedrock / Vertex auth.
- Live streaming / SSE of in-progress turns.
- Any orchestrator/controller that spawns and routes pods.

## Success criteria

- A pod boots, renders global + project CLAUDE.md, merges MCP overlays,
  installs bundled + custom skills, eager-boots claude.
- `POST /v1/messages` with a `callbackUrl` returns 202, claude runs the
  turn, the Stop hook posts the result, the wrapper delivers it to the
  callback.
- `POST /v1/messages` without a `callbackUrl` returns 202; the result is
  retrievable via `GET /v1/messages/{turnId}` once complete.
- A second `POST /v1/messages` while busy returns 409.
- Optional repo clone works; claude can edit files in `/workspace`.
- tatara-cli memory MCP tools are usable from inside the session.
- OIDC rejection on a missing/invalid/wrong-audience token.
- `/metrics` exposes the counters/histogram/gauges above.
- Bumping `CLAUDE_CODE_VERSION` rebuilds only the claude layer.
