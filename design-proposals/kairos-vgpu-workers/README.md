# GPU in Kairos worker pools with a hoster-supplied guest driver

- **Title:** `GPU in Kairos worker pools of tenant Kubernetes clusters with a hoster-supplied guest driver`
- **Author(s):** `@lexfrei`
- **Date:** `2026-10-14`
- **Status:** Review

## Overview

A hoster that sells NVIDIA GPUs wants tenants of Cozystack managed Kubernetes to give a worker pool GPUs, either a vGPU profile or a whole GPU in passthrough, with no driver work on the tenant side. This proposal gives each mode its own guest driver. A vGPU runs the NVIDIA vGPU guest driver, whose license client runs inside the guest. That driver is licensed per customer and may not be published, for example in a public image or repository. A passthrough GPU used for compute runs the regular NVIDIA data center driver and needs no license. Cozystack builds, ships and tests neither driver in CI.

This proposal adds an extension point instead of a driver. Cozystack ships a build recipe that layers a hoster-supplied driver `.run` onto the platform's Kairos worker image, one image per mode. The hoster names the resulting images in platform values, and for vGPU also a license Secret. A Kairos pool takes GPUs through the GPUClaim proposal's `claimSelector` or `claimTemplate`, names its class, boots the image of the class's mode, and for vGPU gets the license files at bootstrap.

NVIDIA's vGPU stack changes quickly. Within the last few releases the driver branches, the way vGPUs are exposed (mediated devices or SR-IOV virtual functions), the KubeVirt device plugins and the licensing rules all changed. This proposal describes that stack as it was in October 2026. Expect it to be updated as NVIDIA and KubeVirt move, with a decision record under it for each change of course, and check the versions named here against your own before relying on them.

## Scope and related proposals

