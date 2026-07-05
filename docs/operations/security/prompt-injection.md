---
title: Prompt-Injection Defenses
---

# Prompt-Injection Defenses

Tatara runs an AI agent that reads issue content written by potentially external users. A malicious issue body could attempt to hijack the agent's behavior through prompt injection. The platform has several layers of defense, ordered from most to least effective.

## Layer 1: Reporter allowlist (intake gate)

The most effective defense: control who can cause the agent to process any content at all.

`spec.scm.reporterLogins` is an allowlist of GitHub/GitLab account logins. When non-empty, the operator only acts on issues and issue comments authored by the bot, a maintainer, or an account in this list. Issues from any other account are **silently dropped at intake** - no task is created, no webhook event is enqueued.

```yaml
spec:
  scm:
    reporterLogins: [alice, bob, charlie]  # only these accounts trigger agent activity
    maintainerLogins: [alice, bob]         # these accounts can also approve
```

**Default:** empty `reporterLogins` means any account can trigger agent activity (open intake, backward-compatible). For production deployments on public repositories, always configure `reporterLogins`.

The check is enforced at the operator webhook handler and at the cron scan - no issue bypasses it regardless of how it arrives.

## Layer 2: Bot-authorship gate at egress

The operator validates the SCM identity of commit authors before accepting writeback. Only commits authored by `botLogin` are written back to the SCM. An injected instruction that attempts to impersonate a human author is rejected at the egress boundary.

## Layer 3: Headless mode with denied interactive pickers

Agent pods run with Claude Code's interactive pickers (`AskUserQuestion`, `ExitPlanMode`, `EnterPlanMode`) **hard-denied** in `settings.json`. An injected instruction that attempts to use these tools to break the headless loop is blocked at the tool-use layer.

This is enforced in `settings.json` at the settings level (not via a prompt instruction), so issue content cannot re-enable them.

## Layer 4: MCP tool surface gating per task kind

The operator sets `TATARA_TOOL_PROFILE` per task kind. The `tatara-cli` MCP server filters available tools at startup:

| Profile | Excluded tools |
|---|---|
| `review` | No commit-push tools; cannot write code to SCM |
| `brainstorm` | No SCM write tools; proposal creation only |
| `incident` | Grafana + investigation tools only |
| `implement` | Full surface |

A successful injection into a `review` agent gains no ability to push commits - the tool is simply absent.

## Layer 5: Human approval before implementation

Even if an injected issue body tricks the triage agent into a bad plan, a human maintainer must approve the plan in the issue thread before any code is written. See [Approval Gates](approval-gates.md).

## Layer 6: PR review before merge

All code changes are visible as a PR. A human reviewer can detect injected behavior in the diff and decline the PR before merge.

## Threat model

| Threat | Defense |
|---|---|
| Malicious issue body tricks agent into exfiltrating secrets | `reporterLogins` allowlist drops non-allowlisted issues before processing; agent pod egress is constrained by the managed NetworkPolicy (DNS + allowlisted in-cluster services + `443` for SCM/Anthropic/Keycloak); the Anthropic credential (`CLAUDE_CODE_OAUTH_TOKEN`) is mounted from an in-pod Secret only |
| Issue body instructs agent to push to unrelated branch | Bot PAT only has `repo` scope on enrolled repos; no cross-org access |
| Issue body instructs agent to open PR to a different repo | Agent clones only repos in `reposInScope`; push is gated to the task branch on enrolled repos |
| Issue body sets up a loop (agent reopens closed issue) | Dedup by issue ref; a closed issue's lifecycle task is terminal and not re-queued |
| Webhook replay attack | HMAC-SHA256 signature with rotating secret; validated on every webhook delivery |
| Bot self-loop (bot's own PR comments triggering new tasks) | `botLogin` excluded from intake; bot-authored issue events are dropped |

## Recommendations for sensitive environments

1. **Always set `reporterLogins`** - enumerate explicitly who can drive agent activity
2. **Always set `maintainerLogins`** - enforce the gated approval chain
3. **Use `mergePolicy: afterApproval`** - require human merge
4. **Enable branch protection** - require PR review before merge on enrolled repos
5. **Monitor intake rejections** - a reporter-allowlist drop is counted as `operator_webhook_events_total{result="ignored"}` (there is no `dropped` result value; querying `result="dropped"` returns nothing and any alert on it would silently never fire). Note `ignored` also covers other benign no-op events (bot-authored, non-actionable actions), so scope the query by `kind`/`action` when alerting.
6. **Audit commits** - `git log --author=<botEmail>` to review all autonomous commits
