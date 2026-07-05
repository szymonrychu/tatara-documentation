# tatara-observability: agent-adjustable alerts-as-code - Design

Date: 2026-06-25
Repos: tatara-observability (new, primary), infra/terraform/grafana (remove tatara rules),
tatara-helmfile (enroll). Status: approved design.

## Problem

The tatara Grafana alert rules shipped into `infra/terraform/grafana` (gitlab.com), a repo the
tatara agents do NOT enroll on. So the platform cannot tune or add its own alerts - it defeats the
self-improving-observability goal. Migrate the alert RULES into an agent-reachable tatara repo;
keep the global routing (contact point + notification policy) in terraform.

## Approved decisions

- New private repo `github.com/szymonrychu/tatara-observability`, enrolled on the tatara Project so
  agents work on it via the normal brainstorm/issue-lifecycle loop.
- Agents edit only `alerts/tatara-*.yaml` (the existing simple `name`/`queries[].expression`/
  `math_operator`/`threshold`/`for`/`decimal_points`/`annotations`/`labels` schema). Terraform is
  the applier (reuses the proven `grafana_alert` module); agents never write terraform/HCL.
- Delivery: GitHub Actions runs terraform - `plan` as a sticky comment on PR, `apply` on merge to
  main. Grafana **Editor** SA token (least-priv: write alert rules, not admin).
- Ownership split: tatara-observability owns its own Grafana folder `Tatara` + the `tatara-*` rule
  groups. The contact point + notification policy STAY in `infra/terraform/grafana` (global homelab
  routing; label-based, so it routes `system=tatara` regardless of which folder/owner the rules are
  in - unchanged).
- State: S3 backend (reuse `szymonrychu-terraform-state`, new key `tatara-observability`).
- Out of scope (YAGNI): dashboards-as-code (the repo can grow into it later).

## Architecture

```
tatara-observability/  (github, private, enrolled on tatara Project)
  alerts/tatara-{operator,memory,chat,wrapper,ingester,logs}.yaml   <- agents PR these
  modules/grafana_alert/   (vendored from infra/terraform/grafana, includes loki query_type)
  grafana.tf providers.tf variables.tf backend.tf
  .github/workflows/apply.yml
  CLAUDE.md MEMORY.md ROADMAP.md README.md .mise.toml .gitignore

  agent edits alerts/*.yaml -> PR -> GH Actions `terraform plan` (sticky comment)
      -> merge to main -> GH Actions `terraform apply`
      -> Grafana folder `Tatara`, rule groups tatara-* (Editor token)
      -> rules carry homelab+system=tatara+component+severity
      -> [unchanged] infra/terraform notification policy routes system=tatara -> Tatara contact
         point -> /operator/webhooks/tatara/grafana -> incident -> brainstorming issue
```

### Units

- `modules/grafana_alert` (vendored): unchanged from infra/terraform, including the `loki`
  query_type support (loki model + expr) added for the Loki rules. One file per `alerts/*.yaml` =
  one `grafana_rule_group`.
- `grafana.tf`: a `grafana_folder "tatara"` (title `Tatara`) + the `module "alerts"` call over
  `fileset(path.module, "alerts/*.yaml")`, folder_uid = the Tatara folder, default_labels =
  `{homelab="true"}` (per-rule labels still replace; tatara rules set system/component/severity).
- `providers.tf`/`variables.tf`: grafana provider, `grafana_url` + `grafana_api_key` (sensitive,
  Editor token) vars.
- `backend.tf`: S3 `szymonrychu-terraform-state` key `tatara-observability`.
- `.github/workflows/apply.yml`: terraform fmt/validate/plan on PR (sticky comment); apply on push
  to main. Secrets: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (state), `TF_VAR_grafana_api_key`
  (Editor), `TF_VAR_grafana_url`.

## Migration sequence (no gap, no double-fire)

1. Bootstrap tatara-observability (repo content + CI). Set the GitHub secrets (user gate). Apply ->
   creates `tatara-*` rule groups in folder `Tatara`.
2. Verify the new rules load (`health: ok`) + routing still fires (label-based, unaffected).
3. Remove `alerts/tatara-*.yaml` from `infra/terraform/grafana` + revert the `grafana_alert` module
   loki change there (homelab alerts do not use loki); apply -> removes the old copies from folder
   `Default`. Net: rules move folder+owner; no duplicate (distinct group names so even transient
   overlap would not double-route - the operator dedups on groupKey).
4. Enroll `tatara-observability` on the tatara Project: add to `tatara-helmfile`
   `values/project-tatara/common.yaml` repositories (semanticIngest, reingest cron) + apply.

## Secrets / deploy gate (user)

GitHub Actions secrets on tatara-observability: `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`
(same as the infra terraform S3 creds), `TF_VAR_grafana_api_key` (a Grafana Editor SA token),
`TF_VAR_grafana_url` (https://grafana.szymonrichert.pl/). These I cannot read/mint (masked infra
CI vars / Grafana admin), so the user provides them; everything else is automated.

## Testing

- `terraform validate` + a golden-render assert (a sample rule emits homelab+system=tatara labels;
  a loki rule emits datasource type loki + expr). CI `plan` on PR.
- Post-apply: `tatara-*` groups present in folder `Tatara` (`health: ok`); a tripped threshold still
  reaches a `tatara` incident Task. Old `Default`-folder copies gone after step 3.

## Risks

- Two terraform states write to one Grafana; disjoint folders + group names avoid collision. The
  `Tatara` folder is owned by tatara-observability; infra keeps `Default`.
- The Editor token is global-write within Grafana alerting (not folder-scoped - Grafana SA tokens
  are role-scoped, not folder-scoped); least-priv is Editor, documented.
- Downstream: the `incident` writeback regression (separate handoff
  `/tmp/tatara-grafana-incident-no-issue-handoff.md`) is unaffected by this migration.
