# Kairos worker pools and WorkerClass for managed Kubernetes

- **Title:** `Kairos worker pools with a WorkerClass baseline and tenant OS settings`
- **Author(s):** `@lexfrei`

## Overview

I propose adding Kairos as an opt-in worker OS for managed tenant Kubernetes. A Kairos worker boots an immutable disk and joins the tenant control plane through the Kairos Cluster API bootstrap provider using kubeadm. Talos remains supported, including every existing classless pool.

A new cluster-scoped `WorkerClass` in `cozystack.io` describes the bootstrap family and an OS/image baseline. It ships with managed Kubernetes in a system package of its own, including its CRD, controller and admission webhooks. Installing a platform without managed Kubernetes does not require these components. Core Cozystack API code does not gain KubernetesNodes-specific defaulting or validation.

The class does not turn every existing tenant choice into a hoster-only setting. A Talos pool can override the baseline with the Talos settings the current path actually supports. An override cannot change the bootstrap family or contradict an image's fixed compatibility contract. OS-specific fields stay explicitly OS-specific; a field that loads a Talos module does not become a Kairos module installer by sharing a name.

Existing pools without a class keep their legacy Talos path and render unchanged. New `KubernetesNodes` applications created through the Cozystack API get an explicit class at creation. Migrating existing Talos pools onto classes remains a required subsequent stage, with no-rollout proof and rollback, but is not a dependency of shipping Kairos.

## Scope and related proposals

