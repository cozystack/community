# 0002. The ledger does not adopt assignments; the core claims on behalf of Services that cannot

- **Number:** `0002`
- **Date:** `2026-10-30`
- **Status:** Accepted
- **Deciders:** `@lllamnyp`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/address-controller#7`](https://github.com/cozystack/address-controller/pull/7)
- **Implemented in:** [`cozystack/address-controller#7`](https://github.com/cozystack/address-controller/pull/7) (open); the held-before-minted rule is not yet implemented

## Context

The first revision of §6 had the controller create an `IPAddress` for every assigned
address it found on a LoadBalancer Service, to inventory addresses MetalLB assigned before
a claim existed. It was added in response to a production report that a shared
auto-assign pool was the normal deployment
([comment, 2026-07-15](https://github.com/cozystack/community/pull/35#issuecomment-4977850733),
[reply](https://github.com/cozystack/community/pull/35#issuecomment-4984507704)).
Placeholder holding, the other half of §6, is record 0001.

Neither controller implemented it, and nothing needs it. A class pool is
`autoAssign: false`, so a plain Service gets a class address only by naming the pool or
pinning the address, which §8 already reports as a conflict. Other pools are outside the
ledger's scope. Adoption of objects nobody asked for is also uncharacteristic of
Kubernetes; the PersistentVolume controller does not adopt volumes it did not provision.

The implementation found the opposite problem. cloud-provider-kubevirt creates a host-side
`type: LoadBalancer` Service for every LoadBalancer Service in a tenant Kubernetes cluster
and copies the guest's annotations once, at creation
([cozystack/cozystack#3382 review](https://github.com/cozystack/cozystack/pull/3382#pullrequestreview-5503155946),
[address-controller#7](https://github.com/cozystack/address-controller/pull/7)). Under
`autoAssign: false` these Services stayed `Pending` with "no available IPs". They should
draw from a class and cannot name a claim, because nothing in their render path knows
about claims.

## Decision

The ledger records only addresses reserved through it. The core has an opt-in auto-claim
path, enabled per class by an annotation on the `IPAddressClass`: for a live LoadBalancer
Service with no claim annotation, no `loadBalancerClass`, no pin and no assigned address,
the core creates an `IPAddressClaim` with the Service's name in the Service's namespace,
owned by the Service, and writes the association annotation on the Service. The driver
then associates as usual. A Service can name a class or opt out through annotations the
core defines, which cloud-provider-kubevirt copies from the guest Service. A claim with a
controller owner follows its owner, not the class reclaim policy: when the Service is
deleted, its claim and addresses are deleted even under `Retain`. Removing the owner
reference makes it an ordinary held claim.

## Why not the alternatives

- **Create an `IPAddress` for every live assignment** (the proposal's own §6 until this
  revision). There is nothing to adopt under `autoAssign: false`, and an adopted address
  has no claim to attribute it to. Recorded in *Alternatives considered*.
- **Require every guest cluster to name a claim.** The cloud-controller-manager copies
  annotations at creation only, so every guest would need to know the contract before
  creating a Service. Auto-claim gives the Service a ledgered address without that.
- **Let auto-claimed addresses follow the class reclaim policy.** Under `Retain` every
  deleted Service would leave a `Released` address. The same objection was raised against
  machinery-minted claims on community #45
  ([review, 2026-10-12](https://github.com/cozystack/community/pull/45#pullrequestreview-5491027438)),
  so the rule applies to any controller owner.

## Consequences

- The core writes to Services on this path. The proposal's statement that it never does
  is withdrawn.
- The auto-claim class is independent of the default class. Mark a class only after
  drivers have opted their placeholder Services out
  ([cozystack/metallb-iad#5](https://github.com/cozystack/metallb-iad/pull/5)); otherwise
  the core claims the driver's holders.
- A deputy that forwards user content does not need to be allowlisted by the admission
  policy. Its Services reach the ledger through the claim-reference and class-selection
  annotations, which the policy leaves writable.
- With the held-before-minted rule (specified, not implemented), a plain LoadBalancer
  Service in a namespace that holds an unattached claim wears that address, and a second
  Service with no spare claim stays `Pending`. This is the attach surface of the brokered
  mode.
- cloud-provider-kubevirt copies annotations at creation only, so class selection and
  opt-out must be on the guest Service before it is created. Syncing annotations on update
  in cloud-provider-kubevirt would remove this constraint.

## Revisit if

cloud-provider-kubevirt gains an annotation filter or a configurable claim contract, so
guest clusters can name claims directly; or a second deputy appears whose behaviour the
annotation-copy assumption does not fit.
