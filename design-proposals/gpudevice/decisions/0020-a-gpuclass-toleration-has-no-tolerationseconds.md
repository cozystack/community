# 0020. A `GPUClass` toleration has no `tolerationSeconds`

- **Number:** `0020`
- **Date:** `2026-11-10`
- **Status:** Accepted
- **Deciders:** `@lexfrei, @lllamnyp`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/cozystack#4998`](https://github.com/cozystack/cozystack/pull/4998#pullrequestreview-5842083890)
- **Implemented in:** not yet

## Context

[0010](./0010-gpuclass-carries-the-tolerations-binding-reads.md) gave `GPUClass` tolerations in the shape of a pod's, and the preview API in cozystack/cozystack#4998 took that shape with `tolerationSeconds`. @kvaps [found](https://github.com/cozystack/cozystack/pull/4998#pullrequestreview-5750199926) that a class tolerating a `NoExecute` taint for 300 seconds counted the node as free, so a VM landed there and was evicted five minutes later. He offered two fixes: not counting such a toleration, or dropping the field. The preview took the first. @lllamnyp [asked](https://github.com/cozystack/cozystack/pull/4998#pullrequestreview-5842083890) for the second, since the design does not need the field.

## Decision

A `GPUClass` toleration has `key`, `operator`, `value` and `effect`, and no `tolerationSeconds`. The field is removed before the first release of the kind. Tolerations copied to launcher pods therefore never carry a duration.

## Why not the alternatives

- **Keep the field and leave such nodes out of `free`**, as the preview does after the review. Capacity is then right, but the VM is still evicted after the duration, because the toleration reaches the pod.
- **Keep the field and refuse every value with a validation rule.** It makes rejection the same for every client, at the cost of a field that exists only to be refused in an unreleased kind. This lost on judgement, not on a hard fact.

## Consequences

- A class tolerates a `NoExecute` taint for good or not at all. A hoster cannot give it a grace period before eviction.

## Revisit if

A hoster needs a per-class grace period, for example for planned maintenance taints. Then the field comes back with its capacity and eviction rules stated.
