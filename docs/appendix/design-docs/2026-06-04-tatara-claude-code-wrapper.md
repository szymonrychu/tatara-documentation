# tatara-claude-code-wrapper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single-session Claude Code supervisor container that drives the
real interactive `claude` REPL over a PTY (never `-p`), submits one user turn
at a time, captures each turn's result via a custom Stop hook, and exposes it
through an OIDC-gated HTTP API with webhook-or-poll delivery.

**Architecture:** Go service (`wrapper`) is PID 1 in the pod. At boot it
renders config files (global/project `CLAUDE.md`, merged `.mcp.json`,
`settings.json` wiring a Stop hook, installs skills, optional repo clone),
then spawns interactive `claude` attached to a PTY. `POST /v1/messages` types
a message into the PTY and returns `202 {turnId}`; at end-of-turn the
`cc-stop-hook` binary reads the transcript and POSTs the result to the
wrapper's loopback-only internal endpoint; the wrapper persists it and
delivers via optional webhook (poll fallback always available). One claude
process per pod, turns strictly sequential (409 while busy). Reuses
tatara-memory's `internal/auth` and `internal/obs` verbatim.

**Tech Stack:** Go (newest stable, pin in `go.mod`), `github.com/creack/pty`,
`github.com/go-chi/chi/v5`, `github.com/coreos/go-oidc/v3`,
`github.com/prometheus/client_golang`, `github.com/stretchr/testify`. Node +
`@anthropic-ai/claude-code` for the runtime CLI. Helm chart via `helm create`.
CI via existing tatara-argo-workflows; release in `~/Documents/infra/helmfile`
for v0.1.0.

**Repo:** new `github.com/szymonrychu/tatara-claude-code-wrapper`, cloned to
`~/Documents/tatara/tatara-claude-code-wrapper/` (gitignored in parent).

---

## File Structure

```
tatara-claude-code-wrapper/
├── go.mod                          # module github.com/szymonrychu/tatara-claude-code-wrapper
├── Makefile                        # build/test/lint/image + bump-claude
├── Dockerfile                      # multi-stage: go build -> node runtime; ARG CLAUDE_CODE_VERSION
├── .golangci.yml                   # v2 schema, copied from tatara-cli
├── .gitignore .dockerignore LICENSE
├── CLAUDE.md                       # copy of parent CLAUDE.md
├── MEMORY.md ROADMAP.md README.md
├── cmd/
│   ├── wrapper/                    # main service
│   │   ├── main.go config.go app.go
│   │   └── config_test.go app_test.go integration_test.go
│   └── cc-stop-hook/               # Stop hook binary
│       ├── main.go hook.go transcript.go
│       └── hook_test.go transcript_test.go
├── internal/
│   ├── version/version.go          # copied from tatara-memory
│   ├── obs/{logger.go,registry.go} # copied subset from tatara-memory
│   ├── auth/{auth.go,verifier.go,middleware.go,testjwks/...}  # copied
│   ├── metrics/metrics.go          # ccw_* collectors
│   ├── turn/{turn.go,store.go}     # turn records + in-memory store
│   ├── session/{session.go,pty.go} # Manager: PTY spawn + submit + complete
│   ├── webhook/webhook.go          # async retrying delivery
│   ├── bootstrap/{bootstrap.go,mcp.go,settings.go,skills.go,repo.go}
│   └── httpapi/{api.go,messages.go,session.go,internal.go}
├── templates/skills/handoff/SKILL.md   # baked default skill
└── charts/tatara-claude-code-wrapper/  # helm chart
```

**Responsibility boundaries (each file one job):**
- `turn` owns the turn record type + thread-safe store. No I/O.
- `session` owns the claude process + PTY + turn-in-flight state machine.
  Depends on `turn`, `metrics`. Emits a "turn done" callback; knows nothing
  about HTTP or webhooks.
- `webhook` owns retrying HTTP delivery. Depends on `turn` (payload),
  `metrics`.
- `bootstrap` owns rendering boot files. Pure filesystem; injectable git
  runner.
- `httpapi` owns routing/handlers + OIDC. Depends on a `SessionController`
  interface (satisfied by `session.Manager`) + `turn.Store`.
- `cmd/wrapper` wires everything; `cmd/cc-stop-hook` is a standalone binary.

---

## Conventions (match sibling repos)

- Module path `github.com/szymonrychu/tatara-claude-code-wrapper`.
- Errors wrapped: `fmt.Errorf("context: %w", err)`.
- `slog` JSON via `obs.NewLogger`. Business actions at INFO with `turn_id`,
  `session_id`, `duration_ms`.
- Tests: testify `require`, table-driven with `t.Run`.
- Chart ConfigMap keys are **UPPER_SNAKE** (matches tatara-memory
  `envConfig` helper + consolidation P8), consumed via `envFrom`. camelCase
  scalar in `values.yaml` -> UPPER_SNAKE ConfigMap/Secret key -> env. Lists
  and file-shaped data go into separate mounted ConfigMaps, never inline.

---

## Task 0: Create the repo on GitHub and clone

**Files:** none yet (scaffold).

- [ ] **Step 1: Create the empty private repo and clone**

```bash
cd ~/Documents/tatara
gh repo create szymonrychu/tatara-claude-code-wrapper --private \
  --description "Single-session Claude Code supervisor (interactive PTY + Stop-hook API)"
git clone git@github.com:szymonrychu/tatara-claude-code-wrapper.git
cd tatara-claude-code-wrapper
```

Expected: empty repo cloned into `~/Documents/tatara/tatara-claude-code-wrapper`.

- [ ] **Step 2: Confirm parent gitignore already excludes it**

Run: `cd ~/Documents/tatara && git check-ignore tatara-claude-code-wrapper`
Expected: prints `tatara-claude-code-wrapper` (already covered per
`.gitignore`; if not, add it and commit separately in the parent repo).

---

## Task 1: SPIKE — pin interactive PTY driving + Stop hook against the real binary

This de-risks the whole architecture. **No production code is written from
guesses.** We run the real `claude` under a PTY and capture exact fixtures.
Everything downstream is TDD'd against these.

**Files:**
- Create: `hack/spike/drive.go` (throwaway; deleted at task end)
- Create (committed fixtures): `internal/session/testdata/hook_payload.json`,
  `internal/session/testdata/transcript_assistant_line.jsonl`,
  `cmd/cc-stop-hook/testdata/hook_payload.json`,
  `cmd/cc-stop-hook/testdata/transcript.jsonl`
- Create: `docs/spike-findings.md`

- [ ] **Step 1: Stand up claude + a capture hook in a scratch dir**

```bash
mkdir -p /tmp/ccw-spike/.claude/skills /tmp/ccw-spike/.cap
cd /tmp/ccw-spike
# capture hook: dumps its stdin payload, then exits 0 (pure side effect)
cat > .cap/hook.sh <<'EOF'
#!/bin/sh
cat > /tmp/ccw-spike/.cap/last_hook_payload.json
exit 0
EOF
chmod +x .cap/hook.sh
cat > .claude/settings.json <<'EOF'
{
  "hooks": { "Stop": [ { "matcher": "", "hooks": [ { "type": "command", "command": "/tmp/ccw-spike/.cap/hook.sh" } ] } ] },
  "enableAllProjectMcpServers": true
}
EOF
```

- [ ] **Step 2: Write the throwaway PTY driver**

`hack/spike/drive.go` (in the new repo, temporarily): allocate a PTY, spawn
`claude --permission-mode bypassPermissions` with `HOME=/tmp/ccw-spike`,
`cwd=/tmp/ccw-spike`, `TERM=xterm-256color`; copy PTY output to stdout in a
goroutine; after a 5s boot wait, write a test message using bracketed paste
then `\r`; wait for `.cap/last_hook_payload.json` to appear; print it; exit.

```go
package main

import (
	"fmt"
	"os"
	"os/exec"
	"time"

	"github.com/creack/pty"
)

func main() {
	cmd := exec.Command("claude", "--permission-mode", "bypassPermissions")
	cmd.Env = append(os.Environ(),
		"HOME=/tmp/ccw-spike", "TERM=xterm-256color")
	cmd.Dir = "/tmp/ccw-spike"
	ptmx, err := pty.Start(cmd)
	if err != nil {
		panic(err)
	}
	defer func() { _ = ptmx.Close() }()
	_ = pty.Setsize(ptmx, &pty.Winsize{Rows: 40, Cols: 120})
	go func() { _, _ = io.Copy(os.Stdout, ptmx) }() // observe TUI

	time.Sleep(8 * time.Second) // boot
	msg := "Reply with exactly the word PONG and nothing else."
	// bracketed paste so newlines never submit early, then CR to submit
	_, _ = ptmx.Write([]byte("\x1b[200~" + msg + "\x1b[201~"))
	time.Sleep(200 * time.Millisecond)
	_, _ = ptmx.Write([]byte("\r"))

	for i := 0; i < 120; i++ {
		if _, err := os.Stat("/tmp/ccw-spike/.cap/last_hook_payload.json"); err == nil {
			b, _ := os.ReadFile("/tmp/ccw-spike/.cap/last_hook_payload.json")
			fmt.Printf("\n=== HOOK PAYLOAD ===\n%s\n", b)
			return
		}
		time.Sleep(time.Second)
	}
	fmt.Println("TIMEOUT: no hook fired")
}
```

(Add `io` to imports.)

- [ ] **Step 3: Run the spike and capture reality**

```bash
cd ~/Documents/tatara/tatara-claude-code-wrapper
export ANTHROPIC_API_KEY=...   # real key from sops/op for the spike only
go mod init github.com/szymonrychu/tatara-claude-code-wrapper
go get github.com/creack/pty
go run ./hack/spike/drive.go | tee /tmp/ccw-spike/run.log
```

Expected: TUI renders; the typed message submits as ONE turn; the Stop hook
fires; the printed `HOOK PAYLOAD` JSON contains at least `session_id`,
`transcript_path`, `hook_event_name":"Stop"`. Record what else is present
(`stop_reason`, `messages`, `cwd`, `permission_mode`, `stop_hook_active`).

If the message does NOT submit, iterate on the submit sequence in Step 2
(candidates, in order: `\r`; `\n`; paste then `\r`; `\x0d`; double `\r`).
Record the working sequence.

- [ ] **Step 4: Capture committed fixtures**

```bash
cp /tmp/ccw-spike/.cap/last_hook_payload.json internal/session/testdata/hook_payload.json
mkdir -p cmd/cc-stop-hook/testdata
cp /tmp/ccw-spike/.cap/last_hook_payload.json cmd/cc-stop-hook/testdata/hook_payload.json
# copy the real transcript the payload points at:
TP=$(python3 -c "import json,sys;print(json.load(open('internal/session/testdata/hook_payload.json'))['transcript_path'])")
cp "$TP" cmd/cc-stop-hook/testdata/transcript.jsonl
# extract one assistant line for the session-level fixture:
grep '"type":"assistant"' "$TP" | tail -1 > internal/session/testdata/transcript_assistant_line.jsonl
```

- [ ] **Step 5: Record findings, then verify a SECOND turn works (persistence)**

Extend the spike to send a second message after the first hook fires (write
another bracketed-paste + `\r`), confirm a second hook fires WITHOUT
respawning claude. Write `docs/spike-findings.md` capturing: the exact
submit byte sequence, the boot-readiness signal (what TUI text/escape marks
"prompt ready"), the full hook payload field list, the transcript assistant
line shape, whether `--mcp-config` was needed or cwd `.mcp.json` +
`enableAllProjectMcpServers` sufficed to avoid an approval prompt, and the
flags that prevented any blocking permission prompt.

- [ ] **Step 6: Remove the throwaway driver, commit fixtures + findings**

```bash
rm -rf hack/spike
git add internal/session/testdata cmd/cc-stop-hook/testdata docs/spike-findings.md go.mod go.sum
git commit -m "spike: capture interactive PTY submit sequence + Stop hook fixtures"
```

> **Downstream tasks consume `docs/spike-findings.md`.** Wherever this plan
> gives a provisional submit sequence or readiness heuristic, the spike
> findings are authoritative — adjust the constant to match the captured
> reality before the test goes green.

---

## Task 2: Scaffold module, copy reusable packages, tooling

