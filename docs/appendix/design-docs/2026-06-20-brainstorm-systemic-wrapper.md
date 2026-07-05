# Plan: brainstorm-systemic - WS2 agent ultracode (wrapper)

Date: 2026-06-20
Repo: `tatara-claude-code-wrapper`
Spec: `docs/superpowers/specs/2026-06-20-brainstorm-systemic-and-agent-ultracode-design.md` (WS2)
Sibling plans: `...-operator.md`, `...-cli.md`

## For agentic workers

This plan is executed via `superpowers:subagent-driven-development`. Each
numbered Task is a self-contained TDD unit dispatched to a sonnet
implementation subagent; an opus subagent merges/reviews. Drive with
`superpowers:executing-plans` or `superpowers:subagent-driven-development`.
Every Task: write the failing test FIRST (`superpowers:test-driven-development`),
watch it fail, implement, watch it pass, then
`superpowers:requesting-code-review` before the commit. Run
`superpowers:verification-before-completion` before claiming any Task done.

Start from a FRESH worktree off `main`:
`git checkout main && git pull && git worktree add ../wrapper-ultracode -b feat/agent-ultracode`.
The current working tree may be on an unrelated feature branch; ignore it.
NEVER build or deploy from the worktree (hard rule 10). After all Tasks merge
to `main`, CI builds/pushes the agent image; deploy bumps `agent.image` in
`tatara-helmfile` (out of scope here, see Deploy note).

## Goal

Give the autonomous agent an "ultracode" lever: read an `EFFORT` env into
config, pass `--effort <level>` to the `claude` CLI, mirror `effortLevel` into
the agent `settings.json`, confirm the headless permission mode does not hang on
`Agent`/`Workflow` dispatch, and fold parallel-orchestration guidance into the
two baked discovery skills so the agent decomposes the cross-repo survey and
fans out one subagent per repo.

## Architecture

The wrapper boots an interactive `claude` PTY (`internal/session/pty.go`
`spawnClaude` -> `claudeArgs`). Launch flags come from `session.Config`
(`internal/session/session.go:37`), populated in `cmd/wrapper/app.go:61` from the
process `config` (`cmd/wrapper/config.go:13`), which reads env in `loadConfig`.
Settings.json is written once at boot by `internal/bootstrap/settings.go`
`writeSettings`, fed by `bootstrap.Params` (`internal/bootstrap/bootstrap.go:20`)
built in `cmd/wrapper/app.go:194` `buildBootstrapParams`. Baked skills live in
`templates/skills/` (`COPY templates/ /templates/` in the Dockerfile), installed
into the workspace by `internal/bootstrap/skills.go` `installSkills`; an
install-guard test exists in `internal/bootstrap/skills_test.go`.

The operator (sibling plan) sets `EFFORT` on the agent pod (default `xhigh`).
The wrapper is value-agnostic: it forwards whatever string `EFFORT` holds.

## Tech Stack

Go 1.25.0 (pinned `.mise.toml`), `log/slog`, `creack/pty`, `stretchr/testify`.
Build/test/lint via mise: `mise run test`, `mise run lint`, `mise run build`
(wrap `make`). Pinned claude in the image: Dockerfile
`ARG CLAUDE_CODE_VERSION=latest` (do NOT change), `ARG TATARA_CLI_VERSION` (do
NOT change). Effort knob verified against the live agent models (Opus 4.x /
Fable 5), all of which accept `low;medium;high;xhigh;max` on `--effort`.

## Global Constraints (spec hard rules - obey all)

1. Newest stable Go; Go directive pinned to the exact minor in `go.mod`.
2. KISS. Three similar lines beats a premature abstraction.
3. Boy-scout adjacent fixes; do not ask.
4. NEVER introduce tech-debt; if complex, record rationale in `MEMORY.md`.
5. Charts via `helm create` then edited (n/a this plan - no chart change).
6. No plain ENVs / lists in `values.yaml` (n/a - no chart change here).
7. Sonnet for implementation subagents; opus for merge/review.
8. EVERYTHING through superpowers.
9. Subagent-driven, parallel where independent (dispatch in one message).
10. Branch flow: worktree off `main` -> merge to `main` -> deploy from `main`.
11. JSON logs only (`log/slog`).
12. Log every business action at INFO with structured fields.
13. Metrics for everything that counts/times-out/can-fail; `/metrics` endpoint.
14. Charts cluster-agnostic (n/a - no chart change here).
15. Deploy ONLY via `tatara-helmfile` GitOps. Never `kubectl set image`/patch.