- This proposal builds on the [GPUClaim proposal](../gpudevice/README.md) and is implemented after it. GPUClaim owns the host layer (host driver, VF profiles, device plugin), the GPU classes, per-tenant reservation and quotas, and usage records of allocated GPUs (model, count, owner) that an external billing system reads. Kairos pools take GPUs only through its `claimSelector` or `claimTemplate`; raw `gpus` entries by resource name are not accepted on Kairos pools.
- [`kubernetes-nodes-split`](../kubernetes-nodes-split/README.md) made worker pools a separate `KubernetesNodes` app. This proposal changes that app, the platform values, and `cozystack-controller`, which gets a Secret reconciler, and possibly the gpu-operator addon of the tenant `kubernetes` app (section 4).
- Kairos pools come from [kairos-io/cluster-api-provider-kairos#141](https://github.com/kairos-io/cluster-api-provider-kairos/pull/141) (merged), which adds `distribution: kubeadm` and needs Cluster API 1.13.4 or newer. [cozystack/cozystack#4902](https://github.com/cozystack/cozystack/pull/4902) moves Cluster API to v1.13.7. [cozystack/cozystack#4911](https://github.com/cozystack/cozystack/pull/4911) (draft), stacked on it, adds Kairos pools to the `kubernetes-nodes` chart.
- The [GPU virtual machines proposal](../vgpu-vms/README.md) (`vgpu-vms`) is a parallel consumer of the same host layer, for tenant virtual machines.

## Decisions

## Context

### What exists

The draft Kairos pool support gives `KubernetesNodes` an `os: talos|kairos` field (cozystack/cozystack#4911). The platform builds one Hadron worker disk per supported Kubernetes minor from `packages/apps/kubernetes-nodes/images/kairos-worker`, with a `test` build stage. Hadron is musl-based. `kairos.image` overrides the disk per pool. Tenant values reach the node only through a ConfigMap that a reconcile Job turns into a `KairosConfigTemplate`, never interpolated into shell. A Kairos pool refuses `gpus`, because the public image has no GPU driver.

A Hadron guest with the 595 vGPU guest driver was licensed against an NVIDIA License System server with a compute (C-series) profile and `FeatureType=4`, and ran CUDA. The license was NVIDIA AI Enterprise: compute profiles are licensed through NVIDIA AI Enterprise, not through NVIDIA vGPU software. Three things were needed beyond installing the driver:

- `/var/lib/nvidia` must exist, created by a `tmpfiles.d` entry.
- `/dev/nvidia-uvm` must be created with `nvidia-modprobe -u -c 0` before `nvidia-persistenced` starts, because the modules load before udev processes its rules.
- The driver userspace is glibc-linked, so it lives in a glibc prefix.

The data center driver in a Hadron guest with a passthrough GPU has not been tested. It is also glibc-linked, so it needs the same prefix; whether it needs the other two fixes is not verified. CUDA Unified Memory (`cudaMallocManaged`) is disabled by default on vGPU, a documented NVIDIA limitation.

On a test VM (Hadron v0.5.3, NoCloud datasource), Kairos saved the userdata to `/oem/95_userdata/userdata` during the first-boot install and applied that copy on every later boot; a changed datasource was not re-read. The bootstrap provider regenerates bootstrap data only from the machine's own `KairosConfig`, which a template change does not touch. Bootstrap files therefore cannot be changed on a running node; only a new machine gets new ones.

### The problem

A Kairos pool refuses `gpus` today, so a hoster cannot offer GPUs on Kairos pools in either mode. Both modes need a driver that Cozystack does not ship, and vGPU also needs a license token on every GPU node.

## Goals

- A hoster enables GPUs on Kairos pools, in vGPU mode, passthrough mode or both, through platform values, plus a license Secret for vGPU and registry credentials and a CA when the image registry needs them, without patching charts.
- A tenant attaches GPUs to a Kairos pool through GPUClaim, held claims with `claimSelector` or per-worker claims with `claimTemplate`, both naming the class.
- Cozystack copies the license token only to tenants with a vGPU entitlement, and the vGPU registry access too, so the vGPU guest driver reaches only them when the hoster's registry requires credentials. After a revocation, a token or registry credentials already delivered stay usable until the hoster rotates or revokes them on the license server or the registry, and on the interim path new machines of an applied template keep getting the token (section 3).
- Cozystack's tree, CI, and registries never contain NVIDIA driver binaries.

### Non-goals

- Shipping NVIDIA binaries in any form. Whether a later revision could publish the passthrough image is an open question.
- Per-tenant licenses. One hoster token serves every tenant.
- Existing Talos GPU pools are left as they are; this proposal does not extend them.
- MIG.
- Graphics in either mode: Q, B and A vGPU profiles, and graphics passthrough, which is licensed. Only compute is in scope.

## Design

### 1. A build recipe in Cozystack, without binaries

A new directory, `packages/apps/kubernetes-nodes/images/kairos-gpu-worker`, holds a Dockerfile, and the chart's Makefile gets a target, `image-kairos-gpu-worker-<mode>-<minor>`, where the mode is `vgpu` or `passthrough`. The Dockerfile builds the platform worker OS stage from the same sources and pins, so kubelet, kubeadm, containerd and Hadron have the same versions as the platform disk of that minor. It then adds a layer with:

- the guest kernel modules and the driver userspace from a `.run` passed in through a named build context: the vGPU guest driver for `vgpu`, the data center driver for `passthrough`, with the userspace, `nvidia-persistenced` and `nvidia-smi` in a glibc prefix outside `/usr/local`;
- `nvidia-container-toolkit`, with `nvidia-ctk` in `/usr/bin` and a link at the path the gpu-operator addon expects (section 4);
- units that run `nvidia-modprobe -u -c 0` before `nvidia-persistenced`;
- for `vgpu` only, `nvidia-gridd`, a `tmpfiles.d` entry for `/var/lib/nvidia`, and a unit that starts `nvidia-gridd` after `nvidia-persistenced`;
- a containerd runtime for `nvidia`, with CDI enabled.

The target turns the result into a bootable disk with AuroraBoot, as for the platform disk, and pushes it to a registry the hoster names. CI never runs it, and the default `make image` does not include it. The image's `test` stage runs every check of the platform `test` stage, plus GPU checks that need no GPU: modules match the kernel, the glibc prefix resolves every library of the driver tools, the units are ordered, and containerd names the `nvidia` runtime. The check on real hardware is a documented manual step.

### 2. Hoster setting in platform values

The setting goes under the existing `gpu` key in `packages/core/platform/values.yaml`:

```yaml
gpu:
  kairosGuest:
    # vGPU only. Secret in cozy-system with the key
    # `client_configuration_token.tok`. Only the name travels; the token
    # never enters values.
    licenseSecretName: ""
    # vGPU only. FeatureType for gridd.conf. 1 lets the driver pick the
    # license type from the vGPU type.
    featureType: 1
    # vGPU only. Bump after replacing the token; rolls every Kairos vGPU
    # pool (section 5). Empty is a valid revision until the first rotation.
    licenseRevision: ""
    # Optional registry access per mode. secretName: a Secret in cozy-system
    # with keys accessKeyId and secretKey (user name and password), as CDI
    # registry.secretRef expects; not dockerconfigjson. caConfigMapName: a
    # ConfigMap in cozy-system with the registry's CA.
    registry:
      vgpu:
        secretName: ""
        caConfigMapName: ""
      passthrough:
        secretName: ""
        caConfigMapName: ""
    # One entry per mode and Kubernetes minor the hoster built an image for.
    workerImages: {}
    #  vgpu:
    #    v1.35:
    #      image: registry.example.com/gpu/kairos-gpu-worker-vgpu-v1.35@sha256:...
    #      kubernetesVersion: v1.35.6
    #  passthrough:
    #    v1.35:
    #      image: registry.example.com/gpu/kairos-gpu-worker-passthrough-v1.35@sha256:...
    #      kubernetesVersion: v1.35.6
```

A hoster that offers only one mode fills only that key of `workerImages`. The setting counts as set when `workerImages` has at least one entry.

The host half of vGPU already lives here: `gpu.vgpuManager` names the hoster's private vGPU Manager image, and `vmImport.vddkImage` follows the same rule for another proprietary image. Those two reach their consumers through Package components, which a per-tenant app cannot use. The `kubernetes-nodes` chart needs `kairosGuest` at render time, so the platform forwards it on the `_cluster` channel, never the token itself, as `_cluster.wildcard-secret-name` already does with a controller replicating the Secret. A dedicated CRD would add an API and RBAC for a few values that rarely change.

`featureType` is a plain value, not part of the Secret, because the chart renders `gridd.conf` from it and it is not sensitive. The value NVIDIA documents for a vGPU guest is `1`, with the license type chosen from the vGPU type. `4` appears only in a comment of the `gridd.conf` template. The live test used `4` on a C-series profile. The hoster sets the value. `kubernetesVersion` is required on each entry: the bootstrap provider refuses a join unless kubeadm matches the Machine's version to the patch, and a Cozystack upgrade can move the pinned patch of a minor. With the patch recorded, the chart compares it with its own pin and refuses to render, instead of every new GPU node failing at join.

The images reach workers the way the platform disk does: CDI on the management cluster imports them into each worker's disk in the tenant namespace, through the `registry` source of a `DataVolume`. Registry access is set per mode, because the vGPU guest driver must not reach tenants without a vGPU entitlement. The reconciler of section 3 copies a mode's Secret and ConfigMap into a tenant namespace only for the modes that namespace has a Kairos GPU pool in, for `vgpu` only when the tenant is entitled, and the chart sets the copies of the pool's mode as the source's `secretRef` and `certConfigMap`; CDI requires both in the namespace of the `DataVolume`. Without them the import is anonymous, as for the platform disk today. The images are never pulled from inside a tenant cluster.

### 3. Tenant pools

#### Choosing the worker image by mode

A Kairos pool takes its GPUs from GPUClaim and names its class. It has one `claimSelector` or one `claimTemplate` entry in `gpus`, never both, and both name the class, the selector in `className`. A selector picks claims of that class the tenant holds, and a template makes GPUClaim create one claim per worker VMI and delete it with the VMI. The chart picks the worker image by the mode of the named class. The `maxReplicas` limit by matching claims applies to `claimSelector` pools only.

The mode is a property of the class, and the pool names the class, so the chart reads the mode from it when it renders the machine template, before any claim is attached. A class's `mode` never changes, and a hoster does not reuse a deleted class's name for another mode (GPUClaim, decision 0009), so the render stays right for as long as the pool names that class.

The chart:

1. Refuses raw `name` entries on a Kairos pool, `claimSelector` together with `claimTemplate`, and a class name that names no `GPUClass`, with a message naming it.
2. Looks up `workerImages` for the pool's mode and minor. A missing entry or an empty field fails the render with a message naming the mode and the minor. For a `vgpu` pool, a `featureType` that is not a positive integer or an empty `licenseSecretName` fails it too. A `kubernetesVersion` that differs from the chart's pin fails the render with both versions in the message.
3. Uses that image instead of the platform disk. `kairos.image` on a GPU pool is refused.
4. For a `vgpu` pool, renders `/etc/nvidia/gridd.conf` with `FeatureType` into the `KairosConfigTemplate` files, and adds an entry for `/etc/nvidia/ClientConfigToken/client_configuration_token.tok` whose content comes from the license copy, as a `contentFrom` reference or filled in by the reconcile Job (below). The chart never sees the token.
5. Labels the nodes `gpu=on` for HAMi, in both modes.

#### vGPU entitlement

vGPU is a yes-or-no grant that a tenant gets from its parent. It is the Tenant flag `gpu.vgpu`, as the Quotas section of the GPUClaim proposal defines. The parent's administrator sets it, or the hoster on a top-level tenant, and it holds only when it also holds on the parent. The root tenant takes it from platform values. The tenant chart stamps the effective value on the namespace as the label `namespace.cozystack.io/vgpu`. Class budgets only count claims and grant nothing.

The reconciler reads that label and copies the license token and the vGPU registry access only into namespaces where it is `"true"`. Any other value, or no label, means not entitled. A `vgpu` pool without a copy never gets its template applied (see the gate below), and the reconciler reports an Event. Passthrough needs no entitlement.

Clearing the flag revokes the entitlement. The label stops being `"true"` as each level re-renders, and the reconciler then removes the namespace's license and vGPU registry copies, so new templates of its `vgpu` pools wait. New machines of a pool whose template is already applied stop on the `contentFrom` path, because they cannot resolve the removed copy. On the interim path the token is inline in the applied template, so scale-ups and MachineHealthCheck replacements keep getting it while the pool names a vGPU class, unless a vGPU registry copy is required for their disk import. Running vGPU nodes keep the token on disk until they are replaced. To cut a tenant off at once, the hoster also revokes or rotates the token on the license server (section 5).

#### Copies of the license and the registry access

A reconciler copies the license Secret from `cozy-system` into each namespace that has a Kairos vGPU pool, one whose class's `mode` is `vgpu`, and an entitlement. It lives in `cozystack-controller` and is built like `internal/controller/wildcardsecret`. A copy is created or refreshed only from a source that holds a non-empty token, and refreshed only when its revision differs from the current `licenseRevision`; a new copy takes the current token. Deleting the source changes no existing copy. The copy is not exposed through `tenantsecrets` and is removed when the namespace has no vGPU pool left or loses its entitlement. Removing the platform setting does not remove copies, because existing pools may still need them. The registry Secret and CA ConfigMap of a mode are copied under a fixed name per mode, under the same ownership rules, but follow their source without a revision bump, because only the disk import reads them.

A vGPU pool's template is applied only once the copy holds a non-empty token at the current revision, so no vGPU machine is created without a token. Whether the license server accepts the token is not checked. Only a copy the reconciler owns counts, identified by its managed-by label and revision annotation; any other Secret with the same name is ignored. The gate has no time limit, so it cannot be the current reconcile Job, which gives up after a bounded number of retries; the reconciler applies the template, or re-runs the Job, once the copy is ready.

How the token gets from the copy into `files` depends on the provider. At b19dd350e84a its `File` takes only inline `content`, with no `contentFrom`, and the only Secret references in the spec whose content the bootstrap controller puts into bootstrap data are the join tokens and `userPasswordSecretRef`. Two ways forward:

- **Upstream (preferred):** add `contentFrom.secret` to `File`, the same shape as the files of Cluster API's `KubeadmConfig`. The chart writes a reference, and the token never appears in a `KairosConfigTemplate` or `KairosConfig` spec.
- **Interim:** the `kairos-reconcile` Job, which already fills runtime addresses into the template, also fills in the token from the copy. The token stays out of the ConfigMap and out of the chart's hash.

Either way the token ends up in the bootstrap data of each vGPU machine, like the join token already stored there.

### 4. Tenant gpu-operator addon

The gpu-operator default of the tenant `kubernetes` app already expects the driver on the host, with `nvidia-ctk` at `/usr/local/bin/nvidia-ctk`. Both Kairos GPU images are built to that contract.

On Kairos the persistent partition is mounted on `/usr/local` and hides anything the image puts there, so the images keep `nvidia-ctk` in `/usr/bin` and link it into `/usr/local/bin` at boot. If that link and the driver lookup work, the default needs no change. Whether the validator and `nvidia-ctk cdi generate` find driver libraries in a glibc prefix is not verified; if they do not, the images add the prefix to their search paths, or the addon gets an override for Kairos. Whether HAMi works on Kairos GPU pools is not verified either.

### 5. Token rotation

Since bootstrap files cannot be changed on a running node, rotating the token means replacing every vGPU machine. The hoster updates the Secret in `cozy-system`, then bumps `licenseRevision`. The revision is part of the `KairosConfigTemplate` name of every vGPU pool, so every entitled vGPU pool rolls, and each new machine boots with the token of the current revision. If the revision is bumped while the source is missing or empty, the copies keep their old revision, the new templates wait, and the rollout stalls without removing existing machines; fixing the Secret lets it continue. With no spare claims, a pool rolls one worker at a time, and surge machines are created but their VMIs are refused until an old worker frees a claim (see the Attaching section of the GPUClaim proposal).

The old token keeps working until the hoster revokes it on the license server, if the server supports that. Nodes not yet replaced still use it, so revoke it only after every vGPU pool has finished rolling.

## User-facing changes

Hosters get `gpu.kairosGuest`, a build target with a mode, and documentation for building the images and rotating the token. To offer vGPU, the hoster sets the GPUClaim `gpu.vgpu` flag on the root and top-level tenants, and each parent's administrator sets it on its children. Tenants attach GPUs to a Kairos pool with `claimSelector` or `claimTemplate`, which name the class.

## Upgrade and rollback compatibility

- Without `gpu.kairosGuest`, or with no `workerImages` entries, nothing changes.
- A Cozystack upgrade that moves a minor's pinned patch, or a tenant moving a GPU pool to another minor, fails the pool's render until the hoster has a matching entry for its mode. The failed render changes nothing in the cluster: scale-ups and MachineHealthCheck replacements keep using the previous image and version. The old chart refuses the new patch's entry, so it cannot be staged ahead of the upgrade.
- Changing an image rolls every GPU pool of its mode and minor, across all tenants. Changing `featureType` or `licenseRevision` rolls every entitled vGPU pool. Setting or clearing a mode's registry access rolls every GPU pool of that mode, because the disk source is part of the machine template.
- Other changes to the platform worker image reach GPU pools only when the hoster rebuilds, so the hoster documentation says to rebuild for every Cozystack release.
- Removing `gpu.kairosGuest` makes every GPU pool fail to render the same way; the copies stay.
- Rolling back GPUClaim removes `claimSelector` and `claimTemplate` entries from Kairos pools. This happens in one phase, before the `gpu.vgpu` flag and the `namespace.cozystack.io/vgpu` label go. The pools keep running without GPUs on the platform image. With entries of a vGPU class left in place, the pools' new templates would wait for a license copy once the label is gone.
- Rolling Cozystack back to a release without this feature makes Kairos GPU pools hit the existing `gpus` refusal. The copies stay behind with no reconciler and have to be removed by hand.

## Security

The vGPU license client runs inside the guest by NVIDIA's design, so the token has to be present on every vGPU node. A tenant who is cluster-admin of their own cluster can read it with a privileged pod and a hostPath mount. It also persists on the node disk, in the saved userdata on the OEM partition. Passthrough nodes carry no token.

A client that holds the token can lease licenses from the hoster's license server. The token does not administer the server and cannot change the license pool. Limiting misuse is a job for the license server (DLS or CLS). Which restrictions each supports is not verified here.

In the management cluster the token sits in the source Secret in `cozy-system`, in the copy in each entitled namespace with a vGPU pool, in the bootstrap data Secret of each vGPU machine, and in the `-userdata` Secret the KubeVirt infrastructure provider makes from it for the VM, which also lands in the VM's config drive on the node that runs it. On the interim path it is also inline in the pool's `KairosConfigTemplate` and in the `KairosConfig` of each machine; old templates are owned by the tenant control plane and not pruned, so rotated tokens stay there until the tenant cluster is deleted. Tenant roles grant no direct read access to Secrets or Cluster API objects in the namespace on the management cluster. A tenant super-admin can still create a VM that mounts any Secret of its namespace, and so read the license copy and the bootstrap data; for an entitled tenant this exposes no token its cluster-admin cannot already read on a node, and a tenant without an entitlement gets no copy. The reconcile Job gets read access to the copy on both paths, which its role does not have today.

The vGPU entitlement is a boundary set from above through the `gpu.vgpu` flag; a tenant cannot grant itself one, since its Tenant object lives in its parent's namespace. The registry Secret copy gives a tenant super-admin the hoster's registry credentials for the modes it is allowed. When `registry.vgpu.secretName` is set, a tenant without an entitlement cannot pull the vGPU image, which carries a driver licensed per customer; without that Secret the vGPU image is pulled anonymously and its protection is only the registry's network reach, so a hoster who offers vGPU should require credentials for it. A tenant that loses its entitlement keeps any credentials it already read, so cutting its access to the vGPU image takes rotating them on the registry. Naming the same Secret for both modes gives up that boundary. The hoster should issue read-only credentials limited to the worker images of each mode.

## Failure and edge cases

- Any refusal of section 3 → the pool fails to render and the cluster is not changed.
- A `vgpu` pool in a namespace whose `namespace.cozystack.io/vgpu` label is not `"true"` → its template is not applied, and the reconciler reports an Event.
- Entitlement revoked while the tenant has vGPU pools → as described under vGPU entitlement.
- License Secret missing or without a token, in a namespace with no copy yet → that tenant's new vGPU pool waits until the Secret is fixed.
- Revision bumped before the Secret was updated → every vGPU pool rolls onto the old token. The copies already carry that revision, so fixing the Secret alone changes nothing: the hoster bumps the revision again and revokes the old token only after that second rollout has finished.
- Hoster image not pullable from the management cluster, or wrong registry credentials → CDI keeps retrying the import, the new machine never boots, and MachineHealthCheck replaces it where the pool's `maxUnhealthy` allows. On a scale-up, a replacement or a new pool, running workers are not affected. On a rollout the pool still removes one old worker under `maxUnavailable: 1`, the only one in a one-worker pool.
- Registry Secret or CA ConfigMap missing, or the Secret in dockerconfigjson format → the importer pod does not start (`CreateContainerConfigError` for the Secret), with the same effect on the pool, and starts by itself once the hoster fixes it.
- Token rejected by the license server → the node joins, `nvidia-gridd` logs the failure, and the GPU runs unlicensed, under whatever restrictions NVIDIA applies. Cozystack does not detect this.
- A tenant Secret or ConfigMap already uses a copy's name → the reconciler leaves it alone and reports an Event. For the license copy that tenant's vGPU pools wait. For a registry copy the disk import uses the tenant's own object, which affects only that tenant's pools.

## Testing

- **Chart unit tests:** every refusal in section 3, including `claimSelector` together with `claimTemplate` and a class that does not exist; the image of each mode; both license files on a configured `vgpu` pool and none on a `passthrough` pool; a revision change renaming the template of a `vgpu` pool only; a Kairos pool without `gpus` rendering exactly as before; `secretRef` and `certConfigMap` on the disk source only when the hoster set them for the pool's mode.
- **Reconciler unit tests:** license copy and vGPU registry copies only in namespaces with the `namespace.cozystack.io/vgpu` label set to `"true"` and a vGPU pool, none when the label is missing or has any other value, whatever the class budgets, removal when it stops being `"true"`; passthrough registry copies in namespaces with a passthrough pool; refresh only on a revision change; keep copies when the source is missing or the setting is removed; a same-named Secret without the managed-by label and revision annotation ignored.
- **Reconcile Job:** bats tests like `hack/kairos-reconcile-script_test.bats` for token substitution and the revision gate.
- **Image:** the `test` stage from section 1 for both modes, run by the hoster at build time.
- **Manual, on GPU hardware:** for vGPU, the pool joins on first boot, `nvidia-smi -q` shows `Licensed`, and a CUDA pod scheduled through the gpu-operator addon passes. For passthrough, the pool joins, `nvidia-smi` sees the whole GPU, and the same CUDA pod passes with no license. Cozystack CI cannot run this.

## Rollout

This proposal is implemented after GPUClaim: its API, the `gpu.vgpu` flag, and `claimSelector` and `claimTemplate` in `kubernetes-nodes` (its Rollout steps 1 and 2) are prerequisites, and this proposal is the Kairos part of its step 3.

1. Cluster API v1.13.7 (cozystack/cozystack#4902), Kairos pools in `kubernetes-nodes` (cozystack/cozystack#4911), and GPUClaim Rollout steps 1 and 2 land.
2. Build recipe, image test, and documentation land. Hosters can build images, and nothing consumes them yet.
3. `gpu.kairosGuest`, the Secret reconciler, and chart support land, on the interim token path unless the provider has released `contentFrom` by then.
4. When the provider ships `contentFrom`, the chart switches to it.

## Open questions

- **Secret-backed `files` in the provider.** Not supported at b19dd350e84a. Does kairos-io accept `contentFrom.secret` on `File`, or should the Cozystack-side path stay?
- **Where the hoster setting lives.** This proposal argues for `gpu.kairosGuest` in platform values. Reviewers may prefer another key or a separate package value.
- **Token rotation trigger.** Is a manual `licenseRevision` bump acceptable, or should the controller derive the revision from a digest of the token, so a forgotten bump or a wrong order cannot happen?
- **Data center driver on Hadron.** The passthrough image is untested. Does the data center driver need the same fixes as the vGPU guest driver?
- **A published passthrough image.** Whether the license of the data center driver would let a later revision build and publish the passthrough image is not checked. If it does, hosters would need to build only the vGPU image.
- **Driver root with a glibc prefix.** Does the existing gpu-operator default find the driver in the prefix and `nvidia-ctk` through the boot-time link, or does Kairos need an override? Does HAMi work on these pools?
- **License server restrictions.** Which misuse limits DLS and CLS actually offer.
- **Unlicensed state.** Should a node whose license client failed be tainted or reported?

## Alternatives considered

- **Cozystack ships the guest driver.** Not allowed for vGPU: the vGPU guest driver is licensed per customer and may not be published. This proposal does not ship the data center driver either; whether a later revision could is an open question.
- **One image with the vGPU guest driver for both modes.** NVIDIA's vGPU software guest driver can run on a passthrough GPU, but then it is licensed as a physical GPU, with `FeatureType` 0 or 2. Compute passthrough with the data center driver needs no license, so one image would add a license and a token where none is needed.
- **Raw `gpus` entries by resource name on Kairos pools.** A raw name does not carry its mode and reserves nothing. GPUClaim classes carry the mode, and the `gpu.vgpu` flag grants vGPU explicitly.
- **Driver container in the tenant cluster.** The gpu-operator driver container would put the guest driver into an image every tenant cluster pulls, so each tenant cluster, where the tenant is cluster-admin, would need pull access to the hoster's registry. With a node image only the management cluster pulls it. Running that container on musl Hadron has not been tried.
- **Token in a ConfigMap or values.** Puts license material into non-Secret objects (a ConfigMap, or the platform Package that holds the values) and into every Helm release revision, where it outlives a rotation.
- **`pullMethod: node` for the worker image.** The kubelet would pull the image, but the credentials would still have to reach every tenant namespace with a GPU pool as a pull Secret, unless every management cluster node got node-level credentials, in the container runtime configuration or through a kubelet credential provider, and Cozystack manages neither.
- **Rewriting the token on running nodes.** Kairos does not re-read a changed datasource on reboot, so this would need an agent on every node with write access to the OEM copy. Replacing machines uses the rollout Cluster API already provides.
- **Talos with a custom extension.** There is no vGPU guest driver extension for Talos, and writing one means maintaining an out-of-tree extension per driver release.
