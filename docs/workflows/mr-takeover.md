---
title: Handing an MR to tatara
---

# Handing an MR to tatara

By default tatara only reviews merge requests it did not open - it posts review
findings but will not push commits or merge. A project maintainer can hand such
an MR (including another bot's, like Renovate) over to tatara for full agency:
conflict resolution, CI fixes, requested changes, and merge on an approved
review.

!!! note "A dependency engine armed for adoption doesn't need this"
    Once a project sets `upgradePolicy.adoptBranchPrefix` and the engine authors as `scm.botLogin`
    (or an `upgradeEngineLogins` entry), its merge requests reach `tatara` ownership automatically,
    no comment required - see [MR Ownership: adopting a dependency engine's merge requests](../architecture/ownership.md#adopting-a-dependency-engines-merge-requests).
    This page's ask-in-a-comment flow covers a human's PR, or an engine MR outside that policy.

## Ask in a comment

Comment on the MR in plain language, for example "please take over, resolve the
conflicts, and merge once green". There is no fixed command syntax. The review
agent judges whether your comment is a hand-over request.

Only a **project maintainer** (an account in the project or repository
`maintainerLogins` set, closed by default) can hand an MR over. The operator
re-checks the comment's author server-side against the maintainer list, so a
request from anyone else is refused even if the agent misreads it - and the
agent's judgment alone can never change ownership.

## What happens next

- tatara posts an announcement comment confirming it is taking over.
- A tatara agent works the MR's own branch: it merges the default branch to
  resolve conflicts (never a force-push, never a rebase), makes the requested
  changes, and pushes.
- The review agent reviews as usual; on an approved review the operator merges.

There is **no approval turn**. Your comment is the authorisation, and the
operator has already verified it is yours, so the agent starts working
immediately rather than writing a plan and waiting for a second go-ahead.

## If tatara declines

The agent can decide not to finish the MR. It comments on the merge request
saying why, and then stops. Asking again in a comment is refused rather than
silently ignored - the operator answers the second request with a conflict
naming the decline.

**A decline holds only while the branch is still tatara's.** Push to it yourself
- which is the natural thing to do after reading the agent's explanation - and
the MR is yours again; ask for a take-over after that and you get one. Leave it
alone instead and the declined Task is collected after its retention window,
after which a fresh take-over can be requested from scratch.

That is the same rule as [handing it back](#handing-it-back) below: the operator
reads the branch, not the agent's stated reason. So standing down never costs
you the ability to ask again either.

## Handing it back

Push any commit to the branch yourself. tatara detects the foreign commit,
posts a stand-down comment, and stops pushing - it keeps reviewing, and the
operator will still merge on an approved review. To hand it back to tatara,
ask again in your own words.

"Detects the foreign commit" is a comparison, not an identity check: the
operator compares the branch head against the last head it recorded as its own.
It never asks who pushed, because a forge actor is only reported on a delivered
webhook and a commit's author is a string the pusher chose. Two consequences
worth knowing:

- **It is not instant.** Detection lands on the next reconcile of that merge
  request - the push webhook, or the periodic sweep if the delivery is dropped.
  Nothing is lost by the delay: the agent never force-pushes, so your commit
  cannot be overwritten in the meantime.
- **A take-over you did not ask for is still refused.** Ownership moving back to
  you does not start any work; only your comment does that.
