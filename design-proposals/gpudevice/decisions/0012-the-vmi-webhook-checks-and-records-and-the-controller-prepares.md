# 0012. The VMI webhook only checks and records claims, and the controller places them while the launcher pod waits at a scheduling gate

- **Number:** `0012`
- **Date:** `2026-10-23`
- **Status:** Accepted
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#99`](https://github.com/cozystack/community/pull/99#pullrequestreview-5616164700)
- **Implemented in:** not yet

## Context

Section 3 had the mutating webhook on VirtualMachineInstance creation do all of the start's GPU work inside the admission call, failing closed: record the attachments, have the binder move the VMI's claims onto one node, and create and bind its template claims, all within the admission timeout, refusing the VMI when a move or a bind did not finish in time. The implementation in [`cozystack/cozystack#4998`](https://github.com/cozystack/cozystack/pull/4998) and [`#5030`](https://github.com/cozystack/cozystack/pull/5030) needed a synchronous handshake for that, a `status.bindRequest` on the claim that the webhook wrote and then waited up to ten seconds for the binder to answer, which the proposal did not describe. A refused VMI went back to KubeVirt's VM controller, whose per-item backoff grows to 1000 seconds, so a VM could start many minutes after room appeared.

@kvaps asked in review of [#99](https://github.com/cozystack/community/pull/99#pullrequestreview-5587787354) for the webhook to check, record and pin, and for the controller to do the rest, and turned that into a request in the [next review](https://github.com/cozystack/community/pull/99#pullrequestreview-5616164700).

## Decision

The VMI webhook resolves the claim references, runs the checks that need no placement, records the attachments of the held claims, and writes one `gpus` entry per GPU unit with the class's resource name, template units included, since KubeVirt renders the launcher pod's requests from the VMI and the VMI spec cannot change afterwards. It binds, moves and creates no claim and waits for nothing. A second mutating webhook adds the scheduling gate `gpu.cozystack.io/claims` and the classes' tolerations to the virt-launcher pod at creation, and to the temporary pod for `WaitForFirstConsumer` volumes, but not to migration target pods. The GPU controller then creates the template claims, gathers the set onto one node, and in one update appends a `metadata.name` requirement to every required node selector term of the pod and removes the gate. A set that cannot be placed keeps the pod gated, with a condition on the claims and the VMI. A pinned pod that its node refuses for lack of resources is deleted, and the node is avoided for that set for ten minutes or until it changes. For a set made only of template claims, the controller keeps its rejected nodes in memory against the VM, and a controller restart forgets them. `status.bindRequest` is dropped.

## Why not the alternatives

- **Keep the work in the webhook.** A fail-closed admission call that writes several objects and waits on another controller is slow under load, needs its own request protocol between the two, and turns every slow move into a refused VMI and a KubeVirt backoff.
- **Refuse the VMI whenever a gather is needed and let the controller move the claims before KubeVirt's retry.** It drops the handshake but keeps the backoff, so the start still comes up to the backoff limit after the claims are ready, and the tenant sees a refusal for a VM that will start.
- **Hold the VMI instead of the pod**, through a KubeVirt start strategy or a VM-level hold. KubeVirt v1.9.0 offers none that keeps the pod from being scheduled: `startStrategy: Paused` still schedules the pod and only pauses the guest. A scheduling gate is the hook Kubernetes provides for this, and KubeVirt neither touches it nor times it out.
- **Write the pin into the VMI's node affinity.** Under `vmRolloutStrategy: LiveUpdate` KubeVirt rewrites a VMI's affinity from the VM template, and it never changes a launcher pod's affinity, so the pod is the place that keeps the pin.

## Consequences

- The webhook is a pure check plus the attachment record, so its latency no longer depends on the binder, and a dry run only skips the record.
- Template claims are created after the VMI exists, so the controller sets their owner at creation, and a template claim no longer needs a grace period for a VMI that never appears.
- A set that cannot be placed is no longer an admission error. The VM shows `Starting`, the VMI waits in `Scheduling` with `PodScheduled=False`, reason `SchedulingGated`, and the reason is a condition on the claims and the VMI. The VM starts as soon as the set fits, with no KubeVirt backoff in between.
- The pod webhook is a fail-closed dependency of every start of a VM with claim references, like the VMI webhook.
- The cluster autoscaler does not scale up for a gated pod, and after the pin a new node cannot help that pod, so GPU node scale-up stays with the hoster.
- A pin cannot be undone, so a node that refuses a pinned pod costs one failed VMI and KubeVirt's start-failure backoff, which the rejected-node rule keeps from repeating.

## Revisit if

KubeVirt offers a hook between VMI creation and launcher pod scheduling that a controller can hold, so the pod no longer needs to be held on the Kubernetes side.
