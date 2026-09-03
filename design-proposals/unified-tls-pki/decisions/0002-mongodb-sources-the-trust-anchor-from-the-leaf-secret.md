# 0002. mongodb sources the trust anchor from its leaf Secret, not from the operator's CA Secret

- **Number:** `0002`
- **Date:** `2026-09-01`
- **Status:** Accepted
- **Deciders:** `@Arsolitt, @lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/cozystack#2692`](https://github.com/cozystack/cozystack/pull/2692), recorded in the proposal by [`cozystack/community#36`](https://github.com/cozystack/community/pull/36)
- **Implemented in:** [`cozystack/cozystack#2692`](https://github.com/cozystack/cozystack/pull/2692)

## Context

The contract said the sentinel names the engine's CA-bearing Secret and the controller strips everything but `ca.crt`. Every engine that had converged when mongodb landed fit that sentence: nats and qdrant name the CA their chart's cert-manager graph renders, and postgres names the CA CloudNativePG creates. So did every engine designed but not yet converged — redis over its chart's cert-manager CA, kafka over Strimzi's key-free one.

mongodb was the first engine with two candidate sources. The Percona PSMDB operator mints a cert-manager chain at runtime and writes both a key-bearing CA Secret at `<release>-ca-cert` and a leaf Secret at `<release>-ssl` that carries `ca.crt` alongside its own key pair. Reading the contract literally pointed at `<release>-ca-cert`, and three properties of that name argued against it, none of which is visible in the resulting template without the comment that now sits above it.

## Decision

The mongodb chart's `TenantProjection` names the leaf Secret `{{ .Release.Name }}-ssl` with `sourceKey: ca.crt`, ungated — the PSMDB operator issues the chain unconditionally, so the source exists for every release.

## Why not `<release>-ca-cert`

- **It is not a name the chart controls.** `<release>-ssl` is the name the chart itself sets in the PSMDB CR's `spec.secrets.ssl`, so chart and sentinel move together; `-ca-cert` is the operator's internal choice and can change under an operator bump with nothing in the chart to notice.
- **It does not exist on every path that produces TLS.** When cert-manager is absent the operator falls back to its own self-signed material and writes the leaf, but no cert-manager CA Secret — so a sentinel over `-ca-cert` would sit at `Ready=False, Reason=SourceNotFound` for that entire configuration while TLS is working.
- **It carries the wrong bundle during a rotation.** The operator merges the old and the new CA into the leaf's `ca.crt`, so the leaf is the object that stays verifiable across a CA rotation; the CA Secret carries only the current CA, and a client that fetched the anchor mid-rotation would hold half of it.

## Consequences

- The contract's sentence about sourcing is narrower than it read. The sentinel names whichever Secret carries a correct and stable `ca.crt`; the engine's CA Secret is the usual answer, not the definition.
- Nothing about the security boundary changes. The leaf is key-bearing too — it holds `tls.key` — so the controller's strip is doing the same work it does everywhere else, and the guard sees the same shape.
- The next per-engine author has three properties to check rather than a name to copy, and this record is where the checklist lives. Reaching for the CA Secret by reflex is the failure mode it exists to prevent.

## Revisit if

PSMDB stops merging the old and new CA into the leaf's `ca.crt` during a rotation, or its self-signed fallback starts writing a CA Secret of its own — either removes one of the three reasons, and the second one removes the strongest.
