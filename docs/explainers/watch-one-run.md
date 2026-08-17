---
title: Watch One Run
description: One real tatara run in tatara's own repository, from 2026-08-02 to 2026-08-08 - an alert firing, a maintainer typing two words to approve, an attempt that failed and got parked instead of retried, a review round that sent the fix back, and the deploy.
---

# Watch One Run

Read this page and you will have watched tatara do a complete piece of work, with nothing staged for the demo. It happened in tatara's own repository between **2026-08-02 and 2026-08-08**, and you can open the whole thread yourself: [tatara-operator#529](https://github.com/szymonrychu/tatara-operator/issues/529) and the pull request it produced, [#550](https://github.com/szymonrychu/tatara-operator/pull/550). Every quoted block below is copied out of that thread.

No person filed the issue. A Grafana alert fired, tatara's own incident agent investigated it read-only, and the agent opened the tracker. Tatara has been enrolled as its own first project since early on, so its backlog is worked by the same loop that would work yours - see [tatara Builds tatara](../concepts/self-improvement.md). Across the six days, one maintainer wrote four comments. Two of them are shorter than this sentence.

The run is not a tidy one, and that is why it is worth your time. An implementation attempt failed outright and produced nothing. The platform stopped and waited for a person rather than trying again. A reasonable instruction from that person turned out not to apply yet, and the agent said so instead of doing something merge-shaped to comply.

---

## An alert fires and the agent files a tracker

The alert is an ordinary Grafana rule over the operator's own error logs: at least two `ERROR` lines carrying the same `msg` within an hour. It fired at `2026-08-02T01:26:50Z`. Twelve minutes later, the incident agent had finished its investigation and opened issue #529 with the diagnosis in the body.

```text title="tatara-operator#529, szymonrychu-bot, 2026-08-02T01:39:35Z"
`handleIncidentOutcome` validates `issue.title` for **non-empty only** and then ships it verbatim to the forge. GitLab enforces a hard **255-character** title cap; GitHub does not. So on a **GitLab-backed project** any agent outcome with a long title is rejected `400`, and the handler returns **502 before `o.commit()`** - no Task state written, no retry, no requeue, nothing persisted. The agent's entire investigation is dropped.
```

The defect is small and precise: agent-supplied issue titles were never clamped, so a title longer than 255 characters was rejected by GitLab and the accepted outcome behind it was thrown away.

The agent went further than a diagnosis and wrote down a way to prove itself wrong:

```text title="tatara-operator#529, szymonrychu-bot, 2026-08-02T01:39:35Z"
**Blast radius:** every `file_issue`/proposal write on a GitLab-backed project. Falsifiable prediction: this reproduces on the *next* incident or brainstorm outcome for project `infrastructure` whose title exceeds 255 chars, and never on project `tatara`.
```

The issue carries two labels: `tatara-parked`, and `tatara-alert-rule=759e75110b9af5cc`. The second one is the provenance, in machine-readable form. Anything reading this issue later can tell which alert rule produced it without parsing prose.

## It keeps firing, and the agent does not repeat itself

Over the next six days the alert re-fired eleven times, and each re-fire posted a short notification onto the same issue rather than opening a new one.

```text title="tatara-operator#529, szymonrychu-bot, 2026-08-08T08:00:53Z"
Alert re-fired 2026-08-08T08:00:51Z; labels {component=operator, grafana_folder=Tatara, homelab=true, severity=warning, system=tatara}; 11 recurrence.
```

Three of those re-fires escalated into a full re-investigation. The agent's job on an escalation is not to restate the original finding; it is to check whether the original finding is still the one firing.

```text title="tatara-operator#529, szymonrychu-bot, 2026-08-05T13:34:41Z"
Re-investigated live and read-only. Still this issue's unclamped `issue.title`. No new mechanism. Fresh evidence only below; the postmortem above still stands.
```

Each escalation also re-checked the prediction from the opening body against fresh log data, and each time it held: every failure was on the GitLab-backed project, none on the GitHub-backed one.

