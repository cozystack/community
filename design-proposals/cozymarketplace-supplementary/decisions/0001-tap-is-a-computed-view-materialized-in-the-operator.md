# 0001. A Tap is a computed view, and the materializer runs in the operator

- **Number:** `0001`
- **Date:** `2026-09-07`
- **Status:** Accepted
- **Deciders:** `@IvanHunters, @kvaps`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/cozystack#3940`](https://github.com/cozystack/cozystack/pull/3940)
- **Implemented in:** [`cozystack/cozystack#3940`](https://github.com/cozystack/cozystack/pull/3940), [`cozystack/cozystack#4308`](https://github.com/cozystack/cozystack/pull/4308)

## Context

The supplementary proposal designed the marketplace backend as a set of `/marketplace/*` HTTP endpoints added to `cozystack-api`, fronted by a `TapIndex` cache controller in the same binary that would watch each tapped source's artifact revision and serve the catalog from memory. The proposal itself noted that all of the catalog state is derivable from the `PackageSource` resources plus the `ApplicationDefinition`s and chart metadata inside each tapped artifact, and that `cozystack-api` already mediates the dashboard's access to cluster resources under the existing auth and RBAC.

When the feature was built in `#3940`, that observation was taken to its conclusion: if the state is fully derivable, the backend does not need to store or cache it behind its own endpoints.

## Decision

The catalog is a cluster-scoped `Tap` resource served through the aggregated Kubernetes API and computed on read from the labelled Flux source, its `PackageSource`(s) and their `ApplicationDefinition`s. Creating a `Tap` records intent only: it writes the labelled Flux `OCIRepository`, nothing more. A controller in the operator does the materialization, pulling the artifact, creating the `PackageSource`(s), and registering the repository's apps. There are no bespoke `/marketplace/*` HTTP endpoints and no `TapIndex` cache; the dashboard drives the standard aggregated `taps` resource for connect, disconnect and read.

## Why not the alternatives

- **`/marketplace/*` endpoints plus a `TapIndex` cache, as this proposal first drew them** ([Architecture and the endpoint table](../README.md#architecture)). Every value they would serve is already derivable from the `PackageSource` plus the parsed artifact, and the aggregated API already carries auth and RBAC, so a computed, read-only resource gives the same result with no endpoint set to maintain and no cache to invalidate on each revision. This is the rejection that is new here: the proposal argued for the endpoints and the cache, and the implementation did not build them.
- **A dedicated CRD for marketplace state (`Tap`, `TapEntry`, ...).** All the necessary state is derivable, so a stored CRD adds migration cost without adding capability, and `Tap` stays a computed view rather than stored data. The proposal had already rejected this in its own Alternatives section ([_A dedicated CRD for marketplace state_](../README.md#alternatives-considered)); it is recorded here only for completeness.

## Consequences

- No cache to invalidate and no endpoint surface to keep in step with the CRDs: a read recomputes from the live `PackageSource` and the artifact metadata. The cost is paid per read rather than amortised in a cache.
- Because the catalog is the cluster-scoped `taps` resource, it inherits Kubernetes RBAC directly: create and delete are admin-only, read is granted to the tenant base roles. That is also why catalog metadata is cluster-wide readable (see the proposal's Security section).
- Two properties this proposal's re-approval ([`cozystack/community#23` review](https://github.com/cozystack/community/pull/23#pullrequestreview-5007756943)) listed were not built, and are not planned as part of this record:
  - **`visibility: private`.** There is no `visibility` field on `Tap` or `PackageSource`; catalog metadata of a private tap is readable by every authenticated user, only the pull credential is protected. Reason: the computed-view model has no place to enforce a per-tap read restriction without the bespoke endpoints that were dropped, so the control was deferred with the endpoints rather than reintroduced on its own.
  - **An `OrphanedSource` indicator.** Orphaning on disconnect works as designed (a user `Package` keeps no owner reference back to the source, and a `apps.cozystack.io/tap-materializer` finalizer gates deletion), but no dedicated orphaned-state condition is surfaced. Reason: the orphaning is the safe default on its own, and the indicator was not load-bearing for it.
- The credential-on-the-Flux-source design (a `secretRef` on the source the tap creates, the admin pre-creating the `Secret` in `cozy-system`) did ship as the proposal wrote it.

## Revisit if

Catalog scale makes per-read recomputation too slow to serve the dashboard, which would justify a cache after all; or a multi-tenant deployment needs per-tap metadata visibility, which would need either the endpoints back or a visibility field on the resource.
