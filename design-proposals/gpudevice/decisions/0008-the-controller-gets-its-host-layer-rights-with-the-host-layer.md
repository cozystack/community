# 0008. The GPU controller gets its node-label right with the host layer, not before

- **Number:** `0008`
- **Date:** `2026-10-20`
- **Status:** Accepted
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#99`](https://github.com/cozystack/community/pull/99)
- **Implemented in:** [`cozystack/cozystack#5013`](https://github.com/cozystack/cozystack/pull/5013) (the VMI deletion right only)

## Context

The Security section listed three rights the GPU controller gained beyond its own kinds and the usage records: patching node labels, restricted by a policy to the GPU workload label and `gpu.cozystack.io/*`; restarting the device plugin pod on a node; and deleting unscheduled VMIs that reference its claims. Only the third was used by Rollout step 1. Node labels and plugin restarts belonged to the node layout of section 6, which was Rollout step 4, and until then the hoster wrote node labels by hand.

## Decision

The node-label patch right and the policy that restricts it ship with the host layer, together with the code that uses them, and the VMI deletion right ships with step 2, which uses it. The device plugin restart is not the controller's: the node agent of the host layer restarts the plugin pod on its own node after writing profiles, under a policy that limits it to that pod. No GPU code gets a right before the code that uses it.

## Why not the alternatives

- **Grant all three with the first GPU code, as the Security section listed them.** Every cluster would carry a write right on every `Node` that no code uses, and the policy that narrows it would have no writer to test against.
- **Grant the label right without its policy until the host layer ships.** That is a broader right than the proposal ever allowed.

## Consequences

- The `update` and `delete` rights on pods in every namespace that the launcher pod gate needs are a separate grant of step 2, not one of the three.
- Until the host layer ships, the controller cannot change any node, so the GPU node labels are the ones the hoster writes.
- The rights and their policy are reviewed with the host layer, next to the code that uses them.

## Revisit if

The host layer is dropped or replaced by one that does not need the controller to write nodes.
