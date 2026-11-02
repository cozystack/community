# 0002. The ledger does not adopt assignments; the core claims on behalf of Services that cannot

- **Number:** `0002`
- **Date:** `2026-10-30`
- **Status:** Accepted
- **Deciders:** `@lllamnyp`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/address-controller#7`](https://github.com/cozystack/address-controller/pull/7)
- **Implemented in:** [`cozystack/address-controller#7`](https://github.com/cozystack/address-controller/pull/7) (open); the held-before-minted rule is not yet implemented

## Context

The proposal's §6 originally had two halves. Holders (record 0001) were one. The other said
*"the inventory still works backwards"*: because an eager allocator assigns before any claim
exists, the controller would also observe `status.loadBalancer` on every Service and
reconcile what it found into `IPAddress` objects after the fact, so that the ledger would
converge on everything in use. It was introduced in the first revision in answer to a
production report that a shared auto-assign pool was the normal deployment
([comment, 2026-07-15](https://github.com/cozystack/community/pull/35#issuecomment-4977850733)
and the [reply](https://github.com/cozystack/community/pull/35#issuecomment-4984507704)).

Neither controller ever implemented it, and when the reason for it was examined it was not
there. With a class's pool at `autoAssign: false`, a plain Service cannot draw a class
address *except* by naming the pool or pinning the address — which is a conflict to
surface, not an allocation to record — and addresses from pools no class manages were never
the ledger's concern. Catch-all adoption of objects nobody asked for is also uncharacteristic
of Kubernetes: the PersistentVolume controller does not adopt volumes it did not provision.

What the implementation *did* find was the opposite problem. The packaging work for tenant
Kubernetes clusters showed that cloud-provider-kubevirt creates a host-side
`type: LoadBalancer` Service for every LoadBalancer Service in a guest, copying the guest's
annotations once at creation and nothing afterwards
([cozystack/cozystack#3382 review](https://github.com/cozystack/cozystack/pull/3382#pullrequestreview-5503155946),
[address-controller#7](https://github.com/cozystack/address-controller/pull/7)). Under
`autoAssign: false` such Services sat `Pending` with *no available IPs*. These are Services
that *should* draw from a class but cannot name a claim, because nothing in their render
path has heard of claims.

## Decision

The ledger records only what was reserved through it; it adopts nothing. In its place the
core offers an **opt-in auto-claim** path, enabled per class by an annotation on the
`IPAddressClass` and inert everywhere otherwise: for a live LoadBalancer Service with no
claim annotation, no `loadBalancerClass`, no pin and no assigned address, the core mints an
`IPAddressClaim` of the same name in the same namespace, **owned by the Service**, and
writes the association annotation onto the Service; the driver then associates as if a
tenant had written it. A Service may name a class, or opt out, through annotations the core
defines — the ones a deputy such as a cloud-controller-manager may safely copy. A claim with
a controller owner follows its owner rather than the class reclaim policy: when the Service
goes, its claim goes and its addresses are deleted even under `Retain`. Removing the owner
reference turns it into an ordinary held claim.

## Why not the alternatives

- **Reconcile every live assignment into the ledger** (the proposal's own §6 text until
  this revision). Lost because there is nothing to adopt once class pools are
  `autoAssign: false`, and because an inventory that adopts foreign assignments would
  record addresses nobody reserved, with no claim to attribute them to. Recorded in the
  proposal's *Alternatives considered*.
- **Require every guest cluster to name a claim.** Lost on the deputy's behaviour: the
  cloud-controller-manager copies annotations at creation only, so a guest would have to
  know about claims before creating its Service, and every Kubernetes-app tenant would
  need to be taught the contract. Auto-claim keeps the guest ignorant and still gives the
  Service a ledgered address.
- **Let auto-claimed addresses follow the class reclaim policy.** Lost because nobody asked
  to keep them: under `Retain` every deleted Service would leave a `Released` address
  behind, which is a leak, not a reservation. The same concern was raised independently
  against machinery-minted claims on community #45
  ([review, 2026-10-12](https://github.com/cozystack/community/pull/45#pullrequestreview-5491027438)),
  which is why the rule is stated for any controller owner, not only Services.

## Consequences

- The core now touches Services on this one path; the proposal's claim that it never does
  is withdrawn.
- Auto-claim is independent of the default class. The two decisions have different
  owners, and a class marked for auto-claim must be deployed after drivers have opted
  their own placeholder Services out
  ([cozystack/metallb-iad#5](https://github.com/cozystack/metallb-iad/pull/5)), or the core
  will claim the driver's holders.
- A deputy that forwards user content no longer needs to be allowlisted by the admission
  fence: its Services reach the ledger through the claim-reference and class-selection
  annotations, which the fence leaves writable.
- The brokered operating mode gains a natural attach surface: with the held-before-minted
  rule (specified, not yet implemented), a plain LoadBalancer Service in a namespace that
  already holds an unattached claim wears that address rather than minting a new one, and
  a second Service with no spare claim stays `Pending`.
- Deputies copy annotations at creation only, so class selection and opt-out must be set on
  the guest Service before it exists; adding them later has no effect.

## Revisit if

cloud-provider-kubevirt gains an annotation filter or a configurable claim contract, which
would let guest clusters name claims directly; or a second deputy appears whose behaviour
the annotation-copy assumption does not fit.