**Files:**
- Create: `go.mod` (already init'd in Task 1), `Makefile`, `.golangci.yml`,
  `.gitignore`, `.dockerignore`, `LICENSE`, `CLAUDE.md`, `MEMORY.md`,
  `ROADMAP.md`, `README.md`
- Create: `internal/version/version.go`
- Copy: `internal/obs/`, `internal/auth/` from tatara-memory (module path
  rewritten)

- [ ] **Step 1: Pin Go and copy tooling from siblings**

```bash
cd ~/Documents/tatara/tatara-claude-code-wrapper
GOV=$(go version | grep -oE '1\.[0-9]+\.[0-9]+')
go mod edit -go=${GOV%.*}        # e.g. go 1.25
cp ../tatara-cli/.golangci.yml .
cp ../tatara-memory/LICENSE .
cp ../CLAUDE.md .                 # parent contract, per hard rules
printf 'bin/\ndist/\n*.log\n' > .gitignore
printf '.git\nbin\ndist\ncharts\ndocs\n*.md\n' > .dockerignore
```

- [ ] **Step 2: Copy version/obs/auth, rewrite the module path**

```bash
mkdir -p internal
cp -r ../tatara-memory/internal/version internal/version
cp -r ../tatara-memory/internal/obs     internal/obs
cp -r ../tatara-memory/internal/auth    internal/auth
# rewrite import paths to the new module
grep -rl 'szymonrychu/tatara-memory' internal | xargs \
  sed -i '' 's#szymonrychu/tatara-memory#szymonrychu/tatara-claude-code-wrapper#g'
```

Keep only what `obs` needs (`NewLogger`, `PromRegistry`/registry, and their
tests). Delete `internal/obs/tracing*.go` and tracing-only tests if OTLP is
not wired in v0.1.0 (it isn't — drop them).

- [ ] **Step 3: Author the Makefile**

```make
SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c

REGISTRY ?= harbor.szymonrichert.pl
IMAGE_NAME ?= containers/tatara-claude-code-wrapper
VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
COMMIT ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
DATE ?= $(shell date -u +%Y-%m-%dT%H:%M:%SZ)
CLAUDE_CODE_VERSION ?= latest
TATARA_CLI_VERSION ?= latest
IMAGE_REF := $(REGISTRY)/$(IMAGE_NAME):$(VERSION)
MODPATH := github.com/szymonrychu/tatara-claude-code-wrapper

.PHONY: all lint test build image push tidy fmt clean chart-test bump-claude

all: lint test build
tidy: ; go mod tidy
fmt:
	gofmt -s -w .
	goimports -w -local $(MODPATH) .
lint: ; golangci-lint run ./... || [ $$? -eq 5 ]
test: ; go test ./... -race -count=1
build:
	CGO_ENABLED=0 go build -trimpath \
	  -ldflags "-s -w -X $(MODPATH)/internal/version.Version=$(VERSION) -X $(MODPATH)/internal/version.Commit=$(COMMIT) -X $(MODPATH)/internal/version.Date=$(DATE)" \
	  -o bin/wrapper ./cmd/wrapper
	CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o bin/cc-stop-hook ./cmd/cc-stop-hook
image:
	docker buildx build --platform=linux/amd64 \
	  --build-arg VERSION=$(VERSION) --build-arg COMMIT=$(COMMIT) --build-arg DATE=$(DATE) \
	  --build-arg CLAUDE_CODE_VERSION=$(CLAUDE_CODE_VERSION) --build-arg TATARA_CLI_VERSION=$(TATARA_CLI_VERSION) \
	  -t $(IMAGE_REF) --load .
push: image ; docker push $(IMAGE_REF)
chart-test: ; helm unittest charts/tatara-claude-code-wrapper
bump-claude:
	@test -n "$(VERSION_ARG)" || { echo "usage: make bump-claude VERSION_ARG=1.2.3"; exit 1; }
	sed -i '' 's/^ARG CLAUDE_CODE_VERSION=.*/ARG CLAUDE_CODE_VERSION=$(VERSION_ARG)/' Dockerfile
clean: ; rm -rf bin dist
```

- [ ] **Step 4: Verify it compiles and copied tests pass**

Run: `go mod tidy && go build ./... && go test ./internal/... -count=1`
Expected: build OK; `internal/auth` and `internal/obs` tests PASS (proven
code, just re-homed).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "chore: scaffold module, copy auth/obs/version, tooling"
```

---

## Task 3: turn record + in-memory store

**Files:**
- Create: `internal/turn/turn.go`, `internal/turn/store.go`
- Test: `internal/turn/store_test.go`

- [ ] **Step 1: Write the failing test**

```go
package turn_test

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/turn"
)

func TestStore_CreateCompleteGet(t *testing.T) {
	s := turn.NewStore()
	t0 := time.Unix(1000, 0)
	rec := s.Create("turn-1", "hello", "https://cb.example/x", t0)
	require.Equal(t, turn.Running, rec.State)

	got, ok := s.Get("turn-1")
	require.True(t, ok)
	require.Equal(t, turn.Running, got.State)
	require.Equal(t, "https://cb.example/x", got.CallbackURL)

	t1 := time.Unix(1005, 0)
	require.NoError(t, s.Complete("turn-1", "PONG",
		json.RawMessage(`{"status":"success"}`), json.RawMessage(`{"output_tokens":3}`), "end_turn", t1))

	got, _ = s.Get("turn-1")
	require.Equal(t, turn.Complete, got.State)
	require.Equal(t, "PONG", got.FinalText)
	require.Equal(t, "end_turn", got.StopReason)
	require.NotNil(t, got.CompletedAt)

	require.ErrorIs(t, s.Complete("missing", "", nil, nil, "", t1), turn.ErrNotFound)
}

func TestStore_ListOrderedAndFail(t *testing.T) {
	s := turn.NewStore()
	s.Create("a", "1", "", time.Unix(1, 0))
	s.Create("b", "2", "", time.Unix(2, 0))
	require.NoError(t, s.Fail("a", "boom", time.Unix(3, 0)))

	list := s.List()
	require.Len(t, list, 2)
	require.Equal(t, "a", list[0].ID)
	require.Equal(t, turn.Failed, list[0].State)
	require.Equal(t, "b", list[1].ID)
}
```

- [ ] **Step 2: Run, verify it fails**

Run: `go test ./internal/turn/ -run TestStore -v`
Expected: FAIL (package `turn` does not compile / undefined).

- [ ] **Step 3: Implement `turn.go`**

```go
// Package turn holds per-turn records and a thread-safe store.
package turn

import (
	"encoding/json"
	"time"
)

type State string

const (
	Running  State = "running"
	Complete State = "complete"
	Failed   State = "failed"
)

// Record is one user turn and its eventual result.
type Record struct {
	ID          string          `json:"turnId"`
	State       State           `json:"state"`
	Text        string          `json:"-"`
	CallbackURL string          `json:"-"`
	FinalText   string          `json:"finalText,omitempty"`
	ResultJSON  json.RawMessage `json:"resultJson,omitempty"`
	Usage       json.RawMessage `json:"usage,omitempty"`
	StopReason  string          `json:"stopReason,omitempty"`
	Error       string          `json:"error,omitempty"`
	StartedAt   time.Time       `json:"startedAt"`
	CompletedAt *time.Time      `json:"completedAt,omitempty"`
}

// Summary is the compact form returned by List.
type Summary struct {
	ID          string     `json:"turnId"`
	State       State      `json:"state"`
	StartedAt   time.Time  `json:"startedAt"`
	CompletedAt *time.Time `json:"completedAt,omitempty"`
}
```

- [ ] **Step 4: Implement `store.go`**

```go
package turn

import (
	"encoding/json"
	"errors"
	"sync"
	"time"
)

var ErrNotFound = errors.New("turn: not found")

type Store struct {
	mu    sync.RWMutex
	byID  map[string]*Record
	order []string
}

func NewStore() *Store { return &Store{byID: map[string]*Record{}} }

func (s *Store) Create(id, text, callbackURL string, now time.Time) *Record {
	s.mu.Lock()
	defer s.mu.Unlock()
	rec := &Record{ID: id, State: Running, Text: text, CallbackURL: callbackURL, StartedAt: now}
	s.byID[id] = rec
	s.order = append(s.order, id)
	return rec
}

func (s *Store) Get(id string) (*Record, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	rec, ok := s.byID[id]
	if !ok {
		return nil, false
	}
	cp := *rec
	return &cp, true
}

func (s *Store) List() []Summary {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]Summary, 0, len(s.order))
	for _, id := range s.order {
		r := s.byID[id]
		out = append(out, Summary{ID: r.ID, State: r.State, StartedAt: r.StartedAt, CompletedAt: r.CompletedAt})
	}
	return out
}

func (s *Store) Complete(id, finalText string, resultJSON, usage json.RawMessage, stopReason string, now time.Time) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	r, ok := s.byID[id]
	if !ok {
		return ErrNotFound
	}
	r.State, r.FinalText, r.ResultJSON, r.Usage, r.StopReason = Complete, finalText, resultJSON, usage, stopReason
	r.CompletedAt = &now
	return nil
}

func (s *Store) Fail(id, msg string, now time.Time) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	r, ok := s.byID[id]
	if !ok {
		return ErrNotFound
	}
	r.State, r.Error, r.CompletedAt = Failed, msg, &now
	return nil
}
```

- [ ] **Step 5: Run tests, then commit**

Run: `go test ./internal/turn/ -race -count=1`
Expected: PASS.

```bash
git add internal/turn && git commit -m "feat: turn record + in-memory store"
```

---

## Task 4: ccw_* prometheus metrics

**Files:**
- Create: `internal/metrics/metrics.go`
- Test: `internal/metrics/metrics_test.go`

- [ ] **Step 1: Write the failing test**

```go
package metrics_test

import (
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/stretchr/testify/require"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/metrics"
)

func TestNew_RegistersAllCollectors(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := metrics.New(reg)
	require.NotNil(t, m)

	m.TurnsTotal.WithLabelValues("complete").Inc()
	m.TurnDuration.Observe(1.2)
	m.TurnInFlight.Set(1)
	m.ClaudeRestarts.Inc()
	m.WebhookDelivery.WithLabelValues("ok").Inc()
	m.HookReceived.Inc()

	mfs, err := reg.Gather()
	require.NoError(t, err)
	names := map[string]bool{}
	for _, mf := range mfs {
		names[mf.GetName()] = true
	}
	for _, want := range []string{
		"ccw_turns_total", "ccw_turn_duration_seconds", "ccw_turn_in_flight",
		"ccw_claude_restarts_total", "ccw_webhook_delivery_total", "ccw_hook_received_total",
	} {
		require.True(t, names[want], "missing %s", want)
	}
}
```

- [ ] **Step 2: Run, verify it fails**

Run: `go test ./internal/metrics/ -v`
Expected: FAIL (undefined `metrics`).

- [ ] **Step 3: Implement `metrics.go`**

```go
// Package metrics holds the wrapper's prometheus collectors.
package metrics

import "github.com/prometheus/client_golang/prometheus"

type Metrics struct {
	TurnsTotal      *prometheus.CounterVec
	TurnDuration    prometheus.Histogram
	TurnInFlight    prometheus.Gauge
	ClaudeRestarts  prometheus.Counter
	WebhookDelivery *prometheus.CounterVec
	HookReceived    prometheus.Counter
}

func New(reg prometheus.Registerer) *Metrics {
	m := &Metrics{
		TurnsTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "ccw_turns_total", Help: "Turns by terminal result."}, []string{"result"}),
		TurnDuration: prometheus.NewHistogram(prometheus.HistogramOpts{
			Name: "ccw_turn_duration_seconds", Help: "Turn wall-clock duration.",
			Buckets: prometheus.ExponentialBuckets(1, 2, 12)}),
		TurnInFlight: prometheus.NewGauge(prometheus.GaugeOpts{
			Name: "ccw_turn_in_flight", Help: "Turns currently in flight (0 or 1)."}),
		ClaudeRestarts: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "ccw_claude_restarts_total", Help: "claude process restarts."}),
		WebhookDelivery: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "ccw_webhook_delivery_total", Help: "Webhook deliveries by result."}, []string{"result"}),
		HookReceived: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "ccw_hook_received_total", Help: "Stop-hook callbacks received."}),
	}
	reg.MustRegister(m.TurnsTotal, m.TurnDuration, m.TurnInFlight,
		m.ClaudeRestarts, m.WebhookDelivery, m.HookReceived)
	return m
}
```

- [ ] **Step 4: Run, then commit**

Run: `go test ./internal/metrics/ -race -count=1` -> PASS

```bash
git add internal/metrics && git commit -m "feat: ccw_* prometheus metrics"
```

---

## Task 5: webhook sender (async, retrying)

**Files:**
- Create: `internal/webhook/webhook.go`
- Test: `internal/webhook/webhook_test.go`

- [ ] **Step 1: Write the failing test**

```go
package webhook_test

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/stretchr/testify/require"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/metrics"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/turn"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/webhook"
)

func newSender(t *testing.T, retries int) *webhook.Sender {
	t.Helper()
	return webhook.New(webhook.Config{Retries: retries, Backoff: time.Millisecond},
		metrics.New(prometheus.NewRegistry()), discardLogger())
}

func TestDeliver_SucceedsAfterRetry(t *testing.T) {
	var hits int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if atomic.AddInt32(&hits, 1) < 2 {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	s := newSender(t, 3)
	rec := &turn.Record{ID: "t1", State: turn.Complete, FinalText: "PONG"}
	s.Deliver(context.Background(), srv.URL, rec)
	require.Eventually(t, func() bool { return atomic.LoadInt32(&hits) == 2 }, time.Second, 5*time.Millisecond)
}

func TestDeliver_EmptyURLIsNoop(t *testing.T) {
	s := newSender(t, 1)
	s.Deliver(context.Background(), "", &turn.Record{ID: "t1"}) // must not panic
}
```

Add a tiny `discardLogger()` helper in the test file:
```go
func discardLogger() *slog.Logger { return slog.New(slog.NewTextHandler(io.Discard, nil)) }
```
(import `io`, `log/slog`.)

- [ ] **Step 2: Run, verify it fails**

Run: `go test ./internal/webhook/ -v`
Expected: FAIL (undefined `webhook`).

- [ ] **Step 3: Implement `webhook.go`**

```go
// Package webhook delivers turn results to caller-supplied callback URLs.
package webhook

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/metrics"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/turn"
)

type Config struct {
	Retries int
	Backoff time.Duration
}

type Sender struct {
	cfg    Config
	client *http.Client
	m      *metrics.Metrics
	log    *slog.Logger
}

func New(cfg Config, m *metrics.Metrics, log *slog.Logger) *Sender {
	if cfg.Backoff <= 0 {
		cfg.Backoff = time.Second
	}
	return &Sender{cfg: cfg, client: &http.Client{Timeout: 30 * time.Second}, m: m, log: log}
}

// Deliver posts the record to url asynchronously, retrying with exponential
// backoff. A blank url is a no-op (poll-only callers).
func (s *Sender) Deliver(ctx context.Context, url string, rec *turn.Record) {
	if url == "" {
		return
	}
	body, err := json.Marshal(rec)
	if err != nil {
		s.log.Error("webhook: marshal", "err", err, "turn_id", rec.ID)
		return
	}
	go s.deliver(ctx, url, rec.ID, body)
}

func (s *Sender) deliver(ctx context.Context, url, turnID string, body []byte) {
	backoff := s.cfg.Backoff
	for attempt := 0; attempt <= s.cfg.Retries; attempt++ {
		if err := s.post(ctx, url, body); err != nil {
			s.log.Warn("webhook: attempt failed", "err", err, "turn_id", turnID, "attempt", attempt)
			select {
			case <-ctx.Done():
				s.m.WebhookDelivery.WithLabelValues("dropped").Inc()
				return
			case <-time.After(backoff):
			}
			backoff *= 2
			continue
		}
		s.m.WebhookDelivery.WithLabelValues("ok").Inc()
		s.log.Info("webhook: delivered", "turn_id", turnID, "url", url)
		return
	}
	s.m.WebhookDelivery.WithLabelValues("dropped").Inc()
	s.log.Error("webhook: dropped after retries", "turn_id", turnID, "url", url)
}

