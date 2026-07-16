<!-- Place this file at design-proposals/distributed-tracing/README.md -->
# Distributed tracing in the Cozystack monitoring stack

- **Title:** `Distributed tracing for managed applications via OTLP and VictoriaTraces`
- **Author(s):** `@scooby87`
- **Date:** `2026-07-16`
- **Status:** Draft

## Overview

Cozystack ships two of the three observability signals out of the box — metrics (VictoriaMetrics) and logs (VictoriaLogs) — but has no supported way to collect distributed **traces**. An operator who wants to see how a request flows through a managed database or messaging cluster, or to correlate a slow span with the logs and metrics it produced, has nothing to turn on. This proposal adds the third signal: an OTLP ingest path, a VictoriaTraces backend that mirrors the existing VictoriaLogs deployment one-for-one, a Grafana traces datasource wired for trace↔logs↔metrics correlation, and a per-application opt-in toggle so a tenant enables tracing on exactly the workloads that need it.

The design is deliberately conservative: it reuses the multi-tenant topology, the operator-driven provisioning, the Grafana datasource pattern, and the values surface that metrics and logs already established, so tracing lands as "the same thing again for a third signal" rather than a new subsystem with new conventions. The backend choice — VictoriaTraces — falls out of that principle: the `VTCluster`/`VTSingle` CRDs already ship in the victoria-metrics-operator that Cozystack deploys today, so no new operator is introduced.

## Scope and related proposals

In scope: a traces backend (platform-wide and per-tenant), an OTLP ingest gateway, a Grafana datasource with correlation, and a per-app opt-in surface. Out of scope: automatic instrumentation of arbitrary tenant workloads, and tracing the internals of Virtual Machines or the Kubernetes control plane (VMs and Kubernetes are not traced by this proposal — only Cozystack-managed applications that can emit OTLP are).

- **Sibling stack:** the platform monitoring stack `packages/system/monitoring` and the per-tenant stack `packages/extra/monitoring` (wired by `packages/apps/tenant/templates/monitoring.yaml`). This proposal extends both.
- **Collection agents:** `packages/system/monitoring-agents` (fluent-bit, vmagent) — the deployment pattern the OTLP collector follows.
- **Prior art in-repo:** Harbor already exposes an internal trace config (`packages/system/harbor/charts/harbor/values.yaml`, provider `jaeger` or `otel`). It is app-local and not a platform backend; this proposal supersedes ad-hoc per-app trace endpoints with a shared destination.
- **Driver:** requested by a client (hidora) who needs request-level visibility across managed DBaaS and messaging services.

## Context

Cozystack's observability is multi-tenant with a central backend. The platform stack runs in `tenant-root` (namespace `cozy-monitoring`) and hosts VictoriaMetrics, VictoriaLogs, Grafana (via grafana-operator), Alerta and vmalert. Tenants either ship signals to the central backend (ExternalName redirects in tenant namespaces point at the root stack; vmagent stamps a `tenant:` external label) or run their own isolated stack from `packages/extra/monitoring`.

Metrics storage is declared as a list of tiers in values and rendered into VictoriaMetrics CRs — `metricsStorages` in `packages/system/monitoring/values.yaml` defines a `shortterm` (3d) and a `longterm` (14d) tier. Logs storage follows the identical shape: `logsStorages` renders one `VLCluster` per entry in `packages/system/monitoring/templates/vlogs/vlogs.yaml`, with the retention period set on `vlstorage.retentionPeriod`, `managedMetadata` labels for application ownership, a label stamped on the storage PVC claim template so the post-delete cleanup hook can find it, and a load-bearing guard that **fails the render** when the list is empty rather than silently shipping to a non-existent endpoint (the fix for issue `cozystack/cozystack#3181`).

Grafana datasources are provisioned as `GrafanaDatasource` CRs by grafana-operator, one per storage: `packages/system/monitoring/templates/vm/grafana-datasource.yaml` (type `prometheus`, per metrics tier) and `packages/system/monitoring/templates/vlogs/grafana-datasource.yaml` (type `victoriametrics-logs-datasource`, per logs storage). Every datasource attaches to Grafana through `instanceSelector: { matchLabels: { dashboards: grafana } }`.

Applications expose metrics today but there is no tracing surface. Most managed engines are scraped unconditionally through a `WorkloadMonitor` CR (clickhouse, kafka, rabbitmq, nats, mariadb) or a native operator mechanism (postgres/CNPG `enablePodMonitor`, redis via a `redis_exporter` sidecar + `VMServiceScrape`). The one app with an explicit observability toggle is foundationdb: `monitoring.enabled` in `packages/apps/foundationdb/values.yaml` gates whether its `WorkloadMonitor` renders — that toggle is the shape a `tracing.enabled` switch should copy.