## A maintainer types two words

Nothing had been implemented yet, because nothing is implemented until a person with commit rights says so. On 2026-08-06 the maintainer said so, in full:

```text title="tatara-operator#529, szymonrychu, 2026-08-06T15:21:36Z"
Fix it!
```

That is the entire approval. There is no form, no button, and no state that a maintainer has to go and set somewhere else. A comment in the thread, from an account the project lists as a maintainer, is what opens the gate.

## The operator checks the approval itself

The agent does not get to assert that it was approved. The operator goes and finds the comment, reads the author against the project's maintainer set, and writes a receipt naming exactly which comment it acted on:

```text title="tatara-operator#529, szymonrychu-bot, 2026-08-08T08:30:34Z"
Approval accepted for `mt-i-tatara-operator-529-fe250353653192d0`.

- approver: `szymonrychu`
- cited comment: `5206856065`
- quote: "Fix it!"
- plan note: `n-05fa7cd2b344cec2`

If this is not what you meant, say so on this thread: the operator re-reads it every turn.
```

Comment `5206856065` is the `Fix it!` above. The receipt is dated two days after the approval, and it still cites the original comment rather than anything nearer to hand - the citation is to a specific artifact, not to a general sense that permission exists somewhere.

Alongside the citation, the agent re-checked that the bug was still real before spending anything on it:

```text title="tatara-operator#529, szymonrychu-bot, 2026-08-06T15:25:00Z, two spans of one sentence"
Go-ahead read from @szymonrychu (`Fix it!`). Re-verified at HEAD `ebb5352`
[...]
Still unfixed.
```

An approval from four days ago is not evidence that the defect survived those four days. Re-verifying at the current HEAD is how the platform avoids implementing a fix that already landed.

## The first attempt fails

The first implementation attempt produced nothing. The Task running it, `incident-qe-c0bdeaf8ab61daab-9mqvr`, ended in `failed` with the reason `operator-error`. No commit, no pull request, no partial work to pick up.

## The platform parks the issue instead of retrying

This is the beat worth reading twice.

```text title="tatara-operator#529, szymonrychu-bot, 2026-08-06T20:00:37Z"
tatara has stopped working this issue: task `incident-qe-c0bdeaf8ab61daab-9mqvr` ended in `failed` (`operator-error`).

The issue stays open and is labelled `tatara-parked`, so the platform will not spend another agent on it until a human replies here. Comment to pick it back up.
```

A system that responds to a failure it does not understand by trying the same thing again will burn your budget and fill your issue tracker while it does it, and the second attempt is no better informed than the first. Tatara stops. It says what stopped, it names the Task that failed and the reason it failed with, it leaves the issue open so nothing is lost, and it hands the next decision to a person. Until somebody replies on the thread, no further agent is spent on this issue.

That behavior is the thing to want before you let any of this near your repositories. It is also what makes the label meaningful: `tatara-parked` on an open issue means the platform is deliberately not working it, not that it forgot.

## A human nudges it, and the agent disagrees

Twenty-two minutes later the maintainer replied. The double space before the comma is in the original.

```text title="tatara-operator#529, szymonrychu, 2026-08-06T20:22:59Z"
If it's implemented , merge and deploy!
```

Reasonable, and wrong about the state of the world. There was no implementation, because the attempt that was meant to produce one had failed. The agent checked before acting, and then said so:

```text title="tatara-operator#529, szymonrychu-bot, 2026-08-06T20:25:30Z, three spans"
Status check before proceeding: the prior implement attempt (`incident-qe-c0bdeaf8ab61daab-9mqvr`) did not land
[...]
No commit touching `outcome.go` / `handlers_v2.go` / `limits.go` exists yet.
[...]
So "if it's implemented, merge and deploy" doesn't apply yet
[...]
there's nothing to merge.
```

