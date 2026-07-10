# Tenant-managed site-to-site connectivity via gateway VMs (`site-router` and `site-gateway`)

- **Title:** `Tenant-managed site-to-site connectivity via gateway VMs (site-router and site-gateway)`
- **Author(s):** `@myasnikovdaniil`
- **Date:** `2026-07-08`
- **Status:** Draft

## Overview

Cozystack tenants need site-to-site connectivity between their workloads and external networks, in both directions — exposing a tenant-managed application to a remote site (inbound), and letting a tenant-managed application reach a service that lives only behind a tunnel (outbound) — and it must work for any application (databases, message queues, object storage, gRPC, raw UDP), not one special case.

The privileged network dataplane (`NET_ADMIN`, XFRM / a `wireguard` interface, `ip_forward`) is terminated **inside a KubeVirt VM**, not a pod: the guest kernel is isolated by hardware virtualization, so the blast radius is contained by the VM boundary and the platform never grants a tenant a privileged host-cluster pod. The gateway VM is a normal, dual-homed member of the tenant's pod network.

There are two legitimate ways to bridge such a tunnel to the tenant's workloads, with genuinely different trade-offs, and both are wanted in practice — so this proposal ships them as **two co-equal catalog apps over a shared foundation**:

- **`site-router`** — *routed* mode. Plain L3 forwarding between the tunnel and the tenant network; the original client source IP is preserved; whole-subnet reachability and ICMP work. Uses kube-ovn routing (kube-ovn-specific).
- **`site-gateway`** — *NAT* mode. The VM DNAT/SNAT-bridges the tunnel to managed-app `ClusterIP`s; CNI-agnostic and fully contained; per-target host:port reachability; the original source IP is not preserved.

