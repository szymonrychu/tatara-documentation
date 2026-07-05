# tatara-cli v0.1.0 + tatara-memory v0.1.3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the tatara platform CLI: device-flow login, authenticated REST passthrough, and a stdio MCP server exposing all of tatara-memory to Claude Code. Land the prerequisite tatara-memory v0.1.3 (route rename + ingress) so the CLI has a stable HTTPS endpoint.

**Architecture:** Two repos. `tatara-memory` v0.1.3 renames `/v1/*` routes to root-level (`/memories`, `/entities`, etc.) and enables ingress at `tatara.szymonrichert.pl/api/v1/memory`. A new `tatara-cli` repo provides a single static Go binary `tatara` with cobra subcommands `login`/`logout`/`raw`/`mcp`/`mcp-config`. Token persisted at `$XDG_CONFIG_HOME/tatara/token.json`. MCP server registers 13 tools, one per tatara-memory REST endpoint.

**Tech Stack:** Go (newest stable), spf13/cobra, golang.org/x/oauth2, coreos/go-oidc/v3, mark3labs/mcp-go, stdlib log/slog. GoReleaser + Homebrew tap + Harbor container release pipeline.

**Specs:**
- This impl: `~/Documents/tatara/docs/superpowers/specs/2026-05-27-tatara-cli-mvp-design.md`
- Parent platform design: `~/Documents/tatara/docs/superpowers/specs/2026-05-24-tatara-phases-0-1-design.md`
- Reference CLI: `~/Documents/spellslinger/spellslinger-cli/` (mirror its layout and auth pattern)

---

## Wave 0 - tatara-memory v0.1.3 (prereq, sequential)

This wave lands on `~/Documents/tatara/tatara-memory/`. Develop in a worktree off main per CLAUDE.md.

### Task 0.1: Worktree + bump baseline

**Files:**
- Modify: `charts/tatara-memory/Chart.yaml`

- [ ] **Step 1:** Enter a worktree.

```bash
cd ~/Documents/tatara/tatara-memory
# Use EnterWorktree if available; otherwise:
git worktree add .claude/worktrees/v0.1.3-routes-and-ingress -b v0.1.3-routes-and-ingress
cd .claude/worktrees/v0.1.3-routes-and-ingress
```

- [ ] **Step 2:** Confirm clean baseline.

```bash
go test ./... 2>&1 | tail -10
```

Expected: all green (only `internal/version` shows `[no test files]`).

- [ ] **Step 3:** Bump chart version. Edit `charts/tatara-memory/Chart.yaml`:

```yaml
version: 0.1.3
appVersion: "0.1.3"
```

- [ ] **Step 4:** Commit baseline bump.

```bash
git add charts/tatara-memory/Chart.yaml
git commit -m "chore(chart): bump to 0.1.3"
```

### Task 0.2: Rename routes (TDD against router_test)

The current router lives in `internal/httpapi/router.go` and roots routes under `/v1/`. All tests in `internal/httpapi/*_test.go` expect `/v1/...` paths.

**Files:**
- Modify: `internal/httpapi/router.go`
- Modify: `internal/httpapi/auth_test.go`
- Modify: `internal/httpapi/e2e_test.go`
- Modify: `internal/httpapi/edges_test.go`
- Modify: `internal/httpapi/entities_test.go`
- Modify: `internal/httpapi/memories_test.go`
- Modify: `internal/httpapi/ingest_test.go`
- Modify: `internal/httpapi/middleware_test.go`
- Modify: `internal/httpapi/queries_test.go`

- [ ] **Step 1:** Read the router.

```bash
grep -n "/v1" internal/httpapi/router.go
```

- [ ] **Step 2:** Update all test files first. In each `*_test.go` under `internal/httpapi/`, replace `/v1/` with `/` in path strings. Examples:
  - `srv.URL + "/v1/memories/m1"` -> `srv.URL + "/memories/m1"`
  - `"POST", "/v1/memories"` -> `"POST", "/memories"`
  - `srv.URL + "/v1/memories:bulk"` -> `srv.URL + "/memories:bulk"`

Use sed for the mechanical change:

```bash
sed -i '' 's|/v1/|/|g' internal/httpapi/*_test.go
```

Then visually scan the diff:

```bash
git diff internal/httpapi/*_test.go | head -80
```

- [ ] **Step 3:** Run tests, expect failures (router still serves `/v1/*`).

```bash
go test ./internal/httpapi/... 2>&1 | tail -15
```

Expected: most tests fail with 404.

- [ ] **Step 4:** Update the router. In `internal/httpapi/router.go`, change the route registration. Look for the chi route grouping (typical pattern):

```go
r.Route("/v1", func(r chi.Router) { ... })
```

Replace with the body inlined at root, or with a no-prefix Route(""). Concretely the simplest change:

```go
// Before:
r.Route("/v1", func(v1 chi.Router) {
    v1.Post("/memories", ...)
    v1.Get("/memories/{id}", ...)
    ...
})

// After:
r.Post("/memories", ...)
r.Get("/memories/{id}", ...)
...
```

If the chi pattern uses `Route("/v1", ...)`, you can keep the structure with `Route("/", ...)` to retain grouping, or inline the routes directly. Pick whichever requires fewer changes to handler wiring.

`/healthz`, `/readyz`, `/metrics` stay at root unchanged.

- [ ] **Step 5:** Run tests, all pass.

```bash
go test ./internal/httpapi/... 2>&1 | tail -10
```

- [ ] **Step 6:** Run full suite to confirm nothing else broke.

```bash
go test ./... 2>&1 | tail -10
```

- [ ] **Step 7:** pre-commit + commit.

```bash
pre-commit run --all-files
git add internal/httpapi/
git commit -m "feat(httpapi)!: drop /v1 route prefix (path versioning at /api/v1/memory ingress)"
```

### Task 0.3: Ingress values: enable + path

**Files:**
- Modify: `charts/tatara-memory/templates/ingress.yaml`
- Modify: `charts/tatara-memory/values.yaml`
- Modify: `charts/tatara-memory/tests/ingress_test.yaml`
- Modify: `values/tatara-memory/default.yaml`

- [ ] **Step 1:** Read the existing ingress template + tests:

```bash
cat charts/tatara-memory/templates/ingress.yaml
cat charts/tatara-memory/tests/ingress_test.yaml
```

- [ ] **Step 2:** Add `ingress.path` to chart values with default `/`. In `charts/tatara-memory/values.yaml`, locate the ingress block and add the `path` field:

```yaml
ingress:
  enabled: false
  className: nginx
  host: ""
  path: "/"
  clusterIssuer: letsencrypt-prod
  tlsSecretName: ""
```

- [ ] **Step 3:** Add an ingress nginx rewrite annotation in `charts/tatara-memory/templates/ingress.yaml`. The rewrite strips the configured `path` prefix so backend services see `/`-rooted requests.

```yaml
{{- if .Values.ingress.enabled }}
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ include "tatara-memory.fullname" . }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "tatara-memory.labels" . | nindent 4 }}
  annotations:
    cert-manager.io/cluster-issuer: {{ .Values.ingress.clusterIssuer }}
    {{- if ne .Values.ingress.path "/" }}
    nginx.ingress.kubernetes.io/rewrite-target: /$2
    {{- end }}
spec:
  ingressClassName: {{ .Values.ingress.className }}
  tls:
    - hosts: [{{ .Values.ingress.host | quote }}]
      secretName: {{ default (printf "%s-tls" (include "tatara-memory.fullname" .)) .Values.ingress.tlsSecretName }}
  rules:
    - host: {{ .Values.ingress.host | quote }}
      http:
        paths:
          - path: {{ if ne .Values.ingress.path "/" }}{{ .Values.ingress.path }}(/|$)(.*){{ else }}/{{ end }}
            pathType: {{ if ne .Values.ingress.path "/" }}ImplementationSpecific{{ else }}Prefix{{ end }}
            backend:
              service:
                name: {{ include "tatara-memory.fullname" . }}
                port:
                  number: {{ .Values.service.port }}
{{- end }}
```

Adapt against the current template's exact shape; preserve any existing fields.

- [ ] **Step 4:** Update helm-unittest tests in `charts/tatara-memory/tests/ingress_test.yaml` to cover the new path field. Add a test case:

```yaml
- it: applies nginx rewrite when path is not "/"
  set:
    ingress.enabled: true
    ingress.host: tatara.szymonrichert.pl
    ingress.path: /api/v1/memory
  asserts:
    - equal:
        path: metadata.annotations["nginx.ingress.kubernetes.io/rewrite-target"]
        value: /$2
    - equal:
        path: spec.rules[0].http.paths[0].path
        value: /api/v1/memory(/|$)(.*)
```

- [ ] **Step 5:** Render-test the template:

```bash
cd ~/Documents/tatara/tatara-memory/.claude/worktrees/v0.1.3-routes-and-ingress
/opt/homebrew/bin/helm unittest charts/tatara-memory --file tests/ingress_test.yaml 2>&1 | tail -10
```

Expected: PASS.

