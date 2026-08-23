---
title: Watch One Run
description: One real tatara run in tatara's own repository on 2026-08-17 - an alert firing, a maintainer typing two words to approve, a review round that caught a real bug in the agent's own diff, an agent that declined half a finding with reasons, and the deploy.
---

# Watch One Run

Read this page and you will have watched tatara do a complete piece of work, with nothing staged for the demo. It happened in tatara's own repository on **2026-08-17**, and you can open the whole thread yourself: [tatara-operator#621](https://github.com/szymonrychu/tatara-operator/issues/621) and the pull request it produced, [#624](https://github.com/szymonrychu/tatara-operator/pull/624). Every quoted block below is copied out of that thread.

No person filed the issue. A Grafana alert fired, tatara's own incident agent investigated it read-only, and the agent opened the tracker. Tatara has been enrolled as its own first project since early on, so its backlog is worked by the same loop that would work yours - see [tatara Builds tatara](../concepts/self-improvement.md). One maintainer wrote one comment on the whole thread, and it is two words long.

The middle of the run is the part worth your time. A second pod reviewed the fix and found that it could mark somebody else's finished work rejected and say so publicly. The agent took that finding, and then declined half of a different one and put its reasons on the record.

---

## An alert fires and the agent files a tracker

The alert is an ordinary Grafana rule over the operator's own error logs: at least two `ERROR` lines carrying the same `msg` within an hour. This firing started at `2026-08-17T14:01:50Z` and was recurrence nineteen. Eleven and a half minutes later the incident agent had finished its investigation and opened issue #621 with the diagnosis in the body.

```text title="tatara-operator#621, szymonrychu-bot, 2026-08-17T14:13:19Z"
**TRUE POSITIVE, and a DIFFERENT root cause from #597 under the same rule.** #597's failure mode contributes **0** lines to this firing. All **53** `Reconciler error` lines in the window are `controller=mergerequest`, from **one** call site:
```

The defect is small and precise, and it lives one layer above an existing fix rather than in new code. A previous change had already taught the operator to stop retrying a permanently gone upstream pull request. It guarded the writes:

```text title="same comment, two spans"
**ROOT CAUSE - why does a permanent 404 loop forever when #436 already fixed exactly that?** Because the guard is at the wrong layer.
[...]
The mirror thread read at `:106-114` runs **before** that loop in the same `Reconcile` and returns raw, so it short-circuits the entire guarded region.
```

Three deleted pull requests in an unrelated repository were retrying forever, at a backoff that had settled just above the alert threshold and would never fall below it. The agent went further than the diagnosis and wrote down the thing that would make a naive fix dangerous:

```text title="same comment"
A 404 on `ListPRComments` proves the PR is gone; it does not prove the **repo** is gone. `-69` succeeding mid-incident [Q7] is the discriminator, and a repo-wide 404 (bad token, deleted repo) must not silently close every mirror in that repo.
```

At this point the issue carries two labels, both written by the operator: `tatara-alert-rule=759e75110b9af5cc` at `14:13:21Z`, and `tatara-parked` at `14:13:30Z`. The first is the provenance, in machine-readable form, so anything reading this issue later can tell which alert rule produced it without parsing prose. Open the issue and you will count three, because a third goes on six hours further down this page: `tatara-approved`, at `20:17:31Z`, in the same second the approval gate opened. Read it as a receipt rather than a switch. The operator writes it after it has accepted an approval, and it is one of four phase labels the operator projects onto an issue; putting it on an issue yourself approves nothing, and the webhook explicitly refuses to let it trigger any work. <!-- stale-ok: tatara-approved -->

## The platform holds it, and waits for a person

Seven seconds after the agent submitted its outcome, the operator posted this:

```text title="tatara-operator#621, szymonrychu-bot, 2026-08-17T14:13:29Z"
tatara has stopped working this issue: task `incident-qe-c0bdeaf8ab61daab-h9dsx` ended in `done` (``).

The issue stays open and is labelled `tatara-parked`, so the platform will not spend another agent on it until a human replies here. Comment to pick it back up.
```

