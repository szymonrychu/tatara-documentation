# tatara-chat (phase 8) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `tatara-chat` Go service - OIDC-gated REST rooms/participants/messages with cursor polling and a 24h TTL sweeper, Postgres-backed, plus a cluster-agnostic helm chart.

**Architecture:** A single Go service mirroring `tatara-memory`'s layout (`cmd/` skeleton, `internal/auth` OIDC, `internal/obs`, `database/sql`+`pgx/v5/stdlib`, embedded-SQL migrations). The chat domain (`internal/chat`) holds the store, handler, types, TTL sweeper, and metrics. A generic chi router (`internal/httpapi`) supplies the middleware stack and mounts chat under auth. Three tables: `chat_rooms`, `chat_participants` (UUID identity), `chat_messages` (append-only, per-participant read cursor).

**Tech Stack:** Go 1.25.x, chi/v5, coreos/go-oidc/v3, golang-jwt/jwt/v5 (test JWKS), jackc/pgx/v5 (stdlib driver), prometheus/client_golang, google/uuid, stretchr/testify. CloudNativePG subchart for storage.

**Spec:** `docs/superpowers/specs/2026-06-05-tatara-chat-design.md`

**Reference sources on disk (read these, do not guess):**
- Conventions/skeleton: `~/Documents/tatara/tatara-memory/` (cmd, internal/auth, internal/obs, internal/version, Dockerfile, Makefile, chart).
- Chat domain reference: `~/Documents/spellslinger/spellslinger-reveries/internal/chat/` (store.go, handler.go, chat.go, migrations/sql/003_chat.sql) - logic to adapt from pgxpool to `database/sql` and from name-keyed to UUID-keyed participants.

**Scope of THIS plan:** the `tatara-chat` repo only (service + chart + tests), producing software testable on its own. Out of this plan (separate follow-on plans, listed at the end): the tatara-cli chat MCP tool group, the Keycloak client, and the infra/helmfile release + ingress + live deploy.

**Module path:** `github.com/szymonrychu/tatara-chat`. Repo dir: `~/Documents/tatara/tatara-chat/`.

**Conventions for "copy" steps:** several infra files are taken verbatim from tatara-memory with two mechanical edits unless noted: replace the module path `github.com/szymonrychu/tatara-memory` -> `github.com/szymonrychu/tatara-chat`, and replace the literal string `tatara-memory` -> `tatara-chat`. These files contain no memory-specific logic.

---

## Task 1: Repo scaffold

**Files:**
- Create: `~/Documents/tatara/tatara-chat/{go.mod,.gitignore,CLAUDE.md,MEMORY.md,ROADMAP.md,Makefile,Dockerfile}`

- [ ] **Step 1: Create the repo dir and init git**

```bash
mkdir -p ~/Documents/tatara/tatara-chat
cd ~/Documents/tatara/tatara-chat
git init -q
```

- [ ] **Step 2: Write `go.mod`**

```
module github.com/szymonrychu/tatara-chat

go 1.25.0

require (
	github.com/coreos/go-oidc/v3 v3.18.0
	github.com/go-chi/chi/v5 v5.3.0
	github.com/golang-jwt/jwt/v5 v5.3.1
	github.com/google/uuid v1.6.0
	github.com/jackc/pgx/v5 v5.7.6
	github.com/prometheus/client_golang v1.24.0
	github.com/stretchr/testify v1.12.0
)
```

