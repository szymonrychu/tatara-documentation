# Incremental Re-ingest + Phase 0 Capture (tatara-memory-repo-ingester) Implementation Plan

## Header

- **Repo:** `tatara-memory-repo-ingester` (`/Users/szymonri/Documents/tatara/tatara-memory-repo-ingester`)
- **Module:** `github.com/szymonrychu/tatara-memory-repo-ingester`
- **Go:** `1.25.0`
- **Spec sources (obey byte-for-byte):**
  - `docs/superpowers/specs/2026-06-09-incremental-reingest-design.md` (Component 2 + Phase 0)
  - `docs/superpowers/specs/2026-06-09-phase0-contract-lock.md` (LOCKED wire types)
  - `docs/superpowers/specs/2026-06-09-namespace-clone-layout-notes.md`
- **Test style:** stdlib `testing`, table-driven `t.Run`, `github.com/stretchr/testify/require`, `httptest` for HTTP, synthetic git repos built with `exec.Command("git", ...)` in `t.TempDir()`. Loggers use `log/slog`. YAML via `sigs.k8s.io/yaml` (direct dep, JSON tags).
- **Scope of this plan (this repo only):**
  1. `internal/contract/contract.go` — mirror the locked Phase 0 wire types (Edge confidence fields, Entity provenance fields, `Hyperedge` + `GraphPush.Hyperedges`, new entity-type + relation constants, confidence-tier helper). Update `contract_shape_test.go`.
  2. `internal/walk/walk.go` — replace `Changed` with a status-aware `Diff` returning `Changes{Files []Change, FullSet bool}`; ls-files for full/empty-since; `git diff --name-status` otherwise; WARN + ls-files fallback when diff errors; `ContentSHA` (sha256 of working-tree file) for A/M.
  3. `internal/analyze/docs.go` — `DocsAnalyzer` emits a doc entity per file (`Type=doc_file`, id `doc:file:<path>`), routes doc files into the code-graph, captures YAML frontmatter (`source_url`/`author`/`captured_at`) onto the entity.
  4. `internal/push` + `cmd/tatara-ingest/run.go` — drive reconcile-per-file: A/M/renamed-new analyze+chunk; D/renamed-old go into code-graph `Files` and memories `reconcile_files` with no entities/items; `/memories:bulk` carries `reconcile_files`; `/code-graph:bulk` `Files` includes deleted paths.
- **Out of scope (other repos / other plans):** tatara-operator clone + cron, tatara-memory migrations + `DeleteMemoriesBySource` + bulk handler, charts.
- **Commit discipline:** one commit per TDD task (test+impl together, conventional message). Branch off `main` in a worktree before starting (per CLAUDE.md rule 10).

> Every task is RED → GREEN → COMMIT. Run commands from the repo root `/Users/szymonri/Documents/tatara/tatara-memory-repo-ingester` unless stated. Code blocks are complete; no placeholders.

---

## Task 1: Contract — add Edge confidence fields + ConfidenceTier helper

**Files**
- Modify: `internal/contract/contract.go`
- Modify: `internal/contract/contract_shape_test.go`

**Steps**

1. **Write the failing test.** Append to `internal/contract/contract_shape_test.go`:

```go
func TestEdgeConfidenceFields(t *testing.T) {
	e := contract.Edge{
		From: "a", To: "b", Relation: contract.RelCalls, SrcFile: "x.go",
		ConfidenceScore: 0.98, ConfidenceTier: contract.TierInferred,
		Properties: map[string]string{"resolution": contract.ResTypeResolved},
	}
	b, err := json.Marshal(e)
	require.NoError(t, err)
	var got map[string]any
	require.NoError(t, json.Unmarshal(b, &got))
	require.ElementsMatch(t,
		[]string{"from", "to", "relation", "src_file", "confidence_score", "confidence_tier", "properties"},
		keys(got))
	require.InDelta(t, 0.98, got["confidence_score"], 1e-9)
	require.Equal(t, "INFERRED", got["confidence_tier"])
}

func TestEdgeConfidenceOmitEmpty(t *testing.T) {
	e := contract.Edge{From: "a", To: "b", Relation: contract.RelCalls, SrcFile: "x.go"}
	b, err := json.Marshal(e)
	require.NoError(t, err)
	var got map[string]any
	require.NoError(t, json.Unmarshal(b, &got))
	_, hasScore := got["confidence_score"]
	_, hasTier := got["confidence_tier"]
	require.False(t, hasScore, "confidence_score must be absent when zero")
	require.False(t, hasTier, "confidence_tier must be absent when empty")
}

func TestTierForScore(t *testing.T) {
	cases := []struct {
		name  string
		score float64
		tier  string
	}{
		{"extracted", 1.0, contract.TierExtracted},
		{"inferred_high", 0.98, contract.TierInferred},
		{"inferred_mid", 0.45, contract.TierInferred},
		{"ambiguous_boundary", 0.3, contract.TierAmbiguous},
		{"ambiguous_low", 0.0, contract.TierAmbiguous},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			require.Equal(t, tc.tier, contract.TierForScore(tc.score))
		})
	}
}
```

2. **Run it (expect FAIL).**
```
go test ./internal/contract/ -run 'TestEdgeConfidence|TestTierForScore' -count=1
```
Expected FAIL: `undefined: contract.TierInferred` (compile error in `internal/contract/contract_shape_test.go`).

3. **Minimal implementation.** In `internal/contract/contract.go`, replace the `Edge` struct (lines 16-23) with:

```go
// Edge is a directed, typed relationship between two entities.
type Edge struct {
	From            string            `json:"from"`
	To              string            `json:"to"`
	Relation        string            `json:"relation"`
	SrcFile         string            `json:"src_file"`
	ConfidenceScore float64           `json:"confidence_score,omitempty"` // 1.0 EXTRACTED, <1 INFERRED
	ConfidenceTier  string            `json:"confidence_tier,omitempty"`  // EXTRACTED|INFERRED|AMBIGUOUS
	Properties      map[string]string `json:"properties,omitempty"`
}
```

   Then add, immediately after the `ConfidenceFor` function (end of file):

```go
// Confidence tier values (typed column on code_edges; promoted from the scalar score).
const (
	TierExtracted = "EXTRACTED"
	TierInferred  = "INFERRED"
	TierAmbiguous = "AMBIGUOUS"
)

// TierForScore maps a confidence score to a tier:
// 1.0 -> EXTRACTED; (0.3,1.0) -> INFERRED; <=0.3 -> AMBIGUOUS.
func TierForScore(score float64) string {
	switch {
	case score >= 1.0:
		return TierExtracted
	case score > 0.3:
		return TierInferred
	default:
		return TierAmbiguous
	}
}
```

4. **Run to pass.**
```
go test ./internal/contract/ -run 'TestEdgeConfidence|TestTierForScore' -count=1
```
Expected PASS: `ok  	github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract`.

5. **Commit.**
```
git add internal/contract/contract.go internal/contract/contract_shape_test.go
git commit -m "feat(contract): add Edge confidence_score/confidence_tier fields and TierForScore"
```

---

## Task 2: Contract — add Entity provenance fields + doc entity-type constants

**Files**
- Modify: `internal/contract/contract.go`
- Modify: `internal/contract/contract_shape_test.go`

**Steps**

1. **Write the failing test.** Append to `internal/contract/contract_shape_test.go`:

```go
func TestEntityProvenanceFields(t *testing.T) {
	e := contract.Entity{
		ID: "doc:file:README.md", Name: "README.md", Type: contract.EntityDocFile,
		FilePath: "README.md", LineStart: 1, LineEnd: 42,
		SourceURL: "https://example.com/x", Author: "alice", CapturedAt: "2026-06-09T00:00:00Z",
	}
	b, err := json.Marshal(e)
	require.NoError(t, err)
	var got map[string]any
	require.NoError(t, json.Unmarshal(b, &got))
	require.ElementsMatch(t,
		[]string{"id", "name", "type", "file_path", "line_start", "line_end", "source_url", "author", "captured_at"},
		keys(got))
}

func TestEntityProvenanceOmitEmpty(t *testing.T) {
	e := contract.Entity{ID: "go:package:m", Name: "m", Type: contract.EntityGoPackage, FilePath: ""}
	b, err := json.Marshal(e)
	require.NoError(t, err)
	var got map[string]any
	require.NoError(t, json.Unmarshal(b, &got))
	for _, k := range []string{"line_start", "line_end", "source_url", "author", "captured_at"} {
		_, has := got[k]
		require.False(t, has, "%s must be absent when empty", k)
	}
}

func TestDocEntityTypeConstants(t *testing.T) {
	require.Equal(t, "doc_file", contract.EntityDocFile)
	require.Equal(t, "doc_section", contract.EntityDocSection)
	require.Equal(t, "concept", contract.EntityConcept)
	require.Equal(t, "rationale", contract.EntityRationale)
}
```

