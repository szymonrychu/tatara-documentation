# Per-Project External Exposure (memory + chat) - Design

Date: 2026-06-12
Status: design, pending user review

## Goal

When a `Project` CR is reconciled, the tatara-operator provisions and exposes that
project's per-project **memory** (already provisioned) and **chat** (newly
provisioned) via a per-project Ingress on the shared host, behind the apps' own
OIDC auth, so the tatara MCP tools (which hit the memory's `/code/*`, `/memories`,
`/queries` routes) are reachable from outside the cluster, project-scoped by path.

Today `mem-<project>` is a ClusterIP-only Service; nothing externally exposes it,
so `tatara mcp` on a developer machine cannot reach the code graph. This closes
that gap and makes per-project chat a first-class provisioned component.

## Decisions locked (from brainstorming)

- **Topology:** per-project PATH on the shared host (not per-project host) -
  `tatara.szymonrichert.pl/api/v1/memory/<project>` and `.../api/v1/chat/<project>`.
  Reuses the one host TLS cert; matches the existing operator(`/`) + chat
  (`/api/v1/chat`) path pattern with `rewrite-target: /$2`.
- **Chat:** the operator PROVISIONS a per-project `chat-<project>` (Deployment +
  Service + ConfigMap, stateless, pointed at `mem-<project>` + the operator),
  mirroring the memory stack. The single standalone `tatara-chat` release is
  retired once per-project chat is live.
- **Trigger:** automatic - every project gets the ingress (+ chat) when the
  operator has `ingressHost` configured. No `Project` spec field.
- **MCP wiring:** tatara-cli gains a `--project`/`-p` flag + `TATARA_PROJECT` env
  (aws-cli `--profile` style); the cli composes the per-project memory URL.
- **Build:** one spec, two phases. Phase 1 = memory ingress + cli `--project`
  (immediate MCP value). Phase 2 = per-project chat provisioning + its ingress
  path.

## Architecture

### Operator config (new, cluster-supplied per rule 14)

`internal/config/config.go` `Config` gains:

| Field | env | infra value | purpose |
|-------|-----|-------------|---------|
| `IngressHost` | `INGRESS_HOST` | `tatara.szymonrichert.pl` | shared host for per-project paths |
| `IngressClassName` | `INGRESS_CLASS_NAME` | `nginx` | ingressClassName |
| `MemoryPathPrefix` | `MEMORY_PATH_PREFIX` | `/api/v1/memory` | base path; project appended |
| `ChatPathPrefix` | `CHAT_PATH_PREFIX` | `/api/v1/chat` | base path; project appended |
| `ChatImage` | `CHAT_IMAGE` | `harbor.szymonrichert.pl/containers/tatara-chat:<tag>` | per-project chat image |

