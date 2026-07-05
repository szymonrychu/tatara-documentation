# Namespace-preserving clone layout (requirements - spec pending)

Date: 2026-06-09
Status: requirements captured, design pending (after graphify analysis)
Repos: tatara-claude-code-wrapper (agent pod), tatara-operator (ingest job clone),
tatara-memory-repo-ingester (REPO_ROOT consumption)

## Requirement

Clone repos onto disk mirroring their namespace path (excluding the SCM host,
including the owner/group). Decided by user:

- Rule: keep full path incl. owner, excl. host.
  - `https://github.com/szymonrychu/tatara-cli(.git)` -> `<root>/szymonrychu/tatara-cli`
  - `https://gitlab.com/szymonrychu/infra/helmfile(.git)` -> `<root>/szymonrychu/infra/helmfile`
  - ssh `git@github.com:szymonrychu/tatara-cli.git` -> `<root>/szymonrychu/tatara-cli`
- Scope: BOTH the agent pod workspace (wrapper) AND the ingester clone.

## Current state (flat, collides)

- Wrapper `internal/bootstrap/bootstrap.go:50`: `dest := filepath.Join(p.Workspace, r.Name)`
  (basename). `repo.go` `CommitAndPushAll` joins `workspace/r.Name`.
- Operator ingest `internal/ingest/job.go`: clones into `/workspace/repo`.

## Sketch (to formalize in spec)

- Shared rule: `namespacePath(gitURL) -> owner[/subgroups...]/repo` (strip scheme,
  host, userinfo, `.git`, leading/trailing slashes). Implement per-repo (tiny,
  KISS; not a shared module across the separate Go modules).
- Wrapper: clone dest = `Join(workspace, namespacePath(url))`, `MkdirAll` parents;
  `CommitAndPushAll` iterates the same derived path; `.mcp.json`/CLAUDE.md/settings
  stay at workspace root; claude cwd stays workspace root so the agent sees the
  namespace tree.
- Operator ingest job: clone into `/workspace/<namespacePath>`, set REPO_ROOT to it.
- RESOLVED: the memory `repo` LABEL stays the logical identifier (Repository CR
  name). The graphify analysis confirmed cross-repo keying uses repo label + FQN
  and entity IDs are `<lang>:<kind>:<fqn>` -- the on-disk path is not part of the
  key. Only the clone DIRECTORY mirrors the namespace; the repo label, entity
  IDs, and cross_repo_symbols keying are unchanged (no re-keying of the graph).
- Both the re-ingest spec and this change edit the operator ingest `cloneCmd`
  (`internal/ingest/job.go`): re-ingest drops `--depth 1`; this change sets the
  clone destination to `/workspace/<namespacePath>` and points REPO_ROOT at it.
  The implementation plan must apply both edits to the one command together.
