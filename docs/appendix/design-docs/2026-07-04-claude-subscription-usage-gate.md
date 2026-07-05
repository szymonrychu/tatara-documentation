# Claude Subscription Usage Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the merged-but-inert `claudeSubscription` budget mode into a working proactive per-kind spawn gate driven by a standalone operator-side poller of Claude's account usage windows.

**Architecture:** A leader-elected operator Runnable polls `GET /api/oauth/usage` every >=180s using the fleet's existing OAuth token, parses the 5h / weekly / per-model / overage windows into a single fleet-wide in-memory snapshot (mirrored to a ConfigMap for restart durability), and the dispatcher admission gate holds each queued work item whose kind has crossed its configured account-usage ceiling. Native Claude Code OTel is enabled in agent pods as a documented cost producer + reactive 429 backstop. On poll failure the snapshot goes stale and the gate falls back to the existing `customWindow` path.

**Tech Stack:** Go 1.x (operator, controller-runtime, envtest), Prometheus client_golang, robfig/cron (already used by budget), Kubernetes ConfigMap, Claude Code native OpenTelemetry, Helm/Helmfile.

## Global Constraints

- Newest stable Go; pin exact minor in `go.mod` (repo rule 1).
- KISS; three similar lines beat a premature abstraction (repo rule 2).
- No plain ENVs / lists in `values.yaml`: camelCase scalar in `values.yaml` -> kebab-case ConfigMap/Secret key -> consumed via `envFrom`; genuinely list/map-shaped data renders into a templated ConfigMap (repo rule 6).
- JSON logs via stdlib `log/slog` (rule 11). Log every business action at INFO with structured fields (rule 12). Metrics for everything that counts/times-out/can-fail; `/metrics` endpoint (rule 13).
- Charts cluster-agnostic (rule 14). Deploy ONLY via tatara-helmfile GitOps, bumping BOTH chart version and `image.tag` (rule 15). Never build/deploy from a worktree (rule 10).
- Run Go tooling through `mise exec --` in the agent container per the repo toolchain section (e.g. `mise exec -- go test ./...`); bare `go` shown below for brevity and works where mise's Go is on PATH.
- Enum kept: `customWindow | claudeSubscription`. Human labels in docs/values only: `by-token-tracking = customWindow`, `claude-code-tracking = claudeSubscription`.
- Empty env / empty CRD scalar must fall to default, never crash (the `envboolor-empty-bootcrash` incident; wrapper `config.go:215-227`).
- OAuth token: Secret `cfg.AnthropicSecretName`, data key `oauth-token` (operator reads the SAME secret the pods mount, `pod.go:460`).
- `/api/oauth/usage` request headers (load-bearing): `Authorization: Bearer <token>`, `anthropic-beta: oauth-2025-04-20`, `User-Agent: claude-code/<pinned>`. Poll floor 180s. Undocumented: treat as pluggable, version-pin, schema-canary, fall back.
- Spec of record: `docs/superpowers/specs/2026-07-04-claude-subscription-usage-gate-design.md`.

---

## Task 0: SPIKE - verify the usage endpoint is reachable (BLOCKING, run before Task 3 merges/deploys)

Not a code task. Confirms the one assumption that can invalidate the poller: the exact auth header and that tatara's in-cluster setup-token can read `/api/oauth/usage`. The poller (Task 3) supports both auth modes via config, so this decides which to configure, not whether to build.

- [ ] **Step 1: Extract the OAuth token in-cluster (do NOT print it to shared logs).**

Run against the live cluster (operator namespace):
```bash
NS=<operator-namespace>
SECRET=$(kubectl -n "$NS" get deploy tatara-operator -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="CLAUDE_CODE_OAUTH_TOKEN")].valueFrom.secretKeyRef.name}')
# token stays in an env var, never echoed:
TOKEN=$(kubectl -n "$NS" get secret "$SECRET" -o jsonpath='{.data.oauth-token}' | base64 -d)
```

- [ ] **Step 2: Try the Bearer form first.**

```bash
curl -sS -o /tmp/usage.json -w "HTTP %{http_code}\n" \
  -H "Authorization: Bearer $TOKEN" \
  -H "anthropic-beta: oauth-2025-04-20" \
  -H "User-Agent: claude-code/1.0.0" \
  https://api.anthropic.com/api/oauth/usage
jq 'keys' /tmp/usage.json 2>/dev/null || cat /tmp/usage.json
```
Expected on success: HTTP 200 and JSON with keys including `five_hour`, `seven_day`. Record whether `utilization` is 0..1 or 0..100, and whether `seven_day_opus`/`seven_day_sonnet`/`extra_usage` are present or null.

- [ ] **Step 3: If Step 2 returns 401, try the x-api-key form.**

```bash
curl -sS -o /tmp/usage2.json -w "HTTP %{http_code}\n" \
  -H "x-api-key: $TOKEN" \
  -H "anthropic-beta: claude-code-20250219,oauth-2025-04-20" \
  -H "User-Agent: claude-cli/1.0.0" \
  -H "x-app: cli" \
  https://api.anthropic.com/api/oauth/usage
```

- [ ] **Step 4: Record the outcome in the spec + a memory.**

Note in `docs/superpowers/specs/2026-07-04-claude-subscription-usage-gate-design.md` under Risks: which auth header worked, the utilization scale (0..1 vs 0..100), which per-model/overage fields are present. This selects the `USAGE_AUTH_MODE` config value (Task 3/9) and confirms the normalize logic (Task 3).

**If the endpoint is unreachable with both forms:** stop and escalate. The proactive ladder cannot be built; fall back to the OTel-only degraded posture (Task 10) and re-brainstorm. Do not ship a poller that 401/429-storms the shared token.

---

## Phase A: tatara-operator (worktree `feat/usage-window-gating` off `origin/main`, already created)

### Task A1: per-kind ceiling logic in the `budget` package

**Files:**
- Modify: `internal/budget/budget.go`
- Test: `internal/budget/budget_test.go` (extend)

**Interfaces:**
- Consumes: existing `Config`, `Subscription`, `subscriptionUsedPercent`, `active`, `ModeClaudeSubscription`.
- Produces: `Config.SpawnCeilingByKind map[string]int`; `func KindBlocked(cfg Config, sub Subscription, kind string, now time.Time) bool`; extended `Subscription` fields `OpusPercent/OpusReset/SonnetPercent/SonnetReset/OverageEnabled/OveragePercent` (carried for metrics; NOT used by the gate in v1).

- [ ] **Step 1: Write failing tests.**

