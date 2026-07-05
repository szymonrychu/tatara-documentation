# tatara-cli v0.1.0 MVP - design

Date: 2026-05-27
Status: Ready for plan
Author: Szymon (with Claude)

## Background

Phase 1 (`tatara-memory`) shipped at v0.1.2 with end-to-end smoke green
against real LightRAG v1.4.16. Phase 2 is the platform CLI:

- Authenticates a human (or service account) against Keycloak.
- Talks to `tatara-memory` over HTTPS.
- Exposes the same surface as a stdio MCP server so Claude Code (and
  later, any agentic worker) can consume the platform via MCP tool
  calls.

The architectural commitments from `~/Documents/tatara/ARCHITECTURE.md`
are honored: Go + cobra, OIDC device flow against the `tatara-cli`
public client, five subcommands (`login`, `logout`, `raw`, `mcp`,
`mcp-config`), Homebrew tap `szymonrychu/tap`, mirrors
`spellslinger-cli`.

## Prerequisite: tatara-memory v0.1.3

CLI MVP cannot ship until tatara-memory is reachable on a stable HTTPS
URL. v0.1.3 of tatara-memory delivers two changes:

### Route rename (breaking)

The httpapi router currently roots its surface at `/v1/*`. With the
platform versioning at `/api/v1/memory/...` in ingress, the
service-internal `/v1` prefix is redundant. Rename to:

| Old | New |
| --- | --- |
| `POST /v1/memories` | `POST /memories` |
| `GET /v1/memories/{id}` | `GET /memories/{id}` |
| `DELETE /v1/memories/{id}` | `DELETE /memories/{id}` |
| `POST /v1/memories:bulk` | `POST /memories:bulk` |
| `GET /v1/ingest-jobs/{id}` | `GET /ingest-jobs/{id}` |
| `POST /v1/queries` | `POST /queries` |
| `POST /v1/queries:describe` | `POST /queries:describe` |
| `GET /v1/entities/{id}` | `GET /entities/{id}` |
| `GET /v1/entities?q=...` | `GET /entities?q=...` |
| `PATCH /v1/entities/{id}` | `PATCH /entities/{id}` |
| `GET /v1/edges` | `GET /edges` |
| `POST /v1/edges` | `POST /edges` |
| `DELETE /v1/edges/{id}` | `DELETE /edges/{id}` |

Update httpapi router, e2e_test, all per-resource tests. `/healthz`,
`/readyz`, `/metrics` stay as-is (operator surface, not API surface).

### Ingress enablement

Enable the existing chart ingress at:

- host: `tatara.szymonrichert.pl`
- path: `/api/v1/memory` with prefix-strip rewrite to `/`
- TLS: `letsencrypt-prod` cluster issuer
- nginx ingress class

The ingress already exists in the chart but is disabled
(`ingress.enabled: false`). Override in `values/tatara-memory/default.yaml`
with the host + path values; chart template must support an
`ingress.path` value that defaults to `/`.

## tatara-cli MVP

### Goal

A single static Go binary `tatara` that:

1. Logs in via OIDC device flow against the `tatara-cli` public client.
2. Persists token + refresh token at
   `${XDG_CONFIG_HOME:-$HOME/.config}/tatara/token.json` (mode 0600).
3. Issues authenticated REST calls against tatara-memory via either
   `tatara raw <VERB> <PATH>` or the MCP server.
4. Drops a working `.mcp.json` into any project dir to register itself
   with Claude Code.

### Non-goals (v0.1.0)

- TUI / interactive prompts beyond what device flow requires.
- Multiple profiles (single token, single base URL).
- Caching, retries beyond a single HTTP retry, or offline mode.
- Table output. JSON in, JSON out.
- Per-user RBAC awareness. The CLI is a thin client; the service does
  the auth.

### Stack

- Go newest stable, pinned to exact minor in `go.mod`.
- `spf13/cobra` for command tree.
- `golang.org/x/oauth2` for device flow + refresh.
- `coreos/go-oidc/v3` for issuer discovery and token claim parsing.
- `mark3labs/mcp-go` for stdio MCP server (mirror what
  spellslinger-cli uses; revisit if a more canonical Go SDK lands).
- Stdlib `log/slog` JSON logs, same shape as tatara-memory.

### Repo layout

