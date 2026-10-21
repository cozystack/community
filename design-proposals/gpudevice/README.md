# GPUDevice: GPUs as a tenant resource

- **Title:** `GPUDevice: a GPU catalog, per-tenant GPU reservations for VMs and worker pools, and the host layer under them`
- **Author(s):** `@lexfrei`
- **Date:** `2026-10-14`
- **Status:** Review

## Overview

A tenant who wants a GPU today writes a device-plugin resource name such as `nvidia.com/AD102GL_L40S` into `gpus` of a VM or a worker pool and hopes one is free. The `gpu` option source shows how many of each name the nodes advertise and on which nodes, but nothing says which model, mode or profile a name stands for, nothing holds a GPU while the VM is off, and an order that does not fit is accepted and then sits in `Pending`. A hoster selling GPUs needs the opposite: a catalog of GPU models and modes, GPUs a tenant takes within a quota and keeps until they release them, the same handling for VMs and managed Kubernetes worker pools, and a usage record of who held what and when, which the hoster's external billing system reads.

This proposal adds three kinds. `GPUClass` is the hoster's catalog: one entry per offer, such as a whole H100 in passthrough or an L40S-24C vGPU profile. `GPUDevice` is a tenant's GPU, or a set of GPUs on one node: ordered against a class, bound to a node with free capacity, and attached to a VM by name or to a Kairos worker pool by a label selector. `GPUNode` is the inventory: every physical GPU and VF on a node, its configuration, and which tenant uses it. The proposal also covers the host layer under them, as its own phase: passthrough and vGPU nodes in one cluster, declarative vGPU profiles, and the device plugin that advertises GPUs to KubeVirt.

NVIDIA's vGPU stack changes quickly. Within the last few releases the driver branches, the way vGPUs are exposed (mediated devices or SR-IOV virtual functions), the KubeVirt device plugins and the licensing rules all changed. This proposal describes that stack as it was in October 2026. Expect it to be updated as NVIDIA and KubeVirt move, with a decision record under it for each change of course, and check the versions named here against your own before relying on them.

## Scope and related proposals

