# tatara-chat (phase 8) - design

Date: 2026-06-05
Status: approved (brainstorm), pending implementation plan

## Purpose

A REST service serving continuous, per-stream chat rooms so agents in an
implementation stream exchange findings and remarks *during* work, not just
at phase or task boundaries. The orchestrator (main agent) spawns a room,
adds and removes subagents as participants, hands each subagent a
`(room_id, participant_id)` handle, and closes the room when the stream
finishes (or it auto-closes after ~24h).

The backend model is lifted from the proven `spellslinger-reveries` chat
subsystem (named rooms, participants with per-participant read cursors, an
append-only message log, cursor-based polling, manual archive) and adapted
to tatara conventions and a server-generated participant-UUID identity.

## Placement

- New repo `github.com/szymonrychu/tatara-chat`, cloned as a gitignored
  subdir of `~/Documents/tatara/` like every other component.
- Own Go service, own helm chart, own small CloudNativePG cluster.
- MCP tools live in `tatara-cli` (consistent with how the tatara-memory
  tools are served). Phase 8 therefore has two workstreams:
  1. the `tatara-chat` service + chart (this repo);
  2. a `chat` MCP tool group added to `tatara-cli`.

## Service architecture

Go, newest stable (the 1.25.x line, matching tatara-memory). `go.mod` pins
the exact minor. Layout mirrors tatara-memory:

```
cmd/tatara-chat/{main,app,config,serve,health}.go
internal/auth/{auth,verifier,middleware}.go   # OIDC, copied pattern
internal/auth/testjwks/testjwks.go
internal/chat/chat.go                          # types + visibility filter
internal/chat/store.go                         # pgxpool store
internal/chat/handler.go                       # chi handler
internal/chat/migrate.go                       # embedded-SQL Migrate()
internal/chat/migrations/0001_chat.sql
internal/obs/{obs,metrics,tracing}.go
internal/version/version.go
charts/tatara-chat/...                          # helm create, then edited
Dockerfile  Makefile  go.mod
```

Migrations follow the tatara pattern: an embedded `.sql` file applied by a
small `Migrate()` on boot (`pool.Exec(ctx, schemaSQL)`), not goose.

## Data model

Three tables in a dedicated `tatara_chat` database on the component's own
cnpg cluster.

```sql
CREATE TABLE chat_rooms (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'archived')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE chat_participants (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id      UUID NOT NULL REFERENCES chat_rooms(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'implementer'
                 CHECK (role IN ('orchestrator', 'implementer', 'reviewer', 'human')),
    joined_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    left_at      TIMESTAMPTZ,
    last_seen_id BIGINT NOT NULL DEFAULT 0
);
CREATE INDEX idx_chat_participants_room ON chat_participants (room_id);

CREATE TABLE chat_messages (
    id           BIGSERIAL PRIMARY KEY,
    room_id      UUID NOT NULL REFERENCES chat_rooms(id) ON DELETE CASCADE,
    author_id    UUID REFERENCES chat_participants(id) ON DELETE SET NULL,
    author_name  TEXT NOT NULL DEFAULT 'system',
    target_id    UUID REFERENCES chat_participants(id) ON DELETE SET NULL,
    kind         TEXT NOT NULL DEFAULT 'message'
                 CHECK (kind IN ('message', 'system')),
    body         TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (kind = 'system' OR author_id IS NOT NULL)
);
CREATE INDEX idx_chat_messages_room ON chat_messages (room_id, id);
```

Key difference from the reference: **participant identity is a
server-generated UUID** (`chat_participants.id`), not a caller-chosen name.
`name` is a human display label only, denormalized into
`chat_messages.author_name` for readable logs. A direct message targets a
participant UUID via `target_id`.

`author_id` is nullable so sweeper-generated lifecycle notices need no
synthetic participant: a `system` message is written with `author_id = NULL`
and `author_name = 'system'`. The `CHECK (kind = 'system' OR author_id IS
NOT NULL)` guarantees every `message`-kind row still has a real author.
System notices are global (`target_id IS NULL`) so every participant sees
them on the next poll. The visibility filter's `author_id = me` clause is
unaffected by NULL authors.

## REST API

```
POST   /rooms                          {name, created_by}
                                       -> 201 {id, name, created_by, status, created_at}
GET    /rooms?status=active|archived   -> {rooms, count}
GET    /rooms/{id}                      -> {room, participants}
DELETE /rooms/{id}                      (close/archive) -> 204
POST   /rooms/{id}/participants         {name, role}
                                       -> 201 {id, room_id, name, role, joined_at, last_seen_id}
GET    /rooms/{id}/participants         -> {participants, count}
DELETE /rooms/{id}/participants/{participantId}  (marks left_at) -> 204
POST   /rooms/{id}/messages             {participant_id, target?, kind?, body}
                                       -> 201 {message}
GET    /rooms/{id}/messages?participant={uuid}   (poll, advances cursor)
                                       -> {messages, count, room_status}
GET    /rooms/{id}/log                  (full history, no cursor advance)
                                       -> {messages, count}

/healthz  /readyz  /metrics             (operator only, NOT exposed via ingress)
```

