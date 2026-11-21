# Per-cluster infrastructure backends for tenant Kubernetes clusters

- **Title:** `Per-cluster infrastructure backends for tenant Kubernetes clusters`
- **Author(s):** `@IvanHunters`
- **Date:** `2026-09-24`
- **Status:** Draft
- **Supplements:** [`cozystack/community#9`](https://github.com/cozystack/community/pull/9) by `@kvaps`

## Overview

A tenant Kubernetes cluster in Cozystack today always runs its workers as KubeVirt VMs. This proposal makes the worker infrastructure a per-cluster choice, fixed when the cluster is created: KubeVirt stays the default backend, and other backends plug in beside it. OpenStack is the first candidate, because [`aenix-org/kubernetes-switchcloud`](https://github.com/aenix-org/kubernetes-switchcloud) already runs Talos workers on OpenStack against a Kamaji control plane in Cozystack, as a separate chart.

The position, in short:

1. One CAPI infrastructure provider per cluster. A backend is a CAPI infrastructure provider plus the components listed in §3; a cluster uses exactly one.
2. A backend is its own `PackageSource` with a control-plane application and a node-pool application, mirroring today's `kubernetes` / `kubernetes-nodes` split. Variants cannot do this, because a variant is chosen for the whole platform.
3. The backend-neutral part of today's `kubernetes` and `kubernetes-nodes` charts moves into a shared library chart, so a backend adds only what is specific to it instead of forking the control plane, as `kubernetes-switchcloud` does today.
4. A backend has a written contract: the list of responsibilities it must cover, derived from an inventory of what is KubeVirt-bound today.
5. Existing KubeVirt clusters do not change: the KubeVirt backend renders the same objects as today.

Mixed clusters, with workers of several backends under one control plane, stay with #9.

## Scope and related proposals

- [`cozystack/community#9`](https://github.com/cozystack/community/pull/9), hybrid kubernetes clusters (Phase 3 placeholder, draft, deferred). It lists external cloud workers, BYO clouds and bare metal as use cases, and sketches `backend.type` on `kubernetes-nodes`, a `LocationProfile` CRD, and cloud pools that bypass CAPI: a native-provider `cluster-autoscaler` per cloud pool plus a node-lifecycle controller. This proposal differs on one point, for single-backend clusters only: it uses CAPI infrastructure providers (CAPO, later CAPH) for cloud workers instead of bypassing CAPI, for the reasons under Alternatives. Mixed clusters, `LocationProfile` and shared credential models stay with #9, and #9's non-CAPI pools remain the way to mix backends. The `cluster-autoscaler-hetzner` and `cluster-autoscaler-azure` packages already in Cozystack scale the management cluster's own nodes, not tenant clusters, so they do not cover this case.
- [`kubernetes-nodes-split`](../kubernetes-nodes-split/) (accepted) split the control plane from node pools and deferred `backend.type` to Phase 3. The library proposed here is cut along that same split.

## Decisions

## Context

Facts below are taken at `cozystack/cozystack` `ff52aeb44`, with `clastix/cluster-api-control-plane-provider-kamaji` v0.19.0, Cluster API v1.10.1, CAPO v0.12.1, `kubevirt/csi-driver` `27b52aa22da7`, `kubevirt/cloud-provider-kubevirt` `a0acf33` plus Cozystack patches, and `kubernetes-switchcloud` v0.11.9.

### The problem

1. A tenant who needs workers outside KubeVirt (another cloud, a BYO account) has no Cozystack application for it. The only working example is a separate chart that copies the control plane.
2. A copy drifts. The components that a non-KubeVirt backend needs are nowhere written down, so every new backend rediscovers them.

### Why one CAPI infrastructure provider per cluster

- A CAPI `Cluster` has a single `infrastructureRef`; ours points at `KubevirtCluster` (`packages/apps/kubernetes/templates/cluster.yaml`). Infrastructure providers resolve their infra cluster from it: CAPO (`controllers/openstackmachine_controller.go`, `getInfraCluster`) and CAPH (`controllers/hcloudmachine_controller.go`) both fetch their own kind by `Cluster.spec.infrastructureRef.name`. A pool of another CAPI provider cannot join a `KubevirtCluster`-backed `Cluster`.
- Two `Cluster` objects cannot share one control plane either. The Kamaji control-plane provider takes its `Cluster` from the first owner reference of the `KamajiControlPlane` (`controllers/kamajicontrolplane_controller.go`), and CAPI sets that reference as the controller owner, which refuses a second one. The `TenantControlPlane` is created 1:1 from the `KamajiControlPlane`.
- Workers that are not CAPI machines (joined with a machine config and a bootstrap token) are not limited by this. That is the path #9 sketches for mixed clusters.

### Why not a variant

A `PackageSource` variant is selected by the `Package` of the same name (`api/v1alpha1/package_types.go`, `internal/operator/package_reconciler.go`), so exactly one variant of a source is active on a platform. The `kubernetes` and `kubernetes-nodes` sources already use a `kubevirt` variant (`packages/core/platform/sources/kubernetes-application.yaml`), and artifact names are derived as `<source>-<variant>-<component>` (`internal/operator/packagesource_reconciler.go`), for example `cozystack-kubernetes-application-kubevirt-kubernetes`. A second variant would switch every cluster on the platform, not add a choice per cluster.

### The precedent and its cost

`kubernetes-switchcloud` ships as its own package source (in an older source format that has to be re-declared with `variants` for the port) and a single `ApplicationDefinition` (`KubernetesSwitchcloud`) with node groups inline in its values. It brings:

- in its application chart: `OpenStackCluster`, per-group `OpenStackMachineTemplate`, a Secret with OpenStack credentials rendered from fields of the cluster's values (or an existing Secret), a DaemonSet that sets `spec.providerID` from instance metadata, a per-node proxy that makes `kubernetes.default.svc` reachable from outside the management pod network, a per-cluster Talos CSR signer, a per-cluster konnectivity Service for the SNI router, and optional Kilo;
- as platform-side HelmReleases of its platform chart: the CAPO provider, its own copy of the Talos bootstrap provider (Cozystack already installs one), an SNI router (`talos-edge-router`) that exposes Talos trustd and konnectivity, `kilo-clustermesh-operator`, and a load-balancer controller, always installed and enabled per cluster, that keeps cloud credentials out of the tenant cluster.

The rest (the Kamaji control plane, the CAPI `Cluster`, addon HelmReleases, the delete hook, resource math) is a copy of the `kubernetes` chart that has since drifted; its `delete.yaml`, for example, differs from Cozystack's by several hundred lines. It offers six of the generic addons (Cilium, CoreDNS, cert-manager and its CRDs, ingress-nginx, metrics-server) out of the eighteen addon HelmReleases of the `kubernetes` chart, and points them at artifacts named after the KubeVirt variant (`cozystack-kubernetes-application-kubevirt-kubernetes-*`).

### What is KubeVirt-bound today

This is the inventory the backend contract is derived from. Paths are in `cozystack/cozystack` unless stated.

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
| Management-side service endpoints | `packages/apps/kubernetes/templates/helmreleases/monitoring-agents.yaml` | Remote write to `vminsert-shortterm.<tenant>.svc` and `vlinsert-generic.<tenant>.svc` of the management cluster | Unreachable outside the management pod network |

Backend-neutral today: the `KamajiControlPlane` and CAPI `Cluster` block apart from `infrastructureRef`, addon HelmReleases apart from CSI and those with management-side endpoints, OIDC and Talos PKI templates, the delete hook, `MachineDeployment`, `MachineHealthCheck` and `WorkloadMonitor` in `kubernetes-nodes`, and the `clusterapi` cluster-autoscaler apart from its RBAC. Talos KubePrism is already disabled on KubeVirt workers.

## Goals

- A tenant can create a Kubernetes cluster on a backend other than KubeVirt through a Cozystack application, with the same control plane and lifecycle as a KubeVirt cluster, and every addon whose contract rows the backend covers.
- Adding a backend does not require copying the control-plane chart, and can be done out of tree.
- Existing KubeVirt clusters render the same objects before and after the change.
- The responsibilities of a backend are written down and testable.

### Non-goals

- Mixed clusters (workers of several backends under one control plane). They stay with #9.
- `LocationProfile`, admin-owned locations and shared credentials. The first external backend takes per-cluster credentials by Secret reference (§5); #9 can add profiles on top.
- Cross-cluster networking (Kilo mesh). `kubernetes-switchcloud` ships it as an option; whether it becomes part of a backend or a separate feature is left to #9, which already asks how Kilo interacts with cloud workers.
- Changing the user-facing `Kubernetes` / `KubernetesNodes` API of KubeVirt clusters.

## Design

### 1. Backend packages

A backend is a `PackageSource` with a `default` variant that provides:

- a control-plane application (`ApplicationDefinition`, for example `KubernetesOpenstack`) whose chart renders the shared control plane from the library plus the backend's infra cluster object and backend components;
- a node-pool application (for example `KubernetesOpenstackNodes`) whose chart renders the shared pool objects from the library plus the backend's machine template;
- `dependsOn` for the platform packages it needs, listed directly as the in-tree Kubernetes sources do: networking and the CAPI operator, the CAPI core, Kamaji control-plane and bootstrap packages (`cozystack.capi-provider-core`, `cozystack.capi-provider-cp-kamaji`, `cozystack.capi-provider-bootstrap-kubeadm`, which also installs the Talos bootstrap provider, so the backend does not ship its own), `cozystack.cozystack-engine` for the `ApplicationDefinition` CRD, its CAPI infrastructure provider, the addon source (§4) and, for backends outside the management pod network, the exposure components (SNI router, load-balancer controller).

The KubeVirt backend is today's `kubernetes-application` and `kubernetes-nodes-application` sources, kinds `Kubernetes` and `KubernetesNodes`, unchanged. The same two charts are also published a second time by `computeplane-application` for the ComputePlane module; that source is a second in-tree consumer of the KubeVirt backend and follows every change below. The OpenStack backend is `kubernetes-switchcloud` ported onto the library and split into two applications like `kubernetes` / `kubernetes-nodes`; it can stay in `aenix-org` and reach platforms through the marketplace like any other source.

For example, the OpenStack backend's source could look like this (names illustrative):

```yaml
apiVersion: cozystack.io/v1alpha1
kind: PackageSource
metadata:
  name: aenix.kubernetes-openstack
spec:
  sourceRef:
    kind: OCIRepository
    name: kubernetes-openstack-packages
    namespace: cozy-system
  variants:
    - name: default
      dependsOn:
        - cozystack.networking
        - cozystack.capi-operator
        - cozystack.capi-provider-core
        - cozystack.capi-provider-cp-kamaji
        - cozystack.capi-provider-bootstrap-kubeadm
        - cozystack.cozystack-engine
        - cozystack.kubernetes-addons
        - aenix.capi-provider-openstack
        - aenix.talos-edge-router
      libraries:
        - name: cozy-lib
          path: library/cozy-lib
      components:
        - name: kubernetes-openstack
          path: apps/kubernetes-openstack
          libraries: ["cozy-lib"]
        - name: kubernetes-openstack-nodes
          path: apps/kubernetes-openstack-nodes
          libraries: ["cozy-lib"]
        - name: kubernetes-openstack-rd
          path: system/kubernetes-openstack-rd
          install:
            namespace: cozy-system
            releaseName: kubernetes-openstack-rd
```

### 2. Shared library chart

A Helm library chart holds what is backend-neutral today:

- control plane: `KamajiControlPlane`, the CAPI `Cluster` with `infrastructureRef` supplied by the backend, konnectivity and trustd sidecar wiring, OIDC, Talos PKI and secrets, the bootstrap-token Job, `WorkloadMonitor`, dashboard resource map, the delete hook and resource math;
- addons: the addon HelmReleases, pointing at the addon source (§4);
- node pools: `MachineDeployment`, `MachineHealthCheck`, `WorkloadMonitor` and the `talos-reconcile` Job that produces the `TalosConfigTemplate`, with the machine template reference and the backend's machine-config patch supplied by the backend.

Helm renders only named templates from a library chart, so every object is emitted by a template of the backend chart that `include`s the library, and render parity is checked on the backend chart's output. The backend's charts call the library and fill defined hook points (infra cluster, machine template, machine-config patch, backend component HelmReleases). Things that are hard-coded to the KubeVirt kinds today become library parameters: the parent application kind and the pool release prefix (the pool chart labels machines with `apps.cozystack.io/application.kind: Kubernetes` and requires its release name to start with `kubernetes-nodes-<cluster>-`; the parent release name is already a value), and the version and image tables read with `.Files.Get`. `.Files` stays the parent chart's, so these files keep being generated and copied into each backend chart by its Makefile, as `kubernetes-nodes` already copies them from `kubernetes`. `cozystack-api` applies the Helm release-name cap to every kind, plus a stricter cap for kind `Kubernetes` derived from the `kubernetes-nodes-` prefix of its pools (`pkg/registry/apps/application/rest.go`); that stricter cap becomes a per-`ApplicationDefinition` setting expressed as the child pool prefix, since `kubernetes-openstack-nodes-` leaves a shorter budget than `kubernetes-nodes-`.

Distribution: in-tree backends consume the library through `PackageSource` `libraries`, which copies it from the same source into the component's `charts/` directory, so its version is the bundle's. Libraries are declared per variant, so all three in-tree sources that publish these charts (`kubernetes-application`, `kubernetes-nodes-application` and `computeplane-application`) add it. Out-of-tree backends cannot take the library from Cozystack's source this way, because `libraries` copies only from the declaring source. The library is also published as a versioned OCI Helm chart; out-of-tree backends vendor a pinned copy, as a Helm dependency or as a `libraries` entry of their own source, and upgrade it on their own schedule.

The first cut of the library is extracted from the KubeVirt charts with a render-parity check, so the KubeVirt backend produces byte-identical manifests.

### 3. Backend contract

A backend must state how it covers each row. The KubeVirt answers are today's components; the OpenStack answers are what `kubernetes-switchcloud` ships today.

| Responsibility | KubeVirt backend | OpenStack backend (`kubernetes-switchcloud`) |
| --- | --- | --- |
| Infra cluster and machine template | `KubevirtCluster`, `KubevirtMachineTemplate` | `OpenStackCluster`, `OpenStackMachineTemplate` |
| Node `providerID` | set by CAPK | DaemonSet reading instance metadata |
| Storage: CSI driver and default storage class | KubeVirt CSI | none shipped today |
| `Service type: LoadBalancer` | kubevirt-cloud-controller-manager | management-side load-balancer controller, enabled per cluster |
| Ingress exposure methods | `Proxied`, `LoadBalancer` | `LoadBalancer` |
| API server endpoint for workers and Cilium | in-cluster `svc` name | Kamaji ingress hostname |
| In-cluster API path (`kubernetes.default.svc`) | management pod network | per-node proxy |
| Talos trustd and konnectivity reachability | ClusterIP in the management pod network | SNI router |
| Management-side services used by addons (monitoring) | management pod network | not covered; monitoring agents not offered |
| Machine-config specifics | install disk `/dev/vda`, management CoreDNS resolver | public endpoint and resolver |
| Cluster-autoscaler RBAC | `kubevirtmachinetemplates` | none shipped today; MachineDeployments carry scale-from-zero capacity annotations only |
| Teardown | backend HelmReleases labelled `cozystack.io/target-cluster-name` so the delete hook removes them; infra objects go with the CAPI `Cluster` | same |

A backend that cannot cover a row declares it unsupported, and its application schema does not offer the dependent options. The OpenStack port has to follow this: `kubernetes-switchcloud` currently keeps `Proxied` in its schema and fails the render instead.

### 4. Addon source

Artifact names are derived from source and variant, so addon charts cannot drop `kubevirt` from their names while they are components of the `kubernetes-application` `kubevirt` variant. They move to their own source, `cozystack.kubernetes-addons` with a `default` variant, installed as a `Package` in the bundles that install a Kubernetes backend (dependencies resolve against `Package` status), and every backend source and `computeplane-application` depend on it.

Switching is tied to the platform upgrade, not to each cluster: addon HelmReleases are rendered by the cluster's own release, whose chart comes with the bundle, so every non-suspended cluster moves to the new artifact names when the platform is upgraded. The `kubevirt` variant keeps publishing the old addon artifacts for one release, for suspended releases and for out-of-tree charts that reference them (`kubernetes-switchcloud` does).

The move must keep the chart name inside each artifact unchanged. The Helm release then sees a new chart source for the same chart and upgrades in place. The helm-controller Cozystack deploys (v1.5.0, `internal/fluxinstall/manifests/fluxcd.yaml`) uninstalls and reinstalls a release whose chart name changes (`internal/action/verify.go`, `ReleaseTargetChanged`), with no opt-out; helm-controller 1.6.0 adds `upgrade.chartNameChangeStrategy`, whose default keeps that behaviour. For Cilium that would be an outage in the tenant cluster. The addon components carry no `install` block, so `Package` orphan cleanup, which only touches releases it installed, does not act on them.

### 5. Credentials

A backend takes cloud credentials only as a reference to a Secret in the tenant namespace; the cluster's schema has no credential fields. They are used by the infrastructure provider and the load-balancer controller on the management side and are not copied into the tenant cluster. The `kubernetes-switchcloud` port drops its inline credential fields accordingly.

## User-facing changes

- Tenants see one additional pair of kinds per installed backend (for example `KubernetesOpenstack` and `KubernetesOpenstackNodes`) with backend-specific fields such as flavor, image and network.
- Existing `Kubernetes` clusters see no change.
- Platform operators enable a backend by installing its package; external backends pull in their CAPI provider and exposure components.

## Upgrade and rollback compatibility

- KubeVirt clusters: the library extraction must be render-identical, checked by snapshot tests like the one added for the `kubernetes-nodes` split, with a new one for `kubernetes`. Rollback is a normal chart downgrade.
- Addon source: the old artifact names stay published for one release (§4).
- `kubernetes-switchcloud` clusters: porting onto the library, the split into two applications and the credential change alter rendered objects, so the port needs its own migration plan in that repository. It is not a Cozystack core change.
- A cluster cannot change backend after creation. Moving workloads between backends means a new cluster.

## Security

- Cloud credentials live in the tenant namespace, are referenced rather than embedded in the application spec, and are consumed on the management side only.
- External backends expose trustd and konnectivity through an SNI router, a new externally reachable endpoint per cluster. Workers still authenticate as today: with the Talos machine token to trustd and with the konnectivity agent token.
- A backend package runs management-side controllers. From a marketplace tap it goes through the existing privileged confirmation only if its author marks those components `install.privileged`; the contract should require that.

## Failure and edge cases

- Backend package installed without its infrastructure provider: `dependsOn` keeps the package from reconciling, and the cluster application does not become available.
- A backend omits a contract row: the dependent options are absent from its schema; for storage, the cluster has no default storage class until the tenant installs one.
- Library upgrade: in-tree backends move with the bundle; out-of-tree backends pin the OCI library version and move when they choose.
- Deleting a cluster: the shared delete hook removes every HelmRelease labelled with the cluster, including backend components.

## Testing

- Render-parity snapshot tests for the KubeVirt charts before and after the library extraction.
- A contract test per backend: a rendered cluster must contain an infra cluster object, a machine template reference, a `providerID` source, and the teardown label on every backend HelmRelease.
- E2E for the KubeVirt backend unchanged. E2E for the OpenStack backend lives with that backend.

## Rollout

1. Move addon charts to `cozystack.kubernetes-addons`, keeping the old artifacts for one release, and point `kubernetes-application` and `computeplane-application` at it.
2. Extract the library from the KubeVirt charts with render parity; ship it through `libraries` in all three in-tree sources and as an OCI chart.
3. Make the kind-specific points in `cozystack-api` per-`ApplicationDefinition`.
4. Publish the backend contract (§3) in the Cozystack docs.
5. Port `kubernetes-switchcloud` onto the library, the addon source and Secret-referenced credentials. The port, or at least a re-point of its addon `chartRef`s, has to land before the release that stops publishing the old addon artifact names.
6. Further backends (for example Hetzner through CAPH) follow the same contract.

## Open questions

- One pair of kinds per backend (`KubernetesOpenstack`) or a single `Kubernetes` kind with a backend selector that the API maps to the right application? The second is nicer for tenants but needs API work.
- Where should the SNI router and load-balancer controller live: in each backend package, or as shared platform packages any external backend can depend on?
- Which addons with management-side endpoints (monitoring today) should external backends support, and through which exposure?

## Alternatives considered

- **A variant per backend.** Rejected: a variant is platform-wide, so it would move every cluster to the new backend (see Context).
- **One `kubernetes` chart with a backend field.** Keeps a single kind, but every backend lands in the core chart, adding one needs a core release, and the chart carries every provider's dependencies. It also prevents out-of-tree backends like `kubernetes-switchcloud`.
- **Cloud workers outside CAPI (the #9 sketch: native-provider autoscaler per pool, node-lifecycle controller).** It is the only way to mix backends in one cluster, and it stays with #9 for that. For single-backend clusters it gives up what CAPI already provides here: `MachineHealthCheck` remediation, rolling machine replacement, `Node` cleanup, label sync and the `clusterapi` autoscaler, and it needs a node-lifecycle controller to replace them. `kubernetes-switchcloud` shows the CAPI route working with CAPO.
- **Two CAPI clusters sharing one control plane.** Not possible: the Kamaji provider and CAPI allow one owning `Cluster` per `KamajiControlPlane`.
- **Status quo: each backend forks the control-plane chart.** This is what `kubernetes-switchcloud` does today, and its copy has already drifted from Cozystack's.
