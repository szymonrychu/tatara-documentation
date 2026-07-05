# SCM Projects Wrapper Tool Flow-Through Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Implementation subagents run sonnet; the merge subagent runs opus. Develop in a worktree off `main`; build/deploy from `main` only. Steps use checkbox (- [ ]) syntax.

**Goal**

Make the three new SCM-projects MCP tools (`propose_issue`, `review_verdict`, `pr_outcome`) reach agents running inside the tatara-claude-code-wrapper, and prove they do. The wrapper does NOT enumerate MCP tools: `RegisterTataraMCP` (`internal/bootstrap/mcp_register.go:8`) runs `tatara mcp-config <workspace>`, which writes a `.mcp.json` entry `{"command":"tatara","args":["mcp"]}`. The `tatara mcp` subcommand serves every tool returned by `OperatorTools()`. So the new tools flow in automatically the moment the baked `tatara` binary is rebuilt from the SCM-projects cli release. The wrapper's only real work is (T1) re-pin the baked tatara-cli version, (T2) add an integration test that runs the rebuilt `tatara mcp` binary and asserts `tools/list` advertises the three new names so the flow-through can never silently regress, and (T3) bump the image + chart appVersion with MEMORY/ROADMAP notes.

**Architecture**

```
wrapper boot (internal/bootstrap)
  RegisterTataraMCP(workspace, run)        -- UNCHANGED: run("tatara","mcp-config",workspace)
        |
        v
  tatara mcp-config writes /workspace/.mcp.json:
        {"mcpServers":{"tatara":{"command":"tatara","args":["mcp"]}}}
        |
        v
  claude launches `tatara mcp` (stdio MCP server)
        |
        v
  tatara mcp serves OperatorTools()  -- 12 tools after SCM-projects cli release
        (9 existing + propose_issue + review_verdict + pr_outcome)
```

The wrapper baked the `tatara` binary at `TATARA_CLI_VERSION=0.4.0` (MEMORY 2026-06-07). The SCM-projects work adds 3 operator tools to the cli and ships them in cli `0.5.0`. This plan re-pins the wrapper to `0.5.0` (Makefile + Dockerfile) and locks the flow-through with a binary-level integration test.

**Tech Stack**

Go 1.25 (`go.mod` `go 1.25.0`), stdlib `log/slog` (JSON), `testify/require`, `os/exec` for the binary integration test, `encoding/json` for MCP JSON-RPC framing. Docker buildx (multi-stage; `COPY --from=tatara-cli`). Helm chart `charts/tatara-claude-code-wrapper`. No new Go module dependencies: tatara-cli is a baked binary, NOT a go.mod require.

---

## File Structure

| File | Create/Modify | Responsibility |
| --- | --- | --- |
| `internal/bootstrap/mcp_register.go` | Modify (comment only) | Document that tool flow-through is binary-driven, not enumerated; no logic change. |
| `internal/bootstrap/mcp_flowthrough_test.go` | Create | Integration test: run the baked `tatara mcp` binary, drive an MCP `initialize` + `tools/list` exchange over stdio, assert the response advertises `propose_issue`, `review_verdict`, `pr_outcome`. Skips cleanly when the binary is absent (local dev) but runs in CI where the image stage provides it. |
| `Dockerfile` | Modify (`ARG TATARA_CLI_VERSION=latest` -> `0.5.0`) | Pin the baked tatara-cli image tag to the SCM-projects cli release. |
| `Makefile` | Modify (`TATARA_CLI_VERSION ?= latest` -> `0.5.0`) | Default the build-arg to the pinned cli release so `make` and CI bake the same version. |
| `charts/tatara-claude-code-wrapper/Chart.yaml` | Modify (`version` + `appVersion`) | Chart `0.1.7 -> 0.1.8`, appVersion `0.1.6 -> 0.1.7` for the re-pinned image. |
| `MEMORY.md` | Modify (append) | Dated entry: tools flow through via `tatara mcp`, not enumeration; cli re-pinned to 0.5.0. |
| `ROADMAP.md` | Modify (mark done / re-scope) | Note SCM-projects tool flow-through shipped. |

