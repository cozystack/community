# 0016. Holding a GPU for a stopped VM is step 2, and stays GPU-specific until DRA can carry it

- **Number:** `0016`
- **Date:** `2026-10-27`
- **Status:** Accepted
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#99`](https://github.com/cozystack/community/pull/99#pullrequestreview-5653244822)
- **Implemented in:** not yet

## Context

The binder, moves, `onCordon`, `Lost` and rebind, the gather at VMI start, the launcher pod gate and pin, and `forbidden` all exist for one problem of six, keeping a GPU for a stopped VM, and for the capacity half of another, refusing an order that does not fit. @kvaps asked in [review](https://github.com/cozystack/community/pull/99#pullrequestreview-5616164700) to split holding from the rest, and later [accepted one proposal](https://github.com/cozystack/community/pull/99#pullrequestreview-5653244822) if the text says what exists in step 1 and gives step 2 its own section and record. @lllamnyp argued in [review](https://github.com/cozystack/community/pull/99#issuecomment-6445311702) that keeping capacity for a stopped VM is not specific to GPUs, since a stopped VM releases its CPU and memory too, and that the hold should be proposed either as a general reservation or through DRA persistent claims, KubeVirt [VEP 344](https://github.com/kubevirt/enhancements/issues/344).

When this was decided, VEP 344 was open and its implementation, kubevirt/kubevirt#17957, unmerged; the NVIDIA DRA driver served no vGPU (kubernetes-sigs/dra-driver-nvidia-gpu#1402); and the vGPU VF reached KubeVirt through a device plugin, the one of the host layer with NVIDIA/kubevirt-gpu-device-plugin#192.

## Decision

Holding is step 2 of the proposal, in a section of its own, and none of its kinds, fields or phases enter the API before it: `GPUClaim`, `onCordon` and the class finalizer, `claim` and `claimSelector` entries, the binder, moves, `Lost` and rebind, the gather, the launcher pod gate and pin, template claims for class entries, the claim order webhook and tamper policy, the usage records of held claims, and `gpu.rawResourceNames: forbidden`.

Holding stays GPU-specific and does not become a generic reservation. CPU and memory are interchangeable across nodes, so a reservation of them is a count the scheduler can meet on any node with room. A GPU hold is a whole unit on a specific node that does not migrate, since KubeVirt cannot live-migrate a VM with a passthrough GPU or an SR-IOV VF, and that is the reason for the binder, `Lost`, the gather and `onCordon`. A DRA path does not exist today: VEP 344 is open and unimplemented, and the vGPU VF is handed out by a device plugin, not by a DRA driver.

## Why not the alternatives

- **Holding in step 1.** Step 1 answers five of the six problems without a hold, the third only for budgets, and the hold puts the most expensive part of the design into a v1alpha1 tenant API before any of it is used.
- **A generic reservation for stopped VMs.** For CPU and memory a per-tenant count would be enough, and none of the node binding would be needed; for GPUs a count without a node fails as soon as free units are spread one per node, as the cluster-wide reservation alternative of the proposal shows. One mechanism would carry the GPU machinery for resources that do not need it.
- **Wait for DRA.** No date can be given for VEP 344 or for vGPU in the NVIDIA DRA driver, and hosters sell vGPU first.

## Consequences

- Step 1 ships without a tenant object, and a GPU VM's next start can fail for a GPU someone else took, as it can today.
- Step 2's `GPUClaim` maps one to one onto a `ResourceClaim` for `dra` classes (section 10), so the tenant API does not change when holding moves to DRA.
- A cluster that keeps raw names allowed has no hard hold guarantee, also in step 2.

## Revisit if

VEP 344 is implemented and a DRA driver hands out vGPU VFs; then holding moves to DRA.
