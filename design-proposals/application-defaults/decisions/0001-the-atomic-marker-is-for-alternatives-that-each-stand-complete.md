# 0001. The atomic marker is for alternatives that each stand complete

- **Number:** `0001`
- **Date:** `2026-11-09`
- **Status:** Accepted
- **Deciders:** `@myasnikovdaniil, @IvanHunters`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/cozystack#5107`](https://github.com/cozystack/cozystack/pull/5107#pullrequestreview-5825099335)
- **Implemented in:** not yet ([`cozystack/cozystack#5107`](https://github.com/cozystack/cozystack/pull/5107), open)

## Context

The merge fills a user's object field by field from the installation entry and stops only at objects it cannot complete: arrays, dictionaries, free-form objects and objects marked `x-kubernetes-map-type: atomic` ([Merge rules](../README.md#merge-rules)). The proposal named one atomic object, the worker image of a node pool, whose `builtin` and `factory` exclude each other. Without the marker, a default for one of them merged into a pool that set the other, and the chart refused the render after the create had gone through.

Review of the implementation showed that the worker image was not the only object like this. IvanHunters rendered four more in the [review](https://github.com/cozystack/cozystack/pull/5107#pullrequestreview-5825099335): monitoring `oidc.customConfig`, rabbitmq `tls`, and `backup` in clickhouse and mongodb. In each, the chart refused a default for one field combined with a user value for another. A second pass over the refusals of the application charts, each pair rendered with `helm template` ([reply](https://github.com/cozystack/cozystack/pull/5107#issuecomment-6742520077)), found three shapes: a choice between alternatives that each stand complete, a pair whose fields only make sense together, and a relation between two objects or two fields at the spec root.

## Decision

`x-kubernetes-map-type: atomic` goes only on an object whose children are alternatives that each stand complete. In the platform packages that is `osImage` of a node pool, `source` of a vm-disk, and `oidc.customConfig` of a Kubernetes cluster and of a monitoring stack. The merge stores the user's value of such an object as written, so a default for a field inside the alternative the user chose is not filled in either (rows 20 and 22 of [Merge cases](../README.md#merge-cases)). The chart guide in cozystack/cozystack, `docs/agents/overview.md`, says when the marker fits.

## Why not the alternatives

- **Mark every object whose chart refuses a combination of its fields**, as the [review](https://github.com/cozystack/cozystack/pull/5107#pullrequestreview-5825099335) first asked. rabbitmq `tls` is a dependent pair: with the marker, a default `enabled: true` and a user's `disableNonTLSListeners: true` no longer combine, and that combination renders today. `backup` in clickhouse and mongodb holds schedule, retention, endpoint and CA next to the exclusive pair, and those defaults would be lost ([reply](https://github.com/cozystack/cozystack/pull/5107#issuecomment-6742520077)).
- **Check the merged spec against the chart's own rules before saving it**, suggested in the same review. Most of the relations sit between objects or at the spec root, where no marker can express them, so this is the guard that would catch them. It needs the rules declared in the schema and evaluated by the API, which the API does not do yet ([`cozystack/cozystack#2657`](https://github.com/cozystack/cozystack/issues/2657)), and most charts declare none. It is a separate piece of work, not a variant of this one.

## Consequences

A user who picks an alternative of an atomic object writes it in full, and the installation entry adds nothing inside it. An entry that sets `osImage.factory.imageFactoryURL` does not reach a pool that sets `osImage.factory.version`; that pool takes its factory from `talos.imageFactoryURL`, which is not atomic and is still filled from the entry.

A default for one field of a dependent pair or of a mixed object, combined with a user value for the other, can still produce a spec the chart refuses. The create succeeds and the HelmRelease fails its render, visibly and for that application only. That is reachable today through rabbitmq `tls`, `backup` in clickhouse, mariadb, mongodb and postgres, `volume` in seaweedfs, and relations between top-level fields such as `cloudflareProxy` and `proxyProtocol` of the ingress.

Catalog authors carry the rule: a new object of alternatives needs the marker in its own schema. The `applicationDefaults` comment in the platform `values.yaml` says so.

## Revisit if

The API evaluates rules declared in the application schema ([`cozystack/cozystack#2657`](https://github.com/cozystack/cozystack/issues/2657)), so the merged spec can be checked against them on create.

---

<!--
Format follows Michael Nygard's architecture decision records
(https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
and MADR (https://adr.github.io/madr/).

Once merged, the header block above is maintained (Status, Superseded by,
Implemented in must track reality) and the prose below it is frozen.
-->
