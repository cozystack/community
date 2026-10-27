# GPUs as a tenant resource

- **Title:** `GPU classes, per-class tenant budgets and the host layer under them, then GPU holds for stopped VMs`
- **Author(s):** `@lexfrei`

## Overview

A tenant who wants a GPU today writes a device-plugin resource name such as `nvidia.com/AD102GL_L40S` into `gpus` of a VM or a worker pool and hopes one is free. The `gpu` option source shows how many of each name the nodes advertise and on which nodes, but nothing says which model, mode or profile a name stands for, nothing holds a GPU while the VM is off, and an order that does not fit is accepted and then sits in `Pending`. A hoster selling GPUs needs the opposite: a catalog of GPU models and modes, budgets per offer, the same handling for VMs and managed Kubernetes worker pools, a usage record of who used what, which the hoster's external billing system reads, and, for some offers, GPUs a tenant keeps while its VM is stopped.

The proposal comes in two steps. Step 1 has no tenant object. It adds a host layer that ends at one resource name per offer, `GPUClass` as the hoster's catalog in the existing `cozystack.io` group, `gpus` entries in `vm-instance` and `kubernetes-nodes` that name a class instead of a resource name, budgets per class that the tenant quota gate of [#48](https://github.com/cozystack/community/pull/48) checks when an application is written, and usage taken from the `Workload` records Cozystack already keeps for virt-launcher pods. Step 2 adds holding: `GPUClaim`, a tenant's GPU kept on a node while its VM is stopped, with the binder, moves, lost capacity and the launcher pod gate that a hold needs. The fields and phases of step 2 enter the API only in step 2.

NVIDIA's vGPU stack changes quickly. Within the last few releases the driver branches, the way vGPUs are exposed (mediated devices or SR-IOV virtual functions), the KubeVirt device plugins and the licensing rules all changed. This proposal describes that stack as it was in October 2026. Expect it to be updated as NVIDIA and KubeVirt move, with a decision record under it for each change of course, and check the versions named here against your own before relying on them.

## Scope and related proposals

