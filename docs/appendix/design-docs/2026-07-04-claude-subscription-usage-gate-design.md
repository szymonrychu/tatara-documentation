# Claude subscription usage gate: complete `claudeSubscription` into a proactive per-kind spawn gate

Date: 2026-07-04
Status: approved design (brainstorm), ready for implementation planning

## Problem

The tatara fleet burned the Claude subscription too fast and had to be scaled
to 0. The whole fleet authenticates with ONE shared `CLAUDE_CODE_OAUTH_TOKEN`
(a personal Max-plan subscription OAuth token, `pod.go:452`), so every agent
pod draws from ONE account-level rate-limit bucket. That bucket is also shared
with the human operator's own Claude Code / claude.ai usage: tatara is not the
only consumer.

A dual-mode token budget already exists on operator `main` (issue #189/#194)
but the subscription half is inert:

- `TokenBudgetSpec.Mode` enum `customWindow | claudeSubscription`
  (`api/v1alpha1/project_types.go:408-414`). `customWindow` reconstructs spend
  from per-turn token counts; `claudeSubscription` is meant to gate on Claude's
  own 5h/weekly usage windows.
- The operator-side consumer exists: admission gate
  (`internal/controller/queue_controller.go:99-117`, `:249-250`), per-turn
  ingestion (`internal/controller/turncallback.go:108-125,304-378`), budget
  evaluation (`internal/budget/budget.go:138-173`), metrics
  (`operator_token_budget_used_ratio`, `operator_task_tokens_total`).
- The PRODUCER is missing. `turncallback.go:109` marks the rate-limit snapshot
  "absent until the wrapper is updated (subtask 7)". The wrapper never was:
  zero rate-limit / `oauth/usage` / `anthropic-ratelimit` code on wrapper
  `main`. So `claudeSubscription` gates on 0% forever.

The wrapper CANNOT be that producer cheaply: it drives `claude` over a PTY, so
it sees no HTTP response headers, no 429s, no `result.json`. It is structurally
blind to the subscription window state.

## Goals

1. Read and monitor the account's 5-hour, weekly, and per-model weekly usage
   windows continuously, on a timer, with NO agent session required (tatara is
   not the only consumer, so it must observe the shared account independently).
2. Track dynamic (rolling) windows via each window's reset timestamp.
3. Proactively gate pod spawning per kind: stop the most discretionary kinds
   at a low utilization %, protect incident response until near 100%. Ceilings
   under 100 reserve headroom for the human sharing the account.
4. Keep the existing `customWindow` (by-token-tracking) mode working unchanged;
   the mode remains a per-Project, operator-wide-defaulted config toggle.
5. Fail safe: an undocumented data source must never leave the fleet blind or
   unprotected.

## Non-goals

- No monthly rate-limit window. It does not exist for subscriptions. The only
  "monthly" construct is `extra_usage` (pay-as-you-go dollar overage), which is
  monitored READ-ONLY (dashboards) and never gates spawning (user decision).
- No renaming of the `customWindow | claudeSubscription` enum (merged; renaming
  is a CRD break + migration for zero semantic gain). Human-facing names map:
  `by-token-tracking = customWindow`, `claude-code-tracking = claudeSubscription`.
- No org Admin / Analytics API. It is org-gated and unreachable from an
  individual Max token, and exposes only daily-delayed history, no live windows.
- Not building the wrapper "subtask 7" per-turn producer. It is CANCELLED and
  superseded by the operator-side poller below.

## Chosen approach (decisions recorded)

Research (workflow `w8ywzl9z5`, 6 agents) established that NO documented, stable
Anthropic interface exposes live subscription-window state for standalone
polling:

- Native OTel (`claude_code.cost.usage` / `token.usage`) is documented and
  stable but carries NO window state.
- `anthropic-ratelimit-unified-*` response headers and statusLine `rate_limits`
  carry windows but only ride on agent traffic (need a live session).
- `GET /api/oauth/usage` is the ONLY source that returns account-wide window
  state on demand with no session, but it is undocumented and aggressively
  429-rate-limited.

Because goal 1 requires standalone continuous monitoring, the poller of
`/api/oauth/usage` is the forced choice. User decisions:

- Window source: poll `/api/oauth/usage`, hardened, with OTel as the documented
  fallback/backstop.
- Monthly overage (`extra_usage`): READ-ONLY (monitor, never gate).

## Architecture

### 1. Account-usage poller (the producer)

A new operator `Runnable` (manager-managed, leader-elected so exactly one
instance polls) ticks every `pollIntervalSeconds`.

- Request: `GET https://api.anthropic.com/api/oauth/usage`
  - `Authorization: Bearer <shared OAuth token>` (auth header is the #1 spike
    item; see Risks. Support `x-api-key` as a config-selectable fallback.)
  - `anthropic-beta: oauth-2025-04-20`
  - `User-Agent: claude-code/<pinned version>` (load-bearing: without the
    `claude-code` UA the endpoint returns persistent 429s)
- Token source: the SAME managed OAuth secret the pods mount (`pod.go:452`);
  the operator reads it directly. No new secret.
- Cadence: default `pollIntervalSeconds = 180`, HARD floor 180. Faster risks
  the endpoint's per-token 429 bucket.
- Response parse (normalize `utilization` to a 0-100 percent regardless of
  whether Anthropic returns 0-1 or 0-100):
  - `five_hour { utilization, resets_at }`
  - `seven_day { utilization, resets_at }`
  - `seven_day_opus`, `seven_day_sonnet` (each nullable; null = not applicable,
    treat as absent, never as 0)
  - `extra_usage { is_enabled, monthly_limit, used_credits, utilization }`
    (read-only)
- Health: consecutive-failure counter. After N failures (default 3) mark the
  snapshot `Stale`, emit an alert-worthy metric, and trigger degradation.
- Schema canary: if required fields are missing or types shift, treat as
  endpoint drift, mark `Stale`, and degrade (do not crash, do not gate on
  garbage).

### 2. Fleet-wide snapshot store (fixes the per-Project bug)

Subscription windows are account-wide, but today the snapshot is persisted
per-Project on `Project.Status.TokenBudget` and only refreshed when THAT
project runs a turn (`turncallback.go:304-342`), so N projects hold N stale
views of one shared account.

- One in-operator snapshot: a mutex-guarded struct owned by the poller,
  holding per-window utilization + reset + poll health + last-success time.
- Durability: mirror to an operator-owned ConfigMap `tatara-account-usage`
  (well-known name) so reset schedules and last-known utilization survive an
  operator restart, and so the state is externally observable.
- The admission gate reads the in-operator snapshot (all projects see the same
  account truth). The per-Project subscription persistence
  (`TokenBudgetStatus.FiveHourPercent/...`) and the `turncallback` `RateLimit`
  ingestion path are retired for subscription mode. `customWindow` per-project
  token accounting (`recordUsage` -> `CumulativeTokens`, `turncallback.go:275`)
  is untouched.

### 3. Per-kind spawn ladder (the proactive gate)

- New field `TokenBudgetSpec.spawnCeilingByKind`: `map[string]int32`, kind (or
  `kind`/`activity` pseudo-key, e.g. `healthCheck`) -> ceiling percent. Mirrors
  the existing `modelByKind` shape and camelCase-map convention.
- Evaluation per queued work item of kind K using model M:
  - `gatingUtilization = max(five_hour.util, seven_day.util,
    seven_day_<M>.util if present)`
  - if `gatingUtilization >= ceiling(K)` -> hold K's `QueuedEvents` in `Queued`
    (no pod spawn), reusing the existing `admitPool` mechanism
    (`queue_controller.go:99-117`). `budget.Evaluate` is extended to return a
    per-kind decision instead of the single `ProactiveBlocked/EmergencyBlocked`
    pair; the coarse pair is retained as the `customWindow` path and as a
    default when no per-kind ceiling is set.
- Keyed on kind+activity so `healthCheck` (which shares `Kind=brainstorm` but
  is tagged by `LabelActivity`) has its own ceiling.
- Reset-aware: when `resets_at` passes, the next poll shows lower utilization
  and held kinds resume automatically. No manual intervention.
- Default ladder (tunable per Project and operator-wide; low = cut first):

  | kind | ceiling % | kind | ceiling % |
  |---|---|---|---|
  | brainstorm | 40 | review | 75 |
  | selfImprove | 55 | issueLifecycle | 80 |
  | healthCheck | 60 | implement | 85 |
  | triageIssue | 70 | incident | 98 |

  `refine` is intentionally absent: it is a scan-pipeline barrier
  (`projectscan.go` `runScans` defers mrScan/issueScan/brainstorm/healthCheck
  until a refine Task reaches a terminal state), so a held refine never runs,
  never terminates, and wedges every scan behind it. `refine` always admits
  and is never gated, regardless of any configured ceiling.

### 4. OTel backstop + graceful degradation

Agent pods enable native Claude Code telemetry so the platform has a
documented, supported spend + error signal independent of the undocumented
endpoint:

- Env injected via wrapper `claudeEnv` (`cmd/wrapper/app.go:460-466`) and/or
  settings (`internal/bootstrap/settings.go`): `CLAUDE_CODE_ENABLE_TELEMETRY=1`,
  `OTEL_METRICS_EXPORTER=otlp`, `OTEL_LOGS_EXPORTER=otlp`,
  `OTEL_EXPORTER_OTLP_ENDPOINT=<in-cluster collector>`, delta temporality.
- Consumes `claude_code.cost.usage` (USD; gives the dead `ccw_turn_cost_usd_total`
  a real producer, closing review gap G1), `claude_code.token.usage`
  (type input/output/cacheRead/cacheCreation x model), and
  `claude_code.api_error` / `api_retries_exhausted` (status_code 429).
- Degradation ladder:
  1. Endpoint healthy -> proactive per-kind window ladder (primary).
  2. Endpoint `Stale` (>= N poll failures or schema drift) -> fall back to
     `customWindow` token gating if the Project configures it, keep alerting,
     and rely on the floor below.
  3. Always-on floor: any live-turn 429 (from the OTel `api_error` event, or a
     future in-band signal) -> emergency fleet stop regardless of mode. This is
     the documented safety net that catches the wall even if every window
     source is down.
- Dependency: an in-cluster OTLP collector must exist / be provisioned. If
  absent, the OTel backstop is a distinct rollout phase; the poller + ladder do
  not depend on it to function (only the fallback richness does).

## Data model / CRD changes (`api/v1alpha1/project_types.go`)

Additions to `TokenBudgetSpec`:

- `PollIntervalSeconds *int32` (default 180, CEL min 180)
- `SpawnCeilingByKind map[string]int32` (values 0-100; keys validated against a
  kind/activity enum where feasible, else BuildTime warn metric for unknown
  keys per review gap G7)
- `MonitorOverage *bool` (default false; read-only overage dashboards)

Additions to `TokenBudgetStatus` (now describing the fleet snapshot mirror, not
per-project state):

- Per-window `Utilization`, `ResetsAt` for `five_hour`, `seven_day`,
  `seven_day_opus`, `seven_day_sonnet`
- `ExtraUsage` (utilization, used, limit; read-only)
- `PollHealth` (healthy | stale), `LastSuccessAt`

Operator-wide defaults for all new fields flow through the existing config path
(`cmd/manager` config + wire), matching how `Mode`/`ProactivePercent` default
today.

## Config surface + naming

- Enum unchanged: `customWindow | claudeSubscription`. Docs + helmfile values
  carry the human labels `by-token-tracking` / `claude-code-tracking`.
- New values (camelCase in `values.yaml` -> kebab ConfigMap key -> `envFrom`,
  per repo rule 6): `pollIntervalSeconds`, `spawnCeilingByKind` (rendered as a
  templated ConfigMap since it is map-shaped), `monitorOverage`.
- Wrapper gains OTel-enable envs only; it needs NO usage-mode env (the poller
  lives entirely operator-side).
- New envs respect the set-but-empty pitfall (`config.go:215-227`,
  `envboolor-empty-bootcrash` incident): empty string must fall to default,
  not crash.

## Observability (`internal/obs/operator_metrics.go`, tatara-observability)

Operator-side (account-level, so `operator_`/`tatara_` prefix, not the
ephemeral wrapper push path):

- `tatara_account_usage_utilization{window=five_hour|seven_day|seven_day_opus|seven_day_sonnet}`
- `tatara_account_usage_resets_at{window=...}` (unix seconds)
- `tatara_account_usage_extra_utilization` + used/limit (read-only monthly)
- `tatara_account_usage_poll_health` (1 healthy / 0 stale), `..._poll_failures_total`
- Extend `operator_token_budget_used_ratio` and `operator_admission_blocked_total`
  with a `kind` dimension.

Dashboards (tatara-observability): per-window utilization + reset countdown,
per-kind admission state (which kinds are currently held), monthly overage $
(read-only), real cost from OTel `claude_code.cost.usage`. Alert rules:
poll_health stale, any window > operator emergency ceiling, overage climbing.

## Testing (TDD)

- `budget` ladder unit tests: per-kind ceiling vs window max, per-model window
  selection, boundary percentages, null per-model windows, unset ceiling ->
  coarse fallback.
- Poller parse tests: fixture responses (0-1 vs 0-100 utilization, null
  opus/sonnet, missing fields -> schema-drift path, `extra_usage` present/absent).
- Degradation tests: N poll failures -> Stale -> fallback; recovery clears Stale.
- Snapshot store tests: ConfigMap mirror round-trip, restart restores last state.
- Admission gate integration (envtest): a kind is held when its window crosses
  its ceiling and released after a simulated reset.
- Regression: `customWindow` mode path unchanged.

## Repos + rollout

- `tatara-operator` (primary): poller Runnable, snapshot store + ConfigMap
  mirror, per-kind ladder in `budget` + `queue_controller`, CRD fields,
  metrics, retire per-Project subscription persistence.
- `tatara-claude-code-wrapper` (backstop): OTel-enable env in `claudeEnv` /
  settings. No producer code.
- `tatara-observability`: dashboards + alert rules.
- `tatara-helmfile` (deploy): CRD value defaults (`spawnCeilingByKind`,
  `pollIntervalSeconds`, `monitorOverage`), OTLP endpoint wiring, operator
  access to the OAuth secret, dual chart+image pin.

Branch flow: a fresh worktree off `origin/main` per child repo (both child
repos currently sit on another agent's `feat/semver-push-cd` branch and MUST
NOT be touched), branch `feat/usage-window-gating`, push branch -> PR per repo.
Implementation subagents Sonnet, merge Opus (repo rule 7). Deploy only via
tatara-helmfile GitOps (repo rule 15).

Fleet re-enable is staged and gated on the spike (below) plus the existing
scale-to-0 posture: deploy with conservative ceilings, watch the per-window
dashboards, widen ceilings stepwise. This dovetails with review gap G3.

## Risks + open items

1. SPIKE (blocking, first step): verify the exact auth header (`Bearer` vs
   `x-api-key`) and that tatara's in-cluster SETUP token can reach
   `/api/oauth/usage` at all. Community reports conflict. Run a `curl` against
   the real token in-cluster (throwaway pod) before building the poller. The
   poller supports both auth modes via config so this confirms which, not
   whether to build.
2. Endpoint instability: undocumented, may change or lock down without notice.
   Mitigated by config gate, UA + beta pin, schema canary, auto-fallback.
3. Token refresh: the shared OAuth access token may expire (~8h). Determine
   whether the setup-token is long-lived for this endpoint or the poller must
   handle refresh. If refresh is needed, the poller owns it (single place).
4. Poll self-consumption: confirm the usage endpoint is metered separately from
   inference so a 180s poll does not itself nibble the shared window. If it is
   metered together, 180s is negligible but must be acknowledged.
5. Leader election: exactly one poller instance across operator replicas
   (replicaCount 3) to avoid 3x the poll rate hitting the 429 bucket.

## Out of scope (follow-ups, not this spec)

- Cross-repo cache reclaim, delegation, handoff-replaces-S3 (token-conservation
  P1/P2).
- The broader review gap list (`docs/2026-07-04-token-conservation-p0-review-gaps.md`);
  this spec closes G1 (real cost producer via OTel) and G3 (staged re-enable)
  and partially G6 (window-based, not just output-token, gating), but the rest
  remain separate brainstorm inputs.