---

### Task 1: Re-pin the baked tatara-cli to the SCM-projects release (0.5.0)

The SCM-projects sequencing (`docs/superpowers/specs/2026-06-09-scm-projects-pr-reactions-design.md` step 2-3) ships the 3 new operator tools in tatara-cli; current baked version is `0.4.0` (MEMORY 2026-06-07), the SCM-projects release is `0.5.0`. tatara-cli is a baked binary (`Dockerfile` stage 2 `FROM harbor.../tatara-cli:${TATARA_CLI_VERSION}`), NOT a go.mod require, so this is a Dockerfile + Makefile pin only -- no `go.mod` change.

**Files:**
- Modify: `Dockerfile` (line `ARG TATARA_CLI_VERSION=latest`)
- Modify: `Makefile` (line `TATARA_CLI_VERSION ?= latest`)

- [ ] **Step 1: Pin the Dockerfile ARG.** Edit `Dockerfile`. Change the build arg default from `latest` to the pinned release so even an un-overridden `docker build` bakes the correct cli:

  ```dockerfile
  ARG TATARA_CLI_VERSION=0.5.0
  ```

  (Replace the existing `ARG TATARA_CLI_VERSION=latest` near the top of the file. The stage-2 `FROM harbor.szymonrichert.pl/containers/tatara-cli:${TATARA_CLI_VERSION} AS tatara-cli` already consumes it; do not touch the FROM line.)

- [ ] **Step 2: Pin the Makefile default.** Edit `Makefile`. Change:

  ```makefile
  TATARA_CLI_VERSION ?= 0.5.0
  ```

  (Replace `TATARA_CLI_VERSION ?= latest`. The `docker buildx build ... --build-arg TATARA_CLI_VERSION=$(TATARA_CLI_VERSION)` line already passes it through; do not touch that line. `?=` keeps it overridable for a manual override build.)

- [ ] **Step 3: Verify the pin is consistent.** Run:

  ```
  grep -n "TATARA_CLI_VERSION" Dockerfile Makefile
  ```

  Expected: both files show `0.5.0` (Dockerfile `ARG TATARA_CLI_VERSION=0.5.0`, Makefile `TATARA_CLI_VERSION ?= 0.5.0`). No `latest` remaining for this arg.

- [ ] **Step 4: Commit the pin.**

  ```
  git add Dockerfile Makefile && git commit -m "chore(image): pin baked tatara-cli to 0.5.0 (SCM-projects tools)"
  ```

---

### Task 2: Lock the tool flow-through with a binary integration test

`RegisterTataraMCP` does not enumerate tools, so a unit test against it (`mcp_register_test.go`) cannot prove the new tools reach agents. The only thing that proves flow-through is the baked `tatara mcp` binary advertising the names. Add an integration test that locates `tatara` on `PATH` (or `/usr/local/bin/tatara`, where the Dockerfile copies it), runs `tatara mcp`, performs the MCP stdio handshake (`initialize` then `tools/list`), and asserts the three names appear. The test skips when the binary is absent so local `go test` stays green; CI builds the image stage that provides the binary and runs it there.

**Files:**
- Create: `internal/bootstrap/mcp_flowthrough_test.go`
- Modify: `internal/bootstrap/mcp_register.go` (doc comment only, Step 5)
- Test path: `internal/bootstrap/mcp_flowthrough_test.go`