2. **Run it (expect FAIL).**
```
go test ./internal/contract/ -run 'TestEntityProvenance|TestDocEntityType' -count=1
```
Expected FAIL: `undefined: contract.EntityDocFile` (compile error in test).

3. **Minimal implementation.** In `internal/contract/contract.go`, replace the `Entity` struct (lines 7-14) with:

```go
// Entity is a node in the code graph.
type Entity struct {
	ID          string            `json:"id"`
	Name        string            `json:"name"`
	Type        string            `json:"type"`
	Description string            `json:"description,omitempty"`
	FilePath    string            `json:"file_path"`
	LineStart   int               `json:"line_start,omitempty"`  // Go already computes these
	LineEnd     int               `json:"line_end,omitempty"`    //
	SourceURL   string            `json:"source_url,omitempty"`  // doc frontmatter
	Author      string            `json:"author,omitempty"`      // doc frontmatter
	CapturedAt  string            `json:"captured_at,omitempty"` // doc frontmatter, RFC3339
	Properties  map[string]string `json:"properties,omitempty"`
}
```

   Then in the `Entity types` const block (currently lines 106-127), add the four new constants after `EntityHelmValue`:

```go
	EntityHelmValue    = "helm_value"
	EntityDocFile      = "doc_file"
	EntityDocSection   = "doc_section"
	EntityConcept      = "concept"
	EntityRationale    = "rationale"
)
```

4. **Run to pass.**
```
go test ./internal/contract/ -run 'TestEntityProvenance|TestDocEntityType' -count=1
```
Expected PASS: `ok  	.../internal/contract`.

5. **Commit.**
```
git add internal/contract/contract.go internal/contract/contract_shape_test.go
git commit -m "feat(contract): add Entity provenance fields and doc entity-type constants"
```

---

## Task 3: Contract — add Hyperedge type + GraphPush.Hyperedges + semantic relations

**Files**
- Modify: `internal/contract/contract.go`
- Modify: `internal/contract/contract_shape_test.go`

**Steps**

1. **Write the failing test.** Append to `internal/contract/contract_shape_test.go`:

```go
func TestHyperedgeJSONShape(t *testing.T) {
	h := contract.Hyperedge{
		ID: "he:1", Label: "trait impl", Relation: "implement",
		ConfidenceScore: 0.9, SrcFile: "x.go",
		Members:    []string{"go:type:m.A", "go:type:m.B", "go:type:m.C"},
		Properties: map[string]string{"k": "v"},
	}
	b, err := json.Marshal(h)
	require.NoError(t, err)
	var got map[string]any
	require.NoError(t, json.Unmarshal(b, &got))
	require.ElementsMatch(t,
		[]string{"id", "label", "relation", "confidence_score", "src_file", "members", "properties"},
		keys(got))
	require.Len(t, got["members"].([]any), 3)
}

func TestGraphPushHyperedgesOmitEmpty(t *testing.T) {
	p := contract.GraphPush{Repo: "r", Files: []string{"a.go"}, Entities: []contract.Entity{}, Edges: []contract.Edge{}}
	b, err := json.Marshal(p)
	require.NoError(t, err)
	var got map[string]any
	require.NoError(t, json.Unmarshal(b, &got))
	_, has := got["hyperedges"]
	require.False(t, has, "hyperedges key must be absent when empty")
}

func TestGraphPushHyperedgesPresentWhenSet(t *testing.T) {
	p := contract.GraphPush{
		Repo: "r", Files: []string{"a.go"}, Entities: []contract.Entity{}, Edges: []contract.Edge{},
		Hyperedges: []contract.Hyperedge{{ID: "he:1", Label: "l", Relation: "form", SrcFile: "a.go",
			Members: []string{"x", "y", "z"}}},
	}
	b, err := json.Marshal(p)
	require.NoError(t, err)
	var got map[string]any
	require.NoError(t, json.Unmarshal(b, &got))
	require.Len(t, got["hyperedges"].([]any), 1)
}

func TestSemanticRelationConstants(t *testing.T) {
	require.Equal(t, "conceptually_related_to", contract.RelConceptuallyRelated)
	require.Equal(t, "semantically_similar_to", contract.RelSemanticallySimilar)
	require.Equal(t, "rationale_for", contract.RelRationaleFor)
	require.Equal(t, "shares_data_with", contract.RelSharesDataWith)
	require.Equal(t, "cites", contract.RelCites)
}
```

2. **Run it (expect FAIL).**
```
go test ./internal/contract/ -run 'TestHyperedge|TestGraphPushHyperedges|TestSemanticRelation' -count=1
```
Expected FAIL: `undefined: contract.Hyperedge` (compile error in test).

3. **Minimal implementation.** In `internal/contract/contract.go`, add the `Hyperedge` type immediately before the `GraphPush` definition (before line 41):

```go
// Hyperedge is an n-ary relationship over 3+ entities (Phase 2 producer; reserved now).
type Hyperedge struct {
	ID              string            `json:"id"`
	Label           string            `json:"label"`
	Relation        string            `json:"relation"` // participate_in|implement|form
	ConfidenceScore float64           `json:"confidence_score,omitempty"`
	SrcFile         string            `json:"src_file"`
	Members         []string          `json:"members"` // entity IDs (3+)
	Properties      map[string]string `json:"properties,omitempty"`
}
```

   Replace the `GraphPush` struct (lines 41-49) with:

```go
// GraphPush is one /code-graph:bulk request.
type GraphPush struct {
	Repo       string      `json:"repo"`
	Commit     string      `json:"commit,omitempty"`
	Files      []string    `json:"files"`
	Entities   []Entity    `json:"entities"`
	Edges      []Edge      `json:"edges"`
	Symbols    []SymbolRow `json:"symbols,omitempty"`
	Hyperedges []Hyperedge `json:"hyperedges,omitempty"` // empty until Phase 2
}
```

   In the `Edge relations` const block (currently ending at line 144 with `RelSubchart`), add the five semantic relations after `RelSubchart`:

```go
	RelSubchart     = "subchart"

	// Semantic relations (reserved Phase 0, emitted Phase 2).
	RelConceptuallyRelated = "conceptually_related_to"
	RelSemanticallySimilar = "semantically_similar_to"
	RelRationaleFor        = "rationale_for"
	RelSharesDataWith      = "shares_data_with"
	RelCites               = "cites"
)
```

4. **Run to pass.**
```
go test ./internal/contract/ -run 'TestHyperedge|TestGraphPushHyperedges|TestSemanticRelation' -count=1
```
Expected PASS: `ok  	.../internal/contract`.

5. **Commit.**
```
git add internal/contract/contract.go internal/contract/contract_shape_test.go
git commit -m "feat(contract): add Hyperedge type, GraphPush.Hyperedges, and semantic relation constants"
```

---

## Task 4: Contract — BulkMemoriesRequest with reconcile_files

**Files**
- Modify: `internal/contract/contract.go`
- Modify: `internal/contract/contract_shape_test.go`

**Steps**

1. **Write the failing test.** Append to `internal/contract/contract_shape_test.go`:

```go
func TestBulkMemoriesRequestShape(t *testing.T) {
	req := contract.BulkMemoriesRequest{
		ReconcileFiles: []string{"a.go", "b.md"},
		Items:          []contract.IngestItem{{IdempotencyKey: "k", Text: "t"}},
	}
	b, err := json.Marshal(req)
	require.NoError(t, err)
	var got map[string]any
	require.NoError(t, json.Unmarshal(b, &got))
	require.ElementsMatch(t, []string{"reconcile_files", "items"}, keys(got))
	require.Len(t, got["reconcile_files"].([]any), 2)
}

func TestBulkMemoriesRequestReconcileOmitEmpty(t *testing.T) {
	req := contract.BulkMemoriesRequest{Items: []contract.IngestItem{{IdempotencyKey: "k", Text: "t"}}}
	b, err := json.Marshal(req)
	require.NoError(t, err)
	var got map[string]any
	require.NoError(t, json.Unmarshal(b, &got))
	_, has := got["reconcile_files"]
	require.False(t, has, "reconcile_files must be absent when empty")
	require.ElementsMatch(t, []string{"items"}, keys(got))
}
```

2. **Run it (expect FAIL).**
```
go test ./internal/contract/ -run 'TestBulkMemoriesRequest' -count=1
```
Expected FAIL: `undefined: contract.BulkMemoriesRequest` (compile error in test).

3. **Minimal implementation.** In `internal/contract/contract.go`, add immediately after the `IngestItem` struct (after line 64):