- [ ] **Step 6:** Enable ingress in the env values. Edit `values/tatara-memory/default.yaml`:

```yaml
ingress:
  enabled: true
  host: tatara.szymonrichert.pl
  path: /api/v1/memory
```

- [ ] **Step 7:** Verify chart render against the env values:

```bash
helmfile template 2>&1 | grep -A20 "kind: Ingress" | head -30
```

Expected: ingress manifest with host `tatara.szymonrichert.pl`, path `/api/v1/memory(/|$)(.*)`, rewrite-target `/$2`.

- [ ] **Step 8:** pre-commit + commit.

```bash
pre-commit run --all-files
git add charts/tatara-memory/ values/tatara-memory/default.yaml
git commit -m "feat(chart): ingress path support; enable at tatara.szymonrichert.pl/api/v1/memory"
```

### Task 0.4: Merge worktree to main, tag, build, deploy

- [ ] **Step 1:** Switch to main + merge.

```bash
cd ~/Documents/tatara/tatara-memory
git checkout main
go test ./... 2>&1 | tail -5  # baseline still green from previous v0.1.2
git merge --no-ff v0.1.3-routes-and-ingress -m "merge: v0.1.3 route rename + ingress"
go test ./... 2>&1 | tail -5
```

- [ ] **Step 2:** Cleanup worktree.

```bash
git worktree remove .claude/worktrees/v0.1.3-routes-and-ingress
git branch -d v0.1.3-routes-and-ingress
```

- [ ] **Step 3:** Tag + push.

```bash
git tag -a v0.1.3 -m "v0.1.3 - route rename + ingress enabled"
git push origin main v0.1.3
```

- [ ] **Step 4:** Build + push image (remember to also push without `v` prefix per MEMORY.md note).

```bash
make push  # builds and pushes vX.Y.Z
docker tag harbor.szymonrichert.pl/containers/tatara-memory:v0.1.3 harbor.szymonrichert.pl/containers/tatara-memory:0.1.3
docker push harbor.szymonrichert.pl/containers/tatara-memory:0.1.3
```

- [ ] **Step 5:** Diff + apply.

```bash
helmfile diff 2>&1 | tail -40
helmfile sync --args '--force-conflicts --timeout=10m' 2>&1 | tail -10
```

- [ ] **Step 6:** Verify ingress is serving and routes work end-to-end. After DNS / cert propagate (cert-manager usually under 30s after first request):

```bash
curl -sv https://tatara.szymonrichert.pl/healthz 2>&1 | tail -5
```

Expected: HTTP 200, valid TLS cert.

Then mint a token and smoke the renamed routes:

```bash
TOKEN=$(curl -s -X POST "https://auth.szymonrichert.pl/realms/master/protocol/openid-connect/token" \
  -d "client_id=tatara-memory" \
  -d "client_secret=$(cd ~/Documents/infra/terraform/keycloak && terraform output -raw tatara_memory_client_secret)" \
  -d "grant_type=client_credentials" -d "scope=tatara" | jq -r .access_token)

# Create
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text":"v0.1.3 smoke test"}' \
  https://tatara.szymonrichert.pl/api/v1/memory/memories

# Query
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"mode":"hybrid","text":"smoke"}' \
  https://tatara.szymonrichert.pl/api/v1/memory/queries
```

Expected: both return JSON 200.

- [ ] **Step 7:** Update tatara-memory `MEMORY.md` + `ROADMAP.md` to reflect v0.1.3 ship; commit + push on main.

```bash
git add MEMORY.md ROADMAP.md
git commit -m "docs: v0.1.3 shipped (routes flattened, ingress live)"
git push origin main
```

Wave 0 complete. tatara-memory is now reachable at `https://tatara.szymonrichert.pl/api/v1/memory` with routes at `/memories`, `/queries`, etc.

---

## Wave 1 - tatara-cli repo bootstrap (sequential)

### Task 1.1: Create GitHub repo + clone

- [ ] **Step 1:** Create the GitHub repo (private acceptable; flip to public when v0.1.0 ships).

```bash
gh repo create szymonrychu/tatara-cli --public --description "Tatara platform CLI (OIDC device flow, REST passthrough, MCP server)"
```

- [ ] **Step 2:** Clone into the parent tatara workspace per the on-disk layout:

```bash
cd ~/Documents/tatara
git clone git@github.com:szymonrychu/tatara-cli.git
cd tatara-cli
```

- [ ] **Step 3:** Confirm parent `.gitignore` already excludes `tatara-cli/`:

```bash
grep "/tatara-cli/" ~/Documents/tatara/.gitignore
```

Expected: match found.

### Task 1.2: Scaffold repo files

**Files:**
- Create: `LICENSE`, `README.md`, `CLAUDE.md`, `MEMORY.md`, `ROADMAP.md`, `.gitignore`
- Create: `go.mod`, `Dockerfile`, `Makefile`, `.golangci.yml`, `.pre-commit-config.yaml`, `.editorconfig`
- Create: `cmd/tatara/main.go`
- Create: `internal/version/version.go`

- [ ] **Step 1:** Copy LICENSE from tatara-memory:

```bash
cp ~/Documents/tatara/tatara-memory/LICENSE .
```

- [ ] **Step 2:** Copy CLAUDE.md from the parent tatara repo (the platform contract is identical):

```bash
cp ~/Documents/tatara/CLAUDE.md .
```

- [ ] **Step 3:** Create `.gitignore`:

```
# Build output
/bin/
/dist/

# Worktree scratch
.claude/worktrees/
.worktrees/

# OS / editor noise
.DS_Store
.idea/
.vscode/
*.swp
*~

# Local scratch
/tmp/
/scratch/
```

- [ ] **Step 4:** Initialise the Go module:

```bash
go mod init github.com/szymonrychu/tatara-cli
```

Verify go.mod uses the newest stable Go (`go 1.25` or current).

- [ ] **Step 5:** Copy and adapt `Makefile` from tatara-memory. Remove chart-related targets (`chart-lint`, `chart-test`, `chart-push`); keep `tidy`, `fmt`, `lint`, `test`, `build`, `image`, `push`, `clean`, `all`. Update `IMAGE_NAME` to `containers/tatara-cli`.

- [ ] **Step 6:** Copy `.pre-commit-config.yaml`, `.golangci.yml`, `.editorconfig` from tatara-memory. They work as-is.

- [ ] **Step 7:** Copy `Dockerfile` from tatara-memory and rename binary to `tatara`:

```dockerfile
# syntax=docker/dockerfile:1.7
FROM golang:1.25-alpine AS build
ARG VERSION=dev
ARG COMMIT=unknown
ARG DATE=unknown
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build \
    -trimpath \
    -ldflags "-s -w \
      -X github.com/szymonrychu/tatara-cli/internal/version.Version=${VERSION} \
      -X github.com/szymonrychu/tatara-cli/internal/version.Commit=${COMMIT} \
      -X github.com/szymonrychu/tatara-cli/internal/version.Date=${DATE}" \
    -o /out/tatara ./cmd/tatara

FROM gcr.io/distroless/static:nonroot
COPY --from=build /out/tatara /tatara
ENTRYPOINT ["/tatara"]
```

- [ ] **Step 8:** Create `internal/version/version.go`:

```go
// Package version holds build-time version info populated via ldflags.
package version

var (
    Version = "dev"
    Commit  = "unknown"
    Date    = "unknown"
)
```

- [ ] **Step 9:** Create `cmd/tatara/main.go` placeholder so `go build` succeeds:

```go
package main

import (
    "fmt"
    "os"

    "github.com/szymonrychu/tatara-cli/internal/version"
)

func main() {
    fmt.Printf("tatara %s (%s) built %s\n", version.Version, version.Commit, version.Date)
    os.Exit(0)
}
```

- [ ] **Step 10:** Create minimal `README.md`, `MEMORY.md` (with the format from the parent), `ROADMAP.md` (with phase 2 in progress).

- [ ] **Step 11:** Verify build + lint.

```bash
go build ./...
pre-commit install
pre-commit run --all-files
```

- [ ] **Step 12:** Commit baseline.

```bash
git add .
git commit -m "feat: repo bootstrap (Makefile, Dockerfile, CLAUDE.md, scaffold)"
git push origin main
```

### Task 1.3: GitHub Actions CI

**Files:**
- Create: `.github/workflows/ci.yaml`

- [ ] **Step 1:** Create CI workflow:

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:

jobs:
  lint-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version-file: go.mod
          cache: true
      - run: go vet ./...
      - uses: golangci/golangci-lint-action@v6
        with:
          version: latest
      - run: go test -race -count=1 ./...
