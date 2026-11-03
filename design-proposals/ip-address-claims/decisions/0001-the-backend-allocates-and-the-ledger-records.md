# 0001. The backend allocates; the ledger records what it assigned

- **Number:** `0001`
- **Date:** `2026-07-20`
- **Status:** Accepted
- **Deciders:** `@lllamnyp`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/metallb-iad#1`](https://github.com/cozystack/metallb-iad/pull/1)
- **Implemented in:** [`cozystack/metallb-iad#1`](https://github.com/cozystack/metallb-iad/pull/1) (merged 2026-07-23)

## Context

Proposal §3 said that for self-allocating backends (MetalLB, Cilium LB-IPAM, kube-vip)
"our provisioner is the IPAM of record": the ledger would carve addresses from the class
range and tell the backend which address to pin.

The first MetalLB driver did this. It walked the class CIDRs, skipped network and
broadcast addresses and every address in the ledger, took the first free one, created the
`IPAddress`, and used create-collision on the object name as its lock
([design §10](https://github.com/cozystack/metallb-iad/blob/main/docs/design.md#10-alternative-considered-driver-side-first-free-allocation)).

This had two defects. MetalLB did not know about the reservation: a held, unattached
address was free in its accounting, and only the admission policy, which impersonation
defeats, stopped a pin from taking it. And the driver allocated independently of MetalLB,
so it could pick an address a plain Service already held from an overlapping pool and
detect the collision only at pin time. @lexfrei's review of the proposal
([2026-07-23](https://github.com/cozystack/community/pull/35#pullrequestreview-4763639339))
asked for the same change from the other side: enforce the reservation in the allocator's
own accounting, with detection as the backstop.

## Decision

The driver does not compute addresses. To reserve one it creates a selectorless
`type: LoadBalancer` placeholder Service in its own namespace, drawing from the class
pool. MetalLB assigns an address to the placeholder and, since it has no endpoints, never
announces it. The driver pins the placeholder to the assigned address and then records the
`IPAddress`. A held address stays assigned in MetalLB's accounting, and MetalLB refuses to
assign it to any other Service. Association is a handoff: record `associatedTo`, delete
the placeholder, pin the workload's Service.

## Why not the alternatives

- **Driver-side first-free allocation** (the first implementation, replaced in
  [`56b3d95`](https://github.com/cozystack/metallb-iad/commit/56b3d95)). A reservation
  MetalLB cannot see is enforced only by identity-keyed admission, and a parallel
  allocator can diverge from the backend's live state. First-free remains valid for a
  backend with no allocation API; MetalLB allocates by Service, so it is not such a
  backend.
- **Gapless handoff by sharing the address between placeholder and workload**
  (`metallb.io/allow-shared-ip`, analysed in
  [`e2e608f`](https://github.com/cozystack/metallb-iad/commit/e2e608f)). Deferred:
  MetalLB's sharing rules require `externalTrafficPolicy: Cluster` and disjoint ports on
  the tenant Service. It remains an optimisation for Services that satisfy both.

## Consequences

- A reserved, unattached address cannot be taken by another Service. Admission and
  conflict detection become defence in depth. Proposal §6 is written around this.
- One placeholder Service per held address, each with a ClusterIP. The number of
  reserved addresses is therefore bounded by the Service CIDR, not by the external pool.
- The handoff is not atomic. Between deleting the placeholder and the first successful
  pin the address is unheld, and because `associatedTo` is already set, a failed pin or a
  driver restart does not re-arm the hold. The admission policy covers that window.
- MetalLB keeps an assignment until the Service is deleted or re-addressed;
  `autoAssign: false` gates only new allocation (observed on a live cluster,
  [design §5](https://github.com/cozystack/metallb-iad/blob/main/docs/design.md#5-association-the-gap-handoff)).
  Withdrawing the pin from a running Service does not take the address away: the Service
  keeps announcing, the recreated placeholder stays `Pending`, and the ledger reports
  `Conflict` naming the Service until it is deleted. Proposal §5 states this.
- The `IPAddress` is created after MetalLB has assigned the address. This is the
  allocate–observe–record flow a cloud driver uses with a provider-side reservation; the
  placeholder plays that object's role. Proposal §3 no longer calls the provisioner the
  IPAM of record.

## Revisit if

MetalLB or Cilium LB-IPAM gains a reservation object that can be held without a Service,
or shared handoff proves safe as the default. A backend with no allocation-by-Service API
would reopen first-free allocation for that driver only.
