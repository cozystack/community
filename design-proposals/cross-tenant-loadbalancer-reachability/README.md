# Cross-tenant reachability of LoadBalancer public IPs

- **Title:** `Cross-tenant reachability of LoadBalancer public IPs`
- **Author(s):** `@mattia-eleuteri`

## Overview

A pod of one tenant cannot reach the public IP of another tenant's
`LoadBalancer` Service, although anyone on the Internet can. With kube-proxy
replacement, Cilium translates the LoadBalancer IP to a backend pod **before**
the tenant egress policy is evaluated, so the verdict is taken against another
tenant's pod and the connection is dropped.

The proposal splits the fix by the kind of Service:

- **Part A, Services with a selector** (ingress controllers, and the
  applications published with `external`): widen the tenant egress policy with
  `toServices` rules on platform-labelled Services. No new datapath; works on
  every variant, isp-slim included. Every such application has a route for the
  label except RabbitMQ, which stays as today until one is verified.
- **Part B, Services of tenant Kubernetes clusters** (kubevirt CCM,
  selectorless): matching their backends in a policy needs Cilium's
  agent-wide `policy-cidr-match-mode=pods`, which would also let a tenant's
  SecurityGroup `toCIDR` reach other tenants' pods, and a single shared rule
  with static ports would open the NodePort range of every tenant cluster to
  every tenant. For these only, an opt-in L4
  mode in cozy-proxy takes the public IP over from Cilium, as the VM mode
  already does for VMs. Prototype: cozystack/cozy-proxy#25.

Part A is independent and can land first. Part B is limited to the variants
that run tenant clusters (iaas), which are the ones with MetalLB and kube-ovn.

## Scope and related proposals

- **Implementation of Part B, draft:** cozystack/cozy-proxy#25, stacked on
  cozystack/cozy-proxy#18 (node-local datapath rules). Its design document,
  `docs/rfc/l4-loadbalancer-mode.md`, carries the datapath detail (nftables
  table, priorities, conntrack purge, lab log) that this proposal summarizes.
- **Interacts with:** cozystack/community#87 (`external-source-ranges`, open).
  Part A must not open a Service whose owner restricted its sources (Part A,
  Security); Part B removes the Cilium frontend that enforces
  `loadBalancerSourceRanges`, so it leaves such a Service to Cilium (Part B,
  Opt-in).
- **Related:** `design-proposals/loadbalancer-announcer-neutrality` — MetalLB
  is the shipped announcer and `loadBalancerClass` the integration point for
  another implementation. Part B keeps MetalLB as allocator and announcer; see
  Alternatives for why it does not use `loadBalancerClass`.
- **Related:** `design-proposals/external-database-exposure` — databases move
  towards Gateway API TLS-passthrough. Part A covers the per-release
  `LoadBalancer` Services that remain. The Gateway does not change Part B: the
  Services it serves are created by tenants inside their own clusters.
- **Out of scope:** VMs (fixed, below), IPv6, UDP, `externalTrafficPolicy:
  Cluster` in Part B, announcers other than MetalLB.

All repository paths below refer to `cozystack/cozystack` unless stated
otherwise.

## Decisions

## Context

### The tenant egress policy

`packages/apps/tenant/templates/networkpolicy.yaml` renders, per tenant, a
`CiliumClusterwideNetworkPolicy` `<tenant>-egress`. It allows the tenant's own
namespaces and, in every ancestor namespace, the `vminsert`, `etcd` and
`cozystack.io/service: ingress` pods. The ingress rule came with `4dfdbfeb6`
(`fix(tenant): allow egress to parent ingress pods`) so that a nested cluster in
`Proxied` mode reaches its own domains through its parent's ingress. A
namespaced `CiliumNetworkPolicy` adds `world` and the tenant's own ingress pods.

The ancestors are derived from the prefixes of the release namespace. A direct
child of `tenant-root` gets `tenant-root`; a deeper tenant does not, because
the children of `tenant-root` are named `tenant-<name>`, not
`tenant-root-<name>`. Rendered with `helm template`:

| Tenant | Ancestor ingress allowed |
|---|---|
| `tenant-foo` (in `tenant-root`) | `tenant-root` |
| `tenant-foo-bar` | `tenant-foo` |
| `tenant-foo-bar-baz` | `tenant-foo`, `tenant-foo-bar` |

So from depth 2 on, a tenant does not reach the host ingress. That is a
separate fix to the existing rule and is not designed here.

### Why the verdict is wrong

With kube-proxy replacement, Cilium resolves the frontend in the client pod's
datapath (socket LB, then tc): a packet to a LoadBalancer IP is translated to a
backend pod IP and port, and the egress policy is evaluated on the translated
destination. Unless a rule above admits the backend, it is dropped. From the
Internet, the same IP works.

### VMs are fixed

`026b1c781` (`[virtual-machine] Exclude external VM services from Cilium BPF
LB`, in v1.3.0) puts `service.kubernetes.io/service-proxy-name` on external VM
Services. Cilium ignores them, cozy-proxy's VM mode serves them, and the packet
leaves the client pod towards the public IP, matches `world`, and comes back
through the normal ingress path. The VM mode is a stateless 1:1 NAT (one
backend, no port translation, `v1.Endpoints`), so it cannot serve the Services
below.

### Tenant Kubernetes clusters, `Proxied` and `LoadBalancer`

`addons.ingressNginx.exposeMethod` defaults to `Proxied`. In that mode
`packages/apps/kubernetes/templates/ingress.yaml` renders an `Ingress` on the
parent's controller with one `ssl-passthrough` rule per entry of
`addons.ingressNginx.hosts`; with no hosts, nothing is routed. Every domain must
therefore be declared on the Kubernetes application beforehand, and only HTTP
and HTTPS reach the cluster through that addon.

Operators who let tenants publish their own domains or TCP services use
`exposeMethod: LoadBalancer`. More generally, `exposeMethod` only concerns the
ingress-nginx addon: **every** `LoadBalancer` Service a tenant creates inside
its cluster (its own ingress controller, a TCP service) is realized by the
kubevirt CCM as a selectorless Service in the tenant namespace, whatever the
addon's mode. Its endpoints are the virt-launcher pods of the cluster's nodes,
written by `kubevirt-eps-controller`, and its target ports are NodePorts.

### The problem

What is broken on `main`, for a client pod in tenant A:

1. The ingress controller of a tenant B that is not an ancestor of A
   (including the host ingress, from depth 2, see above).
2. An application of tenant B that publishes a `LoadBalancer` Service: postgres,
   mariadb, mongodb, redis, valkey, kafka, nats, rabbitmq, opensearch, qdrant,
   openbao and tcp-balancer when `external` is set, and vpn when `externalIPs`
   is empty. Some of these Services come from an operator or a nested chart
   rather than from the application's own templates (Part A).
3. Any `LoadBalancer` Service of a Kubernetes cluster of tenant B: the addon
   in `LoadBalancer` mode, and every Service the tenant creates itself.

Cases 1 and 2 have a selector; case 3 does not. VMs are not in the list: they
are fixed (above). In each case, a tenant that
exposes a service publicly expects it to be reachable from another tenant's
project, as it is from the Internet and as it already is for a VM.

## Goals

- A pod of any tenant reaches the public IP of an opted-in `LoadBalancer`
  Service on its declared ports.
