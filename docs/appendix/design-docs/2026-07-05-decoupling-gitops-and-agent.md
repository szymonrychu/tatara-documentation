# Decoupling tatara from helmfile-GitOps and Claude Code - Design

**Date:** 2026-07-05
**Status:** design, pre-plan. Portability roadmap; NO code in this doc.
**Scope:** Two pluggable seams so the platform stops requiring the maintainer's
personal stack. (1) A deploy/gitops interface behind which helmfile+GitHub is
ONE implementation among argo / flux / plain-CI. (2) A coding-agent interface
behind which Claude Code is ONE adapter among others. Concrete steps, affected
code (file:line), phasing, honest tech-debt.

Out of scope (tracked as roadmap items, not in this doc): the memory stack
`enabled` toggle, the auth-disabled mode, and opening the `{github,gitlab}` SCM
set. Those are genuine hard requirements too, but orthogonal to the two seams
this doc closes.

## North star

The maintainer's directive: "The CD pipeline (tatara-helmfile + GitHub Actions)
is MY way of running tatara. It cannot be a REQUIREMENT to run tatara for other
projects. I don't want to require helmfile, or Claude in the end - just gitops
and a coding agent."

So: helmfile CD and Claude Code are the maintainer's runtime choices, not
platform requirements. This doc closes the gap between that intent and what
ships today.

## Honest current state (verified, file:line)

The architecture *anticipated* both seams; the values behind them are welded.

**Deploy.** The deploy ACT is already fully external: the operator makes zero
helm/kubectl/helmfile calls, it only WATCHES a cascade. Swap the mechanism for
argo/flux/kubectl-in-CI and the core reconcile loop is untouched. But the
operator's deploy-CONFIRMATION feature (close the issue only once the code is
actually applied) is welded to helmfile+GitHub by six non-configurable Go
values, none on any CRD:

| # | Welded value | Location |
|---|---|---|
| 1 | `helmfileRepoName = "tatara-helmfile"` | `internal/controller/deploy_supervision.go:26` |
| 2 | `applyWorkflowFile = "apply.yaml"` | `deploy_supervision.go:28` |
| 3 | `deployPinFiles` (helmfile file layout) | `deploy_supervision.go:44-49` |
| 4 | `releaseArtifact` (release-name -> component map) | `deploy_supervision.go:501-507` |
| 5 | `helmfileReleaseRe` (`.gotmpl` `- name:` regex) | `deploy_supervision.go:510` |
| 6 | GitHub-only `DeployWatcher` (`github_deploy.go` sole impl) | `deploy_supervision.go:193-197` |

Confirmation is done by GET-ing git-committed helmfile files at the apply SHA and
regex-matching version pins (`helmfilePinState`, `deploy_supervision.go:615`), and
by reading the highest `vX.Y.Z` git tag. A GitLab reader logs `cascade
unsupervisable here (cascade is GitHub-only)` and parks
(`deploy_supervision.go:197`). OCI-digest / commit-SHA CD flows (argo
image-updater, flux) publish no matching signal.

Worse, the helmfile-coupled path is the HOT PATH, not an opt-in. Declaring a
change `Significance` is REQUIRED at the `change_summary` MCP tool, so
`pushCDEligible` (`deploy_supervision.go:53-58`) is effectively true for every
implemented change. The no-helmfile escape hatch DOES exist - a change with no
significance takes a legacy close-on-merge path that touches no helmfile and
works on GitHub or GitLab - but the platform logs it as an anomaly
(`writeback.go:311-314`, `WritebackOutcome("opened_no_significance")`) and steers
agents toward push-CD.

Net: tatara CAN run with zero helmfile coupling today, but only by surrendering
deploy-confirmation and swimming against the current.

**Agent.** The operator<->agent WIRE is genuinely generic. `internal/agent/
session.go:27` is `SubmitTurn(ctx, baseURL, text, callbackURL) (turnID, err)`;
the wrapper serves `POST /v1/messages`, `GET /v1/messages/{turnID}`,
`POST /internal/turn-complete`, `GET /readyz` (`httpapi/api.go:162-176`); the
turn-complete callback carries agent-neutral `{finalText, usage, stopReason}`
(`turn/turn.go:23-25`). The operator is ~90% agent-agnostic across this seam.

