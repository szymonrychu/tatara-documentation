# Phase 0 - tatara docs repo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Initialise the `tatara` docs/architecture repo at `/Users/szymonri/Documents/tatara` with all baseline files (README, ARCHITECTURE, LICENSE, MEMORY, ROADMAP, CLAUDE.md, .gitignore), commit them on top of the existing spec commit, and publish to `github.com/szymonrychu/tatara`.

**Architecture:** Docs-only repo. No code, no helm, no umbrella helmfile. Eight subdirectories (one per phase) are gitignored and host their own future repos. The repo's job is to be the index every future component repo links back to, and to carry the cross-repo CLAUDE.md hard rules + spec history.

**Tech Stack:** Plain Markdown. AGPLv3 license file lifted from `/Users/szymonri/Documents/tatara-old/LICENSE`. `gh` CLI for GitHub repo creation. `git` already initialised on `main` with spec commit `ad442d9`.

**Reference inputs (read once at start):**
- `/Users/szymonri/Documents/tatara/docs/superpowers/specs/2026-05-24-tatara-phases-0-1-design.md` (this plan implements its phase-0 section)
- `/Users/szymonri/Documents/tatara-old/README.md` (lineage and tone)
- `/Users/szymonri/Documents/tatara-old/LICENSE` (copy verbatim)
- `/Users/szymonri/.claude/CLAUDE.md` (writing-rules: no em dashes, no arrows, no smart quotes; KISS; superpowers workflow)

---

## File Structure

All files at repo root (`/Users/szymonri/Documents/tatara/`):

| File             | Responsibility                                                            |
| ---------------- | ------------------------------------------------------------------------- |
| `LICENSE`        | AGPLv3 text, copied verbatim from tatara-old                              |
| `CLAUDE.md`      | Hard-rule workflow that every component repo copies. Single source.       |
| `.gitignore`     | Ignores `tatara-*/` child repos and editor/OS noise.                      |
| `README.md`      | One-page overview, repo split table, status, links to child repos.        |
| `ARCHITECTURE.md`| Component diagram, data flow, OIDC model, chart/deploy model.             |
| `MEMORY.md`      | Cross-repo decisions only. Today's entries seed it.                       |
| `ROADMAP.md`     | Phase-level roadmap with status per repo.                                 |

The spec already lives at `docs/superpowers/specs/2026-05-24-tatara-phases-0-1-design.md` (committed in `ad442d9`). This plan adds the baseline files only - no further docs structure.

---

### Task 1: Write LICENSE

**Files:**
- Create: `/Users/szymonri/Documents/tatara/LICENSE`

- [ ] **Step 1: Copy AGPLv3 from tatara-old**

```bash
cp /Users/szymonri/Documents/tatara-old/LICENSE /Users/szymonri/Documents/tatara/LICENSE
```

- [ ] **Step 2: Verify the file is AGPLv3, ~34 KB**

Run: `head -5 /Users/szymonri/Documents/tatara/LICENSE && wc -l /Users/szymonri/Documents/tatara/LICENSE`
Expected: First 5 lines contain "GNU AFFERO GENERAL PUBLIC LICENSE" and "Version 3"; line count ~661.

- [ ] **Step 3: Commit**

```bash
cd /Users/szymonri/Documents/tatara
git add LICENSE
git commit -m "chore: add AGPLv3 LICENSE"
```

---

### Task 2: Write CLAUDE.md

**Files:**
- Create: `/Users/szymonri/Documents/tatara/CLAUDE.md`

- [ ] **Step 1: Write CLAUDE.md verbatim**

Write the file with this exact content (no em dashes, no arrows, no smart quotes):