func (s *Sender) post(ctx context.Context, url string, body []byte) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("new request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := s.client.Do(req)
	if err != nil {
		return fmt.Errorf("do: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode >= 300 {
		return fmt.Errorf("status %d", resp.StatusCode)
	}
	return nil
}
```

- [ ] **Step 4: Run, then commit**

Run: `go test ./internal/webhook/ -race -count=1` -> PASS

```bash
git add internal/webhook && git commit -m "feat: async retrying webhook sender"
```

---

## Task 6: bootstrap — render boot files

**Files:**
- Create: `internal/bootstrap/bootstrap.go`, `mcp.go`, `settings.go`,
  `skills.go`, `repo.go`, `claudejson.go`
- Test: `internal/bootstrap/bootstrap_test.go`, `mcp_test.go`,
  `settings_test.go`, `claudejson_test.go`

> **Spike-confirmed (Task 1, `docs/spike-findings.md`):** a fresh `$HOME`
> shows blocking boot dialogs unless `~/.claude.json` is pre-seeded. This task
> therefore ALSO writes `~/.claude.json` via `claudejson.go` (Step 6b) with
> `hasCompletedOnboarding:true`, `customApiKeyResponses.approved` set to the
> LAST 20 CHARS of the Anthropic API key, and
> `projects[Workspace].hasTrustDialogAccepted:true`. `Params` gains
> `AnthropicAPIKey string`. `settings.go` sets `permissions.defaultMode` (from
> `PermissionMode`) + `enableAllProjectMcpServers` (already specified).

This package is pure filesystem with an injected git runner, so it is fully
unit-testable in a temp dir.

- [ ] **Step 1: Write the failing tests**

```go
package bootstrap_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/bootstrap"
)

func TestRender_WritesClaudeMdSettingsSkillsAndMergesMCP(t *testing.T) {
	home := t.TempDir()
	ws := t.TempDir()
	overlay := t.TempDir()
	skillsSrc := t.TempDir()

	// a baked skill source
	require.NoError(t, os.MkdirAll(filepath.Join(skillsSrc, "handoff"), 0o755))
	require.NoError(t, os.WriteFile(filepath.Join(skillsSrc, "handoff", "SKILL.md"), []byte("# /handoff"), 0o644))
	// an mcp overlay fragment
	require.NoError(t, os.WriteFile(filepath.Join(overlay, "tasks.json"),
		[]byte(`{"mcpServers":{"tasks":{"type":"stdio","command":"/bin/tasks"}}}`), 0o644))

	var gitCalls [][]string
	p := bootstrap.Params{
		HomeDir: home, Workspace: ws,
		GlobalClaudeMd:  "GLOBAL RULES",
		ProjectClaudeMd: "PROJECT RULES",
		BaseMCP:         []byte(`{"mcpServers":{"tatara-memory":{"type":"stdio","command":"tatara","args":["mcp"]}}}`),
		MCPOverlayDir:   overlay,
		SkillsSrc:       []string{skillsSrc},
		HookCommand:     "/usr/local/bin/cc-stop-hook",
		AllowedTools:    []string{"Bash", "Edit"},
		EnableAllMCP:    true,
		PermissionMode:  "bypassPermissions",
	}
	require.NoError(t, bootstrap.Render(p, func(a ...string) error { gitCalls = append(gitCalls, a); return nil }))

	// global + project CLAUDE.md
	b, _ := os.ReadFile(filepath.Join(home, ".claude", "CLAUDE.md"))
	require.Equal(t, "GLOBAL RULES", string(b))
	b, _ = os.ReadFile(filepath.Join(ws, "CLAUDE.md"))
	require.Equal(t, "PROJECT RULES", string(b))

	// merged .mcp.json has BOTH servers
	b, _ = os.ReadFile(filepath.Join(ws, ".mcp.json"))
	var mcp struct{ MCPServers map[string]any `json:"mcpServers"` }
	require.NoError(t, json.Unmarshal(b, &mcp))
	require.Contains(t, mcp.MCPServers, "tatara-memory")
	require.Contains(t, mcp.MCPServers, "tasks")

	// settings.json wires Stop hook + enableAllProjectMcpServers
	b, _ = os.ReadFile(filepath.Join(home, ".claude", "settings.json"))
	require.Contains(t, string(b), "/usr/local/bin/cc-stop-hook")
	require.Contains(t, string(b), "enableAllProjectMcpServers")

	// skill copied
	b, _ = os.ReadFile(filepath.Join(ws, ".claude", "skills", "handoff", "SKILL.md"))
	require.Equal(t, "# /handoff", string(b))

	// no repo configured -> git not called
	require.Empty(t, gitCalls)
}

func TestRender_ClonesRepoWhenURLSet(t *testing.T) {
	var gitCalls [][]string
	p := bootstrap.Params{
		HomeDir: t.TempDir(), Workspace: t.TempDir(),
		BaseMCP: []byte(`{"mcpServers":{}}`),
		RepoURL: "https://github.com/x/y", RepoBranch: "main",
		HookCommand: "/usr/local/bin/cc-stop-hook", PermissionMode: "bypassPermissions",
	}
	require.NoError(t, bootstrap.Render(p, func(a ...string) error { gitCalls = append(gitCalls, a); return nil }))
	require.Len(t, gitCalls, 1)
	require.Contains(t, gitCalls[0], "clone")
	require.Contains(t, gitCalls[0], "https://github.com/x/y")
	require.Contains(t, gitCalls[0], "main")
}
```

- [ ] **Step 2: Run, verify it fails**

Run: `go test ./internal/bootstrap/ -v`
Expected: FAIL (undefined `bootstrap`).

- [ ] **Step 3: Implement `bootstrap.go`**

```go
// Package bootstrap renders the per-session files claude reads at startup.
package bootstrap

import (
	"fmt"
	"os"
	"path/filepath"
)

type Params struct {
	HomeDir, Workspace          string
	GlobalClaudeMd, ProjectClaudeMd string
	BaseMCP                     []byte
	MCPOverlayDir               string
	SkillsSrc                   []string
	HookCommand                 string
	AllowedTools                []string
	EnableAllMCP                bool
	PermissionMode              string
	AnthropicAPIKey             string // used to seed customApiKeyResponses (last 20 chars)
	RepoURL, RepoBranch         string
}

// GitRunner runs a git subcommand; injected for testability.
type GitRunner func(args ...string) error

func Render(p Params, git GitRunner) error {
	claudeHome := filepath.Join(p.HomeDir, ".claude")
	if err := os.MkdirAll(claudeHome, 0o755); err != nil {
		return fmt.Errorf("mkdir claude home: %w", err)
	}
	if err := os.MkdirAll(p.Workspace, 0o755); err != nil {
		return fmt.Errorf("mkdir workspace: %w", err)
	}
	if p.RepoURL != "" {
		if err := cloneRepo(p, git); err != nil {
			return err
		}
	}
	if err := writeIfSet(filepath.Join(p.Workspace, "CLAUDE.md"), p.ProjectClaudeMd); err != nil {
		return err
	}
	if err := writeIfSet(filepath.Join(claudeHome, "CLAUDE.md"), p.GlobalClaudeMd); err != nil {
		return err
	}
	if err := mergeMCP(p); err != nil {
		return err
	}
	if err := writeSettings(p, claudeHome); err != nil {
		return err
	}
	if err := writeClaudeJSON(p); err != nil {
		return err
	}
	return installSkills(p)
}

func writeIfSet(path, content string) error {
	if content == "" {
		return nil
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	return nil
}
```

- [ ] **Step 4: Implement `mcp.go`**

```go
package bootstrap

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

type mcpDoc struct {
	MCPServers map[string]json.RawMessage `json:"mcpServers"`
}

// mergeMCP unions the baked base config with every *.json fragment in the
// overlay dir and writes /workspace/.mcp.json. Overlay keys win on conflict.
func mergeMCP(p Params) error {
	merged := mcpDoc{MCPServers: map[string]json.RawMessage{}}
	if len(p.BaseMCP) > 0 {
		var base mcpDoc
		if err := json.Unmarshal(p.BaseMCP, &base); err != nil {
			return fmt.Errorf("parse base mcp: %w", err)
		}
		for k, v := range base.MCPServers {
			merged.MCPServers[k] = v
		}
	}
	if p.MCPOverlayDir != "" {
		entries, err := os.ReadDir(p.MCPOverlayDir)
		if err != nil && !os.IsNotExist(err) {
			return fmt.Errorf("read mcp overlay: %w", err)
		}
		for _, e := range entries {
			if e.IsDir() || filepath.Ext(e.Name()) != ".json" {
				continue
			}
			b, err := os.ReadFile(filepath.Join(p.MCPOverlayDir, e.Name()))
			if err != nil {
				return fmt.Errorf("read overlay %s: %w", e.Name(), err)
			}
			var frag mcpDoc
			if err := json.Unmarshal(b, &frag); err != nil {
				return fmt.Errorf("parse overlay %s: %w", e.Name(), err)
			}
			for k, v := range frag.MCPServers {
				merged.MCPServers[k] = v
			}
		}
	}
	out, err := json.MarshalIndent(merged, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal mcp: %w", err)
	}
	if err := os.WriteFile(filepath.Join(p.Workspace, ".mcp.json"), out, 0o644); err != nil {
		return fmt.Errorf("write .mcp.json: %w", err)
	}
	return nil
}
```

- [ ] **Step 5: Implement `settings.go`**

```go
package bootstrap

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

func writeSettings(p Params, claudeHome string) error {
	type hookCmd struct {
		Type    string `json:"type"`
		Command string `json:"command"`
	}
	type hookMatcher struct {
		Matcher string    `json:"matcher"`
		Hooks   []hookCmd `json:"hooks"`
	}
	settings := map[string]any{
		"hooks": map[string]any{
			"Stop": []hookMatcher{{Matcher: "", Hooks: []hookCmd{{Type: "command", Command: p.HookCommand}}}},
		},
		"enableAllProjectMcpServers": p.EnableAllMCP,
	}
	if p.PermissionMode != "" || len(p.AllowedTools) > 0 {
		perms := map[string]any{}
		if p.PermissionMode != "" {
			perms["defaultMode"] = p.PermissionMode
		}
		if len(p.AllowedTools) > 0 {
			perms["allow"] = p.AllowedTools
		}
		settings["permissions"] = perms
	}
	out, err := json.MarshalIndent(settings, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal settings: %w", err)
	}
	if err := os.WriteFile(filepath.Join(claudeHome, "settings.json"), out, 0o644); err != nil {
		return fmt.Errorf("write settings.json: %w", err)
	}
	return nil
}
```

> Spike note: if `docs/spike-findings.md` shows `--mcp-config` or a different
> permission key was required for headless auto-approval, reflect it here
> (and in the claude args built in Task 8) before the integration test.

- [ ] **Step 6: Implement `skills.go` and `repo.go`**

```go
// skills.go
package bootstrap

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
)

// installSkills copies every skill tree under each SkillsSrc dir into
// /workspace/.claude/skills, so baked + custom skills coexist.
func installSkills(p Params) error {
	dst := filepath.Join(p.Workspace, ".claude", "skills")
	if err := os.MkdirAll(dst, 0o755); err != nil {
		return fmt.Errorf("mkdir skills: %w", err)
	}
	for _, src := range p.SkillsSrc {
		if src == "" {
			continue
		}
		if _, err := os.Stat(src); os.IsNotExist(err) {
			continue
		}
		if err := copyTree(src, dst); err != nil {
			return fmt.Errorf("install skills from %s: %w", src, err)
		}
	}
	return nil
}

func copyTree(src, dst string) error {
	return filepath.Walk(src, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(src, path)
		target := filepath.Join(dst, rel)
		if info.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		return copyFile(path, target)
	})
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer func() { _ = in.Close() }()
	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer func() { _ = out.Close() }()
	_, err = io.Copy(out, in)
	return err
}
```

```go
// repo.go
package bootstrap

// cloneRepo shallow-clones RepoURL@RepoBranch into the workspace.
func cloneRepo(p Params, git GitRunner) error {
	args := []string{"clone", "--depth", "1"}
	if p.RepoBranch != "" {
		args = append(args, "--branch", p.RepoBranch)
	}
	args = append(args, p.RepoURL, p.Workspace)
	if err := git(args...); err != nil {
		return err
	}
	return nil
}
```

- [ ] **Step 6b: Implement `claudejson.go` (no-dialog seed) + test**

Test (`claudejson_test.go`):
```go
func TestWriteClaudeJSON_SeedsNoDialogKeys(t *testing.T) {
	home := t.TempDir()
	p := bootstrap.Params{HomeDir: home, Workspace: "/workspace",
		AnthropicAPIKey: "sk-ant-XXXXXXXXXXXXXXXXXXXXEentiTPHC9Q-62Rz1wAA"}
	require.NoError(t, bootstrap.WriteClaudeJSONForTest(p)) // thin exported wrapper for the test
	b, _ := os.ReadFile(filepath.Join(home, ".claude.json"))
	var doc map[string]any
	require.NoError(t, json.Unmarshal(b, &doc))
	require.Equal(t, true, doc["hasCompletedOnboarding"])
	approved := doc["customApiKeyResponses"].(map[string]any)["approved"].([]any)
	require.Equal(t, "EentiTPHC9Q-62Rz1wAA", approved[0]) // last 20 chars
	proj := doc["projects"].(map[string]any)["/workspace"].(map[string]any)
	require.Equal(t, true, proj["hasTrustDialogAccepted"])
}
```

Implementation (`claudejson.go`):
```go
package bootstrap

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