- Part A opts in every Service of cases 1 and 2 for which a label route exists
  (all of them but RabbitMQ's, see Part A); Part B opts in the CCM Services of
  case 3 with `externalTrafficPolicy: Local` and no
  `loadBalancerSourceRanges`. What remains broken is listed,
  not implied.
- No tenant gains access to another tenant's pods beyond what each part grants:
  - Part A: the backends of an opted-in Service, on its backend ports, by
    whatever address reaches them on those ports (public IP, ClusterIP or pod
    IP). A `toServices` grant cannot tell these apart (Security).
  - Part B: the public IP of an opted-in Service, on its declared ports only.
    Its ClusterIP, its backends and the other ports of the tenant cluster stay
    unreachable.
- Opt-in is set by the platform only. Part A is switched per kind of
  application, platform-wide. Part B is switched platform-wide for the CCM
  Services created afterwards, applied to existing ones Service by Service
  with a runbook step, and rolled back per Service (Switches).
- A Service whose owner restricted its sources is not opened by this change.
- The VM mode is unchanged.

### Non-goals

- Limiting Part A's grant to the public IP. Reaching an opted-in backend by
  ClusterIP or pod IP on its backend ports is accepted, as it already is for
  the ancestor ingress rule.
- Cross-tenant access through ClusterIPs or service names to anything that
  is not an opted-in backend.
- Distinguishing tenants among in-cluster clients at the backend.
- Replacing MetalLB or Cilium for Services that do not opt in.
- Fixing the ancestor derivation of the existing rule (Context).

## Design

### Part A: a policy rule for Services with a selector

The tenant egress CCNP gains one rule per kind of application, on a label the
platform sets on the published Service. The key is
`networking.cozystack.io/cross-tenant`; its value is the kind name of the
table below (`postgres`, `mariadb`, `mongodb`, `redis`, `valkey`, `kafka`,
`nats`, `opensearch`, `qdrant`, `openbao`, `tcp-balancer`, `vpn`, `ingress`,
and `rabbitmq` once its route is verified):

```yaml
  - toServices:
    - k8sServiceSelector:
        selector:
          matchLabels:
            networking.cozystack.io/cross-tenant: postgres
    toPorts:
    - ports:
      - port: "5432"
        protocol: TCP
```

Cilium matches the selector against Services in every namespace (empty
`namespace`) and turns each matched Service's own selector into an endpoint
selector **scoped to that Service's namespace**
(`newEndpointSelectorForServiceSelector` in `pkg/policy/k8s/service.go`). The
grant is therefore exactly "the backends of these Services, on these ports",
which is what the post-translation verdict needs.

The ports of a rule are the **backend** ports, not the Service's: Cilium
evaluates the translated destination, so tcp-balancer, which publishes 80 and
443 on container ports 8080 and 8443, needs the latter. The tenant chart renders
the rules from one map of kind to ports, so adding a kind is one entry there and
one label in its chart.

Where the label is set, per kind. Paths are under `packages/apps/`; "chart"
means the application's own template, the other rows go through an operator's
CR or a nested chart's values. Inventory read on `main` at `f203a47af`; the
external Services match the table of cozystack/community#87, §4.

| Kind | Published Service(s) | Where the label is set | Backend ports |
|---|---|---|---|
| postgres | `<r>-external-write` | chart, `postgres/templates/external-svc.yaml` | 5432 |
| mariadb, `replicas == 1` | `<r>` | `MariaDB` CR, `spec.service.metadata.labels` (`mariadb/templates/mariadb.yaml`) | 3306 |
| mariadb, `replicas > 1` | `<r>-primary` | `MariaDB` CR, `spec.primaryService.metadata.labels` | 3306 |
| mongodb, sharded | `<r>-external` (mongos) | chart, `mongodb/templates/external-svc.yaml` | 27017 |
| mongodb, replicaset | one per pod | `PerconaServerMongoDB` CR, `replsets[].expose.labels`, the Service labels since `serviceLabels` was deprecated (`mongodb/templates/mongodb.yaml`) | 27017 |
| redis | `<r>-external-lb` | chart, `redis/templates/service.yaml` | 6379 (target port `redis`) |
| valkey | `<r>-external-lb` | chart, `valkey/templates/service.yaml` | 6379 (target port `redis`) |
| kafka | external bootstrap, and one per broker | Strimzi `Kafka` CR, `spec.kafka.template.externalBootstrapService.metadata.labels` and `spec.kafka.template.perPodService.metadata.labels` (`kafka/templates/kafka.yaml`) | 9094 |
| nats | `<r>` | nested chart values, `service.merge.metadata.labels` (`nats/templates/nats.yaml`) | 4222, the only client port the app enables |
| opensearch | `<r>-external`, and `<r>-dashboards-external` with `dashboards.enabled` | chart, `opensearch/templates/external-svc.yaml`, both Services | 9200; 5601 |
| qdrant | `<r>` | nested chart values, `service.additionalLabels` (`qdrant/templates/qdrant.yaml`) | 6333, 6334, 6335 |
| openbao | `<r>`; `<r>-active` and `<r>-standby` when `replicas > 1`; `<r>-ui` with `ui` (default on) | nested chart values: `server.service.extraLabels`, and in HA `server.service.active.extraLabels` and `server.service.standby.extraLabels`, which the chart reads separately and which inherit `server.service.type`; `ui.extraLabels` (`openbao/templates/openbao.yaml`) | 8200, 8201; 8200 |
| tcp-balancer | `<r>-haproxy` | chart, `tcp-balancer/templates/service.yaml` | 8080, 8443, 6443, 50000 |
| vpn | `<r>-vpn`, when `externalIPs` is empty | chart, `vpn/templates/service.yaml`, `LoadBalancer` branch only | 40000 TCP and UDP |
| ingress | the controller's Service | `packages/extra/ingress/templates/nginx-ingress.yaml`, `controller.service.labels` | 80, 443 |
| **rabbitmq** | `<r>` | **none verified**, see below | — |

**rabbitmq stays broken for now.** `RabbitmqCluster.spec.service` takes
annotations only; a label needs `spec.override.service.metadata.labels`, and
`rabbitmq/templates/rabbitmq.yaml` deliberately renders no `override.service`:
the override is a strategic merge against ports the operator regenerates, and
re-adding a port on a LoadBalancer drives a NodePort reallocation loop. A
metadata-only override adds no port, so it may avoid both, but that has to be
shown on a lab against the shipped operator before the chart uses it. Until
then a rabbitmq published with `external` is reachable from the Internet and
not from other tenants, as today.

For the ingress controllers, an equivalent and smaller change is to extend the
existing ancestor rule to `cozystack.io/service: ingress` pods in every
namespace, on 80 and 443. It relies on the same pod label as that rule.

The label is only set when the Service is published **and** carries no source
restriction: wherever cozystack/community#87 routes `sourceRanges`, the label is
left off when it is set, so the rule never opens a Service its owner
restricted. Tenants do not set labels on these Services; the platform's charts
and CRs render them. The label is rendered whether or not the kind is
switched on: without a matching rule it grants nothing, and the switch stays
in one place, the tenant chart (Switches).

What the backend sees: the client pod's IP and identity, not `world`. Tenant
ingress policies admit `cluster` already, so nothing else changes on that side.

### Part B: an L4 mode in cozy-proxy for CCM Services

#### Why not a policy rule

A `toServices` rule on a selectorless Service becomes a CIDR selector on its
EndpointSlice IPs, which matches pods only with `policy-cidr-match-mode=pods`.
Cozystack ships Cilium 1.20.2 since `0f31e7a14`, whose agent accepts that
value (cilium/cilium#45194). The lab run of Part B was on 1.19.5, before that
bump.

The mode is agent-wide, and it changes what every CIDR rule of the cluster
matches, not only the platform's. Tenants write SecurityGroups whose `toCIDR`
becomes a Cilium CIDR rule (`pkg/registry/sdn/securitygroup/cilium.go`). With
`pods` enabled, a tenant's `toCIDR` covering the pod CIDR would match other
tenants' pods, so a tenant could grant itself reach into another tenant. That
is what rules the mode out, whatever the version. Cilium's documentation also
advises against it by default (an identity per pod matched by a CIDR selector).

Without the mode, the remaining policy shapes are wider than the need. The
backends are the virt-launcher pods of whole clusters and their target ports
are NodePorts allocated per Service. A single shared rule with static ports
would have to list the NodePort range, and would open it on every tenant
cluster to every tenant, including NodePorts no tenant declared as
LoadBalancer ports. `toEndpoints` on virt-launcher pods has no Service to take
its ports from, so it has the same exposure.

#### Opt-in

A CCM Service is taken over only when it carries both:

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
announces the IP. The label is the trust anchor: the CCM copies every
annotation of the tenant Service onto the infra Service, but not its labels, so
a tenant cannot opt in by itself.

Both are set by a new CCM patch in
`packages/apps/kubernetes/images/kubevirt-cloud-provider/patches`, switched on
from `packages/apps/kubernetes/templates/cloud-config.yaml` (Switches). When
the CCM creates an infra Service with the switch on, the patch sets both if
the tenant Service has `externalTrafficPolicy: Local` and no
`loadBalancerSourceRanges`. On every other infra Service it removes a copied
`service.cilium.io/type`, so a tenant cannot make its own IP dark either.

The patch acts at creation only. The upstream CCM writes an infra Service's
metadata when it creates it and later updates only its ports
(`EnsureLoadBalancer` and `updateLoadBalancerServicePorts` in
`pkg/provider/loadbalancer.go`), so the switch takes effect cluster by cluster
as Services are created, and removing the annotation from one Service is not
undone by the CCM. Existing infra Services are not migrated by the platform:
the runbook adds the label, then the annotation, Service by Service, with the
same conditions as the patch.

A Service with `loadBalancerSourceRanges` is left to Cilium, which enforces
them: the patch does not opt it in, and cozy-proxy refuses one that carries
the label anyway, as it refuses eTP `Cluster`. A refused Service has its IP
guarded and its traffic dropped rather than looping; the runbook removes the
annotation to hand it back to Cilium.

#### Datapath

cozy-proxy gets a second, independent mode behind `--enable-l4-loadbalancer`
(chart value `l4LoadBalancer.enabled`, off by default), in its own nftables
table `ip cozy_proxy_l4`, rebuilt atomically on every change:

- `guard`, on every node: drops new packets to an L4 IP on an undeclared port.
  Without it, such a packet is routed to the gateway and back, since the IP is
  no longer local once Cilium releases it.
- `translate`, on every node that can receive the IP's traffic: stateful DNAT,
  with port translation and round-robin, to the **local** ready backends from
  the EndpointSlices. A node translates a port when it is MetalLB's L2
  announcer for the IP, or when it hosts a ready backend for that port. The
  second condition is what BGP mode needs: with eTP `Local`, MetalLB advertises
  the IP from every node with a ready backend, and the routers spread flows
  across them with ECMP. A node that advertises nothing receives no traffic
  for the IP, so translating there changes nothing. In L2 mode, eTP `Local` is
  what makes MetalLB pick an announcer that has a backend; an announcer
  without one drops the port.
- `masq`, on the same nodes: masquerades sources that are node IPs only, so
  that replies to in-cluster clients come back through the translating node's
  conntrack. Internet and VM clients keep their source.
- `unnat`, on the same nodes, between conntrack and routing: writes the VIP
  back as the source of replies to flows that were not masqueraded. Conntrack
  reverses the DNAT only in postrouting, after the route lookup, so without it
  a reply is routed with the backend's address as source. Nodes that route by
  source then send it through the default gateway rather than the public
  pool's uplink, and a stateful gateway drops a SYN-ACK whose SYN it never saw
  (seen in production: a VM client on the VIP's L2 could no longer reach a
  tenant ingress once they stopped sharing a node). Each `translate` rule marks
  the flow's conntrack entry with its VIP before the DNAT, and `unnat` reads
  the VIP back from the mark through a map. This is what Cilium did by
  reversing its translation before the route lookup, and what the VM mode
  does for its own addresses at `raw`. Masqueraded replies are left alone:
  they go to a node, which the VIP's routes do not reach.

Inputs: `Service`, `EndpointSlice`, `Node`, and MetalLB's `ServiceL2Status` for
the L2 announcer. Conntrack entries are purged as kube-proxy does: UDP on endpoint
removal, TCP only when the frontend goes away on this node. A DaemonSet restart
leaves the table in place until the new instance replaces it. Conntrack marks
were unused on the production and lab nodes (every entry at mark 0, OVS zones
included); a flow opened before the upgrade has no mark, matches nothing in
`unnat` and keeps its former path.

Part B targets the variants that run tenant clusters. Those run MetalLB and
kube-ovn; isp-slim runs neither, and has no iaas bundle, hence no CCM Service.

#### Admission guard on CCM Services

Because the CCM copies tenant annotations, a platform may want an admission
guard that refuses infra Services carrying copied MetalLB, external-dns or
Cilium annotations. Cozystack ships none today. If it adds one, it must allow
`service.cilium.io/type: ClusterIP` on a Service that also carries the
`lb-proxy` label, and refuse every other `service.cilium.io/*` key. A Kyverno
rule doing that was tested on a lab with Kyverno 1.18.2; it is in the
cozy-proxy design document, section 7.1.

### Switches

Every switch is a value of the `cozystack.cozystack-platform` Package, set by
the platform administrator and carried to the applications as a `_cluster`
key of the `cozystack-values` Secret, like `gateway-class-name`. Tenants
cannot set it: the API refuses application values whose key starts with `_`
(`validateNoInternalKeys` in `pkg/registry/apps/application/rest.go`).

| Platform value | Type, default | `_cluster` key | Read by | Effect |
|---|---|---|---|---|
| `networking.crossTenantLoadBalancer.kinds` | list of kind names, `[]` | `cross-tenant-lb-kinds` (comma-separated) | `packages/apps/tenant/templates/networkpolicy.yaml` | one Part A rule per listed kind |
| `networking.crossTenantLoadBalancer.ccmServices` | bool, `false` | `cross-tenant-lb-ccm` | `packages/apps/kubernetes/templates/cloud-config.yaml`, as `loadBalancer.crossTenantL4: true` read by the CCM patch | Part B opt-in on CCM Services created afterwards |
| `l4LoadBalancer.enabled` of the cozy-proxy chart | bool, `false` | — | `packages/system/cozy-proxy`, set on its Package | runs the L4 mode |

The CCM switch is platform-wide, not a value of the Kubernetes application,
so a tenant cannot turn it on for its own cluster. Turning `ccmServices` on
without `l4LoadBalancer.enabled` makes the opted-in IPs dark; the two live in
different Packages, so the order is the rollout's (cozy-proxy first, CCM
second) rather than a render check.

## User-facing changes

- Tenants: the public IP of an opted-in Service becomes reachable from every
  tenant. Under Part A the backend sees the client pod; under Part B it sees
  the translating node's join-network address (`100.64.x` on kube-ovn).
- Admins: the three platform values of Switches. No new CRD or API.
- Docs: what each Service type shows as client address, for tenants writing
  allowlists.

## Upgrade and rollback compatibility

- Default behavior unchanged until the switches are turned on. The Part A
  labels are rendered from the start and grant nothing on their own.
- Part A: listing or unlisting a kind adds or removes its rule; adding or
  removing the label on one Service adds or removes the grant to it.
- Part B, per Service: add the label (no effect), then the annotation; Cilium
  lets go and cozy-proxy programs the IP on its next sync. Connections opened
  through Cilium may be reset once. Rollback: remove the annotation; Cilium takes
  the IP back and cozy-proxy stops programming it at the same time. Removing
  only the label leaves the IP dark, so the runbook always removes the
  annotation.
- Part B, whole mode: turn `ccmServices` off, remove every annotation, then
  disable the mode, which deletes the table. Nothing is irreversible.

## Security

Part A:

- The grant is the backends of labelled Services on their declared ports, in
  their own namespaces. A tenant reaching them can also do so by pod IP or
  ClusterIP on those ports: the same exposure the ancestor ingress rule already
  accepts, on ports that are public anyway.
- Services with a source restriction are not labelled (#87). Without that
  condition, the rule would let every tenant through a restriction that Cilium's
  socket LB already does not apply to in-cluster clients.

Part B:

| Client | Source seen by the backend | Cilium identity at the backend |
|---|---|---|
| Internet | its own IP | `world` |
| VM with a public IP (VM mode) | the VM's public IP | `world` |
| pod or node process, any node | the translating node's join IP (`100.64.x`) | `world` |

- No tenant gains access to another tenant's pods: the client's egress policy is
  unchanged and still sees `world`; only declared ports are translated, every
  other port to the IP is dropped by `guard`.
- In-cluster clients are indistinguishable from one another at the backend.
- A tenant cannot opt in: the label is set by the CCM patch or the runbook
  only, the switch is a platform value (Switches), and the patch removes a
  copied `service.cilium.io/type` from every Service it does not opt in. A
  tenant still controls the ports and the eTP of its CCM Service, as today.
- `loadBalancerSourceRanges` is enforced by Cilium on the frontend this mode
  removes, so a Service that sets it is not opted in and is refused by
  cozy-proxy if labelled anyway. The owner's restriction keeps applying, and
  such a Service stays unreachable from other tenants, as today. Enforcing the
  ranges in `guard` instead is left to a later change if it is asked for.
- New RBAC for cozy-proxy: read EndpointSlices, Nodes and MetalLB
  `ServiceL2Status`.

## Failure and edge cases

- Part A, label on a Service whose selector matches no pod → the rule grants
  nothing.
- Part B, announcer moved by MetalLB → the new announcer programs the DNAT after
  its `ServiceL2Status` appears; in-flight connections are lost, as with any L2
  failover. Lab: one failed probe out of about 110 at a 0.5 s interval.
- Part B, BGP mode, a node advertises the IP without translating it → the
  flows hashed to that node leave untranslated for the default gateway. This
  happened on a production cluster running MetalLB in L2 and BGP at once,
  while only the L2 announcer translated: the gateway dropped those flows, and
  the clients whose flows hashed there could not connect. Translating on every
  node with a ready backend is the fix (Datapath).
- Part B, no ready local backend on the L2 announcer → the port is dropped, not
  forwarded.
- Part B, undeclared port or ICMP to an L4 IP → dropped on every node; no
  routing loop.
- Part B, eTP `Cluster` Service, or one with `loadBalancerSourceRanges`,
  labelled → refused and guarded; logged.
- Part B, reply to a non-masqueraded client → leaves with the VIP as source
  (`unnat`); a flow opened before the upgrade carries no mark and keeps its
  former path until it closes.
- Part B, cozy-proxy restart → the table stays in the kernel; no interruption.
- Part B, high connection churn from node-masqueraded clients → about 0.06 to
  0.1 % of connections took over a second on the lab (SYN-ACK dropped inside
  OVS/OVN, recovered by retransmission). Mitigated with `masquerade
  fully-random`; Internet clients are not affected. Open question 2.

## Testing

- Part A: chart unit tests, per row of the table, that the label (or the CR
  or values field that carries it) is rendered when the Service is published
  and absent when `sourceRanges` is set; a unit test of the tenant chart that
  renders one rule per listed kind with its ports, and none by default.
- Part A, e2e per kind of the table: the application is published in one
  tenant, the test asserts that every published Service carries the label, then
  opens a connection from another tenant to the public IP on each declared
  port. For the rows that go through an operator or a nested chart (mariadb
  in both modes, mongodb replicaset, kafka bootstrap and brokers, nats,
  qdrant, openbao with and without HA) the label assertion is the only check
  that it reaches the Service, which is what failed for rabbitmq. The
  connection catches drift between the tenant chart's port map and the
  application's backend ports. Plus a refused connection to a non-labelled pod
  of the same tenant.
- rabbitmq, before its row is filled: a metadata-only `override.service` on a
  published `RabbitmqCluster`, checked for NodePort churn over several operator
  reconciles.
- Part B, CCM patch: unit tests next to the patched package, which the CCM
  image build already runs. The label and the annotation are set only with the
  switch on, eTP `Local` and no `loadBalancerSourceRanges`; with the switch off,
  eTP `Cluster` or source ranges, neither is set and a copied
  `service.cilium.io/type` is removed.
- Part B, in cozystack/cozy-proxy#25: unit tests for selection, desired state
  and purge decisions; kernel tests in network namespaces (golden `nft list`,
  real TCP through three namespaces: translation, round-robin, source kept or
  masqueraded, replies sourced from the VIP, guard, purge, rebuild with an
  open connection); a refusal test for a labelled Service with
  `loadBalancerSourceRanges`; lab run on
  Cilium 1.19.5 with kube-ovn and MetalLB L2 against the real CCM ingress of a
  tenant cluster, VM clients, announcer failover, DaemonSet and ingress
  restarts, eTP `Cluster` refusal, 300 connections per second without
  keep-alive.
- Part B, e2e to add: a cross-tenant connection to a `LoadBalancer` Service
  created inside a tenant cluster.
- Part B, lab cases to add: MetalLB in BGP mode with backends on two nodes,
  checking that flows hashed to either node connect; and a VM client of an L4
  IP hosted on a node that translates it. The lab only covered VM clients on
  other nodes. On the client's node the VM mode rewrites the VM's source at
  `raw`, before conntrack, and the L4 mode does not masquerade it, since it is
  not a node IP; the reply should then match conntrack before the VM mode's
  `ingress_dnat` at `mangle`. That is the reasoning, not a measurement.

## Rollout

1. Part A: the labels and the tenant chart's rules, all off; then kinds are
   listed in `crossTenantLoadBalancer.kinds`; rabbitmq once its route is
   verified.
2. Part B: cozy-proxy release with the mode off by default; then the CCM patch
   and, if one is added, the admission guard; then `l4LoadBalancer.enabled`,
   then `ccmServices` for new Services, and the runbook step for existing
   ones with eTP `Local`, per Service.

Each step can be rolled back on its own.

## Open questions

1. **Part B announcer source.** The L4 mode reads MetalLB L2's
   `ServiceL2Status` and, for BGP, the local ready backends (Datapath). Should
   the announcer source be behind an interface for other implementations?
2. **Residual SYN-ACK loss under churn** for node-masqueraded clients: accept
   it, or fix it on the OVN side (conntrack timeouts or ACLs) first?
3. **Admission guard.** Should Cozystack ship a guard on CCM Services, and in
   which package?
4. **Gateway public IPs.** Once databases and HTTP move behind a Cilium Gateway,
   is the Gateway's public IP reachable across tenants? Part A's rule targets
   the per-release Services and would need its own counterpart there.

## Alternatives considered

### For Services with a selector

- **The L4 mode for every type.** The first version of this proposal. It
  works, but it adds a stateful datapath where a policy rule is enough, and it
  does nothing on isp-slim (Cilium L2 announcements, no MetalLB). Rejected in
  favor of Part A, following review.
- **Allow `cluster` in the tenant egress policy.** Removes tenant isolation;
  rejected.

### For CCM Services

- **`toServices` with `policy-cidr-match-mode=pods`.** Accepted by the shipped
  agent since Cilium 1.20, but the mode is agent-wide: a tenant SecurityGroup
  whose `toCIDR` covers the pod CIDR would then match other tenants' pods
  (Part B, Why not a policy rule).
- **`toEndpoints` on virt-launcher pods.** Has no Service to take its ports
  from, so it opens the NodePort range like the shared rule.
- **`exposeMethod: Proxied` everywhere.** Requires every domain to be declared
  on the Kubernetes application beforehand, carries only HTTP and HTTPS, and
  does not cover the `LoadBalancer` Services tenants create themselves.
- **Wait for the Gateway.** The Gateway serves the platform's HTTP and database
  exposure; it does not realize the `LoadBalancer` Services a tenant creates in
  its own cluster, which remain CCM Services.

### Datapath variants for Part B

- **DNAT on the announcer without masquerade.** A pod on another node is
  masqueraded to its node IP by kube-ovn; the backend answers that node
  directly through OVN, outside the announcer's conntrack, and the client gets
  a RST. Observed on the lab.
- **DNAT on every node plus masquerade of every source from the pod
  interface.** Breaks VM clients: the VM mode rewrites a VM's source
  statelessly before conntrack, and with a masquerade on the VM's own node the
  reply is de-masqueraded only after the VM mode has already rewritten its
  destination, so it is never turned back into the VM's pod IP. Observed on
  the lab. The retained datapath also translates on every node with a ready
  backend, but masquerades node IPs only, so a VM's source is left alone.
- **`service.kubernetes.io/service-proxy-name`**, as the VM mode does, instead
  of `service.cilium.io/type: ClusterIP`. Cilium then drops the ClusterIP and
  the NodePort as well, changing in-cluster behavior beyond the public IP.

### Opt-in mechanism for Part B

- **`loadBalancerClass`**, the integration point recorded in
  `loadbalancer-announcer-neutrality`. A Service with a non-default class is
  skipped by MetalLB, which would then neither allocate nor announce its IP.
  The L4 mode is not another LoadBalancer implementation: it relies on MetalLB
  for both and only replaces Cilium's translation. `loadBalancerClass` is also
  immutable, which would turn the per-Service rollback into a Service
  re-creation and an IP change.