```markdown
# CLAUDE.md - tatara

This file briefs any Claude session working on the `tatara` repo or any
of its component child repos (`tatara-memory`, `tatara-cli`, etc.). Every
child repo carries a copy of this file at its own root. Treat it as the
canonical contract.

## What this repo is

`tatara` is the docs and architecture index for the tatara platform. The
platform is split into eight independent GitHub repositories under
`szymonrychu/`. See `README.md` for the full list and `ARCHITECTURE.md`
for how they fit together. The previous monolithic implementation lives
at `~/Documents/tatara-old` as a reference.

## What this repo is NOT

- Not a monorepo. Each component is its own git repo with its own CI,
  helm chart, Dockerfile, MEMORY.md and ROADMAP.md.
- Not an umbrella helmfile. There is no top-level `helmfile.yaml.gotmpl`
  composing the platform; each component deploys itself.
- Not a place for code. Code belongs in the component repo it serves.

## On-disk layout

```
~/Documents/tatara/                   # this repo
├── tatara-memory/                    # child repo (gitignored)
├── tatara-cli/                       # child repo (gitignored)
├── tatara-memory-repo-ingester/      # child repo (gitignored)
├── tatara-claude-code-wrapper/       # child repo (gitignored)
├── tatara-argo-workflows/            # child repo (gitignored)
├── tatara-tasks/                     # child repo (gitignored)
└── tatara-gitlab-bridge/             # child repo (gitignored)
```

Each child clones from `github.com/szymonrychu/<name>` into the matching
subdirectory. The parent `.gitignore` keeps them out of this repo.

## Hard rules (copied to every component repo's CLAUDE.md)

1. **Newest stable Go** for any Go service. Pin the Go directive to the
   exact minor in `go.mod`.
2. **KISS, always.** Prefer simplicity over cleverness. Three similar
   lines is better than a premature abstraction.
3. **Boy-scout rule on adjacent issues.** If you see something easy to
   fix alongside current work, fix it. Do not ask.
4. **NEVER introduce tech-debt.** If a thing is complex, call it out in
   `MEMORY.md` with the rationale. Never defer cleanup to "later".
5. **Charts created via `helm create <name>`** then edited. Never
   hand-rolled.
6. **No plain ENVs in values.yaml. No lists in values.yaml.** All inputs
   map: camelCase scalar in `values.yaml` -> kebab-case key in
   ConfigMap/Secret -> workload consumes via `envFrom`. Genuinely
   list-shaped data is rendered into a templated ConfigMap and read at
   runtime.
7. **Sonnet for implementation. Opus for merges.** Implementation
   subagents are sonnet (`claude-sonnet-4-6` or current stable). The
   merge subagent that integrates parallel work is opus. Plan and
   review work runs in opus.
8. **EVERYTHING through superpowers.** brainstorming, writing-plans,
   test-driven-development, systematic-debugging,
   requesting-code-review, verification-before-completion,
   subagent-driven-development, using-git-worktrees,
   finishing-a-development-branch are mandatory. If a skill might
   apply, invoke it.
9. **Subagent-driven, parallel development** where tasks are
   independent. Dispatch in a single message for true parallelism.
10. **Branch flow:** worktree off `main` -> develop in worktree -> merge
    back to source repo `main` -> cleanup worktree -> build/deploy from
    `main` only. NEVER build or deploy from a worktree. Cleanup
    worktrees regularly.
11. **JSON logs only.** Stdlib `log/slog` in Go. Same logger structure
    everywhere.
12. **Log every business action at INFO** with structured fields
    (request_id, user, action, resource_id, duration_ms where
    relevant). WARN and ERROR used appropriately.
13. **Metrics for everything that counts, times out, or can fail.**
    Counters for events, histograms for durations, gauges for
    in-flight. Expose `/metrics` Prometheus endpoint on every service.

## Writing rules

- No em dashes. No smart quotes. No arrows. No decorative Unicode.
  Plain hyphens and straight quotes.
- No preamble. No recap unless asked. One line at most: what changed,
  any non-obvious choice.
- Show diffs, not whole files, for anything > 30 lines that already
  exists.
- No docstrings, type annotations, or comments on code not being
  changed.
- No error handling for scenarios that cannot happen.

## What I want from a Claude session here

- Read `MEMORY.md` and `ROADMAP.md` before non-trivial work.
- Update `MEMORY.md` when you make a non-obvious decision or hit a
  dead-end. One line per entry, dated.
- Update `ROADMAP.md` when you complete or re-scope a phase.
- Use `/handoff` if you are approaching context limits; do not soldier
  on.
```

