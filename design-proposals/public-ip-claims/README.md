# Public IPs as a first-class resource: `PublicIP`, `PublicIPClaim`, `PublicIPClass`

- **Title:** `Public IPs as a first-class resource: PublicIP, PublicIPClaim, PublicIPClass, and a provisioner contract`
- **Author(s):** `@lllamnyp`
- **Date:** `2026-07-14`
- **Status:** Draft

> **This is a stub.** The problem statement, the primitives survey, and the object
> model are worked out; the controller mechanics, the provisioner contract details,
> and the rollout are deliberately sketched. It is opened early to settle the
> **shape** — is an address a resource? — before anyone writes code.

## Overview

A Cozystack tenant cannot *own* a public address. They can only cause one to appear
as a side effect of creating a `Service type: LoadBalancer`, and it evaporates when
that Service does. There is no object to hold, keep, quota, bill, hand to another
workload, or point DNS at with confidence.

This proposal makes the address a resource. Applying the `PersistentVolume` pattern
to addresses:

| storage | addresses | role |
|---|---|---|
| `StorageClass` | **`PublicIPClass`** | which pool, which provisioner, which announcer |
| `PersistentVolume` | **`PublicIP`** (cluster-scoped) | the address itself, with a `claimRef` and a reclaim policy |
| `PersistentVolumeClaim` | **`PublicIPClaim`** (namespaced) | "give me one" — what a tenant creates |
| CSI driver | **a provisioner contract** | how a class's backend allocates, adopts, and pins |

The experience we are after is the AWS one: **allocate an address, keep it, attach it
to something, detach it, attach it to something else, release it when done.** Not:
"create a Service and hope you get the same IP back."

## Scope and related proposals

