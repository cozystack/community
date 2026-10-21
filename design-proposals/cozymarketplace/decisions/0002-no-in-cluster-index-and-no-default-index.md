# 0002. There is no in-cluster index and no default index

- **Number:** `0002`
- **Date:** `2026-09-02`
- **Status:** Accepted
- **Deciders:** `@IvanHunters`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/cozystack#3940`](https://github.com/cozystack/cozystack/pull/3940)
- **Implemented in:** [`cozystack/cozystack#3940`](https://github.com/cozystack/cozystack/pull/3940) (publication scaffold under `hack/packages-index`)

## Context

The main proposal described a single meta-repository index, krew-index style, with one descriptor per published repository, and the supplementary proposal added a CI publication gate that would run on pull requests to that index repository. Both assumed an index would exist to point at.

What shipped is narrower. `cozypkg` can search an index when given its URL, and the publication side exists as a template under `hack/packages-index` (a validation workflow and an entry-validator script, with a recorded cosign identity per entry). No `cozystack/packages-index` repository was created, and no index is wired into a cluster by default.

## Decision

Ship no in-cluster index resource and no default index repository. Index entries stay a flat list as first proposed. The publication side is a template only, under `hack/packages-index`, for a future index repository to adopt. Discovery works against an index URL the operator supplies; nothing points at an official index out of the box.

## Why not the alternatives

- **Ship a default or official index repository now.** It would commit the maintainers to curating that repository and to operating the two-lane auto-merge gate before the repository-as-unit model is proven in the field. This was a deferral in the plan rather than an option argued down in one thread: the main proposal already staged the index to [_Later phases_](../README.md#rollout), and the 1.7 marketplace epic ([`cozystack/cozystack#4981`](https://github.com/cozystack/cozystack/issues/4981)) tracks the package index as not-yet-built. It rests on that judgement, not on a hard constraint.
- **An in-cluster index resource or CRD.** The index is a publish-side artifact, a list of repositories, not cluster state; nothing running in the cluster needs it to be a resource. Keeping it out of the cluster avoids a CRD that would only mirror a file.

## Consequences

- Discovery needs an index URL supplied out of band; there is no default catalog an operator lands on. A default catalog storefront is tracked separately and waits on the items above.
- The publication template is not yet a working gate. As of 2026-10-20, its pinned `cozypkg` version predates the `validate` subcommand it invokes, and its auto-merge lane does not yet compare entry name, pull-request author, or version order. These are recorded as open items in the 1.7 marketplace epic ([`cozystack/cozystack#4981`](https://github.com/cozystack/cozystack/issues/4981)), not resolved here; this bullet is a snapshot and will go stale once the gate is fixed.
- Because there is no default index, the trust model has no official-versus-community distinction to anchor on yet; that waits on an index existing.

## Revisit if

The default catalog storefront work is picked up, which would create a real index repository and turn the `hack/packages-index` template into an operating publication gate.