- [ ] **Step 2: Verify the file matches the spec hard rules**

Run: `grep -c "Newest stable Go\|KISS\|boy-scout\|tech-debt\|helm create\|values.yaml\|Sonnet for implementation\|superpowers\|worktree\|JSON logs\|INFO\|Metrics" /Users/szymonri/Documents/tatara/CLAUDE.md`
Expected: at least 11 matches (one per hard rule).

- [ ] **Step 3: Verify no forbidden characters**

Run: `grep -cE "—|→|…|'|'|"|"" /Users/szymonri/Documents/tatara/CLAUDE.md`
Expected: `0`

- [ ] **Step 4: Commit**

```bash
cd /Users/szymonri/Documents/tatara
git add CLAUDE.md
git commit -m "docs: add CLAUDE.md with hard-rule workflow"
```

---

### Task 3: Verify .gitignore (already committed during worktree bootstrap)

`.gitignore` was committed on `main` during worktree bootstrap with the
final content. This task only verifies it is present and complete; no
file changes or new commits.

**Files:**
- Verify: `/Users/szymonri/Documents/tatara/.gitignore`

- [ ] **Step 1: Verify file exists and has expected entries**

Run:
```bash
cd /Users/szymonri/Documents/tatara
grep -cE '^/(tatara-memory|tatara-cli|tatara-memory-repo-ingester|tatara-claude-code-wrapper|tatara-argo-workflows|tatara-tasks|tatara-gitlab-bridge)/$' .gitignore
grep -c '^\.worktrees/$' .gitignore
```
Expected: first command prints `7`, second prints `1`.

- [ ] **Step 2: Verify all seven child paths actually get ignored**

Run:
```bash
cd /Users/szymonri/Documents/tatara
mkdir -p tatara-memory tatara-cli tatara-memory-repo-ingester tatara-claude-code-wrapper tatara-argo-workflows tatara-tasks tatara-gitlab-bridge
out=$(git status --short)
rmdir tatara-memory tatara-cli tatara-memory-repo-ingester tatara-claude-code-wrapper tatara-argo-workflows tatara-tasks tatara-gitlab-bridge
echo "$out"
```
Expected: empty output. No `tatara-*/` lines.

- [ ] **Step 3: No commit needed**

This task does not produce any commits.

---

### Task 4: Write README.md

**Files:**
- Create: `/Users/szymonri/Documents/tatara/README.md`

- [ ] **Step 1: Write README.md**