## Verified knowledge (read before coding - do not re-derive)

Effort knob, confirmed against `code.claude.com/docs/en/model-config`:

- CLI flag is `--effort <level>`; valid levels `low|medium|high|xhigh|max`.
  Live agent models (Opus 4.8/4.7, Fable 5) accept all five. If a level the
  model does not support is passed, claude degrades to the highest supported at
  or below it (e.g. `xhigh` -> `high` on Opus 4.6). So `EFFORT=xhigh` is always
  safe; no wrapper-side validation needed.
- settings.json key is `effortLevel`; it accepts ONLY `low|medium|high|xhigh`.
  **`max` is session-only and is rejected/ignored in `effortLevel`** (it works
  only via `--effort` or `CLAUDE_CODE_EFFORT_LEVEL`). The locked contract still
  says "write `effortLevel` when non-empty"; we honor it. The `--effort` flag is
  the authoritative carrier of the level, so an ignored `effortLevel: max` is
  belt-and-suspenders that is simply inert at `max` - acceptable, documented in
  Task 3 and `MEMORY.md`. Do NOT special-case `max` out; keep the wrapper
  value-agnostic per the contract.
- The `/effort ultracode` menu option is a SEPARATE Claude Code setting
  (`"ultracode": true` via `--settings`), distinct from `effortLevel`/`--effort`.
  Per spec D2, our "ultracode" = effort knob (this plan, Tasks 1-3) + the
  orchestration guidance baked into skills (Task 5). We deliberately do NOT use
  the `ultracode` boolean: the locked contract pins `--effort`/`effortLevel`,
  and the orchestration half ships as skill prose that survives prompt edits.
  Record this choice in `MEMORY.md`.

Permission gating, confirmed in-repo:

- `loadConfig` defaults `PERMISSION_MODE` -> `bypassPermissions`
  (`cmd/wrapper/config.go:73`); the operator sets `PERMISSION_MODE` on the pod
  but the bootstrap default is `bypassPermissions`.
- `buildBootstrapParams` (`cmd/wrapper/app.go:205-207`) sets
  `AllowedTools: readLines(cfg.AllowedToolsPath)` and
  `PermissionMode: cfg.PermissionMode`. The baked `allowed-tools.txt`
  (`ALLOWED_TOOLS_PATH=/etc/wrapper/allowed-tools.txt`) does **not exist / is
  empty** in the image, so `readLines` returns an empty slice.
- `writeSettings` (`internal/bootstrap/settings.go:25-33`) writes the
  `permissions` block only when `PermissionMode != "" || len(AllowedTools) > 0`.
  With the empty allow-list, the rendered block is exactly:
  `"permissions": { "defaultMode": "bypassPermissions" }` - NO `allow` key.
- **Verdict: `Agent`/`Workflow` dispatch is ALREADY auto-approved headless.**
  `bypassPermissions` approves every tool with no prompt, and there is no
  restrictive `allow` list to exclude `Agent`/`Workflow`. Even if a non-empty
  allow-list were later supplied, `bypassPermissions` overrides it (bypass
  approves all). Therefore Task 4 is a **verifying/guard test only** - no
  production code change - that locks in: (a) the rendered settings have
  `defaultMode: bypassPermissions`, (b) no `allow` key is emitted when the
  allow-list is empty, (c) if an allow-list IS provided it never silently drops
  `Agent`/`Workflow` under bypass. This guards against a future regression that
  flips `defaultMode` or adds a restrictive allow-list.

---

## Task 1 - Read `EFFORT` env into wrapper config

**Files**
- `cmd/wrapper/config.go` (add `Effort` field + `envOr("EFFORT", "")`)
- `cmd/wrapper/config_test.go` (default + override assertions)

