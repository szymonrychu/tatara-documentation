# Enroll tatara (dogfood) Design

**Date:** 2026-06-07
**Status:** approved design, pre-plan.
**Scope:** Unblock the two image gaps + enroll the tatara platform as the first
real operator `Project`.

## Purpose

Make the operator able to ingest + work on the tatara repos themselves. Two
images block this today; once fixed, create the enrollment objects.

## Blockers being fixed

1. **Ingester image cannot clone or Go-build.** Final stage is
   `gcr.io/distroless/cc-debian12:nonroot` (no `git`, no `/bin/sh`, no `go`),
   but the ingest Job clones with `git` and Go type-resolution needs `go build`.
2. **Wrapper image missing from Harbor**, and it `COPY --from`s
   `tatara-cli:${TATARA_CLI_VERSION}` which is also missing (v0.4.0 was only
   git-tagged, never built as a container).

## Changes

### A. Ingester runtime image (`tatara-memory-repo-ingester`)

- Dockerfile final stage: `gcr.io/distroless/cc-debian12:nonroot` ->
  `golang:1.26-bookworm`. This carries `git`, the `go` toolchain (for
  type-resolved Go edges), and glibc (the binary is CGo / tree-sitter). 1.26
  because the ingester must `go build` repos pinned to Go 1.26 (e.g.
  tatara-operator); `GOTOOLCHAIN=auto` + egress handles anything newer.
- Keep the multi-stage builder unchanged; final stage `COPY`s the
  `tatara-ingest` binary and sets the entrypoint. Run as a non-root UID via the
  ingest Job pod securityContext (chart), not baked.
- Decision (from brainstorming): full Go toolchain for type-resolved Go edges
  (tatara is Go-heavy). No shared module cache yet (YAGNI; `go mod download`
  per ingest, 443 egress already allowed by the managed-pod NetworkPolicy).
- Bump version, build + push `harbor.szymonrichert.pl/containers/tatara-memory-repo-ingester:0.2.0`.
- Update the operator infra value `ingesterImage` -> `:0.2.0` and re-apply the
  operator release (config-only; no operator code change).

### B. tatara-cli container image (`tatara-cli`)

- No code change. Build the existing `v0.4.0` main via its Dockerfile; push
  `harbor.szymonrichert.pl/containers/tatara-cli:0.4.0`. The binary lands at
  `/usr/local/bin/tatara`. Prerequisite for the wrapper.

### C. Wrapper image (`tatara-claude-code-wrapper`)

- No code change. Build with `--build-arg TATARA_CLI_VERSION=0.4.0` (bundles the
  operator MCP tools) and claude `latest`; push
  `harbor.szymonrichert.pl/containers/tatara-claude-code-wrapper:0.1.0`. This is
  the value for `Project.spec.agent.image`.

### D. Enrollment objects (ns `tatara`)

- Secret (referenced by `Project.spec.scmSecretRef`): keys `token` (GitHub PAT,
  supplied by the human; scopes repo + admin:repo_hook) and `webhookSecret`
  (operator-side HMAC, generated). Created via the agent-safe secret pattern (no
  plaintext in git; value piped, never printed).
- `Project` `tatara`: `scmSecretRef`, `triggerLabel: tatara`,
  `agent.image` = the wrapper image, default `spec.memory` (1 cnpg instance).
- `Repository` CRs for the child repos: tatara-memory, tatara-cli,
  tatara-operator, tatara-chat, tatara-memory-repo-ingester,
  tatara-claude-code-wrapper, tatara-argo-workflows (url
  `https://github.com/szymonrychu/<name>`, defaultBranch `main`). All share the
  Project's one memory stack.
- GitHub webhooks per repo -> `https://tatara.szymonrichert.pl/operator/webhooks/tatara`
  (events: push, issues), HMAC = `webhookSecret`. Registered via the PAT
  (`gh api`) or manually.

## Non-goals

- No shared go-module cache PVC (later optimization).
- No automatic first agent run; running a labelled issue end-to-end is a
  separate, human-initiated validation.
- No tatara-cli homebrew/goreleaser release (only the container image is built).

## Validation

- Ingester image: `git` + `go` present, `tatara-ingest --help` runs.
- After enrollment: each `Repository` reaches `phase=Ingested`
  (`lastIngestedCommit` set); the Project memory stack is `Ready`; the
  code-graph is non-empty (`code_entities > 0`). First real ingest will surface
  real-code edge cases (FQN scale, `go build` of each repo).

## Execution

Two parallel subagent workstreams: (A) ingester image; (B) cli image then
wrapper image (sequential). Then config re-apply + enrollment. The GitHub PAT
is requested from the human before the Secret/webhook steps.

## Build decomposition (milestones)

- **E1** ingester Dockerfile base swap + build/push 0.2.0 + infra value bump + re-apply.
- **E2** build/push tatara-cli:0.4.0; then build/push wrapper:0.1.0.
- **E3** enrollment: Secret (PAT) + Project + Repository CRs + webhooks; verify
  ingest reaches the graph.