The operator chart (`charts/tatara-operator`) exposes these as camelCase values ->
ConfigMap keys (rule 6); the infra helmfile
(`helmfiles/tatara/values/tatara-operator/default.yaml`) supplies them. Per-project
ingress provisioning is gated on `IngressHost` being non-empty (so a cluster that
doesn't want external exposure leaves it blank and gets no ingress).

### Per-project Ingress (new)

The Project reconciler builds ONE `networking.k8s.io/v1` Ingress per project,
owner-ref'd to the Project (cascade delete), named `<project>` in the operator
namespace:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: <project>
  namespace: <operator-ns>
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /$2
  ownerReferences: [<Project>]
spec:
  ingressClassName: <IngressClassName>
  rules:
    - host: <IngressHost>
      http:
        paths:
          - path: <MemoryPathPrefix>/<project>(/|$)(.*)
            pathType: ImplementationSpecific
            backend: { service: { name: mem-<project>, port: { number: 8080 } } }
          - path: <ChatPathPrefix>/<project>(/|$)(.*)          # phase 2
            pathType: ImplementationSpecific
            backend: { service: { name: chat-<project>, port: { number: <chatPort> } } }
```

**No TLS block + no cert-manager annotation on this Ingress.** The host's cert is
provided by the existing operator Ingress (which owns the host's TLS); nginx merges
all ingresses for the host and serves every path under that one cert. This avoids
per-project certs and cert-manager contention. **No nginx auth annotations** -
auth is app-level (both memory and chat run OIDC middleware that validates the
Bearer token; the cli/`tatara login` token carries the `tatara-memory` audience).

Phase 1 ships the Ingress with only the memory path; phase 2 adds the chat path.

### Per-project chat provisioning (new, phase 2)

Mirrors the memory provisioner but stateless. New builders (e.g.
`internal/chat/chat_builders.go`): `ChatConfigMap`, `ChatDeployment`,
`ChatService` for `chat-<project>`, using `ChatImage`, owner-ref'd, with config
pointing at `mem-<project>` (memory endpoint) and the operator. Applied in the
Project reconcile alongside the memory stack; health-gated into a new
`status.chat.phase`. The chat container port (default `8080`) is read from the
chat image's known port; exposed by `ChatService` and referenced by the ingress.

### Project CRD / Status

No new Spec field (automatic). `ProjectStatus` gains:
- `memory.externalEndpoint` - e.g. `https://tatara.szymonrichert.pl/api/v1/memory/<project>`.
- `chat` (`*ChatStatus{ Phase, Endpoint, ExternalEndpoint }`) - in-cluster +
  external chat URLs (phase 2).

These let a user read the exact MCP URL from `kubectl get project <p> -o yaml`.

### RBAC + manager wiring

- Operator Role gains `networking.k8s.io/ingresses` (get/list/watch/create/update/
  patch/delete). (apps/deployments + core/services already granted for memory; chat
  reuses them.)
- `SetupWithManager` adds `Owns(&networkingv1.Ingress{})` so the controller watches
  + reconciles owned ingresses.

### tatara-cli `--project` / `-p`

Add a persistent flag `--project`/`-p` + `TATARA_PROJECT` env (precedence:
flag > env > none). When set, the cli composes the per-project memory base URL:
`<memory-base>/<project>` (e.g. baked base `https://tatara.szymonrichert.pl/api/v1/memory`
+ `tatara` -> `.../api/v1/memory/tatara`). The operator base URL is unchanged
(single, not per-project). `.mcp.json` then needs only `-p tatara` (or the env):

```json
"tatara": { "command": ".../tatara", "args": ["-p", "tatara", "mcp"] }
```

The existing `code_*` MCP tools already take a `project` arg / `TATARA_PROJECT`;
this flag wires the same value into the URL composition so the request actually
lands on the right per-project backend.

### Infra

`helmfiles/tatara/values/tatara-operator/default.yaml` adds `ingressHost`,
`ingressClassName`, `memoryPathPrefix`, `chatPathPrefix`, `chatImage`. The
standalone `tatara-chat` release is removed from `helmfiles/tatara/helmfile.yaml.gotmpl`
in phase 2 (retired in favor of per-project chat). The `tatara-chat` image is
built by its new CI pipeline (`:<git-describe>`); pin `chatImage` to that tag.

## Phasing

- **Phase 1 (memory ingress + cli):** operator Config fields + the Ingress builder
  (memory path only) + RBAC + `Owns` + `status.memory.externalEndpoint`; operator
  chart values + RBAC; infra values; tatara-cli `--project`/`-p`. Deliverable: the
  `tatara mcp` tools reach the code graph externally. Ship operator + cli images
  via their CI (no local buildx), bump infra pins, deploy.
- **Phase 2 (chat provisioning):** chat builders + provisioning in the reconcile +
  `status.chat` + the chat ingress path; retire the standalone `tatara-chat`
  release; pin `chatImage`.

## Error handling

- `IngressHost` blank -> skip ingress provisioning entirely (no-op; in-cluster
  only). Logged once.
- Ingress apply uses server-side apply / create-or-update like the memory stack,
  owner-ref'd; a failed apply requeues (same pattern as `applyMemoryStack`).
- Chat health failure -> `status.chat.phase=Provisioning`, requeue; does not block
  memory readiness.

## Testing

- Operator: envtest unit tests for the Ingress builder (paths, rewrite, backends,
  owner-ref, gated on IngressHost) and the chat builders; reconcile test asserting
  the Ingress + chat objects are created and status endpoints populated.
- tatara-cli: unit test for URL composition (`-p`/env -> `/api/v1/memory/<project>`),
  precedence, and the no-project case.
- End-to-end (real cluster, after deploy): `kubectl get ingress <project>` shows
  both paths; `tatara -p tatara raw GET /code/entities?repo=tatara-operator&q=Reconcile`
  returns graph data through `tatara.szymonrichert.pl`; the `tatara` MCP server in
  `.mcp.json` exposes working `code_*` tools.

## Out of scope

- Per-project DNS/hosts (path-based chosen).
- nginx-level auth (app-level OIDC is the auth).
- tatara-tasks / tatara-gitlab-bridge (not deployed).
- Multi-cluster / multiple ingress controllers.
