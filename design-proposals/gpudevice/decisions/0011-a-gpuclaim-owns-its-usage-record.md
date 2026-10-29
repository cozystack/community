# 0011. A `GPUClaim` owns its usage record, and no monitor or reserved label guards it

- **Number:** `0011`
- **Date:** `2026-10-23`
- **Status:** Accepted
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#99`](https://github.com/cozystack/community/pull/99#pullrequestreview-5616164700)
- **Implemented in:** not yet

## Context

Section 5 kept each claim's `Workload` usage record under a `WorkloadMonitor` that the GPU controller owned in the tenant's namespace. The `Workload` controller deletes a record whose `workloads.cozystack.io/monitor` label is missing or names a monitor that is gone, so the record needed a monitor, and the monitor needed a selector. To keep tenant objects from matching that selector, the selector was a reserved label key, and an admission policy refused it on every create and update of every object in every tenant namespace, on every cluster, whether it used GPUs or not, with a ratchet for objects that carried the key before the policy existed. Record 0003 held that rule; it was withdrawn before the proposal merged, and this record replaces it.

@kvaps asked in review why a policy on every tenant write protects a monitor whose selector must match nothing, when the record could have an explicit owner, and which object owns the GPU record, since monitor-made records are also removed by ownerReference garbage collection.

## Decision

Each GPU usage record carries an ownerReference to its `GPUClaim` and no monitor label. The GPU controller keeps it while the claim holds GPUs, as section 9 defines, and garbage collection removes it with the claim. The `Workload` controller leaves a record owned by a `GPUClaim` to its owner instead of deleting it for a missing monitor. There is no GPU `WorkloadMonitor`, no reserved label key and no admission policy for it.

## Why not the alternatives

- **Keep the monitor and reserve its label key, with a ratcheting policy**, as 0003 had it and the [review](https://github.com/cozystack/community/pull/99#pullrequestreview-5616164700) questioned. The policy runs on every write in every tenant namespace on every cluster to protect one selector, and objects labelled before it existed still match the monitor until someone removes the label. It answers a problem the monitor created.
- **Keep the monitor with a selector nothing can match by construction, such as a label value tenants cannot write.** Kubernetes has no label value that a writer of an object cannot set, so this needs the same policy.
- **Write the record with no owner and no monitor label.** The `Workload` controller deletes such a record as soon as it sees it, and nothing would remove a record whose claim was deleted while the GPU controller was down.

## Consequences

- No admission policy matches tenant writes for usage records, and a cluster without GPUs carries nothing for them.
- The `Workload` controller gets a rule for one owner kind. Another kind of record that wants the same treatment needs its own case there.
- A billing system that lists records by monitor finds GPU records by `kind: gpu-claim` instead.

## Revisit if

The `Workload` controller moves to ownerReferences for every record, so the monitor label is no longer how it decides that a record is orphaned.
