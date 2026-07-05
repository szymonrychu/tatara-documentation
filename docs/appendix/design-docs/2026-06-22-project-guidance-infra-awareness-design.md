# Per-project guidance + tatara self-infra awareness

Date: 2026-06-22
Status: approved, ready for planning
Repos touched: tatara-operator, tatara-helmfile

## Problem

The `tatara` project is self-hosting: its repositories (operator, cli, wrapper,
memory, ingester, helmfile) ARE the autonomous-agent platform the agents run
on. Today brainstorm/healthCheck survey the repos for application-level work but
have no instruction to treat the operational infrastructure (helm charts, the
tatara-helmfile releases + deploy pipeline, CI workflows + build tooling,
k8s/ARC-runner/Ceph/memory-backend config, and incidents recorded in each
MEMORY.md) as in-scope. So platform-reliability gaps - the kind that caused the
buildkitd-routing and CR-adoption incidents on 2026-06-22 - never become
proposals.

There is no per-project free-form instruction field; the goal builders take only
`(slugs, repoStateCtx)`.

## Goal

Give each project an optional free-form guidance string that is appended to its
brainstorm and healthCheck goal context, and set the `tatara` project's guidance
to make operational-infrastructure improvements explicitly in-scope.

Non-goals: new live-cluster access for agents (the infra is already codified in
the repos + MEMORY, which the survey reads); changing any other project's
behaviour; per-activity guidance (one project-level string covers both
activities).

## Design

### 1. CRD field (operator)

Add an optional `Guidance string` to `ScmSpec` (api/v1alpha1/project_types.go),
`json:"guidance,omitempty"`, with a doc comment: free-form project charter text
appended verbatim to the brainstorm and healthCheck goal context. Regenerate
manifests (chart `crd-bases` + deepcopy).

### 2. Goal injection (operator)

`brainstormGoalProject` and `healthCheckGoalProject`
(internal/controller/projectscan.go) gain a `guidance string` parameter. When
non-empty, append a trailing block:

```
PROJECT CHARTER: <guidance>
```

(one blank line before it). When empty, the goal is byte-identical to today. The
`brainstorm` and `healthCheck` functions read `proj.Spec.Scm.Guidance` (guard
nil `Scm`) and pass it to their goal builder.

### 3. tatara guidance text (helmfile)

Set `scm.guidance` for the `tatara` project in
`values/project-tatara/common.yaml` (a scalar string; multi-line block scalar is
fine - it renders into the Project CR spec, not a values.yaml env, so the
no-lists rule does not apply). Text:

> This project is self-hosting: its repositories (the tatara operator, cli,
> wrapper, memory, ingester, and helmfile) ARE the autonomous-agent platform you
> and the other agents run on. Treat the operational infrastructure as in-scope,
> not only application features: the helm charts, the tatara-helmfile releases
> and deploy pipeline, the CI workflows and build tooling, the
> k8s/ARC-runner/Ceph/memory-backend configuration, and the incidents recorded
> in each repository's MEMORY.md. When you find a recurring operational failure
> or a gap in the platform's reliability or observability, propose improvements
> to that infrastructure.

The `infrastructure` project is left unchanged (the requirement is specific to
tatara; the field is available to it later if wanted).

### 4. Deploy

Operator code change -> CI builds image + charts at `0.0.0-<opSHA>`. One
tatara-helmfile MR bumping (per the dual-chart-pin memory) the `tatara-operator`
chart pin, BOTH `tatara-project` pins (project-tatara + project-infrastructure),
`values/tatara-operator/common.yaml` image tag, and adding the `scm.guidance`
text to `values/project-tatara/common.yaml`. CRDs apply via the chart.

## Testing (TDD, operator)

- `ScmSpec.Guidance` roundtrips through the CRD (deepcopy/marshal).
- `brainstormGoalProject(slugs, ctx, "")` is byte-identical to the pre-change
  output (no charter block when empty).
- `brainstormGoalProject(slugs, ctx, "CHARTER TEXT")` contains
  `PROJECT CHARTER: CHARTER TEXT`.
- Same two cases for `healthCheckGoalProject`.
- `brainstorm`/`healthCheck` pass `proj.Spec.Scm.Guidance` (nil-Scm safe);
  assert via the existing scan-harness that a project with guidance produces a
  goal containing the charter block.

## Risks / notes

- Guidance is appended verbatim and unbounded; keep the tatara text short. No
  validation beyond optional string.
- Dual-chart-pin: the deploy MUST bump all three `0.0.0-<sha>` pins, else
  project releases fail chart-not-found (see the 2026-06-22 deploy memory).
