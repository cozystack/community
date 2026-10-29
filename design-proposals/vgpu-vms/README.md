# vGPU licensing for virtual machines

- **Title:** `vGPU licensing for virtual machines: the licensee, token delivery, license server reachability, usage and revocation`
- **Author(s):** `@lexfrei`

## Overview

A vGPU in a virtual machine works only when the NVIDIA guest driver inside it holds a license, and the license comes from an NVIDIA license server that the guest reaches over the network with a client configuration token. Today nothing in Cozystack says whose license that is, how the token gets into a tenant's guest, how a guest on a tenant network reaches the server, how license use maps back to tenants, or what happens on rotation and revocation. The usual answer, a token baked into a shared guest image, hands the token to every tenant that can clone the image.

This proposal settles licensing for vGPU VMs on top of [`gpudevice`](../gpudevice/README.md). The hoster's license is the default: a vGPU class names the hoster's token Secret, and the platform puts the token on a disk of each VM that uses the class, in entitled tenants only, as section 6 of `gpudevice` and its record 0018 define. A hoster can also offer classes without a license for tenants that bring their own. The proposal gives the guest-side contract, the network path to the license server, how a hoster reconciles license use with the usage records, rotation and revocation, and what a tenant sees when its guest is not licensed.

NVIDIA's vGPU stack changes quickly: within the last few releases the driver branches, the way vGPUs are exposed, and the licensing rules all changed. This proposal describes that stack as it was in October 2026; check the versions named here against your own before relying on them.

## Scope and related proposals

