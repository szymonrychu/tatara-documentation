# Tatara Consolidation Phase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the 14-item v0.1.x tech-debt backlog across the tatara platform, pivot deploy substrate to a new private `tatara-helmfile` umbrella repo, and ship tatara-memory v0.2.0 + tatara-cli v0.2.0 carrying the Edge.ID + tombstone changes.

**Architecture:** Same as today (per-repo charts, argo CWTs for CI/CD), with one structural change: per-component `helmfile.yaml.gotmpl` files are removed and replaced by a single `tatara-helmfile` repo mirroring the layout of `~/Documents/infra/helmfile`. Each tag pipeline now ends with a `tatara-helmfile-bump` step instead of `helmfile-deploy`; tatara-helmfile's own push_main triggers a single cluster-wide `helmfile sync`.

**Tech Stack:** Go 1.25, helm 3/4, helmfile 0.169, argo-workflows 1.0.14, sops PGP, postgres (cnpg), lightrag v1.4.16.

**Spec:** `~/Documents/tatara/docs/superpowers/specs/2026-05-27-tatara-consolidation-design.md`

---

## File Structure Across Repos

**~/Documents/tatara (parent docs repo)**
- Modify: `CLAUDE.md` - add hard rule 14 (no kubectl set image on helm resources)
- Modify: `MEMORY.md`, `ROADMAP.md` - mark phase complete at end

**~/Documents/tatara/tatara-memory**
- Modify: `Makefile` - strip leading `v` from IMG_TAG
- Modify: `CLAUDE.md` - sync rule 14 from parent
- Modify: `charts/tatara-memory/Chart.yaml` - pin cnpg + neo4j subchart versions
- Modify: `charts/tatara-memory/tests/configmap_test.yaml` - UPPER_SNAKE assertions
- Modify: `charts/tatara-memory/tests/deployment_test.yaml` - UPPER_SNAKE assertions
- Modify: `charts/tatara-memory/tests/helpers_test.yaml` - UPPER_SNAKE assertions
- Create: `internal/ingest/migrations/0002_deleted_memories.sql` - tombstone table
- Create: `internal/memory/tombstone.go` - tombstone reader/writer + reaper
- Create: `internal/memory/tombstone_test.go`
- Modify: `internal/memory/service.go` - DELETE writes tombstone, GET checks
- Modify: `internal/memory/service_test.go` - cover tombstone path
- Modify: `internal/memory/types.go` - Edge.ID opaque encoding
- Create: `internal/memory/edgeid.go` - encode/decode helpers
- Create: `internal/memory/edgeid_test.go`
- Modify: `internal/memory/translate.go` - use new Edge.ID encoder
- Modify: `internal/memory/translate_test.go`
- Modify: `internal/httpapi/edges.go` - decode opaque ID on input
- Modify: `internal/httpapi/edges_test.go`
- Modify: `internal/memory/service.go` - QueryMatch.Score from `/query/data` if available
- Modify: `cmd/tatara-memory/main.go` - wire tombstone reaper goroutine
- Delete: `helmfile.yaml.gotmpl` and `values/` dir at repo root (P14 cutover)
- Modify: `charts/tatara-memory/README.md` (or top-level) - add port-forward block

**~/Documents/tatara/tatara-cli**
- Modify: `MEMORY.md` - prune obsolete Wave 6.1 / tap entries
- Modify: `CLAUDE.md` - sync rule 14
- Modify: `.golangci.yml` - clean v2.12 schema
- Modify: `internal/mcp/tools.go` - `edge_*` tools accept opaque IDs
- Modify: `internal/mcp/tools_test.go`
- Create: `internal/mcp/e2e_test.go` (build tag `e2e`)
- Modify: argo CWT (in tatara-argo-workflows) `tatara-go-ci` to call `make e2e`

**~/Documents/tatara/tatara-argo-workflows**
- Modify: `MEMORY.md` - correct buildkit/kaniko entry
- Modify: `CLAUDE.md` - sync rule 14
- Create: `charts/tatara-argo-workflows/templates/cwt-tatara-helmfile-bump.yaml`
- Create: `charts/tatara-argo-workflows/templates/cwt-tatara-helmfile-deploy.yaml`
- Modify: `charts/tatara-argo-workflows/templates/cwt-tatara-memory-tag.yaml` - replace helmfile-deploy task with tatara-helmfile-bump
- Modify: `charts/tatara-argo-workflows/values.yaml` - add new image refs if needed
- Modify: `charts/tatara-argo-workflows/templates/cwt-go-ci.yaml` - append `make e2e` step

**~/Documents/tatara/tatara-helmfile** (new repo)
- Create: full layout per spec section P14

**~/Documents/infra/helmfile** (already deployed substrate)
- Modify: `helmfiles/coding/values/argo-events/common.yaml` - register tatara-helmfile push_main -> tatara-helmfile-deploy