```

- [ ] **Step 2:** Commit + verify CI runs green.

```bash
git add .github/workflows/ci.yaml
git commit -m "ci: lint + test on push and PR"
git push origin main
gh run watch
```

Expected: green.

---

## Wave 2 - Core packages (parallelizable)

Three independent packages. A subagent can take each.

### Task 2A: internal/auth (device flow + token store)

**Files:**
- Create: `internal/auth/token.go`
- Create: `internal/auth/token_test.go`
- Create: `internal/auth/lock.go`
- Create: `internal/auth/device_flow.go`
- Create: `internal/auth/device_flow_test.go`
- Create: `internal/auth/refresh.go`
- Create: `internal/auth/refresh_test.go`

Mirror `~/Documents/spellslinger/spellslinger-cli/internal/auth/`. Read those files first; adapt the issuer URL, client_id, and scope to tatara.

- [ ] **Step 1: Read the reference implementation**

```bash
cat ~/Documents/spellslinger/spellslinger-cli/internal/auth/token.go
cat ~/Documents/spellslinger/spellslinger-cli/internal/auth/lock.go
cat ~/Documents/spellslinger/spellslinger-cli/internal/auth/device_flow.go
cat ~/Documents/spellslinger/spellslinger-cli/internal/auth/refresh.go
```

- [ ] **Step 2: Token type + on-disk format**

Write `internal/auth/token.go` with the `Token` struct and `LoadToken`/`SaveToken` against `${XDG_CONFIG_HOME:-$HOME/.config}/tatara/token.json`. File mode 0600. Use `os.OpenFile` with `O_CREATE|O_WRONLY|O_TRUNC` and `0o600`.

```go
package auth

import (
    "encoding/json"
    "errors"
    "fmt"
    "os"
    "path/filepath"
    "time"
)

type Token struct {
    AccessToken  string    `json:"access_token"`
    RefreshToken string    `json:"refresh_token"`
    IDToken      string    `json:"id_token,omitempty"`
    ExpiresAt    time.Time `json:"expires_at"`
    TokenType    string    `json:"token_type"`
}

var ErrNoToken = errors.New("auth: no token (run `tatara login`)")

func DefaultTokenPath() (string, error) {
    dir := os.Getenv("XDG_CONFIG_HOME")
    if dir == "" {
        h, err := os.UserHomeDir()
        if err != nil {
            return "", fmt.Errorf("auth: resolve home: %w", err)
        }
        dir = filepath.Join(h, ".config")
    }
    return filepath.Join(dir, "tatara", "token.json"), nil
}

func LoadToken(path string) (*Token, error) {
    b, err := os.ReadFile(path)
    if errors.Is(err, os.ErrNotExist) {
        return nil, ErrNoToken
    }
    if err != nil {
        return nil, fmt.Errorf("auth: read token: %w", err)
    }
    var t Token
    if err := json.Unmarshal(b, &t); err != nil {
        return nil, fmt.Errorf("auth: parse token: %w", err)
    }
    return &t, nil
}

func SaveToken(path string, t *Token) error {
    if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
        return fmt.Errorf("auth: mkdir: %w", err)
    }
    b, err := json.MarshalIndent(t, "", "  ")
    if err != nil {
        return fmt.Errorf("auth: marshal token: %w", err)
    }
    f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
    if err != nil {
        return fmt.Errorf("auth: open token file: %w", err)
    }
    defer f.Close()
    if _, err := f.Write(b); err != nil {
        return fmt.Errorf("auth: write token: %w", err)
    }
    return nil
}

func DeleteToken(path string) error {
    err := os.Remove(path)
    if errors.Is(err, os.ErrNotExist) {
        return nil
    }
    return err
}
```

- [ ] **Step 3: Token round-trip test (failing)**

Write `internal/auth/token_test.go`:

```go
package auth_test

import (
    "os"
    "path/filepath"
    "testing"
    "time"

    "github.com/stretchr/testify/require"

    "github.com/szymonrychu/tatara-cli/internal/auth"
)

func TestTokenRoundTrip(t *testing.T) {
    dir := t.TempDir()
    path := filepath.Join(dir, "token.json")

    in := &auth.Token{
        AccessToken:  "at",
        RefreshToken: "rt",
        ExpiresAt:    time.Now().Add(time.Hour).UTC().Truncate(time.Second),
        TokenType:    "Bearer",
    }
    require.NoError(t, auth.SaveToken(path, in))

    info, err := os.Stat(path)
    require.NoError(t, err)
    require.Equal(t, os.FileMode(0o600), info.Mode().Perm())

    got, err := auth.LoadToken(path)
    require.NoError(t, err)
    require.Equal(t, in.AccessToken, got.AccessToken)
    require.True(t, in.ExpiresAt.Equal(got.ExpiresAt))
}

func TestLoadTokenMissing(t *testing.T) {
    _, err := auth.LoadToken(filepath.Join(t.TempDir(), "absent.json"))
    require.ErrorIs(t, err, auth.ErrNoToken)
}

func TestDefaultTokenPathHonorsXDG(t *testing.T) {
    t.Setenv("XDG_CONFIG_HOME", "/tmp/xdg-test")
    p, err := auth.DefaultTokenPath()
    require.NoError(t, err)
    require.Equal(t, "/tmp/xdg-test/tatara/token.json", p)
}
```

Run, expect PASS (token.go already done).

```bash
go test ./internal/auth/...
```

- [ ] **Step 4: File lock (lock.go + test)**

Write `internal/auth/lock.go` using `syscall.Flock` (mirror spellslinger). Test concurrent SaveToken calls.

```go
package auth

import (
    "fmt"
    "os"
    "syscall"
)

type FileLock struct{ f *os.File }

func AcquireLock(path string) (*FileLock, error) {
    f, err := os.OpenFile(path+".lock", os.O_CREATE|os.O_RDWR, 0o600)
    if err != nil {
        return nil, fmt.Errorf("auth: open lock: %w", err)
    }
    if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX); err != nil {
        _ = f.Close()
        return nil, fmt.Errorf("auth: flock: %w", err)
    }
    return &FileLock{f: f}, nil
}

func (l *FileLock) Release() error {
    if err := syscall.Flock(int(l.f.Fd()), syscall.LOCK_UN); err != nil {
        return err
    }
    return l.f.Close()
}
```

Add a serial-Save smoke test in `internal/auth/lock_test.go`. Run + PASS.

- [ ] **Step 5: Device flow (failing test)**

`internal/auth/device_flow.go`. The OAuth2 device-authorization grant. The Keycloak endpoints are:

- Device auth: `POST https://auth.szymonrichert.pl/realms/master/protocol/openid-connect/auth/device`
- Token: `POST https://auth.szymonrichert.pl/realms/master/protocol/openid-connect/token`

Public client `tatara-cli`, scope `tatara`.

```go
package auth

import (
    "context"
    "encoding/json"
    "errors"
    "fmt"
    "io"
    "net/http"
    "net/url"
    "strings"
    "time"
)

type DeviceCode struct {
    DeviceCode              string `json:"device_code"`
    UserCode                string `json:"user_code"`
    VerificationURI         string `json:"verification_uri"`
    VerificationURIComplete string `json:"verification_uri_complete"`
    ExpiresIn               int    `json:"expires_in"`
    Interval                int    `json:"interval"`
}

type DeviceFlow struct {
    HTTP          *http.Client
    Issuer        string
    ClientID      string
    Scope         string
}

func (d *DeviceFlow) Start(ctx context.Context) (*DeviceCode, error) {
    body := url.Values{}
    body.Set("client_id", d.ClientID)
    body.Set("scope", d.Scope)
    req, err := http.NewRequestWithContext(ctx, http.MethodPost,
        d.Issuer+"/protocol/openid-connect/auth/device", strings.NewReader(body.Encode()))
    if err != nil {
        return nil, err
    }
    req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
    req.Header.Set("Accept", "application/json")
    resp, err := d.client().Do(req)
    if err != nil {
        return nil, fmt.Errorf("auth: device start: %w", err)
    }
    defer resp.Body.Close()
    if resp.StatusCode != http.StatusOK {
        b, _ := io.ReadAll(resp.Body)
        return nil, fmt.Errorf("auth: device start %d: %s", resp.StatusCode, string(b))
    }
    var dc DeviceCode
    if err := json.NewDecoder(resp.Body).Decode(&dc); err != nil {
        return nil, fmt.Errorf("auth: parse device code: %w", err)
    }
    return &dc, nil
}

func (d *DeviceFlow) Poll(ctx context.Context, code *DeviceCode) (*Token, error) {
    interval := time.Duration(code.Interval) * time.Second
    if interval == 0 {
        interval = 5 * time.Second
    }
    deadline := time.Now().Add(time.Duration(code.ExpiresIn) * time.Second)
    for {
        select {
        case <-ctx.Done():
            return nil, ctx.Err()
        default:
        }
        tok, err := d.exchange(ctx, code.DeviceCode)
        if err == nil {
            return tok, nil
        }
        if errors.Is(err, ErrAuthPending) {
            // wait and retry
        } else if errors.Is(err, ErrSlowDown) {
            interval += 5 * time.Second
        } else {
            return nil, err
        }
        if time.Now().After(deadline) {
            return nil, fmt.Errorf("auth: device code expired")
        }
        select {
        case <-ctx.Done():
            return nil, ctx.Err()
        case <-time.After(interval):
        }
    }
}

var (
    ErrAuthPending = errors.New("authorization_pending")
    ErrSlowDown    = errors.New("slow_down")
    ErrAccessDenied = errors.New("access_denied")
)

func (d *DeviceFlow) exchange(ctx context.Context, deviceCode string) (*Token, error) {
    body := url.Values{}
    body.Set("grant_type", "urn:ietf:params:oauth:grant-type:device_code")
    body.Set("device_code", deviceCode)
    body.Set("client_id", d.ClientID)
    req, _ := http.NewRequestWithContext(ctx, http.MethodPost,
        d.Issuer+"/protocol/openid-connect/token", strings.NewReader(body.Encode()))
    req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
    resp, err := d.client().Do(req)
    if err != nil {
        return nil, fmt.Errorf("auth: token exchange: %w", err)
    }
    defer resp.Body.Close()
    raw, _ := io.ReadAll(resp.Body)
    if resp.StatusCode == http.StatusOK {
        var tr struct {
            AccessToken  string `json:"access_token"`
            RefreshToken string `json:"refresh_token"`
            IDToken      string `json:"id_token"`
            ExpiresIn    int    `json:"expires_in"`
            TokenType    string `json:"token_type"`
        }
        if err := json.Unmarshal(raw, &tr); err != nil {
            return nil, fmt.Errorf("auth: parse token: %w", err)
        }
        return &Token{
            AccessToken:  tr.AccessToken,
            RefreshToken: tr.RefreshToken,
            IDToken:      tr.IDToken,
            ExpiresAt:    time.Now().Add(time.Duration(tr.ExpiresIn) * time.Second),
            TokenType:    tr.TokenType,
        }, nil
    }
    var e struct {
        Error string `json:"error"`
    }
    _ = json.Unmarshal(raw, &e)
    switch e.Error {
    case "authorization_pending":
        return nil, ErrAuthPending
    case "slow_down":
        return nil, ErrSlowDown
    case "access_denied":
        return nil, ErrAccessDenied
    default:
        return nil, fmt.Errorf("auth: %s: %s", e.Error, string(raw))
    }
}

func (d *DeviceFlow) client() *http.Client {
    if d.HTTP != nil {
        return d.HTTP
    }
    return &http.Client{Timeout: 30 * time.Second}
}
```

