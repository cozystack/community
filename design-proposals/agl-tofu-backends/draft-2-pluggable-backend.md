# Draft 2: Pluggable Backend for Cozystack AGL

- **Status:** Draft
- **Author:** @kitsunoff
- **Date:** 2026-05-20
- **Target project:** [cozystack/cozystack](https://github.com/cozystack/cozystack)

> Companion document to [`README.md`](./README.md) (overview and comparison) and [`draft-1-parallel-tofu-stack.md`](./draft-1-parallel-tofu-stack.md) (alternative design).

## Summary

Refactor Cozystack's Application Generation Layer to support multiple release backends through a single, generic `ApplicationDefinition`. The first two backends are Helm (existing behaviour) and Terraform/OpenTofu via tofu-controller; the design leaves room for ArgoCD `Application`, Flux `Kustomization`, or plain manifests later without further schema changes.

Existing packages continue to work with no manifest changes, thanks to a defaulted backend type.

## Motivation

Today the AGL is structurally a Helm runtime dressed up as a generic abstraction: the CRD field is named `release`, types embed `helmv2.CrossNamespaceSourceReference`, the REST layer constructs `HelmRelease` directly, and a dedicated reconciler patches `HelmRelease` fields. Adding Terraform support by copying the stack (Draft 1) gets us there fast, but every additional backend pays the same duplication cost.

A pluggable backend turns AGL from "Helm generator" into a real abstraction layer: the user-facing kind, OpenAPI schema, dashboard wiring, secret/service inclusion, and config-hash restart logic are written once; the per-backend code is a small interface implementation.

## Goals

- One `ApplicationDefinition` CRD, multiple backend implementations.
- Backwards-compatible with the current schema (no breaking change to existing packages).
- Add tofu-controller as the second backend.
- A clear Go interface that a third backend can implement without touching the rest of the codebase.

## Non-goals

- Allowing multiple backends inside a single `ApplicationDefinition` (one backend per definition; composite resources are out of scope).
- Building a generic "Terraform module marketplace" — package authors still ship their own modules.
- Replacing Flux. Both backends still reconcile through Flux primitives (helm-controller, tofu-controller).

## Design

### Backend abstraction

A new package `pkg/agl/backend/` defines:

```go
package backend

type Type string

const (
    TypeHelm      Type = "Helm"
    TypeTerraform Type = "Terraform"
)

// Backend translates between the user-facing Application and a concrete
// Flux-managed target object (HelmRelease, Terraform, ...).
type Backend interface {
    // Type returns the discriminator value, e.g. "Helm" or "Terraform".
    Type() Type

    // TargetGVK is the GroupVersionKind of the backing object (HelmRelease,
    // Terraform CR, ArgoCD Application, ...). Used by the REST layer to
    // list/get/watch.
    TargetGVK() schema.GroupVersionKind

    // TargetName computes the name of the backing object from the
    // user-facing name and the definition (typically applies a prefix).
    TargetName(appName string, def *v1alpha1.ApplicationDefinition) string

    // Build produces the backing object from an Application.
    Build(
        ctx context.Context,
        app *appsv1alpha1.Application,
        def *v1alpha1.ApplicationDefinition,
    ) (client.Object, error)

    // ProjectStatus translates the backing object's status into the
    // generic Application.Status the user sees.
    ProjectStatus(target client.Object) (appsv1alpha1.ApplicationStatus, error)

    // Reconcile keeps an existing backing object aligned with the
    // definition when the definition changes (mirrors today's
    // ApplicationDefinitionHelmReconciler logic).
    Reconcile(
        ctx context.Context,
        c client.Client,
        target client.Object,
        def *v1alpha1.ApplicationDefinition,
    ) (updated bool, err error)
}

// Registry resolves a definition to its backend implementation.
type Registry interface {
    Get(def *v1alpha1.ApplicationDefinition) (Backend, error)
    All() []Backend
}
```

Two implementations land in the same PR:

- `pkg/agl/backend/helm/` — extracted from the current `rest.go` and `applicationdefinition_helmreconciler.go`.
- `pkg/agl/backend/terraform/` — new, targets tofu-controller `Terraform` CRD.

### Updated `ApplicationDefinition` schema

`apps.cozystack.io/v1alpha1` gains a `backend` field; the existing `release` field becomes an alias.

```yaml
apiVersion: apps.cozystack.io/v1alpha1
kind: ApplicationDefinition
metadata:
  name: postgres
spec:
  application:
    kind: Postgres
    singular: postgres
    plural: postgreses
    openAPISchema: |
      { ... }
  backend:
    type: Helm                # discriminator: Helm | Terraform
    helm:                     # required when type=Helm
      chartRef:
        kind: ExternalArtifact
        name: cozystack-postgres-chart
        namespace: cozy-system
      prefix: postgres-
      labels:
        sharding.fluxcd.io/key: tenants
      valuesFrom:
        - kind: Secret
          name: cozystack-values
  secrets: { ... }
  services: { ... }
  dashboard: { ... }
```

```yaml
apiVersion: apps.cozystack.io/v1alpha1
kind: ApplicationDefinition
metadata:
  name: vpc
spec:
  application:
    kind: VPC
    singular: vpc
    plural: vpcs
    openAPISchema: |
      { ... }
  backend:
    type: Terraform
    terraform:               # required when type=Terraform
      sourceRef:
        kind: OCIRepository
        name: aws-vpc-module
        namespace: cozy-system
      path: ./
      prefix: vpc-
      approvePlan: auto
      destroyResourcesOnDeletion: true
      writeOutputsToSecret:
        name: "{{ .name }}-outputs"
      runnerPodTemplate:
        spec:
          serviceAccountName: aws-tofu-runner
  secrets: { ... }
  dashboard: { ... }
```

### Go types

```go
type ApplicationDefinitionSpec struct {
    Application ApplicationDefinitionApplication `json:"application"`

    // Backend is the new discriminated union.
    Backend *Backend `json:"backend,omitempty"`

    // Release is the legacy field. Kept for backwards compatibility.
    // If Backend is unset and Release is set, treated as Backend{Type: Helm, Helm: from(Release)}.
    // +deprecated
    Release *ApplicationDefinitionRelease `json:"release,omitempty"`

    Secrets   *ApplicationDefinitionResources `json:"secrets,omitempty"`
    Services  *ApplicationDefinitionResources `json:"services,omitempty"`
    Ingresses *ApplicationDefinitionResources `json:"ingresses,omitempty"`
    Dashboard *ApplicationDefinitionDashboard `json:"dashboard,omitempty"`
}

type Backend struct {
    // +kubebuilder:validation:Enum=Helm;Terraform
    Type BackendType `json:"type"`

    Helm      *HelmBackend      `json:"helm,omitempty"`
    Terraform *TerraformBackend `json:"terraform,omitempty"`
}

type HelmBackend struct {
    ChartRef   *helmv2.CrossNamespaceSourceReference `json:"chartRef"`
    Prefix     string                                `json:"prefix,omitempty"`
    Labels     map[string]string                     `json:"labels,omitempty"`
    ValuesFrom []helmv2.ValuesReference              `json:"valuesFrom,omitempty"`
}

type TerraformBackend struct {
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

A defaulting webhook (or in-process normalization at apiserver startup) projects `spec.release` onto `spec.backend.helm` when only the legacy field is present, so existing packages keep working.

### API server changes

- The aggregation API server schema registers **both** `helmv2` and `tfv1alpha2` (and any future backend's GVKs). Cheap — schemes are just type bindings.
- Dynamic kind registration is unchanged: every package still produces one user-facing kind backed by the internal `Application` type.
- `pkg/registry/apps/application/rest.go` is the main refactor target. Today it calls helm-controller types directly; after refactoring it calls into a `Backend` interface obtained from the registry:

```go
func (r *REST) Create(ctx context.Context, obj runtime.Object, ...) (..., error) {
    app := obj.(*appsv1alpha1.Application)
    def := r.definitions.Get(r.kindName)
    b, err := r.backends.Get(def)
    if err != nil { return nil, err }

    target, err := b.Build(ctx, app, def)
    if err != nil { return nil, err }

    // Common label/annotation injection (extracted once).
    injectAGLLabels(target, app, r.kindName)

    if err := r.c.Create(ctx, target); err != nil { return nil, err }
    return app, nil
}
```

`Get`/`List`/`Update`/`Delete` follow the same shape: the REST layer is backend-agnostic, the backend knows the target object type.

### Generic status

`Application.Status` becomes a small generic envelope plus an opaque per-backend extension:

```go
type ApplicationStatus struct {
    // Common, projected by every backend.
    Conditions []metav1.Condition `json:"conditions,omitempty"`
    Ready      bool               `json:"ready,omitempty"`
    Message    string             `json:"message,omitempty"`

    // Backend-specific, raw JSON. Schema documented per backend.
    Backend *runtime.RawExtension `json:"backend,omitempty"`
}
```

- Helm projects `lastAppliedRevision`, `lastAttemptedRevision` into `backend`.
- Terraform projects `lastAppliedRevision`, `lastPlannedRevision`, `availableOutputs`, `pendingApproval` into `backend`.

This avoids forcing every field of every backend into the top-level schema while keeping the common ready/conditions contract uniform.

### Generic reconciler

Today's `ApplicationDefinitionHelmReconciler` is replaced by a single `ApplicationDefinitionReconciler` that:

1. Watches `ApplicationDefinition`.
2. Resolves backend via the registry.
3. Lists existing target objects by label selector `apps.cozystack.io/application.kind=<Kind>`.
4. For each target, calls `backend.Reconcile(ctx, c, target, def)`.

The existing config-hash restart logic for the aggregation apiserver is untouched.

### Backwards compatibility

Required behaviour:

- A `v1alpha1` `ApplicationDefinition` shipped today, with `spec.release` and no `spec.backend`, must continue to work after the upgrade.
- Existing `HelmRelease`s owned by the AGL must not be recreated; they should be reconciled by the new generic reconciler exactly as before.

Mechanism:

- The CRD keeps both `release` (deprecated) and `backend` fields in `v1alpha1` for one minor version.
- The API server applies a normalization pass on read: if `backend == nil && release != nil`, synthesize `backend = {type: Helm, helm: from(release)}` in memory. Persisted objects are not mutated.
- A `v1alpha2` conversion webhook follows in a later release: `release` is removed from the schema; conversion fills it from `backend.helm` for clients still on v1alpha1.

### Packaging

- No new chart. `packages/system/cozystack-api/` gains a dependency on tofu-controller CRDs (soft dependency: the registry skips registering the Terraform backend if `infra.contrib.fluxcd.io/v1alpha2` is not installed, with a clear log line).
- RBAC of `cozystack-apiserver` extended to read/write `Terraform` CRs.

## Implementation plan

1. **Extract Helm logic** — move all helm-specific code from `rest.go` and `applicationdefinition_helmreconciler.go` behind the new `Backend` interface. No behaviour change, no schema change. PR is large but mechanical; covered by existing e2e tests.
2. **Schema: add `backend`** — introduce `spec.backend` as optional, defaulted from `spec.release`. CRD validation, conversion logic.
3. **Generic reconciler** — replace the helm-specific reconciler with the registry-driven one. Helm backend wired through the registry.
4. **Terraform backend** — implement `pkg/agl/backend/terraform/`. Includes vars marshalling (`Application.Spec` → `Terraform.Spec.Vars`), status projection, reconcile diffing.
5. **Example package** — one Terraform-backed package end-to-end.
6. **Deprecation notice** — emit warning when `spec.release` is used; plan `v1alpha2` removal.
7. **Docs** — update author guide, add backend authoring guide.

Stages 1 and 3 are the risky ones (touch the hot path of every existing package). Stages 4–7 are additive.

## Risks

- **Refactor blast radius.** `rest.go` is large and touched by every Helm-backed package. Mitigation: stage 1 is mechanical extraction with no behaviour change, validated by the full e2e suite before any new backend lands.
- **Interface fitness.** Designing the `Backend` interface against only two backends risks a leaky abstraction the third backend (Argo, Kustomization) can't honour. Mitigation: sketch a third backend on paper before merging stage 1; treat it as a design constraint.
- **Status schema churn.** Opaque `Backend *runtime.RawExtension` defers the schema problem rather than solving it. Consumers (dashboard, kubectl printers) need a documented contract per backend type. Mitigation: ship printer columns and dashboard schemas per backend alongside each implementation.
- **Conversion webhook complexity.** `v1alpha1 → v1alpha2` conversion has to be exact for old objects. Mitigation: keep `v1alpha1` indefinitely if needed; conversion isn't on the critical path for the feature.

## Migration plan

- **Release N (this PR series):** ships behind a feature flag `--enable-pluggable-backends`. Defaults to off in stable channel, on in next. Helm path is rewritten to go through the backend interface either way (so the flag only gates the new types and the Terraform backend).
- **Release N+1:** flag flipped to on by default. Terraform backend GA.
- **Release N+2:** `spec.release` marked deprecated in CRD docs; deprecation warning logged at admission time.
- **Release N+3 (`v1alpha2`):** `spec.release` removed; conversion webhook fills it for legacy v1alpha1 clients.

## Open questions

- **Per-instance backend override?** Should an `Application` (the user-facing instance) be able to override `runnerPodTemplate` or `chartRef` for tenant isolation? Probably yes for cloud creds in multi-tenant clusters, but adds complexity to the `Build` contract.
- **Shared identity injection.** Today every backend gets the same set of AGL labels/annotations. Should that be the backend's responsibility, or extracted into a wrapper? Currently designed as wrapper (`injectAGLLabels` outside the backend), which keeps backends small.
- **Cross-backend ownership.** Can a Helm-backed package's chart reference a `Terraform` CR managed by another AppDef? Out of scope for this draft, but the design should not preclude it.

## Example: side-by-side definitions

```yaml
---
apiVersion: apps.cozystack.io/v1alpha1
kind: ApplicationDefinition
metadata:
  name: postgres
spec:
  application:
    kind: Postgres
    singular: postgres
    plural: postgreses
    openAPISchema: |
      { ... }
  backend:
    type: Helm
    helm:
      chartRef:
        kind: ExternalArtifact
        name: cozystack-postgres-chart
        namespace: cozy-system
      prefix: postgres-
      valuesFrom:
        - kind: Secret
          name: cozystack-values
  secrets:
    include:
      - resourceNames: ["postgres-{{ .name }}-credentials"]
---
apiVersion: apps.cozystack.io/v1alpha1
kind: ApplicationDefinition
metadata:
  name: dns-zone
spec:
  application:
    kind: DNSZone
    singular: dnszone
    plural: dnszones
    openAPISchema: |
      { ... }
  backend:
    type: Terraform
    terraform:
      sourceRef:
        kind: OCIRepository
        name: cloudflare-dns-module
        namespace: cozy-system
      path: ./
      prefix: dns-
      approvePlan: auto
      destroyResourcesOnDeletion: true
      writeOutputsToSecret:
        name: "{{ .name }}-outputs"
      runnerPodTemplate:
        spec:
          serviceAccountName: cloudflare-tofu-runner
  secrets:
    include:
      - resourceNames: ["{{ .name }}-outputs"]
```

End users in `tenant-acme`:

```yaml
---
apiVersion: apps.cozystack.io/v1alpha1
kind: Postgres
metadata: { name: app-db, namespace: tenant-acme }
spec:
  size: 20Gi
  replicas: 3
---
apiVersion: apps.cozystack.io/v1alpha1
kind: DNSZone
metadata: { name: acme-prod, namespace: tenant-acme }
spec:
  zone: acme.example.com
  ttl: 300
```

One CRD, one apiserver, one reconciler. The backend boundary is the only place that knows whether the result is a `HelmRelease` or a `Terraform` CR.