```markdown
# tatara

> Iterative knowledge work for agents on Kubernetes, split into independently shipped pieces.

`tatara` is the docs and architecture index for the **tatara platform**: a
set of microservices that give agents (Claude Code, Codex, anything that
speaks MCP) durable memory, structured task tracking, and Kubernetes-native
execution. The platform is split into eight independent GitHub
repositories under `github.com/szymonrychu/`.

The name comes from the traditional Japanese iron-smelting forge: a
collective, iterative process around a permanent substrate. That is the
metaphor: ephemeral agent sessions doing iterative work against a
permanent knowledge graph.

A previous monolithic implementation lives at
[`tatara-old`](https://github.com/szymonrychu/tatara-old) (or
`~/Documents/tatara-old` locally) as a reference; the split is being
written fresh.

## Status

Phase 0 (this repo). Phase 1 (`tatara-memory`) starts next. See
[`ROADMAP.md`](./ROADMAP.md).

## Component repos

| Phase | Repo                              | Purpose                                                           |
| ----- | --------------------------------- | ----------------------------------------------------------------- |
| 0     | `tatara`                          | this repo - docs and architecture index                           |
| 1     | `tatara-memory`                   | REST memory service over LightRAG, OIDC-gated, helm-deployed      |
| 2     | `tatara-cli`                      | Homebrew-installable CLI with `login`, `raw`, `mcp`, `mcp-config` |
| 3     | `tatara-memory-repo-ingester`     | Batch tool that walks a repo and bulk-ingests into memory         |
| 4     | `tatara-claude-code-wrapper`      | Container image with Claude Code + tatara-cli pre-configured      |
| 5     | `tatara-argo-workflows`           | WorkflowTemplates and a small controller                          |
| 6     | `tatara-tasks`                    | Task tracking service (REST) and MCP wrapper                      |
| 7     | `tatara-gitlab-bridge`            | GitLab webhook -> Argo workflow bridge                            |

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for how they fit together.

## Local layout

Every component repo clones as a subdirectory of this one. The parent
`.gitignore` keeps them out of this repo:

```
~/Documents/tatara/
├── README.md  ARCHITECTURE.md  LICENSE  MEMORY.md  ROADMAP.md  CLAUDE.md
├── docs/superpowers/{specs,plans}/
├── tatara-memory/                    # github.com/szymonrychu/tatara-memory
├── tatara-cli/                       # github.com/szymonrychu/tatara-cli
├── tatara-memory-repo-ingester/      # ...
├── tatara-claude-code-wrapper/
├── tatara-argo-workflows/
├── tatara-tasks/
└── tatara-gitlab-bridge/
```

## How work happens here

Read [`CLAUDE.md`](./CLAUDE.md) for the full workflow contract. The short
version:

- Every phase begins with a brainstorming session (`superpowers:brainstorming`).
- Every implementation ships from a written plan (`superpowers:writing-plans`).
- Development happens in git worktrees; build/deploy run only from
  `main` on the source repo.
- Sonnet subagents implement in parallel; an opus subagent merges.

## License

GNU AGPLv3. See [`LICENSE`](./LICENSE).
```

- [ ] **Step 2: Verify all eight phases mentioned and table renders**

Run: `grep -cE '^\| [0-7] ' /Users/szymonri/Documents/tatara/README.md`
Expected: `8`

- [ ] **Step 3: Verify no forbidden characters**

Run: `grep -cE "—|→|…|'|'|"|"" /Users/szymonri/Documents/tatara/README.md`
Expected: `0`

- [ ] **Step 4: Commit**

```bash
cd /Users/szymonri/Documents/tatara
git add README.md
git commit -m "docs: add README with phase split and platform overview"
```

---

### Task 5: Write ARCHITECTURE.md

**Files:**
- Create: `/Users/szymonri/Documents/tatara/ARCHITECTURE.md`

- [ ] **Step 1: Write ARCHITECTURE.md**

```markdown
# Architecture

How the eight tatara components fit together. For the detailed design of
any single component, see that repo's own docs and the brainstorming spec
under `docs/superpowers/specs/`.

## Component diagram

```
                          users / tatara-cli (device flow)
                                   |
                                   v JWT (aud: tatara-memory)
                          +------------------+
                          |  tatara-memory   |  <-- phase 1
                          |   (Go service)   |
                          +--------+---------+
                                   |
                  +----------------+----------------+
                  |                                 |
                  v                                 v
            +-----------+                    +-------------+
            | LightRAG  |                    | Postgres    |
            | (upstream)|                    | (cnpg)      |
            +-----+-----+                    +-------------+
                  |                                 ^
                  |                                 |
                  v                                 |
            +-----------+               ingest job state
            |  Neo4j    |
            +-----------+

                          tatara-cli (phase 2)
                          tatara-memory-repo-ingester (phase 3)
                          tatara-claude-code-wrapper (phase 4)
                          tatara-argo-workflows (phase 5)
                          tatara-tasks (phase 6)
                          tatara-gitlab-bridge (phase 7)

                          all phase 2-7 clients talk to tatara-memory over REST,
                          authenticated with OIDC bearer tokens from
                          auth.szymonrichert.pl (master realm).