- [ ] **Step 6: Device flow test (httptest)**

`internal/auth/device_flow_test.go`. Run a fake server that returns 200 device-code on `/auth/device`, then returns `authorization_pending` once, then a token success. Assert the polling logic exchanges within deadline. Refer to spellslinger's `device_flow_test.go` for the canned-response pattern.

Tests must cover:
- Happy path (device-code -> pending -> token)
- `slow_down` extends interval
- `access_denied` propagates as `ErrAccessDenied`
- Context cancellation aborts

Run + PASS.

- [ ] **Step 7: Refresh (refresh.go + test)**

`internal/auth/refresh.go`. Public signature must be:

```go
func RefreshToken(ctx context.Context, issuer, clientID string, t *Token) (*Token, error)
```

Body: POST `{issuer}/protocol/openid-connect/token` with form:
- `grant_type=refresh_token`
- `client_id={clientID}`
- `refresh_token={t.RefreshToken}`

Parse the same response shape as the device-flow `exchange` function (access_token, refresh_token, id_token, expires_in, token_type). Return a fresh `*Token`. Refresh-window policy lives in `client.Client.ensureFresh` (see Task 2B); this function only performs the grant.

Test: `internal/auth/refresh_test.go` uses an httptest server that:
- Asserts the form payload contains the right grant_type and refresh_token.
- Returns a new access_token + new refresh_token + expires_in=300.
- The test asserts the returned `*Token` has ExpiresAt within ~300s of now and RefreshToken is the rotated value.

- [ ] **Step 8: Commit**

```bash
go test ./internal/auth/... -race
pre-commit run --all-files
git add internal/auth
git commit -m "feat(auth): OIDC device flow, token store with XDG path, refresh"
git push origin main
```

### Task 2B: internal/client (HTTP client + round-tripper)

**Files:**
- Create: `internal/client/client.go`
- Create: `internal/client/client_test.go`
- Create: `internal/client/config.go`
- Create: `internal/client/config_test.go`

- [ ] **Step 1: Config (base URL resolution)**

`internal/client/config.go`:

```go
package client

import (
    "fmt"
    "os"
    "path/filepath"

    "gopkg.in/yaml.v3"
)

const DefaultBaseURL = "https://tatara.szymonrichert.pl/api/v1/memory"

type FileConfig struct {
    BaseURL string `yaml:"baseUrl"`
    Issuer  string `yaml:"issuer"`
}

// ResolveBaseURL returns the first non-empty value of: flag, env, file, default.
func ResolveBaseURL(flag, env string, file *FileConfig) string {
    if flag != "" {
        return flag
    }
    if env != "" {
        return env
    }
    if file != nil && file.BaseURL != "" {
        return file.BaseURL
    }
    return DefaultBaseURL
}

func DefaultConfigPath() (string, error) {
    dir := os.Getenv("XDG_CONFIG_HOME")
    if dir == "" {
        h, err := os.UserHomeDir()
        if err != nil {
            return "", fmt.Errorf("client: home: %w", err)
        }
        dir = filepath.Join(h, ".config")
    }
    return filepath.Join(dir, "tatara", "config.yaml"), nil
}

func LoadConfig(path string) (*FileConfig, error) {
    b, err := os.ReadFile(path)
    if os.IsNotExist(err) {
        return &FileConfig{}, nil
    }
    if err != nil {
        return nil, err
    }
    var c FileConfig
    if err := yaml.Unmarshal(b, &c); err != nil {
        return nil, err
    }
    return &c, nil
}
```

Test resolution precedence (flag > env > file > default) in `config_test.go`.

- [ ] **Step 2: Client (round-tripper)**

`internal/client/client.go`:

```go
package client

import (
    "bytes"
    "context"
    "encoding/json"
    "errors"
    "fmt"
    "io"
    "net/http"
    "time"

    "github.com/szymonrychu/tatara-cli/internal/auth"
)

type RefreshFunc func(ctx context.Context, t *auth.Token) (*auth.Token, error)

type Client struct {
    base    string
    http    *http.Client
    token   *auth.Token
    refresh RefreshFunc
    save    func(*auth.Token) error
}

type Config struct {
    BaseURL string
    Token   *auth.Token
    Refresh RefreshFunc
    Save    func(*auth.Token) error
    HTTP    *http.Client
}

func New(cfg Config) (*Client, error) {
    if cfg.BaseURL == "" {
        return nil, errors.New("client: BaseURL required")
    }
    h := cfg.HTTP
    if h == nil {
        h = &http.Client{Timeout: 60 * time.Second}
    }
    return &Client{
        base:    cfg.BaseURL,
        http:    h,
        token:   cfg.Token,
        refresh: cfg.Refresh,
        save:    cfg.Save,
    }, nil
}

// Do issues an authenticated request. If body is non-nil, it must be JSON-encodable.
func (c *Client) Do(ctx context.Context, method, path string, body any) (*http.Response, error) {
    if err := c.ensureFresh(ctx); err != nil {
        return nil, err
    }
    var bodyReader io.Reader
    if body != nil {
        switch b := body.(type) {
        case []byte:
            bodyReader = bytes.NewReader(b)
        case io.Reader:
            bodyReader = b
        default:
            buf := &bytes.Buffer{}
            if err := json.NewEncoder(buf).Encode(body); err != nil {
                return nil, fmt.Errorf("client: encode body: %w", err)
            }
            bodyReader = buf
        }
    }
    req, err := http.NewRequestWithContext(ctx, method, c.base+path, bodyReader)
    if err != nil {
        return nil, fmt.Errorf("client: build request: %w", err)
    }
    req.Header.Set("Accept", "application/json")
    if bodyReader != nil {
        req.Header.Set("Content-Type", "application/json")
    }
    if c.token != nil {
        req.Header.Set("Authorization", "Bearer "+c.token.AccessToken)
    }
    return c.http.Do(req)
}

func (c *Client) ensureFresh(ctx context.Context) error {
    if c.token == nil {
        return auth.ErrNoToken
    }
    if time.Until(c.token.ExpiresAt) > 30*time.Second {
        return nil
    }
    if c.refresh == nil {
        return nil
    }
    nt, err := c.refresh(ctx, c.token)
    if err != nil {
        return fmt.Errorf("client: refresh: %w", err)
    }
    c.token = nt
    if c.save != nil {
        return c.save(nt)
    }
    return nil
}
```

- [ ] **Step 3: Client tests**

`internal/client/client_test.go`: httptest server scenarios:

- Bearer header present
- Accept header always set
- Content-Type set when body is non-nil
- Refresh triggers on near-expiry token; new token saved
- `ErrNoToken` when no token loaded

Run + PASS.

- [ ] **Step 4: Commit**

```bash
go test ./internal/client/... -race
pre-commit run --all-files
git add internal/client
git commit -m "feat(client): HTTP client with bearer + auto-refresh, XDG config"
git push origin main
```

