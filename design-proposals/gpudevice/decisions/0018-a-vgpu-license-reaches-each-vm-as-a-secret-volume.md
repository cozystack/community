# 0018. A vGPU license reaches each VM as a Secret volume, copied only into entitled namespaces

- **Number:** `0018`
- **Date:** `2026-10-29`
- **Status:** Accepted
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#99`](https://github.com/cozystack/community/pull/99)
- **Implemented in:** not yet

## Context

A vGPU guest needs two things before its driver licenses the GPU: the client configuration token the hoster downloads from its NVIDIA license server, and a `gridd.conf` with the `FeatureType` that fits the profile. The proposal left both to the hoster: in VMs through the guest image or at boot, and in Kairos worker pools through a separate proposal, which copied the hoster's Secrets into the namespaces of entitled tenants and put the token into each pool's bootstrap data. Rotating a token there meant replacing every vGPU machine.

Golden images in `cozy-public` are open to every tenant: any tenant can clone one and boot it as root. A token baked into such an image is readable by every tenant, entitled or not. The license is a property of the offer, the class, and not of holding, so its delivery belongs to step 1.

## Decision

`GPUClass` carries a `license`, the name of the hoster's token Secret in `cozy-system` and the `FeatureType`, `1` by default or `4`, on vGPU classes only. NVIDIA's [client licensing guide](https://docs.nvidia.com/vgpu/latest/grid-licensing-user-guide/configuring-nls-licensed-client.html) documents `1` for a VM with a vGPU, since "NVIDIA vGPU software automatically selects the correct type of license based on the vGPU type", and its `gridd.conf` template lists `4` as "NVIDIA vGPU for Compute"; `4` has been observed working on a C-series profile, and the field lets a hoster set it per class. The GPU controller copies each such Secret into every namespace whose `namespace.cozystack.io/vgpu` label is `"true"`, adding a `gridd.conf` with the class's `FeatureType`, and deletes the copies when the label, the class or its `license` goes. At VMI creation the VMI webhook adds a KubeVirt `secret` volume for the copy of the VMI's vGPU class, with the volume label `GPULICENSE`, and at every boot a Linux guest copies the token from that disk into a directory on a memory file system and puts a `ClientConfigTokenPath` line naming that directory and the `FeatureType` into its `gridd.conf`, so the token stays off its root disk; `vgpu-vms` covers Windows guests, whose token folder is on the system disk. VMs and pool workers work the same way, and neither chart changes for the license. Rotation is a VMI restart.

## Why not the alternatives

- **A token baked into a shared guest image.** Anyone who can clone the image reads the token, and every tenant can clone a golden image. Rotation means rebuilding the image and every disk cloned from it.
- **A license server reachable only from tenant networks, with the token in the image**, the way KMS serves Windows images. It limits where a leaked token can lease licenses, but the token still reaches every tenant that clones the image, and the network rule has to follow every tenant network.
- **The token in the VM's cloud-init or the pool's bootstrap data.** The data lives in values a tenant reads, and bootstrap data cannot change on a running node, so rotation replaces every machine.

## Consequences

- Only the guests of entitled tenants get the token, and they get it as a disk, so a tenant's root user inside its VM can read it; that is what licensing a guest needs.
- A guest image or a worker image with the vGPU guest driver mounts the `GPULICENSE` disk at boot; an image that does not ignores the disk and is licensed the way it was, or not at all.
- The GPU controller writes Secrets in tenant namespaces, through the cluster-wide Secret write it already has.
- A token names the allocation that serves it, and NVIDIA's license system documents no way to revoke one before its expiry, which defaults to 12 years. Rotation therefore moves to a new allocation or license server instance and disables leasing on the old one, after which a remaining guest keeps its lease at most for that allocation's maximum lease duration ([NLS user guide](https://docs.nvidia.com/license-system/latest/nvidia-license-system-user-guide/index.html), sections 4.4, 4.6 and 4.7).
- Clearing the entitlement takes effect at each VM's next start. A running guest renews its lease while the allocation serves its token, and a released client can check a license out again while the token is served (NLS user guide, section 4.9.1), so an immediate cut-off also stops the tenant's vGPU VMs.
- Every entitled namespace gets a copy for every licensed class, so a token does not tell tenants apart, and the platform cannot give one tenant a token of its own.
- A licensed passthrough GPU for graphics still gets its token from a private image the hoster gives the tenants it licenses, not from the shared golden images, since a passthrough class carries no `license`.

## Revisit if

KubeVirt or the NVIDIA stack delivers a license to a guest without a Secret in the tenant namespace, or hosters need licensed passthrough classes for graphics (`FeatureType` 2), which would turn the vGPU entitlement into an entitlement to licensed classes. Drop `featureType` if `1` proves sufficient on every C-series profile the platform ships.