But below the seam there is exactly ONE adapter and it is a hand-built,
version-specific Claude Code TUI supervisor. The Claude-specific behaviour is
already cleanly partitioned into three wrapper packages, which is the natural
adapter boundary:

- `internal/session` - PTY keystroke driving. `pty.Start` (`session/pty.go:72`),
  bracketed-paste submission (`DefaultSubmitSeq`, `session/session.go:27`),
  "Bypass Permissions mode" dialog nav by writing `\x1b[B` down-arrow
  (`session/session.go:426-431`), `--continue` resume.
- `internal/transcript` - parses `~/.claude/projects/*.jsonl` to recover the
  final assistant text + Anthropic-shaped token usage
  (`transcript/result.go:79,176-179`: Input/Output/CacheRead/CacheCreation).
- `internal/bootstrap` - writes the whole `.claude/` tree: `settings.json`,
  `.claude.json` onboarding seed, `.claude/skills/*`, `.claude/agents/*`
  (`agents.go`), hooks (`hooks.go`, the cc-stop-hook), `.mcp.json` MCP wiring.

And the coupling LEAKS UP into the operator:

| Leak | Location |
|---|---|
| Refuse to build a Pod without an Anthropic secret | `internal/agent/pod.go:135-136`, `ValidatePodSecretRefs` |
| Inject credential under hardcoded env name | `pod.go:460`, `secretEnv("CLAUDE_CODE_OAUTH_TOKEN", cfg.AnthropicSecretName, "oauth-token")` |
| Hardcoded OIDC audience literal | `pod.go:459`, `{Name: "OIDC_AUDIENCE", Value: "tatara-claude-code-wrapper"}` |
| `claude-opus-4-8` literal as incident "safe tier" | `internal/incident/goal.go:61` |
| Worker subagent default `"sonnet"` | wrapper `TATARA_WORKER_MODEL` default, `bootstrap/bootstrap.go:55` |

"Swap the agent" today means writing a brand-new wrapper binary AND editing
operator pod-spawn plus CRD validation. Not a config flip. This is real
engineering, and this doc scopes it.

---

## Part 1 - Pluggable deploy / gitops interface

### 1.1 The minimal contract

The operator does NOT deploy; it OBSERVES a deploy converging so it can close the
originating issue with confidence. The contract is therefore an *observer*, not a
*deployer*. Rename the seam from `DeployWatcher` (GitHub-workflow-shaped) to a
provider-neutral `DeployObserver` with three questions:

```
type DeployObserver interface {
    // PublishedVersion: the opaque identity the merged change published for a
    // component (a semver tag, an OCI digest, a commit SHA - matched by equality,
    // never parsed). Empty => not yet published.
    PublishedVersion(ctx, component ComponentRef) (version string, found bool, err error)

    // ConvergenceOutcome: has the configured deploy target applied `version`
    // for `component` to the cluster yet? Pending | Applied | Failed, plus an
    // opaque human URL for the failing/succeeding run.
    ConvergenceOutcome(ctx, component ComponentRef, version string) (Outcome, error)

    // AppliedState (optional, capability-gated): the version currently live for
    // `component`, read from authoritative cluster/CD state rather than inferred.
    AppliedState(ctx, component ComponentRef) (version string, found bool, err error)
}
```

Key inversions from today:

1. **Confirm against LIVE state, not git files.** The operator already has a
   controller-runtime client. An `Applied` verdict should come from the live
   Deployment image / an argo `Application.status.sync` / a flux
   `Kustomization.status.lastAppliedRevision` - NOT by GET-ing helmfile `.gotmpl`
   files and regex-matching pins. This deletes welded values #3, #4, #5 outright:
   they only exist to reverse-engineer applied state out of git text.
