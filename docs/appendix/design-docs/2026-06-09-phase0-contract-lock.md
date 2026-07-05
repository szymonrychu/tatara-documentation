# Phase 0 contract lock (shared wire types)

Date: 2026-06-09
Purpose: the exact widened wire types both tatara-memory (consumer) and
tatara-memory-repo-ingester (producer, `internal/contract/contract.go` mirror)
must match byte-for-byte. `contract_shape_test.go` guards drift. Locked before
the four implementation plans so they cannot diverge.

## Principle: DRY, fold where a thing already fits

- A **semantic edge** IS an `Edge` (it has a `src_file`, a relation, two entity
  endpoints). It does NOT get its own array. New semantic relations are added to
  the relation vocabulary; confidence is promoted to first-class fields on
  `Edge`. Reconcile already purges/inserts edges per `src_file`.
- A **doc / concept / rationale node** IS an `Entity` (new `Type` values). No
  separate `ConceptNodes` array. DocsAnalyzer emits them into `Entities`.
- A **hyperedge** is genuinely n-ary -> new type + new `GraphPush.Hyperedges`
  array.
- `reconcile_files` is a new field on the `/memories:bulk` request.

This supersedes the spec's wording that named `SemanticEdges`/`ConceptNodes`
arrays; the data flows through the existing `Edges`/`Entities` arrays instead.
Net new wire surface in Phase 0: confidence fields on `Edge`, provenance fields
on `Entity`, the `Hyperedge` type + `GraphPush.Hyperedges`, doc entity-type
constants, and `reconcile_files` on memories bulk.

## Edge (add two fields)

```go
type Edge struct {
	From            string            `json:"from"`
	To              string            `json:"to"`
	Relation        string            `json:"relation"`
	SrcFile         string            `json:"src_file"`
	ConfidenceScore float64           `json:"confidence_score,omitempty"` // NEW: 1.0 EXTRACTED, <1 INFERRED
	ConfidenceTier  string            `json:"confidence_tier,omitempty"`  // NEW: EXTRACTED|INFERRED|AMBIGUOUS
	Properties      map[string]string `json:"properties,omitempty"`
}
```

On reconcile the server writes `confidence_score`/`confidence_tier` into the new
typed columns (DEFAULT 1.0 / 'EXTRACTED' when the producer omits them). Call
analyzers populate them from the existing resolution level
(`contract.ConfidenceFor`): type_resolved -> 0.98/INFERRED, ... unresolved ->
0.0/AMBIGUOUS. Tier mapping: 1.0 -> EXTRACTED; (0,1) -> INFERRED; <=0.3 ->
AMBIGUOUS.

## Entity (add provenance fields + new types)

```go
type Entity struct {
	ID          string            `json:"id"`
	Name        string            `json:"name"`
	Type        string            `json:"type"`
	Description string            `json:"description,omitempty"`
	FilePath    string            `json:"file_path"`
	LineStart   int               `json:"line_start,omitempty"`   // NEW (Go already computes)
	LineEnd     int               `json:"line_end,omitempty"`     // NEW
	SourceURL   string            `json:"source_url,omitempty"`   // NEW (doc frontmatter)
	Author      string            `json:"author,omitempty"`       // NEW
	CapturedAt  string            `json:"captured_at,omitempty"`  // NEW (RFC3339)
	Properties  map[string]string `json:"properties,omitempty"`
}
```

New entity-type constants (both repos): `EntityDocFile = "doc_file"`,
`EntityDocSection = "doc_section"`, `EntityConcept = "concept"`,
`EntityRationale = "rationale"`. Analytics columns (community/cohesion/degree/
betweenness) are server-computed, NOT on the wire.

## Hyperedge (new) + GraphPush.Hyperedges

