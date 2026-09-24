# Backend-bound components of tenant Kubernetes clusters

- **Title:** `Backend-bound components of tenant Kubernetes clusters`
- **Author(s):** `@IvanHunters`
- **Date:** `2026-09-24`
- **Status:** Draft
- **Supplements:** [`cozystack/community#9`](https://github.com/cozystack/community/pull/9) by `@kvaps`

## Overview

Tenant Kubernetes clusters in Cozystack run a set of components that assume every worker is a KubeVirt VM created by CAPK. Hybrid clusters (workers outside the management cluster, [cozystack/community#9](https://github.com/cozystack/community/pull/9)) break that assumption. This proposal inventories those components, records what happens to each of them on a node of another backend, and lists possible directions, so that the hybrid design starts from a verified list instead of rediscovering it.

It is supplementary to #9, not competing with it, and makes no commitment of its own: it changes no code and does not choose between the directions it lists. Merging it means accepting the inventory as the baseline #9 builds on; the directions stay candidates for #9 to decide.

## Scope and related proposals

- [cozystack/community#9](https://github.com/cozystack/community/pull/9), hybrid kubernetes clusters (Phase 3, draft, deferred). Its sketches cover how a pool of another backend gets its machines (`backend.type`, `LocationProfile`, a node-lifecycle controller, a native-provider `cluster-autoscaler` per cloud pool). This proposal covers the other half: what already runs in the tenant cluster. It should be read with #9 and folded into it, or superseded by it, when Phase 3 resumes.
- [`kubernetes-nodes-split`](../kubernetes-nodes-split/) delivered the `kubernetes` / `kubernetes-nodes` split that a per-backend component set would build on.

## Decisions

## Context

The inventory is taken at `cozystack/cozystack` `ff52aeb44`, with upstream `kubevirt/csi-driver` `27b52aa22da7`, `kubevirt/cloud-provider-kubevirt` `a0acf33` plus Cozystack patches, `clastix/cluster-api-control-plane-provider-kamaji` v0.19.0, and Cluster API v1.10.1.

### A working precedent

[`aenix-org/kubernetes-switchcloud`](https://github.com/aenix-org/kubernetes-switchcloud) is a tenant-cluster chart for OpenStack (CAPO) workers running Talos against a Kamaji control plane in Cozystack. It points its generic addons at the same `cozystack-kubernetes-application-kubevirt-kubernetes-*` chart artifacts with backend-specific values. On top of that it needed several management-side packages of its own (an SNI router exposing konnectivity and Talos trustd, a load-balancer controller so the tenant cluster carries no cloud CCM or credentials, a per-cluster CSR signer as its own Deployment where Cozystack embeds `talos-csr-signer` as a sidecar of the Kamaji control-plane pod, the CAPO infrastructure provider, and its own copy of the Talos bootstrap provider that Cozystack now ships as well), and two in-cluster components: a DaemonSet that sets `spec.providerID` from instance metadata, and a per-node proxy that makes `kubernetes.default.svc` reachable from outside the management pod network.

### The problem

1. Today a set like the one above can only ship as a separate chart that duplicates the control-plane part of `kubernetes`.
2. In a hybrid cluster, pools of different backends share one control plane, so every component in the inventory below either has to work on any node or has to come with its backend and stay on its nodes. Nothing today lets a component be confined to one backend.

### Inventory

Paths are in `cozystack/cozystack` unless stated.

| Component | Where it is defined | KubeVirt assumption | On a node of another backend |
| --- | --- | --- | --- |
| KubeVirt CSI node plugin | `packages/apps/kubernetes/templates/helmreleases/csi.yaml`, `packages/system/kubevirt-csi-node/templates/deploy.yaml`, `packages/apps/kubernetes/images/kubevirt-csi-driver/main.go` | Runs on every node (all taints tolerated, no `nodeSelector`); node ID is `<cluster-namespace annotation>/<providerID without kubevirt://>` | Non-CAPI node: exits fatally, never registers. Another CAPI provider (only possible with a second `Cluster` object, see Design): starts with a node ID that is not a valid `namespace/name` key |
| KubeVirt storage classes | same chart; upstream `pkg/service/node.go`, `pkg/service/controller.go` | Default class, `Immediate` binding, no topology from `NodeGetInfo` | A pod with such a PVC can land anywhere. Attach then fails: on a non-CAPI node the external-attacher finds no driver in `CSINode`; on another provider's node `ControllerPublishVolume` cannot parse the node ID |
| kubevirt-cloud-controller-manager | `packages/apps/kubernetes/templates/kccm/manager.yaml`; upstream `pkg/provider/instances_v2.go` | Default controller set; `InstanceExists` returns an error for a non-`kubevirt://` `providerID` | Not deleted today: the node-lifecycle controller only checks NotReady nodes and skips on error. The cloud-node controller's periodic address sync looks the node up by name (or the `node.kubernetes.io/instance-id` label), never by `providerID`, and when no VMI matches it fails with an error that the node controller logs and moves past; a foreign node whose name matches the name or `spec.hostname` of any VMI in the cluster's namespace (including other clusters' workers and tenant VMs) would get that VMI's addresses. Incidental, not a contract. `LoadBalancer` endpoints are mapped to VMIs by node name and foreign nodes are dropped |
| ingress-nginx exposure | `packages/apps/kubernetes/templates/ingress.yaml` | `Proxied` Service selects `cluster.x-k8s.io/cluster-name` plus the ingress role label, i.e. the virt-launcher pods | Never an endpoint in `Proxied`; dropped by kccm in `LoadBalancer` |
| cluster-autoscaler | `packages/apps/kubernetes/templates/cluster-autoscaler/deployment.yaml` | `--cloud-provider=clusterapi`, discovery scoped to the cluster, RBAC for `kubevirtmachinetemplates` only | Invisible unless backed by a CAPI `MachineDeployment` it has RBAC for. A second native-provider instance is not a documented upstream setup (upstream `cluster-autoscaler/FAQ.md` covers a second instance only for check-capacity `ProvisioningRequest`s) and would contend for the default leader lease |
| Worker machine config | `packages/apps/kubernetes-nodes/templates/talos-reconcile-job.yaml` | API endpoint `https://<release>.<namespace>.svc:6443` resolved through `extraHostEntries` to the API server ClusterIP; management CoreDNS as resolver; install disk `/dev/vda` | Unreachable outside the management pod network |
| Control-plane side ports | `packages/apps/kubernetes/templates/cluster.yaml` | API server already has an external SSL-passthrough ingress hostname (added to the SANs by the Kamaji control-plane provider, not listed in the chart's `certSANs`); trustd (50001) and konnectivity only as ClusterIP ports | Can join through the ingress hostname, but cannot reach trustd or konnectivity |
| Cilium | `packages/apps/kubernetes/templates/helmreleases/cilium.yaml` | `k8sServiceHost` is the in-cluster `svc` name | Needs the external hostname instead |
| GPU | `packages/apps/kubernetes/templates/helmreleases/gpu-operator.yaml`, `talos-reconcile-job.yaml` | `NVreg_NvLinkDisable=1` chosen for PCI passthrough, cluster-wide; HAMi's `gpu: "on"` label set only through the KubeVirt pool's Talos `nodeLabels` | NVLink disabled on bare metal; HAMi not scheduled without the label |
| Node labels | `packages/apps/kubernetes-nodes/templates/nodegroup.yaml` | Only `node-role.kubernetes.io/<role>` through CAPI Machine-to-Node sync, plus `gpu: "on"` above | No pool, backend or location label (CAPI's `cluster.x-k8s.io/*` annotations identify the Machine and its MachineSet, but nothing can select on them), so nothing above can be confined to one backend |

Metrics-server does not depend on the backend. Konnectivity itself is node-agnostic apart from reachability. Per-pool `MachineHealthCheck` works for any CAPI provider but does not exist for non-CAPI pools. `kubernetes-switchcloud` covers the reachability rows by pointing machine config and Cilium at the ingress hostname, adding the per-node proxy for in-cluster API traffic, and routing trustd and konnectivity through its SNI router.

## Goals

- The hybrid design (#9) has a verified list of components that depend on the worker backend, with the failure mode of each on a foreign node.
- Each inventory row points at the file or upstream document it is based on, at a pinned revision where one exists.

### Non-goals

- Choosing a hybrid architecture. That stays with #9.
- Any code change. Every direction below would get its own proposal or be folded into #9.

## Design

Possible directions, non-committal:

- **Each backend brings its component set**: CSI driver and storage classes, load-balancer path, supported ingress exposure, machine-config template. The KubeVirt backend's set is today's components, unchanged.
- **Backend and pool labels on nodes**, with every node-local backend component selecting on the backend label. `node.cluster.x-k8s.io` is owned by Cluster API, so the keys should live in a Cozystack domain (for example `cozystack.io/backend`, `cozystack.io/pool`) synced through CAPI's `--additional-sync-machine-labels`, or under `node-restriction.kubernetes.io`, which CAPI syncs without a flag (Cluster API v1.10.1 metadata propagation docs). Non-CAPI backends can set a Cozystack-domain label from their machine config, at the cost that the node labels itself and could claim another backend; the kubelet may not apply `node-restriction.kubernetes.io` to itself (Kubernetes `NodeRestriction` admission), so a management-side controller (such as the node-lifecycle controller sketched in #9) would have to set that domain.
- **Topology-aware KubeVirt storage**: `WaitForFirstConsumer` plus topology reported by the driver (`NodeGetInfo`, accessible topology in `CreateVolume`, the provisioner's topology feature). This is an upstream or patched-driver change.
- **kccm scoped to its own nodes by contract**: for a `providerID` scheme it does not own, report the node as existing in `InstanceExists` (never as absent, which deletes it) and return no metadata from `InstanceMetadata`, pinned by a test, instead of relying on the current error and name-lookup paths.
- **CAPI infrastructure providers for cloud backends**: this conflicts with the Out of scope entry of #9 that bypasses CAPI for cloud-VM workers, and is unresolved. A CAPI-backed cloud pool would reuse the `clusterapi` autoscaler (with its infrastructure-template RBAC widened), CAPI `Node` cleanup and label sync. It does not give mixed clusters: CAPO and CAPH resolve their infrastructure cluster from `Cluster.spec.infrastructureRef`, so a pool of another CAPI provider cannot join a `Cluster` whose infrastructure is `KubevirtCluster`. `kubernetes-switchcloud` uses CAPO for the whole cluster, not per pool.

## User-facing changes

None. This proposal documents current behaviour and changes no code.

## Upgrade and rollback compatibility

Not applicable: no code change.

## Security

No change. Two observations for the hybrid design: a node that sets its own backend label could claim another backend's components (see the labels direction), and the current kccm name lookup can hand a foreign node the addresses of a VMI with the same name (see the inventory).

## Failure and edge cases

Covered per component in the inventory.

## Testing

Not applicable to this document. The kccm direction includes a test that pins how foreign nodes are treated.

## Rollout

None. The inventory should be refreshed against the then-current revision when #9 resumes.

## Open questions

- In a cluster with pools of several backends, which backend's storage class is the default, and does a PVC created before any pool of that backend exists stay pending or fail?
- If the CAPI-provider direction is taken: since CAPO and CAPH cannot join a `Cluster` whose infrastructure belongs to another provider, is a hybrid cluster "one CAPI infrastructure provider per cluster plus non-CAPI pools", or does mixing need a second `Cluster` object per backend?
- The scope and sketches of #9 assume a native-provider `cluster-autoscaler` per cloud pool. How do those instances coexist with each other and with the `clusterapi` one: lease name and namespace per instance, non-overlapping node groups, one discovery scope per provider?

## Alternatives considered

- **Adding this section directly to #9.** #9 is another author's placeholder in a fork branch, so a separate document keeps its text untouched and lets it be merged or dropped independently.
- **A file next to the #9 README, in its directory, once #9 merges.** #9 is deferred with no date, so the inventory would wait for an unrelated decision and go stale meanwhile.
- **A review comment on #9.** Not discoverable from the design-proposals tree and not reviewable line by line.
