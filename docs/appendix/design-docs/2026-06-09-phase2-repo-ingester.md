# Phase 2 Semantic Extraction (Ingester) Implementation Plan

## Overview

This plan adds LLM-based semantic extraction to `tatara-memory-repo-ingester`. After the existing AST push, the ingester asks memory which analyzed files have changed (`semantic-misses`), chunks those files, calls OpenAI `gpt-4o-mini` (JSON mode) with graphify's verbatim extraction-spec prompt, parses the JSON fragment into `contract` types, and pushes a second `GraphPush` tagged `Extractor:"semantic"` carrying `FileSHAs`. The whole stage is gated on `OPENAI_API_KEY` / `SEMANTIC_INGEST` and is strictly best-effort (LLM/parse failures WARN and skip; the AST ingest never fails because of semantics).

## Scope

- `internal/contract/contract.go`: add `GraphPush.Extractor` + `GraphPush.FileSHAs` (per the Phase 2 contract-lock delta); add the `semantic-misses` request/response types; extend `contract_shape_test.go`.
- `internal/llm`: new OpenAI chat/completions client (JSON mode, env-configured, 1 retry on 429/5xx), tested with an `httptest` stub.
- `internal/semantic`: chunker (group files within a token/size budget, ~8 files/chunk), prompt builder (substitutes the `go:embed`-baked extraction-spec template), JSON-fragment parser -> contract `Entities`/`Edges`/`Hyperedges` with deterministic concept ids and the confidence rubric.
- `cmd/tatara-ingest/run.go`: tag the AST push `Extractor:"ast"`; after it, call `POST /code-graph/semantic-misses`, run the semantic stage on misses with bounded concurrency (~4), push the semantic `GraphPush` with `FileSHAs`; skip the whole stage when `OPENAI_API_KEY` is unset or `SEMANTIC_INGEST=false`.

## Conventions (match the existing repo)

- Test framework: `github.com/stretchr/testify/require`. External I/O faked with `net/http/httptest`. No real API calls.
- Test command for the whole module: `CGO_ENABLED=1 go test ./... -race -count=1`. Per-package during TDD: `go test ./internal/<pkg>/... -race -count=1 -run <Name>`.
- JSON logs via `log/slog`; WARN for best-effort skips.
- Bounded concurrency via `golang.org/x/sync/errgroup` with `g.SetLimit(n)` (already an indirect dependency in `go.mod`).
- Conventional Commits for messages.

---

## Task 1: Add `Extractor` + `FileSHAs` to `GraphPush` and lock the shape

### Files
- Modify: `internal/contract/contract.go`
- Modify: `internal/contract/contract_shape_test.go`

### Steps

1. Write the failing tests. Append these functions to `internal/contract/contract_shape_test.go`:

```go
func TestGraphPushExtractorAndFileSHAsShape(t *testing.T) {
	p := contract.GraphPush{
		Repo:      "tatara-cli",
		Commit:    "abc123",
		Extractor: "semantic",
		Files:     []string{"cmd/root.go"},
		Entities:  []contract.Entity{},
		Edges:     []contract.Edge{},
		FileSHAs:  map[string]string{"cmd/root.go": "deadbeef"},
	}
	b, err := json.Marshal(p)
	require.NoError(t, err)
	var got map[string]any
	require.NoError(t, json.Unmarshal(b, &got))
	require.ElementsMatch(t,
		[]string{"repo", "commit", "extractor", "files", "entities", "edges", "file_shas"},
		keys(got))
	require.Equal(t, "semantic", got["extractor"])
	shas := got["file_shas"].(map[string]any)
	require.Equal(t, "deadbeef", shas["cmd/root.go"])
}

func TestGraphPushExtractorAndFileSHAsOmitEmpty(t *testing.T) {
	p := contract.GraphPush{Repo: "r", Files: []string{"a.go"}, Entities: []contract.Entity{}, Edges: []contract.Edge{}}
	b, err := json.Marshal(p)
	require.NoError(t, err)
	var got map[string]any
	require.NoError(t, json.Unmarshal(b, &got))
	_, hasExtractor := got["extractor"]
	_, hasSHAs := got["file_shas"]
	require.False(t, hasExtractor, "extractor must be absent when empty")
	require.False(t, hasSHAs, "file_shas must be absent when empty")
}

func TestSemanticMissesRequestShape(t *testing.T) {
	req := contract.SemanticMissesRequest{
		Repo: "r",
		Files: []contract.FileSHA{
			{Path: "a.go", ContentSHA: "sha1"},
			{Path: "b.go", ContentSHA: "sha2"},
		},
	}
	b, err := json.Marshal(req)
	require.NoError(t, err)
	var got map[string]any
	require.NoError(t, json.Unmarshal(b, &got))
	require.ElementsMatch(t, []string{"repo", "files"}, keys(got))
	first := got["files"].([]any)[0].(map[string]any)
	require.ElementsMatch(t, []string{"path", "content_sha"}, keys(first))
}
```

2. Run RED:

```
go test ./internal/contract/... -race -count=1 -run 'GraphPushExtractor|SemanticMisses'
```

Expected: compile failure -- `p.Extractor undefined`, `p.FileSHAs undefined`, `contract.SemanticMissesRequest undefined`, `contract.FileSHA undefined`.

3. Minimal impl. In `internal/contract/contract.go`, replace the `GraphPush` struct (the current one at the top of the file, the one WITHOUT `Extractor`/`FileSHAs` -- it is defined once) with this exact definition:

```go
// GraphPush is one /code-graph:bulk request. Extractor tags the origin of every
// row in this push ("" or "ast" for the AST extractor, "semantic" for the LLM
// stage); reconcile scopes its per-src_file deletes by it so the two origins do
// not clobber each other. FileSHAs (path->content_sha) is set on the semantic
// push to update the server's extraction cache.
type GraphPush struct {
	Repo       string            `json:"repo"`
	Commit     string            `json:"commit,omitempty"`
	Extractor  string            `json:"extractor,omitempty"`
	Files      []string          `json:"files"`
	Entities   []Entity          `json:"entities"`
	Edges      []Edge            `json:"edges"`
	Symbols    []SymbolRow       `json:"symbols,omitempty"`
	Hyperedges []Hyperedge       `json:"hyperedges,omitempty"`
	FileSHAs   map[string]string `json:"file_shas,omitempty"`
}
```

Then add these new types and the extractor constants, immediately after the `GraphPush` struct:

```go
// Extractor origin tags written onto every row of a GraphPush.
const (
	ExtractorAST      = "ast"
	ExtractorSemantic = "semantic"
)

// FileSHA pairs a repo-relative path with the sha256 of its working-tree content.
type FileSHA struct {
	Path       string `json:"path"`
	ContentSHA string `json:"content_sha"`
}

// SemanticMissesRequest is the POST /code-graph/semantic-misses body: the set of
// analyzed files with their current content_sha. The server returns the subset
// whose stored content_sha differs or is absent (cache miss -> needs extraction).
type SemanticMissesRequest struct {
	Repo  string    `json:"repo"`
	Files []FileSHA `json:"files"`
}
```

4. Run GREEN:

```
go test ./internal/contract/... -race -count=1
```

Expected: PASS (the new tests plus all existing shape tests, including `TestGraphPushJSONShape`, `TestGraphPushHyperedgesOmitEmpty`, `TestGraphPushSymbolsOmitEmpty`, which still pass because the new fields are `omitempty`).

5. Commit:

```
git add internal/contract/contract.go internal/contract/contract_shape_test.go
git commit -m "feat(contract): add GraphPush.Extractor + FileSHAs and semantic-misses types"
```

---

## Task 2: Embed graphify's extraction-spec prompt and build the prompt builder

### Files
- Create: `internal/semantic/extraction_spec.txt`
- Create: `internal/semantic/prompt.go`
- Create: `internal/semantic/prompt_test.go`

### Steps

1. Write the failing test. Create `internal/semantic/prompt_test.go`:

