# GPU virtual machines with hoster-supplied guest images

- **Title:** `GPU virtual machines: vGPU and passthrough with hoster-supplied guest images`
- **Author(s):** `@lexfrei`

## Overview

A hoster that sells GPUs wants a tenant to order a virtual machine with either a vGPU profile or a whole GPU in passthrough, and to boot it from an image that already has a working guest driver. For vGPU, the guest half is the NVIDIA vGPU guest driver, and its license client runs inside the guest. That driver is licensed per customer and may not be published, for example in a public image or repository, so Cozystack cannot build, ship or test such an image. Passthrough used for compute runs the NVIDIA data center driver, which has no license check, but the guest still needs that driver.

This proposal keeps the image on the hoster's side and makes the platform able to carry it. The hoster adds its own images to the golden image collection, marks them as GPU guest images, and can fetch them from a private source. The image picker in the dashboard shows that marking. A tenant creates a disk from such an image, orders GPUs as `GPUClaim` objects, and attaches them to the VM by name.

NVIDIA's vGPU stack changes quickly. Within the last few releases the driver branches, the way vGPUs are exposed (mediated devices or SR-IOV virtual functions), the KubeVirt device plugins and the licensing rules all changed. This proposal describes that stack as it was in October 2026. Expect it to be updated as NVIDIA and KubeVirt move, with a decision record under it for each change of course, and check the versions named here against your own before relying on them.

## Scope and related proposals

- [`gpudevice`](../gpudevice/README.md) adds the GPU catalog, `GPUClaim` objects that a tenant orders within a quota, usage records, and the host layer under them. This proposal builds on it and is implemented after it.
- [`kairos-vgpu-workers`](../kairos-vgpu-workers/README.md) covers GPUs in Kairos worker pools of tenant Kubernetes clusters, vGPU and passthrough.
- [Node fencing](../node-fencing/README.md) restarts VMs of a dead node automatically when its optional package is enabled and the node's BMC answers. A GPU VM with `runStrategy: Always` or `RerunOnFailure` restarts that way once another node has room for its claims. Design §5 gives the details, the other run strategies, and the exception for a claim of a `Stay` class on a cordoned node.

These proposals are written in parallel, so the links to them work only once they are merged.

## Decisions

## Context

### What exists

Golden images live in `packages/system/vm-default-images`. Its `images` list renders one CDI `DataVolume` per entry in `cozy-public`, named `vm-default-images-<name>`, with the OS, architecture and description stored as `vm-default-images.cozystack.io/*` annotations. The only source is an HTTP URL, with no credentials and no custom CA. The package is optional, and its values can be overridden through the `cozystack.vm-default-images` Package. The documentation also describes `hack/cdi_golden_image_create.sh`, which creates such a `DataVolume` by hand, and the picker lists it like any other golden image.

A tenant creates a disk with `vm-disk`, either from a golden image by name, which `vm-disk` clones from `cozy-public`, or from their own image by upload, HTTP URL or a clone of another disk. The dashboard fills the golden image picker from the `image` option source (`pkg/registry/core/option/providers.go`). That provider returns only names: it strips the PVC prefix and drops images whose import failed. The picker in `SourceField.tsx` reads only the value.

A VM takes its GPUs in the `gpus` field of `vm-instance`. Today each entry is `{name: <resource name>}`, the name of a device-plugin resource in the KubeVirt `permittedHostDevices`. `gpudevice` adds `{claim: <name>}` entries that reference a tenant's `GPUClaim`.

### The problem

A tenant who orders a GPU VM expects a guest that works on first boot, and the hoster is the one with the driver entitlement and the license server. Today the hoster can offer such an image only by hand:

- the package's `images` list cannot authenticate, and a public URL is not an option for an image whose driver may not be published;
- adding images to that list means restating the whole default list, because Helm replaces lists instead of merging them;
- a hand-made `DataVolume` lives outside the platform values, the package health check does not cover it, and nothing reconciles it;
- the image picker shows bare names, so a tenant cannot tell a GPU guest image from a plain one.

## Goals

