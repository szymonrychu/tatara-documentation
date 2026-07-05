# pre-commit as the unified lint/test gate

**Goal:** (1) each repo runs pre-commit hooks (lints at commit stage, tests at pre-push stage); (2) the wrapper installs the hooks after cloning so the agent's commits/pushes are gated; (3) CI runs `pre-commit run` instead of hardcoded lints. Pilot tatara-cli + the shared wrapper change end-to-end, then expand to the other repos.

**Decisions (user, 2026-06-14):** tests at **pre-push** stage (lints at commit stage); the agent's own commits run hooks (gated), the wrapper's safety-net `CommitAndPush` uses `--no-verify` (never loses work; CI is the backstop). Builds on the mise rollout. Reference: infra `.mise.toml` pins `pre-commit = '4.6.0'` + `[hooks] postinstall = ["pre-commit install || true"]`.

---

## Per-repo changes

### `.mise.toml` (every repo)
Add to `[tools]`: `pre-commit = "4.6.0"`. Add (or extend) hooks:
```toml
[hooks]
postinstall = ["pre-commit install --hook-type pre-commit --hook-type pre-push || true"]
```
(memory already has a `[hooks]` for helm plugins - append, don't replace.)

### `.pre-commit-config.yaml`
- cli + memory: HAVE lint hooks (eof, trailing-ws, check-yaml, gitleaks, yamllint, go-fmt, golangci-lint, config-verify). ADD a pre-push test hook.
- operator, chat, ingester, wrapper: CREATE the file - copy cli's lint hooks (adjust: ingester needs cgo for the golangci-lint/test; chat/operator have charts -> keep yaml/helm lint as-is) + the test hook.

Test hook (local, pre-push stage, full suite, no filename filter):
```yaml
  - repo: local
    hooks:
      - id: go-test
        name: go test (mise)
        entry: mise run test
        language: system
        pass_filenames: false
        stages: [pre-push]
        types: [go]
```
The lint hooks stay at the default commit stage. golangci-lint/go-fmt come from dnephin/pre-commit-golang and run the system (mise-provided) binaries.

### CI `.github/workflows/ci.yml`
- **lint job**: replace the hardcoded `gofmt` step + `mise run lint` with `pre-commit run --all-files` (runs all commit-stage hooks: gofmt, golangci-lint, yamllint, gitleaks, eof). Keep the `jdx/mise-action@v2` step (installs go, golangci-lint, pre-commit). No `make` needed for the lint job (pre-commit calls hooks directly).
- **test job**: keep `mise run test` (build-essential stays for make/cgo). Optionally `pre-commit run --hook-stage pre-push --all-files` instead - either is fine; keep `mise run test` for simplicity.
- Leave secscan/image/chart jobs unchanged (gitleaks is also in pre-commit now - dedup is a later cleanup, note in ROADMAP).

---

## Wrapper change (shared, one-time) - `tatara-claude-code-wrapper/internal/bootstrap`

1. **Install hooks after clone+checkout.** In `Render`, after `checkoutTaskBranch` for each repo dir, run `mise install` (its `.mise.toml` postinstall runs `pre-commit install`), then explicitly `pre-commit install --hook-type pre-commit --hook-type pre-push` (literal + idempotent; tolerate error if no `.pre-commit-config.yaml`). Best-effort: a failure must NOT abort the clone (some repos may lack a config). Pass the git/cmd runner appropriately (Render uses GitRunner for git; pre-commit/mise are non-git commands - use the CmdRunner seam or extend). Check how `RegisterTataraMCP` runs non-git commands (app.go execRunner) and mirror that, OR run `mise`/`pre-commit` via the GitRunner's underlying exec (add a small runner). KISS: add a `cmd` runner param or reuse the existing pattern.
2. **Safety-net commit bypasses hooks.** In `CommitAndPush` (`repo.go`), add `--no-verify` to the `git commit` so the wrapper's enforced commit never fails on a hook (the agent's own commits remain gated; CI is the backstop). The push is unaffected (pre-push hook would run on the wrapper's push - also bypass with `git push --no-verify` to keep the safety-net push reliable).

TDD the bootstrap change with the existing fake runners.

---

## Pilot (tatara-cli + wrapper) - test ALL levels before expanding

1. cli `.mise.toml` + `.pre-commit-config.yaml` (test hook) + CI (lint -> pre-commit).
2. wrapper bootstrap (install hooks + --no-verify) + wrapper's own `.mise.toml`/`.pre-commit-config.yaml`.
3. LOCAL verification (cli):
   - `mise install` -> installs pre-commit + runs postinstall; `.git/hooks/pre-commit` and `.git/hooks/pre-push` exist.
   - `pre-commit run --all-files` -> all lint hooks pass.
   - `pre-commit run --all-files --hook-stage pre-push` -> the go-test hook runs `mise run test` and passes.
   - A deliberately mis-formatted file -> `git commit` is BLOCKED by the commit hook (proves gating); fix -> commits.
4. LOCAL verification (wrapper bootstrap): unit tests assert `pre-commit install ...` is invoked after checkout and `git commit --no-verify` is used in CommitAndPush.
5. CI: cli PR runs the pre-commit lint job green; wrapper PR builds.

Only after the pilot is green end-to-end, expand to operator/memory/chat/ingester.

## Expand (after pilot)
operator, memory, chat, ingester: same `.mise.toml` pre-commit + `.pre-commit-config.yaml` (create where missing, add test hook) + CI lint -> pre-commit. Each tested locally (`mise install`, `pre-commit run --all-files`, pre-push test).

## Out of scope (ROADMAP)
Dedup the separate gitleaks secscan job now that pre-commit runs gitleaks; a reusable shared CI workflow.
