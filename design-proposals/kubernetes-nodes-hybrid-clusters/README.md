# Hybrid kubernetes clusters: workers in external environments

- **Title:** `Hybrid kubernetes clusters: workers in external environments`
- **Author(s):** `@kvaps`
- **Date:** `2026-05-04`
- **Status:** Draft (deferred)

## Overview

This proposal is a **placeholder for Phase 3** of the kubernetes-application reshape. The detailed design will be filled in only after the preceding work lands.

**Prerequisite**: [`Migrate kubernetes workers to Talos and split control-plane from node pools`](../kubernetes-nodes-split/) (PR #8) — which delivers Phase 1 (Talos migration) and Phase 2 (package split). Nothing in this Phase 3 proposal is meaningful before that lands.

## Scope (intended)

Hybrid kubernetes clusters: workers that live **outside** the Cozystack management cluster. Concretely, the use cases that should drive this design:

- **External cloud workers**: a tenant cluster running its control-plane in Cozystack (Kamaji) but its worker nodes as cloud VMs in Hetzner, Azure, AWS, GCP, etc. Driven by `cluster-autoscaler` with the cloud's native provider, not by CAPI.
- **BYO clusters**: tenants who bring their own cloud account and want their pool to be billed against that account rather than the Cozystack platform's. Implies admin-managed *or* tenant-managed location ownership.
- **Bare metal / on-premise workers**: a tenant wanting nodes in their own datacenter joined to a Cozystack-hosted control-plane.

The Novolos use case is the concrete driving example: workers in different tenant clouds, each with their own `cluster-autoscaler`, all joining a single managed Kamaji control-plane.

## Why deferred

Three reasons:

1. The package split delivered by Phase 2 (PR #8) is the architectural seam Phase 3 needs. Designing external backends before the split is in place forces shoehorning them into the monolithic `kubernetes` chart's `nodeGroups`, which doesn't fit semantically and burns design effort that Phase 2 reclaims.
2. The Talos worker base delivered by Phase 1 (PR #8) is what makes external workers tractable in the first place. Ubuntu + kubeadm joining a remote Kamaji cluster is operationally awkward; Talos + machineconfig over cloud-init is the path of least resistance for both KubeVirt VMs (in-cluster) and cloud VMs (external).
3. Several open Cozystack-side decisions (admin- vs tenant-owned location ownership, credential model for BYO clouds, default deny vs explicit advertise, dashboard surfacing) are best made with concrete Phase 1 + 2 operational experience in hand, not in advance.

## Sketches (non-committal, for orientation)

Several patterns were raised during early discussion of PR #8. They are listed here so the conversation does not restart from zero when work resumes, but **none of them is committed**.

- **New `backend.type` field in `kubernetes-nodes`.** The single-backend "kubevirt-talos" shape from Phase 2 grows a discriminator: `kubevirt-talos`, `cloud-talos-hetzner`, `cloud-talos-azure`, etc. Per-backend sub-charts realise the actual lifecycle (CAPI for KubeVirt-VM backends; `cluster-autoscaler` directly against the cloud's native API for cloud backends).
- **`LocationProfile` CRD** declaring "how to provision in a given location" — credentials, base image reference, region. Owned by the platform admin in `cozy-system` or, optionally, by the tenant in their own namespace (Novolos-style BYO).
- **Node-lifecycle controller (NLC)** from `cozystack/local-ccm` deployed inside the tenant cluster to remove zombie `Node` objects when `cluster-autoscaler` deletes a cloud VM. For CAPI-backed pools (KubeVirt) NLC is not needed; the CAPI machine controller already cleans up the `Node`.
- **Talos image source per backend**: pre-baked snapshot/VHD for cloud providers, KubeVirt-friendly disk image for KubeVirt-VM workers. Defined in the `LocationProfile` (or equivalent) so tenants reference rather than describe.
- **Scheduling-class semantics**: `tenant.spec.scheduling` continues to apply to `kubevirt-*` backends (real VMs scheduled in the management cluster). For `cloud-talos-*` backends it does not apply — there is no management-cluster pod. Equivalent tenant-scoping for cloud backends moves to RBAC on `LocationProfile` (or equivalent).
- **Credentials for BYO cloud**: tenant-namespace Secret referenced by the tenant's own `LocationProfile`. The management-cluster `cluster-autoscaler` for that pool mounts the Secret over the existing kubeconfig path, so credentials never escalate to platform level.

## Backend-bound components

The sketches above cover how a pool of another backend gets its machines. The other half is what already runs in every tenant cluster: the `kubernetes` and `kubernetes-nodes` charts deploy components that assume each worker is a KubeVirt VM created by CAPK. In a hybrid cluster these either have to work on any node or have to come with their backend and stay on its nodes. The inventory below is a starting list for Phase 3, taken at `cozystack/cozystack` `ff52aeb44` (upstream: `kubevirt/csi-driver` `27b52aa22da7`, `kubevirt/cloud-provider-kubevirt` `a0acf33` plus Cozystack patches, Cluster API v1.10.1).

A working precedent for a non-KubeVirt backend exists: [`aenix-org/kubernetes-switchcloud`](https://github.com/aenix-org/kubernetes-switchcloud), a tenant-cluster chart for OpenStack (CAPO) workers running Talos against a Kamaji control plane in Cozystack. It points its generic addons at the same `cozystack-kubernetes-application-kubevirt-kubernetes-*` chart artifacts with backend-specific values. On top of that it needed several management-side packages of its own (an SNI router exposing konnectivity and Talos trustd, a load-balancer controller so the tenant cluster carries no cloud CCM or credentials, a per-cluster CSR signer as its own Deployment where Cozystack embeds `talos-csr-signer` as a sidecar of the Kamaji control-plane pod, the CAPO infrastructure provider, and its own copy of the Talos bootstrap provider that Cozystack now ships as well), and two in-cluster components: a DaemonSet that sets `spec.providerID` from instance metadata, and a per-node proxy that makes `kubernetes.default.svc` reachable from outside the management pod network. Today such a set can only ship as a separate chart that duplicates the control-plane part of `kubernetes`; a backend of `kubernetes-nodes` is a candidate shape for it.

### Inventory

Paths are in `cozystack/cozystack` unless stated.

| Component | Where it is defined | KubeVirt assumption | On a node of another backend |
| --- | --- | --- | --- |
| KubeVirt CSI node plugin | `packages/apps/kubernetes/templates/helmreleases/csi.yaml`, `packages/system/kubevirt-csi-node/templates/deploy.yaml`, `packages/apps/kubernetes/images/kubevirt-csi-driver/main.go` | Runs on every node (all taints tolerated, no `nodeSelector`); node ID is `<cluster-namespace annotation>/<providerID without kubevirt://>` | Non-CAPI node: exits fatally, never registers. Another CAPI provider (only possible with a second `Cluster` object, see below): starts with a node ID that is not a valid `namespace/name` key |
| KubeVirt storage classes | same chart; upstream `pkg/service/node.go`, `pkg/service/controller.go` | Default class, `Immediate` binding, no topology from `NodeGetInfo` | A pod with such a PVC can land anywhere. Attach then fails: on a non-CAPI node the external-attacher finds no driver in `CSINode`; on another provider's node `ControllerPublishVolume` cannot parse the node ID |
| kubevirt-cloud-controller-manager | `packages/apps/kubernetes/templates/kccm/manager.yaml`; upstream `pkg/provider/instances_v2.go` | Default controller set; `InstanceExists` returns an error for a non-`kubevirt://` `providerID` | Not deleted today: the node-lifecycle controller only checks NotReady nodes and skips on error. The cloud-node controller's periodic address sync looks the node up by name (or the `node.kubernetes.io/instance-id` label), never by `providerID`, and skips it when no VMI matches; a foreign node whose name matches the name or `spec.hostname` of any VMI in the cluster's namespace (including other clusters' workers and tenant VMs) would get that VMI's addresses. Incidental, not a contract. `LoadBalancer` endpoints are mapped to VMIs by node name and foreign nodes are dropped |
| ingress-nginx exposure | `packages/apps/kubernetes/templates/ingress.yaml` | `Proxied` Service selects `cluster.x-k8s.io/cluster-name` plus the ingress role label, i.e. the virt-launcher pods | Never an endpoint in `Proxied`; dropped by kccm in `LoadBalancer` |
| cluster-autoscaler | `packages/apps/kubernetes/templates/cluster-autoscaler/deployment.yaml` | `--cloud-provider=clusterapi`, discovery scoped to the cluster, RBAC for `kubevirtmachinetemplates` only | Invisible unless backed by a CAPI `MachineDeployment` it has RBAC for. A second native-provider instance is not a documented upstream setup (the FAQ covers a second instance only for check-capacity `ProvisioningRequest`s) and would contend for the default leader lease |
| Worker machine config | `packages/apps/kubernetes-nodes/templates/talos-reconcile-job.yaml` | API endpoint `https://<release>.<namespace>.svc:6443` resolved through `extraHostEntries` to the API server ClusterIP; management CoreDNS as resolver; install disk `/dev/vda` | Unreachable outside the management pod network |
| Control-plane side ports | `packages/apps/kubernetes/templates/cluster.yaml` | API server already has an external SSL-passthrough ingress hostname (added to the SANs by the Kamaji control-plane provider, not listed in the chart's `certSANs`); trustd (50001) and konnectivity only as ClusterIP ports | Can join through the ingress hostname, but cannot reach trustd or konnectivity |
| Cilium | `packages/apps/kubernetes/templates/helmreleases/cilium.yaml` | `k8sServiceHost` is the in-cluster `svc` name | Needs the external hostname instead |
| GPU | `packages/apps/kubernetes/templates/helmreleases/gpu-operator.yaml`, `talos-reconcile-job.yaml` | `NVreg_NvLinkDisable=1` chosen for PCI passthrough, cluster-wide; HAMi's `gpu: "on"` label set only through the KubeVirt pool's Talos `nodeLabels` | NVLink disabled on bare metal; HAMi not scheduled without the label |
| Node labels | `packages/apps/kubernetes-nodes/templates/nodegroup.yaml` | Only `node-role.kubernetes.io/<role>` through CAPI Machine-to-Node sync, plus `gpu: "on"` above | No pool, backend or location label (CAPI's `cluster.x-k8s.io/*` annotations identify the Machine and its MachineSet, but nothing can select on them), so nothing above can be confined to one backend |

Metrics-server does not depend on the backend. Konnectivity itself is node-agnostic apart from reachability. Per-pool `MachineHealthCheck` works for any CAPI provider but does not exist for non-CAPI pools. `kubernetes-switchcloud` covers the reachability rows by pointing machine config and Cilium at the ingress hostname, adding the per-node proxy for in-cluster API traffic, and routing trustd and konnectivity through its SNI router.

### Possible directions (non-committal)

- **Each backend brings its component set**: CSI driver and storage classes, load-balancer path, supported ingress exposure, machine-config template. The KubeVirt backend's set is today's components, unchanged.
- **Backend and pool labels on nodes**, with every node-local backend component selecting on the backend label. `node.cluster.x-k8s.io` is owned by Cluster API, so the keys should live in a Cozystack domain (for example `cozystack.io/backend`, `cozystack.io/pool`) synced through CAPI's `--additional-sync-machine-labels`, or under `node-restriction.kubernetes.io`, which CAPI syncs without a flag. Non-CAPI backends can set a Cozystack-domain label from their machine config, at the cost that the node labels itself and could claim another backend; the kubelet may not apply `node-restriction.kubernetes.io` to itself, so a management-side controller (such as the node-lifecycle controller from the Sketches) would have to set that domain.
- **Topology-aware KubeVirt storage**: `WaitForFirstConsumer` plus topology reported by the driver (`NodeGetInfo`, accessible topology in `CreateVolume`, the provisioner's topology feature). This is an upstream or patched-driver change.
- **kccm scoped to its own nodes by contract**: for a `providerID` scheme it does not own, report the node as existing in `InstanceExists` (never as absent, which deletes it) and return no metadata from `InstanceMetadata`, pinned by a test, instead of relying on the current error and name-lookup paths.
- **CAPI infrastructure providers for cloud backends**: this conflicts with the "CAPI is bypassed" entry under Out of scope and is unresolved. A CAPI-backed cloud pool would reuse the `clusterapi` autoscaler (with its infrastructure-template RBAC widened), CAPI `Node` cleanup and label sync. It does not give mixed clusters: CAPO and CAPH resolve their infrastructure cluster from `Cluster.spec.infrastructureRef`, so a pool of another CAPI provider cannot join a `Cluster` whose infrastructure is `KubevirtCluster`. `kubernetes-switchcloud` uses CAPO for the whole cluster, not per pool.

## Out of scope (always)

- Anything that should land in Phase 1 or Phase 2 of PR #8.
- Worker bootstrap mechanisms other than Talos. Ubuntu + kubeadm is removed in Phase 1 and not revisited.
- Removing Cluster API entirely. For KubeVirt-VM workers, CAPI + CAPK remains the path. For cloud-VM workers, CAPI is bypassed (autoscaler + native cloud provider), but this is per-backend and does not imply CAPI removal elsewhere.

## Open questions

Will be filled in when work resumes. Initial set, for orientation:

- Admin vs tenant ownership of locations: which is the default, are both supported, what does RBAC look like?
- One `LocationProfile` per (location, backend) combination, or one per location with per-backend fields?
- Pool-level vs Location-level credentials? (Per-pool gives finer granularity; per-Location is simpler.)
- Naming conventions for cloud-backend pools, especially when many locations exist.
- How `Cilium` and the Cozystack networking layer (Kilo mesh, externalIPs CCM) interact with cloud-VM workers — what overlay/encryption is mandatory.
- In a cluster with pools of several backends, which backend's storage class is the default, and does a PVC created before any pool of that backend exists stay pending or fail?
- If the CAPI-provider direction is taken: since CAPO and CAPH cannot join a `Cluster` whose infrastructure belongs to another provider, is a hybrid cluster "one CAPI infrastructure provider per cluster plus non-CAPI pools", or does mixing need a second `Cluster` object per backend?
- Scope and Sketches assume a native-provider `cluster-autoscaler` per cloud pool. How do those instances coexist with each other and with the `clusterapi` one: lease name and namespace per instance, non-overlapping node groups, one discovery scope per provider?

## Status and next steps

This document is held in draft pending PR #8 landing. Once Phase 1 + Phase 2 ship and are operationally stable, this draft is the entry point for restarting Phase 3 design work.