- [ ] **Step 1: Write the failing flow-through test.** Create `internal/bootstrap/mcp_flowthrough_test.go` with the full integration test. It locates the binary, spawns `tatara mcp`, sends a newline-delimited JSON-RPC `initialize` then `tools/list`, reads the responses, and asserts the three tool names are advertised:

  ```go
  package bootstrap_test

  import (
  	"bufio"
  	"context"
  	"encoding/json"
  	"io"
  	"os/exec"
  	"path/filepath"
  	"testing"
  	"time"

  	"github.com/stretchr/testify/require"
  )

  // tataraBinary returns the path to the baked tatara CLI, preferring the
  // Dockerfile install location, falling back to PATH. Empty string => not found.
  func tataraBinary() string {
  	const baked = "/usr/local/bin/tatara"
  	if _, err := exec.LookPath(baked); err == nil {
  		return baked
  	}
  	if p, err := exec.LookPath("tatara"); err == nil {
  		return p
  	}
  	if abs, err := filepath.Abs(baked); err == nil {
  		if _, err := exec.LookPath(abs); err == nil {
  			return abs
  		}
  	}
  	return ""
  }

  type rpcReq struct {
  	JSONRPC string `json:"jsonrpc"`
  	ID      int    `json:"id"`
  	Method  string `json:"method"`
  	Params  any    `json:"params,omitempty"`
  }

  type toolsListResult struct {
  	Result struct {
  		Tools []struct {
  			Name string `json:"name"`
  		} `json:"tools"`
  	} `json:"result"`
  }

  // TestTataraMCP_AdvertisesScmProjectTools proves the baked tatara CLI's `mcp`
  // server advertises the SCM-projects tools that the wrapper relies on flowing
  // through automatically (RegisterTataraMCP runs `tatara mcp-config`, which wires
  // `tatara mcp`; the wrapper never enumerates tools, so this is the only guard
  // against a silent regression when the baked cli version is bumped).
  func TestTataraMCP_AdvertisesScmProjectTools(t *testing.T) {
  	bin := tataraBinary()
  	if bin == "" {
  		t.Skip("tatara binary not found; runs in the image stage / CI where it is baked")
  	}

  	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
  	defer cancel()

  	cmd := exec.CommandContext(ctx, bin, "mcp")
  	stdin, err := cmd.StdinPipe()
  	require.NoError(t, err)
  	stdout, err := cmd.StdoutPipe()
  	require.NoError(t, err)
  	require.NoError(t, cmd.Start())
  	defer func() {
  		_ = stdin.Close()
  		_ = cmd.Wait()
  	}()

  	send := func(r rpcReq) {
  		b, err := json.Marshal(r)
  		require.NoError(t, err)
  		_, err = stdin.Write(append(b, '\n'))
  		require.NoError(t, err)
  	}

  	send(rpcReq{JSONRPC: "2.0", ID: 1, Method: "initialize", Params: map[string]any{
  		"protocolVersion": "2024-11-05",
  		"capabilities":    map[string]any{},
  		"clientInfo":      map[string]any{"name": "wrapper-flowthrough-test", "version": "1"},
  	}})
  	send(rpcReq{JSONRPC: "2.0", ID: 2, Method: "tools/list"})

  	names := collectToolNames(t, stdout)
  	for _, want := range []string{"propose_issue", "review_verdict", "pr_outcome"} {
  		require.Containsf(t, names, want, "tatara mcp must advertise %q; got %v", want, names)
  	}
  }

  // collectToolNames reads newline-delimited JSON-RPC responses until it sees the
  // tools/list result (the one carrying a non-empty tools array), returning the
  // advertised tool names.
  func collectToolNames(t *testing.T, r io.Reader) []string {
  	t.Helper()
  	sc := bufio.NewScanner(r)
  	sc.Buffer(make([]byte, 0, 64*1024), 1<<20)
  	for sc.Scan() {
  		line := sc.Bytes()
  		if len(line) == 0 {
  			continue
  		}
  		var res toolsListResult
  		if err := json.Unmarshal(line, &res); err != nil {
  			continue
  		}
  		if len(res.Result.Tools) == 0 {
  			continue
  		}
  		names := make([]string, 0, len(res.Result.Tools))
  		for _, tl := range res.Result.Tools {
  			names = append(names, tl.Name)
  		}
  		return names
  	}
  	require.NoError(t, sc.Err())
  	t.Fatal("tatara mcp produced no tools/list result")
  	return nil
  }
  ```