### Task 2C: internal/mcp (tool registry, schemas, server)

**Files:**
- Create: `internal/mcp/tools.go`
- Create: `internal/mcp/tools_test.go`
- Create: `internal/mcp/server.go`
- Create: `internal/mcp/server_test.go`

- [ ] **Step 1: Add mcp-go dependency**

```bash
go get github.com/mark3labs/mcp-go@latest
```

(If a more canonical Go SDK emerges, swap here. Spellslinger uses this one as of 2026-05.)

- [ ] **Step 2: Tool registry (tools.go)**

Declare one record per tatara-memory REST endpoint. Each entry carries: tool name, HTTP method, path template, JSON schema for arguments, optional response transform.

```go
package mcp

import (
    "context"
    "encoding/json"
    "fmt"
    "io"
    "net/http"
    "strings"

    "github.com/szymonrychu/tatara-cli/internal/client"
)

type Tool struct {
    Name        string
    Description string
    Schema      json.RawMessage // JSON schema for arguments
    Build       func(args map[string]any) (method, path string, body any, err error)
}

func AllTools() []Tool {
    return []Tool{
        {
            Name:        "create_memory",
            Description: "Insert a new text memory. Returns the track_id.",
            Schema:      json.RawMessage(`{"type":"object","properties":{"text":{"type":"string"},"metadata":{"type":"object","additionalProperties":{"type":"string"}}},"required":["text"]}`),
            Build: func(a map[string]any) (string, string, any, error) {
                return http.MethodPost, "/memories", a, nil
            },
        },
        {
            Name:        "get_memory",
            Description: "Retrieve a memory by track_id.",
            Schema:      json.RawMessage(`{"type":"object","properties":{"id":{"type":"string"}},"required":["id"]}`),
            Build: func(a map[string]any) (string, string, any, error) {
                id, _ := a["id"].(string)
                if id == "" {
                    return "", "", nil, fmt.Errorf("id required")
                }
                return http.MethodGet, "/memories/" + id, nil, nil
            },
        },
        {
            Name:        "delete_memory",
            Description: "Delete a memory by track_id.",
            Schema:      json.RawMessage(`{"type":"object","properties":{"id":{"type":"string"}},"required":["id"]}`),
            Build: func(a map[string]any) (string, string, any, error) {
                id, _ := a["id"].(string)
                if id == "" {
                    return "", "", nil, fmt.Errorf("id required")
                }
                return http.MethodDelete, "/memories/" + id, nil, nil
            },
        },
        {
            Name:        "bulk_create_memories",
            Description: "Submit a batch of memories for async ingest.",
            Schema:      json.RawMessage(`{"type":"object","properties":{"items":{"type":"array","items":{"type":"object","properties":{"text":{"type":"string"},"metadata":{"type":"object"}},"required":["text"]}}},"required":["items"]}`),
            Build: func(a map[string]any) (string, string, any, error) {
                return http.MethodPost, "/memories:bulk", a, nil
            },
        },
        {
            Name:        "get_ingest_job",
            Description: "Poll the status of a bulk ingest job.",
            Schema:      json.RawMessage(`{"type":"object","properties":{"id":{"type":"string"}},"required":["id"]}`),
            Build: func(a map[string]any) (string, string, any, error) {
                id, _ := a["id"].(string)
                return http.MethodGet, "/ingest-jobs/" + id, nil, nil
            },
        },
        {
            Name:        "query",
            Description: "Retrieve memory references for the given query.",
            Schema:      json.RawMessage(`{"type":"object","properties":{"mode":{"type":"string","enum":["local","global","hybrid","naive","mix","bypass"]},"text":{"type":"string"},"top_k":{"type":"integer"}},"required":["mode","text"]}`),
            Build: func(a map[string]any) (string, string, any, error) {
                return http.MethodPost, "/queries", a, nil
            },
        },
        {
            Name:        "describe",
            Description: "Generative answer plus source paths for the given query.",
            Schema:      json.RawMessage(`{"type":"object","properties":{"mode":{"type":"string"},"text":{"type":"string"},"top_k":{"type":"integer"}},"required":["mode","text"]}`),
            Build: func(a map[string]any) (string, string, any, error) {
                return http.MethodPost, "/queries:describe", a, nil
            },
        },
        {
            Name:        "get_entity",
            Description: "Retrieve an entity by name.",
            Schema:      json.RawMessage(`{"type":"object","properties":{"id":{"type":"string"}},"required":["id"]}`),
            Build: func(a map[string]any) (string, string, any, error) {
                id, _ := a["id"].(string)
                return http.MethodGet, "/entities/" + id, nil, nil
            },
        },
        {
            Name:        "search_entities",
            Description: "Search entities by query string.",
            Schema:      json.RawMessage(`{"type":"object","properties":{"q":{"type":"string"}}}`),
            Build: func(a map[string]any) (string, string, any, error) {
                q, _ := a["q"].(string)
                path := "/entities"
                if q != "" {
                    path += "?q=" + q
                }
                return http.MethodGet, path, nil, nil
            },
        },
        {
            Name:        "patch_entity",
            Description: "Apply a partial update to an entity.",
            Schema:      json.RawMessage(`{"type":"object","properties":{"id":{"type":"string"},"patch":{"type":"object"}},"required":["id","patch"]}`),
            Build: func(a map[string]any) (string, string, any, error) {
                id, _ := a["id"].(string)
                patch := a["patch"]
                return http.MethodPatch, "/entities/" + id, patch, nil
            },
        },
        {
            Name:        "list_edges",
            Description: "List all edges in the knowledge graph.",
            Schema:      json.RawMessage(`{"type":"object","properties":{}}`),
            Build: func(a map[string]any) (string, string, any, error) {
                return http.MethodGet, "/edges", nil, nil
            },
        },
        {
            Name:        "create_edge",
            Description: "Create a new edge between two existing entities.",
            Schema:      json.RawMessage(`{"type":"object","properties":{"from_entity":{"type":"string"},"to_entity":{"type":"string"},"relation":{"type":"string"},"properties":{"type":"object"}},"required":["from_entity","to_entity","relation"]}`),
            Build: func(a map[string]any) (string, string, any, error) {
                return http.MethodPost, "/edges", a, nil
            },
        },
        {
            Name:        "delete_edge",
            Description: "Delete an edge by composite ID 'from||to'.",
            Schema:      json.RawMessage(`{"type":"object","properties":{"id":{"type":"string"}},"required":["id"]}`),
            Build: func(a map[string]any) (string, string, any, error) {
                id, _ := a["id"].(string)
                return http.MethodDelete, "/edges/" + id, nil, nil
            },
        },
    }
}

// Invoke runs the tool against the given client and returns the response body
// as raw JSON. Status >= 400 is surfaced as an error.
func Invoke(ctx context.Context, c *client.Client, t Tool, args map[string]any) ([]byte, error) {
    method, path, body, err := t.Build(args)
    if err != nil {
        return nil, err
    }
    resp, err := c.Do(ctx, method, path, body)
    if err != nil {
        return nil, err
    }
    defer resp.Body.Close()
    buf, _ := io.ReadAll(resp.Body)
    if resp.StatusCode >= 400 {
        return nil, fmt.Errorf("tatara: %s %s -> %d: %s", method, path, resp.StatusCode, strings.TrimSpace(string(buf)))
    }
    return buf, nil
}
```

- [ ] **Step 3: Tool registry tests**

`internal/mcp/tools_test.go`:

```go
package mcp_test

import (
    "context"
    "encoding/json"
    "net/http"
    "net/http/httptest"
    "testing"
    "time"

    "github.com/stretchr/testify/require"

    "github.com/szymonrychu/tatara-cli/internal/auth"
    "github.com/szymonrychu/tatara-cli/internal/client"
    "github.com/szymonrychu/tatara-cli/internal/mcp"
)

func TestAllTools_ThirteenEntries(t *testing.T) {
    require.Len(t, mcp.AllTools(), 13)
}

func TestAllTools_SchemasAreValidJSON(t *testing.T) {
    for _, tool := range mcp.AllTools() {
        var x any
        require.NoError(t, json.Unmarshal(tool.Schema, &x), "tool %s schema invalid", tool.Name)
    }
}

func TestInvoke_CreateMemoryPostsJSON(t *testing.T) {
    var got struct {
        Method string
        Path   string
        Body   map[string]any
    }
    srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        got.Method = r.Method
        got.Path = r.URL.Path
        require.NoError(t, json.NewDecoder(r.Body).Decode(&got.Body))
        _, _ = w.Write([]byte(`{"id":"track-1","text":"x"}`))
    }))
    defer srv.Close()

    tok := &auth.Token{AccessToken: "t", ExpiresAt: time.Now().Add(time.Hour)}
    c, err := client.New(client.Config{BaseURL: srv.URL, Token: tok})
    require.NoError(t, err)

    tools := map[string]mcp.Tool{}
    for _, t := range mcp.AllTools() {
        tools[t.Name] = t
    }
    out, err := mcp.Invoke(context.Background(), c, tools["create_memory"], map[string]any{"text": "x"})
    require.NoError(t, err)
    require.Contains(t, string(out), "track-1")

    require.Equal(t, http.MethodPost, got.Method)
    require.Equal(t, "/memories", got.Path)
    require.Equal(t, "x", got.Body["text"])
}
```

