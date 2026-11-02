# IP addresses as a first-class resource: `IPAddress`, `IPAddressClaim`, `IPAddressClass`

- **Title:** `IP addresses as a first-class resource: IPAddress, IPAddressClaim, IPAddressClass, and a provisioner contract`
- **Author(s):** `@lllamnyp`

> **The shape is settled and implemented; the contract details are not.** The problem
> statement, the primitives survey, and the object model are worked out, and phase 1 of
> *Rollout* exists in code (see *Implementation status*, which also lists where the
> document runs ahead of the code). Deliberately still open: the provisioner contract's
> own API (capabilities are not modelled yet), the entitlement object of *Security*, the
> sequencing beyond phase 1, and the open questions below.

## Overview

A tenant cannot *own* an address. They can only cause one to appear as a side effect
of creating a `Service type: LoadBalancer`, and it evaporates when that Service does.
There is no object to hold, keep, quota, hand to another workload, or point DNS
at with confidence.

This proposal makes the address a resource. Applying the `PersistentVolume` pattern
to addresses:

| storage | addresses | role |
|---|---|---|
| `StorageClass` | **`IPAddressClass`** | which pool, which provisioner, which announcer |
| `PersistentVolume` | **`IPAddress`** (cluster-scoped) | the address itself, with a `claimRef` and a reclaim policy |
| `PersistentVolumeClaim` | **`IPAddressClaim`** (namespaced) | "give me one" — what a tenant creates |
| CSI driver | **a provisioner contract** | how a class's backend allocates, adopts, and pins |

The driving use case is the **public** address — the AWS Elastic IP experience:
**allocate an address, keep it, attach it to something, detach it, attach it to
something else, release it when done.** Not: "create a Service and hope you get the
same IP back." But the resource is not public-specific. A class names a pool, and a
platform can hand out more than one kind of pool — public ranges, a private internal
LB range, a provider's static addresses. So the kinds are named for the general thing
(`IPAddress`), and "public" is a property of the class's pool, not of the API.

### Naming

The kinds `IPAddress` and `IPAddressClaim` are deliberate echoes of an existing
pattern, and the overlap is worth naming up front so reviewers are not surprised:

- **Kubernetes core** ships an `IPAddress` kind in `networking.k8s.io` (KEP-1880). It
  is *not* this: it is a low-level record where the object name **is** the IP, with a
  generic `parentRef`, and its authors named "any generalization onto something like
  an IPAM API" an explicit **non-goal** (see Context). Ours lives in a different API
  group, carries a reclaim policy, a claim binding, and a class, and is the IPAM
  object core declined to build.
- **Cluster API** ships `IPAddressClaim`/`IPAddress` in `ipam.cluster.x-k8s.io` with a
  documented third-party provider contract — the same claim-plus-concrete-address
  shape, but only ever wired to **Machine** addressing, never Services, and with no
  reclaim policy or association. We mint our own (*Alternatives considered*); the shared
  names are an acknowledgement that the shape is not novel.
- **`Service.spec.externalIPs`** is the older, unrelated mechanism — a Service claiming an
  address by value with no allocator behind it, the field behind CVE-2020-8554 — and
  `ExternalIP*` kinds, proposed in review, would invite confusion with it. One more reason
  the kinds are not named that way.

Kinds are qualified by API group, so the short-name overlap is not a collision. The
kinds live in `local.sdn.cozystack.io` (see *Positioning*).

## Scope and related proposals

