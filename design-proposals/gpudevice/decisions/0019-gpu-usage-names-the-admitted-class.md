# 0019. GPU usage carries the class a VM was admitted with, so classes may share a resource name

- **Number:** `0019`
- **Date:** `2026-11-10`
- **Status:** Proposed
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/cozystack#4998`](https://github.com/cozystack/cozystack/pull/4998#pullrequestreview-5842083890)
- **Implemented in:** not yet

## Context

Section 3 made a class one resource name, unique among classes, and section 7 let a billing system map a `Workload` record to a class through that name ([0014](./0014-step-1-names-a-class-and-holds-nothing.md)). Budgets did not need the uniqueness, since they count class entries. The mapping needed it, because the class name was lost before the record: the VMI webhook rewrote a class entry into KubeVirt `gpus` entries that carry only the resource name, and the `WorkloadMonitor` summed the virt-launcher pod's requests.

`resourceName` could change (section 3). @kvaps [found](https://github.com/cozystack/cozystack/pull/4998#pullrequestreview-5750199926) that a creation-time rule then hands a name to an older class edited onto it, and the class tenants already run on loses its offer. The implementation in cozystack/cozystack#4998 answered with `status.ownedResourceName`, which records the owner by object UID. @lllamnyp [asked](https://github.com/cozystack/cozystack/pull/4998#pullrequestreview-5842083890) for a record of that change and pointed at the StorageClass model: many StorageClasses share one provisioner, and use is counted under the class name the PVC carries.

## Decision

The usage record carries the class, so uniqueness is no longer needed. When a VMI is created, the VMI webhook writes a snapshot of each class entry: the class UID and name, the count, and the resource name the class had at that moment. The snapshot never changes for the life of the VMI, and an update that adds, edits or removes it is refused.

The virt-launcher pod webhook copies the snapshot to the pod only when the pod's owner is that VMI by UID, the pod was created by KubeVirt's controller, and its GPU devices and requests match the snapshot. A later edit of the pod's snapshot is refused too. The `WorkloadMonitor` then reports the pod's GPUs under `<class>.gpuclass.cozystack.io/devices`, counted from the pod's actual requests, instead of under the resource name. The generic `workload.cozystack.io/resources` annotation cannot set these keys.

Two classes may then share a resource name, and `ResourceNameConflict` and `status.ownedResourceName` go away. `resourceName` stays mutable: a change applies to VMs started after it, and a running VM keeps the name in its snapshot.

A vGPU class requires `profile` and a passthrough class cannot set one. Section 3 only marked the field "vgpu only"; the schema is stricter, and this record makes that the contract.

| Who | What they get |
| --- | --- |
| Tenant writing a VM or VMI | picks a class through a class entry, cannot write or change the snapshot |
| Tenant creating a pod by hand | can request a raw resource name; copied labels, owner references or annotations are not a snapshot, so the use stays under the raw name |
| KubeVirt's controller | gets the snapshot on a launcher pod only when the pod matches the VMI's UID, devices and requests |
| Hoster editing a class | a `resourceName` change reaches new VMs only; a deleted and recreated class has a new UID and inherits no old usage |
| Cluster admin | can change admission and RBAC, so is outside what this protects |

## Questions from the review

Why recorded state and not a stateless rule like `internal/shared/appdefowner`? A stateless rule orders by creation time, and a class can change `resourceName`. An older class edited onto a used name then takes it from the class whose VMs are already running. Recording the owner was specific to GPU classes and is not a pattern for new code. With this record nothing needs one owner per name.

What happens when status is lost? With the recorded owner, a restore or re-create without status falls back to creation order and can pick a different class. The platform cannot tell such a restore from a new collision. With this record there is no ownership state to lose: each running VM and its pod carry their own snapshot.

Why fail closed when two classes record one name? A tie-break would leave usage records whose name maps to two classes, and billing could charge the wrong offer. The cost was real: a controller overlap could stop new starts of an offer that already has running VMs. With this record a shared name is a supported catalog, not a conflict.

## Why not the alternatives

- **Keep the recorded owner**, as cozystack/cozystack#4998 does. It keeps the recovery and availability costs above and still forbids two offers on one profile.
- **Freeze `resourceName`**, the other option in the [review](https://github.com/cozystack/cozystack/pull/4998#pullrequestreview-5842083890). Then only creates collide. Creation time is stored in whole seconds, so two classes created in the same second fall back to the name, and a later class with a lexically smaller name wins over one with running VMs. It also turns a device-plugin rename into a new class every tenant has to move to, which section 2 avoided.
- **Keep uniqueness with a stateless creation-time rule.** An edit onto a used name moves the offer, which is the case @kvaps found.

## Consequences

- A licensed class and a bring-your-own-license class can share one vGPU profile ([vgpu-vms](../../vgpu-vms/README.md)). Picking a class does not grant a license: secret access, entitlement and the one-vGPU-class-per-VM rule keep their own checks.
- Two classes on one resource name are two budgets for the same cards, which section 3 ruled out. A hoster who does not want that publishes one class per name.
- Classes on one resource name describe the same cards. Their capacity and used-unit metrics are views, not additive, and physical totals are per resource name.
- A billing system reads the class from the record instead of mapping names through the catalog. Pods without a valid snapshot, raw-name pods and pods created before the change stay under the resource name, and are never counted under both keys.
- A pod that requests no GPU, such as the one that binds `WaitForFirstConsumer` volumes, counts zero. Each launcher pod counts what it requests.
- Both webhooks gain an update rule, and the `WorkloadMonitor` gains a GPU path. Before this replaces the current implementation, tests cover shared names, a forged snapshot, a UID or request mismatch, a rename while running, a recreated class, raw-name fallback and double counting.
- No API or implementation changes are part of this proposal; existing fields stay until the contract is accepted.

## Revisit if

KubeVirt passes a DRA device class through to the launcher pod, so Kubernetes itself records which class a device came from (section 10).
