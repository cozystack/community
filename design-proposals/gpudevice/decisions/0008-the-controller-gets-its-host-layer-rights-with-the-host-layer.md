# 0008. The GPU controller gets its node-label and plugin-restart rights with the host layer, not before

- **Number:** `0008`
- **Date:** `2026-10-20`
- **Status:** Accepted
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#99`](https://github.com/cozystack/community/pull/99)
- **Implemented in:** [`cozystack/cozystack#5013`](https://github.com/cozystack/cozystack/pull/5013) (the VMI deletion right only)

## Context

The Security section lists three rights the GPU controller gains beyond its own kinds and the usage records: patching node labels, restricted by a policy to the GPU workload label and `gpu.cozystack.io/*`; restarting the device plugin pod on a node; and deleting unscheduled VMIs that reference its claims. Only the third is used by Rollout step 1. Node labels and plugin restarts belong to the node layout of section 6, which ships as Rollout step 4, and until then the hoster writes node labels by hand.

## Decision

Of the three, Rollout step 1 grants the controller only the VMI deletion. The node-label patch right, the policy that restricts it, and the right to restart the device plugin pod ship with the host layer, together with the code that uses them.

## Why not the alternatives

- **Grant all three in step 1, as the Security section lists them.** Every cluster would carry a write right on every `Node` that no code uses, and the policy that narrows it would have no writer to test against.
- **Grant the label right without its policy until the host layer ships.** That is a broader right than the proposal ever allowed.

## Consequences

- The `update` and `delete` rights on tenant pods that the launcher pod gate of section 3 needs are a separate grant of step 1, not one of the three.
- Between step 1 and step 4 the controller cannot change any node, so the GPU node labels that classes match are the ones the hoster writes.
- The rights and their policy are reviewed in step 4, next to the code that uses them.

## Revisit if

The host layer is dropped or replaced by one that does not need the controller to write nodes.