Add similar shape-tests for `get_memory`, `query`, `delete_edge` (composite ID path), `search_entities` (q querystring). Skip the rest — coverage is the table-driven tools above.

Run + PASS.

- [ ] **Step 4: Server (server.go)**

Wrap mcp-go to register every tool. Server reads stdin / writes stdout per MCP transport.

```go
package mcp

import (
    "context"
    "encoding/json"
    "log/slog"

    "github.com/mark3labs/mcp-go/mcp"
    "github.com/mark3labs/mcp-go/server"

    "github.com/szymonrychu/tatara-cli/internal/client"
    "github.com/szymonrychu/tatara-cli/internal/version"
)

type Server struct {
    srv    *server.MCPServer
    client *client.Client
    log    *slog.Logger
}

func NewServer(c *client.Client, log *slog.Logger) *Server {
    s := &Server{
        srv:    server.NewMCPServer("tatara", version.Version),
        client: c,
        log:    log,
    }
    for _, t := range AllTools() {
        s.register(t)
    }
    return s
}

func (s *Server) register(t Tool) {
    tool := mcp.NewTool(
        t.Name,
        mcp.WithDescription(t.Description),
        mcp.WithRawInputSchema(t.Schema),
    )
    s.srv.AddTool(tool, func(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
        args, _ := req.Params.Arguments.(map[string]any)
        body, err := Invoke(ctx, s.client, t, args)
        if err != nil {
            return mcp.NewToolResultError(err.Error()), nil
        }
        var out any
        if err := json.Unmarshal(body, &out); err != nil {
            return mcp.NewToolResultText(string(body)), nil
        }
        pretty, _ := json.MarshalIndent(out, "", "  ")
        return mcp.NewToolResultText(string(pretty)), nil
    })
}

func (s *Server) Run(ctx context.Context) error {
    return server.ServeStdio(s.srv)
}
```

Adjust to the actual mark3labs/mcp-go API surface (the example above is illustrative; the real call shapes may differ). Reference: spellslinger-cli's `internal/cmd/mcp.go`.

- [ ] **Step 5: Server smoke test**

`internal/mcp/server_test.go`: Construct a NewServer with a fake client (in-memory `httptest`), and just verify NewServer doesn't panic and registers 13 tools.

If mcp-go offers a programmatic test harness, use it; otherwise skip end-to-end stdio testing and rely on tools_test.go.

- [ ] **Step 6: Commit**

```bash
go test ./internal/mcp/... -race
pre-commit run --all-files
git add internal/mcp go.mod go.sum
git commit -m "feat(mcp): 13-tool registry mapping tatara-memory REST, stdio server"
git push origin main
```

---

## Wave 3 - cobra root + login/logout/raw (depends on 2A, 2B)

### Task 3.1: Root command + persistent flags

**Files:**
- Create: `internal/cmd/root.go`
- Create: `internal/cmd/root_test.go`
- Modify: `cmd/tatara/main.go`

- [ ] **Step 1: Read spellslinger's root for shape**

```bash
cat ~/Documents/spellslinger/spellslinger-cli/internal/cmd/root.go
```

- [ ] **Step 2: Write root.go**

```go
package cmd

import (
    "github.com/spf13/cobra"

    "github.com/szymonrychu/tatara-cli/internal/version"
)

const (
    DefaultIssuer   = "https://auth.szymonrichert.pl/realms/master"
    DefaultClientID = "tatara-cli"
    DefaultScope    = "tatara"
)

func NewRootCmd() *cobra.Command {
    root := &cobra.Command{
        Use:           "tatara",
        Short:         "tatara platform CLI",
        Version:       version.Version,
        SilenceUsage:  true,
        SilenceErrors: true,
    }
    root.PersistentFlags().String("base-url", "", "tatara-memory base URL (overrides TATARA_MEMORY_URL and config file)")
    root.PersistentFlags().CountP("verbose", "v", "increase log verbosity (-v info, -vv debug)")

    root.AddCommand(
        newLoginCmd(),
        newLogoutCmd(),
        newRawCmd(),
        newMCPCmd(),
        newMCPConfigCmd(),
    )
    return root
}
```

- [ ] **Step 3: Update main.go**

```go
package main

import (
    "fmt"
    "os"

    "github.com/szymonrychu/tatara-cli/internal/cmd"
)

func main() {
    if err := cmd.NewRootCmd().Execute(); err != nil {
        fmt.Fprintf(os.Stderr, "Error: %s\n", err)
        os.Exit(1)
    }
}
```

- [ ] **Step 4: Stub the subcommand constructors so the build compiles**

```go
// in internal/cmd/stubs.go (delete file when each command lands)
package cmd
import "github.com/spf13/cobra"
func newLoginCmd() *cobra.Command     { return &cobra.Command{Use: "login"} }
func newLogoutCmd() *cobra.Command    { return &cobra.Command{Use: "logout"} }
func newRawCmd() *cobra.Command       { return &cobra.Command{Use: "raw"} }
func newMCPCmd() *cobra.Command       { return &cobra.Command{Use: "mcp"} }
func newMCPConfigCmd() *cobra.Command { return &cobra.Command{Use: "mcp-config"} }
```

- [ ] **Step 5: Build + smoke**

```bash
go build ./...
./tatara --help  # should show all 5 subcommands
```

- [ ] **Step 6: Commit**

```bash
git add internal/cmd cmd/tatara/main.go
git commit -m "feat(cmd): root cobra command + subcommand stubs"
git push origin main
```

### Task 3.2: login + logout

**Files:**
- Create: `internal/cmd/login.go`
- Create: `internal/cmd/login_test.go`
- Create: `internal/cmd/logout.go`
- Create: `internal/cmd/logout_test.go`
- Modify (delete): `internal/cmd/stubs.go` (drop login + logout stubs)

- [ ] **Step 1: login.go**

```go
package cmd

import (
    "context"
    "fmt"
    "os"

    "github.com/spf13/cobra"

    "github.com/szymonrychu/tatara-cli/internal/auth"
)

func newLoginCmd() *cobra.Command {
    return &cobra.Command{
        Use:   "login",
        Short: "Authenticate against Keycloak via OIDC device flow.",
        RunE: func(cmd *cobra.Command, _ []string) error {
            ctx := cmd.Context()
            path, err := auth.DefaultTokenPath()
            if err != nil {
                return err
            }
            flow := &auth.DeviceFlow{
                Issuer:   DefaultIssuer,
                ClientID: DefaultClientID,
                Scope:    DefaultScope,
            }
            code, err := flow.Start(ctx)
            if err != nil {
                return err
            }
            fmt.Fprintf(os.Stderr, "Open this URL in your browser to authorize:\n  %s\n\nUser code: %s\nWaiting for authorization...\n", code.VerificationURIComplete, code.UserCode)
            token, err := flow.Poll(ctx, code)
            if err != nil {
                return err
            }
            if err := auth.SaveToken(path, token); err != nil {
                return err
            }
            fmt.Fprintf(os.Stderr, "Logged in. Token saved to %s\n", path)
            _ = ctx
            return nil
        },
    }
}

var _ = context.Background
```

- [ ] **Step 2: login_test.go**

Inject a fake `Issuer` and stub Start/Poll via httptest server, asserting token persisted.

Skip if injection requires substantial plumbing — for v0.1.0, smoke via integration is acceptable. Always test logout though.

- [ ] **Step 3: logout.go**

```go
package cmd

import (
    "fmt"

    "github.com/spf13/cobra"

    "github.com/szymonrychu/tatara-cli/internal/auth"
)

func newLogoutCmd() *cobra.Command {
    return &cobra.Command{
        Use:   "logout",
        Short: "Forget the stored OIDC token.",
        RunE: func(cmd *cobra.Command, _ []string) error {
            path, err := auth.DefaultTokenPath()
            if err != nil {
                return err
            }
            if err := auth.DeleteToken(path); err != nil {
                return err
            }
            fmt.Fprintln(cmd.ErrOrStderr(), "Logged out.")
            return nil
        },
    }
}
```

- [ ] **Step 4: logout_test.go**

Use `t.Setenv("XDG_CONFIG_HOME", t.TempDir())`. Save a token, run the command, assert the file is gone, run it again, assert no error.

- [ ] **Step 5: Drop the login/logout entries from `internal/cmd/stubs.go`. Build + test + commit.**

```bash
go test ./... -race
pre-commit run --all-files
git add internal/cmd
git commit -m "feat(cmd): login (device flow) + logout"
git push origin main
```

### Task 3.3: raw command

**Files:**
- Create: `internal/cmd/raw.go`
- Create: `internal/cmd/raw_test.go`
- Modify: `internal/cmd/stubs.go` (drop raw)

- [ ] **Step 1: raw.go**

