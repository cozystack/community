# 0001. ComputePlane ships as an operator-owned module, not as preset fields on `kind: Kubernetes`

- **Number:** `0001`
- **Date:** `2026-07-18`
- **Status:** Accepted
- **Deciders:** `@kvaps, @lllamnyp`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#33`](https://github.com/cozystack/community/pull/33)
- **Implemented in:** [`cozystack/cozystack#3280`](https://github.com/cozystack/cozystack/pull/3280)

## Context

The first ComputePlane revision ([#17](https://github.com/cozystack/community/pull/17), merged 2026-06-30) delivered a hardened, operator-controlled Kubernetes cluster for running code-executing catalog apps — notebooks, workflow code nodes, plugin systems — behind a per-VM kernel boundary instead of as shared-kernel pods on the management cluster. It was shaped as a tenant module selected by a single string (`computePlane: "<profile>"`), with the cluster's HelmRelease rendered inline in `apps/tenant/templates`.

[#26](https://github.com/cozystack/community/issues/26), filed by @myasnikovdaniil, reopened the delivery question and forced the revision: the cluster a ComputePlane runs on *is* an ordinary managed `kind: Kubernetes` — Kamaji control plane, KubeVirt-VM workers, operator-held kubeconfig — so ComputePlane should not become a distinct kind with a parallel cluster implementation, reconcile loop and RBAC surface.

@lllamnyp's [review on #17](https://github.com/cozystack/community/pull/17#pullrequestreview-4593662101) was an approval, and it argued something different that turned out to be load-bearing here: that "hidden from the tenant" had been conflated with the security boundary, and the real justification for withholding cluster access is **tamper-resistance** — withhold admin, not visibility. #26 quoted that approval in support of its own case, but the "not a distinct kind" argument is #26's alone.

Revision [#27](https://github.com/cozystack/community/pull/27) answered #26 by putting the posture directly on the existing app as two orthogonal user-facing preset fields: `isolationProfile` (`standard` | `sandbox` | `cluster-meshed`) × `componentProfile` (`minimal` | `standard-addons` | `gpu`). A ComputePlane would then be nothing more than `kind: Kubernetes` with `isolationProfile: sandbox`. It was closed unmerged on 2026-07-13, superseded by [#33](https://github.com/cozystack/community/pull/33) the same day.

The isolation mechanism itself was never in question at any point — one-way remote Flux apply via `HelmRelease.spec.kubeConfig.secretRef`, untrusted code behind a disposable guest kernel, single-tenant, scoped egress. Only the delivery surface was contested.

## Decision

ComputePlane ships as a Cozystack-owned **tenant module** (`packages/extra/computeplane`) that provisions a hardened `kind: Kubernetes` by wrapping the unchanged `apps/kubernetes` chart with operator-fixed values, sourced through the existing PackageSource source-only-chart mechanism that NATS and SeaweedFS already use.

Because a tenant module is registered through an `ApplicationDefinition`, ComputePlane does get its own `apps.cozystack.io` kind, with its own API endpoint and input schema — the same CRD-free way every managed service is registered. So the accurate claim is not "no new kind": it is **no new CRD, no new controller or reconcile path, and no new fields on `kind: Kubernetes`**. Catalog apps route onto the cluster through a `placement: ManagementPlane | ComputePlane` field on `ApplicationDefinition`.

## Why not the alternatives

- **User-facing preset fields on `kind: Kubernetes`** ([#27](https://github.com/cozystack/community/pull/27), closed unmerged). Putting the posture on the app object turns an operator's choice into tenant-editable API surface, and the entire value of a sandbox is that the tenant *cannot* weaken it. The durable reason is structural, not a matter of how the chart is packaged today: capabilities and schemas are per-kind, so two postures over one chart need two `ApplicationDefinition`s; `ApplicationDefinitionSpec` ([`api/v1alpha1/applicationdefinitions_types.go`](https://github.com/cozystack/cozystack/blob/main/api/v1alpha1/applicationdefinitions_types.go)) has no fixed-values facility, and the conversion makes the tenant's spec *become* the Helm values (`Values: app.Spec`, [`pkg/registry/apps/application/rest.go`](https://github.com/cozystack/cozystack/blob/main/pkg/registry/apps/application/rest.go)), so operator-fixed values have nowhere to live but a chart. A narrow `openAPISchema` does not substitute: a field you can default is a field the tenant can override, and a field absent from the schema receives the chart's own unhardened default. "Hardened and unreachable" is not expressible in a structural schema.
- **A distinct, heavyweight `kind: ComputePlane` with its own cluster implementation** ([#17](https://github.com/cozystack/community/pull/17) as merged). Duplicates the cluster reconcile and RBAC path, which was the substance of [#26](https://github.com/cozystack/community/issues/26). Note this decision does still register a `ComputePlane` kind — it departs from #26's literal "no new kind at all" — but as a thin wrapper over the unchanged app, which is what the objection was actually about.
- **The single-string `computePlane:` module rendered inline in `apps/tenant/templates`** (the first revision's implementation). Right delivery shape, wrong packaging: the cluster HelmRelease was inlined into the tenant chart instead of shipping as a first-class chart with its own PackageSource, the way `extra/seaweedfs` does.
- **Hardening containers in the tenant namespace instead.** Rejected as the primary boundary: hardening does not make container isolation multi-tenant, and it breaks the apps in scope.
- **gVisor or a sandboxed runtime as the primary boundary.** Rejected for incomplete syscall coverage and no blast-radius containment on a kernel panic. Still valid as an *inner* layer for per-task sandboxes inside the cluster, and tracked as a future runtime option rather than dismissed.

## Consequences

- The hardening is tamper-proof by construction — the argument from the [#17 review](https://github.com/cozystack/community/pull/17#pullrequestreview-4593662101). The cluster's values live entirely in an operator-owned chart and the tenant holds no admin kubeconfig, so "withhold admin" and "own the configuration" become one fact rather than two features to keep in sync.
- `kind: Kubernetes` gains no fields and stays tenant-generic. Node-provisioning and addon changes to the app apply to ComputePlane transparently.
- The cost is composability: several differently-hardened postures per tenant now require separate module variants rather than a combination of two fields. The `placement: <named cluster>` path that would give a tenant N sandbox clusters is deferred.
- The `computeplane` release name is load-bearing. It is enforced in code rather than restated here: [`templates/check-release-name.yaml`](https://github.com/cozystack/cozystack/blob/main/packages/extra/computeplane/templates/check-release-name.yaml) fails the render on a non-canonical name, and [`tests/release_name_test.yaml`](https://github.com/cozystack/cozystack/blob/main/packages/extra/computeplane/tests/release_name_test.yaml) pins it, with the mechanism in the suite comment.
- **The ordering constraint against [#39](https://github.com/cozystack/community/pull/39) is now live.** #33 was asked to co-land with #39 or land after it, because once tenants can set `computeplane` the release-name invariant stops being a markdown disagreement and becomes a migration of live Kamaji clusters holding tenant data. #3280 merged on 2026-07-29 and #39 is still open, so that migration is now a real cost carried by whoever lands #39, rather than a change that purely composes with this one.

## Revisit if

[#39](https://github.com/cozystack/community/pull/39) lands and changes how module kinds are registered, or a concrete need appears for several simultaneous, differently-hardened compute clusters per tenant — the case the single-module shape does not serve and the deferred `placement: <named cluster>` path would.