```go
// BulkMemoriesRequest is the /memories:bulk request body. ReconcileFiles, when
// set, instructs the server to purge prior memories for each file (by source)
// before inserting Items. Absent ReconcileFiles preserves insert-only behavior.
type BulkMemoriesRequest struct {
	ReconcileFiles []string     `json:"reconcile_files,omitempty"`
	Items          []IngestItem `json:"items"`
}
```

4. **Run to pass.**
```
go test ./internal/contract/ -count=1
```
Expected PASS: all contract tests green (`ok  	.../internal/contract`).

5. **Commit.**
```
git add internal/contract/contract.go internal/contract/contract_shape_test.go
git commit -m "feat(contract): add BulkMemoriesRequest with reconcile_files"
```

---

## Task 5: Walk — status-aware Diff: full / since classification + Change/Changes types

**Files**
- Modify: `internal/walk/walk.go`
- Modify: `internal/walk/walk_test.go`

> The existing `Changed` is removed and replaced by `Diff`. The existing three tests (`TestFullWalkListsTrackedFiles`, `TestSinceWalkListsOnlyChanged`, `TestFullFlagOverridesSince`) are rewritten in this task to use `Diff`. All callers (`cmd/tatara-ingest/run.go`) are updated in Task 8; this task leaves `run.go` temporarily referencing `walk.Changed`, so package `walk` builds and its own tests pass, while `cmd/...` will not build until Task 8. Run only the `walk` package tests in this task's run commands.

**Steps**

1. **Write the failing test.** Replace the entire body of `internal/walk/walk_test.go` (keep the helpers `gitRepo`, `write`, `commit`) with:

```go
package walk_test

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/walk"
)

func gitRepo(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	for _, args := range [][]string{
		{"init", "-q"}, {"config", "user.email", "t@t"}, {"config", "user.name", "t"},
	} {
		cmd := exec.Command("git", args...)
		cmd.Dir = dir
		require.NoError(t, cmd.Run())
	}
	return dir
}

func write(t *testing.T, dir, rel, content string) {
	t.Helper()
	p := filepath.Join(dir, rel)
	require.NoError(t, os.MkdirAll(filepath.Dir(p), 0o755))
	require.NoError(t, os.WriteFile(p, []byte(content), 0o644))
}

func commit(t *testing.T, dir, msg string) string {
	t.Helper()
	for _, args := range [][]string{{"add", "-A"}, {"commit", "-q", "-m", msg}} {
		cmd := exec.Command("git", args...)
		cmd.Dir = dir
		require.NoError(t, cmd.Run())
	}
	out, err := exec.Command("git", "-C", dir, "rev-parse", "HEAD").Output()
	require.NoError(t, err)
	return string(out[:len(out)-1])
}

func sha(content string) string {
	sum := sha256.Sum256([]byte(content))
	return hex.EncodeToString(sum[:])
}

// byPath indexes a Changes result by new path for assertions.
func byPath(c walk.Changes) map[string]walk.Change {
	m := map[string]walk.Change{}
	for _, ch := range c.Files {
		m[ch.Path] = ch
	}
	return m
}

func TestFullWalkListsTrackedFiles(t *testing.T) {
	dir := gitRepo(t)
	write(t, dir, "a.go", "package a")
	write(t, dir, "sub/b.py", "x = 1")
	commit(t, dir, "init")

	got, err := walk.Diff(dir, "", false)
	require.NoError(t, err)
	require.True(t, got.FullSet)
	m := byPath(got)
	require.Len(t, m, 2)
	require.Equal(t, 'A', m["a.go"].Status)
	require.Equal(t, 'A', m["sub/b.py"].Status)
	require.Equal(t, sha("package a"), m["a.go"].ContentSHA)
}

func TestFullFlagOverridesSince(t *testing.T) {
	dir := gitRepo(t)
	write(t, dir, "a.go", "package a")
	base := commit(t, dir, "init")
	write(t, dir, "c.go", "package c")
	commit(t, dir, "add c")

	got, err := walk.Diff(dir, base, true)
	require.NoError(t, err)
	require.True(t, got.FullSet)
	m := byPath(got)
	require.Len(t, m, 2)
	require.Contains(t, m, "a.go")
	require.Contains(t, m, "c.go")
}
```

2. **Run it (expect FAIL).**
```
go test ./internal/walk/ -run 'TestFullWalkListsTrackedFiles|TestFullFlagOverridesSince' -count=1
```
Expected FAIL: `undefined: walk.Diff` / `undefined: walk.Changes` (compile error).

3. **Minimal implementation.** Replace the entire contents of `internal/walk/walk.go` with:

```go
// Package walk lists the repository files to ingest.
package walk

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
)

// Change is one touched file classified by git status.
type Change struct {
	Path       string // new path (for renames, the destination)
	OldPath    string // populated only for renames
	Status     rune   // 'A' added, 'M' modified, 'D' deleted, 'R' renamed
	ContentSHA string // sha256 of working-tree content for A/M; empty for D
}

// Changes is the classified diff result.
type Changes struct {
	Files   []Change // every touched file
	FullSet bool     // true when produced by ls-files (first/full/fallback)
}

// Diff classifies the files to ingest. With full or empty since, it lists all
// tracked files as additions (FullSet). Otherwise it runs
// `git diff --name-status <since>..HEAD`; if that command fails (since not an
// ancestor: force-push, rebase, GC'd commit) it logs WARN and falls back to the
// full ls-files path so a job never hard-fails on history rewrite.
func Diff(repoRoot, since string, full bool) (Changes, error) {
	if full || since == "" {
		return fullSet(repoRoot)
	}
	out, err := exec.Command("git", "-C", repoRoot, "diff", "--name-status", since+"..HEAD").Output() //nolint:gosec
	if err != nil {
		slog.Warn("git diff failed; falling back to full ls-files",
			"since", since, "error", err)
		return fullSet(repoRoot)
	}
	return parseDiff(repoRoot, string(out))
}

// fullSet lists all tracked files as additions.
func fullSet(repoRoot string) (Changes, error) {
	out, err := exec.Command("git", "-C", repoRoot, "ls-files").Output() //nolint:gosec
	if err != nil {
		return Changes{}, fmt.Errorf("git -C %s ls-files: %w", repoRoot, err)
	}
	var files []Change
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if line == "" {
			continue
		}
		files = append(files, Change{Path: line, Status: 'A', ContentSHA: contentSHA(repoRoot, line)})
	}
	sortChanges(files)
	return Changes{Files: files, FullSet: true}, nil
}

// parseDiff turns `git diff --name-status` output into classified changes.
// Rename lines `R<score>\told\tnew` become a single Change with Status 'R',
// OldPath=old, Path=new. Copy lines `C<score>` are treated as additions of new.
func parseDiff(repoRoot, out string) (Changes, error) {
	var files []Change
	for _, line := range strings.Split(strings.TrimSpace(out), "\n") {
		if line == "" {
			continue
		}
		fields := strings.Split(line, "\t")
		code := fields[0]
		switch code[0] {
		case 'A', 'M':
			if len(fields) < 2 {
				continue
			}
			p := fields[1]
			files = append(files, Change{Path: p, Status: rune(code[0]), ContentSHA: contentSHA(repoRoot, p)})
		case 'D':
			if len(fields) < 2 {
				continue
			}
			files = append(files, Change{Path: fields[1], Status: 'D'})
		case 'R':
			if len(fields) < 3 {
				continue
			}
			files = append(files, Change{
				Path: fields[2], OldPath: fields[1], Status: 'R',
				ContentSHA: contentSHA(repoRoot, fields[2]),
			})
		case 'C':
			if len(fields) < 3 {
				continue
			}
			p := fields[2]
			files = append(files, Change{Path: p, Status: 'A', ContentSHA: contentSHA(repoRoot, p)})
		}
	}
	sortChanges(files)
	return Changes{Files: files, FullSet: false}, nil
}

// contentSHA returns the sha256 hex of the working-tree file; empty when unreadable.
func contentSHA(repoRoot, rel string) string {
	b, err := os.ReadFile(filepath.Join(repoRoot, rel)) //nolint:gosec
	if err != nil {
		return ""
	}
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

// sortChanges orders by new path for deterministic output.
func sortChanges(files []Change) {
	sort.Slice(files, func(i, j int) bool { return files[i].Path < files[j].Path })
}
```

   > Note: `run.go` still references `walk.Changed` and will not compile after this change. That is expected; Task 8 fixes it. Do not run `go build ./...` or `go test ./...` in this task — run only `./internal/walk/`.

4. **Run to pass.**
```
go test ./internal/walk/ -run 'TestFullWalkListsTrackedFiles|TestFullFlagOverridesSince' -count=1
```
Expected PASS: `ok  	.../internal/walk`.