2. **Version is an opaque token matched by equality.** No semver assumption, no
   `vX.Y.Z` tag read, no substring matching (today's `tokenMatch` /
   `helmfileReleaseRe`). A digest or SHA drops in unchanged.
3. **The target is configuration, not a constant.** Welded values #1, #2 (the
   `tatara-helmfile` repo name, the `apply.yaml` workflow) move onto a
   per-Project descriptor.

### 1.2 The DeployTarget descriptor (CRD)

Add `Spec.deploy` to the Project CRD (`api/v1alpha1/project_types.go`), mirroring
the existing optional-block pattern (`GrafanaSpec`, `project_types.go:43`):

```
type DeploySpec struct {
    // Mode selects the observer. "" or "none" => no deploy supervision
    // (legacy close-on-merge is the SUPPORTED steady state, not an anomaly).
    Mode string `json:"mode,omitempty"`          // none | helmfile | argocd | flux | ...
    // Target names the terminal CD repo/app the cascade ends at.
    Target string `json:"target,omitempty"`       // was welded "tatara-helmfile"
    // ConfirmSignal names the applied-signal source (workflow file, argo app,
    // flux kustomization) the observer polls. Interpreted by the chosen Mode.
    ConfirmSignal string `json:"confirmSignal,omitempty"`  // was welded "apply.yaml"
    // Components attributes a published version to a component. Replaces the
    // welded releaseArtifact map + deployPinFiles + regex. Optional: live-state
    // observers derive attribution from workload labels instead.
    Components []DeployComponent `json:"components,omitempty"`
}
```

Defaults preserve today's behaviour for the maintainer: an unset `Spec.deploy` on
the `tatara` Project resolves to `{mode: helmfile, target: tatara-helmfile,
confirmSignal: apply.yaml, components: <current releaseArtifact>}`. Nobody's
cluster changes.

### 1.3 helmfile becomes one implementation

`helmfileObserver` implements `DeployObserver` by wrapping exactly today's logic
(`LatestWorkflowRun` + `helmfilePinState` + tag read), reading its repo/workflow/
pin-file/regex config from `DeploySpec` instead of package constants. It is
registered under `mode: helmfile`. Two sibling implementations prove the seam:

- `argocdObserver` (`mode: argocd`): `PublishedVersion` = the image tag/digest an
  image-updater wrote; `ConvergenceOutcome`/`AppliedState` = read the `Application`
  CR `status.sync.status == Synced && status.health == Healthy` and
  `status.operationState.syncResult.revision`. No git-file parsing.
- `liveStateObserver` (`mode: kube`, the universal fallback): `AppliedState` =
  the live Deployment's container image tag/digest for the component's workload,
  read via the operator's existing k8s client. Works with ANY gitops mechanism
  (or none) because it observes the RESULT, not the pipeline. This is the
  "just gitops" case the north star names.

Registration is a `map[string]func(cfg DeploySpec) DeployObserver`, mirroring the
SCM `ByProvider` switch (`internal/scm/registry.go:33`). A new CD backend is a
drop-in.

### 1.4 Make no-CD a first-class mode (significance optional)

Today `pushCDEligible` is true for every change because `change_summary`
Significance is required, forcing the helmfile-coupled hot path. Demote it:

- Significance becomes REQUIRED only when `Spec.deploy.Mode != none`. When no
  deploy target is configured, `change_summary` accepts an empty significance and
  the change takes the legacy close-on-merge path WITHOUT the WARN
  (`writeback.go:311-314` stops treating it as an anomaly; retitle the metric
  outcome from `opened_no_significance` to `no_deploy_supervision`).
- `pushCDEligible` gains a guard: `return deployMode != "none" && cs != nil &&
  cs.Significance != ""`.

Result: "run tatara with no deploy supervision at all" is a supported, quiet
mode - not swimming against the current.

### 1.5 Affected code (Part 1)