- [ ] **Step 2: Run the test, expect SKIP locally / FAIL semantics in CI.** Run:

  ```
  go test ./internal/bootstrap/ -run TestTataraMCP_AdvertisesScmProjectTools -v
  ```

  Expected locally (no baked binary on a dev box): `--- SKIP: TestTataraMCP_AdvertisesScmProjectTools (tatara binary not found ...)`. This confirms the test compiles, the skip guard fires, and the suite stays green. In the image stage (where `/usr/local/bin/tatara` is the `0.4.0` binary, pre-rebuild) the same test would FAIL with `tatara mcp must advertise "propose_issue"; got [...9 names...]` -- that is the failing-state proof the test is meaningful. After Task 1 re-pins to `0.5.0` and the image is rebuilt, it PASSES.

- [ ] **Step 3: Verify against a locally-built SCM-projects cli (optional but recommended before merge).** If a `0.5.0` (or HEAD-of-SCM-projects) `tatara` binary is available, put it on `PATH` and re-run to see a real PASS, not just a skip:

  ```
  go build -o /tmp/ttbin/tatara ../tatara-cli/cmd/tatara && PATH="/tmp/ttbin:$PATH" go test ./internal/bootstrap/ -run TestTataraMCP_AdvertisesScmProjectTools -v
  ```

  Expected: `--- PASS: TestTataraMCP_AdvertisesScmProjectTools`. If it FAILs with the 9-tool list, the cli release has not landed the 3 tools yet -- block the wrapper merge until cli `0.5.0` is published (sequencing: operator -> cli -> wrapper).

- [ ] **Step 4: Run the full bootstrap suite to confirm no regression.** Run:

  ```
  go test ./internal/bootstrap/ -v
  ```

  Expected: all existing tests PASS (`TestRegisterTataraMCP_RunsMcpConfig`, `TestRegisterTataraMCP_PropagatesError`, bootstrap/enforce/namespace/claudejson tests) and the new test SKIPs (or PASSes with a baked binary). No FAILs.

- [ ] **Step 5: Document the flow-through in `mcp_register.go` (comment only).** Edit `internal/bootstrap/mcp_register.go`. Replace the existing doc comment so the next reader knows tools are NOT enumerated here and where the guard lives. Change:

  ```go
  // RegisterTataraMCP merges the tatara MCP server into the workspace .mcp.json
  // via the tatara CLI's own mcp-config command.
  func RegisterTataraMCP(workspace string, run CmdRunner) error {
  	return run("tatara", "mcp-config", workspace)
  }
  ```

  to:

  ```go
  // RegisterTataraMCP wires the tatara MCP server into the workspace .mcp.json
  // via the tatara CLI's own mcp-config command. This writes a single entry
  // {"command":"tatara","args":["mcp"]}; the set of MCP tools agents see is
  // whatever `tatara mcp` serves (OperatorTools()), NOT enumerated here. New
  // operator tools therefore flow through automatically once the baked tatara
  // binary is rebuilt; mcp_flowthrough_test.go guards that against regression.
  func RegisterTataraMCP(workspace string, run CmdRunner) error {
  	return run("tatara", "mcp-config", workspace)
  }
  ```

- [ ] **Step 6: gofmt + vet + lint clean.** Run:

  ```
  gofmt -l internal/bootstrap/ && go vet ./internal/bootstrap/ && golangci-lint run ./internal/bootstrap/...
  ```

  Expected: `gofmt -l` prints nothing (no unformatted files), `go vet` and `golangci-lint` exit 0 with no findings.

- [ ] **Step 7: Commit the flow-through guard.**

  ```
  git add internal/bootstrap/mcp_flowthrough_test.go internal/bootstrap/mcp_register.go && git commit -m "test(bootstrap): assert tatara mcp advertises propose_issue/review_verdict/pr_outcome"
  ```

---

### Task 3: Bump chart appVersion + chart version; MEMORY/ROADMAP notes

The re-pinned baked cli is a new image, so the chart `appVersion` (image tag) and `version` (chart revision) both move. Record the decision in MEMORY (so the next agent knows tool flow-through is binary-driven and the cli is pinned at 0.5.0) and update ROADMAP.