5. **Commit.**
```
git add internal/walk/walk.go internal/walk/walk_test.go
git commit -m "feat(walk): replace Changed with status-aware Diff returning Change/Changes"
```

---

## Task 6: Walk — A/M/D/R classification + rename pairing on a since diff

**Files**
- Modify: `internal/walk/walk_test.go`

**Steps**

1. **Write the failing test.** Append to `internal/walk/walk_test.go`:

```go
func TestSinceDiffClassifiesAddModifyDelete(t *testing.T) {
	dir := gitRepo(t)
	write(t, dir, "keep.go", "package a")
	write(t, dir, "gone.go", "package g")
	base := commit(t, dir, "init")

	write(t, dir, "keep.go", "package a // changed")
	write(t, dir, "new.go", "package n")
	require.NoError(t, os.Remove(filepath.Join(dir, "gone.go")))
	commit(t, dir, "mutate")

	got, err := walk.Diff(dir, base, false)
	require.NoError(t, err)
	require.False(t, got.FullSet)
	m := byPath(got)
	require.Equal(t, 'M', m["keep.go"].Status)
	require.Equal(t, sha("package a // changed"), m["keep.go"].ContentSHA)
	require.Equal(t, 'A', m["new.go"].Status)
	require.Equal(t, sha("package n"), m["new.go"].ContentSHA)
	require.Equal(t, 'D', m["gone.go"].Status)
	require.Empty(t, m["gone.go"].ContentSHA, "deleted file has no content sha")
}

func TestSinceDiffPairsRename(t *testing.T) {
	dir := gitRepo(t)
	write(t, dir, "old/name.go", "package x\n\nfunc Stable() {}\n")
	base := commit(t, dir, "init")

	require.NoError(t, os.MkdirAll(filepath.Join(dir, "new"), 0o755))
	require.NoError(t, os.Rename(
		filepath.Join(dir, "old/name.go"), filepath.Join(dir, "new/name.go")))
	commit(t, dir, "rename")

	got, err := walk.Diff(dir, base, false)
	require.NoError(t, err)
	require.False(t, got.FullSet)
	require.Len(t, got.Files, 1)
	ch := got.Files[0]
	require.Equal(t, 'R', ch.Status)
	require.Equal(t, "new/name.go", ch.Path)
	require.Equal(t, "old/name.go", ch.OldPath)
	require.Equal(t, sha("package x\n\nfunc Stable() {}\n"), ch.ContentSHA)
}
```