Read the state, not the sentence. That task ended in `done`, and the reason inside the backticks is empty. Nothing failed. An incident Task is read-only and has exactly one job, which is to investigate and file what it found, and the operator retired it the moment it did: `refined -> done` at `14:13:22Z`, the notice above at `14:13:29Z`, the `tatara-parked` label at `14:13:30Z`.

What the label holds is a different question. Writing code is a different Task, and the issue that would drive one was filed by a bot on an alert, so nobody has yet said it should be worked. The operator's backlog sweep did mint that second Task, an hour and twenty minutes later, and minted it already parked:

``` { .text title="operator log, 2026-08-17T15:35:36.775Z, field values verbatim" }
msg="sweep: minted task for orphan issue"
resource_id=mt-i-tatara-operator-621-12819ff6cd33b54f
repo=tatara-operator  number=621  state=new  park_reason=backlog-sweep
```

So two Tasks stopped at the same place for the same reason: an alert is not an instruction. That is what `tatara-parked` on an open issue means. The platform is deliberately not working it, and it has not forgotten it.

!!! note "The label does not come back off"

    Nobody removed `tatara-parked` on this run. The Task was unparked in the cluster within three seconds of the maintainer's comment and the work ran to completion, but the label stayed on the issue through the merge and is still there now that it is closed. Read `tatara-parked` on an OPEN issue as "not being worked". Do not read anything into its presence on a closed one.

## A maintainer types two words

Nothing is implemented until a person with commit rights says so. At `20:08:03Z`, five hours and fifty-five minutes after the hold went on, one did, in full:

```text title="tatara-operator#621, szymonrychu, 2026-08-17T20:08:03Z"
Fix it
```

That is the entire approval, and it is the only comment a human wrote anywhere on this run. There is no form, no button, and no state a maintainer has to go and set somewhere else. A comment in the thread, from an account the project lists as a maintainer, is what opens the gate. Three seconds later the operator unparked the waiting Task and moved it out of `new`.

## The operator checks the approval itself

The agent does not get to assert that it was approved. The operator goes and finds the comment, reads the author against the project's maintainer set, and writes a receipt naming exactly which comment it acted on:

```text title="tatara-operator#621, szymonrychu-bot, 2026-08-17T20:17:32Z"
Approval accepted for `mt-i-tatara-operator-621-12819ff6cd33b54f`.

- approver: `szymonrychu`
- cited comment: `5319672667`
- quote: "Fix it"
- plan note: `n-0240f8c8ed7c3e22`

If this is not what you meant, say so on this thread: the operator re-reads it every turn.
```

Comment `5319672667` is the `Fix it` above, and the quoted string is the whole of it. The citation is to a specific artifact, not to a general sense that permission exists somewhere. If the reading is wrong, you reply on the thread and the operator picks the correction up on its next turn.

## The plan, then the code

The receipt landed eight seconds after the plan, because the first thing the agent does once through the gate is say what it intends to do, and stop.

```text title="tatara-operator#621, szymonrychu-bot, 2026-08-17T20:17:24Z, three spans"
## Plan

Single repo: `tatara-operator`. No other repo in scope.
[...]
### Tests (red first)
[...]
### Explicitly NOT in scope
```

The plan named the guard it would add, the one confirming repository read it would make before believing any 404, and five tests to write red first. It also named what it was deliberately leaving alone, including the three broken objects the incident report had told a human to go and delete:

```text title="same comment"
- Deleting the three live `mr-mtg-decks-62/64/71` CRs. This fix converges them on the next cadence tick.
```

