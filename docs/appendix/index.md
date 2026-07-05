# Appendix

Primary-source material for the platform: design specs, implementation
plans, audit reports, and runbooks, centralized here so they are readable
without cloning every component repo.

## What's here

- [Design docs](design-docs/index.md) - 156 dated design specs, implementation
  plans, audit reports, and runbooks copied verbatim from the working repo
  (`~/Documents/tatara/docs/`). Spans 2026-05-24 through 2026-07-04: the
  original phase 0/1 design through token-conservation, the Claude
  subscription usage gate, and the semver push-CD cutover.

## Where docs live

This site (`tatara-documentation`) is the **canonical human-facing doc set**:
architecture, concepts, workflows, operations, and this appendix of
historical design/planning artifacts. It is read-only from the component
repos' point of view - nothing here drives agent behavior.

Each component repo keeps its **own** `docs/` directory. Those are
agent-facing: per-repo implementation plans, contract locks, and specs an
agent consults mid-task, kept close to the code they describe. They are not
duplicated here (except where already centralized as of 2026-07-04 in the
platform-wide `docs/superpowers/specs/` and `docs/superpowers/plans/` trees
copied into the [design docs index](design-docs/index.md)).

The component and platform-infra repos are public:

- [tatara-operator](https://github.com/szymonrychu/tatara-operator/tree/main/docs)
- [tatara-memory](https://github.com/szymonrychu/tatara-memory/tree/main/docs)
- [tatara-cli](https://github.com/szymonrychu/tatara-cli/tree/main/docs)
- [tatara-claude-code-wrapper](https://github.com/szymonrychu/tatara-claude-code-wrapper/tree/main/docs)
- [tatara-memory-repo-ingester](https://github.com/szymonrychu/tatara-memory-repo-ingester/tree/main/docs)
- [tatara-chat](https://github.com/szymonrychu/tatara-chat/tree/main/docs)
- [tatara-argo-workflows](https://github.com/szymonrychu/tatara-argo-workflows/tree/main/docs)
- [tatara-helmfile](https://github.com/szymonrychu/tatara-helmfile/tree/main/docs)
- [tatara-observability](https://github.com/szymonrychu/tatara-observability/tree/main/docs)

If a design doc here references a path like `internal/controller/foo.go`,
that file lives in the matching component repo above, not in this
documentation site.
