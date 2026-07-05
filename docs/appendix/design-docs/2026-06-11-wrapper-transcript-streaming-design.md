# Wrapper Transcript Streaming - Design

**Date:** 2026-06-11
**Status:** Design (approved, pre-plan)
**Repo:** tatara-claude-code-wrapper

## 1. Goal

Emit every claude-code message and action (everything in/out: user prompts,
assistant text, tool_use, tool_result, thinking) to the wrapper pod's stdout in
real time, as structured JSON logs, so an operator watching `kubectl logs` sees
exactly what the agent is doing and can step in on hidden issues.

## 2. Approach

claude-code already writes a per-session JSONL transcript where each line is a
complete message. The wrapper already learns its path (`transcriptPath`, from the
Stop-hook payload). A new tailer goroutine follows that transcript and emits one
structured slog event per content block in real time. This captures the full
in/out stream without changing the interactive-PTY invocation.

Rejected alternatives: claude-code hooks (fire only on tool events; miss assistant
reasoning/text), raw PTY capture (terminal escape-code soup, not message-structured).

## 3. Component: `internal/transcript`

New package. Files: `tailer.go`, `redact.go`, `tailer_test.go`, `redact_test.go`.

### 3.1 Tailer

```go
type Tailer struct {
    log      *slog.Logger
    redactor *Redactor
    turnID   func() string // Manager's active turn id, best-effort ("" if none)
    enabled  bool
}

// Follow reads the transcript at path from the start (no events lost), then
// follows appends until ctx is cancelled. Re-locates on truncation/replacement
// (claude restart -> new session -> new transcript).
func (t *Tailer) Follow(ctx context.Context, path string) error
```

- Opens the file, reads complete lines (buffer partial trailing line until newline arrives), parses each as a transcript entry, emits events. After EOF, polls for growth (e.g. 200ms) until ctx done. Handles the file not existing yet (waits for it) and inode change (claude restart) by re-opening.
- One slog event per `message.content[]` block (see 3.3). Non-message lines (system/summary) emit a single passthrough event with `type` set to the line's own `type`.

### 3.2 Transcript entry shape (from claude-code)

Each line (see `testdata/transcript_assistant_line.jsonl`):
```
{ "type":"assistant"|"user"|..., "uuid","sessionId","timestamp",
  "message": { "role", "content":[ {type:text|tool_use|tool_result|thinking, ...} ],
               "stop_reason","usage" } }
```
Parse leniently: unknown fields ignored; a line that fails to parse emits a
`type:"raw"` event carrying the (redacted) raw line + the parse error, never drops.

### 3.3 Emitted events (structured slog, INFO, `action:"agent_stream"`)

Common fields: `action="agent_stream"`, `session_id`, `transcript_uuid`,
`ts` (claude timestamp), `turn_id` (best-effort), `stream_type`.

| content block | stream_type | extra fields |
|---|---|---|
| text | `text` | `role`, `text` (full) |
| thinking | `thinking` | `text` (full) |
| tool_use | `tool_use` | `tool`, `tool_use_id`, `input` (full JSON) |
| tool_result | `tool_result` | `tool_use_id`, `is_error`, `content` (full) |
| envelope end | `message_end` | `role`, `stop_reason`, `usage` (token counts) |

All string payloads are passed through the redactor first. **Uncapped** - full
content, no truncation (per decision).

### 3.4 Redactor (`redact.go`)

```go
type Redactor struct { secrets []string } // distinct non-empty secret values
func NewRedactor(values map[string]string) *Redactor // name -> value
func (r *Redactor) Scrub(s string) string            // replace each value with [REDACTED:<name>]
```

- Built at wrapper startup from the secret-bearing env: the SCM token and any env
  whose key matches `*_TOKEN`, `*_SECRET`, `*_KEY`, `*_PASSWORD` (case-insensitive),
  plus explicit known keys (the OIDC/OpenAI secrets). Values shorter than 8 chars
  are skipped (avoid scrubbing trivial strings). Longest-first replacement so a
  secret that contains another is handled.
- Applied to every emitted text/input/content/raw field.

## 4. Session wiring (`internal/session/session.go`)

- `Manager` gains a `tailer *transcript.Tailer` and starts `Follow` in a goroutine
  from `Start` once it can supply a path. Real-time fidelity:
  - The Manager already records `transcriptPath` when the first hook arrives
    (session.go:281). The tailer is handed the path then and reads from file
    start, so turn-1 entries are emitted (batched at first-hook time); turn-2+ are
    real-time. This satisfies the goal; a projects-dir watcher for turn-1 real-time
    is a noted optional follow-up, out of scope here.
- The goroutine is cancelled on session shutdown (existing context/stop path).
- `turnID` accessor returns the Manager's current in-flight turn id (or "").

## 5. Config

- `CCW_LOG_TRANSCRIPT` (env, default `true`). When false, the tailer is not started.
- Redactor secret values sourced from the process env at startup (no new config).
- No operator/CRD change required for the default-on behavior; the operator may
  later surface a per-Project toggle, out of scope here.

## 6. Observability

The stream events ARE the observability. They log at INFO with `action:"agent_stream"`
so they are filterable (`kubectl logs ... | grep agent_stream`) and distinct from
the wrapper's own lifecycle logs. A counter `ccw_stream_events_total{stream_type}`
is incremented per emitted event (metrics package).

## 7. Testing (TDD)

- `tailer_test.go`: feed `testdata/transcript_assistant_line.jsonl` -> assert one
  `text` event with the full text; craft lines with tool_use/tool_result/thinking
  -> assert the mapped events + fields; a malformed line -> assert a `raw` event
  (not dropped); append-after-open -> assert the new line is emitted (real-time
  follow); re-open on inode change -> assert continued emission.
- `redact_test.go`: a planted secret value in text/input is replaced with
  `[REDACTED:<name>]`; short values are not scrubbed; longest-first ordering.
- session test: tailer started/stopped with the session; disabled when
  `CCW_LOG_TRANSCRIPT=false`.

## 8. Deploy

Wrapper chart + image bump (0.1.12 -> 0.1.13). Update the `tatara` Project CR
`spec.agent.image` to the new wrapper tag (declarative manifest re-apply, like the
activation). No operator change.

## 9. Out of scope

- Turn-1 real-time via projects-dir watcher (noted follow-up).
- Shipping the stream to an external aggregator / separate sink (stdout only).
- Per-Project enable toggle in the CRD (env default-on suffices now).
