---
title: Getting Started
---

# Getting Started

Work through these four pages and you'll have tatara running on your own cluster:
the operator reconciling, a `Project` it has accepted, a webhook your SCM provider
posts to, and at least one repository ingested and watched for labelled issues.
From there, labelling an issue is what starts an agent.

## What each step gets you

| Step | What exists when you finish | How long |
|---|---|---|
| [Prerequisites](prerequisites.md) | A cluster that can host the operator: ingress, storage, identity, the bot account, the deploy runner | Depends entirely on what your cluster already runs. This is the long pole. |
| [Installing the Operator](installation.md) | The `tatara-operator` Helm release running, its CRDs installed, deploys flowing through a reviewed PR | One PR, then one apply. Helm waits up to 900 seconds per release. |
| [Your First Project](first-project.md) | A `Project` the operator accepted, its memory stack `Ready`, a webhook URL to register | About 20 minutes, most of it waiting on the memory stack |
| [Enrolling Repositories](enrollment.md) | One `Repository` per repo, each ingested into the memory graph and monitored | Minutes per repository; the first ingest walks the whole tree |

Budget an afternoon if your cluster already runs an ingress controller, an OIDC
provider, and a GitOps deploy runner. If it doesn't, prerequisites is where the
time goes and nothing after it takes as long.

## What you need before you start

The full list is on [Prerequisites](prerequisites.md), and it's long. These five
are the ones that block everything else, so check them first:

- **Kubernetes 1.33 or later.** The operator chart declares
  `kubeVersion: ">=1.33.0-0"` and Helm refuses the install below it.
- **An ingress your SCM provider can reach.** The operator validates inbound
  webhooks but never registers them, so the URL has to be publicly resolvable and
  you register it by hand.
- **A dedicated bot account** on GitHub or GitLab, with its own token. Not a
  personal account.
- **An OIDC provider.** Every tatara API is token-gated; the reference deployment
  uses Keycloak.
- **A Claude subscription OAuth token**, not a metered API key.

## See it work before you install it

If you'd rather watch tatara do a complete piece of work before you spend an
afternoon on prerequisites, read
[Watch One Run](../explainers/watch-one-run.md). It follows one real run in
tatara's own repository end to end: an alert firing, a maintainer approving in two
words, a review round that caught a real bug in the agent's own diff, an agent
that declined half a finding and put its reasons on the record, and the deploy.
Nothing in it was staged, and the same page carries the 30-day denominator that
one run sits in.

For the shape of the system rather than one run, [How It Works](../explainers/index.md)
is the narrative path, and [Concepts](../concepts/index.md) covers why it's built
this way.

## The four steps

<div class="grid cards" markdown>

-   :material-check-all: **Prerequisites**

    ---

    Get your cluster ready: ingress, storage classes, identity, the bot account,
    and the deploy runner that applies every release.

    [:octicons-arrow-right-24: Prerequisites](prerequisites.md)

-   :material-download: **Installing the Operator**

    ---

    Deploy the operator through the `tatara-helmfile` GitOps flow, where every
    change is a PR with a rendered diff.

    [:octicons-arrow-right-24: Installation](installation.md)

-   :material-rocket-launch: **Your First Project**

    ---

    Apply a `Project` CR and the operator provisions the memory stack, the cron
    schedules, and the webhook receiver behind it.

    [:octicons-arrow-right-24: First Project](first-project.md)

-   :material-source-repository: **Enrolling Repositories**

    ---

    Enroll a repository and the operator ingests it into the memory graph and
    starts watching it for labelled issues.

    [:octicons-arrow-right-24: Enrollment](enrollment.md)

</div>

## The mistake almost every first install makes

!!! danger "An empty `maintainerLogins` means nothing can ever be approved"
    `Project.spec.scm.maintainerLogins` is closed by default. Leave it empty and
    your Project still applies cleanly, still provisions its memory stack, still
    reports healthy, and never advances a single Task past the approval gate at
    `refined`. There is no warning event and no failing condition, because nothing
    is broken - no account is a maintainer, so no comment can approve anything.

    This is the most common reason a first install looks fine and does nothing.
    Put the people who already have commit access in that list. See
    [Your First Project, step 3](first-project.md#3-name-your-maintainers).

## Where the detail lives

Getting Started is the path to a working install. Once you're past it:

- [Project Configuration](../reference/project-configuration.md) documents every
  field you left at its default.
- [Task State Machine](../reference/task-stages.md) explains what a Task's
  `status.state` and `status.parkReason` are telling you.
- [Operations](../operations/index.md) covers running it: tuning, observability,
  and the runbooks.