Crucially, the tracing backend needs no new operator. The victoria-metrics-operator Cozystack already runs (appVersion `v0.68.4`, `packages/system/victoria-metrics-operator`) ships the VictoriaTraces CRDs `VTCluster` and `VTSingle` (`packages/system/victoria-metrics-operator/charts/victoria-metrics-operator/crd.yaml`, CRDs `vtclusters.operator.victoriametrics.com` and `vtsingles.operator.victoriametrics.com`). `VTCluster` decomposes into `VTInsert`/`VTStorage`/`VTSelect` — a direct analog of `VLCluster`'s `vlinsert`/`vlstorage`/`vlselect` — so the render template can be lifted from `vlogs.yaml` almost verbatim.

### The problem

- "A query against my managed Postgres is slow and I can't see where the time goes — which statement, which replica, which downstream call." There is no trace to open.
- "I have a log line for a failed request and a latency spike on a dashboard, but no way to jump from either to the actual request span." The signals don't correlate.
- "My application already emits OTLP spans, but Cozystack gives me nowhere to send them." There is no OTLP endpoint and no backend.

## Goals

- Accept traces over **OTLP** (gRPC `4317` and HTTP `4318`), the standard cloud-native tracing protocol.
- Store traces in a **VictoriaTraces** backend with a **configurable retention period, defaulting to 14 days**.
- Provide a **per-application opt-in** `tracing.enabled` toggle; tracing is **off by default** and adds zero overhead until enabled.
- Provision a **Grafana traces datasource** and wire **trace↔logs↔metrics** correlation so an operator can pivot between all three signals.
- Preserve **per-tenant isolation and the central-backend topology** exactly as metrics and logs do today.
- Introduce **no new operator** and no new provisioning convention — reuse VictoriaTraces (already in vm-operator), the storage-list values shape, and the grafana-operator datasource pattern.

### Non-goals

- Auto-instrumenting arbitrary tenant workloads. This proposal wires the *transport and storage*; emitting spans is the application's job (native where the engine supports it, sidecar/agent otherwise).
- Tracing Virtual Machines or Kubernetes control-plane internals.
- Changing the existing metrics or logs pipelines.
- Mandating a sampling policy for tenant applications (the platform sets a safe default and exposes a knob).

## Design

### 1. Backend: VictoriaTraces (`tracingStorages`)

Add a `tracingStorages` list to the monitoring values, parallel to `metricsStorages` and `logsStorages`, in both `packages/system/monitoring/values.yaml` and `packages/extra/monitoring/values.yaml`:

```yaml
tracingStorages:
- name: generic
  retentionPeriod: "14d"   # configurable; default 14 days per the requirement
  storage: 10Gi
  storageClassName: ""
```

Render one `VTCluster` per entry in a new `templates/vtraces/vtraces.yaml`, lifted from `templates/vlogs/vlogs.yaml`: `managedMetadata` labels for application ownership, replica counts on `vtinsert`/`vtselect`/`vtstorage`, `vtstorage.retentionPeriod` from the entry, the storage-PVC claim-template label for the release-scoped post-delete cleanup hook, and — reusing the `cozystack/cozystack#3181` lesson — a guard that **fails the render on an empty list** so a misconfiguration is loud, not a silent black hole for spans.

```yaml
{{- range .Values.tracingStorages }}
---
apiVersion: operator.victoriametrics.com/v1
kind: VTCluster
metadata:
  name: {{ .name }}
spec:
  managedMetadata:
    labels:
      apps.cozystack.io/application.kind: Monitoring
      apps.cozystack.io/application.name: {{ $.Release.Name }}
  vtinsert:  { replicaCount: 2 }
  vtselect:  { replicaCount: 2 }
  vtstorage:
    retentionPeriod: {{ .retentionPeriod | quote }}
    replicaCount: 2
    storage:
      volumeClaimTemplate:
        metadata:
          labels:
            apps.cozystack.io/application.name: {{ $.Release.Name }}
        spec:
          {{- with .storageClassName }}
          storageClassName: {{ . }}
          {{- end }}
          resources:
            requests:
              storage: {{ .storage }}
{{- end }}
```

The monitoring HelmRelease readiness gate keys on the new `VTCluster` the same way it keys on `VLCluster` today.

### 2. OTLP ingest via an OpenTelemetry Collector