**~/Documents/infra/helmfile/helmfiles/coding/** (after tatara-helmfile takes over)
- Modify: drop the `tatara-argo-workflows` release here? **No** - keep as bootstrap. Verified in spec.

---

## Group A - Mechanical Fixes (P1-P6)

### Task 1: Prune obsolete tatara-cli MEMORY.md entries (P1)

**Files:**
- Modify: `~/Documents/tatara/tatara-cli/MEMORY.md`

- [ ] **Step 1: Identify obsolete entries**

Read `tatara-cli/MEMORY.md`. The three target entries are dated 2026-05-27 and contain:
- "GoReleaser uses `brews` key (deprecated warning but functional in v2.16)..."
- "Wave 6.1 preconditions: (1) create `szymonrychu/tap`..."
- "Release preconditions before pushing v0.1.0 tag: (1) gh repo create szymonrychu/tap..."

- [ ] **Step 2: Replace with single supersession note**

Delete the three obsolete entries. Add one line in their place:

```markdown
2026-05-27 - Pre-argo Wave 6.1 release preconditions (Homebrew tap repo, HOMEBREW_TAP_GITHUB_TOKEN, separate HARBOR creds) are obsolete. Superseded by argo migration entry below: tap dropped, tatara-cli release runs in cluster as `tatara-go-release` CWT with creds from tatara ns secrets.
```

- [ ] **Step 3: Verify file still parses + no dangling references**

Run: `grep -n "Wave 6.1\|brews\|HOMEBREW_TAP_GITHUB_TOKEN" tatara-cli/MEMORY.md`
Expected: only the supersession note remains.

- [ ] **Step 4: Commit**

```bash
cd ~/Documents/tatara/tatara-cli
git add MEMORY.md
git commit -m "docs: prune obsolete Wave 6.1 release preconditions (superseded by argo)"
```

---

### Task 2: Correct tatara-argo-workflows MEMORY.md buildkit entry (P2)

**Files:**
- Modify: `~/Documents/tatara/tatara-argo-workflows/MEMORY.md`

- [ ] **Step 1: Identify the wrong entry**

The line currently reads:
> 2026-05-27 - buildkit-daemonless (single container, no sidecar) over kaniko because kaniko needs separate invocations for multi-arch...

This contradicts what shipped (kaniko, not buildkit).

- [ ] **Step 2: Replace with the real decision**

Delete the buildkit-daemonless line. Add:

```markdown
2026-05-27 - kaniko (gcr.io/kaniko-project/executor:v1.23.2-debug) for in-cluster container builds. buildkit-rootless was the original choice but failed with "rootlesskit:child: failed to share mount point: /: permission denied" on the node kernel (mount-namespace sharing is restricted). kaniko has no such requirement; the only cost is amd64-only invocation (no multi-arch support without re-running per arch), acceptable because the platform is amd64-only.
```

- [ ] **Step 3: Add Dead-ends entry for the buildkit attempt**

Under the "Dead-ends / things tried that did not work" heading add:

```markdown
2026-05-27 - buildkit-rootless (moby/buildkit:v0.14.1-rootless) blocked by kernel restrictions on mount-namespace sharing for unprivileged users. Pod logs: "rootlesskit:child: failed to share mount point: /: permission denied". Switched to kaniko.
```

- [ ] **Step 4: Commit**

```bash
cd ~/Documents/tatara/tatara-argo-workflows
git add MEMORY.md
git commit -m "docs: correct kaniko-vs-buildkit memory entry to match ship state"
```

---

### Task 3: tatara-memory Makefile strip leading v (P3)

**Files:**
- Modify: `~/Documents/tatara/tatara-memory/Makefile`

- [ ] **Step 1: Locate the version line**

Current line in Makefile:
```makefile
VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
```

- [ ] **Step 2: Strip leading v**

Replace with:
```makefile
VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null | sed 's/^v//' || echo dev)
```

- [ ] **Step 3: Verify on the current tag**

Run: `cd ~/Documents/tatara/tatara-memory && make -n image | grep IMAGE_REF`
Expected: IMAGE_REF contains `:0.1.x` not `:v0.1.x` (no leading v).

- [ ] **Step 4: Add MEMORY entry**

Append to `tatara-memory/MEMORY.md` under Decisions:
```markdown
2026-05-27 - Makefile VERSION strips leading v from `git describe --tags`. Chart appVersion and image tag now match without manual re-tagging. Long-standing v0.1.x friction closed.
```

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-memory
git add Makefile MEMORY.md
git commit -m "fix(make): strip leading v from VERSION so image tag matches chart appVersion"
```

---

### Task 4: Pin cnpg + neo4j subchart versions in tatara-memory/Chart.yaml (P4)

**Files:**
- Modify: `~/Documents/tatara/tatara-memory/charts/tatara-memory/Chart.yaml`

- [ ] **Step 1: Find currently-installed versions**

Run:
```bash
helm list -n tatara -o json | jq '.[] | {name, chart}'
helm get all -n tatara tatara-memory | grep -E 'cnpg|neo4j' | head
```

Read what's actually deployed. Record the exact versions.

- [ ] **Step 2: Read current Chart.yaml dependency block**

Open `charts/tatara-memory/Chart.yaml`. The dependencies block lists cnpg, neo4j, lightrag.

- [ ] **Step 3: Pin to exact versions (no tilde, no caret)**

For each dep, set `version: "X.Y.Z"` (string, exact). Do not use `~X.Y` or `^X`. Example shape:

```yaml
dependencies:
  - name: cluster
    version: "0.0.10"            # cnpg cluster subchart, currently deployed
    repository: "https://cloudnative-pg.github.io/charts"
    alias: cnpg-cluster
  - name: neo4j
    version: "5.26.0"             # currently deployed neo4j chart
    repository: "https://helm.neo4j.com/neo4j"
  - name: lightrag
    version: "0.1.0"              # local file ref
    repository: "file://./charts/lightrag"
```

Replace the placeholder version numbers above with whatever Step 1 reports.

- [ ] **Step 4: Refresh dependencies + lint**

Run:
```bash
cd ~/Documents/tatara/tatara-memory/charts/tatara-memory
helm dependency update
make chart-lint
```
Expected: lint passes, `Chart.lock` updates.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-memory
git add charts/tatara-memory/Chart.yaml charts/tatara-memory/Chart.lock
git commit -m "chore(chart): pin cnpg + neo4j subchart versions to match deployed state"
```

---

### Task 5: CLAUDE.md hard rule 14 across parent + components (P5)

**Files:**
- Modify: `~/Documents/tatara/CLAUDE.md`
- Modify: `~/Documents/tatara/tatara-memory/CLAUDE.md`
- Modify: `~/Documents/tatara/tatara-cli/CLAUDE.md`
- Modify: `~/Documents/tatara/tatara-argo-workflows/CLAUDE.md`

- [ ] **Step 1: Locate the "Hard rules" numbered list in parent CLAUDE.md**

Currently ends at rule 13 ("Metrics for everything that counts...").

- [ ] **Step 2: Append rule 14**

Add:
```markdown
14. **Never `kubectl set image`, `kubectl edit`, or `kubectl patch`
    spec fields on a helm-managed resource.** Bump chart appVersion
    and `helm upgrade` instead. Direct kubectl mutations leave orphan
    field-managers (kubectl-edit, kubectl-set, before-first-apply)
    that block helm 4 server-side apply on the next sync. Reason:
    burned us in the v0.1.1 -> v0.1.2 tatara-memory upgrade.
```

- [ ] **Step 3: Replicate to each component CLAUDE.md**

Each component's CLAUDE.md is a copy of the parent. Copy the rule 14 block to each component file too.

- [ ] **Step 4: Verify all four files end at rule 14 + identical wording**

Run:
```bash
for f in ~/Documents/tatara/CLAUDE.md ~/Documents/tatara/tatara-memory/CLAUDE.md ~/Documents/tatara/tatara-cli/CLAUDE.md ~/Documents/tatara/tatara-argo-workflows/CLAUDE.md; do
  echo "=== $f ==="
  grep -A 6 '^14\.' "$f"
done
```
Expected: identical 6 lines under each.

- [ ] **Step 5: Commit each repo separately**

```bash
cd ~/Documents/tatara && git add CLAUDE.md && git commit -m "docs(CLAUDE): add rule 14 - no kubectl set image on helm-managed resources"
cd ~/Documents/tatara/tatara-memory && git add CLAUDE.md && git commit -m "docs(CLAUDE): sync rule 14 from parent"
cd ~/Documents/tatara/tatara-cli && git add CLAUDE.md && git commit -m "docs(CLAUDE): sync rule 14 from parent"
cd ~/Documents/tatara/tatara-argo-workflows && git add CLAUDE.md && git commit -m "docs(CLAUDE): sync rule 14 from parent"
```

---

### Task 6: Verify + document tatara-memory ServiceMonitor (P6)

ServiceMonitor template already exists at `charts/tatara-memory/templates/servicemonitor.yaml`. This task is verify + document only.

**Files:**
- Verify: `~/Documents/tatara/tatara-memory/charts/tatara-memory/templates/servicemonitor.yaml`
- Modify: `~/Documents/tatara/tatara-memory/README.md`

- [ ] **Step 1: Verify ServiceMonitor renders + targets correct port**

Run:
```bash
cd ~/Documents/tatara/tatara-memory
helm template tatara-memory ./charts/tatara-memory --set serviceMonitor.enabled=true 2>/dev/null | yq '. | select(.kind == "ServiceMonitor")'
```
Expected: ServiceMonitor with `port: http`, `path: /metrics`. If port name doesn't match what `service.yaml` exposes, fix it.

- [ ] **Step 2: Verify prometheus actually scrapes it in the cluster**

Run:
```bash
kubectl get servicemonitor -n tatara
kubectl -n monitoring port-forward svc/prometheus-operated 9090 &
sleep 2
curl -s 'http://localhost:9090/api/v1/targets' | jq '.data.activeTargets[] | select(.labels.job | contains("tatara-memory")) | .health'
```
Expected: at least one target with health=up. If empty, the ServiceMonitor namespace selector or labels need fixing - inspect prometheus operator's `serviceMonitorNamespaceSelector` to align.

- [ ] **Step 3: Add port-forward block to tatara-memory README**

Append to `tatara-memory/README.md`:

````markdown
## Operator endpoints

The service exposes `/healthz`, `/readyz`, and `/metrics` on the http
port. These are deliberately not routed through the public ingress
(`tatara.szymonrichert.pl/api/v1/memory`). Reach them locally:

```bash
kubectl -n tatara port-forward svc/tatara-memory 8080:http
curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
curl http://localhost:8080/metrics
```

Prometheus scrapes `/metrics` automatically via the ServiceMonitor.
````

- [ ] **Step 4: Commit**

```bash
cd ~/Documents/tatara/tatara-memory
git add README.md
git commit -m "docs: document operator-endpoint port-forward; ServiceMonitor already wired"
```

---

## Group B - Small Refactors (P7-P11)

### Task 7: tatara-cli .golangci.yml v2 schema cleanup (P7)

**Files:**
- Modify: `~/Documents/tatara/tatara-cli/.golangci.yml`
- Modify: argo CWT `tatara-go-ci` in tatara-argo-workflows (drop `verify: false`)

- [ ] **Step 1: Capture current schema failures**

Run: `cd ~/Documents/tatara/tatara-cli && golangci-lint config verify 2>&1 | head -30`
Expected: specific schema error messages. Save the list.

- [ ] **Step 2: Rewrite .golangci.yml to clean v2 schema**

Replace the file with:

```yaml
version: "2"

run:
  timeout: 5m

linters:
  default: none
  enable:
    - errcheck
    - govet
    - ineffassign
    - staticcheck
    - unused
    - gosec
    - gocritic
    - revive
  settings:
    revive:
      rules:
        - name: exported
          disabled: true
    gosec:
      excludes:
        - G304  # file path from variable, intentional
        - G115  # uintptr->int casts, audited
        - G117  # http.Server timeouts handled via context

issues:
  exclude-rules:
    - path: _test\.go
      linters:
        - gosec
        - gocritic

formatters:
  enable:
    - gci
    - gofmt
  settings:
    gci:
      sections:
        - standard
        - default
        - prefix(github.com/szymonrychu/tatara-cli)
```

- [ ] **Step 3: Verify the schema is clean**

Run: `golangci-lint config verify`
Expected: no errors.

- [ ] **Step 4: Run the linter against the codebase**

Run: `golangci-lint run ./...`
Expected: same set of issues as before the cleanup (or fewer). If new ones appear, they are real - either fix them or add targeted excludes.

- [ ] **Step 5: Update argo CWT to drop verify:false**

In `~/Documents/tatara/tatara-argo-workflows/charts/tatara-argo-workflows/templates/cwt-go-ci.yaml`, the golangci-lint invocation should no longer carry `--verify-config=false`. Read the file; the linter step uses `golangci-lint run ./...`. If a `verify: false` flag is set on a sub-action call, remove it. (For this codebase the CWT uses the linter directly so the flag may not be present; verify before assuming.)

- [ ] **Step 6: Commit each repo separately**

```bash
cd ~/Documents/tatara/tatara-cli
git add .golangci.yml
git commit -m "fix(lint): rewrite .golangci.yml to clean v2.12+ schema"

cd ~/Documents/tatara/tatara-argo-workflows
git add charts/tatara-argo-workflows/templates/cwt-go-ci.yaml
git diff --cached --quiet || git commit -m "chore(ci): drop golangci-lint verify-false workaround"
```

---

### Task 8: tatara-memory helm-unittest UPPER_SNAKE assertions (P8)

**Files:**
- Modify: `~/Documents/tatara/tatara-memory/charts/tatara-memory/tests/configmap_test.yaml`
- Modify: `~/Documents/tatara/tatara-memory/charts/tatara-memory/tests/deployment_test.yaml`
- Modify: `~/Documents/tatara/tatara-memory/charts/tatara-memory/tests/helpers_test.yaml`

- [ ] **Step 1: Read current rendered configmap**

Run:
```bash
cd ~/Documents/tatara/tatara-memory
helm template tatara-memory ./charts/tatara-memory 2>/dev/null | yq '. | select(.kind == "ConfigMap" and .metadata.name | test("tatara-memory")) | .data'
```
Record the exact key names (UPPER_SNAKE) that ship today.

- [ ] **Step 2: Read current configmap_test.yaml + fix assertions**

The existing tests assert kebab-case keys (e.g. `auth-issuer`, `lightrag-url`). Replace each kebab-case key with the UPPER_SNAKE equivalent (e.g. `AUTH_ISSUER`, `LIGHTRAG_URL`). Use the exact key names from Step 1.

- [ ] **Step 3: Run chart-test**

Run: `cd ~/Documents/tatara/tatara-memory && make chart-test 2>&1 | tail -30`
Expected: configmap suite passes.

- [ ] **Step 4: Repeat for deployment_test.yaml**

The deployment test asserts envFrom or env key names. Update to match what `deployment.yaml` template actually renders.

- [ ] **Step 5: Repeat for helpers_test.yaml**

Less likely to have kebab-case, but verify. Update if needed.

- [ ] **Step 6: Run full chart-test suite**

Run: `make chart-test`
Expected: all suites pass.

- [ ] **Step 7: Commit**

```bash
cd ~/Documents/tatara/tatara-memory
git add charts/tatara-memory/tests/
git commit -m "fix(chart-test): assertions match UPPER_SNAKE env keys"
```

---

### Task 9: Tombstone migration + table (P9.1)

**Files:**
- Create: `~/Documents/tatara/tatara-memory/internal/ingest/migrations/0002_deleted_memories.sql`

- [ ] **Step 1: Write the migration file**

Create `internal/ingest/migrations/0002_deleted_memories.sql`:

```sql
CREATE TABLE IF NOT EXISTS deleted_memories (
    track_id   TEXT PRIMARY KEY,
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS deleted_memories_deleted_at_idx
    ON deleted_memories (deleted_at);
```

- [ ] **Step 2: Verify migrate runner picks it up**

Read `internal/ingest/migrate.go`. The runner reads `migrations/*.sql` in lexical order. Confirm 0002 lands after 0001.

- [ ] **Step 3: Write a test for the migration runner against this file**

In `internal/ingest/migrate_test.go`, add:

```go
func TestMigrate_AppliesDeletedMemoriesTable(t *testing.T) {
    if testing.Short() {
        t.Skip("requires postgres")
    }
    db := openTestDB(t)
    defer db.Close()
    if err := Migrate(context.Background(), db); err != nil {
        t.Fatalf("migrate: %v", err)
    }
    var n int
    err := db.QueryRowContext(context.Background(),
        `SELECT count(*) FROM information_schema.tables WHERE table_name = 'deleted_memories'`).Scan(&n)
    if err != nil {
        t.Fatalf("query: %v", err)
    }
    if n != 1 {
        t.Fatalf("deleted_memories table not present: count=%d", n)
    }
}
```

- [ ] **Step 4: Run the migrate test**

Run: `cd ~/Documents/tatara/tatara-memory && go test ./internal/ingest -run TestMigrate_AppliesDeletedMemoriesTable -tags integration -v`
Expected: PASS. If `-tags integration` is not the convention here, drop the tag (read `migrate_test.go` header for existing pattern).

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-memory
git add internal/ingest/migrations/0002_deleted_memories.sql internal/ingest/migrate_test.go
git commit -m "feat(memory): add deleted_memories tombstone table migration"
```

---

### Task 10: Tombstone reader/writer (P9.2)

**Files:**
- Create: `~/Documents/tatara/tatara-memory/internal/memory/tombstone.go`
- Create: `~/Documents/tatara/tatara-memory/internal/memory/tombstone_test.go`

- [ ] **Step 1: Write failing test**

Create `internal/memory/tombstone_test.go`:

```go
package memory

import (
    "context"
    "testing"
    "time"
)

func TestTombstoneStore_MarkAndCheck(t *testing.T) {
    if testing.Short() {
        t.Skip("requires postgres")
    }
    db := openTestDB(t)
    defer db.Close()
    s := NewTombstoneStore(db)
    ctx := context.Background()

    deleted, err := s.IsDeleted(ctx, "abc")
    if err != nil {
        t.Fatalf("IsDeleted before mark: %v", err)
    }
    if deleted {
        t.Fatalf("expected not deleted before mark")
    }

    if err := s.Mark(ctx, "abc"); err != nil {
        t.Fatalf("Mark: %v", err)
    }
    deleted, err = s.IsDeleted(ctx, "abc")
    if err != nil {
        t.Fatalf("IsDeleted after mark: %v", err)
    }
    if !deleted {
        t.Fatalf("expected deleted after mark")
    }
}

func TestTombstoneStore_Reap(t *testing.T) {
    if testing.Short() {
        t.Skip("requires postgres")
    }
    db := openTestDB(t)
    defer db.Close()
    s := NewTombstoneStore(db)
    ctx := context.Background()

    if err := s.Mark(ctx, "old"); err != nil {
        t.Fatalf("mark: %v", err)
    }
    // backdate
    _, err := db.ExecContext(ctx, `UPDATE deleted_memories SET deleted_at = now() - interval '25 hours' WHERE track_id = 'old'`)
    if err != nil {
        t.Fatalf("backdate: %v", err)
    }
    n, err := s.ReapOlderThan(ctx, 24*time.Hour)
    if err != nil {
        t.Fatalf("Reap: %v", err)
    }
    if n != 1 {
        t.Fatalf("expected to reap 1, got %d", n)
    }
}
```

Note: `openTestDB` is a test helper. Find the existing one in `internal/ingest/pgstore_test.go` or similar; reuse the same setup pattern.

- [ ] **Step 2: Run test to confirm it fails**

Run: `cd ~/Documents/tatara/tatara-memory && go test ./internal/memory -run TestTombstoneStore -v`
Expected: FAIL with "undefined: NewTombstoneStore".

- [ ] **Step 3: Implement tombstone.go**

Create `internal/memory/tombstone.go`:

```go
package memory

import (
    "context"
    "database/sql"
    "time"
)

type TombstoneStore struct {
    db *sql.DB
}

func NewTombstoneStore(db *sql.DB) *TombstoneStore {
    return &TombstoneStore{db: db}
}

func (s *TombstoneStore) Mark(ctx context.Context, trackID string) error {
    _, err := s.db.ExecContext(ctx,
        `INSERT INTO deleted_memories (track_id) VALUES ($1)
         ON CONFLICT (track_id) DO UPDATE SET deleted_at = EXCLUDED.deleted_at`,
        trackID)
    return err
}

func (s *TombstoneStore) IsDeleted(ctx context.Context, trackID string) (bool, error) {
    var exists bool
    err := s.db.QueryRowContext(ctx,
        `SELECT EXISTS (SELECT 1 FROM deleted_memories WHERE track_id = $1)`,
        trackID).Scan(&exists)
    return exists, err
}

func (s *TombstoneStore) Delete(ctx context.Context, trackID string) error {
    _, err := s.db.ExecContext(ctx, `DELETE FROM deleted_memories WHERE track_id = $1`, trackID)
    return err
}

func (s *TombstoneStore) ReapOlderThan(ctx context.Context, age time.Duration) (int64, error) {
    res, err := s.db.ExecContext(ctx,
        `DELETE FROM deleted_memories WHERE deleted_at < now() - $1::interval`,
        age.String())
    if err != nil {
        return 0, err
    }
    return res.RowsAffected()
}

func (s *TombstoneStore) List(ctx context.Context, limit int) ([]string, error) {
    rows, err := s.db.QueryContext(ctx,
        `SELECT track_id FROM deleted_memories ORDER BY deleted_at ASC LIMIT $1`, limit)
    if err != nil {
        return nil, err
    }
    defer func() { _ = rows.Close() }()
    var out []string
    for rows.Next() {
        var id string
        if err := rows.Scan(&id); err != nil {
            return nil, err
        }
        out = append(out, id)
    }
    return out, rows.Err()
}
```

- [ ] **Step 4: Run tests pass**

Run: `cd ~/Documents/tatara/tatara-memory && go test ./internal/memory -run TestTombstoneStore -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-memory
git add internal/memory/tombstone.go internal/memory/tombstone_test.go
git commit -m "feat(memory): tombstone store for deleted memory IDs"
```

---

### Task 11: Wire tombstone into DELETE + GET (P9.3)

**Files:**
- Modify: `~/Documents/tatara/tatara-memory/internal/memory/service.go`
- Modify: `~/Documents/tatara/tatara-memory/internal/memory/service_test.go`

- [ ] **Step 1: Write failing tests in service_test.go**

Append to `service_test.go`:

```go
func TestService_DeleteThenGet_ReturnsNotFound(t *testing.T) {
    s, fake, tomb := newTestService(t)
    ctx := context.Background()

    // Seed a doc in the fake lightrag
    fake.SeedDoc("track-123", "hello")

    // Confirm GET works
    m, err := s.GetMemory(ctx, "track-123")
    if err != nil || m.ID != "track-123" {
        t.Fatalf("GetMemory before delete: %v, %v", m, err)
    }

    // DELETE
    if err := s.DeleteMemory(ctx, "track-123"); err != nil {
        t.Fatalf("DeleteMemory: %v", err)
    }

    // Tombstone is set
    set, _ := tomb.IsDeleted(ctx, "track-123")
    if !set {
        t.Fatalf("tombstone not set")
    }

    // GET now returns ErrNotFound regardless of lightrag state
    _, err = s.GetMemory(ctx, "track-123")
    if !errors.Is(err, ErrNotFound) {
        t.Fatalf("GetMemory after delete: expected ErrNotFound, got %v", err)
    }
}
```

`newTestService` is a helper that wires the fake lightrag + an in-memory or test-db-backed `TombstoneStore`. Pattern match against existing service_test.go helpers.

- [ ] **Step 2: Run to confirm failure**

Run: `go test ./internal/memory -run TestService_DeleteThenGet_ReturnsNotFound -v`
Expected: FAIL.

- [ ] **Step 3: Modify Service struct to carry TombstoneStore**

In `service.go`, add `tomb *TombstoneStore` field to `Service`. Update `NewService` constructor signature to take it. Update all call sites (cmd/tatara-memory/main.go, tests).

- [ ] **Step 4: Modify DeleteMemory to write tombstone**

```go
func (s *Service) DeleteMemory(ctx context.Context, id string) error {
    if err := s.lightrag.DeleteDocument(ctx, id); err != nil {
        // Even if the upstream call fails, do not tombstone - the doc still exists.
        return s.wrapUpstream(err)
    }
    if err := s.tomb.Mark(ctx, id); err != nil {
        return fmt.Errorf("tombstone: %w", err)
    }
    return nil
}
```

- [ ] **Step 5: Modify GetMemory to check tombstone first**

```go
func (s *Service) GetMemory(ctx context.Context, id string) (Memory, error) {
    deleted, err := s.tomb.IsDeleted(ctx, id)
    if err != nil {
        return Memory{}, fmt.Errorf("tombstone check: %w", err)
    }
    if deleted {
        return Memory{}, ErrNotFound
    }
    doc, err := s.lightrag.GetDocument(ctx, id)
    if err != nil {
        return Memory{}, s.wrapUpstream(err)
    }
    return s.docToMemory(doc), nil
}
```

- [ ] **Step 6: Run tests pass**

Run: `go test ./internal/memory -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
cd ~/Documents/tatara/tatara-memory
git add internal/memory/service.go internal/memory/service_test.go cmd/tatara-memory/main.go
git commit -m "feat(memory): DELETE writes tombstone; GET returns 404 if tombstoned"
```

---

### Task 12: Tombstone reaper goroutine + metrics (P9.4 + P9.5)

**Files:**
- Modify: `~/Documents/tatara/tatara-memory/internal/memory/tombstone.go`
- Modify: `~/Documents/tatara/tatara-memory/internal/memory/tombstone_test.go`
- Modify: `~/Documents/tatara/tatara-memory/cmd/tatara-memory/main.go`

- [ ] **Step 1: Add reaper method + metric to tombstone.go**

Append to `tombstone.go`:

```go
import (
    "log/slog"

    "github.com/prometheus/client_golang/prometheus"
)

type Reaper struct {
    store    *TombstoneStore
    lightrag interface {
        GetDocument(ctx context.Context, id string) (lightrag.Document, error)
    }
    logger   *slog.Logger
    interval time.Duration
    maxAge   time.Duration
    metric   *prometheus.CounterVec
}

func NewReaper(store *TombstoneStore, lr lightragGetter, logger *slog.Logger, reg prometheus.Registerer) *Reaper {
    m := prometheus.NewCounterVec(
        prometheus.CounterOpts{
            Name: "tatara_memory_tombstone_total",
            Help: "Tombstone operations by type",
        },
        []string{"op"},
    )
    reg.MustRegister(m)
    return &Reaper{
        store:    store,
        lightrag: lr,
        logger:   logger,
        interval: 5 * time.Minute,
        maxAge:   24 * time.Hour,
        metric:   m,
    }
}

func (r *Reaper) Run(ctx context.Context) {
    t := time.NewTicker(r.interval)
    defer t.Stop()
    for {
        select {
        case <-ctx.Done():
            return
        case <-t.C:
            r.tick(ctx)
        }
    }
}

func (r *Reaper) tick(ctx context.Context) {
    ids, err := r.store.List(ctx, 1000)
    if err != nil {
        r.logger.Error("tombstone list", "err", err)
        return
    }
    for _, id := range ids {
        _, err := r.lightrag.GetDocument(ctx, id)
        if isNotFound(err) {
            if derr := r.store.Delete(ctx, id); derr == nil {
                r.metric.WithLabelValues("reaped").Inc()
            }
        }
    }
    forced, err := r.store.ReapOlderThan(ctx, r.maxAge)
    if err != nil {
        r.logger.Error("tombstone reap forced", "err", err)
        return
    }
    if forced > 0 {
        r.metric.WithLabelValues("forced").Add(float64(forced))
    }
}

// Mark / IsDeleted should emit the "created" / "checked" labels.
```

Add `Mark` to increment `created`:
```go
func (s *TombstoneStore) Mark(ctx context.Context, trackID string) error {
    // ... existing exec ...
    if err == nil && s.metric != nil {
        s.metric.WithLabelValues("created").Inc()
    }
    return err
}
```

Optional: thread the metric into TombstoneStore as well, or expose only the reaper metric. Pick simplest - thread it.

- [ ] **Step 2: Test reaper tick**

Append to `tombstone_test.go`:

```go
func TestReaper_Tick_ReapsConfirmedDeleted(t *testing.T) {
    if testing.Short() {
        t.Skip("requires postgres")
    }
    db := openTestDB(t)
    defer db.Close()
    store := NewTombstoneStore(db)
    fake := &fakeLightrag{}
    fake.notFound["gone"] = true
    reg := prometheus.NewRegistry()
    r := NewReaper(store, fake, slog.Default(), reg)
    ctx := context.Background()

    if err := store.Mark(ctx, "gone"); err != nil {
        t.Fatalf("mark: %v", err)
    }
    r.tick(ctx)
    set, _ := store.IsDeleted(ctx, "gone")
    if set {
        t.Fatalf("expected tombstone reaped")
    }
}
```

- [ ] **Step 3: Run tests pass**

Run: `go test ./internal/memory -run TestReaper -v`
Expected: PASS.

- [ ] **Step 4: Wire reaper into main.go**

In `cmd/tatara-memory/main.go`, after building the service:
```go
reaper := memory.NewReaper(tombStore, lightragClient, logger, promReg)
go reaper.Run(ctx)
```
The `ctx` here is the same root context that cancels on signal.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-memory
git add internal/memory/tombstone.go internal/memory/tombstone_test.go cmd/tatara-memory/main.go
git commit -m "feat(memory): tombstone reaper goroutine + prometheus metric"
```

---

### Task 13: QueryMatch.Score from /query/data (P11)

**Files:**
- Modify: `~/Documents/tatara/tatara-memory/internal/lightrag/types.go` (or where Query types live)
- Modify: `~/Documents/tatara/tatara-memory/internal/lightrag/http.go` (or whatever uses /query)
- Modify: `~/Documents/tatara/tatara-memory/internal/memory/translate.go`

- [ ] **Step 1: Inspect the OpenAPI for /query/data**

Run:
```bash
jq '.paths["/query/data"] // .paths["/query_data"] // empty' ~/Documents/tatara/tatara-memory/docs/lightrag-openapi-v1.4.16.json
```
Inspect the response schema. If a per-match `score` / `rank` / `similarity` numeric field exists, proceed. If not, skip to step 6 (accept 0 and write MEMORY entry).

- [ ] **Step 2: If rankable field exists, extend QueryMatch type**

In `internal/lightrag/types.go`, ensure `QueryDataMatch` (or whatever the response item is called) carries the numeric field. Add it to the Go struct.

- [ ] **Step 3: Switch the query handler to /query/data instead of /query**

In `internal/lightrag/http.go`, the existing call to `/query` returns references-only. Change the path + response decoding to `/query/data` (verify exact path from OpenAPI).

- [ ] **Step 4: Map the score in translate.go**

In `internal/memory/translate.go`, the `lightragMatchToDomain` (or equivalent) function: set `QueryMatch.Score = item.Score` (or whatever the field name is).

- [ ] **Step 5: Add test**

In `internal/memory/translate_test.go`:
```go
func TestQueryMatch_ScoreThreadedFromLightrag(t *testing.T) {
    upstream := lightrag.QueryDataMatch{ /* fill with score: 0.87 */ }
    m := lightragMatchToDomain(upstream)
    if m.Score != 0.87 {
        t.Fatalf("Score = %v, want 0.87", m.Score)
    }
}
```

Run: `go test ./internal/memory -run TestQueryMatch_Score -v`
Expected: PASS.

- [ ] **Step 6: Otherwise (no rankable field), accept 0 + lock in MEMORY**

If /query/data has no score, leave behavior unchanged. Append to `tatara-memory/MEMORY.md`:
```markdown
2026-05-27 - QueryMatch.Score remains 0. /query/data inspected in lightrag v1.4.16 OpenAPI; no per-match ranking field exists. Decision locked: callers cannot sort by relevance until lightrag exposes a score; reconsider if upstream adds one.
```

- [ ] **Step 7: Commit**

```bash
cd ~/Documents/tatara/tatara-memory
git add internal/lightrag/ internal/memory/translate.go internal/memory/translate_test.go MEMORY.md
git commit -m "feat(query): thread per-match score from lightrag /query/data (or lock 0)"
```

---

### Task 14: Edge.ID opaque encoding helpers (P10.1)

**Files:**
- Create: `~/Documents/tatara/tatara-memory/internal/memory/edgeid.go`
- Create: `~/Documents/tatara/tatara-memory/internal/memory/edgeid_test.go`

- [ ] **Step 1: Write failing test**

Create `edgeid_test.go`:

```go
package memory

