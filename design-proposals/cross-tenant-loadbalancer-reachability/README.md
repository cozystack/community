# Cross-tenant reachability of LoadBalancer public IPs

- **Title:** `Cross-tenant reachability of LoadBalancer public IPs`
- **Author(s):** `@mattia-eleuteri`
- **Date:** `2026-10-06`
- **Status:** Draft

## Overview

A pod of one tenant cannot reach the public IP of another tenant's
`LoadBalancer` Service, although anyone on the Internet can. The tenant egress
policy only allows the own tenant and `world`, and Cilium's kube-proxy
replacement translates the LoadBalancer IP to the backend pod **before** that
policy is evaluated, so the connection is judged as "tenant A talking to
tenant B's pod" and dropped. VMs exposed through cozy-proxy are not affected,
since `026b1c781` in cozystack/cozystack.

This proposal asks the maintainers to choose how to fix it for the other
LoadBalancer Services: tenant Kubernetes ingress (kubevirt CCM), `Ingress`
applications, and databases with `external: true`. Two families of answers
exist: widen the **policy** so that the post-translation verdict passes, or take
the public IP away from Cilium and handle it in a **datapath** that leaves the
client seen as `world`. The proposal recommends the second, as an opt-in L4
mode in cozy-proxy prototyped in cozystack/cozy-proxy#25, and records why the
policy options fall short, mainly for the selectorless CCM Services.

## Scope and related proposals

- **Implementation, draft:** cozystack/cozy-proxy#25 (L4 mode), stacked on
  cozystack/cozy-proxy#18 (node-local datapath rules). Its design document,
  `docs/rfc/l4-loadbalancer-mode.md` in that PR, carries the datapath detail
  (nftables table, priorities, conntrack purge, lab log) that this proposal
  only summarizes.
- **Related:** `design-proposals/loadbalancer-announcer-neutrality` — MetalLB
  is the shipped announcer and `loadBalancerClass` is the integration point for
  another LoadBalancer implementation. The L4 mode keeps MetalLB as the
  allocator and announcer; see Alternatives for why it does not use
  `loadBalancerClass`.
- **Related:** `design-proposals/external-database-exposure` — databases move
  towards Gateway API TLS-passthrough. The database rows below apply to the
  per-release `LoadBalancer` Services that remain (MariaDB, Kafka, non-sharded
  MongoDB, and any engine before its SNI phase). Whether a Gateway's public IP
  is reachable across tenants is a separate question, listed under Open
  questions.
- **Interacts with:** cozystack/community#87 (`external-source-ranges`, open),
  which restricts sources through `loadBalancerSourceRanges`, enforced by
  Cilium on the LoadBalancer frontend. An opted-in Service has no Cilium
  frontend any more, so the L4 mode must handle that field itself (Security,
  Open question 7).
- **Out of scope:** IPv6, UDP, `externalTrafficPolicy: Cluster` (later phases,
  see Design), and MetalLB in BGP mode.

All repository paths below refer to `cozystack/cozystack` unless stated
otherwise.

## Decisions

## Context

Tenants are isolated on egress. `packages/apps/tenant/templates/networkpolicy.yaml`
renders, per tenant, a `CiliumClusterwideNetworkPolicy` `<tenant>-egress` that
allows the tenant's own namespaces, a list of platform services, and the entity
`world`. Ingress is open to `world` and `cluster`.

With kube-proxy replacement, Cilium resolves the Service frontend in the client
pod's datapath: a packet to a LoadBalancer IP is translated to a backend pod IP
and port, and the egress policy is evaluated on the translated destination. The
verdict is taken against the backend's identity, which belongs to another
tenant, so it is dropped. From the Internet, the same IP works.

cozy-proxy's VM mode avoids this because Cilium does not own the VM's public IP
(`026b1c781`, `[virtual-machine] Exclude external VM services from Cilium BPF
LB`): the packet leaves the pod with the public IP as destination, matches
`world`, and comes back through the normal ingress path. The VM mode is a
stateless 1:1 NAT: one backend, no port translation, `v1.Endpoints` only. It
cannot serve the other Services, which need port translation (CCM Services
target the tenant nodes' NodePorts), several backends, EndpointSlices (CCM
Services are selectorless, their slices are written by `kubevirt-eps-controller`)
and readiness.

### The problem

- A workload in tenant A cannot call an API that tenant B publishes through
  its Kubernetes cluster's ingress, by its public name.
- A tenant cannot connect to another tenant's `Postgres` with `external: true`
  by its public IP, while a VM in the same tenant can.
