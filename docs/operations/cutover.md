---
title: Task-Centric Cutover Runbook
description: The one-way migration to the task-centric operator - three tatara-helmfile PRs, the second of which cannot be undone.
---

# Task-Centric Cutover Runbook

This is a **one-time migration**, executed once, driven by three `tatara-helmfile` PRs named **PR-A**, **PR-B**, and **PR-C**. Only PR-A and the cleanup/verify steps between it and PR-B are reversible.

!!! danger "PR-B is the point of no return. Recovery is forward only."
    ```
    ########################################################################
    #  PR-B IS THE POINT OF NO RETURN. RECOVERY IS FORWARD ONLY.           #
    #                                                                      #
    #  PR-B lands the new operator CHART, and the chart carries the CRDs.  #
    #  After it applies:                                                   #
    #    - the OLD operator cannot read the NEW CRs (different schema);    #
    #    - reverting ONLY the operator is WORSE than going forward: the    #
    #      new cli would then be talking to an old REST surface.           #
    #                                                                      #
    #  AND HELM WILL TRY TO ROLL YOU BACK AUTOMATICALLY.                   #
    #  syncArgs carries --rollback-on-failure GLOBALLY, so ANY failure in  #
    #  this apply triggers a rollback you did not ask for. And a rollback  #
    #  DOES revert the CRDs: resource-policy: keep skips DELETION only,    #
    #  and a CRD present in both revisions is UPDATED - PATCHED BACK TO    #
    #  THE OLD SCHEMA, over the new Issue/MergeRequest CRs.                #
    #                                                                      #
    #  => PR-B MUST REMOVE --rollback-on-failure FROM helmDefaults.syncArgs #
    #     GLOBALLY. PR-C restores it. There is NO per-release syncArgs key #
    #     to scope it with instead. Verify with `helmfile build` first.    #
    #                                                                      #
    #  A failed PR-B must STOP, loudly, in a known-bad state that a human  #
    #  fixes forward - not silently restore a schema that the already-     #
    #  released cli/wrapper/agent-skills cannot talk to.                   #
    #                                                                      #
    #  There is no rollback. There is only fixing forward.                 #
    ########################################################################
    ```

## Release train order

The redesign's own umbrella Task declares this order in `spec.mergeOrder`. Every repo above `tatara-helmfile` merges and releases its artifacts before anything is applied to the cluster; the cutover below is what happens once all six are sitting in Harbor, unapplied.

| Order | Repo | Significance | Why it goes here |
|---|---|---|---|
| 1 | `tatara-operator` | major | The operator chart *and* the `tatara-project` chart both live here - it carries every new CRD. |
| 2 | `tatara-cli` | major | Consumes the operator's new REST surface. |
| 3 | `tatara-agent-skills` | major | Names the cli's new MCP tools; a skill naming a tool the cli does not yet serve leaves the agent with no terminal tool. |
| 4 | `tatara-claude-code-wrapper` | major | Reports the contract version the operator now injects. |
| 5 | `tatara-observability` | alerts + dashboards | **Same train, not after it.** The alerts and dashboards this release replaces key on fields the operator removes in this same release, including both CD-cascade alerts. Shipping the operator without them leaves the merge/deploy path to a cluster-admin-scoped runner with zero alert coverage while every dashboard still reads green. |
| 6 | `tatara-helmfile` | five pins; `replicaCount 0`; values migration; `tatara-chat` uninstall <!-- stale-ok: tatara-chat --> | The cutover itself - everything below this table. |
| 7 | `tatara-documentation` | hard rule #7; new architecture | This page. It lands last because it describes the platform the other six just became, not the one they are leaving. |

**Five pins, and no operator image pin at all** - the operator's image rides its chart's `appVersion`:

