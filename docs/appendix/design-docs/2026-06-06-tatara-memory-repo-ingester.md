# tatara-memory-repo-ingester Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Go batch tool that walks a repository, runs best-per-language static analysis (Go, Python, JavaScript, Terraform, Helm, docs), and pushes a deterministic code graph plus enriched semantic chunks to a running tatara-memory.

**Architecture:** A stateless one-shot CLI. `walk` finds changed files and groups them by `Analyzer`; each `Analyzer` emits `contract.Entity`/`Edge`/`Chunk`; `push` sends the graph synchronously to `/code-graph:bulk` and the chunks to `/memories:bulk` (polling the job to terminal). Adding a language touches only one new file in `internal/analyze` (the hard modularity contract).

**Tech Stack:** Go 1.25, `golang.org/x/tools/go/packages` + `go/types` (Go), `github.com/smacker/go-tree-sitter` (Python/JS, CGo), `github.com/hashicorp/hcl/v2` + `terraform-config-inspect` (Terraform), `text/template` + `sigs.k8s.io/yaml` (Helm), `golang.org/x/oauth2/clientcredentials` (auth), `log/slog` (JSON logs).

**Spec:** `docs/superpowers/specs/2026-06-06-tatara-memory-repo-ingester-design.md`

**Reference repo (mirror its patterns):** `~/Documents/tatara/tatara-memory`.

**Contract facts (frozen, from tatara-memory `df1a137`):**
- `POST {base}/code-graph:bulk` - body is a bare `GraphPush` JSON, returns `200` + `PushResult`.
- `POST {base}/memories:bulk` - body `{"items":[IngestItem...]}`, returns `202` + `IngestJob`.
- `GET {base}/ingest-jobs/{id}` - returns `200` + `IngestJob`. Terminal `status`: `succeeded`, `failed`, `partial`.
- Routes are at the service root (no `/v1` prefix); `{base}` is whatever the deployment exposes (in-cluster: the service root).

---

## Pre-flight: worktree

This repo does not exist yet. Task 1 creates it. All later tasks run inside the new repo. After Task 1, if doing isolated development use `superpowers:using-git-worktrees` to create `.worktrees/feat-ingester-mvp` off the new repo's `main`; otherwise work on a `feat/ingester-mvp` branch. Build/deploy only from `main` after merge (rule 10).

---

### Task 1: Scaffold the repository

**Files:**
- Create: `go.mod`, `CLAUDE.md`, `Makefile`, `.golangci.yml`, `.gitignore`, `Dockerfile`, `README.md`, `MEMORY.md`, `ROADMAP.md`, `LICENSE`, `internal/version/version.go`, `cmd/tatara-ingest/main.go`

- [ ] **Step 1: Create the GitHub repo and clone it into place**

```bash
cd ~/Documents/tatara
gh repo create szymonrychu/tatara-memory-repo-ingester --private --description "Repo ingester: static analysis -> tatara-memory code graph + chunks"
git clone git@github.com:szymonrychu/tatara-memory-repo-ingester.git
cd tatara-memory-repo-ingester
```

- [ ] **Step 2: Initialise the Go module and copy invariant files**

```bash
go mod init github.com/szymonrychu/tatara-memory-repo-ingester
cp ~/Documents/tatara/tatara-memory/CLAUDE.md ./CLAUDE.md
cp ~/Documents/tatara/tatara-memory/LICENSE ./LICENSE
cp ~/Documents/tatara/tatara-memory/.golangci.yml ./.golangci.yml
```

Edit `go.mod` so the directive line reads exactly `go 1.25.0`.
Edit `.golangci.yml`: change the `goimports.local-prefixes` value to `github.com/szymonrychu/tatara-memory-repo-ingester`.

- [ ] **Step 3: Write `.gitignore`**

```
/bin/
/.worktrees/
*.test
coverage.out
```

- [ ] **Step 4: Write `internal/version/version.go`**

```go
// Package version holds build-time version information populated via ldflags.
package version

// Version is the semantic version string, set at build time.
var Version = "dev"

// Commit is the git commit SHA, set at build time.
var Commit = "unknown"

// Date is the build timestamp in RFC3339 format, set at build time.
var Date = "unknown"
```

- [ ] **Step 5: Write `Makefile`** (mirrors tatara-memory but `CGO_ENABLED=1` and ingester names)

```makefile
SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c

REGISTRY ?= harbor.szymonrichert.pl
IMAGE_NAME ?= containers/tatara-memory-repo-ingester
VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
COMMIT ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
DATE ?= $(shell date -u +%Y-%m-%dT%H:%M:%SZ)
IMAGE_REF := $(REGISTRY)/$(IMAGE_NAME):$(VERSION)

MODULE := github.com/szymonrychu/tatara-memory-repo-ingester

.PHONY: all lint test build image tidy fmt clean
all: lint test build

tidy:
	go mod tidy

fmt:
	gofmt -s -w .
	goimports -w -local $(MODULE) .

lint:
	golangci-lint run ./... || [ $$? -eq 5 ]

test:
	CGO_ENABLED=1 go test ./... -race -count=1

build:
	CGO_ENABLED=1 go build -trimpath \
		-ldflags "-s -w \
		  -X $(MODULE)/internal/version.Version=$(VERSION) \
		  -X $(MODULE)/internal/version.Commit=$(COMMIT) \
		  -X $(MODULE)/internal/version.Date=$(DATE)" \
		-o bin/tatara-ingest ./cmd/tatara-ingest

image:
	docker buildx build --platform=linux/amd64 \
		--build-arg VERSION=$(VERSION) --build-arg COMMIT=$(COMMIT) --build-arg DATE=$(DATE) \
		-t $(IMAGE_REF) --load .

clean:
	rm -rf bin
```

- [ ] **Step 6: Write a placeholder `cmd/tatara-ingest/main.go` so the module builds**

```go
// Command tatara-ingest walks a repository and pushes its code graph and
// semantic chunks to tatara-memory.
package main

import "fmt"

func main() {
	fmt.Println("tatara-ingest")
}
```

- [ ] **Step 7: Write stub `README.md`, `MEMORY.md`, `ROADMAP.md`**

`MEMORY.md`:
```markdown
# MEMORY.md - tatara-memory-repo-ingester

- 2026-06-06: New repo (phase 3 sub-project B). Walks a repo, emits the code
  graph + semantic chunks to tatara-memory. Spec in the parent tatara repo at
  docs/superpowers/specs/2026-06-06-tatara-memory-repo-ingester-design.md.
- 2026-06-06: No /metrics endpoint (rule 13). Batch tool with no long-running
  process to scrape; counts are emitted as a structured slog line. Rationale per
  rule 4.
- 2026-06-06: Stateless change detection. Caller supplies --since <commit>;
  querying tatara-memory for "last commit per repo" would couple B to A's
  internal state, which A does not expose.
```

`ROADMAP.md`:
```markdown
# ROADMAP.md - tatara-memory-repo-ingester

- M2 cross-repo linking (emit provides/requires; needs A-side cross_repo_symbols
  table + join). Fast-follow, spans A and B.
- M5 SCIP interchange analyzer (Java/C++/TS via off-the-shelf indexers).
- tree-sitter fallback for non-buildable Go packages.
- Prometheus Pushgateway emitter for batch counts.
```

`README.md`: one paragraph plus the `tatara-ingest --repo-root <path>` usage line.

- [ ] **Step 8: Verify it builds, then commit**

```bash
go build ./... && go vet ./...
git add -A && git commit -m "chore: scaffold tatara-memory-repo-ingester"
```

Expected: build succeeds, no vet errors.

---

### Task 2: `contract` package (wire types + constants + drift guard)

**Files:**
- Create: `internal/contract/contract.go`
- Test: `internal/contract/contract_shape_test.go`

- [ ] **Step 1: Write the failing shape test**

```go
package contract_test

import (
	"encoding/json"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract"
)

func TestGraphPushJSONShape(t *testing.T) {
	p := contract.GraphPush{
		Repo:   "tatara-cli",
		Commit: "abc123",
		Files:  []string{"cmd/root.go"},
		Entities: []contract.Entity{{
			ID: "go:func:m.F", Name: "F", Type: contract.EntityGoFunc,
			FilePath: "cmd/root.go", Properties: map[string]string{"resolution": contract.ResTypeResolved},
		}},
		Edges: []contract.Edge{{
			From: "go:func:m.F", To: "go:func:m.G", Relation: contract.RelCalls,
			SrcFile: "cmd/root.go", Properties: map[string]string{"confidence": "0.98"},
		}},
	}
	b, err := json.Marshal(p)
	require.NoError(t, err)
	var got map[string]any
	require.NoError(t, json.Unmarshal(b, &got))
	require.ElementsMatch(t, []string{"repo", "commit", "files", "entities", "edges"}, keys(got))
	ent := got["entities"].([]any)[0].(map[string]any)
	require.ElementsMatch(t, []string{"id", "name", "type", "file_path", "properties"}, keys(ent))
	edge := got["edges"].([]any)[0].(map[string]any)
	require.ElementsMatch(t, []string{"from", "to", "relation", "src_file", "properties"}, keys(edge))
}

func TestIngestItemJSONShape(t *testing.T) {
	it := contract.IngestItem{IdempotencyKey: "k", Text: "t", Metadata: map[string]string{"repo": "r"}}
	b, err := json.Marshal(it)
	require.NoError(t, err)
	var got map[string]any
	require.NoError(t, json.Unmarshal(b, &got))
	require.ElementsMatch(t, []string{"idempotency_key", "text", "metadata"}, keys(got))
}

func keys(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
```

