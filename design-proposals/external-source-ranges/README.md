# Source-IP restriction for externally published applications

- **Title:** `Source-IP restriction for externally published applications`
- **Author(s):** `@IvanHunters`
- **Date:** `2026-09-24`
- **Status:** Draft

## Overview

A tenant sets `external: true` on a managed database, gets a public address, and cannot say who may connect to it. There is no field for it on any application, and `SecurityGroup` cannot supply one: it selects pods and only widens a baseline that already admits `fromEntities: [world]`.

This proposal adds a `sourceRanges` list to the values of each application that publishes externally. Every chart routes it to whatever object actually carries its external Service — the Service the chart renders, the operator CR field, or the values of a nested chart — and §4 gives that route for every externally published application in the catalog, read one by one in the tree.

Two facts shape the design more than the API does. **An application is not one endpoint**: opensearch and openbao each publish two external Services at once, mongodb and kafka publish a variable number, and three applications decide *which object* carries the Service from a value other than `external`. And **`loadBalancerSourceRanges` does not close a Service**, because the node ports it also answers on are filtered only when `bpf.lbSourceRangeAllTypes` is on, which it is not.

## Scope and related proposals

- **Is a chart-values design, which [#29](https://github.com/cozystack/community/pull/29) was and which was closed.** That is the first thing a reviewer should weigh. #29 was anchored to "the chart renders one additive LoadBalancer Service per target", and its review found that false for most engines. This proposal renders no Service and adds no target: it sets one field on the object each chart already emits, and §4 is the per-chart evidence #29 lacked. The second half of #29's closing — that it "left the discovery-engine address write-back loop without an owner" — does not arise here, because nothing in this design allocates or advertises an address.
- **Does not close the gap [#45](https://github.com/cozystack/community/pull/45) names.** #45 asks for *per-address* ACL and says consumers "must assume application-level ACL granularity" until `SecurityGroup` grows an attachment-scoped target. This gives per-application granularity on the `external: true` path. It is the thing #45 tells consumers to assume, not the thing it defers; §8 covers what happens when attachments land, including the overlap neither proposal currently resolves.
- **Does not extend to** `external-database-exposure` (Accepted). Under SNI consolidation every database shares the tenant Gateway's address, which no per-application field reaches.
- **Does not change** `SecurityGroup` (cozystack#2922).
- **Depends on** [cozystack#4430](https://github.com/cozystack/cozystack/issues/4430) being understood, not necessarily fixed: §3 explains why that bug changes which applications need the Cilium flag.

## Context

### What a tenant can do today

Nothing, for a managed database. `loadBalancerSourceRanges` appears nowhere in `packages/apps`. The `Ingress` application has a `whitelist`, but it renders into the nginx controller's ConfigMap (`packages/extra/ingress/templates/nginx-ingress.yaml`), so it is all-hosts-or-nothing, and `ingresses` is absent from `cozy:tenant:admin:base`. `TCPBalancer` has a working HAProxy `whitelist`, which makes "put a TCPBalancer in front of it" the only self-service answer: an extra hop, application and address per endpoint.

### Why `SecurityGroup` is not it

`pkg/apis/sdn/DESIGN.md` states it as a non-goal: "We do not flip the tenant baseline to default-deny in this change… Cilium allow-rules are additive, so a SecurityGroup can only *widen* — it cannot yet restrict (`ingress: []` does not deny)." Its selector is a membership label on pods; traffic arriving on a LoadBalancer address is not addressed by it.

### Measured on a live cluster

Talos, Kubernetes v1.35.3, Cilium 1.19.5, default `kubeovn-cilium` variant, `kube-proxy-replacement: true`, `cni-chaining-mode: generic-veth`.

The filter is programmed correctly in chaining mode, which could not be assumed — `toFQDNs` is silently ineffective on the same stack (cozystack#3820):

```text
# cilium-dbg bpf lb list
<vip>:80/TCP (0)  0.0.0.0:0 (142) (0) [LoadBalancer, check source-range]
```

Removing the field cleans up; cilium#32617 (service stranded after deletion, closed as not planned) does not reproduce on 1.19.5.

The filter does not cover the Service's node ports:

```text
<vip>:80/TCP           [LoadBalancer, check source-range]
<node-a>:32165/TCP     [NodePort]
0.0.0.0:32165/TCP      [NodePort]
```

In-cluster traffic bypasses it entirely: clients on a permitted node, a non-permitted node and in a pod all reached a restricted service, because `Socket LB: Enabled` rewrites the destination at the socket layer before the tc hook.

**Not measured: refusal from a client outside the cluster.** The test cluster's LB subnet is not routable from anywhere available, cluster nodes carry reserved identities rather than `world`, and pods sit behind Socket LB. §Rollout puts this before the platform change rather than after it.

### The problem

A tenant wants its published endpoints reachable only from a CDN's egress ranges, and a database replica reachable only from two named addresses. The current answer is a TCPBalancer, which they correctly read as a workaround.

## Goals

- A tenant restricts who may connect to an application it publishes, in that application's own spec.
- Every external Service the route table marks as covered is restricted, including the second one where an application publishes two, and every branch where an application publishes through different objects.
- Each chart uses the field its own publishing object already accepts; no new object, no controller, no new RBAC.
- Applications whose publishing object has no such field refuse the value at render time instead of accepting one nothing enforces.
- The proposal states which frontends remain reachable rather than implying completeness.

### Non-goals

- **Per-endpoint granularity inside one application.** One list covers all of an application's external Services; §2 argues why, and Open question 1 carries the case against.
- **Covering every frontend.** `healthCheckNodePort` is served by cilium-agent's userspace listener, outside the BPF LB map, and stays reachable. Seven charts set `externalTrafficPolicy: Local` and so allocate one — postgres, redis, valkey, tcp-balancer, vm-instance, mongodb (both paths) and vpn — which is six of the seven applications Phase 3 covers, so the residual open frontend touches almost all of the first wave.
- **East-west policy, and isolating an endpoint from in-cluster clients.** Socket LB puts the second out of reach of this mechanism entirely.
- **HTTP through the shared ingress-nginx or a tenant Gateway**, and **the tenant Kubernetes API**, which is a ClusterIP Service behind an `ssl-passthrough` Ingress (`packages/apps/kubernetes/templates/cluster.yaml`). All three use a Service shared by the whole tenant.
- **Everything a tenant Kubernetes cluster publishes.** Two distinct surfaces, both out of reach of a field on the `Kubernetes` application. Its own ingress controller is a LoadBalancer Service whenever `exposeMethod: LoadBalancer` (`packages/apps/kubernetes/values.yaml`; the `NodePort` override in `templates/helmreleases/ingress-nginx.yaml` is rendered only for `Proxied`). And `templates/cloud-config.yaml` points cloud-provider-kubevirt at the tenant namespace with `selectorless: true`, so **any** LoadBalancer Service a guest-cluster user creates materialises there — an unbounded set that no value on the parent application can enumerate, let alone restrict. Restricting those belongs to whatever eventually governs guest-cluster exposure, not here.
- **Live synchronisation of a changing allow-list.** §7 gives the real latency.
- **Clusters using RobotLB.** The filter would see the Hetzner load balancer's address rather than the client's. Documented as unsupported; §3 says why it is not detected.
- **FQDN-based lists.** `toFQDNs` does not work on this stack.

## Design

### 1. What the inventory forces

Fourteen applications, sixteen rows in §4, and the gap between those two numbers is the whole point.

- **Two publish two Services at once:** opensearch (API and Dashboards) and openbao (API and UI). The second is gated on `dashboards.enabled`, which defaults **off**, and on `ui`, which defaults **on** — so openbao publishes two by default and opensearch does not.
- **Two publish a variable number:** mongodb in replicaset mode renders one per pod, kafka one bootstrap plus one per broker.
- **Three choose *which object* carries the Service from a value other than `external`:** mongodb on `sharding`, mariadb on `replicas`, vpn on `externalIPs`. (opensearch and openbao add a second Service rather than choosing a different carrier.)

A design assuming one endpoint per application, or one route per application, misses whichever half of each pair it did not look at. That is what sank the previous drafts.

The full table is §4. Three consequences first.

### 2. The field

```yaml
apiVersion: apps.cozystack.io/v1alpha1
kind: OpenSearch
spec:
  external: true
  sourceRanges:
    - 203.0.113.0/24
    - 198.51.100.7/32
```

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `sourceRanges` | `[]string` | `[]` | CIDRs allowed to reach **every** external Service this application publishes |

Empty means unrestricted — today's behaviour, and the only default that does not break every existing `external: true` on upgrade. There is no value meaning "deny all"; `external: false` is that.

**One list, all of the application's external Services.** OpenSearch publishes an API Service and a Dashboards Service; OpenBao publishes API and UI. A map keyed by endpoint would express "only Dashboards", but both applications already have a toggle for the second endpoint (`dashboards.enabled`, `ui`), so the case is reachable without new API. Adding endpoint keys means naming endpoints in a stable, tenant-visible vocabulary, which does not exist yet and which #45 is separately trying to establish. Open question 1 keeps this open.

### 3. Platform precondition, and who actually needs it

`bpf.lbSourceRangeAllTypes: true` makes the filter apply to node ports as well as the VIP. Without it a restricted Service stays reachable on every node.

**Which applications need it is not what the earlier drafts assumed.** Four charts already emit `allocateLoadBalancerNodePorts: false` — `postgres` (`external-svc.yaml:11`), `redis` (`service.yaml:15`), `valkey` (`service.yaml:14`), `mongodb` (`external-svc.yaml:18`) — plus `tcp-balancer` and `vm-instance`, so those Services have no node port to filter. They do it by accident: the guard is always true because `fromYaml` on a scalar yields a non-empty map (cozystack#4430). The flag is therefore needed for **opensearch, vpn, and every operator-rendered Service** (mariadb, mongodb's per-pod replicaset Services, kafka, nats, openbao, qdrant, rabbitmq), none of which suppress node ports.

Consequences, each verified:

- **Retroactive and global.** Every Service already carrying `loadBalancerSourceRanges` changes behaviour at the agent restart.
- **No kill switch.** `--enable-svc-source-range-check=false`, the escape hatch for regressions such as cilium#42334, is gone in 1.19; the running agent offers only `--bpf-lb-source-range-all-types`.
- **ClusterIP unaffected**, gated separately on `bpf.lbExternalClusterIP`, which is `false` and not overridden.

**RobotLB is out of scope, not detected and not worked around.** Where the Hetzner load balancer fronts a Service it is *its* address that reaches the node, not the client's, so a CIDR list would block the load balancer rather than an attacker. Two earlier drafts tried to handle this: first by refusing `sourceRanges` at render time, then by conditioning the flag on the bundle. Both are withdrawn. No reliable signal exists: `_network.tpl` reads `_cluster["bundle-enable"]`, which nothing writes on v1.x, and `_cluster["load-balancer-class"]` is `publishing.loadBalancerClass` for the host ingress, empty by default and never written by `packages/system/hetzner-robotlb`. `robotlb` is an `optional.default` package an operator enables deliberately, and the only platform logic consulting it has been inert since it was written (cozystack#4430). A detector that silently fails is worse than a documented limitation, and a bundle condition adds branching in two places to protect a configuration the platform does not otherwise track. `sourceRanges` does not work behind RobotLB, the documentation says so, and the flag is unconditional.

**Where the value goes.** `packages/system/cilium/values.yaml`, beside `kubeProxyReplacement: true`, which is the same kind of unconditional platform decision and carries its reasoning in a comment plus a rendered-value test (`tests/kube_proxy_replacement_test.yaml`). One merge detail to verify rather than assume: that file has no `bpf:` section today, while `values-kubeovn.yaml` has `bpf: {masquerade: false}`, and the default variant loads both. Distinct keys inside one map merge, so the test asserts the rendered ConfigMap carries both settings rather than one replacing the other. Guest clusters install `system/cilium` with no values files (`packages/core/platform/sources/kubernetes-application.yaml`) and are unaffected.

### 4. The routing table

This table is the proposal. Each row cites where it was read.

| Application | External Service(s) | Where it originates | Condition | Route for `sourceRanges` |
| --- | --- | --- | --- | --- |
| postgres | `<r>-external-write` | chart, `external-svc.yaml:7` | `external` | Service field |
| redis | `<r>-external-lb` | chart, `service.yaml:11` | `external` | Service field |
| valkey | `<r>-external-lb` | chart, `service.yaml:11` | `external` | Service field |
| tcp-balancer | `<r>-haproxy` | chart, `service.yaml:10` | `external` | Service field (see §5) |
| opensearch | `<r>-external` **and** `<r>-dashboards-external` | chart, `external-svc.yaml:21` and `:41` | `external`; second also `dashboards.enabled` | Service field on both |
| mongodb, sharded | `<r>-external` (mongos) | chart, `external-svc.yaml:9` | `external` **and** `sharding` | Service field |
| mongodb, replicaset | one per pod | CR, `mongodb.yaml:151` | `external` **and not** `sharding` | `replsets[].expose.loadBalancerSourceRanges` |
| mariadb, `replicas == 1` | `<r>` | CR, `mariadb.yaml:99` | `external` **and** `replicas == 1` | `spec.service.loadBalancerSourceRanges` |
| mariadb, `replicas > 1` | `<r>-primary` | CR, `mariadb.yaml:110` | `external` **and** `replicas > 1` | `spec.primaryService.loadBalancerSourceRanges` |
| kafka | bootstrap + one per broker | Strimzi listener, `kafka.yaml:41` | `external` | `listeners[].configuration.loadBalancerSourceRanges` (`040-Crd-kafka.yaml:338`) |
| nats | `<r>` | nested chart values, `nats.yaml:118` | `external` | `service.merge.spec.loadBalancerSourceRanges` |
| **openbao** (API **and** UI) | `<r>` and `<r>-ui` | values, `openbao.yaml:95` and `:101` | `external`; UI also `ui` (default **on**) | **none usable** — see below |
| vpn | `<r>-vpn` | chart, `service.yaml:10-18` | **`externalIPs` empty** — `external` selects only `externalTrafficPolicy` | Service field, LoadBalancer branch only |
| **qdrant** | `<r>` | nested chart values, `qdrant.yaml:41` | `external` | **none** — upstream chart offers `loadBalancerIP` only |
| **rabbitmq** | `<r>` | CR, `rabbitmq.yaml:85` | `external` | only `spec.override.service` — see §6 |
| **vm-instance** | `<r>` | chart, `service.yaml:18` | `external` | field settable, **not enforced** — see §6 |

Twelve of the sixteen rows have a route that exists today; four do not. Rows are not Services: opensearch's row covers two, mongodb's replicaset row one per pod, kafka's one bootstrap plus one per broker. A chart implements a route, which is what the table counts.

Three rows need their status stated exactly rather than implied.

**`openbao` cannot be half-covered.** Its UI Service does have a route — `ui.loadBalancerSourceRanges`, rendered through `_helpers.tpl:1115` from the single call site `ui-service.yaml:51`, whose own comment reads "Supported inputs are Values.ui" — but the API Service has none, and both come from one chart and one release. Covering the UI while the API stays open is the failure mode this proposal exists to avoid, and a per-chart refusal (§6) cannot fire for one Service and not the other. So openbao is uncovered until the API route exists upstream.

**`qdrant` has a definite answer, not a pending one.** An earlier draft called this row unverified on the grounds that the chart arrives as an ExternalArtifact; that was wrong in the other direction. The artifact resolves to `packages/system/qdrant`, where the chart **is** vendored, and `charts/qdrant/templates/service.yaml` renders `loadBalancerIP` and nothing else. Same structure as openbao and nats: ExternalArtifact does not mean absent from the tree.

**`vpn`'s condition is not `external`.** The LoadBalancer is the `else` branch of `if .Values.externalIPs`, and `external` only picks `externalTrafficPolicy` (`service.yaml:17`). vpn is published at stock values. So `sourceRanges` on vpn must not be gated on `external`, and when `externalIPs` is set the Service is ClusterIP, where the API rejects the field.

### 5. Applications that already filter

`tcp-balancer` has its own source-IP filter: `whitelist` renders HAProxy ACLs (`templates/configmap.yaml:44,70,94,105`). It is **not** a substitute, and an earlier draft wrongly excluded the application on the assumption that it was: the ACLs on the HTTP and HTTPS frontends are additionally gated on `whitelistHTTP` (`configmap.yaml:42` and `:68`), which defaults to `false` (`values.yaml:96`). With `whitelist` set and that default, only the Kubernetes (6443) and Talos (50000) frontends are filtered.

So `tcp-balancer` gains `sourceRanges` like any other application, and the two coexist at different layers: the Service field drops the packet before it arrives, HAProxy rejects a connection it has already accepted. For traffic entering from outside the cluster the effective set is the intersection. For in-cluster clients it is **not**: Socket LB rewrites the destination before the tc hook (§Context), so only the HAProxy ACLs apply there — and on the HTTP and HTTPS frontends those are gated on `whitelistHTTP`, off by default. The chart documents both halves, and that `whitelist` alone leaves HTTP open. One further limit worth stating: the Kubernetes and Talos frontends are rendered inside `{{- with .Values.kubernetesAndTalos }}`, so a balancer configured only with `httpAndHttps` and the default `whitelistHTTP` filters nothing at the proxy at all.

### 6. Applications that cannot be covered

Three Services have no usable route, and one is unverified. They must refuse the value rather than accept and ignore it — and the refusal has to be written by each chart, because **the platform does not validate unknown fields**: the published OpenAPI is marked documentation-only and forced open (`pkg/cmd/server/openapi.go`), the structural schema is used for defaulting rather than validation (`pkg/registry/apps/application/rest_defaulting.go`), and `values.schema.json` carries no top-level `additionalProperties`.

The guard must not fire when the value is inert, or turning off publication would freeze the release:

```gotemplate
{{- /* thirteen applications: publication is gated on external */}}
{{- if and .Values.sourceRanges .Values.external }}
{{- fail "sourceRanges is not supported for this application: <reason>" }}
{{- end }}

{{- /* vpn: publication is the empty-externalIPs branch, not external */}}
{{- if and .Values.sourceRanges .Values.externalIPs }}
{{- fail "sourceRanges cannot be applied when externalIPs is set: the Service is ClusterIP" }}
{{- end }}
```

- **openbao** — the API Service has no route and the chart cannot refuse for one Service only (§4). Needs an upstream PR, or a vendored-chart patch — and neither `packages/system/openbao` nor `packages/system/qdrant` has a `patches/` directory or a `patch` step today (both `Makefile`s do `rm -rf charts` then re-pull), so establishing that hook is part of the work rather than a detail. `packages/system/kamaji` is the in-tree pattern.
- **rabbitmq** — the only route is `spec.override.service`, and `templates/rabbitmq.yaml:107` records the standing decision "Deliberately no spec.override.service", because on a LoadBalancer it drives a node-port reallocation loop through the operator's Service watch. Either that is disproved for this one field, or rabbitmq stays out.
- **vm-instance** — the field can be set, but the Service carries `service.kubernetes.io/service-proxy-name: cozy-proxy` (`service.yaml:10`), so Cilium does not serve it and `cozy-proxy` implements no such field. In `WholeIP` mode (`port: 65535`) source ranges have no meaning.
- **qdrant** — upstream chart offers `loadBalancerIP` only; same patch-hook caveat as openbao.

Applications outside this proposal — `clickhouse`, `harbor`, `foundationdb`, `bucket`, `kubernetes`, `kubernetes-nodes`, `vm-disk`, `vpc`, `tenant` and the rest — do not declare the field, and the outcome there is worse than being dropped: `pkg/registry/apps/application/rest.go` writes the whole spec into `HelmRelease.spec.values` and reads it back with only `_`-prefixed keys filtered, so `sourceRanges` set on clickhouse is stored and **returned on GET**. A tenant sees their own field echoed and reasonably concludes it is in force. Since the platform already has a write-path warning mechanism for exactly this shape of mistake (`warnRemovedKubernetesFields` in the same file), the honest options are a warning there or a refusal in each chart — not silence. This proposal asks for the warning and says so rather than calling the current behaviour harmless.

### 7. Changing the list

A tenant edits `sourceRanges` like any other value: `cozystack-api` writes the HelmRelease, helm-controller reacts to the generation change through its watch, the chart re-renders. That path is event-driven and normally takes seconds; the `5m` interval configured for application HelmReleases (`pkg/cmd/server/start.go`, `HelmReleaseInterval`) is the drift-detection poll, not the propagation delay, and it is the upper bound only when the watch path is degraded.

Either way it is a reconcile, not a datapath update: adequate for a list an operator changes deliberately, inadequate for continuous synchronisation against a CDN's published ranges. That is a Non-goal rather than a half-served feature. If it becomes a requirement the answer is a named CIDR-group object charts reference and a controller resolves, proposed separately.

### 8. When `EndpointAttachment` lands

#45 renders its own additive Service mirroring the endpoint's selector and ports. An attachment on an endpoint this proposal restricts therefore reaches the same pods through a different Service, which this field does not touch.

There is no clean resolution available here, and claiming one would be wrong: #45 explicitly leaves per-address ACL to a future `SecurityGroup` target, while this proposal covers `external: true`, which #45 keeps alive alongside attachments. Whoever lands second states the interaction. What this commits to is narrow: `sourceRanges` follows `external`, and is sunset with it.

## User-facing changes

- **Tenants** gain `sourceRanges` next to `external` on the twelve covered routes. On the four uncovered applications the field exists and fails the render with the reason.
- **Operators** get the Cilium value in the platform bundle, its three consequences, and a page saying what is covered: listed databases yes; in-cluster no; `healthCheckNodePort` no; HTTP through the shared ingress no; RobotLB no.
- **Admins on RobotLB** get a documented statement that `sourceRanges` does not work there, and nothing else: no detection, no refusal, no conditional flag (§3).

## Upgrade and rollback compatibility

- **Existing clusters:** the field is absent everywhere; nothing changes until it is set.
- **The flag is the breaking part.** Any Service already carrying `loadBalancerSourceRanges` starts filtering node ports at the agent restart. On a stock cluster no chart sets the field, so the blast radius is operator-set Services; the notes must say to inventory them.
- **Setting the field on a live endpoint** filters new connections only; established ones survive.
- **Clearing the field** removes the restriction through the same path that set it. Nothing outside the chart ever owns the field, which is the practical advantage over a controller.
- **Turning off publication while `sourceRanges` is still set** must not fail the render: that would leave the previous release, and its LoadBalancer, in place. The value is therefore ignored — not rejected — when the application publishes nothing externally. **What "publishes nothing" means is per chart, not `external` everywhere**: for thirteen applications it is `external: false`, but for vpn it is a non-empty `externalIPs`, because vpn's Service is a LoadBalancer at stock values with `external: false` (§4). Keying this rule on `external` alone would silently discard the value on the one application that is published by default.

## Security

- **New tenant input:** CIDR lists, which only restrict. The worst a wrong value does is lock the tenant out of its own endpoint.
- **New RBAC:** none. No API group, no verbs, no controller identity.
- **No new trust boundary:** the value travels the path every other application value does.
- **Cannot affect another tenant:** it reaches only objects of the application it is set on.
- **Does not isolate from in-cluster clients** (Socket LB) and **does not cover `healthCheckNodePort`**. Both at the field, not in a footnote.

## Failure and edge cases

- Set on openbao, qdrant, rabbitmq or vm-instance **while the application is publishing** → render fails naming the reason. When it publishes nothing the value is inert and ignored, so disabling publication never freezes a release. The condition is per chart (§6): `external` for thirteen applications, a non-empty `externalIPs` for vpn.
- Set on vpn with `externalIPs` non-empty → render fails: the Service is ClusterIP there and the API rejects the field.
- Set on an application outside this proposal — `clickhouse`, `harbor`, `bucket`, `kubernetes`, `vpc` and the rest — → silently accepted and dropped, because the aggregated API preserves unknown fields (§6). Documented, not guarded.
- Set on a cluster running RobotLB → accepted and rendered, and the filter sees the Hetzner load balancer's address rather than the client's, so legitimate traffic is dropped. Unsupported by documentation, undetected by design (§3).
- Malformed CIDR → the chart matches a syntactic pattern and calls `fail`, as `packages/apps/postgres/templates/scaledobject.yaml` already validates a user value. A pattern catches shape, not semantics: `999.1.1.1/8` passes a naive regex, and the real rejection comes from the API server when the Service is applied.
- IPv6 range on a single-stack IPv4 Service → accepted by the chart, then rejected or ignored by the API server depending on the cluster's `ipFamilies`. The chart does not attempt to reconcile the two; a mixed list is the tenant's to get right.
- The upstream annotation `service.beta.kubernetes.io/load-balancer-source-ranges` set by hand alongside the field → Kubernetes prefers the annotation on providers that honour it. Nothing in the tree sets it, and the charts do not either; worth stating so nobody adds it later as a second source of truth.
- mongodb switches between sharded and replicaset → the route changes with it; both branches carry the field and a chart unit test pins each.
- mariadb crosses `replicas: 1 → 2` → the CR field changes from `service` to `primaryService`; both carry it.
- opensearch with `dashboards.enabled: false` → one Service, same list.
- tcp-balancer with both `whitelist` and `sourceRanges` → effective set is the intersection (§5).
- Cilium flag off → the VIP is filtered and node ports are not. The chart cannot read cluster state, so this is handled by shipping the flag first (§Rollout) and by documentation.

## Testing

- **Unit (helm-unittest), per chart:** absent field renders nothing; present field renders at the route §4 names; both branches for mongodb, mariadb and vpn; opensearch renders it on both Services; `external: false` ignores it on covered and uncovered charts alike; uncovered charts fail only when `external` is true.
- **Unit:** the rendered cilium ConfigMap carries the new source-range setting **and** retains `bpf-masquerade` from the kube-ovn variant, proving the two `bpf` maps merged rather than replaced.
- **E2E (chainsaw), the assertion that gates everything:** publish a database with `sourceRanges` and prove refusal **from outside the cluster**, on the VIP and on a node port. No in-cluster client can prove it (Socket LB) and no cluster node can (reserved identity), so the fixture needs a genuinely external host.
- **E2E:** clearing the field restores reachability.

## Rollout

- **Phase 1 — the external-client fixture and the measurement.** Nothing else ships until refusal has been observed once. Earlier drafts put this after the platform change; that order was wrong.
- **Phase 2 — the Cilium value** in `packages/system/cilium/values.yaml`. It changes nothing on a stock cluster, where no chart sets the field, and it must precede any chart whose Service keeps its node ports.
- **Phase 3 — the charts that render their own Service:** postgres, redis, valkey, tcp-balancer, mongodb sharded, and — now that the flag is in place — opensearch (both Services) and vpn.
- **Phase 4 — the operator routes:** mariadb (both branches), mongodb replicaset, kafka, nats.
- **Phase 5 — the uncovered:** upstream work plus a `patches/` hook for openbao and qdrant, a decision on rabbitmq, `cozy-proxy` support for vm-instance. openbao is only worth doing once the API Service has a route, since covering its UI alone is the failure mode this proposal avoids (§4).

Phases 3 and 4 are separable per chart; nothing in them is ordered relative to another.

## Open questions

1. **One list per application, or keyed by endpoint?** §2 takes the first because both two-endpoint applications already have a toggle for the second endpoint, and because a tenant-visible endpoint vocabulary does not exist yet. If #45's endpoint model lands first, keying becomes natural and this field should follow it rather than invent a second naming.
2. **Is the intersection the right rule for tcp-balancer** when both filters are set, or should the chart refuse the combination outright?
3. **rabbitmq:** disprove the override hazard for this single field, or ship without it?
4. **Is leaving RobotLB undetected acceptable**, or should the platform first grow a reliable signal for which LoadBalancer implementation is in use? cozystack#4430 suggests the current one was never usable, and fixing it is a prerequisite for any detection, here or elsewhere.

## Alternatives considered

**A separate CRD and a controller patching Services.** Two earlier drafts. Rejected on three measured grounds: the label used to authorise resolution (`internal.cozystack.io/tenantresource`) answers "may this tenant see this Secret" and has no consumer on Services; ownership was measured against the `helm` CLI while the platform applies through Flux helm-controller with force ownership; and it bypasses the CR field five operators already provide.

**Host firewall `CiliumClusterwideNetworkPolicy` with `ingressDeny`.** A real deny, already used by Cozystack for platform ports with measured drops. Rejected on a schema fact: `ingressDeny` takes only `from*` selectors and `toPorts`, with no destination match, so a rule for a database port would close it on every node for every tenant.

**`allocateLoadBalancerNodePorts: false` everywhere instead of the Cilium value.** Rejected: the operator-rendered Services do not expose it, and forcing it on RobotLB breaks the load balancer. cozystack#4430 is the argument in miniature — the platform already tried this and the guard has been inert since it was written.

**A `TCPBalancer` in front of every endpoint.** Works today and stays the answer for HTTP. Rejected as the permanent answer for databases.

**FQDN lists.** Rejected: `toFQDNs` is ineffective on the default stack (cozystack#3820).
