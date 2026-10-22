# 0010. `GPUClass` carries the tolerations that binding and `free` read

- **Number:** `0010`
- **Date:** `2026-10-23`
- **Status:** Accepted
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#99`](https://github.com/cozystack/community/pull/99#pullrequestreview-5616164700)
- **Implemented in:** not yet

## Context

Section 2 kept binds, moves, rebinds and gathers off nodes with a `NoSchedule` or `NoExecute` taint "that virt-launcher pods do not tolerate", and [0005](./0005-the-taint-rule-skips-exactly-three-kubernetes-taints.md) fixed which taints that rule skips. @kvaps pointed out in review that a claim is bound when it is ordered, before any VM or pod exists, so there are no tolerations to check a taint against. The implementation checked against none, so a dedicated taint on GPU nodes, the usual way to keep other workloads off them, took every such node out of binding and out of `free`, even for VMs that tolerate it.

## Decision

`GPUClass` has a `tolerations` field, in the shape of a pod's, which the hoster sets to the taints of the class's nodes that its VMs tolerate. A node counts for binding, moves, rebinds, gathers, capacity, `free` and `maxFreePerNode` of a class only if the class tolerates each of its `NoSchedule` and `NoExecute` taints, except the three that 0005 skips, `node.kubernetes.io/unschedulable`, `node.kubernetes.io/not-ready` and `node.kubernetes.io/unreachable`, for the reasons 0005 gives. A set of claims of several classes takes a node only when every class of the set tolerates its taints. The virt-launcher pod of a VMI gets the tolerations of its claims' classes when it is created. `tolerations` can change while claims are held, like `nodeSelector`.

## Why not the alternatives

- **Check taints against no tolerations, as the implementation did.** A dedicated taint takes the node out of the offer, which is the setup a hoster with dedicated GPU nodes uses.
- **Apply the taint rule only at gather time, against a concrete VMI**, the other option in the [review](https://github.com/cozystack/community/pull/99#pullrequestreview-5616164700). Binding and `free` would then ignore taints, so a claim could be bound onto a node no VM of the class can schedule to, and `free` would count units no VM can reach, until a VMI starts and the gather has to move the claim anyway.
- **Read the tolerations from the `vm-instance` chart's defaults.** They describe VMs, not the class, and a claim bound for a worker pool or a VM written directly would not follow them.

## Consequences

- A hoster with tainted GPU nodes lists the taint on every class of those nodes. Without it the class shows no free units there, and a failure case of the proposal says so.
- The class's tolerations reach the pod, so a tenant cannot get a GPU VM onto a tainted node without a class that tolerates the taint.
- A change of `tolerations` moves nothing by itself: as with any taint, a bound claim keeps its node, and only new binds, moves and gathers read the new list.

## Revisit if

Kubernetes lets a reservation be scheduled ahead of its pod, so tolerations can be checked against the pod that will use the GPU, for example through DRA (section 8).
