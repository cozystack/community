# Cross-tenant reachability of LoadBalancer public IPs

- **Title:** `Cross-tenant reachability of LoadBalancer public IPs`
- **Author(s):** `@mattia-eleuteri`
- **Date:** `2026-10-06`; revised `2026-10-07`
- **Status:** Draft

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
  selectorless): no policy rule can admit them without opening the NodePort
  range of every tenant cluster to every tenant. For these only, an opt-in L4
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
  `loadBalancerSourceRanges`, so it must enforce or refuse it.
- **Related:** `design-proposals/loadbalancer-announcer-neutrality` — MetalLB
  is the shipped announcer and `loadBalancerClass` the integration point for
  another implementation. Part B keeps MetalLB as allocator and announcer; see
  Alternatives for why it does not use `loadBalancerClass`.
- **Related:** `design-proposals/external-database-exposure` — databases move
  towards Gateway API TLS-passthrough. Part A covers the per-release
  `LoadBalancer` Services that remain. The Gateway does not change Part B: the
  Services it serves are created by tenants inside their own clusters.
- **Out of scope:** VMs (fixed, below), IPv6, UDP, `externalTrafficPolicy:
  Cluster` in Part B, MetalLB in BGP mode.

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
  case 3 with `externalTrafficPolicy: Local`. What remains broken is listed,
  not implied.
- No tenant gains access to another tenant's ClusterIPs, to pods that are not
  backends of an opted-in Service, or to undeclared ports of a tenant cluster.
- Opt-in is set by the platform only, per Service, with a per-Service rollback.
- A Service whose owner restricted its sources is not opened by this change.
- The VM mode is unchanged.

### Non-goals

- Cross-tenant access through ClusterIPs or service names.
- Distinguishing tenants among in-cluster clients at the backend.
- Replacing MetalLB or Cilium for Services that do not opt in.
- Fixing the ancestor derivation of the existing rule (Context).

## Design

### Part A: a policy rule for Services with a selector

The tenant egress CCNP gains one rule per kind of application, on a label the
platform sets on the published Service:

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
| openbao | `<r>`, and `<r>-ui` with `ui` (default on) | nested chart values, `server.service.extraLabels` and `ui.extraLabels` (`openbao/templates/openbao.yaml`) | 8200, 8201; 8200 |
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
and CRs render them.

What the backend sees: the client pod's IP and identity, not `world`. Tenant
ingress policies admit `cluster` already, so nothing else changes on that side.

### Part B: an L4 mode in cozy-proxy for CCM Services

#### Why not a policy rule

