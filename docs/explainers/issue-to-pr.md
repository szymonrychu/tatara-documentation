---
title: From Issue to PR
---

# From Issue to PR

*Follow one GitHub issue through the whole tatara machine - from the moment you click "Submit" to the moment the PR is merged and the issue closes itself.*

The four labels tatara manages on your issue are a live status bar. At every stage below you will see which label is active and why.

| Label | Default name | What it means |
|-------|-------------|---------------|
| Brainstorming | `tatara-brainstorming` | Agent is deciding what to do |
| Approved | `tatara-approved` | Approved for implementation |
| Implementation | `tatara-implementation` | Code is being written |
| Declined | `tatara-declined` | Will not be implemented |

Label names are configurable in `Project.spec.scm.brainstormingLabel` etc. The defaults above are used here throughout.

---

## Step 1 - You open an issue

You create a GitHub issue in any repository enrolled in your tatara Project. The title and body are your only inputs; tatara reads them verbatim.

**Issue label:** *(nothing yet)*

If you want tatara to start immediately and skip straight to writing code - because the change is already well-defined and you trust it - add the *trigger label* (configured in `Project.spec.triggerLabel`, commonly `tatara`). That fires the webhook path, which skips `clarify` and spawns an `implement` pod directly. For everything else, the periodic issue scan (default: hourly) picks up the issue on its next pass.

This page follows the full path - issue opened, no trigger label, `clarify` runs first.

---

## Step 2 - The operator creates a Task