Neither is a fallback for the other — a cluster admin can offer either or both. The design has been prototyped and validated end-to-end (see [Testing](#testing)). (Naming note: these are distinct from the existing `packages/apps/vpn`, which is a client / remote-access VPN — the two apps here are site-to-site network edges.)

## Scope and related proposals

- **[#29 structured external exposure](https://github.com/cozystack/community/pull/29).** The tunnel's own external entry point (the UDP listener the remote peer dials) is published through the structured `ServiceExposure` / `ExposureClass` primitives from #29 rather than a bespoke `LoadBalancer` path; that exposure class must support **UDP** (IKE/NAT-T 4500, WireGuard).
- **[#7 ClusterMesh / Kilo](https://github.com/cozystack/community/pull/7).** Complementary and a different niche — a routed WireGuard node-to-node mesh between cooperative Kubernetes clusters. See [Alternatives considered](#alternatives-considered).
- **Deferred here:** a platform-run (non-VM) managed variant; HTTP/L7 ingress (the existing tenant Ingress / Gateway API covers public HTTP); a per-tenant egress IP (future work — the gateway VM is its natural home later).

## Context

Cozystack runs tenant workloads under a hardened networking model:

- **Namespace hardening.** Cozystack applies Pod Security Standards to tenant namespaces. The platform operator only sets `pod-security.kubernetes.io/enforce=privileged` on a namespace when a platform-declared package requests it. A regular tenant cannot schedule a privileged, `NET_ADMIN`, or `hostNetwork` pod — deliberately, because such a pod executes against the node kernel and could manipulate host networking or observe other tenants' traffic.
- **Default pod network vs. custom VPC.** Managed apps (databases and friends) run as ordinary pods on the **default cluster pod network** and are reached via `ClusterIP` Services + CoreDNS. They are not placed on a custom kube-ovn VPC; VPCs are isolated from the default network and the managed-app operators reconcile over the default network — so a managed app cannot simply be "moved onto a VPC."
- **Dual-homed VMs.** KubeVirt VMs are the exception: every VM always has a `default` pod-network NIC (and can carry additional multus interfaces). A VM is therefore a first-class member of the default pod network. This is the primitive the design leans on.

### The problem

A tenant today has no supported way to let a remote site reach a tenant-managed app over a tunnel without exposing it to the public internet (inbound), nor to let a tenant app call a service that is only routable through a tunnel (outbound). The naive approaches all require either a privileged host-cluster pod (a tenant-escape vector — defeats the hardening above) or changes to shared, cluster-wide network infrastructure (cross-tenant collision risk). We want a self-service, tenant-scoped mechanism that generalizes to any protocol and touches neither the host kernel nor the managed apps.

## Goals

- Give tenants **self-service** site-to-site connectivity, both directions, through catalog apps.
- Support **inbound** exposure of any tenant-managed app and **outbound** reach to tunnel-only services, for any L4 protocol, without modifying the target apps.
- Keep the privileged dataplane **contained** in a VM guest; add no privileges to the host cluster and none to managed apps.
- Offer **both** a routed mode (source-IP-preserving, L3) and a NAT mode (contained, CNI-agnostic) as **co-equal, admin-gateable** choices — not one as a fallback of the other.
- Support **both IPsec and WireGuard** as tunnel backends (interop vs. simplicity — see [Design](#design)).

### Non-goals

- **HTTP/L7 ingress.** Public HTTP(S) is served by the existing tenant Ingress / Gateway API.
- **A platform-managed, always-on VPN service.** These are tenant-deployed catalog apps; a platform-run variant is possible future work.
- **A per-tenant egress IP.** Deferred future work; the gateway VM is its natural home later.
- **Routing a remote CIDR onto the shared default-VPC router** — rejected (cross-tenant collisions). Routed mode uses tenant-scoped kube-ovn routes, never the shared router.

## Design

### Two apps over one foundation

Both apps share roughly 80% of their implementation — the VM appliance, the tunnel backend, the #29 exposure of the tunnel endpoint, the day-2 configuration mechanism, secret handling, and packaging. They differ only in what happens to a decrypted packet (L3-forward vs DNAT/SNAT) and in the CNI coupling that entails:

| Axis | `site-gateway` (NAT) | `site-router` (routed) |
|------|----------------------|------------------------|
| Inner path | DNAT + SNAT → managed-app `ClusterIP` | plain L3 forwarding, no NAT |
| Return path | SNAT masquerade | kube-ovn `ovn.kubernetes.io/routes` annotation |
| CNI coupling | none — any CNI | kube-ovn-specific (routes + port_security) |
| Source IP | not preserved (SNAT) | preserved |
| Reachability | per host:port | whole subnet + ICMP |
| Config surface | `exposedServices` / `remoteTargets` (per-target ports) | `remoteCIDRs` / static routes / BGP |
| Security posture | fully contained | port_security relaxed (scoped is the target state) |
| Topology-advertising apps | need per-member advertised addresses | transparent |

Why two apps rather than one `mode:` toggle: the two have divergent values schemas (per-target ports vs CIDRs/BGP), asymmetric controllers (routed needs a privileged CNI-mediation controller; NAT needs none), and different security postures a cluster admin may want to gate independently. Sharing the implementation (a common VyOS-driving controller core + image + base chart) keeps this from becoming duplicated code.

### Shared foundation

**Principle.** Terminate the tunnel in a KubeVirt VM (contained privilege). The VM is dual-homed on the tenant's default pod network and is the sole holder of the tunnel.

```mermaid
flowchart TB
    ext["External site<br/>(on-prem router / cloud VPN / another org)"]
    xp["#29 tunnel exposure<br/>(ServiceExposure, UDP)"]
    subgraph node["Worker node"]
        gw["Gateway VM (VyOS)<br/>tunnel termination + firewall + MSS clamp<br/>+ (NAT: DNAT/SNAT) / (routed: L3 forward)"]
        a["managed app A"]
        b["managed app B"]
    end
    ext -->|"IPsec ESP-in-UDP / WireGuard UDP"| xp --> gw
    gw --> a & b
```

**Tunnel backend — IPsec or WireGuard.** VyOS terminates both natively and `tunnel.type` selects per peer. **WireGuard is the simpler default on an overlay CNI** — UDP-native (a single configurable UDP port), so it rides the pod overlay as ordinary UDP with no ESP, no NAT-T, and no forced encapsulation; static keypairs, no IKE, smaller header overhead (see the validated finding in [Testing](#testing)). **IPsec is for interop** — much external/enterprise gear only speaks IKEv2/IPsec; supported, with the encapsulation requirement in [Testing](#testing).

**Tunnel entry point.** Published via #29 `ServiceExposure` + `ExposureClass` (UDP), not a bespoke `LoadBalancer` — one shared external-address contract for both apps.

**Day-2 configuration.** cloud-init provisions the VM only at first boot (KubeVirt bakes it into a static disk that is regenerated only on VM restart), so ongoing changes — add/rotate a peer, add a target or route — are driven by a **platform controller talking to the guest's own management API**. For VyOS the HTTPS API applies an atomic set/delete batch, so a change touches only the affected config and unrelated tunnels stay up. The management-API key is generated at runtime and seeded once via first-boot cloud-init; the API is reachable by the platform only (see [Security](#security)). A first iteration may fall back to "config change = VM restart" if called out explicitly.

**Controller & low-level entities.** Tenant input stays in the catalog-app values (no new CRD); validation is the values schema plus controller-side reconcile checks surfaced as status conditions. A platform controller reconciles the VM + tunnel and pushes rendered config to the guest. The controller is split behind a small backend interface so the neutral VyOS-driving core (render → push → observe → status) is shared, and each app implements only its own materialization and any CNI mediation. The two apps differ sharply here: `site-router` carries a CNI-mediation controller (below); `site-gateway` needs none.

**Image.** A VyOS appliance image (IPsec, WireGuard, native DNAT/SNAT, routing, and firewalling in one network OS), published by the platform as a reproducible, KubeVirt-consumable artifact.

### `site-gateway` — NAT mode

**Inbound (external site → any tenant app).** The remote peer targets a virtual service-exposure address that the gateway owns and maps to an app `ClusterIP`:

1. The remote peer sends tunnel traffic to the gateway's #29-exposed UDP endpoint; the VM decrypts the tunnel.
2. **DNAT**: the virtual destination → the target app's `ClusterIP:port` (one entry per exposed app — the exposure table).
3. **SNAT (masquerade)**: source → the gateway's own pod IP. Mandatory: the cluster has no route back to the remote peer's inner IP, so without SNAT the app's reply leaves via the default route and is black-holed.
4. The VM forwards onto the pod network; the node resolves the `ClusterIP` to a backend pod; the app sees the gateway pod IP as client. Replies retrace the path and are re-encrypted.

**Outbound (any tenant app → remote service over tunnel).** The app talks to a **local Service** whose endpoint is the gateway VM (the only app-side change is the target hostname). The node routes that `ClusterIP` to the gateway on a **per-target-unique listener port** (the local Service still exposes the standard port, mapped to a unique port on the VM), so the VM can disambiguate multiple remote targets that share a destination port; the VM DNATs that unique port to the real remote `IP:port` and SNATs into the tunnel's inner subnet.

**Why it generalizes.** The app only ever sees a `ClusterIP` on its own network and speaks no VPN; DNAT is L4 (TCP/UDP, any port), so it works for databases, queues, object storage, gRPC, and raw UDP. `site-gateway` is CNI-agnostic and fully contained — SNAT keeps every emitted packet on the gateway's own pod IP, so no port-security relaxation is needed.

**Topology-advertising apps.** Protocols where a client bootstraps to one endpoint and is then handed the members' own addresses to connect to directly — Kafka (`advertised.listeners`), MongoDB replica-set/sharded, Redis Cluster, Cassandra/Scylla, Elasticsearch with sniffing — are **not** transparent under plain NAT of the bootstrap endpoint: each member needs its own tunnel-reachable address that the app also advertises. Under `site-gateway` this means explicit per-member mapping (a distinct tunnel-side address:port per member, plus the app configured to advertise it); the managed-app operators already do this kind of thing for public exposure, but #29's `target` is per-named-listener, not per-broker/replica, so #29 does not directly cover per-member addressing today. The transparent path for this class is therefore **`site-router` (routed)** — the members' real addresses are directly reachable, so discovery works as long as members advertise a routable ClusterIP. Folding per-member NAT into a tunnel-backed `ExposureClass` is a possible future unification, not a solved design (see [Open questions](#open-questions)). Single-endpoint protocols (Postgres, MySQL, plain Redis, AMQP, ClickHouse client, HTTP/gRPC) are transparent under NAT and need none of this.

### `site-router` — routed mode

The decrypted packet is forwarded onto the tenant network **without NAT**; the return path is the kube-ovn `ovn.kubernetes.io/routes` namespace annotation (inherited onto pods at creation by the kube-ovn webhook), so the **client source IP is preserved** and whole-subnet reachability + ICMP work. This is the mode for cases where the remote side authorizes by source IP, or needs to reach a whole subnet, or where the app advertises its own topology (members' real addresses are directly reachable).

**CNI-mediation controller.** A tenant cannot annotate its own namespace or touch `port_security`, so the platform controller mediates: validate declared remote CIDRs (disjoint from pod/service/join/node networks; cross-tenant overlap is fine — routes are namespace-scoped), set the namespace routes annotation, scope/relax `port_security` on the gateway VM port, and clean everything up on deletion. Because the annotation is applied at pod **creation**, existing tenant workloads gain reachability only after they are rolled; the controller reports the pods whose annotation lags the namespace ("pods pending route") rather than restarting anything.

**Security posture.** Routed mode relaxes `port_security` on the gateway port. Containment comes from Cilium's sender-side egress enforcement — the endpoint identity is compiled into the per-endpoint eBPF program, so a spoofed source cannot widen the reachable set beyond the tenant tree + world. The target state is **scoped** `port_security` (the declared remote CIDRs added to the port's allowed addresses). Residual risks are documented in [Security](#security).

**Transport note.** Routed mode carries transparent L3 for TCP/UDP/ICMP/SCTP only — non-standard IP protocols (ESP, GRE, VRRP) still do not cross the pod fabric (same root cause as the ESP finding in [Testing](#testing)). It is kube-ovn-specific and requires the remote CIDR to be disjoint from cluster networks.

## User-facing changes

Two catalog apps appear in the tenant dashboard, sharing the tunnel/peer/resources values and differing only in the bridging block. Sketches (cozyvalues-gen annotated):

```yaml
# site-gateway (NAT)
## @param tunnel.type {string} Backend: "wireguard" (default; UDP-native) or "ipsec".
tunnel: { type: wireguard }
## @param peer.address {string} Public address of the remote peer / WireGuard endpoint.
peer:
  address: ""
  auth: { secretRef: "" }   # IPsec IKE/PSK-or-cert; WireGuard peer public key + allowedIPs
## @param exposedServices {array} Inbound: {name, listenPort, targetService, port}
exposedServices: []
## @param remoteTargets {array} Outbound: {name, localPort, remoteHost, remotePort}
remoteTargets: []
## @param resources {object} VM sizing (cpu/memory).
resources: { cpu: "1", memory: "1Gi" }
```

```yaml
# site-router (routed)
## @param tunnel.type {string} Backend: "wireguard" (default) or "ipsec".
tunnel: { type: wireguard }
## @param peer.address {string} Public address of the remote peer / WireGuard endpoint.
peer:
  address: ""
  auth: { secretRef: "" }
## @param remoteCIDRs {array} Remote networks reachable over the tunnel (must be disjoint from cluster networks).
remoteCIDRs: []
## @param staticRoutes {array} Optional extra static routes.
staticRoutes: []
## @param bgp {object} Optional BGP peering with the remote side.
bgp: { enabled: false }
## @param resources {object} VM sizing (cpu/memory).
resources: { cpu: "1", memory: "1Gi" }
```

A cluster admin can enable/disable `site-router` independently — its kube-ovn coupling and `port_security` relaxation may not be wanted on every cluster. No existing app, CRD, or API changes.

## Upgrade and rollback compatibility

Purely additive and opt-in. No migration; existing clusters, manifests, and APIs are unaffected. The gateway VM image is a new artifact built and published by the platform. Rollback is deletion of the app instance (its `VMInstance`/`VMDisk`, the generated Services, and — for `site-router` — the namespace annotation / port_security scoping the controller cleans up), which removes the gateway entirely; nothing else in the tenant is touched.

## Security

- **Contained privilege (both apps).** The privileged dataplane lives in a VM guest kernel isolated by hardware virtualization; a compromise inside the gateway cannot manipulate the host node's networking or observe other tenants, and the platform never hands a tenant a privileged host-cluster pod.
- **`site-gateway` is fully contained.** SNAT keeps every packet the gateway emits on its own pod IP, so the CNI's port-security never sees a foreign source and needs no relaxation.
- **`site-router` relaxes port_security** on the gateway port (scoped is the target state). Containment is Cilium's sender-side egress enforcement; residual risks to document: intra-namespace identity spoofing (receiver-side identity resolved from source IP) and spoofed-source egress where SNAT is absent.
- **Managed apps untouched**; a firewall allow-list on the gateway makes exposure explicit; all state is tenant-scoped.
- **Secret handling.** A tenant does not create `Secret` objects directly; as with the existing managed apps (`apps/vpn`, `postgres`, `mariadb`) the app chart templates the `Secret` into the tenant namespace from the app values — autogenerating material when it is not supplied and using `lookup` to keep it stable across reconciles. The design minimizes what must be shipped: for **WireGuard** the tenant's private key is generated in-chart (never entered) and only its public key is surfaced for the remote side, while the peer's public key + endpoint are ordinary non-secret values — so no tenant secret is shipped at all. The one genuinely secret tenant input is the **IPsec PSK** (it must match the remote peer, so it cannot be autogenerated); it rides a values field into the chart-templated `Secret`, with the same plaintext-in-values-at-rest exposure the platform is already addressing for database passwords. The management-API key is generated at runtime, never entered.
- **Management API is platform-only.** The guest's config API is network-restricted so only the platform controller can reach it.

## Failure and edge cases

- **Missing SNAT → inbound reply black-holed (`site-gateway`).** Without the source-NAT rule the app replies to an unroutable tunnel address and the flow times out. SNAT is mandatory (validated).
- **MTU / double encapsulation.** The overlay already lowers MTU and the tunnel adds overhead; without MSS clamping (and/or a lowered tunnel MTU) large TCP packets black-hole. The gateway sets an **MSS clamp by default**, derived from the detected overlay MTU — clamping is the chosen approach rather than leaving MTU to the tenant.
- **Native ESP dropped pod-to-pod (IPsec).** The CNI drops non-TCP/UDP/ICMP/SCTP IP protocols between pods — native ESP (proto 50) included — so ESP-in-UDP (forced UDP encapsulation) is required unconditionally (validated). In policy-enforced tenant pods the dropper is Cilium's conntrack ("Unknown L4 protocol"); it is not geneve-specific. WireGuard, being UDP-native, is unaffected.
- **DNAT must target stable `ClusterIP`s**, never ephemeral pod IPs (`site-gateway`).
- **Source IP is lost inbound under NAT** (`site-gateway`) — use `site-router` when the remote side needs it.
- **VM image must be bootable and 512-native.** DRBD-backed (4K-sector) StorageClasses cannot boot a 512-native GPT image; the disk must sit on a 512-native StorageClass or use a KubeVirt `blockSize` override (e.g. `blockSize.custom.logical: 512` / `physical: 4096` on the disk spec). The image must ship a real bootloader (validated the hard way — see Testing).
- **A single gateway is a per-tenant SPOF** until HA is added (see [High availability](#high-availability)).

## Testing

The design was **prototyped and validated end-to-end** on a development Cozystack cluster (KubeVirt, Cilium + kube-ovn, LINSTOR), using two gateway VMs — one acting as the tenant gateway, one simulating the external site — with a real tenant-managed Postgres as the inbound target. The **NAT (`site-gateway`) inbound/outbound paths** were validated with the IPsec backend; the **routed (`site-router`) path and the WireGuard backend** are the next to validate (open questions). All items passed:

| # | Item | Result |
|---|------|--------|
| 1 | IKEv2 SA establishes both sides | PASS |
| 2 | Inbound: external site → virtual VIP → DNAT/SNAT → managed Postgres (real server response) | PASS |
| 3 | Outbound: gateway client → tunnel → remote listener | PASS |
| 4 | SNAT-required negative test (removing SNAT black-holes the reply) | PASS |
| 5 | MTU / MSS clamp (tunnel MTU 1320, clamp 1280; multi-MB transfer, no stall) | PASS |

**Key finding — native ESP is dropped by the CNI overlay.** IKE (UDP) negotiated and the SA came up, but the ESP data plane was silently dropped even pod-to-pod (receiver saw zero ESP, 100% loss). Forcing ESP-in-UDP encapsulation on both peers restored bidirectional traffic. On an overlay CNI the IPsec backend must therefore always force UDP encapsulation — and **WireGuard, being UDP-native, sidesteps this entirely**, which is a strong argument for it as the default backend.

**Non-standard-protocol drop (informs HA).** Independently re-verified with a minimal repro: the CNI drops *all* non-TCP/UDP/ICMP/SCTP IP protocols (proto 112/50/47/4) pod-to-pod, both same-node and cross-node — so it is **not** the geneve tunnel. In policy-enforced tenant pods the dropper is **Cilium's conntrack** (`bpf_lxc.c`, "CT: Unknown L4 protocol"); without a policy it is the OVS datapath. This is the same root cause as the native-ESP drop, and it is why keepalived/VRRP cannot elect over the pod network (see [High availability](#high-availability)). Separately, a kube-ovn `Vip` + `ovn.kubernetes.io/aaps` annotation was validated to share a VIP across two gateway pods with `port_security` kept **on** (the VIP moves on owner change and a bogus source is still dropped — anti-spoofing scoped, not disabled).

Planned automated coverage before implementation lands: helm-unittest for chart rendering across backends and both apps; an e2e that stands up the two-VM topology and asserts the inbound/outbound flows plus the SNAT-required and MSS behaviors.

## High availability

Deferred for the first iteration: a **single gateway VM with KubeVirt live-migration** for planned maintenance (conntrack, tunnel SA, and pod IP preserved — no tunnel drop; requires migratable/replicated storage, see the `blockSize` caveat). Automatic **unplanned-failure** HA is a follow-up; the preferred path is **Service-fronted active/passive** because it is CNI-agnostic (no floating L2 VIP, so no port-security interaction) at the cost of a small leader-election/lease agent and seconds-scale endpoint reconvergence. A kube-ovn shared-VIP via allowed-address-pairs was validated to move a VIP with `port_security` kept on, but keepalived-style election cannot run over the pod network (VRRP/proto-112 is dropped — see [Testing](#testing)), so it would need a side-channel or controller to drive the VIP claim.

## Rollout

- **Phase 1 — `site-router` (routed), IPsec backend**: the routed dataplane plus its CNI-mediation controller (CIDR validation, routes annotation, scoped `port_security`), with **MSS clamping** and **tunnel-state observability** (SA up/down, rekey, counters) surfaced from the start. Routed is the primary mode tenants ask for. (Note: the end-to-end prototype so far validated the NAT dataplane, so routed carries the first round of validation here.)
- **Phase 2 — `site-gateway` (NAT)**: the inbound/outbound DNAT/SNAT machinery (prototype-validated) with forced UDP encapsulation.
- **Phase 3 — WireGuard backend**: validate over the overlay and, if confirmed simpler/robust, make it the default `tunnel.type`.
- **Phase 4 — the rest**: HA (Service-fronted active/passive), scoped `port_security` for routed, per-tenant egress IP.

Ordering leads with routed because it is the primary requested mode; the shared foundation is built once and both apps sit on it.

## Open questions

- **IPsec PSK at rest.** WireGuard ships no tenant secret (its key is autogenerated in-chart); the IPsec PSK is the one tenant-supplied secret — decide whether it stays a values field templated into a `Secret` (like DB passwords today — plaintext in values at rest) or gets a write-only / dashboard-mediated affordance.
- **Tunnel exposure via #29.** Confirm the `ExposureClass` UDP support and how the tunnel endpoint address interacts with the tenant's LB pool and quotas.
- **Topology-advertising apps under NAT.** Whether per-member advertised addressing for Kafka / Mongo / Redis-Cluster / Cassandra under `site-gateway` is worth a tunnel-backed `ExposureClass` (needs per-member granularity #29 does not have today) or is simply left to `site-router`.
- **HA mechanism.** Given VRRP does not cross the overlay (see Testing), standardize on Service/endpoint-based failover (needs a controller/lease) vs an AAP shared-VIP with side-channel election.
- **Scoped port_security for `site-router`.** kube-ovn allowed-address-pairs need CIDR support to add the declared remote CIDRs to the port (OVN itself accepts `MAC IP/mask`).
- **WireGuard backend.** Validate the handshake/SA over the overlay and confirm identical bridging before defaulting to it.
- **Relationship to ClusterMesh (#7).** Whether to present these apps and ClusterMesh as one coordinated "tenant connectivity" story, and where the boundary sits.

## Alternatives considered

- **Give tenants privileged / `NET_ADMIN` host pods.** Rejected — tenant escape; defeats namespace hardening. The whole design exists to avoid this.
- **Route the remote CIDR into the shared default-VPC router.** Rejected — the router is shared cluster-wide; injecting tenant RFC1918 CIDRs causes cross-tenant collisions and leakage. (`site-router` instead uses tenant-scoped kube-ovn namespace routes.)
- **Put managed apps on a custom kube-ovn VPC.** Not viable — VPCs are isolated from the default network and managed-app operators reconcile over the default network; only VMs are dual-homed.
- **Platform-run kube-ovn VPC-NAT-Gateway with the tunnel.** Possible future managed variant, but more platform work and it couples XFRM to the host kernel + OVS. The VM approach contains the privilege more cleanly and ships sooner.
- **A single app with a `mode: routed|nat` toggle.** Rejected — the two modes have divergent values schemas, asymmetric controllers (routed needs a privileged CNI-mediation controller, NAT needs none), and different security postures a cluster admin may want to gate independently. Two apps over a shared implementation express this more cleanly than conditional schema + always-on machinery.
- **Traefik / L7 proxy.** Limited — HTTP-only; not general across L4 protocols.

### ClusterMesh / Kilo (#7), and why two mechanisms

[#7](https://github.com/cozystack/community/pull/7) pursues an overlapping goal (tenant access to services across a boundary) but is a different shape, and neither mechanism subsumes the other:

| Axis | ClusterMesh / Kilo (#7) | This proposal (gateway VMs) |
|------|-------------------------|-----------------------------|
| Remote end | A cooperative Kubernetes cluster running Kilo, reached via a kubeconfig | An arbitrary external site — not Kubernetes, no Kilo, no kubeconfig |
| Tenant workload served | The tenant's managed Kubernetes cluster (guest-VM pods) | Managed apps in the host-cluster tenant namespace |
| Transport | WireGuard node-to-node mesh (`mesh-granularity=cross`) | IPsec or WireGuard site-to-site, one VM |
| Address plane | Routed pod-CIDRs, no NAT; disjoint CIDRs required | `site-router`: routed (source-IP preserved); `site-gateway`: NAT to ClusterIPs (tolerates overlap) |
| Throughput | Full node×node mesh (built for Ceph) | Single gateway — app-level flows |
| Privilege | Kilo `NET_ADMIN` inside guest-cluster VMs → contained | `NET_ADMIN` inside the gateway VM guest → contained |

Kilo cannot terminate a third-party VPN (both ends must run Kilo with a kubeconfig), and a single gateway VM cannot serve the Ceph-scale mesh. They are different layers — a platform storage fabric vs. a tenant-edge VPN concentrator — analogous to a cloud provider shipping both VPC Peering and a VPN Gateway. Both share the principle that matters: no privileged pods in the shared host namespace; privilege is contained in guest-cluster VMs (Kilo) or a dedicated gateway VM (this proposal).