Add to `internal/budget/budget_test.go`:
```go
func TestKindBlocked(t *testing.T) {
	future := time.Now().Add(time.Hour)
	sub := Subscription{FiveHourPercent: 42, FiveHourReset: future, WeeklyPercent: 10, WeeklyReset: future}
	base := Config{Enabled: true, Mode: ModeClaudeSubscription,
		SpawnCeilingByKind: map[string]int{"brainstorm": 40, "incident": 98}}
	cases := []struct {
		name    string
		cfg     Config
		kind    string
		blocked bool
	}{
		{"brainstorm over ceiling", base, "brainstorm", true},   // 42 >= 40
		{"incident under ceiling", base, "incident", false},     // 42 < 98
		{"kind without ceiling not blocked", base, "implement", false},
		{"disabled never blocks", Config{Mode: ModeClaudeSubscription, SpawnCeilingByKind: base.SpawnCeilingByKind}, "brainstorm", false},
		{"customWindow mode never per-kind blocks", Config{Enabled: true, Mode: ModeCustomWindow, SpawnCeilingByKind: base.SpawnCeilingByKind}, "brainstorm", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := KindBlocked(tc.cfg, sub, tc.kind, time.Now()); got != tc.blocked {
				t.Fatalf("KindBlocked=%v want %v", got, tc.blocked)
			}
		})
	}
}

func TestKindBlockedIgnoresExpiredWindow(t *testing.T) {
	past := time.Now().Add(-time.Hour)
	sub := Subscription{FiveHourPercent: 99, FiveHourReset: past} // expired -> ignored
	cfg := Config{Enabled: true, Mode: ModeClaudeSubscription, SpawnCeilingByKind: map[string]int{"brainstorm": 40}}
	if KindBlocked(cfg, sub, "brainstorm", time.Now()) {
		t.Fatal("expired window must not block")
	}
}
```

- [ ] **Step 2: Run, verify fail.**

Run: `go test ./internal/budget/ -run TestKindBlocked -v`
Expected: FAIL (undefined: `KindBlocked`, unknown field `SpawnCeilingByKind`).

- [ ] **Step 3: Implement.**

In `internal/budget/budget.go`, add to `Config` (after `EmergencyPercent`):
```go
	// SpawnCeilingByKind gates each Task kind independently in claudeSubscription
	// mode: work of kind K is held once account usage reaches SpawnCeilingByKind[K]
	// percent. Kinds absent from the map are not per-kind gated (they fall through
	// to the pool-class proactive/emergency thresholds). Ignored in customWindow mode.
	SpawnCeilingByKind map[string]int
```
Extend `Subscription` (carried for metrics; gate still uses 5h/weekly max):
```go
	OpusPercent    float64
	OpusReset      time.Time
	SonnetPercent  float64
	SonnetReset    time.Time
	OverageEnabled bool
	OveragePercent float64
```
Add:
```go
// KindBlocked reports whether work of the given kind must be held, given the
// account subscription usage. It applies only in claudeSubscription mode with a
// configured per-kind ceiling; every other case returns false so the caller's
// pool-class Decision remains authoritative.
func KindBlocked(cfg Config, sub Subscription, kind string, now time.Time) bool {
	if !cfg.Enabled || cfg.Mode != ModeClaudeSubscription {
		return false
	}
	ceiling, ok := cfg.SpawnCeilingByKind[kind]
	if !ok || ceiling <= 0 {
		return false
	}
	return subscriptionUsedPercent(sub, now) >= float64(ceiling)
}
```

- [ ] **Step 4: Run, verify pass.**

Run: `go test ./internal/budget/ -v`
Expected: PASS (existing + new).

- [ ] **Step 5: Commit.**

```bash
git add internal/budget/budget.go internal/budget/budget_test.go
git commit -m "feat(budget): per-kind account-usage ceiling gate (KindBlocked)"
```

---

### Task A2: fleet-wide account-usage snapshot store

**Files:**
- Create: `internal/accountusage/store.go`
- Test: `internal/accountusage/store_test.go`

**Interfaces:**
- Produces: `type Snapshot`, `type Window struct{ Percent float64; Reset time.Time }`, `type Overage struct{ Enabled bool; Percent, Used, Limit float64 }`; `type Store` with `Get() Snapshot`, `Set(Snapshot)`; `func (Snapshot) Subscription() budget.Subscription`.
- Consumes: `internal/budget`.

- [ ] **Step 1: Write failing test.**

`internal/accountusage/store_test.go`:
```go
package accountusage

import (
	"testing"
	"time"
)

func TestStoreGetSetIsConcurrencySafeAndCopies(t *testing.T) {
	s := &Store{}
	if got := s.Get(); got.Healthy || !got.UpdatedAt.IsZero() {
		t.Fatal("zero store must be unhealthy/empty")
	}
	reset := time.Now().Add(time.Hour)
	s.Set(Snapshot{FiveHour: Window{Percent: 55, Reset: reset}, Healthy: true, UpdatedAt: time.Now()})
	got := s.Get()
	if got.FiveHour.Percent != 55 || !got.Healthy {
		t.Fatalf("Get mismatch: %+v", got)
	}
}

func TestSnapshotSubscriptionProjection(t *testing.T) {
	reset := time.Now().Add(time.Hour)
	snap := Snapshot{
		FiveHour: Window{Percent: 42, Reset: reset},
		Weekly:   Window{Percent: 71, Reset: reset},
		Opus:     Window{Percent: 80, Reset: reset},
	}
	sub := snap.Subscription()
	if sub.FiveHourPercent != 42 || sub.WeeklyPercent != 71 || sub.OpusPercent != 80 {
		t.Fatalf("projection mismatch: %+v", sub)
	}
	if !sub.FiveHourReset.Equal(reset) {
		t.Fatal("reset not projected")
	}
}
```

- [ ] **Step 2: Run, verify fail.** Run: `go test ./internal/accountusage/ -v` Expected: FAIL (package/types undefined).

- [ ] **Step 3: Implement `store.go`.**

```go
package accountusage

import (
	"sync"
	"time"

	"github.com/szymonrychu/tatara-operator/internal/budget"
)

// Window is one usage window: percent used (0..100) and when it resets.
type Window struct {
	Percent float64
	Reset   time.Time
}

// Overage is the read-only pay-as-you-go overage pool ("monthly").
type Overage struct {
	Enabled bool
	Percent float64
	Used    float64
	Limit   float64
}

// Snapshot is the fleet-wide account usage at a point in time.
type Snapshot struct {
	FiveHour  Window
	Weekly    Window
	Opus      Window // per-model weekly; zero when N/A on the plan
	Sonnet    Window
	Overage   Overage
	Healthy   bool
	UpdatedAt time.Time
}

// Subscription projects the snapshot into the budget gate's input type. The gate
// uses 5h + weekly; per-model + overage ride along for metrics only.
func (s Snapshot) Subscription() budget.Subscription {
	return budget.Subscription{
		FiveHourPercent: s.FiveHour.Percent,
		FiveHourReset:   s.FiveHour.Reset,
		WeeklyPercent:   s.Weekly.Percent,
		WeeklyReset:     s.Weekly.Reset,
		OpusPercent:     s.Opus.Percent,
		OpusReset:       s.Opus.Reset,
		SonnetPercent:   s.Sonnet.Percent,
		SonnetReset:     s.Sonnet.Reset,
		OverageEnabled:  s.Overage.Enabled,
		OveragePercent:  s.Overage.Percent,
	}
}

// Store holds the single fleet-wide snapshot, safe for concurrent reads by every
// project's admission reconcile and writes by the poller.
type Store struct {
	mu   sync.RWMutex
	snap Snapshot
}

func (s *Store) Get() Snapshot {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.snap
}

func (s *Store) Set(snap Snapshot) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.snap = snap
}
```

