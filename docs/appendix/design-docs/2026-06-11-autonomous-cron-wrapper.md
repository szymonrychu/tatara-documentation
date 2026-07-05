# Autonomous Cron - tatara-claude-code-wrapper Implementation Plan

**Date:** 2026-06-11
**Repo:** `tatara-claude-code-wrapper`
**Pairs with:** `2026-06-11-autonomous-cron-design.md`, `2026-06-11-autonomous-cron-contract-lock.md`
**Build order:** step 3 of 4 (operator -> cli -> **wrapper** -> infra). Depends on the cli having shipped `issue_outcome` in a new tagged image.

## Goal

Make the wrapper image bake a tatara-cli that advertises the new `issue_outcome`
MCP tool, and guard that contract at image-build time. The wrapper does **not**
enumerate MCP tools: `RegisterTataraMCP` (internal/bootstrap/mcp_register.go:12)
writes a single `{"command":"tatara","args":["mcp"]}` entry via
`tatara mcp-config`, and the agent sees whatever `tatara mcp` serves
(`OperatorTools()`). So `issue_outcome` flows through automatically the moment
the baked cli is bumped. This plan is therefore a **version-bump +
guard-extension** only: no Go production code changes, no registration code.

## Architecture

Three things change, nothing else:

1. **Pinned cli version** bumped from `0.5.0` to `0.6.0` in `Dockerfile`
   (`ARG TATARA_CLI_VERSION`) and `Makefile` (`TATARA_CLI_VERSION ?=`). `0.6.0`
   is the cli's next minor (a new MCP tool added on top of the `0.5.0`
   SCM-projects release).
2. **Build-stage guard** (`mcp_flowthrough_test.go`) extended to assert the
   baked `tatara mcp` server advertises `issue_outcome` alongside the existing
   `propose_issue` / `review_verdict` / `pr_outcome`. This is the only guard
   against a silent regression when the cli version is bumped; it runs in the
   `test-guard` Docker stage and fails the image build if the tool is missing.
3. **Chart + appVersion bump** (`charts/tatara-claude-code-wrapper/Chart.yaml`):
   `version` 0.1.8 -> 0.1.9, `appVersion` "0.1.7" -> "0.1.8".

## Tech Stack

- Go 1.25 (`go.mod`: `go 1.25.0`).
- Test framework: `testify/require`, table-driven `t.Run` subtests, stdlib
  `log/slog` JSON logging elsewhere in the repo.
- Docker buildx multi-stage build; the `test-guard` stage runs
  `go test ./internal/bootstrap -run TestTataraMCP_AdvertisesScmProjectTools`.
- Helm chart created via `helm create`, edited.

## REQUIRED SUB-SKILL

You MUST use `superpowers:test-driven-development` for the guard-test change
(Task 1 is Red-Green-Refactor). The version/chart bumps (Tasks 2-4) are
mechanical config edits with no test of their own; verify them via the commands
shown, not via new tests. Use `superpowers:verification-before-completion`
before claiming done. Use `superpowers:requesting-code-review` before the final
commit.

---

## Pre-flight: confirm the cli image exists

Before any code change, confirm the cli `0.6.0` image (with `issue_outcome`) is
published to the registry. The wrapper's `test-guard` stage pulls
`harbor.szymonrichert.pl/containers/tatara-cli:0.6.0`; if it does not exist the
image build fails at stage 2.

```
docker pull harbor.szymonrichert.pl/containers/tatara-cli:0.6.0
docker run --rm --entrypoint /usr/local/bin/tatara \
  harbor.szymonrichert.pl/containers/tatara-cli:0.6.0 mcp <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"preflight","version":"1"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
EOF
```

Expected: the `tools/list` result includes `"issue_outcome"`. If the image is
missing or the tool is absent, STOP - the cli plan (build-order step 2) has not
landed yet. Do not proceed.

If `0.6.0` is not the version the cli actually shipped, use the real cli tag
everywhere `0.6.0` appears below (Dockerfile ARG, Makefile var, commit messages).

---

## Task 1: Extend the build-stage guard to require `issue_outcome` (TDD)

**File (Test):** `internal/bootstrap/mcp_flowthrough_test.go`

### 1a. RED - add `issue_outcome` to the asserted tool set

The existing test `TestTataraMCP_AdvertisesScmProjectTools`
(mcp_flowthrough_test.go:54) already drives a `tatara mcp` subprocess and
collects tool names via `collectToolNames`. Only the assertion list changes.

