# Image overrides for every image the platform runs

- **Title:** `Image overrides for every image the platform runs`
- **Author(s):** `@fl0pp5`

## Overview

`registries.imageOverrides` lets a platform administrator replace an image, matched by its name, with another name, tag and digest. It is applied today as a kustomize images post-renderer on every HelmRelease, and to Flux through the installer. That rewrites only `image` in `containers[]` and `initContainers[]`. An inventory of the tree ([inventory.md](./inventory.md)) finds 114 of 366 image references in 32 charts elsewhere: custom resource fields, operator `env` and `args`, ConfigMaps, gzip-packed CAPI provider components, and images an operator picks by itself. A second pass finds more: sidecars a webhook injects into tenant pods, the kubelet image in the machine config of tenant Talos nodes, and platform migrations that write image references into live objects during an upgrade. A third, over every chart at every version and variant and over the Go code, adds Go code that creates another operator's custom resource without naming its image (the FoundationDB backup driver), the addons Cozystack installs into tenant clusters, and Strimzi's per-version image map held in a single env variable. Each of them still pulls the upstream image when an override is set.

This proposal makes the table reach every reference a Cozystack-installed workload runs, and makes it checkable:

- the table is applied at render time wherever a reference lives, through one of four routes chosen by where the reference is rendered;
- an entry can target one version of an image, because many images run at several versions at once;
- every release carries the list of images it runs, and the platform validates the table against it;
- prefix rules expand into entries, so moving a whole image set does not mean listing every image;
- an optional list of allowed registries turns "is everything covered" into a check before install.