**Files:**
- Modify: `charts/tatara-claude-code-wrapper/Chart.yaml` (`version: 0.1.7 -> 0.1.8`, `appVersion: "0.1.6" -> "0.1.7"`)
- Modify: `MEMORY.md` (append dated entry)
- Modify: `ROADMAP.md` (note shipped)

- [ ] **Step 1: Bump the chart.** Edit `charts/tatara-claude-code-wrapper/Chart.yaml`. Change:

  ```yaml
  version: 0.1.7
  appVersion: "0.1.6"
  ```

  to:

  ```yaml
  version: 0.1.8
  appVersion: "0.1.7"
  ```

- [ ] **Step 2: helm lint clean.** Run:

  ```
  helm lint charts/tatara-claude-code-wrapper
  ```

  Expected: `1 chart(s) linted, 0 chart(s) failed`. No errors about version/appVersion format.

- [ ] **Step 3: Append the MEMORY entry.** Edit `MEMORY.md`. Add under the dated-decisions section:

  ```
  - 2026-06-09 - **SCM-projects MCP tools flow through, not enumerated.** RegisterTataraMCP only runs `tatara mcp-config`, which wires `{command:tatara,args:[mcp]}`; agents see whatever `tatara mcp` serves (OperatorTools()). propose_issue/review_verdict/pr_outcome arrived for free once the baked cli was re-pinned 0.4.0 -> 0.5.0 (Dockerfile ARG + Makefile default). mcp_flowthrough_test.go runs the binary's MCP tools/list and asserts the 3 names so a future cli pin can't silently drop them. Chart 0.1.8 / appVersion 0.1.7.
  ```

- [ ] **Step 4: Update ROADMAP.** Edit `ROADMAP.md`. Remove (or strike) any pending "SCM-projects tools reach agents" item if present; otherwise add a shipped note at the top of the relevant section:

  ```
  - SHIPPED 2026-06-09 - SCM-projects MCP tools (propose_issue/review_verdict/pr_outcome) reach agents via the re-pinned baked tatara-cli 0.5.0; flow-through asserted by mcp_flowthrough_test.go.
  ```

- [ ] **Step 5: Commit the bump + notes.**

  ```
  git add charts/tatara-claude-code-wrapper/Chart.yaml MEMORY.md ROADMAP.md && git commit -m "chore(chart): bump 0.1.8/appVersion 0.1.7 for re-pinned cli 0.5.0; MEMORY/ROADMAP"
  ```

---

## Verification before completion

Before claiming done, run and confirm (per superpowers:verification-before-completion):

1. `go test ./internal/bootstrap/ -v` -- all PASS, new test SKIPs locally (or PASSes with a baked binary on PATH).
2. The real flow-through proof (the load-bearing check): build the SCM-projects cli binary and run the wrapper test against it --
   ```
   go build -o /tmp/ttbin/tatara ../tatara-cli/cmd/tatara && PATH="/tmp/ttbin:$PATH" go test ./internal/bootstrap/ -run TestTataraMCP_AdvertisesScmProjectTools -v
   ```
   Expected: `--- PASS`, advertised names include `propose_issue`, `review_verdict`, `pr_outcome`. If only 9 tools show, the cli release has not landed -- do NOT merge the wrapper; the operator -> cli -> wrapper sequence is not satisfied.
3. `grep -n TATARA_CLI_VERSION Dockerfile Makefile` -- both `0.5.0`, no `latest`.
4. `gofmt -l internal/bootstrap/` empty; `go vet ./...` and `golangci-lint run ./...` clean.
5. `helm lint charts/tatara-claude-code-wrapper` -- 0 failed.
6. `pre-commit run --all-files` -- all hooks pass.

Then request code review (superpowers:requesting-code-review), apply critical/high fixes, merge the worktree back to `main`, clean up the worktree, and build/push the image from `main` with `make build TATARA_CLI_VERSION=0.5.0` (the pin is the default, so a bare `make build` also bakes 0.5.0). Build only after cli `0.5.0` is published to Harbor, since stage 2 `COPY --from=tatara-cli` pulls it.
