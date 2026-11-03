# Image references a kustomize images post-renderer cannot reach

Inventory taken on 2026-10-27 against `main` at `0b3f5dd7e` plus the `registries.imageOverrides` branch. Two sources:

- **Static:** every chart under `packages/apps`, `packages/extra` and `packages/system` rendered with `helm template` on `hack/testdata/render-fixtures/configured.yaml` (165 of 167 charts; `kubernetes-nodes` and `vm-instance` need a live `lookup` and were read from their templates instead). Vendored upstream charts are included on the values Cozystack ships.
- **Live:** the `cozy-overrides` test cluster (98 platform releases, 166 pods, no tenant applications), comparing the images of running pods against the images rendered into container specs.

Static totals: 366 references, 252 of them in `containers[]`/`initContainers[]` `image` (reached by the post-renderer), 114 elsewhere in 32 charts.

## Classes

| # | Where the reference lives | Charts | Possible fix |
|---|---|---|---|
| 1 | Image field of a custom resource rendered by a Cozystack chart, as one full reference | apps: postgres (`Cluster.spec.imageName`), mariadb (`MariaDB.spec.image`, exporter), mongodb (`PerconaServerMongoDB` server, backup, pmm), rabbitmq, redis and valkey (`RedisFailover` redis, sentinel, exporter), kubernetes (`KamajiControlPlane.spec.deployment.extraContainers[].image`); system: harbor, keycloak, monitoring, seaweedfs-db (CNPG `imageName`, harbor `RedisFailover`) | Route the value through a template helper that applies the table |
| 2 | Operator `env` or `args` naming the images the operator deploys | kafka-operator (strimzi, 8 refs incl. one per Kafka version), kubevirt-cdi-operator (6), kubevirt-operator, forklift-operator (15), postgres-operator (CNPG operand), etcd-operator, cert-manager (`--acme-http01-solver-image`), kamaji (`args`), kube-ovn-controller (`args`), gpu-operator (`env`), local-storage (`env`), bootbox (`env`) | Same helper where our chart sets the value; for vendored charts only where upstream exposes a value for it |
| 3 | ConfigMap read by an operator | mariadb-operator (`RELATED_IMAGE_*`, with `RELATED_IMAGE_MARIADB_NAME` holding a name without tag), postgres-operator (barman `SIDECAR_IMAGE`), kube-ovn (`ovn-vpc-nat-config.data.image`), piraeus-operator (`image-config`: `base` registry and per-component `image`/`tag` in separate keys) | A helper variant that returns the parts of a reference, not only the whole string |
| 4 | Reference split into `repository` + `image` + `version` in a CR | gpu-operator `ClusterPolicy` (19 components) | Same parts helper |
| 5 | Inside gzip in a ConfigMap's `binaryData` (CAPI provider components) | capi-providers-core, -bootstrap (kubeadm, talos), -cpprovider (kamaji), -infraprovider (capk), -infraprovider-proxmox, -ipam-in-cluster | `spec.deployment.containers[].imageUrl` on the provider CR, already used for capk; set it for every provider through the helper |
| 6 | No image in the CR: the operator picks a built-in default | monitoring and monitoring-agents (VictoriaMetrics `VMCluster`, `VMAgent`, `VMAlert`, `VMAlertmanager`, `VLCluster`; seen live: `vmagent`, `config-reloader`), opensearch (`OpenSearchCluster`), kafka (`Kafka`, defaults come from the strimzi env in class 2), etcd (`EtcdCluster` has no image field; etcd-operator uses `ETCD_IMAGE_REPOSITORY` plus `v<version>`, which our chart does not set), kubernetes (Kamaji `DataStore` and the tenant control-plane images, CAPI `Cluster`/`KubevirtCluster`), linstor (`LinstorSatelliteConfiguration`, defaults from class 3) | Set the image explicitly in the CR through the helper where the CRD has a field; otherwise class 7 |
| 7 | Images an operator derives and does not let us name per image | KubeVirt (`virt-api`, `virt-controller`, `virt-handler`, `virt-launcher`, `virt-exportproxy` derived from the operator image; the CR has one `imageRegistry` and one `imageTag`) | None needed for KubeVirt: virt-operator v1.9.0 reads a whole reference per component from its own env (`VIRT_API_IMAGE`, `VIRT_CONTROLLER_IMAGE`, `VIRT_HANDLER_IMAGE`, `VIRT_LAUNCHER_IMAGE`, `VIRT_EXPORTPROXY_IMAGE`, `VIRT_EXPORTSERVER_IMAGE`, `VIRT_SYNCHRONIZATIONCONTROLLER_IMAGE`, `GS_IMAGE`, `PR_HELPER_IMAGE`, `SIDECAR_SHIM_IMAGE`, `VIRT_TEMPLATE_APISERVER_IMAGE`, `VIRT_TEMPLATE_CONTROLLER_IMAGE`; `pkg/virt-operator/util/config.go`), which moves it to class 2. Confirmed on a cluster: a set variable is used as the whole image |
| 8 | Outside Cozystack | Talos static pods (`kube-apiserver`, `kube-controller-manager`), CoreDNS, the sandbox (`pause`) image and the kubelet of the management cluster's nodes, Talos installer in worker machine configs (`talos.installerRepository` already exists), VM and Talos disk images fetched over HTTP | Talos machine config, registry mirrors, existing values |
| 9 | Written into live objects by a platform migration during an upgrade | core/platform migrations: 37 (CNPG `imageName` of the system databases), 50 (`etcd-migrate --agent-image`, no digest), 54 (`RedisFailover` `redis.image` and `sentinel.image`) | The migration hook's Job template passes each reference through the table and sets it as env; the scripts fall back to their literals |
| 2, injected | Injected by a webhook into tenant pods, built from the operator's env | telepresence (traffic-manager `AGENT_REGISTRY`, `AGENT_IMAGE`), openbao agent injector (disabled by default) | Same as class 2, where the chart exposes a value |

