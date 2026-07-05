# Agent-native tatara tools + memory robustness - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the spawned agent use tatara's own MCP tools natively (headless auth + MCP registration + operator URL), and harden the per-project memory against a postgres blip.

**Architecture:** tatara-cli gains a headless OIDC client-credentials fallback (env-driven) so `tatara raw`/`tatara mcp` work with no `tatara login`. The operator injects `TATARA_OPERATOR_URL` and clears the stale `MemoryNotReady` condition. The wrapper runs `tatara mcp-config /workspace` at bootstrap to register the server. tatara-memory waits for postgres at startup instead of crashlooping.

**Tech Stack:** Go (all repos); controller-runtime + envtest (operator); testify.

**Spec:** `docs/superpowers/specs/2026-06-09-agent-native-tatara-tools-design.md`

**Branch flow (every repo):** worktree off `main` -> TDD -> lint/gofmt/test green -> merge to `main`. The parent owns image builds + deploy.

**Shared env contract (operator emits, CLI reads):** `OIDC_ISSUER`, `CLI_OIDC_CLIENT_ID`, `CLI_OIDC_CLIENT_SECRET` (auth); `TATARA_MEMORY_URL` (memory REST), `TATARA_OPERATOR_URL` (operator REST).

---

## tatara-cli (A1: headless auth)

**Repo:** `/Users/szymonri/Documents/tatara/tatara-cli`

### Task C1: client-credentials token source

**Files:**
- Create: `internal/auth/clientcreds.go`
- Test: `internal/auth/clientcreds_test.go`
- Read first: `internal/auth/token.go` (how the stored token is loaded + the `Token`/error shape).

- [ ] **Step 1: Write the failing test** (clientcreds_test.go) - a fake OIDC server with a discovery doc + token endpoint; assert a client_credentials grant returns the token and parses expiry:

```go
func TestClientCredentialsToken(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/.well-known/openid-configuration", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintf(w, `{"token_endpoint":"http://%s/token"}`, r.Host)
	})
	mux.HandleFunc("/token", func(w http.ResponseWriter, r *http.Request) {
		require.NoError(t, r.ParseForm())
		require.Equal(t, "client_credentials", r.Form.Get("grant_type"))
		require.Equal(t, "cid", r.Form.Get("client_id"))
		require.Equal(t, "secret", r.Form.Get("client_secret"))
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"access_token":"tok-123","expires_in":300,"token_type":"Bearer"}`)
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()

	tok, exp, err := auth.ClientCredentialsToken(context.Background(), srv.URL, "cid", "secret")
	require.NoError(t, err)
	require.Equal(t, "tok-123", tok)
	require.WithinDuration(t, time.Now().Add(300*time.Second), exp, 5*time.Second)
}
```

- [ ] **Step 2: Run, verify fail.** `go test ./internal/auth/ -run TestClientCredentialsToken`. FAIL (undefined).

- [ ] **Step 3: Implement** (clientcreds.go):

```go
package auth

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// ClientCredentialsToken performs an OIDC client_credentials grant against the
// issuer's discovered token endpoint and returns the access token + expiry.
func ClientCredentialsToken(ctx context.Context, issuer, clientID, clientSecret string) (string, time.Time, error) {
	disco := strings.TrimRight(issuer, "/") + "/.well-known/openid-configuration"
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, disco, nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", time.Time{}, fmt.Errorf("oidc discovery: %w", err)
	}
	defer resp.Body.Close()
	var meta struct {
		TokenEndpoint string `json:"token_endpoint"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&meta); err != nil || meta.TokenEndpoint == "" {
		return "", time.Time{}, fmt.Errorf("oidc discovery: no token_endpoint")
	}
	form := url.Values{"grant_type": {"client_credentials"}, "client_id": {clientID}, "client_secret": {clientSecret}}
	treq, _ := http.NewRequestWithContext(ctx, http.MethodPost, meta.TokenEndpoint, strings.NewReader(form.Encode()))
	treq.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	tresp, err := http.DefaultClient.Do(treq)
	if err != nil {
		return "", time.Time{}, fmt.Errorf("token request: %w", err)
	}
	defer tresp.Body.Close()
	if tresp.StatusCode != http.StatusOK {
		return "", time.Time{}, fmt.Errorf("token request: status %d", tresp.StatusCode)
	}
	var tr struct {
		AccessToken string `json:"access_token"`
		ExpiresIn   int    `json:"expires_in"`
	}
	if err := json.NewDecoder(tresp.Body).Decode(&tr); err != nil {
		return "", time.Time{}, fmt.Errorf("token decode: %w", err)
	}
	return tr.AccessToken, time.Now().Add(time.Duration(tr.ExpiresIn) * time.Second), nil
}
```

- [ ] **Step 4: Run, verify pass.** `go test ./internal/auth/ -run TestClientCredentialsToken`. PASS.

- [ ] **Step 5: Commit** `git add internal/auth/clientcreds.go internal/auth/clientcreds_test.go && git commit -m "feat(auth): OIDC client_credentials token source"`

### Task C2: wire the headless fallback into token acquisition

**Files:**
- Modify: `internal/auth/token.go` (the function that returns the token for HTTP calls)
- Test: `internal/auth/clientcreds_test.go`

- [ ] **Step 1: Read** `internal/auth/token.go` to find the exported "load token" function used by `client` (e.g. `Load()`/`Token()`), and how `ErrNoToken` is returned.

- [ ] **Step 2: Write the failing test** - with no stored token but env set, the loader returns the client-creds token; a cached token is reused until near expiry:

```go
func TestTokenFallsBackToClientCreds(t *testing.T) {
	// fake issuer as in C1
	srv := newFakeIssuer(t) // helper returning a server that issues "tok-123"
	defer srv.Close()
	t.Setenv("XDG_CONFIG_HOME", t.TempDir()) // no stored token
	t.Setenv("OIDC_ISSUER", srv.URL)
	t.Setenv("CLI_OIDC_CLIENT_ID", "cid")
	t.Setenv("CLI_OIDC_CLIENT_SECRET", "secret")
	auth.ResetTokenCache() // test helper to clear the package cache
	tok, err := auth.AccessToken(context.Background())
	require.NoError(t, err)
	require.Equal(t, "tok-123", tok)
}

func TestTokenNoEnvStillErrNoToken(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
	t.Setenv("OIDC_ISSUER", "")
	auth.ResetTokenCache()
	_, err := auth.AccessToken(context.Background())
	require.ErrorIs(t, err, auth.ErrNoToken)
}
```
(Refactor the fake issuer from C1 into a `newFakeIssuer` helper.)

- [ ] **Step 3: Run, verify fail.**

- [ ] **Step 4: Implement** - add an `AccessToken(ctx)` (or fold into the existing accessor) that: returns the stored token if present; else if `OIDC_ISSUER`+`CLI_OIDC_CLIENT_ID`+`CLI_OIDC_CLIENT_SECRET` are set, returns a package-cached client-creds token (mutex-guarded; re-mint when `time.Now()` is within 30s of expiry); else `ErrNoToken`. Add `ResetTokenCache()` for tests. Update the HTTP client (`internal/client`) to call `AccessToken` where it currently loads the stored token.

```go
var (
	ccMu    sync.Mutex
	ccTok   string
	ccExp   time.Time
)

func AccessToken(ctx context.Context) (string, error) {
	if tok, err := loadStoredToken(); err == nil && tok != "" {
		return tok, nil
	}
	issuer, id, secret := os.Getenv("OIDC_ISSUER"), os.Getenv("CLI_OIDC_CLIENT_ID"), os.Getenv("CLI_OIDC_CLIENT_SECRET")
	if issuer == "" || id == "" || secret == "" {
		return "", ErrNoToken
	}
	ccMu.Lock()
	defer ccMu.Unlock()
	if ccTok != "" && time.Now().Before(ccExp.Add(-30*time.Second)) {
		return ccTok, nil
	}
	tok, exp, err := ClientCredentialsToken(ctx, issuer, id, secret)
	if err != nil {
		return "", err
	}
	ccTok, ccExp = tok, exp
	return tok, nil
}
func ResetTokenCache() { ccMu.Lock(); ccTok, ccExp = "", time.Time{}; ccMu.Unlock() }
```
(Adapt `loadStoredToken` to the real stored-token accessor in token.go.)

- [ ] **Step 5: Run, verify pass + full module.** `go test ./...`. PASS.

- [ ] **Step 6: Commit** `git commit -am "feat(auth): headless client-credentials fallback for raw + mcp"`

### Task C3: ship tatara-cli

- [ ] `gofmt`/`golangci-lint`/`go test ./...` green; requesting-code-review; bump no chart (CLI has none); merge to main. (Image rebuild owned by parent: the wrapper image bakes tatara-cli at `TATARA_CLI_VERSION`.)

---

## tatara-operator (A2 + B2)

**Repo:** `/Users/szymonri/Documents/tatara/tatara-operator`
**Envtest:** `export KUBEBUILDER_ASSETS="/Users/szymonri/Library/Application Support/io.kubebuilder.envtest/k8s/1.36.0-darwin-arm64"`

### Task O1: TATARA_OPERATOR_URL on the agent pod

**Files:**
- Modify: `internal/agent/pod.go` (PodConfig + env), `internal/config/config.go` (`OPERATOR_URL`), `internal/controller/*wire*.go` (populate PodConfig)
- Test: `internal/agent/pod_test.go`

- [ ] **Step 1: Failing test** - add to the env-check map in `TestBuildPod_PlainEnv` (and set `cfg.OperatorURL` in `sampleInputs`/`testCfg`):

```go
"TATARA_OPERATOR_URL": "http://tatara-operator.tatara.svc:8080",
```

- [ ] **Step 2: Run, verify fail.** `KUBEBUILDER_ASSETS=... go test ./internal/agent/ -run TestBuildPod_PlainEnv`. FAIL (env missing).

- [ ] **Step 3: Implement** - add `OperatorURL string` to `agent.PodConfig`; in `BuildPod` env add `{Name: "TATARA_OPERATOR_URL", Value: cfg.OperatorURL}`. In `internal/config/config.go` add `OperatorURL string` from `os.Getenv("OPERATOR_URL")` (default `http://tatara-operator.tatara.svc:8080`); thread it into the `PodConfig` built in the manager wiring. Update `testCfg()`/`sampleInputs()` to set OperatorURL.

- [ ] **Step 4: Run, verify pass.** Update any other PodConfig construction sites.

- [ ] **Step 5: Commit** `git commit -am "feat(agent): inject TATARA_OPERATOR_URL into the agent pod"`

### Task O2: clear stale MemoryNotReady when memory is Ready

**Files:**
- Modify: `internal/controller/repository_controller.go` (where `MemoryNotReady` is set)
- Test: `internal/controller/repository_controller_test.go` (envtest)

- [ ] **Step 1: Read** the reconcile path that sets `MemoryNotReady` (search `MemoryNotReady`). It is set when `project.Status.Memory == nil || phase != "Ready"`.

- [ ] **Step 2: Failing test** - a Repository whose Project has `memory.phase=Ready` ends reconcile with `MemoryNotReady` condition `False`:

```go
func TestRepository_ClearsMemoryNotReadyWhenReady(t *testing.T) {
	// seed Project with Status.Memory.Phase = "Ready" + a Repository;
	// reconcile; assert findCond(repo.Status.Conditions,"MemoryNotReady").Status == False
}
```
(Model on the existing repository_controller envtest setup.)

- [ ] **Step 3: Run, verify fail.**

- [ ] **Step 4: Implement** - in the reconcile branch where memory IS Ready (before/at ingest), set the condition False:

```go
meta.SetStatusCondition(&repo.Status.Conditions, metav1.Condition{
	Type: "MemoryNotReady", Status: metav1.ConditionFalse,
	Reason: "MemoryReady", Message: "project memory stack is Ready",
	ObservedGeneration: repo.Generation,
})
```
and persist via the existing `r.Status().Update`.

- [ ] **Step 5: Run, verify pass + full suite.** `KUBEBUILDER_ASSETS=... go test ./...`.

- [ ] **Step 6: Commit** `git commit -am "fix(repository): clear MemoryNotReady once project memory is Ready"`

### Task O3: ship operator

- [ ] Bump chart `0.2.10` -> `0.2.11`; MEMORY entry; full suite + lint green; requesting-code-review; merge to main.

---

## tatara-claude-code-wrapper (A3)

**Repo:** `/Users/szymonri/Documents/tatara/tatara-claude-code-wrapper`

### Task W1: run `tatara mcp-config` after bootstrap

**Files:**
- Modify: `cmd/wrapper/app.go` (after `bootstrap.Render`, register MCP), `internal/bootstrap/bootstrap.go` (optional `RegisterTataraMCP` helper)
- Test: `internal/bootstrap/enforce_test.go` or a new `mcp_register_test.go`

- [ ] **Step 1: Failing test** - a `RegisterTataraMCP(workspace, run)` helper invokes `tatara mcp-config <workspace>` via an injected runner, only when enabled:

```go
func TestRegisterTataraMCP_RunsMcpConfig(t *testing.T) {
	var calls [][]string
	run := func(name string, args ...string) error { calls = append(calls, append([]string{name}, args...)); return nil }
	require.NoError(t, bootstrap.RegisterTataraMCP("/workspace", run))
	require.Len(t, calls, 1)
	require.Equal(t, []string{"tatara", "mcp-config", "/workspace"}, calls[0])
}
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement** - add to bootstrap:

```go
// CmdRunner runs an external command; injected for testability.
type CmdRunner func(name string, args ...string) error

// RegisterTataraMCP merges the tatara MCP server into the workspace .mcp.json
// via the tatara CLI's own mcp-config command.
func RegisterTataraMCP(workspace string, run CmdRunner) error {
	return run("tatara", "mcp-config", workspace)
}
```
In `app.go`, after `bootstrap.Render(...)`, when `os.Getenv("TATARA_MEMORY_URL") != ""` and a `tatara` binary is on PATH (`exec.LookPath("tatara")` succeeds), call `bootstrap.RegisterTataraMCP(cfg.Workspace, execRunner)` where `execRunner` runs the command with the pod env and logs (non-fatal on error). Skip otherwise.

- [ ] **Step 4: Run, verify pass + full module.** `go test ./...`.

- [ ] **Step 5: Commit** `git commit -am "feat(bootstrap): register the tatara MCP server via mcp-config at boot"`

### Task W2: ship wrapper

- [ ] Bump chart appVersion `0.1.5` -> `0.1.6`, version `0.1.6` -> `0.1.7`; MEMORY entry; gofmt/lint/test green; merge to main.

---

## tatara-memory (B1)

**Repo:** `/Users/szymonri/Documents/tatara/tatara-memory`

### Task M1: wait for postgres at startup

**Files:**
- Modify: `cmd/tatara-memory/app.go` (the DB-open path in `newAppWithDeps`/`run`)
- Test: `cmd/tatara-memory/app_test.go`

- [ ] **Step 1: Read** `cmd/tatara-memory/app.go` `openDB` / where the pool is opened, and confirm whether a failed `Ping` currently aborts startup.

- [ ] **Step 2: Failing test** - a `waitForDB(ctx, ping, timeout, interval)` retries a ping that fails N times then succeeds, and returns nil; gives up after timeout:

```go
func TestWaitForDB_RetriesThenSucceeds(t *testing.T) {
	n := 0
	ping := func(context.Context) error { n++; if n < 3 { return errors.New("refused") }; return nil }
	require.NoError(t, waitForDB(context.Background(), ping, time.Second, 5*time.Millisecond))
	require.GreaterOrEqual(t, n, 3)
}
func TestWaitForDB_TimesOut(t *testing.T) {
	ping := func(context.Context) error { return errors.New("refused") }
	require.Error(t, waitForDB(context.Background(), ping, 20*time.Millisecond, 5*time.Millisecond))
}
```

- [ ] **Step 3: Run, verify fail.**

- [ ] **Step 4: Implement** `waitForDB`:

```go
func waitForDB(ctx context.Context, ping func(context.Context) error, timeout, interval time.Duration) error {
	deadline := time.Now().Add(timeout)
	for {
		if err := ping(ctx); err == nil {
			return nil
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("database not reachable within %s", timeout)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(interval):
		}
	}
}
```
Call it right after opening the pool in startup: `waitForDB(ctx, db.PingContext, 60*time.Second, 2*time.Second)` before migrate/serve, so a transient pg outage retries instead of exiting. Keep `pool.Resume` errors non-fatal (already WARN-logged).

- [ ] **Step 5: Run, verify pass + full module.** `go test ./...`.

- [ ] **Step 6: Commit** `git commit -am "fix(startup): wait for postgres with backoff instead of crashlooping"`

### Task M2: ship tatara-memory

- [ ] Bump chart `0.2.3` -> `0.2.4` appVersion/version; MEMORY entry; gofmt/lint/test green; merge to main.

---

## Integration (parent-owned)

- [ ] **Terraform verify (A1 dependency):** inspect `infra/terraform/keycloak/tatara_clients.tf` - confirm the `tatara-cli-oidc` confidential client has service accounts enabled + audience mappers for `tatara-memory` AND `tatara-operator`. If missing, add them (gated `terraform plan` -> apply).
- [ ] Build+push images: `tatara-cli:0.4.1` (or rebuild wrapper with it), `tatara-claude-code-wrapper:0.1.6` (`--build-arg TATARA_CLI_VERSION=<cli>`), `tatara-operator:0.2.11` + chart, `tatara-memory:0.2.4`.
- [ ] Bump per-Project `memoryImage` (0.2.4) + operator chart/image (0.2.11) in infra; patch Project `agent.image` -> wrapper 0.1.6; apply; infra MRs.
- [ ] **Validate:** re-run issue #10-style "check all MCP tools" - confirm the agent's report now shows the tatara MCP server REGISTERED and tools CALLABLE (not "shelled out"); confirm a multi-step issue produces real Subtask CRs; confirm `MemoryNotReady` is cleared on repos and `/queries` answers.

## Self-review notes

- Spec coverage: A1 (C1+C2), A2 (O1), A3 (W1), B1 (M1), B2 (O2), terraform verify (Integration), /queries recheck (Integration validate). All covered.
- Type consistency: `AccessToken(ctx)`/`ClientCredentialsToken`/`ResetTokenCache` (cli); `PodConfig.OperatorURL` + `TATARA_OPERATOR_URL` (operator); `RegisterTataraMCP`/`CmdRunner` (wrapper); `waitForDB` (memory) used consistently.
- Env names match the shared contract in both producers (operator) and consumer (cli): `OIDC_ISSUER`, `CLI_OIDC_CLIENT_ID/SECRET`, `TATARA_MEMORY_URL`, `TATARA_OPERATOR_URL`.
