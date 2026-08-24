# Cluster and component health reporting API

- **Title:** `Unified health reporting for core components, nodes, and backups via a native CRD`
- **Author(s):** `@IvanHunters`
- **Date:** `2026-08-21`
- **Status:** Draft

## Overview

Cozystack ships a rich set of core components (Cilium, kube-ovn, LINSTOR/DRBD, etcd, Kamaji, CNPG, cert-manager, backups), but their health is scattered across HelmRelease conditions, pod readiness, per-component CRDs, component REST APIs, and Prometheus metrics. When something breaks, an operator has to know six different places to look and then correlate across them by hand.

This proposal introduces a **health-reporting controller** that periodically collects health from each component's native source and materializes it into a **namespace-scoped `Health` CRD** (`health.cozystack.io`). The result is a single, `kubectl`-native, per-node and per-tenant view of "what is broken and where" that the dashboard, `kubectl`, and the alerting stack all read from the same place.

Scope is deliberately narrow: report **facts** ("linstor: pvc-x Inconsistent on node cp1", "backup keycloak-db: last success 3d ago, SLA 24h"), not automated cross-layer root-cause analysis, and not time-series (that stays in Grafana).

## Scope and related proposals

- Complements the existing metrics stack (VictoriaMetrics + Grafana). This is not a replacement: metrics answer "how did it trend", this answers "what is broken right now".
- Backups reporting overlaps with any future backup-policy proposal; this proposal only *reports* backup freshness, it does not manage backup schedules.
- Per-tenant scoping aligns with the tenant model used elsewhere (Kamaji tenant control planes, per-namespace resources).

## Prior art

Cozystack already carries several partial health/readiness mechanisms. This proposal aggregates on top of them rather than replacing them; the relevant existing work:

- **Aggregated per-app readiness (reuse as a source).** `cozystack/cozystack#2356` added a `WorkloadsReady` condition to `Application.status`, sourced from `WorkloadMonitor`, with the deliberate split "`Ready` reflects the HelmRelease, `WorkloadsReady` reflects the actual pods" (motivated by `cozystack/cozystack#2360`, "Ready=True while pods crashloop"). `WorkloadMonitor` is the existing per-app health primitive (`cozystack/cozystack#812`, `cozystack/cozystack#2391`); the workload-level adapter here should consume it, not re-derive pod health. The open reliability bug `cozystack/cozystack#2335` (the `Operational` status is not persisted on some inputs) is a caveat the adapter must tolerate.
- **kstatus readiness model (adjacent, per-release).** The `cozystack/cozystack#2642` epic moves HelmRelease `Ready` onto the Flux/kstatus model with typed `waitStrategy` / `healthCheckExprs` (`cozystack/cozystack#3273` merged the API, `cozystack/cozystack#3264` adds CEL gates). That makes per-release readiness honest; `Health` aggregates across releases and components on top of it. The two are complementary: kstatus gates one HelmRelease, `Health` rolls up the platform. The epic's empty-status race is a caveat the platform adapter must handle.
- **Backup state visibility.** `cozystack/cozystack#2609` ("surface backup state for managed applications") asks for exactly the backup-freshness view this proposal's backup adapter produces; the `backups.cozystack.io/v1alpha1` CR already carries structured status (`cozystack/cozystack#2319` refactored `status.underlyingResources`). The backup adapter should read that CR rather than invent a new source.
- **Diagnostics surface.** `cozystack/cozystack#2733` ("improve error diagnostics surface across controller, UI, and chart authoring") shares the motivation of making sub-HelmRelease failures visible in dashboard / `kubectl` / CLI. Scope should be coordinated so the two do not duplicate.
- **Existing one-shot health collectors (candidate consumers, logic to reuse).** `cozystack/cozystack#2755` shipped `cmd/check-readiness`, a Go tool that parses status conditions across cozystack / Flux / Kubernetes resources; `cozystack/cozystack#3458` adds a pre-upgrade health gate that reads live LINSTOR satellites, faulty DRBD, and node readiness. Both are point-in-time; the health-controller can materialize their logic continuously, and both are natural consumers of the resulting CRD.
- **"Status lies" incidents (motivation).** The failure mode this proposal targets, a green `Ready` / `Available` hiding a real fault, is well attested: `cozystack/cozystack#3466` ("make operator readiness honest", vacuous `Available=True`), generalized in `cozystack/cozystack#3561`, plus `cozystack/cozystack#1952` (HelmRelease success with an undeployed VM), `cozystack/cozystack#3181` (VictoriaLogs silently dropping logs while Ready), and `cozystack/cozystack#3832` (RestoreJob `Succeeded` on a broken restore). These reinforce the non-goal stance that adapters must report facts, not merely echo native condition flags.

