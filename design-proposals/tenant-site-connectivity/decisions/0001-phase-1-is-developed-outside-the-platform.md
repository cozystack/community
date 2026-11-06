# 0001. Phase 1 is developed outside the Cozystack repositories, as an Aenix operator and catalog app

- **Number:** `0001`
- **Date:** `2026-10-12`
- **Status:** Accepted
- **Deciders:** `@myasnikovdaniil`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** the review of [`cozystack/cozystack#3426`](https://github.com/cozystack/cozystack/pull/3426), where the app was reworked ([comment](https://github.com/cozystack/cozystack/pull/3426#issuecomment-6013471345)). The two grounds below were not argued there.
- **Implemented in:** [`aenix-io/site-router-operator`](https://github.com/aenix-io/site-router-operator) and [`aenix-io/cozyapps`](https://github.com/aenix-io/cozyapps), first release [`free/v0.1.0`](https://github.com/aenix-io/cozyapps/tree/free/v0.1.0)

## Context

The proposal was accepted on 2026-07-21 ([`cozystack/community#30`](https://github.com/cozystack/community/pull/30)). It put two apps, `site-router` and `site-gateway`, in the platform on a shared foundation. Phase 1, the `site-router` app, was opened as [`cozystack/cozystack#3426`](https://github.com/cozystack/cozystack/pull/3426). The review asked for changes, and the app was reworked as a standalone operator with its own CRD, tied to no platform, no CNI and no appliance in particular. Two things stood in the way of the app staying in the platform: licensing concerns about the gateway appliance, an image built from VyOS sources, and the set of applications of the platform not being extended for now.

## Decision

Phase 1 is developed as an Aenix project outside the Cozystack repositories. The gateway is an operator, [`aenix-io/site-router-operator`](https://github.com/aenix-io/site-router-operator), on an appliance image built in [`aenix-io/barerouter`](https://github.com/aenix-io/barerouter). The app with its connector for kube-ovn is in the catalog [`aenix-io/cozyapps`](https://github.com/aenix-io/cozyapps) (`free/site-router`), which a cluster connects with `cozypkg tap`. The app pull request in the monorepo was closed. `site-gateway`, the second app of the proposal, was not made.

## Why not the alternatives

- **Keep the app in the platform, as the proposal and [`cozystack/cozystack#3426`](https://github.com/cozystack/cozystack/pull/3426) had it.** It lost on two grounds given by the author: licensing concerns about the VyOS based appliance image, and the set of platform applications not being extended for now. Neither is a hard fact about the design, both are judgement calls, so the decision is only as firm as they are. They were not argued in that review, and this record is where they are written down.

## Consequences

- A cluster gets the gateway from a catalog and not with the platform. The operator, the app and the appliance have their own releases and versions, and the tests and the docs of the platform do not cover them.
- The routed gateway of the proposal is what the operator builds: a KubeVirt VM in the tenant namespace that keeps the source addresses of the remote networks. That part of the proposal still describes what it does. The NAT variant, `site-gateway`, is not planned.
- The platform repositories carry no code of the app. The docs of the e2e selector in `cozystack/cozystack` still name [`cozystack/cozystack#3426`](https://github.com/cozystack/cozystack/pull/3426) as an example of a selector bug. That is history and it was left.

## Revisit if

The licensing of the appliance is settled for the platform, or the platform starts extending its set of applications. Bringing the app back into the platform is a question again then.

---

<!--
Format follows Michael Nygard's architecture decision records
(https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
and MADR (https://adr.github.io/madr/).

Once merged, the header block above is maintained (Status, Superseded by,
Implemented in must track reality) and the prose below it is frozen.
-->