- `kubernetes-nodes-split` remains the pool model: one `KubernetesNodes` HelmRelease per pool and a separate `Kubernetes` control plane.
- Pool labels and taints follow [community#103](https://github.com/cozystack/community/pull/103), decision 0001 of `kubernetes-nodes-split`; this proposal does not change their ownership, admission rules or propagation paths.
- [community#99](https://github.com/cozystack/community/pull/99) owns GPUClass, GPU mode and licensing, including the `GPULICENSE` guest contract. The Kairos GPU image work described by [#101](https://github.com/cozystack/community/pull/101) is included here. No GPU mode, license or credential field is added to WorkerClass.
- A shared golden-image catalog for VMs and workers, a shared VM template, and an OS-neutral join contract remain separate work. Private worker images wait for that catalog rather than adding a second pull-credential convention here.
- Implementation work includes cozystack/cozystack#4902 (Cluster API), #4911 (Kairos pools), #4927 (labels/taints) and #4949 (scale from zero). Their current code is not the specification of the changes required below.

## Context

A worker pool currently carries its Talos version, schematic, module-loading settings, registry mirrors, Image Factory endpoint/CA, installer repository and substrate image source. The tenant can set those fields on standalone `KubernetesNodes` applications. KubeVirt workers import a Talos factory disk or clone a catalog golden; Proxmox workers clone a pre-existing VM template. Kernel modules must already be in the image, and loading them is a separate operation from installing an extension.

The Kubernetes `version` field chooses a supported minor. The chart's version map selects its exact patch. There is no arbitrary Kubernetes patch selector on the pool today.

The reason for a second OS is narrow: a hoster can add a driver or kernel module to a Kairos image with a layer built from the platform OS image. The image still needs a compatible kernel and userspace, and this does not prove that a particular driver works. Talos remains the simpler option where its published extensions meet the requirement.

## Goals

- Ship opt-in Kairos pools without changing the render of existing classless Talos pools or requiring a platform-wide migration first.
- Keep class machinery with managed Kubernetes rather than adding app-specific behavior to core.
- Preserve supported tenant Talos choices and separate bootstrap selection, image compatibility and tenant reachability settings.
- Make an invalid class, OS-specific override or fixed-image conflict fail deterministically rather than being silently ignored.
- Keep pool scaling, health checks, labels, taints and VM sizing independent of the bootstrap family where the existing implementations provide that behavior.
- Deliver a later, separate migration of existing Talos settings onto the class-based path, with no-rollout evidence and a supported rollback.

### Non-goals

No mutable in-place Kairos worker upgrades, no Kairos Proxmox image in the initial release, no general cloud-init input, no custom bootstrap family, no arbitrary Kubernetes patch override and no second catalog for private worker images. Moving all existing pools is not part of the initial Kairos upgrade.

## Design

### 1. WorkerClass and its package

`WorkerClass` is cluster-scoped in `cozystack.io/v1alpha1`. Its `spec` is immutable as a whole, enforced by CEL (`self == oldSelf`). A hoster publishes another class for another fixed image instead of editing an object used by several tenants. The kind describes how one worker boots and joins; replicas, scaling, health checks and workload placement remain on the pool.

The managed-Kubernetes system package owns the CRD, finalizer controller, MutatingWebhookConfiguration, ValidatingWebhookConfiguration, webhook Service, credentials and RBAC. The controller and webhooks are not components of `cozystack-controller`. The package installs the CRD before its own class objects and is an explicit dependency of the class-using managed-Kubernetes packages. A legacy-only classless pool does not acquire a new class lookup dependency in its render path.

The package ships two built-in names, `talos` and `kairos`. They resolve to the platform's baseline for that family without reading the object during rendering, so deleting an informational built-in class object does not break an existing pool. Admission refuses built-in objects with a conflicting spec. An absent or deleting custom class is not silently replaced by a built-in class.

```yaml
apiVersion: cozystack.io/v1alpha1
kind: WorkerClass
metadata:
  name: kairos-example-v1.35.6-r1
spec:
  bootstrap: kairos
  image:
    kubernetesVersion: v1.35.6
    reference: registry.example.com/workers/kairos-worker@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
---
apiVersion: cozystack.io/v1alpha1
kind: WorkerClass
metadata:
  name: talos-example-r1
spec:
  bootstrap: talos
  talos:
    version: v1.13.6
    schematicID: ce4c980550dd2ab1b17bbf2b08801c7eb59418eafe8f279833297925d67c7515
```

A pool selects a WorkerClass through `workerClassName`. `bootstrap` initially accepts `talos` and `kairos`. `image` describes a fixed Kairos container disk and its exact Kubernetes version; both fields are required together. Its reference must carry a sha256 digest and be publicly pullable. Talos class settings use the current Talos field shapes, with source entries per substrate. A class baseline is not a new registry credential or a grant to read a hoster's Secret.

A Talos baseline distinguishes a factory source, whose version/schematic can be resolved from supported pool overrides, from a fixed source whose declared version/schematic must match the chosen artifact. A golden catalog entry or Proxmox template still has to exist. A compatibility declaration describes the image; it does not verify the bytes of an external Proxmox template.

**Class deletion.** The package controller adds a finalizer to custom classes, including any classes created by the later migration. A referenced class remains `Terminating` with its immutable spec until the last pool moves off or is deleted. References include pools represented by HelmReleases across namespaces, not only objects submitted through the Cozystack API. The controller removes the finalizer when no pool names the class. A VWC refuses a new reference to a class being deleted; this covers requests through the Cozystack API, not direct HelmRelease writes. Existing references continue rendering from the still-present class. The two built-in objects need no finalizer because their render does not read them.

The CRD carries `helm.sh/resource-policy: keep` so removing a package version that does not render the CRD cannot cascade-delete custom classes behind their finalizers. Class deletion before the controller first adds its finalizer, or a race between class deletion and pool admission, can still leave a missing-class reference; the chart fails closed in that case. A finalizer is not a cross-object transaction.

### 2. Creation, updates and admission

The initial platform default is `talos`, chosen by a platform value delivered to the package. Changing it affects later creates, not stored pools. A class annotation is not another default-selection mechanism in this design.

| Request through the KubernetesNodes API | Class behavior |
| --- | --- |
| CREATE without a class | MWC writes the current platform default before validation. |
| CREATE with an explicit class | MWC preserves it; VWC validates it. |
| UPDATE of a classless pool, field omitted or empty | It remains legacy; the current default is not applied. |
| UPDATE of a classified pool, field omitted or empty | MWC restores the stored class, including on a full replace. Empty is not an implicit rollback to the legacy path. |
| UPDATE selecting another class | Validated against the target class, OS-specific overrides and existing bootstrap family. |

MWC and VWC are resources shipped by the worker package and match `kubernetesnodes.apps.cozystack.io`. The VWC GETs a selected custom class for existence, deletion state and compatibility checks; its RBAC grants the access needed for that check. VAP may enforce conditions involving object, oldObject or fixed parameters, but this design does not promise arbitrary class lookup from CEL. Core exposes its generic admission chain; it does not embed worker-specific checks.

The chart independently validates the resolved configuration. Admission does not protect direct HelmRelease writes. A direct producer that wants class semantics writes an explicit class and is responsible for its create-time selection; omission retains legacy behavior. This is not permission to add class lookups or default injection to core.

Legacy rendering without a class lookup is not an admission-availability guarantee. A classless UPDATE that matches an unavailable webhook configured with `failurePolicy: Fail` can be refused before the handler decides that no class GET is needed. Already-rendered workers and classless chart reconciliation do not require that handler, but an application API write may. The package must document which CREATE/UPDATE requests match each webhook, which are excluded, and the observed failure result for each path. It must not promise an outage exemption for legacy updates unless that exemption is configured and tested. The final admission evidence must distinguish transport failure from a healthy handler rejecting a request.

An isolated integration exercised the real kube-aggregator → Cozystack API → host HelmRelease storage path using package-style webhook registrations and research-only CRD, webhook and controller fixtures. The measured wire field was `nodeGroupClassName`, optional and without a schema default, not the final `workerClassName` schema. CREATE mutation was persisted. PUT, JSON PATCH, merge PATCH and SSA preserved legacy absence on omission and the stored class on a classified pool; with an explicitly empty input, those operations restored the classified pool's old class but stored an empty string on a legacy pool. The validating webhook GET rejected new references to a deleting class while allowing an existing pool to stay on it, and real finalizer storage held the class until its last pool reference was removed. A host-backed VAP enforced an object-local rule with a fixed class parameter; arbitrary object-derived lookup was not provided.

An actual webhook HTTP 503 caused matched requests to be refused with `failurePolicy: Fail`. A classless legacy PUT returned HTTP 500: the registrations matched CREATE/UPDATE with Exact matching, empty selectors and no matchConditions, so the handler's legacy no-op was not an availability bypass. The separate Ignore case allowed a dry-run CREATE despite HTTP 503; it did not establish fail-open behavior for other operations or a recommended package policy.

The measured host was Kubernetes 1.33.0; the Cozystack API used apiserver library 0.37.1. These are different versions, and the result is not coverage of other hosts. The fixtures do not establish a shipped WorkerClass package, the complete image/substrate compatibility contract, production controller watches/indexing/HA, tenant/denied RBAC, Service DNS/network routing, or aggregation-backend TLS verification. The outage test is HTTP 503, not TCP/TLS/timeout coverage. Production installation, certificate/readiness wiring and those authorization/availability checks remain delivery requirements. Separate generic main storage failures with stale resourceVersion and a concurrent spec write during admission were reproduced, not fixed or passed; their corrections are tracked in cozystack/cozystack#5234 rather than a worker-specific core hook.

Schema defaults cannot stand in for this mutation. They are static, and Get already applies schema defaults; patch/apply and read-modify-write updates can then persist them. A schema default for the class field would make legacy pools acquire a class merely by being read and updated. The class field therefore has no OpenAPI, kubebuilder or chart-values default. A chart helper must distinguish the legacy empty field from an explicit `talos`, not apply `default "talos"` and lose that distinction.

### 3. Class baseline and tenant settings

For a classified pool, resolution is class baseline followed by supported tenant overrides, then compatibility validation. Explicit presence matters: an unset module list differs from `[]`. Overrides never select a different bootstrap family.

The Talos resolution order is deterministic. First resolve effective `talos.*` from the class baseline and the explicitly supplied pool fields. A supplied scalar replaces its baseline value; an explicit empty value is not silently replaced with the baseline. The `registryMirrors` map is one setting: a supplied map replaces the baseline map, and `{}` clears it rather than merging its entries. For `kernelModules`, a supplied list replaces the baseline list, `[]` disables loading, and the existing automatic NVIDIA rule runs only when neither baseline nor pool supplies a list. Then select and resolve the substrate image source as follows; no image compatibility decision is made before these steps.

| Input | Source selection and precedence | Validation after resolution |
| --- | --- | --- |
| KubeVirt Talos, pool `osImage` absent | Use the class's KubeVirt source if present; otherwise use the default factory path with effective `talos.*`. | Apply current Talos version/schematic, factory URL, support-matrix and builtin golden checks to the resolved source. |
| KubeVirt Talos, pool supplies `osImage` | Replace the class's KubeVirt source selection as a unit, not a deep merge of builtin/factory branches. Explicit `{}` selects the current default factory path. Both builtin and factory together are rejected, as today. | A fixed-artifact class still requires the final selected artifact and its declared Talos version/schematic to match its contract; supplying another source does not waive that check. |
| `osImage.builtin.version` / `.schematicID` | Use the selected builtin's nonempty values; for omitted or empty values use effective `talos.version` / `.schematicID`, as in the current resolver. | Validate the resulting golden identity, catalog existence, storage compatibility and size. A conflicting fixed artifact is rejected. |
| `osImage.factory.version` / `.schematicID` / `.imageFactoryURL` | Use the selected factory's nonempty values; for omitted or empty values use the corresponding effective `talos.*`, as in the current resolver. | The same resolved version/schematic feed the disk and installer. The independently resolved installer repository and import CA reach their existing consumers. |
| Proxmox Talos | Pool `proxmox.templateTags`, when supplied, replaces the class's list; otherwise use the baseline list. An empty final list fails. `osImage` is not an alternative Proxmox source and is rejected when nonempty. | Match the declared fixed template contract after resolving effective `talos.version` / `.schematicID`. This checks declared configuration, not the bytes inside an external VM template. |
| Kairos | Select the class's fixed `image`, or the platform disk for the pool minor when `image` is absent. Talos source and loader fields do not select or modify that disk. | Enforce matching pool/image minor, exact fixed-image patch and supported substrate; reject unsupported nonempty Talos inputs. |

A fixed artifact means the class identifies particular image bytes or a particular existing golden/template with declared Talos compatibility. After resolving overrides, changing that fixed artifact or contradicting its declared version/schematic is a deterministic error, naming the class and conflicting field. A factory baseline whose source is derived from effective Talos settings is configurable rather than a claim of fixed image bytes. Changing a tenant registry mirror, HTTP-import CA or installer reachability prefix is not by itself a reason to replace an unrelated fixed artifact or weaken hoster credentials. The class schema must make that distinction explicit rather than adding a discretionary flag that silently disables existing tenant controls.

| Pool choice | Legacy Talos | Classified path |
| --- | --- | --- |
| Talos version/schematic | Existing `talos.*` and `osImage` precedence remains unchanged. | Tenant Talos-specific version/schematic overrides remain supported. Factory paths can resolve their image from those values; a contradictory fixed-image contract is rejected. |
| Kernel module loading | Existing names/parameters, automatic NVIDIA list when unset, and explicit `[]` opt-out remain unchanged. | A Talos-specific override of the class's module baseline uses the same existing loader. It does not install missing modules. Nonempty Talos module configuration is not accepted as a Kairos module configuration. |
| Registry mirrors | Existing Talos machine.registries behavior. | Supported tenant Talos mirror configuration remains available. It is not a promise about Kairos containerd. |
| Factory endpoint/CA and installer repository | Existing CDI HTTP source/CA and separate in-guest installer source. | Preserve those tenant reachability inputs for the same consumers. They do not replace hoster registry credentials, grant new Secret access or weaken global trust. A fixed image cannot be repointed by pretending its source is a factory. |
| Talos disk source | Factory or matching catalog golden on KubeVirt; existing template tags on Proxmox. | Class supplies a substrate baseline; supported tenant Talos source choices retain the existing field semantics where they do not contradict a fixed image contract. No OS-neutral source override is added. |
| Kubernetes version | Tenant minor, platform-selected patch. | Tenant minor remains. Platform images use the version map; a fixed Kairos image supplies the exact patch it contains. No arbitrary patch field. |
| SSH keys | Talos has no SSH server. | Nonempty `sshKeys` on Talos is rejected; empty is allowed. Kairos currently accepts at most one key. |
| GPUs | Existing available resource selection and Talos module-loading behavior. | GPU attachment remains a pool choice. Driver availability is not inferred from attachment; vGPU/Talos is the known family-based exception. |

The existing Talos field names are retained as explicitly Talos-specific inputs during the first stage; class adoption does not rename registry trust into a generic OS knob. A Talos module or image override that violates the selected fixed source fails with the field and class named. On Kairos, unsupported nonempty Talos settings are rejected, not silently ignored. Remove the blanket Talos schema/chart-values defaults that would manufacture such settings for a Kairos pool. Resolve family-specific defaults only after choosing the path: the legacy chart helper fills the same Talos defaults as before, the classified Talos path uses its baseline, and the Kairos path gets neither. API reads may show fewer schema-injected fields, but read/update cycles must preserve each pool's effective configuration and legacy render.

For KubeVirt factory/builtin paths, resolved Talos version and schematic must feed both the boot disk and the in-guest installer. `imageFactoryCA` configures CDI's HTTPS trust, while `registryMirrors` configures guest pulls, and `installerRepository` is a third consumer. Proxmox still uses its declared template tags, which must match the intended Talos image. A module list only loads modules present in that image. No part of this proposal promises those operations have the same implementation on Kairos.

A pool on a fixed Kairos image must choose the image's minor. Its Machine version, bootstrap version and image are resolved together from the class; a platform patch change does not alter those fields. A class without its own image uses the platform disk and exact patch for the pool's minor. The parent control-plane minor is upgraded first, as today.

### 4. Legacy pools, classified pools and ComputePlane

A new explicit Kairos pool and a Talos pool using a class baseline with a supported tenant override look like this. Omitting `workerClassName` from a new API CREATE selects the platform default; it is not a way to create another legacy pool through that API.

```yaml
apiVersion: apps.cozystack.io/v1alpha1
kind: KubernetesNodes
metadata:
  name: example-kairos
spec:
  cluster: example
  workerClassName: kairos-example-v1.35.6-r1
  version: v1.35
  minReplicas: 2
  maxReplicas: 3
---
apiVersion: apps.cozystack.io/v1alpha1
kind: KubernetesNodes
metadata:
  name: example-talos
spec:
  cluster: example
  workerClassName: talos-example-r1
  version: v1.35
  talos:
    version: v1.13.6
  kernelModules:
    - name: nvme_tcp
```

The Talos image must carry `nvme_tcp`; the pool requests loading it, not installing it. These snippets show the class/override contract, not a claim that every omitted infrastructure or image prerequisite exists in a particular cluster.

A pool without a class uses the original Talos rendering path, with its existing version map, defaults, image-source resolution, module behavior and tenant controls. No class object is looked up. An upgrade that only adds this feature must leave the manifests and template hashes of such a pool unchanged. The original path is preserved, not a newly invented default class hidden behind an empty name.

A classified Talos pool resolves its baseline/overrides before entering the same Talos image and machine-config consumers. A classified Kairos pool enters the Kairos bootstrap/image path. Switching an existing running pool across bootstrap families is refused in the first release: create a second pool, move workloads and remove the first one. A classless Talos pool may adopt an explicitly selected Talos class, but that edit must be checked against the same render-equivalence requirement as the later migration; it is not a shortcut for switching a running pool to Kairos.

ComputePlane is currently a module-owned cluster, not the standalone KubernetesNodes contract. Its allowlist deliberately excludes worker OS/image and Kubernetes-version selection, and passes only pool shape and supported workload settings. The module creates child HelmReleases directly, so the mutating webhook on the aggregated application API cannot default them.

For the first stage, ComputePlane retains that existing Talos contract and emits classless legacy pools through its current allowlist. It does not write a fixed WorkerClass into every group. General worker admission permits supported tenant choices on standalone pools; the current ComputePlane `nodeGroups` contract does not expose worker class choice. A future explicit extension of that module contract can expose a validated class choice and its version policy; it must update both its documented fields and its allowlist. This scope keeps existing ComputePlane behavior unchanged without hard-pinning a class field that would override a later supported choice.

### 5. Substrate and bootstrap

| Family/path | KubeVirt | Proxmox |
| --- | --- | --- |
| Legacy or classified Talos | Factory raw image over HTTP or CDI golden clone; TalosConfigTemplate. | Existing nocloud VM template selected by exact tags; TalosConfigTemplate. |
| Classified Kairos | CDI imports an OCI container disk, EFI firmware; KairosConfigTemplate with `distribution: kubeadm`. | Not supported initially; requires a suitable Proxmox image/template and bootstrap integration. |

The Kairos pool renders the same pool-level MachineDeployment, MachineHealthCheck and WorkloadMonitor shapes as the Talos implementation, with the appropriate infrastructure and bootstrap references. Its reconcile Job waits for the tenant apiserver Service and management DNS Service addresses, then applies a ConfigMap-backed KairosConfigTemplate. Tenant settings are data, not executable script. The Job's `envsubst` handles only the four runtime variables `SVC_IP`, `COREDNS_IP`, `RELEASE` and `NS`. Its get/patch/update permissions are limited to its pool template; RBAC cannot restrict create by resource name.

The tenant apiserver name must resolve during the initial join. The template writes its Service IP into `/etc/hosts`, and the tenant KamajiControlPlane publishes tenant `dnsServiceIPs` for kubelet configuration. The provider supplies providerID and checks the join CA and kubeadm/Machine version consistency. These provider-specific checks do not establish a future custom-bootstrap credential contract.

A bootstrap template is named from its chart inputs. Runtime addresses are excluded from that hash, as on Talos. Kairos consumes bootstrap on first boot, so changing the template requires replacement workers, not an in-place rewrite of running nodes.

### 6. Worker images and upgrades

Cozystack builds a platform Kairos OS image and disk per supported Kubernetes minor. The OS image is published next to the disk so a hoster can build `FROM` it by digest. The platform carries static kubelet and kubeadm at the exact patch in the chart version map, plus containerd, runc, CNI binaries, Kairos init and the immutable Hadron base. AuroraBoot produces the EFI disk; the disk and base image references each have `.tag` files enumerated by the release image tooling.

A fixed custom class names a public disk by digest and its exact Kubernetes version. It cannot receive a private registry Secret from a tenant field. A disk requiring private credentials waits for the shared catalog used by VMs and workers.

At the last recorded validation of #4911, two builds with unchanged image inputs produced different platform disk digests. Because the disk reference is part of the KubeVirt template hash, such a rebuild rolls pools on platform Kairos images. The first experimental release must document that cost. Leaving experimental requires an actual unchanged-input release to preserve the disk digests and the corresponding pool templates; merely pinning the rebuilt result does not prove this.

Kairos workers upgrade by replacement. A custom-image patch upgrade means selecting the class carrying the new image; a minor upgrade changes the pool minor and matching class together after the parent. Changes to class baseline, tenant bootstrap inputs or VM shape can roll workers through their hashed templates. Legacy Talos pools retain their existing rollout behavior.

### 7. Health, autoscaling, labels and taints

MachineHealthCheck parameters remain pool settings. With `maxUnhealthy: 50%`, a singleton's one unhealthy machine exceeds the rounded-down threshold and is not remediated. That mechanism explains this particular status; it is not a diagnosis of every Kairos recovery problem. The cause of the unresolved Kairos recovery failure is **UNKNOWN**. This proposal does not label it fixed or infer a cause from a successful retry.

The test plan includes a two-machine remediation case, a stopped worker and a deliberately unimportable disk. The latter needs an explicit `maxUnhealthy: 100%` because every machine on that class fails. Passing those cases, with evidence of replacement rather than only an unhealthy condition, is required before claiming Kairos remediation support.

The autoscaler scales MachineDeployments using pool annotations. Kairos scale from zero must be tested rather than inferred from the shared infrastructure provider. The default provision timeout is 30 minutes; a Kairos install/reset/reboot adds work that must fit the measured budget.

Labels and taints follow the accepted decision in #103 exactly. `node-restriction.kubernetes.io` and `node.cluster.x-k8s.io` domain labels are propagated by Cluster API from the MachineDeployment template and can change in place. Other allowed labels and all taints go through bootstrap: Talos machine.nodeLabels/registerWithTaints or Kairos kubeadm node-labels/nodeRegistration.taints, and changing them rolls workers. `roles` remains the only role-label input. The shared helper rejects reserved or conflicting inputs and announces the same scheduling metadata through autoscaler capacity annotations. Addons still need appropriate tolerations; selecting a taint does not make a device plugin tolerate it.

### 8. Kairos GPU guests

A pool's `gpus` attaches devices on KubeVirt; it does not install a driver. WorkerClass carries no GPU settings. GPUClass owns GPU mode and licensing. The known exception to image-based capability is the gpudevice refusal of a vGPU class on a Talos worker by bootstrap family; this must resolve the actual pool family, including legacy Talos, not merely test a class name. It is a declared exception, not a proof that any other image supports vGPU.

The hoster builds a GPU image from the platform Kairos OS image. Prior testing of a Hadron guest with the NVIDIA 595 vGPU guest driver, a compute profile, `FeatureType=4` and NVIDIA AI Enterprise licensing ran CUDA. That test required a glibc userspace prefix, `/var/lib/nvidia` from tmpfiles, and creation of `/dev/nvidia-uvm` with `nvidia-modprobe -u -c 0` before `nvidia-persistenced`. It does not prove the passthrough data-center driver or the tenant gpu-operator integration works.

The hoster-only recipe supplies driver binaries through a build context, installs compatible modules/userspace and nvidia-container-toolkit, and configures the NVIDIA containerd/CDI runtime. It keeps nvidia-ctk in `/usr/bin` and links it to `/usr/local/bin` at boot because Kairos mounts its persistent partition over `/usr/local`. It creates device `/dev/char` symlinks because the tenant addon disables their container-side creation. A vGPU image additionally implements gpudevice's `GPULICENSE` disk-label guest contract for nvidia-gridd. Driver binaries are not built or published by platform CI.

The build test checks module/kernel agreement, glibc library resolution, runtime configuration, unit ordering and boot-time links. Hardware tests must show the pool joins, the device works and a CUDA pod runs through the tenant addon. vGPU adds a `Licensed` check and waits for the shared private-image catalog. Whether the gpu-operator validator and CDI tool find the glibc prefix remains a hardware-test question. CUDA Unified Memory's vGPU limitation is not removed by this integration.

### 9. Dependencies and cost

The implementation baseline previously validated is provider v0.2.1, Hadron v0.5.3, kairos-init v4.3.0 and AuroraBoot v0.27.1, with Kubernetes v1.35.6 and Cluster API v1.13.7. These are evidence coordinates, not a claim that every newer release is tested. A Hadron/kernel bump needs validation against the tenant CNI; the prior Hadron kernel failed with older Cilium versions and ran with Cilium 1.20.2 in the recorded test.

| Provider change | Upstream | Status at the recorded validation |
| --- | --- | --- |
| kubeadm worker join | kairos-io/cluster-api-provider-kairos#141 | Released in v0.2.0. |
| kubeadm patches in JoinConfiguration | #148 | Released in v0.2.0. |
| Nonblocking post-bootstrap units, atomic providerID drop-ins | #149 | Released in v0.2.1. |
| k0s nonblocking units / CAPK lb-sans timing | #155 | Open; not required by this kubeadm path. |

The provider code has no downstream patch in that baseline. A manifest adaptation in `packages/system/capi-providers-bootstrap/patches/kairos/` scopes selectors to the Kairos provider label because the upstream controller-manager selector collides in a shared provider namespace (kairos-io/kairos#5815). A manifest adaptation alone does not keep Kairos experimental. A downstream code patch requires an upstream submission, removal when its release is adopted and an entry in the carry list; while such code patches remain, Kairos stays experimental.

The cost is another image pipeline per minor/architecture, a source build of kubelet/kubeadm, per-worker disk imports (about 381 MB for the recorded v1.35 disk), the platform-image rollout cost above, another provider to maintain and a root-capable SSH path where a tenant supplies a key. If upstream stalls, maintaining a pinned fork is possible but does not justify declaring the option mature.

## User-facing changes

New standalone pool CREATE requests receive the platform's explicit default class; a tenant may select an available class. Existing classless pools retain every current Talos field and their legacy render. Classified Talos pools use a baseline plus the supported tenant overrides rather than requiring a new hoster class for each workload's module-loading or reachability needs. Invalid fixed-image combinations fail rather than being silently replaced.

A tenant chooses a Kubernetes minor, not an arbitrary patch. On Kairos, SSH is optional and currently limited to one key; on Talos, nonempty keys are rejected because no server consumes them. Hoster documentation must distinguish publishing an OS image, selecting an existing image source, installing kernel modules in that image and requesting their load in a pool.

## Upgrade and rollback compatibility

### Initial Kairos release

No existing-pool rewrite runs. A classless pool continues through its original Talos path; a schema default or read/update must not classify it accidentally. Install the worker package and establish its admission before exposing class-defaulting application CREATE operations. Class-based pool rendering depends on that package, while the legacy render does not call it. Production certificate/webhook failure behavior must be demonstrated for the installed package, not inferred from the isolated fixture evidence above.

Rollback before the class feature requires removing Kairos pools first; the older chart would interpret a pool as Talos and cannot safely consume Kairos bootstrap data. A migrated-in-place Talos pool is not assumed byte-identical just because both paths are Talos. Before reverting, the hoster restores the effective Talos configuration of any classified Talos pool into the old pool field layout and verifies the old render. The initial release must provide a documented restore tool for classified Talos pools created or modified after adoption; it writes HelmRelease values with hoster authorization rather than inventing tenant access to class administration. Already-classless legacy pools need no restoration.

The kept CRD preserves custom classes on such a revert. Built-in informational class objects may be removed with their owning package and later recreated. A custom class deleted after its finalizer controller is gone remains Terminating until the hoster handles that finalizer. Do not remove the worker package/controller while live classified pools still need it.

Re-upgrade after a rollback is not implemented by replaying a compulsory all-pool migration: the initial feature has no such migration. Any restoration tool's classification fields and their safe reuse must be specified and tested before claiming that a restored pool automatically returns to its earlier class. Existing legacy pools may remain legacy through subsequent upgrades.

### Required later migration

Moving existing Talos configuration onto the class-based path remains a required delivery stage. It must preserve tenant overrides and reachability settings, prove no-rollout conversion using effective installed values, cover every supported substrate, and provide rollback. Moving a value into the baseline is not removal of the corresponding supported tenant override; any cleanup concerns redundant stored baseline copies or superseded storage, not a ban on these field inputs. It must account for old-chart rendering during pre-upgrade hooks, CRD/controller availability, clusters without the platform migration hook and upgrades that skip releases. It must not delete the fields an old chart is still reading.

The generated-class scheme, namespace-UID isolation, field cleanup and any version-stamp replay are not shipped as an untested prerequisite of Kairos. Their design belongs to this subsequent migration stage. No release should advertise completed Talos field migration merely because new pools can already name WorkerClass.

## Security

- Only the hoster administers WorkerClasses and their fixed image baseline. Tenant overrides are limited to supported pool settings; they cannot change the family, gain hoster pull credentials, choose arbitrary internal keys or replace cluster wiring.
- Webhook class GET permission belongs to the package's service account. A pool still renders with deterministic chart checks if a direct HelmRelease writer bypasses application admission; a raw write is not evidence that admission runs on that path.
- Talos mirrors, CDI factory CA and installer source remain separate trust/reachability consumers. A tenant-supplied CA for its disk import does not modify cluster-wide trust or another tenant's imports.
- A Kairos worker gets no Talos machine CA/token. A tenant SSH key authorizes a sudo-capable guest user intentionally; empty keys mean no SSH login through this mechanism, not a claim about every possible out-of-band console.
- A public OCI reference must not deliver a driver that may not be redistributed. Private/licensed disks wait for the shared catalog.

## Failure and edge cases

- Existing classless pool, including after an UPDATE omitting class: original Talos render, no class lookup or default injection.
- Classified UPDATE omits class or sends an empty value: preserve the stored class rather than silently becoming legacy.
- New CREATE omits class: platform default is persisted by MWC; if a selected custom default is missing/deleting, VWC refuses the request rather than selecting another family.
- A matched Fail-webhook is unavailable: application requests can be refused even for legacy classless UPDATEs that need no class lookup. This is distinct from continuing to render an existing classless HelmRelease and must be covered by the admission availability tests.
- Missing custom class or a direct HelmRelease reference to an already-deleted class: render fails and names the class; existing worker objects are not silently converted.
- Deleting a referenced class: it stays Terminating while its pools render from the retained spec. Pre-finalizer deletion/admission races can still produce the missing-class case.
- Unknown bootstrap, incompatible fixed image/version, unsupported substrate, unsupported nonempty OS-specific input or nonempty Talos SSH keys: deterministic rejection with the field/class identified.
- Kairos pool minor differs from custom-image minor, or worker minor leads parent: refused; upgrade parent first and choose a matching image.
- Registry import or join fails: Machine remains without a usable Node; whether it is replaced depends on MHC timing and maxUnhealthy. The unresolved recovery cause is UNKNOWN, not declared fixed here.
- Tenant API Service is re-addressed: runtime-address changes alone do not change the bootstrap hash; rerunning the reconcile mechanism remains OQ7.
- GPU attaches but image has no compatible driver: attachment alone does not make it usable. Talos/vGPU refusal is the explicit bootstrap-family exception.
- Direct HelmRelease creates, including ComputePlane children, do not pass through the KubernetesNodes webhook. Their producer must choose explicit class semantics or intentionally retain legacy semantics.

## Testing

### Render and API contracts

- Golden parity for legacy classless pools on KubeVirt factory, builtin and Proxmox paths, custom/default Talos values, absent/empty module lists and API read/update cycles. The class feature alone changes no legacy manifests or hashes.
- Classified Talos baseline plus tenant overrides reaches the current image/installer, module and registry consumers. A classless-to-classified conversion with equal effective settings preserves render; an intentional configuration change has the existing rollout effect.
- Fixed image conflicts and unsupported family inputs fail with deterministic messages. Test module absent versus empty, partial Talos overrides, factory/builtin source resolution and separate CDI CA versus guest registry consumers.
- MWC/VWC tests through the real aggregated API cover every CREATE/UPDATE row above, absence of schema-driven classification, custom-class GET/refusal, same-family changes, prohibited cross-family changes and preservation of a classified pool's empty input. Include webhook readiness and outage tests for CREATE, classified UPDATE and legacy classless UPDATE separately, recording the matched webhook and failure policy rather than inferring write availability from the lack of a class lookup. Keep the production-package results separate from the isolated mechanism evidence above; unit tests alone do not prove this integration.
- Finalizer tests cover custom/generated classes, last-reference removal, Terminating-class render, no finalizer on built-ins and the documented race outcome. Pin CRD keep through generation and package rendering.
- ComputePlane renders its existing legacy Talos shape with no class set. No undocumented tenant field can escape its allowlist.
- Labels/taints use #103's shared rules and both bootstrap paths; test scheduling metadata and propagation without inventing a new OS-specific API.
- Kairos custom-image patch and template names remain unchanged by an unrelated platform patch bump; platform-image inputs and new published disks change only the intended pools. Image test stages check static binaries, versions, units and kubelet flags.

### End to end and release evidence

The initial upgrade job installs a release before WorkerClass, creates legacy Talos pools with default/custom settings, upgrades, and checks their objects and workers remain unchanged. It then creates explicit/default-class pools through the application API and verifies the stored class, Node readiness, providerID, tenant annotation and a scheduled pod. This does not wait for the later Talos migration stage.

Keep remediation and scale from zero as separate jobs, plus an upgrade job. Existing planning budgets are 243, 216 and 245 minutes respectively against a 255-minute cap introduced by #4911; main's earlier cap was 215. Those estimates mix measured install time with ceilings, not measured runtimes for the new tests, and the API/package checks must be re-budgeted before assigning them to a job. No new waits are assumed to fit merely because they belong to the same proposal.

Test remediation of a stopped worker and an unimportable image with the explicit MHC thresholds, autoscaler 0→1→0, and release-to-release no-roll behavior for unchanged image inputs. Record failures as failures and leave the recovery cause UNKNOWN until a mechanism is demonstrated. Hardware GPU tests remain manual and separately establish guest driver, licensing where applicable and tenant-addon behavior.

The later migration stage adds its own upgrade/no-rollout/rollback and skipped-release tests; it is not marked complete by the initial legacy-preserving upgrade job.

## Rollout

1. Land the required Cluster API/provider prerequisites and preserve the accepted labels/taints contract.
2. Ship the WorkerClass managed-Kubernetes system package with CRD/controller/MWC/VWC, platform default initially `talos`, generation-safe CRD keep, explicit package dependencies and a verified admission path. Core carries no worker-specific hooks. Do not merge an unverified class-defaulting release on the strength of a CEL lookup assumption.
3. Ship opt-in classified Kairos pools and classified Talos baselines/overrides, while retaining legacy no-class pools and the unchanged ComputePlane boundary. Remove schema/chart defaults that erase the legacy/class distinction, and do not enable a compulsory platform migration in non-ISP variants as a side effect. This is the initial experimental Kairos release; it does not wait for OQ16's later-migration design.
4. Deliver the mandatory existing-Talos migration as a separate stage, with the no-rollout proof, supported tenant configuration, per-substrate coverage and rollback described above. Settle OQ12–16 where they apply to that stage before it ships; these are not Kairos release dependencies.
5. Deliver the hoster GPU-image recipe/test stage/documentation after the relevant gpudevice integration. Kairos vGPU additionally waits for the shared private-image catalog and the license guest contract.
6. Remove experimental only after no downstream provider code patch remains, an unchanged-input release preserves platform disk digests/templates, and remediation, autoscaling and upgrade tests pass across two Cozystack releases. A successful retry without a recovery diagnosis is not a fix.

## Open questions

The numbers below preserve the previous proposal's question identifiers; resolved entries stay in place.

1. Small-pool MachineHealthCheck defaults: change the threshold or document that 50% does not remediate a singleton?
2. Resolved: custom WorkerClass deletion is held back by the package controller's finalizer; built-ins are unaffected, with residual races named in section 1.
3. Tenant class discovery: how to list available classes without exposing sensitive class content to every authenticated tenant? Tenant RoleBindings do not grant cluster-scoped list access.
4. Resolved: a platform value chooses the default, initially `talos`; MWC writes it only at CREATE, preserving the UPDATE cases in section 2.
5. In-place bootstrap-family switch: retain new-pool replacement or later validate a rolling switch? Initial release refuses it.
6. Hadron cadence and responsibility for kernel/base CVEs.
7. Re-addressed tenant apiserver Service: what triggers bootstrap-template refresh on both families without a pool Helm upgrade?
8. Future join contract/custom bootstrap: publish per-cluster join inputs, including a reusable credential rotatable per tenant cluster, and provide a tenant-writable place for tenant cloud-init without making hoster-only classes tenant-writable. No credential protocol or template API is implemented here.
9. Passthrough data-center driver on Hadron: which fixes beyond the glibc prefix are required? Real hardware validation remains outstanding.
10. Unlicensed GPU state: report or taint a node whose license client fails?
11. gpu-operator validator/CDI access to the glibc prefix: hardware evidence and image adjustments if required.
12. Later migration: lifecycle of generated classes after their source tenant/namespace disappears, if that mechanism is chosen. It is not required by the initial no-migration release.
13. ComputePlane class selection: an explicit module-contract extension, if desired, must define its allowed tenant/hoster choices, version policy and producer handling. Initial release preserves its existing legacy Talos contract rather than hardcoding a WorkerClass.
14. Later migration rollback/re-upgrade: define restoration of pool fields, classification metadata and any version stamps before shipping that migration. The first Kairos release has no compulsory migration to replay.
15. Later cleanup: how to remove redundant persisted baseline copies or superseded storage without exposing an old chart during a skipped-release pre-upgrade window? This does not remove supported tenant override inputs. Keeping the current fields in the initial stage avoids that window but does not finish their migration.
16. Later migration in variants without the platform hook: how to reach those clusters without replaying unrelated historical migrations? This is a blocker of that migration stage, not of Kairos or unchanged legacy pools.

## Alternatives considered

**A Talos extension instead of Kairos.** Still preferable when an existing extension supplies the module. Custom extensions require building/hosting a compatible image per Talos release; Kairos offers a hoster image layer for cases that need a conventional userspace/driver stack. It does not make driver maintenance disappear.

**Kairos on a glibc distribution instead of Hadron.** A glibc base would accept vendor userspace directly and avoid the glibc prefix. Hadron keeps the existing immutable build and upstream contribution path but adds the prefix and source-built Kubernetes binaries. The tested driver configuration is narrow; another flavour can be supplied as a compatible custom image rather than being advertised as already tested.

**Core-owned class machinery and compulsory migration before Kairos.** This adds app-specific behavior to platforms without managed Kubernetes and couples a new opt-in worker family to every variant's migration history. Package-owned admission plus an unchanged legacy path avoids both dependencies while keeping the Talos migration as required follow-up work.

**Hoster owns every Talos choice.** Image compatibility is a real constraint, but it does not justify removing tenant module-loading and reachability settings that already have consumers. A baseline plus explicit supported overrides keeps that boundary visible and rejects genuine fixed-image conflicts.

**One generic field per OS operation.** Sharing the name does not prove equivalence: a Talos module loader, CDI HTTPS CA and Kairos containerd configuration have different consumers. This proposal retains typed OS-specific inputs instead of claiming an unimplemented cross-OS meaning.

**A second worker-only private catalog.** It would duplicate VM golden import, credentials and clone authorization. Private disks remain deferred to a shared catalog.