## Decisions

<!-- Filled in as implementation proceeds; records live under this
proposal's decisions/ directory, numbered from 0001, linked newest first.
Empty while the proposal is still intent. -->

## Context

Today, to assess cluster health an operator combines:

- `kubectl get hr -A` — HelmRelease readiness and `dependsOn` chains
- `kubectl get pods -A` / CRD conditions — `CiliumNode`, LINSTOR `LinstorSatellite`, CNPG `Cluster`
- component APIs — `linstor node list` / `resource list --faulty`, `cilium status`, `kubectl ko diagnose`
- Prometheus metrics — `etcd_disk_wal_fsync_duration_seconds`, DRBD state, `cilium_*`
- Talos API — for management etcd, which is a static pod outside the Kubernetes API

There is no single object that says "component X is degraded on node Y because Z".

### The problem

A real incident that motivated this proposal (paraphrased operator experience on a 3-node cluster):

- Platform stuck at 9/98 HelmReleases ready. Root cause: `kube-ovn` HelmRelease stuck in upgrade because ~30 orphaned `ovs-ovn` pods on one node blocked the DaemonSet rollout. Finding this meant reading HR conditions, then the DS, then per-node pods.
- Dashboard returned 503. Root cause chain: one control-plane node's etcd fell behind (slow disk) → node NotReady → `postgres-operator` pod stranded on it → CNPG failover never happened → `keycloak-db-rw` endpoint empty → Keycloak down → dashboard OIDC 503. Five layers, correlated by hand.
- `kubectl logs`/`exec` hung for pods on two of three nodes. Root cause: one node registered with a public nodeIP while others used internal IPs; Cilium host-firewall then dropped cross-node traffic to kubelet `:10250`.

None of these were visible in one place. Every diagnosis was a manual walk across component boundaries. An operator staring at a single health page would have seen "kube-ovn: degraded on cp3 (ovs-ovn not ready)", "etcd: cp1 fsync p99 6s", "backups: keycloak-db stale" immediately.

The dashboard-503 chain alone spanned five components; today an operator has to reconstruct it by hand, one layer at a time:

```mermaid
flowchart TD
  d["cp1 slow disk"] --> e["etcd member falls behind"]
  e --> n["cp1 NotReady"]
  n --> p["postgres-operator pod stranded on cp1"]
  p --> f["CNPG failover never happens"]
  f --> ep["keycloak-db-rw endpoint empty"]
  ep --> kc["Keycloak down"]
  kc --> dash["dashboard OIDC 503"]
```

## Goals

- A single namespace-scoped CRD `Health` (`health.cozystack.io`) reporting per-component health, refreshed by a controller.
- Per-node breakdown in `status` (most real problems are node-local).
- Two scopes via namespaces: infra/management health in `cozy-system`, tenant health per tenant namespace.
- Native `kubectl` access: `get` / `describe` / `-w` / `-o yaml` / `printerColumns` / `shortNames`, no client code required.
- Backup freshness reporting (CNPG, etcd snapshots, and any velero-style backups) as a first-class component.
- A stable machine-readable shape the dashboard and `kube-state-metrics`-based alerts both consume.

### Non-goals

- **No automated cross-layer root-cause correlation.** Reporting states facts; it does not infer "A caused B" across components. A bounded set of rule-based hints may be added later, out of scope here.
- **No time-series / graphs.** Trends stay in Grafana. This CRD holds only current actionable state.
- **No remediation.** Read-only reporting; it never restarts, deletes, or reconfigures anything.
- Not a replacement for component-native tooling (`linstor`, `cilium status`, `kubectl ko`).

## Design

### Data model

A namespace-scoped CRD, one object per component per scope:

```yaml
apiVersion: health.cozystack.io/v1alpha1
kind: Health
metadata:
  name: etcd            # component name
  namespace: cozy-system  # scope: cozy-system = infra; tenant-* = that tenant
spec:                   # controller/policy-owned, NOT tenant-writable (see Security)
  component: etcd
  sla: {}               # optional per-component thresholds (e.g. backup maxAge)
status:                 # status subresource; only the controller writes it
  overall: Degraded     # Healthy | Degraded | Down | Unknown (deterministic rollup)
  observedAt: "2026-08-21T18:16:00Z"
  freshUntil: "2026-08-21T18:17:00Z"  # past this, consumers MUST read overall as Unknown
  nodes:
    cp1: Degraded
    cp2: Healthy
    cp3: Healthy
  conditions:
    - type: FsyncLatency
      status: "False"
      reason: SlowDisk
      message: "wal fsync p99 6.2s (>1s)"
      resourceRef:                    # structured, so consumers never parse message
        kind: Node
        name: cp1
      lastTransitionTime: "2026-08-21T18:10:00Z"
    - type: QuorumHealthy
      status: "True"
      message: "3/3 voting members"
```