- [ ] **Step 2: Run it to confirm it fails to compile**

Run: `go test ./internal/contract/...`
Expected: FAIL - `undefined: contract.GraphPush`.

- [ ] **Step 3: Write `internal/contract/contract.go`**

```go
// Package contract holds the wire types and vocabulary shared with
// tatara-memory's codegraph and memory APIs. These types mirror the server's
// JSON shapes byte-for-byte; contract_shape_test.go guards against drift.
package contract

// Entity is a node in the code graph.
type Entity struct {
	ID          string            `json:"id"`
	Name        string            `json:"name"`
	Type        string            `json:"type"`
	Description string            `json:"description,omitempty"`
	FilePath    string            `json:"file_path"`
	Properties  map[string]string `json:"properties,omitempty"`
}

// Edge is a directed, typed relationship between two entities.
type Edge struct {
	From       string            `json:"from"`
	To         string            `json:"to"`
	Relation   string            `json:"relation"`
	SrcFile    string            `json:"src_file"`
	Properties map[string]string `json:"properties,omitempty"`
}

// GraphPush is one /code-graph:bulk request.
type GraphPush struct {
	Repo     string   `json:"repo"`
	Commit   string   `json:"commit,omitempty"`
	Files    []string `json:"files"`
	Entities []Entity `json:"entities"`
	Edges    []Edge   `json:"edges"`
}

// PushResult is the /code-graph:bulk response.
type PushResult struct {
	Repo             string `json:"repo"`
	Files            int    `json:"files"`
	EntitiesUpserted int    `json:"entities_upserted"`
	EdgesUpserted    int    `json:"edges_upserted"`
}

// IngestItem is one /memories:bulk item.
type IngestItem struct {
	IdempotencyKey string            `json:"idempotency_key"`
	Text           string            `json:"text"`
	Metadata       map[string]string `json:"metadata,omitempty"`
}

// IngestJob is the /memories:bulk and /ingest-jobs/{id} response.
type IngestJob struct {
	ID     string `json:"id"`
	Status string `json:"status"`
	Total  int    `json:"total"`
	Done   int    `json:"done"`
	Failed int    `json:"failed"`
	Errors []struct {
		IdempotencyKey string `json:"idempotency_key"`
		Error          string `json:"error"`
	} `json:"errors,omitempty"`
}

// Terminal reports whether a job status is final.
func (j IngestJob) Terminal() bool {
	switch j.Status {
	case JobSucceeded, JobFailed, JobPartial:
		return true
	}
	return false
}

// Chunk is an analyzer's semantic-chunk output (assembled into an IngestItem by push).
type Chunk struct {
	EntityID string
	Type     string
	FilePath string
	Language string
	Header   string
	Body     string
}

// Job status values.
const (
	JobSucceeded = "succeeded"
	JobFailed    = "failed"
	JobPartial   = "partial"
)

// Entity types.
const (
	EntityRepo        = "repo"
	EntityFile        = "file"
	EntityGoPackage   = "go_package"
	EntityGoType      = "go_type"
	EntityGoFunc      = "go_func"
	EntityGoMethod    = "go_method"
	EntityPyModule    = "py_module"
	EntityPyClass     = "py_class"
	EntityPyFunc      = "py_func"
	EntityJSModule    = "js_module"
	EntityJSClass     = "js_class"
	EntityJSFunc      = "js_func"
	EntityTFResource  = "tf_resource"
	EntityTFData      = "tf_data"
	EntityTFModule    = "tf_module"
	EntityTFVariable  = "tf_variable"
	EntityTFOutput    = "tf_output"
	EntityHelmChart   = "helm_chart"
	EntityHelmTemplate = "helm_template"
	EntityHelmValue   = "helm_value"
)

// Edge relations.
const (
	RelContains     = "contains"
	RelDefines      = "defines"
	RelImports      = "imports"
	RelCalls        = "calls"
	RelReferences   = "references"
	RelImplements   = "implements"
	RelDependsOn    = "depends_on"
	RelModuleSource = "module_source"
	RelVarRef       = "var_ref"
	RelOutputRef    = "output_ref"
	RelValueRef     = "value_ref"
	RelIncludes     = "includes"
	RelSubchart     = "subchart"
)

// M3 call-edge resolution levels (property key "resolution").
const (
	ResTypeResolved      = "type_resolved"
	ResScopedNameMatch   = "scoped_name_match"
	ResImportedNameMatch = "imported_name_match"
	ResGlobalNameMatch   = "global_name_match"
	ResAmbiguousMultiDef = "ambiguous_multi_def"
	ResUnresolved        = "unresolved"
)

// ConfidenceFor returns the prior confidence string for a resolution level.
func ConfidenceFor(resolution string) string {
	switch resolution {
	case ResTypeResolved:
		return "0.98"
	case ResScopedNameMatch:
		return "0.85"
	case ResImportedNameMatch:
		return "0.7"
	case ResGlobalNameMatch:
		return "0.45"
	case ResAmbiguousMultiDef:
		return "0.2"
	default:
		return "0.0"
	}
}
```

- [ ] **Step 4: Run the test (PASS), lint, commit**

Run: `go test ./internal/contract/... && golangci-lint run ./internal/contract/...`
Expected: PASS, no lint findings.

```bash
git add internal/contract && git commit -m "feat(contract): wire types, vocabulary, M3 confidence priors"
```

---

### Task 3: `config` package

**Files:**
- Create: `internal/config/config.go`
- Test: `internal/config/config_test.go`

- [ ] **Step 1: Write the failing test**

```go
package config_test

import (
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/config"
)

func TestLoadFromEnv(t *testing.T) {
	env := map[string]string{
		"base-url":          "https://memory.example/",
		"oidc-issuer":       "https://auth.example/realms/master",
		"oidc-client-id":    "ingester",
		"oidc-client-secret": "s3cret",
		"oidc-audience":     "tatara-memory",
		"poll-interval":     "2s",
		"http-timeout":      "30s",
	}
	c, err := config.Load(func(k string) string { return env[k] })
	require.NoError(t, err)
	require.Equal(t, "https://memory.example", c.BaseURL) // trailing slash trimmed
	require.Equal(t, "ingester", c.OIDCClientID)
	require.Equal(t, 2*time.Second, c.PollInterval)
	require.Equal(t, 30*time.Second, c.HTTPTimeout)
}

func TestLoadDefaults(t *testing.T) {
	c, err := config.Load(func(string) string { return "" })
	require.NoError(t, err)
	require.Equal(t, 2*time.Second, c.PollInterval)
	require.Equal(t, 60*time.Second, c.HTTPTimeout)
}
```

- [ ] **Step 2: Run to confirm failure**

Run: `go test ./internal/config/...`
Expected: FAIL - `undefined: config.Load`.

- [ ] **Step 3: Write `internal/config/config.go`**

Keys are kebab-case to match the ConfigMap/Secret keys (rule 6); in the container they arrive as env vars via `envFrom`, read here through a `getenv` function injected for testability. (The deployment maps kebab keys to env; Go reads them by the same kebab string via the injected lookup, which in `main` wraps `os.Getenv` after replacing `-` with `_` and upper-casing - see Task 15.)

