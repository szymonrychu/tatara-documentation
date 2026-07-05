# Tatara Consolidation Phase - Design

**Date:** 2026-05-27
**Scope:** 14 tech-debt items across 4 repos plus 1 new repo
**Successor specs:** none (this is the closing-out phase for v0.1.x debt)

## Goal

Close the post-Phase 5 tech-debt backlog as a single coordinated phase.
Pivot the deploy substrate from per-component `helmfile.yaml.gotmpl` to a
new private `tatara-helmfile` repo that mirrors `~/Documents/infra/helmfile`.
Land a tatara-memory v0.2.0 carrying API-shape changes that need the
substrate pivot to deploy cleanly.

## Repos Touched

- `~/Documents/tatara` (parent docs repo; CLAUDE.md hard-rule add)
- `~/Documents/tatara/tatara-memory`
- `~/Documents/tatara/tatara-cli`
- `~/Documents/tatara/tatara-argo-workflows`
- `~/Documents/tatara/tatara-helmfile` (new private repo)

The existing `~/Documents/infra/helmfile` repo gets one EventSource
registry entry added (P14 wiring). Not a destination for new code; just
config.

## Items In Scope

Numbered to match the triage. "Reason" lines record the prior decision
that made the item debt.

### Group A - mechanical fixes

**P1 - prune obsolete tatara-cli MEMORY.md entries**
The three Wave 6.1 / tap repo / Homebrew preconditions are explicitly
superseded by the argo migration entry. Drop them. Keep the
supersession note so the history is preserved.

**P2 - correct tatara-argo-workflows MEMORY.md**
Replace the "buildkit-daemonless over kaniko" decision with the real
one: kaniko, because buildkit-rootless needs mount-ns sharing that the
node kernel denies. Add a Dead-ends entry covering the buildkit
attempt.

**P3 - tatara-memory Makefile version normalization**
`IMG_TAG` is currently `git describe --tags --always` which yields
`vX.Y.Z`. Strip leading `v` so image tag matches chart `appVersion`.
One edit. MEMORY entry locking the convention.

**P4 - pin cnpg + neo4j subchart versions in tatara-memory/Chart.yaml**
Read currently-installed versions from helm history of the existing
tatara-memory release. Pin exact (not range). No tilde, no caret.

**P5 - CLAUDE.md hard rule on kubectl set image**
Add rule 14 to `~/Documents/tatara/CLAUDE.md` and (since each component
copies it) replicate to all four component CLAUDE.md files. Text:

> 14. **Never `kubectl set image`, `kubectl edit`, or `kubectl patch`
>     spec fields on a helm-managed resource.** Bump chart appVersion
>     and `helm upgrade`. Direct kubectl mutations leave orphan
>     field-managers that block helm 4 server-side apply on the next
>     sync (the v0.1.1 SSA fight).

**P6 - tatara-memory operator endpoints via ServiceMonitor**
Add a templated `ServiceMonitor` resource in
`charts/tatara-memory/templates/servicemonitor.yaml`, gated by the
existing `serviceMonitor.enabled` (already true). Points at the http
port, path `/metrics`, namespace selector for the prometheus operator
that lives in the monitoring namespace. README block documenting the
`kubectl port-forward` command for manual `/healthz`. No ingress
change.

### Group B - small refactors

**P7 - tatara-cli .golangci.yml v2.12+ schema**
Rewrite to clean v2 schema:
- Drop `disable-all` (replaced by `linters: default: none`)
- Port `exclude-rules` to v2 `issues.exclude-rules` form
- Change `local-prefixes` (string) to `formatters.settings.gci.local-prefixes` (list)
- Bump pin to latest stable v2 at impl time
- Drop `verify: false` from the workflow / argo CWT call

**P8 - tatara-memory helm-unittest assertions for UPPER_SNAKE keys**
Read current `configmap.yaml` template + `deployment.yaml` envFrom.
Rewrite the configmap/deployment/helpers test files to assert
UPPER_SNAKE keys. Add `make chart-test` target if absent. Tests must
go green; argo `tatara-go-ci` should pick the new step up automatically
via the existing `make test` invocation, or add it to the CWT
explicitly if it isn't.

**P9 - tatara-memory DeleteMemory tombstone**
Server-side tombstone table in the existing tatara-memory cnpg cluster:

```sql
CREATE TABLE deleted_memories (
  track_id   TEXT PRIMARY KEY,
  deleted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

New migration file in the existing migrations dir (numbered after the
current highest). DELETE handler writes the tombstone alongside the
existing lightrag delete call. GET handler checks the tombstone first
and returns 404 if set. Background reaper goroutine (`tombstone_reaper`)
runs every 5 minutes: for each tombstone, GET against lightrag; on
404, delete the tombstone; tombstones older than 24h are always
deleted (belt-and-suspenders). New prometheus counter
`tatara_memory_tombstone_total{op="created|reaped|forced"}`.

**P10 - Edge.ID v0.2.0 redesign**
Breaking change. New encoding:

```go
// Edge.ID = base64url(from + "\x00" + to)
func encodeEdgeID(from, to string) string {
    return base64.RawURLEncoding.EncodeToString([]byte(from + "\x00" + to))
}
func decodeEdgeID(id string) (from, to string, err error) { ... }
```

`encodeEdgeID` / `decodeEdgeID` package-private to the memory package.
All edge handlers (`GetEdge`, `DeleteEdge`, etc.) decode on input,
encode on output. `ListEdges` keeps the existing `Graph(label)` walk
(O(N) reads stay as a separate, larger fight; MEMORY entry notes that
this phase did not address it).

tatara-cli MCP `edge_*` tools updated in lockstep: callers see opaque
ID strings, never parse them. v0.2.0 git tag pushed on both
tatara-memory and tatara-cli within this phase. The `/v1` URL path
stays - this is a payload semantics change, not a routing change.

**P11 - QueryMatch.Score investigation**
First step: read `tatara-memory/docs/lightrag-openapi-v1.4.16.json` to
see what `/query/data` returns. If there is a `score`, `rank`, or
similar numeric field per match, plumb it through `QueryMatch.Score`.
If genuinely nothing rankable, accept 0 and write a MEMORY entry
locking the decision so it does not get re-raised next time the debt
gets surveyed.

### Group C - new feature work

**P14 - tatara-helmfile new repo**

Greenfield private repo `github.com/szymonrychu/tatara-helmfile`. Sits
as a sibling on disk; gitignored in the parent tatara repo same as the
other components. Layout mirrors `~/Documents/infra/helmfile`:

```
tatara-helmfile/
├── .hook.sh                    # copied from infra, unchanged
├── .sops.yaml                  # PGP fingerprint D39E... (same as infra)
├── .mise.toml                  # helm 4, helmfile, sops pinned
├── .pre-commit-config.yaml
├── .gitignore
├── CLAUDE.md                   # copy of parent
├── MEMORY.md
├── ROADMAP.md
├── helmfile.yaml.gotmpl        # top-level composer
└── helmfiles/
    └── tatara/
        ├── helmfile.yaml.gotmpl    # all releases for the tatara ns
        └── values/
            ├── tatara-memory/
            │   ├── common.yaml
            │   ├── default.yaml
            │   └── default.secrets.yaml
            └── tatara-argo-workflows/
                ├── common.yaml
                ├── default.yaml
                └── default.secrets.yaml
```

Top-level `helmfile.yaml.gotmpl` composes phase dirs (one for now:
`helmfiles/tatara/helmfile.yaml.gotmpl`).

Each release pulls its chart from harbor OCI
(`oci://harbor.szymonrichert.pl/charts/<name>`), version pinned exactly
in `default.yaml`. `helmDefaults`:
- `wait: true`
- `syncArgs: [--rollback-on-failure]` (helm 4)
- `missingFileHandler: Warn`
- Values cascade: `common.yaml` -> `default.yaml` -> `default.secrets.yaml`

Sops handled identically to infra; existing fingerprint applies. No
new key.

**Bump-and-deploy loop**

After a component's chart is pushed to harbor by `tatara-helm-publish`,
a new CWT step `tatara-helmfile-bump` runs:

1. Clones tatara-helmfile.
2. Edits `helmfiles/tatara/values/<component>/default.yaml` to set
   `chart.version: <new>`.
3. Commits with message `chore: bump <component> to <version>`.
4. Pushes to main.

A separate CWT, `tatara-helmfile-deploy`, is triggered by tatara-helmfile's
own `push_main` webhook (via the existing infra argo-events registry).
It clones, imports GPG, helm registry logins, then `helmfile -e default
sync`. Same workspace + image set as today's helmfile-deploy CWT in
tatara-argo-workflows.

**EventSource registry change**

`~/Documents/infra/helmfile/helmfiles/coding/values/argo-events/common.yaml`
gets a new registry entry:

```yaml
tatara-helmfile:
  namespace: tatara
  workflows:
    push_main: tatara-helmfile-deploy
```

Manual `gh api .../hooks` registration as with the others.

**Bootstrap order**

1. Scaffold tatara-helmfile with the two values dirs (tatara-memory
   and tatara-argo-workflows) populated at the chart versions
   currently deployed. First `helmfile sync` from a laptop must be a
   no-op.
2. Add `tatara-helmfile-deploy` + `tatara-helmfile-bump` CWTs to the
   tatara-argo-workflows chart. Helmfile sync them in (via the
   infra release of tatara-argo-workflows).
3. Modify the `tatara-memory-tag` composite CWT: replace its
   `helmfile-deploy` task with `tatara-helmfile-bump`. Same for
   `tatara-cli-tag` if it ever needs a server-side release.
4. Remove per-component `helmfile.yaml.gotmpl` + `values/` dirs from
   tatara-memory.
