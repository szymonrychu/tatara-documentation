# Issue-lifecycle M4 - MR scope completeness - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** The Implement run reports what it delivered; the MR body states the
delivered scope; a partial delivery opens a follow-up issue for the remainder.
(Part B.)

**Architecture:** Per spec. The agent calls `change_summary` at the end of an
Implement run; the operator uses it for the MR title/body and the follow-up
decision. **Repos:** `tatara-cli` + `tatara-operator`.

---

### Task 1: change_summary MCP tool (cli)

**Files:** `tatara-cli/internal/mcp/tools.go`; Tests.

- [ ] Failing test: `change_summary` accepts `{pr_title, pr_body, delivered_scope, remaining_scope?}` (task auto-resolved from `TATARA_TASK`), POSTs to `/tasks/{tk}/change-summary`.
- [ ] Implement. Test PASS. Commit (cli) `feat(cli): change_summary tool`.

### Task 2: Operator stores change summary

**Files:** `api/v1alpha1/task_types.go` (status `changeSummary` sub-struct: `PRTitle`, `PRBody`, `DeliveredScope`, `RemainingScope`), operator REST handler; Tests.

- [ ] Failing test: `POST /tasks/{tk}/change-summary` writes `Status.ChangeSummary` fields.
- [ ] Implement (`make manifests generate` for the new status struct). Test PASS. Commit `feat(operator): change-summary endpoint + status`.

### Task 3: MR body uses delivered scope

**Files:** `internal/controller/lifecycle.go` (Implement open-MR path); Tests.

- [ ] Failing test: when `Status.ChangeSummary` is set, the opened MR uses `PRTitle` as the title and a body that includes `PRBody` + a "Delivered:" block from `DeliveredScope` + the `Closes #N` line (M1). When unset, fall back to the M1 body (`firstLine(Goal)` + `writeBackBody`).
- [ ] Implement. Test PASS. Commit `feat(controller): MR body describes delivered scope`.

### Task 4: Follow-up issue on remaining scope

**Files:** `internal/controller/lifecycle.go`; Tests.

- [ ] Failing tests: after opening the MR, if `Status.ChangeSummary.RemainingScope != ""` the operator `CreateIssue` in the source repo titled "Follow-up: <issue title> (remaining scope)" with a body describing `RemainingScope` and linking the PR URL; the new issue number is recorded (e.g. appended to `Status.DiscoveredIssues`). Empty remaining scope -> no issue. Idempotent: re-entry does not open a second follow-up (guard on a recorded marker).
- [ ] Implement (reuse `writer.CreateIssue`; the idempotency guard mirrors `createProposal`'s source-set guard - record the follow-up issue URL and skip if present).
- [ ] Test PASS. Commit `feat(controller): partial delivery opens a follow-up issue`.

---

## Self-review

- [ ] Follow-up issue creation is idempotent across re-implement/re-entry.
- [ ] MR body falls back gracefully when the agent did not call change_summary.
- [ ] Delivered/remaining scope is agent-self-reported (the operator cannot verify "full scope"; documented).
- [ ] Full suite green across touched repos; lint clean.
