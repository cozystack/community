# 0001. The finish step is a migration-created, least-privilege Job, not a chart-rendered cluster-admin one

- **Number:** `0001`
- **Date:** `2026-11-16`
- **Status:** Accepted
- **Deciders:** `@IvanHunters`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/cozystack#4922`](https://github.com/cozystack/cozystack/pull/4922)
- **Implemented in:** [`cozystack/cozystack#4922`](https://github.com/cozystack/cozystack/pull/4922)

## Context

The proposal's Upgrade and Security sections fixed the second part of a conversion as a Job the platform release renders after the charts switch, run under cluster-admin, not a Helm hook, rerun by hand, and exiting non-zero while a release is left out so "one place shows what is left." It was written before any kind converted; three facts found while implementing the Postgres wave against `cozystack/cozystack` main did not fit.

A chart-rendered Job has a fixed name and an image that changes every platform build (the migrations image is digest-pinned in platform values). A Job's `spec.template` is immutable, so the first platform upgrade after conversion carrying a newer image could not patch it, and the platform HelmRelease, built with `RetryOnFailure` and without `Force`, retried forever. helm-controller also waits for a release's Jobs by default, and finish runs with `--timeout=30m` and exits non-zero while any release is left out, so the release waited on it — contradicting the proposal's own "not a Helm hook, so a long wait does not hold up the upgrade." `DisableWaitForJobs` is set on the whole generated HelmRelease, not per-Job, so it cannot be opted out for this one Job while kept for the rest.

cluster-admin was more than the Postgres wave uses. For Postgres, finish does CRUD on Secrets, `get`/`list`/`patch` on HelmReleases, `create` on Events, and get/create/update on its state ConfigMap — not `pods/exec`, which only the later MariaDB-`root` rotation needs. The same PR's pre-upgrade role-adoption check already ran under a namespaced Role with a Helm-managed lifecycle, so cluster-admin for finish stood out as a broader grant than the work in front of it.

The proposal's signal for an incomplete conversion was the Job's non-zero exit, but nothing consumed it: a detached Job self-deletes on its TTL, and the state ConfigMap it leaves behind has no reader. A conversion that permanently left a release's plaintext readable — the one thing the feature exists to prevent — was therefore fail-open, and its evidence vanished after the TTL.

## Decision

The Postgres conversion's finish Job is created by migration 61's pre-upgrade hook with `kubectl apply`, not rendered by the platform chart, so it runs outside the platform HelmRelease and blocks no upgrade; the hook deletes and recreates it rather than patching its immutable template, and it runs the image the hook runs as, read from its own pod. It binds a least-privilege ClusterRole (Secrets, HelmReleases, Events, ConfigMaps — no cluster-admin, no `pods/exec`), and its ServiceAccount is owned by the Job so the garbage collector removes it with the Job. While any release is left out, credentials-migrate labels its state ConfigMap `credentials.cozystack.io/incomplete=true`; a `CredentialsMigrationIncomplete` alert keys on that label through kube-state-metrics and fires until a finish run leaves none out, so the fail-open is observable and the signal outlives the Job's TTL because the ConfigMap does.

## Why not the alternatives

- **The proposal's chart-rendered Job (the original design).** Its immutable pod template plus a per-build image made it unpatchable on the next upgrade, and helm-controller waited on a 30-minute Job that exits non-zero while a release is skipped, holding up the upgrade — the failure mode the proposal said it avoided.
- **Keep it in the chart, with the image in the Job name and a TTL to dodge the immutability.** A new name per image sidesteps the immutable patch, but helm-controller still waits on the Job, and the wait cannot be disabled for one Job: `DisableWaitForJobs` is a property of the generated HelmRelease, not of a Job. The upgrade still blocks.
- **cluster-admin, as the proposal states.** It grants far more than the Postgres wave uses; a least-privilege ClusterRole covers finish exactly, and the proposal's own `pods/exec` requirement belongs to a later wave (MariaDB `root`), not this one. The scoped pattern was already in the same PR's pre-upgrade check.
- **Treat the Job's non-zero exit as the signal, as the proposal does.** Nothing consumes it and the Job TTLs away, so a stuck conversion is silent and leaves no durable trace. A label on the never-deleted state ConfigMap, surfaced as an alert, is consumed and durable.
- **A reconciler in the operator (the clean end state).** It owns the finish work with Helm-managed, scoped RBAC, reconciles forever instead of a `backoffLimit` give-up, and exposes per-release status as a condition — removing the detached Job, the image-from-pod read, the ServiceAccount garbage-collection dance, and the fire-and-forget signal at once. It lost here on cost, a judgement call rather than a hard fact: a new controller with its own watch, RBAC and status is a larger change and a bigger deviation than the wave needs, and it is left as follow-up. The proposal's Open Questions names a reconciler, but for a different signal (whether the engine applied a mint), not for running the conversion.

## Consequences

- Nothing reconciles the finish Job: if it is deleted it is not recreated, and it is rerun by hand (delete the Job, re-apply its manifest from the migration). The version is stamped once the Job is created, before finish completes, so "the migration ran" and "the plaintext is retired" are distinct facts, joined only by the alert — which is why the alert is not optional.
- A least-privilege ClusterRoleBinding on a fixed name outlives the garbage-collected ServiceAccount on the permanent-failure path; an operator clearing a stuck release deletes the binding and role by hand. This is a smaller residual than a lingering cluster-admin binding, but it is a residual.
- The alert consumer needs the monitoring stack (kube-state-metrics with the `configmaps` collector, and the VictoriaMetrics operator for the VMRule) on a migration-enabled variant. The default variants ship it; a migration-enabled variant without it runs the conversion but surfaces no alert for a release left out.
- The clean end state — a reconciler — is now a named follow-up rather than built, and later waves (MariaDB, ClickHouse) inherit this Job shape and will need `pods/exec` and forced upgrades added to the scoped role, or the reconciler, before they convert.

## Revisit if

A reconciler for the conversion is built, which would carry the finish work under Helm-managed scoped RBAC, retry without giving up, and report status as a condition — superseding this Job. Or helm-controller gains a per-Job wait opt-out, which would let a chart-rendered Job avoid holding up the upgrade and make keeping finish in the chart viable again.