- [`kairos-vgpu-workers`](../kairos-vgpu-workers/README.md) gives managed Kubernetes worker pools on Kairos a worker image per GPU mode, with the vGPU guest driver or the data center driver, and delivers the vGPU license token to the workers. [GPU virtual machines](../vgpu-vms/README.md) (`vgpu-vms`) covers VMs with passthrough and vGPU: the hoster adds GPU guest images to the golden image collection through `extraImages`, marked with a `gpu` field. Both are proposed alongside this one and use `GPUDevice` from the start, through `device` entries and `deviceSelector`; the links resolve once they merge.
- [Node fencing](../node-fencing/README.md), proposed alongside this one, adds an optional package that fences a dead node through its BMC and taints it `out-of-service`, so KubeVirt fails its VMIs and section 2 rebinds their `Lost` devices. The link resolves once it merges.
- [IP addresses as a first-class resource](https://github.com/cozystack/community/pull/35) solves the same problem for addresses with a class, a tenant claim and a backend contract.
- [Tenant quotas as reservation limits](https://github.com/cozystack/community/pull/48) makes declared reservations the quota authority. A `GPUDevice` is a reservation in that sense, and section 4 hands GPU budgets over to it only if its gate takes over subtree caps for GPU keys.
- Host-side discovery of vGPU VFs grouped by profile is [NVIDIA/kubevirt-gpu-device-plugin#192](https://github.com/NVIDIA/kubevirt-gpu-device-plugin/pull/192), still open. Section 6 ships it downstream in the host layer, behind a boundary the rest of the design does not cross.

## Decisions

- [0008. The GPU controller gets its node-label and plugin-restart rights with the host layer, not before](./decisions/0008-the-controller-gets-its-host-layer-rights-with-the-host-layer.md)
- [0007. Whoever can write a VM orders its template devices, while held devices stay with tenant admins](./decisions/0007-whoever-can-write-a-vm-orders-its-template-devices.md)
- [0006. A `Stay` pin holds through `NotReady` and taints, and breaks when the node cannot serve the device](./decisions/0006-a-stay-pin-yields-only-to-a-node-that-cannot-serve-the-device.md)
- [0005. The taint rule skips exactly the three taints that mirror a cordon or readiness](./decisions/0005-the-taint-rule-skips-exactly-three-kubernetes-taints.md)
- [0004. GPUNode health is what kubelet lists as allocatable, and the node reports its GPU resource names and per-function counts](./decisions/0004-gpunode-health-is-kubelet-allocatability.md)
- [0003. The usage-monitor label reservation is always on, and it ratchets](./decisions/0003-the-usage-monitor-label-reservation-is-always-on-and-ratchets.md)
- [0002. GPUDevice works only where gpu-operator serves GPUs to VMs, while its finalizers and usage records always run](./decisions/0002-gpudevice-serves-vms-only-where-gpu-operator-serves-gpus-to-vms.md)
- [0001. A device whose class resource name KubeVirt does not permit is refused at VMI admission](./decisions/0001-a-device-outside-permittedhostdevices-is-refused-at-admission.md)

## Context

### What exists

The `cozystack.gpu-operator` package has three variants, selected by `bundles.iaas.gpuOperatorVariant` ([`docs/gpu-vgpu.md`](https://github.com/cozystack/cozystack/blob/main/docs/gpu-vgpu.md)). `default` binds whole GPUs to `vfio-pci` for VMs, `vgpu` runs the NVIDIA vGPU host driver, and `container` serves GPUs to pods. For the two VM variants the platform writes the `HostDevices` feature gate and, when it has entries, a `permittedHostDevices` table into the KubeVirt CR (`packages/core/platform/templates/bundles/iaas.yaml`). Passthrough GPUs are advertised by NVIDIA's sandbox device plugin.

How NVIDIA's vGPU host driver exposes vGPUs depends on the GPU, the host OS and the driver branch. Older setups use mediated devices (mdev). SR-IOV GPUs on current releases, such as Ubuntu 24.04 with the vGPU 17 or newer branch that Cozystack's guide uses, get VFs, and each VF gets a profile when `current_vgpu_type` is written in its sysfs. KubeVirt v1.9, which Cozystack bundles, advertises such VFs through virt-handler's own device plugin ([kubevirt/kubevirt#16890](https://github.com/kubevirt/kubevirt/pull/16890)), matched by PCI ID; on L40S every VF reports the PF's ID. On GPU Operator nodes nothing in Cozystack writes VF profiles; the `cozystack-nvidia-vgpu-restore` boot unit from cozystack/ansible-cozystack#70 covers only hosts with a host-installed driver.

Both consumers take GPUs the same way: `vm-instance` and `kubernetes-nodes` read `gpus` as a list of `{name: <resource name>}`, one entry per GPU. The deprecated `gn1.*` instance types carry GPU entries of their own in KubeVirt's shape, `{name: gpu1, deviceName: nvidia.com/A400}`, and neither default `permittedHostDevices` table lists that name. Tenant quotas become a namespace `ResourceQuota`, which refuses a GPU only when virt-launcher's pod is created, and the usage records (`Workload`) show a GPU only while a VM runs.

### The problem

- A tenant sees resource names, not offers. The dashboard's `gpu` option source lists the names KubeVirt permits; they say nothing about mode, profile or price.
- A GPU cannot be held. When a VM stops, its GPU goes back to the pool and the next start can fail.
- An order that does not fit is accepted, and the tenant learns it from pod events.
- Nobody can tell which tenant uses a physical GPU without reading pods on the node.
- In the `vgpu` variant a node labelled for passthrough can serve whole GPUs of another model, but a model whose whole GPU and VFs share a PCI ID, such as L40S, is offered either whole or as vGPU, not both, because two device plugins would claim that ID. The limit is per GPU model, not per cluster.
- VF profiles on GPU Operator nodes are written by hand after every reboot.

## Goals

- A hoster publishes a catalog of GPU classes, each with a model, a mode (passthrough or vGPU) and, for vGPU, a profile. Tenants read the catalog with free capacity and their remaining budget before ordering.
- A tenant orders a number of GPUs of a class within their quota and holds them, attached or not, until they release them. The hold is a hard guarantee once `gpu.rawResourceNames` is `forbidden` (section 7). That is the end state, not the default: `forbidden` needs every Talos GPU pool replaced by a Kairos pool, which waits for cozystack/cozystack#4911, and every ComputePlane GPU group gone, which waits for ComputePlane on Kairos workers. Until then a cluster with either keeps raw names, and the hold is not guaranteed there. An order that cannot be satisfied is refused with a message naming the class.
- VMs and Kairos worker pools attach held GPUs the same way. A restarted VM gets the same held device back, and a replaced worker gets one of its pool's devices back unless the tenant's own VM or another pool uses it.
- Every physical GPU and VF can be traced to its node, its configuration and the tenant using it.
- Held GPUs appear in usage records (`Workload`) by tenant, class and time, which an external billing system reads. Cozystack charges nothing and stores no prices.
- One cluster can have passthrough and vGPU nodes of the same GPU model, with vGPU profiles applied declaratively and kept across reboots.
- Existing `gpus` entries keep working, with a documented path to `GPUDevice` for VMs; existing Talos GPU pools stay on `name` entries.
- The catalog can later describe MIG, other vendors and DRA-backed devices without changing the tenant API.

### Non-goals

- Prices and charging. They live in an external billing system, keyed by class name.
- The vGPU guest driver, the license server and license tokens. The hoster provides them (section 5).
- The catalog UI. This proposal provides the data a portal reads.
- GPUs for pods inside tenant clusters (gpu-operator in a tenant cluster, HAMi).
- Time-slicing one device between tenants, and live migration of GPU VMs.

## Design

### 1. Object model

```yaml
# Hoster. One per offer. Cluster-scoped, readable by every authenticated user.
apiVersion: gpu.cozystack.io/v1alpha1
kind: GPUClass
metadata:
  name: l40s-24c
spec:
  displayName: "NVIDIA L40S, 24 GB vGPU (compute)"
  vendor: nvidia
  model: L40S
  mode: vgpu              # passthrough | vgpu
  profile: L40S-24C       # vgpu only
  memory: 24Gi
  maxCount: 2             # vGPUs of this class per VM
  onCordon: Move          # Move | Stay; can change
  nodeSelector:           # nodes carrying this profile
    gpu.cozystack.io/10de-26b9: L40S-24C
  source:
    devicePlugin:
      resourceName: nvidia.com/NVIDIA_L40S-24C
status:
  free: 12
  maxFreePerNode: 4       # most free units on one node
---
# Tenant. One per GPU or per set of GPUs used together on one node.
apiVersion: gpu.cozystack.io/v1alpha1
kind: GPUDevice
metadata:
  name: train-0
  namespace: tenant-acme
spec:
  className: l40s-24c
  count: 1                # GPUs of the class, always on one node
status:
  phase: Attached         # Pending | Bound | Attached | Lost
  nodeName: gpu-node-3
  attachedTo:
    vmi: vm-instance-trainer
  boundAt: "2026-10-14T09:00:00Z"
---
# Inventory. One per GPU node. Cluster-scoped, admin only.
apiVersion: gpu.cozystack.io/v1alpha1
kind: GPUNode
metadata:
  name: gpu-node-3
status:
  devices:
  - pciAddress: "0000:41:00.0"
    model: L40S
    function: pf          # pf | vf | whole
    vfs: 16
  - pciAddress: "0000:41:00.4"
    function: vf
    profile: L40S-24C
    healthy: true
    usedBy: {namespace: tenant-acme, pod: virt-launcher-vm-instance-trainer-x7k2p}
```

A class maps an offer to a backend through a `source` union and to the nodes that serve it through `nodeSelector`. The first release has one source, `devicePlugin`; `dra` is reserved for section 8. Two classes may share a resource name when their node selectors do not overlap, which lets a host layer that cannot tell two offers apart by name tell them apart by node. A node that matches two classes with the same resource name gets a condition on both classes and on its `GPUNode`, which names the two classes, and is not used for binding. Class names are DNS labels, since they become part of quota keys.

A class referenced by held devices cannot disappear or change its meaning. A finalizer keeps a deleted class until its last `Bound`, `Attached` or `Lost` device is gone, new orders against a deleted class are refused, a `Pending` order of a deleted class gets a condition and is never bound, and `vendor`, `model`, `mode`, `profile`, `memory`, `maxCount` and the kind of `source` cannot change while the finalizer is present. `resourceName` and `nodeSelector` can change, for changes in the host layer (section 6) and for moving the offer to other nodes.

A `GPUDevice` with `count: 2` is two GPUs on one node attached together; GPUs that move between VMs separately are separate devices. How many GPUs of a class one VM may take is the hoster's `maxCount` on the class. For vGPU classes it follows NVIDIA's multi-vGPU matrix for the board and the profile series. Compute (C-series) profiles follow the [NVIDIA AI Enterprise matrix](https://docs.nvidia.com/ai-enterprise/release-8/latest/infra-software/vgpu/features/multi-vgpu-support.html), which allows several vGPU for Compute devices per VM on L40S under the Linux with KVM hypervisors it supports (section 6), so the `l40s-24c` class above sets `maxCount: 2`. Graphics (Q-series) profiles follow the vGPU 20 release notes for Linux with KVM, which allow several Q-series vGPUs per VM on L40S and offer no C-series profiles. For passthrough classes it is at most the GPUs per node. NVIDIA requires all vGPUs in one VM to share board type and series. Cozystack keeps it simpler and stricter: a VM takes vGPU devices of one class only, although NVIDIA would also allow, say, A40-48Q next to A40-16Q. `maxCount` defaults to 1 for vGPU classes; on a passthrough class, unset means no limit beyond the GPUs of one node. Admission refuses a device above `maxCount`. The spec is immutable, so a change is a new order.

The inventory is written by a node agent from sysfs and from kubelet's pod-resources API, and goes away with its `Node`. Ownership is exact for running GPUs: a `usedBy` pod leads to its VM, and the VM's devices name it in `status.attachedTo`. A held but not running reservation is a count on a node, not a specific card, because with a device plugin kubelet picks the physical device when the pod starts. PCI addresses stay in `GPUNode`, which tenants cannot read.

What `Bound` guarantees depends on the class's source:

| Source | A `Bound` device holds | The physical device is picked by |
| --- | --- | --- |
| `devicePlugin` | `count` free units of the class on one node | kubelet, when the pod starts |
| `dra` (section 8) | one `ResourceClaim` within budget, and its devices once the claim is allocated at the first start | the scheduler, at allocation |

A tenant sees a class, a count, a phase and, once there is one, a node, never which card it holds, so its API stays the same when an offer moves to a class of the other source.

### 2. Binding and capacity

A controller in `cozystack-controller` binds each new `GPUDevice` to a schedulable node that matches its class and has `count` free units. A node is cordoned when its `spec.unschedulable` is `true`; taints do not count, including the `NoSchedule` taint Fence Agents Remediation sets on a node under remediation. New binds, moves, rebinds and gathers skip cordoned nodes, nodes that are not `Ready`, and nodes with a `NoSchedule` or `NoExecute` taint that virt-launcher pods do not tolerate, and their units do not count in the class's capacity, `free` or `maxFreePerNode`. The taint rule leaves out the `node.kubernetes.io/*` taints Kubernetes sets itself for a cordon or a node that is not `Ready`, which the first two rules already cover. A device already bound to a node that stops being `Ready` stays there until lost capacity moves it (below), so a short outage moves nothing. The one exception is the gather at VMI creation (section 3): it may take an unattached device off a node that is not `Ready`, because no VMI uses that device and the move happens once, for a VM that is starting now, so nothing flaps back when the node returns. Free capacity is the node's allocatable count of the class's resource, minus the units held by bound devices, minus the units pods hold beyond what their devices cover; the last term counts VMs that still use a raw resource name. By default the binder packs onto the fullest node, so whole nodes stay free for large sets.

Admission refuses an order that cannot be met at that moment, by capacity or by budget (section 4), with a message naming the class, the count and the limit it hit: a budget of the tenant or an ancestor, the class's free units, or the free units on one node. Two orders admitted at the same moment can still exceed a limit together, so the binder checks capacity and budgets again before binding. An order that loses this race stays `Pending` with a condition naming the shortage, and is bound when units or budget free up.

The phases decide what a device holds and costs:

| Phase | Holds GPUs | Counts against budget | In usage records |
| --- | --- | --- | --- |
| `Pending` | no | no | no |
| `Bound` | yes, on a node | yes | yes |
| `Attached` | yes, used by a VMI | yes | yes |
| `Lost` | no | yes | no |

A `dra` device before its first start is the exception: it is `Bound` without a node, holds no GPU and is not in the usage records (section 8).

A device leaves `Attached` when its VMI reaches a final phase (`Succeeded` or `Failed`) or is removed from the API, whichever comes first. A deletion timestamp alone is not enough: on an isolated node the domain of a VMI being deleted can still hold the GPU, while a final VMI no longer runs its domain. KubeVirt v1.9.0 deletes a final VMI before it creates the VM's next one ([`vm.go`](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-controller/watch/vm/vm.go)), so a new VMI never finds its device attached to the old one.

A device keeps its node while the `Node` exists, matches the class, has not lost capacity, has no pending layout change (section 6) and is not cordoned under `Move`; otherwise an unattached device that no longer fits there moves to another node of its class, or to `Lost` when none has room. The devices one VM names move as a set: together to one node with room for all of them, together to `Lost` when they must leave and no node has room for all of them, and they are rebound together. A device or set whose only reason to leave is a cordon is the exception: with no room elsewhere it stays where it is. When a device's node is deleted, the controller also deletes the device's unscheduled VMI, so the device becomes unattached and moves. KubeVirt cannot live-migrate a VM with a passthrough GPU or an SR-IOV VF, so pinning adds no cost in normal operation. A cordon moves devices by the class's `onCordon` policy. With `Move`, the default, an unattached device on a cordoned node moves to another node of its class when one has room, keeping its hold and its usage record, and carries a condition naming the cordon while it waits. Since cozystack/cozystack#4403 a drain evicts a `vm-instance` VM that cannot live-migrate, so its device becomes unattached, moves, and the VM's next VMI starts on the new node. Kairos workers use `evictionStrategy: External`, so a drain leaves them to Cluster API, and a replaced worker takes a device by the pool rules of section 3. With `Stay` the device keeps its node and the VM waits for it, for a hoster who wants VMs back on the same hardware after maintenance. When the cordon is the only reason any device of a set has to leave and the set mixes classes, the whole set stays in place, and its VM waits, while a device of a `Stay` class sits on a cordoned node, because the set moves together; a gather at VMI creation, when one is needed, refuses such a set (section 3).

A drop in allocatable alone is not a loss, since kubelet reports zero after every restart until the device plugin registers again. A node has lost capacity when the agent reports a device missing or unhealthy for longer than a grace period, or when the `Node` has been `NotReady` that long without interruption; a node that is not `Ready` fits no device. Then:

- the node and the affected classes get a `LostCapacity` condition;
- unattached devices that no longer fit move to another node of their class, or to `Lost` when none has room;
- on a `NotReady` node, attached devices go to `Lost` as well, which drops them from the usage records, and are rebound once their VMIs reach a final phase or are removed from the API; if the node returns while the VMI still runs, the device goes back to `Attached` there; a device that a gather at VMI creation is moving off the node is the exception: it finishes the move, or loses its attachment and follows these rules when the gather fails;
- a VM started on such a node after the condition is set is refused (section 3), and the controller deletes the unscheduled VMI of a device that no longer fits, so its device becomes unattached and can move. What the VM does next follows its `runStrategy` for a deleted VMI: `Always` and `Once` create a new VMI and start once the device has moved or been rebound from `Lost`, while `Manual` and `RerunOnFailure` stay stopped until the tenant starts the VM (Failure cases); unlike a cordon, the node cannot serve the device, so waiting is no option. A VM restarted earlier, on a node that is not `Ready`, is gathered at creation instead (section 3).

Devices in `Lost` are rebound before new orders when capacity returns, a VM's set onto one node, without blocking smaller orders behind a large one; a device whose VMI is still live waits until that VMI is final or removed (above). The controller's metrics show how many GPUs are waiting. A rebind never fails a budget check, since a `Lost` device already counts.

### 3. Attaching

VMs and Kairos worker pools reference devices in `gpus`:

```yaml
# vm-instance
gpus:
- device: train-0
---
# kubernetes-nodes, Kairos pool: each worker takes one matching device
gpus:
- deviceSelector:
    matchLabels:
      pool: inference
```

The chart renders only the reference, on the VMI template. A mutating webhook on VirtualMachineInstance creation resolves it. For a VM it takes the named devices. For a pool worker it takes the device the worker held before, unless a VM now names it, its node is not `Ready`, or its node has such a taint (section 2). Otherwise it takes one unattached `Bound` device on a `Ready` node that matches the selector and that no VM names in a `device` entry, preferring nodes that are not cordoned and have no such taint. Selectors are not checked against each other: when two pools match the same device, the first worker to start takes it, so a tenant gives each pool its own labels. The webhook records the attachment on the device, so two VMIs can never hold one device, and fills in the class's resource name. An attachment recorded for a VMI that is never created, because a later admission step refused it, is cleared by the controller after a grace period longer than the API server's request timeout. A device still on a cordoned node under `Move` is first moved by the binder when another node has room, and the pod is pinned to the new node. A device that is already attached when its node is cordoned, to a VMI whose pod has not started, moves the same way: for a VM with `runStrategy` `Always` or `Once` the controller deletes that unscheduled VMI and the VM's next VMI gets the moved device; under `Manual` or `RerunOnFailure` the VMI waits on the cordoned node, since deleting it would leave the VM stopped (Failure cases). Devices a VM ordered separately can sit on different nodes, for example two classes the binder placed apart. When the devices a VMI references are on different nodes, on a node that is not `Ready`, on a node with such a taint (section 2), or on a node with no room for the VMI's template devices, the webhook has the binder move them onto one `Ready` node that is not cordoned, has no pending layout change and has room for all of them, template devices included. This runs after the other refusal checks below and after the template devices pass their budget and entitlement checks, so every device in the set is unattached or already attached to this VMI. The webhook records the attachment of the whole set before the move, so no other VMI or background move can take a device of it, and each device keeps its hold. A device of a `Stay` class on a cordoned node does not move. When all of the VMI's devices are on that node and the VMI has no template devices, there is no gather, even if the node is also not `Ready`: the VMI is pinned there and waits. Otherwise the set cannot be gathered, and the VMI is refused. With no node that fits, including when no node serves all the set's classes, the VMI is refused, the attachments are cleared, and the message names each device and its node. A move that does not finish within the admission timeout refuses the VMI the same way; devices already moved keep their new node, and KubeVirt's retry continues from there. Template devices are created after this step, on the node of the set. A second webhook on virt-launcher pod creation pins the pod to the device's node with a required node affinity on the node's name, as a `matchFields` requirement on `metadata.name` added to every node selector term, or as the only term when the pod has none, not with `nodeName`, which would let kubelet start the pod on a cordoned node. The pin goes on the pod because, with `vmRolloutStrategy: LiveUpdate`, KubeVirt rewrites a VMI's node affinity from the VM template.

The webhook refuses to start a VMI, and names each device and its node, when a referenced device:

- is `Pending`, `Lost`, being deleted, or attached to another VMI;
- is bound to a node that is gone, no longer matches the class, has `LostCapacity` the device does not fit under, or has a pending layout change (section 6);
- cannot be brought, with the VMI's other devices and template devices, onto one node with room for all of them (above), for example because a device of a `Stay` class is on a cordoned node while other devices need gathering;
- is a vGPU device next to vGPU devices of another class, or brings the VMI's GPUs of its class above `maxCount`.

A GPU entry is any `gpus` entry, any GPU resource name in the VMI's `spec.domain.resources`, and any `hostDevices` entry whose device name is a GPU resource name, on the VMI or from its instance type. GPU resource names are the resource names of `GPUClass` objects and the names in the hoster's list `gpu.resourceNames`, which defaults to the NVIDIA (`10DE`) and mdev entries of the KubeVirt `permittedHostDevices` table. The list also takes every resource name advertised by a GPU device plugin the platform deploys, the sandbox or patched plugin and the GPU Operator's device plugin, as the agent reports it, so a vfio GPU missing from the table and a name that only pods request, such as `nvidia.com/gpu` of the `container` variant, still count. A NIC is not a GPU entry: a ConnectX or BlueField VF, which NVIDIA's network operator advertises as `nvidia.com/hostdev`, can go next to a GPU device for GPUDirect RDMA. The webhook also refuses a referencing VMI that carries GPU entries of its own, from any source, including an instance type or a VM written directly by a tenant super-admin. A refused VMI leaves its devices unattached, so they move by the rules of section 2. Both webhooks see only VMIs and pods carrying the chart's reference label and fail closed, so an outage blocks the start of VMs with device references and nothing else.

A device cannot disappear under a running VM: a deleted `GPUDevice` stays, with its binding and usage record, until no VMI uses it, and is not attached again.

A `deviceTemplate` entry names a class and a count instead of a held device. At VMI creation the webhook creates a `GPUDevice` from it, named after the VMI and the entry's index and marked as a template device. A repeated call or a recreated VMI finds the same device and reuses it when it carries that mark, has no owner yet and matches the template; a device of that name still being deleted, or still owned by the VM's previous VMI, refuses the VMI, and KubeVirt retries. Tenants cannot set the mark, so a tenant's own order is never taken or deleted this way; an unmarked device that already has the name refuses the VMI, and the message names it. The webhook has the binder bind the device under the same capacity and budget checks as any order, within the admission timeout, and attaches it. The node for template devices is picked with room for all of the VMI's devices, held and template: the node of the gathered set when the VMI has held devices, and otherwise one node with room for all its template devices. The VMI is refused if no node has room for all of them. A bind that does not fit or does not finish in time refuses the VMI, and KubeVirt retries. On a dry run the webhook creates, attaches and moves nothing, so it declares `sideEffects: NoneOnDryRun`. The VMI has no UID yet at that point, so the controller sets the VMI as the device's owner once the VMI exists, and deletes a template device whose VMI never appears after the same grace period as a stale attachment. The controller deletes a template device as soon as its VMI is final or removed, since KubeVirt can keep a finished VMI until the next start, so the device lives and is billed only while its VMI runs, as a `ResourceClaim` made from a `ResourceClaimTemplate` lives only with its pod. Budgets, the vGPU entitlement (section 4), usage records and the refusals above apply to it as to any device, but only while the VMI exists: an order that does not fit is refused when the VM or worker starts, not ahead of time. A template counts as a device reference under `forbidden`. It lets an autoscaled pool pay for running workers only.

A `vm-instance` entry has exactly one of `name`, `device` or `deviceTemplate`, and a `kubernetes-nodes` entry exactly one of `name`, `deviceSelector` or `deviceTemplate`. A list holds only `name` entries or only references, and a pool has at most one `deviceSelector` or one `deviceTemplate`, never both, and a selector's devices must share a class and a `count`; the webhook checks this at each attach, since device labels can change after the pool is admitted. The new entries are KubeVirt-only; Proxmox pools keep refusing `gpus`. ComputePlane is not yet an equal consumer: the module does not let a tenant pick the worker OS, so its GPU groups are Talos pools on `name` entries, and moving them to Kairos is separate work.

Kairos pools take GPUs only through `deviceSelector` or `deviceTemplate`, from classes of both modes. The chart refuses `name` entries on them. The pool's mode, `kairos.gpuMode`, must match the class, and the chart sets it as the `gpu.cozystack.io/mode` label on the worker VMI template ([Choosing the worker image by mode](../kairos-vgpu-workers/README.md#choosing-the-worker-image-by-mode)). At VMI creation the webhook refuses a device, selected or from a template, whose class `mode` differs from that label, and a pool reference on a VMI without the label. The `kubernetes-nodes` chart, from Rollout step 2, fails to render a `deviceSelector` or `deviceTemplate` on a pool with `os: talos`, the field cozystack/cozystack#4911 adds, so the pool's HelmRelease fails before any machine is created. A pool on `deviceSelector` never runs more GPU workers than it has matching devices. Admission on `KubernetesNodes` refuses a `maxReplicas` above that number; the chart default is 10, so a pool with fewer devices sets it. A pool without spare devices rolls one worker at a time: surge machines are created at once, their VMIs are refused until an old worker frees a device, and a surge machine that waits past `nodeStartupTimeout` is replaced by MachineHealthCheck. Talos GPU pools, existing and new, keep working as today on `name` entries, and `GPUDevice` does not extend them.

### 4. Quotas

A tenant's GPU budget is declared in `resourceQuotas`, per class as `gpu.cozystack.io/<class>: <count>` and in total as `gpu.cozystack.io/total: <count>`; the class name `total` is reserved. These keys are not rendered into the namespace `ResourceQuota`, since `GPUDevice` is not a pod, and a tenant with only GPU keys gets no `ResourceQuota` or `LimitRange`. That is a change to the tenant chart, which today renders both for any `resourceQuotas` key.

GPU budgets follow their own rule, not the pool and carve-out rules for other resources: every GPU key caps the whole subtree of the tenant that declares it. Today's pools cannot carry GPU keys, since a tenant that declares any key becomes its own pool root and leaves its parent's limits for every key it does not declare, so a child with only `cpu` would escape its parent's GPU budget. The tenant declaration gate skips `gpu.cozystack.io/*` keys, because under this rule a child's key may overlap its siblings'.

- An order is refused if, for the tenant or any ancestor that declares the class key or `total`, the devices counting against the budget (table in section 2) in that ancestor's subtree would exceed the key.
- A child's key only adds a limit; it never lifts an ancestor's limit or a missing entitlement.
- A raw GPU key (section 7) on the tenant or any ancestor for a class's resource name blocks orders of that class until the tenant that carries the raw key also has the class key or `total`. While the raw key exists, it also caps those classes' devices together with the raw-name VMs it covers, so an existing GPU limit is never bypassed.
- A tenant with no GPU key of either kind anywhere above it is limited only by capacity, for vGPU classes once it is entitled.
- A vGPU class is ordered only by a tenant entitled to vGPU. The Tenant field `gpu.vgpu` grants it. The parent tenant's administrator sets it, or the hoster on a top-level tenant. A tenant is entitled only when the flag is set on it and its parent is entitled. The root tenant is rendered by the platform, not by the tenant chart, so its value comes from platform values. The flag is off by default everywhere, so the hoster turns it on for the root first. No tenant can grant itself vGPU, since its Tenant object lives in its parent's namespace. The tenant chart computes the effective value from the parent's, like its other namespace values, and stamps it on the namespace as the label `namespace.cozystack.io/vgpu`; a change reaches grandchildren as each level re-renders. The chart always writes the label, so only the value `"true"` means entitled; any other value, or no label, means not entitled. Entitlement is separate from budget because a vGPU license is counted per unit, which is a budget, while getting the vGPU guest driver and the license token at all is yes or no. Class keys and `total` then limit vGPU like any other GPU. Clearing the flag refuses new vGPU orders, including the device a `deviceTemplate` would create at the next VMI start, so such a VM or worker no longer starts. Devices already held keep counting and can still move or be rebound, so a pool on `deviceSelector` keeps its devices, and the tenant's Kairos pools lose their vGPU entitlement as `kairos-vgpu-workers` describes. Raw vGPU names in VMs stay as open as today until a raw key limits them or `forbidden` is set (section 7).
- GPU keys play no part in the existing pool membership and carve-outs.

A parent with `total: 4` caps everything below it at four GPUs, each vGPU counting as one. A parent cannot reserve part of its budget for one child. #48 would add that, but as written it covers neither GPUs nor entitlement, so this section stays GPUDevice's own rule, and budgets move to #48 only if its gate takes over subtree caps for GPU keys.

### 5. Usage accounting, catalog and licensing

Cozystack does not bill. It keeps usage records (`Workload`) that an external billing system reads. Each `GPUDevice` gets one with `kind: gpu-device` and resources `gpu.cozystack.io/<class>: <count>` while it is `Bound` or `Attached`, a `dra` device only once its claim is allocated (section 8), next to the records Cozystack already keeps for CPU, memory and storage. The GPU controller writes the record itself, under a `WorkloadMonitor` it owns in the tenant's namespace, whose selector matches no object, so tenant labels cannot drop a device from the usage records. The selector is a reserved label key that admission refuses on every object in a tenant namespace, on create and update, whoever makes the request, so no object can match it, not even a virt-launcher pod or a DataVolume that inherits the label from a VM template. Each record carries the `workloads.cozystack.io/monitor` label naming that monitor: the `Workload` controller deletes a record only when that label is missing or names a monitor that is gone, and deletes nothing by selector. Like other `Workload` records it has no time fields: a held interval is the record's lifetime, which a billing system observes by watching and reconciles by sampling. A virt-launcher pod does not report GPUs its devices cover a second time.

The catalog is the list of `GPUClass` objects with `displayName`, model, mode, profile, memory, `maxCount`, `status.free` and `status.maxFreePerNode`. Every authenticated user reads it, because a hoster publishes its offers and a tenant names a class when ordering, including from `kubectl` or GitOps without a portal. So the status holds only `free` and `maxFreePerNode`, and its conditions, such as `LostCapacity`, name no nodes and carry no counts. The class's capacity, bound units and `Lost` count show the hoster's inventory and load, and they are not in the status. The controller exports them as metrics that platform monitoring scrapes. Per node, a hoster reads device occupancy and health from `GPUNode`, and lists bound devices with a field selector on the `GPUDevice` field `status.nodeName`, which the CRD declares in `selectableFields` and tenants cannot write. A tenant can still watch `free` and `maxFreePerNode` over time and see how busy a class is. The dashboard reads the catalog through a new `gpuclass` option source, which also returns the tenant's remaining budget per class; `OptionItem` gains optional integer fields for `free`, `maxFreePerNode`, `maxCount` and `remainingBudget`, and a portal offers the smallest of `maxFreePerNode`, `remainingBudget` and `maxCount` as the largest order, leaving out a field that is absent because it sets no limit. For a vGPU class and a tenant without the vGPU entitlement, `remainingBudget` is 0. A `gpudevice` source lists a tenant's own devices. The existing `gpu` source keeps serving raw names while they are allowed.

The remaining budget is computed over each ancestor's whole subtree, so a tenant under a shared parent `total` can see how many GPUs its siblings hold, as a number, without names. This is accepted: the alternative is a budget the tenant can only discover by a refused order, which is the failure this proposal removes.

vGPU needs a license, and passthrough needs one only for graphics. Compute (C-series) profiles are licensed through NVIDIA AI Enterprise, graphics (Q-series) profiles through NVIDIA vGPU software. The hoster provides the license server, the token and the guest drivers. In worker pools `kairos-vgpu-workers` delivers the token and the guest license settings. In VMs the hoster does, through its image or at boot, as `vgpu-vms` describes. The class's `mode` tells a portal and a tenant which case applies.

### 6. Host layer

This proposal requires vGPU hosts to run Ubuntu or Kairos with `bundles.system.variant: isp-full-generic`: the vGPU host driver and the patched plugin run there. Cozystack has no Kairos hosts today, so installing the bundle on Kairos and a vGPU Manager image for it are new work of this phase. `docs/gpu-vgpu.md` does not recommend Talos for vGPU, so this phase does not cover Talos hosts; they keep passthrough through the `default` variant as today. NVIDIA AI Enterprise lists vGPU for Compute on Linux with KVM as supported only through specific hypervisor vendors and, on Ubuntu and RHEL with KVM, for single-node hosts only ([support matrix](https://docs.nvidia.com/ai-enterprise/release-8/latest/support/support-matrix-8/8.2.html)), so support for such a host is a matter between the hoster and NVIDIA.

The host layer is everything between the GPUs on a node and the resource names classes point to: the host driver, VF profiles, node modes, and the device plugin that advertises GPUs to KubeVirt. It ships as its own phase (Rollout, step 4), and it is replaceable. The API above it depends on four things only: each class's `resourceName` is advertised on the nodes its `nodeSelector` picks, one name never covers two different offers on one node, `GPUNode` reports devices and their health, and node labels mark each node's mode and profiles. `GPUClass`, `GPUDevice`, `GPUNode`, quotas, both webhooks, usage records and migration use no plugin-specific name, discovery format or configuration; the agent maps whatever device IDs a plugin reports to PCI addresses through sysfs. A different plugin, or a different version of the one chosen here, changes only this phase and the `resourceName` of affected classes, which can change while devices are held. Tenants keep their devices and `device` entries; raw `name` entries and raw quota keys on a renamed resource move to the new name in the same maintenance window, or their consumers move to `GPUDevice` first.

Until the phase ships, a cluster keeps the modes its variant gives it today, one per model for models whose whole GPU and VFs share a PCI ID, and vGPU classes keep the experimental status `docs/gpu-vgpu.md` gives the `vgpu` variant: the hoster writes profiles and node labels by hand. On clusters without a layout, which includes the `default` variant and Talos hosts, node labels stay hand-written.

The device plugin is the decision reviewers should look at first. Cozystack ships NVIDIA's kubevirt-gpu-device-plugin with NVIDIA/kubevirt-gpu-device-plugin#192 applied downstream, as the sandbox device plugin of the `vgpu` variant, until NVIDIA/kubevirt-gpu-device-plugin#192 merges upstream or upstream takes another approach. NVIDIA/kubevirt-gpu-device-plugin#192 adds discovery of vGPU VFs on the vendor-specific VFIO framework and advertises them with one resource name per profile, next to the passthrough names the plugin already advertises for GPUs bound to `vfio-pci`. One plugin then serves both modes of every model under distinct names, so the per-model limit of the problem list goes away. The branch is validated on hardware. Its upstream review has had no maintainer response since 2026-07-10, which is why it ships as a downstream patch rather than waiting. On a card whose VFs all carry a profile, the plugin can read profile names only through NVML, so the platform runs it as its own DaemonSet with the host driver's `libnvidia-ml.so.1` and the `/dev/nvidia*` nodes mounted, privileged; the GPU Operator's DaemonSet has no field for those mounts. The patch leaves when NVIDIA/kubevirt-gpu-device-plugin#192 merges upstream, in this or another form, or when the NVIDIA DRA driver serves vGPU VFs to KubeVirt (kubernetes-sigs/dra-driver-nvidia-gpu#1402); a merged NVIDIA/kubevirt-gpu-device-plugin#192 changes only this phase, while a move to DRA adds new `dra` classes (Rollout, step 6). KubeVirt's built-in plugin was the other candidate (Alternatives).

The phase adds a node layout to the `vgpu` variant, in platform values. For each node selector it names a mode and, for vGPU, the profile of each GPU model. Nodes outside every selector are passthrough, through the `defaultWorkload: vm-passthrough` the layout renders. With a layout:

- The platform runs the patched plugin and renders the KubeVirt `permittedHostDevices` entries for the names it advertises, as entries with an external provider. Other vendors' entries, such as an FPGA, stay as written.
- A node agent writes the VF profiles at boot and when the layout changes. The controller sets each node's workload label and one label per GPU model, `gpu.cozystack.io/<vendor>-<device>` (for L40S `gpu.cozystack.io/10de-26b9`), to the profile or to `passthrough`; class node selectors match these labels.
- The plugin does not pick up VFs that gain a profile after it started, so the controller restarts the plugin on a node when devices there become ready later, for example after a reboot. Running VMs are not affected.

The layout and mdev GPUs coexist: GPUs on the mdev path keep today's configuration through `vgpu-device-manager`, and the agent writes profiles only on nodes that use VFs. New deployments of SR-IOV GPUs on current host releases use VFs.

A layout change on a node, of mode or of profile, waits until the node has no GPU VMs. The node gets a `LayoutPending` condition: new binds and starts there are refused, unattached devices move away, and running VMs keep their GPUs until the hoster drains the node. A GPU VM created before Cozystack switched to `LiveMigrateIfPossible` needs one restart before the drain can evict it. A node whose hand-written profiles already match the layout is not a change. A `vgpu` cluster adopts the layout with vGPU selectors that cover its existing vGPU nodes, and labels those nodes `vm-vgpu` before setting it, so the layout's passthrough default does not change them in between. Its vGPU classes, raw `name` entries and raw quota keys move from the hand-written VF names to the plugin's per-profile names in one maintenance window, or the raw-name consumers move to `GPUDevice` first. KubeVirt's built-in plugin and the patched plugin report different device IDs for the same VF, and kubelet would otherwise hand out VFs in use, so affected nodes are cordoned and free of GPU VMs until the patched plugin has registered there.

### 7. Migration of existing `gpus`

Raw `name` entries keep working, and nothing converts them automatically, because a conversion would bind GPUs a tenant never ordered and put them in the usage records. The binder counts their usage, so it never books a unit a raw-name VM holds. The reverse is not protected: the scheduler does not see a device that is bound but not attached, so while raw names are allowed, a raw-name VM can take that unit. A reservation is a hard guarantee only with `gpu.rawResourceNames: forbidden`.

A tenant moves a VM by stopping it, ordering devices of the matching class, replacing each `name` entry with a `device` entry and starting it. On a full pool the device can only be ordered after the stop; with spare units, ordering first puts both in the usage records until the restart. A Talos GPU pool has no in-place path. The tenant creates a Kairos pool with `kairos.gpuMode` and a `deviceSelector`, which needs a hoster-built worker image for the class's mode, and then deletes the Talos pool. On a full cluster the Talos pool scales down first, so its GPUs are free to order. ComputePlane GPU groups have no such path until ComputePlane runs Kairos workers, so a cluster with them keeps raw names allowed. The `gn1.*` instance types get no class to migrate to: they are deprecated, and the name they request is in neither default `permittedHostDevices` table. A VM on one moves to an instance type without GPUs and takes `device` entries. A Talos pool on one takes the Kairos path above.

Quota keys move too. A raw GPU key is a quota key on a GPU resource name, such as `nvidia.com/AD102GL_L40S`. A device-backed VM's pod still requests that name, so while a tenant migrates, its raw key covers both kinds of VM; after its last raw-name VM has moved, the key is replaced by `gpu.cozystack.io/*` keys. A tenant whose raw-name VMs use vGPU can order their devices only after it is entitled to vGPU (section 4), and within any class keys set above it.

When every consumer has moved, the hoster sets `gpu.rawResourceNames: forbidden`. Preconditions: no Talos GPU pools in the cluster, ComputePlane GPU groups included (a hoster without a passthrough worker image keeps passthrough Talos pools and raw names), no `gn1.*` GPU instance types in use, and no raw GPU quota keys left. With `forbidden`:

- an admission policy refuses GPU entries (section 3) on every VMI created without a device reference, on every path;
- the same policy refuses every pod created in a tenant namespace that requests a GPU resource name, unless it is a virt-launcher pod created by KubeVirt's virt-controller service account with an owner reference to a VirtualMachineInstance. Tenants cannot forge such a pod, since no tenant role can create pods or VMIs, so app charts that copy free-form `resources` into pods cannot request GPUs either;
- tenant validation refuses raw GPU keys;
- the `gpu` option source returns nothing, and the `instancetype` source hides instance types that carry GPU entries.

### 8. Extensions

The catalog is designed so later backends add `source` members and class fields, not tenant fields.

- DRA. Kubernetes DRA has been GA since 1.34, and KubeVirt v1.9 has `GPUsWithDRA` and `HostDevicesWithDRA` in beta. A `dra` source names a `DeviceClass` and selectors, and under it a `GPUDevice` maps one to one onto a `ResourceClaim` created at order time. The scheduler allocates the claim, the GPU controller keeps it allocated through its own `reservedFor` entry, which the resourceclaim controller leaves in place, and the webhook renders the claim reference into the VMI; the binder does not allocate. Three gaps remain. The NVIDIA DRA driver has no vGPU yet (kubernetes-sigs/dra-driver-nvidia-gpu#1402, milestone v0.6.0), and its passthrough is alpha. A claim is allocated only when a pod first needs it, so nothing is held before the first boot: until then a `dra` device is `Bound` without a node, counts against the budget, holds no GPU and stays out of the usage records, and the webhook attaches it like any `Bound` device without pinning the pod to a node. DRA quota is per namespace, without subtree caps. KubeVirt VEP 344 (`persistWhenStopped`) holds a claim for a stopped VM from the VM side; a `dra` class and VEP 344 must not both manage one claim. When a DRA GPU driver is installed, the raw-name policy has to cover claim references too.
- MIG-backed vGPU profiles get their own resource names from the patched plugin, so they are ordinary classes; with DRA, partitionable devices (beta in Kubernetes 1.36) describe MIG natively.
- A non-NVIDIA GPU is a class with another `vendor`; Q, B and A series vGPU profiles are classes like C series.
- NVLink sets that must be attached together are left to the DRA source.
- On-demand slicing. A hoster may let tenants take any size from a shared pool, from a fraction of a GPU, such as a MIG-backed vGPU on a 1/7 slice of an A100, up to several whole GPUs of one node, taken from GPUs the layout keeps out of MIG mode. The tenant contract stays a class, one per size, and a `count`, and a device still lives on one node. The host layer and the binder change: the node agent creates the profile, and on MIG GPUs the MIG instance under it, when a device binds instead of following a fixed layout, so the binder counts capacity from free GPU resources rather than from advertised names. Enabling MIG mode needs an idle GPU and, on Ampere, a GPU reset, so the layout enables it ahead of time. On Hopper and newer the mode does not survive a reboot, so the layout sets it again at boot. A plugin restart per bind, as section 6 does for late profiles, would leave the node's allocatable at zero until the plugin registers again on every bind, so the plugin needs to rediscover profiles without one, separate work on top of NVIDIA/kubevirt-gpu-device-plugin#192.

## User-facing changes

- Tenants get `GPUDevice`, a GPU catalog with free capacity and remaining budget per class, `device` entries in `vm-instance`, `deviceSelector` entries for Kairos pools in `kubernetes-nodes`, and `deviceTemplate` entries in both. Raw resource names keep working until a hoster forbids them.
- A held device is billed while held, whether a VM runs on it or not. An autoscaled Kairos pool on `deviceSelector` holds, and pays for, every device its selector matches, which is at least `maxReplicas`; under `forbidden` nothing runs on GPUs without a device. A tenant who wants to pay only while running uses `deviceTemplate`, and gets the refusal at start instead of at order time.
- Hosters get `GPUClass`, `GPUNode`, GPU budgets in `resourceQuotas` and the `gpu.vgpu` Tenant flag that opens vGPU classes to a tenant, the node layout, `onCordon` on classes, the `gpu.resourceNames` list and `gpu.rawResourceNames`; an external billing system gets a usage record (`Workload`) per held device.

## Upgrade and rollback compatibility

The change is additive. Existing `gpus` entries render as before, and the existing variants work as today until a layout is set.

Rollback is a hoster operation; its step-by-step runbook belongs to the documentation of the implementation. It keeps VMs and worker pools, which restart in the windows, and the tenants' GPU limits, with four exceptions. Choosing `default`, or one mode for a model whose whole GPU and VFs share a PCI ID, drops the other mode's devices and stops their VMs, a loss of service the hoster announces first. Tenants whose only limits were `gpu.cozystack.io/*` keys lose them (rules below), and tenants without the vGPU entitlement can use raw vGPU names again. Kairos GPU pools take GPUs only through `GPUDevice`, so they lose their GPUs and run on the plain worker image. A child tenant that declares its own quotas no longer shares its parent's GPU limit, because a raw key is carved out of the parent's pool rather than capping the subtree.

The phases run in this order. The hoster removes the node layout and chooses the variant in a maintenance window. Raw names are allowed again together with the raw keys, since raw keys are refused while raw names are forbidden and a later quota does not evict a running pod. `device` and `deviceTemplate` entries of VMs become raw `name` entries and those VMs restart. `deviceSelector` and `deviceTemplate` entries and `kairos.gpuMode` are removed from Kairos pools before the `gpu.vgpu` flag goes, since otherwise their new vGPU worker templates wait for a license copy. The `GPUDevice` objects and the `gpu.cozystack.io/*` keys are deleted. Last, the components, the CRDs, the GPU controller's `WorkloadMonitor` objects, the `gpu.vgpu` flag with its namespace label and the `gpu.cozystack.io/*` node labels are removed. Each node's workload label stays on `vgpu`, since without the layout the variant's default would turn passthrough nodes into vGPU nodes; on `default` the former vGPU nodes are relabelled `vm-passthrough` in the first window.

Three rules hold throughout:

- Names move back by the window rule of section 6 in reverse: vGPU classes return to the hand-written VF names, and raw `name` entries and raw keys on the per-profile names move with them in the same window.
- Raw keys go through the existing tenant quota pools, not the subtree rule of section 4: a declared key makes a tenant its own pool root for every resource and a child's key is carved out of its parent's pool. So raw keys go only to tenants that already declare quotas other than `gpu.cozystack.io/*`, and raw names elsewhere are unlimited, as before this proposal.
- A raw key also renders the tenant chart's default `LimitRange`, whose memory limit can sit below a VM's memory request, because KubeVirt sets virt-launcher limits from the namespace quota only when it limits `cpu` and `memory`, so a tenant given a raw key also declares `cpu` and `memory`.

## Security

- Tenant access. Tenants with the `admin` role or above manage `GPUDevice` in their namespaces; lower roles read it. Every authenticated user can read `GPUClass`, through a ClusterRoleBinding to `system:authenticated`, because tenant roles are bound per namespace and cannot grant a cluster-scoped kind. Only platform administrators write `GPUClass`. Tenants cannot read `GPUNode`.
- What a tenant controls. A tenant never supplies a resource name or a node through a device reference: both come from `GPUClass` and from the device's status, which tenants cannot write. Tenants cannot add or remove finalizers on devices, so a held device cannot leave the usage records. Admission refuses the reserved label of the GPU usage monitor on any object in a tenant namespace, on create and update (section 5). A tenant cannot set its own `gpu.vgpu` flag, since its Tenant object lives in its parent's namespace. The webhook takes devices only from the VM's own namespace. A tenant super-admin who writes VirtualMachines directly is covered too: a referencing VMI with its own GPU entries is refused, and with `forbidden` any GPU entry without a device reference is refused, as is any other pod in a tenant namespace that requests a GPU resource name.
- Device plugin. The patched plugin runs privileged with the host driver's NVML library and device nodes, like the vGPU manager does.
- Node agent. It reads sysfs and kubelet's pod-resources socket, writes VF profiles on vGPU nodes from a privileged container, as the vGPU manager already does, and can write only its own node's `GPUNode`.
- Webhook. For `deviceTemplate` entries it creates `GPUDevice` objects in tenant namespaces, only of the class and count the template names.
- Controller. Besides its own `gpu.cozystack.io` kinds and the `Workload` records it already writes, it gains three rights: patching node labels, restricted by a policy to the GPU workload label and `gpu.cozystack.io/*`; restarting the device plugin pod on a node; and deleting unscheduled VMIs that reference its devices.
- Visibility. A tenant sees the node name of its own devices and, through the remaining budget, its siblings' GPU count under a shared parent budget (section 5). Any authenticated user sees each class's `free`, `maxFreePerNode` and conditions, and can follow them over time.

## Failure and edge cases

Each case gives what is seen and what to do.

- A tenant's order is refused at create → the message names the class, the count and the limit, or, for a vGPU class, that the tenant is not entitled to vGPU; order a smaller count, another class, or ask for more budget.
- A device stays `Pending` after an order → its condition names the shortage; it binds when capacity or budget frees up, or the tenant deletes it.
- A VM with devices does not start → the webhook message names each device and its node and the reason. When the reason is that the VM's devices, template devices included, cannot be brought onto one node, either no node can take all of them or a device of a `Stay` class is on a cordoned node while others need gathering. Then uncordon that node, free capacity on one node, drop a device the VM does not need, or, within one class, replace them with one device of the combined `count`. A refusal for the admission timeout usually clears on KubeVirt's retry.
- A node is drained or cordoned → under `Move` its unattached devices move when another node of their class has room, and their VMs start there; with no room, or under `Stay`, the devices stay bound and in the usage records and their VMs wait; uncordon it, add capacity, or delete the node.
- A cordoned node is deleted while a drained VM's VMI still waits for it → the controller deletes that unscheduled VMI, the device moves to another node of its class or to `Lost`, and what the VM does next follows its `runStrategy` for a VMI that was deleted, not one that finished: in KubeVirt v1.9.0 ([`pkg/virt-controller/watch/vm/vm.go`](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-controller/watch/vm/vm.go)) `Always` and `Once` create a new VMI when none exists, while `Manual` and `RerunOnFailure` wait for a start request, so the VM stays stopped until the tenant starts it.
- A node dies but stays registered → after the grace period its attached devices go to `Lost` and leave the usage records, and its unattached ones move or go to `Lost` (section 2); once the node is tainted `out-of-service`, its `Node` deleted or its pods force-deleted, KubeVirt fails the VMIs and the devices are rebound. If the taint or the force-delete comes before the grace period ends, the next VMI of an `Always` or `RerunOnFailure` VM gathers its now unattached devices onto a `Ready` node with room (section 3) and starts there at once. That holds for a VM's held devices. A pool worker takes only a spare device of its pool, template devices follow their own placement, and a VMI pinned by the `Stay` rule waits on its cordoned node (all in section 3). After a `Node` deletion the VM starts on a retry instead, once the controller has moved the devices. Only when no node fits is the VMI refused, and then it is not created at all. KubeVirt v1.9.0 retries the creation with backoff for `Always`, which deletes the failed VMI and starts a new one, and for `RerunOnFailure`, whose failed VMI leaves a start request that stays until a VMI is created ([`vm.go`](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-controller/watch/vm/vm.go)); such a VM starts once a node has room for its set. The retry goes through the VM controller's rate-limited queue, whose per-item backoff grows to 1000 seconds, so the start can come up to about 17 minutes after room appears. `Once` and `Manual` VMs keep their failed VMI, so no new one is created: a `Once` VM stays stopped, and a `Manual` VM starts when the tenant starts it. The failed VMI of a `Once` or `Manual` VM leaves its devices unattached, so after the grace period they move to another node of their class, or to `Lost` when none has room. When the hoster enables the optional `node-remediation` package of [Node fencing](../node-fencing/README.md), it sets the taint after Medik8s NodeHealthCheck and Fence Agents Remediation power the node `off` through its BMC; when the package is off or the node is not fenced, for example because the BMC lost power with the node, the manual procedure of that proposal applies.
- A node loses capacity while a VM's VMI waits for it unscheduled → the controller deletes that VMI so its devices can move; an `Always` or `Once` VM starts again once its devices have moved or been rebound, a `Manual` or `RerunOnFailure` VM stays stopped until the tenant starts it.
- A GPU fails under a running VM → the device stays `Attached` and in the usage records while the VM runs; when the VM restarts it is refused there, and the device moves or goes to `Lost`.
- A layout change does not apply → the node shows `LayoutPending`; drain it. A VM with a raw `name` entry on the node holds the change until it stops.
- A Talos GPU pool, ComputePlane GPU groups included, is created after `gpu.rawResourceNames: forbidden` → its worker VMIs are refused by the raw-name policy; use a Kairos pool with `deviceSelector`, or allow raw names again while the cluster has Talos GPU pools.
- A pool with `os: talos` and a `deviceSelector` or `deviceTemplate` fails to render → Talos pools take GPUs only through `name` entries; use a Kairos pool for devices.
- A Kairos worker VMI is refused because its device's class `mode` differs from the pool's `gpu.cozystack.io/mode` label → set `kairos.gpuMode` to the class's mode, or select devices of a class in the pool's mode.
- A tenant's VM is `Pending` while its device is `Bound` and in the usage records → a raw-name VM took the unit; this can happen only while raw names are allowed, and forbidding them prevents it.
- A pool worker is refused → no matching device is free, because of a surge machine during a rollout, devices still `Pending` or `Lost`, matching devices only on nodes that are not `Ready`, or another pool or a VM using the matching devices; wait, add devices, give each pool its own labels, or lower `maxReplicas`.

## Testing

- Unit tests for the binder and both webhooks: capacity with raw-name usage, budgets over the ancestor walk, the phase table, every move and refusal in sections 2 and 3, concurrent starts, and the pod pin after a LiveUpdate sync.
- Webhook tests for cordons, templates and NICs: under `Move` a device moves at VMI creation and the pod gets a required node affinity, an attached device with an unstarted pod moves for `Always` and waits for `Manual`, on lost capacity the unscheduled VMI is deleted and an `Always` VM restarts while a `Manual` VM stays stopped, the devices of one VM move, go to `Lost` and are rebound as a set, devices of two classes on different nodes, and a device whose node has no room for the VMI's template device, are moved onto one node at VMI creation, refused when no node fits, a `Stay` device on a cordoned node pins the VMI when it holds all of the VM's devices and refuses it when other devices need gathering, devices on a node that is not `Ready` are gathered, two VMIs whose sets share a device do not both gather it, a VMI with only template devices gets one node with room for all of them, and under `Stay` a single device waits; a `deviceTemplate` device is created, refused when it does not fit, deleted with its VMI, deleted when its VMI is refused later in admission, reused on a repeated call, and on a dry run neither created nor attached nor moved; a VM with a GPU device and `nvidia.com/hostdev` passes.
- Chart tests for `kubernetes-nodes`: a `deviceSelector` or `deviceTemplate` with `os: talos` fails to render, and a Kairos pool gets the `gpu.cozystack.io/mode` label on its worker VMI template.
- Quota tests for vGPU: an entitled tenant with no class key orders, an unentitled one is refused, a child's flag without the parent's is refused, `total` caps vGPU, a cleared flag keeps held devices and refuses the next template device, and `remainingBudget` is 0 without the entitlement.
- Admission tests for the schema rules, `maxReplicas`, class lifecycle, tenant finalizers, the agent and controller restrictions, and the raw-name policy and quota keys under `forbidden`, including a tenant super-admin whose direct Pod or VMI is refused by RBAC, whose VirtualMachine with a GPU entry and no device reference gets its VMI refused, and a pod requesting a GPU resource name from any creator other than virt-controller.
- Controller tests for the class metrics: capacity, bound units and the `Lost` count match the devices, and none of them appears in `GPUClass` status.
- Binder tests: a node with a `NoSchedule` taint that virt-launcher pods do not tolerate gets no binds, moves or gathers and its units leave `free`, while the Fence Agents Remediation taint alone does not make a node cordoned.
- Admission tests for the reserved label of the GPU usage monitor, refused on any object in a tenant namespace, on create and update, including a virt-launcher pod and a DataVolume that get it from a VM template.
- Controller tests for the usage record: present exactly while `Bound` or `Attached`, independent of tenant labels, not reported twice, and kept through reconciles of its `WorkloadMonitor` and of the `Workload` controller.
- Changed existing tests: cozy-lib and tenant chart quotas, `quota_test.go`, `providers_test.go`, `WorkloadMonitor`, and the GPU wiring tests in `bundles_gpu_kubevirt_wiring_test.yaml`.
- E2E with passthrough GPUs on the GPU runner: order, attach, stop, confirm another tenant cannot take the GPU, start again, and check the usage record.
- Manual on SR-IOV vGPU hosts: both modes of one model in one cluster, profiles across reboots, a layout change through a drain, and adoption from `default` and from `vgpu`, on Ubuntu and Kairos hosts.

## Rollout

1. `GPUClass`, `GPUDevice`, `GPUNode` and its agent, the binder and its metrics with a scrape object for platform monitoring, both webhooks, the policies, the option sources, `device` and `deviceTemplate` entries in `vm-instance`, the `gpu.vgpu` Tenant flag, budgets and usage records, on the existing variants. Before tenants order vGPU devices, the `gpu.vgpu` flag is set from the root tenant down.
2. `deviceSelector` and `deviceTemplate` in `kubernetes-nodes`, after cozystack/cozystack#4911.
3. The consumers, on top of steps 1 and 2: `kairos-vgpu-workers` adds Kairos GPU pools, `kairos.gpuMode` and the mode label, and `vgpu-vms` adds GPU guest images for VMs with `device` entries. A cluster with Talos GPU pools stays on raw names and has no hard hold guarantee.
4. The host layer: the node layout, profile writes, node labels, the device plugin with NVIDIA/kubevirt-gpu-device-plugin#192 applied downstream, and Kairos vGPU hosts (the bundle on Kairos and a vGPU Manager image for it).
5. Budgets through #48, only if its gate takes over subtree caps for GPU keys.
6. The upstream plugin in place of the downstream patch once NVIDIA/kubevirt-gpu-device-plugin#192, or what replaces it, ships upstream, and `dra` classes once NVIDIA's DRA driver serves vGPU to KubeVirt; an offer moves to a new class of the other source, since a class's kind of source cannot change. The plugin change touches only the host layer and the `resourceName` of affected classes, with raw-name consumers of a renamed resource moved in the same window. A `dra` class is a new class, so tenants move to it by ordering new devices.

## Open questions

- Release on tenant suspension. A hoster may want a suspended tenant's GPUs released after a GPU-specific grace period; Cozystack has no platform-wide notion of suspension yet.
- Names. Aligning with #35 would make `GPUDevice` into `GPUClaim`; the meaning is the same.
- Packing policy. Fullest-node packing is the default; hosters may need spreading across failure domains as a class option.

## Alternatives considered

DRA as the whole API, with `DeviceClass` as the catalog and `ResourceClaim` as the tenant object, or DRA first for passthrough with vGPU later. Rejected for now: a non-Pod `reservedFor` entry does keep a claim allocated for a stopped VM, but the NVIDIA DRA driver does not serve vGPU yet and its passthrough is alpha, and vGPU is the case hosters sell first. DRA is the planned backend under `GPUClass` (section 8), mapped one to one onto `GPUDevice`, so tenants order devices of a `dra` class through the same API.

A cluster-scoped object per reservation unit, like `IPAddress` in #35. Rejected for the device-plugin backend, where kubelet picks the physical device and such an object would claim a precision the backend does not have. `GPUNode` gives the per-device view where it is real.

Tenant quota on the resource name only. Stock `requests.<resource>` quotas need no new code, but they cannot hold a GPU for a stopped VM and still refuse orders only at pod creation.

Placeholder pods that hold the device until the VM starts. Rejected because preemption picks victims by priority, not by owner, so one tenant's VM could evict another tenant's placeholder.

Class-level reservations for worker pools, without per-worker devices. Rejected because a pool would get a promise of capacity, not GPUs.

A cluster-wide reservation per tenant and class, a count without a node. It needs no binding, moves or pod pin, and for single-GPU offers it would be enough. Rejected because the count holds only in sum. With free units spread one per node, a held set of two fits nowhere. A vGPU profile exists only on the nodes the layout gives it. A lost node silently shrinks every tenant's promise instead of showing which devices are `Lost`.

NVIDIA vgpu-device-manager as the profile writer. At v0.5.1 it writes mdev profiles only and has no path for `current_vgpu_type` on SR-IOV VFs, and the pull requests that added VF support were closed unmerged, NVIDIA/vgpu-device-manager#139 as not planned, and NVIDIA/vgpu-device-manager#195 after NVIDIA named gaps on the KubeVirt side and had no resources for that test round. The node agent writes VF profiles instead; nodes on the mdev path keep vgpu-device-manager (section 6).

KubeVirt's built-in device plugin for VFs (kubevirt/kubevirt#16890), with the sandbox plugin for passthrough. It ships in KubeVirt v1.9 and needs no patch, but it gives every vGPU profile of a model one resource name, so classes tell profiles apart only by node. On GPUs whose whole GPU and VFs share a PCI ID it clashes with the sandbox plugin, so both modes of such a model need the built-in plugin for passthrough too, both modes share one name, and raw resource names would have to be forbidden on any cluster with vGPU nodes. Switching between it and the sandbox plugin is a maintenance window each time. Rejected in favour of the downstream patch, whose cost is carrying one patch until upstream settles and running the plugin privileged with NVML access.
