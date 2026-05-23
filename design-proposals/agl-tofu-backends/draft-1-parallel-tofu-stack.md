# Draft 1: Parallel Tofu Stack for Cozystack AGL

- **Status:** Draft
- **Author:** @kitsunoff
- **Date:** 2026-05-20
- **Target project:** [cozystack/cozystack](https://github.com/cozystack/cozystack)

> Companion document to [`README.md`](./README.md) (overview and comparison) and [`draft-2-pluggable-backend.md`](./draft-2-pluggable-backend.md) (alternative design).

## Summary

Add a second, parallel application generation stack to Cozystack that maps user-facing Kubernetes resources to `Terraform` CRs of [flux-iac/tofu-controller](https://github.com/flux-iac/tofu-controller), mirroring the existing Helm-based AGL one-to-one.

No changes to the existing Helm path: the new stack lives next to it as an isolated set of types, API server bindings and reconcilers.

## Motivation

Cozystack's AGL is the right abstraction for "user creates a high-level resource, platform creates the actual workload underneath", but today the only supported backend is `HelmRelease`. Cloud-side primitives (VPCs, managed databases, DNS zones, IAM bindings) do not fit Helm naturally and are typically managed with Terraform/OpenTofu.

`tofu-controller` already provides a Flux-native `Terraform` CRD with the same lifecycle model as `HelmRelease` (source refs, drift detection, reconcile interval, status). The smallest viable change is to repeat the AGL pattern for it.

## Goals

- Allow package authors to declare a user-facing kind (e.g. `VPC`, `S3Bucket`, `DNSZone`) that the API server transparently translates into a `Terraform` CR.
- Reuse the existing dashboard/category/openAPISchema machinery without modification.
- Ship as an additive feature: zero risk of regression for existing Helm-backed packages.

## Non-goals

- Refactoring the existing Helm AGL or introducing a backend abstraction (see Draft 2).
- Supporting a third backend (Argo, Kustomization, plain manifests).
- Mixing Helm and Terraform under a single application definition.
- Building a UI for the Terraform plan/apply approval flow (tofu-controller already exposes it).

## Design

### New CRD: `TofuApplicationDefinition`

Cluster-scoped, lives in `apps.cozystack.io/v1alpha1` alongside `ApplicationDefinition`.

```yaml
apiVersion: apps.cozystack.io/v1alpha1
kind: TofuApplicationDefinition
metadata:
  name: vpc
spec:
  application:
    kind: VPC
    singular: vpc
    plural: vpcs
    openAPISchema: |
      {
        "type": "object",
        "properties": {
          "cidr":   { "type": "string" },
          "region": { "type": "string" }
        }
      }
  terraform:
    sourceRef:
      kind: OCIRepository          # GitRepository | OCIRepository | Bucket
      name: cozystack-vpc-module
      namespace: cozy-system
    path: ./
    prefix: vpc-
    labels:
      sharding.fluxcd.io/key: tenants
    interval: 5m
    approvePlan: auto              # "auto" | "" (manual)
    destroyResourcesOnDeletion: true
    writeOutputsToSecret:
      name: "{{ .name }}-outputs"
    runnerPodTemplate:             # cloud creds, IRSA, custom image
      spec:
        serviceAccountName: tofu-runner
  secrets:
    include:
      - resourceNames: ["{{ .name }}-outputs"]
  dashboard:
    singular: VPC
    category: Infrastructure
    icon: <base64-svg>
```

### Go types

New file `api/v1alpha1/tofuapplicationdefinitions_types.go`:

```go
type TofuApplicationDefinition struct {
    metav1.TypeMeta   `json:",inline"`
    metav1.ObjectMeta `json:"metadata,omitempty"`
    Spec              TofuApplicationDefinitionSpec `json:"spec,omitempty"`
}

type TofuApplicationDefinitionSpec struct {
    Application ApplicationDefinitionApplication        `json:"application"`
    Terraform   TofuApplicationDefinitionTerraform      `json:"terraform"`
    Secrets     *ApplicationDefinitionResources         `json:"secrets,omitempty"`
    Services    *ApplicationDefinitionResources         `json:"services,omitempty"`
    Ingresses   *ApplicationDefinitionResources         `json:"ingresses,omitempty"`
    Dashboard   *ApplicationDefinitionDashboard         `json:"dashboard,omitempty"`
}

type TofuApplicationDefinitionTerraform struct {
    SourceRef                  tfv1alpha2.CrossNamespaceSourceReference `json:"sourceRef"`
    Path                       string                                   `json:"path,omitempty"`
    Prefix                     string                                   `json:"prefix,omitempty"`
    Labels                     map[string]string                        `json:"labels,omitempty"`
    Interval                   metav1.Duration                          `json:"interval,omitempty"`
    ApprovePlan                string                                   `json:"approvePlan,omitempty"`
    DestroyResourcesOnDeletion bool                                     `json:"destroyResourcesOnDeletion,omitempty"`
    WriteOutputsToSecret       *tfv1alpha2.WriteOutputsToSecretSpec     `json:"writeOutputsToSecret,omitempty"`
    RunnerPodTemplate          *tfv1alpha2.RunnerPodTemplate            `json:"runnerPodTemplate,omitempty"`
}
```

The `ApplicationDefinitionApplication`, `ApplicationDefinitionResources` and `ApplicationDefinitionDashboard` types are reused as-is from the existing `applicationdefinitions_types.go` — these parts are backend-agnostic.

### API server changes

- New binary `cmd/cozystack-tofu-api/main.go` (or a feature-flagged subcommand) — boots an aggregation API server identical in shape to `cozystack-api`.
- Group: `tofu.apps.cozystack.io/v1alpha1` (separate to avoid kind collisions with Helm-side AGL).
- Reads `TofuApplicationDefinition` list on startup, registers dynamic kinds against the same internal `Application` type used by the Helm path (the type is generic enough — only `Spec` is `runtime.RawExtension`).
- Registers tofu-controller schema (`infra.contrib.fluxcd.io/v1alpha2`) instead of helm-controller schema.

### REST translation

A near-copy of `pkg/registry/apps/application/rest.go` lives at `pkg/registry/tofuapps/application/rest.go`:

| Method  | Existing (Helm)                    | New (Tofu)                              |
| ------- | ---------------------------------- | --------------------------------------- |
| Create  | build `HelmRelease`, `c.Create`    | build `Terraform`, `c.Create`           |
| Get     | fetch `HelmRelease`, project       | fetch `Terraform`, project              |
| List    | list `HelmRelease` by labels       | list `Terraform` by labels              |
| Update  | patch `HelmRelease.Spec.Values`    | patch `Terraform.Spec.Vars`             |
| Delete  | delete `HelmRelease`               | delete `Terraform` (respects `destroyResourcesOnDeletion`) |

Field mapping `Application` → `Terraform`:

```text
Application.Name                   → Terraform.Name = prefix + Application.Name
Application.Namespace              → Terraform.Namespace
Application.Labels                 → Terraform.Labels (with LabelPrefix)
Application.Spec (RawExtension)    → Terraform.Spec.Vars  (flattened, top-level keys = vars)
TofuAppDef.Terraform.SourceRef     → Terraform.Spec.SourceRef
TofuAppDef.Terraform.Path          → Terraform.Spec.Path
TofuAppDef.Terraform.ApprovePlan   → Terraform.Spec.ApprovePlan
TofuAppDef.Terraform.WriteOutputs… → Terraform.Spec.WriteOutputsToSecret
TofuAppDef.Terraform.RunnerPod…    → Terraform.Spec.RunnerPodTemplate
```

`Application.Spec` keys become Terraform input variables. A small validator on the way in: keys must match `^[a-z_][a-z0-9_]*$` (HCL identifier).

### Status projection

`Terraform.Status` → `Application.Status` (visible to the user):

```text
Terraform.Status.Conditions[Ready]    → Application.Status.Conditions[Ready]
Terraform.Status.LastAppliedRevision  → Application.Status.LastAppliedRevision
Terraform.Status.LastPlannedRevision  → Application.Status.LastPlannedRevision
Terraform.Status.Plan.Pending         → Application.Status.PendingApproval (bool)
Terraform.Status.AvailableOutputs     → Application.Status.Outputs ([]string)
```

Actual output values land in the `writeOutputsToSecret` secret and are surfaced via the existing `spec.secrets.include` mechanism — no special handling needed.

### Reconciler

New controller `internal/controller/tofuapplicationdefinition_controller.go`:

- Watches `TofuApplicationDefinition`.
- Finds existing `Terraform` CRs by label selector `apps.cozystack.io/application.kind=<Kind>`.
- Patches `sourceRef`, `path`, `approvePlan`, `runnerPodTemplate` when the definition changes (mirrors `ApplicationDefinitionHelmReconciler`).
- A second controller (or a shared one) updates the config-hash annotation on the `cozystack-tofu-api` deployment to trigger a pod restart, identical to the Helm path.

### Packaging

New chart `packages/system/cozystack-tofu-api/` mirroring `packages/system/cozystack-api/`:

- Deployment of the new apiserver binary.
- `APIService` registration for `tofu.apps.cozystack.io/v1alpha1`.
- RBAC: read `TofuApplicationDefinition`, full access to `Terraform` and the underlying secrets.

Dependency: requires tofu-controller to be installed. Either:

- Add it as a dependency of the `cozystack` umbrella chart (preferred), or
- Document it as a prerequisite and fail-fast at apiserver startup if the CRD is missing.

## Implementation plan

1. **CRD + types** — `TofuApplicationDefinition` Go types, deepcopy, manifests, validation webhook. No behaviour yet.
2. **API server skeleton** — new binary, dynamic type registration, openapi from `application.openAPISchema`. No translation yet.
3. **REST translation (Create/Get/List/Delete)** — copy from Helm path, swap target type.
4. **Status projection** — map Terraform conditions/outputs into Application status.
5. **Reconciler** — keep `Terraform` CRs in sync with definition changes.
6. **Packaging** — Helm chart for the apiserver, RBAC, APIService.
7. **Example package** — one real Terraform-backed package (e.g. `VPC` or `DNSZone`) for an end-to-end test.
8. **Docs** — author-facing guide on writing a `TofuApplicationDefinition`.

Stages 1–4 are mergeable independently behind a feature flag.

## Risks

- **Two apiservers in the aggregation layer** add operational surface (two deployments, two health checks, two restarts on config change). Mitigated by sharing a single binary with two subcommands if useful.
- **Output secrets** can leak provider credentials if `writeOutputsToSecret` is misconfigured. Mitigated by an admission policy that requires explicit output allow-listing.
- **Tofu-controller upstream churn** — `flux-iac/tofu-controller` is a community fork of the archived `weaveworks/tf-controller`. API may move; pin `v1alpha2` and track.
- **No clean migration path to Draft 2** — once two top-level CRDs exist (`ApplicationDefinition`, `TofuApplicationDefinition`), unifying them later requires a deprecation cycle.

## Open questions

- Should we share `apps.cozystack.io` group and rely on `kind` uniqueness, or use a distinct `tofu.apps.cozystack.io` group? Distinct group is safer for now.
- Should the runner pod template be defined per-AppDef (current design) or per-instance (`Application.Spec.runnerPodOverride`)? Per-instance gives tenants control over cloud identities — probably needed for multi-tenant clusters.
- How to expose plan diffs when `approvePlan: ""` (manual)? Likely via a status field that the dashboard renders, plus a separate `approve` subresource on `Application`.

## Example: `VPC` package

```yaml
apiVersion: apps.cozystack.io/v1alpha1
kind: TofuApplicationDefinition
metadata:
  name: vpc
spec:
  application:
    kind: VPC
    singular: vpc
    plural: vpcs
    openAPISchema: |
      {
        "type": "object",
        "required": ["cidr", "region"],
        "properties": {
          "cidr":   { "type": "string", "pattern": "^[0-9./]+$" },
          "region": { "type": "string" }
        }
      }
  terraform:
    sourceRef:
      kind: OCIRepository
      name: aws-vpc-module
      namespace: cozy-system
    path: ./modules/vpc
    prefix: vpc-
    approvePlan: auto
    destroyResourcesOnDeletion: true
    writeOutputsToSecret:
      name: "{{ .name }}-outputs"
    runnerPodTemplate:
      spec:
        serviceAccountName: aws-tofu-runner
  secrets:
    include:
      - resourceNames: ["{{ .name }}-outputs"]
  dashboard:
    singular: VPC
    category: Infrastructure
```

End-user request:

```yaml
apiVersion: tofu.apps.cozystack.io/v1alpha1
kind: VPC
metadata:
  name: prod
  namespace: tenant-acme
spec:
  cidr: 10.10.0.0/16
  region: eu-central-1
```

Result: tofu-controller reconciles `Terraform/vpc-prod` against the AWS VPC module, outputs (`vpc_id`, `subnet_ids`) land in `Secret/prod-outputs`, surfaced through cozystack's existing secret-include machinery.
