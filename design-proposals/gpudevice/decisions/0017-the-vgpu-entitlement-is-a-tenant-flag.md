# 0017. The vGPU entitlement is a Tenant flag stamped on the namespace, not a budget key

- **Number:** `0017`
- **Date:** `2026-10-27`
- **Status:** Accepted
- **Deciders:** `@lexfrei, @kvaps`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#99`](https://github.com/cozystack/community/pull/99#pullrequestreview-5653244822)
- **Implemented in:** not yet

## Context

A vGPU needs a license, counted per unit, and a tenant that may use vGPU at all gets the vGPU guest driver and the license token. The proposal gave the yes-or-no part to a Tenant field, `gpu.vgpu`, which the tenant chart combines with the parent's value and stamps on the namespace as `namespace.cozystack.io/vgpu`. It lived inside the quota section. @kvaps [asked](https://github.com/cozystack/community/pull/99#pullrequestreview-5616164700) whether a `gpu.cozystack.io/vgpu` budget key, 0 by default, would cover it, since it goes down the subtree like the other GPU keys and can cap the license pool. In Cozystack a missing quota key means no limit. He later [agreed](https://github.com/cozystack/community/pull/99#pullrequestreview-5653244822) that the entitlement is a yes or no, not a quota, and that a Tenant field stamped as a namespace label is the pattern of `namespace.cozystack.io/etcd` and the other module labels, and asked for the flag in its own section with a record that says when to revisit it.

The license delivery of this proposal copies the hoster's license Secret into the namespaces of entitled tenants, and uses the same label to decide where (section 6).

## Decision

The vGPU entitlement stays the Tenant field `gpu.vgpu`, off by default, effective only when the parent is entitled, and stamped on the namespace as `namespace.cozystack.io/vgpu`, where only `"true"` means entitled. It has its own section in the proposal, outside the budgets. The VMI webhook refuses a vGPU class entry or claim without it at every start, and in step 2 the claim order webhook refuses a vGPU order without it. Budgets limit how many vGPUs an entitled tenant uses, through the class keys and the key over every class.

## Why not the alternatives

- **A vGPU budget key with 0 by default.** A missing key means no limit for every other key, so a tenant without the key would read as unlimited to anyone who knows the others, and this one key would mean the opposite. A cap on the license pool is a budget over the vGPU classes and can be added as one, apart from the entitlement.
- **Entitlement on the class.** A class is visible to every tenant and names an offer, not who may take it; per-tenant lists on a class would move tenant administration into the hoster's catalog.

## Consequences

- A hoster turns vGPU on from the root tenant down before tenants use vGPU classes.
- A write that names a vGPU class is not refused for the entitlement, only the start; the option source shows the class with a remaining budget of 0.

## Revisit if

The platform stops copying license Secrets into tenant namespaces (License delivery in section 6), for example once a license reaches the guest without a copy per tenant, so the flag guards nothing but the use of vGPU classes and a budget over those classes could replace it.
