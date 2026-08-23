---
title: Why tatara
---

# Why tatara

## The problem tatara solves

Your backlog fills with small, well-understood improvements that never get done: refactors that would cut cognitive load, test coverage gaps, docs that drift from the code, dependency bumps you keep deferring. None of them are important enough to displace sprint work, and their absence compounds anyway.

Incidents make it worse. The investigation is thorough, the follow-up action items land in the tracker, and then the remediation sits there for months.

tatara works both piles by running a continuous loop against your tracker: it proposes improvements it finds in your codebase graph, implements the ones you approve, and responds to alerts - without a human driving each step.

## What you get

**An autonomous implementation loop.** tatara picks up issues carrying your project's trigger label. The agent runs an approval conversation on the thread, posting questions or a plan and parking until you answer. Once you approve by comment, the same Task implements, opens a PR, and babysits CI. A separate review pod then approves the change and the operator merges it on green CI. No configuration arms the forge's own merge-when-green feature on a tatara-opened PR. You review code and the approval decision, not prompts.

**A periodic brainstorm.** A cron-driven brainstorm agent queries the codebase knowledge graph and files improvements as issues. It files them, it never implements them: you approve a proposal exactly as you would any other issue, by commenting, with the `implement` agent citing that comment and the operator verifying the citation. Nothing else releases it. `maxOpenProposals` caps how many unapproved proposals can be open at once, so the tracker never floods.

**Incident response.** A Grafana alert fires a webhook to the operator. An incident agent starts within seconds, queries Grafana for metrics, logs, and annotations, forms a diagnosis, and files a structured incident issue with findings and remediation proposals. Anything that follows goes through `implement`'s normal approval-gate, code, review handoff.

**Dependency upgrades that go through review.** Turn on the `upgrade` cron (it is off by default) and each tick works one dependency unit: read the release notes between the current pin and the target, make the code changes the bump needs, run the tests. If a dependency engine like Renovate already opens merge requests in your org, tatara can adopt those instead - the engine keeps discovery, tatara keeps merge authority, and a trivial bump costs a single review turn. See [Merge requests tatara did not open](agentic-model.md#merge-requests-tatara-did-not-open).

**Durable code memory.** The ingester walks each enrolled repository and pushes a code graph into LightRAG and Neo4j. Agent sessions query that graph for context, so a session does not read your code cold: the relationships between files, functions, and services are already computed and they persist.

**GitOps-first deploys.** tatara deploys itself under the same discipline it gives your code. Every deploy is a helmfile PR with a rendered diff, reviewed and merged before the in-cluster runner applies it.

## Who tatara is for

**Platform engineers and SREs** extending a Kubernetes investment into the development loop: automated incident response, self-improving infrastructure, agent-driven runbooks.

**Engineering managers** who need backlog velocity without proportional headcount. tatara surfaces proposals on its own and routes them through the review flow you already run.

**Senior developers and architects** adopting it critically. The CRD API is fully auditable, the security model is explicit (allowlists, OIDC, headless agents), and the whole system is open-source under AGPLv3.

## What tatara is not

**Not a general-purpose CI system.** tatara orchestrates agent turns, not arbitrary pipelines. Keep your own CI - GitHub Actions, GitLab CI, whatever you run - for build, test, and deploy. tatara consumes its commit-status signal rather than replacing it.

**Not a hosted service.** You deploy tatara to your own Kubernetes cluster, connect it to your own GitHub or GitLab organization, supply your own OIDC issuer (the reference deployment uses Keycloak; any compliant issuer works), and supply your own Claude Code credential. As shipped the operator injects that credential into the agent pod as `CLAUDE_CODE_OAUTH_TOKEN`, a Claude subscription OAuth token. See [Prerequisites](../getting-started/prerequisites.md) for how to provision it.

**Not a monolith.** Nine independent component repos, each with its own packaging, CI pipeline, and release lifecycle. Packaging differs per component rather than being uniformly a Helm chart: the Go services (operator, memory, claude-code-wrapper, memory-repo-ingester) ship Helm charts or run as Kubernetes Jobs, `tatara-cli` ships through a Homebrew tap and is baked into the wrapper image, `tatara-agent-skills` is a Claude Code plugin bundle, `tatara-helmfile` is Helmfile plus YAML, and `tatara-observability` is Terraform plus YAML. You can adopt components incrementally.

**Not autonomous with unchecked write access - but the write access is real.** You decide whether
an issue gets worked, in exactly one way: a comment on the issue, from an account in
`maintainerLogins` and never the bot, which the `implement` agent cites and the operator verifies
against its own mirror before any code is written. The bot is structurally excluded from satisfying
its own gate.

After that, the default is autonomous. `review` approves the bot's own PR from a separate pod by
calling `submit_outcome(approve)` - never a native forge approval, since GitHub blocks a PR's own
author from approving it - and the operator merges once required checks are green and that verdict
is recorded. There is no human merge step. The agent pod never runs `git merge`, the operator does,
but do not read "the agent cannot merge its own code" as "a human merges every PR." You cannot add
a branch-protection rule requiring a human review on top: the platform's single bot identity means
such a rule would deadlock every merge, because the operator can never satisfy it on its own PR.
See [The Agentic Operating Model](agentic-model.md#gate-2-review-approval-then-an-operator-driven-merge).

## Trade-offs to consider

| Consideration | Detail |
|---|---|
| Anthropic API cost | Every agent session consumes Claude tokens; a busy brainstorm or complex implementation run can be expensive. Monitor with the wrapper's real per-turn series `ccw_turn_tokens_total{type,model}` and `ccw_turn_cost_usd_total`, plus the operator's `operator_task_tokens_total`. `spec.agent.maxTurnsPerPod` and `maxTurnsPerTask` are **deprecated with zero effect** - a turn count measured how much an agent had done, not whether it was stuck, and neither ever stopped a runaway *cost*. The actual cost/runaway levers are: the [24h residency cap](../reference/task-stages.md#residency-the-dead-man-switch) (hardcoded, not a field), **`spec.tokenBudget`** (a proactive/emergency-percent admission gate that pauses the normal or alert pool at a share of a usage window; off by default), and **per-kind tiering** via `spec.agent.modelByKind` / `effortByKind` (run cheap kinds on a smaller model/effort). |
| Keycloak dependency | tatara needs an OIDC provider. The reference deployment uses Keycloak. A different IdP works, but you change configuration across several components to get there. |
| Ceph / PVC dependency | tatara-memory runs Neo4j (PVC) and CNPG Postgres (PVC). On bare-metal Kubernetes that usually means Ceph or another block/file storage provider. |
| Agent trust boundaries | Agent pods hold read-write access to enrolled repositories and post comments as the bot account. The bot PAT's scopes and the `reporterLogins` allowlist bound the blast radius, but it is not zero. |

## Minimum viable adoption

The smallest useful tatara deployment:

1. One Kubernetes cluster (EKS, GKE, on-prem - anything with PVC support)
2. One Keycloak realm with three clients
3. One harbor (or ghcr.io) OCI registry for charts + images
4. One GitHub or GitLab organization with a bot account
5. The `tatara-helmfile` repository forked and configured for your cluster
6. One `Project` CR and one or more `Repository` CRs

You do not need Grafana alerting or the brainstorm cron to start. The minimal loop: label an issue, comment your approval, the `implement` agent cites your comment and the operator verifies it, the same Task implements and a review pod approves, and the operator merges on green CI.