Backup freshness is reported with typed fields, not encoded in `message` or inferred from `lastTransitionTime`:

```yaml
apiVersion: health.cozystack.io/v1alpha1
kind: Health
metadata:
  name: backups
  namespace: tenant-foo
status:
  overall: Degraded       # worst target wins (see rollup contract below)
  observedAt: "2026-08-21T18:16:00Z"
  freshUntil: "2026-08-21T18:17:00Z"
  conditions:
    - type: BackupFresh
      status: "False"
      reason: Stale
      resourceRef:
        kind: Cluster       # CNPG cluster
        name: keycloak-db
      lastSuccessfulAt: "2026-08-18T18:16:00Z"
      maxAge: "24h"         # from spec.sla
      observedAge: "72h"
      lastTransitionTime: "2026-08-19T18:16:00Z"
```

- **Namespace = scope.** `cozy-system/etcd` is the Talos management etcd; `tenant-foo/etcd` is that tenant's Kamaji-managed etcd. `kubectl get health.cozystack.io -A` shows everything; `-n tenant-foo` shows one tenant. A namespaced CRD scopes only object *names*: read isolation is not automatic and requires explicit namespace-only `Role`/`RoleBinding`s per tenant, never a cluster-wide `ClusterRole` (see Security).
- **`status.nodes`** gives the per-node rollup; **`status.conditions`** carry specific problems. Each condition pins the affected object with a structured `resourceRef` (kind/name, optionally namespace) so consumers identify it without parsing `message`; backup conditions additionally carry typed freshness fields (`lastSuccessfulAt`, `maxAge`, `observedAge`) rather than overloading `lastTransitionTime`.
- **`spec` is controller/policy-owned, not tenant-writable**, and the CRD uses the `status` subresource so only the controller writes `status`. `spec.sla` thresholds are set by the platform (or the backup-policy owner), not by tenants, so a tenant cannot silence its own alerts by widening an SLA.
- **`overall` rollup contract (deterministic).** `overall` is computed with fixed precedence `Down > Degraded > Unknown > Healthy` (worst wins) across `status.nodes` and `status.conditions`. A component with no observable source, or whose `observedAt` has passed `freshUntil`, rolls up to `Unknown`, never `Healthy`. Multi-target components (e.g. several backup targets, several nodes) take the worst target's state. Consumers key off `overall` but MUST treat an object past its `freshUntil` as `Unknown` regardless of the stored value.

The value moves between four states; loss of a fresh observation always wins over a stale positive:

```mermaid
stateDiagram-v2
  [*] --> Healthy
  Healthy --> Degraded: a node/condition turns non-ready
  Degraded --> Down: quorum or availability lost
  Down --> Degraded: partial recovery
  Degraded --> Healthy: all conditions clear
  Healthy --> Unknown: source unreachable or past freshUntil
  Degraded --> Unknown: source unreachable or past freshUntil
  Down --> Unknown: source unreachable or past freshUntil
  Unknown --> Healthy: fresh observation, all clear
  Unknown --> Degraded: fresh observation, issues remain
```

### Controller

A dedicated `health-controller` Deployment (separate from `cozystack-api`), with its own reconcile loop:

- Polls each component's native source on a bounded interval, with caching, a per-adapter timeout, cancellation, capped retries with backoff, and a concurrency limit so one slow adapter cannot starve the others. An adapter that exceeds its timeout yields `Unknown` for its object and does not hold up the rest of the reconcile. **Health is never collected at request-time** — the dashboard and `kubectl` read the materialized CRD, so a slow component (e.g. a stalled LINSTOR controller) never blocks the health view. This is deliberate: request-time fan-out to several components is the failure mode that produced the cascading timeouts in the motivating incident.

The write path (controller-side, on a timer) and the read path (consumer-side, against the stored object) are fully decoupled:

```mermaid
sequenceDiagram
  autonumber
  participant T as reconcile ticker
  participant C as health-controller
  participant A as component adapter
  participant S as native source
  participant K as Health CRD
  participant D as dashboard / kubectl / alerts
  T->>C: tick (per interval)
  C->>A: poll (timeout + cancel)
  A->>S: read native state
  alt source responds in time
    S-->>A: facts
    A-->>C: conditions + nodes
    C->>K: write status, set observedAt and freshUntil
  else timeout or source down
    C->>K: write overall=Unknown, keep stale observedAt
  end
  D->>K: get / watch (no collection triggered)
  K-->>D: current Health (Unknown once past freshUntil)
```
- Per-component **adapters** encapsulate "where does this component keep its truth":
  - **etcd (management)** — Prometheus metrics (`etcd_*`); it is a Talos static pod outside the k8s API.
  - **Cilium** — `CiliumNode` CRD, agent readiness, health API, `cilium_*` metrics.
  - **kube-ovn** — DaemonSet/pod state, `ovn-central` quorum, kube-ovn CRDs.
  - **LINSTOR** — controller REST API and/or Piraeus `LinstorCluster`/`LinstorSatellite` conditions (`resource --faulty`).
  - **Kamaji tenant control plane** — Kamaji CR + pods in the tenant namespace.
  - **Backups** — CNPG `Backup`/`ScheduledBackup` status, etcd snapshot age, velero-style backups; compared against SLA to flag `Stale`/`Failed`.
  - **Platform** — HelmRelease graph: report each not-ready release as a fact, along with its `dependsOn` edges, so the reader can see where a cascade originates. Naming a single "root of cascade" is root-cause inference (a heuristic: it misattributes when branches fail independently, and "first" is undefined for multiple roots), so it is deferred to the Phase 4 hint layer rather than emitted as a fact here.
- Adapters are versioned with the components they read (component CRD/output shapes drift between versions). A broken adapter degrades one `Health` object to `Unknown`; it never takes down `cozystack-api` or the dashboard (isolated blast radius, independent release cadence).

### Isolation rationale

The controller is a separate component, not an extension of `cozystack-api`, because: (1) different domain (read-only polling of external sources vs resource CRUD), (2) blast radius (a failing adapter must not break the core API), (3) independent release cycle for per-version adapters.

```mermaid
flowchart LR
  subgraph sources[Native sources]
    m[etcd metrics]
    cn[CiliumNode / cilium API]
    ko[kube-ovn pods / CRDs]
    ls[LINSTOR REST / Piraeus CRDs]
    km[Kamaji CR]
    bk[CNPG / velero / etcd snapshots]
    hr[HelmRelease graph]
  end
  hc[health-controller] --> crd[(Health CRD\nhealth.cozystack.io)]
  sources --> hc
  crd --> dash[Dashboard]
  crd --> kctl[kubectl / plugin]
  crd --> alert[kube-state-metrics -> alerts]
```

## User-facing changes

Native `kubectl`, no client code:

```
kubectl get health.cozystack.io -A            # everything, infra + all tenants
kubectl get health.cozystack.io -n cozy-system
kubectl get health.cozystack.io etcd -n cozy-system
kubectl describe health.cozystack.io etcd -n cozy-system
```

With `additionalPrinterColumns` and `shortNames: [chz]`:

```
$ kubectl get chz -n cozy-system
NAME       OVERALL    DEGRADED-NODES   ISSUES   AGE
cilium     Degraded   cp3              1        40d
kube-ovn   Healthy                     0        40d
linstor    Degraded   cp1              2        40d
etcd       Degraded   cp1              1        40d
backups    Degraded                    1        40d
```

- **Dashboard**: a single "Cluster health" page listing components by scope, each with overall status and the specific problems (with a per-node breakdown and a "details" link into Grafana/the resource). Backups are a first-class row (last successful backup vs SLA per CNPG cluster / etcd). The page is empty/green when healthy — an inbox of problems, not a metrics wall.
- **Optional `kubectl-cozystack` plugin**: `kubectl cozystack health [component]` for a tree/root-first terminal view. Thin sugar reading the same CRD.
- **Alerts**: `kube-state-metrics` custom-resource metrics over `status.conditions` → default `VMRule`s for known failure modes (etcd fsync latency, DRBD not UpToDate, cilium agent down, stale backups).

## Upgrade and rollback compatibility