- `internal/controller/deploy_supervision.go` - lift constants #1-#5 into
  `DeploySpec`; replace the `scm.DeployWatcher` type assertion
  (`deploy_supervision.go:193`) with a `DeployObserver` resolved by
  `Spec.deploy.Mode`; delete `helmfilePinState` / `helmfileReleaseRe` /
  `tokenMatch` from the default path (retain inside `helmfileObserver`).
- `internal/scm/github_deploy.go` - becomes the guts of `helmfileObserver`; no
  longer the sole DeployWatcher.
- `api/v1alpha1/project_types.go` + `zz_generated.deepcopy.go` + CRD manifest -
  add `DeploySpec`.
- `internal/controller/writeback.go:218-233,305-314` - gate significance/WARN on
  `Spec.deploy.Mode`.
- `change_summary` REST handler + the tatara-cli MCP tool schema - significance
  optional-when-no-target.
- **CI (out-of-repo):** the maintainer's `tatara-helmfile` + `cd-release` action
  are unchanged; they remain the reference `helmfile` implementation. An argo/flux
  adopter writes NO operator code - they set `Spec.deploy.Mode` and point
  `ConfirmSignal` at their app/kustomization.

---

## Part 2 - Pluggable coding-agent interface

### 2.1 The exec contract (already 90% there)

Formalize the existing HTTP seam as the canonical `AgentRuntime` wire contract.
It already exists; this step DOCUMENTS and FREEZES it, then makes the operator
address it neutrally:

```
POST /v1/messages          {text, callbackUrl} -> {turnId}      # submit a turn
GET  /v1/messages/{turnId}  -> {state, finalText, usage, stopReason}
POST /internal/turn-complete {turnId, finalText, usage, stopReason}  # hook -> callback
GET  /readyz                -> 200 when the agent session is live
```

`usage` is currently an Anthropic-shaped `json.RawMessage`
(`turn/turn.go:25`, Input/Output/CacheRead/CacheCreation). Freeze it as a
provider-neutral shape `{inputTokens, outputTokens, cacheReadTokens,
cacheCreationTokens, costUsd?}`; each adapter maps its provider's usage into it.
This is the ONLY wire type that leaks a provider today and it is a rename, not a
redesign - the operator's token metrics already consume exactly these fields.

### 2.2 The AgentRuntime adapter (wrapper side)

Inside the wrapper, introduce an `AgentRuntime` interface that the neutral
`httpapi` layer drives, with the Claude implementation being the three existing
packages behind it:

```
type AgentRuntime interface {
    Boot(ctx, BootConfig) error        // launch + reach ready
    SubmitTurn(ctx, text string) (turnID string, err error)
    // completion arrives via the runtime calling back into the turn store;
    // the runtime owns HOW it detects turn end and extracts final text + usage.
    Ready() bool
    Shutdown(ctx) error
}
```

- `claudeRuntime` = today's `internal/session` (PTY, bracketed paste, bypass
  dialog, `--continue`) + `internal/transcript` (jsonl parse) + `internal/
  bootstrap` (the `.claude/` tree). ALL Claude specifics stay inside this
  adapter. `httpapi`, `auth`, `pushclient`, `turn`, `convstore`, `metrics` stay
  provider-neutral and already are.
- `BootConfig` carries NEUTRAL intent the operator passes: headless flag,
  allowed-tools, instruction TEXT (not `CLAUDE.md`), skill-profile selection, MCP
  endpoints (MCP is already cross-vendor, so tatara-cli tools port as-is), model
  id, effort, worker model/effort. Each adapter RENDERS its own agent's config
  from this - the Claude adapter writes `settings.json` / `.claude.json` /
  `.claude/skills` / `.mcp.json`; another adapter writes whatever its agent reads.
- A second adapter (even a thin `execRuntime` that shells a headless
  `agent --print` CLI and reads stdout for the final message) proves the seam
  compiles against something that is NOT Claude. It need not be production-grade
  to validate the interface.

### 2.3 Break the operator's Claude preconditions (operator side)

The pod-spawn leaks (table above) move onto a per-Project agent-adapter
descriptor `Spec.agent.runtime` (extend the existing `AgentSpec`,
`project_types.go:101`):

