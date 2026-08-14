---
title: Upgrade Workflow
---

# Upgrade Workflow

`upgrade` (added 2026-08-13) is a **scheduled, opt-in, disabled-by-default** agent kind that
keeps a project's third-party dependencies current. Each Task takes exactly **one**
dependency-upgrade unit - one thing being bumped, across however many repos it touches - reads
every release note between the current pin and the target, implements the code and config that
must move with the bump (not just the pin), and carries it through review, merge and deploy on
the existing Task lifecycle. There is no half-upgrade: a pin bump submitted without its required
config migration is a broken change, not a smaller one.

## 1. Trigger

Cron only, opt-in per project via `scm.cron.upgrade`:

```yaml
spec:
  scm:
    cron:
      upgrade:
        schedule: "58 */4 * * *"
        maxOpenUpgrades: 2
  upgradePolicy:
    engine: renovate
    majorStrategy: nextHopOnly
    minimumReleaseAge:
      major: 0
      minor: 0
      patch: 0
```

Each due tick mints **at most one** upgrade Task, and only while the project's open upgrade
lanes (live upgrade Tasks plus not-yet-minted enqueued events) sit below `maxOpenUpgrades`.
Throughput is the cron **frequency**, not a fan-out - minting several Tasks per fire was
rejected, since each would self-scan and race for the same top candidate, and there is no
agent-side task-minting tool left to partition the work with (the #521 redesign deleted
`create_subtask` <!-- stale-ok: create_subtask -->). See [`scm.cron.upgrade`](../reference/project.md#scmcronupgrade) and
[`UpgradePolicySpec`](../reference/project.md#upgradepolicyspec) for the full field tables.

Like `documentation`, an upgrade Task has no driving issue and no approval gate to pass, so it
mints straight into `under-implementation` rather than triaging through `refined` - the
project's resolved `upgradePolicy` **is** the standing go-ahead.

## 2. Scope

Project-wide, like `brainstorm`/`incident`/`refine` - unconstrained to a single repo. The pod
sees every enrolled repo in the project cloned under its own `owner/repo` subdirectory and may
open an MR in any of them, in the publish-dependency order it derives itself (`merge_order` has
no default and no inference).

**Third-party only, never a first-party pin.** A pin whose producer is another enrolled repo of
the same project is written by that repo's own release pipeline (CD propagation); a hand edit
races that write or ships a version that was never published. That case is a release-pipeline
fault, reported via `report_internal_issue`, never an upgrade MR.

## 3. Candidate discovery

`upgradePolicy.engine` picks the mechanism:

- **`renovate`** - the agent runs the Renovate CLI **read-only** inside the pod
  (`RENOVATE_PLATFORM=local RENOVATE_DRY_RUN=full`) and reads its report's `packageFiles` as a
  candidate hint, never as the source of truth. The report carries no changelog text and its
  Helm datasource never exposes `appVersion`/`kubeVersion`, so the agent still reads `Chart.yaml`
  and the upstream release notes itself. Renovate is baked into the agent runtime at a pinned
  version (see [tatara-claude-code-wrapper](../components/claude-code-wrapper.md)) - a missing
  binary falls through to manual enumeration rather than blocking the Task.
- **`none`** - no dependency engine on this project (or Renovate was unavailable). The agent
  enumerates candidates by hand: `Chart.yaml` dependencies, image tags, `go.mod`, lockfiles,
  `.mise.toml`, checked against each upstream's own release feed.

## 4. One unit, self-deduped against siblings

There is no per-unit dedup key on the platform - `Task.spec.dedupKey` is fixed at mint time,
before any unit is chosen. Before claiming a candidate, the agent reads
`task_context(index=true)` for live sibling upgrade Tasks and checks each one's **MR title**
(never its frozen goal `<title>`, which the cron template set before any agent picked anything)
for a dependency name already in flight. This is best-effort, not a guarantee: a sibling that
has not opened its MR yet is invisible. The agent's own unit becomes visible to siblings the
moment its MR opens, in the form `chore: <dependency> <current> -> <target>`.

## 5. Picking the hop

`upgradePolicy.majorStrategy` decides how far one Task may jump:

- **`nextHopOnly`** - the next eligible release only (the smallest increment above the current
  pin). A multi-hop chain is walked **statelessly**, one deployed Task at a time - the repo's
  current pin **is** the cursor; nothing persists the chain.
- **`latest`** - the newest eligible release, in one hop.

Either way, a documented mandatory intermediate release or two-phase migration found in the
release notes **truncates** the hop back toward the current pin - it never pushes the target
further out. "Eligible" also requires the release to be at least `minimumReleaseAge` days old
for its semver level; `0` (the default) is bleeding edge, a deliberate trade against a broken
release reaching the cluster before a fix ships.

The agent cannot see the cluster - there is no `kubectl`, no kubeconfig, in its tool profile - so
a release note that raises a minimum Kubernetes/runtime/DB version is checked against what the
project's own repos declare (`Chart.yaml` `kubeVersion`, `.mise.toml`, `go.mod`, ...). If the
minimum cannot be established from the repos, the Task declines rather than assumes.

## 6. Output

```json
{"action":"submitted","title":"chore: cilium 1.16 -> 1.17","body":"...",
 "change_significance":"minor","merge_order":["charts","helmfile"]}
```
or
```json
{"action":"declined","decline_reason":"..."}
```

The outcome schema is **identical** to `documentation`'s - `submitted`/`declined` only, no
approval-gate actions - reused verbatim rather than duplicated so the two cannot drift on a
field nobody meant to change independently. `declined` is a correct and common answer: every
candidate already claimed by a sibling, nothing eligible under the policy's
`minimumReleaseAge`, or a hop that turns out to be unsafe (a pulled release, a raised minimum
the project's repos do not meet, a migration that needs a maintainer decision). "It is bigger
than one turn" is explicitly **not** a decline reason - the Task is multi-turn, and a partial
hop is handed off via `task_note(kind="handoff")` for the next pod on the same Task to resume.

`submitted` routes the Task through the same [review](review.md) and
[merge & deploy](merge-and-deploy.md) path as any other MR-opening kind - a `request_changes`
verdict comes back to the **same** upgrade agent on the **same** Task, not a separate implement
pod.

## 7. Tool profile

16 tools: the six always-on plus `submit_outcome`, `scm_read`, `mr_write`, the four `code_*`
tools, and `memory_query`/`memory_describe`/`memory_write`. `upgrade` gets `implement`'s code and
memory-write grants plus `mr_write`, but **not** `issue_write` (no approval gate to drive - a
cron mint has no issue) and, like every MR-opening kind, **not** `task_list` or
`mr_takeover_request`. See [MCP tools by agent kind](../reference/mcp-tools.md#the-profile-gating-table)
for the full per-kind gating table.

## Reference: Project CR fields

| Field | Type | Default | Description |
|---|---|---|---|
| `scm.cron.upgrade.schedule` | `string` | - (disabled) | 5-field cron expression. Empty disables upgrade for the project. |
| `scm.cron.upgrade.maxOpenUpgrades` | `int` | `1` | Caps concurrent open upgrade lanes. Set explicitly in enrollment values - see [the field table](../reference/project.md#scmcronupgrade). |
| `upgradePolicy.engine` | `string` | `none` | `renovate` or `none`. |
| `upgradePolicy.majorStrategy` | `string` | `nextHopOnly` | `nextHopOnly` or `latest`. |
| `upgradePolicy.minimumReleaseAge` | object | all zero | Per-level (`major`/`minor`/`patch`) minimum release age in days. |

See [Project reference](../reference/project.md#upgradepolicyspec) for the full field tables.