// writeClaudeJSON seeds ~/.claude.json so a fresh-HOME unattended claude boots
// with no interactive dialogs (onboarding, folder trust, custom-API-key).
// Recipe confirmed by the Task-1 spike (docs/spike-findings.md).
func writeClaudeJSON(p Params) error {
	doc := map[string]any{
		"hasCompletedOnboarding": true,
		"autoUpdates":            true,
		"projects": map[string]any{
			p.Workspace: map[string]any{"hasTrustDialogAccepted": true},
		},
	}
	if p.AnthropicAPIKey != "" {
		doc["customApiKeyResponses"] = map[string]any{
			"approved": []string{lastN(p.AnthropicAPIKey, 20)},
			"rejected": []string{},
		}
	}
	b, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal claude.json: %w", err)
	}
	if err := os.WriteFile(filepath.Join(p.HomeDir, ".claude.json"), b, 0o644); err != nil {
		return fmt.Errorf("write claude.json: %w", err)
	}
	return nil
}

func lastN(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[len(s)-n:]
}

// WriteClaudeJSONForTest exposes writeClaudeJSON for the package test.
func WriteClaudeJSONForTest(p Params) error { return writeClaudeJSON(p) }
```

- [ ] **Step 7: Run all bootstrap tests, then commit**

Run: `go test ./internal/bootstrap/ -race -count=1` -> PASS

```bash
git add internal/bootstrap && git commit -m "feat: bootstrap renders CLAUDE.md, merged MCP, settings, skills, repo clone, claude.json seed"
```

---

## Task 7: session manager — PTY spawn, submit, complete, timeout

**Files:**
- Create: `internal/session/session.go`, `internal/session/pty.go`
- Test: `internal/session/session_test.go`

The PTY is driven through a tiny `ptyProc` seam so tests can substitute a
fake without a real terminal. Process spawning lives in `pty.go`; the state
machine in `session.go`.

- [ ] **Step 1: Write the failing test (state machine with a fake PTY)**

```go
package session_test

import (
	"context"
	"io"
	"log/slog"
	"sync"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/stretchr/testify/require"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/metrics"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/session"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/turn"
)

type fakePTY struct {
	mu      sync.Mutex
	written []byte
}

func (f *fakePTY) Write(p []byte) (int, error) { f.mu.Lock(); defer f.mu.Unlock(); f.written = append(f.written, p...); return len(p), nil }
func (f *fakePTY) bytes() []byte                { f.mu.Lock(); defer f.mu.Unlock(); return append([]byte(nil), f.written...) }
func (f *fakePTY) Close() error                 { return nil }

func newMgr(t *testing.T, fp *fakePTY) (*session.Manager, *turn.Store) {
	t.Helper()
	store := turn.NewStore()
	ids := make(chan string, 8)
	ids <- "turn-1"
	ids <- "turn-2"
	m := session.New(session.Config{TurnTimeout: 50 * time.Millisecond, SubmitSeq: session.DefaultSubmitSeq},
		store, metrics.New(prometheus.NewRegistry()),
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		func() time.Time { return time.Unix(100, 0) },
		func() string { return <-ids })
	m.SetWriterForTest(fp) // injects fake PTY, marks READY
	return m, store
}

func TestSubmit_WritesPasteAndSubmit_ThenBusy(t *testing.T) {
	fp := &fakePTY{}
	m, store := newMgr(t, fp)

	id, err := m.Submit("hello\nworld", "https://cb/x")
	require.NoError(t, err)
	require.Equal(t, "turn-1", id)

	w := string(fp.bytes())
	require.Contains(t, w, "\x1b[200~hello\nworld\x1b[201~") // bracketed paste
	require.Contains(t, w, "\r")                              // submit
	rec, _ := store.Get("turn-1")
	require.Equal(t, turn.Running, rec.State)

	// second submit while busy -> ErrBusy
	_, err = m.Submit("again", "")
	require.ErrorIs(t, err, session.ErrBusy)
}

func TestComplete_MarksDoneAndFiresCallback(t *testing.T) {
	fp := &fakePTY{}
	m, store := newMgr(t, fp)
	var got *turn.Record
	m.OnTurnDone = func(r *turn.Record) { got = r }

	_, _ = m.Submit("hi", "https://cb/x")
	require.NoError(t, m.Complete(session.HookResult{FinalText: "PONG", StopReason: "end_turn"}))

	rec, _ := store.Get("turn-1")
	require.Equal(t, turn.Complete, rec.State)
	require.Equal(t, "PONG", rec.FinalText)
	require.NotNil(t, got)
	require.Equal(t, "https://cb/x", got.CallbackURL)

	// now idle again -> next submit allowed
	_, err := m.Submit("next", "")
	require.NoError(t, err)
}

func TestTurnTimeout_FailsAndFiresCallback(t *testing.T) {
	fp := &fakePTY{}
	m, store := newMgr(t, fp)
	done := make(chan *turn.Record, 1)
	m.OnTurnDone = func(r *turn.Record) { done <- r }

	_, _ = m.Submit("hi", "https://cb/x")
	select {
	case r := <-done:
		require.Equal(t, turn.Failed, r.State)
	case <-time.After(time.Second):
		t.Fatal("timeout did not fire")
	}
	rec, _ := store.Get("turn-1")
	require.Equal(t, turn.Failed, rec.State)
}

func TestStart_RealPTYWithCat(t *testing.T) {
	// integration-ish: drive /bin/cat under a real PTY, confirm bytes flow.
	store := turn.NewStore()
	m := session.New(session.Config{ClaudePath: "/bin/cat", TurnTimeout: time.Second, SubmitSeq: session.DefaultSubmitSeq, BootTimeout: time.Second},
		store, metrics.New(prometheus.NewRegistry()),
		slog.New(slog.NewTextHandler(io.Discard, nil)),
		time.Now, func() string { return "t" })
	require.NoError(t, m.Start(context.Background()))
	require.NoError(t, m.Shutdown(context.Background()))
}
```

- [ ] **Step 2: Run, verify it fails**

Run: `go test ./internal/session/ -run TestSubmit -v`
Expected: FAIL (undefined `session`).

- [ ] **Step 3: Implement `pty.go` (process + PTY seam)**

```go
package session

import (
	"fmt"
	"io"
	"os"
	"os/exec"

	"github.com/creack/pty"
)

// ptyWriter is the seam the Manager writes turns into. Real impl is a PTY
// master; tests substitute a fake.
type ptyWriter interface {
	io.Writer
	Close() error
}

type claudeProc struct {
	cmd  *exec.Cmd
	ptmx *os.File
}

func spawnClaude(cfg Config) (*claudeProc, error) {
	args := cfg.claudeArgs()
	cmd := exec.Command(cfg.ClaudePath, args...)
	cmd.Dir = cfg.Workspace
	cmd.Env = cfg.Env
	ptmx, err := pty.Start(cmd)
	if err != nil {
		return nil, fmt.Errorf("pty start claude: %w", err)
	}
	_ = pty.Setsize(ptmx, &pty.Winsize{Rows: 50, Cols: 200})
	return &claudeProc{cmd: cmd, ptmx: ptmx}, nil
}

func (c *claudeProc) Write(p []byte) (int, error) { return c.ptmx.Write(p) }
func (c *claudeProc) Close() error                { return c.ptmx.Close() }
```

- [ ] **Step 4: Implement `session.go` (state machine)**

```go
// Package session supervises one interactive claude process over a PTY and
// turns API submissions into typed turns, correlating Stop-hook callbacks.
package session

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"sync"
	"time"

	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/metrics"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/turn"
)

// DefaultSubmitSeq wraps a message in bracketed paste, then submits with CR.
// Confirmed/overridden by the Task-1 spike.
var DefaultSubmitSeq = SubmitSequence{PasteStart: "\x1b[200~", PasteEnd: "\x1b[201~", Submit: "\r"}

type SubmitSequence struct{ PasteStart, PasteEnd, Submit string }

func (s SubmitSequence) encode(text string) []byte {
	return []byte(s.PasteStart + text + s.PasteEnd + s.Submit)
}

var ErrBusy = errors.New("session busy")

type Config struct {
	ClaudePath  string
	Workspace   string
	Env         []string
	Model       string
	TurnTimeout time.Duration
	BootTimeout time.Duration
	SubmitSeq   SubmitSequence
}

// claudeArgs builds the interactive launch flags. Per the Task-1 spike, we
// pass NO --permission-mode / --dangerously-skip-permissions flag: bypass is
// configured via settings.defaultMode (bootstrap), and the flag would trigger
// an extra "Bypass Permissions" warning dialog. MCP comes from the cwd
// .mcp.json + enableAllProjectMcpServers, so no --mcp-config flag either.
func (c Config) claudeArgs() []string {
	args := []string{}
	if c.Model != "" {
		args = append(args, "--model", c.Model)
	}
	return args
}

// HookResult is the payload cc-stop-hook POSTs to the internal endpoint.
type HookResult struct {
	SessionID  string          `json:"sessionId"`
	FinalText  string          `json:"finalText"`
	ResultJSON json.RawMessage `json:"resultJson,omitempty"`
	Usage      json.RawMessage `json:"usage,omitempty"`
	StopReason string          `json:"stopReason"`
}

type State string

const (
	Booting State = "booting"
	Ready   State = "ready"
	Busy    State = "busy"
	Dead    State = "dead"
)

type Snapshot struct {
	State          State  `json:"state"`
	TurnsCompleted int    `json:"turnsCompleted"`
	Model          string `json:"model"`
	Repo           string `json:"repo"`
}

type Manager struct {
	cfg   Config
	store *turn.Store
	m     *metrics.Metrics
	log   *slog.Logger
	now   func() time.Time
	newID func() string

	OnTurnDone func(*turn.Record)

	mu             sync.Mutex
	w              ptyWriter
	proc           *claudeProc
	state          State
	current        string // in-flight turn id, "" when idle
	currentStarted time.Time
	timer          *time.Timer
	turnsCompleted int
	transcriptPath string
}

func New(cfg Config, store *turn.Store, m *metrics.Metrics, log *slog.Logger, now func() time.Time, newID func() string) *Manager {
	if cfg.BootTimeout <= 0 {
		cfg.BootTimeout = 60 * time.Second
	}
	return &Manager{cfg: cfg, store: store, m: m, log: log, now: now, newID: newID, state: Booting}
}

// SetWriterForTest injects a writer and marks the session READY. Test-only.
func (mgr *Manager) SetWriterForTest(w ptyWriter) {
	mgr.mu.Lock()
	defer mgr.mu.Unlock()
	mgr.w = w
	mgr.state = Ready
}

// Start spawns claude, drains its PTY output, and marks READY after boot.
func (mgr *Manager) Start(ctx context.Context) error {
	proc, err := spawnClaude(mgr.cfg)
	if err != nil {
		return err
	}
	mgr.mu.Lock()
	mgr.proc, mgr.w = proc, proc
	mgr.mu.Unlock()

	go func() { _, _ = io.Copy(io.Discard, proc.ptmx) }() // drain TUI; debug ring buffer is a later enhancement
	go mgr.watch(proc)

	// Readiness: spike-confirmed heuristic. Provisional: bounded boot delay.
	time.Sleep(minDuration(2*time.Second, mgr.cfg.BootTimeout))
	mgr.mu.Lock()
	if mgr.state == Booting {
		mgr.state = Ready
	}
	mgr.mu.Unlock()
	mgr.log.Info("session ready")
	return nil
}

func (mgr *Manager) watch(proc *claudeProc) {
	err := proc.cmd.Wait()
	mgr.mu.Lock()
	mgr.state = Dead
	mgr.mu.Unlock()
	mgr.m.ClaudeRestarts.Inc()
	mgr.log.Error("claude exited", "err", err)
}

func (mgr *Manager) Submit(text, callbackURL string) (string, error) {
	mgr.mu.Lock()
	defer mgr.mu.Unlock()
	if mgr.state == Dead {
		return "", fmt.Errorf("session dead")
	}
	if mgr.state == Booting {
		return "", fmt.Errorf("session not ready")
	}
	if mgr.current != "" {
		return "", ErrBusy
	}
	id := mgr.newID()
	now := mgr.now()
	mgr.store.Create(id, text, callbackURL, now)
	if _, err := mgr.w.Write(mgr.cfg.SubmitSeq.encode(text)); err != nil {
		_ = mgr.store.Fail(id, fmt.Sprintf("write pty: %v", err), now)
		return "", fmt.Errorf("write pty: %w", err)
	}
	mgr.current, mgr.currentStarted, mgr.state = id, now, Busy
	mgr.m.TurnInFlight.Set(1)
	mgr.timer = time.AfterFunc(mgr.cfg.TurnTimeout, func() { mgr.failTimeout(id) })
	mgr.log.Info("turn submitted", "turn_id", id)
	return id, nil
}

// Complete is invoked from the internal endpoint when a Stop hook fires.
func (mgr *Manager) Complete(r HookResult) error {
	mgr.mu.Lock()
	id := mgr.current
	if id == "" {
		mgr.mu.Unlock()
		return fmt.Errorf("no in-flight turn")
	}
	if mgr.timer != nil {
		mgr.timer.Stop()
	}
	if r.SessionID != "" {
		// transcript path can be derived elsewhere; session id recorded in logs
	}
	now := mgr.now()
	_ = mgr.store.Complete(id, r.FinalText, r.ResultJSON, r.Usage, r.StopReason, now)
	mgr.clearCurrentLocked()
	mgr.m.HookReceived.Inc()
	mgr.m.TurnsTotal.WithLabelValues("complete").Inc()
	mgr.m.TurnDuration.Observe(now.Sub(mgr.currentStarted).Seconds())
	rec, _ := mgr.store.Get(id)
	mgr.mu.Unlock()
	mgr.log.Info("turn complete", "turn_id", id, "duration_ms", now.Sub(rec.StartedAt).Milliseconds())
	mgr.fireDone(rec)
	return nil
}