- [ ] **Step 4: Run, verify pass.** Run: `go test ./internal/accountusage/ -v` Expected: PASS.

- [ ] **Step 5: Commit.**
```bash
git add internal/accountusage/store.go internal/accountusage/store_test.go
git commit -m "feat(accountusage): fleet-wide usage snapshot store"
```

---

### Task A3: usage endpoint HTTP client + parse

**Files:**
- Create: `internal/accountusage/client.go`
- Test: `internal/accountusage/client_test.go`

**Interfaces:**
- Consumes: `Snapshot`, `Window`, `Overage` (Task A2).
- Produces: `type Client`, `func NewClient(cfg ClientConfig) *Client`, `func (*Client) Fetch(ctx context.Context) (Snapshot, error)`; `type ClientConfig struct{ BaseURL string; TokenSource func() (string, error); UserAgent string; AuthMode string /* "bearer"|"x-api-key" */; HTTP *http.Client }`.

- [ ] **Step 1: Write failing test with an httptest server.**

`internal/accountusage/client_test.go`:
```go
package accountusage

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

const sampleUsage = `{
  "five_hour": {"utilization": 42.5, "resets_at": "2999-01-01T00:00:00Z"},
  "seven_day": {"utilization": 71.0, "resets_at": "2999-01-02T00:00:00Z"},
  "seven_day_opus": {"utilization": 80.0, "resets_at": "2999-01-02T00:00:00Z"},
  "seven_day_sonnet": null,
  "extra_usage": {"is_enabled": true, "monthly_limit": 100, "used_credits": 25, "utilization": 25.0}
}`

func TestFetchParsesWindowsAndHeaders(t *testing.T) {
	var gotUA, gotAuth, gotBeta string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotUA = r.Header.Get("User-Agent")
		gotAuth = r.Header.Get("Authorization")
		gotBeta = r.Header.Get("anthropic-beta")
		w.Write([]byte(sampleUsage))
	}))
	defer srv.Close()

	c := NewClient(ClientConfig{
		BaseURL:     srv.URL,
		TokenSource: func() (string, error) { return "tok123", nil },
		UserAgent:   "claude-code/9.9.9",
		AuthMode:    "bearer",
	})
	snap, err := c.Fetch(context.Background())
	if err != nil {
		t.Fatalf("Fetch: %v", err)
	}
	if snap.FiveHour.Percent != 42.5 || snap.Weekly.Percent != 71.0 || snap.Opus.Percent != 80.0 {
		t.Fatalf("windows: %+v", snap)
	}
	if snap.Sonnet.Percent != 0 { // null -> zero window
		t.Fatalf("null sonnet must be zero, got %+v", snap.Sonnet)
	}
	if !snap.Overage.Enabled || snap.Overage.Percent != 25.0 {
		t.Fatalf("overage: %+v", snap.Overage)
	}
	if !snap.Healthy {
		t.Fatal("successful fetch must be Healthy")
	}
	if gotUA != "claude-code/9.9.9" || gotAuth != "Bearer tok123" || gotBeta != "oauth-2025-04-20" {
		t.Fatalf("headers UA=%q auth=%q beta=%q", gotUA, gotAuth, gotBeta)
	}
}

func TestFetchXAPIKeyMode(t *testing.T) {
	var gotKey, gotAuth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotKey = r.Header.Get("x-api-key")
		gotAuth = r.Header.Get("Authorization")
		w.Write([]byte(sampleUsage))
	}))
	defer srv.Close()
	c := NewClient(ClientConfig{BaseURL: srv.URL, TokenSource: func() (string, error) { return "k", nil }, UserAgent: "claude-cli/1", AuthMode: "x-api-key"})
	if _, err := c.Fetch(context.Background()); err != nil {
		t.Fatal(err)
	}
	if gotKey != "k" || gotAuth != "" {
		t.Fatalf("x-api-key mode: key=%q auth=%q", gotKey, gotAuth)
	}
}

func TestFetchNon200IsError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(429) }))
	defer srv.Close()
	c := NewClient(ClientConfig{BaseURL: srv.URL, TokenSource: func() (string, error) { return "k", nil }, UserAgent: "x", AuthMode: "bearer"})
	if _, err := c.Fetch(context.Background()); err == nil {
		t.Fatal("429 must error")
	}
}

func TestNormalizeUtilizationFraction(t *testing.T) {
	// utilization may be reported 0..1 (fraction) instead of 0..100; normalize.
	if got := normalizeUtil(0.42); got != 42.0 {
		t.Fatalf("fraction normalize: %v", got)
	}
	if got := normalizeUtil(42.0); got != 42.0 {
		t.Fatalf("percent passthrough: %v", got)
	}
}
```

- [ ] **Step 2: Run, verify fail.** Run: `go test ./internal/accountusage/ -run TestFetch -v` Expected: FAIL (undefined `NewClient`/`Client`/`normalizeUtil`).

- [ ] **Step 3: Implement `client.go`.**

```go
package accountusage

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

const (
	defaultBaseURL  = "https://api.anthropic.com"
	usagePath       = "/api/oauth/usage"
	anthropicBeta   = "oauth-2025-04-20"
	requestTimeout  = 15 * time.Second
)

type ClientConfig struct {
	BaseURL     string
	TokenSource func() (string, error)
	UserAgent   string
	AuthMode    string // "bearer" (default) or "x-api-key"
	HTTP        *http.Client
}

type Client struct {
	base  string
	token func() (string, error)
	ua    string
	auth  string
	http  *http.Client
}

func NewClient(cfg ClientConfig) *Client {
	base := cfg.BaseURL
	if base == "" {
		base = defaultBaseURL
	}
	h := cfg.HTTP
	if h == nil {
		h = &http.Client{Timeout: requestTimeout}
	}
	auth := cfg.AuthMode
	if auth == "" {
		auth = "bearer"
	}
	return &Client{base: base, token: cfg.TokenSource, ua: cfg.UserAgent, auth: auth, http: h}
}

type usageWindow struct {
	Utilization float64 `json:"utilization"`
	ResetsAt    string  `json:"resets_at"`
}

type usageResponse struct {
	FiveHour *usageWindow `json:"five_hour"`
	SevenDay *usageWindow `json:"seven_day"`
	Opus     *usageWindow `json:"seven_day_opus"`
	Sonnet   *usageWindow `json:"seven_day_sonnet"`
	Extra    *struct {
		Enabled     bool    `json:"is_enabled"`
		MonthlyLimit float64 `json:"monthly_limit"`
		UsedCredits  float64 `json:"used_credits"`
		Utilization  float64 `json:"utilization"`
	} `json:"extra_usage"`
}

func (c *Client) Fetch(ctx context.Context) (Snapshot, error) {
	tok, err := c.token()
	if err != nil {
		return Snapshot{}, fmt.Errorf("accountusage: token: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.base+usagePath, nil)
	if err != nil {
		return Snapshot{}, err
	}
	req.Header.Set("User-Agent", c.ua)
	req.Header.Set("Content-Type", "application/json")
	if c.auth == "x-api-key" {
		req.Header.Set("x-api-key", tok)
		req.Header.Set("anthropic-beta", "claude-code-20250219,"+anthropicBeta)
	} else {
		req.Header.Set("Authorization", "Bearer "+tok)
		req.Header.Set("anthropic-beta", anthropicBeta)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return Snapshot{}, fmt.Errorf("accountusage: request: %w", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<16))
	if resp.StatusCode != http.StatusOK {
		return Snapshot{}, fmt.Errorf("accountusage: status %d: %s", resp.StatusCode, string(body))
	}
	var ur usageResponse
	if err := json.Unmarshal(body, &ur); err != nil {
		return Snapshot{}, fmt.Errorf("accountusage: decode: %w", err)
	}
	if ur.FiveHour == nil && ur.SevenDay == nil {
		return Snapshot{}, fmt.Errorf("accountusage: schema drift: no five_hour/seven_day fields")
	}
	snap := Snapshot{Healthy: true, UpdatedAt: time.Now()}
	snap.FiveHour = toWindow(ur.FiveHour)
	snap.Weekly = toWindow(ur.SevenDay)
	snap.Opus = toWindow(ur.Opus)
	snap.Sonnet = toWindow(ur.Sonnet)
	if ur.Extra != nil {
		snap.Overage = Overage{Enabled: ur.Extra.Enabled, Percent: normalizeUtil(ur.Extra.Utilization), Used: ur.Extra.UsedCredits, Limit: ur.Extra.MonthlyLimit}
	}
	return snap, nil
}

func toWindow(w *usageWindow) Window {
	if w == nil {
		return Window{}
	}
	out := Window{Percent: normalizeUtil(w.Utilization)}
	if w.ResetsAt != "" {
		if t, err := time.Parse(time.RFC3339, w.ResetsAt); err == nil {
			out.Reset = t
		}
	}
	return out
}

// normalizeUtil coerces a utilization reported as a 0..1 fraction into 0..100.
// Confirm the actual scale in the Task 0 spike; this handles either.
func normalizeUtil(v float64) float64 {
	if v > 0 && v <= 1 {
		return v * 100
	}
	return v
}
```