Modify the loop at mcp_flowthrough_test.go:89-91. Current:

```go
	names := collectToolNames(t, stdout)
	for _, want := range []string{"propose_issue", "review_verdict", "pr_outcome"} {
		require.Containsf(t, names, want, "tatara mcp must advertise %q; got %v", want, names)
	}
```

Replace with:

```go
	names := collectToolNames(t, stdout)
	for _, want := range []string{"propose_issue", "review_verdict", "pr_outcome", "issue_outcome"} {
		require.Containsf(t, names, want, "tatara mcp must advertise %q; got %v", want, names)
	}
```

That is the only edit in this file. `issue_outcome` is the exact tool name from
the contract lock (section 8: `name: issue_outcome`).

### 1b. Run the test against the OLD baked cli - expect FAIL

If the locally-installed/baked `tatara` is still `0.5.0` (no `issue_outcome`),
the new assertion fails. Run:

```
cd /Users/szymonri/Documents/tatara/tatara-claude-code-wrapper
PATH="$(docker create harbor.szymonrichert.pl/containers/tatara-cli:0.5.0 >/dev/null 2>&1; echo)" \
  go test ./internal/bootstrap -run TestTataraMCP_AdvertisesScmProjectTools -count=1 -v
```

Simpler, deterministic RED: temporarily put the OLD cli on PATH and run the test.

```
TMPOLD=$(mktemp -d)
cid=$(docker create harbor.szymonrichert.pl/containers/tatara-cli:0.5.0)
docker cp "$cid":/usr/local/bin/tatara "$TMPOLD"/tatara
docker rm "$cid" >/dev/null
PATH="$TMPOLD:$PATH" go test ./internal/bootstrap -run TestTataraMCP_AdvertisesScmProjectTools -count=1 -v
```

Expected output (FAIL):

```
--- FAIL: TestTataraMCP_AdvertisesScmProjectTools
    mcp_flowthrough_test.go:90: tatara mcp must advertise "issue_outcome"; got [propose_issue review_verdict pr_outcome ...]
FAIL
```

(If no `tatara` binary is resolvable at all, the test `t.Skip`s -
mcp_flowthrough_test.go:57. That is not a RED; you need the old cli on PATH to
see the real failure. The authoritative RED-then-GREEN is the Docker build in
Task 1d.)

### 1c. GREEN - put the NEW baked cli on PATH, re-run - expect PASS

```
TMPNEW=$(mktemp -d)
cid=$(docker create harbor.szymonrichert.pl/containers/tatara-cli:0.6.0)
docker cp "$cid":/usr/local/bin/tatara "$TMPNEW"/tatara
docker rm "$cid" >/dev/null
PATH="$TMPNEW:$PATH" go test ./internal/bootstrap -run TestTataraMCP_AdvertisesScmProjectTools -count=1 -v
```

Expected output (PASS):

```
--- PASS: TestTataraMCP_AdvertisesScmProjectTools
PASS
ok  	github.com/szymonrychu/tatara-claude-code-wrapper/internal/bootstrap
```

### 1d. gofmt + full unit suite (no cli on PATH -> guard skips, rest runs)

```
gofmt -l internal/bootstrap/mcp_flowthrough_test.go   # expect empty output
go test ./... -race -count=1
```

Expected: all pass; `TestTataraMCP_AdvertisesScmProjectTools` reports SKIP when
no `tatara` is on PATH (that is by design - the real enforcement is the Docker
`test-guard` stage).

### 1e. COMMIT

```
git add internal/bootstrap/mcp_flowthrough_test.go
git commit -m "test(bootstrap): guard tatara mcp advertises issue_outcome"
```

---

## Task 2: Bump the pinned cli version in the Dockerfile

**File (Modify):** `Dockerfile` (line 6) and the guard-stage comment (lines
26-29).

### 2a. Bump the ARG default

Dockerfile:6 current:

```dockerfile
ARG TATARA_CLI_VERSION=0.5.0
```

Replace with:

```dockerfile
ARG TATARA_CLI_VERSION=0.6.0
```

### 2b. Refresh the guard-stage comment to name issue_outcome

Dockerfile:26-29 current:

```dockerfile
# Stage 3: guard -- verify the baked cli still advertises the tools the wrapper relies on.
# This stage runs `go test ./internal/bootstrap -run TestTataraMCP_AdvertisesScmProjectTools`
# with /usr/local/bin/tatara from the tatara-cli stage on PATH.  The image build FAILS if
# the pinned cli dropped propose_issue / review_verdict / pr_outcome.
```

Replace the last comment line with:

```dockerfile
# Stage 3: guard -- verify the baked cli still advertises the tools the wrapper relies on.
# This stage runs `go test ./internal/bootstrap -run TestTataraMCP_AdvertisesScmProjectTools`
# with /usr/local/bin/tatara from the tatara-cli stage on PATH.  The image build FAILS if
# the pinned cli dropped propose_issue / review_verdict / pr_outcome / issue_outcome.
```

The `RUN go test ...` line at Dockerfile:37 is unchanged - it already runs the
extended test by name.

### 2c. Verify the edits

```
grep -n 'TATARA_CLI_VERSION=' Dockerfile          # expect: 6:ARG TATARA_CLI_VERSION=0.6.0
grep -n 'issue_outcome' Dockerfile                # expect: the refreshed comment line
```

### 2d. COMMIT

```
git add Dockerfile
git commit -m "chore(image): pin baked tatara-cli to 0.6.0 (issue_outcome tool)"
```

---

## Task 3: Bump the cli version in the Makefile

**File (Modify):** `Makefile` (line 10).

### 3a. Bump the variable default

Makefile:10 current:

```make
TATARA_CLI_VERSION ?= 0.5.0
```

Replace with:

```make
TATARA_CLI_VERSION ?= 0.6.0
```

### 3b. Verify

```
grep -n 'TATARA_CLI_VERSION ?=' Makefile          # expect: 10:TATARA_CLI_VERSION ?= 0.6.0
```

### 3c. COMMIT

```
git add Makefile
git commit -m "chore(make): default TATARA_CLI_VERSION to 0.6.0"
```

---

## Task 4: Bump the chart version and appVersion

**File (Modify):** `charts/tatara-claude-code-wrapper/Chart.yaml` (lines 5-6).

### 4a. Bump both versions

Chart.yaml:5-6 current:

```yaml
version: 0.1.8
appVersion: "0.1.7"
```

Replace with:

```yaml
version: 0.1.9
appVersion: "0.1.8"
```

`version` is the chart's own patch bump (a new image is referenced).
`appVersion` tracks the wrapper image tag and gets the same patch bump.

### 4b. helm lint + unittest

```
cd /Users/szymonri/Documents/tatara/tatara-claude-code-wrapper
helm lint charts/tatara-claude-code-wrapper
make chart-test    # helm unittest charts/tatara-claude-code-wrapper
```

Expected: lint passes (0 chart(s) failed); unittests pass.

### 4c. COMMIT

```
git add charts/tatara-claude-code-wrapper/Chart.yaml
git commit -m "chore(chart): bump 0.1.9/appVersion 0.1.8 for cli 0.6.0 (issue_outcome)"
```

---

## Task 5: Build the image - the guard stage proves the contract end-to-end

This is the real RED-to-GREEN for the build-stage guard: with the cli bumped to
`0.6.0`, the `test-guard` stage must pass. (Building against the old `0.5.0`
would fail the guard - that is the regression protection working.)

```
cd /Users/szymonri/Documents/tatara/tatara-claude-code-wrapper
make image
```

Expected: build reaches and passes the `test-guard` stage:

```
#NN [test-guard N/N] RUN go test ./internal/bootstrap -run TestTataraMCP_AdvertisesScmProjectTools -count=1
#NN ok  	github.com/szymonrychu/tatara-claude-code-wrapper/internal/bootstrap
...
=> exporting to image
=> naming to harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper:<version>
```

If the `test-guard` stage fails with `tatara mcp must advertise "issue_outcome"`,
the pinned cli image does not actually advertise the tool - return to the
pre-flight check and confirm the cli plan (build-order step 2) shipped the tool.

No commit here; the image is a build artifact, not source. (Push/deploy is a
separate user-gated step per repo rule 10: build/deploy from `main` only, after
merge.)

---

## Task 6: Update MEMORY.md and ROADMAP.md

**Files (Modify):** `MEMORY.md`, `ROADMAP.md` (repo root).

### 6a. MEMORY.md - append one dated line

Append under the existing entries:

```
- 2026-06-11 autonomous-cron: bumped baked cli 0.5.0 -> 0.6.0 for the issue_outcome MCP tool; tools are auto-discovered (RegisterTataraMCP wires `tatara mcp`, no enumeration), so the wrapper change is a version-bump + guard-extension only. mcp_flowthrough_test.go now also asserts issue_outcome; the Dockerfile test-guard stage enforces it at image build.
```

### 6b. ROADMAP.md - move/close the wrapper line for this feature

If a "autonomous-cron wrapper" item exists, mark it done; otherwise add and
immediately close one line noting completion. Keep terse.

### 6c. COMMIT

```
git add MEMORY.md ROADMAP.md
git commit -m "docs: record cli 0.6.0 bump + issue_outcome guard (autonomous-cron)"
```

---

## Code review + final verification

Per repo rules: run `superpowers:requesting-code-review` on the branch diff,
apply critical/high findings, then:

```
gofmt -l .                                   # expect empty
go test ./... -race -count=1                 # expect PASS (guard SKIPs without cli on PATH)
helm lint charts/tatara-claude-code-wrapper  # expect pass
git log --oneline main..HEAD                 # expect the 5-6 commits above
```

The authoritative end-to-end proof is `make image` succeeding through the
`test-guard` stage (Task 5).

---

## Writing-plans self-review

**Spec coverage (design section 15 item 3 + contract lock):**

- "bump TATARA_CLI_VERSION to the new cli version" -> Task 2 (Dockerfile),
  Task 3 (Makefile). Covered.
- "ensure issue_outcome is registered (auto-discovered or explicit)" -> RESOLVED
  as auto-discovered. mcp_register.go:7-11 documents that the wrapper writes one
  `{"command":"tatara","args":["mcp"]}` entry and never enumerates tools; new
  operator tools flow through on cli rebuild. No registration code is added or
  needed. Covered by the version bump alone.
- "extend the build-stage guard test to assert issue_outcome present in the
  baked binary" -> Task 1. Adds `issue_outcome` to the asserted set in
  `TestTataraMCP_AdvertisesScmProjectTools`; enforced by the Dockerfile
  `test-guard` stage (Dockerfile:30-37, unchanged - already runs the test by
  name). Covered.
- "chart + appVersion bump" -> Task 4 (version 0.1.8 -> 0.1.9, appVersion
  "0.1.7" -> "0.1.8"). Covered.
- Contract-lock section 8 tool name `issue_outcome` -> used byte-for-byte in
  Task 1a and Task 2b. No other contract-lock wire types (PRRef/IssueRef/
  BoardItem/REST route/CRD/labels) touch this repo - the wrapper only observes
  the tool *name* over the MCP `tools/list`, which it asserts. Covered.

**Placeholder scan:** no "TBD", no "add appropriate X", no "similar to Task N".
Every code/config edit shows the exact before/after text. Version numbers are
concrete (`0.6.0`, `0.1.9`, `"0.1.8"`); the one conditional is the explicit
pre-flight note that if the cli shipped a tag other than `0.6.0`, substitute the
real tag everywhere it appears.

**Type/name consistency:** the only cross-repo identifier in this repo is the
MCP tool name `issue_outcome` (contract lock section 8). It appears identically
in the test assertion (Task 1a), the Dockerfile guard comment (Task 2b), and the
commit messages. The pinned cli version `0.6.0` is consistent across Dockerfile
ARG, Makefile var, pre-flight pull, Task 5 build, and commit messages. No Go
production types, route paths, label keys, or CRD fields are defined or
referenced here, so there is nothing else to keep byte-aligned with the lock.

**Ambiguity resolved:**

1. *Registration mechanism* - the prompt asked "auto-discovered or enumerated".
   Confirmed auto-discovered from mcp_register.go; plan is version-bump +
   guard-extension only, no registration code. Stated above.
2. *New cli version number* - the spec does not name it. The cli is at `0.5.0`
   (SCM-projects). A new MCP tool is a minor feature, so the next cli release is
   `0.6.0`. Pre-flight verifies the tag exists and instructs substituting the
   real tag if it differs.
3. *Chart vs appVersion bump amount* - prompt said "0.1.8 / appVersion 0.1.7 ->
   bump". Picked a patch bump for both (chart 0.1.8 -> 0.1.9, appVersion 0.1.7
   -> 0.1.8), matching the prior SCM-projects bump pattern in the wrapper git
   history (`chore(chart): bump 0.1.8/appVersion 0.1.7 for re-pinned cli 0.5.0`).
