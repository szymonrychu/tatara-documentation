# Implement-refusal codification — design

**Goal:** Make "the agent declined to implement" a first-class, codified outcome.
The lifecycle must NOT let a no-PR Implement run exit cleanly until a justification
is posted on the issue. Mirrors the existing Triage `issue_outcome` mechanism.

**Approved decisions (user):**
1. **Explicit declaration** — the agent declares refusal via a new MCP tool with a
   mandatory reason (distinct from a silent no-diff/failed run).
2. **Re-prompt until explained, capped** — if the agent exits with no PR and no
   declared decline, re-enter Implement (reuse the `ImplementEmptyRetries` cap) with
   a prompt demanding implement-or-decline-with-reason; after the cap, park for human.
3. **Reuse `tatara-declined`** label + park reason `refused`; post the reason as an
   issue comment.

## Cross-repo contract (mirror the `issue_outcome` flow exactly)

The Triage analog is the template: tatara-cli `issue_outcome` MCP tool → POST
`/tasks/{task}/issue-outcome` → operator writes `Task.Status.IssueOutcome{Action,Comment}`
→ `finishTriage` reads + branches. Build the Implement analog the same way.

### tatara-cli (MCP server)
- New OperatorTool `decline_implementation` with input schema
  `{"type":"object","properties":{"reason":{"type":"string","description":"Why you are NOT implementing this issue (what you considered, why it should not be done / is already done / is wrong). Posted to the issue."}},"required":["reason"]}`.
- Build(): resolve task via `argOrEnv(a,"task","TATARA_TASK")`; POST
  `/tasks/{task}/implement-outcome` with body `{"action":"declined","reason":<reason>}`.
- Register in the OperatorTools list (mirror `issue_outcome` registration). Add a
  unit test mirroring the issue_outcome tool test. The cli `tatara mcp` tools/list
  must still serve WITHOUT a token (build-guard) — `decline_implementation` is an
  operator tool, same as issue_outcome, so this holds.

### tatara-operator
1. **CRD** (`api/v1alpha1/task_types.go`): add
   ```go
   // ImplementOutcome is the agent's declared outcome for an implement task when
   // it opens no PR (e.g. a deliberate refusal). Mirrors IssueOutcome.
   type ImplementOutcome struct {
       // +kubebuilder:validation:Enum=declined
       Action string `json:"action"`
       Reason string `json:"reason"` // required; why no implementation
   }
   ```
   Add `ImplementOutcome *ImplementOutcome json:"implementOutcome,omitempty"` to
   `TaskStatus` (next to `IssueOutcome`). Regenerate CRDs (`make manifests`).
2. **REST handler** (mirror the issue-outcome handler, wherever it lives — the
   `/tasks/{task}/issue-outcome` POST handler): add `POST /tasks/{task}/implement-outcome`
   that decodes `{action, reason}`, validates `action=="declined"` + non-empty `reason`
   (400 otherwise), and writes `task.Status.ImplementOutcome` via status update
   (RetryOnConflict, same as issue-outcome).
3. **`finishImplement`** (`internal/controller/lifecycle.go`) — the no-PR branch
   (currently the `fresh.Status.PrURL == ""` block with `ImplementEmptyRetries` retry
   then park `no-change`/`implement-empty`). REPLACE its logic:
   - **PR opened** → unchanged (proceed to MRCI).
   - **No PR + `ImplementOutcome != nil && Action=="declined" && strings.TrimSpace(Reason) != ""`**
     → CODIFIED REFUSAL: post the Reason as an issue comment (use the existing SCM
     writer comment path, like triage discuss/close), apply the `declined`
     lifecycle label (`lifecycleLabels`/`ensurePhaseLabel`), `setLifecycleState`
     Parked reason `refused`, clear `ImplementOutcome` + reset `ImplementEmptyRetries`.
   - **No PR + no declared decline** → reuse `ImplementEmptyRetries`: if retries < cap
     (2) → `setImplementContext(reentryPrompt)` + `resetAgentRun` + increment (re-enter
     Implement). At cap → park reason `refused-no-explanation`, apply `declined` label,
     post a comment noting the agent neither implemented nor explained (needs human),
     clear retries. (This replaces the old `implement-empty`/`no-change` park.)
   - **Agent run failed** (crash/timeout) → unchanged `implement-failed` park (NOT
     gated on an explanation — a crashed agent can't author one).
   - Reset `ImplementOutcome` whenever entering Implement (in `setLifecycleState` to
     `Implement`, alongside the existing `ImplementEmptyRetries` reset).
4. **Implement-phase prompt** (`internal/controller/turnloop.go` — the implement
   turn/goal text + the empty-implement reentry prompt): add a hard instruction:
   "If after investigation you will NOT implement this, you MUST call
   `decline_implementation` with a clear reason (what you considered and why it
   should not / need not be done). A silent finish with no PR and no
   `decline_implementation` call is NOT allowed and will be re-prompted." The reentry
   prompt (when re-entered with no PR + no decline) states this explicitly.

## Out of scope / unchanged
- Triage `issue_outcome` (close/discuss/implement) unchanged.
- MRCI/Merge/MainCI unchanged.
- The full feature needs a cli release + wrapper `TATARA_CLI_VERSION` bump (so the
  agent's `tatara mcp` exposes `decline_implementation`) + operator deploy. The
  operator enforcement (re-prompt/park) works immediately; the agent can only
  DECLARE once the cli+wrapper ship.

## Tests (TDD)
- cli: `decline_implementation` builds the correct POST (path, body, required reason).
- operator: REST handler writes ImplementOutcome (+ rejects empty reason / wrong action);
  finishImplement: declared-decline → park `refused` + comment + declined label;
  no-decline → re-prompt then park `refused-no-explanation` at cap; PR-opened path
  unaffected; ImplementOutcome reset on Implement entry.

## Deploy chain
operator (CRD+REST+lifecycle+prompt) → operator image → tatara-helmfile bump;
cli (MCP tool) → cli release → wrapper TATARA_CLI_VERSION bump → wrapper image →
agent.image bump. Operator REST endpoint must exist before the cli tool is used
(else 404), so deploy operator first.