import "testing"

func TestEdgeID_RoundTrip(t *testing.T) {
    cases := []struct {
        from, to string
    }{
        {"a", "b"},
        {"node-1", "node-2"},
        {"with space", "with/slash"},
        {"unicode-α", "β-unicode"},
        {"", "anything"},
    }
    for _, c := range cases {
        id := encodeEdgeID(c.from, c.to)
        from2, to2, err := decodeEdgeID(id)
        if err != nil {
            t.Fatalf("decode %q: %v", id, err)
        }
        if from2 != c.from || to2 != c.to {
            t.Fatalf("round-trip mismatch: encoded(%q,%q)=%q decoded=(%q,%q)", c.from, c.to, id, from2, to2)
        }
    }
}

func TestEdgeID_InvalidPayload(t *testing.T) {
    if _, _, err := decodeEdgeID("not-base64!!"); err == nil {
        t.Fatalf("expected decode error on invalid base64")
    }
    if _, _, err := decodeEdgeID("YWJjZA=="); err == nil { // valid base64 but no NUL separator
        t.Fatalf("expected decode error on missing separator")
    }
}
```

- [ ] **Step 2: Run to confirm failure**

Run: `go test ./internal/memory -run TestEdgeID -v`
Expected: FAIL with undefined: encodeEdgeID.

- [ ] **Step 3: Implement edgeid.go**

```go
package memory