```
tatara-cli/
├── cmd/tatara/main.go
├── internal/
│   ├── auth/         # device flow, token store, refresh, lock
│   ├── client/       # HTTP client + bearer round-tripper, base URL resolution
│   ├── cmd/          # cobra: root, login, logout, raw, mcp, mcp-config
│   ├── config/       # XDG config dir, env + flag resolution
│   ├── mcp/          # tool registry, JSON-schema generation, server bootstrap
│   └── version/      # ldflags-populated build info
├── go.mod, go.sum
├── Dockerfile
├── Makefile
├── CLAUDE.md         # copy of the platform CLAUDE.md
├── MEMORY.md, ROADMAP.md, README.md, LICENSE
└── .github/workflows/
    ├── ci.yaml       # lint + test on every push
    └── release.yaml  # goreleaser + homebrew tap + container push
```

### Binary name

`tatara`. Short, platform-named, no `-cli` suffix in the binary itself.

### Subcommands

#### `tatara login`

Device authorization grant against the `tatara-cli` public client at
`https://auth.szymonrichert.pl/realms/master`:

1. POST `/realms/master/protocol/openid-connect/auth/device` with
   `client_id=tatara-cli` and `scope=tatara`.
2. Print `verification_uri_complete` (and `user_code` as fallback) to
   stderr.
3. Poll `/realms/master/protocol/openid-connect/token` with
   `grant_type=urn:ietf:params:oauth:grant-type:device_code` at the
   server-advertised interval until success or denial.
4. Persist `access_token`, `refresh_token`, `expires_at`, `id_token`
   (if present) to the token file. Truncate-and-write with file
   permissions enforced to 0600 (lock file used to prevent two parallel
   logins clobbering).
5. Exit 0 on success; non-zero on denial, slow_down deadline, or
   network error.

#### `tatara logout`

Deletes the token file. Exits 0 even if the file is already absent.

#### `tatara raw <VERB> <PATH> [-d BODY|@FILE]`

- Resolves the target URL as `<base-url><PATH>`.
- Loads token; refreshes via refresh token if `expires_at` is past or
  within 30s of now. On refresh failure, prints "not logged in".
- Sends the request with `Authorization: Bearer <token>` and `Accept:
  application/json`. If `-d` is given, sets
  `Content-Type: application/json` and the body.
- Prints `<status>` to stderr and the response body to stdout.
- Exit 0 if status is 2xx; non-zero otherwise.

`-d @file.json` reads body from disk; `-d -` reads from stdin.

#### `tatara mcp`

Stdio MCP server. On startup:

1. Loads token (refreshes if needed). Refuses to start if not logged
   in.