```

## Data flow

1. A user (or CI job, or workflow pod) gets a bearer token from Keycloak.
   Browser users go through `tatara-cli login` (OIDC device flow). Service
   accounts use the client-credentials grant on the confidential
   `tatara-memory` client.
2. The client calls `tatara-memory` over HTTPS with
   `Authorization: Bearer <token>`.
3. `tatara-memory` verifies the JWT (signature via JWKS, `iss`, `exp`,
   `aud` contains `tatara-memory`).
4. The service translates the domain request (memory ingest, query,
   entity update, edge mutation) into one or more LightRAG calls.
5. LightRAG persists into Postgres (cnpg) and Neo4j.
6. Async ingest jobs persist their own state in a separate `tatara_memory`
   database on the same cnpg cluster, and resume on restart.

## OIDC model

- **Realm:** `master` at `https://auth.szymonrichert.pl`.
- **Clients** (defined in `~/Documents/infra/terraform/keycloak/tatara_clients.tf`):
  - `tatara-memory` - confidential, service-accounts enabled, no browser
    flows. Acts as the **audience** for tokens that should reach the
    memory service.
  - `tatara-cli` - public, device-authorization-grant enabled, used by
    the CLI and by human-driven flows.
- **Scope:** `tatara` (in-token scope), with an audience mapper adding
  `tatara-memory` to the `aud` claim. `tatara-cli` has `tatara` in its
  default scopes, so every CLI-issued token carries the right audience.
- **Verification:** `tatara-memory` discovers JWKS via
  `/.well-known/openid-configuration` and uses `coreos/go-oidc` for
  rotation handling. No per-user RBAC in v1.

## Chart and deploy model

- Each component repo ships **its own helm chart** under
  `charts/<component-name>/`, created via `helm create` then edited.
  Subcharts live under `Chart.yaml` dependencies; no hand-rolled
  upstream copies.
- Each component repo has **its own** `helmfile.yaml.gotmpl` at the
  root, single `default` environment, mirroring the pattern from
  `tatara-old/helmfile.yaml.gotmpl`.
- There is **no umbrella helmfile** in this repo. Composing the
  platform deploy is out of scope for now; each repo deploys itself.
- Values discipline: `values.yaml` holds only camelCase scalars. All
  inputs map through ConfigMap or Secret (kebab-case keys) into the
  workload via `envFrom`. Secrets are SOPS-encrypted in environment
  override files; never plaintext.

## Build and release flow

For every component repo:

1. Development happens in a git worktree off `main` of that repo.
2. Subagents (sonnet) implement in parallel where independent.
3. A merge subagent (opus) integrates the work back onto `main`.
4. The worktree is cleaned up.
5. Container images push to `harbor.szymonrichert.pl` and
   `helmfile apply` run only from `main` on the source repo. Never from
   a worktree.

## Phases 2-7 (sketches)

Detailed designs come in their own brainstorming sessions.

- **tatara-cli** (phase 2). Go + cobra, homebrew tap `szymonrychu/tap`.
  Uses `golang.org/x/oauth2` device-flow against the `tatara-cli`
  client. Subcommands: `login`, `logout`, `raw <verb> <path>`, `mcp`
  (stdio MCP server translating MCP tool calls into REST calls against
  tatara-memory and future services), `mcp-config <dir>` (writes
  `.mcp.json` for the current dir). Mirrors `spellslinger-cli`.
- **tatara-memory-repo-ingester** (phase 3). Go batch tool. Walks a git
  repo, chunks files by a language-aware splitter, POSTs to
  `/v1/memories:bulk`, polls job status until terminal.
- **tatara-claude-code-wrapper** (phase 4). Container image bundling
  Claude Code and tatara-cli pre-installed and pre-configured.