- **Adjacent, not a dependency:** `design-proposals/structured-external-exposure`
  (community #29) restructures how a managed application *requests* exposure. This
  proposal is about what an address **is**. The two meet at one point — an exposure
  entry should be able to name an `IPAddressClaim` instead of implicitly minting an
  address — but neither needs the other to land first, and this proposal does not
  assume #29's shape.
- **Related:** `Service.spec.loadBalancerClass`, the upstream field selecting which LB
  implementation serves a Service. An `IPAddressClass` names a provisioner and a pool,
  which determines the implementation that will announce the address. An `IPAddressClass`
  therefore **determines** the `loadBalancerClass` a consuming Service must carry (§5). (An
  `ExposureClass` kind was proposed alongside community #29 and never shipped; there is no
  `network.cozystack.io` group. That direction went to `loadBalancerClass` instead —
  cozystack/cozystack#3164, cozystack/cozystack#3218.)
- **Depended on by:** `design-proposals/endpoint-attachments` (community #45), which
  attaches one endpoint of an application to an external address. It consumes an
  `IPAddressClaim` and the association annotation of §5, and its `EndpointAttachment`
  kind lives in **this same `local.sdn.cozystack.io` family** — the exposure half beside
  the allocation half, another CRD-served resident relying on the `local.` prefix's
  quota guarantee (*Positioning*). This proposal does not wait on it and lands
  independently.
- **Deferred to a sibling:** the *datapath* for whole-IP 1:1 NAT (all ports in, and
  the workload egressing **as** that address) — today provided by
  [cozy-proxy](https://github.com/cozystack/cozy-proxy)'s nftables rules. That is a
  CNI concern, tracked separately. **This proposal is the allocation half; that is
  the forwarding half.** They are independent: allocation is useful with a stock
  LoadBalancer Service and no 1:1 NAT at all.

## Decisions

Implementation ran ahead of this document, and two of its findings changed the design
rather than merely filling it in. Each is recorded where the reasoning can be found again:

- [0002. The ledger does not adopt assignments; the core claims on behalf of Services that cannot](./decisions/0002-the-ledger-does-not-adopt-the-core-claims-on-behalf-of-services.md) — why §6's backwards inventory was dropped and the opt-in auto-claim took its place.
- [0001. The backend allocates; the ledger records what it assigned](./decisions/0001-the-backend-allocates-and-the-ledger-records.md) — why the reference driver reserves through placeholder Services instead of carving addresses itself, and what that costs.

## Positioning

**This should stand on its own, and also ship as part of the Cozystack API surface —
both, not either.** A controller that reserves an address, binds it to a workload
through whatever pin mechanism the backend exposes, and gates that binding behind an
admission policy is useful to any Kubernetes platform running MetalLB, Cilium LB-IPAM,
or a cloud LB — not only Cozystack. So it is designed to be **valuable standalone**: the
tenant contract is a plain namespaced CRD, the quota is a stock `ResourceQuota`, the
enforcement is a `ValidatingAdmissionPolicy` — all vanilla Kubernetes, with no
Cozystack-only machinery required to run it.

Standalone value does **not** mean it lives outside the Cozystack API group. Cozystack
already ships components that are fully standalone products yet live under
`*.cozystack.io` — `etcd-operator.cozystack.io` is the precedent. This belongs there the
same way, and the component is deliberately shaped so Cozystack integrates it cleanly (a
default class, and an exposure path able to name a claim). Standalone-valuable and
in-the-Cozystack-group are not in tension, and designing for easy Cozystack integration
is a goal, not a compromise.

It also belongs in the **networking** group rather than a new one of its own. Cozystack
networking is `sdn.cozystack.io` today: `SecurityGroup` lives there, served by the
Cozystack API as a tenant-facing interface over Cilium network policies. Cozyplane serves
its own `SecurityGroup` in that same group, plus `local.sdn.cozystack.io` for the kinds it
serves as CRDs. Addresses are the third networking feature to want a home, and the
argument here is only that networking APIs should not accumulate several different group
names: **one `sdn` family**, with the `local.` prefix marking CRD-served kinds and the
bare group the aggregated ones. So the kinds live in **`local.sdn.cozystack.io`**.
`ipam.cozystack.io` is not wrong on its own terms — it adds a fourth name to a subject
area that should have one.

To be clear that this is a proposal and not an appeal to precedent:
`local.sdn.cozystack.io` is new, nothing serves it today, and this would be among the
first components to introduce it. Nor does the group
string create a Cozystack dependency for a standalone user — the same three CRDs install
under any name.

The obvious objection is that `ipam.` describes the object better than `sdn.` does: an
address ledger is not a datapath concern, and its consumers include Gateways and cloud
providers that are not SDN either. That is a fair reading, and the reason it does not win
here is that this is **not all of IPAM and cannot become it** — allocation stays with the
allocator, since AWS and MetalLB both keep it — so an `ipam.cozystack.io` group would
promise a generality the component deliberately does not have. If upstream moves and that
generality becomes real, it is a v2 question. Until then the kinds sit with the rest of
networking.

The kinds are minted rather than adopted from Cluster API's IPAM; *Alternatives
considered* says why.

## Implementation status

Phase 1 of *Rollout* exists, under the Cozystack organisation:

- **[address-controller](https://github.com/cozystack/address-controller)** — the
  class-agnostic core: the three kinds, claim–address binding, status, reclaim, and the
  contract per-class drivers plug into. It touches a Service only on the opt-in
  auto-claim path of §6 (cozystack/address-controller#7).
- **[metallb-iad](https://github.com/cozystack/metallb-iad)** — the reference per-class
  driver, for addresses announced by MetalLB. A reservation is held as a placeholder
  Service in a driver-owned namespace, so MetalLB's own accounting refuses to
  double-assign a held address. cozystack/metallb-iad#6 ships the admission policy of
  *Security* as an opt-in chart template; cozystack/metallb-iad#5 opts the placeholders
  out of auto-claim.
- **cozystack/cozystack#3382** packages both as optional system packages, with tenant
  RBAC on claims and a distribution-specific admission policy. Which policy a deployment
  runs is its own business: the driver's generic one is the reference, and a distribution
  that needs a different shape disables it and ships its own.

One backend proves little on its own, so the load-bearing evidence is a **second
consumer**. [Cozyplane](https://github.com/lllamnyp/cozyplane) wears reserved addresses
through this contract with **no module import, no CRD dependency, and no informer on the
claim kinds**: it copies a claim name into the association annotation (§5) on a Service it
already owns, and the driver does the rest. With the mechanism absent entirely it behaves
identically, auto-assigning instead — reserved and dynamic are one code path. Validated
end to end on a three-node cluster running address-controller + metallb-iad + MetalLB +
cozyplane: an address survived delete-and-rebind and stayed externally reachable
throughout.

Note precisely which seam that tests. *Rollout* names a second **provisioner** as the
*allocation* seam, and it remains outstanding. What is demonstrated is the *association*
seam: that a backend with an entirely different datapath can wear a reserved address while
knowing nothing about the ledger beyond one annotation. The coupling turned out looser
than this document assumed it would need to be.

**Where the document runs ahead of the code.** This is a design proposal; the following
are specified here and not yet implemented, so that nobody mistakes the text for a
description of the binaries: the class-determined `loadBalancerClass` check of §5; a
driver pinning only the address families its Service carries (§2); conditions on
`IPAddress` (§2) and the `Associated` condition on a claim (§5); per-class scoping of
conflict adjudication (§8); the held-before-minted rule of §6; the release delay and the
class-deletion guard of *Failure and edge cases*; Gateway as an association target (§5);
the entitlement object of *Security*. Where the code carries detail the text does not —
`claimRef.uid`, a sticky `status.className` on the claim, claim conditions and a `Lost`
claim phase, a `Pending` address phase, the provisioner stamp annotation, the finalizers —
the implementation's own design document is the reference; none of it changes the model.

## Context

### What exists today

Cozystack installs MetalLB but renders no `IPAddressPool`/`L2Advertisement` itself;
the admin configures an allocator to suit the environment (MetalLB L2/BGP, Cilium
LB-IPAM, a cloud LB, or `Service.spec.externalIPs` pinning).

Across every one of those, the model is the same: **an address is allocated to a
Service, at the moment the Service is created, and freed when it is deleted.**

### The problem

> *"I gave my customer this IP address. It is in their firewall allow-list. I need to
> rebuild the VM behind it, and I need the address to still be mine afterwards."*
>
> *"I want to move this address from the old VM to the new one during a cutover."*
>
> *"I want to reserve four addresses now, because the range is nearly full, and attach
> them over the next month."*

None of these are expressible. The address has no independent existence, so:

- **It cannot be held.** Delete the Service, lose the address. Someone else may get it.
- **It cannot be moved.** There is no detach/attach; there is only "hope the allocator
  hands you the same one."
- **It cannot be quota'd.** There is no object to count, so nothing bounds how many
  addresses a tenant consumes. The only lever is pre-carving a pool per tenant.
- **It cannot be gated.** Anyone who can create a `Service` in a namespace can cause an
  address to be allocated. RBAC authorizes *verbs on resources*, not *fields*.
- **It cannot be shown.** No inventory. "Which addresses do we own, and who has them?"
  has no answer short of listing every Service in the cluster.

This is a **new capability**, argued on its own merits: hold, move, quota, enumerate.
Cozystack has no prior reserve-then-associate mechanism to preserve — the platform uses
kube-ovn only for subnets and VPCs, not for its EIP objects — so there is nothing here
being restored, only something being added.

## Goals

- A tenant can request an address and receive one — or, in a brokered deployment, have
  one requested on their behalf and attach it — without an admin pre-creating a pool
  object for them.
- An address **survives** the deletion of the workload it was attached to (reclaim
  policy `Retain`), and can be attached to a different workload afterwards.
- An operator can enumerate every address the cluster owns, and see who holds it.
- The number of addresses a tenant may hold is bounded by a **plain `ResourceQuota`**
  (`count/ipaddressclaims.<group>`), with no new quota machinery.
- An admin configures the address source **once per class**, not once per tenant and
  not once per address. No pool-per-tenant, no `/32` pools.
- The tenant-facing API does not name the backend. A tenant asks for an address; it
  does not learn whether MetalLB, Cilium, or a cloud API produced it.
- The design admits at least: MetalLB, Cilium LB-IPAM, and a cloud provider whose
  addresses are allocated outside the cluster.
- A reserved address is never *silently* lost to a plain `Service type: LoadBalancer`:
  reservations are reconciled against live Service assignments, so a collision is a
  detected, surfaced state rather than a quiet theft (see *Design §8* and *Security*).

### Non-goals

- **Announcing addresses.** Attraction (ARP/NDP, BGP, a cloud VNIC assignment) stays
  with the LB implementation. This proposal allocates and binds; it never puts a
  packet on a wire.
- **The 1:1 NAT datapath.** See *Scope*.
- **Several owned addresses on one consumer.** One rule is settled and enforced: a claim
  is consumed by **one consumer at a time**, so two consumers cannot quietly share a
  reservation. The converse is *not* settled, and this proposal does not pretend it is —
  how many claims one consumer may hold is open. No limit on a *workload* is claimed:
  nothing stops an application owning several Services, each with its own claim, and a
  managed service reachable on a public address and an internal routable one at once is
  exactly that — two Services, two claims, no new concept. But a single consumer wearing
  several addresses is a real case rather than a hypothetical one, and Gateway API states
  it outright: `Gateway.spec.addresses` is a **list**, so one Gateway legitimately wears
  several addresses, and nothing in the model below says how several claims attach to one
  consumer or how a provisioner renders that. **Acknowledged and deferred to future
  work.** Two neighbouring cases stay out with it: *N* owned addresses on a workload whose
  extra addresses live on a **tunnel interface the platform neither owns nor sees** (a VM
  acting as a router or VPN concentrator), which is orthogonal to reservation; and the
  platform-owned variant of it, which would need an internal address and in-guest
  configuration per address. Dual-stack (one v4 + one v6) is a separate, narrower
  question, treated below rather than folded into this.
- **Replacing the LB implementation.** MetalLB/Cilium/cloud stay exactly where they
  are; this sits above them.
- **IPv4-only thinking.** The model is family-agnostic; a claim may request v4, v6, or
  both; a `Dual` claim binds one `IPAddress` per family (§2).
- **Solving field-level authorization in general.** It is *used* here (see *Security*)
  but the general problem is bigger than this proposal.

## Design

### 1. The primitives that already exist (survey)

Load-bearing findings, because the design is shaped by them:

1. **Nothing has a standing reservation.** Not MetalLB, not Cilium LB-IPAM, not
   kube-vip, not PureLB, not OpenELB — and not Kubernetes core. KEP-1880 shipped
   cluster-scoped `ServiceCIDR`/`IPAddress` kinds (the name *is* the IP, with a
   generic `spec.parentRef`), and then explicitly named *"any generalization onto
   something like an IPAM API"* a **non-goal**. Nothing upstream is coming to provide
   this. It is ours to build.

2. **Every backend has a "pin this exact address" hook, and it is always an
   annotation** — because `Service.spec.loadBalancerIP` was deprecated in Kubernetes
   1.24 with *no core replacement*, so every implementation invented its own:

   | backend | pin mechanism |
   |---|---|
   | MetalLB | `metallb.io/loadBalancerIPs` |
   | Cilium LB-IPAM | `lbipam.cilium.io/ips` |
   | kube-vip | `kube-vip.io/loadbalancerIPs` |
   | OpenELB | `eip.openelb.kubesphere.io/v1alpha1` |
   | AWS LB Controller | `service.beta.kubernetes.io/aws-load-balancer-eip-allocations` |
   | GCP / GKE | `networking.gke.io/load-balancer-ip-addresses` |
   | PureLB | **none found** — see *Open questions* |

   Different spelling, identical semantics. **This is the seam the whole design rests
   on:** if we can pin, we can bind a reserved address to a workload.

3. **A reserved-but-unattached address is inert, everywhere.** MetalLB never announces
   an address that is not the live `status.loadBalancer.ingress` of a Service it
   allocated. Cilium LB-IPAM only *assigns*; something else announces. A cloud EIP
   routes nowhere until associated. This is not an obstacle — **it is correct
   semantics**, and it is exactly why AWS bills for an idle EIP.

4. **One pool can serve many tenants.** MetalLB's
   `IPAddressPool.spec.serviceAllocation.{namespaces,namespaceSelectors,serviceSelectors}`
   and Cilium's `CiliumLoadBalancerIPPool.spec.serviceSelector` both scope a *single*
   pool by namespace or label. And `L2Advertisement`/`BGPAdvertisement` select
   **pools**, not addresses — so one advertisement covers arbitrarily many addresses.
   **The pool-per-tenant and `/32`-pool patterns were never necessary.**

5. **`Service.spec.loadBalancerClass`** (GA in 1.24) is the standard way for multiple
   LB implementations to coexist; MetalLB honours it via `--lb-class`. It is how a
   cluster can run more than one backend without them fighting.

### 2. The object model

```yaml
# Admin, once per address source.
apiVersion: local.sdn.cozystack.io/v1alpha1
kind: IPAddressClass
metadata:
  name: public
  annotations:
    ipaddressclass.local.sdn.cozystack.io/is-default-class: "true"
spec:
  provisioner: metallb.drivers.local.sdn.cozystack.io  # who fulfils claims of this class
  loadBalancerClass: metallb.io/public   # the implementation that announces it;
                                         # empty => the cluster's default implementation
  reclaimPolicy: Retain                  # Retain | Delete   (default Retain)
  # releaseDelay: 720h                   # optional, Retain only: Released -> Available
  #                                      # after this long (Failure and edge cases)
  parameters:                            # opaque to the core controller
    addresses: ["203.0.113.0/24"]
---
# Tenant. This is the whole tenant-facing API.
apiVersion: local.sdn.cozystack.io/v1alpha1
kind: IPAddressClaim
metadata: {name: web, namespace: tenant-a}
spec:
  className: public          # empty => the default class
  family: IPv4               # IPv4 | IPv6 | Dual
  # addressName: ip-203-0-113-7   # optional: bind this specific address (see below)
status:
  phase: Bound
  addresses:                 # a list, so a Dual claim can report v4 + v6
    - name: ip-203-0-113-7   # the IPAddress object
      address: 203.0.113.7   # what the tenant reads, and puts in DNS
---
# Cluster-scoped. The inventory. Created by the provisioner, not the tenant.
apiVersion: local.sdn.cozystack.io/v1alpha1
kind: IPAddress
metadata: {name: ip-203-0-113-7}
spec:
  className: public
  address: 203.0.113.7
  reclaimPolicy: Retain
  claimRef: {namespace: tenant-a, name: web}
  source:                              # a union — see below
    fromClass: {}                      # this controller allocated it
    # providerRef: {id: eipalloc-0a1b}  # ...or it wraps a provider-side reservation
status:
  phase: Bound
  conditions:                          # metav1.Condition; lastTransitionTime is the
    - type: Bound                      # audit trail of who held the address, and when
      status: "True"
      lastTransitionTime: "2026-07-15T09:12:00Z"
    - type: Associated
      status: "True"
      lastTransitionTime: "2026-07-15T09:15:31Z"
  associatedTo:                        # nil => reserved but inert
    kind: Service
    namespace: tenant-a
    name: web-lb
```

**`spec.addressName` — asking for a *specific* address.** The
`PersistentVolumeClaim.spec.volumeName` analogue: a claim may name one `IPAddress` rather
than take whatever its class yields. It is meaningful only for a single-family claim, and
the controller honours it only if every ordinary condition already holds — the address is
`Available`, carries no `claimRef`, is not being deleted, and its class and family match
the ones the claim resolved. A name that matches nothing eligible is not an error: the
claim stays `Pending` and keeps waiting, exactly as it would with no `addressName` at all.
The field therefore **narrows a candidate set and never widens one**. It cannot pull an
address out of another class, take one away from another claim, or reclaim a `Released`
address before an admin has cleared its `claimRef`.

What it deliberately does *not* do is authorize. `IPAddress` is cluster-scoped, so a claim
naming one names an object outside its own namespace. Neither this field nor
`spec.className` — equally free-text, and equally unable to be gated by namespace RBAC —
decides which classes a namespace may draw from. That gap is identical with or without
`addressName`, and closing it belongs above this controller; see *Security*.

This is precisely how Kubernetes treats `volumeName`: any claim may name any `Available`
volume of its class, nothing authorizes the name, and the admin's lever for a volume meant
for one tenant is to **pre-bind** it by setting its `claimRef` first. Pre-provisioned
addresses work the same way — an admin who wants an address to go to one namespace binds
it before anyone can name it. Where a deployment wants the field gated rather than merely
narrowed, *Security* says how.

`IPAddressClaim.status.addresses` is a **list** deliberately: a `Dual` claim binds a
v4 and a v6 `IPAddress` and must report both, and a scalar `address` field could not.
For a single-family claim the list has one entry. `Dual` is one claim binding two objects
— the implementation settled it that way, and the reason is the association contract: a
dual-stack Service names *one* claim in §5, so one claim must be able to yield both
families. The driver pins only the families the Service carries (*Failure and edge cases*).

`IPAddress.status.conditions` exist so the ledger answers *when* as well as *who*. `Bound`
and `Associated` follow `metav1.Condition` semantics, and their `lastTransitionTime` is the
record of which claim held an address, and which consumer wore it, over time. An operator
reconstructing that after the fact — an abuse report naming an address and an hour — reads
it from the ledger, not from Service events, which expire. `Conflict` is reported the same
way, as a condition beside the phase, so that the offending consumer is named somewhere
durable.

### 3. The address-source union — the reason this is not a MetalLB adapter

There are two structurally different worlds, and the model must admit both:

- **Self-allocating backends** (MetalLB, Cilium, kube-vip, OpenELB): *nothing anywhere*
  holds a reservation. **The ledger is the record of reservation**, and the address is
  carved from the class's range (`source.fromClass`). Which component picks the address
  is the driver's business, and the reference driver deliberately lets the backend do it:
  it asks MetalLB for an address through the holder of §6 and records what MetalLB
  assigned, so the backend's allocator and the ledger can never disagree about who holds
  what. A driver-side allocator was tried first and replaced for exactly that reason.
- **Provider-reservation backends** (AWS, GCP, Hetzner): the reservation is already a
  real object with a stable handle (`eipalloc-…`, a named static address), guarded by
  the provider's own IAM. **Our provisioner must adopt, not allocate**
  (`source.providerRef`).

This is precisely the `PersistentVolume` volume-source union (`spec.csi` /
`spec.nfs` / …), and it is the strongest evidence the shape is not bent around any one
backend: it survives a backend where allocation is not ours at all. The tenant's
`IPAddressClaim` never learns the difference.

### 4. The provisioner contract (the "CSI analogue")

`IPAddressClass.spec.provisioner` names a controller, exactly as `StorageClass` names a
CSI driver. Each provisioner declares **capabilities**, and the core controller refuses
what a backend cannot do rather than half-supporting it:

| capability | meaning | consequence if absent |
|---|---|---|
| `Allocate` | can carve an address from a class range | class must use `providerRef` adoption |
| `Adopt` | can wrap a provider-side reservation | `providerRef` claims rejected |
| **`Pin`** | **can bind a specific address to a Service** | **the class cannot serve claims at all** |

`Pin` is not optional decoration — **it is the capability the entire model depends on.**
A backend that cannot be told *which* address to use can never attach a reserved one,
and a class over such a backend must reject `IPAddressClaim`s outright instead of
allocating an address it can never bind. (PureLB may be exactly this case; see *Open
questions*.)

Whether provisioners are in-tree (a switch in one controller) or out-of-tree (separate
deployments, a real CRD contract, as CSI did) is an **open question**. In-tree is
right for three backends; the CSI lesson is that it stops being right at about five.

### 5. Association

Association is a **separate, reversible act** — that is the entire point, and the thing
the current model cannot express.

A workload references the **claim**, never the raw address:

```yaml
kind: Service
metadata:
  annotations:
    local.sdn.cozystack.io/ip-address-claim: web    # a claim in THIS namespace
spec: {type: LoadBalancer, ...}
```

The controller resolves the claim → address, verifies the claim is in the Service's own
namespace, and writes the backend's pin annotation (§1.2). MetalLB/Cilium/the cloud then
allocates and announces exactly that address.

**The class determines the `loadBalancerClass`.** An `IPAddressClass` names the pool and
the provisioner that carves it, which determines the LB implementation that will announce
anything drawn from it, so a class unambiguously implies one `loadBalancerClass` value —
empty meaning the cluster's default implementation (§1.5) — and a consuming Service must
carry that value. This is not a preference: a Service whose `loadBalancerClass` selects one
implementation while its claim's address is held by another is asking two backends for one
address, and both will be wrong about who owns it. The provisioner's own holder Services
(§6) carry the same value for the same reason. The simplest way to express the mapping is
an explicit `spec.loadBalancerClass` on the class, as in §2; a driver that can derive it
from its own parameters may do that instead. What is required is the check, and it is
**specified here and not yet implemented** (*Implementation status*).

The check can only ever be a **refusal**, never a repair: `loadBalancerClass` is immutable
on an existing Service, so a mismatch surfaces as a refused association naming both
values, and whoever creates the Service — a chart, a controller minting one on a tenant's
behalf, or a user — is responsible for setting it at creation. A controller that creates
the Service *and* names the claim has no excuse for getting it wrong, since it can read
the class first.

Remove the annotation and the driver withdraws the pin it wrote, clears `associatedTo`,
and holds the address again; the `IPAddress` stays `Bound` to its claim and becomes inert —
**a reserved address, held, attached to nothing.** Which is what an unassociated EIP is.
One honest caveat: the backend does not necessarily follow. MetalLB keeps an assignment
until the Service changes or goes away, so a Service that drops the annotation but lives
on keeps announcing the address, and the ledger reports it as a `Conflict` naming that
Service until it is deleted or re-addressed. Detaching an address from a *live* Service
therefore means recreating that Service, which is the natural shape of a cutover anyway;
the common path — the workload is deleted, the address is kept — has no such wrinkle.

**The annotated object is not always a Service.** Some consumers *own* the Service they
are announced on rather than being one: under Gateway API the implementation renders the
data-plane Service from the `Gateway`, so there is no user-authored Service to annotate,
and the object the user does write has no way to name an address. The contract is therefore
an **annotated object that resolves to an address consumer** — `Service` is the first
member and `Gateway` the obvious second. Which object a given backend annotates, and where
it lands the pin (`Gateway.spec.addresses`, infrastructure annotations propagated onto the
generated Service, or the generated Service itself), is the provisioner's business (§4).
The consequence that is *not* the provisioner's business is in *Security*:
`Gateway.spec.addresses` names an address **by value**, so it is the same escalation
surface as a backend pin annotation and the admission policy has to cover it.

This is the claim-first path: the address exists before the Service names it. §6 covers
the opposite order — when an eager allocator hands a Service an address before any claim
exists.

### 6. When the allocator gets there first

An eager allocator does not wait to be asked. MetalLB assigns an address the moment it
sees a `Service type: LoadBalancer`, before any `IPAddressClaim` exists and whether or not
one ever will. Two things follow.

**A reservation is held in the allocator's own books, not merely recorded in ours.** For
every address that is reserved but not currently attached, the provisioner keeps a
**holder**: for MetalLB, a selectorless `type: LoadBalancer` Service in a provisioner-owned
namespace, which MetalLB assigns the address to and, having no endpoints, never announces —
held in the backend's accounting, silent on the wire. Neither MetalLB nor Cilium will give
one address to a second Service without an explicit sharing key, so for as long as the
holder exists the reservation is enforced by the allocator itself rather than by our
detection. This is the primary guarantee, and it is what makes a reserved-but-unattached
address safe to leave lying around.

**Nothing adopts what it did not reserve.** An earlier draft had the controller observe
`status.loadBalancer` on every Service and reconcile foreign assignments into the ledger
after the fact. That is dropped (*Alternatives considered*): with a class's pool at
`autoAssign: false` (§7), a plain Service cannot draw a class address except by naming the
pool or pinning the address, and that is a conflict to detect (§8), not an allocation to
adopt. Addresses from pools no class manages are not in the ledger and are of no interest
to it. The inventory therefore answers "what has been reserved, and who holds it", not
"every address in the cluster" — which is the question an operator actually asks.

**Auto-claim: the ledger reaching a Service that has never heard of it.** The opposite
need is real: a Service that *should* draw from a class but names no claim, because
whoever rendered it knows nothing about claims. The concrete case is a
cloud-controller-manager for tenant Kubernetes clusters, which creates a `type:
LoadBalancer` Service on the host for every LoadBalancer Service in the guest, copying the
guest's annotations once at creation and nothing afterwards. Under `autoAssign: false`
such a Service sits `Pending` forever, and asking every guest to name a claim is not an
answer. So the core offers an **opt-in** path, enabled per class by an annotation on the
`IPAddressClass` and off everywhere otherwise: for a LoadBalancer Service with no claim
annotation, no `loadBalancerClass`, no pin and no assigned address, the core mints an
`IPAddressClaim` of the same name in the same namespace, **owned by the Service**, and
writes the §5 annotation onto the Service; the driver then associates exactly as if a
tenant had written it. A Service may name a class explicitly, or opt out, through
annotations the core defines — which is what a guest cluster's annotations, copied by the
deputy, reach for. A class marked for auto-claim is independent of the default class: the
two decisions have different owners.

Two rules follow from "owned by the Service". **A claim with a controller owner follows
its owner, not the class's reclaim policy**: when the Service goes, its claim goes, and
the claim's addresses are deleted even under `Retain`, because nobody asked to keep them
and a `Released` address per deleted Service is a leak, not a reservation. To keep such an
address, remove the owner reference and the claim becomes an ordinary held one. The rule
is stated for any controller owner, not only Services, so machinery that mints claims on a
tenant's behalf (community #45) inherits it. And **held comes before minted**: when the
namespace already holds an unattached claim of that class, auto-claim attaches the oldest
one rather than minting, and mints only when none is free. In a brokered deployment where
tenants cannot create claims (*User-facing changes*), that is what makes a plain
LoadBalancer Service wear the address the tenant owns, and a second Service with no spare
claim stay `Pending` rather than silently mint one. The first rule is implemented
(cozystack/address-controller#7); the second is specified here.

Allocator-first and claim-first still race in the handoff below, and the inventory can
transiently disagree with the live `status.loadBalancer` set. The design commits to
**eventual consistency** there.

What remains once holders are in place is narrower than a general theft window, and worth
naming precisely rather than waving at:

- **The handoff.** Releasing the holder and pinning the workload's own Service is not
  atomic. For that window the address is unheld and an eager allocator could hand it to
  whichever Service happens to be asking. Narrow, but real — and one of the intervals where
  layer 1 of *Security* earns its place.
- **Annotation writers.** With a reserved pool set `autoAssign: false` a plain Service
  cannot *automatically* draw a reserved address; it can only get one by naming the pool or
  pinning the address explicitly. That is precisely the write layer 1 gates and layer 2
  detects.

### 7. What an admin actually configures

For MetalLB, forever, for the whole cluster:

- **one** `IPAddressPool` covering the reserved range, **with `autoAssign: false`**, and
- **one** `L2Advertisement` (or `BGPAdvertisement`) selecting it.

That is all. No pool per tenant, no `/32`s. The `IPAddressClass` carries the range; the
provisioner renders the backend objects. `autoAssign: false` keeps MetalLB from handing a
reserved address out *automatically* — but it does **not** make the pool unreachable,
because a Service can still name the pool explicitly (`metallb.io/address-pool`) or pin an
address (`metallb.io/loadBalancerIPs`). Closing that path is a security concern, not a
pool-config one; see §8 and *Security*.

### 8. Worked examples: how this rides real allocators, and where it does not hold

No static pool layout can *guarantee* a reservation. On MetalLB, `autoAssign: false`
stops only automatic selection — a Service can still name the reserved pool
(`metallb.io/address-pool`) or pin a specific address (`metallb.io/loadBalancerIPs`) and
be handed a reserved address. And any principal who can impersonate the controller's
ServiceAccount — a cluster-admin can — sails past an admission policy keyed on identity.
So the design does **not** rest on an invariant it cannot enforce. It uses **two layers**,
and is explicit about which one covers which threat:

1. **Admission reduces the blast radius for the parties multi-tenancy is about** —
   tenants and namespaced principals. A `ValidatingAdmissionPolicy` forbids them from
   writing *any* backend annotation that selects a pool or pins an address (MetalLB's
   `metallb.io/address-pool` **and** `metallb.io/loadBalancerIPs`, plus the §1.2
   equivalents), leaving the claim reference as their only route to the reserved range.
2. **Reconciliation makes a collision a detected, correctable state — however it arose.**
   The `IPAddress` ledger is authoritative. The controller watches Services and their
   assigned `status.loadBalancer.ingress`; if a Service holds an address belonging to an
   `IPAddress` whose `claimRef`/`associatedTo` does not name that Service, it sets
   `IPAddress.status.phase: Conflict`, raises a condition and a Service event, and does
   not let the rightful claim silently lose its address. This is what "dealt with" means
   for the cases admission cannot prevent (see the trust boundary in *Security*).
   Adjudication is **per class**: the driver serving the address's class is the one that
   marks it, so with two drivers neither judges the other's handoff window as theft.

A Service arriving *without* a claim reference is **not** by itself the thing being
defended against — an eager allocator handing a Service an address is the normal case §6
reconciles after the fact. The conflict is narrower and specific: a Service ending up with
an address already `Bound` to a *different* claim — by an explicit pin, a race, or an
impersonated write. Layer 2 keys on exactly that — a `claimRef`/`associatedTo` mismatch —
not on the mere absence of an annotation. What separates legitimate allocation from theft
is whose reservation the address belongs to, not whether a claim annotation was present.

**MetalLB.** The class's `IPAddressPool` is `autoAssign: false`, so plain Services never
draw from it *automatically*; they draw from the admin's other pools. Explicit selection
(`address-pool`/`loadBalancerIPs`) is blocked for tenants by layer 1 and detected by layer
2 for anyone else. Scoping the pool further with `IPAddressPool.spec.serviceAllocation`
(limit to a controller-set label or namespace) is available defense-in-depth — not a
guarantee.

> *"A cluster-admin runs `kubectl create -f bad-service.yaml --as
> system:serviceaccount:metallb-iad-system:metallb-iad`, impersonating the driver, referencing the
> reserved pool and maybe pinning a specific address."* — Admission cannot stop this: the
> request carries the controller's identity, and a cluster-admin is the cluster's trust
> root anyway (they can rewrite the policy or edit MetalLB objects directly). Layer 2 is
> what applies. The Service is assigned a reserved address; the controller sees an
> assignment no claim authorizes; the `IPAddress` goes `Conflict` with the offending
> Service named. The reservation is not silently overwritten — the conflict is surfaced
> for an operator (or an automated policy) to resolve. That is the honest boundary: the
> model defends tenants against each other, and turns admin/impersonator collisions into
> **visible faults** rather than pretending to prevent them.

**Cilium LB-IPAM.** Cilium has no `autoAssign` flag; the equivalent is `serviceSelector`.
The reserved `CiliumLoadBalancerIPPool` carries a `serviceSelector` matching a label the
controller stamps on associated Services only, so a plain Service lacks it and draws from
an unselected default pool. The same two layers apply: admission stops a tenant from
setting that label directly, and reconciliation catches an assignment no claim authorizes.

**Cloud (adopt).** The provider holds the reservation (`eipalloc-…`) under its own IAM. A
plain LB Service with no annotation is handed an ephemeral address from the provider's
general pool; the provider's API refuses to double-assign a reserved EIP. Here the
separation is enforced by the provider, not by us — which is exactly why
`source.providerRef` (adopt, don't allocate) is the right model for this world, and why
this is the one backend where the reservation genuinely cannot be raided from inside the
cluster.

## User-facing changes

- **Tenants** gain one kind: `IPAddressClaim`. Create it, read `status.addresses[].address`,
  put that in DNS, reference the claim by name from a Service. They never see `IPAddress`,
  `IPAddressClass`, MetalLB, or a pin annotation.
- **Two operating modes, decided by RBAC alone.** In **self-service** mode tenants create
  and delete their own claims, bounded by quota. In **brokered** mode tenants hold *read*
  access to claims, and platform machinery — a billing integration, a portal, a tenant
  controller — creates them on the tenant's behalf: the tenant may attach and detach the
  address, through the §5 annotation or the held-before-minted rule of §6, but cannot
  release it, and an address moved between namespaces is moved by the machinery through
  `spec.addressName`. Nothing in the model differs between the two. A hosting provider
  that sells addresses on a minimum term runs the second; a platform team with internal
  tenants runs the first.
- **Admins** gain `IPAddressClass` (one per address source) and `IPAddress` (a read-mostly
  inventory: `kubectl get ipaddresses` finally answers "what do we own and who has it").
- **Quota** works with no new machinery: `count/ipaddressclaims.<group>` in a stock
  `ResourceQuota`. (A distribution that renders quotas from its own tenant object has to
  pass `count/` keys through; Cozystack's tenant chart does not yet.)
- **Metering follows the claim.** A platform that meters `type: LoadBalancer` Services
  today meters the wrong thing once addresses are claims: a held address is held whether
  or not a Service wears it, and a placeholder in the driver's namespace is nobody's
  workload. The `IPAddress` ledger is the unit of accounting — its `claimRef` says whose,
  its conditions say since when — and a platform that bills addresses bills claims.

## Upgrade and rollback compatibility

- **Additive.** Nothing about today's `Service type: LoadBalancer` path changes; a
  Service with no claim annotation behaves exactly as now.
- **Adoption path.** A cluster with existing LoadBalancer Services can import their
  addresses as `IPAddress` objects with `source.providerRef`/`fromClass` and a `claimRef`
  to a generated claim — an offline, reversible migration.
- **Rollback.** Deleting the CRDs with `reclaimPolicy: Retain` leaves the backend objects
  and the live Services untouched; addresses stay where they are. With `Delete`, it does
  not — flagged as the irreversible case.
- **Adopting Services that are already pinned.** A cluster that enables the fence with
  hand-pinned Services in place does not break them: an unchanged pin always passes, since
  the policy compares old against new. To bring such an address under the ledger, an admin
  creates its `IPAddress` pre-bound to a claim (`claimRef` set, exactly as a pre-provisioned
  PV), then adds the claim annotation to the Service; the driver's pin equals the existing
  one and nothing moves. Removing a pin is a change and is gated, so unpinning a legacy
  Service is an allowlisted operation.
- **Turning the fence on.** The policy ships with `validationActions` configurable, so a
  deployment runs `Warn` or `Audit` first, reads what would have been denied, extends the
  allowlist, and only then switches to `Deny`.

## Security

**Selecting a pool or pinning an address is a privilege-escalation surface, and this is
the sharp edge of the proposal.**

RBAC authorizes *verbs on resources*, not *fields* or *annotations*. Today, anyone who
can create a `Service` in a namespace can write `metallb.io/loadBalancerIPs: <any
address>` — or name the reserved pool with `metallb.io/address-pool` — and because a
self-allocating backend has **no concept of a reservation**, it will happily hand over an
address another tenant reserved but has not yet attached. That is a theft window, on
MetalLB and Cilium alike. It is *not* a cloud problem: there, IAM already guards
`eipalloc-…`.

No single mechanism closes this, so the response has **two layers** (see *Design §8*),
and the design is explicit about which threat each one covers.

**Layer 1 — admission, against tenants.** Turn the ungatable annotation into a reference
to an RBAC-gated object:

1. A tenant may only write `local.sdn.cozystack.io/ip-address-claim`, naming an object in
   their **own namespace**, which RBAC *can* gate.
2. The **driver** of the claim's class writes the backend's raw pool/pin annotations; the
   core never does.
3. A `ValidatingAdmissionPolicy` **rejects a non-allowlisted principal** writing *any*
   backend annotation that selects a pool or pins an address — MetalLB's
   `metallb.io/address-pool` and `metallb.io/loadBalancerIPs`, plus the §1.2 equivalents
   for other backends — on a Service.

*Identifying the driver, and not breaking the platform.* The policy must not rely on a
hardcoded username. It matches the driver's **ServiceAccount** by name and namespace
(`system:serviceaccount:<ns>:<sa>`), configured at install time, and it must carry an
**allowlist**: real clusters have non-tenant principals that legitimately write these
annotations — GitOps reconcilers applying content their operators authored, and system
components whose own Services ship with pool annotations pre-set (an Istio installation,
say). A naive "reject everyone but the driver" breaks reconciliation on day one, so the
policy denies the write only for principals **outside** an explicit, admin-configurable
allowlist of usernames and groups, and it ships with `Warn`/`Audit` modes for rollout
(*Upgrade and rollback compatibility*).

*The fence covers every input the backend honours.* Not only the current annotation: a
backend's deprecated spellings are still parsed, and so may be a core field. For MetalLB
the gated set is `metallb.io/loadBalancerIPs`, `metallb.io/address-pool` and
`metallb.io/allow-shared-ip`, the same three under the legacy `metallb.universe.tf/`
prefix, **and `Service.spec.loadBalancerIP`**, which MetalLB still reads despite the
field's deprecation in Kubernetes 1.24. Omit any one and the fence has a door. The
comparison is old-versus-new, not presence: the driver pins the tenant's own Service, and
the tenant must stay able to edit the rest of it.

*The allowlist's hard case: trusted principals applying untrusted content.* An identity
allowlist assumes a trusted principal writes content it authored, and that assumption fails
wherever a privileged controller applies material some user supplied. Two shapes of this are
ordinary rather than exotic: a GitOps reconciler that applies user-authored releases under
its **own** identity instead of impersonating the user, and a cloud-controller-manager that
copies Service annotations from a user-controlled cluster into an infrastructure Service
verbatim. So **not every platform controller belongs on the allowlist**. A deputy that
forwards user content is *denied*, like the users behind it, and the Services it creates
get their addresses through the one path the fence leaves open: the claim annotation, or
the auto-claim of §6, whose class-selection and opt-out annotations are exactly the ones a
deputy may safely copy. Allowlist such a deputy instead and every user behind it inherits
the ability to write a pin; deny it without that path and legitimate Services stay
`Pending`. Where a deputy must forward gated keys for some other reason, **annotation
filtering at the deputy is a precondition of layer 1, not a hardening extra**: a strip-list
for the gated keys, and impersonation rather than ambient authority in any reconciler
applying user-authored content. Until those hold for a given deputy, layer 1 is
**partial** for the users behind it and layer 2 is what covers them. The design says so
rather than assuming a trusted identity implies trusted content.

**Layer 2 — reconciliation, for everyone admission cannot bind.** Layer 1 is keyed on
identity, so it is defeated by anyone who can impersonate an allowlisted ServiceAccount —
which a cluster-admin can (`--as
system:serviceaccount:metallb-iad-system:metallb-iad`), along with rewriting
the policy or editing MetalLB objects directly. **This is a real trust
boundary, not a bug to be closed:** a cluster-admin is the cluster's trust root, and
handing out a reserved address is within their authority, not an escalation. What the
design owes here is not prevention but **detection**. Because the `IPAddress` ledger is
authoritative and continuously reconciled, any collision — impersonation, a direct
backend edit, a misconfigured pool, MetalLB assigning a reserved address to a Service with
no matching claim — surfaces as `IPAddress.status.phase: Conflict` with the offending
Service named, and the rightful claim is never *silently* overwritten. Admission defends
tenants against each other; reconciliation turns the cases admission cannot reach into
visible faults instead of quiet theft.

Landing layer 1 is worth doing on its own, independent of the rest of the model: the
theft window it closes is live in any cluster running a shared auto-assign pool today.

**Where this component stops, and the entitlement it leaves room for.** The two layers
above cover the surface a reservation can be *stolen* through. They do not decide *which
class* a namespace may draw from: a tenant who can create an `IPAddressClaim` can consume
an address and may name any `spec.className`. `ResourceQuota` bounds how many addresses
they hold, not from where. Kubernetes has the same gap for `StorageClass` — any namespace
may name any class — and closes it, where it bothers to, with per-class quota scoping.

That is deliberate rather than unfinished. This component's job is the **capability** —
reserve an address, hold it, attach it, detach it, release it — and encoding a tenancy
model it cannot see belongs above it. Three things are stated here so that whoever closes
the gap does not have to discover them:

1. **The check keys on the namespace, never on the requesting principal.** Platform
   machinery that mints claims on a tenant's behalf — community #45's attachment
   controller, a billing integration — creates them under its *own* identity, so a policy
   keyed on the requester never sees the tenant. A namespace-keyed check covers
   tenant-authored and machinery-minted claims alike, and a `ValidatingAdmissionPolicy`
   can express it synchronously, since `paramRef` resolves a parameter object in the
   request's namespace.
2. **The empty `className` is a decision, not a convenience.** "Empty means the default
   class" is the documented path, so a rule of the form *empty or entitled* admits every
   namespace to the default class. Whether the default class is entitled is a separate
   boolean.
3. **`spec.addressName` cannot be checked in CEL**, because resolving an address to its
   class needs a second object read. A deployment that gates it either refuses the field
   to tenant principals outright — the reference policy's choice, and the right one where
   platform machinery is what moves specific addresses — or checks it in a webhook.

The natural home for the data is an **optional entitlement object in the core**: per
namespace, per class, the number of claims that namespace may hold of that class, with
zero meaning not entitled and absence meaning unrestricted — the per-class quota scoping
Kubernetes applies to storage, which gives entitlement and a per-tenant ceiling in one
object. When present, the core refuses a claim that exceeds it with a condition, and the
reference admission policy enforces the same bound synchronously on the tenant-facing
path. The object's shape is **deferred** (*Open questions*); what is fixed is that it is
namespace-keyed and that the distribution, not the tenant, writes it. In Cozystack the
`Tenant` object is the obvious source and the tenant chart the obvious renderer, which is
also how a brokered deployment (*User-facing changes*) expresses "this tenant owns three
addresses of the public class".

## Failure and edge cases

- **Class range exhausted** → claim stays `Pending` with a reason; no partial binding.
- **Claim deleted, `reclaimPolicy: Retain`** → `IPAddress` goes `Released`, keeps the
  address, is not reusable until an admin clears the `claimRef`. (PV semantics, deliberately.)
- **Claim deleted, `Delete`** → address returned to the range; the backend object is torn down.
- **Service references a claim in another namespace** → rejected. Cross-namespace address
  sharing is not a thing.
- **Two Services reference one claim** → resolved by reconciliation, not at admission. A
  `ValidatingAdmissionPolicy` cannot see the claim — CEL is given the request object and the
  policy's params, not arbitrary cluster state — so admission-time rejection would need a
  webhook. The rule is **the live holder keeps it**: the Service already in `associatedTo`
  stays associated for as long as it exists, a later Service naming the claim is refused
  with an `AssociationRejected` event, and the claim reports the refusal in an `Associated`
  condition naming both; only when the holder is gone does the next one take over. The
  address is never silently moved. A 1:1 binding is 1:1; letting the second win quietly is
  how an address goes dead.
- **Plain Service pulls a reserved address** (explicit `address-pool`/pin, or a request
  made under an impersonated controller identity) → not prevented in general; **detected**.
  The `IPAddress` goes `Conflict`, the offending Service is named, and the reservation is
  not silently overwritten. See §8 and *Security*.
- **`spec.addressName` names an address that is absent, `Bound`, `Released`, or of another
  class or family** → nothing binds; the claim stays `Pending` with the ordinary waiting
  reason. Naming an address filters, it never seizes.
- **A `Dual` claim associated with a single-stack Service** → the driver pins only the
  family the Service carries; the other address stays `Bound` to the claim and inert. The claim is not
  `Lost`, and the unattached family is never silently released.
- **Backend lacks `Pin`** → claims against that class are refused loudly, rather than
  allocating an address that can never be attached. With no capability registry there is
  nothing for admission to consult, so this is the provisioner refusing and reporting it on
  the claim, not an admission-time rejection.
- **Address adopted from a provider, then released provider-side** → `IPAddress` goes
  `Lost`. It must not silently re-allocate.
- **Controllers down while a namespace holding claims is deleted** → the claim finalizer
  holds the namespace until the core returns, exactly as PVC protection does; a controller
  outage never leaks an address, at the cost of a deletion that waits.
- **Class deleted while addresses of it are bound** → refused, not cascaded. The rendered
  pool and advertisement are owned by the class, so letting the deletion through would
  withdraw every address of the class at once; a finalizer holds the class until its
  addresses are gone.
- **`Released` addresses accumulate under `Retain`** → by default an admin clears
  `claimRef`, PV semantics. A class may set an optional **`releaseDelay`**: once it
  elapses the controller clears `claimRef` itself and the address returns to `Available`,
  so a release followed by a cooling-off period before reuse needs no operator. An
  address marked held — an annotation the controller honours — is exempt, which is how an
  operator quarantines an address they do not want reissued.

## Testing

- **Unit:** the allocator (range carving, exhaustion, reclaim transitions, the source union).
- **Integration:** claim → bind → associate → *delete the Service* → assert the address is
  still held → associate it to a **different** workload → assert the same address comes back.
  **That single test is the proposal.** It is precisely what cannot pass today.
- **e2e (per backend):** MetalLB and Cilium, on a real cluster: an external client reaches
  the workload on the reserved address, and still reaches it after the workload is
  rebuilt behind the same claim.
- **Admission:** a tenant writing a raw pool-select or pin annotation, a legacy-prefixed
  one, or `spec.loadBalancerIP` is rejected; a tenant
  reserving an address and a second tenant attempting to pin it directly is rejected; an
  allowlisted GitOps/system principal writing a pool annotation is **admitted** (the
  allowlist does not over-block).
- **Reconciliation:** a Service assigned a reserved address with no authorizing claim
  (e.g. created under an impersonated controller identity) drives the `IPAddress` to
  `Conflict` and emits an event; the rightful claim's binding is not lost.

## Rollout

Sketch — sequencing is an open question:

1. CRDs + core controller + the **MetalLB** provisioner + the admission policy. (The
   admission policy is not phase 2. See *Security*.) All three exist, the policy as the
   driver's opt-in template — see *Implementation status*. The §5 `loadBalancerClass`
   check and the held-before-minted rule of §6 complete this phase.
2. **Gateway as an association target** (§5), with `Gateway.spec.addresses` fenced. This
   is the consumer tenants will meet first: the accepted external-database-exposure
   proposal puts a tenant's database traffic on the single address of the tenant Gateway,
   so the address a tenant most wants to keep is one a Service annotation cannot reach.
   Which Cilium release honours `spec.addresses` is open question 4.
3. The Cilium provisioner. Proves the abstraction is not a MetalLB adapter — **this is
   the phase that either validates or falsifies the design**, and it should come early.
   A second *consumer* has since exercised the association half (*Implementation
   status*); a second *provisioner* is what remains.
4. A cloud provisioner (adoption path, `source.providerRef`). Proves the source union.

## Open questions

1. **Provisioners: in-tree or out-of-tree?** In-tree for three backends; CSI's history
   says that stops scaling. Where is the line, and do we want the CRD contract on day one?
2. **PureLB.** Does it have *any* "request this exact IP" mechanism? If not, it cannot be a
   backend, and that should be stated rather than discovered.
3. **Sharing one address across Services** (different ports — GCP and Cilium both permit
   it). Today one consumer at a time holds a claim and a second reference is refused (§5).
   Should that relax to several consumers on disjoint ports? It is the mirror of the
   deferred several-addresses-on-one-consumer case in *Non-goals*, and the two probably
   want answering together, since both are really "what is the cardinality between a claim
   and the things that wear it".
4. **Gateway.** `Gateway.spec.addresses` is the natural pin for a Gateway consumer (§5),
   and Cilium supports it with LB-IPAM; whether the Cilium release a platform ships honours
   it, and whether the infrastructure-annotation path is needed as a fallback, is
   unverified. The provisioner decides where the pin lands; which object a tenant
   annotates is the part that needs settling here.
5. **The entitlement object's shape** (*Security*). Namespace-keyed, per-class, a count;
   whether it is one object per namespace or one per class, who may read it, and whether
   the reference admission policy consumes it directly as its `paramRef` are open.

## Alternatives considered

**Bind the address to a Service** — the shape community #29 proposed as
`ServiceExposure`. A namespaced object naming a `serviceRef`, which allocates an address
and reports it in status. **Rejected as the
model for *this* problem:** it fuses *allocation* with *association*, so the address's
lifetime is the Service's lifetime — which is the exact thing being fixed. It cannot
express "keep this address, the workload is gone", which is the entire user request.
The **class** half of that idea is right and is kept here; the exposure half is not a
reservation and cannot be made into one.

**Per-tenant pools.** Give each tenant an `IPAddressPool` (or a `/32` per address) and let
MetalLB do the rest. **Rejected:** it is the status quo's failure mode, it makes the admin
the allocator, it scales as O(tenants) or O(addresses) in operator-managed objects, and it
*still* provides no reservation — the address is released the moment the Service goes away.

**Let the tenant write the backend's pin annotation directly.** **Rejected:** unauthorizable
(RBAC does not gate fields), and it hands every tenant the ability to steal any reserved
address that is not currently attached. See *Security*.

**Reuse Cluster API's IPAM kinds.** `ipam.cluster.x-k8s.io` `IPAddressClaim`/`IPAddress` is
*literally* this pattern — namespaced claim, concrete address object, a documented
third-party provider contract — and shares even the kind names chosen here. **Rejected:**
it has only ever been wired to **Machine** addressing, never Services, and has no reclaim
policy and no association, which are the two things this proposal exists to add. The
contract is copied; the group is not.

**Adopt every live LoadBalancer assignment into the ledger.** An earlier draft reconciled
`status.loadBalancer` on every Service into `IPAddress` objects, so the inventory would
converge on everything in use. **Rejected:** catch-all adoption of objects nobody asked for
is uncharacteristic of Kubernetes, and there is nothing to adopt — a class's pool is
`autoAssign: false`, so a Service holding a class address without a claim got it through a
pin, which is a conflict to surface, not a reservation to record; and addresses outside
every class are not the ledger's concern. The real need underneath — Services that should
draw from a class but cannot name a claim — is met by the opt-in auto-claim of §6.

**Wait for upstream.** **Rejected:** KEP-1880 lists generalizing to an IPAM API as an
explicit non-goal, and `loadBalancerIP` was deprecated with no replacement. There is
nothing to wait for.

**Do nothing.** **Rejected:** the capability simply does not exist today — an address
cannot be held, moved, quota'd, or enumerated — and the pin-annotation theft window in
*Security* is live in any cluster running a shared auto-assign pool. Doing nothing leaves
both the missing capability and the open security hole in place.