- [ ] **Step 4: Run, verify pass.** Run: `go test ./internal/accountusage/ -v` Expected: PASS.

- [ ] **Step 5: Commit.**
```bash
git add internal/accountusage/client.go internal/accountusage/client_test.go
git commit -m "feat(accountusage): /api/oauth/usage client with dual auth + normalize"
```

---

### Task A4: poller Runnable (ticker + health degradation + ConfigMap mirror)

**Files:**
- Create: `internal/accountusage/poller.go`
- Test: `internal/accountusage/poller_test.go`

**Interfaces:**
- Consumes: `Client.Fetch`, `Store.Set`, controller-runtime `client.Client` for the ConfigMap mirror.
- Produces: `type Poller struct{ Fetcher interface{ Fetch(context.Context) (Snapshot, error) }; Store *Store; Interval time.Duration; FailureThreshold int; Now func() time.Time; onUpdate func(Snapshot) }`; `func (*Poller) Start(ctx context.Context) error`; `func (*Poller) NeedLeaderElection() bool { return true }`; `func (*Poller) pollOnce(ctx) ` (unexported, tested via one tick).

- [ ] **Step 1: Write failing test (fake fetcher, no real HTTP, no real timer).**

`internal/accountusage/poller_test.go`:
```go
package accountusage

import (
	"context"
	"errors"
	"testing"
	"time"
)

type fakeFetcher struct {
	snaps []Snapshot
	errs  []error
	i     int
}

func (f *fakeFetcher) Fetch(context.Context) (Snapshot, error) {
	defer func() { f.i++ }()
	if f.i < len(f.errs) && f.errs[f.i] != nil {
		return Snapshot{}, f.errs[f.i]
	}
	if f.i < len(f.snaps) {
		return f.snaps[f.i], nil
	}
	return Snapshot{}, errors.New("exhausted")
}

func TestPollOnceSuccessSetsHealthy(t *testing.T) {
	f := &fakeFetcher{snaps: []Snapshot{{FiveHour: Window{Percent: 33}, Healthy: true}}}
	st := &Store{}
	p := &Poller{Fetcher: f, Store: st, FailureThreshold: 3, Now: time.Now}
	p.pollOnce(context.Background())
	if got := st.Get(); !got.Healthy || got.FiveHour.Percent != 33 {
		t.Fatalf("store after success: %+v", got)
	}
}

func TestConsecutiveFailuresMarkStaleAfterThreshold(t *testing.T) {
	f := &fakeFetcher{
		snaps: []Snapshot{{FiveHour: Window{Percent: 50}, Healthy: true}},
		errs:  []error{nil, errors.New("x"), errors.New("x"), errors.New("x")},
	}
	st := &Store{}
	p := &Poller{Fetcher: f, Store: st, FailureThreshold: 3, Now: time.Now}
	p.pollOnce(context.Background()) // success -> healthy, keep last-known percent
	p.pollOnce(context.Background()) // fail 1 -> still healthy (last-known)
	if !st.Get().Healthy {
		t.Fatal("single failure must not mark stale")
	}
	p.pollOnce(context.Background()) // fail 2
	p.pollOnce(context.Background()) // fail 3 -> stale
	got := st.Get()
	if got.Healthy {
		t.Fatal("threshold failures must mark stale")
	}
	if got.FiveHour.Percent != 50 {
		t.Fatal("stale snapshot must retain last-known windows")
	}
}
```

- [ ] **Step 2: Run, verify fail.** Run: `go test ./internal/accountusage/ -run TestPoll -v` Expected: FAIL (undefined `Poller`).

- [ ] **Step 3: Implement `poller.go`.**

```go
package accountusage

import (
	"context"
	"log/slog"
	"time"
)

type fetcher interface {
	Fetch(context.Context) (Snapshot, error)
}

type Poller struct {
	Fetcher          fetcher
	Store            *Store
	Interval         time.Duration
	FailureThreshold int
	Now              func() time.Time
	// onUpdate, when set, is called after each successful poll (ConfigMap mirror +
	// metrics wired in Task A9). Kept as a hook so the ticker stays testable.
	onUpdate func(Snapshot)

	failures int
}

func (p *Poller) NeedLeaderElection() bool { return true }

func (p *Poller) Start(ctx context.Context) error {
	if p.Interval < 180*time.Second {
		p.Interval = 180 * time.Second
	}
	if p.Now == nil {
		p.Now = time.Now
	}
	p.pollOnce(ctx) // immediate first poll
	t := time.NewTicker(p.Interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-t.C:
			p.pollOnce(ctx)
		}
	}
}

func (p *Poller) pollOnce(ctx context.Context) {
	snap, err := p.Fetcher.Fetch(ctx)
	if err != nil {
		p.failures++
		slog.Warn("accountusage poll failed", "failures", p.failures, "error", err)
		if p.failures >= p.FailureThreshold {
			cur := p.Store.Get()
			cur.Healthy = false // keep last-known windows, mark stale
			p.Store.Set(cur)
		}
		return
	}
	p.failures = 0
	snap.Healthy = true
	snap.UpdatedAt = p.Now()
	p.Store.Set(snap)
	slog.Info("accountusage poll ok", "five_hour_pct", snap.FiveHour.Percent, "weekly_pct", snap.Weekly.Percent)
	if p.onUpdate != nil {
		p.onUpdate(snap)
	}
}
```

