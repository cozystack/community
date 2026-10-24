# 0004. GPUNode health is what kubelet lists as allocatable, and the node reports its GPU resource names and per-function counts

- **Number:** `0004`
- **Date:** `2026-10-20`
- **Status:** Accepted
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#99`](https://github.com/cozystack/community/pull/99)
- **Implemented in:** [`cozystack/cozystack#4998`](https://github.com/cozystack/cozystack/pull/4998) (the fields), [`cozystack/cozystack#5013`](https://github.com/cozystack/cozystack/pull/5013) (the node agent)

## Context

Section 1 has the node agent write `GPUNode` from sysfs and kubelet's pod-resources API, with a `healthy` flag per PCI function, and section 2 decides lost capacity from devices the agent reports missing or unhealthy. Section 3 adds every resource name a GPU device plugin advertises to the GPU resource names, "as the agent reports it". Building the agent showed three gaps in that picture.

The pod-resources API carries no device health. It lists the allocatable devices per resource name and the devices each pod holds, and kubelet lists as allocatable only the devices their plugin reports healthy.

One PCI function is not always one unit. An mdev parent serves several plugin devices, so counting one healthy unit per function would make a node with more than one held mdev device look as if it had lost capacity.

Not every GPU device ID maps to a PCI function. The GPU Operator's device plugin of the `container` variant reports NVML UUIDs, `GPU-…` or `MIG-…`, which sysfs cannot resolve, so the agent has no function to put such a GPU under, and section 3 still needs its resource name, such as `nvidia.com/gpu`.

## Decision

- A function is healthy when every plugin device on it is allocatable, and unhealthy when a pod that has not finished holds one of its devices that is not. A function no plugin device maps to has no health.
- Each function reports `allocatable`, the number of its plugin devices kubelet lists as allocatable: 1 for a VF or a GPU passed through whole, N for an mdev parent. The controller sums it over healthy functions, and a node has lost capacity for a resource name when it holds more units than that sum, or when a function is unhealthy.
- `GPUNode.status.resourceNames` lists every resource name on the node whose devices are GPUs, including names whose device IDs are NVML UUIDs the agent cannot place under a function. The names join the cluster's GPU resource names of section 3.
- A function serving several pods, such as an mdev parent, names one of them in `usedBy`, the smallest by namespace and name, so the report is stable.

## Why not the alternatives

- **Read health from each device plugin's socket.** The agent would talk to every plugin the host layer runs, while kubelet already folds their health into what it lists as allocatable, and kubelet's view is the one the scheduler acts on. A judgement call, not a hard limit.
- **Count one unit per function.** Wrong for mdev parents, as above, and it would fire lost capacity on nodes that have lost nothing.
- **Leave GPUs the agent cannot place out of the report.** Section 3 then misses `nvidia.com/gpu`, so a VMI or a pod that names it would not count as carrying a GPU entry.

## Consequences

- An unhealthy device that no pod holds disappears from the allocatable list and looks the same as a missing one. Both lower `allocatable`, and both become lost capacity after the grace period once the node holds more units than remain.
- `usedBy` traces only one pod per function. Section 1's "ownership is exact for running GPUs" holds per function for VFs and whole GPUs, and for an mdev parent only through the claims' own `status.attachedTo`.
- The agent does not fill `profile` yet; writing and reading VF profiles is host-layer work (Rollout, step 4).

## Revisit if

kubelet's pod-resources API starts reporting device health, or a device plugin that Cozystack ships reports IDs the agent can neither resolve through sysfs nor recognise as NVML UUIDs.