It also names what a table cannot do: a few operators take an image without a digest field, or build its tag themselves. Those need changes upstream or in forks Cozystack maintains, listed in [Known limitations](#known-limitations).

## Scope and related proposals

- **Implemented so far:** `cozystack/cozystack#5136` (PR, `registries.imageOverrides` and the post-renderer on every release) and `cozystack/cozystack#5137` (PR, `cozystackOperator.fluxImageOverrides` for the Flux images the installer deploys).
- **Proof of concept:** every route, helper and check below was implemented on top of those two PRs for at least one chart per mechanism and run on the e2e sandbox; [Proof of concept](#proof-of-concept) lists what it confirmed and what it changed in this design.
- **`cozystack/community#21`** (self-hosted in-cluster registry for air-gapped Cozystack) proposes the *mirror-as-is* model: the same names and digests served from another host through Talos `machine.registries.mirrors`. Its review asks to weigh a *single-namespace rehost* model against it. This proposal is what makes rehosting possible at all: a mirror cannot rename or repin, an override can. The two compose. Mirrors serve the bundle Cozystack ships; overrides serve an image set rebuilt under other names, such as a distribution's registry or a corporate registry with its own naming. Parts of #21's review are answered here: the inventory of images operators choose at runtime is [inventory.md](./inventory.md), the per-release image list of [§5](#5-the-release-image-list) is the input its image-to-digest manifest needs, and the allowed-registries check of [§8](#8-allowed-registries) is a preflight for air-gapped installs.
- **Supporting material:** [inventory.md](./inventory.md) (where references live), [routes.md](./routes.md) (how the table can reach each of them, chart by chart).
- **Follow-ups outside this repository:** an optional `spec.image` in `cozystack/etcd-operator`; digest support in the FoundationDB operator's `imageConfigs` and in Kamaji's control-plane and addon settings (see [Known limitations](#known-limitations)).
- **Deferred:** a report of images running from outside the allowed registries on a live cluster; the source of the platform chart artifact (`cozystackOperator.platformSourceUrl`, raised in #21), which is already a value and not an image; documentation of the settings in `cozystack/website`.

All repository paths below refer to `cozystack/cozystack`.

## Context

The table is a list of kustomize `images` entries in the platform values:

```yaml
registries:
  imageOverrides:
    - name: ghcr.io/cozystack/cozystack/kubeovn
      newName: registry.example.org/os/cozystack-kube-ovn
      newTag: "1.15.26"
      digest: sha256:f80b60d4523dcdecb225c3113bcbd0eedc45b4d2b46f87c0ccf4f14d09a4a35f
```

The platform chart validates it and writes it into the `cozystack-values` Secret as `_cluster.image-overrides`. `cozystack-operator`, `cozystack-api` and the ApplicationDefinition reconciler read it through `pkg/imageoverrides` and set it as a post-renderer on the HelmReleases they build; charts that render a HelmRelease set it through `cozy-lib.postRenderers` (`packages/library/cozy-lib/templates/_imageoverrides.tpl`), and `hack/helmrelease-postrenderers_test.bats` fails on a HelmRelease template that does not call it.

cozy-lib also has `cozy-lib.image` (`packages/library/cozy-lib/templates/_cozyconfig.tpl`), which prefixes `_cluster.images-registry` to a reference and has 17 call sites. Nothing in the platform writes `images-registry`, as noted in the review of #21, so the helper is a no-op today.

`hack/lib/image-refs.sh` enumerates the references Cozystack pins in its own tree, for the release tooling that retags, mirrors and verifies them. It does not cover the defaults of vendored charts or images operators choose, and answers a different question from this proposal: where to rewrite at release, not what a cluster will run. `hack/collect-images.sh` answers the runtime side: after an e2e run it lists every image the sandbox's Talos nodes pulled, by the digest each pull resolved to.

### The problem

A registry mirror serves an image only under the name and digest it was pinned with. An image set rebuilt under other names can only be reached by an override, and an air-gapped cluster needs every image the platform runs to be in that set. An override that covers 69% of references does not make a cluster air-gapped. The cluster installs, and later a tenant's PostgreSQL, Kafka or VM fails with an image pull error, because CloudNativePG reads `Cluster.spec.imageName`, Strimzi reads its operator env, KubeVirt derives `virt-launcher` from the operator image, and the post-renderer touches none of them.

The inventory sorts the 114 references into classes by where they live:

| Class | Where | Examples |
|---|---|---|
| 1 | Whole reference in a CR field rendered by a Cozystack chart | CNPG `imageName`, `MariaDB.spec.image`, `RedisFailover`, `PerconaServerMongoDB`, `KamajiControlPlane` extra containers |
| 2 | Operator `env` or `args` | strimzi, CDI, forklift, KubeVirt, CNPG, cert-manager, kamaji, kube-ovn, gpu-operator, openebs, smee, etcd-operator |
| 3 | ConfigMap read by an operator | mariadb-operator, barman plugin, kube-ovn, piraeus |
| 4 | Reference split into `repository`/`image`/`version` in a CR | gpu-operator `ClusterPolicy`, 19 components |
| 5 | gzip in a ConfigMap `binaryData` | CAPI provider components, 7 archives in 6 charts |
| 6 | Not rendered: the operator falls back to a built-in default | VictoriaMetrics, OpenSearch, Strimzi `Kafka`, FoundationDB, Kamaji control plane and addons, redis sentinel, etcd, MariaDB agents and MaxScale, Percona log collector, gpu-operator init container |
| 7 | Derived by the operator from another image | KubeVirt components |
| 8 | Outside Cozystack | Talos static pods, CoreDNS, the sandbox (`pause`) image and the kubelet of the management cluster's nodes, Talos installer, disk images over HTTP |
| 9 | Written into live objects by a platform migration during an upgrade | migrations 37 (CNPG `imageName` of the system databases), 50 (`etcd-migrate --agent-image`), 54 (`RedisFailover` redis and sentinel) |
| 2, injected | Injected by a webhook into tenant pods, from the operator's env | telepresence agent, openbao agent injector (disabled by default) |

Five further findings shape the design.

**A value in a vendored chart is not a route by itself.** The table is known only at install time. A wrapper's `values.yaml` is static, a subchart's `tpl` sees its own values and `global` but not `_cluster`, and Helm gives a parent chart no way to hand a computed value to a subchart. Of the vendored charts in classes 2 to 4 none passes its image values through `tpl`. [routes.md](./routes.md) lists, per chart, which route can reach each reference.

**Operators repeat their own image.** kamaji `--migrate-image`, kube-ovn `--image`, CNPG `OPERATOR_IMAGE_NAME`, the Strimzi entity operator and Kafka init images, `MARIADB_OPERATOR_IMAGE` and etcd-operator `OPERATOR_IMAGE` carry the image of a container of the same release, and the operator uses that copy for the pods it starts. CNPG runs it as the bootstrap init container of every PostgreSQL pod. Today's post-renderer rewrites the container and leaves the copy, so an override of such an image splits the operator from its operands. An operator that reads the image from its own Pod instead follows an override by itself: the Percona MongoDB operator v1.22.0 takes `initImage` from the first container of the Pod named by its `HOSTNAME`.

**Many images run at several versions at once.** `foundationdb/fdb-kubernetes-monitor` runs as two init containers of one Deployment at `7.3.63` and `7.4.1`; `ghcr.io/cloudnative-pg/postgresql` runs at six major versions chosen per tenant; Strimzi selects a Kafka image per Kafka version. A table keyed by name alone applies `newTag` or `digest` to every version: a PostgreSQL 16 cluster would be moved to a 17 image and fail to start on its data. Only `newName` is safe for such images today.

**Some templates reason about image names.** The postgres chart refuses a flavor change by comparing the last path component of the image in the live `Cluster` with the one it is about to render (`packages/apps/postgres/templates/_versions.tpl`). The CNPG clusters of keycloak, harbor, monitoring (`alerta-db`, `grafana-db`) and seaweedfs-db carry the live `imageName` forward and append `-standard-trixie` to a bare `major.minor` tag. A rename breaks both: in a prototype, an entry renaming `postgresql` to `cozystack-postgresql` made the flavor guard refuse the upgrade of every existing cluster.

**Migrations write image references.** The platform migrations in `packages/core/platform/images/migrations` run during an upgrade and patch live objects directly: migration 37 sets `imageName` of the system CNPG clusters to `ghcr.io/cloudnative-pg/postgresql:17.7-standard-trixie`, migration 54 sets `redis:6.2.6-alpine` into empty `redis.image` and `sentinel.image` of live `RedisFailover` objects, and migration 50 runs `etcd-migrate` with `--agent-image` defaulting to `ghcr.io/cozystack/etcd-operator:v0.5.2`, without a digest and older than the etcd-operator the platform installs. None of them goes through Helm, so no route reaches them.

## Goals

- One table applies to every image of classes 1 to 7 and 9 that a Cozystack-installed workload runs, and to the images of tenant Kubernetes nodes, with the same matching and replacement rules wherever it is applied, wherever the target field can carry the result.
- An entry can target one version of an image by tag or by digest.
- With an empty table and no rules, every chart renders byte-for-byte as it does today.
- Every release carries the list of the images it runs, and the platform refuses a table that targets a version the release does not run or that would give a different result applied twice.
- An administrator can move a whole image set by prefix without listing each image.
- An administrator can require that every image resolves to an allowed registry, and the platform refuses to install otherwise.
- A reference the table cannot reach fails CI, unless it is on an allowlist with a reason.
- What the table cannot reach for lack of a field upstream is listed, with the change that would close it.

### Non-goals

- Class 8: Talos static pods, CoreDNS in the Talos bootstrap, the sandbox image and the kubelet of the management cluster's nodes, the Talos installer and disk images fetched over HTTP (`vm-default-images`, `kubernetes-worker-image`, whose URLs are already values). They are configured through Talos machine config, registry mirrors (which with `overridePath` can also move a path prefix, but not rename one image) and the existing `talos.installerRepository` value, and #21 covers them. They appear in the allowed-registries check as declared exceptions.
- Images a tenant supplies: VM `containerDisk` images and the workloads tenants run. They are not images of the platform.
- Artifacts that are not images: the vulnerability databases Harbor's Trivy downloads as OCI artifacts, and the packages artifact the platform is installed from. Each is a setting of its own program (`trivy.dbRepository`, `cozystackOperator.platformSourceUrl`), not a field of a workload, and stays one.
- Images inside a tenant Kubernetes cluster other than the addons Cozystack installs.
- Replacing the post-renderer. It stays as the route for container images.
- Distributing images. Copying an image set to a registry is the job of the tooling discussed in #21.
- A general rewrite language (regular expressions, tag transformations). Anything beyond name, version selector and prefix expands into ordinary entries before it reaches a route.

## Design

### 1. The table

An entry gains an optional selector:

```yaml
registries:
  imageOverrides:
    # every version of the image
    - name: ghcr.io/cloudnative-pg/postgresql
      newName: registry.example.org/os/postgresql
    # one version, rebuilt under its own tag and digest
    - name: foundationdb/fdb-kubernetes-monitor
      match: {tag: "7.3.63"}
      newName: registry.example.org/os/fdb-kubernetes-monitor
      newTag: "7.3.63-os1"
      digest: sha256:…
```

- `name` matches the reference without tag and digest, as today. Names are not normalized: `redis` and `docker.io/library/redis` are different names, as they are for kustomize.
- `match` is optional and holds exactly one of `tag` or `digest`. With it, the entry applies only to references carrying that exact tag or digest.
- `newName` replaces the name. `newTag` replaces the tag and drops the digest. `digest` replaces the digest and drops the tag. Both together give `name:tag@digest`.
- Entries apply in table order, each to the result of the previous ones, as kustomize applies one transformer per entry.
- Two entries with the same `name` and `match` are refused, as two entries with the same `name` are today.
- The table must be **idempotent**: applied to its own result it must change nothing, because a reference can pass through two routes (§2); validation enforces it (§6). A chain where one entry renames an image to a name a later-checked entry matches is refused.

`match` reaches Flux without any change to it: the entry is passed to the kustomize post-renderer as `name: "<name>:<tag>"` or `name: "<name>@<digest>"`. kustomize matches an image with the expression `^<name>(:<tag>)?(@sha256:<digest>)?$` built from `name` without escaping it, and replaces the parts of the original reference, so a name carrying a tag or digest matches exactly that version and is rewritten like any other entry. This was checked with the kustomize the deployed helm-controller v1.5.0 builds with (`sigs.k8s.io/kustomize/api` v0.20.1, pinned there by a `replace`), end to end through krusty: an entry for `fdb-kubernetes-monitor:7.3.63` rewrote that init container and left `7.4.1` alone, an entry for `postgresql:16.11` replaced a source digest, an entry with a digest selector rewrote only that digest, and `fdb-kubernetes-monitor:7.3` matched nothing. The Flux CRD describes `name` as tag-less, so this relies on the implementation rather than on the documented contract; the parity tests of §2 pin it at the kustomize version the embedded helm-controller uses, and a Flux bump that changes it fails them.

### 2. Applying the table: four routes

Each reference is reached by the route that matches where it is rendered:

| Route | Where the reference is | How the table reaches it |
|---|---|---|
| 0 | `image` of `containers[]`/`initContainers[]` in any rendered object | kustomize post-renderer, as today |
| 1 | A template of a Cozystack chart (application or system CRs, our own operators, generated manifests) | a cozy-lib helper called in the template, reading `_cluster.image-overrides` |
| 2 | A vendored chart value that takes the reference **in parts** (registry, repository, tag, digest) | the platform chart computes the value from the table and passes it in `Package.spec.components.<c>.values`; an app chart does the same for a tenant cluster addon |
| 3 | A vendored chart value that takes the reference **as a whole string** | a patch of the vendored template, applied by `make update`, calls a cozy-lib helper with a table the platform passes under the subchart's own key |

The helpers:

- `cozy-lib.image` returns a whole reference with the table applied. It keeps its signature and its `images-registry` prefix, applied first because that is what the post-renderer sees in a container. Since nothing writes `images-registry`, its 17 call sites change only when an entry matches.
- `cozy-lib.imageParts` returns the same result as `registry`, `repository`, `tag` and `digest`, for CR fields and values that take parts.
- `cozy-lib.imageName` applies only `newName`, for fields that hold a name without a tag, such as mariadb-operator's `RELATED_IMAGE_MARIADB_NAME`.
- `cozy-lib.imageWith` takes the table as an argument instead of reading it from the values, for patched vendored templates (route 3).
- `cozy-lib.imageOverrideEntry` returns what the entries without `match` do to a name whose tag the caller does not know, and `cozy-lib.imageOverrideValues` the same as the `repository`, `tag` and `digest` values a vendored chart takes, for route 2; the platform chart and the kubernetes chart (for a tenant cluster's addons) both call it, so the two cannot disagree.
- `cozy-lib.cnpg.image` renders the image of a CloudNativePG cluster a chart keeps across upgrades (§4).

Each chart that calls a helper vendors cozy-lib as a link, `charts/cozy-lib`. `flux push artifact` skips symbolic links, so every PackageSource that installs such a chart declares the library in `libraries`, the platform chart's own included, and a `make update` that recreates `charts/` puts the link back; `hack/packagesource-libraries_test.bats` holds both, since a chart that misses either renders in CI and fails only once installed.

Route 2 needs no change to the vendored chart, but the platform chart does not know the vendored defaults, so it can only apply entries without `match`, and only where the value takes parts and an unset part keeps the chart's default. That is why whole-string values take route 3, and why a `match` entry for an image reached only by route 2 is refused by validation (§6). How a chart renders a partial value differs: given only a digest, cert-manager renders `repository@digest` as kustomize does, while another chart may render `repository:tag@digest`. Each chart on route 2 therefore gets a helm-unittest of its own.

Route 3 knows the default because the reference comes from the chart's own values, and a patch that no longer applies stops `make update` instead of failing silently. Patches are already how the repository changes a vendored chart (`docs/agents/overview.md`: do not edit upstream charts directly, use `patches/`). The table reaches a patched subchart under its own key, `<subchart>.cozystackImageOverrides`, set by the platform chart only in the component values of packages that carry such a patch. It does not go into `global`: `global` would reach every chart through `cozystack-values`, and rendering every chart with a `global.cozystack` key broke one of them, smee in bootbox, whose template reads `.Values.global.rbac.type` whenever `global` is non-empty.

A reference can pass through two routes. A route 1 or route 2 result that lands in a container is rewritten again by the post-renderer, and the 17 existing `cozy-lib.image` call sites are all of that kind. Applying an idempotent table twice gives the result of applying it once; a non-idempotent one does not (`postgresql` → `registry.example.org/postgresql` on one pass, → `registry.example.org/pg-legacy` on two, with a chain in the wrong order). Validation therefore requires idempotency (§6).

Go code that creates another operator's custom resource is route 1 written in Go: it reads the table through `pkg/imageoverrides.Load`, as the controllers that build HelmReleases already do, and applies it with `pkg/imageoverrides.Apply`. The FoundationDB backup driver is the one such place: it creates a `FoundationDBBackup` that names no image, and now names the agents' image in `imageConfigs` when the table changes it.

One rule set, several implementations: a shared fixture of references and tables, including `match`, feeds a Go test that runs krusty at the helm-controller's kustomize version, the helm-unittest suites of the helpers, and the route 2 helper. The proof of concept's fixture holds 413 cases, every one checked against krusty and against the helpers.

### 3. Routing each class

| Class | Route | Change |
|---|---|---|
| 1 | 1 | Wrap the CR field in `cozy-lib.image`. |
| 2 and 3, our templates | 1 | etcd-operator `OPERATOR_IMAGE` with its container image, and `ETCD_IMAGE_REPOSITORY` (below); for CDI, KubeVirt and forklift `make update` emits a helper call for each env image instead of the literal. KubeVirt virt-operator v1.9.0 reads a whole reference per component from its env (`VIRT_API_IMAGE`, `VIRT_LAUNCHER_IMAGE` and ten more, `pkg/virt-operator/util/config.go`), which moves class 7 into class 2: `make update` replaces the `VIRT_OPERATOR_IMAGE` entry of the vendored manifest with a helper that renders it through the table, and adds the variable of a component only when the table changes that component's image or the operator's. Without the second condition, renaming the operator alone would move every component into the operator's new registry. An operator image with a digest is passed as `name@digest`, the form OLM uses, so the version still comes from `KUBEVIRT_VERSION`. Without a table nothing is added, and the install strategy, whose ID covers these images, does not change. |
| 2 to 4, vendored, parts | 2 | cert-manager, on the management cluster and, through the kubernetes chart, in tenant clusters, where a solver image the tenant sets in `valuesOverride` stays as written; local-storage; CNPG; barman. |
| 2 to 4, vendored, whole string or whole entry | 3 | kube-ovn `--image` and the NAT gateway ConfigMap: the chart builds both from one `global.registry.address`, so values in parts could not move them to different registries; the patch passes each through the helper, `match` included, and leaves the containers to the post-renderer. Strimzi: every reference the chart renders, the operator's own image and its copies, the entity operator and init defaults and each line of the per-Kafka-version maps, comes from one template, `strimzi.image`, which the patch passes through the helper, so each Kafka version can be overridden on its own and the `Kafka` CR needs no image. gpu-operator: the patch renders `ClusterPolicy` and `NVIDIADriver` with a copy of the values in which the triples the table changes are rewritten, so new components need no patch change; a result with a digest is written as `version: sha256:…`, which v26.3.1 joins as `repository/image@version`, and `DRIVER_MANAGER_IMAGE` is the operator's OLM fallback, unused once the triple is set. piraeus: v2.10.2 takes `RELATED_IMAGE_<component>_<image>` from its env as a whole reference before it builds one from `image-config`, an OS-specific image of a `match` rule included; the patch adds that variable for each image the table changes, reading the defaults from the files upstream generates `image-config` from, which `make update` keeps beside the chart. smee `tinkWorkerImage`; mariadb-operator `config.*Image`, `config.mariadbImageName` through `cozy-lib.imageName`, and `MARIADB_OPERATOR_IMAGE`, which the upstream `image` template builds. kamaji `--migrate-image` is a static string in our own wrapper `values.yaml`; the patch derives it from the container image value instead. |
| 5 | 1 | `spec.deployment.containers[].imageUrl` on every CAPI provider CR, as capk already sets. Each of the seven archives holds one Deployment with one container, `manager`, so one `imageUrl` per provider covers it; `capi-providers-bootstrap` carries two providers. The image inside the gzip stays as shipped and goes unused. Where the default lives only in the archive, the chart records it with its digest, names `imageUrl` only when the table changes it, and a test holds the record to the archive. |
| 6 | 1 | Name the image in our CR wherever the CRD has a field, and only when the table changes it: the chart records the operator's default, with its digest and held to the operator release by a test or by `make update`, passes it through the helper and writes the field when the result differs, so without a table the operator keeps choosing. VictoriaMetrics `image` on every component and `configReloaderImage` on VMAgent, VMAlert, VMAlertmanager and VMAuth; OpenSearch `general.image`, `initHelper.image`, `dashboards.image`; RedisFailover `sentinel.image`; FoundationDB `mainContainer.imageConfigs` and `sidecarContainer.imageConfigs` on `FoundationDBCluster` and the in-chart `FoundationDBBackup`, one item for the cluster's version, with `version` set so an upgrade to another version is not held on it; MariaDB `galera.agent.image`, `galera.initContainer.image`, `replication.agent.image`, `replication.initContainer.image`, `maxScale.image` and its exporter, today filled from the operator's `RELATED_IMAGE_*` and its own image; Percona MongoDB `logcollector.image` where enabled (`initImage` needs nothing, see Context); Kamaji control-plane `registry` and `containerImageName`, `addons.konnectivity.{agent,server}.image`, `addons.coreDNS` and `addons.kubeProxy` `imageRepository`/`imageTag`; control-plane tags follow `version`. Some operators write their own default into the CR when it is created: mariadb-operator fills `replication.agent.image` and `replication.initContainer.image` from its env, and on the sandbox those fields kept the image of that moment after the table was removed, because nothing renders them. Only a field the chart sets lets a later change of the table, or its removal, reach the running image; whether mariadb-operator refills such a field from its current env once the chart stops setting it is still to be checked. |
| 8, tenant nodes | 1 | The machine config of tenant Talos nodes is ours (`packages/apps/kubernetes-nodes/templates/_talosconfigtemplate.tpl`): `machine.kubelet.image` (`ghcr.io/siderolabs/kubelet:<version>`, tag following the Kubernetes version) goes through `cozy-lib.image`. The same helper renders the config the `talos-reconcile` Job applies to live nodes, and its content hash names the `TalosConfigTemplate`, so a table that renames the kubelet rolls the pool's workers, as `registryMirrors` does; a pool of one node has no ready node while its worker is replaced, about ten minutes on the sandbox. The template can also carry the CRI sandbox image for tenant nodes. Only the management cluster's nodes stay in class 8. |
| 2, injected | 2 | telepresence: the traffic-manager injects an agent built from `AGENT_REGISTRY` and, when set, `AGENT_IMAGE`; the chart sets only the registry today. The open-source traffic-manager then uses `<registry>/tel2:<version>`, but the chart runs the commercial `ambassador-telepresence-manager`, whose agent image is chosen behind an extension point (`WithAgentImageRetrieverFunc`); which image that is has to be read from a cluster running it (`Using traffic-agent image` in its log) before `agent.image.{registry,name,tag}` can be set from the table. The openbao agent injector, if enabled, takes its agent image as parts. |
| 9 | migrations | See below. |
| 6, etcd | 1 | `EtcdCluster` and `EtcdMember` have no image field, but etcd-operator v0.5.6 takes an operator-wide repository (`--etcd-image-repository`, env `ETCD_IMAGE_REPOSITORY`, default `quay.io/coreos/etcd`) and runs every member as `<repository>:v<spec.version>`. Our chart sets it through `cozy-lib.imageName`, which renames and keeps the tags, only when the table changes the name. The operator builds a member's pod only when it creates it and does not compare the image of a live pod (`ensurePod`, `controllers/etcdmember_controller.go`), as its documentation says of `spec.version`, so a change of the table, in either direction, reaches a member only when the member is recreated; on the sandbox the three members of a tenant's etcd stayed on the old image after the table was removed. Tag and digest: see [Known limitations](#known-limitations). |

Setting an operator's own image at its source keeps the container and its copies in `env` and `args` together, which ends the split described in Context. For piraeus, `LinstorCluster.spec.repository` sets a base registry for every image the operator deploys; it can only move the registry, and the operator's own env makes it unnecessary.

A migration that writes an image reference writes the one the table gives it. The table it has to follow is the one the platform render carries, not the `cozystack-values` Secret: the Secret is a resource of the same release, and Helm applies it only after the `pre-upgrade` hook that runs the migrations has finished, so the hook would read the previous table, or none on the upgrade that introduces it. The hook's Job template renders with the new values and the helpers, so it passes each image a migration writes through the table and sets the result as an env variable, only when the table changes it; each migration falls back to its own literal otherwise (`CNPG_SYSTEM_IMAGE` for migration 37, with `CNPG_SYSTEM_IMAGE_MAJOR` for the annotation of §4 when the tag no longer shows the major version; `ETCD_OPERATOR_IMAGE`, which migration 50 already reads, for its `--agent-image`; `REDIS_PIN_IMAGE` for migration 54). A test holds the literals of the hook to those of the scripts. A reference a migration copies from a live object, such as the bare tag migration 37 completes, cannot be computed at render time; the next render of its chart applies the table to it. A new migration that writes an image has to do the same, and a check over the migration scripts flags a literal image reference in one.

Strimzi's values take one tag and one digest per image key for all Kafka versions, and kube-ovn takes one registry for all its images, so through route 2 neither could honour `match`, and kube-ovn could not even put two of its images in different registries. Both have a single template that renders every reference, which makes route 3 a patch of a few lines and lifts both restrictions.

### 4. Templates that read image names

A template that compares or rewrites an image name must do so on names the table may have changed:

- the postgres chart records the flavor in a `postgres.cozystack.io/flavor` annotation on the `Cluster`, set only when the image is renamed, and its flavor guard and the backup controller read the annotation before they classify a name. Without a table the render does not change, and renaming the image of a live cluster passes the guard;
- the CNPG clusters of keycloak, harbor, `alerta-db`, `grafana-db` and seaweedfs-db render their image through `cozy-lib.cnpg.image`, which keeps only the decision the live image is read for, the major version. A cluster on the chart's major version, or none yet, gets the chart's image through the table; one on another major version keeps its image, with the table applied. A renamed image may carry a tag that does not show the major version, after which the cluster would look like another one and stay on its image through every chart upgrade, so the helper writes the version into a `postgres.cozystack.io/major` annotation when the tag hides it and reads the annotation before the tag.

The CI guard of §9 cannot see these, because they only differ when a live object exists; each gets a helm-unittest with a mocked live object and a renaming table.

### 5. The release image list

The render of every chart that the guard (§9) performs produces `images.yaml`: every reference the release renders, with the chart, object and route that reaches it. Charts with supported versions are rendered once per version (for example every entry of `packages/apps/postgres/files/versions.yaml`), so the list holds every tag a tenant can select.

`images.yaml` is not committed. It holds first-party references, whose digests every image build restamps, and `hack/promote-rewrite-tags.sh` fails a promotion on any file carrying the release-candidate version in reference position outside `charts/` and Markdown. Declaring it in `IMAGE_REF_EXTRA_FILES` would not help, since no single producer stamps it. It is generated into the platform chart when the packages artifact is built, and again after the tag rewrite of a promotion, so it always describes the tree it ships in.

The list covers what the release renders. An image an operator chooses at runtime without a CR field is not in it until its class moves to route 1; those are the allowlist entries of the guard, and they are listed as known exceptions in the allowed-registries check.

A field that names an image only when the table changes it (class 6) renders nothing without a table, so the list is produced from a render with a probe `images-registry`: `cozy-lib.image` prefixes it to every reference it handles, which makes those fields appear and marks what a helper reached. Routes 2 and 3 do not see `_cluster` and are read from the objects that carry them. Each entry records its route, and `joinsTag` marks a reference an operator rebuilds as `repository:tag`, which cannot express a digest without a tag (FoundationDB `imageConfigs`, `VMAgent.spec.image`).

### 6. Validating the table against the release

The platform chart checks the table, after rule expansion, against `images.yaml` on every install and upgrade:

- an entry with `match` that matches no reference in the list is refused, so an entry pinned to a version a new release no longer runs stops the upgrade instead of letting that image fall back to upstream;
- an entry with `match` for an image that only route 2 reaches is refused, because that route cannot honour it;
- a table that changes any listed reference when applied to its own result is refused, naming the reference and both results;
- an entry that leaves a `joinsTag` reference with a digest and no tag is refused, since the operator cannot build it;
- an entry without `match` that matches nothing produces a warning, since it is most likely a misspelt name.
- an entry without `match` that sets `newTag` or `digest` for a name the list holds under more than one tag produces a warning naming those tags. Such an entry moves every version of the name onto one tag or digest, which is what a rebuilt set that keeps a single version wants, and wrong where one name stands for different images. The proof of concept ran into it: VictoriaMetrics runs its operator as `victoriametrics/operator:v0.68.4` and its config reloader as `victoriametrics/operator:config-reloader-v0.68.4`, and an entry meant for the reloader, written without `match`, put the operator onto the reloader image as well, which then crash-looped. An entry that only sets `newName` keeps each tag and is not warned about.

### 7. Prefix rules

```yaml
registries:
  imageRewrites:
    - prefix: ghcr.io/cozystack/cozystack/
      newPrefix: registry.example.org/os/cozystack-
      dropDigest: true
```

A rule renames every image in `images.yaml` whose name starts with `prefix`, keeping the rest of the name and the tag. The platform chart expands rules into ordinary entries before writing `_cluster.image-overrides`, so no route sees anything new. An explicit entry for the same name and `match` takes precedence over an expanded one.

By default a rule keeps the digest. Every first-party reference carries one (`docs/agents/image-refs.md`), so a set rebuilt under new names would then be pulled by digests it does not have; in a prototype a rule over `ghcr.io/cozystack/cozystack/` turned `kamaji:v1.6.0@sha256:7f77…` into `registry.example.org/os/cozystack-kamaji:v1.6.0@sha256:7f77…`. `dropDigest: true` drops it, for sets rebuilt under the same tags; a set that is also pinned needs explicit entries with its digests.

A rule compares the prefix with the normalized name (`busybox` as `docker.io/library/busybox`), because the tree writes two images both ways (`busybox` and `docker.io/library/busybox`, `clastix/kubectl` and `docker.io/clastix/kubectl`) and a rule for `docker.io/` would otherwise miss half of them. This does not change entry matching: each expanded entry carries the name exactly as the tree writes it.

### 8. Allowed registries

```yaml
registries:
  allowedRegistries: [registry.example.org]
```

When set, the platform chart applies the table, after rule expansion, to every reference in `images.yaml` and refuses to install if any resulting reference is outside the list, a name without a registry counting as `docker.io`. Class 8 images and the guard's remaining allowlist are named in the error as exceptions the administrator has to cover by other means, such as Talos registry mirrors. This answers "is everything covered" before install rather than at the first image pull. In a prototype over the images of the current tree, a single rule for `ghcr.io/cozystack/cozystack/` expanded into 40 entries and the check listed the 186 references still pointing elsewhere.

### 9. CI guard

`hack/image-overrides-coverage_test.bats` renders every package with the values the platform chart would give it, so that routes 2 and 3 are visible, collects every reference, then renders again with a table that renames each of them to a sentinel registry. Any original reference left in the second render, including inside base64 and gzip payloads, fails the test with chart, object and path. Known exceptions live in an allowlist, each with its reason, and an allowlist entry that matches nothing fails as well, or a stale one would hide the next reference under its name.

The guard has to see more than one render with one set of values, or it misses what the inventory missed:

- every platform variant, and every supported version of a versioned application;
- a fixture with optional components switched on: 69 image names declared in values files appear in no render with the default values;
- charts that need a `lookup` or particular values to render (`kubernetes-nodes`, `tenant`, `vm-instance` and ten more on the shared fixture), with the mocked objects their tests already use;
- references inside multi-line strings by short Docker Hub names (`redis:6.2.6-alpine`), not only by names with a registry host.

## Proof of concept

The design above was implemented on top of #5136 and #5137 for at least one chart per mechanism the inventory found, in 25 commits, and run on the e2e sandbox.

What was built: `match` in the table and in `pkg/imageoverrides` (`Validate`, `Apply`, `Idempotent`), with the 413-case parity fixture; the cozy-lib helpers of §2; route 1 in postgres, mariadb, redis, the five system CNPG clusters, etcd-operator, VMAgent on the management cluster and in tenant clusters, CAPI core and the kubevirt infrastructure provider, and the kubelet of tenant workers; route 2 for the cert-manager solver on the management cluster and in tenant clusters; route 3 in mariadb-operator, kube-ovn, piraeus, Strimzi and gpu-operator; KubeVirt's per-component env through `make update`; FoundationDB `imageConfigs` in the chart and in the backup driver; the platform checks of §6 against a hand-generated `images.yaml`; the guard of §9 over those charts; the migration hook of §3. Every touched chart renders byte-for-byte as before without a table.

What the sandbox confirmed, with copies of the images pushed under other names and tags, and the same digests, to a second registry and a table of 27 entries:

- a `match` entry moved `fdb-kubernetes-monitor:7.3.63` and left `7.4.1` in the same Deployment;
- the copies operators keep of their own image (etcd-operator `OPERATOR_IMAGE`, `MARIADB_OPERATOR_IMAGE`, kube-ovn `--image`, the Strimzi entity operator) followed the container;
- fields named only when the table changes them appeared and were used (`VMAgent`, CAPI `imageUrl`, etcd `ETCD_IMAGE_REPOSITORY` with the tag kept from `spec.version`);
- the image of a live PostgreSQL cluster was renamed and passed the flavor guard; FoundationDB ran `<tag>@sha256:<digest>` from `imageConfigs.tag`; Strimzi rolled its brokers when the line of their Kafka version changed;
- renaming virt-operator kept every component on its own reference;
- a tenant cluster's worker pulled its kubelet from the copy, and its cert-manager solver and VMAgent followed the table;
- piraeus rolled the satellites, CSI and controllers it creates onto the copies, and LINSTOR and the data on it came through;
- removing the table and restoring it moved every reference back and forth, except where an operator sets an image only on objects it creates ([Upgrade and rollback compatibility](#upgrade-and-rollback-compatibility)).

What it changed in this design: kube-ovn, Strimzi and gpu-operator moved from route 2 to route 3, which lifted the restrictions route 2 imposed on them; piraeus uses its operator's env instead of a patch of `image-config`; class 6 names an image only when the table changes it; §4 needed the flavor and major annotations; cozy-lib has to be declared in `libraries`; the migrations take their references from the hook, because the Secret is stale while it runs; Go code that creates custom resources reads the table itself; and the warning of §6 about a name with several tags came from a table entry that crash-looped an operator.

It also turned up three bugs on main, unrelated to images being overridden, each reported separately: kube-ovn's default NAT gateway image does not exist, the in-chart `FoundationDBBackup` lacks its required `version`, and migration 37 skips a cluster without `imageName`.

## Known limitations

These remain after the design is implemented, because the field the table would have to write does not exist:

| Image | Name | Tag | Digest | Change that closes it |
|---|---|---|---|---|
| etcd | yes, `ETCD_IMAGE_REPOSITORY` for the whole operator | no, always `v<spec.version>` | no | an optional `spec.image` on `EtcdCluster`, copied into `EtcdMember` like `version` and `imagePullSecrets`, in `cozystack/etcd-operator`, which Cozystack maintains; our etcd chart then renders it through `cozy-lib.image` with the version it knows; the operator also has to roll a member whose image no longer matches, which it does not do today even for `spec.version` |
| FoundationDB cluster and backup | yes, `baseImage` | yes, `tag` per version | only with a tag: v2.30.0 builds `baseImage:tag`, and a `tag` of `<tag>@sha256:<digest>` runs (confirmed on a cluster), within the field's 100 characters; a digest without a tag cannot be expressed and is refused by validation | a digest field upstream in fdb-kubernetes-operator |
| Kamaji control plane (apiServer, controllerManager, scheduler) | yes, one `registry` for the three plus a `containerImageName` that may carry a path | no: Kamaji 26.9.4-edge builds `<registry>/<name>:<version>`; `TenantControlPlane` has a `tagSuffix` shared by the three, which `KamajiControlPlane` does not expose | no | per-component tag and digest upstream in Kamaji and its CAPI provider |
| Kamaji konnectivity, CoreDNS, kube-proxy | yes | yes, `version` or `imageTag`, which our chart sets | no field; a `@sha256:` suffix in the tag field is unverified | digest fields upstream in Kamaji |

Until these change, those images must exist in the target registry under the upstream tags, and an install that needs them repinned relies on registry mirrors with the upstream digests. A rebuilt Kamaji control plane, in particular, has to be published under the Kubernetes version tags.

Two more are cosmetic or pending a check. KubeVirt reports the tag of its operator image as its version: an operator retagged without a digest shows as `observedKubeVirtVersion` in the `KubeVirt` status and in the install strategy's annotation, which changes nothing it runs. The telepresence agent image is chosen by the commercial traffic-manager (see §3).

## User-facing changes

- `registries.imageOverrides` entries gain an optional `match` with `tag` or `digest`. Existing tables keep their meaning, except that a table that is not idempotent is now refused.
- New `registries.imageRewrites` (prefix rules, with `dropDigest`) and `registries.allowedRegistries` (install-time check), both optional.
- The platform carries `images.yaml`, the list of images a release runs, which is also what an administrator reads to write the table.
- The table now also rewrites images in CR fields, operator `env`, `args` and ConfigMaps, and the images operators would otherwise choose by default.
- `cozystackOperator.fluxImageOverrides` is unchanged. The installer runs before the platform, so it keeps its own table.

## Upgrade and rollback compatibility

With an empty table and no rules every helper returns its input, and the helm-unittest suites of every migrated chart assert an unchanged render. In the proof of concept every chart it touched, the patched vendored ones included, rendered byte-for-byte the same without a table. Existing clusters upgrade with no migration.

An existing table changes meaning in two respects. Entries now also apply to references outside containers: an entry that renamed an operator image used to split the operator from its operands; it now moves both, which is the intended result. And a table that is not idempotent, harmless while only one route applied it, is now refused. Release notes call out both.

Class 6 names an image only when the table changes it, so without a table the operator keeps choosing as today. With one, the field holds the default the chart records for the operator release Cozystack ships; a test or `make update` keeps that record in step with the operator, so an upgrade moves the image as it would have.

Renaming the image of a live PostgreSQL cluster is safe only once the templates of §4 are migrated; until then the flavor guard refuses it. The rollout orders them accordingly.

Validation of `match` entries can stop an upgrade. That is deliberate: the alternative is an upgrade that pulls from upstream on an air-gapped cluster.

Rolling back returns the references to their upstream form, which is what an empty table renders anyway.

Removing the table from a live cluster was tried on the sandbox, with the table of 27 entries, and so was restoring it. On removal the fields named only when the table changes them left the CRs (`VMAgent`, FoundationDB `imageConfigs`, CAPI `imageUrl`), the KubeVirt component env went and the install strategy was rebuilt, piraeus, kube-ovn, Strimzi and cert-manager moved back, and the postgres flavor annotation was dropped, the guard letting the rename back through. Restoring the table brought back the state from before it was removed. Two things stayed where they were in both directions, each because of its operator: the etcd members and the MariaDB agent images (§3). The data in PostgreSQL, Kafka, MariaDB, Redis, FoundationDB and LINSTOR came through both rolls.

A change of the table rolls everything it touches and takes as long as the slowest operator:

| | Removing | Restoring |
|---|---|---|
| Management cluster HelmReleases Ready | ≈7 min | ≈8 min |
| Management cluster pods moved, FoundationDB aside | ≈9 min | ≈9 min |
| Tenant cluster: worker replaced for the kubelet, addons | ≈20 min | ≈26 min |
| FoundationDB: every process replaced | ≈33 min | ≈42 min |

FoundationDB replaces its processes one at a time and waits 120 seconds after each recovery.

## Security

No new trust boundary or RBAC surface. The table, the rules and the allowed registries are set by the platform administrator in the platform Package and reach charts through `_cluster` and, for patched subcharts, through component values the platform chart sets; these are the channels other platform-wide settings use. An entry already decides which image a container runs; this proposal extends that to images in other fields, under the same validation. `allowedRegistries` adds a check, not a capability. An entry that pins a digest keeps the guarantee a digest gives; an entry or rule that keeps an upstream digest under a new name pulls by that digest, so a rebuilt image is never substituted for a pinned one silently, and `dropDigest` is an explicit choice to give that up.

## Failure and edge cases

- Invalid table, rule or allowed-registries list → the platform chart refuses it; the Go paths stop the Package or refuse the Application write, as today.
- `match` entry for a version the release no longer runs → refused at install or upgrade, naming the entry.
- `match` entry for an image only route 2 reaches → refused, naming the image and the route.
- Table that changes a reference applied to its own result → refused, naming the reference and both results.
- Entry without `match` that matches nothing → warning.
- Entry without `match` that sets `newTag` or `digest` for a name the release runs under several tags → warning naming the tags.
- Entry with a digest and no tag for an image an operator joins as `repository:tag` → refused, naming the image.
- A reference outside `allowedRegistries` after expansion → install refused, listing each reference with the chart that renders it.
- An image renamed under a live object whose template reads image names → covered by §4 and its tests; before §4 is migrated, the postgres flavor guard refuses the upgrade.
- Upstream bump moves an image to a new place → the guard fails on the bump PR; a route 3 patch that no longer applies stops `make update`.
- An operator ignores an explicit CR image for some component → the e2e check in Testing catches it; the component goes on the allowlist with its reason or moves to class 2.
- A behaviour change in kustomize name matching after a Flux bump → the parity tests fail at that bump.
- A migration writes an image without the table → caught by the check over migration scripts; at runtime the object gets the upstream reference until its chart renders it again. A migration that copies a reference from a live object gets the table only from that next render.
- The table changes under an object whose operator sets the image only when it creates the object (etcd members, CR fields an operator defaults on creation) → the running image stays until the object is recreated; for etcd, until the upstream change under [Known limitations](#known-limitations) lands.

## Testing

- Parity fixture shared by the Go test (krusty at the embedded helm-controller's kustomize version) and the helm-unittest suites of the helpers, with `match` cases for tag and digest and with chained tables.
- helm-unittest for the platform chart: rule expansion, precedence and normalization, `dropDigest`, validation against `images.yaml` (stale `match`, route 2 `match`, idempotency, digest without a tag), `allowedRegistries`, route 2 and route 3 values per package, and the env the migration hook passes.
- helm-unittest per migrated chart: empty table leaves the render unchanged, a table replaces the reference in the expected shape; for the templates of §4, a mocked live object with a renamed image.
- The guard under `make unit-tests`, and the check over migration scripts.
- The migrations that write images run with the env the hook passes against mocked objects, and a test holds the hook's references to the scripts' literals.
- e2e: the sandbox installs with a table renaming every image of the platform and of one instance per application and version to a second registry, and `allowedRegistries` set to it; the list `hack/collect-images.sh` writes must hold no image outside that registry, bar the declared class 8 exceptions. The proof of concept ran the manual form of this on the sandbox, with the table removed and restored as well (see [Proof of concept](#proof-of-concept)).

## Rollout

Each phase is useful alone and merges separately:

1. The guard with an allowlist holding everything not yet migrated, and `images.yaml` generated at artifact build. They go first, so that no new gap opens while the others close.
2. Helpers with parity tests, `match` in the table, and validation against `images.yaml` including idempotency.
3. The templates of §4, before any of their images is routed.
4. Class 1, and class 6 moved to class 1.
5. Vendored charts: route 2 in the platform chart and the kubernetes chart, route 3 patches and their component values, the `make update` change for CDI, KubeVirt and forklift; injected agents; the tenant node machine config; Go code that creates another operator's custom resource.
6. Migrations: the env the hook passes, migrations 37, 50 and 54, and the check over migration scripts.
7. Class 5.
8. `imageRewrites` and `allowedRegistries`, and the e2e check.

Each phase shrinks the allowlist; the work in this repository is complete when only class 8 and the known limitations are left on it. The changes in etcd-operator, the FoundationDB operator and Kamaji follow separately.

## Open questions

- Is it acceptable to build `match` on kustomize behaviour the Flux CRD does not document, pinned by tests? The alternative is no per-version overrides for container images until Flux supports a selector.
- Shape of the interface: `match` as a nested object, or flat `tag` and `fromDigest` fields; names of `imageRewrites`, `dropDigest`, `allowedRegistries` and the subchart key `cozystackImageOverrides`.
- An entry without `match` that sets `newTag` or `digest` for a name the release runs under several tags (§6): a warning, as proposed, or a refusal that the administrator overrides with an explicit `match`?
- `virt-operator` per-component env is used as the whole reference (confirmed on a cluster); does it hold across a KubeVirt upgrade?
- konnectivity `version` and CoreDNS or kube-proxy `imageTag` set to `<tag>@sha256:<digest>`: does Kamaji use the string as is, as the FoundationDB operator does?
- etcd-operator: `spec.image` as proposed under Known limitations, or a tag template on the operator; the first keeps per-version digests possible.
- Should `images.yaml` and `hack/lib/image-refs.sh` share a source, or stay separate since they answer different questions?

## Alternatives considered

**Admission webhook rewriting Pod images.** Covers whatever becomes a Pod, including the images of the known limitations. Rejected as the primary mechanism: the platform's own webhook would sit in the path of every Pod in every tenant, including the ones it needs to start itself; operators that compare their rendered spec with the live object fight it; and a misconfiguration surfaces only at runtime. #21 rejects admission-based redirection too. KubeVirt was the one case that seemed to need a webhook, and its per-component env removes it. It remains the fallback for the known limitations if the upstream changes do not land.

**The table in `global`.** One channel into every chart and subchart, no per-package wiring. Rejected because `global` reaches every chart through `cozystack-values`, and a chart that treats any non-empty `global` as carrying its own keys breaks; smee in bootbox does.

**Only the platform channel, no patches.** Avoids touching vendored templates, but whole-string values then need the vendored defaults duplicated in the platform chart, where a `make update` bump leaves them stale silently, and `match` cannot work for any vendored image.

**Only patches, no platform channel.** One mechanism everywhere, at the cost of a patch to maintain for every vendored chart, including those whose values already take parts.

**Registry mirrors only.** They serve an image under its pinned name and digest and cannot rename or repin. They remain the right tool for mirror-as-is, as #21 proposes.

**kustomize patches in the post-renderer per chart** (for env, args and ConfigMaps). Each patch duplicates knowledge of the upstream layout, breaks silently when upstream renames a key, and cannot reach gzip payloads or operator defaults.

**Reviving `images-registry` as a global prefix.** One registry for everything serves only mirror-as-is with a path prefix, and cannot rename or repin. Prefix rules cover the same need and expand into ordinary entries.

**Matching the version in the table key only for the routes that can do it.** Leaves route 0 keyed by name, so a per-version entry would work in CRs and not in containers of the same release. The `name:tag` form gives every route the same behaviour.

**Committing `images.yaml`.** Rejected: its first-party digests change with every image build, and its release-candidate tags fail the promotion's postcondition.