5. Tag tatara-memory v0.2.0 (carrying P9 + P10 + P11). End-to-end
   smoke: composite runs, container builds, chart publishes, helmfile
   bumps, tatara-helmfile main moves, tatara-helmfile-deploy fires,
   cluster ends up on v0.2.0.

**Substrate ownership**

- `~/Documents/infra` keeps: argo-workflows install, argo-events
  install (incl. EventSource + Sensor + EventBus + github HMAC
  secret), the bootstrap tatara-argo-workflows helm release.
- tatara-helmfile owns: every other release in the tatara namespace.

**P12 - tatara-argo-workflows self-onboard**

Collapses into P14. Tag push on tatara-argo-workflows triggers
helm-publish -> tatara-helmfile-bump. tatara-helmfile picks it up on
its own push_main. The infra-managed release stays as the bootstrap.

**P13 - tatara-cli e2e MCP smoke**

New file `tatara-cli/cmd/mcp/mcp_e2e_test.go` (build tag `e2e`). Builds
the binary with `go build`, launches it as `./tatara-cli mcp`, drives
stdio with hand-rolled JSON-RPC framing (Content-Length headers +
JSON body). Tests:

- Initialize handshake returns the expected protocol version.
- `tools/list` returns 13 tools with expected names.
- `tools/call` on `memory_get` against an httptest stub tatara-memory
  server round-trips correctly.

Not exercising the real cluster - this is a smoke, not a system test.
Appended to argo `tatara-go-ci` CWT as a `make e2e` step gated by the
e2e build tag.

## Out of Scope

- argo-workflows-artifacts cross-namespace replication (P-A, marked
  not-an-issue)
- git-access.github-token real PAT (P-A, marked not-an-issue)
- go mod / build cache PVC (P-A, marked not-an-issue)
- ListEdges O(N) walk (P10 only changes ID encoding, not iteration)
- docker-compose integration stack (let-it-be)
- tatara-old archival (let-it-be)
- Phases 3, 4, 6, 7 (roadmap, not debt)

## Versioning

- tatara-memory: v0.1.4 -> v0.2.0 (breaking Edge.ID + new tombstone
  semantics + ServiceMonitor)
- tatara-cli: v0.1.0 -> v0.2.0 (matching edge_* MCP tool change)
- tatara-argo-workflows: chart minor bump (new CWTs: bump + deploy)
- tatara-helmfile: starts at v0.1.0

## Risk and Mitigation

- **Cross-repo Edge.ID change**: tatara-memory v0.2.0 deployed before
  tatara-cli v0.2.0 would let a v0.1.x CLI talk to a v0.2.0 server and
  see opaque IDs it cannot parse. Mitigation: cli was never parsing
  the composite ID (verify in impl); if it was, tag cli first, then
  memory.
- **tatara-helmfile bootstrap collision**: first sync from tatara-helmfile
  against existing releases that infra installed could fail
  ownership-check. Mitigation: bootstrap with chart versions that
  exactly match what's deployed, and verify with `--diff` before the
  first real sync.
- **Reaper goroutine memory leak**: tombstones forever-grow under
  pathological lightrag failures. Mitigation: 24h hard TTL, prometheus
  gauge for tombstone count, alert if > 1000.
- **Removing per-component helmfile.yaml.gotmpl mid-flight**: if the
  tatara-helmfile loop breaks for any reason, there is no fallback.
  Mitigation: keep the per-component files in a feature branch (not
  on main) for one week post-cutover.

## Execution Order

1. Group A in parallel (3 subagents, one per repo).
2. Group B - P7 + P8 in parallel; then P9; then P11; then P10 alone.
3. Group C - P14a (scaffold), P14b (CWTs), P14c (cutover), then P12
   (self-onboard via P14), then P13.

Verification gates between groups documented in the brainstorming
transcript - reproduced verbatim in the implementation plan.

## Models

- Doc edits (P1, P2, P5): haiku
- Mechanical code/config (P3, P4, P6, P7): sonnet
- Test/server logic (P8, P9, P11, P13): sonnet
- Cross-repo breaking change (P10): sonnet impl, opus merge review
- New repo + substrate pivot (P14): sonnet per-file, opus final merge

## Success Criteria

- All four component repos have green pre-commit + CI on main.
- tatara-memory v0.2.0 deployed; smoke covers POST/GET/DELETE/Query
  memories and edge CRUD with opaque IDs.
- DELETE-then-GET on a memory returns 404 within one request
  round-trip (tombstone working).
- tatara-helmfile is the only place that runs `helmfile sync` for the
  tatara namespace; per-component helmfile.yaml.gotmpl files are gone
  from tatara-memory and any other component that had one.
- tatara-argo-workflows tag-pushed triggers its own loop end-to-end.
- tatara-cli `make e2e` runs and passes locally and in argo.
- MEMORY.md across all four repos reflects current reality with no
  contradictions; ROADMAP.md in parent marks the closed items and
  carries any new debt found during impl.