VictoriaTraces ingests OTLP natively, but a collector in front gives the platform a stable tenant-facing endpoint, sampling/rate-limiting, and a place to attach the `tenant:` resource attribute — the same role vmagent and fluent-bit play for metrics and logs. Deploy an **OpenTelemetry Collector** in `cozy-monitoring`, following the `packages/system/monitoring-agents` deployment/service pattern:

- Receivers: `otlp` on gRPC `4317` and HTTP `4318`.
- Processors: `batch`, a `tail_sampling`/`probabilistic_sampler` governed by the platform default, and `resource` to stamp `tenant`.
- Exporter: OTLP to the `vtinsert` service of the `tracingStorages` backend.

Tenants target an in-cluster OTLP endpoint (e.g. `otel-collector.cozy-monitoring.svc`); per-tenant namespaces get an ExternalName redirect to the root collector, mirroring the existing `vlinsert-generic` logs redirect, so the central-vs-isolated choice works identically for traces.

### 3. Grafana datasource and correlation

Add a `GrafanaDatasource` CR per `tracingStorages` entry in `templates/vtraces/grafana-datasource.yaml`, mirroring the logs datasource template and attaching through `instanceSelector: { matchLabels: { dashboards: grafana } }`. VictoriaTraces exposes a Jaeger-compatible query API, so the datasource is `type: jaeger` (or the dedicated VictoriaTraces datasource plugin, allow-listed like `victoriametrics-logs-datasource` is today) pointed at the `vtselect` service. Configure:

- **Trace → logs**: link to the VictoriaLogs datasource keyed on `trace_id`.
- **Trace → metrics**: link to the VictoriaMetrics datasource for RED-style span metrics.
- **Logs/metrics → trace**: derived fields on the existing datasources so a `trace_id` in a log or exemplar opens the trace.

### 4. Per-application opt-in

Add a `tracing` struct to each participating app's `values.yaml` using the cozyvalues-gen annotation conventions, modelled on foundationdb's `monitoring.enabled` toggle and postgres's `backup` struct:

```yaml
## @typedef {struct} Tracing - OpenTelemetry (OTLP) tracing configuration.
## @field {bool} enabled - Enable OTLP trace export from this application.
## @field {string} [endpoint] - OTLP collector endpoint. Defaults to the platform collector in cozy-monitoring.
## @field {string} [samplingRatio] - Head-sampling ratio 0.0..1.0. Defaults to the platform policy.

## @param {Tracing} tracing - OpenTelemetry tracing configuration.
tracing:
  enabled: false
  endpoint: ""
  samplingRatio: ""
```

How an app emits spans depends on the engine:

- **Native OTLP**, wired by chart config: ClickHouse (`opentelemetry_span_log`, currently disabled in `packages/apps/clickhouse/templates/clickhouse.yaml`) and NATS (native OTLP in recent versions).
- **Sidecar/agent OTLP**: Kafka and RabbitMQ (JVM/plugin agents), MariaDB, Redis and Postgres (an OpenTelemetry agent/exporter sidecar). The `tracing.enabled` toggle gates the sidecar and the `OTEL_EXPORTER_OTLP_ENDPOINT` env, defaulting to the platform collector.

### 5. Data flow

```mermaid
flowchart LR
  app["Managed app<br/>(tracing.enabled)"] -- OTLP 4317/4318 --> col["OpenTelemetry Collector<br/>cozy-monitoring"]
  col -- OTLP --> vt["VictoriaTraces<br/>VTCluster (vtinsert→vtstorage)"]
  gr["Grafana"] -- Jaeger query --> vt
  gr -. trace_id .-> vl["VictoriaLogs"]
  gr -. span metrics .-> vm["VictoriaMetrics"]
```

## User-facing changes

- A new `tracingStorages` block in the monitoring values (system and per-tenant), with `retentionPeriod` defaulting to 14 days.
- A new per-app `tracing.*` block; off by default.
- A Traces datasource and Explore/Traces view in Grafana, with pivot links to logs and metrics.
- A docs entry point (`docs/observability/distributed-tracing.md` in `cozystack/cozystack`) covering how to enable tracing and point an app at the collector.

## Upgrade and rollback compatibility

The change is purely additive and opt-in. Existing clusters see no behavioural change until `tracingStorages` is set and an app flips `tracing.enabled`. An empty/absent `tracingStorages` renders no `VTCluster` (guarded, so it fails loudly only if a downstream component is told to expect one — matching the logs behaviour). No data migration is required. Rollback is removing the `tracingStorages` block and the per-app toggles; trace data in VictoriaTraces PVCs is discarded on backend removal (flagged: irreversible for already-stored spans, like logs).

