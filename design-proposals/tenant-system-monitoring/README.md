# Tenant Monitoring as system monitoring

- **Title:** `Tenant Monitoring as system monitoring`
- **Author(s):** `@kvaps`

## Overview

The `Monitoring` application does two jobs today. It is how the platform observes the workloads of a tenant, and it is also a small monitoring product handed to the tenant, with its own Grafana and its own alert console (Alerta). This proposal sets one job for it: system monitoring of the tenant. `Monitoring` collects and stores the metrics, logs and alerts of the applications the platform runs for the tenant, and the Cozystack dashboard shows them.

A user who wants to monitor what they run themselves, inside their own Kubernetes clusters or VMs, installs their own monitoring stack. Later Cozystack can offer that stack as an application of its own, separate from the system `Monitoring`.

The change is split into phases. Each phase is useful on its own, and none of them removes a feature before the dashboard covers it.

## Scope and related proposals

- `design-proposals/tenant-resource-metrics-api` (#94) and `design-proposals/tenant-application-logs` (#95): the dashboard reads metrics and logs through `observability-proxy`. This proposal builds on that read path.
- #96 (tenant alert rules and receivers): `AlertReceiver` renders Alertmanager receivers, the notification path that replaces Alerta in phase 4.
- cozystack/cozystack#5376 (`vmagent.extraRemoteWrite`, `fluent-bit.config.extraOutputs` in `monitoring-agents`): how a managed Kubernetes cluster sends its data to a stack the user runs.
- The user-facing monitoring application of phase 6 gets its own proposal.

## Decisions

## Context

- The `Monitoring` application (`packages/extra/monitoring`, rendered by `packages/system/monitoring`) deploys metrics storages (`metricsStorages`), log storages (`logsStorages`), optional trace storages, `VMAlert`, `VMAlertmanager`, Alerta and Grafana. `alerta` and `grafana` have no `enabled` switch, so every `Monitoring` gets both.
- Alerts go `VMAlert` to `VMAlertmanager` to Alerta, which sends them to Telegram and Slack. Email goes straight from Alertmanager.
- `tenant-root` runs the platform's own `Monitoring`. The cluster-wide `vmagent` and Fluent Bit write platform series and logs there, together with the data of every tenant that has no `Monitoring` of its own.
- A tenant gets its own `Monitoring` with `monitoring: true` on the `Tenant`. The application exporters of its namespaces and its logs then go to that stack.
- The dashboard shows logs and metrics of applications and namespaces (cozystack/cozystack#5218) through `observability-proxy` (#5214, #5219, #5220), under the tenant's Kubernetes RBAC.

### The problem

One application does two jobs. As system monitoring, `Monitoring` has to be cheap enough to run in every tenant and has to follow the tenant's Kubernetes RBAC. As a monitoring product for the user, it brings a second UI with its own access model and lifecycle. One chart serves neither role well.

Grafana and Alerta, each with its own database, run in every tenant that turns monitoring on, whether or not anyone opens them.

The platform's own monitoring lives in `tenant-root`, next to the data of tenants without a stack of their own, and shares storage, retention and access with them.

With logs, metrics and alerts in the dashboard, Grafana and Alerta repeat it for the common case and differ from it in access control.

## Goals

- `Monitoring` is described and documented as system monitoring of a tenant's platform-managed applications.
- A tenant `Monitoring` can run with storages and alerting only.
- The platform's own monitoring is separate from tenant data.
- Everything a tenant needs from Grafana and Alerta is in the dashboard before they are removed.
- Users have a documented way to run their own monitoring, and later a Cozystack application for it.

### Non-goals

- Changing the storage engines, the retention model or the collection agents.
- Designing the user-facing monitoring application of phase 6 in detail.

## Design

The work is done in phases. Phases 1 to 5 go in order; phase 6 can start any time after phase 1.

### Phase 1. Position `Monitoring` as system monitoring

- Documentation and the dashboard describe `Monitoring` as monitoring of the applications Cozystack runs for the tenant: managed databases, brokers, VMs, Kubernetes clusters as platform objects.
- Metrics and logs of what the user runs inside their own Kubernetes clusters are the user's to monitor. The documentation shows how: run a stack in the cluster, or send data to an external store with `extraRemoteWrite` and `extraOutputs` (#5376).
- No change to the charts.

### Phase 2. Light stack, Grafana and Alerta opt-in

- `packages/extra/monitoring` gets `grafana.enabled` and `alerta.enabled`. New tenant stacks have both off; existing stacks keep them on through the migration that adds the values.
- Without Alerta, notifications go through native Alertmanager receivers (`telegram_configs`, `slack_configs`, `email_configs`, `webhook_configs`). The settings under `alerta.alerts` are rendered as Alertmanager receivers when Alerta is off.
- The light stack is metrics and log storages, optional trace storage, `VMAlert` and `VMAlertmanager`.

### Phase 3. Platform monitoring moves to `cozy-monitoring`

- The platform's own series and logs, and the Grafana, Alerta and alerting rules operators use for the platform, move from `tenant-root` to `cozy-monitoring`.
- `tenant-root` becomes a tenant like the others: its `Monitoring` is system monitoring of its own applications.
- Operators keep Grafana and Alerta for the platform in `cozy-monitoring`; nothing in phases 4 and 5 touches them.

### Phase 4. Grafana and Alerta functions in the dashboard

- Alerts page in the dashboard, reading the tenant's Alertmanager through `observability-proxy` with the same path and RBAC scheme as #94 and #95.
- Alert rules and receivers managed from the dashboard through #96.
- Whatever else tenants use in Grafana and Alerta and the dashboard lacks is listed and added in this phase.

### Phase 5. Remove Grafana and Alerta from tenant `Monitoring`

- `packages/system/monitoring` stops rendering Grafana and Alerta for tenants. The release note tells users how to export Grafana dashboards before the upgrade.

### Phase 6. Monitoring for users as an application

- A separate application, or several (metrics, logs, traces), that a user orders to monitor what they run themselves: own scrape targets, own retention, own Grafana.
- It is independent of the system `Monitoring` and can start any time after phase 1.

## User-facing changes

- Phase 1: documentation only.
- Phase 2: new tenant stacks come without Grafana and Alerta unless asked for; alert destinations work without Alerta.
- Phase 3: operators find platform monitoring in `cozy-monitoring`.
- Phase 4: alerts, rules and receivers in the dashboard.
- Phase 5: tenants lose Grafana and Alerta.
- Phase 6: a new application in the catalog.

## Upgrade and rollback compatibility

- Phase 2 keeps existing stacks as they are; only new stacks change.
- Phase 3 moves data and components; it needs a migration plan for the platform series and dashboards and a window where both locations are readable.
- Phase 5 deletes Grafana and Alerta with their databases: saved dashboards and alert history. It goes only after phase 4, with a release note and an export path. Rollback brings the components back empty.

## Security

- Phases 2 and 5 remove two tenant-facing UIs with their own authentication; access to tenant observability data is decided by Kubernetes RBAC only.
- Phase 3 takes platform data out of `tenant-root`, so tenant-side access to `tenant-root` no longer reaches platform series.
- No new trust boundaries.

## Failure and edge cases

- Alertmanager down: `VMAlert` retries; the dashboard shows alerts as unavailable.
- A tenant upgraded through phase 2 with Alerta on and receivers configured in `alerta.alerts`: nothing changes until the tenant turns Alerta off.
- A tenant with saved Grafana dashboards at phase 5: they are removed with Grafana unless exported first.

## Testing

- Chart unit tests: with `grafana.enabled` and `alerta.enabled` off, no Grafana or Alerta objects; receivers rendered from `alerta.alerts` and from `AlertReceiver`.
- e2e: an alert fired in a tenant with the light stack reaches a webhook receiver and shows in the dashboard.
- Phase 3: platform dashboards and alerts work from `cozy-monitoring`; tenant reads through `observability-proxy` return no platform series.

## Rollout

Phases ship in separate releases, in the order above. Phase 6 runs in parallel with phases 2 to 5.

## Open questions

1. **Alertmanager replicas** in a light tenant stack: one, or three as today.
2. **Alerta features** that receivers do not cover (deduplication across sources, acknowledgement history): does anyone need them, and where would they live.
3. **Phase 3 scope:** which of today's `tenant-root` contents are platform data and which belong to `tenant-root` as a tenant.
