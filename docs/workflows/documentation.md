---
title: Documentation Workflow
---

# Documentation Workflow

`documentation` is a **nightly batch**, one Task per project per night, covering everything
delivered in the last 24 hours - not one Task per delivery, and not a per-change "was this
non-trivial?" judgment call.

## 1. Trigger

Cron only, once per project per night:

```
covered := every Task with deliveredAt within the last 24h
                          AND len(mrRefs) > 0 AND every MR merged
                          AND documentedBy == ""
if covered is empty: mint nothing.
else: mint ONE Task, kind=documentation,
      spec.documentsTasks = [covered...], spec.repositoryRef = the docs repo,
      stage = documenting
```

A merge to any enrolled repo's default branch does **not** spawn a documentation Task by itself
- only the next due nightly tick, and only when it finds something covered.

!!! info "Why a batch, not one per delivery"
    Per-delivery was a 3-5x work amplifier against a handful of agent slots: a doc Task, a doc
    MR, a review pod, a merge, and a `tatara-documentation` release - for every one-line patch
    fix. The batch collapses a whole day's deliveries into one documentation pass, one MR, one
    review, one release.

## 2. Scope

Repo-scoped - unlike the other five live origin kinds, `documentation` targets one docs repo
per Task, not a project-wide umbrella. It still sees every other repo in the project at their
default branches for context (to know what changed), but writes back to the docs repo alone.
Model tier is **sonnet**, not opus - documentation is a lower-stakes, schedule-driven kind, not
a live human-facing conversation.

## 3. What never gets documented

`status.documentedBy` stays permanently empty - and the Task is never picked up by a nightly
batch - for any Task with zero merged MRs:

- A brainstorm that correctly says "nothing novel" (`action=skip`).
- An incident marked `false_positive`.
- A fold-only `refine` pass (folds/closes/links, no code).
- A declined `implement` (`action=declined`).

These are never documented, by design - not a gap, a filter.

## 4. Output

The `documenting` pod's `submit_outcome` schema is **identical** to `implement`'s:

```json
{"action":"submitted","title":"...","body":"...",
 "change_significance":"major|minor|patch"}
```
or
```json
{"action":"declined","decline_reason":"..."}
```

Either way, the operator stamps `documentedBy` on **every** Task in `spec.documentsTasks` as the
batch exits - `submitted` moves the stage `documenting -> reviewing` (the docs MR goes through
the same [review](review.md) and [merge](merge-and-deploy.md) path as any other MR); `declined`
moves it straight to `delivered`.

!!! warning "The batch cannot pin its parents"
    A `documenting` Task stuck past its 2h stage-work budget (`docStageBudget`) is force-moved to
    `delivered(doc-timeout)`, and it stamps `documentedBy` on every covered parent **on the way
    out** - a stalled documentation pass never leaves its covered Tasks permanently
    undocumentable.

## Reference: Project CR fields

| Field | Type | Default | Description |
|---|---|---|---|
| `scm.cron.documentation.enabled` | `bool` | `false` | Must be `true` to activate. |
| `scm.cron.documentation.schedule` | `string` | - | 5-field cron expression; runs once nightly. |

See [Project reference](../reference/project.md#scmcrondocumentation) for the full field table.
