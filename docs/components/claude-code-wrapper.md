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
   - `/workspace/.mcp.json` (tatara-cli + any overlays), then any `TATARA_EXTRA_MCP_SERVERS` entries merged in - see [Configuration](#configuration)
   - `~/.claude/settings.json` (Stop hook path, `bypassPermissions`, MCP auto-enable, denied interactive pickers)
   - `~/.claude.json` (seeds onboarding flags so no interactive dialogs appear)
   - `~/.claude/CLAUDE.md` and `/workspace/CLAUDE.md`
   - Skills: cloned from the configured skills repo into a staging dir and
     promoted by rename on success (`SKILLS_SRC_DIRS`), plus any custom
     `TATARA_EXTRA_SKILL_SOURCES`. Nothing is baked into the image - the
     20 skills once shipped in `/templates/skills` were dropped from the
     image on 2026-06-28, and the clone is the sole source. **The boot now
     fails** if that install produces zero skills while at least one source
     was configured ([tatara-claude-code-wrapper#180](https://github.com/szymonrychu/tatara-claude-code-wrapper/pull/180)),
     rather than the previous fail-open that could produce an agent pod with
     no skills and no typed subagents. `installAgents` (the `.claude/agents`
     palette) is unaffected and stays fail-open.
3. **Spawn `claude`** under a PTY. Start the ring-buffer reader and process-wait goroutine.
4. **`bootWait`**: The "Bypass Permissions mode" warning is not seedable and appears on every boot. The wrapper detects it in the ring buffer (ANSI/whitespace-stripped matching) and accepts it (Down + Enter). Then waits for output quiescence (no new PTY bytes for >1.5s, floored at ~4s) before marking the session ready.
5. **Start HTTP servers.** `/readyz` is not served until `Start` returns.

## Baked-in tooling: Node 24 and a pinned Renovate

The image ships Node 24 (bumped from a floating Node 22, 2026-08-13) and a pinned
`renovate` binary (`ARG RENOVATE_VERSION`, installed with `npm install -g --engine-strict`),
for the [`upgrade`](../workflows/upgrade.md) agent kind's read-only candidate-discovery step.
The pin exists because Renovate declares a hard `engines.node` range: under an unsatisfied range
Renovate does not fail loudly, it logs a warning and returns an empty `packageFiles: {}` report
with exit 0 - indistinguishable from "nothing to upgrade" to a caller that does not check the
log. `--engine-strict` turns that silent-wrong-answer mode into a build-time failure instead,
so a future Node bump that breaks the Node/Renovate pairing fails the image build, not an
upgrade agent's every run. Node 24 applies to **every** agent pod on this image, not only
`upgrade` ones.

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
| `POST` | `/v1/interrupt` | Writes a single ESC byte to the PTY under `ptyMu` -> `202`; `503` if there is no live PTY. **Never `409`** - this is the one endpoint meant to act on a turn that is already in flight. See [Interrupting a turn](#interrupting-a-turn) below. |
| `GET` | `/v1/session` | The six existing fields (`state`, `turnsCompleted`, `model`, `repo`, ...) **plus `contractVersion`** <!-- stale-ok: turnsCompleted --> |
| `GET` | `/v1/transcript` | Full JSONL session transcript (debug) |
| `DELETE` | `/v1/session` | Graceful shutdown, pod exits |
| `GET` | `/healthz` `/readyz` `/metrics` | Operator endpoints (not exposed via ingress) |

!!! danger "`POST /v1/interject` is deleted, not deprecated"
    The endpoint raced the Stop hook against the ring-buffer tailer: injecting text mid-turn had no ordering guarantee against the hook reading the transcript at turn-end, so a well-timed interject could be silently lost or attributed to the wrong turn. It was live in production until this release (the operator drove it from a `PendingInterjections` drain loop). It is removed in the same change that removes the operator's drain loop, operator first, so the wrapper never 404s a call the operator is still making. `Session.Interject` and `ErrNotBusy` are deleted with it. <!-- stale-ok: /v1/interject, PendingInterjections -->

## Interrupting a turn

`POST /v1/interrupt` ([tatara-claude-code-wrapper#158](https://github.com/szymonrychu/tatara-claude-code-wrapper/pull/158)) is not a revival of `/v1/interject` - it does not inject content into a turn, it ends one. It writes a single ESC byte to the PTY, which the CLI treats as a genuine interrupt: it lands synchronously (~40ms) even mid-tool-call, and unlike a paste (which the CLI only consumes at the next tool-call boundary) it reaches exactly the case worth interrupting - a turn wedged inside one long tool call. The session, its transcript, and the full conversation context all survive; the next turn runs normally in the same session.

An interrupted turn produces no `end_turn` message and no Stop hook fires, so nothing would otherwise clear `mgr.current` - the session would sit `busy` forever and every future `POST /v1/messages` would `409` permanently. `transcript.WasInterrupted` detects the two literal markers the CLI writes (`[Request interrupted by user for tool use]` mid-tool-call, `[Request interrupted by user]` mid-stream) as the **last** entry in the conversation - a marker earlier in history must not resolve a *later* live turn - and `Manager.completeAfterInterrupt` resolves it as `State=failed`, `StopReason=interrupted`, keeping whatever partial output exists. Resolution polls the transcript (200ms, bounded at 30s) rather than depending on the log tailer, since `CCW_LOG_TRANSCRIPT=false` means there is no tailer at all. Past that deadline the turn is left alone and counted as `ccw_interrupts_total{result="unconfirmed"}` - the operator can still see it in `Snapshot`.

This is the wrapper side of the operator's stall escalation: probe, wait, probe again, and only past `stallProbeMaxAttempts` does the operator call this endpoint and run the ordinary stop-and-handoff sequence. See [Agent Execution](../architecture/agent-execution.md#stall-detection-probe-interrupt-stop).

## The wrapper no longer kills a turn on inactivity

Before [tatara-claude-code-wrapper#158](https://github.com/szymonrychu/tatara-claude-code-wrapper/pull/158), `TURN_TIMEOUT_SECONDS` of silence failed the turn outright. It now calls `markStallSuspected` instead: stamps `stallSuspectedSince` (once per stall, left alone across re-arms so it measures how long the silence has lasted), increments `ccw_turn_stall_suspected_total`, logs a warning, and **re-arms** - it never kills the turn itself. The wrapper's rationale: its only signal was the transcript going quiet, and a single subagent run can silence it for 35+ minutes while the pod works flat out, so the timer's most common firing was never actually a hang. Cleared by genuine transcript activity or by the turn completing; a probe-originated exchange does **not** clear it, so the operator's own escalation cannot erase its own evidence.

**After this change the only wrapper-side bound on a turn already in flight is the pod's TTL** (`admit()`/PodTTL refuses new work past the deadline; it never touches a turn already running). Killing a stalled turn is now entirely the operator's call, made through the probe/interrupt escalation above.

## Contract-version handshake

The wrapper image and the operator image ship in different helm releases and can apply concurrently, so a window where a new operator pairs with an old agent image is reachable. To fail that fast instead of burning a full turn budget against a 404ing tool surface:

1. `contractVersion` is a compile-time constant in the wrapper binary (`const ContractVersion = 4`), bumped in the same release that ships a new tool surface - most recently for the #521 lifecycle redesign's `clarify` fold and `submit_outcome` gate actions. It is reported on every `GET /v1/session` response.
2. The operator injects `TATARA_CONTRACT_VERSION=4` into every agent pod's env (read by tatara-cli, not by the wrapper itself).
3. Before submitting a pod's turn-0, the operator reads `GET /v1/session` and compares the reported `contractVersion` against its own expectation. On a mismatch, or a response with no `contractVersion` field at all (an old wrapper), the operator fails the Task instantly with `stageReason=agent-contract-mismatch` and never submits a turn - zero tokens burned.

See [tatara-cli](cli.md#contract-version-handshake) for the third defense: the cli's MCP server refusing to start on a mismatched `TATARA_CONTRACT_VERSION`.

## Continuity across pods: no session resume

There is no `--resume`, no session replay, and no transcript persisted to S3. `CONVERSATION_SESSION_ID`, `CONVERSATION_OBJECT_KEY`, and `HANDOFF_KEY` are all deleted, and the wrapper makes no fork/replay decision at all. <!-- stale-ok: CONVERSATION_SESSION_ID, CONVERSATION_OBJECT_KEY, HANDOFF_KEY, --resume -->

Every pod's turn-0 context bundle is rendered fresh by the operator, identically every time. Continuity between one pod and the next pod of the same Task comes entirely from `Task.status.notes` - the append-only journal every pod reads at turn 0 - not from replaying a transcript. When a pod is stopped (TTL, crash, or graceful shutdown), the thing that must survive is a note in that journal, not a resumable session; see [Pod TTL: the stop sequence](#pod-ttl-the-stop-sequence) below for how the operator guarantees one is always written.

What *does* need to survive between pods is the git state on disk, and two changes since the redesign harden that path:

- **A merge conflict on resume no longer fails the boot.** Bootstrap resumes an existing remote task branch and merges the repo's base branch into it ([tatara-claude-code-wrapper#159](https://github.com/szymonrychu/tatara-claude-code-wrapper/pull/159)). A conflict used to abort the whole bootstrap - `Render` failed, the pod never became Ready, and the operator respawned it every ~5 minutes toward the residency cap, for zero turns. Now the conflict arm returns a nil error (`merge --abort` still runs, leaving a clean tree - never conflict markers published to the branch), and the finding is injected into the agent's global CLAUDE.md as the hard-requirement first job to resolve. The operator side of this carries a matching, independently-injected turn-0 prompt rule for `implement`/`documentation` Tasks resuming a branch ([tatara-operator#586](https://github.com/szymonrychu/tatara-operator/pull/586)) - belt-and-suspenders with the wrapper's own CLAUDE.md block above.
- **Resuming a workspace repairs it instead of trusting it** ([tatara-claude-code-wrapper#161](https://github.com/szymonrychu/tatara-claude-code-wrapper/pull/161), prerequisite for the [persistent workspace PVC](../reference/project.md#workspacespec)). `PrepareWorkspace` runs before the clone-skip check: a `.git` that fails validity probes is re-cloned; stale lock files (`index.lock`, `HEAD.lock`, etc.) are swept unconditionally; an in-progress merge/rebase/cherry-pick/revert/bisect is aborted; uncommitted tracked changes are **committed** (never stashed or discarded - a stash is invisible to the turn finalizer's own `git add -A`); and local-vs-remote branch divergence is reconciled by **merge only**, never `-B`/force-reset, so local commits (including ones never pushed) are never destroyed. The precedence, always: uncommitted local work > local commits > remote task-branch commits > base branch.

The task branch is also now pushed **mid-turn**, not only at turn end: a `BRANCH_PUSH_INTERVAL_SECONDS`-interval (default 120s) push-only safety net ([tatara-claude-code-wrapper#160](https://github.com/szymonrychu/tatara-claude-code-wrapper/pull/160)) runs alongside the metrics pusher. Push-only, deliberately - no `git add`, no `git commit` - so it can never race the agent's own git calls or publish a half-edited tree; nothing-to-push and a non-fast-forward rejection are both treated as success. This closed a real gap: a pod OOMKilled mid-turn had committed work that never reached origin, because `/workspace` is the container's writable layer and does not survive the pod. The agent's global CLAUDE.md now states plainly that the branch is pushed every couple of minutes and a commit is public the moment it is made - "commit then amend" is not a supported workflow, since force-push is on the deny list.

The end-of-turn `CommitAndPushAll` no longer aborts on the first repo it fails to push ([tatara-claude-code-wrapper#169](https://github.com/szymonrychu/tatara-claude-code-wrapper/pull/169)): it attempts every repo the turn touched and reports two lists, `pushed` and `failed`, instead of stopping the loop at the first error and silently never attempting the rest. `failed` rides the turn-complete callback into `status.lastTurnFailedRepos` ([tatara-operator#606](https://github.com/szymonrychu/tatara-operator/pull/606)) alongside the existing `lastTurnPushedRepos`.

## Pod TTL: the stop sequence

`AGENT_POD_TTL_SECONDS` (from `Project.spec.agentPodTTLSeconds`, default 3600) bounds one pod's life, not the Task - the Task persists across as many pods as it needs. The wrapper computes `t0 = pod start + AGENT_POD_TTL_SECONDS`; the operator (the wrapper's only client) drives the rest of the sequence around that clock:

1. **The wrapper stops admitting normal turns past `t0`.** Any `POST /v1/messages` with `handoff` unset or `false` after `t0` gets `410 Gone`. It still accepts exactly one turn with `handoff: true` - without that carve-out, the handoff turn in step 3 would be refused by this same rule, and `Task.status.notes` would end up empty on every TTL stop.
2. **The operator waits for any in-flight turn's callback**, bounded by `TURN_TIMEOUT_SECONDS`. A pod is mid-turn at TTL expiry essentially always, and `POST /v1/messages` already `409`s while a turn is in flight, so the handoff turn cannot simply be submitted immediately.
3. **The operator submits exactly one `handoff: true` turn**, asking the agent to call `task_note(kind=handoff)` with everything the next pod needs, bounded by `TURN_TIMEOUT_SECONDS`.
4. **Hard cap at `t0 + 2*TURN_TIMEOUT_SECONDS + 60s`.** On that cap, or on any `410`/`409`/5xx from step 3, the operator writes a synthetic handoff note in-process from the last-turn continuation state on the Task (`status.lastTurnFinalText`, `status.lastTurnPushedRepos` and `status.lastTurnFailedRepos`), then stops the pod - force-deleting it only if the graceful stop fails against a pod that is still there.

`Task.status.notes` is never empty after a TTL stop: either the agent wrote a handoff note, or the operator wrote a synthetic one. When there is no last-turn continuation state to synthesize from either, the note that lands is an explicit placeholder and the stop is counted as `handoff="none"` - see the [runbook](../operations/runbooks.md#tatara-runbook-operator-agent-pod-ttl-stopped-with-no-handoff-captured).

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
| `TURN_TIMEOUT_SECONDS` | `1800` | Per-turn inactivity window; also bounds each step of the [pod TTL stop sequence](#pod-ttl-the-stop-sequence). **No longer kills the turn** - see [The wrapper no longer kills a turn on inactivity](#the-wrapper-no-longer-kills-a-turn-on-inactivity) |
| `BOOT_TIMEOUT_SECONDS` | `60` | Max wait for boot quiescence |
| `AGENT_POD_TTL_SECONDS` | `3600` (from `Project.spec.agentPodTTLSeconds`) | Bounds this pod's life; the wrapper computes `t0` from it and enforces the [stop sequence](#pod-ttl-the-stop-sequence) |
| `BRANCH_PUSH_INTERVAL_SECONDS` | `120` | Periodic **push-only** safety net for the task branch, independent of the end-of-turn commit+push. `0` disables. See [Continuity across pods](#continuity-across-pods-no-session-resume) |
| `CLAUDE_CODE_OAUTH_TOKEN` | - | Claude subscription OAuth token. This is what the operator actually injects (from the `anthropicSecretName` Secret, key `oauth-token`); Claude Code reads it directly. |
| `ANTHROPIC_API_KEY` | - | Alternative metered Anthropic API key. Supported (used to pre-seed the trust dialog) but **not** what the deployed platform injects. |

File/list config is mounted under `/etc/wrapper` (chart values: `globalClaudeMd`, `projectClaudeMd`, `baseMcp`, `extraMcpServers`, `allowedTools`, custom skills).

`TATARA_EXTRA_MCP_SERVERS` (from `Project.spec.agent.mcpServers`, see [`MCPServerSpec`](../reference/project.md#mcpserverspec)) is a compact-JSON array of `{name, url, type}`, absent entirely when the project sets no extra servers. The wrapper parses it and merges each entry into the rendered `.mcp.json` after the overlay-dir fragments and before the platform-owned servers (`tatara`, `grafana`, `serena`), so a project-supplied entry can never shadow one of those. An entry using a reserved name is skipped with a warning; malformed JSON or an incomplete entry fails open (warn and continue) rather than failing pod boot - a bad `Project` value can never block agent startup. This is distinct from the chart-level `extraMcpServers` value above, which is static and baked in at deploy time; `TATARA_EXTRA_MCP_SERVERS` is per-Project and set by the operator at pod-build time.

## Metrics

| Metric | Type | Description |
|---|---|---|
| `ccw_turns_total` | counter | Turn completions by result |
| `ccw_turn_tokens_total` | counter | Claude tokens per turn by type/model/kind/repo/project |
| `ccw_turn_cost_usd_total` | counter | Cumulative turn cost in USD by kind/repo/project |
| `ccw_turn_in_flight` | gauge | Turns currently in flight (0 or 1) |
| `ccw_bootstrap_duration_seconds` | histogram | Full bootstrap (`Render()`) duration |
| `ccw_tool_calls_total` | counter | Agent tool calls observed in the transcript by tool and outcome |
| `ccw_interrupts_total{result}` | counter | `POST /v1/interrupt` outcomes, including `unconfirmed` when resolution polling hits its 30s deadline |
| `ccw_turn_stall_suspected_total` | counter | Inactivity-timer firings since it stopped killing turns - see [The wrapper no longer kills a turn on inactivity](#the-wrapper-no-longer-kills-a-turn-on-inactivity) |
| `ccw_safety_push_total{result}` | counter | Periodic `BRANCH_PUSH_INTERVAL_SECONDS` push-only safety-net attempts |
| `ccw_bootstrap_reconcile_total{result}` | counter | Resume-time base-branch reconcile outcomes: `merged`, `up_to_date`, `conflict`, `base_unresolved`, `fetch_fail` |
| `ccw_skills_installed_total{profile}` | counter | Skills installed at boot. Renamed from `wrapper_skills_installed_total` ([#180](https://github.com/szymonrychu/tatara-claude-code-wrapper/pull/180)) - the old `wrapper_`/`agent_` prefix was outside the pod's own push allowlist, so this and the two metrics below never reached Prometheus at all under their old names |
| `ccw_skills_clone_failures_total{source}` | counter | Skills clone failures, `source=skills_repo` or `extra` (a bad `TATARA_EXTRA_SKILL_SOURCES` entry is not a fleet-wide outage) |
| `ccw_agents_installed_total` | counter | Agents (`.claude/agents`) installed at boot |
