# 0001. The trust anchor is declared by a namespaced `TenantProjection` and published as `<release>.tenant-ca`

- **Number:** `0001`
- **Date:** `2026-07-22`
- **Status:** Accepted
- **Deciders:** `@lexfrei, @lllamnyp`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** the API-owner review on [`cozystack/cozystack#3299`](https://github.com/cozystack/cozystack/pull/3299), recorded in the proposal by [`cozystack/community#36`](https://github.com/cozystack/community/pull/36)
- **Implemented in:** [`cozystack/cozystack#3407`](https://github.com/cozystack/cozystack/pull/3407), [`cozystack/cozystack#3408`](https://github.com/cozystack/cozystack/pull/3408), and [`cozystack/cozystack#3411`](https://github.com/cozystack/cozystack/pull/3411), which added the release-prefix pattern the Consequences below rely on

## Context

The accepted proposal delivered the input path with a **label**: an application chart stamped `internal.cozystack.io/publish-ca-cert` on its CA Secret, a controller selected sources by that label, and the key-free copy it wrote was named `<release>-ca-cert`. Both halves of that — the selection mechanism and the object name — were settled on paper and neither survived the implementation in [#3299](https://github.com/cozystack/cozystack/pull/3299).

The label could not carry attribution. cert-manager runs on this platform with `enableCertificateOwnerRef: false`, so a cert-manager-issued Secret holds no ownerReference back to the application that asked for it, and the controller had no way to tell which release a labelled Secret belonged to. The design answered that with a second label carrying the release name, which is where it stopped being a label and started being an object with the fields spelled as strings. It also had nowhere to put a status: a declaration whose source never appears could only requeue, silently, until somebody noticed the trust anchor was missing. An intermediate revision moved selection onto a `spec.caCert` field of the cluster-scoped `ApplicationDefinition` and inherited the mirror-image flaw — a cluster-scoped, per-kind object standing in for a per-namespace, per-release fact.

The name failed twice, and each failure was found by an operator that already claimed it. `<release>-ca-cert` collides across **engines**: Percona PSMDB creates a Secret of exactly that name and puts a private key in it. Its replacement `<release>-tenant-ca` was justified as claimed by no operator the platform ships, and collides across **releases**: for an application `foo` the projection is `postgres-foo-tenant-ca`, and for a sibling application `foo-tenant` CloudNativePG's own CA Secret is `postgres-foo-tenant` plus `-ca` — the same string. That collision has a direction no guard can refuse: if the projection is written first, CNPG rejects the key-free Secret with `missing ca.key secret data`, the sibling's PKI never completes, and that application never starts, blaming a Secret its owner never created. The controller that wrote first was within its rights, so there is nothing to refuse.

@lllamnyp's review on #3299 named the shape of the first problem rather than a fix for it: what the chart is declaring — project this Secret's `ca.crt` as a trust anchor — is a first-class intent, and emulating an object with a label pays for it in every direction at once.

## Decision

The chart declares the trust anchor by rendering a namespaced `TenantProjection` (group `internal.cozystack.io`, version `v1alpha1`, short name `tproj`) that names the source Secret by name in `sourceSecretName` and the key to lift in `sourceKey`; the controller derives the release from the `helm.toolkit.fluxcd.io/name` label Flux stamps on the sentinel, publishes a key-free Secret named `<release>.tenant-ca` carrying only `ca.crt`, owner-references it to the sentinel, and reports the outcome as a `Ready` condition on the sentinel.

## Why not the alternatives

- **The source-selection label, which is what the proposal originally said** ([#3299](https://github.com/cozystack/cozystack/pull/3299)). A label has no name to reference, no status to carry, and no admission surface of its own, so a broken declaration is invisible; and because cert-manager Secrets carry no ownerReference under `enableCertificateOwnerRef: false`, it needed a second label to answer a question the sentinel answers by being a chart object in the release's namespace.
- **A `spec.caCert` field on `ApplicationDefinition`** (an intermediate revision of the same pull request). Cluster-scoped and per-kind, standing in for a fact that is per-namespace and per-release.
- **`<release>-ca-cert` as the canonical name.** Claimed by Percona PSMDB, with a private key in it — the source and the target would collide on the engine that most needs the projection.
- **`<release>-tenant-ca` as the canonical name.** Collides with CloudNativePG's own CA across sibling releases, in the one direction that cannot be guarded; it would have stopped an application that worked before this contract existed from coming up at all.
- **Owner-referencing the projection to the application instance.** Not possible rather than unwise: the `apps.cozystack.io` kinds are virtual, an application is stored as a HelmRelease whose `spec.values` is the application spec, so there is no object in etcd and no UID to reference.
- **Owner-referencing the projection to the HelmRelease directly.** Workable, and rejected for lifetime rather than for lineage: the lineage walk resolves either chain through unmodified code, but the sentinel is the object whose existence *is* the declaration, so pruning the chart collects the projection natively (`internal/controller/cacert/reconciler.go`, "Why the owner is the sentinel, not the HelmRelease"). It does not remove the delete path outright: `withdrawProjection` stays for the one case garbage collection never sees, a sentinel that goes on existing with its `CACert` entry removed.

## Consequences

- A broken declaration is now queryable. `kubectl get tproj` shows a source that never appeared, a source renamed out from under the sentinel, a value that failed the certificate gate, and a release two sentinels are contesting.
- Every converging chart renders one more object, and the platform owns a CRD it did not have.
- Nothing had to be migrated. [#3407](https://github.com/cozystack/cozystack/pull/3407) carried the `.tenant-ca` suffix in the revision that merged, so no release ever wrote a projected `<release>-ca-cert` or `<release>-tenant-ca` into a cluster.
- The dot in the canonical name rests on one assumption the platform does not enforce — that no per-release Secret suffix in use spells `.tenant-ca`. Not the wider claim that suffixes are dot-free: dotted ones are already in use, since the redis chart under [#2729](https://github.com/cozystack/cozystack/pull/2729) names its CA Secret `<release>.ca-tls` and its leaf `<release>.tls`, and the operator that series patches writes `<release>.ca-cert`. The argument is therefore conditional on a set the platform keeps growing. The enforced legs are the application name and the release prefix; the residue is covered by refusing, rather than adopting, a Secret already sitting at the canonical name.

## Revisit if

A validating rule constrains the suffixes charts and operators may append to per-release Secret names, which would make the canonical name disjoint by enforcement rather than by assumption; or the `tenantsecrets` projection gains a per-key field filter, which would retire the copied object and the controller with it.
