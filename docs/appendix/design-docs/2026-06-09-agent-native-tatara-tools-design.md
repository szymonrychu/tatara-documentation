# Agent-native tatara tools + memory robustness - design

**Date:** 2026-06-09
**Status:** approved
**Repos touched:** `tatara-cli`, `tatara-operator`, `tatara-claude-code-wrapper`, `tatara-memory`, `infra/terraform/keycloak` (verify)

## Problem

An agent self-audit (issue szymonrychu/tatara-cli#10, PR #11) found that the
spawned agent cannot use any tatara MCP tool natively: the pod's `.mcp.json` is
empty and the in-pod `tatara` CLI has no token (`auth: no token`). The backends
(operator REST, memory code-graph) are up and reachable; only the agent's path
to them is missing. This is why the agent never created Subtasks - it cannot
call `subtask_create`. Separately, a transient postgres blip crashlooped
`mem-tatara` (7x), briefly 502ing `/queries`, and left a stale `MemoryNotReady`
condition on the repos.

## A. Native tatara tools for the agent

### A1. tatara-cli: non-interactive client-credentials auth

`internal/auth` currently only loads a stored token (`tatara login` device
flow) and returns `ErrNoToken` otherwise. Add a headless fallback: when no
stored token exists AND `CLI_OIDC_CLIENT_ID` + `CLI_OIDC_CLIENT_SECRET` +
`OIDC_ISSUER` are set, perform an OIDC `client_credentials` grant against the
issuer's token endpoint (discovered via `<issuer>/.well-known/openid-configuration`),
cache the access token in memory with its expiry, and refresh when it is within
~30s of expiring. `tatara login` remains the interactive path and a stored token
still wins. No new flags; the fallback is transparent to `tatara raw` and
`tatara mcp`.

Keycloak dependency (verify, fix in terraform if missing): the confidential
client behind `tatara-cli-oidc` must have **service accounts enabled** (for the
grant) and **audience mappers for both `tatara-memory` and `tatara-operator`**
so one token is accepted by both REST APIs. If the client is public/device-only,
add a confidential client or enable service accounts + the two audience mappers
in `infra/terraform/keycloak/tatara_clients.tf`.

### A2. operator: inject TATARA_OPERATOR_URL

`internal/agent/pod.go` injects `TATARA_MEMORY_URL` but not the operator REST
URL, so the CLI's `task_*`/`subtask_*` tools have no endpoint. Add
`TATARA_OPERATOR_URL` pointing at the in-cluster operator REST Service (the same
host the wrapper callback uses, on the REST/HTTP port - a new `PodConfig` field
`OperatorURL`, sourced from operator config, e.g.
`http://tatara-operator.tatara.svc:8080`). The auth env
(`CLI_OIDC_CLIENT_ID/SECRET`, `OIDC_ISSUER`) is already injected and matches the
names A1 reads.

### A3. wrapper: register the tatara MCP server at bootstrap

After `bootstrap.Render` writes `/workspace/.mcp.json`, run
`tatara mcp-config /workspace` (the CLI baked in the wrapper image; merges a
`tatara` entry into the existing `.mcp.json`, idempotent). Gate on the `tatara`
binary being present and `TATARA_MEMORY_URL` set (i.e. a real agent pod), so
tests/dev without the CLI are unaffected. claude then launches `tatara mcp` as a
stdio MCP server that self-configures from `TATARA_MEMORY_URL` /
`TATARA_OPERATOR_URL` / the auth env.

Result: the agent gains `code_*` (graph navigation), `task_*`/`subtask_*`
(self-plan + report), and memory tools natively; real subtask decomposition
becomes possible.

## B. Memory robustness

### B1. mem-tatara survives a postgres blip

The `mem-tatara` pod crashlooped 7x when `mem-tatara-pg-rw:5432` briefly refused
connections. Investigate the actual fatal point (DB open at startup vs the
pool-resume WARN vs liveness). Fix: make startup **wait for postgres** with
bounded backoff (e.g. retry `db.Ping` for up to ~60s) before serving, and keep
`pool.Resume` non-fatal (already a WARN), so a transient pg restart does not take
the memory API down or crashloop the pod. If the fatal point is liveness/readiness
during the window, adjust the probe thresholds rather than the code.

### B2. operator clears the stale MemoryNotReady condition

The RepositoryReconciler sets a `MemoryNotReady` condition while the Project
memory provisions; once `Project.status.memory.phase == Ready` it is never
cleared, so it lingers and misleads. When memory is Ready, set that condition
`False` (Reason `MemoryReady`) on the Repository during reconcile.

### B-verify: live /queries recheck

After B1, re-run a real `/queries` against the per-Project memory to confirm the
semantic layer answers now that the stack is stable; record the result.

## Testing

- **tatara-cli:** `internal/auth` unit test - no stored token + client-creds env
  -> performs a `client_credentials` grant against a fake token endpoint and
  returns/caches the token; expiry triggers a refresh; stored token still wins;
  missing env -> `ErrNoToken` (unchanged).
- **operator:** `pod_test.go` asserts `TATARA_OPERATOR_URL` env present with the
  configured value; controller test asserts the Repository `MemoryNotReady`
  condition is cleared (False) once `memory.phase == Ready`.
- **wrapper:** bootstrap/app test asserts `tatara mcp-config /workspace` is
  invoked after `.mcp.json` is written when the CLI + memory env are present, and
  skipped otherwise (injected runner, like `GitRunner`).
- **tatara-memory:** startup wait-for-pg retries `Ping` and proceeds once pg is
  reachable (fake/slow DB), and does not exit on a transient failure.

## Out of scope

- Per-tool MCP allow-listing / scoping (the agent gets the full tatara tool set).
- Token persistence to disk (in-memory cache is enough for one pod/session).
- Reworking the device-flow `tatara login` path.
