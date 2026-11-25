<!-- Place this file at design-proposals/serverless-managed-apps/README.md -->
# Serverless applications (FaaS + event-driven) as managed apps

- **Title:** `Serverless applications (FaaS + event-driven) as managed apps`
- **Author(s):** `@IvanHunters`
- **Date:** `2026-07-08`
- **Status:** Draft

## Overview

Cozystack ships managed databases, queues and virtualization, but has no
serverless story. This proposal adds one, as tenant-facing marketplace entries.
Two capabilities are commonly called "serverless" and they are different
products: **event-driven autoscaling with scale-to-zero** for ordinary workloads
(queues, cron, Kafka, metrics), which [KEDA](https://keda.sh) does; and
**request-driven FaaS** (scale-to-zero on HTTP, per-revision routing, a URL per
service), which [Knative Serving](https://knative.dev) does. We propose both.

Each follows the standard Cozystack managed-app split, exactly as Postgres/CNPG
and LINSTOR/piraeus-operator do:

- the **controller** (KEDA operator, Knative controller) is trusted platform code
  and runs cluster-wide in its own `cozy-*` system namespace;
- the **workloads** (the tenant's function pods / scaled workers, and the native
  `Knative Service` / `Deployment`+`ScaledObject` behind them) live in the
  **tenant namespace**;
- the tenant interacts only through a Cozystack CR wrapped by an
  `ApplicationDefinition`; the native CRs are an implementation detail, the same
  contract as `Postgres` over CNPG `Cluster`.

Untrusted-code isolation (a function runs arbitrary tenant code) is handled
orthogonally by **placement** on a [`compute-plane`](../compute-plane) (PR #17),
not by anything specific to serverless — see [Security](#security).

## Scope and related proposals

- **[`compute-plane`](../compute-plane) (PR #17)** — the generic isolation
  primitive for code-executing apps. Serverless workloads run arbitrary tenant
  code, so a ComputePlane is the recommended *placement* for them; but that is
  #17's mechanism applied to these apps, referenced here, not re-specified.
  Serverless does not hard-depend on #17: without it, workloads run in the tenant
  namespace like any other managed app, with the isolation trade-off documented
  in Security.
- **`structured-external-exposure` (PR #29)** — Knative functions need external
  HTTP URLs; their exposure rides #29's model rather than a new entrypoint.
- **`application-definition-versioning` (PR #6)** — serverless adds new
  `ApplicationDefinition`s; versioning/conversion follows #6.
- **cozymarketplace (PR #18 / #23)** — serverless entries live in that catalog
  model.
- **Deferred:** a source-to-image / function build pipeline (images-in only);
  billing/metering of invocations. Called out under Open questions.

## Context

Cozystack has no serverless capability today (grep of `packages/` confirms: the
only `keda` hits are inside the `ingress-nginx` subchart, autoscaling the ingress
controller itself, unrelated). The primitives exist; the assembly is new.

- **Package tiers.** `packages/apps/*` (tenant-facing managed apps, e.g.
  `apps/postgres`), `packages/extra/*`, `packages/system/*` (operators, e.g.
  `system/postgres-operator`), `packages/core/platform`.
- **Managed-app pattern (two PackageSources).** Postgres ships as
  `cozystack.postgres-operator` (operator only; `dependsOn: networking,
  prometheus-operator-crds, cert-manager`; installs into `cozy-postgres-operator`)
  and `cozystack.postgres-application` (bundles `apps/postgres` +
  `system/postgres-rd` as components; `dependsOn: networking, postgres-operator,
  cozystack-engine`). Sources in `packages/core/platform/sources/*.yaml`; bundles
  in `packages/core/platform/templates/bundles/{system,paas,…}.yaml`.
- **Marketplace registration.** A `*-rd` package holds an `ApplicationDefinition`
  under `cozyrds/` (e.g. `system/postgres-rd/cozyrds/postgres.yaml`; CRD in
  `system/application-definition-crd/`). It maps a user-facing `kind` to a
  `HelmRelease` (`spec.release.prefix`, `spec.release.chartRef`), carries the
  values `openAPISchema` and `spec.dashboard.*`. Schema is generated from
  `values.yaml` `cozyvalues-gen` annotations (`Makefile`).
- **Dependency ordering.** `PackageSource.spec.variants[].dependsOn` at the
  package level; Flux `HelmRelease.spec.dependsOn` at the component level.
- **Networking.** CNI is Cilium 1.19.5 with `gatewayAPI.enabled: true` and
  `envoy.enabled: true` (`packages/system/cilium/values.yaml`); MetalLB for L2;
  ingress-nginx default (`packages/extra/ingress/`). Since function workloads run
  in the tenant namespace, the Knative request path uses this Cilium Gateway API
  (see Design §4).
- **Migrations.** New packages are registered on existing clusters via a numbered
  migration (`packages/core/platform/images/migrations/`; current
  `migrations.targetVersion` is 50, last file `49`).

### The problem

> "I want to offer functions from the Cozystack dashboard — an HTTP endpoint that
> scales to zero, or a worker that wakes on a queue message — as a managed
> service, the same one-click way a tenant gets a Postgres. There is no managed
> path today."

Serverless is a standard PaaS expectation, and both target workloads map cleanly
onto the existing managed-app model: controller in its own namespace, tenant CR
rendering native objects into the tenant namespace.

## Goals

- Marketplace-installable serverless for tenants: event-driven (KEDA) and HTTP
  FaaS (Knative).
- Controller runs cluster-wide in its own `cozy-*` system namespace (trusted
  platform code, standard operator pattern).
- Tenant workloads and their native CRs render into the tenant namespace, driven
  by a Cozystack CR the tenant owns.
- Reuse existing machinery: two-PackageSource layout, `ApplicationDefinition`,
  `dependsOn` ordering, bundle toggles, platform migrations, Cilium Gateway API
  for HTTP exposure. No new platform primitive.
- Delivery is additive: with the app disabled, rendering is identical to `main`.

### Non-goals

- **A source-to-image / build service.** Images in, functions out.
- **A bespoke Cozystack FaaS runtime.** We wrap upstream KEDA and Knative.
- **Replacing the management ingress or CNI.**
- **Re-specifying untrusted-code isolation.** That is #17's job; this proposal
  only *references* ComputePlane as the recommended placement (Security).

## Design

### 1. Two engines, same managed-app pattern

| Component | Trust | Namespace |
| --- | --- | --- |
| KEDA operator / Knative controller | trusted platform | own `cozy-keda` / `cozy-knative` (cluster-wide) |
| Function pods / scaled workers + native CRs | tenant | tenant namespace |
| Tenant `Function` / `Worker` CR | tenant config | tenant namespace |

KEDA and Knative are complementary catalog entries, not competitors: different
workload shapes (event worker vs HTTP endpoint).

### 2. Tenant CRs

- **`Function`** (Knative) — a near 1:1 wrapper over a Knative `Service`. Values:
  image, `minScale`/`maxScale`, concurrency, env, resources. Renders a Knative
  `Service` into the tenant namespace.
- **`Worker`** (KEDA; name in Open questions) — event-driven. The wrapper is
  thicker because KEDA only *scales* an existing workload, so the chart assembles
  `Deployment` + `Service` + `ScaledObject`. Values: image, trigger
  (queue/Kafka/cron/Prometheus), `minReplicaCount` (0 for scale-to-zero),
  `maxReplicaCount`, env, resources.

Each is an `ApplicationDefinition` (`function-rd` / `keda-rd`), converted to a
`HelmRelease` rendering into the tenant namespace — the same path as `Postgres`.

### 3. Package layout (mirror Postgres)

Two PackageSources per engine:

- `cozystack.keda-operator` / `cozystack.knative-controller` — the engine;
  `dependsOn: [networking]` (+ `cert-manager` where a webhook needs it); installs
  into its own `cozy-*` namespace.
- `cozystack.keda-application` / `cozystack.function-application` — bundles the
  tenant CR chart + `*-rd` as components; `dependsOn: [networking, <engine>,
  cozystack.cozystack-engine]`. Only the `*-application` source depends on
  `cozystack-engine` (it ships the `ApplicationDefinition` CRD); the engine source
  does not.

Guarded by a `bundles.*.enabled` toggle in `bundles/paas.yaml`.

### 4. Networking and external exposure

Function workloads run in the tenant namespace on management, so the Knative
request path uses the existing Cilium 1.19.5 Gateway API — no new gateway. The
two historically-known blockers for Knative over Cilium Gateway API are **fixed
in 1.19.5** (the shipped version):

- Header injection for revision routing (`RequestHeaderModifier` on
  `backendRefs`) — cilium/cilium#30090 (merged 2024-01-30).
- KIngress readiness: Cilium used to report a dummy gateway status
  `192.192.192.192:9999`, so the ingress probe hung `Uninitialized`
  (knative-extensions/net-gateway-api#817, closed *not-planned* on the Knative
  side). Fixed **in Cilium** via community PRs cilium/cilium#46237 (+ #46242 /
  #46296 / #46127), backported through #46400, **shipped in v1.19.5** (release
  notes confirm; absent in v1.19.4).

Caveats: Cilium is not in Knative's conformance suite (net-gateway-api#553, open)
and 1.19.5 is fresh, so this needs a confirmation spike (Testing). If it
regresses, the fallback is a tested gateway (Envoy Gateway / Contour) as a new
component with explicit `GatewayClass` / MetalLB-IP ownership vs Cilium Gateway
API + ingress-nginx (Alternatives). Function URLs are exposed per #29; KEDA
event workers need no external exposure.

### 5. Wrapper thickness asymmetry

The one real difference between engines: Knative's `Service` is already
high-level, so `Function` is a thin wrapper. KEDA does not deploy a workload, so
`Worker` templates `Deployment` + `Service` + `ScaledObject` together. Both are
standard Helm templating.

## User-facing changes

- New marketplace entries under a **Serverless** dashboard category: `Function`
  (Knative) and `Worker` (KEDA).
- A tenant creates `Function` / `Worker` CRs in its namespace; the dashboard shows
  the function URL (via the exposed-services model, like other managed apps) and
  the worker's trigger/scale status.
- No CLI changes in this proposal.

## Upgrade and rollback compatibility

- **Backward compatible:** with the apps disabled, no serverless objects render —
  identical to `main`; helm-unittest covers the 0-document case.
- **Registration on existing clusters:** a numbered migration registers the new
  PackageSources — migration `50`, bump `targetVersion` to 51.
- **Enable/disable:** flipping the bundle toggle adds/removes the engine and the
  tenant CRs' `HelmRelease`s. Standard HR lifecycle; no controller cooperation
  beyond Flux.
- **Rollback:** disabling removes both; tenant CRs are garbage-collected.

## Security

The security-relevant fact is that a function or worker runs **arbitrary
tenant-supplied code** — unlike Postgres, which runs a fixed image the tenant
cannot execute code inside. Two layers:

- **Default placement (tenant namespace).** Like every other managed app, the
  workload runs in the tenant namespace on management nodes. This inherits the
  tenant's existing Cilium network policy, RBAC and quotas, but the pods share the
  management host kernel — the same exposure any tenant pod has today.
- **Recommended hardening (ComputePlane placement, #17).** Because the workload is
  arbitrary code, an operator offering serverless to untrusted tenants **should**
  place these workloads on a [`compute-plane`](../compute-plane): a VM-isolated
  managed cluster where a container-escape CVE is contained to a disposable VM
  rather than a management node. This is #17's generic placement mechanism applied
  to serverless, not a serverless-specific boundary. Whether ComputePlane
  placement is the default for the serverless apps, or an operator opt-in, is an
  Open question.
- **Control plane trust.** The KEDA operator / Knative controller are trusted
  platform code; running them under the operator pattern (own namespace,
  cluster-wide) is correct. The untrusted part is only the tenant's workload,
  which the placement layer addresses.
- **Tenant-supplied inputs.** The tenant supplies image, env, scaling bounds via
  the CR `openAPISchema`; no tenant input reaches a management control-plane
  component beyond conversion to a `HelmRelease`.

## Failure and edge cases

- **Invalid CR input** (bad trigger, image) → chart validation rejects; Flux
  surfaces the error on the CR status.
- **Migration runs twice** → idempotent PackageSource registration; no-op second
  run.
- **Knative KIngress does not reach `Ready` on Cilium** (the old #817 failure) →
  must not recur on 1.19.5; asserted by the spike. If it does, fall back to a
  tested gateway (Alternatives).
- **Scale-to-zero cold start** → first request after idle pays pod cold-start;
  `minScale >= 1` for latency-sensitive functions. On ComputePlane placement with
  scale-to-zero node pools, add VM boot time — keep a warm node pool.
- **Future Cilium bump regresses the untested Knative path** → pin/track Cilium
  version against Knative version.

## Testing

- **helm-unittest** per engine's application chart: disabled baseline (0 docs);
  enabled (correct Knative `Service` / KEDA `Deployment`+`Service`+`ScaledObject`
  in the tenant namespace; correct `spec.release.prefix`).
- **Knative-on-Cilium spike** (gates the Knative release): install Knative +
  `net-gateway-api` on management Cilium 1.19.5, deploy a hello-world `Service`,
  assert it reaches **`Ready`** (not just that `curl` works), regression-check the
  `192.192.192.192` / `Uninitialized` failure mode is gone; confirm scale-to-zero,
  cold start, and weighted traffic split across revisions.
- **bats e2e** under `hack/e2e-apps/`:
  - `function-knative.bats`: create a `Function`, assert `Ready`, URL serves,
    scales to zero, cold-starts, traffic split.
  - `worker-keda.bats`: create a `Worker`, publish to its trigger, assert
    scale-from-zero and back-to-zero.

## Rollout

1. **Release N — KEDA.** `keda-operator` + `keda-application` + `keda-rd`. Event
   -driven scale-to-zero; no gateway dependency; simplest, unblocked. Toggle
   defaults off.
2. **Release N+1 — Knative.** `knative-controller` + `function-application` +
   `function-rd`. HTTP FaaS over Cilium Gateway API, gated on the spike.
3. **Orthogonal:** ComputePlane placement (#17) for untrusted isolation, once #17
   lands; does not block the serverless apps themselves.

## Open questions

1. **ComputePlane placement default.** For the serverless apps specifically,
   should untrusted workloads default to ComputePlane placement (#17), or is that
   an operator opt-in with tenant-namespace as the default? Depends on the target
   tenant trust model.
2. **Tenant CR names.** `Function` reads well for Knative. The KEDA event worker
   is not HTTP, so `Function` misleads — `Worker`? `EventConsumer`? `ScaledApp`?
3. **Single "Serverless" dashboard category** grouping both, or separate entries?
4. **Cold-start budget** for scale-to-zero (and, under ComputePlane placement,
   VM-worker boot) before latency is unacceptable for FaaS.
5. **Ship both in one release train, or KEDA now and Knative after the spike?**

## Alternatives considered

**Knative over Kourier / Istio (its own gateway).** Bundles a second gateway
alongside Cilium Gateway API + ingress-nginx + MetalLB, competing for external
traffic and LoadBalancer IPs. Highest infra redundancy. Rejected; the existing
Cilium 1.19.5 path (blockers fixed) is preferred.

**Knative over Envoy Gateway / Contour.** A Gateway API implementation Knative
explicitly tests, but a new gateway component with the same coexistence cost as
above. Kept only as the fallback if the Cilium spike surfaces residual issues;
note the existing Cilium `envoy.enabled` is Cilium's embedded L7 proxy, **not**
Envoy Gateway — no reuse, it is a separate install.

**OpenFaaS / Fission instead of Knative.** Self-contained FaaS with their own
gateway and function model; fewer Kubernetes-native ties but a non-native
abstraction and their own gateway to coexist with. Documented alternative, not
chosen.

**KEDA http-add-on as the FaaS layer (instead of Knative).** HTTP scale-to-zero
without Knative, but reintroduces its own interceptor proxy and lacks revisions /
traffic splitting. Viable as a lighter FaaS if Knative proves too heavy; noted for
the spike.

**Untrusted function pods with no isolation option.** Rejected. Because functions
run arbitrary code, the proposal must at least *reference* a placement path for
untrusted isolation (#17) even though it does not mandate it — otherwise operators
serving untrusted tenants have no safe answer.

---

<!--
Structure per design-proposals/template.md. References compute-plane (PR #17)
for optional untrusted-workload placement.
-->