```go
package cmd

import (
    "context"
    "fmt"
    "io"
    "net/http"
    "os"
    "strings"

    "github.com/spf13/cobra"

    "github.com/szymonrychu/tatara-cli/internal/auth"
    "github.com/szymonrychu/tatara-cli/internal/client"
)

func newRawCmd() *cobra.Command {
    var dataFlag string
    cmd := &cobra.Command{
        Use:   "raw VERB PATH",
        Short: "Authenticated REST passthrough to tatara-memory.",
        Args:  cobra.ExactArgs(2),
        RunE: func(cmd *cobra.Command, args []string) error {
            verb := strings.ToUpper(args[0])
            path := args[1]
            ctx := cmd.Context()

            // Resolve base URL.
            baseFlag, _ := cmd.Flags().GetString("base-url")
            configPath, err := client.DefaultConfigPath()
            if err != nil {
                return err
            }
            fileCfg, err := client.LoadConfig(configPath)
            if err != nil {
                return err
            }
            base := client.ResolveBaseURL(baseFlag, os.Getenv("TATARA_MEMORY_URL"), fileCfg)

            // Load token.
            tokenPath, err := auth.DefaultTokenPath()
            if err != nil {
                return err
            }
            token, err := auth.LoadToken(tokenPath)
            if err != nil {
                return err
            }

            // Build body.
            var body io.Reader
            switch {
            case dataFlag == "":
                body = nil
            case dataFlag == "-":
                body = cmd.InOrStdin()
            case strings.HasPrefix(dataFlag, "@"):
                f, err := os.Open(dataFlag[1:])
                if err != nil {
                    return err
                }
                defer f.Close()
                body = f
            default:
                body = strings.NewReader(dataFlag)
            }

            refresh := func(ctx context.Context, t *auth.Token) (*auth.Token, error) {
                return auth.RefreshToken(ctx, DefaultIssuer, DefaultClientID, t)
            }
            cli, err := client.New(client.Config{
                BaseURL: base,
                Token:   token,
                Refresh: refresh,
                Save: func(t *auth.Token) error {
                    return auth.SaveToken(tokenPath, t)
                },
            })
            if err != nil {
                return err
            }

            resp, err := cli.Do(ctx, verb, path, body)
            if err != nil {
                return err
            }
            defer resp.Body.Close()
            fmt.Fprintln(cmd.ErrOrStderr(), resp.Status)
            if _, err := io.Copy(cmd.OutOrStdout(), resp.Body); err != nil {
                return err
            }
            if resp.StatusCode >= http.StatusBadRequest {
                return fmt.Errorf("HTTP %d", resp.StatusCode)
            }
            return nil
        },
    }
    cmd.Flags().StringVarP(&dataFlag, "data", "d", "", "Request body (literal JSON, @file, or - for stdin)")
    return cmd
}
```

