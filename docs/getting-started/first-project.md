---
title: Your First Project
---

# Your First Project

By the end of this page you'll have a `Project` custom resource the operator has
accepted, a memory stack running behind it, and a webhook your SCM provider can
post to. Nothing else in tatara works until that exists.

A `Project` is the top-level entity: one per logical SCM namespace, meaning one
GitHub organization or one GitLab group. It binds four things together - the bot
identity, the SCM connection, the agent execution policy, and the per-project
memory stack. Applying it is what makes the operator provision everything
downstream: the memory stack, the cron schedules, the webhook receiver, and the
Grafana incident integration when you turn that on.

Set aside about twenty minutes. Most of that is waiting for the memory stack to
finish coming up.

!!! info "Before you start"
    - tatara-operator deployed and healthy (see [Installation](installation.md))
    - A dedicated bot account on your SCM provider with permissions to comment,
      label, and open pull requests on the repositories you plan to enroll
    - The bot's personal access token (PAT) and a webhook secret, both covered in
      [Prerequisites](prerequisites.md)

---

## 1. Create the SCM Secret

The Project references a Kubernetes Secret by name rather than carrying the token
itself, so create the Secret first. It has to live in the same namespace as the
Project.

```yaml title="tatara-scm.yaml"
apiVersion: v1
kind: Secret
metadata:
  name: tatara-scm
  namespace: tatara
type: Opaque
stringData:
  token: "ghp_..."   # bot PAT: contents, issues, pull requests, metadata, org members read
                     # (GitHub fine-grained) or api + read/write_repository (GitLab). No webhook scope.
```

```sh
kubectl apply -f tatara-scm.yaml
```

The token needs no webhook scope, because the operator never registers a webhook.
You do that by hand in step 6.

---

## 2. Write the smallest Project that works

Only `scmSecretRef` is required at the top level, and `provider`, `owner`, and
`botLogin` are required inside `scm`. Everything else below is either a default
worth seeing written down or a field with no default at all.

```yaml title="my-project.yaml"
apiVersion: tatara.dev/v1alpha1
kind: Project
metadata:
  name: my-project
  namespace: tatara
spec:
  scmSecretRef: tatara-scm   # Secret in this namespace; key "token" holds the bot PAT
  triggerLabel: tatara        # issues carrying this label enter the agent loop (default: tatara)
  maxConcurrentAgents: 3      # kill switch: most agent pods at once, project-wide (default: 3; 0 pauses the project)
  agentPodTTLSeconds: 3600    # bounds one pod's life; the Task outlives its pods (default: 3600, minimum 300)

  scm:
    provider: github          # github or gitlab
    owner: my-org             # GitHub org name or GitLab group path
    botLogin: my-org-bot      # the bot account's username on the provider

  agent:
    model: claude-opus-4-8    # no default in the CRD; set it yourself
    image: harbor.example.com/tatara-claude-code-wrapper:v1.2.3
    effort: xhigh             # low, medium, high, xhigh, max (default: xhigh)
```

Two of those four top-level fields are worth understanding before you go further.
`maxConcurrentAgents` counts agent **pods**, not Tasks, and setting it to `0` is
the whole-project pause: no pod, of any kind, is ever admitted.
`agentPodTTLSeconds` bounds one pod, not the work - a Task that outlives its pod
gets a handoff note and a fresh pod picks up where the last one stopped.

!!! warning "Deploy through tatara-helmfile, not kubectl apply"
    In production, apply the Project through the `tatara-project` chart managed in
    `tatara-helmfile` rather than `kubectl apply` directly. The chart keeps the
    Project CR under Helm ownership and keeps the co-deployed Repository CRs and
    Secrets consistent with it. Direct `kubectl apply` is fine in a development
    cluster, which is what this page assumes. See [Enrollment](enrollment.md).

---

## 3. Name your maintainers

Add at least one maintainer login to the Project you wrote in step 2:

```yaml
spec:
  scm:
    maintainerLogins:
      - alice
      - bob
    reporterLogins:
      - alice
      - bob
```

Skip this and your Project still applies cleanly, still provisions its memory
stack, and still never does any work. `maintainerLogins` is closed by default: an
empty list means no account is a maintainer, so no comment can approve anything
and no issue ever advances out of `refined`. This is the single most common
reason a first Project looks healthy and sits idle.