```go
// Package config loads ingester configuration from the environment.
package config

import (
	"fmt"
	"strings"
	"time"
)

// Config holds runtime configuration for the ingester.
type Config struct {
	BaseURL          string
	OIDCIssuer       string
	OIDCClientID     string
	OIDCClientSecret string
	OIDCAudience     string
	PollInterval     time.Duration
	HTTPTimeout      time.Duration
}

// Load builds a Config from a key lookup function (kebab-case keys).
func Load(getenv func(string) string) (Config, error) {
	c := Config{
		BaseURL:          strings.TrimRight(getenv("base-url"), "/"),
		OIDCIssuer:       getenv("oidc-issuer"),
		OIDCClientID:     getenv("oidc-client-id"),
		OIDCClientSecret: getenv("oidc-client-secret"),
		OIDCAudience:     getenv("oidc-audience"),
		PollInterval:     2 * time.Second,
		HTTPTimeout:      60 * time.Second,
	}
	if v := getenv("poll-interval"); v != "" {
		d, err := time.ParseDuration(v)
		if err != nil {
			return Config{}, fmt.Errorf("parse poll-interval: %w", err)
		}
		c.PollInterval = d
	}
	if v := getenv("http-timeout"); v != "" {
		d, err := time.ParseDuration(v)
		if err != nil {
			return Config{}, fmt.Errorf("parse http-timeout: %w", err)
		}
		c.HTTPTimeout = d
	}
	return c, nil
}
```

- [ ] **Step 4: Run (PASS), commit**

Run: `go test ./internal/config/...`

```bash
git add internal/config && git commit -m "feat(config): env-based configuration"
```

---

### Task 4: `analyze` interface + registry

**Files:**
- Create: `internal/analyze/analyzer.go`
- Test: `internal/analyze/analyzer_test.go`

- [ ] **Step 1: Write the failing test**

```go
package analyze_test

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/analyze"
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract"
)

type fakeAnalyzer struct {
	name  string
	match func(string) bool
}

func (f fakeAnalyzer) Name() string         { return f.name }
func (f fakeAnalyzer) Match(p string) bool  { return f.match(p) }
func (f fakeAnalyzer) Analyze(_ context.Context, _ string, files []string) (analyze.Result, error) {
	return analyze.Result{Entities: []contract.Entity{{ID: f.name, Name: f.name}}}, nil
}

func TestRegistryGroupsByFirstMatch(t *testing.T) {
	reg := analyze.NewRegistry()
	reg.Register(fakeAnalyzer{name: "go", match: func(p string) bool { return p == "a.go" }})
	reg.Register(fakeAnalyzer{name: "docs", match: func(string) bool { return true }}) // catch-all, lower precedence

	groups := reg.Group([]string{"a.go", "README.md"})
	require.Equal(t, []string{"a.go"}, groups["go"])
	require.Equal(t, []string{"README.md"}, groups["docs"])
}

func TestRegistryUnmatchedFileDropped(t *testing.T) {
	reg := analyze.NewRegistry()
	reg.Register(fakeAnalyzer{name: "go", match: func(p string) bool { return p == "a.go" }})
	groups := reg.Group([]string{"a.go", "weird.xyz"})
	require.Equal(t, map[string][]string{"go": {"a.go"}}, groups)
}
```

- [ ] **Step 2: Run to confirm failure**

Run: `go test ./internal/analyze/...`
Expected: FAIL - `undefined: analyze.NewRegistry`.

- [ ] **Step 3: Write `internal/analyze/analyzer.go`**

```go
// Package analyze defines the language-neutral analyzer contract and registry.
// Adding a language means adding one file implementing Analyzer and registering
// it; nothing else in the ingester changes.
package analyze

import (
	"context"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract"
)

// Result is what an Analyzer emits for its assigned file set.
type Result struct {
	Entities []contract.Entity
	Edges    []contract.Edge
	Chunks   []contract.Chunk
}

// Analyzer extracts a code graph and chunks for one language/file class.
type Analyzer interface {
	Name() string
	Match(path string) bool
	Analyze(ctx context.Context, repoRoot string, files []string) (Result, error)
}

// Registry is an ordered set of analyzers; earlier registration wins on Match.
type Registry struct {
	analyzers []Analyzer
}

// NewRegistry returns an empty registry.
func NewRegistry() *Registry { return &Registry{} }

// Register appends an analyzer (precedence = registration order).
func (r *Registry) Register(a Analyzer) { r.analyzers = append(r.analyzers, a) }

// Analyzers returns the registered analyzers in order.
func (r *Registry) Analyzers() []Analyzer { return r.analyzers }

// Group assigns each file to the first analyzer whose Match returns true.
// Files matched by no analyzer are dropped.
func (r *Registry) Group(files []string) map[string][]string {
	groups := map[string][]string{}
	for _, f := range files {
		for _, a := range r.analyzers {
			if a.Match(f) {
				groups[a.Name()] = append(groups[a.Name()], f)
				break
			}
		}
	}
	return groups
}
```

- [ ] **Step 4: Run (PASS), commit**

```bash
git add internal/analyze && git commit -m "feat(analyze): Analyzer interface and registry"
```

---

### Task 5: `walk` + change detection

**Files:**
- Create: `internal/walk/walk.go`
- Test: `internal/walk/walk_test.go`

- [ ] **Step 1: Write the failing test** (uses a real temp git repo)

```go
package walk_test

import (
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

func TestFullWalkListsTrackedFiles(t *testing.T) {
	dir := gitRepo(t)
	write(t, dir, "a.go", "package a")
	write(t, dir, "sub/b.py", "x = 1")
	commit(t, dir, "init")
	files, err := walk.Changed(dir, "", false)
	require.NoError(t, err)
	require.ElementsMatch(t, []string{"a.go", "sub/b.py"}, files)
}

func TestSinceWalkListsOnlyChanged(t *testing.T) {
	dir := gitRepo(t)
	write(t, dir, "a.go", "package a")
	base := commit(t, dir, "init")
	write(t, dir, "c.go", "package c")
	commit(t, dir, "add c")
	files, err := walk.Changed(dir, base, false)
	require.NoError(t, err)
	require.Equal(t, []string{"c.go"}, files)
}

func TestFullFlagOverridesSince(t *testing.T) {
	dir := gitRepo(t)
	write(t, dir, "a.go", "package a")
	base := commit(t, dir, "init")
	write(t, dir, "c.go", "package c")
	commit(t, dir, "add c")
	files, err := walk.Changed(dir, base, true)
	require.NoError(t, err)
	require.ElementsMatch(t, []string{"a.go", "c.go"}, files)
}
```

- [ ] **Step 2: Run to confirm failure**

Run: `go test ./internal/walk/...`
Expected: FAIL - `undefined: walk.Changed`.

- [ ] **Step 3: Write `internal/walk/walk.go`**

```go
// Package walk lists the repository files to ingest.
package walk

import (
	"fmt"
	"os/exec"
	"sort"
	"strings"
)

// Changed returns repo-relative paths to ingest. With full or an empty since, it
// lists all tracked files; otherwise it diffs since..HEAD.
func Changed(repoRoot, since string, full bool) ([]string, error) {
	var args []string
	if full || since == "" {
		args = []string{"-C", repoRoot, "ls-files"}
	} else {
		args = []string{"-C", repoRoot, "diff", "--name-only", since + "..HEAD"}
	}
	out, err := exec.Command("git", args...).Output()
	if err != nil {
		return nil, fmt.Errorf("git %s: %w", strings.Join(args, " "), err)
	}
	var files []string
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if line != "" {
			files = append(files, line)
		}
	}
	sort.Strings(files)
	return files, nil
}
```

- [ ] **Step 4: Run (PASS), commit**

```bash
git add internal/walk && git commit -m "feat(walk): git-based change detection"
```

---

### Task 6: `push` client - graph push, chunk bulk, job poll

**Files:**
- Create: `internal/push/push.go`
- Test: `internal/push/push_test.go`

- [ ] **Step 1: Write the failing test** (httptest stubs all three endpoints, including a partial job)

```go
package push_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract"
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/push"
)

func TestPushGraph(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		require.Equal(t, "/code-graph:bulk", r.URL.Path)
		var p contract.GraphPush
		require.NoError(t, json.NewDecoder(r.Body).Decode(&p))
		require.Equal(t, "tatara-cli", p.Repo)
		w.WriteHeader(200)
		_ = json.NewEncoder(w).Encode(contract.PushResult{Repo: p.Repo, EntitiesUpserted: len(p.Entities)})
	}))
	defer srv.Close()
	c := push.New(srv.URL, http.DefaultClient, time.Millisecond)
	res, err := c.PushGraph(context.Background(), contract.GraphPush{Repo: "tatara-cli", Entities: []contract.Entity{{ID: "x"}}})
	require.NoError(t, err)
	require.Equal(t, 1, res.EntitiesUpserted)
}

func TestPushChunksPollsToTerminal(t *testing.T) {
	var polls int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/memories:bulk":
			var req struct{ Items []contract.IngestItem `json:"items"` }
			require.NoError(t, json.NewDecoder(r.Body).Decode(&req))
			require.Len(t, req.Items, 1)
			w.WriteHeader(202)
			_ = json.NewEncoder(w).Encode(contract.IngestJob{ID: "j1", Status: "running"})
		case strings.HasPrefix(r.URL.Path, "/ingest-jobs/"):
			polls++
			st := "running"
			if polls >= 2 {
				st = contract.JobSucceeded
			}
			_ = json.NewEncoder(w).Encode(contract.IngestJob{ID: "j1", Status: st, Total: 1, Done: 1})
		}
	}))
	defer srv.Close()
	c := push.New(srv.URL, http.DefaultClient, time.Millisecond)
	err := c.PushChunks(context.Background(), []contract.IngestItem{{IdempotencyKey: "k", Text: "t"}})
	require.NoError(t, err)
	require.GreaterOrEqual(t, polls, 2)
}

func TestPushChunksPartialIsError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/memories:bulk" {
			w.WriteHeader(202)
			_ = json.NewEncoder(w).Encode(contract.IngestJob{ID: "j1", Status: "running"})
			return
		}
		_ = json.NewEncoder(w).Encode(contract.IngestJob{ID: "j1", Status: contract.JobPartial, Failed: 1})
	}))
	defer srv.Close()
	c := push.New(srv.URL, http.DefaultClient, time.Millisecond)
	err := c.PushChunks(context.Background(), []contract.IngestItem{{IdempotencyKey: "k", Text: "t"}})
	require.Error(t, err)
}
```