**Interfaces**
- `config` struct (`cmd/wrapper/config.go:13`) gains: `Effort string`
- `loadConfig` populates: `Effort: envOr("EFFORT", "")`

### TDD steps

1.1 RED - extend the existing default + override tests. Append to
`cmd/wrapper/config_test.go`:

```go
func TestLoadConfig_EffortDefaultsEmpty(t *testing.T) {
	cfg, err := loadConfig(nil)
	require.NoError(t, err)
	require.Equal(t, "", cfg.Effort, "EFFORT unset must yield empty (no --effort)")
}

func TestLoadConfig_EffortFromEnv(t *testing.T) {
	t.Setenv("EFFORT", "xhigh")
	cfg, err := loadConfig(nil)
	require.NoError(t, err)
	require.Equal(t, "xhigh", cfg.Effort)
}
```

Run:
```
mise exec -- go test ./cmd/wrapper/ -run 'TestLoadConfig_Effort' -v
```
Expected: compile failure - `cfg.Effort undefined (type config has no field Effort)`.

1.2 GREEN - add the field and the env read.

In the `config` struct, after `Model string` (line 19), add:
```go
	Effort              string
```

In `loadConfig`, after `Model: envOr("MODEL", ""),` (line 72), add:
```go
		Effort:              envOr("EFFORT", ""),
```

Run:
```
mise exec -- go test ./cmd/wrapper/ -run 'TestLoadConfig_Effort' -v
```
Expected: `--- PASS: TestLoadConfig_EffortDefaultsEmpty` and
`--- PASS: TestLoadConfig_EffortFromEnv`, `ok ... tatara-claude-code-wrapper/cmd/wrapper`.

1.3 Full package + lint:
```
mise exec -- go test ./cmd/wrapper/...
mise run lint
```
Expected: `ok`, no lint findings.

**Commit:** `feat: read EFFORT env into wrapper config`

---

## Task 2 - Thread `Effort` to `session.Config` and append `--effort` in `claudeArgs`

**Files**
- `internal/session/session.go` (add `Effort` to `Config`)
- `cmd/wrapper/app.go` (pass `cfg.Effort` into `session.Config`)
- `internal/session/pty.go` (`claudeArgs` appends `--effort <level>`)
- `internal/session/recover_test.go` (extend `claudeArgs` table) OR a new
  `internal/session/pty_test.go`

**Interfaces**
- `session.Config` (`internal/session/session.go:37`) gains: `Effort string`
- `func (c Config) claudeArgs(resume bool) []string` (unchanged signature) -
  appends `"--effort", c.Effort` when `c.Effort != ""`.
- exported test seam already exists:
  `func ConfigClaudeArgs(c Config, resume bool) []string` (`pty.go:34`).

### TDD steps

2.1 RED - add to `internal/session/recover_test.go` (next to
`TestClaudeArgs_ContinueOnResume`):

```go
func TestClaudeArgs_EffortFlag(t *testing.T) {
	args := session.ConfigClaudeArgs(session.Config{Effort: "xhigh"}, false)
	// --effort and its value must be adjacent, in that order.
	idx := -1
	for i, a := range args {
		if a == "--effort" {
			idx = i
			break
		}
	}
	require.GreaterOrEqual(t, idx, 0, "expected --effort in args")
	require.Less(t, idx+1, len(args), "--effort missing its value")
	require.Equal(t, "xhigh", args[idx+1], "--effort value must be the level")
}

func TestClaudeArgs_NoEffortWhenEmpty(t *testing.T) {
	args := session.ConfigClaudeArgs(session.Config{}, false)
	for _, a := range args {
		require.NotEqual(t, "--effort", a, "empty Effort must NOT emit --effort")
	}
}
```

Run:
```
mise exec -- go test ./internal/session/ -run 'TestClaudeArgs' -v
```
Expected: compile failure - `unknown field Effort in struct literal of type session.Config`.

2.2 GREEN - add the field. In `internal/session/session.go` `Config`, after
`Model string` (line ~41), add:
```go
	Effort      string
```