```
type AgentRuntimeSpec struct {
    // Image the wrapper adapter runs (was implicitly the claude wrapper).
    Image string `json:"image,omitempty"`
    // CredentialSecretRef + CredentialEnvName + CredentialSecretKey replace the
    // hardcoded ANTHROPIC precondition and CLAUDE_CODE_OAUTH_TOKEN name.
    CredentialSecretRef string `json:"credentialSecretRef,omitempty"`
    CredentialEnvName   string `json:"credentialEnvName,omitempty"`  // was "CLAUDE_CODE_OAUTH_TOKEN"
    CredentialSecretKey string `json:"credentialSecretKey,omitempty"` // was "oauth-token"
    // OidcAudience replaces the pod.go:459 literal.
    OidcAudience string `json:"oidcAudience,omitempty"`  // was "tatara-claude-code-wrapper"
}
```

- `ValidatePodSecretRefs` (`pod.go:135-136`) stops hard-failing on empty
  `AnthropicSecretName`; it validates whatever `CredentialSecretRef` the
  descriptor names (and only if the descriptor requires a credential at all -
  some agents auth via OIDC alone).
- `pod.go:459-460` reads env name / secret key / OIDC audience from the
  descriptor instead of the three literals.
- Defaults reproduce today's Claude wiring for the `tatara` Project, so nothing
  in the live cluster changes.

### 2.4 Parameterize the residual Claude literals

- `internal/incident/goal.go:61` - the tier-revert "safe tier" `claude-opus-4-8`
  becomes `Spec.agent.safeTierModel` (+ safe effort). Provider-neutral default.
- wrapper `TATARA_WORKER_MODEL="sonnet"` default (`bootstrap/bootstrap.go:55`) -
  keep the env override; drop the Claude-specific literal default to empty and
  require the operator to pass it (it already can, via `ModelByKind` plumbing).
- Keep `claudeSubscription` budget Mode as ONE pluggable mode behind the existing
  `usedPercent` switch, with a provider-neutral default (cost/turn-count) and the
  Anthropic `anthropic-ratelimit-unified-*` header read confined to that mode.

### 2.5 Affected code (Part 2)

- **Wrapper:** new `internal/runtime` (interface + `claudeRuntime` wiring the
  existing `session`/`transcript`/`bootstrap`); `httpapi` calls the interface;
  freeze the neutral `usage` shape in `turn/turn.go`. No behaviour change for the
  Claude path - pure extraction under test.
- **Operator:** `AgentSpec` gains `AgentRuntimeSpec` + `safeTierModel`
  (`project_types.go`); `pod.go:135-136,459-460` read the descriptor;
  `incident/goal.go:61` reads `safeTierModel`. CRD manifest + deepcopy regen.
- **Branding (cosmetic, low priority):** the OIDC audience literal, the
  `ANTHROPIC_SECRET_NAME` operator config env, the `.claude/` conventions - rename
  toward a neutral runtime identity as adapters land; not blocking.

---

## Phasing and sequencing

Both parts are independent (different files, no shared state) and can run in
parallel. Within each, ship the descriptor + default-preserving refactor FIRST
(zero behaviour change, fully testable), then the second implementation that
proves the seam.

**Deploy track (tatara-operator only):**

- D1. `DeploySpec` CRD + resolve `mode: helmfile` from it; wrap today's logic as
  `helmfileObserver` reading config from the descriptor. Default-preserving. No
  cluster change. (Ship inert-equivalent.)
- D2. Introduce the `DeployObserver` interface + registration map; migrate the
  `deploy_supervision.go:193` assertion off `scm.DeployWatcher`.
- D3. `liveStateObserver` (`mode: kube`) - confirm against live Deployment image.
  Proves provider-neutral confirmation.
- D4. Significance optional + quiet no-deploy mode (`writeback.go`,
  `pushCDEligible`, `change_summary`). Makes "no CD" a supported steady state.
- D5. (optional, later) `argocdObserver` when a real argo adopter appears.

