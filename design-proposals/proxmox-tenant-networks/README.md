# Proxmox tenant networks

- **Title:** `Proxmox tenant networks: platform-owned bridge, VLANs and IP pools`
- **Author(s):** `@themoriarti`
- **Date:** `2026-10-20`
- **Status:** Review

## Overview

On a Proxmox-backed tenant cluster, the tenant writes a network name, not a bridge, a VLAN tag or an address range. The admin defines a zone: a VLAN-aware bridge and a static table of tenant networks, each with a namespace, a name, a VLAN, an address range, a gateway and a prefix. The package chart renders the zone and one namespaced `InClusterIPPool` per network. Nothing allocates at runtime: there is no controller, no status and no image.

A ValidatingAdmissionPolicy with the zone as its parameter keeps every NIC of a `ProxmoxMachine` or `ProxmoxMachineTemplate`, and the `ProxmoxCluster` pool, on networks of the request's own namespace. Kube-OVN is not involved. The proposal writes nothing to OVN, adds no egress path and needs no hypervisor change beyond a VLAN-aware bridge.

## Scope and related proposals

- Parent: cozystack/cozystack#3481. This proposal does not decide whether Proxmox becomes a supported platform.
- It builds on the provisioning layer already in main: cozystack/cozystack#4087, cozystack/cozystack#4483, cozystack/cozystack#4088 and cozystack/cozystack#4089 (capmox v0.7.7 and in-cluster IPAM v1.0.3 as optional packages, `substrate: proxmox` for managed clusters and worker pools, storage and cloud-controller integration).
- Follow-up: cozystack/community#93, VPC-backed Proxmox subnets. It reuses the zone, the pools and the admission defined here, and adds dynamic allocation and the drop between VPCs inside OVN. It waits for Kube-OVN to handle the gateway chassis of `logicalGateway` underlay subnets.
- Related: cozystack/community#86 (per-cluster infrastructure backends). If it is accepted, the `proxmox.network` values move with the Proxmox backend's applications.
- Deferred:
  - dynamic VLAN allocation and a hold on a network until its last VM is gone (the follow-up's controller);
  - extra NICs per worker pool (`additionalNetworks`);
  - per-tenant Proxmox credentials (`ProxmoxCluster.credentialsRef`) plus `SDN.Use` per VLAN, as enforcement on the hypervisor side;
  - pinning the PVE resource pool (`proxmox.pool`);
  - IPv6;
  - Proxmox SDN objects.

## Decisions

## Context

- Today a worker pool takes `proxmox.network.{bridge,vlan,mtu}` from tenant values and renders them unchanged into `ProxmoxMachineTemplate.spec.network.default` (`packages/apps/kubernetes-nodes/values.yaml`, typedef `ProxmoxNetwork`; `packages/apps/kubernetes-nodes/templates/nodegroup.yaml`).
- The cluster takes `proxmox.ipv4Config.{addresses,gateway,prefix}` from tenant values and renders it into `ProxmoxCluster.spec.ipv4Config` (`packages/apps/kubernetes/templates/cluster.yaml`). capmox builds one `InClusterIPPool` per cluster from it.
- capmox v0.7.7 requires an IP pool config on `ProxmoxCluster` (`ipv4Config` or `ipv6Config`). It accepts `ipv4PoolRef` on the default NIC and on additional devices.
- The in-cluster IPAM provider validates a pool on its own and against its own allocated addresses. It does not check pools against each other for overlap, and an `IPAddressClaim` can only reference an `InClusterIPPool` in its own namespace. Its delete webhook refuses to delete a pool that still has allocated addresses, and a finalizer holds the pool until the last address is released.
- Tenant roles (`cozy:tenant*`) have no rule on `infrastructure.cluster.x-k8s.io`, `ipam.cluster.x-k8s.io` or core `namespaces`. Tenants never write capmox objects themselves: the platform's Helm releases render them from tenant values.
- No admission policy covers capmox kinds today. capmox runs with one Proxmox token for every tenant.

### The problem

- A tenant can put its workers on any bridge and VLAN, including another tenant's VLAN or an infrastructure VLAN.
- Two clusters can name overlapping address ranges, and nothing stops two VMs from taking the same address.
- An operator who wants to keep tenants apart has to hand-assign bridges, VLANs and ranges, with nothing to enforce them.

## Goals

- A tenant cannot choose a bridge, a VLAN or an address range. It names a network of its own namespace.
- Every VLAN of a zone is unique within its bridge, checked when the zone is written.
- One namespaced `InClusterIPPool` per network is the only allocator for that network's VM addresses.
- Admission refuses a capmox machine NIC, on create or on a network change, that is not on a network of the namespace (in strict mode, any raw NIC), and a `ProxmoxCluster` pool that is not the sentinel of one of the namespace's networks.
- Everything is opt-in, and nothing changes for KubeVirt or for clusters that do not use it.
- Each property above has a test cell that runs on the regular e2e lane, without Proxmox.

### Non-goals

- No L3 boundary between tenant VLANs. Routing between them is whatever the operator's router does, as today. The zone's table makes that router's configuration a fixed function of the zone (see Security).
- No Kube-OVN, no OVN writes, no VPC integration, no egress gateway, no transit network.
- No controller: no runtime allocation, no conditions, no hold on a network while VMs use it beyond what the IPAM provider's finalizer gives.
- No Proxmox API calls and no Proxmox SDN objects.
- No new IPAM and no VM lifecycle controller.

## Design

### Objects and ownership

| Object | Scope | Written by | Read by |
|---|---|---|---|
| `ProxmoxNetworkZone` | cluster | admin (package values, rendered by the package chart) | admission (as `params`), `kubernetes` and `kubernetes-nodes` charts (`lookup`) |
| `InClusterIPPool pxnet-<network>` | tenant namespace | the package chart | capmox (via `ipv4PoolRef`), tenants (read-only) |
| `ValidatingAdmissionPolicy` + one binding per zone | cluster | the package chart | apiserver |

Tenants get read access to the pools of their namespace through the aggregated `cozy:tenant:view` role, and nothing else new.

### Zone

```yaml
apiVersion: proxmox.cozystack.io/v1alpha1
kind: ProxmoxNetworkZone
metadata: {name: default}
spec:
  bridge: vmbr1               # VLAN-aware, same name on every node VMs may land on
  mtu: 1500                   # default for every NIC of the zone
  uplinkVLAN: 70              # optional: the outer VLAN that carries the bridge between hypervisors
  uplinkBridges: [vmbr0]      # optional: bridges that enslave the uplink's parent device
  networks:
  - namespace: tenant-a
    name: workers
    vlan: 201
    addresses: ["198.18.1.10-198.18.1.250"]
    gateway: 198.18.1.1
    prefix: 24
  - namespace: tenant-b
    name: workers
    vlan: 202
    addresses: ["198.18.2.10-198.18.2.250"]
    gateway: 198.18.2.1
    prefix: 24
    mtu: 9000                 # optional override
```

The CRD carries CEL validations: `vlan` in 1..4094 and never equal to `uplinkVLAN`; `vlan` unique within the zone; (`namespace`, `name`) unique within the zone; `uplinkBridges` never contains `bridge`. The package chart refuses two zones on one bridge and the same (`namespace`, `name`) in two zones. There is no status: the zone is the admin's statement, nothing reports on it.

### IP pool

For each network the package chart renders:

```yaml
apiVersion: ipam.cluster.x-k8s.io/v1alpha2
kind: InClusterIPPool
metadata:
  name: pxnet-workers
  namespace: tenant-a
  labels: {proxmox.cozystack.io/zone: default, proxmox.cozystack.io/network: workers}
spec: {addresses: ["198.18.1.10-198.18.1.250"], gateway: 198.18.1.1, prefix: 24}
```

The pool does not carry `ipam.cluster.x-k8s.io/skip-validate-delete-webhook`. When the admin removes a network whose pool still has allocated addresses, the provider's webhook refuses the delete, the release reports it, and the pool stays until the last address is released. That is the whole lifecycle: the IPAM provider holds, the admin decides.

### Consumption

```yaml
# Kubernetes
proxmox:
  network: {name: workers}         # ProxmoxCluster.ipv4Config becomes a sentinel: [gateway]
# KubernetesNodes
proxmox:
  network: {name: workers}         # default NIC: bridge, vlan, mtu, ipv4PoolRef from the zone
```

- The charts list `ProxmoxNetworkZone` with `lookup` and take the entry whose `namespace` is the release namespace and whose `name` matches. No entry fails the render with "no Proxmox network `workers` for this namespace: ask the platform operator to add it to a zone". Two entries fail as ambiguous. The pool must already exist in the namespace; otherwise the render fails, so a machine never waits on a pool that nobody renders.
- Mixing `network.name` with a raw `bridge`, `vlan` or `ipv4Config` is refused at render time. This is for the user's benefit; admission is the boundary.
- capmox requires an IP pool config on `ProxmoxCluster`, so the cluster renders a sentinel range holding only the gateway, which IPAM always excludes.

### Admission

Policy `proxmox-network-zone-binding` has `paramKind: ProxmoxNetworkZone`. The package chart renders one binding per zone with `paramRef.name: <zone>` and `parameterNotFoundAction: Deny`. It covers `ProxmoxMachine` and `ProxmoxMachineTemplate` on CREATE and on UPDATE when `network` changes (the capmox v0.7.7 webhook checks only that `tags` are unchanged), and `ProxmoxCluster` on CREATE and on UPDATE when `ipv4Config` changes. Exemptions: `cozy-*`, `kube-system`, and objects being deleted.

With `mine = params.spec.networks.filter(n, n.namespace == request.namespace)`:

1. A NIC on `params.spec.bridge` must use a (VLAN, `pxnet-<name>` pool) pair of `mine`. A namespace with no network in the zone has an empty `mine`, so no NIC of its machines may touch the zone bridge.
2. If `mine` is not empty, a machine must declare `network.default`; without it the VM keeps the template's NIC, which may sit on an infrastructure bridge.
3. No NIC on another bridge may be tagged with `params.spec.uplinkVLAN`, and no NIC may sit on `params.spec.uplinkBridges`. Both are trunks into the zone (an outer tag the guest sets, or the bond's VLAN device that Proxmox would enslave).
4. If `mine` is not empty, `ProxmoxCluster.spec.ipv4Config` must be the sentinel of one of `mine`: exactly one address, equal to that network's gateway. Otherwise capmox would build its pool from a range the tenant chose.

Each binding judges only its own bridge and its own uplinks. A NIC on another zone's bridge passes this binding and is judged by that zone's. Two zones never share a bridge.

Policy `proxmox-network-raw-networks` has no parameter and holds in every non-exempt namespace. It is rendered for `admission.rawNetworks: Warn` (binding with `validationActions: [Warn, Audit]`) and `Deny` (`[Deny]`), and not at all for `Allow`:

5. Every NIC must sit on a zone bridge (the list of zone bridges is rendered from the package values), and `ProxmoxCluster.spec.ipv4Config` must have the sentinel shape (one address, equal to the gateway).

Admission checks what the platform's Helm releases create from tenant values. Tenants cannot create these kinds themselves.

### Lifecycle

- **Add a network:** the admin adds the entry to the zone in the package values; the next reconcile renders the zone and the pool. The tenant namespace must exist.
- **Remove a network:** the admin removes the entry. The zone and the binding stop admitting it at once; the pool is deleted when it has no allocated address, and held by the IPAM provider's webhook and finalizer while it has one.
- **Change a VLAN or range:** refused for a network with allocated addresses by the IPAM webhook (the pool cannot shrink past an allocated address); otherwise applied on the next reconcile. Machines already created keep their NIC until the chart re-renders them.

### Observability

There is no controller, so there are no custom metrics. kube-state-metrics already runs with `customResourceState` in the monitoring agents; the package adds `InClusterIPPool.status.ipAddresses.{total,used,free}` to that configuration, and one `PrometheusRule` fires for a pool above 90%. Everything else is `kubectl get pxzone`, `kubectl get inclusterippool -A` and the HelmRelease conditions of the package.

## User-facing changes

- **Admin:**
  - the package and its values: zones with their networks, the uplink VLAN and bridges, `admission.rawNetworks`;
  - a VLAN-aware bridge on every node;
  - a router serving the zone's address plan.
- **Tenant:** `proxmox.network.name` in `Kubernetes` and `KubernetesNodes`, plus read-only `kubectl get inclusterippool`.
- **Docs:** an operator guide and the terraform provider fields.

## Upgrade and rollback compatibility

- Nothing changes until the package is enabled. With `rawNetworks: Allow`, the raw path keeps working everywhere except on zone bridges and uplink bridges.
- Migration of an existing cluster:
  1. The admin adds a network for the namespace.
  2. The tenant adds a new worker pool on it.
  3. The tenant scales the old pool down to zero.
  4. The tenant switches the cluster's `ipv4Config` to `network.name`. The IPAM webhook refuses to shrink a pool while an allocated address would fall outside it, so this step comes after the last machine on the old range is gone. Admission tolerates the unchanged raw `ipv4Config` until then: rule 4 judges CREATE and changes only.
  5. The admin moves `rawNetworks` from `Allow` to `Warn`, and to `Deny` once no raw NIC is left.
- Rollback: set `rawNetworks: Allow`, then disable the package. Pools with allocated addresses stay (IPAM finalizer); the CRD is kept (`helm.sh/resource-policy: keep`).

## Security

- **Tenant inputs:** network names only, resolved in the release namespace.
- **New privilege:** none. No new service account, no new controller RBAC. The package chart writes `InClusterIPPool` into tenant namespaces with the privileges the platform's Helm releases already hold.
- **Trust of the parameter:** `ProxmoxNetworkZone` is cluster-scoped and written only by the package chart. No `cozy:tenant*` role has any rule on `proxmox.cozystack.io`, and tenants never see other tenants' entries: zones are not aggregated into tenant roles.
- **Hypervisor:** the guarantees hold only for VMs created through capmox. A guest placed on a zone bridge by other means needs the hypervisor to drop guest VLAN tags.
- **L3 between VLANs:** the operator's router, as today. With the table, the router rule is one statement per zone: deny the zone's aggregate to itself, except within the same subnet. This proposal does not make the L3 boundary between tenant VLANs platform-owned. A network that needs one belongs on a VPC-backed subnet (the follow-up, cozystack/community#93), where the drop sits inside OVN.

## Failure and edge cases

- Network missing from the zone, or pool not rendered yet → the charts refuse to render machines, with the reason.
- Pool exhausted → new machines wait, and existing ones keep working.
- Package release failing (a bad zone, a namespace that does not exist) → nothing changes for existing clusters; the HelmRelease shows the error.
- Zone deleted while bindings exist → `parameterNotFoundAction: Deny` refuses every machine on that bridge until the zone is back.
- Network removed under VMs → the pool is held by the IPAM provider; admission refuses new machines on it.
- Router missing a VLAN → VMs have no gateway. Nothing in the cluster can see this; the operator's environment preflight checks it.

## Testing

- **CEL in the CRD:** every validation with an admitted and a refused case (helm-unittest on the CRD chart).
- **helm-unittest:**
  - the package: zones, pools, bindings per zone, both policies, `rawNetworks` modes, the duplicate and uplink refusals;
  - `kubernetes` and `kubernetes-nodes`, both substrates, with a mocked `lookup` for the zone and the pool, golden snapshots for KubeVirt;
  - the platform bundle wiring.
- **Chainsaw on the regular lane, no Proxmox:**
  - install the capmox CRDs only, from the vendored provider components (no conversion webhooks), so the policy has kinds to judge;
  - render zones, pools, bindings and the raw policy from the checkout with `helm template` and test values, and apply them;
  - two namespaces get distinct VLANs and pools; the CRD refuses a duplicate VLAN and a duplicate network;
  - each admission rule refuses, next to a positive control of the same shape: create, network-change update, uplink VLAN, uplink bridge, strict mode, `ProxmoxCluster` sentinel;
  - an `IPAddressClaim` holds a pool whose network is removed, and the pool goes once the claim is released.
- **Proxmox environment (manual or self-hosted):**
  - a VM gets its address from the pool and reaches the router;
  - L2 isolation between two tenants;
  - the operator router's cross-VLAN deny, with a positive control;
  - scale and replace with no duplicate address.

## Rollout

1. This proposal.
2. The implementation PR in cozystack/cozystack: CRD chart, package, charts, CI suite.
3. Website docs and the terraform provider issue.
4. The follow-up proposal for VPC-backed subnets (cozystack/community#93): dynamic allocation, the hold on a network, the drop between VPCs inside OVN.

## Open questions

1. Default and timeline for `rawNetworks`, and an allowlist for bridges the operator sanctions.
2. Is the operator's router acceptable as the L3 boundary between tenant VLANs for this first piece?
3. API group: `proxmox.cozystack.io`, or an existing group?
4. Pool naming: `pxnet-<name>`, or the network name as is?
5. Should the admin be able to pin the PVE resource pool (`proxmox.pool`) per network in the same table now, or does it wait for the per-tenant credentials follow-up?

## Alternatives considered

- **A controller with a VLAN allocator (the earlier draft of this proposal):** uniqueness across namespaces and the hold until VMs are gone become automatic, at the cost of an image, RBAC and a reconciler. The follow-up brings it back together with the VPC integration, where it is needed anyway.
- **Allocator in Helm (`lookup`):** no safe release while VMs exist, and no conditions.
- **VLAN = hash(namespace):** collides in a small range.
- **`GlobalInClusterIPPool`:** can be claimed from any namespace.
- **A pool per cluster from tenant values:** two allocators over one range.
- **Tenant-chosen VLAN checked against an allow-list:** still needs an allocator for uniqueness.
- **A Proxmox SDN VNet per network:** every SDN apply reloads the network configuration on all nodes; it needs its own proposal.
- **Kube-OVN `Vlan` objects as the allocation record (as in cozystack/cozystack#4813):** ties this piece to Kube-OVN.

---

<!--
Inspired by KubeVirt enhancement proposals
(https://github.com/kubevirt/enhancements) and Kubernetes Enhancement
Proposals (KEPs).
-->