- [`gpudevice`](../gpudevice/README.md) defines GPU classes, class entries in `vm-instance`, the vGPU entitlement and the license delivery this proposal builds on (its section 6 and [record 0018](../gpudevice/decisions/0018-a-vgpu-license-reaches-each-vm-as-a-secret-volume.md)). This proposal adds no API of its own.
- [Kairos tenant worker pools](https://github.com/cozystack/community/pull/115) (cozystack/community#115) is where worker pools on Kairos are proposed. A Kairos GPU worker image follows the same guest contract (section 2).
- Getting a GPU guest image into the golden image collection is not part of this proposal; hosters already have two ways (Context). Recognising golden images by a label instead of a name prefix would help every image and belongs in a generic golden-image proposal.

## Context

### What exists

A hoster can already offer a GPU guest image in two ways. It overrides `images[]` of the `cozystack.vm-default-images` Package, or it creates a CDI `DataVolume` named `vm-default-images-<name>` in `cozy-public` by hand (`hack/cdi_golden_image_create.sh`), with any CDI source, `secretRef` and `certConfigMap` included. The image picker and `vm-disk` match golden images by that name, so a hand-made image is a full golden image. CDI's importer runs inside the cluster, so a `url` reachable only from the cluster network works with either way.

`gpudevice` gives a VM its GPUs through a class entry naming a `GPUClass`. A vGPU class can carry a `license`: the name of the hoster's token Secret in `cozy-system` and the `FeatureType`. The GPU controller copies that Secret into every entitled namespace, and the VMI webhook gives each VMI of the class a KubeVirt `secret` volume with the volume label `GPULICENSE`.

NVIDIA licenses vGPU through the NVIDIA License System. A license server is either NVIDIA's Cloud License Service (CLS) or a Delegated License Service (DLS) instance the hoster runs. A guest needs the client configuration token, which names the server, and reaches it over TCP 443 by default; a Windows guest also uses TCP 80 to return its license at shutdown, and a DLS running as a container can listen on other ports ([NVIDIA License System user guide](https://docs.nvidia.com/license-system/latest/nvidia-license-system-user-guide/index.html)). The server leases a license to each guest for a period the allocation's lease settings set or bound; a lease a guest does not return, because the VM stopped without a clean shutdown, stays taken until it expires. For a vGPU guest, NVIDIA documents `FeatureType=1`, which picks the license from the vGPU type ([client licensing guide](https://docs.nvidia.com/vgpu/latest/grid-licensing-user-guide/configuring-nls-licensed-client.html)).

A tenant namespace allows egress to destinations outside the cluster, to its own tenant and to a fixed list of platform services, and to no other namespace of the cluster (`packages/apps/tenant/templates/networkpolicy.yaml`). A `vm-instance` VM's first interface is on the pod network.

### The problem

- Nobody says who the licensee is: the hoster's NVIDIA entitlement, the tenant's own, or both.
- The token has no defined path into a guest. A token baked into a shared golden image is readable by every tenant that can clone it, and rotating it means rebuilding the image and every disk cloned from it.
- A guest on a tenant network may not reach the license server.
- The hoster cannot map its NVIDIA license count to tenants.
- Rotation, revocation and the state of an unlicensed guest are not described.

## Goals

- The hoster's license is the default, delivered by the platform to the VMs of entitled tenants only, with no token in any shared image.
- A tenant can bring its own license on a class the hoster offers for that.
- A guest image knows exactly where to find the token and the license settings.
- The hoster knows where to put the license server so that tenant VMs reach it.
- The hoster can reconcile license use with the usage records per tenant.
- Rotation, revocation and an unlicensed guest have defined, visible outcomes.

### Non-goals

- Golden image plumbing: list overrides, private sources, per-tenant visibility of images, and markings in the picker.
- Building, shipping or testing guest images or drivers. The images and the driver are the hoster's.
- Running the license server. The hoster runs a DLS instance or uses CLS.
- Licensed passthrough GPUs for graphics. `gpudevice` gives a `license` only to vGPU classes.

## Design

### 1. The licensee

The hoster's NVIDIA entitlement is the default licensee. A vGPU class with a `license` names the hoster's token Secret, and every VM of the class in an entitled tenant gets that token. The hoster's license server counts these guests against the hoster's entitlement, and the hoster charges tenants for them through its billing system (section 4).

A tenant with its own NVIDIA entitlement brings its own license on a vGPU class without a `license`, which the hoster publishes for that purpose. The platform delivers nothing for such a class: the tenant puts its own token into its guest, for example through its image or cloud-init, and its guest reaches the tenant's own license server. Such an image does not run the reference boot unit or script of section 2: the boot unit points the license client at an empty token directory and the script empties the default token folder at every boot. The tenant still needs the vGPU entitlement of `gpudevice`, because that flag decides who may use a vGPU class at all.

Both kinds of class can exist on one cluster, and the hoster decides per class. A change of a class's `license` reaches each VM at its next start.

### 2. The token in the guest

The platform puts the token on a disk of each VMI that uses a licensed vGPU class, as section 6 of `gpudevice` defines. The guest finds a disk with the volume label `GPULICENSE` that holds two files: `client_configuration_token.tok`, the token, and `gridd.conf`, which sets the class's `FeatureType`. A guest image with the vGPU guest driver applies them at boot:

- On Linux, before the license client starts, it mounts the disk by its label, copies the token into a directory on a memory file system such as `/run`, with the file modes NVIDIA's licensing guide asks for, and puts a `ClientConfigTokenPath` line naming that directory and the `FeatureType` line of the disk's `gridd.conf` into `/etc/nvidia/gridd.conf`, keeping any other settings the image carries there. The token never reaches the root disk, so a guest restarted without the `GPULICENSE` disk has none ([NLS user guide](https://docs.nvidia.com/license-system/latest/nvidia-license-system-user-guide/index.html), section 5.1.3.2).
- On Windows, it empties `%SystemDrive%\Program Files\NVIDIA Corporation\vGPU Licensing\ClientConfigToken` at every boot, also without the disk, and copies the token into it when the disk is there. A Windows guest keeps its other license settings in the registry, and NVIDIA's current guide does not require the `FeatureType` value there for a vGPU. It then restarts the `NvDisplayContainer` service, as NVIDIA's procedure does (NLS user guide, section 5.1.1). This folder is on the system disk, and this proposal names no folder in memory for Windows: the `ClientConfigTokenPath` registry value can point elsewhere (NLS user guide, section 5.1.3.1), but the guide wants that folder to hold the token only, which rules out the `GPULICENSE` disk itself. The license service can start before the script and read the token of the previous boot, and what it does with a license so acquired once the script empties the folder and restarts it is not documented.

The Cozystack documentation carries a reference boot unit for Linux and a reference script for Windows that do this. They contain nothing from NVIDIA's packages. A Kairos GPU worker image follows the same contract.

The earlier Kairos GPU proposal, cozystack/community#101, put the token into each pool's bootstrap data. VMs do not reuse that, because bootstrap data cannot change on a running node, so a rotation would replace every machine.

The token reaches the guest only through that disk: it is in no image, no `vm-instance` value and no cloud-init data. A tenant that turns the VM off and on again gets the token of the moment, so rotation is a restart (section 5).

### 3. Reaching the license server

A licensed guest opens TCP 443 to the address in its token, and a Windows guest also TCP 80, unless the hoster's DLS listens on other ports. A tenant namespace allows egress to destinations outside the cluster and to a fixed list of platform services only, and a VM's first interface is on the pod network, so a guest reaches a license server at an address outside the cluster, which is neither a pod nor a node address: NVIDIA's CLS, when the cluster has internet egress, or a DLS instance the hoster runs as an appliance on a network the cluster's egress routes to. The hoster puts that address into the token it downloads, so every guest uses it.

A VM whose only route out is a tenant subnet without egress does not reach the server, and its guest stays unlicensed (section 6). A tenant that isolates its VM keeps an egress path to the license server's address.

A DLS running in a namespace of the management cluster is not reachable from tenant namespaces as they are. Allowing it would need an egress rule from every entitled namespace to that namespace, which this proposal does not add (Alternatives).

### 4. Usage and the license count

The usage records of `gpudevice` show, per tenant, which VM ran how many units of which class and for how long: the launcher pod's `Workload` carries the class's resource name while the pod exists. NVIDIA's license server counts licensed clients under the hoster's terms and shows the leases it has granted. The two counts differ: a VM that stopped without a clean shutdown keeps its lease, and so its place in NVIDIA's count, until the lease expires, while its `Workload` record is gone with its pod. Cozystack does not read the license server. The hoster reconciles the two on its side: the license count is what it pays NVIDIA, and the `Workload` records are what it charges each tenant. For a class without a `license` the tenant's own license server counts, and the hoster charges only for the GPU.

### 5. Rotation and revocation

A client configuration token names the license allocation that serves it, and NVIDIA's license system documents no way to revoke a token before its expiry ([NLS user guide](https://docs.nvidia.com/license-system/latest/nvidia-license-system-user-guide/index.html), section 4.4). The expiry is chosen when the token is generated, 12 years if none is chosen, and a DLS token can be made never to expire (NLS user guide, sections 4.4.1 and 4.4.2).

To rotate the token, the hoster generates a new one on a new allocation or license server instance, replaces it in its Secret, and the controller refreshes every copy. A running guest keeps the token it booted with, and each VM gets the new one at its next start. Once the VMs have restarted, the hoster disables leasing on the old allocation, which then serves no licenses (NLS user guide, section 4.6); a new instance has an address of its own, which guests reach as section 3 describes. A guest still on the old token keeps its lease at most for the maximum lease duration set on that allocation (NLS user guide, section 4.7).

To revoke a tenant, the parent's administrator or the hoster clears its `gpu.vgpu` flag. The controller deletes the namespace's copies, and the VMI webhook refuses the next start of every VM on a vGPU class there. A running guest renews its lease while the allocation serves its token, and a client released on the license server can check a license out again while the token is served (NLS user guide, section 4.9.1). A hoster or parent administrator that must cut a tenant off at once therefore also stops the tenant's vGPU VMs.

Releasing leases on the license server frees the licenses of guests that stopped without a clean shutdown. Single clients are released by hand, within a rolling daily limit of 10% of the licenses allocated to the server (NLS user guide, section 4.9.1). A bulk release on CLS frees every lease of an allocation. On DLS it frees the leases the hoster selects, filtered by client hostname, IP or MAC address, so the hoster can pick one tenant's guests on a shared allocation if it recognises them; a bulk release can be done five times before its limit has to be reset (NLS user guide, section 4.9.2).

A guest's root user can read the token and keep a copy, and only a rotation stops such a copy from working. One token serves every tenant of a class, and every entitled namespace gets a copy for every licensed class, so a token does not tell tenants apart and the platform cannot give one tenant a token of its own.

### 6. What a tenant sees

- Without the vGPU entitlement, a VM on a vGPU class does not start: the VirtualMachine shows the refusal in a `FailedCreate` event that names the class and the entitlement.
- With the entitlement but before the namespace has its copy, the VM shows `Starting`, and its launcher pod waits in `ContainerCreating` with a `FailedMount` event naming the Secret; the class shows `LicenseSecretMissing` when the hoster's Secret is gone or has no token.
- A guest that cannot get a license, because it does not reach the server, its leases were released or its image does not follow section 2, starts with the GPU at full capability and then degrades, by profile series:
  - A C-series (compute) profile, licensed through NVIDIA AI Enterprise: after 20 minutes without a license, compute drops to an idle level until a license is acquired; a licensed VM that loses contact with the license server keeps full capability for 7 days by default on CLS, a period a DLS can configure, and then degrades ([NVIDIA AI Enterprise 8 licensing](https://docs.nvidia.com/ai-enterprise/release-8/latest/infra-software/vgpu/licensing.html)).
  - A Q-series (graphics) profile, licensed through NVIDIA vGPU software: performance drops after 20 minutes without a license and drops further after 24 hours ([client licensing guide, release 20](https://docs.nvidia.com/vgpu/latest/pdf/grid-licensing-user-guide.pdf)); the release 17 guide gives the figures, 15 frames per second with slower CUDA after 20 minutes, and 3 frames per second with failing CUDA calls after 24 hours ([release 17](https://archive.docs.nvidia.com/vgpu/17.0/pdf/grid-licensing-user-guide.pdf)).

  Full capability returns once the guest gets a license, although applications started while it was degraded may need a restart. Inside the guest, `nvidia-smi -q` shows the license status. NVIDIA AI Enterprise 8 is the release the guide in section 7 names, and release 20 is NVIDIA vGPU software's current release; check both against the releases you run.
- On a class without a `license`, the platform delivers nothing, and the guest is licensed only when the tenant licenses it.

### 7. Guide for guest images

The Cozystack documentation gets a page for hosters on what a vGPU guest image needs. It names no binaries and contains nothing from the driver packages.

- The guest driver must be compatible with the host vGPU Manager. A guest driver comes from the host's release or an earlier release of its branch, or from the previous branch ([release notes for Linux with KVM, release 20](https://docs.nvidia.com/vgpu/latest/pdf/grid-vgpu-release-notes-generic-linux-kvm.pdf), section 1.2.1). Section 1.2.1 also lists a later release of the same branch and, on a long-term support host, a driver from the previous long-term support branch. The release 20 table in section 1.2.2 lists every 18.x and earlier guest driver as incompatible, and known issue 3.10 lists a driver from a later release than the vGPU Manager, or from two or more branches back, as incompatible, so this guide keeps to the reading all three allow. A guest driver from a later branch than the host is not supported (section 1.2.2), and with an incompatible guest driver the vGPU fails to load. The release notes of the host release list the exact combinations, so an upgrade of the host vGPU Manager has to be checked against the driver of every vGPU image.
- The image applies the token and `gridd.conf` from the `GPULICENSE` disk at boot as section 2 describes.
- A token that never expires, or expires more than 12 years after it is generated, is not recognised by NVIDIA vGPU releases 13.0 through 13.7 and 15.0 through 15.2, so an image with a guest driver from those releases needs a token with an earlier expiry (NLS user guide, section 4.4.2).
- On vGPU, NVIDIA disables Unified Memory and debuggers by default, and profiling apart from GPU workload trace. Enabling them is a host-side parameter of each vGPU, which none of the GPU proposals sets.
- For C-series (compute) profiles, the driver, license and version rules come from NVIDIA AI Enterprise: the vGPU for Compute guest driver is downloaded from the NGC catalog, its license is the NVIDIA AI Enterprise license, and the NVIDIA AI Enterprise support matrix lists the Virtual GPU Manager and guest driver versions of each release, for example 595.91.04 on the host and 595.91.07 in a Linux guest for release 8.2 ([NVIDIA AI Enterprise licensing](https://docs.nvidia.com/ai-enterprise/release-8/latest/infra-software/vgpu/licensing.html), [NVIDIA AI Enterprise guest driver](https://docs.nvidia.com/ai-enterprise/release-8/latest/infra-software/vgpu/installation/guest-driver.html), [support matrix 8.2](https://docs.nvidia.com/ai-enterprise/release-8/latest/support/support-matrix-8/8.2.html)). NVIDIA AI Enterprise supports vGPU for Compute on a KVM host only in limited setups, which the Host layer section of `gpudevice` names, so support for a compute image on such a host is a matter between the hoster and NVIDIA.
- The vGPU guest driver is licensed per customer and may not be published. A hoster whose NVIDIA terms require the driver to stay with entitled tenants keeps vGPU guest images out of the shared golden images and gives them to entitled tenants only, for example as images in their own namespaces.
- The image needs room for the driver and its kernel module build, so its disk must be larger than for a plain cloud image. A hoster that adds golden images by hand prefixes their names, so that a default image added in a later release cannot take the same name.

## User-facing changes

- Hosters: a documentation page for vGPU guest images and licensing, with the reference boot unit and script. The `license` field of `GPUClass` and the vGPU entitlement come from `gpudevice`.
- Tenants: a VM on a licensed vGPU class gets its license without any action of its own. A tenant that brings its own license uses a class without `license` and licenses its guest itself.

## Upgrade and rollback compatibility

This proposal adds documentation and reference scripts, no API and no chart change. A guest image built before it keeps working as before: an image that does not read the `GPULICENSE` disk ignores it and is licensed the way it was, or not at all. Rolling back `gpudevice` removes the disk, as its Upgrade and rollback compatibility section describes, so the hoster first puts the license token into private images it gives entitled tenants only. Such a private image does not run the reference boot unit or script of the guest contract, since the boot unit points the license client at an empty token directory and the script empties the token folder at every boot. An existing VM whose image follows section 2 keeps no token across boots, so it stays unlicensed after the rollback restarts it until it is recreated from such a private image, or its tenant disables the reference boot unit or script, removes the `ClientConfigTokenPath` line from `gridd.conf` on Linux, and licenses the guest itself.

## Security

- The token reaches only the guests of entitled tenants, where the guest's root user can read it, which licensing a guest needs. It is in no shared image, so a tenant without the entitlement cannot read it by cloning a golden image.
- A tenant super-admin who writes a VirtualMachine directly can mount its namespace's copy into it, which is still within an entitled tenant.
- A token that leaks out of a guest can lease licenses from wherever it reaches the license server, until it expires, which on DLS can be never. A DLS reachable only from the cluster's egress addresses limits where; a short expiry chosen when the token is generated limits how long.
- A tenant that brings its own license keeps its token in its own image or cloud-init data, under its own control.

## Failure and edge cases

- A vGPU VM is refused at start → the tenant has no vGPU entitlement; the parent's administrator sets `gpu.vgpu`, or the hoster on a top-level tenant.
- A vGPU VM stays `Starting` with a `FailedMount` event → the namespace has no license copy yet; check the class's `LicenseSecretMissing` condition and the hoster's Secret.
- A guest runs but is unlicensed → `nvidia-smi -q` in the guest shows the status; the guest does not reach the license server on its port, TCP 443 by default (and 80 on Windows), the image does not follow section 2, or its lease was released. Fix the route, the image, or the token, then restart the guest's license client or the VM.
- A guest keeps the old token after a rotation → it has not restarted since; restart it before the old token expires or its allocation stops leasing.
- The vGPU guest driver is incompatible with the host vGPU Manager, for example after a host upgrade → the vGPU fails to load in the guest. The hoster publishes an image with a compatible driver, and tenants rebuild their VMs from it.

## Testing

- The platform side, the copies and the `GPULICENSE` volume, is tested in `gpudevice`.
- Tests for the reference boot unit and script: the files land in the right paths with the right modes, and a Linux guest without the disk has no token, and a Windows guest without it has an empty token folder.
- Manual on hosts with vGPU: a VM on a licensed class gets licensed on first boot; after a token rotation and a restart it uses the new token; an unentitled tenant is refused; a guest without a route to the license server shows as unlicensed. CI cannot do this, because it has no GPU, no driver and no license.

## Rollout

This proposal ships with the license delivery of `gpudevice`, item 2 of its Rollout, and is its item 3 for VMs: the documentation page and the reference scripts. It does not wait for step 2 of `gpudevice`.

## Open questions

- Should the platform optionally allow egress from entitled namespaces to an in-cluster DLS, for hosters that run DLS as a container in the management cluster?
- Should Cozystack read lease counts from DLS to show license use per tenant next to the usage records, or leave that entirely to the hoster's billing system?

## Alternatives considered

A token baked into a shared guest image. Anyone who can clone the image reads the token, every tenant can clone a golden image, and rotation means rebuilding the image and every disk cloned from it. Rejected, as in record 0018 of `gpudevice`.

A license server reachable only from tenant networks, with the token in the image, the way KMS serves Windows images. It limits where a leaked token can lease licenses, but the token still reaches every tenant that clones the image, and the network rule has to follow every tenant network. Rejected for the same reason; a DLS outside the pod network gives the same limit without putting the token into images.

The token in `vm-instance` cloud-init, written by the hoster. The values belong to the tenant, who reads them, and every VM would need the hoster to edit it. Rejected; cloud-init stays the tenant's own path for a license it brings.

A DLS inside the management cluster with an egress rule from every entitled namespace to it. It keeps the server off the hosts' network, but it adds a platform-managed exception to tenant isolation for one vendor's service. Left as an open question rather than the default.

Golden image changes for GPUs: an `extraImages` list, a `gpu` marking in the picker, and per-tenant visibility of vGPU images. Dropped: hosters already add images by hand or through the `images[]` override, a marking enforces nothing, and visibility by namespace label departs from the standard RBAC model. A generic golden-image proposal can take up label-based discovery.
