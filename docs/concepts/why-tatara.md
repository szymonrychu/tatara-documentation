---
title: Why tatara
---

# Why tatara

## The problem tatara solves

Engineering teams accumulate a backlog of small, well-understood improvements that never get done: refactors that would reduce cognitive load, test coverage gaps, documentation that drifts from the code, dependency updates that keep getting deferred. These items are not important enough to displace sprint work, but their absence compounds over time.

A second problem: incidents generate follow-up action items that are entered into the tracker and then forgotten. The investigation is thorough; the remediation sits unimplemented for months.

Tatara addresses both by running a continuous, autonomous loop: it reads your issue tracker, proposes improvements it discovers from your codebase graph, implements approved proposals, and responds to incidents - all without requiring a human to drive each step.

## What you get

**Autonomous implementation loop.** Issues labeled `tatara` are picked up automatically. The agent clarifies the issue (posting questions or a plan when it needs a human decision, and parking until you respond), implements once approved, opens a PR, and babysits CI. A separate review pod then approves the change and the operator itself merges it on green CI - there is no configuration that arms the forge's own merge-when-green feature on a tatara-opened PR. You review code and the approval decision, not prompts.

**Periodic brainstorm.** A cron-driven brainstorm agent queries the codebase knowledge graph and proposes improvements as GitHub/GitLab issues. Proposals are filed, not implemented - a maintainer approves a proposal the same way as any other issue: a comment matching one of `Project.spec.scm.approvalPhrases`; nothing else releases it. The agent caps proposal volume (`maxOpenProposals`) so the tracker does not flood.

**Incident response.** A Grafana alert fires a webhook to the operator. An incident investigation agent starts within seconds, queries Grafana (metrics, logs, annotations), diagnoses the issue, and files a structured incident issue with findings and remediation proposals. Escalation and follow-up implementation happen through the normal clarify -> implement -> review handoff.

**Durable code memory.** Every enrolled repository is ingested into a LightRAG + Neo4j knowledge graph. Agent sessions query this graph for context. New code is not read cold on every session start; relationships between files, functions, and services are pre-computed and persistent.

**GitOps-first deploy.** Tatara deploys itself and enforces the same discipline for its own updates: every deploy is a helmfile PR with a rendered diff, reviewed, and merged before the in-cluster runner applies it.

## Who tatara is for

**Platform engineers and SREs** who want to extend their Kubernetes investment into the development loop: automated incident response, self-improving infrastructure, agent-driven runbooks.

**Engineering managers** who want backlog velocity without proportional headcount growth. Tatara surfaces proposals automatically and routes them through the existing review flow.

**Senior developers and architects** adopting it critically: the CRD API is fully auditable, the security model is explicit (allowlists, OIDC, headless agents), and the entire system is open-source (AGPLv3).

## What tatara is not

**Not a general-purpose CI system.** Tatara orchestrates agent turns, not arbitrary pipelines. Keep your own CI (GitHub Actions, GitLab CI, or whatever you run) for build/test/deploy; tatara consumes its commit-status signal, it does not replace it.

**Not a hosted service.** You deploy tatara to your own Kubernetes cluster, connect it to your own GitHub or GitLab organization, supply your own OIDC issuer (the reference deployment uses Keycloak; any compliant issuer works), and supply your own Claude Code credential. As shipped the operator injects that credential to the agent pod as `CLAUDE_CODE_OAUTH_TOKEN` (a Claude subscription OAuth token); see [Prerequisites](../getting-started/prerequisites.md) for exact provisioning.

**Not a monolith.** Nine independent component repos, each with its own packaging, CI pipeline, and release lifecycle. Packaging is per-component, not uniformly a Helm chart: the Go services (operator, memory, claude-code-wrapper, memory-repo-ingester) ship Helm charts or run as Kubernetes Jobs, `tatara-cli` ships via a Homebrew tap and is baked into the wrapper image, `tatara-agent-skills` is a Claude Code plugin bundle, `tatara-helmfile` is Helmfile plus YAML, and `tatara-observability` is Terraform plus YAML. You can adopt components incrementally.

**Not autonomous with unchecked write access - but the write access is real.** A maintainer
decides whether an issue gets worked, and decides it in exactly one way: a comment on the
issue whose text matches one of `Project.spec.scm.approvalPhrases`, posted by an account in
`maintainerLogins` and never the bot (the bot is structurally excluded from ever satisfying its
own gate). After that, the default is autonomous: `review` approves the bot's own PR from a
separate pod by calling `submit_outcome(approve)` - never a native forge approval, since GitHub
blocks a PR's own author from approving it either way - and the operator itself merges once
required checks are green and that verdict is recorded, with no human merge step. The agent pod
never runs `git merge` - the operator does - but "the agent cannot merge its own code" should not
be read as "a human merges every PR." There is no SCM branch-protection rule you can add to
require a human review on top of this: the platform's single bot identity means a rule requiring
an approving review would deadlock every merge (the operator can never satisfy it on its own PR).
See [The Agentic Operating Model](agentic-model.md#gate-2-review-approval-then-an-operator-driven-merge).

## Trade-offs to consider

| Consideration | Detail |
|---|---|
| Anthropic API cost | Every agent session consumes Claude tokens; a busy brainstorm or complex implementation run can be expensive. Monitor with the wrapper's real per-turn series `ccw_turn_tokens_total{type,model}` and `ccw_turn_cost_usd_total`, plus the operator's `operator_task_tokens_total`. The actual cost/runaway levers are: **`spec.agent.maxTurnsPerPod`** (caps a single pod's run; the turn-uncapped `implement` kind is exempt), **`spec.agent.maxTurnsPerTask`** (the lifetime ceiling across every pod of a Task, all kinds included - this is what bounds `implement`), **`spec.tokenBudget`** (a proactive/emergency-percent admission gate that pauses the normal or alert pool at a share of a usage window; off by default), and **per-kind tiering** via `spec.agent.modelByKind` / `effortByKind` (run cheap kinds on a smaller model/effort). |
| Keycloak dependency | tatara requires an OIDC provider. The reference deployment uses Keycloak. Replacing this with a different IdP is possible but requires configuration changes across multiple components. |
| Ceph / PVC dependency | tatara-memory uses Neo4j (PVC) and CNPG Postgres (PVC). On bare-metal Kubernetes this typically means Ceph or another block/file storage provider. |
| Agent trust boundaries | Agent pods have read-write access to enrolled repositories and can post comments as the bot account. The blast radius is bounded by the bot PAT's scopes and the `reporterLogins` allowlist, but it is non-zero. |

## Minimum viable adoption

The smallest useful tatara deployment:

1. One Kubernetes cluster (EKS, GKE, on-prem - anything with PVC support)
2. One Keycloak realm with three clients
3. One harbor (or ghcr.io) OCI registry for charts + images
4. One GitHub or GitLab organization with a bot account
5. The `tatara-helmfile` repository forked and configured for your cluster
6. One `Project` CR and one or more `Repository` CRs

You do not need Grafana alerting or the brainstorm cron to start. The minimal loop is: label an issue -> a maintainer comments an approval phrase -> agent implements and a review pod approves -> the operator merges on green CI.