func (mgr *Manager) failTimeout(id string) {
	mgr.mu.Lock()
	if mgr.current != id {
		mgr.mu.Unlock()
		return
	}
	now := mgr.now()
	_ = mgr.store.Fail(id, "turn timed out", now)
	mgr.clearCurrentLocked()
	mgr.m.TurnsTotal.WithLabelValues("failed").Inc()
	rec, _ := mgr.store.Get(id)
	mgr.mu.Unlock()
	mgr.log.Warn("turn timed out", "turn_id", id)
	mgr.fireDone(rec)
}

func (mgr *Manager) clearCurrentLocked() {
	mgr.current = ""
	mgr.turnsCompleted++
	mgr.state = Ready
	mgr.m.TurnInFlight.Set(0)
}

func (mgr *Manager) fireDone(rec *turn.Record) {
	if mgr.OnTurnDone != nil && rec != nil {
		mgr.OnTurnDone(rec)
	}
}

func (mgr *Manager) Snapshot() Snapshot {
	mgr.mu.Lock()
	defer mgr.mu.Unlock()
	return Snapshot{State: mgr.state, TurnsCompleted: mgr.turnsCompleted, Model: mgr.cfg.Model, Repo: ""}
}

func (mgr *Manager) Alive() bool {
	mgr.mu.Lock()
	defer mgr.mu.Unlock()
	return mgr.state != Dead && mgr.state != Booting
}

func (mgr *Manager) TranscriptPath() string {
	mgr.mu.Lock()
	defer mgr.mu.Unlock()
	return mgr.transcriptPath
}

func (mgr *Manager) Shutdown(ctx context.Context) error {
	mgr.mu.Lock()
	w, proc := mgr.w, mgr.proc
	mgr.state = Dead
	mgr.mu.Unlock()
	if w != nil {
		_, _ = w.Write([]byte("\x03")) // Ctrl-C
		_ = w.Close()
	}
	if proc != nil {
		_ = proc.cmd.Process.Kill()
	}
	return nil
}

func minDuration(a, b time.Duration) time.Duration {
	if a < b {
		return a
	}
	return b
}
```

> Spike note: replace the `time.Sleep` readiness in `Start` with the
> spike-confirmed prompt-ready signal if one exists; and update
> `DefaultSubmitSeq` if the captured submit bytes differ.

- [ ] **Step 5: Run the session tests, then commit**

Run: `go test ./internal/session/ -race -count=1`
Expected: PASS (including the `/bin/cat` real-PTY smoke).

```bash
git add internal/session && git commit -m "feat: session manager (PTY spawn, submit, complete, timeout)"
```

---

## Task 8: cc-stop-hook binary — parse transcript, POST result

**Files:**
- Create: `cmd/cc-stop-hook/main.go`, `hook.go`, `transcript.go`
- Test: `cmd/cc-stop-hook/transcript_test.go`, `hook_test.go`
- Uses fixtures from Task 1 (`cmd/cc-stop-hook/testdata/`).

- [ ] **Step 1: Write the failing test (against the real captured transcript)**

```go
package main

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestLastAssistantText_FromRealTranscript(t *testing.T) {
	path := filepath.Join("testdata", "transcript.jsonl")
	if _, err := os.Stat(path); err != nil {
		t.Skip("spike fixture missing; run Task 1")
	}
	text, usage, err := lastAssistantText(path)
	require.NoError(t, err)
	require.NotEmpty(t, text)
	_ = usage // usage may be present; type is json.RawMessage
}

func TestBuildResult_FromHookPayload(t *testing.T) {
	payload, err := os.ReadFile(filepath.Join("testdata", "hook_payload.json"))
	if err != nil {
		t.Skip("spike fixture missing; run Task 1")
	}
	res, err := buildResult(payload, "/nonexistent/result.json")
	require.NoError(t, err)
	require.NotEmpty(t, res.FinalText)
}
```

Add a focused synthetic transcript test so the parser is covered even without
the spike fixture:

```go
func TestLastAssistantText_Synthetic(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "t.jsonl")
	lines := `{"type":"user","message":{"content":[{"type":"text","text":"hi"}]}}
{"type":"assistant","message":{"content":[{"type":"text","text":"first"}],"usage":{"output_tokens":1}}}
{"type":"assistant","message":{"content":[{"type":"thinking","thinking":"hmm"},{"type":"text","text":"final answer"}],"usage":{"output_tokens":2}}}
`
	require.NoError(t, os.WriteFile(p, []byte(lines), 0o644))
	text, usage, err := lastAssistantText(p)
	require.NoError(t, err)
	require.Equal(t, "final answer", text)
	require.JSONEq(t, `{"output_tokens":2}`, string(usage))
}
```

- [ ] **Step 2: Run, verify it fails**

Run: `go test ./cmd/cc-stop-hook/ -run TestLastAssistantText_Synthetic -v`
Expected: FAIL (undefined `lastAssistantText`).

- [ ] **Step 3: Implement `transcript.go`**

```go
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
)

type assistantLine struct {
	Type    string `json:"type"`
	Message struct {
		Content []struct {
			Type string `json:"type"`
			Text string `json:"text"`
		} `json:"content"`
		Usage json.RawMessage `json:"usage"`
	} `json:"message"`
}

// lastAssistantText returns the concatenated text blocks of the final
// assistant line in a JSONL transcript, plus its usage object.
func lastAssistantText(path string) (string, json.RawMessage, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", nil, fmt.Errorf("open transcript: %w", err)
	}
	defer func() { _ = f.Close() }()

	var lastText string
	var lastUsage json.RawMessage
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1024*1024), 16*1024*1024)
	for sc.Scan() {
		line := sc.Bytes()
		var al assistantLine
		if err := json.Unmarshal(line, &al); err != nil || al.Type != "assistant" {
			continue
		}
		text := ""
		for _, c := range al.Message.Content {
			if c.Type == "text" {
				text += c.Text
			}
		}
		if text != "" {
			lastText, lastUsage = text, al.Message.Usage
		}
	}
	if err := sc.Err(); err != nil {
		return "", nil, fmt.Errorf("scan transcript: %w", err)
	}
	return lastText, lastUsage, nil
}
```

- [ ] **Step 4: Implement `hook.go` and `main.go`**

```go
// hook.go
package main

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/session"
)

// hookPayload mirrors the real Stop-hook payload (Task-1 spike, v2.1.162).
// `last_assistant_message` carries the final text directly; there is NO
// `stop_reason` in the payload (it lives in the transcript).
type hookPayload struct {
	SessionID            string `json:"session_id"`
	TranscriptPath       string `json:"transcript_path"`
	LastAssistantMessage string `json:"last_assistant_message"`
}

// buildResult assembles the HookResult. FinalText comes from the payload's
// last_assistant_message (authoritative); the transcript is read only for
// `usage` (and as a fallback for text). Folds in /workspace/result.json if
// the agent wrote one.
func buildResult(payload []byte, resultJSONPath string) (session.HookResult, error) {
	var hp hookPayload
	if err := json.Unmarshal(payload, &hp); err != nil {
		return session.HookResult{}, fmt.Errorf("parse hook payload: %w", err)
	}
	res := session.HookResult{SessionID: hp.SessionID, FinalText: hp.LastAssistantMessage}
	if hp.TranscriptPath != "" {
		if text, usage, err := lastAssistantText(hp.TranscriptPath); err == nil {
			res.Usage = usage
			if res.FinalText == "" {
				res.FinalText = text
			}
		}
	}
	if b, err := os.ReadFile(resultJSONPath); err == nil && json.Valid(b) {
		res.ResultJSON = b
	}
	return res, nil
}
```

```go
// main.go — pure side effect: read stdin payload, POST result, always exit 0.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "cc-stop-hook:", err)
	}
	os.Exit(0) // never block or alter claude
}