- In nested tenants, a child tenant cannot reach the parent tenant's public
  ingress.

A public IP is expected to be public. Today, whether it answers depends on
whether the caller sits in the same cluster, and on whether the Service is a VM.

## Goals

- A pod of any tenant reaches the public IP of an opted-in `LoadBalancer`
  Service on its declared ports, as an Internet client would.
- The connection is seen by the backend, and by its tenant's policies, as
  coming from outside: identity `world`, never the client tenant's identity.
- Tenant isolation is not widened: no tenant gains access to another tenant's
  pods, ClusterIPs or undeclared ports.
- Per-Service opt-in, controlled by the platform only, with a per-Service
  rollback.
- The VM mode is unchanged.

### Non-goals

- Cross-tenant access through ClusterIPs or service names. That stays isolated.
- Distinguishing tenants among in-cluster clients at the backend (see Security).
- Replacing MetalLB or Cilium as the LoadBalancer implementation for Services
  that do not opt in.

## Design

### 1. Opt-in

A Service is taken over only when it carries both:

```yaml
metadata:
  labels:
    networking.cozystack.io/lb-proxy: cozy-proxy
  annotations:
    service.cilium.io/type: ClusterIP
```

`service.cilium.io/type: ClusterIP` makes Cilium install only the ClusterIP
frontend: it stops translating the LoadBalancer IP and the NodePort, while the
ClusterIP keeps its current, isolated behavior. MetalLB still allocates and
announces the IP. The label is the trust anchor: the kubevirt CCM copies every
annotation of the tenant Service onto the infra Service, but not its labels, so
a tenant can never opt its own Service in (Security).

Only `externalTrafficPolicy: Local` is supported in phase 1. A labelled and
annotated Service in `Cluster` is refused: its IP is guarded and its traffic
dropped, rather than looping.

### 2. Datapath

cozy-proxy gets a second, independent mode behind `--enable-l4-loadbalancer`
(chart value `l4LoadBalancer.enabled`, off by default), in its own nftables
table `ip cozy_proxy_l4`, rebuilt atomically on every change:

- `guard`, on every node: drops new packets to an L4 IP on an undeclared
  port. Without it, such a packet is routed to the gateway and back, since the
  IP is no longer local once Cilium releases it.
- `translate`, on the node MetalLB elected to announce the IP only: stateful
  DNAT, with port translation and round-robin, to the **local** ready backends
  from the EndpointSlices. eTP `Local` is what makes MetalLB pick a node that
  has one.
- `masq`, on the same node: masquerades sources that are node IPs only, so that
  replies to in-cluster clients come back through the announcer's conntrack.
  Internet and VM clients keep their source.

Inputs: `Service`, `EndpointSlice`, `Node`, and MetalLB's `ServiceL2Status` for
the announcer. Conntrack entries are purged as kube-proxy does: UDP on endpoint
removal, TCP only when the frontend goes away on this node. A DaemonSet restart
leaves the table in place until the new instance replaces it.

The full datapath, its priorities against the VM mode, and the purge rules are
in the cozy-proxy design document (Scope).

### 3. Opting Services in, per type

Each change sits behind a platform switch.

| Type | Where | Change |
|---|---|---|
| Postgres | `packages/apps/postgres/templates/external-svc.yaml` | label and annotation; eTP is already `Local` |
| MariaDB | `packages/apps/mariadb/templates/mariadb.yaml` | label and annotation through the operator's service template, and eTP `Local` (operator default is `Cluster`) |
| Tenant Kubernetes (CCM) | new patch in `packages/apps/kubernetes/images/kubevirt-cloud-provider/patches`, switched on from `packages/apps/kubernetes/templates/cloud-config.yaml` | the CCM sets the label and the annotation itself, overriding any copied value, and only on eTP `Local` Services; existing infra Services need a one-shot patch |
| Ingress | `packages/extra/ingress/templates/nginx-ingress.yaml` | `controller.service.labels` / `annotations`; eTP is already `Local`; the host ingress with PROXY protocol last |
| cozy-proxy | `packages/system/cozy-proxy` | bump, enable the mode, RBAC for EndpointSlices, Nodes and `ServiceL2Status` |

### 4. Admission guard on CCM Services