import (
    "encoding/base64"
    "errors"
    "strings"
)

const edgeIDSep = "\x00"

func encodeEdgeID(from, to string) string {
    return base64.RawURLEncoding.EncodeToString([]byte(from + edgeIDSep + to))
}

func decodeEdgeID(id string) (from, to string, err error) {
    raw, err := base64.RawURLEncoding.DecodeString(id)
    if err != nil {
        return "", "", err
    }
    s := string(raw)
    i := strings.Index(s, edgeIDSep)
    if i < 0 {
        return "", "", errors.New("edge id: missing separator")
    }
    return s[:i], s[i+len(edgeIDSep):], nil
}
```

- [ ] **Step 4: Run tests pass**

Run: `go test ./internal/memory -run TestEdgeID -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/tatara/tatara-memory
git add internal/memory/edgeid.go internal/memory/edgeid_test.go
git commit -m "feat(memory): opaque Edge.ID encoding helpers"
```

---

### Task 15: Edge.ID server handlers + translate (P10.2)

**Files:**
- Modify: `~/Documents/tatara/tatara-memory/internal/memory/translate.go`
- Modify: `~/Documents/tatara/tatara-memory/internal/memory/translate_test.go`
- Modify: `~/Documents/tatara/tatara-memory/internal/httpapi/edges.go`
- Modify: `~/Documents/tatara/tatara-memory/internal/httpapi/edges_test.go`

- [ ] **Step 1: Find the current "from||to" composite usage**

Run:
```bash
cd ~/Documents/tatara/tatara-memory
grep -rn '||' internal/ | grep -v _test.go | grep -E 'edge|Edge'
```
Map every site that produces or parses the composite.

- [ ] **Step 2: Replace producer sites with encodeEdgeID**

In `internal/memory/translate.go`, the function that builds an Edge from lightrag's relation: replace any `from + "||" + to` with `encodeEdgeID(from, to)`.

- [ ] **Step 3: Replace parser sites with decodeEdgeID**

In `internal/httpapi/edges.go`, the GET/DELETE handlers that take `:id` from the URL: replace any `strings.SplitN(id, "||", 2)` with `from, to, err := decodeEdgeID(id)`. On decode error, return 400.

- [ ] **Step 4: Update translate_test.go assertions**

Tests that hard-coded `"from||to"` expect the new opaque string. Update with:
```go
want := encodeEdgeID("from", "to")
```

- [ ] **Step 5: Update edges_test.go**

Same pattern - opaque IDs in fixtures.

- [ ] **Step 6: Run full test suite**

Run: `cd ~/Documents/tatara/tatara-memory && go test ./...`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
cd ~/Documents/tatara/tatara-memory
git add internal/memory/translate.go internal/memory/translate_test.go internal/httpapi/edges.go internal/httpapi/edges_test.go
git commit -m "feat(memory): opaque Edge.ID in server (BREAKING)"
```