**Agent track (tatara-claude-code-wrapper -> tatara-operator):**

- A1. Wrapper: extract `AgentRuntime` interface; move `session`+`transcript`+
  `bootstrap` behind `claudeRuntime`; freeze neutral `usage` shape. Pure
  refactor under existing tests. (wrapper ships first - operator depends on the
  frozen wire.)
- A2. Operator: `AgentRuntimeSpec` descriptor; `pod.go` reads it; drop the
  hardcoded Anthropic precondition + `CLAUDE_CODE_OAUTH_TOKEN` +
  audience literal. Default-preserving.
- A3. Operator: parameterize `incident/goal.go` safe tier + worker default.
- A4. A second thin adapter (`execRuntime`) in the wrapper proving the interface
  compiles against a non-Claude agent. Validation only.

Cross-track ordering: A1 (frozen wire) gates A2. Otherwise fully parallel.
Deploy default-preserving refactors (D1-D2) and agent refactors (A1-A2) can all
land the same week without touching each other.

## Risks and tech-debt (honest)

- **Refactor blast radius, no user-visible win at first.** D1-D2 and A1-A2 are
  large diffs that change NOTHING observable (defaults reproduce today). Review
  fatigue + regression risk are real. Mitigation: they are pure extractions
  guarded by the existing deploy-supervision and wrapper test suites; require
  green + a `helmfile diff` no-op before merge. Do NOT bundle with behaviour
  changes.
- **live-state confirmation is weaker than git-pin confirmation.** Today's git
  regex proves the EXACT version the operator cut got applied. `liveStateObserver`
  proves the live image EQUALS the published version - equivalent when the version
  is a tag/digest, but a `latest`-tag or mutable-tag cluster would false-confirm.
  Document that `mode: kube` requires immutable version identities; keep the
  helmfile/argo observers for pin-exact confirmation.
- **Second agent adapter is a stub, not proof of production portability.** A4's
  `execRuntime` proves the INTERFACE, not that any real non-Claude agent drives
  the full loop well. Do not oversell: after this work, tatara is *architecturally*
  agent-pluggable with ONE production adapter (Claude) and one toy. Shipping a
  second production agent is separate, larger work.
- **CRD surface growth.** Two new optional spec blocks (`deploy`, `agent.runtime`)
  plus fields. Kept optional + defaulted so existing Projects need no edit; still,
  more CRD to maintain. Acceptable per the north star.
- **Branding half-measure.** Renaming the OIDC audience / secret env / `.claude/`
  conventions is deferred as cosmetic. Until done, the identity surface still
  reads "claude" even where the code is neutral - a portability smell, not a
  blocker. Track it, don't gate on it.
- **Adjacent hard requirements untouched.** This doc does NOT make the memory
  stack or the OIDC IdP optional. A no-helmfile, no-Claude tatara STILL requires
  Kubernetes, a healthy memory stack (LightRAG+Neo4j+CNPG, `task_controller.go:226`
  gates every Task on `Memory.Phase=="Ready"`, `MemorySpec` has no `enabled`
  field), and an OIDC issuer (`config.go:455-465`). Those are the next two
  decouplings; see roadmap. Claiming "any gitops, any agent" while three hard deps
  remain would be dishonest - state it plainly.

## Definition of done

- A Project with `Spec.deploy.Mode: kube` (or unset on a non-helmfile cluster)
  runs the full issue->PR->merge->close loop, confirming deploys from live
  cluster state, with zero reference to `tatara-helmfile` or `apply.yaml`, and no
  `opened_no_significance` anomaly log.
- A Project pointing `Spec.agent.runtime` at a non-Claude adapter image spawns an
  agent Pod with NO Anthropic secret, NO `CLAUDE_CODE_OAUTH_TOKEN`, and completes
  a turn over `/v1/messages` returning neutral `{finalText, usage}`.
- The `tatara` Project itself is unchanged in behaviour: same helmfile cascade,
  same Claude wrapper, all via defaults.
