# 0003. The class references the backend's configuration and does not restate it

- **Number:** `0003`
- **Date:** `2026-11-05`
- **Status:** Accepted
- **Deciders:** `@lllamnyp`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#124`](https://github.com/cozystack/community/pull/124)
- **Implemented in:** not yet

## Context

As merged, §2 gave `IPAddressClass` a `spec.loadBalancerClass` and, for the MetalLB
driver, `parameters.addresses`: the CIDRs of a pool the driver renders. §7 said the driver
does not adopt an existing `IPAddressPool` and that a range moved into a class must leave
its old pool first. [cozystack/metallb-iad](https://github.com/cozystack/metallb-iad)
implements this: it renders a pool with `autoAssign: false`, and an `L2Advertisement`,
from the class parameters.

Both fields restate configuration that the backend already holds:

- **The range.** MetalLB rejects a pool whose CIDRs overlap another pool's
  (`internal/config/config.go:335` in v0.16.1). On a production cluster the class's range
  had been part of the admin's auto-assign pool. That pool was hand-shrunk to one address
  so the rendered pool could exist, which left every plain LoadBalancer Service without
  an address
  ([cozystack/address-controller#7](https://github.com/cozystack/address-controller/pull/7)).
  Anything else MetalLB can say about a pool has to be mirrored into parameters before a
  class can use it, so the driver lists "L2 only, CIDRs only" as a known gap.
- **The implementation class.** MetalLB takes its `loadBalancerClass` from the
  controller's `--lb-class` flag and records it on no object. A field on the class is a
  second copy that no component can check against the backend. Review of the core noted
  that the field is absent from the CRD
  ([address-controller#7 review](https://github.com/cozystack/address-controller/pull/7#pullrequestreview-5782673712)).

## Decision

A class names backend configuration by reference. The MetalLB driver's parameters name an
existing `IPAddressPool` (`pool: <name>`), which the admin owns with its advertisements.
The driver does not render or edit the pool. It refuses a class whose pool is missing,
has `autoAssign: true`, or is named by another class. `IPAddressClass` has no
`loadBalancerClass` field. A driver deployment fronts one LB implementation instance and
is configured with that instance's value. The driver runs the §5 checks against it, warns
about an auto-claim class it serves when the value is non-empty, and reports it as
`status.loadBalancerClass` on every class it serves.

## Why not the alternatives

- **The class carries the CIDRs and the driver renders the pool.** This is §2 and §7 as
  merged, and metallb-iad as built. Two objects describe one range, so creating a class
  can require editing the admin's pools, and every pool feature has to be mirrored into
  parameters.
- **`spec.loadBalancerClass` on the class.** This is §2 and §5 as merged. When the field
  disagrees with the flag, the §5 check refuses correct consumers and admits wrong ones,
  and nothing reports the disagreement.
- **The driver derives the value from its parameters**, which §5 allowed. No MetalLB
  object records the value, so parameters would restate the flag on every class, which is
  the same problem once per class.

## Consequences

- The admin configures MetalLB in MetalLB's terms. Range syntax, `avoidBuggyIPs`, BGP and
  `serviceAllocation` stop being driver gaps.
- Deleting a class no longer garbage-collects its pool. The class-deletion guard stays,
  because the driver resolves the pool through the class to hold and pin its addresses.
- An admin can shrink or delete a pool under bound addresses. MetalLB withdraws the
  affected assignments and the driver reports them `Lost`. This now happens outside the
  driver's control.
- The pool's `serviceAllocation` must admit the placeholder namespace and the consumer's
  namespace. It becomes part of the §5 serviceability check.
- `IPAddressClass` gains a `status` that drivers write. Controllers that create a Service
  and name a claim read `status.loadBalancerClass` instead of a spec field.
- Existing metallb-iad classes migrate by naming the pool the driver rendered and removing
  that pool's owner reference to the class. The API is `v1alpha1`; there is no conversion.

## Revisit if

MetalLB records its `loadBalancerClass` on an API object the driver can read, which would
let the driver drop the flag; or a backend appears whose pools cannot be named.