Because the CCM copies tenant annotations, a platform may want an admission
guard that refuses infra Services carrying copied MetalLB, external-dns or
Cilium annotations. Cozystack ships none today. If it adds one, it must allow
`service.cilium.io/type: ClusterIP` on a Service that also carries the
`lb-proxy` label, and refuse every other `service.cilium.io/*` key. A Kyverno
rule doing that was tested on a lab with Kyverno 1.18.2; it is reproduced in
the cozy-proxy design document, section 7.1.

## User-facing changes

- Tenants: the public IP of an opted-in Service becomes reachable from every
  tenant. In-cluster clients appear with the announcer's join-network address
  (`100.64.x` on kube-ovn) instead of being refused.
- Admins: one platform switch per Service type, and `l4LoadBalancer.enabled`
  on cozy-proxy. No new CRD or API.
- Docs: the source-address table below, for tenants writing allowlists.

## Upgrade and rollback compatibility

- Default render unchanged: the mode is off and no Service carries the label.
- Per Service, forward: add the label (no effect), then the annotation; Cilium
  lets go and cozy-proxy programs the IP on its next sync. Connections opened
  through Cilium may be reset once.
- Per Service, rollback: remove the annotation. Cilium takes the IP back and
  cozy-proxy stops programming it at the same time. Removing only the label
  leaves the IP dark, so the runbook always removes the annotation.
- Whole mode: remove every annotation, then disable the mode, which deletes the
  table. Nothing is irreversible.

## Security

| Client | Source seen by the backend | Cilium identity at the backend |
|---|---|---|
| Internet | its own IP | `world` |
| VM with a public IP (VM mode) | the VM's public IP | `world` |
| pod or node process, any node | the announcer's join IP (`100.64.x`) | `world` |

- No tenant gains access to another tenant's pods: the client tenant's egress
  policy is unchanged and still sees `world`.
- CIDR allowlists now apply to in-cluster clients. A tenant rule that denies
  `0.0.0.0/0` except an office range is evaluated against a `world` source and
  holds; with the VM mode today the in-cluster source is `remote-node`, which
  such a rule does not match.
- In-cluster clients are indistinguishable from one another at the backend:
  all share the announcer's address. A tenant that needs to admit one tenant
  and not another must use ClusterIP and policies, or mTLS.
- A tenant cannot opt in: the label is set by the platform only, and a copied
  `service.cilium.io/type: ClusterIP` without it only makes the tenant's own
  IP dark. A tenant still controls the ports and the eTP of its CCM Service, as
  today.
- `loadBalancerSourceRanges` is enforced by Cilium on the LoadBalancer
  frontend, which an opted-in Service no longer has. The prototype does not
  read the field, so opting in a Service that sets it would silently drop the
  restriction. Before acceptance the L4 mode must either enforce it (a
  per-IP set of allowed sources in `guard`) or refuse such a Service, as it
  refuses eTP `Cluster`.
- New RBAC for cozy-proxy: read EndpointSlices, Nodes and MetalLB
  `ServiceL2Status`.

## Failure and edge cases

- Announcer moved by MetalLB → the new announcer programs the DNAT after its
  `ServiceL2Status` appears; in-flight connections are lost, as with any L2
  failover. Lab: one failed probe out of about 110 at a 0.5 s interval.
- No ready local backend on the announcer → the port is dropped, not forwarded.
- Undeclared port or ICMP to an L4 IP → dropped on every node; no routing loop.
- eTP `Cluster` Service opted in → refused and guarded; logged.
- cozy-proxy restart → the table stays in the kernel; no interruption.
- High connection churn from node-masqueraded clients → about 0.06 to 0.1 % of
  connections took over a second on the lab (SYN-ACK dropped inside OVS/OVN,
  recovered by retransmission). Mitigated with `masquerade fully-random`;
  Internet clients are not affected. Open question 3.

## Testing

- Unit tests for selection, desired state and purge decisions.
- Kernel tests in network namespaces: golden `nft list` output, and real TCP
  through three namespaces (translation, round-robin, source kept or
  masqueraded, guard, purge, rebuild with an open connection).
- Lab, Cilium 1.19.5 with kube-ovn and MetalLB L2: a synthetic multi-port
  Service, a Postgres application, the CCM ingress of a tenant cluster, VM
  clients, announcer failover, DaemonSet and ingress restarts, eTP `Cluster`
  refusal, 300 connections per second without keep-alive.
- e2e, to add before acceptance: a cross-tenant connection to an opted-in
  Postgres and to a tenant cluster ingress.

## Rollout

1. cozy-proxy release with the mode, off by default.
2. Databases (single port, low churn), behind their switch.
3. CCM Services with eTP `Local`, with the CCM patch and, if one is added, the
   admission guard first.