---

### Task 16: Edge.ID in tatara-cli MCP tools (P10.3)

**Files:**
- Modify: `~/Documents/tatara/tatara-cli/internal/mcp/tools.go`
- Modify: `~/Documents/tatara/tatara-cli/internal/mcp/tools_test.go`

- [ ] **Step 1: Inspect existing edge_* MCP tools**

Run: `grep -n 'edge_' ~/Documents/tatara/tatara-cli/internal/mcp/tools.go`
Map every tool that takes an edge id parameter or returns one.

- [ ] **Step 2: Verify tools never parsed the composite**

If any tool was splitting on `||` before, that needs to change to "treat id as opaque string". If they're already opaque pass-throughs, the change is no-op behaviorally.

- [ ] **Step 3: Update tool descriptions**

For each `edge_*` tool, update the input schema's `id` description to:
> "Opaque edge identifier returned by edge_list or edge_search. Do not parse."

- [ ] **Step 4: Add a fixture test**

In `tools_test.go`:
```go
func TestEdgeGet_PassesOpaqueIDThrough(t *testing.T) {
    srv := newStubMemoryServer(t)
    defer srv.Close()
    client := newTestClient(t, srv.URL)
    opaque := base64.RawURLEncoding.EncodeToString([]byte("a\x00b"))
    res, err := callTool(client, "edge_get", map[string]any{"id": opaque})
    if err != nil {
        t.Fatalf("call: %v", err)
    }
    if !strings.Contains(srv.LastPath(), opaque) {
        t.Fatalf("server did not see opaque id; saw %q", srv.LastPath())
    }
    _ = res
}
```

- [ ] **Step 5: Run tests**

Run: `cd ~/Documents/tatara/tatara-cli && go test ./...`
Expected: green.

- [ ] **Step 6: Commit**

```bash
cd ~/Documents/tatara/tatara-cli
git add internal/mcp/tools.go internal/mcp/tools_test.go
git commit -m "feat(mcp): edge_* tools treat id as opaque (matches memory v0.2.0)"
```

---

## Group C - New Feature Work (P12-P14)

### Task 17: Scaffold tatara-helmfile repo (P14a)

**Files:**
- Create: `~/Documents/tatara/tatara-helmfile/` (entire new repo)

- [ ] **Step 1: Create the directory + git init**

```bash
mkdir -p ~/Documents/tatara/tatara-helmfile
cd ~/Documents/tatara/tatara-helmfile
git init -b main
```

- [ ] **Step 2: Copy core files from infra/helmfile**

```bash
cp ~/Documents/infra/helmfile/.hook.sh .hook.sh
cp ~/Documents/infra/helmfile/.sops.yaml .sops.yaml
cp ~/Documents/infra/helmfile/.gitleaks.toml .gitleaks.toml
cp ~/Documents/infra/helmfile/.envrc .envrc
cp ~/Documents/infra/helmfile/.pre-commit-config.yaml .pre-commit-config.yaml
cp ~/Documents/infra/helmfile/.mise.toml .mise.toml
cp ~/Documents/tatara/CLAUDE.md CLAUDE.md
cp ~/Documents/tatara/LICENSE LICENSE
chmod +x .hook.sh
```

- [ ] **Step 3: Write top-level helmfile.yaml.gotmpl**

```yaml
helmfiles:
  - path: helmfiles/tatara/helmfile.yaml.gotmpl

environments:
  default: {}

helmDefaults:
  wait: true
  syncArgs:
    - --rollback-on-failure
```

- [ ] **Step 4: Write helmfiles/tatara/helmfile.yaml.gotmpl**

```yaml
environments:
  default: {}

---

helmDefaults:
  wait: true
  syncArgs:
    - --rollback-on-failure

templates:
  default: &default
    missingFileHandler: Warn
    values:
      - values/{{`{{ .Release.Name }}`}}/common.yaml
      - values/{{`{{ .Release.Name }}`}}/{{`{{ .Environment.Name }}`}}.yaml
    secrets:
      - values/{{`{{ .Release.Name }}`}}/{{`{{ .Environment.Name }}`}}.secrets.yaml

releases:
  - name: tatara-memory
    chart: oci://harbor.szymonrichert.pl/charts/tatara-memory
    version: "0.1.4"      # CURRENT DEPLOYED VERSION - update from helm history
    namespace: tatara
    createNamespace: true
    <<: *default
```

The `version: "0.1.4"` placeholder MUST be replaced with the actual currently-deployed version before first sync. Read from `helm list -n tatara -o json | jq -r '.[] | select(.name == "tatara-memory") | .chart'`.

- [ ] **Step 5: Create values dir for tatara-memory**

```bash
mkdir -p helmfiles/tatara/values/tatara-memory
```

Create `helmfiles/tatara/values/tatara-memory/common.yaml`:
```yaml
# Copy non-secret values from the old tatara-memory/values/common.yaml here.
# Read source: ~/Documents/tatara/tatara-memory/values/common.yaml (will be removed)
```

Create `helmfiles/tatara/values/tatara-memory/default.yaml`:
```yaml
# Copy default-env values from ~/Documents/tatara/tatara-memory/values/default.yaml
```

Encrypt the secrets file with sops using the existing PGP key:
```bash
cp ~/Documents/tatara/tatara-memory/values/default.secrets.yaml helmfiles/tatara/values/tatara-memory/default.secrets.yaml
sops -e -i helmfiles/tatara/values/tatara-memory/default.secrets.yaml
# Verify decryption works:
sops -d helmfiles/tatara/values/tatara-memory/default.secrets.yaml | head -5
```

- [ ] **Step 6: Stub MEMORY.md and ROADMAP.md**

Create `MEMORY.md`:
```markdown
# MEMORY.md

Component-local memory for tatara-helmfile. Cross-repo decisions live in
`~/Documents/tatara/MEMORY.md`.

Format: `YYYY-MM-DD - decision/finding`

---

## Decisions

2026-05-27 - Repo created. Mirrors `~/Documents/infra/helmfile` structure. Owns helm releases for the tatara namespace except the bootstrap tatara-argo-workflows release, which stays in infra to avoid the chicken-and-egg (cluster needs CWTs to self-deploy).

## Dead-ends / things tried that did not work

*(nothing yet)*

## Open questions

*(nothing yet)*
```

Create `ROADMAP.md`:
```markdown
# ROADMAP.md

Component-local roadmap for tatara-helmfile.

Statuses: `planned`, `in progress`, `shipped`.

---

## v0.1.0 - first release

**Status:** in progress 2026-05-27

Mirror infra/helmfile structure. Owns tatara-memory release for now;
other releases added as components onboard.
```

- [ ] **Step 7: Write .gitignore**

```
.envrc
*.swp
.DS_Store
.worktrees/
```

- [ ] **Step 8: Diff-test (must be no-op against cluster)**

Run:
```bash
cd ~/Documents/tatara/tatara-helmfile
mise exec -- helmfile -e default diff
```
Expected: zero changes against the cluster (releases already exist at the pinned versions, values match). If there's a diff, the values/version pins are off; fix before proceeding.

- [ ] **Step 9: Commit initial scaffold**

```bash
cd ~/Documents/tatara/tatara-helmfile
git add -A
git commit -m "feat: scaffold tatara-helmfile umbrella substrate (tatara ns)"
```

- [ ] **Step 10: Push to new private remote**

```bash
gh repo create szymonrychu/tatara-helmfile --private --source=. --remote=origin --push
```

Expected: repo created, push succeeds.

- [ ] **Step 11: Add gitignore entry in parent tatara repo**

In `~/Documents/tatara/.gitignore`, add a line:
```
tatara-helmfile/
```
Commit:
```bash
cd ~/Documents/tatara
git add .gitignore
git commit -m "chore: gitignore tatara-helmfile subdir"
```

---

### Task 18: tatara-helmfile-deploy CWT (P14b.1)

**Files:**
- Create: `~/Documents/tatara/tatara-argo-workflows/charts/tatara-argo-workflows/templates/cwt-tatara-helmfile-deploy.yaml`

- [ ] **Step 1: Write the CWT**

Pattern after `cwt-helmfile-deploy.yaml`. Difference: no `repo` parameter, hard-codes `szymonrychu/tatara-helmfile`. helmfileDir is `.` (top-level).