2. Registers thirteen tools (one per tatara-memory REST endpoint, see
   below). Tool name = snake_case of the operation; JSON schema
   derived from the `internal/memory.*` Go types (or hand-written if
   reflection isn't clean).
3. Reads MCP requests from stdin, writes responses to stdout. Errors
   go to a JSON log file under `${XDG_STATE_HOME:-$HOME/.local/state}/tatara/mcp.log`
   (stderr is reserved for MCP transport in some clients).
4. Each tool call translates one-for-one into a tatara-memory REST
   call. Schema validation happens server-side; the CLI passes JSON
   through.

Tool surface (full /v1/* mapping, no gates):

| Tool name | REST endpoint | Body / args |
| --- | --- | --- |
| `create_memory` | POST /memories | `{text, metadata?}` |
| `get_memory` | GET /memories/{id} | `id` |
| `delete_memory` | DELETE /memories/{id} | `id` |
| `bulk_create_memories` | POST /memories:bulk | `{items: [{text, metadata?}]}` |
| `get_ingest_job` | GET /ingest-jobs/{id} | `id` |
| `query` | POST /queries | `{mode, text, top_k?}` |
| `describe` | POST /queries:describe | `{mode, text, top_k?}` |
| `get_entity` | GET /entities/{id} | `id` |
| `search_entities` | GET /entities?q= | `q` |
| `patch_entity` | PATCH /entities/{id} | `id`, `{patch object}` |
| `list_edges` | GET /edges | none |
| `create_edge` | POST /edges | `{from_entity, to_entity, relation, properties?}` |
| `delete_edge` | DELETE /edges/{id} | `id` (composite "from||to") |

#### `tatara mcp-config <dir>`

Writes (or merges into) `<dir>/.mcp.json` so Claude Code in that dir
picks up the tatara MCP server. The entry looks like:

```json
{
  "mcpServers": {
    "tatara": {
      "command": "<absolute path to the tatara binary>",
      "args": ["mcp"]
    }
  }
}
```

If `<dir>/.mcp.json` already exists with other servers, merge under
`mcpServers.tatara`. Refuse to overwrite if `tatara` is already
present with a different command unless `--force` is passed.

### HTTP client and base URL

Resolution order (first non-empty wins):

1. `--base-url` flag.
2. `TATARA_MEMORY_URL` env var.
3. Config file value at `${XDG_CONFIG_HOME:-$HOME/.config}/tatara/config.yaml`
   (`baseUrl: ...`).
4. Default: `https://tatara.szymonrichert.pl/api/v1/memory`.

The HTTP client uses a custom `http.RoundTripper` that:

- Injects `Authorization: Bearer <token>` unless the request already
  carries an Authorization header.
- Refreshes the token if `expires_at` is within 30s and re-issues the
  request once with the new token. On refresh failure, surfaces a
  clear "not logged in" error.
- Adds `Accept: application/json` and, when a body is present and no
  Content-Type is set, `Content-Type: application/json`.
- Emits an INFO log per call with method, path, status, duration_ms,
  request_id (echoed from server response when present).

### Token storage

Single file `${XDG_CONFIG_HOME:-$HOME/.config}/tatara/token.json`,
mode 0600. Shape:

```json
{
  "access_token": "...",
  "refresh_token": "...",
  "id_token": "...",
  "expires_at": "2026-05-27T20:00:00Z",
  "token_type": "Bearer"
}
```

Reads use an exclusive file lock to serialise concurrent refreshes
from `tatara raw ... &; tatara raw ... &` patterns. Same approach as
spellslinger-cli (`internal/auth/lock.go`).

### Logging

- All CLI subcommands log to stderr at WARN by default; `-v` flips to
  INFO, `-vv` to DEBUG.
- The MCP subcommand logs to a file (see above) at INFO; stderr stays
  silent so it doesn't trip MCP transport edge cases.

### Release pipeline

- **CI** (`.github/workflows/ci.yaml`): on every push and PR, `go vet`,
  `golangci-lint`, `go test ./...`, `pre-commit run --all-files`.
- **Release** (`.github/workflows/release.yaml`): on tag push:
  - GoReleaser builds darwin/linux for amd64/arm64.
  - Pushes the formula to `szymonrychu/tap` (homebrew tap repo).
  - Builds and pushes the container image to
    `harbor.szymonrichert.pl/containers/tatara-cli:<version>` (for the
    phase 4 wrapper).
- Use the `make image` / `make push` Makefile pattern from
  tatara-memory; chart-related targets are absent (CLI has no chart).

### Testing

- `internal/auth`: device-flow handshake via stubbed HTTP server,
  token-file round-trip, refresh-on-expiry, file-lock contention.
- `internal/client`: round-tripper bearer injection, refresh trigger,
  base-URL resolution priority, request_id echo.
- `internal/cmd`: each cobra command's flag wiring, `mcp-config`
  merge/refuse behaviors against synthetic `.mcp.json` inputs.
- `internal/mcp`: tool registry maps cleanly to JSON schemas; a fake
  HTTP server stands in for tatara-memory and verifies one
  request/response per tool.
- Integration (build-tagged): real end-to-end against
  `https://tatara.szymonrichert.pl/api/v1/memory` with a
  client-credentials token; covers `tatara raw` for at least one
  memory round-trip.

## Out of scope for v0.1.0

- Multiple profiles / multiple base URLs.
- Table output, columns flag, pretty-printing tweaks.
- Caching, ETag/If-None-Match handling.
- Per-tool gating (`--allow-mutations` and friends): full MCP surface
  is exposed without gates per the brainstorming session.
- `tatara raw <VERB>` does not parse path placeholders. The user
  supplies the full path string.

## Open questions

- Container image base for `tatara-cli`: `gcr.io/distroless/static`
  (no shell, just the binary) versus a thin alpine for phase 4
  wrapper convenience. Default: distroless static. Confirm during
  plan.
- `mcp-config` should also drop a `~/.claude/mcp.json` user-level entry?
  Not in v0.1.0; per-project only.

## Deliverables for v0.1.0

1. `tatara-memory` v0.1.3 deployed: routes renamed, ingress enabled at
   `tatara.szymonrichert.pl/api/v1/memory`, smoke green via ingress.
2. `tatara-cli` v0.1.0 published: tagged Go binary on
   `github.com/szymonrychu/tatara-cli`, Homebrew formula on
   `szymonrychu/tap`, container image on Harbor.
3. `tatara mcp-config .` writes a working `.mcp.json` in the
   tatara-memory repo and Claude Code can call `create_memory`,
   `get_memory`, `query` against the homelab.
4. MEMORY.md / ROADMAP.md in both `tatara/` and `tatara-cli/` updated.