Approval in tatara is a comment, not a label. A maintainer comments on the issue,
the `implement` agent decides whether that comment approves and cites it by id
plus a verbatim quote, and the operator then checks independently that the comment
exists, that its author is a maintainer and not the bot, and that the quoted text
really appears in the body the operator itself holds. There is no required phrase
and no wordlist. Read [Approval Gates](../operations/security/approval-gates.md#the-approval-grammar)
for the full grammar.

`reporterLogins` is the other half. Leave it empty and the operator acts on issues
and comments from any author. Set it and everyone outside the list, the maintainer
set, and the bot is dropped at intake.

!!! danger "Set both lists to real humans"
    An empty `reporterLogins` lets anyone who can file an issue steer an agent that
    holds elevated SCM permissions. Put the people who already have commit access
    in both lists. See [Prompt-Injection Defenses](../operations/security/prompt-injection.md)
    for the threat model.

    Your `botLogin` must not appear in either list. The API rejects the Project if
    it does, because the bot is excluded from the approval path structurally.

---

## 4. What you did not set

The Project above leaves the rest of the configuration surface at its defaults,
and those defaults are the right place to start:

- **Memory sizing.** One Postgres instance, 10Gi of PGDATA, 8Gi of WAL, 10Gi for
  Neo4j. Enough to evaluate tatara against a single repository.
- **Agent tuning.** A 1800-second per-turn inactivity window, `bypassPermissions`,
  and one model for every agent kind.
- **Cron activities.** No schedule is set, so nothing scans, refines, brainstorms,
  or upgrades on its own yet. Webhook-driven work still runs.
- **Grafana, board projection, project guidance, and the label vocabulary.** All
  optional, all off or defaulted.

When you need to change one of them, every field, its type, its default, and what
it actually does is in the
[Project configuration reference](../reference/project-configuration.md). The
[Project CRD reference](../reference/project.md) covers the same object at the API
level, including the status fields.

---

## 5. Check it before you apply

```sh
kubectl apply -f my-project.yaml --dry-run=server
```

A server-side dry run runs the schema and the validation rules without persisting
anything, so it catches a bad enum value, a missing required field, or a
`botLogin` you also listed as a maintainer.

!!! warning "A misspelled field is not an error"
    Kubernetes prunes fields the live CRD does not know about, silently and with no
    message anywhere. `maxConcurentAgents` does not fail the dry run - it vanishes,
    and the Project runs on the default. If a setting appears to have no effect,
    read the object back with `kubectl -n tatara get project my-project -o yaml`
    and check the field is still there.

---

## 6. Apply and watch

```sh
kubectl apply -f my-project.yaml
```

Now watch the memory stack come up:

```sh
kubectl -n tatara get project my-project -w \
  -o jsonpath='{.status.memory.phase}{"\n"}'
```

Wait for the phase to become `Ready`. The operator sets it once the CNPG Postgres
cluster, Neo4j, LightRAG, and the memory API server are all healthy. Provisioning
four backends from cold takes several minutes.

!!! info "If the phase does not move"
    The phase runs `Provisioning`, then `Ready`, or `Degraded` and `Failed` when a
    backend does not come up. `status.memory.notReady` names which of the four is
    holding it, which is the fastest way to find out what is wrong:

    ```sh
    kubectl -n tatara get project my-project \
      -o jsonpath='{.status.memory.notReady}{"\n"}'
    kubectl -n tatara describe project my-project
    kubectl -n tatara get pods -l tatara.dev/project=my-project
    ```

Repository **ingestion** stays gated for a further three minutes after the phase
first turns `Ready`, so one blip cannot release the whole backlog at once. During
that window a Repository shows a `MemoryNotReady` condition reading `waiting for
project my-project memory stack to become stably Ready` even though
`status.memory.phase` already says `Ready`. That is the debounce working, not a
stuck reconcile. Check `status.memory.readySince` for when the window opened.
Agent pods are not gated by this - they spawn and run whatever the memory stack is
doing, and work against a degraded stack with the degradation declared to them.

Once the phase reads `Ready`, take the webhook URL:

```sh
kubectl -n tatara get project my-project \
  -o jsonpath='{.status.webhookURL}'
```

### Register the webhook

The operator validates inbound payloads but never registers a webhook, so this
part is manual. In GitHub go to organization Settings, then Webhooks; in GitLab go
to group Settings, then Webhooks.

| Setting | Value |
|---|---|
| Payload URL | value from `status.webhookURL` |
| Content type | `application/json` |
| Secret | the `webhookSecret` from [Prerequisites](prerequisites.md#webhook-secret) |
| Events (GitHub) | Issues, Issue comments, Pull requests, Pull request reviews |
| Events (GitLab) | Issues events, Comments, Merge request events |

Then tail the operator's structured logs and confirm it is reconciling your
project without errors:

```sh
kubectl -n tatara logs deploy/tatara-operator -f | jq 'select(.project == "my-project")'
```

### Watch it work

File a test issue, label it with your `triggerLabel`, and watch the Task the
operator mints for it move through the state machine:

```sh
kubectl -n tatara get tasks -o custom-columns=\
NAME:.metadata.name,STATE:.status.state,PARK:.status.parkReason,KIND:.spec.kind,AGENT:.status.agentKind -w
```

`STATE` is the single source of truth, and `PARK` is an orthogonal flag rather
than another state - see the [Task reference](../reference/task.md) for both
enums. `KIND` is the origin the Task was minted with and never changes; `AGENT` is
whichever pod is running right now, so the two diverge as soon as the issue moves
past `refined`.

The mirrored `Issue` and `MergeRequest` CRs the Task owns are visible the same
way:

```sh
kubectl -n tatara get iss,mr -l tatara.dev/task=<task-name>
```

---

## Where to go next

- [Enroll your repositories](enrollment.md) so the operator has something to
  ingest and monitor.
- [Project configuration reference](../reference/project-configuration.md) for the
  fields you left at their defaults in step 4.
- [Task State Machine](../reference/task-stages.md) for what the `STATE` column is
  telling you.
