# mise toolchain across tatara

**Goal:** (1) wrapper container ships mise; (2) each project has a `.mise.toml` and builds via mise; (3) CI uses `.mise.toml`; (4) agent prompt is mise-aware. All tested.

**Decisions (user, 2026-06-14):** minimal tool scope; CI replaces setup-go + golangci-lint-action with mise-action; per-project only (no baked globals); mise version pinned; `.mise.toml` is the golangci-lint source of truth (bump memory 2.11 -> 2.12.2).

**Reference:** `~/Documents/infra/containers/apps/builder/Dockerfile` (mise install pattern), `apps/openclaw-mise/Dockerfile`.

---

## Validated wrapper Dockerfile block (LOCALLY TESTED - apply verbatim)

This exact mise block was built + smoke-tested on a `node:22-bookworm-slim` base (mise 2026.6.3 installs as uid-10001 `agent`; `mise install`+`mise exec -- jq` resolve under `/workspace` with no trust prompt; non-login bash finds tools via shims). Apply to `tatara-claude-code-wrapper/Dockerfile`:

1. After the `ARG TATARA_CLI_VERSION=...` line (top), add:
```dockerfile
# renovate: repository=jdx/mise
ARG MISE_VERSION=v2026.6.3
```
2. Runtime stage apt line: add `curl` (mise installer needs it):
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
```
3. Replace the stage-4 tail (`USER agent` ... `ENTRYPOINT`) with:
```dockerfile
USER agent
ENV HOME=/home/agent HOME_DIR=/home/agent WORKSPACE=/workspace

# mise: per-user tool-version manager for the agent (matches the infra builder
# pattern). Installed as `agent` so it lands in /home/agent/.local; never as root.
# Each cloned repo pins its tools in a root .mise.toml; the agent runs
# `mise install` per repo (no global tools baked here -- the image stays generic).
ARG MISE_VERSION
ENV MISE_VERSION=${MISE_VERSION}
RUN curl https://mise.run | sh \
    && /home/agent/.local/bin/mise --version \
    && /home/agent/.local/bin/mise settings set plugin_autoupdate_last_check_duration "0" \
    && /home/agent/.local/bin/mise settings set not_found_auto_install "true" \
    && /home/agent/.local/bin/mise settings set auto_install "true" \
    && /home/agent/.local/bin/mise settings set task_run_auto_install "true" \
    && /home/agent/.local/bin/mise settings set experimental "true" \
    && /home/agent/.local/bin/mise settings set trusted_config_paths "/workspace" \
    && printf '%s\n' \
        'export PATH="$HOME/.local/bin:$PATH"' \
        'eval "$("$HOME/.local/bin/mise" activate bash)"' \
        >> /home/agent/.bash_profile

# mise binary + shims on PATH so the wrapper-spawned claude process and its
# non-interactive Bash tool calls resolve mise-managed tools. BASH_ENV covers
# login-style shells that need full `mise activate` (env + `mise exec`).
ENV PATH="/home/agent/.local/bin:/home/agent/.local/share/mise/shims:${PATH}"
ENV BASH_ENV="/home/agent/.bash_profile"

WORKDIR /workspace
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/wrapper"]
```

Local re-test (no harbor needed): build a throwaway `node:22-bookworm-slim` + this block, then `docker run` smokes: `mise --version`; `id` (uid 10001); `bash -c 'cd /workspace && printf "[tools]\njq=\"1.7.1\"\n">.mise.toml && mise install && mise exec -- jq --version'` (no trust prompt); non-login `bash -c 'command -v mise'`.

---

## Per-repo `.mise.toml` (verify the go version from each repo's own go.mod)

Common shape (minimal scope). Pin `golangci-lint = "2.12.2"` everywhere (CI parity). `helm = "4.2.1"` ONLY where the repo packages a chart. `GOFLAGS = "-count=1"`. Tasks shell out to the existing Makefile.

```toml
[tools]
go = "<exact go.mod directive>"
golangci-lint = "2.12.2"
# helm = "4.2.1"   # only if the repo has a chart

[env]
GOFLAGS = "-count=1"