4. Tenant ingresses, then the host ingress.

Each step can be rolled back per Service.

## Open questions

1. **Announcer source.** Phase 1 reads MetalLB L2's `ServiceL2Status`. Should
   it be behind an interface, and is BGP mode, where every node advertises,
   required before acceptance?
2. **Admission guard.** Should Cozystack ship a guard on CCM Services, and in
   which package?
3. **Residual SYN-ACK loss under churn** for node-masqueraded clients: accept
   it, or fix it on the OVN side (conntrack timeouts or ACLs) first?
4. **eTP `Cluster`.** Keep refusing it, support it with full masquerade (client
   IP lost), or have the charts force `Local`?
5. **Gateway public IPs.** Once databases and HTTP move behind a Cilium Gateway
   (`external-database-exposure`), is a Gateway's public IP reachable across
   tenants? If not, it needs its own answer: Cilium's Envoy is not a backend
   cozy-proxy can take over.
6. **Label key.** `networking.cozystack.io/lb-proxy: cozy-proxy` is a proposal.
7. **`loadBalancerSourceRanges`** (Security, cozystack/community#87): enforce
   it in the L4 mode, or refuse Services that set it? Enforcing it in `guard`
   would also match in-cluster clients, which Cilium's Socket LB lets through
   today; which source those clients present at that point (pod IP or node
   IP, depending on the node) has to be measured first.

## Alternatives considered

### Policy side

These keep Cilium as the datapath and widen the tenant egress policy so that
the post-translation verdict passes. They need no new datapath, which is their
main appeal.

- **`toServices` for Services with a selector** (Postgres, MariaDB, Ingress).
  An egress rule in the tenant egress `CiliumClusterwideNetworkPolicy` with
  `toServices.k8sServiceSelector` on a platform-set label, and an empty
  namespace, matches those Services in every namespace; Cilium turns each
  Service's selector into an endpoint selector. It works for these Services.
  Its costs: the grant is on the backend **pods**, so the client may also reach
  them by pod IP; ports are whatever the rule lists, the same for every matched
  Service, not each Service's own; and the backend sees the client with its pod
  identity and pod IP, so tenant CIDR allowlists and `pg_hba` rules see
  in-cluster addresses rather than `world`. This is the strongest alternative
  for these three types, and the maintainers may prefer it for them.
- **`toServices` for selectorless Services** (CCM). Cilium converts their
  EndpointSlice IPs into CIDR selectors, which do not match pods unless
  `policy-cidr-match-mode=pods` is set. That value is accepted from Cilium 1.20
  (cilium/cilium#45194); Cozystack ships 1.19.5, whose agent accepts only
  `nodes`. Cilium's documentation advises against it by default: it allocates
  an identity per pod matched by a CIDR selector. Even with 1.20, the rule is
  L3 plus static ports: the backends are the tenant cluster's virt-launcher
  pods and the ports are NodePorts, so a cluster-wide rule opens the NodePort
  range of every opted-in tenant cluster to every tenant, including NodePorts
  the tenant never declared as LoadBalancer ports.
- **`toEndpoints` on virt-launcher pods.** Same exposure as the previous item
  without needing 1.20: every tenant reaches every tenant cluster's nodes on
  the ports listed.
- **Allow `cluster` in the tenant egress policy.** Removes tenant isolation;
  rejected.

### Datapath side

- **DNAT on the announcer without masquerade.** A pod on another node is
  masqueraded to its node IP by kube-ovn; the backend answers that node
  directly through OVN, outside the announcer's conntrack, and the client gets
  a RST. Observed on the lab.
- **DNAT on every node plus masquerade.** Breaks VM clients: the VM mode
  rewrites a VM's source statelessly before conntrack, so the reply is never
  translated back. Observed on the lab.
- **`service.kubernetes.io/service-proxy-name` instead of
  `service.cilium.io/type: ClusterIP`.** Cilium then drops the ClusterIP and
  the NodePort as well.

### Opt-in mechanism

- **`loadBalancerClass`**, the integration point recorded in
  `loadbalancer-announcer-neutrality`. A Service with a non-default class is
  skipped by MetalLB, which would then neither allocate nor announce its IP.
  The L4 mode is not another LoadBalancer implementation: it relies on MetalLB
  for both and only replaces Cilium's translation. A label plus the Cilium
  annotation expresses exactly that. `loadBalancerClass` is also immutable,
  which would turn the per-Service rollback into a Service re-creation and an
  IP change.
