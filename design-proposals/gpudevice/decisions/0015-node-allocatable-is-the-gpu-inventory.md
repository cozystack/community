# 0015. Node allocatable is the GPU inventory, and there is no `GPUNode`

- **Number:** `0015`
- **Date:** `2026-10-27`
- **Status:** Accepted
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#99`](https://github.com/cozystack/community/pull/99#issuecomment-6445311702)
- **Implemented in:** not yet

## Context

`GPUNode` was a cluster-scoped inventory kind per GPU node, written by a node agent from sysfs and kubelet's pod-resources API ([0004](./0004-gpunode-health-is-kubelet-allocatability.md)). It listed each PCI function with its health, an `allocatable` count, the pod using it, and the node's GPU resource names. Its readers were the binder, which judged lost capacity from it and capped its sums at kubelet's node allocatable anyway; the GPU resource names, which joined the name set of the raw-name policy; and a hoster tracing a physical device to a tenant.

@lllamnyp asked in [review](https://github.com/cozystack/community/pull/99#issuecomment-6445311702) whether a hoster-only inventory needs a kind when metrics, node allocatable or DRA's `ResourceSlice` can serve it. Step 1 no longer has a binder ([0014](./0014-step-1-names-a-class-and-holds-nothing.md)).

## Decision

There is no `GPUNode` and no inventory agent. A class's capacity, `free` and `maxFreePerNode` come from the `Node` objects' allocatable count of the class's resource and the requests of pods on those nodes. In step 2 a node has lost capacity for a class when its allocatable count of the class's resource stays below the units held there for longer than the grace period, or when it has been `NotReady` that long. GPU resource names are the union of the hoster's `gpu.resourceNames`, the classes' resource names and the GPU entries of `permittedHostDevices`. Use per tenant comes from the `Workload` records, per virt-launcher pod and resource name. The node agent of the host layer stays, writes VF profiles and restarts the device plugin on its own node.

## Why not the alternatives

- **Keep `GPUNode` for step 2's lost capacity.** 0004 already capped the agent's sum at kubelet's allocatable, and kubelet lists only the devices its plugin reports healthy, so the agent's health adds a per-function view of the same signal, not a different one.
- **Keep `GPUNode` for tracing a physical device to a tenant.** With a device plugin, kubelet picks the device when the pod starts, and the pod object does not say which one. The agent's `usedBy` was exact per VF or whole GPU but named only one pod per mdev parent, and a hoster replacing a failed card drains the node either way.
- **Expose the same data as metrics only.** It would still need the agent on every GPU node, privileged against kubelet's pod-resources socket, for a view no part of the design reads.

## Consequences

- A hoster cannot see from the API which VF or PCI function a VM uses, only which VM, class and count, and the node through the VM's launcher pod; the guest and the node's own tools show the device.
- A GPU name that only pods request, such as `nvidia.com/gpu` of the `container` variant, is a GPU resource name only if the hoster lists it in `gpu.resourceNames`.
- One privileged pod-resources reader fewer runs on every GPU node.

## Revisit if

The design needs a per-device view, for example exact-device allocation, NVLink sets or on-demand slicing; DRA's `ResourceSlice` is the first candidate to provide it.