[tasks.lint]  = "make lint"
[tasks.test]  = "make test"
[tasks.build] = "make build"
```

Per repo:
- **tatara-cli**: go from go.mod; golangci-lint; NO helm. Tasks -> make lint/test/build (verify those targets exist; if not, map to `go vet ./...`/`go test ./...`/`go build ./...`).
- **tatara-claude-code-wrapper**: go from go.mod (1.25.0), `node = "22"`, golangci-lint, `helm = "4.2.1"` (it packages a chart). PLUS the Dockerfile block above. PLUS re-run the docker smoke.
- **tatara-memory**: ALREADY has `.mise.toml` - reconcile, do not replace. Bump `golangci-lint` 2.11 -> 2.12.2. Add `[tasks]` if missing. Keep its existing helm/helmfile/sops/kubectl/stern + `[tool_alias]` + `[hooks]`.
- **tatara-chat**: NEW. Mirror memory's helm setup (its Makefile calls `mise exec` helm + helm-unittest plugin). go from go.mod, golangci-lint, helm, `[hooks] postinstall` for helm-diff + helm-unittest plugins.
- **tatara-memory-repo-ingester**: go from go.mod, golangci-lint, NO helm. Add `CGO_ENABLED = "1"` to `[env]` (it needs cgo; mise does NOT provide gcc - note in MEMORY.md that system gcc is still required).
- **tatara-operator**: go from go.mod (1.26.x), golangci-lint, `helm = "4.2.1"`, optionally `kubectl` matching ENVTEST_K8S_VERSION. Leave controller-gen/envtest as `go run @ver` in the Makefile (already pinned; out of scope - note in MEMORY.md). NOTE: operator `make test` needs envtest/KUBEBUILDER_ASSETS - the `mise run test` task wraps `make test` which handles that.

If a repo's Makefile has no `lint`/`test`/`build` target, either add a thin one or point the mise task at the real command - keep tasks consistent across repos.

---

## CI change (each repo `.github/workflows/ci.yml`)

Replace `actions/setup-go@v5` + `golangci/golangci-lint-action@v8` (and any manual gofmt/golangci-lint install) in the lint/test/build jobs with:
```yaml
      - uses: actions/checkout@v4
      - uses: jdx/mise-action@v2
        with:
          cache: true
      - run: mise run lint     # or test / build, per job
```
- `jdx/mise-action@v2` installs mise, runs `mise install` from `.mise.toml`, caches tools.
- Leave `secscan` (gitleaks), `image` (kaniko/build+push), `chart` (helm push) jobs UNCHANGED - out of scope.
- ingester: keep the `apt-get install build-essential` (or equivalent) step before `mise run build` (cgo needs gcc; mise doesn't provide it).
- Where a job currently installs helm via `azure/setup-helm`, the mise-action + `.mise.toml` helm pin replaces it; drop the azure step.

---

## Agent prompt (part 4) - ships via tatara-helmfile/operator ConfigMap, NOT the wrapper repo

The global agent CLAUDE.md is mounted at `/etc/wrapper/global-claude.md` from a ConfigMap. Find its source (operator embeds it OR tatara-helmfile values) and add a "Toolchain (mise)" section:

```
## Toolchain (mise)

Every tatara repo pins its build tools in a root `.mise.toml`. mise is already
installed in this container and on PATH.

- In a freshly cloned repo, run `mise install` once before building.
- Invoke pinned tools through mise: `mise exec -- go build ./...`,
  `mise exec -- golangci-lint run`, or the repo task `mise run lint` /
  `mise run test` / `mise run build`. Do NOT call a bare `go`/`helm` for a
  build - it may be the wrong version.
- If you change a tool dependency, edit that repo's `.mise.toml` (pin an exact
  version), never install ad-hoc.
- `.mise.toml` under /workspace is pre-trusted; no `mise trust` needed.
```
Ship order: wrapper image (with mise) FIRST, then this prompt change (else the prompt references a tool the image lacks).

---

## Test plan (bulletproof)
- L1 wrapper image: docker build + the smokes above (DONE for the block; re-confirm on the real multi-stage build if harbor login is available, else the standalone smoke suffices).
- L2 per repo: `mise install` exits 0; `mise exec -- go version` matches go.mod; `mise exec -- golangci-lint version` == 2.12.2; helm repos: `mise exec -- helm version` + plugins installed.
- L3 per repo: `mise run lint` / `test` / `build` all green (wrap the passing Makefile targets).
- L4 CI: feature-branch PR per repo; mise-action installs+caches; lint/test/build green via mise; gitleaks/kaniko/chart unchanged.
- L5 agent (post-deploy): one real task runs `mise install` + `mise run`/`mise exec` on a fresh clone with no trust prompt.

## Out of scope (note in ROADMAP/MEMORY)
controller-gen/envtest into mise; gitleaks/kaniko into mise; a reusable shared CI workflow; tatara-argo-workflows .mise.toml (chart-only, low value).