- [ ] **Step 4: Run, verify pass.** Run: `go test ./internal/accountusage/ -v` Expected: PASS.

- [ ] **Step 5: Commit.**
```bash
git add internal/accountusage/poller.go internal/accountusage/poller_test.go
git commit -m "feat(accountusage): leader-elected poller with stale degradation"
```

---

### Task A5: CRD fields (`spawnCeilingByKind`, `pollIntervalSeconds`, `monitorOverage`) + BudgetConfig layering

**Files:**
- Modify: `api/v1alpha1/project_types.go:431-505` (TokenBudgetSpec + BudgetConfig)
- Modify: `api/v1alpha1/zz_generated.deepcopy.go` (via `make generate`)
- Modify: `config/crd/bases/*.yaml` (via `make manifests`)
- Test: `api/v1alpha1/project_types_test.go` (extend or create)

**Interfaces:**
- Consumes: `budget.Config.SpawnCeilingByKind` (Task A1).
- Produces: `TokenBudgetSpec.SpawnCeilingByKind map[string]int32`, `.PollIntervalSeconds *int32`, `.MonitorOverage *bool`; `Project.BudgetConfig` copies `SpawnCeilingByKind` into `budget.Config`.

- [ ] **Step 1: Write failing test.**

Add to `api/v1alpha1/project_types_test.go`:
```go
func TestBudgetConfigCopiesSpawnCeilingByKind(t *testing.T) {
	p := &Project{Spec: ProjectSpec{TokenBudget: &TokenBudgetSpec{
		Enabled: true, Mode: "claudeSubscription",
		SpawnCeilingByKind: map[string]int32{"brainstorm": 40, "incident": 98},
	}}}
	cfg := p.BudgetConfig(budget.Config{})
	if cfg.SpawnCeilingByKind["brainstorm"] != 40 || cfg.SpawnCeilingByKind["incident"] != 98 {
		t.Fatalf("ceilings not copied: %+v", cfg.SpawnCeilingByKind)
	}
}
```

- [ ] **Step 2: Run, verify fail.** Run: `go test ./api/v1alpha1/ -run TestBudgetConfigCopies -v` Expected: FAIL (unknown field).

- [ ] **Step 3: Add fields + CEL, extend BudgetConfig, regenerate.**

In `TokenBudgetSpec` (after `TokenLimit`):
```go
	// SpawnCeilingByKind gates each Task kind independently in claudeSubscription
	// mode: work of kind K is held once account usage reaches the given percent.
	// Keys are Task kinds; kinds absent here fall through to proactive/emergency.
	// +kubebuilder:validation:XValidation:rule="self.all(k, self[k] >= 0 && self[k] <= 100)",message="spawnCeilingByKind values must be 0..100"
	// +kubebuilder:validation:XValidation:rule="self.all(k, k in ['implement','review','selfImprove','triageIssue','brainstorm','issueLifecycle','incident','healthCheck','refine'])",message="spawnCeilingByKind keys must be valid Task kinds"
	// +optional
	SpawnCeilingByKind map[string]int32 `json:"spawnCeilingByKind,omitempty"`
	// PollIntervalSeconds is how often the operator polls Claude account usage
	// (claudeSubscription mode). Floor 180 (enforced operator-side too).
	// +kubebuilder:validation:Minimum=180
	// +optional
	PollIntervalSeconds *int32 `json:"pollIntervalSeconds,omitempty"`
	// MonitorOverage surfaces the pay-as-you-go overage pool on dashboards. It is
	// read-only and never gates spawning.
	// +optional
	MonitorOverage *bool `json:"monitorOverage,omitempty"`
```
In `BudgetConfig`, before `return cfg`:
```go
	if len(s.SpawnCeilingByKind) > 0 {
		cfg.SpawnCeilingByKind = make(map[string]int, len(s.SpawnCeilingByKind))
		for k, v := range s.SpawnCeilingByKind {
			cfg.SpawnCeilingByKind[k] = int(v)
		}
	}
```
Then:
```bash
make generate && make manifests
```
Expected: `zz_generated.deepcopy.go` gains `SpawnCeilingByKind`/`PollIntervalSeconds`/`MonitorOverage` deepcopy; CRD YAML gains the fields + CEL rules.

- [ ] **Step 4: Run, verify pass.** Run: `go test ./api/v1alpha1/ -v` Expected: PASS. Also `go build ./...` Expected: clean.

- [ ] **Step 5: Commit.**
```bash
git add api/v1alpha1/ config/crd/
git commit -m "feat(api): spawnCeilingByKind/pollIntervalSeconds/monitorOverage on TokenBudgetSpec"
```

---

### Task A6: wire the per-kind gate into the dispatcher admission path

**Files:**
- Modify: `internal/controller/queue_controller.go:29-40` (DispatcherReconciler struct), `:84-205` (admit/admitPool), `:228-300` (Reconcile)
- Test: `internal/controller/queue_controller_test.go` (extend; envtest or unit on admit helper)

**Interfaces:**
- Consumes: `accountusage.Store` (Task A2), `budget.KindBlocked` (Task A1), `budget.Evaluate` (existing).
- Produces: `DispatcherReconciler.Usage *accountusage.Store` (new field); admit sources the subscription snapshot from the store in claudeSubscription mode and holds per kind.

**Design note:** In `claudeSubscription` mode the snapshot comes from `r.Usage.Get().Subscription()` (fleet-wide), NOT `proj.BudgetSubscription()` (per-project, retired in Task A8). `customWindow` mode is unchanged. Per-kind hold is added inside `admitPool`'s per-event loop; the existing pool-class `blocked` check remains for `customWindow` and for kinds without a ceiling.

- [ ] **Step 1: Write failing test.**

Add a focused unit test on the per-kind decision (extract a helper `r.blockKindFunc(proj, cfg, sub, now)` returning `func(kind string) bool`):
```go
func TestDispatcherBlockKindUsesStoreInSubscriptionMode(t *testing.T) {
	future := time.Now().Add(time.Hour)
	store := &accountusage.Store{}
	store.Set(accountusage.Snapshot{FiveHour: accountusage.Window{Percent: 60, Reset: future}, Healthy: true})
	r := &DispatcherReconciler{Usage: store}
	proj := &tatarav1alpha1.Project{Spec: tatarav1alpha1.ProjectSpec{TokenBudget: &tatarav1alpha1.TokenBudgetSpec{
		Enabled: true, Mode: "claudeSubscription",
		SpawnCeilingByKind: map[string]int32{"brainstorm": 40, "incident": 98},
	}}}
	cfg := proj.BudgetConfig(r.BudgetDefaults)
	block := r.blockKindFunc(proj, cfg, time.Now())
	if !block("brainstorm") { // 60 >= 40
		t.Fatal("brainstorm must be held at 60%")
	}
	if block("incident") { // 60 < 98
		t.Fatal("incident must not be held at 60%")
	}
}
```

- [ ] **Step 2: Run, verify fail.** Run: `go test ./internal/controller/ -run TestDispatcherBlockKind -v` Expected: FAIL (no `Usage` field / `blockKindFunc`).