A `toServices` rule on a selectorless Service becomes a CIDR selector on its
EndpointSlice IPs, which matches pods only with `policy-cidr-match-mode=pods`.
That value is accepted from Cilium 1.20 (cilium/cilium#45194); Cozystack ships
1.19.5, whose agent accepts only `nodes`, and Cilium's documentation advises
against it by default (an identity per pod matched by a CIDR selector). Even
then the rule is L3 plus static ports, and the backends are the virt-launcher
pods of whole clusters: one cluster-wide rule would open the NodePort range of
every tenant cluster to every tenant, including NodePorts no tenant declared as
LoadBalancer ports. `toEndpoints` on virt-launcher pods has the same exposure.

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
from `packages/apps/kubernetes/templates/cloud-config.yaml`, only on Services
with `externalTrafficPolicy: Local`, overriding any copied value. Existing
infra Services need a one-shot patch. A Service in `Cluster` is refused: its IP
is guarded and its traffic dropped rather than looping.

#### Datapath

cozy-proxy gets a second, independent mode behind `--enable-l4-loadbalancer`
(chart value `l4LoadBalancer.enabled`, off by default), in its own nftables
table `ip cozy_proxy_l4`, rebuilt atomically on every change:

- `guard`, on every node: drops new packets to an L4 IP on an undeclared port.
  Without it, such a packet is routed to the gateway and back, since the IP is
  no longer local once Cilium releases it.
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

## User-facing changes

- Tenants: the public IP of an opted-in Service becomes reachable from every
  tenant. Under Part A the backend sees the client pod; under Part B it sees
  the announcer's join-network address (`100.64.x` on kube-ovn).
- Admins: a platform switch per Service type for Part A, and
  `l4LoadBalancer.enabled` on cozy-proxy plus the CCM switch for Part B. No new
  CRD or API.
- Docs: what each Service type shows as client address, for tenants writing
  allowlists.

## Upgrade and rollback compatibility

- Default render unchanged until the switches are turned on.
- Part A: adding or removing the label adds or removes the grant; removing the
  rule from the tenant chart restores today's behavior.
- Part B, per Service: add the label (no effect), then the annotation; Cilium
  lets go and cozy-proxy programs the IP on its next sync. Connections opened
  through Cilium may be reset once. Rollback: remove the annotation; Cilium takes
  the IP back and cozy-proxy stops programming it at the same time. Removing
  only the label leaves the IP dark, so the runbook always removes the
  annotation.
- Part B, whole mode: remove every annotation, then disable the mode, which
  deletes the table. Nothing is irreversible.

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
| pod or node process, any node | the announcer's join IP (`100.64.x`) | `world` |

- No tenant gains access to another tenant's pods: the client's egress policy is
  unchanged and still sees `world`; only declared ports are translated, every
  other port to the IP is dropped by `guard`.
- In-cluster clients are indistinguishable from one another at the backend.
- A tenant cannot opt in: the label is set by the platform only, and a copied
  `service.cilium.io/type: ClusterIP` without it only makes the tenant's own IP
  dark. A tenant still controls the ports and the eTP of its CCM Service, as
  today.
- `loadBalancerSourceRanges` is enforced by Cilium on the frontend this mode
  removes. The prototype does not read the field yet; before acceptance it must
  either enforce it (a per-IP set of allowed sources in `guard`) or refuse such
  a Service, as it refuses eTP `Cluster`.
- New RBAC for cozy-proxy: read EndpointSlices, Nodes and MetalLB
  `ServiceL2Status`.

## Failure and edge cases

- Part A, label on a Service whose selector matches no pod → the rule grants
  nothing.
- Part B, announcer moved by MetalLB → the new announcer programs the DNAT after
  its `ServiceL2Status` appears; in-flight connections are lost, as with any L2
  failover. Lab: one failed probe out of about 110 at a 0.5 s interval.
- Part B, no ready local backend on the announcer → the port is dropped, not
  forwarded.
- Part B, undeclared port or ICMP to an L4 IP → dropped on every node; no
  routing loop.
- Part B, eTP `Cluster` Service opted in → refused and guarded; logged.
- Part B, cozy-proxy restart → the table stays in the kernel; no interruption.
- Part B, high connection churn from node-masqueraded clients → about 0.06 to
  0.1 % of connections took over a second on the lab (SYN-ACK dropped inside
  OVS/OVN, recovered by retransmission). Mitigated with `masquerade
  fully-random`; Internet clients are not affected. Open question 2.

## Testing

- Part A: chart unit tests, per row of the table, that the label is rendered
  when the Service is published and absent when `sourceRanges` is set; a unit
  test of the tenant chart that renders one rule per kind with its ports; an
  e2e connection from one tenant to another tenant's external Postgres and
  ingress, and a refused connection to a non-labelled pod of the same tenant.
- rabbitmq, before its row is filled: a metadata-only `override.service` on a
  published `RabbitmqCluster`, checked for NodePort churn over several operator
  reconciles.
- Part B, in cozystack/cozy-proxy#25: unit tests for selection, desired state
  and purge decisions; kernel tests in network namespaces (golden `nft list`,
  real TCP through three namespaces: translation, round-robin, source kept or
  masqueraded, guard, purge, rebuild with an open connection); lab run on
  Cilium 1.19.5 with kube-ovn and MetalLB L2 against the real CCM ingress of a
  tenant cluster, VM clients, announcer failover, DaemonSet and ingress
  restarts, eTP `Cluster` refusal, 300 connections per second without
  keep-alive.
- Part B, e2e to add: a cross-tenant connection to a `LoadBalancer` Service
  created inside a tenant cluster.

## Rollout

1. Part A: the egress rules, then the labels kind by kind; rabbitmq once its
   route is verified.
2. Part B: cozy-proxy release with the mode off by default; then the CCM patch
   and, if one is added, the admission guard; then CCM Services with eTP
   `Local`, per Service.

Each step can be rolled back on its own.

## Open questions

1. **Part B announcer source.** Phase 1 reads MetalLB L2's `ServiceL2Status`.
   Should it be behind an interface, and is BGP mode, where every node
   advertises, required before acceptance?
2. **Residual SYN-ACK loss under churn** for node-masqueraded clients: accept
   it, or fix it on the OVN side (conntrack timeouts or ACLs) first?
3. **Admission guard.** Should Cozystack ship a guard on CCM Services, and in
   which package?
4. **Gateway public IPs.** Once databases and HTTP move behind a Cilium Gateway,
   is the Gateway's public IP reachable across tenants? Part A's rule targets
   the per-release Services and would need its own counterpart there.
5. **Label keys.** `networking.cozystack.io/cross-tenant` and
   `networking.cozystack.io/lb-proxy` are proposals.

## Alternatives considered

### For Services with a selector

- **The L4 mode for every type.** The first version of this proposal. It
  works, but it adds a stateful datapath where a policy rule is enough, and it
  does nothing on isp-slim (Cilium L2 announcements, no MetalLB). Rejected in
  favor of Part A, following review.
- **Allow `cluster` in the tenant egress policy.** Removes tenant isolation;
  rejected.

### For CCM Services

- **`toServices` with `policy-cidr-match-mode=pods`**, or **`toEndpoints` on
  virt-launcher pods.** Opens the NodePort range of every tenant cluster to
  every tenant (Part B, Why not a policy rule).
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
- **DNAT on every node plus masquerade.** Breaks VM clients: the VM mode
  rewrites a VM's source statelessly before conntrack, so the reply is never
  translated back. Observed on the lab.
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