```go
package semantic

import (
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestPromptEmbedsExtractionSpecVerbatim(t *testing.T) {
	// Anchor lines from graphify's extraction-spec prompt block must be present verbatim.
	require.Contains(t, extractionSpec, "You are a graphify extraction subagent. Read the files listed and extract a knowledge graph fragment.")
	require.Contains(t, extractionSpec, "Output ONLY valid JSON matching the schema below - no explanation, no markdown fences, no preamble.")
	require.Contains(t, extractionSpec, "- EXTRACTED: relationship explicit in source (import, call, citation, \"see §3.2\")")
	require.Contains(t, extractionSpec, "Maximum 3 hyperedges per chunk.")
	require.Contains(t, extractionSpec, "confidence_score is REQUIRED on every edge - never omit it, never use 0.5 as a default:")
	require.Contains(t, extractionSpec, "\"nodes\":[{\"id\":\"session_validatetoken\"")
}

func TestBuildPromptSubstitutesPlaceholders(t *testing.T) {
	got := BuildPrompt(PromptVars{
		FileList:    "- a.go\n- b.go",
		ChunkNum:    2,
		TotalChunks: 5,
		ChunkPath:   "/tmp/chunk-2.json",
	})
	require.Contains(t, got, "Files (chunk 2 of 5):")
	require.Contains(t, got, "- a.go\n- b.go")
	require.Contains(t, got, "/tmp/chunk-2.json")
	// Placeholders must be fully consumed.
	require.NotContains(t, got, "FILE_LIST")
	require.NotContains(t, got, "CHUNK_NUM")
	require.NotContains(t, got, "TOTAL_CHUNKS")
	require.NotContains(t, got, "CHUNK_PATH")
	// DEEP_MODE is off: the literal token must not leak into the prompt.
	require.NotContains(t, got, "DEEP_MODE (if --mode deep was given)")
}

func TestBuildPromptKeepsSchemaIntact(t *testing.T) {
	got := BuildPrompt(PromptVars{FileList: "- a.go", ChunkNum: 1, TotalChunks: 1, ChunkPath: "/tmp/c.json"})
	require.Contains(t, got, "Generate the extraction JSON matching this schema exactly:")
	require.Contains(t, got, "\"semantically_similar_to\"")
}
```

2. Run RED:

```
go test ./internal/semantic/... -race -count=1 -run 'Prompt'
```

Expected: compile failure -- `extractionSpec undefined`, `BuildPrompt undefined`, `PromptVars undefined`, and (until the embed file exists) `pattern extraction_spec.txt: no matching files found`.

3. Minimal impl.

First create `internal/semantic/extraction_spec.txt` containing the prompt block exactly as it appears inside the triple-backtick fence in `/Users/szymonri/.claude/skills/graphify/references/extraction-spec.md` (lines 6-67, the text BETWEEN the opening ``` and the closing ```), with two edits: remove the `DEEP_MODE` paragraph (the two lines starting "DEEP_MODE (if --mode deep was given): be aggressive with INFERRED edges..." through "...AMBIGUOUS instead of omitting.") since DEEP_MODE is off, and leave `FILE_LIST`, `CHUNK_NUM`, `TOTAL_CHUNKS`, `CHUNK_PATH` as literal placeholder tokens. The file content is exactly:

```
You are a graphify extraction subagent. Read the files listed and extract a knowledge graph fragment.
Output ONLY valid JSON matching the schema below - no explanation, no markdown fences, no preamble.

Files (chunk CHUNK_NUM of TOTAL_CHUNKS):
FILE_LIST

Rules:
- EXTRACTED: relationship explicit in source (import, call, citation, "see §3.2")
- INFERRED: reasonable inference (shared data structure, implied dependency)
- AMBIGUOUS: uncertain - flag for review, do not omit

Code files: focus on semantic edges AST cannot find (call relationships, shared data, arch patterns).
  Do not re-extract imports - AST already has those.
Doc/paper files: extract named concepts, entities, citations. For rationale (WHY decisions were made, trade-offs, design intent): store as a `rationale` attribute on the relevant concept node — do NOT create a separate rationale node or fragment node. Only create a node for something that is itself a named entity or concept. Use `file_type:"rationale"` for concept-like nodes (ideas, principles, mechanisms, design patterns). `file_type` MUST be one of exactly these six values: `code`, `document`, `paper`, `image`, `rationale`, `concept`. Any other value is invalid and will be rejected.
Code files: when adding `calls` edges, source MUST be the caller (the function/class doing the calling), target MUST be the callee. Never reverse this direction. `calls` edges MUST stay within one language: a Python function cannot `calls` a JS/TS/Go/Rust/Java symbol and vice versa — cross-language call edges are phantom artifacts, never emit them.
Image files: use vision to understand what the image IS - do not just OCR.
  UI screenshot: layout patterns, design decisions, key elements, purpose.
  Chart: metric, trend/insight, data source.
  Tweet/post: claim as node, author, concepts mentioned.
  Diagram: components and connections.
  Research figure: what it demonstrates, method, result.
  Handwritten/whiteboard: ideas and arrows, mark uncertain readings AMBIGUOUS.

Semantic similarity: if two concepts in this chunk solve the same problem or represent the same idea without any structural link (no import, no call, no citation), add a `semantically_similar_to` edge marked INFERRED with a confidence_score reflecting how similar they are (0.6-0.95). Examples:
- Two functions that both validate user input but never call each other
- A class in code and a concept in a paper that describe the same algorithm
- Two error types that handle the same failure mode differently
Only add these when the similarity is genuinely non-obvious and cross-cutting. Do not add them for trivially similar things.

Hyperedges: if 3 or more nodes clearly participate together in a shared concept, flow, or pattern that is not captured by pairwise edges alone, add a hyperedge to a top-level `hyperedges` array. Examples:
- All classes that implement a common protocol or interface
- All functions in an authentication flow (even if they don't all call each other)
- All concepts from a paper section that form one coherent idea
Use sparingly — only when the group relationship adds information beyond the pairwise edges. Maximum 3 hyperedges per chunk.

If a file has YAML frontmatter (--- ... ---), copy source_url, captured_at, author,
  contributor onto every node from that file.

confidence_score is REQUIRED on every edge - never omit it, never use 0.5 as a default:
- EXTRACTED edges: confidence_score = 1.0 always
- INFERRED edges: pick exactly ONE value from this set — never 0.5:
    0.95  direct structural evidence (shared data structure, named cross-file reference).
    0.85  strong inference (clear functional alignment, no direct symbol link).
    0.75  reasonable inference (shared problem domain + similar shape, requires interpretation).
    0.65  weak inference (thematically related, no shape evidence).
    0.55  speculative but plausible (surface-level co-occurrence only).
  Models follow discrete rubrics better than continuous ranges; the bimodal
  distribution observed in production (>50% at 0.5, >40% at 0.85+) shows the
  range guidance is being collapsed to a binary. If no value above fits, mark
  the edge AMBIGUOUS rather than picking 0.4 or below.
- AMBIGUOUS edges: 0.1-0.3

Node ID format: lowercase, only `[a-z0-9_]`, no dots or slashes. Format: `{stem}_{entity}` where stem is `{parent_dir}_{filename_without_ext}` (the **immediate** parent directory name + the filename stem, both lowercased with non-alphanumeric chars replaced by `_`) and entity is the symbol name similarly normalized. Only one level of parent is used — not the full path. Examples: `src/auth/session.py` + `ValidateToken` → `auth_session_validatetoken`; `lib/utils/helpers.py` + `parse_url` → `utils_helpers_parse_url`; `tests/test_foo.py` + `_helper` → `tests_test_foo_helper`. Top-level files (no parent dir, e.g. `setup.py`) use just the filename stem: `setup_my_func`. This must match the ID the AST extractor generates — using just the filename (e.g., `session_validatetoken`) or the full path (e.g., `src_auth_session_validatetoken`) will create orphan ghost-duplicate nodes. If you are re-extracting a project that had ghost duplicates under the old format, the user should run `graphify extract --force` to rebuild cleanly. CRITICAL: never append chunk numbers, sequence numbers, or any suffix to an ID (no `_c1`, `_c2`, `_chunk2`, etc.). IDs must be deterministic from the label alone — the same entity must always produce the same ID regardless of which chunk processes it.

Generate the extraction JSON matching this schema exactly:
{"nodes":[{"id":"session_validatetoken","label":"Human Readable Name","file_type":"code|document|paper|image|rationale|concept","source_file":"relative/path","source_location":null,"source_url":null,"captured_at":null,"author":null,"contributor":null}],"edges":[{"source":"node_id","target":"node_id","relation":"calls|implements|references|cites|conceptually_related_to|shares_data_with|semantically_similar_to|rationale_for","confidence":"EXTRACTED|INFERRED|AMBIGUOUS","confidence_score":1.0,"source_file":"relative/path","source_location":null,"weight":1.0}],"hyperedges":[{"id":"snake_case_id","label":"Human Readable Label","nodes":["node_id1","node_id2","node_id3"],"relation":"participate_in|implement|form","confidence":"EXTRACTED|INFERRED","confidence_score":0.75,"source_file":"relative/path"}],"input_tokens":0,"output_tokens":0}

Then write the JSON to disk using the Write tool at this exact absolute path (no relative paths — Write resolves relative paths against an undefined cwd and the file will be silently lost):
CHUNK_PATH
```