## Notes

- Class 1 is the largest group by number of applications, and every reference there is rendered by a Cozystack template, so a helper covers it without upstream changes. `cozy-lib.image` already exists and wraps the `.tag` files of 8 packages with the `_cluster.images-registry` prefix; extending it with the override table is the natural entry point.
- Classes 3 and 4 show that a whole-string helper is not enough: piraeus, mariadb and gpu-operator keep registry, name and tag in separate keys.
- Class 2 in vendored charts depends on what each upstream chart exposes. Each one needs checking before it can be counted as covered; see the next section.

## Class 2 and 3: can the value be routed through a helper

For each reference passed to an operator through `env`, `args` or a ConfigMap, the template it renders from and whether a value controls it. A value means the wrapper chart can set it from the override table without touching the vendored chart; a hardcoded string means the file has to change.

| Chart | Template | Controlled by | Shape |
|---|---|---|---|
| kafka-operator (strimzi) | vendored `060-Deployment-strimzi-cluster-operator.yaml`, `strimzi.image` and `strimzi.kafka.image.map` helpers | `defaultImageRegistry`, `defaultImageRepository`, `defaultImageTag`, per-component `<key>.image.{registry,repository,name,tag,digest}` | parts; Kafka images take a `-kafka-<version>` tag suffix per supported version |
| postgres-operator (CNPG) | vendored `deployment.yaml` | `image.repository`, `image.tag` | parts |
| postgres-operator (barman plugin) | vendored `configmap.yaml` | `sidecarImage.{registry,repository,tag}` | parts |
| cert-manager | vendored `deployment.yaml`, `--acme-http01-solver-image` | `acmesolver.image.*`, `imageRegistry`, `imageNamespace` | parts |
| kamaji | vendored `controller.yaml` (the reference in `args` is the controller's own image) | `image.repository`, `image.tag` | parts |
| kube-ovn | vendored `controller-deploy.yaml` (`--image`), `vpc-nat-config.yaml` | `global.registry.address`, `global.images.{kubeovn,natgateway}.{repository,tag}` | parts, registry shared by every image |
| gpu-operator | vendored `operator.yaml`, `ClusterPolicy` | `driver.manager.{repository,image,version}` and the same triple per component | parts |
| local-storage (openebs) | vendored `deployment.yaml` | `helperPod.image.*`, `global.imageRegistry` | parts |
| mariadb-operator | vendored `operator/configmap.yaml` | `image.*`, `config.{mariadbImage,mariadbImageName,maxscaleImage,exporterImage,exporterMaxscaleImage}` | whole references, plus `mariadbImageName` without a tag |
| piraeus-operator | vendored `config.yaml` | `imageConfigOverride` (a list of `base` plus `components.<name>.{image,tag}` overlays) | parts, registry per overlay |
| bootbox (smee) | vendored `deployment.yaml`, kernel argument `tink_worker_image=` | `tinkWorkerImage` | whole reference inside a string |
| etcd-operator | Cozystack `templates/deployment.yaml` | `image.repository`, `image.tag` (also the container image, which must stay equal) | parts |
| kubevirt-cdi-operator | Cozystack `templates/cdi-operator.yaml`, upstream release manifest downloaded by `make update`, no templating | none: `CONTROLLER_IMAGE`, `IMPORTER_IMAGE`, `CLONER_IMAGE`, `APISERVER_IMAGE`, `UPLOADSERVER_IMAGE`, `UPLOADPROXY_IMAGE` are literals | whole references |
| kubevirt-operator | Cozystack `templates/kubevirt-operator.yaml`, release manifest, no templating | none: `VIRT_OPERATOR_IMAGE` literal; component images derive from it unless the per-component env listed under class 7 is set, which the manifest does not do today | whole references |
| forklift-operator | Cozystack `templates/forklift-operator.yaml`, `kubectl kustomize` output with OLM placeholders substituted by `sed` in `make update` | none: 15 `*_IMAGE` env literals | whole references |

Every vendored upstream chart in this list exposes a value for the image, so no change in the upstream chart repositories is needed. A value alone is not a route, though: the table is known only at install time (see [routes.md](./routes.md)). Charts that take parts and keep an unset part at the chart default are set from the platform chart. Charts that take a whole string, or share one registry or tag across images (kube-ovn, Strimzi, mariadb-operator, piraeus, gpu-operator), need a patch of the vendored template applied by `make update` (README §2 and §3).

## Live pass with tenant applications

Taken on the same test cluster with one instance each of postgres, mariadb, mongodb, kafka, redis, valkey, rabbitmq, clickhouse, nats, qdrant, openbao, opensearch, foundationdb, etcd, monitoring, kubernetes, vm-instance and vm-disk in `tenant-root` (126 releases). Images of running pods that no deployed release renders into a container spec:

| Application | Image the pod runs | Set by | Class |
|---|---|---|---|
| postgres, monitoring (alerta-db, grafana-db) | `ghcr.io/cloudnative-pg/postgresql` | `Cluster.spec.imageName` | 1 |
| mariadb | `mariadb`, `prom/mysqld-exporter` | `MariaDB.spec.image`, exporter | 1 |
| mongodb | `percona/percona-server-mongodb` | `PerconaServerMongoDB.spec.image` | 1 |
| rabbitmq | `rabbitmq` | `RabbitmqCluster.spec.image` | 1 |
| redis, valkey | `redis`, `valkey/valkey`, `oliver006/redis_exporter` | `RedisFailover` redis and exporter | 1 |
| valkey | `valkey/valkey:7.2.11-alpine` for sentinel | redis-operator default, the chart sets no sentinel image | 6 |
| kubernetes | `talos-csr-signer` | `KamajiControlPlane.spec.deployment.extraContainers` | 1 |
| kubernetes | `registry.k8s.io/kube-apiserver`, `kube-controller-manager`, `kube-scheduler`, `kas-network-proxy/proxy-server` | Kamaji, from the control-plane version | 6 (Kamaji has registry settings per control plane) |
| kafka | `quay.io/strimzi/kafka:…-kafka-3.9.0` | strimzi, from its env per Kafka version | 2 |
| etcd | `quay.io/coreos/etcd` | etcd-operator default; `EtcdCluster` has no image field, the operator takes `ETCD_IMAGE_REPOSITORY` | 6; rename through the operator env, tag and digest need an etcd-operator change |
| opensearch | `opensearchproject/opensearch`, `busybox:latest` init | opensearch-operator from the CR version | 6 |
| foundationdb | `foundationdb/foundationdb`, `foundationdb-kubernetes-sidecar` | fdb-operator from `imageType` and version | 6 |
| monitoring | `victoriametrics/vminsert`, `vmselect`, `vmstorage`, `vmalert`, `victoria-logs`, `operator:config-reloader`, `prom/alertmanager` | VictoriaMetrics operator defaults | 6 |
| vm-instance | `quay.io/kubevirt/virt-launcher` | KubeVirt, derived from the operator image | 7 |

Every row matches the class the static pass predicted. Not covered by this pass: applications whose pods had not started. Images inside a tenant Kubernetes cluster itself are covered in the third pass.

## Second pass

The first pass rendered `packages/apps`, `packages/extra` and `packages/system` with one values fixture and compared one live cluster. A second pass, taken 2026-10-28, covered what that missed:

- `packages/core`: the installer and Flux are covered by `cozystackOperator.image` and `fluxImageOverrides`; the platform chart's migration hook is a container; the platform migrations write image references into live objects (class 9: migrations 37, 50, 54).
- The 13 charts that do not render on the shared fixture, read from their templates: the tenant Talos machine config sets `machine.kubelet.image` (`kubernetes-nodes`), which a Cozystack template renders and so is reachable, unlike the management cluster's kubelet.
- Image names declared in every `values.yaml`, vendored ones included, against every render and the live cluster: 194 names, 69 of them in no render with default values. All of them are optional components whose reference lands in a container when enabled, or in a class already listed, except agents a webhook injects into tenant pods (telepresence, openbao).
- Image fields of every CRD against the custom resources Cozystack renders: besides the class 6 entries above, MariaDB agents, init containers and MaxScale, Kafka's rack init image, Percona's log collector, and the gpu-operator init container are left to operator defaults.
- Operand pods on the live cluster that run their operator's image: CNPG, Strimzi and VictoriaMetrics take it from a copy in env (split by an override today); Percona MongoDB reads its own Pod and follows an override.
- References inside multi-line strings by short Docker Hub names, which the first pass's pattern missed: only `001-templates.json.example` in clickhouse-operator, which the operator does not load.
- Go code: only `--vddk-image` in the migration controller, already a flag. The third pass found more.

## Third pass

Taken `<date>`. The second pass still rendered one fixture and read the Go code for flags only. The third covered every chart at every version and variant, and the Go code that creates custom resources. It adds:

- **Go code that creates another operator's custom resource without naming its image:** the FoundationDB backup driver in `backupstrategy-controller` creates a `FoundationDBBackup` with no image, so the operator picks its own default. Same shape as class 6, but the reference is built in Go, not rendered by a chart.
- **Addons Cozystack installs into tenant Kubernetes clusters:** they are chart-rendered HelmReleases (`packages/apps/kubernetes/templates/helmreleases/`) that carry their own copy of the table. cert-manager (solver image), VMAgent and gpu-operator are routed there, so they are in scope even though the images run inside the tenant cluster.
- **Strimzi's per-version image map:** the per-version references of class 2 live in a single env variable (`STRIMZI_KAFKA_IMAGES`), so one name stands for several images at several tags.