The first commit landed at `20:37:56Z`, and the pull request opened at `21:03:55Z`: [#624](https://github.com/szymonrychu/tatara-operator/pull/624), titled `fix(mirror): stop a permanently-gone upstream thread looping forever on the mirror READ`.

## The Task walks its states

None of the state machine shows up on GitHub. The operator logs every transition, and this is the run reconstructed from those logs:

``` { .text .annotate title="reconstructed from the operator's state-transition logs" }
2026-08-17T15:35:36.755Z  (create) -> new                            # (1)!
2026-08-17T20:08:06.176Z  new -> refined                             # (2)!
2026-08-17T20:17:31.159Z  refined -> under-implementation            # (3)!
2026-08-17T21:05:10.755Z  under-implementation -> awaiting-review    # (4)!
2026-08-17T21:24:55.355Z  awaiting-review -> under-implementation    # (5)!
2026-08-17T22:31:23.410Z  under-implementation -> awaiting-review
2026-08-17T22:45:46.411Z  awaiting-review -> merged                  # (6)!
2026-08-17T22:45:51.255Z  merged -> deployed
2026-08-17T23:04:19.370Z  deployed -> done
```

1.  Minted by the backlog sweep and parked in the same write. It sat here for four and a half hours.
2.  Three seconds after the maintainer's comment landed. No pod runs at `new`; the operator triages the Task itself.
3.  `refined` is the approval gate. A pod is up here, and this is where the plan and the approval receipt were written.
4.  Not "the agent pushed". The operator held the submission until CI was green at the pushed head, then released it to review.
5.  The review verdict was changes requested, so the same Task went back to implementation with a fresh pod.
6.  `merged` names the merge phase, not a merged pull request. The operator owns the merge from here.

Two facts a newcomer usually gets wrong are both visible in that list. There is no separate review Task: the same Task moves between `under-implementation` and `awaiting-review`, and the implement pod is torn down and replaced by a review pod on the same work. And only the operator writes these states - an agent submits an outcome and the operator decides what the outcome means. The full table is in [Task State Machine](../reference/task-stages.md).

## The review sends it back, and catches a real bug

Round 1 arrived at `2026-08-17T21:24:53Z` against commit `56bec78`, with four inline findings. This is the entire body of it:

```text title="tatara-operator#624, review by szymonrychu-bot, 2026-08-17T21:24:53Z"
<!-- tatara-review round=1 sha=56bec786028d276705f8e5a7a816925194e5b2c2 -->
## Review: changes requested
```

The finding that mattered was about a function the diff had just added. The intent was defensive: when a read of an upstream pull request comes back 404, mark the local mirror gone instead of retrying it into the ground. The reviewer worked out what that does to a mirror whose upstream was not deleted, but merged.

```text title="tatara-operator#624, round 1 inline finding on internal/controller/mergerequest_controller.go, four spans"
`markMergeRequestThreadGone` writes `state="closed"` over `"merged"`, and nothing stops it.
[...]
A 404 on that read flips `merged -> closed`. `stage.AllMRsMerged` then goes false and `terminalMREdge` / `ownMRsShippedEdge` (`reviewpost.go:425`, `:466`) finalize the owner Task `rejected(mr-closed-externally)`
[...]
with `WithTerminalIssueRelease`'s public comment
[...]
Minimum: refuse when `mr.Status.State == "merged"`. Better: skip the mirror sync entirely for terminal mirrors, which also retires the forever re-probe below.
```

Follow that chain to the end, because it is the reason the loop exists. A patch written to quiet a noisy log line would, in one narrow case, have taken a piece of finished work, recorded it as rejected, and posted a terminal comment saying so on the issue that drove it. None of that is visible in the diff. Seeing it takes a reader who will follow a status field into the state machine that reads it.

The reviewer also explained why the diff's own safety check would not have caught this. The new code probes the repository before believing a 404, on the theory that a deleted repository 404s everything and must never close every mirror in it at once. Against this case the probe answers cheerfully and wrongly:

```text title="same finding"
The probe cannot see it by construction: repo deleted and recreated under the same name, or `Repository.spec.URL` retargeted, makes the repo READABLE while every number 404s
```

## The agent answers its reviewer, and declines part of a finding

An agent that treats findings as a checklist narrows the guard, because narrowing is what it was asked for. Watch for that in what follows, because it is the property being tested here and it is a safety property, not a matter of manners. An agent that concedes to any confidently stated claim can be walked into a worse system by a reviewer that is wrong, and the loop is only worth running if the implementing side can tell a correct finding from an incorrect one.

The reviewer is a different pod with no memory of writing the code, so what arrives back at the implementing pod is a set of claims, not orders. Fifty minutes later the agent answered all four, one comment each. On the finding above it took both of the offered fixes rather than the cheaper one, and named the part of the review that moved it:

```text title="tatara-operator#624, szymonrychu-bot, 2026-08-17T22:13:06Z, two spans"
Fixed, both halves, in `52ac1a5`. Took the "better" option and the "minimum" one, because they cover different call paths.
[...]
Your point that the probe's precondition IS the failure mode is the part that changed my model: a probe is a filter, never a proof, so the disposition it gates has to independently refuse whatever it cannot afford to get wrong.
```

In the same comment it corrected one of the reviewer's line citations, kept the fix regardless, and set out the windows in which the defect was real anyway. Agreeing with a finding and agreeing with every sentence supporting it are not the same act.

On the second finding it went the other way. The reviewer wanted a newly added guard narrowed. The agent declined that half, in public, with two reasons:

```text title="tatara-operator#624, szymonrychu-bot, 2026-08-17T22:13:13Z, four spans"
Not narrowing the guard, for two reasons:
[...]
**It cannot be narrowed from here.**
[...]
Narrowing would mean pushing the predicate into the driver and classifying per intent, which is a different change than the one this PR is scoped to.
[...]
**410 stays.** The MR side excludes it because its disposition terminates somebody's Task publicly.
```

It took the other half of that same finding, which was to add the metric the reviewer had noticed was missing, and then wrote the whole disagreement into a comment at the call site so the next reader does not have to find this thread to understand the asymmetry.

Nothing in the review forced any of that. The agent compared what the two guards do when they are wrong, found the answers genuinely different, and said so somewhere you can argue with it.

## Round 2 approves

The rewritten diff went up at `22:14:36Z`, the operator held the submission until CI was green at that head, and round 2 came back eighty minutes after round 1:

```text title="tatara-operator#624, review by szymonrychu-bot, 2026-08-17T22:45:44Z"
<!-- tatara-review round=2 sha=67b98ca16b9d29382683325b4b7023486a0be650 -->
## Review: approved
```

The approval still carried a finding, about a log line that now claims more than the code behind it does. It did not block. The reviewer could not attach it to any line inside the diff, so rather than drop it, it said why and put it in the body:

```text title="same review, body"
GitHub can only attach an inline comment to a line inside the pull request diff. These findings point outside it (or carry no line), so they are reported here:
```

Two mechanics in that pair are worth knowing before you read any tatara review. Neither round is a native GitHub approval or change request. Both are posted with GitHub's `COMMENTED` state, because GitHub refuses an `APPROVE` or `REQUEST_CHANGES` review from the account that authored the pull request, and tatara authored this one. The verdict rides in the body, behind the `tatara-review` marker the operator writes, and the operator is what reads it back and acts on it at merge time. GitHub's own approval machinery is never in the loop, and no agent posts the review either: the review pod returns a verdict and findings, and the operator writes them to the forge.

The other is the round counter. It advances only on a changes-requested verdict, so an approval consumes no round, which is why this pull request has a round 1 that asked for changes and a round 2 that approved. There is no cap on how many rounds the loop may take. `maxReviewRounds` is gone from the code, along with the park reason its cap used to produce, and the pair is bounded instead by a single hard 24-hour residency limit on the Task. See [PR / MR Review](../workflows/review.md) for the verdict contract.

## Merge, tag, deploy

The rest ran with no person in it.

| when (UTC) | what |
|---|---|
| `21:05:11` | operator applies the `semver:patch` label to #624 |
| `22:45:49` | operator merges #624 as `6f4f5e24` |
| `22:45:51` | issue #621 closes |
| `23:01:31` | `tatara-helmfile#425` merges, moving the operator pin from `3.4.0` to `3.4.1` |
| `23:04:18` | operator posts the closing comment on #621 |

The label goes on before the merge, not after, and the ordering is load-bearing: CI cuts the release tag from the label at the merge commit, so a merge that landed first would be a release that never got tagged. Here the gap is an hour and forty minutes, because the operator writes the label when it releases a submission to review rather than when it decides to merge. Tag `v3.4.1` points at `6f4f5e24551205a96091d7ece339a32a747cf238`, which is the merge commit of #624.

No agent merged this and none could have. Agent pods hold no forge token, and the tool surface handed to an agent contains no merge action at all - it is a structural absence, not an instruction the agent is trusted to follow. The operator performs the merge itself, against the exact head commit the review approved, with CI green at that commit.

```text title="tatara-operator#621, szymonrychu-bot, 2026-08-17T23:04:18Z"
Delivered in tatara-operator!624 (v3.4.1). Closed by tatara.
```

## What the run cost

Nine hours of wall clock, from the alert at `2026-08-17T14:01:50Z` to the closing comment at `23:04:18Z`, all of it inside one day. Be clear about where those hours went: five hours and fifty-five minutes were the issue sitting open and held, waiting for a person to say yes. The delivery itself - Task unparked, plan posted, code written, reviewed twice, merged, tagged, deployed, issue closed - took two hours and fifty-six minutes.

The cost in human attention was one comment of two words. The cost in rework was one review round and the commits that answered it, which the thread records rather than hides. What that round bought was a defect that never reached anybody: a read of a deleted pull request that could have marked a merged one closed and rejected the Task that shipped it.

## One run is an anecdote

Everything above is one run, and it is the run this page chose to show you. Here is the denominator it sits in, so you can price the pick.

In the 30 days to `2026-08-18`, tatara's agents opened **147** pull and merge requests that reached a terminal state, across 15 repositories in three projects on two forges. **134 merged, 13 closed unmerged**, which is 91%. 75 of the 134 landed in repositories that are not tatara's own: tatara 59 merged and 8 closed, infrastructure on GitLab 42 and 3, mtg 33 and 2. Counted at the forges by branch prefix, where `tatara/` is agent work and the other machine producers, `cd/` and `renovate/`, are excluded.

Four things ride with that number, and it misleads without them.

**The volume is ramping steeply.** Agent merges by week were 14, 9, 29 and 76, roughly 2.6x week over week, and the last week alone is 57% of the 30-day total. The figure describes a system that just scaled, not a steady state you can extrapolate from.

**A Task is not a delivered feature.** The operator's own ledger records **227** Tasks reaching a terminal state in seven days: 197 delivered, 30 rejected. Of those 197, **84 are `review`**, which is reviewing a pull request, and **43 are `upgrade`**, which is dependency bumps and CD pin propagation. **19 are `implement`**, the kind this page followed. Publishing 197 as features shipped would be laundering, and this site does not do that.

**Rejected is not failed.** 23 of the 30 rejections are incident Tasks correctly deduplicated onto a tracker that already existed (`tracked-elsewhere`). The same window is inflated at the end by an alert burst: incident Tasks ran 2, 5, 2, 5, 4 and 2 a day, then 15 and 11 on the last two.

**Parked is mostly backlog, not damage.** 87 Tasks are parked as this is written, but 69 of them are `backlog-sweep`, which is minted-and-not-yet-run rather than failed, and 8 are `awaiting-human`, which is the designed wait this run spent five hours and fifty-five minutes in. Genuinely degraded is 10.

The telemetry window is seven days and not thirty because Prometheus retention here is 7d, so a 30-day figure from telemetry does not exist to be quoted. The query is:

``` { .text title="tatara's Prometheus, 2026-08-18" }
sum by (state) (max_over_time(operator_task_terminal_total[7d]))
```

`max_over_time` rather than `increase` because about 50 operator pods carried that counter over the week (3 replicas, some 17 rollouts) and a counter resets on every rollout; `increase` undercounts by 1.8x. It checks out against something countable by hand: the two-day figure for `done` is 87, exactly the 87 live Task CRs still in `done` that the reaper holds at `DeliveredRetention = 48h`. That last number is also the reason live CRs are not a census. What `kubectl get task` shows you is a 48-hour window, not a population.

## Where to go next

- [The Big Picture](big-picture.md) - what the pieces are and why they are arranged this way.
- [From Issue to PR](issue-to-pr.md) - the same journey as a step-by-step reference rather than a single run.
- [Prerequisites](../getting-started/prerequisites.md) - what you need before running any of this yourself.
