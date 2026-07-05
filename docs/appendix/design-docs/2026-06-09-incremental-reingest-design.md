# Incremental Re-ingest: correct diffs, per-file reconcile, cron schedule

Date: 2026-06-09
Status: design, pending implementation
Repos touched: tatara-operator, tatara-memory-repo-ingester, tatara-memory

## Problem

Re-ingestion after the first ingest is broken and incomplete:

1. **Shallow-clone vs `--since` diff (hard failure).** The operator clones the
   repo `--depth 1 --branch <b>` (`internal/ingest/job.go`). Incremental ingest
   then runs `git diff --name-only <lastIngestedCommit>..HEAD`
   (`tatara-memory-repo-ingester/internal/walk/walk.go`). The depth-1 clone does
   not contain `<lastIngestedCommit>`, so git exits 128 and the whole ingest Job
   fails. Verbatim from a failing pod:
   `"ingest failed","error":"git -C /workspace/repo diff --name-only adb5f50..HEAD: exit status 128"`.
   Every incremental re-ingest fails; only the first ingest (full, `ls-files`,
   no diff) works. lightrag is frozen at first-ingest state for every repo.

2. **No periodic trigger.** Re-ingest fires only on a delivered push webhook. A
   missed webhook delivery, or a push that does not reach the operator, means the
   repo never refreshes.

3. **Stale residue (no GC).** Modified files produce new chunks (new idempotency
   hash) while the old chunks linger; deleted files keep all their code-graph
   entities and semantic chunks. The code-graph self-heals per file via
   `Reconcile` only for files present in a push; semantic memories have no
   delete-by-source path at all.

## Guiding invariant (the target)

Project memory must stay **1:1 with the default-branch code**: for every repo,
the code-graph and semantic memory reflect exactly the files at the current
default-branch HEAD -- nothing stale, nothing missing. tatara memory is the
server-side alternative to client-side graphify: agents traverse massive
codebases through the tatara MCP tools (`code_search`, `code_entity`,
`code_*`, semantic `query`) instead of graphify, grep, or ad-hoc scripts, and
they rely on the state being an accurate mirror of the branch. A future
`tatara-plugin` will steer agents to these tools. Every design choice here
serves that invariant: per-file reconcile keeps it exact; the cron catch-up
keeps it convergent even when a webhook is missed.

## Goal

Incremental re-ingest that is correct (changed/deleted files fully reconciled in
both code-graph and semantic memory), resilient (never hard-fails on history
rewrite), and scheduled (per-Repository cron in addition to push webhooks).

## Architecture: reconcile-per-file, symmetric across stores

The ingester diffs `since..HEAD` with status. For every touched file the system
**purges that file's prior state, then inserts its new state**. Deleted files
purge with nothing inserted. This is exactly today's code-graph `Reconcile`
semantics, extended to semantic memories so both stores stay consistent.

```
push webhook  OR  cron tick (per-Repository schedule)
      |
      v
operator stamps tatara.dev/reingest-requested  (existing path, unchanged)
      |
      v
operator launches ingest Job  -- FULL-HISTORY clone (no --depth)
      |
      v
ingester: git diff --name-status <since>..HEAD  ->  {added, modified, deleted, renamed}
      |                    (since unresolvable -> WARN, fall back to full ls-files)
      v
  code-graph:bulk { repo, files:<touched set>, entities:<for added/modified> }
  memories:bulk   { repo, reconcile_files:<touched set>, items:<chunks for added/modified> }
      |
      v
memory purges prior state for every file in <touched set>, then inserts new
      |
      v
lightrag reflects merged HEAD, no stale entities or chunks
```

## Phase 0: graphify-forward-compatible capture

This spec is no longer "keep AST 1:1"; it is "capture everything a server-side
graphify needs, reconcile it all per-file, defer only the global compute." See
`2026-06-09-server-graphify-roadmap.md`. The following forward-compatible
additions land in THIS work so the ingest path is never rewritten when the
semantic layer (Phase 2) arrives. They are cheap now (schema + contract, mostly
empty producers) and expensive to retrofit through reconcile paths we are
already editing.

1. **Widen the GraphPush contract.** Add `SemanticEdges`, `ConceptNodes`
   (doc/rationale/concept entities), and `Hyperedges` arrays. Producers may emit
   them empty until Phase 2. The per-file purge-then-insert reconcile loop covers
   them from day one, so the wire format is final.
2. **Confidence columns now.** In the same migration wave as
   `0002_memory_sources.sql`, add `confidence_score real NOT NULL DEFAULT 1.0`
   and `confidence_tier text NOT NULL DEFAULT 'EXTRACTED'` to `code_edges`, plus
   an index on `(repo, confidence_tier)`. Backfill from `properties.confidence`
   where present. Analyzers already compute call-resolution confidence
   (type_resolved 0.98 .. unresolved 0.0); promote the scalar into the typed
   column on reconcile.