Then create `internal/semantic/prompt.go`:

```go
// Package semantic runs the LLM extraction stage: it chunks analyzed files,
// builds the graphify extraction prompt, and maps the returned JSON fragment to
// contract graph types. It is best-effort: callers log and skip on any failure.
package semantic

import (
	_ "embed"
	"strconv"
	"strings"
)

//go:embed extraction_spec.txt
var extractionSpec string

// PromptVars are the placeholder substitutions for the extraction prompt.
type PromptVars struct {
	FileList    string
	ChunkNum    int
	TotalChunks int
	ChunkPath   string
}

// BuildPrompt returns the verbatim extraction-spec prompt with FILE_LIST,
// CHUNK_NUM, TOTAL_CHUNKS, and CHUNK_PATH substituted. DEEP_MODE is always off.
func BuildPrompt(v PromptVars) string {
	r := strings.NewReplacer(
		"CHUNK_NUM", strconv.Itoa(v.ChunkNum),
		"TOTAL_CHUNKS", strconv.Itoa(v.TotalChunks),
		"FILE_LIST", v.FileList,
		"CHUNK_PATH", v.ChunkPath,
	)
	return r.Replace(extractionSpec)
}
```

4. Run GREEN:

```
go test ./internal/semantic/... -race -count=1 -run 'Prompt'
```

Expected: PASS.

5. Commit:

```
git add internal/semantic/extraction_spec.txt internal/semantic/prompt.go internal/semantic/prompt_test.go
git commit -m "feat(semantic): embed graphify extraction-spec prompt and add prompt builder"
```

---

## Task 3: Build the file chunker

### Files
- Create: `internal/semantic/chunk.go`
- Create: `internal/semantic/chunk_test.go`

### Steps

1. Write the failing test. Create `internal/semantic/chunk_test.go`:

```go
package semantic

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestChunkGroupsByFileCount(t *testing.T) {
	files := make([]LoadedFile, 0, 20)
	for i := 0; i < 20; i++ {
		files = append(files, LoadedFile{Path: "f" + string(rune('a'+i)) + ".go", Content: "tiny"})
	}
	chunks := Chunk(files, ChunkBudget{MaxFiles: 8, MaxBytes: 1 << 20})
	require.Len(t, chunks, 3) // 8 + 8 + 4
	require.Len(t, chunks[0].Files, 8)
	require.Len(t, chunks[1].Files, 8)
	require.Len(t, chunks[2].Files, 4)
}

func TestChunkSplitsOnByteBudget(t *testing.T) {
	big := make([]byte, 600)
	for i := range big {
		big[i] = 'x'
	}
	files := []LoadedFile{
		{Path: "a.go", Content: string(big)},
		{Path: "b.go", Content: string(big)},
		{Path: "c.go", Content: string(big)},
	}
	// MaxBytes 1000 admits one 600-byte file per chunk (a second would exceed it).
	chunks := Chunk(files, ChunkBudget{MaxFiles: 8, MaxBytes: 1000})
	require.Len(t, chunks, 3)
	for _, c := range chunks {
		require.Len(t, c.Files, 1)
	}
}

func TestChunkOversizeFileGetsItsOwnChunk(t *testing.T) {
	big := make([]byte, 2000)
	files := []LoadedFile{
		{Path: "small.go", Content: "x"},
		{Path: "huge.go", Content: string(big)}, // exceeds MaxBytes alone
	}
	chunks := Chunk(files, ChunkBudget{MaxFiles: 8, MaxBytes: 1000})
	require.Len(t, chunks, 2)
	require.Equal(t, "small.go", chunks[0].Files[0].Path)
	require.Equal(t, "huge.go", chunks[1].Files[0].Path)
}

func TestChunkEmptyInputYieldsNoChunks(t *testing.T) {
	require.Empty(t, Chunk(nil, ChunkBudget{MaxFiles: 8, MaxBytes: 1000}))
}

func TestDefaultChunkBudget(t *testing.T) {
	b := DefaultChunkBudget()
	require.Equal(t, 8, b.MaxFiles)
	require.Greater(t, b.MaxBytes, 0)
}
```

2. Run RED:

```
go test ./internal/semantic/... -race -count=1 -run 'Chunk|DefaultChunkBudget'
```

Expected: compile failure -- `LoadedFile`, `Chunk`, `ChunkBudget`, `FileChunk`, `DefaultChunkBudget` undefined.

3. Minimal impl. Create `internal/semantic/chunk.go`:

```go
package semantic

// LoadedFile is one analyzed file with its content, ready to be chunked.
type LoadedFile struct {
	Path    string
	Content string
}

// FileChunk is a token/size-bounded group of files extracted together.
type FileChunk struct {
	Files []LoadedFile
}

// ChunkBudget bounds a single chunk: at most MaxFiles files and MaxBytes of
// content. A single file larger than MaxBytes still gets its own chunk.
type ChunkBudget struct {
	MaxFiles int
	MaxBytes int
}

// DefaultChunkBudget is ~8 files and ~48KB of content per chunk (a rough proxy
// for the gpt-4o-mini context budget; ~4 bytes/token).
func DefaultChunkBudget() ChunkBudget {
	return ChunkBudget{MaxFiles: 8, MaxBytes: 48 * 1024}
}

// Chunk groups files greedily, closing a chunk when adding the next file would
// exceed either bound. Order is preserved.
func Chunk(files []LoadedFile, b ChunkBudget) []FileChunk {
	if b.MaxFiles <= 0 {
		b.MaxFiles = 8
	}
	if b.MaxBytes <= 0 {
		b.MaxBytes = 48 * 1024
	}
	var chunks []FileChunk
	var cur []LoadedFile
	curBytes := 0
	flush := func() {
		if len(cur) > 0 {
			chunks = append(chunks, FileChunk{Files: cur})
			cur = nil
			curBytes = 0
		}
	}
	for _, f := range files {
		n := len(f.Content)
		if len(cur) > 0 && (len(cur) >= b.MaxFiles || curBytes+n > b.MaxBytes) {
			flush()
		}
		cur = append(cur, f)
		curBytes += n
	}
	flush()
	return chunks
}
```

4. Run GREEN:

```
go test ./internal/semantic/... -race -count=1 -run 'Chunk|DefaultChunkBudget'
```

Expected: PASS.

5. Commit:

```
git add internal/semantic/chunk.go internal/semantic/chunk_test.go
git commit -m "feat(semantic): add token/size-bounded file chunker"
```

---

## Task 4: Parse the LLM JSON fragment into contract types

### Files
- Create: `internal/semantic/parse.go`
- Create: `internal/semantic/parse_test.go`

### Steps

1. Write the failing test. Create `internal/semantic/parse_test.go`:

```go
package semantic

import (
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract"
)

const sampleFragment = `{
  "nodes": [
    {"id":"auth_session_validatetoken","label":"Validate Token","file_type":"code","source_file":"auth/session.go"},
    {"id":"retry_backoff","label":"Retry Backoff","file_type":"concept","source_file":"http/client.go"},
    {"id":"why_jitter","label":"Why Jitter","file_type":"rationale","source_file":"http/client.go"}
  ],
  "edges": [
    {"source":"auth_session_validatetoken","target":"retry_backoff","relation":"semantically_similar_to","confidence":"INFERRED","confidence_score":0.85,"source_file":"http/client.go","weight":1.0},
    {"source":"why_jitter","target":"retry_backoff","relation":"rationale_for","confidence":"EXTRACTED","confidence_score":1.0,"source_file":"http/client.go","weight":1.0},
    {"source":"a","target":"b","relation":"calls","confidence":"AMBIGUOUS","confidence_score":0.2,"source_file":"http/client.go"}
  ],
  "hyperedges": [
    {"id":"auth_flow","label":"Auth Flow","nodes":["auth_session_validatetoken","retry_backoff","why_jitter"],"relation":"participate_in","confidence":"INFERRED","confidence_score":0.75,"source_file":"auth/session.go"}
  ],
  "input_tokens": 0,
  "output_tokens": 0
}`

func TestParseFragmentConceptNodeIDs(t *testing.T) {
	res, err := ParseFragment("myrepo", []byte(sampleFragment))
	require.NoError(t, err)
	// code file_type nodes are NOT emitted as entities (they reference AST nodes).
	// concept/rationale file_type nodes ARE emitted with deterministic ids.
	byType := map[string]contract.Entity{}
	for _, e := range res.Entities {
		byType[e.Type] = e
	}
	require.Contains(t, byType, contract.EntityConcept)
	require.Contains(t, byType, contract.EntityRationale)
	require.Equal(t, "concept:myrepo:retry-backoff", byType[contract.EntityConcept].ID)
	require.Equal(t, "concept:myrepo:why-jitter", byType[contract.EntityRationale].ID)
	for _, e := range res.Entities {
		require.Equal(t, "http/client.go", e.FilePath)
	}
}

func TestParseFragmentEdgesMapToContract(t *testing.T) {
	res, err := ParseFragment("myrepo", []byte(sampleFragment))
	require.NoError(t, err)
	require.Len(t, res.Edges, 3)
	var sim contract.Edge
	for _, e := range res.Edges {
		if e.Relation == contract.RelSemanticallySimilar {
			sim = e
		}
	}
	require.Equal(t, contract.RelSemanticallySimilar, sim.Relation)
	require.InDelta(t, 0.85, sim.ConfidenceScore, 1e-9)
	require.Equal(t, contract.TierInferred, sim.ConfidenceTier)
	require.Equal(t, "http/client.go", sim.SrcFile)
}

func TestParseFragmentConfidenceTiers(t *testing.T) {
	res, err := ParseFragment("myrepo", []byte(sampleFragment))
	require.NoError(t, err)
	tiers := map[string]string{}
	for _, e := range res.Edges {
		tiers[e.Relation] = e.ConfidenceTier
	}
	require.Equal(t, contract.TierInferred, tiers[contract.RelSemanticallySimilar]) // 0.85
	require.Equal(t, contract.TierExtracted, tiers[contract.RelRationaleFor])       // 1.0
	require.Equal(t, contract.TierAmbiguous, tiers[contract.RelCalls])              // 0.2
}

func TestParseFragmentHyperedges(t *testing.T) {
	res, err := ParseFragment("myrepo", []byte(sampleFragment))
	require.NoError(t, err)
	require.Len(t, res.Hyperedges, 1)
	h := res.Hyperedges[0]
	require.Equal(t, "Auth Flow", h.Label)
	require.Equal(t, "participate_in", h.Relation)
	require.InDelta(t, 0.75, h.ConfidenceScore, 1e-9)
	require.Equal(t, "auth/session.go", h.SrcFile)
	require.Len(t, h.Members, 3)
	require.Equal(t, "he:myrepo:auth/session.go:auth_flow", h.ID)
}

func TestParseFragmentHyperedgesCappedAtThree(t *testing.T) {
	frag := `{"nodes":[],"edges":[],"hyperedges":[
	  {"id":"h1","label":"a","nodes":["x","y","z"],"relation":"form","confidence_score":0.7,"source_file":"f.go"},
	  {"id":"h2","label":"b","nodes":["x","y","z"],"relation":"form","confidence_score":0.7,"source_file":"f.go"},
	  {"id":"h3","label":"c","nodes":["x","y","z"],"relation":"form","confidence_score":0.7,"source_file":"f.go"},
	  {"id":"h4","label":"d","nodes":["x","y","z"],"relation":"form","confidence_score":0.7,"source_file":"f.go"}
	]}`
	res, err := ParseFragment("r", []byte(frag))
	require.NoError(t, err)
	require.Len(t, res.Hyperedges, 3)
}

func TestParseFragmentRejectsMalformedJSON(t *testing.T) {
	_, err := ParseFragment("r", []byte("not json"))
	require.Error(t, err)
}

func TestParseFragmentStripsCodeFences(t *testing.T) {
	wrapped := "```json\n" + sampleFragment + "\n```"
	res, err := ParseFragment("myrepo", []byte(wrapped))
	require.NoError(t, err)
	require.Len(t, res.Edges, 3)
}

func TestSlugLabel(t *testing.T) {
	require.Equal(t, "retry-backoff", slugLabel("Retry Backoff"))
	require.Equal(t, "why-jitter", slugLabel("Why Jitter!"))
	require.Equal(t, "a-b-c", slugLabel("a  b   c"))
	require.Equal(t, "leadingtrailing", slugLabel("  LeadingTrailing  "))
}
```

2. Run RED:

```
go test ./internal/semantic/... -race -count=1 -run 'ParseFragment|SlugLabel'
```

Expected: compile failure -- `ParseFragment`, `slugLabel` undefined.

3. Minimal impl. Create `internal/semantic/parse.go`:

```go
package semantic

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/analyze"
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract"
)

// rawFragment mirrors graphify's extraction JSON schema.
type rawFragment struct {
	Nodes []struct {
		ID         string `json:"id"`
		Label      string `json:"label"`
		FileType   string `json:"file_type"`
		SourceFile string `json:"source_file"`
		SourceURL  string `json:"source_url"`
		CapturedAt string `json:"captured_at"`
		Author     string `json:"author"`
	} `json:"nodes"`
	Edges []struct {
		Source          string  `json:"source"`
		Target          string  `json:"target"`
		Relation        string  `json:"relation"`
		Confidence      string  `json:"confidence"`
		ConfidenceScore float64 `json:"confidence_score"`
		SourceFile      string  `json:"source_file"`
	} `json:"edges"`
	Hyperedges []struct {
		ID              string   `json:"id"`
		Label           string   `json:"label"`
		Nodes           []string `json:"nodes"`
		Relation        string   `json:"relation"`
		ConfidenceScore float64  `json:"confidence_score"`
		SourceFile      string   `json:"source_file"`
	} `json:"hyperedges"`
}

// maxHyperedgesPerChunk matches the extraction-spec cap.
const maxHyperedgesPerChunk = 3

// ParseFragment maps a graphify extraction JSON fragment to contract graph
// types for one repo. Concept/rationale nodes become Entities with deterministic
// ids (concept:<repo>:<slug>); code/document/paper/image nodes reference AST
// entity ids and are not re-emitted. Edges carry the semantic relation, score,
// and tier. Hyperedges are capped at 3 with deterministic ids.
func ParseFragment(repo string, body []byte) (analyze.Result, error) {
	var f rawFragment
	if err := json.Unmarshal(stripFences(body), &f); err != nil {
		return analyze.Result{}, fmt.Errorf("parse extraction fragment: %w", err)
	}
	var res analyze.Result
	for _, n := range f.Nodes {
		typ := entityTypeFor(n.FileType)
		if typ == "" {
			continue // code/document/paper/image: references an AST node, not re-emitted
		}
		res.Entities = append(res.Entities, contract.Entity{
			ID:         conceptID(repo, n.Label),
			Name:       n.Label,
			Type:       typ,
			FilePath:   n.SourceFile,
			SourceURL:  n.SourceURL,
			Author:     n.Author,
			CapturedAt: n.CapturedAt,
		})
	}
	for _, e := range f.Edges {
		res.Edges = append(res.Edges, contract.Edge{
			From:            e.Source,
			To:              e.Target,
			Relation:        e.Relation,
			SrcFile:         e.SourceFile,
			ConfidenceScore: e.ConfidenceScore,
			ConfidenceTier:  contract.TierForScore(e.ConfidenceScore),
		})
	}
	for i, h := range f.Hyperedges {
		if i >= maxHyperedgesPerChunk {
			break
		}
		res.Hyperedges = append(res.Hyperedges, contract.Hyperedge{
			ID:              fmt.Sprintf("he:%s:%s:%s", repo, h.SourceFile, h.ID),
			Label:           h.Label,
			Relation:        h.Relation,
			ConfidenceScore: h.ConfidenceScore,
			SrcFile:         h.SourceFile,
			Members:         h.Nodes,
		})
	}
	return res, nil
}

// entityTypeFor maps a graphify file_type to a contract concept/rationale entity
// type, or "" for node types that reference an existing AST entity.
func entityTypeFor(fileType string) string {
	switch fileType {
	case "concept":
		return contract.EntityConcept
	case "rationale":
		return contract.EntityRationale
	default:
		return ""
	}
}

// conceptID is the deterministic id for a concept/rationale node: a slug of the
// label scoped to the repo. Re-extraction of the same label upserts, not dupes.
func conceptID(repo, label string) string {
	return "concept:" + repo + ":" + slugLabel(label)
}

// slugLabel lowercases a label and collapses runs of non-[a-z0-9] into single
// hyphens, trimming leading/trailing hyphens.
func slugLabel(label string) string {
	var b strings.Builder
	prevHyphen := false
	for _, r := range strings.ToLower(label) {
		switch {
		case (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9'):
			b.WriteRune(r)
			prevHyphen = false
		default:
			if !prevHyphen {
				b.WriteByte('-')
				prevHyphen = true
			}
		}
	}
	return strings.Trim(b.String(), "-")
}

// stripFences removes a leading ```json / ``` fence and a trailing ``` fence if
// the model wrapped its JSON despite instructions to the contrary.
func stripFences(body []byte) []byte {
	s := strings.TrimSpace(string(body))
	s = strings.TrimPrefix(s, "```json")
	s = strings.TrimPrefix(s, "```")
	s = strings.TrimSuffix(s, "```")
	return []byte(strings.TrimSpace(s))
}
```

4. Run GREEN:

```
go test ./internal/semantic/... -race -count=1
```

Expected: PASS (parse, prompt, and chunk tests).

5. Commit:

```
git add internal/semantic/parse.go internal/semantic/parse_test.go
git commit -m "feat(semantic): map extraction JSON fragment to contract graph types"
```

---

## Task 5: Build the OpenAI chat/completions client

### Files
- Create: `internal/llm/openai.go`
- Create: `internal/llm/openai_test.go`

### Steps

1. Write the failing test. Create `internal/llm/openai_test.go`:

```go
package llm

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestCompleteSendsJSONModeRequest(t *testing.T) {
	var gotBody map[string]any
	var gotAuth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "/chat/completions", r.URL.Path)
		gotAuth = r.Header.Get("Authorization")
		b, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(b, &gotBody)
		w.WriteHeader(200)
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"{\"ok\":true}"}}]}`))
	}))
	defer srv.Close()

	c := New(Config{APIKey: "sk-test", Model: "gpt-4o-mini", BaseURL: srv.URL}, http.DefaultClient)
	out, err := c.Complete(context.Background(), "do the thing")
	require.NoError(t, err)
	require.Equal(t, `{"ok":true}`, out)
	require.Equal(t, "Bearer sk-test", gotAuth)
	require.Equal(t, "gpt-4o-mini", gotBody["model"])
	rf := gotBody["response_format"].(map[string]any)
	require.Equal(t, "json_object", rf["type"])
	msgs := gotBody["messages"].([]any)
	require.GreaterOrEqual(t, len(msgs), 1)
	last := msgs[len(msgs)-1].(map[string]any)
	require.Equal(t, "user", last["role"])
	require.Equal(t, "do the thing", last["content"])
}

func TestCompleteRetriesOnce5xxThenSucceeds(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if atomic.AddInt32(&calls, 1) == 1 {
			w.WriteHeader(503)
			_, _ = w.Write([]byte(`{"error":"overloaded"}`))
			return
		}
		w.WriteHeader(200)
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"{\"ok\":1}"}}]}`))
	}))
	defer srv.Close()

	c := New(Config{APIKey: "k", Model: "m", BaseURL: srv.URL}, http.DefaultClient)
	out, err := c.Complete(context.Background(), "x")
	require.NoError(t, err)
	require.Equal(t, `{"ok":1}`, out)
	require.Equal(t, int32(2), atomic.LoadInt32(&calls))
}

func TestCompleteRetriesOnce429ThenFails(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		w.WriteHeader(429)
		_, _ = w.Write([]byte(`{"error":"rate limited"}`))
	}))
	defer srv.Close()

	c := New(Config{APIKey: "k", Model: "m", BaseURL: srv.URL}, http.DefaultClient)
	_, err := c.Complete(context.Background(), "x")
	require.Error(t, err)
	require.Equal(t, int32(2), atomic.LoadInt32(&calls), "one initial + one retry only")
}

func TestCompleteDoesNotRetryOn400(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		w.WriteHeader(400)
		_, _ = w.Write([]byte(`{"error":"bad request"}`))
	}))
	defer srv.Close()

	c := New(Config{APIKey: "k", Model: "m", BaseURL: srv.URL}, http.DefaultClient)
	_, err := c.Complete(context.Background(), "x")
	require.Error(t, err)
	require.Equal(t, int32(1), atomic.LoadInt32(&calls), "4xx (non-429) must not retry")
}

func TestConfigFromEnvDefaults(t *testing.T) {
	cfg := ConfigFromEnv(func(k string) string {
		switch k {
		case "OPENAI_API_KEY":
			return "sk-xyz"
		default:
			return ""
		}
	})
	require.Equal(t, "sk-xyz", cfg.APIKey)
	require.Equal(t, "gpt-4o-mini", cfg.Model)
	require.Equal(t, "https://api.openai.com/v1", cfg.BaseURL)
}

func TestConfigFromEnvOverrides(t *testing.T) {
	cfg := ConfigFromEnv(func(k string) string {
		switch k {
		case "OPENAI_API_KEY":
			return "sk-1"
		case "SEMANTIC_MODEL":
			return "gpt-4o"
		case "OPENAI_BASE_URL":
			return "http://localhost:1234/v1/"
		default:
			return ""
		}
	})
	require.Equal(t, "gpt-4o", cfg.Model)
	require.Equal(t, "http://localhost:1234/v1", cfg.BaseURL, "trailing slash trimmed")
}
```

2. Run RED:

```
go test ./internal/llm/... -race -count=1
```

Expected: compile failure -- `llm.New`, `llm.Config`, `llm.ConfigFromEnv`, `(*Client).Complete` undefined.

3. Minimal impl. Create `internal/llm/openai.go`:

```go
// Package llm is a minimal OpenAI chat/completions client used by the semantic
// extraction stage. It requests JSON mode and retries once on transient errors.
package llm

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// Config holds the OpenAI client configuration.
type Config struct {
	APIKey  string
	Model   string
	BaseURL string
}

// ConfigFromEnv reads OPENAI_API_KEY, SEMANTIC_MODEL (default gpt-4o-mini), and
// OPENAI_BASE_URL (default https://api.openai.com/v1, trailing slash trimmed).
func ConfigFromEnv(getenv func(string) string) Config {
	model := getenv("SEMANTIC_MODEL")
	if model == "" {
		model = "gpt-4o-mini"
	}
	base := getenv("OPENAI_BASE_URL")
	if base == "" {
		base = "https://api.openai.com/v1"
	}
	return Config{
		APIKey:  getenv("OPENAI_API_KEY"),
		Model:   model,
		BaseURL: strings.TrimRight(base, "/"),
	}
}

// Client posts chat/completions to an OpenAI-compatible endpoint.
type Client struct {
	cfg  Config
	http *http.Client
}

// New constructs an OpenAI client.
func New(cfg Config, hc *http.Client) *Client {
	return &Client{cfg: cfg, http: hc}
}

type chatRequest struct {
	Model          string            `json:"model"`
	Messages       []chatMessage     `json:"messages"`
	ResponseFormat map[string]string `json:"response_format"`
	Temperature    float64           `json:"temperature"`
}

type chatMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type chatResponse struct {
	Choices []struct {
		Message struct {
			Content string `json:"content"`
		} `json:"message"`
	} `json:"choices"`
}