- **tatara-argo-workflows** (phase 5). WorkflowTemplates and a small
  controller, lifted and cleaned from tatara-old.
- **tatara-tasks** (phase 6). REST task service plus MCP wrapper.
  Replaces the TaskBoard CRD path from tatara-old.
- **tatara-gitlab-bridge** (phase 7). Webhook to workflow bridge.
  GitLab only; GitHub deferred.
```

- [ ] **Step 2: Verify all eight phases mentioned**

Run: `grep -cE "phase [0-7]" /Users/szymonri/Documents/tatara/ARCHITECTURE.md`
Expected: at least 8 (one per phase, often more).

- [ ] **Step 3: Verify no forbidden characters**

Run: `grep -cE "—|→|…|'|'|"|"" /Users/szymonri/Documents/tatara/ARCHITECTURE.md`
Expected: `0`

- [ ] **Step 4: Commit**

```bash
cd /Users/szymonri/Documents/tatara
git add ARCHITECTURE.md
git commit -m "docs: add ARCHITECTURE.md covering OIDC model, chart pattern, and phases"
```

---

### Task 6: Write MEMORY.md

**Files:**
- Create: `/Users/szymonri/Documents/tatara/MEMORY.md`

- [ ] **Step 1: Write MEMORY.md**

```markdown
# MEMORY.md

Append-only log of cross-repo decisions, non-obvious choices, and
dead-ends. Newest first. One line per entry; expand only when the
rationale is not obvious from the decision itself.

Per-component memory belongs in that component's repo. This file is for
decisions that affect more than one repo.

Format: `YYYY-MM-DD - decision/finding`

---

## Decisions

- 2026-05-24 - **Platform split into eight independent GitHub repos.**
  The previous monolithic `tatara` (now at `~/Documents/tatara-old`)
  bundled CRDs, controllers, MCP servers, bridge, and helm charts. Blast
  radius of any change was the whole platform. New approach: one repo
  per component, each independently shipped.
- 2026-05-24 - **Docs repo holds child repos as gitignored subdirs.**
  `/Users/szymonri/Documents/tatara/tatara-memory/` etc. are each their
  own git repo. The parent `.gitignore` keeps them out. Avoids
  monorepo-by-accident while keeping `grep -R` across siblings easy.
- 2026-05-24 - **No umbrella helmfile in v0.** Each component deploys
  itself from its own `helmfile.yaml.gotmpl`. Revisit only if composing
  the whole platform with one `helmfile apply` becomes a real ask.
- 2026-05-24 - **OIDC realm: master at auth.szymonrichert.pl.**
  Two clients defined in `infra/terraform/keycloak/tatara_clients.tf`:
  confidential `tatara-memory` (audience), public `tatara-cli`
  (device-flow). Scope `tatara` carries the audience claim.
- 2026-05-24 - **tatara-memory exposes domain-shaped CRUD, not a thin
  LightRAG proxy.** Consumers get a contract that survives LightRAG
  version bumps. Cost: a small `internal/memory` translation layer
  per request.
- 2026-05-24 - **Chart layout: one chart per component, subcharts for
  deps.** `tatara-memory` chart has `cnpg-cluster`, `neo4j/neo4j`, and
  a local `lightrag` subchart as deps. Lightrag has no upstream chart;
  port and clean up the one from tatara-old.
- 2026-05-24 - **Sonnet for implementation, opus for merges.**
  Implementation subagents run sonnet; the merge subagent that
  integrates parallel work runs opus. Planning and review run opus.
- 2026-05-24 - **Build/deploy only from `main` on the source repo,
  never from a worktree.** Worktrees develop; main ships.

## Dead-ends / things tried that did not work

*(nothing yet)*

## Open questions

- 2026-05-24 - **lightrag image pin.** Spec uses `:latest`; pin the
  digest during phase-1 implementation.
