# Kairos as a second worker OS for tenant Kubernetes pools

- **Title:** `Kairos as a second worker OS for tenant Kubernetes pools, selected through a hoster-side NodeGroupClass`
- **Author(s):** `@lexfrei`

## Overview

I propose that a `KubernetesNodes` pool can run Kairos workers next to Talos ones. A Kairos worker boots an immutable disk image built from an OCI image and joins the tenant control plane with `kubeadm join` through the Kairos Cluster API bootstrap provider. Talos stays the default, and a pool that does not ask for Kairos renders exactly as it does today.

The OS choice, and for Kairos pools the worker image, move off the pool into a new cluster-scoped, hoster-owned, immutable `NodeGroupClass`, in the way a PVC names a `StorageClass`. A pool names a class. The platform ships two classes, `talos` and `kairos`, and a hoster adds classes for its own images.

The reason for a second OS is narrow: a hoster who needs a kernel module or a driver that the platform does not ship can add it to a Kairos worker image with one `FROM` layer on a platform image, while on Talos the same module means building and hosting a custom Talos image for every Talos release. Everything else in this proposal is about making that option cost as little as possible for the platform and for pools that never use it.

## Scope and related proposals

- **Builds on [`kubernetes-nodes-split`](../kubernetes-nodes-split/).** One pool per `KubernetesNodes` HelmRelease stays as it is. This proposal adds a second bootstrap path to that chart and a class the pool points at.
- **Labels and taints** follow [cozystack/community#103](https://github.com/cozystack/community/pull/103), approved and pending merge as decision 0001 of `kubernetes-nodes-split`: one field set that both OS paths render. This proposal uses it as is.
- **GPUs are out of scope.** [cozystack/community#99](https://github.com/cozystack/community/pull/99) (GPU devices), [#100](https://github.com/cozystack/community/pull/100) (GPU virtual machines) and [#101](https://github.com/cozystack/community/pull/101) (GPU in Kairos worker pools) come after this one. #101 is on hold until this proposal is settled. The class below leaves room for the fields #101 needs, see [Extending the class](#extending-the-class).
- **Implementation**, all draft or open on 2026-10-23: [cozystack/cozystack#4902](https://github.com/cozystack/cozystack/pull/4902) (Cluster API v1.13.7, a prerequisite), [#4911](https://github.com/cozystack/cozystack/pull/4911) (Kairos pools), [#4927](https://github.com/cozystack/cozystack/pull/4927) (labels and taints), [#4949](https://github.com/cozystack/cozystack/pull/4949) (scale from zero). #4911 changes before it merges: its `os` and `kairos.image` pool fields are replaced by the class, see [Rollout](#rollout).

## Decisions

## Context

### What a pool is today

A `KubernetesNodes` pool renders a `MachineDeployment`, a machine template for its substrate (`KubevirtMachineTemplate` or `ProxmoxMachineTemplate`), a `MachineHealthCheck` and a `WorkloadMonitor`, and its `talos-reconcile` Job produces the `TalosConfigTemplate` (`packages/apps/kubernetes-nodes/`). The tenant cluster has one cluster-autoscaler (`packages/apps/kubernetes/templates/cluster-autoscaler/`), and each pool carries its autoscaler settings as annotations on its `MachineDeployment`. Workers are Talos (`talos.version: v1.13.6` in that chart's `values.yaml` on main), bootstrapped by CABPT, with `talos-csr-signer` next to the Kamaji control plane. On KubeVirt the worker disk comes either from the Talos Image Factory over HTTP or, with `osImage.builtin`, as a CDI clone of a golden DataVolume that `packages/system/kubernetes-worker-image` imports once into `cozy-public`. On Proxmox it is a clone of a Talos VM template selected by `proxmox.templateTags`.

`kubernetes-nodes-split` set the goal that workers are uniformly Talos and that no kubeadm path remains. This proposal brings a kubeadm join back, for opt-in pools only. Talos stays the path every existing pool and every new pool without a class takes.

### What a Kairos worker is

The draft in cozystack/cozystack#4911 builds one worker image per supported Kubernetes minor from `packages/apps/kubernetes-nodes/images/kairos-worker`:

- the OS base is Hadron v0.5.3, a musl-based Kairos distribution;
- kubelet is built from source with `CGO_ENABLED=0`, because the upstream release kubelet is linked against glibc and does not start on musl; kubeadm is the static upstream release binary;
- containerd 2.2.9, runc v1.5.2 and CNI plugins v1.9.1 are fetched with checksum verification;
- a `test` build stage refuses a dynamically linked kubelet or kubeadm and checks that both report the expected version.

AuroraBoot v0.27.1 turns the image into a raw EFI disk, which is converted to a compressed qcow2 and put into a container disk for CDI (`images/kairos-worker-disk`). Today only that disk is pushed; the OS image itself is built locally and not published. The `.tag` files pin the published disks by digest: `ghcr.io/cozystack/cozystack/kairos-worker-v1.31` to `kairos-worker-v1.35`. The v1.35 disk is one gzip layer of about 381 MB.

The pool joins through `KairosConfig` with `distribution: kubeadm`, from [kairos-io/cluster-api-provider-kairos](https://github.com/kairos-io/cluster-api-provider-kairos). The provider mints the join token, cross-checks the CA in the `<cluster>-kubeconfig` Secret against the `<cluster>-ca` Secret, writes the kubelet `providerID` and refuses a join when kubeadm's version differs from the Machine's by even a patch.

### The problem

A hoster sometimes needs a kernel module or a driver in the worker that the platform does not ship: a vendor guest driver, an out-of-tree storage or network module, or a module built with options the stock one lacks. The first concrete case is the NVIDIA vGPU guest driver from #101, which has no Talos extension.

On Talos the supported way to add a module is a system extension, selected through an Image Factory schematic (`talos.schematicID`). Extensions that Sidero Labs publishes are available that way. A module outside that catalog has to be built as an extension against the exact kernel of a Talos release, and the hoster then runs its own image build or image factory to get an installer and a disk that carry it. Since Talos 1.12 the kernel command line includes `module.sig_enforce=1` (`pkg/machinery/kernel/kernel.go` and the `minTalosVersionDisableModSigVerify` quirk in `pkg/machinery/imager/quirks/quirks.go` in siderolabs/talos), and the kernel signs its modules with a key from its own build (`CONFIG_MODULE_SIG_ALL` in the siderolabs/pkgs kernel config), so the extension also has to come out of that build or the image has to drop the argument. Every Talos release repeats this work.

A Kairos worker image is an ordinary OCI image. With the platform's OS image published (see [The worker image](#2-the-worker-image)), a hoster adds a module or a driver in a `FROM` layer on top of it, builds a disk with the same recipe, and points a class at it. The hoster rebuilds when the platform image moves, but uses only a container build to do it.

A second, smaller reason: a kubeadm join is the reference worker join for Cluster API. A Kairos pool does not need `talos-csr-signer` or a Talos machine config, so a fault in either does not reach it.

## Goals

- A pool chooses its worker OS by naming a `NodeGroupClass`; Talos is the default.
- A pool without a class renders byte for byte as before; existing pools do not roll on the upgrade that adds this.
- Kairos pools have the same lifecycle contract as Talos pools: MachineHealthCheck, autoscaling including scale from zero, labels and taints, rollout on every change.
- A hoster can supply its own worker image, including from a registry that needs credentials, without copying a Secret into tenant namespaces.
- A hoster image does not break when a Cozystack upgrade moves the Kubernetes patch the platform pins.
- At general availability Cozystack carries no downstream patches to the Kairos provider.

### Non-goals

- **The OS of management-cluster hosts.** Kairos as a host OS for the management cluster is a separate decision with a different blast radius, and nothing here depends on it. This proposal covers tenant worker VMs only.
- **GPUs.** #99, #100 and #101 cover them.
- **Kairos control planes.** Tenant control planes stay on Kamaji. The provider's `KairosControlPlane` (k0s, k3s) is not used. A kubeadm control plane on Kairos is not supported by the provider today; upstream treats it as a separate design ([kairos-io/kairos#4920](https://github.com/kairos-io/kairos/issues/4920), closed 2026-10-22).
- **Substrates other than KubeVirt.** #4911 refuses `os: kairos` on Proxmox. Proxmox Kairos pools would be a separate change.
- **In-place OS upgrades.** Workers are replaced, never upgraded with `kairos-agent upgrade`.
- **Moving Talos settings into the class.** The `talos.*` and `osImage` pool fields stay where they are. Whether they move into Talos classes later is a separate question.

## Design

### 1. `NodeGroupClass`

A cluster-scoped resource in `kubernetes.cozystack.io/v1alpha1`. Only the hoster creates classes: the tenant chart (`packages/apps/tenant`) grants tenant roles through RoleBindings only, so a tenant has no way to create a cluster-scoped object.

```yaml
apiVersion: kubernetes.cozystack.io/v1alpha1
kind: NodeGroupClass
metadata:
  name: kairos-example-v1.35.6-r1
spec:
  os: kairos                # talos | kairos
  image:                    # optional; omitted = the platform image for the pool's version
    kubernetesVersion: v1.35.6
    # exactly one of:
    reference: registry.example.com/workers/kairos-worker@sha256:0123...
    # catalogEntry: example-kairos-v1.35.6   # an entry in the worker image catalog
```

- `spec` is immutable as a whole, enforced by a CEL rule (`self == oldSelf`). A new image or version is a new class with a new name, so no edit can roll every pool that uses a class at once.
- `image` is allowed only with `os: kairos`, enforced by CEL. Talos image settings stay on the pool (see [Non-goals](#non-goals)), so a Talos class carries nothing but `os`.
- `image.kubernetesVersion` is required when `image` is set, and so is exactly one of `reference` (a publicly pullable disk) or `catalogEntry` (a disk the worker image catalog imports, see [How it reaches a worker](#2-the-worker-image)), enforced by CEL. The Machine `version` of a pool on that class comes from this field, not from the chart's `files/versions.yaml`.
- The platform ships two classes without `image`: `talos` and `kairos`. They mean "the platform's image for this pool's Kubernetes version", so their content does not change between releases while what they resolve to follows the platform, as pools do today.

A pool gets one new field, `nodeGroupClassName`, defaulting to `talos`. The names `talos` and `kairos` are built in: the chart resolves them without reading any object, so a Talos pool does not depend on the class existing, and an upgrade that renders a pool before the platform has created the class objects, or a deleted `talos` class, changes nothing for it. The two objects exist so that the classes can be listed, and an admission rule refuses creating or replacing a class named `talos` or `kairos` with any other content, so a hoster cannot create a `talos` or `kairos` object whose content the chart would silently ignore.

For any other name the chart reads the class with `lookup` at render time, as it already reads the parent `KamajiControlPlane`. A missing class fails the render with a message naming it. A class deleted while pools use it fails their next render. The objects already in the cluster stay, so existing Machines keep running and the autoscaler can still scale the pool from its current templates; a change to the pool or a chart upgrade fails until the class is back. This is stricter than a deleted golden in the worker image catalog today, which fails the render only of a pool that has no cloned disk yet. Refusing that deletion is an open question.

#### Extending the class

The class leaves room for fields that #99 and #101 will define. New optional fields under `spec` do not change the ones above, and the whole-spec immutability rule covers them.

### 2. The worker image

**Who builds it.** Cozystack builds the platform images in its own CI, from `packages/apps/kubernetes-nodes/images/kairos-worker`, one per Kubernetes minor listed in that chart's `files/versions.yaml`. This proposal adds publishing the OS image of each minor next to its disk, which #4911 does not do yet. A hoster image is built by the hoster `FROM` that OS image, so kubelet, kubeadm, containerd and Hadron match the platform image of that minor, and turned into a disk with the same AuroraBoot recipe.

**Versioning.** The platform image of a minor carries the patch `files/versions.yaml` pins for it (v1.35.6 for v1.35 on main). The disk is tagged with the package version and pinned by digest in `images/kairos-worker-<minor>.tag`. The published OS image of the same minor gets its own `.tag` file next to it, so a hoster can pin `FROM` by digest and the release tooling that enumerates `.tag` files (`hack/lib/image-refs.sh`) retags and mirrors it with the rest. A Hadron, containerd or runc bump rebuilds every minor. A Kubernetes patch bump rebuilds the minor it touches.

**Where it is stored.** The disks go to `ghcr.io/cozystack/cozystack/kairos-worker-v1.31` to `kairos-worker-v1.35`, and the OS images to a sibling package per minor. The name is not final; it must differ from `kairos-worker-os-<minor>`, which #4911 already uses for its build cache. The default path imports these disks into each worker's DataVolume without credentials, so the packages must be publicly pullable, like the other platform images. A disk that needs credentials goes through the worker image catalog, described below. Git carries amd64 digests; a release adds arm64, as #4911 does today.

**How it reaches a worker.**

- A class with `image.reference`, and the platform `kairos` class, keep the path in #4911: each worker's DataVolume imports the container disk from the registry, without credentials.
- A class with `image.catalogEntry` clones a golden DataVolume that `packages/system/kubernetes-worker-image` imports once into `cozy-public`, as `osImage.builtin` does for Talos. The catalog entry, in that package's values next to the Talos entries, holds the disk reference and the name of a pull Secret in `cozy-public`; the class holds only the entry name, so the credential is named in one place. CDI requires a registry `secretRef` to be in the DataVolume's own namespace, so a per-worker import would need a copy of the hoster's credential in every tenant namespace; the golden avoids that.
- The golden's lifecycle is the catalog's, as for Talos. Adding an entry is a values change, so the package's next Helm upgrade creates the golden. Goldens carry `helm.sh/resource-policy: keep`, so removing an entry stops managing its golden rather than deleting it, and the hoster deletes it by hand after checking that no pool declaration still names it (the procedure in that package's `values.yaml`). The path inherits the constraints of `builtin`: the package must be enabled (`bundles.enabledPackages`), the pool's StorageClass must match the golden's and be reachable from any node, in practice a replicated class, and `diskSize` must not be smaller than the golden.

**The patch pin.** For the platform `kairos` class the Machine version and the image both follow `files/versions.yaml`, and a Cozystack upgrade that moves the patch rolls those pools, exactly as it does for Talos pools. For a hoster class the Machine version, the image and the `kubernetesVersion` the `KairosConfigTemplate` passes to kubeadm all come from the class. #4911 takes the Machine version and that `kubernetesVersion` from `files/versions.yaml`, and the latter is part of the hash that names the template, so a patch bump there would roll pools on a hoster class. The chart checks only that the class's minor equals the pool's `version` and that the pool is not ahead of its control plane. A Cozystack upgrade therefore does not break the render of a pool on a hoster class, and the hoster can publish the next class before or after the upgrade.

### 3. Bootstrap

A pool on a Kairos class renders a `MachineDeployment`, a `KubevirtMachineTemplate` with EFI firmware (the disk has no BIOS boot partition), a `MachineHealthCheck` and a `WorkloadMonitor`, and its `kairos-reconcile` Job produces a `KairosConfigTemplate` with `distribution: kubeadm`.

A pool on a Kairos class refuses the pool fields that only Talos uses, with an error that names the field: a non-empty `osImage`, `kernelModules` or `gpus` fails the render, as #4911 does today for `os: kairos`. `osImage` selects a Talos disk and `kernelModules` goes into the Talos machine config, while on Kairos a module comes from the image; `gpus` stays refused until #101 defines GPUs on Kairos pools. The three default to empty (`osImage: {}`, `gpus: []`, `kernelModules` unset in the chart's `values.yaml`), so a defaulted field is never refused, and an explicit empty value such as `kernelModules: []` is accepted because it changes nothing on Kairos. The `talos.*` fields cannot be checked that way. `talos.version`, `talos.schematicID` and the factory URLs have non-empty defaults that Helm merges into every render, and the Cozystack API fills schema defaults into every pool it returns (`applySpecDefaults`, called from `pkg/registry/apps/application/rest.go`), so a spec written back through the API stores them, with the values of whichever chart it was read under. A tenant setting there cannot be told apart from a default, so a Kairos pool ignores `talos.*` instead of refusing it, and no `talos.*` value changes its render. That includes the Image Factory CA ConfigMap (`templates/image-factory-ca.yaml`), which #4911 still evaluates from `talos.*` on a Kairos pool (it resolves the Talos image source, and renders the ConfigMap when `talos.imageFactoryCA` is set), and which the chart renders for Talos pools only.

The `KairosConfigTemplate` needs the tenant apiserver Service ClusterIP and the management CoreDNS ClusterIP, which exist only after the control plane is running. As in the Talos path, a reconcile Job waits for them and applies the template. Tenant values reach it only as data in a mounted ConfigMap, so no tenant value passes through a shell. `envsubst` substitutes exactly four variables: `SVC_IP` and `COREDNS_IP`, which the Job reads from the two Services, and `RELEASE` and `NS`, the parent cluster's release name and the pool's namespace, which the chart passes to the Job as quoted environment values. The release name is built from the pool's `cluster` field, the name of the parent `Kubernetes` object; no other tenant value is substituted. The Job can get, patch and update only its own pool's template; `create` stays namespace-wide, because RBAC cannot narrow it by name.

The worker joins `https://<release>.<namespace>.svc:6443`. kubeadm also takes the server name from the `cluster-info` ConfigMap Kamaji publishes, so the name must resolve during the first join; the template writes it to `/etc/hosts` with the Service ClusterIP, the counterpart of Talos `extraHostEntries`. #4911 makes the tenant `KamajiControlPlane` publish the tenant CoreDNS address (`dnsServiceIPs`), so kubeadm-joined kubelets get `clusterDNS`.

Kairos applies its bootstrap data on the first boot and keeps that copy. Any change to the template therefore takes effect only on new machines, and the chart names the template by a hash of its chart inputs so that every change to them rolls the pool. The runtime addresses the Job fills in are left out of that hash, as in the Talos template. The provider attempts the join once per machine. A failed join leaves a Machine without a Node, which is the MachineHealthCheck's case.

### 4. Upgrade

Workers are immutable. A new Kubernetes patch or minor, a new Hadron, or a new hoster class means a new disk and a new template, and the `MachineDeployment` rolls, as a Talos pool does when its image or machine config changes. Nothing on the node is upgraded in place.

Moving a pool on a hoster class to a new patch means pointing `nodeGroupClassName` at the class that carries it. Moving to a new minor means changing the pool's `version` and its class in the same edit, because the chart refuses a class whose minor differs from `version`, and the control plane has to be on that minor first. Switching between two classes of the same OS is allowed and rolls the pool.

### 5. MachineHealthCheck and remediation

The pool's `MachineHealthCheck` is the same object for both OSes, with the same `nodeStartupTimeout` (default 20m) and `maxUnhealthy` (default 50%). Under the default, a pool of one machine is never remediated, because half of one rounds down to zero (the field description in `values.yaml` says so); this applies to Talos pools as well.

No test in cozystack/cozystack pins remediation on a Kairos pool. In a test environment, a single-machine Kairos pool whose machine never became a node was marked unhealthy with `NodeStartupTimeout` and not remediated, and the MachineHealthCheck status gave the reason as the one above: one machine, one unhealthy, `maxUnhealthy: 50%`. The test plan below uses a pool of at least two machines, or an explicit `maxUnhealthy`.

### 6. Autoscaling

The tenant cluster's autoscaler does not depend on the OS: it scales each pool's `MachineDeployment` within the limits in that pool's annotations. For scale from zero, cozystack/cozystack#4927 adds the pool's labels and taints to the capacity annotations already on the `MachineDeployment`, and #4949 adds the role labels and the GPU count. They are the same for both OSes because the VM shape comes from the `KubevirtMachineTemplate`. The autoscaler reads Cluster API through v1beta1 (`CAPI_VERSION=v1beta1`, set in #4902).

Scale from zero has not been tried on a Kairos pool. The `maxNodeProvisionTime` default (30m) and `nodeStartupTimeout` were sized for Talos boots; a Kairos first boot adds a reset into the installed system and a reboot. How much time that adds has not been measured.

### 7. Labels and taints

As in #103: `labels` and `taints` on the pool, rendered as kubeadm `nodeRegistration` on Kairos and as `machine.nodeLabels` and `registerWithTaints` on Talos, with labels in the Cluster API domains on the `MachineDeployment` template. Nothing Kairos-specific is added.

### 8. Upstream dependencies and what Cozystack carries

The provider changes #4911 relies on are merged and released, checked on 2026-10-23:

| Change | Upstream | Merged | First release |
| --- | --- | --- | --- |
| `distribution: kubeadm` for worker joins | [kairos-io/cluster-api-provider-kairos#141](https://github.com/kairos-io/cluster-api-provider-kairos/pull/141) | 2026-10-13 | v0.2.0 |
| kubeadm patches directory named in `JoinConfiguration` (kubeadm rejects `--patches` together with `--config`) | [#148](https://github.com/kairos-io/cluster-api-provider-kairos/pull/148) | 2026-10-17 | v0.2.0 |
| post-bootstrap units no longer block the userdata stage (kubeadm, k3s); the DNS step moves before the join | [#149](https://github.com/kairos-io/cluster-api-provider-kairos/pull/149) | 2026-10-22 | v0.2.1 |
| `--no-block` for the k0s post-bootstrap units, and when the CAPK lb-sans update runs | [#155](https://github.com/kairos-io/cluster-api-provider-kairos/pull/155) | open | none |

What #4911 carries today, and when it goes:

| Carried in #4911 | Upstream equivalent | Removed when |
| --- | --- | --- |
| provider built from commit `b19dd35` (the #141 merge) instead of a tag | v0.2.0 and later tags | the build moves to v0.2.1, before #4911 merges |
| `patches/0001-kubeadm-patches-directory-in-join-configuration.diff` | #148, in v0.2.0 | same bump |
| `patches/0002-queue-post-bootstrap-units-and-early-dns.diff` | #149 in v0.2.1; its k0s part matches #155, which kubeadm workers do not use | same bump |

After that bump no patch is carried. The `patches/` mechanism stays in the image build, so a future fix can ship before an upstream release. The rule for anything placed there: it has an open upstream pull request, it is removed in the first Cozystack release that picks up an upstream release containing it, and the carry table above lists it until then. A patch added after Kairos leaves experimental puts it back into experimental until the patch is gone.

Other upstream pins the image depends on: Hadron v0.5.3 (v0.6.2 was released on 2026-10-22), kairos-init v4.3.0, AuroraBoot v0.27.1. The provider requires Cluster API core v1.13.4 or later (its README), which is why #4902 comes first.

If upstream stops accepting changes or stops releasing, Cozystack can keep building the Apache-2.0 provider from a pinned upstream source with patches, as it does for the Kamaji control-plane provider image (`packages/system/capi-providers-cpprovider`). The Kairos option stays experimental for as long as that is the case. Because the Talos and Kairos bootstrap providers are independent, removing Kairos support means deleting the Kairos pools and the provider; Talos pools are not touched.

Known upstream gaps this proposal does not rely on. [kairos-io/kairos#5233](https://github.com/kairos-io/kairos/issues/5233) (open) asks for an opt-in `reset --detach-mounts` for unattended callers, the cleanup after a kubeadm reset; workers are replaced, never reset that way. The ConfigDrive hostname gap in yip, mentioned when #4920 was closed, comes with an invitation in that closing comment to reopen if it still bites outside the provider path. Validation so far covers one configuration: Hadron v0.5.3, KubeVirt workers, a Kamaji control plane, Kubernetes v1.35.6 and Cluster API v1.13.7.

### 9. What a Kairos pool costs

- A second image pipeline in Cozystack CI: five minors, two architectures, a kubelet built from source, and a rebuild for every Hadron and Kubernetes patch.
- Hadron v0.5.3 ships kernel 7.2. Tenant Cilium 1.19.5 failed on that kernel, and the upstream issue reports the same on 1.19.4, 1.19.7 and 1.20.1 ([cilium/cilium#48016](https://github.com/cilium/cilium/issues/48016), closed). Main ships 1.20.2; in my test environment a Hadron v0.5.3 worker ran with tenant Cilium 1.20.2. A Hadron bump therefore needs a check against the tenant CNI version.
- A young provider (v0.2.x) and a young distribution, with a release cadence Cozystack does not control.
- An SSH login: the worker has a `kairos` user with no password login and an optional `sshPublicKey`. Talos workers have no SSH and no shell.
- A disk import per worker for classes without a golden, about 381 MB per worker for the v1.35 platform disk.

## User-facing changes

- Hoster: a new `NodeGroupClass` resource; the platform installs `talos` and `kairos`. Documentation for building an image `FROM` the platform image and publishing a class.
- Tenant: a `nodeGroupClassName` field on `KubernetesNodes`, default `talos`, and a `kairos.sshPublicKey` setting for break-glass login. The dashboard needs to list available classes; how a tenant reads cluster-scoped classes is an open question.
- Nothing changes for pools that do not set the field.

## Upgrade and rollback compatibility

Existing pools see no change: the upgrade that adds this leaves their render as it is, and the helm unit tests pin that against golden snapshots.

To move a Talos pool to Kairos, create a second pool on a Kairos class, move workloads by cordoning and draining the Talos nodes, scale the Talos pool to zero and delete it. Changing `nodeGroupClassName` between a Talos class and a Kairos class on a running pool is refused in the first release, in either direction, by the same guard #4911 has for `os`: it compares the bootstrap template kind of the live `MachineDeployment` with the one the render asks for. A Cluster API rolling update between bootstrap providers is what `kubernetes-nodes-split` Phase 1 used to move from kubeadm to Talos, so allowing the switch in place is possible later; see [Open questions](#open-questions).

Rolling back a moved pool means scaling the Talos pool back up and deleting the Kairos pool. Reverting Cozystack to a release without Kairos support needs the Kairos pools gone first, because the old chart does not know the class field; the provider and the classes can stay installed without effect.

## Security

- Classes are hoster input. A class decides what OS image every worker of a pool boots, so only the hoster creates them; tenant roles come from RoleBindings and do not reach cluster-scoped kinds.
- Registry credentials for catalog disks live in `cozy-public` next to the goldens and are never copied into a tenant namespace. No binding there lets a tenant read Secrets: tenant groups get `get` and `list` on Flux `helmrepositories` and `helmcharts` (the Role in `packages/system/cozystack-basics/templates/dashboard-role.yaml`, bound in `packages/apps/tenant/templates/tenant.yaml`), and every service account may create `datavolumes/source` to clone (`packages/system/kubevirt-cdi/templates/cdi-cr.yaml`). That second grant means any tenant service account can clone a catalog disk into its own namespace. The pull Secret protects the registry, not the disk: a catalog disk must not carry anything a tenant may not read.
- No Talos secrets on Kairos pools: no Talos token, no machine CA. The provider mints the join token and stops before minting a token when the CA in the `<cluster>-kubeconfig` Secret does not match the `<cluster>-ca` Secret.
- Tenant input to the bootstrap template is YAML-quoted data in a ConfigMap, never script text. The reconcile Job can patch only its own pool's template.
- SSH. Password login is disabled (`userPassword: "*"`). With an empty `sshPublicKey` there is no login at all. A key is a break-glass path for the tenant, not a management channel.

## Failure and edge cases

- Pool names a class that does not exist → the render fails with the class name; no objects change.
- A pool on a class whose catalog entry does not exist → the render fails and names the entry; it clears once the entry lands, because an app HelmRelease retries with no remediation cap (the same behaviour that package's `values.yaml` describes for Talos entries). A golden that is still importing is not an error: CDI holds the worker disk until the source is populated.
- A hoster class deleted while in use → existing Machines keep running and scale-ups use the current templates; the next render fails until the class is back.
- Platform image missing for the pool's minor → the render fails and names the minor.
- Image kubeadm version differs from the Machine's → the provider refuses the join; the Machine never gets a Node; MachineHealthCheck applies.
- Registry refuses the pull → the DataVolume stays in import, the Machine stays in Provisioning, and the error is on the DataVolume. With a catalog entry, one failed import blocks every pool that uses it, and the error is visible on the golden.
- Cozystack upgrade moves the pinned patch → pools on the `kairos` class roll; pools on a hoster class do not change.
- Tenant apiserver Service is re-addressed → the pool does not roll, because the ClusterIP is kept out of the template's hash. The template is refreshed only when the reconcile Job runs again; see [Open questions](#open-questions).
- Join fails on first boot → no retry on the node; MachineHealthCheck replaces the machine after `nodeStartupTimeout`, within `maxUnhealthy`.
- A Talos pool switched to a Kairos class, or a Kairos pool to a Talos class → the render fails and says to create a new pool.
- A pool on a Kairos class sets a non-empty `osImage`, `kernelModules` or `gpus` → the render fails and names the field. `talos.*` values are ignored.

## Testing

- Unit (helm unittest). Talos render unchanged with no class; Kairos render for platform and hoster classes; Machine version and the template's `kubernetesVersion` taken from the class; a patch bump in `files/versions.yaml` leaving every object of a pool on a hoster class unchanged, the `KairosConfigTemplate` name included; class minor checks; guards for a missing class, a missing image and an OS switch in both directions; a Kairos pool refused for a non-empty `osImage`, `kernelModules` or `gpus`, and its render unchanged by a non-default `talos.*` value; Kairos catalog entries rendered as goldens with their pull Secret, and a class with `catalogEntry` cloning from the golden.
- Image. The `test` build stage of every minor: static kubelet and kubeadm, exact versions, units enabled, kubelet unit flags present.
- Build gating (bats). Image tags match `files/versions.yaml`; the published disk and OS image `.tag` files exist for every minor.
- End to end. A new case in the Kubernetes e2e suite: create a pool on the `kairos` class, wait for the Node to become Ready, check `providerID` and the cluster-namespace annotation, run a pod on it.
- Remediation. In a Kairos pool of at least two machines, or with an explicit `maxUnhealthy` that allows it, stop a worker VM and expect a replacement; repeat with a Machine whose disk import fails.
- Autoscaling. Scale a Kairos pool from zero to one and back, and record the time from Machine creation to Node Ready against a Talos pool on the same cluster.
- Upgrade. Upgrade from a release without Kairos support and check that no existing Machine is recreated.
- Private registry. A class with a catalog entry whose disk needs a pull Secret: one golden import, workers clone it, no Secret appears in the tenant namespace.

## Rollout

1. Cluster API v1.13.7 (cozystack/cozystack#4902).
2. `NodeGroupClass` with the `talos` and `kairos` classes, the provider built from v0.2.1 with no patches, and the platform disks and OS images published publicly. #4911 is reworked onto the class before it merges, so the `os` and `kairos.image` pool fields never ship and no migration is needed for them. Kairos is marked experimental in the docs and the dashboard.
3. Labels and taints (#4927) and scale-from-zero annotations (#4949), which apply to both OSes.
4. Kairos entries in the worker image catalog and classes with `catalogEntry`.
5. Experimental is removed when no downstream provider patch remains and the remediation, autoscaling and upgrade tests above pass in CI across two Cozystack releases.
6. #101 resumes on top of the class.

## Open questions

1. Single-machine pools and `maxUnhealthy`. The default `50%` never remediates a pool of one, on Talos and Kairos alike. Should the chart pick a threshold that remediates small pools, or keep the default and document it?
2. Class deletion. Should deleting a class that pools reference be refused, and by what (a finalizer set by a controller, or an admission policy)?
3. Tenant view of classes. Tenants need to see which classes exist to choose one. Read access to a cluster-scoped kind through a tenant RoleBinding does not work, so this needs either a ClusterRole for listing or an aggregated list exposed through the Cozystack API. There is a close precedent for the first: `packages/system/dashboard/templates/rbac.yaml` binds read access to `storageclasses` and `backupclasses` to every authenticated user, the same shape a class list needs.
4. In-place OS switch. Allow changing a pool between a Talos class and a Kairos class as a rolling update, or keep requiring a new pool?
5. Hadron cadence. Who watches Hadron and kernel CVEs, and how fast does a Hadron bump have to land in a Cozystack release?
6. Re-addressed apiserver Service. The reconcile Job writes the current ClusterIP into the template whenever it runs: on Talos it replaces the `TalosConfigTemplate` when CABPT's webhook refuses the changed spec (`packages/apps/kubernetes-nodes/templates/talos-reconcile-job.yaml`), and on Kairos it patches the template with a server-side apply, since the provider has no webhook for `KairosConfigTemplate` (`config/webhook/manifests.yaml` in v0.2.1). A re-address alone does not run the Job, though: its name is a hash of its own spec, and after `ttlSecondsAfterFinished: 600` it is recreated only by the next Helm upgrade of the pool. Should a re-address trigger the Job, for both OSes?
7. Where goldens from credentialed catalog entries live. In `cozy-public` every service account may clone them, so a licensed disk there is readable by every tenant. A candidate is a separate namespace with a clone grant narrowed to the identities that need it. Which identity CDI checks for a DataVolume that CAPK creates is not confirmed, and the answer decides whether that narrowing works.
8. Talos settings in the class. Should the `talos.*` and `osImage` pool fields later move into Talos classes, so both OSes are configured the same way?

## Alternatives considered

**A Talos system extension per module.** The hoster builds an extension for each module and runs an image factory or its own image build for each Talos release. It keeps one worker OS, but every hoster carries a Talos build pipeline instead of one `FROM` layer, and the work repeats on every Talos release. For modules Sidero Labs already publishes, Talos with a schematic stays the better choice, and nothing here changes that.

**A general-purpose distribution image (Ubuntu) with kubeadm.** This is the path `kubernetes-nodes-split` removed. It brings a mutable OS and package management to every worker. Kairos keeps the image immutable and the update model "replace the machine", which matches how the pool already works.

**A Kairos flavor based on Ubuntu instead of Hadron.** It would accept the glibc kubelet and glibc driver userspace as is, at the cost of a second base distribution to track. Hadron is the base of the only configuration validated so far; a hoster can still build a class from another flavor, provided it ships kubelet and kubeadm at the class's version.

**Drivers as containers in the tenant cluster.** Every tenant cluster, where the tenant is cluster-admin, would pull the hoster's driver image and would need the hoster's registry credentials. With a node image only the management cluster pulls it.

**OS and image fields on the pool (as in the current #4911).** Each pool would carry the image reference, and a hoster's registry Secret would have to be copied into every tenant namespace. A change to a shared default would roll the pools of every tenant at once. The class keeps hoster settings in one immutable object.

**Ship #4911 as is and migrate to the class later.** It would need a migration for an API that no release had time to use. #4911 is not merged, so reworking it is cheaper.
