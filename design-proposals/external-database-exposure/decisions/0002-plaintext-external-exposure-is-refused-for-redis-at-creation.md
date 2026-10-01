# 0002. Plaintext external exposure is refused for redis, where TLS is decided at creation, and not keyed on postgres tls.enabled

- **Number:** `0002`
- **Date:** `2026-10-01`
- **Status:** Accepted
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/cozystack#4639`](https://github.com/cozystack/cozystack/pull/4639)
- **Implemented in:** [`cozystack/cozystack#4639`](https://github.com/cozystack/cozystack/pull/4639)

## Context

The proposal's Security section had one rule for every engine: an unset `tls.enabled` followed `external` through the chart's tri-state, and an explicit `tls.enabled: false` with `external: true` was refused at admission. It was written while redis had no TLS support in the tree, and the rule matched the postgres chart.

Two facts found while implementing it did not fit.

Postgres `tls.enabled` did not decide whether the server took plaintext. CloudNativePG listed `ssl` as a fixed parameter set to `on`, and its default pg_hba ended with `host all all all <method>`, which matched TLS and non-TLS connections alike (both in `pkg/postgres/configuration.go` at v1.30.0). The chart's `tls.enabled` only decided whether the external hostname went into the certificate. An external postgres accepted a client that asked for `sslmode=disable` whatever `tls.enabled` said ([`cozystack/cozystack#4619`](https://github.com/cozystack/cozystack/issues/4619)).

Redis TLS had landed in [`cozystack/cozystack#2729`](https://github.com/cozystack/cozystack/pull/2729) as opt-in, not a tri-state: it was never inferred from `external`, and it was fixed when the instance was created. The RedisFailover schema refused a change, while the Redis API accepted one and the HelmRelease then failed. So `external: true` with `tls` unset was a plaintext endpoint, and a spec's `tls.enabled` was a request, not proof of what the instance ran.

## Decision

The admission policy covers redis only. On create, `external: true` requires `tls.enabled: true`. On update, turning `external` on is refused whatever `tls.enabled` says, and the effective `tls.enabled` (unset counts as off) cannot change while `external` stays on. Withdrawing `external` in the same edit lifts that freeze, and the callers trusted by `cozystack-tenant-host-policy` are exempt from it. Other edits to a release that was already external pass, so releases exposed in plaintext before the policy keep working.

## Why not the alternatives

The first three lost to bypasses found while implementing [`cozystack/cozystack#4639`](https://github.com/cozystack/cozystack/pull/4639); each bypass is pinned there as a named case in [`pkg/registry/apps/application/external_tls_policy_test.go`](https://github.com/cozystack/cozystack/blob/16a502a8f7f168f0c88d794d945a195a433b7c6d/pkg/registry/apps/application/external_tls_policy_test.go), and the comment in [`packages/system/cozystack-basics/templates/database-external-tls-policy.yaml`](https://github.com/cozystack/cozystack/blob/16a502a8f7f168f0c88d794d945a195a433b7c6d/packages/system/cozystack-basics/templates/database-external-tls-policy.yaml) carries the mechanism.

- **The proposal's rule, refusing only an explicit `tls.enabled: false`.** For redis it misses the main case, `tls` left unset (`redis external with tls unset`). For postgres it refuses a configuration that is no more plaintext than the one it admits.
- **Ratcheting on the old and new spec, or trusting `tls.enabled` on update: refuse an update only when it makes the spec plaintext and external.** A tenant flips `tls.enabled` on an internal plaintext instance (accepted by the API, refused by the operator), then turns `external` on. Both steps pass, and the instance runs plaintext behind the LoadBalancer while its spec claims TLS (`redis update exposing a release whose spec claims TLS`).
- **Freezing nothing once external: let any edit of an external release pass.** A release created external with TLS can have TLS dropped before the first reconcile. The RedisFailover does not exist yet, so nothing refuses the change, and the instance is created plaintext behind the LoadBalancer (`redis update dropping TLS from an external release`).
- **Making `tls.enabled` immutable in the chart schema with `self == oldSelf`.** The aggregated apiserver does not evaluate CEL rules from `ApplicationDefinition.openAPISchema` ([`cozystack/cozystack#2657`](https://github.com/cozystack/cozystack/issues/2657), `docs/storage-immutability.md`), so the rule would not run.
- **A chart guard that looks up the live RedisFailover.** It reads the instance's real TLS state and keeps turning `external` on possible. It was not argued in a thread; it lost on cost, the author's judgement call rather than a hard fact: a larger change, and the refusal shows up as a failed HelmRelease instead of an API error.

## Consequences

- An internal redis cannot be exposed later. Exposing one means creating a new instance with both fields set, and a withdrawn `external` cannot be turned back on.
- A redis whose `tls.enabled` was flipped before the policy, and which is external, can no longer be unblocked by removing the field alone. The tenant withdraws `external` in the same edit, or an administrator repairs it.
- External postgres keeps accepting plaintext sessions. Closing that is a pg_hba change in the chart, tracked in [`cozystack/cozystack#4619`](https://github.com/cozystack/cozystack/issues/4619), not an admission rule.
- The policy guards the Redis app. A generic TCP publisher such as `tcp-balancer` can still forward to a plaintext Redis, by design.

## Revisit if

The aggregated apiserver starts evaluating schema CEL rules, which would make `tls.enabled` immutable at the API and let the update rules trust it. Or the redis chart gains a way to change TLS on a running instance.
