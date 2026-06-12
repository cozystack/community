# Multi-target log forwarding from kubernetes-tenants to ancestor VictoriaLogs

- **Title:** Multi-target log forwarding from `kubernetes` (k8s-tenant) clusters to ancestor VictoriaLogs instances
- **Author(s):** `@IvanHunters`
- **Date:** 2026-06-12
- **Status:** Draft

## Overview

Tenants of type `Kubernetes` (managed control plane via Kamaji + KubeVirt worker VMs) currently ship their container logs to exactly one VictoriaLogs instance — the immediate parent tenant's `vlinsert-generic`. Operators of the platform-root tenant therefore cannot see logs from a child `kubernetes` tenant in their Grafana, and cannot run cluster-wide log queries (incident response, security forensics, cross-tenant trend analysis) without hopping between per-tenant Grafanas.

This proposal extends the `monitoring-agents` chart (provisioned by the `kubernetes` app for each managed tenant cluster) with a values-driven list of **additional log destinations**. The default behaviour stays identical to today (single-target, immediate parent). When the operator opts in, fluent-bit fans out the same log stream to multiple ancestor `vlinsert` endpoints, so a single log entry appears in both the immediate parent's VictoriaLogs and every chosen upstream's VictoriaLogs.

## Scope and related proposals

This proposal is concerned with the **fan-out** direction of log routing — replicating one source stream to N ancestor destinations. It is complementary to the **split-routing** direction proposed in [cozystack/cozystack#2195](https://github.com/cozystack/cozystack/pull/2195) (per-tenant `rewrite_tag` + per-tenant outputs inside a single shared `monitoring-agents`). Both share the underlying mechanism of templating extra `[OUTPUT]` blocks into fluent-bit config; both should land before either is removed from `valuesOverride` workarounds.

It also depends on `tenant.cozystack.io/*` ancestor labels being correctly propagated onto every tenant namespace — see [cozystack/cozystack#2810](https://github.com/cozystack/cozystack/issues/2810) and [cozystack/cozystack#2912](https://github.com/cozystack/cozystack/pull/2912). Without that fix the Cilium policy half of this proposal cannot work as-is for tenants at depth 2 and below.

Out of scope here:

- Authentication on `vlinsert` endpoints. Today `vlinsert-generic` accepts unauthenticated writes inside the cluster; if that ever changes, this proposal will need a follow-up for credential propagation.
- VictoriaLogs replication / stream copying server-side. Fan-out happens at the fluent-bit layer; we do not introduce a new server-side replication topology.

## Context

The pieces involved live in three Helm packages today:

- `packages/system/monitoring-agents/` — the chart with the fluent-bit `ConfigMap`. Its `values.yaml` exposes `fluent-bit.config.inputs/filters/outputs` as raw strings, with `outputs` rendering a single `[OUTPUT]` block hard-wired to `vlinsert-generic.{{ .Values.global.target }}.svc`. Source: [values.yaml#L343-L369](https://github.com/cozystack/cozystack/blob/main/packages/system/monitoring-agents/values.yaml).
- `packages/apps/kubernetes/templates/helmreleases/monitoring-agents.yaml` — the `HelmRelease` that the `kubernetes` (k8s-tenant) chart materialises in the management cluster's `tenant-<name>` namespace for each managed tenant cluster. It feeds `_namespace.monitoring` (the immediate parent tenant's name) into `fluent-bit.config.outputs` so the rendered host becomes `vlinsert-generic.<parent>.svc.cozy.local:9481`. Source: [helmreleases/monitoring-agents.yaml](https://github.com/cozystack/cozystack/blob/main/packages/apps/kubernetes/templates/helmreleases/monitoring-agents.yaml).
- `packages/apps/tenant/templates/networkpolicy.yaml` — the `CiliumClusterwideNetworkPolicy` `tenant-<name>-egress` that whitelists egress from a tenant's pods to specific apps in ancestor namespaces (today: `vminsert`, `etcd`, `cozystack.io/service: ingress`). `vlinsert` is missing and is being added in [cozystack/cozystack#2910](https://github.com/cozystack/cozystack/pull/2910).

The cross-cluster DNS suffix `svc.cozy.local` resolves a service from the management cluster's `tenant-<name>` namespace from inside a tenant Kubernetes cluster; the routing path goes through the konnectivity tunnel + KubeOVN egress that the `kubernetes` chart sets up.

### The problem

Concrete operator scenarios that are awkward or broken today:

- *"A team-cozystack operator wants to grep the last 24 hours of logs from a customer's `kubernetes` tenant during an incident."* Today the operator opens `https://grafana.aspan.pro` (root tenant Grafana), realises the logs are not there, asks for credentials to the customer tenant's Grafana, logs in, runs the query, and screenshots the result back. There is no single pane.
- *"A new k8s-tenant gets `addons.monitoringAgents.enabled: true` and the operator expects logs to land in the platform-wide Grafana."* They never arrive — the implicit single-target output ships them to a VictoriaLogs the platform operator does not have visibility into.
- *"The tenant-side VictoriaLogs goes down for an hour during a chart upgrade."* All logs from that tenant's pods are lost for the window, because there is no second destination buffering them.

The operator workaround today (used in production on the TTK platform) is to manually patch the rendered `monitoring-agents` `HelmRelease` to add a second `[OUTPUT]` block, then `suspend: true` the parent `kubernetes` `HelmRelease` to prevent Helm reconciliation from reverting the patch. Suspending the parent freezes all addon updates for that tenant, which is acceptable as an emergency unblock but unsustainable as a permanent state.

## Goals

- Operators can declare a list of extra VictoriaLogs destinations on a `Kubernetes` CR, and `monitoring-agents` inside the resulting tenant cluster ships every log line to all of them in addition to the implicit immediate parent.
- The default (no destinations declared) renders bit-for-bit identical fluent-bit `ConfigMap` content as today.
- The Cilium `tenant-<X>-egress` policy automatically grants egress for the declared destinations, with no further manual patching.
- A 3-level hierarchy works without manual labelling: `tenant-root → tenant-A → tenant-A-B`, where `tenant-A-B`'s `kubernetes` CR opts into forwarding to both `tenant-A` and `tenant-root`.
- Multiple inputs (kube container logs, audit, kubelet events) all respect the same destination list — that is, every input that ships to the implicit destination also ships to every declared destination.

### Non-goals

- Partial fan-out per input class (e.g. "ship kube container logs to both, but audit only to parent"). The proposal treats all configured inputs uniformly; if asymmetric routing is needed we can layer a follow-up proposal on top once a use case lands.
- Server-side replication (e.g. `vlinsert` chaining or VictoriaLogs cluster federation). The fan-out happens client-side in fluent-bit.
- A UI / dashboard exposing which destinations are wired up; the source of truth is the `Kubernetes` CR and `kubectl get` is sufficient.
- Authentication / authorization on the cross-tenant write. Today `vlinsert-generic` is open inside-cluster; if that changes, a follow-up proposal will handle credential plumbing.

## Design

### Data model: extend the `Kubernetes` app values

A new optional field is added under `addons.monitoringAgents`:

```yaml
apiVersion: apps.cozystack.io/v1alpha1
kind: Kubernetes
spec:
  addons:
    monitoringAgents:
      enabled: true
      additionalLogTargets:
        - parent          # immediate parent (default behaviour, listed for clarity)
        - tenant-root
      valuesOverride: {}
```

Each entry is either:

- a **shorthand** — `parent` (the immediate parent tenant), `root` (the tenant-root), or `ancestors` (all ancestors including root); resolved at chart render time from the `_namespace` chain that's already passed into the `kubernetes` chart;
- or an **explicit tenant name** — `tenant-ktj`, etc. — for cases where the operator wants to target a specific intermediate ancestor.

When `additionalLogTargets` is empty or absent, the rendered output is exactly today's single-destination config — no behaviour change.

### Chart template: `kubernetes` app pushes the list into `monitoring-agents` values

`packages/apps/kubernetes/templates/helmreleases/monitoring-agents.yaml` deduplicates the implicit destination + the declared list, resolves shorthand entries, and renders one `[OUTPUT]` block per resolved destination:

```yaml
# in helmreleases/monitoring-agents.yaml
{{- $primary := .Values._namespace.monitoring }}
{{- $targets := list $primary }}
{{- range .Values.addons.monitoringAgents.additionalLogTargets | default list }}
  {{- $resolved := include "kubernetes.resolveLogTarget" (dict "shorthand" . "ctx" $) }}
  {{- $targets = append $targets $resolved }}
{{- end }}
{{- $targets = $targets | uniq }}

spec:
  values:
    fluent-bit:
      config:
        outputs: |
          {{- range $targets }}
          [OUTPUT]
              Name http
              Match kube.*
              Host vlinsert-generic.{{ . }}.svc.cozy.local
              port 9481
              compress gzip
              uri /insert/jsonline?_stream_fields=stream,kubernetes_pod_name,kubernetes_container_name,kubernetes_namespace_name&_msg_field=log&_time_field=date
              format json_lines
              json_date_format iso8601
              header AccountID 0
              header ProjectID 0
          {{- end }}
```

The helper `kubernetes.resolveLogTarget` walks the `_namespace.parents` chain (already required by [cozystack/cozystack#2912](https://github.com/cozystack/cozystack/pull/2912)) and maps `parent`/`root`/`ancestors`/`<explicit>` to concrete tenant namespace names.

`monitoring-agents` itself does not change shape — its existing `fluent-bit.config.outputs` field already accepts arbitrary fluent-bit config text, so multi-block content renders cleanly without a chart edit.

### Network policy: extend `tenant-<X>-egress`

The `tenant` chart's egress `CCNP` already loops over ancestor namespaces for each whitelisted app (`vminsert`, `etcd`, `ingress`). Adding `vlinsert` to that loop is the change covered by [cozystack/cozystack#2910](https://github.com/cozystack/cozystack/pull/2910); this proposal depends on that PR landing first.

No new policy is introduced here — the existing template change is sufficient for any ancestor destination, including non-immediate ones, once the `tenant.cozystack.io/*` label chain is correct (depends on [cozystack/cozystack#2912](https://github.com/cozystack/cozystack/pull/2912)).

### Diagram

```mermaid
flowchart LR
  subgraph tenant-ktj-htdev cluster
    FB[fluent-bit DaemonSet]
  end

  subgraph mgmt cluster
    subgraph tenant-ktj ns
      VLktj[vlinsert-generic.tenant-ktj]
    end
    subgraph tenant-root ns
      VLroot[vlinsert-generic.tenant-root]
    end
  end

  FB -->|implicit / primary| VLktj
  FB -.->|additionalLogTargets: [root]| VLroot
```

Solid edge = always present. Dashed edge = enabled by `additionalLogTargets`.

## User-facing changes

- New optional field `spec.addons.monitoringAgents.additionalLogTargets` on the `Kubernetes` (k8s-tenant) CR. Strings; empty list / absent = no change from today.
- No CLI / dashboard / docs entry-point changes besides a one-paragraph addition to the `kubernetes` app reference docs and a worked example in the monitoring docs ("how to make root tenant Grafana see all child cluster logs").
- No CRD schema bump needed (field is additive and optional under an already-existing object).

## Upgrade and rollback compatibility

- Existing `Kubernetes` resources keep working unchanged. They render the same single-target output they do today.
- Setting `additionalLogTargets` is a forward-only operation in terms of log delivery — historical logs already written to the old single destination are not back-filled to the new ones. This is consistent with how every fan-out config in Cozystack works today (e.g. flipping `monitoring: true` on a tenant).
- Removing destinations is supported: fluent-bit reloads its `ConfigMap` on the next agent rollout (handled by the `cozy-monitoring-agents` chart's `checksum/config` annotation), and stops shipping to the removed target.
- No data migration. No CRD conversion.

## Security

- The change relaxes Cilium egress for the affected tenant: pods can now send arbitrary HTTP traffic to ancestor `vlinsert` endpoints. This is symmetric with the existing `vminsert` egress allowance and does not introduce a new trust boundary that didn't already exist for metrics.
- Logs forwarded to an ancestor become visible to anyone with `vlogs-generic` read access in that ancestor's tenant. Operators opting in must already accept this — but it is worth documenting in the chart README, since the implication ("root tenant operators will see your container logs") is non-obvious from a one-liner in values.
- No new secrets stored or transmitted. The current `vlinsert` HTTP API does not require credentials inside the cluster mesh.
- No new RBAC surface.

## Failure and edge cases

- *One of the declared targets is down.* fluent-bit's HTTP output has independent retry / buffer state per `[OUTPUT]` block; the healthy destination continues receiving logs and the dropped target queues up to the configured buffer limit, then drops oldest. Behaviour matches single-output today, just per-destination.
- *A declared target tenant does not have a `vlinsert-generic` service.* The cross-cluster DNS lookup `vlinsert-generic.<name>.svc.cozy.local` returns NXDOMAIN. fluent-bit logs the error every retry interval and otherwise no-ops. The other outputs are unaffected. No render-time validation — operator misconfiguration surfaces as a runtime error in fluent-bit logs, which is acceptable for a v1.
- *Cilium policy lag after first apply.* When a brand-new tenant resource is created with a non-empty `additionalLogTargets`, the egress `CCNP` propagation may briefly lag behind fluent-bit startup; fluent-bit will retry and recover within seconds.
- *Operator declares the implicit primary in the list (`parent` and itself).* Deduplication in the template renders one block, not two; idempotent.
- *Operator declares an `<explicit>` tenant that is not in the ancestor chain.* The egress policy will not whitelist it (the policy template only iterates the ancestor chain). Operator sees fluent-bit retry-loop errors. We accept this as a configuration error and document it; we do not add render-time validation in v1.

## Testing

- **Unit (helm-unittest):** A test in `packages/apps/kubernetes/tests/` covers the rendered HelmRelease for monitoring-agents under three input shapes — `additionalLogTargets` absent, `additionalLogTargets: [parent]` (idempotent), `additionalLogTargets: [tenant-root, parent]` (two distinct OUTPUT blocks). Assertions are exact-string matches on the rendered `fluent-bit.config.outputs`.
- **Integration (CI dev cluster):** Add an `e2e` step that creates a 2-level `Kubernetes` tenant with `additionalLogTargets: [root]`, generates synthetic log traffic, and asserts that a LogsQL query against the root tenant's `vlselect-generic` returns matching entries within 30 s. Reuses the existing `e2e` harness in `hack/`.
- **Manual smoke (release candidate):** Bring up a 3-level hierarchy (`root → A → A-B`), opt `A-B` into forwarding to both `A` and `root`, run a workload that logs deterministically, verify the same entries appear in all three Grafanas. Already exercised manually on the TTK production cluster prior to this proposal.

## Rollout

- **Release N (this proposal accepted):** ship the `kubernetes` chart change behind the new field. Default behaviour unchanged. Document under `docs/v1.5/operations/services/monitoring/logs/`.
- **Release N+1:** the `cozystack:debug` runbook references this mechanism instead of the manual-`suspend`-and-patch workaround currently documented in operator notes.
- No deprecations.

## Open questions

- **Shorthand naming.** `parent` / `root` / `ancestors` is convenient but introduces a small DSL on top of YAML. The alternative is requiring operators to write out concrete tenant names everywhere, with an external operator pre-rendering them. The DSL feels worth it for the common case ("send to root"), but reviewers may prefer the explicit form.
- **Should `additionalLogTargets: [ancestors]` skip the immediate parent (since it's already implicit) or include it?** Current text deduplicates, which means `[ancestors]` is equivalent to all ancestors. That seems least surprising but reviewers might want to see an explicit toggle.
- **Should the `monitoring-agents` chart move the multi-output rendering into its own template** rather than expecting the caller (the `kubernetes` chart) to pre-render the multi-block string? This would centralise the logic and let the `tenant` chart's same use case in [cozystack/cozystack#2195](https://github.com/cozystack/cozystack/pull/2195) reuse it. Trade-off is a larger surface change in `monitoring-agents`. The proposal as written keeps it minimal; if #2195's author wants to consolidate after this lands, that is a clean follow-up.

## Alternatives considered

- **Server-side replication via `vlinsert` chaining.** VictoriaLogs has no built-in cross-instance write replication; an out-of-band tool (vmagent-for-logs?) would have to consume one stream and re-emit. More moving parts, no clear win over fan-out at the producer.
- **Single `vlinsert` shared across the entire platform, dropping per-tenant VictoriaLogs.** Centralises storage and removes the routing problem, but breaks the tenant-data-locality model that Cozystack already commits to elsewhere (each tenant owns its monitoring stack). Too far a departure from the existing model for a logging-only proposal.
- **Per-input destinations (asymmetric routing).** Solves a use case nobody has reported yet ("audit logs go to root only, container logs to parent only"). Adds combinatorial complexity to the values schema. Deferred to a follow-up if and when a real ask lands.
- **Use `vmagent`'s remote_write fan-out as a model and bolt a parallel `vlagent` in front of `vlinsert`.** No such daemon exists upstream for VictoriaLogs today. Doing it client-side via fluent-bit is the simpler path and matches what operators already write by hand.
- **Drive the destination list from a separate `MonitoringRouting` CR.** More CRDs for a feature that is fundamentally a per-tenant config knob. Defers all the day-2 questions (RBAC, ownership, sync) into a layer that doesn't earn its complexity.

---

<!--
Inspired by KubeVirt enhancement proposals
(https://github.com/kubevirt/enhancements) and Kubernetes Enhancement
Proposals (KEPs).
-->
