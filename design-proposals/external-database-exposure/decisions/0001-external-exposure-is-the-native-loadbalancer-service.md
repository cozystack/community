# 0001. External exposure is the native LoadBalancer Service, not a Cozystack exposure API

- **Number:** `0001`
- **Date:** `2026-07-16`
- **Status:** Accepted
- **Deciders:** `@kvaps, @lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** the argument in [`cozystack/cozystack#3164`](https://github.com/cozystack/cozystack/issues/3164), which `cozystack/cozystack#3218` names as its rationale, settled by merging [`cozystack/cozystack#3218`](https://github.com/cozystack/cozystack/pull/3218)
- **Implemented in:** [`cozystack/cozystack#3218`](https://github.com/cozystack/cozystack/pull/3218)

## Context

Two tenant-facing exposure surfaces were in play at once, one landed and one still pending. The pending one was the structured `expose` proposal ([`cozystack/community#29`](https://github.com/cozystack/community/pull/29)), open at the time and set to replace the chart-level `external` boolean with an additive `expose` list layered on `ServiceExposure` — pendency that [`cozystack/cozystack#3164`](https://github.com/cozystack/cozystack/issues/3164) gives as the reason to settle the question before the proposal extended the exposure half rather than after. The implementation had landed ahead of it: `network.cozystack.io/v1alpha1`, a cluster-scoped `ExposureClass` and a namespaced `ServiceExposure` reconciled by a controller in `cozystack-controller`, merged in [`cozystack/cozystack#3081`](https://github.com/cozystack/cozystack/pull/3081) on 2026-06-30.

@lllamnyp filed that issue the next day, and the objection in it is what forced the call; the removal followed two weeks later. The vendor-neutral LoadBalancer selection the new group offered is a native Kubernetes field, `Service.spec.loadBalancerClass`. And two API groups over one domain — `network.cozystack.io` for how an application is reachable, beside `sdn.cozystack.io` for who may reach it — is a fault unless the split is written down, which for the exposure surface it never was, the group having skipped the design process the policy surface went through.

This proposal was written against that group while it existed. Its section 5 handed both the tenant-facing trigger and the per-release `TLSRoute` to the layer that reconciled `expose` entries into `ServiceExposure` objects.

## Decision

External exposure is the native Kubernetes primitive, with no Cozystack object in front of it. `cozystack/cozystack#3218` removed the `network.cozystack.io` group; the host ingress, its only consumer, went on rendering `type: LoadBalancer` and gained an optional `publishing.loadBalancerClass` to pick the LoadBalancer controller, leaving the `externalIPs` node-IP default path unchanged. The ground given in the removal is that managed-application charts own their Service, so native `type: LoadBalancer` plus `loadBalancerClass` and an admin-provisioned address pool cover external exposure without a dedicated API group and a controller to reconcile it. The group never reached users: `docs/changelogs/v1.6.0.md` records it as introduced and removed inside one cycle and not part of v1.6.0.

## Why not the alternatives

- **Keep `ServiceExposure` as the object charts point at instead of rendering a Service.** This is the kind `cozystack/cozystack#3164` puts its open question to — what it provides that `Service.spec.loadBalancerClass` plus an admin-provisioned pool per class does not — and `cozystack/cozystack#3218` answers it: managed-application charts already render their own Service, so the indirection served one consumer, the host ingress, with no second one in prospect.
- **Keep `ExposureClass` and remove only `ServiceExposure`.** `cozystack/cozystack#3164` grants that this half is defensible on its own, an admin-owned named config object on the StorageClass analogy, and aims its open question elsewhere. The removal took both because the class had no reader left: every reader of the kind lived inside the `serviceexposure` controller that went with it, `Service.spec.loadBalancerClass` names the LoadBalancer controller directly, and the pool behind a class is provisioned by an administrator rather than by an object.
- **Leave both kinds in place and write the missing justification for two API groups.** `cozystack/cozystack#3164`'s second concern is that two tenant-facing networking groups over one domain is a fault unless the split is written down, and that the exposure surface skipped the design process the policy surface went through. A justification can defend a split that buys something; what was left after the first bullet did not.

## Consequences

- Nothing mediates between a chart and its external endpoint, so a design that wants an object in between has to earn it rather than assume it. This proposal's section 5 does not ask for one: the per-release `TLSRoute` is rendered by the release's own chart, the shape `packages/apps/harbor/templates/httproute.yaml` uses for an `HTTPRoute` and `packages/system/cozystack-api/templates/api-tlsroute.yaml` for a `TLSRoute`, and the shared engine listener is a `TenantGateway` field specified in [`cozystack/cozystack#3342`](https://github.com/cozystack/cozystack/pull/3342). That split is derived from this decision in the proposal, not argued in `cozystack/cozystack#3218` — the removal thread does not discuss Gateway routing at all.
- The class stops short of the databases. `publishing.loadBalancerClass` reaches charts as `_cluster.load-balancer-class`, and `packages/extra/ingress/templates/nginx-ingress.yaml` is what reads it, so a managed database's LoadBalancer Service carries no class and lands on the cluster's default LoadBalancer implementation.
- The migration cost fell only on clusters tracking `main` that had set `publishing.exposureClass`: provision an address pool, switch to `publishing.loadBalancerClass`, delete any orphaned `cozystack-<class>` pool by hand. No released cluster was affected.

## Revisit if

A successor to the structured `expose` model lands and needs an object of its own. The question to answer then is what that object does beyond selecting a class and naming a pool — which is what `cozystack/cozystack#3164` asked, and what the removed shape had no answer for.