- 2026-05-24 - **cnpg and neo4j chart version pins.** Decide during
  phase-1 chart work; pin in `Chart.yaml`.
- 2026-05-24 - **tatara-old archival.** Keep as a separate repo
  (`tatara-old` on GitHub) and link, or fold into a `_old/`
  subdirectory of this repo. Default: keep separate.
```

- [ ] **Step 2: Verify no forbidden characters**

Run: `grep -cE "—|→|…|'|'|"|"" /Users/szymonri/Documents/tatara/MEMORY.md`
Expected: `0`

- [ ] **Step 3: Commit**

```bash
cd /Users/szymonri/Documents/tatara
git add MEMORY.md
git commit -m "docs: seed MEMORY.md with cross-repo decisions from the spec"
```

---

### Task 7: Write ROADMAP.md

**Files:**
- Create: `/Users/szymonri/Documents/tatara/ROADMAP.md`

- [ ] **Step 1: Write ROADMAP.md**

```markdown
# ROADMAP.md

Phase-level platform roadmap. Per-component roadmaps live in each
component repo's own `ROADMAP.md`.

Statuses: `planned`, `in progress`, `shipped`.

---

## Phase 0 - `tatara` (this repo)

**Status:** in progress

**Goal:** Initialise the docs/architecture index, publish to GitHub.

- [x] Brainstorming spec written and committed
       (`docs/superpowers/specs/2026-05-24-tatara-phases-0-1-design.md`)
- [ ] Baseline files: LICENSE, CLAUDE.md, .gitignore, README,
       ARCHITECTURE, MEMORY, ROADMAP
- [ ] Repo published to `github.com/szymonrychu/tatara`

## Phase 1 - `tatara-memory`

**Status:** planned

**Goal:** REST memory service over LightRAG with CloudNativePG + Neo4j
storage, OIDC-gated via Keycloak master realm. Helm-deployed with
subcharts.

Detailed brainstorming and plan land in this repo's
`docs/superpowers/specs/` and `docs/superpowers/plans/` once phase 0
ships and `tatara-memory` is cloned into the workspace.

## Phase 2 - `tatara-cli`

**Status:** planned

Go CLI installable via Homebrew tap. OIDC device flow against the
public `tatara-cli` client. Subcommands: `login`, `logout`,
`raw <verb> <path>`, `mcp` (stdio MCP server), `mcp-config <dir>`.

## Phase 3 - `tatara-memory-repo-ingester`

**Status:** planned

Go batch tool. Walks a git repo, chunks files by a language-aware
splitter, POSTs to `/v1/memories:bulk` on tatara-memory, polls until
terminal.

## Phase 4 - `tatara-claude-code-wrapper`

**Status:** planned

Container image bundling Claude Code + tatara-cli pre-configured.

## Phase 5 - `tatara-argo-workflows`

**Status:** planned

WorkflowTemplates and a small controller, lifted from tatara-old and
cleaned up. Depends on tatara-memory being live.

## Phase 6 - `tatara-tasks`

**Status:** planned

REST task tracking service plus MCP wrapper served by tatara-cli.
Replaces the TaskBoard CRD path from tatara-old.

## Phase 7 - `tatara-gitlab-bridge`

**Status:** planned

GitLab webhook -> Argo workflow bridge. GitHub support deferred.

## Deferred / out of scope

