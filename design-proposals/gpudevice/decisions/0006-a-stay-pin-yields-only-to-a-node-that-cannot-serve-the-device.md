# 0006. A `Stay` pin holds through `NotReady` and taints, and breaks when the node cannot serve the device

- **Number:** `0006`
- **Date:** `2026-10-20`
- **Status:** Accepted
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#99`](https://github.com/cozystack/community/pull/99)
- **Implemented in:** not yet

## Context

Section 3 pins a VMI to a cordoned node when a device of a `Stay` class is there and all of the VMI's devices are on that node, with no template devices: there is no gather, "even if the node is also not `Ready`", and the VMI waits. Section 2 has a `Stay` device keep its node through a cordon, while lost capacity, a node that is gone and a node that no longer serves the class are hard reasons for a device to leave. The proposal did not say which of the two wins when a pinned node also has a hard reason, so a node under maintenance that then died could hold a VM's devices, and keep them in the usage records, for as long as the dead node stayed registered.

## Decision

The pin holds while the node's only problems are the cordon, a `NotReady` spell shorter than the lost-capacity grace period, or a taint virt-launcher pods do not tolerate. It breaks when a member of the set on that node has lost capacity there, when the node is gone, or when it no longer serves a member's class. Then the set is treated as any set with a hard reason to leave (sections 2 and 3): a new VMI is refused when the node is gone, no longer serves the class, or has lost capacity the device does not fit under, and otherwise its set is gathered onto one node with room for all of it; a VMI already waiting there unscheduled is deleted when its devices must leave, and the unattached set moves together, or goes to `Lost` together when no node has room. The binder at admission and the controller's background pass read the pin through one function, so admission never pins a VMI that the next pass deletes.

## Why not the alternatives

- **The pin always wins.** A `Stay` class exists for a hoster who wants VMs back on the same hardware after maintenance. A dead node is not coming back to the same state on any known schedule, and the VM's devices would stay held and recorded against hardware that serves nothing.
- **Break the pin on `NotReady` alone.** It undoes the point of `Stay` for the most common maintenance step, a reboot, and section 2 already uses the lost-capacity grace period so that a short outage moves nothing.
- **Break the pin on any taint.** Apart from a gather at VMI creation, a taint never moves a bound device; it only keeps new binds off the node (section 2). Breaking the pin on one would make a `Stay` device leave in a case where a device of a `Move` class on an uncordoned node stays.

## Consequences

- A `Stay` VM waits through a reboot of its cordoned node and comes back on it, and after a node failure its devices move like any other VM's once the grace period has passed.
- A pinned VM waits on a dead node for the lost-capacity grace period, five minutes by default, before its devices can move.

## Revisit if

Hosters ask for a `Stay` policy that also outlasts a node failure, for example to wait for a repaired node rather than move off it.