```go
type Hyperedge struct {
	ID              string            `json:"id"`
	Label           string            `json:"label"`
	Relation        string            `json:"relation"` // participate_in|implement|form
	ConfidenceScore float64           `json:"confidence_score,omitempty"`
	SrcFile         string            `json:"src_file"`
	Members         []string          `json:"members"` // entity IDs (3+)
	Properties      map[string]string `json:"properties,omitempty"`
}

type GraphPush struct {
	Repo       string      `json:"repo"`
	Commit     string      `json:"commit,omitempty"`
	Files      []string    `json:"files"`
	Entities   []Entity    `json:"entities"`
	Edges      []Edge      `json:"edges"`
	Symbols    []SymbolRow `json:"symbols,omitempty"`
	Hyperedges []Hyperedge `json:"hyperedges,omitempty"` // NEW (empty until Phase 2)
}
```

Reconcile purges `code_hyperedges` + `code_hyperedge_members` owned by `src_file`
in `Files`, then inserts `Hyperedges` (and members). Producer emits empty in
build unit 1; the path is exercised by tests with a non-empty fixture.

## New semantic relation constants (reserved now, emitted Phase 2)

Both repos add to the relation vocabulary (no analyzer emits them yet except
`implements` from docs if cheap): `RelConceptuallyRelated =
"conceptually_related_to"`, `RelSemanticallySimilar = "semantically_similar_to"`,
`RelRationaleFor = "rationale_for"`, `RelSharesDataWith = "shares_data_with"`,
`RelCites = "cites"`. Memory's traversal sets are unchanged; these are stored and
returned, available to the Phase 2 `code_related` tool.

## Phase 2 delta: GraphPush.Extractor + FileSHAs (origin-scoped reconcile)

`GraphPush` gains two fields. Reconcile scopes its per-`src_file` deletes by
`Extractor` so AST and semantic rows do not clobber each other.

```go
type GraphPush struct {
	Repo       string            `json:"repo"`
	Commit     string            `json:"commit,omitempty"`
	Extractor  string            `json:"extractor,omitempty"`  // NEW: ""/"ast" or "semantic"
	Files      []string          `json:"files"`
	Entities   []Entity          `json:"entities"`
	Edges      []Edge            `json:"edges"`
	Symbols    []SymbolRow       `json:"symbols,omitempty"`
	Hyperedges []Hyperedge       `json:"hyperedges,omitempty"`
	FileSHAs   map[string]string `json:"file_shas,omitempty"`  // NEW: path->content_sha; set on semantic push to update the cache
}
```

`Entity`, `Edge`, `Hyperedge` gain no new wire fields (the `extractor` is a
push-level attribute written onto every row of that push; the DB column defaults
to 'ast'). New entity types `concept`/`rationale` and the semantic relation
constants were already reserved in Phase 0. `semantic-misses` request:
`{repo, files:[{path, content_sha}]}` -> response `[]string` (paths to extract).

## /memories:bulk request (add reconcile_files)

```go
type BulkMemoriesRequest struct {
	ReconcileFiles []string     `json:"reconcile_files,omitempty"` // NEW
	Items          []IngestItem `json:"items"`
}
```

If the current handler accepts a bare `[]IngestItem`, change it to this object
and keep accepting a bare array for back-compat (decode into `Items` when the
body is a JSON array). When `ReconcileFiles` is set, the worker calls
`DeleteMemoriesBySource(repo, f)` for each `f` before inserting items. `repo` is
taken from the items' metadata (all items in one push share it) or a top-level
`repo` field if added; the plan resolves this against the current handler.

## namespacePath rule (operator ingest clone + wrapper bootstrap)

```go
// namespacePath maps a git clone URL to the on-disk subpath: owner[/subgroups]/repo,
// dropping scheme, host, userinfo, and a trailing ".git". Keeps the owner.
//   https://github.com/szymonrychu/tatara-cli.git   -> szymonrychu/tatara-cli
//   https://gitlab.com/szymonrychu/infra/helmfile    -> szymonrychu/infra/helmfile
//   git@github.com:szymonrychu/tatara-cli.git        -> szymonrychu/tatara-cli
//   ssh://git@host:22/group/sub/repo.git             -> group/sub/repo
func namespacePath(cloneURL string) string
```

Implemented independently (tiny) in tatara-operator and tatara-claude-code-wrapper
(separate Go modules; 3 similar lines beat a shared dep). The memory `repo` LABEL
is unchanged (logical identifier); only the clone DIRECTORY mirrors the namespace.
