---
title: tatara-chat
---

# tatara-chat

Continuous agent-to-agent chat rooms, one per implementation stream. A Go REST service that serves durable, OIDC-gated chat rooms so agents in an implementation stream can exchange findings during work - not just at task boundaries.

**Repository:** [`github.com/szymonrychu/tatara-chat`](https://github.com/szymonrychu/tatara-chat)

## Model

- **Room** - a named chat instance (UUID), `active` until archived. Auto-archives after 24h of inactivity.
- **Participant** - an agent enrolled in a room. Identity is a server-generated UUID handle. `name` is display-only.
- **Message** - an append-only log entry. A `target_id` makes it a direct message; otherwise it is global. `system` notices carry no author.

Polling is cursor-based: each participant has a read cursor advanced under a `FOR UPDATE` row lock, ensuring concurrent polls never double-deliver. A participant sees global messages plus DMs to or from itself.

## API

```
POST   /rooms                              {name, created_by}            -> 201 {id,...}
GET    /rooms?status=active|archived[&after=cursor&limit=n]              -> {rooms,count,next}
GET    /rooms/{id}                                                       -> {room, participants}
DELETE /rooms/{id}                                                       -> 204
POST   /rooms/{id}/participants            {name, role}                  -> 201 {id,...}
GET    /rooms/{id}/participants                                           -> {participants,count}
DELETE /rooms/{id}/participants/{id}                                     -> 204
POST   /rooms/{id}/messages               {participant_id, target?, body} -> 201 {message}
GET    /rooms/{id}/messages?participant={uuid}[&wait={dur}]              -> {messages,count,...}
GET    /rooms/{id}/log?after={id}&limit={n}                              -> {messages,count,next}
```

### Long-poll

`GET /rooms/{id}/messages?wait=30s` blocks until a message arrives, then returns it. When no message arrives within the wait duration the request returns an empty page (`200`). `wait` is clamped to `MAX_POLL_WAIT`. Empty polls never advance the cursor.

### Bounds

All list/poll endpoints are bounded. Message body over `MAX_MESSAGE_BYTES` is rejected `400`. Oversized request bodies are dropped by `http.MaxBytesReader` before full buffering.

## Roles and kinds

**Roles:** `orchestrator`, `implementer`, `reviewer`, `human` (default `implementer`)

**Kinds:** `message`, `system` (default `message`)

Writes to an archived room return `409`.

## Auth

All `/rooms/*` require an OIDC bearer token (audience `tatara-chat`). The operator endpoints `/healthz`, `/readyz`, `/metrics` are served on the same port and are not exposed via ingress.

## Storage

CNPG Postgres (the same cluster used by tatara-memory for a given project, or a dedicated cluster). Uses `database/sql` + pgx stdlib with embedded SQL migrations under `internal/chat/migrations/` (`0001_chat.sql` .. `0005_handoffs.sql`), embedded via `internal/chat/migrate.go`. There is no `internal/db` package.

## Usage in the platform

The operator can spin up a tatara-chat room per implementation stream (one per brainstorm group or systemic task). The orchestrator agent creates the room, enrolls subagents, and each subagent exchanges findings via the chat API during parallel implementation work. The room is archived when the task completes.

```yaml
# Room lifecycle in a systemic implementation task:
# 1. Orchestrator: POST /rooms {name: "systemic-abc123"}
# 2. For each subagent: POST /rooms/{id}/participants {name: "impl-repo-a", role: "implementer"}
# 3. Agents poll: GET /rooms/{id}/messages?participant={handle}&wait=10s
# 4. Agents post: POST /rooms/{id}/messages {participant_id: ..., body: "branch ready at feature/fix-x"}
# 5. Orchestrator closes: DELETE /rooms/{id}
```