- **Adjacent, not a dependency:** `design-proposals/structured-external-exposure`
  (community #29) restructures how a managed application *requests* exposure. This
  proposal is about what an address **is**. The two meet at one point — an exposure
  entry should be able to name a `PublicIPClaim` instead of implicitly minting an
  address — but neither needs the other to land first, and this proposal does not
  assume #29's shape.
- **Related:** an `ExposureClass` kind exists today in `network.cozystack.io`. This
  proposal takes a position on it (see *Alternatives*): the **class** idea is right
  and should survive in some form; binding an address's lifetime to a `Service` is
  the part that cannot serve this use case.
- **Deferred to a sibling:** the *datapath* for whole-IP 1:1 NAT (all ports in, and
  the workload egressing **as** that address) — today provided by
  [cozy-proxy](https://github.com/cozystack/cozy-proxy)'s nftables rules. That is a
  CNI concern, tracked separately. **This proposal is the allocation half; that is
  the forwarding half.** They are independent: allocation is useful with a stock
  LoadBalancer Service and no 1:1 NAT at all.

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
  public addresses a tenant consumes. The only lever is pre-carving a pool per tenant.
- **It cannot be gated.** Anyone who can create a `Service` in a namespace can cause a
  public address to be allocated. RBAC authorizes *verbs on resources*, not *fields*.
- **It cannot be shown.** No inventory. "Which addresses do we own, and who has them?"
  has no answer short of listing every Service in the cluster.

### This is a regression, not a wishlist

Cozystack has reserve-then-associate **today**, through kube-ovn: `OvnEip` is a
cluster-scoped object created independently, then bound by an `OvnFip` (1:1 NAT),
`OvnSnatRule`, or `OvnDnatRule` that references it by name. The legacy iptables
gateway mode has the same shape (`IptablesEIP` + `IptablesFIPRule`).

**Migrating off kube-ovn drops this capability on the floor** unless something
replaces it. That reframes the proposal: the question is not "should Cozystack gain an
AWS-style EIP", it is "on what terms does Cozystack keep the EIP it already has."

## Goals

- A tenant can request an address and receive one, without an admin pre-creating a
  pool object for them.
- An address **survives** the deletion of the workload it was attached to (reclaim
  policy `Retain`), and can be attached to a different workload afterwards.
- An operator can enumerate every public address the cluster owns, and see who holds it.
- The number of addresses a tenant may hold is bounded by a **plain `ResourceQuota`**
  (`count/publicipclaims.<group>`), with no new quota machinery.
- An admin configures the address source **once per class**, not once per tenant and
  not once per address. No pool-per-tenant, no `/32` pools.
- The tenant-facing API does not name the backend. A tenant asks for an address; it
  does not learn whether MetalLB, Cilium, or a cloud API produced it.
- The design admits at least: MetalLB, Cilium LB-IPAM, and a cloud provider whose
  addresses are allocated outside the cluster.

### Non-goals

- **Announcing addresses.** Attraction (ARP/NDP, BGP, a cloud VNIC assignment) stays
  with the LB implementation. This proposal allocates and binds; it never puts a
  packet on a wire.
- **The 1:1 NAT datapath.** See *Scope*.
- **Replacing the LB implementation.** MetalLB/Cilium/cloud stay exactly where they
  are; this sits above them.
- **IPv4-only thinking.** The model is family-agnostic; a claim may request v4, v6, or
  both. (Whether one `PublicIP` carries both families or a claim binds two is an open
  question below.)
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
apiVersion: network.cozystack.io/v1alpha1
kind: PublicIPClass
metadata:
  name: public
  annotations:
    publicipclass.network.cozystack.io/is-default-class: "true"
spec:
  provisioner: metallb.cozystack.io      # who fulfils claims of this class
  reclaimPolicy: Retain                  # Retain | Delete   (default Retain)
  parameters:                            # opaque to the core controller
    addresses: ["203.0.113.0/24"]
---
# Tenant. This is the whole tenant-facing API.
apiVersion: network.cozystack.io/v1alpha1
kind: PublicIPClaim
metadata: {name: web, namespace: tenant-a}
spec:
  className: public          # empty => the default class
  family: IPv4               # IPv4 | IPv6 | Dual
status:
  phase: Bound
  publicIPName: pip-203-0-113-7
  address: 203.0.113.7       # what the tenant reads, and puts in DNS
---
# Cluster-scoped. The inventory. Created by the provisioner, not the tenant.
apiVersion: network.cozystack.io/v1alpha1
kind: PublicIP
metadata: {name: pip-203-0-113-7}
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

### 3. The address-source union — the reason this is not a MetalLB adapter

There are two structurally different worlds, and the model must admit both:

- **Self-allocating backends** (MetalLB, Cilium, kube-vip, OpenELB): *nothing anywhere*
  holds a reservation. **Our provisioner is the IPAM of record.** `PublicIP` is
  authoritative, and its address is carved from the class's range (`source.fromClass`).
- **Provider-reservation backends** (AWS, GCP, Hetzner): the reservation is already a
  real object with a stable handle (`eipalloc-…`, a named static address), guarded by
  the provider's own IAM. **Our provisioner must adopt, not allocate**
  (`source.providerRef`).

This is precisely the `PersistentVolume` volume-source union (`spec.csi` /
`spec.nfs` / …), and it is the strongest evidence the shape is not bent around any one
backend: it survives a backend where allocation is not ours at all. The tenant's
`PublicIPClaim` never learns the difference.

### 4. The provisioner contract (the "CSI analogue")

`PublicIPClass.spec.provisioner` names a controller, exactly as `StorageClass` names a
CSI driver. Each provisioner declares **capabilities**, and the core controller refuses
what a backend cannot do rather than half-supporting it:

| capability | meaning | consequence if absent |
|---|---|---|
| `Allocate` | can carve an address from a class range | class must use `providerRef` adoption |
| `Adopt` | can wrap a provider-side reservation | `providerRef` claims rejected |
| **`Pin`** | **can bind a specific address to a Service** | **the class cannot serve claims at all** |

`Pin` is not optional decoration — **it is the capability the entire model depends on.**
A backend that cannot be told *which* address to use can never attach a reserved one,
and a class over such a backend must reject `PublicIPClaim`s outright instead of
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
    network.cozystack.io/public-ip-claim: web    # a claim in THIS namespace
spec: {type: LoadBalancer, ...}
```

The controller resolves the claim → address, verifies the claim is in the Service's own
namespace, and writes the backend's pin annotation (§1.2). MetalLB/Cilium/the cloud then
allocates and announces exactly that address.

Remove the annotation and the announcement is withdrawn, but the `PublicIP` stays
`Bound` to its claim and simply becomes inert — **a reserved address, held, attached to
nothing.** Which is what an unassociated EIP is.

### 6. What an admin actually configures

For MetalLB, forever, for the whole cluster:

- **one** `IPAddressPool` covering the range, and
- **one** `L2Advertisement` (or `BGPAdvertisement`) selecting it.

That is all. No pool per tenant, no `/32`s. The `PublicIPClass` carries the range; the
provisioner renders the backend objects.

## User-facing changes

- **Tenants** gain one kind: `PublicIPClaim`. Create it, read `status.address`, put that
  in DNS, reference it by name from a Service. They never see `PublicIP`,
  `PublicIPClass`, MetalLB, or a pin annotation.
- **Admins** gain `PublicIPClass` (one per address source) and `PublicIP` (a read-mostly
  inventory: `kubectl get publicips` finally answers "what do we own and who has it").
- **Quota** works with no new machinery: `count/publicipclaims.network.cozystack.io` in a
  stock `ResourceQuota`.

## Upgrade and rollback compatibility

- **Additive.** Nothing about today's `Service type: LoadBalancer` path changes; a
  Service with no claim annotation behaves exactly as now.
- **Adoption path.** A cluster with existing LoadBalancer Services can import their
  addresses as `PublicIP` objects with `source.providerRef`/`fromClass` and a `claimRef`
  to a generated claim — an offline, reversible migration.
- **kube-ovn.** `OvnEip` holders need a migration story. Sketch only: enumerate `OvnEip`s,
  mint an equivalent `PublicIP` + `PublicIPClaim` per address, re-point the association.
  **Not designed here.**
- **Rollback.** Deleting the CRDs with `reclaimPolicy: Retain` leaves the backend objects
  and the live Services untouched; addresses stay where they are. With `Delete`, it does
  not — flagged as the irreversible case.

## Security

**The pin annotation is a privilege-escalation surface, and this is the sharp edge of
the proposal.**

RBAC authorizes *verbs on resources*, not *fields*. Today, anyone who can create a
`Service` in a namespace can write `metallb.io/loadBalancerIPs: <any address>` — and
because a self-allocating backend has **no concept of a reservation**, it will happily
hand over an address that another tenant has reserved but not yet attached. That is a
theft window, and it exists on MetalLB and Cilium alike. It is *not* a cloud problem:
there, IAM already guards `eipalloc-…`.

The fix is the same move Cozystack already makes elsewhere — **turn the ungatable field
into a reference to an RBAC-gated object**:

1. A tenant may only write `network.cozystack.io/public-ip-claim`, naming an object in
   their **own namespace**, which RBAC *can* gate.
2. The **controller** writes the backend's raw pin annotation.
3. A `ValidatingAdmissionPolicy` **rejects any principal but the controller** writing
   *any* backend pin annotation (§1.2's list) on a Service.

Without (3) the whole model is advisory. It should land in the same release as the CRDs,
not after.

Note this does not, by itself, close field-level authorization in general — a tenant who
can create a `PublicIPClaim` can still consume an address. That is what the class and the
`ResourceQuota` are for, and it is a bound, not a gate.

## Failure and edge cases

- **Class range exhausted** → claim stays `Pending` with a reason; no partial binding.
- **Claim deleted, `reclaimPolicy: Retain`** → `PublicIP` goes `Released`, keeps the
  address, is not reusable until an admin clears the `claimRef`. (PV semantics, deliberately.)
- **Claim deleted, `Delete`** → address returned to the range; the backend object is torn down.
- **Service references a claim in another namespace** → rejected. Cross-namespace address
  sharing is not a thing.
- **Two Services reference one claim** → the second is rejected. A 1:1 binding is 1:1;
  silently letting the second win is how an address goes quietly dead.
- **Backend lacks `Pin`** → the class rejects claims at admission, loudly, rather than
  allocating an address that can never be attached.
- **Address adopted from a provider, then released provider-side** → `PublicIP` goes
  `Lost`. It must not silently re-allocate.

## Testing

- **Unit:** the allocator (range carving, exhaustion, reclaim transitions, the source union).
- **Integration:** claim → bind → associate → *delete the Service* → assert the address is
  still held → associate it to a **different** workload → assert the same address comes back.
  **That single test is the proposal.** It is precisely what cannot pass today.
- **e2e (per backend):** MetalLB and Cilium, on a real cluster: an external client reaches
  the workload on the reserved address, and still reaches it after the workload is
  rebuilt behind the same claim.
- **Admission:** a tenant writing a raw pin annotation is rejected; a tenant reserving an
  address and a second tenant attempting to pin it directly is rejected.

## Rollout

Sketch — sequencing is an open question:

1. CRDs + core controller + the **MetalLB** provisioner + the admission policy. (The
   admission policy is not phase 2. See *Security*.)
2. The Cilium provisioner. Proves the abstraction is not a MetalLB adapter — **this is
   the phase that either validates or falsifies the design**, and it should come early.
3. A cloud provisioner (adoption path, `source.providerRef`). Proves the source union.
4. kube-ovn `OvnEip` migration.

## Open questions

1. **Is this Cozystack's job at all?** It is not a CNI concern (a CNI consumes an address
   that lands on a node; it has no business owning the inventory). It is not upstream's
   (KEP-1880 says so explicitly). Platform seems right — but it is worth asking whether
   this belongs in a standalone project rather than in Cozystack core.
2. **Provisioners: in-tree or out-of-tree?** In-tree for three backends; CSI's history
   says that stops scaling. Where is the line, and do we want the CRD contract on day one?
3. **Reuse Cluster API's IPAM?** `ipam.cluster.x-k8s.io` `IPAddressClaim`/`IPAddress` is
   *literally* this pattern — namespaced claim, concrete address object, a documented
   third-party provider contract. It has only ever been wired to **Machine** addressing,
   never Services. Do we adopt those kinds, or mint our own and merely copy the contract?
   (Leaning: our own — we need reclaim policy and association, which it has no concept of.
   But the shape is not novel and review should know that.)
4. **`ExposureClass`.** A class kind already exists in `network.cozystack.io`. Is
   `PublicIPClass` a second class kind, or the same one grown a provisioner? Answering
   this depends on the fate of `ExposureClass`/`ServiceExposure`, which is being
   re-examined independently.
5. **Dual-stack.** Does one `PublicIP` carry a v4 and a v6 address, or does a `Dual` claim
   bind two `PublicIP`s? (PV has no precedent. Leaning: two objects, one claim.)
6. **PureLB.** Does it have *any* "request this exact IP" mechanism? If not, it cannot be a
   backend, and that should be stated rather than discovered.
7. **Sharing one address across Services** (different ports — GCP and Cilium both permit
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

**Do nothing.** **Rejected:** it is a regression. Cozystack has reserve-then-associate today
via kube-ovn's `OvnEip`, and the migration off kube-ovn removes it.
