# 0003. The pre-delete hook releases Kamaji's datastore finalizer on a cluster's own etcd, and fails before that etcd's PKI goes if it cannot

- **Number:** `0003`
- **Date:** `2026-11-20`
- **Status:** Accepted
- **Deciders:** `@lllamnyp, @lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/cozystack#4817`](https://github.com/cozystack/cozystack/pull/4817#discussion_r4266874140)
- **Implemented in:** [`cozystack/cozystack#4817`](https://github.com/cozystack/cozystack/pull/4817)

## Context

The proposal as reviewed ordered deletion inside the pre-delete hook: let the `TenantControlPlane` delete, clear the datastore-secret finalizer, then remove the `EtcdCluster` and `DataStore`, so that Kamaji never dials a datastore that is gone.

Kamaji's `finalizer.kamaji.clastix.io` on the `TenantControlPlane` drops the tenant's role, prefix and user from the datastore, and it completes only while the `DataStore` is Ready and answering. For a cluster on its own etcd, all three live in the etcd the same uninstall deletes. If that etcd never came up or has lost quorum, the `TenantControlPlane` never finishes deleting. A pre-delete hook that fails keeps the Helm release, and Flux retries the same teardown.

@themoriarti's [finding 1](https://github.com/cozystack/cozystack/pull/4817#discussion_r4266874140) on cozystack#4817 established that, and asked for the finalizer to be released on this branch. @lexfrei's [later review](https://github.com/cozystack/cozystack/pull/4817#discussion_r4629459788) found that releasing it unconditionally was not safe either.

## Decision

On a cluster that owns its etcd, when the `TenantControlPlane` outlives the hook's wait and its `spec.dataStore` is the cluster's own `DataStore`, the hook removes `finalizer.kamaji.clastix.io` with a JSON patch that tests the value at the index it read, and waits again. Before deleting the etcd's PKI it re-reads the `TenantControlPlane`, and exits non-zero if it cannot read it or if it still carries the finalizer on the own `DataStore`. On a shared `DataStore` the hook never touches the finalizer. Helm deletes release resources only after the pre-delete hook, so no ordering inside the hook is needed for the etcd to outlive Kamaji's normal finalisation. Steps 4a and 8 in `packages/apps/kubernetes/templates/delete.yaml` carry the mechanism; `hack/kubernetes-pre-delete-hook.bats` pins both branches.

## Why not the alternatives

- **Order the deletion inside the hook**, the proposal's own design, also suggested on [community#25](https://github.com/cozystack/community/pull/25#discussion_r3498195690). Unnecessary: the etcd is a release resource, which Helm deletes only after the pre-delete hook completes.
- **Fail the hook while the `TenantControlPlane` outlives the wait**, an earlier revision of cozystack#4817 ([lexfrei, B2](https://github.com/cozystack/cozystack/pull/4817#discussion_r4248584784)). With the etcd down, the finalizer never clears, so Flux retries forever and the cluster cannot be deleted at all ([themoriarti, finding 1](https://github.com/cozystack/cozystack/pull/4817#discussion_r4266874140)).
- **Release the finalizer and exit 0 whatever happens.** If the patch is rejected, Helm goes on to delete the etcd while the finalizer still waits on it, leaving the `TenantControlPlane` and its datastore-config Secret stuck until repaired by hand ([lexfrei](https://github.com/cozystack/cozystack/pull/4817#discussion_r4629459788)).

## Consequences

- A cluster whose own etcd is down can still be deleted without manual work.
- The tenant's role, prefix and user are never cleaned from that etcd, which is harmless because the etcd is deleted next.
- The hook's Role gains `patch` on the release's own `TenantControlPlane`, rendered only for a cluster that owns its etcd.
- The hook's waits on this path add up to 120 + 120 + 60 seconds, which equals Flux's default 5-minute uninstall timeout; the HelmRelease sets none, so the first uninstall of such a cluster likely times out and completes on retry ([androndo, on cozystack#4817](https://github.com/cozystack/cozystack/pull/4817#pullrequestreview-5988920673)).

## Revisit if

Kamaji skips its datastore cleanup when the `DataStore` itself is being deleted, or the Kubernetes application's HelmRelease gains an uninstall timeout.
