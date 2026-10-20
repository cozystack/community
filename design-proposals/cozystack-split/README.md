# Splitting Cozystack into the platform and catalogs

- **Title:** `Splitting Cozystack into the platform and the virtualization and services catalogs`
- **Author(s):** `@myasnikovdaniil`
- **Date:** `2026-10-20`
- **Status:** Draft
- **Supersedes:** [Out-of-tree app catalogs (community#43)](https://github.com/cozystack/community/pull/43) and [Cozystack as a distribution (community#46)](https://github.com/cozystack/community/pull/46). What this proposal takes from each is listed under [Scope and related proposals](#scope-and-related-proposals); both can be closed once it is accepted.

## Overview

Cozystack ships as one tree and one release: the core, the system packages, virtual machines and tenant Kubernetes, and every managed service. We propose to split it into the **platform** and **catalogs**, each with own version, CI and owners:

- The platform is always installed. It holds the core (operator, CRDs, `cozystack-api`, `cozystack-controller`), system packages, tenants and tenant modules, object storage.
- The virtualization catalog holds virtual machines and everything that exists for them: disks, images, VPC and VM networking, GPUs for VMs and tenant clusters, VM import, and tenant Kubernetes, because its workers are VMs.
- The services catalog holds managed services like databases, queues, caches, the registry and secrets, with the operators only they use.

Catalogs depend only on the platform, never on each other. Platform code stops naming catalog applications. Third-party catalogs use the same mechanism as ours.

Most of the work is refactoring inside the platform. Separate repositories are the last and smallest step, and we take it only after the boundary has held in the monorepo. Until then the platform and catalogs live in one tree and ship as separate artifacts.

The seam between the platform and catalogs is a set of contracts, K1–K6, versioned together with the platform. This proposal defines them in full, because without them nobody can check the boundary and the split is not safe.

## Terminology

| Term | Meaning |
|---|---|
| **platform** | What every installation runs. One per cluster. |
| **catalog** | A set of applications for tenants plus everything only they need: operators, CRDs, backup drivers, networking. A catalog depends on the platform and has own version. `virtualization` and `services` are our catalogs, third-party catalogs are the same thing in another repository. |
| **release manifest** | Signed list of the platform version and catalog versions tested together, with artifact digests and migration order. |
| **environment settings** | Host, network provider, storage provider and GPU mode of the platform (`host`, `network.provider`, `storage.provider`, `gpu.mode`). Network and storage can be `none` to use the cluster's own CNI or CSI. Replaces hardcoded platform variants. |
| **platform requirements** | What a catalog needs from the platform, stated as properties (for example a VPC API or an RWX Block storage class) instead of component names. |

Retired terms: **bundle** (system, iaas, paas, naas) becomes the platform and the catalogs, and **platform variant** (`isp-full`, `isp-hosted`, ...) becomes environment settings. `Package`, `PackageSource`, its components and its variants keep their current meaning.

Where a text needs all three at once, it says "the platform and catalogs".

## Scope and related proposals

- [Out-of-tree app catalogs (community#43)](https://github.com/cozystack/community/pull/43), superseded. We keep its word "catalog" and its platform | catalog line, treat virtualization as a catalog too, and cover the coupling [#43](https://github.com/cozystack/community/pull/43) leaves out: backups, RBAC, values and monitoring.
- [Cozystack as a distribution (community#46)](https://github.com/cozystack/community/pull/46), superseded. We keep its release manifest, but unit of versioning is the platform or a catalog instead of a single `PackageSource`.
- [Platform migrations (community#58)](https://github.com/cozystack/community/pull/58): the migration engine. The platform and each catalog keep own ledger.
- [Per-cluster etcd for tenant Kubernetes (community#25)](https://github.com/cozystack/community/pull/25): removes reference to Kamaji from the etcd module.
- [Fold extra into apps (community#39)](https://github.com/cozystack/community/pull/39): part of step 0, the dashboard work uses its `spec.dashboard.visibility`.
- [Cozymarketplace](../cozymarketplace/README.md): delivery path for new catalogs under the contribution policy below.

## Decisions

## Context

How a package gets from the tree to a cluster today:

1. **One artifact.** The installer build runs `flux push artifact --path=packages` and publishes the whole `packages/` tree as single OCI artifact, `cozystack-packages`. On the cluster it is one Flux `OCIRepository` with that name in `cozy-system` (`packages/core/platform/templates/repository.yaml`).
2. **Sources.** 105 `PackageSource` objects in `packages/core/platform/sources/` point into that artifact by path. Each one lists its components (charts), variants, `dependsOn` and libraries like `cozy-lib`.
3. **Bundles and variants.** The platform chart (`packages/core/platform`) renders a `Package` for every source the chosen bundles need: `templates/bundles/{system,iaas,paas,naas}.yaml`. Platform variant (`bundles.system.variant`) picks which variant of each source gets installed, for example the network stack. The chart also writes the `cozystack-values` secret in `cozy-system`.
4. **Packages to releases.** The operator's `Package` reconciler (`internal/operator/package_reconciler.go`) turns each `Package` into Flux `HelmRelease`s, one per component, and Flux installs them.
5. **Applications.** Every application has a `*-rd` chart in `packages/system/` (31 of them) that installs its `ApplicationDefinition`. `cozystack-api` serves each kind under `apps.cozystack.io` from those definitions. When user creates an application, the API writes a `HelmRelease` for the application chart, with values from the `cozystack-values` secret of the tenant namespace (`pkg/registry/apps/application/rest.go`). The tenant chart writes that secret for each tenant.

Everything above ships at one version: the artifact, the platform chart, the operator, the API and every application. Because of that shared version the platform and applications rely on each other without writing anything down.

### The problem

**A small change becomes everyone's change.**

- 31 of 105 `PackageSource`s span several directories, and `cozy-lib` is copied into 34 charts.
- Any core change runs the full e2e: 26 suites on 32 CPU and 128 GB, up to 215 minutes.
- Which applications get tested depends on who wrote a suite, importance of the application plays no role.

**Releases are all or nothing.**

- The patch from 1.6.1 to 1.6.2 changed 73 of 207 artifacts and restarted 43 of 164 pods, measured on a test cluster in [community#46](https://github.com/cozystack/community/pull/46). 25 of those 73 artifacts moved only because one template was added to `cozy-lib`.
- A fix to one application waits for a platform release.
- Version skew already exists and is declared nowhere. For example, 8 applications render `TenantProjection`, but it exists only in core `v1.7.0-alpha.1`, so 1.7 applications cannot be installed on 1.6 core.

**The platform knows the applications.** Platform code names catalog applications in ten places, listed under [What the platform knows today](#what-the-platform-knows-today). Examples:
- The backup controller has a strategy per application kind.
- The tenant role lists every application kind.
- `cozystack-api` serves VM-specific dropdowns.
- The dashboard picks VM tabs by kind.

**Users who need less cannot take less.** Seven platform variants are hardcoded in the operator, each one a fixed combination of host OS, network and size (`cmd/cozystack-operator/main.go:771-777`). New combination needs a new name in Go and a new values file. Nobody can install the platform without catalogs.

## Goals

- The platform installs without any catalog and nothing in it breaks.
- Each catalog has own owners, CI and version.
- A fix in a catalog ships as release of that catalog. The release manifest records versions tested together.
- Contracts define what is a breaking change, and CI catches it before users do.
- Core change is gated by platform tests only. Catalogs test against released platform versions and the next one.
- Our catalogs and third-party catalogs ship through one mechanism.
- Users see no change on the move: same API group, kinds and names.
- Applications with non-free licenses can leave the core repository.

### Non-goals

- Splitting the core out of the platform now. The core stays a boundary inside the platform, see [Rules](#rules).
- Fixing problems that do not cross the platform | catalog boundary: owner of the tenant model, role of `cozystack-controller`, defects found on the way. They are tracked separately and don't block the split.
- Changing licenses of the platform itself (LINSTOR, Talos, ZFS, DRBD) or vendor lock-in.
- Release cadence. The split makes per-catalog releases possible but does not set a cadence.

## Design

### Platform and catalogs

| | Contents |
|---|---|
| **platform** | Core (operator, CRDs, `cozystack-api`, `cozystack-controller`, lineage webhook). System packages (network with multus as its option, storage, the host GPU stack, cert-manager, monitoring, Keycloak). Tenant and tenant modules. Shared operators (`postgres-operator`, `objectstorage-controller`). Object storage (the SeaweedFS tenant module, the COSI controller, the Bucket application). The backup framework as optional component. The dashboard |
| **virtualization** | VMInstance, VMDisk, VirtualPrivateCloud, tenant Kubernetes, kubernetes-nodes, computeplane. KubeVirt with its GPU device wiring, CDI, CAPI, Kamaji, forklift. gpu-operator and HAMi as addons inside tenant clusters. VM and worker images. The VM backup driver and Velero. CSI for tenant clusters |
| **services** | ClickHouse, FoundationDB, Harbor, Kafka, MariaDB, MongoDB, NATS, OpenBao, OpenSearch, Postgres, Qdrant, RabbitMQ, Redis, Valkey, plus `tcp-balancer` and `vpn`. Operators only they use. Their backup drivers |

The data behind this cut:

- virtualization and services share no dependency edge in either direction
- in 12 months of history no commit changed them together without the platform
- 567 of 754 content commits already fit inside one of the three
- the platform without virtualization is already tested by the `e2e-isp-hosted` lane

**Where a new component goes.** If it exists for VMs or for what runs on VMs, it belongs to virtualization, VM networks like VPC included. Multus stays in the platform as option of the network provider: it runs on top of the CNI config and has to match its plugin versions, and the cozyplane network builds VPC without it ([cozystack#4508](https://github.com/cozystack/cozystack/pull/4508)). Same for GPUs: the host GPU stack prepares nodes and is a platform setting, but handing GPUs to VMs and GPU addons of tenant clusters belong to virtualization. If it is finished software a tenant consumes, it belongs to services. If everything needs it, it belongs to the platform.

**Object storage stays in the platform.** The backup framework depends on COSI and the system bucket, the tenant chart enables SeaweedFS as tenant module, and both catalogs consume buckets: virtualization through Velero, services through Harbor, ClickHouse, CNPG and PSMDB. Moving it to services would add a platform → services edge. The bucket class names, `_namespace.seaweedfs` and the `cozy-backups-creds` secret become part of K2 and the backup SDK, so SeaweedFS stays the default provider but not the only one.

**Ownership.** Cozystack is a CNCF project, and the split divides code that is in its monorepo today. The platform and both catalogs stay repositories of the project in the `cozystack` organization, with the same maintainers and the same governance. Each gets own CODEOWNERS entries and CI path, so reviewers can be assigned per catalog and who maintains the project does not change.

### Rules

- **Vertical cut.** An operator used by one application ships with its catalog. An operator used by both catalogs or by the platform itself stays in the platform. So migrations of an application and of its operator stay in one ordered place.
- **The core is a boundary inside the platform.** It gets own directory, CODEOWNERS and CI path, but ships with the platform. A core | system boundary would need the hardest contract (CRD versions, the `_cluster` schema, `cozy-lib` helpers) and today gives nothing visible. We split it out when someone runs the core without our system packages, or when platform releases start holding core fixes.
- **Bundles go away.** system → platform, iaas → virtualization, paas and naas → services. Each catalog carries own list of `Package`s.
- **Platform variants become environment settings.** Network and storage are each a choice of several providers, or `none` to rely on the cluster's own CNI or CSI. GPUs are a choice of mode, `none` by default. The host (Talos, generic Linux, managed Kubernetes) is one more setting. The slim size goes away: minimal installation is the platform without catalogs, and the root monitoring stack is an ordinary option.

| Setting | Values |
|---|---|
| `host` | `talos`, `generic`, `managed` |
| `network.provider` | `kubeovn-cilium` (Kube-OVN with Cilium, the default, with multus as its option), `cilium`, `cilium-kilo`, `cozyplane` (in progress, [cozystack#3149](https://github.com/cozystack/cozystack/pull/3149)), `none` |
| `storage.provider` | `linstor` (LINSTOR with DRBD, the default), `blockstor` (in progress, [cozystack#2942](https://github.com/cozystack/cozystack/pull/2942)), `none`. `local-storage` and `nfs-driver` are options that work with any of them |
| `gpu.mode` | `none` (the default), `passthrough` and `vgpu` (GPUs for VMs), `container` (GPUs for pods on the host's own driver). HAMi, which shares GPUs between pods, is an option |

These are today's network variants, storage packages and `gpu-operator` variants (`passthrough` is today's `default`), with the host part of variant names moved to the `host` setting.

### The boundary and when it is done

A boundary lint runs over the tree. It maps every file to a `PackageSource`, every `PackageSource` to the platform or a catalog, and reports edges between them:

- `dependsOn` entries
- API groups whose CRDs another catalog or the platform ships
- application kinds and plurals
- source names, component paths, release names and namespaces
- `lookup` calls
- `cozy-lib` includes

Edges from a catalog to the platform are allowed. Edges from the platform to a catalog, and between catalogs, are forbidden.

On `bcda5c893` the lint finds 70 forbidden source pairs in 60 files. Without vendored upstream charts 65 pairs in 54 files remain. Pairs overstate the work, one RBAC list alone yields 21 pairs. The work groups into the ten places below.

**Done means:** the lint reports zero forbidden edges outside a reviewed exception list, and every catalog is green against platform N−1, N and N+1. Whether repositories exist does not matter for this. If a repository is cut before the lint reaches zero, every remaining edge becomes a dependency across repositories. Then a new dropdown or tab for VMs needs a platform release, and the shared CI that catches skew today is gone.

The lint sees only strings, it doesn't see behaviour. Values keys, naming conventions and runtime assumptions are covered by the contracts and the N±1 checks instead.

The lint is a prototype today and lands in `hack/` of `cozystack/cozystack` in step 0. It runs in baseline mode: a checked-in file lists every forbidden edge that exists at landing time, one line per source pair and file with a reason, and CI fails on an edge missing from the file. An exception is a line in the same file with link to the issue where it was approved.

### What the platform knows today

Paths are relative to the root of `cozystack/cozystack` and checked on `main` at `aed7fa896` (2026-10-19). Console paths are under `packages/system/dashboard/images/console/apps/console/src/`.

| # | Where the platform knows a catalog | Files | Fix |
|---|---|---|---|
| 1 | Backups: 11 strategies are methods of shared reconcilers with a switch on kind. Deleting a `Backup` defaults to Velero. One `BackupClass` lists every application | `internal/backupcontroller/backupjob_controller.go`, `restorejob_controller.go`, `cmd/backupstrategy-controller/main.go`, `packages/system/backupstrategy-controller/templates/backupclass-default.yaml` | The framework stays in the platform as optional component with a driver registry. Each driver ships with its application. Velero and the VM driver move to virtualization |
| 2 | The tenant-admin role lists every catalog kind, and the platform grants rights and network policies for KubeVirt | `packages/system/cozystack-basics/templates/clusterroles.yaml`, `packages/system/cozystack-api/templates/rbac.yaml`, `packages/apps/tenant/templates/networkpolicy.yaml` | Each `*-rd` chart ships the role for its kind through the existing aggregation labels. Virtualization ships own roles and policies |
| 3 | The tenant chart renders computeplane by name | `packages/apps/tenant/templates/computeplane.yaml` | Generic list of tenant modules driven by `ApplicationDefinition` |
| 4 | Option providers in core Go, 8 of 14 for virtualization | `pkg/registry/core/option/providers.go` | Declarative option sources shipped by the catalog |
| 5 | Warnings and name limits for Kubernetes, Kafka, Postgres and MariaDB in `cozystack-api` | `pkg/registry/apps/application/rest.go` | Remove, or a `ValidatingAdmissionPolicy` with `Warn` in the application chart |
| 6 | WorkloadMonitor knows CDI `DataVolume` | `internal/controller/workloadmonitor_controller.go` | Generic readiness gates declared by the application |
| 7 | VM import: an API group, a controller and a binary in the root Go module | `api/migration/v1alpha1/`, `internal/migrationcontroller/`, `cmd/migration-controller/` | Moves to virtualization whole |
| 8 | The dashboard picks VM tabs and power buttons by kind, always shows the Migration section, and keeps icons for about 30 kinds | `routes/detail/ApplicationDetailPage.tsx`, `routes/sidebar-sections.tsx`, `lib/sidebar-icons.tsx` | The console knows kinds of views, and a catalog declares the views it needs as data: typed tabs and actions and the icon in the AD, plus a sidebar section object |
| 9 | Monitoring dashboards for applications live in the platform | `packages/system/monitoring/dashboards.list` | The application ships own dashboards, monitoring picks them up by label |
| 10 | Tenant Kubernetes installs 19 platform charts into tenant clusters as addons, by artifact name | `packages/core/platform/sources/kubernetes-application.yaml` | Reference them through a source-only `cozystack.cluster-addons`. The 4 charts used only by tenant clusters move to virtualization, which also carries own copies of `gpu-operator` and `hami` for tenant clusters |

Four more edges show up in the lint but need no separate design. Other work removes them, or they are not coupling:

- The etcd tenant module renders a Kamaji `DataStore`. [community#25](https://github.com/cozystack/community/pull/25) removes it, and it is a prerequisite of step 2.
- `nvidia.com/gpu` and similar names in telemetry are Kubernetes extended resource names, they don't reference a catalog.
- RBAC inside vendored upstream charts (cluster-autoscaler, piraeus, Kube-OVN) is upstream content and stays as is.
- `api/apps/v1alpha1`, the Go types generated from application schemas. Nothing in the tree imports it, but it is published for external consumers, so it is not dead code. It splits with the catalogs: the platform keeps tenant and bucket types, each catalog publishes types for its own applications.

### Contracts

The contracts are the seam of the split. Today the platform and applications rely on each other through names, keys and conventions written down nowhere. They hold only because everything ships at one version. Once the platform and catalogs have own versions, the dangerous failures are the silent ones: Helm ignores an unknown values key, a `Capabilities` check switches a template branch off, a `lookup` returns nothing, a webhook lets an object through without labels. Each contract below lists what it covers, so every such assumption is either declared and checked, or removed.

#### What a contract is

A contract is neither free prose nor one OpenAPI document for everything. Each contract has three parts:

1. **A machine-readable schema** in the format that fits what it describes: CRD OpenAPI for resources, JSON Schema for values and annotations, a Helm library chart for `cozy-lib`, a YAML list for names, labels and properties.
2. **A short text** for semantics a schema cannot express: what a missing key means, who writes and who reads, ordering.
3. **A CI check** that fails when a catalog or the platform breaks it.

Contracts have no versions of their own, version of a contract is the platform version. The schemas live in the platform tree under `contracts/<name>/` and are published with every platform release. A catalog declares one number, the minimum platform version it needs. A minor platform release may add to a contract. Removing or changing something is first announced as deprecated in one minor release, shows up as a warning in catalog CI, and is removed no earlier than two minor releases later, so a catalog tested against N−1, N and N+1 always sees the warning before removal.

Rejected: a version per contract. The platform ships as one artifact, so six contract versions would always move together, and catalog author would have to track six numbers instead of one.

Part of this already exists. The `ApplicationDefinition` CRD and all other core CRDs have structural schemas generated by controller-gen and guarded by the codegen drift check. 35 charts have a `values.schema.json` generated by `cozyvalues-gen`, and the release ships an `openapi.json` for `apps.cozystack.io`. Three things have nothing machine-readable today: the `cozystack-values` payload, the `cozy-lib` helper interface and compatibility between a catalog and the platform. No version or compatibility field exists anywhere. `PackageSource` has no field for a minimum platform version.

#### K1. Core API for applications

What a catalog uses to define an application, and what the core does with it.

| Item | Today | Contract |
|---|---|---|
| `ApplicationDefinition` fields: `application`, `release`, `dashboard`, resource selectors | CRD `v1alpha1`. `openAPISchema` is an opaque string the CRD does not validate | CRD schema. `openAPISchema` is validated as JSON Schema on admission |
| `release.cozystack.io/*` annotations (install and upgrade timeouts, disable-wait, server-side apply) | parsed when `cozystack-api` starts. One invalid value stops the whole API | JSON Schema of the annotations. An invalid value disables only that kind and sets a condition on the AD |
| schema extensions `x-cozystack-name`, `x-cozystack-options` | a fixed set of keywords. An unknown keyword disables creation for the kind | JSON Schema of the extensions. An unknown keyword is rejected on admission of the AD |
| CRDs that applications render: `WorkloadMonitor`, `TenantProjection`, `VMPodScrape` and `VMServiceScrape`, COSI, Gateway API | live in the platform with no declared version. `TenantProjection` exists only from core 1.7 | the list of CRDs and versions the platform guarantees |
| labels the core writes on releases: `apps.cozystack.io/application.{kind,group,name}`, lineage labels `internal.cozystack.io/*`, the `helm.toolkit.fluxcd.io/name` label used for lineage | conventions | a YAML list of labels and their meaning |
| release name = `release.prefix` + application name. Values keys starting with `_` are reserved | conventions | stated in the contract text |
| artifact name of a component | every AD copies the formula by hand into `release.chartRef` | the core derives `chartRef` from the source and component, and the formula leaves the contract |
| ownership of a kind | the first AD owns the kind. A later duplicate gets a `KindClaimed` warning event ([cozystack#4424](https://github.com/cozystack/cozystack/pull/4424)), but no condition | the owner is explicit, and a second AD for the same kind is refused with a condition |
| sharding label `sharding.fluxcd.io/key` | 30 of 31 ADs set `tenants`, and the AD reconciler re-applies it on every change | the core sets the shard, ADs do not carry it |

**Check:** catalog CI validates its ADs against the K1 schemas of platform N−1, N and N+1, and installs them on a platform of each version.

#### K2. Tenant contract

What a tenant namespace gives every application in it.

**Values.** Today the core passes platform data to every chart as the `cozystack-values` Secret through `valuesFrom`, under `_cluster` and `_namespace`. There are three writers: the platform chart for `cozy-system`, `cozystack-basics` for `tenant-root` and the tenant chart for child tenants. An operator controller copies the system copy into every system namespace. Root and child writers are separate templates and already drifted ([cozystack#4783](https://github.com/cozystack/cozystack/issues/4783)). There is no schema, `_cluster` has about 47 keys, and nothing in it is secret.

The contract separates **what** the keys are from **how** they reach the chart:

- **Keys.** A JSON Schema lists keys a catalog chart may read: `_namespace.{host, ingress, gateway, monitoring, seaweedfs}` and a small set of `_cluster` keys (`cluster-domain`, `root-host`, `scheduling`, `images-registry`, `issuer-name`, `solver`, `wildcard-secret-name`, `oidc-enabled`). Every other `_cluster` key is platform-internal and doesn't reach catalog charts. `_namespace.etcd` goes away with [community#25](https://github.com/cozystack/community/pull/25).
- **Missing key.** A missing key means `""`. A key is written only when its module is enabled. `cozy-lib` gets a `required` helper for charts that cannot work without a key.
- **Transport.** The Secret is replaced. A typed resource holds the tenant's context, a single controller computes `_namespace` from it and projects the values into a ConfigMap in each tenant namespace. Charts keep reading `.Values._cluster` and `.Values._namespace` through `valuesFrom`, so for them nothing changes. Flux reconciles a release when the ConfigMap changes, which is the property [cozystack#1787](https://github.com/cozystack/cozystack/pull/1787) introduced `valuesFrom` for. Computing `_namespace` in a controller is what [community#39](https://github.com/cozystack/community/pull/39) already proposes. Subchart values that ride the same Secret today (`keda.clusterDomain`, `opensearch-operator.manager.dnsBase`) move into values of their own packages.

Alternatives for the transport:

| Option | Why not |
|---|---|
| keep the Secret, add only a schema | cheapest, but keeps the three writers, the Secret type for non-secret data and per-namespace copies |
| a ConfigMap with the same writers | fixes only the type |
| charts read a typed resource with `lookup` | brings back what [cozystack#1787](https://github.com/cozystack/cozystack/pull/1787) removed: `lookup` does not trigger a reconcile and returns nothing in `helm template` and unit tests |
| the core writes the values into every `HelmRelease` | every platform change rewrites every release, and values written once go stale ([cozystack#4686](https://github.com/cozystack/cozystack/issues/4686) is this class of bug) |

**Objects and labels.**

| Item | Contract |
|---|---|
| IngressClass name = the ingress namespace | declared name |
| Gateway `cozystack` with its listeners | declared name and listener names |
| Kamaji `DataStore` name = the etcd namespace | goes away with [community#25](https://github.com/cozystack/community/pull/25) |
| `BucketClass` `<seaweedfs>[-<pool>][-lock]`, `BucketAccessClass` `<seaweedfs>[-<pool>][-readonly]` | declared names |
| the system bucket `cozy-backups` in `tenant-root` and the `cozy-backups-creds` Secret with its keys | declared names and keys, part of the backup SDK |
| monitoring endpoints (`vminsert-shortterm`, `vmselect-shortterm`, `vlinsert-generic`) | a stable service alias, so charts stop depending on the name user gave the storage |
| namespace labels `namespace.cozystack.io/*`, `tenant.cozystack.io/<ancestor>` | declared list |
| network policy labels `policy.cozystack.io/allow-to-apiserver`, `allow-to-etcd`, and which pods may reach ancestor namespaces | declared list |
| hostname `<release>.<_namespace.host>` | a `cozy-lib` helper instead of a copy in every chart |
| `ResourceQuota` `tenant-quota` and the default `LimitRange` | declared |

**Check:** CI renders all writers and validates their output against the schema. Catalog CI renders each chart with values generated from the K2 schema of platform N−1, N and N+1, and each chart declares `_cluster` and `_namespace` keys it reads in own `values.schema.json`.

#### K3. Platform requirements of virtualization

What virtualization needs from the network, storage and GPU stack of the platform, stated as properties, so a different provider, or `none`, can satisfy them.

Today the only check is the variant name in `bundles/iaas.yaml`: virtualization is allowed with `isp-full` or `isp-full-generic`. Any network variant satisfies the `dependsOn` on `cozystack.networking`, and no virtualization source depends on storage. In practice the requirement is Kube-OVN 1.15 with Cilium 1.20 on top of it, LINSTOR with DRBD, and ingress-nginx with `ssl-passthrough`.

| Property | What virtualization relies on | Met today by |
|---|---|---|
| `network.vpc` | the `Vpc` and `Subnet` APIs | Kube-OVN |
| `network.stableVMAddress` | a VM keeps its IP and MAC across live migration. The chart reads the Kube-OVN `IP` object, and without it the template branch is silently skipped | Kube-OVN |
| `network.secondaryNetworks` | multus on top of the CNI config, with matching CNI plugin versions | multus, an option of the Kube-OVN with Cilium network |
| `network.podMTU` | a value, not a flag: tenant clusters set their MTU from it | published by the network provider |
| `network.loadBalancerWholeIP` | VM services as LoadBalancer with a whole IP | cozy-proxy with MetalLB |
| `storage.vmClass` | a named class with RWX Block, immediate binding, expansion and cloning inside the class, plus a CDI `StorageProfile` | LINSTOR with DRBD. Today no chart creates the class `replicated` |
| `storage.snapshots` | CSI snapshots with a default class | snapshot-controller with LINSTOR |
| `storage.remoteAttach` | classes a tenant cluster can attach over the network | LINSTOR |
| `storage.rwxFilesystem` | RWX Filesystem for tenant clusters | LINSTOR NFS export with Cilium policy |
| `ingress.passthrough` | TLS passthrough for tenant Kubernetes API servers and the VM export and upload proxies | ingress-nginx with `ssl-passthrough` |
| `gpu.vmDevices` | host GPUs bound for VMs (whole cards through `vfio-pci`, or vGPU profiles) and the list of device names VMs may request. Virtualization renders KubeVirt's `permittedHostDevices` from that list | `gpu-operator` in the `passthrough` or `vgpu` mode. Today the platform chart renders `permittedHostDevices` itself in `bundles/iaas.yaml` |
| node capabilities | `kvm`, `openvswitch`, `drbd`, `zfs`, `vfio` as node labels | the host |

Each network and storage provider, and each GPU mode, declares which of these properties it gives and publishes the values (`podMTU`, the VM class name). With `none` the administrator declares them in the platform values. Virtualization declares the properties it needs, and a missing one refuses installation with the property's name.

**Check:** the virtualization CI installs on each provider combination it claims, and install with a missing property fails with that property's name.

#### K4. Tenant Kubernetes addons

What tenant Kubernetes uses to install platform charts into tenant clusters.

| Item | Today | Contract |
|---|---|---|
| artifacts | names derived from the kubernetes `PackageSource` | `cozystack.cluster-addons` with stable artifact names and a deprecation window before a rename |
| values of each addon | nested under the wrapper chart's subchart key. Helm ignores unknown keys, so a renamed key drops the setting with no error (confirmed with `helm template` for Cilium) | a `values.schema.json` per addon artifact, generated by `cozyvalues-gen` |
| install into a remote cluster | the kubernetes chart turns off single templates with flags like `vpaForVPA` | each addon chart declares and tests a remote-cluster mode |
| fixed namespaces (`cozy-velero`, `cozy-ingress-nginx`, `cozy-cilium`) and the coredns zone | conventions | declared list |
| Cilium behaviour in tenant clusters (`kubeProxyReplacement`, Gateway API, Talos settings) | inherited from platform defaults | set explicitly by tenant Kubernetes |
| Kamaji admin kubeconfig secret and its key | convention inside virtualization | stays inside virtualization, pinned by the virtualization version |

**Check:** the virtualization CI renders every addon with tenant Kubernetes values against the addon schema of platform N−1, N and N+1, and fails on an unknown key.

#### K5. `cozy-lib`

The helper library that application charts use.

Today 34 charts get `cozy-lib` through a committed symlink `charts/cozy-lib → ../../../library/cozy-lib`, and on the cluster the operator copies `library/cozy-lib` into every chart artifact that declares it in `PackageSource.variants[].libraries`. Version of the library is the placeholder `0.0.0`, no chart pins a version, and the operator can copy a library only from the same artifact as the chart. So a catalog in another repository has to vendor its own unpinned copy. [community#46](https://github.com/cozystack/community/pull/46) measured cost of the shared copy: in the 1.6.1 → 1.6.2 patch, 25 of the 73 changed artifacts changed only because a helper was added to `cozy-lib`.

The contract:

- **Smaller first.** Helpers with one consumer move to that consumer: `keda`, `strings.hexToInt`, most of `_cozyconfig.tpl`. Helpers with no consumer are removed: `ipv4-cluster-cidr`, `ipv4-service-cidr` and `ipv4-join-cidr` read `_cluster` keys nothing writes, and only the library's own test chart calls them. What stays is the shared core: the tenant RBAC subject model, resource presets, image references, hostname and `required` helpers from K2. That is the public interface, listed in the contract.
- **Delivery.** `cozy-lib` is published as an OCI Helm library chart with every platform release, under the platform version. A catalog pins it in `Chart.yaml` `dependencies`, and `helm dependency build` puts the pinned version into the catalog artifact at build time. `cozypkg validate` checks the pin, `cozypkg init` scaffolds it. The cluster keeps copying what is in the artifact, so on the cluster side nothing changes.
- **Optional.** A catalog does not have to use `cozy-lib`. Without it the catalog depends only on K1, the K2 keys and the tenant aggregation labels.
- **Our catalogs** switch from the symlink to the pinned dependency in step 2, so they are built the same way as third-party catalogs.
- **Version link to K2.** The helpers read `_cluster` and `_namespace`. `cozy-lib` carries the platform version, so a pin also says which K2 schema the catalog was rendered against.

Rejected: the operator resolving the library from the platform at install time. It ties how a catalog renders to the platform version installed on the cluster, instead of the version the catalog was tested with.

**Check:** the library's unit tests, plus every catalog chart rendered with its pinned `cozy-lib` against the K2 schema of each supported platform version.

#### K6. Dashboard

What a catalog declares so the console shows its applications without a console release.

| Item | Contract |
|---|---|
| `AD.spec.dashboard` | the CRD schema. The console reads 5 of its 12 fields today (`category`, `description`, `icon`, `keysOrder`, `module`). The rest are read or declared deprecated. `tabs` becomes the typed list from step 1, `visibility` comes from [community#39](https://github.com/cozystack/community/pull/39) |
| schema extensions | `x-cozystack-options`, the new `x-cozystack-widget`, and the option source annotations, in the K1 extension schema |
| `core.cozystack.io` resources the console uses | options, tenant namespaces, tenant modules, tenant secrets, taps, and their served OpenAPI |
| application labels and release name | from K1 |
| sidebar section object | a CRD with a title, a list of CRDs and a placement |
| RBAC | each catalog grants tenants read access to its CRD schemas and its subresources through the tenant aggregation labels |

**Check:** the console has unit tests that render tabs, actions and sections from fixture ADs of every platform version it supports.

#### Compatibility rule

1. The platform publishes the contract schemas with every release, under the platform version.
2. A catalog declares its minimum platform version and its platform requirements. `PackageSource` gets a field for this, and the operator refuses a catalog the platform cannot satisfy.
3. Catalog CI renders and installs it against platform N−1, N and N+1, where N is the latest platform release and N+1 the next minor in development. A version below the declared minimum of the catalog is skipped. CI fails on an unknown key, a missing object or a CRD version mismatch.
4. A release records the set that was tested together.

### Releases, versions and migrations

- SemVer versions for the platform and for each catalog. Contracts follow the platform version and change only through the deprecation rule in [What a contract is](#what-a-contract-is).
- A release is a signed manifest with the platform and catalog versions tested together, artifact digests and migration order. Users install a release and don't pick versions one by one.
- On partial upgrade, what did not change is not reinstalled. A fix in services ships as a patch release of the manifest where only services moved.
- Migrations use the [community#58](https://github.com/cozystack/community/pull/58) engine, with a ledger for the platform and for each catalog, plus a ledger for migrations that cross them. The vertical cut keeps migrations of an application and its operator in one catalog.

### Contribution policy for new applications

Enforced from step 0:

- New standalone applications are not added to `cozystack/cozystack`. They live in a separate catalog repository, following the external apps model, and get installed through external apps and the marketplace.
- Recommended layout for such repository is `PackageSource` with `cozypkg`, the same format our own catalogs use after step 2. The older layout from `cozystack/external-apps-example`, a Git repository with a bootstrap `HelmRelease`, keeps working but is not recommended for new catalogs.
- Community applications live in a separate GitHub organization, for example `cozystack-community`, which is not part of the CNCF project and does not carry the project's governance requirements. People who bring an application there review and maintain it. The project itself maintains only the platform, virtualization and services.
- New platform components are accepted at maintainers' discretion.
- There is no grace period. Once the policy is accepted it applies to open pull requests and issues too.
- Changes that couple applications tighter to the platform are rejected in review. This covers platform code that names an application's kind, API group, namespace or release name, and core RBAC, backups, option providers, the dashboard or monitoring learning about a specific application.
- The boundary lint enforces the coupling rule in CI: a new forbidden edge fails the build and points to the policy.

The policy text goes into `CONTRIBUTING.md` of `cozystack/cozystack` after this proposal is accepted.

**Is the marketplace ready to be the only path?** Not yet. On `main` a third party can already run `cozypkg init`, `validate` and `push`, connect the artifact with `cozypkg tap` or from the dashboard, and get a new kind with schema-driven forms. All of this came in [cozystack#3940](https://github.com/cozystack/cozystack/pull/3940), which is only in the 1.7 pre-releases. On 1.6 the only paths are the old `external-apps-example` layout or a hand-written `PackageSource` with `cozypkg add`. Before the policy can send everyone to the marketplace, the following must be in place, as part of step 0:

1. **1.7.0 released** with [cozystack#3940](https://github.com/cozystack/cozystack/pull/3940) and [cozystack#4424](https://github.com/cozystack/cozystack/pull/4424).
2. **A package cannot take over another package's `HelmRelease`.** `createOrUpdateHelmRelease` in `internal/operator/package_reconciler.go` overwrites any `HelmRelease` with the same namespace and name, owner references included. `cozypkg init postgres` scaffolds `cozy-system/postgres-rd`, which is the platform's own release name. The fix is the guard [cozystack#3940](https://github.com/cozystack/cozystack/pull/3940) added for `PackageSource` after [cozystack#4001](https://github.com/cozystack/cozystack/issues/4001): refuse a `HelmRelease` that belongs to a different `Package`.
3. **Artifact names cannot collide.** `internal/marketplace/naming/naming.go` replaces `.` with `-`, so `acme.web` and `acme-web` produce the same artifacts. A tap source named `cozystack-postgres-application` can shadow the platform's artifacts. The reserved-prefix check runs only in `cozypkg validate`, so the dashboard path skips it. The encoding has to be injective, and the materializer has to run the reserved-prefix check.
4. **One bad `ApplicationDefinition` does not stop `cozystack-api`.** `pkg/cmd/server/start.go` aborts start-up on the first invalid `release.cozystack.io/*` annotation. Every AD change restarts the API, so one bad AD from a third-party catalog takes down `apps.cozystack.io` for every tenant.
5. **Connecting a catalog does not install code without confirmation.** Each connect creates a registration `Package` that installs the publisher's `-rd` chart into `cozy-system`. Flux applies it with a service account bound to `cluster-admin`, while the 1.7 documentation says "tapping itself installs nothing". [cozystack#4359](https://github.com/cozystack/cozystack/issues/4359) already proposes separating registration from installation, and installation then goes through `cozypkg add`.
6. **A signed index exists.** The trust model relies on signature checks in the community index, but there is only a template in `hack/packages-index/`. Taps pin by tag and nothing verifies a signature at pull time. Either digests are accepted, or `spec.verify` is set from the index entry.
7. **Publisher guidance.** The `cozypkg init` scaffold ships a `ClusterRole` with the tenant aggregation labels, because the tenant-admin role lists kinds by name and otherwise a third-party kind is unusable for tenant admins. Documentation explains how an external chart gets `cozy-lib` (see K5). An e2e test publishes a fixture catalog, connects it, creates an application and disconnects it.

Items 2, 3 and 4 are [cozystack#4975](https://github.com/cozystack/cozystack/issues/4975), [cozystack#4977](https://github.com/cozystack/cozystack/issues/4977) and [cozystack#4976](https://github.com/cozystack/cozystack/issues/4976), item 5 is [cozystack#4978](https://github.com/cozystack/cozystack/issues/4978) (related: [cozystack#4359](https://github.com/cozystack/cozystack/issues/4359)), and pinning by digest from item 6 is [cozystack#4979](https://github.com/cozystack/cozystack/issues/4979).

The marketplace ships enabled in 1.7.0, and items 2 to 7 land in 1.7 patch releases. Items 3, 5 and 6 are reachable only through a connected catalog: the platform ships no `Tap`, and only cluster administrators can create one. Items 2 and 4 are not marketplace code. They sit in the `Package` reconciler and in `cozystack-api`, so the 1.6 path through a hand-written `PackageSource` and `cozypkg add` reaches them too, and they get fixed first.

## User-facing changes

- No change to the API group `apps.cozystack.io`, kinds or names.
- Artifact names do not depend on `sourceRef`, so moving a package between the platform and a catalog does not rename anything users see.
- Installers choose which catalogs to install. The platform alone is a working installation, just without VMs or managed services.
- Platform variants are replaced by environment settings. The `bundles` values (`system`, `iaas`, `paas`, `naas`) are replaced by catalog selection. A migration converts existing values, see [Step 1](#step-1-mechanisms) item 9 and the upgrade note in [Step 2](#step-2-one-artifact-for-the-platform-and-one-per-catalog).

## Upgrade and rollback compatibility

- For a cluster, step 2 changes `sourceRef` of each `PackageSource` and converts `bundles.*.enabled` into catalog selection. Rollback points sources back at the single artifact and converts selection back.
- Migrations run with own ledger for the platform and each catalog ([community#58](https://github.com/cozystack/community/pull/58)).
- **First release with separate artifacts.** A migration creates an `OCIRepository` per catalog, repoints the existing `PackageSource`s and converts `bundles.*.enabled` into catalog selection. `Package` and `HelmRelease` names do not change and artifact names do not depend on `sourceRef`, so no application is reinstalled.
- **Order of upgrades.** The platform is upgraded first, then catalogs. If a catalog requires newer platform than installed, the operator refuses it with the missing version or property. Within the window N−1..N+1 a catalog can lag the platform by one minor release.
- **Rollback of one catalog.** A catalog is rolled back by pointing its source at the previous artifact version. This works when the catalog's own migrations allow it. Migrations that cannot be undone are marked as such in the catalog ledger, as [community#58](https://github.com/cozystack/community/pull/58) already requires.
- **Changes of the transport for tenant values** (K2) keep the keys charts read, so Flux upgrades applications on the next reconcile with no changes to their charts.

## Security

- RBAC moves from one central list into the catalogs. Each catalog grants roles for its own kinds through aggregation, so the platform no longer grants rights for kinds it doesn't install.
- New applications arrive from separate catalog repositories through the marketplace, so anyone who publishes a catalog can reach its defects. Today connecting a catalog installs the publisher's chart as `cluster-admin`, a package can take over a platform `HelmRelease`, and artifact names can collide with the platform's. These get fixed in step 0, before the policy relies on the marketplace, see [Is the marketplace ready](#contribution-policy-for-new-applications).

## Failure and edge cases

- A catalog ships a broken `ApplicationDefinition` → other kinds keep working (step 0), and that kind is disabled with a status condition (step 1 item 10).
- A catalog is installed on a platform older than its declared minimum, or one that lacks a required property → installation is refused with the missing version or property.
- Network or storage is set to `none` and the administrator has not declared the properties virtualization needs → virtualization refuses to install and says which property is missing.
- An addon values key is renamed in the platform → the tenant Kubernetes CI render against platform N fails on the unknown key, and the tenant cluster does not silently lose its CNI.
- Virtualization is not installed → the dashboard does not show VM sections or the Migration section, and backup classes for VMs are not offered.
- A catalog for platform N+1 renders an object kind the platform N does not have, like `TenantProjection` on a 1.6 core → refused by the minimum platform version instead of failing at install.
- A tenant has no monitoring module but its parent has one → monitoring keys are absent instead of pointing at a namespace that does not serve them.

## Testing

- Boundary lint in CI on every PR, in baseline mode: existing edges are listed, a new edge fails.
- For each catalog, a CI job renders and installs it against platform N−1, N and N+1.
- e2e: the platform runs its own suites, each catalog runs its own against released platform versions.
- Cross-version checks inferred from code and not run on a cluster yet. They run on a test cluster before the step that relies on them, checks 1 and 4 first:
  1. an AD with an unknown value of `release.cozystack.io/helm-server-side-apply`: does `cozystack-api` stop for every kind
  2. tenant Kubernetes addons rendered against the previous platform chart: are values dropped silently (one case is already confirmed with `helm template`)
  3. live migration of a VM on a network without the Kube-OVN `IP` API: do the IP and MAC change
  4. a 1.7 application with `TenantProjection` on a 1.6 core
  5. an AD change on a cluster with sharding: do the release shard labels flap
  6. a nested tenant without monitoring under a parent with monitoring: do its logs arrive
  7. `cozystack-api` scaled to 0 while an application pod is recreated: which labels does the pod get

## Rollout

### Step 0: now

Every item here is useful whatever the final shape is, needs no new mechanism and can start before this proposal is accepted:

- Contribution policy for new applications, and the boundary lint in baseline mode.
- Fold `packages/extra` into `packages/apps` ([community#39](https://github.com/cozystack/community/pull/39)), including `spec.dashboard.visibility`.
- Marketplace readiness, the seven items under [Is the marketplace ready](#contribution-policy-for-new-applications): HelmRelease takeover ([cozystack#4975](https://github.com/cozystack/cozystack/issues/4975)), artifact name collisions ([cozystack#4977](https://github.com/cozystack/cozystack/issues/4977)), install on connect, the signed index and publisher guidance. The `cozystack-api` item is the one below.
- Backups: a driver registry instead of the switch on kind. `RestoreJob` skips strategies from a foreign API group, as `BackupJob` already does.
- Application roles come from `*-rd` charts instead of the list in `cozystack-basics`. KubeVirt network policies the tenant chart renders move to virtualization as cluster-wide policies that select tenant namespaces.
- Monitoring dashboards move into application charts.
- Per-cluster etcd for tenant Kubernetes ([community#25](https://github.com/cozystack/community/pull/25), a prototype of phases 1–2 exists in [cozystack#4817](https://github.com/cozystack/cozystack/pull/4817)).
- An error in one AD does not stop all of `cozystack-api` ([cozystack#4976](https://github.com/cozystack/cozystack/issues/4976)). [cozystack#3448](https://github.com/cozystack/cozystack/pull/3448) does not cover it, it skips only ADs with a dangling group or a duplicate kind.
- The AD reconciler stops re-applying the sharding label to every release of a kind on each AD change (K1).
- VM import gets its own `PackageSource`. Today forklift and its controller have none, so they cannot move to virtualization in step 2.
- One HelmRelease spec builder shared by the API, the AD → HelmRelease reconciler and the operator. A symptom is tracked in [cozystack#4409](https://github.com/cozystack/cozystack/issues/4409).
- Boundary defects: the root tenant references modules it never enabled (etcd case: [cozystack#4783](https://github.com/cozystack/cozystack/issues/4783)), and the two writers of `_namespace` disagree.
- Dashboard without new mechanisms: icons and category order from the AD (free categories landed in [cozystack#4741](https://github.com/cozystack/cozystack/pull/4741), order by `weight` remains), `visibility` ([community#39](https://github.com/cozystack/community/pull/39)) instead of special cases, the Migration section only when the forklift API exists, the backup bucket from backup configuration.

Step 0 is tracked in [cozystack#4982](https://github.com/cozystack/cozystack/issues/4982), the marketplace items in [cozystack#4981](https://github.com/cozystack/cozystack/issues/4981).

### Step 1: mechanisms

After step 1 platform code no longer names catalog applications. Everything still lives in one tree and ships as one release, so each change can land and be reverted like any other refactoring. Every mechanism goes in the same order: the mechanism lands in the platform, catalog applications switch to it, then old code is removed. If removing old code would break an upgrade, it is removed one minor release later.

**1. Backup driver SDK** (place 1).
- Today the backup controller has a method per application kind, imports Go types of the operators and defaults to Velero.
- Each driver becomes own controller in its catalog, keyed by `strategyRef` (group and kind). The framework reconciles only its own strategy kinds. A foreign kind the API server knows is skipped, because its driver handles it. An unknown kind ends the job with "driver not installed".
- The shared code moves to `pkg/backups/`: the `BackupClass` resolver, credential projection, status and Job helpers. Code under `internal/` cannot be imported from another Go module, so this move is needed before step 3.
- `cozy-default` is assembled from `BackupClass` fragments labelled `backups.cozystack.io/class=cozy-default`, each one shipped by the catalog that owns the application. The system bucket and its credentials become part of K2.
- The strategy group `strategy.backups.cozystack.io` stays, so stored `Backup` objects and `cozy-default` do not migrate.
- Light drivers can be templates of the generic `Job` strategy instead of Go code. For that the `Job` strategy needs an artifact URI template, a readiness precondition, a cleanup template and a deadline. ClickHouse fits today. Redis, RabbitMQ and Kafka fit after those extensions. CNPG, PSMDB, MariaDB and FoundationDB stay Go drivers.
- Done when: the backup controller imports no operator types and has no `case` on an application kind.

**2. Tenant module list** (place 3).
- Today the tenant chart has a `computeplane: true` switch and renders the computeplane `HelmRelease` itself, with an error message naming the iaas bundle.
- The tenant spec gets a generic list, `modules: {<name>: {enabled: true}}`. For each enabled module the tenant chart renders a `HelmRelease` with the chart reference the tenant values carry for that module. The controller from item 8 writes that reference for each installed module, the way the core derives it (item 10). The chart needs no `lookup`. Release name is the module name, so the existing `computeplane` release keeps its name and is not reinstalled.
- A migration moves `spec.computeplane` to `spec.modules.computeplane.enabled`. It builds on [community#39](https://github.com/cozystack/community/pull/39), which makes modules declarative capabilities of an `ApplicationDefinition`.
- Done when: the tenant chart names no module that a catalog ships.

**3. Declarative option sources** (place 4).
- Today `cozystack-api` fills form dropdowns from 14 providers written in Go. Eight of them read virtualization objects: GPUs, instance types and profiles, networks, images, VM disks and the two VM import lists.
- A catalog ships a cluster-scoped object that describes a source. There are two kinds. A **list** source names a GVR and gives a scope, a prefix to trim, a label template and a CEL filter. A **published** source reads a ConfigMap with a given label, and the owning controller fills it. VM import already uses this pattern.
- Only the `appkind` provider stays in Go. Only administrators install sources, because the API's privileged client executes them.
- The `cozystack-api` ClusterRole becomes an aggregated role, and virtualization adds own read rules to it.
- Done when: `providers.go` and the `cozystack-api` RBAC name no virtualization API group.

**4. Application warnings through admission policy** (place 5).
- Today `rest.go` warns about removed Kubernetes fields, Postgres and MariaDB passwords and MariaDB user name collisions, and enforces name length limits for Kubernetes and Kafka.
- The name limits are removed after the upgrade window has passed. Both applications already declare `x-cozystack-name`.
- Warnings move into the application charts as `ValidatingAdmissionPolicy` objects with `validationActions: [Warn]`. `cozystack-api` already runs the validating admission chain from its REST handlers. We have not verified yet whether CEL can type the schemaless `spec` of an aggregated `Application`. If it cannot, fallback is a schema extension next to `x-cozystack-name`.
- Done when: `rest.go` has no code for a specific kind other than `Tenant`.

**5. WorkloadMonitor readiness gates** (place 6).
- Today the controller knows the CDI `DataVolume` kind and its phases, watches it and holds RBAC for `cdi.kubevirt.io`. It also copies VM instance type annotations into labels.
- `WorkloadMonitor` gets a list of gates: a GVK, a selector and a CEL readiness condition, same form as `release.healthCheckExprs` in the AD. The vm-disk chart declares the `DataVolume` gate itself.
- The vm-instance chart sets those labels on its `WorkloadMonitor` itself, and annotation copying is removed.
- Done when: the controller imports no CDI types and holds no CDI RBAC.

**6. Dashboard from AD data** (place 8). Step 0 already moves icons, category order and visibility into the AD.
- `spec.dashboard.tabs` becomes a typed list. A `websocketConsole` tab has a protocol (`vnc` or `serial`) and a path. A `subresourceAction` has a path and verbs. A `resourceList` has a GVR and a selector.
- `VMInstance` declares its VNC and serial consoles and its power actions in own AD. noVNC and xterm stay in the console as generic renderers, with no KubeVirt names in them.
- A sidebar section is a cluster-scoped object with a title, a list of CRDs and a placement, installed by the catalog. VM import uses it, and backups move to it as well, since both are a list plus a form built from the CRD schema.
- An `x-cozystack-widget` schema extension replaces the special case for any top-level field named `source`.
- Each catalog aggregates into the tenant roles the rights to read its CRD schemas and use its subresources.
- Done when: the console has no `kubevirt.io` or `forklift.cozystack.io` names, and a new VM tab ships as a virtualization release with no console release.

**7. Addons through `cozystack.cluster-addons`** (place 10).
- Today the kubernetes chart installs 19 platform charts into tenant clusters by artifact names derived from its own `PackageSource`, and passes values under the wrapper chart's subchart key. Helm ignores an unknown key, so a renamed key silently drops settings like the API server address for Cilium.
- The platform publishes a source-only `PackageSource`, `cozystack.cluster-addons`, with no `install` section and stable artifact names. Tenant Kubernetes references those artifacts. The platform knows it offers addon charts, and does not know who uses them.
- Every addon artifact ships a `values.schema.json`, and every addon chart declares and tests a mode for installing into a remote cluster. Tenant Kubernetes sets the Cilium behaviour it depends on (`kubeProxyReplacement`, Gateway API, Talos settings) explicitly instead of inheriting platform defaults.
- Done when: CI renders every addon with tenant Kubernetes values against its schema and fails on an unknown key.

**8. Tenant values contract** (K2).
- The keys of `cozystack-values` get a JSON Schema, and only the keys in it reach catalog charts.
- One controller computes tenant values from a typed resource and projects them into a ConfigMap per tenant namespace, replacing the three Helm writers and the Secret. Charts keep reading `.Values._cluster` and `.Values._namespace` through `valuesFrom`. During migration both objects exist, and releases switch to the ConfigMap in one reconcile.
- A key is written only when its module is enabled. A missing key means `""` everywhere, and `cozy-lib` gets a `required` helper for charts that cannot work without it.
- Monitoring gets a stable service alias, so charts stop depending on the name a user gave the storage.

**9. Environment settings and platform requirements** (K3, replaces platform variants).
- The platform values get four settings: `host` (`talos`, `generic`, `managed`), `network.provider`, `storage.provider` and `gpu.mode`. Network and storage each take one of the providers the platform ships, or `none` to use the cluster's own CNI or CSI. `gpu.mode` replaces `bundles.iaas.gpuOperatorVariant`.
- Each provider declares the properties it gives, such as a VPC API, a stable VM address across live migration, an RWX Block storage class, volume snapshots and the pod MTU. With `none` the administrator declares them.
- Virtualization declares the properties it needs. Installation is refused with the name of the missing property, instead of today's check of the variant name.
- The size setting goes away. Minimal installation is the platform without catalogs, and the root monitoring stack is an ordinary option.
- A migration maps the seven variant names to settings. `default`, which enables no bundle, is the platform with no catalog selected. `isp-full` is `talos` with the Kube-OVN and Cilium network and LINSTOR. `-generic` changes the host to `generic`. `isp-hosted` is `managed` with network and storage `none`. `-slim` drops virtualization and root monitoring.

**10. The API watches ApplicationDefinitions** (K1).
- Today `cozystack-api` reads all ADs once at start, and the controller restarts the API Deployment when an AD changes.
- The API watches ADs and adds or removes kinds at runtime. An error in one AD disables that kind with a status condition, and step 0 already stops it from taking down the whole API. The controller stops restarting the API.
- The core derives `chartRef` from the source and component instead of each AD copying the artifact name formula.
- Done when: installing or upgrading a catalog changes the served kinds without restarting `cozystack-api`.

### Step 2: one artifact for the platform and one per catalog

After step 2 the monorepo ships the platform and each catalog as separate artifacts with own versions, and CI checks them against each other. Repositories don't change yet.

**Build.**
- The build pushes one OCI artifact for the platform and one per catalog instead of the single `cozystack-packages`. Each artifact holds its own charts and `PackageSource`s.
- Each `PackageSource` changes only its `sourceRef`, to the `OCIRepository` of the platform or its catalog. Artifact and release names do not depend on `sourceRef`, so no `HelmRelease` changes its chart reference and nothing is reinstalled.
- Each catalog artifact has the layout `cozypkg` produces and the marketplace reads, so our catalogs and third-party catalogs are connected the same way: an `OCIRepository` plus the `PackageSource`s the artifact carries.
- The platform chart stops rendering catalog `Package`s. Each catalog carries own list of `Package`s, its part of today's `bundles/iaas.yaml`, `bundles/paas.yaml` and `bundles/naas.yaml`. Which catalogs a cluster installs is a list of catalog references in the platform values, instead of names in the platform chart.
- Catalog charts pin `cozy-lib` at a platform version (K5) instead of the symlink.

**Moves.** Code that step 1 decoupled moves into the directory of its catalog:
- backup drivers move next to their applications. Velero and the VM driver go to virtualization
- application roles, already in the `*-rd` charts after step 0
- VM import: the API, the controller, the binary and the console section
- the charts used only inside tenant clusters (`kubevirt-csi-node`, `proxmox-csi`, `vsnap-crd`, `coredns`). Virtualization also carries own copies of the `gpu-operator` and `hami` charts for tenant clusters, the host copies stay in the platform
- the GPU device wiring of KubeVirt (`permittedHostDevices` and its defaults), from `bundles/iaas.yaml` into the KubeVirt chart

**Checks and release.**
- The boundary lint fails CI on any forbidden edge outside the exception list, old ones included.
- A CI job per catalog renders and installs it against platform N−1, N and N+1, and fails on an unknown key, a missing object or a CRD version mismatch.
- e2e splits. A platform change runs the platform suites. A catalog change runs that catalog's suites against released platform versions.
- A release is the signed manifest: the platform version, the catalog versions tested together, artifact digests and migration order.

**Upgrade of an existing cluster.** The first release with separate artifacts carries a migration ([community#58](https://github.com/cozystack/community/pull/58)) that creates the catalog `OCIRepository` objects, repoints existing `PackageSource`s and converts `bundles.*.enabled` into catalog selection. `Package` and `HelmRelease` objects keep their names. Rolling back means pointing sources back at the single artifact and converting catalog selection back.

**Done when:** the platform artifact installs and passes its e2e with no catalog installed, and each catalog is green against platform N−1, N and N+1.

### Step 3: separate repositories

We start moving a catalog out only when all of these hold:
1. the boundary lint reports zero forbidden edges outside the reviewed exception list
2. contracts K1–K6 had no removals for two minor platform releases
3. the catalog's CI has been green against released platform versions for two minor releases without synchronized PRs
4. the catalog builds no images from the root Go module

Moving a catalog then changes only where its code lives. What users install stays the same:
- the catalog's directory moves with its history to own repository in the `cozystack` organization
- its Go code (backup drivers, VM import) becomes its own module, and its images build in that repository
- the catalog's CI pulls released platform artifacts and the contract schemas the platform publishes
- artifact names and `PackageSource` names stay the same, so clusters see a new artifact digest and nothing else

Third-party and vendor catalogs are separate repositories from day one.

## Open questions

- Whether two minor releases is the right threshold for step 3 criteria 2 and 3.

## Alternatives considered

### Shape

**Move code into separate repositories now, without the refactoring.** Every forbidden edge becomes a dependency across repositories:
- a backup change in an application needs a core release
- a new application needs a platform release for tenant RBAC
- a new VM tab needs a console release

We would pay the full cost of the split (CI, versions, release sync) and reach none of the goals. Rejected.

**Catalog only, as in [community#43](https://github.com/cozystack/community/pull/43).** Moves managed services out but keeps VMs and tenant Kubernetes in the platform. It also treats the contract as API types only, so backups, RBAC, values and monitoring stay coupled. Our line matches [#43](https://github.com/cozystack/community/pull/43)'s for the platform and extends it to virtualization.

**Platform with virtualization | catalog.** Forces VMs on everyone who needs only managed services. Rejected.

**Seven parts** (core, base, tenancy, iaas, paas, naas, addons). base and tenancy always ship together. These boundaries serve no goal and each one needs a contract. Rejected.

**Layers with the core as its own repository** (core | system | application layers). The core | system contract is the hardest one and has no consumer today. Kept as a later step under the criteria in [Rules](#rules).

### Names

**iaas and paas.** Service-model names describe how a provider sells a service, not what a catalog contains, and they break at the edges: `vpn` and `tcp-balancer` are not PaaS, Harbor and OpenBao are closer to SaaS. They also collide with dashboard categories IaaS, PaaS and NaaS, which stay free data in the AD.

**saas** for managed services. SaaS is finished end-user software, and a managed Postgres is not that.

**compute** for virtualization. Pairs badly with services, since compute is a service too.

**infra**, **managed**. infra also describes the network and storage of the platform. managed also describes managed Kubernetes, which lives in virtualization.

**layer**, **module**, **bundle** for the unit. layer implies a stack, while the two catalogs sit side by side. module and bundle are already taken.
