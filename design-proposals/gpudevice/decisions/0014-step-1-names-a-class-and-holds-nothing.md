# 0014. In step 1 a `gpus` entry names a class, budgets count declared entries, and usage is the virt-launcher pod's `Workload`

- **Number:** `0014`
- **Date:** `2026-10-27`
- **Status:** Accepted
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#99`](https://github.com/cozystack/community/pull/99#issuecomment-6445311702)
- **Implemented in:** not yet

## Context

Rollout step 1 had everything at once: `GPUClass`, `GPUClaim` as a tenant order, `GPUNode` and its agent, the binder, three fail-closed webhooks, the launcher pod gate, budgets with their own subtree rule, and usage records the GPU controller wrote per claim. @kvaps asked in [review](https://github.com/cozystack/community/pull/99#pullrequestreview-5653244822) for the text to say which fields and phases exist in step 1 and what an entry means without the binder. @lllamnyp [went through](https://github.com/cozystack/community/pull/99#issuecomment-6445311702) what the proposal adds above one resource name per offer: the catalog is hoster metadata, per-offer budgets argue for class-scoped keys, refusing an order that does not fit is what [#48](https://github.com/cozystack/community/pull/48) does for every kind by counting declared sizes, and the `Workload` records already sum virt-launcher requests, GPU resource names included. Only the hold needs a tenant object.

The GPU budget rule of the former Quotas section diverged from the existing pool semantics and was meant to move to #48 later, so tenants would have migrated twice.

## Decision

Step 1 has no tenant object. A `vm-instance` or `kubernetes-nodes` `gpus` entry names a class and a count. The VMI webhook writes one KubeVirt `gpus` entry per unit with the class's resource name, after checking the class, `maxCount`, one vGPU class per VM, the vGPU entitlement and `permittedHostDevices`. A webhook on the virt-launcher pod adds the class's tolerations to the pod, since under `vmRolloutStrategy: LiveUpdate` KubeVirt resets a VMI's tolerations to the VM template's. Capacity is not checked at admission: a VM that finds no free unit waits in `Scheduling` like a VM short of CPU. A class is one resource name, unique among classes, and its nodes are the nodes that advertise it, so a class has no node selector.

GPU budgets are ordinary `resourceQuotas` keys under the existing pool rules, and #48's gate checks them at write, counting `count` per class entry and `count` times `maxReplicas` per pool from the defaulted values. Until #48's gate is on, an interim check in the Cozystack API server does the same for GPU keys on `VMInstance` and `KubernetesNodes` writes, and supplies the pool's class entries as GPU usage to the declaration gate on Tenant writes, with the same inputs and pool arithmetic; it stands aside while #48's gate is switched on and is deleted in the release where that gate is on by default.

Usage is the `Workload` the VM's `WorkloadMonitor` already keeps from the virt-launcher pod's requests. Step 1 writes no usage record of its own.

## Why not the alternatives

- **An order object in step 1, checked against budget and capacity at admission, without a hold.** Without the binder an order guarantees nothing at start, so the tenant would pay and wait for an object that only repeats what the VM's entry says.
- **Keep the GPU subtree rule until #48 takes over.** Tenants and hosters would learn one rule and then another, and #48 would have to adopt a GPU exception to keep verdicts the same.
- **Check capacity at VMI admission.** A refused VMI is left to the per-item backoff of KubeVirt's VM controller, which grows to 1000 seconds, for a shortage that may last seconds, while a pending pod starts as soon as a unit frees up.
- **Keep GPU-specific usage records next to the launcher pod's `Workload`.** The pod's record already carries the GPUs for exactly as long as they are used, and a second record would have to be kept from double counting.

## Consequences

- A stopped VM's class entries keep counting against the budget, as its CPU and memory do under #48, while its `Workload` shows no GPU.
- A billing system maps a resource name in a `Workload` record to a class through the catalog, and keeps the names it has seen when a class's `resourceName` changes.
- A vGPU class entry written by an unentitled tenant is accepted at write and refused at start, because the pool gate has no yes-or-no rule.
- Step 2 keeps the entry: it creates a template claim for it at start, so the same entry stays valid when holding ships.

## Revisit if

#48 does not ship or drops the pool semantics the interim check copies, or a hoster needs a capacity guarantee at start without holding.
