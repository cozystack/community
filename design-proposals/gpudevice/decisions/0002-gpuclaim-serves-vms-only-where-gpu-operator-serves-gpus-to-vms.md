# 0002. GPU classes serve VMs only where gpu-operator serves GPUs to VMs, while claim finalizers and usage records always run

- **Number:** `0002`
- **Date:** `2026-10-21`
- **Status:** Accepted
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#99`](https://github.com/cozystack/community/pull/99)
- **Implemented in:** [`cozystack/cozystack#5008`](https://github.com/cozystack/cozystack/pull/5008) (the gate and the policies under it), [`cozystack/cozystack#5013`](https://github.com/cozystack/cozystack/pull/5013) (finalizers and usage records on every cluster), [`cozystack/cozystack#5030`](https://github.com/cozystack/cozystack/pull/5030) (the webhooks, the `vm-instance` refusal and the refusal of `forbidden`)

## Context

The proposal rolled step 1 out "on the existing variants" and said nothing about a cluster where GPUs do not reach VMs: gpu-operator under the `container` variant, gpu-operator not enabled, or no iaas bundle. On such a cluster KubeVirt runs without the `HostDevices` feature gate and without a `permittedHostDevices` table, and nothing there resolves a claim reference.

The `vm-instance` and `kubernetes-nodes` charts render only the entries, on the VMI template, and leave it to the VMI webhook to fill in the GPUs (section 4). Without the webhook a class or claim entry would boot the VM with no GPU and no error. The CRDs, on the other hand, ship with the controller on every cluster, so a `GPUClass` or, in step 2, a `GPUClaim` can exist on a cluster that has no placement running, and in step 2 both carry finalizers.

## Decision

The platform treats GPUs as serving VMs when the iaas bundle is on, `cozystack.gpu-operator` is enabled, and its variant is `default` or `vgpu`. Only there does it turn on the VMI and virt-launcher pod webhooks, the class status controller and its metrics scrape, and in step 2 the binder, the controller's watches on KubeVirt objects, the GPU admission policies and the tenant admin's write access to `GPUClaim`. Elsewhere:

- `vm-instance` and `kubernetes-nodes` refuse to render class and claim entries, through a cluster value the platform writes only where GPUs serve VMs, and `name` entries work as today.
- In step 2 the platform refuses `gpu.rawResourceNames: forbidden`, because the policy that enforces it does not render there.
- In step 2 the controller still adds and removes the finalizers of `GPUClass` and `GPUClaim` and keeps the usage records of claims, so deleting a class, a claim or a tenant namespace never hangs.

## Why not the alternatives

- **Run everything on every cluster.** The webhooks and the binder read KubeVirt objects, which do not exist without KubeVirt, and under the `container` variant the GPUs gpu-operator advertises are meant for pods, not VFIO devices a VM can take. A claim bound there would be held and recorded for a GPU no VM can use.
- **Gate the finalizer paths with the rest.** A hoster who disables GPUs, or moves to the `container` variant, would then leave every existing class and claim stuck on its finalizer, and with it the tenant namespaces that hold claims.
- **Let the charts render class and claim entries anyway and rely on the webhook.** On these clusters no webhook exists, so the failure is a VM that runs without the GPU it asked for, which the tenant may notice only from inside the guest.

## Consequences

- On a cluster without GPUs for VMs, a tenant reads the catalog, and in step 2 its claims, but cannot use them, and a VM with class or claim entries fails at render with a message that says GPUs for VMs are not served.
- Turning GPUs for VMs on writes one new cluster value, which every tenant release reads, so each upgrades once, and only on such clusters.
- In step 2 the controller keeps a resync loop while a deleted claim waits for its VMI, because with the feature off nothing watches VMIs.

## Revisit if

GPUs reach VMs through another path than gpu-operator's VM variants, for example a DRA driver installed on its own (section 10).