- [ ] **Step 2: Run to confirm failure**

Run: `go test ./internal/push/...`
Expected: FAIL - `undefined: push.New`.

- [ ] **Step 3: Write `internal/push/push.go`**

```go
// Package push sends the code graph and semantic chunks to tatara-memory.
package push

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract"
)

// Client posts to a tatara-memory base URL.
type Client struct {
	base         string
	http         *http.Client
	pollInterval time.Duration
}

// New constructs a push client.
func New(base string, hc *http.Client, pollInterval time.Duration) *Client {
	return &Client{base: base, http: hc, pollInterval: pollInterval}
}

// PushGraph posts a GraphPush synchronously and returns the reconciliation summary.
func (c *Client) PushGraph(ctx context.Context, p contract.GraphPush) (contract.PushResult, error) {
	var res contract.PushResult
	if err := c.do(ctx, http.MethodPost, "/code-graph:bulk", p, http.StatusOK, &res); err != nil {
		return contract.PushResult{}, err
	}
	return res, nil
}

// PushChunks posts chunks and polls the resulting job to a terminal state.
func (c *Client) PushChunks(ctx context.Context, items []contract.IngestItem) error {
	if len(items) == 0 {
		return nil
	}
	var job contract.IngestJob
	body := struct {
		Items []contract.IngestItem `json:"items"`
	}{Items: items}
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

func (c *Client) do(ctx context.Context, method, path string, in any, want int, out any) error {
	var rdr io.Reader
	if in != nil {
		b, err := json.Marshal(in)
		if err != nil {
			return fmt.Errorf("marshal %s: %w", path, err)
		}
		rdr = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.base+path, rdr)
	if err != nil {
		return fmt.Errorf("request %s: %w", path, err)
	}
	if in != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("call %s: %w", path, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != want {
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 2048))
		return fmt.Errorf("%s: status %d: %s", path, resp.StatusCode, string(b))
	}
	if out != nil {
		if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
			return fmt.Errorf("decode %s: %w", path, err)
		}
	}
	return nil
}
```

- [ ] **Step 4: Run (PASS), lint, commit**

```bash
git add internal/push && git commit -m "feat(push): graph push, chunk bulk, job poll"
```

---

### Task 7: chunk assembly + OIDC transport

**Files:**
- Modify: `internal/push/push.go` (add `ItemsFromChunks`)
- Create: `internal/push/items.go`, `internal/push/auth.go`
- Test: `internal/push/items_test.go`

- [ ] **Step 1: Write the failing test for chunk assembly**

```go
package push_test

import (
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract"
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/push"
)

func TestItemsFromChunks(t *testing.T) {
	chunks := []contract.Chunk{{
		EntityID: "go:func:m.F", Type: contract.EntityGoFunc, FilePath: "m.go",
		Language: "go", Header: "[go_func] m.F", Body: "func F() {}",
	}}
	items := push.ItemsFromChunks("tatara-cli", chunks)
	require.Len(t, items, 1)
	require.Equal(t, "[go_func] m.F\n---\nfunc F() {}", items[0].Text)
	require.Equal(t, "go:func:m.F", items[0].Metadata["entity_id"])
	require.Equal(t, "tatara-cli", items[0].Metadata["repo"])
	require.True(t, strings.HasPrefix(items[0].IdempotencyKey, "tatara-cli:go:func:m.F:"))
}
```

(Add `"strings"` to the test imports.)

- [ ] **Step 2: Run to confirm failure**

Run: `go test ./internal/push/... -run TestItemsFromChunks`
Expected: FAIL - `undefined: push.ItemsFromChunks`.

- [ ] **Step 3: Write `internal/push/items.go`**

```go
package push

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract"
)

// ItemsFromChunks turns analyzer chunks into /memories:bulk items.
func ItemsFromChunks(repo string, chunks []contract.Chunk) []contract.IngestItem {
	items := make([]contract.IngestItem, 0, len(chunks))
	for _, ch := range chunks {
		text := ch.Header + "\n---\n" + ch.Body
		sum := sha256.Sum256([]byte(text))
		items = append(items, contract.IngestItem{
			IdempotencyKey: fmt.Sprintf("%s:%s:%s", repo, ch.EntityID, hex.EncodeToString(sum[:8])),
			Text:           text,
			Metadata: map[string]string{
				"repo": repo, "entity_id": ch.EntityID, "type": ch.Type,
				"file_path": ch.FilePath, "language": ch.Language,
			},
		})
	}
	return items
}
```

- [ ] **Step 4: Write `internal/push/auth.go`** (OIDC client-credentials HTTP client)

```go
package push

import (
	"context"
	"net/http"
	"time"

	"golang.org/x/oauth2"
	"golang.org/x/oauth2/clientcredentials"
)

// OIDCClient builds an *http.Client that attaches a client-credentials bearer
// token. tokenURL is the issuer's token endpoint; audience is passed as an
// extra form value (Keycloak honours "audience").
func OIDCClient(ctx context.Context, tokenURL, clientID, clientSecret, audience string, timeout time.Duration) *http.Client {
	cfg := clientcredentials.Config{
		ClientID:     clientID,
		ClientSecret: clientSecret,
		TokenURL:     tokenURL,
		EndpointParams: map[string][]string{
			"audience": {audience},
		},
	}
	base := &http.Client{Timeout: timeout}
	ctx = context.WithValue(ctx, oauth2.HTTPClient, base)
	hc := cfg.Client(ctx)
	hc.Timeout = timeout
	return hc
}
```

- [ ] **Step 5: Run (PASS), add deps, commit**

```bash
go get golang.org/x/oauth2@latest
go mod tidy
go test ./internal/push/...
git add internal/push go.mod go.sum && git commit -m "feat(push): chunk assembly and OIDC client-credentials transport"
```

---

### Task 8: Go analyzer

**Files:**
- Create: `internal/analyze/golang.go`
- Test: `internal/analyze/golang_test.go`, `internal/analyze/testdata/go/*`

**Behaviour contract (what the test pins):** given a tiny module with `pkg.F` calling `pkg.G`, emit a `go_package`, a `go_func` for `F` and `G`, a `defines` edge package->F, and a `calls` edge F->G with `properties[resolution]=type_resolved` and `properties[confidence]=0.98`. Entity IDs are `go:package:<importpath>`, `go:func:<importpath>.<name>`.

- [ ] **Step 1: Create the fixture module**

`internal/analyze/testdata/go/go.mod`:
```
module example.com/sample

go 1.25
```
`internal/analyze/testdata/go/pkg/pkg.go`:
```go
package pkg

func G() int { return 1 }

func F() int { return G() + 1 }
```

- [ ] **Step 2: Write the failing test**

```go
package analyze_test

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/analyze"
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract"
)

func findEdge(edges []contract.Edge, rel, from, to string) (contract.Edge, bool) {
	for _, e := range edges {
		if e.Relation == rel && e.From == from && e.To == to {
			return e, true
		}
	}
	return contract.Edge{}, false
}

func TestGoAnalyzer(t *testing.T) {
	a := analyze.NewGo()
	require.True(t, a.Match("pkg/pkg.go"))
	require.False(t, a.Match("README.md"))

	res, err := a.Analyze(context.Background(), "testdata/go", []string{"pkg/pkg.go"})
	require.NoError(t, err)

	ids := map[string]contract.Entity{}
	for _, e := range res.Entities {
		ids[e.ID] = e
	}
	require.Contains(t, ids, "go:func:example.com/sample/pkg.F")
	require.Contains(t, ids, "go:func:example.com/sample/pkg.G")

	call, ok := findEdge(res.Edges, contract.RelCalls,
		"go:func:example.com/sample/pkg.F", "go:func:example.com/sample/pkg.G")
	require.True(t, ok, "expected F->G calls edge")
	require.Equal(t, contract.ResTypeResolved, call.Properties["resolution"])
	require.Equal(t, "0.98", call.Properties["confidence"])
}
```

