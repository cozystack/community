# 0003. The usage-monitor label reservation is always on, and it ratchets

- **Number:** `0003`
- **Date:** `2026-10-20`
- **Status:** Accepted
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#99`](https://github.com/cozystack/community/pull/99)
- **Implemented in:** [`cozystack/cozystack#5008`](https://github.com/cozystack/cozystack/pull/5008); refusing a changed value: not yet

## Context

Section 5 keeps the GPU usage records under a `WorkloadMonitor` whose selector is a reserved label key, `gpu.cozystack.io/usage-monitor`, and has admission refuse that key on every object in a tenant namespace, on create and update, so no object can match the monitor and no tenant can shape the GPU usage records.

Two cases were left open. First, the GPU admission policies render only where GPUs serve VMs ([0002](./0002-gpuclaim-serves-vms-only-where-gpu-operator-serves-gpus-to-vms.md)). Had the reservation followed them, a tenant could label its objects on a cluster without GPUs, and they would match the monitor the day the hoster turns GPUs on. Second, an object can already carry the key when the policy first renders, from before the upgrade that brought it. A policy that refuses every write of an object carrying the key would refuse every update of that object that keeps the label, including the one in which a controller removes its finalizer, so its deletion would hang until someone drops the label by hand.

## Decision

The reservation renders on every cluster where the `ValidatingAdmissionPolicy` API exists, whether or not GPUs are on, and it ratchets: it refuses a create that carries the key and an update that adds the key or changes its value, and it allows an update that keeps the value or drops the key. The rule lives in `packages/system/cozystack-basics/templates/gpu-usage-monitor-label-policy.yaml`, with its cases in `internal/gpu/policytest/usagemonitor_test.go`.

## Why not the alternatives

- **Render it with the other GPU policies.** That leaves the window above open, so the guarantee of section 5 would hold only for labels written after GPUs were enabled.
- **Refuse the key on every create and update, as section 5 has it.** It blocks every update of an object that predates the policy and carries the key, finalizer removal included, so controllers that know nothing about the label stall on it until a person removes the label.
- **Strip the key at admission instead of refusing it.** A mutating webhook on every object in every tenant namespace makes the controller's availability a dependency of every tenant write, for a label no legitimate writer sets.

## Consequences

- No new object in a tenant namespace can match the GPU usage monitor, on any cluster.
- An object that carried the key before the policy existed keeps it until someone removes it, and until then, if its value is `true`, the monitor's selector matches it. The policy cannot see such objects; the hoster finds them by listing objects with the label.
- The policy matches every create and update of every namespaced object in every tenant namespace, whether or not the cluster uses GPUs, so its expression reads only metadata.

## Revisit if

Cozystack gets a way to reserve label keys for its own controllers across tenant namespaces, so each reserved key no longer needs its own policy.