```yaml
apiVersion: argoproj.io/v1alpha1
kind: ClusterWorkflowTemplate
metadata:
  name: tatara-helmfile-deploy
  labels:
    {{- include "tatara-argo-workflows.labels" . | nindent 4 }}
spec:
  entrypoint: main
  onExit: report-exit
  arguments:
    parameters:
      - { name: ref,      value: "" }
      - { name: sha,      value: "" }
      - { name: provider, value: "" }
      - { name: action,   value: "" }
  volumeClaimTemplates:
    - metadata:
        name: workspace
      spec:
        accessModes: [ReadWriteMany]
        storageClassName: rook-ceph-rwx
        resources:
          requests:
            storage: 2Gi
  templates:
    - name: main
      inputs:
        parameters:
          - name: ref
          - name: sha
      steps:
        - - name: report-pending
            templateRef:
              name: tatara-github-status
              template: report
              clusterScope: true
            arguments:
              parameters:
                - { name: repo,        value: "szymonrychu/tatara-helmfile" }
                - { name: sha,         value: "{{`{{inputs.parameters.sha}}`}}" }
                - { name: state,       value: "pending" }
                - { name: context,     value: "tatara/helmfile-sync" }
                - { name: description, value: "tatara-helmfile sync running" }
                - { name: targetUrl,   value: "https://workflows.szymonrichert.pl/workflows/{{`{{workflow.namespace}}`}}/{{`{{workflow.name}}`}}" }
        - - name: clone
            template: clone
            arguments:
              parameters:
                - { name: sha, value: "{{`{{inputs.parameters.sha}}`}}" }
        - - name: sync
            template: sync

    - name: clone
      inputs:
        parameters:
          - name: sha
      container:
        image: {{ .Values.images.git | quote }}
        workingDir: /workspace
        command: [sh, -c]
        env:
          - name: GITHUB_TOKEN
            valueFrom:
              secretKeyRef:
                name: github-status-token
                key: GITHUB_TOKEN
        args:
          - |
            set -eux
            git clone --depth=1 "https://x-access-token:${GITHUB_TOKEN}@github.com/szymonrychu/tatara-helmfile" src
            cd src
            git fetch --depth=1 origin "{{`{{inputs.parameters.sha}}`}}" || true
            git checkout "{{`{{inputs.parameters.sha}}`}}"
        volumeMounts:
          - { name: workspace, mountPath: /workspace }

    - name: sync
      container:
        image: {{ .Values.images.helmfile | quote }}
        workingDir: /workspace/src
        command: [sh, -c]
        env:
          - name: HARBOR_USERNAME
            valueFrom:
              secretKeyRef:
                name: harbor-robot
                key: username
          - name: HARBOR_PASSWORD
            valueFrom:
              secretKeyRef:
                name: harbor-robot
                key: password
          - name: GPG_PRIVATE_RSA_B64
            valueFrom:
              secretKeyRef:
                name: gpg-key
                key: GPG_PRIVATE_RSA_B64
        args:
          - |
            set -eu
            apk add --no-cache gnupg
            { set +x; echo "$GPG_PRIVATE_RSA_B64" | base64 -d | gpg --batch --import; } 2>/dev/null
            printf '%s' "${HARBOR_PASSWORD}" | helm registry login -u "${HARBOR_USERNAME}" --password-stdin {{ .Values.harbor.registry }} 2>&1 | grep -v -i 'password'
            helmfile -e default sync
        volumeMounts:
          - { name: workspace, mountPath: /workspace }

    - name: report-exit
      steps:
        - - name: post
            templateRef:
              name: tatara-github-status
              template: report
              clusterScope: true
            arguments:
              parameters:
                - { name: repo,        value: "szymonrychu/tatara-helmfile" }
                - { name: sha,         value: "{{`{{workflow.parameters.sha}}`}}" }
                - { name: state,       value: "{{`{{workflow.status}}`}}" }
                - { name: context,     value: "tatara/helmfile-sync" }
                - { name: description, value: "{{`{{workflow.status}}`}}" }
                - { name: targetUrl,   value: "https://workflows.szymonrichert.pl/workflows/{{`{{workflow.namespace}}`}}/{{`{{workflow.name}}`}}" }
```

- [ ] **Step 2: Lint the chart**

Run: `cd ~/Documents/tatara/tatara-argo-workflows && helm lint charts/tatara-argo-workflows`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
cd ~/Documents/tatara/tatara-argo-workflows
git add charts/tatara-argo-workflows/templates/cwt-tatara-helmfile-deploy.yaml
git commit -m "feat(cwt): tatara-helmfile-deploy CWT (triggered by tatara-helmfile push_main)"
```

---

### Task 19: tatara-helmfile-bump CWT (P14b.2)

**Files:**
- Create: `~/Documents/tatara/tatara-argo-workflows/charts/tatara-argo-workflows/templates/cwt-tatara-helmfile-bump.yaml`

- [ ] **Step 1: Write the CWT**

Inputs: `component` (e.g. "tatara-memory"), `chartVersion` (e.g. "0.2.0"), `triggeringRepo`, `triggeringSha`.

Steps:
1. clone tatara-helmfile
2. sed the version in `helmfiles/tatara/values/<component>/default.yaml`
3. git config + commit + push to main
4. report github status on the triggering commit

```yaml
apiVersion: argoproj.io/v1alpha1
kind: ClusterWorkflowTemplate
metadata:
  name: tatara-helmfile-bump
  labels:
    {{- include "tatara-argo-workflows.labels" . | nindent 4 }}
spec:
  entrypoint: main
  arguments:
    parameters:
      - { name: component,     value: "" }
      - { name: chartVersion,  value: "" }
      - { name: triggeringRepo, value: "" }
      - { name: triggeringSha, value: "" }
  volumeClaimTemplates:
    - metadata:
        name: workspace
      spec:
        accessModes: [ReadWriteMany]
        storageClassName: rook-ceph-rwx
        resources:
          requests:
            storage: 1Gi
  templates:
    - name: main
      inputs:
        parameters:
          - name: component
          - name: chartVersion
          - name: triggeringRepo
          - name: triggeringSha
      steps:
        - - name: clone
            template: clone
        - - name: bump
            template: bump
            arguments:
              parameters:
                - { name: component,    value: "{{`{{inputs.parameters.component}}`}}" }
                - { name: chartVersion, value: "{{`{{inputs.parameters.chartVersion}}`}}" }
        - - name: report
            templateRef:
              name: tatara-github-status
              template: report
              clusterScope: true
            arguments:
              parameters:
                - { name: repo,        value: "{{`{{inputs.parameters.triggeringRepo}}`}}" }
                - { name: sha,         value: "{{`{{inputs.parameters.triggeringSha}}`}}" }
                - { name: state,       value: "success" }
                - { name: context,     value: "tatara/helmfile-bump" }
                - { name: description, value: "bumped {{`{{inputs.parameters.component}}`}} to {{`{{inputs.parameters.chartVersion}}`}}" }
                - { name: targetUrl,   value: "https://github.com/szymonrychu/tatara-helmfile/commits/main" }

    - name: clone
      container:
        image: {{ .Values.images.git | quote }}
        workingDir: /workspace
        command: [sh, -c]
        env:
          - name: GITHUB_TOKEN
            valueFrom:
              secretKeyRef:
                name: github-status-token
                key: GITHUB_TOKEN
        args:
          - |
            set -eux
            git clone "https://x-access-token:${GITHUB_TOKEN}@github.com/szymonrychu/tatara-helmfile" src
        volumeMounts:
          - { name: workspace, mountPath: /workspace }

    - name: bump
      inputs:
        parameters:
          - name: component
          - name: chartVersion
      container:
        image: {{ .Values.images.git | quote }}
        workingDir: /workspace/src
        command: [sh, -c]
        env:
          - name: GITHUB_TOKEN
            valueFrom:
              secretKeyRef:
                name: github-status-token
                key: GITHUB_TOKEN
        args:
          - |
            set -eux
            apk add --no-cache yq sed
            COMP="{{`{{inputs.parameters.component}}`}}"
            VER="{{`{{inputs.parameters.chartVersion}}`}}"
            FILE="helmfiles/tatara/values/${COMP}/default.yaml"
            # Bump the release's chart version. The release definition in
            # helmfiles/tatara/helmfile.yaml.gotmpl has version: "X.Y.Z" per release.
            sed -i "s|^    version: .*  # ${COMP}$|    version: \"${VER}\"  # ${COMP}|" helmfiles/tatara/helmfile.yaml.gotmpl
            # If the chart version is also referenced in values, update that too:
            if [ -f "$FILE" ]; then
              yq eval ".chartVersion = \"$VER\"" -i "$FILE" || true
            fi
            git config user.email "argo@tatara.local"
            git config user.name "tatara-argo"
            git add -A
            if git diff --cached --quiet; then
              echo "no changes; component already at ${VER}"
              exit 0
            fi
            git commit -m "chore: bump ${COMP} to ${VER}"
            git push origin main
        volumeMounts:
          - { name: workspace, mountPath: /workspace }
```

Note on the sed pattern: requires that each release in helmfiles/tatara/helmfile.yaml.gotmpl has a `# <component>` comment marker on the `version:` line. Add this convention in Task 17 step 4. If the marker is absent, the sed is a no-op.

Better approach (safer): use `yq` directly on the helmfile.yaml.gotmpl. But helmfile templates contain `{{ ... }}` which trip yq. The marker-based sed is the pragmatic path.

- [ ] **Step 2: Update Task 17's helmfile.yaml.gotmpl with the marker**

Edit `helmfiles/tatara/helmfile.yaml.gotmpl` so each release's version line carries a trailing comment:
```yaml
  - name: tatara-memory
    chart: oci://harbor.szymonrichert.pl/charts/tatara-memory
    version: "0.1.4"  # tatara-memory
    namespace: tatara
```

Commit the marker addition to tatara-helmfile.

- [ ] **Step 3: Lint chart**

Run: `cd ~/Documents/tatara/tatara-argo-workflows && helm lint charts/tatara-argo-workflows`

- [ ] **Step 4: Commit CWT**

```bash
cd ~/Documents/tatara/tatara-argo-workflows
git add charts/tatara-argo-workflows/templates/cwt-tatara-helmfile-bump.yaml
git commit -m "feat(cwt): tatara-helmfile-bump (called from component tag pipelines)"
```

---

### Task 20: Register tatara-helmfile in infra argo-events registry (P14b.3)

**Files:**
- Modify: `~/Documents/infra/helmfile/helmfiles/coding/values/argo-events/common.yaml`

- [ ] **Step 1: Read current registry shape**

```bash
yq '.events.github.repos // .repos // .' ~/Documents/infra/helmfile/helmfiles/coding/values/argo-events/common.yaml | head -40
```

- [ ] **Step 2: Add tatara-helmfile entry**