// Complete sends a single user prompt in JSON mode and returns the message
// content. It retries once on 429/5xx with a short backoff.
func (c *Client) Complete(ctx context.Context, prompt string) (string, error) {
	reqBody := chatRequest{
		Model:          c.cfg.Model,
		Messages:       []chatMessage{{Role: "user", Content: prompt}},
		ResponseFormat: map[string]string{"type": "json_object"},
		Temperature:    0,
	}
	b, err := json.Marshal(reqBody)
	if err != nil {
		return "", fmt.Errorf("marshal chat request: %w", err)
	}

	var lastErr error
	for attempt := 0; attempt < 2; attempt++ {
		if attempt > 0 {
			select {
			case <-ctx.Done():
				return "", ctx.Err()
			case <-time.After(500 * time.Millisecond):
			}
		}
		content, retry, err := c.try(ctx, b)
		if err == nil {
			return content, nil
		}
		lastErr = err
		if !retry {
			return "", err
		}
	}
	return "", lastErr
}

// try performs one request. retry is true only for transient (429/5xx) failures.
func (c *Client) try(ctx context.Context, body []byte) (content string, retry bool, err error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.cfg.BaseURL+"/chat/completions", bytes.NewReader(body))
	if err != nil {
		return "", false, fmt.Errorf("request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.cfg.APIKey)

	resp, err := c.http.Do(req)
	if err != nil {
		return "", true, fmt.Errorf("call openai: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()

	if resp.StatusCode != http.StatusOK {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 2048))
		transient := resp.StatusCode == http.StatusTooManyRequests || resp.StatusCode >= 500
		return "", transient, fmt.Errorf("openai status %d: %s", resp.StatusCode, string(b))
	}

	var cr chatResponse
	if err := json.NewDecoder(resp.Body).Decode(&cr); err != nil {
		return "", false, fmt.Errorf("decode openai response: %w", err)
	}
	if len(cr.Choices) == 0 {
		return "", false, fmt.Errorf("openai response has no choices")
	}
	return cr.Choices[0].Message.Content, false, nil
}
```

4. Run GREEN:

```
go test ./internal/llm/... -race -count=1
```

Expected: PASS.

5. Commit:

```
git add internal/llm/openai.go internal/llm/openai_test.go
git commit -m "feat(llm): add OpenAI chat/completions client with JSON mode and retry"
```

---

## Task 6: Wire the semantic stage into run.go (extractor tags, misses, concurrency, second push)

### Files
- Modify: `cmd/tatara-ingest/run.go`
- Modify: `cmd/tatara-ingest/run_test.go`

### Steps

1. Write the failing tests. Append these to `cmd/tatara-ingest/run_test.go` (the `import` block already has `context`, `encoding/json`, `io`, `net/http`, `net/http/httptest`, `os`, `os/exec`, `path/filepath`, `strings`, `testing`, `require`, and the `contract` package; add `"sync/atomic"` to that import block):

```go
func TestRunTagsASTPushWithExtractor(t *testing.T) {
	dir := newGitRepo(t)
	require.NoError(t, os.WriteFile(filepath.Join(dir, "a.go"),
		[]byte("package m\n\nfunc A() {}\n"), 0o644))
	require.NoError(t, os.WriteFile(filepath.Join(dir, "go.mod"),
		[]byte("module example.com/m\n\ngo 1.25\n"), 0o644))
	commitAll(t, dir, "init")

	var astPush contract.GraphPush
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/code-graph:bulk":
			body, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(body, &astPush)
			w.WriteHeader(200)
			_, _ = w.Write([]byte(`{"repo":"m"}`))
		default:
			w.WriteHeader(202)
			_, _ = w.Write([]byte(`{"id":"j","status":"succeeded"}`))
		}
	}))
	defer srv.Close()

	// No OPENAI_API_KEY -> semantic stage skipped, AST push still tagged "ast".
	opts := options{repoRoot: dir, repoName: "m", baseURL: srv.URL, getenv: func(string) string { return "" }}
	require.NoError(t, run(context.Background(), opts, http.DefaultClient))
	require.Equal(t, contract.ExtractorAST, astPush.Extractor)
}

func TestRunSkipsSemanticStageWhenNoKey(t *testing.T) {
	dir := newGitRepo(t)
	require.NoError(t, os.WriteFile(filepath.Join(dir, "a.go"),
		[]byte("package m\n\nfunc A() {}\n"), 0o644))
	require.NoError(t, os.WriteFile(filepath.Join(dir, "go.mod"),
		[]byte("module example.com/m\n\ngo 1.25\n"), 0o644))
	commitAll(t, dir, "init")

	var missesCalled, semanticPush bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/code-graph/semantic-misses":
			missesCalled = true
			w.WriteHeader(200)
			_, _ = w.Write([]byte(`["a.go"]`))
		case "/code-graph:bulk":
			var p contract.GraphPush
			body, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(body, &p)
			if p.Extractor == contract.ExtractorSemantic {
				semanticPush = true
			}
			w.WriteHeader(200)
			_, _ = w.Write([]byte(`{"repo":"m"}`))
		default:
			w.WriteHeader(202)
			_, _ = w.Write([]byte(`{"id":"j","status":"succeeded"}`))
		}
	}))
	defer srv.Close()

	opts := options{repoRoot: dir, repoName: "m", baseURL: srv.URL, getenv: func(string) string { return "" }}
	require.NoError(t, run(context.Background(), opts, http.DefaultClient))
	require.False(t, missesCalled, "semantic-misses must not be called without a key")
	require.False(t, semanticPush, "no semantic push without a key")
}

func TestRunSkipsSemanticStageWhenDisabled(t *testing.T) {
	dir := newGitRepo(t)
	require.NoError(t, os.WriteFile(filepath.Join(dir, "a.go"),
		[]byte("package m\n\nfunc A() {}\n"), 0o644))
	require.NoError(t, os.WriteFile(filepath.Join(dir, "go.mod"),
		[]byte("module example.com/m\n\ngo 1.25\n"), 0o644))
	commitAll(t, dir, "init")

	var missesCalled bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/code-graph/semantic-misses":
			missesCalled = true
			w.WriteHeader(200)
			_, _ = w.Write([]byte(`["a.go"]`))
		case "/code-graph:bulk":
			w.WriteHeader(200)
			_, _ = w.Write([]byte(`{"repo":"m"}`))
		default:
			w.WriteHeader(202)
			_, _ = w.Write([]byte(`{"id":"j","status":"succeeded"}`))
		}
	}))
	defer srv.Close()

	env := map[string]string{"OPENAI_API_KEY": "sk-test", "SEMANTIC_INGEST": "false"}
	opts := options{repoRoot: dir, repoName: "m", baseURL: srv.URL, getenv: func(k string) string { return env[k] }}
	require.NoError(t, run(context.Background(), opts, http.DefaultClient))
	require.False(t, missesCalled, "SEMANTIC_INGEST=false must skip the whole stage")
}