(`auth.RefreshToken` is the function written in Wave 2A's refresh.go.)

- [ ] **Step 2: raw_test.go**

httptest server + temp XDG_CONFIG_HOME with a saved token; invoke the command via cobra in-process, assert it prints status and body.

- [ ] **Step 3: Drop raw from stubs. Build + test + commit.**

```bash
go test ./... -race
pre-commit run --all-files
git add internal/cmd
git commit -m "feat(cmd): raw passthrough with -d body support"
git push origin main
```

---

## Wave 4 - mcp + mcp-config (depends on 2A, 2B, 2C)

### Task 4.1: mcp command

**Files:**
- Create: `internal/cmd/mcp.go`
- Create: `internal/cmd/mcp_test.go`
- Modify: `internal/cmd/stubs.go` (drop mcp)

- [ ] **Step 1: mcp.go**

```go
package cmd

import (
    "context"
    "fmt"
    "io"
    "log/slog"
    "os"
    "path/filepath"

    "github.com/spf13/cobra"

    "github.com/szymonrychu/tatara-cli/internal/auth"
    "github.com/szymonrychu/tatara-cli/internal/client"
    "github.com/szymonrychu/tatara-cli/internal/mcp"
)

func newMCPCmd() *cobra.Command {
    return &cobra.Command{
        Use:   "mcp",
        Short: "Run the tatara MCP server over stdio.",
        RunE: func(cmd *cobra.Command, _ []string) error {
            ctx := cmd.Context()
            logger, closer, err := mcpLogger()
            if err != nil {
                return err
            }
            defer closer.Close()

            baseFlag, _ := cmd.Flags().GetString("base-url")
            configPath, err := client.DefaultConfigPath()
            if err != nil {
                return err
            }
            fileCfg, err := client.LoadConfig(configPath)
            if err != nil {
                return err
            }
            base := client.ResolveBaseURL(baseFlag, os.Getenv("TATARA_MEMORY_URL"), fileCfg)

            tokenPath, err := auth.DefaultTokenPath()
            if err != nil {
                return err
            }
            token, err := auth.LoadToken(tokenPath)
            if err != nil {
                return err
            }

            refresh := func(ctx context.Context, t *auth.Token) (*auth.Token, error) {
                return auth.RefreshToken(ctx, DefaultIssuer, DefaultClientID, t)
            }
            cli, err := client.New(client.Config{
                BaseURL: base,
                Token:   token,
                Refresh: refresh,
                Save:    func(t *auth.Token) error { return auth.SaveToken(tokenPath, t) },
            })
            if err != nil {
                return err
            }
            srv := mcp.NewServer(cli, logger)
            return srv.Run(ctx)
        },
    }
}

func mcpLogger() (*slog.Logger, io.Closer, error) {
    dir := os.Getenv("XDG_STATE_HOME")
    if dir == "" {
        h, err := os.UserHomeDir()
        if err != nil {
            return nil, nil, err
        }
        dir = filepath.Join(h, ".local", "state")
    }
    if err := os.MkdirAll(filepath.Join(dir, "tatara"), 0o700); err != nil {
        return nil, nil, err
    }
    f, err := os.OpenFile(filepath.Join(dir, "tatara", "mcp.log"),
        os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
    if err != nil {
        return nil, nil, fmt.Errorf("mcp: open log: %w", err)
    }
    return slog.New(slog.NewJSONHandler(f, &slog.HandlerOptions{Level: slog.LevelInfo})), f, nil
}
```

- [ ] **Step 2: mcp_test.go**

Skip stdio harness for v0.1.0. Just assert `newMCPCmd().RunE` returns `ErrNoToken` when XDG_CONFIG_HOME is empty.

- [ ] **Step 3: Drop mcp from stubs. Build + test + commit.**

### Task 4.2: mcp-config command

**Files:**
- Create: `internal/cmd/mcp_config.go`
- Create: `internal/cmd/mcp_config_test.go`
- Delete: `internal/cmd/stubs.go` (last stub gone)

- [ ] **Step 1: mcp_config.go**

```go
package cmd

import (
    "encoding/json"
    "fmt"
    "os"
    "path/filepath"

    "github.com/spf13/cobra"
)

func newMCPConfigCmd() *cobra.Command {
    var force bool
    cmd := &cobra.Command{
        Use:   "mcp-config DIR",
        Short: "Write or merge a .mcp.json entry registering tatara in DIR.",
        Args:  cobra.ExactArgs(1),
        RunE: func(cmd *cobra.Command, args []string) error {
            dir := args[0]
            bin, err := os.Executable()
            if err != nil {
                return err
            }
            abs, err := filepath.Abs(bin)
            if err != nil {
                return err
            }

            path := filepath.Join(dir, ".mcp.json")
            cfg := map[string]any{}
            if b, err := os.ReadFile(path); err == nil {
                if err := json.Unmarshal(b, &cfg); err != nil {
                    return fmt.Errorf("mcp-config: parse existing %s: %w", path, err)
                }
            }
            servers, _ := cfg["mcpServers"].(map[string]any)
            if servers == nil {
                servers = map[string]any{}
            }
            if existing, ok := servers["tatara"].(map[string]any); ok && !force {
                if existing["command"] != abs {
                    return fmt.Errorf("mcp-config: %s already has a tatara entry pointing at %v; pass --force to overwrite", path, existing["command"])
                }
            }
            servers["tatara"] = map[string]any{
                "command": abs,
                "args":    []string{"mcp"},
            }
            cfg["mcpServers"] = servers

            out, err := json.MarshalIndent(cfg, "", "  ")
            if err != nil {
                return err
            }
            if err := os.WriteFile(path, append(out, '\n'), 0o644); err != nil {
                return err
            }
            fmt.Fprintf(cmd.ErrOrStderr(), "Wrote tatara MCP entry to %s\n", path)
            return nil
        },
    }
    cmd.Flags().BoolVar(&force, "force", false, "Overwrite an existing tatara entry that points at a different command")
    return cmd
}
```

- [ ] **Step 2: mcp_config_test.go**

```go
package cmd_test

import (
    "encoding/json"
    "os"
    "path/filepath"
    "testing"

    "github.com/stretchr/testify/require"

    "github.com/szymonrychu/tatara-cli/internal/cmd"
)

func TestMCPConfig_WritesFreshFile(t *testing.T) {
    dir := t.TempDir()
    root := cmd.NewRootCmd()
    root.SetArgs([]string{"mcp-config", dir})
    require.NoError(t, root.Execute())

    b, err := os.ReadFile(filepath.Join(dir, ".mcp.json"))
    require.NoError(t, err)
    var got map[string]any
    require.NoError(t, json.Unmarshal(b, &got))
    servers := got["mcpServers"].(map[string]any)
    tatara := servers["tatara"].(map[string]any)
    require.NotEmpty(t, tatara["command"])
}

func TestMCPConfig_RefusesExistingDifferentCommand(t *testing.T) {
    dir := t.TempDir()
    existing := map[string]any{
        "mcpServers": map[string]any{
            "tatara": map[string]any{"command": "/somewhere/else"},
        },
    }
    b, _ := json.Marshal(existing)
    require.NoError(t, os.WriteFile(filepath.Join(dir, ".mcp.json"), b, 0o644))

    root := cmd.NewRootCmd()
    root.SetArgs([]string{"mcp-config", dir})
    require.Error(t, root.Execute())
}

func TestMCPConfig_ForceOverwrites(t *testing.T) {
    dir := t.TempDir()
    existing := map[string]any{
        "mcpServers": map[string]any{
            "tatara": map[string]any{"command": "/somewhere/else"},
        },
    }
    b, _ := json.Marshal(existing)
    require.NoError(t, os.WriteFile(filepath.Join(dir, ".mcp.json"), b, 0o644))

    root := cmd.NewRootCmd()
    root.SetArgs([]string{"mcp-config", dir, "--force"})
    require.NoError(t, root.Execute())
}
```

- [ ] **Step 3: Delete stubs.go. Build + test + commit.**

```bash
rm internal/cmd/stubs.go
go test ./... -race
pre-commit run --all-files
git add internal/cmd
git commit -m "feat(cmd): mcp + mcp-config"
git push origin main
```

---

## Wave 5 - Release pipeline

### Task 5.1: GoReleaser config

**Files:**
- Create: `.goreleaser.yaml`
- Create: `.github/workflows/release.yaml`

- [ ] **Step 1: .goreleaser.yaml**

```yaml
version: 2

before:
  hooks:
    - go mod tidy

builds:
  - id: tatara
    main: ./cmd/tatara
    binary: tatara
    env:
      - CGO_ENABLED=0
    goos: [linux, darwin]
    goarch: [amd64, arm64]
    ldflags:
      - -s -w
      - -X github.com/szymonrychu/tatara-cli/internal/version.Version={{ .Version }}
      - -X github.com/szymonrychu/tatara-cli/internal/version.Commit={{ .ShortCommit }}
      - -X github.com/szymonrychu/tatara-cli/internal/version.Date={{ .Date }}

archives:
  - format: tar.gz
    name_template: "{{ .ProjectName }}_{{ .Os }}_{{ .Arch }}"

brews:
  - name: tatara
    repository:
      owner: szymonrychu
      name: tap
    homepage: https://github.com/szymonrychu/tatara-cli
    description: "tatara platform CLI"
    license: AGPL-3.0
    install: |
      bin.install "tatara"
    test: |
      system "#{bin}/tatara --version"

checksum:
  name_template: "checksums.txt"

snapshot:
  version_template: "{{ incpatch .Version }}-snapshot"
```

- [ ] **Step 2: .github/workflows/release.yaml**

```yaml
name: release

on:
  push:
    tags: ['v*']

permissions:
  contents: write

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-go@v5
        with:
          go-version-file: go.mod
      - uses: goreleaser/goreleaser-action@v6
        with:
          version: latest
          args: release --clean
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          HOMEBREW_TAP_GITHUB_TOKEN: ${{ secrets.HOMEBREW_TAP_GITHUB_TOKEN }}
```

`HOMEBREW_TAP_GITHUB_TOKEN` is a PAT with `repo` scope for `szymonrychu/tap`. Configure it in repo secrets manually.

- [ ] **Step 3: Test goreleaser locally**

```bash
goreleaser release --snapshot --clean --skip=publish
```

Expected: archives appear under `dist/`. Inspect `dist/tatara_darwin_arm64.tar.gz` contains the binary.

- [ ] **Step 4: Commit.**

```bash
git add .goreleaser.yaml .github/workflows/release.yaml
git commit -m "ci: goreleaser config + release workflow with Homebrew tap"
git push origin main
```

### Task 5.2: Container image push

**Files:**
- Modify: `.github/workflows/release.yaml`

- [ ] **Step 1: Append container build/push job that runs after the goreleaser job. Either use docker/build-push-action or call `make push` from the Makefile.**

```yaml
  image:
    runs-on: ubuntu-latest
    needs: release
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with:
          registry: harbor.szymonrichert.pl
          username: ${{ secrets.HARBOR_USERNAME }}
          password: ${{ secrets.HARBOR_PASSWORD }}
      - uses: docker/setup-buildx-action@v3
      - name: Build and push image
        run: |
          VERSION=${GITHUB_REF_NAME}                # e.g. v0.1.0
          BARE=${VERSION#v}                          # 0.1.0
          docker buildx build \
            --platform=linux/amd64,linux/arm64 \
            --build-arg VERSION=${VERSION} \
            --build-arg COMMIT=$(git rev-parse --short HEAD) \
            --build-arg DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
            -t harbor.szymonrichert.pl/containers/tatara-cli:${VERSION} \
            -t harbor.szymonrichert.pl/containers/tatara-cli:${BARE} \
            --push .
```

`HARBOR_USERNAME`/`HARBOR_PASSWORD` go into repo secrets.

- [ ] **Step 2: Commit.**

```bash
git add .github/workflows/release.yaml
git commit -m "ci(release): push container image (both vX.Y.Z and X.Y.Z tags) to Harbor"
git push origin main
```

---

## Wave 6 - Deploy v0.1.0 + smoke test

### Task 6.1: Tag + release

- [ ] **Step 1: Tag**

```bash
cd ~/Documents/tatara/tatara-cli
git tag -a v0.1.0 -m "v0.1.0 - login, raw, mcp, mcp-config"
git push origin v0.1.0
```

- [ ] **Step 2: Watch the release workflow**

```bash
gh run watch
```

Expected: green. Binaries on GitHub Releases page. Formula committed in `szymonrychu/tap`. Container at `harbor.szymonrichert.pl/containers/tatara-cli:0.1.0`.

### Task 6.2: Install + login + smoke

- [ ] **Step 1: Install via Homebrew tap**

```bash
brew tap szymonrychu/tap
brew install tatara
tatara --version
```

- [ ] **Step 2: Login**

```bash
tatara login
```

Open the printed URL, authenticate as szymon.rychu@gmail.com, return to terminal. Token file appears at `~/.config/tatara/token.json` with mode 0600.

- [ ] **Step 3: Smoke - raw**

```bash
tatara raw POST /memories -d '{"text":"v0.1.0 CLI smoke test"}'
```

Expected: `HTTP/2 200 OK` to stderr, body to stdout with a track_id.

```bash
tatara raw GET /queries -d '{"mode":"hybrid","text":"smoke"}'
```

(POST/queries actually. Use the right verb.)

- [ ] **Step 4: Smoke - mcp-config + claude code**

```bash
mkdir -p /tmp/tatara-mcp-test && cd /tmp/tatara-mcp-test
tatara mcp-config .
cat .mcp.json
```

Expected: valid JSON with `mcpServers.tatara.command` pointing at the installed binary path.

Open Claude Code in `/tmp/tatara-mcp-test`. Confirm the tatara server appears in the MCP servers list and the tools (`create_memory`, etc.) are discoverable.

- [ ] **Step 5: Update docs**

Update `tatara-cli/MEMORY.md` and `tatara-cli/ROADMAP.md` (v0.1.0 shipped). Update `~/Documents/tatara/MEMORY.md` and `ROADMAP.md` (phase 2 shipped).

```bash
cd ~/Documents/tatara/tatara-cli
git add MEMORY.md ROADMAP.md
git commit -m "docs: v0.1.0 shipped (login, raw, mcp, mcp-config)"
git push origin main

cd ~/Documents/tatara
git add MEMORY.md ROADMAP.md
git commit -m "docs: phase 2 (tatara-cli) shipped v0.1.0"
git push origin main
```

---

## Out of scope (explicit deferrals)

- Per-tool gating (`--allow-mutations`) - deferred to v0.2.0.
- Multiple profiles / multiple base URLs.
- Table output, columns flag.
- Caching, ETag/If-None-Match handling.
- `mcp-config` user-level entry under `~/.claude/mcp.json`.

## Open questions resolved during planning

- Container base image: `gcr.io/distroless/static:nonroot` (Dockerfile in Task 1.2).
- mcp-go choice: `github.com/mark3labs/mcp-go` (Task 2C). Revisit if a more canonical Go SDK emerges before v0.1.0 ships.

## Total task count

22 tasks across 7 waves. Wave 2 is parallelizable across three subagents. Waves 0 and 6 are deploy-bound and must run sequentially.