- A hoster adds GPU guest images to the golden image collection through Package values, without restating the default list and without a public URL.
- A tenant sees in the image picker which images are GPU guest images, for which mode, and for which driver branch.
- A tenant gets a VM with a vGPU profile or with whole GPUs from `vm-disk`, `vm-instance` and `GPUClaim`.
- Golden images stay a shared catalog, and tenant images stay `vm-disk` sources.
- The document states what happens to a GPU VM on node maintenance and on node failure.
- This proposal adds no NVIDIA driver or guest image to Cozystack's tree, CI or registries.

### Non-goals

- Building, shipping or testing guest images. The image and its contents are the hoster's.
- Licensing on the tenant's behalf. The guest licenses itself, and the hoster decides how the token gets in.
- The GPU catalog, quotas, usage records and the host layer. These belong to `gpudevice`.
- Enforcing that a GPU VM boots from a GPU guest image (Design §3).
- Making GPU VMs survive a node failure without a restart.

## Design

### 1. Golden image entries for GPU guest images

Each golden image entry gets four optional fields, and the hoster gets a second list for its own entries:

```yaml
extraImages:
  - name: hoster-ubuntu-24.04-vgpu-r595
    url: https://images.hoster.example/ubuntu-24.04-vgpu-r595.qcow2
    storage: 30Gi
    os:
      family: Linux
      name: Ubuntu
      version: "24.04"
    architecture: amd64
    description: "Ubuntu 24.04 with the NVIDIA vGPU guest driver"
    gpu:
      vendor: nvidia
      mode: vgpu
      driverBranch: "R595"
    secretRef: images-hoster-example
    certConfigMap: images-hoster-example-ca
```