2. **Run it (expect FAIL or PASS-after-impl).** Implementation from Task 5 already supports A/M/D/R; this task asserts the behavior contractually.
```
go test ./internal/walk/ -run 'TestSinceDiffClassifies|TestSinceDiffPairsRename' -count=1
```
Expected: PASS (rename detection is git's default for a pure move). If the rename test FAILS because git reports `D`+`A` instead of `R` (rare for tiny files), make rename detection explicit by changing the diff invocation in `walk.go` `Diff` to add `-M`:
```
exec.Command("git", "-C", repoRoot, "diff", "-M", "--name-status", since+"..HEAD")
```
Re-run; expect PASS.

3. **Minimal implementation.** No new code beyond the optional `-M` flag above if the rename test required it. (If `-M` was added, include `internal/walk/walk.go` in the commit.)

4. **Run to pass.**
```
go test ./internal/walk/ -count=1
```
Expected PASS: `ok  	.../internal/walk`.

5. **Commit.**
```
git add internal/walk/walk_test.go internal/walk/walk.go
git commit -m "test(walk): cover add/modify/delete classification and rename pairing"
```

---

## Task 7: Walk — missing-since fallback to full ls-files with WARN

**Files**
- Modify: `internal/walk/walk_test.go`

**Steps**

1. **Write the failing test.** Append to `internal/walk/walk_test.go`:

```go
func TestMissingSinceFallsBackToFull(t *testing.T) {
	dir := gitRepo(t)
	write(t, dir, "a.go", "package a")
	write(t, dir, "b.go", "package b")
	commit(t, dir, "init")

	// A since SHA that does not exist in this repo: diff must error and fall back.
	got, err := walk.Diff(dir, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef", false)
	require.NoError(t, err, "missing since must not hard-fail")
	require.True(t, got.FullSet, "fallback uses ls-files full set")
	m := byPath(got)
	require.Len(t, m, 2)
	require.Equal(t, 'A', m["a.go"].Status)
	require.Equal(t, 'A', m["b.go"].Status)
}
```

2. **Run it (expect PASS — guards the fallback).**
```
go test ./internal/walk/ -run 'TestMissingSinceFallsBackToFull' -count=1
```
Expected PASS (Task 5 implemented the WARN + fallback). If it FAILS with a non-nil error, the fallback branch is missing/incorrect — re-check the `if err != nil` branch in `Diff` returns `fullSet(repoRoot)`, then re-run; expect PASS.

3. **Minimal implementation.** None expected; the fallback path already exists.

4. **Run to pass.**
```
go test ./internal/walk/ -count=1
```
Expected PASS: `ok  	.../internal/walk`.

5. **Commit.**
```
git add internal/walk/walk_test.go
git commit -m "test(walk): missing since falls back to full ls-files without hard-failing"
```

---

## Task 8: run.go — consume walk.Diff and split touched files into changed vs purged

**Files**
- Modify: `cmd/tatara-ingest/run.go`
- Modify: `cmd/tatara-ingest/run_test.go`

> After Task 5 the `cmd/...` package does not build (still calls `walk.Changed`). This task restores the build and wires the classified diff into analysis. The reconcile_files plumbing into `/memories:bulk` lands in Task 9; the deleted-paths-into-code-graph behavior is asserted in Task 10.

**Steps**

1. **Write the failing test.** Append to `cmd/tatara-ingest/run_test.go`:

```go
func TestRunSendsDeletedFilesInGraphAndReconcile(t *testing.T) {
	dir := t.TempDir()
	for _, a := range [][]string{{"init", "-q"}, {"config", "user.email", "t@t"}, {"config", "user.name", "t"}} {
		c := exec.Command("git", a...)
		c.Dir = dir
		require.NoError(t, c.Run())
	}
	require.NoError(t, os.WriteFile(filepath.Join(dir, "keep.go"),
		[]byte("package m\n\nfunc Keep() {}\n"), 0o644))
	require.NoError(t, os.WriteFile(filepath.Join(dir, "gone.go"),
		[]byte("package m\n\nfunc Gone() {}\n"), 0o644))
	require.NoError(t, os.WriteFile(filepath.Join(dir, "go.mod"),
		[]byte("module example.com/m\n\ngo 1.25\n"), 0o644))
	base := commitAll(t, dir, "init")

	require.NoError(t, os.Remove(filepath.Join(dir, "gone.go")))
	require.NoError(t, os.WriteFile(filepath.Join(dir, "keep.go"),
		[]byte("package m\n\nfunc Keep() { _ = 1 }\n"), 0o644))
	commitAll(t, dir, "delete gone, modify keep")

	var capturedPush contract.GraphPush
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/code-graph:bulk":
			body, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(body, &capturedPush)
			w.WriteHeader(200)
			_, _ = w.Write([]byte(`{"repo":"m"}`))
		case "/memories:bulk":
			w.WriteHeader(202)
			_, _ = w.Write([]byte(`{"id":"j","status":"succeeded"}`))
		default:
			_, _ = w.Write([]byte(`{"id":"j","status":"succeeded"}`))
		}
	}))
	defer srv.Close()

	opts := options{repoRoot: dir, repoName: "m", baseURL: srv.URL, since: base}
	require.NoError(t, run(context.Background(), opts, http.DefaultClient))

	require.Contains(t, capturedPush.Files, "gone.go", "deleted file must be in code-graph Files")
	require.Contains(t, capturedPush.Files, "keep.go", "modified file must be in code-graph Files")
	for _, e := range capturedPush.Entities {
		require.NotEqual(t, "gone.go", e.FilePath, "deleted file must contribute no entities")
	}
}

// commitAll commits all changes and returns HEAD.
func commitAll(t *testing.T, dir, msg string) string {
	t.Helper()
	for _, a := range [][]string{{"add", "-A"}, {"commit", "-q", "-m", msg}} {
		c := exec.Command("git", a...)
		c.Dir = dir
		require.NoError(t, c.Run())
	}
	out, err := exec.Command("git", "-C", dir, "rev-parse", "HEAD").Output()
	require.NoError(t, err)
	return strings.TrimSpace(string(out))
}
```

   Add `"strings"` to the `cmd/tatara-ingest/run_test.go` import block if not already present.

2. **Run it (expect FAIL).**
```
go test ./cmd/tatara-ingest/ -run 'TestRunSendsDeletedFilesInGraphAndReconcile' -count=1
```
Expected FAIL: compile error `cl.Changed` / `walk.Changed` undefined (the package no longer builds against the new `walk` API).

3. **Minimal implementation.** In `cmd/tatara-ingest/run.go`, replace the `run` function (lines 36-82) with:

```go
func run(ctx context.Context, o options, hc *http.Client) error {
	if o.scipPath != "" {
		return runSCIP(ctx, o, hc)
	}
	start := time.Now()
	changes, err := walk.Diff(o.repoRoot, o.since, o.full)
	if err != nil {
		return err
	}

	// touched is every file in the diff (code-graph Files + memories reconcile_files).
	// analyzeFiles is only A/M/renamed-new (the files we re-analyze and chunk).
	var touched, analyzeFiles []string
	for _, ch := range changes.Files {
		touched = append(touched, ch.Path)
		switch ch.Status {
		case 'A', 'M', 'R':
			analyzeFiles = append(analyzeFiles, ch.Path)
		}
	}

	reg := analyze.Default(o.crossRepoPrefix)
	groups := reg.Group(analyzeFiles)

	var agg analyze.Result
	for _, a := range reg.Analyzers() {
		fs := groups[a.Name()]
		if len(fs) == 0 {
			continue
		}
		res, err := a.Analyze(ctx, o.repoRoot, fs)
		if err != nil {
			slog.Warn("analyzer failed", "analyzer", a.Name(), "error", err)
			continue
		}
		agg.Entities = append(agg.Entities, res.Entities...)
		agg.Edges = append(agg.Edges, res.Edges...)
		agg.Chunks = append(agg.Chunks, res.Chunks...)
		agg.Symbols = append(agg.Symbols, res.Symbols...)
	}

	commit := headCommit(o.repoRoot)
	cl := push.New(o.baseURL, hc, pollOr(o.pollInterval))
	if _, err := cl.PushGraph(ctx, contract.GraphPush{
		Repo: o.repoName, Commit: commit, Files: touched,
		Entities: agg.Entities, Edges: agg.Edges, Symbols: agg.Symbols,
		Hyperedges: agg.Hyperedges,
	}); err != nil {
		return err
	}
	reconcile := touched
	if changes.FullSet {
		reconcile = nil // first/full ingest is insert-only (no reconcile)
	}
	if err := cl.PushChunks(ctx, reconcile, push.ItemsFromChunks(o.repoName, agg.Chunks)); err != nil {
		return err
	}
	slog.Info("ingest complete",
		"repo", o.repoName, "files", len(touched),
		"analyzed", len(analyzeFiles),
		"entities", len(agg.Entities), "edges", len(agg.Edges),
		"chunks", len(agg.Chunks), "symbols", len(agg.Symbols),
		"full", changes.FullSet,
		"duration_ms", time.Since(start).Milliseconds())
	return nil
}
```

   This references `agg.Hyperedges` (added in Task 12) and the new `PushChunks(ctx, reconcile, items)` signature (added in Task 9). Both are introduced in their own tasks; this task changes `PushChunks` call shape too — so do them together is NOT required because the test in this task only needs the build to pass via the Task 9 signature. To keep this task self-contained and GREEN, **also apply the Task 9 `PushChunks` signature change and the Task 12 `analyze.Result.Hyperedges` field now**, OR sequence: do Task 9 and Task 12 first. Recommended sequencing: execute Task 9 and Task 12 before this task. If sequencing Task 9/12 first, the snippet above compiles directly.

   > To avoid a forward reference, the executing agent runs **Task 9 then Task 12 then this Task 8**. (The plan keeps Task numbering for traceability; honor the dependency note.)

4. **Run to pass.**
```
go test ./cmd/tatara-ingest/ -run 'TestRunSendsDeletedFilesInGraphAndReconcile' -count=1
```
Expected PASS: `ok  	.../cmd/tatara-ingest`.

5. **Commit.**
```
git add cmd/tatara-ingest/run.go cmd/tatara-ingest/run_test.go
git commit -m "feat(ingest): drive reconcile-per-file from walk.Diff; deleted paths into code-graph Files"
```

---

## Task 9: push — PushChunks sends BulkMemoriesRequest with reconcile_files

**Files**
- Modify: `internal/push/push.go`
- Modify: `internal/push/push_test.go`

> Execute this task BEFORE Task 8 (Task 8's `run.go` calls the new `PushChunks` signature).

**Steps**

1. **Write the failing test.** Append to `internal/push/push_test.go`:

```go
func TestPushChunksSendsReconcileFiles(t *testing.T) {
	var gotReq contract.BulkMemoriesRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/memories:bulk":
			require.NoError(t, json.NewDecoder(r.Body).Decode(&gotReq))
			w.WriteHeader(202)
			_ = json.NewEncoder(w).Encode(contract.IngestJob{ID: "j1", Status: contract.JobSucceeded, Total: 1, Done: 1})
		case strings.HasPrefix(r.URL.Path, "/ingest-jobs/"):
			_ = json.NewEncoder(w).Encode(contract.IngestJob{ID: "j1", Status: contract.JobSucceeded, Total: 1, Done: 1})
		}
	}))
	defer srv.Close()
	c := push.New(srv.URL, http.DefaultClient, time.Millisecond)
	err := c.PushChunks(context.Background(),
		[]string{"a.go", "gone.go"},
		[]contract.IngestItem{{IdempotencyKey: "k", Text: "t"}})
	require.NoError(t, err)
	require.ElementsMatch(t, []string{"a.go", "gone.go"}, gotReq.ReconcileFiles)
	require.Len(t, gotReq.Items, 1)
}

func TestPushChunksReconcileOnlyDeletion(t *testing.T) {
	// A pure deletion: reconcile_files set, no items. Must still POST and reconcile.
	var gotReq contract.BulkMemoriesRequest
	var posted bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/memories:bulk":
			posted = true
			require.NoError(t, json.NewDecoder(r.Body).Decode(&gotReq))
			w.WriteHeader(202)
			_ = json.NewEncoder(w).Encode(contract.IngestJob{ID: "j1", Status: contract.JobSucceeded})
		case strings.HasPrefix(r.URL.Path, "/ingest-jobs/"):
			_ = json.NewEncoder(w).Encode(contract.IngestJob{ID: "j1", Status: contract.JobSucceeded})
		}
	}))
	defer srv.Close()
	c := push.New(srv.URL, http.DefaultClient, time.Millisecond)
	err := c.PushChunks(context.Background(), []string{"gone.go"}, nil)
	require.NoError(t, err)
	require.True(t, posted, "deletion-only reconcile must still POST /memories:bulk")
	require.Equal(t, []string{"gone.go"}, gotReq.ReconcileFiles)
	require.Empty(t, gotReq.Items)
}

func TestPushChunksNoopWhenNothingToDo(t *testing.T) {
	var posted bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		posted = true
		w.WriteHeader(202)
		_ = json.NewEncoder(w).Encode(contract.IngestJob{ID: "j1", Status: contract.JobSucceeded})
	}))
	defer srv.Close()
	c := push.New(srv.URL, http.DefaultClient, time.Millisecond)
	require.NoError(t, c.PushChunks(context.Background(), nil, nil))
	require.False(t, posted, "no reconcile and no items must not POST")
}
```

   Update the existing `TestPushChunksPollsToTerminal` and `TestPushChunksPartialIsError` calls (lines 57 and 73 of `push_test.go`) to the new signature by inserting a `nil` reconcile argument:
   - `c.PushChunks(context.Background(), nil, []contract.IngestItem{{IdempotencyKey: "k", Text: "t"}})`

   And in `TestPushChunksPollsToTerminal`'s handler, replace the bare `struct{ Items ... }` decode (lines 39-42) with:
```go
				var req contract.BulkMemoriesRequest
				require.NoError(t, json.NewDecoder(r.Body).Decode(&req))
				require.Len(t, req.Items, 1)
```

2. **Run it (expect FAIL).**
```
go test ./internal/push/ -run 'TestPushChunks' -count=1
```
Expected FAIL: `too many arguments in call to c.PushChunks` (signature mismatch, compile error).

3. **Minimal implementation.** In `internal/push/push.go`, replace the `PushChunks` method (lines 37-63) with:

```go
// PushChunks posts a reconcile-aware bulk and polls the resulting job to a
// terminal state. reconcileFiles, when non-empty, instructs the server to purge
// prior memories for each file before inserting items. When both reconcileFiles
// and items are empty there is nothing to do.
func (c *Client) PushChunks(ctx context.Context, reconcileFiles []string, items []contract.IngestItem) error {
	if len(items) == 0 && len(reconcileFiles) == 0 {
		return nil
	}
	var job contract.IngestJob
	body := contract.BulkMemoriesRequest{ReconcileFiles: reconcileFiles, Items: items}
	if err := c.do(ctx, http.MethodPost, "/memories:bulk", body, http.StatusAccepted, &job); err != nil {
		return err
	}
	for !job.Terminal() {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(c.pollInterval):
		}
		if err := c.do(ctx, http.MethodGet, "/ingest-jobs/"+job.ID, nil, http.StatusOK, &job); err != nil {
			return err
		}
	}
	if job.Status != contract.JobSucceeded {
		return fmt.Errorf("ingest job %s ended %s (failed=%d)", job.ID, job.Status, job.Failed)
	}
	return nil
}
```

4. **Run to pass.**
```
go test ./internal/push/ -count=1
```
Expected PASS: `ok  	.../internal/push`.

5. **Commit.**
```
git add internal/push/push.go internal/push/push_test.go
git commit -m "feat(push): PushChunks sends BulkMemoriesRequest with reconcile_files"
```

---

## Task 10: run.go — reconcile_files carries the full touched set on a since diff

**Files**
- Modify: `cmd/tatara-ingest/run_test.go`

> Asserts the reconcile_files wiring end-to-end through `run` (depends on Tasks 8 + 9 done).

**Steps**

1. **Write the failing test.** Append to `cmd/tatara-ingest/run_test.go`:

```go
func TestRunReconcileFilesMatchTouchedSet(t *testing.T) {
	dir := t.TempDir()
	for _, a := range [][]string{{"init", "-q"}, {"config", "user.email", "t@t"}, {"config", "user.name", "t"}} {
		c := exec.Command("git", a...)
		c.Dir = dir
		require.NoError(t, c.Run())
	}
	require.NoError(t, os.WriteFile(filepath.Join(dir, "doc.md"), []byte("# Doc\n\nbody\n"), 0o644))
	require.NoError(t, os.WriteFile(filepath.Join(dir, "old.md"), []byte("# Old\n\ngone\n"), 0o644))
	base := commitAll(t, dir, "init")

	require.NoError(t, os.Remove(filepath.Join(dir, "old.md")))
	require.NoError(t, os.WriteFile(filepath.Join(dir, "doc.md"), []byte("# Doc\n\nbody2\n"), 0o644))
	commitAll(t, dir, "delete old, modify doc")

	var bulkReq contract.BulkMemoriesRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/code-graph:bulk":
			w.WriteHeader(200)
			_, _ = w.Write([]byte(`{"repo":"m"}`))
		case "/memories:bulk":
			body, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(body, &bulkReq)
			w.WriteHeader(202)
			_, _ = w.Write([]byte(`{"id":"j","status":"succeeded"}`))
		default:
			_, _ = w.Write([]byte(`{"id":"j","status":"succeeded"}`))
		}
	}))
	defer srv.Close()

	opts := options{repoRoot: dir, repoName: "m", baseURL: srv.URL, since: base}
	require.NoError(t, run(context.Background(), opts, http.DefaultClient))
	require.ElementsMatch(t, []string{"doc.md", "old.md"}, bulkReq.ReconcileFiles)
}

func TestRunFullIngestHasNoReconcileFiles(t *testing.T) {
	dir := t.TempDir()
	for _, a := range [][]string{{"init", "-q"}, {"config", "user.email", "t@t"}, {"config", "user.name", "t"}} {
		c := exec.Command("git", a...)
		c.Dir = dir
		require.NoError(t, c.Run())
	}
	require.NoError(t, os.WriteFile(filepath.Join(dir, "doc.md"), []byte("# Doc\n\nbody\n"), 0o644))
	commitAll(t, dir, "init")

	var bulkReq contract.BulkMemoriesRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/code-graph:bulk":
			w.WriteHeader(200)
			_, _ = w.Write([]byte(`{"repo":"m"}`))
		case "/memories:bulk":
			body, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(body, &bulkReq)
			w.WriteHeader(202)
			_, _ = w.Write([]byte(`{"id":"j","status":"succeeded"}`))
		default:
			_, _ = w.Write([]byte(`{"id":"j","status":"succeeded"}`))
		}
	}))
	defer srv.Close()

	opts := options{repoRoot: dir, repoName: "m", baseURL: srv.URL} // full (no since)
	require.NoError(t, run(context.Background(), opts, http.DefaultClient))
	require.Empty(t, bulkReq.ReconcileFiles, "full/first ingest is insert-only")
}
```

2. **Run it (expect FAIL or PASS).** With Tasks 8 + 9 in place this should PASS. If `TestRunReconcileFilesMatchTouchedSet` FAILS because `reconcile` is empty, confirm `run.go` sets `reconcile = touched` for non-full diffs.
```
go test ./cmd/tatara-ingest/ -run 'TestRunReconcileFilesMatchTouchedSet|TestRunFullIngestHasNoReconcileFiles' -count=1
```
Expected PASS.

3. **Minimal implementation.** None expected (behavior added in Task 8). If a fix was needed in `run.go`, include it in the commit.

4. **Run to pass.**
```
go test ./cmd/tatara-ingest/ -count=1
```
Expected PASS: `ok  	.../cmd/tatara-ingest`.

5. **Commit.**
```
git add cmd/tatara-ingest/run_test.go cmd/tatara-ingest/run.go
git commit -m "test(ingest): reconcile_files equals touched set on since diff, empty on full"
```

---

## Task 11: docs.go — emit a doc entity per file and route docs into the code-graph

**Files**
- Modify: `internal/analyze/docs.go`
- Modify: `internal/analyze/docs_test.go`

**Steps**

1. **Write the failing test.** Replace the body of `TestDocsAnalyzer` in `internal/analyze/docs_test.go` and add a new test:

```go
func TestDocsAnalyzer(t *testing.T) {
	a := analyze.NewDocs()
	require.True(t, a.Match("README.md"))
	require.True(t, a.Match("docs/guide.txt"))
	require.False(t, a.Match("main.go"))

	res, err := a.Analyze(context.Background(), "testdata/docs", []string{"README.md"})
	require.NoError(t, err)
	require.Len(t, res.Chunks, 1)
	require.Equal(t, "markdown", res.Chunks[0].Language)
	require.Contains(t, res.Chunks[0].Body, "Some prose.")

	require.Len(t, res.Entities, 1, "doc files now emit a doc entity")
	e := res.Entities[0]
	require.Equal(t, contract.EntityDocFile, e.Type)
	require.Equal(t, "doc:file:README.md", e.ID)
	require.Equal(t, "README.md", e.FilePath)
	require.Equal(t, "README.md", e.Name)
	require.Empty(t, res.Edges)
}
```

   Add the `contract` import to `internal/analyze/docs_test.go`:
```go
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract"
```

2. **Run it (expect FAIL).**
```
go test ./internal/analyze/ -run 'TestDocsAnalyzer' -count=1
```
Expected FAIL: `Not equal: expected 1 actual 0` for `len(res.Entities)` (docs currently emit no entities).

3. **Minimal implementation.** Replace the `Analyze` method in `internal/analyze/docs.go` (lines 27-48) with:

```go
func (docsAnalyzer) Analyze(_ context.Context, repoRoot string, files []string) (Result, error) {
	var res Result
	for _, f := range files {
		b, err := os.ReadFile(filepath.Join(repoRoot, f)) //nolint:gosec
		if err != nil {
			continue // unreadable doc: skip, do not fail the run
		}
		lang := "markdown"
		if strings.ToLower(filepath.Ext(f)) == ".txt" {
			lang = "text"
		}
		res.Entities = append(res.Entities, contract.Entity{
			ID:       "doc:file:" + f,
			Name:     f,
			Type:     contract.EntityDocFile,
			FilePath: f,
		})
		res.Chunks = append(res.Chunks, contract.Chunk{
			EntityID: "doc:file:" + f,
			Type:     contract.EntityDocFile,
			FilePath: f,
			Language: lang,
			Header:   "[doc] " + f,
			Body:     string(b),
		})
	}
	return res, nil
}
```

   > The chunk `EntityID` now matches the entity ID (`doc:file:<path>`) so semantic chunks reference the graph node, and `Type` becomes `EntityDocFile` so `code_search` matches docs by type.

4. **Run to pass.**
```
go test ./internal/analyze/ -run 'TestDocsAnalyzer' -count=1
```
Expected PASS: `ok  	.../internal/analyze`.

5. **Commit.**
```
git add internal/analyze/docs.go internal/analyze/docs_test.go
git commit -m "feat(analyze): DocsAnalyzer emits a doc_file entity per file into the code-graph"
```

---

## Task 12: analyze.Result — carry Hyperedges so run.go can forward them

**Files**
- Modify: `internal/analyze/analyzer.go`
- Modify: `internal/analyze/analyzer_test.go`

> Execute this task BEFORE Task 8 (run.go forwards `agg.Hyperedges`).

**Steps**

1. **Write the failing test.** Append to `internal/analyze/analyzer_test.go` (create the test file's package/imports if reusing the existing `analyze_test` package — check the file first; it is `package analyze_test`):

```go
func TestResultCarriesHyperedges(t *testing.T) {
	var r analyze.Result
	r.Hyperedges = append(r.Hyperedges, contract.Hyperedge{
		ID: "he:1", Label: "l", Relation: "form", SrcFile: "a.go",
		Members: []string{"x", "y", "z"},
	})
	require.Len(t, r.Hyperedges, 1)
	require.Equal(t, "he:1", r.Hyperedges[0].ID)
}
```

   Ensure `internal/analyze/analyzer_test.go` imports both:
```go
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/analyze"
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract"
	"github.com/stretchr/testify/require"
```

2. **Run it (expect FAIL).**
```
go test ./internal/analyze/ -run 'TestResultCarriesHyperedges' -count=1
```
Expected FAIL: `r.Hyperedges undefined (type analyze.Result has no field or method Hyperedges)`.

3. **Minimal implementation.** In `internal/analyze/analyzer.go`, replace the `Result` struct (lines 12-18) with:

```go
// Result is what an Analyzer emits for its assigned file set.
type Result struct {
	Entities   []contract.Entity
	Edges      []contract.Edge
	Chunks     []contract.Chunk
	Symbols    []contract.SymbolRow
	Hyperedges []contract.Hyperedge
}
```

4. **Run to pass.**
```
go test ./internal/analyze/ -run 'TestResultCarriesHyperedges' -count=1
```
Expected PASS: `ok  	.../internal/analyze`.

5. **Commit.**
```
git add internal/analyze/analyzer.go internal/analyze/analyzer_test.go
git commit -m "feat(analyze): add Hyperedges to Result for Phase 0 forward-compat"
```

---

## Task 13: docs.go — capture YAML frontmatter (source_url/author/captured_at) onto the doc entity

**Files**
- Modify: `internal/analyze/docs.go`
- Modify: `internal/analyze/docs_test.go`
- Create: `internal/analyze/testdata/docs/front.md`

> Frontmatter is the leading `---\n...\n---\n` YAML block of a Markdown file. Parsed with `sigs.k8s.io/yaml` (already a direct dependency; JSON struct tags). Only available at analyze time, so it is captured here.

**Steps**

1. **Write the failing test.** First create the fixture `internal/analyze/testdata/docs/front.md` with exactly:

```
---
source_url: https://example.com/origin
author: Alice Example
captured_at: 2026-06-09T12:00:00Z
---
# Captured Doc

This document came from elsewhere.
```

   Append to `internal/analyze/docs_test.go`:

```go
func TestDocsAnalyzerCapturesFrontmatter(t *testing.T) {
	a := analyze.NewDocs()
	res, err := a.Analyze(context.Background(), "testdata/docs", []string{"front.md"})
	require.NoError(t, err)
	require.Len(t, res.Entities, 1)
	e := res.Entities[0]
	require.Equal(t, "https://example.com/origin", e.SourceURL)
	require.Equal(t, "Alice Example", e.Author)
	require.Equal(t, "2026-06-09T12:00:00Z", e.CapturedAt)

	require.Len(t, res.Chunks, 1)
	require.Contains(t, res.Chunks[0].Body, "This document came from elsewhere.")
	require.NotContains(t, res.Chunks[0].Body, "source_url",
		"frontmatter block must be stripped from the chunk body")
}

func TestDocsAnalyzerNoFrontmatter(t *testing.T) {
	a := analyze.NewDocs()
	res, err := a.Analyze(context.Background(), "testdata/docs", []string{"README.md"})
	require.NoError(t, err)
	require.Len(t, res.Entities, 1)
	e := res.Entities[0]
	require.Empty(t, e.SourceURL)
	require.Empty(t, e.Author)
	require.Empty(t, e.CapturedAt)
	require.Contains(t, res.Chunks[0].Body, "Some prose.")
}
```

2. **Run it (expect FAIL).**
```
go test ./internal/analyze/ -run 'TestDocsAnalyzerCapturesFrontmatter|TestDocsAnalyzerNoFrontmatter' -count=1
```
Expected FAIL: `Not equal: expected "https://example.com/origin" actual ""` (frontmatter not parsed yet).

3. **Minimal implementation.** Replace the entire `internal/analyze/docs.go` with:

```go
package analyze

import (
	"context"
	"os"
	"path/filepath"
	"strings"

	sigsyaml "sigs.k8s.io/yaml"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract"
)

type docsAnalyzer struct{}

// NewDocs returns the docs analyzer. It emits one doc_file entity per file
// (so docs participate in the code graph) plus a semantic chunk, and captures
// YAML frontmatter (source_url/author/captured_at) onto the entity.
func NewDocs() Analyzer { return docsAnalyzer{} }

func (docsAnalyzer) Name() string { return "docs" }

func (docsAnalyzer) Match(path string) bool {
	switch strings.ToLower(filepath.Ext(path)) {
	case ".md", ".markdown", ".txt", ".rst":
		return true
	}
	return false
}

func (docsAnalyzer) Analyze(_ context.Context, repoRoot string, files []string) (Result, error) {
	var res Result
	for _, f := range files {
		b, err := os.ReadFile(filepath.Join(repoRoot, f)) //nolint:gosec
		if err != nil {
			continue // unreadable doc: skip, do not fail the run
		}
		lang := "markdown"
		if strings.ToLower(filepath.Ext(f)) == ".txt" {
			lang = "text"
		}
		fm, body := splitFrontmatter(string(b))
		ent := contract.Entity{
			ID:         "doc:file:" + f,
			Name:       f,
			Type:       contract.EntityDocFile,
			FilePath:   f,
			SourceURL:  fm.SourceURL,
			Author:     fm.Author,
			CapturedAt: fm.CapturedAt,
		}
		res.Entities = append(res.Entities, ent)
		res.Chunks = append(res.Chunks, contract.Chunk{
			EntityID: ent.ID,
			Type:     contract.EntityDocFile,
			FilePath: f,
			Language: lang,
			Header:   "[doc] " + f,
			Body:     body,
		})
	}
	return res, nil
}

// docFrontmatter is the subset of YAML frontmatter promoted to provenance columns.
type docFrontmatter struct {
	SourceURL  string `json:"source_url"`
	Author     string `json:"author"`
	CapturedAt string `json:"captured_at"`
}

// splitFrontmatter extracts a leading `---\n...\n---\n` YAML block and returns
// the parsed provenance plus the remaining body. With no frontmatter, it returns
// a zero docFrontmatter and the original content unchanged. A malformed block is
// ignored (zero provenance) and the original content is returned.
func splitFrontmatter(content string) (docFrontmatter, string) {
	if !strings.HasPrefix(content, "---\n") {
		return docFrontmatter{}, content
	}
	rest := content[len("---\n"):]
	end := strings.Index(rest, "\n---\n")
	if end < 0 {
		return docFrontmatter{}, content
	}
	yamlBlock := rest[:end]
	body := rest[end+len("\n---\n"):]
	var fm docFrontmatter
	if err := sigsyaml.Unmarshal([]byte(yamlBlock), &fm); err != nil {
		return docFrontmatter{}, content
	}
	return fm, body
}
```

4. **Run to pass.**
```
go test ./internal/analyze/ -run 'TestDocsAnalyzer' -count=1
```
Expected PASS: all `TestDocsAnalyzer*` green (`ok  	.../internal/analyze`).

5. **Commit.**
```
git add internal/analyze/docs.go internal/analyze/docs_test.go internal/analyze/testdata/docs/front.md
git commit -m "feat(analyze): capture doc YAML frontmatter (source_url/author/captured_at) onto doc entity"
```

---

## Task 14: golang.go — promote line_start/line_end into typed Entity columns

**Files**
- Modify: `internal/analyze/golang.go`
- Modify: `internal/analyze/golang_test.go`

> Phase 0 item 3: Go already computes line numbers into `Properties`; promote them into the typed `LineStart`/`LineEnd` Entity columns (kept in `Properties` too, for back-compat with anything reading the string form).

**Steps**

1. **Write the failing test.** Append to `internal/analyze/golang_test.go` (it is `package analyze_test`; confirm imports include `context`, `testify/require`, `analyze`, `contract`):

```go
func TestGoEntityHasTypedLineColumns(t *testing.T) {
	a := analyze.NewGo("github.com/szymonrychu/")
	res, err := a.Analyze(context.Background(), "testdata/go", []string{"pkg/pkg.go"})
	require.NoError(t, err)

	var fn *contract.Entity
	for i := range res.Entities {
		if res.Entities[i].Type == contract.EntityGoFunc || res.Entities[i].Type == contract.EntityGoMethod {
			fn = &res.Entities[i]
			break
		}
	}
	require.NotNil(t, fn, "expected at least one func/method entity")
	require.Greater(t, fn.LineStart, 0, "line_start promoted to typed column")
	require.GreaterOrEqual(t, fn.LineEnd, fn.LineStart, "line_end promoted to typed column")
}
```

2. **Run it (expect FAIL).**
```
go test ./internal/analyze/ -run 'TestGoEntityHasTypedLineColumns' -count=1
```
Expected FAIL: `line_start promoted to typed column` (`fn.LineStart` is 0).

3. **Minimal implementation.** In `internal/analyze/golang.go` `processPackage`, replace the entity append (lines 196-209) with:

```go
			lineStart := pkg.Fset.Position(fd.Pos()).Line
			lineEnd := pkg.Fset.Position(fd.End()).Line
			props := map[string]string{
				"line_start": fmt.Sprintf("%d", lineStart),
				"line_end":   fmt.Sprintf("%d", lineEnd),
				"signature":  sig,
				"exported":   fmt.Sprintf("%v", ast.IsExported(fd.Name.Name)),
			}

			res.Entities = append(res.Entities, contract.Entity{
				ID:         entityID,
				Name:       name,
				Type:       entityType,
				FilePath:   rel,
				LineStart:  lineStart,
				LineEnd:    lineEnd,
				Properties: props,
			})
```

4. **Run to pass.**
```
go test ./internal/analyze/ -run 'TestGoEntityHasTypedLineColumns' -count=1
```
Expected PASS: `ok  	.../internal/analyze`.

5. **Commit.**
```
git add internal/analyze/golang.go internal/analyze/golang_test.go
git commit -m "feat(analyze): promote Go line_start/line_end into typed Entity columns"
```

---

## Task 15: golang.go — promote call-edge confidence into typed Edge columns

**Files**
- Modify: `internal/analyze/golang.go`
- Modify: `internal/analyze/golang_test.go`

> Phase 0 item 2: the Go analyzer already stamps `properties["confidence"]` from the resolution level. Promote that scalar into `Edge.ConfidenceScore` and derive `Edge.ConfidenceTier` via `contract.TierForScore`.

**Steps**

1. **Write the failing test.** Append to `internal/analyze/golang_test.go`:

```go
func TestGoCallEdgeHasTypedConfidence(t *testing.T) {
	a := analyze.NewGo("github.com/szymonrychu/")
	res, err := a.Analyze(context.Background(), "testdata/go", []string{"pkg/pkg.go", "pkg/other.go"})
	require.NoError(t, err)

	var call *contract.Edge
	for i := range res.Edges {
		if res.Edges[i].Relation == contract.RelCalls {
			call = &res.Edges[i]
			break
		}
	}
	require.NotNil(t, call, "expected at least one calls edge in the fixture")
	require.InDelta(t, 0.98, call.ConfidenceScore, 1e-9, "type_resolved -> 0.98 promoted to column")
	require.Equal(t, contract.TierInferred, call.ConfidenceTier, "0.98 maps to INFERRED")
}
```

2. **Run it (expect FAIL).**
```
go test ./internal/analyze/ -run 'TestGoCallEdgeHasTypedConfidence' -count=1
```
Expected FAIL: `type_resolved -> 0.98 promoted to column` (`call.ConfidenceScore` is 0). (If the fixture yields no `calls` edge with the given file subset, broaden the file list to the package's files; the `testdata/go/pkg` package contains an intra-package call.)

3. **Minimal implementation.** In `internal/analyze/golang.go` `emitCallEdges`, replace the edge append (lines 461-470) with:

```go
		score := scoreFor(contract.ResTypeResolved)
		res.Edges = append(res.Edges, contract.Edge{
			From:            callerID,
			To:              calleeID,
			Relation:        contract.RelCalls,
			SrcFile:         callerRelFile,
			ConfidenceScore: score,
			ConfidenceTier:  contract.TierForScore(score),
			Properties: map[string]string{
				"resolution": contract.ResTypeResolved,
				"confidence": contract.ConfidenceFor(contract.ResTypeResolved),
			},
		})
```

   Add this helper at the end of `internal/analyze/golang.go`:

```go
// scoreFor parses the confidence prior string for a resolution level into a float.
func scoreFor(resolution string) float64 {
	switch resolution {
	case contract.ResTypeResolved:
		return 0.98
	case contract.ResScopedNameMatch:
		return 0.85
	case contract.ResImportedNameMatch:
		return 0.7
	case contract.ResGlobalNameMatch:
		return 0.45
	case contract.ResAmbiguousMultiDef:
		return 0.2
	default:
		return 0.0
	}
}
```

4. **Run to pass.**
```
go test ./internal/analyze/ -run 'TestGoCallEdgeHasTypedConfidence' -count=1
```
Expected PASS: `ok  	.../internal/analyze`.

5. **Commit.**
```
git add internal/analyze/golang.go internal/analyze/golang_test.go
git commit -m "feat(analyze): promote Go call-edge confidence into typed Edge score/tier columns"
```

---

## Task 16: Full regression sweep, vet, and contract-shape lock confirmation

**Files**
- None (verification only). If `go vet` or a regression surfaces a defect, fix the minimal file and include it in the commit.

**Steps**

1. **Run the whole suite.**
```
go build ./... && go test ./... -count=1
```
Expected PASS: every package `ok`, including `internal/contract`, `internal/walk`, `internal/analyze`, `internal/push`, `cmd/tatara-ingest`. If `cmd/tatara-ingest/e2e_test.go` or `main_test.go` reference the old `walk.Changed` or two-arg `PushChunks`, update those call sites to the new APIs (`walk.Diff`, `PushChunks(ctx, reconcileFiles, items)`) — this is the boy-scout fix.

2. **Vet + tidy check (read-only verify).**
```
go vet ./... && go mod tidy && git diff --exit-code go.mod go.sum
```
Expected: `go vet` clean; `go.mod`/`go.sum` unchanged (`sigs.k8s.io/yaml` was already a direct dependency, so no new requires). If `git diff --exit-code` reports changes, stage them.

3. **Confirm the contract shape lock matches the spec byte-for-byte.** Re-read `internal/contract/contract.go` against `2026-06-09-phase0-contract-lock.md`:
   - `Edge`: `confidence_score`, `confidence_tier` both `,omitempty`. ✓
   - `Entity`: `line_start`,`line_end`,`source_url`,`author`,`captured_at` all `,omitempty`; `file_path` NOT omitempty. ✓
   - `Hyperedge`: `members` (no omitempty), `relation` non-omitempty; `GraphPush.Hyperedges` `,omitempty`. ✓
   - `BulkMemoriesRequest`: `reconcile_files,omitempty`, `items` non-omitempty. ✓
   Run the shape tests alone to prove the lock:
```
go test ./internal/contract/ -run 'JSONShape|Confidence|Provenance|Hyperedge|BulkMemories|TierForScore|SemanticRelation|DocEntityType' -v -count=1
```
Expected PASS for every named test.

4. **Commit any boy-scout fixes.**
```
git add -A
git commit -m "chore(ingest): align e2e/main tests with walk.Diff and PushChunks reconcile signature"
```
   (If step 1-3 needed no edits, skip the commit.)

5. **Final state confirmation.**
```
git log --oneline -16
go test ./... -count=1
```
Expected: 14-16 commits on the feature branch, full suite green.

---

## Execution ordering note (dependencies)

Run tasks in this order to avoid forward references:

1. Task 1 → 2 → 3 → 4 (contract types; no cross-package deps)
2. Task 9 (push `PushChunks` new signature)
3. Task 12 (`analyze.Result.Hyperedges`)
4. Task 8 (run.go wiring — needs Tasks 5+9+12)
5. Task 5 (walk `Diff`) must precede Task 8's build; run **Task 5 → 6 → 7 first**, then 9 → 12 → 8 → 10.
6. Task 11 → 13 (docs)
7. Task 14 → 15 (golang typed columns)
8. Task 16 (sweep)

Concretely: **1, 2, 3, 4, 5, 6, 7, 9, 12, 8, 10, 11, 13, 14, 15, 16.**

---

### Critical Files for Implementation
- /Users/szymonri/Documents/tatara/tatara-memory-repo-ingester/internal/contract/contract.go
- /Users/szymonri/Documents/tatara/tatara-memory-repo-ingester/internal/walk/walk.go
- /Users/szymonri/Documents/tatara/tatara-memory-repo-ingester/cmd/tatara-ingest/run.go
- /Users/szymonri/Documents/tatara/tatara-memory-repo-ingester/internal/push/push.go
- /Users/szymonri/Documents/tatara/tatara-memory-repo-ingester/internal/analyze/docs.go