2.3 GREEN - append the flag. In `internal/session/pty.go` `claudeArgs`, after the
`--model` block (lines 53-55), before the `resume` block, add:
```go
	if c.Effort != "" {
		args = append(args, "--effort", c.Effort)
	}
```

Run:
```
mise exec -- go test ./internal/session/ -run 'TestClaudeArgs' -v
```
Expected: `--- PASS: TestClaudeArgs_EffortFlag`, `--- PASS: TestClaudeArgs_NoEffortWhenEmpty`,
and the pre-existing `--- PASS: TestClaudeArgs_ContinueOnResume` still green.

2.4 GREEN - wire `cfg.Effort` into the session. In `cmd/wrapper/app.go:61`
`session.Config{...}`, after `Model: cfg.Model,` (line 65), add:
```go
		Effort:      cfg.Effort,
```

Run:
```
mise exec -- go build ./...
mise exec -- go test ./internal/session/... ./cmd/wrapper/...
mise run lint
```
Expected: build ok; both packages `ok`; no lint findings.

**Commit:** `feat: pass --effort to the claude CLI from EFFORT config`

---

## Task 3 - Write `effortLevel` into agent `settings.json`

**Files**
- `internal/bootstrap/bootstrap.go` (add `Effort` to `Params`)
- `internal/bootstrap/settings.go` (`writeSettings` adds `effortLevel`)
- `cmd/wrapper/app.go` (pass `cfg.Effort` into `buildBootstrapParams`)
- `internal/bootstrap/settings_test.go` (NEW - direct `writeSettings` tests)

**Interfaces**
- `bootstrap.Params` (`internal/bootstrap/bootstrap.go:20`) gains: `Effort string`
- `writeSettings(p Params, claudeHome string) error` (unchanged signature) -
  sets `settings["effortLevel"] = p.Effort` when `p.Effort != ""`, omits the key
  otherwise.

`writeSettings` is package-private. There is no existing `settings_test.go`, so
add one in `package bootstrap` (internal test) to call `writeSettings` directly
and assert the JSON. (The end-to-end `bootstrap_test.go` is external `package
bootstrap_test` and only reads the file via `Render`; a focused internal test is
clearer and cheaper.)

### TDD steps

3.1 RED - create `internal/bootstrap/settings_test.go`:

```go
package bootstrap

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func readSettings(t *testing.T, home string) map[string]any {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(home, "settings.json"))
	if err != nil {
		t.Fatalf("read settings.json: %v", err)
	}
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatalf("unmarshal settings.json: %v", err)
	}
	return m
}

func TestWriteSettings_EffortLevelWhenSet(t *testing.T) {
	home := t.TempDir()
	if err := writeSettings(Params{HookCommand: "/x", Effort: "xhigh"}, home); err != nil {
		t.Fatalf("writeSettings: %v", err)
	}
	m := readSettings(t, home)
	if m["effortLevel"] != "xhigh" {
		t.Fatalf("effortLevel = %v, want xhigh", m["effortLevel"])
	}
}

func TestWriteSettings_NoEffortLevelWhenEmpty(t *testing.T) {
	home := t.TempDir()
	if err := writeSettings(Params{HookCommand: "/x"}, home); err != nil {
		t.Fatalf("writeSettings: %v", err)
	}
	m := readSettings(t, home)
	if _, ok := m["effortLevel"]; ok {
		t.Fatalf("effortLevel must be absent when Effort empty, got %v", m["effortLevel"])
	}
}
```

Run:
```
mise exec -- go test ./internal/bootstrap/ -run 'TestWriteSettings_Effort|TestWriteSettings_NoEffort' -v
```
Expected: compile failure - `unknown field Effort in struct literal of type bootstrap.Params`.

3.2 GREEN - add the field. In `internal/bootstrap/bootstrap.go` `Params`, near
`PermissionMode string` (line 30), add:
```go
	Effort                          string // claude reasoning-effort level for the agent session
```

3.3 GREEN - write the key. In `internal/bootstrap/settings.go` `writeSettings`,
after the `settings := map[string]any{...}` literal (line 24), before the
`permissions` block, add:
```go
	if p.Effort != "" {
		settings["effortLevel"] = p.Effort
	}
```