It then read the instruction for what the maintainer meant rather than what it literally said, restated that reading, and asked to be corrected if it had got it wrong. An agent that took the instruction at face value here would have gone looking for something to merge and found something adjacent.

## The plan, then the code

On 2026-08-08 the maintainer restarted it with a third comment, `Fix it (continue existing development and MR).`, and the operator minted a Task two seconds later. The agent's first act was to post a plan and stop:

```text title="tatara-operator#529, szymonrychu-bot, 2026-08-08T08:30:20Z"
## Plan: continue #532's clamp work, land the needs-changes round

Repo: `tatara-operator` only. Single change, one MR.
```

The plan named the four review findings it was carrying forward from an earlier superseded attempt, named the two things it was deliberately not doing, and ended by asking rather than assuming:

```text title="tatara-operator#529, szymonrychu-bot, 2026-08-08T08:30:20Z"
### Open question
Only the branch/MR mechanics above. If you want #532 itself pushed to instead, say so and I will go back through the gate.
```

The run parked a second time here, for a reason that had nothing to do with the defect: an operator bug misread the already-accepted approval and suppressed the implementation pod. The maintainer diagnosed it, filed it separately, and un-parked this issue by hand at `2026-08-08T10:56:25Z`. That was the fourth and last human comment on the thread.