func run() error {
	payload, err := io.ReadAll(os.Stdin)
	if err != nil {
		return fmt.Errorf("read stdin: %w", err)
	}
	internalURL := envOr("CCW_INTERNAL_URL", "http://127.0.0.1:8090/internal/turn-complete")
	resultPath := envOr("CCW_RESULT_JSON", "/workspace/result.json")

	res, err := buildResult(payload, resultPath)
	if err != nil {
		return err
	}
	body, err := json.Marshal(res)
	if err != nil {
		return fmt.Errorf("marshal result: %w", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, internalURL, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("new request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return fmt.Errorf("post result: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	return nil
}

func envOr(k, def string) string {
	if v, ok := os.LookupEnv(k); ok {
		return v
	}
	return def
}
```

- [ ] **Step 5: Run all hook tests, then commit**

Run: `go test ./cmd/cc-stop-hook/ -race -count=1` -> PASS (synthetic always;
fixture tests run if Task 1 fixtures present).

```bash
git add cmd/cc-stop-hook && git commit -m "feat: cc-stop-hook parses transcript and posts turn result"
```

---

## Task 9: httpapi — handlers, OIDC, internal endpoint

**Files:**
- Create: `internal/httpapi/api.go`, `messages.go`, `session.go`, `internal.go`
- Test: `internal/httpapi/messages_test.go`, `internal_test.go`

Handlers depend on a `SessionController` interface so they test with a fake.

- [ ] **Step 1: Write the failing test**

```go
package httpapi_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/require"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/httpapi"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/session"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/turn"
)

type fakeCtl struct {
	submitID  string
	submitErr error
	completed session.HookResult
}

func (f *fakeCtl) Submit(text, cb string) (string, error) { return f.submitID, f.submitErr }
func (f *fakeCtl) Complete(r session.HookResult) error     { f.completed = r; return nil }
func (f *fakeCtl) Snapshot() session.Snapshot              { return session.Snapshot{State: session.Ready} }
func (f *fakeCtl) TranscriptPath() string                  { return "" }
func (f *fakeCtl) Alive() bool                             { return true }
func (f *fakeCtl) Shutdown(context.Context) error          { return nil }

func newAPI(ctl httpapi.SessionController, store *turn.Store) *httpapi.API {
	return httpapi.New(httpapi.Deps{Ctl: ctl, Store: store}) // Verifier nil -> public router skips OIDC in test mode
}

func TestPostMessage_202(t *testing.T) {
	store := turn.NewStore()
	api := newAPI(&fakeCtl{submitID: "turn-9"}, store)
	body, _ := json.Marshal(map[string]string{"text": "hi", "callbackUrl": "https://cb/x"})
	req := httptest.NewRequest(http.MethodPost, "/v1/messages", bytes.NewReader(body))
	rec := httptest.NewRecorder()
	api.TestRouter().ServeHTTP(rec, req)
	require.Equal(t, http.StatusAccepted, rec.Code)
	require.Contains(t, rec.Body.String(), "turn-9")
}

func TestPostMessage_409WhenBusy(t *testing.T) {
	api := newAPI(&fakeCtl{submitErr: session.ErrBusy}, turn.NewStore())
	req := httptest.NewRequest(http.MethodPost, "/v1/messages", bytes.NewReader([]byte(`{"text":"x"}`)))
	rec := httptest.NewRecorder()
	api.TestRouter().ServeHTTP(rec, req)
	require.Equal(t, http.StatusConflict, rec.Code)
}

func TestGetMessage_404Then200(t *testing.T) {
	store := turn.NewStore()
	api := newAPI(&fakeCtl{}, store)

	req := httptest.NewRequest(http.MethodGet, "/v1/messages/none", nil)
	rec := httptest.NewRecorder()
	api.TestRouter().ServeHTTP(rec, req)
	require.Equal(t, http.StatusNotFound, rec.Code)

	store.Create("turn-1", "hi", "", timeZero())
	req = httptest.NewRequest(http.MethodGet, "/v1/messages/turn-1", nil)
	rec = httptest.NewRecorder()
	api.TestRouter().ServeHTTP(rec, req)
	require.Equal(t, http.StatusOK, rec.Code)
}

func TestInternalTurnComplete_CallsController(t *testing.T) {
	ctl := &fakeCtl{}
	api := newAPI(ctl, turn.NewStore())
	body, _ := json.Marshal(session.HookResult{FinalText: "PONG", StopReason: "end_turn"})
	req := httptest.NewRequest(http.MethodPost, "/internal/turn-complete", bytes.NewReader(body))
	rec := httptest.NewRecorder()
	api.InternalRouter().ServeHTTP(rec, req)
	require.Equal(t, http.StatusNoContent, rec.Code)
	require.Equal(t, "PONG", ctl.completed.FinalText)
}

var _ = errors.New // keep errors import if unused after edits
```

Add `timeZero()` helper (`func timeZero() time.Time { return time.Unix(0,0) }`,
import `time`).

- [ ] **Step 2: Run, verify it fails**

Run: `go test ./internal/httpapi/ -v`
Expected: FAIL (undefined `httpapi`).

- [ ] **Step 3: Implement `api.go`**

```go
// Package httpapi exposes the wrapper's public (OIDC) and internal (loopback)
// HTTP surfaces.
package httpapi

import (
	"context"
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/auth"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/session"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/turn"
)

// SessionController is the slice of session.Manager the API needs.
type SessionController interface {
	Submit(text, callbackURL string) (string, error)
	Complete(session.HookResult) error
	Snapshot() session.Snapshot
	TranscriptPath() string
	Alive() bool
	Shutdown(context.Context) error
}

type Deps struct {
	Ctl      SessionController
	Store    *turn.Store
	Verifier *auth.Verifier
	Log      *slog.Logger
	Registry *prometheus.Registry
}

type API struct {
	ctl   SessionController
	store *turn.Store
	v     *auth.Verifier
	log   *slog.Logger
	reg   *prometheus.Registry
}

func New(d Deps) *API {
	if d.Log == nil {
		d.Log = slog.Default()
	}
	return &API{ctl: d.Ctl, store: d.Store, v: d.Verifier, log: d.Log, reg: d.Registry}
}

// Router is the public surface: OIDC-gated /v1/* plus open operator endpoints.
func (a *API) Router() http.Handler {
	r := chi.NewRouter()
	r.Group(func(pr chi.Router) {
		if a.v != nil {
			pr.Use(auth.Middleware(a.v))
		}
		a.mountV1(pr)
	})
	r.Get("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	r.Get("/readyz", a.readyz)
	if a.reg != nil {
		r.Handle("/metrics", promhttp.HandlerFor(a.reg, promhttp.HandlerOpts{}))
	}
	return r
}

// TestRouter is the public surface without OIDC, for handler unit tests.
func (a *API) TestRouter() http.Handler {
	r := chi.NewRouter()
	a.mountV1(r)
	r.Get("/readyz", a.readyz)
	return r
}

func (a *API) mountV1(r chi.Router) {
	r.Post("/v1/messages", a.postMessage)
	r.Get("/v1/messages", a.listMessages)
	r.Get("/v1/messages/{turnID}", a.getMessage)
	r.Get("/v1/session", a.getSession)
	r.Get("/v1/transcript", a.getTranscript)
	r.Delete("/v1/session", a.deleteSession)
}

// InternalRouter is the loopback-only surface the Stop hook posts to.
func (a *API) InternalRouter() http.Handler {
	r := chi.NewRouter()
	r.Post("/internal/turn-complete", a.turnComplete)
	return r
}

func (a *API) readyz(w http.ResponseWriter, _ *http.Request) {
	if a.ctl.Alive() {
		w.WriteHeader(http.StatusOK)
		return
	}
	http.Error(w, "not ready", http.StatusServiceUnavailable)
}
```

- [ ] **Step 4: Implement `messages.go`**

```go
package httpapi

import (
	"encoding/json"
	"errors"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/session"
)

type postMessageReq struct {
	Text        string `json:"text"`
	CallbackURL string `json:"callbackUrl"`
}

func (a *API) postMessage(w http.ResponseWriter, r *http.Request) {
	var req postMessageReq
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Text == "" {
		http.Error(w, "text is required", http.StatusBadRequest)
		return
	}
	id, err := a.ctl.Submit(req.Text, req.CallbackURL)
	if errors.Is(err, session.ErrBusy) {
		http.Error(w, "session busy", http.StatusConflict)
		return
	}
	if err != nil {
		http.Error(w, err.Error(), http.StatusServiceUnavailable)
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]string{"turnId": id})
}

func (a *API) listMessages(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, a.store.List())
}

func (a *API) getMessage(w http.ResponseWriter, r *http.Request) {
	rec, ok := a.store.Get(chi.URLParam(r, "turnID"))
	if !ok {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	writeJSON(w, http.StatusOK, rec)
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}
```

- [ ] **Step 5: Implement `session.go` and `internal.go`**

```go
// session.go
package httpapi

import (
	"net/http"
	"os"
)

func (a *API) getSession(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, a.ctl.Snapshot())
}

func (a *API) getTranscript(w http.ResponseWriter, _ *http.Request) {
	p := a.ctl.TranscriptPath()
	if p == "" {
		http.Error(w, "no transcript yet", http.StatusNotFound)
		return
	}
	b, err := os.ReadFile(p)
	if err != nil {
		http.Error(w, "transcript unavailable", http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/x-ndjson")
	_, _ = w.Write(b)
}

func (a *API) deleteSession(w http.ResponseWriter, r *http.Request) {
	_ = a.ctl.Shutdown(r.Context())
	w.WriteHeader(http.StatusAccepted)
}
```

```go
// internal.go
package httpapi

import (
	"encoding/json"
	"net/http"

	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/session"
)

func (a *API) turnComplete(w http.ResponseWriter, r *http.Request) {
	var res session.HookResult
	if err := json.NewDecoder(r.Body).Decode(&res); err != nil {
		http.Error(w, "bad payload", http.StatusBadRequest)
		return
	}
	if err := a.ctl.Complete(res); err != nil {
		http.Error(w, err.Error(), http.StatusConflict)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
```

- [ ] **Step 6: Run all httpapi tests, then commit**

Run: `go test ./internal/httpapi/ -race -count=1` -> PASS

```bash
git add internal/httpapi && git commit -m "feat: httpapi handlers, OIDC gate, loopback internal endpoint"
```

---

## Task 10: cmd/wrapper — config, app wiring, main

**Files:**
- Create: `cmd/wrapper/config.go`, `app.go`, `main.go`
- Test: `cmd/wrapper/config_test.go`

- [ ] **Step 1: Write the failing config test**

```go
package main

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestLoadConfig_Defaults(t *testing.T) {
	cfg, err := loadConfig(nil)
	require.NoError(t, err)
	require.Equal(t, ":8080", cfg.HTTPAddr)
	require.Equal(t, "127.0.0.1:8090", cfg.InternalAddr)
	require.Equal(t, "tatara-claude-code-wrapper", cfg.OIDCAudience)
	require.Equal(t, "bypassPermissions", cfg.PermissionMode)
	require.Equal(t, 1800, cfg.TurnTimeoutSeconds)
	require.Equal(t, 3, cfg.WebhookRetries)
}

func TestLoadConfig_EnvOverride(t *testing.T) {
	t.Setenv("HTTP_ADDR", ":9000")
	t.Setenv("TURN_TIMEOUT_SECONDS", "42")
	cfg, err := loadConfig(nil)
	require.NoError(t, err)
	require.Equal(t, ":9000", cfg.HTTPAddr)
	require.Equal(t, 42, cfg.TurnTimeoutSeconds)
}
```

- [ ] **Step 2: Run, verify it fails**

Run: `go test ./cmd/wrapper/ -run TestLoadConfig -v`
Expected: FAIL (undefined `loadConfig`).

- [ ] **Step 3: Implement `config.go`**

```go
package main

import (
	"flag"
	"fmt"
	"os"
	"strconv"
)

type config struct {
	HTTPAddr           string
	InternalAddr       string
	OIDCIssuer         string
	OIDCAudience       string
	LogLevel           string
	Model              string
	PermissionMode     string
	RepoURL            string
	RepoBranch         string
	DefaultCallbackURL string
	TurnTimeoutSeconds int
	BootTimeoutSeconds int
	WebhookRetries     int
	Workspace          string
	HomeDir            string
	ClaudePath         string
	HookPath           string
	GlobalClaudeMdPath string
	ProjectClaudeMdPath string
	MCPBasePath        string
	MCPOverlayDir      string
	SkillsSrcDirs      string // colon-separated
	AllowedToolsPath   string
}

func loadConfig(args []string) (config, error) {
	ti, err := envIntOr("TURN_TIMEOUT_SECONDS", 1800)
	if err != nil {
		return config{}, err
	}
	bt, err := envIntOr("BOOT_TIMEOUT_SECONDS", 60)
	if err != nil {
		return config{}, err
	}
	wr, err := envIntOr("WEBHOOK_RETRIES", 3)
	if err != nil {
		return config{}, err
	}
	cfg := config{
		HTTPAddr:            envOr("HTTP_ADDR", ":8080"),
		InternalAddr:        envOr("INTERNAL_ADDR", "127.0.0.1:8090"),
		OIDCIssuer:          envOr("OIDC_ISSUER", "https://auth.szymonrichert.pl/realms/master"),
		OIDCAudience:        envOr("OIDC_AUDIENCE", "tatara-claude-code-wrapper"),
		LogLevel:            envOr("LOG_LEVEL", "info"),
		Model:               envOr("MODEL", ""),
		PermissionMode:      envOr("PERMISSION_MODE", "bypassPermissions"),
		RepoURL:             envOr("REPO_URL", ""),
		RepoBranch:          envOr("REPO_BRANCH", ""),
		DefaultCallbackURL:  envOr("DEFAULT_CALLBACK_URL", ""),
		TurnTimeoutSeconds:  ti,
		BootTimeoutSeconds:  bt,
		WebhookRetries:      wr,
		Workspace:           envOr("WORKSPACE", "/workspace"),
		HomeDir:             envOr("HOME_DIR", os.Getenv("HOME")),
		ClaudePath:          envOr("CLAUDE_PATH", "claude"),
		HookPath:            envOr("HOOK_PATH", "/usr/local/bin/cc-stop-hook"),
		GlobalClaudeMdPath:  envOr("GLOBAL_CLAUDE_MD_PATH", "/etc/wrapper/global-claude.md"),
		ProjectClaudeMdPath: envOr("PROJECT_CLAUDE_MD_PATH", "/etc/wrapper/project-claude.md"),
		MCPBasePath:         envOr("MCP_BASE_PATH", "/etc/wrapper/mcp-base.json"),
		MCPOverlayDir:       envOr("MCP_OVERLAY_DIR", "/etc/wrapper/mcp.d"),
		SkillsSrcDirs:       envOr("SKILLS_SRC_DIRS", "/templates/skills:/etc/wrapper/skills"),
		AllowedToolsPath:    envOr("ALLOWED_TOOLS_PATH", "/etc/wrapper/allowed-tools.txt"),
	}
	fs := flag.NewFlagSet("wrapper", flag.ContinueOnError)
	fs.StringVar(&cfg.HTTPAddr, "http-addr", cfg.HTTPAddr, "public HTTP listen address")
	fs.StringVar(&cfg.InternalAddr, "internal-addr", cfg.InternalAddr, "loopback internal listen address")
	if err := fs.Parse(args); err != nil {
		return config{}, err
	}
	return cfg, nil
}

func envOr(k, def string) string {
	if v, ok := os.LookupEnv(k); ok {
		return v
	}
	return def
}

func envIntOr(k string, def int) (int, error) {
	v, ok := os.LookupEnv(k)
	if !ok {
		return def, nil
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		return 0, fmt.Errorf("env %s: %w", k, err)
	}
	return n, nil
}
```

- [ ] **Step 4: Implement `app.go` (wiring) and `main.go`**

```go
// app.go
package main

import (
	"context"
	"log/slog"
	"net"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/auth"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/bootstrap"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/httpapi"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/metrics"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/obs"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/session"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/turn"
	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/webhook"
)

type app struct {
	log      *slog.Logger
	pub      *http.Server
	internal *http.Server
	sess     *session.Manager
}

func newApp(ctx context.Context, cfg config) (*app, error) {
	log := obs.NewLogger(os.Stdout, parseLevel(cfg.LogLevel))
	reg := obs.PromRegistry()
	m := metrics.New(reg)

	if err := bootstrap.Render(buildBootstrapParams(cfg), gitRunner(cfg.Workspace)); err != nil {
		return nil, err
	}

	store := turn.NewStore()
	sess := session.New(session.Config{
		ClaudePath:  cfg.ClaudePath, Workspace: cfg.Workspace,
		Env:         claudeEnv(cfg),
		Model:       cfg.Model,
		TurnTimeout: time.Duration(cfg.TurnTimeoutSeconds) * time.Second,
		BootTimeout: time.Duration(cfg.BootTimeoutSeconds) * time.Second,
		SubmitSeq:   session.DefaultSubmitSeq,
	}, store, m, log, time.Now, newTurnID)

	sender := webhook.New(webhook.Config{Retries: cfg.WebhookRetries}, m, log)
	defaultCB := cfg.DefaultCallbackURL
	sess.OnTurnDone = func(rec *turn.Record) {
		url := rec.CallbackURL
		if url == "" {
			url = defaultCB
		}
		sender.Deliver(context.Background(), url, rec)
	}

	if err := sess.Start(ctx); err != nil {
		return nil, err
	}

	var verifier *auth.Verifier
	v, err := auth.NewVerifier(ctx, auth.Config{Issuer: cfg.OIDCIssuer, Audience: cfg.OIDCAudience})
	if err != nil {
		return nil, err
	}
	verifier = v

	api := httpapi.New(httpapi.Deps{Ctl: sess, Store: store, Verifier: verifier, Log: log, Registry: reg})
	return &app{
		log:      log,
		sess:     sess,
		pub:      &http.Server{Addr: cfg.HTTPAddr, Handler: api.Router(), ReadHeaderTimeout: 10 * time.Second},
		internal: &http.Server{Addr: cfg.InternalAddr, Handler: api.InternalRouter(), ReadHeaderTimeout: 10 * time.Second},
	}, nil
}

func (a *app) run() error {
	errCh := make(chan error, 2)
	go func() { errCh <- a.internal.ListenAndServe() }()
	go func() { errCh <- a.pub.ListenAndServe() }()
	return <-errCh
}

func (a *app) shutdown(ctx context.Context) error {
	_ = a.sess.Shutdown(ctx)
	_ = a.internal.Shutdown(ctx)
	return a.pub.Shutdown(ctx)
}

func buildBootstrapParams(cfg config) bootstrap.Params {
	return bootstrap.Params{
		HomeDir: cfg.HomeDir, Workspace: cfg.Workspace,
		GlobalClaudeMd:  readFileOrEmpty(cfg.GlobalClaudeMdPath),
		ProjectClaudeMd: readFileOrEmpty(cfg.ProjectClaudeMdPath),
		BaseMCP:         readBytesOrDefault(cfg.MCPBasePath, []byte(`{"mcpServers":{}}`)),
		MCPOverlayDir:   cfg.MCPOverlayDir,
		SkillsSrc:       strings.Split(cfg.SkillsSrcDirs, ":"),
		HookCommand:     cfg.HookPath,
		AllowedTools:    readLines(cfg.AllowedToolsPath),
		EnableAllMCP:    true,
		PermissionMode:  cfg.PermissionMode,
		AnthropicAPIKey: os.Getenv("ANTHROPIC_API_KEY"),
		RepoURL:         cfg.RepoURL, RepoBranch: cfg.RepoBranch,
	}
}
```

```go
// helpers in app.go (same file or a small util.go)
func parseLevel(s string) slog.Level {
	switch strings.ToLower(s) {
	case "debug":
		return slog.LevelDebug
	case "warn":
		return slog.LevelWarn
	case "error":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}

func claudeEnv(cfg config) []string {
	env := append(os.Environ(), "TERM=xterm-256color")
	if cfg.HomeDir != "" {
		env = append(env, "HOME="+cfg.HomeDir)
	}
	return env
}

func gitRunner(dir string) bootstrap.GitRunner {
	return func(args ...string) error {
		cmd := exec.Command("git", args...)
		cmd.Dir = dir
		out, err := cmd.CombinedOutput()
		if err != nil {
			return fmt.Errorf("git %v: %v: %w", args, string(out), err)
		}
		return nil
	}
}

func readFileOrEmpty(p string) string {
	b, err := os.ReadFile(p)
	if err != nil {
		return ""
	}
	return string(b)
}
func readBytesOrDefault(p string, def []byte) []byte {
	b, err := os.ReadFile(p)
	if err != nil {
		return def
	}
	return b
}
func readLines(p string) []string {
	b, err := os.ReadFile(p)
	if err != nil {
		return nil
	}
	var out []string
	for _, ln := range strings.Split(string(b), "\n") {
		if s := strings.TrimSpace(ln); s != "" {
			out = append(out, s)
		}
	}
	return out
}

func newTurnID() string { return "turn-" + strconv.FormatInt(time.Now().UnixNano(), 36) }
```

(Add imports: `os/exec`, `fmt`, `strconv` to app.go.)

```go
// main.go
package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/szymonrychu/tatara-claude-code-wrapper/internal/version"
)

func run(ctx context.Context, args []string) error {
	cfg, err := loadConfig(args)
	if err != nil {
		return err
	}
	a, err := newApp(ctx, cfg)
	if err != nil {
		return err
	}
	a.log.Info("starting", "version", version.Version, "addr", cfg.HTTPAddr)

	errCh := make(chan error, 1)
	go func() { errCh <- a.run() }()

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGTERM, syscall.SIGINT)
	select {
	case err := <-errCh:
		return err
	case <-sig:
	case <-ctx.Done():
	}
	a.log.Info("shutdown")
	sctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return a.shutdown(sctx)
}

func main() {
	if err := run(context.Background(), os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
```

- [ ] **Step 5: Run config test + build, then commit**

Run: `go test ./cmd/wrapper/ -run TestLoadConfig -count=1 && go build ./...`
Expected: PASS + clean build.

```bash
git add cmd/wrapper && git commit -m "feat: wrapper config + app wiring + main"
```

---

## Task 11: end-to-end integration test with a stub claude

Drives the FULL loop with no Anthropic key: a stub "claude" reads the PTY,
appends an assistant line to a transcript, and invokes the real built
`cc-stop-hook`, which POSTs to the running internal server.

**Files:**
- Create: `cmd/wrapper/testdata/stubclaude/main.go`
- Test: `cmd/wrapper/integration_test.go` (build tag `integration`)

- [ ] **Step 1: Write the stub claude**

```go
//go:build ignore

// stub claude: reads bracketed-paste blocks from stdin (the PTY slave); for
// each submitted message, appends an assistant line to $STUB_TRANSCRIPT and
// runs $STUB_HOOK with a synthetic hook payload on its stdin.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strings"
)

func main() {
	tp := os.Getenv("STUB_TRANSCRIPT")
	hook := os.Getenv("STUB_HOOK")
	fmt.Println("stub-claude ready") // readiness marker
	sc := bufio.NewScanner(os.Stdin)
	for sc.Scan() {
		line := sc.Text()
		msg := strings.TrimSuffix(strings.TrimPrefix(line, "\x1b[200~"), "\x1b[201~")
		if msg == "" {
			continue
		}
		f, _ := os.OpenFile(tp, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
		al := map[string]any{"type": "assistant", "message": map[string]any{
			"content": []map[string]any{{"type": "text", "text": "echo:" + msg}},
			"usage":   map[string]any{"output_tokens": 1}}}
		b, _ := json.Marshal(al)
		_, _ = f.Write(append(b, '\n'))
		_ = f.Close()

		payload, _ := json.Marshal(map[string]any{
			"session_id": "stub", "transcript_path": tp, "stop_reason": "end_turn"})
		c := exec.Command(hook)
		c.Stdin = strings.NewReader(string(payload))
		c.Env = os.Environ()
		_ = c.Run()
	}
}
```

- [ ] **Step 2: Write the integration test**

```go
//go:build integration

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestEndToEnd_SubmitTurn_GetResult(t *testing.T) {
	dir := t.TempDir()
	// build stub claude + cc-stop-hook
	stub := filepath.Join(dir, "claude")
	require.NoError(t, exec.Command("go", "build", "-o", stub, "./testdata/stubclaude").Run())
	hook := filepath.Join(dir, "cc-stop-hook")
	require.NoError(t, exec.Command("go", "build", "-o", hook, "../cc-stop-hook").Run())
	transcript := filepath.Join(dir, "t.jsonl")

	t.Setenv("CLAUDE_PATH", stub)
	t.Setenv("HOOK_PATH", hook)
	t.Setenv("WORKSPACE", dir)
	t.Setenv("HOME_DIR", dir)
	t.Setenv("HTTP_ADDR", "127.0.0.1:18080")
	t.Setenv("INTERNAL_ADDR", "127.0.0.1:18090")
	t.Setenv("STUB_TRANSCRIPT", transcript)
	t.Setenv("STUB_HOOK", hook)
	t.Setenv("CCW_INTERNAL_URL", "http://127.0.0.1:18090/internal/turn-complete")
	t.Setenv("OIDC_ISSUER", "") // skip OIDC: see note below

	go func() { _ = run(context.Background(), nil) }()
	requireUp(t, "http://127.0.0.1:18080/readyz")

	body, _ := json.Marshal(map[string]string{"text": "hello"})
	resp, err := http.Post("http://127.0.0.1:18080/v1/messages", "application/json", bytes.NewReader(body))
	require.NoError(t, err)
	require.Equal(t, http.StatusAccepted, resp.StatusCode)
	var pm struct{ TurnID string `json:"turnId"` }
	require.NoError(t, json.NewDecoder(resp.Body).Decode(&pm))

	require.Eventually(t, func() bool {
		r, err := http.Get("http://127.0.0.1:18080/v1/messages/" + pm.TurnID)
		if err != nil || r.StatusCode != 200 {
			return false
		}
		var rec struct {
			State     string `json:"state"`
			FinalText string `json:"finalText"`
		}
		_ = json.NewDecoder(r.Body).Decode(&rec)
		return rec.State == "complete" && rec.FinalText == "echo:hello"
	}, 10*time.Second, 100*time.Millisecond)
}

func requireUp(t *testing.T, url string) {
	t.Helper()
	require.Eventually(t, func() bool {
		r, err := http.Get(url)
		return err == nil && r.StatusCode == 200
	}, 10*time.Second, 100*time.Millisecond)
}
```

> OIDC in integration: `newApp` currently always builds a verifier. Add a
> guard so an empty `OIDCIssuer` skips OIDC (verifier nil -> `Router` mounts
> `/v1` without the middleware). Make that change in `app.go` as part of this
> task:
> ```go
> var verifier *auth.Verifier
> if cfg.OIDCIssuer != "" {
>     v, err := auth.NewVerifier(ctx, auth.Config{Issuer: cfg.OIDCIssuer, Audience: cfg.OIDCAudience})
>     if err != nil { return nil, err }
>     verifier = v
> }
> ```
> Production always sets `OIDC_ISSUER`, so the gate is always on in the
> cluster. Document this in README (OIDC is mandatory in deployment).

- [ ] **Step 3: Run the integration test**

Run: `go test ./cmd/wrapper/ -tags integration -run TestEndToEnd -count=1 -v`
Expected: PASS — turn submitted, stub echoes, hook posts, store shows
`complete` + `echo:hello`.

- [ ] **Step 4: Commit**

```bash
git add cmd/wrapper && git commit -m "test: end-to-end turn loop with stub claude + real hook"
```

---

## Task 12: Dockerfile (modular, claude bumpable) + bake skill

**Files:**
- Create: `Dockerfile`, `templates/skills/handoff/SKILL.md`

- [ ] **Step 1: Bake the default handoff skill**

```bash
mkdir -p templates/skills/handoff
cp ../../tatara-old/images/agent-claude/skills/handoff/SKILL.md templates/skills/handoff/SKILL.md
```

(If the old file references spawn-MCP `continue_with_handoff`, trim those
lines — this wrapper has no spawn MCP. Keep the doc-writing guidance.)

- [ ] **Step 2: Write the Dockerfile**

```dockerfile
# syntax=docker/dockerfile:1.7

ARG GO_VERSION=1.25
ARG NODE_VERSION=22
ARG CLAUDE_CODE_VERSION=latest
ARG TATARA_CLI_VERSION=latest

# Stage 1: build the Go binaries (cached independently of the claude layer).
FROM golang:${GO_VERSION}-alpine AS go-build
WORKDIR /src
RUN apk add --no-cache git ca-certificates
COPY go.mod go.sum ./
RUN go mod download
COPY . .
ARG VERSION=dev
ARG COMMIT=unknown
ARG DATE=unknown
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath \
      -ldflags "-s -w -X github.com/szymonrychu/tatara-claude-code-wrapper/internal/version.Version=${VERSION} -X github.com/szymonrychu/tatara-claude-code-wrapper/internal/version.Commit=${COMMIT} -X github.com/szymonrychu/tatara-claude-code-wrapper/internal/version.Date=${DATE}" \
      -o /out/wrapper ./cmd/wrapper && \
    CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags "-s -w" -o /out/cc-stop-hook ./cmd/cc-stop-hook

# Stage 2: pull the tatara-cli binary at a pinned version.
FROM harbor.szymonrichert.pl/containers/tatara-cli:${TATARA_CLI_VERSION} AS tatara-cli

# Stage 3: runtime — node + claude in their own layer for trivial bumps.
FROM node:${NODE_VERSION}-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# claude lives in its OWN layer: bumping CLAUDE_CODE_VERSION rebuilds only this.
ARG CLAUDE_CODE_VERSION
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} && npm cache clean --force

COPY --from=tatara-cli /usr/local/bin/tatara /usr/local/bin/tatara
COPY --from=go-build /out/wrapper /usr/local/bin/wrapper
COPY --from=go-build /out/cc-stop-hook /usr/local/bin/cc-stop-hook
COPY templates/ /templates/

# non-root, writable HOME + workspace
RUN useradd -m -u 10001 agent && mkdir -p /workspace && chown -R agent:agent /workspace /templates
USER agent
ENV HOME=/home/agent HOME_DIR=/home/agent WORKSPACE=/workspace
WORKDIR /workspace
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/wrapper"]
```

> If the pinned `tatara-cli` image path/tag differs, adjust Stage 2. Confirm
> the tatara-cli image exists at `harbor.szymonrichert.pl/containers/tatara-cli`
> (it ships from phase 2).

- [ ] **Step 3: Build locally to validate (no push)**

Run: `make image CLAUDE_CODE_VERSION=latest TATARA_CLI_VERSION=latest`
Expected: image builds. (If no Docker locally, defer to CI; note it.)

- [ ] **Step 4: Commit**

```bash
git add Dockerfile templates && git commit -m "feat: modular Dockerfile (claude in its own layer) + baked handoff skill"
```

---

## Task 13: Helm chart

**Files:**
- Create: `charts/tatara-claude-code-wrapper/` via `helm create`, then trim to:
  `Chart.yaml`, `values.yaml`, `templates/_helpers.tpl`, `configmap.yaml`,
  `configmap-files.yaml`, `secret.yaml`, `deployment.yaml`, `service.yaml`,
  `networkpolicy.yaml`, `servicemonitor.yaml`, `serviceaccount.yaml`
- Test: `charts/tatara-claude-code-wrapper/tests/*.yaml` (helm-unittest)

- [ ] **Step 1: Generate and trim**

```bash
cd charts && helm create tatara-claude-code-wrapper && cd ..
rm -rf charts/tatara-claude-code-wrapper/templates/{hpa.yaml,ingress.yaml,tests,NOTES.txt}
rm -f charts/tatara-claude-code-wrapper/templates/serviceaccount.yaml # regenerate minimal below if needed
```

- [ ] **Step 2: `values.yaml`** (camelCase scalars; lists/multiline are
  separate file-shaped values rendered into a mounted ConfigMap)

```yaml
image:
  repository: harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper
  tag: ""
  pullPolicy: IfNotPresent
imagePullSecrets:
  - name: regcred
replicaCount: 1

httpAddr: ":8080"
internalAddr: "127.0.0.1:8090"
oidcIssuer: "https://auth.szymonrichert.pl/realms/master"
oidcAudience: "tatara-claude-code-wrapper"
logLevel: "info"
model: ""
permissionMode: "bypassPermissions"
repoUrl: ""
repoBranch: ""
defaultCallbackUrl: ""
turnTimeoutSeconds: 1800
bootTimeoutSeconds: 60
webhookRetries: 3

# file-shaped inputs rendered into the files ConfigMap, mounted at /etc/wrapper
globalClaudeMd: ""
projectClaudeMd: ""
allowedTools: []          # list -> rendered to allowed-tools.txt, NOT env
extraMcpServers: {}        # map name->server json -> rendered into mcp.d/
baseMcp: |
  {"mcpServers":{"tatara-memory":{"type":"stdio","command":"tatara","args":["mcp"]}}}

# secrets via external Secret (sops); set existingSecret to override
existingSecret: ""
anthropicApiKeySecret: "tatara-claude-code-wrapper"
anthropicApiKeyKey: "anthropic-api-key"

service:
  type: ClusterIP
  port: 8080

serviceMonitor:
  enabled: true
  interval: "30s"
  scrapeTimeout: "10s"

networkPolicy:
  enabled: true
  # namespace label selector for allowed callers (e.g. argo)
  callerNamespace: "tatara"

resources: {}
persistence:
  enabled: false          # PVC for /workspace (survives restart); default emptyDir
  size: 5Gi
  storageClass: "rook-ceph-block"
```

- [ ] **Step 3: `_helpers.tpl` envConfig (UPPER_SNAKE keys)**

```
{{- define "tatara-claude-code-wrapper.envConfig" -}}
HTTP_ADDR: {{ .Values.httpAddr | quote }}
INTERNAL_ADDR: {{ .Values.internalAddr | quote }}
OIDC_ISSUER: {{ .Values.oidcIssuer | quote }}
OIDC_AUDIENCE: {{ .Values.oidcAudience | quote }}
LOG_LEVEL: {{ .Values.logLevel | quote }}
MODEL: {{ .Values.model | quote }}
PERMISSION_MODE: {{ .Values.permissionMode | quote }}
REPO_URL: {{ .Values.repoUrl | quote }}
REPO_BRANCH: {{ .Values.repoBranch | quote }}
DEFAULT_CALLBACK_URL: {{ .Values.defaultCallbackUrl | quote }}
TURN_TIMEOUT_SECONDS: {{ .Values.turnTimeoutSeconds | quote }}
BOOT_TIMEOUT_SECONDS: {{ .Values.bootTimeoutSeconds | quote }}
WEBHOOK_RETRIES: {{ .Values.webhookRetries | quote }}
GLOBAL_CLAUDE_MD_PATH: "/etc/wrapper/global-claude.md"
PROJECT_CLAUDE_MD_PATH: "/etc/wrapper/project-claude.md"
MCP_BASE_PATH: "/etc/wrapper/mcp-base.json"
MCP_OVERLAY_DIR: "/etc/wrapper/mcp.d"
SKILLS_SRC_DIRS: "/templates/skills:/etc/wrapper/skills"
ALLOWED_TOOLS_PATH: "/etc/wrapper/allowed-tools.txt"
{{- end -}}
```

(Keep the standard `helm create` name/fullname/labels helpers.)

- [ ] **Step 4: `configmap.yaml` (scalars) + `configmap-files.yaml`
  (file-shaped)**

```yaml
# configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "tatara-claude-code-wrapper.fullname" . }}
  labels: {{- include "tatara-claude-code-wrapper.labels" . | nindent 4 }}
data:
  {{- include "tatara-claude-code-wrapper.envConfig" . | nindent 2 }}
```

```yaml
# configmap-files.yaml — multiline/list/map data, mounted as files
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "tatara-claude-code-wrapper.fullname" . }}-files
  labels: {{- include "tatara-claude-code-wrapper.labels" . | nindent 4 }}
data:
  global-claude.md: |-
    {{- .Values.globalClaudeMd | nindent 4 }}
  project-claude.md: |-
    {{- .Values.projectClaudeMd | nindent 4 }}
  mcp-base.json: |-
    {{- .Values.baseMcp | nindent 4 }}
  allowed-tools.txt: |-
    {{- range .Values.allowedTools }}
    {{ . }}
    {{- end }}
  {{- range $name, $server := .Values.extraMcpServers }}
  mcp.d__{{ $name }}.json: |-
    {{- dict "mcpServers" (dict $name $server) | toJson | nindent 4 }}
  {{- end }}
```

> The `mcp.d__<name>.json` keys are projected to `/etc/wrapper/mcp.d/<name>.json`
> via a `items` mapping in the volume (Step 6), keeping list-shaped MCP data
> file-based per hard rule 6.

- [ ] **Step 5: `secret.yaml`**

```yaml
{{- if not .Values.existingSecret }}
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "tatara-claude-code-wrapper.fullname" . }}
  labels: {{- include "tatara-claude-code-wrapper.labels" . | nindent 4 }}
type: Opaque
data: {}
{{- end }}
```

(Real secret values come from a sops/external Secret named per
`anthropicApiKeySecret`; this stub exists so `envFrom.secretRef` resolves
when no external secret is set in non-prod.)

- [ ] **Step 6: `deployment.yaml`**

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "tatara-claude-code-wrapper.fullname" . }}
  labels: {{- include "tatara-claude-code-wrapper.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.replicaCount }}
  selector:
    matchLabels: {{- include "tatara-claude-code-wrapper.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      labels: {{- include "tatara-claude-code-wrapper.selectorLabels" . | nindent 8 }}
    spec:
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets: {{- toYaml . | nindent 8 }}
      {{- end }}
      containers:
        - name: wrapper
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          envFrom:
            - configMapRef:
                name: {{ include "tatara-claude-code-wrapper.fullname" . }}
            - secretRef:
                name: {{ default (include "tatara-claude-code-wrapper.fullname" .) .Values.existingSecret }}
          env:
            - name: ANTHROPIC_API_KEY
              valueFrom:
                secretKeyRef:
                  name: {{ .Values.anthropicApiKeySecret }}
                  key: {{ .Values.anthropicApiKeyKey }}
          ports:
            - name: http
              containerPort: 8080
          readinessProbe:
            httpGet: { path: /readyz, port: http }
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet: { path: /healthz, port: http }
            initialDelaySeconds: 10
            periodSeconds: 20
          volumeMounts:
            - name: files
              mountPath: /etc/wrapper
            - name: workspace
              mountPath: /workspace
          resources: {{- toYaml .Values.resources | nindent 12 }}
      volumes:
        - name: files
          configMap:
            name: {{ include "tatara-claude-code-wrapper.fullname" . }}-files
            items:
              - { key: global-claude.md, path: global-claude.md }
              - { key: project-claude.md, path: project-claude.md }
              - { key: mcp-base.json, path: mcp-base.json }
              - { key: allowed-tools.txt, path: allowed-tools.txt }
              {{- range $name, $server := .Values.extraMcpServers }}
              - { key: "mcp.d__{{ $name }}.json", path: "mcp.d/{{ $name }}.json" }
              {{- end }}
        - name: workspace
          {{- if .Values.persistence.enabled }}
          persistentVolumeClaim:
            claimName: {{ include "tatara-claude-code-wrapper.fullname" . }}
          {{- else }}
          emptyDir: {}
          {{- end }}
```

(Add `pvc.yaml` gated by `.Values.persistence.enabled`; standard cnpg-style
PVC like tatara-memory's `pvc.yaml`.)

- [ ] **Step 7: `service.yaml`, `networkpolicy.yaml`, `servicemonitor.yaml`,
  `serviceaccount.yaml`**

Service: ClusterIP exposing port 8080 -> http. NetworkPolicy: ingress to
8080 only from `callerNamespace`; egress allowed (claude needs the internet +
tatara-memory). ServiceMonitor gated by `serviceMonitor.enabled`, path
`/metrics`, port http. (Model these on tatara-memory's equivalents.)

- [ ] **Step 8: helm-unittest tests**

```yaml
# tests/configmap_test.yaml
suite: configmap env keys are UPPER_SNAKE
templates:
  - configmap.yaml
tests:
  - it: renders UPPER_SNAKE scalar keys
    asserts:
      - isKind: { of: ConfigMap }
      - matchRegex: { path: "data.OIDC_AUDIENCE", pattern: "tatara-claude-code-wrapper" }
      - exists: { path: "data.TURN_TIMEOUT_SECONDS" }
      - notExists: { path: "data.allowedTools" }
```

```yaml
# tests/deployment_test.yaml
suite: deployment wiring
templates:
  - deployment.yaml
  - configmap.yaml
  - configmap-files.yaml
  - secret.yaml
tests:
  - it: mounts files and injects ANTHROPIC_API_KEY
    asserts:
      - template: deployment.yaml
        contains:
          path: spec.template.spec.containers[0].volumeMounts
          content: { name: files, mountPath: /etc/wrapper }
      - template: deployment.yaml
        contains:
          path: spec.template.spec.containers[0].env
          content:
            name: ANTHROPIC_API_KEY
            valueFrom:
              secretKeyRef: { name: tatara-claude-code-wrapper, key: anthropic-api-key }
  - it: defaults workspace to emptyDir
    asserts:
      - template: deployment.yaml
        exists: { path: "spec.template.spec.volumes[?(@.name=='workspace')].emptyDir" }
```

```yaml
# tests/files_configmap_test.yaml
suite: file-shaped config never leaks into env configmap
templates:
  - configmap-files.yaml
tests:
  - it: renders extra mcp servers as mcp.d files
    set:
      extraMcpServers:
        tasks: { type: stdio, command: /bin/tasks }
    asserts:
      - exists: { path: "data.mcp\\.d__tasks\\.json" }
```

- [ ] **Step 9: Lint + unittest, then commit**

```bash
helm lint charts/tatara-claude-code-wrapper
make chart-test
```
Expected: lint clean; unittests PASS.

```bash
git add charts && git commit -m "feat: helm chart (UPPER_SNAKE env CM, file CM for lists/multiline, PVC opt-in)"
```

---

## Task 14: README, MEMORY, ROADMAP, Chart appVersion

**Files:**
- Create/modify: `README.md`, `MEMORY.md`, `ROADMAP.md`,
  `charts/tatara-claude-code-wrapper/Chart.yaml`

- [ ] **Step 1: Chart.yaml version + appVersion = 0.1.0**

Set `version: 0.1.0` and `appVersion: "0.1.0"`; description per the goal.

- [ ] **Step 2: README** documenting: purpose, the PTY/interactive-not-`-p`
  decision, API endpoints, config table (env + file-shaped), webhook-or-poll,
  OIDC mandatory in deployment, `make bump-claude`, the v0.1.0 limitations
  (claude crash loses context; no resume; 409-on-busy; ClusterIP only).

- [ ] **Step 3: MEMORY.md** seed entries (dated 2026-06-04): the PTY decision
  and why `-p` is forbidden; the spike-captured submit sequence + hook payload
  shape (reference `docs/spike-findings.md`); UPPER_SNAKE ConfigMap convention;
  claude-in-its-own-layer Dockerfile rationale.

- [ ] **Step 4: ROADMAP.md** with v0.2 items: `claude --resume` from
  transcript after crash; turn queueing; ingress + multi-pod router; SSE live
  streaming; Bedrock/Vertex.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "docs: README, MEMORY, ROADMAP, chart 0.1.0"
```

---

## Task 15: CI wiring (argo) + parent repo bookkeeping

**Files:**
- Modify (infra repo): `~/Documents/infra/helmfile/helmfiles/coding/values/argo-events/common.yaml`
- Modify (parent): `~/Documents/tatara/ROADMAP.md`, `MEMORY.md`, `ARCHITECTURE.md`

- [ ] **Step 1: Register the repo in the argo-events registry**

Add an entry mirroring the existing tatara-cli/tatara-memory ones so
`push_tag` triggers go-ci + container-build + helm-publish for
`tatara-claude-code-wrapper` (namespace `tatara`). Follow the exact shape
already in that file (read it first; do not invent keys).

- [ ] **Step 2: Register the GitHub webhook**

```bash
gh api repos/szymonrychu/tatara-claude-code-wrapper/hooks -X POST \
  -f config[url]=<argo-events ingress url> -f config[content_type]=json \
  -f events[]=push   # match how the siblings registered (see infra MEMORY)
```

(Exact URL/secret per the existing argo-events EventSource; copy the pattern
used for tatara-memory.)

- [ ] **Step 3: Update parent docs** — flip ROADMAP Phase 4 to
  `in progress`/`shipped` as appropriate, add a MEMORY entry, and note the
  component is no longer just "a container image" but a PTY-driven session
  API. Commit in the PARENT repo (separate from the child repo).

- [ ] **Step 4: Tag v0.1.0 once CI is green**

```bash
cd ~/Documents/tatara/tatara-claude-code-wrapper
git tag v0.1.0 && git push origin v0.1.0
```

Expected: argo runs go-ci -> container-build -> helm-publish; image + chart
land in harbor. Add the helm release in `~/Documents/infra/helmfile` (a
`tatara-claude-code-wrapper` release in the tatara namespace) and
`helmfile diff` before `apply`.

- [ ] **Step 5: Keycloak client precondition**

Before deploy, ensure the `tatara-claude-code-wrapper` Keycloak
client/audience exists (terraform in `~/Documents/infra/terraform/keycloak`,
mirror the tatara-memory client). `terraform plan` then `apply`. This is a
hard precondition for the OIDC gate.

---

## Self-Review

**Spec coverage:**
- Persistent multi-turn, one-per-pod, PTY interactive (no `-p`): Tasks 1, 7. ✓
- Stop hook posts result: Tasks 1, 8, 9 (internal endpoint). ✓
- Async submit + webhook + poll fallback; callbackUrl optional: Tasks 5, 9
  (`/v1/messages` GETs), 10 (default callback). ✓
- Repo optional / both: Task 6 (`cloneRepo`), Task 10 wiring. ✓
- OIDC bearer: Task 2 (copied auth), Task 9 (middleware), Task 11 (gate
  guard), Task 15 (Keycloak client). ✓
- Session config baked at pod start, eager boot: Task 10 + Task 13 (env CM). ✓
- Extra MCP servers: Task 6 (`mergeMCP`), Task 13 (`extraMcpServers` ->
  mcp.d files). ✓
- Skills incl. custom: Task 6 (`installSkills`), Task 12 (baked), Task 13
  (`SKILLS_SRC_DIRS` mount). ✓
- Global + project CLAUDE.md via config: Task 6, Task 13 (file CM). ✓
- Modular container, claude bumpable: Task 12 (own layer + ARG),
  Makefile `bump-claude`. ✓
- Observability (ccw_* metrics, slog): Tasks 4, 7, 9. ✓
- Failure modes (claude dies, turn timeout, webhook drop): Tasks 7
  (timeout, watch/Dead), 5 (drop). ✓ (claude crash = pod restart, documented;
  resume is roadmap.)
- Hard rule 6 (UPPER_SNAKE env CM, file CM for lists/multiline): Task 13. ✓

**Placeholder scan:** No `TBD`/`TODO`. Provisional values (submit sequence,
readiness heuristic) are explicitly bound to the Task-1 spike with "adjust to
captured reality" notes — these are de-risking steps, not placeholders.

**Type consistency:** `session.HookResult`, `turn.Record`/`turn.Store`
methods, `httpapi.SessionController`, `bootstrap.Params`/`GitRunner`,
`webhook.Config`/`Sender.Deliver`, `metrics.Metrics` fields are defined once
(Tasks 3-7) and referenced consistently in Tasks 8-13. `Manager.Complete`
signature matches the `SessionController` interface and the internal handler.

---

## Execution Notes

- **Models:** implementation subagents sonnet; the merge/integration review
  opus (hard rule 7). Task 1 (spike) and Task 11 (e2e) warrant opus review.
- **Branch flow:** worktree off the child repo `main`; merge back to child
  `main`; build/deploy only from `main` (hard rule 10).
- **Anthropic key** is needed ONLY for Task 1 (spike) and any manual cluster
  smoke. All unit + integration tests run without it (stub claude).