- [ ] **Step 3: Run to confirm failure**

Run: `go test ./internal/analyze/... -run TestGoAnalyzer`
Expected: FAIL - `undefined: analyze.NewGo`.

- [ ] **Step 4: Implement `internal/analyze/golang.go`**

Approach (the test is the contract of done):
- `Match`: returns `strings.HasSuffix(path, ".go")`.
- `Analyze`: call `packages.Load(&packages.Config{Mode: packages.NeedName|NeedFiles|NeedSyntax|NeedTypes|NeedTypesInfo|NeedDeps|NeedImports, Dir: repoRoot, Context: ctx}, "./...")`. If a package has `len(pkg.Errors) > 0`, log WARN via the package logger and skip it.
- For each package: emit a `go_package` entity (`ID = "go:package:"+pkg.PkgPath`). For each `*ast.FuncDecl` in `pkg.Syntax`, emit `go_func` (or `go_method` if it has a receiver) with `ID = "go:func:"+pkg.PkgPath+"."+name` (methods: `"go:method:"+pkgPath+".("+recv+")."+name`), `properties[line_start]/[line_end]/[signature]/[exported]`. Emit a `defines` edge package->func.
- Calls: walk each function body with `ast.Inspect`; for each `*ast.CallExpr`, resolve the callee via `pkg.TypesInfo.Uses[ident]` to a `*types.Func`; map its `obj.Pkg().Path()` + name to the callee entity ID; emit a `calls` edge with `properties[resolution]=contract.ResTypeResolved`, `properties[confidence]=contract.ConfidenceFor(ResTypeResolved)`. Skip callees outside the loaded packages (stdlib/third-party).
- `implements`: for each named type with methods, compare against interface types in the package set via `types.Implements`; emit `implements` edges (covered by a second fixture/test if time permits; the call-edge test above is the gate).
- Emit a `contract.Chunk` per func/type with a structural header (`[go_func] <id>`, `file:`, `package:`, `signature:`) and the source body sliced from the file via `token.FileSet` positions.
- `Name()` returns `"go"`.

Add the constructor `func NewGo() Analyzer { return goAnalyzer{log: slog.Default()} }` and a `goAnalyzer` struct.

- [ ] **Step 5: Add the dep, run until PASS**

```bash
go get golang.org/x/tools@latest && go mod tidy
go test ./internal/analyze/... -run TestGoAnalyzer
```
Expected: PASS.

- [ ] **Step 6: Lint, commit**

```bash
git add internal/analyze/golang.go internal/analyze/golang_test.go internal/analyze/testdata/go go.mod go.sum
git commit -m "feat(analyze): Go analyzer (type-resolved graph + chunks)"
```

---

### Task 9: Python analyzer

**Files:**
- Create: `internal/analyze/python.go`
- Test: `internal/analyze/python_test.go`, `internal/analyze/testdata/py/*`

**Behaviour contract:** given `pkg/mod.py` defining `class C` with method `m` and a module function `f` that calls `g` (also defined in the module), emit `py_module`, `py_class` C, `py_func` for `f`, `g`, and `C.m`; `defines` edges; a `calls` edge `f->g` with `resolution=scoped_name_match`, `confidence=0.85`; and an unresolved call to an imported/builtin name recorded as a `dangling_call` property (no edge). FQN = dotted path rooted at the repo (`pkg.mod.f`, `pkg.mod.C.m`).

- [ ] **Step 1: Create fixtures**

`internal/analyze/testdata/py/pkg/mod.py`:
```python
def g():
    return 1

def f():
    return g() + len([])
```

- [ ] **Step 2: Write the failing test**

```go
package analyze_test

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/analyze"
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract"
)

func TestPythonAnalyzer(t *testing.T) {
	a := analyze.NewPython()
	require.True(t, a.Match("pkg/mod.py"))

	res, err := a.Analyze(context.Background(), "testdata/py", []string{"pkg/mod.py"})
	require.NoError(t, err)

	ids := map[string]bool{}
	for _, e := range res.Entities {
		ids[e.ID] = true
	}
	require.True(t, ids["py:func:pkg.mod.f"])
	require.True(t, ids["py:func:pkg.mod.g"])

	call, ok := findEdge(res.Edges, contract.RelCalls, "py:func:pkg.mod.f", "py:func:pkg.mod.g")
	require.True(t, ok, "expected f->g calls edge")
	require.Equal(t, contract.ResScopedNameMatch, call.Properties["resolution"])
	require.Equal(t, "0.85", call.Properties["confidence"])

	// len([]) is a builtin: no edge, recorded as a dangling call on f.
	_, hasLen := findEdge(res.Edges, contract.RelCalls, "py:func:pkg.mod.f", "py:func:pkg.mod.len")
	require.False(t, hasLen)
}
```

(`findEdge` is defined in `golang_test.go`, same package.)

- [ ] **Step 3: Run to confirm failure**

Run: `go test ./internal/analyze/... -run TestPythonAnalyzer`
Expected: FAIL - `undefined: analyze.NewPython`.

- [ ] **Step 4: Implement `internal/analyze/python.go`**

Approach:
- Import `sitter "github.com/smacker/go-tree-sitter"` and `python "github.com/smacker/go-tree-sitter/python"`.
- `Match`: `strings.HasSuffix(path, ".py")`.
- For each file: read bytes, `parser.SetLanguage(python.GetLanguage())`, parse to a tree. Module FQN = the file's repo-relative path with `/`->`.` and trailing `.py` removed (`pkg/mod.py` -> `pkg.mod`).
- Walk the tree for `function_definition` and `class_definition` nodes; emit `py_func`/`py_class` entities (`ID = "py:func:"+moduleFQN+"."+name`; methods nested in a class -> `moduleFQN+"."+class+"."+name`). Emit `defines` edges (module->func/class, class->method).
- Build an in-module def name set. Walk each function body for `call` nodes; the callee name is the `function` child (an `identifier` or `attribute`). Resolve: if the name is in the same function/class scope set -> `scoped_name_match`; if it resolves via a tracked `import_statement`/`import_from_statement` to an in-repo module def -> `imported_name_match`; if it matches a unique def elsewhere in the repo's module set -> `global_name_match`; if >1 -> `ambiguous_multi_def`; otherwise (builtin/external) record `properties[dangling_call]` listing the name on the source entity and emit no edge. Set `properties[confidence]=contract.ConfidenceFor(resolution)`.
- Detect hard constructs: if the call is inside a `decorated_definition`, or the callee is an attribute on a dynamically-produced object (`getattr`), add to `degraded_by` (comma-joined) and cap confidence at 0.45.
- Emit a `contract.Chunk` per module/class/func.
- `Name()` returns `"python"`.

- [ ] **Step 5: Add dep, run until PASS** (CGo required)

```bash
go get github.com/smacker/go-tree-sitter@latest && go mod tidy
CGO_ENABLED=1 go test ./internal/analyze/... -run TestPythonAnalyzer
```
Expected: PASS.

- [ ] **Step 6: Lint, commit**

```bash
git add internal/analyze/python.go internal/analyze/python_test.go internal/analyze/testdata/py go.mod go.sum
git commit -m "feat(analyze): Python analyzer (tree-sitter, M3 confidence)"
```

---

### Task 10: JavaScript analyzer

**Files:**
- Create: `internal/analyze/javascript.go`
- Test: `internal/analyze/javascript_test.go`, `internal/analyze/testdata/js/*`

**Behaviour contract:** given `src/app.js` defining `function g()` and `const f = () => g()`, emit `js_module` (ID `js:module:src/app.js`), `js_func` for `g` and `f` (IDs `js:func:src/app.js::g`, `js:func:src/app.js::f`), `defines` edges, and a `calls` edge `f->g` with `resolution=scoped_name_match`. An ES `import {x} from './util.js'` emits an `imports` edge module->`js:module:src/util.js`.

- [ ] **Step 1: Create fixtures**

`internal/analyze/testdata/js/src/app.js`:
```javascript
import { h } from './util.js';

function g() { return 1; }

const f = () => g() + h();
```
`internal/analyze/testdata/js/src/util.js`:
```javascript
export function h() { return 2; }
```

- [ ] **Step 2: Write the failing test**

