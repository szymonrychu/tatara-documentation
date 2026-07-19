---
title: Handing an MR to tatara
---

# Handing an MR to tatara

By default tatara only reviews merge requests it did not open - it posts review
findings but will not push commits or merge. A project maintainer can hand such
an MR (including another bot's, like Renovate) over to tatara for full agency:
conflict resolution, CI fixes, requested changes, and merge on an approved
review.

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

## Handing it back

Push any commit to the branch yourself. tatara detects the foreign commit,
posts a stand-down comment, and stops pushing - it keeps reviewing, and the
operator will still merge on an approved review. To hand it back to tatara,
ask again in your own words.
