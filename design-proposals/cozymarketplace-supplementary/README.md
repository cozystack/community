# Cozymarketplace: backend, private sources, and publication validation (supplementary to #18)

- **Title:** `Cozymarketplace — Phase 1 backend, private repository support, and publication validation`
- **Author(s):** `@IvanHunters`
- **Date:** `2026-06-25`
- **Status:** Draft
- **Supplements:** [`cozystack/community#18`](https://github.com/cozystack/community/pull/18) by `@kvaps`
- **Related code:** [`cozystack/cozystack#2472`](https://github.com/cozystack/cozystack/pull/2472), [`cozystack/cozystack#2455`](https://github.com/cozystack/cozystack/issues/2455)

## Overview

This proposal is supplementary, not competing, to `#18`. It accepts the repository-centric model — the install and version unit is the External-Apps repository as a versioned OCI artifact — and fills in three concrete pieces that `#18` lists as open questions or leaves implicit: the in-cluster backend the dashboard talks to, the `PackageSourceRef` change that makes private repositories work in one command, and the publication validation that gates submissions to the meta-index. Per-package version pinning remains out of scope, siding with `#18`.

## Scope and related proposals

`#18` defines the meta-index, the repository-as-unit model, and the `cozypkg` repository commands. This proposal does not modify any of them. It specifies the backend that the dashboard view in `#18` implies, the credential plumbing private taps require, and the CI gate that lets the meta-index accept community submissions safely.

## Context

`#18` already covers how Cozystack ships External Apps today (`PackageSource` + `Package` + Flux `HelmRelease`) and how the repository-as-unit model layers on top. Three pieces in that picture remain underspecified.

First, `#18` says the catalog should be visible inside Cozystack so operators can install apps from there, with the dashboard handling enable/disable in Phase 1. It does not specify how the dashboard obtains the catalog data — walking k8s resources from the browser is impractical, and an aggregating server-side component is required.

Second, `#18` lists private repositories as an open question. The current `PackageSourceRef` CRD has no `secretRef` field, so tapping a private repository requires creating a `Secret`, then a `GitRepository`/`OCIRepository` with `secretRef` set, then a `PackageSource` referencing it — three out-of-band steps that defeat the one-command tap UX.

Third, `#18` lists publication validation as an open question. Without a gate, a community-submitted index entry can point at an artifact that does not pull, contains malformed `marketplace.yaml`, or ships a chart that fails `helm lint`, and the failure surfaces only at install time in someone else's cluster.

## Goals

Provide an in-cluster backend that powers the dashboard marketplace view: a small set of endpoints in `cozystack-api`, a `TapIndex` cache controller, and well-defined RBAC for connecting and disconnecting taps.

Add a `secretRef` field to the `PackageSourceRef` CRD so that connecting a private repository becomes a single command, with the reconciler materializing the underlying Flux source with the same credential.

Provide a `cozypkg validate` subcommand that lints a candidate marketplace repository offline, and reuse it in a GitHub Actions workflow that gates PRs to the meta-index repository.

Keep the existing External-Apps pipeline unchanged. All additions are additive: existing public installs see zero behaviour change when the new fields are left empty.

## Non-goals

Per-package version pinning. Out of scope, deferred per `#18`.

A dynamic external catalog backend. The external browse surface — a public site that lists all submitted repositories — is designed as a static site generated from the meta-index. No database, no runtime API, no telemetry. The publication CI is a GitHub Actions workflow, not a service.

Commercial / paid-operator marketplace. Acknowledged in `#18` as a later, separate marketplace.

Cross-tap dependency resolution with version constraints. Within a single tapped repository `dependsOn` already works; cross-tap version-constrained resolution is not addressed here.

## Design

### Backend endpoints in `cozystack-api`

The dashboard reads marketplace state through a small set of endpoints layered into `cozystack-api`, which already mediates dashboard access to platform CRs and existing auth/RBAC. No new component, no new CRDs — the marketplace state is fully derived from `PackageSource` plus the parsed artifact contents.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/marketplace/taps` | List connected taps with metadata. |
| `GET` | `/marketplace/taps/{name}/packages` | Packages exposed by one tap. |
| `GET` | `/marketplace/search?q=<query>` | Search across all taps by name, tag, description. |
| `POST` | `/marketplace/taps` | Connect a tap; creates `PackageSource` and, when given, the `Secret`. |
| `DELETE` | `/marketplace/taps/{name}` | Disconnect a tap. Lifecycle of installed `Package` CRs is an open question (see below). |

A `TapIndex` cache controller in the same binary watches `OCIRepository.status.artifact.revision`, pulls the parsed `marketplace.yaml` on each revision change, and serves the GET endpoints from memory. Without it, every search would hit OCI per request. Cluster-admin is required for `POST` and `DELETE`, matching the existing `Package` cluster-scoped model. `GET` is open to any authenticated user so tenant-admins can browse.

### Private repository support — `SecretRef` in `PackageSourceRef`

The new field is additive and nil-default:

```go
type PackageSourceRef struct {
    Kind, Name, Namespace, Path string
    SecretRef *corev1.LocalObjectReference  // NEW; nil preserves current behaviour
}
```

The `packagesource-reconciler` sets `spec.secretRef` on the materialized Flux source when `SecretRef != nil`. Secret format depends on source kind, matching what Flux source-controller already documents: `kubernetes.io/dockerconfigjson` for OCI; Opaque with `username`+`password` or `bearerToken` for Git over HTTPS; Opaque with `identity` (PEM private key) and `known_hosts` for Git over SSH. The Secret must exist in `cozy-system` before the reconciler runs; otherwise Flux reports a failed condition until it appears. This is symmetric with the platform-source change already in flight in `cozystack/cozystack#2472` — that PR closed the gap for the bootstrap platform source; this CRD field closes the same gap for every user-tapped repository.

### Publication validation — `cozypkg validate` and CI gate

A new `cozypkg validate <repository-url>[@<tag>]` subcommand pulls the artifact (or fails); parses `marketplace.yaml` against the published JSON schema; for each declared `PackageSource`, runs `helm lint` on every `Component.Path`; verifies that every `dependsOn` resolves either inside the same repository or in a known cozystack-shipped source; flags components with `install.Privileged: true` so the operator sees a privileged badge in the dashboard before install; and, with `--require-signature`, performs cosign verification.

The same logic runs in a GitHub Actions workflow in the meta-index repository, triggered on PRs that add or modify an entry. The workflow resolves `source.url` and `source.tag` from the diff, runs the validator, annotates the PR with the report, and blocks merge on hard failures. It does not replace maintainer review; it lowers the cost of that review by surfacing structural failures up front.

### External catalog (design only, deferred)

The public browse surface — a site at e.g. `marketplace.cozystack.io` — is designed as a static site generated from the meta-index on every meta-index merge. No runtime backend, no database. Hugo, Astro, or MkDocs all fit. Client-side search via Pagefind. Icons, screenshots, READMEs are served by reference into the OCI artifacts produced by repository authors. Implementation is explicitly deferred; flagged here only to confirm no dynamic backend is required.

## User-facing changes

CLI: a new `cozypkg validate` subcommand; existing `tap` / `add` / `list` semantics unchanged.

CRD: a new optional `PackageSourceRef.secretRef` field; nil preserves current behaviour.

Dashboard: a marketplace view backed by the new `/marketplace/*` endpoints. Tapped repositories are visible, packages browsable, installs flow through the existing Package-creation path.

Meta-index repository: a new `validate.yaml` GitHub Actions workflow.

## Upgrade and rollback compatibility

Strictly additive. `PackageSourceRef.SecretRef = nil` produces the same Flux source manifest as today, so existing tapped public repositories see no change. The new endpoints are net-new paths under `/marketplace/*`; no existing route changes shape. The `TapIndex` cache starts empty and populates from existing `PackageSource` resources at startup; nothing else relies on its presence. Rolling back to a `cozystack-api` version without the marketplace endpoints leaves the cluster fully functional — only the dashboard marketplace view goes blank. No migration script is required for the `secretRef` field alone.

## Security

The trust boundary `#18` already describes — tapping a third-party repository runs that repository's charts in the operator's cluster — is preserved. The publication CI gate is the first line of defence on the meta-index side, but it does not endorse content; it only checks structural validity. Maintainer review remains required for every PR.

Privileged components are surfaced both at validation time (the CI workflow emits a warning and labels the entry) and at install time (`cozypkg add` prompts for confirmation unless `--allow-privileged` is passed).

The new `secretRef` references a Secret by name; the controller never reads or logs the credentials. The Secret is consumed by Flux source-controller under its existing RBAC.

## Failure and edge cases

`marketplace.yaml` malformed inside the artifact → cache controller logs the parse error, marks the tap as `Degraded`, and returns the last-known-good payload. Operator sees a clear error in the dashboard.

Secret removed while still referenced → reconcile produces the source with a broken `secretRef`; Flux surfaces the failed pull condition, which the marketplace endpoint forwards.

Tap removed while packages from it remain installed → see open question. Default behaviour proposed: leave installed `Package` resources in place (they carry `helm.sh/resource-policy: keep`) and mark them as `OrphanedSource` in the dashboard until the operator re-taps or removes them with `cozypkg del`.

`POST /marketplace/taps` with a `secretRef` pointing at a non-existent Secret → endpoint accepts the request (Flux can recover later when the Secret appears), but the dashboard shows the `Secret not found in cozy-system` condition sourced from Flux.

## Testing

Unit: new endpoints against a fake k8s client; cache controller refresh logic against a fake OCI source; `cozypkg validate` golden-output tests against fixture repositories, both valid and intentionally broken. Integration: end-to-end test that taps a fixture private OCI repository with a `dockerconfigjson` Secret on a kind cluster and verifies the dashboard endpoint surfaces the parsed packages. CI workflow is exercised on a fixture entry pointing at `cozystack/external-apps-example`.

## Rollout

Four independent PRs in cozystack, two of them landable in parallel. `secretRef` in `PackageSourceRef` (~50 LOC plus a migration script if needed) and `cozypkg validate` plus the CI workflow (~600 LOC Go and ~200 LOC YAML/shell) can land before or after `#18`. The marketplace endpoints in `cozystack-api` (~400 LOC Go) and the `TapIndex` cache controller (~300 LOC Go) consume the `marketplace.yaml` shape introduced by `#18` and depend on it landing first. The external static catalog is a separate proposal under Aenix maintenance; designed-only here.

## Open questions

Tap-remove lifecycle. When a tap is disconnected and packages installed from it remain, do we orphan them with a dashboard marker, cascade-delete, or block deletion until the operator confirms? Default proposed: orphan with marker.

Endpoint hosting. Marketplace endpoints inside `cozystack-api` versus a new `cozystack-marketplace-controller`. Default proposed: extend `cozystack-api`.

External catalog hostname. `marketplace.cozystack.io`, `apps.cozystack.io`, `hub.cozystack.io`. Aenix-side decision.

Privileged-tap policy. Should a cluster be configurable to refuse tapping any repository whose components declare `Privileged: true`, regardless of operator approval? Deferred to a follow-up.

Verified-vs-community labelling. Who maintains the verified allowlist and on what criteria. Maintainer-side governance question.

## Alternatives considered

Building the marketplace surface entirely in the dashboard frontend against the existing `cozystack-api` CR proxy. Rejected because the catalog requires aggregation across multiple artifacts plus parsed `marketplace.yaml` content; doing this in the browser per page load is too slow and would re-implement caching client-side.

A dedicated CRD for marketplace state (`Tap`, `TapEntry`, ...). Rejected because all of the necessary state is derivable from `PackageSource` plus the parsed artifact; new CRDs raise migration cost without changing capability.

A dynamic external catalog backend (database plus API). Rejected as Phase 1 over-engineering. The meta-index is already a single source of truth; a static generator is sufficient and dramatically lower-cost.

Inline credentials in `PackageSourceRef` (e.g. `username`/`password` directly on the CR field). Rejected on principle — credentials live in Secrets, never on CR fields, to remain encryptable at rest and to flow through existing RBAC.

Per-package version pinning as part of this proposal. Out of scope to stay aligned with `#18`. A separate proposal exists in the author's internal notes and may be revived if a concrete operational case emerges (CVE in a single package of a large repository, partner publishing on independent cadence).