3. **Reserve analytics + provenance columns now (compute later).** On
   `code_entities`: `community int`, `cohesion real`, `degree int`,
   `betweenness real`, `source_url text`, `author text`, `captured_at timestamptz`,
   and promote Go's already-computed `line_start int` / `line_end int` to typed
   columns. Create empty `code_hyperedges(repo, id, label, relation,
   confidence_score, src_file, properties)` and `code_hyperedge_members(repo,
   hyperedge_id, entity_id)` tables. Reserving is nearly free; retrofitting means
   a second migration plus re-touching every reconcile path.
4. **content_sha in walk.** `walk.Changes` carries the content sha (and blob) for
   added/modified files, so a Phase-2 semantic stage can cache by
   `(repo, file_path, content_sha)` without re-plumbing walk. Capturing the sha
   now is nearly free.
5. **Docs become graph-participating.** `DocsAnalyzer` starts emitting a doc
   entity per file (`type=doc_file`/`doc_section`, id `doc:section:path#heading`)
   into `GraphPush.Entities`, and doc files route into the code-graph `Files`
   set, not only memories. Doc files reconcile into BOTH stores (code-graph
   entities + semantic chunks). This is the single change that turns tatara from
   a code-graph into a code+docs knowledge graph; once present, `code_search`
   matches docs by type for free. YAML frontmatter (source_url/author/captured_at)
   is captured at analyze time into the reserved columns (only available during
   the walk).
