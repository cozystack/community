# 0001. ComputePlane ships as an operator-owned module, not as preset fields on `kind: Kubernetes`

- **Number:** `0001`
- **Date:** `2026-07-18`
- **Status:** Accepted
- **Deciders:** `@kvaps, @myasnikovdaniil`
- **Proposal:** [`design-proposals/compute-plane/README.md`](../design-proposals/compute-plane/README.md)
- **Implemented in:** not yet — see the proposal's Rollout section

## Context

The first ComputePlane revision ([#17](https://github.com/cozystack/community/pull/17), merged 2026-06-30) delivered a hardened, operator-controlled Kubernetes cluster for running code-executing catalog apps — notebooks, workflow code nodes, plugin systems — behind a per-VM kernel boundary instead of as shared-kernel pods on the management cluster. It was shaped as a tenant module selected by a single string (`computePlane: "<profile>"`), with the cluster's HelmRelease rendered inline in `apps/tenant/templates`.

Two reviews converged on the same objection ([#26](https://github.com/cozystack/community/issues/26), and Timofei Larkin on [#17](https://github.com/cozystack/community/pull/17)): the cluster a ComputePlane runs on *is* an ordinary managed `kind: Kubernetes` — Kamaji control plane, KubeVirt-VM workers, operator-held kubeconfig — so ComputePlane should not become a distinct kind with a parallel cluster implementation, reconcile loop and RBAC surface.

Revision [#27](https://github.com/cozystack/community/pull/27) answered that by putting the posture directly on the existing app as two orthogonal user-facing preset fields: `isolationProfile` (`standard` | `sandbox` | `cluster-meshed`) × `componentProfile` (`minimal` | `standard-addons` | `gpu`). A ComputePlane would then be nothing more than `kind: Kubernetes` with `isolationProfile: sandbox`.

The isolation mechanism itself was never in question at any point — one-way remote Flux apply via `HelmRelease.spec.kubeConfig.secretRef`, untrusted code behind a disposable guest kernel, single-tenant, scoped egress. Only the delivery surface was contested.

## Decision

ComputePlane ships as a Cozystack-owned **tenant module** (`packages/extra/computeplane`) that provisions a hardened `kind: Kubernetes` by wrapping the unchanged `apps/kubernetes` chart with operator-fixed values, sourced through the existing PackageSource source-only-chart mechanism that NATS and SeaweedFS already use.

Because a tenant module is registered through an `ApplicationDefinition`, ComputePlane does get its own `apps.cozystack.io` kind, with its own API endpoint and input schema — the same CRD-free way every managed service is registered. So the accurate claim is not "no new kind": it is **no new CRD, no new controller or reconcile path, and no new fields on `kind: Kubernetes`**. Catalog apps route onto the cluster through a `placement: ManagementPlane | ComputePlane` field on `ApplicationDefinition`.

## Why not the alternatives

- **User-facing preset fields on `kind: Kubernetes` ([#27](https://github.com/cozystack/community/pull/27)).** Putting the posture on the app object turns an operator's choice into tenant-editable API surface. The entire value of a sandbox is that the tenant *cannot* weaken it, and any field that configures the hardening is a field that can relax it. It also loads a hardened posture onto the one `ApplicationDefinition` that has to stay tenant-generic.
- **A distinct, heavyweight `kind: ComputePlane` with its own cluster implementation ([#17](https://github.com/cozystack/community/pull/17) as merged).** Duplicates the cluster reconcile and RBAC path, which was the substance of the #26 / #17-review objection. Note this decision does still register a `ComputePlane` kind — it departs from #26's literal "no new kind at all" — but as a thin wrapper over the unchanged app, which is what the objection was actually about.
- **The single-string `computePlane:` module rendered inline in `apps/tenant/templates` (the first revision's implementation).** Right delivery shape, wrong packaging: the cluster HelmRelease was inlined into the tenant chart instead of shipping as a first-class chart with its own PackageSource, the way `extra/seaweedfs` does.
- **Hardening containers in the tenant namespace instead.** Rejected as the primary boundary: hardening does not make container isolation multi-tenant, and it breaks the apps in scope.
- **gVisor or a sandboxed runtime as the primary boundary.** Rejected for incomplete syscall coverage and no blast-radius containment on a kernel panic. Still valid as an *inner* layer for per-task sandboxes inside the cluster, and tracked as a future runtime option rather than dismissed.

## Consequences

- The hardening is tamper-proof by construction. The cluster's values live entirely in an operator-owned chart and the tenant holds no admin kubeconfig, so "withhold admin" and "own the configuration" become one fact rather than two features to keep in sync.
- `kind: Kubernetes` gains no fields and stays tenant-generic. Node-provisioning and addon changes to the app apply to ComputePlane transparently.
- The cost is composability: several differently-hardened postures per tenant now require separate module variants rather than a combination of two fields. The `placement: <named cluster>` path that would give a tenant N sandbox clusters is deferred.
- The `computeplane` release name is load-bearing (proposal, Design §1). Any later move of the chart between directories must preserve it.
- [#39](https://github.com/cozystack/community/pull/39) (folding `extra` into `apps` as declarative `ApplicationDefinition` capabilities) composes with this rather than conflicting: ComputePlane becomes a directory move plus `visibility: module` and `cardinality: {scope: tenant, max: 1}`, and the two-piece structure carries over unchanged.

## Revisit if

[#39](https://github.com/cozystack/community/pull/39) lands and changes how module kinds are registered, or a concrete need appears for several simultaneous, differently-hardened compute clusters per tenant — the case the single-module shape does not serve and the deferred `placement: <named cluster>` path would.
