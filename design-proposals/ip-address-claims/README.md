# IP addresses as a first-class resource: `IPAddress`, `IPAddressClaim`, `IPAddressClass`

- **Title:** `IP addresses as a first-class resource: IPAddress, IPAddressClaim, IPAddressClass, and a provisioner contract`
- **Author(s):** `@lllamnyp`

> The object model, the problem statement and the survey of existing primitives are
> settled. The controllers of phase 1 of *Rollout* exist and are pending review;
> *Implementation status* lists what phase 1 still lacks and where the text is ahead of
> the code. Open: the provisioner contract's own API (capabilities
> are not modelled), the entitlement object of *Security*, sequencing after phase 1, and
> *Open questions*.

## Overview

A tenant cannot own an address. An address appears when a tenant creates a `Service` of
`type: LoadBalancer` and disappears when the Service is deleted. No object holds it,
counts it, moves it to another workload, or is stable enough to put in DNS.

This proposal makes the address a resource, on the `PersistentVolume` pattern:

| storage | addresses | role |
|---|---|---|
| `StorageClass` | **`IPAddressClass`** | which backend pool, which provisioner |
| `PersistentVolume` | **`IPAddress`** (cluster-scoped) | the address itself, with a `claimRef` and a reclaim policy |
| `PersistentVolumeClaim` | **`IPAddressClaim`** (namespaced) | "give me one" — what a tenant creates |
| CSI driver | **a provisioner contract** | how a class's backend allocates, adopts, and pins |

The driving use case is the public address, with the semantics of an AWS Elastic IP:
allocate, keep, attach, detach, attach elsewhere, release. The resource is not
public-specific. A class names a pool, and a platform can offer several pools: public
ranges, a private LB range, a provider's static addresses. The kinds are therefore named
`IPAddress`, and "public" is a property of a class's pool.

### Naming

`IPAddress` and `IPAddressClaim` share their short names with two existing kinds:

- **Kubernetes core** has `IPAddress` in `networking.k8s.io` (KEP-1880). It is a record
  whose name is the IP, with a generic `parentRef`. KEP-1880 names "any generalization
  onto something like an IPAM API" a non-goal. Our kind is in a different group and
  carries a reclaim policy, a claim binding and a class.
- **Cluster API** has `IPAddressClaim` and `IPAddress` in `ipam.cluster.x-k8s.io`, with a
  provider contract. They serve Machine addressing only and have no reclaim policy and no
  association. We define our own kinds (*Alternatives considered*).

`ExternalIP*`, proposed in review, was not chosen. `Service.spec.externalIPs` is an older,
unrelated mechanism in which a Service claims an address by value with no allocator
(CVE-2020-8554), and the kinds would be confused with it.

Kinds are qualified by API group, so the shared short names do not collide. The group is
`local.sdn.cozystack.io` (*Positioning*).

## Scope and related proposals

- **Adjacent:** community #29 (`structured-external-exposure`) restructures how a managed
  application requests exposure. This proposal defines what an address is. They meet at
  one point: an exposure entry can name an `IPAddressClaim` instead of minting an address.
  Neither depends on the other.
- **Related:** `Service.spec.loadBalancerClass` selects the LB implementation for a
  Service. An `IPAddressClass` determines, through its provisioner, the
  `loadBalancerClass` a consuming Service must carry (§5). The `ExposureClass` kind proposed with #29 never shipped, and
  `network.cozystack.io` does not exist; cozystack/cozystack#3164 and
  cozystack/cozystack#3218 chose `loadBalancerClass`.
- **Depended on by:** community #45 (`endpoint-attachments`), which attaches one endpoint
  of an application to an address. It consumes an `IPAddressClaim` and the annotation of
  §5, and its `EndpointAttachment` kind is in the same `local.sdn.cozystack.io` group
  (*Positioning*). This proposal does not wait for it.