- [ ] **Step 3: Implement.**

Add to `DispatcherReconciler` struct (`:29`):
```go
	// Usage is the fleet-wide Claude account usage snapshot (claudeSubscription
	// mode). Nil-safe: a nil store yields an empty snapshot (nothing per-kind held).
	Usage *accountusage.Store
```
Add the helper:
```go
// blockKindFunc returns a predicate that reports whether a queued event of a
// given kind must be held on the account usage gate. Subscription mode reads the
// fleet-wide store; other modes never per-kind block.
func (r *DispatcherReconciler) blockKindFunc(proj *tatarav1alpha1.Project, cfg budget.Config, now time.Time) func(string) bool {
	var sub budget.Subscription
	if r.Usage != nil {
		sub = r.Usage.Get().Subscription()
	}
	return func(kind string) bool {
		return budget.KindBlocked(cfg, sub, kind, now)
	}
}
```
In `Reconcile` (near `:272` where `decision` is computed), build the predicate and pass it to `admit`. Change `admit` signature to accept it:
```go
func (r *DispatcherReconciler) admit(ctx context.Context, proj *tatarav1alpha1.Project,
	qes []tatarav1alpha1.QueuedEvent, tasks []tatarav1alpha1.Task, d budget.Decision, blockKind func(string) bool) (requeue bool, err error) {
```
Inside `admitPool`, in the loop that iterates candidate events of the class, before admitting each event add:
```go
		if blockKind != nil && blockKind(kindOf(qe)) {
			continue // held on its per-kind account-usage ceiling; leave Queued
		}
```
where `kindOf(qe)` reads the event's Task kind (use the existing field the loop already has; if the event carries `qe.Spec.Kind` use it directly). Keep the existing pool-class `blocked` short-circuit for `customWindow`.

In `Reconcile`, in subscription mode source the decision's snapshot from the store so the pool-class `UsedPercent`/metrics still reflect the account (replace `proj.BudgetSubscription()` with the store snapshot when `cfg.Mode == budget.ModeClaudeSubscription`):
```go
	sub := proj.BudgetSubscription()
	if budgetCfg.Mode == budget.ModeClaudeSubscription && r.Usage != nil {
		sub = r.Usage.Get().Subscription()
	}
	decision := budget.Evaluate(budgetCfg, proj.BudgetWindowState(), sub, time.Now())
	...
	requeue, err := r.admit(ctx, proj, qes, tasks, decision, r.blockKindFunc(proj, budgetCfg, time.Now()))
```

- [ ] **Step 4: Run, verify pass.** Run: `go test ./internal/controller/ -run TestDispatcher -v` Expected: PASS. Then `go build ./...`.

- [ ] **Step 5: Commit.**
```bash
git add internal/controller/queue_controller.go internal/controller/queue_controller_test.go
git commit -m "feat(dispatcher): per-kind account-usage hold from fleet-wide store"
```

---

### Task A7: operator metrics for account usage + per-kind admission

**Files:**
- Modify: `internal/obs/operator_metrics.go`
- Test: `internal/obs/operator_metrics_test.go` (extend)

**Interfaces:**
- Produces: `Metrics.SetAccountUsage(window string, percent float64)`, `SetAccountUsageReset(window string, unix float64)`, `SetAccountUsagePollHealth(healthy bool)`, `SetAccountOverage(percent, used, limit float64)`; extend `AdmissionBlocked` with a `kind` label (or add `AdmissionHeldByKind(project, kind string)`).

- [ ] **Step 1: Write failing test.**
```go
func TestSetAccountUsageGauge(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := New(reg)
	m.SetAccountUsage("five_hour", 42.5)
	m.SetAccountUsagePollHealth(true)
	if v := testutil.ToFloat64(m.accountUsageUtil.WithLabelValues("five_hour")); v != 42.5 {
		t.Fatalf("gauge=%v", v)
	}
}
```

- [ ] **Step 2: Run, verify fail.** Run: `go test ./internal/obs/ -run TestSetAccountUsage -v` Expected: FAIL.

- [ ] **Step 3: Implement.** Declare and register in `New(reg)`:
```go
	accountUsageUtil = prometheus.NewGaugeVec(prometheus.GaugeOpts{
		Name: "tatara_account_usage_utilization",
		Help: "Claude account usage utilization percent (0..100) by window.",
	}, []string{"window"})
	accountUsageReset = prometheus.NewGaugeVec(prometheus.GaugeOpts{
		Name: "tatara_account_usage_resets_at_seconds", Help: "Unix time each usage window resets.",
	}, []string{"window"})
	accountUsagePollHealth = prometheus.NewGauge(prometheus.GaugeOpts{
		Name: "tatara_account_usage_poll_health", Help: "1 when the usage poll is healthy, 0 when stale.",
	})
```
Store on the `Metrics` struct, `MustRegister` them, add the setter methods. Extend `operator_admission_blocked_total` with a `kind` label (update the existing `AdmissionBlocked` callers to pass kind, defaulting to `""` for pool-class blocks).

- [ ] **Step 4: Run, verify pass.** Run: `go test ./internal/obs/ -v` Expected: PASS.

- [ ] **Step 5: Commit.**
```bash
git add internal/obs/operator_metrics.go internal/obs/operator_metrics_test.go
git commit -m "feat(metrics): account-usage gauges + per-kind admission label"
```

---

### Task A8: retire per-Project subscription persistence (cleanup)

**Files:**
- Modify: `internal/controller/turncallback.go` (drop `turnRateLimit` ingestion + `applyRateLimit`; keep `recordUsage` custom-window token accounting)
- Modify: `api/v1alpha1/project_types.go` (mark `TokenBudgetStatus.FiveHourPercent/FiveHourReset/WeeklyPercent/WeeklyReset` deprecated; remove `BudgetSubscription` use from the gate)
- Test: `internal/controller/turncallback_test.go` (remove/adjust rate-limit-path tests)

**Interfaces:**
- Removes: the wrapper->operator per-turn `RateLimit` snapshot path (never had a producer). Subscription state now lives only in the fleet-wide store.

- [ ] **Step 1: Adjust tests.** Delete tests asserting `applyRateLimit`/per-project 5h persistence; keep custom-window accumulation tests. Add a test asserting a turn-complete payload with a (now-ignored) `rateLimit` field does not error and does not write `Status.TokenBudget.FiveHourPercent`.

- [ ] **Step 2: Run, verify fail/adjust.** Run: `go test ./internal/controller/ -run TurnComplete -v`.

- [ ] **Step 3: Implement.** Remove `applyRateLimit` and the `rl *turnRateLimit` parameter from `updateProjectBudget`; drop the `RateLimit` field handling in the turn-complete decode (accept-and-ignore for wire compatibility). Leave `TokenBudgetStatus` subscription fields in place (CRD backward-compat) but stop writing them; add a one-line deprecation comment.

- [ ] **Step 4: Run, verify pass.** Run: `go test ./internal/controller/ -v`.

- [ ] **Step 5: Commit.**
```bash
git add internal/controller/turncallback.go api/v1alpha1/project_types.go internal/controller/turncallback_test.go
git commit -m "refactor(budget): retire per-project subscription snapshot (superseded by poller)"
```