6. **1:1 invariant extends to semantic edges, hyperedges, and doc nodes.** Each
   is owned by its `source_file` and purged when that file is in the touched set,
   exactly like AST entities and chunks. No separate GC. (Stops semantic residue
   reappearing -- the same class of bug as the stale-chunk problem in Problem #3.)
7. **Global analytics stay out of per-file reconcile.** community/cohesion/
   degree/betweenness are GLOBAL; a separate debounced post-reingest analytics
   job (Phase 2) populates the reserved columns after a successful incremental
   ingest. The re-ingest path stays cheap and file-granular.

Phase 0 is the schema/contract surface; the Go that fills SemanticEdges/
Hyperedges/concept nodes and the analytics job are Phase 2. The columns and
arrays exist now so that lands without touching ingest again.

## Component 1: tatara-operator

### 1a. Full-history clone (`internal/ingest/job.go`)

Drop `--depth 1`. Clone `--branch <defaultBranch> <url> <dir>` so `<since>` is
always present for the diff. Repos are small; a full clone is bulletproof and
removes the one code path that was failing. (If a repo ever grows large enough to
matter, switch to a treeless partial clone `--filter=blob:none` with persistent
credentials for on-demand blob fetch during analysis; noted in MEMORY, not built
now -- YAGNI.)

The clone command keeps the existing inline credential helper that injects
`SCM_TOKEN`. No other change to the Job spec.

### 1b. Per-Repository cron schedule

New **required** field `Repository.spec.reingestSchedule` (string, standard
5-field cron, e.g. `0 6 * * *`). New status field
`Repository.status.lastScheduledReingest (*metav1.Time)`.

`internal/controller/repository_controller.go`, after a repo is `Ingested`:

- Parse `spec.reingestSchedule` with `github.com/robfig/cron/v3`
  (`cron.ParseStandard`). On parse error: log ERROR with the bad expression,
  skip scheduling (do not requeue on schedule, do not crash). Validation on the
  CRD field catches most bad input at write time.
- `base := status.lastScheduledReingest` if set, else `status.lastIngestTime`,
  else `creationTimestamp`. `next := schedule.Next(base)`.
- If `now >= next`: stamp `tatara.dev/reingest-requested = now` (RFC3339, the
  existing annotation the webhook uses), set `status.lastScheduledReingest = now`,
  return (the annotation change re-triggers reconcile, which launches the Job via
  the existing path). Guard: only stamp when `now.After(status.lastIngestTime)`
  to avoid firing while an ingest from another trigger is still in flight.

The scheduled run is the **catch-up** that keeps memory convergent with the
branch: it diffs `lastIngestedCommit..HEAD`, which spans every commit since the
last successful ingest -- including any pushes whose webhooks were missed and the
deletions in them. It is therefore complete (restores 1:1) yet still cheap
(touches only changed files), so it scales to massive repos without re-analyzing
the whole tree. A full re-analyze is only needed to recover from genuine store
drift (DB corruption, partial-failure), which is an ops action, not this routine.
- Else: `RequeueAfter = next.Sub(now)` (clamped to a sane max, e.g. 6h, so clock
  skew or long sleeps still re-evaluate).

The schedule reuses the entire existing webhook re-ingest mechanism; it only adds
a time-based stamper. No CronJob resource, no new RBAC, no new image.

The cron hour/expression is per-Repository and operator-agnostic; no operator env
needed. (Charts stay cluster-agnostic; the schedule is repo data, not cluster
config.)

### 1c. CRD + samples + live CRs

- `api/v1alpha1/repository_types.go`: add `ReingestSchedule string` with
  `+kubebuilder:validation:Required` and a pattern/min-length guard; add
  `LastScheduledReingest *metav1.Time` to status. Regenerate CRD + deepcopy.
- `deploy-samples/tatara-project.yaml`: add `reingestSchedule: "0 6 * * *"` to
  every Repository.
- Deploy step patches the 6 live Repository CRs with `reingestSchedule`.

## Component 2: tatara-memory-repo-ingester

### 2a. Status-aware diff (`internal/walk/walk.go`)

Replace the `Changed` diff with `git diff --name-status <since>..HEAD`. Return a
classified result instead of a flat path list:

```go
type Change struct {
    Path       string // new path (for renames, the destination)
    OldPath    string // populated only for renames
    Status     rune   // 'A' added, 'M' modified, 'D' deleted, 'R' renamed
    ContentSHA string // sha256 of file content for A/M (Phase 0: semantic cache key); empty for D
}
type Changes struct {
    Files   []Change // every touched file
    FullSet bool     // true when produced by ls-files (first/full/fallback)
}
```

`ContentSHA` is captured here (Phase 0, item 4) so a future semantic stage caches
by `(repo, file_path, content_sha)` without re-plumbing walk. It is computed from
the working-tree file for A/M; deleted files have none.

- `full || since == ""`: `git ls-files` -> all files as `Status:'A'`,
  `FullSet:true`.
- else: `git diff --name-status <since>..HEAD`. Parse `R<score>` rename lines
  into an old+new pair (delete old, add new).
- **Fallback:** if the diff command fails (since not an ancestor: force-push,
  rebase, GC'd commit), log WARN with the error and the since SHA, then run the
  `ls-files` full path. A job must never hard-fail on history rewrite.

### 2b. Drive reconcile (`cmd/tatara-ingest/run.go` + `internal/push`)

- Added/Modified/Renamed-new: analyze + chunk as today; their paths go into the
  code-graph `Files` and the memories `reconcile_files`, plus their entities and
  chunk items.
- Deleted/Renamed-old: path goes into code-graph `Files` and memories
  `reconcile_files` with **no** entities/items -> server purges them.
- `internal/push`: `/memories:bulk` request gains `reconcile_files []string`
  (the full touched set). `/code-graph:bulk` already carries `Files`; ensure it
  includes deleted paths.

## Component 3: tatara-memory

### 3a. Source index

New migration `internal/memory/migrations/0002_memory_sources.sql`:

```sql
CREATE TABLE IF NOT EXISTS memory_sources (
    repo      text NOT NULL,
    file_path text NOT NULL,
    track_id  text NOT NULL,
    PRIMARY KEY (repo, file_path, track_id)
);
CREATE INDEX IF NOT EXISTS memory_sources_repo_file
    ON memory_sources (repo, file_path);
```

Same migration wave (Phase 0, items 2-3) -- a sibling code-graph migration adds
the confidence columns, reserved analytics/provenance columns, and the empty
hyperedge tables:

```sql
ALTER TABLE code_edges
    ADD COLUMN IF NOT EXISTS confidence_score real NOT NULL DEFAULT 1.0,
    ADD COLUMN IF NOT EXISTS confidence_tier  text NOT NULL DEFAULT 'EXTRACTED';
CREATE INDEX IF NOT EXISTS code_edges_repo_tier ON code_edges (repo, confidence_tier);

ALTER TABLE code_entities
    ADD COLUMN IF NOT EXISTS community    int,
    ADD COLUMN IF NOT EXISTS cohesion     real,
    ADD COLUMN IF NOT EXISTS degree       int,
    ADD COLUMN IF NOT EXISTS betweenness  real,
    ADD COLUMN IF NOT EXISTS source_url   text,
    ADD COLUMN IF NOT EXISTS author       text,
    ADD COLUMN IF NOT EXISTS captured_at  timestamptz,
    ADD COLUMN IF NOT EXISTS line_start   int,
    ADD COLUMN IF NOT EXISTS line_end     int;

CREATE TABLE IF NOT EXISTS code_hyperedges (
    repo             text NOT NULL,
    id               text NOT NULL,
    label            text NOT NULL,
    relation         text NOT NULL,
    confidence_score real NOT NULL DEFAULT 1.0,
    src_file         text NOT NULL,
    properties       jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (repo, id)
);
CREATE TABLE IF NOT EXISTS code_hyperedge_members (
    repo         text NOT NULL,
    hyperedge_id text NOT NULL,
    entity_id    text NOT NULL,
    PRIMARY KEY (repo, hyperedge_id, entity_id)
);
CREATE INDEX IF NOT EXISTS code_hyperedges_src ON code_hyperedges (repo, src_file);
```

Reconcile promotes call-resolution confidence into the typed columns and purges
hyperedges by `src_file` alongside entities/edges. Backfill `confidence_*` from
existing `properties.confidence`.

The async ingest worker, after `CreateMemory` returns a `track_id`, inserts
`(repo, file_path, track_id)` using `repo` and `file_path` from the item's
metadata (the ingester already stamps both, see `push/items.go`). Items without a
`file_path` (none today) are simply not indexed.

### 3b. Delete-by-source + bulk reconcile

- `memory.Service.DeleteMemoriesBySource(ctx, repo, filePath) (int, error)`:
  `SELECT track_id FROM memory_sources WHERE repo=$1 AND file_path=$2`, call the
  existing `DeleteMemory(track_id)` for each (lightrag `DeleteDocs` + tombstone),
  then `DELETE FROM memory_sources WHERE repo=$1 AND file_path=$2`. Idempotent;
  returns count purged.
- `/memories:bulk` handler: when the request carries `reconcile_files`, the
  worker first calls `DeleteMemoriesBySource(repo, f)` for each `f` in
  `reconcile_files`, then enqueues/inserts the items. Purge-then-insert ordering
  lives in job processing so it is atomic with respect to the job. When
  `reconcile_files` is absent (back-compat / first full ingest), behavior is
  unchanged (insert only, idempotent).

This makes add/modify/delete uniform: a modified file's old chunks are purged
before the new ones land; a deleted file's chunks are purged with nothing added.

### 3c. Chart

`charts/tatara-memory` appVersion + version -> 0.2.5; the new migration ships in
the image; no values change.

## Error handling

- Operator clone failure / Job failure: unchanged (status `Failed`, surfaced).
- Ingester missing-since: WARN + full fallback (2a). Never hard-fail.
- Memory purge: per-file delete failures wrap-and-return so the job item fails
  loudly rather than silently leaving residue.
- Bad cron: ERROR + skip scheduling for that repo; webhook path still works.

## Testing (TDD throughout)

- **walk**: table-driven over synthetic git repos created in the test:
  add/modify/delete/rename classification; rename old+new pairing; missing-since
  triggers `ls-files` fallback with `FullSet:true`.
- **memory**: `memory_sources` populate after `CreateMemory`;
  `DeleteMemoriesBySource` purges the right track_ids and index rows and is
  idempotent; `memories:bulk` with `reconcile_files` purges-then-inserts (old
  chunk gone, new chunk present); without it, insert-only unchanged.
- **operator**: clone command has no `--depth`; schedule stamps the annotation
  when `now >= Next(base)` and not before; `lastScheduledReingest` prevents
  double-fire within one cron interval; bad cron logs and skips; `RequeueAfter`
  is set to the next fire when not yet due.
- **end-to-end (live)**: merge a commit -> incremental Job Succeeds ->
  `lastIngestedCommit` advances -> code search reflects the change and a deleted
  symbol disappears from both code-graph and semantic search.

## Build / deploy (branch-flow, build from main only)

Worktree per repo -> TDD -> merge to each repo `main` -> build/push from main:

- tatara-memory image + chart 0.2.5 (migration).
- tatara-memory-repo-ingester image (new tag).
- tatara-operator image 0.2.13 (CRD regen, clone, scheduler).

Then one infra MR bumping the three image pins in `helmfiles/tatara/`
(`MEMORY_IMAGE`, `INGESTER_IMAGE`, operator image/appVersion), apply, patch live
Repository CRs with `reingestSchedule`, validate end-to-end.

## Out of scope

- Treeless/partial clone optimization for large repos (noted, YAGNI now).
- Per-Project schedule overrides (schedule is per-Repository by decision).
- Backfill GC of chunks already orphaned before this ships. A full pass is
  insert-only (no `reconcile_files`), so it cannot remove pre-existing orphans
  or files deleted before the pass. Clearing them means wiping the Project's
  memory and re-ingesting from scratch, or a future dedicated GC pass; optional
  follow-up, not built now.