- **Deferred to a sibling:** the datapath for whole-IP 1:1 NAT (all ports in, egress as
  that address), provided today by [cozy-proxy](https://github.com/cozystack/cozy-proxy).
  That is a CNI concern. This proposal is the allocation half; allocation works with a
  stock LoadBalancer Service and no NAT.

## Decisions

Three implementation findings changed the design:

- [0003. The class references the backend's configuration and does not restate it](./decisions/0003-the-class-references-backend-configuration-and-does-not-restate-it.md) — the MetalLB class names an existing pool instead of listing CIDRs, and the class has no `loadBalancerClass` field.
- [0002. The ledger does not adopt assignments; the core claims on behalf of Services that cannot](./decisions/0002-the-ledger-does-not-adopt-the-core-claims-on-behalf-of-services.md) — the backwards inventory of §6 is dropped and auto-claim replaces it.
- [0001. The backend allocates; the ledger records what it assigned](./decisions/0001-the-backend-allocates-and-the-ledger-records.md) — the reference driver reserves through placeholder Services instead of allocating itself.

## Positioning

The component is useful to any Kubernetes platform that runs MetalLB, Cilium LB-IPAM or a
cloud LB, and it also ships as part of the Cozystack API. The tenant contract is a
namespaced CRD, the quota is a `ResourceQuota`, the enforcement is a
`ValidatingAdmissionPolicy`. Nothing requires Cozystack.

Standalone does not mean outside the Cozystack API group. `etcd-operator.cozystack.io` is
a standalone product under `*.cozystack.io`, and this follows it. The component is shaped
for Cozystack integration: a default class, and an exposure path that can name a claim.

The group is `local.sdn.cozystack.io`, in the networking family, because networking APIs
should not accumulate group names. `SecurityGroup` is in `sdn.cozystack.io`, served by the
aggregated Cozystack API. Cozyplane serves its own `SecurityGroup` in that group and its
CRD-served kinds in `local.sdn.cozystack.io`. The `local.` prefix marks CRD-served kinds;
the bare group marks aggregated ones. `ipam.cozystack.io` would be a fourth name for one
subject.

The prefix also carries a guarantee. CRD-served kinds pass through the kube-apiserver's
ResourceQuota admission, so `count/` quotas work on them. Kinds served by the aggregated
API do not.

`local.sdn.cozystack.io` is new; nothing serves it today. The group string creates no
Cozystack dependency: the three CRDs install under any name.

The objection is that `ipam.` describes an address ledger better than `sdn.`: the ledger
is not a datapath, and its consumers include Gateways and cloud providers. The answer is
that this component is not all of IPAM and cannot become it. Allocation stays with the
allocator (AWS, MetalLB), so an `ipam.` group would promise more generality than the
component has. If that changes, the group is a v2 question.

The kinds are defined here, not adopted from Cluster API's IPAM (*Alternatives
considered*).

## Implementation status

The controllers of phase 1 of *Rollout* exist under the Cozystack organisation, pending
review:

- **[address-controller](https://github.com/cozystack/address-controller)**: the core.
  The three kinds, claim–address binding, status, reclaim, and the driver contract. It
  writes to a Service only on the auto-claim path of §6
  (cozystack/address-controller#7).
- **[metallb-iad](https://github.com/cozystack/metallb-iad)**: the reference driver for
  MetalLB. It holds a reservation as a placeholder Service in its own namespace, so
  MetalLB's accounting refuses to assign a held address elsewhere. On `main` the chart
  has no admission policy, so layer 1 of *Security* is absent as shipped.
  cozystack/metallb-iad#6, open, adds the policy as an opt-in chart template;
  cozystack/metallb-iad#5, open, opts the placeholders out of auto-claim.
- **cozystack/cozystack#3382** packages both as optional system packages, with tenant RBAC
  on claims and a distribution-specific admission policy. The driver's policy is the
  reference; a distribution may disable it and ship its own.

A second consumer tests the association contract.
[Cozyplane](https://github.com/lllamnyp/cozyplane) copies a claim name into the §5
annotation on a Service it owns, with no module import, no CRD dependency and no informer
on the claim kinds. Without the mechanism it auto-assigns; reserved and dynamic are one
code path. On a three-node cluster with address-controller, metallb-iad, MetalLB and
cozyplane, an address survived delete-and-rebind and stayed reachable.

This tests the association seam only. The allocation seam, a second provisioner, is
untested (*Rollout*).

**Specified here and not yet implemented:** the `loadBalancerClass` check and the
serviceability check of §5; the pool-or-pin exclusion and the auto-claim class
constraint of §6; pinning
only the families a Service carries (§2); conditions on `IPAddress` (§2) and the
`Associated` condition on a claim (§5); per-class conflict adjudication (§8); the
held-before-minted rule of §6; the class naming its backend pool and reporting
`status.loadBalancerClass` (§2, §7; metallb-iad still renders its pool from CIDRs in the
class parameters); the release delay and the class-deletion guard (*Failure and edge
cases*); Gateway as an association target (§5); the entitlement object
(*Security*).

**In the code and not in this text:** `claimRef.uid`, `status.className` on the claim,
the claim's `conditions` field with `ClassResolved` and `Bound`, a `Pending` address
phase, the provisioner stamp annotation, the finalizers. The implementation's design document is the reference for
these. None changes the model.

## Context

### What exists today

Cozystack installs MetalLB and renders no `IPAddressPool` or `L2Advertisement`. The admin
configures an allocator: MetalLB L2 or BGP, Cilium LB-IPAM, a cloud LB, or
`Service.spec.externalIPs`.

In every one of them an address is allocated to a Service when the Service is created and
freed when it is deleted.

### The problem

> *"I gave my customer this IP address. It is in their firewall allow-list. I need to
> rebuild the VM behind it, and I need the address to still be mine afterwards."*
>
> *"I want to move this address from the old VM to the new one during a cutover."*
>
> *"I want to reserve four addresses now, because the range is nearly full, and attach
> them over the next month."*

None of these is expressible, because the address has no existence apart from the
Service:

- **It cannot be held.** Deleting the Service frees the address, and another tenant may
  receive it.
- **It cannot be moved.** There is no detach and attach.
- **It cannot be counted.** There is no object to quota. The only lever is a pool per
  tenant.
- **It cannot be gated.** Anyone who can create a Service can allocate an address. RBAC
  authorizes verbs on resources, not fields.
- **It cannot be listed.** "Which addresses do we own, and who holds them?" requires
  listing every Service in the cluster.

This is a new capability: hold, move, quota, enumerate. Cozystack has no
reserve-then-associate mechanism to preserve; it uses kube-ovn for subnets and VPCs, not
for its EIP objects.

## Goals

- A tenant can request an address and receive one, or in a brokered deployment have one
  requested on their behalf and attach it, without an admin pre-creating a pool.
- An address survives the deletion of the workload it was attached to (reclaim policy
  `Retain`) and can be attached to another workload.
- An operator can list every address the cluster owns and who holds it.
- A `ResourceQuota` on `count/ipaddressclaims.<group>` bounds how many addresses a tenant
  holds. No new quota machinery.
- An admin configures an address source once per class. No pool per tenant, no `/32`
  pools.
- The tenant API does not name the backend.
- The design admits MetalLB, Cilium LB-IPAM, and a cloud provider that allocates outside
  the cluster.
- A reserved address is never silently lost to a plain LoadBalancer Service. A collision
  is detected and reported (§8, *Security*).

### Non-goals

- **Announcing addresses.** ARP/NDP, BGP and cloud VNIC assignment stay with the LB
  implementation.
- **The 1:1 NAT datapath.** See *Scope*.
- **Several addresses on one consumer.** One rule is settled and enforced: a claim is
  consumed by one consumer at a time. The converse is open: how many claims one consumer
  may hold. A workload may own several Services, each with its own claim; a managed
  service with a public address and an internal one is two Services with two claims. A
  single consumer with several addresses is a real case: `Gateway.spec.addresses` is a
  list. How several claims attach to one consumer, and how a provisioner renders that, is
  deferred. Two related cases are deferred with it: extra addresses on a tunnel interface
  the platform does not see (a VM acting as a router), which is unrelated to reservation;
  and the platform-owned variant, which needs an internal address and in-guest
  configuration per address.
- **Replacing the LB implementation.** MetalLB, Cilium and cloud LBs stay; this sits above
  them.
- **IPv4-only thinking.** A claim may request IPv4, IPv6 or both; a `Dual` claim binds one
  `IPAddress` per family (§2).
- **Field-level authorization in general.** It is used in *Security*; the general problem
  is out of scope.

## Design

### 1. The primitives that already exist (survey)

Five findings shape the design:

1. **Nothing has a standing reservation.** Not MetalLB, Cilium LB-IPAM, kube-vip, PureLB,
   OpenELB, or Kubernetes core. KEP-1880 shipped `ServiceCIDR` and `IPAddress` (the name
   is the IP, with a generic `spec.parentRef`) and named "any generalization onto
   something like an IPAM API" a non-goal. Nothing upstream will provide this.

2. **Every backend can pin a specific address.** `Service.spec.loadBalancerIP` was
   deprecated in Kubernetes 1.24 with no replacement, so each implementation defined its
   own annotation, and one still reads the field:

   | backend | pin mechanism |
   |---|---|
   | MetalLB | `metallb.io/loadBalancerIPs` |
   | Cilium LB-IPAM | `lbipam.cilium.io/ips` |
   | kube-vip | `kube-vip.io/loadbalancerIPs` |
   | OpenELB | `eip.openelb.kubesphere.io/v1alpha1` |
   | AWS LB Controller | `service.beta.kubernetes.io/aws-load-balancer-eip-allocations` |
   | GCP / GKE | `networking.gke.io/load-balancer-ip-addresses` |
   | PureLB | `spec.loadBalancerIP`, the deprecated core field; no annotation ([purelb#132](https://gitlab.com/purelb/purelb/-/issues/132)) |

   The semantics are identical. This is the seam the design rests on: a backend that can
   pin can bind a reserved address to a workload.

3. **A reserved, unattached address is inert everywhere.** MetalLB announces only the live
   `status.loadBalancer.ingress` of a Service it allocated. Cilium LB-IPAM assigns;
   something else announces. A cloud EIP routes nowhere until associated. These are the
   correct semantics; AWS bills for an idle EIP for this reason.

4. **One pool can serve many tenants.** MetalLB's `IPAddressPool.spec.serviceAllocation`
   and Cilium's `CiliumLoadBalancerIPPool.spec.serviceSelector` scope a single pool by
   namespace or label. `L2Advertisement` and `BGPAdvertisement` select pools, not
   addresses. Pool-per-tenant and `/32` pools were never necessary.

5. **`Service.spec.loadBalancerClass`** (GA in 1.24) lets several LB implementations
   coexist; MetalLB honours it via `--lb-class`.

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
  reclaimPolicy: Retain                  # Retain | Delete   (default Retain)
  # releaseDelay: 720h                   # optional, Retain only: Released -> Available
  #                                      # after this long (Failure and edge cases)
  parameters:                            # opaque to the core controller
    pool: public                         # an existing MetalLB IPAddressPool (§7)
status:                                  # written by the provisioner
  loadBalancerClass: ""                  # the implementation it fronts (§5);
                                         # empty => the cluster's default implementation
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
  phase: Bound               # Pending | Bound | Lost
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

**The class references backend configuration.** `parameters` names objects the backend
already has, such as a MetalLB pool (§7). The provisioner, not the class, determines the
`loadBalancerClass` and reports it in `status` (§5). Decision 0003 records why.

**`spec.addressName`** is the analogue of `PersistentVolumeClaim.spec.volumeName`: a
claim names one `IPAddress` instead of taking any address of its class. It applies to
single-family claims. The controller binds the named address only if it is `Available`,
has no `claimRef`, is not being deleted, and matches the claim's class and family.
Otherwise the claim stays `Pending`, as it would without the field. The field narrows the
candidate set. It cannot take an address from another class, from another claim, or from
`Released` state before an admin clears the `claimRef`.

The field does not authorize. `IPAddress` is cluster-scoped, so the claim names an object
outside its namespace, and RBAC cannot gate the name. `spec.className` has the same
property. Kubernetes treats `volumeName` the same way: any claim may name any `Available`
volume of its class, and an admin who wants a volume to go to one tenant pre-binds it by
setting its `claimRef`. Pre-provisioned addresses are pre-bound the same way. Gating the
field is covered in *Security*.

`IPAddressClaim.status.addresses` is a list because a `Dual` claim binds one `IPAddress`
per family and reports both. A single-family claim has one entry. `Dual` is one claim
binding two objects because a dual-stack Service names one claim in §5. The driver pins
only the families the Service carries (*Failure and edge cases*).

`IPAddress.status.conditions` record when, not only who. `Bound` and `Associated` are
`metav1.Condition`s; their `lastTransitionTime` records which claim held the address and
which consumer wore it. An operator answering "which tenant had this address at this hour"
reads the ledger, not Service events, which expire. `Conflict` is reported as a condition
beside the phase, so the offending consumer is recorded durably.

### 3. The address-source union

Two kinds of backend exist, and the model admits both:

- **Self-allocating backends** (MetalLB, Cilium, kube-vip, OpenELB) hold no reservation.
  The ledger is the record of reservation, and the address comes from the backend pool
  the class names (`source.fromClass`). Which component picks the address is the
  driver's choice. The reference driver asks MetalLB for an address through the holder of
  §6 and records what MetalLB assigned, so the backend and the ledger cannot disagree
  (decision 0001).
- **Provider-reservation backends** (AWS, GCP, Hetzner) already hold the reservation as an
  object with a stable handle (`eipalloc-…`, a named static address), under the provider's
  IAM. The provisioner adopts it (`source.providerRef`) and does not allocate.

This is the `PersistentVolume` volume-source union (`spec.csi`, `spec.nfs`, …). It is the
evidence that the shape is not specific to MetalLB: it survives a backend where allocation
is not ours. The claim does not expose the difference.

### 4. The provisioner contract

`IPAddressClass.spec.provisioner` names a controller, as `StorageClass` names a CSI driver.
The contract as implemented is two things. The core stamps the provisioner name onto each
claim as an annotation, and a driver selects its claims by it. The driver fills the
`IPAddress` source union with `fromClass` or `providerRef`. Nothing declares capabilities;
the core has no registry to consult, and a driver that cannot serve a claim leaves it
`Pending` and reports why on the claim.

The intended shape is a declaration per provisioner, with the driver refusing what it
cannot do:

| capability | meaning | consequence if absent |
|---|---|---|
| `Allocate` | can obtain an address from the class's pool | class must use `providerRef` adoption |
| `Adopt` | can wrap a provider-side reservation | `providerRef` claims rejected |
| **`Pin`** | **can bind a specific address to a Service** | **the class cannot serve claims at all** |

`Pin` is the capability the model depends on. A backend that cannot be told which address
to use cannot attach a reserved one, so a class over such a backend rejects claims instead
of allocating an address it cannot bind. No surveyed backend lacks it (§1.2). The
declaration is deferred with the registration object it would live on (*Open questions*).

Whether provisioners are in-tree (a switch in one controller) or out-of-tree (separate
deployments with a CRD contract, as CSI did) is open. In-tree suffices for three
backends; CSI's history says it stops at about five.

### 5. Association

Association is a separate, reversible act. A workload references the claim, never the
address:

```yaml
kind: Service
metadata:
  annotations:
    local.sdn.cozystack.io/ip-address-claim: web    # a claim in THIS namespace
spec: {type: LoadBalancer, ...}
```

The driver reads the annotation, looks up a claim of that name in the Service's own
namespace, resolves it to its address, and writes the backend's pin annotation (§1.2). The
backend then allocates and announces that address.

**The provisioner determines the `loadBalancerClass`.** Each driver deployment fronts one
LB implementation instance and is configured with that instance's value (empty means the
cluster default, §1.5). It reports the value as `status.loadBalancerClass` on every class
it serves (decision 0003). A consuming Service must carry that value. A Service whose
`loadBalancerClass` selects one implementation while its claim's address is held by
another asks two backends for one address. The provisioner's holder Services (§6) carry
the same value. The check is specified and not implemented (*Implementation status*).

The check refuses and never repairs: `loadBalancerClass` is immutable on an existing
Service. A mismatch is a refused association naming both values. Whoever creates the
Service sets the field at creation; a controller that creates the Service and names the
claim reads the class's `status.loadBalancerClass` first.

**Serviceability before handoff.** The driver checks that it can serve the consumer
before it releases the hold: the Service is `type: LoadBalancer`, its `loadBalancerClass`
matches the class, the class's backend pool admits the Service (for MetalLB, the pool's
`serviceAllocation`), and the backend will act on the pin. A consumer that fails the check
is refused with an event, `associatedTo` stays unset, and the holder keeps the address.
The order matters. Setting `associatedTo` disarms the hold (§6), so a consumer recorded
before it is checked leaves the address unheld, announced nowhere, and reported as
attached. Specified, not implemented.

**Detaching.** When the annotation is removed, the driver withdraws its pin, clears
`associatedTo`, and holds the address again. The `IPAddress` stays `Bound` to its claim:
reserved and attached to nothing, like an unassociated EIP. MetalLB keeps an assignment
until the Service is deleted or re-addressed, so a running Service that drops the
annotation keeps announcing the address, and the ledger marks the address `Conflict`
naming that Service until it is gone (decision 0001). To detach an address from a running
Service, delete or recreate the Service.

**The annotated object is not always a Service.** Under Gateway API the implementation
renders the data-plane Service from the `Gateway`, so there is no user-authored Service to
annotate. The contract is therefore an annotated object that resolves to an address
consumer. `Service` is the first member; `Gateway` is the second. Which object a backend
annotates, and where it lands the pin (`Gateway.spec.addresses`, infrastructure
annotations propagated to the generated Service, or the generated Service), is the
provisioner's choice (§4). `Gateway.spec.addresses` names an address by value, so the
admission policy must cover it (*Security*).

This is the claim-first path. §6 covers the allocator-first path.

### 6. When the allocator gets there first

MetalLB assigns an address to a `Service` of `type: LoadBalancer` as soon as it sees it,
before any claim exists.

**A reservation is held in the allocator's own accounting.** For every reserved,
unattached address the provisioner keeps a holder. For MetalLB the holder is a
selectorless `type: LoadBalancer` Service in a provisioner-owned namespace. MetalLB assigns
the address to it and, since it has no endpoints, never announces it. MetalLB and Cilium do
not give one address to a second Service without a sharing key, so the allocator itself
enforces the reservation while the holder exists. This is the primary guarantee (decision
0001).

**The ledger records only addresses reserved through it.** The controller does not create
`IPAddress` objects for addresses it finds on Services. An address a plain Service holds
from a class pool is a conflict (§8), not an inventory entry. Decision 0002 covers the
earlier design.

**Auto-claim.** Some Services should draw from a class but cannot name a claim, because
the component that renders them does not know about claims. The case that forced this is
cloud-provider-kubevirt, the cloud-controller-manager of tenant Kubernetes clusters: it
creates a host-side `type: LoadBalancer` Service for each LoadBalancer Service in the
guest and copies the guest's annotations once, at creation. Under `autoAssign: false` these Services stay
`Pending`. The core therefore has an opt-in path, enabled per class by an annotation on
the `IPAddressClass`: for a LoadBalancer Service with no claim annotation, no
`loadBalancerClass`, no pin and no assigned address, the core creates an `IPAddressClaim`
with the Service's name in the Service's namespace, owned by the Service, and writes the
§5 annotation on the Service. The driver associates as usual. A Service can name a class
or opt out through annotations the core defines; cloud-provider-kubevirt copies these
from the guest Service. The core also skips any Service that carries a backend pool or pin
input (*Security* lists them): such a Service has chosen its address source. The driver's
placeholders carry the pool annotation, so this rule excludes them without the opt-out
annotation and without knowledge of the driver's namespace. The auto-claim class is
independent of the default class.

An auto-claimed Service has no `loadBalancerClass`, so the cluster's default
implementation serves it (§1.5). An auto-claim class is therefore a class of the default
implementation, with an empty `loadBalancerClass`. This is the semantics of the field,
not a rule of this design: the field is immutable once the Service is
`type: LoadBalancer`, and MetalLB reads no annotation in its place, so no component can
route an unclassed Service to another implementation after creation. The provisioner
reports an event on a class it serves that is marked for auto-claim while its
`loadBalancerClass` is non-empty, since no Service the core mints for can be served.
Auto-claim into such a class needs the field set at creation, which a mutating admission
webhook could do from the class's status; that is deferred.

Two rules follow:

- **A claim with a controller owner follows its owner, not the class reclaim policy.**
  When the Service is deleted its claim is deleted, and the claim's addresses are deleted
  even under `Retain`. A `Released` address per deleted Service would be a leak. To keep
  the address, remove the owner reference; the claim is then an ordinary held claim. The
  rule applies to any controller owner, so machinery that mints claims for a tenant
  (community #45) inherits it. Implemented in cozystack/address-controller#7.
- **Held before minted.** If the namespace holds an unattached claim of the class,
  auto-claim attaches the oldest one and mints only when none is free. In a brokered
  deployment (*User-facing changes*) a plain LoadBalancer Service wears the address the
  tenant owns, and a second Service with no spare claim stays `Pending`. Specified, not
  implemented.

Claim-first and allocator-first allocations race during the handoff, so the inventory can
disagree with `status.loadBalancer` briefly. The design accepts eventual consistency there.

Two gaps remain with holders in place:

- **The handoff.** Releasing the holder and pinning the workload's Service is not atomic.
  The address is unheld until the first successful pin; `associatedTo` is already set, so
  a failed pin or a driver restart does not re-arm the hold. Layer 1 of *Security* covers
  the window.
- **Annotation writers.** With the pool at `autoAssign: false`, a plain Service receives a
  reserved address only by naming the pool or pinning the address. Layer 1 gates that
  write; layer 2 detects it.

### 7. What an admin configures

For MetalLB, once per cluster:

- one `IPAddressPool` for the reserved range, with `autoAssign: false`;
- one `L2Advertisement` or `BGPAdvertisement` selecting it.

The admin writes both in MetalLB's own terms, and the `IPAddressClass` names the pool
(`parameters.pool`). The driver does not render or edit it. It refuses a class whose pool
is missing, has `autoAssign: true`, or is named by another class, and reports why on the
class's claims and in an event on the class. Range syntax, `avoidBuggyIPs`,
`serviceAllocation` and the choice of L2 or BGP are pool and advertisement settings, not
class parameters (decision 0003).
`autoAssign: false` stops MetalLB from assigning a reserved address automatically. It does
not stop a Service from naming the pool (`metallb.io/address-pool`) or pinning an address
(`metallb.io/loadBalancerIPs`); §8 and *Security* cover that.

Under BGP, MetalLB announces each assigned address as a host route whether or not it is
reserved, so scattered reservations within a range do not change route-table growth.
`BGPAdvertisement.aggregationLength` summarises as it does without this proposal.

### 8. How this rides real allocators

No pool layout guarantees a reservation. A Service can name the reserved pool or pin an
address (§7), and a principal that can impersonate the driver's ServiceAccount passes an
identity-keyed admission policy. The design therefore uses two layers:

1. **Admission, for tenants and namespaced principals.** A `ValidatingAdmissionPolicy`
   forbids them from writing any backend input that selects a pool or pins an address
   (*Security* lists them). The claim reference is their only route to the reserved range.
2. **Reconciliation, for everything else.** The driver watches
   `status.loadBalancer.ingress` on Services. If a Service holds an address whose
   `IPAddress` has a `claimRef` or `associatedTo` that does not name that Service, the
   driver sets `IPAddress.status.phase: Conflict`, adds a condition, and emits a Service
   event. The rightful claim keeps its binding. Adjudication is per class: the driver
   serving the address's class marks the conflict, so two drivers do not mark each other's
   handoff windows.

A Service without a claim annotation is not the threat. The conflict is a Service holding
an address `Bound` to a different claim, through a pin, a race, or an impersonated write.
Layer 2 keys on the `claimRef`/`associatedTo` mismatch, not on the absence of an
annotation.

**MetalLB.** The class's pool is `autoAssign: false`, and the driver refuses one that is
not (§7), so plain Services draw from other pools. Explicit selection is blocked for
tenants by layer 1 and detected by layer 2 for everyone else. `IPAddressPool.spec.serviceAllocation` can restrict the pool to a
controller-set label or namespace as defence in depth.

A cluster-admin who runs `kubectl create --as
system:serviceaccount:metallb-iad-system:metallb-iad` with a pinned Service passes
admission, because the request carries the driver's identity. A cluster-admin is the trust
root and can rewrite the policy or edit MetalLB objects directly, so admission does not
apply to them. Layer 2 applies: the `IPAddress` goes `Conflict` naming the Service, and an
operator resolves it. The model defends tenants against each other and reports admin
collisions as faults.

**Cilium LB-IPAM.** Cilium has no `autoAssign`; the equivalent is `serviceSelector`. The
reserved `CiliumLoadBalancerIPPool` selects a label the driver sets on associated
Services, so a plain Service draws from a default pool. Admission stops a tenant from
setting the label; reconciliation detects an assignment no claim authorizes.

**Cloud (adopt).** The provider holds the reservation under its IAM. A plain Service
receives an ephemeral address from the provider's pool, and the provider refuses to
double-assign a reserved EIP. The provider enforces the separation, which is why
`source.providerRef` is the model for this backend.

## User-facing changes

- **Tenants** gain one kind, `IPAddressClaim`. They create it, read
  `status.addresses[].address`, put it in DNS, and name the claim from a Service. They
  never see `IPAddress`, `IPAddressClass`, MetalLB or a pin annotation.
- **Two operating modes, set by RBAC.** In self-service mode tenants create and delete
  claims, bounded by quota. In brokered mode tenants have read access to claims, and
  platform machinery (a billing integration, a portal, a tenant controller) creates them.
  The tenant attaches and detaches through the §5 annotation or the held-before-minted
  rule of §6 and cannot release. The machinery moves an address between namespaces
  through `spec.addressName`. The model is the same in both modes. A hosting provider that
  sells addresses on a minimum term runs brokered; a platform team with internal tenants
  runs self-service.
- **Admins** gain `IPAddressClass`, one per address source, and `IPAddress`, the
  inventory. `kubectl get ipaddresses` answers who holds what.
- **Quota** is a `ResourceQuota` on `count/ipaddressclaims.<group>`. A distribution that
  renders quotas from its own tenant object must pass `count/` keys through; Cozystack's
  tenant chart does not yet.
- **Metering follows the claim.** A held address is held whether or not a Service wears
  it, and a placeholder in the driver's namespace belongs to no tenant workload. A platform
  that meters LoadBalancer Services meters the wrong object once addresses are claims. The
  `IPAddress` ledger is the unit of accounting: `claimRef` says whose, the conditions say
  since when.

## Upgrade and rollback compatibility

- **Additive.** A Service with no claim annotation behaves as today.
- **Adoption.** Existing LoadBalancer Services can be imported: create an `IPAddress` with
  `source.providerRef` or `fromClass` and a `claimRef` to a generated claim. The migration
  is offline and reversible.
- **Rollback.** Deleting the CRDs under `reclaimPolicy: Retain` leaves backend objects and
  Services untouched. Under `Delete` it does not; this is the irreversible case.
- **Already-pinned Services.** The policy compares old and new values, so an unchanged pin
  passes. To bring a pinned address under the ledger, create its `IPAddress` pre-bound to
  a claim (`claimRef` set, as for a pre-provisioned PV), then add the claim annotation to
  the Service. The driver's pin equals the existing one. Removing a pin is a change and is
  gated, so unpinning a legacy Service requires an allowlisted principal.
- **Enabling the policy.** `validationActions` is configurable. Run `Warn` or `Audit`
  first, read what would have been denied, extend the allowlist, then switch to `Deny`.

## Security

**Selecting a pool or pinning an address is a privilege-escalation surface.**

RBAC authorizes verbs on resources, not fields or annotations. Anyone who can create a
Service in a namespace can write `metallb.io/loadBalancerIPs: <address>` or name the
reserved pool with `metallb.io/address-pool`. A self-allocating backend has no concept of
a reservation and assigns the address, including one another tenant reserved but has not
attached. This applies to MetalLB and Cilium. It does not apply to cloud backends, where
IAM guards the reservation.

The response has two layers (§8).

**Layer 1: admission, against tenants.** The ungatable annotation becomes a reference to
an RBAC-gated object:

1. A tenant writes only `local.sdn.cozystack.io/ip-address-claim`, naming an object in
   their own namespace. RBAC gates that object.
2. The driver of the claim's class writes the backend's pool and pin inputs. The core
   never does.
3. A `ValidatingAdmissionPolicy` rejects a non-allowlisted principal that writes any
   backend input selecting a pool or pinning an address on a Service.

*Identifying the driver.* The policy matches the driver's ServiceAccount by name and
namespace (`system:serviceaccount:<ns>:<sa>`), configured at install. It carries an
allowlist of usernames and groups, because non-tenant principals legitimately write these
inputs: GitOps reconcilers applying content their operators authored, and system
components whose Services ship with pool annotations (an Istio installation, for example).
Without the allowlist, reconciliation breaks on the first such write. The policy ships with
`Warn` and `Audit` modes (*Upgrade and rollback compatibility*).

*The gated set is every input the backend honours.* Deprecated spellings are still parsed,
and so may be a core field. For MetalLB the set is `metallb.io/loadBalancerIPs`,
`metallb.io/address-pool`, `metallb.io/allow-shared-ip`, the same three under the legacy
`metallb.universe.tf/` prefix, and `Service.spec.loadBalancerIP`, which MetalLB still
reads. The comparison is old value against new value, not presence, because the driver
pins the tenant's own Service and the tenant must be able to edit the rest of it.

*Deputies.* An identity allowlist assumes the principal authored what it writes. A
privileged controller that applies user-supplied content breaks that assumption. Two
common cases: a GitOps reconciler that applies user-authored releases under its own
identity instead of impersonating the user, and cloud-provider-kubevirt, which copies
Service annotations from the tenant cluster into the host Service it creates. Such a
deputy is denied, like the users behind it, and is not allowlisted. Its Services obtain
addresses through the inputs the policy leaves writable: the claim annotation and the
auto-claim annotations of §6, which cloud-provider-kubevirt copies from the guest Service. Where a deputy
must forward gated inputs, filtering at the deputy is a precondition of layer 1: a
strip-list for the gated keys, or impersonation instead of ambient authority in the
reconciler. Until a deputy filters, layer 1 is partial for the users behind it and layer 2
covers them.

**Layer 2: reconciliation, for everyone admission cannot bind.** Layer 1 is keyed on
identity. A cluster-admin can impersonate the driver's ServiceAccount, rewrite the policy,
or edit MetalLB objects. This is a trust boundary: a cluster-admin is the trust root, and
handing out a reserved address is within their authority. The design detects rather than
prevents. The ledger is reconciled continuously, so an impersonated write, a direct
backend edit, a misconfigured pool, or an assignment with no matching claim sets
`IPAddress.status.phase: Conflict` naming the Service, and the rightful claim keeps its
binding.

Layer 1 is worth landing alone: the theft window exists in every cluster with a shared
auto-assign pool.

**Where this component stops.** The two layers cover theft of a reservation. They do not
decide which class a namespace may draw from: a tenant who can create an `IPAddressClaim`
may name any `spec.className`. `ResourceQuota` bounds the count, not the source.
Kubernetes has the same gap for `StorageClass` and closes it with per-class quota scoping.

Encoding a tenancy model belongs above this component. Three constraints apply to whatever
closes the gap:

1. **The check keys on the namespace, not on the requesting principal.** Machinery that
   mints claims for a tenant (community #45's attachment controller, a billing
   integration) creates them under its own identity, so a requester-keyed policy never
   sees the tenant. A `ValidatingAdmissionPolicy` can key on the namespace, since
   `paramRef` resolves a parameter object in the request's namespace.
2. **The empty `className` is an explicit decision.** Empty means the default class, so a
   rule of the form "empty or entitled" admits every namespace to the default class.
   Whether the default class is entitled is a separate boolean.
3. **`spec.addressName` cannot be checked in CEL**, because resolving an address to its
   class needs a second object read. A deployment either refuses the field to tenant
   principals, which is the reference policy's choice, or checks it in a webhook.

The data belongs in an optional entitlement object in the core: per namespace, per class,
the number of claims the namespace may hold. Zero means not entitled; absence means
unrestricted. This is the per-class quota scoping Kubernetes applies to storage, and the
shape community #99 uses for GPU budgets; it gives entitlement and a per-tenant ceiling
in one object. When present, the core refuses
a claim over the limit with a condition, and the reference admission policy enforces the
same bound synchronously. The object's shape is open (*Open questions*). The distribution
writes it, not the tenant. In Cozystack the `Tenant` object is the source and the tenant
chart the renderer; a brokered deployment (*User-facing changes*) uses it to express
"this tenant owns three addresses of the public class".

## Failure and edge cases

- **Class pool exhausted** → claim stays `Pending` with a reason; no partial binding.
- **Claim deleted, `Retain`** → `IPAddress` goes `Released` and keeps the address. It is
  not reusable until an admin clears `claimRef` (PV semantics).
- **Claim deleted, `Delete`** → address returned to the range; the backend object is torn
  down.
- **Service names a claim in another namespace** → rejected.
- **Two Services name one claim** → resolved by reconciliation. A
  `ValidatingAdmissionPolicy` cannot read the claim (CEL sees the request object and the
  policy's params), so admission-time rejection would need a webhook. The live holder
  keeps the address: the Service in `associatedTo` stays associated while it exists, a
  later Service is refused with an `AssociationRejected` event, and the claim reports the
  refusal in an `Associated` condition naming both. When the holder is gone, the next
  Service takes over.
- **Plain Service receives a reserved address** (pool annotation, pin, or impersonated
  write) → detected, not prevented. The `IPAddress` goes `Conflict` naming the Service;
  the reservation is not overwritten (§8, *Security*).
- **`spec.addressName` names an address that is absent, `Bound`, `Released`, or of another
  class or family** → nothing binds; the claim stays `Pending` with the ordinary reason.
- **`Dual` claim on a single-stack Service** → the driver pins only the family the Service
  carries. The other address stays `Bound` and unattached. The claim is not `Lost`.
- **Backend lacks `Pin`** → the provisioner refuses claims of that class and reports it on
  the claim. There is no capability registry for admission to consult.
- **Adopted address released provider-side** → `IPAddress` goes `Lost`. It is not
  reallocated. The claim reports `Lost`, and the address keeps its `claimRef` and its
  entry in `status.addresses` until an admin deletes the `IPAddress` or restores the
  provider-side reservation; the claim then rebinds.
- **Service names a claim but the driver cannot serve it** (not `type: LoadBalancer`, or a
  `loadBalancerClass` the backend ignores) → refused with an event before the hold is
  released (§5). The address stays held.
- **Two default classes, or two auto-claim classes** → claims with an empty `className`
  stay `Pending` with `MultipleDefaultClasses`; auto-claim does nothing and emits a
  Warning event on the Service. A CRD schema cannot validate uniqueness across objects;
  Kubernetes treats duplicate default `StorageClass`es the same way.
- **Namespace with claims deleted while the core is down** → the claim finalizer holds the
  namespace until the core returns, as PVC protection does. No address leaks; the deletion
  waits.
- **Class deleted with bound addresses** → refused. The driver resolves the pool through
  the class to hold and pin its addresses, so deleting the class leaves them unservable. A
  finalizer holds the class until its addresses are gone. The pool is the admin's and is
  not deleted with the class.
- **The class's pool deleted, shrunk, or switched to `autoAssign: true`** → MetalLB
  withdraws assignments outside the remaining range, and the affected addresses go `Lost`.
  A pool that auto-assigns is refused (§7).
- **`Released` addresses accumulate under `Retain`** → an admin clears `claimRef`. A class
  may set `releaseDelay`: after it elapses the controller clears `claimRef` and the address
  returns to `Available`. An address marked held by an annotation is exempt, which
  quarantines an address an operator does not want reissued.

## Testing

- **Unit:** the allocator: range carving, exhaustion, reclaim transitions, the source
  union.
- **Integration:** claim → bind → associate → delete the Service → assert the address is
  still held → associate it to a different workload → assert the same address returns.
  This test is the proposal; it cannot pass today.
- **e2e, per backend:** MetalLB and Cilium on a real cluster. An external client reaches
  the workload on the reserved address, and still reaches it after the workload is rebuilt
  behind the same claim.
- **Admission:** a tenant writing a pool-select or pin annotation, a legacy-prefixed one,
  or `spec.loadBalancerIP` is rejected; a second tenant pinning a reserved address is
  rejected; an allowlisted principal writing a pool annotation is admitted.
- **Reconciliation:** a Service assigned a reserved address with no authorizing claim
  drives the `IPAddress` to `Conflict` with an event; the rightful claim keeps its
  binding.

## Rollout

Sequencing is open:

1. CRDs, core controller, the MetalLB provisioner, and the admission policy. The
   controllers exist; the policy is in an open PR (*Implementation status*). The §5
   `loadBalancerClass` and serviceability checks and the held-before-minted rule of §6
   complete this phase.
2. Gateway as an association target (§5), with `Gateway.spec.addresses` fenced. The
   accepted external-database-exposure proposal puts a tenant's database traffic on the
   tenant Gateway's single address, so this is the consumer tenants meet first. Which
   Cilium release honours `spec.addresses` is open question 3.
3. The Cilium provisioner. A second provisioner tests the allocation seam; a second
   consumer has tested the association seam (*Implementation status*).
4. A cloud provisioner (`source.providerRef`). Tests the source union.

## Open questions

1. **Provisioners: in-tree or out-of-tree?** In-tree suffices for three backends; CSI's
   history says it stops scaling. Where is the line, and is the CRD contract needed on day
   one?
2. **Sharing one address across Services on disjoint ports.** GCP and Cilium permit it.
   Today one consumer holds a claim at a time (§5). This is the mirror of the
   several-addresses-on-one-consumer case in *Non-goals*; both are the cardinality
   between a claim and its consumers.
3. **Gateway.** `Gateway.spec.addresses` is the natural pin for a Gateway consumer, and
   Cilium supports it with LB-IPAM. Whether the shipped Cilium release honours it, and
   whether the infrastructure-annotation path is needed as a fallback, is unverified.
   Which object a tenant annotates needs settling here.
4. **The entitlement object's shape** (*Security*). One object per namespace or per class,
   who may read it, and whether the reference admission policy consumes it as its
   `paramRef`.

## Alternatives considered

**Bind the address to a Service** (community #29's `ServiceExposure`): a namespaced object
naming a `serviceRef`, which allocates an address and reports it. Rejected: it fuses
allocation with association, so the address's lifetime is the Service's lifetime, which is
the problem being fixed. The class half of the idea is kept.

**Per-tenant pools**: an `IPAddressPool` per tenant, or a `/32` per address. Rejected: the
admin becomes the allocator, the object count scales with tenants or addresses, and there
is still no reservation.

**Let the tenant write the pin annotation.** Rejected: RBAC does not gate fields, so every
tenant could take any reserved, unattached address (*Security*).

**Reuse Cluster API's IPAM kinds.** `ipam.cluster.x-k8s.io` has `IPAddressClaim` and
`IPAddress` with a provider contract and the same kind names. Rejected: they serve Machine
addressing only and have no reclaim policy and no association, which are the two things
this proposal adds. The contract is copied; the group is not.

**Adopt every live LoadBalancer assignment into the ledger.** An earlier draft had the
controller create an `IPAddress` for every assigned address found on a Service. Rejected:
a class pool is `autoAssign: false`, so a Service holding a class address without a claim
got it through a pin, which is a conflict (§8), and addresses outside every class are out
of scope. Adoption of objects nobody asked for is also uncharacteristic of Kubernetes; the
PersistentVolume controller does not adopt volumes it did not provision. The need
underneath, Services that cannot name a claim, is met by auto-claim (§6, decision 0002).

**The class restates the backend's configuration**: CIDRs for the driver to render into
a MetalLB pool, and a `loadBalancerClass` field. Rejected in decision 0003.

**Wait for upstream.** Rejected: KEP-1880 names an IPAM API a non-goal, and
`loadBalancerIP` was deprecated with no replacement.

**Do nothing.** Rejected: an address cannot be held, moved, counted or listed, and the
theft window in *Security* exists in every cluster with a shared auto-assign pool.
