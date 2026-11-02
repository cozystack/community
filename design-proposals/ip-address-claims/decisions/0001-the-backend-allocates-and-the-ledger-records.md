# 0001. The backend allocates; the ledger records what it assigned

- **Number:** `0001`
- **Date:** `2026-07-20`
- **Status:** Accepted
- **Deciders:** `@lllamnyp`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/metallb-iad#1`](https://github.com/cozystack/metallb-iad/pull/1)
- **Implemented in:** [`cozystack/metallb-iad#1`](https://github.com/cozystack/metallb-iad/pull/1) (merged 2026-07-23)

## Context

The proposal's §3 said that for self-allocating backends — MetalLB, Cilium LB-IPAM,
kube-vip — *"our provisioner is the IPAM of record"*: nothing in those backends holds a
reservation, so the ledger would carve addresses from the class range itself and the
backend would merely be told which address to pin.

The reference MetalLB driver was first built that way. It walked the class CIDRs, skipped
the network and broadcast addresses and every address already in the ledger, took the
first free one, created the `IPAddress`, and relied on create-collision on the object name
as its lock
([design §10](https://github.com/cozystack/metallb-iad/blob/main/docs/design.md#10-alternative-considered-driver-side-first-free-allocation)).

Two things were wrong with it, and both were structural rather than bugs. A ledger-only
reservation is **invisible to MetalLB**: a held-but-unattached address was free in
MetalLB's books, so the entire anti-theft posture rested on the admission policy, which the
proposal had already conceded is defeated by impersonation. And the driver was a **second
IPAM** that could disagree with MetalLB's live state: it could pick an address some plain
Service already held from an overlapping pool, and discover the collision only at pin
time. Reviewer @lexfrei had argued the same point from the other direction on the proposal
([review, 2026-07-23](https://github.com/cozystack/community/pull/35#pullrequestreview-4763639339)):
a reservation should be enforced in the allocator's own accounting, with detection as the
backstop.

## Decision

The driver does not compute addresses. To reserve one it creates a selectorless
`type: LoadBalancer` **placeholder Service** in its own namespace, drawing from the class's
pool; MetalLB assigns an address to it and, the Service having no endpoints, never
announces it. The driver observes the assignment, pins the placeholder to it, and only then
records the `IPAddress`. A held address therefore exists in MetalLB's accounting for as
long as it is held, and MetalLB itself refuses to hand it to any other Service. The ledger
is the record of reservation; the backend is the allocator of record. Association is a
**handoff**: record `associatedTo`, delete the placeholder, pin the workload's Service.

## Why not the alternatives

- **Driver-side first-free allocation** (the first implementation, replaced in
  [`56b3d95`](https://github.com/cozystack/metallb-iad/commit/56b3d95)). Lost on the two
  structural facts above: a reservation MetalLB cannot see is not enforced by anything
  but identity-keyed admission, and a parallel allocator can split-brain against the
  backend's live state. The design remains valid for a backend with no allocation API at
  all, but MetalLB is not such a backend — allocation by Service *is* its API.
- **Gapless handoff by sharing the address between placeholder and workload**
  (`metallb.io/allow-shared-ip`, analysed in
  [`e2e608f`](https://github.com/cozystack/metallb-iad/commit/e2e608f)). Deferred, not
  rejected: MetalLB's sharing rules would impose `externalTrafficPolicy: Cluster` and
  port-disjointness on tenant Services. Kept as an opportunistic optimisation for Services
  that happen to satisfy both.

## Consequences

- A reserved-but-unattached address is safe to leave lying around, which the proposal
  could not previously claim; admission and conflict detection become defence in depth
  rather than the primary guarantee. Proposal §6 was rewritten around this.
- One placeholder Service, with a ClusterIP, per held address. Cheap, but not free, and
  visible in the driver's namespace.
- The handoff is not atomic. Between deleting the placeholder and pinning the workload's
  Service the address is briefly unheld; the window is bounded by reconcile latency and is
  the one interval where the admission policy is load-bearing.
- MetalLB assignments are sticky and `autoAssign: false` gates only *new* allocation
  (observed on a live cluster,
  [design §5](https://github.com/cozystack/metallb-iad/blob/main/docs/design.md#5-association-the-gap-handoff)).
  So withdrawing the pin from a live Service does not take the address away from it: the
  ex-holder keeps announcing, the re-created placeholder sits `Pending`, and the ledger
  reports `Conflict` naming the ex-holder until that Service is deleted or re-addressed.
  Detaching an address from a live Service means recreating the Service; the proposal's
  §5 now says so instead of claiming the announcement is withdrawn.
- The `IPAddress` is created after allocation is a fact, which is the allocate–observe–record
  flow a cloud driver would use with a provider-side reservation; the placeholder plays
  that object's role. This is why the proposal's §3 no longer says the provisioner is the
  IPAM of record for self-allocating backends.

## Revisit if

MetalLB or Cilium LB-IPAM gains a first-class reservation object that can be held without
a Service, or the shared-handoff optimisation proves safe enough to make the default. A
backend with no allocation-by-Service API at all would reopen first-free allocation for
*that* driver only.