func TestRunSemanticStagePushesSecondGraphWithSHAs(t *testing.T) {
	dir := newGitRepo(t)
	require.NoError(t, os.WriteFile(filepath.Join(dir, "a.go"),
		[]byte("package m\n\nfunc A() {}\n"), 0o644))
	require.NoError(t, os.WriteFile(filepath.Join(dir, "go.mod"),
		[]byte("module example.com/m\n\ngo 1.25\n"), 0o644))
	commitAll(t, dir, "init")

	// Fake OpenAI endpoint returns a valid fragment with one concept + one edge.
	openai := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "/chat/completions", r.URL.Path)
		w.WriteHeader(200)
		frag := `{"nodes":[{"id":"misc_idea","label":"Misc Idea","file_type":"concept","source_file":"a.go"}],` +
			`"edges":[{"source":"go:func:example.com/m.A","target":"concept:m:misc-idea","relation":"conceptually_related_to","confidence":"INFERRED","confidence_score":0.75,"source_file":"a.go"}],` +
			`"hyperedges":[]}`
		out := map[string]any{"choices": []map[string]any{{"message": map[string]any{"content": frag}}}}
		_ = json.NewEncoder(w).Encode(out)
	}))
	defer openai.Close()

	var semanticPush contract.GraphPush
	var sawSemantic atomic.Bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/code-graph/semantic-misses":
			var req contract.SemanticMissesRequest
			body, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(body, &req)
			require.Equal(t, "m", req.Repo)
			w.WriteHeader(200)
			_, _ = w.Write([]byte(`["a.go"]`))
		case "/code-graph:bulk":
			var p contract.GraphPush
			body, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(body, &p)
			if p.Extractor == contract.ExtractorSemantic {
				semanticPush = p
				sawSemantic.Store(true)
			}
			w.WriteHeader(200)
			_, _ = w.Write([]byte(`{"repo":"m"}`))
		default:
			w.WriteHeader(202)
			_, _ = w.Write([]byte(`{"id":"j","status":"succeeded"}`))
		}
	}))
	defer srv.Close()

	env := map[string]string{"OPENAI_API_KEY": "sk-test", "OPENAI_BASE_URL": openai.URL}
	opts := options{repoRoot: dir, repoName: "m", baseURL: srv.URL, getenv: func(k string) string { return env[k] }}
	require.NoError(t, run(context.Background(), opts, http.DefaultClient))

	require.True(t, sawSemantic.Load(), "expected a semantic GraphPush")
	require.Equal(t, contract.ExtractorSemantic, semanticPush.Extractor)
	require.Contains(t, semanticPush.Files, "a.go")
	require.NotEmpty(t, semanticPush.FileSHAs["a.go"], "semantic push must carry content_sha for the miss")
	require.NotEmpty(t, semanticPush.Edges, "semantic edge must be present")
	require.Equal(t, contract.RelConceptuallyRelated, semanticPush.Edges[0].Relation)
}

func TestRunSemanticStageBestEffortOnLLMError(t *testing.T) {
	dir := newGitRepo(t)
	require.NoError(t, os.WriteFile(filepath.Join(dir, "a.go"),
		[]byte("package m\n\nfunc A() {}\n"), 0o644))
	require.NoError(t, os.WriteFile(filepath.Join(dir, "go.mod"),
		[]byte("module example.com/m\n\ngo 1.25\n"), 0o644))
	commitAll(t, dir, "init")

	// OpenAI always 500s (after the one retry it stays failed).
	openai := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(500)
		_, _ = w.Write([]byte(`{"error":"boom"}`))
	}))
	defer openai.Close()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/code-graph/semantic-misses":
			w.WriteHeader(200)
			_, _ = w.Write([]byte(`["a.go"]`))
		case "/code-graph:bulk":
			w.WriteHeader(200)
			_, _ = w.Write([]byte(`{"repo":"m"}`))
		default:
			w.WriteHeader(202)
			_, _ = w.Write([]byte(`{"id":"j","status":"succeeded"}`))
		}
	}))
	defer srv.Close()

	env := map[string]string{"OPENAI_API_KEY": "sk-test", "OPENAI_BASE_URL": openai.URL}
	opts := options{repoRoot: dir, repoName: "m", baseURL: srv.URL, getenv: func(k string) string { return env[k] }}
	// LLM failure must NOT fail the ingest.
	require.NoError(t, run(context.Background(), opts, http.DefaultClient))
}

// newGitRepo creates an initialized git repo in a temp dir.
func newGitRepo(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	for _, a := range [][]string{{"init", "-q"}, {"config", "user.email", "t@t"}, {"config", "user.name", "t"}} {
		c := exec.Command("git", a...)
		c.Dir = dir
		require.NoError(t, c.Run())
	}
	return dir
}
```

2. Run RED:

```
go test ./cmd/tatara-ingest/... -race -count=1 -run 'Semantic|TagsASTPush|SkipsSemantic'
```

Expected: compile failure -- `options` has no field `getenv`; then logical failures (`Extractor` not set on the AST push, no semantic push).

3. Minimal impl. In `cmd/tatara-ingest/run.go`:

a. Add the `getenv` field to the `options` struct (add the field after `scipRepo`):

```go
type options struct {
	repoRoot        string
	repoName        string
	since           string
	full            bool
	baseURL         string
	pollInterval    time.Duration
	crossRepoPrefix string
	scipPath        string
	scipRepo        string
	getenv          func(string) string
}
```

b. Update the imports block at the top of `run.go` to add the new packages:

```go
import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"golang.org/x/sync/errgroup"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/analyze"
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract"
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/llm"
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/push"
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/scip"
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/semantic"
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/walk"
)
```

c. Tag the AST push. In `run`, change the `cl.PushGraph` call that pushes the AST graph to set `Extractor`. Replace the block:

```go
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
```

with:

```go
	commit := headCommit(o.repoRoot)
	cl := push.New(o.baseURL, hc, pollOr(o.pollInterval))
	if _, err := cl.PushGraph(ctx, contract.GraphPush{
		Repo: o.repoName, Commit: commit, Extractor: contract.ExtractorAST, Files: touched,
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

	// Best-effort semantic stage: errors are logged and never fail the ingest.
	runSemantic(ctx, o, cl, commit, changes)
```

d. Add the semantic-stage functions at the end of `run.go`:

```go
// shaFor returns the content_sha for an A/M/R analyzed change in the diff.
func shaFor(changes walk.Changes, path string) string {
	for _, ch := range changes.Files {
		if ch.Path == path {
			return ch.ContentSHA
		}
	}
	return ""
}

// runSemantic is the best-effort LLM extraction stage. It is a no-op when the
// OpenAI key is unset or SEMANTIC_INGEST=false. Any failure (misses call, LLM,
// parse, push) is logged and swallowed so it never fails the AST ingest.
func runSemantic(ctx context.Context, o options, cl *push.Client, commit string, changes walk.Changes) {
	getenv := o.getenv
	if getenv == nil {
		getenv = os.Getenv
	}
	if getenv("OPENAI_API_KEY") == "" {
		return
	}
	if strings.EqualFold(getenv("SEMANTIC_INGEST"), "false") {
		return
	}

	// Candidate files: analyzed (A/M/R) changes that have a content_sha.
	var req contract.SemanticMissesRequest
	req.Repo = o.repoName
	for _, ch := range changes.Files {
		switch ch.Status {
		case 'A', 'M', 'R':
			if ch.ContentSHA != "" {
				req.Files = append(req.Files, contract.FileSHA{Path: ch.Path, ContentSHA: ch.ContentSHA})
			}
		}
	}
	if len(req.Files) == 0 {
		return
	}

	misses, err := cl.SemanticMisses(ctx, req)
	if err != nil {
		slog.Warn("semantic-misses failed; skipping semantic stage", "repo", o.repoName, "error", err)
		return
	}
	if len(misses) == 0 {
		return
	}

	// Load miss-file contents and chunk them.
	var loaded []semantic.LoadedFile
	fileSHAs := map[string]string{}
	for _, p := range misses {
		b, err := os.ReadFile(filepath.Join(o.repoRoot, p)) //nolint:gosec
		if err != nil {
			slog.Warn("semantic: unreadable miss file; skipping", "file", p, "error", err)
			continue
		}
		loaded = append(loaded, semantic.LoadedFile{Path: p, Content: string(b)})
		fileSHAs[p] = shaFor(changes, p)
	}
	if len(loaded) == 0 {
		return
	}
	chunks := semantic.Chunk(loaded, semantic.DefaultChunkBudget())

	client := llm.New(llm.ConfigFromEnv(getenv), cl.HTTP())
	results := make([]analyze.Result, len(chunks))
	g, gctx := errgroup.WithContext(ctx)
	g.SetLimit(4)
	for i, ck := range chunks {
		i, ck := i, ck
		g.Go(func() error {
			res, ok := extractChunk(gctx, o.repoName, client, ck, i+1, len(chunks))
			if ok {
				results[i] = res
			}
			return nil // best-effort: never propagate a chunk error
		})
	}
	_ = g.Wait()

	var agg analyze.Result
	for _, r := range results {
		agg.Entities = append(agg.Entities, r.Entities...)
		agg.Edges = append(agg.Edges, r.Edges...)
		agg.Hyperedges = append(agg.Hyperedges, r.Hyperedges...)
	}
	if len(agg.Entities) == 0 && len(agg.Edges) == 0 && len(agg.Hyperedges) == 0 {
		return
	}

	if _, err := cl.PushGraph(ctx, contract.GraphPush{
		Repo: o.repoName, Commit: commit, Extractor: contract.ExtractorSemantic,
		Files:      misses,
		Entities:   agg.Entities,
		Edges:      agg.Edges,
		Hyperedges: agg.Hyperedges,
		FileSHAs:   fileSHAs,
	}); err != nil {
		slog.Warn("semantic graph push failed", "repo", o.repoName, "error", err)
		return
	}
	slog.Info("semantic stage complete",
		"repo", o.repoName, "misses", len(misses), "chunks", len(chunks),
		"entities", len(agg.Entities), "edges", len(agg.Edges), "hyperedges", len(agg.Hyperedges))
}

// extractChunk runs one chunk through the LLM and parser. ok is false on any
// failure (logged WARN), so the caller drops that chunk's contribution.
func extractChunk(ctx context.Context, repo string, client *llm.Client, ck semantic.FileChunk, chunkNum, total int) (analyze.Result, bool) {
	var fl strings.Builder
	for _, f := range ck.Files {
		fl.WriteString("- ")
		fl.WriteString(f.Path)
		fl.WriteString("\n")
	}
	prompt := semantic.BuildPrompt(semantic.PromptVars{
		FileList:    strings.TrimRight(fl.String(), "\n"),
		ChunkNum:    chunkNum,
		TotalChunks: total,
		ChunkPath:   "/dev/null",
	})
	out, err := client.Complete(ctx, prompt)
	if err != nil {
		slog.Warn("semantic LLM call failed; skipping chunk", "repo", repo, "chunk", chunkNum, "error", err)
		return analyze.Result{}, false
	}
	res, err := semantic.ParseFragment(repo, []byte(out))
	if err != nil {
		slog.Warn("semantic parse failed; skipping chunk", "repo", repo, "chunk", chunkNum, "error", err)
		return analyze.Result{}, false
	}
	return res, true
}
```

4. Run GREEN:

```
go test ./cmd/tatara-ingest/... -race -count=1 -run 'Semantic|TagsASTPush|SkipsSemantic'
```

Expected: still RED on `cl.SemanticMisses` and `cl.HTTP` undefined -- those are added in Task 7. Continue to Task 7 before re-running. (If you prefer a self-contained GREEN per task, implement Task 7 first; the ordering here keeps run.go and the push client commits separate.)

5. Commit (after Task 7 makes it compile -- see Task 7 step 5). The run.go changes are committed together with the push-client additions in Task 7.

---

## Task 7: Add `SemanticMisses` and an HTTP accessor to the push client

### Files
- Modify: `internal/push/push.go`
- Modify: `internal/push/push_test.go`

### Steps

1. Write the failing test. Append to `internal/push/push_test.go`:

```go
func TestSemanticMissesReturnsMissPaths(t *testing.T) {
	var gotReq contract.SemanticMissesRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "/code-graph/semantic-misses", r.URL.Path)
		require.NoError(t, json.NewDecoder(r.Body).Decode(&gotReq))
		w.WriteHeader(200)
		_ = json.NewEncoder(w).Encode([]string{"a.go", "c.go"})
	}))
	defer srv.Close()
	c := push.New(srv.URL, http.DefaultClient, time.Millisecond)
	misses, err := c.SemanticMisses(context.Background(), contract.SemanticMissesRequest{
		Repo: "r",
		Files: []contract.FileSHA{
			{Path: "a.go", ContentSHA: "s1"},
			{Path: "b.go", ContentSHA: "s2"},
			{Path: "c.go", ContentSHA: "s3"},
		},
	})
	require.NoError(t, err)
	require.ElementsMatch(t, []string{"a.go", "c.go"}, misses)
	require.Equal(t, "r", gotReq.Repo)
	require.Len(t, gotReq.Files, 3)
}

