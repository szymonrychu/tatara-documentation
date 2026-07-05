# tatara-operator shared contracts (pin set)

Authoritative cross-milestone agreements for all `tatara-operator` plans.
The spec (`docs/superpowers/specs/2026-06-06-tatara-operator-design.md`) is
the source of intent; this file pins the exact names/paths/signatures every
milestone plan MUST use so the milestones compose. Do not deviate.

## Repo basics

- Module: `github.com/szymonrychu/tatara-operator`. Go `1.25.x` (match siblings).
- Framework: kubebuilder / controller-runtime.
- API group `tatara.dev`, version `v1alpha1`. Kinds: `Project`, `Repository`,
  `Task`, `Subtask`. All namespaced (live in ns `tatara`).
- CRD spec/status fields: exactly as in the spec "CRD model" section.

## Canonical file layout (every milestone uses these exact paths)

```
cmd/manager/main.go
api/v1alpha1/{groupversion_info,project_types,repository_types,
              task_types,subtask_types,doc,zz_generated.deepcopy}.go
internal/controller/{project_controller,repository_controller,task_controller}.go
internal/ingest/job.go            # builds *batchv1.Job for the ingester
internal/agent/pod.go             # builds wrapper Pod + Service
internal/agent/session.go         # wrapper HTTP client (Session interface)
internal/webhook/{server,github,gitlab}.go
internal/restapi/{server,handlers}.go
internal/scm/{scm,github,gitlab}.go
internal/auth/{verifier,tokensource}.go
internal/obs/{logger,metrics}.go
internal/config/config.go
charts/tatara-operator/...
Dockerfile  Makefile  go.mod  CLAUDE.md  MEMORY.md  ROADMAP.md  README.md
```

## Config (`internal/config`, env scalars via ConfigMap/Secret envFrom, rule 6)

`HTTP_ADDR` (REST + webhook listener), `METRICS_ADDR`, `INTERNAL_ADDR`
(turn-complete callback listener), `OIDC_ISSUER`, `OIDC_AUDIENCE`
(=`tatara-operator`), `MEMORY_BASE_URL`, `INGESTER_IMAGE`,
`EXTERNAL_WEBHOOK_BASE` (used to render `Project.status.webhookURL`),
`OPERATOR_OIDC_CLIENT_ID`, `OPERATOR_OIDC_CLIENT_SECRET` (client-credentials
mint), `ANTHROPIC_SECRET_NAME`, `CLI_OIDC_SECRET_NAME`, `LOG_LEVEL`.

## obs (`internal/obs`, mirror tatara-chat)

JSON `slog` handler; a prometheus registry. Metrics:
`operator_reconcile_total{kind,result}`,
`operator_ingest_job_duration_seconds`,
`operator_turn_duration_seconds`,
`operator_webhook_events_total{provider,kind,result}`,
`operator_tasks_inflight` (gauge).

## auth (`internal/auth`)

- `Verifier`: OIDC JWKS discovery + verify (`iss`, `exp`, `aud` contains
  `OIDC_AUDIENCE`). Mirror the sibling `internal/auth` in tatara-chat/tatara-memory.
- `TokenSource`: client-credentials grant against the issuer token endpoint
  using `OPERATOR_OIDC_CLIENT_ID`/`OPERATOR_OIDC_CLIENT_SECRET`; mints bearer
  tokens used to call tatara-memory and the wrapper. Caches until near expiry.

## Re-ingest trigger contract (M1 defines, M2 uses)

`RepositoryReconciler` spawns an ingest Job when:
- `status.lastIngestedCommit == ""` -> full ingest (no `--since`), OR
- annotation `tatara.dev/reingest-requested` (an RFC3339 timestamp) is newer
  than `status.lastIngestTime` -> incremental ingest (`--since
  <status.lastIngestedCommit>`).

`status.jobName` guards against launching a second Job while one is active.
The Job is owner-referenced to the Repository. On Job success the reconciler
sets `status.lastIngestedCommit` (cloned HEAD SHA), `status.lastIngestTime`,
`status.phase=Ingested`. The M2 webhook sets the annotation on a push.

## Ingest Job (`internal/ingest/job.go`)

`*batchv1.Job`, owner-ref Repository. One container from `INGESTER_IMAGE`.
The container (a) clones `Repository.spec.url` at `spec.defaultBranch` using
the Project SCM token into an `emptyDir`, then (b) runs
`tatara-ingest --repo-root <dir> --repo-name <Repository.name>
--base-url $MEMORY_BASE_URL [--since <sha>]` with an OIDC bearer (operator
client-credentials token, injected via env). RestartPolicy Never,
backoffLimit small.

## Wrapper agent Pod (M4, `internal/agent/pod.go`)

Bare `*corev1.Pod` + `*corev1.Service`, both owner-ref Task. Pod runs the
wrapper image from `Project.spec.agent.image`. Env: `REPO_URL`,
`REPO_BRANCH` (from Repository), `MODEL`, `PERMISSION_MODE`,
`TURN_TIMEOUT_SECONDS` (from `Project.spec.agent`), `DEFAULT_CALLBACK_URL`
(= operator `INTERNAL_ADDR` `/internal/turn-complete`). Secrets:
`ANTHROPIC_API_KEY` (from `ANTHROPIC_SECRET_NAME`), Project SCM token (git
creds for clone + push), tatara-cli OIDC client creds (from
`CLI_OIDC_SECRET_NAME`). Service name = pod name; operator reaches the
wrapper at `http://<svc>.<ns>.svc:8080`.

