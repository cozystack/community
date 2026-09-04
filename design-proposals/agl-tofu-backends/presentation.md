---
marp: true
theme: default
paginate: true
size: 16:9
header: "Cozystack AGL — Terraform Backend"
footer: "Draft proposal · 2026-05-20"
style: |
  section { font-size: 26px; }
  h1 { color: #1a73e8; }
  h2 { color: #1a73e8; }
  code { background: #f6f8fa; padding: 2px 5px; border-radius: 3px; }
  table { font-size: 22px; }
  .small { font-size: 20px; }
---

# Terraform-backed resources<br>in Cozystack AGL

Two design drafts for mapping API resources<br>to `Terraform` CRs of tofu-controller

Maxim Belyy · 2026-05-20

---

## Context: what is Cozystack AGL?

**Application Generation Layer** — maps a user-facing Kubernetes kind<br>(e.g. `Postgres`, `Kafka`, `Bucket`) to a Flux `HelmRelease` under the hood.

- Cluster-scoped CRD `ApplicationDefinition` describes the mapping.
- Custom aggregation API server registers user-facing kinds dynamically.
- Per-instance: `Application.Spec` → `HelmRelease.Spec.Values`.
- Dashboard, OpenAPI validation, RBAC come for free.

The user creates a high-level resource. The platform turns it into a real workload.

---

## How it works today

```text
                  ┌──────────────────────────────┐
   kubectl ──►    │ cozystack-api (aggregation)  │
   apply Postgres │  - reads ApplicationDefinition│
                  │  - registers dynamic kinds    │
                  │  - REST: Postgres ↔ HelmRelease│
                  └──────────────┬───────────────┘
                                 │ creates
                                 ▼
                       ┌──────────────────┐
                       │   HelmRelease    │  ◄── Flux helm-controller
                       │  postgres-mydb   │       reconciles to actual
                       └──────────────────┘       Kubernetes workloads
```

One CRD, one apiserver, one reconciler — and **one backend: Helm**.

---

## The problem

Some primitives don't fit Helm:

- Cloud VPCs, subnets, IAM
- Managed DNS zones
- Managed databases on hyperscalers
- External buckets, queues, secrets

These are naturally **Terraform/OpenTofu** territory.

`flux-iac/tofu-controller` already provides a Flux-native `Terraform` CRD<br>with the same lifecycle model as `HelmRelease`.

**Goal:** let AGL emit `Terraform` CRs the same way it emits `HelmRelease`.

---

## Where the Helm assumption lives

| File | What's hard-coded |
| --- | --- |
| `api/v1alpha1/applicationdefinitions_types.go` | imports `helmv2`, field `Release.ChartRef` is `helmv2.CrossNamespaceSourceReference` |
| `pkg/registry/apps/application/rest.go` | builds and reads `helmv2.HelmRelease` directly |
| `pkg/apiserver/apiserver.go` | registers only the helm-controller schema |
| `internal/controller/applicationdefinition_helmreconciler.go` | patches `HelmRelease.Spec.ChartRef` / `ValuesFrom` |

The abstraction is structurally there. The code is not.

---

## Two ways forward

**Draft 1** — Parallel stack: copy the AGL for Terraform, leave Helm path untouched.

**Draft 2** — Pluggable backend: refactor AGL so Helm and Terraform are<br>two implementations of one interface.

Let's look at both.

---

## Draft 1 — Parallel Tofu Stack

New CRD `TofuApplicationDefinition`, new apiserver, new reconciler — sitting next to the existing Helm AGL.

```yaml
apiVersion: apps.cozystack.io/v1alpha1
kind: TofuApplicationDefinition
spec:
  application:
    kind: VPC
    openAPISchema: |
      { "type": "object", "properties": { "cidr": ... } }
  terraform:
    sourceRef:                       # tofu-controller source
      kind: OCIRepository
      name: aws-vpc-module
    approvePlan: auto
    destroyResourcesOnDeletion: true
    writeOutputsToSecret:
      name: "{{ .name }}-outputs"
```

`Application.Spec` keys become Terraform input variables.

---

## Draft 1 — Architecture

```text
                ┌───────────────────────────┐    ┌────────────────────────────┐
  Postgres ───► │ cozystack-api             │    │ cozystack-tofu-api         │ ◄─── VPC
                │ ↳ ApplicationDefinition   │    │ ↳ TofuApplicationDefinition│
                │ ↳ HelmRelease translator  │    │ ↳ Terraform translator     │
                └─────────────┬─────────────┘    └─────────────┬──────────────┘
                              ▼                                ▼
                       ┌────────────┐                  ┌──────────────┐
                       │ HelmRelease│                  │  Terraform   │
                       └────────────┘                  └──────────────┘
                       helm-controller                  tofu-controller
```

Two parallel stacks. Zero shared code (initially).

---

## Draft 1 — Trade-offs

**Pros**

- Days, not weeks. Mechanical copy of the existing stack.
- Zero risk to Helm-backed packages.
- Easy review; can land incrementally behind a feature flag.

**Cons**

- ~80% code duplication (REST, reconciler, dynamic-type registration).
- Two apiservers in the aggregation layer — more operational surface.
- A third backend (Argo, Kustomization) means another full copy.
- Future unification needs a deprecation cycle.

---

## Draft 2 — Pluggable backend

One `ApplicationDefinition`, discriminated by `backend.type`.

```yaml
apiVersion: apps.cozystack.io/v1alpha1
kind: ApplicationDefinition
spec:
  application:
    kind: VPC
    openAPISchema: |
      { ... }
  backend:
    type: Terraform               # Helm | Terraform | ...
    terraform:
      sourceRef: { kind: OCIRepository, name: aws-vpc-module, ... }
      approvePlan: auto
      destroyResourcesOnDeletion: true
```

Existing packages keep working — `spec.release` is defaulted to `spec.backend.helm`.

---

## Draft 2 — The interface

```go
type Backend interface {
    Type() Type                                              // "Helm" | "Terraform"
    TargetGVK() schema.GroupVersionKind                      // HelmRelease | Terraform | ...
    TargetName(appName string, def *ApplicationDefinition) string

    Build(ctx, app *Application, def *ApplicationDefinition) (client.Object, error)
    ProjectStatus(target client.Object) (ApplicationStatus, error)
    Reconcile(ctx, c client.Client, target client.Object,
              def *ApplicationDefinition) (updated bool, err error)
}
```

Two implementations in the same PR:

- `pkg/agl/backend/helm/` — extracted from current code.
- `pkg/agl/backend/terraform/` — new.

---

## Draft 2 — Architecture

```text
                ┌────────────────────────────────────────────────┐
  Postgres ───► │ cozystack-api                                  │ ◄─── VPC
                │   ApplicationDefinition (single CRD)            │
                │                                                 │
                │   ┌─────────────────┐    ┌───────────────────┐ │
                │   │  HelmBackend    │    │ TerraformBackend  │ │
                │   └────────┬────────┘    └─────────┬─────────┘ │
                └────────────┼───────────────────────┼───────────┘
                             ▼                       ▼
                      ┌────────────┐         ┌──────────────┐
                      │ HelmRelease│         │  Terraform   │
                      └────────────┘         └──────────────┘
```

One apiserver. One reconciler. N backends.

---

## Draft 2 — Trade-offs

**Pros**

- Operationally simpler in the long run: one CRD, one apiserver, one reconciler.
- New backends (Argo, Kustomization, plain manifests) cost only an interface impl.
- AGL finally is what its name says: a *Generation Layer*, not a Helm wrapper.

**Cons**

- Large refactor of `rest.go` and the reconciler — touches every Helm package.
- Interface designed against two backends risks being leaky for a third.
- Generic `Application.Status` envelope + per-backend opaque extension —<br>defers some schema problems rather than solving them.

---

## Comparison

|                            | Draft 1 (parallel)       | Draft 2 (decoupled)     |
| -------------------------- | ------------------------ | ----------------------- |
| Time to first PoC          | days                     | weeks                   |
| Regression risk            | minimal                  | medium                  |
| Code duplication           | high                     | none                    |
| Cost of 3rd backend        | another full copy        | one interface impl      |
| User-facing UX             | two AppDef kinds         | one AppDef, switch type |
| Review burden              | low                      | high, needs slicing     |
| Long-term maintenance cost | high                     | low                     |

---

## Recommendation

**Hybrid path:**

1. **Now:** Draft 1 as a feature-branch PoC.<br>Prove tofu-controller mapping. Surface real requirements for vars marshalling,<br>cloud-creds runner pods, output secret handling.

2. **Next:** With two working backends in hand, do Draft 2 refactor.<br>The interface is designed against concrete code, not speculation.

This trades a small amount of throwaway code for much lower<br>risk of a bad abstraction.

---

## Next steps

- Review and approve the two drafts.
- Decide: ship Draft 1 standalone, or commit to the hybrid path?
- Pick the first Terraform-backed example package<br>(candidate: `VPC`, `DNSZone`, `S3Bucket`).
- Discuss in cozystack maintainers' sync; if positive, open an RFC issue.

**Documents in this draft:**

- `draft-1-parallel-tofu-stack.md`
- `draft-2-pluggable-backend.md`
- `presentation.md` (this deck)

---

## Questions

Thank you.

<span class="small">Sources studied: `cozystack/api/v1alpha1/applicationdefinitions_types.go`,<br>`cozystack/pkg/registry/apps/application/rest.go`,<br>`cozystack/pkg/apiserver/`, `cozystack/internal/controller/`,<br>`cozystack/packages/system/postgres-rd/`.</span>
