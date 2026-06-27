---
title: Why tatara
---

# Why tatara

## The problem tatara solves

Engineering teams accumulate a backlog of small, well-understood improvements that never get done: refactors that would reduce cognitive load, test coverage gaps, documentation that drifts from the code, dependency updates that keep getting deferred. These items are not important enough to displace sprint work, but their absence compounds over time.

A second problem: incidents generate follow-up action items that are entered into the tracker and then forgotten. The investigation is thorough; the remediation sits unimplemented for months.

Tatara addresses both by running a continuous, autonomous loop: it reads your issue tracker, proposes improvements it discovers from your codebase graph, implements approved proposals, and responds to incidents - all without requiring a human to drive each step.

## What you get

**Autonomous implementation loop.** Issues labeled `tatara` are picked up automatically. The agent triages the issue, posts a plan for human review, waits for approval, implements, opens a PR, and parks the task until the PR is merged or the human provides further feedback. You review code, not prompts.

**Periodic brainstorm.** A cron-driven brainstorm agent queries the codebase knowledge graph and proposes improvements as GitHub/GitLab issues. Proposals are filed, not implemented - a human decides which ones to approve. The agent caps proposal volume (`maxOpenProposals`) so the tracker does not flood.

**Incident response.** A Grafana alert fires a webhook to the operator. An incident investigation agent starts within seconds, queries Grafana (metrics, logs, annotations), diagnoses the issue, and files a structured incident issue with findings and remediation proposals. Escalation and follow-up implementation happen through the normal issue lifecycle.

**Durable code memory.** Every enrolled repository is ingested into a LightRAG + Neo4j knowledge graph. Agent sessions query this graph for context. New code is not read cold on every session start; relationships between files, functions, and services are pre-computed and persistent.

**GitOps-first deploy.** Tatara deploys itself and enforces the same discipline for its own updates: every deploy is a helmfile PR with a rendered diff, reviewed, and merged before the in-cluster runner applies it.

## Who tatara is for

**Platform engineers and SREs** who want to extend their Kubernetes investment into the development loop: automated incident response, self-improving infrastructure, agent-driven runbooks.

**Engineering managers** who want backlog velocity without proportional headcount growth. Tatara surfaces proposals automatically and routes them through the existing review flow.

**Senior developers and architects** adopting it critically: the CRD API is fully auditable, the security model is explicit (allowlists, OIDC, headless agents), and the entire system is open-source (AGPLv3).

## What tatara is not

**Not a general-purpose CI system.** Tatara orchestrates agent turns, not arbitrary pipelines. Use Argo Workflows or GitHub Actions for build/test/deploy.

**Not a hosted service.** You deploy tatara to your own Kubernetes cluster, connect it to your own GitHub or GitLab organization, and supply your own Keycloak instance for OIDC and your own Anthropic API key.

**Not a monolith.** Nine independent component repos, each with its own Helm chart, CI pipeline, and release lifecycle. You can adopt components incrementally.

**Not autonomous with unchecked write access.** Every implementation requires human approval in the issue thread. Every PR requires human review and merge (under the default `afterApproval` policy). The agent cannot merge its own code.

## Trade-offs to consider

| Consideration | Detail |
|---|---|
| Anthropic API cost | Every agent session consumes Claude tokens. A busy brainstorm or complex implementation run can be expensive. Monitor via `ccw_token_usage_total` and set `maxTurnsPerTask` conservatively. |
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

You do not need Grafana alerting, Argo Workflows, or the brainstorm cron to start. The minimal loop is: label an issue -> agent triages and implements -> you review the PR.