```go
package analyze_test

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/analyze"
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract"
)

func TestJavaScriptAnalyzer(t *testing.T) {
	a := analyze.NewJavaScript()
	require.True(t, a.Match("src/app.js"))

	res, err := a.Analyze(context.Background(), "testdata/js", []string{"src/app.js", "src/util.js"})
	require.NoError(t, err)

	ids := map[string]bool{}
	for _, e := range res.Entities {
		ids[e.ID] = true
	}
	require.True(t, ids["js:func:src/app.js::f"])
	require.True(t, ids["js:func:src/app.js::g"])

	_, ok := findEdge(res.Edges, contract.RelCalls, "js:func:src/app.js::f", "js:func:src/app.js::g")
	require.True(t, ok)
	_, imp := findEdge(res.Edges, contract.RelImports, "js:module:src/app.js", "js:module:src/util.js")
	require.True(t, imp)
}
```

- [ ] **Step 3: Run to confirm failure**

Run: `go test ./internal/analyze/... -run TestJavaScriptAnalyzer`
Expected: FAIL - `undefined: analyze.NewJavaScript`.

- [ ] **Step 4: Implement `internal/analyze/javascript.go`**

Approach: mirror the Python analyzer with `javascript "github.com/smacker/go-tree-sitter/javascript"`. `Match`: `.js`/`.mjs`/`.cjs`. Module FQN = repo-relative path (kept verbatim, incl. `.js`). Symbols `<module>::<name>`. Emit `js_func` for `function_declaration` and arrow functions bound to a name (`lexical_declaration` -> `variable_declarator` with an `arrow_function` value), `js_class` for `class_declaration`. Imports: `import_statement` source string and CommonJS `require(...)` calls resolved to a repo-relative module path (resolve `./util.js` against the importing file's dir; add `.js` if no extension). `calls` use the same M3 ladder and `degraded_by` rules as Python. `Name()` returns `"javascript"`.

- [ ] **Step 5: Run until PASS, lint, commit**

```bash
CGO_ENABLED=1 go test ./internal/analyze/... -run TestJavaScriptAnalyzer
git add internal/analyze/javascript.go internal/analyze/javascript_test.go internal/analyze/testdata/js
git commit -m "feat(analyze): JavaScript analyzer (tree-sitter, ES+CJS imports)"
```

---

### Task 11: Terraform analyzer

**Files:**
- Create: `internal/analyze/terraform.go`
- Test: `internal/analyze/terraform_test.go`, `internal/analyze/testdata/tf/*`

**Behaviour contract:** given a `main.tf` with `variable "name"`, a `resource "null_resource" "a"` whose `triggers` reference `var.name`, and an `output "id"` referencing `null_resource.a.id`, emit `tf_variable:name`, `tf_resource:null_resource.a`, `tf_output:id`; a `var_ref` edge resource->variable and an `output_ref`/`references` edge output->resource. IDs: `tf:variable:name`, `tf:resource:null_resource.a`, `tf:output:id`. All `resolution=type_resolved`.

- [ ] **Step 1: Create fixture**

`internal/analyze/testdata/tf/main.tf`:
```hcl
variable "name" { type = string }

resource "null_resource" "a" {
  triggers = { n = var.name }
}

output "id" { value = null_resource.a.id }
```

- [ ] **Step 2: Write the failing test**

```go
package analyze_test

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/analyze"
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract"
)

func TestTerraformAnalyzer(t *testing.T) {
	a := analyze.NewTerraform()
	require.True(t, a.Match("main.tf"))

	res, err := a.Analyze(context.Background(), "testdata/tf", []string{"main.tf"})
	require.NoError(t, err)

	ids := map[string]string{}
	for _, e := range res.Entities {
		ids[e.ID] = e.Type
	}
	require.Equal(t, contract.EntityTFVariable, ids["tf:variable:name"])
	require.Equal(t, contract.EntityTFResource, ids["tf:resource:null_resource.a"])
	require.Equal(t, contract.EntityTFOutput, ids["tf:output:id"])

	_, ok := findEdge(res.Edges, contract.RelVarRef, "tf:resource:null_resource.a", "tf:variable:name")
	require.True(t, ok, "resource should reference var.name")
}
```

- [ ] **Step 3: Run to confirm failure**

Run: `go test ./internal/analyze/... -run TestTerraformAnalyzer`
Expected: FAIL - `undefined: analyze.NewTerraform`.

- [ ] **Step 4: Implement `internal/analyze/terraform.go`**

Approach: `Match`: `.tf`. Use `github.com/hashicorp/hcl/v2/hclparse` to parse each file, then walk the body via `hcl/v2/hclsyntax` (`*hclsyntax.Body` -> `Blocks`). For `variable`/`output` blocks emit `tf_variable`/`tf_output`; for `resource`/`data`/`module` blocks emit `tf_resource`/`tf_data`/`tf_module` with `ID = "tf:resource:"+type+"."+name`. For each attribute expression, collect `hclsyntax.Variables(expr)` traversals; map `var.X` -> `var_ref` edge to `tf:variable:X`, `<type>.<name>.*` -> `references` edge to that resource, `module.X` -> `references` to `tf:module:X`, and `output`/explicit `depends_on` accordingly. Use `terraform-config-inspect` (`tfconfig.LoadModule`) as a cross-check for module sources if convenient. All edges `resolution=type_resolved`. Emit a chunk per block. `Name()` returns `"terraform"`.

- [ ] **Step 5: Add deps, run until PASS, lint, commit**

```bash
go get github.com/hashicorp/hcl/v2@latest github.com/hashicorp/terraform-config-inspect@latest && go mod tidy
go test ./internal/analyze/... -run TestTerraformAnalyzer
git add internal/analyze/terraform.go internal/analyze/terraform_test.go internal/analyze/testdata/tf go.mod go.sum
git commit -m "feat(analyze): Terraform analyzer (HCL graph)"
```

---

### Task 12: Helm analyzer

**Files:**
- Create: `internal/analyze/helm.go`
- Test: `internal/analyze/helm_test.go`, `internal/analyze/testdata/helm/*`

**Behaviour contract:** given a chart `mychart` with `Chart.yaml`, `values.yaml` defining `image.repository`, and `templates/deployment.yaml` referencing `.Values.image.repository` and `include "mychart.labels"`, emit `helm_chart:mychart`, `helm_template:mychart/templates/deployment.yaml`, `helm_value:mychart.image.repository`; a `value_ref` edge template->value and an `includes` edge template->`mychart.labels`. IDs: `helm:chart:mychart`, `helm:template:mychart/templates/deployment.yaml`, `helm:value:mychart.image.repository`.

- [ ] **Step 1: Create fixtures**

`internal/analyze/testdata/helm/mychart/Chart.yaml`:
```yaml
apiVersion: v2
name: mychart
version: 0.1.0
```
`internal/analyze/testdata/helm/mychart/values.yaml`:
```yaml
image:
  repository: nginx
```
`internal/analyze/testdata/helm/mychart/templates/deployment.yaml`:
```yaml
image: {{ .Values.image.repository }}
labels: {{ include "mychart.labels" . }}
```

- [ ] **Step 2: Write the failing test**

```go
package analyze_test

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/analyze"
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract"
)

func TestHelmAnalyzer(t *testing.T) {
	a := analyze.NewHelm()
	require.True(t, a.Match("mychart/templates/deployment.yaml"))
	require.True(t, a.Match("mychart/Chart.yaml"))

	res, err := a.Analyze(context.Background(), "testdata/helm",
		[]string{"mychart/Chart.yaml", "mychart/values.yaml", "mychart/templates/deployment.yaml"})
	require.NoError(t, err)

	ids := map[string]bool{}
	for _, e := range res.Entities {
		ids[e.ID] = true
	}
	require.True(t, ids["helm:chart:mychart"])
	require.True(t, ids["helm:value:mychart.image.repository"])

	tmpl := "helm:template:mychart/templates/deployment.yaml"
	_, vref := findEdge(res.Edges, contract.RelValueRef, tmpl, "helm:value:mychart.image.repository")
	require.True(t, vref)
}
```

- [ ] **Step 3: Run to confirm failure**

Run: `go test ./internal/analyze/... -run TestHelmAnalyzer`
Expected: FAIL - `undefined: analyze.NewHelm`.

- [ ] **Step 4: Implement `internal/analyze/helm.go`**

Approach: `Match`: a path under a `templates/` dir, or a `Chart.yaml`/`values.yaml`. Group files by chart (the dir containing `Chart.yaml`). Parse `Chart.yaml` with `sigs.k8s.io/yaml` -> `helm_chart` entity + `subchart` edges from `dependencies[].name`. Flatten `values.yaml` into dotted keys -> `helm_value:<chart>.<dotted.key>` entities. For each template, parse with `text/template` (`template.New(name).Funcs(noopFuncMap).Parse`); to extract `.Values.*` references and `include`/`template` calls, walk the parsed `*parse.Tree` nodes (`text/template/parse`): `FieldNode`/`ChainNode` starting at `Values` -> `value_ref` edge template->`helm:value:<chart>.<dotted>`; `include`/`template` action with a string literal arg -> `includes` edge template->`helm:include:<name>` (use the literal as the target id suffix). All `resolution=type_resolved`. Emit a chunk per template. `Name()` returns `"helm"`.

Note: register the analyzer with helm precedence ABOVE a generic yaml/docs match so Helm templates are not captured by the docs analyzer.

- [ ] **Step 5: Add dep, run until PASS, lint, commit**

```bash
go get sigs.k8s.io/yaml@latest && go mod tidy
go test ./internal/analyze/... -run TestHelmAnalyzer
git add internal/analyze/helm.go internal/analyze/helm_test.go internal/analyze/testdata/helm go.mod go.sum
git commit -m "feat(analyze): Helm analyzer (chart/template/value graph)"
```

---

### Task 13: Docs analyzer (chunks only)

**Files:**
- Create: `internal/analyze/docs.go`
- Test: `internal/analyze/docs_test.go`, `internal/analyze/testdata/docs/README.md`

**Behaviour contract:** given `README.md`, emit zero entities/edges and one `contract.Chunk` whose `EntityID` is the owning file (`file:<path>`), `Language` is `markdown`, and `Body` is the file content.

- [ ] **Step 1: Create fixture**

`internal/analyze/testdata/docs/README.md`:
```markdown
# Title

Some prose.
```

- [ ] **Step 2: Write the failing test**

```go
package analyze_test

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/analyze"
)

func TestDocsAnalyzer(t *testing.T) {
	a := analyze.NewDocs()
	require.True(t, a.Match("README.md"))
	require.True(t, a.Match("docs/guide.txt"))
	require.False(t, a.Match("main.go"))

	res, err := a.Analyze(context.Background(), "testdata/docs", []string{"README.md"})
	require.NoError(t, err)
	require.Empty(t, res.Entities)
	require.Empty(t, res.Edges)
	require.Len(t, res.Chunks, 1)
	require.Equal(t, "markdown", res.Chunks[0].Language)
	require.Contains(t, res.Chunks[0].Body, "Some prose.")
}
```

- [ ] **Step 3: Run to confirm failure, then implement `internal/analyze/docs.go`**

Run: `go test ./internal/analyze/... -run TestDocsAnalyzer` -> FAIL.

```go
package analyze

import (
	"context"
	"os"
	"path/filepath"
	"strings"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract"
)

type docsAnalyzer struct{}

// NewDocs returns the docs (chunk-only) analyzer.
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
		b, err := os.ReadFile(filepath.Join(repoRoot, f))
		if err != nil {
			continue // unreadable doc: skip, do not fail the run
		}
		lang := "markdown"
		if strings.ToLower(filepath.Ext(f)) == ".txt" {
			lang = "text"
		}
		res.Chunks = append(res.Chunks, contract.Chunk{
			EntityID: "file:" + f,
			Type:     contract.EntityFile,
			FilePath: f,
			Language: lang,
			Header:   "[doc] " + f,
			Body:     string(b),
		})
	}
	return res, nil
}
```

- [ ] **Step 4: Run (PASS), commit**

```bash
go test ./internal/analyze/... -run TestDocsAnalyzer
git add internal/analyze/docs.go internal/analyze/docs_test.go internal/analyze/testdata/docs
git commit -m "feat(analyze): docs analyzer (chunk-only)"
```

---

### Task 14: Default registry wiring

**Files:**
- Create: `internal/analyze/registry.go`
- Test: `internal/analyze/registry_test.go`

- [ ] **Step 1: Write the failing test**

```go
package analyze_test

import (
	"testing"

	"github.com/stretchr/testify/require"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/analyze"
)

func TestDefaultRegistryPrecedence(t *testing.T) {
	reg := analyze.Default()
	g := reg.Group([]string{
		"main.go", "app.py", "web.js", "main.tf",
		"mychart/templates/deployment.yaml", "README.md",
	})
	require.Equal(t, []string{"main.go"}, g["go"])
	require.Equal(t, []string{"app.py"}, g["python"])
	require.Equal(t, []string{"web.js"}, g["javascript"])
	require.Equal(t, []string{"main.tf"}, g["terraform"])
	require.Equal(t, []string{"mychart/templates/deployment.yaml"}, g["helm"])
	require.Equal(t, []string{"README.md"}, g["docs"])
}
```

- [ ] **Step 2: Run to confirm failure, then implement `internal/analyze/registry.go`**

```go
package analyze

// Default returns the registry with all built-in analyzers in precedence order.
// Helm is registered before docs so chart YAML is not swallowed by the doc match.
func Default() *Registry {
	r := NewRegistry()
	r.Register(NewGo())
	r.Register(NewPython())
	r.Register(NewJavaScript())
	r.Register(NewTerraform())
	r.Register(NewHelm())
	r.Register(NewDocs())
	return r
}
```

- [ ] **Step 3: Run (PASS), commit**

```bash
CGO_ENABLED=1 go test ./internal/analyze/... -run TestDefaultRegistryPrecedence
git add internal/analyze/registry.go internal/analyze/registry_test.go
git commit -m "feat(analyze): default registry with all analyzers"
```

---

### Task 15: `cmd/tatara-ingest` wiring

**Files:**
- Modify: `cmd/tatara-ingest/main.go`
- Create: `cmd/tatara-ingest/run.go`
- Test: `cmd/tatara-ingest/run_test.go`

- [ ] **Step 1: Write the failing test** (run against an httptest server, no auth)

```go
package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestRunIngestsFixtureRepo(t *testing.T) {
	dir := t.TempDir()
	for _, a := range [][]string{{"init", "-q"}, {"config", "user.email", "t@t"}, {"config", "user.name", "t"}} {
		c := exec.Command("git", a...)
		c.Dir = dir
		require.NoError(t, c.Run())
	}
	require.NoError(t, os.WriteFile(filepath.Join(dir, "README.md"), []byte("# hi\n"), 0o644))
	for _, a := range [][]string{{"add", "-A"}, {"commit", "-q", "-m", "init"}} {
		c := exec.Command("git", a...)
		c.Dir = dir
		require.NoError(t, c.Run())
	}

	var graphHit, bulkHit bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/code-graph:bulk":
			graphHit = true
			w.WriteHeader(200)
			_, _ = w.Write([]byte(`{"repo":"x"}`))
		case "/memories:bulk":
			bulkHit = true
			w.WriteHeader(202)
			_, _ = w.Write([]byte(`{"id":"j","status":"succeeded"}`))
		default:
			_, _ = w.Write([]byte(`{"id":"j","status":"succeeded"}`))
		}
	}))
	defer srv.Close()

	opts := options{repoRoot: dir, repoName: "x", baseURL: srv.URL}
	require.NoError(t, run(context.Background(), opts, http.DefaultClient))
	require.True(t, graphHit)
	require.True(t, bulkHit)
}
```

- [ ] **Step 2: Run to confirm failure, then write `cmd/tatara-ingest/run.go`**

```go
package main

import (
	"context"
	"log/slog"
	"net/http"
	"os/exec"
	"strings"
	"time"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/analyze"
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/contract"
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/push"
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/walk"
)

type options struct {
	repoRoot     string
	repoName     string
	since        string
	full         bool
	baseURL      string
	pollInterval time.Duration
}

func run(ctx context.Context, o options, hc *http.Client) error {
	start := time.Now()
	files, err := walk.Changed(o.repoRoot, o.since, o.full)
	if err != nil {
		return err
	}
	reg := analyze.Default()
	groups := reg.Group(files)

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
	}

	commit := headCommit(o.repoRoot)
	cl := push.New(o.baseURL, hc, pollOr(o.pollInterval))
	if _, err := cl.PushGraph(ctx, contract.GraphPush{
		Repo: o.repoName, Commit: commit, Files: files,
		Entities: agg.Entities, Edges: agg.Edges,
	}); err != nil {
		return err
	}
	if err := cl.PushChunks(ctx, push.ItemsFromChunks(o.repoName, agg.Chunks)); err != nil {
		return err
	}
	slog.Info("ingest complete",
		"repo", o.repoName, "files", len(files),
		"entities", len(agg.Entities), "edges", len(agg.Edges),
		"chunks", len(agg.Chunks), "duration_ms", time.Since(start).Milliseconds())
	return nil
}

func pollOr(d time.Duration) time.Duration {
	if d <= 0 {
		return 2 * time.Second
	}
	return d
}

func headCommit(repoRoot string) string {
	out, err := exec.Command("git", "-C", repoRoot, "rev-parse", "HEAD").Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}
```

- [ ] **Step 3: Write `cmd/tatara-ingest/main.go`** (flags + env + slog + auth wiring)

```go
// Command tatara-ingest walks a repository and pushes its code graph and
// semantic chunks to tatara-memory.
package main

import (
	"context"
	"flag"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/config"
	"github.com/szymonrychu/tatara-memory-repo-ingester/internal/push"
)

func main() {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stderr, nil)))
	if err := realMain(); err != nil {
		slog.Error("ingest failed", "error", err)
		os.Exit(1)
	}
}

func realMain() error {
	cfg, err := config.Load(envLookup)
	if err != nil {
		return err
	}
	o := options{pollInterval: cfg.PollInterval, baseURL: cfg.BaseURL}
	fs := flag.NewFlagSet("tatara-ingest", flag.ContinueOnError)
	fs.StringVar(&o.repoRoot, "repo-root", "", "path to the repository root (required)")
	fs.StringVar(&o.repoName, "repo-name", "", "logical repo name (default: basename of repo-root)")
	fs.StringVar(&o.since, "since", "", "base commit for incremental ingest")
	fs.BoolVar(&o.full, "full", false, "force full re-ingest")
	fs.StringVar(&o.baseURL, "base-url", o.baseURL, "tatara-memory base URL")
	if err := fs.Parse(os.Args[1:]); err != nil {
		return err
	}
	if o.repoRoot == "" {
		return errMissingRepoRoot
	}
	if o.repoName == "" {
		o.repoName = filepath.Base(strings.TrimRight(o.repoRoot, "/"))
	}

	ctx := context.Background()
	hc := http.DefaultClient
	if cfg.OIDCClientID != "" {
		hc = push.OIDCClient(ctx, cfg.OIDCIssuer+"/protocol/openid-connect/token",
			cfg.OIDCClientID, cfg.OIDCClientSecret, cfg.OIDCAudience, orDur(cfg.HTTPTimeout))
	}
	return run(ctx, o, hc)
}

func orDur(d time.Duration) time.Duration {
	if d <= 0 {
		return 60 * time.Second
	}
	return d
}

// envLookup maps a kebab-case config key to its UPPER_SNAKE env var.
func envLookup(key string) string {
	return os.Getenv(strings.ToUpper(strings.ReplaceAll(key, "-", "_")))
}
```

Add to `run.go` (or a small `errors.go`): `var errMissingRepoRoot = errors.New("--repo-root is required")` (import `"errors"`).

- [ ] **Step 4: Run the test, build, commit**

```bash
CGO_ENABLED=1 go test ./cmd/... -run TestRunIngestsFixtureRepo
CGO_ENABLED=1 go build ./...
git add cmd && git commit -m "feat(cmd): tatara-ingest wiring (walk -> analyze -> push)"
```

---

### Task 16: Integration e2e (gated)

**Files:**
- Create: `cmd/tatara-ingest/e2e_test.go`

- [ ] **Step 1: Write the gated e2e test**

```go
//go:build integration

package main

import (
	"context"
	"net/http"
	"os"
	"testing"

	"github.com/stretchr/testify/require"
)

// Requires a running tatara-memory and:
//   TATARA_TEST_BASE_URL  e.g. http://localhost:8080
//   TATARA_TEST_REPO_ROOT a checked-out repo to ingest
func TestE2EIngest(t *testing.T) {
	base := os.Getenv("TATARA_TEST_BASE_URL")
	root := os.Getenv("TATARA_TEST_REPO_ROOT")
	if base == "" || root == "" {
		t.Skip("set TATARA_TEST_BASE_URL and TATARA_TEST_REPO_ROOT")
	}
	o := options{repoRoot: root, repoName: "e2e-fixture", baseURL: base}
	require.NoError(t, run(context.Background(), o, http.DefaultClient))

	resp, err := http.Get(base + "/code/entities?repo=e2e-fixture&limit=1")
	require.NoError(t, err)
	defer resp.Body.Close()
	require.Equal(t, 200, resp.StatusCode)
}
```

- [ ] **Step 2: Verify it compiles under the build tag, commit**

```bash
go vet -tags integration ./cmd/...
git add cmd/tatara-ingest/e2e_test.go && git commit -m "test(e2e): gated end-to-end ingest"
```

---

### Task 17: Dockerfile (CGo) + Helm Job chart

**Files:**
- Modify: `Dockerfile`
- Create: `charts/tatara-memory-repo-ingester/*` (via `helm create`)

- [ ] **Step 1: Write `Dockerfile`** (CGo builder + libc runtime)

```dockerfile
# syntax=docker/dockerfile:1.7
ARG GO_VERSION=1.25
FROM golang:${GO_VERSION}-bookworm AS builder
WORKDIR /src
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY go.mod go.sum ./
RUN go mod download
COPY . .
ARG VERSION=dev
ARG COMMIT=unknown
ARG DATE=unknown
RUN CGO_ENABLED=1 GOOS=linux go build -trimpath \
    -ldflags "-s -w \
      -X github.com/szymonrychu/tatara-memory-repo-ingester/internal/version.Version=${VERSION} \
      -X github.com/szymonrychu/tatara-memory-repo-ingester/internal/version.Commit=${COMMIT} \
      -X github.com/szymonrychu/tatara-memory-repo-ingester/internal/version.Date=${DATE}" \
    -o /out/tatara-ingest ./cmd/tatara-ingest

FROM gcr.io/distroless/cc-debian12:nonroot
COPY --from=builder /out/tatara-ingest /tatara-ingest
USER nonroot:nonroot
ENTRYPOINT ["/tatara-ingest"]
```

- [ ] **Step 2: Build the image to verify CGo links**

```bash
docker buildx build --platform=linux/amd64 -t tatara-ingest:test --load .
```
Expected: build succeeds (tree-sitter C compiles, binary links libc).

- [ ] **Step 3: Create the chart and edit it to a Job**

```bash
cd charts && helm create tatara-memory-repo-ingester && cd ..
```
Then edit per rule 6/14:
- Delete `templates/deployment.yaml`, `service.yaml`, `hpa.yaml`, `ingress.yaml`, `serviceaccount.yaml` test pod - keep a single `templates/job.yaml` (`kind: Job`, `restartPolicy: Never`, `backoffLimit: 1`, env via `envFrom` referencing a ConfigMap + Secret rendered from `values.yaml`).
- `values.yaml`: only camelCase scalars (`image.repository`, `image.tag`, `baseUrl`, `oidcIssuer`, `oidcClientId`, `oidcAudience`, `repoName`, `repoRoot`). NO lists, no plaintext secrets, no baked imagePullSecrets/affinity/storage (rule 14 - cluster config comes from the infra helmfile).
- Add `templates/configmap.yaml` mapping camelCase values to kebab keys (`base-url`, `oidc-issuer`, `oidc-client-id`, `oidc-audience`, `repo-name`) and a Secret reference for `oidc-client-secret` (provided by the infra helmfile, not the chart).

- [ ] **Step 4: Lint the chart, commit**

```bash
helm lint charts/tatara-memory-repo-ingester
git add Dockerfile charts && git commit -m "feat: CGo Dockerfile and Helm Job chart"
```

---

### Task 18: Final integration sweep

- [ ] **Step 1: Full build, lint, test from repo root**

```bash
CGO_ENABLED=1 go build ./...
golangci-lint run ./... || [ $? -eq 5 ]
CGO_ENABLED=1 go test ./... -race -count=1
```
Expected: all green.

- [ ] **Step 2: Update MEMORY.md / ROADMAP.md with anything learned, commit**

```bash
git add -A && git commit -m "docs: ingester MVP build notes"
```

- [ ] **Step 3: Finish the branch** with `superpowers:finishing-a-development-branch` (merge to `main`, clean worktree). Build/deploy only from `main` (rule 10).

---

## Notes for the executor

- **Parallelisable:** Tasks 8-13 (the six analyzers) are independent units against the frozen `contract` and `Analyzer` interface - dispatch them in parallel, then Task 14 wires the registry. Tasks 1-7 are sequential foundation; 15-18 are sequential integration.
- **CGo:** every `go test`/`go build` touching `internal/analyze` (Python/JS) or the whole module needs `CGO_ENABLED=1`. Pure-Go packages (`contract`, `config`, `walk`, `push`, Go/TF/Helm/docs analyzers) build without it, but run the whole suite with CGo on.
- **Confidence calibration:** the M3 numbers come from `contract.ConfidenceFor`; they are priors to be recalibrated later by tatara-memory's M1 Track A. Do not hand-tune them here.
- **Deferred (do not build):** M2 cross-repo provides/requires, M5 SCIP, the tree-sitter fallback for non-buildable Go. They are in ROADMAP.md.
