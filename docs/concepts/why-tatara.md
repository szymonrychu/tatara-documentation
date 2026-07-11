---
title: Why tatara
---

# Why tatara

## The problem tatara solves

Engineering teams accumulate a backlog of small, well-understood improvements that never get done: refactors that would reduce cognitive load, test coverage gaps, documentation that drifts from the code, dependency updates that keep getting deferred. These items are not important enough to displace sprint work, but their absence compounds over time.

A second problem: incidents generate follow-up action items that are entered into the tracker and then forgotten. The investigation is thorough; the remediation sits unimplemented for months.

Tatara addresses both by running a continuous, autonomous loop: it reads your issue tracker, proposes improvements it discovers from your codebase graph, implements approved proposals, and responds to incidents - all without requiring a human to drive each step.

## What you get

**Autonomous implementation loop.** Issues labeled `tatara` are picked up automatically. The agent triages the issue (posting questions or a plan when it needs a human decision, and parking until you respond), implements once cleared, opens a PR, and babysits CI. Under the default merge policy the operator then squash-merges the PR on green CI on its own; add a review-gated branch-protection rule if you want to review each PR before it lands. You review code and the triage decision, not prompts.

**Periodic brainstorm.** A cron-driven brainstorm agent queries the codebase knowledge graph and proposes improvements as GitHub/GitLab issues. Proposals are filed, not implemented - a human decides which ones to approve. The agent caps proposal volume (`maxOpenProposals`) so the tracker does not flood.

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

**Not a monolith.** Eight independent component repos, each with its own packaging, CI pipeline, and release lifecycle. Packaging is per-component, not uniformly a Helm chart: the Go services (operator, memory, chat) ship Helm charts, but `tatara-cli` ships via a Homebrew tap and is baked into the wrapper image, `tatara-helmfile` is Helmfile plus YAML, and `tatara-observability` is Terraform plus YAML. You can adopt components incrementally.

**Not autonomous with unchecked write access - but the write access is real.** A human decides whether an issue gets worked (clarify's conversation gate). After that, the default is autonomous: `review` approves the bot's own PR from a separate pod, and the deploy supervisor squash-merges once required checks are green and that approval is present, with no human merge step. The agent pod never runs `git merge` - the operator does - but "the agent cannot merge its own code" should not be read as "a human merges every PR." To require a human review before merge in addition to `review`'s own approval, configure an SCM branch-protection rule mandating an approving review; that gate is enforced by the forge, not by tatara's default. See [The Agentic Operating Model](agentic-model.md#gate-2-review-approval-approve-label-native-review-never-a-merge-call).

## Trade-offs to consider

| Consideration | Detail |
|---|---|
| Anthropic API cost | Every agent session consumes Claude tokens; a busy brainstorm or complex implementation run can be expensive. Monitor with the wrapper's real per-turn series `ccw_turn_tokens_total{type,model}` and `ccw_turn_cost_usd_total`, plus the operator's `operator_task_tokens_total`. The actual cost/runaway levers are: **`spec.agent.maxTaskTokens`** (per-Task cumulative *output*-token ceiling - a runaway backstop for the turn-uncapped `implement` kind, disabled at 0 by default), **`spec.tokenBudget`** (a proactive/emergency-percent admission gate that pauses the normal or alert pool at a share of a usage window; off by default), and **per-kind tiering** via `spec.agent.modelByKind` / `effortByKind` (run cheap kinds on a smaller model/effort). `maxTurnsPerTask` only bounds the turn-capped kinds; it is not the primary cost control for implementation work. |
| Keycloak dependency | tatara requires an OIDC provider. The reference deployment uses Keycloak. Replacing this with a different IdP is possible but requires configuration changes across multiple components. |
| Ceph / PVC dependency | tatara-memory uses Neo4j (PVC) and CNPG Postgres (PVC). On bare-metal Kubernetes this typically means Ceph or another block/file storage provider. |
| S3 dependency | Conversation persistence requires an S3-compatible object store. Without it, pod restarts begin fresh sessions. |
| Agent trust boundaries | Agent pods have read-write access to enrolled repositories and can post comments as the bot account. The blast radius is bounded by the bot PAT's scopes and the `reporterLogins` allowlist, but it is non-zero. |

## Minimum viable adoption

The smallest useful tatara deployment:

1. One Kubernetes cluster (EKS, GKE, on-prem - anything with PVC support)
2. One Keycloak realm with three clients
3. One harbor (or ghcr.io) OCI registry for charts + images
4. One GitHub or GitLab organization with a bot account
5. The `tatara-helmfile` repository forked and configured for your cluster
6. One `Project` CR and one or more `Repository` CRs

You do not need Grafana alerting or the brainstorm cron to start. The minimal loop is: label an issue -> agent triages and implements -> the PR merges on green CI (add a review-gated branch-protection rule if you want a human merge step first).
