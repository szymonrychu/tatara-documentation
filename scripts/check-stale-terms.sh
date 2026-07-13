#!/usr/bin/env bash
# Fails when an in-nav docs page mentions a concept the task-centric redesign
# deleted. docs/appendix/** is a dated archive and is never checked.
#
# LINE-LEVEL SUPPRESSION: a page that must legitimately NAME a dead term (e.g.
# to say it was removed) does not need a whole-file exemption. Add an HTML
# comment on that line naming the term(s) it exempts, comma-separated:
#
#   Auto-merge is never armed. <!-- stale-ok: auto-merge -->
#   `approvalLabel`, `ideaLabel` and `rejectedLabel` are removed from the CRD. <!-- stale-ok: approvalLabel, ideaLabel, rejectedLabel -->
#
# The marker must NAME every term it exempts - a blanket `<!-- stale-ok -->`
# exempts nothing, so it cannot silently hide an unrelated stale claim. Any
# OTHER dead term on the same line that is not named still fails the guard.
# Matching is case-insensitive. An HTML comment renders invisibly in mkdocs,
# so the published page is unaffected. Bash 3.2 (macOS) compatible - no
# mapfile, no associative arrays, no `${var,,}`.
set -euo pipefail

cd "$(dirname "$0")/.."

# One term per line. Extended regex, case-insensitive.
TERMS='lifecycleState|cascadeStage|deployedVersion|deployArtifact|deployDeadline
|status\.phase|Task\.Status\.Phase|turnsCompleted|resultSummary|prOutcome|issueOutcome
|implementOutcome|brainstormOutcome|reviewVerdict|changeSummary|change_summary
|pendingInterjections|pendingComments|conversationObjectKey|sessionID|implementContext
|implementGiveUps|linksSyncFailures|handover
|Subtask|subtask|WorkItem|workItem|workitem
|tatara-chat|tatara-mcp-chat|chat room|chat_handoffs
|auto-merge|auto merge|autoMerge|enablePullRequestAutoMerge|gh pr merge|pr_outcome
|maxConcurrentTasks|queuedAutonomousCap|approvalLabel|approvedLabel
|ideaLabel|rejectedLabel|tatara-approved
|issueLifecycle|triageIssue|selfImprove|healthCheck
|deploy supervisor|deploy-supervisor|deploy supervision
|review_verdict|create_subtask|update_subtask|list_subtasks|comment_on_issue
|maxTaskTokens|deployBudgetSeconds|deploySingleHopBudgetSeconds
|five CRDs
|approval label|applying a label|approves? by applying a label
|other six live kinds|six live kinds
|72 tools|42 tools|74 tools|6-tool operator surface|4-tool .alwaysOn. set
|proposedIssue|ProposedIssueSpec|reposInScope|systemicGroup|SystemicGroup
|approvalRequired|maxTurns\b|TaskSource\b
|gateEnteredAt|lastActivityAt|deadlineAt\b|headBranch\b|prNumber\b
|mergeCommitSHA|mergedHeadSHA|cumulativeTokens|lifecycleIterations
|parkReason|approvedByMaintainer|autoApproved\b|mergeWaitDeadline
|reviewResolveDeadline|issueLinks|prLinks\b
|discoveredIssues|linksSyncedURLs|writebackSkip4xxAttempts|disarmFailures'

# Collapse the multi-line term list into one alternation.
PATTERN="$(printf '%s' "$TERMS" | tr -d '\n')"
MARKER_RE='<!--[[:space:]]*stale-ok:[^>]*-->'

LEDGERED=()

# extract_marker_terms LINE
# Prints one lowercased, trimmed exempted term per line (nothing if no marker).
# Honours EVERY stale-ok marker on the line, not just the first.
extract_marker_terms() {
  local line="$1" marker body part oldifs
  while IFS= read -r marker; do
    [[ -z "$marker" ]] && continue
    body="${marker#*stale-ok:}"
    body="${body%-->}"
    oldifs="$IFS"
    IFS=','
    for part in $body; do
      IFS="$oldifs"
      part="$(printf '%s' "$part" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' | tr '[:upper:]' '[:lower:]')"
      [[ -n "$part" ]] && printf '%s\n' "$part"
    done
    IFS="$oldifs"
  done < <(printf '%s\n' "$line" | grep -oE "$MARKER_RE" || true)
}

fail=0
while IFS= read -r page; do
  rel="${page#docs/}"
  for l in "${LEDGERED[@]:-}"; do
    [[ "$rel" == "$l" ]] && continue 2
  done
  while IFS=: read -r lineno linetext; do
    [[ -z "$lineno" ]] && continue
    marker_terms="$(extract_marker_terms "$linetext")"
    hits="$(printf '%s\n' "$linetext" | grep -oEi "$PATTERN" || true)"
    line_fails=0
    while IFS= read -r hit; do
      [[ -z "$hit" ]] && continue
      hit_lc="$(printf '%s' "$hit" | tr '[:upper:]' '[:lower:]')"
      exempted=0
      if [[ -n "$marker_terms" ]]; then
        while IFS= read -r mt; do
          [[ -z "$mt" ]] && continue
          [[ "$hit_lc" == "$mt" ]] && { exempted=1; break; }
        done <<<"$marker_terms"
      fi
      [[ "$exempted" -eq 0 ]] && line_fails=1
    done <<<"$hits"
    if [[ "$line_fails" -eq 1 ]]; then
      printf '%s:%s:%s\n' "$page" "$lineno" "$linetext"
      fail=1
    fi
  done < <(grep -nEi "$PATTERN" "$page" || true)
done < <(find docs -name '*.md' -not -path 'docs/appendix/*' | sort)

if [[ "$fail" -ne 0 ]]; then
  echo
  echo "FAIL: the pages above mention a concept the task-centric redesign deleted."
  echo "Rewrite the page. There is no ledger any more."
  exit 1
fi

echo "OK: no stale terms"
