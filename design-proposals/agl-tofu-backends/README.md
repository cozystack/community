# Terraform/OpenTofu backend for Cozystack AGL

- **Title:** Terraform/OpenTofu backend for Cozystack Application Generation Layer
- **Author(s):** @kitsunoff
- **Date:** 2026-05-20
- **Status:** Draft

## Overview

Cozystack's Application Generation Layer (AGL) today maps a user-facing Kubernetes kind (e.g. `Postgres`, `Kafka`, `Bucket`) to a Flux `HelmRelease` under the hood. Helm is the only supported backend.

This proposal adds a second backend: `Terraform` CRs of [flux-iac/tofu-controller](https://github.com/flux-iac/tofu-controller), so platform engineers can describe cloud primitives (VPCs, DNS zones, managed services, IAM bindings, external buckets) under the same AGL abstraction.

Two architectural alternatives are documented here for review:

- **[Draft 1 — Parallel Tofu Stack](./draft-1-parallel-tofu-stack.md)** — a new `TofuApplicationDefinition` CRD with its own apiserver and reconciler, mirroring the existing Helm AGL one-to-one. Minimal risk to existing packages, but ~80% code duplication.
- **[Draft 2 — Pluggable Backend](./draft-2-pluggable-backend.md)** — refactor AGL so Helm and Terraform are two implementations of a single `Backend` interface, behind one `ApplicationDefinition` CRD. Cleaner long-term, but a larger refactor of the hot path.

A short slide deck summarising both drafts side-by-side is included as [`presentation.md`](./presentation.md) (Marp-flavoured).

## Scope and related proposals

This proposal targets [cozystack/cozystack](https://github.com/cozystack/cozystack). No related proposals at this time.

The two drafts in this directory are **alternatives**, not sibling proposals. The intent of the PR is to choose one of them (possibly via the hybrid path in the recommendation below) before implementation starts.

## Context

The AGL today consists of:

- A cluster-scoped CRD `ApplicationDefinition` (`apps.cozystack.io/v1alpha1`) describing the mapping from a user-facing kind to a `HelmRelease`.
- A custom aggregation API server (`cozystack-api`) that reads `ApplicationDefinition`s and registers dynamic kinds.
- A REST layer that translates `Application.Spec` (opaque `RawExtension`) into `HelmRelease.Spec.Values` on create/update, and projects `HelmRelease.Status` back into `Application.Status`.
- A reconciler (`ApplicationDefinitionHelmReconciler`) that keeps existing `HelmRelease`s aligned with the definition when the definition changes, and a config-hash mechanism that restarts the aggregation apiserver when the set of registered kinds changes.

Relevant files in `cozystack/cozystack`:

- `api/v1alpha1/applicationdefinitions_types.go`
- `pkg/registry/apps/application/rest.go`
- `pkg/apiserver/apiserver.go`
- `internal/controller/applicationdefinition_helmreconciler.go`
- `packages/system/cozystack-api/`

### The problem

Some platform primitives don't fit Helm naturally:

- Cloud VPCs, subnets, route tables, IAM bindings.
- Managed DNS zones.
- Managed databases on hyperscalers.
- External object storage buckets, queues, secrets in cloud KMS.

These are naturally Terraform/OpenTofu territory. Today, a tenant who wants a `VPC` resource alongside their `Postgres` resource has no way to express it through AGL — Cozystack would need a separate, non-AGL surface for cloud-side primitives, which defeats the whole point of having a generation layer.

`flux-iac/tofu-controller` already provides a Flux-native `Terraform` CRD with the same lifecycle model as `HelmRelease` (source refs, drift detection, reconcile interval, status conditions). The shape of the integration is therefore well-defined: AGL should be able to emit `Terraform` CRs the same way it emits `HelmRelease`s.

## Goals

- Allow package authors to declare a user-facing kind (e.g. `VPC`, `S3Bucket`, `DNSZone`) that AGL translates into a `Terraform` CR.
- Reuse the existing dashboard/category/openAPISchema/secret-include machinery without modification.
- Stay backwards-compatible: existing Helm-backed packages must keep working with no manifest changes.

## Non-goals

- Mixing Helm and Terraform under a single application definition (one backend per definition; composite resources are out of scope).
- Building a "Terraform module marketplace" — package authors still ship their own modules.
- Replacing Flux. Both backends still reconcile through Flux primitives (helm-controller, tofu-controller).
- A UI for the Terraform plan/apply approval flow — tofu-controller already exposes it.

## Design

Two alternative designs are proposed. Detailed designs (CRD shapes, Go types, API server changes, REST translation, status projection, reconciler, packaging, implementation plan, risks, open questions) live in the per-draft documents:

- **[Draft 1 — Parallel Tofu Stack](./draft-1-parallel-tofu-stack.md)**
- **[Draft 2 — Pluggable Backend](./draft-2-pluggable-backend.md)**

### Comparison

| Dimension                  | Draft 1 (parallel)     | Draft 2 (decoupled)     |
| -------------------------- | ---------------------- | ----------------------- |
| Time to first PoC          | days                   | weeks                   |
| Regression risk            | minimal                | medium                  |
| Code duplication           | high                   | none                    |
| Cost of 3rd backend        | another full copy      | one interface impl      |
| User-facing UX             | two AppDef kinds       | one AppDef, switch type |
| Review burden              | low                    | high, needs slicing     |
| Long-term maintenance cost | high                   | low                     |

### Recommendation

A **hybrid path**:

1. **First:** ship Draft 1 as a feature-branch PoC. Prove the tofu-controller mapping. Surface real requirements for vars marshalling, cloud-creds runner pods, output secret handling, plan-approval UX.
2. **Then:** with two working backends in hand, do the Draft 2 refactor. The `Backend` interface is then designed against concrete code, not speculation.

This trades a small amount of throwaway code for much lower risk of a bad abstraction.

## User-facing changes

- A new backend type for `ApplicationDefinition` (shape depends on chosen draft): package authors can declare Terraform-backed kinds.
- Tenants can `kubectl apply` a `VPC`/`DNSZone`/`S3Bucket` (or any other Terraform-backed kind a package defines) the same way they apply `Postgres` today.
- Dashboard renders Terraform-backed kinds via the existing category/icon/openAPISchema machinery.
- Outputs of a Terraform run are surfaced through the existing `spec.secrets.include` mechanism.

No change to existing Helm-backed packages or their CRs.

## Upgrade and rollback compatibility

- **Draft 1:** purely additive. Existing `ApplicationDefinition` and `HelmRelease` objects are untouched. Rollback = remove the new chart and CRD.
- **Draft 2:** the `release` field stays in `v1alpha1` as a deprecated alias for `backend.helm`. An apiserver-side normalization pass projects `spec.release` onto `spec.backend.helm` in memory; persisted objects are not mutated. Rollback of the apiserver is safe as long as the CRD still accepts both fields. The `v1alpha2` removal of `release` is deferred to a later release with a conversion webhook.

Both drafts include a feature flag for the rollout phase.

## Security

- **New trust boundary:** runner pods executing `terraform plan`/`apply` need cloud credentials. Definitions can pin a `runnerPodTemplate` with a `ServiceAccount` (e.g. IRSA/Workload Identity).
- **New tenant-supplied inputs:** `Application.Spec` fields become Terraform input variables. Inputs are validated against `application.openAPISchema` before any backend call, and additionally must match HCL identifier regex `^[a-z_][a-z0-9_]*$` for variable names.
- **New secrets stored or transmitted:** `writeOutputsToSecret` can contain provider-returned values (e.g. access keys). Mitigated by an admission policy that requires explicit output allow-listing, plus the existing `spec.secrets.include` allow-list.
- **New RBAC surface:** the aggregation apiserver gains read/write on `Terraform` CRs in tenant namespaces. Scope follows the existing pattern for `HelmRelease`.

## Failure and edge cases

- Invalid input rejected by `openAPISchema` → apiserver returns 422 before any Flux object is created (existing behaviour).
- Tofu-controller not installed → Draft 1: apiserver fails fast on startup; Draft 2: registry skips registering the Terraform backend and logs a clear line. Packages that declare a Terraform backend get a "backend not available" status condition.
- Manual approval pending (`approvePlan: ""`) → surfaced as `Application.Status.PendingApproval=true`. Dashboard renders the plan diff.
- `destroyResourcesOnDeletion: true` on a Terraform CR with broken provider creds → destroy fails, `Terraform` CR remains, `Application` deletion is blocked by finalizer. User intervention required.
- Tenant deletes a Terraform-backed `Application` mid-apply → tofu-controller handles cancellation; finalizer waits for terminal state.

## Testing

- Unit tests for the REST translation (Draft 1 and Draft 2): `Application.Spec` ↔ `Terraform.Spec.Vars` mapping, status projection round-trips.
- Unit tests for the backend interface (Draft 2): each backend tested in isolation with a fake client.
- Integration tests with a real tofu-controller against a local provider (e.g. `null_resource`, `random_id`) — no cloud creds needed.
- e2e: one Terraform-backed example package (`VPC` against localstack, or `DNSZone` against a mock) end-to-end through the aggregation apiserver.
- Backwards-compat (Draft 2): the existing Helm-backed e2e suite must pass unchanged after the refactor.

## Rollout

- **Release N:** ships behind feature flag (`--enable-tofu-backend` for Draft 1, `--enable-pluggable-backends` for Draft 2). Off by default in stable channel.
- **Release N+1:** flag on by default. First Terraform-backed example package shipped.
- **Release N+2:** deprecation of `spec.release` announced (Draft 2 only).
- **Release N+3:** `v1alpha2` of `ApplicationDefinition` removes `spec.release` (Draft 2 only), conversion webhook fills it for v1alpha1 clients.

## Open questions

- Which alternative should land? Draft 1, Draft 2, or the hybrid path (Draft 1 first, then refactor to Draft 2)?
- Per-instance backend override (e.g. `Application.Spec.runnerPodOverride`) for multi-tenant cloud identity isolation — needed in v1 or deferred?
- Should the runner pod template be defined per-AppDef, per-instance, or both?
- How should plan diffs be exposed when `approvePlan` is manual — status field + dashboard rendering, or a separate `approve` subresource on `Application`?
- Is `apps.cozystack.io` the right group for Terraform-backed kinds (Draft 1), or should they live under a distinct `tofu.apps.cozystack.io` group?

## Alternatives considered

The two drafts in this directory are themselves the alternatives evaluated. Within each draft, alternatives for narrower decisions (group naming, status schema shape, conversion strategy) are discussed in the draft's own "Risks" and "Open questions" sections.

Out-of-this-proposal alternatives that were dismissed:

- **Treat Terraform as out of scope for AGL and build a parallel "infra" CRD set.** Rejected: defeats the purpose of having a generation layer, fragments the dashboard and tenant experience.
- **Use ArgoCD `Application` with a Terraform plugin.** Rejected: introduces a second reconciliation engine alongside Flux, doesn't reuse the existing AGL machinery.
- **Generate raw `Job`s that shell out to `tofu`.** Rejected: re-implements drift detection, state management, and lifecycle that tofu-controller already provides.

---

## Reading these documents

Rendered, browsable version (with navigation and the Marp slide deck as HTML): <https://kitsunoff.github.io/cozystack-agl-backends/>.

Source repository: <https://github.com/kitsunoff/cozystack-agl-backends>.