Run:
```
mise exec -- go test ./internal/bootstrap/ -run 'TestWriteSettings_Effort|TestWriteSettings_NoEffort' -v
```
Expected: `--- PASS: TestWriteSettings_EffortLevelWhenSet`,
`--- PASS: TestWriteSettings_NoEffortLevelWhenEmpty`.

3.4 GREEN - wire `cfg.Effort` into params. In `cmd/wrapper/app.go:194`
`buildBootstrapParams`, after `PermissionMode: cfg.PermissionMode,` (line 207),
add:
```go
		Effort:          cfg.Effort,
```

Run:
```
mise exec -- go build ./...
mise exec -- go test ./internal/bootstrap/... ./cmd/wrapper/...
mise run lint
```
Expected: build ok; both packages `ok`; no lint findings.

3.5 MEMORY note - append to `MEMORY.md` (one line, dated):
`- 2026-06-20: agent ultracode = EFFORT env -> --effort flag (authoritative, accepts max) + effortLevel settings.json key (belt-and-suspenders; claude IGNORES effortLevel=max, accepts only low/medium/high/xhigh). Wrapper stays value-agnostic per locked contract; operator CRD default EFFORT=xhigh. Did NOT use the separate "ultracode":true setting - orchestration ships via baked skills instead.`

**Commit:** `feat: write effortLevel into agent settings.json from EFFORT`

---

## Task 4 - Guard: headless permission mode permits Agent/Workflow dispatch

**Files**
- `internal/bootstrap/settings_test.go` (append guard tests; NO production change)

**Interfaces** - none. This Task adds verifying tests only.

**Why no code change:** per "Verified knowledge", the live boot renders
`"permissions": {"defaultMode": "bypassPermissions"}` with no `allow` key (empty
baked allow-list), which auto-approves `Agent`/`Workflow` with no prompt. The
risk is a *future* regression (someone flips `defaultMode`, or adds a restrictive
`allow` list that omits `Agent`/`Workflow`). These guards lock the current,
correct behavior.

### TDD steps

4.1 RED-then-GREEN (characterization) - append to
`internal/bootstrap/settings_test.go`:

```go
func TestWriteSettings_BypassDefaultMode_AutoApprovesDispatch(t *testing.T) {
	home := t.TempDir()
	// Mirrors the live boot: bypassPermissions, empty allow-list.
	if err := writeSettings(Params{HookCommand: "/x", PermissionMode: "bypassPermissions"}, home); err != nil {
		t.Fatalf("writeSettings: %v", err)
	}
	m := readSettings(t, home)
	perms, ok := m["permissions"].(map[string]any)
	if !ok {
		t.Fatalf("permissions block missing: %v", m["permissions"])
	}
	if perms["defaultMode"] != "bypassPermissions" {
		t.Fatalf("defaultMode = %v, want bypassPermissions (auto-approves Agent/Workflow headless)", perms["defaultMode"])
	}
	// With an empty allow-list, NO allow key is emitted, so nothing can exclude
	// Agent/Workflow. (A present-but-partial allow list under bypass is still
	// fully permissive, but we assert absence to catch an accidental restriction.)
	if _, present := perms["allow"]; present {
		t.Fatalf("allow key must be absent under empty allow-list, got %v", perms["allow"])
	}
}

func TestWriteSettings_AllowListNeverDropsDispatchTools(t *testing.T) {
	// If a non-empty allow-list is ever supplied, it must include the dispatch
	// tools (Agent, Workflow) so a future switch away from bypassPermissions
	// would not hang a headless turn on subagent/workflow approval.
	home := t.TempDir()
	allow := []string{"Bash", "Edit", "Agent", "Workflow"}
	if err := writeSettings(Params{HookCommand: "/x", PermissionMode: "bypassPermissions", AllowedTools: allow}, home); err != nil {
		t.Fatalf("writeSettings: %v", err)
	}
	m := readSettings(t, home)
	perms := m["permissions"].(map[string]any)
	got, _ := perms["allow"].([]any)
	var asStr []string
	for _, v := range got {
		asStr = append(asStr, v.(string))
	}
	for _, want := range []string{"Agent", "Workflow"} {
		found := false
		for _, a := range asStr {
			if a == want {
				found = true
			}
		}
		if !found {
			t.Fatalf("dispatch tool %q missing from allow-list %v", want, asStr)
		}
	}
}
```