---

### Task A9: manager wiring - config, secret, poller Runnable, ConfigMap mirror, store injection

**Files:**
- Modify: `cmd/manager/config.go` (new config: `UsageAuthMode`, `UsagePollInterval`, `UsageUserAgent`, `UsageBaseURL`; the operator already reads `AnthropicSecretName`)
- Modify: `cmd/manager/wire.go:157-280` (build store + client + poller, `mgr.Add`, inject `Usage` into DispatcherReconciler at `:208`)
- Create: `internal/accountusage/mirror.go` (ConfigMap read/write of the snapshot for restart durability)
- Test: `internal/accountusage/mirror_test.go` (fake client round-trip); `cmd/manager` wiring covered by `go build` + existing manager smoke test

**Interfaces:**
- Consumes: `accountusage.NewClient`, `accountusage.Poller`, `accountusage.Store`, controller-runtime client, `cfg.AnthropicSecretName`.
- Produces: a running poller updating the store; DispatcherReconciler `Usage` set.

- [ ] **Step 1: Write failing test for the ConfigMap mirror.**
```go
func TestMirrorRoundTrip(t *testing.T) {
	scheme := runtime.NewScheme()
	_ = corev1.AddToScheme(scheme)
	cl := fake.NewClientBuilder().WithScheme(scheme).Build()
	m := &Mirror{Client: cl, Namespace: "tatara", Name: "tatara-account-usage"}
	reset := time.Now().Add(time.Hour).Truncate(time.Second)
	want := Snapshot{FiveHour: Window{Percent: 61, Reset: reset}, Weekly: Window{Percent: 40, Reset: reset}, Healthy: true}
	if err := m.Save(context.Background(), want); err != nil {
		t.Fatal(err)
	}
	got, err := m.Load(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if got.FiveHour.Percent != 61 || !got.FiveHour.Reset.Equal(reset) {
		t.Fatalf("round-trip mismatch: %+v", got)
	}
}
```

- [ ] **Step 2: Run, verify fail.** Run: `go test ./internal/accountusage/ -run TestMirror -v` Expected: FAIL.

- [ ] **Step 3: Implement `mirror.go`** (JSON-encode the snapshot into a single ConfigMap data key `snapshot.json`; `Save` upserts, `Load` decodes, missing ConfigMap -> zero snapshot + nil error). Wire the config + poller in `wire.go`:
```go
	usageStore := &accountusage.Store{}
	usageMirror := &accountusage.Mirror{Client: mgr.GetClient(), Namespace: cfg.Namespace, Name: "tatara-account-usage"}
	if snap, err := usageMirror.Load(context.Background()); err == nil {
		snap.Healthy = false // restored state is stale until the first live poll
		usageStore.Set(snap)
	}
	usageClient := accountusage.NewClient(accountusage.ClientConfig{
		TokenSource: secretTokenSource(mgr.GetAPIReader(), cfg.Namespace, cfg.AnthropicSecretName, "oauth-token"),
		UserAgent:   cfg.UsageUserAgent, // e.g. "claude-code/<pinned>"
		AuthMode:    cfg.UsageAuthMode,  // "bearer" (default) or "x-api-key", from Task 0 spike
	})
	poller := &accountusage.Poller{
		Fetcher: usageClient, Store: usageStore,
		Interval: cfg.UsagePollInterval, FailureThreshold: 3, Now: time.Now,
	}
	poller.SetOnUpdate(func(s accountusage.Snapshot) {
		_ = usageMirror.Save(context.Background(), s)
		metrics.SetAccountUsage("five_hour", s.FiveHour.Percent)
		metrics.SetAccountUsage("seven_day", s.Weekly.Percent)
		metrics.SetAccountUsage("seven_day_opus", s.Opus.Percent)
		metrics.SetAccountUsage("seven_day_sonnet", s.Sonnet.Percent)
		metrics.SetAccountUsagePollHealth(s.Healthy)
	})
	if err := mgr.Add(poller); err != nil {
		return nil, fmt.Errorf("add usage poller: %w", err)
	}
```
Add `Usage: usageStore` to the `DispatcherReconciler{...}` literal at `:208`. Add a `SetOnUpdate` setter + `secretTokenSource` helper (reads the Secret data key via the API reader; returns `func() (string, error)`). Add the four new config fields to `cmd/manager/config.go` with defaults (`UsageAuthMode="bearer"`, `UsagePollInterval=180s`, `UsageUserAgent="claude-code/1.0.0"`), read via the existing env helpers.

- [ ] **Step 4: Run, verify pass + build.** Run: `go test ./internal/accountusage/ -v && go build ./...` Expected: PASS + clean.

- [ ] **Step 5: Commit.**
```bash
git add internal/accountusage/mirror.go internal/accountusage/mirror_test.go cmd/manager/
git commit -m "feat(manager): wire account-usage poller, store, ConfigMap mirror"
```

---

### Task A10: RBAC + operator smoke + full suite

**Files:**
- Modify: `config/rbac/role.yaml` (via kubebuilder marker) - operator needs `get`/`list`/`watch` on the OAuth Secret and `get`/`create`/`update` on ConfigMaps in its namespace.
- Modify: `internal/controller/queue_controller.go` (add `+kubebuilder:rbac` markers for configmaps/secrets if not already granted)

- [ ] **Step 1: Add RBAC markers** for `secrets` (get on the named OAuth secret) and `configmaps` (get;create;update) in the operator namespace. Run `make manifests`.
- [ ] **Step 2: Run the full unit suite.** Run: `go test ./... 2>&1 | tail -30` Expected: all PASS (controller envtest tests may need `make envtest`/`setup-envtest` binaries; if the baseline already ran them, they must stay green).
- [ ] **Step 3: Lint.** Run: `mise exec -- golangci-lint run ./...` Expected: clean.
- [ ] **Step 4: Commit.**
```bash
git add config/rbac/ internal/
git commit -m "chore(rbac): operator access to OAuth secret + account-usage ConfigMap"
```

---

## Phase B: tatara-claude-code-wrapper - native OTel backstop (separate worktree off `origin/main`)

**Worktree:** `.worktrees/otel-backstop` off `origin/main`, branch `feat/otel-usage-backstop`.

### Task B1: enable native Claude Code telemetry in the agent pod env

**Files:**
- Modify: `cmd/wrapper/app.go` (`claudeEnv`, ~`:460-466`) to append OTel env when enabled
- Modify: `cmd/wrapper/config.go` (`loadConfig`) - new `OTEL_ENABLED` bool + `OTEL_EXPORTER_OTLP_ENDPOINT` passthrough, respecting the empty-env-to-default rule
- Test: `cmd/wrapper/app_test.go` (assert env presence when enabled, absence when not)