## Wrapper client (M4, `internal/agent/session.go`)

```go
type TurnResult struct {
    State, FinalText, StopReason, Err string
}
type Session interface {
    SubmitTurn(ctx context.Context, baseURL, text, callbackURL string) (turnID string, err error)
    GetTurn(ctx context.Context, baseURL, turnID string) (TurnResult, error)
    DeleteSession(ctx context.Context, baseURL string) error
}
```

Wrapper REST (audience `tatara-claude-code-wrapper`, bearer minted by
TokenSource): `POST /v1/messages {text,callbackUrl} -> 202 {turnId}`;
`GET /v1/messages/{turnId}`; `DELETE /v1/session`.

## Operator internal callback (M4)

`POST /internal/turn-complete` on `INTERNAL_ADDR` (in-cluster only, no OIDC):
receives the wrapper turn result, correlates `turnId` -> Task/Subtask,
records the Subtask result, requeues the Task. A periodic Task requeue
backstops missed callbacks (poll `GET /v1/messages/{turnId}`).

## REST API (M3, `internal/restapi`, OIDC audience `tatara-operator`)

```
GET   /projects
GET   /projects/{p}
GET   /projects/{p}/repositories
GET   /projects/{p}/tasks
GET   /tasks/{t}
PATCH /tasks/{t}                 # status notes from the agent
GET   /tasks/{t}/subtasks
POST  /tasks/{t}/subtasks        # agent self-plans subtasks
PATCH /subtasks/{s}              # mark Done / add result
```

Backed by the controller-runtime client. Shares the `HTTP_ADDR` listener
with the webhook server (different path prefixes: `/operator/webhooks/...`
vs the REST paths above).

## tatara-cli MCP tools (M3, repo `tatara-cli`, `internal/mcp/tools.go`)

Add a tool group mapping 1:1 to the REST endpoints, mirroring the existing
tool-registration style: `project_list`, `project_get`, `repo_list`,
`task_list`, `task_get`, `task_update`, `subtask_list`, `subtask_create`,
`subtask_update`. New base-URL + audience config for the operator service.

## scm interface (`internal/scm/scm.go`; M2 builds webhook half, M5 builds write half)

```go
type WebhookEvent struct {
    Kind     string // "push" | "issue" | "mr" | "other"
    Repo     string // remote URL
    Branch   string // for push
    Labels   []string
    Title    string
    Body     string
    IssueRef string // owner/repo#123 (github) or group/proj!iid (gitlab)
    URL      string
}
type Client interface {
    Provider() string // "github" | "gitlab"
    DetectAndVerify(h http.Header, payload []byte, secret string) (WebhookEvent, error)
    OpenChange(ctx context.Context, repoURL, token, sourceBranch, targetBranch, title, body string) (url string, err error)
    Comment(ctx context.Context, token, issueRef, body string) error
}
```

- Provider detection: header `X-GitHub-Event` -> github; `X-Gitlab-Event`
  -> gitlab.
- HMAC: github `X-Hub-Signature-256` (`sha256=` hex HMAC of body with
  secret, constant-time compare); gitlab `X-Gitlab-Token` (constant-time
  equal to secret).
- M2 implements `DetectAndVerify` for both providers; `OpenChange`/`Comment`
  are added/implemented in M5.

## Webhook server (M2, `internal/webhook`)

Path `/operator/webhooks/{project}` on `HTTP_ADDR`. Look up the Project,
read `webhookSecret` from the `scmSecretRef` Secret, pick the scm.Client by
header, `DetectAndVerify`. Then:
- `push` with `Branch == Repository.defaultBranch` (matched by remote URL)
  -> set `tatara.dev/reingest-requested` annotation on that Repository.
- `issue`/`mr` whose `Labels` contain `Project.spec.triggerLabel` -> create a
  Task (`goal` = `Body`, `source` from event). (Task creation wiring lands in
  M5 alongside the work-item path; M2 may stub Task creation behind the same
  handler and M5 completes it - state which.)

## SCM secret shape

`Project.spec.scmSecretRef` -> Secret with keys `token` (provider PAT for
clone + PR/MR + comment) and `webhookSecret` (HMAC verification).

## Deploy (M6)

Chart via `helm create` then edited (rule 5), cluster-agnostic (rule 14):
no baked regcred/affinity/ingress host/storage class. CRDs installed by the
chart. values.yaml only camelCase scalars -> ConfigMap/Secret -> `envFrom`
(rule 6). NetworkPolicy egress allowlist on agent + ingest pods (memory,
operator svc, tatara-chat, SCM hosts, DNS). New confidential Keycloak client
`tatara-operator` (service accounts) + `tatara-operator` audience, in
`infra/terraform/keycloak/tatara_clients.tf`. Release added to the infra
helmfile `tatara` bucket.
