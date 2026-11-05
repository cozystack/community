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
does not adopt an existing `IPAddressPool`.
[cozystack/metallb-iad](https://github.com/cozystack/metallb-iad) renders the pool, with
`autoAssign: false`, and an `L2Advertisement` from the class parameters. It supports L2
and CIDR syntax only and lists the rest as a known gap.

MetalLB rejects a pool whose CIDRs overlap another pool's (`internal/config/config.go:335`
in v0.16.1). On a production cluster the class's range had been part of the admin's
auto-assign pool. The admin shrank that pool to one address so the rendered pool could
exist, and plain LoadBalancer Services were left without addresses
([cozystack/address-controller#7](https://github.com/cozystack/address-controller/pull/7)).

MetalLB takes its `loadBalancerClass` from the controller's `--lb-class` flag and records
it on no API object. The CRD had no `loadBalancerClass` field, which review of the core
noted
([address-controller#7 review](https://github.com/cozystack/address-controller/pull/7#pullrequestreview-5782673712)).

## Decision

A class names backend configuration by reference. The MetalLB driver's parameters name an
existing `IPAddressPool` (`pool: <name>`), which the admin owns with its advertisements.
The driver does not render or edit the pool, and refuses a class whose pool is missing,
has `autoAssign: true`, or is named by another class. `IPAddressClass` has no
`loadBalancerClass` field. Each driver deployment fronts one LB implementation instance
and is configured with that instance's value. The driver runs the §5 checks against it,
warns about an auto-claim class it serves when the value is non-empty, and reports it as
`status.loadBalancerClass` on every class it serves.

## Why not the alternatives

- **The class carries the CIDRs and the driver renders the pool** (§2 and §7 as merged,
  metallb-iad as built). Two objects describe one range. Creating a class can then require
  editing the admin's pools, and each pool setting must be mirrored into parameters.
- **`spec.loadBalancerClass` on the class** (§2 and §5 as merged). No component can check
  the field against the flag. When they disagree, the §5 check refuses correct consumers
  and admits wrong ones.
- **The driver derives the value from its parameters**, which §5 allowed. No MetalLB
  object records the value, so the parameters of every class restate the flag.

## Consequences

- The admin configures pools and advertisements in MetalLB's terms. Range syntax,
  `avoidBuggyIPs`, BGP and `serviceAllocation` need no class parameter.
- Deleting a class does not delete its pool. The class-deletion guard stays: the driver
  resolves the pool through the class to hold and pin addresses.
- An admin can shrink or delete the pool under bound addresses, outside the driver's
  control. MetalLB withdraws the assignments and the driver reports the addresses `Lost`.
- The §5 serviceability check includes the pool's `serviceAllocation`, which must admit
  the placeholder namespace and the consumer's namespace.
- `IPAddressClass` gains a `status` written by drivers. Controllers that create a Service
  and name a claim read `status.loadBalancerClass`.
- Existing metallb-iad classes migrate by naming the pool the driver rendered and removing
  that pool's owner reference to the class. The API is `v1alpha1`; there is no conversion.

## Revisit if

MetalLB records its `loadBalancerClass` on an API object the driver can read, which would
let the driver drop the flag; or a backend appears whose pools cannot be named.