Run:
```
mise exec -- go test ./internal/bootstrap/ -run 'TestWriteSettings_Bypass|TestWriteSettings_AllowListNeverDrops' -v
```
Expected: both PASS immediately - `writeSettings` already emits
`defaultMode: bypassPermissions` and round-trips the allow-list. These tests are
characterization guards: they pin the dispatch-safe behavior so a later change
that breaks it fails CI.

4.2 Full bootstrap package + lint:
```
mise exec -- go test ./internal/bootstrap/...
mise run lint
```
Expected: `ok`, no lint findings.

**Commit:** `test: guard headless bypass permits Agent/Workflow dispatch`

---

## Task 5 - Fold orchestration guidance into the baked discovery skills

**Files**
- `templates/skills/tatara-deep-research/SKILL.md` (markdown edit)
- `templates/skills/tatara-health-check/SKILL.md` (markdown edit)
- `internal/bootstrap/skills_test.go` (extend the install-guard /
  content-assertion test)

**Interfaces** - none (markdown + a test assertion). The "test" for the prose is
(a) a content guard in `skills_test.go` asserting the new orchestration section
exists in each baked SKILL.md, plus (b) the documented exact diff below.

The repo already has install-guard tests for these skills
(`TestDiscoverySkillsPresentAndValid` and `TestInstallSkills_CopiesDiscoverySkills`
in `internal/bootstrap/skills_test.go`), so per the brief we ADD a content guard
rather than rely on manual verification.

### TDD steps

5.1 RED - add a content guard to `internal/bootstrap/skills_test.go`:

```go
func TestDiscoverySkillsCarryOrchestrationGuidance(t *testing.T) {
	root := "../../templates/skills"
	for _, name := range []string{"tatara-deep-research", "tatara-health-check"} {
		b, err := os.ReadFile(filepath.Join(root, name, "SKILL.md"))
		if err != nil {
			t.Fatalf("read %s: %v", name, err)
		}
		s := string(b)
		for _, want := range []string{
			"## Orchestration",
			"maximum effort",
			"one parallel subagent per repo",
			"Workflow",
		} {
			if !strings.Contains(s, want) {
				t.Fatalf("%s: orchestration guidance missing %q", name, want)
			}
		}
	}
}
```

Run:
```
mise exec -- go test ./internal/bootstrap/ -run 'TestDiscoverySkillsCarryOrchestrationGuidance' -v
```
Expected: FAIL - `tatara-deep-research: orchestration guidance missing "## Orchestration"`.

5.2 GREEN - edit `templates/skills/tatara-deep-research/SKILL.md`. Insert a new
section immediately AFTER the `## Hard constraints` block (after the
`- Communication only via \`tatara\` MCP tools.` line, line ~31) and BEFORE the
`The \`tatara\` tools auto-scope...` paragraph. Exact text to insert:

```markdown

## Orchestration (run at maximum effort)

This is a deep, cross-repo research turn - run it at **maximum effort** and
orchestrate, do not work single-threaded:

- The pod's `EFFORT` is already set high; sustain deep multi-step reasoning and
  read widely before deciding. Spend the thinking budget on the survey.
- **Decompose** the cross-repo survey into one independent unit of work per
  repository in the Project (the repos under `/workspace/*/` plus the cross-repo
  graph view).
- **Dispatch one parallel subagent per repo** to gather that repo's state
  (roadmap themes, fragile/load-bearing code via the `code_*` graph tools, open
  issues/MRs, recurring debt). Launch them in a single batch so they run
  concurrently; do not serialize what can fan out.
- Use a **Workflow** to fan the per-repo investigations out and then **synthesize**
  their findings into the single highest-leverage SYSTEMIC opportunity - a
  pattern spanning >=2 repos, a platform-wide gap, or recurring debt - in
  preference to a one-repo tweak.
- Only after synthesis do you choose the propose-vs-comment action below. For a
  genuinely systemic improvement you MAY open one `propose_issue` per affected
  repo sharing a single `systemicId` you generate (bounded, <=6); the operator
  correlates them and counts the group as one against the proposal cap.

```

5.3 GREEN - edit `templates/skills/tatara-health-check/SKILL.md`. Insert the same
kind of section immediately AFTER the `## Hard constraints` block (after the
`- Communication only via \`tatara\` MCP tools.` line, line ~33) and BEFORE the
`The \`tatara\` tools auto-scope...` paragraph. Exact text to insert:

```markdown

## Orchestration (run at maximum effort)

This is a multi-repo health survey - run it at **maximum effort** and
orchestrate, do not work single-threaded:

- The pod's `EFFORT` is already set high; sustain deep multi-step reasoning and
  reproduce failures before deciding. Spend the thinking budget on the survey.
- **Decompose** the survey into one independent unit of work per repository in
  the Project (the repos under `/workspace/*/` plus the cross-repo graph view).
- **Dispatch one parallel subagent per repo** to probe that repo's five health
  dimensions (CI failures via `mise run test`/`lint`, coverage gaps, code to
  simplify, missing pipeline steps, other tech-debt) and its `code_*` graph
  signal. Launch them in a single batch so they run concurrently.
- Use a **Workflow** to fan the per-repo probes out and then **synthesize** their
  findings: prefer a systemic health issue recurring across >=2 repos or a
  platform-wide pipeline gap over a single-repo decay, then pick the ONE
  highest-leverage, well-evidenced finding.
- Only after synthesis do you compose the proposal below.

```

5.4 GREEN - run the content guard and the existing skill install guards:
```
mise exec -- go test ./internal/bootstrap/ -run 'Skills' -v
```
Expected: `TestDiscoverySkillsCarryOrchestrationGuidance` PASS,
`TestDiscoverySkillsPresentAndValid` PASS (frontmatter unchanged, body still
non-empty), `TestInstallSkills_CopiesDiscoverySkills` PASS.

5.5 Manual verification (documented) - confirm the rendered diff:
```
git -C templates/skills diff -- tatara-deep-research/SKILL.md tatara-health-check/SKILL.md
```
Expected: only the two `## Orchestration (run at maximum effort)` sections added,
no changes to frontmatter, hard constraints, workflow steps, or anti-patterns.

5.6 Whole-repo gate:
```
mise run test
mise run lint
```
Expected: all packages `ok`, no lint findings.

**Commit:** `feat: bake parallel-orchestration guidance into discovery skills`

---

## Final verification (before finishing the branch)

`superpowers:verification-before-completion`:
```
mise run test
mise run lint
mise run build
mise exec -- go vet ./...
```
Expected: every command exits 0; `mise run test` ends `ok` for every package
including `internal/session`, `internal/bootstrap`, `cmd/wrapper`.

Then `superpowers:requesting-code-review` (opus) on the full diff, fix
critical/high findings, `pre-commit run --all-files`, and
`superpowers:finishing-a-development-branch` to merge to `main`, then remove the
worktree.

## Deploy note (out of scope, do NOT do here)

After all five commits are on `tatara-claude-code-wrapper` `main`, CI builds and
pushes the agent image to harbor (hard rule 15 - never local buildx). The deploy
is a `tatara-helmfile` MR bumping the wrapper `agent.image` tag to the new digest
(per `[[tatara-helmfile-reextracted-2026-06-13]]` /
`[[deploy-only-via-tatara-helmfile]]`). The operator side (CRD
`Project.Spec.Agent.Effort` enum default `xhigh`, `BuildPod` `EFFORT` env) ships
from the sibling operator plan and MUST land before this wrapper change has any
effect (no `EFFORT` env -> empty `cfg.Effort` -> no `--effort`, no `effortLevel`
- safe no-op). Do NOT bump the Dockerfile `CLAUDE_CODE_VERSION` /
`TATARA_CLI_VERSION` pins in this plan.
```