- The `gpu` field marks the image. `mode` is `vgpu` or `passthrough`, because a guest image is built for one of the two device types. `vendor` is a free string, so later vendors need no schema change. When `gpu` is set, `vendor` and `mode` are required and the chart fails on any other `mode`. `driverBranch` is optional free text shown to tenants. The chart writes the three as `vm-default-images.cozystack.io/gpu-vendor`, `gpu-mode` and `gpu-driver-branch` annotations.
- The `secretRef`, `secretExtraHeaders` and `certConfigMap` fields go to the CDI `http` source unchanged. `secretRef` names one Secret with the basic auth user name in the key `accessKeyId` and the password in `secretKey`, the keys CDI v1.66.1 reads for an HTTP source; a Secret without them stops the importer pod from starting. `secretExtraHeaders` is a list of Secret names, each value a whole header line such as `Authorization: Bearer <token>`, with no trailing newline. Basic auth through `secretRef` and an `Authorization` header are alternatives: when both are set, CDI v1.66.1 sends a different one on different requests, so an entry uses one of them. The hoster keeps the CA under the key `tls.crt` of the `certConfigMap`. The transfer trusts every key of it, but CDI v1.66.1 passes only `<certDir>/tls.crt` to the nbdkit curl plugin it uses to probe the image ([`pkg/image/nbdkit.go`](https://github.com/kubevirt/containerized-data-importer/blob/v1.66.1/pkg/image/nbdkit.go)). CDI requires the Secrets and the ConfigMap in the namespace of the `DataVolume`, `cozy-public`, where the hoster creates them. Tenants have no read access to Secrets there.
- The `extraImages` list has the same entry type as `images`. The chart renders both and fails if a name appears twice, in both lists or within one. A hoster adds images without copying the defaults, and a later change of the default list still reaches them.

A tenant can still upload an image with their own driver and boot a GPU VM from it, unmarked.

### 2. Image picker

The `image` option provider reads the marking from the `DataVolume` of each golden image, which it already lists to filter out failed imports. For an image with `gpu` it sets `Label` to the name followed by the marking, for example `hoster-ubuntu-24.04-vgpu-r595 (nvidia vgpu, driver R595)`, and leaves out an empty `driverBranch`. The picker in `SourceField.tsx` renders `Label` when it is set and the value otherwise, so an image without `gpu` shows as before. A tooltip is not enough, because the dashboard renders `Description` only as the `title` of an `<option>`, which a native select does not show in every browser.

When the provider cannot list `DataVolume`s, it already shows every image unfiltered, and then shows no marking. The PVC is not a fallback, because CDI copies the annotations to it only once, at creation, so it can carry a marking the hoster has since changed.

### 3. No enforcement

The chart and the dashboard do not refuse a GPU VM that boots from an image without the marking. A disk may come from an upload, an HTTP URL or a clone, which carry no marking, and a tenant with their own driver would lose a configuration that works. A wrong pairing harms only that VM: the device is there and unusable without a driver. The marking is a hint to the tenant.

### 4. GPUs for the VM

A tenant orders GPUs before the VM, as `GPUClaim` objects of a `GPUClass`. The class is the offer of a model, a mode and, for vGPU, a profile, and the claim holds a number of GPUs of that class within the tenant's quota. The VM references its claims in `gpus`:

```yaml
gpus:
  - claim: train-0
```

The Attaching section of `gpudevice` resolves the reference when the VM starts and pins the VM to the node of its claims. One VM takes vGPUs of only one class, and no more GPUs of a class than its `maxCount` (Object model section of `gpudevice`). Quotas and usage records follow the claim, not the VM (Quotas and Usage accounting, catalog and licensing sections). A vGPU VM normally takes one claim of `count: 1`, and a passthrough VM one claim with the number of GPUs it needs. A tenant who wants to hold GPUs only while the VM runs uses a `claimTemplate` entry instead, which names a class and a count and gets its claim when the VM starts.

Existing `{name: <resource name>}` entries and `gn1.*` instance types keep their current behaviour and move to `claim` entries as the Migration of existing `gpus` section of `gpudevice` describes. A `vgpu` cluster that adopts the node layout of the Host layer section renames its vGPU resources. In the same maintenance window the hoster moves raw entries and raw quota keys to the new names, or the VMs move to `claim` entries first, and a VM left on an old name stays Pending. Guest images from this proposal need no change for that.

### 5. Node maintenance and node failure

A GPU VM does not live-migrate:

- Whole GPUs in passthrough are not live-migratable in KubeVirt.
- KubeVirt v1.9, the release Cozystack ships, has live migration for one vGPU per VM as an alpha feature behind the `VGPULiveMigration` gate, and treats a GPU as migratable only when its resource name is listed under `mediatedDevices` in `permittedHostDevices` ([`pkg/virt-handler/vm.go`](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-handler/vm.go), `isMdevGPU`). An SR-IOV vGPU is a virtual function advertised under `pciHostDevices`, so it never migrates. This proposal does not enable the gate.

On a node drain, the GPU VM is evicted instead of blocking the drain, because Cozystack sets the `LiveMigrateIfPossible` eviction strategy. A VMI keeps the eviction strategy it was created with, so a VM started before Cozystack set it blocks the drain until it is restarted. What happens next depends on `runStrategy`, per KubeVirt v1.9.0 ([`pkg/virt-controller/watch/vm/vm.go`](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-controller/watch/vm/vm.go)):

- `Always`, the default, starts a new instance.
- `Manual` stays stopped.
- `Once` stays stopped while the finished instance remains, and starts a new one if it is deleted.
- `RerunOnFailure` starts again only for an instance in `Failed` without a deletion timestamp. An evicted guest that shuts down within its grace period ends in `Succeeded` and stays stopped; one destroyed after it ends in `Failed` and starts again. An instance that a controller deletes leaves it stopped.

The new instance is pinned to the node of its claims, and the class's `onCordon` policy decides where that is. Under `Move`, the default, the claim of an evicted VM moves to another node of its class with room and keeps its hold, and the new instance starts there. With no room elsewhere the claim stays, and the new instance waits in `Scheduling` with its launcher pod gated until a node has room. Under `Stay` the claim keeps its node, and the new instance is pinned to the cordoned node and waits there. When the node is deleted, the claims of a gated instance are gathered elsewhere, while a pinned instance is deleted by the controller of `gpudevice`, and the VM follows the rules above for a deleted instance. A drain for a node layout change moves the claim under either policy, and the VM can start on another node that has room, or waits while the claim is `Lost`. The Binding and capacity section of `gpudevice` holds the rules.

On a node failure, the virt-launcher pod on a node that lost power stays `Terminating`, and KubeVirt does not fail the VMI while that pod exists. Recovery needs someone to force-delete the pod, delete the Node, or set the `out-of-service` taint, which the [node fencing](../node-fencing/README.md) package does after its BMC fence succeeds. A VM whose disks are all on the `replicated` StorageClass may already be released by the Piraeus HA Controller that Cozystack ships; that is its documented behaviour, not verified end to end with virt-launcher pods on Cozystack, and for a VM with other volumes the controller's volume rules decide ([`pkg/agent/reconcile_failover.go`](https://github.com/piraeusdatastore/piraeus-ha-controller/blob/v1.3.1/pkg/agent/reconcile_failover.go)). Meanwhile the claim goes to `Lost` once the Node has been `NotReady` longer than a grace period, and is bound to another node after KubeVirt fails the old instance. The guest then boots fresh on that node, under the same `runStrategy` rules. If the old instance ends before that grace period, through the fence, the HA Controller or a forced pod delete, its claims are no longer attached. The next instance of an `Always` or `RerunOnFailure` VM gathers them onto a `Ready` node with room when it is created and starts there at once. The exception is a claim of a `Stay` class on a cordoned node. When all of the VM's claims are on that node, the instance is pinned there, and if the node stays down past the grace period the controller of `gpudevice` deletes the unscheduled instance: an `Always` VM then starts on another node, and a `RerunOnFailure` VM stays stopped. Otherwise the claims cannot be gathered, and the instance waits in `Scheduling` with its launcher pod held at a scheduling gate (the Attaching section of `gpudevice`). After a `Node` deletion the VM starts the same way, once the controller of `gpudevice` has moved the claims. When no node fits, the instance waits gated, with a condition on its claims naming the reason, and starts as soon as a node has room for its claims; KubeVirt's create backoff does not apply, because nothing is refused. `Once` and `Manual` VMs keep their failed instance: a `Once` VM stays stopped, and a `Manual` VM starts when the tenant starts it. The Failure and edge cases section of `gpudevice` holds the details. Neither this proposal nor `gpudevice` keeps spare GPUs free for this case.

The paragraphs above describe VMs whose GPU entries are all `claim` entries. On a drain or a node failure, a VM whose GPU entries are all `claimTemplate` entries holds nothing between instances. Its claim is deleted when the instance ends, so neither `onCordon` nor the rebind above applies. The next instance gets a new claim on a `Ready`, uncordoned node with room, or waits gated, with a condition naming the reason, while there is none or the tenant's budget is used up. For a VM with more than one claim, which move together and only to a node with room for all of them, or that mixes template and held claims, the Attaching section of `gpudevice` decides where the claims go and when the instance waits gated.

### 6. Guide for guest images

The Cozystack documentation gets a page for hosters that describes what a GPU guest image needs. It names no binaries and contains nothing from the driver packages.

For vGPU:

- The guest driver must be compatible with the host vGPU Manager. NVIDIA supports a guest driver from any release of the host's branch, from the previous branch, and from the previous long-term support branch on a long-term support host. A guest driver from a later branch than the host is not supported, and with an incompatible guest driver the vGPU fails to load. The release notes of the host release list the exact combinations, so an upgrade of the host vGPU Manager has to be checked against the `driverBranch` of every vGPU image.
- The guest license client reads a client configuration token from `/etc/nvidia/ClientConfigToken/` and the feature type from `/etc/nvidia/gridd.conf`. For a vGPU guest, NVIDIA documents `FeatureType=1`, which picks the license from the vGPU profile. The `gridd.conf` template also lists `4`, for NVIDIA Virtual Compute Server, now named NVIDIA vGPU for Compute, but NVIDIA's licensing guide does not list `4` for a vGPU guest. A Windows guest takes the token in `%SystemDrive%\Program Files\NVIDIA Corporation\vGPU Licensing\ClientConfigToken` and keeps its other license settings in the registry. The hoster runs or rents the license server and decides whether the token is baked into the image or injected at boot, for example through cloud-init. Either way, root inside the guest can read it.
- On vGPU, NVIDIA disables Unified Memory and debuggers by default, and profiling apart from GPU workload trace. Enabling them is a host-side parameter of each vGPU, which none of the GPU proposals sets.

For C-series (compute) profiles, the driver, license and version rules come from NVIDIA AI Enterprise: the vGPU for Compute guest driver is downloaded from the NGC catalog, its license is the NVIDIA AI Enterprise license, and the NVIDIA AI Enterprise support matrix lists the Virtual GPU Manager and guest driver versions of each release, for example 595.91.04 on the host and 595.91.07 in a Linux guest for release 8.2 ([NVIDIA AI Enterprise licensing](https://docs.nvidia.com/ai-enterprise/release-8/latest/infra-software/vgpu/licensing.html), [NVIDIA AI Enterprise guest driver](https://docs.nvidia.com/ai-enterprise/release-8/latest/infra-software/vgpu/installation/guest-driver.html), [support matrix 8.2](https://docs.nvidia.com/ai-enterprise/release-8/latest/support/support-matrix-8/8.2.html)). NVIDIA AI Enterprise supports vGPU for Compute on a KVM host only in limited setups, which the Host layer section of `gpudevice` names, so support for a compute image on such a host is a matter between the hoster and NVIDIA. The token path, `gridd.conf` and the Windows registry stay as NVIDIA's [licensing guide](https://docs.nvidia.com/vgpu/latest/grid-licensing-user-guide/) describes.

For passthrough used for compute, the guest uses the NVIDIA data center driver, which has no license check. Graphics passthrough runs the vGPU software guest driver and is licensed, typically with `FeatureType=2`, NVIDIA RTX Virtual Workstation. Its image follows the token and license settings above, but not the points on the host vGPU Manager and Unified Memory, because a passthrough GPU bypasses the vGPU Manager.

For both, the image needs room for the driver and its kernel module build, so `storage` must be larger than for a plain cloud image. The hoster prefixes the names of its entries, so that a default image added in a later release cannot take the same name.

## User-facing changes

- Hosters: `extraImages`, and `gpu`, `secretRef`, `secretExtraHeaders`, `certConfigMap` per image in `vm-default-images` values. A documentation page for GPU guest images.
- Tenants: the image picker shows the GPU marking next to the name of a marked image. GPU VMs take `claim` or `claimTemplate` entries, as `gpudevice` defines.

## Upgrade and rollback compatibility

All new fields are optional, and an entry without them renders the same `DataVolume` as before. The new annotations are added to existing `DataVolume`s in place and do not cause a new import. On rollback Helm removes them again, `extraImages` entries are no longer rendered, and Helm deletes their `DataVolume`s. Disks already cloned from them are separate PVCs and stay, and a clone that has not started copying yet can lose its source. An entry of `images` that uses `secretRef`, `secretExtraHeaders` or `certConfigMap` blocks the rollback, because the old chart renders it without those fields and CDI refuses the change to the `DataVolume` spec. The hoster moves such entries to `extraImages` or removes them before rolling back, and either way their golden images are deleted.

## Security

- `secretRef` and `secretExtraHeaders` point to Secrets the hoster creates in `cozy-public`. Tenants cannot read Secrets there, and the chart only passes the names to CDI.
- A GPU guest image contains the guest driver, and an image with the vGPU guest driver may contain the hoster's license token. Golden images in `cozy-public` are open to every tenant: any tenant can clone one and boot it as root. The vGPU entitlement of `gpudevice`, the Tenant flag `gpu.vgpu`, which an entitled namespace shows as the label `namespace.cozystack.io/vgpu: "true"`, decides who can order a vGPU class, but not who gets the driver and the token from a shared image. The label holds the effective value, the tenant's flag combined with its parent's entitlement, and any other value, or no label, means no entitlement. The golden image collection has no per-tenant visibility, unlike the per-tenant registry access of `kairos-vgpu-workers`. A hoster whose NVIDIA terms require the driver to stay with entitled tenants keeps vGPU guest images out of the shared catalog and uploads them into the namespace of each entitled tenant instead. Such a hoster also keeps the token out of any shared image. A license server reachable only from tenant networks limits where a token that leaks out of the cluster can check out licenses.
- CDI v1.66.1 sends the basic auth credentials and the extra headers again on every HTTP redirect, whatever host it points to. A hoster whose image server redirects, for example to a CDN, sends them there too.
- No new tenant input. The marking is set by the hoster and only displayed.

## Failure and edge cases

- The credentials for an image URL are wrong, or the Secret is missing → CDI does not treat this as fatal. The import retries without end, or the importer never starts, and the `DataVolume` never reaches `Failed`. The package stays not Ready, the image stays in the picker, and a tenant disk cloned from it waits, as for any golden image with a broken source today.
- A name appears twice, in both lists or within one → rendering fails and names it. This also happens when a later Cozystack release adds a default image with the name of a hoster entry, so the guide tells hosters to prefix their names.
- An `extraImages` name matches a hand-made `DataVolume` in `cozy-public` → helm-controller takes ownership of the existing object. If the specs differ, CDI refuses the change and the release fails; if they are equal, the release adopts it and deletes it with its PVC when the entry leaves `extraImages` or on rollback. The hoster deletes the hand-made one or picks another name.
- `gpu` is set without `vendor` or `mode`, or with a `mode` other than `vgpu` or `passthrough` → rendering fails and names the image.
- The hoster changes the source of an image whose `DataVolume` already exists → CDI refuses the change to the `DataVolume` spec and the upgrade of the package fails, as for a changed `url` today. The hoster adds the image under a new name instead.
- A GPU VM boots from an image without a matching guest driver → the VM runs and the device is unusable.
- The vGPU guest driver is incompatible with the host vGPU Manager, for example after a host upgrade → the vGPU fails to load in the guest. The hoster publishes an image with a compatible driver, and tenants rebuild their VMs from it.
- The node of a GPU VM is drained or fails → Design §5.

## Testing

- Helm unit tests in `vm-default-images`: annotations from `gpu`, the secret and CA fields in the `http` source, rendering of `extraImages`, an entry without the new fields rendering the same `DataVolume` as before, and the failures on a duplicate name, on a `gpu` without `vendor` or `mode`, and on an unknown `mode`.
- Go unit tests for the `image` option provider: the label from `DataVolume` annotations, the label without `driverBranch`, no label when listing `DataVolume`s fails even if the PVC carries a marking, and unchanged output for images without a marking.
- Dashboard unit test for the image picker showing the label when it is set and the value otherwise.
- Manual on hosts with GPUs: import an image from an authenticated HTTP source, create a disk and a VM with a vGPU claim, check that the guest is licensed. Repeat with a passthrough claim. CI cannot do this, because it has no GPU, no driver and no license.

## Rollout

This proposal is implemented after `gpudevice`: it is step 3 of the Rollout of `gpudevice`, on top of its steps 1 and 2. Step 1 adds `claim` and `claimTemplate` entries to `vm-instance`. One release ships Design §1 and §2 and the guide.

## Open questions

- Should a registry source (`registry` in CDI) also be supported for golden images, for hosters who keep images in an OCI registry?
- Should the image picker show the import state, so that a tenant does not clone an image whose import is stuck?
- Should a GPU VM show a warning in the dashboard when none of its disks comes from a marked image?
- Should the image picker and the clone of a golden image marked `mode: vgpu` be limited to namespaces whose label `namespace.cozystack.io/vgpu` is `"true"`? The label gives the signal. What is open is whether a cloning rule in `cozy-public` keyed on it is worth its cost.
- Should tenants be able to mark their own uploaded images, so the picker treats them the same way?

## Alternatives considered

A new package or CRD only for GPU images was rejected: the golden image collection already does everything except authentication and marking, and a second catalog would need its own picker and its own clone path in `vm-disk`.

Cozystack could build the image from a hoster-supplied driver, as `kairos-vgpu-workers` does with a build recipe. That was rejected for VMs: a VM guest is a full OS that the tenant owns, and hosters will want different distributions, versions and extra software. A recipe would cover one of them and leave the rest to the hoster.

A `DataVolume` created by hand already accepts any CDI source and could carry the marking annotations. It was not used as the base, because it is not declarative, not covered by the package health check, and not reconciled.

Restating the default list instead of adding `extraImages` works today without a change, but the hoster's copy of the defaults goes stale with every Cozystack release that changes them.