- Purely additive: a new CRD and a new controller Deployment. No existing CRD, manifest, or API changes.
- No migration. Existing clusters gain the `Health` objects once the controller runs.
- Rollback follows dependency order: the dashboard "Cluster health" page, the KSM alert rules, and the optional plugin consume the CRD, so disable those consumers (or make them tolerate the CRD's absence) first, then remove the controller and CRD. Everything is read-only, so removal cannot corrupt managed state — only the health view disappears.

## Security

- Controller is **read-only** against component sources and only writes its own `Health` CRD. It never mutates managed resources.
- RBAC: the controller needs read across component CRDs/metrics and write on `health.cozystack.io` (via the `status` subresource). Tenant read isolation is **not** implied by the namespaced CRD — it requires explicit namespace-only `Role`/`RoleBinding`s granting `get`/`list`/`watch` on the tenant namespace only, never a cluster-wide `ClusterRole` (which would expose `cozy-system` and other tenants). The rollout must include a test that a tenant credential cannot list `cozy-system` or other tenants' `Health`.
- `spec` (including `sla` thresholds) is controller/policy-owned and not tenant-writable, enforced by RBAC (and optionally a validating/admission policy pinning `spec`). There are no tenant-supplied inputs and no new secrets. Adapters that call component REST APIs (e.g. LINSTOR) reuse existing in-cluster credentials.

## Failure and edge cases

- **Source unavailable** (slow/stalled LINSTOR, metrics gap): the component's `Health` goes `Unknown` with `observedAt` stale, not a false `Healthy`. The dashboard shows "stale/unknown", never silent green.
- **Controller stopped or `status` write failing**: a previously `Healthy` object must not stay `Healthy` indefinitely with an old `observedAt`. Every object carries a `freshUntil` deadline (a small multiple of its reconcile interval); once it passes, every consumer (dashboard, plugin, KSM alerts) treats `overall` as `Unknown`, and a watchdog alert fires when objects go stale cluster-wide (the controller itself is down).
- **Adapter breaks on a new component version**: degrades that one object to `Unknown`; other components and the core API are unaffected.
- **Management etcd**: reachable only via metrics/Talos, not the k8s API — the adapter must not assume a pod exists.
- **Node churn** (reboot, reset): per-node entries reflect current membership; stale nodes are pruned on reconcile.

## Testing

- Unit: each adapter against recorded fixtures of its source (CRD samples, metric snapshots, REST payloads), asserting the produced `conditions`.
- Integration: spin a kind/dev cluster, inject known-bad states (faulty DRBD resource, cordoned node, expired backup), assert `Health` objects report them.
- e2e/manual: reproduce a subset of the motivating incident (stalled kube-ovn DS, stale backup) and confirm the health page/`kubectl` surfaces the root fact.
- Resilience: assert a stalled/slow adapter is marked `Unknown` within its timeout without blocking other adapters, and that an object past its `freshUntil` (controller stopped) is surfaced as `Unknown` by consumers rather than a stale `Healthy`.
- RBAC: assert a tenant credential can read only its own namespace and cannot `list` `Health` cluster-wide or in `cozy-system`.

## Rollout

- Phase 1: CRD + controller + adapters for the highest-value set (platform HR graph, etcd, LINSTOR, cilium, kube-ovn, backups). `kubectl`-native only.
- Phase 2: dashboard "Cluster health" page consuming the CRD.
- Phase 3: default `VMRule`s via kube-state-metrics; optional `kubectl-cozystack health` plugin.
- Phase 4 (optional, separate proposal): bounded rule-based root-cause hints for known failure chains.

## Open questions

- CRD kind/name: `Health` (object-per-component) vs a single aggregate object per scope. Object-per-component gives cleaner `kubectl get`, per-object RBAC, and smaller writes; the aggregate gives one-shot reads. Leaning object-per-component.
- Reconcile interval and staleness threshold defaults per component.
- Backup SLA thresholds are policy-owned (not tenant-writable); still open is whether they are set directly in this CRD's `spec.sla` or mirrored from a dedicated backup-policy owner, and how precedence resolves if both exist.
- The HelmRelease adapter reports not-ready releases as facts in Phase 1; naming a single cascade root is deferred to the Phase 4 hint layer. Open: whether that hint logic lives in this controller or a separate platform-status component.
- Default `freshUntil` multiple of the reconcile interval, per component.

## Alternatives considered

- **Extend `cozystack-api` to serve health directly.** Rejected: couples release cycles, request-time fan-out to components reintroduces cascading-timeout failures, and a failing adapter would take down the core API. A dedicated controller isolates all three.
- **A standalone REST health service.** Rejected as the primary interface: not reachable via `kubectl`, needs its own auth/RBAC/ingress, and duplicates what a CRD gives natively. A thin read-only REST facade over the CRD remains possible later for consumers outside the k8s API (external status page).
- **Dashboards/alerts in Grafana only.** Rejected as sufficient: Grafana answers "how did it trend", not "what is broken right now"; backup freshness and dependency-cascade roots render poorly as time-series; and there is no `kubectl`-native surface.
- **Parse component logs.** Rejected: brittle across versions. Adapters read structured sources (CRDs, APIs, metrics) only.