## Security

- **New tenant-supplied input**: the OTLP endpoint accepts spans from tenant workloads. The collector is the trust boundary — it enforces per-tenant `tenant` resource attribution, rate-limits, and sampling to prevent a noisy or hostile tenant from exhausting the shared backend.
- **Isolation**: central-backend mode keeps the existing tenant-labelling model; isolated mode (`packages/extra/monitoring`) keeps traces inside the tenant.
- **Transport**: OTLP endpoints should be TLS-terminated; align with the unified TLS/PKI model (`design-proposals/unified-tls-pki`) rather than minting bespoke certs.
- **RBAC**: new `VTCluster`/`GrafanaDatasource`/collector resources need the same narrowly-scoped RBAC the metrics/logs equivalents already have. No new secret classes are introduced beyond the OTLP endpoint credentials, if any.

## Failure and edge cases

- Empty `tracingStorages` while a consumer expects a backend → loud render failure (mirrors `cozystack/cozystack#3181`), never a silent span black hole.
- Collector unreachable from an app → the app's exporter drops/retries per OTLP defaults; the app itself does not crash and serving is unaffected.
- Backend storage exhausted → oldest traces evicted per `retentionPeriod`; ingest backpressures at the collector, not the app.
- `tracing.enabled: false` → no sidecar, no env, no CR: zero overhead.
- App emits OTLP but no backend deployed → collector accepts and drops (or the toggle is guarded to require a backend); documented, not surprising.

## Testing

- **Unit/lint**: `helm template` + `helm lint` for the new `vtraces` templates and each app's `tracing` block; assert `VTCluster`, the collector, and the `GrafanaDatasource` render only when configured, and that an empty `tracingStorages` fails the render.
- **e2e** (Chainsaw, per `docs/agents/e2e-testing.md`): deploy the monitoring stack with a `tracingStorages` entry, deploy one app (start with a native-OTLP engine, e.g. ClickHouse) with `tracing.enabled: true`, generate activity, then assert a trace is queryable via the `vtselect` Jaeger API and visible in Grafana.
- **Manual**: verify trace→logs and trace→metrics pivots in Grafana.

## Rollout

1. **Backend + ingest**: `tracingStorages`/`VTCluster` and the OpenTelemetry Collector in `packages/system/monitoring` and `packages/extra/monitoring`. No app changes yet.
2. **Grafana**: traces datasource + correlation links.
3. **Per-app toggles**: start with native-OTLP engines (ClickHouse, NATS), then sidecar-based engines (Kafka, RabbitMQ, MariaDB, Redis, Postgres), one PR per app.
4. **Docs**: enablement guide under `docs/observability/`.

## Open questions

- **VictoriaTraces maturity**: the CRDs ship in the operator, but is VictoriaTraces production-ready at the version Cozystack pins? If not, Grafana Tempo is the drop-in fallback (see Alternatives) — the collector and per-app surfaces are backend-agnostic, so only the backend template and datasource type change.
- **`VTCluster` vs `VTSingle`** as the default: cluster for HA parity with metrics/logs, or single for a lighter footprint on small clusters?
- **Sampling**: head sampling at the app vs tail sampling at the collector; what platform default?
- **Collector deployment**: Deployment (gateway) vs DaemonSet (agent) — gateway matches the central-backend model; DaemonSet matches fluent-bit.
- **External OTLP exposure**: should tenants be able to push spans from outside the cluster, and if so through which ingress/Gateway path?

## Alternatives considered

- **Grafana Tempo** (backend): mature, object-storage-backed (cheap retention on the seaweedfs/COSI storage Cozystack already runs), and the strongest Grafana-native correlation story. Rejected as the *primary* choice only to keep the stack single-vendor (VictoriaMetrics/Logs/Traces share one operator and one operational model). It remains the recommended fallback if VictoriaTraces proves immature — the rest of this design is unchanged by the swap.
- **Jaeger** (backend): mature and OTLP-native, but its own UI and weaker Grafana integration cut against the single-pane correlation goal, and it adds an operator/storage story Cozystack doesn't already have.
- **No collector, app → backend directly** (ingest): simpler, but loses the shared trust boundary, per-tenant attribution, and central sampling/rate-limiting; rejected for the same reasons metrics go through vmagent and logs through fluent-bit rather than writing to storage directly.
- **Always-on tracing** (opt-in model): rejected — tracing overhead and storage cost must be a tenant's explicit choice; default off matches the requirement and the principle of least surprise.