| # | Location | What it pins |
|---|---|---|
| 1 | `helmfile.yaml.gotmpl:64` | `tatara-operator` chart version - lands the new CRDs |
| 2 | `helmfile.yaml.gotmpl:83` | `project-tatara` release, `tatara-project` chart version |
| 3 | `helmfile.yaml.gotmpl:95` | `project-infrastructure` release, `tatara-project` chart version |
| 4 | `values/project-tatara/common.yaml:38` | agent image |
| 5 | `values/project-infrastructure/common.yaml:37` | agent image |

Pins 2 and 3 are what render the `Project` and `Repository` CRs. Miss either one and that project's CRs stay on the old vocabulary - silently, because a `map[string]string` ignores an unmatched key rather than rejecting it.

## Pre-flight (reversible)

Nothing below is applied to the cluster yet.

1. **Forensic backup.** It will **not** restore cleanly into the new CRDs - it is for post-mortems, not rollback. Dump every `Task`, `Subtask`, `Project`, `Repository` and `QueuedEvent` object: `kubectl get tasks,subtasks,projects,repositories,queuedevents -n tatara -o yaml > cutover-backup-$(date +%F).yaml`. <!-- stale-ok: Subtask, subtasks -->
2. **Back up `tatara-chat`'s Postgres, or explicitly accept the loss, in writing.** Its CNPG cluster is a subchart of the chat chart with no retain annotation and no backup configured. Rooms are TTL'd and disposable; `chat_handoffs` is not - dump it with `kubectl exec -n tatara <chat-pg-primary> -- pg_dump -t chat_handoffs ...`, or lose it. <!-- stale-ok: tatara-chat, chat_handoffs -->
   The loss is triggered by the uninstall in PR-B (`installed: false` -> `helm uninstall` -> the `Cluster` CR is deleted -> CNPG garbage-collects the PVCs), not by deleting the helmfile block - see [PR-B](#pr-b-the-train-irreversible) below.
3. **Merge and release the code**, in the order of the table above (operator, cli, agent-skills, wrapper, observability). Images and charts publish. Nothing is applied to the cluster yet.

## PR-A: pause, declaratively, and only that (reversible)

One `tatara-helmfile` PR, exactly one line:

```yaml
# values/tatara-operator/default.yaml
replicaCount: 0
```

Nothing else. No image pins, no chart-version bumps, no chat change.

!!! warning "kubectl scale is undone by the release - PR-A must be alone"
    The operator chart carries the CRDs (`charts/tatara-operator/templates/crds.yaml`). Any `helmfile apply` is a `helm upgrade`: if PR-A bundled the pause with the new chart version, that single apply would land the new CRD schemas over the live Task population **before the wipe** - the point of no return, one PR too early. `kubectl scale --replicas=0` is not used at all: it is an imperative override that the very next `helmfile apply` resets back to 3, mid-cutover, against a half-wiped world. PR-A commits the pause instead, so it survives every apply until PR-C explicitly reverses it.

Verify: 0/0 operator pods.

## Step 2: cleanup (reversible - nothing is ever deleted from SCM)

Run the `tatara-platform-reset` skill. Dry-run first; it shows the close-list before it acts. Across both projects' 15 repos it:

- closes every bot-authored open issue (human-authored open issues survive - the sweep re-mints them as backlog Tasks after cutover)
- closes every open agent PR/MR and deletes its head branch
- strips the operator's `tatara-*` labels
- leaves closed history untouched

!!! warning "This trips GitHub's secondary rate limit if it is not paced"
    15 repos times (close bot issues + close PRs + delete branches + strip labels) is 300-600 content-creating mutations against a limit of 80/minute and 500/hour. Run unpaced, it 403s partway through and leaves the cleanup half-applied - the worst possible state to carry into a window that has no rollback. `tatara-platform-reset` is paced (at most one mutation/second, with jitter), idempotent, and resumable from a progress ledger: a 403 pauses it, re-running it picks up where it left off and never double-acts.

## Step 3: verify (the last reversible moment)

Confirm all of the following before touching PR-B:

- operator at 0 replicas
- the reset ledger is complete
- all five component releases (operator, cli, agent-skills, wrapper, observability) are green in `main`
- the `tatara-chat` <!-- stale-ok: tatara-chat --> Postgres dump exists, or the loss is signed off in writing
- both agent-image pins are ready to land: `values/project-tatara/common.yaml` and `values/project-infrastructure/common.yaml`
- the fifth pin is ready: the `tatara-project` chart, at both `helmfile.yaml.gotmpl:83` and `:95`
- both `values/project-*/common.yaml` are migrated to the new vocabulary (see [PR-B item (e)](#pr-b-the-train-irreversible) below), and a `helmfile template` diff confirms the rendered `Project` CR carries `maxConcurrentAgents: 5` - every one of these migrations fails silently if skipped
- PR-B removes `--rollback-on-failure` from `helmDefaults.syncArgs` globally, and `helmfile build` passes
- PR-B edits the CRD-adopt pre-hook: adds `issues`, adds `mergerequests`, drops `subtasks` <!-- stale-ok: subtask -->

Once every box above is checked, the next step is irreversible.

## Step 4: the wipe (irreversible)

The wipe precedes PR-B, so the new CRDs land on a clean world instead of over the live Task population. Delete every `Task`, `Subtask`, and `QueuedEvent` object, then delete the `Subtask` CRD itself: run `kubectl delete tasks,subtasks,queuedevents --all -n tatara`, then `kubectl delete crd subtasks.tatara.dev`. <!-- stale-ok: Subtask, subtasks -->

!!! warning "The Subtask CRD survives a plain upgrade" <!-- stale-ok: Subtask -->
    `charts/tatara-operator/templates/crds.yaml` carries `helm.sh/resource-policy: keep`, so helm will not remove it on its own. The `kubectl delete crd` above is an explicit, required step - it does not happen by itself.

## PR-B: the train (irreversible)

One `tatara-helmfile` PR containing all of the following, together:

**(a) The new operator chart version** - it carries the new CRDs, `Issue` and `MergeRequest`.

**(b) Rollback disabled, globally.** Every `helmfile apply` already runs with `--rollback-on-failure` set in `helmDefaults.syncArgs` (`helmfile.yaml.gotmpl:9-10`). There is no per-release `syncArgs` key to scope it with instead, and `atomic: false` cannot countermand a raw arg string - so the only fix is a global one:

```diff
 helmDefaults:
   syncArgs:
-    - --rollback-on-failure
     - --force-conflicts
```

Left in place, any failure during PR-B's own apply would trigger an automatic rollback nobody asked for - and a rollback of a CRD present in both revisions is an **update**, not a skipped deletion. `resource-policy: keep` only skips deletion. That update would patch the new `Issue`/`MergeRequest` schema back to the old one, over the CRs the new operator just wrote. **PR-C restores this line once the operator is back at `replicaCount: 3`.** Verify the removal with `helmfile build` before merging either PR - it strict-decodes the state file, so a stray or missing key errors loudly instead of being silently ignored.

**(c) The agent image pin, in both files** - `values/project-tatara/common.yaml:38` and `values/project-infrastructure/common.yaml:37`. `project-infrastructure` is the GitLab project, and its repos include `tatara-helmfile` itself: miss this pin and every infrastructure Task starts failing on a contract mismatch.

**(d) The `tatara-project` chart pins, at both `helmfile.yaml.gotmpl:83` and `:95`.** The `Project`/`Repository` CRs are rendered by a separate chart from the operator's own. Miss this pin and both `Project` CRs stay on the old vocabulary, silently.

**(d2) The CRD-adopt pre-hook** (`values/tatara-operator/hooks/crd-adopt.tatara-operator.pre.sh:20-24`) - a hardcoded CRD list the hook uses to adopt pre-existing CRDs into the helm release. `projects.tatara.dev`, `repositories.tatara.dev`, `tasks.tatara.dev` and `queuedevents.tatara.dev` are unchanged; PR-B adds `issues.tatara.dev` and `mergerequests.tatara.dev`, and drops `subtasks.tatara.dev`. <!-- stale-ok: subtask -->
A CRD missing from this list is never adopted; a deleted CRD still listed makes the hook operate on something that no longer exists.

**(e) Migrate both `values/project-*/common.yaml` to the new vocabulary.** Every line below fails silently, not loudly, if skipped:

| Old key | New key | Failure mode if missed |
|---|---|---|
| `maxConcurrentTasks: 5` <!-- stale-ok: maxConcurrentTasks --> | `maxConcurrentAgents: 5` | structural pruning drops the old key; the kubebuilder default of 3 applies - concurrency silently cut from 5 to 3 |
| `modelByKind`/`effortByKind` keyed on `triageIssue` <!-- stale-ok: triageIssue --> | rekeyed on the surviving agent kinds | a `map[string]string` ignores a stale key rather than rejecting it - every kind falls back to the Opus/high default, the exact cost regression this migration exists to prevent |
| `approvalLabel: tatara/awaiting-approval` <!-- stale-ok: approvalLabel --> | deleted | the field no longer exists on the CRD |
| `maxOpenTasks: 6` | unchanged | kept as-is; verify it survives the migration as a neighbour of the renamed fields |

**(f) `tatara-chat`: `installed: false`.** <!-- stale-ok: tatara-chat --> Keep the release block - do not delete it. `helmfile apply` does not prune a release whose block is removed from the state file; deleting the block orphans the release, and it keeps running as a zombie while the operator de-references it. `installed: false` is the only declarative way to trigger `helm uninstall`, and that uninstall is also what deletes the CNPG `Cluster` CR - see the [pre-flight backup](#pre-flight-reversible) above.

**(g) The observability alerts and dashboards.**

Merge and apply. The operator is **still at `replicaCount: 0`** when this PR merges - nothing runs yet.

Verify: `helm list -n tatara` no longer shows `tatara-chat`; <!-- stale-ok: tatara-chat --> its pods and its CNPG `Cluster` are gone. Verify both `Project` CRs show `maxConcurrentAgents: 5`, not 3.

## Step 6: de-reference chat

Verify the operator release carries no remaining reference to `tatara-chat` <!-- stale-ok: tatara-chat --> anywhere: the pod env, the per-Project Ingress path, the `NetworkPolicy` egress rule, the `/readyz` tool-surface probe, and `deploy_supervision.go`'s release-artifact map. If any reference survives, the operator reconciles against a Service that no longer exists and the probe alerts forever.

## PR-C: start (the moment of truth)

Third `tatara-helmfile` PR, two changes:

```yaml
# values/tatara-operator/default.yaml
replicaCount: 3
```

```diff
 # helmfile.yaml.gotmpl:9-10
 helmDefaults:
   syncArgs:
+    - --rollback-on-failure
     - --force-conflicts
```

Merge. The operator comes up against a clean world, and every future apply is atomic again. This is a real PR with a real review - not an imperative `kubectl` command the next apply would silently erase.

## Step 8: observe

Three things to watch after PR-C lands:

1. **`operator_agent_contract_mismatch_total`.** Any non-zero value means the release train is skewed - check the infrastructure project specifically, since its agent-image pin lives in a second file.
2. **The sweep minting `parked(backlog-sweep)` Tasks at 5 per pass, with zero pods.** This is the surviving human-authored issue backlog being mirrored in, not a malfunction.
3. **A post-deploy check that the eight rewritten alerts fire on synthetic no-data**, rather than reporting silently OK.

## Also delete

PR-C's own diff should also remove the now-dead `tatara-chat` <!-- stale-ok: tatara-chat --> helmfile files entirely, once the release block has done its `installed: false` job and the uninstall has been verified.