The issueScan reconciler sees a new open issue in an enrolled repository and creates (or joins) a **Task** custom resource. A Task is the durable, per-project unit that carries state across the whole implementation stream - branch names, PR numbers, CI results, token counts, and (once a PR lands) the deploy supervisor's `lifecycleState`. There is one project-scoped Task per implementation stream; a new issue starts (or joins) that Task's umbrella [`WorkItems` ledger](../reference/task.md#task-umbrella-and-the-workitem-ledger) rather than getting its own isolated object.

```yaml
apiVersion: tatara.dev/v1alpha1
kind: Task
metadata:
  name: myproject-42-a3f9d  # deterministic from project + issue ref
spec:
  kind: clarify
  source:
    issueRef: "owner/myrepo#42"
    number: 42
    title: "Support dark mode in the dashboard"
```

The operator applies the first label to the issue.

**Issue label:** `tatara-brainstorming`

---

## Step 3 - `clarify` reads the issue

The operator checks that the project's memory service is ready (tatara-memory holds a code knowledge graph built from recent repository ingests), then schedules a **wrapper pod** running the `clarify` kind - a container running `tatara-claude-code-wrapper` with `tatara-cli` as its MCP server. The pod:

1. Clones the repository.
2. Fetches the issue title, body, and all existing comments via the GitHub API.
3. Loads the code knowledge graph from tatara-memory.
4. Reads the prior handoff summary via `get_handoff` (keyed by the task's conversation key) if a previous pod left one; on first contact there is none.
5. Presents everything to the agent as a structured prompt and waits for a decision.

`clarify` has read-only access to the repository and issue at this stage - it does not write code. See [Clarify](../workflows/clarify.md) for the full workflow.

---

## Step 4 - `clarify` decides: should we do this?

The agent reads the issue and the codebase and calls the `issue_outcome` MCP tool with one of three answers.

### Path A - decline

The issue is out of scope, already fixed elsewhere, or not actionable. The agent calls `issue_outcome(action="close", comment="...")`. The operator:

- Swaps the label to `tatara-declined`.
- Posts the reason as a comment.
- Closes the issue.
- Marks the issue's WorkItem done within the Task.

**Issue label:** `tatara-declined` - then issue closes.

### Path B - discuss

The issue needs clarification, a design choice, or human input. The agent calls `issue_outcome(action="discuss", comment="...")`, posting its questions. The operator:

- Keeps the `tatara-brainstorming` label.
- Tears down the pod (no cost while waiting).
- Starts a 60-minute idle timer.

**Issue label:** `tatara-brainstorming` (waiting)

When you reply, the webhook fires and `clarify` runs again immediately: the agent re-reads the full comment thread and tries again. This loop continues until you apply the trigger label (skip to `implement`) or 60 minutes pass with no activity (the Task parks, resumable by commenting again).

!!! note "Self-approve guard"
    If the issue was filed by the tatara bot itself (e.g., from a brainstorm proposal), `clarify` will not auto-approve implementation without a human engaging first. It posts a discuss outcome and waits. Issues opened by a human bypass this guard.

### Path C - implement

The agent decides this is worth building. It calls `issue_outcome(action="implement")`. The operator swaps the label.

**Issue label:** `tatara-approved`

The operator schedules an `implement` pod next.

---

## Step 5 - `implement` writes the code

A new pod spawns running the `implement` kind (see [Implement](../workflows/implement.md)). It may work across every affected repo under the Task's umbrella - a run is not scoped to just the repo the issue was filed in. The operator sets the implementation label.

**Issue label:** `tatara-implementation`

The agent:

1. Re-reads the issue and its conversation thread.
2. Queries the code knowledge graph for relevant context.
3. Plans the change - for large or cross-repo work it tiers out sub-agents (see [subagent tiering](../workflows/implement.md#subagent-tiering)).
4. Writes code, commits, and pushes to branch `tatara/task-<taskname>`.
5. Calls [`change_summary`](../workflows/implement.md) with a PR title, PR body, what was delivered, a required **change significance** (`major`/`minor`/`patch`, which drives the semver tag on push-CD repos - see Step 9), and optionally what remains.

The operator then opens the pull request. The PR body contains `Closes #42` so GitHub will auto-close the issue on merge. If the agent reported remaining scope, the operator opens a follow-up issue to track it.

If the agent exhausted a significant portion of its context window during a long implementation run, the operator may ask it to write a handover document (`submit_handover`) before the session ends. The next session starts by reading that document rather than re-reading everything from scratch.

---

## Step 6 - CI runs; once green, `review` is invoked

The `implement` pod is gone once it finishes pushing. Every 30 seconds the operator checks the PR's CI pipeline.

**Issue label:** `tatara-implementation` (unchanged)

| CI result | What the operator does |
|-----------|----------------------|
| Pending | Waits and polls again |
| **Green** | The PR becomes eligible for `review`; the operator schedules a review pod |
| **Red** | Re-spawns an `implement` pod with the failing check output as context; agent fixes and pushes; repeat |
| Deadline (60 min) | Posts a comment on the PR and parks the Task |

If CI never goes green within the deadline the PR is left open for a human and the Task parks. An iteration counter (capped at 10 by default) provides a hard backstop so the fix loop cannot run indefinitely.

---

## Step 7 - `review` approves, the deploy supervisor merges

A `review` pod spawns against the opened PR (see [PR / MR Review](../workflows/review.md)). It
checks the diff read-only and calls one of three verdicts:

- **approve** - applies `tatara-approved` to the PR and posts a native PR approval. This is the
  entire signal the deploy supervisor needs; no separate human sign-off step runs in the shipped
  default.
- **request_changes** - re-adds `tatara-implementation`, invoking `implement` again with the
  review feedback as context.
- **comment** - posts feedback without blocking; the Task stays where it is.

**Issue label:** `tatara-implementation` (unchanged) until review approves.

Once required checks are green **and** `tatara-approved` is present, the
[deploy supervisor](../workflows/deploy-supervisor.md) - not any agent pod - merges the PR. If
the merge hits a conflict, the deploy supervisor re-invokes `implement` to rebase and push again;
`review` then re-reviews the updated head.

---

## Step 8 - Post-merge CI is watched, then the issue closes

After the squash merge, the deploy supervisor polls the default-branch pipeline for the merge commit SHA every 30 seconds.

| Result | What the operator does |
|--------|----------------------|
| Pending | Waits |
| **Green** | For a change with no declared significance, closes issue #42 (idempotent if `Closes #N` already did it) and sets `lifecycleState` to done. For a push-CD-eligible change (a significance was declared), `lifecycleState` instead moves to deploying - see Step 9 |
| **Red** | Clears the merged-PR fields and re-invokes `implement` to open a brand-new PR with the fix |

Once the issue closes, all managed labels disappear with it.

**Issue:** closed.

---

## Step 9 - Opt-in push-CD: tatara ships the change

Repositories wired into semver push-CD (the tatara platform's own components deploy themselves this way) take one more hop before the issue closes. When the agent declared a `change_significance` on `change_summary` - or a human set a `semver:<level>` label on the PR - the merged change is **push-CD-eligible**, and after main CI goes green the Task enters the pod-less **Deploying** state instead of going straight to Done.

No agent pod runs during Deploying. The operator supervises a release cascade:

1. The bot-authored PR was auto-merged on green required checks (agents never merge their own PRs).
2. CI cuts a semver tag from the declared significance (`major`/`minor`/`patch`) and publishes the versioned artifact - container image and/or chart - at `vX.Y.Z`.
3. The new version pin propagates to the parent repo, and finally to `tatara-helmfile`.
4. `tatara-helmfile` applies the pin to the cluster on merge (GitOps, via an in-cluster runner).
5. On a successful apply the operator closes issue #42 and moves the Task to **Done**.

If the apply does not land within the deploy budget, the Task parks recoverable with a `deploy-timeout` reason, so a stuck release surfaces for a human rather than hanging silently. See [Deploy Supervisor & Semver Push-CD](../workflows/deploy-supervisor.md) for the full mechanics.

**Issue label:** `tatara-implementation` (unchanged) until the apply lands, then the issue closes.

This path is opt-in per repository. Repos not wired into push-CD take the Step 8 Green path straight to Done, and everything above stays exactly the same up to that point.

---

## Full sequence diagram

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant GH as GitHub
    participant Op as tatara-operator
    participant Pod as Agent Pod
    participant Rev as review Pod
    participant Mem as tatara-memory

    Dev->>GH: Open issue #42
    GH-->>Op: issueScan (periodic)
    Op->>Op: Create Task (kind=clarify)
    Op->>GH: Add label: tatara-brainstorming

    Note over Op,Pod: clarify pod running

    Op->>Mem: Wait for memory-ready
    Mem-->>Op: Ready
    Op->>Pod: Schedule clarify pod
    Pod->>GH: Fetch issue + comments
    Pod->>Mem: Query code graph
    Pod-->>Op: issue_outcome = "implement"

    Op->>GH: Swap label: tatara-approved
    Op->>GH: Swap label: tatara-implementation

    Note over Op,Pod: implement pod running

    Op->>Pod: Schedule implement pod
    Pod->>GH: Clone repo
    Pod->>Pod: Write code, commit
    Pod->>GH: Push branch tatara/task-xyz
    Pod-->>Op: change_summary (title, body, scope)
    Op->>GH: Open PR (Closes #42)

    loop Poll CI every 30 s
        Op->>GH: GetPRState (CI status)
        GH-->>Op: pending ... success
    end

    Op->>Rev: Schedule review pod

    Note over Op,Rev: review pod running

    Rev->>GH: Read PR diff (read-only)
    Rev-->>Op: verdict = "approve"
    Op->>GH: Apply label: tatara-approved (on PR)
    Rev->>GH: Post native PR approval

    Op->>GH: Squash-merge PR (required checks green + tatara-approved)

    loop Poll default-branch CI
        Op->>GH: GetCommitCIStatus (merge SHA)
        GH-->>Op: success
    end

    Op->>GH: Close issue #42
```

---

## Labels as a status bar

Here is the same journey as a label timeline.

```
Issue #42 opened
  [tatara-brainstorming]  -- clarify running
  [tatara-approved]       -- clarify: will implement
  [tatara-implementation] -- implement running, CI running, then review running
  (issue closed)          -- merged + main CI green
```

If the agent needs clarification:

```
  [tatara-brainstorming]  -- clarify: discuss (questions posted)
  [tatara-brainstorming]  -- Waiting for your reply...
  (you reply)
  [tatara-brainstorming]  -- clarify re-running
  [tatara-approved]       -- Approved, proceeding
  [tatara-implementation] -- ...
```

If the issue is out of scope:

```
  [tatara-brainstorming]  -- clarify running
  [tatara-declined]       -- Declined, reason posted
  (issue closed)
```

---

## What to do when a Task is Parked

A Task enters **Parked** when the operator cannot proceed without human input: the babysit deadline expired, a merge conflict was not resolved, the iteration cap was hit, or the agent explicitly declined with `decline_implementation`. The operator posts a comment explaining what stopped it, unless the [comment turn-taking gate](../operations/security/bot-identity.md#comment-turn-taking-gate) withholds it - e.g. a Task that keeps parking on the same unanswered thread stops re-commenting after the first note.

Your options:

- **Comment on the issue** to reactivate it. The Task re-enters `clarify` with the full thread as context.
- **Apply the trigger label** to skip `clarify` and go straight to `implement`.
- **Fix the underlying problem** (e.g., merge conflict, failing test) and then apply the trigger label.

While Parked the Task consumes no resources. It waits indefinitely until you act.
