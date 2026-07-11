---
title: Documentation Workflow
---

# Documentation Workflow

`documentation` is a schedule-driven kind that keeps a project's docs repo current without a
human filing an issue for every change. It replaced the previous push-webhook trigger entirely.

## 1. Trigger

Cron only - `scm.cron.documentation.schedule` on the `Project` CR. There is no webhook path: the
prior `maybeEnqueueDocumentation` push-triggered enqueue is removed. A merge to any enrolled
repo's default branch does **not** spawn a documentation Task by itself; only the next due cron
tick does.

## 2. Scope

Repo-scoped - unlike the other six live kinds, `documentation` targets one docs repo per Task,
not a project-wide umbrella. It still sees every other repo in the project at their default
branches for context (to know what changed), but writes back to the docs repo alone.

## 3. Behavior

On each tick, the agent:

1. Determines when the docs repo was last meaningfully updated.
2. Diffs what changed across the project's other repos since that point.
3. Updates the docs repo only if the accumulated change is non-trivial - a single typo fix
   elsewhere in the project does not by itself justify a documentation Task's PR; a new
   component, a changed CRD field, or a materially different workflow does.

Model tier is **sonnet** (not opus) - documentation is a lower-stakes, schedule-driven kind, not
a live human-facing conversation.

## Reference: Project CR fields

| Field | Type | Default | Description |
|---|---|---|---|
| `scm.cron.documentation.enabled` | `bool` | `false` | Must be `true` to activate. |
| `scm.cron.documentation.schedule` | `string` | - | 5-field cron expression. |

See [Project reference](../reference/project.md#scmcrondocumentation) for the full field table.