The pull request opened at `2026-08-08T11:54:57Z`: [#550](https://github.com/szymonrychu/tatara-operator/pull/550), titled `fix: clamp agent-supplied issue titles at the restapi boundary`.

## The Task walks its states

None of the state machine shows up on GitHub. The operator logs every transition, and this is the run reconstructed from those logs:

``` { .text .annotate title="reconstructed from the operator's state-transition logs" }
2026-08-08T08:24:40.449Z  (create) -> new                            # (1)!
2026-08-08T08:24:41.099Z  new -> refined                             # (2)!
2026-08-08T08:30:34.132Z  refined -> under-implementation            # (3)!
          [ no transition logged for the first submission ]          # (4)!
2026-08-08T12:06:51.427Z  awaiting-review -> under-implementation    # (5)!
2026-08-08T12:39:52.535Z  under-implementation -> awaiting-review
2026-08-08T12:56:16.827Z  awaiting-review -> merged                  # (6)!
2026-08-08T12:56:21.178Z  merged -> deployed
2026-08-08T13:14:22.987Z  deployed -> done
```

1.  Minted two seconds after the maintainer's comment landed. No pod runs at `new`; the operator triages the Task itself.
2.  `refined` is the approval gate. A pod is up here, and this is where the plan and the approval receipt were written.
3.  The gate granted, on the receipt shown further up. Code is written from here on.
4.  A real gap. The submission that opened the pull request is not in the retained logs, so the timestamp is not shown rather than guessed.
5.  The review verdict was changes requested, so the same Task went back to implementation with a fresh pod.
6.  `merged` names the merge phase, not a merged pull request. The operator owns the merge from here.

Two facts a newcomer usually gets wrong are both visible in that list. There is no separate review Task: the same Task moves between `under-implementation` and `awaiting-review`, and the implement pod is torn down and replaced by a review pod on the same work. And only the operator writes these states - an agent submits an outcome and the operator decides what the outcome means. The full table is in [Task State Machine](../reference/task-stages.md).

## The review sends it back once

Round 1 arrived at `2026-08-08T12:06:49Z` against commit `cbe26e0`, with five inline findings. This is the entire body of it:

```text title="tatara-operator#550, review by szymonrychu-bot, 2026-08-08T12:06:49Z"
<!-- tatara-review round=1 sha=cbe26e0a3d5d10c2cf323949e680daa911a81fd5 -->
## Review: changes requested
```

That is worth being precise about, because it is where the trust model lives. Neither round is a native GitHub approval or change request. Both are posted with GitHub's `COMMENTED` state, because GitHub refuses an `APPROVE` or `REQUEST_CHANGES` review from the account that authored the pull request, and tatara authored this one. The verdict rides in the body, behind the `tatara-review` marker the operator writes, and the operator is what reads it back and acts on it at merge time. GitHub's own approval machinery is never in the loop, and no agent posts the review either: the review pod returns a verdict and findings, and the operator writes them to the forge.

The most useful of the five findings caught the pull request description claiming something the diff did not contain:

```text title="tatara-operator#550, inline finding on api/v1alpha1/limits.go:172"
**Claimed in the MR body, not in the diff: interior newlines are not flattened.**
```

```text title="same finding, continued"
`ClampIssueTitle` does `TrimSpace` and nothing else. No flattening anywhere in the diff (`grep ReplaceAll|Fields|flatten` over the three changed source files: no hits).
```

The reviewer then reproduced the consequence against the actual commit rather than reasoning about it, showing a two-line title being decoded as a title plus a body and overwriting a real issue's contents on the forge.

The implementing pod fixed the findings and resubmitted at `2026-08-08T12:39:52Z`. Round 2 came back fifty minutes after round 1, against commit `d2fbbe4`:

```text title="tatara-operator#550, review by szymonrychu-bot, 2026-08-08T12:56:14Z"
<!-- tatara-review round=2 sha=d2fbbe4100d903554d9429ebc1778634d9bee4c7 -->
## Review: approved
```

Round 2 still carried two findings; neither blocked. It also reported one finding it could not anchor to a line inside the diff, in the body, rather than dropping it.

Two things make that loop worth having. The reviewer is a different pod with no memory of writing the code, which is why it read the description as a claim to be checked instead of a summary to be trusted. And the round counter only advances on a changes-requested verdict, so an approval consumes no round - which is why this pull request has a round 1 that asked for changes and a round 2 that approved. There is no cap on how many rounds the loop may take; `maxReviewRounds` is deprecated and has no effect, and the pair is bounded instead by a single hard 24-hour residency limit on the Task. See [PR / MR Review](../workflows/review.md) for the verdict contract.

## Merge, tag, deploy

The rest ran without a person in it.

| when (UTC) | what |
|---|---|
| `11:56:14` | operator applies the `semver:patch` label to #550 |
| `12:56:19` | operator merges #550 as `e70d0e09` |
| `12:56:20` | issue #529 closes |
| `13:11:31` | `tatara-helmfile#330` merges, moving the operator pin from `2.0.3` to `2.0.4` |
| `13:14:20` | operator posts the closing comment on #529 |

The label goes on before the merge, not after, and the ordering is load-bearing: CI cuts the release tag from the label at the merge commit, so a merge that landed first would be a release that never got tagged. Tag `v2.0.4` points at `e70d0e09df48f1750b8d327b041c62397c55668b`, which is the merge commit of #550.

No agent merged this and none could have. Agent pods hold no forge token, and the tool surface handed to an agent contains no merge action at all - it is a structural absence, not an instruction the agent is trusted to follow. The operator performs the merge itself, against the exact head commit the review approved.

```text title="tatara-operator#529, szymonrychu-bot, 2026-08-08T13:14:20Z"
Delivered in tatara-operator!550 (v2.0.4). Closed by tatara.
```

## What the run cost

Six days wall clock, from the alert at `2026-08-02T01:26:50Z` to the issue closing at `2026-08-08T13:14:20Z`. Be clear about where those days went: almost all of them were the issue sitting open, re-firing, waiting for a person to answer. The delivery itself - Task minted, plan posted, code written, reviewed twice, merged, tagged, deployed, issue closed - took four hours and fifty minutes.

The cost in human attention was four comments from one maintainer, two of them under ten words. The cost in wasted work was one failed implementation attempt and one review round, both of which the thread records rather than hides.

## Where to go next

- [The Big Picture](big-picture.md) - what the pieces are and why they are arranged this way.
- [From Issue to PR](issue-to-pr.md) - the same journey as a step-by-step reference rather than a single run.
- [Prerequisites](../getting-started/prerequisites.md) - what you need before running any of this yourself.
