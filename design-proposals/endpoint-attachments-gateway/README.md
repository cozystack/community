# `EndpointAttachment` through the tenant Gateway: the `gateway` member

- **Title:** `EndpointAttachment through the tenant Gateway: the gateway member`
- **Author(s):** `@lllamnyp`

## Overview

[`endpoint-attachments`](../endpoint-attachments/README.md) (community#45) defines `EndpointAttachment` (`local.sdn.cozystack.io/v1alpha1`) with one mechanism member, `loadBalancer`, which gives one endpoint of one application a dedicated address. It reserves the member name `gateway`. This proposal specifies that member.

**`gateway`** is a **shared-Gateway route**. The attachment binds the endpoint to a hostname on a shared tenant Gateway by referencing one of two **app-shipped** artifacts: the app's `Service` (the L4 case — the controller synthesizes a passthrough `TLSRoute` for SNI, or a `TCPRoute` for a raw port) or the app's own `HTTPRoute` (the L7 case, carrying the app's path topology). It does no L7 routing itself — paths, methods, and header matches stay in that `HTTPRoute`. This is the tenant-facing surface the accepted SNI-consolidation design (external-database-exposure, community #20) was waiting on — the synthesized `TLSRoute` *is* #20's data plane — generalized past databases and reconciled with the platform's existing legacy path, the tenant-root ingress controller.

Both members share everything above the data plane: the resolve step and its authorization, the lifecycle, the status shape. *Changes to the base API* lists what this member adds to the resource and what it changes.

## Scope and related proposals

- **Extends** community#45 (`endpoint-attachments`). Everything this proposal does not restate — resolve, lifecycle, garbage collection, conditions, RBAC, quota — is #45's and applies to both members unchanged.
- **Supplies the tenant-facing surface for** community #20 (external database exposure via Gateway API TLS-passthrough, **Accepted**). #20 collapses a tenant's fitting databases — same-engine instances included — onto the tenant Gateway's single LoadBalancer address, routed by SNI, and specifies that data plane fully (shared per-engine passthrough listeners, per-release `TLSRoute`s, SAN injection, end-to-end TLS). What #20 deliberately left to a structured-exposure model — the tenant-facing *trigger*, originally expected from #29 — is the `gateway` member designed here. This proposal owns the route resource, its authorization, and its lifecycle; #20 remains the authority on the database passthrough data plane and the TLS/SAN story the `gateway` member drives. This **largely resolves** #20's ownerless-trigger gap and generalizes the same shape past databases. See *Routes: the `gateway` member*.
- **References** the unified-TLS effort (community #19, cozystack #2811) for the PKI half of the route story: certificate issuance and TLS *termination* stay there, so the `gateway` member ships passthrough (SNI) first (for databases, the SNI hostname scheme itself is #20's, already decided).
- **Built on** Gateway API: the `gateway` member binds an app `Service` (synthesizing a passthrough `TLSRoute`/`TCPRoute`) or an app `HTTPRoute` onto a tenant Gateway — binding, not an L7 route table of its own (see *Routes: the `gateway` member*).

## Decisions

None yet.

## Context

#45 publishes an endpoint on a dedicated address. A dedicated address is the wrong tool for SNI consolidation, the managed-Kubernetes API and HTTP endpoints: it costs an address per endpoint and gives no hostname. #20 specifies the database passthrough data plane on the tenant Gateway, and its trigger was expected from #29's structured-exposure model, which closed in favour of #45. One route case is already live: the managed-Kubernetes chart publishes the API server through an `Ingress` with `ssl-passthrough` on the tenant-root ingress controller (*Legacy mode*).

## Goals

- A tenant can publish an endpoint **by hostname** through a shared Gateway — binding the app's `Service` (the controller synthesizing a passthrough `TLSRoute`/`TCPRoute`) or the app's own `HTTPRoute`, never a path rule of the platform's own — when a dedicated address is the wrong tool (SNI consolidation, the managed-Kubernetes API, HTTP endpoints), through the same one kind.
- The `gateway` identifier (`hostname`, `protocol`) is immutable under a live attachment.
- A route shares #45's resolve step, lifecycle, garbage collection and status shape.

### Non-goals

- **A hostname *reservation* resource.** The `gateway` member binds a hostname, but a hostname is **not** a platform-allocated resource the way an address is: the tenant brings it, already reserved at a registrar. There is no allocation event for the platform to model, so there is no reservable/quota'd/movable hostname object paralleling #35's `IPAddressClaim` — the "move" is a DNS re-point the tenant performs outside the platform. *Routes: the `gateway` member* makes this allocation asymmetry explicit.
- **TLS termination and certificate issuance for routes.** Owned by the unified-TLS effort (community #19, cozystack #2811). The `gateway` member ships **passthrough only** (SNI for TLS, plain `HTTPRoute` for HTTP); termination with a platform-issued certificate waits on that effort. For databases the SNI hostname scheme and the end-to-end-TLS/SAN story are #20's, already decided and consumed unchanged.
- **DNS record publication.** Pointing a hostname at a Gateway address is external-dns's or the tenant's job; the controller reports the hostname, it does not create DNS records.
- **The platform-Gateway-with-per-tenant-listeners variant.** The default is a Gateway per tenant (*Routes: the `gateway` member*). A single platform Gateway carrying per-tenant listeners is left open as a future possibility — plausibly useful in private clouds — but its hostname-isolation admission guard is sketched, not designed, here.
- **Sub-hostname L7 routing (paths, methods, header matching).** The `gateway` member binds a whole hostname/SNI/port to a backend; it renders no path or header rules. Routing *within* a hostname is the application's own `HTTPRoute`, authored where the topology is known. See *Alternatives*.
- **Gateway-routing virtual machines (and tenant-ordered LoadBalancers).** A VM's public IP sits on its interface (1:1 via cozy-proxy); a shared gateway only makes that harder, and a `Service type: LoadBalancer` a tenant orders *inside* their own managed Kubernetes cluster is reconciled by kubevirt-cloud-provider, not here. VMs stay on the `loadBalancer` (attach-public-IP) path; putting a VM or a tenant-cluster LoadBalancer behind the `gateway` member is deferred (a kubevirt-owned gateway controller is a someday option, not this design).

## Design

### Changes to the base API

The `gateway` member adds to #45's resource and changes it in these places:

| Field or rule | In #45 | With this proposal |
|---|---|---|
| Mechanism union | `[has(self.loadBalancer)]`, exactly one | `[has(self.loadBalancer), has(self.gateway)]`, exactly one; the set member stays immutable |
| `spec.gateway` | reserved name | `hostname`, `protocol` (`TLSPassthrough` \| `TCP` \| `HTTP`), optional `gatewayRef`; `hostname` and `protocol` immutable |
| `spec.endpoint` | `serviceName`, required | exactly one of `serviceName` or `routeName`; `routeName` only with `gateway` |
| `status` | `serviceName`, `claimName`, `addresses` | adds `routeName` and `url` for the `gateway` member |
| `Provisioned` | claim bound, Service carries its addresses | for `gateway`: the route is accepted by the Gateway |
| Resolve | Services only | also `HTTPRoute`s, discovered by lineage labels the webhook stamps on route kinds |
| Per-chart work | none | HTTP exposure needs the chart to ship an `HTTPRoute` |

`serviceName` becoming optional is the one change to an existing field. Attachments created under #45 all set it and stay valid.

### The resource

```yaml
apiVersion: local.sdn.cozystack.io/v1alpha1
kind: EndpointAttachment
metadata:
  name: mydb-ro-public
  namespace: tenant-a
spec:
  applicationRef:            # immutable; the application, in this namespace
    group: apps.cozystack.io # optional; defaults to apps.cozystack.io
    kind: Postgres
    name: mydb
  endpoint:
    serviceName: postgres-mydb-ro     # bind a tenant-facing Service (L4; controller synthesizes TLSRoute/TCPRoute)
    # routeName: myapp-routes         # ...or bind the app's own HTTPRoute-set (L7); gateway member only
  gateway:
    hostname: mydb-ro.tenant-a.example.org   # tenant-supplied; NOT reserved by the platform
    protocol: TLSPassthrough                 # TLSPassthrough | TCP (bind the Service) | HTTP (bind the app's HTTPRoute)
    # gatewayRef: {name: ...}                # optional; defaults to the tenant's own Gateway
    #                                        #   (an exposure-class vocabulary is the pending direction — see Open questions)
status:
  phase: Attached            # Pending | Attached | Detached, as in #45
  routeName: mydb-ro-public-x7ktq          # the rendered route (TLSRoute/TCPRoute/HTTPRoute)
  url: tls://mydb-ro.tenant-a.example.org:5432  # what to connect to, instead of addresses
  conditions:
    - type: Resolved
    - type: Provisioned      # route accepted by the Gateway
```

`spec.endpoint` names the app-shipped artifact to bind: `serviceName` for a `Service` (the L4 case, and what `loadBalancer` always binds) or `routeName` for the app's own `HTTPRoute`-set (the `gateway` member's L7 case; see *Routes: the `gateway` member*). `serviceName` is first-class today and `routeName` support grows as charts ship routes (*Open questions*).

The `gateway` identifier (`hostname`, `protocol`) is frozen by CEL: any change could re-point the hostname a customer has baked into a connection string. Which data plane an attachment uses is likewise fixed; switching it is delete-and-recreate.

### Route render

When the mechanism is `gateway`, #45's render step produces a Gateway API route instead of a Service, under the same controller-ownerReference identity rule. For an L4 endpoint (bound by `serviceName`) the controller **synthesizes** a passthrough `TLSRoute` (SNI) or `TCPRoute` (port) with `spec.hostnames` set to the attachment's `hostname`, `parentRefs` attaching to the tenant Gateway's listener, and `backendRefs` the resolved Service (cross-namespace via `ReferenceGrant` where the Gateway is not in the tenant namespace). For an L7 endpoint (bound by `routeName`) it attaches the app's **own** `HTTPRoute`-set to the listener and synthesizes nothing — the app owns that route's path topology. No address is minted and no `loadBalancerClass` applies — #45's claim step is skipped — and `Provisioned` reflects the route's acceptance by the Gateway. For a database endpoint the synthesized `TLSRoute` is exactly #20's per-release route onto its shared per-engine listener; where the live data plane is the tenant-root ingress controller, the controller emits the legacy passthrough `Ingress` form instead (*Legacy mode*). Resolve, report, and GC are otherwise identical to the `loadBalancer` path.

### Routes: the `gateway` member

The `loadBalancer` member gives an endpoint its own address; the `gateway` member binds it to a hostname on a shared Gateway. Both rest on one principle, and it is the line this proposal holds against re-implementing an L7 router:

**The platform owns the external *binding* at every layer; the application owns its intrinsic *routing topology*, which only L7 has.** An address, an SNI, a port, a hostname, a listener are bindings — operational, per-instance, per-tenant, known to the platform and not to the chart. HTTP paths, methods, header matches, rewrites are topology — intrinsic to the application, known only to it. L4 (TCP/TLS) has no topology at all, so it collapses entirely to binding; L7 has topology, authored in the app's own `HTTPRoute`, while the hostname and listener under it stay binding.

The `gateway` member therefore does **no L7 routing**. It binds an entry point to one backend and never expresses paths, methods, or header matches; those are the app's `HTTPRoute`. It renders **no** `type: LoadBalancer` Service and mints **no** address — the Gateway already has one — and shares everything above the data plane with `loadBalancer`: the same resolve step and tenant-facing authorization (#45, *What the controller does*, step 1), the same `Detached`-not-deleted lifecycle, the same GC, the same `metav1.Condition` status, differing only in that `status` reports a `url`/`routeName` instead of `addresses`.

**Two bind targets, both app-shipped.** The attachment references the application and binds one of exactly two artifacts the app already ships, discovered by the lineage labels (below):

- **A `Service`** — the **L4 / sufficient contract**. Every reachable app has one, chart-owned or operator-created, already lineage-labelled. A `Service` says "there is a TCP/UDP socket on this port," which for a plain L4 app (Postgres, Redis, a TCP service) is the whole contract. From it the controller **synthesizes** the route the binding needs — a passthrough `TLSRoute` that routes a TLS stream to the backend by its **SNI hostname** without terminating (this *is* #20's per-release route, and it is what lets many TLS backends share one address disambiguated by name, which a `type: LoadBalancer` Service cannot do), or — low priority — a `TCPRoute` putting a raw-TCP service on a shared gateway port (a possible simpler alternative to the `lbipam.cilium.io/sharing-key` path, not a near-term need). `TLSRoute` and `TCPRoute` are **never** bind targets: an app gains nothing by shipping them, since their only inputs are the backend (the `Service`) and, for TLS, the SNI hostname (a binding the attachment supplies).
- **An `HTTPRoute`** — the **L7 case**, and the only route type an app legitimately ships. It carries the app's path/method topology and its cross-route hostname-consistency contract — things a `Service` cannot express and the platform cannot infer. The attachment binds the app's `HTTPRoute`(s); the controller synthesizes nothing.

Services are first-class now; app-shipped `HTTPRoute` support expands gradually as charts grow one (*Open questions*). Where an app ships only a `Service`, the member offers the L4 bindings; HTTP exposure waits on the app declaring its `HTTPRoute`.

**The L7 multi-route case — the constraint that keeps an app whole.** A plain L4 app's `Service` is a sufficient contract; a multi-route L7 app is the hard one. If a tenant could bind one of its routes to one listener and another to another, they could land the frontend on one hostname and the backend on another and **break the app** — frontend and backend must share a hostname for cookies and auth. Resolution: **the app declares the traffic it accepts through the routes it ships, and an app that must know its own hostname declares that hostname in its spec**, which templates a *consistent set* of `HTTPRoute`s (backend `/api`, frontend `/`, on one hostname). The attachment binds **the route-set, not the individual routes**, and the controller permits attachment only on the hostnames those routes already declare — so the attachment supplies hostname, listener, and gateway binding, and can never split the app across hosts. The route-set carries the consistency contract; path/method/header routing stays entirely inside it. Restated: *the app declares how it can be reached; the attachment declares where and whether to expose it.*

**Discovery, listeners, and lineage.** Listeners, Gateways, and `GatewayClass`es are admin/platform-owned and live outside the application — the app never has power over a listener. The lineage webhook is a provenance labeler only: extending its labelled-kinds list to route objects makes an app's `HTTPRoute`s discoverable by the same group/kind/name labels as its Services, so an attachment finds both alike; the webhook never creates or places a route. Binding leans on the Gateway API capability where a route need not name its listener (the listener selects the route, and one route may be attached by several listeners), giving the attachment top-down binding without the app's route hard-coding a listener — an **experimental** Gateway API feature this depends on (*Open questions*). Patching labels onto app-owned objects is safe under GitOps: Flux and Argo both use server-side apply with per-field ownership, so a reconciler does not revert a field a controller owns.

The worked case is **ClickHouse**: its chart renders an Altinity `ClickHouseInstallation`, the operator materializes the `Service` from an inline `serviceTemplate` (ClusterIP, ports 8123/9000), and `values.yaml` carries **no `external` key at all** — no exposure knob, no route. The operator's `Service` still carries the instance and lineage labels, so an attachment discovers and binds it even though the chart ships no exposure field and renders no route. That is why discovery-by-label beats a chart-authored exposure field.

**The union discriminates the data plane, and each member brings its own identity resource.** `loadBalancer` brings an `IPAddressClaim`. The `gateway` member's identity is a **hostname plus the Gateway's own address** — and only the latter is a platform-allocated resource (an `IPAddress`/`IPAddressClaim` under #35, typically a system-owned attachment on the tenant Gateway). The hostname is **not** such a resource, and the asymmetry is the point:

- **A hostname is registrar-allocated, not platform-allocated.** #35 exists because the platform hands out addresses from a finite pool it controls, so it needs an object to hold, quota, and arbitrate. The platform does not allocate hostnames — the tenant brings one it already owns through DNS. There is no allocation event, so there is nothing to reserve: no hostname object, no `count/` quota on names, no hold/move resource. The "move" is a DNS re-point the tenant performs at their registrar; the attachment just stops naming the old hostname and starts naming the new one.
- **Conflicts resolve without a platform arbiter.** Two attachments claiming the same hostname on one tenant's Gateway is Gateway API's own deterministic conflict resolution — oldest `creationTimestamp` wins, the loser reports its conflict in status — a tenant colliding with itself, surfaced not silently resolved. Across tenants it does not arise: tenants do not share a Gateway (below), and the ultimate arbiter of which address a hostname resolves to is DNS, which its true owner controls.

**Which endpoints can take a route is a property of the endpoint, not the attachment.** A route needs the endpoint's protocol-per-port: `HTTP` binds the app's own `HTTPRoute`, direct-TLS-with-SNI a synthesized passthrough `TLSRoute`, plain TCP a synthesized `TCPRoute`. Validation refuses the mismatches rather than rendering a route that cannot work — `HTTPRoute` to raw-protocol Postgres; passthrough `TLSRoute` to a server that negotiates TLS in-band (pre-17 Postgres STARTTLS-style, MariaDB server-speaks-first); any hostname route to a discovery engine whose clients need per-broker addresses (Kafka). The clean fits are the managed-Kubernetes API (the live case, below), HTTP dashboards, direct-TLS PostgreSQL 17+ (`sslnegotiation=direct`), Redis and sharded `mongos` with SNI. This is why endpoints need a **protocol-aware vocabulary** (per-port protocol declared in the `ApplicationDefinition`); until it exists, the `gateway` member is restricted to endpoints whose protocol the controller can determine — see *Future work* and *Open questions*.

**Relationship to #20 (the accepted SNI-consolidation design).** For databases, #20 is the data plane this member drives, not a competitor. #20 fully specifies the database passthrough mechanics — shared per-engine listeners on native ports, per-release `TLSRoute`s, SAN injection, end-to-end TLS from the operator certificate — but declared its tenant-facing *trigger* out of scope, expecting it from #29's structured-exposure model. #45 succeeded #29, so that trigger lands here: a `gateway` attachment on a database endpoint synthesizes the per-release `TLSRoute` onto #20's shared listener. This proposal owns the resource, the authorization, and the lifecycle; #20 remains the authority on the passthrough mechanics and the TLS/SAN story. That the same member also serves non-database routes (the K8s API, HTTP endpoints) is the generalization #20's database-only scope could not make. SNI exposure for databases therefore still depends, per engine, on #20's unified-TLS convergence (the operator certificate must carry the external SAN); the K8s-API and HTTP cases do not, so they can ship first.

**Which Gateway — one per tenant, by default.** The route attaches to the **tenant's own Gateway** (the existing `TenantGateway` model, inherited like the tenant's ingress). That single assumption does all the cross-tenant isolation work: tenants never share a listener, so a tenant can only publish hostnames onto their own Gateway, and DNS arbitrates the rest. A single **platform** Gateway carrying per-tenant listeners is a coherent alternative — plausibly wanted in private clouds — but it is left open, not designed: if adopted, it needs one admission guard (a tenant may write only hostnames under its own delegated apex), which the per-tenant default makes unnecessary. The Gateway's own address is itself an `IPAddress` behind a system-owned attachment, so "where the Gateway's address comes from" has the same answer as everything else here.

### Legacy mode: the tenant-root ingress controller

One route case is already live and predates this design: the managed-Kubernetes chart publishes the API server through an `Ingress` with `ssl-passthrough` on the tenant-root ingress controller, at host `kubernetes-<name>.<tenant>.<apex>` — a passthrough `TLSRoute` in `Ingress` clothing, on a platform-wide controller rather than a per-tenant Gateway. The platform generates that hostname from the release name and the tenant apex, so it avoids hostname conflicts itself, without the Gateway API machinery above. This proposal supports that path **to a bounded extent**, so "how is this application reachable" has one answer rather than two vocabularies:

- Where the data plane is the tenant-root ingress controller rather than a Gateway, the `gateway` member may render the legacy `Ingress` form (passthrough) instead of a `TLSRoute`, as a transitional rendering of the same attachment — same resource, same authorization, same status.
- Chart-rendered exposures not created through an attachment (the Kamaji `Ingress`, `external: true` Services) are **reported read-only** alongside an application's attachments, so an enumeration of "reachable from outside" is complete, and are migrated to attachments on the same address- (and name-) preserving path as `external` (#45, *What this replaces*).

Legacy support is deliberately bounded: it is a compatibility and reporting surface, not a second first-class route API. New route exposure targets a Gateway.

### The managed Kubernetes API

The clean `gateway`-member case, and already live via legacy — the chart publishes it through a passthrough `Ingress` at `kubernetes-<name>.<tenant>.<apex>`, which the `gateway` member expresses directly (passthrough `TLSRoute`, or the legacy `Ingress` rendering). Because the client reaches it by that hostname, which the API server's certificate already covers, there is no SAN write-back to do — the route path sidesteps the dedicated-address complication entirely (#45, *Engines with in-protocol discovery*).

### Future work (non-binding sketches)

- **A protocol-aware named endpoint vocabulary.** #45's named-endpoint sketch, with a **protocol per port** (plain TCP/UDP, HTTP, TLS-with-SNI): the chart can say which endpoints can take a route versus only an address. The `gateway` member needs exactly this to validate route-vs-address fit per endpoint (*Routes: the `gateway` member*); it is also the vocabulary a portal/dashboard "how do I connect" surface reads.

## User-facing changes

- `EndpointAttachment` gains a second data plane: a shared-Gateway route (`gateway`, a tenant-owned hostname; read the URL from `status`), beside #45's dedicated address.
- HTTP exposure needs the application's chart to ship an `HTTPRoute`.
- Nothing changes for attachments that use `loadBalancer`.

## Upgrade and rollback compatibility

Additive over #45: a new union member, a new optional `endpoint` field, and new status fields. Existing attachments stay valid. Rollback removes the member; rendered routes are owned by attachments and are removed with them.

## Security

- **Routes carry no new cross-tenant surface.** The `gateway` member reuses #45's tenant-facing-endpoint check for *what* may be published; for *under which hostname*, the per-tenant Gateway is the boundary — a tenant writes routes only onto its own Gateway's listeners, so it cannot bind a hostname in front of another tenant's traffic, and which address a hostname resolves to is DNS, controlled by the hostname's registrant. The platform does not adjudicate name ownership (it cannot — the registrar already did). The one place a guard is needed is the open platform-Gateway-with-per-tenant-listeners variant (*Non-goals*): there, an admission policy restricting a tenant to hostnames under its delegated apex; the per-tenant default needs none. Passthrough routes never terminate TLS, so no private key is held at the edge; terminating routes wait on the unified-TLS effort, which owns that key custody.
- The controller writes only objects it owns (synthesized routes) plus ownerReferences and lineage labels on attachments; it binds — references — an app's own `HTTPRoute` without mutating it. (The lineage webhook separately labels app objects, including routes, for discovery; under GitOps that patch is safe because Flux and Argo use server-side apply with per-field ownership, so a reconciler does not revert a controller-owned field.)

## Failure and edge cases

- **`gateway` route whose `protocol` does not fit the endpoint** (`HTTPRoute` to a raw-protocol database, passthrough `TLSRoute` to an in-band-TLS server, any route to a discovery engine) → refused with `Resolved=False`/`Provisioned=False` and a reason; no route that cannot carry traffic is rendered. Determining fit needs the protocol-aware endpoint vocabulary (*Future work*); absent it, the member is limited to endpoints of known protocol.
- **Two `gateway` attachments claiming one hostname on one tenant Gateway** → Gateway API's deterministic resolution applies (oldest `creationTimestamp` wins); the loser reports its conflict in `status`. Not a platform-arbitrated reservation — a tenant colliding with itself, surfaced.
- **An HTTP `gateway` attachment asked to route by path or header** → not expressible: the member binds the whole hostname, and sub-hostname routing belongs to an app `HTTPRoute` (*Alternatives*).
- **A `gateway` route to an endpoint that later disappears** → same as the address path: phase `Detached`, the route torn down, nothing minted to keep; reattaches on return.
- **A `gateway` identifier field is mutated** (`hostname`, `protocol`) → rejected at admission by CEL.

## Testing

- **e2e (`gateway` member, managed Kubernetes — the flagship route case):** attach the API endpoint with `protocol: TLSPassthrough` and a tenant hostname → an external client reaches the API over TLS at that hostname via the tenant Gateway → delete the attachment → the route is withdrawn, the application untouched. A `gateway` attachment on a database endpoint renders #20's per-release `TLSRoute` onto the shared listener (asserted at the route level). Protocol-mismatch refusals (`HTTPRoute` to Postgres, passthrough `TLSRoute` to an in-band-TLS server, any route to Kafka) are unit-tested.
- **GC:** delete the application → rendered routes gone with the attachments.

## Rollout

1. The **`gateway`** member: bind an app `Service` (synthesizing a passthrough `TLSRoute`/`TCPRoute`) or an app `HTTPRoute` onto the tenant Gateway, with the **managed-Kubernetes API as the flagship** (no TLS-convergence dependency — the cert already covers the hostname), plus the legacy tenant-root-ingress rendering and the read-only reporting of chart-rendered exposures. Database SNI routes follow this, per engine, as #20's unified-TLS convergence lands for each — this proposal supplies the trigger, #20 the data plane. This phase is what largely resolves #20. App-shipped `HTTPRoute` discovery (extending the lineage webhook's labelled kinds) grows across charts over time.
2. Confirm `ApplicationDefinition` endpoint coverage per kind — the per-port protocol the route path needs (the vocabulary of *Future work*); extend where kinds under-declare.
3. The analogous reporting-or-migration of legacy route `Ingress`es, alongside #45's address-preserving migration.
4. TLS-terminating routes with the unified-TLS effort, its own design.

## Open questions

1. **The platform-Gateway-with-per-tenant-listeners variant.** The default — a Gateway per tenant — is what makes cross-tenant hostname isolation free (*Routes: the `gateway` member*). A single platform Gateway hosting per-tenant listeners may be wanted, especially in private clouds (fewer addresses, one place to manage listeners). Adopting it needs a hostname-under-tenant-apex admission guard and a listener-budget story (Gateway API caps listeners at 64, the ceiling #20 already discusses). Left open deliberately: the machinery is not designed here, and nothing in the per-tenant default forecloses it.
2. **How far legacy-ingress support goes.** Two bounded options are on the table (*Legacy mode*): render the `gateway` member as a legacy passthrough `Ingress` where the tenant-root controller is the live data plane, and/or report chart-rendered exposures read-only beside attachments. Which of these ship, and whether legacy `Ingress`es are actively migrated to `gateway` attachments or only reported until the charts move to Gateway API, is a sequencing question for the charts' own migration.
3. **A dedicated object and controller, or routes in existing charts.** The objection on the table (lexfrei) is that routes belong in the application charts, as #20 says, with the platform owning Gateways, listeners and addresses, and no Cozystack object in front of a Route. Recorded as an open choice, with the `gateway` member as the provisional direction because it is the one being specified.
4. **Route→listener reference direction.** The binding model leans on the Gateway API capability where a listener selects a route rather than the route naming its listener — top-down, so an app's route need not know the gateway. That feature is **experimental** in Gateway API; the design depends on it stabilizing, or on a fallback where the controller patches `parentRefs`. Not formally settled.
5. **Routes in charts up front, or Services now and routes gradually.** Services are first-class immediately (every app has one, already labelled). App-shipped `HTTPRoute`s arrive over time — by extending the lineage webhook's labelled kinds (*Routes: the `gateway` member*) and by patching charts where routes do not yet exist (ClickHouse ships none today). Whether to require routes in charts up front or grow them is open; the lean is gradual.
6. **The exposure-class vocabulary (`gatewayRef` → a class).** A tenant should pick exposure from a granted, named vocabulary rather than naming a concrete `Gateway` object — mirroring `IPAddressClass` / `loadBalancerClass`. Agreed in principle, held on one blocker: the class-kind name, since `GatewayClass` is taken upstream (controller selection) and `ExposureClass` was removed in cozystack #3218. Until a name is chosen the field stays `gatewayRef`; the rename is not in this revision.
7. **A mechanism-agnostic spec instead of the union** (myasnikovdaniil, 2026-10-19). The tenant states what to expose (`protocol`, `hostname`, `port`) and optionally an `address` (`claimName` or `className`), and the controller picks the objects: a `ListenerSet` and `TLSRoute`/`TCPRoute`/`HTTPRoute` per attachment, a LoadBalancer Service for a dedicated L4 address, cozy-proxy for VMs. It would replace #45's `loadBalancer` member with an `address` field, so adopting it changes the base API.

## Alternatives considered

**One data plane only — addresses, or routes, but not both.** Rejected: the two serve disjoint needs and neither subsumes the other. A shared-Gateway route cannot give an address to allow-list, hold, or move, cannot serve a client that needs a raw IP, and cannot route MariaDB (server-speaks-first) or a pre-direct-TLS Postgres client; a dedicated address cannot give the SNI consolidation #20 is accepted for, costs an IP per endpoint, and is the wrong tool for HTTP or the managed-Kubernetes API. Forcing one onto the other's cases is exactly the over-narrow `external: true` #45 replaces. One kind, two members, the tenant (or portal) picks per endpoint.

**A separate kind for routes, beside `EndpointAttachment`.** Rejected: a route and an address share the hard parts — the tenant-facing-endpoint authorization seam, the N-per-application lifecycle, the `Detached`-not-deleted semantics, the GC, and the status shape — and differ only in the data plane. A second kind would duplicate all of that and force a user, a dashboard, and the portal to learn two nouns for one action ("publish this endpoint"). A discriminated union is the honest encoding: shared envelope, one member per data plane.

**A first-class hostname resource, parallel to #35's `IPAddressClaim`.** Rejected on an allocation asymmetry (*Routes: the `gateway` member*). #35 exists because the platform allocates addresses from a pool it owns, so they must be held, quota'd, and arbitrated as objects. Hostnames are allocated by registrars, not the platform; the tenant brings one it already owns. With no allocation event there is nothing to reserve, no scarcity to quota, and no arbiter to be — Gateway API resolves same-Gateway collisions and DNS resolves the rest. A hostname object would model a scarcity that does not exist.

**Paths in the `gateway` member (`example.com/grafana`).** Rejected. A path is L7 routing topology, not a binding: realizing it requires the gateway to match and usually rewrite (strip) the prefix, and a stripped prefix leaks the moment the app issues a redirect or an absolute URL from what it believes is its root — unfixable from the binding layer, since it is the app's own URL generation. "Path as binding" does not survive: any path handling is L7 processing. The need it served — several apps under one hostname — is met by host-based routing (one app per hostname, the clean default) or by an app-/tenant-authored `HTTPRoute` over a listener this member provisions, with the app made base-path-aware. A path field would reintroduce the lossy L7-exposure object (Ingress) the rest of this design avoids.

---

<!--
Inspired by KubeVirt enhancement proposals
(https://github.com/kubevirt/enhancements) and Kubernetes Enhancement
Proposals (KEPs).
-->