Append under the matching key (same shape as tatara-memory's entry):
```yaml
events:
  github:
    repos:
      tatara-helmfile:
        namespace: tatara
        workflows:
          push_main: tatara-helmfile-deploy
```

Verify shape exactly matches the other entries (e.g. tatara-memory).

- [ ] **Step 3: helmfile diff to inspect**

```bash
cd ~/Documents/infra/helmfile/helmfiles/coding
helmfile -e default diff --selector name=argo-events
```
Expected: only the new EventSource entry changes.

- [ ] **Step 4: Apply**

```bash
helmfile -e default sync --selector name=argo-events
```

- [ ] **Step 5: Manually register the github webhook**

```bash
HMAC=$(kubectl -n argo-events get secret git-access -o jsonpath='{.data.github-token}' | base64 -d)
gh api -X POST repos/szymonrychu/tatara-helmfile/hooks \
  -f name=web \
  -F active=true \
  -F 'events[]=push' \
  -f 'config[url]=https://events.szymonrichert.pl/github/push/tatara-helmfile' \
  -f 'config[content_type]=json' \
  -f "config[secret]=${HMAC}" \
  -f 'config[insecure_ssl]=0'
```

- [ ] **Step 6: Commit infra change**

```bash
cd ~/Documents/infra/helmfile
git add helmfiles/coding/values/argo-events/common.yaml
git commit -m "feat(events): register tatara-helmfile push_main -> tatara-helmfile-deploy"
```

---

### Task 21: Swap tatara-memory composite CWT helmfile-deploy for tatara-helmfile-bump (P14c.1)

**Files:**
- Modify: `~/Documents/tatara/tatara-argo-workflows/charts/tatara-argo-workflows/templates/cwt-tatara-memory-tag.yaml`

- [ ] **Step 1: Replace helmfile-deploy task with bump**

In the composite CWT's DAG, the current task `helmfile-deploy` becomes `helmfile-bump`. Replace the task definition:

```yaml
  - name: helmfile-bump
    depends: container-build && helm-publish
    templateRef:
      name: tatara-helmfile-bump
      template: main
      clusterScope: true
    arguments:
      parameters:
        - { name: component,      value: "tatara-memory" }
        - { name: chartVersion,   value: "{{`{{tasks.helm-publish.outputs.parameters.version}}`}}" }
        - { name: triggeringRepo, value: "{{`{{workflow.parameters.repo}}`}}" }
        - { name: triggeringSha,  value: "{{`{{workflow.parameters.sha}}`}}" }
```

The `version` output from helm-publish: the existing `helm-publish` task must emit the published version as an output parameter. Read its current template; if it doesn't, add an `outputs.parameters.version` block.

- [ ] **Step 2: Add version output to helm-publish step**

In the same file, the `helm-publish` template: append the output declaration. The script already computes `VER`; write it to a file:

```yaml
    - name: helm-publish
      outputs:
        parameters:
          - name: version
            valueFrom:
              path: /workspace/published-version.txt
      container:
        # ... existing ...
        args:
          - |
            set -eu
            # ... existing logic ...
            printf '%s' "${VER}" > /workspace/published-version.txt
```

- [ ] **Step 3: Remove the old helmfile-deploy task definition + its container template**

Delete both the DAG entry and the `- name: helmfile-deploy` template body (the large container that did `helmfile -e default sync ...`). It's superseded by the bump call.

- [ ] **Step 4: Lint chart**

Run: `cd ~/Documents/tatara/tatara-argo-workflows && helm lint charts/tatara-argo-workflows`

- [ ] **Step 5: helmfile diff against infra (which manages this release)**

The chart is owned by infra. Read its release values:
```bash
cd ~/Documents/infra/helmfile/helmfiles/coding
helmfile -e default diff --selector name=tatara-argo-workflows
```
Expected: new CWTs land, composite changes apply.

- [ ] **Step 6: Apply**

```bash
helmfile -e default sync --selector name=tatara-argo-workflows
```

- [ ] **Step 7: Commit tatara-argo-workflows**

```bash
cd ~/Documents/tatara/tatara-argo-workflows
git add charts/tatara-argo-workflows/templates/cwt-tatara-memory-tag.yaml
git commit -m "feat(cwt): tatara-memory-tag composite uses tatara-helmfile-bump (not helmfile-deploy)"
```

---

### Task 22: Remove per-component helmfile.yaml.gotmpl from tatara-memory (P14c.2)

**Files:**
- Delete: `~/Documents/tatara/tatara-memory/helmfile.yaml.gotmpl`
- Delete: `~/Documents/tatara/tatara-memory/values/` (entire dir)

- [ ] **Step 1: Sanity-check that values were ported into tatara-helmfile**

```bash
diff -r ~/Documents/tatara/tatara-memory/values ~/Documents/tatara/tatara-helmfile/helmfiles/tatara/values/tatara-memory | head
```
Decrypted contents should match (decrypt both sides for comparison). Resolve any difference by copying the missing bits to tatara-helmfile.

- [ ] **Step 2: Move files into a feature branch as fallback**

Create a backup branch on tatara-memory so the helmfile is recoverable:
```bash
cd ~/Documents/tatara/tatara-memory
git checkout -b archive/per-component-helmfile main
git checkout main
```

- [ ] **Step 3: Delete from main**

```bash
cd ~/Documents/tatara/tatara-memory
git rm helmfile.yaml.gotmpl
git rm -r values/
git commit -m "refactor: drop per-repo helmfile (substrate moved to tatara-helmfile)"
```

- [ ] **Step 4: Verify CI on tatara-memory still passes**

Run: `make ci`
Expected: lint + test still pass. (CI does not depend on helmfile.yaml.gotmpl.)

---

### Task 23: End-to-end cutover smoke - tag tatara-memory v0.2.0 (P14c.3)

**Files:** none (operational task)

- [ ] **Step 1: Verify all prior tasks landed on main**

For tatara-memory: tombstone + edge.id + v0.2.0 changes committed. Image+chart Makefile correct. helmfile.yaml.gotmpl removed.

For tatara-argo-workflows: new CWTs deployed (verified by helmfile sync from infra). Composite uses bump.

For tatara-helmfile: scaffolded, deployed, EventSource registered.

- [ ] **Step 2: Tag**

```bash
cd ~/Documents/tatara/tatara-memory
git tag v0.2.0
git push origin v0.2.0
```

- [ ] **Step 3: Watch the composite workflow**

```bash
argo list -n tatara --since 5m
argo logs -n tatara @latest --follow
```
Expected: clone -> container-build + helm-publish parallel -> helmfile-bump -> success.

- [ ] **Step 4: Watch tatara-helmfile deploy fire**

After the bump pushes a commit to tatara-helmfile main, EventSource should trigger `tatara-helmfile-deploy`:
```bash
argo list -n tatara --since 5m | grep tatara-helmfile-deploy
argo logs -n tatara @latest --follow
```

- [ ] **Step 5: Verify cluster is on v0.2.0**

```bash
kubectl -n tatara get deployment tatara-memory -o jsonpath='{.spec.template.spec.containers[0].image}'
```
Expected: `harbor.szymonrichert.pl/containers/tatara-memory:0.2.0`.

- [ ] **Step 6: End-to-end API smoke against the external URL**

```bash
TOKEN=$(get-oidc-token)  # whatever the existing script is
curl -H "Authorization: Bearer $TOKEN" \
  -X POST https://tatara.szymonrichert.pl/api/v1/memory/memories \
  -d '{"text":"smoke test v0.2.0"}'
# Then GET, DELETE, GET-after-DELETE expecting 404
```

Expected: POST 200, GET 200, DELETE 200, GET-after-DELETE returns 404 (tombstone working).

- [ ] **Step 7: Smoke edge CRUD with opaque IDs**

```bash
# List edges, capture an opaque id
EDGE_ID=$(curl -s -H "Authorization: Bearer $TOKEN" https://tatara.szymonrichert.pl/api/v1/memory/edges | jq -r '.items[0].id')
# GET that edge by opaque id
curl -H "Authorization: Bearer $TOKEN" "https://tatara.szymonrichert.pl/api/v1/memory/edges/$EDGE_ID"
```
Expected: 200, edge returned. ID is base64 (no `||`).

- [ ] **Step 8: Update parent MEMORY + ROADMAP**

In `~/Documents/tatara/MEMORY.md`:
```markdown
2026-05-27 - Consolidation phase shipped. tatara-memory v0.2.0 deployed via tatara-helmfile (new umbrella substrate). Per-component helmfile.yaml.gotmpl removed from tatara-memory. End-to-end smoke green: POST/GET/DELETE memories with tombstone semantics, edge CRUD with opaque IDs, query (score from /query/data if available else 0). 14-item tech-debt backlog closed.
```

In `~/Documents/tatara/ROADMAP.md`, move the "Phase 5 v0.1.1 follow-ups" section items that were in scope here to a "Phase 5.5 - Consolidation - shipped 2026-05-27" section. Leave the items marked out-of-scope intact.

```bash
cd ~/Documents/tatara
git add MEMORY.md ROADMAP.md
git commit -m "docs: consolidation phase shipped 2026-05-27"
```

---

### Task 24: tatara-argo-workflows self-onboard (P12)

**Files:**
- Modify: `~/Documents/infra/helmfile/helmfiles/coding/values/argo-events/common.yaml`

- [ ] **Step 1: Add tatara-argo-workflows tag_pushed entry**

In the registry, add:
```yaml
tatara-argo-workflows:
  namespace: tatara
  workflows:
    tag_pushed: tatara-argo-workflows-tag
```

- [ ] **Step 2: Create the tag_pushed CWT in tatara-argo-workflows**

Pattern after tatara-memory-tag but without container-build (no container in this repo). Steps: clone -> helm-publish (existing) -> tatara-helmfile-bump.

```yaml
apiVersion: argoproj.io/v1alpha1
kind: ClusterWorkflowTemplate
metadata:
  name: tatara-argo-workflows-tag
spec:
  entrypoint: main
  # ... pattern from tatara-memory-tag, drop container-build task ...
```

- [ ] **Step 3: Add tatara-argo-workflows entry to tatara-helmfile**

```bash
cd ~/Documents/tatara/tatara-helmfile
mkdir -p helmfiles/tatara/values/tatara-argo-workflows
# Copy current values from infra (where the bootstrap release lives)
```

The infra release of tatara-argo-workflows stays as the bootstrap. The tatara-helmfile release is "second copy" - both target the same release name. **Decision point**: do not duplicate the release across substrates - skip adding tatara-argo-workflows as a tatara-helmfile release. The CWT bump can write a no-op (or skip the bump step for this component).

Simplest: tag on tatara-argo-workflows runs `helm-publish` only. No bump. Infra-driven helmfile sync of tatara-argo-workflows still owns its deploy. Drop step 2 and step 3 - the self-onboard becomes "publishing-only".

Document this choice in `tatara-argo-workflows/MEMORY.md`:
```markdown
2026-05-27 - Self-onboard scope: tag_pushed publishes chart to harbor OCI but does NOT bump tatara-helmfile (the infra release stays canonical to avoid two substrates owning the same release). Deploys of tatara-argo-workflows still run via `helmfile sync` from ~/Documents/infra/helmfile.
```

- [ ] **Step 4: Register webhook**

Same gh api command as Task 20 step 5 but for tatara-argo-workflows + `events[]=create` (tag_pushed) instead of push.

- [ ] **Step 5: Tag + verify**

```bash
cd ~/Documents/tatara/tatara-argo-workflows
git tag v0.2.0
git push origin v0.2.0
```
Watch the workflow run helm-publish, push chart to harbor OCI. Verify:
```bash
helm pull oci://harbor.szymonrichert.pl/charts/tatara-argo-workflows --version 0.2.0
```
Expected: download succeeds.

- [ ] **Step 6: Commit infra + argo-workflows**

```bash
cd ~/Documents/infra/helmfile
git add helmfiles/coding/values/argo-events/common.yaml
git commit -m "feat(events): tatara-argo-workflows tag_pushed -> own helm-publish CWT"

cd ~/Documents/tatara/tatara-argo-workflows
git add charts/tatara-argo-workflows/templates/cwt-tatara-argo-workflows-tag.yaml MEMORY.md
git commit -m "feat(cwt): self-onboard tag pipeline (helm-publish only, no bump)"
```

---

### Task 25: tatara-cli end-to-end MCP smoke (P13)

**Files:**
- Create: `~/Documents/tatara/tatara-cli/internal/mcp/e2e_test.go`
- Modify: `~/Documents/tatara/tatara-cli/Makefile`
- Modify: `~/Documents/tatara/tatara-argo-workflows/charts/tatara-argo-workflows/templates/cwt-go-ci.yaml`

- [ ] **Step 1: Add make e2e target**

In `tatara-cli/Makefile`, append:
```makefile
.PHONY: e2e
e2e:
	go build -o ./bin/tatara-cli ./cmd/tatara
	go test -tags=e2e -v ./internal/mcp -run TestE2E
```

- [ ] **Step 2: Write the e2e test file**

Create `internal/mcp/e2e_test.go`:

```go
//go:build e2e

package mcp

import (
    "bufio"
    "encoding/json"
    "fmt"
    "io"
    "net/http"
    "net/http/httptest"
    "os/exec"
    "strconv"
    "strings"
    "testing"
    "time"
)

// jsonRPCFrame implements LSP/MCP framing: Content-Length header + JSON body.
func writeJSONRPC(w io.Writer, payload any) error {
    b, err := json.Marshal(payload)
    if err != nil {
        return err
    }
    _, err = fmt.Fprintf(w, "Content-Length: %d\r\n\r\n%s", len(b), b)
    return err
}

func readJSONRPC(r *bufio.Reader) (map[string]any, error) {
    var contentLength int
    for {
        line, err := r.ReadString('\n')
        if err != nil {
            return nil, err
        }
        line = strings.TrimRight(line, "\r\n")
        if line == "" {
            break
        }
        if strings.HasPrefix(line, "Content-Length:") {
            n, err := strconv.Atoi(strings.TrimSpace(strings.TrimPrefix(line, "Content-Length:")))
            if err != nil {
                return nil, err
            }
            contentLength = n
        }
    }
    body := make([]byte, contentLength)
    if _, err := io.ReadFull(r, body); err != nil {
        return nil, err
    }
    var out map[string]any
    if err := json.Unmarshal(body, &out); err != nil {
        return nil, err
    }
    return out, nil
}

func TestE2E_MCP_HandshakeAndToolsList(t *testing.T) {
    // Stub tatara-memory server
    srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        w.Header().Set("Content-Type", "application/json")
        w.WriteHeader(200)
        _, _ = w.Write([]byte(`{"items":[]}`))
    }))
    defer srv.Close()

    cmd := exec.Command("./bin/tatara-cli", "mcp")
    cmd.Env = append(cmd.Env,
        "TATARA_MEMORY_BASE_URL="+srv.URL,
        "TATARA_AUTH_TOKEN=stub-token",
    )
    stdin, _ := cmd.StdinPipe()
    stdout, _ := cmd.StdoutPipe()
    if err := cmd.Start(); err != nil {
        t.Fatalf("start: %v", err)
    }
    defer func() { _ = cmd.Process.Kill() }()

    r := bufio.NewReader(stdout)

    // initialize
    if err := writeJSONRPC(stdin, map[string]any{
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": map[string]any{
            "protocolVersion": "2024-11-05",
            "capabilities": map[string]any{},
            "clientInfo": map[string]any{"name": "tatara-cli-e2e", "version": "0.0.1"},
        },
    }); err != nil {
        t.Fatalf("write init: %v", err)
    }
    resp, err := readJSONRPC(r)
    if err != nil {
        t.Fatalf("read init: %v", err)
    }
    if _, ok := resp["result"]; !ok {
        t.Fatalf("initialize response missing result: %v", resp)
    }

    // initialized notification
    _ = writeJSONRPC(stdin, map[string]any{"jsonrpc": "2.0", "method": "notifications/initialized"})

    // tools/list
    if err := writeJSONRPC(stdin, map[string]any{
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
    }); err != nil {
        t.Fatalf("write tools/list: %v", err)
    }
    resp, err = readJSONRPC(r)
    if err != nil {
        t.Fatalf("read tools/list: %v", err)
    }
    result, ok := resp["result"].(map[string]any)
    if !ok {
        t.Fatalf("tools/list result missing: %v", resp)
    }
    tools, ok := result["tools"].([]any)
    if !ok {
        t.Fatalf("tools/list tools missing: %v", result)
    }
    if len(tools) != 13 {
        t.Fatalf("expected 13 tools, got %d", len(tools))
    }

    // tools/call memory_get
    if err := writeJSONRPC(stdin, map[string]any{
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": map[string]any{
            "name": "memory_get",
            "arguments": map[string]any{"id": "abc"},
        },
    }); err != nil {
        t.Fatalf("write tools/call: %v", err)
    }
    resp, err = readJSONRPC(r)
    if err != nil {
        t.Fatalf("read tools/call: %v", err)
    }
    if _, ok := resp["result"]; !ok {
        t.Fatalf("tools/call missing result: %v", resp)
    }

    _ = time.Second // unused, kept for clarity
}
```

- [ ] **Step 3: Run the test locally**

Run: `cd ~/Documents/tatara/tatara-cli && make e2e`
Expected: builds, test passes.

- [ ] **Step 4: Append make e2e to tatara-go-ci CWT**

In `~/Documents/tatara/tatara-argo-workflows/charts/tatara-argo-workflows/templates/cwt-go-ci.yaml`, the go test step: add `make e2e` after `make test`.

- [ ] **Step 5: Lint chart + commit**

```bash
cd ~/Documents/tatara/tatara-argo-workflows
helm lint charts/tatara-argo-workflows
git add charts/tatara-argo-workflows/templates/cwt-go-ci.yaml
git commit -m "feat(cwt): tatara-go-ci runs make e2e"
```

- [ ] **Step 6: Commit tatara-cli**

```bash
cd ~/Documents/tatara/tatara-cli
git add internal/mcp/e2e_test.go Makefile
git commit -m "feat(mcp): e2e smoke test (spawns binary, drives stdio JSON-RPC)"
```

---

### Task 26: Phase close-out - parent MEMORY + ROADMAP final

**Files:**
- Modify: `~/Documents/tatara/MEMORY.md`
- Modify: `~/Documents/tatara/ROADMAP.md`

- [ ] **Step 1: Final MEMORY entry**

Append:
```markdown
2026-05-27 - Consolidation phase complete. 14 of 14 items shipped:
- Group A (P1-P6): MEMORY pruning, MEMORY corrections, version normalization, subchart pins, CLAUDE.md rule 14, ServiceMonitor verification
- Group B (P7-P11): golangci v2 schema, helm-unittest UPPER_SNAKE, tombstone (table + store + reaper + metrics), QueryMatch.Score, Edge.ID opaque encoding
- Group C (P12-P14): tatara-helmfile umbrella substrate (new private repo), self-onboard via tag_pushed (publish-only for tatara-argo-workflows), MCP e2e smoke

Versions tagged: tatara-memory v0.2.0, tatara-cli v0.2.0, tatara-argo-workflows v0.2.0, tatara-helmfile v0.1.0.
Substrate ownership: infra owns argo-workflows + argo-events + tatara-argo-workflows bootstrap; tatara-helmfile owns everything else in the tatara ns.
```

- [ ] **Step 2: ROADMAP update**

Add new section under Phase 5:
```markdown
## Phase 5.5 - Consolidation

**Status:** shipped 2026-05-27

14-item v0.1.x tech-debt closure. See spec/plan.

See spec: `docs/superpowers/specs/2026-05-27-tatara-consolidation-design.md`.
See plan: `docs/superpowers/plans/2026-05-27-tatara-consolidation.md`.
```

Update the "Phase 5 v0.1.1 follow-ups" section: strike out the items closed by this phase, leave the items marked out-of-scope intact.

- [ ] **Step 3: Commit**

```bash
cd ~/Documents/tatara
git add MEMORY.md ROADMAP.md
git commit -m "docs: phase 5.5 (consolidation) shipped"
```

---

## Execution Notes

**Order**: Tasks 1-6 in parallel (3 subagents, one per repo where they cluster). Tasks 7-13 serial in tatara-memory + tatara-cli (some can be parallel; trust subagent-driven-development's task-by-task review). Tasks 14-16 serial (Edge.ID code first, then cli MCP). Tasks 17-26 serial (tatara-helmfile scaffold strictly first, then CWTs, then cutover).

**Verification gates**:
- After Group A: each repo's `pre-commit run --all-files` clean.
- After Group B: tatara-memory `go test ./...` green; tatara-cli `golangci-lint config verify` clean.
- After Edge.ID (Task 16): smoke locally against an existing cluster tatara-memory v0.1.x is impossible (different ID shape); skip until cutover.
- After Task 23 cutover: full end-to-end smoke via external URL.

**Backout plan**: If Task 22 (delete per-component helmfile) breaks deploy, revert that commit and `helmfile sync` from the per-component file again. The archive branch (Task 22 step 2) preserves it.

**Worktrees**: one worktree per repo per group is impractical (too many). Recommendation: one worktree per repo for the whole phase, named `consolidation-2026-05-27`. Cleanup at end via finishing-a-development-branch.
