# 0013. The GPU kinds live in `cozystack.io`, with no group of their own

- **Number:** `0013`
- **Date:** `2026-10-27`
- **Status:** Accepted
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#99`](https://github.com/cozystack/community/pull/99#issuecomment-6445311702)
- **Implemented in:** not yet

## Context

The proposal introduced an API group, `gpu.cozystack.io`, for `GPUClass`, `GPUClaim` and `GPUNode`, and used the group name as the prefix of everything around them: the tenant quota keys `gpu.cozystack.io/<class>` and `gpu.cozystack.io/total`, the node labels, the finalizers, the labels and annotations on VMIs and pods, and the scheduling gate. It gave no reason for a group of its own. cozystack/cozystack#4998, which would create it, was open.

@lllamnyp pointed out in review that those keys are part of the API contract even though the API server does not enforce them, so the group decides their names too, and that upstream handled less interchangeable devices without a device-type group: distinct offers get distinct resource names, and where counts were not enough, the new group is named after the mechanism, `resource.k8s.io`, with class-scoped quota keys `<class>.deviceclass.resource.k8s.io/devices`. He also pointed at `network.cozystack.io`, which cozystack/cozystack#3081 added on 2026-06-30 and cozystack/cozystack#3218 removed on 2026-07-16, as the case not to repeat with tenant quota keys involved.

The `cozystack.io` group already held hoster-side kinds, `SchedulingClass` and `Package`, and tenant-namespace kinds, `Workload` and `WorkloadMonitor`.

## Decision

There is no `gpu.cozystack.io` group. `GPUClass` (step 1) and `GPUClaim` (step 2) are kinds of `cozystack.io/v1alpha1`, and there is no `GPUNode` ([0015](./0015-node-allocatable-is-the-gpu-inventory.md)). The budget of one class is `<class>.gpuclass.cozystack.io/devices`, after DRA's form, and the budget over every class is `gpuclass.cozystack.io/devices`. Labels, annotations, finalizers and the scheduling gate are `cozystack.io/` keys with a `gpu` name, such as `cozystack.io/gpu-reference`, `cozystack.io/gpuclaim-protection` and the gate `cozystack.io/gpuclaims`; the vGPU entitlement stays on the existing `namespace.cozystack.io/` prefix of module labels. Section 1 of the proposal lists every name.

`cozystack.io` then holds the hoster-side kinds (`SchedulingClass`, `Package`, `GPUClass`) and the tenant-namespace kinds the platform writes or a tenant admin owns (`Workload`, `WorkloadMonitor`, and in step 2 `GPUClaim`).

## Why not the alternatives

- **Keep `gpu.cozystack.io`.** Nothing in the design needs a group of its own: no separate versioning, no separate API server, no RBAC that a group boundary would make simpler. A CRD cannot move to another group in place, so a wrong group would cost every tenant's quota keys and objects a migration, as the `network.cozystack.io` removal did on a smaller scale.
- **Name the keys after the group, `cozystack.io/gpu-<class>`.** A flat key cannot be told apart from a label-style name without parsing, and it does not follow the one upstream form readers already know. The DRA form puts the class in the DNS prefix, so a key without the prefix naturally means every class and no class name has to be reserved for a total.
- **Put the catalog in platform values instead of a kind.** Values can describe offers, but tenants and their GitOps tools could not read them, and nothing could carry a status with free units.

## Consequences

- cozystack/cozystack#4998 creates no new group; its CRDs move to `cozystack.io`, and every `gpu.cozystack.io` string in the GPU code is renamed before anything merges.
- The class name `total` is no longer reserved.
- A future GPU kind goes into `cozystack.io` too, unless a requirement appears that only a separate group can carry; then it is written down in a record first.

## Revisit if

GPU offers move to DRA `DeviceClass` objects, so the catalog and its quota keys become DRA's own, or a GPU kind needs versioning or serving apart from the rest of `cozystack.io`.