- [ ] **Step 1: Write failing test** asserting `claudeEnv` contains `CLAUDE_CODE_ENABLE_TELEMETRY=1`, `OTEL_METRICS_EXPORTER=otlp`, `OTEL_LOGS_EXPORTER=otlp`, and the endpoint when `cfg.OtelEnabled` is true and the endpoint is set; and contains none of them when disabled.
- [ ] **Step 2: Run, verify fail.** Run: `go test ./cmd/wrapper/ -run Otel -v`.
- [ ] **Step 3: Implement.** In `loadConfig` add `OtelEnabled` (`envBoolOr("OTEL_ENABLED", false)`) and `OtelEndpoint` (`envOr("OTEL_EXPORTER_OTLP_ENDPOINT", "")`). In `claudeEnv`, when `cfg.OtelEnabled && cfg.OtelEndpoint != ""`, append `CLAUDE_CODE_ENABLE_TELEMETRY=1`, `OTEL_METRICS_EXPORTER=otlp`, `OTEL_LOGS_EXPORTER=otlp`, `OTEL_EXPORTER_OTLP_PROTOCOL=grpc`, `OTEL_EXPORTER_OTLP_ENDPOINT=<cfg.OtelEndpoint>`, `OTEL_METRIC_EXPORT_INTERVAL=60000`.
- [ ] **Step 4: Run, verify pass + build.** Run: `go test ./cmd/wrapper/ -v && go build ./...`.
- [ ] **Step 5: Commit.**
```bash
git add cmd/wrapper/
git commit -m "feat(wrapper): enable native Claude Code OTel export (cost/429 backstop)"
```

**Note:** The 429-reactive fleet-stop (consuming `claude_code.api_error{status_code=429}`) is an operator-side alert->incident rule handled in Phase C, not wrapper code.

---

## Phase C: tatara-observability - dashboards + alerts (separate worktree off `origin/main`)

**Worktree:** `.worktrees/usage-dashboards` off `origin/main`, branch `feat/usage-window-dashboards`.

### Task C1: usage-window dashboard + alert rules

**Files:**
- Create/modify: dashboard JSON under the repo's dashboards dir (follow existing panel-provisioning pattern).
- Create/modify: alert rules (the repo migrated alert RULES here; PR=plan, merge=apply, per memory `grafana-alerting-terraform-broken-contactpoints`). Rules need `homelab` + `system=tatara` labels.

- [ ] **Step 1: Add panels** for `tatara_account_usage_utilization{window=...}` (per-window gauge + time series), reset countdown from `tatara_account_usage_resets_at_seconds`, per-kind admission state from `operator_admission_blocked_total{kind=...}`, monthly overage read-only from the overage gauges, and real cost from `claude_code_cost_usage` (OTel).
- [ ] **Step 2: Add alert rules:** `tatara_account_usage_poll_health == 0` (poll stale), any `window` utilization above an emergency ceiling, `claude_code_api_error{status_code="429"}` rate > 0 (reactive backstop -> incident), overage climbing. Label each with `homelab` + `system=tatara`.
- [ ] **Step 3: Validate** dashboards render against the metric names from Task A7 (use the grafana MCP `get_dashboard_summary` / `query_prometheus` once metrics flow post-deploy).
- [ ] **Step 4: Commit + open PR** (this repo is agent-enrolled: PR = plan).
```bash
git add <dashboards> <alerts>
git commit -m "feat(observability): Claude account usage windows + per-kind gate dashboards/alerts"
```

---

## Phase D: tatara-helmfile - deploy (separate worktree off `origin/main`)

**Worktree:** `.worktrees/usage-gate-deploy` off `origin/main`, branch `feat/usage-window-gate-deploy`.

### Task D1: CRD values + OTLP wiring + dual pin

**Files:**
- Modify: per-project `values/project-*/common.yaml` (agent/tokenBudget block): set `mode: claudeSubscription`, `spawnCeilingByKind` (rendered as a templated ConfigMap, NOT a values list, per rule 6), `pollIntervalSeconds: 180`, `monitorOverage: false`.
- Modify: operator release values - `USAGE_AUTH_MODE` (from Task 0 spike), `USAGE_USER_AGENT`, secret access for the OAuth secret, `OTEL_EXPORTER_OTLP_ENDPOINT` for wrapper pods.
- Modify: operator + tatara-project chart version pins AND `image.tag` (dual pin, per memory `tatara-helmfile-dual-chart-pin`).

- [ ] **Step 1:** Set the tokenBudget block with the default ladder (brainstorm 40, selfImprove 55, healthCheck 60, triageIssue 70, review 75, issueLifecycle 80, implement 85, incident 98). `refine` is deliberately omitted: it is a scan-pipeline barrier (a held refine never reaches terminal, wedging every scan behind it), so it always admits and is never gated. Start with `enabled: true` on ONE project first (staged re-enable, spec section Rollout / review gap G3).
- [ ] **Step 2:** Bump operator + tatara-project chart versions to the merged-main SHA and set `image.tag` to the same, once Phase A is merged and CI has published both charts + image (memory `operator-ci-partial-chart-publish`).
- [ ] **Step 3:** `helmfile diff` the branch; review the CRD + ConfigMap + env changes.
- [ ] **Step 4: Commit + open MR.** Deploy applies via the in-cluster ARC runner on merge. Do NOT merge until Task 0 spike passed and Phase A/B are on their repos' `main`.
```bash
git add values/
git commit -m "feat(deploy): enable claudeSubscription usage gate (staged, one project)"
```

---

## Self-Review

**Spec coverage:**
- Goal 1 (standalone continuous windows): Tasks A3 (client) + A4 (poller) + A9 (wiring). Covered.
- Goal 2 (dynamic/rolling windows via reset): `Window.Reset` parsed (A3), gauges (A7), `active()` expiry in the gate (A1). Covered.
- Goal 3 (proactive per-kind ladder + headroom): A1 (KindBlocked) + A5 (CRD map) + A6 (gate). Covered.
- Goal 4 (customWindow unchanged): A1/A6 leave customWindow paths intact; A10 full suite. Covered.
- Goal 5 (fail safe): A4 stale degradation + A6 nil-safe store + Phase B/C OTel 429 backstop. Covered.
- Non-goal monthly read-only: `Overage` carried, never gates (A2/A3/A7 metrics only). Covered.
- Account-level ownership fix: A2 store + A8 retire per-project. Covered.

**Placeholder scan:** No "TBD"/"handle errors"/"similar to". Each code step carries real code. Task 0 is a spike (explicitly non-code) with concrete commands.

**Type consistency:** `Snapshot`/`Window`/`Overage` (A2) consumed unchanged in A3/A4/A9. `budget.KindBlocked` signature `(Config, Subscription, string, time.Time) bool` used identically in A1 test and A6 wiring. `SpawnCeilingByKind` is `map[string]int32` on the CRD (A5), converted to `map[string]int` for `budget.Config` (A1/A5) - conversion is explicit in A5 Step 3. `DispatcherReconciler.Usage *accountusage.Store` (A6) set in A9. Consistent.

## Execution Handoff

Two execution options:

1. **Subagent-Driven (recommended)** - fresh subagent per task (Sonnet impl per rule 7), two-stage review between tasks, Opus merge.
2. **Inline Execution** - execute tasks in this session with checkpoints.

Given the independent phases (operator core is a serial chain A1->A10; wrapper B, observability C are independent; helmfile D depends on A/B merged), the operator chain suits subagent-driven-development, and B/C can run as parallel subagents. Task 0 (spike) is user-run before any deploy.