*(populate as decisions are made to defer)*
```

- [ ] **Step 2: Verify all eight phases listed**

Run: `grep -cE "^## Phase [0-7]" /Users/szymonri/Documents/tatara/ROADMAP.md`
Expected: `8`

- [ ] **Step 3: Verify no forbidden characters**

Run: `grep -cE "—|→|…|'|'|"|"" /Users/szymonri/Documents/tatara/ROADMAP.md`
Expected: `0`

- [ ] **Step 4: Commit**

```bash
cd /Users/szymonri/Documents/tatara
git add ROADMAP.md
git commit -m "docs: add ROADMAP.md with phase 0-7 status"
```

---

### Task 8: Verify repo state and history

**Files:** none

- [ ] **Step 1: Verify all expected files present at repo root**

Run:
```bash
cd /Users/szymonri/Documents/tatara
ls -1 README.md ARCHITECTURE.md LICENSE MEMORY.md ROADMAP.md CLAUDE.md .gitignore
```
Expected: all seven listed, none missing.

- [ ] **Step 2: Verify git history**

Run: `git log --oneline`
Expected: roughly 8 commits, oldest being `ad442d9 docs: phases 0+1 design spec`,
newest being the ROADMAP commit. Order may vary by which order tasks ran but
all commit subjects should be prefixed with `docs:` or `chore:`.

- [ ] **Step 3: Verify working tree is clean**

Run: `git status --short`
Expected: empty output.

- [ ] **Step 4: Verify no forbidden characters across all docs**

Run:
```bash
cd /Users/szymonri/Documents/tatara
grep -lE "—|→|…|'|'|"|"" README.md ARCHITECTURE.md MEMORY.md ROADMAP.md CLAUDE.md docs/superpowers/specs/*.md docs/superpowers/plans/*.md 2>&1 | grep -v "^$"
```
Expected: empty output (no files contain forbidden characters).

---

### Task 9: Publish to GitHub

**Files:** none

- [ ] **Step 1: Confirm `gh` is authenticated for `szymonrychu`**

Run: `gh auth status`
Expected: logged in to `github.com` as `szymonrychu` (or your linked
account). If not, stop and run `gh auth login` interactively; do not
proceed otherwise.

- [ ] **Step 2: Create the remote repo**

Run:
```bash
cd /Users/szymonri/Documents/tatara
gh repo create szymonrychu/tatara \
  --private \
  --source=. \
  --description="Tatara platform - docs and architecture index" \
  --remote=origin
```
Expected: repo created at `github.com/szymonrychu/tatara`, `origin`
remote added pointing at it. If the repo already exists, this errors;
in that case run `git remote add origin git@github.com:szymonrychu/tatara.git`
manually and continue.

Note: `--private` matches the user's other infrastructure repos. Flip
to `--public` later when the platform is ready to be shown off.

- [ ] **Step 3: Push `main`**

Run: `git push -u origin main`
Expected: all commits push successfully; `main` tracks `origin/main`.

- [ ] **Step 4: Verify remote**

Run: `gh repo view szymonrychu/tatara --json url,defaultBranchRef -q '.url + " (default: " + .defaultBranchRef.name + ")"'`
Expected: prints the repo URL with default branch `main`.

- [ ] **Step 5: Mark phase 0 shipped in ROADMAP.md**

Edit `/Users/szymonri/Documents/tatara/ROADMAP.md`. In the Phase 0
section:
- Change `**Status:** in progress` to `**Status:** shipped 2026-05-24`.
- Tick the two remaining boxes (`Baseline files` and `Repo published`).

- [ ] **Step 6: Commit and push the status update**

```bash
cd /Users/szymonri/Documents/tatara
git add ROADMAP.md
git commit -m "docs: mark phase 0 shipped"
git push
```

---

## Phase 1 handoff

After Task 9, the docs repo is live on GitHub. Phase 1 begins in a new
session:

1. Clone `tatara-memory` (once the repo is created) into
   `/Users/szymonri/Documents/tatara/tatara-memory/`.
2. Open a new Claude session with that as the working dir.
3. Run `superpowers:brainstorming` to flesh out the phase-1 detailed
   design beyond what the existing spec already covers (the spec has
   the contract; the brainstorm clarifies open implementation
   questions: lightrag image pin, chart version pins, ingest worker
   pool size defaults, etc.).
4. Run `superpowers:writing-plans` to produce the phase-1
   implementation plan.
5. Drive it with `superpowers:subagent-driven-development`.

Do not start phase 1 in this session; phase 0 is the deliverable.