The subagent's handle is `(room_id, participant_id)`. `send` and `poll`
identify the in-room speaker by `participant_id`; the OIDC bearer token
authorizes service access. `participant_id` must belong to the room and not
be marked left, else `404`.

## Polling and visibility

Cursor-based, identical in spirit to the reference `Poll`:

- A `FOR UPDATE` row lock on the participant row serializes concurrent
  polls by the same participant, preventing cursor overwrites and duplicate
  delivery.
- The query returns messages with `id > last_seen_id`, advances the cursor
  to the max fetched id (advancing past globally-fetched messages the
  participant filtered out so they are not re-scanned), and returns the room
  status.
- A new participant's cursor starts at the room's current max message id, so
  it sees only messages from join onward.
- Visible to participant P = global messages (`target_id IS NULL`) OR
  `target_id = P` OR `author_id = P`.

`GET /log` returns every message in id order without advancing any cursor,
for review.

## Lifecycle and TTL

A room closes two ways:

1. Explicit `DELETE /rooms/{id}` by the orchestrator (sets `status =
   'archived'`).
2. A background sweeper archives any `active` room with
   `created_at < now() - roomTtlHours` (default **24h**, configurable). The
   sweeper ticks on a configurable interval (default 5m), and on archiving a
   room emits a `system` message ("room auto-archived after TTL") so polling
   agents observe the change via `room_status: archived` on their next poll.

Writes to an archived room (`POST /messages`) return `409 Conflict`. TTL is
measured from `created_at` (room open time), per the stated requirement.

## Auth and cross-repo dependencies

- **Keycloak** (infra terraform `tatara_clients.tf`): add a confidential
  `tatara-chat` client to act as the token audience, and add `tatara-chat`
  to the `tatara` scope's audience mapper so existing tatara-cli and
  service-account tokens already carry the right `aud`. Same onboarding
  shape as tatara-memory. The service verifies JWTs via JWKS with
  `iss = https://auth.szymonrichert.pl/realms/master` and `aud` containing
  `tatara-chat`. No per-user RBAC in v1.
- **infra/helmfile**: a new release in the existing `helmfiles/tatara/`
  bucket - per-release `values/tatara-chat/{common,default}.yaml` plus a
  sops `default.secrets.yaml` (PG password, OIDC secret if needed),
  `regcred` from bucket common, and the ingress host/path
  (e.g. `https://tatara.szymonrichert.pl/api/v1/chat`, nginx prefix-strip)
  - all cluster-specific, living in the helmfile per hard rule 14, never in
  the chart.
- **tatara-cli**: a new `chat` MCP tool group, a REST client for
  tatara-chat, and a `TATARA_CHAT_BASE_URL` configuration entry.

## Chart and deploy

- `helm create tatara-chat`, then edited (hard rule 5).
- One CloudNativePG `cluster` subchart (alias `postgres`,
  `condition: postgres.enabled`, version pinned to match tatara-memory's
  `0.6.x` line). No neo4j, no lightrag.
- Cluster-agnostic per hard rules 6 and 14: only camelCase scalars in
  `values.yaml`, mapped through ConfigMap/Secret kebab-case keys consumed via
  `envFrom`; `imagePullSecrets`, `storageClass`, ingress host/class all empty
  or omitted in chart defaults, supplied by the infra helmfile.
- Build and deploy from `main` only, via the infra helmfile (hard rule 10).
  No argo (retired for tatara); CI replacement is the same open platform
  question as for tatara-memory/tatara-cli.

## Observability

- slog JSON, identical logger structure to the other services (hard rule
  11).
- INFO per business action (hard rule 12): room created/archived,
  participant added/removed, message sent, poll - with structured fields
  (request_id, room_id, participant_id, action, count, duration_ms where
  relevant). WARN/ERROR as appropriate.
- Prometheus `/metrics` (hard rule 13): counters
  `chat_rooms_total{op}` (created|archived|ttl_archived),
  `chat_participants_total{op}` (join|leave),
  `chat_messages_total{kind,scope}` (scope = global|dm),
  `chat_polls_total`; a gauge for active rooms; a request-duration
  histogram.

## Testing (TDD)

Mirrors tatara-memory's test layout:

- Store tests against a real Postgres: cursor advances correctly; two
  concurrent polls by the same participant deliver no duplicates; visibility
  filter (global vs DM vs own); join cursor starts at current max; write to
  archived room rejected; TTL sweep archives only rooms older than the
  cutoff and emits the system notice.
- Handler tests with a fake store: status codes, validation (missing
  name/body, invalid role/kind/status filter, unknown room/participant),
  JSON shapes.
- Auth middleware test using `testjwks` (valid/expired/wrong-audience).

## Out of scope (YAGNI)

- No websockets, SSE, or long-poll. Poll-only fits MCP's request/response
  model and the agent loop.
- No read receipts beyond the per-participant cursor.
- No RBAC beyond OIDC audience verification.
- No message editing or deletion.
- No cross-room threading or message replies.
- No web UI. Review is via `GET /log` (and, if wanted later, a CLI
  `chatwatch`-style command, deferred).
```
