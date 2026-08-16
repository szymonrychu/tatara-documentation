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
saying why, and then stops.

**A decline holds only while the branch is still tatara's.** Push to it yourself
- which is the natural thing to do after reading the agent's explanation - and
the MR is yours again; ask for a take-over after that and you get one.

Leaving it alone is not a second route back. The declined Task is collected
after its retention window, but collection does not change who the merge request
is marked for, and your push is what does. Ask for a take-over while it is still
marked tatara's and nothing happens: the request either reaches nobody, or is
answered yes and mints nothing. Push whenever suits you, before or after the
window - it is the half that returns the merge request to you.

Asking again *before* you push is not refused - it reaches nothing. The declined
Task still holds the merge request and runs no agent, so there is nobody to read
the comment until ownership is back with you.

That is the same rule as [handing it back](#handing-it-back) below: the operator
reads the branch, not the agent's stated reason. So standing down never costs
you the ability to ask again either.

## If a take-over stops for some other reason

A decline is the common case, not the only one: a take-over can also come to
rest on CI it could not get green, on a head that kept moving under it, or on an
operator limit on how many times one Task may re-enter a state. Push and ask
again - before your push a second request reaches nothing here either, for the
reason given above - and the operator answers with a **conflict** naming
whichever it was. It means *this
Task will not start again, so a second request would be answered yes and then do
nothing* - not a judgement about your merge request, and not a refusal to work
on it.

**A push does not clear these, and the remedy above does not apply.** Only a
decline is converted into a stand-down when the branch comes back to you; every
other stopping reason survives your push, so asking again after it returns the
same conflict. While the merge request stays open, what removes the stopped Task
is the retention window.

Waiting that out is not the whole remedy either. Collection removes the Task but
does not change who the merge request is marked for, so a fresh take-over needs
both halves: the window to collect the Task, and your push to mark the merge
request yours again. Getting the conflict at all means your push has already
landed, since that is what let the request through. The merge request is yours
to work on throughout.

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
