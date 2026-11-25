# 0001. The live `KamajiControlPlane`, not `_namespace.etcd`, decides whether a cluster owns its etcd

- **Number:** `0001`
- **Date:** `2026-10-08`
- **Status:** Accepted
- **Deciders:** `@lllamnyp, @lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/cozystack#4817`](https://github.com/cozystack/cozystack/pull/4817), argued on [`cozystack/community#25`](https://github.com/cozystack/community/pull/25#pullrequestreview-5491419490)
- **Implemented in:** [`cozystack/cozystack#4817`](https://github.com/cozystack/cozystack/pull/4817)

## Context

The proposal as reviewed said the chart would honour an explicitly set `_namespace.etcd` for clusters that already used a shared etcd, and give every new cluster its own, including one created under an ancestor that runs a shared etcd. The delete hook was to branch on the same condition.

@lexfrei's [review of the proposal](https://github.com/cozystack/community/pull/25#pullrequestreview-5491419490) showed the condition was undefined: `cozystack-basics` hardcodes `_namespace.etcd: tenant-root` and the tenant chart passes the parent's value down unchanged, so every tenant namespace publishes a value whether or not an etcd runs behind it. "Explicitly set" could not tell a new cluster from an existing one. The same review named the live `KamajiControlPlane`'s `spec.dataStoreName` as the discriminator.

A changed `dataStoreName` on a live cluster is not a rename. The CAPI provider re-projects it onto the `TenantControlPlane` on every reconcile, which starts a Kamaji datastore migration: writes frozen while it copies, and every worker kubelet needing a restart afterwards.

## Decision

`kubernetes.etcd.sharedDataStore` (`packages/apps/kubernetes/templates/_helpers.tpl`) looks up the release's own `KamajiControlPlane`. If it exists and names a `DataStore` other than the cluster's own `<namespace>.<release>`, the chart keeps that `DataStore` and renders exactly what it rendered before, pre-delete hook included. Otherwise — no `KamajiControlPlane`, or one already on its own `DataStore` — the cluster owns its etcd. `_namespace.etcd` is not read. The render and the delete hook use the same helper, so they cannot disagree.

## Why not the alternatives

- **Honour a non-empty `_namespace.etcd`**, the proposal's own wording. Every tenant resolves it to at least `tenant-root`, so it would keep every new cluster on a `DataStore/tenant-root` that may not exist, and the per-cluster path would never be taken ([lexfrei, point 1](https://github.com/cozystack/community/pull/25#pullrequestreview-5491419490), [inline](https://github.com/cozystack/community/pull/25#discussion_r4248519921)).
- **Keep following `_namespace.etcd` for existing clusters**, as the chart did before. Turning `spec.etcd` on for a mid-level tenant then silently moves every cluster below it to a new datastore, which is the Kamaji migration above, triggered by an unrelated edit ([cozystack#4817](https://github.com/cozystack/cozystack/pull/4817), "New clusters vs existing ones").

## Consequences

- An upgrade rolls no control-plane pods: an existing cluster's render is byte-identical apart from randomly generated Talos tokens.
- A cluster stays on its shared `DataStore` whatever its ancestors' `spec.etcd` later does, so moving one is only ever the deliberate runbook.
- The chart depends on a `lookup` of its own object. Offline `helm template` and helm-unittest without a mock see only the empty branch, the new-cluster render; the shared branch is tested with a mocked lookup, and no e2e lane exercises it, since the chart can no longer create a cluster on a shared etcd.
- A cluster on a shared etcd whose `KamajiControlPlane` is absent at render time — deleted by hand, or not yet restored — is read as new and gets an empty `DataStore` of its own ([androndo, on cozystack#4817](https://github.com/cozystack/cozystack/pull/4817#pullrequestreview-5988920673)). This is not handled.

## Revisit if

Phase 4 removes the shared-`DataStore` branch, or a restore path recreates a cluster's HelmRelease before its `KamajiControlPlane`.