- [`kairos-vgpu-workers`](../kairos-vgpu-workers/README.md) gives managed Kubernetes worker pools on Kairos a worker image per GPU mode and delivers the vGPU license token to the workers. [GPU virtual machines](../vgpu-vms/README.md) (`vgpu-vms`) covers GPU guest images for VMs. Both name a `GPUClass` in their `gpus` entries from step 1 on, and take held claims once step 2 ships; the links resolve once they merge.
- [Tenant quotas as reservation limits](https://github.com/cozystack/community/pull/48) makes declared sizes the quota authority and refuses an application that does not fit its pool when it is written. GPU budgets are ordinary keys of that gate (section 5). Until it ships, an interim check does the same for GPU keys only.
- [Node fencing](../node-fencing/README.md), proposed alongside this one, adds an optional package that fences a dead node through its BMC and taints it `out-of-service`, so KubeVirt fails its VMIs and step 2 rebinds their `Lost` claims. The link resolves once it merges.
- [IP addresses as a first-class resource](https://github.com/cozystack/community/pull/35) solves a similar problem for addresses with a class, a tenant claim and a backend contract.
- Host-side discovery of vGPU VFs grouped by profile is [NVIDIA/kubevirt-gpu-device-plugin#192](https://github.com/NVIDIA/kubevirt-gpu-device-plugin/pull/192), still open. Section 2 ships it downstream in the host layer, behind a boundary the rest of the design does not cross.

## Decisions

- [0017. The vGPU entitlement is a Tenant flag stamped on the namespace, not a budget key](./decisions/0017-the-vgpu-entitlement-is-a-tenant-flag.md)
- [0016. Holding a GPU for a stopped VM is step 2, and stays GPU-specific until DRA can carry it](./decisions/0016-holding-is-step-2-and-stays-gpu-specific.md)
- [0015. Node allocatable is the GPU inventory, and there is no `GPUNode`](./decisions/0015-node-allocatable-is-the-gpu-inventory.md)
- [0014. In step 1 a `gpus` entry names a class, budgets count declared entries, and usage is the virt-launcher pod's `Workload`](./decisions/0014-step-1-names-a-class-and-holds-nothing.md)
- [0013. The GPU kinds live in `cozystack.io`, with no group of their own](./decisions/0013-the-gpu-kinds-live-in-cozystack-io.md)
- [0012. The VMI webhook only checks and records claims, and the controller places them while the launcher pod waits at a scheduling gate](./decisions/0012-the-vmi-webhook-checks-and-records-and-the-controller-prepares.md)
- [0011. A `GPUClaim` owns its usage record, and no monitor or reserved label guards it](./decisions/0011-a-gpuclaim-owns-its-usage-record.md)
- [0010. `GPUClass` carries the tolerations that `free`, binding and the launcher pod read](./decisions/0010-gpuclass-carries-the-tolerations-binding-reads.md)
- [0009. `GPUClass.mode` never changes, and a pool names its class](./decisions/0009-gpuclass-mode-is-immutable-and-a-pool-names-its-class.md)
- [0008. The GPU controller gets its node-label right with the host layer, not before](./decisions/0008-the-controller-gets-its-host-layer-rights-with-the-host-layer.md)
- [0007. Whoever can write a VM uses the GPUs of its class entries, while held claims stay with tenant admins](./decisions/0007-whoever-can-write-a-vm-orders-its-template-claims.md)
- [0006. A `Stay` pin holds through `NotReady` and taints, and breaks when the node cannot serve the claim](./decisions/0006-a-stay-pin-yields-only-to-a-node-that-cannot-serve-the-claim.md)
- [0005. The taint rule skips exactly the three taints that mirror a cordon or readiness](./decisions/0005-the-taint-rule-skips-exactly-three-kubernetes-taints.md), superseded by 0010
- [0004. GPUNode health is what kubelet lists as allocatable, and the node reports its GPU resource names and per-function counts](./decisions/0004-gpunode-health-is-kubelet-allocatability.md), superseded by 0015
- [0002. GPU classes serve VMs only where gpu-operator serves GPUs to VMs, while claim finalizers and usage records always run](./decisions/0002-gpuclaim-serves-vms-only-where-gpu-operator-serves-gpus-to-vms.md)
- [0001. A class whose resource name KubeVirt does not permit is refused at VMI admission](./decisions/0001-a-claim-outside-permittedhostdevices-is-refused-at-admission.md)

## Context

### What exists

The `cozystack.gpu-operator` package has three variants, selected by `bundles.iaas.gpuOperatorVariant` (Cozystack's [GPU guide](https://cozystack.io/docs/virtualization/vgpu/)). `default` binds whole GPUs to `vfio-pci` for VMs, `vgpu` runs the NVIDIA vGPU host driver, and `container` serves GPUs to pods. For the two VM variants the platform writes the `HostDevices` feature gate and, when it has entries, a `permittedHostDevices` table into the KubeVirt CR (`packages/core/platform/templates/bundles/iaas.yaml`). Passthrough GPUs are advertised by NVIDIA's sandbox device plugin.

How NVIDIA's vGPU host driver exposes vGPUs depends on the GPU, the host OS and the driver branch. Older setups use mediated devices (mdev). SR-IOV GPUs on current releases, such as Ubuntu 24.04 with the vGPU 17 or newer branch that Cozystack's guide uses, get VFs, and each VF gets a profile when `current_vgpu_type` is written in its sysfs. KubeVirt v1.9, which Cozystack bundles, advertises such VFs through virt-handler's own device plugin ([kubevirt/kubevirt#16890](https://github.com/kubevirt/kubevirt/pull/16890)), matched by PCI ID. Observed on L40S with SR-IOV vGPU: the VF shows `10de:26b9` in the guest, the PF's ID, and `permittedHostDevices` selects VFs by `10DE:26B9`. On GPU Operator nodes nothing in Cozystack writes VF profiles; the `cozystack-nvidia-vgpu-restore` boot unit from cozystack/ansible-cozystack#70 covers only hosts with a host-installed driver.

Both consumers take GPUs the same way: `vm-instance` and `kubernetes-nodes` read `gpus` as a list of `{name: <resource name>}`, one entry per GPU. The deprecated `gn1.*` instance types carry GPU entries of their own in KubeVirt's shape, `{name: gpu1, deviceName: nvidia.com/A400}`, and neither default `permittedHostDevices` table lists that name. Tenant quotas become a namespace `ResourceQuota`, which refuses a GPU only when virt-launcher's pod is created, and the usage records (`Workload`) show a GPU only while a VM runs.

### The problem

- A tenant sees resource names, not offers. The dashboard's `gpu` option source lists the names KubeVirt permits and how many of each are available on which nodes; they say nothing about mode, profile or price.
- A GPU cannot be held. When a VM stops, its GPU goes back to the pool and the next start can fail.
- An order that does not fit is accepted, and the tenant learns it from pod events.
- Nobody can tell which tenant uses a physical GPU without reading pods on the node.
- In the `vgpu` variant a node labelled for passthrough can serve whole GPUs of another model, but a model whose whole GPU and VFs share a PCI ID, such as L40S, is offered either whole or as vGPU, not both, because two device plugins would both take that ID. The limit is per GPU model, not per cluster.
- VF profiles on GPU Operator nodes are written by hand after every reboot.

Step 1 answers the first, fifth and sixth problems, the third for budgets and the fourth per tenant and class. Step 2 answers the second, and the third for capacity, since an order is then checked against free units.

## Goals

Step 1:

- One cluster can have passthrough and vGPU nodes of the same GPU model, with vGPU profiles applied declaratively and kept across reboots, and every offer advertised under a resource name of its own.
- A hoster publishes a catalog of GPU classes, each with a model, a mode (passthrough or vGPU) and, for vGPU, a profile. Tenants read the catalog with free capacity and their remaining budget.
- VMs and worker pools name a class, not a resource name, and a tenant's budget per class and in total is checked when the VM or pool is written, with a message naming the class and the limit.
- Usage records (`Workload`) show which tenant runs how many GPUs of which offer in which VM or worker, which an external billing system reads. Cozystack charges nothing and stores no prices.
- Existing `gpus` entries keep working, with a documented path to class entries.

Step 2:

- A tenant holds a number of GPUs of a class within its budget, attached or not, until it releases them, and a restarted VM gets its held claims back. The hold is a hard guarantee once `gpu.rawResourceNames` is `forbidden`, which needs every GPU consumer on class or claim entries; until then it is not guaranteed.
- The catalog can later describe MIG, other vendors and DRA-backed devices without changing the tenant API.

### Non-goals

- Prices and charging. They live in an external billing system, keyed by class name.
- The vGPU guest driver, the license server and license tokens. The hoster provides them (section 7).
- The catalog UI. This proposal provides the data a portal reads.
- GPUs for pods inside tenant clusters (gpu-operator in a tenant cluster, HAMi).
- Time-slicing one GPU between tenants, and live migration of GPU VMs.
- A per-device inventory naming the physical GPU or VF behind each VM ([0015](./decisions/0015-node-allocatable-is-the-gpu-inventory.md)).

## Design

Sections 1 to 8 are step 1. Section 9 is step 2 and is the only place that adds fields, kinds or phases for holding.

### 1. API group and names

The GPU kinds join the existing `cozystack.io` group, which already holds the hoster-side `SchedulingClass` and `Package` and the tenant-namespace `Workload` and `WorkloadMonitor`; there is no `gpu.cozystack.io` group ([0013](./decisions/0013-the-gpu-kinds-live-in-cozystack-io.md)). Step 1 adds one kind, `GPUClass`; step 2 adds `GPUClaim`.

Every other name follows the group:

| Name | Step | Use |
| --- | --- | --- |
| `<class>.gpuclass.cozystack.io/devices` | 1 | budget of one class in `resourceQuotas` (section 5) |
| `gpuclass.cozystack.io/devices` | 1 | budget over every class |
| `cozystack.io/gpu-reference` | 1 | label on a VMI template with class or claim entries, which KubeVirt copies to the VMI and its pods |
| `cozystack.io/gpus` | 1 | annotation on that template listing the entries |
| `cozystack.io/gpu-<vendor>-<device>` | 1 | host-layer node label carrying a model's mode or profile (section 2) |
| `cozystack.io/gpu-layout-pending` | 1 | node label marking a pending layout change (section 2) |
| `namespace.cozystack.io/vgpu` | 1 | namespace label carrying the vGPU entitlement, like the other module labels (section 6) |
| `cozystack.io/gpuclass-protection` | 2 | finalizer on a class in use (section 9) |
| `cozystack.io/gpuclaim-protection` | 2 | finalizer on a claim a VMI uses |
| `cozystack.io/gpuclaim-template` | 2 | label marking a claim the controller made for a class entry |
| `cozystack.io/gpuclaims` | 2 | scheduling gate on a launcher pod |

The quota keys follow DRA's `<class>.deviceclass.resource.k8s.io/devices`, so a class key never collides with a resource name a device plugin advertises, and a key without a class prefix counts every class.

### 2. Host layer

The host layer is everything between the GPUs on a node and the resource names classes point to: the host driver, VF profiles, node modes, and the device plugin that advertises GPUs to KubeVirt. It ends at one resource name per offer: a whole L40S in passthrough and an L40S-24C vGPU are two names, and one name never stands for two offers. That is its whole contract with the API above it. `GPUClass`, quotas, the GPU webhooks and usage records use no plugin-specific name, discovery format or configuration. A different plugin, or a different version of the one chosen here, changes only this layer and the `resourceName` of affected classes. Tenants keep their class entries; raw `name` entries and raw quota keys on a renamed resource move to the new name in the same maintenance window, or their consumers move to class entries first. It ships first and alone, with no tenant API.

The host layer does not depend on the host OS: it needs the vGPU host driver, `vfio-pci` and the device plugin, wherever they run. Which OSes Cozystack supports as hosts of the management cluster is decided outside this proposal. Today vGPU hosts run Ubuntu with `bundles.system.variant: isp-full-generic`, where the vGPU host driver and the patched plugin run. Cozystack's GPU guide does not recommend Talos for vGPU, so this layer does not cover Talos hosts; they keep passthrough through the `default` variant as today. NVIDIA AI Enterprise lists vGPU for Compute on Linux with KVM as supported only through specific hypervisor vendors and, on Ubuntu and RHEL with KVM, for single-node hosts only ([support matrix](https://docs.nvidia.com/ai-enterprise/release-8/latest/support/support-matrix-8/8.2.html)), so support for such a host is a matter between the hoster and NVIDIA.

Until the layer ships, a cluster keeps the modes its variant gives it today, one per model for models whose whole GPU and VFs share a PCI ID, and vGPU classes keep the experimental status the GPU guide gives the `vgpu` variant: the hoster writes profiles and node labels by hand. On clusters without a layout, which includes the `default` variant and Talos hosts, node labels stay hand-written. Where one resource name still covers two offers, as with KubeVirt's built-in plugin, which gives every vGPU profile of a model one name, the hoster can publish only one class for that name (section 3).

The device plugin is the decision reviewers should look at first. Cozystack ships NVIDIA's kubevirt-gpu-device-plugin with NVIDIA/kubevirt-gpu-device-plugin#192 applied downstream, as the sandbox device plugin of the `vgpu` variant, until NVIDIA/kubevirt-gpu-device-plugin#192 merges upstream or upstream takes another approach. NVIDIA/kubevirt-gpu-device-plugin#192 adds discovery of vGPU VFs on the vendor-specific VFIO framework and advertises them with one resource name per profile, next to the passthrough names the plugin already advertises for GPUs bound to `vfio-pci`. One plugin then serves both modes of every model under distinct names, so the per-model limit of the problem list goes away. The branch is validated on hardware. Its upstream review has had no maintainer response since 2026-07-10, which is why it ships as a downstream patch rather than waiting. It reads profile names from sysfs `creatable_vgpu_types` first and falls back to NVML on a card whose VFs all carry a profile, so the platform runs the plugin as its own DaemonSet with the host driver's `libnvidia-ml.so.1` and the `/dev/nvidia*` nodes mounted, privileged; the GPU Operator's DaemonSet has no field for those mounts. The patch leaves when NVIDIA/kubevirt-gpu-device-plugin#192 merges upstream, in this or another form, or when the NVIDIA DRA driver serves vGPU VFs to KubeVirt (kubernetes-sigs/dra-driver-nvidia-gpu#1402); a merged NVIDIA/kubevirt-gpu-device-plugin#192 changes only this layer, while a move to DRA adds new `dra` classes (section 10). KubeVirt's built-in plugin was the other candidate (Alternatives).

The layer adds a node layout to the `vgpu` variant, in platform values. For each node selector it names a mode and, for vGPU, the profile of each GPU model. Nodes outside every selector are passthrough, through the `defaultWorkload: vm-passthrough` the layout renders. With a layout:

- The platform runs the patched plugin and renders the KubeVirt `permittedHostDevices` entries for the names it advertises, as entries with an external provider. Other vendors' entries, such as an FPGA, stay as written.
- A node agent writes the VF profiles at boot and when the layout changes. The controller sets each node's workload label and one label per GPU model, `cozystack.io/gpu-<vendor>-<device>` (for L40S `cozystack.io/gpu-10de-26b9`), to the profile or to `passthrough`.
- The plugin does not pick up VFs that gain a profile after it started, so the node agent restarts the plugin pod on its own node after it has written profiles there, for example after a reboot. Running VMs are not affected.

The controller gets the right to patch those node labels, restricted by a policy to the GPU workload label and `cozystack.io/gpu-*`, with this layer and not before ([0008](./decisions/0008-the-controller-gets-its-host-layer-rights-with-the-host-layer.md)).

The layout and mdev GPUs coexist: GPUs on the mdev path keep today's configuration through `vgpu-device-manager`, and the agent writes profiles only on nodes that use VFs. New deployments of SR-IOV GPUs on current host releases use VFs.

A layout change on a node, of mode or of profile, waits until the node has no GPU VMs. The controller labels the node `cozystack.io/gpu-layout-pending`, and running VMs keep their GPUs until the hoster cordons and drains the node; the cordon is what keeps new GPU VMs off it, since nothing in step 1 places a launcher pod. A GPU VM created before Cozystack switched to `LiveMigrateIfPossible` needs one restart before the drain can evict it. A node whose hand-written profiles already match the layout is not a change. A `vgpu` cluster adopts the layout with vGPU selectors that cover its existing vGPU nodes, and labels those nodes `vm-vgpu` before setting it, so the layout's passthrough default does not change them in between. Its vGPU classes, raw `name` entries and raw quota keys move from the hand-written VF names to the plugin's per-profile names in one maintenance window, or the raw-name consumers move to class entries first. KubeVirt's built-in plugin and the patched plugin report different device IDs for the same VF, and kubelet would otherwise hand out VFs in use, so affected nodes are cordoned and free of GPU VMs until the patched plugin has registered there.

### 3. GPU classes

```yaml
# Hoster. One per offer. Cluster-scoped, readable by every authenticated user.
apiVersion: cozystack.io/v1alpha1
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
  tolerations:            # taints of this offer's nodes that its VMs tolerate
  - key: dedicated
    value: gpu
    effect: NoSchedule
  source:
    devicePlugin:
      resourceName: nvidia.com/NVIDIA_L40S-24C
status:
  free: 12
  maxFreePerNode: 4       # most free units on one node
```

A class maps an offer to a backend through a `source` union. The first release has one source, `devicePlugin`; `dra` is reserved for section 10. A class is one resource name, and two classes cannot have the same one: a class whose `resourceName` another class already has gets a `ResourceNameConflict` condition and is not offered, so a budget on one class cannot be sidestepped through another, and a usage record's resource name leads to exactly one class. The nodes of a class are the nodes that advertise its resource name. Class names are DNS labels, since they become part of quota keys.

`vendor`, `model`, `mode`, `profile`, `memory` and the kind of `source` never change, so a class name stands for one offer for the class's lifetime, and whatever names a class, a VM, a worker pool or a billing system, knows the offer from the name ([0009](./decisions/0009-gpuclass-mode-is-immutable-and-a-pool-names-its-class.md)). A different offer is a new class, and a hoster does not reuse a deleted class's name for another offer, since tenants' budgets and billing records are keyed by it. `displayName`, `maxCount`, `resourceName` and `tolerations` can change: `resourceName` for changes in the host layer, `tolerations` when the hoster retaints its GPU nodes. A change applies to VMs started after it; a running VM keeps the resource name it started with, and its launcher pod the tolerations. Deleting a class refuses the next start of every VM that names it and leaves running VMs alone.

How many GPUs of a class one VM may take is the hoster's `maxCount` on the class. For vGPU classes it follows NVIDIA's multi-vGPU matrix for the board and the profile series. Compute (C-series) profiles follow the [NVIDIA AI Enterprise matrix](https://docs.nvidia.com/ai-enterprise/release-8/latest/infra-software/vgpu/features/multi-vgpu-support.html), which allows several vGPU for Compute devices per VM on L40S under the Linux with KVM hypervisors it supports (section 2), so the `l40s-24c` class above sets `maxCount: 2`. Graphics (Q-series) profiles follow the vGPU 20 release notes for Linux with KVM, which allow several Q-series vGPUs per VM on L40S and offer no C-series profiles. For passthrough classes it is at most the GPUs per node. NVIDIA requires all vGPUs in one VM to share board type and series. Cozystack keeps it simpler and stricter: a VM takes vGPUs of one class only, although NVIDIA would also allow, say, A40-48Q next to A40-16Q. `maxCount` defaults to 1 for vGPU classes; on a passthrough class, unset means no limit beyond the GPUs of one node.

`tolerations` lists the taints of the class's nodes that its VMs tolerate, such as a taint that reserves GPU nodes for GPU workloads, in the shape of a pod's with the operators `Exists` and `Equal` only. The launcher pod of a VM that names the class gets them (section 4), and `free` reads them ([0010](./decisions/0010-gpuclass-carries-the-tolerations-binding-reads.md)).

`status.free` is the class's allocatable count summed over its nodes that are `Ready`, not cordoned, and have no `NoSchedule` or `NoExecute` taint the class does not tolerate, minus what pods on those nodes request of the resource name, and no node counts below zero; `status.maxFreePerNode` is the largest such figure on one node. A class whose `resourceName` the KubeVirt `permittedHostDevices` table leaves out gets a `ResourceNameNotPermitted` condition, so the hoster sees the gap before a tenant's VM is refused ([0001](./decisions/0001-a-claim-outside-permittedhostdevices-is-refused-at-admission.md)). The taint rule leaves out exactly the three taints Kubernetes sets itself for a cordon or a node that is not `Ready`, `node.kubernetes.io/unschedulable`, `node.kubernetes.io/not-ready` and `node.kubernetes.io/unreachable`, which the first two rules already cover; any other taint under that prefix counts, a pressure taint or `out-of-service` included ([0005](./decisions/0005-the-taint-rule-skips-exactly-three-kubernetes-taints.md)). Both figures come from the `Node` and `Pod` objects alone, with no inventory kind ([0015](./decisions/0015-node-allocatable-is-the-gpu-inventory.md)). In step 1 they describe the moment, not a promise: nothing is held, so a unit counted free can be taken by the next VM to start.

### 4. Class entries in VMs and worker pools

VMs and worker pools name a class in `gpus`:

```yaml
# vm-instance
gpus:
- className: l40s-24c
  count: 2
---
# kubernetes-nodes: every worker of the pool gets the entry
gpus:
- className: h100-pt
  count: 1
```

A `vm-instance` or `kubernetes-nodes` entry has exactly one of `name` or `className`, and a list holds only `name` entries or only class entries. `count` defaults to 1. The new entries are KubeVirt-only; Proxmox pools keep refusing `gpus`. On a cluster where gpu-operator does not serve GPUs to VMs, `vm-instance` and `kubernetes-nodes` refuse class entries at render, since no webhook would resolve them ([0002](./decisions/0002-gpuclaim-serves-vms-only-where-gpu-operator-serves-gpus-to-vms.md)). ComputePlane is not yet an equal consumer: the module does not let a tenant pick the worker OS, so its GPU groups are Talos pools on `name` entries, and moving them to class entries is separate work.

The chart renders only the entries, on the VMI template, as the `cozystack.io/gpus` annotation next to the `cozystack.io/gpu-reference` label, which KubeVirt copies from the VMI to its pods. A mutating webhook on VirtualMachineInstance creation, which sees only VMIs carrying the label, resolves each entry to its class and writes one `gpus` entry per GPU unit with the class's resource name. These entries have to be complete at admission: KubeVirt renders the launcher pod's resource requests from the VMI, and nothing but KubeVirt itself can change a VMI's spec afterwards. The webhook refuses to start the VMI, and names the class, when an entry:

- names a class that does not exist, is being deleted, or has a `ResourceNameConflict` condition;
- is a vGPU class next to another vGPU class, or brings the VMI's GPUs of its class above `maxCount`;
- names a vGPU class in a namespace without the vGPU entitlement (section 6);
- names a class whose `resourceName` is not in the KubeVirt `permittedHostDevices` table, when the table is set, since KubeVirt would otherwise accept the VMI and leave it `Pending` with no pod for good ([0001](./decisions/0001-a-claim-outside-permittedhostdevices-is-refused-at-admission.md)).

A GPU entry is any `gpus` entry, any GPU resource name in the VMI's `spec.domain.resources`, and any `hostDevices` entry whose device name is a GPU resource name, on the VMI or from its instance type. GPU resource names are always the union of three sets: the hoster's list `gpu.resourceNames`, empty unless the hoster sets it; the resource names of `GPUClass` objects; and the NVIDIA (`10DE`) and mdev entries of the KubeVirt `permittedHostDevices` table. None of them replaces another, so a vfio GPU missing from the table and a name that only pods request, which the hoster lists, still count. A NIC is not a GPU entry: a ConnectX or BlueField VF, which NVIDIA's network operator advertises as `nvidia.com/hostdev`, can go next to a GPU class entry for GPUDirect RDMA. The webhook also refuses a labelled VMI that carries GPU entries of its own, from any source, including an instance type or a VM written directly by a tenant super-admin. A second mutating webhook, on the creation of a virt-launcher pod that carries the reference label, adds the classes' `tolerations` to the pod, and to the temporary pod KubeVirt creates to bind `WaitForFirstConsumer` volumes, so a local volume binds on a node the VM can use. They go on the pod and not on the VMI, because with `vmRolloutStrategy: LiveUpdate`, which Cozystack sets, KubeVirt v1.9.0 replaces a VMI's tolerations with the VM template's on every sync ([`vm.go`](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-controller/watch/vm/vm.go), `handleTolerationsChangeRequest`). Both webhooks fail closed, so their outage blocks the start of VMs with class entries and no other VM. The VMI webhook reads the classes and the KubeVirt CR from the API server, not from a cache, since the controller's replicas serve admission and a lagging cache could admit what should be refused. Neither webhook writes any object besides its own patch, so in step 1 both declare `sideEffects: None`.

Capacity is not checked at admission in step 1. A started VM whose class has no free unit on a node it can use waits in `Scheduling`, its launcher pod `Pending` with `Unschedulable` and the VM shown as `ErrorUnschedulable`, as a VM short of CPU or memory does today, and starts when a unit frees up; the catalog's `free` and `maxFreePerNode` tell a tenant beforehand whether that is likely. Refusing the VMI instead would leave the VM to the per-item backoff of KubeVirt's VM controller, which grows to 1000 seconds, for a shortage that may last seconds.

A pool states no mode of its own: its entry names the class, and the class's `mode` never changes, so the chart and admission on `KubernetesNodes` take the mode from the class ([0009](./decisions/0009-gpuclass-mode-is-immutable-and-a-pool-names-its-class.md)). A Talos pool, which today is every pool, takes passthrough classes only: a passthrough GPU is what Talos GPU pools already take through `name` entries, while a vGPU class needs the vGPU guest driver, which Talos workers do not carry. Admission on `KubernetesNodes` refuses a class entry that names a vGPU class on a Talos pool, before any machine is created. Worker pools on another OS, such as the Kairos pools of `kairos-vgpu-workers`, take vGPU classes as that proposal describes. Talos GPU pools on `name` entries keep working as today.

### 5. Budgets

A tenant's GPU budget is declared in `resourceQuotas`, per class as `<class>.gpuclass.cozystack.io/devices: <count>` and over every class as `gpuclass.cozystack.io/devices: <count>`, each vGPU counting as one. These keys are not resource names a pod requests, so they are not rendered into the namespace `ResourceQuota`, and a tenant with only GPU keys gets no `ResourceQuota` or `LimitRange`. That is a change to the tenant chart, which today renders both for any `resourceQuotas` key.

GPU keys follow the same hierarchical pool rules as every other key, and #48 is what checks them, so tenants learn one rule and do not migrate when #48 ships: a `vm-instance` reserves `count` units of its class for each class entry, a `kubernetes-nodes` pool reserves `count` times `maxReplicas`, both evaluated from the defaulted values, and each also reserves the sum under the key without a class prefix. A write that would take a pool over a declared GPU key is refused when it is made, with a message naming the pool, the key, the limit and what is already reserved. A stopped VM keeps counting, as its CPU and memory do under #48: the budget limits what is declared, and the usage records (section 7) show what runs. A tenant with no GPU key anywhere above it is limited only by capacity, and for vGPU classes by its entitlement (section 6).

Until #48's gate is on, an interim check in the Cozystack API server does this for GPU keys only. On a create or update of a `VMInstance` or a `KubernetesNodes`, it sums the class entries of every `VMInstance` and `KubernetesNodes` release in the pool, as above, with the object under admission at its new size, and refuses the write when a GPU key of the pool would be exceeded. On an update only the delta counts, so shrinking and no-op edits always pass. It reads the tenants' declared `resourceQuotas` and the applications' defaulted values, the inputs #48 reads, and applies the same pool arithmetic, so the switch to #48 changes no verdict. Like #48's gate it is a read-check-write without a transaction, so two writes at the same moment can both pass, and nothing evicts the excess. The interim check stands aside while #48's gate is switched on, and is deleted in the release where that gate is on by default, which needs `VMInstance` and `KubernetesNodes` to declare their class entries in their consumption.

The tenant declaration gate validates GPU keys like any other: a child's declared GPU key may not exceed what its parent's pool has left. Today the gate reads what a pool uses from the `ResourceQuota`, which has no GPU keys, so until #48 the interim check also runs on Tenant writes and supplies the pool's class entries as its GPU usage.

Pool membership follows the existing rules too, which #48 keeps. Until #48 ships, the runtime pool controller reads the rendered `ResourceQuota`, which has no GPU keys, so a tenant whose quotas hold only GPU keys is a pool root for the interim check and not for that controller; #48 removes the difference by reading declared values on both sides. Existing raw GPU keys, quota keys on a resource name such as `nvidia.com/AD102GL_L40S`, keep their meaning: they render into the `ResourceQuota` and limit launcher pods when they are created, whether the VM names the resource or a class of it (section 8).

### 6. vGPU entitlement

A vGPU class is used only by a tenant entitled to vGPU ([0017](./decisions/0017-the-vgpu-entitlement-is-a-tenant-flag.md)). The Tenant field `gpu.vgpu` grants it. The parent tenant's administrator sets it, or the hoster on a top-level tenant. A tenant is entitled only when the flag is set on it and its parent is entitled. The root tenant is rendered by the platform, not by the tenant chart, so its value comes from platform values. The flag is off by default everywhere, so the hoster turns it on for the root first. No tenant can grant itself vGPU, since its Tenant object lives in its parent's namespace. The tenant chart computes the effective value from the parent's, like its other namespace values, and stamps it on the namespace as the label `namespace.cozystack.io/vgpu`, the same pattern as `namespace.cozystack.io/etcd` and the other module labels; a change reaches grandchildren as each level re-renders. The chart always writes the label, so only the value `"true"` means entitled; any other value, or no label, means not entitled.

Entitlement is separate from budget because a vGPU license is counted per unit, which is a budget, while getting the vGPU guest driver and the license token at all is yes or no. The GPU keys of section 5 then limit vGPU like any other GPU. The VMI webhook refuses a vGPU class entry without the entitlement at every start (section 4), so clearing the flag stops VMs on vGPU classes from starting again while running ones keep running, and the tenant's Kairos pools lose their vGPU entitlement as `kairos-vgpu-workers` describes. A write that names a vGPU class is not refused for the entitlement, since the pool gate of section 5 counts sizes and has no yes-or-no rule; the option source offers a vGPU class to an unentitled tenant with a remaining budget of 0 (section 7). Raw vGPU names in VMs stay as open as today until a raw key limits them or step 2's `forbidden` is set.

### 7. Usage records, catalog and licensing

Cozystack does not bill. It keeps usage records (`Workload`) that an external billing system reads, and step 1 adds no record of its own. The `WorkloadMonitor` of a VM already sums the requests of its virt-launcher pod into a `Workload` per pod, extended resources included, and KubeVirt v1.9.0 puts one unit of the device's resource name into the pod's requests per GPU entry ([`renderresources.go`](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-controller/services/renderresources.go)). A VM on a class entry therefore shows its GPUs there under the class's resource name, for exactly as long as the pod exists, next to its CPU and memory. A worker pool's monitor selects its workers' launcher pods, so they are recorded the same way. The record names the VM's pod, not the node or the device ([0015](./decisions/0015-node-allocatable-is-the-gpu-inventory.md)). Since a resource name belongs to one class at a time (section 3), a billing system maps a record to a class through the catalog. A class whose `resourceName` changes appears under the new name from the next start of each VM, so a billing system keeps the names it has seen per class. Like other `Workload` records they have no time fields: a used interval is the pod's lifetime, which a billing system observes by watching and reconciles by sampling.

The catalog is the list of `GPUClass` objects with `displayName`, model, mode, profile, memory, `maxCount`, `status.free` and `status.maxFreePerNode`. Every authenticated user reads it, because a hoster publishes its offers and a tenant names a class in a VM, including from `kubectl` or GitOps without a portal. So the status holds only `free` and `maxFreePerNode`, and its conditions name no nodes and carry no counts. The class's capacity and used units show the hoster's inventory and load, and they are not in the status; the controller exports them as metrics that platform monitoring scrapes. A tenant can still watch `free` and `maxFreePerNode` over time and see how busy a class is.

The dashboard reads the catalog through a new `gpuclass` option source, which also returns the tenant's remaining budget per class; `OptionItem` gains optional integer fields for `free`, `maxFreePerNode`, `maxCount` and `remainingBudget`, and a portal offers the smallest of `maxFreePerNode`, `remainingBudget` and `maxCount` as the largest entry, leaving out a field that is absent because it sets no limit. The remaining budget is the pool figure of section 5, so a tenant under a shared parent budget can see how many GPUs its siblings declare, as a number, without names. This is accepted: the alternative is a budget the tenant can only discover by a refused write, which is the failure this proposal removes. For a vGPU class and a tenant without the vGPU entitlement, `remainingBudget` is 0. The existing `gpu` source keeps serving raw names while they are allowed.

vGPU needs a license, and passthrough needs one only for graphics. Compute (C-series) profiles are licensed through NVIDIA AI Enterprise, graphics (Q-series) profiles through NVIDIA vGPU software. The hoster provides the license server, the token and the guest drivers. In worker pools `kairos-vgpu-workers` delivers the token and the guest license settings. In VMs the hoster does, through its image or at boot, as `vgpu-vms` describes. The class's `mode` tells a portal and a tenant which case applies.

### 8. Migration of existing `gpus`

Raw `name` entries keep working, and nothing converts them automatically. A tenant moves a VM by replacing each `name` entry with a class entry of the matching class and restarting it; the VM requests the same resource name as before, so the move needs no free unit. A Talos GPU pool on a passthrough name moves by replacing its `name` entries with a class entry of the matching passthrough class, which rolls its workers. A Talos pool on a raw vGPU name has no path here, since Talos workers carry no vGPU guest driver; it keeps its raw name until it moves to a worker OS that does. ComputePlane GPU groups have no path until the module accepts class entries. The `gn1.*` instance types get no class to migrate to: they are deprecated, and the name they request is in neither default `permittedHostDevices` table. A VM on one moves to an instance type without GPUs and takes class entries, and a pool on one takes the pool path above.

Quota keys move too. A class entry's launcher pod requests the class's resource name, so while a tenant migrates, a raw key on that name limits both kinds of VM at pod creation, and a GPU key of section 5 limits only class entries, at write. After its last raw-name VM has moved, the raw key is replaced by GPU keys. A tenant whose raw-name VMs use vGPU can move them to class entries only after it is entitled to vGPU (section 6).

Raw names stay allowed in step 1. Forbidding them is part of step 2, where it is what makes a hold hard (section 9).

### 9. Step 2: holding GPUs

Step 2 lets a tenant keep GPUs on a node while its VM is stopped, so the next start does not fail for a GPU someone else took. Holding stays GPU-specific and does not become a generic reservation ([0016](./decisions/0016-holding-is-step-2-and-stays-gpu-specific.md)): CPU and memory are interchangeable across nodes, while a GPU hold is a whole unit on a specific node that does not migrate, which is why it needs a binder, `Lost`, the gather and `onCordon`. DRA would be the general path, but KubeVirt [VEP 344](https://github.com/kubevirt/enhancements/issues/344) (VM-scoped persistent ResourceClaims) is open and unimplemented, and the vGPU VF is handed out by a device plugin (section 2), not by a DRA driver. Holding moves to DRA once VEP 344 is implemented and a DRA driver hands out vGPU VFs.

Nothing in this section exists in step 1. Step 2 adds `GPUClaim`, the `onCordon` field and a finalizer on `GPUClass`, `claim` entries in `vm-instance`, `claimSelector` entries in `kubernetes-nodes`, the binder, the launcher pod gate and webhook, the claim order webhook, claim usage records and `gpu.rawResourceNames: forbidden`.

#### Objects

```yaml
# Hoster. Step 2 adds onCordon to GPUClass.
apiVersion: cozystack.io/v1alpha1
kind: GPUClass
metadata:
  name: l40s-24c
spec:
  # ... the step 1 fields
  onCordon: Move          # Move | Stay; can change
---
# Tenant. One per GPU or per set of GPUs used together on one node.
apiVersion: cozystack.io/v1alpha1
kind: GPUClaim
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
```

A class is in use while any claim of it is `Bound`, `Attached` or `Lost`, and a class in use cannot disappear. A finalizer, `cozystack.io/gpuclass-protection`, keeps a deleted class while it is in use, new orders against a deleted class are refused, a `Pending` order of a deleted class gets a condition and is never bound, and `maxCount` cannot change while the class is in use.

A `GPUClaim` with `count: 2` is two GPUs on one node attached together; GPUs that move between VMs separately are separate claims. Admission refuses a claim above the class's `maxCount`. The spec is immutable, so a change is a new order. A held claim is a `GPUClaim` a tenant orders and keeps, as opposed to a template claim (Attaching held claims); it holds GPUs while it is `Bound` or `Attached` (the phase table). A `Bound` claim that no VMI uses is a count on a node, not a specific card, because with a device plugin kubelet picks the physical device when the pod starts.

What `Bound` guarantees depends on the class's source:

| Source | A `Bound` claim holds | The physical device is picked by |
| --- | --- | --- |
| `devicePlugin` | `count` free units of the class on one node | kubelet, when the pod starts |
| `dra` (section 10) | one `ResourceClaim` within budget, and its devices once the claim is allocated at the first start | the scheduler, at allocation |

A tenant sees a class, a count, a phase and, once there is one, a node, never which card it holds, so its API stays the same when an offer moves to a class of the other source.

#### Binding and capacity

A controller in `cozystack-controller` binds each new `GPUClaim` to a schedulable node of its class that has `count` free units. A node is cordoned when its `spec.unschedulable` is `true`; taints do not count, including the `NoSchedule` taint Fence Agents Remediation sets on a node under remediation. New binds, moves, rebinds and gathers skip cordoned nodes, nodes that are not `Ready`, and nodes with a `NoSchedule` or `NoExecute` taint that the class's `tolerations` do not tolerate, by the taint rule of section 3, and their units do not count in the class's capacity, `free` or `maxFreePerNode`; a set of claims of several classes takes a node only when every class of the set tolerates its taints ([0010](./decisions/0010-gpuclass-carries-the-tolerations-binding-reads.md)). A claim already bound to a node that stops being `Ready` stays there until lost capacity moves it (below), so a short outage moves nothing. The one exception is the gather at VMI start (Placing a VMI's claims): it may take a claim attached only to the starting VMI off a node that is not `Ready`, because no other VMI uses that claim and the move happens once, for a VM that is starting now, so nothing flaps back when the node returns. Free capacity is the node's allocatable count of the class's resource, minus the units held by bound claims, minus the units pods hold beyond what their claims cover; the last term counts VMs that still use a raw resource name. By default the binder packs onto the fullest node, so whole nodes stay free for large sets.

Admission refuses an order that cannot be met at that moment, by capacity or by budget (Budgets and usage records of held claims), with a message naming the class, the count and the limit it hit: a budget of the pool, the class's free units, or the free units on one node. Two orders admitted at the same moment can still exceed a limit together, so the binder checks capacity and budgets again before binding. An order that loses this race stays `Pending` with a condition naming the shortage, and is bound when units or budget free up.

The phases decide what a claim holds and costs:

| Phase | Holds GPUs | Counts against budget | In usage records |
| --- | --- | --- | --- |
| `Pending` | no | no | no |
| `Bound` | yes, on a node | yes | yes |
| `Attached` | yes, used by a VMI | yes | yes |
| `Lost` | no | yes | no |

A `dra` claim before its first start is the exception: it is `Bound` without a node, holds no GPU and is not in the usage records (section 10).

A claim leaves `Attached` when its VMI reaches a final phase (`Succeeded` or `Failed`) or is removed from the API, whichever comes first. A deletion timestamp alone is not enough: on an isolated node the domain of a VMI being deleted can still hold the GPU, while a final VMI no longer runs its domain. KubeVirt v1.9.0 names a VMI after its VM, so it never creates a VM's next VMI while the previous one exists: under `Always` and `RerunOnFailure` the VM controller deletes a final VMI before it starts the next one, under `Manual` a start request first stops the final VMI and then starts a new one, and under `Once` no next VMI is created ([`vm.go`](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-controller/watch/vm/vm.go)). A new VMI therefore never finds its claim attached to the old one.

A claim keeps its node while the `Node` exists, serves the class, has not lost capacity, has no pending layout change (section 2) and is not cordoned under `Move`; otherwise an unattached claim that no longer fits there moves to another node of its class, or to `Lost` when none has room. The claims one VM names move as a set: together to one node with room for all of them, together to `Lost` when they must leave and no node has room for all of them, and they are rebound together. A claim or set whose only reason to leave is a cordon is the exception: with no room elsewhere it stays where it is. When a claim's node is deleted or no longer serves the claim's class, the controller also deletes the claim's unscheduled VMI if its pod is already pinned, so the claim becomes unattached and moves; a VMI whose pod is still gated keeps waiting while its claims are gathered elsewhere. KubeVirt cannot live-migrate a VM with a passthrough GPU or an SR-IOV VF, so pinning adds no cost in normal operation. A cordon moves claims by the class's `onCordon` policy. With `Move`, the default, an unattached claim on a cordoned node moves to another node of its class when one has room, keeping its hold and its usage record, and carries a condition naming the cordon while it waits. Since cozystack/cozystack#4403 a drain evicts a `vm-instance` VM that cannot live-migrate, so its claim becomes unattached, moves, and the VM's next VMI starts on the new node. Worker pool VMs use `evictionStrategy: External`, so a drain leaves them to Cluster API, and a replaced worker takes a claim by the pool rules below. With `Stay` the claim keeps its node and the VM waits for it, for a hoster who wants VMs back on the same hardware after maintenance. When the cordon is the only reason any claim of a set has to leave and the set mixes classes, the whole set stays in place, and its VM waits, while a claim of a `Stay` class sits on a cordoned node, because the set moves together; a gather at VMI start, when one is needed, cannot take such a set.

A drop in allocatable alone is not a loss, since kubelet reports zero after every restart until the device plugin registers again. A node has lost capacity for a class when its allocatable count of the class's resource stays below the units held there for longer than a grace period, or when the `Node` has been `NotReady` that long without interruption; a node that is not `Ready` fits no claim ([0015](./decisions/0015-node-allocatable-is-the-gpu-inventory.md)). Then:

- the affected classes get a `LostCapacity` condition;
- unattached claims that no longer fit move to another node of their class, or to `Lost` when none has room;
- on a `NotReady` node, attached claims go to `Lost` as well, which drops them from the usage records, and are rebound once their VMIs reach a final phase or are removed from the API; if the node returns while the VMI still runs, the claim goes back to `Attached` there; a claim that a gather at VMI start is moving off the node is the exception: it finishes the move;
- a VM started after the condition is set has its claims gathered off the node before its pod is released, and the controller deletes the unscheduled VMI of a claim that no longer fits once that VMI's pod is already pinned, so its claim becomes unattached and can move; a VMI whose pod is still gated is not deleted, its claims are gathered instead. What the VM does next follows its `runStrategy` for a deleted VMI: `Always` and `Once` create a new VMI and start once the claim has moved or been rebound from `Lost`, while `Manual` and `RerunOnFailure` stay stopped until the tenant starts the VM (Failure cases); unlike a cordon, the node cannot serve the claim, so waiting is no option.

Claims in `Lost` are rebound before new orders when capacity returns, a VM's set onto one node, without blocking smaller orders behind a large one; a claim whose VMI is still live on the lost node waits until that VMI is final or removed (above), while a claim of a VMI whose pod is still gated is rebound for that VMI. The controller's metrics show how many GPUs are waiting. A rebind never fails a budget check, since a `Lost` claim already counts.

#### Attaching held claims

VMs and worker pools reference held claims in `gpus`:

```yaml
# vm-instance
gpus:
- claim: train-0
---
# kubernetes-nodes: each worker of the pool takes one matching claim
gpus:
- claimSelector:
    className: l40s-24c
    matchLabels:
      pool: inference
```

A `vm-instance` entry has exactly one of `name`, `className` or `claim`, and a `kubernetes-nodes` entry exactly one of `name`, `className` or `claimSelector`. A list holds only `name` entries or only class and claim entries, a pool has at most one `claimSelector` and never next to a class entry, and a selector names its class in `className` and matches only claims of that class, which must share a `count`; the webhook checks the `count` at each attach, since claim labels can change after the pool is admitted. The rules of section 4 for Talos pools, the vGPU entitlement and `permittedHostDevices` apply to the class of a claim as to a class entry.

The VMI webhook of section 4 also resolves claim references, runs the checks that need no placement, and records the attachments; it binds, moves and creates no claim, and waits for nothing. For a VM it takes the named claims. For a pool worker it takes the claim the worker held before, unless a VM now names it, its node is not `Ready`, or its node has a taint the class does not tolerate. Otherwise it takes one unattached `Bound` claim on a `Ready` node that matches the selector and that no VM names in a `claim` entry, preferring nodes that are not cordoned and have no such taint. Selectors are not checked against each other: when two pools match the same claim, the first worker to start takes it, so a tenant gives each pool its own labels. The webhook records the attachment of each held claim to this VMI, so two VMIs can never hold one claim and no background move can take one, and writes one `gpus` entry per GPU unit, held or from a class entry, with the class's resource name. The launcher pod webhook of section 4 adds the tolerations of the held claims' classes as it does for class entries. An attachment recorded for a VMI that is never created, because a later admission step refused it, is cleared by the controller after a grace period longer than the API server's request timeout. On a dry run the webhook records nothing, so in step 2 it declares `sideEffects: NoneOnDryRun`. The GPU controller places the claims after admission, while the launcher pod waits behind a scheduling gate (Placing a VMI's claims).

The webhook refuses to start a VMI, and names each claim and its node, when a referenced claim does not exist, is being deleted, or is attached to another VMI, besides the refusals of section 4 for its class. A refused VMI leaves its claims unattached, so they move by the rules above. A claim that is `Pending` or `Lost`, or bound to a node that cannot serve it, is not refused: the controller binds or moves it before it releases the pod.

A claim cannot disappear under a running VM: a deleted `GPUClaim` stays, with its binding and usage record, under the `cozystack.io/gpuclaim-protection` finalizer until no VMI uses it, and is not attached again.

Once holding exists, a class entry needs a place on a node too, or a VM that names only a class could start on a unit another tenant holds. Once the launcher pod waits at its gate, the controller creates a `GPUClaim` from each class entry, named after the VMI and the entry's index, marked by the `cozystack.io/gpuclaim-template` label, and owned by the VMI. A claim of that name that carries the mark and is owned by this VMI is reused, so a repeated pass creates nothing twice; one still being deleted, or still owned by the VM's previous VMI, is waited for. Tenants cannot set the mark, so a tenant's own order is never taken or deleted this way; an unmarked claim that already has the name is never taken, and the pod stays gated with a condition naming it. The controller binds these template claims under the same capacity checks as any order, on the node of the gathered set when the VMI has held claims, and otherwise on one node with room for all of them, and attaches them. It deletes a template claim as soon as its VMI is final or removed, since KubeVirt can keep a finished VMI until the next start, so the claim lives only while its VMI runs, as a `ResourceClaim` made from a `ResourceClaimTemplate` lives only with its pod. A template claim counts against no budget of its own, since its class entry already counts (section 5), and it is not in the usage records, since its launcher pod is. A class entry counts as a claim reference under `forbidden`, and a pool on class entries pays for running workers only.

A pool on `claimSelector` never runs more GPU workers than it has matching claims. Admission on `KubernetesNodes` refuses a `maxReplicas` above that number; the chart default is 10, so a pool with fewer claims sets it. A pool without spare claims rolls one worker at a time: surge machines are created at once, their VMIs are refused until an old worker frees a claim, and a surge machine that waits past `nodeStartupTimeout` is replaced by MachineHealthCheck.

#### Placing a VMI's claims

A launcher pod waits for its GPUs behind a scheduling gate, so nothing is refused for not being ready yet ([0012](./decisions/0012-the-vmi-webhook-checks-and-records-and-the-controller-prepares.md)). The launcher pod webhook of section 4 also adds the gate `cozystack.io/gpuclaims` in step 2. It matches only pods labelled `kubevirt.io: virt-launcher` that carry the reference label, adds the gate only when it is not there yet, since a repeated call would otherwise fail on a duplicate gate, and fails closed: a pod that reached the scheduler ungated would run unpinned. It also gates the temporary pod KubeVirt creates to bind `WaitForFirstConsumer` volumes, so a local volume binds on the node of the claims. Migration target pods are left alone: KubeVirt deletes a target pod that stays pending for 15 minutes, a VM with a passthrough GPU or a VF does not live-migrate, and Cozystack does not enable the `VGPULiveMigration` gate that mdev vGPUs would need. A set that already has a `WaitForFirstConsumer` volume bound on a node stays on that node: the controller places it only where the node affinity of the VMI's bound volumes allows. KubeVirt never touches scheduling gates and has no timeout for a VMI whose pod waits, so the VMI stays in `Scheduling` with `PodScheduled=False`, reason `SchedulingGated` ([`lifecycle.go`](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-controller/watch/vmi/lifecycle.go)), and the VM's own start backoff never begins.

For a gated pod the controller creates the VMI's template claims, then brings the set onto one node. Claims a VM ordered separately can sit on different nodes, for example two classes the binder placed apart. When the claims a VMI references are `Pending` or `Lost`, on different nodes, on a node that is gone, no longer serves a class, has lost capacity or has a pending layout change, on a node that is not `Ready`, on a node with a taint one of their classes does not tolerate, on a cordoned node under `Move`, or on a node with no room for the VMI's template claims, the controller binds or moves them onto one `Ready` node that is not cordoned, has no pending layout change, is tolerated by every class of the set and has room for all of them, template claims included. Every held claim of the set is attached to this VMI by then, so no other VMI or background move can take one, and each claim keeps its hold while it moves. A claim of a `Stay` class on a cordoned node does not move. When all of the VMI's claims are on that node and the VMI has no template claims, there is no gather, even if the node is also not `Ready`: the pod is pinned there and waits. When some of its claims are elsewhere, or it has template claims, the set cannot be gathered. The pin holds through a node that is not `Ready` or has such a taint, and breaks once a claim of the set has lost capacity there, the node is gone, or it no longer serves a claim's class; the set then follows the rules for those cases ([0006](./decisions/0006-a-stay-pin-yields-only-to-a-node-that-cannot-serve-the-claim.md)).

Once the set is on one node, the controller pins the pod and removes the gate in one update. The pin is a `matchFields` requirement on `metadata.name` appended to every required node selector term of the pod, or the single term when it has none. Kubernetes lets a gated pod only narrow its scheduling, so a term cannot be added next to existing ones, and KubeVirt usually sets a required term already, for host-model CPUs. The pin is not `nodeName`, which would let kubelet start the pod on a cordoned node, and it goes on the pod rather than the VMI because, with `vmRolloutStrategy: LiveUpdate`, KubeVirt rewrites a VMI's node affinity from the VM template.

When the set cannot be placed, because no node has room or a `Stay` claim sits on a cordoned node while others need gathering, the pod stays gated, the claims carry a condition naming the reason, and the VMI gets a `Warning` event with the same reason. The VMI gets no condition because KubeVirt's admission guards writes to a VMI. The controller releases the pod once the set becomes placeable. A pod that is already pinned cannot be gated again or unpinned. If the scheduler then refuses it on a node that is `Ready`, not cordoned and still serves the set, for lack of CPU, memory or a device the plugin no longer advertises, the controller deletes the launcher pod. A cordon, a node that is not `Ready` and a `Stay` pin do not count as such a refusal, and the pinned pod waits there, as above; a cordon under `Move` and lost capacity follow their own rules. After the deletion the VMI becomes `Failed`, and the VM's `runStrategy` decides what follows: `Always` and `RerunOnFailure` start again after KubeVirt's start-failure backoff, with the VM shown as `CrashLoopBackOff`; a `Once` VM keeps its failed VMI, so it stays down and also shows `CrashLoopBackOff`, and a `Manual` VM stays stopped until the tenant starts it (Failure cases). The controller records that node as rejected for that set, and the binder does not pick it for the set again until the node's allocatable, its taints or its `Ready` condition change, or ten minutes pass. A set of template claims only loses its claims with the failed VMI, so for it the controller keeps the record in memory against the VM, and a controller restart forgets it. Ten minutes outlasts a kubelet restart and the device plugin registering again, and keeps a node that the hoster has fixed from staying excluded for long.

A claim that is already attached when its node is cordoned, to a VMI whose pod has not started, moves the same way while the pod is gated. Once the pod is pinned, under `Move`, for a VM with `runStrategy` `Always` or `Once` the controller deletes that unscheduled VMI and the VM's next VMI gets the moved claim; under `Manual` or `RerunOnFailure` the VMI waits on the cordoned node, since deleting it would leave the VM stopped (Failure cases).

#### Budgets and usage records of held claims

A held claim counts against the same GPU keys as a class entry, from the moment it is `Bound` until it is deleted (the phase table), in the same pool figure as the class entries of section 5; a `claim` or `claimSelector` entry declares nothing of its own, since its claims already count. The validating webhook on `GPUClaim` creation checks each order against its class, `maxCount`, free capacity, that pool figure and the vGPU entitlement. A raw GPU key on the tenant or any ancestor for a class's resource name blocks orders of that class until the tenant that carries the raw key also has a GPU key covering the class, so a held claim, which no `ResourceQuota` sees, never bypasses an existing raw limit. Clearing the vGPU flag refuses new vGPU orders; claims already held keep counting and can still move or be rebound, so a pool on `claimSelector` keeps its claims.

Each held claim gets a usage record with `kind: gpu-claim` and resources `<class>.gpuclass.cozystack.io/devices: <count>` while it is `Bound` or `Attached`, a `dra` claim only once its `ResourceClaim` is allocated (section 10). The GPU controller writes the record itself, with its `GPUClaim` as the record's owner through an ownerReference, and with no `WorkloadMonitor` and no label selector, so no tenant label can add a record or drop one ([0011](./decisions/0011-a-gpuclaim-owns-its-usage-record.md)). The `Workload` controller, which today deletes a record whose `workloads.cozystack.io/monitor` label is missing or names a monitor that is gone, leaves a record owned by a `GPUClaim` to its owner: the GPU controller deletes it when the claim leaves `Bound` and `Attached`, and garbage collection deletes it with the claim. A virt-launcher pod's `Workload` does not report GPUs its held claims cover a second time.

The catalog's `free` and `maxFreePerNode` subtract held units from step 2 on. A hoster lists bound claims per node with a field selector on the `GPUClaim` field `status.nodeName`, which the CRD declares in `selectableFields` and tenants cannot write, and the class's bound units and `Lost` count join its metrics. A `gpuclaim` option source lists a tenant's own claims.

#### Forbidding raw names

While raw names are allowed, the scheduler does not see a claim that is bound but not attached, so a raw-name VM can take that unit; the binder counts raw-name usage, so it never books a unit a raw-name VM holds, but not the reverse. A hold is a hard guarantee only with `gpu.rawResourceNames: forbidden`.

When every consumer has moved to class or claim entries, the hoster sets `gpu.rawResourceNames: forbidden`. The platform refuses `forbidden` on a cluster where gpu-operator does not serve GPUs to VMs, since the policy that enforces it renders only there. The preconditions are the hoster's checklist, and the platform does not verify them: no worker pools with raw-name `gpus` entries, ComputePlane GPU groups included, no `gn1.*` GPU instance types in use, and no raw GPU quota keys left. With `forbidden`:

- an admission policy refuses GPU entries (section 4) on every VMI created without a class or claim reference, on every path;
- the same policy refuses every pod created in a tenant namespace that requests a GPU resource name, unless it is a virt-launcher pod created by KubeVirt's virt-controller service account with an owner reference to a VirtualMachineInstance. Tenants cannot forge such a pod, since no tenant role can create pods or VMIs, so app charts that copy free-form `resources` into pods cannot request GPUs either;
- tenant validation refuses raw GPU keys;
- the `gpu` option source returns nothing, and the `instancetype` source hides instance types that carry GPU entries.

The policy reads its settings from a ConfigMap the controller writes in `cozy-system`, so on a cluster with `forbidden` it refuses pod creation in tenant namespaces until the controller has published it once.

### 10. Extensions

The catalog is designed so later backends add `source` members and class fields, not tenant fields.

- DRA. Kubernetes DRA has been GA since 1.34, and KubeVirt v1.9 has `GPUsWithDRA` and `HostDevicesWithDRA` in beta. A `dra` source names a `DeviceClass` and selectors. In step 1 a class entry of a `dra` class renders a `ResourceClaimTemplate` reference into the VMI, and the scheduler allocates per pod. In step 2 a `GPUClaim` maps one to one onto a `ResourceClaim` created at order time. The scheduler allocates the `ResourceClaim`, the GPU controller keeps it allocated through its own `reservedFor` entry, which the resourceclaim controller leaves in place, and the webhook renders the `ResourceClaim` reference into the VMI; the binder does not allocate. Three gaps remain. The NVIDIA DRA driver has no vGPU yet (kubernetes-sigs/dra-driver-nvidia-gpu#1402, milestone v0.6.0), and its passthrough is alpha. A `ResourceClaim` is allocated only when a pod first needs it, so nothing is held before the first boot: until then a `dra` claim is `Bound` without a node, counts against the budget, holds no GPU and stays out of the usage records, and the webhook attaches it like any `Bound` claim without pinning the pod to a node. DRA quota is per namespace, without pools. KubeVirt VEP 344 (`persistWhenStopped`) holds a claim for a stopped VM from the VM side; a `dra` class and VEP 344 must not both manage one claim. When a DRA GPU driver is installed, the raw-name policy has to cover `ResourceClaim` references too.
- MIG-backed vGPU profiles get their own resource names from the patched plugin, so they are ordinary classes; with DRA, partitionable devices (beta in Kubernetes 1.36) describe MIG natively.
- A non-NVIDIA GPU is a class with another `vendor`; Q, B and A series vGPU profiles are classes like C series.
- NVLink sets that must be attached together are left to the DRA source.
- On-demand slicing. A hoster may let tenants take any size from a shared pool, from a fraction of a GPU, such as a MIG-backed vGPU on a 1/7 slice of an A100, up to several whole GPUs of one node, taken from GPUs the layout keeps out of MIG mode. The tenant contract stays a class, one per size, and a `count`, and a claim still lives on one node. The host layer and the binder change: the node agent creates the profile, and on MIG GPUs the MIG instance under it, when a claim binds instead of following a fixed layout, so the binder counts capacity from free GPU resources rather than from advertised names. Enabling MIG mode needs an idle GPU and, on Ampere, a GPU reset, so the layout enables it ahead of time. On Hopper and newer the mode does not survive a reboot, so the layout sets it again at boot. A plugin restart per bind, as section 2 does for late profiles, would leave the node's allocatable at zero until the plugin registers again on every bind, so the plugin needs to rediscover profiles without one, separate work on top of NVIDIA/kubevirt-gpu-device-plugin#192.

## User-facing changes

Step 1:

- Tenants get a GPU catalog with free capacity and remaining budget per class, and `className` entries in `vm-instance` and `kubernetes-nodes`. Raw resource names keep working.
- A write of a VM or pool whose class entries do not fit a GPU budget is refused. A started VM that finds no free unit waits in `Scheduling` until one frees up.
- Hosters get `GPUClass`, GPU keys in `resourceQuotas`, the `gpu.vgpu` Tenant flag that opens vGPU classes to a tenant, the node layout and the `gpu.resourceNames` list; an external billing system reads GPUs from the `Workload` records of virt-launcher pods.

Step 2:

- Tenants get `GPUClaim`, `claim` entries in `vm-instance` and `claimSelector` entries in `kubernetes-nodes`. A held claim is billed while it holds GPUs, `Bound` or `Attached`, whether a VM runs on it or not. An autoscaled pool on `claimSelector` holds, and pays for, every claim its selector matches, which is at least `maxReplicas`. A tenant who wants to pay only while running uses class entries, and finds out at start, not at order time, whether a unit is free.
- Hosters get `onCordon` on classes and `gpu.rawResourceNames`; an external billing system gets a usage record per held claim.

## Upgrade and rollback compatibility

The change is additive. Existing `gpus` entries render as before, and the existing variants work as today until a layout is set.

Rollback is a hoster operation; its step-by-step runbook belongs to the documentation of the implementation. It keeps VMs and worker pools, which restart in the windows, with these exceptions. Choosing `default`, or one mode for a model whose whole GPU and VFs share a PCI ID, drops the other mode's devices and stops their VMs, a loss of service the hoster announces first. Tenants whose only GPU limits were GPU keys lose them, and on a cluster that ran step 2 with `forbidden`, tenants without the vGPU entitlement can use raw vGPU names again. Kairos GPU pools take GPUs only through classes, so they lose their GPUs and run on the plain worker image.

Step 2 rolls back first. Raw names are allowed again, since raw keys are refused while raw names are forbidden. `claim` entries of VMs and `claimSelector` entries of pools become class entries, and those VMs restart and those pools roll. The `GPUClaim` objects are deleted, and the step 2 components go.

Step 1 rolls back next. The hoster removes the node layout and chooses the variant in a maintenance window. Class entries become raw `name` entries with the class's resource name, and GPU keys become raw keys on those names where the tenant wants a limit, and the remaining GPU keys are deleted. Class entries are removed from Kairos pools before the `gpu.vgpu` flag goes, since otherwise their new vGPU worker templates wait for a license copy. Last, the components, the `GPUClass` CRD, the `gpu.vgpu` flag with its namespace label and the `cozystack.io/gpu-*` node labels are removed. Each node's workload label stays on `vgpu`, since without the layout the variant's default would turn passthrough nodes into vGPU nodes; on `default` the former vGPU nodes are relabelled `vm-passthrough` in the first window.

Three rules hold throughout:

- Names move back by the window rule of section 2 in reverse: vGPU classes return to the hand-written VF names, and raw `name` entries and raw keys on the per-profile names move with them in the same window.
- Raw keys render into the `ResourceQuota` and are checked at pod creation, not at write, so a tenant moved back to them is refused later than before.
- A raw key also renders the tenant chart's default `LimitRange`, whose memory limit can sit below a VM's memory request, because KubeVirt sets virt-launcher limits from the namespace quota only when it limits `cpu` and `memory`, so a tenant given a raw key also declares `cpu` and `memory`.

## Security

Step 1:

- Tenant access. Every authenticated user can read `GPUClass`, through a ClusterRoleBinding to `system:authenticated`, because tenant roles are bound per namespace and cannot grant a cluster-scoped kind. Only platform administrators write `GPUClass`.
- What a tenant controls. A tenant never supplies a resource name through a class entry: it comes from `GPUClass`, which tenants cannot write. A tenant cannot set its own `gpu.vgpu` flag, since its Tenant object lives in its parent's namespace. A tenant super-admin who writes VirtualMachines directly can still write raw GPU names, as today, until step 2's `forbidden`; a VMI with class entries and GPU entries of its own is refused. GPU keys are checked only when an application is written. A VirtualMachine written directly, with class entries in its annotation or a higher `count` than its application declares, is not counted against them, also under `forbidden`. That path closes when tenant super-admins lose write access to VirtualMachines, which #48 lists as a prerequisite of its phase 4. Until then a hoster who sells GPU budgets to sub-tenants does not give them the super-admin role.
- Device plugin. The patched plugin runs privileged with the host driver's NVML library and device nodes, like the vGPU manager does.
- Node agent. It reads sysfs and writes VF profiles on vGPU nodes from a privileged container, as the vGPU manager already does, and can delete only the device plugin pod of its own node.
- Webhooks. The VMI and virt-launcher pod webhooks fail closed and see only objects carrying the reference label, so their outage blocks the start of VMs with class entries.
- Controller. It reads nodes and pods to compute class status, and writes `GPUClass` status. With the host layer it gains patching node labels, restricted by a policy to the GPU workload label and `cozystack.io/gpu-*` ([0008](./decisions/0008-the-controller-gets-its-host-layer-rights-with-the-host-layer.md)).
- Visibility. A tenant sees, through the remaining budget, its siblings' declared GPU count under a shared parent budget (section 7). Any authenticated user sees each class's `free`, `maxFreePerNode` and conditions, and can follow them over time.

Step 2 adds:

- Tenant access. Tenants with the `admin` role or above manage `GPUClaim` in their namespaces, on a cluster where gpu-operator serves GPUs to VMs; lower roles, and every role elsewhere, read it.
- What a tenant controls. A tenant never supplies a node through a claim reference: it comes from the claim's status, which tenants cannot write. Tenants cannot add or remove finalizers on claims, so a held claim cannot leave the usage records. The webhook takes claims only from the VM's own namespace. With `forbidden` any GPU entry without a class or claim reference is refused, as is any other pod in a tenant namespace that requests a GPU resource name.
- Webhooks. Three GPU webhooks fail closed. The VMI and virt-launcher pod webhooks see only objects carrying the reference label, so their outage blocks the start of VMs with class or claim entries. The pod webhook has to fail closed, because a pod created without the gate would reach the scheduler unpinned. A validating webhook on `GPUClaim` creation checks each order, so its outage also blocks new orders, while held claims and running VMs are not affected. A template claim skips it: the VMI webhook has checked the entry, and the controller checks capacity when it creates and binds the claim.
- Tamper policy and its trust boundary. An admission policy on `GPUClaim` creation and update refuses adding or removing a finalizer, except `orphan` and `foregroundDeletion`, which the API server and the garbage collector own, and setting, changing or removing the `cozystack.io/gpuclaim-template` label. It lets through, unchecked, requests from `system:kube-controller-manager`, from members of `system:masters`, and from every service account in `cozy-system` and `kube-system`. Anything running under a service account in those two namespaces can therefore create a claim marked as a template claim, which skips the order webhook, or remove a claim's finalizer, so a held claim leaves the usage records; write access to workloads in those namespaces is as sensitive as the GPU controller's own rights.
- Template claims. For class entries the controller creates `GPUClaim` objects in tenant namespaces, only of the class and count the entry names, after the VMI webhook has checked them. Neither checks who wrote the VM, so anyone who can write a VM, a tenant ServiceAccount included, uses the GPUs of its class entries, and anyone who can start it makes their claims exist, within the tenant's budgets and entitlement ([0007](./decisions/0007-whoever-can-write-a-vm-orders-its-template-claims.md)).
- Controller. It gains deleting unscheduled VMIs that reference its claims, and `update` and `delete` on pods in every namespace, cluster-wide like its write on Secrets, because tenant namespaces appear at runtime. RBAC cannot narrow these to virt-launcher pods, so the right covers every pod, container images, labels and tolerations included, which is enough to run code under a tenant pod's service account; the controller uses it only to remove its own gate while appending the pin, and to delete a pinned launcher pod that its node refuses. It also writes one ConfigMap, the GPU settings in `cozy-system`.
- Visibility. A tenant sees the node name of its own claims.

## Failure and edge cases

Each case gives what is seen and what to do.

Step 1:

- A write of a VM or pool is refused → the message names the pool, the GPU key, the limit and what is already reserved; use a smaller `count`, another class, or ask for more budget.
- A VM with class entries is refused at start → the webhook refuses the VMI, and the VirtualMachine shows the message in a `FailedCreate` event, naming the class and the reason: the class does not exist or shares its resource name with another class, a second vGPU class, more than `maxCount`, no vGPU entitlement, a resource name KubeVirt does not permit, or GPU entries of the VMI's own; fix the entry, or ask the hoster.
- A VM with class entries shows `ErrorUnschedulable` → its launcher pod is `Pending` and `Unschedulable`: no node it can use has a free unit of the class. It starts by itself once one frees up. The catalog's `free` shows the shortage.
- A class shows `free: 0` while its nodes have GPUs to spare → the nodes carry a `NoSchedule` or `NoExecute` taint the class does not tolerate; add it to the class's `tolerations`, or remove the taint.
- A class has a `ResourceNameConflict` condition → another class has its resource name; delete one of them or point it at its own name.
- A class is deleted while VMs name it → running VMs keep running, and their next start is refused naming the class; the tenant moves them to another class.
- A layout change does not apply → the node carries the `cozystack.io/gpu-layout-pending` label; cordon and drain it. A GPU VM on the node holds the change until it stops or is evicted.
- A Talos pool with a vGPU class entry is refused → Talos workers carry no vGPU guest driver; use a passthrough class, or a worker OS that carries the driver.

Step 2:

- A tenant's order is refused at create → the message names the class, the count and the limit, or, for a vGPU class, that the tenant is not entitled to vGPU; order a smaller count, another class, or ask for more budget.
- A claim stays `Pending` after an order → its condition names the shortage; it binds when capacity or budget frees up, or the tenant deletes it.
- A VM with claims is refused at start → the webhook refuses the VMI for a reference problem, such as a claim that does not exist, is being deleted or is attached to another VMI, and the VirtualMachine shows the message in a `FailedCreate` event, naming each claim and the reason; fix the reference.
- A VM with claims stays `Starting` → its VMI is in `Scheduling` with its pod gated, the claims carry a condition and the VMI a `Warning` event naming the reason: no node can take the whole set, template claims included, or a claim of a `Stay` class is on a cordoned node while others need gathering. The VM starts by itself once the set can be placed. To get there sooner, uncordon that node, free capacity on one node, drop a claim the VM does not need, or, within one class, replace them with one claim of the combined `count`.
- The pinned node refuses the launcher pod after the pin, for lack of CPU, memory or a device → the controller deletes the pod, the VMI fails, and a condition on the claims names the node and the reason. An `Always` or `RerunOnFailure` VM shows `CrashLoopBackOff` and starts again after a delay that grows with each failure up to five minutes, and the count resets once a VMI runs; a `Once` VM keeps its failed VMI and stays down, also shown as `CrashLoopBackOff`, and a `Manual` VM stays stopped until the tenant starts it. The binder avoids that node for the set for ten minutes or until the node changes. Free CPU or memory on GPU nodes if this repeats.
- A node is drained or cordoned → under `Move` its unattached claims move when another node of their class has room, and their VMs start there; with no room, or under `Stay`, the claims stay bound and in the usage records and their VMs wait; uncordon it, add capacity, or delete the node.
- A cordoned node is deleted while a drained VM's VMI still waits for it with its pod pinned → the controller deletes that unscheduled VMI (a VMI whose pod is still gated just has its claims gathered elsewhere), the claim moves to another node of its class or to `Lost`, and what the VM does next follows its `runStrategy` for a VMI that was deleted, not one that finished: in KubeVirt v1.9.0 ([`pkg/virt-controller/watch/vm/vm.go`](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-controller/watch/vm/vm.go)) `Always` and `Once` create a new VMI when none exists, while `Manual` and `RerunOnFailure` wait for a start request, so the VM stays stopped until the tenant starts it.
- A node dies but stays registered → after the grace period its attached claims go to `Lost` and leave the usage records, and its unattached ones move or go to `Lost`; once the node is tainted `out-of-service`, its `Node` deleted or its pods force-deleted, KubeVirt fails the VMIs and the claims are rebound. If the taint or the force-delete comes before the grace period ends, the next VMI of an `Always` or `RerunOnFailure` VM gathers its now unattached claims onto a `Ready` node with room and starts there at once. That holds for a VM's held claims. A pool worker takes only a spare claim of its pool, template claims follow their own placement, and a VMI pinned by the `Stay` rule waits on its cordoned node. After a `Node` deletion it starts the same way, once the controller has moved the claims. When no node fits, the new VMI waits in `Scheduling` with its pod gated until a node has room for its set. `Once` and `Manual` VMs keep their failed VMI, so no new one is created: a `Once` VM stays stopped, and a `Manual` VM starts when the tenant starts it. The failed VMI of a `Once` or `Manual` VM leaves its claims unattached, so after the grace period they move to another node of their class, or to `Lost` when none has room. When the hoster enables the optional `node-remediation` package of [Node fencing](../node-fencing/README.md), it sets the taint after Medik8s NodeHealthCheck and Fence Agents Remediation power the node `off` through its BMC; when the package is off or the node is not fenced, for example because the BMC lost power with the node, the manual procedure of that proposal applies.
- A node loses capacity while a VM's VMI waits for it with its pod already pinned → the controller deletes that VMI so its claims can move, while a VMI whose pod is still gated just has its claims gathered elsewhere; an `Always` or `Once` VM starts again once its claims have moved or been rebound, a `Manual` or `RerunOnFailure` VM stays stopped until the tenant starts it.
- A cordoned node dies while a VM's VMI waits on it, pinned there by a claim of a `Stay` class → the pin holds while the node is `NotReady`, so the VMI keeps waiting and its claims stay bound and in the usage records. Once the node has been `NotReady` for the grace period it has lost capacity, and the pin breaks ([0006](./decisions/0006-a-stay-pin-yields-only-to-a-node-that-cannot-serve-the-claim.md)): the controller deletes the unscheduled VMI, and the VM's claims move together onto a `Ready` node with room for all of them, or go to `Lost` together, leaving the usage records, when no node has room, and are rebound together once one has. An `Always` or `Once` VM then starts on the new node once its claims have moved or been rebound; a `Manual` or `RerunOnFailure` VM stays stopped until the tenant starts it. A `Node` deleted before the grace period ends has the same effect at once. To keep the VM on that hardware, bring the node back within the grace period.
- A GPU fails under a running VM → the claim stays `Attached` and in the usage records while the VM runs; when the VM restarts, the controller moves the claim off that node before it releases the pod, or the claim goes to `Lost` and the pod stays gated until it is rebound.
- A GPU pool on raw names, ComputePlane GPU groups included, is created after `gpu.rawResourceNames: forbidden` → its worker VMIs are refused by the raw-name policy; use class entries or a `claimSelector`, or allow raw names again while the cluster has such pools.
- A tenant's VM is `Pending` while its claim is `Bound` and in the usage records → a raw-name VM took the unit; this can happen only while raw names are allowed, and forbidding them prevents it.
- A pool worker is refused → no matching claim is free, because of a surge machine during a rollout, claims still `Pending` or `Lost`, matching claims only on nodes that are not `Ready`, or another pool or a VM using the matching claims; wait, add claims, give each pool its own labels, or lower `maxReplicas`.

## Testing

Step 1:

- Unit tests for the class status: `free` and `maxFreePerNode` from node allocatable and pod requests, a node with a `NoSchedule` taint the class does not tolerate left out and one whose taint it tolerates counted, the three skipped taints, the Fence Agents Remediation taint alone not making a node cordoned, and a second class with the same resource name getting `ResourceNameConflict`.
- Webhook tests for class entries: one `gpus` entry per unit with the class's resource name, the classes' tolerations on the launcher pod and on the temporary `WaitForFirstConsumer` pod and still there after a LiveUpdate sync of the VM, each refusal of section 4, a VM with a GPU class entry and `nvidia.com/hostdev` passing, and a dry run.
- Budget tests: the interim check refuses a `VMInstance` and a `KubernetesNodes` write over a class key or the total key, counts `count` times `maxReplicas` for a pool and the defaulted `maxReplicas` for a pool that does not set it, passes shrinking and no-op updates, refuses a child tenant's GPU key against the class entries already in the parent's pool, applies the pool rules like other keys, stands aside while #48's gate is on, and reaches the same verdicts as #48's evaluator on the same inputs.
- Admission tests for `KubernetesNodes`: a vGPU class entry on a Talos pool is refused, and a passthrough one is accepted.
- Entitlement tests: a child's flag without the parent's gives no entitlement, a vGPU class entry without it is refused at start, and `remainingBudget` is 0 without it.
- Usage tests: a VM on a class entry shows its GPUs in its `Workload` under the class's resource name while its launcher pod exists.
- Admission tests for the class schema: the frozen fields, `tolerations` operators, and the class name as a DNS label.
- Changed existing tests: cozy-lib and tenant chart quotas, `quota_test.go`, `providers_test.go`, and the GPU wiring tests in `bundles_gpu_kubevirt_wiring_test.yaml`.
- E2E with passthrough GPUs on the GPU runner: a VM on a class entry starts with the GPU, a write over budget is refused, and the `Workload` shows the GPU.
- Manual on SR-IOV vGPU hosts: both modes of one model in one cluster, profiles across reboots, a layout change through a drain, and adoption from `default` and from `vgpu`, on Ubuntu hosts.

Step 2:

- Unit tests for the binder and the three GPU webhooks: capacity with raw-name usage, budgets in the pool figure with class entries, the phase table, every move and refusal in section 9, concurrent starts, the gate added once on a repeated call and on the temporary `WaitForFirstConsumer` pod but not on a migration target, the pin appended to every existing node selector term and the gate removed in the same update, and the pod pin after a LiveUpdate sync.
- Webhook and controller tests for cordons, template claims and NICs: the webhook binds, moves and creates no claim, a set that cannot be placed keeps the pod gated with a condition and is released once it can, a pinned pod that the node refuses is deleted and that node is avoided for the set, under `Move` a claim moves at VMI start and the pod gets a required node affinity, an attached claim with an unstarted pod moves for `Always` and waits for `Manual`, on lost capacity a VMI with a pinned pod is deleted and an `Always` VM restarts while a `Manual` VM stays stopped, a gated VMI is not deleted and its claims are gathered, a cordon or `NotReady` on the pinned node does not count as a refusal, a `Once` or `Manual` VM stays stopped after a refused pod is deleted, the claims of one VM move, go to `Lost` and are rebound as a set, claims of two classes on different nodes, and a claim whose node has no room for the VMI's template claim, are moved onto one node at VMI start, left in place when no node fits, a `Stay` claim on a cordoned node pins the pod when it holds all of the VM's claims and blocks the gather when other claims need gathering, claims on a node that is not `Ready` are gathered, two VMIs whose sets share a claim do not both gather it, a VMI with only class entries gets one node with room for all of its template claims, and under `Stay` a single claim waits; a template claim is created by the controller once the pod waits at the gate, under the capacity checks, owned by the VMI, deleted with its VMI, reused on a repeated pass, counted against no budget of its own, and a dry run records nothing.
- Quota tests for held claims: a held claim counts from `Bound`, a `claim` entry declares nothing, a raw key blocks orders of its class until a GPU key covers it, and a cleared vGPU flag keeps held claims and refuses new orders.
- Admission tests for `maxReplicas`, class lifecycle while the class is in use, tenant finalizers, the controller restrictions, and the raw-name policy and quota keys under `forbidden`, including a tenant super-admin whose direct Pod or VMI is refused by RBAC, whose VirtualMachine with a GPU entry and no class or claim reference gets its VMI refused, and a pod requesting a GPU resource name from any creator other than virt-controller.
- Controller tests for the usage record: present exactly while `Bound` or `Attached`, owned by its `GPUClaim`, independent of tenant labels, not reported twice, kept through reconciles of the `Workload` controller although it has no monitor label, and gone with its claim.
- E2E with passthrough GPUs on the GPU runner: order, attach, stop, confirm another tenant cannot take the GPU, start again, and check the usage record.

## Rollout

Step 1:

1. The host layer: the node layout, profile writes, node labels, and the device plugin with NVIDIA/kubevirt-gpu-device-plugin#192 applied downstream. It needs no tenant API and can land first.
2. `GPUClass` with its status controller and metrics, class entries in `vm-instance` and `kubernetes-nodes` with the VMI webhook, the `gpu.vgpu` Tenant flag, GPU keys with the interim check, and the `gpuclass` option source, on the existing variants that serve GPUs to VMs, `default` and `vgpu`; elsewhere the CRD ships and the charts refuse class entries. Before tenants use vGPU classes, the `gpu.vgpu` flag is set from the root tenant down. It works on any cluster where each offer it publishes has a resource name of its own, with or without item 1.
3. GPU keys through #48, and the interim check deleted, in the release where #48's gate is on by default.

Step 2:

4. Holding: `GPUClaim`, the binder and its metrics, the launcher pod webhook and gate, the claim order webhook and tamper policy, template claims for class entries, `claim` entries in `vm-instance`, `claimSelector` in `kubernetes-nodes` for Talos pools with passthrough classes, claim usage records, `onCordon`, and `gpu.rawResourceNames`. A cluster with GPU pools on raw names keeps raw names allowed and has no hard hold guarantee.

Then:

5. The consumers: `kairos-vgpu-workers` adds Kairos GPU pools on class entries, and on `claimSelector` once step 2 ships, and `vgpu-vms` adds GPU guest images.
6. The upstream plugin in place of the downstream patch once NVIDIA/kubevirt-gpu-device-plugin#192, or what replaces it, ships upstream, and `dra` classes once NVIDIA's DRA driver serves vGPU to KubeVirt; an offer moves to a new class of the other source, since a class's kind of source cannot change. The plugin change touches only the host layer and the `resourceName` of affected classes, with raw-name consumers of a renamed resource moved in the same window. A `dra` class is a new class, so tenants move to it by naming it.

## Open questions

- Release on tenant suspension. A hoster may want a suspended tenant's held GPUs released after a GPU-specific grace period; Cozystack has no platform-wide notion of suspension yet.
- Packing policy. Fullest-node packing is the binder's default in step 2; hosters may need spreading across failure domains as a class option.
- Held claims under #48. A `GPUClaim` is not an application, so #48's oracle, which sums application releases, does not see it. Step 2 needs either `GPUClaim` counted next to applications in the pool figure, or the claim made an application kind with a declared consumption.

## Alternatives considered

A `gpu.cozystack.io` group for the GPU kinds and their keys. Rejected ([0013](./decisions/0013-the-gpu-kinds-live-in-cozystack-io.md)): no requirement here needs a group of its own, and a CRD cannot move to another group in place.

Holding in step 1, as earlier revisions had it. Rejected ([0016](./decisions/0016-holding-is-step-2-and-stays-gpu-specific.md)): step 1 answers five of the six problems without a hold, the third only for budgets, and the hold brings the binder, moves, lost capacity and the pod gate into a v1alpha1 tenant API.

A generic reservation for stopped VMs that covers CPU, memory and GPUs. Rejected for now ([0016](./decisions/0016-holding-is-step-2-and-stays-gpu-specific.md)): CPU and memory are interchangeable across nodes, a GPU hold is a whole unit on one node that does not migrate.

DRA as the whole API, with `DeviceClass` as the catalog and `ResourceClaim` as the tenant object, or DRA first for passthrough with vGPU later. Rejected for now: a non-Pod `reservedFor` entry does keep a `ResourceClaim` allocated for a stopped VM, but the NVIDIA DRA driver does not serve vGPU yet and its passthrough is alpha, and vGPU is the case hosters sell first. DRA is the planned backend under `GPUClass` (section 10).

`GPUNode`, an inventory kind per node written by a node agent. Rejected ([0015](./decisions/0015-node-allocatable-is-the-gpu-inventory.md)): node allocatable gives capacity and loss, and the `Workload` records give use per tenant.

A cluster-scoped object per reservation unit, like `IPAddress` in #35. Rejected for the device-plugin backend, where kubelet picks the physical device and such an object would promise a precision the backend does not have.

Tenant quota on the resource name only. Stock `requests.<resource>` quotas need no new code, but they refuse only at pod creation and tie a budget to a name the host layer can change.

Placeholder pods that hold a GPU until the VM starts. Rejected because preemption picks victims by priority, not by owner, so one tenant's VM could evict another tenant's placeholder.

Class-level reservations for worker pools, without per-worker claims. Rejected because a pool would get a promise of capacity, not GPUs.

A cluster-wide reservation per tenant and class, a count without a node. It needs no binding, moves or pod pin, and for single-GPU offers it would be enough. Rejected because the count holds only in sum. With free units spread one per node, a held set of two fits nowhere. A vGPU profile exists only on the nodes the layout gives it. A lost node silently shrinks every tenant's promise instead of showing which claims are `Lost`.

NVIDIA vgpu-device-manager as the profile writer. At v0.5.1 it writes mdev profiles only and has no path for `current_vgpu_type` on SR-IOV VFs, and the pull requests that added VF support were closed unmerged, NVIDIA/vgpu-device-manager#139 as not planned, and NVIDIA/vgpu-device-manager#195 after NVIDIA named gaps on the KubeVirt side and had no resources for that test round. The node agent writes VF profiles instead; nodes on the mdev path keep vgpu-device-manager (section 2).

KubeVirt's built-in device plugin for VFs (kubevirt/kubevirt#16890), with the sandbox plugin for passthrough. It ships in KubeVirt v1.9 and needs no patch, but it gives every vGPU profile of a model one resource name, which breaks the host layer's one name per offer. On GPUs whose whole GPU and VFs share a PCI ID it clashes with the sandbox plugin, so both modes of such a model need the built-in plugin for passthrough too, and both modes share one name. Switching between it and the sandbox plugin is a maintenance window each time. Rejected in favour of the downstream patch, whose cost is carrying one patch until upstream settles and running the plugin privileged with NVML access.
