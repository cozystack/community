# IP addresses as a first-class resource: `IPAddress`, `IPAddressClaim`, `IPAddressClass`

- **Title:** `IP addresses as a first-class resource: IPAddress, IPAddressClaim, IPAddressClass, and a provisioner contract`
- **Author(s):** `@lllamnyp`
- **Date:** `2026-07-15`
- **Status:** Draft

> **The shape is settled and implemented; the contract details are not.** The problem
> statement, the primitives survey, and the object model are worked out, and phase 1 of
> *Rollout* now exists in code (see *Implementation status*). Deliberately still open:
> the provisioner contract's own API (capabilities are not modelled yet), the admission
> policy of *Security*, the sequencing beyond phase 1, and the open questions below.

## Overview

A tenant cannot *own* an address. They can only cause one to appear as a side effect
of creating a `Service type: LoadBalancer`, and it evaporates when that Service does.
There is no object to hold, keep, quota, bill, hand to another workload, or point DNS
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
  reclaim policy or association. Whether we adopt that group and contract or mint our
  own is an open question (below); the name choice keeps that door open.

Kinds are qualified by API group, so the short-name overlap is not a collision. The
kinds live in `local.sdn.cozystack.io` (see *Positioning*).

## Scope and related proposals

- **Adjacent, not a dependency:** `design-proposals/structured-external-exposure`
  (community #29) restructures how a managed application *requests* exposure. This
  proposal is about what an address **is**. The two meet at one point — an exposure
  entry should be able to name an `IPAddressClaim` instead of implicitly minting an
  address — but neither needs the other to land first, and this proposal does not
  assume #29's shape.
- **Related:** an `ExposureClass` kind exists today in `network.cozystack.io`. This
  proposal takes a position on it (see *Alternatives*): the **class** idea is right
  and should survive in some form; binding an address's lifetime to a `Service` is
  the part that cannot serve this use case.
- **Depended on by:** `design-proposals/endpoint-attachments` (community #45), which
  attaches one endpoint of an application to an external address. It consumes an
  `IPAddressClaim` and the association annotation of §5; this proposal does not wait on
  it and lands independently.
- **Deferred to a sibling:** the *datapath* for whole-IP 1:1 NAT (all ports in, and
  the workload egressing **as** that address) — today provided by
  [cozy-proxy](https://github.com/cozystack/cozy-proxy)'s nftables rules. That is a
  CNI concern, tracked separately. **This proposal is the allocation half; that is
  the forwarding half.** They are independent: allocation is useful with a stock
  LoadBalancer Service and no 1:1 NAT at all.

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

Whether the kinds are greenfield or an adoption of Cluster API's IPAM kinds is an open
question (below).

## Implementation status

Phase 1 of *Rollout* exists:

- **[address-controller](https://github.com/lllamnyp/address-controller)** — the
  class-agnostic core: the three kinds, claim–address binding, status, reclaim, and the
  contract per-class drivers plug into. It never touches a Service.
- **[metallb-iad](https://github.com/lllamnyp/metallb-iad)** — the reference per-class
  driver, for addresses announced by MetalLB. A reservation is held as a placeholder
  Service in a driver-owned namespace, so MetalLB's own accounting refuses to
  double-assign a held address.

One backend proves little on its own, so the load-bearing evidence is a **second
consumer**. [Cozyplane](https://github.com/lllamnyp/cozyplane) wears reserved addresses
through this contract with **no module import, no CRD dependency, and no informer on the
claim kinds**: it copies a claim name into the association annotation (§5) on a Service it
already owns, and the driver does the rest. With the mechanism absent entirely it behaves
identically, auto-assigning instead — reserved and dynamic are one code path. Validated
end to end on a three-node cluster running address-controller + metallb-iad + MetalLB +
cozyplane: an address survived delete-and-rebind and stayed externally reachable
throughout.

Note precisely which seam that tests. *Rollout* §2 — a second **provisioner** — is the
*allocation* seam, and it remains outstanding. What is demonstrated is the *association*
seam: that a backend with an entirely different datapath can wear a reserved address while
knowing nothing about the ledger beyond one annotation. The coupling turned out looser
than this document assumed it would need to be.

The `ValidatingAdmissionPolicy` of *Security* is implemented in neither repository. It is
the outstanding half of phase 1, not a phase 2 item.

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

> *"I want to move this address from the old VM to the new one during a cutover."*

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

This is a **new capability**, argued on its own merits: hold, move, quota, enumerate,
bill. Cozystack has no prior reserve-then-associate mechanism to preserve — the
platform uses kube-ovn only for subnets and VPCs, not for its EIP objects — so there
is nothing here being restored, only something being added.

## Goals

- A tenant can request an address and receive one, without an admin pre-creating a
  pool object for them.
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
- **Multiple platform-owned addresses per workload.** A claim binds to one Service;
  a workload holds one claim. Requesting *N* owned addresses for one workload is a
  step beyond "an address is a resource", and it is not clear the case is even real:
  the obvious example (a VM acting as a router or VPN concentrator) attaches its extra
  addresses on a **tunnel interface the platform neither owns nor sees**, which makes
  it orthogonal to address *reservation* rather than an extension of it. Deferred until
  a concrete platform-owned use case exists. Dual-stack (one v4 + one v6) is a separate,
  narrower question and is treated below, not folded into this.
- **Replacing the LB implementation.** MetalLB/Cilium/cloud stay exactly where they
  are; this sits above them.
- **IPv4-only thinking.** The model is family-agnostic; a claim may request v4, v6, or
  both. (Whether one `IPAddress` carries both families or a `Dual` claim binds two is
  an open question below.)
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
  reclaimPolicy: Retain                  # Retain | Delete   (default Retain)
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
  associatedTo:                        # nil => reserved but inert
    kind: Service
    namespace: tenant-a
    name: web-lb
```

`IPAddressClaim.status.addresses` is a **list** deliberately: a `Dual` claim binds a
v4 and a v6 `IPAddress` and must report both, and a scalar `address` field could not.
For a single-family claim the list has one entry. (Whether `Dual` is even one claim
binding two objects, or two claims, is an open question — but the status shape must not
foreclose the list case, so it is a list from the start.)

### 3. The address-source union — the reason this is not a MetalLB adapter

There are two structurally different worlds, and the model must admit both:

- **Self-allocating backends** (MetalLB, Cilium, kube-vip, OpenELB): *nothing anywhere*
  holds a reservation. **Our provisioner is the IPAM of record.** `IPAddress` is
  authoritative, and its address is carved from the class's range (`source.fromClass`).
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

Remove the annotation and the announcement is withdrawn, but the `IPAddress` stays
`Bound` to its claim and simply becomes inert — **a reserved address, held, attached to
nothing.** Which is what an unassociated EIP is.

This is the claim-first path: the address exists before the Service names it. §6 covers
the opposite order — when an eager allocator hands a Service an address before any claim
exists.

### 6. When the allocator gets there first

An eager allocator does not wait to be asked. MetalLB assigns an address the moment it sees
a `Service type: LoadBalancer`, before any `IPAddressClaim` exists and whether or not one
ever will. So the inventory cannot assume every address originates from a claim: the
controller must also work **backwards** — observing `status.loadBalancer` on Services and
reconciling the addresses it finds into `IPAddress` objects after the fact — so the
cluster-scoped inventory converges on what is actually in use rather than only on what was
asked for.

This is loosely the shape of a `StatefulSet` `volumeClaimTemplate` — a workload causing a
first-class object to exist without anyone hand-writing it — but the analogy is weak and
not worth leaning on:

- **An address is non-fungible.** Any 20 GiB PV substitutes for any other; a specific IP
  does not. "Give me one" and "give me *this* one" are different requests, and that exact,
  scarce identity is the whole reason an inventory is needed.
- **The allocator does not wait for us.** A `volumeClaimTemplate` PVC materialises a volume
  only through the provisioner; here MetalLB has already acted by the time the controller
  looks. Claim-first and allocator-first allocations can therefore race, and the inventory
  can transiently disagree with the live `status.loadBalancer` set.

The design commits only to **eventual consistency** here, and to Service → inventory
reconciliation as a first-class direction alongside claim → address. How the race is
resolved is implementation, not settled in this document.

And eventual consistency really is the ceiling for now. An eager allocator that gets there
first can hand a plain Service an address that is already `Bound` to a claim but not yet
associated — a reserved-but-inert address it has no way of knowing is spoken for. Layer 2
(§8) detects the collision, but there is no clean remedy today: the address is already
announced for the wrong Service, and the allocator will not surrender it on request. A real
fix likely waits for the per-provider provisioners (§4), where allocation is ours and can
decline a reserved address in the first place. Until then this is an acknowledged gap,
deferred to the implementation phase.

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
> system:serviceaccount:cozy-address-controller:address-controller`, referencing the
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
- **Admins** gain `IPAddressClass` (one per address source) and `IPAddress` (a read-mostly
  inventory: `kubectl get ipaddresses` finally answers "what do we own and who has it").
- **Quota** works with no new machinery: `count/ipaddressclaims.<group>` in a stock
  `ResourceQuota`.

## Upgrade and rollback compatibility

- **Additive.** Nothing about today's `Service type: LoadBalancer` path changes; a
  Service with no claim annotation behaves exactly as now.
- **Adoption path.** A cluster with existing LoadBalancer Services can import their
  addresses as `IPAddress` objects with `source.providerRef`/`fromClass` and a `claimRef`
  to a generated claim — an offline, reversible migration.
- **Rollback.** Deleting the CRDs with `reclaimPolicy: Retain` leaves the backend objects
  and the live Services untouched; addresses stay where they are. With `Delete`, it does
  not — flagged as the irreversible case.

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
2. The **controller** writes the backend's raw pool/pin annotations.
3. A `ValidatingAdmissionPolicy` **rejects a non-allowlisted principal** writing *any*
   backend annotation that selects a pool or pins an address — MetalLB's
   `metallb.io/address-pool` and `metallb.io/loadBalancerIPs`, plus the §1.2 equivalents
   for other backends — on a Service.

*Identifying the controller, and not breaking the platform.* The policy must not rely on
a hardcoded username. It matches the controller's **ServiceAccount** by name and
namespace (`system:serviceaccount:<ns>:<sa>`), configured at install time. And it must
carry an **allowlist**: real clusters have non-tenant principals that legitimately write
these annotations — GitOps reconcilers (Flux/Argo SAs) and system controllers (system
Istio Services ship with pool annotations already set). A naive "reject everyone but the
controller" breaks reconciliation on day one, so the policy denies the write only for
principals **outside** an explicit, admin-configurable allowlist.

**Layer 2 — reconciliation, for everyone admission cannot bind.** Layer 1 is keyed on
identity, so it is defeated by anyone who can impersonate an allowlisted ServiceAccount —
which a cluster-admin can (`--as
system:serviceaccount:cozy-address-controller:address-controller`), along with rewriting
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

Note this does not, by itself, close field-level authorization in general — a tenant who
can create an `IPAddressClaim` can still consume an address. That is what the class and
the `ResourceQuota` are for, and it is a bound, not a gate.

## Failure and edge cases

- **Class range exhausted** → claim stays `Pending` with a reason; no partial binding.
- **Claim deleted, `reclaimPolicy: Retain`** → `IPAddress` goes `Released`, keeps the
  address, is not reusable until an admin clears the `claimRef`. (PV semantics, deliberately.)
- **Claim deleted, `Delete`** → address returned to the range; the backend object is torn down.
- **Service references a claim in another namespace** → rejected. Cross-namespace address
  sharing is not a thing.
- **Two Services reference one claim** → the second is rejected. A 1:1 binding is 1:1;
  silently letting the second win is how an address goes quietly dead.
- **Plain Service pulls a reserved address** (explicit `address-pool`/pin, or a request
  made under an impersonated controller identity) → not prevented in general; **detected**.
  The `IPAddress` goes `Conflict`, the offending Service is named, and the reservation is
  not silently overwritten. See §8 and *Security*.
- **Backend lacks `Pin`** → the class rejects claims at admission, loudly, rather than
  allocating an address that can never be attached.
- **Address adopted from a provider, then released provider-side** → `IPAddress` goes
  `Lost`. It must not silently re-allocate.

## Testing

- **Unit:** the allocator (range carving, exhaustion, reclaim transitions, the source union).
- **Integration:** claim → bind → associate → *delete the Service* → assert the address is
  still held → associate it to a **different** workload → assert the same address comes back.
  **That single test is the proposal.** It is precisely what cannot pass today.
- **e2e (per backend):** MetalLB and Cilium, on a real cluster: an external client reaches
  the workload on the reserved address, and still reaches it after the workload is
  rebuilt behind the same claim.
- **Admission:** a tenant writing a raw pool-select or pin annotation is rejected; a tenant
  reserving an address and a second tenant attempting to pin it directly is rejected; an
  allowlisted GitOps/system principal writing a pool annotation is **admitted** (the
  allowlist does not over-block).
- **Reconciliation:** a Service assigned a reserved address with no authorizing claim
  (e.g. created under an impersonated controller identity) drives the `IPAddress` to
  `Conflict` and emits an event; the rightful claim's binding is not lost.

## Rollout

Sketch — sequencing is an open question:

1. CRDs + core controller + the **MetalLB** provisioner + the admission policy. (The
   admission policy is not phase 2. See *Security*.) The controllers and the driver
   exist; the admission policy does not — see *Implementation status*.
2. The Cilium provisioner. Proves the abstraction is not a MetalLB adapter — **this is
   the phase that either validates or falsifies the design**, and it should come early.
   A second *consumer* has since exercised the association half (*Implementation
   status*); a second *provisioner* is what remains.
3. A cloud provisioner (adoption path, `source.providerRef`). Proves the source union.

## Open questions

1. **Provisioners: in-tree or out-of-tree?** In-tree for three backends; CSI's history
   says that stops scaling. Where is the line, and do we want the CRD contract on day one?
2. **Reuse Cluster API's IPAM?** `ipam.cluster.x-k8s.io` `IPAddressClaim`/`IPAddress` is
   *literally* this pattern — namespaced claim, concrete address object, a documented
   third-party provider contract — and shares even the kind names we chose. It has only
   ever been wired to **Machine** addressing, never Services, and has no reclaim policy or
   association. Do we adopt those kinds (and that group), or mint our own and merely copy
   the contract? (Leaning: our own — we need reclaim policy and association, which it has
   no concept of. But the shape is not novel and review should know that.) This is the
   greenfield-vs-adopt question *Positioning* leaves open.
3. **`ExposureClass`.** A class kind already exists in `network.cozystack.io`. Is
   `IPAddressClass` a second class kind, or the same one grown a provisioner? Answering
   this depends on the fate of `ExposureClass`/`ServiceExposure`, which is being
   re-examined independently.
4. **Dual-stack.** Does one `IPAddress` carry a v4 and a v6 address, or does a `Dual` claim
   bind two `IPAddress`es? (PV has no precedent. Leaning: two objects, one claim — the
   status list already admits it.)
5. **PureLB.** Does it have *any* "request this exact IP" mechanism? If not, it cannot be a
   backend, and that should be stated rather than discovered.
6. **Sharing one address across Services** (different ports — GCP and Cilium both permit
   it). Does a claim bind to one Service, or may several reference it on disjoint ports?

## Alternatives considered

**Bind the address to a Service (`ServiceExposure`-shaped).** A namespaced object naming
a `serviceRef`, which allocates an address and reports it in status. **Rejected as the
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

**Wait for upstream.** **Rejected:** KEP-1880 lists generalizing to an IPAM API as an
explicit non-goal, and `loadBalancerIP` was deprecated with no replacement. There is
nothing to wait for.

**Do nothing.** **Rejected:** the capability simply does not exist today — an address
cannot be held, moved, quota'd, or enumerated — and the pin-annotation theft window in
*Security* is live in any cluster running a shared auto-assign pool. Doing nothing leaves
both the missing capability and the open security hole in place.