func TestSemanticMissesPropagatesError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(500)
		_, _ = w.Write([]byte(`{"error":"boom"}`))
	}))
	defer srv.Close()
	c := push.New(srv.URL, http.DefaultClient, time.Millisecond)
	_, err := c.SemanticMisses(context.Background(), contract.SemanticMissesRequest{Repo: "r"})
	require.Error(t, err)
}

func TestClientHTTPAccessor(t *testing.T) {
	hc := &http.Client{}
	c := push.New("http://x", hc, time.Millisecond)
	require.Same(t, hc, c.HTTP())
}
```

2. Run RED:

```
go test ./internal/push/... -race -count=1 -run 'SemanticMisses|HTTPAccessor'
```

Expected: compile failure -- `(*Client).SemanticMisses` and `(*Client).HTTP` undefined.

3. Minimal impl. In `internal/push/push.go`, add these methods after `PushChunks`:

```go
// SemanticMisses asks the server which of the supplied files need semantic
// re-extraction (stored content_sha differs or is absent) and returns their paths.
func (c *Client) SemanticMisses(ctx context.Context, req contract.SemanticMissesRequest) ([]string, error) {
	var misses []string
	if err := c.do(ctx, http.MethodPost, "/code-graph/semantic-misses", req, http.StatusOK, &misses); err != nil {
		return nil, err
	}
	return misses, nil
}

// HTTP exposes the underlying HTTP client so callers (e.g. the LLM stage) reuse
// the same authenticated transport.
func (c *Client) HTTP() *http.Client { return c.http }
```

4. Run GREEN (push package, then the run package that depends on it):

```
go test ./internal/push/... -race -count=1
go test ./cmd/tatara-ingest/... -race -count=1
```

Expected: both PASS. The run-package tests from Task 6 (`TestRunTagsASTPushWithExtractor`, `TestRunSkipsSemanticStageWhenNoKey`, `TestRunSkipsSemanticStageWhenDisabled`, `TestRunSemanticStagePushesSecondGraphWithSHAs`, `TestRunSemanticStageBestEffortOnLLMError`) now pass, and the existing run tests (`TestRunReconcileFilesMatchTouchedSet`, etc.) still pass because the AST push only gained the `Extractor:"ast"` field and the semantic stage is a no-op when `getenv` returns empty.

5. Commit (push client + run.go wiring together, since they compile as a unit):

```
git add internal/push/push.go internal/push/push_test.go cmd/tatara-ingest/run.go cmd/tatara-ingest/run_test.go
git commit -m "feat(ingest): run best-effort semantic extraction stage after AST push"
```

---

## Task 8: Tidy modules and run the full suite

### Files
- Modify: `go.mod` (promote `golang.org/x/sync` from indirect to direct)
- Modify: `go.sum` (unchanged hashes; `go mod tidy` reconciles)

### Steps

1. Promote the dependency and verify embeds resolve:

```
go mod tidy
```

Expected: `golang.org/x/sync` moves out of the `// indirect` block (it is now imported directly by `cmd/tatara-ingest/run.go`). No new modules are downloaded; `golang.org/x/sync v0.20.0` is already in `go.sum`.

2. Run the full module suite (the project's canonical command):

```
CGO_ENABLED=1 go test ./... -race -count=1
```

Expected: PASS across `internal/contract`, `internal/llm`, `internal/semantic`, `internal/push`, `cmd/tatara-ingest`, and all pre-existing packages.

3. Run the linter the way CI does:

```
golangci-lint run ./... || [ $? -eq 5 ]
```

Expected: clean (exit 0, or 5 which the Makefile tolerates).

4. Build to confirm the embed is baked into the binary:

```
CGO_ENABLED=1 go build ./...
```

Expected: success; `internal/semantic/extraction_spec.txt` is embedded.

5. Commit:

```
git add go.mod go.sum
git commit -m "chore: promote golang.org/x/sync to a direct dependency"
```

---

## Critical Files for Implementation
- /Users/szymonri/Documents/tatara/tatara-memory-repo-ingester/internal/contract/contract.go
- /Users/szymonri/Documents/tatara/tatara-memory-repo-ingester/cmd/tatara-ingest/run.go
- /Users/szymonri/Documents/tatara/tatara-memory-repo-ingester/internal/push/push.go
- /Users/szymonri/Documents/tatara/tatara-memory-repo-ingester/internal/semantic/parse.go (new) plus /Users/szymonri/Documents/tatara/tatara-memory-repo-ingester/internal/semantic/extraction_spec.txt (new, verbatim prompt)
- /Users/szymonri/Documents/tatara/tatara-memory-repo-ingester/internal/llm/openai.go (new)