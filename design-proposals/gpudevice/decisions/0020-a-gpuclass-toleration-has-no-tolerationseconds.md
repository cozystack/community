# 0020. A `GPUClass` toleration has no `tolerationSeconds`

- **Number:** `0020`
- **Date:** `2026-11-10`
- **Status:** Proposed
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/cozystack#4998`](https://github.com/cozystack/cozystack/pull/4998#pullrequestreview-5842083890)
- **Implemented in:** not yet

## Context

[0010](./0010-gpuclass-carries-the-tolerations-binding-reads.md) gave `GPUClass` tolerations in the shape of a pod's, and the preview API in cozystack/cozystack#4998 took that shape with `tolerationSeconds`. @kvaps [found](https://github.com/cozystack/cozystack/pull/4998#pullrequestreview-5750199926) that a class tolerating a `NoExecute` taint for 300 seconds counted the node as free, so a VM landed there and was evicted five minutes later. He offered two fixes: not counting such a toleration, or dropping the field. The preview took the first. @lllamnyp [asked](https://github.com/cozystack/cozystack/pull/4998#pullrequestreview-5842083890) for the second, since the design does not need the field.

## Decision

A `GPUClass` toleration has `key`, `operator`, `value` and `effect`, and no `tolerationSeconds`. The field is removed before the first release of the kind. Tolerations copied to launcher pods therefore never carry a duration.

The CRD prunes unknown fields, so the field-validation mode of a request decides what happens to the removed field. With `Strict` a request that sets it fails. With `Warn` it gets a warning and the field is dropped, and with `Ignore` it is dropped silently. A dropped duration turns a finite toleration into an indefinite one, so whoever used the preview removes the field from manifests on purpose. Launcher pods that already run keep the tolerations they were created with.

## Why not the alternatives

- **Keep the field and leave such nodes out of `free`**, as the preview does after the review. Capacity is then right, but the VM is still evicted after the duration, because the toleration reaches the pod.
- **Keep the field and refuse every value with a validation rule.** It makes rejection the same for every client, at the cost of a field that exists only to be refused in an unreleased kind. This lost on judgement, not on a hard fact.

## Consequences

- A class tolerates a `NoExecute` taint for good or not at all. A hoster cannot give it a grace period before eviction.
- A request with `Ignore` does not report that the field was dropped.
- No API or implementation changes are part of this proposal; existing fields stay until the contract is accepted.

## Revisit if

A hoster needs a per-class grace period, for example for planned maintenance taints. Then the field comes back with its capacity and eviction rules stated.