(Exact patch versions: after writing, run `go mod tidy` which resolves the full set; match tatara-memory's `go.mod`/`go.sum` versions where they overlap by copying lines. Confirm the Go directive is the newest stable installed: `go version` - bump `go 1.25.0` to the installed minor if newer, per hard rule 1.)

- [ ] **Step 3: Copy `.gitignore`, `CLAUDE.md`, `Dockerfile`, `Makefile` from tatara-memory**

```bash
cd ~/Documents/tatara/tatara-chat
for f in .gitignore CLAUDE.md Dockerfile Makefile; do cp ~/Documents/tatara/tatara-memory/$f ./$f; done
```

Then edit `Dockerfile` and `Makefile`: replace every `tatara-memory` with `tatara-chat` (binary name, build path `./cmd/tatara-chat`). `CLAUDE.md` is the shared canonical contract - leave its body unchanged (it already describes the whole platform). The `.gitignore` is generic.

- [ ] **Step 4: Write `MEMORY.md` and `ROADMAP.md` stubs**

`MEMORY.md`:
```markdown
# MEMORY.md - tatara-chat

Append-only log of non-obvious decisions and dead-ends. Newest first.
Format: `YYYY-MM-DD - decision/finding`

---

- 2026-06-05 - **Phase 8 init.** Service mirrors tatara-memory conventions
  (database/sql + pgx stdlib, embedded-SQL Migrate, internal/auth OIDC,
  internal/obs). Chat domain adapted from spellslinger-reveries; participant
  identity is a server-generated UUID (not a caller-chosen name); messages
  carry nullable author_id so TTL/system notices need no synthetic
  participant. Spec: docs/superpowers/specs/2026-06-05-tatara-chat-design.md
  in the parent tatara repo.
```

`ROADMAP.md`:
```markdown
# ROADMAP.md - tatara-chat

Statuses: planned, in progress, shipped.

---

## v0.1.0 - MVP

**Status:** in progress

REST rooms/participants/messages, cursor polling, 24h TTL sweeper, OIDC
audience `tatara-chat`, cnpg-backed, cluster-agnostic chart.

## Follow-on (separate plans)

- tatara-cli `chat` MCP tool group + REST client.
- Keycloak `tatara-chat` confidential client + audience mapper entry.
- infra/helmfile `tatara` bucket release + ingress + live smoke.
```

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-chat
git add -A && git commit -q -m "chore: scaffold tatara-chat repo"
```

---

## Task 2: Copy infra packages (auth, obs, version)

**Files:**
- Create: `internal/auth/{auth.go,verifier.go,middleware.go,testjwks/testjwks.go}`
- Create: `internal/obs/{obs.go,metrics.go,tracing.go}`
- Create: `internal/version/version.go`

- [ ] **Step 1: Copy the packages verbatim**

```bash
cd ~/Documents/tatara/tatara-chat
mkdir -p internal
cp -R ~/Documents/tatara/tatara-memory/internal/auth ./internal/auth
cp -R ~/Documents/tatara/tatara-memory/internal/obs ./internal/obs
cp -R ~/Documents/tatara/tatara-memory/internal/version ./internal/version
```

- [ ] **Step 2: Apply the two mechanical edits across the copied tree**

Replace `github.com/szymonrychu/tatara-memory` -> `github.com/szymonrychu/tatara-chat` and `tatara-memory` -> `tatara-chat` in every copied file. In `internal/auth/middleware.go` this changes `Bearer realm="tatara-memory"` -> `Bearer realm="tatara-chat"`. In `internal/obs/*` it changes the tracer service name default if present.

```bash
cd ~/Documents/tatara/tatara-chat
grep -rl 'tatara-memory' internal/auth internal/obs internal/version | xargs sed -i '' 's#github.com/szymonrychu/tatara-memory#github.com/szymonrychu/tatara-chat#g; s/tatara-memory/tatara-chat/g'
```

- [ ] **Step 3: Verify it builds the auth verifier test (testjwks)**

Run: `cd ~/Documents/tatara/tatara-chat && go build ./internal/auth/... ./internal/obs/... ./internal/version/...`
Expected: builds clean (after `go mod tidy` in Task 1; if deps missing, run `go mod tidy`).

- [ ] **Step 4: Commit**

```bash
git add internal/auth internal/obs internal/version && git commit -q -m "chore: copy auth/obs/version infra from tatara-memory"
```

---

## Task 3: Chat types and visibility filter

**Files:**
- Create: `internal/chat/chat.go`
- Test: `internal/chat/chat_test.go`

- [ ] **Step 1: Write the failing test**

`internal/chat/chat_test.go`:
```go
package chat

import "testing"

func sp(s string) *string { return &s }

func TestFilterVisible(t *testing.T) {
	me := "p-me"
	other := "p-other"
	msgs := []Message{
		{ID: 1, Body: "global", AuthorID: sp(other)},                 // global, visible
		{ID: 2, Body: "dm-to-me", AuthorID: sp(other), TargetID: sp(me)},   // dm to me, visible
		{ID: 3, Body: "dm-to-other", AuthorID: sp(me), TargetID: sp(other)}, // dm by me, visible
		{ID: 4, Body: "dm-elsewhere", AuthorID: sp(other), TargetID: sp(other)}, // hidden
		{ID: 5, Body: "system", Kind: "system"},                      // global system, visible
	}
	got := filterVisible(msgs, me)
	if len(got) != 4 {
		t.Fatalf("want 4 visible, got %d", len(got))
	}
	for _, m := range got {
		if m.ID == 4 {
			t.Fatalf("message 4 should be hidden from %s", me)
		}
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Documents/tatara/tatara-chat && go test ./internal/chat/ -run TestFilterVisible`
Expected: FAIL (undefined: Message, filterVisible).

- [ ] **Step 3: Write `internal/chat/chat.go`**

```go
// Package chat provides the agent chat subsystem: named rooms, participants
// with server-generated UUID identity, and an append-only message log with
// per-participant read cursors.
package chat

import (
	"errors"
	"time"
)

// ErrNotFound is returned when a room or participant does not exist.
var ErrNotFound = errors.New("not found")

// ErrRoomArchived is returned when writing to an archived room.
var ErrRoomArchived = errors.New("room is archived")

// Room is a named chat instance.
type Room struct {
	ID        string    `json:"id"`
	Name      string    `json:"name"`
	CreatedBy string    `json:"created_by"`
	Status    string    `json:"status"`
	CreatedAt time.Time `json:"created_at"`
}

// Participant is an agent enrolled in a room. ID is server-generated and is
// the handle the agent uses to send and poll. Name is a display label only.
type Participant struct {
	ID         string     `json:"id"`
	RoomID     string     `json:"room_id"`
	Name       string     `json:"name"`
	Role       string     `json:"role"`
	JoinedAt   time.Time  `json:"joined_at"`
	LeftAt     *time.Time `json:"left_at,omitempty"`
	LastSeenID int64      `json:"last_seen_id"`
}

// Message is one entry in a room's append-only log. TargetID nil means the
// message went to the whole room; a non-nil TargetID is a direct message.
// AuthorID is nil for system-generated lifecycle notices.
type Message struct {
	ID         int64     `json:"id"`
	RoomID     string    `json:"room_id"`
	AuthorID   *string   `json:"author_id,omitempty"`
	AuthorName string    `json:"author_name"`
	TargetID   *string   `json:"target_id,omitempty"`
	Kind       string    `json:"kind"`
	Body       string    `json:"body"`
	CreatedAt  time.Time `json:"created_at"`
}

// filterVisible returns the messages visible to the participant id: global
// messages plus direct messages sent to or by the participant.
func filterVisible(msgs []Message, pid string) []Message {
	out := make([]Message, 0, len(msgs))
	for _, m := range msgs {
		switch {
		case m.TargetID == nil:
			out = append(out, m)
		case *m.TargetID == pid:
			out = append(out, m)
		case m.AuthorID != nil && *m.AuthorID == pid:
			out = append(out, m)
		}
	}
	return out
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/Documents/tatara/tatara-chat && go test ./internal/chat/ -run TestFilterVisible`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/chat/chat.go internal/chat/chat_test.go && git commit -q -m "feat: chat types and visibility filter"
```

---

## Task 4: Schema migration and Migrate()

**Files:**
- Create: `internal/chat/migrations/0001_chat.sql`
- Create: `internal/chat/migrate.go`
- Test: `internal/chat/migrate_test.go`

- [ ] **Step 1: Write `internal/chat/migrations/0001_chat.sql`**

```sql
CREATE TABLE IF NOT EXISTS chat_rooms (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'archived')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chat_participants (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id      UUID NOT NULL REFERENCES chat_rooms(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'implementer'
                 CHECK (role IN ('orchestrator', 'implementer', 'reviewer', 'human')),
    joined_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    left_at      TIMESTAMPTZ,
    last_seen_id BIGINT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_chat_participants_room ON chat_participants (room_id);

CREATE TABLE IF NOT EXISTS chat_messages (
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
CREATE INDEX IF NOT EXISTS idx_chat_messages_room ON chat_messages (room_id, id);
```

- [ ] **Step 2: Write `internal/chat/migrate.go`**

```go
package chat

import (
	"context"
	"database/sql"
	_ "embed"
)

//go:embed migrations/0001_chat.sql
var migration0001 string

// Migrate applies the chat schema to db, creating tables if they do not exist.
func Migrate(ctx context.Context, db *sql.DB) error {
	_, err := db.ExecContext(ctx, migration0001)
	return err
}
```

- [ ] **Step 3: Write the failing integration test**

`internal/chat/migrate_test.go`:
```go
//go:build integration

package chat_test

import (
	"context"
	"database/sql"
	"os"
	"testing"

	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-chat/internal/chat"
)

// openPG returns a *sql.DB to a throwaway test Postgres, skipping when
// TATARA_TEST_PG_DSN is unset. Each test migrates and truncates.
func openPG(t *testing.T) *sql.DB {
	t.Helper()
	dsn := os.Getenv("TATARA_TEST_PG_DSN")
	if dsn == "" {
		t.Skip("TATARA_TEST_PG_DSN not set")
	}
	db, err := sql.Open("pgx", dsn)
	require.NoError(t, err)
	require.NoError(t, db.PingContext(context.Background()))
	require.NoError(t, chat.Migrate(context.Background(), db))
	_, err = db.Exec(`TRUNCATE chat_messages, chat_participants, chat_rooms RESTART IDENTITY CASCADE`)
	require.NoError(t, err)
	return db
}

func TestMigrateIdempotent(t *testing.T) {
	db := openPG(t)
	defer db.Close()
	require.NoError(t, chat.Migrate(context.Background(), db))
}
```

- [ ] **Step 4: Run the integration test**

Run: `cd ~/Documents/tatara/tatara-chat && go test -tags integration ./internal/chat/ -run TestMigrateIdempotent -v`
Expected: PASS if `TATARA_TEST_PG_DSN` points at a Postgres (e.g. `export TATARA_TEST_PG_DSN='postgres://postgres:postgres@localhost:5432/postgres?sslmode=disable'`; start one with `docker run --rm -d -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16`). Otherwise SKIP.

- [ ] **Step 5: Commit**

```bash
git add internal/chat/migrations internal/chat/migrate.go internal/chat/migrate_test.go && git commit -q -m "feat: chat schema migration"
```

---

## Task 5: Store - rooms

**Files:**
- Create: `internal/chat/store.go`
- Test: `internal/chat/store_test.go`

- [ ] **Step 1: Write the failing integration test**

Append to `internal/chat/store_test.go` (new file):
```go
//go:build integration

package chat_test

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-chat/internal/chat"
)

func TestRoomLifecycle(t *testing.T) {
	ctx := context.Background()
	db := openPG(t)
	defer db.Close()
	s := chat.NewStore(db)

	rm, err := s.CreateRoom(ctx, "stream-x", "orchestrator")
	require.NoError(t, err)
	require.NotEmpty(t, rm.ID)
	require.Equal(t, "active", rm.Status)

	got, err := s.GetRoom(ctx, rm.ID)
	require.NoError(t, err)
	require.Equal(t, rm.ID, got.ID)

	rooms, err := s.ListRooms(ctx, "active")
	require.NoError(t, err)
	require.Len(t, rooms, 1)

	require.NoError(t, s.ArchiveRoom(ctx, rm.ID))
	got, err = s.GetRoom(ctx, rm.ID)
	require.NoError(t, err)
	require.Equal(t, "archived", got.Status)

	_, err = s.GetRoom(ctx, "00000000-0000-0000-0000-000000000000")
	require.ErrorIs(t, err, chat.ErrNotFound)
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/Documents/tatara/tatara-chat && go test -tags integration ./internal/chat/ -run TestRoomLifecycle`
Expected: FAIL (undefined: NewStore).

- [ ] **Step 3: Write `internal/chat/store.go` (rooms section)**

```go
package chat

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
)

// Store persists rooms, participants, and messages on a *sql.DB (pgx stdlib).
type Store struct {
	db *sql.DB
}

// NewStore creates a chat Store.
func NewStore(db *sql.DB) *Store { return &Store{db: db} }

// CreateRoom inserts a new active room.
func (s *Store) CreateRoom(ctx context.Context, name, createdBy string) (Room, error) {
	var rm Room
	err := s.db.QueryRowContext(ctx, `
		INSERT INTO chat_rooms (name, created_by)
		VALUES ($1, $2)
		RETURNING id, name, created_by, status, created_at`,
		name, createdBy).Scan(&rm.ID, &rm.Name, &rm.CreatedBy, &rm.Status, &rm.CreatedAt)
	if err != nil {
		return Room{}, fmt.Errorf("create room: %w", err)
	}
	return rm, nil
}

// ListRooms returns rooms, optionally filtered by status, newest first.
func (s *Store) ListRooms(ctx context.Context, status string) ([]Room, error) {
	q := `SELECT id, name, created_by, status, created_at FROM chat_rooms`
	var args []any
	if status != "" {
		q += ` WHERE status = $1`
		args = append(args, status)
	}
	q += ` ORDER BY created_at DESC`
	rows, err := s.db.QueryContext(ctx, q, args...)
	if err != nil {
		return nil, fmt.Errorf("list rooms: %w", err)
	}
	defer rows.Close()
	var out []Room
	for rows.Next() {
		var rm Room
		if err := rows.Scan(&rm.ID, &rm.Name, &rm.CreatedBy, &rm.Status, &rm.CreatedAt); err != nil {
			return nil, fmt.Errorf("scan room: %w", err)
		}
		out = append(out, rm)
	}
	return out, rows.Err()
}

// GetRoom fetches a room by id. Returns ErrNotFound when missing.
func (s *Store) GetRoom(ctx context.Context, id string) (Room, error) {
	var rm Room
	err := s.db.QueryRowContext(ctx, `
		SELECT id, name, created_by, status, created_at FROM chat_rooms WHERE id = $1`,
		id).Scan(&rm.ID, &rm.Name, &rm.CreatedBy, &rm.Status, &rm.CreatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return Room{}, ErrNotFound
	}
	if err != nil {
		return Room{}, fmt.Errorf("get room: %w", err)
	}
	return rm, nil
}

// ArchiveRoom sets a room's status to archived. Returns ErrNotFound when missing.
func (s *Store) ArchiveRoom(ctx context.Context, id string) error {
	tag, err := s.db.ExecContext(ctx, `UPDATE chat_rooms SET status = 'archived' WHERE id = $1`, id)
	if err != nil {
		return fmt.Errorf("archive room: %w", err)
	}
	n, _ := tag.RowsAffected()
	if n == 0 {
		return ErrNotFound
	}
	return nil
}
```

Note: a malformed UUID passed to `GetRoom`/`ArchiveRoom` makes Postgres error on the `= $1` comparison; the handler (Task 10+) validates UUID format and returns 404 before calling the store, so the store only ever sees syntactically valid UUIDs or the not-found path.

- [ ] **Step 4: Run to verify it passes**

Run: `cd ~/Documents/tatara/tatara-chat && go test -tags integration ./internal/chat/ -run TestRoomLifecycle`
Expected: PASS (or SKIP without a DSN).

- [ ] **Step 5: Commit**

```bash
git add internal/chat/store.go internal/chat/store_test.go && git commit -q -m "feat: chat store rooms"
```

---

## Task 6: Store - participants

**Files:**
- Modify: `internal/chat/store.go`
- Test: `internal/chat/store_test.go`

- [ ] **Step 1: Write the failing test (append to store_test.go)**

```go
func TestParticipantLifecycle(t *testing.T) {
	ctx := context.Background()
	db := openPG(t)
	defer db.Close()
	s := chat.NewStore(db)

	rm, err := s.CreateRoom(ctx, "stream-y", "orchestrator")
	require.NoError(t, err)

	p, err := s.AddParticipant(ctx, rm.ID, "impl-1", "implementer")
	require.NoError(t, err)
	require.NotEmpty(t, p.ID)
	require.Equal(t, rm.ID, p.RoomID)
	require.Equal(t, "implementer", p.Role)

	got, err := s.GetParticipant(ctx, rm.ID, p.ID)
	require.NoError(t, err)
	require.Equal(t, "impl-1", got.Name)

	parts, err := s.ListParticipants(ctx, rm.ID)
	require.NoError(t, err)
	require.Len(t, parts, 1)

	require.NoError(t, s.RemoveParticipant(ctx, rm.ID, p.ID))
	_, err = s.GetParticipant(ctx, rm.ID, p.ID)
	require.ErrorIs(t, err, chat.ErrNotFound) // left participants are not active

	_, err = s.AddParticipant(ctx, "00000000-0000-0000-0000-000000000000", "x", "implementer")
	require.ErrorIs(t, err, chat.ErrNotFound)
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/Documents/tatara/tatara-chat && go test -tags integration ./internal/chat/ -run TestParticipantLifecycle`
Expected: FAIL (undefined: AddParticipant).

- [ ] **Step 3: Add the participants section to `internal/chat/store.go`**

```go
// AddParticipant enrolls an agent in a room and returns it with a
// server-generated UUID id. Its read cursor starts at the room's current max
// message id, so it sees only messages from join onward.
func (s *Store) AddParticipant(ctx context.Context, roomID, name, role string) (Participant, error) {
	if _, err := s.GetRoom(ctx, roomID); err != nil {
		return Participant{}, err
	}
	var p Participant
	err := s.db.QueryRowContext(ctx, `
		INSERT INTO chat_participants (room_id, name, role, last_seen_id)
		VALUES ($1, $2, $3, COALESCE((SELECT MAX(id) FROM chat_messages WHERE room_id = $1), 0))
		RETURNING id, room_id, name, role, joined_at, left_at, last_seen_id`,
		roomID, name, role).Scan(
		&p.ID, &p.RoomID, &p.Name, &p.Role, &p.JoinedAt, &p.LeftAt, &p.LastSeenID)
	if err != nil {
		return Participant{}, fmt.Errorf("add participant: %w", err)
	}
	return p, nil
}

// GetParticipant fetches an active (not left) participant of a room by id.
func (s *Store) GetParticipant(ctx context.Context, roomID, participantID string) (Participant, error) {
	var p Participant
	err := s.db.QueryRowContext(ctx, `
		SELECT id, room_id, name, role, joined_at, left_at, last_seen_id
		FROM chat_participants
		WHERE id = $1 AND room_id = $2 AND left_at IS NULL`,
		participantID, roomID).Scan(
		&p.ID, &p.RoomID, &p.Name, &p.Role, &p.JoinedAt, &p.LeftAt, &p.LastSeenID)
	if errors.Is(err, sql.ErrNoRows) {
		return Participant{}, ErrNotFound
	}
	if err != nil {
		return Participant{}, fmt.Errorf("get participant: %w", err)
	}
	return p, nil
}

// RemoveParticipant marks an active participant as left.
func (s *Store) RemoveParticipant(ctx context.Context, roomID, participantID string) error {
	tag, err := s.db.ExecContext(ctx, `
		UPDATE chat_participants SET left_at = NOW()
		WHERE id = $1 AND room_id = $2 AND left_at IS NULL`,
		participantID, roomID)
	if err != nil {
		return fmt.Errorf("remove participant: %w", err)
	}
	n, _ := tag.RowsAffected()
	if n == 0 {
		return ErrNotFound
	}
	return nil
}

// ListParticipants returns every participant of a room, join order.
func (s *Store) ListParticipants(ctx context.Context, roomID string) ([]Participant, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, room_id, name, role, joined_at, left_at, last_seen_id
		FROM chat_participants WHERE room_id = $1 ORDER BY joined_at`, roomID)
	if err != nil {
		return nil, fmt.Errorf("list participants: %w", err)
	}
	defer rows.Close()
	var out []Participant
	for rows.Next() {
		var p Participant
		if err := rows.Scan(&p.ID, &p.RoomID, &p.Name, &p.Role, &p.JoinedAt, &p.LeftAt, &p.LastSeenID); err != nil {
			return nil, fmt.Errorf("scan participant: %w", err)
		}
		out = append(out, p)
	}
	return out, rows.Err()
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd ~/Documents/tatara/tatara-chat && go test -tags integration ./internal/chat/ -run TestParticipantLifecycle`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/chat/store.go internal/chat/store_test.go && git commit -q -m "feat: chat store participants"
```

---

## Task 7: Store - messages, Poll, Log

**Files:**
- Modify: `internal/chat/store.go`
- Test: `internal/chat/store_test.go`

- [ ] **Step 1: Write the failing test (append to store_test.go)**

```go
func TestMessagesPollLog(t *testing.T) {
	ctx := context.Background()
	db := openPG(t)
	defer db.Close()
	s := chat.NewStore(db)

	rm, _ := s.CreateRoom(ctx, "stream-z", "orchestrator")
	a, _ := s.AddParticipant(ctx, rm.ID, "a", "implementer")
	b, _ := s.AddParticipant(ctx, rm.ID, "b", "reviewer")

	// a posts a global message and a DM to b.
	_, err := s.AddMessage(ctx, rm.ID, a.ID, nil, "message", "global hi")
	require.NoError(t, err)
	_, err = s.AddMessage(ctx, rm.ID, a.ID, &b.ID, "message", "dm to b")
	require.NoError(t, err)

	// b polls: sees both (global + dm-to-b).
	msgs, status, err := s.Poll(ctx, rm.ID, b.ID)
	require.NoError(t, err)
	require.Equal(t, "active", status)
	require.Len(t, msgs, 2)

	// second poll by b: cursor advanced, nothing new.
	msgs, _, err = s.Poll(ctx, rm.ID, b.ID)
	require.NoError(t, err)
	require.Len(t, msgs, 0)

	// a polls: sees its own global + its own dm (author==me), not filtered out.
	msgs, _, err = s.Poll(ctx, rm.ID, a.ID)
	require.NoError(t, err)
	require.Len(t, msgs, 2)

	// log returns everything regardless of cursor.
	all, err := s.Log(ctx, rm.ID)
	require.NoError(t, err)
	require.Len(t, all, 2)

	// invalid author rejected.
	_, err = s.AddMessage(ctx, rm.ID, "00000000-0000-0000-0000-000000000000", nil, "message", "x")
	require.ErrorIs(t, err, chat.ErrNotFound)

	// archived room rejects writes.
	require.NoError(t, s.ArchiveRoom(ctx, rm.ID))
	_, err = s.AddMessage(ctx, rm.ID, a.ID, nil, "message", "late")
	require.ErrorIs(t, err, chat.ErrRoomArchived)
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/Documents/tatara/tatara-chat && go test -tags integration ./internal/chat/ -run TestMessagesPollLog`
Expected: FAIL (undefined: AddMessage).

- [ ] **Step 3: Add the messages section to `internal/chat/store.go`**

Add `"github.com/jackc/pgx/v5/stdlib"` is NOT needed here (driver registered in tests/app). The section:

```go
// AddMessage appends a message to a room. The author must be an active
// participant of the room; the author's display name is denormalized onto the
// row. Archived rooms are rejected.
func (s *Store) AddMessage(ctx context.Context, roomID, authorID string, target *string, kind, body string) (Message, error) {
	rm, err := s.GetRoom(ctx, roomID)
	if err != nil {
		return Message{}, err
	}
	if rm.Status == "archived" {
		return Message{}, ErrRoomArchived
	}
	var m Message
	err = s.db.QueryRowContext(ctx, `
		INSERT INTO chat_messages (room_id, author_id, author_name, target_id, kind, body)
		SELECT $1, p.id, p.name, $3, $4, $5
		FROM chat_participants p
		WHERE p.id = $2 AND p.room_id = $1 AND p.left_at IS NULL
		RETURNING id, room_id, author_id, author_name, target_id, kind, body, created_at`,
		roomID, authorID, target, kind, body).Scan(
		&m.ID, &m.RoomID, &m.AuthorID, &m.AuthorName, &m.TargetID, &m.Kind, &m.Body, &m.CreatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return Message{}, ErrNotFound // author not an active participant
	}
	if err != nil {
		return Message{}, fmt.Errorf("add message: %w", err)
	}
	return m, nil
}

// Poll returns the messages visible to the participant with id greater than
// the participant's read cursor, advances the cursor to the max fetched id,
// and returns the room status. A FOR UPDATE row lock on the participant row
// serializes concurrent polls, preventing cursor overwrites and duplicate
// delivery. The cursor advances past global messages the participant filtered
// out so they are not re-scanned.
func (s *Store) Poll(ctx context.Context, roomID, participantID string) ([]Message, string, error) {
	rm, err := s.GetRoom(ctx, roomID)
	if err != nil {
		return nil, "", err
	}

	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, "", fmt.Errorf("poll begin tx: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	var cursor int64
	err = tx.QueryRowContext(ctx, `
		SELECT last_seen_id FROM chat_participants
		WHERE id = $1 AND room_id = $2 AND left_at IS NULL
		FOR UPDATE`,
		participantID, roomID).Scan(&cursor)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, "", ErrNotFound
	}
	if err != nil {
		return nil, "", fmt.Errorf("poll cursor: %w", err)
	}

	rows, err := tx.QueryContext(ctx, `
		SELECT id, room_id, author_id, author_name, target_id, kind, body, created_at
		FROM chat_messages WHERE room_id = $1 AND id > $2 ORDER BY id`,
		roomID, cursor)
	if err != nil {
		return nil, "", fmt.Errorf("poll messages: %w", err)
	}
	defer rows.Close()
	var all []Message
	for rows.Next() {
		var m Message
		if err := rows.Scan(&m.ID, &m.RoomID, &m.AuthorID, &m.AuthorName, &m.TargetID, &m.Kind, &m.Body, &m.CreatedAt); err != nil {
			return nil, "", fmt.Errorf("scan message: %w", err)
		}
		all = append(all, m)
	}
	if err := rows.Err(); err != nil {
		return nil, "", fmt.Errorf("poll rows: %w", err)
	}

	newCursor := cursor
	if n := len(all); n > 0 {
		newCursor = all[n-1].ID
	}
	if newCursor != cursor {
		if _, err := tx.ExecContext(ctx, `
			UPDATE chat_participants SET last_seen_id = $3
			WHERE id = $1 AND room_id = $2`,
			participantID, roomID, newCursor); err != nil {
			return nil, "", fmt.Errorf("advance cursor: %w", err)
		}
	}

	if err := tx.Commit(ctx); err != nil {
		return nil, "", fmt.Errorf("poll commit: %w", err)
	}
	return filterVisible(all, participantID), rm.Status, nil
}

// Log returns every message in a room in id order, without advancing any
// cursor. Used for review.
func (s *Store) Log(ctx context.Context, roomID string) ([]Message, error) {
	if _, err := s.GetRoom(ctx, roomID); err != nil {
		return nil, err
	}
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, room_id, author_id, author_name, target_id, kind, body, created_at
		FROM chat_messages WHERE room_id = $1 ORDER BY id`, roomID)
	if err != nil {
		return nil, fmt.Errorf("log messages: %w", err)
	}
	defer rows.Close()
	var out []Message
	for rows.Next() {
		var m Message
		if err := rows.Scan(&m.ID, &m.RoomID, &m.AuthorID, &m.AuthorName, &m.TargetID, &m.Kind, &m.Body, &m.CreatedAt); err != nil {
			return nil, fmt.Errorf("scan message: %w", err)
		}
		out = append(out, m)
	}
	return out, rows.Err()
}
```

NOTE: `tx.Commit(ctx)` and `tx.Rollback()` - `database/sql`'s `*sql.Tx` `Commit`/`Rollback` take no context (the tx was opened with `BeginTx(ctx,...)`). Use `tx.Commit()` and `tx.Rollback()` (no args). Adjust the two calls accordingly: `defer func() { _ = tx.Rollback() }()` and `if err := tx.Commit(); err != nil`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd ~/Documents/tatara/tatara-chat && go test -tags integration ./internal/chat/ -run TestMessagesPollLog`
Expected: PASS

- [ ] **Step 5: Add a concurrent-poll no-duplicate test, then commit**

```go
func TestConcurrentPollNoDup(t *testing.T) {
	ctx := context.Background()
	db := openPG(t)
	defer db.Close()
	s := chat.NewStore(db)
	rm, _ := s.CreateRoom(ctx, "race", "orchestrator")
	a, _ := s.AddParticipant(ctx, rm.ID, "a", "implementer")
	for i := 0; i < 20; i++ {
		_, _ = s.AddMessage(ctx, rm.ID, a.ID, nil, "message", "m")
	}
	type res struct{ n int }
	ch := make(chan res, 2)
	for i := 0; i < 2; i++ {
		go func() {
			msgs, _, err := s.Poll(ctx, rm.ID, a.ID)
			require.NoError(t, err)
			ch <- res{len(msgs)}
		}()
	}
	total := (<-ch).n + (<-ch).n
	require.Equal(t, 20, total) // each message delivered exactly once across both polls
}
```

Run: `cd ~/Documents/tatara/tatara-chat && go test -tags integration ./internal/chat/ -run 'TestMessagesPollLog|TestConcurrentPollNoDup' -v`
Expected: PASS

```bash
git add internal/chat/store.go internal/chat/store_test.go && git commit -q -m "feat: chat store messages, poll, log"
```

---

## Task 8: Store - ArchiveExpired (TTL)

**Files:**
- Modify: `internal/chat/store.go`
- Test: `internal/chat/store_test.go`

- [ ] **Step 1: Write the failing test (append to store_test.go)**

```go
func TestArchiveExpired(t *testing.T) {
	ctx := context.Background()
	db := openPG(t)
	defer db.Close()
	s := chat.NewStore(db)

	old, _ := s.CreateRoom(ctx, "old", "orchestrator")
	fresh, _ := s.CreateRoom(ctx, "fresh", "orchestrator")
	// backdate `old` past the TTL.
	_, err := db.Exec(`UPDATE chat_rooms SET created_at = NOW() - INTERVAL '48 hours' WHERE id = $1`, old.ID)
	require.NoError(t, err)

	n, err := s.ArchiveExpired(ctx, 24*time.Hour)
	require.NoError(t, err)
	require.Equal(t, 1, n)

	go1, _ := s.GetRoom(ctx, old.ID)
	require.Equal(t, "archived", go1.Status)
	go2, _ := s.GetRoom(ctx, fresh.ID)
	require.Equal(t, "active", go2.Status)

	// a system notice was written into the archived room.
	msgs, _ := s.Log(ctx, old.ID)
	require.Len(t, msgs, 1)
	require.Equal(t, "system", msgs[0].Kind)
	require.Nil(t, msgs[0].AuthorID)
}
```

Add `"time"` to the test file imports.

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/Documents/tatara/tatara-chat && go test -tags integration ./internal/chat/ -run TestArchiveExpired`
Expected: FAIL (undefined: ArchiveExpired).

- [ ] **Step 3: Add ArchiveExpired to `internal/chat/store.go`**

```go
import "time" // add to the existing import block

// ArchiveExpired archives every active room older than ttl (measured from
// created_at), writes a system notice into each, and returns the count
// archived. Runs in one transaction.
func (s *Store) ArchiveExpired(ctx context.Context, ttl time.Duration) (int, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return 0, fmt.Errorf("ttl begin tx: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	rows, err := tx.QueryContext(ctx, `
		UPDATE chat_rooms SET status = 'archived'
		WHERE status = 'active' AND created_at < NOW() - make_interval(secs => $1)
		RETURNING id`, ttl.Seconds())
	if err != nil {
		return 0, fmt.Errorf("ttl archive: %w", err)
	}
	var ids []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			rows.Close()
			return 0, fmt.Errorf("ttl scan: %w", err)
		}
		ids = append(ids, id)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return 0, fmt.Errorf("ttl rows: %w", err)
	}

	for _, id := range ids {
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO chat_messages (room_id, kind, body)
			VALUES ($1, 'system', 'room auto-archived after TTL')`, id); err != nil {
			return 0, fmt.Errorf("ttl notice: %w", err)
		}
	}

	if err := tx.Commit(); err != nil {
		return 0, fmt.Errorf("ttl commit: %w", err)
	}
	return len(ids), nil
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd ~/Documents/tatara/tatara-chat && go test -tags integration ./internal/chat/ -run TestArchiveExpired`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/chat/store.go internal/chat/store_test.go && git commit -q -m "feat: chat store TTL ArchiveExpired"
```

---

## Task 9: Response helpers and domain metrics

**Files:**
- Create: `internal/chat/httpx.go`
- Create: `internal/chat/metrics.go`

- [ ] **Step 1: Write `internal/chat/httpx.go`**

```go
package chat

import (
	"encoding/json"
	"net/http"
)

type errorBody struct {
	Error string `json:"error"`
	Field string `json:"field,omitempty"`
	Hint  string `json:"hint,omitempty"`
}

// WriteError writes a JSON error with an optional field and hint.
func WriteError(w http.ResponseWriter, status int, msg, field, hint string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(errorBody{Error: msg, Field: field, Hint: hint})
}

// WriteJSON serialises body as JSON with the given status.
func WriteJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}
```

- [ ] **Step 2: Write `internal/chat/metrics.go`**

```go
package chat

import "github.com/prometheus/client_golang/prometheus"

// Metrics holds the chat domain counters/gauges.
type Metrics struct {
	Rooms        *prometheus.CounterVec // op: created|archived|ttl_archived
	Participants *prometheus.CounterVec // op: join|leave
	Messages     *prometheus.CounterVec // kind x scope (global|dm)
	Polls        prometheus.Counter
	ActiveRooms  prometheus.Gauge
}

// NewMetrics registers and returns the chat metrics on reg.
func NewMetrics(reg prometheus.Registerer) *Metrics {
	m := &Metrics{
		Rooms: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "chat_rooms_total", Help: "Chat rooms by operation."}, []string{"op"}),
		Participants: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "chat_participants_total", Help: "Chat participant ops."}, []string{"op"}),
		Messages: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "chat_messages_total", Help: "Chat messages by kind and scope."}, []string{"kind", "scope"}),
		Polls: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "chat_polls_total", Help: "Chat poll requests."}),
		ActiveRooms: prometheus.NewGauge(prometheus.GaugeOpts{
			Name: "chat_active_rooms", Help: "Active chat rooms (sampled by the sweeper)."}),
	}
	reg.MustRegister(m.Rooms, m.Participants, m.Messages, m.Polls, m.ActiveRooms)
	return m
}
```

- [ ] **Step 3: Verify build**

Run: `cd ~/Documents/tatara/tatara-chat && go build ./internal/chat/`
Expected: builds clean.

- [ ] **Step 4: Commit**

```bash
git add internal/chat/httpx.go internal/chat/metrics.go && git commit -q -m "feat: chat response helpers and metrics"
```

---

## Task 10: Handler - rooms endpoints

**Files:**
- Create: `internal/chat/handler.go`
- Test: `internal/chat/handler_test.go`

The handler depends on an interface (`chatStore`) so tests use a fake. Define the full interface now (covers all later handler tasks).

- [ ] **Step 1: Write the failing test**

`internal/chat/handler_test.go`:
```go
package chat_test

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-chat/internal/chat"
)

// fakeStore is an in-memory chatStore for handler tests.
type fakeStore struct {
	rooms   map[string]chat.Room
	parts   map[string]chat.Participant
	created int
}

func newFake() *fakeStore {
	return &fakeStore{rooms: map[string]chat.Room{}, parts: map[string]chat.Participant{}}
}

func (f *fakeStore) CreateRoom(_ context.Context, name, by string) (chat.Room, error) {
	f.created++
	id := "room-" + name
	rm := chat.Room{ID: id, Name: name, CreatedBy: by, Status: "active", CreatedAt: time.Now()}
	f.rooms[id] = rm
	return rm, nil
}
func (f *fakeStore) ListRooms(_ context.Context, status string) ([]chat.Room, error) {
	var out []chat.Room
	for _, r := range f.rooms {
		if status == "" || r.Status == status {
			out = append(out, r)
		}
	}
	return out, nil
}
func (f *fakeStore) GetRoom(_ context.Context, id string) (chat.Room, error) {
	r, ok := f.rooms[id]
	if !ok {
		return chat.Room{}, chat.ErrNotFound
	}
	return r, nil
}
func (f *fakeStore) ArchiveRoom(_ context.Context, id string) error {
	r, ok := f.rooms[id]
	if !ok {
		return chat.ErrNotFound
	}
	r.Status = "archived"
	f.rooms[id] = r
	return nil
}
func (f *fakeStore) AddParticipant(_ context.Context, roomID, name, role string) (chat.Participant, error) {
	if _, ok := f.rooms[roomID]; !ok {
		return chat.Participant{}, chat.ErrNotFound
	}
	p := chat.Participant{ID: "p-" + name, RoomID: roomID, Name: name, Role: role, JoinedAt: time.Now()}
	f.parts[p.ID] = p
	return p, nil
}
func (f *fakeStore) GetParticipant(_ context.Context, roomID, id string) (chat.Participant, error) {
	p, ok := f.parts[id]
	if !ok || p.RoomID != roomID {
		return chat.Participant{}, chat.ErrNotFound
	}
	return p, nil
}
func (f *fakeStore) RemoveParticipant(_ context.Context, roomID, id string) error {
	p, ok := f.parts[id]
	if !ok || p.RoomID != roomID {
		return chat.ErrNotFound
	}
	delete(f.parts, id)
	return nil
}
func (f *fakeStore) ListParticipants(_ context.Context, roomID string) ([]chat.Participant, error) {
	var out []chat.Participant
	for _, p := range f.parts {
		if p.RoomID == roomID {
			out = append(out, p)
		}
	}
	return out, nil
}
func (f *fakeStore) AddMessage(_ context.Context, roomID, authorID string, target *string, kind, body string) (chat.Message, error) {
	r, ok := f.rooms[roomID]
	if !ok {
		return chat.Message{}, chat.ErrNotFound
	}
	if r.Status == "archived" {
		return chat.Message{}, chat.ErrRoomArchived
	}
	p, ok := f.parts[authorID]
	if !ok || p.RoomID != roomID {
		return chat.Message{}, chat.ErrNotFound
	}
	aid := authorID
	return chat.Message{ID: 1, RoomID: roomID, AuthorID: &aid, AuthorName: p.Name, TargetID: target, Kind: kind, Body: body, CreatedAt: time.Now()}, nil
}
func (f *fakeStore) Poll(_ context.Context, roomID, id string) ([]chat.Message, string, error) {
	r, ok := f.rooms[roomID]
	if !ok {
		return nil, "", chat.ErrNotFound
	}
	if _, ok := f.parts[id]; !ok {
		return nil, "", chat.ErrNotFound
	}
	return nil, r.Status, nil
}
func (f *fakeStore) Log(_ context.Context, roomID string) ([]chat.Message, error) {
	if _, ok := f.rooms[roomID]; !ok {
		return nil, chat.ErrNotFound
	}
	return nil, nil
}

func newTestRouter(f *fakeStore) http.Handler {
	h := chat.NewHandler(f, chat.NewMetrics(prometheus.NewRegistry()))
	r := chi.NewRouter()
	r.Route("/", h.Routes)
	return r
}

func TestCreateRoom(t *testing.T) {
	r := newTestRouter(newFake())
	req := httptest.NewRequest(http.MethodPost, "/rooms", strings.NewReader(`{"name":"stream-1","created_by":"orchestrator"}`))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusCreated, w.Code)
	require.Contains(t, w.Body.String(), `"id"`)
}

func TestCreateRoomMissingName(t *testing.T) {
	r := newTestRouter(newFake())
	req := httptest.NewRequest(http.MethodPost, "/rooms", strings.NewReader(`{}`))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusBadRequest, w.Code)
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/Documents/tatara/tatara-chat && go test ./internal/chat/ -run TestCreateRoom`
Expected: FAIL (undefined: NewHandler).

- [ ] **Step 3: Write `internal/chat/handler.go`**

```go
package chat

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"slices"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

// chatStore is the persistence surface the handler needs.
type chatStore interface {
	CreateRoom(ctx context.Context, name, createdBy string) (Room, error)
	ListRooms(ctx context.Context, status string) ([]Room, error)
	GetRoom(ctx context.Context, id string) (Room, error)
	ArchiveRoom(ctx context.Context, id string) error
	AddParticipant(ctx context.Context, roomID, name, role string) (Participant, error)
	GetParticipant(ctx context.Context, roomID, participantID string) (Participant, error)
	RemoveParticipant(ctx context.Context, roomID, participantID string) error
	ListParticipants(ctx context.Context, roomID string) ([]Participant, error)
	AddMessage(ctx context.Context, roomID, authorID string, target *string, kind, body string) (Message, error)
	Poll(ctx context.Context, roomID, participantID string) ([]Message, string, error)
	Log(ctx context.Context, roomID string) ([]Message, error)
}

var (
	validRoles = []string{"orchestrator", "implementer", "reviewer", "human"}
	validKinds = []string{"message", "system"}
)

// Handler serves the chat API.
type Handler struct {
	store   chatStore
	metrics *Metrics
}

// NewHandler creates a chat Handler.
func NewHandler(s chatStore, m *Metrics) *Handler { return &Handler{store: s, metrics: m} }

// Routes registers chat routes on r. Mount with r.Route("/", h.Routes) or under a prefix.
func (h *Handler) Routes(r chi.Router) {
	r.Post("/rooms", h.createRoom)
	r.Get("/rooms", h.listRooms)
	r.Get("/rooms/{id}", h.getRoom)
	r.Delete("/rooms/{id}", h.archiveRoom)
	r.Post("/rooms/{id}/participants", h.addParticipant)
	r.Get("/rooms/{id}/participants", h.listParticipants)
	r.Delete("/rooms/{id}/participants/{participantId}", h.removeParticipant)
	r.Post("/rooms/{id}/messages", h.sendMessage)
	r.Get("/rooms/{id}/messages", h.pollMessages)
	r.Get("/rooms/{id}/log", h.log)
}

// validRoomID returns the room id from the URL, or "" (and writes 404) if it
// is not a valid UUID - keeps malformed ids out of the store's SQL.
func validRoomID(w http.ResponseWriter, r *http.Request) string {
	id := chi.URLParam(r, "id")
	if _, err := uuid.Parse(id); err != nil {
		WriteError(w, http.StatusNotFound, "room not found", "id", "check the room id")
		return ""
	}
	return id
}

type createRoomReq struct {
	Name      string `json:"name"`
	CreatedBy string `json:"created_by"`
}

func (h *Handler) createRoom(w http.ResponseWriter, r *http.Request) {
	var req createRoomReq
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, "invalid request body", "", "send JSON with a name field")
		return
	}
	if req.Name == "" {
		WriteError(w, http.StatusBadRequest, "name is required", "name", "name the room after the implementation stream")
		return
	}
	createdBy := req.CreatedBy
	if createdBy == "" {
		createdBy = "orchestrator"
	}
	rm, err := h.store.CreateRoom(r.Context(), req.Name, createdBy)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to create room", "", "")
		return
	}
	h.metrics.Rooms.WithLabelValues("created").Inc()
	WriteJSON(w, http.StatusCreated, rm)
}

func (h *Handler) listRooms(w http.ResponseWriter, r *http.Request) {
	status := r.URL.Query().Get("status")
	if status != "" && status != "active" && status != "archived" {
		WriteError(w, http.StatusBadRequest, "invalid status filter", "status", "status must be active or archived")
		return
	}
	rooms, err := h.store.ListRooms(r.Context(), status)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to list rooms", "", "")
		return
	}
	if rooms == nil {
		rooms = []Room{}
	}
	WriteJSON(w, http.StatusOK, map[string]any{"rooms": rooms, "count": len(rooms)})
}

func (h *Handler) getRoom(w http.ResponseWriter, r *http.Request) {
	id := validRoomID(w, r)
	if id == "" {
		return
	}
	rm, err := h.store.GetRoom(r.Context(), id)
	if errors.Is(err, ErrNotFound) {
		WriteError(w, http.StatusNotFound, "room not found", "id", "check the room id")
		return
	}
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to fetch room", "", "")
		return
	}
	parts, err := h.store.ListParticipants(r.Context(), id)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to fetch participants", "", "")
		return
	}
	if parts == nil {
		parts = []Participant{}
	}
	WriteJSON(w, http.StatusOK, map[string]any{"room": rm, "participants": parts})
}

func (h *Handler) archiveRoom(w http.ResponseWriter, r *http.Request) {
	id := validRoomID(w, r)
	if id == "" {
		return
	}
	err := h.store.ArchiveRoom(r.Context(), id)
	if errors.Is(err, ErrNotFound) {
		WriteError(w, http.StatusNotFound, "room not found", "id", "check the room id")
		return
	}
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to archive room", "", "")
		return
	}
	h.metrics.Rooms.WithLabelValues("archived").Inc()
	w.WriteHeader(http.StatusNoContent)
}
```

(The participant and message handlers are added in Tasks 11 and 12; the file already references them via Routes, so those tasks complete the file. To keep this task compiling on its own, temporarily stub the four missing methods - `addParticipant`, `listParticipants`, `removeParticipant`, `sendMessage`, `pollMessages`, `log` - by adding them in this step as `http.NotFound(w, r)` one-liners, then replace them in Tasks 11-12.)

Add these temporary stubs at the end of `handler.go`:
```go
func (h *Handler) addParticipant(w http.ResponseWriter, r *http.Request)    { http.NotFound(w, r) }
func (h *Handler) listParticipants(w http.ResponseWriter, r *http.Request)  { http.NotFound(w, r) }
func (h *Handler) removeParticipant(w http.ResponseWriter, r *http.Request) { http.NotFound(w, r) }
func (h *Handler) sendMessage(w http.ResponseWriter, r *http.Request)       { http.NotFound(w, r) }
func (h *Handler) pollMessages(w http.ResponseWriter, r *http.Request)      { http.NotFound(w, r) }
func (h *Handler) log(w http.ResponseWriter, r *http.Request)               { http.NotFound(w, r) }
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd ~/Documents/tatara/tatara-chat && go test ./internal/chat/ -run TestCreateRoom`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/chat/handler.go internal/chat/handler_test.go && git commit -q -m "feat: chat handler rooms endpoints"
```

---

## Task 11: Handler - participants endpoints

**Files:**
- Modify: `internal/chat/handler.go` (replace the participant stubs)
- Test: `internal/chat/handler_test.go`

- [ ] **Step 1: Write the failing test (append to handler_test.go)**

```go
func TestAddParticipantReturnsUUID(t *testing.T) {
	f := newFake()
	rm, _ := f.CreateRoom(context.Background(), "s", "orchestrator")
	r := newTestRouter(f)
	req := httptest.NewRequest(http.MethodPost, "/rooms/"+rm.ID+"/participants",
		strings.NewReader(`{"name":"impl-1","role":"implementer"}`))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusCreated, w.Code)
	require.Contains(t, w.Body.String(), `"id"`)
}

func TestAddParticipantInvalidRole(t *testing.T) {
	f := newFake()
	rm, _ := f.CreateRoom(context.Background(), "s", "orchestrator")
	r := newTestRouter(f)
	req := httptest.NewRequest(http.MethodPost, "/rooms/"+rm.ID+"/participants",
		strings.NewReader(`{"name":"x","role":"wizard"}`))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusBadRequest, w.Code)
}
```

Note: the fake creates room ids like `room-s` which are not UUIDs. To keep `validRoomID` from rejecting them in handler tests, the participant/message routes use a separate URL param that is NOT UUID-validated for the room (the room id is validated by the store via ErrNotFound). Adjust `validRoomID` usage: apply UUID validation ONLY on `GET/DELETE /rooms/{id}` (Task 10). For nested participant/message routes, read the raw `chi.URLParam(r, "id")` and rely on store ErrNotFound -> 404. (This keeps fakes simple and still keeps malformed ids from reaching real SQL in a damaging way, since the store's GetRoom/AddParticipant return ErrNotFound for unknown ids; a malformed-UUID SQL error maps to 500, acceptable for the nested case. If you prefer strict 404s here too, switch the fake to emit UUID ids.)

For determinism, change the fake to emit UUID-shaped ids: in `newFake`'s `CreateRoom`, set `id := uuid.NewString()` and in `AddParticipant` `p.ID = uuid.NewString()`, importing `github.com/google/uuid` in the test. Then nested handlers can also use `validRoomID`. Implement this fake change as part of Step 1.

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/Documents/tatara/tatara-chat && go test ./internal/chat/ -run TestAddParticipant`
Expected: FAIL (stub returns 404).

- [ ] **Step 3: Replace the participant stubs in `internal/chat/handler.go`**

Remove the three participant stub lines and add:
```go
type addParticipantReq struct {
	Name string `json:"name"`
	Role string `json:"role"`
}

func (h *Handler) addParticipant(w http.ResponseWriter, r *http.Request) {
	id := validRoomID(w, r)
	if id == "" {
		return
	}
	var req addParticipantReq
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, "invalid request body", "", "send JSON with name and role")
		return
	}
	if req.Name == "" {
		WriteError(w, http.StatusBadRequest, "name is required", "name", "give the participant a display name")
		return
	}
	role := req.Role
	if role == "" {
		role = "implementer"
	}
	if !slices.Contains(validRoles, role) {
		WriteError(w, http.StatusBadRequest, "invalid role", "role",
			"role must be one of: orchestrator, implementer, reviewer, human")
		return
	}
	p, err := h.store.AddParticipant(r.Context(), id, req.Name, role)
	if errors.Is(err, ErrNotFound) {
		WriteError(w, http.StatusNotFound, "room not found", "id", "check the room id")
		return
	}
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to add participant", "", "")
		return
	}
	h.metrics.Participants.WithLabelValues("join").Inc()
	WriteJSON(w, http.StatusCreated, p)
}

func (h *Handler) listParticipants(w http.ResponseWriter, r *http.Request) {
	id := validRoomID(w, r)
	if id == "" {
		return
	}
	if _, err := h.store.GetRoom(r.Context(), id); errors.Is(err, ErrNotFound) {
		WriteError(w, http.StatusNotFound, "room not found", "id", "check the room id")
		return
	}
	parts, err := h.store.ListParticipants(r.Context(), id)
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to list participants", "", "")
		return
	}
	if parts == nil {
		parts = []Participant{}
	}
	WriteJSON(w, http.StatusOK, map[string]any{"participants": parts, "count": len(parts)})
}

func (h *Handler) removeParticipant(w http.ResponseWriter, r *http.Request) {
	id := validRoomID(w, r)
	if id == "" {
		return
	}
	pid := chi.URLParam(r, "participantId")
	if _, err := uuid.Parse(pid); err != nil {
		WriteError(w, http.StatusNotFound, "participant not found", "participantId", "check the participant id")
		return
	}
	err := h.store.RemoveParticipant(r.Context(), id, pid)
	if errors.Is(err, ErrNotFound) {
		WriteError(w, http.StatusNotFound, "participant not found", "participantId", "check the participant id")
		return
	}
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to remove participant", "", "")
		return
	}
	h.metrics.Participants.WithLabelValues("leave").Inc()
	w.WriteHeader(http.StatusNoContent)
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd ~/Documents/tatara/tatara-chat && go test ./internal/chat/ -run 'TestAddParticipant|TestCreateRoom'`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/chat/handler.go internal/chat/handler_test.go && git commit -q -m "feat: chat handler participants endpoints"
```

---

## Task 12: Handler - messages, poll, log endpoints

**Files:**
- Modify: `internal/chat/handler.go` (replace the message stubs)
- Test: `internal/chat/handler_test.go`

- [ ] **Step 1: Write the failing test (append to handler_test.go)**

```go
func TestSendMessage(t *testing.T) {
	f := newFake()
	rm, _ := f.CreateRoom(context.Background(), "s", "orchestrator")
	p, _ := f.AddParticipant(context.Background(), rm.ID, "a", "implementer")
	r := newTestRouter(f)
	body := `{"participant_id":"` + p.ID + `","body":"hello"}`
	req := httptest.NewRequest(http.MethodPost, "/rooms/"+rm.ID+"/messages", strings.NewReader(body))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusCreated, w.Code)
}

func TestSendMessageArchived(t *testing.T) {
	f := newFake()
	rm, _ := f.CreateRoom(context.Background(), "s", "orchestrator")
	p, _ := f.AddParticipant(context.Background(), rm.ID, "a", "implementer")
	_ = f.ArchiveRoom(context.Background(), rm.ID)
	r := newTestRouter(f)
	body := `{"participant_id":"` + p.ID + `","body":"late"}`
	req := httptest.NewRequest(http.MethodPost, "/rooms/"+rm.ID+"/messages", strings.NewReader(body))
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusConflict, w.Code)
}

func TestPollRequiresParticipant(t *testing.T) {
	f := newFake()
	rm, _ := f.CreateRoom(context.Background(), "s", "orchestrator")
	r := newTestRouter(f)
	req := httptest.NewRequest(http.MethodGet, "/rooms/"+rm.ID+"/messages", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	require.Equal(t, http.StatusBadRequest, w.Code)
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/Documents/tatara/tatara-chat && go test ./internal/chat/ -run 'TestSendMessage|TestPoll'`
Expected: FAIL (stubs 404).

- [ ] **Step 3: Replace the message stubs in `internal/chat/handler.go`**

```go
type sendMessageReq struct {
	ParticipantID string  `json:"participant_id"`
	Target        *string `json:"target"`
	Kind          string  `json:"kind"`
	Body          string  `json:"body"`
}

func (h *Handler) sendMessage(w http.ResponseWriter, r *http.Request) {
	id := validRoomID(w, r)
	if id == "" {
		return
	}
	var req sendMessageReq
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		WriteError(w, http.StatusBadRequest, "invalid request body", "", "send JSON with participant_id and body")
		return
	}
	if _, err := uuid.Parse(req.ParticipantID); err != nil {
		WriteError(w, http.StatusBadRequest, "participant_id is required", "participant_id", "use the id returned when the participant joined")
		return
	}
	if req.Body == "" {
		WriteError(w, http.StatusBadRequest, "body is required", "body", "provide message text")
		return
	}
	kind := req.Kind
	if kind == "" {
		kind = "message"
	}
	if !slices.Contains(validKinds, kind) {
		WriteError(w, http.StatusBadRequest, "invalid kind", "kind", "kind must be message or system")
		return
	}
	if req.Target != nil {
		if *req.Target == "" {
			req.Target = nil
		} else if _, err := uuid.Parse(*req.Target); err != nil {
			WriteError(w, http.StatusBadRequest, "invalid target", "target", "target must be a participant id")
			return
		}
	}
	m, err := h.store.AddMessage(r.Context(), id, req.ParticipantID, req.Target, kind, req.Body)
	if errors.Is(err, ErrNotFound) {
		WriteError(w, http.StatusNotFound, "room or participant not found", "participant_id", "join the room before sending")
		return
	}
	if errors.Is(err, ErrRoomArchived) {
		WriteError(w, http.StatusConflict, "room is archived", "id", "the room is closed; messages can no longer be posted")
		return
	}
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to send message", "", "")
		return
	}
	scope := "global"
	if m.TargetID != nil {
		scope = "dm"
	}
	h.metrics.Messages.WithLabelValues(m.Kind, scope).Inc()
	WriteJSON(w, http.StatusCreated, m)
}

func (h *Handler) pollMessages(w http.ResponseWriter, r *http.Request) {
	id := validRoomID(w, r)
	if id == "" {
		return
	}
	participant := r.URL.Query().Get("participant")
	if _, err := uuid.Parse(participant); err != nil {
		WriteError(w, http.StatusBadRequest, "participant query parameter is required", "participant",
			"provide ?participant=<your participant id>")
		return
	}
	msgs, status, err := h.store.Poll(r.Context(), id, participant)
	if errors.Is(err, ErrNotFound) {
		WriteError(w, http.StatusNotFound, "room or participant not found", "participant", "join the room before polling")
		return
	}
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to poll messages", "", "")
		return
	}
	h.metrics.Polls.Inc()
	if msgs == nil {
		msgs = []Message{}
	}
	WriteJSON(w, http.StatusOK, map[string]any{"messages": msgs, "count": len(msgs), "room_status": status})
}

func (h *Handler) log(w http.ResponseWriter, r *http.Request) {
	id := validRoomID(w, r)
	if id == "" {
		return
	}
	msgs, err := h.store.Log(r.Context(), id)
	if errors.Is(err, ErrNotFound) {
		WriteError(w, http.StatusNotFound, "room not found", "id", "check the room id")
		return
	}
	if err != nil {
		WriteError(w, http.StatusInternalServerError, "failed to fetch log", "", "")
		return
	}
	if msgs == nil {
		msgs = []Message{}
	}
	WriteJSON(w, http.StatusOK, map[string]any{"messages": msgs, "count": len(msgs)})
}
```

- [ ] **Step 4: Run the full chat unit suite (no integration tag)**

Run: `cd ~/Documents/tatara/tatara-chat && go test ./internal/chat/`
Expected: PASS (all handler + filterVisible tests).

- [ ] **Step 5: Commit**

```bash
git add internal/chat/handler.go internal/chat/handler_test.go && git commit -q -m "feat: chat handler messages, poll, log endpoints"
```

---

## Task 13: TTL sweeper

**Files:**
- Create: `internal/chat/sweeper.go`
- Test: `internal/chat/sweeper_test.go`

- [ ] **Step 1: Write the failing test**

`internal/chat/sweeper_test.go`:
```go
package chat_test

import (
	"context"
	"sync/atomic"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-chat/internal/chat"
)

type fakeSweepStore struct{ calls atomic.Int32 }

func (f *fakeSweepStore) ArchiveExpired(_ context.Context, _ time.Duration) (int, error) {
	f.calls.Add(1)
	return 1, nil
}

func TestSweeperTicks(t *testing.T) {
	f := &fakeSweepStore{}
	sw := chat.NewSweeper(f, 24*time.Hour, 20*time.Millisecond, nil, chat.NewMetrics(prometheus.NewRegistry()))
	ctx, cancel := context.WithCancel(context.Background())
	sw.Start(ctx)
	require.Eventually(t, func() bool { return f.calls.Load() >= 2 }, time.Second, 5*time.Millisecond)
	cancel()
	sw.Wait()
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd ~/Documents/tatara/tatara-chat && go test ./internal/chat/ -run TestSweeperTicks`
Expected: FAIL (undefined: NewSweeper).

- [ ] **Step 3: Write `internal/chat/sweeper.go`**

```go
package chat

import (
	"context"
	"log/slog"
	"sync"
	"time"
)

// sweepStore is the persistence surface the sweeper needs.
type sweepStore interface {
	ArchiveExpired(ctx context.Context, ttl time.Duration) (int, error)
}

// Sweeper periodically archives rooms older than ttl.
type Sweeper struct {
	store    sweepStore
	ttl      time.Duration
	interval time.Duration
	log      *slog.Logger
	metrics  *Metrics
	wg       sync.WaitGroup
}

// NewSweeper creates a Sweeper. A nil logger falls back to slog.Default.
func NewSweeper(s sweepStore, ttl, interval time.Duration, log *slog.Logger, m *Metrics) *Sweeper {
	if log == nil {
		log = slog.Default()
	}
	return &Sweeper{store: s, ttl: ttl, interval: interval, log: log, metrics: m}
}

// Start runs the sweep loop until ctx is cancelled.
func (s *Sweeper) Start(ctx context.Context) {
	s.wg.Add(1)
	go func() {
		defer s.wg.Done()
		t := time.NewTicker(s.interval)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				n, err := s.store.ArchiveExpired(ctx, s.ttl)
				if err != nil {
					s.log.WarnContext(ctx, "chat: ttl sweep failed", "error", err)
					continue
				}
				if n > 0 {
					s.log.InfoContext(ctx, "chat: ttl sweep", "archived", n)
					if s.metrics != nil {
						s.metrics.Rooms.WithLabelValues("ttl_archived").Add(float64(n))
					}
				}
			}
		}
	}()
}

// Wait blocks until the sweep loop has exited.
func (s *Sweeper) Wait() { s.wg.Wait() }
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd ~/Documents/tatara/tatara-chat && go test ./internal/chat/ -run TestSweeperTicks`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/chat/sweeper.go internal/chat/sweeper_test.go && git commit -q -m "feat: chat TTL sweeper"
```

---

## Task 14: HTTP router and middleware

**Files:**
- Create: `internal/httpapi/middleware.go`
- Create: `internal/httpapi/router.go`
- Test: `internal/httpapi/router_test.go`

- [ ] **Step 1: Copy the generic middleware from tatara-memory**

```bash
cd ~/Documents/tatara/tatara-chat
mkdir -p internal/httpapi
cp ~/Documents/tatara/tatara-memory/internal/httpapi/middleware.go ./internal/httpapi/middleware.go
```

Edit the copied `middleware.go`: replace `github.com/szymonrychu/tatara-memory` -> `github.com/szymonrychu/tatara-chat`. Keep only the generic pieces it provides: `RequestID`, `Recover`, `AccessLog`, `RequestIDFromContext`, and `NewMetrics`/`Metrics.Middleware` (HTTP request duration/count). If the copied file imports memory-domain symbols, delete those imports and any handler-specific helpers; the file must compile against only chi + prometheus + slog. Verify by reading `~/Documents/tatara/tatara-memory/internal/httpapi/middleware.go` first.

- [ ] **Step 2: Write `internal/httpapi/router.go`**

```go
package httpapi

import (
	"context"
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// Config holds the router dependencies.
type Config struct {
	Chat       func(chi.Router) // chat.Handler.Routes
	Verify     func(http.Handler) http.Handler // auth middleware; nil disables auth (tests only)
	Logger     *slog.Logger
	Registry   *prometheus.Registry
	ReadyCheck func(context.Context) error
}

// NewRouter builds the chi router. Middleware order: request-id -> recover ->
// access-log -> http-metrics -> (auth on API routes). /healthz, /readyz, and
// /metrics are excluded from auth.
func NewRouter(cfg Config) *chi.Mux {
	if cfg.Logger == nil {
		cfg.Logger = slog.Default()
	}
	if cfg.Registry == nil {
		cfg.Registry = prometheus.NewRegistry()
	}
	metrics := NewMetrics(cfg.Registry)

	r := chi.NewRouter()
	r.Use(RequestID)
	r.Use(Recover)
	r.Use(AccessLog(cfg.Logger))
	r.Use(metrics.Middleware)

	r.Get("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	r.Get("/readyz", func(w http.ResponseWriter, req *http.Request) {
		if cfg.ReadyCheck != nil {
			if err := cfg.ReadyCheck(req.Context()); err != nil {
				w.WriteHeader(http.StatusServiceUnavailable)
				return
			}
		}
		w.WriteHeader(http.StatusOK)
	})
	r.Handle("/metrics", promhttp.HandlerFor(cfg.Registry, promhttp.HandlerOpts{}))

	r.Group(func(r chi.Router) {
		if cfg.Verify != nil {
			r.Use(cfg.Verify)
		}
		if cfg.Chat != nil {
			cfg.Chat(r)
		}
	})
	return r
}
```

- [ ] **Step 2b: Adjust the `NewMetrics` signature if needed**

If tatara-memory's `NewMetrics(reg *prometheus.Registry)` differs, match the copied middleware's actual signature. Read the copied file and align the call in `router.go`.

- [ ] **Step 3: Write the router test**

`internal/httpapi/router_test.go`:
```go
package httpapi_test

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/go-chi/chi/v5"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-chat/internal/httpapi"
)

func TestHealthzAndAuthGate(t *testing.T) {
	denyAll := func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusUnauthorized)
		})
	}
	r := httpapi.NewRouter(httpapi.Config{
		Verify:   denyAll,
		Registry: prometheus.NewRegistry(),
		Chat: func(rt chi.Router) {
			rt.Get("/rooms", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
		},
	})

	// healthz is open.
	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	require.Equal(t, http.StatusOK, w.Code)

	// chat routes are gated.
	w = httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/rooms", nil))
	require.Equal(t, http.StatusUnauthorized, w.Code)
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd ~/Documents/tatara/tatara-chat && go test ./internal/httpapi/`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/httpapi && git commit -q -m "feat: http router and middleware mounting chat under auth"
```

---

## Task 15: cmd wiring

**Files:**
- Create: `cmd/tatara-chat/{main.go,serve.go,config.go,health.go,app.go}`
- Test: `cmd/tatara-chat/config_test.go`

- [ ] **Step 1: Copy `main.go` and `serve.go` verbatim (with the two mechanical edits)**

```bash
cd ~/Documents/tatara/tatara-chat
mkdir -p cmd/tatara-chat
cp ~/Documents/tatara/tatara-memory/cmd/tatara-memory/main.go cmd/tatara-chat/main.go
cp ~/Documents/tatara/tatara-memory/cmd/tatara-memory/serve.go cmd/tatara-chat/serve.go
sed -i '' 's#github.com/szymonrychu/tatara-memory#github.com/szymonrychu/tatara-chat#g; s/tatara-memory/tatara-chat/g' cmd/tatara-chat/main.go cmd/tatara-chat/serve.go
```

`main.go` and `serve.go` contain no domain logic (run/waitForSignal/serve/newListener) and work unchanged.

- [ ] **Step 2: Write `cmd/tatara-chat/config.go`**

```go
package main

import (
	"flag"
	"fmt"
	"os"
	"strconv"
	"time"
)

type config struct {
	HTTPAddr      string
	PGDSN         string
	OIDCIssuer    string
	OIDCAudience  string
	RoomTTL       time.Duration
	SweepInterval time.Duration
	LogLevel      string
	OTLPEndpoint  string
}

func envOr(key, def string) string {
	if v, ok := os.LookupEnv(key); ok {
		return v
	}
	return def
}

func envDurOr(key string, def time.Duration) (time.Duration, error) {
	v, ok := os.LookupEnv(key)
	if !ok {
		return def, nil
	}
	d, err := time.ParseDuration(v)
	if err != nil {
		return 0, fmt.Errorf("env %s: %w", key, err)
	}
	return d, nil
}

func loadConfig(args []string) (config, error) {
	ttl, err := envDurOr("ROOM_TTL", 24*time.Hour)
	if err != nil {
		return config{}, err
	}
	sweep, err := envDurOr("SWEEP_INTERVAL", 5*time.Minute)
	if err != nil {
		return config{}, err
	}
	cfg := config{
		HTTPAddr:      envOr("HTTP_ADDR", ":8080"),
		PGDSN:         envOr("PG_DSN", ""),
		OIDCIssuer:    envOr("OIDC_ISSUER", "https://auth.szymonrichert.pl/realms/master"),
		OIDCAudience:  envOr("OIDC_AUDIENCE", "tatara-chat"),
		RoomTTL:       ttl,
		SweepInterval: sweep,
		LogLevel:      envOr("LOG_LEVEL", "info"),
		OTLPEndpoint:  envOr("OTLP_ENDPOINT", ""),
	}

	fs := flag.NewFlagSet("tatara-chat", flag.ContinueOnError)
	fs.StringVar(&cfg.HTTPAddr, "http-addr", cfg.HTTPAddr, "HTTP listen address")
	fs.StringVar(&cfg.PGDSN, "pg-dsn", cfg.PGDSN, "Postgres DSN")
	fs.StringVar(&cfg.OIDCIssuer, "oidc-issuer", cfg.OIDCIssuer, "OIDC issuer URL")
	fs.StringVar(&cfg.OIDCAudience, "oidc-audience", cfg.OIDCAudience, "OIDC audience")
	fs.DurationVar(&cfg.RoomTTL, "room-ttl", cfg.RoomTTL, "Room auto-archive age")
	fs.DurationVar(&cfg.SweepInterval, "sweep-interval", cfg.SweepInterval, "TTL sweep interval")
	fs.StringVar(&cfg.LogLevel, "log-level", cfg.LogLevel, "Log level (debug|info|warn|error)")
	fs.StringVar(&cfg.OTLPEndpoint, "otlp-endpoint", cfg.OTLPEndpoint, "OTLP endpoint (empty disables tracing)")
	if err := fs.Parse(args); err != nil {
		return config{}, err
	}
	_ = strconv.Atoi // keep imports stable if later needed; remove if unused
	return cfg, nil
}

func (c config) validate() error {
	if c.PGDSN == "" {
		return fmt.Errorf("pg-dsn is required")
	}
	if c.RoomTTL <= 0 {
		return fmt.Errorf("room-ttl must be > 0")
	}
	if c.SweepInterval <= 0 {
		return fmt.Errorf("sweep-interval must be > 0")
	}
	return nil
}
```

Remove the `_ = strconv.Atoi` line and the `strconv` import (it is not used); they are noted only to flag that the tatara-memory original imported strconv for int envs - chat uses durations instead.

- [ ] **Step 3: Write the config test**

`cmd/tatara-chat/config_test.go`:
```go
package main

import (
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestLoadConfigDefaults(t *testing.T) {
	cfg, err := loadConfig(nil)
	require.NoError(t, err)
	require.Equal(t, ":8080", cfg.HTTPAddr)
	require.Equal(t, "tatara-chat", cfg.OIDCAudience)
	require.Equal(t, 24*time.Hour, cfg.RoomTTL)
	require.Equal(t, 5*time.Minute, cfg.SweepInterval)
}

func TestValidateRequiresDSN(t *testing.T) {
	cfg, _ := loadConfig(nil)
	require.Error(t, cfg.validate())
	cfg.PGDSN = "postgres://x"
	require.NoError(t, cfg.validate())
}
```

- [ ] **Step 4: Write `cmd/tatara-chat/health.go`**

```go
package main

import (
	"context"
)

type pinger interface {
	PingContext(ctx context.Context) error
}

// readyzFunc returns a readiness check that pings the database.
func readyzFunc(db pinger) func(context.Context) error {
	return func(ctx context.Context) error {
		return db.PingContext(ctx)
	}
}
```

- [ ] **Step 5: Write `cmd/tatara-chat/app.go`**

```go
package main

import (
	"context"
	"database/sql"
	"log/slog"
	"net/http"
	"os"
	"time"

	"github.com/prometheus/client_golang/prometheus"

	"github.com/szymonrychu/tatara-chat/internal/auth"
	"github.com/szymonrychu/tatara-chat/internal/chat"
	"github.com/szymonrychu/tatara-chat/internal/httpapi"
	"github.com/szymonrychu/tatara-chat/internal/obs"

	_ "github.com/jackc/pgx/v5/stdlib"
)

type app struct {
	log     *slog.Logger
	reg     *prometheus.Registry
	db      *sql.DB
	sweeper *chat.Sweeper
	cancel  context.CancelFunc
	server  *http.Server
	stopOTL func(context.Context) error
}

func (a *app) shutdown(ctx context.Context) error {
	shutdownCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	if a.server != nil {
		_ = a.server.Shutdown(shutdownCtx)
	}
	if a.cancel != nil {
		a.cancel()
	}
	if a.sweeper != nil {
		a.sweeper.Wait()
	}
	if a.db != nil {
		_ = a.db.Close()
	}
	if a.stopOTL != nil {
		_ = a.stopOTL(shutdownCtx)
	}
	return nil
}

func buildObs(ctx context.Context, cfg config) (*slog.Logger, *prometheus.Registry, func(context.Context) error, error) {
	level := slog.LevelInfo
	switch cfg.LogLevel {
	case "debug":
		level = slog.LevelDebug
	case "warn":
		level = slog.LevelWarn
	case "error":
		level = slog.LevelError
	}
	logger := obs.NewLogger(os.Stdout, level)
	reg := obs.PromRegistry()
	_, stop, err := obs.TracerProvider(ctx, cfg.OTLPEndpoint, "tatara-chat")
	if err != nil {
		return nil, nil, nil, err
	}
	return logger, reg, stop, nil
}

func openDB(dsn string) (*sql.DB, error) {
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(10)
	db.SetMaxIdleConns(2)
	return db, nil
}

func newApp(ctx context.Context, cfg config) (*app, error) {
	logger, reg, stop, err := buildObs(ctx, cfg)
	if err != nil {
		return nil, err
	}

	db, err := openDB(cfg.PGDSN)
	if err != nil {
		return nil, err
	}
	if err := chat.Migrate(ctx, db); err != nil {
		return nil, err
	}

	metrics := chat.NewMetrics(reg)
	store := chat.NewStore(db)
	handler := chat.NewHandler(store, metrics)

	sweepCtx, cancel := context.WithCancel(context.Background())
	sweeper := chat.NewSweeper(store, cfg.RoomTTL, cfg.SweepInterval, logger, metrics)
	sweeper.Start(sweepCtx)

	verifier, err := auth.NewVerifier(ctx, auth.Config{Issuer: cfg.OIDCIssuer, Audience: cfg.OIDCAudience})
	if err != nil {
		cancel()
		return nil, err
	}

	router := httpapi.NewRouter(httpapi.Config{
		Chat:       handler.Routes,
		Verify:     auth.Middleware(verifier),
		Logger:     logger,
		Registry:   reg,
		ReadyCheck: readyzFunc(db),
	})

	srv := &http.Server{Addr: cfg.HTTPAddr, Handler: router, ReadHeaderTimeout: 10 * time.Second}

	return &app{log: logger, reg: reg, db: db, sweeper: sweeper, cancel: cancel, server: srv, stopOTL: stop}, nil
}
```

- [ ] **Step 6: Run, build, and test**

Run: `cd ~/Documents/tatara/tatara-chat && go build ./... && go test ./cmd/...`
Expected: builds clean; config tests PASS.

If `obs.TracerProvider`/`obs.NewLogger`/`obs.PromRegistry` signatures differ from those used here, read `~/Documents/tatara/tatara-memory/internal/obs/obs.go` and match exactly (these were copied verbatim in Task 2, so they should align).

- [ ] **Step 7: Commit**

```bash
git add cmd/tatara-chat && git commit -q -m "feat: cmd wiring (config, app, health, main)"
```

---

## Task 16: Full build, vet, lint, integration gate

**Files:** none (verification task)

- [ ] **Step 1: Tidy, build, vet**

Run:
```bash
cd ~/Documents/tatara/tatara-chat
go mod tidy
go build ./...
go vet ./...
```
Expected: all clean.

- [ ] **Step 2: Run unit tests (no DB)**

Run: `cd ~/Documents/tatara/tatara-chat && go test ./...`
Expected: PASS (integration-tagged tests are excluded without the tag).

- [ ] **Step 3: Run integration tests against a throwaway Postgres**

Run:
```bash
cd ~/Documents/tatara/tatara-chat
docker run --rm -d --name tatara-chat-pg -e POSTGRES_PASSWORD=postgres -p 5433:5432 postgres:16
export TATARA_TEST_PG_DSN='postgres://postgres:postgres@localhost:5433/postgres?sslmode=disable'
sleep 3
go test -tags integration ./internal/chat/ -v
docker rm -f tatara-chat-pg
```
Expected: all store/migrate integration tests PASS.

- [ ] **Step 4: golangci-lint (if installed; matches hard rule)**

Run: `cd ~/Documents/tatara/tatara-chat && golangci-lint run ./... || echo "install golangci-lint or run via mise"`
Expected: no findings. Fix any reported issues.

- [ ] **Step 5: Commit any fixes**

```bash
git add -A && git commit -q -m "chore: tidy, vet, lint pass" || echo "nothing to commit"
```

---

## Task 17: Helm chart (cluster-agnostic)

**Files:**
- Create: `charts/tatara-chat/...` via `helm create`, then edited.

- [ ] **Step 1: Scaffold the chart**

```bash
cd ~/Documents/tatara/tatara-chat
mkdir -p charts
helm create charts/tatara-chat
rm -rf charts/tatara-chat/templates/tests charts/tatara-chat/templates/hpa.yaml charts/tatara-chat/templates/serviceaccount.yaml
```

- [ ] **Step 2: Write `charts/tatara-chat/Chart.yaml`**

```yaml
apiVersion: v2
name: tatara-chat
description: Tatara chat service (agent-to-agent rooms, OIDC-gated, cnpg-backed)
type: application
version: 0.1.0
appVersion: "0.1.0"

dependencies:
  - name: cluster
    version: 0.6.1
    repository: https://cloudnative-pg.github.io/charts
    alias: postgres
    condition: postgres.enabled
```

Run `cd ~/Documents/tatara/tatara-chat && helm dependency update charts/tatara-chat` to vendor the cnpg subchart. Read `~/Documents/tatara/tatara-memory/charts/tatara-memory/values.yaml` for the exact cnpg `postgres:` value shape (cluster name, instances, storage, bootstrap database `tatara_chat`, owner/secret wiring) and mirror it, renaming the database to `tatara_chat`.

- [ ] **Step 3: Write `charts/tatara-chat/values.yaml` (cluster-agnostic, hard rules 6 + 14)**

Only camelCase scalars; no plaintext env, no lists, no cluster assumptions:
```yaml
image:
  repository: harbor.szymonrichert.pl/tatara/tatara-chat
  tag: ""           # defaults to .Chart.AppVersion
  pullPolicy: IfNotPresent

imagePullSecrets: []   # supplied by the infra helmfile (rule 14)

replicaCount: 1

service:
  type: ClusterIP
  port: 8080

# App config -> ConfigMap (kebab-case keys) -> envFrom.
config:
  httpAddr: ":8080"
  oidcIssuer: "https://auth.szymonrichert.pl/realms/master"
  oidcAudience: "tatara-chat"
  roomTtl: "24h"
  sweepInterval: "5m"
  logLevel: "info"
  otlpEndpoint: ""

# Secret values -> Secret (kebab-case keys) -> envFrom. Supplied by helmfile sops.
secret:
  pgDsn: ""

ingress:
  enabled: false   # host/class/path come from the infra helmfile, never here

resources: {}
nodeSelector: {}
tolerations: []
affinity: {}

postgres:
  enabled: true
  # cnpg cluster values mirrored from tatara-memory, database: tatara_chat
```

- [ ] **Step 4: Template the ConfigMap, Secret, Deployment, Service**

Write `charts/tatara-chat/templates/configmap.yaml` (kebab-case keys consumed via envFrom):
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "tatara-chat.fullname" . }}
  labels: {{- include "tatara-chat.labels" . | nindent 4 }}
data:
  http-addr: {{ .Values.config.httpAddr | quote }}
  oidc-issuer: {{ .Values.config.oidcIssuer | quote }}
  oidc-audience: {{ .Values.config.oidcAudience | quote }}
  room-ttl: {{ .Values.config.roomTtl | quote }}
  sweep-interval: {{ .Values.config.sweepInterval | quote }}
  log-level: {{ .Values.config.logLevel | quote }}
  otlp-endpoint: {{ .Values.config.otlpEndpoint | quote }}
```

The app reads UPPER_SNAKE env names (`HTTP_ADDR`, `OIDC_ISSUER`, `OIDC_AUDIENCE`, `ROOM_TTL`, `SWEEP_INTERVAL`, `LOG_LEVEL`, `OTLP_ENDPOINT`, `PG_DSN`). `envFrom` does NOT transform keys, so the ConfigMap/Secret keys must be the exact env names, not kebab-case. Resolve this mismatch by making the keys match the env vars:

```yaml
data:
  HTTP_ADDR: {{ .Values.config.httpAddr | quote }}
  OIDC_ISSUER: {{ .Values.config.oidcIssuer | quote }}
  OIDC_AUDIENCE: {{ .Values.config.oidcAudience | quote }}
  ROOM_TTL: {{ .Values.config.roomTtl | quote }}
  SWEEP_INTERVAL: {{ .Values.config.sweepInterval | quote }}
  LOG_LEVEL: {{ .Values.config.logLevel | quote }}
  OTLP_ENDPOINT: {{ .Values.config.otlpEndpoint | quote }}
```

IMPORTANT: read `~/Documents/tatara/tatara-memory/charts/tatara-memory/templates/configmap.yaml` and `secret.yaml` FIRST and mirror their exact key convention. tatara-memory already resolved the envFrom key question in production - copy whatever it does (it uses UPPER_SNAKE env names mapped via envFrom). Use the identical approach here; the "kebab-case" wording in hard rule 6 refers to ConfigMap key style generally, but envFrom requires keys to equal env names, and the canonical tatara-memory chart is the source of truth. Mirror it.

Write `charts/tatara-chat/templates/secret.yaml` similarly with `PG_DSN`, sourced from `.Values.secret.pgDsn`.

Edit `charts/tatara-chat/templates/deployment.yaml` to:
- use `envFrom` referencing both the ConfigMap and Secret,
- set container port 8080,
- liveness `GET /healthz`, readiness `GET /readyz`,
- honor `imagePullSecrets`, `resources`, `nodeSelector`, `tolerations`, `affinity` from values (default empty).

Mirror `~/Documents/tatara/tatara-memory/charts/tatara-memory/templates/deployment.yaml` exactly for the envFrom + probes pattern.

- [ ] **Step 5: Lint and template-render**

Run:
```bash
cd ~/Documents/tatara/tatara-chat
helm lint charts/tatara-chat
helm template t charts/tatara-chat --set postgres.enabled=false >/dev/null && echo "render ok"
```
Expected: lint passes (info-level only), render ok.

- [ ] **Step 6: Commit**

```bash
git add charts/tatara-chat && git commit -q -m "feat: cluster-agnostic helm chart with cnpg subchart"
```

---

## Task 18: Repo docs and publish

**Files:**
- Create: `README.md`
- Modify: `MEMORY.md`, `ROADMAP.md`

- [ ] **Step 1: Write a short `README.md`**

Describe the service in 15-25 lines: purpose (agent-to-agent chat per implementation stream), the room/participant/message model, the `(room_id, participant_id)` handle flow, the endpoint table, OIDC audience `tatara-chat`, 24h TTL, and that deploy lives in the infra helmfile. Link the spec path.

- [ ] **Step 2: Update `ROADMAP.md`** - mark v0.1.0 MVP `shipped` (code-complete on main), keep the follow-on list.

- [ ] **Step 3: Commit**

```bash
git add README.md MEMORY.md ROADMAP.md && git commit -q -m "docs: README, MEMORY, ROADMAP for v0.1.0"
```

- [ ] **Step 4: Create the GitHub repo and push (only when the user confirms)**

```bash
cd ~/Documents/tatara/tatara-chat
gh repo create szymonrychu/tatara-chat --private --source=. --remote=origin --push
```

Do NOT push without explicit user confirmation (per the user's git policy: commit/push only when asked).

---

## Follow-on work (separate plans, not in this plan)

These were enumerated in the spec and are intentionally out of this plan's scope. Each is its own brainstorming/plan cycle:

1. **tatara-cli `chat` MCP tool group.** Add a REST client + ~10 MCP tools (chat_room_create/list/get/close, chat_add_participant/remove_participant/roster, chat_send/poll/log) to `tatara-cli`, plus a `TATARA_CHAT_BASE_URL` config entry. Reference: `~/Documents/spellslinger/spellslinger-cli/internal/tools/chat.go`.
2. **Keycloak client.** Add a confidential `tatara-chat` client + audience-mapper entry to `~/Documents/infra/terraform/keycloak/tatara_clients.tf` so cli/service tokens carry `aud: tatara-chat`. `terraform plan`/`apply`.
3. **infra/helmfile release.** Add `tatara-chat` to `helmfiles/tatara/helmfile.yaml.gotmpl` with `values/tatara-chat/{common,default}.yaml` + sops `default.secrets.yaml` (PG_DSN), `regcred` from bucket common, ingress at `https://tatara.szymonrichert.pl/api/v1/chat` (nginx prefix-strip). MR off main.
4. **Image build + chart publish + deploy + smoke.** Build/push the container to Harbor, publish the chart, deploy via the helmfile MR, smoke-test the full flow with an OIDC token (create room -> add 2 participants -> cross-send -> poll -> archive -> verify 409 on archived write).

---

## Self-review notes (addressed)

- **Spec coverage:** rooms/participants/messages (Tasks 5-7, 10-12), UUID participant identity (Task 6 + handler), cursor poll + FOR UPDATE no-dup (Task 7), visibility filter (Task 3), TTL sweeper from created_at + system notice (Tasks 8, 13), nullable system author + CHECK (Task 4), OIDC audience `tatara-chat` (Tasks 2, 14, 15), `/healthz`,`/readyz`,`/metrics` un-gated (Task 14), domain metrics (Task 9), cluster-agnostic chart + cnpg (Task 17). Cross-repo deps deferred to the Follow-on section, matching the spec's split.
- **Type consistency:** store methods (`AddParticipant`, `GetParticipant`, `AddMessage(...,authorID,...)`, `Poll(...,participantID)`, `ArchiveExpired(ttl)`) match the `chatStore`/`sweepStore` interfaces and the handler call sites. `Message.AuthorID`/`TargetID` are `*string` throughout. `tx.Commit()`/`Rollback()` take no args under `database/sql` (flagged in Task 7).
- **Known follow-up nits to resolve during execution:** confirm tatara-memory's `internal/httpapi/middleware.go` exports exactly `RequestID`, `Recover`, `AccessLog`, `NewMetrics` (Task 14 Step 1 reads it first); confirm `obs` signatures (Task 15 Step 6); drop the unused `strconv` note in config.go.
```
